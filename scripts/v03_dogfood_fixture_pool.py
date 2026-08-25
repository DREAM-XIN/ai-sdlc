#!/usr/bin/env python3
"""Closed release-only fixture definitions for the three v0.3 dogfood scenarios."""
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
from validate_feature_manifest import validate_manifest

POOL_PROFILE = "v03-dogfood-fixture-pool/v1"
POOL_ISSUE = 345
PROFILE = {
    "id": "v03-dogfood-fixture",
    "version": "0.1.0",
    "risk_profile": "low",
    "stages": [
        {"id": "implementation", "role": "developer"},
        {
            "id": "code-review",
            "role": "reviewer",
            "depends_on": ["implementation"],
            "gate": "code-gate",
        },
        {
            "id": "verification",
            "role": "qa",
            "depends_on": ["code-review"],
            "gate": "verification-gate",
        },
        {
            "id": "acceptance",
            "role": "product",
            "depends_on": ["verification"],
            "gate": "release-gate",
        },
    ],
}


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
        return f"EVT-{self.feature_id}-IMPLEMENTATION-START"

    @property
    def event_path(self) -> str:
        return f"state/events/{self.feature_id}/{self.event_id}.yaml"

    @property
    def implementation_path(self) -> str:
        return f"docs/features/{self.feature_id}/implementation.md"


SLOTS = (
    SlotSpec(
        "happy_path",
        "F-OPERATOR-V03-DOGFOOD-HAPPY-0001",
        "verification/v0.3-dogfood-happy-239",
        "2026-08-25T06:30:01Z",
        "2026-08-25T06:31:01Z",
    ),
    SlotSpec(
        "review_remediation",
        "F-OPERATOR-V03-DOGFOOD-REMEDIATION-0001",
        "verification/v0.3-dogfood-remediation-239",
        "2026-08-25T06:30:02Z",
        "2026-08-25T06:31:02Z",
    ),
    SlotSpec(
        "session_recovery",
        "F-OPERATOR-V03-DOGFOOD-SESSION-0001",
        "verification/v0.3-dogfood-session-239",
        "2026-08-25T06:30:03Z",
        "2026-08-25T06:31:03Z",
    ),
)

EXPECTED_SCENARIOS = ("happy_path", "review_remediation", "session_recovery")


def validate_inventory() -> None:
    if tuple(slot.scenario for slot in SLOTS) != EXPECTED_SCENARIOS:
        raise RuntimeError("dogfood fixture pool differs from the frozen three release scenarios")
    if len({slot.feature_id for slot in SLOTS}) != 3:
        raise RuntimeError("dogfood fixture pool contains duplicate Feature ids")
    if len({slot.target_ref for slot in SLOTS}) != 3:
        raise RuntimeError("dogfood fixture pool contains duplicate target refs")
    if any(not slot.target_ref.startswith("verification/v0.3-dogfood-") for slot in SLOTS):
        raise RuntimeError("dogfood fixture refs escaped the fixed verification namespace")
    # Explicitly prove no destructive #221 fixture identity can be reused.
    if any("-FI-" in slot.feature_id or "fi-" in slot.target_ref for slot in SLOTS):
        raise RuntimeError("dogfood fixture pool overlaps Issue #221 fault-injection identity")


def implementation_text(slot: SlotSpec) -> str:
    return f"""# v0.3 release dogfood fixture: {slot.scenario}

This branch is one fixed release-only fixture slot for Issue #239 / prerequisite #345.

Fixed Feature: `{slot.feature_id}`  
Fixed ref: `{slot.target_ref}`

It intentionally contains no product implementation change. Trusted provisioning registers
this file as the single draft implementation artifact and moves only `implementation` from
`READY` to `WORKING`. The release-only profile then uses the same production role/gate
suffix as `standard-feature`: Developer -> independent Reviewer -> QA -> Product.

Provisioning performs no model call, Worker dispatch, Operation start, protected Store
mutation, Gate verdict, Product Acceptance, dogfood evidence, or release evidence. Runtime
lifecycle mutation remains protected Store + exact Feature Event/Persist authority.

This slot is permanently bound to scenario `{slot.scenario}`. It must not be reset, reused
for another scenario, force-pushed, or counted as release evidence before a trusted real run
produces a provenance-verified release-run record.
"""


def _bootstrap_input(slot: SlotSpec) -> dict[str, Any]:
    return {
        "version": "0.1.0",
        "feature": {
            "id": slot.feature_id,
            "title": f"v0.3 release dogfood fixture: {slot.scenario}",
            "risk": "low",
            "issue": f"#{POOL_ISSUE}",
        },
        "profile": PROFILE["id"],
        "created_at": slot.created_at,
    }


def build_bootstrap_manifest(slot: SlotSpec) -> dict[str, Any]:
    validate_inventory()
    result = build_manifest(_bootstrap_input(slot), PROFILE)
    if result.get("outcome") != "BOOTSTRAPPED":
        raise RuntimeError(f"dogfood slot bootstrap failed: {slot.scenario}: {result}")
    manifest = result["manifest"]
    errors = validate_manifest(manifest)
    if errors:
        raise RuntimeError(
            f"dogfood slot bootstrap Manifest invalid: {slot.scenario}: {'; '.join(errors)}"
        )
    stages = {row["id"]: row["status"] for row in manifest["workflow"]["stages"]}
    gates = {row["id"]: row["status"] for row in manifest["gates"]}
    if (
        manifest["revision"] != 0
        or manifest["workflow"]["status"] != "ACTIVE"
        or manifest["workflow"]["current_stage"] != "implementation"
        or stages
        != {
            "implementation": "READY",
            "code-review": "TODO",
            "verification": "TODO",
            "acceptance": "TODO",
        }
        or gates
        != {
            "code-gate": "PENDING",
            "verification-gate": "PENDING",
            "release-gate": "PENDING",
        }
        or manifest["artifacts"]
        or manifest["evidence"]
        or manifest["tasks"]
        or manifest["applied_events"]
    ):
        raise RuntimeError(f"dogfood slot bootstrap state drifted: {slot.scenario}")
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
            {"kind": "stage", "id": "implementation", "status": "WORKING"},
        ],
    }


def validate_active_manifest(
    slot: SlotSpec,
    manifest: dict[str, Any],
    *,
    repository: str,
) -> None:
    errors = validate_manifest(manifest)
    if errors:
        raise RuntimeError(
            f"active dogfood Manifest invalid: {slot.scenario}: {'; '.join(errors)}"
        )
    workflow = manifest["workflow"]
    stages = {row["id"]: row["status"] for row in workflow["stages"]}
    gates = {row["id"]: row["status"] for row in manifest["gates"]}
    expected_artifact = {
        "id": "implementation-v1",
        "type": "implementation",
        "uri": slot.implementation_path,
        "status": "draft",
    }
    if (
        manifest["revision"] != 1
        or workflow["status"] != "ACTIVE"
        or workflow["current_stage"] != "implementation"
        or stages
        != {
            "implementation": "WORKING",
            "code-review": "TODO",
            "verification": "TODO",
            "acceptance": "TODO",
        }
        or gates
        != {
            "code-gate": "PENDING",
            "verification-gate": "PENDING",
            "release-gate": "PENDING",
        }
        or manifest["evidence"] != []
        or manifest["tasks"] != []
        or manifest["artifacts"] != [expected_artifact]
        or manifest["applied_events"] != [slot.event_id]
    ):
        raise RuntimeError(f"active dogfood slot state drifted: {slot.scenario}")

    probe_head = "2" * 40
    feature = FeatureSnapshot.from_manifest(
        repository=repository,
        target_ref=slot.target_ref,
        manifest=manifest,
        candidate_pr_number=999998,
        candidate_head_sha=probe_head,
    )
    action = select_vertical_action(
        feature=feature,
        manifest=manifest,
        occurred_at="2026-08-25T06:35:00Z",
    )
    if (
        action.kind != "dispatch"
        or action.step != "IMPLEMENTATION_WORK"
        or action.role != "developer"
        or action.candidate_head_sha != probe_head
    ):
        raise RuntimeError(
            f"active dogfood slot is not exact Developer-dispatch ready: {slot.scenario}"
        )
    dumped = yaml.safe_dump(manifest, sort_keys=False)
    if probe_head in dumped or "999998" in dumped:
        raise RuntimeError(f"active dogfood slot embeds candidate authority: {slot.scenario}")


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
            raise RuntimeError(f"dogfood slot path already exists: {slot.scenario}: {relative}")
        target.write_text(content, encoding="utf-8")
    return slot_plan(slot, phase="bootstrap", revision=0, stage_status="READY")


def materialize_activation(
    slot: SlotSpec,
    *,
    repo_dir: Path,
    repository: str,
) -> dict[str, Any]:
    root = repo_dir.resolve()
    manifest_file = root / slot.manifest_path
    implementation_file = root / slot.implementation_path
    event_file = root / slot.event_path
    if not manifest_file.is_file() or not implementation_file.is_file() or event_file.exists():
        raise RuntimeError(f"dogfood activation requires exact bootstrap file set: {slot.scenario}")
    bootstrap = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    if bootstrap != build_bootstrap_manifest(slot):
        raise RuntimeError(f"dogfood bootstrap drifted before activation: {slot.scenario}")

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
        raise RuntimeError(f"dogfood activation Event not PLANNED: {slot.scenario}: {planned}")
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
        raise RuntimeError(f"dogfood slot is not exact bootstrap file set: {slot.scenario}")
    if yaml.safe_load(manifest_file.read_text(encoding="utf-8")) != build_bootstrap_manifest(slot):
        raise RuntimeError(f"dogfood bootstrap Manifest drifted: {slot.scenario}")
    if implementation_file.read_text(encoding="utf-8") != implementation_text(slot):
        raise RuntimeError(f"dogfood implementation content drifted: {slot.scenario}")
    return slot_plan(slot, phase="verified-bootstrap", revision=0, stage_status="READY")


def verify_active_files(
    slot: SlotSpec,
    *,
    repo_dir: Path,
    repository: str,
) -> dict[str, Any]:
    root = repo_dir.resolve()
    manifest_file = root / slot.manifest_path
    implementation_file = root / slot.implementation_path
    event_file = root / slot.event_path
    if not manifest_file.is_file() or not implementation_file.is_file() or not event_file.is_file():
        raise RuntimeError(f"dogfood slot is not exact active file set: {slot.scenario}")
    if implementation_file.read_text(encoding="utf-8") != implementation_text(slot):
        raise RuntimeError(f"dogfood implementation content drifted: {slot.scenario}")
    manifest = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
    validate_active_manifest(slot, manifest, repository=repository)
    if yaml.safe_load(event_file.read_text(encoding="utf-8")) != activation_event(slot):
        raise RuntimeError(f"dogfood activation Event drifted: {slot.scenario}")
    return slot_plan(slot, phase="verified-active", revision=1, stage_status="WORKING")


def slot_plan(slot: SlotSpec, *, phase: str, revision: int, stage_status: str) -> dict[str, Any]:
    return {
        "schema_version": "ai-sdlc.v03-dogfood-fixture-slot/v1",
        "pool_profile": POOL_PROFILE,
        "scenario": slot.scenario,
        "feature_id": slot.feature_id,
        "target_ref": slot.target_ref,
        "phase": phase,
        "manifest_revision": revision,
        "workflow_status": "ACTIVE",
        "current_stage": "implementation",
        "stage_status": stage_status,
        "release_eligible": False,
    }


def inventory_document() -> dict[str, Any]:
    validate_inventory()
    slots = [asdict(slot) for slot in SLOTS]
    canonical = json.dumps(slots, sort_keys=True, separators=(",", ":"))
    return {
        "schema_version": "ai-sdlc.v03-dogfood-fixture-pool/v1",
        "pool_profile": POOL_PROFILE,
        "issue": POOL_ISSUE,
        "profile": PROFILE,
        "scenarios": list(EXPECTED_SCENARIOS),
        "slots": slots,
        "inventory_digest": hashlib.sha256(canonical.encode()).hexdigest(),
        "release_eligible": False,
    }
