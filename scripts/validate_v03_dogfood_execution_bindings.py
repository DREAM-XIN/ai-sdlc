#!/usr/bin/env python3
"""Zero-effect regression tests for v0.3 dogfood execution binding preflight."""
from __future__ import annotations

from gh_aw_provider_registry import load_registry
from v03_dogfood_execution_bindings import (
    V03DogfoodExecutionBindingError,
    credential_identities,
    render_result,
    require_trusted_main_context,
    resolve_dogfood_execution_bindings,
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def base_presence() -> dict[str, bool]:
    registry = load_registry()
    return {identity: False for identity in credential_identities(registry)}


def preferred_presence() -> dict[str, bool]:
    presence = base_presence()
    presence.update(
        {
            "COPILOT_GITHUB_TOKEN": True,
            "OPENAI_API_KEY": True,
            "CODEX_API_KEY": False,
            "ANTHROPIC_API_KEY": True,
            "GEMINI_API_KEY": True,
        }
    )
    return presence


def by_role(bindings):
    return {binding.role: binding for binding in bindings}


def validate_preferred_bindings():
    bindings = resolve_dogfood_execution_bindings(preferred_presence())
    require(len(bindings) == 3, "dogfood preflight must resolve exactly three production bindings")
    rows = by_role(bindings)

    developer = rows["developer"]
    require(developer.selected_profile == "codex", "Developer preferred route must select codex")
    require(developer.worker_workflow == "ai-sdlc-gh-aw-worker-codex.lock.yml", "Developer must use trusted generic codex lock")
    require(developer.present_credential_identities == ("OPENAI_API_KEY",), "Developer exact present credential binding drifted")
    require(developer.specialized_role_worker is False, "Developer must remain on generic Worker contract")
    require(developer.fallback is False, "Developer preferred route unexpectedly fell back")

    reviewer = rows["reviewer"]
    require(reviewer.selected_profile == "claude", "Reviewer preferred route must select claude")
    require(reviewer.worker_workflow == "ai-sdlc-gh-aw-reviewer-claude.lock.yml", "Reviewer specialized claude lock drifted")
    require(reviewer.present_credential_identities == ("ANTHROPIC_API_KEY",), "Reviewer credential binding drifted")
    require(reviewer.specialized_role_worker is True, "Reviewer must use specialized read-only Gate worker")

    qa = rows["qa"]
    require(qa.selected_profile == "gemini", "QA preferred route must select gemini")
    require(qa.worker_workflow == "ai-sdlc-gh-aw-qa-gemini.lock.yml", "QA specialized gemini lock drifted")
    require(qa.present_credential_identities == ("GEMINI_API_KEY",), "QA credential binding drifted")
    require(qa.specialized_role_worker is True, "QA must use specialized read-only Gate worker")

    result = render_result(installation_sha="a" * 40, bindings=bindings)
    require(result["status"] == "READY", "complete exact bindings must be READY")
    for field in (
        "model_called",
        "worker_dispatched",
        "operator_store_mutated",
        "feature_event_written",
        "dogfood_evidence_created",
        "release_evidence_created",
        "release_status_changed",
    ):
        require(result[field] is False, f"binding preflight must remain zero-effect: {field}")
    require(result["entitlement_verified"] is False, "presence-only preflight must not overclaim provider entitlement")


def validate_fallbacks_are_explicit():
    cases = (
        ("developer", ("OPENAI_API_KEY", "CODEX_API_KEY"), "ai-sdlc-gh-aw-worker.lock.yml"),
        ("reviewer", ("ANTHROPIC_API_KEY",), "ai-sdlc-gh-aw-reviewer-copilot.lock.yml"),
        ("qa", ("GEMINI_API_KEY",), "ai-sdlc-gh-aw-qa-copilot.lock.yml"),
    )
    for role, preferred_credentials, expected_workflow in cases:
        presence = preferred_presence()
        for identity in preferred_credentials:
            presence[identity] = False
        rows = by_role(resolve_dogfood_execution_bindings(presence))
        binding = rows[role]
        require(binding.selected_profile == "copilot", f"{role}: missing preferred credential must select copilot fallback")
        require(binding.fallback is True, f"{role}: fallback must be explicit")
        require(binding.fallback_reason == "PREFERRED_CANDIDATE_NOT_READY", f"{role}: fallback reason drifted")
        require(binding.worker_workflow == expected_workflow, f"{role}: fallback Worker identity drifted")
        require(binding.present_credential_identities == ("COPILOT_GITHUB_TOKEN",), f"{role}: fallback credential binding drifted")


def validate_no_ready_candidate_fails_closed():
    presence = preferred_presence()
    presence["OPENAI_API_KEY"] = False
    presence["CODEX_API_KEY"] = False
    presence["ANTHROPIC_API_KEY"] = False
    presence["GEMINI_API_KEY"] = False
    presence["COPILOT_GITHUB_TOKEN"] = False
    try:
        resolve_dogfood_execution_bindings(presence)
    except Exception as exc:
        require("NO_READY_CANDIDATE" in str(exc), "no-ready failure must expose fail-closed routing reason")
    else:
        raise AssertionError("no-ready production bindings unexpectedly resolved")


def validate_presence_contract_is_closed():
    presence = preferred_presence()
    removed = next(iter(presence))
    del presence[removed]
    try:
        resolve_dogfood_execution_bindings(presence)
    except V03DogfoodExecutionBindingError:
        pass
    else:
        raise AssertionError("incomplete credential-presence contract unexpectedly accepted")

    presence = preferred_presence()
    presence["UNTRUSTED_EXTRA_CREDENTIAL"] = True
    try:
        resolve_dogfood_execution_bindings(presence)
    except V03DogfoodExecutionBindingError:
        pass
    else:
        raise AssertionError("extra credential-presence identity unexpectedly accepted")


def validate_main_only_gate():
    sha = "b" * 40
    require(
        require_trusted_main_context(
            event_name="workflow_dispatch",
            ref="refs/heads/main",
            workflow_sha=sha,
            checkout_sha=sha,
        ) == sha,
        "exact trusted-main workflow_dispatch should pass pure gate",
    )
    for kwargs in (
        dict(event_name="pull_request", ref="refs/heads/main", workflow_sha=sha, checkout_sha=sha),
        dict(event_name="workflow_dispatch", ref="refs/heads/feature/x", workflow_sha=sha, checkout_sha=sha),
        dict(event_name="workflow_dispatch", ref="refs/heads/main", workflow_sha=sha, checkout_sha="c" * 40),
    ):
        try:
            require_trusted_main_context(**kwargs)
        except V03DogfoodExecutionBindingError:
            continue
        raise AssertionError(f"unauthorized dogfood binding context unexpectedly accepted: {kwargs}")


def main():
    validate_preferred_bindings()
    validate_fallbacks_are_explicit()
    validate_no_ready_candidate_fails_closed()
    validate_presence_contract_is_closed()
    validate_main_only_gate()
    print("v0.3 dogfood execution binding validation passed")
    print("- Developer: codex -> copilot; generic trusted Registry Worker")
    print("- Reviewer: claude -> copilot; specialized read-only Gate Worker")
    print("- QA: gemini -> copilot; specialized read-only Gate Worker")
    print("- exact credential presence + Worker identity are bound before cloud spend")
    print("- no model call, Worker dispatch, Store/Event mutation, or dogfood/release evidence")


if __name__ == "__main__":
    main()
