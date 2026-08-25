#!/usr/bin/env python3
"""Trusted fresh-marker attestation bound to a strictly-new history generation.

GitHub's repository-ruleset source serialization is replica-variant across the
write response, current-detail and history surfaces.  The long-lived canonical
writer still requires the existing exact current-detail/write-response
``updated_at`` binding.  For the random fresh marker only, this layer binds the
write to the repository-scoped request path plus a pre-write history baseline
and a strictly newer same-nonce history generation on that exact ruleset.

The marker PUT response must still prove the exact ruleset id, marker name,
Repository source type, target, enforcement, conditions, bypass actors, safe
writer rules and captured ``updated_at``.  Its raw ``source`` string is bounded
but is deliberately not used as repository identity authority.  Generic/read-
only verification and the durable post-attestation verifier remain unchanged.
"""
from __future__ import annotations

import copy

from operator_store_github_ruleset_attested import (
    RulesetWriteAttestation,
    _current_matches_canonical_writer,
    _state_digest,
)
from operator_store_github_ruleset_current_detail_bound import (
    CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner,
    _is_fresh_marker_name,
)
from operator_store_github_ruleset_provision import (
    RULESET_WRITER_NAME,
    RulesetProvisioningError,
    writer_ruleset_payload,
)
from operator_store_github_ruleset_stabilized import (
    HistoryObservation,
    _OMISSION_ONLY_WRITER_RULES,
    _STRICT_WRITER_RULES,
    _eligible_for_transient_source_settling,
    _expected_state,
)


def _marker_write_response_binds_submitted_generation(
    write_response: object,
    *,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Validate the marker PUT response without trusting replica-variant source.

    Repository identity is already bound by the same-process pending binding
    created by ``_write_ruleset(repository, ...)`` and by the strictly-new
    history generation fetched back from that exact repository/ruleset path.
    Requiring the response's raw ``source`` value to duplicate that identity is
    both redundant and, on live GitHub, unstable across replicas.
    """

    if not _is_fresh_marker_name(payload.get("name")):
        return False
    if payload.get("rules") != _STRICT_WRITER_RULES:
        return False
    if not isinstance(expected_updated_at, str) or not expected_updated_at:
        return False
    if not isinstance(write_response, dict):
        return False
    if write_response.get("updated_at") != expected_updated_at:
        return False
    if write_response.get("id") != ruleset_id:
        return False
    if write_response.get("source_type") != "Repository":
        return False

    source = write_response.get("source")
    if not isinstance(source, str) or not source or len(source) > 1024:
        return False

    for field in (
        "name",
        "target",
        "enforcement",
        "conditions",
        "bypass_actors",
    ):
        if write_response.get(field) != payload.get(field):
            return False

    return write_response.get("rules") in (
        _STRICT_WRITER_RULES,
        _OMISSION_ONLY_WRITER_RULES,
    )


class GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner(
    CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Bind the fresh marker to one history generation created after this write."""

    def _pre_marker_history_version(self, repository: str, ruleset_id: int | None) -> int:
        if ruleset_id is None:
            return 0
        observed = self._latest_history_version(repository, ruleset_id)
        if observed is None:
            raise RulesetProvisioningError(
                "existing writer ruleset lacks a readable pre-marker history baseline"
            )
        summary, version = observed
        summary_id = summary.get("version_id") if isinstance(summary, dict) else None
        version_id = version.get("version_id") if isinstance(version, dict) else None
        if (
            not isinstance(summary_id, int)
            or summary_id <= 0
            or version_id != summary_id
        ):
            raise RulesetProvisioningError(
                "existing writer ruleset has an invalid pre-marker history baseline"
            )
        return summary_id

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
            or not isinstance(minimum_version_id, int)
            or minimum_version_id < 0
            or not isinstance(observation.version_id, int)
            or observation.version_id <= minimum_version_id
            or not _eligible_for_transient_source_settling(observation, payload=payload)
            or not _marker_write_response_binds_submitted_generation(
                binding[4],
                ruleset_id=ruleset_id,
                payload=payload,
                expected_updated_at=binding[3],
            )
        ):
            return None, observation

        canonical_state = _expected_state(
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        )
        return (
            observation.version_id,
            copy.deepcopy(canonical_state),
        ), HistoryObservation(
            "fresh-marker-strictly-new-request-bound",
            version_id=observation.version_id,
            state_name=observation.state_name,
        )

    def _attest_writer_ruleset(
        self,
        repository: str,
        ruleset_id: int | None,
        state_ref: str,
    ) -> int:
        nonce = self.nonce_factory()
        if (
            not isinstance(nonce, str)
            or not nonce
            or any(ch not in "0123456789abcdefABCDEF" for ch in nonce)
        ):
            raise RulesetProvisioningError(
                "attestation nonce factory returned an invalid marker"
            )

        pre_marker_version_id = self._pre_marker_history_version(repository, ruleset_id)

        marker_payload = writer_ruleset_payload(state_ref, self.operator_app_id)
        marker_payload["name"] = f"{RULESET_WRITER_NAME} [attest:{nonce.lower()}]"
        writer_id, _ = self._write_ruleset(repository, ruleset_id, marker_payload)
        marker_version_id, _ = self._wait_for_exact_history_state(
            repository,
            writer_id,
            marker_payload,
            minimum_version_id=pre_marker_version_id,
        )

        canonical_payload = writer_ruleset_payload(state_ref, self.operator_app_id)
        writer_id, final_response = self._write_ruleset(
            repository,
            writer_id,
            canonical_payload,
        )
        final_version_id, final_state = self._wait_for_exact_history_state(
            repository,
            writer_id,
            canonical_payload,
            minimum_version_id=marker_version_id,
        )

        current_updated_at = final_response.get("updated_at")
        if not isinstance(current_updated_at, str) or not current_updated_at:
            raise RulesetProvisioningError(
                "final writer ruleset response lacks updated_at write binding"
            )
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
            raise RulesetProvisioningError(
                "final writer ruleset current detail does not match bounded write"
            )
        if current.get("updated_at") != current_updated_at:
            raise RulesetProvisioningError(
                "final writer ruleset changed after bounded write response"
            )

        self.write_attestations[writer_id] = RulesetWriteAttestation(
            ruleset_id=writer_id,
            marker_version_id=marker_version_id,
            version_id=final_version_id,
            current_updated_at=current_updated_at,
            state_digest=_state_digest(final_state),
        )
        return writer_id
