#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-command.yml"
PROFILE_GATEWAY = ROOT / ".github" / "workflows" / "ai-sdlc-gh-aw-dispatch-profile.yml"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    command = COMMAND.read_text(encoding="utf-8")
    gateway = PROFILE_GATEWAY.read_text(encoding="utf-8")

    # The maintenance command is a control-plane/runtime entrypoint. It may identify
    # Feature state and routing policy, but it must not accept or resolve provider,
    # model, engine profile, credential, or compiled-worker details.
    for forbidden in (
        "worker=",
        "worker_workflow",
        "engine_profile=",
        "DEEPSEEK",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "COPILOT_GITHUB_TOKEN",
    ):
        require(forbidden not in command, f"gh-aw command leaked execution-plane selector/credential: {forbidden}")

    require("target_ref=" in command, "gh-aw command lost Feature target ref")
    require("manifest=" in command, "gh-aw command lost Manifest selector")
    require("policy=" in command, "gh-aw command lost dispatch-policy selector")
    require(
        "gh workflow run ai-sdlc-gh-aw-dispatch-profile.yml" in command,
        "gh-aw command must hand off to the trusted execution-profile gateway",
    )
    require(
        "--field engine_profile=" not in command,
        "control-plane command must not override the profile gateway's trusted default/selection policy",
    )

    # Provider/model selection remains in the execution plane behind the trusted
    # registry-backed profile gateway.
    require("engine_profile:" in gateway, "profile gateway lost trusted engine-profile input")
    require("scripts/resolve_gh_aw_engine.py" in gateway, "profile gateway bypasses trusted profile registry")
    require("worker_workflow=" in gateway, "profile gateway no longer resolves the compiled worker")
    require("ai-sdlc-gh-aw-dispatch.yml" in gateway, "profile gateway no longer hands off to core runtime gateway")

    print("gh-aw command/runtime/provider boundary checks passed")


if __name__ == "__main__":
    main()
