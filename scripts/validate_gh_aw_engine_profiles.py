#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import yaml

from gh_aw_provider_registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtimes/gh-aw/runtime.yaml"
CANONICAL = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md"
GATEWAY = ROOT / ".github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml"
RENDERER = ROOT / "scripts/render_gh_aw_workers.py"

# Test-only backward compatibility snapshot. Runtime trust comes only from the Registry.
LEGACY_COMPATIBILITY_BASELINE = {
    "copilot": ("copilot", "COPILOT_GITHUB_TOKEN", (), "ai-sdlc-gh-aw-worker.lock.yml"),
    "codex": ("codex", "OPENAI_API_KEY", ("CODEX_API_KEY",), "ai-sdlc-gh-aw-worker-codex.lock.yml"),
    "claude": ("claude", "ANTHROPIC_API_KEY", (), "ai-sdlc-gh-aw-worker-claude.lock.yml"),
    "gemini": ("gemini", "GEMINI_API_KEY", (), "ai-sdlc-gh-aw-worker-gemini.lock.yml"),
    "deepseek": ("copilot", "DEEPSEEK_API_KEY", (), "ai-sdlc-gh-aw-worker-deepseek.lock.yml"),
}


def fail(message):
    raise SystemExit(message)


def require(condition, message):
    if not condition:
        fail(message)


def main():
    registry = load_registry()
    profiles = {profile.profile_id: profile for profile in registry.profiles}

    # Explicit legacy compatibility proof is not an execution or extension authority.
    require(
        set(LEGACY_COMPATIBILITY_BASELINE).issubset(profiles),
        f"legacy compatibility profiles missing: {sorted(set(LEGACY_COMPATIBILITY_BASELINE) - set(profiles))}",
    )
    for profile_id, (engine, credential, aliases, worker) in LEGACY_COMPATIBILITY_BASELINE.items():
        profile = profiles[profile_id]
        require(
            (profile.engine, profile.credential, profile.credential_aliases, profile.worker_workflow)
            == (engine, credential, aliases, worker),
            f"{profile_id}: backward-compatible trusted mapping drifted",
        )

    runtime = yaml.safe_load(RUNTIME.read_text(encoding="utf-8"))
    require(
        runtime.get("engine_profile_registry") == "runtimes/gh-aw/engine-profiles.yaml",
        "runtime profile registry drifted",
    )
    require(runtime.get("default_engine_profile") in profiles, "runtime default engine profile missing")
    require(runtime.get("default_engine_profile") == "copilot", "default profile must remain copilot")

    canonical = CANONICAL.read_text(encoding="utf-8")
    require(canonical.count("engine: copilot\n") == 1, "canonical worker must retain one renderer engine marker")
    require("permissions: read-all" in canonical, "canonical agent must remain read-only")
    for marker in (
        "target_repository:",
        "target_owner:",
        "target_repo_name:",
        "repository: ${{ inputs.target_repository }}",
        "ref: ${{ inputs.target_ref }}",
        "current: true",
        "AI_SDLC_RUNTIME_APP_CLIENT_ID",
        "AI_SDLC_RUNTIME_APP_PRIVATE_KEY",
        "safe-outputs-github-app:",
        "target-repo: ${{ inputs.target_repository }}",
        "base-branch: ${{ inputs.target_ref }}",
        "protected-files: blocked",
        "fallback-as-issue: false",
        "PR_URL: ${{ needs.safe_outputs.outputs.created_pr_url }}",
        '--field target_repository="$TARGET_REPOSITORY"',
        "Do not edit the Feature Manifest directly",
        "Do not pass or waive any Gate",
        "Do not merge or release",
    ):
        require(marker in canonical, f"canonical cross-repo worker missing marker: {marker}")
    for forbidden in ("docs/gh-aw-dogfood/**", "permissions:\n  contents: write", "report-result:"):
        require(forbidden not in canonical, f"canonical worker retained obsolete/unsafe marker: {forbidden}")

    gateway = GATEWAY.read_text(encoding="utf-8")
    require("target_repository:" in gateway, "profile gateway lost target repository handoff")
    require("ai-sdlc-gh-aw-cross-repo-dispatch.yml" in gateway, "profile gateway does not route cross-repo requests")
    require("scripts/resolve_gh_aw_engine.py" in gateway, "trusted profile resolver bypassed")

    spec = importlib.util.spec_from_file_location("ghaw_renderer", RENDERER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    for profile in registry.profiles:
        expected = module.expected_source(profile, canonical)
        actual = (ROOT / profile.worker_source).read_text(encoding="utf-8")
        require(actual == expected, f"{profile.profile_id}: committed worker source drifted from deterministic renderer")

    for command in (
        [sys.executable, str(ROOT / "scripts/validate_gh_aw_provider_registry.py")],
        [sys.executable, str(ROOT / "scripts/validate_gh_aw_worker_materialization.py")],
        [sys.executable, str(RENDERER), "--all", "--check"],
        [sys.executable, str(ROOT / "scripts/render_gh_aw_profile_surfaces.py"), "--check"],
    ):
        subprocess.run(command, cwd=ROOT, check=True)

    print(
        "gh-aw profiles preserve registry-driven trust, legacy backward compatibility, "
        "and the generic cross-repository worker security contract"
    )


if __name__ == "__main__":
    main()
