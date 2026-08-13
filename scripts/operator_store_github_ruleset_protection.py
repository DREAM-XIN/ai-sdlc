#!/usr/bin/env python3
"""Trusted GitHub repository-ruleset protection proof for Operator Store refs.

This is the personal-repository-compatible alternative to classic branch push
restrictions. It proves two layered properties:

1. creation/update is restricted and only the configured Operator Integration
   can bypass those writer rules;
2. deletion/non-fast-forward is independently blocked by rulesets with no
   bypass actors, including for the Operator Integration itself.

The verifier is read-only and fail-closed. It never creates or edits rulesets.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionReceipt

REQUIRED_WRITER_RULES = frozenset({"creation", "update"})
REQUIRED_INTEGRITY_RULES = frozenset({"deletion", "non_fast_forward"})
STRICT_UPDATE_PARAMETERS = {"update_allows_fetch_and_merge": False}
BRANCH_RULE_PAGE_SIZE = 100


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest(value) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - trusted GitHub API URL
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body: object = {}
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            pass
        return exc.code, body
    except (URLError, TimeoutError, OSError):
        return 0, {}


def _headers(token: str, api_version: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": api_version,
        "User-Agent": "ai-sdlc-operator-store",
    }


def _rule_types_by_ruleset(rows: list[dict]) -> dict[int, set[str]]:
    grouped: dict[int, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ruleset_id = row.get("ruleset_id")
        rule_type = row.get("type")
        if isinstance(ruleset_id, int) and isinstance(rule_type, str):
            grouped.setdefault(ruleset_id, set()).add(rule_type)
    return grouped


def _operator_only_bypass(bypass_actors: object, operator_app_id: int) -> bool:
    if not isinstance(bypass_actors, list) or len(bypass_actors) != 1:
        return False
    actor = bypass_actors[0]
    return (
        isinstance(actor, dict)
        and actor.get("actor_type") == "Integration"
        and actor.get("actor_id") == operator_app_id
        and actor.get("bypass_mode") == "always"
    )


def _no_bypass(bypass_actors: object) -> bool:
    return isinstance(bypass_actors, list) and len(bypass_actors) == 0


def _strict_update_parameters(detail: dict) -> bool:
    """Require one exact bounded update rule in the authoritative ruleset detail.

    The branch-rules endpoint is used only to establish which rulesets apply to
    the target branch. Parameters are security-significant and therefore must be
    positively re-read from the repository-ruleset detail. Missing, duplicated,
    malformed, permissive, or future-expanded parameter shapes are not accepted.
    """
    rules = detail.get("rules")
    if not isinstance(rules, list):
        return False
    updates = [row for row in rules if isinstance(row, dict) and row.get("type") == "update"]
    if len(updates) != 1:
        return False
    parameters = updates[0].get("parameters")
    return isinstance(parameters, dict) and parameters == STRICT_UPDATE_PARAMETERS


class GitHubRulesetProtectionVerifier:
    """Prove Store protection from active repository rulesets.

    The token must be trusted control/install authority with enough ruleset
    access for `bypass_actors` and rule parameters to be returned. If GitHub
    omits either, the proof is UNKNOWN rather than guessing a safe value.
    """

    test_only = False

    def __init__(
        self,
        *,
        token: str,
        operator_app_id: int,
        api_base: str = "https://api.github.com",
        api_version: str = "2022-11-28",
        http_get: Callable[[str, dict[str, str]], tuple[int, object]] = _default_get,
        clock: Callable[[], str] = _utc_now,
    ):
        if not token:
            raise ValueError("trusted GitHub token is required for ruleset verification")
        if not isinstance(operator_app_id, int) or operator_app_id <= 0:
            raise ValueError("trusted Operator GitHub App integration id is required")
        if not api_base.startswith("https://"):
            raise ValueError("GitHub ruleset API base must use HTTPS")
        self.token = token
        self.operator_app_id = operator_app_id
        self.api_base = api_base.rstrip("/")
        self.api_version = api_version
        self.http_get = http_get
        self.clock = clock

    def _branch_rules(self, repository: str, branch: str) -> list[dict] | None:
        """Read the complete active branch-rule set or fail closed.

        GitHub paginates the branch-rules endpoint. A protection proof is valid
        only when every page is retrieved and parsed; a later-page transport or
        shape failure is therefore UNKNOWN rather than a partial safe result.
        """
        rows: list[dict] = []
        page = 1
        while True:
            rules_url = (
                f"{self.api_base}/repos/{repository}/rules/branches/{quote(branch, safe='')}"
                f"?per_page={BRANCH_RULE_PAGE_SIZE}&page={page}"
            )
            status, payload = self.http_get(rules_url, _headers(self.token, self.api_version))
            if status != 200 or not isinstance(payload, list):
                return None
            if any(not isinstance(row, dict) for row in payload):
                return None
            rows.extend(payload)
            if len(payload) < BRANCH_RULE_PAGE_SIZE:
                return rows
            page += 1

    def verify(self, repository: str, state_ref: str) -> ProtectionReceipt:
        verified_at = self.clock()
        if not state_ref.startswith("refs/heads/"):
            return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
        branch = state_ref[len("refs/heads/"):]
        payload = self._branch_rules(repository, branch)
        if payload is None:
            return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)

        grouped = _rule_types_by_ruleset(payload)
        all_types = set().union(*grouped.values()) if grouped else set()
        required = REQUIRED_WRITER_RULES | REQUIRED_INTEGRITY_RULES
        if not required.issubset(all_types):
            return ProtectionReceipt(
                repository, state_ref, UNPROTECTED, "github-ruleset", verified_at,
                _digest({"rules": payload, "missing": sorted(required - all_types)}),
            )

        details: dict[int, dict] = {}
        for ruleset_id in sorted(grouped):
            detail_url = f"{self.api_base}/repos/{repository}/rulesets/{ruleset_id}?includes_parents=true"
            detail_status, detail = self.http_get(detail_url, _headers(self.token, self.api_version))
            if detail_status != 200 or not isinstance(detail, dict):
                return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
            if "bypass_actors" not in detail:
                return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
            if detail.get("enforcement") != "active" or detail.get("target") != "branch":
                return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
            if detail.get("source_type") != "Repository":
                return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
            source = detail.get("source")
            if not isinstance(source, str) or source.lower() != repository.lower():
                return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
            details[ruleset_id] = detail

        writer_rule_ids = {
            ruleset_id
            for ruleset_id, types in grouped.items()
            if types & REQUIRED_WRITER_RULES
        }

        # Every active creation/update restriction must be bypassable by exactly
        # the Operator Integration and by nobody else. Update semantics are part
        # of the protection proof: permissive or unobservable parameters are not
        # silently reduced to a mere `update` type assertion.
        for ruleset_id in writer_rule_ids:
            detail = details[ruleset_id]
            if "update" in grouped[ruleset_id] and not _strict_update_parameters(detail):
                return ProtectionReceipt(repository, state_ref, UNKNOWN, "github-ruleset", verified_at, None)
            if not _operator_only_bypass(detail.get("bypass_actors"), self.operator_app_id):
                return ProtectionReceipt(
                    repository, state_ref, UNPROTECTED, "github-ruleset", verified_at,
                    _digest({"rules": payload, "details": details}),
                )

        # Deletion/non-fast-forward protection must contain at least one layer
        # with no bypass actors for each rule type.
        for integrity_type in REQUIRED_INTEGRITY_RULES:
            candidates = [
                ruleset_id
                for ruleset_id, types in grouped.items()
                if integrity_type in types and _no_bypass(details[ruleset_id].get("bypass_actors"))
            ]
            if not candidates:
                return ProtectionReceipt(
                    repository, state_ref, UNPROTECTED, "github-ruleset", verified_at,
                    _digest({"rules": payload, "details": details}),
                )

        proof = {
            "rules": payload,
            "rulesets": {str(key): details[key] for key in sorted(details)},
            "operator_app_id": self.operator_app_id,
            "strict_update_parameters": STRICT_UPDATE_PARAMETERS,
        }
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity=f"github-ruleset:integration:{self.operator_app_id}",
            verified_at=verified_at,
            policy_digest=_digest(proof),
        )
