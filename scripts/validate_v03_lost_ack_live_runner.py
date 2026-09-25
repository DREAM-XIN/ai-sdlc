#!/usr/bin/env python3
"""Zero-effect runner-contract validation for the two-process lost-ACK live scenario."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import v03_lost_ack_live_runner as subject
from v03_real_runtime_fault_injection import LostAckCrashAfterLaunchDispatchGateway
from v03_real_runtime_lost_ack_orchestration import LostAckDispatchBinding

REPOSITORY = "dream-xin/ai-sdlc"
EXTERNAL_KEY = "ext-" + "a" * 32
OPERATION_ID = "op-" + "b" * 32
INSTALLATION_SHA = "1" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def binding():
    return LostAckDispatchBinding(
        repository=REPOSITORY,
        feature_id="F-OPERATOR-V03-REAL-RUNTIME-FI-0013",
        target_ref="verification/v0.3-real-runtime-fixture-221-r13",
        feature_revision=11,
        current_stage="implementation",
        candidate_pr_number=901,
        candidate_head_sha="c" * 40,
        role="developer",
        task_id="task-1",
        task_identity="task-1",
        semantic_effect_key="sem-" + "d" * 32,
        external_dispatch_key=EXTERNAL_KEY,
        operation_id=OPERATION_ID,
        idempotency_key=subject.IDEMPOTENCY_KEY,
    )


class DelegateGateway:
    def __init__(self):
        self.launch_count = 0

    def launch(self, *, dispatch):
        self.launch_count += 1
        require(dispatch["external_dispatch_key"] == EXTERNAL_KEY, "phase1 launched wrong external key")
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-101"}

    def lookup(self, *, external_dispatch_key):
        require(external_dispatch_key == EXTERNAL_KEY, "phase1 lookup key drifted")
        return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}


class StartBackend:
    def __init__(self, base):
        self.base = base

    def invoke(self, request, trusted_context):
        require(request["idempotency_key"] == subject.IDEMPOTENCY_KEY, "phase1 idempotency drifted")
        return self.base.dispatch_gateway.launch(
            dispatch={"external_dispatch_key": EXTERNAL_KEY}
        )


class Bundle:
    def __init__(self):
        self.runtime = SimpleNamespace(backend=object())
        self.raw_dispatch_gateway = DelegateGateway()
        self.one_shot_dispatch_gateway = subject.StoreBackedOneShotExternalCreateGateway(
            runtime=self.runtime,
            delegate=self.raw_dispatch_gateway,
            trusted_context_digest="trusted-context",
            effect_lineage_required=True,
        )
        # The focused runner test does not re-test the one-shot implementation;
        # keep the exact topology while making its calls zero-effect/in-memory.
        self.one_shot_dispatch_gateway.launch = self.raw_dispatch_gateway.launch
        self.one_shot_dispatch_gateway.lookup = self.raw_dispatch_gateway.lookup
        base = SimpleNamespace(
            dispatch_gateway=self.one_shot_dispatch_gateway,
            config=SimpleNamespace(trusted_context_digest="trusted-context"),
        )
        self.executor = SimpleNamespace(base=base)
        self.backends = {"operation.start": StartBackend(base)}
        provider = SimpleNamespace(for_request=lambda request: {"trusted": request})
        self.write_bundle = SimpleNamespace(read_bundle=SimpleNamespace(trusted_context_provider=provider))


class Preflight:
    def __init__(self):
        self.execution = SimpleNamespace(
            repository=REPOSITORY,
            installation_commit_sha=INSTALLATION_SHA,
        )
        self.live_authority = SimpleNamespace(
            materialization_commit_sha="2" * 40,
            protected_state_ref_sha="4" * 40,
            policy=SimpleNamespace(bundle_digest="3" * 64),
        )
        self.fixture_candidate = SimpleNamespace(
            candidate_pr_number=901,
            candidate_head_sha="c" * 40,
        )
        bundle = Bundle()
        self.composition = SimpleNamespace(
            bundle=bundle,
            dispatch_gateway=bundle.raw_dispatch_gateway,
        )


class ExitSignal(BaseException):
    def __init__(self, code):
        self.code = code


class FakeConfiguredFeatureEventGateway:
    def __init__(self, manifest):
        self.manifest = manifest
        self.calls = []

    def read_feature(self, *, feature_id):
        self.calls.append(feature_id)
        return self.manifest


def validate_binding_uses_configured_feature_authority_only():
    expected = binding()
    gateway = FakeConfiguredFeatureEventGateway({"revision": expected.feature_revision})
    captured = {}
    original = subject.derive_lost_ack_dispatch_binding
    subject.derive_lost_ack_dispatch_binding = lambda **kwargs: captured.update(kwargs) or expected
    preflight = SimpleNamespace(
        execution=SimpleNamespace(
            repository=expected.repository,
            installation_commit_sha=INSTALLATION_SHA,
        ),
        fixture_candidate=SimpleNamespace(
            candidate_pr_number=expected.candidate_pr_number,
            candidate_head_sha=expected.candidate_head_sha,
        ),
        composition=SimpleNamespace(
            feature_event_gateway=gateway,
            feature_id=expected.feature_id,
            target_ref=expected.target_ref,
            bundle=SimpleNamespace(runtime=SimpleNamespace(clock=lambda: "2026-08-18T00:00:00Z")),
        ),
    )
    try:
        actual = subject._binding(preflight)
        require(actual == expected, "configured Manifest read changed lost-ACK binding")
        require(gateway.calls == [expected.feature_id], "lost-ACK Manifest read escaped configured Feature authority")
        require(captured["repository"] == expected.repository, "derived binding lost trusted repository")
        require(captured["target_ref"] == expected.target_ref, "derived binding lost trusted target ref")
        require(
            captured["idempotency_key"] == f"{subject.BASE_IDEMPOTENCY_KEY}-{INSTALLATION_SHA}",
            "derived binding was not scoped to exact trusted-main installation",
        )
    finally:
        subject.derive_lost_ack_dispatch_binding = original


def validate_phase1_requires_exact_one_shot_production_topology():
    preflight = Preflight()
    base, gateway = subject._production_one_shot_dispatch(preflight)
    require(gateway is preflight.composition.bundle.one_shot_dispatch_gateway, "lost-ACK runner did not retain production one-shot fence")
    require(gateway.delegate is preflight.composition.dispatch_gateway, "one-shot fence lost exact raw gh-aw delegate")
    base.dispatch_gateway = preflight.composition.dispatch_gateway
    try:
        subject._production_one_shot_dispatch(preflight)
    except subject.V03LostAckLiveError:
        pass
    else:
        raise AssertionError("lost-ACK runner accepted raw dispatch bypass around one-shot fence")


def validate_phase1_uses_real_fault_wrapper_and_hard_exit():
    preflight = Preflight()
    b = binding()
    original_binding = subject._binding
    original_events = subject._scenario_events
    calls = {"events": 0}

    def events(_preflight, _binding):
        calls["events"] += 1
        if calls["events"] == 1:
            return []
        return [
            {
                "event_type": "dispatch.launch.authorized",
                "operation_generation": 0,
                "event_id": "evt-auth-g0",
                "payload": {"external_dispatch_key": EXTERNAL_KEY},
            }
        ]

    subject._binding = lambda _: b
    subject._scenario_events = events
    try:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "phase1.json"
            try:
                subject.run_phase1(
                    preflight=preflight,
                    evidence_path=path,
                    hard_exit=lambda code: (_ for _ in ()).throw(ExitSignal(code)),
                )
            except ExitSignal as exc:
                require(exc.code == subject.PHASE1_EXIT, "phase1 used wrong hard-exit code")
            else:
                raise AssertionError("phase1 did not hard-exit after injected launch crash")
            evidence = json.loads(path.read_text(encoding="utf-8"))
            require(evidence["fault_code"] == "FI_CRASH_AFTER_LAUNCH_BEFORE_LOCAL_ACK", "phase1 marker lost fault code")
            require(evidence["g0_lookup_count"] == 0, "phase1 marker falsely recorded local lookup")
            require(evidence["binding"] == subject.asdict(b), "phase1 marker lost exact dispatch binding")
            require(isinstance(preflight.composition.bundle.executor.base.dispatch_gateway, LostAckCrashAfterLaunchDispatchGateway), "phase1 did not install verification-only lost-ACK wrapper")
            require(preflight.composition.dispatch_gateway.launch_count == 1, "phase1 did not create exactly one external launch")
    finally:
        subject._binding = original_binding
        subject._scenario_events = original_events


def validate_phase2_is_fresh_binding_checked_and_remains_pending_until_result_persist():
    preflight = Preflight()
    b = binding()
    original_binding = subject._binding
    original_events = subject._scenario_events
    original_takeover = subject.run_phase2_takeover_and_adopt
    takeover_calls = []
    subject._binding = lambda _: b
    subject.run_phase2_takeover_and_adopt = lambda **kwargs: takeover_calls.append(kwargs) or {
        "operation_id": OPERATION_ID,
        "generation": 1,
        "external_dispatch_key": EXTERNAL_KEY,
        "runtime_receipt_identity": "run-101",
    }
    subject._scenario_events = lambda *_: [
        {"event_type": "dispatch.launch.authorized"},
        {"event_type": "dispatch.launch.authorized"},
        {"event_type": "dispatch.launch.lookup-recorded"},
    ]
    try:
        with TemporaryDirectory() as directory:
            phase1 = Path(directory) / "phase1.json"
            final = Path(directory) / "final.json"
            phase1.write_text(
                json.dumps(
                    {
                        "schema_version": "ai-sdlc.v03-live-lost-ack-phase1/v1",
                        "installation_commit_sha": preflight.execution.installation_commit_sha,
                        "materialization_commit_sha": preflight.live_authority.materialization_commit_sha,
                        "policy_bundle_digest": preflight.live_authority.policy.bundle_digest,
                        "fixture_candidate_head_sha": preflight.fixture_candidate.candidate_head_sha,
                        "binding": subject.asdict(b),
                    }
                ),
                encoding="utf-8",
            )
            evidence = subject.run_phase2(
                preflight=preflight,
                phase1_path=phase1,
                final_path=final,
            )
            require(len(takeover_calls) == 1, "phase2 did not invoke trusted takeover/adoption exactly once")
            require(takeover_calls[0]["binding"] == b, "phase2 takeover used different dispatch binding")
            require(evidence["phase_status"] == "PASS", "takeover phase itself did not PASS")
            require(evidence["status"] == "PENDING", "takeover-only evidence overclaimed full lost-ACK PASS")
            require(
                evidence["remaining_release_proof"] == [
                    "exact first-attempt Worker result correlation",
                    "Feature Persist at most once",
                ],
                "takeover-only evidence lost exact remaining release proof",
            )
            require(evidence["overall_issue_221_pass"] is False, "takeover phase overclaimed overall #221 PASS")
            require(evidence["duplicate_external_effect_count"] == 0, "lost-ACK phase evidence did not retain zero-duplicate claim")
            require(final.is_file(), "phase2 did not persist takeover-phase evidence")

            changed = json.loads(phase1.read_text(encoding="utf-8"))
            changed["fixture_candidate_head_sha"] = "9" * 40
            phase1.write_text(json.dumps(changed), encoding="utf-8")
            takeover_calls.clear()
            try:
                subject.run_phase2(preflight=preflight, phase1_path=phase1, final_path=final)
            except subject.V03LostAckLiveError:
                require(takeover_calls == [], "phase2 reached takeover after candidate drift")
            else:
                raise AssertionError("phase2 accepted fixture candidate drift after crash")
    finally:
        subject._binding = original_binding
        subject._scenario_events = original_events
        subject.run_phase2_takeover_and_adopt = original_takeover


def main():
    validate_binding_uses_configured_feature_authority_only()
    validate_phase1_requires_exact_one_shot_production_topology()
    validate_phase1_uses_real_fault_wrapper_and_hard_exit()
    validate_phase2_is_fresh_binding_checked_and_remains_pending_until_result_persist()
    print("PASS: lost-ACK runner proves hard-crash + same-key takeover while full scenario remains PENDING until exact result/Persist")


if __name__ == "__main__":
    main()
