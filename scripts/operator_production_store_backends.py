#!/usr/bin/env python3
"""Trusted target-scoping wrappers for Store-backed canonical capabilities."""
from __future__ import annotations

from typing import Any

from operator_decision_backends import TrustedOperatorScope
from operator_store_backends import OperationCancelBackend, OperationStartBackend, OperationStatusBackend
from operator_store_model import StoreInvariantError, normalize_repository, rebuild_projection
from operator_production_runtime import OperatorProductionRuntimeError, TrustedOperatorRuntimeConfig, GitHubTrustedProjectFeatureReader


class _ScopedStoreBackend:
    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str, delegate: Any, runtime: Any):
        self.config = config
        self.adapter_id = adapter_id
        self.delegate = delegate
        self.runtime = runtime

    def availability(self, capability: str, trusted_context: dict[str, Any]):
        try:
            scope = TrustedOperatorScope.from_context(trusted_context)
            if scope.client_adapter_id != self.adapter_id:
                return False, "POLICY_RESTRICTED"
            if self.config.target_repository not in scope.repositories:
                return False, "POLICY_RESTRICTED"
        except Exception:
            return False, "POLICY_RESTRICTED"
        return self.delegate.availability(capability, trusted_context)

    def _scope(self, request: dict[str, Any], trusted_context: dict[str, Any]) -> TrustedOperatorScope:
        scope = TrustedOperatorScope.from_context(trusted_context)
        declared = str((request.get("client_identity") or {}).get("adapter_id") or "")
        if declared != self.adapter_id or declared != scope.client_adapter_id:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "adapter identity does not match trusted runtime")
        return scope

    def _target(self, request: dict[str, Any]) -> tuple[str, str | None]:
        target = request.get("target") or {}
        repository = normalize_repository(str(target.get("repository") or ""))
        feature_id = target.get("feature_id")
        return repository, str(feature_id) if feature_id is not None else None

    def _operation_projection(self, operation_id: str) -> dict[str, Any]:
        try:
            return rebuild_projection(self.runtime.backend.read_snapshot(), operation_id)
        except StoreInvariantError as exc:
            raise OperatorProductionRuntimeError("INVALID_REQUEST", str(exc)) from exc

    def _authorize_projection(
        self,
        *,
        request: dict[str, Any],
        trusted_context: dict[str, Any],
        projection: dict[str, Any],
    ) -> None:
        scope = self._scope(request, trusted_context)
        durable_repository = normalize_repository(str(projection.get("target_repository") or ""))
        durable_feature_id = str(projection.get("feature_id") or "")
        if not scope.allows(durable_repository, durable_feature_id):
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "Operation target is outside trusted runtime scope")
        if durable_repository != self.config.target_repository:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "Operation target repository is outside trusted runtime configuration")
        request_repository, request_feature_id = self._target(request)
        if request_repository != durable_repository:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "request target does not match durable Operation repository")
        if request_feature_id is not None and request_feature_id != durable_feature_id:
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "request target does not match durable Operation Feature")


class ScopedOperationStartBackend(_ScopedStoreBackend):
    """Bind operation.start to freshly read trusted Feature truth."""

    def __init__(
        self,
        *,
        config: TrustedOperatorRuntimeConfig,
        adapter_id: str,
        runtime: Any,
        reader: GitHubTrustedProjectFeatureReader,
        operation_profile: str | None = None,
    ):
        super().__init__(
            config=config,
            adapter_id=adapter_id,
            delegate=OperationStartBackend(runtime, operation_profile=operation_profile),
            runtime=runtime,
        )
        self.reader = reader

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        scope = self._scope(request, trusted_context)
        repository, feature_id = self._target(request)
        if not feature_id or repository != self.config.target_repository or not scope.allows(repository, feature_id):
            raise OperatorProductionRuntimeError("UNAUTHORIZED", "Feature is outside trusted runtime scope")

        manifest = self.reader.feature_manifest(feature_id)
        revision = int(manifest.get("revision", -1))
        expected = (request.get("context") or {}).get("expected_feature_revision")
        if expected != revision:
            raise OperatorProductionRuntimeError("STALE_REVISION", "trusted Feature revision no longer matches request")

        verified = dict(trusted_context)
        verified["feature_verification"] = {
            "repository": repository,
            "feature_id": feature_id,
            "revision": revision,
            "target_ref": self.config.feature_ref(feature_id),
        }
        return self.delegate.invoke(request, verified)


class ScopedOperationStatusBackend(_ScopedStoreBackend):
    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str, runtime: Any):
        super().__init__(
            config=config,
            adapter_id=adapter_id,
            delegate=OperationStatusBackend(runtime),
            runtime=runtime,
        )

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        operation_id = str((request.get("context") or {}).get("operation_id") or "")
        if not operation_id:
            raise OperatorProductionRuntimeError("INVALID_REQUEST", "context.operation_id is required")
        projection = self._operation_projection(operation_id)
        self._authorize_projection(request=request, trusted_context=trusted_context, projection=projection)
        return self.delegate.invoke(request, trusted_context)


class ScopedOperationCancelBackend(_ScopedStoreBackend):
    def __init__(self, *, config: TrustedOperatorRuntimeConfig, adapter_id: str, runtime: Any):
        super().__init__(
            config=config,
            adapter_id=adapter_id,
            delegate=OperationCancelBackend(runtime),
            runtime=runtime,
        )

    def invoke(self, request: dict[str, Any], trusted_context: dict[str, Any]):
        operation_id = str((request.get("context") or {}).get("operation_id") or "")
        if not operation_id:
            raise OperatorProductionRuntimeError("INVALID_REQUEST", "context.operation_id is required")
        projection = self._operation_projection(operation_id)
        self._authorize_projection(request=request, trusted_context=trusted_context, projection=projection)
        return self.delegate.invoke(request, trusted_context)


def scoped_store_backends(
    *,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    runtime: Any,
    reader: GitHubTrustedProjectFeatureReader,
    operation_profile: str | None = None,
) -> dict[str, Any]:
    return {
        "operation.start": ScopedOperationStartBackend(
            config=config,
            adapter_id=adapter_id,
            runtime=runtime,
            reader=reader,
            operation_profile=operation_profile,
        ),
        "operation.status": ScopedOperationStatusBackend(
            config=config,
            adapter_id=adapter_id,
            runtime=runtime,
        ),
        "operation.cancel": ScopedOperationCancelBackend(
            config=config,
            adapter_id=adapter_id,
            runtime=runtime,
        ),
    }
