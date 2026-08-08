#!/usr/bin/env python3
"""Validate automatic Feature Event push resolution against real git ranges."""

from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess

import yaml

from resolve_feature_event_push import ZERO_SHA, resolve, resolve_push


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


def init_repo(repo: Path) -> None:
    run(repo, "init")
    run(repo, "config", "user.name", "AI-SDLC Test")
    run(repo, "config", "user.email", "ai-sdlc-test@example.invalid")


def require_rejected(fn, expected: str) -> None:
    try:
        fn()
    except SystemExit as exc:
        if expected not in str(exc):
            raise AssertionError(f"expected rejection containing {expected!r}, got {exc!r}") from exc
    else:
        raise AssertionError(f"expected rejection containing {expected!r}")


def event(feature_id: str, event_id: str, expected_revision: int) -> dict:
    return {
        "version": "0.1.0",
        "id": event_id,
        "feature_id": feature_id,
        "expected_revision": expected_revision,
        "occurred_at": "2026-08-08T00:00:00Z",
        "changes": [
            {"kind": "stage", "id": "implementation", "status": "WORKING"},
        ],
    }


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def main() -> None:
    # Legacy exact-one resolution remains stable for callers that only need changed paths.
    with TemporaryDirectory() as temp:
        repo = Path(temp)
        init_repo(repo)
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

    # One genuinely new Event is still the only persistable automatic mutation.
    with TemporaryDirectory() as temp:
        repo = Path(temp)
        init_repo(repo)
        feature_id = "F-0200"
        write_yaml(
            repo / "state" / "features" / f"{feature_id}.yaml",
            {"revision": 0, "feature": {"id": feature_id}, "applied_events": []},
        )
        base = commit(repo, "manifest")
        write_yaml(repo / "state" / "events" / feature_id / "EVT-NEW.yaml", event(feature_id, "EVT-NEW", 0))
        pushed = commit(repo, "new event")
        mode, event_path, manifest_path, changed = resolve_push(repo, base, pushed)
        assert mode == "persist"
        assert event_path == f"state/events/{feature_id}/EVT-NEW.yaml"
        assert manifest_path == f"state/features/{feature_id}.yaml"
        assert changed == [event_path]

    # Archive merges may add multiple historical Event files at once. They are a no-op only
    # when every Event is already recorded at its exact applied revision slot.
    with TemporaryDirectory() as temp:
        repo = Path(temp)
        init_repo(repo)
        feature_id = "F-0300"
        write_yaml(
            repo / "state" / "features" / f"{feature_id}.yaml",
            {
                "revision": 2,
                "feature": {"id": feature_id},
                "applied_events": ["EVT-A", "EVT-B"],
            },
        )
        base = commit(repo, "final manifest before event archive")
        write_yaml(repo / "state" / "events" / feature_id / "EVT-A.yaml", event(feature_id, "EVT-A", 0))
        write_yaml(repo / "state" / "events" / feature_id / "EVT-B.yaml", event(feature_id, "EVT-B", 1))
        archived = commit(repo, "archive two applied events")
        mode, event_path, manifest_path, changed = resolve_push(repo, base, archived)
        assert mode == "noop"
        assert event_path == ""
        assert manifest_path == ""
        assert changed == [
            f"state/events/{feature_id}/EVT-A.yaml",
            f"state/events/{feature_id}/EVT-B.yaml",
        ]

        # Reusing an applied id with a different revision slot is not an idempotent replay.
        write_yaml(repo / "state" / "events" / feature_id / "EVT-A.yaml", event(feature_id, "EVT-A", 1))
        collision = commit(repo, "corrupt archived event revision")
        require_rejected(lambda: resolve_push(repo, archived, collision), "already applied at revision slot 0")

    # A multi-Event push that mixes archived history with a new Event remains rejected.
    with TemporaryDirectory() as temp:
        repo = Path(temp)
        init_repo(repo)
        feature_id = "F-0400"
        write_yaml(
            repo / "state" / "features" / f"{feature_id}.yaml",
            {"revision": 1, "feature": {"id": feature_id}, "applied_events": ["EVT-A"]},
        )
        base = commit(repo, "manifest with one applied event")
        write_yaml(repo / "state" / "events" / feature_id / "EVT-A.yaml", event(feature_id, "EVT-A", 0))
        write_yaml(repo / "state" / "events" / feature_id / "EVT-NEW.yaml", event(feature_id, "EVT-NEW", 1))
        mixed = commit(repo, "archive plus new event")
        require_rejected(lambda: resolve_push(repo, base, mixed), "unless every changed Event is already applied")

    print("Automatic Feature Event push resolution and archive-idempotency checks passed")


if __name__ == "__main__":
    main()
