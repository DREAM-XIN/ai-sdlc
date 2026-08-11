#!/usr/bin/env python3
"""Closed validation boundary for Operator-submitted Feature Events."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
FEATURE_EVENT_SCHEMA = ROOT / "spec" / "feature-event.schema.json"


class TrustedFeatureEventValidationError(ValueError):
    pass


def validate_trusted_feature_event(event: dict[str, Any]) -> None:
    if not isinstance(event, dict):
        raise TrustedFeatureEventValidationError("Feature Event must be an object")
    try:
        schema = json.loads(FEATURE_EVENT_SCHEMA.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TrustedFeatureEventValidationError("Feature Event schema is unavailable") from exc
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(event), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise TrustedFeatureEventValidationError(
            f"Feature Event schema validation failed at {location}: {first.message}"
        )
