#!/usr/bin/env python3
"""Trusted-main materialization of the reviewed v0.3 Vertical policy bundle."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from operator_decision_policy import DECISION_POLICY_SCHEMA
from operator_effect_resolution import ALLOWED_RESOLUTION_CHOICES, EFFECT_RESOLUTION_POLICY_SCHEMA
from operator_effect_rollout import (
    LINEAGE_WRITER_CAPABILITY,
    REQUIRED_FENCED_CAPABILITIES,
    ROLLOUT_SCHEMA,
    WRITER_FENCE_SCHEMA,
)
from operator_protected_policy_materializer import (
    DEFAULT_STATE_REF,
    POLICY_NAMESPACE,
    TRACKING_REF,
    ProtectedPolicyBundleMaterializer,
    ProtectedPolicyDocument,
    ProtectedPolicyMaterializerConfig,
)
from operator_store_github_protection_v03_trusted import GitHubRepositoryProtectionVerifier
from operator_store_model import canonical_json, digest_json, normalize_repository
from operator_vertical import VERTICAL_PROFILE

EVIDENCE_PATH = Path("evidence/v03-vertical-policy-materialization.json")
WRITER_FENCE_PROOF_PATH = Path("evidence/v03-vertical-writer-fence-proof.json")
BOOTSTRAP_PATH = "state/operator/v1/.bootstrap"
BOOTSTRAP_MARKER = "ai-sdlc-operator-store-bootstrap-v1"
POLICY_EPOCH = "v0.3-release-1"
AUTHORITY_ID = "trusted-release-controller"
EVIDENCE_SOURCE_ID = "protected-operator-state-v03"
ISSUER = "trusted-release-controller"


class TrustedVerticalPolicyMaterializationError(RuntimeError):
    pass


def _sha(value: str, label: str) -> str:
    text = str(value or "").lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise TrustedVerticalPolicyMaterializationError(f"exact {label} SHA is required")
    return text


def _required_env(name: str) -> str:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        raise TrustedVerticalPolicyMaterializationError(f"missing trusted configuration: {name}")
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
        raise TrustedVerticalPolicyMaterializationError(
            f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


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


def _policy(value: dict) -> dict:
    row = dict(value)
    row["policy_digest"] = digest_json(row)
    return row


def _policy_documents(
    *,
    repository: str,
    installation_commit_sha: str,
    state_ref: str,
    issued_at: str,
    writer_fence_proof: dict,
    protected_ref_fn,
    seal_receipt_fn,
) -> tuple[ProtectedPolicyDocument, ...]:
    repository = normalize_repository(repository)
    paths = {
        "rollout": f"{POLICY_NAMESPACE}/effect-lineage-rollout.json",
        "writer_fence": f"{POLICY_NAMESPACE}/writer-fence-receipt.json",
        "resolution": f"{POLICY_NAMESPACE}/effect-resolution-policy.json",
        "resolution_evidence": f"{POLICY_NAMESPACE}/effect-resolution-evidence.json",
        "decision": f"{POLICY_NAMESPACE}/decision-policy.json",
    }
    receipt_path = f"{POLICY_NAMESPACE}/bundle-receipt.json"

    fence = {
        "schema_version": WRITER_FENCE_SCHEMA,
        "repository": repository,
        "state_ref": state_ref,
        "operation_profile": VERTICAL_PROFILE,
        "state": "QUIESCED",
        "fenced_capabilities": sorted(REQUIRED_FENCED_CAPABILITIES),
        "receipt_id": "writer-fence-v03-release-1",
        "issued_at": issued_at,
        "issuer": ISSUER,
        "quiescence_proof": dict(writer_fence_proof),
    }
    rollout = _policy(
        {
            "schema_version": ROLLOUT_SCHEMA,
            "repository": repository,
            "state_ref": state_ref,
            "operation_profile": VERTICAL_PROFILE,
            "policy_ref": protected_ref_fn(repository, state_ref, paths["rollout"]),
            "effect_lineage_required": True,
            "writer_capability": LINEAGE_WRITER_CAPABILITY,
            "writer_fence_receipt_ref": protected_ref_fn(
                repository, state_ref, paths["writer_fence"]
            ),
        }
    )

    facts: dict[str, dict] = {}
    source_digest = digest_json(facts)
    evidence = {
        "source_id": EVIDENCE_SOURCE_ID,
        "source_digest": source_digest,
        "facts": facts,
    }
    trusted_profile_digest = digest_json(
        {
            "installation_commit_sha": installation_commit_sha,
            "operation_profile": VERTICAL_PROFILE,
        }
    )
    resolution = _policy(
        {
            "schema_version": EFFECT_RESOLUTION_POLICY_SCHEMA,
            "repository": repository,
            "state_ref": state_ref,
            "operation_profile": VERTICAL_PROFILE,
            "policy_ref": protected_ref_fn(repository, state_ref, paths["resolution"]),
            "policy_epoch": POLICY_EPOCH,
            "authority_id": AUTHORITY_ID,
            "allowed_choices": sorted(ALLOWED_RESOLUTION_CHOICES),
            "allowed_resolvers": [AUTHORITY_ID],
            "trusted_profile_digest": trusted_profile_digest,
            "strong_evidence_types": [],
            "evidence_source_id": EVIDENCE_SOURCE_ID,
            "evidence_source_digest": source_digest,
        }
    )

    # The full production bundle requires a real protected Decision verifier, but
    # #221 does not require a new Decision authority. Keep the live base policy
    # deliberately non-authorizing until a separately reviewed need exists.
    decision = _policy(
        {
            "schema_version": DECISION_POLICY_SCHEMA,
            "repository": repository,
            "state_ref": state_ref,
            "operation_profile": VERTICAL_PROFILE,
            "policy_ref": protected_ref_fn(repository, state_ref, paths["decision"]),
            "policy_epoch": POLICY_EPOCH,
            "allowed_target_repositories": [repository],
            "decision_types": {},
        }
    )

    artifacts = {
        "rollout": (paths["rollout"], rollout),
        "writer_fence": (paths["writer_fence"], fence),
        "resolution": (paths["resolution"], resolution),
        "resolution_evidence": (paths["resolution_evidence"], evidence),
        "decision": (paths["decision"], decision),
    }
    receipt = seal_receipt_fn(
        repository=repository,
        installation_commit_sha=installation_commit_sha,
        state_ref=state_ref,
        operation_profile=VERTICAL_PROFILE,
        artifacts=artifacts,
        issued_at=issued_at,
        issuer=ISSUER,
        receipt_path=receipt_path,
    )
    docs = [
        ProtectedPolicyDocument(path, value)
        for path, value in [*artifacts.values(), (receipt_path, receipt)]
    ]
    return tuple(docs)


def _load_writer_fence_proof(installation_sha: str) -> dict:
    if not WRITER_FENCE_PROOF_PATH.is_file():
        raise TrustedVerticalPolicyMaterializationError(
            "trusted-main writer-surface proof is missing"
        )
    try:
        proof = json.loads(WRITER_FENCE_PROOF_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TrustedVerticalPolicyMaterializationError(
            "trusted-main writer-surface proof is invalid JSON"
        ) from exc
    if not isinstance(proof, dict):
        raise TrustedVerticalPolicyMaterializationError(
            "trusted-main writer-surface proof is not an object"
        )
    raw = dict(proof)
    stored_digest = str(raw.pop("proof_digest", ""))
    if stored_digest != digest_json(raw):
        raise TrustedVerticalPolicyMaterializationError(
            "trusted-main writer-surface proof digest mismatch"
        )
    if proof.get("installation_commit_sha") != installation_sha:
        raise TrustedVerticalPolicyMaterializationError(
            "writer-surface proof is not bound to this trusted-main installation"
        )
    if proof.get("effect_lineage_write_fence_installed") is not True:
        raise TrustedVerticalPolicyMaterializationError(
            "writer-surface proof does not confirm EffectLineageWriteFence"
        )
    if set(proof.get("fenced_capabilities") or ()) != set(REQUIRED_FENCED_CAPABILITIES):
        raise TrustedVerticalPolicyMaterializationError(
            "writer-surface proof capability coverage is incomplete"
        )
    if proof.get("workflow_raw_writer_entrypoints") != []:
        raise TrustedVerticalPolicyMaterializationError(
            "writer-surface proof found workflow raw-writer authority"
        )
    if proof.get("production_legacy_compatibility_entrypoints") != []:
        raise TrustedVerticalPolicyMaterializationError(
            "writer-surface proof found production legacy writer authority"
        )
    return proof


def _bootstrap_quiescence_proof(expected_ref_sha: str) -> dict:
    paths = tuple(
        path for path in _git(
            "ls-tree", "-r", "--name-only", expected_ref_sha
        ).splitlines() if path
    )
    if paths != (BOOTSTRAP_PATH,):
        raise TrustedVerticalPolicyMaterializationError(
            "first Vertical policy materialization requires bootstrap-only protected Store state"
        )
    bootstrap = _git("show", f"{expected_ref_sha}:{BOOTSTRAP_PATH}")
    if bootstrap != BOOTSTRAP_MARKER:
        raise TrustedVerticalPolicyMaterializationError(
            "protected Store bootstrap marker is invalid"
        )
    return {
        "schema_version": "ai-sdlc.vertical-writer-quiescence-proof/v1",
        "pre_materialization_ref_sha": expected_ref_sha,
        "semantic_store_state": "bootstrap-only",
        "bootstrap_path": BOOTSTRAP_PATH,
        "bootstrap_sha256": hashlib.sha256(
            (bootstrap + "\n").encode()
        ).hexdigest(),
    }


def main() -> None:
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise TrustedVerticalPolicyMaterializationError(
            "live policy materialization requires explicit workflow_dispatch"
        )
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise TrustedVerticalPolicyMaterializationError(
            "live policy materialization is authorized only from refs/heads/main"
        )

    repository = normalize_repository(_required_env("GITHUB_REPOSITORY"))
    installation_sha = _sha(_required_env("GITHUB_SHA"), "trusted-main installation")
    state_ref = _required_env("STATE_REF")
    if state_ref != DEFAULT_STATE_REF:
        raise TrustedVerticalPolicyMaterializationError("unexpected Operator state ref")
    admin_token = _required_env("AI_SDLC_OPERATOR_ADMIN_TOKEN")
    app_slug = _required_env("AI_SDLC_OPERATOR_APP_SLUG")
    app_id_raw = _required_env("AI_SDLC_OPERATOR_APP_INTEGRATION_ID")
    if not app_id_raw.isdigit() or int(app_id_raw) < 1:
        raise TrustedVerticalPolicyMaterializationError(
            "AI_SDLC_OPERATOR_APP_INTEGRATION_ID must be a positive integer"
        )

    # #267 is a mandatory trusted-main prerequisite. Delayed import makes this
    # script fail closed before that reviewed authority code is present on main.
    try:
        from operator_vertical_policy_state import (
            ProtectedVerticalPolicyBundleLoader,
            protected_ref,
            seal_receipt,
        )
    except ImportError as exc:
        raise TrustedVerticalPolicyMaterializationError(
            "reviewed Vertical policy authority (#267) is not present on trusted main"
        ) from exc

    head = _sha(_git("rev-parse", "HEAD"), "checkout")
    if head != installation_sha:
        raise TrustedVerticalPolicyMaterializationError(
            "trusted checkout HEAD does not match workflow installation SHA"
        )

    remote_line = _git("ls-remote", "--refs", "origin", state_ref)
    if not remote_line:
        raise TrustedVerticalPolicyMaterializationError(
            "protected Operator state ref does not exist"
        )
    remote_sha, separator, remote_ref = remote_line.partition("\t")
    if separator != "\t" or remote_ref != state_ref:
        raise TrustedVerticalPolicyMaterializationError(
            "unexpected protected Operator state-ref response"
        )
    expected = _sha(remote_sha, "protected Operator state-ref")

    writer_surface_proof = _load_writer_fence_proof(installation_sha)
    bootstrap_proof = _bootstrap_quiescence_proof(expected)
    quiescence_proof = {
        **bootstrap_proof,
        "installation_commit_sha": installation_sha,
        "writer_surface_proof_digest": writer_surface_proof["proof_digest"],
    }
    issued_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    docs = _policy_documents(
        repository=repository,
        installation_commit_sha=installation_sha,
        state_ref=state_ref,
        issued_at=issued_at,
        writer_fence_proof=quiescence_proof,
        protected_ref_fn=protected_ref,
        seal_receipt_fn=seal_receipt,
    )

    verifier = GitHubRepositoryProtectionVerifier(
        token=admin_token,
        operator_app_slug=app_slug,
        operator_app_id=int(app_id_raw),
        api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    materializer = ProtectedPolicyBundleMaterializer(
        ProtectedPolicyMaterializerConfig(
            repository=repository,
            trusted_checkout=Path("."),
            state_ref=state_ref,
            remote_name="origin",
        ),
        protection_verifier=verifier,
    )
    result = materializer.materialize(
        expected_ref_sha=expected,
        documents=docs,
    )

    materialization_sha = result.materialization_commit_sha
    receipt_path = f"{POLICY_NAMESPACE}/bundle-receipt.json"

    def installation_verifier(requested_repo: str, sha: str) -> bool:
        return (
            normalize_repository(requested_repo) == repository
            and sha == installation_sha
            and _git("rev-parse", "HEAD") == installation_sha
        )

    def materialization_verifier(
        requested_repo: str, requested_ref: str, sha: str
    ) -> bool:
        if normalize_repository(requested_repo) != repository or requested_ref != state_ref:
            return False
        if sha != materialization_sha:
            return False
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, TRACKING_REF],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        )

    authority = ProtectedVerticalPolicyBundleLoader(
        repository=repository,
        installation_commit_sha=installation_sha,
        materialization_commit_sha=materialization_sha,
        state_ref=state_ref,
        operation_profile=VERTICAL_PROFILE,
        receipt_path=receipt_path,
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
    authority.resolution_policy_verifier.verify_current()
    authority.decision_policy_verifier._load_base()
    if not rollout.effect_lineage_required or rollout.writer_capability != LINEAGE_WRITER_CAPABILITY:
        raise TrustedVerticalPolicyMaterializationError(
            "post-write production rollout authority is incomplete"
        )

    evidence = {
        "schema_version": "ai-sdlc.vertical-policy-materialization-evidence/v1",
        "repository": repository,
        "state_ref": state_ref,
        "installation_commit_sha": installation_sha,
        "materialization_commit_sha": materialization_sha,
        "receipt_ref": authority.receipt_ref,
        "receipt_digest": authority.receipt_digest,
        "policy_bundle_digest": authority.bundle_digest,
        "materializer_bundle_digest": result.bundle_digest,
        "protection": {
            "verifier_identity": result.protection_verifier_identity,
            "verified_at": result.protection_verified_at,
            "policy_digest": result.protection_policy_digest,
        },
        "artifacts": [
            {"path": row.path, "digest": row.digest} for row in result.artifacts
        ],
        "effect_lineage_required": rollout.effect_lineage_required,
        "writer_capability": rollout.writer_capability,
        "writer_fence_receipt_digest": rollout.writer_fence_receipt_digest,
        "writer_fence_quiescence_proof": quiescence_proof,
        "effect_resolution_policy_epoch": authority.resolution_policy_verifier.verify_current().policy_epoch,
        "decision_authority": "base-policy-only-no-decision-types",
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
