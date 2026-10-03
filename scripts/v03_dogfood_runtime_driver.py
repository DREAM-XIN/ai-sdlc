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

from operator_external_create_attempt import external_create_attempt_path
from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_store import query_unfinished
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import operation_events, rebuild_projection, reservation_path
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_recovery import plan_vertical_takeover
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_live_gate import ALLOWED_SCENARIOS, assemble_dogfood_live_gate
from v03_dogfood_openai_host import V03DogfoodOpenAIHostConfig, V03DogfoodOpenAIResponsesHost
from v03_dogfood_runtime_preflight import build_v03_dogfood_runtime_preflight
from v03_dogfood_scenario_runner import run_scenario
from v03_real_runtime_live_authority import load_live_authority, require_trusted_main_execution

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
