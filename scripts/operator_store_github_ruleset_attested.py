#!/usr/bin/env python3
"""Causal write-attestation for live-normalized GitHub ruleset semantics.

The generic ruleset verifier remains read-only and fail-closed. This module adds
one trusted installation-only authority boundary: the provisioner writes a
unique marker version followed by the canonical safe writer ruleset and accepts
the omission-only current detail only when GitHub history has positively
observed both writes in order. The resulting in-memory attestation is then
revalidated against the current ruleset and latest history version on every
protection proof.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import secrets
from typing import Callable

from operator_store_github_ruleset_protection import (
    GitHubRulesetProtectionVerifier,
    _digest,
    _headers,
    _strict_update_parameters,
    _update_parameters_omitted,
)
from operator_store_github_ruleset_provision import (
    GitHubOperatorStoreRulesetProvisioner,
    RULESET_INTEGRITY_NAME,
    RULESET_WRITER_NAME,
    RulesetProvisioningError,
    integrity_ruleset_payload,
    writer_ruleset_payload,
)

MARKER_PREFIX = f"{RULESET_WRITER_NAME} [attest:"
HISTORY_PAGE = "?per_page=1&page=1"


@dataclass(frozen=True)
class RulesetWriteAttestation:
    ruleset_id: int
    marker_version_id: int
    version_id: int
    current_updated_at: str
    state_digest: str


def _state_digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _state_matches_payload(state: object, *, repository: str, ruleset_id: int, payload: dict) -> bool:
    if not isinstance(state, dict):
        return False
    expected = {
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
    return all(state.get(key) == value for key, value in expected.items())


def _current_matches_canonical_writer(
    detail: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
) -> bool:
    if not isinstance(detail, dict):
        return False
    identity = {
        "id": ruleset_id,
        "name": payload.get("name"),
        "target": payload.get("target"),
        "source_type": "Repository",
        "source": repository,
        "enforcement": payload.get("enforcement"),
        "conditions": payload.get("conditions"),
        "bypass_actors": payload.get("bypass_actors"),
    }
    if any(detail.get(key) != value for key, value in identity.items()):
        return False
    rules = detail.get("rules")
    if not isinstance(rules, list):
        return False
    rule_types = [row.get("type") for row in rules if isinstance(row, dict)]
    if len(rule_types) != len(rules) or sorted(rule_types) != ["creation", "update"]:
        return False
    return _strict_update_parameters(detail) or _update_parameters_omitted(detail)


class AttestedGitHubRulesetProtectionVerifier(GitHubRulesetProtectionVerifier):
    """Ruleset verifier whose omission fallback requires a causal write attestation."""

    def __init__(self, *, write_attestations: dict[int, RulesetWriteAttestation], **kwargs):
        super().__init__(**kwargs)
        self.write_attestations = dict(write_attestations)

    def _latest_version_state(
        self,
        repository: str,
        ruleset_id: int,
        current_detail: dict,
    ) -> tuple[dict, dict] | None:
        attestation = self.write_attestations.get(ruleset_id)
        if attestation is None or attestation.ruleset_id != ruleset_id:
            return None
        if current_detail.get("updated_at") != attestation.current_updated_at:
            return None
        if not _update_parameters_omitted(current_detail):
            return None

        headers = _headers(self.token, self.api_version)
        history_url = f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}/history{HISTORY_PAGE}"
        history_status, history = self.http_get(history_url, headers)
        if history_status != 200 or not isinstance(history, list) or len(history) != 1:
            return None
        summary = history[0]
        if not isinstance(summary, dict) or summary.get("version_id") != attestation.version_id:
            return None
        initial_history_updated_at = summary.get("updated_at")

        version_url = (
            f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}/history/"
            f"{attestation.version_id}"
        )
        version_status, version = self.http_get(version_url, headers)
        if version_status != 200 or not isinstance(version, dict):
            return None
        if version.get("version_id") != attestation.version_id:
            return None
        state = version.get("state")
        if not isinstance(state, dict):
            return None
        if _state_digest(state) != attestation.state_digest or not _strict_update_parameters(state):
            return None

        identity_fields = (
            "id",
            "name",
            "target",
            "source_type",
            "source",
            "enforcement",
            "conditions",
            "bypass_actors",
        )
        if any(state.get(field) != current_detail.get(field) for field in identity_fields):
            return None

        detail_url = f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}?includes_parents=true"
        final_detail_status, final_detail = self.http_get(detail_url, headers)
        if final_detail_status != 200 or not isinstance(final_detail, dict):
            return None
        stable_detail_fields = (
            "id",
            "name",
            "target",
            "source_type",
            "source",
            "enforcement",
            "conditions",
            "bypass_actors",
            "rules",
        )
        if any(final_detail.get(field) != current_detail.get(field) for field in stable_detail_fields):
            return None
        if final_detail.get("updated_at") != attestation.current_updated_at:
            return None
        if not _update_parameters_omitted(final_detail):
            return None

        final_history_status, final_history = self.http_get(history_url, headers)
        if final_history_status != 200 or not isinstance(final_history, list) or len(final_history) != 1:
            return None
        final_summary = final_history[0]
        if not isinstance(final_summary, dict):
            return None
        if final_summary.get("version_id") != attestation.version_id:
            return None
        if final_summary.get("updated_at") != initial_history_updated_at:
            return None

        proof = {
            "marker_version_id": attestation.marker_version_id,
            "version_id": attestation.version_id,
            "current_updated_at": attestation.current_updated_at,
            "state_digest": attestation.state_digest,
            "revalidated_current_digest": _digest(
                {field: final_detail.get(field) for field in (*stable_detail_fields, "updated_at")}
            ),
            "revalidated_latest_version_id": final_summary.get("version_id"),
        }
        return state, proof


class AttestedGitHubOperatorStoreRulesetProvisioner(GitHubOperatorStoreRulesetProvisioner):
    """Trusted provisioner that causally binds omitted current semantics to history."""

    def __init__(
        self,
        *,
        admin_token: str,
        operator_app_id: int,
        api_base: str = "https://api.github.com",
        api_version: str = "2022-11-28",
        http_request: Callable | None = None,
        sleeper: Callable[[float], None] | None = None,
        nonce_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        attestation_attempts: int = 8,
        attestation_interval_seconds: float = 0.5,
    ):
        kwargs = {
            "admin_token": admin_token,
            "operator_app_id": operator_app_id,
            "api_base": api_base,
            "api_version": api_version,
        }
        if http_request is not None:
            kwargs["http_request"] = http_request
        if sleeper is not None:
            kwargs["sleeper"] = sleeper
        super().__init__(**kwargs)
        if attestation_attempts < 1:
            raise ValueError("attestation attempts must be positive")
        if attestation_interval_seconds < 0:
            raise ValueError("attestation interval must not be negative")
        self.nonce_factory = nonce_factory
        self.attestation_attempts = attestation_attempts
        self.attestation_interval_seconds = attestation_interval_seconds
        self.write_attestations: dict[int, RulesetWriteAttestation] = {}

    @staticmethod
    def _writer_existing_id(rows: list[dict]) -> int | None:
        matches = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("source_type") in {None, "Repository"}
            and (
                row.get("name") == RULESET_WRITER_NAME
                or (
                    isinstance(row.get("name"), str)
                    and row["name"].startswith(MARKER_PREFIX)
                    and row["name"].endswith("]")
                )
            )
        ]
        if len(matches) > 1:
            raise RulesetProvisioningError("multiple canonical/orphaned Operator Store writer rulesets")
        if not matches:
            return None
        value = matches[0].get("id")
        if not isinstance(value, int) or value <= 0:
            raise RulesetProvisioningError("Operator Store writer ruleset has invalid id")
        return value

    def _write_ruleset(self, repository: str, ruleset_id: int | None, payload: dict) -> tuple[int, dict]:
        if ruleset_id is None:
            status, result = self._request("POST", f"{self.api_base}/repos/{repository}/rulesets", payload)
            expected = 201
        else:
            status, result = self._request(
                "PUT",
                f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}",
                payload,
            )
            expected = 200
        if status != expected or not isinstance(result, dict):
            action = "create" if ruleset_id is None else "update"
            raise RulesetProvisioningError(
                f"unable to {action} causally-attested ruleset {payload.get('name')!r}: HTTP {status}"
            )
        value = result.get("id", ruleset_id)
        if not isinstance(value, int) or value <= 0:
            raise RulesetProvisioningError("causally-attested ruleset response lacks id")
        return value, result

    def _latest_history_version(self, repository: str, ruleset_id: int) -> tuple[dict, dict] | None:
        history_url = f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}/history{HISTORY_PAGE}"
        status, history = self._request("GET", history_url)
        if status != 200 or not isinstance(history, list) or len(history) != 1:
            return None
        summary = history[0]
        if not isinstance(summary, dict):
            return None
        version_id = summary.get("version_id")
        if not isinstance(version_id, int) or version_id <= 0:
            return None
        status, version = self._request(
            "GET",
            f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}/history/{version_id}",
        )
        if status != 200 or not isinstance(version, dict) or version.get("version_id") != version_id:
            return None
        return summary, version

    def _wait_for_exact_history_state(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
        *,
        minimum_version_id: int | None = None,
    ) -> tuple[int, dict]:
        for attempt in range(self.attestation_attempts):
            observed = self._latest_history_version(repository, ruleset_id)
            if observed is not None:
                _, version = observed
                version_id = version.get("version_id")
                state = version.get("state")
                newer = (
                    isinstance(version_id, int)
                    and version_id > 0
                    and (minimum_version_id is None or version_id > minimum_version_id)
                )
                if newer and _state_matches_payload(
                    state,
                    repository=repository,
                    ruleset_id=ruleset_id,
                    payload=payload,
                ):
                    return version_id, copy.deepcopy(state)
            if attempt + 1 < self.attestation_attempts:
                self.sleeper(self.attestation_interval_seconds)
        raise RulesetProvisioningError(
            f"ruleset history did not causally attest write for {payload.get('name')!r}"
        )

    def _attest_writer_ruleset(self, repository: str, ruleset_id: int | None, state_ref: str) -> int:
        nonce = self.nonce_factory()
        if not isinstance(nonce, str) or not nonce or any(ch not in "0123456789abcdefABCDEF" for ch in nonce):
            raise RulesetProvisioningError("attestation nonce factory returned an invalid marker")

        marker_payload = writer_ruleset_payload(state_ref, self.operator_app_id)
        marker_payload["name"] = f"{RULESET_WRITER_NAME} [attest:{nonce.lower()}]"
        writer_id, _ = self._write_ruleset(repository, ruleset_id, marker_payload)
        marker_version_id, _ = self._wait_for_exact_history_state(
            repository,
            writer_id,
            marker_payload,
        )

        canonical_payload = writer_ruleset_payload(state_ref, self.operator_app_id)
        writer_id, final_response = self._write_ruleset(repository, writer_id, canonical_payload)
        final_version_id, final_state = self._wait_for_exact_history_state(
            repository,
            writer_id,
            canonical_payload,
            minimum_version_id=marker_version_id,
        )

        current_updated_at = final_response.get("updated_at")
        if not isinstance(current_updated_at, str) or not current_updated_at:
            raise RulesetProvisioningError("final writer ruleset response lacks updated_at write binding")
        status, current = self._request(
            "GET",
            f"{self.api_base}/repos/{repository}/rulesets/{writer_id}?includes_parents=true",
        )
        if status != 200 or not _current_matches_canonical_writer(
            current,
            repository=repository,
            ruleset_id=writer_id,
            payload=canonical_payload,
        ):
            raise RulesetProvisioningError("final writer ruleset current detail does not match bounded write")
        if current.get("updated_at") != current_updated_at:
            raise RulesetProvisioningError("final writer ruleset changed after bounded write response")

        self.write_attestations[writer_id] = RulesetWriteAttestation(
            ruleset_id=writer_id,
            marker_version_id=marker_version_id,
            version_id=final_version_id,
            current_updated_at=current_updated_at,
            state_digest=_state_digest(final_state),
        )
        return writer_id

    def ensure_rulesets(self, repository: str, state_ref: str) -> tuple[int, int]:
        if "/" not in repository:
            raise ValueError("repository must be owner/name")
        if not state_ref.startswith("refs/heads/"):
            raise ValueError("Operator Store state ref must be a branch ref")
        self.write_attestations.clear()
        rows = self._list_rulesets(repository)
        writer_id = self._attest_writer_ruleset(
            repository,
            self._writer_existing_id(rows),
            state_ref,
        )
        rows = self._list_rulesets(repository)
        integrity_id = self._upsert(
            repository,
            self._existing_id(rows, RULESET_INTEGRITY_NAME),
            integrity_ruleset_payload(state_ref),
        )
        return writer_id, integrity_id

    def protection_verifier(self) -> AttestedGitHubRulesetProtectionVerifier:
        def get(url: str, headers: dict[str, str]):
            return self.http_request("GET", url, headers, None)

        return AttestedGitHubRulesetProtectionVerifier(
            token=self.admin_token,
            operator_app_id=self.operator_app_id,
            api_base=self.api_base,
            api_version=self.api_version,
            http_get=get,
            write_attestations=self.write_attestations,
        )
