#!/usr/bin/env python3
"""Trusted-main driver for one frozen v0.3 real release dogfood scenario."""
from __future__ import annotations

import argparse
import base64
import hashlib
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from operator_external_create_attempt import external_create_attempt_path, find_external_create_attempt
from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_store import plan_launch_lookup, query_unfinished
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import StoreMutation, StoreMutationPlan, digest_json, operation_events, rebuild_projection, reservation_path
from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_recovery import plan_vertical_takeover
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_live_gate import ALLOWED_SCENARIOS, assemble_dogfood_live_gate
from v03_dogfood_openai_host import V03DogfoodOpenAIHostConfig, V03DogfoodOpenAIResponsesHost
from v03_dogfood_runtime_preflight import build_v03_dogfood_runtime_preflight
from v03_dogfood_scenario_runner import (
    V03DogfoodScenarioRunnerError, _wait_current_dispatch, run_scenario,
)
from v03_real_runtime_live_authority import load_live_authority, require_trusted_main_execution

VALIDATE_ONLY = "validate-only"
PREFLIGHT_ONLY = "preflight-only"
RUN = "run"
MODES = frozenset({VALIDATE_ONLY, PREFLIGHT_ONLY, RUN})
DOGFOOD_RESPONSES_PROVIDER = "deepseek"
DOGFOOD_RESPONSES_API_BASE = "https://api.deepseek.com"
DOGFOOD_RESPONSES_MODEL = "deepseek-flash"


HISTORICAL_PREHTTP_RECOVERY = {
    "scenario": "happy_path",
    "run_id": 37089196141,
    "workflow_id": 342691463,
    "installation_commit_sha": "ca74b11b360516c5b881126c7183aaf9d67d83d8",
    "composition_blob_sha": "c0f4f7eb9eab2fefa99849bb9fe599167a43ad5c",
    "operation_id": "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4",
    "generation": 1,
    "feature_id": "F-OPERATOR-V03-DOGFOOD-HAPPY-0001",
    "candidate_pr_number": 552,
    "candidate_head_sha": "70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23",
    "semantic_effect_key": "80b31137a408f2b0ee85248bd069b972af80b9777f91f2bd00c9b27b42f9e804",
    "external_dispatch_key": "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1",
    "attempt_id": "eca-1f340ab49f04ab7b0747907075aead41b8fa6f23",
    "claim_id": "dc-eed43eeddee6ae756e0f538621f2593fde10a828",
    "dispatch_id": "vertical-31df3f1ed41b54c58ed4c4030a9f97d9",
    "selected_event_id": "loop-step-selected-d13700446f0139fb96e5717dc101bbdf",
    "claim_event_id": "dispatch-claimed-f55e33e1ea7fd6b35eb44831e700d91f",
    "authorization_event_id": "dispatch-launch-authorized-8ea8faac43fb02dc1c3c8e481a40da93",
    "lookup_event_id": "dispatch-launch-lookup-recorded-ef426f7c675283149f805fdab65861ec",
    "trusted_context_digest": "fbdb342609142209114f3ed10db8d6830ca16ff50aa48c7530f7e8fdd373e325",
    "last_sequence": 11,
    "task_identity": "vertical:implementation:1",
    "task_id": "vertical:implementation:1",
    "role": "developer",
    "stage": "implementation",
    "workflow_file": "ai-sdlc-gh-aw-worker.lock.yml",
    "target_ref": "dogfood/v0.3-happy-path-0001",
}
HISTORICAL_PREHTTP_CORE_BLOBS = {
    "scripts/operator_vertical_gh_aw_actions_transport.py": "ec9ea44f81cda1a052cd984cd345f1572c4903e7",
    "scripts/operator_external_create_gateway.py": "9176be1219359719288ae5b44df87ef32ee4bac2",
    "scripts/operator_vertical_executor.py": "93639d2a299f1b43cd3636ff2ecd7408d6ced3ac",
    "scripts/operator_vertical_gh_aw.py": "8f0181f31d17d7a81c831b19a68bf63c83a72392",
    "scripts/operator_vertical_runtime.py": "fe3b5090cdb6c0189f8b6ad854763480af6d6d12",
    "scripts/operator_v03_write_runtime.py": "bbe929eaafbe36411068b8738b0b9c209e95db3c",
    "scripts/operator_openai_responses_production.py": "eea851224ac3e3a4c2e823e9845c3c0682aa0417",
}
PREHTTP_RECOVERY_MARKER_SCHEMA = "ai-sdlc.v03-dogfood-prehttp-recovery-attempt/v1"
PREHTTP_RECOVERY_MARKER_PATH = (
    "state/operator/v1/operations/"
    + HISTORICAL_PREHTTP_RECOVERY["operation_id"]
    + "/dogfood-prehttp-recovery-attempt.json"
)


class V03DogfoodRuntimeDriverError(RuntimeError):
    pass



# This is a review input, never a launch/invalidation capability.
HISTORICAL_FAILED_WORKER = {
    "run_id": 37204777409,
    "workflow_id": 329456712,
    "head_sha": "5ed049a4cce9c39a42385337da35a46dcaf378eb",
    "workflow_blob_sha": "e618477192ef8cde47cf36a9b162aee1069f3eef",
    "jobs": {
        "activation": (111443581703, "success"),
        "agent": (111443640237, "failure"),
        "detection": (111443685002, "success"),
        "safe_outputs": (111443824035, "failure"),
        "conclusion": (111443899606, "failure"),
    },
}


def collect_historical_worker_recovery_evidence(*, read_json, read_bytes) -> dict[str, Any]:
    """Bracket immutable first-attempt facts from GitHub; grant no recovery."""
    h, w = HISTORICAL_PREHTTP_RECOVERY, HISTORICAL_FAILED_WORKER
    run_path = f"/actions/runs/{w['run_id']}"
    before = read_json(run_path)
    identity = {
        "id": w["run_id"], "workflow_id": w["workflow_id"],
        "event": "workflow_dispatch", "head_branch": "main",
        "head_sha": w["head_sha"],
        "path": ".github/workflows/" + h["workflow_file"],
        "display_title": "AI-SDLC gh-aw " + h["external_dispatch_key"],
        "run_attempt": 1, "status": "completed", "conclusion": "failure",
    }

    def require_run(run):
        if (not isinstance(run, dict)
                or any(type(run.get(k)) is not int for k in ("id", "workflow_id", "run_attempt"))
                or any(run.get(k) != v for k, v in identity.items())):
            raise V03DogfoodRuntimeDriverError("historical failed Worker identity/attempt/status drifted")
        repository = run.get("repository") or {}
        if str(repository.get("full_name") or "").lower() != "dream-xin/ai-sdlc":
            raise V03DogfoodRuntimeDriverError("historical failed Worker repository drifted")
        if not run.get("updated_at"):
            raise V03DogfoodRuntimeDriverError("historical failed Worker lacks update identity")

    require_run(before)
    source_path = "/contents/.github/workflows/" + h["workflow_file"] + "?ref=" + w["head_sha"]
    source = read_json(source_path)
    if not isinstance(source, dict) or (
        source.get("sha"), source.get("encoding"), source.get("path"), source.get("type")
    ) != (w["workflow_blob_sha"], "base64", ".github/workflows/" + h["workflow_file"], "file"):
        raise V03DogfoodRuntimeDriverError("historical failed Worker source binding drifted")
    try:
        raw = base64.b64decode("".join(str(source["content"]).split()), validate=True)
    except (KeyError, ValueError) as exc:
        raise V03DogfoodRuntimeDriverError("historical failed Worker source is malformed") from exc
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + bytes([0]) + raw).hexdigest()
    if blob != w["workflow_blob_sha"]:
        raise V03DogfoodRuntimeDriverError("historical failed Worker source bytes drifted")

    jobs_path = run_path + "/attempts/1/jobs?per_page=100"

    def require_jobs(payload):
        rows = payload.get("jobs") if isinstance(payload, dict) else None
        if (not isinstance(rows, list) or type(payload.get("total_count")) is not int
                or payload["total_count"] != len(w["jobs"]) or len(rows) != len(w["jobs"])):
            raise V03DogfoodRuntimeDriverError("historical first-attempt jobs are incomplete")
        by_name = {}
        for job in rows:
            if (not isinstance(job, dict) or not isinstance(job.get("name"), str)
                    or job.get("name") in by_name):
                raise V03DogfoodRuntimeDriverError("historical first-attempt jobs are ambiguous")
            name = job.get("name")
            expected = w["jobs"].get(name)
            if (expected is None
                    or any(type(job.get(k)) is not int for k in ("id", "run_id", "run_attempt"))
                    or (
                job.get("id"), job.get("conclusion"), job.get("status"),
                job.get("run_id"), job.get("run_attempt"), job.get("head_sha")
            ) != (expected[0], expected[1], "completed", w["run_id"], 1, w["head_sha"])):
                raise V03DogfoodRuntimeDriverError("historical first-attempt job identity drifted")
            steps = job.get("steps")
            if (not isinstance(steps, list) or not steps
                    or any(not isinstance(s, dict) or s.get("status") != "completed"
                           or type(s.get("number")) is not int for s in steps)
                    or [s["number"] for s in steps] != sorted({s["number"] for s in steps})):
                raise V03DogfoodRuntimeDriverError("historical first-attempt steps are incomplete")
            by_name[name] = job
        return by_name

    jobs_first = read_json(jobs_path)
    jobs = require_jobs(jobs_first)

    def require_step(job, name, conclusion):
        matches = [s for s in jobs[job]["steps"] if s.get("name") == name]
        if len(matches) != 1 or matches[0].get("conclusion") != conclusion:
            raise V03DogfoodRuntimeDriverError("historical execution boundary drifted: " + job + "/" + name)
        return matches[0]["number"]

    failed = require_step("agent", "Generate GitHub App token for checkout (0)", "failure")
    checkout = require_step("agent", "Checkout repository", "skipped")
    model = require_step("agent", "Execute GitHub Copilot CLI", "skipped")
    if not failed < checkout < model:
        raise V03DogfoodRuntimeDriverError("historical pre-model failure ordering drifted")
    require_step("agent", "Generate GitHub App token", "skipped")
    require_step("detection", "Execute threat detection with AWF", "skipped")
    safe_token = require_step("safe_outputs", "Generate GitHub App token", "failure")
    safe_outputs = require_step("safe_outputs", "Process Safe Outputs", "skipped")
    if not safe_token < safe_outputs:
        raise V03DogfoodRuntimeDriverError("historical safe-output failure ordering drifted")
    require_step("conclusion", "Dispatch structured worker result after Draft PR", "failure")

    logs = read_bytes(f"/actions/jobs/{w['jobs']['conclusion'][0]}/logs")
    if not isinstance(logs, bytes) or not logs:
        raise V03DogfoodRuntimeDriverError("historical conclusion log is unavailable")
    from operator_vertical_gh_aw_github_source import TargetScopedGitHubActionsGhAwResultSource
    values = TargetScopedGitHubActionsGhAwResultSource._log_env(logs)
    expected_env = {
        "FEATURE_ID": h["feature_id"], "EXPECTED_REVISION": "1",
        "TARGET_REF": h["target_ref"], "TARGET_REPOSITORY": "dream-xin/ai-sdlc",
    }
    for key, value in expected_env.items():
        if tuple(dict.fromkeys(values.get(key, ()))) != (value,):
            raise V03DogfoodRuntimeDriverError("historical conclusion input binding drifted: " + key)
    if not values.get("PR_URL") or any(value != "" for value in values["PR_URL"]):
        raise V03DogfoodRuntimeDriverError("historical conclusion does not prove empty Draft PR input")

    jobs_second = read_json(jobs_path)
    require_jobs(jobs_second)
    after = read_json(run_path)
    require_run(after)
    if before != after or jobs_first != jobs_second:
        raise V03DogfoodRuntimeDriverError("historical evidence changed during read-only collection")

    material = {
        "schema_version": "ai-sdlc.v03-dogfood-historical-worker-observation/v1",
        "repository": "dream-xin/ai-sdlc",
        "operation_id": h["operation_id"], "operation_generation": h["generation"],
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "runtime_receipt_identity": str(w["run_id"]),
        "run_identity": identity, "run_updated_at": after["updated_at"],
        "workflow_blob_sha": blob,
        "jobs_digest": digest_json(jobs_second),
        "conclusion_log_sha256": hashlib.sha256(logs).hexdigest(),
        "agent_model_step": "skipped", "safe_output_processing_step": "skipped",
        "callback_pr_url_empty": True, "stable_reads": True,
        "provider_invalidation": False, "future_attempts_fenced": False,
        "recovery_authority": False, "release_eligible": False,
        "remaining_requirements": [
            "trusted-proof-preventing-any-later-old-execution-effect",
            "independent-admission-of-a-bounded-recovery-contract",
            "protected-cas-authorization-before-any-successor-execution",
            "deepseek-capable-independent-developer-reviewer-qa-bindings",
        ],
    }
    return {**material, "observation_digest": "sha256:" + digest_json(material)}



def observe_historical_worker_for_review(*, actions_read_token: str) -> dict[str, Any]:
    """Use only the existing read-only, credential-safe GitHub result reader."""
    if not isinstance(actions_read_token, str) or not actions_read_token:
        raise V03DogfoodRuntimeDriverError("historical observation requires an Actions read token")
    from operator_vertical_gh_aw_github_source import (
        GitHubActionsGhAwResultSourceConfig, TargetScopedGitHubActionsGhAwResultSource,
    )
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow=HISTORICAL_PREHTTP_RECOVERY["workflow_file"],
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    repository = "dream-xin/ai-sdlc"
    source = TargetScopedGitHubActionsGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(
            control_repository=repository, control_token=actions_read_token,
            target_token=actions_read_token, workflows=workflows,
            collector_identity="v03-historical-worker-observation-reader",
        ),
        target_repository=repository,
    )
    return collect_historical_worker_recovery_evidence(
        read_json=lambda suffix: source._json(repository, suffix, actions_read_token),
        read_bytes=lambda suffix: source._bytes(repository, suffix, actions_read_token),
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise V03DogfoodRuntimeDriverError(f"missing trusted dogfood configuration: {name}")
    return value


def dogfood_responses_host_config(env: Mapping[str, str]) -> V03DogfoodOpenAIHostConfig:
    """Resolve the fixed pre-effect provider transport for v0.3 dogfood."""
    return V03DogfoodOpenAIHostConfig(
        api_key=_required(env, "AI_SDLC_DEEPSEEK_API_KEY"),
        model=DOGFOOD_RESPONSES_MODEL,
        api_base=DOGFOOD_RESPONSES_API_BASE,
        continuation_mode="full_history",
    )


def _head() -> str:
    completed = subprocess.run(["git", "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0:
        raise V03DogfoodRuntimeDriverError("cannot resolve exact dogfood checkout HEAD")
    return completed.stdout.strip().lower()


def _clock() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_mode(*, mode: str, scenario: str, event_name: str, ref: str) -> tuple[str, str]:
    if mode not in MODES:
        raise V03DogfoodRuntimeDriverError("unsupported dogfood runtime mode")
    if scenario not in ALLOWED_SCENARIOS:
        raise V03DogfoodRuntimeDriverError("scenario escaped frozen dogfood inventory")
    if mode == VALIDATE_ONLY:
        if event_name == "workflow_dispatch":
            raise V03DogfoodRuntimeDriverError("workflow_dispatch may not masquerade as validate-only")
        return mode, scenario
    if event_name != "workflow_dispatch" or ref != "refs/heads/main":
        raise V03DogfoodRuntimeDriverError("dogfood preflight/run is authorized only by workflow_dispatch on main")
    return mode, scenario


@contextmanager
def store_git_transport(*, token: str, repository: str, repo_path: Path = Path(".")):
    """Limit Git authentication to this process and the installed origin URL."""
    if not token or repository.lower() != "dream-xin/ai-sdlc":
        raise V03DogfoodRuntimeDriverError("Store Git transport lacks fixed repository/App authority")
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=repo_path,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    origin = result.stdout.strip()
    canonical = origin.lower().removesuffix(".git")
    if result.returncode or canonical != "https://github.com/" + repository.lower():
        raise V03DogfoodRuntimeDriverError("Store Git origin differs from installed repository")
    if os.environ.get("GIT_CONFIG_COUNT") not in {None, "0"}:
        raise V03DogfoodRuntimeDriverError("unexpected inherited Git transport configuration")
    encoded = base64.b64encode(("x-access-token:" + token).encode()).decode("ascii")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::add-mask::" + encoded, flush=True)
    transient = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http." + origin + ".extraheader",
        "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + encoded,
        "GIT_TERMINAL_PROMPT": "0",
    }
    previous = {key: os.environ.get(key) for key in transient}
    try:
        os.environ.update(transient)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def assemble_preflight(*, scenario: str, env: Mapping[str, str], checkout_sha: str):
    require_mode(
        mode=PREFLIGHT_ONLY,
        scenario=scenario,
        event_name=_required(env, "GITHUB_EVENT_NAME"),
        ref=_required(env, "GITHUB_REF"),
    )
    execution = require_trusted_main_execution(
        event_name=env["GITHUB_EVENT_NAME"],
        ref=env["GITHUB_REF"],
        repository=_required(env, "GITHUB_REPOSITORY"),
        workflow_sha=_required(env, "GITHUB_SHA"),
        checkout_sha=checkout_sha,
    )
    gate = assemble_dogfood_live_gate(scenario=scenario, env=env, checkout_sha=checkout_sha)
    admin_token = _required(env, "AI_SDLC_OPERATOR_ADMIN_TOKEN")
    app_slug = _required(env, "AI_SDLC_OPERATOR_APP_SLUG")
    app_id_raw = _required(env, "AI_SDLC_OPERATOR_APP_INTEGRATION_ID")
    if not app_id_raw.isdigit() or int(app_id_raw) < 1:
        raise V03DogfoodRuntimeDriverError("AI_SDLC_OPERATOR_APP_INTEGRATION_ID must be a positive integer")
    api_base = _required(env, "GITHUB_API_URL")
    live = load_live_authority(
        execution=execution,
        admin_token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=int(app_id_raw),
        api_base=api_base,
    )
    protection = GitHubRepositoryProtectionVerifier(
        token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=int(app_id_raw),
        api_base=api_base,
    )
    actions_token = _required(env, "AI_SDLC_ACTIONS_READ_TOKEN")
    event_write_token = _required(env, "AI_SDLC_EVENT_WRITE_TOKEN")
    return build_v03_dogfood_runtime_preflight(
        execution=execution,
        live_authority=live,
        live_gate=gate,
        slot=require_slot(scenario),
        protection_verifier=protection,
        adapter_id=OPENAI_RESPONSES_ADAPTER_ID,
        target_read_token=actions_token,
        actions_token=actions_token,
        event_write_token=event_write_token,
        clock=_clock,
        store_checkout=Path(str(env.get("AI_SDLC_STORE_CHECKOUT") or ".")).resolve(),
        github_api_base=api_base,
    )



def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    ).hexdigest()


def _github_json(preflight: Any, suffix: str) -> dict[str, Any]:
    transport = preflight.composition.actions_transport
    status, _, body = transport.http(
        method="GET",
        url=transport._api(suffix),
        token=transport.config.token,
        body=None,
    )
    if status != 200:
        raise V03DogfoodRuntimeDriverError(
            f"historical pre-HTTP proof GET failed with HTTP {status}"
        )
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP proof returned malformed JSON"
        ) from exc
    if not isinstance(value, dict):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP proof returned a non-object"
        )
    return value


def _historical_attempt_identity(
    snapshot: Any,
    preflight: Any,
    *,
    require_unknown: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    h = HISTORICAL_PREHTTP_RECOVERY
    if preflight.slot.scenario != h["scenario"] or preflight.slot.feature_id != h["feature_id"]:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery escaped the fixed happy_path slot"
        )
    if preflight.slot.target_ref != h["target_ref"]:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery target ref drifted"
        )
    projection = rebuild_projection(snapshot, h["operation_id"])
    if (
        int(projection.get("generation", -1)) != h["generation"]
        or projection.get("operation_profile") != VERTICAL_PROFILE
        or int(projection.get("expected_feature_revision", -1)) != 1
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery operation identity drifted"
        )
    if h["external_dispatch_key"] not in set(projection.get("authorized_dispatches") or ()):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery lost durable launch authorization"
        )
    if require_unknown and (
        projection.get("status") != "BLOCKED"
        or set(projection.get("unresolved_unknown") or ()) != {h["external_dispatch_key"]}
        or int(projection.get("last_sequence", -1)) != h["last_sequence"]
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery is not at the exact unresolved UNKNOWN boundary"
        )

    reservation = snapshot.get(reservation_path(h["semantic_effect_key"]))
    if not isinstance(reservation, dict):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery lacks the exact reservation"
        )
    reservation_expected = {
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "feature_id": h["feature_id"],
        "expected_revision": 1,
        "current_stage": h["stage"],
        "role": h["role"],
        "candidate_head_sha": h["candidate_head_sha"],
        "task_identity": h["task_identity"],
    }
    if any(reservation.get(key) != value for key, value in reservation_expected.items()):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery reservation identity drifted"
        )

    attempt = find_external_create_attempt(
        snapshot, external_dispatch_key=h["external_dispatch_key"]
    )
    if not isinstance(attempt, dict):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery lacks the durable one-shot attempt"
        )
    attempt_expected = {
        "attempt_id": h["attempt_id"],
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "created_operation_id": h["operation_id"],
        "created_generation": h["generation"],
        "creator_claim_id": h["claim_id"],
        "creator_dispatch_id": h["dispatch_id"],
        "authorization_event_id": h["authorization_event_id"],
        "trusted_context_digest": h["trusted_context_digest"],
    }
    if any(attempt.get(key) != value for key, value in attempt_expected.items()):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery attempt identity drifted"
        )
    binding = attempt.get("execution_binding") or {}
    if (
        binding.get("role") != h["role"]
        or binding.get("workflow_file") != h["workflow_file"]
        or binding.get("default_branch") != "main"
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery execution binding drifted"
        )

    generation_events = {
        int(row.get("sequence", -1)): row
        for row in operation_events(snapshot, h["operation_id"])
        if int(row.get("operation_generation", -1)) == h["generation"]
    }
    try:
        selected, claimed, authorized, lookup = (
            generation_events[8],
            generation_events[9],
            generation_events[10],
            generation_events[11],
        )
    except KeyError as exc:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery journal lost the exact immutable dispatch window"
        ) from exc
    if (
        selected.get("event_id") != h["selected_event_id"]
        or claimed.get("event_id") != h["claim_event_id"]
        or authorized.get("event_id") != h["authorization_event_id"]
        or lookup.get("event_id") != h["lookup_event_id"]
        or tuple(row.get("event_type") for row in (selected, claimed, authorized, lookup))
        != (
            "loop.step.selected",
            "dispatch.claimed",
            "dispatch.launch.authorized",
            "dispatch.launch.lookup-recorded",
        )
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery journal escaped the exact immutable dispatch window"
        )
    if (
        (selected.get("payload") or {}).get("step") != "IMPLEMENTATION_WORK"
        or (selected.get("payload") or {}).get("task_identity") != h["task_identity"]
        or (claimed.get("payload") or {}).get("claim_id") != h["claim_id"]
        or (claimed.get("payload") or {}).get("semantic_effect_key") != h["semantic_effect_key"]
        or (claimed.get("payload") or {}).get("external_dispatch_key") != h["external_dispatch_key"]
        or authorized.get("event_id") != h["authorization_event_id"]
        or (authorized.get("payload") or {}).get("dispatch_id") != h["dispatch_id"]
        or (authorized.get("payload") or {}).get("candidate_head_sha") != h["candidate_head_sha"]
        or (lookup.get("payload") or {}).get("external_dispatch_key") != h["external_dispatch_key"]
        or (lookup.get("payload") or {}).get("lookup_state") != "UNKNOWN"
        or (lookup.get("payload") or {}).get("receipt_id") is not None
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery immutable dispatch facts drifted"
        )
    return projection, reservation, attempt


def _historical_recovery_dispatch(
    snapshot: Any,
    preflight: Any,
) -> dict[str, Any]:
    _, reservation, attempt = _historical_attempt_identity(
        snapshot, preflight, require_unknown=False
    )
    h = HISTORICAL_PREHTTP_RECOVERY
    candidate = preflight.composition.candidate_provider.current_candidate(
        operation_id=h["operation_id"],
        repository=preflight.execution.repository,
        feature_id=h["feature_id"],
        target_ref=h["target_ref"],
    )
    if (
        candidate.candidate_pr_number != h["candidate_pr_number"]
        or candidate.candidate_head_sha != h["candidate_head_sha"]
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery fixed candidate changed"
        )
    return {
        "operation_id": h["operation_id"],
        "operation_generation": h["generation"],
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "dispatch_id": h["dispatch_id"],
        "target_repository": str(reservation["target_repository"]),
        "target_ref": h["target_ref"],
        "feature_id": h["feature_id"],
        "expected_revision": 1,
        "feature_stage": h["stage"],
        "task_id": h["task_id"],
        "task_identity": h["task_identity"],
        "role": h["role"],
        "candidate_pr_number": h["candidate_pr_number"],
        "candidate_head_sha": h["candidate_head_sha"],
        "_attempt_id": attempt["attempt_id"],
    }


def _verify_historical_prehttp_proof(
    preflight: Any,
    dispatch: dict[str, Any],
) -> str:
    h = HISTORICAL_PREHTTP_RECOVERY
    run = _github_json(preflight, f"/actions/runs/{h['run_id']}")
    if (
        run.get("id") != h["run_id"]
        or run.get("workflow_id") != h["workflow_id"]
        or run.get("head_sha") != h["installation_commit_sha"]
        or run.get("path") != ".github/workflows/v03-real-dogfood-scenario.yml"
        or run.get("event") != "workflow_dispatch"
        or run.get("run_attempt") != 1
        or run.get("conclusion") != "failure"
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery source run identity drifted"
        )

    composition = _github_json(
        preflight,
        "/contents/scripts/v03_dogfood_full_composition.py"
        + f"?ref={h['installation_commit_sha']}",
    )
    if composition.get("sha") != h["composition_blob_sha"]:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery composition blob drifted"
        )

    root = Path(__file__).resolve().parents[1]
    for path, expected_blob in HISTORICAL_PREHTTP_CORE_BLOBS.items():
        historical = _github_json(
            preflight,
            f"/contents/{path}?ref={h['installation_commit_sha']}",
        )
        if historical.get("sha") != expected_blob:
            raise V03DogfoodRuntimeDriverError(
                f"historical pre-HTTP recovery source blob drifted: {path}"
            )
        if _git_blob_sha(root / path) != expected_blob:
            raise V03DogfoodRuntimeDriverError(
                f"current pre-HTTP recovery core blob differs from historical source: {path}"
            )

    workflow = preflight.composition.workflows.developer_workflow
    inputs = GhAwVerticalRoleDispatchGateway(
        transport=object(), workflows=preflight.composition.workflows
    )._inputs(dispatch)
    http_calls: list[dict[str, Any]] = []

    def forbidden_http(**kwargs):
        http_calls.append(dict(kwargs))
        raise AssertionError("historical transport crossed HTTP boundary")

    historical_transport = GitHubActionsVerticalGhAwTransport(
        preflight.composition.actions_transport.config,
        http=forbidden_http,
        sleeper=lambda _seconds: None,
    )
    try:
        historical_transport.dispatch(
            workflow=workflow,
            ref="main",
            inputs=inputs,
        )
    except VerticalInvariantError as exc:
        if exc.code != "POLICY_DENIED":
            raise V03DogfoodRuntimeDriverError(
                "historical transport replay failed with a different policy outcome"
            ) from exc
    except AssertionError as exc:
        raise V03DogfoodRuntimeDriverError(
            "historical transport replay reached HTTP before rejection"
        ) from exc
    else:
        raise V03DogfoodRuntimeDriverError(
            "historical transport replay no longer rejects the old payload"
        )
    if http_calls:
        raise V03DogfoodRuntimeDriverError(
            "historical transport replay crossed HTTP before POLICY_DENIED"
        )

    accepted_key = preflight.composition.actions_transport._validate_dispatch_inputs(
        workflow=workflow,
        ref="main",
        inputs=inputs,
    )
    if accepted_key != h["external_dispatch_key"]:
        raise V03DogfoodRuntimeDriverError(
            "current fixed transport does not bind the exact historical key"
        )

    material = {
        "schema_version": "ai-sdlc.v03-dogfood-prehttp-proof/v1",
        "historical_run_id": h["run_id"],
        "historical_head_sha": h["installation_commit_sha"],
        "historical_composition_blob_sha": h["composition_blob_sha"],
        "core_blobs": dict(sorted(HISTORICAL_PREHTTP_CORE_BLOBS.items())),
        "operation_id": h["operation_id"],
        "operation_generation": h["generation"],
        "attempt_id": h["attempt_id"],
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "candidate_pr_number": h["candidate_pr_number"],
        "candidate_head_sha": h["candidate_head_sha"],
        "historical_replay": "POLICY_DENIED_BEFORE_HTTP",
        "provider_invalidation": False,
    }
    return digest_json(material)


def _validate_prehttp_recovery_marker(marker: Any, proof_digest: str) -> None:
    h = HISTORICAL_PREHTTP_RECOVERY
    expected = {
        "schema_version": PREHTTP_RECOVERY_MARKER_SCHEMA,
        "historical_run_id": h["run_id"],
        "historical_head_sha": h["installation_commit_sha"],
        "operation_id": h["operation_id"],
        "operation_generation": h["generation"],
        "attempt_id": h["attempt_id"],
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "candidate_pr_number": h["candidate_pr_number"],
        "candidate_head_sha": h["candidate_head_sha"],
        "proof_digest": proof_digest,
        "provider_invalidation": False,
    }
    if not isinstance(marker, dict) or any(marker.get(k) != v for k, v in expected.items()):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery marker identity drifted"
        )
    if not marker.get("recovery_installation_commit_sha") or not marker.get("created_at"):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery marker lacks audit provenance"
        )


def _plan_prehttp_recovery_marker(
    snapshot: Any,
    *,
    preflight: Any,
    proof_digest: str,
    occurred_at: str,
) -> StoreMutationPlan:
    h = HISTORICAL_PREHTTP_RECOVERY
    _historical_attempt_identity(snapshot, preflight, require_unknown=True)
    existing = snapshot.get(PREHTTP_RECOVERY_MARKER_PATH)
    if existing is not None:
        _validate_prehttp_recovery_marker(existing, proof_digest)
        return StoreMutationPlan(
            snapshot.ref_sha, tuple(), {"acquired": False, "marker": existing}
        )
    value = {
        "schema_version": PREHTTP_RECOVERY_MARKER_SCHEMA,
        "historical_run_id": h["run_id"],
        "historical_head_sha": h["installation_commit_sha"],
        "operation_id": h["operation_id"],
        "operation_generation": h["generation"],
        "attempt_id": h["attempt_id"],
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "candidate_pr_number": h["candidate_pr_number"],
        "candidate_head_sha": h["candidate_head_sha"],
        "proof_digest": proof_digest,
        "provider_invalidation": False,
        "recovery_installation_commit_sha": preflight.execution.installation_commit_sha,
        "created_at": occurred_at,
        "trusted_context_digest": preflight.trusted_context_digest,
    }
    return StoreMutationPlan(
        snapshot.ref_sha,
        (StoreMutation("create_immutable", PREHTTP_RECOVERY_MARKER_PATH, value),),
        {"acquired": True, "marker": value},
    )


def _durable_exact_recovery_launch(
    snapshot: Any,
    *,
    receipt_id: str,
) -> bool:
    """Recognize the one durable recovery LAUNCHED fact across installations."""
    h = HISTORICAL_PREHTTP_RECOVERY
    launched: list[dict[str, Any]] = []
    for row in operation_events(snapshot, h["operation_id"]):
        if (
            row.get("event_type") != "dispatch.launch.lookup-recorded"
            or int(row.get("operation_generation", -1)) != h["generation"]
        ):
            continue
        payload = row.get("payload") or {}
        if (
            str(payload.get("external_dispatch_key") or "")
            != h["external_dispatch_key"]
            or payload.get("lookup_state") != "LAUNCHED"
        ):
            continue
        launched.append(row)

    if not launched:
        return False
    if len(launched) != 1:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery has ambiguous durable launch receipts"
        )
    row = launched[0]
    payload = row.get("payload") or {}
    try:
        sequence = int(row.get("sequence", -1))
    except (TypeError, ValueError) as exc:
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP durable launch lacks valid sequence identity"
        ) from exc
    if (
        str(payload.get("receipt_id") or "") != receipt_id
        or sequence <= h["last_sequence"]
        or not str(row.get("trusted_context_digest") or "")
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP durable launch receipt identity drifted"
        )
    return True


def _record_exact_recovery_launch(
    preflight: Any,
    *,
    receipt: dict[str, Any],
) -> None:
    h = HISTORICAL_PREHTTP_RECOVERY
    if (
        receipt.get("lookup_state") != "LAUNCHED"
        or not receipt.get("receipt_id")
    ):
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery lacks an exact launched receipt"
        )
    receipt_id = str(receipt["receipt_id"])
    runtime = preflight.composition.runtime

    def plan(snapshot: Any) -> StoreMutationPlan:
        if _durable_exact_recovery_launch(snapshot, receipt_id=receipt_id):
            return StoreMutationPlan(
                snapshot.ref_sha,
                tuple(),
                {"already_recorded": True, "receipt_id": receipt_id},
            )
        return plan_launch_lookup(
            snapshot,
            operation_id=h["operation_id"],
            generation=h["generation"],
            external_dispatch_key_value=h["external_dispatch_key"],
            lookup_state="LAUNCHED",
            receipt_id=receipt_id,
            occurred_at=runtime.clock(),
            trusted_context_digest=preflight.trusted_context_digest,
        )

    runtime.commit_replanned(plan)


def recover_historical_prehttp_attempt(preflight: Any) -> bool:
    """Recover one exact dogfood attempt proven to have failed before HTTP.

    This is not provider invalidation and does not resolve a generic UNKNOWN.
    It applies only to the frozen happy_path attempt whose historical exact
    source deterministically rejected before any HTTP call. A separate immutable
    marker is committed before the sole recovery POST permission is consumed.
    Marker replay is lookup-only forever.
    """
    h = HISTORICAL_PREHTTP_RECOVERY
    if preflight.slot.scenario != h["scenario"]:
        return False

    runtime = preflight.composition.runtime
    snapshot = runtime.backend.read_snapshot()
    try:
        dispatch = _historical_recovery_dispatch(snapshot, preflight)
    except Exception as exc:
        if isinstance(exc, V03DogfoodRuntimeDriverError):
            raise
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery could not reconstruct exact dispatch"
        ) from exc
    dispatch.pop("_attempt_id", None)
    proof_digest = _verify_historical_prehttp_proof(preflight, dispatch)

    marker = snapshot.get(PREHTTP_RECOVERY_MARKER_PATH)
    if marker is not None:
        _validate_prehttp_recovery_marker(marker, proof_digest)
        receipt = preflight.composition.dispatch_gateway.lookup(
            external_dispatch_key=h["external_dispatch_key"]
        )
        if isinstance(receipt, dict) and receipt.get("lookup_state") == "LAUNCHED":
            _record_exact_recovery_launch(preflight, receipt=receipt)
            return True
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery permission was already consumed; exact launch remains unconfirmed"
        )

    _historical_attempt_identity(snapshot, preflight, require_unknown=True)
    before = preflight.composition.dispatch_gateway.lookup(
        external_dispatch_key=h["external_dispatch_key"]
    )
    if isinstance(before, dict) and before.get("lookup_state") == "LAUNCHED":
        _record_exact_recovery_launch(preflight, receipt=before)
        return True
    if not isinstance(before, dict) or before.get("lookup_state") != "NOT_LAUNCHED":
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery requires current exhaustive NOT_LAUNCHED lookup"
        )

    marker_result = runtime.commit_replanned(
        lambda fresh: _plan_prehttp_recovery_marker(
            fresh,
            preflight=preflight,
            proof_digest=proof_digest,
            occurred_at=runtime.clock(),
        )
    ).result
    if not marker_result.get("acquired"):
        receipt = preflight.composition.dispatch_gateway.lookup(
            external_dispatch_key=h["external_dispatch_key"]
        )
        if isinstance(receipt, dict) and receipt.get("lookup_state") == "LAUNCHED":
            _record_exact_recovery_launch(preflight, receipt=receipt)
            return True
        raise V03DogfoodRuntimeDriverError(
            "historical pre-HTTP recovery marker race consumed the only recovery permission"
        )

    try:
        receipt = preflight.composition.dispatch_gateway.launch(dispatch=dispatch)
    except Exception:
        receipt = preflight.composition.dispatch_gateway.lookup(
            external_dispatch_key=h["external_dispatch_key"]
        )
    if isinstance(receipt, dict) and receipt.get("lookup_state") == "LAUNCHED":
        _record_exact_recovery_launch(preflight, receipt=receipt)
        return True
    raise V03DogfoodRuntimeDriverError(
        "historical pre-HTTP recovery POST remains unconfirmed; marker forbids a second attempt"
    )


_SAFE_INSTALLATION_TRANSITION_EVENTS = (
    "operation.started",
    "loop.step.selected",
    "dispatch.claimed",
    "dispatch.launch.authorized",
    "dispatch.launch.lookup-recorded",
)


def _previous_installation_window(snapshot: Any, preflight: Any) -> tuple[str | None, str | None, str | None]:
    """Validate the complete immutable window on this exact Store snapshot."""
    unfinished = query_unfinished(
        snapshot,
        target_repository=preflight.execution.repository,
        feature_id=preflight.slot.feature_id,
    )
    if not unfinished:
        return None, None, None
    if len(unfinished) != 1:
        raise V03DogfoodRuntimeDriverError(
            "dogfood installation transition requires exactly one unfinished Operation"
        )

    operation_id = str(unfinished[0].get("operation_id") or "")
    if not operation_id:
        raise V03DogfoodRuntimeDriverError(
            "unfinished dogfood Operation lacks immutable identity"
        )
    projection = rebuild_projection(snapshot, operation_id)
    if (
        projection.get("operation_profile") != VERTICAL_PROFILE
        or int(projection.get("expected_feature_revision", -1)) != 1
        or projection.get("status") != "RUNNING"
    ):
        raise V03DogfoodRuntimeDriverError(
            "unfinished dogfood Operation is outside the bounded pre-launch transition"
        )

    generation = int(projection.get("generation", -1))
    journal = operation_events(snapshot, operation_id)
    current = [
        row for row in journal
        if int(row.get("operation_generation", -1)) == generation
    ]
    digests = {str(row.get("trusted_context_digest") or "") for row in current}
    if not current or "" in digests:
        raise V03DogfoodRuntimeDriverError(
            "unfinished dogfood Operation lacks one trusted installation context"
        )
    if digests == {preflight.trusted_context_digest}:
        return operation_id, None, None
    if preflight.trusted_context_digest in digests or len(digests) != 1:
        raise V03DogfoodRuntimeDriverError(
            "unfinished dogfood Operation mixes installation contexts"
        )

    event_types = tuple(str(row.get("event_type") or "") for row in journal)
    if generation != 0 or current != journal or event_types != _SAFE_INSTALLATION_TRANSITION_EVENTS:
        raise V03DogfoodRuntimeDriverError(
            "unfinished dogfood Operation escaped the exact pre-launch transition window"
        )

    selected, claimed, authorized, lookup = current[1:]
    selected_payload = selected.get("payload") or {}
    claim_payload = claimed.get("payload") or {}
    authorization_payload = authorized.get("payload") or {}
    lookup_payload = lookup.get("payload") or {}
    semantic_key = str(claim_payload.get("semantic_effect_key") or "")
    external_key = str(claim_payload.get("external_dispatch_key") or "")
    reservation = snapshot.get(reservation_path(semantic_key)) if semantic_key else None
    if (
        selected_payload.get("step") != "IMPLEMENTATION_WORK"
        or not isinstance(reservation, dict)
        or reservation.get("external_dispatch_key") != external_key
        or authorization_payload.get("semantic_effect_key") != semantic_key
        or authorization_payload.get("external_dispatch_key") != external_key
        or lookup_payload.get("external_dispatch_key") != external_key
        or lookup_payload.get("lookup_state") != "NOT_LAUNCHED"
        or lookup_payload.get("receipt_id") is not None
        or snapshot.get(external_create_attempt_path(semantic_key)) is not None
    ):
        raise V03DogfoodRuntimeDriverError(
            "unfinished dogfood Operation lacks exact no-launch/no-attempt reservation proof"
        )
    return operation_id, semantic_key, external_key


def prepare_previous_installation_operation(preflight: Any) -> str | None:
    """Move one exact pre-launch dogfood Operation onto the current installation.

    This is deliberately narrower than general Operation recovery.  It accepts
    only the observed old-installation window: one generation-0 revision-1
    vertical Operation whose complete journal selects and authorizes exactly one
    dispatch, whose only trusted lookup proves NOT_LAUNCHED, and which has no
    callback, Persist, or external-create-attempt fact.  Every CAS retry repeats
    this complete predicate against the fresh protected Store snapshot.
    """
    runtime = preflight.composition.runtime
    operation_id, semantic_key, external_key = _previous_installation_window(
        runtime.backend.read_snapshot(), preflight
    )
    if operation_id is None or semantic_key is None or external_key is None:
        return operation_id

    def checked_takeover(fresh):
        fresh_operation_id, fresh_semantic_key, fresh_external_key = (
            _previous_installation_window(fresh, preflight)
        )
        if (
            fresh_operation_id != operation_id
            or fresh_semantic_key != semantic_key
            or fresh_external_key != external_key
        ):
            raise V03DogfoodRuntimeDriverError(
                "dogfood installation transition changed during protected CAS replan"
            )
        return plan_vertical_takeover(
            fresh,
            operation_id=operation_id,
            occurred_at=runtime.clock(),
            trusted_context_digest=preflight.trusted_context_digest,
        )

    runtime.commit_replanned(checked_takeover)
    after = rebuild_projection(runtime.backend.read_snapshot(), operation_id)
    if (
        int(after.get("generation", -1)) != 1
        or after.get("status") != "RUNNING"
        or external_key not in set(after.get("authorized_dispatches") or ())
    ):
        raise V03DogfoodRuntimeDriverError(
            "dogfood installation transition did not preserve same-key authority"
        )
    return operation_id


def public_preflight(preflight: Any) -> dict[str, Any]:
    return {
        "schema_version": "ai-sdlc.v03-dogfood-runtime-driver-preflight/v1",
        "scenario": preflight.slot.scenario,
        "repository": preflight.execution.repository,
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
        "protected_state_ref_sha": preflight.live_authority.protected_state_ref_sha,
        "issue_221_ledger_digest": preflight.live_gate.issue221.ledger_digest,
        "feature_id": preflight.slot.feature_id,
        "target_ref": preflight.slot.target_ref,
        "candidate_pr_number": preflight.candidate_pr_number,
        "candidate_head_sha": preflight.candidate_head_sha,
        "trusted_context_digest": preflight.trusted_context_digest,
        "release_evidence": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=sorted(MODES), required=True)
    parser.add_argument("--scenario", choices=sorted(ALLOWED_SCENARIOS), required=True)
    args = parser.parse_args()
    mode, scenario = require_mode(
        mode=args.mode,
        scenario=args.scenario,
        event_name=str(os.environ.get("GITHUB_EVENT_NAME") or ""),
        ref=str(os.environ.get("GITHUB_REF") or ""),
    )
    if mode == VALIDATE_ONLY:
        print(json.dumps({
            "schema_version": "ai-sdlc.v03-dogfood-runtime-driver-validation/v1",
            "scenario": scenario,
            "live_authority_loaded": False,
            "model_called": False,
            "worker_dispatched": False,
            "release_evidence": False,
        }, sort_keys=True))
        return 0

    with store_git_transport(
        token=_required(os.environ, "AI_SDLC_EVENT_WRITE_TOKEN"),
        repository=_required(os.environ, "GITHUB_REPOSITORY"),
    ):
        return _execute_live(mode=mode, scenario=scenario)


def _execute_live(*, mode: str, scenario: str) -> int:
    preflight = assemble_preflight(scenario=scenario, env=os.environ, checkout_sha=_head())
    if mode == PREFLIGHT_ONLY:
        print(json.dumps(public_preflight(preflight), indent=2, sort_keys=True))
        return 0

    recovered_historical_attempt = recover_historical_prehttp_attempt(preflight)
    if not recovered_historical_attempt:
        prepare_previous_installation_operation(preflight)
    else:
        # Receipt adoption does not imply result collectibility. Apply the same
        # protected launch/current-installation/first-attempt wait as collection
        # before paying for or starting a new Responses session.
        h = HISTORICAL_PREHTTP_RECOVERY
        try:
            _wait_current_dispatch(preflight, h["operation_id"], h["external_dispatch_key"])
        except V03DogfoodScenarioRunnerError as exc:
            raise V03DogfoodRuntimeDriverError(
                "HISTORICAL_WORKER_NOT_COLLECTIBLE: " + str(exc)
                + "; original receipt retained; rerun, replacement dispatch and release evidence remain unauthorized"
            ) from exc

    host_config = dogfood_responses_host_config(os.environ)
    host = V03DogfoodOpenAIResponsesHost(config=host_config, adapter=preflight.composition.adapter)
    recovery_host = (
        V03DogfoodOpenAIResponsesHost(config=host_config, adapter=preflight.composition.adapter)
        if scenario == "session_recovery"
        else None
    )
    observation = run_scenario(preflight=preflight, host=host, recovery_host=recovery_host)
    final_candidate = preflight.composition.candidate_provider.current_candidate(
        operation_id=observation.operation_id, repository=preflight.execution.repository,
        feature_id=preflight.slot.feature_id, target_ref=preflight.slot.target_ref,
    )
    if final_candidate.candidate_pr_number != preflight.candidate_pr_number:
        raise V03DogfoodRuntimeDriverError("dogfood terminal target PR changed")
    doc = {
        "schema_version": "ai-sdlc.v03-dogfood-runtime-observation/v1",
        **asdict(observation),
        "repository": preflight.execution.repository,
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "feature_id": preflight.slot.feature_id,
        "target_ref": preflight.slot.target_ref,
        "candidate_pr_number": preflight.candidate_pr_number,
        "candidate_head_sha": final_candidate.candidate_head_sha,
        "initial_candidate_head_sha": preflight.candidate_head_sha,
        "trusted_context_digest": preflight.trusted_context_digest,
        "release_eligible": False,
        "provenance_verified": False,
    }
    output = Path(str(os.environ.get("AI_SDLC_DOGFOOD_OBSERVATION_PATH") or f"evidence/v03-dogfood/{scenario}-observation.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
