#!/usr/bin/env python3
"""Real-Git regression for recoverable v0.3 protected policy materialization."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import postverify_v03_vertical_policy_state as postverify
import recover_v03_vertical_policy_state as recovery
from operator_effect_rollout import LINEAGE_WRITER_CAPABILITY
from operator_protected_policy_materializer import POLICY_NAMESPACE, REQUIRED_POLICY_PATHS
from operator_store_model import digest_json

REPO = "dream-xin/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
BOOTSTRAP = "state/operator/v1/.bootstrap"
BOOTSTRAP_MARKER = "ai-sdlc-operator-store-bootstrap-v1"


def require(value, message):
    if not value:
        raise AssertionError(message)


def git(root: Path, *args: str, ok: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], text=True, capture_output=True
    )
    if ok and result.returncode != 0:
        raise AssertionError(result.stderr or f"git {' '.join(args)} failed")
    if not ok:
        return str(result.returncode)
    return result.stdout.strip()


class Receipt:
    repository = REPO
    state_ref = STATE_REF
    verifier_identity = "github-ruleset:integration:4576406"
    verified_at = "2026-08-15T09:00:00Z"
    policy_digest = "a" * 64


class FakeLoader:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def load(self):
        k = self.kwargs
        require(k["installation_commit_verifier"](REPO, k["installation_commit_sha"]), "installation anchor rejected")
        require(
            k["materialization_commit_verifier"](
                REPO, STATE_REF, k["materialization_commit_sha"]
            ),
            "materialization anchor rejected",
        )
        receipt = k["document_loader"](
            k["materialization_commit_sha"], k["receipt_path"]
        )
        current_receipt = k["protected_document_loader"](
            REPO, STATE_REF, k["receipt_path"]
        )
        require(receipt == current_receipt, "current receipt drift was accepted")
        descriptors = receipt["artifacts"]
        for row in descriptors.values():
            exact = k["document_loader"](
                k["materialization_commit_sha"], row["path"]
            )
            current = k["protected_document_loader"](REPO, STATE_REF, row["path"])
            require(exact == current, f"current policy drift was accepted: {row['path']}")
            require(digest_json(exact) == row["digest"], "artifact digest drift was accepted")

        rollout = SimpleNamespace(
            effect_lineage_required=True,
            writer_capability=LINEAGE_WRITER_CAPABILITY,
            writer_fence_receipt_digest="f" * 64,
        )
        return SimpleNamespace(
            receipt_ref=(
                f"protected-commit://{REPO}@{k['materialization_commit_sha']}/{k['receipt_path']}"
            ),
            receipt_digest=receipt["receipt_digest"],
            bundle_digest=receipt["bundle_digest"],
            rollout_verifier=SimpleNamespace(verify=lambda **_kwargs: rollout),
            resolution_policy_verifier=SimpleNamespace(
                verify_current=lambda: SimpleNamespace(policy_epoch="v0.3-release-1")
            ),
            decision_policy_verifier=SimpleNamespace(_load_base=lambda: {}),
        )


def write_json(root: Path, path: str, value: dict) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def policy_documents(installation: str, bootstrap_sha: str, proof_digest: str) -> dict[str, dict]:
    quiescence = {
        "schema_version": "ai-sdlc.vertical-writer-quiescence-proof/v1",
        "pre_materialization_ref_sha": bootstrap_sha,
        "semantic_store_state": "bootstrap-only",
        "bootstrap_path": BOOTSTRAP,
        "bootstrap_sha256": __import__("hashlib").sha256((BOOTSTRAP_MARKER + "\n").encode()).hexdigest(),
        "installation_commit_sha": installation,
        "writer_surface_proof_digest": proof_digest,
    }
    docs = {
        f"{POLICY_NAMESPACE}/effect-lineage-rollout.json": {
            "kind": "rollout",
            "effect_lineage_required": True,
        },
        f"{POLICY_NAMESPACE}/writer-fence-receipt.json": {
            "kind": "writer-fence",
            "quiescence_proof": quiescence,
        },
        f"{POLICY_NAMESPACE}/effect-resolution-policy.json": {
            "kind": "resolution",
            "policy_epoch": "v0.3-release-1",
        },
        f"{POLICY_NAMESPACE}/effect-resolution-evidence.json": {
            "kind": "resolution-evidence",
            "facts": {},
        },
        f"{POLICY_NAMESPACE}/decision-policy.json": {
            "kind": "decision",
            "decision_types": {},
        },
    }
    descriptors = {}
    name_by_file = {
        "effect-lineage-rollout.json": "rollout",
        "writer-fence-receipt.json": "writer_fence",
        "effect-resolution-policy.json": "resolution",
        "effect-resolution-evidence.json": "resolution_evidence",
        "decision-policy.json": "decision",
    }
    for path, value in docs.items():
        descriptors[name_by_file[path.rsplit("/", 1)[1]]] = {
            "path": path,
            "digest": digest_json(value),
        }
    material = {
        "repository": REPO,
        "installation_commit_sha": installation,
        "state_ref": STATE_REF,
        "operation_profile": "vertical-loop-v1",
        "artifacts": descriptors,
    }
    receipt = {
        "schema_version": "ai-sdlc.vertical-policy-bundle-receipt/v1",
        **material,
        "bundle_digest": digest_json(material),
        "issued_at": "2026-08-15T09:00:00Z",
        "issuer": "trusted-release-controller",
    }
    receipt["receipt_digest"] = digest_json(receipt)
    docs[f"{POLICY_NAMESPACE}/bundle-receipt.json"] = receipt
    require(set(docs) == REQUIRED_POLICY_PATHS, "test bundle path set drifted")
    return docs


def writer_proof(installation: str) -> dict:
    body = {
        "schema_version": "ai-sdlc.vertical-writer-surface-proof/v1",
        "installation_commit_sha": installation,
        "effect_lineage_write_fence_installed": True,
        "fenced_capabilities": ["dispatch.claim", "dispatch.launch"],
        "workflow_raw_writer_entrypoints": [],
        "production_legacy_compatibility_entrypoints": [],
    }
    # Recovery only consumes the proof digest; materialize's stricter loader has
    # already validated the rest of the real proof before the trusted call.
    return {**body, "proof_digest": digest_json(body)}


def set_env(installation: str) -> dict[str, str | None]:
    values = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_SHA": installation,
        "STATE_REF": STATE_REF,
        "AI_SDLC_OPERATOR_ADMIN_TOKEN": "test-admin",
        "AI_SDLC_OPERATOR_APP_SLUG": "runtime-app",
        "AI_SDLC_OPERATOR_APP_INTEGRATION_ID": "4576406",
    }
    old = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    return old


def restore_env(old):
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def validate_workflow_recovery_wiring() -> None:
    raw = Path(".github/workflows/materialize-v03-vertical-policy-state.yml").read_text(encoding="utf-8")
    classify = "PYTHONPATH=scripts python scripts/recover_v03_vertical_policy_state.py"
    materialize = "PYTHONPATH=scripts python scripts/materialize_v03_vertical_policy_state.py"
    postverify_cmd = "PYTHONPATH=scripts python scripts/postverify_v03_vertical_policy_state.py"
    require(classify in raw, "trusted workflow does not classify/recover before write")
    require("id: policy-state" in raw, "policy recovery step lacks bounded workflow output id")
    require(
        "if: steps.policy-state.outputs.policy_action == 'materialize'" in raw,
        "original CAS materializer is not gated to bootstrap-only classification",
    )
    require(
        raw.index(classify) < raw.index(materialize) < raw.index(postverify_cmd),
        "recovery/materialization/postverify workflow order drifted",
    )
    validate_raw = Path(".github/workflows/validate-v03-protected-policy-materializer.yml").read_text(encoding="utf-8")
    require(
        "scripts/validate_v03_vertical_policy_recovery.py" in validate_raw
        and "scripts/recover_v03_vertical_policy_state.py" in validate_raw,
        "fresh-process recovery regression is not wired into exact-head CI",
    )


def main() -> None:
    validate_workflow_recovery_wiring()
    original_cwd = Path.cwd()
    original_verify = postverify._verify_protection
    original_unchanged = postverify._require_remote_snapshot_unchanged
    old_module = sys.modules.get("operator_vertical_policy_state")
    try:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            remote = base / "remote.git"
            work = base / "work"
            git(base, "init", "--bare", str(remote))
            git(base, "init", str(work))
            git(work, "config", "user.name", "recovery-validator")
            git(work, "config", "user.email", "recovery@example.invalid")
            git(work, "remote", "add", "origin", str(remote))

            (work / "main-seed").write_text("trusted main\n", encoding="utf-8")
            git(work, "add", "main-seed")
            git(work, "commit", "-m", "trusted main")
            git(work, "branch", "-M", "main")
            installation = git(work, "rev-parse", "HEAD")
            git(work, "push", "origin", "HEAD:refs/heads/main")

            # Bootstrap-only state must classify as the only legal write state.
            git(work, "checkout", "--orphan", "state-build")
            subprocess.run(["git", "-C", str(work), "rm", "-rf", "."], text=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "clean", "-fdx"], check=True, text=True, capture_output=True)
            target = work / BOOTSTRAP
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(BOOTSTRAP_MARKER + "\n", encoding="utf-8")
            git(work, "add", BOOTSTRAP)
            git(work, "commit", "-m", "bootstrap state")
            bootstrap_sha = git(work, "rev-parse", "HEAD")
            git(work, "push", "origin", f"HEAD:{STATE_REF}")
            git(work, "checkout", "main")

            os.chdir(work)
            proof = writer_proof(installation)
            bootstrap = recovery.classify_or_recover(
                repository=REPO,
                installation_sha=installation,
                state_ref=STATE_REF,
                writer_surface_proof=proof,
                protection_receipt=Receipt(),
                loader_cls=FakeLoader,
            )
            require(bootstrap["policy_action"] == "materialize", "bootstrap-only state was not writable")
            require(bootstrap["evidence"] is None, "bootstrap classification fabricated evidence")

            # Model one successful protected push, then return to trusted main as a
            # fresh workflow process would after a failed postverify step.
            git(work, "checkout", "state-build")
            for path, value in policy_documents(installation, bootstrap_sha, proof["proof_digest"]).items():
                write_json(work, path, value)
            git(work, "add", *sorted(REQUIRED_POLICY_PATHS))
            git(work, "commit", "-m", "AI-SDLC protected Vertical policy materialization")
            materialization_sha = git(work, "rev-parse", "HEAD")
            git(work, "push", "origin", f"HEAD:{STATE_REF}")
            # Later unrelated Store state may advance without changing policy paths.
            unrelated = work / "state/operator/v1/events/op/example.json"
            unrelated.parent.mkdir(parents=True, exist_ok=True)
            unrelated.write_text("{}\n", encoding="utf-8")
            git(work, "add", str(unrelated.relative_to(work)))
            git(work, "commit", "-m", "unrelated Store progress")
            live_sha = git(work, "rev-parse", "HEAD")
            git(work, "push", "origin", f"HEAD:{STATE_REF}")
            git(work, "checkout", "main")

            remote_before = git(work, "ls-remote", "--refs", "origin", STATE_REF).split()[0]
            first = recovery.classify_or_recover(
                repository=REPO,
                installation_sha=installation,
                state_ref=STATE_REF,
                writer_surface_proof=proof,
                protection_receipt=Receipt(),
                loader_cls=FakeLoader,
            )
            require(first["policy_action"] == "adopt", "existing exact materialization was not adopted")
            require(first["evidence"]["materialization_commit_sha"] == materialization_sha, "wrong materialization commit adopted")
            require(first["evidence"]["materialization_recovery"]["zero_second_push"] is True, "recovery does not attest zero second push")
            recovery.EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
            recovery.EVIDENCE_PATH.write_text(json.dumps(first["evidence"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
            remote_after = git(work, "ls-remote", "--refs", "origin", STATE_REF).split()[0]
            require(remote_before == remote_after == live_sha, "recovery performed a second protected push")

            old_env = set_env(installation)
            postverify._verify_protection = lambda **_kwargs: Receipt()
            sys.modules["operator_vertical_policy_state"] = SimpleNamespace(
                ProtectedVerticalPolicyBundleLoader=FakeLoader
            )
            try:
                # Inject the exact review window: durable write exists, preliminary
                # evidence is reconstructed, but postverify fails and deletes it.
                postverify._require_remote_snapshot_unchanged = (
                    lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        postverify.PostWriteVerticalPolicyVerificationError(
                            "simulated transient postverify failure"
                        )
                    )
                )
                try:
                    postverify.main()
                except postverify.PostWriteVerticalPolicyVerificationError:
                    pass
                else:
                    raise AssertionError("simulated failed postverify unexpectedly passed")
                require(not recovery.EVIDENCE_PATH.exists(), "failed postverify retained preliminary evidence")

                # Fresh process/run: re-adopt exact durable materialization, zero
                # push, then the unchanged postverify path can finalize authority.
                second = recovery.classify_or_recover(
                    repository=REPO,
                    installation_sha=installation,
                    state_ref=STATE_REF,
                    writer_surface_proof=proof,
                    protection_receipt=Receipt(),
                    loader_cls=FakeLoader,
                )
                require(second["policy_action"] == "adopt", "fresh run did not re-adopt materialization")
                recovery.EVIDENCE_PATH.write_text(
                    json.dumps(second["evidence"], indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                postverify._require_remote_snapshot_unchanged = original_unchanged
                finalized = postverify.verify_and_finalize()
                require(finalized["materialization_commit_sha"] == materialization_sha, "postverify finalized wrong materialization")
                require(finalized["post_write_verified_state_ref_sha"] == live_sha, "postverify did not bind stable live snapshot")
                require(
                    git(work, "ls-remote", "--refs", "origin", STATE_REF).split()[0] == live_sha,
                    "fresh recovery/postverify changed protected state ref",
                )
            finally:
                restore_env(old_env)

            # Policy drift after materialization must not be silently adopted.
            git(work, "checkout", "state-build")
            drift_path = f"{POLICY_NAMESPACE}/decision-policy.json"
            drift = json.loads((work / drift_path).read_text(encoding="utf-8"))
            drift["decision_types"] = {"forbidden": {}}
            write_json(work, drift_path, drift)
            git(work, "add", drift_path)
            git(work, "commit", "-m", "foreign policy drift")
            git(work, "push", "origin", f"HEAD:{STATE_REF}")
            git(work, "checkout", "main")
            try:
                recovery.classify_or_recover(
                    repository=REPO,
                    installation_sha=installation,
                    state_ref=STATE_REF,
                    writer_surface_proof=proof,
                    protection_receipt=Receipt(),
                    loader_cls=FakeLoader,
                )
            except Exception as exc:
                require(
                    isinstance(exc, (recovery.VerticalPolicyRecoveryError, AssertionError)),
                    f"drift rejection failed for unexpected reason: {exc}",
                )
            else:
                raise AssertionError("drifted protected policy was adopted")

            print("v0.3 protected Vertical policy recovery validation passed")
            print("- bootstrap-only is the only first-write state")
            print("- durable materialization is adopted by exact commit with zero second push")
            print("- failed postverify deletes evidence; fresh run re-adopts and finalizes")
            print("- unrelated Store progress is allowed; policy drift fails closed")
    finally:
        os.chdir(original_cwd)
        postverify._verify_protection = original_verify
        postverify._require_remote_snapshot_unchanged = original_unchanged
        if old_module is None:
            sys.modules.pop("operator_vertical_policy_state", None)
        else:
            sys.modules["operator_vertical_policy_state"] = old_module


if __name__ == "__main__":
    main()
