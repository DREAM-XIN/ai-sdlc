#!/usr/bin/env python3
"""Trusted protection boundary for Operator Store state refs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

PROTECTED = "PROTECTED"
UNPROTECTED = "UNPROTECTED"
UNKNOWN = "UNKNOWN"
VALID_PROTECTION_STATES = frozenset({PROTECTED, UNPROTECTED, UNKNOWN})


class ProtectionError(PermissionError):
    pass


@dataclass(frozen=True)
class ProtectionReceipt:
    repository: str
    state_ref: str
    status: str
    verifier_identity: str
    verified_at: str
    policy_digest: str | None = None

    def validate_for(self, repository: str, state_ref: str) -> None:
        if self.status not in VALID_PROTECTION_STATES:
            raise ProtectionError("invalid protection receipt status")
        if self.repository.lower() != repository.lower() or self.state_ref != state_ref:
            raise ProtectionError("protection receipt binding mismatch")
        if self.status != PROTECTED:
            raise ProtectionError("operator state ref is not positively verified protected")
        if not self.verifier_identity or not self.verified_at:
            raise ProtectionError("protection receipt is incomplete")


class StateRefProtectionVerifier(Protocol):
    def verify(self, repository: str, state_ref: str) -> ProtectionReceipt:
        ...


class StaticProtectionVerifier:
    """Trusted test/control verifier; never construct it from client/Worker payload."""
    def __init__(self, *, status: str, verifier_identity: str = "trusted-test-verifier", policy_digest: str | None = "test-policy"):
        if status not in VALID_PROTECTION_STATES:
            raise ValueError("invalid protection state")
        self.status = status
        self.verifier_identity = verifier_identity
        self.policy_digest = policy_digest

    def verify(self, repository: str, state_ref: str) -> ProtectionReceipt:
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=self.status,
            verifier_identity=self.verifier_identity,
            verified_at="trusted-verification",
            policy_digest=self.policy_digest,
        )


def require_protected(receipt: ProtectionReceipt | None, *, repository: str, state_ref: str) -> None:
    if receipt is None:
        raise ProtectionError("missing trusted protection receipt")
    receipt.validate_for(repository, state_ref)


def semantic_bootstrap_allowed(receipt: ProtectionReceipt | None, *, repository: str, state_ref: str) -> bool:
    """Semantic state is disabled until protection is positively verified."""
    try:
        require_protected(receipt, repository=repository, state_ref=state_ref)
    except ProtectionError:
        return False
    return True
