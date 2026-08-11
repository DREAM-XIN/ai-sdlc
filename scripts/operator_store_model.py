#!/usr/bin/env python3
"""Pure deterministic model/reducer helpers for the v0.3 Operator Store."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any

STORE_ROOT = "state/operator/v1"
EVENT_SCHEMA_VERSION = "ai-sdlc.operation-event/v1"
TERMINAL_STATUSES = frozenset({"DONE", "CANCELLED"})
VALID_STATUSES = frozenset(
    {"RUNNING", "WAITING_EXTERNAL", "BLOCKED", "NEEDS_USER", "DONE", "CANCELLED"}
)
_EVENT_RE = re.compile(r"^state/operator/v1/operations/([^/]+)/events/(\d+)-([^/]+)\.json$")
_DECISION_RE = re.compile(r"^state/operator/v1/decisions/([A-Za-z0-9._:-]{1,128})\.json$")
_NOTIFICATION_RE = re.compile(r"^state/operator/v1/notifications/([A-Za-z0-9._:-]{1,128})\.json$")


class StoreInvariantError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def normalize_repository(value: str) -> str:
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        raise StoreInvariantError("invalid target repository")
    return text.lower()


def semantic_effect_material(
    *,
    target_repository: str,
    feature_id: str,
    expected_revision: int,
    current_stage: str,
    task_identity: str,
    role: str,
    candidate_head_sha: str | None = None,
) -> dict[str, Any]:
    if expected_revision < 0:
        raise StoreInvariantError("expected revision must be non-negative")
    values = {
        "target_repository": normalize_repository(target_repository),
        "feature_id": feature_id,
        "expected_revision": expected_revision,
        "current_stage": current_stage,
        "task_identity": task_identity,
        "role": role,
        "candidate_head_sha": candidate_head_sha,
    }
    if any(not str(values[k]).strip() for k in ("feature_id", "current_stage", "task_identity", "role")):
        raise StoreInvariantError("semantic effect identity fields must be non-empty")
    return values


def semantic_effect_key(**kwargs: Any) -> str:
    return digest_json(semantic_effect_material(**kwargs))


def external_dispatch_key(effect_key: str) -> str:
    return "dispatch-" + hashlib.sha256(("ai-sdlc-external:" + effect_key).encode()).hexdigest()[:40]


def operation_id_for(target_repository: str, feature_id: str, idempotency_key: str) -> str:
    return "op-" + digest_json(
        {
            "repository": normalize_repository(target_repository),
            "feature_id": feature_id,
            "idempotency_key": idempotency_key,
        }
    )[:40]


def feature_claim_id(operation_id: str, generation: int) -> str:
    return "fc-" + digest_json({"operation_id": operation_id, "generation": generation})[:40]


def dispatch_claim_id(operation_id: str, generation: int, effect_key: str) -> str:
    return "dc-" + digest_json(
        {"operation_id": operation_id, "generation": generation, "effect_key": effect_key}
    )[:40]


def event_path(operation_id: str, sequence: int, event_id: str) -> str:
    return f"{STORE_ROOT}/operations/{operation_id}/events/{sequence:08d}-{event_id}.json"


def projection_path(operation_id: str) -> str:
    return f"{STORE_ROOT}/projections/{operation_id}.json"


def reservation_path(effect_key: str) -> str:
    return f"{STORE_ROOT}/reservations/external/{effect_key}.json"


def dispatch_claim_path(claim_id: str) -> str:
    return f"{STORE_ROOT}/claims/dispatch/{claim_id}.json"


def feature_claim_path(target_repository: str, feature_id: str, claim_id: str) -> str:
    repo_hash = hashlib.sha256(normalize_repository(target_repository).encode()).hexdigest()[:24]
    return f"{STORE_ROOT}/claims/feature/{repo_hash}/{feature_id}/{claim_id}.json"


def decision_path(decision_id: str) -> str:
    path = f"{STORE_ROOT}/decisions/{decision_id}.json"
    if not _DECISION_RE.fullmatch(path):
        raise StoreInvariantError("invalid Decision id/path")
    return path


def notification_path(notification_id: str) -> str:
    path = f"{STORE_ROOT}/notifications/{notification_id}.json"
    if not _NOTIFICATION_RE.fullmatch(path):
        raise StoreInvariantError("invalid Notification id/path")
    return path


def is_projection_path(path: str) -> bool:
    return (
        path.startswith(f"{STORE_ROOT}/projections/")
        or path.startswith(f"{STORE_ROOT}/effect-lineages/projections/")
    ) and path.endswith(".json")


def is_immutable_path(path: str) -> bool:
    base_immutable = path.startswith(
        (
            f"{STORE_ROOT}/operations/",
            f"{STORE_ROOT}/reservations/external/",
            f"{STORE_ROOT}/claims/dispatch/",
            f"{STORE_ROOT}/claims/feature/",
            f"{STORE_ROOT}/effect-lineages/anchors/",
            f"{STORE_ROOT}/effect-lineages/members/",
            f"{STORE_ROOT}/effect-lineages/proposals/",
            f"{STORE_ROOT}/effect-lineages/events/",
            f"{STORE_ROOT}/effect-lineages/resolutions/",
        )
    ) and path.endswith(".json") and not is_projection_path(path)
    return base_immutable or bool(_DECISION_RE.fullmatch(path)) or bool(_NOTIFICATION_RE.fullmatch(path))


def validate_store_path(path: str) -> None:
    if not path.startswith(STORE_ROOT + "/") or ".." in path.split("/"):
        raise StoreInvariantError("invalid Store path")


@dataclass(frozen=True)
class StoreSnapshot:
    ref_sha: str | None = None
    files: dict[str, Any] = field(default_factory=dict)

    def get(self, path: str) -> Any | None:
        return self.files.get(path)


@dataclass(frozen=True)
class StoreMutation:
    kind: str
    path: str
    value: Any


@dataclass(frozen=True)
class StoreMutationPlan:
    expected_ref_sha: str | None
    mutations: tuple[StoreMutation, ...]
    result: dict[str, Any]


def apply_plan_to_snapshot(
    snapshot: StoreSnapshot, plan: StoreMutationPlan, *, new_ref_sha: str | None = None
) -> StoreSnapshot:
    if plan.expected_ref_sha != snapshot.ref_sha:
        raise StoreInvariantError("plan expected ref does not match snapshot")
    files = dict(snapshot.files)
    for mutation in plan.mutations:
        validate_store_path(mutation.path)
        if mutation.kind == "create_immutable":
            if not is_immutable_path(mutation.path):
                raise StoreInvariantError("create_immutable used for non-immutable path")
            if mutation.path in files:
                if canonical_json(files[mutation.path]) != canonical_json(mutation.value):
                    raise StoreInvariantError("immutable store artifact conflict")
                continue
            files[mutation.path] = mutation.value
        elif mutation.kind == "replace_projection":
            if not is_projection_path(mutation.path):
                raise StoreInvariantError("only projection cache may be replaced")
            files[mutation.path] = mutation.value
        else:
            raise StoreInvariantError(f"unsupported store mutation kind: {mutation.kind}")
    return StoreSnapshot(
        ref_sha=new_ref_sha if new_ref_sha is not None else snapshot.ref_sha,
        files=files,
    )


def operation_events(snapshot: StoreSnapshot, operation_id: str) -> list[dict[str, Any]]:
    rows = []
    for path, value in snapshot.files.items():
        match = _EVENT_RE.match(path)
        if match and match.group(1) == operation_id:
            rows.append((int(match.group(2)), match.group(3), value))
    rows.sort(key=lambda row: row[0])
    for expected, (sequence, event_id, event) in enumerate(rows, start=1):
        if (
            sequence != expected
            or not isinstance(event, dict)
            or event.get("schema_version") != EVENT_SCHEMA_VERSION
            or event.get("operation_id") != operation_id
            or event.get("sequence") != sequence
            or event.get("event_id") != event_id
        ):
            raise StoreInvariantError("operation event binding/schema mismatch")
    return [row[2] for row in rows]


def operation_ids(snapshot: StoreSnapshot) -> tuple[str, ...]:
    return tuple(sorted({match.group(1) for path in snapshot.files if (match := _EVENT_RE.match(path))}))


def next_sequence(snapshot: StoreSnapshot, operation_id: str) -> int:
    return len(operation_events(snapshot, operation_id)) + 1


def make_event(
    *,
    operation_id: str,
    generation: int,
    sequence: int,
    event_id: str,
    event_type: str,
    occurred_at: str,
    payload: dict[str, Any] | None = None,
    trusted_context_digest: str = "trusted",
) -> dict[str, Any]:
    if generation < 0 or sequence < 1 or not event_id or not event_type:
        raise StoreInvariantError("invalid operation event")
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "operation_id": operation_id,
        "operation_generation": generation,
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "trusted_context_digest": trusted_context_digest,
        "payload": dict(payload or {}),
    }


def rebuild_projection(snapshot: StoreSnapshot, operation_id: str) -> dict[str, Any]:
    events = operation_events(snapshot, operation_id)
    if not events:
        raise StoreInvariantError("operation not found")

    status = None
    generation = 0
    operation_profile = None
    target_repository = None
    feature_id = None
    expected_revision = None
    authorized_dispatches: set[str] = set()
    unresolved_unknown: set[str] = set()
    lineage_blocks: set[str] = set()
    requested_persists: set[str] = set()
    linearized_persists: set[str] = set()
    confirmed_persists: set[str] = set()
    superseded: set[int] = set()
    callback_ids: dict[str, str] = {}
    pending_decisions: set[str] = set()
    resolved_decisions: set[str] = set()
    expired_decisions: set[str] = set()
    unread_notifications: set[str] = set()

    for event in events:
        event_generation = int(event["operation_generation"])
        event_type = event["event_type"]
        payload = event.get("payload") or {}
        if event_type == "operation.started":
            if status is not None:
                raise StoreInvariantError("duplicate operation.started")
            generation = event_generation
            status = "RUNNING"
            operation_profile = payload.get("operation_profile")
            target_repository = payload.get("target_repository")
            feature_id = payload.get("feature_id")
            expected_revision = payload.get("expected_revision")
            continue
        if event_type == "operation.superseded":
            superseded.add(event_generation)
            continue
        if event_type == "operation.generation.started":
            if event_generation <= generation:
                raise StoreInvariantError("generation must increase")
            generation = event_generation
            status = "BLOCKED" if (unresolved_unknown or lineage_blocks) else "RUNNING"
            continue
        if event_generation < generation or event_generation in superseded:
            raise StoreInvariantError("superseded generation attempted new operation fact")
        if event_generation > generation:
            raise StoreInvariantError("event generation lacks generation-start fact")
        if status == "CANCELLED" and event_type not in {
            "dispatch.launch.lookup-recorded",
            "worker.callback.recorded",
            "persist.confirmed",
            "notification.created",
            "notification.acknowledged",
        }:
            raise StoreInvariantError("new decision fact after cancellation")

        if event_type == "dispatch.launch.authorized":
            if status in {"CANCELLED", "BLOCKED", "NEEDS_USER"}:
                raise StoreInvariantError("launch authorization after cancellation/stable stop")
            authorized_dispatches.add(str(payload["external_dispatch_key"]))
            status = "WAITING_EXTERNAL"
        elif event_type == "dispatch.launch.lookup-recorded":
            key = str(payload["external_dispatch_key"])
            lookup_state = payload["lookup_state"]
            if key not in authorized_dispatches:
                raise StoreInvariantError("launch lookup lacks authorized dispatch binding")
            if lookup_state == "UNKNOWN":
                unresolved_unknown.add(key)
                status = "BLOCKED"
            elif lookup_state == "LAUNCHED":
                unresolved_unknown.discard(key)
                if status != "CANCELLED":
                    status = "BLOCKED" if lineage_blocks else "WAITING_EXTERNAL"
            elif lookup_state == "NOT_LAUNCHED":
                unresolved_unknown.discard(key)
                if status != "CANCELLED":
                    status = "BLOCKED" if lineage_blocks else "RUNNING"
            else:
                raise StoreInvariantError("invalid launch lookup state")
        elif event_type == "worker.callback.recorded":
            key = str(payload["external_dispatch_key"])
            if key not in authorized_dispatches:
                raise StoreInvariantError("callback lacks authorized dispatch binding")
            callback_id = str(payload["callback_id"])
            callback_digest = str(payload["callback_digest"])
            if callback_id in callback_ids and callback_ids[callback_id] != callback_digest:
                raise StoreInvariantError("conflicting callback history")
            callback_ids[callback_id] = callback_digest
            if status not in TERMINAL_STATUSES and status != "NEEDS_USER" and not unresolved_unknown and not lineage_blocks:
                status = "RUNNING"
        elif event_type == "effect.lineage.blocked":
            lineage_id = str(payload.get("effect_lineage_id") or "")
            if not lineage_id:
                raise StoreInvariantError("lineage block lacks effect_lineage_id")
            lineage_blocks.add(lineage_id)
            status = "BLOCKED"
        elif event_type == "effect.lineage.resolved":
            lineage_id = str(payload.get("effect_lineage_id") or "")
            if not lineage_id or lineage_id not in lineage_blocks:
                raise StoreInvariantError("lineage resolution lacks current block")
            lineage_blocks.discard(lineage_id)
            external_key = payload.get("predecessor_external_dispatch_key")
            if external_key:
                unresolved_unknown.discard(str(external_key))
            status = "BLOCKED" if (lineage_blocks or unresolved_unknown) else "RUNNING"
        elif event_type == "decision.requested":
            decision_id = str(payload.get("decision_id") or "")
            if not decision_id or decision_id in resolved_decisions or decision_id in expired_decisions:
                raise StoreInvariantError("invalid Decision request history")
            pending_decisions.add(decision_id)
            status = "NEEDS_USER"
        elif event_type == "decision.responded":
            decision_id = str(payload.get("decision_id") or "")
            if decision_id not in pending_decisions:
                raise StoreInvariantError("Decision response lacks pending Decision")
            pending_decisions.remove(decision_id)
            resolved_decisions.add(decision_id)
            if status != "CANCELLED":
                status = "NEEDS_USER" if pending_decisions else "BLOCKED" if (lineage_blocks or unresolved_unknown) else "RUNNING"
        elif event_type == "decision.expired":
            decision_id = str(payload.get("decision_id") or "")
            if decision_id not in pending_decisions:
                raise StoreInvariantError("Decision expiry lacks pending Decision")
            pending_decisions.remove(decision_id)
            expired_decisions.add(decision_id)
            if status != "CANCELLED":
                status = "NEEDS_USER" if pending_decisions else "BLOCKED"
        elif event_type == "decision.superseded":
            decision_id = str(payload.get("decision_id") or "")
            if decision_id not in pending_decisions:
                raise StoreInvariantError("Decision supersession lacks pending Decision")
            pending_decisions.remove(decision_id)
            if status != "CANCELLED":
                status = "NEEDS_USER" if pending_decisions else "BLOCKED" if (lineage_blocks or unresolved_unknown) else "RUNNING"
        elif event_type == "decision.authorization-consumed":
            decision_id = str(payload.get("decision_id") or "")
            if decision_id not in resolved_decisions:
                raise StoreInvariantError("Decision authorization consumption lacks resolved Decision")
        elif event_type == "notification.created":
            notification_id = str(payload.get("notification_id") or "")
            if not notification_id:
                raise StoreInvariantError("Notification creation lacks id")
            unread_notifications.add(notification_id)
        elif event_type == "notification.acknowledged":
            notification_id = str(payload.get("notification_id") or "")
            if not notification_id:
                raise StoreInvariantError("Notification acknowledgement lacks id")
            unread_notifications.discard(notification_id)
        elif event_type == "operation.blocked":
            status = "BLOCKED"
        elif event_type == "operation.needs-user":
            status = "NEEDS_USER"
        elif event_type == "operation.cancelled":
            status = "CANCELLED"
        elif event_type == "operation.done":
            status = "DONE"
        elif event_type == "persist.requested":
            if status in {"BLOCKED", "NEEDS_USER"}:
                raise StoreInvariantError("persist request while stopped")
            requested_persists.add(str(payload["feature_event_id"]))
        elif event_type == "persist.linearized":
            feature_event_id = str(payload["feature_event_id"])
            if status in {"BLOCKED", "NEEDS_USER"}:
                raise StoreInvariantError("persist linearization while stopped")
            if feature_event_id not in requested_persists:
                raise StoreInvariantError("persist linearization lacks request")
            linearized_persists.add(feature_event_id)
        elif event_type == "persist.confirmed":
            feature_event_id = str(payload["feature_event_id"])
            if feature_event_id not in linearized_persists:
                raise StoreInvariantError("persist confirmation lacks linearization")
            confirmed_persists.add(feature_event_id)
        elif event_type == "dispatch.claimed":
            if status in {"BLOCKED", "NEEDS_USER"}:
                raise StoreInvariantError("dispatch claim while stopped")
        elif event_type == "loop.stable-stop":
            stable_status = str(payload.get("status", status))
            if stable_status not in {"WAITING_EXTERNAL", "BLOCKED", "NEEDS_USER", "DONE", "CANCELLED"}:
                raise StoreInvariantError("invalid loop stable-stop status")
            status = stable_status
        elif event_type in {
            "loop.step.selected",
            "worker.result.validated",
            "worker.result.rejected",
            "feature.event.translated",
        }:
            pass
        else:
            raise StoreInvariantError(f"unsupported operation event type: {event_type}")

    if status not in VALID_STATUSES:
        raise StoreInvariantError("operation projection has invalid status")
    return {
        "operation_id": operation_id,
        "generation": generation,
        "status": status,
        "operation_profile": operation_profile,
        "target_repository": target_repository,
        "feature_id": feature_id,
        "expected_feature_revision": expected_revision,
        "last_sequence": len(events),
        "journal_digest": digest_json(events),
        "authorized_dispatches": sorted(authorized_dispatches),
        "unresolved_unknown": sorted(unresolved_unknown),
        "lineage_blocks": sorted(lineage_blocks),
        "requested_persists": sorted(requested_persists),
        "linearized_persists": sorted(linearized_persists),
        "confirmed_persists": sorted(confirmed_persists),
        "pending_decisions": sorted(pending_decisions),
        "resolved_decisions": sorted(resolved_decisions),
        "expired_decisions": sorted(expired_decisions),
        "unread_notifications": sorted(unread_notifications),
    }


def projection_public(projection: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": projection["operation_id"],
        "generation": projection["generation"],
        "status": projection["status"],
    }


def unfinished_operations(
    snapshot: StoreSnapshot,
    *,
    target_repository: str | None = None,
    feature_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    normalized = normalize_repository(target_repository) if target_repository else None
    for operation_id in operation_ids(snapshot):
        projection = rebuild_projection(snapshot, operation_id)
        if projection["status"] in TERMINAL_STATUSES:
            continue
        if normalized and normalize_repository(str(projection["target_repository"])) != normalized:
            continue
        if feature_id and projection["feature_id"] != feature_id:
            continue
        rows.append(projection)
    rows.sort(key=lambda row: row["operation_id"])
    return rows


def immutable_object(snapshot: StoreSnapshot, path: str) -> dict[str, Any] | None:
    value = snapshot.get(path)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StoreInvariantError("immutable store artifact must be object")
    return value


def ensure_exact_or_absent(snapshot: StoreSnapshot, path: str, value: dict[str, Any]) -> bool:
    existing = immutable_object(snapshot, path)
    if existing is None:
        return False
    if canonical_json(existing) != canonical_json(value):
        raise StoreInvariantError("immutable store artifact identity conflict")
    return True
