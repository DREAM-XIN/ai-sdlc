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
from datetime import datetime, timezone
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
        for command in [
            ("git", "init", "-q"),
            ("git", "config", "user.email", "audit@example.invalid"),
            ("git", "config", "user.name", "audit"),
        ]:
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

        result = run("python", "scripts/validate_public_history.py", "--json-output", "report.json", cwd=repo)
        if result.returncode != 1:
            raise AssertionError(f"expected history audit to block, got {result.returncode}: {result.stdout} {result.stderr}")
        report = json.loads((repo / "report.json").read_text(encoding="utf-8"))
        if report["outcome"] != "BLOCKED" or not any(item["kind"] == "GitHub classic token" for item in report["findings"]):
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
    secondary = module.retry_delay_seconds(403, {"Retry-After": "2", "X-RateLimit-Remaining": "17"}, '{"message":"secondary rate limit"}', 0, now=1000.0)
    if secondary is None or secondary < 2:
        raise AssertionError(secondary)
    primary = module.retry_delay_seconds(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1010"}, '{"message":"API rate limit exceeded"}', 0, now=1000.0)
    if primary is None or primary < 11:
        raise AssertionError(primary)
    if module.retry_delay_seconds(503, {}, "", 1, now=1000.0) is None:
        raise AssertionError("HTTP 503 must be retryable")
    denied = module.retry_delay_seconds(403, {"X-RateLimit-Remaining": "42"}, '{"message":"Resource not accessible by integration"}', 0, now=1000.0)
    if denied is not None:
        raise AssertionError("plain permission 403 must fail closed")


def validate_actions_baseline_delta_selection() -> None:
    module = load_actions_module()
    cutoff = datetime(2026, 8, 9, 4, 5, tzinfo=timezone.utc)
    old = {"created_at": "2026-08-09T03:00:00Z", "updated_at": "2026-08-09T04:04:59Z"}
    rerun = {"created_at": "2026-08-09T03:00:00Z", "updated_at": "2026-08-09T04:06:00Z"}
    new = {"created_at": "2026-08-09T04:05:00Z", "updated_at": "2026-08-09T04:05:00Z"}
    unknown = {"created_at": None, "updated_at": None}
    if module.item_changed_since(old, cutoff):
        raise AssertionError("pre-baseline item should be excluded")
    for item in (rerun, new, unknown):
        if not module.item_changed_since(item, cutoff):
            raise AssertionError(f"delta item must be selected: {item}")

    with tempfile.TemporaryDirectory() as temp_dir:
        baseline_path = Path(temp_dir) / "baseline.json"
        valid = {
            "version": 1,
            "repository": "example/control",
            "full_actions_audit": {
                "commit": "a" * 40,
                "run_id": 123,
                "incremental_since": "2026-08-09T04:05:00Z",
                "outcome": "PASS",
                "commit_status_state": "success",
            },
        }
        baseline_path.write_text(json.dumps(valid), encoding="utf-8")
        loaded = module.load_baseline(baseline_path, "example/control")
        if loaded["run_id"] != 123 or loaded["incremental_since"] != cutoff:
            raise AssertionError(loaded)

        invalid_repo = dict(valid)
        invalid_repo["repository"] = "other/repo"
        baseline_path.write_text(json.dumps(invalid_repo), encoding="utf-8")
        try:
            module.load_baseline(baseline_path, "example/control")
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched baseline repository must fail")

        invalid_outcome = json.loads(json.dumps(valid))
        invalid_outcome["full_actions_audit"]["outcome"] = "BLOCKED"
        baseline_path.write_text(json.dumps(invalid_outcome), encoding="utf-8")
        try:
            module.load_baseline(baseline_path, "example/control")
        except ValueError:
            pass
        else:
            raise AssertionError("non-PASS baseline must fail")


def main() -> int:
    validate_history_deleted_secret_is_detected()
    validate_actions_archive_secret_is_detected()
    validate_actions_http_retry_policy()
    validate_actions_baseline_delta_selection()
    print("Public-release historical audit regression scenarios passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
