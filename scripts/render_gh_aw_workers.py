#!/usr/bin/env python3
"""Render provider-specific gh-aw worker sources from the canonical worker.

The canonical worker owns the lifecycle/security contract. Engine profiles are
allowed to vary only the workflow name and the gh-aw engine id.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import yaml

# Materialization trigger: provider runtime sources must be durable alongside compiled locks.
ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md"
PROFILES = ROOT / "runtimes/gh-aw/engine-profiles.yaml"


def load_profiles() -> dict[str, dict[str, str]]:
    data = yaml.safe_load(PROFILES.read_text(encoding="utf-8"))
    if data.get("version") != "0.1.0" or not isinstance(data.get("profiles"), dict):
        raise SystemExit("invalid gh-aw engine profile registry")
    return data["profiles"]


def render(profile: str, cfg: dict[str, str]) -> Path:
    engine = cfg["engine"]
    source = ROOT / cfg["worker_source"]
    base = CANONICAL.read_text(encoding="utf-8")

    if profile == "copilot":
        if source != CANONICAL:
            raise SystemExit("copilot profile must point at the canonical worker")
        return CANONICAL

    if base.count("name: AI-SDLC gh-aw Worker\n") != 1:
        raise SystemExit("canonical worker name marker changed")
    if base.count("engine: copilot\n") != 1:
        raise SystemExit("canonical worker engine marker changed")

    rendered = base.replace(
        "name: AI-SDLC gh-aw Worker\n",
        f"name: AI-SDLC gh-aw Worker ({engine})\n",
        1,
    ).replace("engine: copilot\n", f"engine: {engine}\n", 1)
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
        print(f"{profile}\t{profiles[profile]['engine']}\t{path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
