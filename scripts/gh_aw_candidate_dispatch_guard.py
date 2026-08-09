#!/usr/bin/env python3
"""Fail-closed candidate identity guard immediately before autonomous Gate dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


class DispatchGuardError(ValueError):
    pass


def load_gate_candidate(plan_path: Path):
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    if data.get("outcome") != "PLANNED":
        raise DispatchGuardError("dispatch plan must be PLANNED")
    plan = data.get("plan") or {}
    dispatches = plan.get("dispatches") or []
    if len(dispatches) != 1:
        raise DispatchGuardError("dispatch plan must contain exactly one dispatch")
    dispatch = dispatches[0]
    role = dispatch.get("role")
    stage = dispatch.get("stage")
    if (role, stage) not in {("reviewer", "code-review"), ("qa", "verification")}:
        return None
    inputs = dispatch.get("inputs") or {}
    pr_number = inputs.get("candidate_pr_number")
    head_sha = inputs.get("candidate_head_sha")
    repository = inputs.get("target_repository")
    if not isinstance(pr_number, int) or pr_number <= 0:
        raise DispatchGuardError("Gate dispatch is missing a valid candidate_pr_number")
    if not isinstance(head_sha, str) or len(head_sha) != 40 or any(c not in "0123456789abcdef" for c in head_sha):
        raise DispatchGuardError("Gate dispatch is missing a canonical candidate_head_sha")
    if repository != plan.get("repository"):
        raise DispatchGuardError("candidate repository differs from dispatch plan repository")
    return {"repository": repository, "pr_number": pr_number, "head_sha": head_sha, "role": role, "stage": stage}


def verify(plan_path: Path, current_head_sha: str | None):
    candidate = load_gate_candidate(plan_path)
    if candidate is None:
        return {"outcome": "NO_GATE_CANDIDATE"}
    if not isinstance(current_head_sha, str) or current_head_sha != candidate["head_sha"]:
        raise DispatchGuardError("candidate PR head moved after trusted planning; refusing stale Gate dispatch")
    return {"outcome": "VERIFIED", **candidate}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dispatch_plan", type=Path)
    parser.add_argument("--current-head-sha")
    args = parser.parse_args()
    try:
        result = verify(args.dispatch_plan, args.current_head_sha)
    except (OSError, json.JSONDecodeError, DispatchGuardError) as exc:
        print(json.dumps({"outcome": "INVALID", "error": str(exc)}, separators=(",", ":")))
        raise SystemExit(2)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
