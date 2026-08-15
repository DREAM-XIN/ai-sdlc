#!/usr/bin/env python3
"""Pure GitHub Actions transport for trusted Vertical gh-aw external effects.

This adapter does not persist Feature/Store/Gate state. The protected Vertical
runtime owns reservation and launch linearization; this class performs at most one
workflow_dispatch transport attempt and provides exhaustive stable-key lookup.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Callable
from urllib import error, parse, request

from operator_store_model import normalize_repository
from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap

_DISPATCH_KEY_RE = re.compile(r"^dispatch-[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^[1-9][0-9]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class GitHubActionsWorkflowTransportConfig:
    control_repository: str
    token: str
    workflows: GhAwVerticalWorkflowMap
    api_url: str = "https://api.github.com"
    api_version: str = "2022-11-28"
    user_agent: str = "ai-sdlc-operator-v0.3-gh-aw-transport"
    page_size: int = 100
    max_lookup_pages: int = 20
    launch_poll_attempts: int = 8
    launch_poll_seconds: float = 1.0

    def __post_init__(self):
        object.__setattr__(self, "control_repository", normalize_repository(self.control_repository))
        if not self.token:
            raise ValueError("trusted GitHub Actions transport token is required")
        if not self.api_url.startswith("https://"):
            raise ValueError("GitHub API URL must use https")
        if not 1 <= self.page_size <= 100:
            raise ValueError("GitHub Actions lookup page_size must be 1..100")
        if not 1 <= self.max_lookup_pages <= 100:
            raise ValueError("GitHub Actions lookup max pages must be 1..100")
        if not 1 <= self.launch_poll_attempts <= 60:
            raise ValueError("GitHub Actions launch polling bound must be 1..60")
        if self.launch_poll_seconds < 0 or self.launch_poll_seconds > 30:
            raise ValueError("GitHub Actions launch poll interval is invalid")


class GitHubActionsVerticalGhAwTransport:
    """Production `GhAwWorkflowTransport` over GitHub's Actions REST API."""

    def __init__(
        self,
        config: GitHubActionsWorkflowTransportConfig,
        *,
        http: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.config = config
        self.http = http or self._http
        self.sleeper = sleeper or time.sleep
        self._workflow_order = (
            config.workflows.developer_workflow,
            config.workflows.reviewer_workflow,
            config.workflows.qa_workflow,
        )
        self._trusted_workflows = frozenset(self._workflow_order)
        if len(self._trusted_workflows) != 3:
            raise ValueError("Vertical gh-aw role workflows must be distinct")

    def _api(self, suffix: str) -> str:
        return (
            f"{self.config.api_url.rstrip('/')}/repos/"
            f"{self.config.control_repository}{suffix}"
        )

    def _http(
        self,
        *,
        method: str,
        url: str,
        token: str,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        req = request.Request(url, data=body, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("X-GitHub-Api-Version", self.config.api_version)
        req.add_header("User-Agent", self.config.user_agent)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=30) as response:
                return int(response.status), dict(response.headers.items()), response.read()
        except error.HTTPError as exc:
            return (
                int(exc.code),
                dict(exc.headers.items()) if exc.headers else {},
                exc.read(),
            )
        except Exception as exc:
            raise VerticalInvariantError(
                "BLOCKED", f"trusted GitHub Actions transport failed: {exc}"
            ) from exc

    def _role_for_workflow(self, workflow: str) -> str:
        matches = tuple(
            role
            for role in ("developer", "reviewer", "qa")
            if self.config.workflows.workflow_for(role) == workflow
        )
        if len(matches) != 1:
            raise VerticalInvariantError(
                "POLICY_DENIED", "workflow is not one exact trusted Vertical role workflow"
            )
        return matches[0]

    def _validate_lookup_identity(
        self, *, workflow: str, ref: str, dispatch_key: str
    ) -> str:
        if workflow not in self._trusted_workflows:
            raise VerticalInvariantError("POLICY_DENIED", "untrusted gh-aw workflow")
        if ref != self.config.workflows.default_branch:
            raise VerticalInvariantError("POLICY_DENIED", "gh-aw transport requires trusted default branch")
        if not _DISPATCH_KEY_RE.fullmatch(str(dispatch_key or "")):
            raise VerticalInvariantError("INVALID_REQUEST", "invalid stable external dispatch key")
        return self._role_for_workflow(workflow)

    @staticmethod
    def _require_int(value: Any, *, field: str, minimum: int = 0) -> int:
        try:
            parsed = int(value)
        except Exception as exc:
            raise VerticalInvariantError("INVALID_REQUEST", f"invalid {field}") from exc
        if parsed < minimum:
            raise VerticalInvariantError("INVALID_REQUEST", f"invalid {field}")
        return parsed

    def _validate_dispatch_inputs(
        self, *, workflow: str, ref: str, inputs: dict[str, str]
    ) -> str:
        if not isinstance(inputs, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in inputs.items()
        ):
            raise VerticalInvariantError("INVALID_REQUEST", "gh-aw transport inputs must be string map")
        dispatch_key = str(inputs.get("dispatch_key") or "")
        role = self._validate_lookup_identity(
            workflow=workflow, ref=ref, dispatch_key=dispatch_key
        )
        expected_stage = (
            "verification" if role == "qa" else "code-review" if role == "reviewer" else "implementation"
        )
        required = {
            "feature_id",
            "expected_revision",
            "target_repository",
            "target_owner",
            "target_repo_name",
            "target_ref",
            "stage",
            "role",
            "task_payload",
            "dispatch_key",
        }
        if role in {"reviewer", "qa"}:
            required |= {"candidate_pr_number", "candidate_head_sha"}
        if set(inputs) != required:
            raise VerticalInvariantError(
                "POLICY_DENIED", "gh-aw transport input surface differs from trusted Vertical contract"
            )
        if inputs.get("role") != role or inputs.get("stage") != expected_stage:
            raise VerticalInvariantError("POLICY_DENIED", "gh-aw workflow/role/stage binding mismatch")
        repository = normalize_repository(inputs["target_repository"])
        if repository != normalize_repository(
            f"{inputs['target_owner']}/{inputs['target_repo_name']}"
        ):
            raise VerticalInvariantError("INVALID_REQUEST", "gh-aw target repository parts mismatch")
        revision = self._require_int(inputs["expected_revision"], field="expected revision")
        if not inputs["feature_id"] or not inputs["target_ref"]:
            raise VerticalInvariantError("INVALID_REQUEST", "gh-aw target identity is incomplete")
        candidate_pr: int | None = None
        candidate_head: str | None = None
        if role in {"reviewer", "qa"}:
            candidate_pr = self._require_int(
                inputs["candidate_pr_number"], field="Gate candidate PR", minimum=1
            )
            candidate_head = inputs["candidate_head_sha"]
            if not _SHA_RE.fullmatch(candidate_head):
                raise VerticalInvariantError("INVALID_REQUEST", "invalid Gate candidate binding")
        try:
            payload = json.loads(inputs["task_payload"])
        except Exception as exc:
            raise VerticalInvariantError("INVALID_REQUEST", "gh-aw task payload is not JSON") from exc
        if not isinstance(payload, dict):
            raise VerticalInvariantError("INVALID_REQUEST", "gh-aw task payload is not an object")
        task = payload.get("task")
        context = payload.get("feature_context")
        vertical = context.get("vertical") if isinstance(context, dict) else None
        if not isinstance(task, dict) or not isinstance(context, dict) or not isinstance(vertical, dict):
            raise VerticalInvariantError("POLICY_DENIED", "gh-aw task payload lacks trusted Vertical structure")
        if (
            payload.get("contract") != "ai-sdlc-task-v0.1"
            or task.get("role") != role
            or str(context.get("id") or "") != inputs["feature_id"]
            or normalize_repository(str(context.get("repository") or "")) != repository
            or vertical.get("profile") != VERTICAL_PROFILE
            or vertical.get("external_dispatch_key") != dispatch_key
            or self._require_int(
                vertical.get("expected_revision"), field="task payload expected revision"
            )
            != revision
            or vertical.get("candidate_head_sha") != candidate_head
        ):
            raise VerticalInvariantError(
                "POLICY_DENIED", "gh-aw task payload is not bound to trusted Vertical dispatch"
            )
        if role in {"reviewer", "qa"} and candidate_pr is None:
            raise VerticalInvariantError("POLICY_DENIED", "Gate transport lacks candidate PR binding")
        return dispatch_key

    def _workflow_runs_url(self, *, workflow: str, ref: str, page: int) -> str:
        query = parse.urlencode(
            {
                "event": "workflow_dispatch",
                "branch": ref,
                "per_page": self.config.page_size,
                "page": page,
            }
        )
        return self._api(f"/actions/workflows/{workflow}/runs?{query}")

    @staticmethod
    def _row_run_id(run: dict[str, Any]) -> int | None:
        try:
            run_id = int(run.get("id") or 0)
        except Exception:
            return None
        if run_id <= 0 or not _RUN_ID_RE.fullmatch(str(run_id)):
            return None
        return run_id

    def _trusted_match(
        self, *, run: dict[str, Any], workflow: str, ref: str, dispatch_key: str
    ) -> int | None:
        run_id = self._row_run_id(run)
        if run_id is None:
            return None
        if (
            str(run.get("display_title") or "") != f"AI-SDLC gh-aw {dispatch_key}"
            or run.get("event") != "workflow_dispatch"
            or str(run.get("head_branch") or "") != ref
            or str(run.get("path") or "") != f".github/workflows/{workflow}"
        ):
            return None
        return run_id

    def lookup(
        self, *, workflow: str, ref: str, dispatch_key: str
    ) -> dict[str, Any]:
        self._validate_lookup_identity(
            workflow=workflow, ref=ref, dispatch_key=dispatch_key
        )
        total_count: int | None = None
        all_run_ids: set[int] = set()
        matching_ids: set[int] = set()
        for page in range(1, self.config.max_lookup_pages + 1):
            try:
                status, _, raw = self.http(
                    method="GET",
                    url=self._workflow_runs_url(workflow=workflow, ref=ref, page=page),
                    token=self.config.token,
                    body=None,
                )
            except Exception:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            if status != 200:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            try:
                document = json.loads(raw.decode("utf-8"))
                rows = document["workflow_runs"]
                count = int(document["total_count"])
            except Exception:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            if not isinstance(rows, list) or count < 0:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            if total_count is None:
                total_count = count
            elif total_count != count:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            for row in rows:
                if not isinstance(row, dict):
                    return {"lookup_state": "UNKNOWN", "receipt_id": None}
                run_id = self._row_run_id(row)
                if run_id is None or run_id in all_run_ids:
                    return {"lookup_state": "UNKNOWN", "receipt_id": None}
                all_run_ids.add(run_id)
                match = self._trusted_match(
                    run=row,
                    workflow=workflow,
                    ref=ref,
                    dispatch_key=dispatch_key,
                )
                if match is not None:
                    matching_ids.add(match)
                    if len(matching_ids) > 1:
                        return {"lookup_state": "UNKNOWN", "receipt_id": None}
            if total_count == len(all_run_ids):
                if len(matching_ids) == 1:
                    return {
                        "lookup_state": "LAUNCHED",
                        "receipt_id": str(next(iter(matching_ids))),
                    }
                return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
            if len(all_run_ids) > total_count or not rows:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
        return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def _global_preflight(
        self, *, selected_workflow: str, ref: str, dispatch_key: str
    ) -> dict[str, Any]:
        launched: list[tuple[str, dict[str, Any]]] = []
        for workflow in self._workflow_order:
            receipt = self.lookup(
                workflow=workflow,
                ref=ref,
                dispatch_key=dispatch_key,
            )
            state = receipt.get("lookup_state")
            if state == "UNKNOWN":
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            if state == "LAUNCHED":
                launched.append((workflow, receipt))
            elif state != "NOT_LAUNCHED":
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
        if not launched:
            return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
        if len(launched) == 1 and launched[0][0] == selected_workflow:
            return launched[0][1]
        # Same semantic dispatch key appearing in another trusted role workflow,
        # or more than one workflow, is a cross-role collision/duplicate effect.
        return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def _poll_after_create_attempt(
        self, *, workflow: str, ref: str, dispatch_key: str
    ) -> dict[str, Any]:
        """Resolve a just-attempted external create without ever proving safe absence.

        Once a workflow_dispatch POST has crossed the transport boundary, a currently
        empty eventually-consistent list is not authority to declare NOT_LAUNCHED.
        The only positive convergence is the exact selected-role run. Everything
        else remains UNKNOWN/fail-closed until a later trusted read can observe it.
        """
        for attempt in range(self.config.launch_poll_attempts):
            receipt = self._global_preflight(
                selected_workflow=workflow,
                ref=ref,
                dispatch_key=dispatch_key,
            )
            if receipt.get("lookup_state") == "LAUNCHED":
                return receipt
            if receipt.get("lookup_state") == "UNKNOWN":
                return receipt
            if attempt + 1 < self.config.launch_poll_attempts:
                self.sleeper(self.config.launch_poll_seconds)
        return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def dispatch(
        self, *, workflow: str, ref: str, inputs: dict[str, str]
    ) -> dict[str, Any]:
        dispatch_key = self._validate_dispatch_inputs(
            workflow=workflow, ref=ref, inputs=inputs
        )

        # Stable-key preflight is global across all trusted role workflows. An
        # already-launched run for this selected role is adopted without another
        # external effect. Any UNKNOWN or cross-role stable-key collision forbids
        # speculative dispatch. All three workflows must exhaustively prove
        # NOT_LAUNCHED before one POST is allowed.
        before = self._global_preflight(
            selected_workflow=workflow,
            ref=ref,
            dispatch_key=dispatch_key,
        )
        if before.get("lookup_state") == "LAUNCHED":
            return before
        if before.get("lookup_state") != "NOT_LAUNCHED":
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

        body = json.dumps(
            {"ref": ref, "inputs": dict(inputs)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        # Exactly one transport attempt after global exhaustive absence proof.
        # A lost/ambiguous acknowledgement is handled *inside* the transport so
        # the executor never falls back to a standalone lookup that could
        # misclassify eventual-consistency absence as NOT_LAUNCHED.
        try:
            status, _, _ = self.http(
                method="POST",
                url=self._api(f"/actions/workflows/{workflow}/dispatches"),
                token=self.config.token,
                body=body,
            )
        except Exception:
            return self._poll_after_create_attempt(
                workflow=workflow,
                ref=ref,
                dispatch_key=dispatch_key,
            )
        if status != 204:
            raise VerticalInvariantError(
                "BLOCKED", f"trusted workflow dispatch returned HTTP {status}"
            )

        # HTTP 204 proves GitHub accepted the request. The same post-create
        # resolver is used for both acknowledged and ambiguous create attempts:
        # only an exact visible run may become LAUNCHED; absence is UNKNOWN.
        return self._poll_after_create_attempt(
            workflow=workflow,
            ref=ref,
            dispatch_key=dispatch_key,
        )