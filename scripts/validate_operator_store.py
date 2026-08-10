#!/usr/bin/env python3
"""Deterministic verification for the v0.3 durable Operator Store substrate."""
from __future__ import annotations
import json, subprocess, tempfile
from pathlib import Path
from jsonschema import Draft202012Validator

from operator_api import API_VERSION, dispatch
from operator_store import (
    StoreCommandError, plan_authorize_launch, plan_callback, plan_cancel,
    plan_dispatch_claim, plan_launch_lookup, plan_operation_start,
    plan_persist_confirmed, plan_persist_linearized, plan_persist_requested,
    plan_semantic_reservation, plan_takeover, query_unfinished,
)
from operator_store_backends import OperatorStoreRuntime, store_backends
from operator_store_git import CasConflict, GitStateRefBackend, MemoryStateRefBackend
from operator_store_model import (
    StoreInvariantError, StoreMutation, StoreMutationPlan, StoreSnapshot,
    apply_plan_to_snapshot, projection_path, rebuild_projection, reservation_path,
)
from operator_store_protection import PROTECTED, UNKNOWN, UNPROTECTED, ProtectionError, StaticProtectionVerifier

ROOT=Path(__file__).resolve().parents[1]
STATE_REF="refs/heads/ai-sdlc-operator-state"
REPO="DREAM-XIN/ai-sdlc"
NOW="2026-08-10T04:00:00Z"
TRUST="trusted-test-context"

def require(condition,message):
    if not condition: raise AssertionError(message)

def expect_code(code,fn):
    try: fn()
    except StoreCommandError as exc:
        require(exc.code==code,f"expected {code}, got {exc.code}: {exc}"); return
    raise AssertionError(f"expected StoreCommandError {code}")

def receipt(status=PROTECTED): return StaticProtectionVerifier(status=status).verify(REPO,STATE_REF)
def commit(backend,plan): return backend.commit(plan,receipt()).snapshot

def start(snapshot,backend,feature,rev=3,key=None):
    plan=plan_operation_start(snapshot,target_repository=REPO,feature_id=feature,expected_revision=rev,idempotency_key=key or f"start-{feature}",occurred_at=NOW,trusted_context_digest=TRUST)
    result=backend.commit(plan,receipt()); return result.snapshot,result.result["operation_id"]

def reserve(snapshot,backend,op,feature,task="task-1",rev=3):
    plan=plan_semantic_reservation(snapshot,operation_id=op,generation=rebuild_projection(snapshot,op)["generation"],target_repository=REPO,feature_id=feature,expected_revision=rev,current_stage="implementation",task_identity=task,role="developer",candidate_head_sha=None,occurred_at=NOW,trusted_context_digest=TRUST)
    result=backend.commit(plan,receipt()); return result.snapshot,result.result

def claim(snapshot,backend,op,effect):
    g=rebuild_projection(snapshot,op)["generation"]; result=backend.commit(plan_dispatch_claim(snapshot,operation_id=op,generation=g,effect_key=effect,occurred_at=NOW,trusted_context_digest=TRUST),receipt()); return result.snapshot,result.result

def authorize(snapshot,backend,op,claim_id,dispatch_id="dispatch-1"):
    g=rebuild_projection(snapshot,op)["generation"]; result=backend.commit(plan_authorize_launch(snapshot,operation_id=op,generation=g,claim_id=claim_id,dispatch_id=dispatch_id,occurred_at=NOW,trusted_context_digest=TRUST,verified_expected_revision=3,verified_stage="implementation",verified_candidate_head_sha=None),receipt()); return result.snapshot

def validate_schemas(snapshot):
    root=ROOT/"spec/operator/store"
    names=("operation-event","operation-projection","semantic-reservation","dispatch-claim","feature-claim","protection-receipt")
    for name in names:
        schema=json.loads((root/f"{name}.schema.json").read_text(encoding="utf-8")); Draft202012Validator.check_schema(schema)
    for path,value in snapshot.files.items():
        if "/events/" in path: schema="operation-event"
        elif "/projections/" in path: schema="operation-projection"
        elif "/reservations/external/" in path: schema="semantic-reservation"
        elif "/claims/dispatch/" in path: schema="dispatch-claim"
        elif "/claims/feature/" in path: schema="feature-claim"
        else: continue
        Draft202012Validator(json.loads((root/f"{schema}.schema.json").read_text())).validate(value)
    Draft202012Validator(json.loads((root/"protection-receipt.schema.json").read_text())).validate(receipt().__dict__)

def validate_core_model_and_commands():
    backend=MemoryStateRefBackend(repository=REPO,state_ref=STATE_REF); s=backend.read_snapshot()
    s,op=start(s,backend,"F-STORE-TEST-1")
    duplicate=plan_operation_start(s,target_repository=REPO,feature_id="F-STORE-TEST-1",expected_revision=3,idempotency_key="another-equivalent-start",occurred_at=NOW,trusted_context_digest=TRUST)
    require(not duplicate.mutations and duplicate.result["operation_id"]==op,"equivalent operation.start did not converge")
    expect_code("ALREADY_CLAIMED",lambda:plan_operation_start(s,target_repository=REPO,feature_id="F-STORE-TEST-1",expected_revision=4,idempotency_key="stale-start",occurred_at=NOW,trusted_context_digest=TRUST))
    s,res=reserve(s,backend,op,"F-STORE-TEST-1"); effect=res["semantic_effect_key"]; external=res["external_dispatch_key"]
    again=plan_semantic_reservation(s,operation_id=op,generation=0,target_repository=REPO,feature_id="F-STORE-TEST-1",expected_revision=3,current_stage="implementation",task_identity="task-1",role="developer",candidate_head_sha=None,occurred_at="later",trusted_context_digest="another-trusted-receipt")
    require(not again.mutations and again.result["external_dispatch_key"]==external,"semantic reservation did not converge")
    s,cl=claim(s,backend,op,effect); cid=cl["claim_id"]
    duplicate_claim=plan_dispatch_claim(s,operation_id=op,generation=0,effect_key=effect,occurred_at="later",trusted_context_digest=TRUST)
    require(not duplicate_claim.mutations and duplicate_claim.result["external_dispatch_key"]==external,"duplicate dispatch claim did not converge")
    original=rebuild_projection(s,op); without_cache=StoreSnapshot(ref_sha=s.ref_sha,files={k:v for k,v in s.files.items() if k!=projection_path(op)}); rebuilt=rebuild_projection(without_cache,op)
    require(original==rebuilt,"projection cache influenced deterministic rebuild")
    bad=dict(s.files[reservation_path(effect)]); bad["external_dispatch_key"]="dispatch-"+"0"*40
    try: apply_plan_to_snapshot(s,StoreMutationPlan(s.ref_sha,(StoreMutation("create_immutable",reservation_path(effect),bad),),{})); raise AssertionError("immutable reservation overwrite accepted")
    except StoreInvariantError: pass
    validate_schemas(s)

    # Cancellation before launch authorization wins.
    s2,op2=start(s,backend,"F-STORE-TEST-2"); s2,res2=reserve(s2,backend,op2,"F-STORE-TEST-2"); s2,cl2=claim(s2,backend,op2,res2["semantic_effect_key"]); s2=commit(backend,plan_cancel(s2,operation_id=op2,reason="stop",occurred_at=NOW,trusted_context_digest=TRUST))
    expect_code("CANCELLED_OPERATION",lambda:plan_authorize_launch(s2,operation_id=op2,generation=0,claim_id=cl2["claim_id"],dispatch_id="late",occurred_at=NOW,trusted_context_digest=TRUST,verified_expected_revision=3,verified_stage="implementation",verified_candidate_head_sha=None))

    # UNKNOWN blocks, survives takeover, and resolves only through same external key.
    s3,op3=start(s2,backend,"F-STORE-TEST-3"); s3,res3=reserve(s3,backend,op3,"F-STORE-TEST-3"); s3,cl3=claim(s3,backend,op3,res3["semantic_effect_key"]); s3=authorize(s3,backend,op3,cl3["claim_id"],"unknown-dispatch")
    s3=commit(backend,plan_launch_lookup(s3,operation_id=op3,generation=0,external_dispatch_key_value=res3["external_dispatch_key"],lookup_state="UNKNOWN",receipt_id=None,occurred_at=NOW,trusted_context_digest=TRUST))
    require(rebuild_projection(s3,op3)["status"]=="BLOCKED","UNKNOWN did not block Operation")
    expect_code("BLOCKED",lambda:plan_semantic_reservation(s3,operation_id=op3,generation=0,target_repository=REPO,feature_id="F-STORE-TEST-3",expected_revision=3,current_stage="implementation",task_identity="new-task",role="developer",candidate_head_sha=None,occurred_at=NOW,trusted_context_digest=TRUST))
    expect_code("INVALID_REQUEST",lambda:plan_launch_lookup(s3,operation_id=op3,generation=0,external_dispatch_key_value="dispatch-"+"9"*40,lookup_state="UNKNOWN",receipt_id=None,occurred_at=NOW,trusted_context_digest=TRUST))
    s3=commit(backend,plan_takeover(s3,operation_id=op3,occurred_at=NOW,trusted_context_digest=TRUST)); p3=rebuild_projection(s3,op3)
    require(p3["generation"]==1 and p3["status"]=="BLOCKED" and res3["external_dispatch_key"] in p3["unresolved_unknown"],"UNKNOWN reservation not inherited across takeover")
    s3=commit(backend,plan_launch_lookup(s3,operation_id=op3,generation=1,external_dispatch_key_value=res3["external_dispatch_key"],lookup_state="LAUNCHED",receipt_id="receipt-1",occurred_at=NOW,trusted_context_digest=TRUST))
    require(rebuild_projection(s3,op3)["status"]=="WAITING_EXTERNAL","LAUNCHED receipt did not resolve UNKNOWN")
    expect_code("INVALID_REQUEST",lambda:plan_callback(s3,operation_id=op3,generation=1,callback_id="cb-bad",callback_payload={},external_dispatch_key_value="dispatch-"+"8"*40,occurred_at=NOW,trusted_context_digest=TRUST))
    s3=commit(backend,plan_callback(s3,operation_id=op3,generation=1,callback_id="cb-1",callback_payload={"ok":True},external_dispatch_key_value=res3["external_dispatch_key"],occurred_at=NOW,trusted_context_digest=TRUST))

    # Persist linearization ordering and lost-ack correlation.
    s4,op4=start(s3,backend,"F-STORE-TEST-4"); common=dict(operation_id=op4,generation=0,feature_event_id="EVT-F4",expected_revision=3,target_ref="feature/F4",candidate_head_sha=None,occurred_at=NOW,trusted_context_digest=TRUST)
    expect_code("INVALID_REQUEST",lambda:plan_persist_linearized(s4,**common))
    s4=commit(backend,plan_persist_requested(s4,**common)); s4=commit(backend,plan_persist_linearized(s4,**common)); s4=commit(backend,plan_cancel(s4,operation_id=op4,reason="after-linearize",occurred_at=NOW,trusted_context_digest=TRUST)); s4=commit(backend,plan_persist_confirmed(s4,**common))
    p4=rebuild_projection(s4,op4); require(p4["status"]=="CANCELLED" and "EVT-F4" in p4["confirmed_persists"],"pre-cancel Persist linearization could not correlate confirmation")
    expect_code("CANCELLED_OPERATION",lambda:plan_persist_requested(s4,**{**common,"feature_event_id":"EVT-LATE"}))

    unfinished=query_unfinished(s4,target_repository=REPO)
    require(all(row["status"] not in {"DONE","CANCELLED"} for row in unfinished),"unfinished query exposed terminal Operations")
    return backend,s4

def validate_protection_and_cas():
    backend=MemoryStateRefBackend(repository=REPO,state_ref=STATE_REF); initial=backend.read_snapshot(); plan=plan_operation_start(initial,target_repository=REPO,feature_id="F-PROTECTION",expected_revision=1,idempotency_key="p",occurred_at=NOW,trusted_context_digest=TRUST)
    for state in (UNPROTECTED,UNKNOWN):
        try: backend.commit(plan,receipt(state)); raise AssertionError(f"{state} protection unexpectedly allowed write")
        except ProtectionError: pass
        require(backend.read_snapshot().ref_sha is None,"failed protection check mutated state")
    forged=receipt(); forged=type(forged)(repository="OTHER/repo",state_ref=STATE_REF,status=PROTECTED,verifier_identity=forged.verifier_identity,verified_at=forged.verified_at,policy_digest=forged.policy_digest)
    try: backend.commit(plan,forged); raise AssertionError("mismatched protection receipt accepted")
    except ProtectionError: pass
    calls={"n":0}; backend.inject_conflict_once()
    def planner(snapshot):
        calls["n"]+=1; return plan_operation_start(snapshot,target_repository=REPO,feature_id="F-PROTECTION",expected_revision=1,idempotency_key="p",occurred_at=NOW,trusted_context_digest=TRUST)
    result=backend.commit_replanned(planner,receipt()); require(calls["n"]>=2 and result.result["status"]=="RUNNING","CAS conflict did not re-read and semantically re-plan")

def validate_local_git_cas():
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-store-git-") as td:
        subprocess.run(["git","init","-q",td],check=True); subprocess.run(["git","-C",td,"config","user.name","ai-sdlc-test"],check=True); subprocess.run(["git","-C",td,"config","user.email","ai-sdlc@example.invalid"],check=True)
        backend=GitStateRefBackend(repo_path=td,repository=REPO,state_ref=STATE_REF); empty=backend.read_snapshot(); plan=plan_operation_start(empty,target_repository=REPO,feature_id="F-GIT",expected_revision=2,idempotency_key="git-start",occurred_at=NOW,trusted_context_digest=TRUST); result=backend.commit(plan,receipt())
        require(result.ref_sha==backend.read_snapshot().ref_sha and rebuild_projection(result.snapshot,result.result["operation_id"])["status"]=="RUNNING","local Git CAS did not materialize Store state")
        try: backend.commit(plan,receipt()); raise AssertionError("stale local Git CAS unexpectedly succeeded")
        except CasConflict: pass

def canonical_request(capability,request_id,payload,*,target=None,context=None,idempotency=None):
    body={"api_version":API_VERSION,"request_id":request_id,"capability":capability,"client_identity":{"adapter_id":"operator-store-test"},"payload":payload}
    if target is not None: body["target"]=target
    if context is not None: body["context"]=context
    if idempotency is not None: body["idempotency_key"]=idempotency
    return body

def validate_canonical_backing():
    backend=MemoryStateRefBackend(repository=REPO,state_ref=STATE_REF); runtime=OperatorStoreRuntime(backend=backend,protection_verifier=StaticProtectionVerifier(status=PROTECTED),clock=lambda:NOW); backends=store_backends(runtime)
    trusted={"trusted_context_digest":TRUST,"feature_verification":{"repository":REPO,"feature_id":"F-API","revision":7}}
    start_req=canonical_request("operation.start","req-start",{},target={"repository":REPO,"feature_id":"F-API"},context={"expected_feature_revision":7},idempotency="api-start")
    start_res=dispatch(start_req,trusted_context=trusted,backends=backends); require(start_res["ok"] and start_res["result"]["status"]=="RUNNING",f"canonical start failed: {start_res}"); op=start_res["result"]["operation_id"]
    status_res=dispatch(canonical_request("operation.status","req-status",{},context={"operation_id":op}),trusted_context={},backends=backends); require(status_res["ok"] and status_res["result"]["operation_id"]==op,"canonical operation.status failed")
    cancel_res=dispatch(canonical_request("operation.cancel","req-cancel",{"reason":"done"},context={"operation_id":op},idempotency="api-cancel"),trusted_context={},backends=backends); require(cancel_res["ok"] and cancel_res["result"]["status"]=="CANCELLED","canonical operation.cancel failed")
    inbox=dispatch(canonical_request("operator.inbox","req-inbox",{}),trusted_context={},backends=backends); require(not inbox["ok"] and inbox["error"]["code"]=="CAPABILITY_UNAVAILABLE","operator.inbox was falsely advertised/backed")
    resume=dispatch(canonical_request("operation.resume","req-resume",{},target={"repository":REPO,"feature_id":"F-API"},context={"operation_id":op,"expected_feature_revision":7},idempotency="resume"),trusted_context={},backends=backends); require(not resume["ok"] and resume["error"]["code"]=="CAPABILITY_UNAVAILABLE","operation.resume was falsely backed")
    capabilities=dispatch(canonical_request("system.capabilities","req-caps",{}),trusted_context={},backends=backends); rows={r["id"]:r for r in capabilities["result"]["capabilities"]}; require(all(rows[k]["available"] for k in ("operation.start","operation.status","operation.cancel")),"Store-backed capabilities not advertised available"); require(not rows["operator.inbox"]["available"] and not rows["operation.resume"]["available"],"deferred capabilities advertised available")
    stale=dispatch({**start_req,"request_id":"req-stale","idempotency_key":"stale","context":{"expected_feature_revision":8}},trusted_context=trusted,backends=backends); require(not stale["ok"] and stale["error"]["code"]=="STALE_REVISION","Store domain error was not preserved as structured canonical error")

def main():
    validate_core_model_and_commands(); validate_protection_and_cas(); validate_local_git_cas(); validate_canonical_backing(); print("Operator Store deterministic validation passed")
if __name__=="__main__": main()
