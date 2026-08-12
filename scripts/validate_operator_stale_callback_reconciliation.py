#!/usr/bin/env python3
"""Validate durable stale-callback convergence for Issue #254."""
from __future__ import annotations

from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_store import plan_operation_start
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import operation_events, projection_public, rebuild_projection, reservation_path
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import FeatureSnapshot, TrustedDispatchContext, VERTICAL_PROFILE
from operator_vertical_controller import select_vertical_action
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_reconcile import TrustedRecoveringVerticalExecutor
from operator_vertical_recovery import plan_vertical_callback_record
from validate_operator_effect_lineage_v2 import PolicyFixture
from validate_operator_vertical_recovery import HEAD, NOW, REPO, FEATURE, _apply, _authorized_dispatch, _start

STATE_REF = "refs/heads/ai-sdlc-operator-state"
STALE_HEAD = "d" * 40
LINEAGE_HEAD_A = "a" * 40
LINEAGE_HEAD_B = "b" * 40
LINEAGE_FEATURE = "F-STALE-CALLBACK-LINEAGE-TEST"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FixedFeatureGateway:
    def __init__(self, candidate_head_sha):
        self.candidate_head_sha = candidate_head_sha

    def read_feature(self, *, operation_id):
        return FeatureSnapshot(
            repository=REPO,
            feature_id=FEATURE,
            target_ref="feature/test",
            revision=10,
            manifest_digest="fixture-manifest",
            current_stage="implementation",
            stages={"implementation": "WORKING"},
            gates={},
            remediation_tasks=tuple(),
            artifacts=tuple(),
            candidate_pr_number=1,
            candidate_head_sha=self.candidate_head_sha,
        ), {}


class NeverReadFeatureGateway:
    def read_feature(self, *, operation_id):
        raise AssertionError("already-rejected callback must not be reprocessed")


class ExplodingFeatureGateway:
    def read_feature(self, *, operation_id):
        raise RuntimeError("transient Feature read failure")


class MutableReviewFeatureGateway:
    def __init__(self, candidate_head_sha):
        self.candidate_head_sha = candidate_head_sha

    def read_feature(self, *, operation_id):
        return FeatureSnapshot(
            repository=REPO,
            feature_id=LINEAGE_FEATURE,
            target_ref="feature/test",
            revision=7,
            manifest_digest="lineage-fixture-manifest",
            current_stage="code-review",
            stages={"code-review": "WORKING"},
            gates={},
            remediation_tasks=tuple(),
            artifacts=tuple(),
            candidate_pr_number=1,
            candidate_head_sha=self.candidate_head_sha,
        ), {"tasks": []}


class UnusedPersistGateway:
    def persist_feature_event(self, *, event, target_ref):
        raise AssertionError("stale callback must not Persist Feature state")

    def lookup_feature_event(self, *, event_id, target_ref):
        raise AssertionError("stale callback must not reconcile Persist")


class UnusedDispatchGateway:
    def launch(self, *, dispatch):
        raise AssertionError("stale callback recovery must not launch external work")

    def lookup(self, *, external_dispatch_key):
        raise AssertionError("stale callback recovery must not lookup external work")


class CountingDispatchGateway:
    def __init__(self):
        self.launch_calls = []
        self.lookup_calls = []

    def launch(self, *, dispatch):
        self.launch_calls.append(dict(dispatch))
        return {"lookup_state": "LAUNCHED", "receipt_id": f"run-{len(self.launch_calls)}"}

    def lookup(self, *, external_dispatch_key):
        self.lookup_calls.append(external_dispatch_key)
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-existing"}


def callback_context(operation_id, effect_key, external_key):
    return TrustedDispatchContext(
        operation_id=operation_id,
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=effect_key,
        external_dispatch_key=external_key,
        dispatch_id="vertical-dispatch-1",
        runtime_receipt_identity="run-1",
        target_repository=REPO,
        target_ref="feature/test",
        feature_id=FEATURE,
        expected_revision=10,
        feature_stage="implementation",
        task_id="vertical:implementation:10",
        role="developer",
        candidate_pr_number=1,
        candidate_head_sha=HEAD,
        worker_identity="developer-worker",
        collector_identity="collector-1",
    )


def durable_callback_snapshot(*, callback_id):
    snapshot, operation_id = _start()
    snapshot, effect_key, external_key = _authorized_dispatch(snapshot, operation_id)
    context = callback_context(operation_id, effect_key, external_key)
    callback = plan_vertical_callback_record(
        snapshot,
        context=context,
        callback_id=callback_id,
        worker_payload={"status": "BLOCKED", "summary": "fixture callback", "outputs": []},
        receipts=[],
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    return _apply(snapshot, callback, "s6"), operation_id, context


def make_executor(snapshot, feature_gateway):
    runtime = OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF, snapshot=snapshot),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )
    base = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=UnusedPersistGateway(),
        dispatch_gateway=UnusedDispatchGateway(),
        config=TrustedVerticalExecutorConfig(
            target_ref="feature/test",
            trusted_context_digest="trusted",
            max_auto_steps=4,
            effect_lineage_required=False,
            legacy_compatibility_mode=True,
        ),
    )
    return TrustedRecoveringVerticalExecutor(
        base_executor=base,
        content_loader=lambda _uri: b"unused",
        trusted_role_policy="trusted-role-policy",
        collector_namespace_policy="trusted-collector-policy",
    )


def rows(executor, operation_id, event_type):
    return [
        event for event in operation_events(executor.runtime.backend.read_snapshot(), operation_id)
        if event["event_type"] == event_type
    ]


def reservation_paths(snapshot):
    return {path for path in snapshot.files if "/reservations/external/" in path}


def validate_stale_candidate_converges():
    snapshot, operation_id, _ = durable_callback_snapshot(callback_id="callback-stale-candidate")
    executor = make_executor(snapshot, FixedFeatureGateway(STALE_HEAD))
    require(executor._reconcile_callback(operation_id) is True, "stale callback was not reconciled")
    public = projection_public(rebuild_projection(executor.runtime.backend.read_snapshot(), operation_id))
    require(public["status"] == "BLOCKED", public)
    rejected = rows(executor, operation_id, "worker.result.rejected")
    require(len(rejected) == 1, rejected)
    require((rejected[0].get("payload") or {}).get("code") == "STALE_REVISION", rejected)
    require(not rows(executor, operation_id, "feature.event.translated"), "stale callback translated an Event")
    require(
        not any(e["event_type"].startswith("persist.") for e in operation_events(executor.runtime.backend.read_snapshot(), operation_id)),
        "stale callback gained Persist authority",
    )
    before = len(operation_events(executor.runtime.backend.read_snapshot(), operation_id))
    require(executor._reconcile_callback(operation_id) is None, "complete stale rejection remained resumable")
    after = len(operation_events(executor.runtime.backend.read_snapshot(), operation_id))
    require(before == after, "complete stale rejection mutated on replay")


def validate_rejection_crash_window(*, code, expected_status, callback_id):
    snapshot, operation_id, _ = durable_callback_snapshot(callback_id=callback_id)
    before_reservations = reservation_paths(snapshot)
    first = make_executor(snapshot, FixedFeatureGateway(STALE_HEAD))
    first._record_fact(
        operation_id,
        "worker.result.rejected",
        {"code": code, "reason": f"durable {code} rejection before stable stop", "callback_id": callback_id},
    )
    crashed = first.runtime.backend.read_snapshot()
    require(projection_public(rebuild_projection(crashed, operation_id))["status"] == "RUNNING", "fault window not created")
    require(len(rows(first, operation_id, "worker.result.rejected")) == 1, "fault fixture rejection count")

    fresh = make_executor(crashed, NeverReadFeatureGateway())
    require(fresh._reconcile_callback(operation_id) is True, "fresh process did not repair mapped stop")
    repaired = fresh.runtime.backend.read_snapshot()
    require(projection_public(rebuild_projection(repaired, operation_id))["status"] == expected_status, expected_status)
    require(len(rows(fresh, operation_id, "worker.result.rejected")) == 1, "repair duplicated rejection")
    require(not rows(fresh, operation_id, "feature.event.translated"), "repair reprocessed callback")
    require(
        not any(e["event_type"].startswith("persist.") for e in operation_events(repaired, operation_id)),
        "repair gained Persist authority",
    )
    require(reservation_paths(repaired) == before_reservations, "repair created new external reservation")
    before = len(operation_events(repaired, operation_id))
    require(fresh._reconcile_callback(operation_id) is None, "completed repair remained resumable")
    require(len(operation_events(fresh.runtime.backend.read_snapshot(), operation_id)) == before, "second repair mutated Store")


def validate_rejection_crash_windows():
    validate_rejection_crash_window(code="STALE_REVISION", expected_status="BLOCKED", callback_id="callback-crash-stale")
    validate_rejection_crash_window(code="NEEDS_USER", expected_status="NEEDS_USER", callback_id="callback-crash-needs-user")


def validate_transient_read_is_not_reclassified():
    snapshot, operation_id, _ = durable_callback_snapshot(callback_id="callback-transient-read")
    executor = make_executor(snapshot, ExplodingFeatureGateway())
    before = executor.runtime.backend.read_snapshot()
    before_count = len(operation_events(before, operation_id))
    before_status = projection_public(rebuild_projection(before, operation_id))["status"]
    try:
        executor._reconcile_callback(operation_id)
    except RuntimeError as exc:
        require("transient Feature read failure" in str(exc), exc)
    else:
        raise AssertionError("transient Feature read was durably reclassified")
    after = executor.runtime.backend.read_snapshot()
    require(not rows(executor, operation_id, "worker.result.rejected"), "transient read created rejection")
    require(len(operation_events(after, operation_id)) == before_count, "transient read mutated Store")
    require(projection_public(rebuild_projection(after, operation_id))["status"] == before_status, "transient read changed status")


def validate_lineage_successor_stays_fenced():
    runtime = OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
    )
    started = runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id=LINEAGE_FEATURE,
            expected_revision=7,
            idempotency_key="stale-callback-lineage-start",
            occurred_at=NOW,
            trusted_context_digest="trusted",
            operation_profile=VERTICAL_PROFILE,
        )
    )
    operation_id = started.result["operation_id"]
    feature_gateway = MutableReviewFeatureGateway(LINEAGE_HEAD_A)
    dispatch = CountingDispatchGateway()
    base = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=UnusedPersistGateway(),
        dispatch_gateway=dispatch,
        config=TrustedVerticalExecutorConfig(
            target_ref="feature/test",
            trusted_context_digest="trusted",
            max_auto_steps=4,
            effect_lineage_required=True,
            old_writers_quiesced=True,
            rollout_policy_digest="focused-lineage-rollout",
            writer_fence_receipt_digest="focused-writer-fence",
        ),
        resolution_policy_verifier=PolicyFixture().verifier(),
    )
    executor = TrustedRecoveringVerticalExecutor(
        base_executor=base,
        content_loader=lambda _uri: b"unused",
        trusted_role_policy="trusted-role-policy",
        collector_namespace_policy="trusted-collector-policy",
    )
    first = executor.advance_until_stop(operation_id=operation_id)
    require(first["status"] == "WAITING_EXTERNAL", first)
    require(len(dispatch.launch_calls) == 1, dispatch.launch_calls)
    launched = dispatch.launch_calls[0]

    context = TrustedDispatchContext(
        operation_id=operation_id,
        operation_generation=0,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key=launched["semantic_effect_key"],
        external_dispatch_key=launched["external_dispatch_key"],
        dispatch_id=launched["dispatch_id"],
        runtime_receipt_identity="run-1",
        target_repository=REPO,
        target_ref="feature/test",
        feature_id=LINEAGE_FEATURE,
        expected_revision=7,
        feature_stage="code-review",
        task_id=launched["task_id"],
        role="reviewer",
        candidate_pr_number=1,
        candidate_head_sha=LINEAGE_HEAD_A,
        worker_identity="reviewer-worker-a",
        collector_identity="collector-1",
    )
    runtime.commit_replanned(
        lambda snapshot: plan_vertical_callback_record(
            snapshot,
            context=context,
            callback_id="callback-lineage-a",
            worker_payload={"verdict": "BLOCKED", "summary": "stale reviewer callback", "outputs": []},
            receipts=[],
            occurred_at=NOW,
            trusted_context_digest="trusted",
        )
    )
    feature_gateway.candidate_head_sha = LINEAGE_HEAD_B
    require(executor._reconcile_callback(operation_id) is True, "stale A callback not rejected")
    require(projection_public(rebuild_projection(runtime.backend.read_snapshot(), operation_id))["status"] == "BLOCKED", "A did not BLOCK")

    feature_b, manifest_b = feature_gateway.read_feature(operation_id=operation_id)
    action_b = select_vertical_action(feature=feature_b, manifest=manifest_b, occurred_at=NOW)
    policy = executor.resolution_policy_verifier.verify_current()
    blocked = runtime.commit_replanned(
        lambda snapshot: plan_lineage_gated_reservation(
            snapshot,
            operation_id=operation_id,
            generation=0,
            target_repository=REPO,
            feature_id=LINEAGE_FEATURE,
            expected_revision=7,
            current_stage="code-review",
            task_identity=str(action_b.task_identity),
            role=str(action_b.role),
            candidate_head_sha=LINEAGE_HEAD_B,
            current_target_ref="feature/test",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            logical_work_slot=action_b.step,
            task_id=action_b.task_id,
            occurred_at=NOW,
            trusted_context_digest="trusted",
            trusted_profile_digest=policy.proposal_profile_digest,
        )
    )
    require(blocked.result["status"] == "BLOCKED", blocked.result)
    require(blocked.result["reason"] == "UNRESOLVED_PREDECESSOR", blocked.result)
    proposed = blocked.result["proposed_semantic_effect_key"]
    require(runtime.backend.read_snapshot().get(reservation_path(proposed)) is None, "B received reservation")
    before_reservations = reservation_paths(runtime.backend.read_snapshot())
    executor.advance_action(operation_id=operation_id, action=action_b)
    final = runtime.backend.read_snapshot()
    require(reservation_paths(final) == before_reservations, "B retry created second reservation")
    require(len(dispatch.launch_calls) == 1, "B caused second launch")
    authorizations = [e for e in operation_events(final, operation_id) if e["event_type"] == "dispatch.launch.authorized"]
    require(len(authorizations) == 1, "B gained second launch authorization")


def main():
    validate_stale_candidate_converges()
    validate_rejection_crash_windows()
    validate_transient_read_is_not_reclassified()
    validate_lineage_successor_stays_fenced()
    print("Operator stale callback reconciliation validation passed")
    print("- stale callback => exactly one durable STALE_REVISION rejection + BLOCKED")
    print("- rejection-durable/stable-stop-absent crash repairs after restart without callback reprocessing")
    print("- STALE_REVISION=>BLOCKED and NEEDS_USER=>NEEDS_USER are exactly-once across restart")
    print("- transient Feature-read failure stays transient with zero Store mutation")
    print("- lineage-required candidate B remains UNRESOLVED_PREDECESSOR with zero second reservation/launch")


if __name__ == "__main__":
    main()
