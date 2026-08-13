#!/usr/bin/env python3
"""Deterministic regression for GitHub live ruleset update normalization."""
from __future__ import annotations

import copy
from urllib.parse import parse_qs, urlparse

from operator_store_github_ruleset_protection import GitHubRulesetProtectionVerifier
from operator_store_protection import PROTECTED, UNKNOWN

REPOSITORY = "DREAM-XIN/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
BRANCH = "ai-sdlc-operator-state"
APP_ID = 4576406
WRITER_ID = 20775740
INTEGRITY_ID = 20775741
UPDATED = "2026-08-13T02:08:25.535Z"
UPDATED_V4 = "2026-08-13T02:09:25.535Z"
NOW = "2026-08-13T02:12:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _writer_rules(parameters):
    update = {"type": "update"}
    if parameters is not None:
        update["parameters"] = copy.deepcopy(parameters)
    return [{"type": "creation"}, update]


def _writer_detail(parameters=None, *, updated_at=UPDATED):
    return {
        "id": WRITER_ID,
        "name": "AI-SDLC Operator Store writer",
        "target": "branch",
        "source_type": "Repository",
        "source": REPOSITORY,
        "enforcement": "active",
        "conditions": {"ref_name": {"exclude": [], "include": [STATE_REF]}},
        "rules": _writer_rules(parameters),
        "bypass_actors": [
            {"actor_id": APP_ID, "actor_type": "Integration", "bypass_mode": "always"}
        ],
        "updated_at": updated_at,
    }


def _integrity_detail():
    return {
        "id": INTEGRITY_ID,
        "name": "AI-SDLC Operator Store integrity",
        "target": "branch",
        "source_type": "Repository",
        "source": REPOSITORY,
        "enforcement": "active",
        "conditions": {"ref_name": {"exclude": [], "include": [STATE_REF]}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
        "bypass_actors": [],
        "updated_at": "2026-08-13T02:08:26.047Z",
    }


def _advance_current_to_v4(api):
    api.current_parameters = {"update_allows_fetch_and_merge": True}
    api.current_updated_at = UPDATED_V4
    api.history_version_id = 4
    api.history_updated_at = UPDATED_V4


def _advance_history_only_to_v4(api):
    api.history_version_id = 4
    api.history_updated_at = UPDATED_V4


class FakeLiveRulesetApi:
    def __init__(self):
        self.current_parameters = None
        self.current_updated_at = UPDATED
        self.history_status = 200
        self.history_version_id = 3
        self.history_updated_at = UPDATED
        self.version_status = 200
        self.version_updated_at = UPDATED
        self.version_parameters = {"update_allows_fetch_and_merge": False}
        self.version_mutator = None
        self.after_first_history = None
        self.after_version_fetch = None
        self.history_reads = 0
        self.calls = []

    def get(self, url, headers):
        self.calls.append(url)
        parsed = urlparse(url)
        path = parsed.path
        query = parse_qs(parsed.query)

        branch_path = f"/repos/{REPOSITORY}/rules/branches/{BRANCH}"
        if path == branch_path:
            page = int((query.get("page") or ["1"])[0])
            if page > 1:
                return 200, []
            return 200, [
                {"type": "deletion", "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY, "ruleset_id": INTEGRITY_ID},
                {"type": "non_fast_forward", "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY, "ruleset_id": INTEGRITY_ID},
                {"type": "creation", "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY, "ruleset_id": WRITER_ID},
                {"type": "update", "ruleset_source_type": "Repository", "ruleset_source": REPOSITORY, "ruleset_id": WRITER_ID},
            ]

        writer_path = f"/repos/{REPOSITORY}/rulesets/{WRITER_ID}"
        if path == writer_path:
            return 200, _writer_detail(self.current_parameters, updated_at=self.current_updated_at)

        integrity_path = f"/repos/{REPOSITORY}/rulesets/{INTEGRITY_ID}"
        if path == integrity_path:
            return 200, _integrity_detail()

        history_path = f"/repos/{REPOSITORY}/rulesets/{WRITER_ID}/history"
        if path == history_path:
            if self.history_status != 200:
                return self.history_status, {}
            self.history_reads += 1
            summary = {
                "version_id": self.history_version_id,
                "actor": {"id": 1, "type": "User"},
                "updated_at": self.history_updated_at,
            }
            if self.history_reads == 1 and self.after_first_history is not None:
                mutator = self.after_first_history
                self.after_first_history = None
                mutator(self)
            return 200, [summary]

        version_path = f"/repos/{REPOSITORY}/rulesets/{WRITER_ID}/history/3"
        if path == version_path:
            if self.version_status != 200:
                return self.version_status, {}
            state = _writer_detail(self.version_parameters, updated_at=self.version_updated_at)
            if self.version_mutator is not None:
                self.version_mutator(state)
            state.pop("updated_at", None)
            payload = {
                "version_id": 3,
                "actor": {"id": 1, "type": "User"},
                "updated_at": self.version_updated_at,
                "state": state,
            }
            if self.after_version_fetch is not None:
                mutator = self.after_version_fetch
                self.after_version_fetch = None
                mutator(self)
            return 200, payload

        return 404, {}


def verify(api):
    return GitHubRulesetProtectionVerifier(
        token="trusted-admin-token",
        operator_app_id=APP_ID,
        http_get=api.get,
        clock=lambda: NOW,
    ).verify(REPOSITORY, STATE_REF)


def history_was_called(api):
    needle = f"/repos/{REPOSITORY}/rulesets/{WRITER_ID}/history"
    return any(needle in url for url in api.calls)


def main():
    live = FakeLiveRulesetApi()
    receipt = verify(live)
    require(receipt.status == PROTECTED and receipt.policy_digest, "live-normalized omission was not positively resolved through stable current version state")
    require(history_was_called(live), "omission-only live shape did not use version authority")
    require(live.history_reads == 2, "omission fallback did not re-read latest history after version attestation")

    explicit_false = FakeLiveRulesetApi()
    explicit_false.current_parameters = {"update_allows_fetch_and_merge": False}
    receipt = verify(explicit_false)
    require(receipt.status == PROTECTED, "explicit bounded update parameters regressed")
    require(not history_was_called(explicit_false), "explicit bounded detail unexpectedly required history fallback")

    permissive = FakeLiveRulesetApi()
    permissive.current_parameters = {"update_allows_fetch_and_merge": True}
    receipt = verify(permissive)
    require(receipt.status == UNKNOWN, "explicit permissive current update parameters were accepted")
    require(not history_was_called(permissive), "history fallback overrode an explicit permissive current value")

    expanded = FakeLiveRulesetApi()
    expanded.current_parameters = {
        "update_allows_fetch_and_merge": False,
        "future_relaxation": False,
    }
    receipt = verify(expanded)
    require(receipt.status == UNKNOWN, "expanded current update parameters were accepted")
    require(not history_was_called(expanded), "history fallback overrode an explicit ambiguous current value")

    history_unavailable = FakeLiveRulesetApi()
    history_unavailable.history_status = 403
    require(verify(history_unavailable).status == UNKNOWN, "unavailable admin-gated history did not fail closed")

    stale_history = FakeLiveRulesetApi()
    stale_history.history_updated_at = "2026-08-13T02:07:25.535Z"
    require(verify(stale_history).status == UNKNOWN, "stale latest-history summary authorized current protection")

    stale_version = FakeLiveRulesetApi()
    stale_version.version_updated_at = "2026-08-13T02:07:25.535Z"
    require(verify(stale_version).status == UNKNOWN, "stale ruleset version authorized current protection")

    permissive_version = FakeLiveRulesetApi()
    permissive_version.version_parameters = {"update_allows_fetch_and_merge": True}
    require(verify(permissive_version).status == UNKNOWN, "permissive version-state update semantics were accepted")

    wrong_identity = FakeLiveRulesetApi()
    wrong_identity.version_mutator = lambda state: state.update({"source": "DREAM-XIN/other"})
    require(verify(wrong_identity).status == UNKNOWN, "mismatched version-state repository identity was accepted")

    wrong_bypass = FakeLiveRulesetApi()
    wrong_bypass.version_mutator = lambda state: state["bypass_actors"].append(
        {"actor_id": 77, "actor_type": "User", "bypass_mode": "always"}
    )
    require(verify(wrong_bypass).status == UNKNOWN, "mismatched version-state bypass identity was accepted")

    missing_current_timestamp = FakeLiveRulesetApi()
    missing_current_timestamp.current_updated_at = None
    require(verify(missing_current_timestamp).status == UNKNOWN, "missing current version timestamp did not fail closed")

    history_drift_after_h1 = FakeLiveRulesetApi()
    history_drift_after_h1.after_first_history = _advance_history_only_to_v4
    require(
        verify(history_drift_after_h1).status == UNKNOWN,
        "latest history advancing from V3 to V4 after H1 still authorized superseded V3",
    )
    require(history_drift_after_h1.history_reads == 2, "TOCTOU history regression did not reach final history revalidation")

    current_drift_after_v3 = FakeLiveRulesetApi()
    current_drift_after_v3.after_version_fetch = _advance_current_to_v4
    require(
        verify(current_drift_after_v3).status == UNKNOWN,
        "current ruleset advancing to permissive V4 after V3 fetch still authorized V3",
    )

    print("Operator Store live ruleset compatibility validation passed")


if __name__ == "__main__":
    main()