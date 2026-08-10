#!/usr/bin/env python3
"""Concrete vertical RoleDispatchGateway over the existing trusted gh-aw workflow transport."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol

from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError


class GhAwWorkflowTransport(Protocol):
    """Trusted default-branch Actions transport supplied by the control plane."""

    def dispatch(self, *, workflow: str, ref: str, inputs: dict[str, str]) -> dict[str, Any]: ...
    def lookup(self, *, workflow: str, ref: str, dispatch_key: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class GhAwVerticalWorkflowMap:
    default_branch: str
    developer_workflow: str
    reviewer_workflow: str
    qa_workflow: str

    def __post_init__(self):
        if not self.default_branch:
            raise ValueError("trusted default branch is required")
        for workflow in (self.developer_workflow, self.reviewer_workflow, self.qa_workflow):
            if not re.fullmatch(r"[A-Za-z0-9._-]+\.ya?ml", workflow):
                raise ValueError("trusted gh-aw workflow must be a workflow filename")

    def workflow_for(self, role: str) -> str:
        if role == "developer":
            return self.developer_workflow
        if role == "reviewer":
            return self.reviewer_workflow
        if role == "qa":
            return self.qa_workflow
        raise VerticalInvariantError("POLICY_DENIED", "unsupported vertical gh-aw role")


class GhAwVerticalRoleDispatchGateway:
    """Map one authorized vertical semantic effect to one existing gh-aw workflow dispatch."""

    def __init__(self, *, transport: GhAwWorkflowTransport, workflows: GhAwVerticalWorkflowMap):
        self.transport = transport
        self.workflows = workflows
        self._workflow_by_dispatch_key: dict[str, str] = {}

    @staticmethod
    def _task_payload(dispatch: dict[str, Any]) -> str:
        role = str(dispatch["role"])
        step = str(dispatch.get("task_identity") or dispatch["task_id"])
        remediation = role == "developer" and "code-remediation" in step
        task = {
            "id": str(dispatch["task_id"]),
            "kind": "remediation" if remediation else "stage",
            "feature_id": str(dispatch["feature_id"]),
            "role": role,
            "goal": f"Execute trusted vertical loop step {step} and return only the assigned role result contract.",
            "inputs": [],
            "allowed_scope": ["assigned Feature branch and bounded vertical task"],
            "forbidden_scope": [
                "authoritative Feature Manifest mutation",
                "arbitrary Feature Event or Gate mutation",
                "release/merge authority",
            ],
            "expected_outputs": ["structured role result; collected outputs are materialized by trusted runtime"],
            "definition_of_done": ["Return the role-specific structured result for the exact assigned candidate/revision."],
            "runtime": "gh-aw",
            "max_attempts": 3,
        }
        payload = {
            "contract": "ai-sdlc-task-v0.1",
            "task": task,
            "feature_context": {
                "id": dispatch["feature_id"],
                "repository": dispatch["target_repository"],
                "manifest_ref": f"state/features/{dispatch['feature_id']}.yaml",
                "vertical": {
                    "profile": VERTICAL_PROFILE,
                    "operation_id": dispatch["operation_id"],
                    "operation_generation": dispatch["operation_generation"],
                    "semantic_effect_key": dispatch["semantic_effect_key"],
                    "external_dispatch_key": dispatch["external_dispatch_key"],
                    "dispatch_id": dispatch["dispatch_id"],
                    "expected_revision": dispatch["expected_revision"],
                    "candidate_head_sha": dispatch.get("candidate_head_sha"),
                },
            },
            "worker_rules": [
                "Do not edit the authoritative Feature Manifest.",
                "Do not emit executable Feature Events or proposed_events.",
                "Do not self-approve any Gate.",
                "Output labels are logical only; do not choose authoritative URI/path/artifact/evidence ids.",
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _target_parts(repository: str) -> tuple[str, str]:
        parts = repository.split("/")
        if len(parts) != 2 or not all(parts):
            raise VerticalInvariantError("INVALID_REQUEST", "target repository must be owner/repo")
        return parts[0], parts[1]

    def _inputs(self, dispatch: dict[str, Any]) -> dict[str, str]:
        if dispatch.get("operation_profile") != VERTICAL_PROFILE:
            raise VerticalInvariantError("POLICY_DENIED", "gh-aw vertical dispatch requires immutable vertical profile")
        role = str(dispatch["role"])
        owner, repo_name = self._target_parts(str(dispatch["target_repository"]))
        stage = "verification" if role == "qa" else "code-review" if role == "reviewer" else "implementation"
        inputs = {
            "feature_id": str(dispatch["feature_id"]),
            "expected_revision": str(dispatch["expected_revision"]),
            "target_repository": str(dispatch["target_repository"]),
            "target_owner": owner,
            "target_repo_name": repo_name,
            "target_ref": str(dispatch["target_ref"]),
            "stage": stage,
            "role": role,
            "task_payload": self._task_payload(dispatch),
            "dispatch_key": str(dispatch["external_dispatch_key"]),
        }
        if role in {"reviewer", "qa"}:
            pr = dispatch.get("candidate_pr_number")
            head = dispatch.get("candidate_head_sha")
            if not isinstance(pr, int) or pr <= 0 or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
                raise VerticalInvariantError("STALE_REVISION", "gate role dispatch requires exact trusted PR/head candidate")
            inputs["candidate_pr_number"] = str(pr)
            inputs["candidate_head_sha"] = head
        return inputs

    def launch(self, *, dispatch: dict[str, Any]) -> dict[str, Any]:
        role = str(dispatch["role"])
        workflow = self.workflows.workflow_for(role)
        key = str(dispatch["external_dispatch_key"])
        inputs = self._inputs(dispatch)
        self._workflow_by_dispatch_key[key] = workflow
        receipt = self.transport.dispatch(
            workflow=workflow,
            ref=self.workflows.default_branch,
            inputs=inputs,
        )
        if not isinstance(receipt, dict):
            raise VerticalInvariantError("INTERNAL_FAILURE", "gh-aw workflow transport returned invalid dispatch receipt")
        state = receipt.get("lookup_state", "LAUNCHED")
        if state not in {"LAUNCHED", "NOT_LAUNCHED", "UNKNOWN"}:
            raise VerticalInvariantError("INTERNAL_FAILURE", "gh-aw workflow transport returned invalid launch state")
        return {"lookup_state": state, "receipt_id": receipt.get("receipt_id")}

    def lookup(self, *, external_dispatch_key: str) -> dict[str, Any]:
        workflow = self._workflow_by_dispatch_key.get(external_dispatch_key)
        if workflow is None:
            # A fresh process may not have the in-memory map. Query every trusted vertical
            # role workflow; exactly one positive match is required, otherwise fail closed.
            workflows = tuple(dict.fromkeys((
                self.workflows.developer_workflow,
                self.workflows.reviewer_workflow,
                self.workflows.qa_workflow,
            )))
        else:
            workflows = (workflow,)
        matches = []
        unknown = False
        for candidate in workflows:
            receipt = self.transport.lookup(
                workflow=candidate,
                ref=self.workflows.default_branch,
                dispatch_key=external_dispatch_key,
            )
            if receipt is None:
                continue
            state = receipt.get("lookup_state", "UNKNOWN")
            if state == "LAUNCHED":
                matches.append(receipt)
            elif state == "UNKNOWN":
                unknown = True
            elif state != "NOT_LAUNCHED":
                unknown = True
        if len(matches) == 1:
            return {"lookup_state": "LAUNCHED", "receipt_id": matches[0].get("receipt_id")}
        if len(matches) > 1 or unknown:
            return {"lookup_state": "UNKNOWN", "receipt_id": None}
        return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
