#!/usr/bin/env python3
"""Trusted-control CAS materializer for reviewed v0.3 Vertical policy JSON.

This module deliberately does not widen the normal Operator Store mutation model.
It writes only the fixed protected policy namespace used by the reviewed Vertical
policy authority, and only after a fresh positive production protection proof.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any, Iterable

from operator_store_git import CasConflict
from operator_store_model import canonical_json, digest_json, normalize_repository
from operator_store_protection import (
    ProtectionReceipt,
    StateRefProtectionVerifier,
    require_protected,
)

POLICY_NAMESPACE = "config/operator/v03-vertical-policy"
DEFAULT_STATE_REF = "refs/heads/ai-sdlc-operator-state"
TRACKING_REF = "refs/ai-sdlc/protected-policy/materialization"
_COMMIT_MESSAGE = "AI-SDLC protected Vertical policy materialization\n"

REQUIRED_POLICY_FILENAMES = frozenset(
    {
        "effect-lineage-rollout.json",
        "writer-fence-receipt.json",
        "effect-resolution-policy.json",
        "effect-resolution-evidence.json",
        "decision-policy.json",
        "bundle-receipt.json",
    }
)
REQUIRED_POLICY_PATHS = frozenset(
    f"{POLICY_NAMESPACE}/{name}" for name in REQUIRED_POLICY_FILENAMES
)
_SAFE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.json$")


class ProtectedPolicyMaterializationError(RuntimeError):
    pass


def _sha(value: str, *, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"exact {label} SHA is required")
    return text


def _policy_path(value: str) -> str:
    path = str(value or "")
    if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError("invalid protected policy path")
    if PurePosixPath(path).as_posix() != path or path.startswith("./") or "//" in path:
        raise ValueError("protected policy path is not canonical")
    prefix = POLICY_NAMESPACE + "/"
    if not path.startswith(prefix):
        raise ValueError("protected policy path escapes fixed reviewed namespace")
    filename = path[len(prefix):]
    if "/" in filename or not _SAFE_FILENAME_RE.fullmatch(filename):
        raise ValueError("protected policy filename is not canonical")
    if path not in REQUIRED_POLICY_PATHS:
        raise ValueError("protected policy path is not part of the reviewed v0.3 bundle")
    return path


@dataclass(frozen=True)
class ProtectedPolicyDocument:
    path: str
    value: dict[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "path", _policy_path(self.path))
        if not isinstance(self.value, dict):
            raise ValueError("protected policy document must be a JSON object")
        try:
            json.dumps(
                self.value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("protected policy document is not strict JSON") from exc


@dataclass(frozen=True)
class ProtectedPolicyMaterializerConfig:
    repository: str
    trusted_checkout: Path
    state_ref: str = DEFAULT_STATE_REF
    remote_name: str = "origin"

    def __post_init__(self):
        object.__setattr__(self, "repository", normalize_repository(self.repository))
        object.__setattr__(self, "trusted_checkout", Path(self.trusted_checkout))
        if self.state_ref != DEFAULT_STATE_REF:
            raise ValueError("v0.3 policy materializer requires the reviewed protected state ref")
        if (
            not self.remote_name
            or self.remote_name.startswith("-")
            or any(ch.isspace() for ch in self.remote_name)
        ):
            raise ValueError("trusted policy materializer remote name is invalid")


@dataclass(frozen=True)
class ProtectedPolicyArtifactReceipt:
    path: str
    digest: str


@dataclass(frozen=True)
class ProtectedPolicyBundleReceipt:
    repository: str
    state_ref: str
    expected_ref_sha: str
    materialization_commit_sha: str
    namespace: str
    bundle_digest: str
    artifacts: tuple[ProtectedPolicyArtifactReceipt, ...]
    protection_verifier_identity: str
    protection_verified_at: str
    protection_policy_digest: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "state_ref": self.state_ref,
            "expected_ref_sha": self.expected_ref_sha,
            "materialization_commit_sha": self.materialization_commit_sha,
            "namespace": self.namespace,
            "bundle_digest": self.bundle_digest,
            "artifacts": [
                {"path": artifact.path, "digest": artifact.digest}
                for artifact in self.artifacts
            ],
            "protection": {
                "verifier_identity": self.protection_verifier_identity,
                "verified_at": self.protection_verified_at,
                "policy_digest": self.protection_policy_digest,
            },
        }


class ProtectedPolicyBundleMaterializer:
    """Create one exact fast-forward policy bundle commit on the protected state ref."""

    def __init__(
        self,
        config: ProtectedPolicyMaterializerConfig,
        *,
        protection_verifier: StateRefProtectionVerifier,
    ):
        self.config = config
        self.protection_verifier = protection_verifier
        if not callable(getattr(protection_verifier, "verify", None)):
            raise ValueError("trusted policy materializer requires a protection verifier")
        if bool(getattr(protection_verifier, "test_only", False)):
            raise ValueError("test-only protection verifier cannot enable policy materialization")

    def _git(
        self,
        *args: str,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        merged.update(env or {})
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.config.trusted_checkout,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=merged,
                check=False,
            )
        except OSError as exc:
            raise ProtectedPolicyMaterializationError(f"git execution failed: {exc}") from exc
        if check and result.returncode != 0:
            raise ProtectedPolicyMaterializationError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result

    def _fresh_protected_receipt(self) -> ProtectionReceipt:
        receipt = self.protection_verifier.verify(
            self.config.repository, self.config.state_ref
        )
        require_protected(
            receipt,
            repository=self.config.repository,
            state_ref=self.config.state_ref,
        )
        return receipt

    @staticmethod
    def _same_protection_generation(
        before: ProtectionReceipt, after: ProtectionReceipt
    ) -> bool:
        return (
            before.repository.lower() == after.repository.lower()
            and before.state_ref == after.state_ref
            and before.verifier_identity == after.verifier_identity
            and before.policy_digest == after.policy_digest
        )

    def _remote_sha(self) -> str | None:
        result = self._git(
            "ls-remote", "--refs", self.config.remote_name, self.config.state_ref,
            check=False,
        )
        if result.returncode != 0:
            raise CasConflict("unable to read protected policy state ref")
        line = result.stdout.strip()
        if not line:
            return None
        sha, separator, ref = line.partition("\t")
        if separator != "\t" or ref != self.config.state_ref:
            raise CasConflict("unexpected protected policy state-ref response")
        try:
            return _sha(sha, label="remote state-ref")
        except ValueError as exc:
            raise CasConflict("invalid protected policy state-ref SHA") from exc

    def _fetch_exact_remote(self, expected_sha: str) -> None:
        fetched = self._git(
            "fetch", "--no-tags", self.config.remote_name,
            f"+{self.config.state_ref}:{TRACKING_REF}", check=False,
        )
        if fetched.returncode != 0:
            raise CasConflict("unable to fetch protected policy state ref")
        local = self._git("rev-parse", "--verify", TRACKING_REF).stdout.strip()
        if local != expected_sha:
            raise CasConflict("protected policy state ref changed during fetch")

    def _namespace_paths(self, commit_sha: str) -> frozenset[str]:
        listed = self._git(
            "ls-tree", "-r", "--name-only", commit_sha, "--", POLICY_NAMESPACE,
            check=False,
        )
        if listed.returncode != 0:
            raise ProtectedPolicyMaterializationError(
                "unable to inspect protected policy namespace"
            )
        return frozenset(path for path in listed.stdout.splitlines() if path)

    def _validate_existing_namespace(self, expected_ref_sha: str) -> None:
        existing = self._namespace_paths(expected_ref_sha)
        unexpected = existing - REQUIRED_POLICY_PATHS
        if unexpected:
            raise ProtectedPolicyMaterializationError(
                "protected policy namespace contains unexpected existing paths"
            )

    @staticmethod
    def _normalize_documents(
        documents: Iterable[ProtectedPolicyDocument],
    ) -> tuple[ProtectedPolicyDocument, ...]:
        rows = tuple(documents)
        if not rows:
            raise ValueError("protected policy materialization requires documents")
        seen: set[str] = set()
        normalized: list[ProtectedPolicyDocument] = []
        for row in rows:
            if not isinstance(row, ProtectedPolicyDocument):
                raise ValueError("protected policy materialization requires typed documents")
            if row.path in seen:
                raise ValueError("duplicate protected policy path in one materialization")
            seen.add(row.path)
            normalized.append(row)
        if seen != REQUIRED_POLICY_PATHS:
            raise ValueError("protected policy materialization requires the exact v0.3 bundle")
        return tuple(sorted(normalized, key=lambda row: row.path))

    def _build_commit(
        self,
        expected_ref_sha: str,
        documents: tuple[ProtectedPolicyDocument, ...],
    ) -> str:
        fd, index_path = tempfile.mkstemp(prefix="ai-sdlc-protected-policy-index-")
        os.close(fd)
        os.unlink(index_path)
        try:
            env = {"GIT_INDEX_FILE": index_path}
            self._git("read-tree", expected_ref_sha, env=env)
            for document in documents:
                blob = self._git(
                    "hash-object", "-w", "--stdin",
                    input_text=canonical_json(document.value) + "\n",
                ).stdout.strip()
                _sha(blob, label="policy blob")
                self._git(
                    "update-index", "--add", "--cacheinfo",
                    f"100644,{blob},{document.path}", env=env,
                )
            tree = self._git("write-tree", env=env).stdout.strip()
            _sha(tree, label="policy tree")
            commit = self._git(
                "commit-tree", tree, "-p", expected_ref_sha,
                input_text=_COMMIT_MESSAGE,
            ).stdout.strip()
            return _sha(commit, label="policy materialization commit")
        finally:
            try:
                os.unlink(index_path)
            except FileNotFoundError:
                pass

    def _push_candidate(self, candidate_sha: str) -> None:
        pushed = self._git(
            "push", "--porcelain", self.config.remote_name,
            f"{candidate_sha}:{self.config.state_ref}", check=False,
        )
        if pushed.returncode != 0:
            raise CasConflict("protected policy state-ref CAS push rejected")

    def _read_json_at(self, commit_sha: str, path: str) -> dict[str, Any]:
        result = self._git("show", f"{commit_sha}:{_policy_path(path)}", check=False)
        if result.returncode != 0:
            raise ProtectedPolicyMaterializationError("durable policy document is missing")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProtectedPolicyMaterializationError(
                "durable policy document is invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ProtectedPolicyMaterializationError(
                "durable policy document is not an object"
            )
        return value

    def _verify_durable(
        self,
        commit_sha: str,
        documents: tuple[ProtectedPolicyDocument, ...],
    ) -> tuple[ProtectedPolicyArtifactReceipt, ...]:
        if self._namespace_paths(commit_sha) != REQUIRED_POLICY_PATHS:
            raise ProtectedPolicyMaterializationError(
                "durable protected policy namespace is not the exact reviewed bundle"
            )
        receipts = []
        for document in documents:
            durable = self._read_json_at(commit_sha, document.path)
            if canonical_json(durable) != canonical_json(document.value):
                raise ProtectedPolicyMaterializationError(
                    f"durable protected policy digest mismatch: {document.path}"
                )
            receipts.append(
                ProtectedPolicyArtifactReceipt(document.path, digest_json(durable))
            )
        return tuple(receipts)

    def materialize(
        self,
        *,
        expected_ref_sha: str,
        documents: Iterable[ProtectedPolicyDocument],
    ) -> ProtectedPolicyBundleReceipt:
        expected = _sha(expected_ref_sha, label="expected protected state-ref")
        normalized = self._normalize_documents(documents)
        protection_before = self._fresh_protected_receipt()

        if self._remote_sha() != expected:
            raise CasConflict("protected policy state ref differs from expected SHA")
        self._fetch_exact_remote(expected)
        self._validate_existing_namespace(expected)
        candidate = self._build_commit(expected, normalized)

        # Re-check both protection and remote generation immediately before transport.
        # A race after the remote read remains rejected by ordinary non-force push.
        protection_before_push = self._fresh_protected_receipt()
        if not self._same_protection_generation(
            protection_before, protection_before_push
        ):
            raise ProtectedPolicyMaterializationError(
                "protected-state policy changed during materialization"
            )
        if self._remote_sha() != expected:
            raise CasConflict("protected policy state ref changed before push")
        self._push_candidate(candidate)

        durable_sha = self._remote_sha()
        if durable_sha != candidate:
            raise CasConflict(
                "protected policy state ref did not confirm materialization commit"
            )
        self._fetch_exact_remote(candidate)
        artifacts = self._verify_durable(candidate, normalized)
        bundle_digest = digest_json({row.path: row.digest for row in artifacts})
        return ProtectedPolicyBundleReceipt(
            repository=self.config.repository,
            state_ref=self.config.state_ref,
            expected_ref_sha=expected,
            materialization_commit_sha=candidate,
            namespace=POLICY_NAMESPACE,
            bundle_digest=bundle_digest,
            artifacts=artifacts,
            protection_verifier_identity=protection_before_push.verifier_identity,
            protection_verified_at=protection_before_push.verified_at,
            protection_policy_digest=protection_before_push.policy_digest,
        )
