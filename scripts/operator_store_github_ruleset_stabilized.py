#!/usr/bin/env python3
"""Bounded stabilization and redacted diagnostics for ruleset write attestation.

This module deliberately does not weaken the causal marker -> canonical proof in
``operator_store_github_ruleset_attested``. It gives the Administration history
surface more time to converge and, only inside that trusted write-attestation
boundary, recognizes GitHub's observed omission-only serialization of the exact
strict writer payload. Generic read-only verification remains fail-closed.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from operator_store_github_ruleset_attested import (
    HISTORY_PAGE,
    AttestedGitHubOperatorStoreRulesetProvisioner,
    AttestedGitHubRulesetProtectionVerifier,
)
from operator_store_github_ruleset_provision import RulesetProvisioningError

DEFAULT_STABILIZATION_ATTEMPTS = 60
DEFAULT_STABILIZATION_INTERVAL_SECONDS = 1.0
_SAFE_RULE_TYPES = frozenset({"creation", "update"})
_UPDATE_PARAMETER = "update_allows_fetch_and_merge"
_STRICT_WRITER_RULES = [
    {"type": "creation"},
    {"type": "update", "parameters": {_UPDATE_PARAMETER: False}},
]
_OMISSION_ONLY_WRITER_RULES = [
    {"type": "creation"},
    {"type": "update"},
]


@dataclass(frozen=True)
class HistoryObservation:
    category: str
    version_id: int | None = None
    state_name: str | None = None
    mismatch_fields: tuple[str, ...] = ()
    http_status: int | None = None
    rules_shape: tuple[str, ...] = ()

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
        if self.rules_shape:
            parts.append("rules_shape=" + "|".join(self.rules_shape))
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


def _safe_rules_shape(rules: object) -> tuple[str, ...]:
    """Describe only bounded rule semantics; never echo arbitrary rule payload text."""
    if not isinstance(rules, list):
        return ("rules=malformed",)

    rows: list[str] = []
    for index, row in enumerate(rules):
        if not isinstance(row, dict):
            rows.append(f"{index}:malformed")
            continue

        raw_type = row.get("type")
        rule_type = raw_type if raw_type in _SAFE_RULE_TYPES else "other"
        if "parameters" not in row:
            rows.append(f"{index}:{rule_type}:parameters=absent")
            continue

        parameters = row.get("parameters")
        if not isinstance(parameters, dict):
            rows.append(f"{index}:{rule_type}:parameters=malformed")
            continue

        if _UPDATE_PARAMETER not in parameters:
            update_value = "absent"
        else:
            value = parameters.get(_UPDATE_PARAMETER)
            if value is True:
                update_value = "true"
            elif value is False:
                update_value = "false"
            else:
                update_value = "malformed"
        other_keys = sum(1 for key in parameters if key != _UPDATE_PARAMETER)
        rows.append(
            f"{index}:{rule_type}:parameters=present:"
            f"{_UPDATE_PARAMETER}={update_value}:other_keys={other_keys}"
        )
    return tuple(rows)


def _normalize_trusted_write_state(
    state: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
) -> dict | None:
    """Normalize only the exact live omission shape of this trusted strict write.

    This is intentionally not a generic rule semantic. The caller must already
    be inside the causal marker/canonical write-attestation path and provide the
    exact payload it just submitted. Any payload other than the canonical strict
    creation+update(false) pair, or any observed extra/malformed rule structure,
    remains non-matching.
    """
    if not isinstance(state, dict):
        return None
    if payload.get("rules") != _STRICT_WRITER_RULES:
        return None
    if state.get("rules") != _OMISSION_ONLY_WRITER_RULES:
        return None

    expected = _expected_state(repository=repository, ruleset_id=ruleset_id, payload=payload)
    for field, value in expected.items():
        if field == "rules":
            continue
        if state.get(field) != value:
            return None

    normalized = copy.deepcopy(state)
    normalized["rules"] = copy.deepcopy(_STRICT_WRITER_RULES)
    return normalized


def _payload_from_omission_current(current_detail: dict) -> dict | None:
    if current_detail.get("rules") != _OMISSION_ONLY_WRITER_RULES:
        return None
    return {
        "name": current_detail.get("name"),
        "target": current_detail.get("target"),
        "enforcement": current_detail.get("enforcement"),
        "conditions": copy.deepcopy(current_detail.get("conditions")),
        "bypass_actors": copy.deepcopy(current_detail.get("bypass_actors")),
        "rules": copy.deepcopy(_STRICT_WRITER_RULES),
    }


class NormalizedAttestedGitHubRulesetProtectionVerifier(AttestedGitHubRulesetProtectionVerifier):
    """Attested verifier that canonicalizes only the causally-bound omission state."""

    def __init__(self, *, http_get, **kwargs):
        self._raw_http_get = http_get
        self._normalization_context: tuple[str, int, dict] | None = None
        super().__init__(http_get=self._get_with_trusted_normalization, **kwargs)

    def _get_with_trusted_normalization(self, url: str, headers: dict[str, str]):
        status, body = self._raw_http_get(url, headers)
        context = self._normalization_context
        if status != 200 or context is None or not isinstance(body, dict):
            return status, body
        if "state" not in body:
            return status, body

        repository, ruleset_id, current_detail = context
        payload = _payload_from_omission_current(current_detail)
        if payload is None:
            return status, body
        normalized_state = _normalize_trusted_write_state(
            body.get("state"),
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        )
        if normalized_state is None:
            return status, body
        normalized_body = copy.deepcopy(body)
        normalized_body["state"] = normalized_state
        return status, normalized_body

    def _latest_version_state(
        self,
        repository: str,
        ruleset_id: int,
        current_detail: dict,
    ) -> tuple[dict, dict] | None:
        self._normalization_context = (repository, ruleset_id, copy.deepcopy(current_detail))
        try:
            return super()._latest_version_state(repository, ruleset_id, current_detail)
        finally:
            self._normalization_context = None


class StabilizedAttestedGitHubOperatorStoreRulesetProvisioner(
    AttestedGitHubOperatorStoreRulesetProvisioner
):
    """Attested provisioner with bounded eventual-consistency stabilization.

    The one compatibility allowance is scoped to a trusted write that explicitly
    submitted the canonical strict writer payload and whose history state has the
    exact omission-only serialization observed in live GitHub. It never grants a
    generic read-only verifier authority to infer omitted parameters.
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

        comparison_state = _normalize_trusted_write_state(
            state,
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        ) or state
        mismatches = _state_mismatch_fields(
            comparison_state,
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
                rules_shape=_safe_rules_shape(state.get("rules")) if "rules" in mismatches else (),
            )

        category = "normalized-exact-match" if comparison_state is not state else "exact-match"
        return (version_id, copy.deepcopy(comparison_state)), HistoryObservation(
            category,
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

    def protection_verifier(self) -> NormalizedAttestedGitHubRulesetProtectionVerifier:
        def get(url: str, headers: dict[str, str]):
            return self.http_request("GET", url, headers, None)

        return NormalizedAttestedGitHubRulesetProtectionVerifier(
            token=self.admin_token,
            operator_app_id=self.operator_app_id,
            api_base=self.api_base,
            api_version=self.api_version,
            http_get=get,
            write_attestations=self.write_attestations,
        )
