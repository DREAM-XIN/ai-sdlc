#!/usr/bin/env python3
from copy import deepcopy
import hashlib
import json

from apply_feature_event import apply_event
from orchestrator_state import compute_state
from validate_orchestrator_examples import base_manifest, load_profile


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def event(
    feature_id,
    changes,
    occurred_at="2026-08-07T11:11:00Z",
    event_id=None,
    expected_revision=None,
):
    if event_id is None:
        signature = json.dumps(
            {
                "feature_id": feature_id,
                "occurred_at": occurred_at,
                "changes": changes,
                "expected_revision": expected_revision,
            },
            sort_keys=True,
        )
        event_id = "EVT-" + hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    result = {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": feature_id,
        "occurred_at": occurred_at,
        "changes": changes,
    }
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    return result


def main():
    profile = load_profile("standard-feature")
    initial = base_manifest()

    first = compute_state(initial, profile)
    require(first["outcome"] == "DISPATCH" and first["actions"][0]["stage"] == "design", f"unexpected initial state: {first}")

    start_event = event(
        "F-0030",
        [{"kind": "stage", "id": "design", "status": "WORKING"}],
        expected_revision=0,
    )
    started = apply_event(initial, start_event)
    require(started["outcome"] == "APPLIED", f"start event failed: {started}")
    require(started["source_revision"] == 0 and started["result_revision"] == 1, f"wrong revision transition: {started}")
    require(started["manifest"]["revision"] == 1, "result manifest revision was not incremented")
    require(start_event["id"] in started["manifest"].get("applied_events", []), "event identity not recorded")

    replay = apply_event(started["manifest"], start_event)
    require(replay["outcome"] == "INVALID", "replayed event unexpectedly passed")
    require("already applied" in "\n".join(replay["errors"]), f"replay rejection lacks detail: {replay}")

    stale = apply_event(
        started["manifest"],
        event(
            "F-0030",
            [{"kind": "stage", "id": "design", "status": "DONE"}],
            "2026-08-07T11:11:30Z",
            event_id="EVT-STALE-DESIGN-DONE",
            expected_revision=0,
        ),
    )
    require(stale["outcome"] == "INVALID", "stale revision unexpectedly passed")
    require("stale event revision" in "\n".join(stale["errors"]), f"stale rejection lacks detail: {stale}")
    require(started["manifest"]["revision"] == 1, "stale event mutated source manifest")

    # Low-level protocol compatibility: legacy v0.1 events may omit both id and expected_revision.
    legacy_event = dict(start_event)
    legacy_event.pop("id")
    legacy_event.pop("expected_revision")
    legacy_started = apply_event(initial, legacy_event)
    require(legacy_started["outcome"] == "APPLIED", f"legacy event without id/revision failed: {legacy_started}")
    require(legacy_started["manifest"]["revision"] == 1, "legacy event did not adopt revision tracking")
    legacy_id = legacy_started.get("event_id")
    require(legacy_id and legacy_id.startswith("legacy-"), f"legacy identity not derived: {legacy_started}")
    legacy_replay = apply_event(legacy_started["manifest"], legacy_event)
    require(legacy_replay["outcome"] == "INVALID", "legacy event replay unexpectedly passed")

    waiting = compute_state(started["manifest"], profile)
    require(waiting["outcome"] == "WAIT", f"working stage should wait: {waiting}")

    completed = apply_event(
        started["manifest"],
        event(
            "F-0030",
            [{"kind": "stage", "id": "design", "status": "DONE"}],
            "2026-08-07T11:12:00Z",
            expected_revision=1,
        ),
    )
    require(completed["outcome"] == "APPLIED", f"completion failed: {completed}")
    require(completed["manifest"]["revision"] == 2, "second valid event did not increment revision")
    next_state = compute_state(completed["manifest"], profile)
    require(next_state["outcome"] == "DISPATCH", f"next dispatch missing: {next_state}")
    require([item["stage"] for item in next_state["actions"]] == ["design-review"], f"wrong next dispatch: {next_state}")

    blocked = apply_event(
        initial,
        event(
            "F-0030",
            [{"kind": "stage", "id": "design", "status": "BLOCKED", "reason": "missing API contract"}],
            expected_revision=0,
        ),
    )
    require(blocked["outcome"] == "APPLIED", f"block failed: {blocked}")
    require(compute_state(blocked["manifest"], profile)["outcome"] == "BLOCKED", "blocked manifest did not block orchestrator")

    rework = apply_event(
        blocked["manifest"],
        event(
            "F-0030",
            [{"kind": "stage", "id": "design", "status": "READY"}],
            "2026-08-07T11:13:00Z",
            expected_revision=1,
        ),
    )
    require(rework["outcome"] == "APPLIED", f"rework failed: {rework}")
    require(compute_state(rework["manifest"], profile)["outcome"] == "DISPATCH", "rework did not restore dispatch")

    with_evidence = apply_event(
        initial,
        event(
            "F-0030",
            [
                {"kind": "evidence", "record": {"id": "EVID-DESIGN", "type": "review", "status": "pass", "uri": "review://design"}},
                {"kind": "gate", "id": "design-gate", "status": "PASS", "evidence": ["EVID-DESIGN"]},
            ],
            expected_revision=0,
        ),
    )
    require(with_evidence["outcome"] == "APPLIED", f"evidence/gate event failed: {with_evidence}")
    gate = next(item for item in with_evidence["manifest"]["gates"] if item["id"] == "design-gate")
    require(gate["status"] == "PASS" and gate["evidence"] == ["EVID-DESIGN"], f"gate evidence not persisted: {gate}")

    duplicate_evidence = apply_event(
        with_evidence["manifest"],
        event(
            "F-0030",
            [{"kind": "evidence", "record": {"id": "EVID-DESIGN", "type": "review", "status": "pass", "uri": "review://again"}}],
            expected_revision=1,
        ),
    )
    require(duplicate_evidence["outcome"] == "INVALID", "duplicate evidence unexpectedly passed")

    mismatch = apply_event(
        initial,
        event("F-OTHER", [{"kind": "stage", "id": "design", "status": "WORKING"}], expected_revision=0),
    )
    require(mismatch["outcome"] == "INVALID", "feature mismatch unexpectedly passed")

    done_manifest = deepcopy(initial)
    done_stage = next(item for item in done_manifest["workflow"]["stages"] if item["id"] == "design")
    done_stage["status"] = "DONE"
    illegal = apply_event(
        done_manifest,
        event("F-0030", [{"kind": "stage", "id": "design", "status": "WORKING"}], expected_revision=0),
    )
    require(illegal["outcome"] == "INVALID", "illegal DONE -> WORKING unexpectedly passed")

    gate_without_evidence = apply_event(
        initial,
        event("F-0030", [{"kind": "gate", "id": "design-gate", "status": "PASS"}], expected_revision=0),
    )
    require(gate_without_evidence["outcome"] == "INVALID", "gate PASS without evidence unexpectedly passed")

    terminal = deepcopy(initial)
    terminal["workflow"]["status"] = "CANCELLED"
    terminal_event = apply_event(
        terminal,
        event("F-0030", [{"kind": "stage", "id": "design", "status": "WORKING"}], expected_revision=0),
    )
    require(terminal_event["outcome"] == "INVALID", "terminal workflow unexpectedly reopened")
    require("terminal workflow" in "\n".join(terminal_event["errors"]), f"terminal failure lacks detail: {terminal_event}")

    # Parallel work may proceed independently, but Feature-state writes are serialized by revision.
    parallel_manifest = {
        "protocol_version": "0.1.0",
        "revision": 0,
        "feature": {"id": "F-P", "title": "Parallel", "risk": "medium"},
        "workflow": {
            "profile": "parallel-test",
            "status": "ACTIVE",
            "current_stage": "backend",
            "stages": [
                {"id": "root", "status": "DONE"},
                {"id": "backend", "status": "READY"},
                {"id": "frontend", "status": "READY"},
            ],
        },
        "gates": [],
        "applied_events": [],
        "updated_at": "2026-08-07T11:01:00Z",
    }
    backend_start = apply_event(
        parallel_manifest,
        event("F-P", [{"kind": "stage", "id": "backend", "status": "WORKING"}], event_id="EVT-BE", expected_revision=0),
    )
    require(backend_start["outcome"] == "APPLIED", f"parallel backend start failed: {backend_start}")
    stale_frontend = apply_event(
        backend_start["manifest"],
        event("F-P", [{"kind": "stage", "id": "frontend", "status": "WORKING"}], event_id="EVT-FE-STALE", expected_revision=0),
    )
    require(stale_frontend["outcome"] == "INVALID", "parallel stale state write unexpectedly passed")
    refreshed_frontend = apply_event(
        backend_start["manifest"],
        event("F-P", [{"kind": "stage", "id": "frontend", "status": "WORKING"}], event_id="EVT-FE", expected_revision=1),
    )
    require(refreshed_frontend["outcome"] == "APPLIED", f"refreshed parallel state write failed: {refreshed_frontend}")
    require(refreshed_frontend["manifest"]["revision"] == 2, "parallel refreshed write did not increment revision")

    print("Feature transition, revision, replay and closed-loop orchestration scenarios passed")


if __name__ == "__main__":
    main()
