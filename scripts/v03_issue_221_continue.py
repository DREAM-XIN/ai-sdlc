#!/usr/bin/env python3
"""Resume exact-main #221 producers without treating cached runs as release PASS."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from v03_effect_safety_final_live_ledger import producer_plan

class ContinueError(RuntimeError):
    pass

@dataclass(frozen=True)
class Step:
    workflow: str
    scenario: str = ""
    artifact: str = ""

BOOTSTRAP = (
    Step("materialize-v03-vertical-policy-state.yml"),
    Step("provision-v03-real-runtime-fixture.yml"),
    Step("provision-v03-scenario-fixture-pool.yml"),
    Step("v03-real-runtime-preflight-live.yml"),
)

def live_steps():
    return tuple(
        Step(spec.workflow_file, spec.scenarios[0] if len(spec.scenarios) == 1 else "", spec.artifact_name)
        for spec in producer_plan()
    )

def selected_steps(mode, scenario):
    steps = live_steps()
    if mode == "resume":
        if scenario:
            raise ContinueError("resume mode does not accept a scenario filter")
        return BOOTSTRAP + steps + (Step("v03-final-live-ledger.yml"),)
    if mode in {"scenario", "scenario-first"}:
        matches = tuple(step for step in steps if step.scenario == scenario)
        if len(matches) != 1:
            raise ContinueError("scenario mode requires one allowlisted singleton scenario")
        if mode == "scenario-first":
            return BOOTSTRAP + matches + tuple(s for s in steps if s != matches[0]) + (Step("v03-final-live-ledger.yml"),)
        return BOOTSTRAP + matches
    raise ContinueError("unknown continuation mode")

def positive_id(value):
    return type(value) is int and value > 0

def choose_reuse(step, sha, runs, list_artifacts):
    """Scheduling hint only. The unchanged final ledger independently verifies bytes."""
    exact = [
        run for run in runs
        if run.get("head_sha") == sha and run.get("head_branch") == "main"
        and run.get("event") == "workflow_dispatch" and positive_id(run.get("id"))
    ]
    if any(run.get("status") != "completed" for run in exact):
        raise ContinueError("an exact-main producer is still active; do not dispatch concurrently")
    successes = []
    blocked = False
    for run in exact:
        if run.get("status") != "completed":
            blocked = True
            continue
        if step.artifact:
            artifacts = list_artifacts(run["id"])
            named = [a for a in artifacts if a.get("name") == step.artifact]
            valid = [a for a in named if a.get("expired") is False and positive_id(a.get("id"))]
            if len(named) > 1:
                raise ContinueError("ambiguous artifact identity")
            if run.get("conclusion") == "success" and valid:
                successes.append(run["id"])
            elif named:
                blocked = True
            elif run.get("conclusion") != "success":
                # Setup can fail before a scenario-specific artifact is uploaded.
                # Do not guess whether an external launch or Store write happened.
                blocked = True
        elif run.get("conclusion") == "success":
            successes.append(run["id"])
        else:
            blocked = True
    if step.artifact and len(successes) > 1:
        raise ContinueError("multiple successful records would make the final ledger ambiguous")
    if successes:
        return max(successes)
    if blocked:
        raise ContinueError("existing active/failed/expired run requires recovery inspection; no automatic redispatch")
    # A completed successful run with no usable artifact may already have consumed
    # its fixture. Fail closed instead of silently launching it again.
    if step.artifact and any(r.get("conclusion") == "success" for r in exact):
        # Other scenario successes on the same workflow are normal. Only a missing
        # artifact from a success cannot identify its input; manual inspection is needed.
        # An artifact for another known scenario proves that this run is unrelated.
        known = {s.artifact for s in live_steps() if s.workflow == step.workflow}
        for run in exact:
            if run.get("conclusion") == "success":
                names = {a.get("name") for a in list_artifacts(run["id"])}
                if not names.intersection(known):
                    raise ContinueError("successful run has missing scenario artifacts; inspect before redispatch")
    return None

def gh(*args):
    result = subprocess.run(["gh", *args], text=True, capture_output=True)
    if result.returncode:
        raise ContinueError("GitHub command failed: " + " ".join(args[:2]))
    return result.stdout

def api(path):
    return json.loads(gh("api", path))

def pages(path, key):
    rows = []
    for page in range(1, 11):
        data = api(path + f"&per_page=100&page={page}")
        batch = data.get(key)
        if not isinstance(batch, list) or any(not isinstance(r, dict) for r in batch):
            raise ContinueError("malformed GitHub listing")
        rows.extend(batch)
        if len(rows) >= int(data.get("total_count", 0)):
            return rows
        if not batch:
            raise ContinueError("incomplete GitHub pagination")
    raise ContinueError("GitHub listing exceeds bounded 1000-record search")

def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    if repo.lower() != "dream-xin/ai-sdlc" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ContinueError("continuation requires the trusted repository and main")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ContinueError("invalid installation SHA")
    mode = os.environ.get("CONTINUE_MODE") or "resume"
    scenario = os.environ.get("CONTINUE_SCENARIO") or ""
    steps = selected_steps(mode, scenario)
    prefix = f"repos/{repo}"

    def guard():
        if api(prefix + "/git/ref/heads/main")["object"]["sha"] != sha:
            raise ContinueError("main changed; old-version records cannot be reused")
    def runs(step):
        return pages(prefix + f"/actions/workflows/{step.workflow}/runs?event=workflow_dispatch&branch=main&head_sha={sha}", "workflow_runs")
    def artifacts(run_id):
        return pages(prefix + f"/actions/runs/{run_id}/artifacts?", "artifacts")
    def note(message):
        print(message, flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(message + "\n\n")

    guard()
    if gh("api", prefix + f"/git/commits/{sha}", "--jq", ".sha").strip() != sha:
        raise ContinueError("installation commit cannot be resolved")
    note(f"Mode: {mode}; installation: {sha}. Reused runs are scheduling candidates, not release PASS.")
    for step in steps:
        guard()
        existing = choose_reuse(step, sha, runs(step), artifacts)
        label = step.scenario or step.workflow
        if existing:
            note(f"Reuse {label}: https://github.com/{repo}/actions/runs/{existing}")
            continue
        # Resolve new runs by set difference, never by an ambiguous timestamp.
        before = {r["id"] for r in runs(step)}
        args = ["workflow", "run", step.workflow, "--repo", repo, "--ref", "main"]
        if step.scenario:
            args.extend(["--field", f"scenario={step.scenario}"])
        guard()
        gh(*args)
        import time
        candidates = []
        for _ in range(30):
            guard()
            candidates = [r for r in runs(step) if r["id"] not in before]
            if candidates:
                break
            time.sleep(2)
        if len(candidates) != 1:
            raise ContinueError("new downstream run is missing or ambiguous; no redispatch")
        run_id = candidates[0]["id"]
        note(f"Run {label}: https://github.com/{repo}/actions/runs/{run_id}")
        # Stream progress; a failed watch never triggers an automatic live retry.
        result = subprocess.run(["gh", "run", "watch", str(run_id), "--repo", repo, "--exit-status"])
        guard()
        if result.returncode:
            raise ContinueError(f"downstream run {run_id} failed; inspect recovery before retry")
        chosen = choose_reuse(step, sha, runs(step), artifacts)
        if chosen != run_id:
            raise ContinueError("new run did not produce a unique reusable record")
    note("Selected producers completed. Scenario mode does not claim overall #221 PASS.")

if __name__ == "__main__":
    try:
        main()
    except ContinueError as exc:
        raise SystemExit(str(exc))
