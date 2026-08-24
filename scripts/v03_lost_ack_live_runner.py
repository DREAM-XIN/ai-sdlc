#!/usr/bin/env python3
"""Two-process trusted-main lost-ACK live runner for v0.3 Issue #221."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from typing import Any, Callable

from operator_store_model import operation_events
from v03_real_runtime_driver import assemble_live_preflight
from v03_real_runtime_fault_injection import (
    InjectedRunnerCrash,
    LostAckCrashAfterLaunchDispatchGateway,
)
from v03_real_runtime_lost_ack_orchestration import (
    LostAckDispatchBinding,
    derive_lost_ack_dispatch_binding,
    run_phase2_takeover_and_adopt,
)

PHASE1_EXIT = 86
IDEMPOTENCY_KEY = "v03-release-fi-lost-ack"
ADAPTER_ID = "v03-real-runtime-release-verifier"
PHASE1_EVIDENCE = Path("evidence/v03-live-lost-ack-phase1.json")
FINAL_EVIDENCE = Path("evidence/v03-live-lost-ack.json")


class V03LostAckLiveError(RuntimeError):
    pass


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _binding(preflight) -> LostAckDispatchBinding:
    manifest = preflight.composition.feature_event_gateway.read_feature(
        repository=preflight.execution.repository,
        feature_id=preflight.composition.feature_id,
        target_ref=preflight.composition.target_ref,
    )
    return derive_lost_ack_dispatch_binding(
        repository=preflight.execution.repository,
        feature_id=preflight.composition.feature_id,
        target_ref=preflight.composition.target_ref,
        manifest=manifest,
        candidate_pr_number=preflight.fixture_candidate.candidate_pr_number,
        candidate_head_sha=preflight.fixture_candidate.candidate_head_sha,
        idempotency_key=IDEMPOTENCY_KEY,
        occurred_at=preflight.composition.bundle.runtime.clock(),
    )


def _start_request(binding: LostAckDispatchBinding) -> dict[str, Any]:
    return {
        "idempotency_key": binding.idempotency_key,
        "client_identity": {"adapter_id": ADAPTER_ID},
        "target": {"repository": binding.repository, "feature_id": binding.feature_id},
        "context": {"expected_feature_revision": binding.feature_revision},
    }


def _trusted_context(preflight, binding: LostAckDispatchBinding):
    return preflight.composition.bundle.write_bundle.read_bundle.trusted_context_provider.for_request(
        {"repository": binding.repository, "feature_id": binding.feature_id}
    )


def _scenario_events(preflight, binding: LostAckDispatchBinding) -> list[dict[str, Any]]:
    return operation_events(
        preflight.composition.bundle.runtime.backend.read_snapshot(),
        binding.operation_id,
    )


def run_phase1(
    *,
    preflight,
    evidence_path: Path = PHASE1_EVIDENCE,
    hard_exit: Callable[[int], Any] = os._exit,
) -> None:
    """Launch exactly once and terminate the process before local lookup evidence."""
    binding = _binding(preflight)
    existing = _scenario_events(preflight, binding)
    if existing:
        raise V03LostAckLiveError(
            "lost-ACK live scenario requires a clean idempotency key; durable Operation already exists"
        )
    bundle = preflight.composition.bundle
    base = getattr(bundle.executor, "base", bundle.executor)
    normal_gateway = getattr(base, "dispatch_gateway", None)
    if normal_gateway is not preflight.composition.dispatch_gateway:
        raise V03LostAckLiveError("phase1 executor does not use exact production dispatch gateway")
    base.dispatch_gateway = LostAckCrashAfterLaunchDispatchGateway(
        delegate=normal_gateway,
        expected_external_dispatch_key=binding.external_dispatch_key,
    )
    start = bundle.backends.get("operation.start")
    if start is None or not callable(getattr(start, "invoke", None)):
        raise V03LostAckLiveError("production composition lacks operation.start")
    try:
        start.invoke(_start_request(binding), _trusted_context(preflight, binding))
    except InjectedRunnerCrash as crash:
        if crash.external_dispatch_key != binding.external_dispatch_key:
            raise V03LostAckLiveError("fault escaped for a different dispatch key")
        events = _scenario_events(preflight, binding)
        authorized = [
            row for row in events
            if row.get("event_type") == "dispatch.launch.authorized"
            and int(row.get("operation_generation", -1)) == 0
            and (row.get("payload") or {}).get("external_dispatch_key") == binding.external_dispatch_key
        ]
        looked_up = [
            row for row in events
            if row.get("event_type") == "dispatch.launch.lookup-recorded"
            and int(row.get("operation_generation", -1)) == 0
            and (row.get("payload") or {}).get("external_dispatch_key") == binding.external_dispatch_key
        ]
        if len(authorized) != 1 or looked_up:
            raise V03LostAckLiveError("phase1 durable Store is outside exact crash-after-launch window")
        _write_json(
            evidence_path,
            {
                "schema_version": "ai-sdlc.v03-live-lost-ack-phase1/v1",
                "installation_commit_sha": preflight.execution.installation_commit_sha,
                "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
                "protected_state_ref_sha_before": preflight.live_authority.protected_state_ref_sha,
                "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
                "fixture_candidate_pr_number": preflight.fixture_candidate.candidate_pr_number,
                "fixture_candidate_head_sha": preflight.fixture_candidate.candidate_head_sha,
                "binding": asdict(binding),
                "fault_code": crash.code,
                "g0_launch_authorized_event_id": authorized[0]["event_id"],
                "g0_lookup_count": 0,
                "hard_exit_code": PHASE1_EXIT,
            },
        )
        hard_exit(PHASE1_EXIT)
        raise V03LostAckLiveError("hard-exit hook returned instead of terminating phase1")
    raise V03LostAckLiveError("phase1 returned without exact injected process crash")


def _load_phase1(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise V03LostAckLiveError("fresh-process phase2 lacks valid phase1 evidence") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "ai-sdlc.v03-live-lost-ack-phase1/v1":
        raise V03LostAckLiveError("phase1 evidence schema is invalid")
    return value


def run_phase2(*, preflight, phase1_path: Path = PHASE1_EVIDENCE, final_path: Path = FINAL_EVIDENCE) -> dict[str, Any]:
    """Fresh process: re-derive exact identity, take over into G1 and adopt same launch.

    This is deliberately only the launch/takeover phase of the Issue #221 lost-ACK
    scenario. Exact Worker-result correlation and at-most-once Feature Persist are
    completed by the chained Persist ACK-loss runner before end-to-end lost-ACK can
    become release PASS.
    """
    prior = _load_phase1(phase1_path)
    binding = _binding(preflight)
    if prior.get("binding") != asdict(binding):
        raise V03LostAckLiveError("fresh-process binding differs from phase1 exact identity")
    if prior.get("installation_commit_sha") != preflight.execution.installation_commit_sha:
        raise V03LostAckLiveError("phase2 trusted-main installation differs from phase1")
    if prior.get("materialization_commit_sha") != preflight.live_authority.materialization_commit_sha:
        raise V03LostAckLiveError("phase2 live policy materialization differs from phase1")
    if prior.get("policy_bundle_digest") != preflight.live_authority.policy.bundle_digest:
        raise V03LostAckLiveError("phase2 policy bundle differs from phase1")
    if prior.get("fixture_candidate_head_sha") != preflight.fixture_candidate.candidate_head_sha:
        raise V03LostAckLiveError("fixture candidate changed between crash and takeover")

    result = run_phase2_takeover_and_adopt(
        bundle=preflight.composition.bundle,
        binding=binding,
    )
    events = _scenario_events(preflight, binding)
    counts = {}
    for event_type in (
        "dispatch.launch.authorized",
        "dispatch.launch.lookup-recorded",
        "dispatch.launch.unknown",
        "feature.event.translated",
        "persist.confirmed",
    ):
        counts[event_type] = sum(1 for row in events if row.get("event_type") == event_type)
    if counts["dispatch.launch.authorized"] != 2:
        raise V03LostAckLiveError("lost-ACK scenario does not have exactly G0+G1 launch authorizations")
    if counts["dispatch.launch.lookup-recorded"] != 1:
        raise V03LostAckLiveError("lost-ACK scenario must have exactly one G1 durable lookup/adoption")
    if counts["dispatch.launch.unknown"] != 0:
        raise V03LostAckLiveError("lost-ACK scenario observed UNKNOWN speculative state")
    if counts["feature.event.translated"] != 0 or counts["persist.confirmed"] != 0:
        raise V03LostAckLiveError("takeover phase unexpectedly consumed Worker result/Persist authority")

    evidence = {
        "schema_version": "ai-sdlc.v03-live-lost-ack/v1",
        "scenario": "lost-ack-crash-takeover",
        "status": "PENDING",
        "phase_status": "PASS",
        "release_evidence_scope": "issue-221-lost-ack-launch-takeover-phase-only",
        "remaining_release_proof": [
            "exact first-attempt Worker result correlation",
            "Feature Persist at most once",
        ],
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
        "protected_state_ref_sha_preflight": preflight.live_authority.protected_state_ref_sha,
        "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
        "fixture_candidate_pr_number": preflight.fixture_candidate.candidate_pr_number,
        "fixture_candidate_head_sha": preflight.fixture_candidate.candidate_head_sha,
        "binding": asdict(binding),
        "phase1": prior,
        "phase2": result,
        "durable_event_counts": counts,
        "duplicate_external_effect_count": 0,
        "speculative_retry_under_unknown": 0,
        "overall_issue_221_pass": False,
    }
    _write_json(final_path, evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("phase1", "phase2"), required=True)
    args = parser.parse_args()
    preflight = assemble_live_preflight(env=os.environ, checkout_sha=os.environ["GITHUB_SHA"])
    if args.phase == "phase1":
        run_phase1(preflight=preflight)
        return
    print(json.dumps(run_phase2(preflight=preflight), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
