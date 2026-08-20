#!/usr/bin/env python3
"""Real GitHub Actions workflow-dispatch transport for Operator external launches."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any, Callable
from urllib import error, parse, request


class GitHubActionsTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubActionsTransportConfig:
    repository: str
    token: str
    api_url: str = "https://api.github.com"
    api_version: str = "2022-11-28"
    user_agent: str = "ai-sdlc-operator-v0.3"
    receipt_poll_attempts: int = 5
    receipt_poll_seconds: float = 1.0

    def __post_init__(self):
        if "/" not in self.repository or not all(part for part in self.repository.split("/", 1)):
            raise ValueError("repository must be owner/repo")
        if not self.token:
            raise ValueError("GitHub Actions transport token is required")
        if not self.api_url.startswith("https://"):
            raise ValueError("GitHub API URL must use https")
        if self.receipt_poll_attempts < 1 or self.receipt_poll_attempts > 30:
            raise ValueError("invalid receipt poll attempts")
        if self.receipt_poll_seconds < 0 or self.receipt_poll_seconds > 10:
            raise ValueError("invalid receipt poll interval")

    @classmethod
    def from_env(cls, *, repository: str | None = None) -> "GitHubActionsTransportConfig":
        repo = repository or os.environ.get("GITHUB_REPOSITORY", "")
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        return cls(repository=repo, token=token, api_url=api_url)


class GitHubActionsWorkflowTransport:
    """Launch and query real workflow_dispatch runs using exact dispatch-key run names.

    The target gh-aw workflows use `run-name: AI-SDLC gh-aw <dispatch_key>`.
    A successful dispatch HTTP request proves GitHub accepted the launch request,
    but absence of an immediately visible run is treated as UNKNOWN rather than
    NOT_LAUNCHED. Fresh-process lookup may later correlate the exact run receipt.
    """

    def __init__(
        self,
        config: GitHubActionsTransportConfig,
        *,
        http: Callable[..., tuple[int, dict[str, str], bytes]] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.http = http or self._http
        self.sleeper = sleeper

    def _url(self, suffix: str) -> str:
        return f"{self.config.api_url.rstrip('/')}/repos/{self.config.repository}{suffix}"

    def _http(self, *, method: str, url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, str], bytes]:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.config.token}")
        req.add_header("X-GitHub-Api-Version", self.config.api_version)
        req.add_header("User-Agent", self.config.user_agent)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with request.urlopen(req, timeout=30) as response:
                return int(response.status), dict(response.headers.items()), response.read()
        except error.HTTPError as exc:
            return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read()
        except Exception as exc:
            raise GitHubActionsTransportError(f"GitHub Actions transport request failed: {exc}") from exc

    @staticmethod
    def _safe_workflow(value: str) -> str:
        if not value or "/" in value or ".." in value or not value.endswith((".yml", ".yaml")):
            raise GitHubActionsTransportError("invalid workflow filename")
        return parse.quote(value, safe="")

    @staticmethod
    def _dispatch_key(value: str) -> str:
        text = str(value or "")
        if not text.startswith("dispatch-") or len(text) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-" for ch in text):
            raise GitHubActionsTransportError("invalid external dispatch key")
        return text

    def _runs(self, *, workflow: str, ref: str) -> list[dict[str, Any]]:
        encoded = self._safe_workflow(workflow)
        query = parse.urlencode({"event": "workflow_dispatch", "branch": ref, "per_page": "100"})
        status, _, raw = self.http(method="GET", url=self._url(f"/actions/workflows/{encoded}/runs?{query}"), body=None)
        if status != 200:
            raise GitHubActionsTransportError(f"workflow run lookup failed with HTTP {status}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise GitHubActionsTransportError("workflow run lookup returned invalid JSON") from exc
        rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise GitHubActionsTransportError("workflow run lookup lacks workflow_runs")
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _run_title(row: dict[str, Any]) -> str:
        return str(row.get("display_title") or row.get("name") or "")

    def lookup(self, *, workflow: str, ref: str, dispatch_key: str) -> dict[str, Any]:
        key = self._dispatch_key(dispatch_key)
        expected = f"AI-SDLC gh-aw {key}"
        try:
            matches = [row for row in self._runs(workflow=workflow, ref=ref) if self._run_title(row) == expected]
        except GitHubActionsTransportError:
            return {"lookup_state": "UNKNOWN", "receipt_id": None}
        if not matches:
            return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
        ids = {str(row.get("id") or "") for row in matches if row.get("id") is not None}
        if len(matches) != 1 or len(ids) != 1 or "" in ids:
            # Multiple real runs for the same stable key is itself an ambiguous
            # safety state. Never pick one arbitrarily.
            return {"lookup_state": "UNKNOWN", "receipt_id": None}
        return {"lookup_state": "LAUNCHED", "receipt_id": next(iter(ids))}

    def dispatch(self, *, workflow: str, ref: str, inputs: dict[str, str]) -> dict[str, Any]:
        if not isinstance(inputs, dict):
            raise GitHubActionsTransportError("workflow inputs must be an object")
        key = self._dispatch_key(str(inputs.get("dispatch_key") or ""))
        encoded = self._safe_workflow(workflow)

        # Preflight convergence avoids knowingly creating a second real run for
        # an already-correlated stable external key.
        existing = self.lookup(workflow=workflow, ref=ref, dispatch_key=key)
        if existing["lookup_state"] in {"LAUNCHED", "UNKNOWN"}:
            return existing

        status, _, _ = self.http(
            method="POST",
            url=self._url(f"/actions/workflows/{encoded}/dispatches"),
            body={"ref": ref, "inputs": {str(k): str(v) for k, v in inputs.items()}},
        )
        if status not in {201, 204}:
            # A rejected/failed dispatch request is not proof that no launch may
            # occur. Return UNKNOWN to keep the Operator fail-closed.
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

        for attempt in range(self.config.receipt_poll_attempts):
            current = self.lookup(workflow=workflow, ref=ref, dispatch_key=key)
            if current["lookup_state"] == "LAUNCHED":
                return current
            if current["lookup_state"] == "UNKNOWN":
                return current
            if attempt + 1 < self.config.receipt_poll_attempts:
                self.sleeper(self.config.receipt_poll_seconds)
        # GitHub accepted the dispatch but the run is not observable yet. This
        # is exactly the lost-ACK / delayed-indexing boundary and must be UNKNOWN.
        return {"lookup_state": "UNKNOWN", "receipt_id": None}
