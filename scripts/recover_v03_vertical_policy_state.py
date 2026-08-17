#!/usr/bin/env python3
"""Classify or recover the protected v0.3 Vertical policy materialization.

A fresh trusted-control run must distinguish the only two release-safe states:

* bootstrap-only: the existing materializer may perform the one policy write;
* exact previously materialized bundle: adopt the durable commit with zero write,
  reconstruct preliminary authority evidence, then let the independent post-write
  verifier close the live ref/protection proof.

Every other protected-state shape fails closed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

from materialize_v03_vertical_policy_state import (
    BOOTSTRAP_PATH,
    EVIDENCE_PATH,
    POLICY_EPOCH,
    _bootstrap_quiescence_proof,
    _git,
    _load_writer_fence_proof,
    _read_json_at,
    _required_env,
    _sha,
)
from operator_effect_rollout import LINEAGE_WRITER_CAPABILITY
from operator_protected_policy_materializer import (
    DEFAULT_STATE_REF,
    POLICY_NAMESPACE,
    REQUIRED_POLICY_PATHS,
    TRACKING_REF,
)
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import digest_json, normalize_repository
from operator_store_protection import require_protected
from operator_vertical import VERTICAL_PROFILE

RECEIPT_PATH = f"{POLICY_NAMESPACE}/bundle-receipt.json"
WRITER_FENCE_PATH = f"{POLICY_NAMESPACE}/writer-fence-receipt.json"


class VerticalPolicyRecoveryError(RuntimeError):
    pass


def _remote_ref_sha(state_ref: str) -> str:
    line = _git("ls-remote", "--refs", "origin", state_ref)
    if not line:
        raise VerticalPolicyRecoveryError("protected Operator state ref does not exist")
    sha, separator, ref = line.partition("\t")
    if separator != "\t" or ref != state_ref:
        raise VerticalPolicyRecoveryError("unexpected protected Operator state-ref response")
    return _sha(sha, "protected Operator state-ref")


def _refresh_exact_tracking_ref(state_ref: str, expected_sha: str) -> None:
    before = _remote_ref_sha(state_ref)
    if before != expected_sha:
        raise VerticalPolicyRecoveryError("protected state ref changed before recovery fetch")
    _git("fetch", "--no-tags", "origin", f"+{state_ref}:{TRACKING_REF}")
    local = _sha(_git("rev-parse", "--verify", TRACKING_REF), "protected tracking ref")
    after = _remote_ref_sha(state_ref)
    if local != expected_sha or after != expected_sha:
        raise VerticalPolicyRecoveryError("protected state ref changed during recovery fetch")


def _namespace_paths(ref: str) -> frozenset[str]:
    raw = _git("ls-tree", "-r", "--name-only", ref, "--", POLICY_NAMESPACE)
    return frozenset(path for path in raw.splitlines() if path)


def _existing_materialization_commit(snapshot_sha: str) -> tuple[str, str]:
    commits: set[str] = set()
    for path in sorted(REQUIRED_POLICY_PATHS):
        commit = _git("log", "-1", "--format=%H", snapshot_sha, "--", path)
        if not commit:
            raise VerticalPolicyRecoveryError(
                f"protected policy path lacks durable materialization history: {path}"
            )
        commits.add(_sha(commit, f"materialization history for {path}"))
    if len(commits) != 1:
        raise VerticalPolicyRecoveryError(
            "protected policy paths do not share one exact materialization commit"
        )
    materialization_sha = next(iter(commits))
    parent_row = _git("rev-list", "--parents", "-n", "1", materialization_sha).split()
    if len(parent_row) != 2 or parent_row[0] != materialization_sha:
        raise VerticalPolicyRecoveryError(
            "protected policy materialization is not one linear commit"
        )
    parent_sha = _sha(parent_row[1], "pre-materialization parent")
    changed = frozenset(
        path
        for path in _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", materialization_sha
        ).splitlines()
        if path
    )
    if changed != REQUIRED_POLICY_PATHS:
        raise VerticalPolicyRecoveryError(
            "protected policy materialization commit changed paths outside the exact bundle"
        )
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", materialization_sha, snapshot_sha],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode != 0:
        raise VerticalPolicyRecoveryError(
            "protected policy materialization is not an ancestor of the live state ref"
        )
    return materialization_sha, parent_sha


def _protection_dict(receipt) -> dict[str, Any]:
    return {
        "verifier_identity": receipt.verifier_identity,
        "verified_at": receipt.verified_at,
        "policy_digest": receipt.policy_digest,
    }


def _adopt_existing_materialization(
    *,
    repository: str,
    installation_sha: str,
    state_ref: str,
    snapshot_sha: str,
    writer_surface_proof: dict,
    protection_receipt,
    loader_cls,
) -> dict[str, Any]:
    if _namespace_paths(TRACKING_REF) != REQUIRED_POLICY_PATHS:
        raise VerticalPolicyRecoveryError(
            "existing protected policy namespace is not the exact reviewed bundle"
        )
    materialization_sha, parent_sha = _existing_materialization_commit(snapshot_sha)
    bootstrap_proof = _bootstrap_quiescence_proof(parent_sha)
    quiescence_proof = {
        **bootstrap_proof,
        "installation_commit_sha": installation_sha,
        "writer_surface_proof_digest": writer_surface_proof["proof_digest"],
    }

    receipt = _read_json_at(materialization_sha, RECEIPT_PATH)
    if _sha(receipt.get("installation_commit_sha", ""), "receipt installation") != installation_sha:
        raise VerticalPolicyRecoveryError(
            "existing policy bundle is not bound to this trusted-main installation"
        )
    fence = _read_json_at(materialization_sha, WRITER_FENCE_PATH)
    if fence.get("quiescence_proof") != quiescence_proof:
        raise VerticalPolicyRecoveryError(
            "existing writer-fence quiescence proof does not match trusted bootstrap/writer audit"
        )

    def installation_verifier(requested_repo: str, sha: str) -> bool:
        return (
            normalize_repository(requested_repo) == repository
            and sha == installation_sha
            and _sha(_git("rev-parse", "HEAD"), "checkout") == installation_sha
        )

    def materialization_verifier(
        requested_repo: str, requested_ref: str, sha: str
    ) -> bool:
        return (
            normalize_repository(requested_repo) == repository
            and requested_ref == state_ref
            and sha == materialization_sha
            and subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, TRACKING_REF],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )

    authority = loader_cls(
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
        installation_commit_verifier=installation_verifier,
        materialization_commit_verifier=materialization_verifier,
    ).load()

    rollout = authority.rollout_verifier.verify(
        repository=repository,
        state_ref=state_ref,
        operation_profile=VERTICAL_PROFILE,
    )
    resolution = authority.resolution_policy_verifier.verify_current()
    authority.decision_policy_verifier._load_base()
    if (
        not rollout.effect_lineage_required
        or rollout.writer_capability != LINEAGE_WRITER_CAPABILITY
        or resolution.policy_epoch != POLICY_EPOCH
    ):
        raise VerticalPolicyRecoveryError(
            "recovered protected Vertical policy authority is incomplete"
        )

    artifacts = []
    for path in sorted(REQUIRED_POLICY_PATHS):
        document = _read_json_at(materialization_sha, path)
        if not document:
            raise VerticalPolicyRecoveryError(
                f"exact materialized policy document is missing: {path}"
            )
        artifacts.append({"path": path, "digest": digest_json(document)})
    materializer_bundle_digest = digest_json(
        {row["path"]: row["digest"] for row in artifacts}
    )

    return {
        "schema_version": "ai-sdlc.vertical-policy-materialization-evidence/v1",
        "repository": repository,
        "state_ref": state_ref,
        "installation_commit_sha": installation_sha,
        "materialization_commit_sha": materialization_sha,
        "receipt_ref": authority.receipt_ref,
        "receipt_digest": authority.receipt_digest,
        "policy_bundle_digest": authority.bundle_digest,
        "materializer_bundle_digest": materializer_bundle_digest,
        "protection": _protection_dict(protection_receipt),
        "artifacts": artifacts,
        "effect_lineage_required": rollout.effect_lineage_required,
        "writer_capability": rollout.writer_capability,
        "writer_fence_receipt_digest": rollout.writer_fence_receipt_digest,
        "writer_fence_quiescence_proof": quiescence_proof,
        "effect_resolution_policy_epoch": resolution.policy_epoch,
        "decision_authority": "base-policy-only-no-decision-types",
        "materialization_recovery": {
            "mode": "adopted-existing",
            "recovered_state_ref_sha": snapshot_sha,
            "zero_second_push": True,
        },
    }


def classify_or_recover(
    *,
    repository: str,
    installation_sha: str,
    state_ref: str,
    writer_surface_proof: dict,
    protection_receipt,
    loader_cls,
) -> dict[str, Any]:
    snapshot_sha = _remote_ref_sha(state_ref)
    _refresh_exact_tracking_ref(state_ref, snapshot_sha)
    namespace = _namespace_paths(TRACKING_REF)
    if not namespace:
        _bootstrap_quiescence_proof(snapshot_sha)
        return {
            "policy_action": "materialize",
            "state_ref_sha": snapshot_sha,
            "evidence": None,
        }
    if namespace != REQUIRED_POLICY_PATHS:
        raise VerticalPolicyRecoveryError(
            "protected state contains partial, foreign, or drifted Vertical policy paths"
        )
    evidence = _adopt_existing_materialization(
        repository=repository,
        installation_sha=installation_sha,
        state_ref=state_ref,
        snapshot_sha=snapshot_sha,
        writer_surface_proof=writer_surface_proof,
        protection_receipt=protection_receipt,
        loader_cls=loader_cls,
    )
    return {
        "policy_action": "adopt",
        "state_ref_sha": snapshot_sha,
        "evidence": evidence,
    }


def _emit_action(action: str) -> None:
    output = str(os.environ.get("GITHUB_OUTPUT") or "").strip()
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        handle.write(f"policy_action={action}\n")


def main() -> None:
    EVIDENCE_PATH.unlink(missing_ok=True)
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise VerticalPolicyRecoveryError(
            "live policy recovery requires explicit workflow_dispatch"
        )
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise VerticalPolicyRecoveryError(
            "live policy recovery is authorized only from refs/heads/main"
        )

    repository = normalize_repository(_required_env("GITHUB_REPOSITORY"))
    installation_sha = _sha(_required_env("GITHUB_SHA"), "trusted-main installation")
    state_ref = _required_env("STATE_REF")
    if state_ref != DEFAULT_STATE_REF:
        raise VerticalPolicyRecoveryError("unexpected Operator state ref")
    if _sha(_git("rev-parse", "HEAD"), "checkout") != installation_sha:
        raise VerticalPolicyRecoveryError(
            "trusted checkout HEAD does not match workflow installation SHA"
        )

    admin_token = _required_env("AI_SDLC_OPERATOR_ADMIN_TOKEN")
    app_slug = _required_env("AI_SDLC_OPERATOR_APP_SLUG")
    app_id_raw = _required_env("AI_SDLC_OPERATOR_APP_INTEGRATION_ID")
    if not app_id_raw.isdigit() or int(app_id_raw) < 1:
        raise VerticalPolicyRecoveryError(
            "AI_SDLC_OPERATOR_APP_INTEGRATION_ID must be a positive integer"
        )
    try:
        from operator_vertical_policy_state import ProtectedVerticalPolicyBundleLoader
    except ImportError as exc:
        raise VerticalPolicyRecoveryError(
            "reviewed Vertical policy authority (#267) is not present on trusted main"
        ) from exc

    writer_surface_proof = _load_writer_fence_proof(installation_sha)
    verifier = GitHubRepositoryProtectionVerifier(
        token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=int(app_id_raw),
        api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    protection = verifier.verify(repository, state_ref)
    require_protected(protection, repository=repository, state_ref=state_ref)

    result = classify_or_recover(
        repository=repository,
        installation_sha=installation_sha,
        state_ref=state_ref,
        writer_surface_proof=writer_surface_proof,
        protection_receipt=protection,
        loader_cls=ProtectedVerticalPolicyBundleLoader,
    )
    action = result["policy_action"]
    if action == "adopt":
        evidence = result["evidence"]
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    _emit_action(action)
    print(json.dumps({k: v for k, v in result.items() if k != "evidence"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
