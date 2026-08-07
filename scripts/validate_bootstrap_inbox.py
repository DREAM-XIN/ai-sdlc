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


def bootstrap_doc(feature_id, profile_id, risk="medium"):
    return {
        "version": "0.1.0",
        "feature": {
            "id": feature_id,
            "title": f"Bootstrap {profile_id}",
            "risk": risk,
            "issue": "#41",
        },
        "profile": profile_id,
        "created_at": "2026-08-07T11:22:00Z",
    }


def main():
    for profile_id, risk in [
        ("standard-feature", "medium"),
        ("small-change", "low"),
        ("high-risk", "high"),
    ]:
        profile = load_profile(profile_id)
        created = build_manifest(bootstrap_doc(f"F-{profile_id}", profile_id, risk), profile)
        require(created["outcome"] == "BOOTSTRAPPED", f"{profile_id} bootstrap failed: {created}")
        manifest = created["manifest"]
        require(manifest["revision"] == 0, f"{profile_id} bootstrap did not start at revision 0")
        require(manifest["workflow"]["stages"][0]["status"] == "READY", f"{profile_id} first stage not READY")
        require(all(stage["status"] == "TODO" for stage in manifest["workflow"]["stages"][1:]), f"{profile_id} later stages not TODO")
        gate_ids = {stage.get("gate") for stage in profile["stages"] if stage.get("gate")}
        require({gate["id"] for gate in manifest["gates"]} == gate_ids, f"{profile_id} gate initialization mismatch")
        first_state = compute_state(manifest, profile)
        require(first_state["outcome"] == "DISPATCH", f"{profile_id} did not yield first dispatch: {first_state}")
        require(first_state["actions"][0]["stage"] == profile["stages"][0]["id"], f"{profile_id} wrong first stage")
        require(first_state["actions"][0]["role"] == profile["stages"][0]["role"], f"{profile_id} wrong first role")

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
    require(manifest["revision"] == 0, "bootstrap revision should be zero")
    require(manifest["workflow"]["current_stage"] == "requirement", "wrong bootstrap current stage")
    require(not manifest["applied_events"], "bootstrap should start with empty event ledger")

    first = compute_state(manifest, profile)
    require(first["outcome"] == "DISPATCH", f"bootstrap did not dispatch: {first}")
    require(first["actions"][0]["stage"] == "requirement", f"wrong first dispatch: {first}")
    require(first["actions"][0]["role"] == "product", f"wrong first role: {first}")

    missing_revision = event(
        "F-0041",
        [{"kind": "stage", "id": "requirement", "status": "WORKING"}],
        "2026-08-07T11:22:30Z",
        event_id="EVT-F0041-NO-REV",
    )
    missing_revision_result = ingest(
        manifest,
        missing_revision,
        event_path="state/events/F-0041/EVT-F0041-NO-REV.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(missing_revision_result["outcome"] == "INVALID", "Inbox accepted an event without expected_revision")
    require("requires expected_revision" in "\n".join(missing_revision_result["errors"]), "missing revision rejection lacks detail")

    start = event(
        "F-0041",
        [{"kind": "stage", "id": "requirement", "status": "WORKING"}],
        "2026-08-07T11:23:00Z",
        event_id="EVT-F0041-REQ-START",
        expected_revision=0,
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
    require(start_result["plan"]["manifest"]["source_revision"] == 0, "persistence plan lost source revision")
    require(start_result["plan"]["manifest"]["revision"] == 1, "persistence plan lost result revision")
    require(len(start_result["plan"]["manifest"]["source_sha256"]) == 64, "source manifest digest missing")
    working_manifest = materialize(start_result)
    require(working_manifest["revision"] == 1, "first Inbox event did not produce revision 1")
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

    stale_done = event(
        "F-0041",
        [{"kind": "stage", "id": "requirement", "status": "DONE"}],
        "2026-08-07T11:23:30Z",
        event_id="EVT-F0041-REQ-DONE-STALE",
        expected_revision=0,
    )
    stale_result = ingest(
        working_manifest,
        stale_done,
        event_path="state/events/F-0041/EVT-F0041-REQ-DONE-STALE.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(stale_result["outcome"] == "INVALID", "Inbox accepted a stale expected revision")

    done = event(
        "F-0041",
        [{"kind": "stage", "id": "requirement", "status": "DONE"}],
        "2026-08-07T11:24:00Z",
        event_id="EVT-F0041-REQ-DONE",
        expected_revision=1,
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
    require(completed_manifest["revision"] == 2, "second Inbox event did not produce revision 2")
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

    legacy = dict(start)
    legacy.pop("id")
    legacy_inbox = ingest(
        manifest,
        legacy,
        event_path="state/events/F-0041/legacy.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0041.yaml",
        target_ref="feature/F-0041",
    )
    require(legacy_inbox["outcome"] == "INVALID", "Inbox accepted a legacy event without explicit id")

    print("Feature bootstrap, revision and Event Inbox scenarios passed")


if __name__ == "__main__":
    main()
