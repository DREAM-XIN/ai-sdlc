#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

from commander import build_commander_plan, commander_bootstrap
from orchestrator_state import load_profile
from project_adapter import load_project_adapter, validate_adapter
from runtime_router import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    generic_result = load_project_adapter(ROOT / "examples" / "project-adapters" / "generic.yaml")
    require(generic_result["outcome"] == "VALID", f"generic adapter invalid: {generic_result}")
    generic = generic_result["adapter"]

    java_result = load_project_adapter(ROOT / "examples" / "project-adapters" / "java-spring-vue.yaml")
    require(java_result["outcome"] == "VALID", f"Java/Vue adapter invalid: {java_result}")

    duplicate_command = deepcopy(generic)
    duplicate_command["commands"].append(deepcopy(duplicate_command["commands"][0]))
    require(
        any("duplicate command id" in error for error in validate_adapter(duplicate_command)),
        "duplicate command id unexpectedly passed",
    )

    missing_required = deepcopy(generic)
    missing_required["defaults"]["required_commands"].append("security-scan")
    require(
        any("required command does not exist" in error for error in validate_adapter(missing_required)),
        "missing required command unexpectedly passed",
    )

    traversal = deepcopy(generic)
    traversal["commands"][0]["cwd"] = "../outside"
    require(
        any("path traversal" in error for error in validate_adapter(traversal)),
        "path traversal cwd unexpectedly passed",
    )

    overlap = deepcopy(generic)
    overlap["ownership"].append(
        {"id": "nested", "role": "developer", "roots": ["src/api"]}
    )
    require(
        any("ambiguous ownership overlap" in error for error in validate_adapter(overlap)),
        "ambiguous ownership overlap unexpectedly passed",
    )

    shared_overlap = deepcopy(overlap)
    shared_overlap["ownership"][-1]["shared"] = True
    require(
        not any("ambiguous ownership overlap" in error for error in validate_adapter(shared_overlap)),
        "explicit shared ownership overlap was rejected",
    )

    bad_repo = deepcopy(generic)
    bad_repo["repository"]["full_name"] = "not-an-owner-repo"
    require(
        any("owner/name" in error for error in validate_adapter(bad_repo)),
        "invalid GitHub full_name unexpectedly passed",
    )

    control_arg = deepcopy(generic)
    control_arg["commands"][0]["argv"][0] = "python\nmalicious"
    require(
        any("control character" in error for error in validate_adapter(control_arg)),
        "command argv control character unexpectedly passed",
    )

    profile = load_profile("standard-feature")
    policy = load_yaml(ROOT / "dispatch" / "default.yaml")
    bootstrap = {
        "version": "0.1.0",
        "feature": {
            "id": "F-0048",
            "title": "Project Adapter",
            "risk": "medium",
            "issue": "#48",
        },
        "profile": "standard-feature",
        "created_at": "2026-08-07T12:57:00Z",
    }
    created = commander_bootstrap(bootstrap, profile)
    require(created["outcome"] == "BOOTSTRAPPED", f"bootstrap failed: {created}")

    plan = build_commander_plan(
        created["manifest"],
        profile,
        policy,
        repository=generic["repository"]["full_name"],
        manifest_ref="state/features/F-0048.yaml",
        project_adapter=generic,
        project_ref=".ai-sdlc/project.yaml",
    )
    require(plan["outcome"] == "DISPATCH", f"Commander adapter plan failed: {plan}")
    dispatch = plan["dispatches"][0]
    package = dispatch["package"]
    require(package["context"]["repository"] == "example/sample-app", "adapter repository not preserved")
    require(".ai-sdlc/project.yaml" in package["context"]["read"], "project adapter not included in durable reads")
    require("docs/architecture.md" in package["context"]["read"], "project read context not propagated")
    require("CONTRIBUTING.md" in package["context"]["project_rules"], "project rules not propagated")
    execution = "\n".join(package["instructions"]["execution"])
    require("ownership boundaries" in execution, "ownership instruction missing")
    require("test, lint" in execution, "required verification commands missing")
    require(".ai-sdlc/project.yaml" in dispatch["prompt"], "rendered prompt lacks project adapter reference")

    legacy_plan = build_commander_plan(
        created["manifest"],
        profile,
        policy,
        repository="example/legacy",
    )
    require(legacy_plan["outcome"] == "DISPATCH", "Commander without adapter regressed")
    require(
        legacy_plan["dispatches"][0]["package"]["context"]["project_rules"] == ["AGENTS.md"],
        "legacy default project rule changed",
    )

    print("Project Adapter schema, semantics, and Commander integration scenarios passed")


if __name__ == "__main__":
    main()
