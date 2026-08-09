#!/usr/bin/env python3
"""Enrich a trusted Developer result Event with immutable implementation candidate records."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from apply_feature_event import validate_event
from gh_aw_candidate import CandidateError, candidate_artifact_changes


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def enrich(manifest: dict, event: dict, *, repository: str, pr_number: int, head_sha: str):
    changes = candidate_artifact_changes(manifest, repository, pr_number, head_sha)
    # Candidate records are trusted control-plane context and must be present before the
    # implementation/remediation completion change in the same atomic Feature Event.
    event = dict(event)
    event["changes"] = changes + list(event.get("changes", []))
    errors = validate_event(event)
    if errors:
        raise CandidateError("candidate-enriched Event is invalid: " + "; ".join(errors))
    return event


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("event", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = enrich(
            load_yaml(args.manifest), load_yaml(args.event), repository=args.repository,
            pr_number=args.pr_number, head_sha=args.head_sha,
        )
    except CandidateError as exc:
        raise SystemExit(str(exc)) from exc
    args.output.write_text(yaml.safe_dump(result, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main()
