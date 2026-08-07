#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/gh_aw_runtime_preflight.py"
WORKFLOW = ROOT / ".github/workflows/ai-sdlc-gh-aw-preflight.yml"
PROFILES = ("copilot", "codex", "claude", "gemini")


def run(profile: str, present: bool) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), profile, "--credential-present", str(present).lower()],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    for profile in PROFILES:
        missing = run(profile, False)
        if missing.get("status") != "MISSING_CREDENTIAL":
            fail(f"{profile}: missing credential must not be considered ready")
        if missing.get("entitlement_verified") is not False:
            fail(f"{profile}: preflight must never claim entitlement verification")

        present = run(profile, True)
        if present.get("status") != "READY_FOR_ENTITLEMENT_PROBE":
            fail(f"{profile}: valid lock + credential must reach entitlement probe readiness")
        if present.get("compiler_version") != "v0.83.4" or present.get("lock_strict") is not True:
            fail(f"{profile}: preflight must verify pinned strict compiler metadata")
        if present.get("entitlement_verified") is not False:
            fail(f"{profile}: credential presence must not imply provider entitlement")

    text = WORKFLOW.read_text(encoding="utf-8")
    if "permissions:\n  contents: read" not in text:
        fail("preflight workflow must remain repository read-only")
    for forbidden in ("gh workflow run ai-sdlc-gh-aw-dispatch", "contents: write", "state/features/", "state/events/"):
        if forbidden in text:
            fail(f"preflight workflow contains forbidden mutation/dispatch marker: {forbidden}")
    if "secrets.COPILOT_GITHUB_TOKEN != ''" not in text:
        fail("preflight workflow must test credential presence without exposing secret values")
    if "READY_FOR_ENTITLEMENT_PROBE" not in text:
        fail("preflight workflow must explain entitlement remains unverified")

    print("gh-aw provider-neutral runtime preflight checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
