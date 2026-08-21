#!/usr/bin/env python3
"""Deterministic zero-effect validation for #221 full trusted pre-launch assembly."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import v03_real_runtime_full_preflight as subject
from operator_v03_reviewer_worker_readiness import ReviewerWorkerSelection
from v03_real_runtime_live_authority import TrustedMainExecution, V03LiveAuthority

REPOSITORY = "dream-xin/ai-sdlc"
INSTALLATION = "1" * 40
MATERIALIZATION = "2" * 40
STATE_SHA = "3" * 40
CANDIDATE_HEAD = "4" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


class FakeProtectionVerifier:
    def verify(self, repository, state_ref):
        raise AssertionError("preflight construction must not invoke live protection verifier")


class FakeCandidateProvider:
    def __init__(self, *, number=901, head=CANDIDATE_HEAD):
        self.number = number
        self.head = head
        self.calls = []

    def current_candidate(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            candidate_pr_number=self.number,
            candidate_head_sha=self.head,
        )


class FakeComposition:
    def __init__(self, provider):
        self.candidate_provider = provider
        self.runtime = object()
        self.bundle = SimpleNamespace(runtime=self.runtime)


def execution():
    return TrustedMainExecution(
        repository=REPOSITORY,
        installation_commit_sha=INSTALLATION,
        state_ref="refs/heads/ai-sdlc-operator-state",
    )


def reviewer(*, workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml", present=True):
    return ReviewerWorkerSelection(
        worker_id=(
            "code-review-reviewer-claude"
            if "claude" in workflow
            else "code-review-reviewer-copilot"
        ),
        role="reviewer",
        stage="code-review",
        profile="claude" if "claude" in workflow else "copilot",
        workflow_file=workflow,
        credential_env="ANTHROPIC_API_KEY" if "claude" in workflow else "COPILOT_GITHUB_TOKEN",
        credential_present=present,
        selection_policy="v03-frozen-reviewer-provider-order/v1",
    )


def policy(*, installation=INSTALLATION):
    return SimpleNamespace(
        installation_commit_sha=installation,
        materialization_commit_sha=MATERIALIZATION,
        bundle_digest="b" * 64,
        rollout_verifier=object(),
        resolution_policy_verifier=object(),
        decision_policy_verifier=object(),
    )


def live(*, exec_obj=None, policy_obj=None):
    exec_obj = exec_obj or execution()
    return V03LiveAuthority(
        execution=exec_obj,
        materialization_commit_sha=MATERIALIZATION,
        protected_state_ref_sha=STATE_SHA,
        protection_receipt=SimpleNamespace(status="PROTECTED"),
        policy=policy_obj or policy(),
    )


def validate_positive_preflight_is_fixed_and_zero_effect():
    original = subject.build_v03_real_runtime_full_composition
    captures = {}
    provider = FakeCandidateProvider()

    def fake_builder(**kwargs):
        captures.update(kwargs)
        return FakeComposition(provider)

    subject.build_v03_real_runtime_full_composition = fake_builder
    try:
        result = subject.build_v03_full_runtime_preflight(
            execution=execution(),
            live_authority=live(),
            reviewer_selection=reviewer(),
            protection_verifier=FakeProtectionVerifier(),
            adapter_id="v03-real-runtime-release-verifier",
            target_read_token="bounded-read-token",
            actions_token="bounded-actions-token",
            event_write_token="bounded-event-write-token",
            clock=lambda: "2026-08-18T00:00:00Z",
        )
    finally:
        subject.build_v03_real_runtime_full_composition = original

    require(result.execution == execution(), "preflight lost exact trusted-main execution")
    require(result.live_authority.execution == execution(), "preflight live authority binding drifted")
    require(result.reviewer_selection.workflow_file == "ai-sdlc-gh-aw-reviewer-claude.lock.yml", "reviewer selection drifted")
    require(result.workflows.default_branch == "main", "workflow map escaped trusted main")
    require(result.workflows.developer_workflow == subject.DEVELOPER_WORKFLOW, "developer workflow drifted")
    require(result.workflows.reviewer_workflow == "ai-sdlc-gh-aw-reviewer-claude.lock.yml", "reviewer workflow drifted")
    require(result.workflows.qa_workflow == subject.QA_WORKFLOW, "QA workflow drifted")
    require(len(result.trusted_context_digest) == 64, "trusted preflight digest is not canonical SHA-256")
    require(result.fixture_candidate.candidate_pr_number == 901, "fixture candidate PR drifted")
    require(result.fixture_candidate.candidate_head_sha == CANDIDATE_HEAD, "fixture candidate head drifted")
    require(len(provider.calls) == 1, "fixture candidate was not fresh-read exactly once")
    require(provider.calls[0]["feature_id"] == subject.FIXTURE_FEATURE_ID, "fixture Feature scope drifted")
    require(provider.calls[0]["target_ref"] == subject.FIXTURE_TARGET_REF, "fixture ref scope drifted")
    require(provider.calls[0]["repository"] == REPOSITORY, "fixture repository scope drifted")

    config = captures["config"]
    require(config.target_repository == REPOSITORY, "target repository drifted")
    require(config.store_repository == REPOSITORY, "Store repository drifted")
    require(config.installation_ref == "main", "Store installation ref drifted")
    require(config.feature_ids == frozenset({subject.FIXTURE_FEATURE_ID}), "runtime escaped fixed fixture Feature")
    require(config.feature_ref(subject.FIXTURE_FEATURE_ID) == subject.FIXTURE_TARGET_REF, "runtime escaped fixed fixture ref")
    require(captures["control_repository"] == REPOSITORY, "control repository differs from target/Store")
    require(captures["workflows"] == result.workflows, "composition did not receive exact reviewed workflow map")
    require(captures["policy_authority"] is result.live_authority.policy, "composition did not receive exact live policy authority")
    require(captures["protection_verifier"].__class__ is FakeProtectionVerifier, "composition did not receive exact protection verifier")
    require(captures["target_read_token"] == "bounded-read-token", "read credential boundary drifted")
    require(captures["actions_token"] == "bounded-actions-token", "Actions credential boundary drifted")
    require(captures["event_write_token"] == "bounded-event-write-token", "Event-write credential boundary drifted")
    require(captures["trusted_context_digest"] == result.trusted_context_digest, "composition trusted context digest drifted")


def _expect_rejected(*, exec_obj=None, live_obj=None, reviewer_obj=None, protection=None, actions="actions", event="event"):
    calls = []
    original = subject.build_v03_real_runtime_full_composition
    subject.build_v03_real_runtime_full_composition = lambda **kwargs: calls.append(kwargs)
    try:
        try:
            subject.build_v03_full_runtime_preflight(
                execution=exec_obj or execution(),
                live_authority=live_obj or live(exec_obj=exec_obj or execution()),
                reviewer_selection=reviewer_obj or reviewer(),
                protection_verifier=protection if protection is not None else FakeProtectionVerifier(),
                adapter_id="adapter",
                target_read_token="read",
                actions_token=actions,
                event_write_token=event,
                clock=lambda: "now",
            )
        except (ValueError, subject.V03FullRuntimePreflightError):
            require(calls == [], "rejected preflight reached production composition builder")
            return
        raise AssertionError("invalid preflight authority was accepted")
    finally:
        subject.build_v03_real_runtime_full_composition = original


def validate_negative_authority_fences():
    other_exec = TrustedMainExecution(
        repository=REPOSITORY,
        installation_commit_sha="9" * 40,
        state_ref="refs/heads/ai-sdlc-operator-state",
    )
    _expect_rejected(exec_obj=execution(), live_obj=live(exec_obj=other_exec))
    _expect_rejected(live_obj=live(policy_obj=policy(installation="8" * 40)))
    _expect_rejected(reviewer_obj=reviewer(workflow="unreviewed-reviewer.yml"))
    _expect_rejected(reviewer_obj=reviewer(present=False))
    _expect_rejected(protection=object())
    _expect_rejected(actions="shared", event="shared")


def validate_fixture_candidate_fails_closed_before_launch():
    original = subject.build_v03_real_runtime_full_composition
    for number, head in ((0, CANDIDATE_HEAD), (901, "short")):
        provider = FakeCandidateProvider(number=number, head=head)
        subject.build_v03_real_runtime_full_composition = lambda **kwargs: FakeComposition(provider)
        try:
            try:
                subject.build_v03_full_runtime_preflight(
                    execution=execution(),
                    live_authority=live(),
                    reviewer_selection=reviewer(),
                    protection_verifier=FakeProtectionVerifier(),
                    adapter_id="adapter",
                    target_read_token="read",
                    actions_token="actions",
                    event_write_token="event",
                    clock=lambda: "now",
                )
            except subject.V03FullRuntimePreflightError:
                continue
            raise AssertionError("malformed fixed fixture candidate was accepted")
        finally:
            subject.build_v03_real_runtime_full_composition = original


def main():
    validate_positive_preflight_is_fixed_and_zero_effect()
    validate_negative_authority_fences()
    validate_fixture_candidate_fails_closed_before_launch()
    print("PASS: v0.3 full-runtime preflight binds exact main/policy/reviewer/fixture/composition without launch")


if __name__ == "__main__":
    main()
