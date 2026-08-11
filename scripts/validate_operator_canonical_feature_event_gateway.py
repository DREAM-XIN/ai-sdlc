#!/usr/bin/env python3
"""Validate canonical Feature Event bytes/digest are stable across reconstruction order."""
from __future__ import annotations

from collections import OrderedDict

import yaml

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_feature_event_validation import validate_trusted_feature_event


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def canonical_fixture():
    event = {
        "version": "0.1.0",
        "id": "EVT-CANONICAL-FIXTURE-0001",
        "feature_id": "F-CANONICAL-FIXTURE-0001",
        "expected_revision": 3,
        "occurred_at": "2026-08-11T05:00:00Z",
        "changes": [
            {"kind": "stage", "id": "implementation", "status": "WORKING"},
        ],
    }
    validate_trusted_feature_event(event)
    return event


def main():
    event = canonical_fixture()
    feature_id = str(event["feature_id"])
    expected_revision = int(event["expected_revision"])
    reordered = OrderedDict(reversed(list(event.items())))

    event_id_a, text_a = CanonicalExactRevisionGitHubFeatureEventGateway._validate_event(
        event,
        feature_id=feature_id,
        expected_revision=expected_revision,
    )
    event_id_b, text_b = CanonicalExactRevisionGitHubFeatureEventGateway._validate_event(
        dict(reordered),
        feature_id=feature_id,
        expected_revision=expected_revision,
    )
    require(event_id_a == event_id_b, "reconstructed Event id changed")
    require(text_a == text_b, "canonical Event bytes depend on mapping insertion order")
    require(
        text_a == yaml.safe_dump(event, sort_keys=True, allow_unicode=True, default_flow_style=False),
        "canonical serializer drifted",
    )

    print("Canonical Feature Event identity validation passed")
    print("- fixture: self-contained schema-valid frozen-contract Event")
    print("- top-level mapping order: irrelevant")
    print("- exact bytes/digest: deterministic across reconstruction")


if __name__ == "__main__":
    main()
