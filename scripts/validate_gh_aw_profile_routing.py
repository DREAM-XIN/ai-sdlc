#!/usr/bin/env python3
"""Deterministic validation for trusted gh-aw profile routing."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from gh_aw_profile_readiness import readiness_from_presence
from gh_aw_profile_routing import RoutingValidationError, load_routing_policy, resolve_route, resolution_payload
from gh_aw_provider_registry import load_registry

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_invalid(text, registry, fragment):
    with tempfile.TemporaryDirectory(prefix="ghaw-routing-invalid-") as tmp:
        path = Path(tmp) / "routing.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            load_routing_policy(path, registry=registry)
        except Exception as exc:
            require(fragment in str(exc), f"unexpected validation failure: {exc}")
        else:
            raise AssertionError(f"invalid routing fixture unexpectedly passed: {fragment}")


def main():
    registry = load_registry()
    policy = load_routing_policy(registry=registry)
    require(policy.default_profile == "copilot", "routing compatibility default changed")
    expected = {
        ("product", "requirement"): ("claude", "copilot"),
        ("architect", "design"): ("claude", "copilot"),
        ("orchestrator", "plan"): ("codex", "copilot"),
        ("developer", "implementation"): ("codex", "copilot"),
        ("reviewer", "code-review"): ("claude", "copilot"),
        ("qa", "verification"): ("gemini", "copilot"),
    }
    for key, candidates in expected.items():
        require(policy.require_rule(*key).candidates == candidates, f"candidate order drifted for {key}")

    presence = {
        identity: False
        for profile in registry.profiles
        for identity in (profile.credential, *profile.credential_aliases)
    }
    presence.update(
        {
            "COPILOT_GITHUB_TOKEN": True,
            "OPENAI_API_KEY": True,
            "CODEX_API_KEY": True,
            "ANTHROPIC_API_KEY": True,
            "GEMINI_API_KEY": True,
        }
    )
    readiness = readiness_from_presence(registry, presence)
    resolution, profile = resolve_route(policy, registry, role="developer", stage="implementation", readiness=readiness, validate_compiled_worker=False)
    preferred_payload = resolution_payload(resolution, profile)
    require(preferred_payload["selected"]["profile"] == "codex", "Developer preferred route did not select Codex")
    require(preferred_payload["candidate_order"] == ["codex", "copilot"], "preferred audit lost complete candidate order")

    fallback_presence = dict(presence)
    fallback_presence["OPENAI_API_KEY"] = False
    fallback_presence["CODEX_API_KEY"] = False
    resolution, profile = resolve_route(policy, registry, role="developer", stage="implementation", readiness=readiness_from_presence(registry, fallback_presence), validate_compiled_worker=False)
    payload = resolution_payload(resolution, profile)
    require(payload["selected"]["profile"] == "copilot", "Developer fallback route did not select Copilot")
    require(payload["fallback"] is True, "fallback audit flag missing")
    require(payload["candidate_order"] == ["codex", "copilot"], "fallback audit lost complete candidate order")
    require(
        payload["candidates"] == [
            {"profile": "codex", "ready": False, "reason": "MISSING_CREDENTIAL"},
            {"profile": "copilot", "ready": True, "reason": "SELECTED"},
        ],
        "fallback evaluated-decision audit drifted",
    )
    require(payload["selection_mode"] == "policy", "policy selection mode missing")
    require(payload["entitlement_verified"] is False, "static routing overclaimed entitlement")
    for profile in registry.profiles:
        for identity in (profile.credential, *profile.credential_aliases):
            require(identity not in json.dumps(payload), f"credential identity {identity!r} leaked into routing audit")
            require(identity not in json.dumps(preferred_payload), f"credential identity {identity!r} leaked into preferred routing audit")

    no_ready = dict(fallback_presence)
    no_ready["COPILOT_GITHUB_TOKEN"] = False
    try:
        resolve_route(policy, registry, role="developer", stage="implementation", readiness=readiness_from_presence(registry, no_ready), validate_compiled_worker=False)
    except RoutingValidationError as exc:
        require("NO_READY_CANDIDATE" in str(exc), f"unexpected no-ready failure: {exc}")
    else:
        raise AssertionError("no-ready route unexpectedly selected a profile")

    try:
        policy.require_rule("product", "acceptance")
    except RoutingValidationError:
        pass
    else:
        raise AssertionError("unknown role/stage route unexpectedly resolved")

    incomplete = {"codex": True}
    try:
        resolve_route(policy, registry, role="developer", stage="implementation", readiness=incomplete, validate_compiled_worker=False)
    except RoutingValidationError as exc:
        require("copilot" in str(exc) and "readiness" in str(exc), f"unexpected incomplete readiness failure: {exc}")
    else:
        raise AssertionError("partial readiness map unexpectedly selected preferred profile")

    base = """version: 0.1.0\ndefault_profile: copilot\nrules:\n  - id: fixture\n    match: {role: developer, stage: implementation}\n    candidates: [codex, copilot]\n    allow_experimental: false\n"""
    expect_invalid(base.replace("candidates: [codex, copilot]", "candidates: [codex, codex]"), registry, "duplicate candidates")
    expect_invalid(base.replace("codex, copilot", "not-registered, copilot"), registry, "unknown gh-aw engine profile")
    expect_invalid(base.replace("codex, copilot", "qwen, copilot"), registry, "experimental profile")
    trusted_experimental = base.replace("codex, copilot", "qwen, copilot").replace("allow_experimental: false", "allow_experimental: true")
    with tempfile.TemporaryDirectory(prefix="ghaw-routing-") as tmp:
        path = Path(tmp) / "routing.yaml"
        path.write_text(trusted_experimental, encoding="utf-8")
        exp_policy = load_routing_policy(path, registry=registry)
        require(exp_policy.require_rule("developer", "implementation").allow_experimental is True, "trusted experimental opt-in rejected")

    duplicate_match = base + """  - id: fixture-two\n    match: {role: developer, stage: implementation}\n    candidates: [copilot]\n    allow_experimental: false\n"""
    expect_invalid(duplicate_match, registry, "duplicate routing role/stage match")
    print("gh-aw trusted profile routing validation passed")


if __name__ == "__main__":
    main()
