#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spec" / "dispatch-policy.schema.json"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_policy(policy):
    with SCHEMA.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    errors = []
    for error in Draft202012Validator(schema).iter_errors(policy):
        location = ".".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"policy:{location}: {error.message}")
    route_ids = [route.get("id") for route in policy.get("routes", [])]
    duplicates = sorted({item for item in route_ids if route_ids.count(item) > 1})
    errors.extend(f"policy: duplicate route id: {item}" for item in duplicates)
    return errors


def route_matches(route, action, risk):
    values = {"stage": action.get("stage"), "role": action.get("role"), "risk": risk}
    return all(values.get(key) == expected for key, expected in route["match"].items())


def select_runtime(action, risk, policy):
    errors = validate_policy(policy)
    if errors:
        return {"outcome": "INVALID", "errors": errors}

    candidates = [route for route in policy["routes"] if route_matches(route, action, risk)]
    if not candidates:
        return {
            "outcome": "INVALID",
            "errors": [f"no runtime route for stage={action.get('stage')} role={action.get('role')} risk={risk}"],
        }

    max_priority = max(route.get("priority", 0) for route in candidates)
    winners = [route for route in candidates if route.get("priority", 0) == max_priority]
    runtimes = {(route["runtime"]["id"], route["runtime"]["mode"]) for route in winners}
    if len(runtimes) != 1:
        return {
            "outcome": "INVALID",
            "errors": [
                "ambiguous runtime routes at priority "
                + str(max_priority)
                + ": "
                + ", ".join(sorted(route["id"] for route in winners))
            ],
        }

    runtime_id, mode = next(iter(runtimes))
    return {
        "outcome": "ROUTED",
        "route_ids": sorted(route["id"] for route in winners),
        "runtime": {"id": runtime_id, "mode": mode},
    }


def main():
    parser = argparse.ArgumentParser(description="Select a runtime for one AI-SDLC dispatch action")
    parser.add_argument("--policy", type=Path, default=ROOT / "dispatch" / "default.yaml")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--risk", choices=["low", "medium", "high"], required=True)
    args = parser.parse_args()

    result = select_runtime({"stage": args.stage, "role": args.role}, args.risk, load_yaml(args.policy))
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["outcome"] == "INVALID":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
