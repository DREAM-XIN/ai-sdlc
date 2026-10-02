#!/usr/bin/env python3
"""Closed independent fixture pool for the three v0.3 real release dogfoods."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bootstrap_feature import build_manifest
from ingest_feature_event import ingest
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import select_vertical_action
from validate_feature_manifest import validate_manifest

DOGFOOD_PROFILE_ID = "v03-release-dogfood"
DOGFOOD_PROFILE = {
    "id": DOGFOOD_PROFILE_ID,
    "version": "0.1.0",
    "risk_profile": "low",
    "stages": [
        {"id": "implementation", "role": "developer"},
        {"id": "code-review", "role": "reviewer", "depends_on": ["implementation"], "gate": "code-gate"},
        {"id": "verification", "role": "qa", "depends_on": ["code-review"], "gate": "verification-gate"},
        {"id": "acceptance", "role": "product", "depends_on": ["verification"], "gate": "release-gate"},
    ],
}
DOGFOOD_ISSUE = 342
TASK_ARTIFACT_ID = "dogfood-scenario-task"


@dataclass(frozen=True)
class DogfoodSlot:
    scenario: str
    feature_id: str
    target_ref: str
    created_at: str
    activated_at: str

    @property
    def manifest_path(self) -> str:
        return f"state/features/{self.feature_id}.yaml"

    @property
    def task_path(self) -> str:
        return f"docs/features/{self.feature_id}/dogfood-task.md"

    @property
    def event_id(self) -> str:
        return f"EVT-{self.feature_id}-IMPLEMENTATION-START"

    @property
    def event_path(self) -> str:
        return f"state/events/{self.feature_id}/{self.event_id}.yaml"


SLOTS = (
    DogfoodSlot("happy_path", "F-OPERATOR-V03-DOGFOOD-HAPPY-0001", "dogfood/v0.3-happy-path-0001", "2026-08-25T07:20:01Z", "2026-08-25T07:21:01Z"),
    DogfoodSlot("review_remediation", "F-OPERATOR-V03-DOGFOOD-REMEDIATION-0001", "dogfood/v0.3-review-remediation-0001", "2026-08-25T07:20:02Z", "2026-08-25T07:21:02Z"),
    DogfoodSlot("session_recovery", "F-OPERATOR-V03-DOGFOOD-SESSION-0001", "dogfood/v0.3-session-recovery-0001", "2026-08-25T07:20:03Z", "2026-08-25T07:21:03Z"),
)
_BY_SCENARIO = {slot.scenario: slot for slot in SLOTS}


class DogfoodFixtureError(RuntimeError):
    pass


def validate_inventory() -> None:
    if tuple(slot.scenario for slot in SLOTS) != ("happy_path", "review_remediation", "session_recovery"):
        raise DogfoodFixtureError("dogfood fixture scenario inventory drifted")
    if len({slot.feature_id for slot in SLOTS}) != 3 or len({slot.target_ref for slot in SLOTS}) != 3:
        raise DogfoodFixtureError("dogfood fixture identities are not unique")
    if DOGFOOD_PROFILE["stages"] != [
        {"id": "implementation", "role": "developer"},
        {"id": "code-review", "role": "reviewer", "depends_on": ["implementation"], "gate": "code-gate"},
        {"id": "verification", "role": "qa", "depends_on": ["code-review"], "gate": "verification-gate"},
        {"id": "acceptance", "role": "product", "depends_on": ["verification"], "gate": "release-gate"},
    ]:
        raise DogfoodFixtureError("dogfood fixture profile drifted")


def require_slot(scenario: str) -> DogfoodSlot:
    validate_inventory()
    try:
        return _BY_SCENARIO[scenario]
    except KeyError as exc:
        raise DogfoodFixtureError("unknown v0.3 dogfood fixture scenario") from exc


def _bootstrap_input(slot: DogfoodSlot) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "feature": {
            "id": slot.feature_id,
            "title": f"v0.3 release dogfood: {slot.scenario}",
            "risk": "low",
            "issue": f"#{DOGFOOD_ISSUE}",
        },
        "profile": DOGFOOD_PROFILE_ID,
        "created_at": slot.created_at,
    }


def build_bootstrap_manifest(slot: DogfoodSlot) -> dict[str, Any]:
    result = build_manifest(_bootstrap_input(slot), DOGFOOD_PROFILE)
    if result.get("outcome") != "BOOTSTRAPPED":
        raise DogfoodFixtureError(f"dogfood fixture bootstrap failed: {slot.scenario}")
    manifest = result["manifest"]
    errors = validate_manifest(manifest)
    if errors:
        raise DogfoodFixtureError("dogfood fixture Manifest invalid: " + "; ".join(errors))
    stages = {row["id"]: row["status"] for row in manifest["workflow"]["stages"]}
    if (
        manifest["revision"] != 0
        or manifest["workflow"]["current_stage"] != "implementation"
        or stages != {"implementation": "READY", "code-review": "TODO", "verification": "TODO", "acceptance": "TODO"}
        or manifest["artifacts"] or manifest["evidence"] or manifest["tasks"] or manifest["applied_events"]
    ):
        raise DogfoodFixtureError("dogfood fixture did not bootstrap to exact implementation READY state")
    return manifest


def activation_event(slot: DogfoodSlot) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "id": slot.event_id,
        "feature_id": slot.feature_id,
        "expected_revision": 0,
        "occurred_at": slot.activated_at,
        "changes": [
            {
                "kind": "artifact-record",
                "record": {
                    "id": TASK_ARTIFACT_ID,
                    "type": "dogfood-task",
                    "uri": slot.task_path,
                    "status": "draft",
                },
            },
            {"kind": "stage", "id": "implementation", "status": "WORKING"},
        ],
    }


def build_active_manifest(slot: DogfoodSlot, *, repository: str) -> dict[str, Any]:
    planned = ingest(
        build_bootstrap_manifest(slot),
        activation_event(slot),
        event_path=slot.event_path,
        repository=repository,
        manifest_path=slot.manifest_path,
        target_ref=slot.target_ref,
        issue=DOGFOOD_ISSUE,
    )
    if planned.get("outcome") != "PLANNED":
        raise DogfoodFixtureError(f"dogfood fixture activation was not PLANNED: {slot.scenario}: {planned}")
    manifest = yaml.safe_load(planned["plan"]["manifest"]["content"])
    errors = validate_manifest(manifest)
    if errors:
        raise DogfoodFixtureError("active dogfood fixture Manifest invalid: " + "; ".join(errors))
    stages = {row["id"]: row["status"] for row in manifest["workflow"]["stages"]}
    expected_artifact = [{
        "id": TASK_ARTIFACT_ID,
        "type": "dogfood-task",
        "uri": slot.task_path,
        "status": "draft",
    }]
    if (
        manifest["revision"] != 1
        or manifest["workflow"]["status"] != "ACTIVE"
        or manifest["workflow"]["current_stage"] != "implementation"
        or stages != {"implementation": "WORKING", "code-review": "TODO", "verification": "TODO", "acceptance": "TODO"}
        or manifest["applied_events"] != [slot.event_id]
        or manifest["artifacts"] != expected_artifact
        or manifest["evidence"] or manifest["tasks"]
    ):
        raise DogfoodFixtureError("dogfood fixture activation did not converge to Developer WORKING with registered scenario task")
    return manifest


def task_text(slot: DogfoodSlot) -> str:
    instruction = {
        "happy_path": (
            "Create one minimal documentation-only implementation candidate under this Feature. "
            "The candidate must contain `dogfood_result: happy-path` and no unrelated changes. "
            "Independent Reviewer and QA should PASS only if that exact contract is satisfied."
        ),
        "review_remediation": (
            "This scenario intentionally requires a real remediation round trip. On the initial Developer pass, "
            "create a minimal documentation-only candidate containing exactly `dogfood_review_state: initial-needs-remediation` "
            "and do not claim it is final. The independent Reviewer must treat that marker as a MAJOR REWORK because "
            "the accepted final state is `dogfood_review_state: remediated`. On the remediation Developer pass, replace "
            "the initial marker with the final marker and make no unrelated changes; independent re-review and QA may then PASS."
        ),
        "session_recovery": (
            "Create one minimal documentation-only candidate containing `dogfood_session_choice: PENDING_USER`. "
            "The release controller intentionally ends its original client session after the first durable external stop. "
            "A fresh session then requests the protected `NEEDS_AUTHORIZATION` Decision for this explicit choice and must "
            "rediscover the same Operation plus its pending Decision/Notification through the production Responses read surface, "
            "without replaying operation.start; the frozen scenario must finish with the same durable Operation at `NEEDS_USER`."
        ),
    }[slot.scenario]
    return f"""# v0.3 real release dogfood fixture — {slot.scenario}

Feature: `{slot.feature_id}`  
Fixed ref: `{slot.target_ref}`
Scenario task artifact: `{TASK_ARTIFACT_ID}`

{instruction}

This release-only slot is independent from all Issue #221 fault-injection fixtures. It must not
be reset, force-pushed, recycled, or merged as a product change. Worker/model output is evidence
only; lifecycle authority remains the protected Operator Store plus canonical Feature Persist.
Product Acceptance is not performed by this fixture; the Feature may become `acceptance: READY`
while the dogfood Operation itself reaches its reviewed terminal status.
"""


def validate_active(slot: DogfoodSlot, manifest: dict[str, Any], *, repository: str, candidate_head: str) -> None:
    if manifest != build_active_manifest(slot, repository=repository):
        raise DogfoodFixtureError("active dogfood fixture Manifest content drifted")
    feature = FeatureSnapshot.from_manifest(
        repository=repository,
        target_ref=slot.target_ref,
        manifest=manifest,
        candidate_pr_number=999999,
        candidate_head_sha=candidate_head,
    )
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at="2026-08-25T07:22:00Z")
    if action.kind != "dispatch" or action.step != "IMPLEMENTATION_WORK" or action.role != "developer" or action.candidate_head_sha != candidate_head:
        raise DogfoodFixtureError("active dogfood fixture is not exact-head Developer-dispatch ready")


def materialize_bootstrap(slot: DogfoodSlot, *, repo_dir: Path) -> tuple[str, ...]:
    root = repo_dir.resolve()
    manifest_path, task_path = root / slot.manifest_path, root / slot.task_path
    if manifest_path.exists() or task_path.exists():
        raise DogfoodFixtureError("dogfood fixture bootstrap paths already exist")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(yaml.safe_dump(build_bootstrap_manifest(slot), sort_keys=False), encoding="utf-8")
    task_path.write_text(task_text(slot), encoding="utf-8")
    return tuple(sorted((slot.manifest_path, slot.task_path)))


def materialize_activation(slot: DogfoodSlot, *, repo_dir: Path, repository: str) -> tuple[str, ...]:
    root = repo_dir.resolve()
    manifest_path, task_path, event_path = root / slot.manifest_path, root / slot.task_path, root / slot.event_path
    if not manifest_path.is_file() or not task_path.is_file() or event_path.exists():
        raise DogfoodFixtureError("dogfood fixture activation requires exact bootstrap file set")
    if yaml.safe_load(manifest_path.read_text(encoding="utf-8")) != build_bootstrap_manifest(slot):
        raise DogfoodFixtureError("dogfood bootstrap Manifest drifted before activation")
    manifest_path.write_text(yaml.safe_dump(build_active_manifest(slot, repository=repository), sort_keys=False), encoding="utf-8")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(yaml.safe_dump(activation_event(slot), sort_keys=False), encoding="utf-8")
    return tuple(sorted((slot.manifest_path, slot.event_path)))


def verify_active_files(slot: DogfoodSlot, *, repo_dir: Path, repository: str, candidate_head: str) -> None:
    root = repo_dir.resolve()
    manifest_path, task_path, event_path = root / slot.manifest_path, root / slot.task_path, root / slot.event_path
    if not manifest_path.is_file() or not task_path.is_file() or not event_path.is_file():
        raise DogfoodFixtureError("dogfood fixture active file set is incomplete")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    validate_active(slot, manifest, repository=repository, candidate_head=candidate_head)
    if yaml.safe_load(event_path.read_text(encoding="utf-8")) != activation_event(slot):
        raise DogfoodFixtureError("dogfood fixture activation Event drifted")
    if task_path.read_text(encoding="utf-8") != task_text(slot):
        raise DogfoodFixtureError("dogfood fixture task content drifted")


def inventory_document() -> dict[str, Any]:
    validate_inventory()
    return {
        "schema_version": "ai-sdlc.v03-dogfood-fixture-pool/v1",
        "issue": DOGFOOD_ISSUE,
        "profile": DOGFOOD_PROFILE_ID,
        "release_eligible": False,
        "slots": [{
            "scenario": slot.scenario,
            "feature_id": slot.feature_id,
            "target_ref": slot.target_ref,
            "manifest_path": slot.manifest_path,
            "event_path": slot.event_path,
            "task_path": slot.task_path,
        } for slot in SLOTS],
    }
