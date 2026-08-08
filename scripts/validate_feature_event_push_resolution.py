#!/usr/bin/env python3
"""Validate automatic Feature Event push resolution against real git ranges."""

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

from resolve_feature_event_push import ZERO_SHA, resolve


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    run(repo, "add", ".")
    run(repo, "commit", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def require_rejected(fn, expected: str) -> None:
    try:
        fn()
    except SystemExit as exc:
        if expected not in str(exc):
            raise AssertionError(f"expected rejection containing {expected!r}, got {exc!r}") from exc
    else:
        raise AssertionError(f"expected rejection containing {expected!r}")


def main() -> None:
    with TemporaryDirectory() as temp:
        repo = Path(temp)
        run(repo, "init")
        run(repo, "config", "user.name", "AI-SDLC Test")
        run(repo, "config", "user.email", "ai-sdlc-test@example.invalid")
        (repo / "README.md").write_text("base\n", encoding="utf-8")
        base = commit(repo, "base")

        event_dir = repo / "state" / "events" / "F-0100"
        event_dir.mkdir(parents=True)
        (event_dir / "EVT-ONE.yaml").write_text("version: 0.1.0\n", encoding="utf-8")
        one = commit(repo, "one event")
        event_path, manifest_path = resolve(repo, base, one)
        assert event_path == "state/events/F-0100/EVT-ONE.yaml"
        assert manifest_path == "state/features/F-0100.yaml"

        (event_dir / "EVT-TWO.yaml").write_text("version: 0.1.0\n", encoding="utf-8")
        (event_dir / "EVT-THREE.yml").write_text("version: 0.1.0\n", encoding="utf-8")
        many = commit(repo, "two events")
        require_rejected(lambda: resolve(repo, one, many), "exactly one")

        (repo / "README.md").write_text("non-event\n", encoding="utf-8")
        none = commit(repo, "no event")
        require_rejected(lambda: resolve(repo, many, none), "found 0")
        require_rejected(lambda: resolve(repo, ZERO_SHA, none), "branch-creation push")

    print("Automatic Feature Event push resolution checks passed")


if __name__ == "__main__":
    main()
