#!/usr/bin/env python3
import argparse
import json
from pathlib import Path, PurePosixPath

from apply_feature_event import load_yaml, validate_event
from github_persistence import build_plan


def validate_inbox_path(path_value: str, event: dict):
    path = PurePosixPath(path_value)
    if path.is_absolute() or ".." in path.parts:
        return "event path must be repository-relative without parent traversal"
    if len(path.parts) != 4 or path.parts[:2] != ("state", "events"):
        return "event path must match state/events/<feature-id>/<event-id>.yaml"
    if path.parts[2] != event.get("feature_id"):
        return f"event path feature id mismatch: path={path.parts[2]} event={event.get('feature_id')}"
    if path.suffix not in {".yaml", ".yml"}:
        return "event path must end in .yaml or .yml"
    if path.stem != event.get("id"):
        return f"event filename must equal event id: file={path.stem} event={event.get('id')}"
    return None


def ingest(manifest, event, event_path: str, repository: str, manifest_path: str, target_ref: str, issue: int | None = None):
    event_errors = validate_event(event)
    if event_errors:
        return {"outcome": "INVALID", "errors": event_errors}
    if event.get("expected_revision") is None:
        return {
            "outcome": "INVALID",
            "errors": ["repository Event Inbox requires expected_revision"],
        }
    path_error = validate_inbox_path(event_path, event)
    if path_error:
        return {"outcome": "INVALID", "errors": [path_error]}
    return build_plan(
        manifest,
        event,
        repository=repository,
        manifest_path=manifest_path,
        target_ref=target_ref,
        issue=issue,
    )


def main():
    parser = argparse.ArgumentParser(description="Validate and ingest an AI-SDLC Feature Event from the repository inbox")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("event", type=Path)
    parser.add_argument("--event-path", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--target-ref", required=True)
    parser.add_argument("--issue", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = ingest(
        load_yaml(args.manifest),
        load_yaml(args.event),
        event_path=args.event_path,
        repository=args.repository,
        manifest_path=args.manifest_path,
        target_ref=args.target_ref,
        issue=args.issue,
    )
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
