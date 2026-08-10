#!/usr/bin/env python3
"""Deterministic adversarial validation for v0.3 Effect Lineage safety."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from operator_store import (
    StoreCommandError,
    plan_launch_lookup,
    plan_operation_start,
    plan_semantic_reservation,
    plan_takeover,
)
from operator_store_git import CasConflict, MemoryStateRefBackend
from operator_store_model import (
    StoreSnapshot,
    digest_json,
    operation_events,
    projection_path,
    rebuild_projection,
    reservation_path,
)
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import VERTICAL_PROFILE
from operator_effect_lineage_fences import (
    plan_lineage_authorize_launch,
    plan_lineage_dispatch_claim,
)
from operator_effect_lineage_integration import (
    assert_lineage_member,
    plan_lineage_gated_reservation,
)
from operator_effect_lineage_model import (
    CausalWorkResolver,
    AmbiguousLineageError,
    anchor_path,
    effect_lineage_id,
    lineage_projection_path,
    member_lineage,
    rebuild_lineage_projection,
)
from operator_effect_migration import (
    LegacyMigrationEvidence,
    plan_legacy_lineage_attachment,
    validate_lineage_rollout,
)
from operator_effect_resolution import (
    ALLOWED_RESOLUTION_CHOICES,
    EffectResolutionAuthority,
    plan_effect_resolution,
    resolution_identity,
)
from validate_v03_effect_lineage_contract import main as validate_release_contract

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "spec" / "operator" / "effect-lineage"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
REPO = "DREAM-XIN/ai-sdlc"
NOW = "2026-08-10T14:30:00Z"
TRUST = "effect-lineage-test-trust"
PROFILE_DIGEST = digest_json({"operation_profile": VERTICAL_PROFILE, "effect_lineage_required": True})


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_code(code, fn):
    try:
        fn()
    except StoreCommandError as exc:
        require(exc.code == code, f"expected {code}, got {exc.code}: {exc}")
        return
    raise AssertionError(f"expected StoreCommandError {code}")


def receipt():
    return StaticProtectionVerifier(status=PROTECTED).verify(REPO, STATE_REF)


def commit(backend, plan):
    return backend.commit(plan, receipt())


def start(backend, feature, rev=7):
    snapshot = backend.read_snapshot()
    result = commit(
        backend,
        plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id=feature,
            expected_revision=rev,
            idempotency_key=f"start-{feature}",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            operation_profile=VERTICAL_PROFILE,
        ),
    )
    return result.snapshot, result.result["operation_id"]


def gate(
    snapshot,
    *,
    operation_id,
    feature,
    rev=7,
    stage="code-review",
    role="reviewer",
    candidate="a" * 40,
    task_identity=None,
    logical_work_slot="CODE_REVIEW",
    task_id=None,
    target_ref=None,
):
    task_identity = task_identity or f"vertical:code-review:{candidate}"
    return plan_lineage_gated_reservation(
        snapshot,
        operation_id=operation_id,
        generation=rebuild_projection(snapshot, operation_id)["generation"],
        target_repository=REPO,
        feature_id=feature,
        expected_revision=rev,
        current_stage=stage,
        task_identity=task_identity,
        role=role,
        candidate_head_sha=candidate,
        current_target_ref=target_ref or f"feature/{feature}",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        logical_work_slot=logical_work_slot,
        task_id=task_id or task_identity,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
        trusted_profile_digest=PROFILE_DIGEST,
    )


def authority():
    return EffectResolutionAuthority(
        authority_id="test-authority-v1",
        allowed_choices=frozenset(ALLOWED_RESOLUTION_CHOICES),
        allowed_resolvers=frozenset({"trusted-test-resolver"}),
        trusted_policy_ref="protected://effect-resolution/v1",
        trusted_policy_digest=digest_json({"policy": "effect-resolution-v1"}),
    )


def resolution_id_for(
    *,
    lineage_id,
    predecessor_key,
    predecessor_external,
    operation_id,
    generation,
    revision,
    target_ref,
    candidate,
    proposal_id,
    proposed_key,
    choice,
    evidence,
):
    auth = authority()
    return resolution_identity(
        {
            "target_repository": REPO.lower(),
            "feature_id": "F-LINEAGE-TEST",
            "effect_lineage_id": lineage_id,
            "predecessor_semantic_effect_key": predecessor_key,
            "predecessor_external_dispatch_key": predecessor_external,
            "current_operation_id": operation_id,
            "current_operation_generation": generation,
            "current_feature_revision": revision,
            "current_target_ref": target_ref,
            "current_candidate_head_sha": candidate,
            "successor_proposal_id": proposal_id,
            "successor_proposed_semantic_effect_key": proposed_key,
            "choice": choice,
            "trusted_policy_ref": auth.trusted_policy_ref,
            "trusted_policy_digest": auth.trusted_policy_digest,
            "resolver_identity": "trusted-test-resolver",
            "evidence_digests": [digest_json(row) for row in evidence],
        }
    )


def resolve_plan(
    snapshot,
    *,
    lineage_id,
    predecessor_key,
    predecessor_external,
    operation_id,
    revision,
    target_ref,
    candidate,
    proposal_id,
    proposed_key,
    choice,
    evidence,
):
    generation = rebuild_projection(snapshot, operation_id)["generation"]
    rid = resolution_id_for(
        lineage_id=lineage_id,
        predecessor_key=predecessor_key,
        predecessor_external=predecessor_external,
        operation_id=operation_id,
        generation=generation,
        revision=revision,
        target_ref=target_ref,
        candidate=candidate,
        proposal_id=proposal_id,
        proposed_key=proposed_key,
        choice=choice,
        evidence=evidence,
    )
    return plan_effect_resolution(
        snapshot,
        authority=authority(),
        resolution_id=rid,
        target_repository=REPO,
        feature_id="F-LINEAGE-TEST",
        effect_lineage_id=lineage_id,
        predecessor_semantic_effect_key=predecessor_key,
        predecessor_external_dispatch_key=predecessor_external,
        current_operation_id=operation_id,
        current_operation_generation=generation,
        current_feature_revision=revision,
        current_target_ref=target_ref,
        current_candidate_head_sha=candidate,
        successor_proposal_id=proposal_id,
        successor_proposed_semantic_effect_key=proposed_key,
        choice=choice,
        resolver_identity="trusted-test-resolver",
        evidence=evidence,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )


def validate_identity_stability():
    resolver = CausalWorkResolver()
    causal_a = resolver.resolve(
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        logical_work_slot="CODE_REVIEW",
        task_id="candidate-a",
    )
    causal_b = resolver.resolve(
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        logical_work_slot="CODE_REREVIEW",
        task_id="candidate-b-remediation-9",
    )
    require(causal_a == causal_b, "candidate/remediation round changed trusted review causal work")
    lid_a = effect_lineage_id(
        target_repository=REPO,
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        causal_work_id=causal_a.causal_work_id,
        external_effect_scope=causal_a.external_effect_scope,
    )
    lid_b = effect_lineage_id(
        target_repository=REPO,
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        causal_work_id=causal_b.causal_work_id,
        external_effect_scope=causal_b.external_effect_scope,
    )
    require(lid_a == lid_b, "revision/candidate/generation-neutral causal identity did not converge")
    try:
        resolver.resolve(
            feature_id="F-LINEAGE-TEST",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            role="reviewer",
            logical_work_slot="CLIENT-CHOSEN-NEW-LINEAGE",
            task_id="random",
        )
        raise AssertionError("ambiguous client-selected lineage discriminator was accepted")
    except AmbiguousLineageError:
        pass


def validate_candidate_block_and_never_authorized_resolution():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LINEAGE-TEST")
    root = commit(backend, gate(s, operation_id=op, feature="F-LINEAGE-TEST", candidate="a" * 40))
    s = root.snapshot
    k0 = root.result["semantic_effect_key"]
    x0 = root.result["external_dispatch_key"]
    lineage_id = root.result["effect_lineage_id"]

    blocked = commit(
        backend,
        gate(
            s,
            operation_id=op,
            feature="F-LINEAGE-TEST",
            candidate="b" * 40,
            task_identity="vertical:code-review:" + "b" * 40,
        ),
    )
    s = blocked.snapshot
    require(blocked.result["status"] == "BLOCKED", "candidate B did not become a blocked successor proposal")
    k1 = blocked.result["proposed_semantic_effect_key"]
    require(s.get(reservation_path(k1)) is None, "blocked candidate B received an external reservation")
    require(member_lineage(s, k1) is None, "blocked candidate B became a lineage member")
    projection = rebuild_lineage_projection(s, lineage_id)
    require(projection["predecessor_state"] == "NEVER_AUTHORIZED", "never-authorized predecessor state was not preserved")

    evidence = [{
        "type": "EXTERNAL_NOT_LAUNCHED",
        "external_dispatch_key": x0,
        "observed_at": NOW,
        "source_digest": digest_json({"lookup": x0, "state": "NOT_LAUNCHED"}),
    }]
    resolved = commit(
        backend,
        resolve_plan(
            s,
            lineage_id=lineage_id,
            predecessor_key=k0,
            predecessor_external=x0,
            operation_id=op,
            revision=7,
            target_ref="feature/F-LINEAGE-TEST",
            candidate="b" * 40,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence=evidence,
        ),
    )
    s = resolved.snapshot
    require(resolved.result["semantic_effect_key"] == k1, "safe successor was not atomically activated")
    require(s.get(reservation_path(k1)) is not None, "safe successor reservation missing after resolution")
    require(rebuild_lineage_projection(s, lineage_id)["current_leaf_semantic_effect_key"] == k1, "successor is not current lineage leaf")
    require(rebuild_projection(s, op)["status"] == "RUNNING", "safe resolution did not clear lineage block")


def validate_stale_runner_authorized_not_launched():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LINEAGE-TEST")
    root = commit(backend, gate(s, operation_id=op, feature="F-LINEAGE-TEST", candidate="a" * 40))
    s = root.snapshot
    k0 = root.result["semantic_effect_key"]
    x0 = root.result["external_dispatch_key"]
    lineage_id = root.result["effect_lineage_id"]
    claim = commit(
        backend,
        plan_lineage_dispatch_claim(
            s,
            effect_lineage_id=lineage_id,
            operation_id=op,
            generation=0,
            effect_key=k0,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    s = claim.snapshot
    # Durable launch authorization is the linearization point; stale runner pauses here.
    authorized = commit(
        backend,
        plan_lineage_authorize_launch(
            s,
            effect_lineage_id=lineage_id,
            operation_id=op,
            generation=0,
            claim_id=claim.result["claim_id"],
            dispatch_id="stale-runner-k0",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=7,
            verified_stage="code-review",
            verified_candidate_head_sha="a" * 40,
        ),
    )
    s = authorized.snapshot
    require(any(e["event_type"] == "dispatch.launch.authorized" and e["payload"].get("external_dispatch_key") == x0 for e in operation_events(s, op)), "K0 launch authorization is not durable")
    s = commit(
        backend,
        plan_launch_lookup(
            s,
            operation_id=op,
            generation=0,
            external_dispatch_key_value=x0,
            lookup_state="NOT_LAUNCHED",
            receipt_id=None,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    ).snapshot
    blocked = commit(
        backend,
        gate(
            s,
            operation_id=op,
            feature="F-LINEAGE-TEST",
            candidate="b" * 40,
            task_identity="vertical:code-review:" + "b" * 40,
        ),
    )
    s = blocked.snapshot
    k1 = blocked.result["proposed_semantic_effect_key"]
    require(blocked.result["predecessor_state"] == "AUTHORIZED_NOT_LAUNCHED_OBSERVED", "authorized + NOT_LAUNCHED state collapsed incorrectly")
    require(s.get(reservation_path(k1)) is None, "K1 reservation exists during stale-runner window")
    evidence = [{
        "type": "EXTERNAL_NOT_LAUNCHED",
        "external_dispatch_key": x0,
        "observed_at": NOW,
        "source_digest": digest_json({"lookup": x0, "state": "NOT_LAUNCHED"}),
    }]
    expect_code(
        "AUTHORIZED_EFFECT_STILL_EXECUTABLE",
        lambda: resolve_plan(
            s,
            lineage_id=lineage_id,
            predecessor_key=k0,
            predecessor_external=x0,
            operation_id=op,
            revision=7,
            target_ref="feature/F-LINEAGE-TEST",
            candidate="b" * 40,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence=evidence,
        ),
    )
    require(s.get(reservation_path(k1)) is None, "failed resolution attempt materialized K1")
    authorized_keys = {
        e["payload"].get("external_dispatch_key")
        for e in operation_events(s, op)
        if e["event_type"] == "dispatch.launch.authorized"
    }
    require(authorized_keys == {x0}, "NOT_LAUNCHED observation made both K0/K1 launch-authorized")
    # The paused runner already owns only the exact durable K0 authorization; no new key is available.
    require(blocked.result.get("external_dispatch_key") is None, "blocked successor leaked an external key")


def validate_cas_race_generation_and_stale_resolution():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LINEAGE-TEST")
    root = commit(backend, gate(s, operation_id=op, feature="F-LINEAGE-TEST", candidate="a" * 40))
    s = root.snapshot
    lineage_id = root.result["effect_lineage_id"]
    stale_b_plan = gate(
        s,
        operation_id=op,
        feature="F-LINEAGE-TEST",
        candidate="b" * 40,
        task_identity="vertical:code-review:" + "b" * 40,
    )
    b = commit(backend, stale_b_plan)
    s = b.snapshot
    stale_snapshot = StoreSnapshot(ref_sha=stale_b_plan.expected_ref_sha, files=dict(root.snapshot.files))
    stale_c_plan = gate(
        stale_snapshot,
        operation_id=op,
        feature="F-LINEAGE-TEST",
        candidate="c" * 40,
        task_identity="vertical:code-review:" + "c" * 40,
    )
    try:
        backend.commit(stale_c_plan, receipt())
        raise AssertionError("stale concurrent lineage plan unexpectedly won CAS")
    except CasConflict:
        pass
    c = backend.commit_replanned(
        lambda fresh: gate(
            fresh,
            operation_id=op,
            feature="F-LINEAGE-TEST",
            candidate="c" * 40,
            task_identity="vertical:code-review:" + "c" * 40,
        ),
        receipt(),
    )
    s = c.snapshot
    require(s.get(reservation_path(b.result["proposed_semantic_effect_key"])) is None, "candidate B proposal became active during planner race")
    require(s.get(reservation_path(c.result["proposed_semantic_effect_key"])) is None, "candidate C proposal became active during planner race")
    require(rebuild_lineage_projection(s, lineage_id)["current_proposal_id"] == c.result["proposal_id"], "CAS loser did not re-plan to current lineage state")

    # Resolution bound to B is stale after C becomes current proposal.
    x0 = root.result["external_dispatch_key"]
    evidence = [{
        "type": "EXTERNAL_NOT_LAUNCHED",
        "external_dispatch_key": x0,
        "observed_at": NOW,
        "source_digest": "trusted-nonlaunch",
    }]
    stale_resolution = resolve_plan(
        b.snapshot,
        lineage_id=lineage_id,
        predecessor_key=root.result["semantic_effect_key"],
        predecessor_external=x0,
        operation_id=op,
        revision=7,
        target_ref="feature/F-LINEAGE-TEST",
        candidate="b" * 40,
        proposal_id=b.result["proposal_id"],
        proposed_key=b.result["proposed_semantic_effect_key"],
        choice="PROVE_NOT_LAUNCHED",
        evidence=evidence,
    )
    # The stale bytes cannot commit by ref CAS, and semantically replaying the old binding is rejected.
    try:
        backend.commit(stale_resolution, receipt())
        raise AssertionError("stale resolution unexpectedly committed")
    except CasConflict:
        pass
    rid = resolution_id_for(
        lineage_id=lineage_id,
        predecessor_key=root.result["semantic_effect_key"],
        predecessor_external=x0,
        operation_id=op,
        generation=0,
        revision=7,
        target_ref="feature/F-LINEAGE-TEST",
        candidate="b" * 40,
        proposal_id=b.result["proposal_id"],
        proposed_key=b.result["proposed_semantic_effect_key"],
        choice="PROVE_NOT_LAUNCHED",
        evidence=evidence,
    )
    expect_code(
        "STALE_RESOLUTION",
        lambda: plan_effect_resolution(
            s,
            authority=authority(),
            resolution_id=rid,
            target_repository=REPO,
            feature_id="F-LINEAGE-TEST",
            effect_lineage_id=lineage_id,
            predecessor_semantic_effect_key=root.result["semantic_effect_key"],
            predecessor_external_dispatch_key=x0,
            current_operation_id=op,
            current_operation_generation=0,
            current_feature_revision=7,
            current_target_ref="feature/F-LINEAGE-TEST",
            current_candidate_head_sha="b" * 40,
            successor_proposal_id=b.result["proposal_id"],
            successor_proposed_semantic_effect_key=b.result["proposed_semantic_effect_key"],
            choice="PROVE_NOT_LAUNCHED",
            resolver_identity="trusted-test-resolver",
            evidence=evidence,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )

    takeover = commit(backend, plan_takeover(s, operation_id=op, occurred_at=NOW, trusted_context_digest=TRUST))
    s = takeover.snapshot
    require(rebuild_projection(s, op)["generation"] == 1 and rebuild_projection(s, op)["status"] == "BLOCKED", "generation takeover lost durable lineage block")
    next_plan = gate(
        s,
        operation_id=op,
        feature="F-LINEAGE-TEST",
        candidate="c" * 40,
        task_identity="vertical:code-review:" + "c" * 40,
    )
    require(next_plan.result["effect_lineage_id"] == lineage_id, "generation takeover manufactured a fresh lineage")
    require(next_plan.result["status"] == "BLOCKED", "generation takeover bypassed unresolved predecessor")


def validate_migration_projection_and_mixed_writer():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LEGACY", rev=3)
    legacy = commit(
        backend,
        plan_semantic_reservation(
            s,
            operation_id=op,
            generation=0,
            target_repository=REPO,
            feature_id="F-LEGACY",
            expected_revision=3,
            current_stage="implementation",
            task_identity="vertical:implementation:3",
            role="developer",
            candidate_head_sha=None,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    s = legacy.snapshot
    key = legacy.result["semantic_effect_key"]
    expect_code("EFFECT_LINEAGE_REQUIRED", lambda: assert_lineage_member(s, key))
    ambiguous = commit(
        backend,
        plan_legacy_lineage_attachment(
            s,
            semantic_effect_key=key,
            target_repository=REPO,
            feature_id="F-LEGACY",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            role="developer",
            logical_work_slot="IMPLEMENTATION_WORK",
            task_id="vertical:implementation:3",
            evidence=LegacyMigrationEvidence(
                source="protected-store-reconstruction",
                provenance_digest="ambiguous-legacy-proof",
                unique_lineage_proven=False,
            ),
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    require(ambiguous.result["reason"] == "LEGACY_UNRESOLVED_LINEAGE", "ambiguous legacy reservation did not fail closed")
    require(member_lineage(ambiguous.snapshot, key) is None, "ambiguous legacy reservation was attached anyway")

    backend2 = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s2, op2 = start(backend2, "F-LEGACY", rev=3)
    legacy2 = commit(
        backend2,
        plan_semantic_reservation(
            s2,
            operation_id=op2,
            generation=0,
            target_repository=REPO,
            feature_id="F-LEGACY",
            expected_revision=3,
            current_stage="implementation",
            task_identity="vertical:implementation:3",
            role="developer",
            candidate_head_sha=None,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    attached = commit(
        backend2,
        plan_legacy_lineage_attachment(
            legacy2.snapshot,
            semantic_effect_key=legacy2.result["semantic_effect_key"],
            target_repository=REPO,
            feature_id="F-LEGACY",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            role="developer",
            logical_work_slot="IMPLEMENTATION_WORK",
            task_id="vertical:implementation:3",
            evidence=LegacyMigrationEvidence(
                source="trusted-profile-reconstruction",
                provenance_digest="unique-legacy-proof",
                unique_lineage_proven=True,
            ),
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    lid = attached.result["effect_lineage_id"]
    projection = rebuild_lineage_projection(attached.snapshot, lid)
    without_cache = StoreSnapshot(
        ref_sha=attached.snapshot.ref_sha,
        files={k: v for k, v in attached.snapshot.files.items() if k != lineage_projection_path(lid)},
    )
    require(rebuild_lineage_projection(without_cache, lid) == projection, "lineage projection is not rebuildable from immutable facts")
    try:
        validate_lineage_rollout(old_writers_quiesced=False, effect_lineage_required=True)
        raise AssertionError("mixed old/new writers were accepted")
    except StoreCommandError as exc:
        require(exc.code == "MIXED_WRITER_FORBIDDEN", "wrong mixed-writer failure code")


def validate_schemas_and_bypass_rejection():
    schemas = {}
    for name in (
        "lineage-anchor",
        "lineage-member",
        "lineage-proposal",
        "lineage-event",
        "lineage-projection",
        "effect-resolution-record",
    ):
        schema = json.loads((SCHEMA_ROOT / f"{name}.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        schemas[name] = Draft202012Validator(schema)

    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LINEAGE-TEST")
    root = commit(backend, gate(s, operation_id=op, feature="F-LINEAGE-TEST"))
    s = root.snapshot
    lid = root.result["effect_lineage_id"]
    schemas["lineage-anchor"].validate(s.get(anchor_path(lid)))
    for path, value in s.files.items():
        if f"/effect-lineages/members/{lid}/" in path:
            schemas["lineage-member"].validate(value)
        elif path == lineage_projection_path(lid):
            schemas["lineage-projection"].validate(value)

    try:
        EffectResolutionAuthority(
            authority_id="bad",
            allowed_choices=frozenset({"FORCE_RETRY"}),
            allowed_resolvers=frozenset({"worker"}),
            trusted_policy_ref="bad",
            trusted_policy_digest="bad",
        )
        raise AssertionError("FORCE_RETRY authority expansion was accepted")
    except ValueError:
        pass
    for bypass in ("IGNORE_UNKNOWN", "DROP_RESERVATION", "NEW_KEY_ANYWAY"):
        require(bypass not in ALLOWED_RESOLUTION_CHOICES, f"forbidden bypass leaked into allowed resolution choices: {bypass}")


def main():
    validate_identity_stability()
    validate_candidate_block_and_never_authorized_resolution()
    validate_stale_runner_authorized_not_launched()
    validate_cas_race_generation_and_stale_resolution()
    validate_migration_projection_and_mixed_writer()
    validate_schemas_and_bypass_rejection()
    validate_release_contract()
    print("Operator Effect Lineage validation passed")


if __name__ == "__main__":
    main()
