#!/usr/bin/env python3
"""Pure execution-authority gate for real v0.3 external-effect smoke runs.

Automatic push/PR validation is never launch authority. A real external dispatch
is permitted only when the reviewed workflow exists on trusted `main`, the run
was explicitly started through `workflow_dispatch`, and that manual job opts in
to external execution. This helper performs no I/O and grants no authority by
itself; the workflow's job-level GitHub token permissions remain the outer fence.
"""
from __future__ import annotations

TRUSTED_CONTROL_REF = "refs/heads/main"
TRUSTED_EXECUTION_EVENT = "workflow_dispatch"


class RealRuntimeExecutionAuthorityError(ValueError):
    pass


def require_real_runtime_execution_authority(
    *,
    github_event_name: str,
    github_ref: str,
    external_execution_authorized: str,
) -> None:
    if github_event_name != TRUSTED_EXECUTION_EVENT:
        raise RealRuntimeExecutionAuthorityError(
            "real external dispatch requires explicit workflow_dispatch"
        )
    if github_ref != TRUSTED_CONTROL_REF:
        raise RealRuntimeExecutionAuthorityError(
            "real external dispatch workflow must execute from trusted main"
        )
    if external_execution_authorized != "1":
        raise RealRuntimeExecutionAuthorityError(
            "real external dispatch job lacks explicit execution authorization"
        )
