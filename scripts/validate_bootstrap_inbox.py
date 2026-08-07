#!/usr/bin/env python3
import yaml

from bootstrap_feature import build_manifest, load_profile
from ingest_feature_event import ingest
from orchestrator_state import compute_state
from validate_feature_transition import event


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def materialize(plan_result):
    return yaml.safe_load(plan_result["plan"]["manifest"]["content"])


def main():
    profile = load_profile("standard-feature")
    bootstrap = {
        "version": "0.1.0",
        "feature": {
            "id": "F-0041",
            "title": "Bootstrap and event inbox",
            "risk": "medium",
            "issue": "#41",
        },
        "profile": "standard-feature",
        "created_at": "2026-08-07T11:22:00Z",
    }
    created = build_manifest(bootstrap, profile)
    require(created["outcome"] == "BOOTSTRAPPED", f"bootstrap failed: {created}")
    manifest = created["manifest"]
    require(manifest["workflow"]["current_stage"] == "requirement", "wrong bootstrap current stage")
    require(manifest["workflow"]["stages"][0]["status"] == "READY", "first stage not READY")
    require(not manifest["applied_events"], "bootstrap should start with empty event ledger")

    first = compute_state(manifest, profile)
    require(first["outcome"] == "DISPATCH", f"bootstrap did not dispatch: {first}")
    require(first["actions"][0]["stage"] == "requirement", f"wrong first dispatch: {first}")
    require(first["actions"][0]["role"] == "product", f"wrong first role: {first}")

    start = event(
        "F-0041",
        [{"kind": "stage", "id": "requirement", "status": "WORKING"}],
        "2026-08-07T11:23:00Z",
        event_id="EVT-F0041-REQ-START",
    )
    start_result = ingest(
        manifest,
        start,
        event_path="state/events/F-0041/EVT-F0041-REQ-START.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
        issue=41,
    )
    require(start_result["outcome"] == "PLANNED", f"start inbox event failed: {start_result}")
    working_manifest = materialize(start_result)
    require("EVT-F0041-REQ-START" in working_manifest["applied_events"], "event id not persisted")
    require(compute_state(working_manifest, profile)["outcome"] == "WAIT", "working stage should produce WAIT")

    replay = ingest(
        working_manifest,
        start,
        event_path="state/events/F-0041/EVT-F0041-REQ-START.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(replay["outcome"] == "INVALID", "replayed inbox event unexpectedly passed")

    done = event(
        "F-0041",
        [{"kind": "stage", "id": "requirement", "status": "DONE"}],
        "2026-08-07T11:24:00Z",
        event_id="EVT-F0041-REQ-DONE",
    )
    done_result = ingest(
        working_manifest,
        done,
        event_path="state/events/F-0041/EVT-F0041-REQ-DONE.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(done_result["outcome"] == "PLANNED", f"done inbox event failed: {done_result}")
    completed_manifest = materialize(done_result)
    next_state = compute_state(completed_manifest, profile)
    require(next_state["outcome"] == "DISPATCH", f"completion did not unlock next dispatch: {next_state}")
    require([action["stage"] for action in next_state["actions"]] == ["requirement-review"], f"wrong next stage: {next_state}")

    wrong_folder = ingest(
        manifest,
        start,
        event_path="state/events/F-OTHER/EVT-F0041-REQ-START.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(wrong_folder["outcome"] == "INVALID", "wrong feature inbox folder unexpectedly passed")

    wrong_filename = ingest(
        manifest,
        start,
        event_path="state/events/F-0041/not-the-event-id.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(wrong_filename["outcome"] == "INVALID", "wrong inbox filename unexpectedly passed")

    print("Feature bootstrap and event inbox scenarios passed")


if __name__ == "__main__":
    main()
