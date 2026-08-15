#!/usr/bin/env python3
"""Digest-bound production content layer for the Operation-bound gh-aw collector.

The base GitHub source resolves trusted run/PR/comment identity. This layer closes
the resolve->receipt TOCTOU window by binding every collector-owned URI to the
canonical content/provenance observed immediately after resolution. Fresh content
loading must reproduce that exact binding before bytes are admitted to a receipt.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

from operator_store_model import canonical_json, digest_json, normalize_repository
from operator_vertical import VerticalInvariantError
from operator_vertical_callback import TrustedVerticalCallbackCoordinator
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_collector import (
    MaterializedGhAwOutput,
    TrustedGhAwResolvedResult,
)
from operator_vertical_gh_aw_github_source import (
    GitHubActionsGhAwResultSourceConfig,
    ProductionGhAwVerticalResultCollector,
    TargetScopedGitHubActionsGhAwResultSource,
)

_BASE_PR_RE = re.compile(
    r"^(?P<prefix>docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/)"
    r"developer-pr-(?P<number>[1-9][0-9]*)-(?P<head>[0-9a-f]{40})\.json$"
)
_BASE_GATE_RE = re.compile(
    r"^(?P<prefix>docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/)"
    r"(?P<role>reviewer|qa)-comment-(?P<comment>[1-9][0-9]*)\.json$"
)
_BOUND_PR_RE = re.compile(
    r"^(?P<prefix>docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/)"
    r"developer-pr-(?P<number>[1-9][0-9]*)-(?P<head>[0-9a-f]{40})-binding-(?P<binding>[0-9a-f]{64})\.json$"
)
_BOUND_GATE_RE = re.compile(
    r"^(?P<prefix>docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/)"
    r"(?P<role>reviewer|qa)-comment-(?P<comment>[1-9][0-9]*)-binding-(?P<binding>[0-9a-f]{64})\.json$"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DigestBoundTargetScopedGitHubActionsGhAwResultSource(
    TargetScopedGitHubActionsGhAwResultSource
):
    """Production source whose materialized URIs are immutable observation receipts."""

    def _developer_content_for_target(self, pr: dict[str, Any]) -> bytes:
        return TargetScopedGitHubActionsGhAwResultSource._canonical_pr_bytes(
            self.target_repository, pr
        )

    def _developer_binding_material(self, pr: dict[str, Any], content: bytes) -> dict[str, Any]:
        return {
            "kind": "developer-pr",
            "repository": self.target_repository,
            "pr_number": int(pr.get("number") or 0),
            "pr_url": str(pr.get("html_url") or ""),
            "state": str(pr.get("state") or ""),
            "draft": pr.get("draft"),
            "base_ref": str((pr.get("base") or {}).get("ref") or ""),
            "head_ref": str((pr.get("head") or {}).get("ref") or ""),
            "head_sha": str((pr.get("head") or {}).get("sha") or ""),
            "content_sha256": _sha256(content),
        }

    def _gate_content_and_binding(
        self, *, comment: dict[str, Any], role: str
    ) -> tuple[bytes, dict[str, Any]]:
        payload = self._gate_payload(str(comment.get("body") or ""))
        content = (canonical_json(payload) + "\n").encode("utf-8")
        material = {
            "kind": "gate-comment",
            "repository": self.target_repository,
            "role": role,
            "comment_id": int(comment.get("id") or 0),
            "comment_url": str(comment.get("html_url") or ""),
            "issue_url": str(comment.get("issue_url") or ""),
            "author_type": str((comment.get("user") or {}).get("type") or ""),
            "content_sha256": _sha256(content),
        }
        return content, material

    @staticmethod
    def _binding_digest(material: dict[str, Any]) -> str:
        return digest_json(material)

    def _revalidate_developer(
        self,
        *,
        resolved: TrustedGhAwResolvedResult,
        trusted_context: dict[str, Any],
        base_uri: str,
    ) -> MaterializedGhAwOutput:
        match = _BASE_PR_RE.fullmatch(base_uri)
        if not match:
            raise VerticalInvariantError("INTERNAL_FAILURE", "Developer output URI is not base collector form")
        number = int(match.group("number"))
        head = match.group("head")
        if resolved.run.candidate_pr_number != number or resolved.run.candidate_head_sha != head:
            raise VerticalInvariantError("STALE_REVISION", "Developer output URI/run candidate mismatch")
        pr = self._json(self.target_repository, f"/pulls/{number}", self.config.target_token)
        expected_url = f"https://github.com/{self.target_repository}/pull/{number}"
        expected_prefix = (
            f"gh-aw/{trusted_context['feature_id']}-{resolved.run.run_id}"
            f"-v{trusted_context['expected_revision']}"
        )
        if (
            not isinstance(pr, dict)
            or int(pr.get("number") or 0) != number
            or str(pr.get("html_url") or "").lower() != expected_url.lower()
            or str(pr.get("state") or "") != "open"
            or pr.get("draft") is not True
            or str((pr.get("base") or {}).get("ref") or "") != str(trusted_context["target_ref"])
            or str((pr.get("head") or {}).get("sha") or "") != head
            or not str((pr.get("head") or {}).get("ref") or "").startswith(expected_prefix)
        ):
            raise VerticalInvariantError(
                "STALE_REVISION",
                "Developer PR changed after trusted result resolution",
            )
        content = self._developer_content_for_target(pr)
        material = self._developer_binding_material(pr, content)
        binding = self._binding_digest(material)
        return MaterializedGhAwOutput(
            label="implementation",
            kind="artifact",
            media_type="application/json",
            trusted_uri=(
                f"{match.group('prefix')}developer-pr-{number}-{head}"
                f"-binding-{binding}.json"
            ),
        )

    def _revalidate_gate(
        self,
        *,
        resolved: TrustedGhAwResolvedResult,
        trusted_context: dict[str, Any],
        base_uri: str,
    ) -> MaterializedGhAwOutput:
        match = _BASE_GATE_RE.fullmatch(base_uri)
        if not match:
            raise VerticalInvariantError("INTERNAL_FAILURE", "Gate output URI is not base collector form")
        role = match.group("role")
        comment_id = int(match.group("comment"))
        if role != resolved.run.role:
            raise VerticalInvariantError("STALE_REVISION", "Gate output role changed")
        number = resolved.run.candidate_pr_number
        head = resolved.run.candidate_head_sha
        if not isinstance(number, int) or number <= 0 or not head:
            raise VerticalInvariantError("STALE_REVISION", "Gate output lacks exact candidate")
        comment = self._json(
            self.target_repository,
            f"/issues/comments/{comment_id}",
            self.config.target_token,
        )
        expected_comment_url = (
            f"https://github.com/{self.target_repository}/pull/{number}"
            f"#issuecomment-{comment_id}"
        )
        expected_issue_url = (
            f"{self.config.api_url.rstrip('/')}/repos/{self.target_repository}"
            f"/issues/{number}"
        )
        if (
            not isinstance(comment, dict)
            or int(comment.get("id") or 0) != comment_id
            or str(comment.get("html_url") or "").lower() != expected_comment_url.lower()
            or str(comment.get("issue_url") or "").lower() != expected_issue_url.lower()
            or str((comment.get("user") or {}).get("type") or "") != "Bot"
        ):
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "Gate comment provenance changed after trusted result resolution",
            )
        external = self._gate_payload(str(comment.get("body") or ""))
        self._schema_validate(external, role)
        expected = {
            "contract": (
                "ai-sdlc-gh-aw-reviewer-result-v0.1"
                if role == "reviewer"
                else "ai-sdlc-gh-aw-qa-result-v0.1"
            ),
            "feature_id": str(trusted_context["feature_id"]),
            "task_id": resolved.run.task_id,
            "expected_revision": int(trusted_context["expected_revision"]),
            "target_repository": self.target_repository,
            "target_ref": str(trusted_context["target_ref"]),
            "stage": str(trusted_context["feature_stage"]),
            "role": role,
            "candidate_pr_number": number,
            "candidate_head_sha": head,
        }
        for field, value in expected.items():
            self._eq(external.get(field), value, field)
        normalized = (
            self._reviewer_payload(external)
            if role == "reviewer"
            else self._qa_payload(external)
        )
        if canonical_json(normalized) != canonical_json(resolved.role_payload):
            raise VerticalInvariantError(
                "STALE_REVISION",
                "Gate role payload changed after trusted result resolution",
            )
        _content, material = self._gate_content_and_binding(comment=comment, role=role)
        binding = self._binding_digest(material)
        label = "review-evidence" if role == "reviewer" else "verification-evidence"
        return MaterializedGhAwOutput(
            label=label,
            kind="evidence",
            media_type="application/json",
            trusted_uri=(
                f"{match.group('prefix')}{role}-comment-{comment_id}"
                f"-binding-{binding}.json"
            ),
        )

    def resolve(
        self,
        *,
        external_dispatch_key: str,
        expected_receipt_identity: str,
        trusted_context: dict[str, Any],
    ) -> TrustedGhAwResolvedResult:
        resolved = super().resolve(
            external_dispatch_key=external_dispatch_key,
            expected_receipt_identity=expected_receipt_identity,
            trusted_context=trusted_context,
        )
        if len(resolved.outputs) != 1:
            raise VerticalInvariantError(
                "INTERNAL_FAILURE",
                "production gh-aw source requires one exact logical output",
            )
        base = resolved.outputs[0]
        if resolved.run.role == "developer":
            bound = self._revalidate_developer(
                resolved=resolved,
                trusted_context=trusted_context,
                base_uri=base.trusted_uri,
            )
        elif resolved.run.role in {"reviewer", "qa"}:
            bound = self._revalidate_gate(
                resolved=resolved,
                trusted_context=trusted_context,
                base_uri=base.trusted_uri,
            )
        else:
            raise VerticalInvariantError("POLICY_DENIED", "unsupported production gh-aw role")
        return TrustedGhAwResolvedResult(
            run=resolved.run,
            role_payload=resolved.role_payload,
            outputs=(bound,),
        )

    def load_content(self, uri: str) -> bytes:
        pr_match = _BOUND_PR_RE.fullmatch(str(uri or ""))
        if pr_match:
            number = int(pr_match.group("number"))
            pr = self._json(
                self.target_repository,
                f"/pulls/{number}",
                self.config.target_token,
            )
            if (
                not isinstance(pr, dict)
                or int(pr.get("number") or 0) != number
                or str((pr.get("head") or {}).get("sha") or "")
                != pr_match.group("head")
            ):
                raise VerticalInvariantError("BLOCKED", "trusted Developer PR content changed")
            content = self._developer_content_for_target(pr)
            material = self._developer_binding_material(pr, content)
            if self._binding_digest(material) != pr_match.group("binding"):
                raise VerticalInvariantError(
                    "BLOCKED",
                    "trusted Developer PR provenance/content changed after resolution",
                )
            return content

        gate_match = _BOUND_GATE_RE.fullmatch(str(uri or ""))
        if gate_match:
            comment_id = int(gate_match.group("comment"))
            role = gate_match.group("role")
            comment = self._json(
                self.target_repository,
                f"/issues/comments/{comment_id}",
                self.config.target_token,
            )
            if not isinstance(comment, dict) or int(comment.get("id") or 0) != comment_id:
                raise VerticalInvariantError("BLOCKED", "trusted Gate comment content changed")
            content, material = self._gate_content_and_binding(comment=comment, role=role)
            if self._binding_digest(material) != gate_match.group("binding"):
                raise VerticalInvariantError(
                    "BLOCKED",
                    "trusted Gate comment provenance/content changed after resolution",
                )
            return content

        raise VerticalInvariantError(
            "POLICY_DENIED",
            "unrecognized digest-bound trusted gh-aw collector URI",
        )


def build_digest_bound_production_collector(
    *,
    executor,
    source_config: GitHubActionsGhAwResultSourceConfig,
    target_repository: str,
    workflows: GhAwVerticalWorkflowMap,
    control_repository: str,
    clock,
    trusted_role_policy: str,
    collector_namespace_policy: str,
    http: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
) -> ProductionGhAwVerticalResultCollector:
    """Supported production composition for #221 full-runtime callback adoption."""
    if normalize_repository(source_config.control_repository) != normalize_repository(control_repository):
        raise ValueError("production gh-aw source/collector control repository mismatch")
    if source_config.workflows != workflows:
        raise ValueError("production gh-aw source/collector workflow map mismatch")
    source = DigestBoundTargetScopedGitHubActionsGhAwResultSource(
        source_config,
        target_repository=target_repository,
        http=http,
    )
    coordinator = TrustedVerticalCallbackCoordinator(
        executor=executor,
        trusted_role_policy=trusted_role_policy,
        collector_namespace_policy=collector_namespace_policy,
        content_loader=source.load_content,
    )
    return ProductionGhAwVerticalResultCollector(
        callback_coordinator=coordinator,
        result_source=source,
        workflows=workflows,
        control_repository=control_repository,
        clock=clock,
    )
