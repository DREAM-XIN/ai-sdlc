#!/usr/bin/env python3
"""Reconstruct the candidate head from persisted production Worker facts."""
from __future__ import annotations

import re
from typing import Any, Mapping

from v03_dogfood_scenario_runner import SCENARIO_ROLE_SEQUENCES


class V03DogfoodCandidateProvenanceError(RuntimeError):
    pass


def _sha(value: Any, label: str) -> str:
    result = str(value or "").lower()
    if re.fullmatch(r"[0-9a-f]{40}", result) is None:
        raise V03DogfoodCandidateProvenanceError(f"{label} is not an exact Git SHA")
    return result


def reconstruct_candidate_head(
    events: list[dict[str, Any]], *, scenario: str, feature_id: str, pr_number: int, initial_head: str
) -> str:
    expected_roles = SCENARIO_ROLE_SEQUENCES.get(scenario)
    if expected_roles is None:
        raise V03DogfoodCandidateProvenanceError("scenario escaped frozen dogfood inventory")
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
        raise V03DogfoodCandidateProvenanceError("fixture candidate PR number is invalid")
    launches = [index for index, row in enumerate(events) if row.get("event_type") == "dispatch.launch.authorized"]
    roles = tuple(str((events[index].get("payload") or {}).get("role") or "") for index in launches)
    if roles != expected_roles:
        raise V03DogfoodCandidateProvenanceError("candidate reconstruction lacks frozen authorized launch sequence")

    current = _sha(initial_head, "initial candidate head")
    uri_pattern = re.compile(
        rf"docs/features/{re.escape(feature_id)}/worker-runs/(?P<dispatch>[^/]+)/"
        rf"developer-pr-(?P<pr>[1-9][0-9]*)-(?P<head>[0-9a-f]{{40}})-binding-[0-9a-f]{{64}}\.json"
    )
    may_advance = False
    for position, start in enumerate(launches):
        end = launches[position + 1] if position + 1 < len(launches) else len(events)
        launch = events[start].get("payload") or {}
        role = roles[position]
        launched_head = _sha(launch.get("candidate_head_sha"), "authorized launch candidate head")
        if may_advance:
            current = launched_head
            may_advance = False
        elif launched_head != current:
            raise V03DogfoodCandidateProvenanceError("authorized launch used a stale candidate head")
        segment = events[start + 1:end]
        translated = [row for row in segment if row.get("event_type") == "feature.event.translated"]
        if role != "developer":
            continue
        if scenario == "session_recovery":
            if translated:
                raise V03DogfoodCandidateProvenanceError("session recovery unexpectedly persisted Developer output")
            continue
        if len(translated) != 1:
            raise V03DogfoodCandidateProvenanceError("Developer launch lacks one translated result")
        payload = translated[0].get("payload") or {}
        event_id = str(payload.get("feature_event_id") or "")
        feature_event = payload.get("feature_event") or {}
        if not isinstance(feature_event, Mapping) or feature_event.get("id") != event_id:
            raise V03DogfoodCandidateProvenanceError("Developer translated Feature event identity is inconsistent")
        changes = feature_event.get("changes") or []
        uris = [
            (change.get("record") or {}).get("uri")
            for change in changes if isinstance(change, Mapping) and change.get("kind") == "artifact-record"
            and isinstance(change.get("record"), Mapping) and change["record"].get("type") == "implementation"
        ]
        if len(uris) != 1:
            raise V03DogfoodCandidateProvenanceError("Developer result lacks one bound implementation artifact")
        match = uri_pattern.fullmatch(str(uris[0] or ""))
        if (match is None or match.group("dispatch") != str(launch.get("dispatch_id") or "")
                or int(match.group("pr")) == pr_number):
            raise V03DogfoodCandidateProvenanceError("Developer artifact escaped exact dispatch binding")
        if not event_id or not any(
            row.get("event_type") == "persist.confirmed"
            and (row.get("payload") or {}).get("feature_event_id") == event_id
            for row in segment[segment.index(translated[0]) + 1:]
        ):
            raise V03DogfoodCandidateProvenanceError("Developer candidate head lacks persisted Feature event")
        # Developer output is a separate Draft PR based on the fixture branch.
        # The next trusted Gate launch captures the fixture PR's candidate SHA.
        may_advance = True
    if may_advance:
        raise V03DogfoodCandidateProvenanceError("Developer completion lacks a later candidate-bound Gate launch")
    return current
