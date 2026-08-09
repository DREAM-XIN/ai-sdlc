#!/usr/bin/env python3
"""Validate GitHub Actions supply-chain pins and dangerous trigger patterns."""

import json
import re
from pathlib import Path

from gh_aw_provider_registry import RegistryValidationError, load_registry

ROOT = Path(__file__).resolve().parents[1]
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)(?:\s+#.*)?$")
INSTALL_PLACEHOLDERS = {
    "DREAM-XIN/ai-sdlc/.github/actions/control@REPLACE_WITH_AI_SDLC_FULL_SHA",
    "DREAM-XIN/ai-sdlc/.github/actions/resolve-event-push@REPLACE_WITH_AI_SDLC_FULL_SHA",
}
PLACEHOLDER_MARKER = "ai-sdlc-install-placeholder"
GHAW_LOCK_CANDIDATE = re.compile(r"^ai-sdlc-gh-aw-worker(?:-[a-z][a-z0-9-]*)?\.lock\.yml$")
GHAW_METADATA_PREFIX = "# gh-aw-metadata: "
PINNED_GHAW_COMPILER = "v0.83.4"


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


def gh_aw_lock_metadata(path: Path, text: str, trusted_lock_names: frozenset[str]):
    """Return trusted gh-aw metadata for an exact Registry worker lock, else None.

    The filename must be an exact worker_workflow identity from the fully validated
    Registry, and the file must carry the repository-pinned strict compiler
    attestation. Provider names never become security-authority branches here.
    """
    if (
        path.parent != ROOT / ".github" / "workflows"
        or path.name not in trusted_lock_names
    ):
        return None
    first_line = text.splitlines()[0] if text else ""
    if not first_line.startswith(GHAW_METADATA_PREFIX):
        return None
    try:
        metadata = json.loads(first_line[len(GHAW_METADATA_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if metadata.get("strict") is not True:
        return None
    if metadata.get("compiler_version") != PINNED_GHAW_COMPILER:
        return None
    if metadata.get("schema_version") != "v4":
        return None
    return metadata


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
        if is_template(path) and target in INSTALL_PLACEHOLDERS:
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


def validate_workflow_policy(path: Path, text: str, trusted_lock_names: frozenset[str]):
    errors = []
    if ".github/workflows" not in path.as_posix():
        return errors

    trusted_gh_aw_lock = gh_aw_lock_metadata(path, text, trusted_lock_names)
    if GHAW_LOCK_CANDIDATE.fullmatch(path.name):
        if path.name not in trusted_lock_names:
            errors.append(f"{path}: gh-aw worker lock is not registered in the trusted Registry")
        elif trusted_gh_aw_lock is None:
            errors.append(
                f"{path}: gh-aw worker lock must carry strict {PINNED_GHAW_COMPILER} v4 compiler metadata"
            )

    forbidden = {
        "pull_request_target:": "pull_request_target is forbidden for v0.1 control workflows",
        "workflow_run:": "workflow_run is forbidden for v0.1 control workflows",
        "permissions: write-all": "write-all permissions are forbidden",
        "secrets: inherit": "secrets: inherit is forbidden",
    }
    for needle, message in forbidden.items():
        if needle in text:
            errors.append(f"{path}: {message}")

    # AI-SDLC-authored workflows must never persist checkout credentials. The
    # official gh-aw strict compiler can emit persist-credentials:true inside
    # generated worker lock safe-output plumbing. Accept that behavior only for
    # an exact Registry worker identity with pinned strict compiler attestation;
    # every other workflow remains subject to the stronger AI-SDLC rule.
    if "persist-credentials: true" in text and trusted_gh_aw_lock is None:
        errors.append(f"{path}: checkout credentials must not be explicitly persisted")

    # A PR-triggered workflow must remain read-only. This prevents a normal
    # validation workflow from silently becoming a privileged PR execution path.
    if "pull_request:" in text and "contents: write" in text:
        errors.append(f"{path}: pull_request-triggered workflow must not request contents: write")
    return errors


def main():
    errors = []
    try:
        registry = load_registry()
    except RegistryValidationError as exc:
        raise SystemExit(f"trusted gh-aw Registry invalid: {exc}") from None
    trusted_lock_names = frozenset(registry.trusted_worker_workflows())

    files = workflow_files()
    if not files:
        raise SystemExit("no GitHub Action/workflow files found")
    for path in files:
        text = path.read_text(encoding="utf-8")
        errors.extend(validate_uses(path, text))
        errors.extend(validate_workflow_policy(path, text, trusted_lock_names))

    if errors:
        for error in errors:
            print(error)
        raise SystemExit(2)

    print(f"Immutable GitHub Action pins and trigger policy passed for {len(files)} files")


if __name__ == "__main__":
    main()
