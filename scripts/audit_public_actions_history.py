#!/usr/bin/env python3
"""Audit retained GitHub Actions logs and artifacts before making a repo public.

The script intentionally reports only finding type and location; it never prints
matched secret values. It expects GITHUB_TOKEN and GITHUB_REPOSITORY.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping

API_ROOT = "https://api.github.com"
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_NESTED_ZIP_DEPTH = 2
MAX_WORKERS = 3
MAX_HTTP_ATTEMPTS = 5
MAX_RETRY_SLEEP_SECONDS = 120
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub classic token", re.compile(r"gh" + r"p_[A-Za-z0-9]{30,}")),
    ("GitHub fine-grained token", re.compile(r"github" + r"_pat_[A-Za-z0-9_]{40,}")),
    ("OpenAI project API key", re.compile(r"sk" + r"-proj-[A-Za-z0-9_-]{20,}")),
    ("OpenAI/DeepSeek-style API key", re.compile(r"sk" + r"-[A-Za-z0-9]{20,}")),
    ("Anthropic API key", re.compile(r"sk" + r"-ant-[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"AI" + r"za[0-9A-Za-z_-]{30,}")),
    ("AWS access key", re.compile(r"AK" + r"IA[0-9A-Z]{16}")),
    ("private key block", re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def request_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-sdlc-public-release-audit",
    }


def retry_delay_seconds(
    status: int,
    headers: Mapping[str, str],
    body_text: str,
    attempt: int,
    *,
    now: float | None = None,
) -> float | None:
    """Return a bounded retry delay only for clearly transient HTTP failures.

    A plain permission-denied 403 is never retried. A 403 is retryable only
    when GitHub exposes rate-limit evidence through Retry-After,
    X-RateLimit-Remaining=0, or the standard rate-limit error message.
    """

    now_value = time.time() if now is None else now
    retry_after = headers.get("Retry-After")
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    message = body_text.lower()
    rate_limited = (
        bool(retry_after)
        or remaining == "0"
        or "secondary rate limit" in message
        or "rate limit exceeded" in message
        or "rate limit" in message and status == 403
    )

    retryable = status in {429, 500, 502, 503, 504} or (status == 403 and rate_limited)
    if not retryable or attempt >= MAX_HTTP_ATTEMPTS - 1:
        return None

    delay = float(min(2**attempt, 16))
    if retry_after:
        try:
            delay = max(delay, float(retry_after))
        except ValueError:
            pass
    if remaining == "0" and reset:
        try:
            delay = max(delay, float(reset) - now_value + 1.0)
        except ValueError:
            pass
    return max(0.0, min(delay, float(MAX_RETRY_SLEEP_SECONDS)))


def safe_http_error_kind(prefix: str, error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"{prefix}: HTTP {error.code}"
    return f"{prefix}: {type(error).__name__}"


def github_json(repository: str, token: str, path: str) -> dict[str, Any]:
    url = f"{API_ROOT}/repos/{repository}{path}"
    for attempt in range(MAX_HTTP_ATTEMPTS):
        req = urllib.request.Request(url, headers=request_headers(token))
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            body_text = error.read().decode("utf-8", errors="ignore")
            delay = retry_delay_seconds(error.code, error.headers, body_text, attempt)
            if delay is None:
                raise
            print(
                f"GitHub API transient HTTP {error.code}; retrying request after bounded backoff "
                f"(attempt {attempt + 2}/{MAX_HTTP_ATTEMPTS}).",
                file=sys.stderr,
            )
            time.sleep(delay)
        except urllib.error.URLError:
            if attempt >= MAX_HTTP_ATTEMPTS - 1:
                raise
            time.sleep(float(min(2**attempt, 16)))
    raise RuntimeError("GitHub JSON request retry loop exhausted")


def download_github_archive(repository: str, token: str, path: str) -> bytes | None:
    url = f"{API_ROOT}/repos/{repository}{path}"
    opener = urllib.request.build_opener(NoRedirect)

    for attempt in range(MAX_HTTP_ATTEMPTS):
        req = urllib.request.Request(url, headers=request_headers(token))
        try:
            try:
                response = opener.open(req, timeout=60)
            except urllib.error.HTTPError as error:
                if error.code in {404, 410}:
                    return None
                if error.code in {301, 302, 303, 307, 308}:
                    location = error.headers.get("Location")
                    if not location:
                        raise
                    response = urllib.request.urlopen(location, timeout=60)
                else:
                    body_text = error.read().decode("utf-8", errors="ignore")
                    delay = retry_delay_seconds(error.code, error.headers, body_text, attempt)
                    if delay is None:
                        raise
                    print(
                        f"GitHub archive endpoint transient HTTP {error.code}; retrying after bounded backoff "
                        f"(attempt {attempt + 2}/{MAX_HTTP_ATTEMPTS}).",
                        file=sys.stderr,
                    )
                    time.sleep(delay)
                    continue

            with response:
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES} bytes")
            return data
        except urllib.error.URLError:
            if attempt >= MAX_HTTP_ATTEMPTS - 1:
                raise
            time.sleep(float(min(2**attempt, 16)))

    raise RuntimeError("GitHub archive request retry loop exhausted")


def paged_items(repository: str, token: str, path: str, key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        payload = github_json(repository, token, f"{path}{separator}per_page=100&page={page}")
        batch = payload.get(key, [])
        if not isinstance(batch, list):
            raise ValueError(f"unexpected GitHub API payload for {path}: missing list {key}")
        items.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return items


def scan_text(text: str, location: str) -> list[dict[str, str | int]]:
    findings: list[dict[str, str | int]] = []
    for label, pattern in PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(
                {
                    "location": location,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "kind": label,
                }
            )
    return findings


def scan_zip_bytes(data: bytes, location: str, depth: int = 0) -> list[dict[str, str | int]]:
    findings: list[dict[str, str | int]] = []
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        text = data.decode("utf-8", errors="ignore")
        return scan_text(text, location)

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            entry_location = f"{location}!/{info.filename}"
            suffix = Path(info.filename).suffix.lower()
            if suffix in SENSITIVE_SUFFIXES:
                findings.append({"location": entry_location, "line": 0, "kind": "sensitive key/certificate file"})
            if info.file_size > MAX_ENTRY_BYTES:
                findings.append({"location": entry_location, "line": 0, "kind": "oversized archive entry was not scanned"})
                continue
            raw = archive.read(info)
            if suffix == ".zip" and depth < MAX_NESTED_ZIP_DEPTH:
                findings.extend(scan_zip_bytes(raw, entry_location, depth + 1))
                continue
            if b"\0" in raw[:8192]:
                continue
            findings.extend(scan_text(raw.decode("utf-8", errors="ignore"), entry_location))
    return findings


def scan_run_log(repository: str, token: str, run_id: int) -> tuple[int, int, list[dict[str, str | int]]]:
    location = f"run:{run_id}:logs"
    try:
        data = download_github_archive(repository, token, f"/actions/runs/{run_id}/logs")
    except (ValueError, urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        return 0, 0, [{"location": location, "line": 0, "kind": safe_http_error_kind("log audit error", error)}]
    if data is None:
        return 0, 1, []
    return 1, 0, scan_zip_bytes(data, location)


def scan_artifact(repository: str, token: str, artifact: dict[str, Any]) -> tuple[int, int, list[dict[str, str | int]]]:
    artifact_id = int(artifact["id"])
    name = str(artifact.get("name", artifact_id))
    location = f"artifact:{artifact_id}:{name}"
    if artifact.get("expired"):
        return 0, 1, []
    size = int(artifact.get("size_in_bytes", 0))
    if size > MAX_DOWNLOAD_BYTES:
        return 0, 0, [{"location": location, "line": 0, "kind": f"artifact exceeds {MAX_DOWNLOAD_BYTES} bytes and was not scanned"}]
    try:
        data = download_github_archive(repository, token, f"/actions/artifacts/{artifact_id}/zip")
    except (ValueError, urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        return 0, 0, [{"location": location, "line": 0, "kind": safe_http_error_kind("artifact audit error", error)}]
    if data is None:
        return 0, 1, []
    return 1, 0, scan_zip_bytes(data, location)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Public release historical Actions audit",
        "",
        f"**Outcome:** `{report['outcome']}`",
        "",
        f"- Workflow runs enumerated: {report['workflow_runs_enumerated']}",
        f"- Retained run log archives scanned: {report['run_logs_scanned']}",
        f"- Run logs unavailable/expired: {report['run_logs_unavailable']}",
        f"- Artifacts enumerated: {report['artifacts_enumerated']}",
        f"- Retained artifacts scanned: {report['artifacts_scanned']}",
        f"- Expired artifacts skipped: {report['artifacts_expired']}",
        "",
    ]
    if report["findings"]:
        lines.extend(["## Blocking findings", ""])
        for finding in report["findings"]:
            line = f":{finding['line']}" if finding.get("line") else ""
            lines.append(f"- `{finding['location']}{line}` — {finding['kind']}")
    else:
        lines.append("No retained Actions log or artifact credential findings were detected.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(
    args: argparse.Namespace,
    *,
    runs: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    logs_scanned: int,
    logs_unavailable: int,
    artifacts_scanned: int,
    artifacts_expired: int,
    findings: list[dict[str, str | int]],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "outcome": "BLOCKED" if findings else "PASS",
        "workflow_runs_enumerated": len(runs),
        "run_logs_scanned": logs_scanned,
        "run_logs_unavailable": logs_unavailable,
        "artifacts_enumerated": len(artifacts),
        "artifacts_scanned": artifacts_scanned,
        "artifacts_expired": artifacts_expired,
        "findings": findings,
    }
    args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(args.markdown_output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-output", type=Path, default=Path("public-actions-audit.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("public-actions-audit.md"))
    args = parser.parse_args()

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repository or not token:
        print("GITHUB_REPOSITORY and GITHUB_TOKEN are required", file=sys.stderr)
        return 2

    findings: list[dict[str, str | int]] = []
    runs: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    # Enumerate both collections before the high-volume archive downloads. This
    # ensures a later transient throttle cannot prevent the audit from knowing
    # the complete retained run/artifact inventory.
    try:
        runs = paged_items(repository, token, "/actions/runs", "workflow_runs")
    except (ValueError, urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        findings.append({
            "location": "actions:runs",
            "line": 0,
            "kind": safe_http_error_kind("workflow-run enumeration error", error),
        })
    try:
        artifacts = paged_items(repository, token, "/actions/artifacts", "artifacts")
    except (ValueError, urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as error:
        findings.append({
            "location": "actions:artifacts",
            "line": 0,
            "kind": safe_http_error_kind("artifact enumeration error", error),
        })

    logs_scanned = 0
    logs_unavailable = 0
    if runs:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(scan_run_log, repository, token, int(run["id"])) for run in runs]
            for future in as_completed(futures):
                scanned, unavailable, run_findings = future.result()
                logs_scanned += scanned
                logs_unavailable += unavailable
                findings.extend(run_findings)

    artifacts_scanned = 0
    artifacts_expired = 0
    if artifacts:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(scan_artifact, repository, token, artifact) for artifact in artifacts]
            for future in as_completed(futures):
                scanned, expired, artifact_findings = future.result()
                artifacts_scanned += scanned
                artifacts_expired += expired
                findings.extend(artifact_findings)

    report = write_report(
        args,
        runs=runs,
        artifacts=artifacts,
        logs_scanned=logs_scanned,
        logs_unavailable=logs_unavailable,
        artifacts_scanned=artifacts_scanned,
        artifacts_expired=artifacts_expired,
        findings=findings,
    )

    print(
        f"Historical Actions audit {report['outcome']}: "
        f"{len(runs)} runs, {logs_scanned} retained log archives, {artifacts_scanned} retained artifacts scanned."
    )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
