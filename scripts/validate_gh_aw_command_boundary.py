#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-command.yml"
PROFILE_GATEWAY = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-dispatch-profile.yml"
CORE_GATEWAY = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-dispatch.yml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    command = COMMAND.read_text(encoding="utf-8")
    gateway = PROFILE_GATEWAY.read_text(encoding="utf-8")
    core = CORE_GATEWAY.read_text(encoding="utf-8")

    # The Issue Comment syntax may identify Feature state and the lifecycle dispatch
    # policy only. Execution-profile/routing knobs must not be parsed from user text.
    parser_surface = command.split("- name: Dispatch trusted role-aware gh-aw runtime gateway", 1)[0]
    for forbidden in (
        "worker=",
        "worker_workflow=",
        "engine_profile=",
        "provider=",
        "model=",
        "credential=",
        "allow_experimental=",
        "candidate_order=",
        "candidates=",
        "profile_routing=",
        "routing_policy=",
    ):
        require(
            forbidden not in parser_surface,
            f"gh-aw Issue Comment syntax exposed execution/routing selector: {forbidden}",
        )

    for secret_identity in (
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "COPILOT_GITHUB_TOKEN",
    ):
        require(secret_identity not in command, f"gh-aw command leaked credential identity: {secret_identity}")

    require("target_ref=" in command, "gh-aw command lost Feature target ref")
    require("manifest=" in command, "gh-aw command lost Manifest selector")
    require("policy=" in command, "gh-aw command lost lifecycle dispatch-policy selector")
    require(
        "gh workflow run ai-sdlc-gh-aw-dispatch.yml" in command,
        "normal Issue Comment command must enter the role-aware core runtime gateway",
    )
    require(
        "gh workflow run ai-sdlc-gh-aw-dispatch-profile.yml" not in command,
        "normal Issue Comment command still bypasses policy routing through manual profile gateway",
    )
    require(
        "--field worker_workflow=''" in command,
        "normal Issue Comment command must force empty trusted worker override so policy routing owns selection",
    )
    require(
        "steps.command.outputs.worker_workflow" not in command
        and "steps.command.outputs.engine_profile" not in command,
        "target command output can influence worker/profile identity",
    )

    # The manual profile gateway remains a separate trusted operator diagnostic path.
    require("engine_profile:" in gateway, "profile gateway lost trusted engine-profile input")
    require(
        "scripts/resolve_gh_aw_engine.py" in gateway,
        "profile gateway bypasses trusted profile registry",
    )
    require("worker_workflow=" in gateway, "profile gateway no longer resolves the compiled worker")
    require(
        "ai-sdlc-gh-aw-dispatch.yml" in gateway,
        "profile gateway no longer hands off to core runtime gateway",
    )

    # Core normal routing must own policy selection when the trusted override is empty.
    require("WORKER_OVERRIDE: ${{ inputs.worker_workflow }}" in core, "core gateway lost trusted override boundary")
    require("load_routing_policy" in core and "resolve_route" in core, "core gateway no longer resolves role-aware policy")
    require("selection_mode':'manual-trusted-profile'" in core, "manual trusted override is not audit-distinct")

    print("gh-aw command/runtime/provider/routing boundary checks passed")


if __name__ == "__main__":
    main()
