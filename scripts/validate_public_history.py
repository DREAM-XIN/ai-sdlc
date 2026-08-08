#!/usr/bin/env python3
"""Scan every reachable Git blob for obvious credential material.

This complements validate_public_readiness.py, which intentionally scans only the
current tracked tree. Run this from a complete clone after fetching branches,
tags, and GitHub pull-request refs.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 8 * 1024 * 1024
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GitHub classic token", re.compile(r"gh" + r"p_[A-Za-z0-9]{30,}")),
    ("GitHub fine-grained token", re.compile(r"github" + r"_pat_[A-Za-z0-9_]{40,}")),
    ("OpenAI project API key", re.compile(r"sk" + r"-proj-[A-Za-z0-9_-]{20,}")),
    ("Anthropic API key", re.compile(r"sk" + r"-ant-[A-Za-z0-9_-]{20,}")),
    ("Google API key", re.compile(r"AI" + r"za[0-9A-Za-z_-]{30,}")),
    ("AWS access key", re.compile(r"AK" + r"IA[0-9A-Z]{16}")),
    ("private key block", re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)


def git(*args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return result.stdout


def reachable_blobs() -> dict[str, set[str]]:
    """Return reachable blob SHA -> known historical paths."""
    output = git("rev-list", "--objects", "--all").decode("utf-8", errors="surrogateescape")
    candidates: dict[str, set[str]] = defaultdict(set)
    for line in output.splitlines():
        sha, sep, path = line.partition(" ")
        if sep and path:
            candidates[sha].add(path)
        else:
            candidates.setdefault(sha, set())

    shas = list(candidates)
    if not shas:
        return {}

    batch = ("\n".join(shas) + "\n").encode()
    metadata = git("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)", input_bytes=batch)
    blobs: dict[str, set[str]] = {}
    for line in metadata.decode().splitlines():
        sha, object_type, size_text = line.split(" ", 2)
        if object_type != "blob":
            continue
        size = int(size_text)
        paths = candidates.get(sha, set())
        if size > MAX_BLOB_BYTES and not any(Path(p).suffix.lower() in SENSITIVE_SUFFIXES for p in paths):
            continue
        blobs[sha] = paths
    return blobs


def scan_blob(sha: str, paths: set[str]) -> list[dict[str, str | int]]:
    findings: list[dict[str, str | int]] = []
    for path in sorted(paths):
        if Path(path).suffix.lower() in SENSITIVE_SUFFIXES:
            findings.append({"blob": sha, "path": path, "line": 0, "kind": "historical sensitive key/certificate file"})

    size = int(git("cat-file", "-s", sha).decode().strip())
    if size > MAX_BLOB_BYTES:
        return findings

    raw = git("cat-file", "blob", sha)
    if b"\0" in raw[:8192]:
        return findings
    text = raw.decode("utf-8", errors="ignore")
    display_path = sorted(paths)[0] if paths else "<unknown>"
    for label, pattern in PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(
                {
                    "blob": sha,
                    "path": display_path,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "kind": label,
                }
            )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()

    findings: list[dict[str, str | int]] = []
    blobs = reachable_blobs()
    for sha, paths in blobs.items():
        findings.extend(scan_blob(sha, paths))

    report = {
        "outcome": "BLOCKED" if findings else "PASS",
        "reachable_blob_count": len(blobs),
        "findings": findings,
    }
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown_output:
        lines = [
            "# Public release Git history audit",
            "",
            f"**Outcome:** `{report['outcome']}`",
            "",
            f"- Reachable blobs scanned: {report['reachable_blob_count']}",
            "",
        ]
        if findings:
            lines.extend(["## Blocking findings", ""])
            for finding in findings:
                line = f":{finding['line']}" if finding["line"] else ""
                lines.append(f"- `{finding['path']}{line}` — {finding['kind']} (blob `{str(finding['blob'])[:12]}`)")
        else:
            lines.append("No obvious credential material was detected in reachable Git history.")
        args.markdown_output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if findings:
        print("Full-history public-release scan failed:", file=sys.stderr)
        for finding in findings:
            location = f"{finding['path']}:{finding['line']}" if finding["line"] else str(finding["path"])
            print(f"- {location}: {finding['kind']} (blob {str(finding['blob'])[:12]})", file=sys.stderr)
        return 1

    print(f"Full-history public-release scan passed across {len(blobs)} reachable blobs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
