#!/usr/bin/env python3
"""Resolve exact production Worker bindings before v0.3 real dogfood.

This is a presence-only, zero-effect prerequisite check. It never calls a model,
dispatches a Worker, starts an Operation, mutates protected Operator Store state,
writes a Feature Event, or creates dogfood/release evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping

from gh_aw_profile_readiness import readiness_from_presence
from gh_aw_profile_routing import load_routing_policy, resolve_route
from gh_aw_provider_registry import load_registry
from gh_aw_role_workers import resolve_role_worker

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_METADATA_PREFIX = "# gh-aw-metadata: "
_MANIFEST_PREFIX = "# gh-aw-manifest: "
STRICT_COMPILER_VERSION = "v0.83.4"


class V03DogfoodExecutionBindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class DogfoodExecutionBinding:
    role: str
    stage: str
    rule_id: str
    candidate_order: tuple[str, ...]
    selected_profile: str
    engine: str
    provider: str
    protocol: str
    model: str | None
    worker_workflow: str
    credential_source: str
    accepted_credential_identities: tuple[str, ...]
    present_credential_identities: tuple[str, ...]
    fallback: bool
    fallback_reason: str | None
    specialized_role_worker: bool


# These are the frozen production contexts required by the three release dogfoods.
_CONTEXTS = (
    ("developer", "implementation", "implementation-developer", ("codex", "copilot"), False),
    ("reviewer", "code-review", "code-review-reviewer", ("claude", "copilot"), True),
    ("qa", "verification", "verification-qa", ("gemini", "copilot"), True),
)


def _bool(value: object, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise V03DogfoodExecutionBindingError(f"trusted presence signal {label!r} must be boolean")


def credential_identities(registry) -> tuple[str, ...]:
    ordered: list[str] = []
    for profile in registry.profiles:
        for identity in (profile.credential, *profile.credential_aliases):
            if identity not in ordered:
                ordered.append(identity)
    return tuple(ordered)


def presence_from_environment(registry, environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    source = os.environ if environ is None else environ
    result: dict[str, bool] = {}
    for identity in credential_identities(registry):
        env_name = f"HAS_{identity}"
        if env_name not in source:
            raise V03DogfoodExecutionBindingError(
                f"missing trusted credential-presence signal {env_name!r}"
            )
        result[identity] = _bool(source[env_name], env_name)
    return result


def _json_header(path: Path, line_number: int, prefix: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise V03DogfoodExecutionBindingError(f"trusted role-worker file missing or non-regular: {path.name}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= line_number or not lines[line_number].startswith(prefix):
        raise V03DogfoodExecutionBindingError(f"trusted role-worker header missing: {path.name}")
    try:
        value = json.loads(lines[line_number][len(prefix) :])
    except json.JSONDecodeError as exc:
        raise V03DogfoodExecutionBindingError(f"trusted role-worker header invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise V03DogfoodExecutionBindingError(f"trusted role-worker header is not an object: {path.name}")
    return value


def _require_specialized_role_worker(*, role: str, stage: str, profile) -> str:
    worker = resolve_role_worker(role, stage, profile.profile_id)
    source = ROOT / worker.worker_source
    lock = WORKFLOW_ROOT / worker.worker_workflow
    if not source.is_file() or source.is_symlink():
        raise V03DogfoodExecutionBindingError(
            f"trusted specialized Worker source missing or non-regular: {worker.worker_source}"
        )
    metadata = _json_header(lock, 0, _METADATA_PREFIX)
    manifest = _json_header(lock, 1, _MANIFEST_PREFIX)
    if (
        metadata.get("schema_version") != "v4"
        or metadata.get("strict") is not True
        or metadata.get("compiler_version") != STRICT_COMPILER_VERSION
        or metadata.get("agent_id") != profile.engine
    ):
        raise V03DogfoodExecutionBindingError(
            f"trusted specialized Worker compiler identity drifted: {worker.worker_workflow}"
        )
    secrets = manifest.get("secrets")
    if not isinstance(secrets, list) or profile.credential not in secrets:
        raise V03DogfoodExecutionBindingError(
            f"trusted specialized Worker does not declare selected profile credential: {worker.worker_workflow}"
        )
    return worker.worker_workflow


def resolve_dogfood_execution_bindings(
    presence: Mapping[str, object],
) -> tuple[DogfoodExecutionBinding, ...]:
    """Resolve all three production role bindings from presence-only signals."""
    registry = load_registry()
    policy = load_routing_policy(registry=registry)

    expected_presence = set(credential_identities(registry))
    if set(presence) != expected_presence:
        missing = sorted(expected_presence - set(presence))
        extra = sorted(set(presence) - expected_presence)
        raise V03DogfoodExecutionBindingError(
            f"credential-presence contract drifted; missing={missing}, extra={extra}"
        )
    normalized = {identity: _bool(presence[identity], identity) for identity in expected_presence}
    readiness = readiness_from_presence(registry, normalized)

    bindings: list[DogfoodExecutionBinding] = []
    for role, stage, expected_rule, expected_candidates, specialized in _CONTEXTS:
        resolution, profile = resolve_route(
            policy,
            registry,
            role=role,
            stage=stage,
            readiness=readiness,
            validate_compiled_worker=True,
        )
        if resolution.rule_id != expected_rule or resolution.candidate_order != expected_candidates:
            raise V03DogfoodExecutionBindingError(
                f"frozen v0.3 production route drifted for {role}/{stage}"
            )

        accepted = (profile.credential, *profile.credential_aliases)
        present = tuple(identity for identity in accepted if normalized[identity])
        if not present:
            raise V03DogfoodExecutionBindingError(
                f"selected profile lacks a present credential for {role}/{stage}"
            )

        if specialized:
            worker_workflow = _require_specialized_role_worker(
                role=role,
                stage=stage,
                profile=profile,
            )
        else:
            # resolve_route(validate_compiled_worker=True) already verifies this exact
            # registered generic Developer lock against the trusted Registry.
            worker_workflow = profile.worker_workflow

        bindings.append(
            DogfoodExecutionBinding(
                role=role,
                stage=stage,
                rule_id=resolution.rule_id,
                candidate_order=resolution.candidate_order,
                selected_profile=profile.profile_id,
                engine=profile.engine,
                provider=profile.provider,
                protocol=profile.protocol,
                model=profile.model,
                worker_workflow=worker_workflow,
                credential_source=profile.credential_source,
                accepted_credential_identities=accepted,
                present_credential_identities=present,
                fallback=resolution.fallback,
                fallback_reason=resolution.fallback_reason,
                specialized_role_worker=specialized,
            )
        )

    if {(row.role, row.stage) for row in bindings} != {
        ("developer", "implementation"),
        ("reviewer", "code-review"),
        ("qa", "verification"),
    }:
        raise V03DogfoodExecutionBindingError("dogfood execution binding set is incomplete")
    return tuple(bindings)


def require_trusted_main_context(
    *,
    event_name: str,
    ref: str,
    workflow_sha: str,
    checkout_sha: str,
) -> str:
    if event_name != "workflow_dispatch":
        raise V03DogfoodExecutionBindingError("dogfood binding preflight requires workflow_dispatch")
    if ref != "refs/heads/main":
        raise V03DogfoodExecutionBindingError("dogfood binding preflight is authorized only from refs/heads/main")
    workflow_sha = str(workflow_sha or "").lower()
    checkout_sha = str(checkout_sha or "").lower()
    if not _SHA40.fullmatch(workflow_sha) or not _SHA40.fullmatch(checkout_sha):
        raise V03DogfoodExecutionBindingError("exact trusted-main SHA is required")
    if workflow_sha != checkout_sha:
        raise V03DogfoodExecutionBindingError("checked-out installation differs from workflow trusted-main SHA")
    return workflow_sha


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise V03DogfoodExecutionBindingError("cannot resolve exact checkout HEAD")
    return completed.stdout.strip().lower()


def render_result(*, installation_sha: str, bindings: tuple[DogfoodExecutionBinding, ...]) -> dict[str, Any]:
    return {
        "contract": "ai-sdlc.v0.3-dogfood-execution-bindings/v1",
        "status": "READY",
        "installation_commit_sha": installation_sha,
        "binding_count": len(bindings),
        "bindings": [
            {
                **asdict(binding),
                "candidate_order": list(binding.candidate_order),
                "accepted_credential_identities": list(binding.accepted_credential_identities),
                "present_credential_identities": list(binding.present_credential_identities),
            }
            for binding in bindings
        ],
        "entitlement_verified": False,
        "model_called": False,
        "worker_dispatched": False,
        "operator_store_mutated": False,
        "feature_event_written": False,
        "dogfood_evidence_created": False,
        "release_evidence_created": False,
        "release_status_changed": False,
    }


def main() -> int:
    try:
        registry = load_registry()
        presence = presence_from_environment(registry)
        installation_sha = require_trusted_main_context(
            event_name=os.environ.get("GITHUB_EVENT_NAME", ""),
            ref=os.environ.get("GITHUB_REF", ""),
            workflow_sha=os.environ.get("GITHUB_SHA", ""),
            checkout_sha=_git_head(),
        )
        bindings = resolve_dogfood_execution_bindings(presence)
        result = render_result(installation_sha=installation_sha, bindings=bindings)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "contract": "ai-sdlc.v0.3-dogfood-execution-bindings/v1",
                    "status": "BLOCKED",
                    "error": str(exc),
                    "dogfood_evidence_created": False,
                    "release_evidence_created": False,
                    "release_status_changed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
