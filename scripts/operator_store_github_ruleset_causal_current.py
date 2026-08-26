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
from urllib.parse import unquote, urlparse

from operator_store_github_ruleset_attested import (
    AttestedGitHubRulesetProtectionVerifier,
    _current_matches_canonical_writer,
    _state_digest,
)
from operator_store_github_ruleset_generation_bound import (
    GenerationBoundAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_provision import RulesetProvisioningError
from operator_store_github_ruleset_stabilized import (
    _OMISSION_ONLY_WRITER_RULES,
    _STRICT_WRITER_RULES,
    _safe_source_shape,
    _state_mismatch_fields,
)


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
                if binding is None:
                    return status, value
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

            # History authority is already bound by the exact attested generation
            # and canonical state digest.  It must not depend on whether the
            # separate current-detail replica happened to require an opaque-source
            # replay binding.  This keeps the normalization process-local and
            # fail-closed while allowing canonical current + replica-opaque history.
            if version_id != attestation.version_id or value.get("version_id") != version_id:
                return status, value
            state = value.get("state")
            if not isinstance(state, dict):
                return status, value
            normalized_state = copy.deepcopy(state)
            if normalized_state.get("source_type") != "Repository":
                return status, value
            if _safe_source_shape(normalized_state.get("source"), repository) == "other-string":
                normalized_state["source"] = repository
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
