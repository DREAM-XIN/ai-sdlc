#!/usr/bin/env python3
"""Read-only runner for v0.3 full real-runtime release preflight.

Exit 0 means the explicitly configured existing Feature/PR fixture is READY for a
later trusted full-runtime runner. Exit 2 means BLOCKED. Either way this runner
performs GitHub GETs only and writes only a local CI artifact; it never mutates
Store state, dispatches a Worker, or persists a Feature Event.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib import error, request
from urllib.parse import quote

import yaml

from v03_real_runtime_full_preflight import (
    FullRuntimePreflightError,
    build_full_runtime_preflight,
    evaluate_release_fixture,
)
from v03_real_runtime_prerequisites import collect_trusted_main_prerequisites

EVIDENCE_PATH = Path("evidence/v03-real-runtime-full-preflight.json")


def _get_json(url: str, token: str) -> object:
    req = request.Request(url, method="GET")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def _get_json_optional(url: str, token: str) -> object | None:
    try:
        return _get_json(url, token)
    except error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _write(record: dict) -> None:
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    token = os.environ["GITHUB_TOKEN"]
    feature_id = os.environ.get("FI_FULL_FEATURE_ID", "").strip()
    target_ref = os.environ.get("FI_FULL_TARGET_REF", "").strip()
    pr_text = os.environ.get("FI_FULL_PR_NUMBER", "").strip()

    prerequisites = collect_trusted_main_prerequisites(
        repository=repository,
        api_base=api,
        get_json_optional=lambda url: _get_json_optional(url, token),
    )

    fixture = None
    fixture_error = None
    missing_fixture_inputs = [
        name
        for name, value in (
            ("FI_FULL_FEATURE_ID", feature_id),
            ("FI_FULL_TARGET_REF", target_ref),
            ("FI_FULL_PR_NUMBER", pr_text),
        )
        if not value
    ]
    if missing_fixture_inputs:
        fixture_error = "missing explicit fixture inputs: " + ", ".join(missing_fixture_inputs)
    else:
        try:
            pr_number = int(pr_text)
            pull_request = _get_json(f"{api}/repos/{repository}/pulls/{pr_number}", token)
            manifest_path = f"state/features/{feature_id}.yaml"
            content = _get_json(
                f"{api}/repos/{repository}/contents/{manifest_path}?ref={quote(target_ref, safe='')}",
                token,
            )
            if not isinstance(content, dict) or not isinstance(content.get("content"), str):
                raise FullRuntimePreflightError("fixture Feature Manifest response is invalid")
            manifest = yaml.safe_load(base64.b64decode(content["content"]).decode())
            fixture = evaluate_release_fixture(
                feature_id=feature_id,
                target_ref=target_ref,
                candidate_pr_number=pr_number,
                pull_request=pull_request,
                manifest=manifest,
            )
        except (ValueError, FullRuntimePreflightError, KeyError, TypeError) as exc:
            fixture_error = str(exc)

    record = build_full_runtime_preflight(
        prerequisites=prerequisites,
        fixture=fixture,
        fixture_error=fixture_error,
    )
    record["repository"] = repository
    record["control_ref"] = "main"
    _write(record)
    if record["status"] != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
