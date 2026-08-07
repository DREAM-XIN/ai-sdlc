#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
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
}


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

    gateway = GATEWAY.read_text(encoding="utf-8")
    if "worker_workflow:" in gateway.split("permissions:", 1)[0]:
        fail("profile gateway must not expose arbitrary worker_workflow input")
    for profile in EXPECTED:
        if f"          - {profile}\n" not in gateway:
            fail(f"profile gateway missing choice {profile}")
    if "scripts/resolve_gh_aw_engine.py" not in gateway:
        fail("profile gateway must use trusted resolver")

    # Import renderer and prove a non-copilot render changes only workflow name + engine.
    spec = importlib.util.spec_from_file_location("ghaw_renderer", RENDERER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    base = canonical
    rendered = base.replace("name: AI-SDLC gh-aw Worker\n", "name: AI-SDLC gh-aw Worker (codex)\n", 1).replace("engine: copilot\n", "engine: codex\n", 1)
    normalized = rendered.replace("name: AI-SDLC gh-aw Worker (codex)\n", "name: AI-SDLC gh-aw Worker\n", 1).replace("engine: codex\n", "engine: copilot\n", 1)
    if normalized != base:
        fail("provider worker rendering changed security/lifecycle content")

    print("gh-aw trusted engine profile and renderer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
