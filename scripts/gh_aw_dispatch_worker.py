#!/usr/bin/env python3
"""Select the exact trusted gh-aw workflow for one Commander autonomous dispatch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from gh_aw_profile_readiness import readiness_from_environment
from gh_aw_profile_routing import load_routing_policy, resolve_route, resolution_payload
from gh_aw_provider_registry import RegistryValidationError, load_registry
from gh_aw_role_workers import ALLOWED_ROLE_STAGES, RoleWorkerError, require_role_worker_workflow, resolve_role_worker


class WorkerSelectionError(ValueError):
    pass


def autonomous_action(commander: dict):
    dispatches = [d for d in commander.get("dispatches", []) if d.get("runtime") == {"id": "gh-aw", "mode": "autonomous"}]
    if len(dispatches) != 1:
        raise WorkerSelectionError("worker selection requires exactly one autonomous Commander dispatch")
    return dispatches[0]["action"]


def select(commander: dict, override: str = ""):
    action = autonomous_action(commander)
    role, stage = action["role"], action["stage"]
    gate_role = (role, stage) in ALLOWED_ROLE_STAGES
    registry = load_registry()

    if override:
        try:
            if gate_role:
                role_worker = require_role_worker_workflow(role, stage, override)
                profile = registry.require_profile(role_worker.profile)
                worker = role_worker.worker_workflow
                role_worker_id = role_worker.id
            else:
                profile = registry.require_worker_workflow(override)
                worker = profile.worker_workflow
                role_worker_id = None
        except (RoleWorkerError, RegistryValidationError) as exc:
            raise WorkerSelectionError(str(exc)) from exc
        payload = {
            "status": "SELECTED",
            "selection_mode": "manual-trusted-profile",
            "role": role,
            "stage": stage,
            "selected": {
                "profile": profile.profile_id,
                "engine": profile.engine,
                "provider": profile.provider,
                "protocol": profile.protocol,
                "model": profile.model,
                "worker_workflow": worker,
                "maturity": profile.maturity,
            },
            "role_worker_id": role_worker_id,
            "fallback": False,
            "fallback_reason": None,
            "entitlement_verified": False,
        }
        return payload

    policy = load_routing_policy(registry=registry)
    readiness = readiness_from_environment(registry)
    resolution, profile = resolve_route(policy, registry, role=role, stage=stage, readiness=readiness)
    payload = resolution_payload(resolution, profile)
    payload["role"] = role
    payload["stage"] = stage
    payload["selection_mode"] = "trusted-role-routing"
    if gate_role:
        role_worker = resolve_role_worker(role, stage, profile.profile_id)
        payload["selected"]["worker_workflow"] = role_worker.worker_workflow
        payload["role_worker_id"] = role_worker.id
    else:
        payload["role_worker_id"] = None
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commander_plan", type=Path)
    parser.add_argument("--worker-override", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = select(json.loads(args.commander_plan.read_text(encoding="utf-8")), args.worker_override)
    except WorkerSelectionError as exc:
        print(json.dumps({"outcome": "INVALID", "errors": [str(exc)]}, indent=2))
        raise SystemExit(2)
    args.output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(payload["selected"]["worker_workflow"])


if __name__ == "__main__":
    main()
