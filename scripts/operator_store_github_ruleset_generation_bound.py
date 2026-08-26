#!/usr/bin/env python3
"""Trusted marker/canonical attestation bound to strictly-new history generations.

GitHub's repository-ruleset source serialization is replica-variant across the
write response, current-detail and history surfaces.  This layer never treats a
raw ``source`` string from a PUT response as repository identity authority.

For the random fresh marker, repository authority comes from the same-process
repository-scoped request plus a pre-write history baseline and a strictly newer
same-nonce history generation.

For the long-lived canonical writer, the same allowance is available only after
that marker has already been positively attested in this process.  The canonical
history generation must be strictly newer than the marker generation and carry
the exact canonical safe payload apart from the already-observed ``source,rules``
omission serialization.  This provisioner-only causal allowance is not durable:
before an attestation is published, current detail must settle back to the exact
repository-scoped canonical writer.  The attestation timestamp is bound to that
final exact current detail rather than assuming GitHub's PUT-response timestamp
and current-detail timestamp are replica-stable.

Generic/read-only protection verification and the durable post-attestation
verifier remain unchanged and strict.
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
    _safe_source_shape,
    _state_mismatch_fields,
)

# Exact-main run 32846146699 exhausted the existing 900-second transient-source
# budget while current detail remained in the same bounded omission-only shape.
# Keep current-detail authority strict, but give this post-canonical replica
# convergence its own bounded budget instead of reusing the history budget.
DEFAULT_FINAL_CURRENT_SETTLING_ATTEMPTS = 1800


def _bounded_source(value: object) -> bool:
    return isinstance(value, str) and bool(value) and len(value) <= 1024


def _write_response_binds_safe_payload(
    write_response: object,
    *,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Validate one repository-scoped ruleset write response without trusting source."""
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
    if not _bounded_source(write_response.get("source")):
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


def _marker_write_response_binds_submitted_generation(
    write_response: object,
    *,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Validate the fresh-marker PUT response without trusting replica source."""
    return _is_fresh_marker_name(payload.get("name")) and _write_response_binds_safe_payload(
        write_response,
        ruleset_id=ruleset_id,
        payload=payload,
        expected_updated_at=expected_updated_at,
    )


def _canonical_write_response_binds_submitted_generation(
    write_response: object,
    *,
    ruleset_id: int,
    payload: dict,
    expected_updated_at: str,
) -> bool:
    """Validate only the exact long-lived canonical writer PUT response."""
    return payload.get("name") == RULESET_WRITER_NAME and _write_response_binds_safe_payload(
        write_response,
        ruleset_id=ruleset_id,
        payload=payload,
        expected_updated_at=expected_updated_at,
    )


class GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner(
    CurrentDetailBoundAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Bind marker then canonical writer to two strictly ordered generations."""

    def __init__(
        self,
        *,
        final_current_settling_attempts: int = DEFAULT_FINAL_CURRENT_SETTLING_ATTEMPTS,
        **kwargs,
    ):
        # Process-local sequencing fence.  It is populated only after the fresh
        # marker has already been positively attested and is cleared immediately
        # after the one canonical wait.  Nothing is serialized as authority.
        self._canonical_generation_floor: tuple[str, int, int] | None = None
        super().__init__(**kwargs)
        if (
            not isinstance(final_current_settling_attempts, int)
            or final_current_settling_attempts < self.transient_source_settling_attempts
        ):
            raise ValueError(
                "final current-detail settling attempts must be an integer >= transient source settling attempts"
            )
        self.final_current_settling_attempts = final_current_settling_attempts

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
        ):
            return None, observation

        marker_bound = _marker_write_response_binds_submitted_generation(
            binding[4],
            ruleset_id=ruleset_id,
            payload=payload,
            expected_updated_at=binding[3],
        )
        canonical_floor = self._canonical_generation_floor
        canonical_bound = (
            canonical_floor == (repository, ruleset_id, minimum_version_id)
            and minimum_version_id > 0
            and _canonical_write_response_binds_submitted_generation(
                binding[4],
                ruleset_id=ruleset_id,
                payload=payload,
                expected_updated_at=binding[3],
            )
        )
        if not marker_bound and not canonical_bound:
            return None, observation

        canonical_state = _expected_state(
            repository=repository,
            ruleset_id=ruleset_id,
            payload=payload,
        )
        category = (
            "fresh-marker-strictly-new-request-bound"
            if marker_bound
            else "canonical-writer-strictly-new-request-bound"
        )
        return (
            observation.version_id,
            copy.deepcopy(canonical_state),
        ), HistoryObservation(
            category,
            version_id=observation.version_id,
            state_name=observation.state_name,
        )

    def _wait_for_exact_canonical_current_detail(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
    ) -> tuple[dict, str]:
        """Wait only for the exact canonical current identity after causal history proof.

        An opaque current ``source`` may be waited through only when every other
        identity field and the omission-only safe rule pair are already exact.
        It is never normalized or accepted as current authority.
        """
        for attempt in range(self.final_current_settling_attempts):
            status, current = self._request(
                "GET",
                f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}?includes_parents=true",
            )
            if status == 200 and _current_matches_canonical_writer(
                current,
                repository=repository,
                ruleset_id=ruleset_id,
                payload=payload,
            ):
                updated_at = current.get("updated_at") if isinstance(current, dict) else None
                if not isinstance(updated_at, str) or not updated_at:
                    raise RulesetProvisioningError(
                        "final writer ruleset current detail lacks updated_at authority"
                    )
                return copy.deepcopy(current), updated_at

            if status != 200 or not isinstance(current, dict):
                raise RulesetProvisioningError(
                    "final writer ruleset current detail could not be revalidated"
                )
            if (
                current.get("rules") != _OMISSION_ONLY_WRITER_RULES
                or _safe_source_shape(current.get("source"), repository) != "other-string"
                or _state_mismatch_fields(
                    current,
                    repository=repository,
                    ruleset_id=ruleset_id,
                    payload=payload,
                ) != ("source", "rules")
            ):
                raise RulesetProvisioningError(
                    "final writer ruleset current detail drifted outside bounded settling shape"
                )
            if attempt + 1 < self.final_current_settling_attempts:
                self.sleeper(self.attestation_interval_seconds)

        raise RulesetProvisioningError(
            "final writer ruleset current detail did not settle to exact repository identity"
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
        self._canonical_generation_floor = (repository, writer_id, marker_version_id)
        try:
            writer_id, final_response = self._write_ruleset(
                repository,
                writer_id,
                canonical_payload,
            )
            response_updated_at = final_response.get("updated_at")
            if not isinstance(response_updated_at, str) or not response_updated_at:
                raise RulesetProvisioningError(
                    "final writer ruleset response lacks updated_at write binding"
                )
            if not _canonical_write_response_binds_submitted_generation(
                final_response,
                ruleset_id=writer_id,
                payload=canonical_payload,
                expected_updated_at=response_updated_at,
            ):
                raise RulesetProvisioningError(
                    "final writer ruleset response is not bound to the safe canonical write"
                )
            final_version_id, final_state = self._wait_for_exact_history_state(
                repository,
                writer_id,
                canonical_payload,
                minimum_version_id=marker_version_id,
            )
        finally:
            self._canonical_generation_floor = None

        _, current_updated_at = self._wait_for_exact_canonical_current_detail(
            repository,
            writer_id,
            canonical_payload,
        )

        self.write_attestations[writer_id] = RulesetWriteAttestation(
            ruleset_id=writer_id,
            marker_version_id=marker_version_id,
            version_id=final_version_id,
            current_updated_at=current_updated_at,
            state_digest=_state_digest(final_state),
        )
        return writer_id
