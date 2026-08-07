#!/usr/bin/env python3
"""Evaluate an AI-SDLC gate against a durable state snapshot.

The v0.1 evaluator intentionally uses a small contract: every gate check declares a
canonical target string and a state snapshot declares the set of satisfied targets.
Adapters are responsible for translating GitHub/CI/review facts into those targets.
"""

import argparse
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATES = ROOT / "gates" / "core-gates.yaml"


def load_yaml(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def evaluate(gate_id: str, state: dict, gates_path: Path = DEFAULT_GATES):
    gate_doc = load_yaml(gates_path)
    try:
        gate = gate_doc["gates"][gate_id]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown gate: {gate_id}") from exc

    satisfied = set(state.get("satisfied", []))
    checks = gate.get("checks", [])
    results = []
    for check in checks:
        target = check["target"]
        results.append({
            "type": check.get("type", "unknown"),
            "target": target,
            "pass": target in satisfied,
        })

    policy = gate.get("pass_policy", "all-required")
    if policy in {"all", "all-required"}:
        passed = all(result["pass"] for result in results)
    else:
        raise ValueError(f"unsupported pass_policy in v0.1 evaluator: {policy}")

    return {
        "gate": gate_id,
        "pass": passed,
        "human_approval_required": bool(gate.get("human_approval", False)),
        "checks": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", required=True)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    args = parser.parse_args()

    result = evaluate(args.gate, load_yaml(args.state), args.gates)
    print(yaml.safe_dump(result, sort_keys=False).strip())
    sys.exit(0 if result["pass"] else 2)


if __name__ == "__main__":
    main()
