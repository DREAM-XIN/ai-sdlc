#!/usr/bin/env python3
"""Canonical closed fixture-pool definitions for remaining v0.3 Issue #221 scenarios."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from bootstrap_feature import build_manifest
from ingest_feature_event import ingest
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import select_vertical_action
from provision_v03_real_runtime_fixture import FIXTURE_PROFILE as ORIGINAL_FIXTURE_PROFILE
from validate_feature_manifest import validate_manifest

POOL_PROFILE = "v03-real-runtime-scenario-fixture-pool/v1"
POOL_ISSUE = 310
BASE_CREATED_AT = "2026-08-18T05:10:00Z"


@dataclass(frozen=True)
class SlotSpec:
    scenario: str
    feature_id: str
    target_ref: str
    created_at: str
    activated_at: str

    @property
    def manifest_path(self) -> str:
        return f"state/features/{self.feature_id}.yaml"

    @property
    def event_id(self) -> str:
        return f"EVT-{self.feature_id}-CODE-REVIEW-START"

    @property
    def event_path(self) -> str:
        return f"state/events/{self.feature_id}/{self.event_id}.yaml"

    @property
    def implementation_path(self) -> str:
        return f"docs/features/{self.feature_id}/implementation.md"


SLOTS = (
    SlotSpec("cancel-before-persist-linearization", "F-OPERATOR-V03-FI-CANCEL-BEFORE-PERSIST-0001", "verification/v0.3-fi-cancel-before-persist-221", "2026-08-18T05:10:01Z", "2026-08-18T05:11:01Z"),
    SlotSpec("persist-linearized-before-cancel", "F-OPERATOR-V03-FI-PERSIST-BEFORE-CANCEL-0001", "verification/v0.3-fi-persist-before-cancel-221", "2026-08-18T05:10:02Z", "2026-08-18T05:11:02Z"),
    SlotSpec("unknown-takeover", "F-OPERATOR-V03-FI-UNKNOWN-TAKEOVER-0001", "verification/v0.3-fi-unknown-takeover-221", "2026-08-18T05:10:03Z", "2026-08-18T05:11:03Z"),
    SlotSpec("duplicate-callback", "F-OPERATOR-V03-FI-DUPLICATE-CALLBACK-0001", "verification/v0.3-fi-duplicate-callback-221", "2026-08-18T05:10:04Z", "2026-08-18T05:11:04Z"),
    SlotSpec("out-of-order-callback", "F-OPERATOR-V03-FI-OUT-OF-ORDER-CALLBACK-0001", "verification/v0.3-fi-out-of-order-callback-221", "2026-08-18T05:10:05Z", "2026-08-18T05:11:05Z"),
    SlotSpec("duplicate-worker-completion", "F-OPERATOR-V03-FI-DUPLICATE-WORKER-0001", "verification/v0.3-fi-duplicate-worker-221", "2026-08-18T05:10:06Z", "2026-08-18T05:11:06Z"),
    SlotSpec("concurrent-resume", "F-OPERATOR-V03-FI-CONCURRENT-RESUME-0001", "verification/v0.3-fi-concurrent-resume-221", "2026-08-18T05:10:07Z", "2026-08-18T05:11:07Z"),
    SlotSpec("stale-candidate-result", "F-OPERATOR-V03-FI-STALE-CANDIDATE-0001", "verification/v0.3-fi-stale-candidate-221", "2026-08-18T05:10:08Z", "2026-08-18T05:11:08Z"),
    SlotSpec("reservation-committed-pre-authorization-crash-recovery", "F-OPERATOR-V03-FI-PREAUTH-CRASH-0001", "verification/v0.3-fi-preauth-crash-221", "2026-08-18T05:10:09Z", "2026-08-18T05:11:09Z"),
)

EXPECTED_SCENARIOS = tuple(slot.scenario for slot in SLOTS)


def validate_inventory() -> None:
    if len(SLOTS) != 9:
        raise RuntimeError("v0.3 destructive scenario fixture pool must contain exactly nine slots")
    if len({slot.scenario for slot in SLOTS}) != len(SLOTS):
        raise RuntimeError("scenario fixture pool contains duplicate scenario ids")
    if len({slot.feature_id for slot in SLOTS}) != len(SLOTS):
        raise RuntimeError("scenario fixture pool contains duplicate Feature ids")
    if len({slot.target_ref for slot in SLOTS}) != len(SLOTS):
        raise RuntimeError("scenario fixture pool contains duplicate refs")
    if tuple(slot.scenario for slot in SLOTS) != (
        "cancel-before-persist-linearization",
        "persist-linearized-before-cancel",
        "unknown-takeover",
        "duplicate-callback",
        "out-of-order-callback",
        "duplicate-worker-completion",
        "concurrent-resume",
        "stale-candidate-result",
        "reservation-committed-pre-authorization-crash-recovery",
    ):
        raise RuntimeError("scenario fixture pool inventory differs from Issue #310 closed set")
    if ORIGINAL_FIXTURE_PROFILE != {
        "id": "v03-real-runtime-fixture",
        "version": "0.1.0",
        "risk_profile": "low",
        "stages": [
            {"id": "code-review", "role": "reviewer", "gate": "code-gate"},
            {"id": "verification", "role": "qa", "depends_on": ["code-review"], "gate": "verification-gate"},
            {"id": "acceptance", "role": "product", "depends_on": ["verification"], "gate": "release-gate"},
        ],
    }:
        raise RuntimeError("original #276 Code-Review-first fixture profile drifted")


def implementation_text(slot: SlotSpec) -> str:
    return f"""# v0.3 real-runtime scenario fixture: {slot.scenario}

This branch is one fixed release-only fixture slot for Issue #221 under prerequisite #310.

Fixed Feature: `{slot.feature_id}`  
Fixed ref: `{slot.target_ref}`

It intentionally contains no product implementation change. Provisioning registers this
file as the single draft implementation artifact and moves only `code-review` from
`READY` to `WORKING` using the same reviewed Code-Review-first profile as #276/#277.
It does not fabricate a Worker result, Gate verdict, Product Acceptance, dogfood, or
release evidence. Runtime lifecycle mutation remains protected Store + exact Feature
Event/Persist authority.

The slot is permanently bound to scenario `{slot.scenario}` for v0.3. It must not be
reset, recycled for another scenario, force-pushed, or merged as a product change.
"""


def _bootstrap_input(slot: SlotSpec) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "feature": {
            "id": slot.feature_id,
            "title": f"v0.3 real-runtime fixture: {slot.scenario}",
            "risk": "low",
            "issue": f"#{POOL_ISSUE}",
        },
        "profile": ORIGINAL_FIXTURE_PROFILE["id"],
        "created_at": slot.created_at,
    }


def build_bootstrap_manifest(slot: SlotSpec) -> dict[str, Any]:
    validate_inventory()
    result = build_manifest(_bootstrap_input(slot), ORIGINAL_FIXTURE_PROFILE)
    if result.get("outcome") != "BOOTSTRAPPED":
        raise RuntimeError(f"slot bootstrap failed: {slot.scenario}: {result}")
    manifest = result["manifest"]
    errors = validate_manifest(manifest)
    if errors:
        raise RuntimeError(f"slot bootstrap Manifest invalid: {slot.scenario}: {'; '.join(errors)}")
    stages = {row["id"]: row["status"] for row in manifest["workflow"]["stages"]}
    if (
        manifest["revision"] != 0
        or manifest["workflow"]["status"] != "ACTIVE"
        or manifest["workflow"]["current_stage"] != "code-review"
        or stages != {"code-review": "READY", "verification": "TODO", "acceptance": "TODO"}
        or manifest["artifacts"]
        or manifest["evidence"]
        or manifest["tasks"]
        or manifest["applied_events"]
    ):
        raise RuntimeError(f"slot bootstrap state drifted: {slot.scenario}")
    return manifest


def activation_event(slot: SlotSpec) -> dict[str, Any]:
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
                    "id": "implementation-v1",
                    "type": "implementation",
                    "uri": slot.implementation_path,
                    "status": "draft",
                },
            },
            {"kind": "stage", "id": "code-review", "status": "WORKING"},
        ],
    }


def validate_active_manifest(slot: SlotSpec, manifest: dict[str, Any], *, repository: str) -> None:
    errors = validate_manifest(manifest)
    if errors:
        raise RuntimeError(f"active slot Manifest invalid: {slot.scenario}: {'; '.join(errors)}")
    workflow = manifest["workflow"]
    stages = {row["id"]: row["status"] for row in workflow["stages"]}
    gates = {row["id"]: row["status"] for row in manifest["gates"]}
    if (
        manifest["revision"] != 1
        or workflow["status"] != "ACTIVE"
        or workflow["current_stage"] != "code-review"
        or stages != {"code-review": "WORKING", "verification": "TODO", "acceptance": "TODO"}
        or set(gates.values()) != {"PENDING"}
        or manifest["evidence"] != []
        or manifest["tasks"] != []
        or manifest["applied_events"] != [slot.event_id]
        or manifest["artifacts"] != [{
            "id": "implementation-v1",
            "type": "implementation",
            "uri": slot.implementation_path,
            "status": "draft",
        }]
    ):
        raise RuntimeError(f"active slot state drifted: {slot.scenario}")
    probe_head = "1" * 40
    feature = FeatureSnapshot.from_manifest(
        repository=repository,
        target_ref=slot.target_ref,
        manifest=manifest,
        candidate_pr_number=999999,
        candidate_head_sha=probe_head,
    )
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at="2026-08-18T05:20:00Z")
    if action.kind != "dispatch" or action.step != "CODE_REVIEW" or action.role != "reviewer" or action.candidate_head_sha != probe_head:
        raise RuntimeError(f"active slot is not exact-head Reviewer-dispatch ready: {slot.scenario}")
    if probe_head in yaml.safe_dump(manifest, sort_keys=False):
        raise RuntimeError(f"active slot embeds candidate head identity: {slot.scenario}")


def materialize_bootstrap(slot: SlotSpec, *, repo_dir: Path) -> dict[str, Any]:
    root = repo_dir.resolve()
    outputs = {
        slot.manifest_path: yaml.safe_dump(build_bootstrap_manifest(slot), sort_keys=False),
        slot.implementation_path: implementation_text(slot),
    }
    for relative, content in outputs.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError(f"slot path already exists: {slot.scenario}: {relative}")
        target.write_text(content, encoding="utf-8")
    return slot_plan(slot, phase="bootstrap", revision=0, stage_status="READY")


def materialize_activation(slot: SlotSpec, *, repo_dir: Path, repository: str) -> dict[str, Any]:
    root = repo_dir.resolve()
    manifest_file = root / slot.manifest_path
    implementation_file = root / slot.implementation_path
    event_file = root / slot.event_path
    if not manifest_file.is_file() or not implementation_file.is_file() or event_file.exists():
        raise RuntimeError(f"slot activation requires exact bootstrap file set: {slot.scenario}")
    bootstrap = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    if bootstrap != build_bootstrap_manifest(slot):
        raise RuntimeError(f"slot bootstrap content drifted before activation: {slot.scenario}")
    event = activation_event(slot)
    planned = ingest(
        bootstrap,
        event,
        event_path=slot.event_path,
        repository=repository,
        manifest_path=slot.manifest_path,
        target_ref=slot.target_ref,
        issue=POOL_ISSUE,
    )
    if planned.get("outcome") != "PLANNED":
        raise RuntimeError(f"slot activation Event not PLANNED: {slot.scenario}: {planned}")
    manifest = yaml.safe_load(planned["plan"]["manifest"]["content"])
    validate_active_manifest(slot, manifest, repository=repository)
    manifest_file.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_text(yaml.safe_dump(event, sort_keys=False), encoding="utf-8")
    return slot_plan(slot, phase="activation", revision=1, stage_status="WORKING")


def verify_bootstrap_files(slot: SlotSpec, *, repo_dir: Path) -> dict[str, Any]:
    root = repo_dir.resolve()
    manifest_file = root / slot.manifest_path
    implementation_file = root / slot.implementation_path
    event_file = root / slot.event_path
    if not manifest_file.is_file() or not implementation_file.is_file() or event_file.exists():
        raise RuntimeError(f"slot is not exact bootstrap file set: {slot.scenario}")
    if yaml.safe_load(manifest_file.read_text(encoding="utf-8")) != build_bootstrap_manifest(slot):
        raise RuntimeError(f"slot bootstrap Manifest drifted: {slot.scenario}")
    if implementation_file.read_text(encoding="utf-8") != implementation_text(slot):
        raise RuntimeError(f"slot implementation content drifted: {slot.scenario}")
    return slot_plan(slot, phase="verified-bootstrap", revision=0, stage_status="READY")


def verify_active_files(slot: SlotSpec, *, repo_dir: Path, repository: str) -> dict[str, Any]:
    root = repo_dir.resolve()
    manifest_file = root / slot.manifest_path
    implementation_file = root / slot.implementation_path
    event_file = root / slot.event_path
    if not manifest_file.is_file() or not implementation_file.is_file() or not event_file.is_file():
        raise RuntimeError(f"slot is not exact active file set: {slot.scenario}")
    if implementation_file.read_text(encoding="utf-8") != implementation_text(slot):
        raise RuntimeError(f"slot implementation content drifted: {slot.scenario}")
    manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    validate_active_manifest(slot, manifest, repository=repository)
    if yaml.safe_load(event_file.read_text(encoding="utf-8")) != activation_event(slot):
        raise RuntimeError(f"slot activation Event drifted: {slot.scenario}")
    return slot_plan(slot, phase="verified-active", revision=1, stage_status="WORKING")


def slot_plan(slot: SlotSpec, *, phase: str, revision: int, stage_status: str) -> dict[str, Any]:
    return {
        "schema_version": "ai-sdlc.v03-scenario-fixture-slot/v1",
        "pool_profile": POOL_PROFILE,
        "scenario": slot.scenario,
        "feature_id": slot.feature_id,
        "target_ref": slot.target_ref,
        "phase": phase,
        "manifest_revision": revision,
        "workflow_status": "ACTIVE",
        "current_stage": "code-review",
        "stage_status": stage_status,
        "release_eligible": False,
    }


def inventory_document() -> dict[str, Any]:
    validate_inventory()
    slots = [asdict(slot) | {
        "manifest_path": slot.manifest_path,
        "event_id": slot.event_id,
        "event_path": slot.event_path,
        "implementation_path": slot.implementation_path,
    } for slot in SLOTS]
    digest = hashlib.sha256(json.dumps(slots, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": "ai-sdlc.v03-scenario-fixture-pool-inventory/v1",
        "pool_profile": POOL_PROFILE,
        "issue": POOL_ISSUE,
        "slot_count": len(slots),
        "slots": slots,
        "inventory_digest": digest,
        "release_eligible": False,
    }


def main() -> None:
    # Deliberately no slot/Feature/ref selector CLI. The trusted provisioner imports
    # SLOTS and processes the entire closed inventory.
    validate_inventory()
    print(json.dumps(inventory_document(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
