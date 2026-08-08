#!/usr/bin/env python3
"""Resolve a trusted Feature Event push into persistence or an idempotent no-op."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import NoReturn

import yaml

from apply_feature_event import validate_event

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


def eligible_event_paths(repo_dir: Path, before: str, after: str) -> list[str]:
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
    return eligible


def manifest_path_for(event_path: str) -> str:
    path = PurePosixPath(event_path)
    feature_id = path.parts[2]
    if not feature_id or feature_id in {".", ".."}:
        fail("event path contains an invalid feature id")
    return PurePosixPath("state", "features", f"{feature_id}.yaml").as_posix()


def load_yaml(path: Path) -> dict:
    if not path.is_file():
        fail(f"required repository file is missing: {path.as_posix()}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        fail(f"expected YAML object: {path.as_posix()}")
    return data


def is_exact_replay(repo_dir: Path, event_path: str) -> bool:
    event_posix = PurePosixPath(event_path)
    manifest_path = manifest_path_for(event_path)
    event = load_yaml(repo_dir / event_path)
    manifest = load_yaml(repo_dir / manifest_path)

    errors = validate_event(event)
    if errors:
        fail("invalid Feature Event in automatic push: " + "; ".join(errors))

    feature_id = event_posix.parts[2]
    event_id = event.get("id")
    if event.get("feature_id") != feature_id:
        fail(f"event feature id mismatch: path={feature_id} event={event.get('feature_id')}")
    if event_posix.stem != event_id:
        fail(f"event filename must equal event id: file={event_posix.stem} event={event_id}")
    if manifest.get("feature", {}).get("id") != feature_id:
        fail(f"manifest feature id mismatch for {event_path}")

    expected_revision = event.get("expected_revision")
    if not isinstance(expected_revision, int) or expected_revision < 0:
        fail("repository Event Inbox requires non-negative integer expected_revision")

    applied = manifest.get("applied_events", [])
    if not isinstance(applied, list) or any(not isinstance(item, str) for item in applied):
        fail("manifest applied_events must be a list of event ids")

    positions = [index for index, item in enumerate(applied) if item == event_id]
    if not positions:
        return False
    if len(positions) != 1:
        fail(f"manifest contains duplicate applied event id: {event_id}")
    if positions[0] != expected_revision:
        fail(
            f"event id {event_id} is already applied at revision slot {positions[0]}, "
            f"but event expected_revision is {expected_revision}"
        )
    if manifest.get("revision", 0) < expected_revision + 1:
        fail(f"manifest revision is inconsistent with applied event {event_id}")
    return True


def resolve(repo_dir: Path, before: str, after: str) -> tuple[str, str]:
    """Legacy single-new-event resolver used by callers/tests."""
    eligible = eligible_event_paths(repo_dir, before, after)
    if len(eligible) != 1:
        fail(
            "automatic persistence requires exactly one added/modified Feature Event in the push; "
            f"found {len(eligible)}: {eligible}"
        )
    event_path = eligible[0]
    return event_path, manifest_path_for(event_path)


def resolve_push(repo_dir: Path, before: str, after: str) -> tuple[str, str, str, list[str]]:
    eligible = eligible_event_paths(repo_dir, before, after)
    if not eligible:
        fail("automatic persistence requires at least one added/modified Feature Event in the push; found 0")

    replay = [is_exact_replay(repo_dir, path) for path in eligible]
    if all(replay):
        return "noop", "", "", eligible

    new_paths = [path for path, already_applied in zip(eligible, replay) if not already_applied]
    if len(eligible) != 1 or len(new_paths) != 1:
        fail(
            "automatic persistence requires exactly one new Feature Event unless every changed Event is already applied; "
            f"changed={eligible}, new={new_paths}"
        )

    event_path = new_paths[0]
    return "persist", event_path, manifest_path_for(event_path), eligible


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir)
    mode, event_path, manifest_path, changed_events = resolve_push(repo_dir, args.before, args.after)

    lines = [
        f"mode={mode}",
        f"event_path={event_path}",
        f"manifest_path={manifest_path}",
        f"event_count={len(changed_events)}",
    ]
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
