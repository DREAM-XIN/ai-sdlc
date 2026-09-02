#!/usr/bin/env python3
"""Focused fail-closed checks for the v0.3 real-dogfood production composition."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from operator_openai_responses import ADAPTER_ID
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_full_composition import (
    DogfoodGitHubCandidateProvider,
    V03DogfoodCompositionError,
    build_v03_dogfood_full_composition,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "v03_dogfood_full_composition.py"
REPOSITORY = "dream-xin/ai-sdlc"
HEAD = "1" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _pr(slot, *, repository=REPOSITORY, head=HEAD, draft=False, state="open"):
    return {
        "number": 431,
        "state": state,
        "draft": draft,
        "head": {"ref": slot.target_ref, "sha": head, "repo": {"full_name": repository}},
        "base": {"ref": "main", "repo": {"full_name": repository}},
    }


def candidate_tests() -> None:
    slot = require_slot("happy_path")
    calls = []

    def get(url, headers):
        calls.append((url, headers))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        require(query.get("state") == ["open"], "candidate lookup did not request open PRs")
        require(query.get("base") == ["main"], "candidate lookup escaped main base")
        require(query.get("head") == [f"dream-xin:{slot.target_ref}"], "candidate lookup escaped fixed head")
        return 200, [_pr(slot)]

    provider = DogfoodGitHubCandidateProvider(
        slot=slot,
        repository=REPOSITORY,
        token="candidate-test-token",
        http_get=get,
    )
    candidate = provider.current_candidate(
        operation_id="op-dogfood-preflight",
        repository=REPOSITORY,
        feature_id=slot.feature_id,
        target_ref=slot.target_ref,
    )
    require(candidate.candidate_pr_number == 431 and candidate.candidate_head_sha == HEAD, "candidate authority changed")
    require(len(calls) == 1, "candidate provider performed unexpected reads")

    for label, row in (
        ("cross-repository head", _pr(slot, repository="dream-xin/other")),
        ("draft PR", _pr(slot, draft=True)),
        ("closed PR", _pr(slot, state="closed")),
        ("invalid head", _pr(slot, head="bad")),
    ):
        bad = DogfoodGitHubCandidateProvider(
            slot=slot,
            repository=REPOSITORY,
            token="candidate-test-token",
            http_get=lambda _url, _headers, row=row: (200, [row]),
        )
        try:
            bad.current_candidate(
                operation_id="op-dogfood-preflight",
                repository=REPOSITORY,
                feature_id=slot.feature_id,
                target_ref=slot.target_ref,
            )
        except V03DogfoodCompositionError:
            pass
        else:
            raise AssertionError(f"{label} unexpectedly gained dogfood candidate authority")

    try:
        provider.current_candidate(
            operation_id="op-dogfood-preflight",
            repository=REPOSITORY,
            feature_id="F-OTHER",
            target_ref=slot.target_ref,
        )
    except V03DogfoodCompositionError:
        pass
    else:
        raise AssertionError("candidate lookup escaped fixed Feature identity")


def source_contract_tests() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    require(
        "build_openai_responses_production_bundle" in calls,
        "dogfood composition does not construct through Responses production bundle",
    )
    require(
        "build_v03_vertical_write_ready_operator_bundle" not in calls,
        "dogfood composition bypasses Responses and constructs raw Operator runtime",
    )
    require("responses: OpenAIResponsesProductionBundle" in source, "composition does not retain Responses authority")
    require("return self.responses.adapter" in source, "composition does not expose exact production adapter")
    require("operation.resume" in source, "composition lost server-only capability leak assertion")


def early_adapter_gate_test() -> None:
    slot = require_slot("happy_path")
    config = TrustedOperatorRuntimeConfig(
        target_repository=REPOSITORY,
        store_repository=REPOSITORY,
        installation_ref="main",
        store_checkout=Path("."),
        principal="composition-test",
        feature_bindings=(TrustedFeatureBinding(slot.feature_id, slot.target_ref),),
    )
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-copilot.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    try:
        build_v03_dogfood_full_composition(
            slot=slot,
            config=config,
            adapter_id="fixture.bypass",
            target_read_token="read-token",
            actions_token="actions-token",
            event_write_token="event-token",
            control_repository=REPOSITORY,
            workflows=workflows,
            protection_verifier=object(),
            policy_authority=object(),
            trusted_context_digest="digest",
            collector_namespace_policy="collector",
            trusted_role_policy="roles",
            clock=lambda: "2026-08-25T12:00:00Z",
        )
    except ValueError as exc:
        require("OpenAI Responses adapter" in str(exc), "wrong early adapter rejection")
    else:
        raise AssertionError("non-Responses adapter reached dogfood production construction")

    require(ADAPTER_ID, "Responses adapter id unexpectedly empty")
    signature = inspect.signature(build_v03_dogfood_full_composition)
    require("adapter_id" in signature.parameters, "dogfood builder lost explicit adapter binding")


def main() -> None:
    candidate_tests()
    source_contract_tests()
    early_adapter_gate_test()
    print("v0.3 real-dogfood Responses production composition: PASS")


if __name__ == "__main__":
    main()
