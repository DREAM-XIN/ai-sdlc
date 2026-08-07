#!/usr/bin/env python3
"""Validate GitHub Actions supply-chain pins and dangerous trigger patterns."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)(?:\s+#.*)?$")
INSTALL_PLACEHOLDER = "DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA"
PLACEHOLDER_MARKER = "ai-sdlc-install-placeholder"


def workflow_files():
    files = []
    for root in (ROOT / ".github" / "workflows", ROOT / ".github" / "actions"):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".yml", ".yaml"}:
                files.append(path)
    template_root = ROOT / "templates" / "github"
    if template_root.exists():
        files.extend(path for path in template_root.glob("*.yml") if path.is_file())
        files.extend(path for path in template_root.glob("*.yaml") if path.is_file())
    return sorted(set(files))


def is_template(path: Path) -> bool:
    try:
        path.relative_to(ROOT / "templates" / "github")
        return True
    except ValueError:
        return False


def validate_uses(path: Path, text: str):
    errors = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = USES.match(line)
        if not match:
            continue
        target = match.group(1)
        if target.startswith("./"):
            continue
        if "@" not in target:
            errors.append(f"{path}:{line_number}: external action reference lacks @ref: {target}")
            continue
        action, ref = target.rsplit("@", 1)
        if is_template(path) and target == INSTALL_PLACEHOLDER:
            if PLACEHOLDER_MARKER not in line:
                errors.append(
                    f"{path}:{line_number}: AI-SDLC install placeholder must carry '# {PLACEHOLDER_MARKER}'"
                )
            continue
        if not FULL_SHA.fullmatch(ref):
            errors.append(
                f"{path}:{line_number}: external action must be pinned to a full 40-char commit SHA: {action}@{ref}"
            )
    return errors


def validate_workflow_policy(path: Path, text: str):
    errors = []
    if ".github/workflows" not in path.as_posix():
        return errors

    forbidden = {
        "pull_request_target:": "pull_request_target is forbidden for v0.1 control workflows",
        "workflow_run:": "workflow_run is forbidden for v0.1 control workflows",
        "permissions: write-all": "write-all permissions are forbidden",
        "secrets: inherit": "secrets: inherit is forbidden",
        "persist-credentials: true": "checkout credentials must not be explicitly persisted",
    }
    for needle, message in forbidden.items():
        if needle in text:
            errors.append(f"{path}: {message}")

    # A PR-triggered workflow must remain read-only. This prevents a normal
    # validation workflow from silently becoming a privileged PR execution path.
    if "pull_request:" in text and "contents: write" in text:
        errors.append(f"{path}: pull_request-triggered workflow must not request contents: write")
    return errors


def main():
    errors = []
    files = workflow_files()
    if not files:
        raise SystemExit("no GitHub Action/workflow files found")
    for path in files:
        text = path.read_text(encoding="utf-8")
        errors.extend(validate_uses(path, text))
        errors.extend(validate_workflow_policy(path, text))

    if errors:
        for error in errors:
            print(error)
        raise SystemExit(2)

    print(f"Immutable GitHub Action pins and trigger policy passed for {len(files)} files")


if __name__ == "__main__":
    main()
