#!/usr/bin/env python3
"""Deterministic adversarial validation for v0.3 Effect Lineage safety."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from operator_store import (
    StoreCommandError,
    plan_authorize_launch,
    plan_dispatch_claim,
    plan_launch_lookup,
    plan_operation_start,
    plan_semantic_reservation,
    plan_takeover,
)
from operator_store_backends import OperatorStoreRuntime, StoreBackendError
from operator_store_git import CasConflict, MemoryStateRefBackend
from operator_store_model import StoreSnapshot, digest_json, operation_events, rebuild_projection, reservation_path
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE
from operator_effect_lineage_fences import plan_lineage_authorize_launch, plan_lineage_dispatch_claim
from operator_effect_lineage_integration import plan_lineage_gated_reservation
from operator_effect_lineage_model import (
    AmbiguousLineageError,
    CausalWorkResolver,
    anchor_path,
    effect_lineage_id,
    lineage_projection_path,
    member_lineage,
    rebuild_lineage_projection,
)
from operator_effect_migration import plan_legacy_lineage_attachment, reconstruct_legacy_lineage
from operator_effect_resolution import (
    ALLOWED_RESOLUTION_CHOICES,
    EffectResolutionAuthority,
    TrustedEffectEvidenceVerifier,
    plan_effect_resolution,
    resolution_identity,
)
from operator_effect_rollout import (
    EffectLineageWriteFence,
    ProtectedEffectLineageRolloutVerifier,
    REQUIRED_FENCED_CAPABILITIES,
    ROLLOUT_SCHEMA,
    WRITER_FENCE_SCHEMA,
)
from validate_v03_effect_lineage_contract import main as validate_release_contract

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "spec" / "operator" / "effect-lineage"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
REPO = "DREAM-XIN/ai-sdlc"
NOW = "2026-08-10T14:58:00Z"
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


def expect_backend_code(code, fn):
    try:
        fn()
    except StoreBackendError as exc:
        require(exc.code == code, f"expected {code}, got {exc.code}: {exc}")
        return
    raise AssertionError(f"expected StoreBackendError {code}")


def receipt():
    return StaticProtectionVerifier(status=PROTECTED).verify(REPO, STATE_REF)


def commit(backend, plan):
    return backend.commit(plan, receipt())


def start(backend, feature, rev=7):
    result = commit(
        backend,
        plan_operation_start(
            backend.read_snapshot(),
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


def feature_truth(feature_id="F-LINEAGE-TEST", *, rev=7, stage="code-review", candidate="b" * 40, target_ref=None, tasks=None):
    manifest = {
        "protocol_version": "0.1.0",
        "revision": rev,
        "feature": {"id": feature_id, "title": "lineage fixture", "risk": "high", "issue": "#1"},
        "workflow": {
            "profile": "standard-feature",
            "status": "ACTIVE",
            "current_stage": stage,
            "stages": [{"id": stage, "status": "WORKING"}],
        },
        "tasks": list(tasks or []),
        "artifacts": [],
        "gates": [],
        "evidence": [],
        "applied_events": [],
        "updated_at": NOW,
    }
    feature = FeatureSnapshot.from_manifest(
        repository=REPO,
        target_ref=target_ref or f"feature/{feature_id}",
        manifest=manifest,
        candidate_pr_number=1,
        candidate_head_sha=candidate,
    )
    return feature, manifest


def authority(*, profile_digest=PROFILE_DIGEST):
    return EffectResolutionAuthority(
        authority_id="test-authority-v1",
        allowed_choices=frozenset(ALLOWED_RESOLUTION_CHOICES),
        allowed_resolvers=frozenset({"trusted-test-resolver"}),
        trusted_policy_ref="protected://effect-resolution/v1",
        trusted_policy_digest=digest_json({"policy": "effect-resolution-v1"}),
        operation_profile=VERTICAL_PROFILE,
        trusted_profile_digest=profile_digest,
    )


def evidence_verifier(facts, *, strong=frozenset()):
    return TrustedEffectEvidenceVerifier(
        source_id="trusted-effect-source/v1",
        source_digest=digest_json({"source": "trusted-effect-source/v1", "facts": facts}),
        fact_loader=lambda ref: dict(facts[ref]),
        strong_evidence_types=frozenset(strong),
    )


def resolution_id_for(
    *,
    lineage_id,
    predecessor_key,
    predecessor_external,
    operation_id,
    feature,
    proposal_id,
    proposed_key,
    choice,
    evidence_refs,
    verifier,
    auth,
    snapshot,
):
    generation = rebuild_projection(snapshot, operation_id)["generation"]
    verified = verifier.verify(evidence_refs, predecessor_external_dispatch_key=predecessor_external)
    return resolution_identity(
        {
            "target_repository": feature.repository,
            "feature_id": feature.feature_id,
            "effect_lineage_id": lineage_id,
            "predecessor_semantic_effect_key": predecessor_key,
            "predecessor_external_dispatch_key": predecessor_external,
            "current_operation_id": operation_id,
            "current_operation_generation": generation,
            "current_feature_revision": feature.revision,
            "current_target_ref": feature.target_ref,
            "current_candidate_head_sha": feature.candidate_head_sha,
            "successor_proposal_id": proposal_id,
            "successor_proposed_semantic_effect_key": proposed_key,
            "choice": choice,
            "trusted_policy_ref": auth.trusted_policy_ref,
            "trusted_policy_digest": auth.trusted_policy_digest,
            "resolver_identity": "trusted-test-resolver",
            "evidence_digests": [row["evidence_digest"] for row in verified],
        }
    )


def resolve_plan(
    snapshot,
    *,
    lineage_id,
    predecessor_key,
    predecessor_external,
    operation_id,
    feature,
    proposal_id,
    proposed_key,
    choice,
    evidence_refs,
    verifier,
    auth=None,
):
    auth = auth or authority()
    generation = rebuild_projection(snapshot, operation_id)["generation"]
    rid = resolution_id_for(
        lineage_id=lineage_id,
        predecessor_key=predecessor_key,
        predecessor_external=predecessor_external,
        operation_id=operation_id,
        feature=feature,
        proposal_id=proposal_id,
        proposed_key=proposed_key,
        choice=choice,
        evidence_refs=evidence_refs,
        verifier=verifier,
        auth=auth,
        snapshot=snapshot,
    )
    return plan_effect_resolution(
        snapshot,
        authority=auth,
        trusted_feature=feature,
        evidence_verifier=verifier,
        resolution_id=rid,
        effect_lineage_id=lineage_id,
        predecessor_semantic_effect_key=predecessor_key,
        predecessor_external_dispatch_key=predecessor_external,
        current_operation_id=operation_id,
        current_operation_generation=generation,
        successor_proposal_id=proposal_id,
        successor_proposed_semantic_effect_key=proposed_key,
        choice=choice,
        resolver_identity="trusted-test-resolver",
        evidence_refs=evidence_refs,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )


def validate_identity_stability():
    resolver = CausalWorkResolver()
    a = resolver.resolve(
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        logical_work_slot="CODE_REVIEW",
        task_id="candidate-a",
    )
    b = resolver.resolve(
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        logical_work_slot="CODE_REREVIEW",
        task_id="remediation-9",
    )
    require(a == b, "candidate/remediation round changed trusted review causal work")
    lid_a = effect_lineage_id(
        target_repository=REPO,
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        causal_work_id=a.causal_work_id,
        external_effect_scope=a.external_effect_scope,
    )
    lid_b = effect_lineage_id(
        target_repository=REPO,
        feature_id="F-LINEAGE-TEST",
        operation_profile=VERTICAL_PROFILE,
        effect_kind="worker-dispatch",
        role="reviewer",
        causal_work_id=b.causal_work_id,
        external_effect_scope=b.external_effect_scope,
    )
    require(lid_a == lid_b, "candidate change manufactured a new trusted lineage")
    try:
        resolver.resolve(
            feature_id="F-LINEAGE-TEST",
            operation_profile=VERTICAL_PROFILE,
            effect_kind="worker-dispatch",
            role="reviewer",
            logical_work_slot="CLIENT-CHOSEN-NEW-LINEAGE",
            task_id="random",
        )
        raise AssertionError("client-selected lineage discriminator was accepted")
    except AmbiguousLineageError:
        pass


def _root_and_blocked(*, authorize=False, lookup_not_launched=False):
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LINEAGE-TEST")
    root = commit(backend, gate(s, operation_id=op, feature="F-LINEAGE-TEST", candidate="a" * 40))
    s = root.snapshot
    if authorize:
        claim = commit(
            backend,
            plan_lineage_dispatch_claim(
                s,
                effect_lineage_id=root.result["effect_lineage_id"],
                operation_id=op,
                generation=0,
                effect_key=root.result["semantic_effect_key"],
                occurred_at=NOW,
                trusted_context_digest=TRUST,
            ),
        )
        s = claim.snapshot
        s = commit(
            backend,
            plan_lineage_authorize_launch(
                s,
                effect_lineage_id=root.result["effect_lineage_id"],
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
        ).snapshot
        if lookup_not_launched:
            s = commit(
                backend,
                plan_launch_lookup(
                    s,
                    operation_id=op,
                    generation=0,
                    external_dispatch_key_value=root.result["external_dispatch_key"],
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
    return backend, blocked.snapshot, op, root, blocked


def validate_candidate_block_and_safe_never_authorized_resolution():
    backend, s, op, root, blocked = _root_and_blocked()
    k1 = blocked.result["proposed_semantic_effect_key"]
    require(blocked.result["status"] == "BLOCKED", "candidate B was not blocked")
    require(s.get(reservation_path(k1)) is None and member_lineage(s, k1) is None, "blocked candidate B received external identity")
    require(rebuild_lineage_projection(s, root.result["effect_lineage_id"])["predecessor_state"] == "NEVER_AUTHORIZED", "never-authorized state lost")
    facts = {
        "not-launched": {
            "type": "EXTERNAL_NOT_LAUNCHED",
            "external_dispatch_key": root.result["external_dispatch_key"],
            "observed_at": NOW,
            "source_digest": "trusted-external-lookup",
        }
    }
    verifier = evidence_verifier(facts)
    feature, _ = feature_truth(candidate="b" * 40)
    resolved = commit(
        backend,
        resolve_plan(
            s,
            lineage_id=root.result["effect_lineage_id"],
            predecessor_key=root.result["semantic_effect_key"],
            predecessor_external=root.result["external_dispatch_key"],
            operation_id=op,
            feature=feature,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence_refs=["not-launched"],
            verifier=verifier,
        ),
    )
    require(resolved.result["semantic_effect_key"] == k1, "safe never-authorized successor was not atomically activated")
    require(rebuild_projection(resolved.snapshot, op)["status"] == "RUNNING", "safe resolution did not clear lineage block")


def validate_stale_runner_authorized_not_launched():
    _backend, s, op, root, blocked = _root_and_blocked(authorize=True, lookup_not_launched=True)
    k1 = blocked.result["proposed_semantic_effect_key"]
    require(blocked.result["predecessor_state"] == "AUTHORIZED_NOT_LAUNCHED_OBSERVED", "authorized + NOT_LAUNCHED state collapsed")
    require(s.get(reservation_path(k1)) is None, "K1 reservation exists during stale-runner window")
    facts = {
        "not-launched": {
            "type": "EXTERNAL_NOT_LAUNCHED",
            "external_dispatch_key": root.result["external_dispatch_key"],
            "observed_at": NOW,
            "source_digest": "trusted-external-lookup",
        }
    }
    feature, _ = feature_truth(candidate="b" * 40)
    expect_code(
        "AUTHORIZED_EFFECT_STILL_EXECUTABLE",
        lambda: resolve_plan(
            s,
            lineage_id=root.result["effect_lineage_id"],
            predecessor_key=root.result["semantic_effect_key"],
            predecessor_external=root.result["external_dispatch_key"],
            operation_id=op,
            feature=feature,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence_refs=["not-launched"],
            verifier=evidence_verifier(facts),
        ),
    )
    require(s.get(reservation_path(k1)) is None, "failed stale-runner resolution materialized K1")
    authorized = {
        event["payload"].get("external_dispatch_key")
        for event in operation_events(s, op)
        if event["event_type"] == "dispatch.launch.authorized"
    }
    require(authorized == {root.result["external_dispatch_key"]}, "both predecessor and successor became launch-authorized")


def validate_resolution_fresh_truth_and_strong_evidence():
    _backend, s, op, root, blocked = _root_and_blocked()
    lineage_id = root.result["effect_lineage_id"]
    k1 = blocked.result["proposed_semantic_effect_key"]
    facts = {
        "not-launched": {
            "type": "EXTERNAL_NOT_LAUNCHED",
            "external_dispatch_key": root.result["external_dispatch_key"],
            "observed_at": NOW,
            "source_digest": "trusted-external-lookup",
        },
        "fake-fence": {
            "type": "EXTERNAL_KEY_INVALIDATED",
            "external_dispatch_key": root.result["external_dispatch_key"],
            "fence_receipt": "caller-shaped-fake",
        },
        "fake-scope": {"type": "NON_OVERLAPPING_SCOPE", "proof_digest": "caller-shaped-fake"},
    }
    verifier = evidence_verifier(facts)

    changed_candidate, _ = feature_truth(candidate="c" * 40)
    expect_code(
        "STALE_RESOLUTION",
        lambda: resolve_plan(
            s,
            lineage_id=lineage_id,
            predecessor_key=root.result["semantic_effect_key"],
            predecessor_external=root.result["external_dispatch_key"],
            operation_id=op,
            feature=changed_candidate,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence_refs=["not-launched"],
            verifier=verifier,
        ),
    )
    changed_ref, _ = feature_truth(candidate="b" * 40, target_ref="feature/other-ref")
    expect_code(
        "STALE_RESOLUTION",
        lambda: resolve_plan(
            s,
            lineage_id=lineage_id,
            predecessor_key=root.result["semantic_effect_key"],
            predecessor_external=root.result["external_dispatch_key"],
            operation_id=op,
            feature=changed_ref,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence_refs=["not-launched"],
            verifier=verifier,
        ),
    )
    current, _ = feature_truth(candidate="b" * 40)
    expect_code(
        "STALE_RESOLUTION",
        lambda: resolve_plan(
            s,
            lineage_id=lineage_id,
            predecessor_key=root.result["semantic_effect_key"],
            predecessor_external=root.result["external_dispatch_key"],
            operation_id=op,
            feature=current,
            proposal_id=blocked.result["proposal_id"],
            proposed_key=k1,
            choice="PROVE_NOT_LAUNCHED",
            evidence_refs=["not-launched"],
            verifier=verifier,
            auth=authority(profile_digest="changed-profile-policy"),
        ),
    )
    for ref in ("fake-fence", "fake-scope"):
        expect_code(
            "INSUFFICIENT_EVIDENCE",
            lambda ref=ref: resolve_plan(
                s,
                lineage_id=lineage_id,
                predecessor_key=root.result["semantic_effect_key"],
                predecessor_external=root.result["external_dispatch_key"],
                operation_id=op,
                feature=current,
                proposal_id=blocked.result["proposal_id"],
                proposed_key=k1,
                choice="RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN",
                evidence_refs=[ref],
                verifier=verifier,
            ),
        )


def validate_cas_race_generation_and_projection_rebuild():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, op = start(backend, "F-LINEAGE-TEST")
    root = commit(backend, gate(s, operation_id=op, feature="F-LINEAGE-TEST", candidate="a" * 40))
    stale_root = root.snapshot
    b_plan = gate(stale_root, operation_id=op, feature="F-LINEAGE-TEST", candidate="b" * 40)
    b = commit(backend, b_plan)
    c_plan = gate(
        StoreSnapshot(ref_sha=stale_root.ref_sha, files=dict(stale_root.files)),
        operation_id=op,
        feature="F-LINEAGE-TEST",
        candidate="c" * 40,
        task_identity="vertical:code-review:" + "c" * 40,
    )
    try:
        backend.commit(c_plan, receipt())
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
    require(c.result["status"] == "BLOCKED", "CAS re-plan bypassed predecessor")
    require(c.snapshot.get(reservation_path(b.result["proposed_semantic_effect_key"])) is None, "candidate B unexpectedly activated")
    require(c.snapshot.get(reservation_path(c.result["proposed_semantic_effect_key"])) is None, "candidate C unexpectedly activated")
    lineage_id = root.result["effect_lineage_id"]
    projection = rebuild_lineage_projection(c.snapshot, lineage_id)
    without_cache = StoreSnapshot(
        ref_sha=c.snapshot.ref_sha,
        files={k: v for k, v in c.snapshot.files.items() if k != lineage_projection_path(lineage_id)},
    )
    require(rebuild_lineage_projection(without_cache, lineage_id) == projection, "lineage projection did not rebuild from immutable facts")
    takeover = commit(backend, plan_takeover(c.snapshot, operation_id=op, occurred_at=NOW, trusted_context_digest=TRUST))
    require(rebuild_projection(takeover.snapshot, op)["status"] == "BLOCKED", "generation takeover cleared lineage block")
    after = gate(
        takeover.snapshot,
        operation_id=op,
        feature="F-LINEAGE-TEST",
        candidate="c" * 40,
        task_identity="vertical:code-review:" + "c" * 40,
    )
    require(after.result["effect_lineage_id"] == lineage_id and after.result["status"] == "BLOCKED", "takeover manufactured a fresh lineage")


def _legacy_reservation(backend, *, feature_id="F-LEGACY", rev=3, stage="implementation", role="developer", candidate=None, task_identity=None):
    s, op = start(backend, feature_id, rev=rev)
    task_identity = task_identity or f"vertical:implementation:{rev}"
    result = commit(
        backend,
        plan_semantic_reservation(
            s,
            operation_id=op,
            generation=0,
            target_repository=REPO,
            feature_id=feature_id,
            expected_revision=rev,
            current_stage=stage,
            task_identity=task_identity,
            role=role,
            candidate_head_sha=candidate,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    return result.snapshot, op, result.result["semantic_effect_key"]


def validate_legacy_reconstruction():
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s, _op, key = _legacy_reservation(backend)
    feature, manifest = feature_truth("F-LEGACY", rev=3, stage="implementation", candidate=None)
    reconstructed = reconstruct_legacy_lineage(s, semantic_effect_key=key, trusted_feature=feature, trusted_manifest=manifest)
    attached = commit(
        backend,
        plan_legacy_lineage_attachment(
            s,
            semantic_effect_key=key,
            trusted_feature=feature,
            trusted_manifest=manifest,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    require(attached.result["status"] == "ATTACHED", "trusted durable legacy reconstruction did not attach")
    restarted = StoreSnapshot(ref_sha=s.ref_sha, files=dict(s.files))
    reconstructed2 = reconstruct_legacy_lineage(restarted, semantic_effect_key=key, trusted_feature=feature, trusted_manifest=manifest)
    require(reconstructed2.provenance_digest == reconstructed.provenance_digest, "legacy reconstruction changed across restart")

    backend2 = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    bad, _op, bad_key = _legacy_reservation(backend2, task_identity="caller:chosen:new-lineage")
    blocked = plan_legacy_lineage_attachment(
        bad,
        semantic_effect_key=bad_key,
        trusted_feature=feature,
        trusted_manifest=manifest,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )
    require(blocked.result["reason"] == "LEGACY_UNRESOLVED_LINEAGE", "wrong legacy work slot did not fail closed")

    candidate = "a" * 40
    missing_task_identity = f"vertical:code-remediation:F-MISSING:{candidate}"
    backend3 = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    bad3, _op, key3 = _legacy_reservation(
        backend3,
        feature_id="F-LEGACY-REMEDIATION",
        stage="code-review",
        role="developer",
        candidate=candidate,
        task_identity=missing_task_identity,
    )
    f3, m3 = feature_truth("F-LEGACY-REMEDIATION", rev=3, stage="code-review", candidate=candidate, tasks=[])
    blocked3 = plan_legacy_lineage_attachment(
        bad3,
        semantic_effect_key=key3,
        trusted_feature=f3,
        trusted_manifest=m3,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )
    require(blocked3.result["reason"] == "LEGACY_UNRESOLVED_LINEAGE", "wrong remediation task id did not fail closed")

    incomplete = StoreSnapshot(
        ref_sha=s.ref_sha,
        files={k: v for k, v in s.files.items() if "/operations/" not in k},
    )
    blocked4 = plan_legacy_lineage_attachment(
        incomplete,
        semantic_effect_key=key,
        trusted_feature=feature,
        trusted_manifest=manifest,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )
    require(blocked4.result["reason"] == "LEGACY_UNRESOLVED_LINEAGE", "incomplete Operation history did not fail closed")

    backend4 = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    s4, op4 = start(backend4, "F-LEGACY-REVIEW", rev=3)
    keys = []
    for candidate in ("a" * 40, "b" * 40):
        row = commit(
            backend4,
            plan_semantic_reservation(
                backend4.read_snapshot(),
                operation_id=op4,
                generation=0,
                target_repository=REPO,
                feature_id="F-LEGACY-REVIEW",
                expected_revision=3,
                current_stage="code-review",
                task_identity=f"vertical:code-review:{candidate}",
                role="reviewer",
                candidate_head_sha=candidate,
                occurred_at=NOW,
                trusted_context_digest=TRUST,
            ),
        )
        keys.append(row.result["semantic_effect_key"])
    f4a, m4 = feature_truth("F-LEGACY-REVIEW", rev=3, stage="code-review", candidate="a" * 40)
    first = commit(
        backend4,
        plan_legacy_lineage_attachment(
            backend4.read_snapshot(),
            semantic_effect_key=keys[0],
            trusted_feature=f4a,
            trusted_manifest=m4,
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    f4b = FeatureSnapshot.from_manifest(
        repository=REPO,
        target_ref="feature/F-LEGACY-REVIEW",
        manifest=m4,
        candidate_pr_number=1,
        candidate_head_sha="b" * 40,
    )
    second = plan_legacy_lineage_attachment(
        first.snapshot,
        semantic_effect_key=keys[1],
        trusted_feature=f4b,
        trusted_manifest=m4,
        occurred_at=NOW,
        trusted_context_digest=TRUST,
    )
    require(second.result["reason"] == "LEGACY_UNRESOLVED_LINEAGE", "competing plausible legacy lineage member was attached")


def verified_rollout(*, fence_state="QUIESCED"):
    receipt_doc = {
        "schema_version": WRITER_FENCE_SCHEMA,
        "receipt_id": "writer-fence-1",
        "repository": REPO.lower(),
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "state": fence_state,
        "fenced_capabilities": sorted(REQUIRED_FENCED_CAPABILITIES),
        "issued_at": NOW,
        "issuer": "trusted-installation-controller",
    }
    policy = {
        "schema_version": ROLLOUT_SCHEMA,
        "repository": REPO.lower(),
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "effect_lineage_required": True,
        "writer_capability": "lineage-aware-v1",
        "writer_fence_receipt_ref": "protected://writer-fences/fence-1",
        "policy_ref": "default-branch://operator/effect-lineage-rollout-v1",
    }
    policy["policy_digest"] = digest_json(policy)
    verifier = ProtectedEffectLineageRolloutVerifier(
        policy_loader=lambda repository, state_ref, operation_profile: dict(policy),
        writer_fence_receipt_loader=lambda ref: dict(receipt_doc),
    )
    return verifier.verify(repository=REPO, state_ref=STATE_REF, operation_profile=VERTICAL_PROFILE)


def validate_rollout_and_active_old_writer_fence():
    try:
        verified_rollout(fence_state="RUNNING")
        raise AssertionError("unverified old-writer state enabled Effect Lineage rollout")
    except StoreCommandError as exc:
        require(exc.code == "MIXED_WRITER_FENCED", "wrong unquiesced rollout failure")

    rollout = verified_rollout()
    backend = MemoryStateRefBackend(repository=REPO, state_ref=STATE_REF)
    runtime = OperatorStoreRuntime(
        backend=backend,
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: NOW,
        plan_guard=EffectLineageWriteFence(rollout),
    )
    started = runtime.commit_replanned(
        lambda snapshot: plan_operation_start(
            snapshot,
            target_repository=REPO,
            feature_id="F-FENCE",
            expected_revision=1,
            idempotency_key="fence-start",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            operation_profile=VERTICAL_PROFILE,
        )
    )
    op = started.result["operation_id"]
    expect_backend_code(
        "MIXED_WRITER_FENCED",
        lambda: runtime.commit_replanned(
            lambda snapshot: plan_semantic_reservation(
                snapshot,
                operation_id=op,
                generation=0,
                target_repository=REPO,
                feature_id="F-FENCE",
                expected_revision=1,
                current_stage="code-review",
                task_identity="vertical:code-review:" + "a" * 40,
                role="reviewer",
                candidate_head_sha="a" * 40,
                occurred_at=NOW,
                trusted_context_digest=TRUST,
            )
        ),
    )

    root = commit(backend, gate(backend.read_snapshot(), operation_id=op, feature="F-FENCE", rev=1, candidate="a" * 40))
    expect_backend_code(
        "MIXED_WRITER_FENCED",
        lambda: runtime.commit_replanned(
            lambda snapshot: plan_dispatch_claim(
                snapshot,
                operation_id=op,
                generation=0,
                effect_key=root.result["semantic_effect_key"],
                occurred_at=NOW,
                trusted_context_digest=TRUST,
            )
        ),
    )
    claim = commit(
        backend,
        plan_lineage_dispatch_claim(
            backend.read_snapshot(),
            effect_lineage_id=root.result["effect_lineage_id"],
            operation_id=op,
            generation=0,
            effect_key=root.result["semantic_effect_key"],
            occurred_at=NOW,
            trusted_context_digest=TRUST,
        ),
    )
    expect_backend_code(
        "MIXED_WRITER_FENCED",
        lambda: runtime.commit_replanned(
            lambda snapshot: plan_authorize_launch(
                snapshot,
                operation_id=op,
                generation=0,
                claim_id=claim.result["claim_id"],
                dispatch_id="old-writer-launch",
                occurred_at=NOW,
                trusted_context_digest=TRUST,
                verified_expected_revision=1,
                verified_stage="code-review",
                verified_candidate_head_sha="a" * 40,
            )
        ),
    )
    allowed = runtime.commit_replanned(
        lambda snapshot: plan_lineage_authorize_launch(
            snapshot,
            effect_lineage_id=root.result["effect_lineage_id"],
            operation_id=op,
            generation=0,
            claim_id=claim.result["claim_id"],
            dispatch_id="lineage-writer-launch",
            occurred_at=NOW,
            trusted_context_digest=TRUST,
            verified_expected_revision=1,
            verified_stage="code-review",
            verified_candidate_head_sha="a" * 40,
        )
    )
    require(allowed.result.get("status") == "WAITING_EXTERNAL", "lineage-aware authorization was fenced")


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
    s, op = start(backend, "F-SCHEMA")
    root = commit(backend, gate(s, operation_id=op, feature="F-SCHEMA"))
    lid = root.result["effect_lineage_id"]
    schemas["lineage-anchor"].validate(root.snapshot.get(anchor_path(lid)))
    for path, value in root.snapshot.files.items():
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
            operation_profile=VERTICAL_PROFILE,
            trusted_profile_digest=PROFILE_DIGEST,
        )
        raise AssertionError("FORCE_RETRY authority expansion was accepted")
    except ValueError:
        pass
    for bypass in ("IGNORE_UNKNOWN", "DROP_RESERVATION", "NEW_KEY_ANYWAY"):
        require(bypass not in ALLOWED_RESOLUTION_CHOICES, f"forbidden bypass leaked into allowed choices: {bypass}")


def main():
    validate_identity_stability()
    validate_candidate_block_and_safe_never_authorized_resolution()
    validate_stale_runner_authorized_not_launched()
    validate_resolution_fresh_truth_and_strong_evidence()
    validate_cas_race_generation_and_projection_rebuild()
    validate_legacy_reconstruction()
    validate_rollout_and_active_old_writer_fence()
    validate_schemas_and_bypass_rejection()
    validate_release_contract()
    print("Operator Effect Lineage validation passed")


if __name__ == "__main__":
    main()
