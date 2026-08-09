#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gh_aw_cross_repo_runtime import parse_repository, validate_worker_workflow
from gh_aw_provider_registry import load_registry

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/gh_aw_runtime_preflight.py"
WORKFLOW = ROOT / ".github/workflows/ai-sdlc-gh-aw-preflight.yml"
CROSS_REPO_PREFLIGHT = ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-preflight.yml"
CROSS_REPO_GATEWAY = ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-dispatch.yml"
COMMAND_BRIDGE = ROOT / "templates/github/ai-sdlc-command.yml"

LEGACY_COMPATIBILITY_BASELINE = {
    "copilot": ("native", "native", "reference"),
    "codex": ("native", "native", "reference"),
    "claude": ("native", "native", "reference"),
    "gemini": ("native", "native", "reference"),
    "deepseek": ("deepseek", "openai-compatible", "experimental"),
}


def run(profile: str, present: bool) -> dict:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), profile, "--credential-present", str(present).lower()],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def fail(message: str) -> None:
    raise SystemExit(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def expected_presence_expression(profile, credential: str) -> str:
    if profile.credential_source == "secret":
        return f"secrets.{credential} != ''"
    if profile.credential_source == "github-token":
        require(
            credential == profile.credential and not profile.credential_aliases,
            f"{profile.profile_id}: github-token profile must have one canonical credential identity",
        )
        return "github.token != ''"
    fail(f"{profile.profile_id}: unsupported credential source escaped Registry validation")


def validate_provider_preflight() -> None:
    registry = load_registry()
    profiles = {profile.profile_id: profile for profile in registry.profiles}
    require(
        set(LEGACY_COMPATIBILITY_BASELINE).issubset(profiles),
        "legacy preflight compatibility profile missing",
    )
    for profile_id, expected in LEGACY_COMPATIBILITY_BASELINE.items():
        profile = profiles[profile_id]
        require(
            (profile.provider, profile.protocol, profile.maturity) == expected,
            f"{profile_id}: legacy compatibility identity/maturity drifted",
        )

    for profile in registry.profiles:
        missing = run(profile.profile_id, False)
        require(
            missing.get("status") == "MISSING_CREDENTIAL",
            f"{profile.profile_id}: missing credential must not be considered ready",
        )
        require(
            missing.get("entitlement_verified") is False,
            f"{profile.profile_id}: preflight must never claim entitlement verification",
        )

        present = run(profile.profile_id, True)
        require(
            present.get("status") == "READY_FOR_ENTITLEMENT_PROBE",
            f"{profile.profile_id}: valid lock + credential must reach entitlement probe readiness",
        )
        require(
            present.get("compiler_version") == "v0.83.4" and present.get("lock_strict") is True,
            f"{profile.profile_id}: preflight must verify pinned strict compiler metadata",
        )
        require(
            present.get("entitlement_verified") is False,
            f"{profile.profile_id}: credential presence must not imply provider entitlement",
        )
        require(
            (present.get("provider"), present.get("protocol"), present.get("maturity"))
            == (profile.provider, profile.protocol, profile.maturity),
            f"{profile.profile_id}: Registry-driven identity/maturity drifted",
        )

    text = WORKFLOW.read_text(encoding="utf-8")
    require("permissions:\n  contents: read" in text, "preflight workflow must remain repository read-only")
    for forbidden in ("gh workflow run ai-sdlc-gh-aw-dispatch", "contents: write", "state/features/", "state/events/"):
        require(forbidden not in text, f"preflight workflow contains forbidden mutation/dispatch marker: {forbidden}")
    for profile in registry.profiles:
        for credential in (profile.credential, *profile.credential_aliases):
            expected = expected_presence_expression(profile, credential)
            require(
                expected in text,
                f"preflight workflow must derive presence from Registry credential source without exposing values: {profile.profile_id}/{credential}",
            )
            if profile.credential_source == "github-token":
                require(
                    f"secrets.{credential} != ''" not in text,
                    f"{profile.profile_id}: github-token readiness regressed to repository secret lookup",
                )
    require("READY_FOR_ENTITLEMENT_PROBE" in text, "preflight workflow must explain entitlement remains unverified")
    require("rate-limit headroom" in text, "preflight workflow must not imply static checks prove provider rate-limit capacity")


def validate_cross_repo_preflight() -> None:
    registry = load_registry()
    target = parse_repository("DREAM-XIN/private-target")
    require(
        target == {"repository": "DREAM-XIN/private-target", "owner": "DREAM-XIN", "repo_name": "private-target"},
        "cross-repo repository parser lost exact identity",
    )
    for invalid in ("private-target", "DREAM-XIN/../private-target", "DREAM-XIN/private target", "/private-target"):
        try:
            parse_repository(invalid)
        except ValueError:
            pass
        else:
            fail(f"invalid cross-repo repository identity was accepted: {invalid}")

    for profile in registry.profiles:
        result = validate_worker_workflow(profile.worker_workflow)
        require(result.get("profile") == profile.profile_id, f"{profile.profile_id}: registered worker lookup lost exact profile identity")
    for untrusted in ("validate.yml", "ai-sdlc-gh-aw-result.yml", "../worker.yml"):
        try:
            validate_worker_workflow(untrusted)
        except ValueError:
            pass
        else:
            fail(f"unregistered worker workflow was accepted: {untrusted}")

    preflight = CROSS_REPO_PREFLIGHT.read_text(encoding="utf-8")
    require("permissions:\n  contents: read" in preflight, "cross-repo runtime preflight must remain control-repository read-only")
    input_block = preflight.split("inputs:", 1)[1].split("permissions:", 1)[0]
    require("target_repository:" in input_block, "cross-repo preflight target repository input missing")
    for forbidden in ("provider", "model", "engine_profile", "worker_workflow", "target_ref", "manifest_path"):
        require(forbidden not in input_block, f"cross-repo runtime preflight exposes forbidden input: {forbidden}")
    for marker in (
        "vars.AI_SDLC_RUNTIME_APP_CLIENT_ID != ''",
        "secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY != ''",
        "MISSING_RUNTIME_APP_CLIENT_ID",
        "MISSING_RUNTIME_APP_PRIVATE_KEY",
        "permission-contents: read",
        "permission-metadata: read",
        "scripts/gh_aw_cross_repo_runtime.py target",
    ):
        require(marker in preflight, f"cross-repo runtime preflight missing required marker: {marker}")
    for forbidden in (
        "permission-contents: write",
        "permission-pull-requests: write",
        "permission-actions: write",
        "contents: write",
        "state/features/",
        "state/events/",
        "gh workflow run",
    ):
        require(forbidden not in preflight, f"cross-repo runtime preflight contains forbidden mutation/dispatch marker: {forbidden}")

    gateway = CROSS_REPO_GATEWAY.read_text(encoding="utf-8")
    require("scripts/gh_aw_cross_repo_runtime.py worker" in gateway, "cross-repo gateway does not bind worker_workflow to trusted registry")
    require("scripts/gh_aw_cross_repo_runtime.py target" in gateway, "cross-repo gateway does not use shared repository identity validation")
    config_index = gateway.find("Validate trusted Runtime App configuration")
    token_index = gateway.find("Mint exact-target read token")
    require(config_index >= 0 and token_index >= 0 and config_index < token_index, "Runtime App configuration must fail before target token mint")
    for marker in (
        "MISSING_RUNTIME_APP_CLIENT_ID",
        "MISSING_RUNTIME_APP_PRIVATE_KEY",
        "vars.AI_SDLC_RUNTIME_APP_CLIENT_ID != ''",
        "secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY != ''",
    ):
        require(marker in gateway, f"real cross-repo gateway missing fail-fast runtime App check: {marker}")

    command = COMMAND_BRIDGE.read_text(encoding="utf-8")
    gh_aw_syntax = next(line for line in command.splitlines() if "gh_aw = re.fullmatch" in line)
    for forbidden in ("provider=", "model=", "engine_profile=", "credential=", "worker_workflow=", "policy="):
        require(forbidden not in gh_aw_syntax, f"target command surface exposes forbidden runtime selector: {forbidden}")


def main() -> int:
    validate_provider_preflight()
    validate_cross_repo_preflight()
    print("gh-aw registry-driven credential-source and cross-repository runtime preflight checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
