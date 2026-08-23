#!/usr/bin/env python3
"""Deterministic zero-effect validation for #221 full production composition prep."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import v03_real_runtime_full_composition as subject
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from provision_v03_real_runtime_fixture import FEATURE_ID, TARGET_REF

REPOSITORY = "DREAM-XIN/ai-sdlc"
HEAD = "a" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def _candidate_row(*, number=901, head=HEAD, draft=False, state="open", ref=TARGET_REF):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "head": {"ref": ref, "sha": head, "repo": {"full_name": REPOSITORY}},
        "base": {"ref": "main"},
    }


class CandidateHttp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        return self.status, self.payload


def validate_fixed_fixture_candidate_provider():
    http = CandidateHttp([_candidate_row()])
    provider = subject.FixedFixtureGitHubCandidateProvider(
        repository=REPOSITORY,
        token="bounded-read-token",
        http_get=http,
    )
    snapshot = provider.current_candidate(
        operation_id="op-fixture",
        repository=REPOSITORY,
        feature_id=FEATURE_ID,
        target_ref=TARGET_REF,
    )
    require(snapshot.candidate_pr_number == 901, "fixture candidate PR number drifted")
    require(snapshot.candidate_head_sha == HEAD, "fixture candidate exact head drifted")
    require(len(http.calls) == 1, "candidate provider did not perform exactly one fresh PR truth read")
    parsed = urlparse(http.calls[0][0])
    query = parse_qs(parsed.query)
    require(query.get("state") == ["open"], "candidate provider did not force open PR state")
    require(query.get("head") == [REPOSITORY.split("/", 1)[0].lower() + ":" + TARGET_REF], "candidate provider did not bind fixed head ref")
    require(query.get("base") == ["main"], "candidate provider did not bind trusted main base")
    require(http.calls[0][1]["Authorization"] == "Bearer bounded-read-token", "candidate read auth drifted")

    no_call = CandidateHttp([_candidate_row()])
    escaped = subject.FixedFixtureGitHubCandidateProvider(
        repository=REPOSITORY,
        token="bounded-read-token",
        http_get=no_call,
    )
    for kwargs in (
        {"repository": "DREAM-XIN/other", "feature_id": FEATURE_ID, "target_ref": TARGET_REF},
        {"repository": REPOSITORY, "feature_id": "F-OTHER", "target_ref": TARGET_REF},
        {"repository": REPOSITORY, "feature_id": FEATURE_ID, "target_ref": "other"},
    ):
        try:
            escaped.current_candidate(operation_id="op-fixture", **kwargs)
        except subject.V03RealRuntimeCompositionError:
            pass
        else:
            raise AssertionError("candidate provider accepted identity outside fixed fixture")
    require(no_call.calls == [], "escaped fixture identity reached GitHub read authority")

    wrong_repo = _candidate_row()
    wrong_repo["head"]["repo"]["full_name"] = "DREAM-XIN/other"
    missing_repo = _candidate_row()
    missing_repo["head"].pop("repo")
    missing_full_name = _candidate_row()
    missing_full_name["head"]["repo"] = {}
    for payload in (
        [],
        [_candidate_row(draft=True)],
        [_candidate_row(state="closed")],
        [_candidate_row(), _candidate_row(number=902, head="b" * 40)],
        [_candidate_row(ref="another-ref")],
        [_candidate_row(head="short")],
        [wrong_repo],
        [missing_repo],
        [missing_full_name],
    ):
        bad_http = CandidateHttp(payload)
        bad = subject.FixedFixtureGitHubCandidateProvider(
            repository=REPOSITORY,
            token="bounded-read-token",
            http_get=bad_http,
        )
        try:
            bad.current_candidate(
                operation_id="op-fixture",
                repository=REPOSITORY,
                feature_id=FEATURE_ID,
                target_ref=TARGET_REF,
            )
        except subject.V03RealRuntimeCompositionError:
            continue
        raise AssertionError("candidate provider accepted stale/ambiguous fixture PR truth")


class FakeTruth:
    def __init__(self, *, runtime, feature_gateway, candidate_provider):
        self.runtime = runtime
        self.feature_gateway = feature_gateway
        self.candidate_provider = candidate_provider

    def read_feature(self, *, operation_id):
        return "feature", {"operation_id": operation_id}


class FakeEventGateway:
    pass


class FakeSource:
    last = None

    def __init__(self, config, *, target_repository):
        self.config = config
        self.target_repository = target_repository
        self.load_calls = 0
        FakeSource.last = self

    def load_content(self, uri):
        self.load_calls += 1
        return b"{}"


class FakeTransport:
    last = None

    def __init__(self, config):
        self.config = config
        self.dispatch_calls = 0
        self.lookup_calls = 0
        FakeTransport.last = self

    def dispatch(self, **kwargs):
        self.dispatch_calls += 1
        raise AssertionError("composition construction must not dispatch Worker")

    def lookup(self, **kwargs):
        self.lookup_calls += 1
        raise AssertionError("composition construction must not perform Actions lookup")


class FakeDispatch:
    last = None

    def __init__(self, *, transport, workflows):
        self.transport = transport
        self.workflows = workflows
        FakeDispatch.last = self


class FakeCollector:
    def __init__(self, *, callback_coordinator, result_source, workflows, control_repository, clock):
        self.callback_coordinator = callback_coordinator
        self.result_source = result_source
        self.workflows = workflows
        self.control_repository = control_repository.lower()
        self.clock = clock


class FakeBundle:
    def __init__(self):
        self.runtime = object()
        self.callback_coordinator = SimpleNamespace(executor=SimpleNamespace(runtime=self.runtime))
        self.backends = {"operation.start": object(), "operation.cancel": object()}
        self.feature_id = FEATURE_ID


def validate_deferred_truth_is_fail_closed_and_one_time():
    original = subject.DurableDecisionFeatureTruthGateway
    subject.DurableDecisionFeatureTruthGateway = FakeTruth
    try:
        bridge = subject.DeferredFixtureFeatureTruthGateway()
        try:
            bridge.read_feature(operation_id="op-before-bind")
        except subject.V03RealRuntimeCompositionError:
            pass
        else:
            raise AssertionError("deferred FeatureTruth did not fail closed before exact runtime bind")
        delegate = FakeTruth(runtime=object(), feature_gateway=FakeEventGateway(), candidate_provider=object())
        bridge.bind(delegate)
        require(bridge.delegate is delegate, "deferred FeatureTruth lost exact delegate identity")
        require(bridge.read_feature(operation_id="op-bound")[1]["operation_id"] == "op-bound", "bound FeatureTruth delegation failed")
        try:
            bridge.bind(delegate)
        except subject.V03RealRuntimeCompositionError:
            pass
        else:
            raise AssertionError("deferred FeatureTruth accepted a second runtime binding")
    finally:
        subject.DurableDecisionFeatureTruthGateway = original


def validate_full_composition_wires_one_authority_graph_without_effects():
    originals = {
        "DurableDecisionFeatureTruthGateway": subject.DurableDecisionFeatureTruthGateway,
        "build_release_decision_event_gateway": subject.build_release_decision_event_gateway,
        "FirstAttemptDigestBoundGhAwResultSource": subject.FirstAttemptDigestBoundGhAwResultSource,
        "GitHubActionsVerticalGhAwTransport": subject.GitHubActionsVerticalGhAwTransport,
        "GhAwVerticalRoleDispatchGateway": subject.GhAwVerticalRoleDispatchGateway,
        "build_v03_vertical_write_ready_operator_bundle": subject.build_v03_vertical_write_ready_operator_bundle,
        "ProductionGhAwVerticalResultCollector": subject.ProductionGhAwVerticalResultCollector,
    }
    captures = {}
    fake_bundle = FakeBundle()
    fake_event = FakeEventGateway()

    def fake_event_builder(**kwargs):
        captures["event_kwargs"] = dict(kwargs)
        return fake_event

    def fake_bundle_builder(**kwargs):
        captures["bundle_kwargs"] = dict(kwargs)
        loader = kwargs["collector_content_loader"]
        require(getattr(loader, "__self__", None) is FakeSource.last, "bundle content loader does not use exact first-attempt source")
        require(kwargs["feature_event_gateway"] is fake_event, "bundle Persist/Event authority differs from production Event gateway")
        require(isinstance(kwargs["feature_gateway"], subject.DeferredFixtureFeatureTruthGateway), "bundle did not receive deferred FeatureTruth bridge")
        require(kwargs["dispatch_gateway"] is FakeDispatch.last, "bundle dispatch authority differs from production dispatch gateway")
        return fake_bundle

    subject.DurableDecisionFeatureTruthGateway = FakeTruth
    subject.build_release_decision_event_gateway = fake_event_builder
    subject.FirstAttemptDigestBoundGhAwResultSource = FakeSource
    subject.GitHubActionsVerticalGhAwTransport = FakeTransport
    subject.GhAwVerticalRoleDispatchGateway = FakeDispatch
    subject.build_v03_vertical_write_ready_operator_bundle = fake_bundle_builder
    subject.ProductionGhAwVerticalResultCollector = FakeCollector
    try:
        config = TrustedOperatorRuntimeConfig(
            target_repository=REPOSITORY,
            store_repository=REPOSITORY,
            installation_ref="main",
            store_checkout=Path("."),
            principal="release-controller",
            feature_bindings=(TrustedFeatureBinding(FEATURE_ID, TARGET_REF),),
        )
        workflows = GhAwVerticalWorkflowMap(
            default_branch="main",
            developer_workflow="developer.yml",
            reviewer_workflow="reviewer.yml",
            qa_workflow="qa.yml",
        )
        authority = SimpleNamespace(
            rollout_verifier=object(),
            resolution_policy_verifier=object(),
            decision_policy_verifier=object(),
        )
        composition = subject.build_v03_real_runtime_full_composition(
            config=config,
            adapter_id="v03-real-runtime-verifier",
            target_read_token="read-actions-token",
            actions_token="read-actions-token",
            event_write_token="bounded-app-write-token",
            control_repository=REPOSITORY,
            workflows=workflows,
            protection_verifier=object(),
            policy_authority=authority,
            trusted_context_digest="c" * 64,
            collector_namespace_policy="collector-policy",
            trusted_role_policy="role-policy",
            clock=lambda: "2026-08-18T00:00:00Z",
        )
        require(composition.bundle is fake_bundle, "composition replaced exact production bundle")
        require(composition.runtime is fake_bundle.runtime, "composition runtime differs from production bundle runtime")
        require(composition.result_source is FakeSource.last, "composition did not retain exact first-attempt source")
        require(composition.collector.result_source is composition.result_source, "collector source identity drifted")
        require(composition.collector.callback_coordinator is fake_bundle.callback_coordinator, "collector callback authority drifted")
        require(composition.feature_truth_gateway.delegate.runtime is fake_bundle.runtime, "FeatureTruth is not bound to unique Store runtime")
        require(composition.feature_truth_gateway.delegate.feature_gateway is fake_event, "FeatureTruth/Event gateway identity drifted")
        require(composition.dispatch_gateway is FakeDispatch.last, "dispatch gateway identity drifted")
        require(composition.actions_transport is FakeTransport.last, "Actions transport identity drifted")
        require(FakeTransport.last.dispatch_calls == 0, "composition construction dispatched external Worker")
        require(FakeTransport.last.lookup_calls == 0, "composition construction performed external Actions lookup")
        require("operation.resume" not in composition.bundle.backends, "server-only resume leaked into adapter backends")
        require(captures["event_kwargs"]["token"] == "bounded-app-write-token", "Feature Event write token boundary drifted")
        require(captures["event_kwargs"]["poll_attempts"] == 60, "real Persist polling bound was not enlarged")
        require(captures["event_kwargs"]["feature_refs"] == {FEATURE_ID: TARGET_REF}, "Event scope escaped fixed fixture")
        require(captures["bundle_kwargs"]["target_read_token"] == "read-actions-token", "read token boundary drifted")
        require(captures["bundle_kwargs"]["policy_verifier"] is authority.decision_policy_verifier, "Decision policy authority drifted")
        require(captures["bundle_kwargs"]["rollout_verifier"] is authority.rollout_verifier, "rollout policy authority drifted")
        require(captures["bundle_kwargs"]["resolution_policy_verifier"] is authority.resolution_policy_verifier, "resolution policy authority drifted")
    finally:
        for name, value in originals.items():
            setattr(subject, name, value)


def validate_credential_split_fence():
    config = TrustedOperatorRuntimeConfig(
        target_repository=REPOSITORY,
        store_repository=REPOSITORY,
        installation_ref="main",
        store_checkout=Path("."),
        principal="release-controller",
        feature_bindings=(TrustedFeatureBinding(FEATURE_ID, TARGET_REF),),
    )
    workflows = GhAwVerticalWorkflowMap("main", "developer.yml", "reviewer.yml", "qa.yml")
    authority = SimpleNamespace(
        rollout_verifier=object(),
        resolution_policy_verifier=object(),
        decision_policy_verifier=object(),
    )
    try:
        subject.build_v03_real_runtime_full_composition(
            config=config,
            adapter_id="adapter",
            target_read_token="read-token",
            actions_token="shared",
            event_write_token="shared",
            control_repository=REPOSITORY,
            workflows=workflows,
            protection_verifier=object(),
            policy_authority=authority,
            trusted_context_digest="d" * 64,
            collector_namespace_policy="collector",
            trusted_role_policy="role",
            clock=lambda: "now",
        )
    except ValueError:
        return
    raise AssertionError("composition accepted one token for Actions/read and canonical Event writes")


def main():
    validate_fixed_fixture_candidate_provider()
    validate_deferred_truth_is_fail_closed_and_one_time()
    validate_full_composition_wires_one_authority_graph_without_effects()
    validate_credential_split_fence()
    print("PASS: v0.3 full-runtime composition is fixed-fixture, single-runtime, split-authority, and zero-effect at construction")


if __name__ == "__main__":
    main()
