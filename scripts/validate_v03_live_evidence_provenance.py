#!/usr/bin/env python3
"""Zero-effect validation for the Issue #221 live evidence provenance writer."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from operator_store_protection import ProtectionReceipt
import v03_live_evidence_provenance as subject

REPOSITORY = "dream-xin/ai-sdlc"
FEATURE = "F-OPERATOR-V03-REAL-RUNTIME-FI-0001"
REF = "verification/v0.3-real-runtime-fixture-221"
MAIN = "1" * 40
MATERIALIZATION = "2" * 40
POLICY = "3" * 64
SECRET_MARKER = "must-never-appear-in-provenance"


def require(value, message):
    if not value:
        raise AssertionError(message)


def preflight(*, protected=True, lineage=True, quiesced=True):
    receipt = ProtectionReceipt(
        repository=REPOSITORY,
        state_ref="refs/heads/ai-sdlc-operator-state",
        status="PROTECTED" if protected else "UNPROTECTED",
        verifier_identity="trusted-live-test-verifier",
        verified_at="2026-08-18T00:00:00Z",
        policy_digest="4" * 64,
    )
    config = SimpleNamespace(
        effect_lineage_required=lineage,
        old_writers_quiesced=quiesced,
        secret=SECRET_MARKER,
    )
    executor = SimpleNamespace(base=SimpleNamespace(config=config))
    composition = SimpleNamespace(
        feature_id=FEATURE,
        target_ref=REF,
        bundle=SimpleNamespace(executor=executor),
        secret=SECRET_MARKER,
    )
    policy = SimpleNamespace(
        installation_commit_sha=MAIN,
        materialization_commit_sha=MATERIALIZATION,
        bundle_digest=POLICY,
        secret=SECRET_MARKER,
    )
    live = SimpleNamespace(
        materialization_commit_sha=MATERIALIZATION,
        protection_receipt=receipt,
        policy=policy,
        secret=SECRET_MARKER,
    )
    execution = SimpleNamespace(
        repository=REPOSITORY,
        installation_commit_sha=MAIN,
        state_ref="refs/heads/ai-sdlc-operator-state",
        secret=SECRET_MARKER,
    )
    return SimpleNamespace(
        execution=execution,
        live_authority=live,
        composition=composition,
        secret=SECRET_MARKER,
    )


def evidence_document():
    return {
        "schema_version": "ai-sdlc.v03-live-persist-ack-loss/v1",
        "status": "PASS",
        "overall_issue_221_pass": False,
        "operation_id": "op-1",
    }


def expect_error(fn, contains):
    try:
        fn()
    except subject.LiveEvidenceProvenanceError as exc:
        require(contains in str(exc), f"wrong error: {exc}")
    else:
        raise AssertionError(f"expected error containing {contains!r}")


def validate_exact_authority_and_artifact_digest():
    fixture = preflight()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        evidence = root / "evidence.json"
        raw = (json.dumps(evidence_document(), indent=2, sort_keys=True) + "\n").encode()
        evidence.write_bytes(raw)
        provenance_path = root / "provenance.json"
        authority_path = root / "authority.json"
        authority, provenance = subject.write_live_evidence_envelope(
            preflight=fixture,
            evidence_path=evidence,
            provenance_path=provenance_path,
            authority_path=authority_path,
            github_workflow_run_id="32100123456",
            workflow_sha=MAIN,
        )
        require(authority["schema_version"] == "ai-sdlc.v03-effect-safety-live-authority/v1", "wrong authority schema")
        require(authority["trusted_main_head_sha"] == MAIN, "authority lost exact main")
        require(authority["materialization_commit_sha"] == MATERIALIZATION, "authority lost materialization anchor")
        require(authority["policy_bundle_digest"] == POLICY, "authority lost policy digest")
        require(authority["protected_policy_status"] == "PROTECTED", "authority did not retain positive protection")
        require(authority["effect_lineage_required"] is True and authority["writer_fence_quiesced"] is True, "authority lost lineage/fence")
        require(provenance["artifact_sha256"] == hashlib.sha256(raw).hexdigest(), "provenance digest not bound to exact bytes")
        require(provenance["github_workflow_run_id"] == 32100123456, "provenance lost workflow run id")
        require(provenance["evidence_class"] == "release-live-real-runtime", "provenance allowed wrong evidence class")
        require(provenance["record_id"].startswith("issue-221:evidence:32100123456:"), "default record id is not run/digest bound")
        on_disk = authority_path.read_text() + provenance_path.read_text()
        require(SECRET_MARKER not in on_disk, "trusted provenance leaked unrelated secret/config values")


def validate_fail_closed_authority_drift():
    expect_error(
        lambda: subject.live_authority_document(preflight=preflight(), workflow_sha="9" * 40),
        "workflow SHA differs",
    )
    expect_error(
        lambda: subject.live_authority_document(preflight=preflight(protected=False), workflow_sha=MAIN),
        "not stably PROTECTED",
    )
    expect_error(
        lambda: subject.live_authority_document(preflight=preflight(lineage=False), workflow_sha=MAIN),
        "not Effect-Lineage-required",
    )
    expect_error(
        lambda: subject.live_authority_document(preflight=preflight(quiesced=False), workflow_sha=MAIN),
        "quiesced writer fence",
    )

    bad = preflight()
    bad.live_authority.policy.installation_commit_sha = "8" * 40
    expect_error(
        lambda: subject.live_authority_document(preflight=bad, workflow_sha=MAIN),
        "policy authority installation differs",
    )
    bad = preflight()
    bad.live_authority.policy.materialization_commit_sha = "7" * 40
    expect_error(
        lambda: subject.live_authority_document(preflight=bad, workflow_sha=MAIN),
        "policy authority materialization anchor differs",
    )


def validate_fail_closed_artifact_and_run_identity():
    fixture = preflight()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        good = root / "good.json"
        good.write_text(json.dumps(evidence_document()), encoding="utf-8")
        for invalid in (0, "0", "01", "not-a-run"):
            expect_error(
                lambda invalid=invalid: subject.live_provenance_document(
                    preflight=fixture,
                    evidence_path=good,
                    github_workflow_run_id=invalid,
                    workflow_sha=MAIN,
                ),
                "workflow run id",
            )
        empty = root / "empty.json"
        empty.write_bytes(b"")
        expect_error(
            lambda: subject.live_provenance_document(
                preflight=fixture,
                evidence_path=empty,
                github_workflow_run_id=1,
                workflow_sha=MAIN,
            ),
            "artifact is empty",
        )
        invalid_json = root / "bad.json"
        invalid_json.write_text("not-json", encoding="utf-8")
        expect_error(
            lambda: subject.live_provenance_document(
                preflight=fixture,
                evidence_path=invalid_json,
                github_workflow_run_id=1,
                workflow_sha=MAIN,
            ),
            "valid UTF-8 JSON",
        )
        missing_schema = root / "missing-schema.json"
        missing_schema.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        expect_error(
            lambda: subject.live_provenance_document(
                preflight=fixture,
                evidence_path=missing_schema,
                github_workflow_run_id=1,
                workflow_sha=MAIN,
            ),
            "lacks schema identity",
        )


def main():
    validate_exact_authority_and_artifact_digest()
    validate_fail_closed_authority_drift()
    validate_fail_closed_artifact_and_run_identity()
    print("PASS: live evidence provenance is exact-byte/run/main/policy/protection bound and secret-minimal")


if __name__ == "__main__":
    main()
