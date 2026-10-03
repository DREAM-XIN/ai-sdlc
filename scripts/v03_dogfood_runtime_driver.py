#!/usr/bin/env python3
"""Trusted-main driver for one frozen v0.3 real release dogfood scenario."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_live_gate import ALLOWED_SCENARIOS, assemble_dogfood_live_gate
from v03_dogfood_openai_host import V03DogfoodOpenAIHostConfig, V03DogfoodOpenAIResponsesHost
from v03_dogfood_runtime_preflight import build_v03_dogfood_runtime_preflight
from v03_dogfood_scenario_runner import run_scenario
from v03_real_runtime_live_authority import load_live_authority, require_trusted_main_execution

from operator_store import plan_cancel
from operator_store_model import operation_events, rebuild_projection
from operator_external_create_attempt import find_external_create_attempt
from operator_vertical import VERTICAL_PROFILE

OPERATION = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
SOURCE_RUN = 37085507909
SOURCE_HEAD = "d2c98bc0762ba49acab400dd77bfcd3e8a9ef1ef"
SOURCE_CONTEXT = "3d5095e119b05e2b8787b78ae3a6b10446ded90cc973db9afab6044e68e018e2"
FEATURE = "F-OPERATOR-V03-DOGFOOD-HAPPY-0001"
CANDIDATE = "70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23"
EFFECT = "80b31137a408f2b0ee85248bd069b972af80b9777f91f2bd00c9b27b42f9e804"
EXTERNAL_KEY = "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1"
REASON = "v03-reviewed-pre-create-retirement:37085507909"
EVENT_IDS = (
    "operation-started-739f4331137732d6184000cf3d8b4915",
    "loop-step-selected-2cf82f42399ec59c7283df1711819426",
    "dispatch-claimed-687520874a948a5c4534e4e30d97b366",
    "dispatch-launch-authorized-5f6d063282780be1d174254ce8ef9134",
    "dispatch-launch-lookup-recorded-8629bfae40264dc5d15df601bc67686d",
)
PAYLOADS = (
    {"expected_revision": 1, "feature_id": FEATURE, "operation_profile": VERTICAL_PROFILE,
     "target_repository": "dream-xin/ai-sdlc"},
    {"feature_revision": 1, "kind": "dispatch", "step": "IMPLEMENTATION_WORK",
     "task_identity": "vertical:implementation:1"},
    {"claim_id": "dc-44100fdf26b6028d24a39ba66482cbefcfcb5e61",
     "external_dispatch_key": EXTERNAL_KEY, "semantic_effect_key": EFFECT},
    {"candidate_head_sha": CANDIDATE, "claim_id": "dc-44100fdf26b6028d24a39ba66482cbefcfcb5e61",
     "dispatch_id": "vertical-7acafffd7380397d926664f8014832c5", "expected_revision": 1,
     "external_dispatch_key": EXTERNAL_KEY, "feature_id": FEATURE, "role": "developer",
     "semantic_effect_key": EFFECT, "stage": "implementation"},
    {"external_dispatch_key": EXTERNAL_KEY, "lookup_state": "NOT_LAUNCHED", "receipt_id": None},
)
EVENT_TYPES = ("operation.started", "loop.step.selected", "dispatch.claimed",
               "dispatch.launch.authorized", "dispatch.launch.lookup-recorded")


class DogfoodPreCreateRetirementError(RuntimeError):
    pass


def require_failed_source(run):
    if not isinstance(run, dict) or (
        run.get("id"), run.get("status"), run.get("conclusion"), run.get("event"),
        run.get("head_branch"), run.get("head_sha"), run.get("path"), run.get("run_attempt"),
        str((run.get("repository") or {}).get("full_name") or "").lower(),
    ) != (
        SOURCE_RUN, "completed", "failure", "workflow_dispatch", "main", SOURCE_HEAD,
        ".github/workflows/v03-real-dogfood-scenario.yml", 1, "dream-xin/ai-sdlc",
    ):
        raise DogfoodPreCreateRetirementError("reviewed source run is not exact completed failed authority")


def retirement_plan(snapshot, *, occurred_at, trusted_context_digest):
    """Recheck all fences inside the production CAS planner."""
    rows = operation_events(snapshot, OPERATION)
    if not rows:
        return None
    projection = rebuild_projection(snapshot, OPERATION)
    if (projection.get("target_repository"), projection.get("feature_id"),
        projection.get("operation_profile"), projection.get("generation"),
        projection.get("expected_feature_revision")) != (
        "dream-xin/ai-sdlc", FEATURE, VERTICAL_PROFILE, 0, 1,
    ):
        raise DogfoodPreCreateRetirementError("reviewed Operation target/generation drifted")
    retired = projection.get("status") == "CANCELLED"
    prefix = rows[:-1] if retired else rows
    if len(prefix) != 5 or any(
        (row.get("operation_id"), row.get("operation_generation"), row.get("sequence"),
         row.get("event_id"), row.get("event_type"), row.get("payload"),
         row.get("trusted_context_digest")) != (
             OPERATION, 0, index + 1, EVENT_IDS[index], EVENT_TYPES[index],
             PAYLOADS[index], SOURCE_CONTEXT,
         )
        for index, row in enumerate(prefix)
    ):
        raise DogfoodPreCreateRetirementError("reviewed pre-create history changed")
    if find_external_create_attempt(snapshot, external_dispatch_key=EXTERNAL_KEY) is not None:
        raise DogfoodPreCreateRetirementError("external-create attempt exists; retirement forbidden")
    # Reject malformed/misbound attempt objects as well as the canonical lookup.
    if any(isinstance(value, dict) and value.get("schema_version") == "ai-sdlc.external-create-attempt/v1"
           and (value.get("semantic_effect_key") == EFFECT
                or value.get("created_operation_id") == OPERATION)
           for value in snapshot.files.values()):
        raise DogfoodPreCreateRetirementError("reviewed Operation has external-create authority")
    if retired:
        last = rows[-1]
        if (last.get("event_type"), last.get("operation_generation"), last.get("sequence"),
            last.get("payload")) != ("operation.cancelled", 0, 6, {"reason": REASON}):
            raise DogfoodPreCreateRetirementError("Operation was retired outside the reviewed boundary")
        return None
    if projection.get("status") != "RUNNING" or trusted_context_digest == SOURCE_CONTEXT:
        raise DogfoodPreCreateRetirementError("retirement requires unfinished pre-create Operation and new authority")
    return plan_cancel(snapshot, operation_id=OPERATION, reason=REASON,
                       occurred_at=occurred_at, trusted_context_digest=trusted_context_digest)


def retire_reviewed_pre_create_operation(preflight):
    """Only RUN mode calls this; readiness and finalization remain read-only here."""
    if preflight.slot.scenario != "happy_path":
        return
    if (preflight.execution.repository.lower(), preflight.slot.feature_id,
        preflight.slot.target_ref, preflight.candidate_head_sha) != (
        "dream-xin/ai-sdlc", FEATURE, "dogfood/v0.3-happy-path-0001", CANDIDATE,
    ):
        raise DogfoodPreCreateRetirementError("retirement escaped exact unchanged fixture candidate")
    runtime = preflight.composition.runtime
    snapshot = runtime.backend.read_snapshot()
    if not operation_events(snapshot, OPERATION):
        return
    source = preflight.composition.result_source
    run = source._json(source.config.control_repository, f"/actions/runs/{SOURCE_RUN}",
                       source.config.control_token)
    require_failed_source(run)
    if retirement_plan(snapshot, occurred_at=runtime.clock(),
                       trusted_context_digest=preflight.trusted_context_digest) is None:
        return

    def planner(current):
        plan = retirement_plan(current, occurred_at=runtime.clock(),
                               trusted_context_digest=preflight.trusted_context_digest)
        if plan is None:
            # A competing retirement must never silently authorize a second run.
            raise DogfoodPreCreateRetirementError("retirement changed during CAS planning")
        return plan

    runtime.commit_replanned(planner)


VALIDATE_ONLY = "validate-only"
PREFLIGHT_ONLY = "preflight-only"
RUN = "run"
MODES = frozenset({VALIDATE_ONLY, PREFLIGHT_ONLY, RUN})
DOGFOOD_RESPONSES_PROVIDER = "deepseek"
DOGFOOD_RESPONSES_API_BASE = "https://api.deepseek.com"
DOGFOOD_RESPONSES_MODEL = "deepseek-flash"


class V03DogfoodRuntimeDriverError(RuntimeError):
    pass


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

    retire_reviewed_pre_create_operation(preflight)
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
