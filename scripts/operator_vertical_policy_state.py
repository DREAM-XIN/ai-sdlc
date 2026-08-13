#!/usr/bin/env python3
"""Exact-commit trusted policy loading for the v0.3 production Vertical runtime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from operator_decision_policy import ProtectedDecisionPolicyVerifier
from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
from operator_effect_rollout import (
    LINEAGE_WRITER_CAPABILITY,
    ProtectedEffectLineageRolloutVerifier,
    REQUIRED_FENCED_CAPABILITIES,
)
from operator_store import StoreCommandError
from operator_store_model import digest_json, normalize_repository
from operator_vertical import VERTICAL_PROFILE

BUNDLE_SCHEMA = "ai-sdlc.vertical-policy-bundle-receipt/v1"


@dataclass(frozen=True)
class TrustedVerticalPolicyAuthority:
    receipt_ref: str
    receipt_digest: str
    bundle_digest: str
    rollout_verifier: ProtectedEffectLineageRolloutVerifier
    resolution_policy_verifier: ProtectedEffectResolutionPolicyVerifier
    decision_policy_verifier: ProtectedDecisionPolicyVerifier


def exact_ref(repository: str, commit_sha: str, path: str) -> str:
    repository = normalize_repository(repository)
    sha = commit_sha.lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError("exact installation commit SHA is required")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError("invalid trusted policy path")
    return f"default-branch://{repository}@{sha}/{path}"


def seal_receipt(
    *, repository: str, commit_sha: str, state_ref: str, operation_profile: str,
    artifacts: dict[str, tuple[str, dict[str, Any]]], issued_at: str, issuer: str,
    receipt_path: str,
) -> dict[str, Any]:
    required = {"rollout", "writer_fence", "resolution", "resolution_evidence", "decision"}
    if set(artifacts) != required or operation_profile != VERTICAL_PROFILE:
        raise StoreCommandError("POLICY_DENIED", "Vertical policy bundle scope is invalid")
    descriptors = {}
    for name in sorted(required):
        path, value = artifacts[name]
        exact_ref(repository, commit_sha, path)
        descriptors[name] = {"path": path, "digest": digest_json(value)}
    material = {
        "repository": normalize_repository(repository),
        "installation_commit_sha": commit_sha.lower(),
        "state_ref": state_ref,
        "operation_profile": operation_profile,
        "artifacts": descriptors,
    }
    receipt = {
        "schema_version": BUNDLE_SCHEMA,
        **material,
        "bundle_digest": digest_json(material),
        "issued_at": issued_at,
        "issuer": issuer,
        "receipt_ref": exact_ref(repository, commit_sha, receipt_path),
    }
    receipt["receipt_digest"] = digest_json(receipt)
    return receipt


class ProtectedVerticalPolicyBundleLoader:
    def __init__(
        self, *, repository: str, commit_sha: str, state_ref: str,
        operation_profile: str, receipt_path: str,
        document_loader: Callable[[str, str], dict[str, Any]],
    ):
        self.repository = normalize_repository(repository)
        self.commit_sha = commit_sha.lower()
        self.state_ref = state_ref
        self.operation_profile = operation_profile
        self.receipt_path = receipt_path
        self.document_loader = document_loader
        exact_ref(self.repository, self.commit_sha, receipt_path)
        if operation_profile != VERTICAL_PROFILE or not state_ref.startswith("refs/heads/"):
            raise ValueError("reviewed Vertical profile and Store branch ref are required")
        if not callable(document_loader):
            raise ValueError("exact-commit document loader is required")

    def _artifact(self, descriptor):
        if not isinstance(descriptor, dict):
            raise StoreCommandError("POLICY_DENIED", "invalid policy artifact descriptor")
        path, expected = descriptor.get("path"), descriptor.get("digest")
        exact_ref(self.repository, self.commit_sha, str(path or ""))
        value = self.document_loader(self.commit_sha, path)
        if not isinstance(value, dict) or digest_json(value) != expected:
            raise StoreCommandError("POLICY_DENIED", "policy artifact is missing or stale")
        return value

    def load(self) -> TrustedVerticalPolicyAuthority:
        receipt = self.document_loader(self.commit_sha, self.receipt_path)
        if not isinstance(receipt, dict) or receipt.get("schema_version") != BUNDLE_SCHEMA:
            raise StoreCommandError("POLICY_DENIED", "policy bundle receipt is missing")
        if normalize_repository(str(receipt.get("repository", ""))) != self.repository:
            raise StoreCommandError("POLICY_DENIED", "receipt repository mismatch")
        if receipt.get("installation_commit_sha") != self.commit_sha:
            raise StoreCommandError("POLICY_DENIED", "receipt installation commit mismatch")
        if receipt.get("state_ref") != self.state_ref or receipt.get("operation_profile") != self.operation_profile:
            raise StoreCommandError("POLICY_DENIED", "receipt Store/profile mismatch")
        if receipt.get("receipt_ref") != exact_ref(self.repository, self.commit_sha, self.receipt_path):
            raise StoreCommandError("POLICY_DENIED", "receipt ref mismatch")
        raw = dict(receipt)
        stored_receipt_digest = raw.pop("receipt_digest", None)
        if stored_receipt_digest != digest_json(raw):
            raise StoreCommandError("POLICY_DENIED", "receipt digest mismatch")
        material = {
            "repository": self.repository,
            "installation_commit_sha": self.commit_sha,
            "state_ref": self.state_ref,
            "operation_profile": self.operation_profile,
            "artifacts": receipt.get("artifacts"),
        }
        if receipt.get("bundle_digest") != digest_json(material):
            raise StoreCommandError("POLICY_DENIED", "bundle digest mismatch")
        descriptors = receipt.get("artifacts")
        required = {"rollout", "writer_fence", "resolution", "resolution_evidence", "decision"}
        if not isinstance(descriptors, dict) or set(descriptors) != required:
            raise StoreCommandError("POLICY_DENIED", "policy bundle artifact set is not exact")

        rollout = self._artifact(descriptors["rollout"])
        fence = self._artifact(descriptors["writer_fence"])
        resolution = self._artifact(descriptors["resolution"])
        evidence = self._artifact(descriptors["resolution_evidence"])
        decision = self._artifact(descriptors["decision"])

        fence_ref = exact_ref(self.repository, self.commit_sha, descriptors["writer_fence"]["path"])
        if rollout.get("writer_fence_receipt_ref") != fence_ref:
            raise StoreCommandError("MIXED_WRITER_FENCED", "rollout fence reference mismatch")
        rollout_verifier = ProtectedEffectLineageRolloutVerifier(
            policy_loader=lambda repo, ref, profile: rollout
            if (repo, ref, profile) == (self.repository, self.state_ref, self.operation_profile) else {},
            writer_fence_receipt_loader=lambda ref: fence if ref == fence_ref else {},
        )
        verified = rollout_verifier.verify(
            repository=self.repository, state_ref=self.state_ref,
            operation_profile=self.operation_profile,
        )
        fenced = frozenset(str(v) for v in fence.get("fenced_capabilities", []))
        if verified.test_only or not verified.effect_lineage_required:
            raise StoreCommandError("MIXED_WRITER_FENCED", "non-production rollout authority")
        if verified.writer_capability != LINEAGE_WRITER_CAPABILITY:
            raise StoreCommandError("MIXED_WRITER_FENCED", "lineage-aware writer capability missing")
        if not REQUIRED_FENCED_CAPABILITIES.issubset(fenced):
            raise StoreCommandError("MIXED_WRITER_FENCED", "writer-fence coverage incomplete")

        source_id = str(resolution.get("evidence_source_id") or "")
        source_digest = str(resolution.get("evidence_source_digest") or "")
        facts = evidence.get("facts")
        if evidence.get("source_id") != source_id or not isinstance(facts, dict):
            raise StoreCommandError("POLICY_RESTRICTED", "resolution evidence source mismatch")
        if evidence.get("source_digest") != source_digest or digest_json(facts) != source_digest:
            raise StoreCommandError("POLICY_RESTRICTED", "resolution evidence digest mismatch")
        resolution_verifier = ProtectedEffectResolutionPolicyVerifier(
            repository=self.repository, state_ref=self.state_ref,
            operation_profile=self.operation_profile,
            policy_loader=lambda repo, ref, profile: resolution
            if (repo, ref, profile) == (self.repository, self.state_ref, self.operation_profile) else {},
            evidence_fact_loader=lambda sid, ref: facts.get(ref, {}) if sid == source_id else {},
        )
        resolution_verifier.verify_current()
        decision_verifier = ProtectedDecisionPolicyVerifier(
            repository=self.repository, state_ref=self.state_ref,
            operation_profile=self.operation_profile,
            policy_loader=lambda repo, ref, profile: decision
            if (repo, ref, profile) == (self.repository, self.state_ref, self.operation_profile) else {},
        )
        decision_verifier._load_base()

        return TrustedVerticalPolicyAuthority(
            receipt_ref=receipt["receipt_ref"], receipt_digest=stored_receipt_digest,
            bundle_digest=receipt["bundle_digest"], rollout_verifier=rollout_verifier,
            resolution_policy_verifier=resolution_verifier,
            decision_policy_verifier=decision_verifier,
        )
