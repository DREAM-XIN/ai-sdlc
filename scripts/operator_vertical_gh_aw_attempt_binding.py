#!/usr/bin/env python3
"""First-attempt run-lease binding for the production Operation-bound gh-aw collector.

A durable Vertical launch receipt currently identifies one GitHub Actions run id. A
GitHub Actions re-run keeps that run id but increments run_attempt and executes the
Worker again. The protected Store did not separately authorize that second external
execution, so v0.3 production collection accepts attempt 1 only and brackets the run
identity across run/jobs/log resolution and fresh output loading.

The exact first-attempt lease is encoded only into collector-owned trusted output URIs.
Those URIs are persisted inside the durable callback/output receipt, allowing a fresh
process to re-establish the same run/attempt/workflow/head/key authority from protected
callback state plus current GitHub truth without trusting Worker-supplied run identity
or process-local memory.
"""
from __future__ import annotations

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
from operator_vertical_gh_aw_content_binding import (
    DigestBoundTargetScopedGitHubActionsGhAwResultSource,
)
from operator_vertical_gh_aw_github_source import (
    GitHubActionsGhAwResultSourceConfig,
    ProductionGhAwVerticalResultCollector,
)

_RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DISPATCH_KEY_RE = r"[A-Za-z0-9._:-]+"
_FIRST_ATTEMPT_URI_RE = re.compile(
    r"^(?P<base>docs/features/[A-Za-z0-9._:-]+/worker-runs/[A-Za-z0-9._:-]+/"
    r"[-A-Za-z0-9._:]+-binding-[0-9a-f]{64})"
    r"--first-attempt--key-(?P<key>" + _DISPATCH_KEY_RE + r")"
    r"--run-(?P<run>[1-9][0-9]*)"
    r"--head-(?P<head>[0-9a-f]{40})"
    r"--lease-(?P<lease>[0-9a-f]{64})\.json$"
)


class FirstAttemptDigestBoundGhAwResultSource(
    DigestBoundTargetScopedGitHubActionsGhAwResultSource
):
    """Supported v0.3 source: exact first Actions attempt + digest-bound content."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exact_run_snapshots: dict[int, dict[str, Any]] = {}

    def _first_attempt_run_snapshot(
        self, *, run_id: int, external_dispatch_key: str
    ) -> dict[str, Any]:
        run = self._json(
            self.config.control_repository,
            f"/actions/runs/{run_id}",
            self.config.control_token,
        )
        if not isinstance(run, dict) or int(run.get("id") or 0) != run_id:
            raise VerticalInvariantError(
                "BLOCKED", "trusted first-attempt lease did not resolve exact Actions run"
            )
        try:
            run_attempt = int(run.get("run_attempt"))
        except Exception as exc:
            raise VerticalInvariantError(
                "BLOCKED", "exact gh-aw run lacks trusted run_attempt"
            ) from exc
        if run_attempt != 1:
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "durable run-id receipt does not authorize a GitHub Actions re-run attempt",
            )

        workflow = self._workflow_file(run)
        role_matches = tuple(
            role
            for role in ("developer", "reviewer", "qa")
            if self.config.workflows.workflow_for(role) == workflow
        )
        if len(role_matches) != 1:
            raise VerticalInvariantError(
                "POLICY_DENIED", "first-attempt run is not one exact trusted role workflow"
            )
        role = role_matches[0]
        run_url = str(run.get("html_url") or "")
        expected_url = (
            f"https://github.com/{normalize_repository(self.config.control_repository)}"
            f"/actions/runs/{run_id}"
        )
        head_sha = str(run.get("head_sha") or "")
        if (
            run_url.lower() != expected_url.lower()
            or str(run.get("display_title") or "")
            != f"AI-SDLC gh-aw {external_dispatch_key}"
            or run.get("event") != "workflow_dispatch"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or str(run.get("head_branch") or "")
            != self.config.workflows.default_branch
            or not _SHA_RE.fullmatch(head_sha)
        ):
            raise VerticalInvariantError(
                "BLOCKED", "first-attempt gh-aw run snapshot is not exact/successful"
            )
        return {
            "run_id": run_id,
            "run_attempt": 1,
            "run_url": run_url,
            "workflow_file": workflow,
            "role": role,
            "display_title": str(run.get("display_title") or ""),
            "event": str(run.get("event") or ""),
            "status": str(run.get("status") or ""),
            "conclusion": str(run.get("conclusion") or ""),
            "head_branch": str(run.get("head_branch") or ""),
            "head_sha": head_sha,
            "external_dispatch_key": external_dispatch_key,
        }

    @staticmethod
    def _same_run_snapshot(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return canonical_json(left) == canonical_json(right)

    def _exact_run(
        self, *, external_dispatch_key: str, receipt: str
    ) -> tuple[dict[str, Any], str, bytes]:
        if not _RUN_ID_RE.fullmatch(str(receipt or "")):
            raise VerticalInvariantError(
                "INVALID_REQUEST", "runtime receipt must be one exact Actions run id"
            )
        run_id = int(receipt)
        before = self._first_attempt_run_snapshot(
            run_id=run_id, external_dispatch_key=external_dispatch_key
        )
        run, workflow, logs = super()._exact_run(
            external_dispatch_key=external_dispatch_key,
            receipt=receipt,
        )
        after = self._first_attempt_run_snapshot(
            run_id=run_id, external_dispatch_key=external_dispatch_key
        )
        if not self._same_run_snapshot(before, after):
            raise VerticalInvariantError(
                "BLOCKED",
                "gh-aw run/attempt changed while resolving jobs or conclusion logs",
            )
        if (
            workflow != after["workflow_file"]
            or int(run.get("id") or 0) != run_id
            or int(run.get("run_attempt") or 0) != 1
        ):
            raise VerticalInvariantError(
                "BLOCKED", "base gh-aw resolution escaped first-attempt run lease"
            )
        self._exact_run_snapshots[run_id] = dict(after)
        return run, workflow, logs

    def _resolved_run_matches_snapshot(
        self, *, resolved: TrustedGhAwResolvedResult, snapshot: dict[str, Any]
    ) -> None:
        run = resolved.run
        expected = {
            "run_id": run.run_id,
            "run_url": run.run_url,
            "workflow_file": run.workflow_file,
            "role": run.role,
            "display_title": run.display_title,
            "event": run.event,
            "status": run.status,
            "conclusion": run.conclusion,
            "external_dispatch_key": run.external_dispatch_key,
        }
        for field, value in expected.items():
            if snapshot.get(field) != value:
                raise VerticalInvariantError(
                    "STALE_REVISION",
                    f"first-attempt run snapshot/resolved result mismatch: {field}",
                )
        if run.workflow_ref != self.config.workflows.default_branch:
            raise VerticalInvariantError(
                "POLICY_DENIED", "resolved run escaped trusted default branch"
            )

    @staticmethod
    def _lease_uri(output: MaterializedGhAwOutput, snapshot: dict[str, Any]) -> str:
        uri = str(output.trusted_uri or "")
        if not uri.endswith(".json"):
            raise VerticalInvariantError(
                "INTERNAL_FAILURE", "digest-bound gh-aw output URI is not canonical JSON"
            )
        external_dispatch_key = str(snapshot["external_dispatch_key"])
        if not re.fullmatch(_DISPATCH_KEY_RE, external_dispatch_key):
            raise VerticalInvariantError(
                "INTERNAL_FAILURE", "stable dispatch key cannot be represented in durable lease URI"
            )
        return (
            uri[:-5]
            + f"--first-attempt--key-{external_dispatch_key}"
            + f"--run-{int(snapshot['run_id'])}"
            + f"--head-{snapshot['head_sha']}"
            + f"--lease-{digest_json(snapshot)}.json"
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
        expected = self._exact_run_snapshots.get(resolved.run.run_id)
        if not isinstance(expected, dict):
            raise VerticalInvariantError(
                "INTERNAL_FAILURE", "first-attempt run lease was not retained during resolution"
            )
        current = self._first_attempt_run_snapshot(
            run_id=resolved.run.run_id,
            external_dispatch_key=external_dispatch_key,
        )
        if not self._same_run_snapshot(expected, current):
            raise VerticalInvariantError(
                "BLOCKED", "gh-aw run/attempt changed after trusted result resolution"
            )
        self._resolved_run_matches_snapshot(resolved=resolved, snapshot=current)
        outputs = tuple(
            MaterializedGhAwOutput(
                label=output.label,
                kind=output.kind,
                media_type=output.media_type,
                trusted_uri=self._lease_uri(output, current),
            )
            for output in resolved.outputs
        )
        return TrustedGhAwResolvedResult(
            run=resolved.run,
            role_payload=resolved.role_payload,
            outputs=outputs,
        )

    def load_content(self, uri: str) -> bytes:
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(str(uri or ""))
        if not match:
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "collector content URI lacks a durable first-attempt run lease",
            )
        run_id = int(match.group("run"))
        dispatch_key = match.group("key")
        expected = self._first_attempt_run_snapshot(
            run_id=run_id, external_dispatch_key=dispatch_key
        )
        if (
            expected["head_sha"] != match.group("head")
            or digest_json(expected) != match.group("lease")
        ):
            raise VerticalInvariantError(
                "BLOCKED", "durable first-attempt URI lease no longer matches trusted run truth"
            )
        base_uri = match.group("base") + ".json"
        data = super().load_content(base_uri)
        after = self._first_attempt_run_snapshot(
            run_id=run_id, external_dispatch_key=dispatch_key
        )
        if not self._same_run_snapshot(expected, after):
            raise VerticalInvariantError(
                "BLOCKED", "gh-aw run/attempt changed while output receipt was loading"
            )
        return data


def build_first_attempt_production_collector(
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
    """Final supported v0.3 production composition for #221 callback adoption."""
    if normalize_repository(source_config.control_repository) != normalize_repository(
        control_repository
    ):
        raise ValueError("production gh-aw source/collector control repository mismatch")
    if source_config.workflows != workflows:
        raise ValueError("production gh-aw source/collector workflow map mismatch")
    source = FirstAttemptDigestBoundGhAwResultSource(
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
