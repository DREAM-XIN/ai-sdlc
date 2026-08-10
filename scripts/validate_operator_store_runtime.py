#!/usr/bin/env python3
"""Validate durable remote Store composition and concrete protection verification."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from operator_store import plan_operation_start
from operator_store_github_protection import GitHubBranchProtectionVerifier
from operator_store_git import CasConflict
from operator_store_model import operation_ids, rebuild_projection
from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionReceipt, StaticProtectionVerifier
from operator_store_runtime import (
    DEFAULT_OPERATOR_STATE_REF,
    TrustedOperatorStoreConfig,
    build_trusted_operator_api_backends,
    build_trusted_operator_store_runtime,
)

REPO = "DREAM-XIN/ai-sdlc"
NOW = "2026-08-10T04:30:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FixtureProductionVerifier:
    test_only = False

    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity="fixture-production-verifier",
            verified_at=NOW,
            policy_digest="fixture-policy",
        )


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def clone(remote: Path, target: Path):
    git("clone", "-q", str(remote), str(target))
    git("config", "user.name", "ai-sdlc-test", cwd=target)
    git("config", "user.email", "ai-sdlc@example.invalid", cwd=target)


def start_plan(snapshot, feature_id, key):
    return plan_operation_start(
        snapshot,
        target_repository=REPO,
        feature_id=feature_id,
        expected_revision=1,
        idempotency_key=key,
        occurred_at=NOW,
        trusted_context_digest="runtime-fixture",
    )


def validate_remote_durability_and_cas():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-operator-remote-") as td:
        root = Path(td)
        remote = root / "control.git"
        writer_a = root / "writer-a"
        writer_b = root / "writer-b"
        fresh = root / "fresh"
        git("init", "--bare", "-q", str(remote))
        clone(remote, writer_a)
        clone(remote, writer_b)

        verifier = FixtureProductionVerifier()
        config_a = TrustedOperatorStoreConfig(repository=REPO, trusted_checkout=writer_a)
        config_b = TrustedOperatorStoreConfig(repository=REPO, trusted_checkout=writer_b)
        runtime_a = build_trusted_operator_store_runtime(config_a, protection_verifier=verifier)
        runtime_b = build_trusted_operator_store_runtime(config_b, protection_verifier=verifier)
        receipt_a = verifier.verify(REPO, DEFAULT_OPERATOR_STATE_REF)
        receipt_b = verifier.verify(REPO, DEFAULT_OPERATOR_STATE_REF)

        first = runtime_a.backend.commit(start_plan(runtime_a.backend.read_snapshot(), "F-REMOTE-A", "a"), receipt_a)
        require(first.ref_sha, "first remote Store write did not produce a commit")

        seen_b = runtime_b.backend.read_snapshot()
        require(first.result["operation_id"] in operation_ids(seen_b), "second clone could not observe durable remote Store state")
        require(rebuild_projection(seen_b, first.result["operation_id"])["status"] == "RUNNING", "remote projection did not survive fresh reader")

        stale_b = seen_b
        second = runtime_a.backend.commit(start_plan(runtime_a.backend.read_snapshot(), "F-REMOTE-B", "b"), receipt_a)
        require(second.result["operation_id"] in operation_ids(runtime_a.backend.read_snapshot()), "second remote write missing")
        try:
            runtime_b.backend.commit(start_plan(stale_b, "F-REMOTE-C", "c"), receipt_b)
            raise AssertionError("stale remote CAS unexpectedly succeeded")
        except CasConflict:
            pass

        # Re-planning after a real remote conflict converges on the new durable head.
        replanned = runtime_b.backend.commit_replanned(
            lambda snapshot: start_plan(snapshot, "F-REMOTE-C", "c"),
            receipt_b,
        )
        require(replanned.result["operation_id"] in operation_ids(runtime_b.backend.read_snapshot()), "remote CAS re-plan did not persist")

        clone(remote, fresh)
        runtime_fresh = build_trusted_operator_store_runtime(
            TrustedOperatorStoreConfig(repository=REPO, trusted_checkout=fresh),
            protection_verifier=verifier,
        )
        durable = runtime_fresh.backend.read_snapshot()
        require(len(operation_ids(durable)) == 3, "fresh clone did not recover all remote Operation state")


def validate_concrete_protection_verifier():
    protected_payload = {
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "restrictions": {"apps": [{"slug": "ai-sdlc-operator"}]},
    }

    calls = []
    def protected_get(url, headers):
        calls.append((url, headers))
        return 200, protected_payload

    verifier = GitHubBranchProtectionVerifier(
        token="trusted-token",
        operator_app_slug="ai-sdlc-operator",
        http_get=protected_get,
        clock=lambda: NOW,
    )
    result = verifier.verify(REPO, DEFAULT_OPERATOR_STATE_REF)
    require(result.status == PROTECTED and result.policy_digest, "positive GitHub protection proof was not accepted")
    require("Authorization" in calls[0][1] and calls[0][1]["Authorization"].startswith("Bearer "), "GitHub protection query lacks trusted authentication")
    require("ai-sdlc-operator-state/protection" in calls[0][0], "protection verifier queried wrong branch")

    missing_app = GitHubBranchProtectionVerifier(
        token="trusted-token",
        operator_app_slug="other-app",
        http_get=lambda u, h: (200, protected_payload),
        clock=lambda: NOW,
    ).verify(REPO, DEFAULT_OPERATOR_STATE_REF)
    require(missing_app.status == UNPROTECTED, "missing configured Operator App did not fail closed")

    not_found = GitHubBranchProtectionVerifier(
        token="trusted-token",
        operator_app_slug="ai-sdlc-operator",
        http_get=lambda u, h: (404, {}),
        clock=lambda: NOW,
    ).verify(REPO, DEFAULT_OPERATOR_STATE_REF)
    require(not_found.status == UNPROTECTED, "known absent branch protection did not map to UNPROTECTED")

    unavailable = GitHubBranchProtectionVerifier(
        token="trusted-token",
        operator_app_slug="ai-sdlc-operator",
        http_get=lambda u, h: (503, {}),
        clock=lambda: NOW,
    ).verify(REPO, DEFAULT_OPERATOR_STATE_REF)
    require(unavailable.status == UNKNOWN, "protection inspection failure did not fail closed as UNKNOWN")


def validate_production_composition_guards():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-operator-runtime-") as td:
        checkout = Path(td)
        config = TrustedOperatorStoreConfig(repository=REPO, trusted_checkout=checkout)
        require(config.state_ref == DEFAULT_OPERATOR_STATE_REF, "trusted default state ref changed")
        try:
            build_trusted_operator_store_runtime(
                config,
                protection_verifier=StaticProtectionVerifier(status=PROTECTED),
            )
            raise AssertionError("test-only StaticProtectionVerifier entered production runtime")
        except ValueError:
            pass
        try:
            TrustedOperatorStoreConfig(
                repository=REPO,
                trusted_checkout=checkout,
                state_ref="feature/user-controlled",
            )
            raise AssertionError("non-ref trusted state-ref override unexpectedly accepted")
        except ValueError:
            pass

    # Capability composition still exposes only the approved Store-backed slice.
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-operator-compose-") as td:
        root = Path(td)
        remote = root / "control.git"
        checkout = root / "checkout"
        git("init", "--bare", "-q", str(remote))
        clone(remote, checkout)
        backends = build_trusted_operator_api_backends(
            TrustedOperatorStoreConfig(repository=REPO, trusted_checkout=checkout),
            protection_verifier=FixtureProductionVerifier(),
        )
        require(set(backends) == {"operation.start", "operation.status", "operation.cancel"}, "production runtime exposed out-of-scope capability")


def main():
    validate_remote_durability_and_cas()
    validate_concrete_protection_verifier()
    validate_production_composition_guards()
    print("Operator Store remote durability/protection validation passed")


if __name__ == "__main__":
    main()
