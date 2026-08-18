#!/usr/bin/env python3
"""Prove OpenAI Responses production dependencies come from the PR base.

The Responses adapter is intentionally not allowed to carry the production
Vertical/Persist/recovery authority that makes Supported mode possible. Those
modules must arrive through independently reviewed base/main work. This validator
compares the PR candidate with its explicit base SHA and fails if #233 itself
changes any protected dependency-authority path.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

# Authority implemented/reviewed outside the Responses feature. Keep this list
# exact and narrow: adapter-owned binding/harness files are intentionally not
# protected here because they are the scope of #233 itself.
PROTECTED_DEPENDENCY_PATHS = frozenset(
    {
        "scripts/operator_vertical_feature_persist_gateway.py",
        "scripts/operator_vertical_reconcile_classified.py",
        "scripts/operator_v03_write_runtime.py",
        "scripts/operator_v03_vertical_production_runtime.py",
        "scripts/operator_vertical_callback.py",
    }
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return completed.stdout


def changed_paths(*, base_sha: str, head_sha: str) -> frozenset[str]:
    if not base_sha or not head_sha:
        raise ValueError("explicit base/head SHA are required for dependency provenance")
    output = _git("diff", "--name-only", f"{base_sha}...{head_sha}")
    return frozenset(line.strip() for line in output.splitlines() if line.strip())


def validate(*, base_sha: str, head_sha: str) -> frozenset[str]:
    changed = changed_paths(base_sha=base_sha, head_sha=head_sha)
    leaked = sorted(PROTECTED_DEPENDENCY_PATHS & changed)
    if leaked:
        raise AssertionError(
            "OpenAI Responses PR carries protected production dependency authority: "
            + ", ".join(leaked)
        )
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", default="HEAD")
    args = parser.parse_args()

    changed = validate(base_sha=args.base_sha, head_sha=args.head_sha)
    print("OpenAI Responses dependency provenance validation passed")
    print(f"- compared base {args.base_sha} to head {args.head_sha}")
    print(f"- candidate changed paths: {len(changed)}")
    print("- protected Vertical/Persist/recovery authority has zero #233 delta")
    print("- Supported dependencies therefore cannot be manufactured by this PR")


if __name__ == "__main__":
    main()
