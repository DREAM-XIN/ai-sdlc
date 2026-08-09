#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import subprocess
import sys

import yaml

from gh_aw_provider_registry import RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
LEGACY_PROFILE_PREFIX = ("copilot", "codex", "claude", "gemini", "deepseek")
CERTIFIED_COMPATIBLE_PREFIX = ("deepseek", "qwen", "glm", "minimax")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_invalid(fn, marker: str) -> None:
    try:
        fn()
    except RegistryValidationError as exc:
        require(marker in str(exc), f"registry failure should identify {marker!r}: {exc}")
    else:
        raise AssertionError(f"expected registry validation failure containing {marker!r}")


def write_fixture(root: Path, data: dict) -> Path:
    registry = root / "runtimes/gh-aw/engine-profiles.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    for cfg in data.get("profiles", {}).values():
        if not isinstance(cfg, dict):
            continue
        source = cfg.get("worker_source")
        if isinstance(source, str) and source.startswith(".github/workflows/") and ".." not in source:
            path = root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    return registry


def main() -> int:
    current = load_registry(REGISTRY, repo_root=ROOT)
    profile_ids = current.profile_ids()
    require(
        profile_ids[: len(LEGACY_PROFILE_PREFIX)] == LEGACY_PROFILE_PREFIX,
        "legacy compatibility profile ordering drifted",
    )
    compatible_ids = tuple(p.profile_id for p in current.openai_compatible_profiles())
    require(
        compatible_ids[: len(CERTIFIED_COMPATIBLE_PREFIX)] == CERTIFIED_COMPATIBLE_PREFIX,
        "certified compatible-provider prefix drifted",
    )
    require(
        current.require_worker_workflow("ai-sdlc-gh-aw-worker-deepseek.lock.yml").profile_id
        == "deepseek",
        "worker index lost exact identity",
    )
    for profile_id in ("qwen", "glm", "minimax"):
        profile = current.require_profile(profile_id)
        require(
            profile.is_openai_compatible and profile.maturity == "experimental",
            f"{profile_id}: required compatible experimental profile missing",
        )
        require(
            current.require_worker_workflow(profile.worker_workflow).profile_id == profile_id,
            f"{profile_id}: worker index lost exact identity",
        )
    expect_invalid(lambda: current.require_profile("not-registered"), "unknown gh-aw engine profile")
    expect_invalid(lambda: current.require_worker_workflow("unregistered.lock.yml"), "not registered")

    raw = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    with TemporaryDirectory() as tmp:
        root = Path(tmp)

        malformed = copy.deepcopy(raw)
        malformed["profiles"]["synthetic"] = {
            "engine": "copilot",
            "protocol": "openai-compatible",
            "provider": "synthetic",
            "provider_type": "openai",
            "wire_api": "completions",
            "base_url": "https://synthetic.invalid/v1?token=forbidden",
            "network_host": "synthetic.invalid",
            "model": "synthetic-model",
            "worker_source": ".github/workflows/synthetic.md",
            "worker_workflow": "synthetic.lock.yml",
            "credential": "SYNTHETIC_API_KEY",
            "maturity": "experimental",
        }
        registry = write_fixture(root, malformed)
        expect_invalid(
            lambda: load_registry(registry, repo_root=root).require_profile("copilot"),
            "base_url",
        )

        duplicate_worker = copy.deepcopy(raw)
        duplicate_worker["profiles"]["deepseek"]["worker_workflow"] = duplicate_worker[
            "profiles"
        ]["gemini"]["worker_workflow"]
        registry = write_fixture(root, duplicate_worker)
        expect_invalid(lambda: load_registry(registry, repo_root=root), "worker_workflow")

        duplicate_credential = copy.deepcopy(raw)
        duplicate_credential["profiles"]["deepseek"]["credential"] = duplicate_credential[
            "profiles"
        ]["gemini"]["credential"]
        registry = write_fixture(root, duplicate_credential)
        expect_invalid(lambda: load_registry(registry, repo_root=root), "credential")

        traversal = copy.deepcopy(raw)
        traversal["profiles"]["deepseek"]["worker_source"] = ".github/workflows/../escape.md"
        registry = write_fixture(root, traversal)
        expect_invalid(lambda: load_registry(registry, repo_root=root), "worker_source")

        duplicate_separator = copy.deepcopy(raw)
        duplicate_separator["profiles"]["deepseek"]["worker_source"] = (
            ".github/workflows//ai-sdlc-gh-aw-worker-deepseek.md"
        )
        registry = write_fixture(root, duplicate_separator)
        expect_invalid(lambda: load_registry(registry, repo_root=root), "worker_source")

        dot_segment = copy.deepcopy(raw)
        dot_segment["profiles"]["deepseek"]["worker_source"] = (
            ".github/workflows/./ai-sdlc-gh-aw-worker-deepseek.md"
        )
        registry = write_fixture(root, dot_segment)
        expect_invalid(lambda: load_registry(registry, repo_root=root), "worker_source")

        compatible_alias = copy.deepcopy(raw)
        compatible_alias["profiles"]["deepseek"]["credential_aliases"] = [
            "DEEPSEEK_COMPAT_API_KEY"
        ]
        registry = write_fixture(root, compatible_alias)
        expect_invalid(lambda: load_registry(registry, repo_root=root), "credential_aliases")

        valid_extension = copy.deepcopy(raw)
        valid_extension["profiles"]["fixture-provider"] = {
            "engine": "copilot",
            "protocol": "openai-compatible",
            "provider": "fixture-provider",
            "provider_type": "openai",
            "wire_api": "responses",
            "base_url": "https://fixture.invalid/openai/v1",
            "network_host": "fixture.invalid",
            "model": "fixture-model-v1",
            "worker_source": ".github/workflows/fixture-provider.md",
            "worker_workflow": "fixture-provider.lock.yml",
            "credential": "FIXTURE_PROVIDER_API_KEY",
            "maturity": "experimental",
        }
        registry = write_fixture(root, valid_extension)
        extended = load_registry(registry, repo_root=root)
        fixture = extended.require_profile("fixture-provider")
        require(
            fixture.is_openai_compatible and fixture.wire_api == "responses",
            "valid compatible fixture was not normalized",
        )

    subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_gh_aw_registry_extension.py")],
        cwd=ROOT,
        check=True,
    )
    print("gh-aw shared provider registry validation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
