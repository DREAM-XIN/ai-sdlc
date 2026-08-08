#!/usr/bin/env python3
from copy import deepcopy

import yaml

from commander import (
    build_commander_plan,
    commander_bootstrap,
    commander_ingest,
    commander_plan_errors,
)
from orchestrator_state import load_profile
from runtime_router import load_yaml
from validate_feature_transition import event

ROOT_POLICY = "dispatch/default.yaml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def materialize(persistence_result):
    return yaml.safe_load(persistence_result["plan"]["manifest"]["content"])


def small_change_manifest(current_stage, statuses, code_gate="PENDING", verification_gate="PENDING"):
    return {
        "protocol_version": "0.1.0",
        "revision": 2,
        "feature": {"id": "F-SMALL-COMMANDER", "title": "Small Commander change", "risk": "low"},
        "workflow": {
            "profile": "small-change",
            "status": "ACTIVE",
            "current_stage": current_stage,
            "stages": [
                {"id": "requirement", "status": statuses["requirement"]},
                {"id": "implementation", "status": statuses["implementation"]},
                {"id": "review", "status": statuses["review"], "gate": "code-gate"},
                {"id": "verification", "status": statuses["verification"], "gate": "verification-gate"},
            ],
        },
        "gates": [
            {"id": "code-gate", "status": code_gate},
            {"id": "verification-gate", "status": verification_gate},
        ],
        "updated_at": "2026-08-08T14:42:00Z",
    }


def main():
    profile = load_profile("standard-feature")
    policy = load_yaml(__import__("pathlib").Path(ROOT_POLICY))
    bootstrap = {
        "version": "0.1.0",
        "feature": {
            "id": "F-0043",
            "title": "Reference Commander CLI",
            "risk": "medium",
            "issue": "#43",
        },
        "profile": "standard-feature",
        "created_at": "2026-08-07T11:31:00Z",
    }

    created = commander_bootstrap(bootstrap, profile)
    require(created["outcome"] == "BOOTSTRAPPED", f"Commander bootstrap failed: {created}")
    manifest = created["manifest"]
    require(manifest["revision"] == 0, "Commander bootstrap did not initialize revision 0")

    first_plan = build_commander_plan(
        manifest,
        profile,
        policy,
        repository="DREAM-XIN/ai-sdlc",
        manifest_ref="state/features/F-0043.yaml",
    )
    require(first_plan["outcome"] == "DISPATCH", f"Commander first plan failed: {first_plan}")
    require(first_plan["summary"]["revision"] == 0, "Commander Plan does not expose current revision")
    require(not commander_plan_errors(first_plan), f"Commander Plan did not validate: {first_plan}")
    first_dispatch = first_plan["dispatches"][0]
    require(first_dispatch["action"]["stage"] == "requirement", f"wrong first stage: {first_dispatch}")
    require(first_dispatch["action"]["role"] == "product", f"wrong first role: {first_dispatch}")
    require(first_dispatch["runtime"] == {"id": "chatgpt-web", "mode": "manual"}, f"wrong runtime: {first_dispatch}")
    require("task" in first_dispatch and "package" in first_dispatch and "prompt" in first_dispatch, "manual dispatch lacks executable payload")
    require("Repository: DREAM-XIN/ai-sdlc" in first_dispatch["prompt"], "prompt lacks repository")
    require("## Definition of Done" in first_dispatch["prompt"], "prompt lacks DoD")
    require("expected_revision to 0" in first_dispatch["prompt"], "manual prompt lacks revision precondition")

    standard_implementation_manifest = deepcopy(manifest)
    standard_implementation_manifest["revision"] = 6
    standard_implementation_manifest["workflow"]["current_stage"] = "implementation"
    standard_stage_statuses = {
        "requirement": "DONE",
        "requirement-review": "DONE",
        "design": "DONE",
        "design-review": "DONE",
        "plan": "DONE",
        "implementation": "READY",
        "code-review": "TODO",
        "verification": "TODO",
        "acceptance": "TODO",
    }
    for stage in standard_implementation_manifest["workflow"]["stages"]:
        stage["status"] = standard_stage_statuses[stage["id"]]
    for gate in standard_implementation_manifest["gates"]:
        gate["status"] = "PASS" if gate["id"] in {"requirement-gate", "design-gate"} else "PENDING"
    standard_impl_plan = build_commander_plan(
        standard_implementation_manifest,
        profile,
        policy,
        repository="DREAM-XIN/ai-sdlc",
    )
    require(standard_impl_plan["outcome"] == "DISPATCH", f"standard implementation plan failed: {standard_impl_plan}")
    standard_impl_inputs = standard_impl_plan["dispatches"][0]["task"]["inputs"]
    require("approved design" in standard_impl_inputs, "standard implementation lost approved design")
    require("assigned work unit" in standard_impl_inputs, "standard implementation lost assigned work unit")

    small_profile = load_profile("small-change")
    small_impl_plan = build_commander_plan(
        small_change_manifest(
            "implementation",
            {"requirement": "DONE", "implementation": "READY", "review": "TODO", "verification": "TODO"},
        ),
        small_profile,
        policy,
        repository="DREAM-XIN/example-small-change",
    )
    require(small_impl_plan["outcome"] == "DISPATCH", f"small-change implementation plan failed: {small_impl_plan}")
    require(not commander_plan_errors(small_impl_plan), f"small-change implementation plan invalid: {small_impl_plan}")
    small_impl_dispatch = small_impl_plan["dispatches"][0]
    require(small_impl_dispatch["action"]["stage"] == "implementation", f"wrong small-change implementation stage: {small_impl_dispatch}")
    require("requirement artifact" in small_impl_dispatch["task"]["inputs"], "small-change implementation lacks requirement artifact")
    require("approved design" not in small_impl_dispatch["task"]["inputs"], "small-change implementation requires nonexistent design")
    require("assigned work unit" not in small_impl_dispatch["task"]["inputs"], "small-change implementation requires nonexistent work unit")
    require("approved design" not in small_impl_dispatch["prompt"], "small-change implementation prompt references nonexistent design")
    require("assigned work unit" not in small_impl_dispatch["prompt"], "small-change implementation prompt references nonexistent work unit")

    small_review_plan = build_commander_plan(
        small_change_manifest(
            "review",
            {"requirement": "DONE", "implementation": "DONE", "review": "READY", "verification": "TODO"},
        ),
        small_profile,
        policy,
        repository="DREAM-XIN/example-small-change",
    )
    require(small_review_plan["outcome"] == "DISPATCH", f"small-change review plan failed: {small_review_plan}")
    require(not commander_plan_errors(small_review_plan), f"small-change review plan invalid: {small_review_plan}")
    require(small_review_plan["dispatches"][0]["action"]["stage"] == "review", "small-change review stage did not resolve")
    require(small_review_plan["dispatches"][0]["task"]["role"] == "reviewer", "small-change review role did not resolve")

    small_verification_plan = build_commander_plan(
        small_change_manifest(
            "verification",
            {"requirement": "DONE", "implementation": "DONE", "review": "DONE", "verification": "READY"},
            code_gate="PASS",
        ),
        small_profile,
        policy,
        repository="DREAM-XIN/example-small-change",
    )
    require(small_verification_plan["outcome"] == "DISPATCH", f"small-change verification plan failed: {small_verification_plan}")
    require("approved design" not in small_verification_plan["dispatches"][0]["task"]["inputs"], "small-change verification requires nonexistent design")

    future_policy = deepcopy(policy)
    future_policy["routes"].append(
        {
            "id": "autonomous-requirement",
            "priority": 100,
            "match": {"stage": "requirement", "role": "product"},
            "runtime": {"id": "gh-aw", "mode": "autonomous"},
        }
    )
    future_plan = build_commander_plan(
        manifest,
        profile,
        future_policy,
        repository="DREAM-XIN/ai-sdlc",
    )
    require(future_plan["outcome"] == "DISPATCH", f"future runtime route failed: {future_plan}")
    future_dispatch = future_plan["dispatches"][0]
    require(future_dispatch["runtime"] == {"id": "gh-aw", "mode": "autonomous"}, f"future runtime not selected: {future_dispatch}")
    require(not any(field in future_dispatch for field in ("task", "package", "prompt")), "Commander fabricated payload for unsupported autonomous runtime")
    require(not commander_plan_errors(future_plan), f"future Commander Plan invalid: {future_plan}")

    start = event(
        "F-0043",
        [{"kind": "stage", "id": "requirement", "status": "WORKING"}],
        "2026-08-07T11:32:00Z",
        event_id="EVT-F0043-REQ-START",
        expected_revision=0,
    )
    start_result = commander_ingest(
        manifest,
        start,
        event_path="state/events/F-0043/EVT-F0043-REQ-START.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0043.yaml",
        target_ref="feature/F-0043",
        issue=43,
    )
    require(start_result["outcome"] == "PLANNED", f"Commander ingest START failed: {start_result}")
    working = materialize(start_result)
    require(working["revision"] == 1, "Commander START did not advance revision")
    wait_plan = build_commander_plan(working, profile, policy, repository="DREAM-XIN/ai-sdlc")
    require(wait_plan["outcome"] == "WAIT", f"Commander did not WAIT for working stage: {wait_plan}")
    require(wait_plan["summary"]["revision"] == 1, "WAIT plan lost current revision")
    require(not wait_plan["dispatches"], "WAIT plan unexpectedly contains dispatches")

    done = event(
        "F-0043",
        [{"kind": "stage", "id": "requirement", "status": "DONE"}],
        "2026-08-07T11:33:00Z",
        event_id="EVT-F0043-REQ-DONE",
        expected_revision=1,
    )
    done_result = commander_ingest(
        working,
        done,
        event_path="state/events/F-0043/EVT-F0043-REQ-DONE.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0043.yaml",
        target_ref="feature/F-0043",
        issue=43,
    )
    require(done_result["outcome"] == "PLANNED", f"Commander ingest DONE failed: {done_result}")
    completed_requirement = materialize(done_result)
    require(completed_requirement["revision"] == 2, "Commander DONE did not advance revision")
    next_plan = build_commander_plan(completed_requirement, profile, policy, repository="DREAM-XIN/ai-sdlc")
    require(next_plan["outcome"] == "DISPATCH", f"Commander did not dispatch next stage: {next_plan}")
    require(next_plan["summary"]["revision"] == 2, "next Commander Plan lost revision")
    require([item["action"]["stage"] for item in next_plan["dispatches"]] == ["requirement-review"], f"wrong next Commander dispatch: {next_plan}")
    require(next_plan["dispatches"][0]["runtime"] == {"id": "chatgpt-web", "mode": "manual"}, "review stage not routed to manual web")
    require("expected_revision to 2" in next_plan["dispatches"][0]["prompt"], "next prompt did not refresh revision")

    blocked_event = event(
        "F-0043",
        [{"kind": "stage", "id": "requirement", "status": "BLOCKED", "reason": "missing product decision"}],
        "2026-08-07T11:34:00Z",
        event_id="EVT-F0043-REQ-BLOCKED",
        expected_revision=0,
    )
    blocked_result = commander_ingest(
        manifest,
        blocked_event,
        event_path="state/events/F-0043/EVT-F0043-REQ-BLOCKED.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0043.yaml",
        target_ref="feature/F-0043",
    )
    blocked_manifest = materialize(blocked_result)
    blocked_plan = build_commander_plan(blocked_manifest, profile, policy, repository="DREAM-XIN/ai-sdlc")
    require(blocked_plan["outcome"] == "BLOCKED" and not blocked_plan["dispatches"], f"blocked Commander plan is unsafe: {blocked_plan}")

    terminal = deepcopy(manifest)
    for stage in terminal["workflow"]["stages"]:
        stage["status"] = "DONE"
    for gate in terminal["gates"]:
        gate["status"] = "PASS"
    terminal["workflow"]["status"] = "DONE"
    terminal["workflow"]["current_stage"] = terminal["workflow"]["stages"][-1]["id"]
    terminal_plan = build_commander_plan(terminal, profile, policy, repository="DREAM-XIN/ai-sdlc")
    require(terminal_plan["outcome"] == "COMPLETE" and not terminal_plan["dispatches"], f"terminal Commander plan unsafe: {terminal_plan}")

    no_route_policy = deepcopy(policy)
    no_route_policy["routes"] = [route for route in no_route_policy["routes"] if route["match"].get("role") != "product"]
    invalid_plan = build_commander_plan(manifest, profile, no_route_policy, repository="DREAM-XIN/ai-sdlc")
    require(invalid_plan["outcome"] == "INVALID" and not invalid_plan["dispatches"], f"missing route did not fail closed: {invalid_plan}")

    print("Reference Commander revision-aware end-to-end scenarios passed")


if __name__ == "__main__":
    main()
