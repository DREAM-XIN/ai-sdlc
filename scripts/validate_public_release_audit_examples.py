#!/usr/bin/env python3
"""Regression checks for the public-release historical audit tooling."""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def load_actions_module():
    path = ROOT / "scripts" / "audit_public_actions_history.py"
    spec = importlib.util.spec_from_file_location("audit_public_actions_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load audit_public_actions_history.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_history_deleted_secret_is_detected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        repo = Path(temp_dir)
        (repo / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts" / "validate_public_history.py", repo / "scripts" / "validate_public_history.py")
        commands = [
            ("git", "init", "-q"),
            ("git", "config", "user.email", "audit@example.invalid"),
            ("git", "config", "user.name", "audit"),
        ]
        for command in commands:
            result = run(*command, cwd=repo)
            if result.returncode:
                raise AssertionError(result.stderr)

        (repo / "README.md").write_text("clean\n", encoding="utf-8")
        run("git", "add", ".", cwd=repo)
        if run("git", "commit", "-qm", "initial", cwd=repo).returncode:
            raise AssertionError("failed to create initial fixture commit")

        token = "gh" + "p_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
        (repo / "removed-secret.txt").write_text(f"TOKEN={token}\n", encoding="utf-8")
        run("git", "add", "removed-secret.txt", cwd=repo)
        if run("git", "commit", "-qm", "add historical fixture", cwd=repo).returncode:
            raise AssertionError("failed to create secret fixture commit")
        run("git", "rm", "-q", "removed-secret.txt", cwd=repo)
        if run("git", "commit", "-qm", "remove historical fixture", cwd=repo).returncode:
            raise AssertionError("failed to remove secret fixture")

        result = run(
            "python",
            "scripts/validate_public_history.py",
            "--json-output",
            "report.json",
            cwd=repo,
        )
        if result.returncode != 1:
            raise AssertionError(f"expected history audit to block, got {result.returncode}: {result.stdout} {result.stderr}")
        report = json.loads((repo / "report.json").read_text(encoding="utf-8"))
        if report["outcome"] != "BLOCKED":
            raise AssertionError(report)
        if not any(item["kind"] == "GitHub classic token" for item in report["findings"]):
            raise AssertionError(report)


def validate_actions_archive_secret_is_detected() -> None:
    module = load_actions_module()
    token = "gh" + "p_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        archive.writestr("agent-output.txt", f"TOKEN={token}\n")

    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("nested.zip", inner.getvalue())

    findings = module.scan_zip_bytes(outer.getvalue(), "fixture")
    if not any(item["kind"] == "GitHub classic token" for item in findings):
        raise AssertionError(findings)


def validate_actions_http_retry_policy() -> None:
    module = load_actions_module()

    secondary = module.retry_delay_seconds(
        403,
        {"Retry-After": "2", "X-RateLimit-Remaining": "17"},
        '{"message":"You have exceeded a secondary rate limit"}',
        0,
        now=1000.0,
    )
    if secondary is None or secondary < 2:
        raise AssertionError(f"secondary rate limit was not retryable: {secondary!r}")

    primary = module.retry_delay_seconds(
        403,
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"},
        '{"message":"API rate limit exceeded"}',
        0,
        now=1000.0,
    )
    if primary is None or primary < 11:
        raise AssertionError(f"primary rate limit reset was not respected: {primary!r}")

    unavailable = module.retry_delay_seconds(503, {}, "", 1, now=1000.0)
    if unavailable is None:
        raise AssertionError("HTTP 503 must be retryable")

    permission_denied = module.retry_delay_seconds(
        403,
        {"X-RateLimit-Remaining": "42"},
        '{"message":"Resource not accessible by integration"}',
        0,
        now=1000.0,
    )
    if permission_denied is not None:
        raise AssertionError(f"plain permission 403 must fail closed without retry: {permission_denied!r}")


def main() -> int:
    validate_history_deleted_secret_is_detected()
    validate_actions_archive_secret_is_detected()
    validate_actions_http_retry_policy()
    print("Public-release historical audit regression scenarios passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
