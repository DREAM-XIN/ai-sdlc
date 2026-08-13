#!/usr/bin/env python3
"""Bounded stabilization and redacted diagnostics for ruleset write attestation.

This module deliberately does not weaken the causal marker -> canonical proof in
``operator_store_github_ruleset_attested``. It only gives the Administration
history surface more time to converge and records a non-sensitive classification
of the last observation when convergence still fails.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from operator_store_github_ruleset_attested import (
    HISTORY_PAGE,
    AttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_provision import RulesetProvisioningError

DEFAULT_STABILIZATION_ATTEMPTS = 60
DEFAULT_STABILIZATION_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class HistoryObservation:
    category: str
    version_id: int | None = None
    state_name: str | None = None
    mismatch_fields: tuple[str, ...] = ()
    http_status: int | None = None

    def render(self) -> str:
        parts = [f"category={self.category}"]
        if self.http_status is not None:
            parts.append(f"http_status={self.http_status}")
        if self.version_id is not None:
            parts.append(f"version_id={self.version_id}")
        if self.state_name is not None:
            safe_name = self.state_name[:160].replace("\n", " ").replace("\r", " ")
            parts.append(f"state_name={safe_name!r}")
        if self.mismatch_fields:
            parts.append("mismatch_fields=" + ",".join(self.mismatch_fields))
        return " ".join(parts)


def _expected_state(*, repository: str, ruleset_id: int, payload: dict) -> dict:
    return {
        "id": ruleset_id,
        "name": payload.get("name"),
        "target": payload.get("target"),
        "source_type": "Repository",
        "source": repository,
        "enforcement": payload.get("enforcement"),
        "conditions": payload.get("conditions"),
        "bypass_actors": payload.get("bypass_actors"),
        "rules": payload.get("rules"),
    }


def _state_mismatch_fields(
    state: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
) -> tuple[str, ...]:
    if not isinstance(state, dict):
        return ("state",)
    expected = _expected_state(repository=repository, ruleset_id=ruleset_id, payload=payload)
    return tuple(key for key, value in expected.items() if state.get(key) != value)


class StabilizedAttestedGitHubOperatorStoreRulesetProvisioner(
    AttestedGitHubOperatorStoreRulesetProvisioner
):
    """Attested provisioner with bounded eventual-consistency stabilization.

    Security semantics are unchanged: only an exact historical state match may
    satisfy the marker/canonical attestation. A longer bounded window helps with
    delayed history visibility; if the surface still does not converge, the
    exception reports only structural metadata and mismatch field names.
    """

    def __init__(
        self,
        *,
        attestation_attempts: int = DEFAULT_STABILIZATION_ATTEMPTS,
        attestation_interval_seconds: float = DEFAULT_STABILIZATION_INTERVAL_SECONDS,
        **kwargs,
    ):
        super().__init__(
            attestation_attempts=attestation_attempts,
            attestation_interval_seconds=attestation_interval_seconds,
            **kwargs,
        )

    def _observe_latest_history_state(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
        *,
        minimum_version_id: int | None,
    ) -> tuple[tuple[int, dict] | None, HistoryObservation]:
        history_url = (
            f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}/history{HISTORY_PAGE}"
        )
        history_status, history = self._request("GET", history_url)
        if history_status != 200:
            return None, HistoryObservation("history-http", http_status=history_status)
        if not isinstance(history, list) or len(history) != 1 or not isinstance(history[0], dict):
            return None, HistoryObservation("history-shape")

        version_id = history[0].get("version_id")
        if not isinstance(version_id, int) or version_id <= 0:
            return None, HistoryObservation("history-version-id")

        version_status, version = self._request(
            "GET",
            f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}/history/{version_id}",
        )
        if version_status != 200:
            return None, HistoryObservation(
                "version-http",
                version_id=version_id,
                http_status=version_status,
            )
        if not isinstance(version, dict) or version.get("version_id") != version_id:
            return None, HistoryObservation("version-shape", version_id=version_id)

        state = version.get("state")
        if not isinstance(state, dict):
            return None, HistoryObservation("version-state-shape", version_id=version_id)
        state_name = state.get("name") if isinstance(state.get("name"), str) else None

        if minimum_version_id is not None and version_id <= minimum_version_id:
            return None, HistoryObservation(
                "stale-version",
                version_id=version_id,
                state_name=state_name,
            )

        mismatches = _state_mismatch_fields(
            state,
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        )
        if mismatches:
            category = "state-name-mismatch" if "name" in mismatches else "state-shape-mismatch"
            return None, HistoryObservation(
                category,
                version_id=version_id,
                state_name=state_name,
                mismatch_fields=mismatches,
            )

        return (version_id, copy.deepcopy(state)), HistoryObservation(
            "exact-match",
            version_id=version_id,
            state_name=state_name,
        )

    def _wait_for_exact_history_state(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
        *,
        minimum_version_id: int | None = None,
    ) -> tuple[int, dict]:
        last = HistoryObservation("not-observed")
        for attempt in range(self.attestation_attempts):
            matched, last = self._observe_latest_history_state(
                repository,
                ruleset_id,
                payload,
                minimum_version_id=minimum_version_id,
            )
            if matched is not None:
                return matched
            if attempt + 1 < self.attestation_attempts:
                self.sleeper(self.attestation_interval_seconds)

        target_name = payload.get("name")
        safe_target = target_name if isinstance(target_name, str) else "<unnamed-ruleset>"
        raise RulesetProvisioningError(
            "ruleset history stabilization exhausted for "
            f"{safe_target!r} after {self.attestation_attempts} attempts: {last.render()}"
        )
