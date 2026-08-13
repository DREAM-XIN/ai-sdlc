#!/usr/bin/env python3
"""Validate the v0.3 draft evidence-accounting ledger without claiming release readiness."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "release" / "v0.3.0-draft.yaml"
LEDGER = ROOT / "release" / "v0.3.0-evidence.yaml"
LEDGER_REF = "release/v0.3.0-evidence.yaml"

ALLOWED_STATUSES = {
    "unresolved",
    "implemented-awaiting-release-evidence",
    "resolved-by-feature-evidence",
    "resolved-by-release-dogfood",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
TRUNCATED_PROOF_MARKERS = {"Issue", "PR", "run"}


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def require(condition, message, errors):
    if not condition:
        errors.append(message)


def evidence_paths(entry):
    value = entry.get("evidence", []) if isinstance(entry, dict) else []
    return value if isinstance(value, list) else []


def remaining_proof(entry):
    value = entry.get("remaining_proof", []) if isinstance(entry, dict) else []
    return value if isinstance(value, list) else []


def validate_entry(scope, item_id, entry, errors):
    require(isinstance(entry, dict), f"{scope} {item_id}: ledger entry must be a mapping", errors)
    if not isinstance(entry, dict):
        return
    status = entry.get("status")
    require(status in ALLOWED_STATUSES, f"{scope} {item_id}: unsupported status {status!r}", errors)
    evidence = evidence_paths(entry)
    proof = remaining_proof(entry)
    require(isinstance(entry.get("evidence", []), list), f"{scope} {item_id}: evidence must be a list", errors)
    require(isinstance(entry.get("remaining_proof", []), list), f"{scope} {item_id}: remaining_proof must be a list", errors)

    for index, item in enumerate(proof):
        require(
            isinstance(item, str) and bool(item.strip()),
            f"{scope} {item_id}: remaining_proof[{index}] must be a non-empty string",
            errors,
        )
        if isinstance(item, str):
            require(
                item.strip() not in TRUNCATED_PROOF_MARKERS,
                f"{scope} {item_id}: remaining_proof[{index}] looks truncated by YAML comment syntax; quote references containing #",
                errors,
            )

    if status == "unresolved":
        require(bool(proof), f"{scope} {item_id}: unresolved status requires remaining_proof", errors)
    elif status == "implemented-awaiting-release-evidence":
        require(bool(evidence), f"{scope} {item_id}: implemented-awaiting-release-evidence requires durable evidence", errors)
        require(bool(proof), f"{scope} {item_id}: implemented-awaiting-release-evidence requires remaining_proof", errors)
    elif status in {"resolved-by-feature-evidence", "resolved-by-release-dogfood"}:
        require(bool(evidence), f"{scope} {item_id}: resolved status requires durable evidence", errors)

    for ref in evidence:
        require(isinstance(ref, str) and ref, f"{scope} {item_id}: evidence references must be non-empty strings", errors)
        if not isinstance(ref, str) or not ref:
            continue
        if ref.startswith(("http://", "https://", "issue:", "pr:", "run:")):
            continue
        require((ROOT / ref).is_file(), f"{scope} {item_id}: evidence path does not exist: {ref}", errors)


def validate_draft_projection(scope, draft_items, ledger_items, errors):
    require(isinstance(draft_items, list), f"release draft {scope} must be a list", errors)
    if not isinstance(draft_items, list) or not isinstance(ledger_items, dict):
        return
    indexed = {
        item.get("id"): item
        for item in draft_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    require(set(indexed) == set(ledger_items), f"release draft {scope} ids must exactly match evidence ledger", errors)
    fragment = "workstreams" if scope == "required_workstreams" else "blockers"
    for item_id, ledger_entry in ledger_items.items():
        draft_entry = indexed.get(item_id, {})
        status = ledger_entry.get("status") if isinstance(ledger_entry, dict) else None
        require(draft_entry.get("status") == status, f"{scope} {item_id}: draft status must equal ledger status", errors)
        expected_ref = f"{LEDGER_REF}#{fragment}/{item_id}"
        if status == "unresolved":
            require(
                draft_entry.get("evidence_record") in {None, expected_ref},
                f"{scope} {item_id}: unresolved evidence_record must be absent or point to its exact ledger record",
                errors,
            )
        else:
            require(
                draft_entry.get("evidence_record") == expected_ref,
                f"{scope} {item_id}: non-unresolved status requires exact evidence_record {expected_ref}",
                errors,
            )


def main():
    errors = []
    require(DRAFT.is_file(), "release/v0.3.0-draft.yaml is missing", errors)
    require(LEDGER.is_file(), "release/v0.3.0-evidence.yaml is missing", errors)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(2)

    draft = load_yaml(DRAFT)
    ledger = load_yaml(LEDGER)
    require(isinstance(draft, dict), "v0.3 release draft must be a mapping", errors)
    require(isinstance(ledger, dict), "v0.3 evidence ledger must be a mapping", errors)
    if not isinstance(draft, dict) or not isinstance(ledger, dict):
        for error in errors:
            print(error)
        raise SystemExit(2)

    require(draft.get("status") == "planning", "v0.3 draft must remain planning until release-candidate governance", errors)
    require(draft.get("evidence_ledger") == LEDGER_REF, "v0.3 draft must reference the validated evidence ledger", errors)
    require(draft.get("release_ready") is False, "v0.3 draft must explicitly remain release_ready=false", errors)

    require(ledger.get("release_version") == "0.3.0", "evidence ledger release_version must be 0.3.0", errors)
    require(ledger.get("tracking_issue") == "#218", "evidence ledger must bind Issue #218", errors)
    require(ledger.get("release_ready") is False, "planning evidence ledger must not claim release_ready", errors)
    as_of = ledger.get("as_of_main_commit")
    require(isinstance(as_of, str) and bool(SHA40.fullmatch(as_of)), "evidence ledger as_of_main_commit must be a full commit SHA", errors)

    taxonomy = ledger.get("status_taxonomy")
    require(isinstance(taxonomy, dict), "status_taxonomy must be a mapping", errors)
    if isinstance(taxonomy, dict):
        require(set(taxonomy) == ALLOWED_STATUSES, "status_taxonomy must define exactly the approved Issue #218 statuses", errors)
        for status, definition in taxonomy.items():
            require(isinstance(definition, dict) and isinstance(definition.get("meaning"), str) and definition.get("meaning"), f"status taxonomy {status}: meaning is required", errors)

    ledger_workstreams = ledger.get("workstreams")
    require(isinstance(ledger_workstreams, dict), "workstreams ledger must be a mapping", errors)
    if isinstance(ledger_workstreams, dict):
        for item_id, entry in ledger_workstreams.items():
            validate_entry("workstream", item_id, entry, errors)
        validate_draft_projection("required_workstreams", draft.get("required_workstreams"), ledger_workstreams, errors)

    ledger_blockers = ledger.get("blockers")
    require(isinstance(ledger_blockers, dict), "blockers ledger must be a mapping", errors)
    if isinstance(ledger_blockers, dict):
        for item_id, entry in ledger_blockers.items():
            validate_entry("blocker", item_id, entry, errors)
        validate_draft_projection("known_release_blockers", draft.get("known_release_blockers"), ledger_blockers, errors)

        for adapter_blocker in (
            "fewer-than-two-supported-ai-client-adapters",
            "no-write-capable-ai-client-adapter",
        ):
            entry = ledger_blockers.get(adapter_blocker, {})
            require(entry.get("status") == "unresolved", f"{adapter_blocker} must remain unresolved until accepted second-adapter evidence exists", errors)

        for runtime_blocker in (
            "duplicate-external-dispatch-key-for-same-semantic-effect",
            "cancel-before-launch-linearization-can-still-launch",
            "claim-launch-crash-recovery-gap",
            "callback-replay-side-effects",
            "persist-cancel-linearization-gap",
            "unknown-launch-can-be-bypassed-by-feature-revision-or-candidate-change",
            "unresolved-predecessor-can-create-independent-successor-dispatch",
        ):
            entry = ledger_blockers.get(runtime_blocker, {})
            require(
                entry.get("status") in {"unresolved", "implemented-awaiting-release-evidence"},
                f"{runtime_blocker} cannot be release-resolved before Issue #221 real-runtime evidence",
                errors,
            )

    decisions = (ledger_workstreams or {}).get("operator-decisions-authorization-notifications", {})
    require(decisions.get("status") == "resolved-by-feature-evidence", "accepted Decisions/Notifications workstream should be resolved by bounded Feature evidence", errors)

    planning = (ledger_workstreams or {}).get("planning-manifest-validation", {})
    require(planning.get("status") == "unresolved", "Issue #218 cannot mark its own planning-manifest-validation workstream resolved before draft synchronization merges", errors)

    if errors:
        print("v0.3 release evidence validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    workstream_count = len(ledger_workstreams or {})
    blocker_count = len(ledger_blockers or {})
    print(
        "v0.3 release evidence ledger passed: "
        f"{workstream_count} workstreams, {blocker_count} blockers, release_ready=false"
    )


if __name__ == "__main__":
    main()
