#!/usr/bin/env python3
"""Prove generated worker bootstrapping does not weaken normal Registry reads."""
from __future__ import annotations

from pathlib import Path
import tempfile

from gh_aw_provider_registry import RegistryValidationError, load_registry
import render_gh_aw_workers as renderer


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def fixture_registry(source: str) -> str:
    return f"""version: 0.1.0
profiles:
  fixture-provider:
    engine: copilot
    provider: fixture-provider
    protocol: openai-compatible
    provider_type: openai
    wire_api: completions
    base_url: https://fixture-provider.example/v1
    network_host: fixture-provider.example
    model: fixture-model
    worker_source: {source}
    worker_workflow: ai-sdlc-gh-aw-worker-fixture-provider.lock.yml
    credential: FIXTURE_PROVIDER_API_KEY
    maturity: experimental
"""


def main() -> int:
    # Prove renderer wiring is local: write mode relaxes existence, --check remains strict.
    calls: list[bool] = []
    original = renderer.load_registry
    sentinel = object()
    try:
        def capture(**kwargs):
            calls.append(kwargs.get("require_source_files"))
            return sentinel

        renderer.load_registry = capture
        require(renderer.load_renderer_registry(check=False) is sentinel, "write-mode Registry load did not return captured value")
        require(renderer.load_renderer_registry(check=True) is sentinel, "check-mode Registry load did not return captured value")
    finally:
        renderer.load_registry = original
    require(calls == [False, True], f"renderer source-existence modes drifted: {calls}")

    with tempfile.TemporaryDirectory(prefix="ghaw-materialize-") as tmp:
        root = Path(tmp)
        workflows = root / ".github/workflows"
        workflows.mkdir(parents=True)
        canonical = workflows / "ai-sdlc-gh-aw-worker.md"
        canonical_text = "---\nname: AI-SDLC gh-aw Worker\nengine: copilot\n---\nfixture body\n"
        canonical.write_text(canonical_text, encoding="utf-8")

        source_rel = ".github/workflows/ai-sdlc-gh-aw-worker-fixture-provider.md"
        source = root / source_rel
        registry_path = root / "engine-profiles.yaml"
        registry_path.write_text(fixture_registry(source_rel), encoding="utf-8")

        try:
            load_registry(registry_path, repo_root=root)
        except RegistryValidationError as exc:
            require("registered worker source does not exist" in str(exc), f"normal load failed for unexpected reason: {exc}")
        else:
            raise AssertionError("normal Registry load accepted an absent registered worker source")

        bounded = load_registry(
            registry_path,
            repo_root=root,
            require_source_files=False,
        )
        profile = bounded.require_profile("fixture-provider")
        expected = renderer.render_text(profile, canonical_text)
        source.write_text(expected, encoding="utf-8")

        strict = load_registry(registry_path, repo_root=root)
        require(strict.require_profile("fixture-provider").worker_source == source_rel, "normal Registry load did not recover after materialization")
        require(source.read_text(encoding="utf-8") == expected, "materialized worker did not match deterministic renderer output")

        source.unlink()
        try:
            load_registry(registry_path, repo_root=root)
        except RegistryValidationError as exc:
            require("registered worker source does not exist" in str(exc), f"post-delete strict load failed for unexpected reason: {exc}")
        else:
            raise AssertionError("normal Registry load accepted a deleted registered worker source")

    print("gh-aw bounded worker materialization and strict read-mode checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
