#!/usr/bin/env python3
"""Trusted write-attestation bound to GitHub's exact current ruleset detail.

GitHub's ruleset history surface can serialize the repository ``source`` as an
opaque string for a long time after an otherwise exact ruleset update. Waiting
for that history-only field to converge is not a useful trust primitive.

This module keeps the existing marker -> canonical history proof. Canonical
writer authority remains bound to the just-completed write response ``updated_at``.
For the fresh cryptographic marker only, the same-process pending write plus the
exact random marker name may instead bind the opaque history source to an exact
same-ruleset current detail. If that current detail is itself replica-opaque,
the fresh marker may additionally use an exact repository-scoped write response
plus the same exact ``updated_at`` and omission-only current/history shapes.
Generic/read-only verification remains unchanged.
"""
from __future__ import annotations

import copy

from operator_store_github_ruleset_attested import (
    MARKER_PREFIX,
    RulesetWriteAttestation,
    _current_matches_canonical_writer,
    _state_digest,
)
from operator_store_github_ruleset_provision import RulesetProvisioningError
from operator_store_github_ruleset_stabilized import (
    HistoryObservation,
    NormalizedAttestedGitHubRulesetProtectionVerifier,
    StabilizedAttestedGitHubOperatorStoreRulesetProvisioner,
    _OMISSION_ONLY_WRITER_RULES,
    _STRICT_WRITER_RULES,
    _eligible_for_transient_source_settling,
    _expected_state,
    _normalize_trusted_write_state,
    _payload_from_omission_current,
    _safe_source_shape,
    _state_mismatch_fields,
)


def _writer_detail_matches_payload(
    current_detail: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
) -> bool:
    if payload.get("rules") != _STRICT_WRITER_RULES:
        return False
    if not isinstance(current_detail, dict):
        return False
    if current_detail.get("rules") not in (
        _STRICT_WRITER_RULES,
        _OMISSION_ONLY_WRITER_RULES,
    ):
        return False
    return _current_matches_canonical_writer(
        current_detail,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
    )


def _current_detail_binds_exact_write(
    current_detail: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Prove current detail is the exact repository-scoped write we just made."""
    if not isinstance(expected_updated_at, str) or not expected_updated_at:
        return False
    if not isinstance(current_detail, dict):
        return False
    if current_detail.get("updated_at") != expected_updated_at:
        return False
    return _writer_detail_matches_payload(
        current_detail,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
    )


def _is_fresh_marker_name(value: object) -> bool:
    """Match only the lowercase hex marker form emitted by _attest_writer_ruleset."""
    if not isinstance(value, str) or not value.startswith(MARKER_PREFIX) or not value.endswith("]"):
        return False
    nonce = value[len(MARKER_PREFIX) : -1]
    return bool(nonce) and nonce == nonce.lower() and all(ch in "0123456789abcdef" for ch in nonce)


def _current_detail_binds_fresh_marker(
    current_detail: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
) -> bool:
    """Bind only this process's exact fresh marker without timestamp authority.

    The caller additionally requires the marker name to equal the same-process
    pending write binding and the newest history version to carry that exact name.
    The unpredictable marker nonce is therefore the causal cross-surface token;
    this helper never applies to the long-lived canonical writer name.
    """
    if not _is_fresh_marker_name(payload.get("name")):
        return False
    return _writer_detail_matches_payload(
        current_detail,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
    )


def _write_response_binds_fresh_marker(
    write_response: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Bind a fresh marker to the exact repository-scoped write response.

    This is deliberately stronger than trusting the request URL alone. The
    response must itself expose the exact repository identity and exact safe
    writer identity, and its ``updated_at`` must be the same value captured by
    the same-process pending-write binding.
    """
    if not _is_fresh_marker_name(payload.get("name")):
        return False
    if not isinstance(expected_updated_at, str) or not expected_updated_at:
        return False
    if not isinstance(write_response, dict):
        return False
    if write_response.get("updated_at") != expected_updated_at:
        return False
    return _writer_detail_matches_payload(
        write_response,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
    )


def _opaque_current_detail_binds_fresh_marker_write(
    current_detail: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Accept only the exact observed replica-opaque fresh-marker current shape."""
    if not _is_fresh_marker_name(payload.get("name")):
        return False
    if not isinstance(current_detail, dict):
        return False
    if current_detail.get("updated_at") != expected_updated_at:
        return False
    if current_detail.get("rules") != _OMISSION_ONLY_WRITER_RULES:
        return False
    if _safe_source_shape(current_detail.get("source"), repository) != "other-string":
        return False
    return _state_mismatch_fields(
        current_detail,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
    ) == ("source", "rules")


def _normalize_history_state_from_bound_current(
    state: object,
    current_detail: object,
    *,
    repository: str,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> dict | None:
    """Canonicalize only the exact opaque-source history serialization.

    This helper is used by the durable post-attestation verifier, so it retains
    the exact write-response ``updated_at`` binding. Fresh-marker fallback is a
    provisioner-only causal mechanism and is never available here.
    """
    if not isinstance(state, dict):
        return None
    if not _current_detail_binds_exact_write(
        current_detail,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
        expected_updated_at=expected_updated_at,
    ):
        return None
    if state.get("rules") != _OMISSION_ONLY_WRITER_RULES:
        return None
    if _safe_source_shape(state.get("source"), repository) != "other-string":
        return None

    mismatches = _state_mismatch_fields(
        state,
        repository=repository,
        ruleset_id=ruleset_id,
        payload=payload,
    )
    if mismatches != ("source", "rules"):
        return None

    normalized = copy.deepcopy(state)
    normalized["source"] = repository
    normalized["rules"] = copy.deepcopy(_STRICT_WRITER_RULES)
    return normalized


class CurrentDetailBoundAttestedGitHubRulesetProtectionVerifier(
    NormalizedAttestedGitHubRulesetProtectionVerifier
):
    """Revalidate opaque history source only against the attested current detail."""

    def _get_with_trusted_normalization(self, url: str, headers: dict[str, str]):
        status, body = super()._get_with_trusted_normalization(url, headers)
        context = self._normalization_context
        if status != 200 or context is None or not isinstance(body, dict):
            return status, body
        if "state" not in body:
            return status, body

        repository, ruleset_id, current_detail = context
        attestation = self.write_attestations.get(ruleset_id)
        if attestation is None:
            return status, body
        payload = _payload_from_omission_current(current_detail)
        if payload is None:
            return status, body

        # If the inherited omission-only normalization already succeeded, the
        # source is exact and no cross-surface authority is needed.
        inherited_state = body.get("state")
        if _normalize_trusted_write_state(
            inherited_state,
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        ) is not None:
            return status, body

        normalized_state = _normalize_history_state_from_bound_current(
            inherited_state,
            current_detail,
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
            expected_updated_at=attestation.current_updated_at,
        )
        if normalized_state is None:
            return status, body
        normalized_body = copy.deepcopy(body)
        normalized_body["state"] = normalized_state
        return status, normalized_body


class CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner(
    StabilizedAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Trusted provisioner whose history source identity is current-detail bound."""

    def __init__(self, **kwargs):
        self._pending_write_binding: tuple[str, int, str | None, str, dict] | None = None
        self._active_write_binding: tuple[str, int, str | None, str, dict] | None = None
        super().__init__(**kwargs)

    def _write_ruleset(
        self,
        repository: str,
        ruleset_id: int | None,
        payload: dict,
    ) -> tuple[int, dict]:
        written_id, response = super()._write_ruleset(repository, ruleset_id, payload)
        updated_at = response.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            raise RulesetProvisioningError(
                "causally-attested ruleset response lacks updated_at write binding"
            )
        self._pending_write_binding = (
            repository,
            written_id,
            payload.get("name") if isinstance(payload.get("name"), str) else None,
            updated_at,
            copy.deepcopy(response),
        )
        return written_id, response

    def _wait_for_exact_history_state(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
        *,
        minimum_version_id: int | None = None,
    ) -> tuple[int, dict]:
        pending = self._pending_write_binding
        expected_name = payload.get("name") if isinstance(payload.get("name"), str) else None
        if (
            pending is not None
            and pending[0] == repository
            and pending[1] == ruleset_id
            and pending[2] == expected_name
        ):
            self._active_write_binding = pending
        else:
            self._active_write_binding = None
        try:
            return super()._wait_for_exact_history_state(
                repository,
                ruleset_id,
                payload,
                minimum_version_id=minimum_version_id,
            )
        finally:
            self._active_write_binding = None
            if self._pending_write_binding == pending:
                self._pending_write_binding = None

    def _observe_latest_history_state(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
        *,
        minimum_version_id: int | None,
    ) -> tuple[tuple[int, dict] | None, HistoryObservation]:
        matched, observation = super()._observe_latest_history_state(
            repository,
            ruleset_id,
            payload,
            minimum_version_id=minimum_version_id,
        )
        if matched is not None:
            return matched, observation

        binding = self._active_write_binding
        if (
            binding is None
            or binding[0] != repository
            or binding[1] != ruleset_id
            or binding[2] != payload.get("name")
            or not _eligible_for_transient_source_settling(observation, payload=payload)
            or not isinstance(observation.version_id, int)
            or observation.version_id <= 0
        ):
            return None, observation

        detail_status, current_detail = self._request(
            "GET",
            f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}?includes_parents=true",
        )
        if detail_status != 200:
            return None, observation

        exact_write_bound = _current_detail_binds_exact_write(
            current_detail,
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
            expected_updated_at=binding[3],
        )
        fresh_marker_bound = False
        if not exact_write_bound:
            fresh_marker_bound = _current_detail_binds_fresh_marker(
                current_detail,
                repository=repository,
                ruleset_id=ruleset_id,
                payload=payload,
            )

        fresh_marker_write_response_bound = False
        if not exact_write_bound and not fresh_marker_bound:
            fresh_marker_write_response_bound = (
                _write_response_binds_fresh_marker(
                    binding[4],
                    repository=repository,
                    ruleset_id=ruleset_id,
                    payload=payload,
                    expected_updated_at=binding[3],
                )
                and _opaque_current_detail_binds_fresh_marker_write(
                    current_detail,
                    repository=repository,
                    ruleset_id=ruleset_id,
                    payload=payload,
                    expected_updated_at=binding[3],
                )
            )

        if not exact_write_bound and not fresh_marker_bound and not fresh_marker_write_response_bound:
            return None, observation

        # ``observation`` was produced from the exact newest history version and
        # proves every expected field except source/rules already matches. Build
        # the canonical state from the exact submitted payload rather than ever
        # trusting or retaining the opaque source value.
        canonical_state = _expected_state(
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        )
        if fresh_marker_write_response_bound:
            category = "fresh-marker-write-response-bound-transient"
        elif fresh_marker_bound:
            category = "fresh-marker-current-detail-bound-transient"
        else:
            category = "current-detail-bound-transient"
        return (
            observation.version_id,
            copy.deepcopy(canonical_state),
        ), HistoryObservation(
            category,
            version_id=observation.version_id,
            state_name=observation.state_name,
        )

    def protection_verifier(self) -> CurrentDetailBoundAttestedGitHubRulesetProtectionVerifier:
        def get(url: str, headers: dict[str, str]):
            return self.http_request("GET", url, headers, None)

        return CurrentDetailBoundAttestedGitHubRulesetProtectionVerifier(
            token=self.admin_token,
            operator_app_id=self.operator_app_id,
            api_base=self.api_base,
            api_version=self.api_version,
            http_get=get,
            write_attestations=self.write_attestations,
        )
