#!/usr/bin/env python3
"""Adversarial + real-Git validation for two-anchor v0.3 Vertical policy authority."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from operator_effect_resolution import ALLOWED_RESOLUTION_CHOICES, EFFECT_RESOLUTION_POLICY_SCHEMA
from operator_effect_rollout import LINEAGE_WRITER_CAPABILITY, REQUIRED_FENCED_CAPABILITIES, ROLLOUT_SCHEMA, WRITER_FENCE_SCHEMA
from operator_store import StoreCommandError
from operator_store_model import digest_json
from operator_vertical import VERTICAL_PROFILE
from operator_vertical_policy_state import (
    ProtectedVerticalPolicyBundleLoader,
    protected_commit_ref,
    protected_ref,
    seal_receipt,
)

REPO = "dream-xin/ai-sdlc"
TARGET = "dream-xin/target"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
INSTALLATION = "a" * 40
MATERIALIZATION = "b" * 40
ROOT = "config/operator/v03-vertical-policy"
RECEIPT_PATH = f"{ROOT}/bundle-receipt.json"


def policy(document):
    row = dict(document)
    row["policy_digest"] = digest_json(row)
    return row


def build_documents(*, installation_commit_sha=INSTALLATION):
    paths = {
        "rollout": f"{ROOT}/effect-lineage-rollout.json",
        "writer_fence": f"{ROOT}/writer-fence-receipt.json",
        "resolution": f"{ROOT}/effect-resolution-policy.json",
        "resolution_evidence": f"{ROOT}/effect-resolution-evidence.json",
        "decision": f"{ROOT}/decision-policy.json",
    }
    fence = {
        "schema_version": WRITER_FENCE_SCHEMA,
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "state": "QUIESCED",
        "fenced_capabilities": sorted(REQUIRED_FENCED_CAPABILITIES),
        "receipt_id": "writer-fence-v03",
        "issued_at": "2026-08-14T00:00:00Z",
        "issuer": "trusted-release-controller",
    }
    rollout = policy({
        "schema_version": ROLLOUT_SCHEMA,
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": protected_ref(REPO, STATE_REF, paths["rollout"]),
        "effect_lineage_required": True,
        "writer_capability": LINEAGE_WRITER_CAPABILITY,
        "writer_fence_receipt_ref": protected_ref(REPO, STATE_REF, paths["writer_fence"]),
    })
    facts = {
        "evidence://launch": {
            "type": "EXTERNAL_LAUNCH_RECEIPT",
            "external_dispatch_key": "dispatch-example",
            "receipt_id": "run-1",
        }
    }
    source_digest = digest_json(facts)
    evidence = {
        "source_id": "protected-operator-state-v03",
        "source_digest": source_digest,
        "facts": facts,
    }
    resolution = policy({
        "schema_version": EFFECT_RESOLUTION_POLICY_SCHEMA,
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": protected_ref(REPO, STATE_REF, paths["resolution"]),
        "policy_epoch": "v0.3-release-1",
        "authority_id": "trusted-release-controller",
        "allowed_choices": sorted(ALLOWED_RESOLUTION_CHOICES),
        "allowed_resolvers": ["trusted-release-controller"],
        "trusted_profile_digest": "reviewed-v03-profile",
        "strong_evidence_types": [],
        "evidence_source_id": evidence["source_id"],
        "evidence_source_digest": source_digest,
    })
    decision = policy({
        "schema_version": "ai-sdlc.decision-policy/v1",
        "repository": REPO,
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "policy_ref": protected_ref(REPO, STATE_REF, paths["decision"]),
        "policy_epoch": "v0.3-release-1",
        "allowed_target_repositories": [TARGET],
        "decision_types": {
            "review-remediation": {
                "choices": {"REMEDIATE": "operation.resume", "CANCEL": "operation.cancel"},
                "allowed_responders": ["human:product-owner"],
                "ttl_seconds": 3600,
                "warning_seconds": 600,
            }
        },
    })
    artifacts = {
        "rollout": (paths["rollout"], rollout),
        "writer_fence": (paths["writer_fence"], fence),
        "resolution": (paths["resolution"], resolution),
        "resolution_evidence": (paths["resolution_evidence"], evidence),
        "decision": (paths["decision"], decision),
    }
    receipt = seal_receipt(
        repository=REPO,
        installation_commit_sha=installation_commit_sha,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        artifacts=artifacts,
        issued_at="2026-08-14T00:00:00Z",
        issuer="trusted-release-controller",
        receipt_path=RECEIPT_PATH,
    )
    docs = {path: deepcopy(value) for _name, (path, value) in artifacts.items()}
    docs[RECEIPT_PATH] = receipt
    return paths, docs


def load(
    docs,
    *,
    protected_docs=None,
    repository=REPO,
    installation_commit_sha=INSTALLATION,
    materialization_commit_sha=MATERIALIZATION,
    state_ref=STATE_REF,
    installation_ok=True,
    materialization_ok=True,
):
    current = docs if protected_docs is None else protected_docs

    def document_loader(requested_sha, path):
        if requested_sha != MATERIALIZATION:
            return {}
        return deepcopy(docs.get(path))

    def protected_document_loader(requested_repo, requested_ref, path):
        if requested_repo != REPO or requested_ref != STATE_REF:
            return {}
        return deepcopy(current.get(path))

    return ProtectedVerticalPolicyBundleLoader(
        repository=repository,
        installation_commit_sha=installation_commit_sha,
        materialization_commit_sha=materialization_commit_sha,
        state_ref=state_ref,
        operation_profile=VERTICAL_PROFILE,
        receipt_path=RECEIPT_PATH,
        document_loader=document_loader,
        protected_document_loader=protected_document_loader,
        installation_commit_verifier=lambda repo, sha: installation_ok and repo == REPO and sha == INSTALLATION,
        materialization_commit_verifier=lambda repo, ref, sha: (
            materialization_ok and repo == REPO and ref == STATE_REF and sha == MATERIALIZATION
        ),
    ).load()


def expect_closed(docs, message, **kwargs):
    try:
        load(docs, **kwargs)
    except (StoreCommandError, ValueError):
        return
    raise AssertionError(message)


def reseal(docs):
    receipt = docs[RECEIPT_PATH]
    for descriptor in receipt["artifacts"].values():
        descriptor["digest"] = digest_json(docs[descriptor["path"]])
    material = {
        "repository": receipt["repository"],
        "installation_commit_sha": receipt["installation_commit_sha"],
        "state_ref": receipt["state_ref"],
        "operation_profile": receipt["operation_profile"],
        "artifacts": receipt["artifacts"],
    }
    receipt["bundle_digest"] = digest_json(material)
    raw = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    receipt["receipt_digest"] = digest_json(raw)


def _git(root: Path, *args: str, check=True) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _write_documents(root: Path, docs: dict[str, dict]):
    for path, value in docs.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def validate_real_git_two_anchor_materialization():
    """Prove actual Git commits can be created without knowing their future SHA."""
    with TemporaryDirectory() as temp:
        root = Path(temp)
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.name", "AI-SDLC test")
        _git(root, "config", "user.email", "ai-sdlc-test@example.invalid")
        (root / "INSTALLATION").write_text("reviewed installation code\n", encoding="utf-8")
        _git(root, "add", "INSTALLATION")
        _git(root, "commit", "-m", "reviewed installation")
        installation_sha = _git(root, "rev-parse", "HEAD")

        _git(root, "checkout", "--orphan", "ai-sdlc-operator-state")
        _git(root, "rm", "-rf", ".")
        bootstrap = root / "state/operator/v1/.bootstrap"
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text("ai-sdlc-operator-store-bootstrap-v1\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "operator store bootstrap")

        _paths, docs = build_documents(installation_commit_sha=installation_sha)
        serialized_precommit = json.dumps(docs, sort_keys=True)
        _write_documents(root, docs)
        _git(root, "add", ".")
        _git(root, "commit", "-m", "materialize reviewed Vertical policies")
        materialization_sha = _git(root, "rev-parse", "HEAD")
        assert materialization_sha not in serialized_precommit
        assert installation_sha in serialized_precommit

        def document_loader(sha, path):
            try:
                return json.loads(_git(root, "show", f"{sha}:{path}"))
            except (AssertionError, json.JSONDecodeError):
                return {}

        def protected_document_loader(repo, ref, path):
            if repo != REPO or ref != STATE_REF:
                return {}
            try:
                return json.loads(_git(root, "show", f"{ref}:{path}"))
            except (AssertionError, json.JSONDecodeError):
                return {}

        def installation_verifier(repo, sha):
            if repo != REPO:
                return False
            return subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, "refs/heads/main"],
                cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0

        def materialization_verifier(repo, ref, sha):
            if repo != REPO or ref != STATE_REF:
                return False
            return subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, ref],
                cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0

        def real_load():
            return ProtectedVerticalPolicyBundleLoader(
                repository=REPO,
                installation_commit_sha=installation_sha,
                materialization_commit_sha=materialization_sha,
                state_ref=STATE_REF,
                operation_profile=VERTICAL_PROFILE,
                receipt_path=RECEIPT_PATH,
                document_loader=document_loader,
                protected_document_loader=protected_document_loader,
                installation_commit_verifier=installation_verifier,
                materialization_commit_verifier=materialization_verifier,
            ).load()

        authority = real_load()
        assert authority.receipt_ref == protected_commit_ref(REPO, materialization_sha, RECEIPT_PATH)
        assert authority.installation_commit_sha == installation_sha
        assert authority.materialization_commit_sha == materialization_sha

        # Later runtime state writes do not invalidate unchanged protected policy paths.
        runtime_fact = root / "state/operator/v1/runtime-proof"
        runtime_fact.write_text("unrelated durable runtime state\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "append unrelated runtime state")
        real_load()

        # But later protected policy-path drift is detected even though the exact
        # materialization commit remains an ancestor of the state ref.
        resolution_path = root / f"{ROOT}/effect-resolution-policy.json"
        drifted = json.loads(resolution_path.read_text(encoding="utf-8"))
        drifted["policy_epoch"] = "unauthorized-drift"
        resolution_path.write_text(json.dumps(drifted, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "simulate protected policy drift")
        try:
            real_load()
        except StoreCommandError:
            pass
        else:
            raise AssertionError("protected policy drift after materialization was accepted")


def main():
    paths, docs = build_documents()
    authority = load(docs)
    assert authority.installation_commit_sha == INSTALLATION
    assert authority.materialization_commit_sha == MATERIALIZATION
    assert authority.receipt_ref == protected_commit_ref(REPO, MATERIALIZATION, RECEIPT_PATH)
    rollout = authority.rollout_verifier.verify(
        repository=REPO, state_ref=STATE_REF, operation_profile=VERTICAL_PROFILE
    )
    assert rollout.effect_lineage_required
    assert rollout.writer_capability == LINEAGE_WRITER_CAPABILITY
    authority.resolution_policy_verifier.verify_current()
    authority.decision_policy_verifier.verify_current(
        target_repository=TARGET, feature_id="F-REAL-RUNTIME-0001",
        target_ref="feature/F-REAL-RUNTIME-0001", decision_type="review-remediation",
    )

    stale = deepcopy(docs)
    stale[paths["resolution"]]["policy_epoch"] = "tampered"
    expect_closed(stale, "stale policy digest accepted")

    missing_fence = deepcopy(docs)
    missing_fence[paths["writer_fence"]]["fenced_capabilities"] = [
        sorted(REQUIRED_FENCED_CAPABILITIES)[0]
    ]
    reseal(missing_fence)
    expect_closed(missing_fence, "incomplete writer fence accepted")

    test_only = deepcopy(docs)
    test_only[paths["rollout"]]["policy_ref"] = "test-only://inert"
    test_only[paths["rollout"]]["policy_digest"] = digest_json(
        {k: v for k, v in test_only[paths["rollout"]].items() if k != "policy_digest"}
    )
    reseal(test_only)
    expect_closed(test_only, "test-only policy promoted")

    evidence_drift = deepcopy(docs)
    evidence_drift[paths["resolution_evidence"]]["facts"]["evidence://launch"]["receipt_id"] = "forged"
    reseal(evidence_drift)
    expect_closed(evidence_drift, "evidence source drift accepted")

    protected_drift = deepcopy(docs)
    protected_drift[paths["decision"]]["policy_epoch"] = "later-unreviewed-policy"
    expect_closed(docs, "current protected policy drift accepted", protected_docs=protected_drift)

    expect_closed(docs, "wrong repository accepted", repository="dream-xin/foreign")
    expect_closed(docs, "wrong state ref accepted", state_ref="refs/heads/foreign-state")
    expect_closed(docs, "wrong installation commit accepted", installation_commit_sha="c" * 40)
    expect_closed(docs, "wrong materialization commit accepted", materialization_commit_sha="d" * 40)
    expect_closed(docs, "unverified installation commit accepted", installation_ok=False)
    expect_closed(docs, "unverified materialization commit accepted", materialization_ok=False)

    validate_real_git_two_anchor_materialization()

    print("trusted v0.3 Vertical policy authority validation passed")
    print("- realizable reviewed-installation + protected-materialization two-anchor model")
    print("- real Git commits prove no commit-SHA self-reference")
    print("- protected current policy paths remain digest-bound across unrelated state writes")
    print("- complete QUIESCED old-writer fence and resolution/decision authority")
    print("- stale/tampered/test-only/untrusted anchors fail closed")


if __name__ == "__main__":
    main()
