#!/usr/bin/env python3
"""Adversarial validation for the Operation-bound trusted gh-aw collector."""
from __future__ import annotations

from dataclasses import replace

from operator_store import plan_authorize_launch, plan_dispatch_claim, plan_launch_lookup, plan_operation_start
from operator_store_model import StoreSnapshot, apply_plan_to_snapshot
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE, VerticalInvariantError, validate_collected_outputs
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_collector import (
    GhAwVerticalResultCollector,
    MaterializedGhAwOutput,
    TrustedGhAwResolvedResult,
    TrustedGhAwRun,
)
from operator_vertical_store import plan_vertical_semantic_reservation

NOW = "2026-08-14T00:00:00Z"
REPO = "dream-xin/ai-sdlc"
FEATURE = "F-COLLECTOR-TEST"
HEAD = "a" * 40


def _apply(snapshot, plan, sha):
    return apply_plan_to_snapshot(snapshot, plan, new_ref_sha=sha)


def _state(*, lookup_state="LAUNCHED", receipt_id="run-101"):
    snapshot = StoreSnapshot(ref_sha="s0")
    start = plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=7,
        idempotency_key="idem-collector",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        operation_profile=VERTICAL_PROFILE,
    )
    snapshot = _apply(snapshot, start, "s1")
    operation_id = start.result["operation_id"]
    reservation = plan_vertical_semantic_reservation(
        snapshot,
        operation_id=operation_id,
        generation=0,
        target_repository=REPO,
        feature_id=FEATURE,
        expected_revision=7,
        current_stage="code-review",
        task_identity="REVIEW-1",
        role="reviewer",
        candidate_head_sha=HEAD,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, reservation, "s2")
    effect_key = reservation.result["semantic_effect_key"]
    claim = plan_dispatch_claim(
        snapshot,
        operation_id=operation_id,
        generation=0,
        effect_key=effect_key,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, claim, "s3")
    external_key = claim.result["external_dispatch_key"]
    launch = plan_authorize_launch(
        snapshot,
        operation_id=operation_id,
        generation=0,
        claim_id=claim.result["claim_id"],
        dispatch_id="vertical-review-1",
        occurred_at=NOW,
        trusted_context_digest="trusted",
        verified_expected_revision=7,
        verified_stage="code-review",
        verified_candidate_head_sha=HEAD,
    )
    snapshot = _apply(snapshot, launch, "s4")
    lookup = plan_launch_lookup(
        snapshot,
        operation_id=operation_id,
        generation=0,
        external_dispatch_key_value=external_key,
        lookup_state=lookup_state,
        receipt_id=receipt_id,
        occurred_at=NOW,
        trusted_context_digest="trusted",
    )
    snapshot = _apply(snapshot, lookup, "s5")
    return snapshot, operation_id, external_key


class Backend:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def read_snapshot(self):
        return self.snapshot


class Runtime:
    def __init__(self, snapshot):
        self.backend = Backend(snapshot)


class Config:
    target_ref = "feature/test"


class Executor:
    def __init__(self, snapshot):
        self.runtime = Runtime(snapshot)
        self.config = Config()


class Coordinator:
    def __init__(self, snapshot, blobs):
        self.executor = Executor(snapshot)
        self.blobs = dict(blobs)
        self.calls = []

    def content_loader(self, uri):
        return self.blobs[uri]

    def handle(self, **kwargs):
        self.calls.append(kwargs)
        return {"status": "accepted", "callback_id": kwargs["callback_id"]}


class Source:
    def __init__(self, resolved):
        self.resolved = resolved
        self.calls = []

    def resolve(self, *, external_dispatch_key, expected_receipt_identity):
        self.calls.append((external_dispatch_key, expected_receipt_identity))
        return self.resolved


def _workflows():
    return GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker-codex.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )


def _resolved(external_key, *, receipt="run-101", head=HEAD, workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml"):
    run = TrustedGhAwRun(
        run_id=101,
        run_url="https://github.com/DREAM-XIN/ai-sdlc/actions/runs/101",
        receipt_identity=receipt,
        control_repository=REPO,
        workflow_file=workflow,
        workflow_ref="main",
        event="workflow_dispatch",
        status="completed",
        conclusion="success",
        display_title=f"AI-SDLC gh-aw {external_key}",
        external_dispatch_key=external_key,
        role="reviewer",
        task_id="REVIEW-1",
        worker_identity="worker:claude-reviewer",
        collector_identity="collector:gh-aw",
        candidate_pr_number=42,
        candidate_head_sha=head,
    )
    payload = {
        "verdict": "PASS",
        "summary": "Independent exact-head review passed.",
        "findings": [],
        "outputs": [{"label": "review-evidence", "kind": "evidence"}],
    }
    output = MaterializedGhAwOutput(
        label="review-evidence",
        kind="evidence",
        media_type="application/json",
        trusted_uri=f"docs/features/{FEATURE}/worker-runs/vertical-review-1/review-evidence.json",
    )
    return TrustedGhAwResolvedResult(run=run, role_payload=payload, outputs=(output,))


def _collector(snapshot, external_key, resolved):
    uri = resolved.outputs[0].trusted_uri
    coordinator = Coordinator(snapshot, {uri: b'{"verdict":"PASS"}\n'})
    source = Source(resolved)
    collector = GhAwVerticalResultCollector(
        callback_coordinator=coordinator,
        result_source=source,
        workflows=_workflows(),
        control_repository=REPO,
        clock=lambda: NOW,
    )
    return collector, coordinator, source


def validate_happy_path():
    snapshot, operation_id, external_key = _state()
    resolved = _resolved(external_key)
    collector, coordinator, source = _collector(snapshot, external_key, resolved)
    result = collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
    assert result["status"] == "accepted"
    assert source.calls == [(external_key, "run-101")]
    call = coordinator.calls[0]
    context = call["context"]
    assert context.operation_id == operation_id
    assert context.runtime_receipt_identity == "run-101"
    assert context.candidate_head_sha == HEAD
    assert context.candidate_pr_number == 42
    assert context.worker_identity == "worker:claude-reviewer"
    assert call["receipts"][0]["external_dispatch_key"] == external_key
    assert call["callback_id"].startswith("gh-aw-callback-")


def validate_forged_receipt_rejected():
    snapshot, operation_id, external_key = _state()
    collector, _, _ = _collector(snapshot, external_key, _resolved(external_key, receipt="run-999"))
    try:
        collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
        raise AssertionError("forged runtime receipt unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "STALE_REVISION"


def validate_wrong_candidate_rejected():
    snapshot, operation_id, external_key = _state()
    collector, _, _ = _collector(snapshot, external_key, _resolved(external_key, head="b" * 40))
    try:
        collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
        raise AssertionError("mismatched candidate unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "STALE_REVISION"


def validate_wrong_workflow_rejected():
    snapshot, operation_id, external_key = _state()
    collector, _, _ = _collector(snapshot, external_key, _resolved(external_key, workflow="ai-sdlc-gh-aw-worker-codex.lock.yml"))
    try:
        collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
        raise AssertionError("wrong role workflow unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "POLICY_DENIED"


def validate_unknown_receipt_rejected():
    snapshot, operation_id, external_key = _state(lookup_state="UNKNOWN", receipt_id=None)
    collector, _, _ = _collector(snapshot, external_key, _resolved(external_key))
    try:
        collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
        raise AssertionError("UNKNOWN launch state unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "BLOCKED"


def validate_role_schema_rejected():
    snapshot, operation_id, external_key = _state()
    resolved = _resolved(external_key)
    resolved = replace(resolved, role_payload={"verdict": "PASS", "summary": "missing findings/outputs"})
    collector, _, _ = _collector(snapshot, external_key, resolved)
    try:
        collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
        raise AssertionError("invalid role payload unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "INVALID_REQUEST"


def validate_digest_tamper_rejected_by_existing_callback_contract():
    snapshot, operation_id, external_key = _state()
    resolved = _resolved(external_key)
    collector, coordinator, _ = _collector(snapshot, external_key, resolved)
    collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
    call = coordinator.calls[0]
    context = call["context"]
    receipt = call["receipts"][0]
    feature = FeatureSnapshot(
        repository=REPO,
        feature_id=FEATURE,
        target_ref="feature/test",
        revision=7,
        manifest_digest="m" * 64,
        current_stage="code-review",
        stages={},
        gates={},
        remediation_tasks=(),
        artifacts=(),
        candidate_pr_number=42,
        candidate_head_sha=HEAD,
    )
    try:
        validate_collected_outputs(
            context=context,
            feature=feature,
            worker_payload=call["worker_payload"],
            receipts=[receipt],
            content_loader=lambda _uri: b"tampered-bytes",
        )
        raise AssertionError("tampered collected bytes unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "BLOCKED"


def validate_callback_identity_is_deterministic():
    snapshot, operation_id, external_key = _state()
    resolved = _resolved(external_key)
    collector, coordinator, _ = _collector(snapshot, external_key, resolved)
    first = collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
    second = collector.handle(operation_id=operation_id, external_dispatch_key=external_key)
    assert first["callback_id"] == second["callback_id"]
    assert coordinator.calls[0]["callback_id"] == coordinator.calls[1]["callback_id"]


def main():
    validate_happy_path()
    validate_forged_receipt_rejected()
    validate_wrong_candidate_rejected()
    validate_wrong_workflow_rejected()
    validate_unknown_receipt_rejected()
    validate_role_schema_rejected()
    validate_digest_tamper_rejected_by_existing_callback_contract()
    validate_callback_identity_is_deterministic()
    print("Operator vertical gh-aw trusted result collector validation passed")


if __name__ == "__main__":
    main()
