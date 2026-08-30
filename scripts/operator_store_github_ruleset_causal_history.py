#!/usr/bin/env python3
"""Trusted-only settling for lagging ruleset-history replicas.

The generic/read-only ruleset verifier remains unchanged and fail-closed. This
layer is used only after the same trusted process has already established a
marker -> canonical write attestation for one exact repository ruleset
version. GitHub can subsequently serve an older latest-history summary from a
replica even while current detail is stable at the attested canonical write.

Only that one bounded condition may settle: an older positive version id can be
re-read for a bounded period. The wrapper never upgrades old history into
current authority. It returns successfully only when the history endpoint
itself exposes the exact process-local attested version. A newer version,
malformed/unavailable history, wrong ruleset, or timeout is left unchanged so
the existing verifier fails closed.
"""
from __future__ import annotations

from urllib.parse import unquote, urlparse

from operator_store_github_ruleset_causal_current import (
    CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner,
)

DEFAULT_PROTECTION_HISTORY_SETTLING_ATTEMPTS = 120


class CausalHistorySettledAttestedGitHubOperatorStoreRulesetProvisioner(
    CausalCurrentAttestedGitHubOperatorStoreRulesetProvisioner
):
    """Wait boundedly for the exact already-attested latest history version."""

    def __init__(
        self,
        *,
        protection_history_settling_attempts: int = DEFAULT_PROTECTION_HISTORY_SETTLING_ATTEMPTS,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if (
            not isinstance(protection_history_settling_attempts, int)
            or protection_history_settling_attempts < 1
        ):
            raise ValueError("protection history settling attempts must be a positive integer")
        self.protection_history_settling_attempts = protection_history_settling_attempts

    @staticmethod
    def _history_list_identity(url: str) -> tuple[str, int] | None:
        parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
        if (
            len(parts) == 6
            and parts[0] == "repos"
            and parts[3] == "rulesets"
            and parts[5] == "history"
        ):
            try:
                ruleset_id = int(parts[4])
            except ValueError:
                return None
            if ruleset_id <= 0:
                return None
            return f"{parts[1]}/{parts[2]}", ruleset_id
        return None

    def protection_verifier(self):
        verifier = super().protection_verifier()
        base_get = verifier.http_get
        attestations = dict(self.write_attestations)

        def get(url: str, headers: dict[str, str]):
            identity = self._history_list_identity(url)
            if identity is None:
                return base_get(url, headers)

            repository, ruleset_id = identity
            attestation = attestations.get(ruleset_id)
            if attestation is None or attestation.ruleset_id != ruleset_id:
                return base_get(url, headers)

            last_status = 0
            last_value: object = {}
            for attempt in range(self.protection_history_settling_attempts):
                status, value = base_get(url, headers)
                last_status, last_value = status, value
                if status != 200 or not isinstance(value, list) or len(value) != 1:
                    return status, value
                summary = value[0]
                if not isinstance(summary, dict):
                    return status, value
                version_id = summary.get("version_id")
                if not isinstance(version_id, int) or version_id <= 0:
                    return status, value

                if version_id == attestation.version_id:
                    return status, value

                # A newer generation is an authority change, never replica lag.
                # Return it immediately so the existing exact-version verifier
                # rejects the stale process-local attestation.
                if version_id > attestation.version_id:
                    return status, value

                # Only a strictly older positive version may be a lagging replica.
                if attempt + 1 < self.protection_history_settling_attempts:
                    self.sleeper(self.attestation_interval_seconds)

            # Do not synthesize the expected version on timeout. Return the last
            # real GitHub observation and let the existing verifier fail closed.
            return last_status, last_value

        verifier.http_get = get
        return verifier
