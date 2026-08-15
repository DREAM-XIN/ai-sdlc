#!/usr/bin/env python3
"""Deterministic validation for stable post-write Vertical policy authority snapshots."""
from __future__ import annotations

from pathlib import Path

import postverify_v03_vertical_policy_state as postverify

WORKFLOW = Path(".github/workflows/materialize-v03-vertical-policy-state.yml")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def validate_stable_refresh():
    original_remote = postverify._remote_ref_sha
    original_git = postverify._git
    try:
        calls = iter(["a" * 40, "a" * 40])
        postverify._remote_ref_sha = lambda _ref: next(calls)
        postverify._git = lambda *args, **kwargs: (
            "a" * 40 if args[:2] == ("rev-parse", "--verify") else ""
        )
        assert postverify._refresh_stable_tracking_ref(
            "refs/heads/ai-sdlc-operator-state"
        ) == "a" * 40

        calls = iter(["a" * 40, "b" * 40])
        postverify._remote_ref_sha = lambda _ref: next(calls)
        try:
            postverify._refresh_stable_tracking_ref(
                "refs/heads/ai-sdlc-operator-state"
            )
        except postverify.PostWriteVerticalPolicyVerificationError:
            pass
        else:
            raise AssertionError("remote race during stable snapshot refresh was accepted")

        postverify._remote_ref_sha = lambda _ref: "b" * 40
        try:
            postverify._require_remote_snapshot_unchanged(
                "refs/heads/ai-sdlc-operator-state", "a" * 40
            )
        except postverify.PostWriteVerticalPolicyVerificationError:
            pass
        else:
            raise AssertionError("remote race during #267 authority load was accepted")
    finally:
        postverify._remote_ref_sha = original_remote
        postverify._git = original_git


def validate_protection_generation():
    class Receipt:
        def __init__(self, identity, digest):
            self.repository = "dream-xin/ai-sdlc"
            self.state_ref = "refs/heads/ai-sdlc-operator-state"
            self.verifier_identity = identity
            self.policy_digest = digest

    assert postverify._same_protection_generation(
        Receipt("github-ruleset:integration:1", "d1"),
        Receipt("github-ruleset:integration:1", "d1"),
    )
    assert not postverify._same_protection_generation(
        Receipt("github-ruleset:integration:1", "d1"),
        Receipt("github-ruleset:integration:1", "d2"),
    )
    assert not postverify._same_protection_generation(
        Receipt("github-ruleset:integration:1", "d1"),
        Receipt("github-ruleset:integration:2", "d1"),
    )


def validate_workflow_ordering():
    raw = WORKFLOW.read_text(encoding="utf-8")
    materialize = "PYTHONPATH=scripts python scripts/materialize_v03_vertical_policy_state.py"
    postwrite = "PYTHONPATH=scripts python scripts/postverify_v03_vertical_policy_state.py"
    upload = "name: Upload durable materialization evidence"
    publish = "name: Publish materialization receipt to Issue 263"
    require(materialize in raw, "materialization step missing")
    require(postwrite in raw, "stable post-write authority verifier missing")
    require(raw.index(materialize) < raw.index(postwrite), "post-write verifier runs before write")
    require(raw.index(postwrite) < raw.index(upload), "evidence upload precedes post-write verification")
    require(raw.index(postwrite) < raw.index(publish), "Issue #263 publication precedes post-write verification")
    require(
        raw.count("AI_SDLC_OPERATOR_ADMIN_TOKEN: ${{ secrets.AI_SDLC_OPERATOR_ADMIN_TOKEN }}") >= 2,
        "post-write protection re-verification lacks admin verifier authority",
    )
    require(
        raw.count("AI_SDLC_OPERATOR_APP_SLUG: ${{ steps.writer-token.outputs.app-slug }}") >= 2,
        "post-write protection verifier lacks exact Integration slug binding",
    )


def validate_failure_evidence_boundary():
    raw = Path("scripts/postverify_v03_vertical_policy_state.py").read_text(encoding="utf-8")
    require(
        "EVIDENCE_PATH.unlink(missing_ok=True)" in raw,
        "failed post-write verification may leave preliminary evidence behind",
    )
    require(
        "post_write_verified_state_ref_sha" in raw,
        "final evidence does not bind stable post-write state-ref SHA",
    )
    require(
        "post_write_protection" in raw,
        "final evidence does not bind post-write protection receipt",
    )
    require(
        "_require_remote_snapshot_unchanged(state_ref, snapshot_sha)" in raw,
        "remote snapshot is not bracketed across #267 loader verification",
    )


def main():
    validate_stable_refresh()
    validate_protection_generation()
    validate_workflow_ordering()
    validate_failure_evidence_boundary()
    print("stable post-write Vertical policy authority validation passed")
    print("- remote-before/fetch/local/remote-after stable snapshot")
    print("- remote snapshot bracketed across #267 two-anchor loader")
    print("- protection generation bracketed across post-write verification")
    print("- failed post-write verification removes preliminary release evidence")


if __name__ == "__main__":
    main()
