#!/usr/bin/env python3
"""Production provenance verifier for v0.3 real release dogfood.

A release-run YAML record is never authority for its own provenance.  This
verifier independently resolves GitHub PR/workflow truth and requires trusted
runtime/Store resolvers to re-establish receipt and milestone evidence before
returning a DogfoodAttestation accepted by validate_v03_dogfood_evidence.py.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from operator_store_model import normalize_repository
from v03_dogfood_trusted_provenance import (
    DogfoodAttestation,
    DogfoodProvenanceVerificationError,
    VerifiedWorkflowRun,
    canonical_record_digest,
)

HttpGet = Callable[[str, Mapping[str, str]], tuple[int, Any]]
RuntimeReceiptResolver = Callable[[Mapping[str, Any]], Mapping[str, Any]]
MilestoneResolver = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _default_get(url: str, headers: Mapping[str, str]) -> tuple[int, Any]:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"message": str(exc)}
        return exc.code, payload
    except (URLError, TimeoutError, OSError) as exc:
        raise DogfoodProvenanceVerificationError(f"GitHub provenance lookup failed: {exc}") from exc


def _sha40(value: Any, label: str) -> str:
    value = str(value or "").strip().lower()
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise DogfoodProvenanceVerificationError(f"{label} is not an exact Git SHA")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise DogfoodProvenanceVerificationError(f"{label} is not a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DogfoodProvenanceVerificationError(f"{label} is not a positive integer") from exc
    if result < 1:
        raise DogfoodProvenanceVerificationError(f"{label} is not a positive integer")
    return result


@dataclass(frozen=True)
class ProductionDogfoodProvenanceConfig:
    repository: str
    verifier_identity: str
    supported_adapter_id: str
    runtime_kind: str
    github_token: str
    github_api_base: str = "https://api.github.com"

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", normalize_repository(self.repository))
        if not all((self.verifier_identity, self.supported_adapter_id, self.runtime_kind, self.github_token)):
            raise ValueError("production dogfood provenance configuration is incomplete")
        if not str(self.github_api_base).startswith("https://"):
            raise ValueError("production dogfood provenance GitHub API must use HTTPS")


class ProductionDogfoodProvenanceVerifier:
    """Closed production verifier over external and durable dogfood evidence."""

    test_only = False

    def __init__(
        self,
        *,
        config: ProductionDogfoodProvenanceConfig,
        runtime_receipt_resolver: RuntimeReceiptResolver,
        milestone_resolver: MilestoneResolver,
        http_get: HttpGet = _default_get,
    ) -> None:
        if not callable(runtime_receipt_resolver) or not callable(milestone_resolver) or not callable(http_get):
            raise ValueError("production dogfood provenance resolvers must be callable")
        self.config = config
        self._runtime_receipt_resolver = runtime_receipt_resolver
        self._milestone_resolver = milestone_resolver
        self._http_get = http_get

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.config.github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str) -> Any:
        status, payload = self._http_get(self.config.github_api_base.rstrip("/") + path, self._headers)
        if status != 200:
            raise DogfoodProvenanceVerificationError(f"GitHub provenance lookup returned HTTP {status}")
        return payload

    def _verify_candidate(self, record: Mapping[str, Any]) -> tuple[int | None, str | None]:
        candidate = record.get("candidate") or {}
        pr_number = candidate.get("pr_number")
        head_sha = candidate.get("head_sha")
        if pr_number is None and head_sha is None:
            return None, None
        if pr_number is None or head_sha is None:
            raise DogfoodProvenanceVerificationError("candidate PR/head authority is incomplete")
        pr_number = _positive_int(pr_number, "candidate PR number")
        head_sha = _sha40(head_sha, "candidate head")
        payload = self._get(f"/repos/{self.config.repository}/pulls/{pr_number}")
        if not isinstance(payload, dict):
            raise DogfoodProvenanceVerificationError("candidate PR response is malformed")
        if payload.get("state") != "open" or payload.get("draft") is not False:
            raise DogfoodProvenanceVerificationError("candidate PR is not one open non-draft authority")
        head = payload.get("head") or {}
        base = payload.get("base") or {}
        head_repo = normalize_repository(((head.get("repo") or {}).get("full_name") or ""))
        base_repo = normalize_repository(((base.get("repo") or {}).get("full_name") or ""))
        if head_repo != self.config.repository or base_repo != self.config.repository:
            raise DogfoodProvenanceVerificationError("candidate PR escaped trusted repository")
        if str(base.get("ref") or "") != "main":
            raise DogfoodProvenanceVerificationError("candidate PR is not based on trusted main")
        if _sha40(head.get("sha"), "GitHub candidate head") != head_sha:
            raise DogfoodProvenanceVerificationError("candidate head changed after dogfood evidence was recorded")
        return pr_number, head_sha

    def _verify_runs(self, record: Mapping[str, Any], candidate_head: str | None) -> tuple[VerifiedWorkflowRun, ...]:
        runtime = record.get("runtime") or {}
        declared = runtime.get("workflow_run_ids") or []
        run_ids = tuple(_positive_int(value, "workflow run id") for value in declared)
        if not run_ids or len(set(run_ids)) != len(run_ids):
            raise DogfoodProvenanceVerificationError("release dogfood requires a non-empty unique workflow run set")
        verified: list[VerifiedWorkflowRun] = []
        for run_id in sorted(run_ids):
            payload = self._get(f"/repos/{self.config.repository}/actions/runs/{run_id}")
            if not isinstance(payload, dict):
                raise DogfoodProvenanceVerificationError(f"workflow run {run_id} response is malformed")
            run_repo = normalize_repository(((payload.get("repository") or {}).get("full_name") or ""))
            if run_repo != self.config.repository:
                raise DogfoodProvenanceVerificationError(f"workflow run {run_id} escaped trusted repository")
            if payload.get("event") != "workflow_dispatch" or str(payload.get("conclusion") or "").lower() != "success":
                raise DogfoodProvenanceVerificationError(f"workflow run {run_id} is not a successful trusted dispatch")
            head_sha = _sha40(payload.get("head_sha"), f"workflow run {run_id} head")
            if candidate_head is not None and head_sha != candidate_head:
                raise DogfoodProvenanceVerificationError(f"workflow run {run_id} candidate head mismatch")
            verified.append(VerifiedWorkflowRun(run_id, str(record.get("repository")), "success", head_sha))
        return tuple(verified)

    def _verify_runtime_receipt(self, record: Mapping[str, Any], run_ids: tuple[int, ...]) -> str:
        resolved = self._runtime_receipt_resolver(record)
        if not isinstance(resolved, Mapping):
            raise DogfoodProvenanceVerificationError("trusted runtime receipt resolver returned malformed evidence")
        receipt_identity = str(resolved.get("receipt_identity") or "")
        if not receipt_identity:
            raise DogfoodProvenanceVerificationError("trusted runtime receipt resolver found no receipt identity")
        resolved_runs = tuple(sorted(_positive_int(value, "resolved workflow run id") for value in (resolved.get("workflow_run_ids") or [])))
        if resolved_runs != tuple(sorted(run_ids)):
            raise DogfoodProvenanceVerificationError("runtime receipt is not bound to the declared workflow run set")
        declared_receipt = str(((record.get("runtime") or {}).get("receipt_identity")) or "")
        if receipt_identity != declared_receipt:
            raise DogfoodProvenanceVerificationError("trusted runtime receipt identity mismatch")
        return receipt_identity

    def _verify_milestones(self, record: Mapping[str, Any]) -> Mapping[str, frozenset[str]]:
        resolved = self._milestone_resolver(record)
        if not isinstance(resolved, Mapping):
            raise DogfoodProvenanceVerificationError("trusted milestone resolver returned malformed evidence")
        declared = {
            str(row.get("name")): frozenset(str(value) for value in (row.get("evidence_categories") or []))
            for row in (record.get("milestones") or [])
            if isinstance(row, Mapping) and row.get("name")
        }
        observed = {str(name): frozenset(str(value) for value in (categories or [])) for name, categories in resolved.items()}
        if observed != declared:
            raise DogfoodProvenanceVerificationError("trusted milestone evidence categories do not match release record")
        return observed

    def verify(self, record: Mapping[str, Any]) -> DogfoodAttestation:
        if not isinstance(record, Mapping):
            raise DogfoodProvenanceVerificationError("dogfood release record is malformed")
        repository = str(record.get("repository") or "")
        if normalize_repository(repository) != self.config.repository:
            raise DogfoodProvenanceVerificationError("dogfood record targets another repository")
        adapter_id = str(((record.get("adapter") or {}).get("adapter_id")) or "")
        if adapter_id != self.config.supported_adapter_id:
            raise DogfoodProvenanceVerificationError("dogfood record does not use the configured supported adapter")
        runtime_kind = str(((record.get("runtime") or {}).get("runtime_kind")) or "")
        if runtime_kind != self.config.runtime_kind:
            raise DogfoodProvenanceVerificationError("dogfood record does not use the configured production runtime")

        pr_number, candidate_head = self._verify_candidate(record)
        runs = self._verify_runs(record, candidate_head)
        receipt_identity = self._verify_runtime_receipt(record, tuple(run.run_id for run in runs))
        milestones = self._verify_milestones(record)
        provenance = record.get("provenance") or {}
        if provenance.get("verifier_identity") != self.config.verifier_identity:
            raise DogfoodProvenanceVerificationError("record verifier identity differs from configured trusted verifier")

        return DogfoodAttestation(
            verifier_identity=self.config.verifier_identity,
            record_digest=canonical_record_digest(record),
            repository=repository,
            candidate_pr_number=pr_number,
            candidate_head_sha=candidate_head,
            adapter_id=adapter_id,
            runtime_kind=runtime_kind,
            receipt_identity=receipt_identity,
            workflow_runs=runs,
            milestone_evidence_categories=milestones,
        )
