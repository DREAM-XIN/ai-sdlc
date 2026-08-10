#!/usr/bin/env python3
"""Pure semantic commands for the durable AI-SDLC Operator Store."""
from __future__ import annotations
from typing import Any
from operator_store_model import *

class StoreCommandError(RuntimeError):
    def __init__(self, code:str, message:str): super().__init__(message); self.code=code

def _event_id(t:str,m:dict[str,Any])->str:return t.replace('.', '-')+'-'+digest_json(m)[:32]
def _projection(snapshot:StoreSnapshot,operation_id:str,generation:int|None=None,*,allow_blocked:bool=False,allow_needs_user:bool=False,allow_cancelled:bool=False)->dict[str,Any]:
    p=rebuild_projection(snapshot,operation_id)
    if generation is not None and p['generation']!=generation: raise StoreCommandError('SUPERSEDED_GENERATION','operation generation is no longer current')
    if p['status']=='CANCELLED' and not allow_cancelled: raise StoreCommandError('CANCELLED_OPERATION','operation is cancelled')
    if p['status']=='BLOCKED' and not allow_blocked: raise StoreCommandError('BLOCKED','operation is blocked by unresolved safety state')
    if p['status']=='NEEDS_USER' and not allow_needs_user: raise StoreCommandError('NEEDS_USER','operation requires user input')
    return p

def _append_event(snapshot:StoreSnapshot,*,operation_id:str,generation:int,event_type:str,occurred_at:str,payload:dict[str,Any],trusted_context_digest:str,identity_material:dict[str,Any]|None=None):
    material=dict(identity_material or payload); material.update({'operation_id':operation_id,'generation':generation,'event_type':event_type}); eid=_event_id(event_type,material)
    for event in operation_events(snapshot,operation_id):
        if event['event_id']==eid:
            existing=dict(event); expected=make_event(operation_id=operation_id,generation=generation,sequence=event['sequence'],event_id=eid,event_type=event_type,occurred_at=event['occurred_at'],payload=payload,trusted_context_digest=trusted_context_digest)
            existing['occurred_at']=expected['occurred_at']='<ignored>'
            if canonical_json(existing)!=canonical_json(expected): raise StoreCommandError('ALREADY_APPLIED','event identity already exists with different semantics')
            return snapshot,StoreMutation('create_immutable',event_path(operation_id,event['sequence'],eid),event)
    seq=next_sequence(snapshot,operation_id); event=make_event(operation_id=operation_id,generation=generation,sequence=seq,event_id=eid,event_type=event_type,occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest); m=StoreMutation('create_immutable',event_path(operation_id,seq,eid),event)
    return apply_plan_to_snapshot(snapshot,StoreMutationPlan(snapshot.ref_sha,(m,),{})),m

def _finalize(snapshot,working,mutations,operation_id,result=None):
    p=rebuild_projection(working,operation_id); return StoreMutationPlan(snapshot.ref_sha,tuple(mutations+[StoreMutation('replace_projection',projection_path(operation_id),p)]),result or projection_public(p))
def _feature_claims(snapshot,repository,feature_id):
    out=[]
    for path,claim in snapshot.files.items():
        if '/claims/feature/' in path and isinstance(claim,dict) and str(claim.get('target_repository','')).lower()==repository.lower() and claim.get('feature_id')==feature_id: out.append(claim)
    return out
def _active_feature_operation(snapshot,repository,feature_id):
    rows={}
    for claim in _feature_claims(snapshot,repository,feature_id):
        op=claim.get('operation_id')
        if op not in operation_ids(snapshot): raise StoreInvariantError('feature claim references missing operation')
        p=rebuild_projection(snapshot,op)
        if p['status'] not in TERMINAL_STATUSES: rows[op]=p
    if len(rows)>1: raise StoreInvariantError('multiple nonterminal operation owners for feature')
    return next(iter(rows.values()),None)

def plan_operation_start(snapshot:StoreSnapshot,*,target_repository:str,feature_id:str,expected_revision:int,idempotency_key:str,occurred_at:str,trusted_context_digest:str,operation_profile:str|None=None)->StoreMutationPlan:
    if operation_profile is not None and not operation_profile.strip(): raise StoreCommandError('INVALID_REQUEST','operation profile must be non-empty when supplied')
    active=_active_feature_operation(snapshot,target_repository,feature_id)
    if active is not None:
        if active['expected_feature_revision']!=expected_revision: raise StoreCommandError('ALREADY_CLAIMED','feature already has an active operation at another revision')
        if active.get('operation_profile')!=operation_profile: raise StoreCommandError('ALREADY_CLAIMED','feature already has an active operation with another profile')
        return StoreMutationPlan(snapshot.ref_sha,tuple(),projection_public(active))
    op=operation_id_for(target_repository,feature_id,idempotency_key)
    if op in operation_ids(snapshot):
        p=rebuild_projection(snapshot,op)
        if p['expected_feature_revision']!=expected_revision or p.get('operation_profile')!=operation_profile: raise StoreCommandError('ALREADY_APPLIED','idempotency key is bound to incompatible operation semantics')
        return StoreMutationPlan(snapshot.ref_sha,tuple(),projection_public(p))
    g=0; cid=feature_claim_id(op,g); claim={'claim_id':cid,'target_repository':target_repository.lower(),'feature_id':feature_id,'operation_id':op,'operation_generation':g,'expected_revision':expected_revision,'idempotency_key':idempotency_key,'created_at':occurred_at,'trusted_context_digest':trusted_context_digest}
    muts=[StoreMutation('create_immutable',feature_claim_path(target_repository,feature_id,cid),claim)]; working=apply_plan_to_snapshot(snapshot,StoreMutationPlan(snapshot.ref_sha,tuple(muts),{})); working,e=_append_event(working,operation_id=op,generation=g,event_type='operation.started',occurred_at=occurred_at,payload={'target_repository':target_repository.lower(),'feature_id':feature_id,'expected_revision':expected_revision,'operation_profile':operation_profile},trusted_context_digest=trusted_context_digest,identity_material={'idempotency_key':idempotency_key,'operation_profile':operation_profile}); muts.append(e); return _finalize(snapshot,working,muts,op)

def plan_semantic_reservation(snapshot:StoreSnapshot,*,operation_id:str,generation:int,target_repository:str,feature_id:str,expected_revision:int,current_stage:str,task_identity:str,role:str,candidate_head_sha:str|None,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    p=_projection(snapshot,operation_id,generation)
    if p['expected_feature_revision']!=expected_revision: raise StoreCommandError('STALE_REVISION','expected feature revision does not match operation')
    material=semantic_effect_material(target_repository=target_repository,feature_id=feature_id,expected_revision=expected_revision,current_stage=current_stage,task_identity=task_identity,role=role,candidate_head_sha=candidate_head_sha); key=semantic_effect_key(**material); path=reservation_path(key); value={'semantic_effect_key':key,'external_dispatch_key':external_dispatch_key(key),**material,'created_operation_id':operation_id,'created_generation':generation,'created_at':occurred_at,'trusted_context_digest':trusted_context_digest}
    existing=snapshot.get(path)
    if existing is not None:
        a=dict(existing); b=dict(value)
        for v in (a,b):
            for k in ('created_at','created_operation_id','created_generation','trusted_context_digest'): v.pop(k,None)
        if canonical_json(a)!=canonical_json(b): raise StoreCommandError('ALREADY_CLAIMED','semantic effect reservation conflicts with existing identity')
        return StoreMutationPlan(snapshot.ref_sha,tuple(),{'semantic_effect_key':key,'external_dispatch_key':existing['external_dispatch_key']})
    return StoreMutationPlan(snapshot.ref_sha,(StoreMutation('create_immutable',path,value),),{'semantic_effect_key':key,'external_dispatch_key':value['external_dispatch_key']})

def plan_dispatch_claim(snapshot:StoreSnapshot,*,operation_id:str,generation:int,effect_key:str,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    _projection(snapshot,operation_id,generation); reservation=snapshot.get(reservation_path(effect_key))
    if not isinstance(reservation,dict): raise StoreCommandError('INVALID_REQUEST','semantic reservation does not exist')
    cid=dispatch_claim_id(operation_id,generation,effect_key); path=dispatch_claim_path(cid); value={'claim_id':cid,'operation_id':operation_id,'operation_generation':generation,'semantic_effect_key':effect_key,'external_dispatch_key':reservation['external_dispatch_key'],'created_at':occurred_at,'trusted_context_digest':trusted_context_digest}
    if snapshot.get(path) is not None:return StoreMutationPlan(snapshot.ref_sha,tuple(),{'claim_id':cid,'external_dispatch_key':value['external_dispatch_key']})
    muts=[StoreMutation('create_immutable',path,value)]; working=apply_plan_to_snapshot(snapshot,StoreMutationPlan(snapshot.ref_sha,tuple(muts),{})); working,e=_append_event(working,operation_id=operation_id,generation=generation,event_type='dispatch.claimed',occurred_at=occurred_at,payload={'claim_id':cid,'semantic_effect_key':effect_key,'external_dispatch_key':value['external_dispatch_key']},trusted_context_digest=trusted_context_digest,identity_material={'claim_id':cid}); muts.append(e); return _finalize(snapshot,working,muts,operation_id,{'claim_id':cid,'external_dispatch_key':value['external_dispatch_key']})

def plan_authorize_launch(snapshot:StoreSnapshot,*,operation_id:str,generation:int,claim_id:str,dispatch_id:str,occurred_at:str,trusted_context_digest:str,verified_expected_revision:int,verified_stage:str,verified_candidate_head_sha:str|None=None)->StoreMutationPlan:
    _projection(snapshot,operation_id,generation); claim=snapshot.get(dispatch_claim_path(claim_id))
    if not isinstance(claim,dict) or claim.get('operation_id')!=operation_id or claim.get('operation_generation')!=generation: raise StoreCommandError('ALREADY_CLAIMED','dispatch claim is not owned by current generation')
    reservation=snapshot.get(reservation_path(claim['semantic_effect_key']))
    if not isinstance(reservation,dict) or reservation.get('external_dispatch_key')!=claim.get('external_dispatch_key'): raise StoreInvariantError('dispatch claim/reservation binding mismatch')
    if reservation.get('expected_revision')!=verified_expected_revision: raise StoreCommandError('STALE_REVISION','verified feature revision does not match reservation')
    if reservation.get('current_stage')!=verified_stage or reservation.get('candidate_head_sha')!=verified_candidate_head_sha: raise StoreCommandError('STALE_REVISION','verified stage/candidate binding does not match reservation')
    payload={'claim_id':claim_id,'dispatch_id':dispatch_id,'semantic_effect_key':claim['semantic_effect_key'],'external_dispatch_key':claim['external_dispatch_key'],'feature_id':reservation['feature_id'],'expected_revision':verified_expected_revision,'stage':verified_stage,'role':reservation['role'],'candidate_head_sha':verified_candidate_head_sha}; working,e=_append_event(snapshot,operation_id=operation_id,generation=generation,event_type='dispatch.launch.authorized',occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest,identity_material={'claim_id':claim_id,'dispatch_id':dispatch_id}); return _finalize(snapshot,working,[e],operation_id)

def plan_launch_lookup(snapshot:StoreSnapshot,*,operation_id:str,generation:int,external_dispatch_key_value:str,lookup_state:str,receipt_id:str|None,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    if lookup_state not in {'NOT_LAUNCHED','LAUNCHED','UNKNOWN'}: raise StoreCommandError('INVALID_REQUEST','invalid launch receipt state')
    p=_projection(snapshot,operation_id,generation,allow_blocked=True,allow_needs_user=True,allow_cancelled=True)
    if external_dispatch_key_value not in p['authorized_dispatches']: raise StoreCommandError('INVALID_REQUEST','lookup is not correlated to an authorized dispatch')
    payload={'external_dispatch_key':external_dispatch_key_value,'lookup_state':lookup_state,'receipt_id':receipt_id}; working,e=_append_event(snapshot,operation_id=operation_id,generation=generation,event_type='dispatch.launch.lookup-recorded',occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest,identity_material=payload); return _finalize(snapshot,working,[e],operation_id)

def plan_callback(snapshot:StoreSnapshot,*,operation_id:str,generation:int,callback_id:str,callback_payload:dict[str,Any],external_dispatch_key_value:str,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    p=_projection(snapshot,operation_id,generation,allow_blocked=True,allow_needs_user=True,allow_cancelled=True)
    if external_dispatch_key_value not in p['authorized_dispatches']: raise StoreCommandError('INVALID_REQUEST','callback is not correlated to an authorized dispatch')
    payload={'callback_id':callback_id,'callback_digest':digest_json(callback_payload),'external_dispatch_key':external_dispatch_key_value}; working,e=_append_event(snapshot,operation_id=operation_id,generation=generation,event_type='worker.callback.recorded',occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest,identity_material={'callback_id':callback_id}); return _finalize(snapshot,working,[e],operation_id)

def plan_operation_fact(snapshot:StoreSnapshot,*,operation_id:str,generation:int,event_type:str,payload:dict[str,Any],occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    allowed={'loop.step.selected','worker.result.validated','worker.result.rejected','feature.event.translated','loop.stable-stop'}
    if event_type not in allowed: raise StoreCommandError('INVALID_REQUEST','unsupported vertical operation fact')
    _projection(snapshot,operation_id,generation,allow_blocked=True,allow_needs_user=True)
    working,e=_append_event(snapshot,operation_id=operation_id,generation=generation,event_type=event_type,occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest); return _finalize(snapshot,working,[e],operation_id)

def plan_needs_user(snapshot:StoreSnapshot,*,operation_id:str,generation:int,reason_code:str,summary:str,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    _projection(snapshot,operation_id,generation,allow_blocked=True,allow_needs_user=True)
    payload={'reason_code':reason_code[:128],'summary':summary[:512]}; working,e=_append_event(snapshot,operation_id=operation_id,generation=generation,event_type='operation.needs-user',occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest,identity_material=payload); return _finalize(snapshot,working,[e],operation_id)

def plan_cancel(snapshot:StoreSnapshot,*,operation_id:str,reason:str,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    p=rebuild_projection(snapshot,operation_id)
    if p['status']=='CANCELLED': return StoreMutationPlan(snapshot.ref_sha,tuple(),projection_public(p))
    if p['status']=='DONE': raise StoreCommandError('ALREADY_APPLIED','operation is already done')
    working,e=_append_event(snapshot,operation_id=operation_id,generation=p['generation'],event_type='operation.cancelled',occurred_at=occurred_at,payload={'reason':reason[:512]},trusted_context_digest=trusted_context_digest,identity_material={'operation_id':operation_id}); return _finalize(snapshot,working,[e],operation_id)

def plan_takeover(snapshot:StoreSnapshot,*,operation_id:str,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    p=rebuild_projection(snapshot,operation_id)
    if p['status'] in TERMINAL_STATUSES: raise StoreCommandError('CANCELLED_OPERATION','terminal operation cannot be taken over')
    old=p['generation']; new=old+1; claims=[c for c in _feature_claims(snapshot,p['target_repository'],p['feature_id']) if c.get('operation_id')==operation_id]
    if not claims: raise StoreInvariantError('operation takeover lacks feature claim')
    source=sorted(claims,key=lambda c:c['operation_generation'])[-1]; cid=feature_claim_id(operation_id,new); new_claim={'claim_id':cid,'target_repository':p['target_repository'],'feature_id':p['feature_id'],'operation_id':operation_id,'operation_generation':new,'expected_revision':p['expected_feature_revision'],'idempotency_key':source['idempotency_key'],'created_at':occurred_at,'trusted_context_digest':trusted_context_digest}; muts=[]; working=snapshot
    path=feature_claim_path(p['target_repository'],p['feature_id'],cid)
    if snapshot.get(path) is None: muts.append(StoreMutation('create_immutable',path,new_claim)); working=apply_plan_to_snapshot(working,StoreMutationPlan(snapshot.ref_sha,(muts[-1],),{}))
    working,e1=_append_event(working,operation_id=operation_id,generation=old,event_type='operation.superseded',occurred_at=occurred_at,payload={'superseded_generation':old,'next_generation':new},trusted_context_digest=trusted_context_digest,identity_material={'next_generation':new}); muts.append(e1)
    working,e2=_append_event(working,operation_id=operation_id,generation=new,event_type='operation.generation.started',occurred_at=occurred_at,payload={'previous_generation':old},trusted_context_digest=trusted_context_digest,identity_material={'generation':new}); muts.append(e2); return _finalize(snapshot,working,muts,operation_id)

def _plan_persist_event(snapshot:StoreSnapshot,*,operation_id:str,generation:int,event_type:str,feature_event_id:str,expected_revision:int,target_ref:str,candidate_head_sha:str|None,occurred_at:str,trusted_context_digest:str)->StoreMutationPlan:
    p=_projection(snapshot,operation_id,generation,allow_blocked=(event_type=='persist.confirmed'),allow_needs_user=(event_type=='persist.confirmed'),allow_cancelled=(event_type=='persist.confirmed'))
    if p['expected_feature_revision']!=expected_revision: raise StoreCommandError('STALE_REVISION','persist expected revision does not match operation')
    if event_type=='persist.linearized' and feature_event_id not in p['requested_persists']: raise StoreCommandError('INVALID_REQUEST','persist linearization lacks requested record')
    if event_type=='persist.confirmed' and feature_event_id not in p['linearized_persists']: raise StoreCommandError('INVALID_REQUEST','persist confirmation lacks linearization')
    payload={'feature_event_id':feature_event_id,'expected_revision':expected_revision,'target_ref':target_ref,'candidate_head_sha':candidate_head_sha}; working,e=_append_event(snapshot,operation_id=operation_id,generation=generation,event_type=event_type,occurred_at=occurred_at,payload=payload,trusted_context_digest=trusted_context_digest,identity_material={'feature_event_id':feature_event_id,'event_type':event_type}); return _finalize(snapshot,working,[e],operation_id)
def plan_persist_requested(snapshot:StoreSnapshot,**kwargs)->StoreMutationPlan:return _plan_persist_event(snapshot,event_type='persist.requested',**kwargs)
def plan_persist_linearized(snapshot:StoreSnapshot,**kwargs)->StoreMutationPlan:return _plan_persist_event(snapshot,event_type='persist.linearized',**kwargs)
def plan_persist_confirmed(snapshot:StoreSnapshot,**kwargs)->StoreMutationPlan:return _plan_persist_event(snapshot,event_type='persist.confirmed',**kwargs)
def query_unfinished(snapshot:StoreSnapshot,*,target_repository:str|None=None,feature_id:str|None=None):return unfinished_operations(snapshot,target_repository=target_repository,feature_id=feature_id)
