#!/usr/bin/env python3
from copy import deepcopy
import json
from pathlib import Path

from apply_feature_event import apply_event
from bootstrap_feature import build_manifest, load_profile
from commander import build_commander_plan
from gh_aw_adapter import build_dispatch_plan, result_to_event, start_event_for_plan
from project_adapter import load_project_adapter
from runtime_router import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def stage_status(manifest, stage_id):
    return next(stage["status"] for stage in manifest["workflow"]["stages"] if stage["id"] == stage_id)


def task_status(manifest, task_id):
    return next(task["status"] for task in manifest.get("tasks", []) if task["id"] == task_id)


def gh_aw_policy():
    policy = deepcopy(load_yaml(ROOT / "dispatch" / "default.yaml"))
    policy["routes"].append(
        {
            "id": "gh-aw-product-requirement",
            "priority": 100,
            "match": {"stage": "requirement", "role": "product"},
            "runtime": {"id": "gh-aw", "mode": "autonomous"},
        }
    )
    return policy


def main():
    profile = load_profile("standard-feature")
    bootstrap = {
        "version": "0.1.0",
        "feature": {
            "id": "F-GHAW",
            "title": "Validate gh-aw autonomous runtime",
            "risk": "medium",
            "issue": "#3",
        },
        "profile": "standard-feature",
        "created_at": "2026-08-07T13:40:00Z",
    }
    created = build_manifest(bootstrap, profile)
    require(created["outcome"] == "BOOTSTRAPPED", f"bootstrap failed: {created}")
    manifest = created["manifest"]
    require(manifest["revision"] == 0, "gh-aw fixture must begin at revision 0")
    require(stage_status(manifest, "requirement") == "READY", "requirement should start READY")

    policy = gh_aw_policy()
    commander_plan = build_commander_plan(manifest, profile, policy, repository="DREAM-XIN/example-target")
    require(commander_plan["outcome"] == "DISPATCH", f"Commander did not dispatch: {commander_plan}")
    require(commander_plan["summary"]["revision"] == 0, "Commander revision mismatch")
    require(commander_plan["dispatches"][0]["runtime"] == {"id": "gh-aw", "mode": "autonomous"}, "gh-aw route not selected")
    require("task" not in commander_plan["dispatches"][0], "Commander fabricated a gh-aw task payload instead of leaving it to the adapter")

    project_result = load_project_adapter(ROOT / "examples" / "project-adapters" / "generic.yaml")
    require(project_result["outcome"] == "VALID", f"project fixture invalid: {project_result}")
    project = project_result["adapter"]
    planned = build_dispatch_plan(
        manifest,
        commander_plan,
        policy,
        repository="DREAM-XIN/example-target",
        target_ref="feature/F-GHAW",
        worker_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        project=project,
    )
    require(planned["outcome"] == "PLANNED", f"gh-aw dispatch plan failed: {planned}")
    plan = planned["plan"]
    require(plan["revision"] == 0, "dispatch plan source revision mismatch")
    require(len(plan["dispatches"]) == 1, "v0.1 gh-aw adapter must serialize autonomous dispatch")
    dispatch = plan["dispatches"][0]
    require(dispatch["work_kind"] == "stage", "ordinary stage dispatch lost stage work kind")
    require(dispatch["inputs"]["expected_revision"] == 1, "worker result must target post-START revision 1")
    require(dispatch["inputs"]["target_ref"] == "feature/F-GHAW", "worker target_ref input lost")
    require(dispatch["workflow"] == "ai-sdlc-gh-aw-worker.lock.yml", "worker workflow lost")
    require(dispatch["ref"] == "feature/F-GHAW", "workflow dispatch ref lost")
    payload = json.loads(dispatch["inputs"]["task_payload"])
    require(payload["contract"] == "ai-sdlc-task-v0.1", "runtime task contract missing")
    require(payload["task"]["feature_id"] == "F-GHAW", "runtime task feature identity missing")
    require(payload["task"]["runtime"] == "gh-aw", "runtime task runtime id mismatch")
    require(".ai-sdlc/project.yaml" in payload["task"]["inputs"], "Project Adapter reference missing from task inputs")
    require(payload["project"]["required_commands"], "Project Adapter command context missing")
    require("Do not self-approve any Gate." in payload["worker_rules"], "worker self-approval prohibition missing")

    start_ready = start_event_for_plan(planned, "2026-08-07T13:41:00Z")
    require(start_ready["outcome"] == "EVENT_READY", f"START event failed: {start_ready}")
    start_event = start_ready["event"]
    require(start_event["expected_revision"] == 0, "START event must reserve source revision 0")
    require(start_event["changes"] == [{"kind": "stage", "id": "requirement", "status": "WORKING"}], "START event does not reserve requirement stage")
    started = apply_event(manifest, start_event)
    require(started["outcome"] == "APPLIED", f"START transition failed: {started}")
    working = started["manifest"]
    require(working["revision"] == 1, "START transition did not produce revision 1")
    require(stage_status(working, "requirement") == "WORKING", "START transition did not set WORKING")

    worker_result = {
        "version": "0.1.0",
        "id": "GHAW-F-GHAW-REQ-1",
        "feature_id": "F-GHAW",
        "task_id": dispatch["task_id"],
        "stage": "requirement",
        "expected_revision": 1,
        "status": "COMPLETED",
        "occurred_at": "2026-08-07T13:42:00Z",
        "artifacts": [{"id": "ART-GHAW-REQ", "type": "requirement", "uri": "repo://docs/requirements/F-GHAW.md"}],
        "evidence": [{"id": "EVID-GHAW-REQ", "type": "worker-result", "status": "pass", "uri": "actions://run/123"}],
    }
    converted = result_to_event(worker_result)
    require(converted["outcome"] == "EVENT_READY", f"COMPLETED result conversion failed: {converted}")
    result_event = converted["event"]
    require(result_event["expected_revision"] == 1, "worker result event revision mismatch")
    require(any(change.get("kind") == "stage" and change.get("status") == "DONE" for change in result_event["changes"]), "COMPLETED worker did not complete its work stage")
    require(not any(change.get("kind") == "gate" for change in result_event["changes"]), "worker self-approved a Gate")
    completed = apply_event(working, result_event)
    require(completed["outcome"] == "APPLIED", f"worker result transition failed: {completed}")
    require(completed["manifest"]["revision"] == 2, "worker result did not produce revision 2")
    require(stage_status(completed["manifest"], "requirement") == "DONE", "completed worker did not leave work stage DONE")

    next_plan = build_commander_plan(completed["manifest"], profile, load_yaml(ROOT / "dispatch" / "default.yaml"), repository="DREAM-XIN/example-target")
    require(next_plan["outcome"] == "DISPATCH", f"Commander did not advance to independent review: {next_plan}")
    require(next_plan["dispatches"][0]["action"]["stage"] == "requirement-review", "next dispatch is not the independent review stage")

    # Independent review feedback creates a durable remediation task without reopening requirement.
    remediation_create_event = {
        "version": "0.1.0",
        "id": "EVT-F-GHAW-REMEDIATION-CREATE",
        "feature_id": "F-GHAW",
        "expected_revision": 2,
        "occurred_at": "2026-08-07T13:42:10Z",
        "changes": [{
            "kind": "task-record",
            "record": {
                "id": "F-GHAW-REMEDIATION-1",
                "kind": "remediation",
                "stage": "requirement",
                "role": "product",
                "source_stage": "requirement-review",
                "feedback": "Address the specific independent review feedback without approving the review stage.",
                "target_pr": "https://github.com/DREAM-XIN/example-target/pull/10",
                "status": "TODO",
                "runtime": "gh-aw",
            },
        }],
    }
    remediation_created = apply_event(completed["manifest"], remediation_create_event)
    require(remediation_created["outcome"] == "APPLIED", f"remediation task creation failed: {remediation_created}")
    remediation_manifest = remediation_created["manifest"]
    require(stage_status(remediation_manifest, "requirement") == "DONE", "remediation reopened requirement stage")
    require(stage_status(remediation_manifest, "requirement-review") != "DONE", "remediation incorrectly completed independent review")
    require(task_status(remediation_manifest, "F-GHAW-REMEDIATION-1") == "TODO", "remediation task was not persisted")

    remediation_commander = build_commander_plan(remediation_manifest, profile, policy, repository="DREAM-XIN/example-target")
    require(remediation_commander["outcome"] == "DISPATCH", f"Commander did not dispatch remediation: {remediation_commander}")
    remediation_action = remediation_commander["dispatches"][0]["action"]
    require(remediation_action["kind"] == "remediation" and remediation_action["task_id"] == "F-GHAW-REMEDIATION-1", f"Commander lost remediation identity: {remediation_action}")
    require(remediation_action["stage"] == "requirement" and remediation_action["role"] == "product", f"Commander remediation routing drifted: {remediation_action}")

    remediation_planned = build_dispatch_plan(
        remediation_manifest,
        remediation_commander,
        policy,
        repository="DREAM-XIN/example-target",
        target_ref="feature/F-GHAW",
        worker_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        project=project,
    )
    require(remediation_planned["outcome"] == "PLANNED", f"gh-aw remediation plan failed: {remediation_planned}")
    remediation_dispatch = remediation_planned["plan"]["dispatches"][0]
    require(remediation_dispatch["work_kind"] == "remediation", "remediation work kind was not carried to gh-aw")
    require(remediation_dispatch["task_id"] == "F-GHAW-REMEDIATION-1", "remediation task id drifted")
    require(remediation_dispatch["inputs"]["expected_revision"] == 4, "remediation worker must target post-START revision 4")
    remediation_payload = json.loads(remediation_dispatch["inputs"]["task_payload"])
    require(remediation_payload["task"]["kind"] == "remediation", "remediation task kind missing from payload")
    require("independent review feedback" in remediation_payload["task"]["goal"], "remediation feedback missing from task goal")
    require("https://github.com/DREAM-XIN/example-target/pull/10" in remediation_payload["task"]["inputs"], "target PR missing from remediation inputs")

    remediation_start = start_event_for_plan(remediation_planned, "2026-08-07T13:42:20Z")
    require(remediation_start["outcome"] == "EVENT_READY", f"remediation START failed: {remediation_start}")
    require(remediation_start["event"]["changes"] == [{"kind": "task", "id": "F-GHAW-REMEDIATION-1", "status": "WORKING"}], "remediation START mutated a lifecycle stage")
    remediation_working = apply_event(remediation_manifest, remediation_start["event"])
    require(remediation_working["outcome"] == "APPLIED", f"remediation START transition failed: {remediation_working}")
    require(task_status(remediation_working["manifest"], "F-GHAW-REMEDIATION-1") == "WORKING", "remediation task did not become WORKING")
    require(stage_status(remediation_working["manifest"], "requirement") == "DONE", "remediation START reopened requirement")

    remediation_result = {
        "version": "0.1.0",
        "id": "GHAW-F-GHAW-REMEDIATION-1",
        "feature_id": "F-GHAW",
        "task_id": "F-GHAW-REMEDIATION-1",
        "stage": "requirement",
        "work_kind": "remediation",
        "expected_revision": 4,
        "status": "COMPLETED",
        "occurred_at": "2026-08-07T13:42:30Z",
        "artifacts": [{"id": "ART-GHAW-REMEDIATION", "type": "pull-request", "uri": "https://github.com/DREAM-XIN/example-target/pull/11"}],
        "evidence": [{"id": "EVID-GHAW-REMEDIATION", "type": "runtime-run", "status": "pass", "uri": "actions://run/456"}],
    }
    remediation_converted = result_to_event(remediation_result)
    require(remediation_converted["outcome"] == "EVENT_READY", f"remediation result conversion failed: {remediation_converted}")
    require(any(change.get("kind") == "task" and change.get("id") == "F-GHAW-REMEDIATION-1" and change.get("status") == "DONE" for change in remediation_converted["event"]["changes"]), "remediation result did not complete task")
    require(not any(change.get("kind") in {"stage", "gate"} for change in remediation_converted["event"]["changes"]), "remediation result changed review or Gate authority")
    remediation_completed = apply_event(remediation_working["manifest"], remediation_converted["event"])
    require(remediation_completed["outcome"] == "APPLIED", f"remediation result transition failed: {remediation_completed}")
    require(task_status(remediation_completed["manifest"], "F-GHAW-REMEDIATION-1") == "DONE", "remediation result did not persist DONE")
    require(stage_status(remediation_completed["manifest"], "requirement") == "DONE", "remediation completion changed work stage")
    require(stage_status(remediation_completed["manifest"], "requirement-review") != "DONE", "remediation completion self-approved review")
    require(not any(gate["status"] == "PASS" for gate in remediation_completed["manifest"].get("gates", [])), "remediation completion self-approved a Gate")
    review_resumes = build_commander_plan(remediation_completed["manifest"], profile, load_yaml(ROOT / "dispatch" / "default.yaml"), repository="DREAM-XIN/example-target")
    require(review_resumes["outcome"] == "DISPATCH" and review_resumes["dispatches"][0]["action"]["stage"] == "requirement-review", f"independent review did not resume after remediation: {review_resumes}")

    stale_result = deepcopy(worker_result)
    stale_result["id"] = "GHAW-F-GHAW-STALE"
    stale_result["expected_revision"] = 0
    stale_event = result_to_event(stale_result)
    require(stale_event["outcome"] == "EVENT_READY", "adapter should preserve, not silently rewrite, worker expected revision")
    stale_apply = apply_event(working, stale_event["event"])
    require(stale_apply["outcome"] == "INVALID", "stale gh-aw result unexpectedly mutated state")
    require("stale event revision" in "\n".join(stale_apply["errors"]), "stale gh-aw result lacks revision diagnostic")

    for status in ("BLOCKED", "FAILED"):
        blocked_result = deepcopy(worker_result)
        blocked_result.update({"id": f"GHAW-F-GHAW-{status}", "status": status, "reason": f"{status.lower()} runtime condition", "evidence": [], "artifacts": []})
        converted_blocked = result_to_event(blocked_result)
        require(converted_blocked["outcome"] == "EVENT_READY", f"{status} result conversion failed")
        stage_change = next(change for change in converted_blocked["event"]["changes"] if change["kind"] == "stage")
        require(stage_change["status"] == "BLOCKED", f"{status} did not map to BLOCKED")
        require(stage_change["reason"] == blocked_result["reason"], f"{status} reason lost")
        applied_blocked = apply_event(working, converted_blocked["event"])
        require(applied_blocked["outcome"] == "APPLIED", f"{status} event failed transition")
        require(stage_status(applied_blocked["manifest"], "requirement") == "BLOCKED", f"{status} did not persist BLOCKED state")

    malformed = deepcopy(worker_result)
    malformed["id"] = "GHAW-BLOCKED-NO-REASON"
    malformed["status"] = "BLOCKED"
    malformed.pop("reason", None)
    require(result_to_event(malformed)["outcome"] == "INVALID", "BLOCKED result without reason unexpectedly passed")

    stale_commander = deepcopy(commander_plan)
    stale_commander["summary"]["revision"] = 9
    stale_plan = build_dispatch_plan(manifest, stale_commander, policy, repository="DREAM-XIN/example-target", target_ref="feature/F-GHAW", worker_workflow="ai-sdlc-gh-aw-worker.lock.yml")
    require(stale_plan["outcome"] == "INVALID", "stale Commander Plan unexpectedly produced gh-aw dispatch")

    double_commander = deepcopy(commander_plan)
    second = deepcopy(double_commander["dispatches"][0])
    second["action"] = {"stage": "design", "role": "architect", "gate": None, "parallel": True, "kind": "stage"}
    double_commander["dispatches"].append(second)
    double_plan = build_dispatch_plan(manifest, double_commander, policy, repository="DREAM-XIN/example-target", target_ref="feature/F-GHAW", worker_workflow="ai-sdlc-gh-aw-worker.lock.yml")
    require(double_plan["outcome"] == "INVALID", "parallel gh-aw dispatch unexpectedly passed v0.1 serialization guard")

    manual_policy = load_yaml(ROOT / "dispatch" / "default.yaml")
    manual_commander = build_commander_plan(manifest, profile, manual_policy, repository="DREAM-XIN/example-target")
    no_dispatch = build_dispatch_plan(manifest, manual_commander, manual_policy, repository="DREAM-XIN/example-target", target_ref="feature/F-GHAW", worker_workflow="ai-sdlc-gh-aw-worker.lock.yml")
    require(no_dispatch["outcome"] == "NO_DISPATCH", "manual-only Commander Plan was incorrectly treated as gh-aw work")

    print("gh-aw autonomous runtime adapter lifecycle and remediation scenarios passed")


if __name__ == "__main__":
    main()
