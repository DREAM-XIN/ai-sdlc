#!/usr/bin/env python3
"""Fail when the tracked repository tree contains obvious credential material.

This validator is intentionally conservative and deterministic. It scans tracked
text files in the current checkout for credential formats that should never be
committed. It is not a replacement for a full history scan before changing a
repository from private to public.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub classic token", re.compile(r"gh" + r"p_[A-Za-z0-9]{30,}")),
    ("GitHub fine-grained token", re.compile(r"github" + r"_pat_[A-Za-z0-9_]{40,}")),
    ("Anthropic API key", re.compile(r"sk" + r"-ant-[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"AI" + r"za[0-9A-Za-z_-]{30,}")),
    ("AWS access key", re.compile(r"AK" + r"IA[0-9A-Z]{16}")),
    (
        "private key block",
        re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ),
)

# These file types are commonly binary or generated archives. They are skipped
# rather than decoded heuristically. Secrets must not be stored in them either;
# repository policy should keep such artifacts out of source control.
BINARY_SUFFIXES = {
    ".7z",
    ".class",
    ".dll",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".pyc",
    ".tar",
    ".tgz",
    ".webp",
    ".zip",
}

SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]
    return paths


def scan_file(relative: Path) -> list[str]:
    path = ROOT / relative
    findings: list[str] = []

    if path.resolve() == SELF:
        # The validator contains deliberately split signatures used to detect
        # credentials and therefore does not need to scan itself.
        return findings

    if relative.suffix.lower() in SENSITIVE_SUFFIXES:
        findings.append(f"sensitive key/certificate file is tracked: {relative}")
        return findings

    if relative.suffix.lower() in BINARY_SUFFIXES:
        return findings

    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return findings

    for label, pattern in PATTERNS:
        match = pattern.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{relative}:{line}: detected {label}")

    return findings


def main() -> int:
    findings: list[str] = []
    for relative in tracked_files():
        findings.extend(scan_file(relative))

    if findings:
        print("Public-readiness secret scan failed:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1

    print("Public-readiness secret scan passed for tracked files.")
    print(
        "Note: run a separate full-history and historical Actions-log audit before changing repository visibility."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
