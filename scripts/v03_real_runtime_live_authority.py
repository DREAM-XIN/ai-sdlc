#!/usr/bin/env python3
"""Trusted-main live authority bootstrap for v0.3 Issue #221.

Importing this module and validating execution context are side-effect free. The
live protection verifier is constructed only after an explicit trusted-main
workflow_dispatch gate succeeds; that verifier may establish its reviewed causal
ruleset attestation and therefore must never be instantiated by PR CI.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from typing import Any, Callable

from operator_protected_policy_materializer import DEFAULT_STATE_REF, TRACKING_REF
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import normalize_repository
from operator_store_protection import require_protected
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_policy_state import ProtectedVerticalPolicyBundleLoader, TrustedVerticalPolicyAuthority

POLICY_NAMESPACE = "config/operator/v03-vertical-policy"
RECEIPT_PATH = f"{POLICY_NAMESPACE}/bundle-receipt.json"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class V03LiveAuthorityError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrustedMainExecution:
    repository: str
    installation_commit_sha: str
    state_ref: str


@dataclass(frozen=True)
class V03LiveAuthority:
    execution: TrustedMainExecution
    materialization_commit_sha: str
    protected_state_ref_sha: str
    protection_receipt: Any
    policy: TrustedVerticalPolicyAuthority


def _sha(value: object, label: str) -> str:
    text = str(value or "").lower()
    if not _SHA40.fullmatch(text):
        raise V03LiveAuthorityError(f"exact {label} SHA is required")
    return text


def require_trusted_main_execution(
    *,
    event_name: str,
    ref: str,
    repository: str,
    workflow_sha: str,
    checkout_sha: str,
    state_ref: str = DEFAULT_STATE_REF,
) -> TrustedMainExecution:
    """Pure gate: no GitHub API, Git, Store, ruleset or Worker side effects."""
    if event_name != "workflow_dispatch":
        raise V03LiveAuthorityError("live #221 authority requires explicit workflow_dispatch")
    if ref != "refs/heads/main":
        raise V03LiveAuthorityError("live #221 authority is authorized only from refs/heads/main")
    repository = normalize_repository(repository)
    workflow_sha = _sha(workflow_sha, "workflow installation")
    checkout_sha = _sha(checkout_sha, "checkout")
    if workflow_sha != checkout_sha:
        raise V03LiveAuthorityError("trusted checkout does not match workflow installation SHA")
    if state_ref != DEFAULT_STATE_REF:
        raise V03LiveAuthorityError("unexpected Operator state ref")
    return TrustedMainExecution(
        repository=repository,
        installation_commit_sha=workflow_sha,
        state_ref=state_ref,
    )


def _default_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise V03LiveAuthorityError(
            f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _read_json(git: Callable[..., str], ref: str, path: str) -> dict[str, Any]:
    try:
        text = git("show", f"{ref}:{path}")
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _remote_state_sha(git: Callable[..., str], state_ref: str) -> str:
    raw = git("ls-remote", "--refs", "origin", state_ref)
    sha, separator, ref = raw.partition("\t")
    if separator != "\t" or ref != state_ref:
        raise V03LiveAuthorityError("unexpected protected state-ref response")
    return _sha(sha, "protected state-ref")


def _refresh_stable_snapshot(git: Callable[..., str], state_ref: str) -> str:
    before = _remote_state_sha(git, state_ref)
    git("fetch", "--no-tags", "origin", f"+{state_ref}:{TRACKING_REF}")
    local = _sha(git("rev-parse", "--verify", TRACKING_REF), "tracking ref")
    after = _remote_state_sha(git, state_ref)
    if before != local or after != local:
        raise V03LiveAuthorityError("protected state ref changed while refreshing live authority")
    return local


def _same_protection_generation(before: Any, after: Any) -> bool:
    return (
        str(before.repository).lower() == str(after.repository).lower()
        and before.state_ref == after.state_ref
        and before.verifier_identity == after.verifier_identity
        and before.policy_digest == after.policy_digest
    )


def _materialization_sha(git: Callable[..., str]) -> str:
    sha = git("log", "-1", "--format=%H", TRACKING_REF, "--", RECEIPT_PATH)
    return _sha(sha, "policy materialization")


def load_live_authority(
    *,
    execution: TrustedMainExecution,
    admin_token: str,
    operator_app_slug: str,
    operator_app_id: int,
    api_base: str = "https://api.github.com",
    git: Callable[..., str] = _default_git,
) -> V03LiveAuthority:
    """Establish positive live protection + exact current-main policy authority.

    Caller must have passed ``require_trusted_main_execution`` first. This function
    may perform the reviewed causal ruleset attestation through the trusted v0.3
    protection verifier; it remains read-only with respect to Operator semantic
    Store state and never dispatches a Worker or writes a Feature Event.
    """
    if not isinstance(execution, TrustedMainExecution):
        raise ValueError("trusted-main execution gate is required")
    if not admin_token or not operator_app_slug or not isinstance(operator_app_id, int) or operator_app_id < 1:
        raise V03LiveAuthorityError("trusted Operator protection credentials are incomplete")
    if not api_base.startswith("https://"):
        raise V03LiveAuthorityError("GitHub API URL must use HTTPS")

    verifier = GitHubRepositoryProtectionVerifier(
        token=admin_token,
        operator_app_slug=operator_app_slug,
        operator_app_id=operator_app_id,
        api_base=api_base,
    )
    protection_before = verifier.verify(execution.repository, execution.state_ref)
    require_protected(
        protection_before,
        repository=execution.repository,
        state_ref=execution.state_ref,
    )

    snapshot_sha = _refresh_stable_snapshot(git, execution.state_ref)
    materialization_sha = _materialization_sha(git)
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", materialization_sha, snapshot_sha],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        raise V03LiveAuthorityError("policy materialization is not an ancestor of protected state")

    receipt = _read_json(git, materialization_sha, RECEIPT_PATH)
    if receipt.get("installation_commit_sha") != execution.installation_commit_sha:
        raise V03LiveAuthorityError(
            "live policy was not materialized from this exact trusted-main installation"
        )

    authority = ProtectedVerticalPolicyBundleLoader(
        repository=execution.repository,
        installation_commit_sha=execution.installation_commit_sha,
        materialization_commit_sha=materialization_sha,
        state_ref=execution.state_ref,
        operation_profile=VERTICAL_PROFILE,
        receipt_path=RECEIPT_PATH,
        document_loader=lambda sha, path: _read_json(git, sha, path),
        protected_document_loader=lambda repo, ref, path: (
            _read_json(git, TRACKING_REF, path)
            if normalize_repository(repo) == execution.repository and ref == execution.state_ref
            else {}
        ),
        installation_commit_verifier=lambda repo, sha: (
            normalize_repository(repo) == execution.repository
            and sha == execution.installation_commit_sha
            and _sha(git("rev-parse", "HEAD"), "checkout") == execution.installation_commit_sha
        ),
        materialization_commit_verifier=lambda repo, ref, sha: (
            normalize_repository(repo) == execution.repository
            and ref == execution.state_ref
            and sha == materialization_sha
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, snapshot_sha],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
        ),
    ).load()

    if _remote_state_sha(git, execution.state_ref) != snapshot_sha:
        raise V03LiveAuthorityError("protected state ref changed during live authority loading")
    protection_after = verifier.verify(execution.repository, execution.state_ref)
    require_protected(
        protection_after,
        repository=execution.repository,
        state_ref=execution.state_ref,
    )
    if not _same_protection_generation(protection_before, protection_after):
        raise V03LiveAuthorityError("protection generation changed during live authority loading")

    return V03LiveAuthority(
        execution=execution,
        materialization_commit_sha=materialization_sha,
        protected_state_ref_sha=snapshot_sha,
        protection_receipt=protection_after,
        policy=authority,
    )
