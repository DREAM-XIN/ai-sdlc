#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

import yaml

from manual_dispatch import build_dispatches
from runtime_router import select_runtime
from validate_orchestrator_examples import base_manifest, load_profile

ROOT = Path(__file__).resolve().parents[1]


def load_policy():
    with (ROOT / "dispatch" / "default.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    policy = load_policy()

    routed = select_runtime({"stage": "design", "role": "architect"}, "medium", policy)
    require(routed["outcome"] == "ROUTED", f"design route failed: {routed}")
    require(routed["runtime"] == {"id": "chatgpt-web", "mode": "manual"}, f"unexpected runtime: {routed}")

    unknown = select_runtime({"stage": "mystery", "role": "unknown"}, "medium", policy)
    require(unknown["outcome"] == "INVALID", f"unknown route unexpectedly passed: {unknown}")

    ambiguous_policy = deepcopy(policy)
    ambiguous_policy["routes"].append(
        {
            "id": "conflicting-architect",
            "priority": 10,
            "match": {"role": "architect"},
            "runtime": {"id": "codex", "mode": "autonomous"},
        }
    )
    ambiguous = select_runtime({"stage": "design", "role": "architect"}, "medium", ambiguous_policy)
    require(ambiguous["outcome"] == "INVALID", f"ambiguous route unexpectedly passed: {ambiguous}")

    single = build_dispatches(
        base_manifest(),
        load_profile("standard-feature"),
        policy,
        "example-org/example-repo",
        manifest_ref="features/F-0030/manifest.yaml",
    )
    require(single["outcome"] == "DISPATCH", f"single dispatch failed: {single}")
    require(len(single["dispatches"]) == 1, f"expected one dispatch: {single}")
    one = single["dispatches"][0]
    require(one["action"]["stage"] == "design", f"wrong stage: {one}")
    require(one["task"]["role"] == "architect", f"wrong role: {one}")
    require(one["package"]["transport"] == {"runtime": "chatgpt-web", "mode": "manual"}, f"wrong transport: {one}")
    for fragment in (
        "Repository: example-org/example-repo",
        "Produce a technical design",
        "features/F-0030/manifest.yaml",
        "## Allowed scope",
        "## Definition of Done",
        "## Write back",
    ):
        require(fragment in one["prompt"], f"prompt missing {fragment!r}")

    parallel_profile = {
        "id": "parallel-dispatch-test",
        "version": "0.1.0",
        "risk_profile": "medium",
        "stages": [
            {"id": "requirement", "role": "product"},
            {"id": "design", "role": "architect", "depends_on": ["requirement"]},
            {"id": "implementation", "role": "developer", "depends_on": ["requirement"]},
        ],
    }
    parallel_manifest = {
        "protocol_version": "0.1.0",
        "feature": {"id": "F-PARALLEL", "title": "Parallel dispatch", "risk": "medium"},
        "workflow": {
            "profile": "parallel-dispatch-test",
            "status": "ACTIVE",
            "current_stage": "design",
            "stages": [
                {"id": "requirement", "status": "DONE"},
                {"id": "design", "status": "READY"},
                {"id": "implementation", "status": "READY"},
            ],
        },
        "updated_at": "2026-08-07T11:06:00Z",
    }
    parallel = build_dispatches(parallel_manifest, parallel_profile, policy, "example-org/example-repo")
    require(parallel["outcome"] == "DISPATCH", f"parallel dispatch failed: {parallel}")
    require(
        {item["action"]["stage"] for item in parallel["dispatches"]} == {"design", "implementation"},
        f"parallel dispatch stages wrong: {parallel}",
    )
    require(len({item["task"]["id"] for item in parallel["dispatches"]}) == 2, "parallel task ids are not independent")

    print("Runtime routing and ChatGPT Web manual dispatch scenarios passed")


if __name__ == "__main__":
    main()
