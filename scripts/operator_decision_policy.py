#!/usr/bin/env python3
"""Trusted current-policy verification for bounded Operator Decisions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from operator_store import StoreCommandError
from operator_store_model import digest_json, normalize_repository

DECISION_POLICY_SCHEMA = "ai-sdlc.decision-policy/v1"
TRUSTED_POLICY_PREFIXES = ("protected://", "default-branch://", "installation://")


@dataclass(frozen=True)
class VerifiedDecisionPolicy:
    policy_ref: str
    policy_epoch: str
    policy_digest: str
    base_policy_digest: str
    decision_type: str
    allowed_choices: tuple[str, ...]
    choice_actions: dict[str, str]
    allowed_responders: frozenset[str]
    ttl_seconds: int
    warning_seconds: int

    def action_for(self, choice: str) -> str:
        if choice not in self.allowed_choices:
            raise StoreCommandError("POLICY_DENIED", "Decision response is not an exact current allowed choice")
        action = self.choice_actions.get(choice)
        if not action:
            raise StoreCommandError("POLICY_DENIED", "Decision choice lacks a bounded trusted action")
        return action


class ProtectedDecisionPolicyVerifier:
    """Re-read protected/default-branch/installation Decision policy on every authority use."""

    def __init__(
        self,
        *,
        repository: str,
        state_ref: str,
        operation_profile: str,
        policy_loader: Callable[[str, str, str], dict[str, Any]],
        feature_restriction_loader: Callable[[str, str, str], dict[str, Any] | None] | None = None,
    ):
        self.repository = normalize_repository(repository)
        if not state_ref.startswith("refs/heads/"):
            raise ValueError("Decision policy verifier requires a trusted branch state ref")
        if not operation_profile or not callable(policy_loader):
            raise ValueError("Decision policy verifier is incomplete")
        self.state_ref = state_ref
        self.operation_profile = operation_profile
        self.policy_loader = policy_loader
        self.feature_restriction_loader = feature_restriction_loader

    @staticmethod
    def _base_material(policy: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in policy.items() if key != "policy_digest"}

    def _load_base(self) -> tuple[dict[str, Any], str]:
        policy = self.policy_loader(self.repository, self.state_ref, self.operation_profile)
        if not isinstance(policy, dict) or policy.get("schema_version") != DECISION_POLICY_SCHEMA:
            raise StoreCommandError("POLICY_DENIED", "invalid current protected Decision policy")
        if normalize_repository(str(policy.get("repository", ""))) != self.repository:
            raise StoreCommandError("POLICY_DENIED", "Decision policy repository binding mismatch")
        if policy.get("state_ref") != self.state_ref or policy.get("operation_profile") != self.operation_profile:
            raise StoreCommandError("POLICY_DENIED", "Decision policy Store/profile binding mismatch")
        policy_ref = str(policy.get("policy_ref") or "")
        if not policy_ref.startswith(TRUSTED_POLICY_PREFIXES):
            raise StoreCommandError("POLICY_DENIED", "Decision policy is not from trusted control state")
        if not str(policy.get("policy_epoch") or ""):
            raise StoreCommandError("POLICY_DENIED", "Decision policy epoch is missing")
        expected = digest_json(self._base_material(policy))
        if policy.get("policy_digest") != expected:
            raise StoreCommandError("POLICY_DENIED", "Decision policy digest mismatch")
        return policy, expected

    @staticmethod
    def _decision_rule(policy: dict[str, Any], decision_type: str) -> dict[str, Any]:
        rules = policy.get("decision_types")
        rule = rules.get(decision_type) if isinstance(rules, dict) else None
        if not isinstance(rule, dict):
            raise StoreCommandError("POLICY_DENIED", "Decision type is not authorized by current policy")
        choices = rule.get("choices")
        if not isinstance(choices, dict) or not choices:
            raise StoreCommandError("POLICY_DENIED", "Decision policy has no bounded choices")
        for choice, action in choices.items():
            if not isinstance(choice, str) or not choice or not isinstance(action, str) or not action:
                raise StoreCommandError("POLICY_DENIED", "Decision policy contains invalid choice/action")
        return rule

    @staticmethod
    def _apply_restrictions(
        *,
        rule: dict[str, Any],
        restriction: dict[str, Any] | None,
    ) -> tuple[tuple[str, ...], dict[str, str], frozenset[str], int, int, dict[str, Any]]:
        choices = {str(k): str(v) for k, v in rule["choices"].items()}
        responders = frozenset(str(value) for value in rule.get("allowed_responders", []))
        if not responders or any(not value for value in responders):
            raise StoreCommandError("POLICY_DENIED", "Decision policy requires trusted responder identities")
        ttl = int(rule.get("ttl_seconds", 0))
        warning = int(rule.get("warning_seconds", 0))
        if ttl < 1 or ttl > 7 * 24 * 3600 or warning < 0 or warning >= ttl:
            raise StoreCommandError("POLICY_DENIED", "Decision policy TTL/warning is invalid")
        restriction = dict(restriction or {})
        unknown = set(restriction) - {"allowed_choices", "allowed_responders", "max_ttl_seconds", "warning_seconds"}
        if unknown:
            raise StoreCommandError("POLICY_DENIED", "Feature restriction contains authority-expanding fields")
        if "allowed_choices" in restriction:
            narrowed = tuple(str(value) for value in restriction["allowed_choices"])
            if not narrowed or len(set(narrowed)) != len(narrowed) or not set(narrowed).issubset(choices):
                raise StoreCommandError("POLICY_DENIED", "Feature branch attempted to expand Decision choices")
            choices = {key: choices[key] for key in narrowed}
        if "allowed_responders" in restriction:
            narrowed_responders = frozenset(str(value) for value in restriction["allowed_responders"])
            if not narrowed_responders or not narrowed_responders.issubset(responders):
                raise StoreCommandError("POLICY_DENIED", "Feature branch attempted to expand Decision responders")
            responders = narrowed_responders
        if "max_ttl_seconds" in restriction:
            requested_ttl = int(restriction["max_ttl_seconds"])
            if requested_ttl < 1 or requested_ttl > ttl:
                raise StoreCommandError("POLICY_DENIED", "Feature branch attempted to expand Decision TTL")
            ttl = requested_ttl
        if "warning_seconds" in restriction:
            requested_warning = int(restriction["warning_seconds"])
            if requested_warning < 0 or requested_warning > warning or requested_warning >= ttl:
                raise StoreCommandError("POLICY_DENIED", "Feature branch attempted to expand Decision warning window")
            warning = requested_warning
        return tuple(sorted(choices)), choices, responders, ttl, warning, restriction

    def verify_current(
        self,
        *,
        target_repository: str,
        feature_id: str,
        target_ref: str,
        decision_type: str,
    ) -> VerifiedDecisionPolicy:
        if normalize_repository(target_repository) != self.repository:
            raise StoreCommandError("POLICY_DENIED", "Decision target repository is outside verifier authority")
        policy, base_digest = self._load_base()
        rule = self._decision_rule(policy, decision_type)
        restriction = None
        if self.feature_restriction_loader is not None:
            restriction = self.feature_restriction_loader(self.repository, feature_id, target_ref)
            if restriction is not None and not isinstance(restriction, dict):
                raise StoreCommandError("POLICY_DENIED", "trusted Feature restriction loader returned invalid data")
        choices, actions, responders, ttl, warning, restriction = self._apply_restrictions(
            rule=rule,
            restriction=restriction,
        )
        effective_digest = digest_json(
            {
                "base_policy_digest": base_digest,
                "feature_id": feature_id,
                "target_ref": target_ref,
                "decision_type": decision_type,
                "restriction": restriction,
                "allowed_choices": list(choices),
                "choice_actions": actions,
                "allowed_responders": sorted(responders),
                "ttl_seconds": ttl,
                "warning_seconds": warning,
            }
        )
        return VerifiedDecisionPolicy(
            policy_ref=str(policy["policy_ref"]),
            policy_epoch=str(policy["policy_epoch"]),
            policy_digest=effective_digest,
            base_policy_digest=base_digest,
            decision_type=decision_type,
            allowed_choices=choices,
            choice_actions=actions,
            allowed_responders=responders,
            ttl_seconds=ttl,
            warning_seconds=warning,
        )
