#!/usr/bin/env python3
"""Validate durable v0.3 dogfood evidence without manufacturing release PASS."""
from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from operator_store_model import VALID_STATUSES
from v03_dogfood_trusted_provenance import (
    DogfoodAttestation,
    DogfoodProvenanceVerificationError,
    VerifiedWorkflowRun,
    canonical_record_digest,
)
from validate_v03_dogfood_preflight import main as validate_v03_dogfood_preflight

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spec" / "operator" / "dogfood-evidence.schema.json"
INDEX = ROOT / "release" / "v0.3-dogfood-index.yaml"
DRAFT = ROOT / "release" / "v0.3.0-draft.yaml"
FIXTURES = ROOT / "examples" / "operator" / "dogfood"

SCENARIOS = {"happy_path", "review_remediation", "session_recovery"}

# Closed release profiles. Milestone names, their resulting durable Operation
# state and the minimum evidence categories are normative for this contract.
# start_state is the first durable Operation projection at the scenario boundary:
# happy_path's operation.started establishes RUNNING, while review_remediation
# begins from an already-running Operation. Pre-Operation pseudo-states such as
# READY are never represented; every durable state must come from the Store model.
SCENARIO_PROFILES = {
    "happy_path": {
        "start_state": "RUNNING",
        "end_state": "DONE",
        "milestones": [
            ("operation-started", "RUNNING", {"operation", "persisted_state"}),
            ("developer-completed", "WAITING_EXTERNAL", {"candidate", "runtime_receipt", "persisted_state"}),
            ("independent-review-passed", "WAITING_EXTERNAL", {"candidate", "independent_review", "persisted_state"}),
            ("qa-passed-and-done", "DONE", {"verification", "notification", "persisted_state"}),
        ],
    },
    "review_remediation": {
        "start_state": "RUNNING",
        "end_state": "DONE",
        "milestones": [
            ("developer-completed", "WAITING_EXTERNAL", {"candidate", "runtime_receipt", "persisted_state"}),
            ("reviewer-requested-changes", "RUNNING", {"independent_review", "decision", "persisted_state"}),
            ("remediation-completed", "WAITING_EXTERNAL", {"remediation", "candidate", "runtime_receipt", "persisted_state"}),
            ("independent-re-review-passed", "WAITING_EXTERNAL", {"candidate", "independent_review", "persisted_state"}),
            ("qa-passed", "DONE", {"verification", "notification", "persisted_state"}),
        ],
    },
    "session_recovery": {
        "start_state": "WAITING_EXTERNAL",
        "end_state": "NEEDS_USER",
        "milestones": [
            ("durable-state-created", "WAITING_EXTERNAL", {"operation", "persisted_state"}),
            ("original-session-ended", "WAITING_EXTERNAL", {"persisted_state"}),
            (
                "new-session-discovered-operation-and-user-items",
                "NEEDS_USER",
                {"operation", "decision", "notification", "persisted_state", "session_recovery"},
            ),
        ],
    },
}


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_json(path: Path):
    import json
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def schema_errors(record):
    validator = Draft202012Validator(load_json(SCHEMA), format_checker=FormatChecker())
    return sorted(validator.iter_errors(record), key=lambda error: list(error.absolute_path))


def all_evidence_uris(record):
    uris = list(record.get("evidence_uris") or [])
    for milestone in record.get("milestones") or []:
        if isinstance(milestone, dict):
            uris.extend(milestone.get("evidence_uris") or [])
    return [str(uri) for uri in uris]


def has_github_run_uri(uris, workflow_run_id):
    marker = f"/actions/runs/{workflow_run_id}"
    return any(uri.startswith("https://github.com/") and marker in uri for uri in uris)


def has_exact_github_uri(uris, expected):
    return any(uri == expected or uri.startswith(expected + "/") for uri in uris)


def _milestone_profile_errors(record):
    scenario = record.get("scenario")
    profile = SCENARIO_PROFILES.get(scenario)
    if profile is None:
        return ["unknown dogfood scenario"]

    errors = []
    start_state = record.get("start_state")
    end_state = record.get("end_state")
    if start_state not in VALID_STATUSES:
        errors.append(
            f"start_state must be an authoritative Operation state, got {start_state!r}"
        )
    if end_state not in VALID_STATUSES:
        errors.append(
            f"end_state must be an authoritative Operation state, got {end_state!r}"
        )
    if start_state != profile["start_state"]:
        errors.append(
            f"{scenario} start_state must be {profile['start_state']}"
        )
    if end_state != profile["end_state"]:
        errors.append(
            f"{scenario} end_state must be {profile['end_state']}"
        )

    milestones = record.get("milestones") or []
    expected = profile["milestones"]
    sequences = [item.get("sequence") for item in milestones if isinstance(item, dict)]
    if sequences != list(range(1, len(milestones) + 1)):
        errors.append("milestone sequence must be contiguous from 1")

    for index, milestone in enumerate(milestones, start=1):
        if isinstance(milestone, dict) and milestone.get("state_after") not in VALID_STATUSES:
            errors.append(
                f"milestone {index} state_after must be an authoritative Operation state, got {milestone.get('state_after')!r}"
            )

    names = [item.get("name") if isinstance(item, dict) else None for item in milestones]
    expected_names = [name for name, _, _ in expected]
    if names != expected_names:
        errors.append(
            f"{scenario} milestone names/order must be exactly {expected_names}"
        )
        return errors

    for index, (milestone, (_, state_after, required_categories)) in enumerate(zip(milestones, expected), start=1):
        if milestone.get("state_after") != state_after:
            errors.append(
                f"{scenario} milestone {index} state_after must be {state_after}"
            )
        categories = set(milestone.get("evidence_categories") or [])
        if categories != required_categories:
            errors.append(
                f"{scenario} milestone {index} evidence_categories must be exactly {sorted(required_categories)}"
            )

    if milestones and milestones[-1].get("state_after") != record.get("end_state"):
        errors.append("final milestone state_after must equal end_state")
    return errors


def _trusted_provenance_errors(record, verifier, *, allow_test_verifier=False):
    """Bind a release PASS to independent externally-resolved evidence."""
    errors = []
    provenance = record.get("provenance") or {}
    if provenance.get("verification_status") != "VERIFIED":
        errors.append("real PASS requires provenance.verification_status = VERIFIED")
    verifier_identity = provenance.get("verifier_identity")
    attestation_uri = provenance.get("attestation_uri")
    if not verifier_identity:
        errors.append("real PASS requires a durable provenance verifier identity")
    if not attestation_uri:
        errors.append("real PASS requires a durable provenance attestation URI")
    elif attestation_uri not in all_evidence_uris(record):
        errors.append("real PASS attestation URI must be included in evidence_uris")

    if verifier is None:
        errors.append("real PASS requires a trusted provenance verifier; YAML cannot self-attest")
        return errors
    if getattr(verifier, "test_only", False) and not allow_test_verifier:
        errors.append("real PASS cannot use a test-only provenance verifier")
        return errors

    try:
        attestation = verifier.verify(record)
    except DogfoodProvenanceVerificationError as exc:
        errors.append(f"trusted provenance verification failed: {exc}")
        return errors
    except Exception as exc:
        errors.append(f"trusted provenance verifier errored: {type(exc).__name__}: {exc}")
        return errors
    if not isinstance(attestation, DogfoodAttestation):
        errors.append("trusted provenance verifier returned unsupported attestation")
        return errors

    candidate = record.get("candidate") or {}
    adapter = record.get("adapter") or {}
    runtime = record.get("runtime") or {}
    expected_digest = canonical_record_digest(record)
    exact_claims = [
        (attestation.record_digest, expected_digest, "record digest"),
        (attestation.verifier_identity, verifier_identity, "verifier identity"),
        (attestation.repository, record.get("repository"), "repository"),
        (attestation.candidate_pr_number, candidate.get("pr_number"), "candidate PR"),
        (attestation.candidate_head_sha, candidate.get("head_sha"), "candidate head"),
        (attestation.adapter_id, adapter.get("adapter_id"), "adapter identity"),
        (attestation.runtime_kind, runtime.get("runtime_kind"), "runtime identity"),
        (attestation.receipt_identity, runtime.get("receipt_identity"), "runtime receipt identity"),
    ]
    for actual, expected, label in exact_claims:
        if actual != expected:
            errors.append(f"trusted provenance {label} mismatch")

    declared_run_ids = tuple(sorted(runtime.get("workflow_run_ids") or []))
    attested_runs = tuple(sorted(attestation.workflow_runs, key=lambda item: item.run_id))
    if tuple(run.run_id for run in attested_runs) != declared_run_ids:
        errors.append("trusted provenance workflow run set mismatch")
    else:
        for run in attested_runs:
            if run.repository != record.get("repository"):
                errors.append(f"trusted workflow run {run.run_id} repository mismatch")
            if str(run.conclusion).lower() != "success":
                errors.append(f"trusted workflow run {run.run_id} did not conclude success")
            head_sha = candidate.get("head_sha")
            if head_sha is not None and run.head_sha != head_sha:
                errors.append(f"trusted workflow run {run.run_id} candidate head mismatch")

    declared_categories = {
        milestone["name"]: frozenset(milestone.get("evidence_categories") or [])
        for milestone in record.get("milestones") or []
        if isinstance(milestone, dict) and isinstance(milestone.get("name"), str)
    }
    attested_categories = dict(attestation.milestone_evidence_categories)
    if set(attested_categories) != set(declared_categories):
        errors.append("trusted provenance milestone evidence set mismatch")
    else:
        for name, categories in declared_categories.items():
            if frozenset(attested_categories.get(name) or []) != categories:
                errors.append(f"trusted provenance milestone evidence mismatch: {name}")

    return errors


def semantic_errors(record, provenance_verifier=None, *, allow_test_verifier=False):
    errors = []
    scenario = record.get("scenario")
    kind = record.get("evidence_kind")
    verdict = record.get("verdict")
    release_eligible = record.get("release_eligible")
    repository = record.get("repository") or ""
    adapter = record.get("adapter") or {}
    runtime = record.get("runtime") or {}
    provenance = record.get("provenance") or {}
    assertions = record.get("assertions") or {}
    counts = record.get("counts") or {}
    candidate = record.get("candidate") or {}
    evidence_uris = all_evidence_uris(record)

    errors.extend(_milestone_profile_errors(record))

    pr_number = candidate.get("pr_number")
    head_sha = candidate.get("head_sha")
    if (pr_number is None) != (head_sha is None):
        errors.append("candidate PR number and head SHA must be present or absent together")

    if kind == "deterministic-fixture":
        if release_eligible is not False:
            errors.append("deterministic fixture cannot be release eligible")
        if provenance.get("verification_status") != "NOT_APPLICABLE":
            errors.append("deterministic fixture provenance must be NOT_APPLICABLE")
        if provenance.get("verifier_identity") is not None or provenance.get("attestation_uri") is not None:
            errors.append("deterministic fixture cannot claim trusted provenance")
    elif kind == "release-run":
        if any(uri.startswith("fixture://") for uri in evidence_uris):
            errors.append("release-run evidence cannot use fixture URIs")
        if any("example.invalid" in uri or "localhost" in uri for uri in evidence_uris):
            errors.append("release-run evidence cannot use placeholder or local URIs")
        if str(record.get("run_id") or "").startswith("fixture-"):
            errors.append("release-run run_id cannot use fixture identity")
        if str(adapter.get("adapter_id") or "").startswith("fixture."):
            errors.append("release-run adapter cannot use fixture identity")
        if str(runtime.get("runtime_kind") or "").lower() == "fixture":
            errors.append("release-run runtime cannot be a fixture")
        if str(runtime.get("receipt_identity") or "").startswith("fixture-"):
            errors.append("release-run receipt cannot use fixture identity")
        if verdict != "PASS" and release_eligible is True:
            errors.append("non-PASS release-run cannot be release eligible")
        if verdict == "PASS":
            if release_eligible is not True:
                errors.append("real PASS must be explicitly release eligible")
            if adapter.get("supported") is not True:
                errors.append("real PASS requires a supported adapter")
            if runtime.get("real_supported_runtime") is not True:
                errors.append("real PASS requires a real supported runtime")
            workflow_run_ids = runtime.get("workflow_run_ids") or []
            if not workflow_run_ids:
                errors.append("real PASS requires at least one workflow run id")
            for workflow_run_id in workflow_run_ids:
                if not has_github_run_uri(evidence_uris, workflow_run_id):
                    errors.append(
                        f"real PASS workflow run id {workflow_run_id} must be bound to a GitHub Actions run evidence URI"
                    )
            if not runtime.get("receipt_identity"):
                errors.append("real PASS requires a runtime receipt identity")
            if assertions.get("durable_operation_state") is not True:
                errors.append("real PASS requires durable Operation state")
            if assertions.get("no_repeated_continue_messages") is not True:
                errors.append("real PASS requires no repeated continue messages")
            if counts.get("repeated_continue_messages") != 0:
                errors.append("real PASS requires repeated_continue_messages = 0")
            if pr_number is not None and head_sha is not None:
                expected_pr = f"https://github.com/{repository}/pull/{pr_number}"
                expected_head = f"https://github.com/{repository}/commit/{head_sha}"
                if not has_exact_github_uri(evidence_uris, expected_pr):
                    errors.append("real PASS candidate PR must be bound to an exact GitHub PR evidence URI")
                if not has_exact_github_uri(evidence_uris, expected_head):
                    errors.append("real PASS candidate head SHA must be bound to an exact GitHub commit evidence URI")
            errors.extend(
                _trusted_provenance_errors(
                    record,
                    provenance_verifier,
                    allow_test_verifier=allow_test_verifier,
                )
            )
        elif provenance.get("verification_status") == "VERIFIED":
            errors.append("non-PASS release-run cannot claim VERIFIED provenance")
    else:
        errors.append("unknown evidence kind")

    if scenario == "happy_path":
        if adapter.get("write_capable") is not True:
            errors.append("happy path requires a write-capable adapter")
        if assertions.get("independent_review_observed") is not True:
            errors.append("happy path requires independent review")
        if assertions.get("remediation_round_trip_observed") is not False:
            errors.append("happy path must not claim remediation round trip")
    elif scenario == "review_remediation":
        if adapter.get("write_capable") is not True:
            errors.append("review remediation requires a write-capable adapter")
        if pr_number is None or head_sha is None:
            errors.append("review remediation requires exact candidate PR/head binding")
        if assertions.get("independent_review_observed") is not True:
            errors.append("review remediation requires independent review")
        if assertions.get("remediation_round_trip_observed") is not True:
            errors.append("review remediation requires remediation and re-review")
    elif scenario == "session_recovery":
        if assertions.get("new_session_discovery_observed") is not True:
            errors.append("session recovery requires new-session discovery")
    else:
        errors.append("unknown dogfood scenario")

    return errors


def validate_record(record, label, provenance_verifier=None, *, allow_test_verifier=False):
    errors = [f"schema: {error.message}" for error in schema_errors(record)]
    if not errors:
        errors.extend(
            semantic_errors(
                record,
                provenance_verifier,
                allow_test_verifier=allow_test_verifier,
            )
        )
    if errors:
        raise AssertionError(f"{label}: " + "; ".join(errors))


def require_rejected(record, label, expected_fragment, provenance_verifier=None, *, allow_test_verifier=False):
    try:
        validate_record(
            record,
            label,
            provenance_verifier,
            allow_test_verifier=allow_test_verifier,
        )
        raise AssertionError(f"{label}: synthetic record unexpectedly passed release validation")
    except AssertionError as exc:
        if "synthetic record unexpectedly" in str(exc):
            raise
        if expected_fragment not in str(exc):
            raise AssertionError(f"{label}: wrong rejection: {exc}") from exc


class _FixtureTrustedVerifier:
    """Test-only verifier used solely to exercise binding checks."""

    test_only = True

    def __init__(self, attestation: DogfoodAttestation | None = None, error: str | None = None):
        self.attestation = attestation
        self.error = error

    def verify(self, record):
        if self.error:
            raise DogfoodProvenanceVerificationError(self.error)
        if self.attestation is None:
            raise DogfoodProvenanceVerificationError("no fixture attestation")
        return self.attestation


def _promoted_release_record():
    promoted = copy.deepcopy(load_yaml(FIXTURES / "happy-path-fixture.yaml"))
    promoted["evidence_kind"] = "release-run"
    promoted["release_eligible"] = True
    promoted["run_id"] = "dogfood-synthetic-001"
    promoted["repository"] = "DREAM-XIN/ai-sdlc"
    promoted["adapter"]["adapter_id"] = "ai-sdlc.openai.responses"
    promoted["adapter"]["supported"] = True
    promoted["runtime"]["runtime_kind"] = "github-actions-gh-aw"
    promoted["runtime"]["real_supported_runtime"] = True
    promoted["runtime"]["receipt_identity"] = "gh-aw-receipt-001"
    promoted["runtime"]["workflow_run_ids"] = [123456789]
    promoted["provenance"] = {
        "verification_status": "VERIFIED",
        "verifier_identity": "trusted-release-verifier:v1",
        "attestation_uri": "https://github.com/DREAM-XIN/ai-sdlc/actions/runs/123456789#dogfood-attestation",
    }
    for milestone in promoted["milestones"]:
        milestone["evidence_uris"] = [f"https://github.com/DREAM-XIN/ai-sdlc/issues/239#{milestone['name']}"]
    promoted["evidence_uris"] = [
        f"https://github.com/DREAM-XIN/ai-sdlc/pull/{promoted['candidate']['pr_number']}",
        f"https://github.com/DREAM-XIN/ai-sdlc/commit/{promoted['candidate']['head_sha']}",
        "https://github.com/DREAM-XIN/ai-sdlc/actions/runs/123456789",
        promoted["provenance"]["attestation_uri"],
    ]
    return promoted


def _matching_test_attestation(record):
    candidate = record["candidate"]
    runtime = record["runtime"]
    return DogfoodAttestation(
        verifier_identity=record["provenance"]["verifier_identity"],
        record_digest=canonical_record_digest(record),
        repository=record["repository"],
        candidate_pr_number=candidate["pr_number"],
        candidate_head_sha=candidate["head_sha"],
        adapter_id=record["adapter"]["adapter_id"],
        runtime_kind=runtime["runtime_kind"],
        receipt_identity=runtime["receipt_identity"],
        workflow_runs=tuple(
            VerifiedWorkflowRun(
                run_id=run_id,
                repository=record["repository"],
                conclusion="success",
                head_sha=candidate["head_sha"],
            )
            for run_id in runtime["workflow_run_ids"]
        ),
        milestone_evidence_categories={
            milestone["name"]: frozenset(milestone["evidence_categories"])
            for milestone in record["milestones"]
        },
    )


def validate_fixtures():
    expected = {
        "happy-path-fixture.yaml": "happy_path",
        "review-remediation-fixture.yaml": "review_remediation",
        "session-recovery-fixture.yaml": "session_recovery",
    }
    for name, scenario in expected.items():
        record = load_yaml(FIXTURES / name)
        validate_record(record, name)
        if record["scenario"] != scenario:
            raise AssertionError(f"{name}: wrong scenario")
        if record["release_eligible"] is not False:
            raise AssertionError(f"{name}: fixture unexpectedly release eligible")

    # Plausible-looking GitHub URLs and self-declared VERIFIED provenance are
    # insufficient without a trusted verifier.
    promoted = _promoted_release_record()
    require_rejected(
        promoted,
        "synthetic-release-pass-with-plausible-urls",
        "requires a trusted provenance verifier",
    )

    nonexistent = _FixtureTrustedVerifier(error="workflow run 123456789 does not exist")
    require_rejected(
        promoted,
        "synthetic-nonexistent-workflow-run",
        "workflow run 123456789 does not exist",
        nonexistent,
        allow_test_verifier=True,
    )

    matching = _matching_test_attestation(promoted)
    validate_record(
        promoted,
        "trusted-boundary-positive-fixture",
        _FixtureTrustedVerifier(matching),
        allow_test_verifier=True,
    )

    wrong_head_run = replace(
        matching.workflow_runs[0],
        head_sha="c" * 40,
    )
    require_rejected(
        promoted,
        "trusted-mismatched-run-head",
        "candidate head mismatch",
        _FixtureTrustedVerifier(replace(matching, workflow_runs=(wrong_head_run,))),
        allow_test_verifier=True,
    )

    require_rejected(
        promoted,
        "trusted-mismatched-receipt",
        "runtime receipt identity mismatch",
        _FixtureTrustedVerifier(replace(matching, receipt_identity="other-receipt")),
        allow_test_verifier=True,
    )

    wrong_digest = replace(matching, record_digest="sha256:" + "0" * 64)
    require_rejected(
        promoted,
        "trusted-stale-record-attestation",
        "record digest mismatch",
        _FixtureTrustedVerifier(wrong_digest),
        allow_test_verifier=True,
    )

    # Closed scenario profile adversarial coverage.
    reordered = copy.deepcopy(load_yaml(FIXTURES / "happy-path-fixture.yaml"))
    reordered["milestones"][1], reordered["milestones"][2] = reordered["milestones"][2], reordered["milestones"][1]
    reordered["milestones"][1]["sequence"] = 2
    reordered["milestones"][2]["sequence"] = 3
    require_rejected(reordered, "reordered-milestones", "milestone names/order must be exactly")

    renamed = copy.deepcopy(load_yaml(FIXTURES / "happy-path-fixture.yaml"))
    renamed["milestones"][2]["name"] = "review-ish"
    require_rejected(renamed, "renamed-milestone", "milestone names/order must be exactly")

    omitted = copy.deepcopy(load_yaml(FIXTURES / "review-remediation-fixture.yaml"))
    omitted["milestones"].pop(1)
    for sequence, milestone in enumerate(omitted["milestones"], start=1):
        milestone["sequence"] = sequence
    require_rejected(omitted, "omitted-milestone", "milestone names/order must be exactly")

    impossible_start = copy.deepcopy(load_yaml(FIXTURES / "happy-path-fixture.yaml"))
    impossible_start["start_state"] = "READY"
    require_rejected(
        impossible_start,
        "non-authoritative-start-state",
        "start_state must be an authoritative Operation state",
    )

    impossible_milestone_state = copy.deepcopy(load_yaml(FIXTURES / "happy-path-fixture.yaml"))
    impossible_milestone_state["milestones"][0]["state_after"] = "READY"
    require_rejected(
        impossible_milestone_state,
        "non-authoritative-milestone-state",
        "state_after must be an authoritative Operation state",
    )

    impossible_end = copy.deepcopy(load_yaml(FIXTURES / "session-recovery-fixture.yaml"))
    impossible_end["end_state"] = "DONE"
    require_rejected(impossible_end, "impossible-end", "end_state must be NEEDS_USER")

    missing_decision = copy.deepcopy(load_yaml(FIXTURES / "review-remediation-fixture.yaml"))
    missing_decision["milestones"][1]["evidence_categories"].remove("decision")
    require_rejected(missing_decision, "missing-decision-evidence", "evidence_categories must be exactly")

    missing_notification = copy.deepcopy(load_yaml(FIXTURES / "session-recovery-fixture.yaml"))
    missing_notification["milestones"][2]["evidence_categories"].remove("notification")
    require_rejected(missing_notification, "missing-notification-evidence", "evidence_categories must be exactly")

    missing_persisted = copy.deepcopy(load_yaml(FIXTURES / "happy-path-fixture.yaml"))
    missing_persisted["milestones"][0]["evidence_categories"].remove("persisted_state")
    require_rejected(missing_persisted, "missing-persisted-state-evidence", "evidence_categories must be exactly")


def validate_index(provenance_verifier=None):
    index = load_yaml(INDEX)
    draft = load_yaml(DRAFT)
    if index.get("release_version") != "0.3.0" or index.get("tracking_issue") != "#239":
        raise AssertionError("dogfood index identity mismatch")
    if index.get("release_ready") is not False:
        raise AssertionError("dogfood evidence work must not mark release ready")
    scenarios = index.get("scenarios") or {}
    if set(scenarios) != SCENARIOS:
        raise AssertionError("dogfood index scenario ids drifted")

    draft_dogfood = (draft or {}).get("required_dogfood") or {}
    for scenario, entry in scenarios.items():
        if not isinstance(entry, dict):
            raise AssertionError(f"{scenario}: index entry must be a mapping")
        if entry.get("requirement") != (draft_dogfood.get(scenario) or {}).get("requirement"):
            raise AssertionError(f"{scenario}: requirement drift from frozen release draft")
        status = entry.get("status")
        evidence = entry.get("evidence") or []
        if status not in {"pending", "passed", "blocked"}:
            raise AssertionError(f"{scenario}: invalid index status {status!r}")
        if status == "pending" and evidence:
            raise AssertionError(f"{scenario}: pending scenario cannot cite release-completion evidence")
        if status == "passed":
            if not evidence:
                raise AssertionError(f"{scenario}: passed scenario requires release evidence")
            for relative in evidence:
                path = ROOT / relative
                if not path.is_file():
                    raise AssertionError(f"{scenario}: missing release evidence {relative}")
                record = load_yaml(path)
                # Deliberately fail closed when authoritative validation has no
                # independent trusted verifier configured for a claimed PASS.
                validate_record(record, relative, provenance_verifier)
                if record.get("scenario") != scenario:
                    raise AssertionError(f"{relative}: scenario binding mismatch")
                if record.get("evidence_kind") != "release-run" or record.get("verdict") != "PASS" or record.get("release_eligible") is not True:
                    raise AssertionError(f"{relative}: cannot close dogfood scenario")

    excluded = index.get("excluded_release_evidence") or {}
    fault = excluded.get("fault_injection") or {}
    if fault.get("owner_issue") != "#221":
        raise AssertionError("fault injection must remain owned by Issue #221")


def main():
    if not SCHEMA.is_file() or not INDEX.is_file():
        raise AssertionError("dogfood schema/index missing")
    validate_fixtures()
    validate_index()
    validate_v03_dogfood_preflight()
    print("v0.3 dogfood evidence validation passed")
    print("- deterministic fixtures: closed scenario profiles, never release eligible")
    print("- release PASS: defaults fail-closed without independent trusted provenance verifier")
    print("- provenance binding: canonical record/repository/candidate/run/adapter/runtime/receipt/categories")
    print("- milestone semantics: exact names/order/resulting authoritative Operation states/evidence categories per scenario")
    print("- happy_path/review_remediation/session_recovery: pending until real release-run records exist")
    print("- fault injection: excluded and remains owned by Issue #221")


if __name__ == "__main__":
    main()
