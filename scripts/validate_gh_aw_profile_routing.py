#!/usr/bin/env python3
"""Deterministic regression tests for trusted gh-aw role-aware routing."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from gh_aw_profile_readiness import readiness_from_presence
from gh_aw_profile_routing import (
    RoutingValidationError,
    load_routing_policy,
    resolve_route,
    resolution_payload,
)
from gh_aw_provider_registry import RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_invalid(text: str, registry, fragment: str) -> None:
    with tempfile.TemporaryDirectory(prefix="ghaw-routing-") as tmp:
        path = Path(tmp) / "routing.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            load_routing_policy(path, registry=registry)
        except (RoutingValidationError, RegistryValidationError) as exc:
            require(fragment in str(exc), f"unexpected trusted validation error: {exc}")
        else:
            raise AssertionError(f"invalid routing fixture unexpectedly passed: {fragment}")


def main() -> int:
    registry = load_registry()
    policy = load_routing_policy(registry=registry)

    require(policy.default_profile == "copilot", "global compatibility default drifted")
    require(policy.require_rule("developer", "implementation").candidates == ("codex", "copilot"), "Developer route drifted")
    require(policy.require_rule("reviewer", "code-review").candidates == ("claude", "copilot"), "Reviewer route drifted")
    require(policy.require_rule("qa", "verification").candidates == ("gemini", "copilot"), "QA route drifted")
    for rule in policy.rules:
        require(not rule.allow_experimental, f"default rule {rule.rule_id} unexpectedly allows experimental profiles")
        require(all(registry.require_profile(pid).maturity != "experimental" for pid in rule.candidates), f"default rule {rule.rule_id} contains experimental profile")

    presence = {profile.credential: False for profile in registry.profiles}
    for profile in registry.profiles:
        for alias in profile.credential_aliases:
            presence[alias] = False

    preferred_presence = dict(presence)
    preferred_presence["OPENAI_API_KEY"] = True
    preferred_presence["COPILOT_GITHUB_TOKEN"] = True
    readiness = readiness_from_presence(registry, preferred_presence)
    resolution, profile = resolve_route(
        policy, registry, role="developer", stage="implementation", readiness=readiness, validate_compiled_worker=False
    )
    require(profile.profile_id == "codex", "Developer did not select preferred Codex profile")
    require(not resolution.fallback, "preferred selection incorrectly marked fallback")

    alias_presence = dict(presence)
    alias_presence["CODEX_API_KEY"] = True
    alias_presence["COPILOT_GITHUB_TOKEN"] = True
    alias_readiness = readiness_from_presence(registry, alias_presence)
    require(alias_readiness["codex"], "Codex approved alias did not produce readiness")

    fallback_presence = dict(presence)
    fallback_presence["COPILOT_GITHUB_TOKEN"] = True
    fallback_readiness = readiness_from_presence(registry, fallback_presence)
    resolution, profile = resolve_route(
        policy, registry, role="developer", stage="implementation", readiness=fallback_readiness, validate_compiled_worker=False
    )
    require(profile.profile_id == "copilot", "Developer fallback did not select Copilot")
    require(resolution.fallback, "fallback selection was not audited")
    require(resolution.fallback_reason == "PREFERRED_CANDIDATE_NOT_READY", "fallback reason drifted")
    payload = resolution_payload(resolution, profile)
    require(payload["selection_mode"] == "policy", "policy selection mode missing")
    require(payload["entitlement_verified"] is False, "static routing overclaimed entitlement")
    require("OPENAI_API_KEY" not in json.dumps(payload), "credential identity leaked into routing audit")

    try:
        resolve_route(
            policy, registry, role="developer", stage="implementation", readiness=readiness_from_presence(registry, presence), validate_compiled_worker=False
        )
    except RoutingValidationError as exc:
        require("NO_READY_CANDIDATE" in str(exc), f"unexpected no-ready failure: {exc}")
    else:
        raise AssertionError("no-ready route unexpectedly selected a profile")

    try:
        policy.require_rule("product", "requirement")
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
        require(exp_policy.rules[0].allow_experimental, "trusted experimental opt-in was not retained")

    require(registry.require_profile("copilot").credential_source == "github-token", "Copilot credential source drifted")
    require(not registry.require_profile("copilot").credential_aliases, "github-token profile unexpectedly has aliases")
    for profile in registry.profiles:
        require(profile.credential_source in {"secret", "github-token"}, "unsupported credential source escaped validation")

    print("gh-aw trusted role-aware routing validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
