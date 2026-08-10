#!/usr/bin/env python3
"""Validate the vertical role-dispatch adapter over existing gh-aw workflows."""
from __future__ import annotations

import json

from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from validate_operator_vertical_completion import main as validate_vertical_completion
from validate_operator_vertical_reconcile import main as validate_vertical_reconcile

HEAD = "d" * 40
KEY = "dispatch-" + "e" * 40


class FakeTransport:
    def __init__(self):
        self.dispatched = []
        self.lookup_rows = {}

    def dispatch(self, *, workflow, ref, inputs):
        self.dispatched.append((workflow, ref, dict(inputs)))
        return {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}

    def lookup(self, *, workflow, ref, dispatch_key):
        return self.lookup_rows.get((workflow, ref, dispatch_key))


def _gateway(transport):
    return GhAwVerticalRoleDispatchGateway(
        transport=transport,
        workflows=GhAwVerticalWorkflowMap(
            default_branch="main",
            developer_workflow="ai-sdlc-gh-aw-worker-codex.lock.yml",
            reviewer_workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
            qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
        ),
    )


def _dispatch(role):
    return {
        "operation_id": "op-1",
        "operation_generation": 0,
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": "f" * 64,
        "external_dispatch_key": KEY,
        "dispatch_id": "vertical-1",
        "target_repository": "DREAM-XIN/ai-sdlc",
        "target_ref": "feature/test",
        "feature_id": "F-TEST",
        "expected_revision": 12,
        "feature_stage": "verification" if role == "qa" else "code-review" if role == "reviewer" else "implementation",
        "task_id": "TASK-1",
        "task_identity": "vertical:test",
        "role": role,
        "candidate_pr_number": 7 if role in {"reviewer", "qa"} else None,
        "candidate_head_sha": HEAD if role in {"reviewer", "qa"} else None,
    }


def _object_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _object_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _object_keys(child)


def validate_mapping():
    transport = FakeTransport()
    gateway = _gateway(transport)
    receipt = gateway.launch(dispatch=_dispatch("reviewer"))
    assert receipt == {"lookup_state": "LAUNCHED", "receipt_id": "run-1"}
    workflow, ref, inputs = transport.dispatched[-1]
    assert workflow == "ai-sdlc-gh-aw-reviewer-claude.lock.yml"
    assert ref == "main"
    assert inputs["dispatch_key"] == KEY
    assert inputs["candidate_pr_number"] == "7"
    assert inputs["candidate_head_sha"] == HEAD
    payload = json.loads(inputs["task_payload"])
    assert payload["feature_context"]["vertical"]["external_dispatch_key"] == KEY
    assert "proposed_events" not in set(_object_keys(payload))
    assert "authoritative Feature Manifest mutation" in payload["task"]["forbidden_scope"]


def validate_gate_candidate_required():
    transport = FakeTransport()
    gateway = _gateway(transport)
    bad = _dispatch("qa")
    bad["candidate_head_sha"] = None
    try:
        gateway.launch(dispatch=bad)
        raise AssertionError("QA launch without exact candidate unexpectedly accepted")
    except VerticalInvariantError as exc:
        assert exc.code == "STALE_REVISION"


def validate_fresh_process_lookup():
    transport = FakeTransport()
    gateway = _gateway(transport)
    transport.lookup_rows[("ai-sdlc-gh-aw-reviewer-claude.lock.yml", "main", KEY)] = {
        "lookup_state": "LAUNCHED",
        "receipt_id": "run-review",
    }
    assert gateway.lookup(external_dispatch_key=KEY) == {
        "lookup_state": "LAUNCHED",
        "receipt_id": "run-review",
    }

    transport.lookup_rows[("ai-sdlc-gh-aw-qa-gemini.lock.yml", "main", KEY)] = {
        "lookup_state": "LAUNCHED",
        "receipt_id": "run-qa",
    }
    assert gateway.lookup(external_dispatch_key=KEY)["lookup_state"] == "UNKNOWN"


def validate_unknown_is_honest():
    transport = FakeTransport()
    gateway = _gateway(transport)
    transport.lookup_rows[("ai-sdlc-gh-aw-reviewer-claude.lock.yml", "main", KEY)] = {
        "lookup_state": "UNKNOWN",
        "receipt_id": None,
    }
    assert gateway.lookup(external_dispatch_key=KEY) == {"lookup_state": "UNKNOWN", "receipt_id": None}


def main():
    validate_mapping()
    validate_gate_candidate_required()
    validate_fresh_process_lookup()
    validate_unknown_is_honest()
    validate_vertical_completion()
    validate_vertical_reconcile()
    print("Operator vertical gh-aw validation passed")


if __name__ == "__main__":
    main()
