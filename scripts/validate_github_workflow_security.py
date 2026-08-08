#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = [
    ROOT / ".github" / "workflows" / "ai-sdlc-commander.yml",
    ROOT / ".github" / "workflows" / "ai-sdlc-persist.yml",
]
PR_LIFECYCLE = ROOT / ".github" / "workflows" / "ai-sdlc-pr-lifecycle.yml"


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
        require(
            "verify_git_write_precondition.py" in text,
            f"{path.name}: write-capable transport lost optimistic remote-branch precondition",
        )
        require(
            '--target-ref "$TARGET_REF"' in text and '--default-branch "$DEFAULT_BRANCH"' in text,
            f"{path.name}: write precondition is missing target/default branch inputs",
        )

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
    require(
        '--repo-dir workspace' in commander,
        "Commander bootstrap persistence does not guard the checked-out workspace",
    )

    persistence = WORKFLOWS[1].read_text(encoding="utf-8")
    require("python runtime/scripts/ingest_feature_event.py" in persistence, "Persistence does not execute trusted inbox code")
    require("git -C workspace push" in persistence, "Persistence does not isolate Git push to workspace")
    require(
        '--repo-dir workspace' in persistence,
        "Persistence does not guard the checked-out workspace before push",
    )
    require("push:\n    paths:" in persistence, "Persistence automatic push trigger is missing")
    require("'state/events/**/*.yaml'" in persistence, "Persistence push trigger is not bounded to YAML event inbox paths")
    require("'state/events/**/*.yml'" in persistence, "Persistence push trigger is not bounded to YML event inbox paths")
    require(
        "runtime/scripts/resolve_feature_event_push.py" in persistence,
        "Persistence automatic mode does not use the trusted push resolver",
    )
    require(
        'echo "allow_default_branch=false"' in persistence,
        "Persistence automatic mode may bypass default-branch protection",
    )
    require(
        "expected_manifest = PurePosixPath('state', 'features', f'{event.parts[2]}.yaml').as_posix()" in persistence,
        "Persistence does not bind an event path to its matching Feature Manifest path",
    )
    require(
        "workflow_dispatch:" in persistence,
        "Persistence lost the manual recovery workflow_dispatch entry point",
    )

    lifecycle = PR_LIFECYCLE.read_text(encoding="utf-8")
    require("pull_request_review:" in lifecycle, "PR lifecycle workflow lost the approved-review trigger")
    require("contents: write" in lifecycle and "checks: read" in lifecycle, "PR lifecycle workflow permissions are incomplete")
    require("Checkout trusted control plane" in lifecycle, "PR lifecycle workflow lost trusted default-branch checkout")
    require("ref: ${{ github.event.repository.default_branch }}" in lifecycle, "PR lifecycle trusted runtime is not pinned to default branch")
    require("persist-credentials: false" in lifecycle, "PR lifecycle trusted checkout persists credentials")
    require("head_ref.startswith('gh-aw/')" in lifecycle, "PR lifecycle no longer limits privileged work PR heads to gh-aw/*")
    require("head.get('repo')" in lifecycle and "fork/cross-repository PRs are not eligible" in lifecycle, "PR lifecycle lost same-repository enforcement")
    require("base_ref == os.environ['DEFAULT_BRANCH']" in lifecycle, "PR lifecycle lost default-branch write rejection")
    require("runtime/scripts/resolve_pr_lifecycle_context.py" in lifecycle, "PR lifecycle no longer uses trusted Feature/evidence resolution")
    require(lifecycle.count("runtime/scripts/ingest_feature_event.py") == 2, "PR lifecycle must apply exactly review and verification Events through trusted ingest")
    require(lifecycle.count("runtime/scripts/verify_git_write_precondition.py") == 2, "PR lifecycle must protect both writes with optimistic preconditions")
    require("git -C workspace push origin \"HEAD:$BASE_REF\"" in lifecycle, "PR lifecycle writes outside the isolated Feature workspace")
    require("pull_request_target:" not in lifecycle, "PR lifecycle must never use pull_request_target")

    print("GitHub workflow trusted-runtime and optimistic-write isolation checks passed")


if __name__ == "__main__":
    main()
