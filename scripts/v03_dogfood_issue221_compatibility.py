#!/usr/bin/env python3
"""Reuse immutable #221 evidence only across this reviewed dogfood-only tree delta.

The original artifacts keep their original installation SHA. Every pre-existing
path except the two exact pinned control-workflow blobs must be byte-identical.
Any runtime, policy, dependency, fixture, result-verifier or other existing-file
change invalidates reuse. New policy materialization still binds current main.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess

SOURCE_MAIN = "85b73b76c6fdb96e66a5b70a92b9be30a37e8c68"
SOURCE_FINAL_LEDGER_RUN = 37030167082
SOURCE_CONTROL_BLOBS = {".github/workflows/v03-trusted-control-command.yml":"5e76daa9d342d2a488644f7cbed5eed150a28c3c",".github/workflows/validate-v03-trusted-control-command.yml":"5d0e2e5c19f3dcb252fba724aa577955747232a0"}
DOGFOOD_CONTROL_BLOBS = {".github/workflows/v03-trusted-control-command.yml":"498cf324a12b8257fa7dbb53003f4ff3fa240fc3",".github/workflows/validate-v03-trusted-control-command.yml":"f43fe229679c6fb463b3c3f051c638e4eaee9e31"}
ADDED_PATHS = frozenset([".github/workflows/v03-finalize-real-dogfood-scenario.yml",".github/workflows/v03-real-dogfood-scenario.yml",".github/workflows/validate-v03-dogfood-live-gate.yml","scripts/provision_v03_dogfood_fixture.py","scripts/v03_dogfood_fixture_pool.py","scripts/v03_dogfood_fixture_pr_authority.py","scripts/v03_dogfood_full_composition.py","scripts/v03_dogfood_live_gate.py","scripts/v03_dogfood_openai_host.py","scripts/v03_dogfood_post_run_finalizer.py","scripts/v03_dogfood_production_provenance.py","scripts/v03_dogfood_release_finalizer.py","scripts/v03_dogfood_runtime_driver.py","scripts/v03_dogfood_runtime_preflight.py","scripts/v03_dogfood_scenario_runner.py","scripts/validate_v03_dogfood_fixture_pool.py","scripts/validate_v03_dogfood_live_gate.py","scripts/validate_v03_dogfood_openai_host.py","scripts/validate_v03_dogfood_production_provenance.py","scripts/validate_v03_dogfood_release_finalizer.py","scripts/validate_v03_dogfood_runtime_composition.py","scripts/validate_v03_dogfood_runtime_driver.py","scripts/validate_v03_dogfood_scenario_runner.py","scripts/v03_dogfood_issue221_compatibility.py","scripts/validate_v03_dogfood_issue221_compatibility.py",".github/workflows/v03-dogfood-readiness.yml",".github/workflows/provision-v03-dogfood-fixtures.yml","release/v0.3-dogfood-session-policy.json","scripts/v03_dogfood_session_policy.py","scripts/validate_v03_dogfood_session_policy.py"])
_SHA = re.compile(r"^[0-9a-f]{40}$")


class Issue221CompatibilityError(RuntimeError):
    pass


def validate_delta(rows, *, source_sha, installation_sha, ancestor):
    if source_sha != SOURCE_MAIN or not _SHA.fullmatch(str(installation_sha)):
        raise Issue221CompatibilityError("unapproved #221 evidence generation")
    if ancestor is not True:
        raise Issue221CompatibilityError("#221 evidence is not trusted-main ancestry")
    paths = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "status", "old_sha", "new_sha", "old_mode", "new_mode"}:
            raise Issue221CompatibilityError("malformed tree delta")
        path = row["path"]
        if path in paths:
            raise Issue221CompatibilityError("duplicate delta path")
        paths.add(path)
        if row["status"] == "A" and path in ADDED_PATHS:
            if row["old_sha"] != "0" * 40 or row["old_mode"] != "000000" or row["new_mode"] != "100644" or not _SHA.fullmatch(row["new_sha"]):
                raise Issue221CompatibilityError("invalid added dogfood blob")
        elif row["status"] == "M" and path in SOURCE_CONTROL_BLOBS:
            if (row["old_sha"], row["new_sha"], row["old_mode"], row["new_mode"]) != (
                SOURCE_CONTROL_BLOBS[path], DOGFOOD_CONTROL_BLOBS[path], "100644", "100644"
            ):
                raise Issue221CompatibilityError("control bridge differs from reviewed exact blobs")
        else:
            raise Issue221CompatibilityError("existing tested code changed: " + str(path))
    proof = {
        "source_main_sha": source_sha,
        "installation_commit_sha": installation_sha,
        "source_final_ledger_run": SOURCE_FINAL_LEDGER_RUN,
        "existing_runtime_tree_unchanged": True,
        "tree_delta": sorted(rows, key=lambda row: row["path"]),
    }
    proof["compatibility_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return proof


def _git(*args):
    result = subprocess.run(["git", *args], cwd=Path(__file__).resolve().parents[1],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise Issue221CompatibilityError("cannot establish exact #221 tree compatibility")
    return result.stdout


def verify_installation(installation_sha):
    if not _SHA.fullmatch(str(installation_sha)):
        raise Issue221CompatibilityError("invalid installation SHA")
    if _git("rev-parse", "HEAD").decode().strip() != installation_sha:
        raise Issue221CompatibilityError("checkout does not match trusted installation")
    _git("merge-base", "--is-ancestor", SOURCE_MAIN, installation_sha)
    raw = _git("diff", "--raw", "--no-renames", "--no-abbrev", "-z", SOURCE_MAIN, installation_sha)
    fields = raw.decode("utf-8").split("\0")
    if fields[-1] != "" or (len(fields) - 1) % 2:
        raise Issue221CompatibilityError("malformed raw git delta")
    rows = []
    for index in range(0, len(fields) - 1, 2):
        metadata, path = fields[index:index+2]
        parts = metadata.lstrip(":").split()
        if len(parts) != 5:
            raise Issue221CompatibilityError("malformed raw git metadata")
        old_mode, new_mode, old_sha, new_sha, status = parts
        rows.append(dict(path=path, status=status, old_sha=old_sha, new_sha=new_sha,
                         old_mode=old_mode, new_mode=new_mode))
    return validate_delta(rows, source_sha=SOURCE_MAIN, installation_sha=installation_sha, ancestor=True)
