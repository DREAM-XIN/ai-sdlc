#!/usr/bin/env python3
"""Render/check bounded gh-aw profile and credential workflow surfaces."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from gh_aw_provider_registry import EngineProfile, RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtimes/gh-aw/runtime.yaml"
PREFLIGHT = ROOT / ".github/workflows/ai-sdlc-gh-aw-preflight.yml"
DISPATCH = ROOT / ".github/workflows/ai-sdlc-gh-aw-dispatch-profile.yml"

OPTIONS_BEGIN = "# BEGIN AI-SDLC GENERATED GH-AW PROFILE OPTIONS"
OPTIONS_END = "# END AI-SDLC GENERATED GH-AW PROFILE OPTIONS"
CREDENTIAL_ENV_BEGIN = "# BEGIN AI-SDLC GENERATED GH-AW CREDENTIAL PRESENCE ENV"
CREDENTIAL_ENV_END = "# END AI-SDLC GENERATED GH-AW CREDENTIAL PRESENCE ENV"
CREDENTIAL_CASE_BEGIN = "# BEGIN AI-SDLC GENERATED GH-AW CREDENTIAL PRESENCE CASE"
CREDENTIAL_CASE_END = "# END AI-SDLC GENERATED GH-AW CREDENTIAL PRESENCE CASE"


def replace_block(text: str, begin: str, end: str, rendered: str) -> str:
    marker_start = text.find(begin)
    marker_end = text.find(end, marker_start + 1)
    if marker_start < 0 or marker_end < 0:
        raise RegistryValidationError(f"generated workflow marker block missing: {begin}")
    start = text.rfind("\n", 0, marker_start) + 1
    finish = text.find("\n", marker_end + len(end))
    if finish < 0:
        finish = len(text)
    return text[:start] + rendered + text[finish:]


def render_options(profiles: tuple[EngineProfile, ...], indent: str = "        ") -> str:
    lines = [indent + OPTIONS_BEGIN, indent + "options:"]
    lines.extend(indent + f"  - {profile.profile_id}" for profile in profiles)
    lines.append(indent + OPTIONS_END)
    return "\n".join(lines)


def credential_env_name(secret_name: str) -> str:
    return f"HAS_{secret_name}"


def render_credential_env(profiles: tuple[EngineProfile, ...], indent: str = "          ") -> str:
    seen: set[str] = set()
    secrets: list[str] = []
    for profile in profiles:
        for secret_name in (profile.credential, *profile.credential_aliases):
            if secret_name not in seen:
                seen.add(secret_name)
                secrets.append(secret_name)
    lines = [indent + CREDENTIAL_ENV_BEGIN]
    lines.extend(
        indent + f"{credential_env_name(secret_name)}: ${{{{ secrets.{secret_name} != '' }}}}"
        for secret_name in secrets
    )
    lines.append(indent + CREDENTIAL_ENV_END)
    return "\n".join(lines)


def render_credential_case(profiles: tuple[EngineProfile, ...], indent: str = "          ") -> str:
    lines = [indent + CREDENTIAL_CASE_BEGIN, indent + 'case "$PROFILE" in']
    for profile in profiles:
        names = tuple(
            credential_env_name(secret_name)
            for secret_name in (profile.credential, *profile.credential_aliases)
        )
        if len(names) == 1:
            lines.append(indent + f'  {profile.profile_id}) present="${names[0]}" ;;')
        else:
            checks = " || ".join(f'"${name}" == "true"' for name in names)
            lines.extend(
                [
                    indent + f"  {profile.profile_id})",
                    indent + f"    if [[ {checks} ]]; then present=true; else present=false; fi",
                    indent + "    ;;",
                ]
            )
    lines.extend(
        [
            indent + '  *) echo "unexpected trusted profile" >&2; exit 2 ;;',
            indent + "esac",
            indent + CREDENTIAL_CASE_END,
        ]
    )
    return "\n".join(lines)


def validate_default_profile(profile_ids: tuple[str, ...]) -> str:
    runtime = yaml.safe_load(RUNTIME.read_text(encoding="utf-8"))
    default = runtime.get("default_engine_profile")
    if default not in profile_ids:
        raise RegistryValidationError("runtime default engine profile is not registered")
    return default


def render_preflight(text: str, profiles: tuple[EngineProfile, ...]) -> str:
    text = replace_block(text, OPTIONS_BEGIN, OPTIONS_END, render_options(profiles))
    text = replace_block(
        text,
        CREDENTIAL_ENV_BEGIN,
        CREDENTIAL_ENV_END,
        render_credential_env(profiles),
    )
    text = replace_block(
        text,
        CREDENTIAL_CASE_BEGIN,
        CREDENTIAL_CASE_END,
        render_credential_case(profiles),
    )
    return text


def render_dispatch(text: str, profiles: tuple[EngineProfile, ...]) -> str:
    return replace_block(text, OPTIONS_BEGIN, OPTIONS_END, render_options(profiles))


def apply(path: Path, expected: str, check: bool) -> None:
    actual = path.read_text(encoding="utf-8")
    if check:
        if actual != expected:
            raise RegistryValidationError(
                f"{path.relative_to(ROOT)} drifted from Registry-generated gh-aw profile surfaces"
            )
        return
    if actual != expected:
        path.write_text(expected, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated workflow surfaces drift")
    args = parser.parse_args()

    try:
        registry = load_registry()
        profiles = registry.profiles
        validate_default_profile(registry.profile_ids())
        preflight_text = PREFLIGHT.read_text(encoding="utf-8")
        dispatch_text = DISPATCH.read_text(encoding="utf-8")
        expected_preflight = render_preflight(preflight_text, profiles)
        expected_dispatch = render_dispatch(dispatch_text, profiles)
        apply(PREFLIGHT, expected_preflight, args.check)
        apply(DISPATCH, expected_dispatch, args.check)
    except RegistryValidationError as exc:
        raise SystemExit(str(exc)) from None

    mode = "verified" if args.check else "rendered"
    print(f"gh-aw Registry-derived workflow profile surfaces {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
