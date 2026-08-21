#!/usr/bin/env python3
"""Validate duplicate first-attempt Worker completion against production collector semantics."""
from __future__ import annotations

from operator_store_model import operation_events
from operator_vertical_callback import TrustedVerticalCallbackCoordinator
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_collector import TrustedGhAwResolvedResult, TrustedGhAwRun
from operator_vertical_gh_aw_github_source import ProductionGhAwVerticalResultCollector
from v03_duplicate_worker_completion_collector import ReplaySafeProductionGhAwCollector
from v03_real_runtime_lost_ack_orchestration import derive_lost_ack_dispatch_binding
from validate_v03_real_runtime_lost_ack_orchestration import (
    CANDIDATE,
    FEATURE,
    IDEMPOTENCY,
    REF,
    REPOSITORY,
    TRUSTED_DIGEST,
    Clock,
    ExternalRuntime,
    FeatureGateway,
    LookupFirstDispatchGateway,
    make_bundle,
    make_executor,
    make_runtime,
    manifest,
)

RUN_ID = 101
WORKFLOW = "ai-sdlc-gh-aw-developer-codex.lock.yml"


def require(value, message):
    if not value:
        raise AssertionError(message)


class ExactPersistGateway:
    def __init__(self):
        self.events = {}
        self.write_count = 0
        self.lookup_count = 0

    def persist_feature_event(self, *, event, target_ref):
        event_id = str(event["id"])
        existing = self.events.get(event_id)
        require(existing is None or existing == event, "duplicate completion changed exact Feature Event")
        if existing is None:
            self.events[event_id] = dict(event)
            self.write_count += 1
        return {"event_id": event_id, "target_ref": target_ref, "result_revision": 12}

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_count += 1
        if str(event_id) not in self.events:
            return None
        return {"event_id": str(event_id), "target_ref": target_ref, "result_revision": 12}


class NoSuccessorExecutorProxy:
    """Delegate every production authority except automatic post-Persist successor dispatch."""

    verification_only = True

    def __init__(self, delegate):
        self.delegate = delegate
        self.suppressed_successor_count = 0

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def advance_until_stop(self, *, operation_id):
        self.suppressed_successor_count += 1
        return self.delegate._public(operation_id)


class SameCompletionSource:
    def __init__(self, *, binding, receipt_identity):
        self.binding = binding
        self.receipt_identity = receipt_identity
        self.resolve_count = 0
        self.trusted_contexts = []
        self.payload = {
            "status": "COMPLETED",
            "summary": "exact same first-attempt Developer completion",
            "outputs": [],
        }
        self.result = TrustedGhAwResolvedResult(
            run=TrustedGhAwRun(
                run_id=RUN_ID,
                run_url=f"https://github.com/{REPOSITORY}/actions/runs/{RUN_ID}",
                receipt_identity=receipt_identity,
                control_repository=REPOSITORY,
                workflow_file=WORKFLOW,
                workflow_ref="main",
                event="workflow_dispatch",
                status="completed",
                conclusion="success",
                display_title=f"AI-SDLC gh-aw {binding.external_dispatch_key}",
                external_dispatch_key=binding.external_dispatch_key,
                role="developer",
                task_id=binding.task_identity,
                worker_identity="fixture-developer-worker",
                collector_identity="fixture-production-collector",
                candidate_pr_number=None,
                candidate_head_sha=None,
            ),
            role_payload=dict(self.payload),
            outputs=tuple(),
        )

    def resolve(self, *, external_dispatch_key, expected_receipt_identity, trusted_context):
        require(external_dispatch_key == self.binding.external_dispatch_key, "source received wrong dispatch key")
        require(expected_receipt_identity == self.receipt_identity, "source received wrong durable receipt")
        self.resolve_count += 1
        self.trusted_contexts.append(dict(trusted_context))
        return self.result

    def load_content(self, uri):
        raise AssertionError("duplicate completion fixture declares zero outputs")


def count_events(backend, operation_id, event_type, *, callback_id=None):
    rows = [
        row for row in operation_events(backend.read_snapshot(), operation_id)
        if row["event_type"] == event_type
    ]
    if callback_id is not None:
        rows = [row for row in rows if (row.get("payload") or {}).get("callback_id") == callback_id]
    return len(rows)


def main():
    fixture = manifest()
    idempotency = "fi-duplicate-worker-completion"
    binding = derive_lost_ack_dispatch_binding(
        repository=REPOSITORY,
        feature_id=FEATURE,
        target_ref=REF,
        manifest=fixture,
        candidate_pr_number=230,
        candidate_head_sha=CANDIDATE,
        idempotency_key=idempotency,
        occurred_at="2026-08-11T00:00:00Z",
    )

    from operator_store_git import MemoryStateRefBackend

    backend = MemoryStateRefBackend(
        repository=REPOSITORY,
        state_ref="refs/heads/ai-sdlc-operator-state",
    )
    clock = Clock()
    external = ExternalRuntime()
    feature_gateway = FeatureGateway(fixture)
    dispatch_gateway = LookupFirstDispatchGateway(external)
    runtime = make_runtime(backend, clock)
    executor = make_executor(runtime, feature_gateway, dispatch_gateway)
    persist_gateway = ExactPersistGateway()
    executor.base.persist_gateway = persist_gateway
    bundle = make_bundle(runtime, executor)

    start = bundle.backends["operation.start"].invoke(
        {
            "idempotency_key": idempotency,
            "target": {"repository": REPOSITORY, "feature_id": FEATURE},
            "context": {"expected_feature_revision": 11},
        },
        bundle.write_bundle.read_bundle.trusted_context_provider.for_request(
            {"repository": REPOSITORY, "feature_id": FEATURE}
        ),
    )
    require(start["status"] == "WAITING_EXTERNAL", "fixture did not stop at first Worker")
    require(external.post_count == 1, "fixture did not create exactly one Worker execution")
    require(dispatch_gateway.launch_calls == [binding.external_dispatch_key], "fixture launched wrong dispatch key")

    lookup_events = [
        event for event in operation_events(backend.read_snapshot(), binding.operation_id)
        if event["event_type"] == "dispatch.launch.lookup-recorded"
    ]
    require(len(lookup_events) == 1, "fixture lacks one exact durable Worker receipt")
    receipt_identity = str((lookup_events[0].get("payload") or {})["receipt_id"])

    proxy = NoSuccessorExecutorProxy(executor)
    coordinator = TrustedVerticalCallbackCoordinator(
        executor=proxy,
        trusted_role_policy="fixture-role-policy",
        collector_namespace_policy="fixture-collector-policy",
        content_loader=lambda _uri: b"unused",
    )
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow=WORKFLOW,
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    source = SameCompletionSource(binding=binding, receipt_identity=receipt_identity)
    production = ProductionGhAwVerticalResultCollector(
        callback_coordinator=coordinator,
        result_source=source,
        workflows=workflows,
        control_repository=REPOSITORY,
        clock=clock,
    )
    collector = ReplaySafeProductionGhAwCollector(delegate=production)

    first = collector.handle(
        operation_id=binding.operation_id,
        external_dispatch_key=binding.external_dispatch_key,
    )
    callbacks = [
        event for event in operation_events(backend.read_snapshot(), binding.operation_id)
        if event["event_type"] == "worker.callback.recorded"
    ]
    require(len(callbacks) == 1, "first production collection did not create one durable callback")
    callback_id = str((callbacks[0].get("payload") or {})["callback_id"])
    require(count_events(backend, binding.operation_id, "worker.result.validated", callback_id=callback_id) == 1, "first completion not validated exactly once")
    require(count_events(backend, binding.operation_id, "feature.event.translated", callback_id=callback_id) == 1, "first completion not translated exactly once")
    require(count_events(backend, binding.operation_id, "persist.confirmed") == 1, "first completion not Persist-confirmed exactly once")
    require(persist_gateway.write_count == 1, "first completion did not perform one exact Feature Event write")
    require(proxy.suppressed_successor_count == 1, "verification proxy did not suppress exactly one successor dispatch")

    event_count_before = len(operation_events(backend.read_snapshot(), binding.operation_id))
    second = collector.handle(
        operation_id=binding.operation_id,
        external_dispatch_key=binding.external_dispatch_key,
    )
    event_count_after = len(operation_events(backend.read_snapshot(), binding.operation_id))

    require(source.resolve_count == 2, "duplicate completion was not independently re-read twice")
    require(collector.replay_callback_ids == [callback_id], "duplicate completion did not derive exact same callback id")
    require(event_count_after == event_count_before, "duplicate completion mutated durable Store")
    require(count_events(backend, binding.operation_id, "worker.callback.recorded") == 1, "duplicate completion created second callback")
    require(count_events(backend, binding.operation_id, "worker.result.validated", callback_id=callback_id) == 1, "duplicate completion created second validated result")
    require(count_events(backend, binding.operation_id, "feature.event.translated", callback_id=callback_id) == 1, "duplicate completion translated second Feature Event")
    require(count_events(backend, binding.operation_id, "persist.confirmed") == 1, "duplicate completion created second Persist confirmation")
    require(persist_gateway.write_count == 1, "duplicate completion created second external Feature Event write")
    require(external.post_count == 1, "duplicate completion caused second Worker execution")
    require(dispatch_gateway.launch_calls == [binding.external_dispatch_key], "duplicate completion caused second Worker launch")
    require(first == second, "read-only duplicate completion replay changed public durable result")

    print("v0.3 duplicate Worker completion production-collector validation passed")
    print("- same first-attempt run is independently resolved twice and derives one exact callback id")
    print("- first completion creates one callback, one validated result, one translated Event and one Persist confirmation")
    print("- second completion is read-only after exact durable replay proof: zero Store mutation, zero second Persist, zero second Worker launch")
    print("- post-Persist successor dispatch is suppressed only by a verification-only executor proxy")
    print("- deterministic support only; real #221 Worker completion must still be exercised on trusted main")


if __name__ == "__main__":
    main()
