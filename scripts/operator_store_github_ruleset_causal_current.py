#!/usr/bin/env python3
"""Trusted v0.3 causal normalization for replica-opaque ruleset source fields.

This module is intentionally narrower than the generic/read-only ruleset verifier.
It is used only after the same-process repository-scoped marker -> canonical
sequence has already produced strictly newer history generations.

GitHub can keep serializing ``source`` as an opaque token for the admin-token
view even after the canonical generation is durable.  Repository identity is
therefore not taken from that opaque token.  Instead, this trusted-only layer
binds the opaque current observation to the exact repository-scoped causal
write, exact ruleset id, stable current ``updated_at``, and the canonical history
state digest.  The raw opaque current source must be stable across two reads.

Only the process-local verifier returned by this provisioner sees normalized
source/history values.  The generic verifier and every new process remain
strict and receive no persisted relaxation.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from urllib.parse import unquote, urlparse

from operator_store_github_ruleset_attested import (
    AttestedGitHubRulesetProtectionVerifier,
    _current_matches_canonical_writer,
    _state_digest,
)
from operator_store_github_ruleset_generation_bound import (
    GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_provision import (
    RulesetProvisioningError,
    writer_ruleset_payload,
)
from operator_store_github_ruleset_stabilized import (
    _OMISSION_ONLY_WRITER_RULES,
    _STRICT_WRITER_RULES,
    _safe_source_shape,
    _state_mismatch_fields,
)

_AUTHORITY_STATE_FIELDS = (
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

# The live #263 history observation demonstrated only these timestamp keys as
# response metadata outside the protection-authority state.  Keep this schema
# deliberately closed: any new/unknown state key must fail closed rather than
# being silently projected away.
_ALLOWED_HISTORY_STATE_METADATA_FIELDS = (
    "created_at",
    "updated_at",
)


def _authority_state(value: dict) -> dict:
    """Project one ruleset state onto the fields that carry protection authority."""
    return {field: copy.deepcopy(value.get(field)) for field in _AUTHORITY_STATE_FIELDS}


def _closed_history_authority_state(value: dict) -> dict | None:
    """Return authority fields only for the explicitly observed closed state schema."""
    allowed = set(_AUTHORITY_STATE_FIELDS) | set(_ALLOWED_HISTORY_STATE_METADATA_FIELDS)
    if any(key not in allowed for key in value):
        return None
    for field in _ALLOWED_HISTORY_STATE_METADATA_FIELDS:
        if field in value:
            metadata = value.get(field)
            if not isinstance(metadata, str) or not metadata or len(metadata) > 128:
                return None
    return _authority_state(value)


def _canonical_writer_authority_state(
    repository: str,
    ruleset_id: int,
    state_ref: str,
    operator_app_id: int,
) -> dict:
    """Build the exact authority projection of the trusted canonical writer payload."""
    payload = writer_ruleset_payload(state_ref, operator_app_id)
    return {
        "id": ruleset_id,
        "name": payload.get("name"),
        "target": payload.get("target"),
        "source_type": "Repository",
        "source": repository,
        "enforcement": payload.get("enforcement"),
        "conditions": copy.deepcopy(payload.get("conditions")),
        "bypass_actors": copy.deepcopy(payload.get("bypass_actors")),
        "rules": copy.deepcopy(payload.get("rules")),
    }


class CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner(
    GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Bind persistent opaque current source to one exact causal generation."""

    def __init__(self, **kwargs):
        self._opaque_current_bindings: dict[int, tuple[str, str]] = {}
        super().__init__(**kwargs)

    @staticmethod
    def _updated_at(current: dict) -> str:
        value = current.get("updated_at")
        if not isinstance(value, str) or not value:
            raise RulesetProvisioningError(
                "final writer ruleset current detail lacks updated_at authority"
            )
        return value

    def _wait_for_exact_canonical_current_detail(
        self,
        repository: str,
        ruleset_id: int,
        payload: dict,
    ) -> tuple[dict, str]:
        """Accept exact detail, or two stable bounded opaque observations.

        The opaque value itself never becomes repository identity.  It is kept
        only as a process-local replay fence so the verifier can prove that the
        same replica serialization is being re-read before normalizing it to the
        already causally-bound repository.
        """
        opaque_candidate: tuple[str, str] | None = None
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
                self._opaque_current_bindings.pop(ruleset_id, None)
                return copy.deepcopy(current), self._updated_at(current)

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
                    "final writer ruleset current detail drifted outside bounded causal shape"
                )

            source = current.get("source")
            if not isinstance(source, str) or not source or len(source) > 1024:
                raise RulesetProvisioningError(
                    "final writer ruleset current detail has invalid opaque source"
                )
            candidate = (source, self._updated_at(current))
            if opaque_candidate == candidate:
                self._opaque_current_bindings[ruleset_id] = candidate
                return copy.deepcopy(current), candidate[1]
            opaque_candidate = candidate
            if attempt + 1 < self.final_current_settling_attempts:
                self.sleeper(self.attestation_interval_seconds)

        raise RulesetProvisioningError(
            "final writer ruleset current detail did not produce a stable causal observation"
        )

    def _attest_writer_ruleset(
        self,
        repository: str,
        ruleset_id: int | None,
        state_ref: str,
    ) -> int:
        """Rebind the process-local digest to the exact submitted authority projection.

        The inherited marker -> canonical generation proof still establishes the
        exact version and current ``updated_at``.  GitHub history responses can
        later add or vary the narrowly observed timestamp metadata inside
        ``state``.  That metadata is admitted only by a closed schema and never
        becomes protection authority.
        """
        writer_id = super()._attest_writer_ruleset(repository, ruleset_id, state_ref)
        attestation = self.write_attestations.get(writer_id)
        if attestation is None or attestation.ruleset_id != writer_id:
            raise RulesetProvisioningError("writer ruleset attestation was not retained")
        canonical = _canonical_writer_authority_state(
            repository,
            writer_id,
            state_ref,
            self.operator_app_id,
        )
        self.write_attestations[writer_id] = replace(
            attestation,
            state_digest=_state_digest(canonical),
        )
        return writer_id

    @staticmethod
    def _ruleset_path_identity(url: str) -> tuple[str, int, int | None] | None:
        """Return (repository, ruleset_id, history_version_id) for exact endpoints."""
        parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
        if len(parts) == 5 and parts[0] == "repos" and parts[3] == "rulesets":
            try:
                return f"{parts[1]}/{parts[2]}", int(parts[4]), None
            except ValueError:
                return None
        if (
            len(parts) == 7
            and parts[0] == "repos"
            and parts[3] == "rulesets"
            and parts[5] == "history"
        ):
            try:
                return f"{parts[1]}/{parts[2]}", int(parts[4]), int(parts[6])
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalized_current_state_digest(value: dict, repository: str) -> str | None:
        """Digest only canonical ruleset-state fields after bounded replica normalization."""
        source = value.get("source")
        if _safe_source_shape(source, repository) != "other-string":
            return None
        if value.get("source_type") != "Repository" or value.get("rules") != _OMISSION_ONLY_WRITER_RULES:
            return None
        state = _authority_state(value)
        state["source"] = repository
        state["rules"] = copy.deepcopy(_STRICT_WRITER_RULES)
        return _state_digest(state)

    def protection_verifier(self) -> AttestedGitHubRulesetProtectionVerifier:
        attestations = dict(self.write_attestations)
        opaque_bindings = dict(self._opaque_current_bindings)

        def get(url: str, headers: dict[str, str]):
            status, value = self.http_request("GET", url, headers, None)
            identity = self._ruleset_path_identity(url)
            if status != 200 or not isinstance(value, dict) or identity is None:
                return status, value

            repository, ruleset_id, version_id = identity
            attestation = attestations.get(ruleset_id)
            if attestation is None:
                return status, value

            if version_id is None:
                binding = opaque_bindings.get(ruleset_id)
                if binding is not None:
                    opaque_source, current_updated_at = binding
                    if (
                        value.get("id") != ruleset_id
                        or value.get("source_type") != "Repository"
                        or value.get("source") != opaque_source
                        or value.get("updated_at") != current_updated_at
                        or current_updated_at != attestation.current_updated_at
                    ):
                        return status, value
                    normalized = copy.deepcopy(value)
                    normalized["source"] = repository
                    return status, normalized

                # A canonical final attestation read can be followed by an opaque
                # admin-token replica during the later protection proof.  Do not
                # trust that late opaque token.  Re-read it once in the same
                # process and accept only a stable omission-only observation whose
                # normalized canonical state digest and updated_at are exactly the
                # already-attested causal generation.
                digest = self._normalized_current_state_digest(value, repository)
                source = value.get("source")
                updated_at = value.get("updated_at")
                if (
                    value.get("id") == ruleset_id
                    and isinstance(source, str)
                    and source
                    and len(source) <= 1024
                    and isinstance(updated_at, str)
                    and updated_at == attestation.current_updated_at
                    and digest == attestation.state_digest
                ):
                    second_status, second = self.http_request("GET", url, headers, None)
                    if second_status != 200 or not isinstance(second, dict):
                        return status, value
                    second_digest = self._normalized_current_state_digest(second, repository)
                    stable_fields = (
                        "id",
                        "name",
                        "target",
                        "source_type",
                        "source",
                        "enforcement",
                        "conditions",
                        "bypass_actors",
                        "rules",
                        "updated_at",
                    )
                    if (
                        second_digest == attestation.state_digest
                        and all(second.get(field) == value.get(field) for field in stable_fields)
                    ):
                        opaque_bindings[ruleset_id] = (source, updated_at)
                        normalized = copy.deepcopy(value)
                        normalized["source"] = repository
                        return status, normalized
                return status, value

            # History authority is bound to the exact attested generation and the
            # exact submitted protection fields.  Admit only the explicitly
            # observed timestamp metadata outside those fields; every unknown key
            # remains a fail-closed version-state drift signal.
            if version_id != attestation.version_id or value.get("version_id") != version_id:
                return status, value
            state = value.get("state")
            if not isinstance(state, dict):
                return status, value
            normalized_state = _closed_history_authority_state(state)
            if normalized_state is None or normalized_state.get("source_type") != "Repository":
                return status, value
            source_shape = _safe_source_shape(normalized_state.get("source"), repository)
            if source_shape == "other-string":
                normalized_state["source"] = repository
            elif source_shape != "equals-repository":
                return status, value
            if normalized_state.get("rules") == _OMISSION_ONLY_WRITER_RULES:
                normalized_state["rules"] = copy.deepcopy(_STRICT_WRITER_RULES)
            if _state_digest(normalized_state) != attestation.state_digest:
                return status, value
            normalized = copy.deepcopy(value)
            normalized["state"] = normalized_state
            return status, normalized

        return AttestedGitHubRulesetProtectionVerifier(
            token=self.admin_token,
            operator_app_id=self.operator_app_id,
            api_base=self.api_base,
            api_version=self.api_version,
            http_get=get,
            write_attestations=attestations,
        )
