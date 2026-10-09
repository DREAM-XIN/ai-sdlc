#!/usr/bin/env python3
"""Focused fail-closed checks for the v0.3 real-dogfood production composition."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from operator_openai_responses import ADAPTER_ID
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from v03_dogfood_fixture_pool import require_slot
from v03_dogfood_runtime_preflight import _execution_bindings
from v03_dogfood_full_composition import (
    DogfoodCandidateHandoff,
    DogfoodCandidateBoundActionsTransport,
    DogfoodExecutionBoundDispatchGateway,
    DogfoodGitHubCandidateProvider,
    DogfoodTrustedCallbackCoordinator,
    V03DogfoodCompositionError,
    build_v03_dogfood_full_composition,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "v03_dogfood_full_composition.py"
REPOSITORY = "dream-xin/ai-sdlc"
HEAD = "1" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _pr(slot, *, repository=REPOSITORY, head=HEAD, draft=False, state="open"):
    return {
        "number": 431,
        "state": state,
        "draft": draft,
        "head": {"ref": slot.target_ref, "sha": head, "repo": {"full_name": repository}},
        "base": {"ref": "main", "repo": {"full_name": repository}},
    }


def candidate_tests() -> None:
    slot = require_slot("happy_path")
    calls = []

    def get(url, headers):
        calls.append((url, headers))
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        require(query.get("state") == ["open"], "candidate lookup did not request open PRs")
        require(query.get("base") == ["main"], "candidate lookup escaped main base")
        require(query.get("head") == [f"dream-xin:{slot.target_ref}"], "candidate lookup escaped fixed head")
        return 200, [_pr(slot)]

    provider = DogfoodGitHubCandidateProvider(
        slot=slot,
        repository=REPOSITORY,
        token="candidate-test-token",
        http_get=get,
    )
    candidate = provider.current_candidate(
        operation_id="op-dogfood-preflight",
        repository=REPOSITORY,
        feature_id=slot.feature_id,
        target_ref=slot.target_ref,
    )
    require(candidate.candidate_pr_number == 431 and candidate.candidate_head_sha == HEAD, "candidate authority changed")
    require(len(calls) == 1, "candidate provider performed unexpected reads")

    for label, row in (
        ("cross-repository head", _pr(slot, repository="dream-xin/other")),
        ("draft PR", _pr(slot, draft=True)),
        ("closed PR", _pr(slot, state="closed")),
        ("invalid head", _pr(slot, head="bad")),
    ):
        bad = DogfoodGitHubCandidateProvider(
            slot=slot,
            repository=REPOSITORY,
            token="candidate-test-token",
            http_get=lambda _url, _headers, row=row: (200, [row]),
        )
        try:
            bad.current_candidate(
                operation_id="op-dogfood-preflight",
                repository=REPOSITORY,
                feature_id=slot.feature_id,
                target_ref=slot.target_ref,
            )
        except V03DogfoodCompositionError:
            pass
        else:
            raise AssertionError(f"{label} unexpectedly gained dogfood candidate authority")

    try:
        provider.current_candidate(
            operation_id="op-dogfood-preflight",
            repository=REPOSITORY,
            feature_id="F-OTHER",
            target_ref=slot.target_ref,
        )
    except V03DogfoodCompositionError:
        pass
    else:
        raise AssertionError("candidate lookup escaped fixed Feature identity")


def handoff_and_supersession_tests() -> None:
    import hashlib
    from copy import deepcopy
    from operator_store import (
        plan_operation_start, plan_dispatch_claim, plan_authorize_launch, plan_launch_lookup,
    )
    from operator_store_backends import OperatorStoreRuntime
    from operator_effect_rollout import EffectLineageWriteFence, VerifiedEffectLineageRollout
    from operator_store_git import MemoryStateRefBackend, CommitResult, CasConflict
    from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, operation_events, rebuild_projection
    from operator_store_protection import StaticProtectionVerifier, PROTECTED
    from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_recovery import plan_vertical_callback_record
    from operator_vertical_store import (
        plan_vertical_semantic_reservation, plan_vertical_persist_requested,
        plan_vertical_persist_linearized, plan_vertical_persist_confirmed,
    )
    from v03_dogfood_full_composition import (
        _handoff_paths, _handoff_binding, _plan_handoff_intent, read_dogfood_handoff,
    )

    slot = require_slot("review_remediation")
    prior, developer, persisted = "1" * 40, "2" * 40, "3" * 40
    now = "2026-10-03T00:00:00Z"
    uri = f"docs/features/{slot.feature_id}/worker-runs/vertical-dispatch-1/developer-pr-900-{developer}.json"
    artifact_bytes = b'{"authenticated":"Developer-output"}'
    receipts = [{
        "kind": "artifact", "label": "implementation", "trusted_uri": uri,
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(), "size_bytes": len(artifact_bytes),
    }]

    class ShaMemoryBackend(MemoryStateRefBackend):
        """Real protected Memory backend; only fake commit IDs use Git's SHA shape."""
        compete_once = False
        def commit(self, plan, receipt):
            competing = self.compete_once and any(m.path.endswith("/intent.json") for m in plan.mutations)
            self.compete_once = False if competing else self.compete_once
            result = super().commit(plan, receipt)
            sha = hashlib.sha1(("handoff-test:" + result.ref_sha).encode()).hexdigest()
            self.snapshot = StoreSnapshot(ref_sha=sha, files=result.snapshot.files)
            if competing:
                raise CasConflict("another writer won the identical handoff intent")
            return CommitResult(sha, self.read_snapshot(), result.result)

    def fixture():
        snapshot = StoreSnapshot(ref_sha="a" * 40)
        sequence = 0
        def apply(plan):
            nonlocal snapshot, sequence
            sequence += 1
            snapshot = apply_plan_to_snapshot(snapshot, plan, new_ref_sha=f"{sequence:040x}")
            return plan.result
        start = apply(plan_operation_start(
            snapshot, target_repository=REPOSITORY, feature_id=slot.feature_id,
            expected_revision=10, idempotency_key="handoff-test", occurred_at=now,
            trusted_context_digest="trusted", operation_profile=VERTICAL_PROFILE,
        ))
        operation_id = start["operation_id"]
        reservation = apply(plan_vertical_semantic_reservation(
            snapshot, operation_id=operation_id, generation=0, target_repository=REPOSITORY,
            feature_id=slot.feature_id, expected_revision=10, current_stage="implementation",
            task_identity="vertical:implementation:10", role="developer", candidate_head_sha=prior,
            occurred_at=now, trusted_context_digest="trusted",
        ))
        effect_key = reservation["semantic_effect_key"]
        claim = apply(plan_dispatch_claim(
            snapshot, operation_id=operation_id, generation=0, effect_key=effect_key,
            occurred_at=now, trusted_context_digest="trusted",
        ))
        apply(plan_authorize_launch(
            snapshot, operation_id=operation_id, generation=0, claim_id=claim["claim_id"],
            dispatch_id="vertical-dispatch-1", occurred_at=now, trusted_context_digest="trusted",
            verified_expected_revision=10, verified_stage="implementation", verified_candidate_head_sha=prior,
        ))
        apply(plan_launch_lookup(
            snapshot, operation_id=operation_id, generation=0,
            external_dispatch_key_value=claim["external_dispatch_key"], lookup_state="LAUNCHED",
            receipt_id="9001", occurred_at=now, trusted_context_digest="trusted",
        ))
        context = TrustedDispatchContext(
            operation_id=operation_id, operation_generation=0, operation_profile=VERTICAL_PROFILE,
            semantic_effect_key=effect_key, external_dispatch_key=claim["external_dispatch_key"],
            dispatch_id="vertical-dispatch-1", runtime_receipt_identity="9001",
            target_repository=REPOSITORY, target_ref=slot.target_ref, feature_id=slot.feature_id,
            expected_revision=10, feature_stage="implementation", task_id="vertical:implementation:10",
            role="developer", candidate_pr_number=None, candidate_head_sha=prior,
            worker_identity="developer-worker", collector_identity="collector",
        )
        apply(plan_vertical_callback_record(
            snapshot, context=context, callback_id="callback-1",
            worker_payload={"status": "COMPLETED", "summary": "done",
                            "outputs": [{"label": "implementation", "kind": "artifact"}]},
            receipts=receipts, occurred_at=now, trusted_context_digest="trusted",
        ))
        backend = ShaMemoryBackend(repository=REPOSITORY, state_ref="refs/heads/handoff-test", snapshot=snapshot)
        runtime = OperatorStoreRuntime(
            backend=backend, protection_verifier=StaticProtectionVerifier(status=PROTECTED), clock=lambda: now,
            plan_guard=EffectLineageWriteFence(VerifiedEffectLineageRollout(
                repository=REPOSITORY, state_ref=backend.state_ref, operation_profile=VERTICAL_PROFILE,
                effect_lineage_required=True, policy_ref="fixture-policy", policy_digest="fixture-policy-digest",
                writer_capability="lineage-aware-v1", writer_fence_receipt_ref="fixture-fence",
                writer_fence_receipt_digest="fixture-fence-digest", test_only=True,
            )),
        )
        executor = TrustedVerticalExecutor(
            runtime=runtime, feature_gateway=None, persist_gateway=None, dispatch_gateway=None,
            config=TrustedVerticalExecutorConfig(
                target_ref=slot.target_ref, trusted_context_digest="trusted", legacy_compatibility_mode=True,
            ),
        )
        state = {"ref": prior, "patches": 0, "ack_loss": False, "crash_after_patch": False,
                 "post_patch_read_failure": False, "facts": [], "persisted": []}
        provider = DogfoodGitHubCandidateProvider(
            slot=slot, repository=REPOSITORY, token="read-token",
            http_get=lambda _url, _headers: (200, [_pr(slot, head=state["ref"])]),
        )
        provider.bind_runtime(runtime)
        def http(method, url, _headers, body):
            if method == "GET" and url.endswith("/pulls/900"):
                return 200, {
                    "number": 900, "state": "open", "draft": True,
                    "base": {"ref": slot.target_ref, "repo": {"full_name": REPOSITORY}},
                    "head": {"sha": developer, "repo": {"full_name": REPOSITORY}},
                }
            if method == "GET" and f"/compare/{prior}...{developer}" in url:
                return 200, {"status": "ahead", "ahead_by": 1, "behind_by": 0,
                             "merge_base_commit": {"sha": prior}}
            if method == "GET" and "/git/refs/heads/" in url:
                if state["post_patch_read_failure"]:
                    state["post_patch_read_failure"] = False
                    return 0, {}
                return 200, {"object": {"sha": state["ref"]}}
            if method == "PATCH" and "/git/refs/heads/" in url:
                intent_path, applied_path = _handoff_paths(operation_id, "callback-1")
                durable = backend.read_snapshot()
                require(durable.get(intent_path) is not None and durable.get(applied_path) is None,
                        "PATCH preceded durable intent or repeated an applied handoff")
                require(body == {"sha": developer, "force": False}, "handoff ref update escaped exact non-force output")
                state["patches"] += 1
                state["ref"] = developer
                if state["crash_after_patch"]:
                    state["post_patch_read_failure"] = True
                return (0, {}) if state["ack_loss"] else (200, {"object": {"sha": developer}})
            raise AssertionError(f"unexpected handoff request: {method} {url}")
        handoff = DogfoodCandidateHandoff(
            slot=slot, repository=REPOSITORY, token="write-token",
            candidate_provider=provider, http_request=http,
        )
        handoff.content_loader = lambda requested: artifact_bytes if requested == uri else b"wrong"
        return SimpleNamespace(
            backend=backend, runtime=runtime, executor=executor, context=context, state=state,
            handoff=handoff, provider=provider, operation_id=operation_id,
        )

    def adopt(f):
        f.handoff.adopt(executor=f.executor, context=f.context, callback_id="callback-1", receipts=receipts)

    def candidate(f):
        return f.provider.current_candidate(
            operation_id=f.operation_id, repository=REPOSITORY,
            feature_id=slot.feature_id, target_ref=slot.target_ref,
        )

    def denied(action, message):
        try:
            action()
        except V03DogfoodCompositionError:
            return
        raise AssertionError(message)

    f = fixture()
    journal_before = deepcopy(operation_events(f.backend.read_snapshot(), f.operation_id))
    adopt(f)
    durable = f.backend.read_snapshot()
    fact = read_dogfood_handoff(durable, f.operation_id, "callback-1", require_applied=True)
    require(f.state["patches"] == 1 and f.state["ref"] == developer, "handoff did not adopt exact output once")
    require(fact["intent"]["source_candidate_head_sha"] == developer, "handoff output identity missing")
    require(operation_events(durable, f.operation_id) == journal_before, "handoff polluted original Operation journal")
    require(rebuild_projection(durable, f.operation_id)["status"] == "RUNNING", "real projection rejected sidecar facts")
    require(candidate(f).candidate_head_sha == prior, "pending callback lost prior candidate view")
    adopt(f)
    require(f.state["patches"] == 1, "applied replay issued a second PATCH")

    # Preserve original provider completion coverage using real supported Store events.
    f.executor._record_fact(f.operation_id, "worker.result.validated",
        {"callback_id": "callback-1", "role": "developer", "dispatch_id": "vertical-dispatch-1"})
    f.executor._record_fact(f.operation_id, "feature.event.translated",
        {"callback_id": "callback-1", "feature_event_id": "EVT-1"})
    common = dict(operation_id=f.operation_id, generation=0, feature_event_id="EVT-1",
                  expected_revision=10, target_ref=slot.target_ref, candidate_head_sha=prior,
                  occurred_at=now, trusted_context_digest="trusted")
    f.runtime.commit_replanned(lambda snap: plan_vertical_persist_requested(snap, **common))
    f.runtime.commit_replanned(lambda snap: plan_vertical_persist_linearized(snap, **common))
    f.runtime.commit_replanned(lambda snap: plan_vertical_persist_confirmed(snap, result_revision=11, **common))
    f.state["ref"] = persisted
    require(candidate(f).candidate_head_sha == persisted, "confirmed handoff remained pinned to prior")

    ack = fixture()
    ack.state["ack_loss"] = True
    adopt(ack)
    require(ack.state["patches"] == 1, "PATCH acknowledgement loss repeated mutation")
    read_dogfood_handoff(ack.backend.read_snapshot(), ack.operation_id, "callback-1", require_applied=True)

    crash = fixture()
    crash.state["crash_after_patch"] = True
    denied(lambda: adopt(crash), "post-PATCH read failure unexpectedly sealed application")
    pending = read_dogfood_handoff(crash.backend.read_snapshot(), crash.operation_id, "callback-1")
    require(pending["applied"] is None and crash.state["patches"] == 1, "ambiguous PATCH lost pending intent")
    require(candidate(crash).candidate_head_sha == prior, "intent-only replay cannot recover provider view")
    crash.state["crash_after_patch"] = False
    adopt(crash)
    require(crash.state["patches"] == 1, "source-head replay issued a second PATCH")
    read_dogfood_handoff(crash.backend.read_snapshot(), crash.operation_id, "callback-1", require_applied=True)

    prepatch = fixture()
    prepatch.runtime.commit_replanned(lambda snap: _plan_handoff_intent(
        snap, binding=_handoff_binding(snap, prepatch.operation_id, "callback-1", 431)))
    denied(lambda: adopt(prepatch), "existing intent with prior ref gained retry authority")
    require(prepatch.state["patches"] == 0, "pre-PATCH crash replay sent a PATCH")

    loser = fixture()
    loser.backend.compete_once = True
    denied(lambda: adopt(loser), "CAS loser gained original winner mutation authority")
    require(loser.state["patches"] == 0, "CAS loser sent a PATCH")

    # Distinct callback IDs cannot independently adopt against one unresolved fixture handoff.
    competing_callback = fixture()
    competing_callback.runtime.commit_replanned(lambda snap: _plan_handoff_intent(
        snap, binding=_handoff_binding(snap, competing_callback.operation_id, "callback-1", 431)))
    competing_callback.runtime.commit_replanned(lambda snap: plan_vertical_callback_record(
        snap, context=competing_callback.context, callback_id="callback-2",
        worker_payload={"status": "COMPLETED", "summary": "done",
                        "outputs": [{"label": "implementation", "kind": "artifact"}]},
        receipts=receipts, occurred_at=now, trusted_context_digest="trusted",
    ))
    before_second = competing_callback.backend.read_snapshot()
    denied(lambda: competing_callback.handoff.adopt(
        executor=competing_callback.executor, context=competing_callback.context,
        callback_id="callback-2", receipts=receipts),
        "different callback ID bypassed the unresolved same-ref handoff")
    after_second = competing_callback.backend.read_snapshot()
    second_intent, second_applied = _handoff_paths(competing_callback.operation_id, "callback-2")
    require(competing_callback.state["patches"] == 0, "competing callback sent a PATCH")
    require(after_second.get(second_intent) is None and after_second.get(second_applied) is None,
            "competing callback created sidecar authority")
    require(after_second.files == before_second.files and after_second.ref_sha == before_second.ref_sha,
            "rejected competing callback changed protected Store")
    require(rebuild_projection(after_second, competing_callback.operation_id)["status"] == "RUNNING",
            "valid two-callback fixture damaged the shared projection")

    # The completed first handoff must not permanently fence a later Developer round.
    # Seed a distinct, valid later launch using the real planners; sidecar admission
    # itself still executes through the protected runtime and active write guard.
    from dataclasses import replace
    sequential_snapshot = f.backend.read_snapshot()
    sequential_step = 100
    first_completed = read_dogfood_handoff(
        sequential_snapshot, f.operation_id, "callback-1", require_applied=True)
    require(any(e["event_type"] == "persist.confirmed" and e["payload"]["feature_event_id"] == "EVT-1"
                for e in operation_events(sequential_snapshot, f.operation_id)),
            "sequential admission fixture lacks exact predecessor Persist confirmation")
    def sequential_apply(plan):
        nonlocal sequential_snapshot, sequential_step
        sequential_step += 1
        sequential_snapshot = apply_plan_to_snapshot(
            sequential_snapshot, plan, new_ref_sha=f"{sequential_step:040x}")
        return plan.result
    second_reservation = sequential_apply(plan_vertical_semantic_reservation(
        sequential_snapshot, operation_id=f.operation_id, generation=0, target_repository=REPOSITORY,
        feature_id=slot.feature_id, expected_revision=11, current_stage="implementation",
        task_identity="vertical:implementation:11", role="developer", candidate_head_sha=developer,
        occurred_at=now, trusted_context_digest="trusted"))
    second_claim = sequential_apply(plan_dispatch_claim(
        sequential_snapshot, operation_id=f.operation_id, generation=0,
        effect_key=second_reservation["semantic_effect_key"],
        occurred_at=now, trusted_context_digest="trusted"))
    sequential_apply(plan_authorize_launch(
        sequential_snapshot, operation_id=f.operation_id, generation=0, claim_id=second_claim["claim_id"],
        dispatch_id="vertical-dispatch-2", occurred_at=now, trusted_context_digest="trusted",
        verified_expected_revision=11, verified_stage="implementation", verified_candidate_head_sha=developer))
    sequential_apply(plan_launch_lookup(
        sequential_snapshot, operation_id=f.operation_id, generation=0,
        external_dispatch_key_value=second_claim["external_dispatch_key"], lookup_state="LAUNCHED",
        receipt_id="9002", occurred_at=now, trusted_context_digest="trusted"))
    second_context = replace(
        f.context, semantic_effect_key=second_reservation["semantic_effect_key"],
        external_dispatch_key=second_claim["external_dispatch_key"], dispatch_id="vertical-dispatch-2",
        runtime_receipt_identity="9002", expected_revision=11, task_id="vertical:implementation:11",
        candidate_head_sha=developer)
    second_uri = f"docs/features/{slot.feature_id}/worker-runs/vertical-dispatch-2/developer-pr-901-{persisted}.json"
    second_receipts = [dict(receipts[0], trusted_uri=second_uri)]
    sequential_apply(plan_vertical_callback_record(
        sequential_snapshot, context=second_context, callback_id="callback-next-round",
        worker_payload={"status": "COMPLETED", "summary": "second round done",
                        "outputs": [{"label": "implementation", "kind": "artifact"}]},
        receipts=second_receipts, occurred_at=now, trusted_context_digest="trusted"))
    f.backend.snapshot = sequential_snapshot
    admitted = f.runtime.commit_replanned(lambda snap: _plan_handoff_intent(
        snap, binding=_handoff_binding(snap, f.operation_id, "callback-next-round", 431))).result
    require(admitted["created"] is True, "completed predecessor permanently fenced next Developer round")
    require(admitted["intent"]["prior_candidate_head_sha"] == developer
            and admitted["intent"]["source_candidate_head_sha"] == persisted,
            "next round lost distinct candidate lineage")
    require(read_dogfood_handoff(f.backend.read_snapshot(), f.operation_id, "callback-1", require_applied=True)
            == first_completed, "next round rewrote first immutable handoff")
    require(rebuild_projection(f.backend.read_snapshot(), f.operation_id)["status"] == "RUNNING",
            "sequential sidecar admission damaged shared projection")

    divergent = fixture()
    divergent.runtime.commit_replanned(lambda snap: _plan_handoff_intent(
        snap, binding=_handoff_binding(snap, divergent.operation_id, "callback-1", 431)))
    divergent.state["ref"] = persisted
    denied(lambda: adopt(divergent), "unrelated live head gained handoff authority")
    denied(lambda: candidate(divergent), "unrelated live head was masked to prior")
    require(divergent.state["patches"] == 0, "unrelated head triggered PATCH")

    ambiguous = fixture()
    denied(lambda: ambiguous.handoff.adopt(
        executor=ambiguous.executor, context=ambiguous.context, callback_id="callback-1",
        receipts=receipts + receipts), "ambiguous Developer outputs gained handoff authority")
    require(ambiguous.state["patches"] == 0, "ambiguous outputs triggered PATCH")

    corrupt = fixture()
    corrupt.handoff.content_loader = lambda _uri: b"corrupt"
    denied(lambda: adopt(corrupt), "corrupt Developer artifact gained handoff authority")
    require(corrupt.state["patches"] == 0, "corrupt artifact triggered PATCH")
    require(read_dogfood_handoff(corrupt.backend.read_snapshot(), corrupt.operation_id, "callback-1") is None,
            "corrupt artifact created durable intent")

    state, handoff = f.state, f.handoff
    feature = SimpleNamespace(
        revision=7, current_stage="code-review", manifest_digest="manifest-digest",
        candidate_head_sha=persisted, target_ref=slot.target_ref,
    )
    manifest = {
        "tasks": [{"id": "remediation-1", "kind": "remediation", "status": "DONE"}],
        "artifacts": [
            {"id": "artifact-old", "type": "implementation", "status": "draft", "uri": "old-uri"},
            {"id": "artifact-new", "type": "implementation", "status": "draft", "uri": uri},
        ],
    }
    fake_executor = SimpleNamespace(
        feature_gateway=SimpleNamespace(
            read_feature=lambda **_kwargs: (feature, manifest),
        ),
        runtime=SimpleNamespace(clock=lambda: "2026-10-03T00:00:00Z"),
        _record_fact=lambda op, typ, payload: state["facts"].append((op, typ, payload)),
        _persist=lambda op, event, bound: state["persisted"].append((op, event, bound)),
    )
    coordinator = DogfoodTrustedCallbackCoordinator(
        delegate=SimpleNamespace(executor=fake_executor),
        candidate_handoff=handoff,
    )
    coordinator._supersede_remediation_artifact(
        context=SimpleNamespace(operation_id="op-handoff", task_id="remediation-1", feature_id=slot.feature_id),
        callback_id="callback-2",
        receipts=[{"kind": "artifact", "trusted_uri": uri}],
    )
    require(len(state["persisted"]) == 1, "remediation supersession did not enter protected Persist")
    changes = state["persisted"][0][1]["changes"]
    require(
        changes == [{"kind": "artifact", "id": "artifact-old", "status": "superseded"}],
        "remediation supersession did not preserve exactly one approvable replacement",
    )



def capture_post_handoff_authority_graph(responses):
    """Capture references before installation mutates the shared backend objects."""
    from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor
    bundle = responses.operator_bundle
    previous = bundle.executor
    require(isinstance(previous, FailureClassifyingTrustedRecoveringVerticalExecutor),
            "graph fixture lacks the real production recovering executor")
    runtime = responses.runtime
    collections = (bundle.backends, bundle.vertical_bundle.api_backends,
                   responses.backends, responses.adapter.backends)
    return {
        "responses": responses, "bundle": bundle, "vertical": bundle.vertical_bundle,
        "write_bundle": bundle.write_bundle, "read_bundle": bundle.write_bundle.read_bundle,
        "previous": previous, "base": previous.base, "runtime": runtime,
        "backend": runtime.backend, "protection_verifier": runtime.protection_verifier,
        "plan_guard": runtime.plan_guard, "persist_gateway": previous.base.persist_gateway,
        "dispatch_gateway": previous.base.dispatch_gateway, "feature_gateway": previous.base.feature_gateway,
        "config": previous.base.config, "resolution_policy_verifier": previous.base.resolution_policy_verifier,
        "callback_coordinator": bundle.callback_coordinator,
        "decision_coordinator": bundle.decision_notification_coordinator,
        "content_loader": previous.content_loader, "trusted_role_policy": previous.trusted_role_policy,
        "collector_namespace_policy": previous.collector_namespace_policy,
        "registration": responses.registration, "journal": responses.journal, "adapter": responses.adapter,
        "backend_collections": tuple((collection, dict(collection)) for collection in collections),
        "executor_backends": tuple((backend, getattr(backend, "executor", None))
                                  for collection in collections for backend in collection.values()),
    }


def assert_post_handoff_authority_graph(before, responses, policy_authority, *, predecessor_events=None):
    """Identity proof; exact-history callers also assert unfiltered immutable history."""
    from dataclasses import is_dataclass
    from operator_store_model import operation_events
    from v03_dogfood_full_composition import DogfoodPostHandoffRecoveringExecutor, RECOVERY_OPERATION_ID
    bundle = responses.operator_bundle
    executor = bundle.executor
    require(isinstance(executor, DogfoodPostHandoffRecoveringExecutor), "dogfood executor was not installed")
    require(executor is not before["previous"] and executor.base is before["base"],
            "installer created a shadow base executor or retained the old wrapper")
    for actual, key in (
        (responses.runtime, "runtime"), (bundle.runtime, "runtime"),
        (bundle.vertical_bundle.runtime, "runtime"), (executor.runtime, "runtime"),
        (executor.runtime.backend, "backend"), (executor.runtime.protection_verifier, "protection_verifier"),
        (executor.runtime.plan_guard, "plan_guard"), (executor.base.persist_gateway, "persist_gateway"),
        (executor.base.dispatch_gateway, "dispatch_gateway"), (executor.base.feature_gateway, "feature_gateway"),
        (executor.base.config, "config"), (executor.base.resolution_policy_verifier, "resolution_policy_verifier"),
        (bundle.write_bundle, "write_bundle"), (bundle.write_bundle.read_bundle, "read_bundle"),
        (bundle.callback_coordinator, "callback_coordinator"),
        (bundle.decision_notification_coordinator, "decision_coordinator"),
        (executor.content_loader, "content_loader"), (responses.registration, "registration"),
        (responses.journal, "journal"), (responses.adapter, "adapter"),
    ):
        require(actual is before[key], "post-handoff installation split or replaced authority: " + key)
    require(executor.post_handoff_policy_authority is policy_authority, "consumer policy authority was replaced")
    require(executor.trusted_role_policy == before["trusted_role_policy"]
            and executor.collector_namespace_policy == before["collector_namespace_policy"],
            "installer changed callback validation policy")
    require(bundle.callback_coordinator.executor is executor,
            "callback coordinator retained a different recovering executor")
    require(responses.journal.runtime is before["runtime"], "Responses journal uses a second Store")
    require(responses.adapter.journal is responses.journal
            and responses.adapter.registration is responses.registration,
            "adapter stopped using its original journal or registration")
    current_collections = (bundle.backends, bundle.vertical_bundle.api_backends,
                           responses.backends, responses.adapter.backends)
    for current, (original, members) in zip(current_collections, before["backend_collections"]):
        require(current is original and set(current) == set(members),
                "installer replaced or expanded a backend collection")
        require(all(current[name] is backend for name, backend in members.items()),
                "installer replaced canonical backend objects")
    for backend, old_executor in before["executor_backends"]:
        if old_executor is before["previous"]:
            require(backend.executor is executor, "backend retained the old recovering wrapper")
        elif old_executor is not None:
            require(backend.executor is old_executor, "installer rewired an unrelated executor")
    require(bundle.backends["operation.start"].executor is executor
            and responses.backends["operation.start"].executor is executor
            and responses.adapter.backends["operation.start"].executor is executor,
            "start/Responses/adapter entrypoints do not reach one new wrapper")
    require("operation.resume" not in responses.backends and "operation.resume" not in responses.adapter.backends,
            "server-only resume leaked into model capabilities")
    for value in (responses, bundle, bundle.vertical_bundle):
        require(is_dataclass(value) and value.__dataclass_params__.frozen,
                "installer substituted a mutable or fake production bundle")
    require(before["responses"].operator_bundle is before["bundle"]
            and before["bundle"].vertical_bundle is before["vertical"]
            and before["vertical"].executor is before["previous"],
            "installer mutated frozen predecessor bundle references")
    if predecessor_events is not None:
        require(len(predecessor_events) == 15, "replay identity test requires the exact fifteen-event predecessor")
        actual = operation_events(before["runtime"].backend.read_snapshot(), RECOVERY_OPERATION_ID)
        visible = executor._events(RECOVERY_OPERATION_ID)
        require(visible == actual, "post-handoff wrapper hides or filters protected Operation history")
        require(actual[:15] == predecessor_events, "post-handoff replay rewrote the frozen predecessor")
        require(actual[13]["event_type"] == "worker.result.rejected"
                and actual[14]["event_type"] == "loop.stable-stop"
                and actual[14]["payload"]["status"] == "BLOCKED",
                "original rejected observation or BLOCKED fact was hidden")


def assemble_post_handoff_responses_graph(
    *, runtime, base_executor, content_loader, slot, config, policy_authority,
    decision_policy_verifier, trusted_role_policy, collector_namespace_policy,
    reader_http_get, target_read_token="test-target-read",
    registration_id="post-handoff-real-graph-test",
    provider_scope_id="post-handoff-real-graph-test",
):
    """Assemble real canonical classes around the supplied single test runtime.

    This helper is test-only composition, not a claim that a Memory backend
    satisfies the production RemoteGit factory admission check. It neither
    creates a second runtime nor bypasses/patches that production check.
    Callback, start, adapter and Persist transitions execute through their
    actual classes after the caller supplies frozen history and fake HTTP.
    """
    from operator_decision_backends import DecisionListBackend, NotificationListBackend, OperatorInboxBackend
    from operator_production_runtime import (
        BoundedTrustedContextProvider, FeatureStatusBackend, GitHubTrustedProjectFeatureReader,
        ProjectInspectBackend, TrustedOperatorReadBundle,
    )
    from operator_production_store_backends import scoped_store_backends
    from operator_production_write_bundle import extend_with_trusted_decision_writes
    from operator_store_backends import OperatorStoreRuntime, store_backends
    from operator_store_model import normalize_repository
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor
    from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_controller import VerticalLoopResumeBackend
    from operator_vertical_runtime import TrustedVerticalRuntimeBundle, VerticalLoopStartBackend
    from operator_v03_vertical_production_runtime import (
        TrustedV03VerticalProductionBundle, _validate_decision_policy_binding,
    )
    from operator_openai_responses import TrustedResponsesRegistration, OpenAIResponsesOperatorAdapter
    from operator_openai_responses_journal import StoreResponsesCallJournal
    from operator_openai_responses_production import (
        OpenAIResponsesProductionBundle, _responses_backends,
        _require_shared_runtime, _require_final_runtime_types,
    )
    from v03_dogfood_full_composition import install_post_handoff_executor

    require(isinstance(runtime, OperatorStoreRuntime) and isinstance(base_executor, TrustedVerticalExecutor),
            "real graph requires actual supplied Store runtime and base executor")
    require(base_executor.runtime is runtime, "graph assembler received a shadow base runtime")
    require(normalize_repository(runtime.backend.repository) == config.store_repository
            and runtime.backend.state_ref == config.state_ref,
            "graph assembler Store/config identity mismatch")
    require(config.feature_ids == frozenset({slot.feature_id})
            and config.feature_ref(slot.feature_id) == slot.target_ref
            and base_executor.config.target_ref == slot.target_ref,
            "graph assembler escaped its fixed Feature/ref")
    require(callable(content_loader) and callable(reader_http_get),
            "graph assembler requires explicit trusted content and fake-provider loaders")
    _validate_decision_policy_binding(config, decision_policy_verifier)
    previous = FailureClassifyingTrustedRecoveringVerticalExecutor(
        base_executor=base_executor, content_loader=content_loader,
        trusted_role_policy=trusted_role_policy, collector_namespace_policy=collector_namespace_policy)
    callbacks = TrustedVerticalCallbackCoordinator(
        executor=previous, content_loader=content_loader,
        trusted_role_policy=trusted_role_policy, collector_namespace_policy=collector_namespace_policy)
    resume = VerticalLoopResumeBackend(
        runtime=runtime, feature_gateway=base_executor.feature_gateway, executor=previous)
    vertical_backends = store_backends(
        runtime, operation_profile=VERTICAL_PROFILE, resume_backend=resume)
    vertical_backends["operation.start"] = VerticalLoopStartBackend(
        delegate=vertical_backends["operation.start"], executor=previous)
    vertical = TrustedVerticalRuntimeBundle(
        runtime=runtime, executor=previous, callback_coordinator=callbacks,
        api_backends=vertical_backends)

    reader = GitHubTrustedProjectFeatureReader(
        config=config, token=target_read_token, api_base="https://api.github.com",
        http_get=reader_http_get)
    provider = BoundedTrustedContextProvider(config=config, adapter_id=ADAPTER_ID)
    canonical = {
        "project.inspect": ProjectInspectBackend(config=config, adapter_id=ADAPTER_ID, reader=reader),
        "feature.status": FeatureStatusBackend(config=config, adapter_id=ADAPTER_ID, reader=reader),
        "operator.inbox": OperatorInboxBackend(runtime),
        "decision.list": DecisionListBackend(runtime),
        "notification.list": NotificationListBackend(runtime),
        **scoped_store_backends(
            config=config, adapter_id=ADAPTER_ID, runtime=runtime, reader=reader,
            operation_profile=VERTICAL_PROFILE),
    }
    canonical["operation.start"] = VerticalLoopStartBackend(
        delegate=canonical["operation.start"], executor=previous)
    read_bundle = TrustedOperatorReadBundle(
        config=config, trusted_context_provider=provider, backends=canonical, runtime=runtime)
    write_bundle = extend_with_trusted_decision_writes(
        read_bundle, policy_verifier=decision_policy_verifier,
        feature_gateway=base_executor.feature_gateway,
        trusted_context_digest=base_executor.config.trusted_context_digest)
    operator_bundle = TrustedV03VerticalProductionBundle(
        write_bundle=write_bundle, vertical_bundle=vertical, feature_id=slot.feature_id)
    # Apply the actual downstream graph/type checks. The separate production
    # backend-admission check is intentionally not represented as passed.
    _require_shared_runtime(operator_bundle, runtime)
    _require_final_runtime_types(operator_bundle)
    model_backends = _responses_backends(operator_bundle)
    context = dict(provider.for_request(
        {"repository": config.target_repository, "feature_id": slot.feature_id}))
    context["trusted_context_digest"] = base_executor.config.trusted_context_digest
    registration = TrustedResponsesRegistration(
        registration_id=registration_id, provider_scope_id=provider_scope_id,
        target_repository=config.target_repository, feature_refs={slot.feature_id: slot.target_ref},
        trusted_context=context, human_principal=config.principal)
    journal = StoreResponsesCallJournal(runtime)
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration, backends=model_backends, journal=journal)
    responses = OpenAIResponsesProductionBundle(
        operator_bundle=operator_bundle, runtime=runtime, registration=registration,
        journal=journal, backends=model_backends, adapter=adapter)
    captured = capture_post_handoff_authority_graph(responses)
    installed = install_post_handoff_executor(responses, policy_authority)
    assert_post_handoff_authority_graph(captured, installed, policy_authority)
    return installed, captured


def post_handoff_executor_graph_tests() -> None:
    """Real-class installation identity only; the exact-history suite proves execution."""
    from dataclasses import FrozenInstanceError, replace
    from operator_store_backends import OperatorStoreRuntime, store_backends
    from operator_store_git import MemoryStateRefBackend
    from operator_store_model import StoreSnapshot, canonical_json
    from operator_store_protection import StaticProtectionVerifier, PROTECTED
    from operator_effect_rollout import EffectLineageWriteFence, VerifiedEffectLineageRollout
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_controller import VerticalLoopResumeBackend
    from operator_vertical_runtime import TrustedVerticalRuntimeBundle, VerticalLoopStartBackend
    from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
    from operator_release_feature_event_gateway import build_release_decision_event_gateway
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway
    from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig
    from operator_production_runtime import TrustedOperatorReadBundle, BoundedTrustedContextProvider
    from operator_production_write_bundle import TrustedOperatorWriteBundle
    from operator_v03_vertical_production_runtime import TrustedV03VerticalProductionBundle
    from operator_openai_responses import TrustedResponsesRegistration, OpenAIResponsesOperatorAdapter
    from operator_openai_responses_journal import StoreResponsesCallJournal
    from operator_openai_responses_production import OpenAIResponsesProductionBundle
    from v03_real_runtime_full_composition import DeferredFixtureFeatureTruthGateway
    from v03_dogfood_full_composition import install_post_handoff_executor

    calls = []
    def no_provider_effect(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("executor installation must not invoke external providers")
    slot = require_slot("happy_path")
    config = TrustedOperatorRuntimeConfig(
        target_repository=REPOSITORY, store_repository=REPOSITORY, installation_ref="main",
        store_checkout=Path("."), principal="installer-graph-test",
        feature_bindings=(TrustedFeatureBinding(slot.feature_id, slot.target_ref),))
    backend = MemoryStateRefBackend(
        repository=REPOSITORY, state_ref=config.state_ref,
        snapshot=StoreSnapshot(ref_sha="a" * 40, files={}))
    rollout = VerifiedEffectLineageRollout(
        repository=REPOSITORY, state_ref=config.state_ref, operation_profile=VERTICAL_PROFILE,
        effect_lineage_required=True, policy_ref="graph-policy", policy_digest="graph-policy-digest",
        writer_capability="lineage-aware-v1", writer_fence_receipt_ref="graph-fence",
        writer_fence_receipt_digest="graph-fence-digest", test_only=True)
    runtime = OperatorStoreRuntime(
        backend=backend, protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout))
    event_gateway = build_release_decision_event_gateway(
        token="test-event", repository=REPOSITORY, default_branch="main",
        feature_refs={slot.feature_id: slot.target_ref}, http_request=no_provider_effect)
    persist = DurableVerticalFeaturePersistGateway(runtime=runtime, event_gateway=event_gateway)
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main", developer_workflow="developer.yml",
        reviewer_workflow="reviewer.yml", qa_workflow="qa.yml")
    transport = GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(control_repository=REPOSITORY, token="test-actions",
                                              workflows=workflows),
        http=no_provider_effect, sleeper=no_provider_effect)
    dispatch = StoreBackedOneShotExternalCreateGateway(
        runtime=runtime, delegate=GhAwVerticalRoleDispatchGateway(transport=transport, workflows=workflows),
        trusted_context_digest="graph-context", effect_lineage_required=True)
    feature = DeferredFixtureFeatureTruthGateway()
    resolution = ProtectedEffectResolutionPolicyVerifier(
        repository=REPOSITORY, state_ref=config.state_ref, operation_profile=VERTICAL_PROFILE,
        policy_loader=no_provider_effect, evidence_fact_loader=no_provider_effect)
    base = TrustedVerticalExecutor(
        runtime=runtime, feature_gateway=feature, persist_gateway=persist, dispatch_gateway=dispatch,
        config=TrustedVerticalExecutorConfig(
            target_ref=slot.target_ref, trusted_context_digest="graph-context",
            effect_lineage_required=True, old_writers_quiesced=True,
            rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest),
        resolution_policy_verifier=resolution)
    previous = FailureClassifyingTrustedRecoveringVerticalExecutor(
        base_executor=base, content_loader=no_provider_effect,
        trusted_role_policy="graph-roles", collector_namespace_policy="graph-collector")
    callbacks = TrustedVerticalCallbackCoordinator(
        executor=previous, content_loader=no_provider_effect,
        trusted_role_policy="graph-roles", collector_namespace_policy="graph-collector")
    resume = VerticalLoopResumeBackend(runtime=runtime, feature_gateway=feature, executor=previous)
    backends = store_backends(runtime, operation_profile=VERTICAL_PROFILE, resume_backend=resume)
    backends["operation.start"] = VerticalLoopStartBackend(delegate=backends["operation.start"], executor=previous)
    vertical = TrustedVerticalRuntimeBundle(
        runtime=runtime, executor=previous, callback_coordinator=callbacks, api_backends=backends)
    read = TrustedOperatorReadBundle(
        config=config, trusted_context_provider=BoundedTrustedContextProvider(config=config, adapter_id=ADAPTER_ID),
        backends=backends, runtime=runtime)
    write = TrustedOperatorWriteBundle(read_bundle=read, backends=backends, decision_notification_coordinator=None)
    bundle = TrustedV03VerticalProductionBundle(write_bundle=write, vertical_bundle=vertical, feature_id=slot.feature_id)
    registration = TrustedResponsesRegistration(
        registration_id="graph-registration", provider_scope_id="graph-provider", target_repository=REPOSITORY,
        feature_refs={slot.feature_id: slot.target_ref}, trusted_context={})
    model_backends = {name: value for name, value in backends.items() if name != "operation.resume"}
    journal = StoreResponsesCallJournal(runtime)
    adapter = OpenAIResponsesOperatorAdapter(registration=registration, backends=model_backends, journal=journal)
    responses = OpenAIResponsesProductionBundle(
        operator_bundle=bundle, runtime=runtime, registration=registration,
        journal=journal, backends=model_backends, adapter=adapter)
    before = capture_post_handoff_authority_graph(responses)
    before_store = canonical_json(backend.read_snapshot().files), backend.read_snapshot().ref_sha
    policy_authority = object()  # An identity token only; no attestation or execution is claimed here.
    installed = install_post_handoff_executor(responses, policy_authority)
    assert_post_handoff_authority_graph(before, installed, policy_authority)
    require(installed is not responses and installed.operator_bundle is not bundle
            and installed.operator_bundle.vertical_bundle is not vertical,
            "installer failed to replace frozen outer references")
    require(installed.operator_bundle.executor.persist_gateway.runtime is runtime
            and installed.operator_bundle.executor.dispatch_gateway.runtime is runtime,
            "Persist or one-shot dispatch uses another runtime")
    for frozen in (installed, installed.operator_bundle, installed.operator_bundle.vertical_bundle):
        try:
            frozen.runtime = object()
        except (FrozenInstanceError, AttributeError):
            pass
        else:
            raise AssertionError("installed production bundle lost frozen semantics")
    require(before_store == (canonical_json(backend.read_snapshot().files), backend.read_snapshot().ref_sha)
            and not calls, "executor graph installation mutated Store or reached a provider")
    shadow = replace(installed, runtime=OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=REPOSITORY, state_ref=config.state_ref),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED)))
    try:
        assert_post_handoff_authority_graph(before, shadow, policy_authority)
    except AssertionError:
        pass
    else:
        raise AssertionError("identity assertion accepted a shadow Store runtime")


def source_contract_tests() -> None:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    require(
        "build_openai_responses_production_bundle" in calls,
        "dogfood composition does not construct through Responses production bundle",
    )
    require(
        "build_v03_vertical_write_ready_operator_bundle" not in calls,
        "dogfood composition bypasses Responses and constructs raw Operator runtime",
    )
    require("responses: OpenAIResponsesProductionBundle" in source, "composition does not retain Responses authority")
    require("return self.responses.adapter" in source, "composition does not expose exact production adapter")
    require("operation.resume" in source, "composition lost server-only capability leak assertion")
    require("HANDOFF_SCHEMA" in source and "read_dogfood_handoff" in source, "Developer handoff lost immutable sidecar authority")
    require('"candidate.handoff.adopted"' not in source, "unsupported handoff event returned to shared journal")
    require("remediation_artifact_supersession" in source, "remediation lifecycle lost explicit supersession")


def readiness_execution_binding_test() -> None:
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    rows = (
        SimpleNamespace(
            role="developer", stage="implementation", selected_profile="copilot",
            worker_workflow=workflows.developer_workflow, specialized_role_worker=False,
            accepted_credential_identities=("COPILOT_GITHUB_TOKEN",),
        ),
        SimpleNamespace(
            role="reviewer", stage="code-review", selected_profile="deepseek",
            worker_workflow=workflows.reviewer_workflow, specialized_role_worker=True,
            accepted_credential_identities=("DEEPSEEK_API_KEY",),
        ),
        SimpleNamespace(
            role="qa", stage="verification", selected_profile="gemini",
            worker_workflow=workflows.qa_workflow, specialized_role_worker=True,
            accepted_credential_identities=("GEMINI_API_KEY",),
        ),
    )
    bindings = _execution_bindings(SimpleNamespace(bindings=rows), workflows)
    require(bindings["developer"]["worker_id"] == "ai-sdlc-gh-aw-worker", "Copilot Developer worker id drifted")
    require(bindings["developer"]["profile"] == "copilot", "Copilot Developer profile drifted")
    require(bindings["reviewer"]["worker_id"] == "code-review-reviewer-deepseek", "DeepSeek Reviewer worker id drifted")
    require(bindings["reviewer"]["credential_name"] == "DEEPSEEK_API_KEY", "DeepSeek Reviewer credential drifted")
    require(bindings["qa"]["worker_id"] == "verification-qa-gemini", "Gemini QA worker id drifted")
    require(
        {bindings[role]["workflow_file"] for role in bindings}
        == {workflows.developer_workflow, workflows.reviewer_workflow, workflows.qa_workflow},
        "readiness execution binding workflow set drifted",
    )


def execution_binding_wrapper_test() -> None:
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    raw = __import__("operator_vertical_gh_aw").GhAwVerticalRoleDispatchGateway(
        transport=object(), workflows=workflows,
    )
    bindings = {
        "developer": {
            "worker_id": "ai-sdlc-gh-aw-worker",
            "role": "developer",
            "profile": "copilot",
            "workflow_file": workflows.developer_workflow,
            "selection_policy_id": "v03-frozen-vertical-workflow-map/v1",
            "default_branch": "main",
        },
        "reviewer": {
            "worker_id": "code-review-reviewer-deepseek",
            "role": "reviewer",
            "profile": "deepseek",
            "workflow_file": workflows.reviewer_workflow,
            "selection_policy_id": "v03-frozen-reviewer-provider-order/v2",
            "default_branch": "main",
            "credential_name": "DEEPSEEK_API_KEY",
        },
        "qa": {
            "worker_id": "verification-qa-gemini",
            "role": "qa",
            "profile": "gemini",
            "workflow_file": workflows.qa_workflow,
            "selection_policy_id": "v03-frozen-vertical-workflow-map/v1",
            "default_branch": "main",
        },
    }
    bound = DogfoodExecutionBoundDispatchGateway(delegate=raw, execution_bindings=bindings)
    require(bound.transport is raw.transport and bound.workflows is workflows, "dogfood wrapper replaced production transport")
    require(
        bound.execution_binding(dispatch={"role": "developer"}) == bindings["developer"],
        "Copilot Developer execution binding drifted",
    )
    try:
        bound.execution_binding(dispatch={"role": "product"})
    except Exception:
        pass
    else:
        raise AssertionError("unknown dogfood role escaped exact execution binding set")



def developer_candidate_transport_tests() -> None:
    import json
    from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway
    from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig
    from validate_operator_vertical_gh_aw_actions_transport import FakeGitHubActionsHttp

    slot = require_slot("happy_path")
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main", developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    head = "70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23"
    state = {"head": head}
    provider = DogfoodGitHubCandidateProvider(
        slot=slot, repository=REPOSITORY, token="test-read",
        http_get=lambda _url, _headers: (200, [_pr(slot, head=state["head"])]),
    )
    config = GitHubActionsWorkflowTransportConfig(
        control_repository=REPOSITORY, token="test-actions", workflows=workflows,
        launch_poll_attempts=1, launch_poll_seconds=0,
    )
    dispatch = dict(
        operation_id="op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4", operation_generation=1,
        operation_profile=VERTICAL_PROFILE,
        semantic_effect_key="80b31137a408f2b0ee85248bd069b972af80b9777f91f2bd00c9b27b42f9e804",
        external_dispatch_key="dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1",
        dispatch_id="vertical-31df3f1ed41b54c58ed4c4030a9f97d9",
        target_repository=REPOSITORY, target_ref=slot.target_ref, feature_id=slot.feature_id,
        expected_revision=1, feature_stage="implementation", task_id="vertical:implementation:1",
        task_identity="vertical:implementation:1", role="developer",
        candidate_pr_number=552, candidate_head_sha=head,
    )
    inputs = GhAwVerticalRoleDispatchGateway(transport=object(), workflows=workflows)._inputs(dispatch)
    original = json.dumps(inputs, sort_keys=True)
    old_http = FakeGitHubActionsHttp(create_run_on_post=True)
    old = GitHubActionsVerticalGhAwTransport(config, http=old_http, sleeper=lambda _: None)
    try:
        old.dispatch(workflow=workflows.developer_workflow, ref="main", inputs=inputs)
    except VerticalInvariantError as exc:
        require(exc.code == "POLICY_DENIED" and "payload is not bound" in str(exc), "wrong root-cause rejection")
    else:
        raise AssertionError("old transport accepted real Developer candidate")
    require(old_http.get_calls == 0 and old_http.post_calls == 0, "root-cause path reached HTTP")
    print("Historical Developer candidate mismatch reproduced: POLICY_DENIED before any HTTP")

    http = FakeGitHubActionsHttp(create_run_on_post=True)
    fixed = DogfoodCandidateBoundActionsTransport(config, candidate_provider=provider, http=http, sleeper=lambda _: None)
    result = fixed.dispatch(workflow=workflows.developer_workflow, ref="main", inputs=inputs)
    require(result == {"lookup_state": "LAUNCHED", "receipt_id": "9001"}, "fixed Developer did not converge")
    require(http.post_calls == 1, "fixed transport did not preserve one POST")
    sent = http.last_post["document"]["inputs"]
    require(json.loads(sent["task_payload"])["feature_context"]["vertical"]["candidate_head_sha"] == head, "candidate stripped from real payload")
    require(sent == inputs and json.dumps(inputs, sort_keys=True) == original, "transport mutated original semantic inputs")
    fixed.dispatch(workflow=workflows.developer_workflow, ref="main", inputs=inputs)
    require(http.post_calls == 1, "existing receipt caused another POST")

    state["head"] = "2" * 40
    stale_http = FakeGitHubActionsHttp(create_run_on_post=True)
    stale = DogfoodCandidateBoundActionsTransport(config, candidate_provider=provider, http=stale_http, sleeper=lambda _: None)
    try:
        stale.dispatch(workflow=workflows.developer_workflow, ref="main", inputs=inputs)
    except VerticalInvariantError as exc:
        require(exc.code == "STALE_REVISION", "wrong stale-candidate rejection")
    else:
        raise AssertionError("changed candidate reached dispatch")
    require(stale_http.post_calls == stale_http.get_calls == 0, "stale candidate reached Actions HTTP")
    state["head"] = head
    for field, value in (("role", "reviewer"), ("stage", "verification"), ("dispatch_key", "invalid")):
        bad = dict(inputs, **{field: value})
        try:
            fixed.dispatch(workflow=workflows.developer_workflow, ref="main", inputs=bad)
        except VerticalInvariantError as exc:
            require(exc.code in {"POLICY_DENIED", "INVALID_REQUEST"}, "wrong envelope rejection")
        else:
            raise AssertionError("Developer envelope tamper accepted: " + field)
    require(http.post_calls == 1, "tampered envelope reached another POST")
    for role in ("reviewer", "qa"):
        role_dispatch = dict(dispatch, role=role)
        role_inputs = GhAwVerticalRoleDispatchGateway(transport=object(), workflows=workflows)._inputs(role_dispatch)
        role_http = FakeGitHubActionsHttp(create_run_on_post=True)
        role_transport = DogfoodCandidateBoundActionsTransport(config, candidate_provider=provider, http=role_http, sleeper=lambda _: None)
        receipt = role_transport.dispatch(workflow=workflows.workflow_for(role), ref="main", inputs=role_inputs)
        require(receipt["lookup_state"] == "LAUNCHED" and role_http.post_calls == 1, "Gate role contract changed")


def early_adapter_gate_test() -> None:
    slot = require_slot("happy_path")
    config = TrustedOperatorRuntimeConfig(
        target_repository=REPOSITORY,
        store_repository=REPOSITORY,
        installation_ref="main",
        store_checkout=Path("."),
        principal="composition-test",
        feature_bindings=(TrustedFeatureBinding(slot.feature_id, slot.target_ref),),
    )
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-copilot.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    try:
        build_v03_dogfood_full_composition(
            slot=slot,
            config=config,
            adapter_id="fixture.bypass",
            target_read_token="read-token",
            actions_token="actions-token",
            event_write_token="event-token",
            control_repository=REPOSITORY,
            workflows=workflows,
            execution_bindings={
                "developer": {"worker_id": "x", "role": "developer", "profile": "copilot", "workflow_file": workflows.developer_workflow, "selection_policy_id": "p", "default_branch": "main"},
                "reviewer": {"worker_id": "y", "role": "reviewer", "profile": "copilot", "workflow_file": workflows.reviewer_workflow, "selection_policy_id": "p", "default_branch": "main"},
                "qa": {"worker_id": "z", "role": "qa", "profile": "gemini", "workflow_file": workflows.qa_workflow, "selection_policy_id": "p", "default_branch": "main"},
            },
            protection_verifier=object(),
            policy_authority=object(),
            trusted_context_digest="digest",
            collector_namespace_policy="collector",
            trusted_role_policy="roles",
            clock=lambda: "2026-08-25T12:00:00Z",
        )
    except ValueError as exc:
        require("OpenAI Responses adapter" in str(exc), "wrong early adapter rejection")
    else:
        raise AssertionError("non-Responses adapter reached dogfood production construction")

    require(ADAPTER_ID, "Responses adapter id unexpectedly empty")
    signature = inspect.signature(build_v03_dogfood_full_composition)
    require("adapter_id" in signature.parameters, "dogfood builder lost explicit adapter binding")


def main() -> None:
    candidate_tests()
    handoff_and_supersession_tests()
    post_handoff_executor_graph_tests()
    source_contract_tests()
    readiness_execution_binding_test()
    execution_binding_wrapper_test()
    developer_candidate_transport_tests()
    early_adapter_gate_test()
    print("v0.3 real-dogfood Responses production composition: PASS")


if __name__ == "__main__":
    main()
