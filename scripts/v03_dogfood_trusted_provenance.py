#!/usr/bin/env python3
"""Trusted provenance contract for v0.3 release dogfood evidence.

This module deliberately separates local evidence-shape validation from the
trusted act of proving that external release evidence is real. A release PASS
cannot verify itself by embedding plausible GitHub URLs or booleans in YAML.

A trusted control/release integration must independently resolve the cited
GitHub workflow runs and runtime receipt, then return a `DogfoodAttestation`
bound to the canonical record digest. Deterministic tests may inject a fake
verifier to exercise this boundary, but the normal repository validator never
promotes that fake result to release evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Protocol


class DogfoodProvenanceVerificationError(RuntimeError):
    """Trusted provenance could not be positively established."""


def canonical_record_digest(record: Mapping) -> str:
    """Return a stable digest for the complete release evidence record."""
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class VerifiedWorkflowRun:
    run_id: int
    repository: str
    conclusion: str
    head_sha: str | None


@dataclass(frozen=True)
class DogfoodAttestation:
    """Claims returned only after an independent trusted verification step."""

    verifier_identity: str
    record_digest: str
    repository: str
    candidate_pr_number: int | None
    candidate_head_sha: str | None
    adapter_id: str
    runtime_kind: str
    receipt_identity: str
    workflow_runs: tuple[VerifiedWorkflowRun, ...]
    milestone_evidence_categories: Mapping[str, frozenset[str]]


class TrustedDogfoodProvenanceVerifier(Protocol):
    """Release/install authority capable of resolving external evidence.

    Implementations must independently verify, rather than trust fields from
    the record, at least:

    - target repository;
    - every cited GitHub Actions workflow run exists and concluded success;
    - candidate PR/head binding when candidate-bound;
    - supported adapter/runtime identity;
    - runtime receipt identity/correlation;
    - milestone evidence categories claimed by the record.

    Failure or uncertainty must raise `DogfoodProvenanceVerificationError`.
    """

    test_only: bool

    def verify(self, record: Mapping) -> DogfoodAttestation:
        ...
