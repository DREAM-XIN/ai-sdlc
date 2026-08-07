#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

from bootstrap_feature import load_profile
from commander import build_commander_plan, commander_bootstrap
from github_commander_transport import render_transport
from runtime_router import load_yaml
from validate_feature_transition import event
from commander import commander_ingest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def materialize(result):
    return yaml.safe_load(result["plan"]["manifest"]["content"])


def main():
    profile = load_profile("standard-feature")
    policy = load_yaml(ROOT / "dispatch" / "default.yaml")
    bootstrap = {
        "version": "0.1.0",
        "feature": {"id": "F-0045", "title": "GitHub Commander transport", "risk": "medium", "issue": "#45"},
        "profile": "standard-feature",
        "created_at": "2026-08-07T11:38:00Z",
    }
    created = commander_bootstrap(bootstrap, profile)
    require(created["outcome"] == "BOOTSTRAPPED", f"bootstrap failed: {created}")
    manifest = created["manifest"]

    manual_plan = build_commander_plan(manifest, profile, policy, repository="DREAM-XIN/ai-sdlc")
    rendered = render_transport(manual_plan)
    require(rendered["outcome"] == "RENDERED", f"manual transport render failed: {rendered}")
    require("Outcome: **DISPATCH**" in rendered["summary"], "manual summary lacks outcome")
    require("chatgpt-web/manual" in rendered["summary"], "manual summary lacks runtime")
    require("===== requirement / product =====" in rendered["prompts"], "manual prompt artifact missing")
    require("Repository: DREAM-XIN/ai-sdlc" in rendered["prompts"], "manual prompt lacks repository")

    future_policy = deepcopy(policy)
    future_policy["routes"].append(
        {
            "id": "gh-aw-requirement",
            "priority": 100,
            "match": {"stage": "requirement", "role": "product"},
            "runtime": {"id": "gh-aw", "mode": "autonomous"},
        }
    )
    future_plan = build_commander_plan(manifest, profile, future_policy, repository="DREAM-XIN/ai-sdlc")
    future_rendered = render_transport(future_plan)
    require(future_rendered["outcome"] == "RENDERED", f"future render failed: {future_rendered}")
    require("gh-aw/autonomous" in future_rendered["summary"], "future runtime missing from summary")
    require("delegated to its runtime adapter" in future_rendered["summary"], "future adapter boundary missing")
    require(future_rendered["prompts"] == "", "future runtime unexpectedly produced ChatGPT prompt")

    start = event(
        "F-0045",
        [{"kind": "stage", "id": "requirement", "status": "WORKING"}],
        "2026-08-07T11:39:00Z",
        event_id="EVT-F0045-REQ-START",
    )
    start_result = commander_ingest(
        manifest,
        start,
        event_path="state/events/F-0045/EVT-F0045-REQ-START.yaml",
        repository="DREAM-XIN/ai-sdlc",
        manifest_path="state/features/F-0045.yaml",
        target_ref="feature/F-0045",
    )
    waiting_manifest = materialize(start_result)
    wait_plan = build_commander_plan(waiting_manifest, profile, policy, repository="DREAM-XIN/ai-sdlc")
    wait_rendered = render_transport(wait_plan)
    require("Outcome: **WAIT**" in wait_rendered["summary"], "WAIT summary missing")
    require("No runtime dispatch is available" in wait_rendered["summary"], "WAIT summary claims dispatch")
    require(wait_rendered["prompts"] == "", "WAIT state unexpectedly produced prompts")

    print("GitHub-native Commander transport scenarios passed")


if __name__ == "__main__":
    main()
