#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [
    ROOT / ".github" / "workflows" / "ai-sdlc-commander.yml",
    ROOT / ".github" / "workflows" / "ai-sdlc-persist.yml",
]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    for path in WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        require("Checkout trusted control plane" in text, f"{path.name}: trusted checkout missing")
        require("ref: ${{ github.event.repository.default_branch }}" in text, f"{path.name}: trusted runtime is not pinned to default branch")
        require("path: runtime" in text, f"{path.name}: trusted runtime path missing")
        require("path: workspace" in text, f"{path.name}: target workspace isolation missing")
        require("pip install -r runtime/requirements-dev.txt" in text, f"{path.name}: dependencies are not loaded from trusted runtime")
        require("python scripts/" not in text, f"{path.name}: executes Python from checkout root/target ref")
        require("pip install -r requirements-dev.txt" not in text, f"{path.name}: installs dependencies from checkout root/target ref")

    commander = WORKFLOWS[0].read_text(encoding="utf-8")
    require("permissions:\n      contents: read" in commander, "Commander plan job lost read-only permission")
    require("persist-credentials: false" in commander, "Commander read-only checkout persists credentials")
    require(
        "policy_args=(--policy runtime/dispatch/default.yaml)" in commander,
        "Commander no longer defaults to the trusted runtime Dispatch Policy",
    )
    require(
        "workspace/.ai-sdlc/project.yaml" in commander and "--project workspace/.ai-sdlc/project.yaml" in commander,
        "Commander no longer loads the canonical target Project Adapter",
    )

    persistence = WORKFLOWS[1].read_text(encoding="utf-8")
    require("python runtime/scripts/ingest_feature_event.py" in persistence, "Persistence does not execute trusted inbox code")
    require("git -C workspace push" in persistence, "Persistence does not isolate Git push to workspace")

    print("GitHub workflow trusted-runtime isolation checks passed")


if __name__ == "__main__":
    main()
