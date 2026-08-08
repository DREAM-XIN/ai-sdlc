#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
RUNTIME = ROOT / "runtimes/gh-aw/runtime.yaml"
CANONICAL = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md"
GATEWAY = ROOT / ".github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml"
RENDERER = ROOT / "scripts/render_gh_aw_workers.py"

EXPECTED = {
    "copilot": ("copilot", "COPILOT_GITHUB_TOKEN", "ai-sdlc-gh-aw-worker.lock.yml"),
    "codex": ("codex", "OPENAI_API_KEY", "ai-sdlc-gh-aw-worker-codex.lock.yml"),
    "claude": ("claude", "ANTHROPIC_API_KEY", "ai-sdlc-gh-aw-worker-claude.lock.yml"),
    "gemini": ("gemini", "GEMINI_API_KEY", "ai-sdlc-gh-aw-worker-gemini.lock.yml"),
    "deepseek": ("copilot", "DEEPSEEK_API_KEY", "ai-sdlc-gh-aw-worker-deepseek.lock.yml"),
}
PINNED_ENGINE_VERSIONS = {"gemini": "0.39.1"}
PINNED_NATIVE_MODELS = {"gemini": "gemini-3.5-flash-lite"}
OPENAI_COMPATIBLE = {
    "deepseek": {
        "provider": "deepseek",
        "protocol": "openai-compatible",
        "provider_type": "openai",
        "wire_api": "completions",
        "base_url": "https://api.deepseek.com",
        "network_host": "api.deepseek.com",
        "model": "deepseek-chat",
        "maturity": "experimental",
    }
}
CANONICAL_MAX_TURN_CACHE_MISSES = 20


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> int:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    profiles = registry.get("profiles", {})
    if set(profiles) != set(EXPECTED):
        fail(f"unexpected gh-aw profile set: {sorted(profiles)}")

    workers = set()
    for profile, (engine, credential, worker) in EXPECTED.items():
        cfg = profiles[profile]
        if cfg.get("engine") != engine or cfg.get("credential") != credential:
            fail(f"{profile}: engine/credential mapping drifted")
        if cfg.get("worker_workflow") != worker:
            fail(f"{profile}: worker workflow mapping drifted")
        if worker in workers:
            fail("worker workflow names must be unique")
        workers.add(worker)
        source = cfg.get("worker_source", "")
        if not source.startswith(".github/workflows/") or not source.endswith(".md"):
            fail(f"{profile}: worker_source must be a workflow markdown path")
        if not (ROOT / source).is_file():
            fail(f"{profile}: committed worker_source is missing: {source}")

        expected_version = PINNED_ENGINE_VERSIONS.get(profile)
        if expected_version is not None:
            if cfg.get("engine_version") != expected_version:
                fail(f"{profile}: engine_version must remain pinned to {expected_version}")
        elif cfg.get("engine_version") is not None:
            fail(f"{profile}: unexpected engine_version pin")

        if profile in OPENAI_COMPATIBLE:
            expected_provider = OPENAI_COMPATIBLE[profile]
            for key, expected_value in expected_provider.items():
                if cfg.get(key) != expected_value:
                    fail(f"{profile}: {key} must remain {expected_value!r}")
        else:
            if cfg.get("protocol") is not None or cfg.get("provider") is not None:
                fail(f"{profile}: native profile must not declare provider protocol overrides")
            expected_model = PINNED_NATIVE_MODELS.get(profile)
            if expected_model is not None:
                if cfg.get("model") != expected_model:
                    fail(f"{profile}: model must remain pinned to {expected_model}")
            elif cfg.get("model") is not None:
                fail(f"{profile}: unexpected native model pin")

    runtime = yaml.safe_load(RUNTIME.read_text(encoding="utf-8"))
    if runtime.get("engine_profile_registry") != "runtimes/gh-aw/engine-profiles.yaml":
        fail("runtime must declare the engine profile registry")
    if runtime.get("default_engine_profile") not in profiles:
        fail("runtime default engine profile must exist")

    canonical = CANONICAL.read_text(encoding="utf-8")
    if canonical.count("engine: copilot\n") != 1:
        fail("canonical worker must contain exactly one copilot engine marker")
    if "permissions: read-all" not in canonical:
        fail("canonical worker must remain read-only")
    if "docs/gh-aw-dogfood/**" not in canonical:
        fail("canonical worker must retain bounded safe-output scope")
    if f"max-turn-cache-misses: {CANONICAL_MAX_TURN_CACHE_MISSES}\n" not in canonical:
        fail("canonical worker must retain the bounded cache-miss turn budget")
    if "pull-requests: read" not in canonical or "actions: write" not in canonical:
        fail("deterministic conclusion handoff must have only the required PR-read/actions-write permissions")

    required_checkout_markers = [
        "checkout:\n  fetch-depth: 0\n  fetch:\n    - \"*\"\n",
    ]
    for marker in required_checkout_markers:
        if marker not in canonical:
            fail(f"canonical worker missing safe-output base prefetch marker: {marker!r}")

    safe_output_auth_markers = [
        'github-token: ${{ secrets.GITHUB_TOKEN }}',
        "fallback-as-issue: false",
    ]
    for marker in safe_output_auth_markers:
        if marker not in canonical:
            fail(f"canonical worker missing same-repo Safe Output auth marker: {marker}")

    deterministic_result_markers = [
        "jobs:\n  conclusion:",
        "pre-steps:",
        "Dispatch structured worker result after Draft PR",
        'EXPECTED_HEAD_PREFIX="gh-aw/${FEATURE_ID}-${GITHUB_RUN_ID}-v${EXPECTED_REVISION}"',
        "--json url,title,isDraft,headRefName",
        '(.headRefName | startswith($prefix))',
        'test -n "$PR_URL"',
        "gh workflow run ai-sdlc-gh-aw-result.yml",
        "unique Draft PR whose remote head starts with this run/revision branch prefix",
        "Do not call any result-reporting tool",
    ]
    for marker in deterministic_result_markers:
        if marker not in canonical:
            fail(f"canonical worker missing deterministic result handoff marker: {marker}")
    forbidden_result_markers = [
        "report-result:",
        "report_result",
        'SUMMARY: ${{ inputs.summary }}',
        'test -n "$SUMMARY"',
        "Submission phase 2 is mandatory and ordered",
        "needs.safe_outputs.outputs.created_pr_url",
        '--head "$EXPECTED_HEAD"',
    ]
    for marker in forbidden_result_markers:
        if marker in canonical:
            fail(f"canonical worker must not retain obsolete result handoff marker: {marker}")

    required_head_base_markers = [
        "create and switch to the local work branch `gh-aw/${{ inputs.feature_id }}-${{ github.run_id }}-v${{ inputs.expected_revision }}`",
        "PR base only",
        "never use `${{ inputs.target_ref }}` as the local work branch or as `create_pull_request.branch`",
        "Set its `branch` argument to exactly `gh-aw/${{ inputs.feature_id }}-${{ github.run_id }}-v${{ inputs.expected_revision }}`",
        "gh-aw may append a collision-avoidance salt",
        "Do not set or override the PR base",
        "The head branch and `${{ inputs.target_ref }}` must be different",
    ]
    for marker in required_head_base_markers:
        if marker not in canonical:
            fail(f"canonical worker missing PR head/base separation marker: {marker}")

    gateway = GATEWAY.read_text(encoding="utf-8")
    if "worker_workflow:" in gateway.split("permissions:", 1)[0]:
        fail("profile gateway must not expose arbitrary worker_workflow input")
    for profile in EXPECTED:
        if f"          - {profile}\n" not in gateway:
            fail(f"profile gateway missing choice {profile}")
    if "scripts/resolve_gh_aw_engine.py" not in gateway:
        fail("profile gateway must use trusted resolver")

    spec = importlib.util.spec_from_file_location("ghaw_renderer", RENDERER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)

    rendered_profiles = (set(PINNED_ENGINE_VERSIONS) | set(PINNED_NATIVE_MODELS) | set(OPENAI_COMPATIBLE)) - {"copilot"}
    for profile in rendered_profiles:
        cfg = profiles[profile]
        expected_source = module.render_text(profile, cfg, canonical)
        actual_source = (ROOT / cfg["worker_source"]).read_text(encoding="utf-8")
        if actual_source != expected_source:
            fail(f"{profile}: committed worker source drifted from deterministic renderer")
        expected_version = PINNED_ENGINE_VERSIONS.get(profile)
        if expected_version is not None:
            marker = f'  version: "{expected_version}"\n'
            if marker not in actual_source:
                fail(f"{profile}: pinned CLI version is not materialized in worker frontmatter")
        if profile in OPENAI_COMPATIBLE:
            provider_markers = [
                "  id: copilot\n",
                f"    COPILOT_PROVIDER_BASE_URL: {cfg['base_url']}\n",
                f"    COPILOT_MODEL: {cfg['model']}\n",
                f"    COPILOT_PROVIDER_API_KEY: ${{{{ secrets.{cfg['credential']} }}}}\n",
                "    COPILOT_PROVIDER_TYPE: openai\n",
                f"    COPILOT_PROVIDER_WIRE_API: {cfg['wire_api']}\n",
                f"    - {cfg['network_host']}\n",
            ]
            for marker in provider_markers:
                if marker not in actual_source:
                    fail(f"{profile}: rendered BYOK worker missing marker: {marker.strip()}")

    print("gh-aw native and OpenAI-compatible profiles, target-base prefetch, Safe Output auth, deterministic result handoff, provider isolation, and renderer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
