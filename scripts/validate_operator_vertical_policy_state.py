#!/usr/bin/env python3
"""Adversarial validation for exact-commit v0.3 Vertical policy authority."""
from copy import deepcopy

from operator_effect_resolution import ALLOWED_RESOLUTION_CHOICES, EFFECT_RESOLUTION_POLICY_SCHEMA
from operator_effect_rollout import LINEAGE_WRITER_CAPABILITY, REQUIRED_FENCED_CAPABILITIES, ROLLOUT_SCHEMA, WRITER_FENCE_SCHEMA
from operator_store import StoreCommandError
from operator_store_model import digest_json
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_policy_state import ProtectedVerticalPolicyBundleLoader, exact_ref, seal_receipt

REPO = "dream-xin/ai-sdlc"
TARGET = "dream-xin/target"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
COMMIT = "a" * 40
ROOT = "config/operator/v03-vertical-policy"
RECEIPT_PATH = f"{ROOT}/bundle-receipt.json"


def policy(document):
    row = dict(document)
    row["policy_digest"] = digest_json(row)
    return row


def build_documents():
    paths = {
        "rollout": f"{ROOT}/effect-lineage-rollout.json",
        "writer_fence": f"{ROOT}/writer-fence-receipt.json",
        "resolution": f"{ROOT}/effect-resolution-policy.json",
        "resolution_evidence": f"{ROOT}/effect-resolution-evidence.json",
        "decision": f"{ROOT}/decision-policy.json",
    }
    fence = {
        "schema_version": WRITER_FENCE_SCHEMA,
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "state": "QUIESCED",
        "fenced_capabilities": sorted(REQUIRED_FENCED_CAPABILITIES),
        "receipt_id": "writer-fence-v03",
        "issued_at": "2026-08-14T00:00:00Z",
        "issuer": "trusted-release-controller",
    }
    rollout = policy({
        "schema_version": ROLLOUT_SCHEMA,
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": exact_ref(REPO, COMMIT, paths["rollout"]),
        "effect_lineage_required": True,
        "writer_capability": LINEAGE_WRITER_CAPABILITY,
        "writer_fence_receipt_ref": exact_ref(REPO, COMMIT, paths["writer_fence"]),
    })
    facts = {
        "evidence://launch": {
            "type": "EXTERNAL_LAUNCH_RECEIPT",
            "external_dispatch_key": "dispatch-example",
            "receipt_id": "run-1",
        }
    }
    source_digest = digest_json(facts)
    evidence = {
        "source_id": "protected-default-branch-v03",
        "source_digest": source_digest,
        "facts": facts,
    }
    resolution = policy({
        "schema_version": EFFECT_RESOLUTION_POLICY_SCHEMA,
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": exact_ref(REPO, COMMIT, paths["resolution"]),
        "policy_epoch": "v0.3-release-1",
        "authority_id": "trusted-release-controller",
        "allowed_choices": sorted(ALLOWED_RESOLUTION_CHOICES),
        "allowed_resolvers": ["trusted-release-controller"],
        "trusted_profile_digest": "reviewed-v03-profile",
        "strong_evidence_types": [],
        "evidence_source_id": evidence["source_id"],
        "evidence_source_digest": source_digest,
    })
    decision = policy({
        "schema_version": "ai-sdlc.decision-policy/v1",
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": exact_ref(REPO, COMMIT, paths["decision"]),
        "policy_epoch": "v0.3-release-1",
        "allowed_target_repositories": [TARGET],
        "decision_types": {
            "review-remediation": {
                "choices": {"REMEDIATE": "operation.resume", "CANCEL": "operation.cancel"},
                "allowed_responders": ["human:product-owner"],
                "ttl_seconds": 3600,
                "warning_seconds": 600,
            }
        },
    })
    artifacts = {
        "rollout": (paths["rollout"], rollout),
        "writer_fence": (paths["writer_fence"], fence),
        "resolution": (paths["resolution"], resolution),
        "resolution_evidence": (paths["resolution_evidence"], evidence),
        "decision": (paths["decision"], decision),
    }
    receipt = seal_receipt(
        repository=REPO, commit_sha=COMMIT, state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE, artifacts=artifacts,
        issued_at="2026-08-14T00:00:00Z", issuer="trusted-release-controller",
        receipt_path=RECEIPT_PATH,
    )
    docs = {path: deepcopy(value) for _name, (path, value) in artifacts.items()}
    docs[RECEIPT_PATH] = receipt
    return paths, docs


def load(docs, *, repository=REPO, commit_sha=COMMIT, state_ref=STATE_REF):
    def document_loader(requested_sha, path):
        if requested_sha != COMMIT:
            return {}
        return deepcopy(docs.get(path))
    return ProtectedVerticalPolicyBundleLoader(
        repository=repository, commit_sha=commit_sha, state_ref=state_ref,
        operation_profile=VERTICAL_PROFILE, receipt_path=RECEIPT_PATH,
        document_loader=document_loader,
    ).load()


def expect_closed(docs, message, **kwargs):
    try:
        load(docs, **kwargs)
    except (StoreCommandError, ValueError):
        return
    raise AssertionError(message)


def reseal(docs):
    receipt = docs[RECEIPT_PATH]
    for _name, descriptor in receipt["artifacts"].items():
        descriptor["digest"] = digest_json(docs[descriptor["path"]])
    material = {
        "repository": receipt["repository"],
        "installation_commit_sha": receipt["installation_commit_sha"],
        "state_ref": receipt["state_ref"],
        "operation_profile": receipt["operation_profile"],
        "artifacts": receipt["artifacts"],
    }
    receipt["bundle_digest"] = digest_json(material)
    raw = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    receipt["receipt_digest"] = digest_json(raw)


def main():
    paths, docs = build_documents()
    authority = load(docs)
    rollout = authority.rollout_verifier.verify(
        repository=REPO, state_ref=STATE_REF, operation_profile=VERTICAL_PROFILE
    )
    assert rollout.effect_lineage_required
    assert rollout.writer_capability == LINEAGE_WRITER_CAPABILITY
    authority.resolution_policy_verifier.verify_current()
    authority.decision_policy_verifier.verify_current(
        target_repository=TARGET, feature_id="F-REAL-RUNTIME-0001",
        target_ref="feature/F-REAL-RUNTIME-0001", decision_type="review-remediation",
    )

    stale = deepcopy(docs)
    stale[paths["resolution"]]["policy_epoch"] = "tampered"
    expect_closed(stale, "stale policy digest accepted")

    missing_fence = deepcopy(docs)
    missing_fence[paths["writer_fence"]]["fenced_capabilities"] = [
        sorted(REQUIRED_FENCED_CAPABILITIES)[0]
    ]
    reseal(missing_fence)
    expect_closed(missing_fence, "incomplete writer fence accepted")

    test_only = deepcopy(docs)
    test_only[paths["rollout"]]["policy_ref"] = "test-only://inert"
    test_only[paths["rollout"]]["policy_digest"] = digest_json(
        {k: v for k, v in test_only[paths["rollout"]].items() if k != "policy_digest"}
    )
    reseal(test_only)
    expect_closed(test_only, "test-only policy promoted")

    evidence_drift = deepcopy(docs)
    evidence_drift[paths["resolution_evidence"]]["facts"]["evidence://launch"]["receipt_id"] = "forged"
    reseal(evidence_drift)
    expect_closed(evidence_drift, "evidence source drift accepted")

    expect_closed(docs, "wrong repository accepted", repository="dream-xin/foreign")
    expect_closed(docs, "wrong state ref accepted", state_ref="refs/heads/foreign-state")
    expect_closed(docs, "wrong exact commit accepted", commit_sha="b" * 40)

    print("trusted v0.3 Vertical policy authority validation passed")
    print("- exact repository/state-ref/profile/installation-commit binding")
    print("- complete QUIESCED old-writer fence")
    print("- exact Effect Resolution policy/evidence-source digest")
    print("- protected target-scoped Decision policy")
    print("- stale/tampered/test-only authority fails closed")


if __name__ == "__main__":
    main()
