#!/usr/bin/env python3
"""Deterministic identity helpers for trusted gh-aw dispatches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

RETRYABLE_CONCLUSIONS = {"failure", "cancelled", "timed_out", "startup_failure"}


def dispatch_identity(plan_document: dict) -> dict:
    if plan_document.get("outcome") != "PLANNED" or "plan" not in plan_document:
        raise ValueError("dispatch identity requires a PLANNED gh-aw dispatch document")
    plan = plan_document["plan"]
    dispatches = plan.get("dispatches", [])
    if len(dispatches) != 1:
        raise ValueError("dispatch identity requires exactly one gh-aw dispatch")
    dispatch = dispatches[0]
    inputs = dispatch.get("inputs", {})
    material = {
        "version": "gh-aw-dispatch-key-v1",
        "repository": plan.get("repository"),
        "target_ref": plan.get("target_ref"),
        "feature_id": plan.get("feature_id"),
        "task_id": dispatch.get("task_id"),
        "stage": dispatch.get("stage"),
        "role": dispatch.get("role"),
        "work_kind": dispatch.get("work_kind", "stage"),
        "expected_revision": inputs.get("expected_revision"),
    }
    if not material["repository"] or not material["target_ref"] or not material["feature_id"] or not material["task_id"]:
        raise ValueError("dispatch identity is missing repository/ref/feature/task identity")
    if not isinstance(material["expected_revision"], int):
        raise ValueError("dispatch identity requires integer expected_revision")
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return {
        "dispatch_key": f"ghaw-v1-{digest}",
        "run_name": f"AI-SDLC gh-aw ghaw-v1-{digest}",
        "material": material,
    }


def _newest(runs: list[dict]) -> dict:
    return sorted(
        runs,
        key=lambda run: (run.get("created_at") or "", int(run.get("id") or 0)),
        reverse=True,
    )[0]


def should_suppress(existing_runs: list[dict], run_name: str) -> dict:
    matches = [run for run in existing_runs if run.get("display_title") == run_name]
    if not matches:
        return {"suppress": False, "reason": "no-existing-run", "run": None}

    # Success is terminal for one semantic work unit even if somebody later
    # manually creates a failed run with the same trusted key.
    successful = [run for run in matches if run.get("status") == "completed" and run.get("conclusion") == "success"]
    if successful:
        return {"suppress": True, "reason": "existing-success", "run": _newest(successful)}

    # Any queued/in-progress run owns the execution lease. Do not race it.
    active = [run for run in matches if run.get("status") != "completed"]
    if active:
        run = _newest(active)
        return {"suppress": True, "reason": f"existing-{run.get('status') or 'active'}", "run": run}

    latest = _newest(matches)
    conclusion = latest.get("conclusion")
    if conclusion in RETRYABLE_CONCLUSIONS:
        return {"suppress": False, "reason": f"retry-after-{conclusion}", "run": latest}

    # Unknown/neutral/skipped/stale completed states are fail-closed. A human
    # can inspect them instead of risking another implementation side effect.
    return {"suppress": True, "reason": f"existing-nonretryable-{conclusion or 'unknown'}", "run": latest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute or evaluate trusted gh-aw dispatch identity")
    subparsers = parser.add_subparsers(dest="command", required=True)
    key = subparsers.add_parser("key")
    key.add_argument("dispatch_plan", type=Path)
    key.add_argument("--json", action="store_true")
    check = subparsers.add_parser("check-runs")
    check.add_argument("runs", type=Path)
    check.add_argument("run_name")
    args = parser.parse_args()

    if args.command == "key":
        result = dispatch_identity(json.loads(args.dispatch_plan.read_text(encoding="utf-8")))
        print(json.dumps(result, sort_keys=True) if args.json else result["dispatch_key"])
        return

    raw = json.loads(args.runs.read_text(encoding="utf-8"))
    runs = raw.get("workflow_runs", raw if isinstance(raw, list) else [])
    print(json.dumps(should_suppress(runs, args.run_name), sort_keys=True))


if __name__ == "__main__":
    main()
