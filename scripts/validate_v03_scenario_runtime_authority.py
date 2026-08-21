#!/usr/bin/env python3
"""Deterministic zero-effect/adversarial validation for Issue #312 scenario authority."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import v03_scenario_runtime_composition as composition_subject
import v03_scenario_runtime_preflight as preflight_subject
from operator_v03_reviewer_worker_readiness import ReviewerWorkerSelection
from provision_v03_real_runtime_fixture import FEATURE_ID as ORIGINAL_FEATURE_ID, TARGET_REF as ORIGINAL_TARGET_REF
from v03_real_runtime_live_authority import TrustedMainExecution, V03LiveAuthority
from v03_scenario_fixture_pool import EXPECTED_SCENARIOS, SLOTS

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
        raise AssertionError("scenario preflight construction must not verify/mutate live Store")


class FakeCandidateProvider:
    def __init__(self, slot, *, number=901, head=CANDIDATE_HEAD):
        self.slot = slot
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
    def __init__(self, slot, provider):
        self.scenario = slot.scenario
        self.slot = slot
        self.feature_id = slot.feature_id
        self.target_ref = slot.target_ref
        self.candidate_provider = provider
        self.runtime = object()
        self.bundle = SimpleNamespace(runtime=self.runtime)


def execution(installation=INSTALLATION):
    return TrustedMainExecution(
        repository=REPOSITORY,
        installation_commit_sha=installation,
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


def validate_closed_inventory_and_api_surface():
    require(len(SLOTS) == 9, "scenario runtime authority inventory is not exactly nine slots")
    require(tuple(slot.scenario for slot in SLOTS) == EXPECTED_SCENARIOS, "scenario order drifted")
    require(len(set(EXPECTED_SCENARIOS)) == 9, "scenario ids are not unique")
    require(len({slot.feature_id for slot in SLOTS}) == 9, "scenario Feature ids are not unique")
    require(len({slot.target_ref for slot in SLOTS}) == 9, "scenario refs are not unique")
    require(
        all(slot.feature_id != ORIGINAL_FEATURE_ID and slot.target_ref != ORIGINAL_TARGET_REF for slot in SLOTS),
        "remaining scenario authority silently reused the original single fixture",
    )
    for slot in SLOTS:
        require(composition_subject.slot_for_scenario(slot.scenario) == slot, f"slot lookup drifted: {slot.scenario}")
    for invalid in ("", "unknown", "lost-ack-crash-takeover", ORIGINAL_FEATURE_ID, ORIGINAL_TARGET_REF):
        try:
            composition_subject.slot_for_scenario(invalid)
        except composition_subject.V03ScenarioRuntimeCompositionError:
            continue
        raise AssertionError(f"closed scenario selector accepted invalid value: {invalid!r}")

    preflight_parameters = inspect.signature(
        preflight_subject.build_v03_scenario_runtime_preflight
    ).parameters
    require("scenario" in preflight_parameters, "preflight lost closed scenario selector")
    require("feature_id" not in preflight_parameters, "preflight exposed arbitrary Feature selector")
    require("target_ref" not in preflight_parameters, "preflight exposed arbitrary ref selector")


def validate_all_nine_preflights_are_exact_and_zero_effect():
    original = preflight_subject.build_v03_scenario_runtime_composition
    captures = []
    providers = {}
    digests = set()

    def fake_builder(**kwargs):
        slot = composition_subject.slot_for_scenario(kwargs["scenario"])
        provider = FakeCandidateProvider(slot)
        providers[slot.scenario] = provider
        captures.append(dict(kwargs))
        return FakeComposition(slot, provider)

    preflight_subject.build_v03_scenario_runtime_composition = fake_builder
    try:
        for slot in SLOTS:
            result = preflight_subject.build_v03_scenario_runtime_preflight(
                scenario=slot.scenario,
                execution=execution(),
                live_authority=live(),
                reviewer_selection=reviewer(),
                protection_verifier=FakeProtectionVerifier(),
                adapter_id="v03-scenario-release-verifier",
                target_read_token="bounded-read-token",
                actions_token="bounded-actions-token",
                event_write_token="bounded-event-write-token",
                clock=lambda: "2026-08-20T00:00:00Z",
            )
            require(result.scenario == slot.scenario and result.slot == slot, "preflight lost exact scenario slot")
            require(result.composition.feature_id == slot.feature_id, "preflight Feature binding drifted")
            require(result.composition.target_ref == slot.target_ref, "preflight ref binding drifted")
            require(len(result.trusted_context_digest) == 64, "scenario trusted context digest is not SHA-256")
            require(result.trusted_context_digest not in digests, "different scenario slots shared trusted context digest")
            digests.add(result.trusted_context_digest)
            provider = providers[slot.scenario]
            require(len(provider.calls) == 1, "scenario candidate was not fresh-read exactly once")
            call = provider.calls[0]
            require(call["repository"] == REPOSITORY, "candidate repository scope drifted")
            require(call["feature_id"] == slot.feature_id, "candidate Feature scope drifted")
            require(call["target_ref"] == slot.target_ref, "candidate ref scope drifted")
            require(call["operation_id"].endswith(slot.scenario), "candidate preflight operation id lost scenario")

        require(len(captures) == 9, "not every closed slot reached composition exactly once")
        for call, slot in zip(captures, SLOTS):
            config = call["config"]
            require(call["scenario"] == slot.scenario, "composition scenario argument drifted")
            require(config.target_repository == REPOSITORY, "target repository drifted")
            require(config.store_repository == REPOSITORY, "Store repository drifted")
            require(config.installation_ref == "main", "installation ref escaped trusted main")
            require(config.feature_ids == frozenset({slot.feature_id}), "runtime escaped exact slot Feature")
            require(config.feature_ref(slot.feature_id) == slot.target_ref, "runtime escaped exact slot ref")
            require(call["control_repository"] == REPOSITORY, "control repository differs from target/Store")
            require(call["target_read_token"] == "bounded-read-token", "read credential boundary drifted")
            require(call["actions_token"] == "bounded-actions-token", "Actions credential boundary drifted")
            require(call["event_write_token"] == "bounded-event-write-token", "Event-write credential boundary drifted")
            require(call["actions_token"] != call["event_write_token"], "credential split collapsed")
    finally:
        preflight_subject.build_v03_scenario_runtime_composition = original


def _expect_preflight_rejected(
    *,
    scenario=None,
    exec_obj=None,
    live_obj=None,
    reviewer_obj=None,
    protection=None,
    actions="actions",
    event="event",
):
    calls = []
    original = preflight_subject.build_v03_scenario_runtime_composition
    preflight_subject.build_v03_scenario_runtime_composition = lambda **kwargs: calls.append(kwargs)
    exec_obj = exec_obj or execution()
    try:
        try:
            preflight_subject.build_v03_scenario_runtime_preflight(
                scenario=scenario or SLOTS[0].scenario,
                execution=exec_obj,
                live_authority=live_obj or live(exec_obj=exec_obj),
                reviewer_selection=reviewer_obj or reviewer(),
                protection_verifier=protection if protection is not None else FakeProtectionVerifier(),
                adapter_id="adapter",
                target_read_token="read",
                actions_token=actions,
                event_write_token=event,
                clock=lambda: "now",
            )
        except (
            ValueError,
            composition_subject.V03ScenarioRuntimeCompositionError,
            preflight_subject.V03ScenarioRuntimePreflightError,
        ):
            require(calls == [], "rejected authority reached production composition builder")
            return
        raise AssertionError("invalid scenario preflight authority was accepted")
    finally:
        preflight_subject.build_v03_scenario_runtime_composition = original


def validate_preflight_authority_fences():
    other_exec = execution(installation="9" * 40)
    _expect_preflight_rejected(live_obj=live(exec_obj=other_exec))
    _expect_preflight_rejected(live_obj=live(policy_obj=policy(installation="8" * 40)))
    _expect_preflight_rejected(reviewer_obj=reviewer(workflow="unreviewed-reviewer.yml"))
    _expect_preflight_rejected(reviewer_obj=reviewer(present=False))
    _expect_preflight_rejected(protection=object())
    _expect_preflight_rejected(actions="shared", event="shared")
    _expect_preflight_rejected(scenario="not-in-closed-inventory")


def _pr_row(slot, *, draft=False, state="open", ref=None, head=CANDIDATE_HEAD, repo=REPOSITORY, number=701):
    return {
        "number": number,
        "state": state,
        "draft": draft,
        "head": {
            "ref": ref or slot.target_ref,
            "sha": head,
            "repo": {"full_name": repo},
        },
        "base": {"ref": "main"},
    }


def _candidate_provider(slot, payload, *, status=200):
    calls = []
    def fake_get(url, headers):
        calls.append((url, dict(headers)))
        return status, payload
    provider = composition_subject.ScenarioFixtureGitHubCandidateProvider(
        repository=REPOSITORY,
        slot=slot,
        token="read-token",
        http_get=fake_get,
    )
    return provider, calls


def _expect_candidate_rejected(slot, payload, *, status=200):
    provider, _calls = _candidate_provider(slot, payload, status=status)
    try:
        provider.current_candidate(
            operation_id="op",
            repository=REPOSITORY,
            feature_id=slot.feature_id,
            target_ref=slot.target_ref,
        )
    except composition_subject.V03ScenarioRuntimeCompositionError:
        return
    raise AssertionError(f"invalid candidate truth was accepted: {slot.scenario}")


def validate_candidate_truth_is_exact_and_fail_closed():
    for index, slot in enumerate(SLOTS, start=1):
        provider, calls = _candidate_provider(slot, [_pr_row(slot, number=700 + index)])
        candidate = provider.current_candidate(
            operation_id=f"op-{index}",
            repository=REPOSITORY,
            feature_id=slot.feature_id,
            target_ref=slot.target_ref,
        )
        require(candidate.candidate_pr_number == 700 + index, "candidate PR identity drifted")
        require(candidate.candidate_head_sha == CANDIDATE_HEAD, "candidate head identity drifted")
        require(len(calls) == 1 and slot.target_ref.replace("/", "%2F") in calls[0][0], "candidate lookup did not bind the exact slot ref in one GitHub query")

        for bad_kwargs in (
            {"repository": "dream-xin/other"},
            {"feature_id": SLOTS[(index % 9)].feature_id},
            {"target_ref": SLOTS[(index % 9)].target_ref},
        ):
            kwargs = {
                "operation_id": "op",
                "repository": REPOSITORY,
                "feature_id": slot.feature_id,
                "target_ref": slot.target_ref,
            }
            kwargs.update(bad_kwargs)
            try:
                provider.current_candidate(**kwargs)
            except composition_subject.V03ScenarioRuntimeCompositionError:
                continue
            raise AssertionError("candidate lookup accepted identity outside fixed slot")

    slot = SLOTS[0]
    _expect_candidate_rejected(slot, [])
    _expect_candidate_rejected(slot, [_pr_row(slot, draft=True)])
    _expect_candidate_rejected(slot, [_pr_row(slot, state="closed")])
    _expect_candidate_rejected(slot, [_pr_row(slot, ref="verification/wrong-ref")])
    _expect_candidate_rejected(slot, [_pr_row(slot), _pr_row(slot, number=702)])
    _expect_candidate_rejected(slot, [_pr_row(slot, head="short")])
    _expect_candidate_rejected(slot, [_pr_row(slot, repo="dream-xin/other")])
    missing_repo = _pr_row(slot)
    missing_repo["head"].pop("repo")
    _expect_candidate_rejected(slot, [missing_repo])
    missing_full_name = _pr_row(slot)
    missing_full_name["head"]["repo"] = {}
    _expect_candidate_rejected(slot, [missing_full_name])
    _expect_candidate_rejected(slot, {"not": "a-list"})
    _expect_candidate_rejected(slot, [], status=403)


def validate_malformed_candidate_fails_before_any_runner():
    original = preflight_subject.build_v03_scenario_runtime_composition
    slot = SLOTS[0]
    for number, head in ((0, CANDIDATE_HEAD), (901, "short")):
        provider = FakeCandidateProvider(slot, number=number, head=head)
        preflight_subject.build_v03_scenario_runtime_composition = (
            lambda **kwargs: FakeComposition(slot, provider)
        )
        try:
            try:
                preflight_subject.build_v03_scenario_runtime_preflight(
                    scenario=slot.scenario,
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
            except preflight_subject.V03ScenarioRuntimePreflightError:
                continue
            raise AssertionError("malformed scenario fixture candidate was accepted")
        finally:
            preflight_subject.build_v03_scenario_runtime_composition = original


def main():
    validate_closed_inventory_and_api_surface()
    validate_all_nine_preflights_are_exact_and_zero_effect()
    validate_preflight_authority_fences()
    validate_candidate_truth_is_exact_and_fail_closed()
    validate_malformed_candidate_fails_before_any_runner()
    print("PASS: #312 binds all and only the nine #310 slots to exact production authority without launch/effect")
    print("- arbitrary Feature/ref selectors are absent; original single fixture is not reused")
    print("- exact open non-draft PR/head truth is required and adversarial candidate ambiguity fails closed")
    print("- deterministic validation only; zero #221 release rows are claimed")


if __name__ == "__main__":
    main()
