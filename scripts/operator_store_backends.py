#!/usr/bin/env python3
"""Canonical ai-sdlc.operator/v1 backends over the durable Operator Store."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from operator_store import StoreCommandError, plan_cancel, plan_operation_start
from operator_store_git import CasConflict
from operator_store_model import StoreInvariantError, projection_public, rebuild_projection
from operator_store_protection import ProtectionError, StateRefProtectionVerifier


class StoreBackendError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class OperatorStoreRuntime:
    def __init__(
        self,
        *,
        backend,
        protection_verifier: StateRefProtectionVerifier,
        clock: Callable[[], str] = _utc_now,
        plan_guard=None,
    ):
        self.backend = backend
        self.protection_verifier = protection_verifier
        self.clock = clock
        self.plan_guard = plan_guard

    def protected_receipt(self):
        return self.protection_verifier.verify(self.backend.repository, self.backend.state_ref)

    def _fresh_protected_receipt(self):
        try:
            receipt = self.protected_receipt()
            receipt.validate_for(self.backend.repository, self.backend.state_ref)
            return receipt
        except ProtectionError:
            raise
        except Exception as exc:
            raise ProtectionError("trusted protection verification failed") from exc

    @staticmethod
    def _protection_authority(receipt):
        return receipt.verifier_identity, receipt.policy_digest

    def commit_replanned(self, planner, *, max_attempts: int = 4):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        baseline_authority = None
        last_conflict = None

        try:
            for _ in range(max_attempts):
                # Protection authority is refreshed for every CAS attempt. A conflict
                # never reuses the receipt that authorized the previous attempt.
                receipt = self._fresh_protected_receipt()
                authority = self._protection_authority(receipt)
                if baseline_authority is None:
                    baseline_authority = authority
                elif authority != baseline_authority:
                    raise ProtectionError("operator Store protection authority changed during CAS retry")

                snapshot = self.backend.read_snapshot()
                plan = planner(snapshot)
                if self.plan_guard is not None:
                    self.plan_guard(snapshot, plan)

                try:
                    # Exactly one write attempt uses this freshly verified receipt.
                    # The runtime, rather than the backend retry helper, owns retry so
                    # the next attempt must re-enter trusted protection verification.
                    return self.backend.commit(plan, receipt)
                except CasConflict as exc:
                    last_conflict = exc

            raise CasConflict("operator state ref CAS retries exhausted") from last_conflict
        except ProtectionError as exc:
            raise StoreBackendError("POLICY_DENIED", str(exc)) from exc
        except CasConflict as exc:
            raise StoreBackendError("TRANSIENT_FAILURE", str(exc)) from exc
        except StoreCommandError as exc:
            raise StoreBackendError(exc.code, str(exc)) from exc
        except StoreInvariantError as exc:
            raise StoreBackendError("INTERNAL_FAILURE", str(exc)) from exc


def _target(request):
    target = request.get("target") or {}
    repository = target.get("repository")
    feature_id = target.get("feature_id")
    if not repository or not feature_id:
        raise StoreBackendError("INVALID_REQUEST", "target.repository and target.feature_id are required")
    return repository, feature_id


def _verified_feature(request, trusted_context):
    repository, feature_id = _target(request)
    context = request.get("context") or {}
    expected = context.get("expected_feature_revision")
    receipt = trusted_context.get("feature_verification")
    if not isinstance(receipt, dict):
        raise StoreBackendError("UNAUTHORIZED", "trusted feature verification is required")
    if str(receipt.get("repository", "")).lower() != repository.lower() or receipt.get("feature_id") != feature_id:
        raise StoreBackendError("UNAUTHORIZED", "trusted feature verification binding mismatch")
    if receipt.get("revision") != expected:
        raise StoreBackendError("STALE_REVISION", "trusted Feature revision no longer matches request")
    return repository, feature_id, expected


class OperationStartBackend:
    def __init__(self, runtime: OperatorStoreRuntime, *, operation_profile: str | None = None):
        self.runtime = runtime
        self.operation_profile = operation_profile

    def availability(self, capability, trusted_context):
        try:
            receipt = self.runtime.protected_receipt()
            receipt.validate_for(self.runtime.backend.repository, self.runtime.backend.state_ref)
        except Exception:
            return False, "POLICY_RESTRICTED"
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        repository, feature_id, expected_revision = _verified_feature(request, trusted_context)
        idempotency_key = request["idempotency_key"]
        occurred_at = self.runtime.clock()
        trusted_digest = str(trusted_context.get("trusted_context_digest") or "trusted-runtime")
        result = self.runtime.commit_replanned(
            lambda snapshot: plan_operation_start(
                snapshot,
                target_repository=repository,
                feature_id=feature_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
                trusted_context_digest=trusted_digest,
                operation_profile=self.operation_profile,
            )
        )
        return dict(result.result)


class OperationStatusBackend:
    def __init__(self, runtime: OperatorStoreRuntime):
        self.runtime = runtime

    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        operation_id = (request.get("context") or {}).get("operation_id")
        if not operation_id:
            raise StoreBackendError("INVALID_REQUEST", "context.operation_id is required")
        try:
            return projection_public(rebuild_projection(self.runtime.backend.read_snapshot(), operation_id))
        except StoreInvariantError as exc:
            raise StoreBackendError("INVALID_REQUEST", str(exc)) from exc


class OperationCancelBackend:
    def __init__(self, runtime: OperatorStoreRuntime):
        self.runtime = runtime

    def availability(self, capability, trusted_context):
        try:
            receipt = self.runtime.protected_receipt()
            receipt.validate_for(self.runtime.backend.repository, self.runtime.backend.state_ref)
        except Exception:
            return False, "POLICY_RESTRICTED"
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        operation_id = (request.get("context") or {}).get("operation_id")
        if not operation_id:
            raise StoreBackendError("INVALID_REQUEST", "context.operation_id is required")
        reason = (request.get("payload") or {}).get("reason", "")
        occurred_at = self.runtime.clock()
        trusted_digest = str(trusted_context.get("trusted_context_digest") or "trusted-runtime")
        result = self.runtime.commit_replanned(
            lambda snapshot: plan_cancel(
                snapshot,
                operation_id=operation_id,
                reason=reason,
                occurred_at=occurred_at,
                trusted_context_digest=trusted_digest,
            )
        )
        public = dict(result.result)
        return {"operation_id": public["operation_id"], "status": public["status"]}


def store_backends(
    runtime: OperatorStoreRuntime,
    *,
    operation_profile: str | None = None,
    resume_backend=None,
):
    """Compose only capabilities backed by trusted runtime dependencies."""
    backends = {
        "operation.start": OperationStartBackend(runtime, operation_profile=operation_profile),
        "operation.status": OperationStatusBackend(runtime),
        "operation.cancel": OperationCancelBackend(runtime),
    }
    if resume_backend is not None:
        backends["operation.resume"] = resume_backend
    return backends
