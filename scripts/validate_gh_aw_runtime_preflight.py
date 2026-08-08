#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from gh_aw_cross_repo_runtime import parse_repository, validate_worker_workflow

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/gh_aw_runtime_preflight.py"
WORKFLOW = ROOT / ".github/workflows/ai-sdlc-gh-aw-preflight.yml"
CROSS_REPO_PREFLIGHT = ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-preflight.yml"
CROSS_REPO_GATEWAY = ROOT / ".github/workflows/ai-sdlc-gh-aw-cross-repo-dispatch.yml"
COMMAND_BRIDGE = ROOT / "templates/github/ai-sdlc-command.yml"
PROFILES = ("copilot", "codex", "claude", "gemini", "deepseek")


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


def validate_provider_preflight() -> None:
    for profile in PROFILES:
        missing = run(profile, False)
        if missing.get("status") != "MISSING_CREDENTIAL":
            fail(f"{profile}: missing credential must not be considered ready")
        if missing.get("entitlement_verified") is not False:
            fail(f"{profile}: preflight must never claim entitlement verification")

        present = run(profile, True)
        if present.get("status") != "READY_FOR_ENTITLEMENT_PROBE":
            fail(f"{profile}: valid lock + credential must reach entitlement probe readiness")
        if present.get("compiler_version") != "v0.83.4" or present.get("lock_strict") is not True:
            fail(f"{profile}: preflight must verify pinned strict compiler metadata")
        if present.get("entitlement_verified") is not False:
            fail(f"{profile}: credential presence must not imply provider entitlement")
        if profile == "deepseek":
            if present.get("provider") != "deepseek" or present.get("protocol") != "openai-compatible":
                fail("deepseek: preflight must expose provider/protocol identity")
            if present.get("maturity") != "experimental":
                fail("deepseek: preflight must not overstate live maturity")

    text = WORKFLOW.read_text(encoding="utf-8")
    if "permissions:\n  contents: read" not in text:
        fail("preflight workflow must remain repository read-only")
    for forbidden in ("gh workflow run ai-sdlc-gh-aw-dispatch", "contents: write", "state/features/", "state/events/"):
        if forbidden in text:
            fail(f"preflight workflow contains forbidden mutation/dispatch marker: {forbidden}")
    for marker in ("secrets.COPILOT_GITHUB_TOKEN != ''", "secrets.DEEPSEEK_API_KEY != ''"):
        if marker not in text:
            fail(f"preflight workflow must test credential presence without exposing values: {marker}")
    if "READY_FOR_ENTITLEMENT_PROBE" not in text:
        fail("preflight workflow must explain entitlement remains unverified")
    if "rate-limit headroom" not in text:
        fail("preflight workflow must not imply static checks prove provider rate-limit capacity")


def validate_cross_repo_preflight() -> None:
    target = parse_repository("DREAM-XIN/private-target")
    require(target == {"repository": "DREAM-XIN/private-target", "owner": "DREAM-XIN", "repo_name": "private-target"}, "cross-repo repository parser lost exact identity")
    for invalid in ("private-target", "DREAM-XIN/../private-target", "DREAM-XIN/private target", "/private-target"):
        try:
            parse_repository(invalid)
        except ValueError:
            pass
        else:
            fail(f"invalid cross-repo repository identity was accepted: {invalid}")

    validate_worker_workflow("ai-sdlc-gh-aw-worker.lock.yml")
    validate_worker_workflow("ai-sdlc-gh-aw-worker-deepseek.lock.yml")
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
    for forbidden in ("provider=", "model=", "engine_profile=", "worker_workflow=", "policy="):
        require(forbidden not in gh_aw_syntax, f"target command surface exposes forbidden runtime selector: {forbidden}")


def main() -> int:
    validate_provider_preflight()
    validate_cross_repo_preflight()
    print("gh-aw provider-neutral and cross-repository runtime preflight checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
