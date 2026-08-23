#!/usr/bin/env python3
"""Zero-effect deterministic validation for the #221 trusted-main live authority gate."""
from __future__ import annotations

from types import SimpleNamespace

import v03_real_runtime_live_authority as subject
from operator_store_protection import PROTECTED, ProtectionReceipt
from operator_vertical import VERTICAL_PROFILE

REPOSITORY = "dream-xin/ai-sdlc"
INSTALLATION = "1" * 40
MATERIALIZATION = "2" * 40
STATE_SHA = "3" * 40
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(value, message):
    if not value:
        raise AssertionError(message)


def _protected(*, digest="a" * 64):
    return ProtectionReceipt(
        repository=REPOSITORY,
        state_ref=STATE_REF,
        status=PROTECTED,
        verifier_identity="github-ruleset:integration:4576406",
        verified_at="2026-08-18T00:00:00Z",
        policy_digest=digest,
    )


def validate_execution_gate_is_pure_and_exact():
    execution = subject.require_trusted_main_execution(
        event_name="workflow_dispatch",
        ref="refs/heads/main",
        repository="DREAM-XIN/ai-sdlc",
        workflow_sha=INSTALLATION,
        checkout_sha=INSTALLATION,
    )
    require(execution.repository == REPOSITORY, "repository was not canonicalized")
    require(execution.installation_commit_sha == INSTALLATION, "installation SHA drifted")
    require(execution.state_ref == STATE_REF, "state ref drifted")

    invalid = (
        dict(event_name="pull_request", ref="refs/heads/main", workflow_sha=INSTALLATION, checkout_sha=INSTALLATION),
        dict(event_name="workflow_dispatch", ref="refs/heads/dev", workflow_sha=INSTALLATION, checkout_sha=INSTALLATION),
        dict(event_name="workflow_dispatch", ref="refs/heads/main", workflow_sha=INSTALLATION, checkout_sha="4" * 40),
        dict(event_name="workflow_dispatch", ref="refs/heads/main", workflow_sha="short", checkout_sha="short"),
    )
    for kwargs in invalid:
        try:
            subject.require_trusted_main_execution(repository=REPOSITORY, **kwargs)
        except subject.V03LiveAuthorityError:
            continue
        raise AssertionError("live authority execution gate accepted non-trusted context")


class FakeVerifier:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.receipts = [_protected(), _protected()]
        self.__class__.instances.append(self)

    def verify(self, repository, state_ref):
        self.calls.append((repository, state_ref))
        return self.receipts[len(self.calls) - 1]


class FakeLoader:
    last_kwargs = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeLoader.last_kwargs = kwargs

    def load(self):
        require(
            self.kwargs["installation_commit_verifier"](REPOSITORY, INSTALLATION),
            "installation verifier did not bind exact checkout",
        )
        require(
            self.kwargs["materialization_commit_verifier"](REPOSITORY, STATE_REF, MATERIALIZATION),
            "materialization verifier rejected exact protected ancestor",
        )
        exact = self.kwargs["document_loader"](MATERIALIZATION, subject.RECEIPT_PATH)
        current = self.kwargs["protected_document_loader"](REPOSITORY, STATE_REF, subject.RECEIPT_PATH)
        require(exact == current, "exact/current receipt loader drifted")
        return SimpleNamespace(
            receipt_ref=f"protected-commit://{REPOSITORY}@{MATERIALIZATION}/{subject.RECEIPT_PATH}",
            receipt_digest="b" * 64,
            bundle_digest="c" * 64,
            installation_commit_sha=INSTALLATION,
            materialization_commit_sha=MATERIALIZATION,
            rollout_verifier=object(),
            resolution_policy_verifier=object(),
            decision_policy_verifier=object(),
        )


class Completed:
    def __init__(self, returncode=0):
        self.returncode = returncode


def _git_factory(*, receipt_installation=INSTALLATION, remote_after=STATE_SHA):
    calls = []

    def git(*args):
        calls.append(args)
        if args[:3] == ("ls-remote", "--refs", "origin"):
            return f"{remote_after}\t{STATE_REF}"
        if args[:3] == ("fetch", "--no-tags", "origin"):
            return ""
        if args[:2] == ("rev-parse", "--verify"):
            return STATE_SHA
        if args[:2] == ("rev-parse", "HEAD"):
            return INSTALLATION
        if args[:3] == ("log", "-1", "--format=%H"):
            return MATERIALIZATION
        if args[:1] == ("show",):
            ref_path = args[1]
            if ref_path in {
                f"{MATERIALIZATION}:{subject.RECEIPT_PATH}",
                f"{subject.TRACKING_REF}:{subject.RECEIPT_PATH}",
            }:
                return '{"installation_commit_sha":"' + receipt_installation + '"}'
            return "{}"
        raise AssertionError(f"unexpected fake git call: {args}")

    return git, calls


def validate_live_loader_binds_positive_protection_and_exact_current_main():
    originals = (
        subject.GitHubRepositoryProtectionVerifier,
        subject.ProtectedVerticalPolicyBundleLoader,
        subject.subprocess.run,
    )
    subject.GitHubRepositoryProtectionVerifier = FakeVerifier
    subject.ProtectedVerticalPolicyBundleLoader = FakeLoader
    subject.subprocess.run = lambda *args, **kwargs: Completed(0)
    FakeVerifier.instances.clear()
    try:
        git, calls = _git_factory()
        execution = subject.TrustedMainExecution(REPOSITORY, INSTALLATION, STATE_REF)
        live = subject.load_live_authority(
            execution=execution,
            admin_token="trusted-admin",
            operator_app_slug="runtime-app",
            operator_app_id=4576406,
            git=git,
        )
        require(live.execution is execution, "live authority lost trusted-main execution identity")
        require(live.materialization_commit_sha == MATERIALIZATION, "materialization SHA drifted")
        require(live.protected_state_ref_sha == STATE_SHA, "stable protected state SHA drifted")
        require(len(FakeVerifier.instances) == 1, "live authority built more than one protection verifier")
        verifier = FakeVerifier.instances[0]
        require(verifier.calls == [(REPOSITORY, STATE_REF), (REPOSITORY, STATE_REF)], "protection was not checked before and after")
        require(verifier.kwargs["admin_token"] if "admin_token" in verifier.kwargs else verifier.kwargs["token"] == "trusted-admin", "trusted protection token boundary drifted")
        require(FakeLoader.last_kwargs["installation_commit_sha"] == INSTALLATION, "policy loader not bound to exact current main")
        require(FakeLoader.last_kwargs["materialization_commit_sha"] == MATERIALIZATION, "policy loader not bound to exact materialization")
        require(any(call[:3] == ("fetch", "--no-tags", "origin") for call in calls), "protected state was not refreshed")
    finally:
        (
            subject.GitHubRepositoryProtectionVerifier,
            subject.ProtectedVerticalPolicyBundleLoader,
            subject.subprocess.run,
        ) = originals


def validate_stale_installation_and_protection_drift_fail_closed():
    originals = (
        subject.GitHubRepositoryProtectionVerifier,
        subject.ProtectedVerticalPolicyBundleLoader,
        subject.subprocess.run,
    )
    subject.GitHubRepositoryProtectionVerifier = FakeVerifier
    subject.ProtectedVerticalPolicyBundleLoader = FakeLoader
    subject.subprocess.run = lambda *args, **kwargs: Completed(0)
    try:
        stale_git, _ = _git_factory(receipt_installation="9" * 40)
        try:
            subject.load_live_authority(
                execution=subject.TrustedMainExecution(REPOSITORY, INSTALLATION, STATE_REF),
                admin_token="trusted-admin",
                operator_app_slug="runtime-app",
                operator_app_id=4576406,
                git=stale_git,
            )
        except subject.V03LiveAuthorityError as exc:
            require("exact trusted-main installation" in str(exc), "stale installation failed for wrong reason")
        else:
            raise AssertionError("stale policy installation was accepted")

        FakeVerifier.instances.clear()
        class DriftVerifier(FakeVerifier):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.receipts = [_protected(digest="a" * 64), _protected(digest="d" * 64)]
        subject.GitHubRepositoryProtectionVerifier = DriftVerifier
        git, _ = _git_factory()
        try:
            subject.load_live_authority(
                execution=subject.TrustedMainExecution(REPOSITORY, INSTALLATION, STATE_REF),
                admin_token="trusted-admin",
                operator_app_slug="runtime-app",
                operator_app_id=4576406,
                git=git,
            )
        except subject.V03LiveAuthorityError as exc:
            require("protection generation changed" in str(exc), "protection drift failed for wrong reason")
        else:
            raise AssertionError("protection generation drift was accepted")
    finally:
        (
            subject.GitHubRepositoryProtectionVerifier,
            subject.ProtectedVerticalPolicyBundleLoader,
            subject.subprocess.run,
        ) = originals


def main():
    validate_execution_gate_is_pure_and_exact()
    validate_live_loader_binds_positive_protection_and_exact_current_main()
    validate_stale_installation_and_protection_drift_fail_closed()
    print("PASS: v0.3 live authority is workflow-dispatch/main/exact-installation bound and fails closed on policy/protection drift")


if __name__ == "__main__":
    main()
