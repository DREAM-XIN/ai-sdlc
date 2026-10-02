#!/usr/bin/env python3
"""Deterministic checks for the closed real-dogfood release finalizer."""
from __future__ import annotations

from copy import deepcopy
from operator_vertical import VerticalInvariantError
from operator_store_model import (
    StoreSnapshot,
    decision_path,
    digest_json,
    event_path,
    make_event,
    notification_path,
    rebuild_projection,
)
from v03_dogfood_scenario_runner import SESSION_TRACE_SCHEMA, session_trace_path

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
        state = next(state for milestone_name, state, _categories in SCENARIO_PROFILES[scenario]["milestones"] if milestone_name == name)
        milestones.append({
            "name": name,
            "state_after": state,
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
        "operation_generation": 0,
        "human_interventions": 0,
        "start_state": SCENARIO_PROFILES[scenario]["start_state"],
        "end_state": SCENARIO_PROFILES[scenario]["end_state"],
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


def _event(operation_id, sequence, event_type, payload=None):
    return make_event(
        operation_id=operation_id,
        generation=0,
        sequence=sequence,
        event_id=f"evt-{sequence:03d}-{event_type.replace('.', '-')}",
        event_type=event_type,
        occurred_at=f"2026-10-03T00:00:{sequence % 60:02d}Z",
        payload=dict(payload or {}),
        trusted_context_digest="trusted-test",
    )


def _snapshot_with_rows(snapshot: StoreSnapshot, rows):
    files = {
        path: value
        for path, value in snapshot.files.items()
        if "/events/" not in path
    }
    for row in rows:
        files[event_path(row["operation_id"], row["sequence"], row["event_id"])] = row
    return StoreSnapshot(ref_sha=snapshot.ref_sha, files=files)


def durable_history(scenario: str):
    obs = observation(scenario)
    operation_id = obs["operation_id"]
    feature_id = obs["feature_id"]
    target_ref = obs["target_ref"]
    steps = {
        "happy_path": ["IMPLEMENTATION_WORK", "CODE_REVIEW", "VERIFICATION_QA"],
        "review_remediation": ["IMPLEMENTATION_WORK", "CODE_REVIEW", "CODE_REMEDIATION", "CODE_REREVIEW", "VERIFICATION_QA"],
        "session_recovery": ["IMPLEMENTATION_WORK"],
    }[scenario]
    roles = {
        "IMPLEMENTATION_WORK": "developer",
        "CODE_REVIEW": "reviewer",
        "CODE_REMEDIATION": "developer",
        "CODE_REREVIEW": "reviewer",
        "VERIFICATION_QA": "qa",
    }
    rows = []
    seq = 0
    revision = 1
    def add(event_type, payload=None):
        nonlocal seq
        seq += 1
        row = _event(operation_id, seq, event_type, payload)
        rows.append(row)
        return row
    add("operation.started", {
        "target_repository": REPO.lower(),
        "feature_id": feature_id,
        "expected_revision": 1,
        "operation_profile": "vertical-implementation-review-qa/v1",
    })

    for index, step in enumerate(steps):
        role = roles[step]
        key = "dispatch-" + str(index + 1).zfill(40)
        dispatch_id = f"dispatch-{index + 1}"
        callback_id = f"callback-{index + 1}"
        add("loop.step.selected", {"step": step})
        add("dispatch.claimed", {
            "claim_id": f"claim-{index + 1}",
            "semantic_effect_key": str(index + 1) * 64,
            "external_dispatch_key": key,
        })
        add("dispatch.launch.authorized", {
            "claim_id": f"claim-{index + 1}",
            "dispatch_id": dispatch_id,
            "semantic_effect_key": str(index + 1) * 64,
            "external_dispatch_key": key,
            "feature_id": feature_id,
            "expected_revision": revision,
            "stage": "implementation",
            "role": role,
            "candidate_head_sha": HEAD,
        })
        add("dispatch.launch.lookup-recorded", {
            "external_dispatch_key": key,
            "lookup_state": "LAUNCHED",
            "receipt_id": str(9001 + index),
        })
        if scenario == "session_recovery":
            continue

        if role == "developer":
            worker_payload = {"status": "COMPLETED"}
        elif role == "reviewer":
            verdict = "REWORK" if scenario == "review_remediation" and step == "CODE_REVIEW" else "PASS"
            worker_payload = {"verdict": verdict}
        else:
            worker_payload = {"verdict": "PASS"}
        add("worker.callback.recorded", {
            "callback_id": callback_id,
            "external_dispatch_key": key,
            "callback_digest": "a" * 64,
            "trusted_callback_envelope": {
                "worker_payload": worker_payload,
                "collected_outputs": [],
                "trusted_context": {},
            },
            "trusted_callback_envelope_digest": "b" * 64,
        })
        add("worker.result.validated", {
            "callback_id": callback_id,
            "role": role,
            "dispatch_id": dispatch_id,
        })

        def translate(changes, purpose=None):
            nonlocal revision
            feature_event_id = f"EVT-{scenario}-{seq + 1}"
            feature_event = {
                "version": "0.1.0",
                "id": feature_event_id,
                "feature_id": feature_id,
                "expected_revision": revision,
                "occurred_at": "2026-10-03T00:00:00Z",
                "changes": changes,
            }
            payload = {
                "feature_event_id": feature_event_id,
                "feature_event_digest": digest_json(feature_event),
                "feature_event": feature_event,
                "feature_revision": revision,
                "feature_stage": "implementation",
                "feature_manifest_digest": "m" * 64,
                "candidate_head_sha": HEAD,
                "target_ref": target_ref,
                "callback_id": callback_id,
            }
            if purpose:
                payload["purpose"] = purpose
            add("feature.event.translated", payload)
            persist = {
                "feature_event_id": feature_event_id,
                "expected_revision": revision,
                "target_ref": target_ref,
                "candidate_head_sha": HEAD,
            }
            add("persist.requested", persist)
            add("persist.linearized", persist)
            add("persist.confirmed", {**persist, "result_revision": revision + 1})
            revision += 1

        if step == "IMPLEMENTATION_WORK":
            translate([
                {"kind": "artifact-record", "record": {"id": "impl-1", "type": "implementation", "status": "draft"}},
                {"kind": "stage", "id": "code-review", "status": "READY"},
            ])
        elif step == "CODE_REVIEW" and scenario == "review_remediation":
            translate([
                {"kind": "task-record", "record": {"id": "remediation-1", "kind": "remediation", "status": "READY"}},
            ])
        elif step == "CODE_REMEDIATION":
            translate([
                {"kind": "artifact-record", "record": {"id": "impl-2", "type": "implementation", "status": "draft"}},
                {"kind": "task", "id": "remediation-1", "status": "DONE"},
            ])
            translate([
                {"kind": "artifact", "id": "impl-1", "status": "superseded"},
            ], purpose="remediation_artifact_supersession")
        elif step in {"CODE_REVIEW", "CODE_REREVIEW"}:
            translate([{"kind": "gate", "id": "code-gate", "status": "PASS"}])
        elif step == "VERIFICATION_QA":
            translate([{"kind": "gate", "id": "verification-gate", "status": "PASS"}])

    files = {}
    if scenario == "session_recovery":
        decision_id = "decision-1"
        notification_id = "notification-1"
        add("decision.requested", {"decision_id": decision_id, "decision_type": "NEEDS_AUTHORIZATION"})
        add("notification.created", {
            "notification_id": notification_id,
            "notification_type": "decision.requested",
        })
        files[decision_path(decision_id)] = {
            "decision_id": decision_id,
            "operation_id": operation_id,
            "operation_generation": 0,
            "feature_id": feature_id,
            "target_ref": target_ref,
        }
        files[notification_path(notification_id)] = {
            "notification_id": notification_id,
            "notification_type": "decision.requested",
            "operation_id": operation_id,
            "operation_generation": 0,
            "feature_id": feature_id,
            "decision_id": decision_id,
        }
        files[session_trace_path(operation_id)] = {
            "schema_version": SESSION_TRACE_SCHEMA,
            "scenario": "session_recovery",
            "operation_id": operation_id,
            "operation_generation": 0,
            "repository": REPO.lower(),
            "feature_id": feature_id,
            "target_ref": target_ref,
            "original_end_status": "WAITING_EXTERNAL",
            "final_status": "NEEDS_USER",
            "original_session": {
                "response_ids": obs["response_ids"],
                "function_call_ids": obs["function_call_ids"],
                "terminal_response_id": obs["response_ids"][-1],
            },
            "recovery_session": {
                "response_ids": obs["recovery_response_ids"],
                "function_call_ids": obs["recovery_function_call_ids"],
                "terminal_response_id": obs["recovery_response_ids"][-1],
            },
            "inbox_discovery": {
                "call_id": obs["recovery_function_call_ids"][0],
                "output_digest": "c" * 64,
                "operation_status": "NEEDS_USER",
                "decision_id": decision_id,
                "decision_status": "PENDING",
                "notification_id": notification_id,
                "notification_status": "UNREAD",
            },
            "decision_id": decision_id,
            "notification_id": notification_id,
        }
    else:
        add("operation.done", {"feature_revision": revision})
        notification_id = "completion-1"
        add("notification.created", {
            "notification_id": notification_id,
            "notification_type": "operation.completed",
        })
        files[notification_path(notification_id)] = {
            "notification_id": notification_id,
            "notification_type": "operation.completed",
            "operation_id": operation_id,
            "operation_generation": 0,
            "feature_id": feature_id,
        }

    for row in rows:
        files[event_path(operation_id, row["sequence"], row["event_id"])] = row
    snapshot = StoreSnapshot(ref_sha="f" * 40, files=files)
    projection = rebuild_projection(snapshot, operation_id)
    return snapshot, rows, projection


def reconstruct(scenario, snapshot, rows, projection, obs=None):
    return _reconstruct_release_authority(
        scenario,
        snapshot,
        rows,
        projection,
        observation(scenario) if obs is None else obs,
        repository=REPO,
        source_run_id=8000,
    )


def validate_durable_authority_reconstruction():
    for scenario in SCENARIO_PROFILES:
        snapshot, rows, projection = durable_history(scenario)
        milestones, assertions, start_state, end_state = reconstruct(scenario, snapshot, rows, projection)
        assert assertions == trusted_assertions(scenario)
        assert [row["name"] for row in milestones] == [
            name for name, _state, _categories in SCENARIO_PROFILES[scenario]["milestones"]
        ]
        assert start_state == SCENARIO_PROFILES[scenario]["start_state"]
        assert end_state == SCENARIO_PROFILES[scenario]["end_state"]

    snapshot, rows, projection = durable_history("review_remediation")
    rows = [row for row in rows if not (
        row["event_type"] == "feature.event.translated"
        and any(
            change.get("kind") == "task-record"
            for change in ((row.get("payload") or {}).get("feature_event") or {}).get("changes", [])
        )
    )]
    broken = _snapshot_with_rows(snapshot, rows)
    require_rejected(
        "Reviewer REWORK without exact remediation Feature Event",
        lambda: reconstruct("review_remediation", broken, rows, rebuild_projection(broken, observation("review_remediation")["operation_id"])),
    )

    snapshot, rows, projection = durable_history("happy_path")
    rows = [row for row in rows if row["event_type"] != "persist.confirmed"]
    broken = _snapshot_with_rows(snapshot, rows)
    require_rejected(
        "missing durable Persist confirmation",
        lambda: reconstruct("happy_path", broken, rows, rebuild_projection(broken, observation("happy_path")["operation_id"])),
    )

    snapshot, rows, projection = durable_history("happy_path")
    changed = deepcopy(rows)
    qa_translation = next(
        row for row in changed
        if row["event_type"] == "feature.event.translated"
        and any(
            change.get("id") == "verification-gate"
            for change in ((row.get("payload") or {}).get("feature_event") or {}).get("changes", [])
        )
    )
    qa_translation["payload"]["feature_event"]["changes"][0]["status"] = "FAIL"
    qa_translation["payload"]["feature_event_digest"] = digest_json(qa_translation["payload"]["feature_event"])
    broken = _snapshot_with_rows(snapshot, changed)
    require_rejected(
        "QA verdict without durable verification Gate PASS",
        lambda: reconstruct("happy_path", broken, changed, rebuild_projection(broken, observation("happy_path")["operation_id"])),
    )

    snapshot, rows, projection = durable_history("session_recovery")
    drift = deepcopy(snapshot.files[session_trace_path(observation("session_recovery")["operation_id"])])
    drift["decision_id"] = "decision-other"
    files = dict(snapshot.files)
    files[session_trace_path(observation("session_recovery")["operation_id"])] = drift
    broken = StoreSnapshot(ref_sha=snapshot.ref_sha, files=files)
    require_rejected(
        "protected recovery trace wrong Decision identity",
        lambda: reconstruct("session_recovery", broken, rows, projection),
    )

    snapshot, rows, projection = durable_history("session_recovery")
    files = dict(snapshot.files)
    files.pop(session_trace_path(observation("session_recovery")["operation_id"]))
    broken = StoreSnapshot(ref_sha=snapshot.ref_sha, files=files)
    require_rejected(
        "missing protected recovery session trace",
        lambda: reconstruct("session_recovery", broken, rows, projection),
    )


    snapshot, rows, projection = durable_history("happy_path")
    milestones, _assertions, _start, _end = reconstruct("happy_path", snapshot, rows, projection)
    by_name = {row["name"]: row["evidence_uris"] for row in milestones}
    assert "/actions/runs/9001" in " ".join(by_name["developer-completed"])
    assert "/actions/runs/9002" not in " ".join(by_name["developer-completed"])
    assert "/actions/runs/9002" in " ".join(by_name["independent-review-passed"])
    assert "/actions/runs/9003" in " ".join(by_name["qa-passed-and-done"])


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
