#!/usr/bin/env python3
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "runtimes/gh-aw/engine-profiles.yaml"
SOURCE = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker-deepseek.md"
LOCK = ROOT / ".github/workflows/ai-sdlc-gh-aw-worker-deepseek.lock.yml"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    cfg = registry["profiles"]["deepseek"]
    model = cfg["model"]
    source = SOURCE.read_text(encoding="utf-8")
    lock = LOCK.read_text(encoding="utf-8")

    require(f'  model: "{model}"\n' in source, "DeepSeek source must pin engine.model for gh-aw audit metadata")
    require(f"    COPILOT_MODEL: {model}\n" in source, "DeepSeek source must pin COPILOT_MODEL for provider routing")
    require(f'GH_AW_INFO_MODEL: "{model}"' in lock, "compiled DeepSeek run metadata must report the effective BYOK model")
    require(f'GH_AW_ENGINE_MODEL: "{model}"' in lock, "compiled DeepSeek telemetry must report the effective BYOK model")
    require("claude-sonnet-4.6" not in lock, "compiled DeepSeek worker must not retain the Copilot default model fallback")

    print("gh-aw BYOK effective model routing and audit metadata checks passed")


if __name__ == "__main__":
    main()
