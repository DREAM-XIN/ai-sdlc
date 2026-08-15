#!/usr/bin/env python3
"""Build and verify the fixed v0.3 real-runtime Feature fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from bootstrap_feature import build_manifest
from ingest_feature_event import ingest
from operator_vertical import FeatureSnapshot
from operator_vertical_controller import select_vertical_action
from validate_feature_manifest import validate_manifest

FEATURE_ID = "F-OPERATOR-V03-REAL-RUNTIME-FI-0001"
TARGET_REF = "verification/v0.3-real-runtime-fixture-221"
ISSUE_NUMBER = 276
MANIFEST_PATH = f"state/features/{FEATURE_ID}.yaml"
EVENT_ID = f"EVT-{FEATURE_ID}-CODE-REVIEW-START"
EVENT_PATH = f"state/events/{FEATURE_ID}/{EVENT_ID}.yaml"
IMPLEMENTATION_PATH = f"docs/features/{FEATURE_ID}/implementation.md"
CREATED_AT = "2026-08-14T09:40:00Z"
ACTIVATED_AT = "2026-08-14T09:41:00Z"
PROFILE_ID = "v03-real-runtime-fixture"

# Issue #276 is explicit: this release-only profile starts at Code Review.
# There is intentionally no implementation lifecycle stage to complete or fake.
FIXTURE_PROFILE = {
    "id": PROFILE_ID,
    "version": "0.1.0",
    "risk_profile": "low",
    "stages": [
        {"id": "code-review", "role": "reviewer", "gate": "code-gate"},
        {"id": "verification", "role": "qa", "depends_on": ["code-review"], "gate": "verification-gate"},
        {"id": "acceptance", "role": "product", "depends_on": ["verification"], "gate": "release-gate"},
    ],
}

IMPLEMENTATION_TEXT = """# v0.3 real-runtime fault-injection fixture

This branch is a release-only Feature/PR fixture for Issue #221.

It intentionally contains no product implementation change. The fixture-local
workflow starts at Code Review exactly as required by Issue #276. Provisioning
registers this document as the one draft implementation artifact and moves only
`code-review` from `READY` to `WORKING`; it does not fabricate an implementation
lifecycle stage, an implementation completion, a Gate verdict, or Review evidence.

A real Reviewer `PASS` may approve this draft artifact through the normal Vertical
callback path. A real Reviewer `REWORK` remains canonical through the narrow
artifact-backed Code-Review-first remediation contract: the remediation identity
continues to target `implementation`, but no implementation lifecycle stage is
invented or completed merely to make remediation possible.

A Worker result remains recommendation/evidence only. Any lifecycle mutation
still requires the protected Operator Store callback path and exact Feature
Event/Persist authority exercised by the #221 full-runtime test.

This fixture is not Product Acceptance, dogfood, or release-ready evidence and
must not be merged as a product change.
"""


def _bootstrap_input() -> dict:
    return {
        "version": "0.1.0",
        "feature": {
            "id": FEATURE_ID,
            "title": "v0.3 trusted real-runtime fault-injection fixture",
            "risk": "low",
            "issue": f"#{ISSUE_NUMBER}",
        },
        "profile": PROFILE_ID,
        "created_at": CREATED_AT,
    }


def build_bootstrap_manifest() -> dict:
    result = build_manifest(_bootstrap_input(), FIXTURE_PROFILE)
    if result.get("outcome") != "BOOTSTRAPPED":
        raise RuntimeError(f"fixture bootstrap failed: {result}")
    manifest = result["manifest"]
    errors = validate_manifest(manifest)
    if errors:
        raise RuntimeError("bootstrap fixture Manifest is invalid: " + "; ".join(errors))
    stages = {row["id"]: row["status"] for row in manifest["workflow"]["stages"]}
    if (
        manifest["revision"] != 0
        or manifest["workflow"]["status"] != "ACTIVE"
        or manifest["workflow"]["current_stage"] != "code-review"
        or stages != {
            "code-review": "READY",
            "verification": "TODO",
            "acceptance": "TODO",
        }
        or manifest["artifacts"]
        or manifest["evidence"]
        or manifest["tasks"]
        or manifest["applied_events"]
    ):
        raise RuntimeError("bootstrap fixture Manifest did not converge to exact Code-Review-first revision-0 state")
    return manifest


def activation_event() -> dict:
    return {
        "version": "0.1.0",
        "id": EVENT_ID,
        "feature_id": FEATURE_ID,
        "expected_revision": 0,
        "occurred_at": ACTIVATED_AT,
        "changes": [
            {
                "kind": "artifact-record",
                "record": {
                    "id": "implementation-v1",
                    "type": "implementation",
                    "uri": IMPLEMENTATION_PATH,
                    "status": "draft",
                },
            },
            {"kind": "stage", "id": "code-review", "status": "WORKING"},
        ],
    }


def activate_manifest(*, bootstrap_manifest: dict, repository: str) -> tuple[dict, dict]:
    expected = build_bootstrap_manifest()
    if bootstrap_manifest != expected:
        raise RuntimeError("existing fixture bootstrap Manifest differs from exact canonical revision-0 state")
    event = activation_event()
    planned = ingest(
        bootstrap_manifest,
        event,
        event_path=EVENT_PATH,
        repository=repository,
        manifest_path=MANIFEST_PATH,
        target_ref=TARGET_REF,
        issue=ISSUE_NUMBER,
    )
    if planned.get("outcome") != "PLANNED":
        raise RuntimeError(f"fixture activation Event was not PLANNED: {planned}")
    manifest = yaml.safe_load(planned["plan"]["manifest"]["content"])
    validate_active_manifest(manifest, repository=repository)
    return manifest, event


def validate_active_manifest(manifest: dict, *, repository: str) -> None:
    errors = validate_manifest(manifest)
    if errors:
        raise RuntimeError("materialized fixture Manifest is invalid: " + "; ".join(errors))
    workflow = manifest["workflow"]
    stages = {row["id"]: row["status"] for row in workflow["stages"]}
    gates = {row["id"]: row["status"] for row in manifest["gates"]}
    if (
        manifest["revision"] != 1
        or workflow["status"] != "ACTIVE"
        or workflow["current_stage"] != "code-review"
        or stages != {
            "code-review": "WORKING",
            "verification": "TODO",
            "acceptance": "TODO",
        }
        or set(gates.values()) != {"PENDING"}
        or manifest["evidence"] != []
        or manifest["tasks"] != []
        or manifest["applied_events"] != [EVENT_ID]
    ):
        raise RuntimeError("fixture Manifest did not converge to the exact reviewed Code-Review-first runtime state")
    if manifest["artifacts"] != [{
        "id": "implementation-v1",
        "type": "implementation",
        "uri": IMPLEMENTATION_PATH,
        "status": "draft",
    }]:
        raise RuntimeError("fixture must contain exactly one draft implementation artifact")

    probe_head = "1" * 40
    feature = FeatureSnapshot.from_manifest(
        repository=repository,
        target_ref=TARGET_REF,
        manifest=manifest,
        candidate_pr_number=999999,
        candidate_head_sha=probe_head,
    )
    action = select_vertical_action(feature=feature, manifest=manifest, occurred_at="2026-08-14T09:42:00Z")
    if (
        action.kind != "dispatch"
        or action.step != "CODE_REVIEW"
        or action.role != "reviewer"
        or action.candidate_head_sha != probe_head
    ):
        raise RuntimeError("fixture is not an immediate exact-head Reviewer dispatch")
    if probe_head in yaml.safe_dump(manifest, sort_keys=False):
        raise RuntimeError("fixture Manifest unexpectedly embeds candidate head identity")


def _read_yaml(path: Path, *, label: str) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"{label} is not readable YAML") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} is not a mapping")
    return value


def verify_bootstrap_files(*, repo_dir: Path) -> dict:
    root = repo_dir.resolve()
    manifest_file = root / MANIFEST_PATH
    implementation_file = root / IMPLEMENTATION_PATH
    event_file = root / EVENT_PATH
    if not manifest_file.is_file() or not implementation_file.is_file() or event_file.exists():
        raise RuntimeError("existing fixture branch is not the exact bootstrap file set")
    if _read_yaml(manifest_file, label="bootstrap Manifest") != build_bootstrap_manifest():
        raise RuntimeError("existing fixture bootstrap Manifest content drifted")
    if implementation_file.read_text(encoding="utf-8") != IMPLEMENTATION_TEXT:
        raise RuntimeError("existing fixture implementation content drifted")
    return {
        "phase": "verified-bootstrap",
        "feature_id": FEATURE_ID,
        "target_ref": TARGET_REF,
        "manifest_revision": 0,
        "current_stage": "code-review",
        "stage_status": "READY",
        "release_eligible": False,
    }


def verify_active_files(*, repo_dir: Path, repository: str) -> dict:
    root = repo_dir.resolve()
    manifest_file = root / MANIFEST_PATH
    implementation_file = root / IMPLEMENTATION_PATH
    event_file = root / EVENT_PATH
    if not manifest_file.is_file() or not implementation_file.is_file() or not event_file.is_file():
        raise RuntimeError("existing fixture branch is not the exact active file set")
    if implementation_file.read_text(encoding="utf-8") != IMPLEMENTATION_TEXT:
        raise RuntimeError("existing fixture implementation content drifted")
    manifest = _read_yaml(manifest_file, label="active Manifest")
    validate_active_manifest(manifest, repository=repository)
    if _read_yaml(event_file, label="activation Event") != activation_event():
        raise RuntimeError("existing fixture activation Event content drifted")
    return {
        "phase": "verified-active",
        "schema_version": "ai-sdlc.v03-real-runtime-fixture-plan/v1",
        "repository": repository.lower(),
        "feature_id": FEATURE_ID,
        "target_ref": TARGET_REF,
        "manifest_revision": 1,
        "workflow_status": "ACTIVE",
        "current_stage": "code-review",
        "stage_status": "WORKING",
        "release_eligible": False,
    }


def materialize_bootstrap(*, repo_dir: Path) -> dict:
    root = repo_dir.resolve()
    manifest = build_bootstrap_manifest()
    outputs = {
        MANIFEST_PATH: yaml.safe_dump(manifest, sort_keys=False),
        IMPLEMENTATION_PATH: IMPLEMENTATION_TEXT,
    }
    for relative, content in outputs.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError(f"fixture path already exists: {relative}")
        target.write_text(content, encoding="utf-8")
    return {
        "phase": "bootstrap",
        "feature_id": FEATURE_ID,
        "target_ref": TARGET_REF,
        "manifest_revision": 0,
        "current_stage": "code-review",
        "stage_status": "READY",
        "release_eligible": False,
    }


def materialize_activation(*, repo_dir: Path, repository: str) -> dict:
    root = repo_dir.resolve()
    manifest_file = root / MANIFEST_PATH
    implementation_file = root / IMPLEMENTATION_PATH
    event_file = root / EVENT_PATH
    if not manifest_file.is_file() or not implementation_file.is_file():
        raise RuntimeError("fixture activation requires the exact bootstrap commit files")
    if event_file.exists():
        raise RuntimeError("fixture activation Event already exists")
    bootstrap_manifest = _read_yaml(manifest_file, label="bootstrap Manifest")
    manifest, event = activate_manifest(bootstrap_manifest=bootstrap_manifest, repository=repository)
    manifest_file.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    event_file.parent.mkdir(parents=True, exist_ok=True)
    event_file.write_text(yaml.safe_dump(event, sort_keys=False), encoding="utf-8")
    return verify_active_files(repo_dir=root, repository=repository) | {"phase": "activation"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", type=Path, required=True)
    parser.add_argument("--repository")
    parser.add_argument(
        "--phase",
        choices=["bootstrap", "activate", "verify-bootstrap", "verify-active"],
        required=True,
    )
    parser.add_argument("--plan-output", type=Path)
    args = parser.parse_args()
    if args.phase in {"activate", "verify-active"}:
        if not args.repository or "/" not in args.repository or any(ch.isspace() for ch in args.repository):
            raise SystemExit(f"{args.phase} requires exact --repository owner/name")
    if args.phase == "bootstrap":
        plan = materialize_bootstrap(repo_dir=args.repo_dir)
    elif args.phase == "activate":
        plan = materialize_activation(repo_dir=args.repo_dir, repository=args.repository)
    elif args.phase == "verify-bootstrap":
        plan = verify_bootstrap_files(repo_dir=args.repo_dir)
    else:
        plan = verify_active_files(repo_dir=args.repo_dir, repository=args.repository)
    text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if args.plan_output:
        args.plan_output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
