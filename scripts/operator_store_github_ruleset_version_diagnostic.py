#!/usr/bin/env python3
"""Trusted-only fine-grained diagnostics for rejected exact-version proofs.

This layer does not change the protection verdict or any GitHub observation. It
wraps the already-reviewed causal-summary verifier and records only a bounded
category naming the first inherited exact-version predicate that rejects the
proof. Raw GitHub values are never exposed through the diagnostic surface.
"""
from __future__ import annotations

from operator_store_github_ruleset_attested import _state_digest
from operator_store_github_ruleset_causal_summary import (
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner,
)
from operator_store_github_ruleset_protection import (
    _strict_update_parameters,
    _update_parameters_omitted,
)


class VersionProofDiagnosedAttestedGitHubOperatorStoreRulesetProvisioner(
    CausalSummarySettledAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Expose only the rejected inherited exact-version predicate category."""

    def protection_verifier(self):
        verifier = super().protection_verifier()
        base_latest_version_state = verifier._latest_version_state
        base_get = verifier.http_get
        base_categories = verifier.protection_diagnostic_categories
        attestations = dict(self.write_attestations)

        failure_stage: list[str] = []
        active: dict[str, object] = {}
        history_reads = {"count": 0, "initial_updated_at": None}

        def record(category: str) -> None:
            if not failure_stage:
                failure_stage.append(category)

        def get(url: str, headers: dict[str, str]):
            status, value = base_get(url, headers)
            repository = active.get("repository")
            ruleset_id = active.get("ruleset_id")
            current_detail = active.get("current_detail")
            attestation = active.get("attestation")
            if (
                not isinstance(repository, str)
                or not isinstance(ruleset_id, int)
                or not isinstance(current_detail, dict)
                or attestation is None
            ):
                return status, value

            history_identity = self._history_list_identity(url)
            path_identity = self._ruleset_path_identity(url)

            if history_identity == (repository, ruleset_id):
                history_reads["count"] += 1
                final_read = history_reads["count"] > 1
                prefix = "final" if final_read else "initial"
                if status != 200:
                    record(f"version-proof-{prefix}-history-unavailable")
                    return status, value
                if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
                    record(f"version-proof-{prefix}-history-shape-rejected")
                    return status, value
                summary = value[0]
                if summary.get("version_id") != attestation.version_id:
                    record(f"version-proof-{prefix}-history-version-rejected")
                    return status, value
                updated_at = summary.get("updated_at")
                if not final_read:
                    history_reads["initial_updated_at"] = updated_at
                elif updated_at != history_reads["initial_updated_at"]:
                    record("version-proof-final-history-updated-at-rejected")
                return status, value

            if path_identity == (repository, ruleset_id, attestation.version_id):
                if status != 200 or not isinstance(value, dict):
                    record("version-proof-exact-version-unavailable")
                    return status, value
                if value.get("version_id") != attestation.version_id:
                    record("version-proof-exact-version-id-rejected")
                    return status, value
                state = value.get("state")
                if not isinstance(state, dict):
                    record("version-proof-state-shape-rejected")
                    return status, value
                if _state_digest(state) != attestation.state_digest:
                    record("version-proof-state-digest-rejected")
                    return status, value
                if not _strict_update_parameters(state):
                    record("version-proof-state-rules-rejected")
                    return status, value
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
                    record("version-proof-state-current-identity-rejected")
                return status, value

            if path_identity == (repository, ruleset_id, None):
                if status != 200 or not isinstance(value, dict):
                    record("version-proof-final-current-unavailable")
                    return status, value
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
                if any(value.get(field) != current_detail.get(field) for field in stable_detail_fields):
                    record("version-proof-final-current-drift-rejected")
                    return status, value
                if value.get("updated_at") != attestation.current_updated_at:
                    record("version-proof-final-current-updated-at-rejected")
                    return status, value
                if not _update_parameters_omitted(value):
                    record("version-proof-final-current-rules-rejected")
                return status, value

            return status, value

        verifier.http_get = get

        def latest_version_state(repository: str, ruleset_id: int, current_detail: dict):
            failure_stage.clear()
            history_reads["count"] = 0
            history_reads["initial_updated_at"] = None
            attestation = attestations.get(ruleset_id)
            active.clear()
            active.update({
                "repository": repository,
                "ruleset_id": ruleset_id,
                "current_detail": current_detail,
                "attestation": attestation,
            })

            if attestation is None or attestation.ruleset_id != ruleset_id:
                record("version-proof-attestation-missing")
            elif current_detail.get("updated_at") != attestation.current_updated_at:
                record("version-proof-current-updated-at-rejected")
            elif not _update_parameters_omitted(current_detail):
                record("version-proof-current-rules-rejected")

            resolved = base_latest_version_state(repository, ruleset_id, current_detail)
            active.clear()
            return resolved

        def diagnostic_categories() -> tuple[str, ...]:
            categories = tuple(base_categories())
            if categories == ("underlying-version-proof-rejected",) and failure_stage:
                return tuple(failure_stage)
            return categories

        verifier._latest_version_state = latest_version_state
        verifier.protection_diagnostic_categories = diagnostic_categories
        return verifier
