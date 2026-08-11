#!/usr/bin/env python3
"""Trusted production composition for canonical Operator adapter backends.

This module supplies the shared server-owned read/runtime boundary used by AI
client adapters. It is not a client configuration parser: repository, Feature
refs, principal, Store ref and GitHub credential all come from trusted process
startup configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

from operator_decision_backends import DecisionListBackend, NotificationListBackend, OperatorInboxBackend, TrustedOperatorScope
from operator_store_backends import OperationStatusBackend, store_backends
from operator_store_model import normalize_repository
from operator_store_runtime import TrustedOperatorStoreConfig, build_github_operator_store_runtime

RUNTIME_CONFIG_VERSION = "ai-sdlc.operator-runtime-config/v1"
PROJECT_ADAPTER_PATH = ".ai-sdlc/project.yaml"


class OperatorProductionRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TrustedFeatureBinding:
    feature_id: str
    target_ref: str

    def __post_init__(self):
        if not self.feature_id or any(ch in self.feature_id for ch in "/\\"):
            raise ValueError("trusted Feature id is invalid")
        if not self.target_ref or self.target_ref.startswith("refs/") or ".." in self.target_ref:
            raise ValueError("trusted Feature target ref is invalid")


@dataclass(frozen=True)
class TrustedOperatorRuntimeConfig:
    repository: str
    installation_ref: str
    trusted_checkout: Path
    principal: str
    feature_bindings: tuple[TrustedFeatureBinding, ...]
    state_ref: str = "refs/heads/ai-sdlc-operator-state"
    remote_name: str = "origin"
    operator_app_slug: str = "ai-sdlc-operator"

    def __post_init__(self):
        normalized = normalize_repository(self.repository)
        object.__setattr__(self, "repository", normalized)
        if not self.installation_ref or ".." in self.installation_ref:
            raise ValueError("trusted installation ref is invalid")
        if not self.principal:
            raise ValueError("trusted Operator principal is required")
        if not self.state_ref.startswith("refs/heads/"):
            raise ValueError("trusted Operator Store state ref must be a branch ref")
        ids = [row.feature_id for row in self.feature_bindings]
        refs = [row.target_ref for row in self.feature_bindings]
        if len(ids) != len(set(ids)) or len(refs) != len(set(refs)):
            raise ValueError("trusted Feature bindings must be one-to-one")

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, config_base: Path | None = None) -> "TrustedOperatorRuntimeConfig":
        if not isinstance(data, dict) or data.get("version") != RUNTIME_CONFIG_VERSION:
            raise ValueError("unsupported trusted Operator runtime config")
        allowed = {
            "version",
            "repository",
            "installation_ref",
            "trusted_checkout",
            "principal",
            "feature_refs",
            "state_ref",
            "remote_name",
            "operator_app_slug",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown trusted Operator runtime config keys: {sorted(unknown)}")
        raw_refs = data.get("feature_refs")
        if not isinstance(raw_refs, dict) or not raw_refs:
            raise ValueError("trusted Operator runtime requires Feature/ref bindings")
        bindings = tuple(
            TrustedFeatureBinding(str(feature_id), str(target_ref))
            for feature_id, target_ref in sorted(raw_refs.items())
        )
        raw_checkout = Path(str(data.get("trusted_checkout") or "."))
        if not raw_checkout.is_absolute() and config_base is not None:
            raw_checkout = (config_base / raw_checkout).resolve()
        return cls(
            repository=str(data.get("repository") or ""),
            installation_ref=str(data.get("installation_ref") or "main"),
            trusted_checkout=raw_checkout,
            principal=str(data.get("principal") or ""),
            feature_bindings=bindings,
            state_ref=str(data.get("state_ref") or "refs/heads/ai-sdlc-operator-state"),
            remote_name=str(data.get("remote_name") or "origin"),
            operator_app_slug=str(data.get("operator_app_slug") or "ai-sdlc-operator"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "TrustedOperatorRuntimeConfig":
        config_path = Path(path).resolve()
        with config_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return cls.from_mapping(data, config_base=config_path.parent)

    def feature_ref(self, feature_id: str) -> str:
        matches = [row.target_ref for row in self.feature_bindings if row.feature_id == feature_id]
        if len(matches) != 1:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "Feature is outside trusted runtime configuration")
        return matches[0]

    @property
    def feature_ids(self) -> frozenset[str]:
        return frozenset(row.feature_id for row in self.feature_bindings)


def _default_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - trusted GitHub API URL
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload: object = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {}
        return int(exc.code), payload
    except (URLError, TimeoutError, OSError):
        return 0, {}


class GitHubTrustedProjectFeatureReader:
    """Read exact trusted project/Feature truth from GitHub Contents API."""

    def __init__(
        self,
        *,
        config: TrustedOperatorRuntimeConfig,
        token: str,
        api_base: str = "https://api.github.com",
        http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    ):
        if not token:
            raise ValueError("trusted GitHub read token is required")
        if not api_base.startswith("https://"):
            raise ValueError("GitHub API base must use HTTPS")
        self.config = config
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.http_get = http_get

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-operator-runtime",
        }

    def _content(self, *, path: str, ref: str, missing_ok: bool = False) -> str | None:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        encoded_ref = quote(ref, safe="")
        url = f"{self.api_base}/repos/{self.config.repository}/contents/{encoded_path}?ref={encoded_ref}"
        status, payload = self.http_get(url, self._headers())
        if status == 404 and missing_ok:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise OperatorProductionRuntimeError("TRANSIENT_FAILURE", f"trusted GitHub content read failed with HTTP {status}")
        encoded = payload.get("content")
        if not isinstance(encoded, str):
            raise OperatorProductionRuntimeError("INTERNAL_FAILURE", "trusted GitHub content response lacks file content")
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except Exception as exc:
            raise OperatorProductionRuntimeError("INTERNAL_FAILURE", "trusted GitHub content response is invalid") from exc

    def project_installed(self) -> bool:
        text = self._content(path=PROJECT_ADAPTER_PATH, ref=self.config.installation_ref, missing_ok=True)
        if text is None:
            return False
        try:
            project = yaml.safe_load(text)
        except Exception as exc:
            raise OperatorProductionRuntimeError("INTERNAL_FAILURE", "installed Project Adapter is invalid YAML") from exc
        declared = ((project or {}).get("repository") or {}).get("full_name") if isinstance(project, dict) else None
        if not isinstance(declared, str):
            return False
        return normalize_repository(declared) == self.config.repository

    def feature_manifest(self, feature_id: str) -> dict[str, Any]:
        target_ref = self.config.feature_ref(feature_id)
        path = f"state/features/{feature_id}.yaml"
        text = self._content(path=path, ref=target_ref, missing_ok=False)
        try:
            manifest = yaml.safe_load(text or "")
        except Exception as exc:
            raise OperatorProductionRuntimeError("INTERNAL_FAILURE", "trusted Feature Manifest is invalid YAML") from exc
        if not isinstance(manifest, dict) or str((manifest.get("feature") or {}).get("id") or "") != feature_id:
            raise OperatorProductionRuntimeError("INTERNAL_FAILURE", "trusted Feature Manifest identity mismatch")
        return manifest


class BoundedTrustedContextProvider:
    """Server-owned canonical trusted identity/scope for one adapter process."""

    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str):
        if not adapter_id:
            raise ValueError("trusted adapter id is required")
        self.config = config
        self.adapter_id = adapter_id

    def for_request(self, target: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "trusted_identity": {
                "service_id": "ai-sdlc-operator",
                "runtime_id": "production-runtime",
                "authorization_context": "trusted-installed-project-scope",
            },
            "trusted_scope": {
                "repositories": [self.config.repository],
                "feature_ids": sorted(self.config.feature_ids),
            },
            "trusted_principal": self.config.principal,
            "trusted_client_adapter_id": self.adapter_id,
        }


class _ScopedReadBackend:
    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str):
        self.config = config
        self.adapter_id = adapter_id

    def availability(self, capability: str, trusted_context: dict[str, Any]):
        try:
            scope = TrustedOperatorScope.from_context(trusted_context)
            if scope.client_adapter_id != self.adapter_id:
                return False, "POLICY_RESTRICTED"
            if self.config.repository not in scope.repositories:
                return False, "POLICY_RESTRICTED"
        except Exception:
            return False, "POLICY_RESTRICTED"
        return True, "AVAILABLE"

    def _scope(self, request: dict[str, Any], trusted_context: dict[str, Any]) -> TrustedOperatorScope:
        scope = TrustedOperatorScope.from_context(trusted_context)
        declared = str((request.get("client_identity") or {}).get("adapter_id") or "")
        if declared != self.adapter_id or declared != scope.client_adapter_id:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "adapter identity does not match trusted runtime")
        return scope


class ProjectInspectBackend(_ScopedReadBackend):
    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str, reader: GitHubTrustedProjectFeatureReader):
        super().__init__(config=config, adapter_id=adapter_id)
        self.reader = reader

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        scope = self._scope(request, trusted_context)
        target = request.get("target") or {}
        repository = normalize_repository(str(target.get("repository") or ""))
        if repository not in scope.repositories or repository != self.config.repository:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "repository is outside trusted runtime scope")
        return {"repository": repository, "installed": self.reader.project_installed()}


class FeatureStatusBackend(_ScopedReadBackend):
    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str, reader: GitHubTrustedProjectFeatureReader):
        super().__init__(config=config, adapter_id=adapter_id)
        self.reader = reader

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        scope = self._scope(request, trusted_context)
        target = request.get("target") or {}
        repository = normalize_repository(str(target.get("repository") or ""))
        feature_id = str(target.get("feature_id") or "")
        if not feature_id or not scope.allows(repository, feature_id):
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "Feature is outside trusted runtime scope")
        if repository != self.config.repository:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "repository is outside trusted runtime scope")
        manifest = self.reader.feature_manifest(feature_id)
        workflow = manifest.get("workflow") or {}
        return {
            "feature_id": feature_id,
            "revision": int(manifest.get("revision", 0)),
            "workflow_status": str(workflow.get("status") or ""),
            "current_stage": str(workflow.get("current_stage") or ""),
        }


@dataclass(frozen=True)
class TrustedOperatorReadBundle:
    config: TrustedOperatorRuntimeConfig
    trusted_context_provider: BoundedTrustedContextProvider
    backends: dict[str, Any]
    runtime: Any


def build_trusted_operator_read_bundle(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    github_token: str,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
    protection_verifier=None,
) -> TrustedOperatorReadBundle:
    """Compose the shared canonical read bundle from trusted process inputs.

    Tests may inject an already-constructed production-like verifier. Normal
    startup uses the existing GitHub protection verifier; Issue #241 extends
    that concrete builder with personal-repository ruleset proof without
    changing this adapter-facing composition boundary.
    """
    store_config = TrustedOperatorStoreConfig(
        repository=config.repository,
        trusted_checkout=config.trusted_checkout,
        state_ref=config.state_ref,
        remote_name=config.remote_name,
    )
    if protection_verifier is None:
        runtime = build_github_operator_store_runtime(
            store_config,
            github_token=github_token,
            operator_app_slug=config.operator_app_slug,
            github_api_base=github_api_base,
        )
    else:
        from operator_store_runtime import build_trusted_operator_store_runtime
        runtime = build_trusted_operator_store_runtime(
            store_config,
            protection_verifier=protection_verifier,
        )

    reader = GitHubTrustedProjectFeatureReader(
        config=config,
        token=github_token,
        api_base=github_api_base,
        http_get=reader_http_get,
    )
    provider = BoundedTrustedContextProvider(config=config, adapter_id=adapter_id)
    store_map = store_backends(runtime)
    backends: dict[str, Any] = {
        "project.inspect": ProjectInspectBackend(config=config, adapter_id=adapter_id, reader=reader),
        "feature.status": FeatureStatusBackend(config=config, adapter_id=adapter_id, reader=reader),
        "operator.inbox": OperatorInboxBackend(runtime),
        "operation.status": OperationStatusBackend(runtime),
        "decision.list": DecisionListBackend(runtime),
        "notification.list": NotificationListBackend(runtime),
        # Store-backed semantic writes are part of the same canonical runtime
        # bundle. A transport such as MCP remains read-only simply by never
        # registering write tools; future approved adapters may reuse them.
        "operation.start": store_map["operation.start"],
        "operation.cancel": store_map["operation.cancel"],
    }
    return TrustedOperatorReadBundle(
        config=config,
        trusted_context_provider=provider,
        backends=backends,
        runtime=runtime,
    )
