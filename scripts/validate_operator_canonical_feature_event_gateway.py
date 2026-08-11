#!/usr/bin/env python3
"""Validate canonical Feature Event bytes/digest are stable across reconstruction order."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import yaml

from operator_canonical_feature_event_gateway import CanonicalExactRevisionGitHubFeatureEventGateway
from operator_feature_event_validation import TrustedFeatureEventValidationError, validate_trusted_feature_event

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def find_valid_event():
    candidates = []
    for base in (ROOT / "examples", ROOT / "events", ROOT / "tests" / "fixtures"):
        if base.exists():
            candidates.extend(base.rglob("*.yaml"))
    for path in candidates:
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                continue
            validate_trusted_feature_event(doc)
            return path, doc
        except (TrustedFeatureEventValidationError, yaml.YAMLError, OSError):
            continue
    raise AssertionError("no repository Feature Event fixture satisfies canonical schema")


def main():
    path, event = find_valid_event()
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
    require(text_a == yaml.safe_dump(event, sort_keys=True, allow_unicode=True, default_flow_style=False), "canonical serializer drifted")

    print("Canonical Feature Event identity validation passed")
    print(f"- fixture: {path.relative_to(ROOT)}")
    print("- top-level mapping order: irrelevant")
    print("- exact bytes/digest: deterministic across reconstruction")


if __name__ == "__main__":
    main()
