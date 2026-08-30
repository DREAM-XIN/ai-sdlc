#!/usr/bin/env python3
"""Trusted-only settling for replica-variant exact ruleset-history summaries.

This layer is intentionally narrower than the generic/read-only ruleset verifier
and narrower than the #355 older-version settling layer. It is used only after
the same trusted process has already established an exact marker -> canonical
write attestation and #355 has handled any strictly older positive history
version.

GitHub may serve the exact same attested ``version_id`` while the history-list
summary ``updated_at`` still flips between replicas. The inherited attested
verifier deliberately requires its first and final history summaries to retain
the same ``updated_at`` value. This wrapper preserves that fence: it never
rewrites or synthesizes a summary. Instead, the initial exact-version summary is
accepted only after GitHub returns the same real ``(version_id, updated_at)`` on
two consecutive reads. Later reads may wait boundedly for GitHub itself to
return that exact process-local bound pair again.

A newer generation, malformed/unavailable history, missing/empty ``updated_at``,
or retry exhaustion remains fail-closed. The binding and rejection diagnostics
are process-local and are never serialized. Diagnostics contain category names
only; they never retain raw GitHub values.
"""
from __future__ import annotations

from operator_store_github_ruleset_causal_history import (
    CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)

DEFAULT_EXACT_HISTORY_SUMMARY_SETTLING_ATTEMPTS = 120


class CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner(
    CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Bind the exact attested latest-history summary to stable real observations."""

    def __init__(
        self,
        *,
        exact_history_summary_settling_attempts: int = DEFAULT_EXACT_HISTORY_SUMMARY_SETTLING_ATTEMPTS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if (
            not isinstance(exact_history_summary_settling_attempts, int)
            or exact_history_summary_settling_attempts < 2
        ):
            raise ValueError(
                "exact history-summary settling attempts must be an integer >= 2"
            )
        self.exact_history_summary_settling_attempts = exact_history_summary_settling_attempts

    @staticmethod
    def _exact_summary_key(value: object, *, expected_version_id: int) -> tuple[int, str] | None:
        if not isinstance(value, list) or len(value) != 1:
            return None
        summary = value[0]
        if not isinstance(summary, dict):
            return None
        version_id = summary.get("version_id")
        updated_at = summary.get("updated_at")
        if version_id != expected_version_id:
            return None
        if not isinstance(updated_at, str) or not updated_at:
            return None
        return version_id, updated_at

    @staticmethod
    def _is_exact_version_with_invalid_metadata(
        value: object,
        *,
        expected_version_id: int,
    ) -> bool:
        if not isinstance(value, list) or len(value) != 1:
            return False
        summary = value[0]
        if not isinstance(summary, dict) or summary.get("version_id") != expected_version_id:
            return False
        updated_at = summary.get("updated_at")
        return not isinstance(updated_at, str) or not updated_at

    @staticmethod
    def _non_exact_summary_category(value: object, *, expected_version_id: int) -> str:
        if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
            return "history-summary-malformed"
        version_id = value[0].get("version_id")
        if not isinstance(version_id, int) or version_id <= 0:
            return "history-summary-malformed"
        if version_id < expected_version_id:
            return "history-summary-older-version"
        if version_id > expected_version_id:
            return "history-summary-newer-version"
        return "history-summary-invalid-metadata"

    def protection_verifier(self):
        verifier = super().protection_verifier()
        base_get = verifier.http_get
        base_latest_version_state = verifier._latest_version_state
        attestations = dict(self.write_attestations)
        summary_bindings: dict[tuple[str, int, int], tuple[int, str]] = {}
        invalid_exact_metadata: set[tuple[str, int, int]] = set()
        rejection_categories: list[str] = []

        def record(category: str) -> None:
            if not rejection_categories or rejection_categories[-1] != category:
                rejection_categories.append(category)

        def get(url: str, headers: dict[str, str]):
            identity = self._history_list_identity(url)
            if identity is None:
                return base_get(url, headers)

            repository, ruleset_id = identity
            attestation = attestations.get(ruleset_id)
            if attestation is None or attestation.ruleset_id != ruleset_id:
                return base_get(url, headers)

            binding_key = (repository.lower(), ruleset_id, attestation.version_id)
            expected = summary_bindings.get(binding_key)

            status, value = base_get(url, headers)
            last_status, last_value = status, value
            if status != 200:
                record("history-summary-unavailable")
                return status, value

            exact_key = self._exact_summary_key(
                value,
                expected_version_id=attestation.version_id,
            )
            if exact_key is None:
                category = self._non_exact_summary_category(
                    value,
                    expected_version_id=attestation.version_id,
                )
                record(category)
                if self._is_exact_version_with_invalid_metadata(
                    value,
                    expected_version_id=attestation.version_id,
                ):
                    invalid_exact_metadata.add(binding_key)
                return status, value

            if expected is not None:
                if exact_key == expected:
                    return status, value

                for attempt in range(1, self.exact_history_summary_settling_attempts):
                    self.sleeper(self.attestation_interval_seconds)
                    status, value = base_get(url, headers)
                    last_status, last_value = status, value
                    if status != 200:
                        record("history-summary-unavailable")
                        return status, value
                    exact_key = self._exact_summary_key(
                        value,
                        expected_version_id=attestation.version_id,
                    )
                    if exact_key is None:
                        category = self._non_exact_summary_category(
                            value,
                            expected_version_id=attestation.version_id,
                        )
                        record(category)
                        if self._is_exact_version_with_invalid_metadata(
                            value,
                            expected_version_id=attestation.version_id,
                        ):
                            invalid_exact_metadata.add(binding_key)
                        return status, value
                    if exact_key == expected:
                        return status, value
                record("history-summary-replay-timeout")
                return last_status, last_value

            candidate = exact_key
            for attempt in range(1, self.exact_history_summary_settling_attempts):
                self.sleeper(self.attestation_interval_seconds)
                status, value = base_get(url, headers)
                last_status, last_value = status, value
                if status != 200:
                    record("history-summary-unavailable")
                    return status, value
                exact_key = self._exact_summary_key(
                    value,
                    expected_version_id=attestation.version_id,
                )
                if exact_key is None:
                    category = self._non_exact_summary_category(
                        value,
                        expected_version_id=attestation.version_id,
                    )
                    record(category)
                    if self._is_exact_version_with_invalid_metadata(
                        value,
                        expected_version_id=attestation.version_id,
                    ):
                        invalid_exact_metadata.add(binding_key)
                    return status, value
                if exact_key == candidate:
                    summary_bindings[binding_key] = candidate
                    return status, value
                candidate = exact_key

            record("history-summary-initial-settle-timeout")
            return last_status, last_value

        def latest_version_state(repository: str, ruleset_id: int, current_detail: dict):
            rejection_categories.clear()
            attestation = attestations.get(ruleset_id)
            binding_key = None
            if attestation is not None and attestation.ruleset_id == ruleset_id:
                binding_key = (repository.lower(), ruleset_id, attestation.version_id)
                invalid_exact_metadata.discard(binding_key)

            resolved = base_latest_version_state(repository, ruleset_id, current_detail)
            if binding_key is not None and binding_key in invalid_exact_metadata:
                record("history-summary-invalid-metadata")
                return None
            if resolved is None and not rejection_categories:
                record("underlying-version-proof-rejected")
            return resolved

        def diagnostic_categories() -> tuple[str, ...]:
            return tuple(rejection_categories)

        verifier.http_get = get
        verifier._latest_version_state = latest_version_state
        verifier.protection_diagnostic_categories = diagnostic_categories
        return verifier
