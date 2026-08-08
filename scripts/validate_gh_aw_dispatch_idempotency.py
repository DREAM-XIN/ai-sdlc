#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path

from gh_aw_dispatch_identity import dispatch_identity, should_suppress

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value:
        raise AssertionError(message)


def planned(*, repository="DREAM-XIN/example-target", target_ref="feature/F-IDEMP", task_id="F-IDEMP-IMPLEMENTATION", expected_revision=10):
    return {
        "outcome": "PLANNED",
        "plan": {
            "repository": repository,
            "target_ref": target_ref,
            "feature_id": "F-IDEMP",
            "revision": expected_revision,
            "dispatches": [{
                "task_id": task_id,
                "stage": "implementation",
                "role": "developer",
                "work_kind": "stage",
                "inputs": {"expected_revision": expected_revision},
            }],
        },
    }


def main():
    base = planned()
    identity = dispatch_identity(base)
    require(identity["dispatch_key"].startswith("ghaw-v1-"), "dispatch key version prefix missing")
    require(identity["run_name"] == f"AI-SDLC gh-aw {identity['dispatch_key']}", "worker run-name is not derived from dispatch key")
    require(dispatch_identity(deepcopy(base))["dispatch_key"] == identity["dispatch_key"], "dispatch key is not deterministic")

    # Fresh START at source revision N expects worker revision N+1. A later
    # WORKING adoption sees N+1 as current revision, so source revision must not
    # affect the semantic execution identity.
    fresh_source_revision = deepcopy(base)
    fresh_source_revision["plan"]["revision"] = 9
    require(dispatch_identity(fresh_source_revision)["dispatch_key"] == identity["dispatch_key"], "source revision leaked into semantic key; fresh->WORKING adoption would drift")

    changed = planned(expected_revision=11)
    require(dispatch_identity(changed)["dispatch_key"] != identity["dispatch_key"], "expected revision must change dispatch key")
    changed = planned(repository="DREAM-XIN/other-target")
    require(dispatch_identity(changed)["dispatch_key"] != identity["dispatch_key"], "repository identity must change dispatch key")
    changed = planned(task_id="F-IDEMP-REMEDIATION-1")
    require(dispatch_identity(changed)["dispatch_key"] != identity["dispatch_key"], "work-unit identity must change dispatch key")

    run_name = identity["run_name"]
    active = [{"id": 10, "display_title": run_name, "status": "in_progress", "conclusion": None, "created_at": "2026-08-08T00:00:00Z", "html_url": "https://example/run/10"}]
    require(should_suppress(active, run_name)["suppress"], "in-progress semantic duplicate must be suppressed")
    queued = [{"id": 11, "display_title": run_name, "status": "queued", "conclusion": None, "created_at": "2026-08-08T00:00:01Z"}]
    require(should_suppress(queued, run_name)["suppress"], "queued semantic duplicate must be suppressed")
    succeeded = [{"id": 12, "display_title": run_name, "status": "completed", "conclusion": "success", "created_at": "2026-08-08T00:00:02Z"}]
    require(should_suppress(succeeded, run_name)["suppress"], "successful semantic duplicate must be suppressed")
    failed = [{"id": 13, "display_title": run_name, "status": "completed", "conclusion": "failure", "created_at": "2026-08-08T00:00:03Z"}]
    require(not should_suppress(failed, run_name)["suppress"], "failed worker must remain retryable")
    cancelled = [{"id": 14, "display_title": run_name, "status": "completed", "conclusion": "cancelled", "created_at": "2026-08-08T00:00:04Z"}]
    require(not should_suppress(cancelled, run_name)["suppress"], "cancelled worker must remain retryable")
    mixed = succeeded + [{"id": 15, "display_title": run_name, "status": "completed", "conclusion": "failure", "created_at": "2026-08-08T00:00:05Z"}]
    decision = should_suppress(mixed, run_name)
    require(decision["suppress"] and decision["reason"] == "existing-success", "an older success must remain terminal even after a newer manual failure")
    neutral = [{"id": 16, "display_title": run_name, "status": "completed", "conclusion": "neutral", "created_at": "2026-08-08T00:00:06Z"}]
    require(should_suppress(neutral, run_name)["suppress"], "ambiguous completed worker states must fail closed")

    gateway = (ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-dispatch.yml").read_text(encoding="utf-8")
    profile = (ROOT / ".github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml").read_text(encoding="utf-8")
    worker = (ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md").read_text(encoding="utf-8")
    command = (ROOT / "templates/github/ai-sdlc-command.yml").read_text(encoding="utf-8")

    require("concurrency:" in gateway and "cancel-in-progress: false" in gateway, "cross-repo gateway must serialize without cancelling the active handoff")
    require("gh_aw_dispatch_identity.py key" in gateway and "gh_aw_dispatch_identity.py check-runs" in gateway, "gateway does not use trusted semantic dispatch identity")
    require("dispatch_key=$dispatch_key" in gateway, "gateway dispatch key output missing")
    require("dispatch_key={os.environ['DISPATCH_KEY']}" in gateway, "gateway does not pass trusted dispatch key to worker")
    require("Resolve exact worker run lease" in gateway and "display_title" in gateway, "gateway does not resolve exact worker lease before completion")
    require("permission-contents: write" in gateway, "trusted persistence write token disappeared")
    require("permission-pull-requests: write" not in gateway and "permission-actions: write" not in gateway, "dedupe hardening broadened target App token permissions")

    require('run-name: "AI-SDLC gh-aw ${{ inputs.dispatch_key' in worker, "worker lacks deterministic run-name")
    require("dispatch_key:" in worker and "required: false" in worker, "same-repository compatibility fallback for dispatch key missing")
    require("permissions: read-all" in worker, "agent permission boundary changed")

    require('run-name: "AI-SDLC gh-aw profile ${{ inputs.request_id' in profile, "profile gateway lacks request-id run-name")
    require('--field request_id="$REQUEST_ID"' in profile, "profile gateway does not forward request correlation")

    require('request_id="cmd-${REPOSITORY_ID}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in command, "command bridge does not mint request id from trusted run identity")
    require('--field request_id="$request_id"' in command, "command bridge does not send trusted request id")
    require("display_title') == expected" in command, "autonomous receipt still lacks exact display-title correlation")
    autonomous_parser = command.split("gh_aw =", 1)[1].split("bootstrap_path", 1)[0]
    for forbidden in ("provider", "model", "engine_profile", "worker_workflow", "policy"):
        require(forbidden not in autonomous_parser, f"target autonomous command parser exposes forbidden selector {forbidden}")

    print("gh-aw deterministic dispatch key, retry semantics, cross-repo serialization, exact worker lease, receipt correlation, and permission checks passed")


if __name__ == "__main__":
    main()
