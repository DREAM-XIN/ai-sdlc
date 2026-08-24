#!/usr/bin/env python3
"""Deterministic validation for the real-runtime external execution authority gate."""
from __future__ import annotations

from v03_real_runtime_execution_authority import (
    RealRuntimeExecutionAuthorityError,
    require_real_runtime_execution_authority,
)


def accepted(event="workflow_dispatch", ref="refs/heads/main", authorized="1"):
    require_real_runtime_execution_authority(
        github_event_name=event,
        github_ref=ref,
        external_execution_authorized=authorized,
    )


def rejected(event, ref, authorized):
    try:
        accepted(event, ref, authorized)
    except RealRuntimeExecutionAuthorityError:
        return
    raise AssertionError((event, ref, authorized))


def main():
    accepted()
    rejected("push", "refs/heads/main", "1")
    rejected("pull_request", "refs/pull/231/merge", "1")
    rejected("workflow_dispatch", "refs/heads/verification/v0.3-real-runtime-effect-safety-221", "1")
    rejected("workflow_dispatch", "refs/heads/main", "0")
    rejected("workflow_dispatch", "refs/heads/main", "")

    print("v0.3 real-runtime execution authority validation passed")
    print("- only explicit workflow_dispatch on refs/heads/main with job opt-in is accepted")
    print("- push, pull_request, non-main manual runs and missing opt-in all fail closed")


if __name__ == "__main__":
    main()
