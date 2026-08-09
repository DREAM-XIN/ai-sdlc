#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import copy

import yaml

from gh_aw_provider_registry import RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"


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
    require(
        current.profile_ids() == ("copilot", "codex", "claude", "gemini", "deepseek"),
        "current compatibility profile ordering drifted",
    )
    require(
        tuple(p.profile_id for p in current.openai_compatible_profiles()) == ("deepseek",),
        "current compatible-provider classification drifted",
    )
    require(
        current.require_worker_workflow("ai-sdlc-gh-aw-worker-deepseek.lock.yml").profile_id
        == "deepseek",
        "worker index lost exact identity",
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

    print("gh-aw shared provider registry validation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
