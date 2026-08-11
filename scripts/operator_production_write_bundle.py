#!/usr/bin/env python3
"""Explicit write-capable extension for trusted Operator production bundles.

The base production bundle intentionally exposes only the Store-backed operation
writes required for canonical composition. Decision/Notification writes require
additional trusted policy and Feature-truth dependencies and are added only by
this explicit factory. AI-client transports still decide which canonical
capabilities they register/expose.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from operator_decision_backends import decision_notification_backends
from operator_production_runtime import TrustedOperatorReadBundle

REQUIRED_V03_WRITE_SLICE = frozenset(
    {
        "operation.start",
        "operation.cancel",
        "decision.respond",
        "notification.ack",
    }
)


@dataclass(frozen=True)
class TrustedOperatorWriteBundle:
    read_bundle: TrustedOperatorReadBundle
    backends: dict[str, Any]
    decision_notification_coordinator: Any

    @property
    def config(self):
        return self.read_bundle.config

    @property
    def trusted_context_provider(self):
        return self.read_bundle.trusted_context_provider

    @property
    def runtime(self):
        return self.read_bundle.runtime


def extend_with_trusted_decision_writes(
    read_bundle: TrustedOperatorReadBundle,
    *,
    policy_verifier: Any,
    feature_gateway: Any,
    trusted_context_digest: str,
) -> TrustedOperatorWriteBundle:
    """Add the approved Decision/Notification writes using existing authority.

    No default/fallback policy verifier or Feature gateway is constructed here.
    Missing trusted dependencies are a startup/configuration error rather than a
    reason to silently expose weaker write semantics.
    """
    if policy_verifier is None:
        raise ValueError("trusted Decision policy verifier is required for write bundle")
    if feature_gateway is None:
        raise ValueError("trusted Feature truth gateway is required for write bundle")
    if not trusted_context_digest:
        raise ValueError("trusted context digest is required for write bundle")

    decision_backends, coordinator = decision_notification_backends(
        read_bundle.runtime,
        policy_verifier=policy_verifier,
        feature_gateway=feature_gateway,
        trusted_context_digest=trusted_context_digest,
    )
    combined = dict(read_bundle.backends)
    combined.update(decision_backends)

    missing = REQUIRED_V03_WRITE_SLICE - set(combined)
    if missing:
        raise RuntimeError(f"trusted write bundle is missing required v0.3 writes: {sorted(missing)}")

    return TrustedOperatorWriteBundle(
        read_bundle=read_bundle,
        backends=combined,
        decision_notification_coordinator=coordinator,
    )
