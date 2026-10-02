#!/usr/bin/env python3
"""Deterministic checks for the closed real-dogfood release finalizer."""
from __future__ import annotations

from copy import deepcopy

from v03_dogfood_post_run_finalizer import (
    V03DogfoodPostRunFinalizerError,
    _reconstruct_release_authority,
)
from v03_dogfood_release_finalizer import V03DogfoodReleaseFinalizerError, build_release_record
from v03_dogfood_trusted_provenance import DogfoodAttestation, VerifiedWorkflowRun, canonical_record_digest
from validate_v03_dogfood_evidence import SCENARIO_PROFILES

REPO = "DREAM-XIN/ai-sdlc"
HEAD = "1" * 40
PR = 348
ADAPTER = "openai.responses"
RUNTIME = "github-actions-gh-aw"
VERIFIER = "ai-sdlc/v03-dogfood-production-provenance/v1"
ATTEST = f"https://github.com/{REPO}/actions/runs/9001#attestation"


class ExactVerifier:
    test_only = False

    def verify(self, record):
        runtime = record["runtime"]
        categories = {
            row["name"]: frozenset(row["evidence_categories"])
            for row in record["milestones"]
        }
        return DogfoodAttestation(
            verifier_identity=VERIFIER,
            record_digest=canonical_record_digest(record),
            repository=REPO,
            candidate_pr_number=PR,
            candidate_head_sha=HEAD,
            adapter_id=ADAPTER,
            runtime_kind=RUNTIME,
            receipt_identity=runtime["receipt_identity"],
            workflow_runs=tuple(
                VerifiedWorkflowRun(run_id, REPO, "success", HEAD)
                for run_id in sorted(runtime["workflow_run_ids"])
            ),
            milestone_evidence_categories=categories,
        )


def observation(scenario: str):
    profile = SCENARIO_PROFILES[scenario]
    roles = {
        "happy_path": ["developer", "reviewer", "qa"],
        "review_remediation": ["developer", "reviewer", "developer", "reviewer", "qa"],
        "session_recovery": ["developer"],
    }[scenario]
    run_ids = list(range(9001, 9001 + len(roles)))
    return {
        "scenario": scenario,
        "operation_id": f"op-{scenario}",
        "start_status": profile["start_state"],
        "final_status": profile["end_state"],
        "dispatch_roles": roles,
        "workflow_run_ids": run_ids,
        "runtime_receipt_identity": str(run_ids[-1]),
        "response_ids": [f"resp-{scenario}"],
        "function_call_ids": [f"call-{scenario}"],
        "recovery_response_ids": ["resp-recovery"] if scenario == "session_recovery" else [],
        "recovery_function_call_ids": ["call-recovery"] if scenario == "session_recovery" else [],
        "new_session_discovery_observed": scenario == "session_recovery",
        "repeated_continue_messages": 0,
        "repository": REPO,
        "feature_id": f"F-DOGFOOD-{scenario}",
        "target_ref": "refs/heads/v03-dogfood-target",
        "candidate_pr_number": PR,
        "candidate_head_sha": HEAD,
        "release_eligible": False,
        "provenance_verified": False,
    }


def trusted_assertions(scenario: str):
    return {
        "durable_operation_state": True,
        "independent_review_observed": scenario in {"happy_path", "review_remediation"},
        "remediation_round_trip_observed": scenario == "review_remediation",
        "new_session_discovery_observed": scenario == "session_recovery",
    }


def facts(scenario: str, run_ids):
    milestones = []
    for name, _state, categories in SCENARIO_PROFILES[scenario]["milestones"]:
        milestones.append({
            "name": name,
            "evidence_categories": sorted(categories),
            "evidence_uris": [f"https://github.com/{REPO}/issues/239#{scenario}-{name}"],
        })
    evidence = [
        f"https://github.com/{REPO}/pull/{PR}",
        f"https://github.com/{REPO}/commit/{HEAD}",
        ATTEST,
    ] + [f"https://github.com/{REPO}/actions/runs/{run_id}" for run_id in run_ids]
    return {
        "release_run_id": f"release-{scenario}-9001",
        "recorded_at": "2026-08-26T00:00:00Z",
        "operation_generation": 7,
        "human_interventions": 0,
        "milestones": milestones,
        "assertions": trusted_assertions(scenario),
        "evidence_uris": evidence,
        "provenance_verifier": ExactVerifier(),
    }


def finalize(scenario: str):
    obs = observation(scenario)
    return build_release_record(
        observation=obs,
        trusted_facts=facts(scenario, obs["workflow_run_ids"]),
        verifier_identity=VERIFIER,
        attestation_uri=ATTEST,
        adapter_id=ADAPTER,
        runtime_kind=RUNTIME,
    )


def require_rejected(label, fn):
    try:
        fn()
    except (V03DogfoodReleaseFinalizerError, V03DogfoodPostRunFinalizerError, AssertionError):
        return
    raise AssertionError(f"{label} unexpectedly finalized release evidence")


def event(sequence, event_type, payload=None):
    return {"sequence": sequence, "event_type": event_type, "payload": dict(payload or {})}


def durable_history(scenario: str):
    steps = {
        "happy_path": ["IMPLEMENTATION_WORK", "CODE_REVIEW", "VERIFICATION_QA"],
        "review_remediation": ["IMPLEMENTATION_WORK", "CODE_REVIEW", "CODE_REMEDIATION", "CODE_REREVIEW", "VERIFICATION_QA"],
        "session_recovery": ["IMPLEMENTATION_WORK"],
    }[scenario]
    rows = [event(1, "operation.started")]
    seq = 1
    for index, step in enumerate(steps):
        seq += 1; rows.append(event(seq, "loop.step.selected", {"step": step}))
        seq += 1; rows.append(event(seq, "dispatch.claimed"))
        seq += 1; rows.append(event(seq, "dispatch.launch.lookup-recorded", {"lookup_state": "LAUNCHED", "receipt_id": str(9001 + index)}))
        if scenario != "session_recovery":
            seq += 1; rows.append(event(seq, "worker.result.validated"))
        if index < len(steps) - 1:
            seq += 1; rows.append(event(seq, "loop.stable-stop", {"status": "WAITING_EXTERNAL"}))
    if scenario == "session_recovery":
        seq += 1; rows.append(event(seq, "loop.stable-stop", {"status": "WAITING_EXTERNAL"}))
        seq += 1; rows.append(event(seq, "decision.requested", {"decision_id": "decision-1"}))
        seq += 1; rows.append(event(seq, "notification.created", {"notification_id": "notification-1"}))
        seq += 1; rows.append(event(seq, "loop.stable-stop", {"status": "NEEDS_USER"}))
        projection = {"status": "NEEDS_USER", "pending_decisions": ["decision-1"], "unread_notifications": ["notification-1"]}
    else:
        seq += 1; rows.append(event(seq, "notification.created", {"notification_id": "notification-1"}))
        seq += 1; rows.append(event(seq, "operation.done"))
        projection = {"status": "DONE", "pending_decisions": [], "unread_notifications": ["notification-1"]}
    return rows, projection


def validate_durable_authority_reconstruction():
    for scenario in SCENARIO_PROFILES:
        rows, projection = durable_history(scenario)
        categories, assertions = _reconstruct_release_authority(scenario, rows, projection, observation(scenario))
        assert assertions == trusted_assertions(scenario)
        assert set(categories) == {name for name, _state, _categories in SCENARIO_PROFILES[scenario]["milestones"]}

    rows, projection = durable_history("review_remediation")
    rows = [row for row in rows if not (row["event_type"] == "loop.step.selected" and row["payload"].get("step") == "CODE_REMEDIATION")]
    require_rejected(
        "missing durable remediation step",
        lambda: _reconstruct_release_authority("review_remediation", rows, projection, observation("review_remediation")),
    )

    rows, projection = durable_history("session_recovery")
    projection = dict(projection)
    projection["pending_decisions"] = []
    require_rejected(
        "missing durable pending Decision",
        lambda: _reconstruct_release_authority("session_recovery", rows, projection, observation("session_recovery")),
    )

    rows, projection = durable_history("happy_path")
    rows = [row for row in rows if row["event_type"] != "notification.created"]
    require_rejected(
        "missing durable Notification",
        lambda: _reconstruct_release_authority("happy_path", rows, projection, observation("happy_path")),
    )


def validate_historical_runtime_bindings():

    # Post-run binding reconstruction uses historical protected revisions and
    # the production source without replaying a callback or Feature Persist.
    import v03_dogfood_post_run_finalizer as post
    from types import SimpleNamespace
    from operator_store_model import reservation_path
    semantic = "1"*64
    key = "dispatch-" + "a"*40
    slot = SimpleNamespace(feature_id="F-DOGFOOD", target_ref="dogfood/ref")
    files = {reservation_path(semantic): dict(external_dispatch_key=key, feature_id=slot.feature_id,
                                              role="reviewer", expected_revision=4)}
    calls = []
    def resolve(**kwargs):
        calls.append(kwargs)
        assert kwargs["trusted_context"]["expected_revision"] == 4
        return SimpleNamespace(run=SimpleNamespace(run_id=7001, role="reviewer",
                               candidate_pr_number=401, candidate_head_sha="a"*40))
    preflight = SimpleNamespace(slot=slot, execution=SimpleNamespace(repository=REPO),
        candidate_pr_number=401, workflows=SimpleNamespace(workflow_for=lambda role: "reviewer.yml"),
        composition=SimpleNamespace(runtime=SimpleNamespace(backend=SimpleNamespace(
            read_snapshot=lambda: SimpleNamespace(get=lambda path: files.get(path)))),
            result_source=SimpleNamespace(resolve=resolve)))
    events = [
        dict(event_type="dispatch.launch.authorized", operation_generation=1,
             payload=dict(external_dispatch_key=key, semantic_effect_key=semantic,
                          dispatch_id="dispatch-id", role="reviewer", stage="code-review", candidate_head_sha="a"*40)),
        dict(event_type="dispatch.launch.lookup-recorded", operation_generation=1,
             payload=dict(external_dispatch_key=key, lookup_state="LAUNCHED", receipt_id="7001")),
    ]
    old_projection = post.vertical_projection
    try:
        post.vertical_projection = lambda snapshot, op: dict(operation_profile="vertical-implementation-review-qa/v1")
        bound = post._durable_run_bindings(preflight, dict(operation_id="op-1"), events)
        assert bound[7001]["candidate_input_head_sha"] == "a"*40 and len(calls) == 1
        require_rejected("duplicate protected authorization",
                         lambda: post._durable_run_bindings(preflight, dict(operation_id="op-1"), events + [events[0]]))
        files[reservation_path(semantic)]["feature_id"] = "F-OTHER"
        require_rejected("wrong reservation Feature",
                         lambda: post._durable_run_bindings(preflight, dict(operation_id="op-1"), events))
    finally:
        post.vertical_projection = old_projection


def main() -> int:
    validate_durable_authority_reconstruction()
    validate_historical_runtime_bindings()
    for scenario in SCENARIO_PROFILES:
        record = finalize(scenario)
        assert record["evidence_kind"] == "release-run"
        assert record["verdict"] == "PASS"
        assert record["release_eligible"] is True
        assert record["provenance"]["verification_status"] == "VERIFIED"
        assert record["assertions"]["independent_review_observed"] == trusted_assertions(scenario)["independent_review_observed"]

    raw_overclaim = observation("happy_path")
    raw_overclaim["release_eligible"] = True
    require_rejected(
        "raw observation overclaim",
        lambda: build_release_record(
            observation=raw_overclaim,
            trusted_facts=facts("happy_path", raw_overclaim["workflow_run_ids"]),
            verifier_identity=VERIFIER,
            attestation_uri=ATTEST,
            adapter_id=ADAPTER,
            runtime_kind=RUNTIME,
        ),
    )

    missing_run = observation("review_remediation")
    missing_facts = facts("review_remediation", missing_run["workflow_run_ids"])
    missing_facts["evidence_uris"] = [uri for uri in missing_facts["evidence_uris"] if "/actions/runs/9003" not in uri]
    require_rejected(
        "missing workflow authority URI",
        lambda: build_release_record(
            observation=missing_run,
            trusted_facts=missing_facts,
            verifier_identity=VERIFIER,
            attestation_uri=ATTEST,
            adapter_id=ADAPTER,
            runtime_kind=RUNTIME,
        ),
    )

    bad_assertions = facts("review_remediation", observation("review_remediation")["workflow_run_ids"])
    bad_assertions["assertions"] = trusted_assertions("happy_path")
    require_rejected(
        "wrong durable remediation assertion",
        lambda: build_release_record(
            observation=observation("review_remediation"),
            trusted_facts=bad_assertions,
            verifier_identity=VERIFIER,
            attestation_uri=ATTEST,
            adapter_id=ADAPTER,
            runtime_kind=RUNTIME,
        ),
    )

    drift = observation("session_recovery")
    drift["new_session_discovery_observed"] = False
    require_rejected(
        "session recovery discovery drift",
        lambda: build_release_record(
            observation=drift,
            trusted_facts=facts("session_recovery", drift["workflow_run_ids"]),
            verifier_identity=VERIFIER,
            attestation_uri=ATTEST,
            adapter_id=ADAPTER,
            runtime_kind=RUNTIME,
        ),
    )

    bad_facts = facts("happy_path", observation("happy_path")["workflow_run_ids"])
    bad_facts["provenance_verifier"] = None
    require_rejected(
        "missing trusted verifier",
        lambda: build_release_record(
            observation=observation("happy_path"),
            trusted_facts=bad_facts,
            verifier_identity=VERIFIER,
            attestation_uri=ATTEST,
            adapter_id=ADAPTER,
            runtime_kind=RUNTIME,
        ),
    )

    print("v0.3 closed dogfood release finalizer validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
