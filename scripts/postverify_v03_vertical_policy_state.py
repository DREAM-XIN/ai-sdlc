#!/usr/bin/env python3
"""Stable post-write authority verification for live v0.3 Vertical policy state.

The materializer proves the exact write. This verifier independently refreshes the
live protected ref and protection policy after that write, loads the authority via
PR #267's two-anchor loader from one stable remote snapshot, and refuses to retain
materialization evidence if either the ref or protection generation changes during
verification.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from operator_protected_policy_materializer import DEFAULT_STATE_REF, TRACKING_REF
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import normalize_repository
from operator_store_protection import require_protected
from operator_vertical import VERTICAL_PROFILE

EVIDENCE_PATH = Path("evidence/v03-vertical-policy-materialization.json")
POLICY_NAMESPACE = "config/operator/v03-vertical-policy"
RECEIPT_PATH = f"{POLICY_NAMESPACE}/bundle-receipt.json"


class PostWriteVerticalPolicyVerificationError(RuntimeError):
    pass


def _sha(value: str, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise PostWriteVerticalPolicyVerificationError(f"exact {label} SHA is required")
    return text


def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise PostWriteVerticalPolicyVerificationError(f"missing trusted configuration: {name}")
    return value


def _git(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise PostWriteVerticalPolicyVerificationError(
            f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _remote_ref_sha(state_ref: str) -> str:
    result = _git("ls-remote", "--refs", "origin", state_ref)
    if not result:
        raise PostWriteVerticalPolicyVerificationError("protected state ref disappeared")
    sha, separator, ref = result.partition("\t")
    if separator != "\t" or ref != state_ref:
        raise PostWriteVerticalPolicyVerificationError("unexpected protected state-ref response")
    return _sha(sha, "protected state-ref")


def _refresh_stable_tracking_ref(state_ref: str) -> str:
    before = _remote_ref_sha(state_ref)
    _git("fetch", "--no-tags", "origin", f"+{state_ref}:{TRACKING_REF}")
    local = _sha(_git("rev-parse", "--verify", TRACKING_REF), "tracking ref")
    after = _remote_ref_sha(state_ref)
    if before != local or after != local:
        raise PostWriteVerticalPolicyVerificationError(
            "protected state ref changed while refreshing post-write snapshot"
        )
    return local


def _require_remote_snapshot_unchanged(state_ref: str, expected_sha: str) -> None:
    if _remote_ref_sha(state_ref) != expected_sha:
        raise PostWriteVerticalPolicyVerificationError(
            "protected state ref changed during post-write authority verification"
        )


def _read_json_at(ref: str, path: str) -> dict:
    completed = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _verify_protection(
    *, repository: str, state_ref: str, admin_token: str, app_slug: str, app_id: int
):
    verifier = GitHubRepositoryProtectionVerifier(
        token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=app_id,
        api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    receipt = verifier.verify(repository, state_ref)
    require_protected(receipt, repository=repository, state_ref=state_ref)
    return receipt


def _same_protection_generation(before, after) -> bool:
    return (
        before.repository.lower() == after.repository.lower()
        and before.state_ref == after.state_ref
        and before.verifier_identity == after.verifier_identity
        and before.policy_digest == after.policy_digest
    )


def _load_preliminary_evidence() -> dict:
    if not EVIDENCE_PATH.is_file():
        raise PostWriteVerticalPolicyVerificationError(
            "materializer did not produce preliminary evidence"
        )
    try:
        value = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PostWriteVerticalPolicyVerificationError(
            "preliminary materialization evidence is invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise PostWriteVerticalPolicyVerificationError(
            "preliminary materialization evidence is not an object"
        )
    return value


def verify_and_finalize() -> dict:
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise PostWriteVerticalPolicyVerificationError(
            "post-write verification requires explicit workflow_dispatch"
        )
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise PostWriteVerticalPolicyVerificationError(
            "post-write verification is authorized only from refs/heads/main"
        )

    evidence = _load_preliminary_evidence()
    repository = normalize_repository(_required_env("GITHUB_REPOSITORY"))
    installation_sha = _sha(_required_env("GITHUB_SHA"), "trusted-main installation")
    state_ref = _required_env("STATE_REF")
    if state_ref != DEFAULT_STATE_REF:
        raise PostWriteVerticalPolicyVerificationError("unexpected Operator state ref")
    materialization_sha = _sha(
        evidence.get("materialization_commit_sha", ""), "materialization"
    )
    if normalize_repository(str(evidence.get("repository") or "")) != repository:
        raise PostWriteVerticalPolicyVerificationError("preliminary evidence repository mismatch")
    if evidence.get("installation_commit_sha") != installation_sha:
        raise PostWriteVerticalPolicyVerificationError("preliminary evidence installation mismatch")
    if evidence.get("state_ref") != state_ref:
        raise PostWriteVerticalPolicyVerificationError("preliminary evidence state-ref mismatch")
    if _sha(_git("rev-parse", "HEAD"), "checkout") != installation_sha:
        raise PostWriteVerticalPolicyVerificationError("trusted checkout changed before post-write verification")

    admin_token = _required_env("AI_SDLC_OPERATOR_ADMIN_TOKEN")
    app_slug = _required_env("AI_SDLC_OPERATOR_APP_SLUG")
    app_id_raw = _required_env("AI_SDLC_OPERATOR_APP_INTEGRATION_ID")
    if not app_id_raw.isdigit() or int(app_id_raw) < 1:
        raise PostWriteVerticalPolicyVerificationError(
            "AI_SDLC_OPERATOR_APP_INTEGRATION_ID must be a positive integer"
        )
    app_id = int(app_id_raw)

    try:
        from operator_vertical_policy_state import ProtectedVerticalPolicyBundleLoader
    except ImportError as exc:
        raise PostWriteVerticalPolicyVerificationError(
            "reviewed Vertical policy authority (#267) is not present on trusted main"
        ) from exc

    protection_before = _verify_protection(
        repository=repository,
        state_ref=state_ref,
        admin_token=admin_token,
        app_slug=app_slug,
        app_id=app_id,
    )
    snapshot_sha = _refresh_stable_tracking_ref(state_ref)
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", materialization_sha, snapshot_sha],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        raise PostWriteVerticalPolicyVerificationError(
            "materialization commit is not an ancestor of the live protected snapshot"
        )

    authority = ProtectedVerticalPolicyBundleLoader(
        repository=repository,
        installation_commit_sha=installation_sha,
        materialization_commit_sha=materialization_sha,
        state_ref=state_ref,
        operation_profile=VERTICAL_PROFILE,
        receipt_path=RECEIPT_PATH,
        document_loader=lambda sha, path: _read_json_at(sha, path),
        protected_document_loader=lambda repo, ref, path: (
            _read_json_at(TRACKING_REF, path)
            if normalize_repository(repo) == repository and ref == state_ref
            else {}
        ),
        installation_commit_verifier=lambda repo, sha: (
            normalize_repository(repo) == repository
            and sha == installation_sha
            and _git("rev-parse", "HEAD") == installation_sha
        ),
        materialization_commit_verifier=lambda repo, ref, sha: (
            normalize_repository(repo) == repository
            and ref == state_ref
            and sha == materialization_sha
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, snapshot_sha],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        ),
    ).load()

    _require_remote_snapshot_unchanged(state_ref, snapshot_sha)
    protection_after = _verify_protection(
        repository=repository,
        state_ref=state_ref,
        admin_token=admin_token,
        app_slug=app_slug,
        app_id=app_id,
    )
    if not _same_protection_generation(protection_before, protection_after):
        raise PostWriteVerticalPolicyVerificationError(
            "protection generation changed during post-write authority verification"
        )

    expected = {
        "receipt_ref": authority.receipt_ref,
        "receipt_digest": authority.receipt_digest,
        "policy_bundle_digest": authority.bundle_digest,
    }
    for field, value in expected.items():
        if evidence.get(field) != value:
            raise PostWriteVerticalPolicyVerificationError(
                f"preliminary evidence {field} does not match independently loaded authority"
            )

    finalized = dict(evidence)
    finalized["post_write_verified_state_ref_sha"] = snapshot_sha
    finalized["post_write_protection"] = {
        "verifier_identity": protection_after.verifier_identity,
        "verified_at": protection_after.verified_at,
        "policy_digest": protection_after.policy_digest,
    }
    EVIDENCE_PATH.write_text(
        json.dumps(finalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return finalized


def main() -> None:
    try:
        finalized = verify_and_finalize()
    except Exception:
        # Never leave preliminary materialization evidence behind when the live
        # post-write authority snapshot/protection proof did not close.
        EVIDENCE_PATH.unlink(missing_ok=True)
        raise
    print(json.dumps(finalized, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
