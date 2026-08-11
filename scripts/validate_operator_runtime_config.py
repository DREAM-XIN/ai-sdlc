#!/usr/bin/env python3
"""Validate the closed trusted Operator runtime configuration contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from operator_production_runtime import TrustedOperatorRuntimeConfig

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spec" / "operator" / "runtime-config.schema.json"
EXAMPLE = ROOT / "examples" / "operator" / "production-runtime-config.yaml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def schema_errors(doc):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return list(Draft202012Validator(schema).iter_errors(doc))


def parser_accepts(doc):
    return TrustedOperatorRuntimeConfig.from_mapping(doc, config_base=EXAMPLE.parent)


def main():
    example = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    errors = schema_errors(example)
    require(not errors, "; ".join(error.message for error in errors))
    parsed = parser_accepts(example)
    require(parsed.target_repository == "example-org/product-repo", parsed)
    require(parsed.store_repository == "example-org/control-repo", parsed)
    require(parsed.target_repository != parsed.store_repository, "example must exercise split target/Store trust domains")
    require(parsed.feature_ref("F-EXAMPLE-0001") == "feature/F-EXAMPLE-0001", parsed)

    forbidden_keys = (
        "github_token",
        "store_token",
        "target_read_token",
        "trusted_scope",
        "trusted_identity",
        "authorization_policy",
        "client_selected_backend",
        "feature_event_writer",
    )
    for key in forbidden_keys:
        mutated = copy.deepcopy(example)
        mutated[key] = "attacker-controlled"
        require(schema_errors(mutated), f"schema accepted forbidden authority field {key}")
        try:
            parser_accepts(mutated)
            raise AssertionError(f"parser accepted forbidden authority field {key}")
        except ValueError:
            pass

    bad_feature_ref = copy.deepcopy(example)
    bad_feature_ref["feature_refs"] = {"F-EXAMPLE-0001": "refs/heads/attacker"}
    require(schema_errors(bad_feature_ref), "schema accepted refs/ Feature binding")
    try:
        parser_accepts(bad_feature_ref)
        raise AssertionError("parser accepted refs/ Feature binding")
    except ValueError:
        pass

    duplicate_ref = copy.deepcopy(example)
    duplicate_ref["feature_refs"] = {
        "F-EXAMPLE-0001": "feature/shared",
        "F-EXAMPLE-0002": "feature/shared",
    }
    # JSON Schema permits syntactically valid repeated values; semantic parser
    # must enforce the trusted one-to-one Feature/ref mapping.
    require(not schema_errors(duplicate_ref), "schema should leave cross-property uniqueness to semantic parser")
    try:
        parser_accepts(duplicate_ref)
        raise AssertionError("semantic parser accepted duplicate Feature target refs")
    except ValueError:
        pass

    no_features = copy.deepcopy(example)
    no_features["feature_refs"] = {}
    require(schema_errors(no_features), "runtime config unexpectedly allowed an empty Feature scope")

    print("Operator trusted runtime configuration validation passed")
    print("- closed schema and semantic parser agree on supported fields")
    print("- secrets/policy/backend authority cannot enter the YAML contract")
    print("- Feature/ref map is non-empty and semantically one-to-one")
    print("- example exercises distinct target and control/Store repositories")


if __name__ == "__main__":
    main()
