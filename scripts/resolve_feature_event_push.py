#!/usr/bin/env python3
"""Resolve one eligible Feature Event from a trusted push range."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import NoReturn

SHA = re.compile(r"^[0-9a-fA-F]{40}$")
ZERO_SHA = "0" * 40


def fail(message: str) -> NoReturn:
    raise SystemExit(message)


def git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fail(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def resolve(repo_dir: Path, before: str, after: str) -> tuple[str, str]:
    if not SHA.fullmatch(before) or not SHA.fullmatch(after):
        fail("before and after must be full 40-character commit SHAs")
    if before == ZERO_SHA:
        fail("automatic persistence does not accept a branch-creation push; commit the event in a subsequent push")

    changed = [
        line.strip()
        for line in git(repo_dir, "diff", "--name-only", "--diff-filter=AM", before, after).splitlines()
        if line.strip()
    ]
    eligible: list[str] = []
    for raw in changed:
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            continue
        if len(path.parts) != 4 or path.parts[:2] != ("state", "events"):
            continue
        if path.suffix not in {".yaml", ".yml"}:
            continue
        eligible.append(path.as_posix())

    if len(eligible) != 1:
        fail(
            "automatic persistence requires exactly one added/modified Feature Event in the push; "
            f"found {len(eligible)}: {eligible}"
        )

    event_path = PurePosixPath(eligible[0])
    feature_id = event_path.parts[2]
    if not feature_id or feature_id in {".", ".."}:
        fail("event path contains an invalid feature id")
    manifest_path = PurePosixPath("state", "features", f"{feature_id}.yaml").as_posix()
    return event_path.as_posix(), manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)
    event_path, manifest_path = resolve(repo_dir, args.before, args.after)

    lines = [f"event_path={event_path}", f"manifest_path={manifest_path}"]
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
