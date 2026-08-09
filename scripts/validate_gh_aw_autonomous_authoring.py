#!/usr/bin/env python3
"""Deterministic regressions for bounded autonomous authoring roles."""

from copy import deepcopy
from pathlib import Path

import yaml

from gh_aw_authoring_result import AuthoringResultError, canonical_artifact, translate
from gh_aw_role_workers import AUTHORING_ROLE_STAGES, load_role_workers, resolve_role_worker
from runtime_router import select_runtime

ROOT=Path(__file__).resolve().parents[1]
POLICY=yaml.safe_load((ROOT/"dispatch"/"gh-aw-developer.yaml").read_text(encoding="utf-8"))
REPO="DREAM-XIN/example"
REF="feature/F-EXAMPLE-0001"


def require(value,message):
    if not value: raise AssertionError(message)


def manifest(stage,status="WORKING",artifacts=None,tasks=None):
    stages=[
        {"id":"requirement","status":status if stage=="requirement" else "DONE"},
        {"id":"requirement-review","status":"TODO" if stage=="requirement" else "DONE","gate":"requirement-gate"},
        {"id":"design","status":status if stage=="design" else ("TODO" if stage=="requirement" else "DONE")},
        {"id":"design-review","status":"TODO" if stage in {"requirement","design"} else "DONE","gate":"design-gate"},
        {"id":"plan","status":status if stage=="plan" else "TODO"},
        {"id":"implementation","status":"TODO"},
    ]
    return {"revision":5,"feature":{"id":"F-EXAMPLE-0001","issue":"#9"},"workflow":{"stages":stages},"artifacts":artifacts or [],"tasks":tasks or [],"evidence":[]}


def result(role,stage,status="COMPLETED",work_kind="stage"):
    out={"contract":"ai-sdlc-gh-aw-authoring-result-v0.1","feature_id":"F-EXAMPLE-0001","task_id":f"F-EXAMPLE-0001-{stage}","work_kind":work_kind,"expected_revision":5,"target_repository":REPO,"target_ref":REF,"stage":stage,"role":role,"status":status,"artifact_body":"# Bounded artifact\n","summary":"done"}
    if status=="BLOCKED": out["reason"]="missing trusted context"
    return out


def validate_routes():
    cases=[("product","requirement","gh-aw","autonomous"),("architect","design","gh-aw","autonomous"),("orchestrator","plan","gh-aw","autonomous"),("product","acceptance","chatgpt-web","manual"),("reviewer","requirement-review","chatgpt-web","manual"),("reviewer","design-review","chatgpt-web","manual")]
    for role,stage,runtime,mode in cases:
        routed=select_runtime({"role":role,"stage":stage},"high",POLICY)
        require(routed["runtime"]=={"id":runtime,"mode":mode},f"unexpected runtime for {role}/{stage}: {routed}")


def validate_registry():
    workers=[w for w in load_role_workers() if (w.role,w.stage) in AUTHORING_ROLE_STAGES]
    require(len(workers)==6,"expected six authoring role workers")
    require(resolve_role_worker("product","requirement","claude").worker_workflow.endswith("product-claude.lock.yml"),"Product worker mismatch")
    require(resolve_role_worker("architect","design","claude").worker_workflow.endswith("architect-claude.lock.yml"),"Architect worker mismatch")
    require(resolve_role_worker("orchestrator","plan","codex").worker_workflow.endswith("orchestrator-codex.lock.yml"),"Orchestrator worker mismatch")


def validate_paths():
    expected={
        ("product","requirement"):("requirement","docs/features/F-EXAMPLE-0001/requirement.md","requirement-review"),
        ("architect","design"):("design","docs/features/F-EXAMPLE-0001/design.md","design-review"),
        ("orchestrator","plan"):("plan","docs/features/F-EXAMPLE-0001/plan.md","implementation"),
    }
    for key,value in expected.items(): require(canonical_artifact("F-EXAMPLE-0001",*key)==value,f"canonical path mismatch: {key}")
    for key in [("product","acceptance"),("reviewer","design-review")]:
        try: canonical_artifact("F-EXAMPLE-0001",*key); raise AssertionError(f"unsupported path resolved: {key}")
        except AuthoringResultError: pass


def validate_translation():
    scenarios=[("product","requirement","requirement","requirement-review"),("architect","design","design","design-review"),("orchestrator","plan","plan","implementation")]
    for role,stage,artifact_type,next_stage in scenarios:
        event=translate(manifest(stage),result(role,stage),comment_url="https://github.com/DREAM-XIN/example/issues/9#issuecomment-1",occurred_at="2026-08-09T15:00:00Z")["event"]
        require(any(c["kind"]=="artifact-record" and c["record"]["type"]==artifact_type and c["record"]["status"]=="draft" for c in event["changes"]),f"missing {artifact_type} draft")
        require(any(c["kind"]=="stage" and c["id"]==stage and c["status"]=="DONE" for c in event["changes"]),f"{stage} not completed")
        require(any(c["kind"]=="stage" and c["id"]==next_stage and c["status"]=="READY" for c in event["changes"]),f"{next_stage} not readied")
        require(not any(c["kind"]=="gate" for c in event["changes"]),"authoring result unexpectedly mutated Gate")

    prior=[{"id":"design-v1","type":"design","uri":"docs/features/F-EXAMPLE-0001/design.md","status":"draft"}]
    event=translate(manifest("design",artifacts=prior),result("architect","design"),comment_url="https://github.com/x/y/issues/9#issuecomment-1",occurred_at="2026-08-09T15:00:00Z")["event"]
    require(any(c=={"kind":"artifact","id":"design-v1","status":"superseded"} for c in event["changes"]),"old current draft not superseded")
    require(any(c["kind"]=="artifact-record" and c["record"]["id"]=="design-v2" for c in event["changes"]),"replacement version not deterministic")

    ambiguous=prior+[{"id":"design-v2","type":"design","uri":"docs/features/F-EXAMPLE-0001/design.md","status":"draft"}]
    try: translate(manifest("design",artifacts=ambiguous),result("architect","design"),comment_url="x",occurred_at="2026-08-09T15:00:00Z"); raise AssertionError("multiple drafts unexpectedly accepted")
    except AuthoringResultError: pass

    blocked=translate(manifest("plan"),result("orchestrator","plan","BLOCKED"),comment_url="x",occurred_at="2026-08-09T15:00:00Z")["event"]
    require(any(c["kind"]=="stage" and c["id"]=="plan" and c["status"]=="BLOCKED" for c in blocked["changes"]),"BLOCKED authoring did not fail closed")
    require(not any(c["kind"]=="artifact-record" for c in blocked["changes"]),"BLOCKED authoring created artifact")


def validate_closed_schema():
    invalid=result("product","requirement"); invalid["path"]="state/features/F-EXAMPLE-0001.yaml"
    try: translate(manifest("requirement"),invalid,comment_url="x",occurred_at="2026-08-09T15:00:00Z"); raise AssertionError("model-supplied path unexpectedly accepted")
    except AuthoringResultError: pass
    stale=result("product","requirement"); stale["expected_revision"]=4
    try: translate(manifest("requirement"),stale,comment_url="x",occurred_at="2026-08-09T15:00:00Z"); raise AssertionError("stale revision accepted")
    except AuthoringResultError: pass


def main():
    validate_routes(); validate_registry(); validate_paths(); validate_translation(); validate_closed_schema(); print("gh-aw autonomous authoring validation passed")


if __name__=="__main__": main()
