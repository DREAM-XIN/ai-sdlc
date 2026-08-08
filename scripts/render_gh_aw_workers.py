#!/usr/bin/env python3
"""Render provider-specific gh-aw worker sources from the canonical worker.

The canonical worker owns the lifecycle/security contract, including the invariants
that autonomous work branches derive from the reserved target ref rather than the
workflow default-branch checkout and that conclusion PR discovery uses the job token
while cross-workflow result dispatch uses the dedicated trigger credential. Trusted
profiles may vary engine installation/model settings and, for OpenAI-compatible
providers, BYOK endpoint/auth configuration. Provider-specific workers must not
change the AI-SDLC lifecycle or Safe Output contract.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import urllib.parse
import yaml

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md"
PROFILES = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
CREDENTIAL_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def load_profiles() -> dict[str, dict[str, str]]:
    data = yaml.safe_load(PROFILES.read_text(encoding="utf-8"))
    if data.get("version") != "0.1.0" or not isinstance(data.get("profiles"), dict):
        raise SystemExit("invalid gh-aw engine profile registry")
    return data["profiles"]


def validate_openai_compatible(cfg: dict[str, str]) -> None:
    if cfg.get("engine") != "copilot":
        raise SystemExit("openai-compatible profiles currently require the gh-aw copilot BYOK engine")
    required = ["provider", "base_url", "network_host", "model", "credential"]
    missing = [key for key in required if not isinstance(cfg.get(key), str) or not cfg[key].strip()]
    if missing:
        raise SystemExit(f"openai-compatible profile missing fields: {', '.join(missing)}")
    if cfg.get("provider_type", "openai") != "openai":
        raise SystemExit("openai-compatible profile provider_type must be openai")
    if cfg.get("wire_api", "completions") not in {"completions", "responses"}:
        raise SystemExit("openai-compatible wire_api must be completions or responses")
    if not MODEL_RE.fullmatch(cfg["model"]):
        raise SystemExit(f"invalid provider model: {cfg['model']!r}")
    if not CREDENTIAL_RE.fullmatch(cfg["credential"]):
        raise SystemExit(f"invalid provider credential name: {cfg['credential']!r}")
    parsed = urllib.parse.urlparse(cfg["base_url"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit("openai-compatible base_url must be an https URL without embedded credentials")
    if parsed.hostname != cfg["network_host"] or not HOST_RE.fullmatch(cfg["network_host"]):
        raise SystemExit("openai-compatible network_host must exactly match base_url hostname")


def engine_frontmatter(cfg: dict[str, str]) -> str:
    engine = cfg["engine"]
    version = cfg.get("engine_version")
    model = cfg.get("model")
    protocol = cfg.get("protocol")

    if protocol == "openai-compatible":
        validate_openai_compatible(cfg)
        # BYOK custom models are passed through Copilot's provider environment, not engine.model.
        lines = [
            "engine:",
            "  id: copilot",
            "  env:",
            f"    COPILOT_PROVIDER_BASE_URL: {cfg['base_url']}",
            f"    COPILOT_MODEL: {cfg['model']}",
            f"    COPILOT_PROVIDER_API_KEY: ${{{{ secrets.{cfg['credential']} }}}}",
            "    COPILOT_PROVIDER_TYPE: openai",
            f"    COPILOT_PROVIDER_WIRE_API: {cfg.get('wire_api', 'completions')}",
            "network:",
            "  allowed:",
            "    - defaults",
            f"    - {cfg['network_host']}",
        ]
        return "\n".join(lines) + "\n"

    if version is None and model is None:
        return f"engine: {engine}\n"
    if version is not None and (not isinstance(version, str) or not VERSION_RE.fullmatch(version)):
        raise SystemExit(f"invalid pinned engine_version for {engine}: {version!r}")
    if model is not None and (not isinstance(model, str) or not MODEL_RE.fullmatch(model)):
        raise SystemExit(f"invalid pinned model for {engine}: {model!r}")
    lines = ["engine:", f"  id: {engine}"]
    if version is not None:
        lines.append(f'  version: "{version}"')
    if model is not None:
        lines.append(f"  model: {model}")
    return "\n".join(lines) + "\n"


def render_text(profile: str, cfg: dict[str, str], base: str) -> str:
    if base.count("name: AI-SDLC gh-aw Worker\n") != 1:
        raise SystemExit("canonical worker name marker changed")
    if base.count("engine: copilot\n") != 1:
        raise SystemExit("canonical worker engine marker changed")
    return base.replace(
        "name: AI-SDLC gh-aw Worker\n",
        f"name: AI-SDLC gh-aw Worker ({profile})\n",
        1,
    ).replace("engine: copilot\n", engine_frontmatter(cfg), 1)


def render(profile: str, cfg: dict[str, str]) -> Path:
    source = ROOT / cfg["worker_source"]
    base = CANONICAL.read_text(encoding="utf-8")

    if profile == "copilot":
        if source != CANONICAL:
            raise SystemExit("copilot profile must point at the canonical worker")
        if cfg.get("engine_version") is not None or cfg.get("model") is not None or cfg.get("protocol") is not None:
            raise SystemExit("canonical copilot profile cannot inject engine/provider overrides")
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
        model = profiles[profile].get("model", "default")
        provider = profiles[profile].get("provider", "native")
        protocol = profiles[profile].get("protocol", "native")
        print(f"{profile}\t{profiles[profile]['engine']}\t{provider}\t{protocol}\t{version}\t{model}\t{path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
