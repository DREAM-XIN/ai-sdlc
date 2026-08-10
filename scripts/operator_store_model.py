#!/usr/bin/env python3
"""Pure deterministic model/reducer helpers for the v0.3 Operator Store."""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib, json, re
from typing import Any
STORE_ROOT="state/operator/v1"; EVENT_SCHEMA_VERSION="ai-sdlc.operation-event/v1"
TERMINAL_STATUSES=frozenset({"DONE","CANCELLED"}); VALID_STATUSES=frozenset({"RUNNING","WAITING_EXTERNAL","BLOCKED","DONE","CANCELLED"})
_EVENT_RE=re.compile(r"^state/operator/v1/operations/([^/]+)/events/(\d+)-([^/]+)\.json$")
class StoreInvariantError(ValueError): pass
def canonical_json(value:Any)->str:return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)
def digest_json(value:Any)->str:return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def normalize_repository(value:str)->str:
    text=value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",text): raise StoreInvariantError("invalid target repository")
    return text.lower()
def semantic_effect_material(*,target_repository:str,feature_id:str,expected_revision:int,current_stage:str,task_identity:str,role:str,candidate_head_sha:str|None=None)->dict[str,Any]:
    if expected_revision<0: raise StoreInvariantError("expected revision must be non-negative")
    values={"target_repository":normalize_repository(target_repository),"feature_id":feature_id,"expected_revision":expected_revision,"current_stage":current_stage,"task_identity":task_identity,"role":role,"candidate_head_sha":candidate_head_sha}
    if any(not str(values[k]).strip() for k in ("feature_id","current_stage","task_identity","role")): raise StoreInvariantError("semantic effect identity fields must be non-empty")
    return values
def semantic_effect_key(**kwargs:Any)->str:return digest_json(semantic_effect_material(**kwargs))
def external_dispatch_key(effect_key:str)->str:return "dispatch-"+hashlib.sha256(("ai-sdlc-external:"+effect_key).encode()).hexdigest()[:40]
def operation_id_for(target_repository:str,feature_id:str,idempotency_key:str)->str:return "op-"+digest_json({"repository":normalize_repository(target_repository),"feature_id":feature_id,"idempotency_key":idempotency_key})[:40]
def feature_claim_id(operation_id:str,generation:int)->str:return "fc-"+digest_json({"operation_id":operation_id,"generation":generation})[:40]
def dispatch_claim_id(operation_id:str,generation:int,effect_key:str)->str:return "dc-"+digest_json({"operation_id":operation_id,"generation":generation,"effect_key":effect_key})[:40]
def event_path(operation_id:str,sequence:int,event_id:str)->str:return f"{STORE_ROOT}/operations/{operation_id}/events/{sequence:08d}-{event_id}.json"
def projection_path(operation_id:str)->str:return f"{STORE_ROOT}/projections/{operation_id}.json"
def reservation_path(effect_key:str)->str:return f"{STORE_ROOT}/reservations/external/{effect_key}.json"
def dispatch_claim_path(claim_id:str)->str:return f"{STORE_ROOT}/claims/dispatch/{claim_id}.json"
def feature_claim_path(target_repository:str,feature_id:str,claim_id:str)->str:
    repo_hash=hashlib.sha256(normalize_repository(target_repository).encode()).hexdigest()[:24]; return f"{STORE_ROOT}/claims/feature/{repo_hash}/{feature_id}/{claim_id}.json"
def is_projection_path(path:str)->bool:return path.startswith(f"{STORE_ROOT}/projections/") and path.endswith(".json")
def is_immutable_path(path:str)->bool:return path.startswith((f"{STORE_ROOT}/operations/",f"{STORE_ROOT}/reservations/external/",f"{STORE_ROOT}/claims/dispatch/",f"{STORE_ROOT}/claims/feature/")) and path.endswith(".json") and not is_projection_path(path)
def validate_store_path(path:str)->None:
    if not path.startswith(STORE_ROOT+"/") or ".." in path.split("/"): raise StoreInvariantError("invalid Store path")
@dataclass(frozen=True)
class StoreSnapshot:
    ref_sha:str|None=None; files:dict[str,Any]=field(default_factory=dict)
    def get(self,path:str)->Any|None:return self.files.get(path)
@dataclass(frozen=True)
class StoreMutation: kind:str; path:str; value:Any
@dataclass(frozen=True)
class StoreMutationPlan: expected_ref_sha:str|None; mutations:tuple[StoreMutation,...]; result:dict[str,Any]
def apply_plan_to_snapshot(snapshot:StoreSnapshot,plan:StoreMutationPlan,*,new_ref_sha:str|None=None)->StoreSnapshot:
    if plan.expected_ref_sha!=snapshot.ref_sha: raise StoreInvariantError("plan expected ref does not match snapshot")
    files=dict(snapshot.files)
    for m in plan.mutations:
        validate_store_path(m.path)
        if m.kind=="create_immutable":
            if not is_immutable_path(m.path): raise StoreInvariantError("create_immutable used for non-immutable path")
            if m.path in files:
                if canonical_json(files[m.path])!=canonical_json(m.value): raise StoreInvariantError("immutable store artifact conflict")
                continue
            files[m.path]=m.value
        elif m.kind=="replace_projection":
            if not is_projection_path(m.path): raise StoreInvariantError("only projection cache may be replaced")
            files[m.path]=m.value
        else: raise StoreInvariantError(f"unsupported store mutation kind: {m.kind}")
    return StoreSnapshot(ref_sha=new_ref_sha if new_ref_sha is not None else snapshot.ref_sha,files=files)
def operation_events(snapshot:StoreSnapshot,operation_id:str)->list[dict[str,Any]]:
    rows=[]
    for path,value in snapshot.files.items():
        match=_EVENT_RE.match(path)
        if match and match.group(1)==operation_id: rows.append((int(match.group(2)),match.group(3),value))
    rows.sort(key=lambda r:r[0])
    for expected,(seq,event_id,event) in enumerate(rows,start=1):
        if seq!=expected or not isinstance(event,dict) or event.get("schema_version")!=EVENT_SCHEMA_VERSION or event.get("operation_id")!=operation_id or event.get("sequence")!=seq or event.get("event_id")!=event_id: raise StoreInvariantError("operation event binding/schema mismatch")
    return [r[2] for r in rows]
def operation_ids(snapshot:StoreSnapshot)->tuple[str,...]:return tuple(sorted({m.group(1) for p in snapshot.files if (m:=_EVENT_RE.match(p))}))
def next_sequence(snapshot:StoreSnapshot,operation_id:str)->int:return len(operation_events(snapshot,operation_id))+1
def make_event(*,operation_id:str,generation:int,sequence:int,event_id:str,event_type:str,occurred_at:str,payload:dict[str,Any]|None=None,trusted_context_digest:str="trusted")->dict[str,Any]:
    if generation<0 or sequence<1 or not event_id or not event_type: raise StoreInvariantError("invalid operation event")
    return {"schema_version":EVENT_SCHEMA_VERSION,"operation_id":operation_id,"operation_generation":generation,"sequence":sequence,"event_id":event_id,"event_type":event_type,"occurred_at":occurred_at,"trusted_context_digest":trusted_context_digest,"payload":dict(payload or {})}
def rebuild_projection(snapshot:StoreSnapshot,operation_id:str)->dict[str,Any]:
    events=operation_events(snapshot,operation_id)
    if not events: raise StoreInvariantError("operation not found")
    status=None; generation=0; target_repository=None; feature_id=None; expected_revision=None
    authorized_dispatches=set(); unresolved_unknown=set(); requested_persists=set(); linearized_persists=set(); confirmed_persists=set(); superseded=set(); callback_ids={}
    for event in events:
        g=int(event["operation_generation"]); t=event["event_type"]; p=event.get("payload") or {}
        if t=="operation.started":
            if status is not None: raise StoreInvariantError("duplicate operation.started")
            generation=g; status="RUNNING"; target_repository=p.get("target_repository"); feature_id=p.get("feature_id"); expected_revision=p.get("expected_revision")
        elif t=="operation.superseded": superseded.add(g)
        elif t=="operation.generation.started":
            if g<=generation: raise StoreInvariantError("generation must increase")
            generation=g; status="BLOCKED" if unresolved_unknown else "RUNNING"
        else:
            if g<generation or g in superseded: raise StoreInvariantError("superseded generation attempted new operation fact")
            if g>generation: raise StoreInvariantError("event generation lacks generation-start fact")
            if status=="CANCELLED" and t not in {"dispatch.launch.lookup-recorded","worker.callback.recorded","persist.confirmed"}: raise StoreInvariantError("new decision fact after cancellation")
            if t=="dispatch.launch.authorized":
                if status in {"CANCELLED","BLOCKED"}: raise StoreInvariantError("launch authorization after cancellation/block")
                authorized_dispatches.add(str(p["external_dispatch_key"])); status="WAITING_EXTERNAL"
            elif t=="dispatch.launch.lookup-recorded":
                key=str(p["external_dispatch_key"]); state=p["lookup_state"]
                if key not in authorized_dispatches: raise StoreInvariantError("launch lookup lacks authorized dispatch binding")
                if state=="UNKNOWN": unresolved_unknown.add(key); status="BLOCKED"
                elif state=="LAUNCHED": unresolved_unknown.discard(key); status="WAITING_EXTERNAL" if status!="CANCELLED" else status
                elif state=="NOT_LAUNCHED": unresolved_unknown.discard(key); status="RUNNING" if status!="CANCELLED" else status
                else: raise StoreInvariantError("invalid launch lookup state")
            elif t=="worker.callback.recorded":
                key=str(p["external_dispatch_key"])
                if key not in authorized_dispatches: raise StoreInvariantError("callback lacks authorized dispatch binding")
                cid=str(p["callback_id"]); cd=str(p["callback_digest"])
                if cid in callback_ids and callback_ids[cid]!=cd: raise StoreInvariantError("conflicting callback history")
                callback_ids[cid]=cd
                if status not in TERMINAL_STATUSES and not unresolved_unknown: status="RUNNING"
            elif t=="operation.blocked": status="BLOCKED"
            elif t=="operation.cancelled": status="CANCELLED"
            elif t=="operation.done": status="DONE"
            elif t=="persist.requested":
                if status=="BLOCKED": raise StoreInvariantError("persist request while blocked")
                requested_persists.add(str(p["feature_event_id"]))
            elif t=="persist.linearized":
                eid=str(p["feature_event_id"])
                if status=="BLOCKED": raise StoreInvariantError("persist linearization while blocked")
                if eid not in requested_persists: raise StoreInvariantError("persist linearization lacks request")
                linearized_persists.add(eid)
            elif t=="persist.confirmed":
                eid=str(p["feature_event_id"])
                if eid not in linearized_persists: raise StoreInvariantError("persist confirmation lacks linearization")
                confirmed_persists.add(eid)
            elif t=="dispatch.claimed":
                if status=="BLOCKED": raise StoreInvariantError("dispatch claim while blocked")
            else: raise StoreInvariantError(f"unsupported operation event type: {t}")
    if status not in VALID_STATUSES: raise StoreInvariantError("operation projection has invalid status")
    return {"operation_id":operation_id,"generation":generation,"status":status,"target_repository":target_repository,"feature_id":feature_id,"expected_feature_revision":expected_revision,"last_sequence":len(events),"journal_digest":digest_json(events),"authorized_dispatches":sorted(authorized_dispatches),"unresolved_unknown":sorted(unresolved_unknown),"requested_persists":sorted(requested_persists),"linearized_persists":sorted(linearized_persists),"confirmed_persists":sorted(confirmed_persists)}
def projection_public(p:dict[str,Any])->dict[str,Any]:return {"operation_id":p["operation_id"],"generation":p["generation"],"status":p["status"]}
def unfinished_operations(snapshot:StoreSnapshot,*,target_repository:str|None=None,feature_id:str|None=None)->list[dict[str,Any]]:
    rows=[]; normalized=normalize_repository(target_repository) if target_repository else None
    for op in operation_ids(snapshot):
        p=rebuild_projection(snapshot,op)
        if p["status"] in TERMINAL_STATUSES: continue
        if normalized and normalize_repository(str(p["target_repository"]))!=normalized: continue
        if feature_id and p["feature_id"]!=feature_id: continue
        rows.append(p)
    rows.sort(key=lambda r:r["operation_id"]); return rows
def immutable_object(snapshot:StoreSnapshot,path:str)->dict[str,Any]|None:
    v=snapshot.get(path)
    if v is None:return None
    if not isinstance(v,dict):raise StoreInvariantError("immutable store artifact must be object")
    return v
def ensure_exact_or_absent(snapshot:StoreSnapshot,path:str,value:dict[str,Any])->bool:
    existing=immutable_object(snapshot,path)
    if existing is None:return False
    if canonical_json(existing)!=canonical_json(value):raise StoreInvariantError("immutable store artifact identity conflict")
    return True
