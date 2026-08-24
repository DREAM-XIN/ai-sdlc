#!/usr/bin/env python3
"""Validate GitHub Actions permission partition for the v0.3 real smoke."""
from __future__ import annotations

from pathlib import Path

from v03_real_runtime_smoke_workflow_authority import validate_smoke_workflow_authority_text

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/v03-real-runtime-effect-safety-smoke.yml"


def main():
    validate_smoke_workflow_authority_text(WORKFLOW.read_text(encoding="utf-8"))

    print("v0.3 real-runtime smoke workflow authority validation passed")
    print("- automatic push/PR job has no Actions write permission and explicitly disables execution")
    print("- non-main workflow_dispatch is rejected with read-only permissions")
    print("- Actions write exists only on explicit workflow_dispatch from refs/heads/main")
    print("- manual fixture identity is supplied only through three required workflow inputs")


if __name__ == "__main__":
    main()
