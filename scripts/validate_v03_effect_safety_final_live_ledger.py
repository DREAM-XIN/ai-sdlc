#!/usr/bin/env python3
"""Deterministic zero-effect validation for the final Issue #221 live ledger aggregator."""
from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

from v03_effect_safety_final_live_ledger import (
    DISPATCH_RECOVERY_SCENARIOS,
    LAUNCH_CANCEL,
    ORIGINAL_SEQUENCE,
    REMAINING_SIX_SCENARIOS,
    ProducerSpec,
    SelectedArtifact,
    V03FinalLiveLedgerError,
    aggregate_selected_artifacts,
    producer_plan,
    select_exact_artifacts,
    validate_closed_plan,
)
from v03_effect_safety_live_ledger import REQUIRED_SCENARIOS, ReleaseAuthority
from v03_effect_safety_live_ledger_authority_set import authority_set_document
from v03_scenario_fixture_pool import inventory_document

ROOT = Path(__file__).resolve().parents[1]
LIVE_WORKFLOW = ROOT / ".github" / "workflows" / "v03-final-live-ledger.yml"
VALIDATION_WORKFLOW = ROOT / ".github" / "workflows" / "validate-v03-final-live-ledger.yml"
INSTALLATION = "a" * 40


def require(value, message):
    if not value:
        raise AssertionError(message)


def expect_error(fn, fragment):
    try:
        fn()
    except V03FinalLiveLedgerError as exc:
        require(fragment in str(exc), f"wrong failure: {exc}")
    else:
        raise AssertionError(f"expected V03FinalLiveLedgerError containing {fragment!r}")


def canonical_authority_set():
    authority = ReleaseAuthority(
        repository="dream-xin/ai-sdlc",
        feature_id="unused-original-fixture",
        target_ref="verification/unused-original-fixture",
        trusted_main_head_sha=INSTALLATION,
        materialization_commit_sha="b" * 40,
        policy_bundle_digest="c" * 64,
    )
    return authority_set_document(
        authority=authority,
        fixture_pool_inventory_digest=inventory_document()["inventory_digest"],
    )


def archive_for(spec: ProducerSpec, authority_doc, workflow_run_id: int):
    values = {
        spec.evidence_name: {
            "schema_version": "fixture-evidence",
            "status": "PASS",
            "completed_issue_221_scenarios": list(spec.scenarios),
        },
        spec.provenance_name: {
            "schema_version": "fixture-provenance",
            "record_id": spec.artifact_name,
            "github_workflow_run_id": workflow_run_id,
        },
    }
    if spec.authority_set_name:
        values[spec.authority_set_name] = authority_doc
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, value in values.items():
            bundle.writestr(f"nested/{name}", json.dumps(value, sort_keys=True) + "\n")
    return buf.getvalue()


def validate_plan():
    plan = validate_closed_plan(producer_plan())
    require(len(plan) == 11, "producer plan must contain 11 records")
    claims = [scenario for row in plan for scenario in row.scenarios]
    require(len(claims) == 13, "producer plan must contain 13 rows")
    require(set(claims) == set(REQUIRED_SCENARIOS), "producer plan must equal closed Issue #221 rows")
    require(ORIGINAL_SEQUENCE.scenarios == ("lost-ack-crash-takeover", "persist-ack-loss-recovery"), "original sequence drifted")
    require(
        LAUNCH_CANCEL.scenarios == (
            "cancellation-before-launch-authorization",
            "launch-authorization-before-cancellation",
        ),
        "launch/cancel pair drifted",
    )
    require(len(DISPATCH_RECOVERY_SCENARIOS) == 3, "dispatch/recovery trio drifted")
    require(len(REMAINING_SIX_SCENARIOS) == 6, "remaining-six inventory drifted")

    expect_error(lambda: validate_closed_plan(plan[:-1]), "exactly 11")
    duplicate = list(plan)
    duplicate[-1] = ProducerSpec(
        workflow_file=duplicate[-1].workflow_file,
        artifact_name=duplicate[-2].artifact_name,
        evidence_name=duplicate[-1].evidence_name,
        provenance_name=duplicate[-1].provenance_name,
        authority_set_name=duplicate[-1].authority_set_name,
        scenarios=duplicate[-1].scenarios,
    )
    expect_error(lambda: validate_closed_plan(duplicate), "reuses an artifact name")


def validate_selection():
    plan = producer_plan()
    run_map = {}
    artifact_map = {}
    for index, spec in enumerate(plan):
        run_id = 1000 + index
        artifact_id = 2000 + index
        run_map.setdefault(spec.workflow_file, []).append({
            "id": run_id,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": INSTALLATION,
        })
        artifact_map[run_id] = [{
            "id": artifact_id,
            "name": spec.artifact_name,
            "expired": False,
        }]

    selected = select_exact_artifacts(
        plan=plan,
        installation_sha=INSTALLATION,
        list_runs=lambda workflow: list(run_map.get(workflow, [])),
        list_artifacts=lambda run_id: list(artifact_map.get(run_id, [])),
    )
    require(len(selected) == 11, "selection must contain 11 records")
    require(len({row.workflow_run_id for row in selected}) == 11, "selection must bind 11 runs")

    first = plan[0]
    duplicate_runs = {name: list(rows) for name, rows in run_map.items()}
    duplicate_run_id = 9001
    duplicate_runs[first.workflow_file].append({
        "id": duplicate_run_id,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": INSTALLATION,
    })
    duplicate_artifacts = {key: list(value) for key, value in artifact_map.items()}
    duplicate_artifacts[duplicate_run_id] = [{
        "id": 9901,
        "name": first.artifact_name,
        "expired": False,
    }]
    expect_error(
        lambda: select_exact_artifacts(
            plan=plan,
            installation_sha=INSTALLATION,
            list_runs=lambda workflow: list(duplicate_runs.get(workflow, [])),
            list_artifacts=lambda run_id: list(duplicate_artifacts.get(run_id, [])),
        ),
        "expected exactly one successful exact-main artifact",
    )

    wrong_head_runs = {name: [dict(row) for row in rows] for name, rows in run_map.items()}
    wrong_head_runs[first.workflow_file][0]["head_sha"] = "d" * 40
    expect_error(
        lambda: select_exact_artifacts(
            plan=plan,
            installation_sha=INSTALLATION,
            list_runs=lambda workflow: list(wrong_head_runs.get(workflow, [])),
            list_artifacts=lambda run_id: list(artifact_map.get(run_id, [])),
        ),
        "found 0",
    )


def validate_aggregation():
    plan = producer_plan()
    authority_doc = canonical_authority_set()
    selections = tuple(
        SelectedArtifact(
            workflow_file=spec.workflow_file,
            artifact_name=spec.artifact_name,
            artifact_id=3000 + index,
            workflow_run_id=4000 + index,
            scenarios=spec.scenarios,
        )
        for index, spec in enumerate(plan)
    )
    archives = {
        selected.artifact_id: archive_for(spec, authority_doc, selected.workflow_run_id)
        for selected, spec in zip(selections, plan)
    }

    def fake_evaluator(*, authority_set, evidence):
        require(authority_set.trusted_main_head_sha == INSTALLATION, "fake evaluator received wrong authority")
        require(len(evidence) == 11, "fake evaluator must receive 11 exact records")
        return {
            "schema_version": "ai-sdlc.v03-effect-safety-live-authority-set-ledger/v1",
            "issue": 221,
            "status": "PASS",
            "overall_issue_221_pass": True,
            "required_scenarios": list(REQUIRED_SCENARIOS),
            "satisfied_scenarios": list(REQUIRED_SCENARIOS),
            "unresolved_scenarios": [],
            "accepted_record_count": 11,
            "accepted_workflow_run_count": 11,
            "observed_zero_measurements": [
                "duplicate_external_effect_count",
                "duplicate_feature_write_count",
                "speculative_retry_under_unknown_count",
                "stale_evidence_accepted_count",
                "unauthorized_lifecycle_transition_count",
            ],
            "deterministic_evidence_accepted": False,
        }

    selection, ledger = aggregate_selected_artifacts(
        plan=plan,
        selections=selections,
        download_artifact=lambda artifact_id: archives[artifact_id],
        installation_sha=INSTALLATION,
        evaluator=fake_evaluator,
    )
    require(selection["record_count"] == 11 and selection["scenario_count"] == 13, "final selection counts drifted")
    require(selection["release_eligible"] is True, "exact PASS selection should be release eligible")
    require(ledger["overall_issue_221_pass"] is True, "fake exact ledger did not remain PASS")

    mismatched_provenance = dict(archives)
    first_selected = selections[0]
    mismatched_provenance[first_selected.artifact_id] = archive_for(
        plan[0],
        authority_doc,
        first_selected.workflow_run_id + 999,
    )
    expect_error(
        lambda: aggregate_selected_artifacts(
            plan=plan,
            selections=selections,
            download_artifact=lambda artifact_id: mismatched_provenance[artifact_id],
            installation_sha=INSTALLATION,
            evaluator=fake_evaluator,
        ),
        "artifact/provenance workflow run binding differs",
    )

    bad_authority = dict(authority_doc)
    bad_authority["trusted_main_head_sha"] = "e" * 40
    last_singleton = next(
        (index for index, spec in reversed(list(enumerate(plan))) if spec.authority_set_name),
        None,
    )
    require(last_singleton is not None, "test plan lacks singleton authority")
    broken = dict(archives)
    broken[selections[last_singleton].artifact_id] = archive_for(
        plan[last_singleton],
        bad_authority,
        selections[last_singleton].workflow_run_id,
    )
    expect_error(
        lambda: aggregate_selected_artifacts(
            plan=plan,
            selections=selections,
            download_artifact=lambda artifact_id: broken[artifact_id],
            installation_sha=INSTALLATION,
            evaluator=fake_evaluator,
        ),
        "authority-set documents differ",
    )


def validate_workflows():
    live = LIVE_WORKFLOW.read_text(encoding="utf-8")
    validation = VALIDATION_WORKFLOW.read_text(encoding="utf-8")

    require("workflow_dispatch:" in live, "final live workflow lacks explicit dispatch")
    require("pull_request:" not in live, "final live workflow must not be PR-triggered")
    require("github.ref != 'refs/heads/main'" in live, "final live workflow lacks non-main rejection")
    require("actions: read" in live and "contents: read" in live, "final live workflow lacks read authority")
    require("issues: write" in live, "final live workflow cannot publish final receipt")
    require("contents: write" not in live, "final live workflow must remain repository read-only")
    require("create-github-app-token" not in live, "final ledger must not mint a write token")
    require("operation.start" not in live, "final ledger must not start an Operation")
    require("v03_effect_safety_final_live_ledger.py" in live, "final live workflow does not invoke exact aggregator")
    require("v03-effect-safety-final-ledger.json" in live, "final live workflow does not retain exact ledger")

    require("pull_request:" in validation, "validator workflow must run on PRs")
    require("workflow_dispatch:" not in validation, "validator workflow must not expose a live dispatch surface")
    require("contents: write" not in validation, "validator workflow must be read-only")
    require("validate_v03_effect_safety_final_live_ledger.py" in validation, "validator workflow misses deterministic validator")


def main():
    validate_plan()
    validate_selection()
    validate_aggregation()
    validate_workflows()
    print("v0.3 final Issue #221 live-ledger aggregation validation passed")
    print("- exact producer plan: 11 distinct successful workflow runs / 13 closed scenarios")
    print("- exact-main artifact selection fails closed on missing, stale or ambiguous successful evidence")
    print("- nine #310 authority-set documents must agree before the closed ledger can PASS")
    print("- final workflow is workflow_dispatch/main-only, read-only, and cannot dispatch Worker/Store/Feature writes")


if __name__ == "__main__":
    main()
