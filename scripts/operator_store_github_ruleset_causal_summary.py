#!/usr/bin/env python3
"""Trusted-only settling for replica-variant exact ruleset-history metadata.

This layer is intentionally narrower than the generic/read-only ruleset verifier
and narrower than the #355 older-version settling layer. It is used only after
the same trusted process has already established an exact marker -> canonical
write attestation and #355 has handled any strictly older positive history
version.

GitHub may serve the exact same attested ``version_id`` while history-list and
exact-version ``updated_at`` metadata come from a different replica than current
detail. The generic verifier deliberately requires those timestamps to match.
This trusted-only wrapper does not weaken that verifier. Instead it first proves
that each replica observation is stable, belongs to the exact process-local
attested version, and (for exact-version detail) carries the exact already-
attested canonical state digest. Only then does the process-local transport
normalize that replica-only timestamp to the already-attested current timestamp.

A newer generation, malformed/unavailable history, missing/empty ``updated_at``,
state-digest drift, unstable metadata, or retry exhaustion remains fail-closed.
Bindings are process-local and are never serialized.
"""
from __future__ import annotations

import copy

from operator_store_github_ruleset_attested import _state_digest
from operator_store_github_ruleset_causal_history import (
    CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)

DEFAULT_EXACT_HISTORY_SUMMARY_SETTLING_ATTEMPTS = 120


class CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner(
    CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Bind exact attested history metadata to stable real observations."""

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
    def _exact_version_detail_key(
        value: object,
        *,
        expected_version_id: int,
        expected_state_digest: str,
    ) -> tuple[int, str, str] | None:
        if not isinstance(value, dict) or value.get("version_id") != expected_version_id:
            return None
        updated_at = value.get("updated_at")
        state = value.get("state")
        if not isinstance(updated_at, str) or not updated_at or not isinstance(state, dict):
            return None
        digest = _state_digest(state)
        if digest != expected_state_digest:
            return None
        return expected_version_id, updated_at, digest

    @staticmethod
    def _normalized_summary(value: list, current_updated_at: str) -> list:
        normalized = copy.deepcopy(value)
        normalized[0]["updated_at"] = current_updated_at
        return normalized

    @staticmethod
    def _normalized_version_detail(value: dict, current_updated_at: str) -> dict:
        normalized = copy.deepcopy(value)
        normalized["updated_at"] = current_updated_at
        return normalized

    def protection_verifier(self):
        verifier = super().protection_verifier()
        base_get = verifier.http_get
        base_latest_version_state = verifier._latest_version_state
        attestations = dict(self.write_attestations)
        # Replay fences are intentionally scoped to this one process-local
        # verifier. Nothing here is persisted as authority.
        summary_bindings: dict[tuple[str, int, int], tuple[int, str]] = {}
        version_bindings: dict[tuple[str, int, int], tuple[int, str, str]] = {}
        invalid_exact_metadata: set[tuple[str, int, int]] = set()

        def get(url: str, headers: dict[str, str]):
            history_identity = self._history_list_identity(url)
            path_identity = self._ruleset_path_identity(url)

            # Exact-version detail: the inherited causal-current transport has
            # already normalized only bounded source/rules replica shapes and
            # verified the canonical state digest. Require two identical real
            # exact-version metadata observations before normalizing only the
            # replica timestamp to the already-attested current timestamp.
            if path_identity is not None and path_identity[2] is not None:
                repository, ruleset_id, version_id = path_identity
                attestation = attestations.get(ruleset_id)
                if (
                    attestation is None
                    or attestation.ruleset_id != ruleset_id
                    or version_id != attestation.version_id
                ):
                    return base_get(url, headers)

                binding_key = (repository.lower(), ruleset_id, attestation.version_id)
                status, value = base_get(url, headers)
                if status != 200:
                    return status, value
                detail_key = self._exact_version_detail_key(
                    value,
                    expected_version_id=attestation.version_id,
                    expected_state_digest=attestation.state_digest,
                )
                if detail_key is None:
                    return status, value

                expected = version_bindings.get(binding_key)
                if expected is not None:
                    if detail_key != expected:
                        return status, value
                    return status, self._normalized_version_detail(
                        value, attestation.current_updated_at
                    )

                second_status, second = base_get(url, headers)
                if second_status != 200:
                    return second_status, second
                second_key = self._exact_version_detail_key(
                    second,
                    expected_version_id=attestation.version_id,
                    expected_state_digest=attestation.state_digest,
                )
                if second_key is None or second_key != detail_key:
                    return second_status, second
                version_bindings[binding_key] = detail_key
                return second_status, self._normalized_version_detail(
                    second, attestation.current_updated_at
                )

            if history_identity is None:
                return base_get(url, headers)

            repository, ruleset_id = history_identity
            attestation = attestations.get(ruleset_id)
            if attestation is None or attestation.ruleset_id != ruleset_id:
                return base_get(url, headers)

            binding_key = (repository.lower(), ruleset_id, attestation.version_id)
            expected = summary_bindings.get(binding_key)

            status, value = base_get(url, headers)
            last_status, last_value = status, value
            if status != 200:
                return status, value

            exact_key = self._exact_summary_key(
                value,
                expected_version_id=attestation.version_id,
            )
            if exact_key is None:
                if self._is_exact_version_with_invalid_metadata(
                    value,
                    expected_version_id=attestation.version_id,
                ):
                    invalid_exact_metadata.add(binding_key)
                return status, value

            if expected is not None:
                if exact_key == expected:
                    return status, self._normalized_summary(
                        value, attestation.current_updated_at
                    )

                # Do not move the replay fence. Wait only for GitHub itself to
                # return the originally bound real exact-version pair.
                for _attempt in range(1, self.exact_history_summary_settling_attempts):
                    self.sleeper(self.attestation_interval_seconds)
                    status, value = base_get(url, headers)
                    last_status, last_value = status, value
                    if status != 200:
                        return status, value
                    exact_key = self._exact_summary_key(
                        value,
                        expected_version_id=attestation.version_id,
                    )
                    if exact_key is None:
                        if self._is_exact_version_with_invalid_metadata(
                            value,
                            expected_version_id=attestation.version_id,
                        ):
                            invalid_exact_metadata.add(binding_key)
                        return status, value
                    if exact_key == expected:
                        return status, self._normalized_summary(
                            value, attestation.current_updated_at
                        )
                return last_status, last_value

            # First use of this exact attested generation: require two
            # consecutive real observations of the same raw summary before
            # installing the process-local replay fence.
            candidate = exact_key
            for _attempt in range(1, self.exact_history_summary_settling_attempts):
                self.sleeper(self.attestation_interval_seconds)
                status, value = base_get(url, headers)
                last_status, last_value = status, value
                if status != 200:
                    return status, value
                exact_key = self._exact_summary_key(
                    value,
                    expected_version_id=attestation.version_id,
                )
                if exact_key is None:
                    if self._is_exact_version_with_invalid_metadata(
                        value,
                        expected_version_id=attestation.version_id,
                    ):
                        invalid_exact_metadata.add(binding_key)
                    return status, value
                if exact_key == candidate:
                    summary_bindings[binding_key] = candidate
                    return status, self._normalized_summary(
                        value, attestation.current_updated_at
                    )
                candidate = exact_key

            # No stable real exact-version summary was observed inside the
            # bound. Return the last real response; never manufacture authority.
            return last_status, last_value

        def latest_version_state(repository: str, ruleset_id: int, current_detail: dict):
            attestation = attestations.get(ruleset_id)
            binding_key = None
            if attestation is not None and attestation.ruleset_id == ruleset_id:
                binding_key = (repository.lower(), ruleset_id, attestation.version_id)
                invalid_exact_metadata.discard(binding_key)

            resolved = base_latest_version_state(repository, ruleset_id, current_detail)
            if binding_key is not None and binding_key in invalid_exact_metadata:
                return None
            return resolved

        verifier.http_get = get
        verifier._latest_version_state = latest_version_state
        return verifier
