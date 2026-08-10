#!/usr/bin/env python3
"""Deterministic durable Effect Lineage identities, records, reducer and invariants."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from operator_store_model import (
    STORE_ROOT,
    StoreInvariantError,
    StoreSnapshot,
    canonical_json,
    digest_json,
    normalize_repository,
    operation_events,
    operation_ids,
    reservation_path,
)

LINEAGE_KEY_SCHEMA = "ai-sdlc.effect-lineage-key/v1"
LINEAGE_SCHEMA = "ai-sdlc.effect-lineage/v1"
LINEAGE_EVENT_SCHEMA = "ai-sdlc.effect-lineage-event/v1"
LINEAGE_PROPOSAL_SCHEMA = "ai-sdlc.effect-lineage-proposal/v1"
LINEAGE_MEMBER_SCHEMA = "ai-sdlc.effect-lineage-member/v1"
LINEAGE_RESOLUTION_SCHEMA = "ai-sdlc.effect-resolution-record/v1"
LINEAGE_PROJECTION_SCHEMA = "ai-sdlc.effect-lineage-projection/v1"
SUPPORTED_VERTICAL_PROFILE = "vertical-implementation-review-qa/v1"

ALLOWED_LINEAGE_EVENTS = frozenset(
    {
        "lineage.root-activated",
        "lineage.successor-proposed",
        "lineage.predecessor-blocked",
        "lineage.predecessor-correlated",
        "lineage.predecessor-retired",
        "lineage.successor-activated",
        "lineage.member-superseded",
        "lineage.member-adopted",
        "lineage.legacy-attached",
        "lineage.legacy-unresolved",
        "lineage.resolution-applied",
    }
)
PREDECESSOR_STATES = frozenset(
    {
        "NEVER_AUTHORIZED",
        "AUTHORIZED_UNCONFIRMED",
        "AUTHORIZED_NOT_LAUNCHED_OBSERVED",
        "LAUNCHED_CORRELATED",
        "UNKNOWN",
        "RETIRED_NO_DUPLICATE_PROVEN",
        "LEGACY_UNRESOLVED",
    }
)

_LINEAGE_EVENT_RE = re.compile(
    r"^state/operator/v1/effect-lineages/events/([^/]+)/(\d+)-([^/]+)\.json$"
)
_MEMBER_RE = re.compile(
    r"^state/operator/v1/effect-lineages/members/([^/]+)/([^/]+)\.json$"
)
_PROPOSAL_RE = re.compile(
    r"^state/operator/v1/effect-lineages/proposals/([^/]+)/([^/]+)\.json$"
)
_RESOLUTION_RE = re.compile(
    r"^state/operator/v1/effect-lineages/resolutions/([^/]+)/([^/]+)\.json$"
)


class LineageInvariantError(StoreInvariantError):
    pass


class AmbiguousLineageError(LineageInvariantError):
    code = "AMBIGUOUS_LINEAGE"


@dataclass(frozen=True)
class CausalWork:
    causal_work_id: str
    external_effect_scope: str


class CausalWorkResolver:
    """Trusted v0.3 vertical-profile causal identity resolver.

    Revision, candidate, Operation id/generation, runner and session identity are deliberately
    absent. The caller supplies only reviewed vertical planner semantics (logical slot/task id).
    """

    def resolve(
        self,
        *,
        feature_id: str,
        operation_profile: str,
        effect_kind: str,
        role: str,
        logical_work_slot: str,
        task_id: str | None,
    ) -> CausalWork:
        if operation_profile != SUPPORTED_VERTICAL_PROFILE or effect_kind != "worker-dispatch":
            raise AmbiguousLineageError("unsupported profile/effect kind has no reviewed lineage mapping")
        if not feature_id or role not in {"developer", "reviewer", "qa"}:
            raise AmbiguousLineageError("trusted causal work inputs are incomplete")

        if logical_work_slot == "IMPLEMENTATION_WORK" and role == "developer":
            causal = f"implementation:{feature_id}:primary"
        elif logical_work_slot == "CODE_REMEDIATION" and role == "developer":
            if not task_id:
                raise AmbiguousLineageError("code remediation lacks durable remediation task id")
            causal = f"implementation:{feature_id}:remediation:{task_id}"
        elif logical_work_slot in {"CODE_REVIEW", "CODE_REREVIEW"} and role == "reviewer":
            # Candidate/remediation rounds are exact-work changes inside one ordered review lineage.
            causal = f"code-review:{feature_id}:review"
        elif logical_work_slot == "VERIFICATION_QA" and role == "qa":
            causal = f"verification:{feature_id}:qa"
        else:
            raise AmbiguousLineageError("logical work slot/role mapping is ambiguous")
        return CausalWork(causal, "vertical-worker-dispatch")


def lineage_key_material(
    *,
    target_repository: str,
    feature_id: str,
    operation_profile: str,
    effect_kind: str,
    role: str,
    causal_work_id: str,
    external_effect_scope: str,
) -> dict[str, Any]:
    material = {
        "schema": LINEAGE_KEY_SCHEMA,
        "target_repository": normalize_repository(target_repository),
        "feature_id": str(feature_id),
        "operation_profile": str(operation_profile),
        "effect_kind": str(effect_kind),
        "role": str(role),
        "causal_work_id": str(causal_work_id),
        "external_effect_scope": str(external_effect_scope),
    }
    if any(not str(material[k]).strip() for k in material if k != "schema"):
        raise LineageInvariantError("lineage identity fields must be non-empty")
    return material


def effect_lineage_id(**kwargs: Any) -> str:
    return "lin-" + digest_json(lineage_key_material(**kwargs))[:48]


def anchor_path(lineage_id: str) -> str:
    return f"{STORE_ROOT}/effect-lineages/anchors/{lineage_id}.json"


def member_path(lineage_id: str, semantic_effect_key: str) -> str:
    return f"{STORE_ROOT}/effect-lineages/members/{lineage_id}/{semantic_effect_key}.json"


def proposal_path(lineage_id: str, proposal_id: str) -> str:
    return f"{STORE_ROOT}/effect-lineages/proposals/{lineage_id}/{proposal_id}.json"


def lineage_event_path(lineage_id: str, sequence: int, event_id: str) -> str:
    return f"{STORE_ROOT}/effect-lineages/events/{lineage_id}/{sequence:08d}-{event_id}.json"


def resolution_path(lineage_id: str, resolution_id: str) -> str:
    return f"{STORE_ROOT}/effect-lineages/resolutions/{lineage_id}/{resolution_id}.json"


def lineage_projection_path(lineage_id: str) -> str:
    return f"{STORE_ROOT}/effect-lineages/projections/{lineage_id}.json"


def make_anchor(*, lineage_id: str, material: dict[str, Any], created_at: str, trusted_context_digest: str) -> dict[str, Any]:
    expected = effect_lineage_id(
        target_repository=material["target_repository"],
        feature_id=material["feature_id"],
        operation_profile=material["operation_profile"],
        effect_kind=material["effect_kind"],
        role=material["role"],
        causal_work_id=material["causal_work_id"],
        external_effect_scope=material["external_effect_scope"],
    )
    if lineage_id != expected:
        raise LineageInvariantError("lineage anchor id/material mismatch")
    return {
        "schema_version": LINEAGE_SCHEMA,
        "effect_lineage_id": lineage_id,
        **{k: material[k] for k in (
            "target_repository", "feature_id", "operation_profile", "effect_kind", "role",
            "causal_work_id", "external_effect_scope"
        )},
        "created_at": created_at,
        "trusted_context_digest": trusted_context_digest,
    }


def make_member(
    *,
    lineage_id: str,
    semantic_effect_key: str,
    external_dispatch_key: str,
    operation_id: str,
    operation_generation: int,
    expected_revision: int,
    stage: str,
    task_identity: str,
    role: str,
    candidate_head_sha: str | None,
    predecessor_semantic_effect_key: str | None,
    activated_from_proposal_id: str | None,
    activated_at: str,
    trusted_context_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": LINEAGE_MEMBER_SCHEMA,
        "effect_lineage_id": lineage_id,
        "semantic_effect_key": semantic_effect_key,
        "external_dispatch_key": external_dispatch_key,
        "operation_id_at_activation": operation_id,
        "operation_generation_at_activation": operation_generation,
        "expected_revision": expected_revision,
        "stage": stage,
        "task_identity": task_identity,
        "role": role,
        "candidate_head_sha": candidate_head_sha,
        "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
        "activated_from_proposal_id": activated_from_proposal_id,
        "activated_at": activated_at,
        "trusted_context_digest": trusted_context_digest,
    }


def proposal_identity(
    *,
    lineage_id: str,
    predecessor_semantic_effect_key: str,
    proposed_exact_semantic_material: dict[str, Any],
    current_feature_revision: int,
    current_stage: str,
    current_target_ref: str,
    current_candidate_head_sha: str | None,
    operation_id: str,
    operation_generation: int,
    trusted_profile_digest: str,
) -> str:
    return "prop-" + digest_json(
        {
            "effect_lineage_id": lineage_id,
            "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
            "proposed_exact_semantic_material": proposed_exact_semantic_material,
            "current_feature_revision": current_feature_revision,
            "current_stage": current_stage,
            "current_target_ref": current_target_ref,
            "current_candidate_head_sha": current_candidate_head_sha,
            "operation_id": operation_id,
            "operation_generation": operation_generation,
            "trusted_profile_digest": trusted_profile_digest,
        }
    )[:48]


def make_proposal(
    *,
    proposal_id: str,
    lineage_id: str,
    predecessor_semantic_effect_key: str,
    proposed_semantic_effect_key: str,
    proposed_exact_semantic_material: dict[str, Any],
    current_feature_revision: int,
    current_stage: str,
    current_target_ref: str,
    current_candidate_head_sha: str | None,
    operation_id: str,
    operation_generation: int,
    trusted_profile_digest: str,
    proposed_at: str,
    trusted_context_digest: str,
) -> dict[str, Any]:
    expected = proposal_identity(
        lineage_id=lineage_id,
        predecessor_semantic_effect_key=predecessor_semantic_effect_key,
        proposed_exact_semantic_material=proposed_exact_semantic_material,
        current_feature_revision=current_feature_revision,
        current_stage=current_stage,
        current_target_ref=current_target_ref,
        current_candidate_head_sha=current_candidate_head_sha,
        operation_id=operation_id,
        operation_generation=operation_generation,
        trusted_profile_digest=trusted_profile_digest,
    )
    if proposal_id != expected:
        raise LineageInvariantError("proposal id/material mismatch")
    return {
        "schema_version": LINEAGE_PROPOSAL_SCHEMA,
        "proposal_id": proposal_id,
        "effect_lineage_id": lineage_id,
        "predecessor_semantic_effect_key": predecessor_semantic_effect_key,
        "proposed_semantic_effect_key": proposed_semantic_effect_key,
        "proposed_exact_semantic_material": proposed_exact_semantic_material,
        "current_feature_revision": current_feature_revision,
        "current_stage": current_stage,
        "current_target_ref": current_target_ref,
        "current_candidate_head_sha": current_candidate_head_sha,
        "operation_id": operation_id,
        "operation_generation": operation_generation,
        "trusted_profile_digest": trusted_profile_digest,
        "proposed_at": proposed_at,
        "trusted_context_digest": trusted_context_digest,
    }


def make_lineage_event(
    *,
    lineage_id: str,
    sequence: int,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    trusted_context_digest: str,
    identity_material: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in ALLOWED_LINEAGE_EVENTS or sequence < 1:
        raise LineageInvariantError("unsupported lineage event")
    material = dict(identity_material or payload)
    material.update({"effect_lineage_id": lineage_id, "event_type": event_type})
    event_id = event_type.replace(".", "-") + "-" + digest_json(material)[:32]
    return {
        "schema_version": LINEAGE_EVENT_SCHEMA,
        "effect_lineage_id": lineage_id,
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
        "payload": dict(payload),
    }


def lineage_events(snapshot: StoreSnapshot, lineage_id: str) -> list[dict[str, Any]]:
    rows: list[tuple[int, str, dict[str, Any]]] = []
    for path, value in snapshot.files.items():
        match = _LINEAGE_EVENT_RE.match(path)
        if match and match.group(1) == lineage_id:
            rows.append((int(match.group(2)), match.group(3), value))
    rows.sort(key=lambda row: row[0])
    for expected, (sequence, event_id, event) in enumerate(rows, start=1):
        if (
            sequence != expected
            or not isinstance(event, dict)
            or event.get("schema_version") != LINEAGE_EVENT_SCHEMA
            or event.get("effect_lineage_id") != lineage_id
            or event.get("sequence") != sequence
            or event.get("event_id") != event_id
            or event.get("event_type") not in ALLOWED_LINEAGE_EVENTS
        ):
            raise LineageInvariantError("lineage event binding/schema mismatch")
    return [row[2] for row in rows]


def next_lineage_sequence(snapshot: StoreSnapshot, lineage_id: str) -> int:
    return len(lineage_events(snapshot, lineage_id)) + 1


def lineage_members(snapshot: StoreSnapshot, lineage_id: str) -> dict[str, dict[str, Any]]:
    members: dict[str, dict[str, Any]] = {}
    for path, value in snapshot.files.items():
        match = _MEMBER_RE.match(path)
        if not match or match.group(1) != lineage_id:
            continue
        key = match.group(2)
        if not isinstance(value, dict) or value.get("effect_lineage_id") != lineage_id or value.get("semantic_effect_key") != key:
            raise LineageInvariantError("lineage member path binding mismatch")
        reservation = snapshot.get(reservation_path(key))
        if not isinstance(reservation, dict) or reservation.get("external_dispatch_key") != value.get("external_dispatch_key"):
            raise LineageInvariantError("lineage member/reservation binding mismatch")
        members[key] = value
    return members


def lineage_proposals(snapshot: StoreSnapshot, lineage_id: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path, value in snapshot.files.items():
        match = _PROPOSAL_RE.match(path)
        if not match or match.group(1) != lineage_id:
            continue
        pid = match.group(2)
        if not isinstance(value, dict) or value.get("effect_lineage_id") != lineage_id or value.get("proposal_id") != pid:
            raise LineageInvariantError("lineage proposal path binding mismatch")
        if "external_dispatch_key" in value:
            raise LineageInvariantError("successor proposal must not have external dispatch identity")
        rows[pid] = value
    return rows


def lineage_resolutions(snapshot: StoreSnapshot, lineage_id: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path, value in snapshot.files.items():
        match = _RESOLUTION_RE.match(path)
        if not match or match.group(1) != lineage_id:
            continue
        rid = match.group(2)
        if not isinstance(value, dict) or value.get("effect_lineage_id") != lineage_id or value.get("resolution_id") != rid:
            raise LineageInvariantError("resolution path binding mismatch")
        rows[rid] = value
    return rows


def member_lineage(snapshot: StoreSnapshot, semantic_effect_key: str) -> str | None:
    found: set[str] = set()
    for path in snapshot.files:
        match = _MEMBER_RE.match(path)
        if match and match.group(2) == semantic_effect_key:
            found.add(match.group(1))
    if len(found) > 1:
        raise LineageInvariantError("one semantic effect belongs to multiple lineages")
    return next(iter(found), None)


def predecessor_effective_state(snapshot: StoreSnapshot, member: dict[str, Any], lineage_id: str) -> str:
    key = str(member["semantic_effect_key"])
    external_key = str(member["external_dispatch_key"])
    retired = False
    legacy_unresolved = False
    for event in lineage_events(snapshot, lineage_id):
        payload = event.get("payload") or {}
        if payload.get("predecessor_semantic_effect_key") != key and payload.get("semantic_effect_key") != key:
            continue
        if event["event_type"] == "lineage.predecessor-retired":
            retired = True
        elif event["event_type"] == "lineage.legacy-unresolved":
            legacy_unresolved = True
    if legacy_unresolved:
        return "LEGACY_UNRESOLVED"
    if retired:
        return "RETIRED_NO_DUPLICATE_PROVEN"

    rows: list[tuple[str, str, int, dict[str, Any]]] = []
    for operation_id in operation_ids(snapshot):
        for event in operation_events(snapshot, operation_id):
            payload = event.get("payload") or {}
            if payload.get("external_dispatch_key") == external_key or payload.get("semantic_effect_key") == key:
                rows.append((str(event.get("occurred_at", "")), operation_id, int(event["sequence"]), event))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    authorized = False
    latest_lookup: str | None = None
    for _, _, _, event in rows:
        payload = event.get("payload") or {}
        if event["event_type"] == "dispatch.launch.authorized" and payload.get("external_dispatch_key") == external_key:
            authorized = True
        elif event["event_type"] == "dispatch.launch.lookup-recorded" and payload.get("external_dispatch_key") == external_key:
            latest_lookup = str(payload.get("lookup_state"))
    if not authorized:
        return "NEVER_AUTHORIZED"
    if latest_lookup == "UNKNOWN":
        return "UNKNOWN"
    if latest_lookup == "NOT_LAUNCHED":
        return "AUTHORIZED_NOT_LAUNCHED_OBSERVED"
    if latest_lookup == "LAUNCHED":
        return "LAUNCHED_CORRELATED"
    return "AUTHORIZED_UNCONFIRMED"


def rebuild_lineage_projection(snapshot: StoreSnapshot, lineage_id: str) -> dict[str, Any]:
    anchor = snapshot.get(anchor_path(lineage_id))
    if not isinstance(anchor, dict) or anchor.get("effect_lineage_id") != lineage_id:
        raise LineageInvariantError("lineage anchor not found or invalid")
    members = lineage_members(snapshot, lineage_id)
    proposals = lineage_proposals(snapshot, lineage_id)
    resolutions = lineage_resolutions(snapshot, lineage_id)
    events = lineage_events(snapshot, lineage_id)

    current_leaf: str | None = None
    current_proposal: str | None = None
    blocks_on: str | None = None
    relations: list[dict[str, Any]] = []
    activated: set[str] = set()

    for event in events:
        event_type = event["event_type"]
        payload = event.get("payload") or {}
        if event_type in {"lineage.root-activated", "lineage.legacy-attached"}:
            key = str(payload.get("semantic_effect_key") or "")
            if key not in members:
                raise LineageInvariantError("lineage activation references missing member")
            member = members[key]
            if current_leaf is not None or member.get("predecessor_semantic_effect_key") is not None:
                raise LineageInvariantError("lineage has invalid root activation")
            current_leaf = key
            activated.add(key)
        elif event_type == "lineage.successor-proposed":
            pid = str(payload.get("proposal_id") or "")
            if pid not in proposals or current_leaf is None:
                raise LineageInvariantError("successor proposal references invalid lineage state")
            proposal = proposals[pid]
            if proposal.get("predecessor_semantic_effect_key") != current_leaf:
                raise LineageInvariantError("successor proposal is not based on current leaf")
            current_proposal = pid
            blocks_on = current_leaf
            relations.append({"kind": "blocks_on", "from": proposal["proposed_semantic_effect_key"], "to": current_leaf})
        elif event_type == "lineage.predecessor-blocked":
            predecessor = str(payload.get("predecessor_semantic_effect_key") or "")
            if predecessor != current_leaf:
                raise LineageInvariantError("lineage block references non-leaf predecessor")
            blocks_on = predecessor
        elif event_type == "lineage.successor-activated":
            pid = str(payload.get("proposal_id") or "")
            key = str(payload.get("semantic_effect_key") or "")
            if pid not in proposals or key not in members or current_leaf is None:
                raise LineageInvariantError("successor activation references missing proposal/member")
            proposal = proposals[pid]
            member = members[key]
            if proposal.get("predecessor_semantic_effect_key") != current_leaf or member.get("predecessor_semantic_effect_key") != current_leaf:
                raise LineageInvariantError("successor activation predecessor mismatch")
            if key in activated:
                raise LineageInvariantError("duplicate active lineage member")
            relations.append({"kind": "predecessor", "from": key, "to": current_leaf})
            current_leaf = key
            activated.add(key)
            current_proposal = None
            blocks_on = None
        elif event_type == "lineage.member-superseded":
            predecessor = str(payload.get("semantic_effect_key") or "")
            relations.append({"kind": "supersedes", "from": current_proposal, "to": predecessor})
        elif event_type in {"lineage.member-adopted", "lineage.predecessor-correlated"}:
            predecessor = str(payload.get("predecessor_semantic_effect_key") or payload.get("semantic_effect_key") or "")
            relations.append({"kind": "adopts", "from": current_proposal, "to": predecessor})
        elif event_type in {"lineage.predecessor-retired", "lineage.resolution-applied", "lineage.legacy-unresolved"}:
            pass
        else:
            raise LineageInvariantError("unsupported lineage event type")

    if members and not events:
        raise LineageInvariantError("lineage members exist without immutable activation history")
    predecessor_state = None
    if current_leaf is not None:
        predecessor_state = predecessor_effective_state(snapshot, members[current_leaf], lineage_id)
        if predecessor_state not in PREDECESSOR_STATES:
            raise LineageInvariantError("invalid predecessor effective state")
    history = {
        "anchor": anchor,
        "members": [members[k] for k in sorted(members)],
        "proposals": [proposals[k] for k in sorted(proposals)],
        "events": events,
        "resolutions": [resolutions[k] for k in sorted(resolutions)],
    }
    return {
        "schema_version": LINEAGE_PROJECTION_SCHEMA,
        "effect_lineage_id": lineage_id,
        "current_leaf_semantic_effect_key": current_leaf,
        "current_leaf_external_dispatch_key": members[current_leaf]["external_dispatch_key"] if current_leaf else None,
        "current_proposal_id": current_proposal,
        "blocks_on_semantic_effect_key": blocks_on,
        "predecessor_state": predecessor_state,
        "relations": relations,
        "last_sequence": len(events),
        "history_digest": digest_json(history),
    }


def assert_projection_equivalent(snapshot: StoreSnapshot, lineage_id: str) -> dict[str, Any]:
    rebuilt = rebuild_lineage_projection(snapshot, lineage_id)
    cached = snapshot.get(lineage_projection_path(lineage_id))
    if cached is not None and canonical_json(cached) != canonical_json(rebuilt):
        raise LineageInvariantError("lineage projection cache diverges from immutable history")
    return rebuilt
