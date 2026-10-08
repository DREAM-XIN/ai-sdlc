#!/usr/bin/env python3
"""Production authority graph for one fixed v0.3 real-dogfood fixture.

The OpenAI Responses production bundle is the sole Operator-runtime construction
entrypoint.  The real dogfood therefore exercises the accepted write-capable
adapter rather than bypassing it and calling operation.start directly.  The
same returned Operator bundle is then wired to the production gh-aw collector;
no second Store, Persist, callback or dispatch authority is constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib import error, parse, request

from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway, TrustedCandidateSnapshot
from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_openai_responses_production import (
    OpenAIResponsesProductionBundle,
    build_openai_responses_production_bundle,
)
from operator_production_runtime import TrustedOperatorRuntimeConfig
from operator_release_feature_event_gateway import build_release_decision_event_gateway
from operator_store_model import digest_json, normalize_repository, operation_events, reservation_path
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError, validate_worker_result
from operator_vertical_callback import process_recorded_callback
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_recovery import plan_vertical_callback_record
from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig
from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource
from operator_vertical_gh_aw_github_source import (
    GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector,
    _build_receipts, _current_launch_binding, _validate_run,
)
from v03_dogfood_fixture_pool import DogfoodSlot
from v03_dogfood_session_policy import DogfoodSessionDecisionPolicyVerifier
from v03_real_runtime_full_composition import DeferredFixtureFeatureTruthGateway

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_DEVELOPER_PR_URI = re.compile(
    r"^docs/features/(?P<feature>[^/]+)/worker-runs/(?P<dispatch>[^/]+)/"
    r"developer-pr-(?P<pr>[1-9][0-9]*)-(?P<head>[0-9a-f]{40})\.json$"
)
DEFAULT_BRANCH = "main"
COLLECTOR_IDENTITY = "ai-sdlc-v03-real-dogfood-collector"
PROVIDER_SCOPE_ID = "v03-real-release-dogfood"
RECOVERY_DEVELOPER_WORKFLOW = "ai-sdlc-gh-aw-worker-deepseek-v03-local.lock.yml"
RECOVERY_SCHEMA = "ai-sdlc.v03-dogfood-bounded-recovery/v1"
RECOVERY_OPERATION_ID = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
RECOVERY_RECEIPT_PATH = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-bounded-recovery/sealed-receipt.json"


class V03DogfoodCompositionError(RuntimeError):
    pass


class DogfoodGitHubCandidateProvider:
    """Fresh-read exactly one same-repository PR for one immutable dogfood slot."""

    def __init__(self, *, slot: DogfoodSlot, repository: str, token: str, api_base: str = "https://api.github.com", http_get=None):
        self.slot = slot
        self.repository = normalize_repository(repository)
        self.token = str(token or "")
        self.api_base = str(api_base or "").rstrip("/")
        self.http_get = http_get or self._default_get
        self.runtime = None
        if not self.token or not self.api_base.startswith("https://"):
            raise ValueError("dogfood candidate provider requires trusted HTTPS GitHub read authority")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-dogfood-candidate",
        }

    @staticmethod
    def _default_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw.decode()) if raw else []
        except error.HTTPError as exc:
            return int(exc.code), []
        except Exception:
            return 0, []

    def bind_runtime(self, runtime: Any) -> None:
        if self.runtime is not None and self.runtime is not runtime:
            raise V03DogfoodCompositionError("candidate provider runtime binding changed")
        if runtime is None or getattr(runtime, "backend", None) is None:
            raise ValueError("candidate provider requires protected Store runtime")
        self.runtime = runtime

    @staticmethod
    def _developer_receipt(envelope: dict[str, Any]) -> tuple[int, str]:
        receipts = envelope.get("collected_outputs")
        if not isinstance(receipts, list):
            raise V03DogfoodCompositionError("developer callback lacks sealed output receipts")
        matches: list[tuple[int, str]] = []
        for receipt in receipts:
            if not isinstance(receipt, dict) or receipt.get("kind") != "artifact":
                continue
            match = _DEVELOPER_PR_URI.fullmatch(str(receipt.get("trusted_uri") or ""))
            if match:
                matches.append((int(match.group("pr")), match.group("head")))
        if len(matches) != 1:
            raise V03DogfoodCompositionError("developer callback must bind one exact Draft PR output")
        return matches[0]

    def _pending_handoff(self, operation_id: str) -> dict[str, Any] | None:
        if self.runtime is None:
            return None
        callbacks: dict[str, dict[str, Any]] = {}
        handoffs: dict[str, dict[str, Any]] = {}
        translated: dict[str, str] = {}
        confirmed: set[str] = set()
        for row in operation_events(self.runtime.backend.read_snapshot(), operation_id):
            event_type = str(row.get("event_type") or "")
            payload = row.get("payload") or {}
            if event_type == "worker.callback.recorded":
                envelope = payload.get("trusted_callback_envelope")
                context = (envelope or {}).get("trusted_context") if isinstance(envelope, dict) else None
                callback_id = str(payload.get("callback_id") or "")
                if isinstance(context, dict) and context.get("role") == "developer" and callback_id:
                    callbacks[callback_id] = envelope
            elif event_type == "candidate.handoff.adopted":
                callback_id = str(payload.get("callback_id") or "")
                if callback_id:
                    handoffs[callback_id] = payload
            elif event_type == "feature.event.translated":
                callback_id = str(payload.get("callback_id") or "")
                event_id = str(payload.get("feature_event_id") or "")
                if callback_id and event_id:
                    translated.setdefault(callback_id, event_id)
            elif event_type == "persist.confirmed":
                event_id = str(payload.get("feature_event_id") or "")
                if event_id:
                    confirmed.add(event_id)
        pending: list[dict[str, Any]] = []
        for callback_id, envelope in callbacks.items():
            event_id = translated.get(callback_id)
            if event_id and event_id in confirmed:
                continue
            context = envelope.get("trusted_context") or {}
            handoff = handoffs.get(callback_id)
            if not isinstance(handoff, dict):
                continue
            source_pr, source_head = self._developer_receipt(envelope)
            if (
                handoff.get("source_candidate_pr_number") != source_pr
                or handoff.get("source_candidate_head_sha") != source_head
                or handoff.get("prior_candidate_head_sha") != context.get("candidate_head_sha")
                or handoff.get("dispatch_id") != context.get("dispatch_id")
            ):
                raise V03DogfoodCompositionError("durable candidate handoff differs from sealed Developer callback")
            pending.append(handoff)
        if len(pending) > 1:
            raise V03DogfoodCompositionError("multiple incomplete Developer candidate handoffs")
        return pending[0] if pending else None

    def _is_descendant(self, ancestor: str, descendant: str) -> bool:
        if ancestor == descendant:
            return True
        status, payload = self.http_get(
            f"{self.api_base}/repos/{self.repository}/compare/{ancestor}...{descendant}",
            self._headers(),
        )
        return bool(
            status == 200
            and isinstance(payload, dict)
            and payload.get("status") == "ahead"
            and int(payload.get("behind_by") or 0) == 0
            and str(((payload.get("merge_base_commit") or {}).get("sha")) or "").lower() == ancestor
        )

    def _candidate(self) -> dict[str, Any]:
        owner = self.repository.split("/", 1)[0]
        query = parse.urlencode({
            "state": "open",
            "head": f"{owner}:{self.slot.target_ref}",
            "base": DEFAULT_BRANCH,
            "per_page": 100,
        })
        status, payload = self.http_get(
            f"{self.api_base}/repos/{self.repository}/pulls?{query}",
            self._headers(),
        )
        if status != 200 or not isinstance(payload, list):
            raise V03DogfoodCompositionError("dogfood candidate PR truth lookup failed closed")
        rows = [row for row in payload if isinstance(row, dict) and row.get("state") == "open" and row.get("draft") is False]
        if len(rows) != 1:
            raise V03DogfoodCompositionError("dogfood slot must resolve exactly one open non-draft PR")
        row = rows[0]
        head = row.get("head") or {}
        base = row.get("base") or {}
        head_repo = str(((head.get("repo") or {}).get("full_name")) or "").lower()
        base_repo = str(((base.get("repo") or {}).get("full_name")) or "").lower()
        head_sha = str(head.get("sha") or "").lower()
        number = row.get("number")
        if (
            head_repo != self.repository
            or base_repo != self.repository
            or head.get("ref") != self.slot.target_ref
            or base.get("ref") != DEFAULT_BRANCH
            or not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
            or not _SHA40.fullmatch(head_sha)
        ):
            raise V03DogfoodCompositionError("dogfood candidate PR repository/ref/head authority drifted")
        return row

    def current_candidate(self, *, operation_id: str, repository: str, feature_id: str, target_ref: str) -> TrustedCandidateSnapshot:
        if (
            not operation_id
            or normalize_repository(repository) != self.repository
            or feature_id != self.slot.feature_id
            or target_ref != self.slot.target_ref
        ):
            raise V03DogfoodCompositionError("candidate lookup escaped fixed dogfood slot identity")
        row = self._candidate()
        current_head = str(row["head"]["sha"]).lower()
        pending = self._pending_handoff(operation_id)
        if pending is not None:
            adopted_head = str(pending.get("source_candidate_head_sha") or "").lower()
            prior_head = str(pending.get("prior_candidate_head_sha") or "").lower()
            if (
                int(pending.get("fixture_candidate_pr_number") or 0) != int(row["number"])
                or not _SHA40.fullmatch(adopted_head)
                or not _SHA40.fullmatch(prior_head)
                or not self._is_descendant(adopted_head, current_head)
            ):
                raise V03DogfoodCompositionError("incomplete handoff no longer matches fixed candidate lineage")
            current_head = prior_head
        return TrustedCandidateSnapshot(
            candidate_pr_number=int(row["number"]),
            candidate_head_sha=current_head,
        )


class DogfoodCandidateHandoff:
    """Adopt one sealed Developer Draft PR head onto the isolated fixture ref."""

    def __init__(
        self,
        *,
        slot: DogfoodSlot,
        repository: str,
        token: str,
        candidate_provider: DogfoodGitHubCandidateProvider,
        api_base: str = "https://api.github.com",
        http_request=None,
    ):
        self.slot = slot
        self.repository = normalize_repository(repository)
        self.token = str(token or "")
        self.candidate_provider = candidate_provider
        self.api_base = str(api_base or "").rstrip("/")
        self.http_request = http_request or self._default_request
        if not self.token or not self.api_base.startswith("https://"):
            raise ValueError("candidate handoff requires trusted HTTPS GitHub write authority")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-dogfood-handoff",
        }

    @staticmethod
    def _default_request(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None):
        data = None if body is None else json.dumps(body, sort_keys=True).encode("utf-8")
        request_headers = dict(headers)
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        req = request.Request(url, headers=request_headers, data=data, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw.decode()) if raw else {}
        except error.HTTPError as exc:
            return int(exc.code), {}
        except Exception:
            return 0, {}

    def _api(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, object]:
        return self.http_request(
            method,
            f"{self.api_base}/repos/{self.repository}{path}",
            self._headers(),
            body,
        )

    def adopt(
        self,
        *,
        executor: Any,
        context: Any,
        callback_id: str,
        receipts: list[dict[str, Any]],
    ) -> None:
        if context.role != "developer" or context.feature_id != self.slot.feature_id or context.target_ref != self.slot.target_ref:
            raise V03DogfoodCompositionError("candidate handoff escaped fixed Developer/fixture authority")
        envelope = {"collected_outputs": receipts}
        source_pr, source_head = self.candidate_provider._developer_receipt(envelope)
        fixture = self.candidate_provider._candidate()
        prior_head = str(context.candidate_head_sha or "").lower()
        fixture_head = str(fixture["head"]["sha"]).lower()
        if int(fixture["number"]) < 1 or fixture_head not in {prior_head, source_head}:
            raise V03DogfoodCompositionError("fixture candidate changed before Developer handoff")
        status, pr = self._api("GET", f"/pulls/{source_pr}")
        if (
            status != 200
            or not isinstance(pr, dict)
            or pr.get("state") != "open"
            or pr.get("draft") is not True
            or str((pr.get("base") or {}).get("ref") or "") != self.slot.target_ref
            or str((pr.get("head") or {}).get("sha") or "").lower() != source_head
        ):
            raise V03DogfoodCompositionError("sealed Developer Draft PR changed before handoff")
        status, comparison = self._api("GET", f"/compare/{prior_head}...{source_head}")
        if (
            status != 200
            or not isinstance(comparison, dict)
            or comparison.get("status") != "ahead"
            or int(comparison.get("ahead_by") or 0) < 1
            or int(comparison.get("behind_by") or 0) != 0
            or str(((comparison.get("merge_base_commit") or {}).get("sha")) or "").lower() != prior_head
        ):
            raise V03DogfoodCompositionError("Developer output is not a strict fast-forward of its reviewed fixture input")
        ref_path = f"/git/refs/heads/{parse.quote(self.slot.target_ref, safe='')}"
        status, current = self._api("GET", ref_path)
        current_sha = str(((current or {}).get("object") or {}).get("sha") or "").lower() if isinstance(current, dict) else ""
        if status != 200 or current_sha not in {prior_head, source_head}:
            raise V03DogfoodCompositionError("fixture ref changed outside one-shot Developer handoff")
        if current_sha == prior_head:
            status, updated = self._api("PATCH", ref_path, {"sha": source_head, "force": False})
            if status != 200 or str(((updated or {}).get("object") or {}).get("sha") or "").lower() != source_head:
                raise V03DogfoodCompositionError("trusted Developer candidate fast-forward was not accepted")
        executor._record_fact(
            context.operation_id,
            "candidate.handoff.adopted",
            {
                "callback_id": callback_id,
                "dispatch_id": context.dispatch_id,
                "target_ref": self.slot.target_ref,
                "fixture_candidate_pr_number": int(fixture["number"]),
                "prior_candidate_head_sha": prior_head,
                "source_candidate_pr_number": source_pr,
                "source_candidate_head_sha": source_head,
            },
        )


class DogfoodTrustedCallbackCoordinator:
    """Pause accepted callbacks around the trusted Developer handoff/lifecycle fence."""

    def __init__(self, *, delegate: Any, candidate_handoff: DogfoodCandidateHandoff):
        self.delegate = delegate
        self.executor = delegate.executor
        self.candidate_handoff = candidate_handoff

    @staticmethod
    def _artifact_uri(receipts: list[dict[str, Any]]) -> str:
        rows = [
            str(row.get("trusted_uri") or "")
            for row in receipts
            if isinstance(row, dict) and row.get("kind") == "artifact"
        ]
        if len(rows) != 1 or _DEVELOPER_PR_URI.fullmatch(rows[0]) is None:
            raise V03DogfoodCompositionError("Developer callback lacks one exact implementation receipt")
        return rows[0]

    def _supersede_remediation_artifact(
        self,
        *,
        context: Any,
        callback_id: str,
        receipts: list[dict[str, Any]],
    ) -> None:
        feature, manifest = self.executor.feature_gateway.read_feature(operation_id=context.operation_id)
        tasks = manifest.get("tasks") or []
        remediation = [
            row for row in tasks
            if isinstance(row, dict)
            and row.get("id") == context.task_id
            and row.get("kind") == "remediation"
            and row.get("status") == "DONE"
        ]
        if not remediation:
            return
        uri = self._artifact_uri(receipts)
        drafts = [
            row for row in (manifest.get("artifacts") or [])
            if isinstance(row, dict)
            and row.get("type") == "implementation"
            and row.get("status", "draft") == "draft"
        ]
        current = [row for row in drafts if row.get("uri") == uri]
        previous = [row for row in drafts if row.get("uri") != uri]
        if len(current) != 1 or len(previous) != 1:
            raise VerticalInvariantError(
                "BLOCKED",
                "remediation supersession requires exactly one predecessor and one exact replacement draft",
            )
        event = {
            "version": "0.1.0",
            "id": "EVT-" + context.feature_id + "-VERTICAL-REMEDIATION-SUPERSEDE-"
            + digest_json({
                "callback_id": callback_id,
                "previous": previous[0]["id"],
                "replacement": current[0]["id"],
                "revision": feature.revision,
            })[:12].upper(),
            "feature_id": context.feature_id,
            "expected_revision": feature.revision,
            "occurred_at": self.executor.runtime.clock(),
            "changes": [
                {"kind": "artifact", "id": previous[0]["id"], "status": "superseded"},
            ],
        }
        self.executor._record_fact(
            context.operation_id,
            "feature.event.translated",
            {
                "feature_event_id": event["id"],
                "feature_event_digest": digest_json(event),
                "feature_event": event,
                "feature_revision": feature.revision,
                "feature_stage": feature.current_stage,
                "feature_manifest_digest": feature.manifest_digest,
                "candidate_head_sha": feature.candidate_head_sha,
                "target_ref": feature.target_ref,
                "callback_id": callback_id,
                "purpose": "remediation_artifact_supersession",
                "replacement_artifact_id": current[0]["id"],
                "superseded_artifact_id": previous[0]["id"],
            },
        )
        self.executor._persist(context.operation_id, event, feature)

    def handle(
        self,
        *,
        context: Any,
        callback_id: str,
        worker_payload: dict[str, Any],
        receipts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        projection = self.executor._projection(context.operation_id)
        if projection["generation"] != context.operation_generation:
            raise VerticalInvariantError("SUPERSEDED_GENERATION", "callback belongs to a superseded generation")
        if context.target_ref != self.executor.config.target_ref:
            raise VerticalInvariantError("STALE_REVISION", "callback target ref is outside trusted dogfood runtime")
        self.executor._commit(
            lambda snapshot: plan_vertical_callback_record(
                snapshot,
                context=context,
                callback_id=callback_id,
                worker_payload=worker_payload,
                receipts=receipts,
                occurred_at=self.executor.runtime.clock(),
                trusted_context_digest=self.executor.config.trusted_context_digest,
            )
        )
        if context.role == "developer":
            self.candidate_handoff.adopt(
                executor=self.executor,
                context=context,
                callback_id=callback_id,
                receipts=receipts,
            )
        result = process_recorded_callback(
            self.executor,
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
            trusted_role_policy=self.delegate.trusted_role_policy,
            collector_namespace_policy=self.delegate.collector_namespace_policy,
            content_loader=self.delegate.content_loader,
            continue_after=False,
        )
        if context.role == "developer" and str(result.get("status") or "") not in {"BLOCKED", "NEEDS_USER"}:
            self._supersede_remediation_artifact(
                context=context,
                callback_id=callback_id,
                receipts=receipts,
            )
        return self.executor.advance_until_stop(operation_id=context.operation_id)



class DogfoodCandidateBoundActionsTransport(GitHubActionsVerticalGhAwTransport):
    """Keep Developer's fixed PR head in its payload, under fresh candidate truth.

    The shared transport's Developer contract predates real-dogfood fixtures and
    requires a null candidate payload field. All its other checks and its actual
    one-POST/lookup behavior remain inherited unchanged.
    """

    def __init__(self, config, *, candidate_provider, **kwargs):
        if not isinstance(candidate_provider, DogfoodGitHubCandidateProvider):
            raise ValueError("dogfood transport requires the fixed candidate provider")
        super().__init__(config, **kwargs)
        self.candidate_provider = candidate_provider

    def _validate_dispatch_inputs(self, *, workflow, ref, inputs):
        if workflow != self.config.workflows.developer_workflow:
            return super()._validate_dispatch_inputs(workflow=workflow, ref=ref, inputs=inputs)
        try:
            payload = json.loads(inputs["task_payload"])
            context = payload["feature_context"]
            vertical = context["vertical"]
            head = vertical["candidate_head_sha"]
            if not isinstance(head, str) or not _SHA40.fullmatch(head):
                raise ValueError("candidate head is not exact")
            checked = json.loads(inputs["task_payload"])
            checked["feature_context"]["vertical"]["candidate_head_sha"] = None
            checked_inputs = dict(inputs, task_payload=json.dumps(checked, sort_keys=True, separators=(",", ":")))
        except (KeyError, TypeError, ValueError) as exc:
            raise VerticalInvariantError("INVALID_REQUEST", "dogfood Developer payload lacks exact candidate head") from exc
        key = super()._validate_dispatch_inputs(workflow=workflow, ref=ref, inputs=checked_inputs)
        candidate = self.candidate_provider.current_candidate(
            operation_id=str(vertical.get("operation_id") or ""),
            repository=inputs["target_repository"],
            feature_id=inputs["feature_id"],
            target_ref=inputs["target_ref"],
        )
        if candidate.candidate_head_sha != head:
            raise VerticalInvariantError("STALE_REVISION", "dogfood Developer candidate changed before transport")
        # Only the validation copy was normalized. The inherited dispatcher
        # sends the original candidate-bound bytes and retains the stable key.
        return key


class DogfoodExecutionBoundDispatchGateway:
    """Dogfood-only adapter exposing exact readiness-resolved execution identity."""

    def __init__(self, *, delegate: GhAwVerticalRoleDispatchGateway, execution_bindings: dict[str, dict[str, str]]):
        if not isinstance(delegate, GhAwVerticalRoleDispatchGateway):
            raise ValueError("dogfood dispatch binding requires the production gh-aw gateway")
        if set(execution_bindings) != {"developer", "reviewer", "qa"}:
            raise ValueError("dogfood execution binding map is incomplete")
        self.delegate = delegate
        self.execution_bindings = {role: dict(value) for role, value in execution_bindings.items()}
        self.transport = delegate.transport
        self.workflows = delegate.workflows
        for role, binding in self.execution_bindings.items():
            if (
                binding.get("role") != role
                or binding.get("workflow_file") != self.workflows.workflow_for(role)
                or binding.get("default_branch") != self.workflows.default_branch
                or not binding.get("worker_id")
                or not binding.get("profile")
                or not binding.get("selection_policy_id")
            ):
                raise ValueError(f"dogfood execution binding drifted for {role}")

    def execution_binding(self, *, dispatch: dict[str, Any]) -> dict[str, str]:
        role = str(dispatch.get("role") or "")
        binding = self.execution_bindings.get(role)
        if binding is None:
            raise VerticalInvariantError("POLICY_DENIED", "dogfood dispatch role escaped resolved execution bindings")
        if self.workflows.workflow_for(role) != binding["workflow_file"]:
            raise VerticalInvariantError("POLICY_DENIED", "dogfood workflow changed after readiness binding")
        return dict(binding)

    def launch(self, *, dispatch: dict[str, Any]) -> dict[str, Any]:
        return self.delegate.launch(dispatch=dispatch)

    def lookup(self, *, external_dispatch_key: str) -> dict[str, Any]:
        return self.delegate.lookup(external_dispatch_key=external_dispatch_key)


class DogfoodRecoveryCollector:
    """Collect only the one sealed recovery run, then reuse the closed callback path."""

    def __init__(self, *, callback_coordinator, result_source, workflows, control_repository, clock):
        self.callback_coordinator = callback_coordinator
        self.result_source = result_source
        self.workflows = workflows
        self.control_repository = normalize_repository(control_repository)
        self.clock = clock

    def handle(self, *, operation_id: str, external_dispatch_key: str) -> dict[str, Any]:
        if operation_id != RECOVERY_OPERATION_ID:
            raise VerticalInvariantError("POLICY_DENIED", "recovery collector escaped frozen Operation")
        executor = self.callback_coordinator.executor
        snapshot = executor.runtime.backend.read_snapshot()
        sealed = snapshot.get(RECOVERY_RECEIPT_PATH)
        if (
            not isinstance(sealed, dict)
            or sealed.get("schema_version") != RECOVERY_SCHEMA
            or sealed.get("operation_id") != operation_id
            or sealed.get("external_dispatch_key") != external_dispatch_key
            or sealed.get("workflow_file") != RECOVERY_DEVELOPER_WORKFLOW
            or not str(sealed.get("receipt_id") or "").isdigit()
            or not str(sealed.get("recovery_dispatch_key") or "")
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery collector lacks sealed exact receipt")
        projection, launch, historical_receipt = _current_launch_binding(
            snapshot, operation_id=operation_id, external_dispatch_key=external_dispatch_key
        )
        if str(historical_receipt) != "37204777409" or str(launch.get("role") or "") != "developer":
            raise VerticalInvariantError("POLICY_DENIED", "historical launch binding drifted")
        semantic_key = str(launch["semantic_effect_key"])
        reservation = snapshot.get(reservation_path(semantic_key))
        if not isinstance(reservation, dict):
            raise VerticalInvariantError("INTERNAL_FAILURE", "durable semantic reservation is missing")
        recovery_key = str(sealed["recovery_dispatch_key"])
        receipt = str(sealed["receipt_id"])
        trusted = {
            "operation_id": operation_id,
            "operation_generation": int(projection["generation"]),
            "operation_profile": VERTICAL_PROFILE,
            "semantic_effect_key": semantic_key,
            "external_dispatch_key": recovery_key,
            "dispatch_id": str(sealed["recovery_dispatch_id"]),
            "target_repository": normalize_repository(str(projection["target_repository"])),
            "target_ref": executor.config.target_ref,
            "feature_id": str(projection["feature_id"]),
            "expected_revision": int(projection["expected_feature_revision"]),
            "feature_stage": str(launch["stage"]),
            "role": "developer",
            "launch_candidate_head_sha": launch.get("candidate_head_sha"),
        }
        resolved = self.result_source.resolve(
            external_dispatch_key=recovery_key,
            expected_receipt_identity=receipt,
            trusted_context=trusted,
        )
        recovery_launch = dict(launch)
        recovery_launch.update({
            "external_dispatch_key": recovery_key,
            "dispatch_id": str(sealed["recovery_dispatch_id"]),
        })
        recovery_reservation = dict(reservation)
        recovery_reservation["external_dispatch_key"] = recovery_key
        _validate_run(
            resolved.run,
            control_repository=self.control_repository,
            workflows=self.workflows,
            launch=recovery_launch,
            expected_receipt_identity=receipt,
            external_dispatch_key=recovery_key,
            reservation=recovery_reservation,
        )
        worker_payload = validate_worker_result("developer", resolved.role_payload)
        context = TrustedDispatchContext(
            operation_id=operation_id,
            operation_generation=int(projection["generation"]),
            operation_profile=VERTICAL_PROFILE,
            semantic_effect_key=semantic_key,
            external_dispatch_key=external_dispatch_key,
            dispatch_id=str(launch["dispatch_id"]),
            runtime_receipt_identity=receipt,
            target_repository=str(projection["target_repository"]),
            target_ref=executor.config.target_ref,
            feature_id=str(projection["feature_id"]),
            expected_revision=int(projection["expected_feature_revision"]),
            feature_stage=str(launch["stage"]),
            task_id=resolved.run.task_id,
            role="developer",
            candidate_pr_number=None,
            candidate_head_sha=launch.get("candidate_head_sha"),
            worker_identity=resolved.run.worker_identity,
            collector_identity=resolved.run.collector_identity,
        )
        declared = {str(row["label"]): str(row["kind"]) for row in worker_payload.get("outputs", [])}
        receipts = _build_receipts(
            coordinator=self.callback_coordinator,
            context=context,
            outputs=resolved.outputs,
            declared_outputs=declared,
            collected_at=str(self.clock()),
        )
        if set(declared.items()) != {(row["label"], row["kind"]) for row in receipts}:
            raise VerticalInvariantError("BLOCKED", "sealed recovery outputs differ from role result")
        callback_id = "gh-aw-recovery-callback-" + digest_json({
            "operation_id": operation_id,
            "external_dispatch_key": external_dispatch_key,
            "recovery_dispatch_key": recovery_key,
            "runtime_receipt_identity": receipt,
            "run_id": resolved.run.run_id,
        })[:24]
        return self.callback_coordinator.handle(
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
        )


@dataclass(frozen=True)
class V03DogfoodFullComposition:
    slot: DogfoodSlot
    workflows: GhAwVerticalWorkflowMap
    candidate_provider: DogfoodGitHubCandidateProvider
    feature_truth_gateway: DeferredFixtureFeatureTruthGateway
    feature_event_gateway: Any
    actions_transport: GitHubActionsVerticalGhAwTransport
    dispatch_gateway: DogfoodExecutionBoundDispatchGateway
    result_source: FirstAttemptDigestBoundGhAwResultSource
    responses: OpenAIResponsesProductionBundle
    bundle: Any
    collector: ProductionGhAwVerticalResultCollector
    recovery_workflows: GhAwVerticalWorkflowMap
    recovery_dispatch_gateway: GhAwVerticalRoleDispatchGateway
    recovery_result_source: FirstAttemptDigestBoundGhAwResultSource
    recovery_collector: DogfoodRecoveryCollector
    policy_authority: Any

    @property
    def runtime(self):
        return self.responses.runtime

    @property
    def adapter(self):
        return self.responses.adapter



def build_v03_dogfood_full_composition(
    *,
    slot: DogfoodSlot,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    actions_token: str,
    event_write_token: str,
    control_repository: str,
    workflows: GhAwVerticalWorkflowMap,
    execution_bindings: dict[str, dict[str, str]],
    protection_verifier: Any,
    policy_authority: Any,
    trusted_context_digest: str,
    collector_namespace_policy: str,
    trusted_role_policy: str,
    clock: Callable[[], Any],
    github_api_base: str = "https://api.github.com",
    persist_poll_attempts: int = 60,
    persist_poll_seconds: float = 2.0,
) -> V03DogfoodFullComposition:
    if not isinstance(config, TrustedOperatorRuntimeConfig):
        raise ValueError("trusted Operator runtime config is required")
    if adapter_id != OPENAI_RESPONSES_ADAPTER_ID:
        raise ValueError("real v0.3 dogfood must use the supported OpenAI Responses adapter")
    if normalize_repository(control_repository) != config.target_repository:
        raise ValueError("dogfood control/target repository must be identical")
    if config.feature_ids != frozenset({slot.feature_id}) or config.feature_ref(slot.feature_id) != slot.target_ref:
        raise ValueError("dogfood production composition escaped fixed slot binding")
    if workflows.default_branch != DEFAULT_BRANCH:
        raise ValueError("dogfood workflows must dispatch from main")
    if not all((target_read_token, actions_token, event_write_token, trusted_context_digest)):
        raise ValueError("dogfood composition requires explicit bounded credentials/context")
    if actions_token == event_write_token:
        raise ValueError("Actions/read authority and Feature Event write authority must remain split")
    if not callable(clock):
        raise ValueError("dogfood composition requires trusted clock")
    for name in ("rollout_verifier", "resolution_policy_verifier", "decision_policy_verifier"):
        if getattr(policy_authority, name, None) is None:
            raise ValueError(f"protected policy authority lacks {name}")

    feature_event_gateway = build_release_decision_event_gateway(
        token=event_write_token,
        repository=config.target_repository,
        default_branch=DEFAULT_BRANCH,
        feature_refs={slot.feature_id: slot.target_ref},
        api_base=github_api_base,
        poll_attempts=persist_poll_attempts,
        poll_seconds=persist_poll_seconds,
    )
    candidate_provider = DogfoodGitHubCandidateProvider(
        slot=slot,
        repository=config.target_repository,
        token=target_read_token,
        api_base=github_api_base,
    )
    feature_truth = DeferredFixtureFeatureTruthGateway()
    source_config = GitHubActionsGhAwResultSourceConfig(
        control_repository=control_repository,
        control_token=actions_token,
        target_token=target_read_token,
        workflows=workflows,
        collector_identity=COLLECTOR_IDENTITY,
        api_url=github_api_base,
    )
    result_source = FirstAttemptDigestBoundGhAwResultSource(
        source_config,
        target_repository=config.target_repository,
    )
    actions_transport = DogfoodCandidateBoundActionsTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=control_repository,
            token=actions_token,
            workflows=workflows,
            api_url=github_api_base,
        ),
        candidate_provider=candidate_provider,
    )
    recovery_workflows = GhAwVerticalWorkflowMap(
        default_branch=DEFAULT_BRANCH,
        developer_workflow=RECOVERY_DEVELOPER_WORKFLOW,
        reviewer_workflow=workflows.reviewer_workflow,
        qa_workflow=workflows.qa_workflow,
    )
    recovery_source_config = GitHubActionsGhAwResultSourceConfig(
        control_repository=control_repository,
        control_token=actions_token,
        target_token=target_read_token,
        workflows=recovery_workflows,
        collector_identity=COLLECTOR_IDENTITY,
        api_url=github_api_base,
    )
    recovery_result_source = FirstAttemptDigestBoundGhAwResultSource(
        recovery_source_config,
        target_repository=config.target_repository,
    )
    recovery_transport = DogfoodCandidateBoundActionsTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=control_repository,
            token=actions_token,
            workflows=recovery_workflows,
            api_url=github_api_base,
        ),
        candidate_provider=candidate_provider,
    )
    recovery_dispatch_gateway = GhAwVerticalRoleDispatchGateway(
        transport=recovery_transport,
        workflows=recovery_workflows,
    )
    raw_dispatch_gateway = GhAwVerticalRoleDispatchGateway(transport=actions_transport, workflows=workflows)
    dispatch_gateway = DogfoodExecutionBoundDispatchGateway(
        delegate=raw_dispatch_gateway,
        execution_bindings=execution_bindings,
    )

    decision_verifier = policy_authority.decision_policy_verifier
    if slot.scenario == "session_recovery":
        decision_verifier = DogfoodSessionDecisionPolicyVerifier(
            repository=config.store_repository,
            installation_sha=policy_authority.installation_commit_sha,
            token=target_read_token,
            api_base=github_api_base,
        )
    responses = build_openai_responses_production_bundle(
        config=config,
        feature_id=slot.feature_id,
        registration_id=f"v03-dogfood:{slot.scenario}",
        provider_scope_id=PROVIDER_SCOPE_ID,
        target_read_token=target_read_token,
        protection_verifier=protection_verifier,
        rollout_verifier=policy_authority.rollout_verifier,
        resolution_policy_verifier=policy_authority.resolution_policy_verifier,
        feature_gateway=feature_truth,
        feature_event_gateway=feature_event_gateway,
        dispatch_gateway=dispatch_gateway,
        collector_content_loader=result_source.load_content,
        policy_verifier=decision_verifier,
        trusted_context_digest=trusted_context_digest,
        collector_namespace_policy=collector_namespace_policy,
        trusted_role_policy=trusted_role_policy,
        github_api_base=github_api_base,
        clock=clock,
    )
    bundle = responses.operator_bundle
    durable_truth = DurableDecisionFeatureTruthGateway(
        runtime=responses.runtime,
        feature_gateway=feature_event_gateway,
        candidate_provider=candidate_provider,
    )
    feature_truth.bind(durable_truth)
    candidate_provider.bind_runtime(responses.runtime)
    candidate_handoff = DogfoodCandidateHandoff(
        slot=slot,
        repository=config.target_repository,
        token=event_write_token,
        candidate_provider=candidate_provider,
        api_base=github_api_base,
    )
    callback_coordinator = DogfoodTrustedCallbackCoordinator(
        delegate=bundle.callback_coordinator,
        candidate_handoff=candidate_handoff,
    )
    collector = ProductionGhAwVerticalResultCollector(
        callback_coordinator=callback_coordinator,
        result_source=result_source,
        workflows=workflows,
        control_repository=control_repository,
        clock=clock,
    )
    recovery_collector = DogfoodRecoveryCollector(
        callback_coordinator=callback_coordinator,
        result_source=recovery_result_source,
        workflows=recovery_workflows,
        control_repository=control_repository,
        clock=clock,
    )

    if responses.runtime is not bundle.runtime or durable_truth.runtime is not responses.runtime:
        raise V03DogfoodCompositionError("Responses/dogfood FeatureTruth escaped unique production Store runtime")
    if (
        collector.result_source is not result_source
        or collector.callback_coordinator is not callback_coordinator
        or callback_coordinator.executor is not bundle.callback_coordinator.executor
    ):
        raise V03DogfoodCompositionError("dogfood collector escaped production authority graph")
    if responses.adapter.registration.registration_id != f"v03-dogfood:{slot.scenario}":
        raise V03DogfoodCompositionError("dogfood Responses registration escaped fixed scenario")
    if "operation.resume" in responses.backends:
        raise V03DogfoodCompositionError("server-only operation.resume leaked into dogfood Responses adapter")

    return V03DogfoodFullComposition(
        slot=slot,
        workflows=workflows,
        candidate_provider=candidate_provider,
        feature_truth_gateway=feature_truth,
        feature_event_gateway=feature_event_gateway,
        actions_transport=actions_transport,
        dispatch_gateway=dispatch_gateway,
        result_source=result_source,
        responses=responses,
        bundle=bundle,
        collector=collector,
        recovery_workflows=recovery_workflows,
        recovery_dispatch_gateway=recovery_dispatch_gateway,
        recovery_result_source=recovery_result_source,
        recovery_collector=recovery_collector,
        policy_authority=policy_authority,
    )
