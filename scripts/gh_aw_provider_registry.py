#!/usr/bin/env python3
"""Authoritative trusted registry boundary for gh-aw engine/provider profiles."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Mapping
import re
import urllib.parse

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
REGISTRY_VERSION = "0.1.0"
SUPPORTED_NATIVE_ENGINES = frozenset({"copilot", "codex", "claude", "gemini"})
SUPPORTED_MATURITY = frozenset({"reference", "experimental"})
SUPPORTED_WIRE_APIS = frozenset({"completions", "responses"})
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
CREDENTIAL_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)
WORKER_WORKFLOW_RE = re.compile(r"^[A-Za-z0-9._-]+\.lock\.ya?ml$")
ROOT_FIELDS = frozenset({"version", "profiles"})
PROFILE_FIELDS = frozenset(
    {
        "engine",
        "engine_version",
        "model",
        "worker_source",
        "worker_workflow",
        "credential",
        "credential_aliases",
        "maturity",
        "protocol",
        "provider",
        "provider_type",
        "wire_api",
        "base_url",
        "network_host",
    }
)
OPENAI_ONLY_FIELDS = frozenset({"provider", "provider_type", "wire_api", "base_url", "network_host"})


class RegistryValidationError(ValueError):
    """Deterministic fail-closed Registry validation failure."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RegistryValidationError(f"duplicate mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class EngineProfile:
    profile_id: str
    engine: str
    worker_source: str
    worker_workflow: str
    credential: str
    maturity: str
    engine_version: str | None = None
    model: str | None = None
    credential_aliases: tuple[str, ...] = ()
    protocol: str = "native"
    provider: str = "native"
    provider_type: str | None = None
    wire_api: str | None = None
    base_url: str | None = None
    network_host: str | None = None

    @property
    def is_openai_compatible(self) -> bool:
        return self.protocol == "openai-compatible"


@dataclass(frozen=True)
class ProviderRegistry:
    version: str
    profiles: tuple[EngineProfile, ...]
    _by_id: Mapping[str, EngineProfile]
    _by_worker: Mapping[str, EngineProfile]

    def require_profile(self, profile_id: str) -> EngineProfile:
        try:
            return self._by_id[profile_id]
        except KeyError as exc:
            allowed = ", ".join(self.profile_ids())
            raise RegistryValidationError(
                f"unknown gh-aw engine profile {profile_id!r}; allowed: {allowed}"
            ) from exc

    def profile_ids(self) -> tuple[str, ...]:
        return tuple(profile.profile_id for profile in self.profiles)

    def openai_compatible_profiles(self) -> tuple[EngineProfile, ...]:
        return tuple(profile for profile in self.profiles if profile.is_openai_compatible)

    def trusted_worker_workflows(self) -> Mapping[str, EngineProfile]:
        return self._by_worker

    def require_worker_workflow(self, worker_workflow: str) -> EngineProfile:
        try:
            return self._by_worker[worker_workflow]
        except KeyError as exc:
            raise RegistryValidationError(
                "worker_workflow is not registered in trusted gh-aw engine profiles"
            ) from exc


def _error(profile_id: str | None, field: str | None, message: str) -> RegistryValidationError:
    prefix = "registry"
    if profile_id is not None:
        prefix += f" profile {profile_id!r}"
    if field is not None:
        prefix += f" field {field!r}"
    return RegistryValidationError(f"{prefix}: {message}")


def _require_string(cfg: Mapping[str, object], profile_id: str, field: str) -> str:
    value = cfg.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _error(profile_id, field, "must be a non-empty string")
    return value


def _validate_worker_source(
    profile_id: str,
    value: str,
    repo_root: Path,
    require_source_files: bool,
) -> None:
    if "\\" in value:
        raise _error(profile_id, "worker_source", "must use repository-relative POSIX separators")
    path = PurePosixPath(value)
    parts = path.parts
    if path.is_absolute() or not parts or ".." in parts or "." in parts or any(not part for part in parts):
        raise _error(profile_id, "worker_source", "must be a normalized repository-relative path")
    if len(parts) < 3 or parts[0:2] != (".github", "workflows") or path.suffix != ".md":
        raise _error(profile_id, "worker_source", "must be a .md file under .github/workflows/")
    if require_source_files and not (repo_root / Path(*parts)).is_file():
        raise _error(profile_id, "worker_source", "registered worker source does not exist")


def _validate_credential(profile_id: str, field: str, value: str) -> None:
    if not CREDENTIAL_RE.fullmatch(value) or value.startswith("GITHUB_"):
        raise _error(
            profile_id,
            field,
            "must be an uppercase repository secret name and not use GITHUB_ prefix",
        )


def _normalize_base_url(profile_id: str, base_url: str, network_host: str) -> tuple[str, str]:
    try:
        parsed = urllib.parse.urlsplit(base_url)
    except ValueError as exc:
        raise _error(profile_id, "base_url", "must be a valid absolute HTTPS URL") from exc
    if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
        raise _error(profile_id, "base_url", "must be an absolute HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise _error(profile_id, "base_url", "must not embed credentials")
    if parsed.query or parsed.fragment:
        raise _error(profile_id, "base_url", "must not contain query or fragment components")
    normalized_host = parsed.hostname.lower()
    if not HOST_RE.fullmatch(network_host) or network_host.lower() != normalized_host:
        raise _error(profile_id, "network_host", "must exactly match the base_url hostname")
    return base_url, normalized_host


def _infer_repo_root(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.parent.name == "gh-aw" and resolved.parent.parent.name == "runtimes":
        return resolved.parents[2]
    return resolved.parent


def load_registry(
    path: Path | str = DEFAULT_REGISTRY,
    *,
    repo_root: Path | str | None = None,
    require_source_files: bool = True,
) -> ProviderRegistry:
    """Load and validate the complete trusted Registry before exposing any identity."""
    registry_path = Path(path)
    root = Path(repo_root) if repo_root is not None else _infer_repo_root(registry_path)
    try:
        text = registry_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryValidationError(f"cannot read gh-aw engine profile registry: {registry_path}") from exc
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except RegistryValidationError:
        raise
    except yaml.YAMLError as exc:
        raise RegistryValidationError("invalid gh-aw engine profile registry YAML") from exc

    if not isinstance(data, dict):
        raise RegistryValidationError("registry root must be a mapping")
    unknown_root = sorted(set(data) - ROOT_FIELDS)
    if unknown_root:
        raise RegistryValidationError(
            f"registry root contains unsupported fields: {', '.join(map(str, unknown_root))}"
        )
    if data.get("version") != REGISTRY_VERSION:
        raise RegistryValidationError(
            f"unsupported gh-aw engine profile registry version: {data.get('version')!r}"
        )
    raw_profiles = data.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise RegistryValidationError("registry profiles must be a non-empty mapping")

    normalized: list[EngineProfile] = []
    seen_sources: dict[str, str] = {}
    seen_workers: dict[str, str] = {}
    seen_credentials: dict[str, str] = {}

    for profile_id, raw_cfg in raw_profiles.items():
        if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
            raise _error(str(profile_id), None, "profile id must match ^[a-z][a-z0-9-]*$")
        if not isinstance(raw_cfg, dict):
            raise _error(profile_id, None, "profile definition must be a mapping")
        unknown = sorted(set(raw_cfg) - PROFILE_FIELDS)
        if unknown:
            raise _error(
                profile_id,
                None,
                f"contains unsupported fields: {', '.join(map(str, unknown))}",
            )

        engine = _require_string(raw_cfg, profile_id, "engine")
        worker_source = _require_string(raw_cfg, profile_id, "worker_source")
        worker_workflow = _require_string(raw_cfg, profile_id, "worker_workflow")
        credential = _require_string(raw_cfg, profile_id, "credential")
        maturity = _require_string(raw_cfg, profile_id, "maturity")
        protocol = raw_cfg.get("protocol", "native")
        if not isinstance(protocol, str) or protocol not in {"native", "openai-compatible"}:
            raise _error(profile_id, "protocol", "must be native or openai-compatible")
        if maturity not in SUPPORTED_MATURITY:
            raise _error(
                profile_id,
                "maturity",
                f"must be one of: {', '.join(sorted(SUPPORTED_MATURITY))}",
            )

        _validate_worker_source(profile_id, worker_source, root, require_source_files)
        if (
            not WORKER_WORKFLOW_RE.fullmatch(worker_workflow)
            or "/" in worker_workflow
            or "\\" in worker_workflow
        ):
            raise _error(
                profile_id,
                "worker_workflow",
                "must be a .lock.yml/.lock.yaml workflow filename",
            )
        _validate_credential(profile_id, "credential", credential)

        aliases_raw = raw_cfg.get("credential_aliases", ())
        if aliases_raw is None:
            aliases_raw = ()
        if not isinstance(aliases_raw, (list, tuple)) or any(
            not isinstance(alias, str) for alias in aliases_raw
        ):
            raise _error(profile_id, "credential_aliases", "must be a sequence of secret names")
        aliases = tuple(aliases_raw)
        if len(set(aliases)) != len(aliases):
            raise _error(profile_id, "credential_aliases", "must not contain duplicates")
        for alias in aliases:
            _validate_credential(profile_id, "credential_aliases", alias)

        engine_version = raw_cfg.get("engine_version")
        if engine_version is not None and (
            not isinstance(engine_version, str) or not VERSION_RE.fullmatch(engine_version)
        ):
            raise _error(profile_id, "engine_version", "must be pinned semantic version x.y.z")
        model = raw_cfg.get("model")
        if model is not None and (
            not isinstance(model, str) or not MODEL_RE.fullmatch(model)
        ):
            raise _error(profile_id, "model", "contains invalid model identifier syntax")

        provider = "native"
        provider_type = None
        wire_api = None
        base_url = None
        network_host = None
        if protocol == "openai-compatible":
            if engine != "copilot":
                raise _error(
                    profile_id,
                    "engine",
                    "openai-compatible profiles require the copilot BYOK engine",
                )
            provider = _require_string(raw_cfg, profile_id, "provider")
            if not PROVIDER_ID_RE.fullmatch(provider):
                raise _error(profile_id, "provider", "must match ^[a-z][a-z0-9-]*$")
            provider_type = _require_string(raw_cfg, profile_id, "provider_type")
            if provider_type != "openai":
                raise _error(
                    profile_id,
                    "provider_type",
                    "must be openai for openai-compatible protocol",
                )
            wire_api = _require_string(raw_cfg, profile_id, "wire_api")
            if wire_api not in SUPPORTED_WIRE_APIS:
                raise _error(profile_id, "wire_api", "must be completions or responses")
            if model is None:
                raise _error(profile_id, "model", "is required for openai-compatible profiles")
            base_url_raw = _require_string(raw_cfg, profile_id, "base_url")
            network_host_raw = _require_string(raw_cfg, profile_id, "network_host")
            base_url, network_host = _normalize_base_url(
                profile_id,
                base_url_raw,
                network_host_raw,
            )
        else:
            if engine not in SUPPORTED_NATIVE_ENGINES:
                raise _error(profile_id, "engine", f"unsupported native engine: {engine!r}")
            unexpected_openai_fields = sorted(
                field for field in OPENAI_ONLY_FIELDS if field in raw_cfg
            )
            if unexpected_openai_fields:
                raise _error(
                    profile_id,
                    None,
                    "native profile contains openai-compatible-only fields: "
                    + ", ".join(unexpected_openai_fields),
                )

        for value, field_name, seen in (
            (worker_source, "worker_source", seen_sources),
            (worker_workflow, "worker_workflow", seen_workers),
        ):
            previous = seen.get(value)
            if previous is not None:
                raise _error(profile_id, field_name, f"duplicates profile {previous!r}")
            seen[value] = profile_id
        for secret_name in (credential, *aliases):
            previous = seen_credentials.get(secret_name)
            if previous is not None:
                raise _error(
                    profile_id,
                    "credential",
                    f"secret name {secret_name!r} duplicates profile {previous!r}",
                )
            seen_credentials[secret_name] = profile_id

        normalized.append(
            EngineProfile(
                profile_id=profile_id,
                engine=engine,
                worker_source=worker_source,
                worker_workflow=worker_workflow,
                credential=credential,
                maturity=maturity,
                engine_version=engine_version,
                model=model,
                credential_aliases=aliases,
                protocol=protocol,
                provider=provider,
                provider_type=provider_type,
                wire_api=wire_api,
                base_url=base_url,
                network_host=network_host,
            )
        )

    by_id = MappingProxyType({profile.profile_id: profile for profile in normalized})
    by_worker = MappingProxyType({profile.worker_workflow: profile for profile in normalized})
    return ProviderRegistry(REGISTRY_VERSION, tuple(normalized), by_id, by_worker)
