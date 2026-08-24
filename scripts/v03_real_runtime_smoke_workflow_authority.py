#!/usr/bin/env python3
"""Pure structural contract for trusted-main v0.3 real-smoke workflow authority."""
from __future__ import annotations

from typing import Any

import yaml


class SmokeWorkflowAuthorityError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeWorkflowAuthorityError(message)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    _require(isinstance(value, dict), f"{field} must be a mapping")
    return value


def validate_smoke_workflow_authority_text(source: str) -> None:
    """Require the exact reviewed permission/trigger partition for external execution."""
    _require(isinstance(source, str) and bool(source.strip()), "smoke workflow source is empty")
    try:
        # BaseLoader avoids YAML 1.1 coercion of the key `on` to boolean.
        doc = yaml.load(source, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise SmokeWorkflowAuthorityError("smoke workflow is not valid YAML") from exc
    _require(isinstance(doc, dict), "smoke workflow is not a YAML mapping")

    triggers = _mapping(doc.get("on"), "on")
    _require(
        set(triggers) == {"push", "pull_request", "workflow_dispatch"},
        "smoke workflow trigger set drifted",
    )

    workflow_permissions = _mapping(doc.get("permissions"), "permissions")
    _require(workflow_permissions.get("contents") == "read", "workflow contents permission is not read-only")
    _require(
        workflow_permissions.get("pull-requests") == "read",
        "workflow pull-request permission is not read-only",
    )
    _require("actions" not in workflow_permissions, "workflow-level Actions write authority is forbidden")
    _require("issues" not in workflow_permissions, "workflow-level Issues write authority is forbidden")

    jobs = _mapping(doc.get("jobs"), "jobs")
    _require(
        set(jobs) == {
            "automatic-preflight",
            "reject-non-main-manual-dispatch",
            "trusted-main-manual-smoke",
        },
        "smoke job set drifted",
    )

    automatic = _mapping(jobs["automatic-preflight"], "automatic-preflight")
    _require(
        automatic.get("if") == "github.event_name != 'workflow_dispatch'",
        "automatic job condition drifted",
    )
    automatic_permissions = _mapping(automatic.get("permissions"), "automatic-preflight.permissions")
    _require(automatic_permissions.get("contents") == "read", "automatic job lacks contents:read")
    _require(
        automatic_permissions.get("pull-requests") == "read",
        "automatic job lacks pull-requests:read",
    )
    _require(
        automatic_permissions.get("issues") == "write",
        "automatic job may only write durable Issue evidence",
    )
    _require(
        automatic_permissions.get("actions") != "write",
        "automatic push/PR job must never receive Actions write authority",
    )

    automatic_steps = automatic.get("steps")
    _require(isinstance(automatic_steps, list), "automatic steps missing")
    probe_steps = [
        row
        for row in automatic_steps
        if isinstance(row, dict)
        and row.get("name") == "Probe release prerequisites and fail closed before external authority"
    ]
    _require(len(probe_steps) == 1, "automatic probe step missing or duplicated")
    probe_env = _mapping(probe_steps[0].get("env"), "automatic probe env")
    _require(
        probe_env.get("FI_EXTERNAL_EXECUTION_AUTHORIZED") == "0",
        "automatic probe must explicitly disable external execution",
    )

    denied = _mapping(jobs["reject-non-main-manual-dispatch"], "reject-non-main-manual-dispatch")
    _require(
        denied.get("if") == "github.event_name == 'workflow_dispatch' && github.ref != 'refs/heads/main'",
        "non-main manual rejection condition drifted",
    )
    denied_permissions = _mapping(denied.get("permissions"), "reject-non-main-manual-dispatch.permissions")
    _require(
        denied_permissions == {"contents": "read"},
        "non-main rejection job has unnecessary authority",
    )

    manual = _mapping(jobs["trusted-main-manual-smoke"], "trusted-main-manual-smoke")
    _require(
        manual.get("if") == "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main'",
        "trusted-main manual execution condition drifted",
    )
    manual_permissions = _mapping(manual.get("permissions"), "trusted-main-manual-smoke.permissions")
    _require(manual_permissions.get("contents") == "read", "manual job lacks contents:read")
    _require(
        manual_permissions.get("pull-requests") == "read",
        "manual job lacks pull-requests:read",
    )
    _require(manual_permissions.get("issues") == "write", "manual job lacks Issue evidence permission")
    _require(
        manual_permissions.get("actions") == "write",
        "trusted-main manual job is the only job that should receive Actions write",
    )

    for name, job in jobs.items():
        if name == "trusted-main-manual-smoke":
            continue
        permissions = _mapping(job.get("permissions"), f"{name}.permissions")
        _require(
            permissions.get("actions") != "write",
            f"{name} unexpectedly has Actions write authority",
        )

    manual_steps = manual.get("steps")
    _require(isinstance(manual_steps, list), "manual steps missing")
    launch_steps = [
        row
        for row in manual_steps
        if isinstance(row, dict)
        and row.get("name") == "Launch and recover the exact selector-derived gh-aw dispatch"
    ]
    _require(len(launch_steps) == 1, "manual launch step missing or duplicated")
    launch_env = _mapping(launch_steps[0].get("env"), "manual launch env")
    _require(
        launch_env.get("FI_EXTERNAL_EXECUTION_AUTHORIZED") == "1",
        "manual launch step lacks explicit execution opt-in",
    )
    _require(
        launch_env.get("FI_PR_NUMBER") == "${{ inputs.fi_pr_number }}",
        "manual PR fixture is not explicit workflow input",
    )
    _require(
        launch_env.get("FI_FEATURE_ID") == "${{ inputs.fi_feature_id }}",
        "manual Feature fixture is not explicit workflow input",
    )
    _require(
        launch_env.get("FI_TARGET_REF") == "${{ inputs.fi_target_ref }}",
        "manual ref fixture is not explicit workflow input",
    )

    dispatch_inputs = _mapping(triggers.get("workflow_dispatch"), "workflow_dispatch")
    inputs = _mapping(dispatch_inputs.get("inputs"), "workflow_dispatch.inputs")
    _require(
        set(inputs) == {"fi_pr_number", "fi_feature_id", "fi_target_ref"},
        "manual smoke input set drifted",
    )
    _require(
        all(_mapping(value, f"input.{name}").get("required") == "true" for name, value in inputs.items()),
        "all manual smoke fixture inputs must be required",
    )


def smoke_workflow_authority_ready(source: str | None) -> bool:
    if not source:
        return False
    try:
        validate_smoke_workflow_authority_text(source)
    except SmokeWorkflowAuthorityError:
        return False
    return True
