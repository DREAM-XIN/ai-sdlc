#!/usr/bin/env python3
"""Pure helpers for composing immutable Effect Lineage mutations."""
from __future__ import annotations

from typing import Any

from operator_store_model import (
    StoreMutation,
    StoreMutationPlan,
    StoreSnapshot,
    apply_plan_to_snapshot,
    canonical_json,
)
from operator_effect_lineage_model import (
    LineageInvariantError,
    lineage_event_path,
    lineage_events,
    lineage_projection_path,
    make_lineage_event,
    next_lineage_sequence,
    rebuild_lineage_projection,
)


def add_immutable(
    original: StoreSnapshot,
    working: StoreSnapshot,
    mutations: list[StoreMutation],
    *,
    path: str,
    value: dict[str, Any],
) -> StoreSnapshot:
    existing = working.get(path)
    if existing is not None:
        if canonical_json(existing) != canonical_json(value):
            raise LineageInvariantError("immutable lineage artifact identity conflict")
        return working
    mutation = StoreMutation("create_immutable", path, value)
    mutations.append(mutation)
    return apply_plan_to_snapshot(
        working,
        StoreMutationPlan(original.ref_sha, (mutation,), {}),
    )


def append_lineage_event(
    original: StoreSnapshot,
    working: StoreSnapshot,
    mutations: list[StoreMutation],
    *,
    lineage_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any],
    trusted_context_digest: str,
    identity_material: dict[str, Any] | None = None,
) -> StoreSnapshot:
    probe = make_lineage_event(
        lineage_id=lineage_id,
        sequence=1,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        trusted_context_digest=trusted_context_digest,
        identity_material=identity_material,
    )
    for existing in lineage_events(working, lineage_id):
        if existing["event_id"] != probe["event_id"]:
            continue
        left = dict(existing)
        right = dict(probe)
        left.pop("sequence", None)
        right.pop("sequence", None)
        left["occurred_at"] = right["occurred_at"] = "<ignored>"
        if canonical_json(left) != canonical_json(right):
            raise LineageInvariantError("lineage event identity already exists with different semantics")
        return working

    event = make_lineage_event(
        lineage_id=lineage_id,
        sequence=next_lineage_sequence(working, lineage_id),
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        trusted_context_digest=trusted_context_digest,
        identity_material=identity_material,
    )
    return add_immutable(
        original,
        working,
        mutations,
        path=lineage_event_path(lineage_id, event["sequence"], event["event_id"]),
        value=event,
    )


def append_projection(
    original: StoreSnapshot,
    working: StoreSnapshot,
    mutations: list[StoreMutation],
    *,
    lineage_id: str,
) -> StoreSnapshot:
    projection = rebuild_lineage_projection(working, lineage_id)
    mutation = StoreMutation("replace_projection", lineage_projection_path(lineage_id), projection)
    mutations.append(mutation)
    return apply_plan_to_snapshot(
        working,
        StoreMutationPlan(original.ref_sha, (mutation,), {}),
    )
