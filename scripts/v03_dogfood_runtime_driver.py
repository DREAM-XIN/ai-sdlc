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
import tempfile
import time
from typing import Any, Mapping
from urllib import error as urlerror, request as urlrequest, parse

from operator_external_create_attempt import external_create_attempt_path, find_external_create_attempt
from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_store import plan_launch_lookup, query_unfinished
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import StoreMutation, StoreMutationPlan, canonical_json, digest_json, normalize_repository, operation_events, rebuild_projection, reservation_path
from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_recovery import plan_vertical_takeover
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport
from v03_dogfood_full_composition import (
    ARMED_RECOVERY_SOURCE, ARMED_RECOVERY_STORE, ARMED_RECOVERY_KEY,
    ARMED_RECOVERY_SOURCE_BLOBS, ARMED_RECOVERY_AUTHORIZATION_BLOB,
    ARMED_RECOVERY_ATTEMPT_BLOB, ARMED_RECOVERY_NO_HTTP_PROOF,
    RECOVERY_CONTINUATION_PATH, RECOVERY_CONTINUATION_SCHEMA,
    REPLACEMENT_AUTHORIZATION_PATH, REPLACEMENT_ATTEMPT_PATH, REPLACEMENT_RECEIPT_PATH,
    REPLACEMENT_ADMISSION, REPLACEMENT_FAILED_SOURCE, REPLACEMENT_FAILED_RUN,
    REPLACEMENT_FAILED_PR, REPLACEMENT_FAILED_HEAD, REPLACEMENT_WORKER_BLOBS,
    REPLACEMENT_FAILED_OBSERVATION, REPLACEMENT_PATHS,
    REPLACEMENT_ACCOUNTING, REPLACEMENT_ACCOUNTING_DIGEST, REPLACEMENT_ACCOUNTING_URI,
    replacement_present, validate_replacement_predecessor, validate_replacement_chain,
    replacement_authorization_identity, recovery_route,
    validate_armed_recovery_pair, validate_recovery_continuation,
    validate_recovery_execution_seal, recovery_execution_binding, RECOVERY_COLLECTOR_DISPATCH_ID,
)
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


RECOVERY_SCHEMA = "ai-sdlc.v03-dogfood-bounded-recovery/v1"
RECOVERY_WORKFLOW = "ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml"
RECOVERY_BASE_PATH = (
    "state/operator/v1/operations/" + HISTORICAL_PREHTTP_RECOVERY["operation_id"]
    + "/dogfood-bounded-recovery"
)
RECOVERY_AUTHORIZATION_PATH = RECOVERY_BASE_PATH + "/authorization.json"
RECOVERY_ATTEMPT_PATH = RECOVERY_BASE_PATH + "/create-attempt.json"
RECOVERY_RECEIPT_PATH = RECOVERY_BASE_PATH + "/sealed-receipt.json"
RECOVERY_OBSERVATION_DIGEST = "sha256:a86b7ead37bf96abe9b6e43098b7873b821833c6d93c720ed7409835af18916f"
HISTORICAL_APP_PUBLIC_KEY_DIGEST = "sha256:2765aa5be8fe724421236d1ff15fb6ebaccc51b7476d82c27ead7e366cb32636"
HISTORICAL_WORKER_RECEIPT = "37204777409"

# Reviewed, immutable, non-release proof of the exact historical key revocation.
# This is not launch authority: protected CAS and provider reauthentication
# remain separately required. Do not follow the mutable diagnostic branch.
REVOCATION_PROBE_SOURCE = "9dce67c90df3a8b302e0509d77c9420db353836e"
REVOCATION_PROBE_PARENT = "9647b0802035dd15ca09a47774fb4e12c0cf9e14"
REVOCATION_OBSERVATION_COMMIT = "af3e170de4c6bcec6ffcc61ee63f2101a6dcdda9"
REVOCATION_OBSERVATION_BLOB = "040a2c0ae668a9f4f0ff497122889a0236480ccd"
REVOCATION_PROBE_RUN = 37877145475
REVOCATION_PROBE_WORKFLOW_ID = 329730419
REVOCATION_PROBE_BRANCH = "dogfood/gh-aw-diagnose-key-revocation-20261008"
REVOCATION_PROBE_WORKFLOW = ".github/workflows/ai-sdlc-gh-aw-run-diagnostic.yml"
REVOCATION_NEW_KEY_DIGEST = "sha256:cf2341fc6c86e0a1226f9e4a5e409c4f432f3e326075be6c11425712be75e9ce"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _app_jwt_and_public_digest(*, private_key: str, issuer: str) -> tuple[str, str]:
    if not private_key.strip() or not issuer.strip():
        raise V03DogfoodRuntimeDriverError("provider key fence lacks App signing identity")
    now = int(time.time())
    header = _b64url(b'{"alg":"RS256","typ":"JWT"}')
    payload = _b64url(json.dumps(
        {"iat": now - 60, "exp": now + 480, "iss": issuer},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    signing_input = (header + "." + payload).encode("ascii")
    key_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(private_key)
            key_path = handle.name
        signature = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=signing_input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        public = subprocess.run(
            ["openssl", "pkey", "-in", key_path, "-pubout", "-outform", "DER"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    finally:
        if key_path:
            try:
                os.unlink(key_path)
            except OSError:
                pass
    if signature.returncode or public.returncode or not signature.stdout or not public.stdout:
        raise V03DogfoodRuntimeDriverError("provider key fence could not derive App key identity")
    return (
        header + "." + payload + "." + _b64url(signature.stdout),
        "sha256:" + hashlib.sha256(public.stdout).hexdigest(),
    )


def _validate_provider_rotation_fence(observation: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version": RECOVERY_SCHEMA,
        "app_id": 4576406,
        "app_client_id": "Iv23libojxnnuF43petx",
        "installation_id": 153325330,
        "historical_status": 401,
        "recovery_status": 200,
        "historical_public_key_digest": HISTORICAL_APP_PUBLIC_KEY_DIGEST,
        "provider_observation_run_id": REVOCATION_PROBE_RUN,
        "provider_observation_blob_sha": REVOCATION_OBSERVATION_BLOB,
    }
    if any(observation.get(key) != value for key, value in expected.items()):
        raise V03DogfoodRuntimeDriverError("provider key fence does not prove old-401/new-200 same-App rotation")
    new_digest = str(observation.get("recovery_public_key_digest") or "")
    if not new_digest.startswith("sha256:") or new_digest == HISTORICAL_APP_PUBLIC_KEY_DIGEST:
        raise V03DogfoodRuntimeDriverError("provider key fence lacks a distinct recovery public key")
    material = dict(observation)
    material["recovery_authority"] = False
    material["release_eligible"] = False
    material["fence_digest"] = "sha256:" + digest_json(material)
    return material


def _load_pinned_revocation_observation(
    env: Mapping[str, str], *, read_json=None,
) -> dict[str, Any]:
    """Read only exact GitHub run + immutable source/output/blob provenance.

    A mutable branch, an Issue comment, a current secret, or a synthetic
    test fixture must never act as provider revocation authority.
    """
    if normalize_repository(_required(env, "GITHUB_REPOSITORY")) != "dream-xin/ai-sdlc":
        raise V03DogfoodRuntimeDriverError("revocation observation escaped fixed repository")
    api = _required(env, "GITHUB_API_URL").rstrip("/")
    if api != "https://api.github.com":
        raise V03DogfoodRuntimeDriverError("revocation observation requires exact GitHub provider API")
    token = _required(env, "AI_SDLC_ACTIONS_READ_TOKEN")

    def provider_get(path: str) -> dict[str, Any]:
        req = urlrequest.Request(
            api + "/repos/DREAM-XIN/ai-sdlc/" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-sdlc-reviewed-revocation-evidence",
            },
            method="GET",
        )
        try:
            with urlrequest.urlopen(req, timeout=20) as response:
                if response.status != 200:
                    raise V03DogfoodRuntimeDriverError("revocation provenance GET was not successful")
                value = json.loads(response.read().decode("utf-8"))
        except (ValueError, UnicodeError, OSError, urlerror.HTTPError) as exc:
            raise V03DogfoodRuntimeDriverError("revocation provenance GET was unavailable") from exc
        if not isinstance(value, dict):
            raise V03DogfoodRuntimeDriverError("revocation provenance is not an object")
        return value

    get = read_json if read_json is not None else provider_get
    run = get("actions/runs/" + str(REVOCATION_PROBE_RUN))
    if any((
        run.get("id") != REVOCATION_PROBE_RUN,
        run.get("workflow_id") != REVOCATION_PROBE_WORKFLOW_ID,
        run.get("path") != REVOCATION_PROBE_WORKFLOW,
        run.get("event") != "push",
        run.get("head_branch") != REVOCATION_PROBE_BRANCH,
        run.get("head_sha") != REVOCATION_PROBE_SOURCE,
        run.get("run_attempt") != 1,
        run.get("status") != "completed",
        run.get("conclusion") != "success",
    )):
        raise V03DogfoodRuntimeDriverError("revocation provider probe run identity drifted")
    jobs = get("actions/runs/" + str(REVOCATION_PROBE_RUN) + "/jobs?per_page=100")
    rows = jobs.get("jobs")
    if (
        jobs.get("total_count") != 2
        or not isinstance(rows, list) or len(rows) != 2
        or {row.get("name") for row in rows if isinstance(row, dict)}
        != {"validate-probe", "verify-key-fence"}
        or any(not isinstance(row, dict) or row.get("run_id") != REVOCATION_PROBE_RUN
               or row.get("run_attempt") != 1 or row.get("status") != "completed"
               or row.get("conclusion") != "success" for row in rows)
    ):
        raise V03DogfoodRuntimeDriverError("revocation provider probe jobs are not successful first-attempt facts")

    source = get("git/commits/" + REVOCATION_PROBE_SOURCE)
    receipt_commit = get("git/commits/" + REVOCATION_OBSERVATION_COMMIT)
    if (
        source.get("sha") != REVOCATION_PROBE_SOURCE
        or [row.get("sha") for row in source.get("parents", [])] != [REVOCATION_PROBE_PARENT]
        or source.get("message") != "dogfood: verify revoked historical Worker key"
        or receipt_commit.get("sha") != REVOCATION_OBSERVATION_COMMIT
        or [row.get("sha") for row in receipt_commit.get("parents", [])] != [REVOCATION_PROBE_SOURCE]
        or receipt_commit.get("message") != "dogfood: record bounded provider key revocation observation"
        or not isinstance(receipt_commit.get("tree"), dict)
    ):
        raise V03DogfoodRuntimeDriverError("revocation source/output commit chain changed")
    tree_sha = receipt_commit["tree"].get("sha")
    if not isinstance(tree_sha, str) or len(tree_sha) != 40:
        raise V03DogfoodRuntimeDriverError("revocation evidence tree identity is invalid")
    tree = get("git/trees/" + tree_sha + "?recursive=1")
    entries = tree.get("tree")
    if (
        tree.get("sha") != tree_sha or tree.get("truncated") is not False
        or not isinstance(entries, list)
        or len([entry for entry in entries if isinstance(entry, dict)
                and entry.get("path") == "dogfood/gh-aw-key-revocation-observation.json"
                and entry.get("mode") == "100644" and entry.get("type") == "blob"
                and entry.get("sha") == REVOCATION_OBSERVATION_BLOB]) != 1
    ):
        raise V03DogfoodRuntimeDriverError("revocation observation blob not bound to pinned output tree")

    blob = get("git/blobs/" + REVOCATION_OBSERVATION_BLOB)
    if (
        blob.get("sha") != REVOCATION_OBSERVATION_BLOB
        or blob.get("encoding") != "base64"
        or not isinstance(blob.get("size"), int)
        or not 0 < blob["size"] <= 8192
        or not isinstance(blob.get("content"), str)
    ):
        raise V03DogfoodRuntimeDriverError("revocation observation blob metadata changed")
    try:
        raw = base64.b64decode("".join(blob["content"].split()), validate=True)
        observed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise V03DogfoodRuntimeDriverError("revocation observation blob is malformed") from exc
    if (
        len(raw) != blob["size"]
        or hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw).hexdigest()
        != REVOCATION_OBSERVATION_BLOB
        or not isinstance(observed, dict)
    ):
        raise V03DogfoodRuntimeDriverError("revocation observation immutable bytes changed")
    old, new = observed.get("old_key"), observed.get("recovery_key")
    if (
        observed.get("schema_version") != "ai-sdlc.v03-historical-worker-key-revocation-observation/v1"
        or observed.get("repository") != "dream-xin/ai-sdlc"
        or observed.get("source_sha") != REVOCATION_PROBE_SOURCE
        or observed.get("run_id") != REVOCATION_PROBE_RUN
        or observed.get("historical_run_id") != 37204777409
        or observed.get("historical_workflow_blob_sha") != HISTORICAL_FAILED_WORKER["workflow_blob_sha"]
        or observed.get("old_credential_identity") != "AI_SDLC_RUNTIME_APP_PRIVATE_KEY"
        or observed.get("recovery_credential_identity") != "AI_SDLC_DOGFOOD_RECOVERY_APP_PRIVATE_KEY"
        or observed.get("client_id") != "Iv23libojxnnuF43petx"
        or observed.get("status") != "REVOCATION_OBSERVED"
        or observed.get("provider_invalidation") is not True
        or observed.get("future_attempts_fenced") is not False
        or observed.get("recovery_authority") is not False
        or observed.get("release_eligible") is not False
        or not isinstance(old, dict) or not isinstance(new, dict)
        or old.get("http_status") != 401
        or old.get("public_key_sha256") != HISTORICAL_APP_PUBLIC_KEY_DIGEST.split(":", 1)[1]
        or new.get("http_status") != 200
        or new.get("app_id") != 4576406
        or new.get("app_slug") != "dream-xin-ai-sdlc-runtime-operator"
        or "sha256:" + str(new.get("public_key_sha256") or "") != REVOCATION_NEW_KEY_DIGEST
        or not all(isinstance(x.get("github_request_id"), str) and x["github_request_id"]
                   and isinstance(x.get("github_date"), str) and x["github_date"]
                   for x in (old, new))
    ):
        raise V03DogfoodRuntimeDriverError("revocation provider observation contract changed")
    return observed


def _observe_provider_rotation(env: Mapping[str, str]) -> dict[str, Any]:
    # An old Secret retained under a legacy name could be overwritten by a
    # working key, re-enabling historical pinned Workers. Removal is mandatory.
    if (
        env.get("AI_SDLC_LEGACY_SECRET_PRESENT") != "false"
        or str(env.get("AI_SDLC_HISTORICAL_APP_PRIVATE_KEY") or "").strip()
    ):
        raise V03DogfoodRuntimeDriverError("legacy App credential must be absent before recovery")
    observed = _load_pinned_revocation_observation(env)
    issuer = _required(env, "AI_SDLC_DOGFOOD_RECOVERY_APP_CLIENT_ID")
    if issuer != "Iv23libojxnnuF43petx":
        raise V03DogfoodRuntimeDriverError("recovery App client identity drifted")
    recovery_jwt, recovery_digest = _app_jwt_and_public_digest(
        private_key=_required(env, "AI_SDLC_RECOVERY_APP_PRIVATE_KEY"),
        issuer=issuer,
    )
    if recovery_digest != REVOCATION_NEW_KEY_DIGEST:
        raise V03DogfoodRuntimeDriverError("live recovery key differs from the authenticated pinned probe")
    # Live new-key reauthentication is independently checked on each
    # execution. Old-key 401 is bound to the prior immutable provider probe.
    api = _required(env, "GITHUB_API_URL").rstrip("/")
    if api != "https://api.github.com":
        raise V03DogfoodRuntimeDriverError("live recovery key probe escaped GitHub")
    req = urlrequest.Request(
        api + "/app",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + recovery_jwt,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-bounded-recovery-fence",
        },
        method="GET",
    )
    try:
        with urlrequest.urlopen(req, timeout=20) as response:
            recovery_status = int(response.status)
            recovery_app = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        recovery_status, recovery_app = int(exc.code), {}
        exc.close()
    except (OSError, ValueError) as exc:
        raise V03DogfoodRuntimeDriverError("live recovery provider identity probe indeterminate") from exc
    if (
        not isinstance(recovery_app, dict)
        or recovery_status != 200
        or recovery_app.get("id") != 4576406
        or recovery_app.get("client_id") != issuer
        or recovery_app.get("slug") != "dream-xin-ai-sdlc-runtime-operator"
    ):
        raise V03DogfoodRuntimeDriverError("recovery key did not authenticate the pinned GitHub App")
    return _validate_provider_rotation_fence({
        "schema_version": RECOVERY_SCHEMA,
        "app_id": 4576406,
        "app_client_id": issuer,
        "installation_id": 153325330,
        "historical_status": observed["old_key"]["http_status"],
        "recovery_status": recovery_status,
        "historical_public_key_digest": HISTORICAL_APP_PUBLIC_KEY_DIGEST,
        "recovery_public_key_digest": recovery_digest,
        "provider_observation_run_id": REVOCATION_PROBE_RUN,
        "provider_observation_blob_sha": REVOCATION_OBSERVATION_BLOB,
        # Stable provider observation identity: retries must reconstruct the
        # same protected-CAS fence digest after a lost local acknowledgement.
        # The live /app authentication is rechecked but does not rename it.
        "observed_at": observed["old_key"]["github_date"],
    })


def _bounded_recovery_identity(snapshot: Any, preflight: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    projection, reservation, _attempt = _historical_attempt_identity(
        snapshot, preflight, require_unknown=False
    )
    h = HISTORICAL_PREHTTP_RECOVERY
    events = [
        row for row in operation_events(snapshot, h["operation_id"])
        if int(row.get("operation_generation", -1)) == h["generation"]
    ]
    by_sequence = {int(row.get("sequence", -1)): row for row in events}
    launched = by_sequence.get(12)
    payload = (launched or {}).get("payload") or {}
    forbidden = {
        "worker.callback.recorded", "feature.event.translated", "persist.confirmed",
        "candidate.handoff.adopted", "effect.lineage.blocked",
    }
    if (
        projection.get("status") != "WAITING_EXTERNAL"
        or int(projection.get("last_sequence", -1)) != 12
        or not isinstance(launched, dict)
        or launched.get("event_type") != "dispatch.launch.lookup-recorded"
        or payload.get("external_dispatch_key") != h["external_dispatch_key"]
        or payload.get("lookup_state") != "LAUNCHED"
        or str(payload.get("receipt_id") or "") != HISTORICAL_WORKER_RECEIPT
        or any(str(row.get("event_type") or "") in forbidden for row in events)
        or any(int(row.get("sequence", -1)) > 12 for row in events)
    ):
        raise V03DogfoodRuntimeDriverError("bounded recovery escaped exact seq12 failed-Worker boundary")
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
        raise V03DogfoodRuntimeDriverError("bounded recovery candidate/ref identity drifted")
    return projection, reservation


def _recovery_worker_blobs() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = (
        ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.md",
        ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml",
        ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.md",
        ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.lock.yml",
    )
    result = {path: _git_blob_sha(root / path) for path in paths}
    if len(set(result.values())) != len(result):
        raise V03DogfoodRuntimeDriverError("recovery worker blob identities are ambiguous")
    return result


def _validate_recovery_pair(snapshot: Any, expected: Mapping[str, Any]) -> bool:
    if replacement_present(snapshot):
        authorization, attempt, _ = validate_replacement_chain(snapshot)
    else:
        authorization = snapshot.get(RECOVERY_AUTHORIZATION_PATH)
        attempt = snapshot.get(RECOVERY_ATTEMPT_PATH)
    if authorization is None and attempt is None:
        return False
    if not isinstance(authorization, dict) or not isinstance(attempt, dict):
        raise V03DogfoodRuntimeDriverError("bounded recovery authorization/attempt is incomplete")
    stable = {key: value for key, value in expected.items() if key != "created_at"}
    if any(authorization.get(key) != value for key, value in stable.items()):
        raise V03DogfoodRuntimeDriverError("bounded recovery authorization identity drifted")
    authorization_digest = "sha256:" + digest_json(authorization)
    expected_attempt = dict(authorization)
    expected_attempt.update({
        "authorization_digest": authorization_digest,
        "attempt_id": ("replacement-1-claim-" if replacement_present(snapshot) else "recovery-create-attempt-") + digest_json(authorization)[:32],
        "status": "ARMED",
    })
    if canonical_json(attempt) != canonical_json(expected_attempt):
        raise V03DogfoodRuntimeDriverError("bounded recovery create-attempt/full authorization binding drifted")
    return True



def _observe_armed_recovery_no_http(preflight: Any) -> dict[str, Any]:
    """Authenticate an exact immutable rejecting program, not absence of runs.

    The pinned driver always calls lookup before launch; both production lookup
    and dispatch reject its fixed recovery-* key before HTTP. The pinned
    workflow checks checkout HEAD == GITHUB_SHA. Thus an old checked-out runner
    remains rejecting, while an old workflow rerun after main advances cannot
    reach the driver. Terminal run/log observations supplement that source proof.
    """
    proof = ARMED_RECOVERY_NO_HTTP_PROOF
    def get(suffix):
        return _github_json(preflight, suffix)
    def source(path, ref, sha):
        document = get("/contents/" + path + "?ref=" + ref)
        if (
            document.get("type") != "file" or document.get("path") != path
            or document.get("encoding") != "base64" or document.get("sha") != sha
        ):
            raise V03DogfoodRuntimeDriverError("armed recovery proof source identity drifted")
        try:
            raw = base64.b64decode("".join(str(document["content"]).split()), validate=True)
        except (KeyError, ValueError) as exc:
            raise V03DogfoodRuntimeDriverError("armed recovery proof bytes unavailable") from exc
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw).hexdigest()
        if actual != sha:
            raise V03DogfoodRuntimeDriverError("armed recovery proof source bytes drifted")
    def run():
        value = get("/actions/runs/37892560162")
        expected = {
            "id": 37892560162, "run_attempt": 1, "workflow_id": 342691463,
            "head_sha": ARMED_RECOVERY_SOURCE, "head_branch": "main",
            "path": ".github/workflows/v03-real-dogfood-scenario.yml",
            "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
        }
        if (
            any(value.get(k) != v for k, v in expected.items())
            or any(type(value.get(k)) is not int for k in ("id", "run_attempt", "workflow_id"))
            or str((value.get("repository") or {}).get("full_name") or "").lower()
               != "dream-xin/ai-sdlc"
            or not value.get("updated_at")
        ):
            raise V03DogfoodRuntimeDriverError("armed recovery failed run/attempt drifted")
        return {**expected, "updated_at": value["updated_at"]}
    before = run()
    for path, sha in ARMED_RECOVERY_SOURCE_BLOBS.items():
        source(path, ARMED_RECOVERY_SOURCE, sha)
    source(RECOVERY_AUTHORIZATION_PATH, ARMED_RECOVERY_STORE, ARMED_RECOVERY_AUTHORIZATION_BLOB)
    source(RECOVERY_ATTEMPT_PATH, ARMED_RECOVERY_STORE, ARMED_RECOVERY_ATTEMPT_BLOB)
    job = get("/actions/jobs/113696529763")
    expected_job = {
        "id": 113696529763, "run_id": 37892560162, "run_attempt": 1,
        "head_sha": ARMED_RECOVERY_SOURCE, "status": "completed", "conclusion": "failure",
        "name": "dogfood",
    }
    if (any(job.get(k) != v for k, v in expected_job.items())
            or any(type(job.get(k)) is not int for k in ("id", "run_id", "run_attempt"))):
        raise V03DogfoodRuntimeDriverError("armed recovery failed job identity drifted")
    transport = preflight.composition.actions_transport
    status, _, raw = transport.http(
        method="GET", url=transport._api("/actions/jobs/113696529763/logs"),
        token=transport.config.token, body=None,
    )
    if status != 200:
        raise V03DogfoodRuntimeDriverError("armed recovery traceback observation unavailable")
    try:
        log = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise V03DogfoodRuntimeDriverError("armed recovery traceback is malformed") from exc
    required = (
        'v03_dogfood_runtime_driver.py", line 1543, in recover_historical_prehttp_attempt',
        "before = preflight.composition.recovery_dispatch_gateway.lookup(",
        'operator_vertical_gh_aw_actions_transport.py", line 132, in _validate_lookup_identity',
        "operator_vertical.VerticalInvariantError: invalid stable external dispatch key",
    )
    if any(marker not in log for marker in required) or before != run():
        raise V03DogfoodRuntimeDriverError("armed recovery source-bound pre-HTTP traceback drifted")
    return json.loads(canonical_json(proof))


class _RecoveryReadOnlyResult(RuntimeError):
    def __init__(self, result):
        self.result = result


def _commit_recovery_nonempty(runtime, planner):
    """Use protected replan reads, without turning replay into an empty write."""
    def guarded(snapshot):
        plan = planner(snapshot)
        if not plan.mutations:
            raise _RecoveryReadOnlyResult(plan.result)
        return plan
    try:
        return runtime.commit_replanned(guarded).result
    except _RecoveryReadOnlyResult as replay:
        return replay.result


def _plan_bounded_recovery(snapshot: Any, *, preflight: Any, fence: Mapping[str, Any], proof: Mapping[str, Any]) -> StoreMutationPlan:
    _bounded_recovery_identity(snapshot, preflight)
    authorization, attempt = validate_armed_recovery_pair(snapshot)
    if (
        proof != ARMED_RECOVERY_NO_HTTP_PROOF
        or fence["fence_digest"] != authorization["provider_fence_digest"]
        or _recovery_worker_blobs() != authorization["worker_blobs"]
    ):
        raise V03DogfoodRuntimeDriverError("armed recovery continuation proof/fence/worker bytes drifted")
    existing = snapshot.get(RECOVERY_CONTINUATION_PATH)
    if existing is not None:
        _, _, continuation = validate_recovery_continuation(snapshot)
        return StoreMutationPlan(snapshot.ref_sha, tuple(), {
            "acquired": False, "authorization": authorization, "continuation": continuation,
        })
    if snapshot.get(RECOVERY_RECEIPT_PATH) is not None:
        raise V03DogfoodRuntimeDriverError("recovery receipt exists without a continuation")
    continuation = {
        "schema_version": RECOVERY_CONTINUATION_SCHEMA, "admission_version": 1, "status": "ARMED",
        "collector_dispatch_id": RECOVERY_COLLECTOR_DISPATCH_ID,
        "authorization_digest": "sha256:" + digest_json(authorization),
        "create_attempt_digest": "sha256:" + digest_json(attempt),
        "original_source_head_sha": ARMED_RECOVERY_SOURCE,
        "no_http_proof": dict(proof),
        "no_http_proof_digest": "sha256:" + digest_json(proof),
        **recovery_execution_binding(preflight.composition.policy_authority),
        "execution_trusted_context_digest": preflight.trusted_context_digest,
        "created_at": preflight.composition.runtime.clock(),
    }
    for key in (
        "operation_id", "operation_generation", "semantic_effect_key", "external_dispatch_key",
        "recovery_dispatch_key", "recovery_dispatch_id", "workflow_file", "feature_id",
        "target_repository", "target_ref", "task_id", "task_identity", "stage", "role",
        "expected_revision", "candidate_pr_number", "candidate_head_sha",
        "provider_fence_digest", "historical_observation_digest", "worker_blobs",
    ):
        continuation[key] = authorization[key]
    from copy import deepcopy
    proposed = deepcopy(snapshot)
    proposed.files[RECOVERY_CONTINUATION_PATH] = continuation
    validate_recovery_continuation(proposed)
    # Validate the actual production gateway payload and transport before the
    # irreversible claim, while the transport is strictly lookup-only.
    gateway = preflight.composition.recovery_dispatch_gateway
    gateway.transport.admit_continuation(
        proposed, allow_post=False,
        **recovery_execution_binding(preflight.composition.policy_authority),
        execution_trusted_context_digest=preflight.trusted_context_digest,
    )
    dispatch = _bounded_recovery_dispatch(preflight, authorization)
    gateway.transport._validate_dispatch_inputs(
        workflow=RECOVERY_WORKFLOW, ref="main", inputs=gateway._inputs(dispatch),
    )
    return StoreMutationPlan(snapshot.ref_sha, (
        StoreMutation("create_immutable", RECOVERY_CONTINUATION_PATH, continuation),
    ), {"acquired": True, "authorization": authorization, "continuation": continuation})


def _bounded_recovery_dispatch(preflight: Any, authorization: Mapping[str, Any]) -> dict[str, Any]:
    h = HISTORICAL_PREHTTP_RECOVERY
    return {
        "operation_id": h["operation_id"],
        "operation_generation": h["generation"],
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": authorization["recovery_dispatch_key"],
        "dispatch_id": authorization["recovery_dispatch_id"],
        "target_repository": preflight.execution.repository,
        "target_ref": h["target_ref"],
        "feature_id": h["feature_id"],
        "expected_revision": 1,
        "feature_stage": h["stage"],
        "task_id": h["task_id"],
        "task_identity": h["task_identity"],
        "role": h["role"],
        "candidate_pr_number": h["candidate_pr_number"],
        "candidate_head_sha": h["candidate_head_sha"],
    }


def _recovery_trusted_context(authorization: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": authorization["operation_id"],
        "operation_generation": authorization["operation_generation"],
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": authorization["semantic_effect_key"],
        "external_dispatch_key": authorization["recovery_dispatch_key"],
        "dispatch_id": authorization["recovery_dispatch_id"],
        "target_repository": authorization["target_repository"],
        "target_ref": authorization["target_ref"],
        "feature_id": authorization["feature_id"],
        "expected_revision": authorization["expected_revision"],
        "feature_stage": authorization["stage"],
        "role": authorization["role"],
        "task_id": authorization["task_id"],
        "launch_candidate_head_sha": authorization["candidate_head_sha"],
        "source_head_sha": authorization["source_head_sha"],
    }


def _resolve_recovery_run_for_seal(preflight: Any, *, authorization: Mapping[str, Any], receipt_id: str) -> Any:
    source = preflight.composition.recovery_result_source
    continuation = recovery_route(preflight.composition.runtime.backend.read_snapshot())["bridge"]
    if any(continuation.get(k) != v for k, v in recovery_execution_binding(
        preflight.composition.policy_authority,
    ).items()):
        raise V03DogfoodRuntimeDriverError("recovery resolver escaped current trusted execution authority")
    execution_source = continuation["execution_source_head_sha"]
    key = str(authorization["recovery_dispatch_key"])
    for _poll in range(121):
        readiness = source.seal_readiness(
            external_dispatch_key=key,
            expected_receipt_identity=receipt_id,
            source_head_sha=execution_source,
        )
        if readiness == "READY":
            resolved = source.resolve(
                external_dispatch_key=key,
                expected_receipt_identity=receipt_id,
                trusted_context={**_recovery_trusted_context(authorization), "source_head_sha": execution_source,
                                 "dispatch_id": RECOVERY_COLLECTOR_DISPATCH_ID,
                                 "execution_dispatch_id": authorization["recovery_dispatch_id"]},
            )
            if (
                resolved.run.run_id != int(receipt_id)
                or resolved.run.receipt_identity != receipt_id
                or resolved.run.workflow_file != RECOVERY_WORKFLOW
                or resolved.run.workflow_ref != "main"
                or resolved.run.event != "workflow_dispatch"
                or resolved.run.status != "completed"
                or resolved.run.conclusion != "success"
                or resolved.run.display_title != authorization["display_title"]
                or resolved.run.external_dispatch_key != key
                or resolved.run.role != authorization["role"]
                or resolved.run.task_id != authorization["task_id"]
                or resolved.run.worker_identity
                   != f"gh-aw:{RECOVERY_WORKFLOW}@{execution_source}"
                or not isinstance(resolved.run.candidate_pr_number, int)
                or resolved.run.candidate_pr_number < 1
                or not str(resolved.run.candidate_head_sha or "")
                or len(resolved.outputs) != 1
                or resolved.outputs[0].label != "implementation"
                or resolved.outputs[0].kind != "artifact"
            ):
                raise V03DogfoodRuntimeDriverError("recovery successful run/Safe Output binding is incomplete")
            return resolved
        if readiness != "PENDING":
            raise V03DogfoodRuntimeDriverError("recovery run seal readiness is invalid")
        time.sleep(10)
    raise V03DogfoodRuntimeDriverError("recovery run did not complete within bounded seal wait")


def _seal_recovery_receipt(preflight: Any, *, authorization: Mapping[str, Any], receipt: Mapping[str, Any], resolved: Any) -> dict[str, Any]:
    receipt_id = str(receipt.get("receipt_id") or "")
    if (
        receipt.get("lookup_state") != "LAUNCHED"
        or not receipt_id.isdigit()
        or resolved.run.run_id != int(receipt_id)
    ):
        raise V03DogfoodRuntimeDriverError("bounded recovery lacks one exact successful launched receipt")
    snapshot = preflight.composition.runtime.backend.read_snapshot()
    _validate_recovery_pair(snapshot, authorization)
    route = recovery_route(snapshot)
    continuation, attempt = route["bridge"], route["attempt"]
    if not isinstance(attempt, dict):
        raise V03DogfoodRuntimeDriverError("bounded recovery create-attempt disappeared before seal")
    expected = {
        "schema_version": RECOVERY_SCHEMA,
        "operation_id": authorization["operation_id"],
        "operation_generation": authorization["operation_generation"],
        "semantic_effect_key": authorization["semantic_effect_key"],
        "external_dispatch_key": authorization["external_dispatch_key"],
        "historical_attempt_id": authorization["historical_attempt_id"],
        "historical_runtime_receipt_identity": authorization["historical_runtime_receipt_identity"],
        "historical_observation_digest": authorization["historical_observation_digest"],
        "provider_fence_digest": authorization["provider_fence_digest"],
        "authorization_digest": "sha256:" + digest_json(authorization),
        "create_attempt_digest": "sha256:" + digest_json(attempt),
        "recovery_dispatch_key": authorization["recovery_dispatch_key"],
        "recovery_dispatch_id": authorization["recovery_dispatch_id"],
        "receipt_id": receipt_id,
        "workflow_file": RECOVERY_WORKFLOW,
        "installation_commit_sha": authorization["installation_commit_sha"],
        "source_head_sha": authorization["source_head_sha"],
        "target_repository": authorization["target_repository"],
        "head_branch": authorization["head_branch"],
        "event": authorization["event"],
        "display_title": authorization["display_title"],
        "run_attempt": 1,
        "trusted_context_digest": authorization["trusted_context_digest"],
        "feature_id": authorization["feature_id"],
        "target_ref": authorization["target_ref"],
        "task_id": authorization["task_id"],
        "task_identity": authorization["task_identity"],
        "expected_revision": authorization["expected_revision"],
        "candidate_pr_number": authorization["candidate_pr_number"],
        "candidate_head_sha": authorization["candidate_head_sha"],
        "worker_blobs": authorization["worker_blobs"],
        "role": authorization["role"],
        "stage": authorization["stage"],
        "run_status": resolved.run.status,
        "run_conclusion": resolved.run.conclusion,
        "output_candidate_pr_number": resolved.run.candidate_pr_number,
        "output_candidate_head_sha": resolved.run.candidate_head_sha,
        "safe_output_artifact_proof": preflight.composition.recovery_result_source.safe_output_proof(run_id=resolved.run.run_id),
        "safe_output_artifact_digest": "sha256:" + digest_json(preflight.composition.recovery_result_source.safe_output_proof(run_id=resolved.run.run_id)),
        "safe_output_uri": resolved.outputs[0].trusted_uri,
        "safe_output_digest": "sha256:" + digest_json({"trusted_uri": resolved.outputs[0].trusted_uri}),
        "resolved_run_digest": "sha256:" + digest_json({
            "run_id": resolved.run.run_id,
            "receipt_identity": resolved.run.receipt_identity,
            "workflow_file": resolved.run.workflow_file,
            "workflow_ref": resolved.run.workflow_ref,
            "event": resolved.run.event,
            "status": resolved.run.status,
            "conclusion": resolved.run.conclusion,
            "display_title": resolved.run.display_title,
            "external_dispatch_key": resolved.run.external_dispatch_key,
            "role": resolved.run.role,
            "task_id": resolved.run.task_id,
            "worker_identity": resolved.run.worker_identity,
            "candidate_pr_number": resolved.run.candidate_pr_number,
            "candidate_head_sha": resolved.run.candidate_head_sha,
        }),
        "continuation_digest": "sha256:" + digest_json(continuation),
        "collector_dispatch_id": RECOVERY_COLLECTOR_DISPATCH_ID,
        **recovery_execution_binding(preflight.composition.policy_authority),
        "execution_trusted_context_digest": continuation["execution_trusted_context_digest"],
        "sealed_at": preflight.composition.runtime.clock(),
    }
    def plan(snapshot: Any) -> StoreMutationPlan:
        _bounded_recovery_identity(snapshot, preflight)
        _validate_recovery_pair(snapshot, authorization)
        validate_recovery_execution_seal(snapshot, expected, execution_binding=recovery_execution_binding(
            preflight.composition.policy_authority,
        ))
        existing = snapshot.get(route["receipt_path"])
        if existing is not None:
            stable = {key: value for key, value in expected.items() if key != "sealed_at"}
            if not isinstance(existing, dict) or any(existing.get(key) != value for key, value in stable.items()):
                raise V03DogfoodRuntimeDriverError("bounded recovery sealed receipt conflicts")
            return StoreMutationPlan(snapshot.ref_sha, tuple(), {"receipt": existing})
        return StoreMutationPlan(
            snapshot.ref_sha,
            (StoreMutation("create_immutable", route["receipt_path"], expected),),
            {"receipt": expected},
        )
    return _commit_recovery_nonempty(preflight.composition.runtime, plan)["receipt"]



def _require_recovery_execution_source(preflight: Any, source_sha: str) -> None:
    ref = _github_json(preflight, "/git/ref/heads/main")
    if (
        ref.get("ref") != "refs/heads/main"
        or (ref.get("object") or {}).get("type") != "commit"
        or (ref.get("object") or {}).get("sha") != source_sha
    ):
        raise V03DogfoodRuntimeDriverError("recovery execution main source changed before admission/POST")


def recover_historical_prehttp_attempt(preflight: Any) -> dict[str, Any] | None:
    """Use one provider-fenced, protected-CAS recovery; never rerun attempt 1."""
    h = HISTORICAL_PREHTTP_RECOVERY
    if preflight.slot.scenario != h["scenario"]:
        return None
    observation = observe_historical_worker_for_review(
        actions_read_token=_required(os.environ, "AI_SDLC_ACTIONS_READ_TOKEN")
    )
    if observation.get("observation_digest") != RECOVERY_OBSERVATION_DIGEST:
        raise V03DogfoodRuntimeDriverError("historical immutable observation digest drifted")
    fence = _observe_provider_rotation(os.environ)
    proof = _observe_armed_recovery_no_http(preflight)
    _require_recovery_execution_source(preflight, preflight.execution.installation_commit_sha)
    result = _commit_recovery_nonempty(preflight.composition.runtime,
        lambda snapshot: _plan_bounded_recovery(snapshot, preflight=preflight, fence=fence, proof=proof)
    )
    authorization = result["authorization"]
    key = str(authorization["recovery_dispatch_key"])
    admitted_snapshot = preflight.composition.runtime.backend.read_snapshot()
    preflight.composition.recovery_dispatch_gateway.transport.admit_continuation(
        admitted_snapshot, allow_post=result.get("acquired") is True,
        **recovery_execution_binding(preflight.composition.policy_authority),
        execution_trusted_context_digest=preflight.trusted_context_digest,
    )
    existing = admitted_snapshot.get(RECOVERY_RECEIPT_PATH)
    if existing is not None:
        if not isinstance(existing, dict) or not str(existing.get("receipt_id") or "").isdigit():
            raise V03DogfoodRuntimeDriverError("existing recovery receipt is malformed before model execution")
        validate_recovery_execution_seal(admitted_snapshot, existing, execution_binding=recovery_execution_binding(
            preflight.composition.policy_authority,
        ))
        resolved = _resolve_recovery_run_for_seal(
            preflight, authorization=authorization, receipt_id=str(existing["receipt_id"])
        )
        return _seal_recovery_receipt(
            preflight,
            authorization=authorization,
            receipt={"lookup_state": "LAUNCHED", "receipt_id": str(existing["receipt_id"])},
            resolved=resolved,
        )

    before = preflight.composition.recovery_dispatch_gateway.lookup(
        external_dispatch_key=key
    )
    if result.get("acquired") is not True:
        if isinstance(before, dict) and before.get("lookup_state") == "LAUNCHED":
            receipt_id = str(before.get("receipt_id") or "")
            resolved = _resolve_recovery_run_for_seal(
                preflight, authorization=authorization, receipt_id=receipt_id
            )
            return _seal_recovery_receipt(
                preflight, authorization=authorization, receipt=before, resolved=resolved
            )
        raise V03DogfoodRuntimeDriverError(
            "bounded recovery attempt already armed; zero or ambiguous receipt forbids another POST"
        )
    if not isinstance(before, dict) or before.get("lookup_state") != "NOT_LAUNCHED":
        raise V03DogfoodRuntimeDriverError("bounded recovery pre-POST lookup is not exhaustive NOT_LAUNCHED")
    _require_recovery_execution_source(preflight, result["continuation"]["execution_source_head_sha"])
    dispatch = _bounded_recovery_dispatch(preflight, authorization)
    try:
        receipt = preflight.composition.recovery_dispatch_gateway.launch(dispatch=dispatch)
    except Exception:
        receipt = preflight.composition.recovery_dispatch_gateway.lookup(external_dispatch_key=key)
    receipt_id = str(receipt.get("receipt_id") or "") if isinstance(receipt, dict) else ""
    resolved = _resolve_recovery_run_for_seal(
        preflight, authorization=authorization, receipt_id=receipt_id
    )
    return _seal_recovery_receipt(
        preflight, authorization=authorization, receipt=receipt, resolved=resolved
    )


def _observe_failed_replacement_predecessor(preflight):
    """Fresh, bracketed read-only observation; failure is never success evidence."""
    expected = REPLACEMENT_ADMISSION
    path = f"/actions/runs/{REPLACEMENT_FAILED_RUN}"
    def run():
        row = _github_json(preflight, path)
        exact = {"id": REPLACEMENT_FAILED_RUN, "run_attempt": 1,
                 "head_sha": REPLACEMENT_FAILED_SOURCE, "head_branch": "main",
                 "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
                 "display_title": "AI-SDLC gh-aw " + ARMED_RECOVERY_KEY,
                 "path": ".github/workflows/" + RECOVERY_WORKFLOW}
        if (any(row.get(k) != v for k, v in exact.items())
                or type(row.get("id")) is not int or type(row.get("run_attempt")) is not int
                or str((row.get("repository") or {}).get("full_name") or "").lower() != "dream-xin/ai-sdlc"):
            raise V03DogfoodRuntimeDriverError("replacement predecessor is no longer exact terminal failed attempt one")
        return row
    before = run()
    jobs = _github_json(preflight, path + "/attempts/1/jobs?per_page=100")
    rows = jobs.get("jobs")
    if (not isinstance(rows, list) or type(jobs.get("total_count")) is not int
            or jobs["total_count"] != len(rows) or len(rows) != len(expected["failed_jobs"])):
        raise V03DogfoodRuntimeDriverError("replacement predecessor jobs are not exhaustive")
    for name, job_id in expected["failed_jobs"].items():
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
        if (len(matches) != 1 or matches[0].get("id") != job_id
                or type(matches[0].get("id")) is not int
                or type(matches[0].get("run_attempt")) is not int or matches[0]["run_attempt"] != 1
                or matches[0].get("run_id") != REPLACEMENT_FAILED_RUN
                or matches[0].get("head_sha") != REPLACEMENT_FAILED_SOURCE
                or matches[0].get("status") != "completed"
                or matches[0].get("conclusion") != ("failure" if name == "conclusion" else "success")):
            raise V03DogfoodRuntimeDriverError("replacement predecessor job identity/terminal state drifted")
    pr = _github_json(preflight, f"/pulls/{REPLACEMENT_FAILED_PR}")
    if (pr.get("number") != REPLACEMENT_FAILED_PR or pr.get("state") != "open" or pr.get("draft") is not True
            or (pr.get("head") or {}).get("sha") != REPLACEMENT_FAILED_HEAD
            or (pr.get("head") or {}).get("ref") != "gh-aw/F-OPERATOR-V03-DOGFOOD-HAPPY-0001-37897902667-v1-c1a21d03a1e716db"
            or (pr.get("base") or {}).get("sha") != HISTORICAL_PREHTTP_RECOVERY["candidate_head_sha"]
            or (pr.get("base") or {}).get("ref") != HISTORICAL_PREHTTP_RECOVERY["target_ref"]
            or any(str(((pr.get(side) or {}).get("repo") or {}).get("full_name") or "").lower() != "dream-xin/ai-sdlc"
                   for side in ("head", "base"))):
        raise V03DogfoodRuntimeDriverError("retained failed-origin draft PR changed")
    listing = _github_json(preflight, path + "/artifacts?per_page=100")
    artifacts = listing.get("artifacts")
    if (not isinstance(artifacts, list) or type(listing.get("total_count")) is not int
            or listing["total_count"] != len(artifacts) or len(artifacts) > 100):
        raise V03DogfoodRuntimeDriverError("failed-origin artifact listing is incomplete")
    matches = [row for row in artifacts if isinstance(row, dict) and row.get("name") == "safe-outputs-items"]
    if len(matches) != 1:
        raise V03DogfoodRuntimeDriverError("failed-origin artifact is missing or ambiguous")
    artifact = matches[0]
    if (type(artifact.get("id")) is not int or artifact["id"] != expected["failed_artifact_id"]
            or artifact.get("digest") != expected["failed_artifact_digest"] or artifact.get("expired") is not False
            or any((artifact.get("workflow_run") or {}).get(k) != v for k, v in {
                "id": REPLACEMENT_FAILED_RUN, "head_sha": REPLACEMENT_FAILED_SOURCE, "head_branch": "main",
                "repository_id": 1326302284, "head_repository_id": 1326302284}.items())):
        raise V03DogfoodRuntimeDriverError("failed-origin Safe Output artifact changed")
    if canonical_json(before) != canonical_json(run()):
        raise V03DogfoodRuntimeDriverError("failed predecessor changed while inspected")
    return dict(REPLACEMENT_FAILED_OBSERVATION)


def _replacement_authorization(snapshot, preflight):
    original, attempt, continuation = validate_replacement_predecessor(snapshot)
    binding = recovery_execution_binding(preflight.composition.policy_authority)
    authorization = dict(original,
        replacement_admission=REPLACEMENT_ADMISSION,
        replacement_admission_digest="sha256:" + digest_json(REPLACEMENT_ADMISSION),
        predecessor_authorization_digest="sha256:" + digest_json(original),
        predecessor_attempt_digest="sha256:" + digest_json(attempt),
        predecessor_continuation_digest="sha256:" + digest_json(continuation),
        worker_blobs=REPLACEMENT_WORKER_BLOBS,
        collector_dispatch_id=RECOVERY_COLLECTOR_DISPATCH_ID,
        failed_observation=REPLACEMENT_FAILED_OBSERVATION,
        observed_accounting=REPLACEMENT_ACCOUNTING, observed_accounting_digest=REPLACEMENT_ACCOUNTING_DIGEST,
        observed_accounting_uri=REPLACEMENT_ACCOUNTING_URI,
        source_head_sha=binding["execution_source_head_sha"],
        installation_commit_sha=binding["execution_source_head_sha"],
        trusted_context_digest=preflight.trusted_context_digest,
        execution_trusted_context_digest=preflight.trusted_context_digest,
        created_at=preflight.composition.runtime.clock(),
        **binding)
    identity = replacement_authorization_identity(authorization)
    authorization["recovery_dispatch_key"] = "dispatch-" + digest_json(identity)[:40]
    authorization["recovery_dispatch_id"] = "replacement-1-" + digest_json(identity)[:32]
    authorization["display_title"] = "AI-SDLC gh-aw " + authorization["recovery_dispatch_key"]
    dispatch = _bounded_recovery_dispatch(preflight, authorization)
    authorization["task_payload_digest"] = "sha256:" + digest_json(json.loads(
        GhAwVerticalRoleDispatchGateway._task_payload(dispatch)))
    return authorization


def _plan_fixed_replacement(snapshot, *, preflight):
    from copy import deepcopy
    if replacement_present(snapshot):
        authorization, claim, _ = validate_replacement_chain(snapshot)
        return StoreMutationPlan(snapshot.ref_sha, (), {"acquired": False, "authorization": authorization, "continuation": claim})
    _bounded_recovery_identity(snapshot, preflight)
    validate_replacement_predecessor(snapshot)
    prefix = f"state/operator/v1/operations/{HISTORICAL_PREHTTP_RECOVERY['operation_id']}/dogfood-candidate-handoffs/"
    if any(path.startswith(prefix) for path in snapshot.files):
        raise V03DogfoodRuntimeDriverError("replacement cannot follow any candidate handoff")
    if _recovery_worker_blobs() != REPLACEMENT_WORKER_BLOBS:
        raise V03DogfoodRuntimeDriverError("replacement Worker bytes differ from exact reviewed source")
    gateway = preflight.composition.recovery_dispatch_gateway
    binding = dict(recovery_execution_binding(preflight.composition.policy_authority),
                   execution_trusted_context_digest=preflight.trusted_context_digest)
    gateway.transport.admit_continuation(snapshot, allow_post=False, **binding)
    gateway.transport._validate_lookup_identity(workflow=RECOVERY_WORKFLOW, ref="main", dispatch_key=ARMED_RECOVERY_KEY)
    _require_recovery_execution_source(preflight, preflight.execution.installation_commit_sha)
    if _observe_failed_replacement_predecessor(preflight) != REPLACEMENT_FAILED_OBSERVATION:
        raise V03DogfoodRuntimeDriverError("replacement failed observation drifted")
    old = gateway.lookup(external_dispatch_key=ARMED_RECOVERY_KEY)
    if old.get("lookup_state") != "LAUNCHED" or str(old.get("receipt_id")) != str(REPLACEMENT_FAILED_RUN):
        raise V03DogfoodRuntimeDriverError("replacement predecessor global scan is not uniquely terminal")
    authorization = _replacement_authorization(snapshot, preflight)
    claim = dict(authorization, authorization_digest="sha256:" + digest_json(authorization),
                 attempt_id="replacement-1-claim-" + digest_json(authorization)[:32], status="ARMED")
    proposed = deepcopy(snapshot)
    proposed.files[REPLACEMENT_AUTHORIZATION_PATH] = authorization
    proposed.files[REPLACEMENT_ATTEMPT_PATH] = claim
    validate_replacement_chain(proposed)
    gateway.transport.admit_continuation(proposed, allow_post=False, **binding)
    dispatch = _bounded_recovery_dispatch(preflight, authorization)
    gateway.transport._validate_dispatch_inputs(workflow=RECOVERY_WORKFLOW, ref="main", inputs=gateway._inputs(dispatch))
    observed = gateway.lookup(external_dispatch_key=authorization["recovery_dispatch_key"])
    if observed.get("lookup_state") != "NOT_LAUNCHED":
        raise V03DogfoodRuntimeDriverError("replacement preclaim scan is not exhaustive absence")
    return StoreMutationPlan(snapshot.ref_sha, (
        StoreMutation("create_immutable", REPLACEMENT_AUTHORIZATION_PATH, authorization),
        StoreMutation("create_immutable", REPLACEMENT_ATTEMPT_PATH, claim),
    ), {"acquired": True, "authorization": authorization, "continuation": claim})


def _replacement_prepost_old_scan(preflight, snapshot):
    from copy import deepcopy
    gateway = preflight.composition.recovery_dispatch_gateway
    historical = deepcopy(snapshot)
    for path in REPLACEMENT_PATHS:
        historical.files.pop(path, None)
    binding = dict(recovery_execution_binding(preflight.composition.policy_authority),
                   execution_trusted_context_digest=preflight.trusted_context_digest)
    gateway.transport.admit_continuation(historical, allow_post=False, **binding)
    old = gateway.lookup(external_dispatch_key=ARMED_RECOVERY_KEY)
    if old.get("lookup_state") != "LAUNCHED" or str(old.get("receipt_id")) != str(REPLACEMENT_FAILED_RUN):
        raise V03DogfoodRuntimeDriverError("replacement final predecessor scan is ambiguous")
    _observe_failed_replacement_predecessor(preflight)
    # Restoring the already-won process-local capability is not a new claim.
    gateway.transport.admit_continuation(snapshot, allow_post=True, **binding)



def _reviewer_worker_blobs():
    from v03_dogfood_live_gate import CURRENT_DOGFOOD_WORKFLOWS
    root = Path(__file__).resolve().parents[1]
    return {".github/workflows/" + name: _git_blob_sha(root / ".github/workflows" / name)
            for workflow in CURRENT_DOGFOOD_WORKFLOWS.values()
            for name in (workflow, workflow.replace(".lock.yml", ".md"))}


def _observe_reviewer_pre_model_failure(preflight):
    from v03_dogfood_full_composition import (
        REVIEWER_FAILED_RUN, REVIEWER_PREDECESSOR_SOURCE, REVIEWER_OLD_WORKFLOW,
        REVIEWER_OLD_KEY, REVIEWER_FAILURE_JOBS, REVIEWER_FAILURE_WORKER_BLOBS)
    expected_run = {"id": REVIEWER_FAILED_RUN, "run_attempt": 1, "workflow_id": 372854672,
        "head_sha": REVIEWER_PREDECESSOR_SOURCE, "head_branch": "main", "event": "workflow_dispatch",
        "path": ".github/workflows/" + REVIEWER_OLD_WORKFLOW, "status": "completed", "conclusion": "failure",
        "display_title": "AI-SDLC gh-aw " + REVIEWER_OLD_KEY}
    before = _github_json(preflight, f"/actions/runs/{REVIEWER_FAILED_RUN}")
    if (any(before.get(k) != v for k, v in expected_run.items())
            or any(type(before.get(k)) is not int for k in ("id", "run_attempt", "workflow_id"))):
        raise V03DogfoodRuntimeDriverError("Reviewer predecessor run is not exact failed attempt one")
    jobs_path = f"/actions/runs/{REVIEWER_FAILED_RUN}/attempts/1/jobs?per_page=100"
    jobs = _github_json(preflight, jobs_path)
    rows = jobs.get("jobs")
    if (not isinstance(rows, list) or jobs.get("total_count") != len(rows) or type(jobs.get("total_count")) is not int
            or len(rows) != len(REVIEWER_FAILURE_JOBS)):
        raise V03DogfoodRuntimeDriverError("Reviewer predecessor job listing is not exact and exhaustive")
    normalized = []
    conclusions = {"activation": "success", "agent": "failure", "detection": "success",
                   "safe_outputs": "failure", "conclusion": "failure"}
    for name, job_id in REVIEWER_FAILURE_JOBS.items():
        selected = [row for row in rows if row.get("name") == name]
        if (len(selected) != 1 or selected[0].get("id") != job_id or type(selected[0].get("id")) is not int
                or selected[0].get("run_id") != REVIEWER_FAILED_RUN
                or selected[0].get("run_attempt") != 1 or type(selected[0].get("run_attempt")) is not int
                or selected[0].get("head_sha") != REVIEWER_PREDECESSOR_SOURCE
                or selected[0].get("status") != "completed" or selected[0].get("conclusion") != conclusions[name]
                or not isinstance(selected[0].get("steps"), list)):
            raise V03DogfoodRuntimeDriverError("Reviewer predecessor terminal jobs changed")
        row = selected[0]
        steps = row["steps"]
        required = {
            "agent": {"Generate GitHub App token for checkout (0)": "failure",
                      "Checkout repository": "skipped", "Checkout dream-xin/ai-sdlc": "skipped",
                      "Initialize agent execution evidence": "skipped", "Execute GitHub Copilot CLI": "skipped"},
            "detection": {"Execute threat detection with AWF": "skipped"},
            "safe_outputs": {"Generate GitHub App token": "failure", "Process Safe Outputs": "skipped"},
        }.get(name, {})
        for label, conclusion in required.items():
            found = [step for step in steps if step.get("name") == label]
            if len(found) != 1 or found[0].get("status") != "completed" or found[0].get("conclusion") != conclusion:
                raise V03DogfoodRuntimeDriverError("Reviewer predecessor model/effect execution is not absent")
        normalized.append({"name": name, "id": job_id, "conclusion": conclusions[name],
                           "steps": [{k: step.get(k) for k in ("number", "name", "status", "conclusion")} for step in steps]})
    for path, blob in REVIEWER_FAILURE_WORKER_BLOBS.items():
        document = _github_json(preflight, "/contents/" + path + "?ref=" + REVIEWER_PREDECESSOR_SOURCE)
        if document.get("sha") != blob or document.get("encoding") != "base64":
            raise V03DogfoodRuntimeDriverError("Reviewer predecessor source proof differs")
        raw = base64.b64decode(document.get("content", ""), validate=False)
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
        if actual != blob:
            raise V03DogfoodRuntimeDriverError("Reviewer predecessor source bytes differ")
    transport = preflight.composition.actions_transport
    status, _, raw = transport.http(method="GET",
        url=transport._api(f"/actions/jobs/{REVIEWER_FAILURE_JOBS['agent']}/logs"),
        token=transport.config.token, body=None)
    diagnostic = "The 'private-key' input must be set to a non-empty string."
    if status != 200 or diagnostic not in raw.decode("utf-8", errors="strict"):
        raise V03DogfoodRuntimeDriverError("Reviewer predecessor exact pre-model diagnostic is missing")
    artifacts = _github_json(preflight, f"/actions/runs/{REVIEWER_FAILED_RUN}/artifacts?per_page=100")
    artifact_rows = artifacts.get("artifacts")
    if (not isinstance(artifact_rows, list) or type(artifacts.get("total_count")) is not int
            or artifacts["total_count"] != len(artifact_rows) or len(artifact_rows) > 100
            or any(row.get("name") == "safe-outputs-items" for row in artifact_rows)):
        raise V03DogfoodRuntimeDriverError("Reviewer predecessor has unknown or contradictory output artifacts")
    after = _github_json(preflight, f"/actions/runs/{REVIEWER_FAILED_RUN}")
    if (any(after.get(k) != before.get(k) for k in (*expected_run, "updated_at"))
            or _github_json(preflight, jobs_path) != jobs):
        raise V03DogfoodRuntimeDriverError("Reviewer predecessor changed during proof")
    material = {"run": expected_run, "updated_at": before.get("updated_at"), "jobs": normalized,
                "source_blobs": REVIEWER_FAILURE_WORKER_BLOBS, "diagnostic_digest": hashlib.sha256(raw).hexdigest(),
                "artifact_ids": sorted(row["id"] for row in artifact_rows)}
    return {"schema_version": "ai-sdlc.v03-reviewer-pre-model-failure/v1",
            "run_id": REVIEWER_FAILED_RUN, "run_attempt": 1, "source_head_sha": REVIEWER_PREDECESSOR_SOURCE,
            "jobs": REVIEWER_FAILURE_JOBS, "model_executed": False, "safe_outputs_processed": False,
            "semantic_safety_pass": False, "observation_digest": "sha256:" + digest_json(material)}




def _observe_reviewer_post_model_failure(preflight):
    from v03_dogfood_full_composition import (
        REVIEWER_POST_MODEL_FAILED_RUN,REVIEWER_POST_MODEL_SOURCE,REVIEWER_POST_MODEL_FAILED_KEY,
        REVIEWER_POST_MODEL_FAILURE_JOBS,REVIEWER_NEW_WORKFLOW)
    from v03_dogfood_live_gate import HISTORICAL_RELEASE_DOGFOOD_BLOBS
    from v03_dogfood_full_composition import REVIEWER_AUTH_PATH
    historical=preflight.composition.runtime.backend.read_snapshot().get(REVIEWER_AUTH_PATH)
    original_failure=_observe_reviewer_pre_model_failure(preflight)
    if (not isinstance(historical,dict)
            or canonical_json(original_failure)!=canonical_json(historical.get("pre_model_failure_proof"))):
        raise V03DogfoodRuntimeDriverError("original Reviewer failed attempt proof changed")
    run_id=REVIEWER_POST_MODEL_FAILED_RUN
    expected={"id":run_id,"run_attempt":1,"workflow_id":379567834,
        "head_sha":REVIEWER_POST_MODEL_SOURCE,"head_branch":"main","event":"workflow_dispatch",
        "path":".github/workflows/"+REVIEWER_NEW_WORKFLOW,"status":"completed","conclusion":"failure",
        "display_title":"AI-SDLC gh-aw "+REVIEWER_POST_MODEL_FAILED_KEY}
    before=_github_json(preflight,f"/actions/runs/{run_id}")
    if (canonical_json({k:before.get(k) for k in expected})!=canonical_json(expected)):
        raise V03DogfoodRuntimeDriverError("post-model Reviewer is not exact failed attempt one")
    path=f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100"
    jobs=_github_json(preflight,path);rows=jobs.get("jobs")
    if (not isinstance(rows,list) or type(jobs.get("total_count")) is not int
            or jobs["total_count"]!=5 or len(rows)!=5):
        raise V03DogfoodRuntimeDriverError("post-model Reviewer jobs are not exhaustive")
    outcomes={"activation":"success","agent":"success","detection":"failure","safe_outputs":"skipped","conclusion":"failure"}
    normalized=[]
    for name,job_id in REVIEWER_POST_MODEL_FAILURE_JOBS.items():
        selected=[r for r in rows if r.get("name")==name]
        wanted={"id":job_id,"run_id":run_id,"run_attempt":1,"head_sha":REVIEWER_POST_MODEL_SOURCE,
                "status":"completed","conclusion":outcomes[name]}
        if (len(selected)!=1 or canonical_json({k:selected[0].get(k) for k in wanted})!=canonical_json(wanted)
                or not isinstance(selected[0].get("steps"),list)):
            raise V03DogfoodRuntimeDriverError("post-model Reviewer terminal jobs changed")
        steps=selected[0]["steps"]
        required={"agent":{"Execute GitHub Copilot CLI":"success"},
                  "detection":{"Execute threat detection with AWF":"success","Conclude threat detection":"failure"},
                  "conclusion":{"Record non-authoritative Gate execution identity":"failure"}}.get(name,{})
        for label,outcome in required.items():
            matches=[s for s in steps if s.get("name")==label]
            if len(matches)!=1 or matches[0].get("status")!="completed" or matches[0].get("conclusion")!=outcome:
                raise V03DogfoodRuntimeDriverError("post-model Reviewer execution/failure boundary differs")
        if name=="safe_outputs" and steps:
            raise V03DogfoodRuntimeDriverError("failed Reviewer Safe Outputs may have executed")
        normalized.append({"name":name,"id":job_id,"conclusion":outcomes[name],
                           "steps":[{k:s.get(k) for k in ("number","name","status","conclusion")} for s in steps]})
    source_blobs={".github/workflows/"+name:sha for name,sha in HISTORICAL_RELEASE_DOGFOOD_BLOBS.items()
                  if "reviewer-" in name}
    for source_path,sha in source_blobs.items():
        doc=_github_json(preflight,"/contents/"+source_path+"?ref="+REVIEWER_POST_MODEL_SOURCE)
        if doc.get("sha")!=sha or doc.get("encoding")!="base64":
            raise V03DogfoodRuntimeDriverError("failed Reviewer source proof differs")
        raw=base64.b64decode(doc.get("content",""),validate=False)
        if hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\x00"+raw).hexdigest()!=sha:
            raise V03DogfoodRuntimeDriverError("failed Reviewer source bytes differ")
    transport=preflight.composition.actions_transport
    status,_,raw=transport.http(method="GET",url=transport._api(
        f"/actions/jobs/{REVIEWER_POST_MODEL_FAILURE_JOBS['detection']}/logs"),
        token=transport.config.token,body=None)
    log=raw.decode("utf-8",errors="strict")
    required=("THREAT_DETECTION_STATUS: reason=engine_timeout exit=2",
              "detection engine did not record a verdict within 5m0s",
              "Detection result file not found at: /tmp/gh-aw/threat-detection/detection_result.json")
    if status!=200 or len(raw)>2*1024*1024 or any(value not in log for value in required):
        raise V03DogfoodRuntimeDriverError("failed Reviewer detector timeout/no-verdict proof missing")
    artifacts=_github_json(preflight,f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifact_rows=artifacts.get("artifacts")
    fixed_artifacts={"usage":[11615306199,"sha256:9ddbdb622f2081412ab40300632f6c3d17c2eb5a5975a73136124f18cc9f8159"],"detection":[11615291183,"sha256:ba7d688ef4a4e6a11953e05ba55b02ba3a7a3c5d7b4c3adeb3a7d2181172dbe8"],"agent-output-fallback":[11614806653,"sha256:8aaa58dd2068255f44ce825c677f31a4d9805dcddef34fc3d9c57bfeb9807641"],"activation":[11614602643,"sha256:473677789b3d60cefe6f821ce470a81570d3d8d46dbd917b6d16f71beaf832e0"],"info":[11614473040,"sha256:048ea882529c55e17d89f0e54e639889ab807ced5bb71262dd70b098887c412f"],"agent":[11614239306,"sha256:41906db5ed959e854f7466925c9e594f3519fd2e58f11985b0ddbbe98f8b42e5"]}
    if (not isinstance(artifact_rows,list) or type(artifacts.get("total_count")) is not int
            or artifacts["total_count"]!=6 or len(artifact_rows)!=6
            or {r.get("name"):(r.get("id"),r.get("digest")) for r in artifact_rows}
               !={name:tuple(value) for name,value in fixed_artifacts.items()}
            or any(r.get("workflow_run",{}).get("id")!=run_id or r.get("expired") is not False for r in artifact_rows)):
        raise V03DogfoodRuntimeDriverError("failed Reviewer output inventory differs")
    issue=_github_json(preflight,"/issues/580")
    if (type(issue.get("id")) is not int or issue["id"]!=5777870748 or issue.get("number")!=580
            or issue.get("html_url")!="https://github.com/DREAM-XIN/ai-sdlc/issues/580"
            or issue.get("user",{}).get("login")!="github-actions[bot]"
            or f"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/{run_id}" not in str(issue.get("body") or "")):
        raise V03DogfoodRuntimeDriverError("historical Reviewer failure issue inventory differs")
    after=_github_json(preflight,f"/actions/runs/{run_id}")
    if (any(after.get(k)!=before.get(k) for k in (*expected,"updated_at"))
            or _github_json(preflight,path)!=jobs
            or _github_json(preflight,f"/actions/runs/{run_id}/artifacts?per_page=100")!=artifacts
            or _github_json(preflight,"/issues/580")!=issue):
        raise V03DogfoodRuntimeDriverError("failed Reviewer proof changed while observing")
    material={"run":expected,"updated_at":before.get("updated_at"),"jobs":normalized,
              "original_failure":original_failure,"source_blobs":source_blobs,"detector_log_sha256":hashlib.sha256(raw).hexdigest(),
              "artifacts":fixed_artifacts,"failure_issue":{k:issue.get(k) for k in
                  ("id","number","html_url","title","body","created_at","updated_at")}}
    return {"schema_version":"ai-sdlc.v03-reviewer-post-model-failure/v1","run_id":run_id,"run_attempt":1,
        "source_head_sha":REVIEWER_POST_MODEL_SOURCE,"jobs":REVIEWER_POST_MODEL_FAILURE_JOBS,
        "model_executed":True,"detector_timed_out":True,"semantic_safety_pass":False,
        "safe_outputs_processed":False,"failure_issue_number":580,
        "observation_digest":"sha256:"+digest_json(material)}


def _reviewer_existing_producer_check(preflight, snapshot):
    from v03_dogfood_full_composition import POST_HANDOFF_RUN, POST_HANDOFF_SOURCE, validate_post_handoff_predecessor, observe_post_handoff_pr
    sealed, _ = validate_post_handoff_predecessor(snapshot)
    source = preflight.composition.recovery_result_source
    before = source._first_attempt_run_snapshot(run_id=POST_HANDOFF_RUN, external_dispatch_key=sealed["recovery_dispatch_key"])
    if before["head_sha"] != POST_HANDOFF_SOURCE:
        raise V03DogfoodRuntimeDriverError("existing Developer producer changed before Reviewer replacement")
    observe_post_handoff_pr(source, snapshot)
    after = source._first_attempt_run_snapshot(run_id=POST_HANDOFF_RUN, external_dispatch_key=sealed["recovery_dispatch_key"])
    if not source._same_run_snapshot(before, after):
        raise V03DogfoodRuntimeDriverError("existing Developer changed during Reviewer preclaim proof")


def recover_reviewer_pre_model(preflight):
    from v03_dogfood_full_composition import (
        RECOVERY_OPERATION_ID, REVIEWER_AUTH_PATH, REVIEWER_CLAIM_PATH, REVIEWER_SEAL_PATH, REVIEWER_CANDIDATE,
        REVIEWER_OLD_KEY, REVIEWER_FAILED_RUN, REVIEWER_NEW_WORKFLOW,
        reviewer_replacement_present, validate_reviewer_predecessor, validate_reviewer_authorization,
        reviewer_replacement_route, plan_reviewer_replacement, reviewer_dispatch, reviewer_trusted_context,
        _reviewer_authority_identity, _reviewer_complete_authorization, reviewer_scan,
        DogfoodReviewerReplacementTransport)
    if preflight.slot.scenario != "happy_path":
        return None
    runtime = preflight.composition.runtime
    binding = recovery_execution_binding(preflight.composition.policy_authority)
    snapshot = runtime.backend.read_snapshot()
    old, events = validate_reviewer_predecessor(snapshot)
    source = preflight.composition.result_source
    source.bind_reviewer(runtime, preflight.composition.policy_authority)
    if reviewer_replacement_present(snapshot):
        auth, _ = validate_reviewer_authorization(snapshot, consumer_binding=binding)
        if (_observe_reviewer_pre_model_failure(preflight) != auth["pre_model_failure_proof"]
                or _reviewer_worker_blobs() != auth["worker_blobs"]):
            raise V03DogfoodRuntimeDriverError("Reviewer replay predecessor/source changed")
        result = {"acquired": False, "authorization": auth}
    else:
        proof = _observe_reviewer_pre_model_failure(preflight)
        workers = _reviewer_worker_blobs()
        def planner(current):
            if reviewer_replacement_present(current):
                return plan_reviewer_replacement(current, consumer_binding=binding,
                    worker_blobs=workers, failure_proof=proof)
            validate_reviewer_predecessor(current, fresh=True)
            _reviewer_existing_producer_check(preflight, current)
            _require_recovery_execution_source(preflight, binding["execution_source_head_sha"])
            if _observe_reviewer_pre_model_failure(preflight) != proof or _reviewer_worker_blobs() != workers:
                raise V03DogfoodRuntimeDriverError("Reviewer source/failure proof drifted before claim")
            candidate = preflight.composition.candidate_provider.current_candidate(
                operation_id=RECOVERY_OPERATION_ID, repository=preflight.execution.repository,
                feature_id=preflight.slot.feature_id, target_ref=preflight.slot.target_ref)
            if candidate.candidate_pr_number != 552 or candidate.candidate_head_sha != REVIEWER_CANDIDATE:
                raise V03DogfoodRuntimeDriverError("Reviewer replacement candidate drifted before claim")
            proposed = _reviewer_complete_authorization(_reviewer_authority_identity(
                current, consumer_binding=binding, worker_blobs=workers, failure_proof=proof))
            gateway = preflight.composition.dispatch_gateway
            raw_gateway = gateway.delegate
            inputs = raw_gateway._inputs(reviewer_dispatch(proposed))
            raw_gateway.transport._validate_dispatch_inputs(workflow=REVIEWER_NEW_WORKFLOW, ref="main", inputs=inputs)
            if reviewer_scan(raw_gateway.transport, physical_key=proposed["physical_key"])["lookup_state"] != "NOT_LAUNCHED":
                raise V03DogfoodRuntimeDriverError("Reviewer replacement already exists before claim")
            return plan_reviewer_replacement(current, consumer_binding=binding,
                worker_blobs=workers, failure_proof=proof)
        result = _commit_recovery_nonempty(runtime, planner)
        auth = result["authorization"]
    snapshot = runtime.backend.read_snapshot()
    transport = DogfoodReviewerReplacementTransport(preflight.composition.actions_transport.config,
        snapshot=snapshot, consumer_binding=binding, allow_post=result["acquired"],
        http=preflight.composition.actions_transport.http, sleeper=time.sleep)
    def prepost():
        _reviewer_existing_producer_check(preflight, runtime.backend.read_snapshot())
        if _observe_reviewer_pre_model_failure(preflight) != auth["pre_model_failure_proof"]:
            raise V03DogfoodRuntimeDriverError("Reviewer predecessor proof changed before POST")
    transport.prepost = prepost
    if REVIEWER_SEAL_PATH in snapshot.files:
        route = reviewer_replacement_route(snapshot, consumer_binding=binding)
        receipt = reviewer_scan(transport, physical_key=auth["physical_key"])
        if receipt != {"lookup_state": "LAUNCHED", "receipt_id": route["receipt_id"]}:
            raise V03DogfoodRuntimeDriverError("Reviewer sealed execution lookup changed")
    else:
        receipt = reviewer_scan(transport, physical_key=auth["physical_key"])
        if result["acquired"]:
            if receipt["lookup_state"] != "NOT_LAUNCHED":
                raise V03DogfoodRuntimeDriverError("Reviewer winner lost exhaustive absence")
            gateway = GhAwVerticalRoleDispatchGateway(transport=transport, workflows=transport.config.workflows)
            receipt = gateway.launch(dispatch=reviewer_dispatch(auth))
        elif receipt["lookup_state"] != "LAUNCHED":
            raise V03DogfoodRuntimeDriverError("Reviewer create slot consumed; lookup cannot establish a run")
    if receipt.get("lookup_state") != "LAUNCHED":
        raise V03DogfoodRuntimeDriverError("Reviewer execution uncertain; no second POST is authorized")
    from v03_dogfood_scenario_runner import wait_for_worker_run
    run_id = int(receipt["receipt_id"])
    wait_for_worker_run(read_run=lambda n: _github_json(preflight, f"/actions/runs/{n}"),
        receipt=str(run_id), workflow=REVIEWER_NEW_WORKFLOW,
        installation_sha=binding["execution_source_head_sha"], external_dispatch_key=auth["physical_key"])
    resolved = source.resolve(external_dispatch_key=auth["physical_key"], expected_receipt_identity=str(run_id),
        trusted_context=reviewer_trusted_context(auth))
    proof = source._reviewer_proofs[run_id]
    def seal(current):
        current_auth, claim = validate_reviewer_authorization(current, consumer_binding=binding)
        expected = {"schema_version": current_auth["schema_version"], "ordinal": 1,
            "authorization_digest": "sha256:" + digest_json(current_auth), "claim_digest": "sha256:" + digest_json(claim),
            "logical_key": REVIEWER_OLD_KEY, "physical_key": current_auth["physical_key"],
            "run_id": run_id, "run_attempt": 1, "conclusion": "success", "workflow_file": REVIEWER_NEW_WORKFLOW,
            "execution_binding": binding, **proof}
        if REVIEWER_SEAL_PATH in current.files:
            existing = reviewer_replacement_route(current, consumer_binding=binding)["sealed"]
            if existing != expected:
                raise V03DogfoodRuntimeDriverError("Reviewer seal replay changed")
            return StoreMutationPlan(current.ref_sha, (), {"sealed": existing})
        from operator_store_model import apply_plan_to_snapshot
        plan = StoreMutationPlan(current.ref_sha,
            (StoreMutation("create_immutable", REVIEWER_SEAL_PATH, expected),), {"sealed": expected})
        reviewer_replacement_route(apply_plan_to_snapshot(current, plan), consumer_binding=binding)
        return plan
    return _commit_recovery_nonempty(runtime, seal)


def _observe_reviewer_structured_predecessor(preflight):
    """Authenticate the spent ordinal2 publication as history, never a Gate result."""
    import re
    from v03_dogfood_live_gate import CURRENT_DOGFOOD_BLOBS
    source = "ff2fcfebfceaef2baf4edc2a6de2ab820760d48b"
    run_id, controller_id, comment_id = 38018044654, 38017902256, 6092979158
    expected = {
        "id": run_id, "run_attempt": 1, "workflow_id": 380203519,
        "path": ".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml",
        "event": "workflow_dispatch", "head_branch": "main", "head_sha": source,
        "status": "completed", "conclusion": "success",
        "display_title": "AI-SDLC gh-aw dispatch-72f9f220eff8e8a1ab1577c22d3d680bb778abf9",
        "updated_at": "2026-10-10T02:49:32Z",
    }
    controller_expected = {
        "id": controller_id, "run_attempt": 1, "workflow_id": 342691463,
        "path": ".github/workflows/v03-real-dogfood-scenario.yml",
        "event": "workflow_dispatch", "head_branch": "main", "head_sha": source,
        "status": "completed", "conclusion": "failure",
        "display_title": "v0.3 real dogfood happy_path @ " + source,
        "updated_at": "2026-10-10T02:49:55Z",
    }
    def identity(row, wanted, label):
        if not isinstance(row, dict) or canonical_json({key: row.get(key) for key in wanted}) != canonical_json(wanted):
            raise V03DogfoodRuntimeDriverError("structured predecessor " + label + " differs")
        return {key: row[key] for key in wanted}
    before = _github_json(preflight, f"/actions/runs/{run_id}")
    before_controller = _github_json(preflight, f"/actions/runs/{controller_id}")
    run = identity(before, expected, "Worker")
    controller = identity(before_controller, controller_expected, "controller")
    job_ids = {"activation": 114112634510, "agent": 114112679654,
               "detection": 114113343613, "safe_outputs": 114113561614, "conclusion": 114113607288}
    jobs_path = f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100"
    controller_jobs_path = f"/actions/runs/{controller_id}/attempts/1/jobs?per_page=100"
    jobs = _github_json(preflight, jobs_path)
    controller_jobs = _github_json(preflight, controller_jobs_path)
    def normalized_jobs(payload, expected_jobs, observed_run):
        rows = payload.get("jobs")
        if (not isinstance(rows, list) or type(payload.get("total_count")) is not int
                or payload["total_count"] != len(expected_jobs) or len(rows) != len(expected_jobs)):
            raise V03DogfoodRuntimeDriverError("structured predecessor job inventory is incomplete")
        result = []
        for name, (job_id, outcome) in expected_jobs.items():
            matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
            if len(matches) != 1:
                raise V03DogfoodRuntimeDriverError("structured predecessor job name is ambiguous")
            row = matches[0]
            wanted = {"id": job_id, "run_id": observed_run, "run_attempt": 1,
                      "head_sha": source, "status": "completed", "conclusion": outcome}
            item = identity(row, wanted, "job")
            steps = row.get("steps")
            if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
                raise V03DogfoodRuntimeDriverError("structured predecessor job steps are malformed")
            required = {
                "agent": {"Execute GitHub Copilot CLI": "success"},
                "detection": {"Execute threat detection with AWF": "success", "Conclude threat detection": "success"},
                "safe_outputs": {"Require first attempt and affirmative detection before Safe Outputs effects": "success",
                                 "Process Safe Outputs": "success"},
                "conclusion": {"Record non-authoritative Gate execution identity": "success"},
                "dogfood": {"Execute one frozen real dogfood scenario": "failure"},
            }.get(name, {})
            for step_name, conclusion in required.items():
                selected = [step for step in steps if step.get("name") == step_name]
                if len(selected) != 1 or selected[0].get("status") != "completed" or selected[0].get("conclusion") != conclusion:
                    raise V03DogfoodRuntimeDriverError("structured predecessor execution boundary differs")
            item.update(name=name, steps=[{key: step.get(key) for key in ("number", "name", "status", "conclusion")} for step in steps])
            result.append(item)
        return result
    normalized = normalized_jobs(jobs, {name: (job_id, "success") for name, job_id in job_ids.items()}, run_id)
    normalized_controller = normalized_jobs(controller_jobs, {
        "dogfood": (114112184854, "failure"), "reject-non-main": (114112185859, "skipped")}, controller_id)
    source_blobs = {".github/workflows/" + name: sha for name, sha in CURRENT_DOGFOOD_BLOBS.items()
                    if "reviewer-" in name}
    if len(source_blobs) != 2:
        raise V03DogfoodRuntimeDriverError("structured predecessor source inventory differs")
    for path, sha in source_blobs.items():
        document = _github_json(preflight, "/contents/" + path + "?ref=" + source)
        if document.get("sha") != sha or document.get("encoding") != "base64" or not isinstance(document.get("content"), str):
            raise V03DogfoodRuntimeDriverError("structured predecessor source metadata differs")
        try:
            raw = base64.b64decode("".join(document["content"].split()), validate=True)
        except Exception as exc:
            raise V03DogfoodRuntimeDriverError("structured predecessor source encoding differs") from exc
        if len(raw) > 512 * 1024 or hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest() != sha:
            raise V03DogfoodRuntimeDriverError("structured predecessor source bytes differ")
    comment_path = f"/issues/comments/{comment_id}"
    comment = _github_json(preflight, comment_path)
    comment_expected = {
        "id": comment_id, "url": f"https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/comments/{comment_id}",
        "html_url": f"https://github.com/DREAM-XIN/ai-sdlc/pull/552#issuecomment-{comment_id}",
        "issue_url": "https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/552",
        "created_at": "2026-10-10T02:49:10Z", "updated_at": "2026-10-10T02:49:10Z",
        "author_association": "NONE",
    }
    comment_identity = identity(comment, comment_expected, "comment")
    author = identity(comment.get("user"), {"id": 41898282, "login": "github-actions[bot]", "type": "Bot"}, "comment author")
    body = comment.get("body")
    body_digest = "6053f28b1c0eab9d4e587b2754366b7a5ac66408b460a3247a6786223bb88fc2"
    if not isinstance(body, str) or hashlib.sha256(body.encode("utf-8")).hexdigest() != body_digest:
        raise V03DogfoodRuntimeDriverError("original REWORK comment bytes changed")
    comment_identity.update(author=author, body_sha256=body_digest)
    artifacts_path = f"/actions/runs/{run_id}/artifacts?per_page=100"
    artifacts = _github_json(preflight, artifacts_path)
    fixed_artifacts = {"agent-output-fallback":[11657725741,"sha256:941127d655b47a3ca4614ea22f7467c012a0a0c7faa2e6e3521bf5bf8d2913c7"],"safe-outputs-items":[11657685930,"sha256:46eac190846d28821169713bcfed4cb089dcd792003d07b108786142fbeda8c9"],"activation":[11657560510,"sha256:a6cf914a0cdef5b009e71e2403a45202853af221905ebe7776a015d253732e4d"],"info":[11657410505,"sha256:e7902061a46f7eb7d27d9dd38ccf9026ffa4a55f0c86e788d6d0374bd74f21f2"],"agent":[11657156209,"sha256:3a8a0b6440bfe557588b30ad37b76dec1798531ad43cc8fe0c44e90f5ad5a2d4"],"detection":[11656898673,"sha256:a561a574ae8e37ae1929073845527efb2be6c45da1a4b0f1c408ba4eab4769fe"],"usage":[11656628927,"sha256:15143685c0b1edfbd19531973ac307ee9b975eff1e08d5075fe1af34716cb60c"]}
    def artifact_identity(payload):
        rows = payload.get("artifacts")
        if (not isinstance(rows, list) or type(payload.get("total_count")) is not int
                or payload["total_count"] != len(fixed_artifacts) or len(rows) != len(fixed_artifacts)):
            raise V03DogfoodRuntimeDriverError("structured predecessor artifact inventory is incomplete")
        result = []
        for name, (artifact_id, digest) in fixed_artifacts.items():
            matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
            if len(matches) != 1:
                raise V03DogfoodRuntimeDriverError("structured predecessor artifact name is ambiguous")
            row = matches[0]
            value = identity(row, {"id": artifact_id, "name": name, "digest": digest, "expired": False}, "artifact")
            value["workflow_run"] = identity(row.get("workflow_run"), {
                "id": run_id, "repository_id": 1326302284, "head_repository_id": 1326302284,
                "head_branch": "main", "head_sha": source}, "artifact owner")
            result.append(value)
        return result
    artifact_proof = artifact_identity(artifacts)
    expected_records = {"detection":["2026-10-10T02:48:53.5743242Z THREAT_DETECTION_STATUS: reason=result_recorded exit=0","2026-10-10T02:48:55.9040688Z THREAT_DETECTION_STATUS: reason=result_recorded exit=0"],"safe_outputs":["2026-10-10T02:49:10.4079918Z Created comment: https://github.com/DREAM-XIN/ai-sdlc/pull/552#issuecomment-6092979158","2026-10-10T02:49:10.4081765Z 📝 Manifest: logged add_comment → https://github.com/DREAM-XIN/ai-sdlc/pull/552#issuecomment-6092979158","2026-10-10T02:49:10.4166100Z Exported comment_id: 6092979158"],"controller":["2026-10-10T02:49:49.9516755Z operator_vertical.VerticalInvariantError: Gate Safe Output comment has invalid machine envelope"]}
    transport = preflight.composition.actions_transport
    def log_records(job_id, label):
        status, _, raw = transport.http(method="GET", url=transport._api(f"/actions/jobs/{job_id}/logs"),
                                       token=transport.config.token, body=None)
        if status != 200 or not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
            raise V03DogfoodRuntimeDriverError("structured predecessor log is unavailable")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise V03DogfoodRuntimeDriverError("structured predecessor log is not UTF-8") from exc
        selected = []
        for line in text.splitlines():
            matched = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z) (.*)", line)
            if matched is None:
                continue
            payload = matched[2]
            prefixes = {
                "detection": ("THREAT_DETECTION_STATUS:",),
                "safe_outputs": ("Created comment:", "📝 Manifest: logged add_comment", "Exported comment_id:"),
                "controller": ("operator_vertical.VerticalInvariantError:",),
            }[label]
            if payload.startswith(prefixes):
                selected.append(line)
        if selected != expected_records[label]:
            raise V03DogfoodRuntimeDriverError("structured predecessor selected log records differ")
        return selected
    logs = {label: log_records(job, label) for label, job in
            (("detection", job_ids["detection"]), ("safe_outputs", job_ids["safe_outputs"]), ("controller", 114112184854))}
    # Re-read authority-bearing projections to close the observation bracket.
    identity(_github_json(preflight, f"/actions/runs/{run_id}"), expected, "Worker")
    identity(_github_json(preflight, f"/actions/runs/{controller_id}"), controller_expected, "controller")
    if (normalized_jobs(_github_json(preflight, jobs_path), {name: (job, "success") for name, job in job_ids.items()}, run_id) != normalized
            or normalized_jobs(_github_json(preflight, controller_jobs_path),
                {"dogfood": (114112184854, "failure"), "reject-non-main": (114112185859, "skipped")}, controller_id) != normalized_controller
            or artifact_identity(_github_json(preflight, artifacts_path)) != artifact_proof):
        raise V03DogfoodRuntimeDriverError("structured predecessor metadata changed while observing")
    after_comment = _github_json(preflight, comment_path)
    if (identity(after_comment, comment_expected, "comment") != {key: comment_identity[key] for key in comment_expected}
            or identity(after_comment.get("user"), author, "comment author") != author
            or after_comment.get("body") != body):
        raise V03DogfoodRuntimeDriverError("original REWORK changed while observing")
    for label, job in (("detection", job_ids["detection"]), ("safe_outputs", job_ids["safe_outputs"]), ("controller", 114112184854)):
        if log_records(job, label) != logs[label]:
            raise V03DogfoodRuntimeDriverError("structured predecessor log changed while observing")
    material = {"run": run, "controller": controller, "jobs": normalized, "controller_jobs": normalized_controller,
                "source_blobs": source_blobs, "artifacts": artifact_proof, "comment": comment_identity, "selected_log_records": logs}
    return {"schema_version": "ai-sdlc.v03-reviewer-structured-predecessor/v1",
            "run_id": run_id, "run_attempt": 1, "source_head_sha": source,
            "controller_run_id": controller_id, "comment_id": comment_id, "body_sha256": body_digest,
            "verdict_inventory": "REWORK", "adopted": False,
            "observation_digest": "sha256:" + digest_json(material)}



def recover_reviewer_structured(preflight):
    """One separately admitted Reviewer; every replay is lookup-only."""
    import v03_dogfood_full_composition as c
    if preflight.slot.scenario != "happy_path":
        return None
    runtime=preflight.composition.runtime
    binding=c.recovery_execution_binding(preflight.composition.policy_authority)
    snapshot=runtime.backend.read_snapshot()
    c.validate_reviewer_structured_predecessor(snapshot)
    source=preflight.composition.result_source
    source.bind_reviewer(runtime,preflight.composition.policy_authority)
    builder=preflight.composition.dispatch_gateway.delegate.context_builder
    def fresh_predecessor():
        _reviewer_existing_producer_check(preflight,runtime.backend.read_snapshot())
        historical = runtime.backend.read_snapshot().get(c.REVIEWER_POST_MODEL_AUTH_PATH)
        observed = _observe_reviewer_post_model_failure(preflight)
        if (not isinstance(historical, dict)
                or canonical_json(observed) != canonical_json(historical.get("post_model_failure_proof"))):
            raise V03DogfoodRuntimeDriverError("older Reviewer failure proof changed before corrected execution")
        return _observe_reviewer_structured_predecessor(preflight)
    proof=fresh_predecessor()
    if c.reviewer_structured_present(snapshot):
        auth,claim=c.validate_reviewer_structured_authorization(snapshot,consumer_binding=binding)
        if canonical_json(proof)!=canonical_json(auth["predecessor_proof"]):
            raise V03DogfoodRuntimeDriverError("corrected Reviewer historical observation changed")
        result={"acquired":False,"authorization":auth,"claim":claim}
    else:
        def planner(current):
            if c.reviewer_structured_present(current):
                return c.plan_reviewer_structured_replacement(current,consumer_binding=binding,
                    predecessor_proof=proof,dispatch_inputs=None)
            c.validate_reviewer_structured_predecessor(current,fresh=True)
            _require_recovery_execution_source(preflight,binding["execution_source_head_sha"])
            if fresh_predecessor()!=proof:
                raise V03DogfoodRuntimeDriverError("corrected Reviewer predecessor changed before claim")
            auth=c.reviewer_structured_authorization(current,consumer_binding=binding,predecessor_proof=proof)
            dispatch=c.reviewer_dispatch(auth)
            context=builder.build_prospective_reviewer(auth)
            inputs=GhAwVerticalRoleDispatchGateway._inputs(preflight.composition.dispatch_gateway.delegate,dispatch)
            payload=json.loads(inputs["task_payload"])
            payload["feature_context"]["gate_context"]=context
            inputs["task_payload"]=canonical_json(payload)
            preflight.composition.actions_transport._validate_dispatch_inputs(
                workflow=c.STRUCTURED_GATE_WORKFLOWS["reviewer"],ref="main",inputs=inputs)
            if runtime.backend.read_snapshot().ref_sha!=current.ref_sha:
                raise V03DogfoodRuntimeDriverError("Store changed during corrected Reviewer context preparation")
            if c.reviewer_structured_scan(preflight.composition.actions_transport,
                    physical_key=auth["physical_key"])["lookup_state"]!="NOT_LAUNCHED":
                raise V03DogfoodRuntimeDriverError("corrected Reviewer exists before claim")
            return c.plan_reviewer_structured_replacement(current,consumer_binding=binding,
                predecessor_proof=proof,dispatch_inputs=inputs)
        result=_commit_recovery_nonempty(runtime,planner)
        auth,claim=result["authorization"],result["claim"]
    transport=c.DogfoodStructuredReviewerTransport(preflight.composition.actions_transport.config,
        snapshot=runtime.backend.read_snapshot(),consumer_binding=binding,allow_post=result["acquired"],
        http=preflight.composition.actions_transport.http,sleeper=time.sleep)
    def prepost():
        if fresh_predecessor()!=auth["predecessor_proof"]:
            raise V03DogfoodRuntimeDriverError("corrected Reviewer predecessor changed before POST")
        fresh=builder(c.reviewer_dispatch(auth))
        frozen=json.loads(claim["dispatch_inputs"]["task_payload"])["feature_context"]["gate_context"]
        from copy import deepcopy
        comparable=deepcopy(fresh)
        comparable["provenance"]["store_commit_sha"]=frozen["provenance"]["store_commit_sha"]
        comparable["context_sha256"]=frozen["context_sha256"]
        if canonical_json(comparable)!=canonical_json(frozen):
            raise V03DogfoodRuntimeDriverError("corrected Reviewer evidence changed after claim")
        c.validate_reviewer_structured_authorization(runtime.backend.read_snapshot(),consumer_binding=binding)
    transport.prepost=prepost
    receipt=c.reviewer_structured_scan(transport,physical_key=auth["physical_key"])
    if result["acquired"]:
        if receipt["lookup_state"]!="NOT_LAUNCHED":
            raise V03DogfoodRuntimeDriverError("corrected Reviewer CAS winner lost absence")
        receipt=transport.dispatch(workflow=auth["workflow_file"],ref="main",inputs=claim["dispatch_inputs"])
    elif receipt["lookup_state"]!="LAUNCHED":
        raise V03DogfoodRuntimeDriverError("corrected Reviewer claim consumed without a known run; no retry")
    if receipt.get("lookup_state")!="LAUNCHED":
        raise V03DogfoodRuntimeDriverError("corrected Reviewer launch unknown; no second POST")
    from v03_dogfood_scenario_runner import wait_for_worker_run
    run_id=int(receipt["receipt_id"])
    wait_for_worker_run(read_run=lambda n:_github_json(preflight,f"/actions/runs/{n}"),
        receipt=str(run_id),workflow=auth["workflow_file"],installation_sha=binding["execution_source_head_sha"],
        external_dispatch_key=auth["physical_key"])
    resolved=source.resolve(external_dispatch_key=auth["physical_key"],expected_receipt_identity=str(run_id),
        trusted_context=c.reviewer_trusted_context(auth))
    material=source._reviewer_proofs[run_id]
    verdict=resolved.role_payload.get("verdict")
    if verdict in {"REWORK","BLOCKED"}:
        return _commit_recovery_nonempty(runtime,lambda current:c.plan_reviewer_structured_terminal(
            current,run_id=run_id,role_payload=resolved.role_payload,proof=material,consumer_binding=binding,
            occurred_at=runtime.clock(),trusted_context_digest=preflight.trusted_context_digest))
    if verdict!="PASS":
        raise V03DogfoodRuntimeDriverError("corrected Reviewer has no affirmative PASS; stopped")
    def seal(current):
        current_auth,current_claim=c.validate_reviewer_structured_authorization(current,consumer_binding=binding)
        if c.REVIEWER_STRUCTURED_TERMINAL_PATH in current.files:
            raise V03DogfoodRuntimeDriverError("corrected Reviewer already terminal")
        expected={"schema_version":current_auth["schema_version"],"ordinal":3,
            "authorization_digest":"sha256:"+digest_json(current_auth),"claim_digest":"sha256:"+digest_json(current_claim),
            "logical_key":c.REVIEWER_OLD_KEY,"physical_key":current_auth["physical_key"],"run_id":run_id,
            "run_attempt":1,"conclusion":"success","workflow_file":auth["workflow_file"],
            "execution_binding":binding,"recommendation":"PASS",**material}
        if c.REVIEWER_STRUCTURED_SEAL_PATH in current.files:
            existing=c.reviewer_replacement_route(current,consumer_binding=binding)["sealed"]
            if canonical_json(existing)!=canonical_json(expected):
                raise V03DogfoodRuntimeDriverError("corrected Reviewer PASS seal replay differs")
            return StoreMutationPlan(current.ref_sha,(),{"sealed":existing})
        from operator_store_model import apply_plan_to_snapshot
        plan=StoreMutationPlan(current.ref_sha,(StoreMutation("create_immutable",c.REVIEWER_STRUCTURED_SEAL_PATH,expected),),
            {"sealed":expected})
        c.reviewer_replacement_route(apply_plan_to_snapshot(current,plan),consumer_binding=binding)
        return plan
    return _commit_recovery_nonempty(runtime,seal)


def recover_reviewer_post_model(preflight):
    from v03_dogfood_full_composition import (
        RECOVERY_OPERATION_ID, REVIEWER_POST_MODEL_AUTH_PATH as REVIEWER_AUTH_PATH,
        REVIEWER_POST_MODEL_CLAIM_PATH as REVIEWER_CLAIM_PATH, REVIEWER_POST_MODEL_SEAL_PATH as REVIEWER_SEAL_PATH, REVIEWER_CANDIDATE,
        REVIEWER_OLD_KEY, REVIEWER_POST_MODEL_FAILED_RUN as REVIEWER_FAILED_RUN,
        REVIEWER_BOUNDED_WORKFLOW as REVIEWER_NEW_WORKFLOW,
        reviewer_post_model_present as reviewer_replacement_present,
        validate_reviewer_post_model_predecessor as validate_reviewer_predecessor, validate_reviewer_post_model_authorization as validate_reviewer_authorization,
        reviewer_replacement_route, plan_reviewer_post_model_replacement as plan_reviewer_replacement, reviewer_dispatch, reviewer_trusted_context,
        _reviewer_post_model_identity as _reviewer_authority_identity, _reviewer_complete_authorization,
        reviewer_post_model_scan as reviewer_scan,
        DogfoodReviewerReplacementTransport)
    if preflight.slot.scenario != "happy_path":
        return None
    runtime = preflight.composition.runtime
    binding = recovery_execution_binding(preflight.composition.policy_authority)
    snapshot = runtime.backend.read_snapshot()
    old, events = validate_reviewer_predecessor(snapshot)
    source = preflight.composition.result_source
    source.bind_reviewer(runtime, preflight.composition.policy_authority)
    if reviewer_replacement_present(snapshot):
        auth, _ = validate_reviewer_authorization(snapshot, consumer_binding=binding)
        if (_observe_reviewer_post_model_failure(preflight) != auth["post_model_failure_proof"]
                or _reviewer_worker_blobs() != auth["worker_blobs"]):
            raise V03DogfoodRuntimeDriverError("Reviewer replay predecessor/source changed")
        result = {"acquired": False, "authorization": auth}
    else:
        proof = _observe_reviewer_post_model_failure(preflight)
        workers = _reviewer_worker_blobs()
        def planner(current):
            if reviewer_replacement_present(current):
                return plan_reviewer_replacement(current, consumer_binding=binding,
                    worker_blobs=workers, failure_proof=proof)
            validate_reviewer_predecessor(current, fresh=True)
            _reviewer_existing_producer_check(preflight, current)
            _require_recovery_execution_source(preflight, binding["execution_source_head_sha"])
            if _observe_reviewer_post_model_failure(preflight) != proof or _reviewer_worker_blobs() != workers:
                raise V03DogfoodRuntimeDriverError("Reviewer source/failure proof drifted before claim")
            candidate = preflight.composition.candidate_provider.current_candidate(
                operation_id=RECOVERY_OPERATION_ID, repository=preflight.execution.repository,
                feature_id=preflight.slot.feature_id, target_ref=preflight.slot.target_ref)
            if candidate.candidate_pr_number != 552 or candidate.candidate_head_sha != REVIEWER_CANDIDATE:
                raise V03DogfoodRuntimeDriverError("Reviewer replacement candidate drifted before claim")
            proposed = _reviewer_complete_authorization(_reviewer_authority_identity(
                current, consumer_binding=binding, worker_blobs=workers, failure_proof=proof))
            gateway = preflight.composition.dispatch_gateway
            raw_gateway = gateway.delegate
            inputs = raw_gateway._inputs(reviewer_dispatch(proposed))
            raw_gateway.transport._validate_dispatch_inputs(workflow=REVIEWER_NEW_WORKFLOW, ref="main", inputs=inputs)
            if reviewer_scan(raw_gateway.transport, physical_key=proposed["physical_key"])["lookup_state"] != "NOT_LAUNCHED":
                raise V03DogfoodRuntimeDriverError("Reviewer replacement already exists before claim")
            return plan_reviewer_replacement(current, consumer_binding=binding,
                worker_blobs=workers, failure_proof=proof)
        result = _commit_recovery_nonempty(runtime, planner)
        auth = result["authorization"]
    snapshot = runtime.backend.read_snapshot()
    transport = DogfoodReviewerReplacementTransport(preflight.composition.actions_transport.config,
        snapshot=snapshot, consumer_binding=binding, allow_post=result["acquired"],
        http=preflight.composition.actions_transport.http, sleeper=time.sleep)
    def prepost():
        _reviewer_existing_producer_check(preflight, runtime.backend.read_snapshot())
        if _observe_reviewer_post_model_failure(preflight) != auth["post_model_failure_proof"]:
            raise V03DogfoodRuntimeDriverError("Reviewer predecessor proof changed before POST")
    transport.prepost = prepost
    if REVIEWER_SEAL_PATH in snapshot.files:
        route = reviewer_replacement_route(snapshot, consumer_binding=binding)
        receipt = reviewer_scan(transport, physical_key=auth["physical_key"])
        if receipt != {"lookup_state": "LAUNCHED", "receipt_id": route["receipt_id"]}:
            raise V03DogfoodRuntimeDriverError("Reviewer sealed execution lookup changed")
    else:
        receipt = reviewer_scan(transport, physical_key=auth["physical_key"])
        if result["acquired"]:
            if receipt["lookup_state"] != "NOT_LAUNCHED":
                raise V03DogfoodRuntimeDriverError("Reviewer winner lost exhaustive absence")
            gateway = GhAwVerticalRoleDispatchGateway(transport=transport, workflows=transport.config.workflows)
            receipt = gateway.launch(dispatch=reviewer_dispatch(auth))
        elif receipt["lookup_state"] != "LAUNCHED":
            raise V03DogfoodRuntimeDriverError("Reviewer create slot consumed; lookup cannot establish a run")
    if receipt.get("lookup_state") != "LAUNCHED":
        raise V03DogfoodRuntimeDriverError("Reviewer execution uncertain; no second POST is authorized")
    from v03_dogfood_scenario_runner import wait_for_worker_run
    run_id = int(receipt["receipt_id"])
    wait_for_worker_run(read_run=lambda n: _github_json(preflight, f"/actions/runs/{n}"),
        receipt=str(run_id), workflow=REVIEWER_NEW_WORKFLOW,
        installation_sha=binding["execution_source_head_sha"], external_dispatch_key=auth["physical_key"])
    resolved = source.resolve(external_dispatch_key=auth["physical_key"], expected_receipt_identity=str(run_id),
        trusted_context=reviewer_trusted_context(auth))
    proof = source._reviewer_proofs[run_id]
    def seal(current):
        current_auth, claim = validate_reviewer_authorization(current, consumer_binding=binding)
        expected = {"schema_version": current_auth["schema_version"], "ordinal": 2,
            "authorization_digest": "sha256:" + digest_json(current_auth), "claim_digest": "sha256:" + digest_json(claim),
            "logical_key": REVIEWER_OLD_KEY, "physical_key": current_auth["physical_key"],
            "run_id": run_id, "run_attempt": 1, "conclusion": "success", "workflow_file": REVIEWER_NEW_WORKFLOW,
            "execution_binding": binding, **proof}
        if REVIEWER_SEAL_PATH in current.files:
            existing = reviewer_replacement_route(current, consumer_binding=binding)["sealed"]
            if existing != expected:
                raise V03DogfoodRuntimeDriverError("Reviewer seal replay changed")
            return StoreMutationPlan(current.ref_sha, (), {"sealed": existing})
        from operator_store_model import apply_plan_to_snapshot
        plan = StoreMutationPlan(current.ref_sha,
            (StoreMutation("create_immutable", REVIEWER_SEAL_PATH, expected),), {"sealed": expected})
        reviewer_replacement_route(apply_plan_to_snapshot(current, plan), consumer_binding=binding)
        return plan
    return _commit_recovery_nonempty(runtime, seal)


def reconcile_post_handoff(preflight):
    """Read the existing successful output; this entry has no Developer gateway."""
    if preflight.slot.scenario != "happy_path":
        return None
    from v03_dogfood_full_composition import (
        POST_HANDOFF_RUN, POST_HANDOFF_HEAD, POST_HANDOFF_SOURCE, POST_HANDOFF_PATH,
        validate_post_handoff_predecessor, validate_post_handoff_reconciliation,
        post_handoff_present, observe_post_handoff_pr, plan_post_handoff_reconciliation,
    )
    runtime = preflight.composition.runtime
    source = preflight.composition.recovery_result_source
    binding = recovery_execution_binding(preflight.composition.policy_authority)
    snapshot = runtime.backend.read_snapshot()
    sealed, events = validate_post_handoff_predecessor(snapshot)
    source.bind_post_handoff(runtime, preflight.composition.policy_authority)
    if post_handoff_present(snapshot):
        attestation, _, observation = validate_post_handoff_reconciliation(snapshot, consumer_binding=binding)
        if any(e["event_type"] == "worker.result.rejected"
               and e["payload"].get("callback_id") == attestation["observation_callback_id"] for e in events[16:]):
            raise V03DogfoodRuntimeDriverError("the single reconciled observation failed; no further reconsideration")
    elif len(events) != 15:
        raise V03DogfoodRuntimeDriverError("post-handoff admission escaped the exact stopped predecessor")
    trusted = dict(events[12]["payload"]["trusted_callback_envelope"]["trusted_context"],
        external_dispatch_key=sealed["recovery_dispatch_key"],
        dispatch_id=sealed["collector_dispatch_id"],
        execution_dispatch_id=sealed["recovery_dispatch_id"], source_head_sha=POST_HANDOFF_SOURCE)
    resolved = source.resolve(external_dispatch_key=sealed["recovery_dispatch_key"],
        expected_receipt_identity=str(POST_HANDOFF_RUN), trusted_context=trusted)
    if (len(resolved.outputs) != 1 or resolved.outputs[0].trusted_uri != sealed["safe_output_uri"]
            or resolved.run.run_id != POST_HANDOFF_RUN
            or resolved.run.candidate_pr_number != sealed["output_candidate_pr_number"]
            or resolved.run.candidate_head_sha != POST_HANDOFF_HEAD):
        raise V03DogfoodRuntimeDriverError("post-handoff fresh result differs from original seal")
    _, _, closed, historical = observe_post_handoff_pr(source, snapshot)
    def planner(current):
        if not post_handoff_present(current):
            _require_recovery_execution_source(preflight, binding["execution_source_head_sha"])
            source.resolve(external_dispatch_key=sealed["recovery_dispatch_key"],
                expected_receipt_identity=str(POST_HANDOFF_RUN), trusted_context=trusted)
            _, _, fresh_closed, fresh_historical = observe_post_handoff_pr(source, current)
            if fresh_closed != closed or fresh_historical != historical:
                raise V03DogfoodRuntimeDriverError("post-handoff proof changed before CAS")
            ref = source._json(preflight.execution.repository,
                "/git/ref/heads/" + parse.quote(sealed["target_ref"], safe=""), source.config.target_token)
            if not isinstance(ref, dict) or (ref.get("object") or {}).get("sha") != POST_HANDOFF_HEAD:
                raise V03DogfoodRuntimeDriverError("fixture ref differs from the exact applied handoff before reconciliation")
        return plan_post_handoff_reconciliation(current, consumer_binding=binding,
            closed_pr_attestation=closed, historical_open_binding=historical,
            occurred_at=runtime.clock(), trusted_context_digest=preflight.trusted_context_digest)
    return _commit_recovery_nonempty(runtime, planner)


def recover_approved_replacement(preflight):
    """Exactly one approved slot. Failure/uncertainty consumes it permanently."""
    if preflight.slot.scenario != "happy_path":
        return None
    runtime = preflight.composition.runtime
    result = _commit_recovery_nonempty(runtime, lambda snapshot: _plan_fixed_replacement(snapshot, preflight=preflight))
    authorization = result["authorization"]
    snapshot = runtime.backend.read_snapshot()
    route = recovery_route(snapshot)
    gateway = preflight.composition.recovery_dispatch_gateway
    gateway.transport.admit_continuation(snapshot, allow_post=result["acquired"] is True,
        **recovery_execution_binding(preflight.composition.policy_authority),
        execution_trusted_context_digest=preflight.trusted_context_digest)
    sealed = snapshot.get(route["receipt_path"])
    if sealed is not None:
        validate_recovery_execution_seal(snapshot, sealed, execution_binding=recovery_execution_binding(preflight.composition.policy_authority))
        receipt = {"lookup_state": "LAUNCHED", "receipt_id": sealed["receipt_id"]}
    else:
        receipt = gateway.lookup(external_dispatch_key=authorization["recovery_dispatch_key"])
        if result["acquired"] is True:
            if receipt.get("lookup_state") != "NOT_LAUNCHED":
                raise V03DogfoodRuntimeDriverError("replacement winner lost exhaustive absence")
            _replacement_prepost_old_scan(preflight, snapshot)
            _require_recovery_execution_source(preflight, authorization["execution_source_head_sha"])
            receipt = gateway.launch(dispatch=_bounded_recovery_dispatch(preflight, authorization))
        elif receipt.get("lookup_state") != "LAUNCHED":
            raise V03DogfoodRuntimeDriverError("replacement claim consumed; absence or uncertainty forbids another POST")
    if not isinstance(receipt, dict) or receipt.get("lookup_state") != "LAUNCHED":
        raise V03DogfoodRuntimeDriverError("replacement result remains uncertain; no additional create")
    resolved = _resolve_recovery_run_for_seal(preflight, authorization=authorization, receipt_id=str(receipt.get("receipt_id") or ""))
    return _seal_recovery_receipt(preflight, authorization=authorization, receipt=receipt, resolved=resolved)


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

    from v03_dogfood_full_composition import REVIEWER_BOUNDED_WORKFLOW, STRUCTURED_GATE_WORKFLOWS
    recovery = (recover_reviewer_structured if preflight.workflows.reviewer_workflow == STRUCTURED_GATE_WORKFLOWS["reviewer"]
                else recover_reviewer_post_model if preflight.workflows.reviewer_workflow == REVIEWER_BOUNDED_WORKFLOW
                else recover_reviewer_pre_model)
    recovered_historical_attempt = recovery(preflight)
    if isinstance(recovered_historical_attempt, dict) and "terminal" in recovered_historical_attempt:
        terminal = recovered_historical_attempt["terminal"]
        print(json.dumps({"status": "NEEDS_USER", "recommendation": terminal["outcome"],
            "reviewer_run_id": terminal["run_id"], "release_eligible": False}, sort_keys=True))
        return 1
    if recovered_historical_attempt is not None:
        reconcile_post_handoff(preflight)
    if recovered_historical_attempt is None:
        prepare_previous_installation_operation(preflight)

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
