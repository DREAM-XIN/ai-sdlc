#!/usr/bin/env python3
"""Zero-effect contract validation for the live Persist ACK-loss two-process runner."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import v03_persist_ack_loss_live_runner as subject
from v03_real_runtime_fault_injection import LostAckCrashAfterPersistGateway

REPOSITORY = "dream-xin/ai-sdlc"
OPERATION_ID = "op-" + "a" * 32
EXTERNAL_KEY = "ext-" + "b" * 32
SEMANTIC_KEY = "d" * 64
EVENT_ID = "EVT-F-OPERATOR-V03-REAL-RUNTIME-FI-0001-CODE-REVIEW-PASS-DEADBEEF0001"
CALLBACK_ID = "gh-aw-callback-" + "c" * 24
TARGET_REF = "verification/v0.3-real-runtime-fixture-221"


def require(value, message):
    if not value:
        raise AssertionError(message)


class PersistDelegate:
    def __init__(self):
        self.persist_calls = 0
        self.lookup_calls = 0

    def persist_feature_event(self, *, event, target_ref):
        self.persist_calls += 1
        require(event["id"] == EVENT_ID and target_ref == TARGET_REF, "phase1 persisted wrong exact Event/ref")
        return {"event_id": EVENT_ID, "result_revision": 2}

    def lookup_feature_event(self, *, event_id, target_ref):
        self.lookup_calls += 1
        require(event_id == EVENT_ID and target_ref == TARGET_REF, "phase2 lookup escaped exact Event/ref")
        return {"event_id": EVENT_ID, "result_revision": 2}


class FakeBackend:
    def read_snapshot(self):
        return object()


class Phase1Collector:
    def __init__(self, bundle):
        self.bundle = bundle

    def handle(self, *, operation_id, external_dispatch_key):
        require(operation_id == OPERATION_ID and external_dispatch_key == EXTERNAL_KEY, "collector routing drifted")
        base = self.bundle.executor.base
        return base.persist_gateway.persist_feature_event(
            event={"id": EVENT_ID},
            target_ref=TARGET_REF,
        )


class Phase2Executor:
    def __init__(self, base):
        self.base = base
        self.calls = 0

    def _reconcile_persist(self, operation_id):
        require(operation_id == OPERATION_ID, "fresh reconcile used wrong Operation")
        self.calls += 1
        if self.calls == 1:
            receipt = self.base.persist_gateway.lookup_feature_event(
                event_id=EVENT_ID,
                target_ref=TARGET_REF,
            )
            require(receipt["result_revision"] == 2, "fresh reconcile lost exact result revision")
            return True
        return None


class Preflight:
    def __init__(self, *, phase2=False):
        self.execution = SimpleNamespace(
            repository=REPOSITORY,
            installation_commit_sha="1" * 40,
        )
        self.live_authority = SimpleNamespace(
            materialization_commit_sha="2" * 40,
            policy=SimpleNamespace(bundle_digest="3" * 64),
        )
        self.fixture_candidate = SimpleNamespace(
            candidate_pr_number=901,
            candidate_head_sha="4" * 40,
        )
        persist = PersistDelegate()
        base = SimpleNamespace(persist_gateway=persist)
        executor = Phase2Executor(base) if phase2 else SimpleNamespace(base=base)
        bundle = SimpleNamespace(
            executor=executor,
            runtime=SimpleNamespace(backend=FakeBackend()),
        )
        if not phase2:
            bundle.executor = SimpleNamespace(base=base)
        self.composition = SimpleNamespace(
            target_ref=TARGET_REF,
            feature_event_gateway=persist,
            bundle=bundle,
        )
        if not phase2:
            self.composition.collector = Phase1Collector(bundle)


class ExitSignal(BaseException):
    def __init__(self, code):
        self.code = code


def phase1_events():
    return [
        {"event_type": "worker.callback.recorded", "event_id": "e-cb", "payload": {"callback_id": CALLBACK_ID}},
        {"event_type": "worker.result.validated", "event_id": "e-valid", "payload": {"callback_id": CALLBACK_ID}},
        {"event_type": "feature.event.translated", "event_id": "e-trans", "payload": {"feature_event_id": EVENT_ID}},
        {"event_type": "persist.requested", "event_id": "e-req", "payload": {"feature_event_id": EVENT_ID}},
        {"event_type": "persist.linearized", "event_id": "e-lin", "payload": {"feature_event_id": EVENT_ID}},
    ]


def combined_events():
    return [
        {"event_type": "dispatch.launch.authorized", "operation_generation": 0, "event_id": "g0", "payload": {"external_dispatch_key": EXTERNAL_KEY}},
        {"event_type": "dispatch.launch.authorized", "operation_generation": 1, "event_id": "g1", "payload": {"external_dispatch_key": EXTERNAL_KEY}},
        {"event_type": "dispatch.launch.lookup-recorded", "operation_generation": 1, "event_id": "lookup", "payload": {"external_dispatch_key": EXTERNAL_KEY, "lookup_state": "LAUNCHED", "receipt_id": "101"}},
        {"event_type": "worker.callback.recorded", "operation_generation": 1, "event_id": "e-cb", "payload": {"callback_id": CALLBACK_ID}},
        {"event_type": "feature.event.translated", "operation_generation": 1, "event_id": "e-trans", "payload": {"feature_event_id": EVENT_ID}},
        {"event_type": "persist.confirmed", "operation_generation": 1, "event_id": "e-confirm", "payload": {"feature_event_id": EVENT_ID, "result_revision": 2}},
    ]


def validate_phase1_continues_existing_takeover_and_hard_exits():
    preflight = Preflight()
    original_operation = subject._operation_id
    original_predecessor = subject._require_lost_ack_takeover_state
    original_events = subject._scenario_events
    subject._operation_id = lambda _: OPERATION_ID
    subject._require_lost_ack_takeover_state = lambda *_: {
        "operation_id": OPERATION_ID,
        "generation": 1,
        "external_dispatch_key": EXTERNAL_KEY,
        "runtime_receipt_identity": "101",
        "expected_revision": 1,
    }
    subject._scenario_events = lambda *_: phase1_events()
    prepared = {
        "feature_event_id": EVENT_ID,
        "callback_id": CALLBACK_ID,
        "run_id": 101,
        "runtime_receipt_identity": "101",
        "expected_revision": 1,
        "candidate_head_sha": "4" * 40,
        "semantic_effect_key": SEMANTIC_KEY,
    }
    try:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "phase1.json"
            try:
                subject.run_phase1(
                    preflight=preflight,
                    evidence_path=path,
                    hard_exit=lambda code: (_ for _ in ()).throw(ExitSignal(code)),
                    prepare_exact_reviewer_event=lambda *args, **kwargs: prepared,
                )
            except ExitSignal as exc:
                require(exc.code == subject.PHASE1_EXIT, "phase1 used wrong hard-exit code")
            else:
                raise AssertionError("phase1 did not hard-exit after authoritative Persist receipt")
            evidence = json.loads(path.read_text(encoding="utf-8"))
            require(evidence["operation_id"] == OPERATION_ID, "phase1 did not retain predecessor Operation")
            require(evidence["operation_generation"] == 1, "phase1 did not remain on takeover generation")
            require(evidence["semantic_effect_key"] == SEMANTIC_KEY, "phase1 marker lost semantic effect identity")
            require(evidence["feature_event_id"] == EVENT_ID, "phase1 marker lost exact Event")
            require(evidence["persist_confirmed_count"] == 0, "phase1 falsely confirmed local Persist")
            wrapper = preflight.composition.bundle.executor.base.persist_gateway
            require(isinstance(wrapper, LostAckCrashAfterPersistGateway), "phase1 did not install reviewed Persist fault wrapper")
            require(wrapper.injected is True, "phase1 Persist wrapper did not inject")
            require(preflight.composition.feature_event_gateway.persist_calls == 1, "phase1 did not perform exactly one external Feature write")
    finally:
        subject._operation_id = original_operation
        subject._require_lost_ack_takeover_state = original_predecessor
        subject._scenario_events = original_events


def validate_phase2_is_lookup_only_idempotent_and_completes_lost_ack():
    preflight = Preflight(phase2=True)
    original_operation = subject._operation_id
    original_events = subject._scenario_events
    original_projection = subject.vertical_projection
    subject._operation_id = lambda _: OPERATION_ID
    subject._scenario_events = lambda *_: combined_events()
    subject.vertical_projection = lambda *_: {"generation": 1, "expected_feature_revision": 2}
    try:
        with TemporaryDirectory() as directory:
            phase1 = Path(directory) / "phase1.json"
            final = Path(directory) / "final.json"
            phase1.write_text(
                json.dumps(
                    {
                        "schema_version": "ai-sdlc.v03-live-persist-ack-loss-phase1/v1",
                        "installation_commit_sha": preflight.execution.installation_commit_sha,
                        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
                        "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
                        "operation_id": OPERATION_ID,
                        "operation_generation": 1,
                        "semantic_effect_key": SEMANTIC_KEY,
                        "external_dispatch_key": EXTERNAL_KEY,
                        "runtime_receipt_identity": "101",
                        "reviewer_run_id": 101,
                        "callback_id": CALLBACK_ID,
                        "feature_event_id": EVENT_ID,
                        "target_ref": TARGET_REF,
                        "expected_revision": 1,
                        "expected_result_revision": 2,
                    }
                ),
                encoding="utf-8",
            )
            evidence = subject.run_phase2(
                preflight=preflight,
                phase1_path=phase1,
                final_path=final,
            )
            fence = preflight.composition.bundle.executor.base.persist_gateway
            require(isinstance(fence, subject.LookupOnlyPersistRecoveryGateway), "phase2 did not install lookup-only recovery fence")
            require(fence.lookup_calls == 1 and fence.persist_calls == 0, "phase2 performed anything other than one exact lookup")
            require(preflight.composition.feature_event_gateway.lookup_calls == 1, "phase2 did not delegate exact lookup to production gateway")
            require(preflight.composition.feature_event_gateway.persist_calls == 0, "phase2 issued a second Feature Event write")
            require(evidence["status"] == "PASS", "phase2 did not produce Persist scenario PASS")
            require(
                evidence["completed_issue_221_scenarios"] == [
                    "lost-ack-crash-takeover",
                    "persist-ack-loss-recovery",
                ],
                "combined evidence did not close the exact two chained Issue #221 scenarios",
            )
            require(evidence["lost_ack_crash_takeover_status"] == "PASS", "exact result/Persist did not complete lost-ACK")
            require(evidence["persist_ack_loss_recovery_status"] == "PASS", "Persist ACK-loss scenario did not complete")
            require(evidence["external_runtime_execution_count"] == 1, "combined evidence did not prove one external runtime execution")
            require(evidence["feature_persist_count"] == 1, "combined evidence did not prove exactly one Feature Persist")
            require(evidence["duplicate_external_effect_count"] == 0, "combined evidence lost zero duplicate external effect")
            require(evidence["duplicate_feature_write_count"] == 0, "phase2 evidence lost zero duplicate Feature write")
            require(evidence["overall_issue_221_pass"] is False, "two scenarios overclaimed Issue #221 PASS")
            require(final.is_file(), "phase2 did not retain final evidence")
    finally:
        subject._operation_id = original_operation
        subject._scenario_events = original_events
        subject.vertical_projection = original_projection


def main():
    validate_phase1_continues_existing_takeover_and_hard_exits()
    validate_phase2_is_lookup_only_idempotent_and_completes_lost_ack()
    print("PASS: Persist ACK-loss fresh recovery also completes the chained lost-ACK end-to-end result/Persist proof")


if __name__ == "__main__":
    main()
