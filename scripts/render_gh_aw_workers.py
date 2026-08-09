#!/usr/bin/env python3
"""Render trusted gh-aw worker sources from the canonical worker contract."""
from __future__ import annotations

import argparse
from pathlib import Path

from gh_aw_provider_registry import EngineProfile, RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker.md"


def engine_frontmatter(profile: EngineProfile) -> str:
    if profile.is_openai_compatible:
        lines = [
            "engine:",
            "  id: copilot",
            f'  model: "{profile.model}"',
            "  env:",
            f"    COPILOT_PROVIDER_BASE_URL: {profile.base_url}",
            f"    COPILOT_MODEL: {profile.model}",
            f"    COPILOT_PROVIDER_API_KEY: ${{{{ secrets.{profile.credential} }}}}",
            f"    COPILOT_PROVIDER_TYPE: {profile.provider_type}",
            f"    COPILOT_PROVIDER_WIRE_API: {profile.wire_api}",
            "network:",
            "  allowed:",
            "    - defaults",
            f"    - {profile.network_host}",
        ]
        return "\n".join(lines) + "\n"

    if profile.engine_version is None and profile.model is None:
        return f"engine: {profile.engine}\n"
    lines = ["engine:", f"  id: {profile.engine}"]
    if profile.engine_version is not None:
        lines.append(f'  version: "{profile.engine_version}"')
    if profile.model is not None:
        lines.append(f"  model: {profile.model}")
    return "\n".join(lines) + "\n"


def render_text(profile: EngineProfile, base: str) -> str:
    if base.count("name: AI-SDLC gh-aw Worker\n") != 1:
        raise RegistryValidationError("canonical worker name marker changed")
    if base.count("engine: copilot\n") != 1:
        raise RegistryValidationError("canonical worker engine marker changed")
    return base.replace(
        "name: AI-SDLC gh-aw Worker\n",
        f"name: AI-SDLC gh-aw Worker ({profile.profile_id})\n",
        1,
    ).replace("engine: copilot\n", engine_frontmatter(profile), 1)


def expected_source(profile: EngineProfile, base: str) -> str:
    source = ROOT / profile.worker_source
    if source == CANONICAL:
        if (
            profile.engine != "copilot"
            or profile.engine_version is not None
            or profile.model is not None
            or profile.protocol != "native"
        ):
            raise RegistryValidationError(
                "canonical worker profile cannot inject engine/provider overrides"
            )
        return base
    return render_text(profile, base)


def materialize(profile: EngineProfile, base: str, check: bool) -> Path:
    source = ROOT / profile.worker_source
    expected = expected_source(profile, base)
    if check:
        try:
            actual = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise RegistryValidationError(
                f"profile {profile.profile_id!r}: registered worker source is missing"
            ) from exc
        if actual != expected:
            raise RegistryValidationError(
                f"profile {profile.profile_id!r}: committed worker source drifted from deterministic renderer"
            )
        return source
    if source != CANONICAL:
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(expected, encoding="utf-8")
    return source


def load_renderer_registry(*, check: bool):
    """Permit absent generated sources only while deterministic write materialization runs."""
    return load_registry(require_source_files=check)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="append", help="profile id to render; repeatable")
    parser.add_argument("--all", action="store_true", help="render every registered profile")
    parser.add_argument("--check", action="store_true", help="verify generated sources without writing")
    args = parser.parse_args()

    try:
        registry = load_renderer_registry(check=args.check)
        selected_ids = list(registry.profile_ids()) if args.all else (args.profile or [])
        if not selected_ids:
            raise RegistryValidationError("select --all or at least one --profile")
        profiles = [registry.require_profile(profile_id) for profile_id in selected_ids]
        base = CANONICAL.read_text(encoding="utf-8")
        for profile in profiles:
            path = materialize(profile, base, args.check)
            print(
                "\t".join(
                    (
                        profile.profile_id,
                        profile.engine,
                        profile.provider,
                        profile.protocol,
                        profile.engine_version or "default",
                        profile.model or "default",
                        str(path.relative_to(ROOT)),
                    )
                )
            )
    except RegistryValidationError as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
