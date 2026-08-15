#!/usr/bin/env python3
"""Real-Git adversarial validation for trusted protected policy materialization."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from operator_protected_policy_materializer import (
    POLICY_NAMESPACE,
    REQUIRED_POLICY_PATHS,
    ProtectedPolicyBundleMaterializer,
    ProtectedPolicyDocument,
    ProtectedPolicyMaterializationError,
    ProtectedPolicyMaterializerConfig,
)
from operator_store_git import CasConflict
from operator_store_model import canonical_json, digest_json
from operator_store_protection import (
    PROTECTED,
    UNPROTECTED,
    ProtectionError,
    ProtectionReceipt,
    StaticProtectionVerifier,
)

REPO = "dream-xin/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def run(cwd: Path, *args: str, check=True, input_text: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, text=True, input=input_text,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


class RecordingProtectionVerifier:
    test_only = False

    def __init__(self, states=None):
        self.states = list(states or [(PROTECTED, "ruleset-digest-v1")])
        self.calls = 0

    def verify(self, repository, state_ref):
        index = min(self.calls, len(self.states) - 1)
        status, policy_digest = self.states[index]
        self.calls += 1
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=status,
            verifier_identity="trusted-real-git-validator",
            verified_at=f"2026-08-14T06:00:{self.calls:02d}Z",
            policy_digest=policy_digest,
        )


def exact_remote_sha(checkout: Path) -> str:
    line = run(checkout, "ls-remote", "--refs", "origin", STATE_REF)
    sha, ref = line.split("\t")
    assert ref == STATE_REF and len(sha) == 40
    return sha


def documents(epoch="v0.3-release-1"):
    payloads = {
        "effect-lineage-rollout.json": {"schema_version": "rollout/v1", "policy_epoch": epoch},
        "writer-fence-receipt.json": {"schema_version": "writer-fence/v1", "policy_epoch": epoch},
        "effect-resolution-policy.json": {"schema_version": "resolution/v1", "policy_epoch": epoch},
        "effect-resolution-evidence.json": {"schema_version": "evidence/v1", "policy_epoch": epoch},
        "decision-policy.json": {"schema_version": "decision/v1", "policy_epoch": epoch},
        "bundle-receipt.json": {"schema_version": "bundle/v1", "policy_epoch": epoch},
    }
    rows = tuple(
        ProtectedPolicyDocument(f"{POLICY_NAMESPACE}/{name}", value)
        for name, value in payloads.items()
    )
    assert {row.path for row in rows} == REQUIRED_POLICY_PATHS
    return rows


def assert_raises(expected, callback, message):
    try:
        callback()
    except expected:
        return
    raise AssertionError(message)


def bootstrap(temp: Path, *, unexpected_policy=False):
    remote = temp / "remote.git"
    seed = temp / "seed"
    checkout = temp / "checkout"
    run(temp, "init", "--bare", str(remote))
    seed.mkdir()
    run(seed, "init", "-b", "ai-sdlc-operator-state")
    run(seed, "config", "user.name", "AI-SDLC validator")
    run(seed, "config", "user.email", "validator@example.invalid")
    bootstrap_path = seed / "state/operator/v1/.bootstrap"
    bootstrap_path.parent.mkdir(parents=True)
    bootstrap_path.write_text("ai-sdlc-operator-store-bootstrap-v1\n", encoding="utf-8")
    unrelated = seed / "state/operator/v1/runtime-proof.json"
    unrelated.write_text('{"keep":true}\n', encoding="utf-8")
    if unexpected_policy:
        legacy = seed / POLICY_NAMESPACE / "legacy-policy.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"legacy":true}\n', encoding="utf-8")
    run(seed, "add", ".")
    run(seed, "commit", "-m", "bootstrap protected state")
    run(seed, "remote", "add", "origin", str(remote))
    run(seed, "push", "origin", f"HEAD:{STATE_REF}")

    checkout.mkdir()
    run(checkout, "init")
    run(checkout, "config", "user.name", "AI-SDLC validator")
    run(checkout, "config", "user.email", "validator@example.invalid")
    run(checkout, "remote", "add", "origin", str(remote))
    return remote, checkout


def make_materializer(checkout: Path, verifier=None):
    verifier = verifier or RecordingProtectionVerifier()
    return ProtectedPolicyBundleMaterializer(
        ProtectedPolicyMaterializerConfig(
            repository=REPO,
            trusted_checkout=checkout,
            state_ref=STATE_REF,
        ),
        protection_verifier=verifier,
    )


def validate_happy_path_and_fences():
    with TemporaryDirectory() as raw:
        temp = Path(raw)
        _remote, checkout = bootstrap(temp)
        verifier = RecordingProtectionVerifier()
        materializer = make_materializer(checkout, verifier)
        initial = exact_remote_sha(checkout)
        docs = documents()
        result = materializer.materialize(
            expected_ref_sha=initial,
            documents=docs,
        )
        assert verifier.calls == 2
        assert result.expected_ref_sha == initial
        assert result.materialization_commit_sha == exact_remote_sha(checkout)
        assert result.namespace == POLICY_NAMESPACE
        assert result.protection_verifier_identity == "trusted-real-git-validator"
        assert result.bundle_digest == digest_json({row.path: row.digest for row in result.artifacts})
        assert run(checkout, "rev-parse", f"{result.materialization_commit_sha}^") == initial
        assert set(run(
            checkout, "ls-tree", "-r", "--name-only",
            result.materialization_commit_sha, "--", POLICY_NAMESPACE
        ).splitlines()) == REQUIRED_POLICY_PATHS

        for document in docs:
            durable = json.loads(run(
                checkout, "show", f"{result.materialization_commit_sha}:{document.path}"
            ))
            assert canonical_json(durable) == canonical_json(document.value)
        kept = run(
            checkout, "show",
            f"{result.materialization_commit_sha}:state/operator/v1/runtime-proof.json"
        )
        assert json.loads(kept) == {"keep": True}

        assert_raises(
            CasConflict,
            lambda: materializer.materialize(
                expected_ref_sha=initial,
                documents=documents("v0.3-release-2"),
            ),
            "stale expected protected-state SHA was accepted",
        )
        assert exact_remote_sha(checkout) == result.materialization_commit_sha

        forged = list(docs)
        forged[0] = ProtectedPolicyDocument(forged[0].path, {"forged": True})
        assert_raises(
            ProtectedPolicyMaterializationError,
            lambda: materializer._verify_durable(
                result.materialization_commit_sha, tuple(forged)
            ),
            "durable policy mismatch was accepted",
        )

        materializer._fetch_exact_remote(result.materialization_commit_sha)
        stale_candidate = materializer._build_commit(
            result.materialization_commit_sha,
            documents("v0.3-release-stale-candidate"),
        )
        race_tree = run(
            checkout, "rev-parse", f"{result.materialization_commit_sha}^{{tree}}"
        )
        race_commit = run(
            checkout, "commit-tree", race_tree, "-p",
            result.materialization_commit_sha,
            input_text="independent protected-state race\n",
        )
        run(checkout, "push", "origin", f"{race_commit}:{STATE_REF}")
        assert_raises(
            CasConflict,
            lambda: materializer._push_candidate(stale_candidate),
            "non-fast-forward stale candidate push was accepted",
        )
        assert exact_remote_sha(checkout) == race_commit


def validate_pre_push_race_detection():
    with TemporaryDirectory() as raw:
        temp = Path(raw)
        _remote, checkout = bootstrap(temp)

        class RacingMaterializer(ProtectedPolicyBundleMaterializer):
            def _build_commit(self, expected_ref_sha, docs):
                candidate = super()._build_commit(expected_ref_sha, docs)
                tree = self._git("rev-parse", f"{expected_ref_sha}^{{tree}}").stdout.strip()
                race = self._git(
                    "commit-tree", tree, "-p", expected_ref_sha,
                    input_text="race before policy push\n",
                ).stdout.strip()
                pushed = self._git(
                    "push", "--porcelain", self.config.remote_name,
                    f"{race}:{self.config.state_ref}", check=False,
                )
                assert pushed.returncode == 0
                self.race_sha = race
                return candidate

        materializer = RacingMaterializer(
            ProtectedPolicyMaterializerConfig(repository=REPO, trusted_checkout=checkout),
            protection_verifier=RecordingProtectionVerifier(),
        )
        initial = exact_remote_sha(checkout)
        assert_raises(
            CasConflict,
            lambda: materializer.materialize(
                expected_ref_sha=initial,
                documents=documents(),
            ),
            "remote race before policy push was accepted",
        )
        assert exact_remote_sha(checkout) == materializer.race_sha


def validate_protection_generation_fences():
    with TemporaryDirectory() as raw:
        temp = Path(raw)
        _remote, checkout = bootstrap(temp)
        current = exact_remote_sha(checkout)

        unprotected = make_materializer(
            checkout,
            RecordingProtectionVerifier([(UNPROTECTED, "ruleset-digest-v1")]),
        )
        assert_raises(
            ProtectionError,
            lambda: unprotected.materialize(
                expected_ref_sha=current, documents=documents()
            ),
            "unprotected state ref was accepted",
        )
        assert exact_remote_sha(checkout) == current

        drops_before_push = make_materializer(
            checkout,
            RecordingProtectionVerifier(
                [(PROTECTED, "ruleset-digest-v1"), (UNPROTECTED, "ruleset-digest-v1")]
            ),
        )
        assert_raises(
            ProtectionError,
            lambda: drops_before_push.materialize(
                expected_ref_sha=current, documents=documents()
            ),
            "protection loss before push was accepted",
        )
        assert exact_remote_sha(checkout) == current

        policy_drift = make_materializer(
            checkout,
            RecordingProtectionVerifier(
                [(PROTECTED, "ruleset-digest-v1"), (PROTECTED, "ruleset-digest-v2")]
            ),
        )
        assert_raises(
            ProtectedPolicyMaterializationError,
            lambda: policy_drift.materialize(
                expected_ref_sha=current, documents=documents()
            ),
            "protection policy generation drift was accepted",
        )
        assert exact_remote_sha(checkout) == current

        assert_raises(
            ValueError,
            lambda: ProtectedPolicyBundleMaterializer(
                ProtectedPolicyMaterializerConfig(
                    repository=REPO, trusted_checkout=checkout
                ),
                protection_verifier=StaticProtectionVerifier(status=PROTECTED),
            ),
            "test-only protection verifier was accepted",
        )


def validate_namespace_exactness():
    with TemporaryDirectory() as raw:
        temp = Path(raw)
        _remote, checkout = bootstrap(temp, unexpected_policy=True)
        materializer = make_materializer(checkout)
        current = exact_remote_sha(checkout)
        assert_raises(
            ProtectedPolicyMaterializationError,
            lambda: materializer.materialize(
                expected_ref_sha=current, documents=documents()
            ),
            "unexpected pre-existing policy namespace content was accepted",
        )
        assert exact_remote_sha(checkout) == current


def validate_input_rejection():
    bad_paths = (
        "/config/operator/v03-vertical-policy/effect-lineage-rollout.json",
        "config/operator/v03-vertical-policy/../effect-lineage-rollout.json",
        "config/operator/v03-vertical-policy/effect-lineage-rollout.yaml",
        "state/operator/v1/policy.json",
        "config/operator/v03-vertical-policy\\effect-lineage-rollout.json",
        "config/operator/v03-vertical-policy/nested/effect-lineage-rollout.json",
        "config/operator/v03-vertical-policy/legacy-policy.json",
        "config/operator/v03-vertical-policy/effect-lineage-rollout.json\n",
    )
    for path in bad_paths:
        assert_raises(
            ValueError,
            lambda path=path: ProtectedPolicyDocument(path, {"x": 1}),
            f"bad policy path accepted: {path}",
        )
    assert_raises(
        ValueError,
        lambda: ProtectedPolicyDocument(
            f"{POLICY_NAMESPACE}/decision-policy.json", {"x": float("nan")}
        ),
        "non-strict JSON was accepted",
    )

    with TemporaryDirectory() as raw:
        temp = Path(raw)
        _remote, checkout = bootstrap(temp)
        materializer = make_materializer(checkout)
        current = exact_remote_sha(checkout)
        docs = documents()
        duplicate = (docs[0], docs[0], *docs[1:])
        assert_raises(
            ValueError,
            lambda: materializer.materialize(
                expected_ref_sha=current, documents=duplicate
            ),
            "duplicate protected policy path was accepted",
        )
        incomplete = docs[:-1]
        assert_raises(
            ValueError,
            lambda: materializer.materialize(
                expected_ref_sha=current, documents=incomplete
            ),
            "incomplete protected policy bundle was accepted",
        )
        assert exact_remote_sha(checkout) == current

        assert_raises(
            ValueError,
            lambda: ProtectedPolicyMaterializerConfig(
                repository=REPO,
                trusted_checkout=checkout,
                remote_name="--upload-pack=evil",
            ),
            "option-like remote name was accepted",
        )


def main():
    validate_input_rejection()
    validate_namespace_exactness()
    validate_protection_generation_fences()
    validate_happy_path_and_fences()
    validate_pre_push_race_detection()
    print("trusted protected policy materializer validation passed")
    print("- exact six-file config/operator/v03-vertical-policy bundle only")
    print("- fresh production protection verification at start and before push")
    print("- exact remote SHA + exact-parent non-force CAS")
    print("- unrelated protected-state tree paths are preserved")
    print("- durable namespace and canonical-JSON digests are re-read and verified")
    print("- stale SHA, policy drift, path escape, incomplete bundle and races fail closed")


if __name__ == "__main__":
    main()
