#!/usr/bin/env python3
"""Deterministic checks for the closed real-dogfood release finalizer."""
from __future__ import annotations

from copy import deepcopy
from operator_vertical import VerticalInvariantError

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
        "recovery_discovery_decision_ids": ["decision-1"] if scenario == "session_recovery" else [],
        "recovery_discovery_notification_ids": ["notification-1"] if scenario == "session_recovery" else [],
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
    except (V03DogfoodReleaseFinalizerError, V03DogfoodPostRunFinalizerError, VerticalInvariantError, AssertionError):
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
            role = {
                "IMPLEMENTATION_WORK": "developer",
                "CODE_REVIEW": "reviewer",
                "CODE_REMEDIATION": "developer",
                "CODE_REREVIEW": "reviewer",
                "VERIFICATION_QA": "qa",
            }[step]
            if role == "developer":
                worker_payload = {"status": "COMPLETED"}
            elif role == "reviewer":
                worker_payload = {
                    "verdict": "REWORK"
                    if scenario == "review_remediation" and step == "CODE_REVIEW"
                    else "PASS"
                }
            else:
                worker_payload = {"verdict": "PASS"}
            callback_id = f"callback-{scenario}-{index}"
            seq += 1; rows.append(event(seq, "worker.callback.recorded", {
                "callback_id": callback_id,
                "trusted_callback_envelope": {
                    "trusted_context": {"role": role},
                    "worker_payload": worker_payload,
                },
            }))
            seq += 1; rows.append(event(seq, "worker.result.validated", {
                "callback_id": callback_id,
                "role": role,
            }))
            feature_event_id = f"EVT-{scenario}-{index}"
            expected_revision = 1 + index
            persist_payload = {
                "feature_event_id": feature_event_id,
                "expected_revision": expected_revision,
                "target_ref": "refs/heads/v03-dogfood-target",
                "candidate_head_sha": HEAD,
            }
            seq += 1; rows.append(event(seq, "persist.requested", persist_payload))
            seq += 1; rows.append(event(seq, "persist.linearized", persist_payload))
            seq += 1; rows.append(event(seq, "persist.confirmed", {
                **persist_payload, "result_revision": expected_revision + 1,
            }))
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
        seq += 1; rows.append(event(seq, "operation.done", {"feature_revision": 1 + len(steps)}))
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

    rows, projection = durable_history("review_remediation")
    drift = deepcopy(rows)
    first_review = next(
        row for row in drift
        if row["event_type"] == "worker.callback.recorded"
        and ((row.get("payload") or {}).get("trusted_callback_envelope") or {}).get("trusted_context", {}).get("role") == "reviewer"
    )
    first_review["payload"]["trusted_callback_envelope"]["worker_payload"]["verdict"] = "PASS"
    require_rejected(
        "review remediation without durable REWORK verdict",
        lambda: _reconstruct_release_authority("review_remediation", drift, projection, observation("review_remediation")),
    )

    rows, projection = durable_history("happy_path")
    rows = [row for row in rows if row["event_type"] != "persist.confirmed"]
    require_rejected(
        "missing durable Persist confirmation",
        lambda: _reconstruct_release_authority("happy_path", rows, projection, observation("happy_path")),
    )

    rows, projection = durable_history("session_recovery")
    drift = observation("session_recovery")
    drift["recovery_discovery_decision_ids"] = ["decision-other"]
    require_rejected(
        "fresh session wrong Decision identity",
        lambda: _reconstruct_release_authority("session_recovery", rows, projection, drift),
    )

    rows, projection = durable_history("happy_path")
    rows = [row for row in rows if row["event_type"] != "notification.created"]
    require_rejected(
        "missing durable Notification",
        lambda: _reconstruct_release_authority("happy_path", rows, projection, observation("happy_path")),
    )


def consumed_result_fixture(role="reviewer"):
    """A consumed production receipt, then mutable GitHub source truth."""
    from types import SimpleNamespace
    from operator_store_model import digest_json
    from operator_vertical import TrustedDispatchContext
    from operator_vertical_recovery import _context_payload
    from operator_vertical_gh_aw_collector import _build_receipts, MaterializedGhAwOutput
    context = TrustedDispatchContext(
        operation_id="op-1", operation_generation=1,
        operation_profile="vertical-implementation-review-qa/v1",
        semantic_effect_key="1"*64, external_dispatch_key="dispatch-" + "a"*40,
        dispatch_id="dispatch-id", runtime_receipt_identity="7001",
        target_repository=REPO, target_ref="dogfood/ref", feature_id="F-DOGFOOD",
        expected_revision=4, feature_stage="code-review" if role == "reviewer" else "implementation",
        task_id="task-1", role=role, candidate_pr_number=401 if role == "reviewer" else None,
        candidate_head_sha="a"*40, worker_identity="independent-" + role,
        collector_identity="sealed-collector",
    )
    kind = "evidence" if role == "reviewer" else "artifact"
    uri = "docs/features/F-DOGFOOD/worker-runs/dispatch-id/" + role + "-sealed-original.json"
    material = (b'{"verdict":"REWORK"}\n' if role == "reviewer"
                else b'{"developer_pr":402,"head":"original"}\n')
    source = SimpleNamespace(load_content=lambda location: material if location == uri else b"replacement")
    output = MaterializedGhAwOutput(role + "-result", kind, "application/json", uri)
    worker_payload = (dict(verdict="REWORK", summary="Original review", findings=[],
                           outputs=[dict(label=output.label, kind=kind)]) if role == "reviewer"
                      else dict(status="COMPLETED", summary="Original implementation",
                                outputs=[dict(label=output.label, kind=kind)]))
    receipts = _build_receipts(
        coordinator=SimpleNamespace(content_loader=source.load_content), context=context,
        outputs=(output,), declared_outputs={output.label: kind},
        collected_at="2026-10-02T00:00:00Z",
    )
    callback_id = "gh-aw-callback-" + digest_json(dict(
        operation_id=context.operation_id, generation=1,
        external_dispatch_key=context.external_dispatch_key,
        runtime_receipt_identity="7001", run_id=7001,
    ))[:24]
    envelope = dict(trusted_context=_context_payload(context), worker_payload=worker_payload,
                    collected_outputs=receipts)
    rows = [
        dict(sequence=3, event_type="worker.callback.recorded", operation_generation=1,
             payload=dict(callback_id=callback_id, external_dispatch_key=context.external_dispatch_key,
                          callback_digest=digest_json(dict(worker_payload=worker_payload, receipts=receipts)),
                          trusted_callback_envelope=envelope,
                          trusted_callback_envelope_digest=digest_json(envelope))),
        dict(sequence=4, event_type="worker.result.validated", operation_generation=1,
             payload=dict(callback_id=callback_id, role=role, dispatch_id=context.dispatch_id)),
    ]
    trusted = {name: getattr(context, name) for name in (
        "operation_id", "operation_generation", "operation_profile", "semantic_effect_key",
        "external_dispatch_key", "dispatch_id", "target_repository", "target_ref",
        "feature_id", "expected_revision", "feature_stage", "role",
    )}
    trusted["launch_candidate_head_sha"] = context.candidate_head_sha
    resolved = SimpleNamespace(
        run=SimpleNamespace(run_id=7001, role=role, candidate_pr_number=context.candidate_pr_number,
                            candidate_head_sha=context.candidate_head_sha, task_id=context.task_id,
                            worker_identity=context.worker_identity, collector_identity=context.collector_identity),
        role_payload=deepcopy(worker_payload), outputs=(output,),
    )
    return trusted, resolved, source, rows


def validate_original_consumed_result():
    from dataclasses import replace
    from operator_store_model import digest_json
    from v03_dogfood_post_run_finalizer import _verify_consumed_result
    for role in ("reviewer", "developer"):
        trusted, resolved, source, rows = consumed_result_fixture(role)
        def check(events=rows, result=resolved, backing=source):
            return _verify_consumed_result(
                events=events, trusted=trusted, resolved=result,
                result_source=backing, lookup_sequence=2,
            )
        check()
        # Re-resolution may mint a new valid sealed URI after a Gate comment
        # verdict or Developer PR head changes. It cannot replace the consumed one.
        changed = deepcopy(resolved)
        changed.outputs = (replace(changed.outputs[0], trusted_uri=changed.outputs[0].trusted_uri.replace(
            "sealed-original", "sealed-replacement")),)
        require_rejected(role + " replacement sealed URI", lambda: check(result=changed))
        changed = deepcopy(resolved)
        if role == "reviewer":
            changed.role_payload["verdict"] = "PASS"
        else:
            changed.role_payload["candidate_head_sha"] = "b"*40
        require_rejected(role + " replacement Worker result", lambda: check(result=changed))
        from types import SimpleNamespace
        require_rejected(role + " same-location content mutation",
                         lambda: check(backing=SimpleNamespace(load_content=lambda uri: b"replacement bytes")))
        require_rejected(role + " missing original callback", lambda: check(events=rows[1:]))
        require_rejected(role + " duplicate original callback", lambda: check(events=rows + [rows[0]]))
        require_rejected(role + " missing acceptance", lambda: check(events=rows[:1]))
        require_rejected(role + " duplicate acceptance", lambda: check(events=rows + [rows[1]]))
        changed_rows = deepcopy(rows)
        changed_rows[1]["payload"]["dispatch_id"] = "other-dispatch"
        require_rejected(role + " acceptance dispatch drift", lambda: check(events=changed_rows))
        changed_rows = deepcopy(rows)
        changed_rows[1]["sequence"] = 1
        require_rejected(role + " reordered acceptance", lambda: check(events=changed_rows))
        rejected = deepcopy(rows[1])
        rejected["event_type"] = "worker.result.rejected"
        require_rejected(role + " rejected original callback", lambda: check(events=rows + [rejected]))
        changed_rows = deepcopy(rows)
        changed_rows[0]["payload"]["trusted_callback_envelope"]["worker_payload"]["summary"] = "tampered"
        require_rejected(role + " envelope corruption", lambda: check(events=changed_rows))
        changed_rows = deepcopy(rows)
        protected = changed_rows[0]["payload"]
        protected["trusted_callback_envelope"]["trusted_context"]["expected_revision"] = 5
        protected["trusted_callback_envelope_digest"] = digest_json(protected["trusted_callback_envelope"])
        require_rejected(role + " historical context drift", lambda: check(events=changed_rows))
        changed_rows = deepcopy(rows)
        protected = changed_rows[0]["payload"]
        protected["trusted_callback_envelope"]["collected_outputs"][0]["expected_revision"] = 5
        protected["trusted_callback_envelope_digest"] = digest_json(protected["trusted_callback_envelope"])
        protected["callback_digest"] = digest_json(dict(
            worker_payload=protected["trusted_callback_envelope"]["worker_payload"],
            receipts=protected["trusted_callback_envelope"]["collected_outputs"],
        ))
        require_rejected(role + " original receipt binding drift", lambda: check(events=changed_rows))


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
    trusted, resolved, source, callbacks = consumed_result_fixture()
    calls = []
    def resolve(**kwargs):
        calls.append(kwargs)
        assert kwargs["trusted_context"]["expected_revision"] == 4
        return resolved
    preflight = SimpleNamespace(slot=slot, execution=SimpleNamespace(repository=REPO),
        candidate_pr_number=401, workflows=SimpleNamespace(workflow_for=lambda role: "reviewer.yml"),
        composition=SimpleNamespace(runtime=SimpleNamespace(backend=SimpleNamespace(
            read_snapshot=lambda: SimpleNamespace(get=lambda path: files.get(path)))),
            result_source=SimpleNamespace(resolve=resolve, load_content=source.load_content)))
    events = [
        dict(sequence=1, event_type="dispatch.launch.authorized", operation_generation=1,
             payload=dict(external_dispatch_key=key, semantic_effect_key=semantic,
                          dispatch_id="dispatch-id", role="reviewer", stage="code-review", candidate_head_sha="a"*40)),
        dict(sequence=2, event_type="dispatch.launch.lookup-recorded", operation_generation=1,
             payload=dict(external_dispatch_key=key, lookup_state="LAUNCHED", receipt_id="7001")),
    ]
    events += callbacks
    old_projection = post.vertical_projection
    try:
        post.vertical_projection = lambda snapshot, op: dict(operation_profile="vertical-implementation-review-qa/v1")
        require_rejected(
            "Gate without trusted Developer candidate handoff",
            lambda: post._durable_run_bindings(preflight, dict(operation_id="op-1"), events),
        )
        assert len(calls) == 1
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
    validate_original_consumed_result()
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
