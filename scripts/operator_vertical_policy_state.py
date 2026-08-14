#!/usr/bin/env python3
"""Two-anchor trusted policy loading for the v0.3 production Vertical runtime.

A realizable policy bundle has two different Git authorities:

* ``installation_commit_sha`` is an already-existing reviewed default-branch
  code/install commit. Policy bytes may safely bind it because its SHA is known
  before materialization starts.
* ``materialization_commit_sha`` is the later commit that actually stores the
  policy documents/receipt on the protected Operator state ref. Its SHA is
  supplied independently after Git creates that commit; it is never embedded in
  the bytes whose hash creates the commit.

The loader verifies both anchors and also re-reads the current protected policy
paths. Ordinary later Operation Store writes may therefore advance the state ref
without invalidating the policy generation, while any policy-path drift fails
closed.
"""
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


def _sha(value: str, *, label: str) -> str:
    sha = str(value or "").lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError(f"exact {label} commit SHA is required")
    return sha


def _path(value: str) -> str:
    path = str(value or "")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise ValueError("invalid trusted policy path")
    return path


def exact_ref(repository: str, commit_sha: str, path: str) -> str:
    """Immutable default-branch/install artifact reference.

    Kept for callers that need to cite an already-existing installation commit;
    live policy documents themselves use ``protected_ref`` so they never need to
    predict their own future materialization SHA.
    """
    repository = normalize_repository(repository)
    sha = _sha(commit_sha, label="installation")
    return f"default-branch://{repository}@{sha}/{_path(path)}"


def protected_ref(repository: str, state_ref: str, path: str) -> str:
    repository = normalize_repository(repository)
    if not state_ref.startswith("refs/heads/"):
        raise ValueError("protected policy state ref must be a branch ref")
    return f"protected://{repository}@{state_ref}/{_path(path)}"


def protected_commit_ref(repository: str, commit_sha: str, path: str) -> str:
    repository = normalize_repository(repository)
    sha = _sha(commit_sha, label="materialization")
    return f"protected-commit://{repository}@{sha}/{_path(path)}"


@dataclass(frozen=True)
class TrustedVerticalPolicyAuthority:
    receipt_ref: str
    receipt_digest: str
    bundle_digest: str
    installation_commit_sha: str
    materialization_commit_sha: str
    rollout_verifier: ProtectedEffectLineageRolloutVerifier
    resolution_policy_verifier: ProtectedEffectResolutionPolicyVerifier
    decision_policy_verifier: ProtectedDecisionPolicyVerifier


def seal_receipt(
    *, repository: str, installation_commit_sha: str, state_ref: str,
    operation_profile: str, artifacts: dict[str, tuple[str, dict[str, Any]]],
    issued_at: str, issuer: str, receipt_path: str,
) -> dict[str, Any]:
    """Seal policy bytes before the protected materialization commit exists.

    The receipt binds an already-existing reviewed installation commit plus exact
    artifact digests. It deliberately does not contain the future materialization
    commit SHA or an exact receipt ref; those are known only after Git creates the
    commit and are supplied independently to the loader.
    """
    required = {"rollout", "writer_fence", "resolution", "resolution_evidence", "decision"}
    repository = normalize_repository(repository)
    installation_commit_sha = _sha(installation_commit_sha, label="installation")
    _path(receipt_path)
    if not state_ref.startswith("refs/heads/") or set(artifacts) != required or operation_profile != VERTICAL_PROFILE:
        raise StoreCommandError("POLICY_DENIED", "Vertical policy bundle scope is invalid")
    descriptors: dict[str, dict[str, str]] = {}
    for name in sorted(required):
        path, value = artifacts[name]
        descriptors[name] = {"path": _path(path), "digest": digest_json(value)}
    material = {
        "repository": repository,
        "installation_commit_sha": installation_commit_sha,
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
    }
    receipt["receipt_digest"] = digest_json(receipt)
    return receipt


class ProtectedVerticalPolicyBundleLoader:
    """Load one exact protected materialization bound to reviewed installation code.

    ``installation_commit_verifier`` is trusted installation/default-branch
    authority. ``materialization_commit_verifier`` proves that the exact policy
    commit is still authorized by the protected state ref (for example, it is a
    protected-ref ancestor and remains the current policy generation).
    ``protected_document_loader`` re-reads the current protected paths so ordinary
    later Operation Store commits are allowed while any policy-path drift fails
    closed.
    """

    def __init__(
        self, *, repository: str, installation_commit_sha: str,
        materialization_commit_sha: str, state_ref: str, operation_profile: str,
        receipt_path: str, document_loader: Callable[[str, str], dict[str, Any]],
        protected_document_loader: Callable[[str, str, str], dict[str, Any]],
        installation_commit_verifier: Callable[[str, str], bool],
        materialization_commit_verifier: Callable[[str, str, str], bool],
    ):
        self.repository = normalize_repository(repository)
        self.installation_commit_sha = _sha(installation_commit_sha, label="installation")
        self.materialization_commit_sha = _sha(materialization_commit_sha, label="materialization")
        self.state_ref = state_ref
        self.operation_profile = operation_profile
        self.receipt_path = _path(receipt_path)
        self.document_loader = document_loader
        self.protected_document_loader = protected_document_loader
        self.installation_commit_verifier = installation_commit_verifier
        self.materialization_commit_verifier = materialization_commit_verifier
        if operation_profile != VERTICAL_PROFILE or not state_ref.startswith("refs/heads/"):
            raise ValueError("reviewed Vertical profile and protected Store branch ref are required")
        if not all(callable(value) for value in (
            document_loader,
            protected_document_loader,
            installation_commit_verifier,
            materialization_commit_verifier,
        )):
            raise ValueError("trusted two-anchor Vertical policy loaders/verifiers are required")

    def _verify_anchors(self) -> None:
        if not self.installation_commit_verifier(self.repository, self.installation_commit_sha):
            raise StoreCommandError("POLICY_DENIED", "reviewed installation commit authority failed")
        if not self.materialization_commit_verifier(
            self.repository, self.state_ref, self.materialization_commit_sha
        ):
            raise StoreCommandError("POLICY_DENIED", "protected policy materialization commit authority failed")

    def _exact_document(self, path: str) -> dict[str, Any]:
        value = self.document_loader(self.materialization_commit_sha, _path(path))
        if not isinstance(value, dict):
            raise StoreCommandError("POLICY_DENIED", "exact policy materialization document is missing")
        return value

    def _current_protected_document(self, path: str) -> dict[str, Any]:
        value = self.protected_document_loader(self.repository, self.state_ref, _path(path))
        if not isinstance(value, dict):
            raise StoreCommandError("POLICY_DENIED", "current protected policy document is missing")
        return value

    def _artifact(self, descriptor: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "digest"}:
            raise StoreCommandError("POLICY_DENIED", "invalid policy artifact descriptor")
        path = _path(descriptor.get("path"))
        expected = str(descriptor.get("digest") or "")
        if len(expected) != 64:
            raise StoreCommandError("POLICY_DENIED", "policy artifact digest is invalid")
        exact = self._exact_document(path)
        current = self._current_protected_document(path)
        if digest_json(exact) != expected or digest_json(current) != expected:
            raise StoreCommandError("POLICY_DENIED", "policy artifact is stale or protected path drifted")
        return exact

    def load(self) -> TrustedVerticalPolicyAuthority:
        self._verify_anchors()
        receipt = self._exact_document(self.receipt_path)
        current_receipt = self._current_protected_document(self.receipt_path)
        if digest_json(receipt) != digest_json(current_receipt):
            raise StoreCommandError("POLICY_DENIED", "protected policy receipt drifted after materialization")
        if receipt.get("schema_version") != BUNDLE_SCHEMA:
            raise StoreCommandError("POLICY_DENIED", "policy bundle receipt is invalid")
        if normalize_repository(str(receipt.get("repository", ""))) != self.repository:
            raise StoreCommandError("POLICY_DENIED", "receipt repository mismatch")
        if receipt.get("installation_commit_sha") != self.installation_commit_sha:
            raise StoreCommandError("POLICY_DENIED", "receipt installation commit mismatch")
        if receipt.get("state_ref") != self.state_ref or receipt.get("operation_profile") != self.operation_profile:
            raise StoreCommandError("POLICY_DENIED", "receipt Store/profile mismatch")
        # A realizable Git object may not claim to contain its own final SHA/ref.
        if "materialization_commit_sha" in receipt or "receipt_ref" in receipt:
            raise StoreCommandError("POLICY_DENIED", "policy receipt contains a self-referential materialization field")
        raw = dict(receipt)
        stored_receipt_digest = raw.pop("receipt_digest", None)
        if stored_receipt_digest != digest_json(raw):
            raise StoreCommandError("POLICY_DENIED", "receipt digest mismatch")
        descriptors = receipt.get("artifacts")
        required = {"rollout", "writer_fence", "resolution", "resolution_evidence", "decision"}
        if not isinstance(descriptors, dict) or set(descriptors) != required:
            raise StoreCommandError("POLICY_DENIED", "policy bundle artifact set is not exact")
        material = {
            "repository": self.repository,
            "installation_commit_sha": self.installation_commit_sha,
            "state_ref": self.state_ref,
            "operation_profile": self.operation_profile,
            "artifacts": descriptors,
        }
        if receipt.get("bundle_digest") != digest_json(material):
            raise StoreCommandError("POLICY_DENIED", "bundle digest mismatch")

        rollout = self._artifact(descriptors["rollout"])
        fence = self._artifact(descriptors["writer_fence"])
        resolution = self._artifact(descriptors["resolution"])
        evidence = self._artifact(descriptors["resolution_evidence"])
        decision = self._artifact(descriptors["decision"])

        rollout_ref = protected_ref(self.repository, self.state_ref, descriptors["rollout"]["path"])
        fence_ref = protected_ref(self.repository, self.state_ref, descriptors["writer_fence"]["path"])
        resolution_ref = protected_ref(self.repository, self.state_ref, descriptors["resolution"]["path"])
        decision_ref = protected_ref(self.repository, self.state_ref, descriptors["decision"]["path"])
        if rollout.get("policy_ref") != rollout_ref or rollout.get("writer_fence_receipt_ref") != fence_ref:
            raise StoreCommandError("MIXED_WRITER_FENCED", "rollout policy protected source binding mismatch")
        if resolution.get("policy_ref") != resolution_ref:
            raise StoreCommandError("POLICY_RESTRICTED", "Effect Resolution protected source binding mismatch")
        if decision.get("policy_ref") != decision_ref:
            raise StoreCommandError("POLICY_DENIED", "Decision protected source binding mismatch")

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
            receipt_ref=protected_commit_ref(
                self.repository, self.materialization_commit_sha, self.receipt_path
            ),
            receipt_digest=str(stored_receipt_digest),
            bundle_digest=str(receipt["bundle_digest"]),
            installation_commit_sha=self.installation_commit_sha,
            materialization_commit_sha=self.materialization_commit_sha,
            rollout_verifier=rollout_verifier,
            resolution_policy_verifier=resolution_verifier,
            decision_policy_verifier=decision_verifier,
        )
