#!/usr/bin/env python3
from pathlib import Path

import yaml

from validate_feature_manifest import validate_manifest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "features"


def load_yaml(path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    valid = load_yaml(EXAMPLES / "feature-manifest-pass.yaml")
    invalid = load_yaml(EXAMPLES / "feature-manifest-invalid.yaml")

    valid_errors = validate_manifest(valid)
    if valid_errors:
        print("Expected valid manifest failed:")
        for error in valid_errors:
            print(f"- {error}")
        raise SystemExit(1)

    invalid_errors = validate_manifest(invalid)
    if not invalid_errors:
        print("Expected invalid manifest unexpectedly passed")
        raise SystemExit(1)

    required_fragments = [
        "unknown current_stage",
        "duplicate task id",
        "is DONE but gate requirement-gate is FAIL",
        "unknown evidence EVID-MISSING",
        "workflow is DONE with unfinished stages",
        "workflow is DONE with non-passing gates",
    ]
    joined = "\n".join(invalid_errors)
    missing = [fragment for fragment in required_fragments if fragment not in joined]
    if missing:
        print("Invalid manifest did not trigger expected semantic failures:")
        for fragment in missing:
            print(f"- {fragment}")
        raise SystemExit(1)

    print("Feature Manifest fixtures passed structural and semantic validation tests")


if __name__ == "__main__":
    main()
