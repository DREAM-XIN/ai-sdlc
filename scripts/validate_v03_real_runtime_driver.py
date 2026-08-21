#!/usr/bin/env python3
"""Zero-effect validation for the v0.3 release-driver shell."""
from __future__ import annotations

from types import SimpleNamespace

import v03_real_runtime_driver as subject

REPOSITORY = "dream-xin/ai-sdlc"
SHA = "1" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def validate_mode_gate():
    require(
        subject.require_driver_mode(
            mode=subject.VALIDATE_ONLY,
            event_name="pull_request",
            ref="refs/pull/303/merge",
        ) == subject.VALIDATE_ONLY,
        "PR validate-only mode was rejected",
    )
    invalid = (
        (subject.VALIDATE_ONLY, "workflow_dispatch", "refs/heads/main"),
        (subject.PREFLIGHT_ONLY, "pull_request", "refs/pull/303/merge"),
        (subject.PREFLIGHT_ONLY, "workflow_dispatch", "refs/heads/dev"),
        ("execute", "workflow_dispatch", "refs/heads/main"),
    )
    for mode, event, ref in invalid:
        try:
            subject.require_driver_mode(mode=mode, event_name=event, ref=ref)
        except subject.V03ReleaseDriverError:
            continue
        raise AssertionError("driver mode gate accepted unauthorized execution mode/context")


def validate_live_assembly_binds_existing_authorities_without_effect():
    calls = []
    execution = SimpleNamespace(repository=REPOSITORY, installation_commit_sha=SHA)
    live = SimpleNamespace(
        execution=execution,
        materialization_commit_sha="2" * 40,
        protected_state_ref_sha="3" * 40,
        policy=SimpleNamespace(bundle_digest="b" * 64),
    )
    selection = SimpleNamespace(
        worker_id="code-review-reviewer-claude",
        workflow_file="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
    )
    protection = object()
    preflight = SimpleNamespace(marker="preflight")

    def fake_live_loader(**kwargs):
        calls.append(("live", kwargs))
        return live

    def fake_reviewer_selector(**kwargs):
        calls.append(("reviewer", kwargs))
        return selection

    def fake_protection(**kwargs):
        calls.append(("protection", kwargs))
        return protection

    def fake_preflight(**kwargs):
        calls.append(("preflight", kwargs))
        require(kwargs["live_authority"] is live, "driver did not pass exact live authority")
        require(kwargs["reviewer_selection"] is selection, "driver did not pass exact Reviewer selection")
        require(kwargs["protection_verifier"] is protection, "driver did not pass exact protection verifier")
        require(kwargs["target_read_token"] == "actions-read", "target read token drifted")
        require(kwargs["actions_token"] == "actions-read", "Actions token drifted")
        require(kwargs["event_write_token"] == "event-write", "Event-write token drifted")
        return preflight

    original_execution_gate = subject.require_trusted_main_execution
    subject.require_trusted_main_execution = lambda **kwargs: execution
    try:
        result = subject.assemble_live_preflight(
            env={
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_REPOSITORY": REPOSITORY,
                "GITHUB_SHA": SHA,
                "GITHUB_API_URL": "https://api.github.com",
                "AI_SDLC_OPERATOR_ADMIN_TOKEN": "admin",
                "AI_SDLC_OPERATOR_APP_SLUG": "runtime-app",
                "AI_SDLC_OPERATOR_APP_INTEGRATION_ID": "4576406",
                "AI_SDLC_ACTIONS_READ_TOKEN": "actions-read",
                "AI_SDLC_EVENT_WRITE_TOKEN": "event-write",
            },
            checkout_sha=SHA,
            live_loader=fake_live_loader,
            reviewer_selector=fake_reviewer_selector,
            preflight_builder=fake_preflight,
            protection_verifier_factory=fake_protection,
            clock=lambda: "now",
        )
    finally:
        subject.require_trusted_main_execution = original_execution_gate
    require(result is preflight, "driver replaced exact preflight result")
    require([name for name, _ in calls] == ["live", "reviewer", "protection", "preflight"], "driver authority assembly order drifted")
    require(calls[0][1]["admin_token"] == "admin", "live protection admin token boundary drifted")
    require(calls[2][1]["token"] == "admin", "Store protection verifier admin token drifted")


def validate_missing_or_invalid_configuration_fails_before_preflight():
    base = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_SHA": SHA,
        "GITHUB_API_URL": "https://api.github.com",
        "AI_SDLC_OPERATOR_ADMIN_TOKEN": "admin",
        "AI_SDLC_OPERATOR_APP_SLUG": "runtime-app",
        "AI_SDLC_OPERATOR_APP_INTEGRATION_ID": "4576406",
        "AI_SDLC_ACTIONS_READ_TOKEN": "actions-read",
        "AI_SDLC_EVENT_WRITE_TOKEN": "event-write",
    }
    for key, value in (
        ("AI_SDLC_OPERATOR_ADMIN_TOKEN", ""),
        ("AI_SDLC_OPERATOR_APP_INTEGRATION_ID", "bad"),
        ("AI_SDLC_ACTIONS_READ_TOKEN", ""),
        ("AI_SDLC_EVENT_WRITE_TOKEN", ""),
    ):
        env = dict(base)
        env[key] = value
        touched = []
        original_execution_gate = subject.require_trusted_main_execution
        subject.require_trusted_main_execution = lambda **kwargs: SimpleNamespace(repository=REPOSITORY, installation_commit_sha=SHA)
        try:
            try:
                subject.assemble_live_preflight(
                    env=env,
                    checkout_sha=SHA,
                    live_loader=lambda **kwargs: touched.append("live"),
                    reviewer_selector=lambda **kwargs: touched.append("reviewer"),
                    preflight_builder=lambda **kwargs: touched.append("preflight"),
                    protection_verifier_factory=lambda **kwargs: touched.append("protection"),
                )
            except subject.V03ReleaseDriverError:
                continue
            raise AssertionError(f"invalid driver configuration {key} was accepted")
        finally:
            subject.require_trusted_main_execution = original_execution_gate


def main():
    validate_mode_gate()
    validate_live_assembly_binds_existing_authorities_without_effect()
    validate_missing_or_invalid_configuration_fails_before_preflight()
    print("PASS: v0.3 release-driver shell keeps PR zero-effect and live mode preflight-only")


if __name__ == "__main__":
    main()
