#!/usr/bin/env python3
"""Focused fail-closed checks for the v0.3 real-dogfood production composition."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from operator_openai_responses import ADAPTER_ID
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_runtime_preflight import _execution_bindings
from v03_dogfood_full_composition import (
    DogfoodCandidateHandoff,
    DogfoodExecutionBoundDispatchGateway,
    DogfoodGitHubCandidateProvider,
    DogfoodTrustedCallbackCoordinator,
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


def handoff_and_supersession_tests() -> None:
    slot = require_slot("review_remediation")
    prior = "1" * 40
    developer = "2" * 40
    persisted = "3" * 40
    state = {"ref": prior, "events": [], "facts": [], "persisted": []}

    def fixture_pr(head):
        return {
            "number": 431,
            "state": "open",
            "draft": False,
            "head": {"ref": slot.target_ref, "sha": head, "repo": {"full_name": REPOSITORY}},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        }

    def read(url, _headers):
        if "/pulls?" in url:
            return 200, [fixture_pr(state["ref"])]
        if f"/compare/{developer}...{persisted}" in url:
            return 200, {
                "status": "ahead", "behind_by": 0,
                "merge_base_commit": {"sha": developer},
            }
        raise AssertionError("unexpected candidate read: " + url)

    provider = DogfoodGitHubCandidateProvider(
        slot=slot, repository=REPOSITORY, token="read-token", http_get=read,
    )

    def write(method, url, _headers, body):
        if method == "GET" and url.endswith("/pulls/900"):
            return 200, {
                "number": 900, "state": "open", "draft": True,
                "base": {"ref": slot.target_ref}, "head": {"sha": developer},
            }
        if method == "GET" and f"/compare/{prior}...{developer}" in url:
            return 200, {
                "status": "ahead", "ahead_by": 1, "behind_by": 0,
                "merge_base_commit": {"sha": prior},
            }
        if method == "GET" and "/git/refs/heads/" in url:
            return 200, {"object": {"sha": state["ref"]}}
        if method == "PATCH" and "/git/refs/heads/" in url:
            require(body == {"sha": developer, "force": False}, "handoff was not a non-force exact ref update")
            state["ref"] = developer
            return 200, {"object": {"sha": developer}}
        raise AssertionError(f"unexpected handoff request: {method} {url}")

    handoff = DogfoodCandidateHandoff(
        slot=slot,
        repository=REPOSITORY,
        token="write-token",
        candidate_provider=provider,
        http_request=write,
    )
    context = SimpleNamespace(
        role="developer", feature_id=slot.feature_id, target_ref=slot.target_ref,
        candidate_head_sha=prior, operation_id="op-handoff", dispatch_id="dispatch-1",
        task_id=slot.feature_id + "-IMPLEMENTATION",
    )
    uri = (
        f"docs/features/{slot.feature_id}/worker-runs/dispatch-1/"
        f"developer-pr-900-{developer}.json"
    )
    executor = SimpleNamespace(_record_fact=lambda op, typ, payload: state["facts"].append((op, typ, payload)))
    handoff.adopt(
        executor=executor, context=context, callback_id="callback-1",
        receipts=[{"kind": "artifact", "trusted_uri": uri}],
    )
    require(state["ref"] == developer, "Developer output was not adopted onto fixed fixture ref")
    require(
        len(state["facts"]) == 1
        and state["facts"][0][1] == "candidate.handoff.adopted"
        and state["facts"][0][2]["source_candidate_head_sha"] == developer,
        "handoff did not retain exact Developer output identity",
    )

    envelope = {
        "trusted_context": {
            "role": "developer", "candidate_head_sha": prior, "dispatch_id": "dispatch-1",
        },
        "collected_outputs": [{"kind": "artifact", "trusted_uri": uri}],
    }
    state["events"] = [
        {
            "event_type": "worker.callback.recorded",
            "payload": {"callback_id": "callback-1", "trusted_callback_envelope": envelope},
        },
        {
            "event_type": "candidate.handoff.adopted",
            "payload": state["facts"][0][2],
        },
    ]
    provider.bind_runtime(
        SimpleNamespace(
            backend=SimpleNamespace(
                read_snapshot=lambda: SimpleNamespace(),
            )
        )
    )
    import v03_dogfood_full_composition as composition
    original_events = composition.operation_events
    try:
        composition.operation_events = lambda _snapshot, _operation_id: list(state["events"])
        pinned = provider.current_candidate(
            operation_id="op-handoff", repository=REPOSITORY,
            feature_id=slot.feature_id, target_ref=slot.target_ref,
        )
        require(pinned.candidate_head_sha == prior, "incomplete callback lost its pre-handoff candidate fence")
        state["ref"] = persisted
        state["events"].extend([
            {
                "event_type": "feature.event.translated",
                "payload": {"callback_id": "callback-1", "feature_event_id": "EVT-1"},
            },
            {
                "event_type": "persist.confirmed",
                "payload": {"feature_event_id": "EVT-1"},
            },
        ])
        current = provider.current_candidate(
            operation_id="op-handoff", repository=REPOSITORY,
            feature_id=slot.feature_id, target_ref=slot.target_ref,
        )
        require(current.candidate_head_sha == persisted, "confirmed handoff remained pinned to predecessor head")
    finally:
        composition.operation_events = original_events

    feature = SimpleNamespace(
        revision=7, current_stage="code-review", manifest_digest="manifest-digest",
        candidate_head_sha=persisted, target_ref=slot.target_ref,
    )
    manifest = {
        "tasks": [{"id": "remediation-1", "kind": "remediation", "status": "DONE"}],
        "artifacts": [
            {"id": "artifact-old", "type": "implementation", "status": "draft", "uri": "old-uri"},
            {"id": "artifact-new", "type": "implementation", "status": "draft", "uri": uri},
        ],
    }
    fake_executor = SimpleNamespace(
        feature_gateway=SimpleNamespace(
            read_feature=lambda **_kwargs: (feature, manifest),
        ),
        runtime=SimpleNamespace(clock=lambda: "2026-10-03T00:00:00Z"),
        _record_fact=lambda op, typ, payload: state["facts"].append((op, typ, payload)),
        _persist=lambda op, event, bound: state["persisted"].append((op, event, bound)),
    )
    coordinator = DogfoodTrustedCallbackCoordinator(
        delegate=SimpleNamespace(executor=fake_executor),
        candidate_handoff=handoff,
    )
    coordinator._supersede_remediation_artifact(
        context=SimpleNamespace(operation_id="op-handoff", task_id="remediation-1", feature_id=slot.feature_id),
        callback_id="callback-2",
        receipts=[{"kind": "artifact", "trusted_uri": uri}],
    )
    require(len(state["persisted"]) == 1, "remediation supersession did not enter protected Persist")
    changes = state["persisted"][0][1]["changes"]
    require(
        changes == [{"kind": "artifact", "id": "artifact-old", "status": "superseded"}],
        "remediation supersession did not preserve exactly one approvable replacement",
    )

    for broken in (
        [fixture_pr(prior)],
        [
            {"kind": "artifact", "trusted_uri": uri},
            {"kind": "artifact", "trusted_uri": uri},
        ],
    ):
        if isinstance(broken[0], dict) and "head" in broken[0]:
            continue
        try:
            handoff.adopt(
                executor=executor, context=context, callback_id="callback-bad",
                receipts=broken,
            )
        except V03DogfoodCompositionError:
            pass
        else:
            raise AssertionError("ambiguous Developer output unexpectedly gained handoff authority")


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
    require("candidate.handoff.adopted" in source, "Developer output handoff lost durable authority fact")
    require("remediation_artifact_supersession" in source, "remediation lifecycle lost explicit supersession")


def readiness_execution_binding_test() -> None:
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    rows = (
        SimpleNamespace(
            role="developer", stage="implementation", selected_profile="copilot",
            worker_workflow=workflows.developer_workflow, specialized_role_worker=False,
            accepted_credential_identities=("COPILOT_GITHUB_TOKEN",),
        ),
        SimpleNamespace(
            role="reviewer", stage="code-review", selected_profile="deepseek",
            worker_workflow=workflows.reviewer_workflow, specialized_role_worker=True,
            accepted_credential_identities=("DEEPSEEK_API_KEY",),
        ),
        SimpleNamespace(
            role="qa", stage="verification", selected_profile="gemini",
            worker_workflow=workflows.qa_workflow, specialized_role_worker=True,
            accepted_credential_identities=("GEMINI_API_KEY",),
        ),
    )
    bindings = _execution_bindings(SimpleNamespace(bindings=rows), workflows)
    require(bindings["developer"]["worker_id"] == "ai-sdlc-gh-aw-worker", "Copilot Developer worker id drifted")
    require(bindings["developer"]["profile"] == "copilot", "Copilot Developer profile drifted")
    require(bindings["reviewer"]["worker_id"] == "code-review-reviewer-deepseek", "DeepSeek Reviewer worker id drifted")
    require(bindings["reviewer"]["credential_name"] == "DEEPSEEK_API_KEY", "DeepSeek Reviewer credential drifted")
    require(bindings["qa"]["worker_id"] == "verification-qa-gemini", "Gemini QA worker id drifted")
    require(
        {bindings[role]["workflow_file"] for role in bindings}
        == {workflows.developer_workflow, workflows.reviewer_workflow, workflows.qa_workflow},
        "readiness execution binding workflow set drifted",
    )


def execution_binding_wrapper_test() -> None:
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    raw = __import__("operator_vertical_gh_aw").GhAwVerticalRoleDispatchGateway(
        transport=object(), workflows=workflows,
    )
    bindings = {
        "developer": {
            "worker_id": "ai-sdlc-gh-aw-worker",
            "role": "developer",
            "profile": "copilot",
            "workflow_file": workflows.developer_workflow,
            "selection_policy_id": "v03-frozen-vertical-workflow-map/v1",
            "default_branch": "main",
        },
        "reviewer": {
            "worker_id": "code-review-reviewer-deepseek",
            "role": "reviewer",
            "profile": "deepseek",
            "workflow_file": workflows.reviewer_workflow,
            "selection_policy_id": "v03-frozen-reviewer-provider-order/v2",
            "default_branch": "main",
            "credential_name": "DEEPSEEK_API_KEY",
        },
        "qa": {
            "worker_id": "verification-qa-gemini",
            "role": "qa",
            "profile": "gemini",
            "workflow_file": workflows.qa_workflow,
            "selection_policy_id": "v03-frozen-vertical-workflow-map/v1",
            "default_branch": "main",
        },
    }
    bound = DogfoodExecutionBoundDispatchGateway(delegate=raw, execution_bindings=bindings)
    require(bound.transport is raw.transport and bound.workflows is workflows, "dogfood wrapper replaced production transport")
    require(
        bound.execution_binding(dispatch={"role": "developer"}) == bindings["developer"],
        "Copilot Developer execution binding drifted",
    )
    try:
        bound.execution_binding(dispatch={"role": "product"})
    except Exception:
        pass
    else:
        raise AssertionError("unknown dogfood role escaped exact execution binding set")


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
            execution_bindings={
                "developer": {"worker_id": "x", "role": "developer", "profile": "copilot", "workflow_file": workflows.developer_workflow, "selection_policy_id": "p", "default_branch": "main"},
                "reviewer": {"worker_id": "y", "role": "reviewer", "profile": "copilot", "workflow_file": workflows.reviewer_workflow, "selection_policy_id": "p", "default_branch": "main"},
                "qa": {"worker_id": "z", "role": "qa", "profile": "gemini", "workflow_file": workflows.qa_workflow, "selection_policy_id": "p", "default_branch": "main"},
            },
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
    handoff_and_supersession_tests()
    source_contract_tests()
    readiness_execution_binding_test()
    execution_binding_wrapper_test()
    early_adapter_gate_test()
    print("v0.3 real-dogfood Responses production composition: PASS")


if __name__ == "__main__":
    main()
