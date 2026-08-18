#!/usr/bin/env python3
"""Lane-A adversarial fault coverage through the real OpenAI Responses boundary.

Every scenario first crosses the production Responses parser/request builder and
protected-Store call journal. Rare Operator states are then forced only at the
Design-v2-authorized lower-level trusted test seam using already accepted Store,
Vertical, Decision/Notification and Effect Lineage deterministic validators.

This file is never Supported-production evidence. Lane B remains mandatory.
"""
from __future__ import annotations

import importlib
import json

from operator_api import API_VERSION
from operator_openai_responses import ADAPTER_ID, OpenAIResponsesOperatorAdapter, TrustedResponsesRegistration
from operator_openai_responses_journal import StoreResponsesCallJournal
from operator_store import (
    StoreCommandError,
    plan_authorize_launch,
    plan_cancel,
    plan_dispatch_claim,
    plan_launch_lookup,
    plan_operation_start,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, operation_events, rebuild_projection
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_controller import select_vertical_action
from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
from operator_vertical_recovery import plan_vertical_takeover
from operator_vertical_store import plan_vertical_semantic_reservation
from validate_operator_decisions_notifications import (
    test_decision_lifecycle_and_adversaries,
    test_expiry_reconcile_and_notifications,
    test_policy_verifier,
)
from validate_operator_effect_lineage_v2 import validate_candidate_block_and_safe_never_authorized_resolution
from validate_operator_vertical import HEAD, NOW, REPO as VERTICAL_REPO, FEATURE as VERTICAL_FEATURE, _base_manifest, _feature
from validate_operator_vertical_reconcile import (
    DispatchGateway,
    PersistGateway,
    Runtime as VerticalTestRuntime,
    validate_launch_ack_recovery_and_unknown_boundary,
    validate_persist_linearization_recovery_and_cancel_order,
)
from validate_operator_vertical_recovery import validate_unknown_takeover_inheritance

REPO = "DREAM-XIN/responses-fault-fixture"
FEATURE = "F-RESPONSES-FAULT-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
HEAD2 = "e" * 40


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _call(name: str, call_id: str, arguments: dict) -> dict:
    return {
        "type": "function_call",
        "id": f"fc-{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
        "status": "completed",
    }


def _runtime() -> tuple[MemoryStateRefBackend, OperatorStoreRuntime]:
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T11:40:00Z",
    )
    return backend, runtime


def _registration() -> TrustedResponsesRegistration:
    return TrustedResponsesRegistration(
        registration_id="responses-fault-registration",
        provider_scope_id="responses-fault-provider-scope",
        target_repository=REPO,
        feature_refs={FEATURE: "refs/heads/feature/F-RESPONSES-FAULT-0001"},
        trusted_context={
            "trusted_identity": {
                "service_id": "responses-fault-service",
                "runtime_id": "responses-fault-runtime",
                "authorization_context": "responses-fault-policy",
            },
            "trusted_scope": {"repositories": [REPO], "feature_ids": [FEATURE]},
            "trusted_principal": "responses-fault-principal",
        },
        human_principal="responses-fault-principal",
    )


def _start_store_fixture() -> tuple[StoreSnapshot, str]:
    snapshot = StoreSnapshot(ref_sha="s0")
    start = plan_operation_start(
        snapshot,
        target_repository=VERTICAL_REPO,
        feature_id=VERTICAL_FEATURE,
        expected_revision=10,
        idempotency_key="responses-fault-start",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    return apply_plan_to_snapshot(snapshot, start, new_ref_sha="s1"), start.result["operation_id"]


def _reserve_claim(snapshot: StoreSnapshot, operation_id: str):
    reservation = plan_vertical_semantic_reservation(
        snapshot,
        operation_id=operation_id,
        generation=0,
        target_repository=VERTICAL_REPO,
        feature_id=VERTICAL_FEATURE,
        expected_revision=10,
        current_stage="implementation",
        task_identity="vertical:implementation:10",
        role="developer",
        candidate_head_sha=HEAD,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = apply_plan_to_snapshot(snapshot, reservation, new_ref_sha="s2")
    claim = plan_dispatch_claim(
        snapshot,
        operation_id=operation_id,
        generation=0,
        effect_key=reservation.result["semantic_effect_key"],
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = apply_plan_to_snapshot(snapshot, claim, new_ref_sha="s3")
    return snapshot, reservation.result, claim.result


def validate_cancel_before_after_launch_linearization() -> None:
    # Cancellation wins before durable launch authorization.
    snapshot, operation_id = _start_store_fixture()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = apply_plan_to_snapshot(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="cancel-before-launch",
            occurred_at=NOW,
            trusted_context_digest="trusted",
        ),
        new_ref_sha="s4",
    )
    try:
        plan_authorize_launch(
            snapshot,
            operation_id=operation_id,
            generation=0,
            claim_id=claim["claim_id"],
            dispatch_id="responses-cancel-before",
            occurred_at=NOW,
            trusted_context_digest="trusted",
            verified_expected_revision=10,
            verified_stage="implementation",
            verified_candidate_head_sha=HEAD,
        )
        raise AssertionError("post-cancel launch authorization unexpectedly accepted")
    except StoreCommandError as exc:
        require(exc.code == "CANCELLED_OPERATION", f"unexpected cancel-before-launch code: {exc.code}")
    require(
        reservation["external_dispatch_key"] not in rebuild_projection(snapshot, operation_id)["authorized_dispatches"],
        "cancel-before-launch left an authorized external key",
    )

    # Authorization wins first. Cancellation does not revoke that exact already-
    # linearized key, but the operation remains CANCELLED after exact receipt observation.
    snapshot, operation_id = _start_store_fixture()
    snapshot, reservation, claim = _reserve_claim(snapshot, operation_id)
    snapshot = apply_plan_to_snapshot(
        snapshot,
        plan_authorize_launch(
            snapshot,
            operation_id=operation_id,
            generation=0,
            claim_id=claim["claim_id"],
            dispatch_id="responses-launch-before-cancel",
            occurred_at=NOW,
            trusted_context_digest="trusted",
            verified_expected_revision=10,
            verified_stage="implementation",
            verified_candidate_head_sha=HEAD,
        ),
        new_ref_sha="s4",
    )
    snapshot = apply_plan_to_snapshot(
        snapshot,
        plan_cancel(
            snapshot,
            operation_id=operation_id,
            reason="cancel-after-launch-authorization",
            occurred_at=NOW,
            trusted_context_digest="trusted",
        ),
        new_ref_sha="s5",
    )
    snapshot = apply_plan_to_snapshot(
        snapshot,
        plan_launch_lookup(
            snapshot,
            operation_id=operation_id,
            generation=0,
            external_dispatch_key_value=reservation["external_dispatch_key"],
            lookup_state="LAUNCHED",
            receipt_id="responses-run-1",
            occurred_at=NOW,
            trusted_context_digest="trusted",
        ),
        new_ref_sha="s6",
    )
    projection = rebuild_projection(snapshot, operation_id)
    require(projection["status"] == "CANCELLED", "post-authorize cancellation was lost")
    require(
        reservation["external_dispatch_key"] in projection["authorized_dispatches"],
        "cancellation erased an already-linearized exact launch authorization",
    )


def validate_candidate_stale_before_launch() -> None:
    snapshot, operation_id = _start_store_fixture()
    runtime = VerticalTestRuntime(snapshot)
    manifest = _base_manifest()

    class FlippingFeatureGateway:
        def __init__(self):
            self.calls = 0

        def read_feature(self, *, operation_id):
            self.calls += 1
            head = HEAD if self.calls == 1 else HEAD2
            return _feature(manifest, head=head), manifest

    feature_gateway = FlippingFeatureGateway()
    dispatch = DispatchGateway("NOT_LAUNCHED")
    executor = TrustedVerticalExecutor(
        runtime=runtime,
        feature_gateway=feature_gateway,
        persist_gateway=PersistGateway(),
        dispatch_gateway=dispatch,
        config=TrustedVerticalExecutorConfig(
            target_ref="feature/test",
            trusted_context_digest="trusted",
            max_auto_steps=8,
            effect_lineage_required=False,
            old_writers_quiesced=False,
            legacy_compatibility_mode=True,
        ),
    )
    initial = _feature(manifest, head=HEAD)
    action = select_vertical_action(feature=initial, manifest=manifest, occurred_at=NOW)
    require(action.kind == "dispatch", "fixture did not select a dispatch action")
    try:
        executor.advance_action(operation_id=operation_id, action=action)
        raise AssertionError("candidate changed before launch but dispatch unexpectedly continued")
    except VerticalInvariantError as exc:
        require(exc.code == "STALE_REVISION", f"unexpected stale-candidate code: {exc.code}")
    require(not dispatch.launch_calls, "stale candidate reached external launch")
    events = operation_events(runtime.backend.read_snapshot(), operation_id)
    require(
        not any(event["event_type"] == "dispatch.launch.authorized" for event in events),
        "stale candidate reached durable launch authorization",
    )


def _persist_classification_available() -> bool:
    """Run the accepted classification suite only after that dependency lands."""

    try:
        module = importlib.import_module("validate_operator_persist_reconcile_classification")
    except ImportError:
        return False
    main = getattr(module, "main", None)
    if not callable(main):
        return False
    main()
    return True


class ScenarioBackend:
    test_only = True

    def __init__(self, capability: str, scenario):
        self.capability = capability
        self.scenario = scenario
        self.calls = 0

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        self.calls += 1
        require(request["client_identity"]["adapter_id"] == ADAPTER_ID, "Responses adapter identity was lost")
        if self.capability == "operation.start":
            require(
                request.get("target") == {"repository": REPO, "feature_id": FEATURE},
                "Responses Feature-scoped start target binding drifted",
            )
        else:
            require("target" not in request, f"{self.capability} unexpectedly accepted client-selected Feature target")
        self.scenario()
        if self.capability == "operation.start":
            return {"operation_id": "op-responses-fault", "generation": 0, "status": "RUNNING"}
        if self.capability == "decision.respond":
            return {"decision_id": "decision-responses-fault", "status": "RESPONDED"}
        if self.capability == "notification.ack":
            return {"notification_id": "notification-responses-fault", "status": "ACKNOWLEDGED"}
        raise AssertionError(self.capability)


class SecretFailureBackend:
    test_only = True

    def __init__(self):
        self.calls = 0

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        self.calls += 1
        raise RuntimeError("Bearer RESPONSES-TOP-SECRET token password=responses-secret")


def _decode(output: dict) -> dict:
    require(output["type"] == "function_call_output", "Responses output type drifted")
    return json.loads(output["output"])


def _invoke_replayed(adapter, store_backend, backend, item, *, expected_ok: bool) -> dict:
    first = adapter.invoke_function_call(item)
    ref_after_first = store_backend.read_snapshot().ref_sha
    second = adapter.invoke_function_call(item)
    require(first == second, "exact Responses replay changed output")
    require(store_backend.read_snapshot().ref_sha == ref_after_first, "exact Responses replay mutated Store")
    require(backend.calls == 1, "exact Responses replay re-executed trusted fault seam")
    body = _decode(first)
    require(body["ok"] is expected_ok, f"unexpected canonical response: {body}")
    return body


def main() -> None:
    store_backend, runtime = _runtime()
    registration = _registration()

    def vertical_fault_group():
        validate_cancel_before_after_launch_linearization()
        validate_launch_ack_recovery_and_unknown_boundary()
        validate_unknown_takeover_inheritance()
        validate_candidate_stale_before_launch()
        validate_candidate_block_and_safe_never_authorized_resolution()
        validate_persist_linearization_recovery_and_cancel_order()

    def decision_fault_group():
        test_policy_verifier()
        test_decision_lifecycle_and_adversaries()

    def notification_fault_group():
        test_expiry_reconcile_and_notifications()

    start_backend = ScenarioBackend("operation.start", vertical_fault_group)
    decision_backend = ScenarioBackend("decision.respond", decision_fault_group)
    notification_backend = ScenarioBackend("notification.ack", notification_fault_group)
    secret_backend = SecretFailureBackend()
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends={
            "operation.start": start_backend,
            "decision.respond": decision_backend,
            "notification.ack": notification_backend,
            "feature.status": secret_backend,
        },
        journal=StoreResponsesCallJournal(runtime),
    )

    _invoke_replayed(
        adapter,
        store_backend,
        start_backend,
        _call(
            "aisdlc_v1_operation_start",
            "fault-vertical",
            {
                "api_version": API_VERSION,
                "feature_id": FEATURE,
                "expected_feature_revision": 19,
                "mode": "ASSISTED",
            },
        ),
        expected_ok=True,
    )
    _invoke_replayed(
        adapter,
        store_backend,
        decision_backend,
        _call(
            "aisdlc_v1_decision_respond",
            "fault-decision",
            {
                "api_version": API_VERSION,
                "decision_id": "decision-responses-fault",
                "response": "approve",
            },
        ),
        expected_ok=True,
    )
    _invoke_replayed(
        adapter,
        store_backend,
        notification_backend,
        _call(
            "aisdlc_v1_notification_ack",
            "fault-notification",
            {"api_version": API_VERSION, "notification_id": "notification-responses-fault"},
        ),
        expected_ok=True,
    )
    secret = _invoke_replayed(
        adapter,
        store_backend,
        secret_backend,
        _call(
            "aisdlc_v1_feature_status",
            "fault-redaction",
            {"api_version": API_VERSION, "feature_id": FEATURE},
        ),
        expected_ok=False,
    )
    rendered = json.dumps(secret, sort_keys=True)
    require("RESPONSES-TOP-SECRET" not in rendered, "secret leaked through Responses error")
    require("responses-secret" not in rendered, "password leaked through Responses error")

    persist_classification = _persist_classification_available()
    coverage = {
        "6_cancel_before_after_launch_linearization": True,
        "7_external_lookup_unknown_fail_closed": True,
        "8_lost_launch_ack_same_key_recovery": True,
        "9_generation_takeover_stable_external_identity": True,
        "10_candidate_stale_before_launch": True,
        "11_effect_lineage_blocked_successor": True,
        "12_decision_invalid_stale_expired_policy_mismatch": True,
        "13_notification_duplicate_ack": True,
        "14_persist_requested_linearized_ack_loss": True,
        "14_persist_deterministic_rejection_classification": persist_classification,
        "15_secret_error_redaction": True,
    }
    print("OpenAI Responses Lane-A fault-injection validation passed")
    print(json.dumps(coverage, indent=2, sort_keys=True))
    print("- every executed group crossed the production Responses parser/request/journal boundary first")
    print("- lower-level fault seams reuse accepted deterministic Operator semantics")
    print("- exact Responses replay is read-only and does not re-execute fault logic")
    print("- Lane A remains insufficient for Supported production status")
    if not persist_classification:
        print("- WU6 remains partially blocked until deterministic Persist rejection classification lands on main")


if __name__ == "__main__":
    main()