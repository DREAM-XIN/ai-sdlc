#!/usr/bin/env python3
"""Deterministic validation for the trusted v0.3 real-dogfood upstream gate."""
from __future__ import annotations

from dataclasses import replace
import io
import json
import zipfile

from gh_aw_provider_registry import load_registry
from v03_dogfood_execution_bindings import credential_identities
from v03_dogfood_live_gate import (
    ISSUE221_FINAL_LEDGER_ARTIFACT_DIGEST,
    ISSUE221_FINAL_LEDGER_ARTIFACT_ID,
    ISSUE221_FINAL_LEDGER_ARTIFACT_NAME,
    ISSUE221_FINAL_LEDGER_RUN_ID,
    ISSUE221_FINAL_LEDGER_WORKFLOW,
    Issue221Closure,
    V03DogfoodLiveGateError,
    assemble_dogfood_live_gate,
    public_gate,
    select_review_anchor,
    validate_pinned_issue_221_final_ledger,
)
from v03_dogfood_issue221_compatibility import SOURCE_MAIN

SHA = "a" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def ready_env():
    registry = load_registry()
    env = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": "DREAM-XIN/ai-sdlc",
        "GITHUB_API_URL": "https://api.github.test",
        "AI_SDLC_ACTIONS_READ_TOKEN": "presence-only-test-token",
    }
    for identity in credential_identities(registry):
        env[f"HAS_{identity}"] = "false"
    # Other configured providers must not override the explicit paid DeepSeek
    # choice for current dogfood; their shared support remains unchanged.
    env["HAS_COPILOT_GITHUB_TOKEN"] = "true"
    env["HAS_GEMINI_API_KEY"] = "true"
    env["HAS_DEEPSEEK_API_KEY"] = "true"
    return env


def closure(**kwargs):
    return Issue221Closure(
        trusted_main_head_sha=kwargs["installation_sha"],
        accepted_record_count=11,
        accepted_workflow_run_count=11,
        satisfied_scenario_count=13,
        workflow_run_ids=tuple(range(101, 112)),
        ledger_digest="sha256:" + "b" * 64,
    )


def expect_failure(*, scenario="happy_path", env=None, verifier=closure, label):
    try:
        assemble_dogfood_live_gate(
            scenario=scenario,
            env=ready_env() if env is None else env,
            checkout_sha=SHA,
            issue221_verifier=verifier,
        )
    except Exception:
        return
    raise AssertionError(f"{label} unexpectedly passed dogfood gate")



def pinned_ledger_fixture():
    selection = {
        "schema_version": "ai-sdlc.v03-effect-safety-final-selection/v1",
        "issue": 221,
        "trusted_main_head_sha": SOURCE_MAIN,
        "record_count": 11,
        "scenario_count": 13,
        "workflow_run_ids": list(range(1001, 1012)),
        "records": [],
        "authority_set_sha256": "a" * 64,
        "release_eligible": True,
    }
    ledger = {
        "status": "PASS",
        "overall_issue_221_pass": True,
        "accepted_record_count": 11,
        "accepted_workflow_run_count": 11,
        "satisfied_scenarios": [f"scenario-{i}" for i in range(13)],
        "unresolved_scenarios": [],
        "deterministic_evidence_accepted": False,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("nested/v03-effect-safety-final-selection.json", json.dumps(selection))
        bundle.writestr("nested/v03-effect-safety-final-ledger.json", json.dumps(ledger))
    run = {
        "id": ISSUE221_FINAL_LEDGER_RUN_ID,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": SOURCE_MAIN,
        "path": ISSUE221_FINAL_LEDGER_WORKFLOW,
    }
    artifact = {
        "id": ISSUE221_FINAL_LEDGER_ARTIFACT_ID,
        "name": ISSUE221_FINAL_LEDGER_ARTIFACT_NAME,
        "expired": False,
        "digest": ISSUE221_FINAL_LEDGER_ARTIFACT_DIGEST,
        "workflow_run": {
            "id": ISSUE221_FINAL_LEDGER_RUN_ID,
            "head_branch": "main",
            "head_sha": SOURCE_MAIN,
        },
    }
    return run, artifact, buffer.getvalue()


def main():
    run, artifact, archive = pinned_ledger_fixture()
    selection, ledger = validate_pinned_issue_221_final_ledger(
        run=run, artifact=artifact, archive=archive,
    )
    require(selection["scenario_count"] == 13 and ledger["overall_issue_221_pass"] is True,
            "pinned Issue #221 final ledger fixture did not validate")
    for bad_run in (
        dict(run, id=ISSUE221_FINAL_LEDGER_RUN_ID + 1),
        dict(run, head_sha="0" * 40),
        dict(run, conclusion="failure"),
    ):
        try:
            validate_pinned_issue_221_final_ledger(run=bad_run, artifact=artifact, archive=archive)
        except V03DogfoodLiveGateError:
            pass
        else:
            raise AssertionError("drifted pinned Issue #221 run unexpectedly passed")
    for bad_artifact in (
        dict(artifact, id=ISSUE221_FINAL_LEDGER_ARTIFACT_ID + 1),
        dict(artifact, expired=True),
        dict(artifact, digest="sha256:" + "0" * 64),
    ):
        try:
            validate_pinned_issue_221_final_ledger(run=run, artifact=bad_artifact, archive=archive)
        except V03DogfoodLiveGateError:
            pass
        else:
            raise AssertionError("drifted pinned Issue #221 artifact unexpectedly passed")
    try:
        validate_pinned_issue_221_final_ledger(run=run, artifact=artifact, archive=b"not-a-zip")
    except V03DogfoodLiveGateError:
        pass
    else:
        raise AssertionError("malformed pinned Issue #221 archive unexpectedly passed")

    for scenario in ("happy_path", "review_remediation", "session_recovery"):
        gate = assemble_dogfood_live_gate(
            scenario=scenario,
            env=ready_env(),
            checkout_sha=SHA,
            issue221_verifier=closure,
        )
        require(gate.installation_commit_sha == SHA, "gate installation SHA drifted")
        require(gate.issue221.satisfied_scenario_count == 13, "#221 closure lost 13-row proof")
        require(len(gate.issue221.workflow_run_ids) == 11, "#221 closure lost 11 source runs")
        bindings = {row.role: row for row in gate.bindings}
        expected_workflows = {
            "developer": "ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml",
            "reviewer": "ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml",
            "qa": "ai-sdlc-gh-aw-qa-deepseek-v03-bounded-local.lock.yml",
        }
        for role, workflow in expected_workflows.items():
            require(bindings[role].selected_profile == "deepseek", role + " current paid provider drifted")
            require(bindings[role].candidate_order == ("deepseek",), role + " current route is not explicit")
            require(bindings[role].worker_workflow == workflow, role + " actual selected workflow drifted")
            require(bindings[role].accepted_credential_identities == ("DEEPSEEK_API_KEY",),
                    role + " credential identity was relabeled")
        only_paid = ready_env()
        only_paid["HAS_COPILOT_GITHUB_TOKEN"] = "false"
        only_paid["HAS_GEMINI_API_KEY"] = "false"
        paid_gate = assemble_dogfood_live_gate(
            scenario=scenario, env=only_paid, checkout_sha=SHA, issue221_verifier=closure,
        )
        require(paid_gate.bindings == gate.bindings,
                "unselected provider absence changed current dogfood binding")
        rendered = public_gate(gate)
        require(rendered["status"] == "READY", "public gate did not render READY")
        require(rendered["model_called"] is False, "gate claimed a model call")
        require(rendered["worker_dispatched"] is False, "gate claimed Worker dispatch")
        require(rendered["operator_store_mutated"] is False, "gate claimed Store mutation")
        require(rendered["dogfood_evidence_created"] is False, "gate fabricated dogfood evidence")

    expect_failure(scenario="unknown", label="unknown scenario")

    wrong_ref = ready_env()
    wrong_ref["GITHUB_REF"] = "refs/heads/release/test"
    expect_failure(env=wrong_ref, label="non-main trusted context")

    wrong_repo = ready_env()
    wrong_repo["GITHUB_REPOSITORY"] = "DREAM-XIN/other"
    expect_failure(env=wrong_repo, label="wrong repository")

    no_paid = ready_env()
    no_paid["HAS_DEEPSEEK_API_KEY"] = "false"
    # Other credentials remain present: no silent switch to unavailable quota.
    for scenario in ("happy_path", "review_remediation", "session_recovery"):
        expect_failure(scenario=scenario, env=no_paid, label="missing paid DeepSeek credential")

    def not_closed(**kwargs):
        raise V03DogfoodLiveGateError("Issue #221 final live ledger is not 13/13 PASS")

    expect_failure(verifier=not_closed, label="Issue #221 unresolved")

    def wrong_generation(**kwargs):
        return replace(closure(**kwargs), trusted_main_head_sha="c" * 40)

    expect_failure(verifier=wrong_generation, label="Issue #221 generation mismatch")

    digest = "sha256:" + "d" * 64
    review_head = "e" * 40
    pulls = [{"number": 348, "head": {"sha": review_head}}]
    reviews = {
        348: [{
            "id": 7001,
            "state": "COMMENTED",
            "commit_id": review_head,
            "body": "Independent Runtime / Dogfood Release-Evidence Review — PASS\n"
                    + "Issue221-Compatibility-Anchor: " + digest,
        }]
    }
    anchor = select_review_anchor(
        pulls=pulls, reviews_by_pr=reviews, installation_sha=SHA, reviewed_delta_digest=digest,
    )
    require(anchor["pull_number"] == 348 and anchor["review_commit_id"] == review_head,
            "reviewed delta anchor identity drifted")
    bad = {348: [dict(reviews[348][0], commit_id="f" * 40)]}
    try:
        select_review_anchor(pulls=pulls, reviews_by_pr=bad, installation_sha=SHA, reviewed_delta_digest=digest)
    except V03DogfoodLiveGateError:
        pass
    else:
        raise AssertionError("stale-head independent review anchor unexpectedly passed")
    bad = {348: [dict(reviews[348][0], body="Independent Runtime / Dogfood Release-Evidence Review — PASS")]}
    try:
        select_review_anchor(pulls=pulls, reviews_by_pr=bad, installation_sha=SHA, reviewed_delta_digest=digest)
    except V03DogfoodLiveGateError:
        pass
    else:
        raise AssertionError("review without exact compatibility digest unexpectedly passed")

    print("v0.3 dogfood live upstream gate: PASS")


if __name__ == "__main__":
    main()
