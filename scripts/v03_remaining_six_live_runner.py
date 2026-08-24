#!/usr/bin/env python3
"""Trusted-main live producer for the six remaining #221 scenario-pool rows.

Every scenario consumes exactly one fixed #310 slot.  Real GitHub Actions Worker
completion, protected Operator Store state, production Feature Event persistence,
and the existing provenance/closed-ledger authority are reused.  Fault injection
is limited to the exact callback/Persist/candidate-transition boundary named by
the scenario.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

from operator_store import plan_cancel
from operator_store_backends import StoreBackendError
from operator_store_model import operation_events
from operator_vertical import VerticalInvariantError
from operator_vertical_callback import (
    TrustedVerticalCallbackCoordinator,
    process_recorded_callback,
)
from operator_vertical_recovery import (
    plan_vertical_callback_record,
    plan_vertical_takeover,
)
from operator_vertical_gh_aw_github_source import ProductionGhAwVerticalResultCollector
from operator_vertical_store import vertical_projection
from v03_dispatch_recovery_live_runner import (
    _authorization_rows,
    _base,
    _binding_from_claim,
    _events,
    _generic_record,
    _lookup_rows,
    _manifest,
    _operation_id,
    _persist_rows,
    _preflight,
    _seal,
    _select_dispatch,
    _start_only,
    require,
)
from v03_duplicate_worker_completion_collector import ReplaySafeProductionGhAwCollector


CANCEL_BEFORE = "cancel-before-persist-linearization"
PERSIST_BEFORE_CANCEL = "persist-linearized-before-cancel"
DUPLICATE_CALLBACK = "duplicate-callback"
OUT_OF_ORDER = "out-of-order-callback"
DUPLICATE_WORKER = "duplicate-worker-completion"
STALE_CANDIDATE = "stale-candidate-result"

SCENARIOS = (
    CANCEL_BEFORE,
    PERSIST_BEFORE_CANCEL,
    DUPLICATE_CALLBACK,
    OUT_OF_ORDER,
    DUPLICATE_WORKER,
    STALE_CANDIDATE,
)

IDEMPOTENCY = {
    CANCEL_BEFORE: "v03-release-fi-cancel-before-persist-linearization",
    PERSIST_BEFORE_CANCEL: "v03-release-fi-persist-linearized-before-cancel",
    DUPLICATE_CALLBACK: "v03-release-fi-duplicate-callback",
    OUT_OF_ORDER: "v03-release-fi-out-of-order-callback",
    DUPLICATE_WORKER: "v03-release-fi-duplicate-worker-completion",
    STALE_CANDIDATE: "v03-release-fi-stale-candidate-result",
}

SENTINEL_PATH = ".ai-sdlc/v03-stale-candidate-transition.json"


class V03RemainingSixLiveError(RuntimeError):
    pass


class NoSuccessorExecutorProxy:
    """Delegate production authority while suppressing only post-Persist successor dispatch."""

    verification_only = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.suppressed_successor_count = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def advance_until_stop(self, *, operation_id):
        self.suppressed_successor_count += 1
        return self.delegate._public(operation_id)


class CaptureCoordinator:
    """Let the production collector prove one real completion without mutating Store yet."""

    verification_only = True

    def __init__(self, executor, content_loader):
        if not callable(content_loader):
            raise ValueError("capture coordinator requires production content loader")
        self.executor = executor
        self.content_loader = content_loader
        self.captured: dict[str, Any] | None = None

    def handle(self, *, context, callback_id, worker_payload, receipts):
        if self.captured is not None:
            raise V03RemainingSixLiveError("production collector produced multiple callback envelopes")
        self.captured = {
            "context": context,
            "callback_id": str(callback_id),
            "worker_payload": dict(worker_payload),
            "receipts": [dict(row) for row in receipts],
        }
        return {"status": "CAPTURED"}


class CountingPersistGateway:
    def __init__(self, delegate):
        self.delegate = delegate
        self.persist_calls = 0
        self.lookup_calls = 0

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls += 1
        return self.delegate.persist_feature_event(event=event, target_ref=target_ref)

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        return self.delegate.lookup_feature_event(event_id=event_id, target_ref=target_ref)


class CancelBeforeLinearizationFeatureGateway:
    """Inject exactly one cancel after persist.requested and before persist.linearized."""

    verification_only = True

    def __init__(self, *, delegate, runtime, operation_id, trusted_context_digest):
        self.delegate = delegate
        self.runtime = runtime
        self.operation_id = operation_id
        self.trusted_context_digest = trusted_context_digest
        self.cancel_count = 0

    def read_feature(self, *, operation_id):
        if operation_id != self.operation_id:
            raise V03RemainingSixLiveError("cancel-before wrapper escaped exact Operation")
        rows = operation_events(self.runtime.backend.read_snapshot(), operation_id)
        requested = any(row.get("event_type") == "persist.requested" for row in rows)
        linearized = any(row.get("event_type") == "persist.linearized" for row in rows)
        cancelled = any(row.get("event_type") == "operation.cancelled" for row in rows)
        if requested and not linearized and not cancelled:
            self.runtime.commit_replanned(
                lambda snapshot: plan_cancel(
                    snapshot,
                    operation_id=operation_id,
                    reason="release fault injection: cancel before Persist linearization",
                    occurred_at=self.runtime.clock(),
                    trusted_context_digest=self.trusted_context_digest,
                )
            )
            self.cancel_count += 1
        return self.delegate.read_feature(operation_id=operation_id)


class CancelAfterLinearizedPersistGateway:
    """Perform one exact production write, then cancel before its ACK is consumed."""

    verification_only = True

    def __init__(self, *, delegate, runtime, operation_id, trusted_context_digest):
        self.delegate = delegate
        self.runtime = runtime
        self.operation_id = operation_id
        self.trusted_context_digest = trusted_context_digest
        self.persist_calls = 0
        self.lookup_calls = 0
        self.event_id: str | None = None

    def persist_feature_event(self, *, event, target_ref):
        rows = [
            row for row in operation_events(self.runtime.backend.read_snapshot(), self.operation_id)
            if row.get("event_type") == "persist.linearized"
            and (row.get("payload") or {}).get("feature_event_id") == str(event.get("id") or "")
        ]
        require(len(rows) == 1, "production Feature write crossed no/excess Persist linearization")
        self.persist_calls += 1
        require(self.persist_calls == 1, "persist-before-cancel attempted multiple production writes")
        receipt = self.delegate.persist_feature_event(event=event, target_ref=target_ref)
        self.event_id = str(event["id"])
        self.runtime.commit_replanned(
            lambda snapshot: plan_cancel(
                snapshot,
                operation_id=self.operation_id,
                reason="release fault injection: cancel after Persist linearization",
                occurred_at=self.runtime.clock(),
                trusted_context_digest=self.trusted_context_digest,
            )
        )
        return receipt

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        return self.delegate.lookup_feature_event(event_id=event_id, target_ref=target_ref)


def _runtime_receipt(preflight, operation_id: str) -> str:
    rows = _lookup_rows(preflight, operation_id, 0)
    require(len(rows) == 1, "scenario lacks exactly one durable launch lookup")
    payload = rows[0].get("payload") or {}
    require(payload.get("lookup_state") == "LAUNCHED", "scenario Worker was not durably LAUNCHED")
    receipt = str(payload.get("receipt_id") or "")
    require(receipt.isdigit() and int(receipt) > 0, "scenario lacks exact Actions run receipt")
    return receipt


def _launch(preflight, scenario: str):
    operation_id, revision = _start_only(preflight, scenario)
    _feature, action = _select_dispatch(preflight, operation_id)
    base = _base(preflight)
    result = base.advance_action(operation_id=operation_id, action=action)
    require(result.get("status") == "WAITING_EXTERNAL", "scenario did not stop at real Worker")
    binding = _binding_from_claim(preflight, operation_id, 0)
    require(len(_authorization_rows(preflight, operation_id, 0)) == 1, "scenario lacks one launch authorization")
    receipt = _runtime_receipt(preflight, operation_id)
    return operation_id, revision, action, binding, receipt


def _production_capture(preflight, *, operation_id: str, external_dispatch_key: str) -> dict[str, Any]:
    capture = CaptureCoordinator(
        preflight.composition.bundle.executor,
        preflight.composition.result_source.load_content,
    )
    collector = ProductionGhAwVerticalResultCollector(
        callback_coordinator=capture,
        result_source=preflight.composition.result_source,
        workflows=preflight.workflows,
        control_repository=preflight.execution.repository,
        clock=preflight.composition.bundle.runtime.clock,
    )
    collector.handle(operation_id=operation_id, external_dispatch_key=external_dispatch_key)
    if not isinstance(capture.captured, dict):
        raise V03RemainingSixLiveError("production collector did not materialize exact callback")
    return capture.captured


def _coordinator(preflight, *, suppress_successor: bool):
    original = preflight.composition.bundle.callback_coordinator
    executor = preflight.composition.bundle.executor
    proxy = NoSuccessorExecutorProxy(executor) if suppress_successor else executor
    coordinator = TrustedVerticalCallbackCoordinator(
        executor=proxy,
        trusted_role_policy=original.trusted_role_policy,
        collector_namespace_policy=original.collector_namespace_policy,
        content_loader=preflight.composition.result_source.load_content,
    )
    return coordinator, proxy


def _record(
    *,
    preflight,
    scenario: str,
    operation_id: str,
    generation: int,
    binding: dict[str, Any],
    candidate_head_sha: str,
    feature_revision_before: int,
    runtime_receipt_identity: str,
    measurements: dict[str, int],
    extra: dict[str, Any],
):
    record = _generic_record(
        scenario=scenario,
        operation_id=operation_id,
        generation=generation,
        semantic_effect_key=str(binding["semantic_effect_key"]),
        external_dispatch_key=str(binding["external_dispatch_key"]),
        candidate_head_sha=candidate_head_sha,
        feature_revision_before=feature_revision_before,
        runtime_lookup_state="LAUNCHED",
        runtime_receipt_identity=runtime_receipt_identity,
        measurements=measurements,
        extra=extra,
    )
    _seal(preflight, scenario, record)


def run_cancel_before() -> None:
    preflight = _preflight(CANCEL_BEFORE)
    operation_id, revision, action, binding, receipt = _launch(preflight, CANCEL_BEFORE)
    base = _base(preflight)
    original_feature = base.feature_gateway
    original_persist = base.persist_gateway
    counter = CountingPersistGateway(original_persist)
    base.feature_gateway = CancelBeforeLinearizationFeatureGateway(
        delegate=original_feature,
        runtime=base.runtime,
        operation_id=operation_id,
        trusted_context_digest=base.config.trusted_context_digest,
    )
    base.persist_gateway = counter
    try:
        try:
            preflight.composition.collector.handle(
                operation_id=operation_id,
                external_dispatch_key=binding["external_dispatch_key"],
            )
        except StoreBackendError as exc:
            require(exc.code == "CANCELLED_OPERATION", "cancel-before failed with unexpected Store code")
    finally:
        wrapper = base.feature_gateway
        base.feature_gateway = original_feature
        base.persist_gateway = original_persist
    projection = vertical_projection(base.runtime.backend.read_snapshot(), operation_id)
    requested = _events(preflight, operation_id, "persist.requested", 0)
    require(getattr(wrapper, "cancel_count", 0) == 1, "cancel-before was not injected exactly once")
    require(len(requested) == 1, "cancel-before lacks one Persist request")
    require(not _events(preflight, operation_id, "persist.linearized", 0), "cancel-before became linearized")
    require(not _events(preflight, operation_id, "persist.confirmed", 0), "cancel-before became confirmed")
    require(counter.persist_calls == 0 and counter.lookup_calls == 0, "cancel-before touched Feature gateway")
    require(projection.get("status") == "CANCELLED", "cancel-before final Operation is not CANCELLED")
    _record(
        preflight=preflight,
        scenario=CANCEL_BEFORE,
        operation_id=operation_id,
        generation=0,
        binding=binding,
        candidate_head_sha=action.candidate_head_sha,
        feature_revision_before=revision,
        runtime_receipt_identity=receipt,
        measurements={
            "duplicate_feature_write_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "real_worker_execution_count": 1,
            "persist_request_count": 1,
            "persist_linearized_count": 0,
            "external_feature_write_count": 0,
            "final_status": "CANCELLED",
        },
    )


def run_persist_before_cancel() -> None:
    preflight = _preflight(PERSIST_BEFORE_CANCEL)
    operation_id, revision, action, binding, receipt = _launch(preflight, PERSIST_BEFORE_CANCEL)
    base = _base(preflight)
    original = base.persist_gateway
    wrapper = CancelAfterLinearizedPersistGateway(
        delegate=original,
        runtime=base.runtime,
        operation_id=operation_id,
        trusted_context_digest=base.config.trusted_context_digest,
    )
    base.persist_gateway = wrapper
    try:
        result = preflight.composition.collector.handle(
            operation_id=operation_id,
            external_dispatch_key=binding["external_dispatch_key"],
        )
    finally:
        base.persist_gateway = original
    projection = vertical_projection(base.runtime.backend.read_snapshot(), operation_id)
    requested = _events(preflight, operation_id, "persist.requested", 0)
    linearized = _events(preflight, operation_id, "persist.linearized", 0)
    cancelled = _events(preflight, operation_id, "operation.cancelled", 0)
    confirmed = _events(preflight, operation_id, "persist.confirmed", 0)
    require(len(requested) == len(linearized) == len(cancelled) == len(confirmed) == 1,
            "persist-before-cancel lacks exact request/linearize/cancel/confirm sequence")
    require(
        requested[0]["sequence"] < linearized[0]["sequence"] < cancelled[0]["sequence"] < confirmed[0]["sequence"],
        "persist-before-cancel durable ordering drifted",
    )
    require(wrapper.persist_calls == 1 and wrapper.lookup_calls == 0, "persist-before-cancel external write count drifted")
    require(projection.get("status") == "CANCELLED" and result.get("status") == "CANCELLED",
            "persist-before-cancel escaped CANCELLED")
    _record(
        preflight=preflight,
        scenario=PERSIST_BEFORE_CANCEL,
        operation_id=operation_id,
        generation=0,
        binding=binding,
        candidate_head_sha=action.candidate_head_sha,
        feature_revision_before=revision,
        runtime_receipt_identity=receipt,
        measurements={
            "duplicate_feature_write_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "real_worker_execution_count": 1,
            "external_feature_write_count": 1,
            "persist_confirmed_count": 1,
            "feature_event_id": wrapper.event_id,
            "final_status": "CANCELLED",
        },
    )


def run_duplicate_callback() -> None:
    preflight = _preflight(DUPLICATE_CALLBACK)
    operation_id, revision, action, binding, receipt = _launch(preflight, DUPLICATE_CALLBACK)
    captured = _production_capture(
        preflight,
        operation_id=operation_id,
        external_dispatch_key=binding["external_dispatch_key"],
    )
    executor = preflight.composition.bundle.executor
    context = captured["context"]
    callback_id = captured["callback_id"]
    for _ in range(2):
        executor._commit(
            lambda snapshot: plan_vertical_callback_record(
                snapshot,
                context=context,
                callback_id=callback_id,
                worker_payload=captured["worker_payload"],
                receipts=captured["receipts"],
                occurred_at=executor.runtime.clock(),
                trusted_context_digest=executor.config.trusted_context_digest,
            )
        )
    callbacks = [
        row for row in _events(preflight, operation_id, "worker.callback.recorded", 0)
        if (row.get("payload") or {}).get("callback_id") == callback_id
    ]
    require(len(callbacks) == 1, "duplicate callback delivery created multiple durable callback facts")
    coordinator, proxy = _coordinator(preflight, suppress_successor=True)
    result = process_recorded_callback(
        proxy,
        context=context,
        callback_id=callback_id,
        worker_payload=captured["worker_payload"],
        receipts=captured["receipts"],
        trusted_role_policy=coordinator.trusted_role_policy,
        collector_namespace_policy=coordinator.collector_namespace_policy,
        content_loader=preflight.composition.result_source.load_content,
        continue_after=False,
    )
    validated = [
        row for row in _events(preflight, operation_id, "worker.result.validated", 0)
        if (row.get("payload") or {}).get("callback_id") == callback_id
    ]
    translated = [
        row for row in _events(preflight, operation_id, "feature.event.translated", 0)
        if (row.get("payload") or {}).get("callback_id") == callback_id
    ]
    require(len(validated) == 1, "duplicate callback did not validate actual completion exactly once")
    require(len(translated) <= 1, "duplicate callback translated more than one Feature Event")
    require(len(_events(preflight, operation_id, "persist.confirmed", 0)) <= 1,
            "duplicate callback confirmed Feature Persist more than once")
    _record(
        preflight=preflight,
        scenario=DUPLICATE_CALLBACK,
        operation_id=operation_id,
        generation=0,
        binding=binding,
        candidate_head_sha=action.candidate_head_sha,
        feature_revision_before=revision,
        runtime_receipt_identity=receipt,
        measurements={
            "duplicate_feature_write_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "real_worker_execution_count": 1,
            "callback_delivery_count": 2,
            "durable_callback_count": 1,
            "validated_result_count": 1,
            "translated_feature_event_count": len(translated),
            "persist_confirmed_count": len(_events(preflight, operation_id, "persist.confirmed", 0)),
            "public_status": result.get("status"),
        },
    )


def run_out_of_order() -> None:
    preflight = _preflight(OUT_OF_ORDER)
    operation_id, revision, action, binding, receipt = _launch(preflight, OUT_OF_ORDER)
    captured = _production_capture(
        preflight,
        operation_id=operation_id,
        external_dispatch_key=binding["external_dispatch_key"],
    )
    base = _base(preflight)
    base.runtime.commit_replanned(
        lambda snapshot: plan_vertical_takeover(
            snapshot,
            operation_id=operation_id,
            occurred_at=base.runtime.clock(),
            trusted_context_digest=base.config.trusted_context_digest,
        )
    )
    projection = vertical_projection(base.runtime.backend.read_snapshot(), operation_id)
    require(int(projection.get("generation", -1)) == 1, "out-of-order scenario did not enter G1")
    coordinator, _proxy = _coordinator(preflight, suppress_successor=True)
    try:
        coordinator.handle(
            context=captured["context"],
            callback_id=captured["callback_id"],
            worker_payload=captured["worker_payload"],
            receipts=captured["receipts"],
        )
    except VerticalInvariantError as exc:
        require(exc.code == "SUPERSEDED_GENERATION", "out-of-order callback failed with wrong code")
    else:
        raise V03RemainingSixLiveError("superseded G0 callback was accepted after G1 takeover")
    require(not _events(preflight, operation_id, "worker.callback.recorded", 0),
            "out-of-order G0 callback became durable after takeover")
    require(not _events(preflight, operation_id, "feature.event.translated", 0),
            "out-of-order callback translated lifecycle authority")
    require(not _persist_rows(preflight, operation_id), "out-of-order callback created Persist authority")
    _record(
        preflight=preflight,
        scenario=OUT_OF_ORDER,
        operation_id=operation_id,
        generation=1,
        binding=binding,
        candidate_head_sha=action.candidate_head_sha,
        feature_revision_before=revision,
        runtime_receipt_identity=receipt,
        measurements={
            "duplicate_feature_write_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "real_worker_execution_count": 1,
            "superseded_callback_rejected_count": 1,
            "stale_callback_durable_count": 0,
            "feature_persist_count": 0,
            "final_generation": 1,
        },
    )


def run_duplicate_worker() -> None:
    preflight = _preflight(DUPLICATE_WORKER)
    operation_id, revision, action, binding, receipt = _launch(preflight, DUPLICATE_WORKER)
    original = preflight.composition.bundle.callback_coordinator
    proxy = NoSuccessorExecutorProxy(preflight.composition.bundle.executor)
    coordinator = TrustedVerticalCallbackCoordinator(
        executor=proxy,
        trusted_role_policy=original.trusted_role_policy,
        collector_namespace_policy=original.collector_namespace_policy,
        content_loader=preflight.composition.result_source.load_content,
    )
    production = ProductionGhAwVerticalResultCollector(
        callback_coordinator=coordinator,
        result_source=preflight.composition.result_source,
        workflows=preflight.workflows,
        control_repository=preflight.execution.repository,
        clock=preflight.composition.bundle.runtime.clock,
    )
    collector = ReplaySafeProductionGhAwCollector(delegate=production)
    first = collector.handle(operation_id=operation_id, external_dispatch_key=binding["external_dispatch_key"])
    before = list(operation_events(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id))
    second = collector.handle(operation_id=operation_id, external_dispatch_key=binding["external_dispatch_key"])
    after = list(operation_events(preflight.composition.bundle.runtime.backend.read_snapshot(), operation_id))
    callbacks = _events(preflight, operation_id, "worker.callback.recorded", 0)
    translated = _events(preflight, operation_id, "feature.event.translated", 0)
    confirmed = _events(preflight, operation_id, "persist.confirmed", 0)
    require(first == second, "duplicate Worker replay changed public durable result")
    require(len(after) == len(before), "duplicate Worker replay mutated protected Store")
    require(len(callbacks) == len(translated) == len(confirmed) == 1,
            "duplicate Worker completion did not converge to one callback/Event/Persist")
    require(len(collector.replay_callback_ids) == 1, "duplicate Worker completion was not recognized read-only")
    require(proxy.suppressed_successor_count == 1, "duplicate Worker scenario suppressed wrong successor count")
    _record(
        preflight=preflight,
        scenario=DUPLICATE_WORKER,
        operation_id=operation_id,
        generation=0,
        binding=binding,
        candidate_head_sha=action.candidate_head_sha,
        feature_revision_before=revision,
        runtime_receipt_identity=receipt,
        measurements={
            "duplicate_external_effect_count": 0,
            "duplicate_feature_write_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "real_worker_execution_count": 1,
            "collector_resolution_count": 2,
            "durable_callback_count": 1,
            "translated_feature_event_count": 1,
            "persist_confirmed_count": 1,
            "second_completion_store_mutation_count": 0,
        },
    )


def _api_json(*, method: str, url: str, token: str, payload: dict[str, Any] | None = None):
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=raw, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "ai-sdlc-v03-stale-candidate-live")
    if raw is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=30) as response:
            body = response.read()
            return int(response.status), json.loads(body.decode("utf-8")) if body else {}
    except error.HTTPError as exc:
        body = exc.read()
        try:
            parsed = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            parsed = {}
        return int(exc.code), parsed


def _advance_candidate(preflight, *, old_head: str) -> str:
    token = str(os.environ.get("AI_SDLC_EVENT_WRITE_TOKEN") or "")
    api = str(os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    require(bool(token), "stale-candidate transition lacks bounded contents-write token")
    repository = preflight.execution.repository
    slot = preflight.slot
    content_url = f"{api}/repos/{repository}/contents/{SENTINEL_PATH}"
    status, existing = _api_json(
        method="GET",
        url=content_url + "?ref=" + slot.target_ref,
        token=token,
    )
    require(status == 404, "stale-candidate slot was already consumed by a prior transition")
    sentinel = {
        "schema_version": "ai-sdlc.v03-stale-candidate-transition/v1",
        "scenario": STALE_CANDIDATE,
        "old_candidate_head_sha": old_head,
        "trusted_main_head_sha": preflight.execution.installation_commit_sha,
        "workflow_run_id": str(os.environ.get("GITHUB_RUN_ID") or ""),
        "release_fixture_only": True,
    }
    status, created = _api_json(
        method="PUT",
        url=content_url,
        token=token,
        payload={
            "message": "test(v0.3): advance stale-candidate fault fixture",
            "content": base64.b64encode((json.dumps(sentinel, sort_keys=True) + "\n").encode("utf-8")).decode("ascii"),
            "branch": slot.target_ref,
        },
    )
    require(status == 201, f"stale-candidate transition commit failed with HTTP {status}")
    commit = created.get("commit") or {}
    new_head = str(commit.get("sha") or "").lower()
    require(len(new_head) == 40 and new_head != old_head, "stale-candidate transition lacks distinct exact B head")
    current = preflight.composition.candidate_provider.current_candidate(
        operation_id="v03-stale-candidate-transition",
        repository=repository,
        feature_id=slot.feature_id,
        target_ref=slot.target_ref,
    )
    require(current.candidate_head_sha == new_head, "fixture PR current head does not equal transition B")
    return new_head


def run_stale_candidate() -> None:
    preflight = _preflight(STALE_CANDIDATE)
    operation_id, revision, action, binding, receipt = _launch(preflight, STALE_CANDIDATE)
    captured = _production_capture(
        preflight,
        operation_id=operation_id,
        external_dispatch_key=binding["external_dispatch_key"],
    )
    old_head = str(action.candidate_head_sha)
    require(captured["context"].candidate_head_sha == old_head, "captured callback lost candidate A")
    new_head = _advance_candidate(preflight, old_head=old_head)
    coordinator, _proxy = _coordinator(preflight, suppress_successor=True)
    result = coordinator.handle(
        context=captured["context"],
        callback_id=captured["callback_id"],
        worker_payload=captured["worker_payload"],
        receipts=captured["receipts"],
    )
    require(result.get("status") == "BLOCKED", "stale candidate result did not fail closed")
    rejected = [
        row for row in _events(preflight, operation_id, "worker.result.rejected", 0)
        if (row.get("payload") or {}).get("callback_id") == captured["callback_id"]
    ]
    validated = [
        row for row in _events(preflight, operation_id, "worker.result.validated", 0)
        if (row.get("payload") or {}).get("callback_id") == captured["callback_id"]
    ]
    require(len(rejected) == 1 and (rejected[0].get("payload") or {}).get("code") == "STALE_REVISION",
            "candidate A completion was not rejected exactly once as STALE_REVISION")
    require(not validated, "stale candidate A evidence was accepted")
    require(not _events(preflight, operation_id, "feature.event.translated", 0),
            "stale candidate A translated lifecycle authority")
    require(not _persist_rows(preflight, operation_id), "stale candidate A created Persist authority")
    _record(
        preflight=preflight,
        scenario=STALE_CANDIDATE,
        operation_id=operation_id,
        generation=0,
        binding=binding,
        candidate_head_sha=old_head,
        feature_revision_before=revision,
        runtime_receipt_identity=receipt,
        measurements={
            "stale_evidence_accepted_count": 0,
            "unauthorized_lifecycle_transition_count": 0,
        },
        extra={
            "real_worker_execution_count": 1,
            "old_candidate_head_sha": old_head,
            "current_candidate_head_sha": new_head,
            "candidate_transition_commit_count": 1,
            "stale_result_rejected_count": 1,
            "validated_stale_result_count": 0,
            "feature_persist_count": 0,
            "fixture_transition_path": SENTINEL_PATH,
        },
    )


RUNNERS = {
    CANCEL_BEFORE: run_cancel_before,
    PERSIST_BEFORE_CANCEL: run_persist_before_cancel,
    DUPLICATE_CALLBACK: run_duplicate_callback,
    OUT_OF_ORDER: run_out_of_order,
    DUPLICATE_WORKER: run_duplicate_worker,
    STALE_CANDIDATE: run_stale_candidate,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    args = parser.parse_args()
    RUNNERS[args.scenario]()


if __name__ == "__main__":
    main()
