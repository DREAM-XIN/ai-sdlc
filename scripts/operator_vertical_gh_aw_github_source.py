#!/usr/bin/env python3
"""Production GitHub Actions backing for the Operation-bound gh-aw collector."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
import re
from pathlib import PurePosixPath
from typing import Any, Callable
from urllib import error, request

from jsonschema import Draft202012Validator

from operator_store_model import canonical_json, digest_json, normalize_repository, reservation_path
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError, validate_worker_result
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_collector import (
    MaterializedGhAwOutput,
    TrustedGhAwResolvedResult,
    TrustedGhAwRun,
    _build_receipts,
    _current_launch_binding,
    _validate_run,
)

_GATE_START = "<!-- AI-SDLC-GATE-RESULT\n"
_GATE_END = "\nAI-SDLC-GATE-RESULT -->"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_RE = re.compile(r"^[1-9][0-9]*$")
_PR_URI_RE = re.compile(
    r"^docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/developer-pr-(?P<number>[1-9][0-9]*)-(?P<head>[0-9a-f]{40})\.json$"
)
_COMMENT_URI_RE = re.compile(
    r"^docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/(?P<role>reviewer|qa)-comment-(?P<comment>[1-9][0-9]*)\.json$"
)


@dataclass(frozen=True)
class GitHubActionsGhAwResultSourceConfig:
    control_repository: str
    control_token: str
    target_token: str
    workflows: GhAwVerticalWorkflowMap
    collector_identity: str
    api_url: str = "https://api.github.com"
    api_version: str = "2022-11-28"
    user_agent: str = "ai-sdlc-operator-v0.3-gh-aw-collector"

    def __post_init__(self):
        normalize_repository(self.control_repository)
        if not self.control_token or not self.target_token or not self.collector_identity:
            raise ValueError("trusted GitHub result source credentials/identity are required")
        if not self.api_url.startswith("https://"):
            raise ValueError("GitHub API URL must use https")


class TargetScopedGitHubActionsGhAwResultSource:
    """Resolve one exact Worker run + Safe Output from GitHub truth.

    The durable Store receipt selects the Actions run. The caller additionally
    supplies the Store-derived callback context; it is never accepted from the
    Worker. Conclusion logs are used only for trusted workflow inputs/Safe Output
    identities and are cross-checked against that protected context.
    """

    def __init__(
        self,
        config: GitHubActionsGhAwResultSourceConfig,
        *,
        target_repository: str,
        http: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
    ):
        self.config = config
        self.target_repository = normalize_repository(target_repository)
        self.http = http or self._http

    def _api(self, repository: str, suffix: str) -> str:
        return f"{self.config.api_url.rstrip('/')}/repos/{normalize_repository(repository)}{suffix}"

    def _http(self, *, method: str, url: str, token: str) -> tuple[int, dict[str, str], bytes]:
        req = request.Request(url, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("X-GitHub-Api-Version", self.config.api_version)
        req.add_header("User-Agent", self.config.user_agent)
        try:
            with request.urlopen(req, timeout=30) as response:
                return int(response.status), dict(response.headers.items()), response.read()
        except error.HTTPError as exc:
            return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read()
        except Exception as exc:
            raise VerticalInvariantError("BLOCKED", f"trusted GitHub lookup failed: {exc}") from exc

    def _json(self, repository: str, suffix: str, token: str) -> Any:
        status, _, raw = self.http(method="GET", url=self._api(repository, suffix), token=token)
        if status != 200:
            raise VerticalInvariantError("BLOCKED", f"trusted GitHub lookup returned HTTP {status}")
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise VerticalInvariantError("BLOCKED", "trusted GitHub lookup returned invalid JSON") from exc

    def _bytes(self, repository: str, suffix: str, token: str) -> bytes:
        status, _, raw = self.http(method="GET", url=self._api(repository, suffix), token=token)
        if status != 200:
            raise VerticalInvariantError("BLOCKED", f"trusted GitHub content lookup returned HTTP {status}")
        return raw

    @staticmethod
    def _workflow_file(run: dict[str, Any]) -> str:
        path = str(run.get("path") or "")
        prefix = ".github/workflows/"
        if not path.startswith(prefix) or "/" in path[len(prefix):]:
            raise VerticalInvariantError("POLICY_DENIED", "exact run lacks trusted workflow path")
        return path[len(prefix):]

    @staticmethod
    def _log_env(log_bytes: bytes) -> dict[str, tuple[str, ...]]:
        try:
            text = log_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise VerticalInvariantError("BLOCKED", "conclusion log is not UTF-8") from exc
        found: dict[str, list[str]] = {}
        pattern = re.compile(r"^[^\n]*Z\s{3}([A-Z][A-Z0-9_]*):\s?(.*)$")
        for line in text.splitlines():
            match = pattern.match(line)
            if match and match.group(2) != "***":
                found.setdefault(match.group(1), []).append(match.group(2))
        return {key: tuple(rows) for key, rows in found.items()}

    @staticmethod
    def _one(values: dict[str, tuple[str, ...]], key: str, *, required: bool = True) -> str:
        rows = tuple(dict.fromkeys(value for value in values.get(key, ()) if value != ""))
        if len(rows) == 1:
            return rows[0]
        if not rows and not required:
            return ""
        raise VerticalInvariantError("BLOCKED", f"conclusion log lacks one exact {key} value")

    @staticmethod
    def _eq(actual: Any, expected: Any, field: str) -> None:
        if field in {"expected_revision", "operation_generation", "candidate_pr_number", "source_run_id"}:
            try:
                actual = int(actual)
            except Exception:
                pass
        if field == "target_repository":
            actual = normalize_repository(str(actual))
            expected = normalize_repository(str(expected))
        if actual != expected:
            raise VerticalInvariantError("STALE_REVISION", f"trusted gh-aw result identity mismatch: {field}")

    def _exact_run(self, *, external_dispatch_key: str, receipt: str) -> tuple[dict[str, Any], str, bytes]:
        if not _RUN_RE.fullmatch(str(receipt or "")):
            raise VerticalInvariantError("INVALID_REQUEST", "runtime receipt must be an exact Actions run id")
        run_id = int(receipt)
        run = self._json(self.config.control_repository, f"/actions/runs/{run_id}", self.config.control_token)
        if not isinstance(run, dict) or int(run.get("id") or 0) != run_id:
            raise VerticalInvariantError("BLOCKED", "runtime receipt did not resolve exact Actions run")
        workflow = self._workflow_file(run)
        if workflow not in {
            self.config.workflows.developer_workflow,
            self.config.workflows.reviewer_workflow,
            self.config.workflows.qa_workflow,
        }:
            raise VerticalInvariantError("POLICY_DENIED", "runtime receipt points to untrusted workflow")
        if str(run.get("display_title") or "") != f"AI-SDLC gh-aw {external_dispatch_key}":
            raise VerticalInvariantError("STALE_REVISION", "run-name is not bound to stable dispatch key")
        if run.get("event") != "workflow_dispatch" or run.get("status") != "completed" or run.get("conclusion") != "success":
            raise VerticalInvariantError("BLOCKED", "exact gh-aw run is not successful workflow_dispatch")
        if str(run.get("head_branch") or "") != self.config.workflows.default_branch:
            raise VerticalInvariantError("POLICY_DENIED", "exact gh-aw run is not from trusted default branch")
        if not _SHA_RE.fullmatch(str(run.get("head_sha") or "")):
            raise VerticalInvariantError("BLOCKED", "exact gh-aw run lacks immutable source SHA")

        jobs_doc = self._json(self.config.control_repository, f"/actions/runs/{run_id}/jobs?per_page=100", self.config.control_token)
        jobs = jobs_doc.get("jobs") if isinstance(jobs_doc, dict) else None
        if not isinstance(jobs, list):
            raise VerticalInvariantError("BLOCKED", "exact gh-aw run lacks jobs")
        safe = [row for row in jobs if isinstance(row, dict) and row.get("name") == "safe_outputs"]
        conclusion = [row for row in jobs if isinstance(row, dict) and row.get("name") == "conclusion"]
        if len(safe) != 1 or safe[0].get("conclusion") != "success":
            raise VerticalInvariantError("BLOCKED", "gh-aw Safe Output job did not succeed")
        if len(conclusion) != 1 or conclusion[0].get("conclusion") != "success":
            raise VerticalInvariantError("BLOCKED", "gh-aw conclusion job did not succeed")
        job_id = conclusion[0].get("id")
        if not isinstance(job_id, int) or job_id <= 0:
            raise VerticalInvariantError("BLOCKED", "gh-aw conclusion job lacks exact identity")
        logs = self._bytes(self.config.control_repository, f"/actions/jobs/{job_id}/logs", self.config.control_token)
        return run, workflow, logs

    def _gate_observation(
        self,
        *,
        values: dict[str, tuple[str, ...]],
        run_id: int,
        workflow: str,
        trusted: dict[str, Any],
    ) -> dict[str, Any]:
        expected_ref = (
            f"{self.config.control_repository}/.github/workflows/{workflow}"
            f"@refs/heads/{self.config.workflows.default_branch}"
        )
        observed = {
            "source_run_id": self._one(values, "SOURCE_RUN_ID"),
            "source_workflow_ref": self._one(values, "SOURCE_WORKFLOW_REF"),
            "target_repository": self._one(values, "TARGET_REPOSITORY"),
            "target_ref": self._one(values, "TARGET_REF"),
            "feature_id": self._one(values, "FEATURE_ID"),
            "expected_revision": self._one(values, "EXPECTED_REVISION"),
            "stage": self._one(values, "STAGE"),
            "role": self._one(values, "ROLE"),
            "task_id": self._one(values, "TRUSTED_TASK_ID"),
            "candidate_pr_number": self._one(values, "CANDIDATE_PR_NUMBER"),
            "candidate_head_sha": self._one(values, "CANDIDATE_HEAD_SHA"),
            "comment_id": self._one(values, "COMMENT_ID"),
            "comment_url": self._one(values, "COMMENT_URL"),
        }
        self._eq(observed["source_run_id"], run_id, "source_run_id")
        if observed["source_workflow_ref"].lower() != expected_ref.lower():
            raise VerticalInvariantError("POLICY_DENIED", "Gate source workflow ref mismatch")
        for field, key in (
            ("target_repository", "target_repository"),
            ("target_ref", "target_ref"),
            ("feature_id", "feature_id"),
            ("expected_revision", "expected_revision"),
            ("stage", "feature_stage"),
            ("role", "role"),
            ("candidate_head_sha", "launch_candidate_head_sha"),
        ):
            self._eq(observed[field], trusted[key], field)
        return observed

    def _developer_observation(
        self,
        *,
        values: dict[str, tuple[str, ...]],
        run_id: int,
        external_dispatch_key: str,
        trusted: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            payload = json.loads(self._one(values, "TASK_PAYLOAD"))
            task = payload["task"]
            vertical = payload["feature_context"]["vertical"]
        except Exception as exc:
            raise VerticalInvariantError("BLOCKED", "Developer conclusion task payload is invalid") from exc
        expected_run_url = f"https://github.com/{self.config.control_repository}/actions/runs/{run_id}"
        if self._one(values, "RUN_URL").lower() != expected_run_url.lower():
            raise VerticalInvariantError("STALE_REVISION", "Developer run URL mismatch")
        observed = {
            "target_repository": self._one(values, "TARGET_REPOSITORY"),
            "target_ref": self._one(values, "TARGET_REF"),
            "feature_id": self._one(values, "FEATURE_ID"),
            "expected_revision": self._one(values, "EXPECTED_REVISION"),
            "stage": self._one(values, "STAGE"),
            "role": str(task.get("role") or ""),
            "task_id": str(task.get("id") or ""),
            "operation_id": str(vertical.get("operation_id") or ""),
            "operation_generation": vertical.get("operation_generation"),
            "operation_profile": str(vertical.get("profile") or ""),
            "semantic_effect_key": str(vertical.get("semantic_effect_key") or ""),
            "external_dispatch_key": str(vertical.get("external_dispatch_key") or ""),
            "dispatch_id": str(vertical.get("dispatch_id") or ""),
            "launch_candidate_head_sha": vertical.get("candidate_head_sha") or None,
            "pr_url": self._one(values, "PR_URL"),
        }
        for field, key in (
            ("target_repository", "target_repository"),
            ("target_ref", "target_ref"),
            ("feature_id", "feature_id"),
            ("expected_revision", "expected_revision"),
            ("stage", "feature_stage"),
            ("role", "role"),
            ("operation_id", "operation_id"),
            ("operation_generation", "operation_generation"),
            ("operation_profile", "operation_profile"),
            ("semantic_effect_key", "semantic_effect_key"),
            ("external_dispatch_key", "external_dispatch_key"),
            ("dispatch_id", "dispatch_id"),
            ("launch_candidate_head_sha", "launch_candidate_head_sha"),
        ):
            self._eq(observed[field], trusted[key], field)
        self._eq(observed["external_dispatch_key"], external_dispatch_key, "external_dispatch_key")
        return observed

    @staticmethod
    def _gate_payload(body: str) -> dict[str, Any]:
        if not body.startswith(_GATE_START) or body.count(_GATE_START) != 1 or body.count(_GATE_END) != 1:
            raise VerticalInvariantError("BLOCKED", "Gate Safe Output comment has invalid machine envelope")
        try:
            payload = json.loads(body[len(_GATE_START):body.index(_GATE_END)])
        except Exception as exc:
            raise VerticalInvariantError("BLOCKED", "Gate Safe Output envelope is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise VerticalInvariantError("BLOCKED", "Gate Safe Output envelope is not an object")
        return payload

    @staticmethod
    def _schema_validate(payload: dict[str, Any], role: str) -> None:
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        name = "reviewer-result.schema.json" if role == "reviewer" else "qa-result.schema.json"
        schema = json.loads((root / "runtimes" / "gh-aw" / name).read_text(encoding="utf-8"))
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.absolute_path))
        if errors:
            where = ".".join(str(v) for v in errors[0].absolute_path) or "<root>"
            raise VerticalInvariantError("INVALID_REQUEST", f"Gate result {where}: {errors[0].message}")

    def _reviewer_payload(self, external: dict[str, Any]) -> dict[str, Any]:
        findings = [
            {"severity": str(row["severity"]), "code": str(row["code"]), "summary": str(row["message"])}
            for row in external.get("findings", [])
        ]
        verdict = str(external["verdict"])
        if verdict == "PASS" and any(row["severity"] in {"BLOCKER", "MAJOR"} for row in findings):
            raise VerticalInvariantError("INVALID_REQUEST", "Reviewer PASS contains blocking findings")
        if verdict == "PASS" and any(str(row.get("status")) != "pass" for row in external.get("evidence", [])):
            raise VerticalInvariantError("INVALID_REQUEST", "Reviewer PASS contains non-passing evidence")
        return {
            "verdict": verdict,
            "summary": str(external.get("reason") or f"Trusted gh-aw reviewer verdict {verdict}."),
            "findings": findings,
            "outputs": [{"label": "review-evidence", "kind": "evidence"}],
        }

    def _qa_payload(self, external: dict[str, Any]) -> dict[str, Any]:
        verdict = str(external["verdict"])
        checks = []
        for prefix, rows, label in (
            ("qa-check", external.get("checks", []), "name"),
            ("qa-coverage", external.get("coverage", []), "criterion"),
        ):
            for index, row in enumerate(rows):
                status = str(row["status"])
                checks.append({
                    "code": f"{prefix}-{index + 1}",
                    "status": "PASS" if status == "pass" else "FAIL" if status == "fail" else "SKIP",
                    "summary": str(row.get("detail") or row.get("evidence") or row[label]),
                })
        if verdict == "PASS" and any(row["status"] != "PASS" for row in checks):
            raise VerticalInvariantError("INVALID_REQUEST", "QA PASS contains non-passing checks/coverage")
        if verdict == "PASS" and any(str(row.get("status")) != "pass" for row in external.get("evidence", [])):
            raise VerticalInvariantError("INVALID_REQUEST", "QA PASS contains non-passing evidence")
        return {
            "verdict": "PASS" if verdict == "PASS" else "BLOCKED",
            "summary": str(external.get("reason") or f"Trusted gh-aw QA verdict {verdict}."),
            "checks": checks,
            "outputs": [{"label": "verification-evidence", "kind": "evidence"}],
        }

    def resolve(
        self,
        *,
        external_dispatch_key: str,
        expected_receipt_identity: str,
        trusted_context: dict[str, Any],
    ) -> TrustedGhAwResolvedResult:
        if not isinstance(trusted_context, dict) or normalize_repository(str(trusted_context.get("target_repository") or "")) != self.target_repository:
            raise VerticalInvariantError("POLICY_DENIED", "protected Store context is outside target-scoped collector")
        run, workflow, logs = self._exact_run(
            external_dispatch_key=external_dispatch_key,
            receipt=expected_receipt_identity,
        )
        values = self._log_env(logs)
        role = str(trusted_context.get("role") or "")
        if workflow != self.config.workflows.workflow_for(role):
            raise VerticalInvariantError("POLICY_DENIED", "Store role does not select exact trusted workflow")
        if role == "developer":
            observed = self._developer_observation(
                values=values,
                run_id=int(run["id"]),
                external_dispatch_key=external_dispatch_key,
                trusted=trusted_context,
            )
        elif role in {"reviewer", "qa"}:
            observed = self._gate_observation(
                values=values,
                run_id=int(run["id"]),
                workflow=workflow,
                trusted=trusted_context,
            )
        else:
            raise VerticalInvariantError("POLICY_DENIED", "unsupported trusted gh-aw Vertical role")

        task_id = str(observed.get("task_id") or "")
        if not task_id:
            raise VerticalInvariantError("BLOCKED", "trusted conclusion log lacks task identity")
        candidate_pr_number = None
        candidate_head_sha = None
        dispatch_id = str(trusted_context["dispatch_id"])
        feature_id = str(trusted_context["feature_id"])

        if role == "developer":
            pr_url = str(observed.get("pr_url") or "")
            match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)", pr_url)
            if not match or normalize_repository(match.group(1)) != self.target_repository:
                raise VerticalInvariantError("STALE_REVISION", "Developer Safe Output PR URL is not canonical")
            candidate_pr_number = int(match.group(2))
            pr = self._json(self.target_repository, f"/pulls/{candidate_pr_number}", self.config.target_token)
            if not isinstance(pr, dict) or int(pr.get("number") or 0) != candidate_pr_number:
                raise VerticalInvariantError("BLOCKED", "Developer Draft PR cannot be re-resolved")
            if str(pr.get("state") or "") != "open" or pr.get("draft") is not True or str(pr.get("html_url") or "") != pr_url:
                raise VerticalInvariantError("BLOCKED", "Developer Safe Output is not the exact open Draft PR")
            if str((pr.get("base") or {}).get("ref") or "") != str(trusted_context["target_ref"]):
                raise VerticalInvariantError("STALE_REVISION", "Developer Draft PR base ref mismatch")
            candidate_head_sha = str((pr.get("head") or {}).get("sha") or "")
            head_ref = str((pr.get("head") or {}).get("ref") or "")
            expected_prefix = f"gh-aw/{feature_id}-{run['id']}-v{trusted_context['expected_revision']}"
            if not _SHA_RE.fullmatch(candidate_head_sha) or not head_ref.startswith(expected_prefix):
                raise VerticalInvariantError("POLICY_DENIED", "Developer Draft PR head is not exact run-bound Safe Output")
            role_payload = {
                "status": "COMPLETED",
                "summary": f"Trusted gh-aw Draft PR #{candidate_pr_number} completed for {task_id}.",
                "outputs": [{"label": "implementation", "kind": "artifact"}],
            }
            uri = f"docs/features/{feature_id}/worker-runs/{dispatch_id}/developer-pr-{candidate_pr_number}-{candidate_head_sha}.json"
            outputs = (MaterializedGhAwOutput("implementation", "artifact", "application/json", uri),)
        else:
            candidate_pr_number = int(observed["candidate_pr_number"])
            candidate_head_sha = str(observed["candidate_head_sha"])
            comment_id = int(observed["comment_id"])
            comment_url = str(observed["comment_url"])
            comment = self._json(self.target_repository, f"/issues/comments/{comment_id}", self.config.target_token)
            if not isinstance(comment, dict) or int(comment.get("id") or 0) != comment_id:
                raise VerticalInvariantError("BLOCKED", "Gate Safe Output comment cannot be re-resolved")
            if str(comment.get("html_url") or "") != comment_url:
                raise VerticalInvariantError("STALE_REVISION", "Gate Safe Output comment URL changed")
            issue_url = f"{self.config.api_url.rstrip('/')}/repos/{self.target_repository}/issues/{candidate_pr_number}"
            if str(comment.get("issue_url") or "").lower() != issue_url.lower() or str((comment.get("user") or {}).get("type") or "") != "Bot":
                raise VerticalInvariantError("POLICY_DENIED", "Gate Safe Output comment provenance is not trusted")
            external = self._gate_payload(str(comment.get("body") or ""))
            self._schema_validate(external, role)
            expected = {
                "contract": "ai-sdlc-gh-aw-reviewer-result-v0.1" if role == "reviewer" else "ai-sdlc-gh-aw-qa-result-v0.1",
                "feature_id": feature_id,
                "task_id": task_id,
                "expected_revision": int(trusted_context["expected_revision"]),
                "target_repository": self.target_repository,
                "target_ref": str(trusted_context["target_ref"]),
                "stage": str(trusted_context["feature_stage"]),
                "role": role,
                "candidate_pr_number": candidate_pr_number,
                "candidate_head_sha": candidate_head_sha,
            }
            for field, value in expected.items():
                self._eq(external.get(field), value, field)
            role_payload = self._reviewer_payload(external) if role == "reviewer" else self._qa_payload(external)
            label = "review-evidence" if role == "reviewer" else "verification-evidence"
            uri = f"docs/features/{feature_id}/worker-runs/{dispatch_id}/{role}-comment-{comment_id}.json"
            outputs = (MaterializedGhAwOutput(label, "evidence", "application/json", uri),)

        trusted_run = TrustedGhAwRun(
            run_id=int(run["id"]),
            run_url=str(run.get("html_url") or ""),
            receipt_identity=str(run["id"]),
            control_repository=normalize_repository(self.config.control_repository),
            workflow_file=workflow,
            workflow_ref=self.config.workflows.default_branch,
            event="workflow_dispatch",
            status="completed",
            conclusion="success",
            display_title=str(run["display_title"]),
            external_dispatch_key=external_dispatch_key,
            role=role,
            task_id=task_id,
            worker_identity=f"gh-aw:{workflow}@{run['head_sha']}",
            collector_identity=self.config.collector_identity,
            candidate_pr_number=candidate_pr_number,
            candidate_head_sha=candidate_head_sha,
        )
        return TrustedGhAwResolvedResult(run=trusted_run, role_payload=role_payload, outputs=outputs)

    @staticmethod
    def _canonical_pr_bytes(repository: str, pr: dict[str, Any]) -> bytes:
        row = {
            "repository": normalize_repository(repository),
            "pr_number": int(pr["number"]),
            "pr_url": str(pr["html_url"]),
            "base_ref": str((pr.get("base") or {})["ref"]),
            "head_sha": str((pr.get("head") or {})["sha"]),
        }
        return (canonical_json(row) + "\n").encode("utf-8")

    def load_content(self, uri: str) -> bytes:
        pr_match = _PR_URI_RE.fullmatch(str(uri or ""))
        if pr_match:
            pr = self._json(self.target_repository, f"/pulls/{int(pr_match.group('number'))}", self.config.target_token)
            if not isinstance(pr, dict) or str((pr.get("head") or {}).get("sha") or "") != pr_match.group("head"):
                raise VerticalInvariantError("BLOCKED", "trusted Developer PR content changed")
            return self._canonical_pr_bytes(self.target_repository, pr)
        comment_match = _COMMENT_URI_RE.fullmatch(str(uri or ""))
        if comment_match:
            comment = self._json(self.target_repository, f"/issues/comments/{int(comment_match.group('comment'))}", self.config.target_token)
            if not isinstance(comment, dict):
                raise VerticalInvariantError("BLOCKED", "trusted Gate comment content changed")
            return (canonical_json(self._gate_payload(str(comment.get("body") or ""))) + "\n").encode("utf-8")
        raise VerticalInvariantError("POLICY_DENIED", "unrecognized trusted gh-aw collector URI")


class ProductionGhAwVerticalResultCollector:
    """Wire the production source into the accepted Operation-bound callback path."""

    def __init__(self, *, callback_coordinator, result_source, workflows, control_repository: str, clock):
        self.callback_coordinator = callback_coordinator
        self.result_source = result_source
        self.workflows = workflows
        self.control_repository = normalize_repository(control_repository)
        self.clock = clock
        if not callable(clock):
            raise ValueError("trusted gh-aw collector clock is required")

    def handle(self, *, operation_id: str, external_dispatch_key: str) -> dict[str, Any]:
        executor = self.callback_coordinator.executor
        snapshot = executor.runtime.backend.read_snapshot()
        projection, launch, receipt = _current_launch_binding(
            snapshot, operation_id=operation_id, external_dispatch_key=external_dispatch_key
        )
        semantic_key = str(launch["semantic_effect_key"])
        reservation = snapshot.get(reservation_path(semantic_key))
        if not isinstance(reservation, dict):
            raise VerticalInvariantError("INTERNAL_FAILURE", "durable semantic reservation is missing")
        trusted = {
            "operation_id": operation_id,
            "operation_generation": int(projection["generation"]),
            "operation_profile": VERTICAL_PROFILE,
            "semantic_effect_key": semantic_key,
            "external_dispatch_key": external_dispatch_key,
            "dispatch_id": str(launch["dispatch_id"]),
            "target_repository": normalize_repository(str(projection["target_repository"])),
            "target_ref": executor.config.target_ref,
            "feature_id": str(projection["feature_id"]),
            "expected_revision": int(projection["expected_feature_revision"]),
            "feature_stage": str(launch["stage"]),
            "role": str(launch["role"]),
            "launch_candidate_head_sha": launch.get("candidate_head_sha"),
        }
        resolved = self.result_source.resolve(
            external_dispatch_key=external_dispatch_key,
            expected_receipt_identity=receipt,
            trusted_context=trusted,
        )
        _validate_run(
            resolved.run,
            control_repository=self.control_repository,
            workflows=self.workflows,
            launch=launch,
            expected_receipt_identity=receipt,
            external_dispatch_key=external_dispatch_key,
            reservation=reservation,
        )
        role = str(launch["role"])
        worker_payload = validate_worker_result(role, resolved.role_payload)
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
            role=role,
            candidate_pr_number=resolved.run.candidate_pr_number if role in {"reviewer", "qa"} else None,
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
            raise VerticalInvariantError("BLOCKED", "trusted gh-aw materialized outputs do not match role result")
        callback_id = "gh-aw-callback-" + digest_json({
            "operation_id": operation_id,
            "generation": context.operation_generation,
            "external_dispatch_key": external_dispatch_key,
            "runtime_receipt_identity": receipt,
            "run_id": resolved.run.run_id,
        })[:24]
        return self.callback_coordinator.handle(
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
        )
