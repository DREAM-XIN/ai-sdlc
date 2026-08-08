#!/usr/bin/env python3
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from runtime_router import load_yaml
from validate_target_installation import validate_installation

ROOT = Path(__file__).resolve().parents[1]
FULL_SHA = "0123456789abcdef0123456789abcdef01234567"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_text(root: Path, relative: str, content: str = "fixture\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def valid_workspace(root: Path):
    adapter = deepcopy(load_yaml(ROOT / "examples" / "project-adapters" / "generic.yaml"))
    write_text(root, ".ai-sdlc/project.yaml", yaml.safe_dump(adapter, sort_keys=False))
    for relative in ("AGENTS.md", "CONTRIBUTING.md", "README.md", "docs/architecture.md"):
        write_text(root, relative)
    write_text(
        root,
        ".github/workflows/ai-sdlc-plan.yml",
        "name: fixture\nsteps:\n  - uses: DREAM-XIN/ai-sdlc/.github/actions/control@"
        + FULL_SHA
        + "\n",
    )
    return adapter


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid_workspace(root)
        ready = validate_installation(
            root, expected_repository="example/sample-app", expected_default_branch="main"
        )
        require(ready["outcome"] == "READY", f"valid installation rejected: {ready}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        adapter = valid_workspace(root)
        del adapter["context"]["rules"]
        write_text(root, ".ai-sdlc/project.yaml", yaml.safe_dump(adapter, sort_keys=False))
        result = validate_installation(root)
        require(result["outcome"] == "INVALID", "missing context.rules unexpectedly passed")
        require("context" in "\n".join(result["errors"]), f"missing-rules diagnostic absent: {result}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        adapter = valid_workspace(root)
        adapter["commands"][0]["purpose"] = "verification"
        write_text(root, ".ai-sdlc/project.yaml", yaml.safe_dump(adapter, sort_keys=False))
        result = validate_installation(root)
        require(result["outcome"] == "INVALID", "invalid command purpose unexpectedly passed")
        require("verification" in "\n".join(result["errors"]), f"invalid-purpose diagnostic absent: {result}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid_workspace(root)
        (root / "AGENTS.md").unlink()
        result = validate_installation(root)
        require(result["outcome"] == "INVALID", "missing AGENTS.md unexpectedly passed")
        require("AGENTS.md" in "\n".join(result["errors"]), f"missing-AGENTS diagnostic absent: {result}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid_workspace(root)
        (root / "docs" / "architecture.md").unlink()
        result = validate_installation(root)
        require(result["outcome"] == "INVALID", "missing context.read unexpectedly passed")
        require("context.read" in "\n".join(result["errors"]), f"missing-read diagnostic absent: {result}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid_workspace(root)
        write_text(
            root,
            ".github/workflows/ai-sdlc-plan.yml",
            "uses: DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA # ai-sdlc-install-placeholder\n",
        )
        result = validate_installation(root)
        require(result["outcome"] == "INVALID", "install placeholder unexpectedly passed")
        require("placeholder" in "\n".join(result["errors"]), f"placeholder diagnostic absent: {result}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid_workspace(root)
        write_text(
            root,
            ".github/workflows/ai-sdlc-plan.yml",
            "uses: DREAM-XIN/ai-sdlc/.github/actions/control@main\n",
        )
        result = validate_installation(root)
        require(result["outcome"] == "INVALID", "moving AI-SDLC ref unexpectedly passed")
        require("40-character" in "\n".join(result["errors"]), f"moving-ref diagnostic absent: {result}")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid_workspace(root)
        result = validate_installation(
            root, expected_repository="other/repository", expected_default_branch="develop"
        )
        require(result["outcome"] == "INVALID", "repository identity mismatch unexpectedly passed")
        errors = "\n".join(result["errors"])
        require("full_name mismatch" in errors, f"repository mismatch diagnostic absent: {result}")
        require("default_branch mismatch" in errors, f"default-branch mismatch diagnostic absent: {result}")

    print("Target installation schema, context, identity, and immutable-pin preflight scenarios passed")


if __name__ == "__main__":
    main()
