#!/usr/bin/env python3
"""Retire one reviewed, failed pre-create dogfood Operation without erasing history."""
from __future__ import annotations

from operator_store import plan_cancel
from operator_store_model import operation_events, rebuild_projection
from operator_external_create_attempt import find_external_create_attempt
from operator_vertical import VERTICAL_PROFILE

OPERATION = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
SOURCE_RUN = 37085507909
SOURCE_HEAD = "d2c98bc0762ba49acab400dd77bfcd3e8a9ef1ef"
SOURCE_CONTEXT = "3d5095e119b05e2b8787b78ae3a6b10446ded90cc973db9afab6044e68e018e2"
FEATURE = "F-OPERATOR-V03-DOGFOOD-HAPPY-0001"
CANDIDATE = "70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23"
EFFECT = "80b31137a408f2b0ee85248bd069b972af80b9777f91f2bd00c9b27b42f9e804"
EXTERNAL_KEY = "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1"
REASON = "v03-reviewed-pre-create-retirement:37085507909"
EVENT_IDS = (
    "operation-started-739f4331137732d6184000cf3d8b4915",
    "loop-step-selected-2cf82f42399ec59c7283df1711819426",
    "dispatch-claimed-687520874a948a5c4534e4e30d97b366",
    "dispatch-launch-authorized-5f6d063282780be1d174254ce8ef9134",
    "dispatch-launch-lookup-recorded-8629bfae40264dc5d15df601bc67686d",
)
PAYLOADS = (
    {"expected_revision": 1, "feature_id": FEATURE, "operation_profile": VERTICAL_PROFILE,
     "target_repository": "dream-xin/ai-sdlc"},
    {"feature_revision": 1, "kind": "dispatch", "step": "IMPLEMENTATION_WORK",
     "task_identity": "vertical:implementation:1"},
    {"claim_id": "dc-44100fdf26b6028d24a39ba66482cbefcfcb5e61",
     "external_dispatch_key": EXTERNAL_KEY, "semantic_effect_key": EFFECT},
    {"candidate_head_sha": CANDIDATE, "claim_id": "dc-44100fdf26b6028d24a39ba66482cbefcfcb5e61",
     "dispatch_id": "vertical-7acafffd7380397d926664f8014832c5", "expected_revision": 1,
     "external_dispatch_key": EXTERNAL_KEY, "feature_id": FEATURE, "role": "developer",
     "semantic_effect_key": EFFECT, "stage": "implementation"},
    {"external_dispatch_key": EXTERNAL_KEY, "lookup_state": "NOT_LAUNCHED", "receipt_id": None},
)
EVENT_TYPES = ("operation.started", "loop.step.selected", "dispatch.claimed",
               "dispatch.launch.authorized", "dispatch.launch.lookup-recorded")


class DogfoodPreCreateRetirementError(RuntimeError):
    pass


def require_failed_source(run):
    if not isinstance(run, dict) or (
        run.get("id"), run.get("status"), run.get("conclusion"), run.get("event"),
        run.get("head_branch"), run.get("head_sha"), run.get("path"), run.get("run_attempt"),
        str((run.get("repository") or {}).get("full_name") or "").lower(),
    ) != (
        SOURCE_RUN, "completed", "failure", "workflow_dispatch", "main", SOURCE_HEAD,
        ".github/workflows/v03-real-dogfood-scenario.yml", 1, "dream-xin/ai-sdlc",
    ):
        raise DogfoodPreCreateRetirementError("reviewed source run is not exact completed failed authority")


def retirement_plan(snapshot, *, occurred_at, trusted_context_digest):
    """Recheck all fences inside the production CAS planner."""
    rows = operation_events(snapshot, OPERATION)
    if not rows:
        return None
    projection = rebuild_projection(snapshot, OPERATION)
    if (projection.get("target_repository"), projection.get("feature_id"),
        projection.get("operation_profile"), projection.get("generation"),
        projection.get("expected_feature_revision")) != (
        "dream-xin/ai-sdlc", FEATURE, VERTICAL_PROFILE, 0, 1,
    ):
        raise DogfoodPreCreateRetirementError("reviewed Operation target/generation drifted")
    retired = projection.get("status") == "CANCELLED"
    prefix = rows[:-1] if retired else rows
    if len(prefix) != 5 or any(
        (row.get("operation_id"), row.get("operation_generation"), row.get("sequence"),
         row.get("event_id"), row.get("event_type"), row.get("payload"),
         row.get("trusted_context_digest")) != (
             OPERATION, 0, index + 1, EVENT_IDS[index], EVENT_TYPES[index],
             PAYLOADS[index], SOURCE_CONTEXT,
         )
        for index, row in enumerate(prefix)
    ):
        raise DogfoodPreCreateRetirementError("reviewed pre-create history changed")
    if find_external_create_attempt(snapshot, external_dispatch_key=EXTERNAL_KEY) is not None:
        raise DogfoodPreCreateRetirementError("external-create attempt exists; retirement forbidden")
    # Reject malformed/misbound attempt objects as well as the canonical lookup.
    if any(isinstance(value, dict) and value.get("schema_version") == "ai-sdlc.external-create-attempt/v1"
           and (value.get("semantic_effect_key") == EFFECT
                or value.get("created_operation_id") == OPERATION)
           for value in snapshot.files.values()):
        raise DogfoodPreCreateRetirementError("reviewed Operation has external-create authority")
    if retired:
        last = rows[-1]
        if (last.get("event_type"), last.get("operation_generation"), last.get("sequence"),
            last.get("payload")) != ("operation.cancelled", 0, 6, {"reason": REASON}):
            raise DogfoodPreCreateRetirementError("Operation was retired outside the reviewed boundary")
        return None
    if projection.get("status") != "RUNNING" or trusted_context_digest == SOURCE_CONTEXT:
        raise DogfoodPreCreateRetirementError("retirement requires unfinished pre-create Operation and new authority")
    return plan_cancel(snapshot, operation_id=OPERATION, reason=REASON,
                       occurred_at=occurred_at, trusted_context_digest=trusted_context_digest)


def retire_reviewed_pre_create_operation(preflight):
    """Only RUN mode calls this; readiness and finalization remain read-only here."""
    if preflight.slot.scenario != "happy_path":
        return
    if (preflight.execution.repository.lower(), preflight.slot.feature_id,
        preflight.slot.target_ref, preflight.candidate_head_sha) != (
        "dream-xin/ai-sdlc", FEATURE, "dogfood/v0.3-happy-path-0001", CANDIDATE,
    ):
        raise DogfoodPreCreateRetirementError("retirement escaped exact unchanged fixture candidate")
    runtime = preflight.composition.runtime
    snapshot = runtime.backend.read_snapshot()
    if not operation_events(snapshot, OPERATION):
        return
    source = preflight.composition.result_source
    run = source._json(source.config.control_repository, f"/actions/runs/{SOURCE_RUN}",
                       source.config.control_token)
    require_failed_source(run)
    if retirement_plan(snapshot, occurred_at=runtime.clock(),
                       trusted_context_digest=preflight.trusted_context_digest) is None:
        return

    def planner(current):
        plan = retirement_plan(current, occurred_at=runtime.clock(),
                               trusted_context_digest=preflight.trusted_context_digest)
        if plan is None:
            # A competing retirement must never silently authorize a second run.
            raise DogfoodPreCreateRetirementError("retirement changed during CAS planning")
        return plan

    runtime.commit_replanned(planner)
