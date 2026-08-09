#!/usr/bin/env python3
"""Static validation helpers for trusted compiled gh-aw worker locks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json

from gh_aw_provider_registry import EngineProfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW_DIR = ROOT / ".github/workflows"
METADATA_PREFIX = "# gh-aw-metadata: "
PINNED_COMPILER = "v0.83.4"
METADATA_SCHEMA = "v4"


class CompiledWorkerError(ValueError):
    pass


class MissingCompiledWorkerError(CompiledWorkerError):
    pass


class InvalidCompiledWorkerError(CompiledWorkerError):
    pass


@dataclass(frozen=True)
class CompiledWorker:
    profile: EngineProfile
    path: Path
    metadata: dict
    text: str


def load_compiled_worker(
    profile: EngineProfile,
    workflow_dir: Path | str = DEFAULT_WORKFLOW_DIR,
) -> CompiledWorker:
    path = Path(workflow_dir) / profile.worker_workflow
    if not path.is_file():
        raise MissingCompiledWorkerError(
            f"profile {profile.profile_id!r}: registered compiled worker is missing"
        )
    try:
        text = path.read_text(encoding="utf-8")
        first = text.splitlines()[0]
    except (OSError, IndexError) as exc:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: cannot read compiled worker metadata"
        ) from exc
    if not first.startswith(METADATA_PREFIX):
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker metadata header is missing"
        )
    try:
        metadata = json.loads(first[len(METADATA_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker metadata is invalid JSON"
        ) from exc
    if not isinstance(metadata, dict):
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker metadata must be an object"
        )
    if metadata.get("strict") is not True:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker is not strict"
        )
    if metadata.get("compiler_version") != PINNED_COMPILER:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker compiler pin drifted"
        )
    if metadata.get("schema_version") != METADATA_SCHEMA:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker metadata schema drifted"
        )
    if metadata.get("agent_id") != profile.engine:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker engine identity drifted"
        )
    if profile.engine_version is not None:
        versions = metadata.get("engine_versions")
        if not isinstance(versions, dict) or versions.get(profile.engine) != profile.engine_version:
            raise InvalidCompiledWorkerError(
                f"profile {profile.profile_id!r}: compiled worker engine version drifted"
            )
    if profile.model is not None and metadata.get("agent_model") != profile.model:
        raise InvalidCompiledWorkerError(
            f"profile {profile.profile_id!r}: compiled worker effective model metadata drifted"
        )
    return CompiledWorker(profile=profile, path=path, metadata=metadata, text=text)
