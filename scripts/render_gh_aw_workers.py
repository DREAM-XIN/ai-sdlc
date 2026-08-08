#!/usr/bin/env python3
"""Render provider-specific gh-aw worker sources from the canonical worker.

The canonical worker owns the lifecycle/security contract. Engine profiles are
allowed to vary only the workflow name, gh-aw engine id, and an optional pinned
engine CLI version declared by the trusted profile registry.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import yaml

# Materialization trigger: refresh durable provider sources and strict-compiled locks.
ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md"
PROFILES = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def load_profiles() -> dict[str, dict[str, str]]:
    data = yaml.safe_load(PROFILES.read_text(encoding="utf-8"))
    if data.get("version") != "0.1.0" or not isinstance(data.get("profiles"), dict):
        raise SystemExit("invalid gh-aw engine profile registry")
    return data["profiles"]


def engine_frontmatter(cfg: dict[str, str]) -> str:
    engine = cfg["engine"]
    version = cfg.get("engine_version")
    if version is None:
        return f"engine: {engine}\n"
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise SystemExit(f"invalid pinned engine_version for {engine}: {version!r}")
    return f"engine:\n  id: {engine}\n  version: \"{version}\"\n"


def render_text(profile: str, cfg: dict[str, str], base: str) -> str:
    engine = cfg["engine"]
    if base.count("name: AI-SDLC gh-aw Worker\n") != 1:
        raise SystemExit("canonical worker name marker changed")
    if base.count("engine: copilot\n") != 1:
        raise SystemExit("canonical worker engine marker changed")
    return base.replace(
        "name: AI-SDLC gh-aw Worker\n",
        f"name: AI-SDLC gh-aw Worker ({engine})\n",
        1,
    ).replace("engine: copilot\n", engine_frontmatter(cfg), 1)


def render(profile: str, cfg: dict[str, str]) -> Path:
    source = ROOT / cfg["worker_source"]
    base = CANONICAL.read_text(encoding="utf-8")

    if profile == "copilot":
        if source != CANONICAL:
            raise SystemExit("copilot profile must point at the canonical worker")
        if cfg.get("engine_version") is not None:
            raise SystemExit("canonical copilot profile cannot inject an engine version")
        return CANONICAL

    rendered = render_text(profile, cfg, base)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(rendered, encoding="utf-8")
    return source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="append", help="profile id to render; repeatable")
    parser.add_argument("--all", action="store_true", help="render every registered profile")
    args = parser.parse_args()

    profiles = load_profiles()
    selected = list(profiles) if args.all else (args.profile or [])
    if not selected:
        raise SystemExit("select --all or at least one --profile")

    for profile in selected:
        if profile not in profiles:
            raise SystemExit(f"unknown gh-aw engine profile: {profile}")
        path = render(profile, profiles[profile])
        version = profiles[profile].get("engine_version", "default")
        print(f"{profile}\t{profiles[profile]['engine']}\t{version}\t{path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
