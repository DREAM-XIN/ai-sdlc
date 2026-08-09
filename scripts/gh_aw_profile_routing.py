#!/usr/bin/env python3
"""Trusted role-aware gh-aw profile routing and static-readiness fallback."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import yaml

from gh_aw_compiled_worker import (
    InvalidCompiledWorkerError,
    MissingCompiledWorkerError,
    load_compiled_worker,
)
from gh_aw_provider_registry import ProviderRegistry, RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTING_POLICY = ROOT / "runtimes/gh-aw/profile-routing.yaml"
POLICY_VERSION = "0.1.0"
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ROOT_FIELDS = frozenset({"version", "default_profile", "rules"})
RULE_FIELDS = frozenset({"id", "match", "candidates", "allow_experimental"})
MATCH_FIELDS = frozenset({"role", "stage"})


class RoutingValidationError(ValueError):
    """Fail-closed routing policy or resolution error."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise RoutingValidationError(f"duplicate routing policy mapping key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class RoutingRule:
    rule_id: str
    role: str
    stage: str
    candidates: tuple[str, ...]
    allow_experimental: bool


@dataclass(frozen=True)
class RoutingPolicy:
    version: str
    default_profile: str
    rules: tuple[RoutingRule, ...]
    _by_context: Mapping[tuple[str, str], RoutingRule]

    def require_rule(self, role: str, stage: str) -> RoutingRule:
        try:
            return self._by_context[(role, stage)]
        except KeyError as exc:
            raise RoutingValidationError(
                f"no trusted gh-aw routing rule for role={role!r}, stage={stage!r}"
            ) from exc


@dataclass(frozen=True)
class CandidateDecision:
    profile: str
    ready: bool
    reason: str


@dataclass(frozen=True)
class RoutingResolution:
    policy_version: str
    rule_id: str
    role: str
    stage: str
    decisions: tuple[CandidateDecision, ...]
    selected_profile: str
    fallback: bool
    fallback_reason: str | None


def _require_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise RoutingValidationError(f"{label} must match ^[a-z][a-z0-9-]*$")
    return value


def load_routing_policy(
    path: Path | str = DEFAULT_ROUTING_POLICY,
    *,
    registry: ProviderRegistry | None = None,
) -> RoutingPolicy:
    policy_path = Path(path)
    trusted_registry = registry or load_registry()
    try:
        text = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingValidationError(f"cannot read gh-aw routing policy: {policy_path}") from exc
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except RoutingValidationError:
        raise
    except yaml.YAMLError as exc:
        raise RoutingValidationError("invalid gh-aw routing policy YAML") from exc

    if not isinstance(data, dict):
        raise RoutingValidationError("routing policy root must be a mapping")
    unknown_root = sorted(set(data) - ROOT_FIELDS)
    if unknown_root:
        raise RoutingValidationError(
            f"routing policy contains unsupported fields: {', '.join(map(str, unknown_root))}"
        )
    if data.get("version") != POLICY_VERSION:
        raise RoutingValidationError(
            f"unsupported gh-aw routing policy version: {data.get('version')!r}"
        )
    default_profile = _require_identifier(data.get("default_profile"), "default_profile")
    trusted_registry.require_profile(default_profile)

    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RoutingValidationError("routing policy rules must be a non-empty list")

    rules: list[RoutingRule] = []
    seen_ids: set[str] = set()
    seen_contexts: set[tuple[str, str]] = set()
    for index, raw_rule in enumerate(raw_rules):
        if not isinstance(raw_rule, dict):
            raise RoutingValidationError(f"routing rule {index} must be a mapping")
        unknown_rule = sorted(set(raw_rule) - RULE_FIELDS)
        if unknown_rule:
            raise RoutingValidationError(
                f"routing rule {index} contains unsupported fields: {', '.join(map(str, unknown_rule))}"
            )
        rule_id = _require_identifier(raw_rule.get("id"), f"routing rule {index} id")
        if rule_id in seen_ids:
            raise RoutingValidationError(f"duplicate routing rule id: {rule_id!r}")
        seen_ids.add(rule_id)

        match = raw_rule.get("match")
        if not isinstance(match, dict):
            raise RoutingValidationError(f"routing rule {rule_id!r} match must be a mapping")
        unknown_match = sorted(set(match) - MATCH_FIELDS)
        if unknown_match:
            raise RoutingValidationError(
                f"routing rule {rule_id!r} match contains unsupported fields: {', '.join(map(str, unknown_match))}"
            )
        role = _require_identifier(match.get("role"), f"routing rule {rule_id!r} role")
        stage = _require_identifier(match.get("stage"), f"routing rule {rule_id!r} stage")
        context = (role, stage)
        if context in seen_contexts:
            raise RoutingValidationError(
                f"duplicate routing role/stage match: role={role!r}, stage={stage!r}"
            )
        seen_contexts.add(context)

        raw_candidates = raw_rule.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise RoutingValidationError(f"routing rule {rule_id!r} candidates must be non-empty")
        candidates = tuple(
            _require_identifier(value, f"routing rule {rule_id!r} candidate")
            for value in raw_candidates
        )
        if len(set(candidates)) != len(candidates):
            raise RoutingValidationError(f"routing rule {rule_id!r} contains duplicate candidates")

        allow_experimental = raw_rule.get("allow_experimental")
        if not isinstance(allow_experimental, bool):
            raise RoutingValidationError(
                f"routing rule {rule_id!r} allow_experimental must be boolean"
            )
        for profile_id in candidates:
            profile = trusted_registry.require_profile(profile_id)
            if profile.maturity == "experimental" and not allow_experimental:
                raise RoutingValidationError(
                    f"routing rule {rule_id!r} contains experimental profile {profile_id!r} without trusted opt-in"
                )

        rules.append(
            RoutingRule(
                rule_id=rule_id,
                role=role,
                stage=stage,
                candidates=candidates,
                allow_experimental=allow_experimental,
            )
        )

    by_context = MappingProxyType({(rule.role, rule.stage): rule for rule in rules})
    return RoutingPolicy(POLICY_VERSION, default_profile, tuple(rules), by_context)


def resolve_route(
    policy: RoutingPolicy,
    registry: ProviderRegistry,
    *,
    role: str,
    stage: str,
    readiness: Mapping[str, bool],
    validate_compiled_worker: bool = True,
) -> tuple[RoutingResolution, object]:
    rule = policy.require_rule(role, stage)

    # Validate the complete trusted readiness contract before making any selection.
    # This avoids accepting a partial map merely because an earlier candidate is ready.
    for profile_id in rule.candidates:
        if profile_id not in readiness or not isinstance(readiness[profile_id], bool):
            raise RoutingValidationError(
                f"missing trusted boolean readiness signal for profile {profile_id!r}"
            )

    decisions: list[CandidateDecision] = []
    selected = None
    for profile_id in rule.candidates:
        profile = registry.require_profile(profile_id)
        if profile.maturity == "experimental" and not rule.allow_experimental:
            raise RoutingValidationError(
                f"routing rule {rule.rule_id!r} attempted disallowed experimental profile {profile_id!r}"
            )
        if not readiness[profile_id]:
            decisions.append(CandidateDecision(profile_id, False, "MISSING_CREDENTIAL"))
            continue
        if validate_compiled_worker:
            try:
                load_compiled_worker(profile)
            except MissingCompiledWorkerError as exc:
                raise RoutingValidationError(
                    f"registered compiled worker is missing for profile {profile_id!r}"
                ) from exc
            except InvalidCompiledWorkerError as exc:
                raise RoutingValidationError(
                    f"registered compiled worker is invalid for profile {profile_id!r}"
                ) from exc
        decisions.append(CandidateDecision(profile_id, True, "SELECTED"))
        selected = profile
        break

    if selected is None:
        raise RoutingValidationError(
            f"NO_READY_CANDIDATE for routing rule {rule.rule_id!r}"
        )

    fallback = selected.profile_id != rule.candidates[0]
    return (
        RoutingResolution(
            policy_version=policy.version,
            rule_id=rule.rule_id,
            role=role,
            stage=stage,
            decisions=tuple(decisions),
            selected_profile=selected.profile_id,
            fallback=fallback,
            fallback_reason="PREFERRED_CANDIDATE_NOT_READY" if fallback else None,
        ),
        selected,
    )


def resolution_payload(resolution: RoutingResolution, profile) -> dict:
    return {
        "status": "SELECTED",
        "selection_mode": "policy",
        "policy_version": resolution.policy_version,
        "rule_id": resolution.rule_id,
        "role": resolution.role,
        "stage": resolution.stage,
        "candidates": [
            {"profile": item.profile, "ready": item.ready, "reason": item.reason}
            for item in resolution.decisions
        ],
        "selected": {
            "profile": profile.profile_id,
            "engine": profile.engine,
            "provider": profile.provider,
            "protocol": profile.protocol,
            "model": profile.model,
            "worker_workflow": profile.worker_workflow,
            "maturity": profile.maturity,
        },
        "fallback": resolution.fallback,
        "fallback_reason": resolution.fallback_reason,
        "entitlement_verified": False,
    }


def _readiness_json(value: str) -> dict[str, bool]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("readiness JSON must be valid JSON") from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(flag, bool)
        for key, flag in parsed.items()
    ):
        raise argparse.ArgumentTypeError("readiness JSON must map profile ids to booleans")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--readiness-json", required=True, type=_readiness_json)
    parser.add_argument("--policy", default=str(DEFAULT_ROUTING_POLICY))
    args = parser.parse_args()

    try:
        registry = load_registry()
        policy = load_routing_policy(args.policy, registry=registry)
        resolution, profile = resolve_route(
            policy,
            registry,
            role=args.role,
            stage=args.stage,
            readiness=args.readiness_json,
        )
    except (RegistryValidationError, RoutingValidationError) as exc:
        print(
            json.dumps(
                {
                    "status": "INVALID_ROUTING",
                    "selection_mode": "policy",
                    "role": args.role,
                    "stage": args.stage,
                    "error": str(exc),
                    "entitlement_verified": False,
                },
                separators=(",", ":"),
            )
        )
        return 2

    print(json.dumps(resolution_payload(resolution, profile), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
