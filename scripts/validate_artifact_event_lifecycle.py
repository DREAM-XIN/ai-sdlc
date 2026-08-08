#!/usr/bin/env python3
from apply_feature_event import apply_event


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def manifest():
    return {
        "protocol_version": "0.1.0",
        "revision": 0,
        "feature": {"id": "F-ART", "title": "Artifact event lifecycle", "risk": "low"},
        "workflow": {
            "profile": "standard-feature",
            "status": "ACTIVE",
            "current_stage": "requirement",
            "stages": [
                {"id": "requirement", "status": "READY"},
                {"id": "requirement-review", "status": "TODO", "gate": "requirement-gate"},
            ],
        },
        "tasks": [],
        "artifacts": [],
        "gates": [{"id": "requirement-gate", "status": "PENDING"}],
        "evidence": [],
        "applied_events": [],
        "updated_at": "2026-08-08T10:00:00Z",
    }


def event(event_id, revision, changes, occurred_at):
    return {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": "F-ART",
        "expected_revision": revision,
        "occurred_at": occurred_at,
        "changes": changes,
    }


def main():
    initial = manifest()
    requirement = {
        "id": "ART-F-ART-REQ",
        "type": "requirement",
        "uri": "docs/features/F-ART/requirement.md",
        "status": "draft",
    }

    registered = apply_event(
        initial,
        event(
            "EVT-ART-REGISTER",
            0,
            [
                {"kind": "artifact-record", "record": requirement},
                {"kind": "stage", "id": "requirement", "status": "WORKING"},
                {"kind": "stage", "id": "requirement", "status": "DONE"},
            ],
            "2026-08-08T10:01:00Z",
        ),
    )
    require(registered["outcome"] == "APPLIED", f"draft artifact registration failed: {registered}")
    require(registered["manifest"]["revision"] == 1, "artifact registration did not increment revision")
    artifact = registered["manifest"]["artifacts"][0]
    require(artifact == requirement, f"artifact record drifted: {artifact}")
    require(registered["manifest"]["workflow"]["current_stage"] == "requirement-review", "requirement completion did not advance to review")

    duplicate = apply_event(
        registered["manifest"],
        event(
            "EVT-ART-DUP",
            1,
            [{"kind": "artifact-record", "record": requirement}],
            "2026-08-08T10:02:00Z",
        ),
    )
    require(duplicate["outcome"] == "INVALID", "duplicate artifact id unexpectedly passed")
    require("duplicate artifact id" in "\n".join(duplicate["errors"]), f"duplicate rejection lacks detail: {duplicate}")

    self_approved = apply_event(
        initial,
        event(
            "EVT-ART-SELF-APPROVE",
            0,
            [
                {"kind": "artifact-record", "record": requirement},
                {"kind": "evidence", "record": {"id": "EVID-SELF", "type": "review", "status": "pass", "uri": "review://self"}},
                {"kind": "artifact", "id": "ART-F-ART-REQ", "status": "approved", "evidence": ["EVID-SELF"]},
            ],
            "2026-08-08T10:03:00Z",
        ),
    )
    require(self_approved["outcome"] == "INVALID", "same-event artifact registration and approval unexpectedly passed")
    require("cannot be registered and approved/superseded in the same event" in "\n".join(self_approved["errors"]), f"same-event approval rejection lacks detail: {self_approved}")

    no_evidence = apply_event(
        registered["manifest"],
        event(
            "EVT-ART-NO-EVID",
            1,
            [{"kind": "artifact", "id": "ART-F-ART-REQ", "status": "approved"}],
            "2026-08-08T10:04:00Z",
        ),
    )
    require(no_evidence["outcome"] == "INVALID", "artifact approval without evidence unexpectedly passed")

    unknown_evidence = apply_event(
        registered["manifest"],
        event(
            "EVT-ART-UNKNOWN-EVID",
            1,
            [{"kind": "artifact", "id": "ART-F-ART-REQ", "status": "approved", "evidence": ["EVID-MISSING"]}],
            "2026-08-08T10:05:00Z",
        ),
    )
    require(unknown_evidence["outcome"] == "INVALID", "artifact approval with unknown evidence unexpectedly passed")
    require("references unknown evidence" in "\n".join(unknown_evidence["errors"]), f"unknown-evidence rejection lacks detail: {unknown_evidence}")

    approved = apply_event(
        registered["manifest"],
        event(
            "EVT-ART-APPROVE",
            1,
            [
                {"kind": "evidence", "record": {"id": "EVID-REQ-REVIEW", "type": "review", "status": "pass", "uri": "docs/features/F-ART/requirement-review.md"}},
                {"kind": "artifact", "id": "ART-F-ART-REQ", "status": "approved", "evidence": ["EVID-REQ-REVIEW"]},
                {"kind": "stage", "id": "requirement-review", "status": "WORKING"},
                {"kind": "stage", "id": "requirement-review", "status": "DONE"},
                {"kind": "gate", "id": "requirement-gate", "status": "PASS", "evidence": ["EVID-REQ-REVIEW"]},
            ],
            "2026-08-08T10:06:00Z",
        ),
    )
    require(approved["outcome"] == "APPLIED", f"independent artifact approval failed: {approved}")
    require(approved["manifest"]["revision"] == 2, "artifact approval did not advance revision")
    artifact = approved["manifest"]["artifacts"][0]
    require(artifact["status"] == "approved", f"artifact was not approved: {artifact}")
    gate = approved["manifest"]["gates"][0]
    require(gate["status"] == "PASS" and gate["evidence"] == ["EVID-REQ-REVIEW"], f"review Gate evidence drifted: {gate}")

    superseded = apply_event(
        approved["manifest"],
        event(
            "EVT-ART-SUPERSEDE",
            2,
            [{"kind": "artifact", "id": "ART-F-ART-REQ", "status": "superseded"}],
            "2026-08-08T10:07:00Z",
        ),
    )
    require(superseded["outcome"] == "APPLIED", f"approved artifact supersede failed: {superseded}")
    require(superseded["manifest"]["artifacts"][0]["status"] == "superseded", "artifact did not become superseded")

    print("Feature Event artifact registration, independent approval, evidence, and supersede checks passed")


if __name__ == "__main__":
    main()
