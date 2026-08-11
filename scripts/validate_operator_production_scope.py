#!/usr/bin/env python3
"""Adversarial scope validation for the authoritative production bundle builder."""
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from operator_api import API_VERSION, dispatch
from operator_mcp import ADAPTER_ID
from operator_production_bundle import build_trusted_operator_backend_bundle
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_store import plan_operation_start
from operator_store_model import rebuild_projection
from operator_store_protection import PROTECTED, ProtectionReceipt
from operator_store_remote_git import RemoteGitStateRefBackend
from validate_operator_production_runtime import FakeGitHubContents

TARGET = "DREAM-XIN/fixture"
OTHER = "DREAM-XIN/other"
STORE = "DREAM-XIN/control-fixture"
FEATURE = "F-RUNTIME-COMPOSITION-0001"
FEATURE_REF = "feature/F-RUNTIME-COMPOSITION-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-11T05:00:00Z"

WRITE_CAPABILITIES = {"operation.start", "operation.cancel"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FixtureVerifier:
    test_only = False

    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository.lower(),
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity="scope-fixture",
            verified_at=NOW,
            policy_digest="scope-policy",
        )


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def seed(backend, receipt, *, repository, feature_id, key):
    plan = plan_operation_start(
        backend.read_snapshot(),
        target_repository=repository,
        feature_id=feature_id,
        expected_revision=7,
        idempotency_key=key,
        occurred_at=NOW,
        trusted_context_digest="scope-fixture",
    )
    return backend.commit(plan, receipt).result["operation_id"]


def req(capability, *, target, context, adapter_id=ADAPTER_ID, payload=None):
    canonical_context = {key: value for key, value in context.items() if key != "idempotency_key"}
    operation_marker = canonical_context.get("operation_id", context.get("idempotency_key", "x"))
    body = {
        "api_version": API_VERSION,
        "request_id": f"scope-{capability.replace('.', '-')}-{operation_marker}",
        "capability": capability,
        "context": canonical_context,
        "client_identity": {"adapter_id": adapter_id},
        "payload": dict(payload or {}),
    }
    if target is not None:
        body["target"] = dict(target)
    if capability in WRITE_CAPABILITIES:
        body["idempotency_key"] = str(
            context.get("idempotency_key")
            or f"scope-{capability.replace('.', '-')}-{operation_marker}"
        )
    return body


def main():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-production-scope-") as td:
        root = Path(td)
        remote = root / "control.git"
        checkout = root / "checkout"
        git("init", "--bare", "-q", str(remote))
        git("clone", "-q", str(remote), str(checkout))
        git("config", "user.name", "ai-sdlc-test", cwd=checkout)
        git("config", "user.email", "ai-sdlc@example.invalid", cwd=checkout)

        backend = RemoteGitStateRefBackend(
            repo_path=checkout,
            repository=STORE,
            state_ref=STATE_REF,
        )
        verifier = FixtureVerifier()
        receipt = verifier.verify(STORE, STATE_REF)
        allowed_operation = seed(backend, receipt, repository=TARGET, feature_id=FEATURE, key="allowed-existing")
        allowed_cancel_operation = seed(
            backend,
            receipt,
            repository=TARGET,
            feature_id=FEATURE,
            key="allowed-cancel-existing",
        )
        forbidden_operation = seed(backend, receipt, repository=OTHER, feature_id="F-OTHER", key="forbidden-existing")

        config = TrustedOperatorRuntimeConfig(
            target_repository=TARGET,
            store_repository=STORE,
            installation_ref="main",
            store_checkout=checkout,
            principal="scope-principal",
            feature_bindings=(TrustedFeatureBinding(FEATURE, FEATURE_REF),),
            state_ref=STATE_REF,
        )
        github = FakeGitHubContents()
        bundle = build_trusted_operator_backend_bundle(
            config=config,
            adapter_id=ADAPTER_ID,
            target_read_token="target-token",
            store_token="store-token",
            github_api_base="https://api.github.test",
            reader_http_get=github,
            protection_verifier=verifier,
        )
        trusted = bundle.trusted_context_provider.for_request({"repository": TARGET, "feature_id": FEATURE})
        allowed_target = {"repository": TARGET, "feature_id": FEATURE}

        # Canonical operation.status is operation-id-bound. A client need not
        # restate target authority; production scope comes from durable truth.
        allowed_status = dispatch(
            req("operation.status", target=None, context={"operation_id": allowed_operation}),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(allowed_status["ok"] is True, allowed_status)

        # A target hint remains an optional narrowing assertion and may match.
        hinted_status = dispatch(
            req("operation.status", target=allowed_target, context={"operation_id": allowed_operation}),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(hinted_status["ok"] is True, hinted_status)

        # Targetless operation.cancel is equally authorized from the durable
        # projection + trusted scope, not from model/client target claims.
        allowed_cancel = dispatch(
            req(
                "operation.cancel",
                target=None,
                context={"operation_id": allowed_cancel_operation},
                payload={"reason": "targetless-authorized-cancel"},
            ),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(allowed_cancel["ok"] is True, allowed_cancel)
        require(
            rebuild_projection(backend.read_snapshot(), allowed_cancel_operation)["status"] == "CANCELLED",
            "targetless authorized cancel did not durably cancel allowed Operation",
        )

        # Knowing a foreign Operation id is insufficient. With no client target,
        # durable projection ownership still denies cross-scope reads/writes.
        targetless_foreign_status = dispatch(
            req("operation.status", target=None, context={"operation_id": forbidden_operation}),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(
            targetless_foreign_status["ok"] is False
            and targetless_foreign_status["error"]["code"] == "UNAUTHORIZED",
            targetless_foreign_status,
        )

        targetless_foreign_cancel = dispatch(
            req(
                "operation.cancel",
                target=None,
                context={"operation_id": forbidden_operation},
                payload={"reason": "foreign-id-only"},
            ),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(
            targetless_foreign_cancel["ok"] is False
            and targetless_foreign_cancel["error"]["code"] == "UNAUTHORIZED",
            targetless_foreign_cancel,
        )
        require(
            rebuild_projection(backend.read_snapshot(), forbidden_operation)["status"] == "RUNNING",
            "targetless unauthorized cancel mutated foreign durable Operation",
        )

        # If the client does supply an allowed-looking target while naming a
        # foreign Operation, durable truth remains authoritative.
        forged_status = dispatch(
            req("operation.status", target=allowed_target, context={"operation_id": forbidden_operation}),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(forged_status["ok"] is False and forged_status["error"]["code"] == "UNAUTHORIZED", forged_status)

        forged_cancel = dispatch(
            req(
                "operation.cancel",
                target=allowed_target,
                context={"operation_id": forbidden_operation},
                payload={"reason": "attacker-forged-target"},
            ),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(forged_cancel["ok"] is False and forged_cancel["error"]["code"] == "UNAUTHORIZED", forged_cancel)
        forbidden_projection = rebuild_projection(backend.read_snapshot(), forbidden_operation)
        require(forbidden_projection["status"] == "RUNNING", "unauthorized cancel mutated foreign durable Operation")

        # Exact Feature truth is re-read by the scoped start backend and injected
        # as server-owned feature_verification before delegating to Store logic.
        started = dispatch(
            req(
                "operation.start",
                target=allowed_target,
                context={"expected_feature_revision": 7, "idempotency_key": "scoped-new-start"},
            ),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(started["ok"] is True, started)
        new_projection = rebuild_projection(backend.read_snapshot(), started["result"]["operation_id"])
        require(new_projection["target_repository"] == "dream-xin/fixture", new_projection)
        require(new_projection["feature_id"] == FEATURE, new_projection)
        require(new_projection["expected_feature_revision"] == 7, new_projection)

        stale = dispatch(
            req(
                "operation.start",
                target=allowed_target,
                context={"expected_feature_revision": 6, "idempotency_key": "stale-start"},
            ),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(stale["ok"] is False and stale["error"]["code"] == "STALE_REVISION", stale)

        wrong_adapter = dispatch(
            req(
                "operation.status",
                target=None,
                context={"operation_id": allowed_operation},
                adapter_id="evil.adapter",
            ),
            trusted_context=trusted,
            backends=bundle.backends,
        )
        require(wrong_adapter["ok"] is False and wrong_adapter["error"]["code"] == "UNAUTHORIZED", wrong_adapter)

    print("Operator production scope validation passed")
    print("- targetless status/cancel authorize from durable Operation target + trusted scope")
    print("- targetless foreign Operation status/cancel fail closed")
    print("- unauthorized foreign Operation cancellation produced zero durable mutation")
    print("- supplied target is an optional narrowing hint and cannot override durable truth")
    print("- operation.start re-reads trusted exact Feature revision before Store write")
    print("- stale Feature revision and wrong adapter fail closed")


if __name__ == "__main__":
    main()
