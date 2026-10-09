#!/usr/bin/env python3
"""Pure authority-gate validation for the v0.3 dogfood runtime driver."""
from __future__ import annotations

import inspect
import v03_dogfood_runtime_driver as driver_subject

from v03_dogfood_runtime_driver import (
    PREFLIGHT_ONLY,
    RUN,
    VALIDATE_ONLY,
    V03DogfoodRuntimeDriverError,
    DOGFOOD_RESPONSES_API_BASE,
    DOGFOOD_RESPONSES_MODEL,
    dogfood_responses_host_config,
    prepare_previous_installation_operation,
    require_mode,
)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def rejected(**kwargs):
    try:
        require_mode(**kwargs)
    except V03DogfoodRuntimeDriverError:
        return
    raise AssertionError(f"unexpectedly accepted: {kwargs}")


def git_store_transport_tests(live):
    """Exercise the production remote Git backend with an isolated local remote."""
    import os
    from pathlib import Path
    import subprocess
    import tempfile
    from unittest.mock import patch
    from v03_dogfood_runtime_driver import store_git_transport
    from operator_store_git import CasConflict
    from operator_store_model import StoreMutation, StoreMutationPlan
    from operator_store_protection import ProtectionReceipt
    from operator_store_remote_git import RemoteGitStateRefBackend

    job = live["jobs"]["dogfood"]
    identity = {
        "GIT_AUTHOR_NAME": "AI-SDLC Operator Store",
        "GIT_AUTHOR_EMAIL": "operator-store@ai-sdlc.invalid",
        "GIT_COMMITTER_NAME": "AI-SDLC Operator Store",
        "GIT_COMMITTER_EMAIL": "operator-store@ai-sdlc.invalid",
    }
    expect(all(job.get("env", {}).get(key) == value for key, value in identity.items()),
           "live Store Git subprocesses lack explicit trusted commit identity")
    checkouts = [step for step in job["steps"]
                 if str(step.get("uses", "")).startswith("actions/checkout@")]
    expect(len(checkouts) == 1, "live checkout identity is ambiguous")
    settings = checkouts[0].get("with", {})
    expect(settings.get("token") == "${{ steps.event-token.outputs.token }}",
           "Store Git transport must use the bounded Runtime App token")
    expect(settings.get("persist-credentials") is False,
           "Store Git credentials must not persist in checkout")
    tokens = [step for step in job["steps"] if step.get("id") == "event-token"]
    expect(len(tokens) == 1, "Runtime App token identity is ambiguous")
    app = tokens[0]["with"]
    expect(app.get("permission-contents") == "write"
           and app.get("repositories") == "${{ github.event.repository.name }}"
           and app.get("owner") == "${{ github.repository_owner }}",
           "Store token escaped the installed repository")
    expect(live["permissions"]["contents"] == "read",
           "Actions token must not replace the Runtime App write identity")

    # Real Git transport, no network/model/Worker or release evidence. Ignore
    # runner/global Git identity so the pre-fix commit-tree failure is exercised.
    isolated = {key: value for key, value in os.environ.items()
                if not key.startswith("GIT_")}
    isolated.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                    GIT_TERMINAL_PROMPT="0", GITHUB_ACTIONS="false")
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-store-transport-") as temporary:
        root = Path(temporary)
        remote, first, second = root / "remote.git", root / "first", root / "second"
        def git(cwd, *args):
            return subprocess.run(["git", *args], cwd=cwd, text=True,
                                  capture_output=True, check=True)
        with patch.dict(os.environ, isolated, clear=True):
            git(root, "init", "--bare", str(remote))
            for directory in (first, second):
                git(root, "init", str(directory))
                git(directory, "remote", "add", "origin", "https://github.com/DREAM-XIN/ai-sdlc")
                git(directory, "remote", "add", "store-fixture", str(remote))
            backend = RemoteGitStateRefBackend(
                repo_path=first, repository="dream-xin/ai-sdlc",
                state_ref="refs/heads/ai-sdlc-operator-state", remote_name="store-fixture")
            path = "state/operator/v1/projections/op-transport-check.json"
            initial = StoreMutationPlan(
                None, (StoreMutation("replace_projection", path, {"transport": 1}),),
                {"transport": 1})
            try:
                backend._build_commit(backend.read_snapshot(), initial)
            except subprocess.CalledProcessError:
                pass
            else:
                raise AssertionError("clean Store commit unexpectedly borrowed Git identity")
            with patch.dict(os.environ, identity):
                receipt = ProtectionReceipt(
                    repository=backend.repository, state_ref=backend.state_ref,
                    status="PROTECTED", verifier_identity="local-transport-test-only",
                    verified_at="2026-10-02T00:00:00Z", policy_digest="test-only")
                with store_git_transport(token="local-test-token", repository=backend.repository,
                                         repo_path=first):
                    header = git(first, "config", "--get-urlmatch", "http.extraheader",
                                 "https://github.com/DREAM-XIN/ai-sdlc").stdout.strip()
                    expect(header.startswith("AUTHORIZATION: basic "),
                           "Git subprocess did not receive process-scoped App authentication")
                    created = backend.commit(initial, receipt)
                    expect(created.snapshot.get(path) == {"transport": 1},
                           "remote Store did not confirm committed projection")
                    author = git(first, "show", "-s", "--format=%an <%ae>|%cn <%ce>",
                                 created.ref_sha).stdout.strip()
                    expect(author == "AI-SDLC Operator Store <operator-store@ai-sdlc.invalid>"
                           "|AI-SDLC Operator Store <operator-store@ai-sdlc.invalid>",
                           "commit-tree did not inherit configured workflow identity")
                    rival = RemoteGitStateRefBackend(
                        repo_path=second, repository=backend.repository,
                        state_ref=backend.state_ref, remote_name="store-fixture")
                    stale = rival.read_snapshot()
                    advanced = backend.commit(StoreMutationPlan(
                        created.ref_sha,
                        (StoreMutation("replace_projection", path, {"transport": 2}),),
                        {"transport": 2}), receipt)
                    try:
                        rival.commit(StoreMutationPlan(
                            stale.ref_sha,
                            (StoreMutation("replace_projection", path, {"transport": 3}),),
                            {"transport": 3}), receipt)
                    except CasConflict:
                        pass
                    else:
                        raise AssertionError("stale Store writer overwrote remote CAS winner")
                    truth = backend.read_snapshot()
                    expect(truth.ref_sha == advanced.ref_sha and truth.get(path) == {"transport": 2},
                           "rejected stale writer changed durable remote Store")
                expect("GIT_CONFIG_VALUE_0" not in os.environ,
                       "Store transport left credential material in the process environment")
                expect("AUTHORIZATION" not in (first / ".git/config").read_text(),
                       "Store transport persisted credential material to checkout")
                try:
                    with store_git_transport(token="local-test-token", repository=backend.repository,
                                             repo_path=first):
                        raise RuntimeError("simulated Store failure")
                except RuntimeError:
                    pass
                expect("GIT_CONFIG_VALUE_0" not in os.environ,
                       "exceptional Store exit left credential material in the environment")
                with patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1"}):
                    try:
                        with store_git_transport(token="local-test-token", repository=backend.repository,
                                                 repo_path=first):
                            pass
                    except V03DogfoodRuntimeDriverError:
                        pass
                    else:
                        raise AssertionError("inherited Git transport authority was accepted")
                git(first, "remote", "set-url", "origin", "https://github.com/other/repo")
                try:
                    with store_git_transport(token="local-test-token", repository=backend.repository,
                                             repo_path=first):
                        pass
                except V03DogfoodRuntimeDriverError:
                    pass
                else:
                    raise AssertionError("credential transport accepted a different origin")
                git(first, "remote", "set-url", "origin", "https://github.com/DREAM-XIN/ai-sdlc")
                for token, repository in (("", backend.repository), ("local-test-token", "other/repo")):
                    try:
                        with store_git_transport(token=token, repository=repository, repo_path=first):
                            pass
                    except V03DogfoodRuntimeDriverError:
                        pass
                    else:
                        raise AssertionError("unconfigured Store authority was accepted")
    print("- isolated real commit-tree/push/read-back and stale-writer CAS passed")



def installation_transition_tests():
    """Prove the old-installation window becomes G+1 without a new effect key."""
    from types import SimpleNamespace

    from operator_store import (
        plan_authorize_launch,
        plan_dispatch_claim,
        plan_launch_lookup,
        plan_operation_fact,
        plan_operation_start,
        plan_semantic_reservation,
    )
    from operator_external_create_attempt import plan_external_create_attempt
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_git import MemoryStateRefBackend
    from operator_store_model import operation_events, rebuild_projection
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_recovery import plan_vertical_takeover
    from v03_dogfood_fixture_pool import require_slot

    repository = "DREAM-XIN/ai-sdlc"
    state_ref = "refs/heads/ai-sdlc-operator-state"
    feature_id = require_slot("happy_path").feature_id
    old_context = "old-installation-context"
    current_context = "current-installation-context"
    now = "2026-10-03T02:00:00Z"

    def seeded(lookup_state, *, lookup_context=old_context):
        backend = MemoryStateRefBackend(repository=repository, state_ref=state_ref)
        runtime = OperatorStoreRuntime(
            backend=backend,
            protection_verifier=StaticProtectionVerifier(status=PROTECTED),
            clock=lambda: now,
        )
        started = runtime.commit_replanned(
            lambda snapshot: plan_operation_start(
                snapshot,
                target_repository=repository,
                feature_id=feature_id,
                expected_revision=1,
                idempotency_key="old-installation-start",
                occurred_at=now,
                trusted_context_digest=old_context,
                operation_profile=VERTICAL_PROFILE,
            )
        )
        operation_id = started.result["operation_id"]
        runtime.commit_replanned(
            lambda snapshot: plan_operation_fact(
                snapshot,
                operation_id=operation_id,
                generation=0,
                event_type="loop.step.selected",
                payload={"step": "IMPLEMENTATION_WORK", "task_id": "implementation"},
                occurred_at=now,
                trusted_context_digest=old_context,
            )
        )
        reserved = runtime.commit_replanned(
            lambda snapshot: plan_semantic_reservation(
                snapshot,
                operation_id=operation_id,
                generation=0,
                target_repository=repository,
                feature_id=feature_id,
                expected_revision=1,
                current_stage="implementation",
                task_identity="vertical:implementation:fixture",
                role="developer",
                candidate_head_sha="a" * 40,
                occurred_at=now,
                trusted_context_digest=old_context,
            )
        )
        claimed = runtime.commit_replanned(
            lambda snapshot: plan_dispatch_claim(
                snapshot,
                operation_id=operation_id,
                generation=0,
                effect_key=reserved.result["semantic_effect_key"],
                occurred_at=now,
                trusted_context_digest=old_context,
            )
        )
        runtime.commit_replanned(
            lambda snapshot: plan_authorize_launch(
                snapshot,
                operation_id=operation_id,
                generation=0,
                claim_id=claimed.result["claim_id"],
                dispatch_id="dispatch-old-installation",
                occurred_at=now,
                trusted_context_digest=old_context,
                verified_expected_revision=1,
                verified_stage="implementation",
                verified_candidate_head_sha="a" * 40,
            )
        )
        runtime.commit_replanned(
            lambda snapshot: plan_launch_lookup(
                snapshot,
                operation_id=operation_id,
                generation=0,
                external_dispatch_key_value=reserved.result["external_dispatch_key"],
                lookup_state=lookup_state,
                receipt_id="1001" if lookup_state == "LAUNCHED" else None,
                occurred_at=now,
                trusted_context_digest=lookup_context,
            )
        )
        preflight = SimpleNamespace(
            execution=SimpleNamespace(repository=repository),
            slot=require_slot("happy_path"),
            trusted_context_digest=current_context,
            composition=SimpleNamespace(runtime=runtime),
        )
        return (
            runtime,
            preflight,
            operation_id,
            reserved.result["semantic_effect_key"],
            reserved.result["external_dispatch_key"],
        )

    runtime, preflight, operation_id, semantic_key, external_key = seeded("NOT_LAUNCHED")
    before = runtime.backend.read_snapshot()
    before_events = operation_events(before, operation_id)
    before_reservations = {
        path: value for path, value in before.files.items()
        if "/reservations/external/" in path
    }
    expect(
        prepare_previous_installation_operation(preflight) == operation_id,
        "bounded old-installation Operation was not selected",
    )
    after = runtime.backend.read_snapshot()
    projection = rebuild_projection(after, operation_id)
    expect(
        projection["generation"] == 1 and projection["status"] == "RUNNING",
        "bounded installation transition did not create resumable generation 1",
    )
    expect(
        external_key in projection["authorized_dispatches"],
        "installation transition lost the existing external key",
    )
    expect(
        before_reservations == {
            path: value for path, value in after.files.items()
            if "/reservations/external/" in path
        },
        "installation transition replaced immutable effect authority",
    )
    expect(
        operation_events(after, operation_id)[:len(before_events)] == before_events,
        "installation transition rewrote old Operation facts",
    )
    prepare_previous_installation_operation(preflight)
    expect(
        rebuild_projection(runtime.backend.read_snapshot(), operation_id)["generation"] == 1,
        "current-installation replay created a second takeover",
    )

    launched_runtime, launched_preflight, launched_operation_id, _, _ = seeded("LAUNCHED")
    try:
        prepare_previous_installation_operation(launched_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("launched predecessor escaped bounded transition")
    expect(
        rebuild_projection(launched_runtime.backend.read_snapshot(), launched_operation_id)["generation"] == 0,
        "rejected launched predecessor was mutated",
    )

    unknown_runtime, unknown_preflight, unknown_operation_id, _, _ = seeded("UNKNOWN")
    try:
        prepare_previous_installation_operation(unknown_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("UNKNOWN predecessor escaped bounded transition")
    expect(
        rebuild_projection(unknown_runtime.backend.read_snapshot(), unknown_operation_id)["generation"] == 0,
        "rejected UNKNOWN predecessor was mutated",
    )

    mixed_runtime, mixed_preflight, mixed_operation_id, _, _ = seeded(
        "NOT_LAUNCHED", lookup_context="different-old-context"
    )
    try:
        prepare_previous_installation_operation(mixed_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("mixed-context journal escaped bounded transition")
    expect(
        rebuild_projection(mixed_runtime.backend.read_snapshot(), mixed_operation_id)["generation"] == 0,
        "rejected mixed-context journal was mutated",
    )

    attempt_runtime, attempt_preflight, attempt_operation_id, attempt_semantic, attempt_external = seeded(
        "NOT_LAUNCHED"
    )
    attempt_events = operation_events(attempt_runtime.backend.read_snapshot(), attempt_operation_id)
    claim_payload = attempt_events[2]["payload"]
    authorization_payload = attempt_events[3]["payload"]
    attempt_runtime.commit_replanned(
        lambda snapshot: plan_external_create_attempt(
            snapshot,
            operation_id=attempt_operation_id,
            generation=0,
            claim_id=claim_payload["claim_id"],
            dispatch_id=authorization_payload["dispatch_id"],
            semantic_effect_key=attempt_semantic,
            external_dispatch_key_value=attempt_external,
            execution_binding={
                "worker_id": "dogfood-developer",
                "role": "developer",
                "profile": "dogfood",
                "workflow_file": "dogfood-worker.yml",
                "selection_policy_id": "dogfood-test-policy",
                "default_branch": "main",
            },
            occurred_at=now,
            trusted_context_digest=old_context,
        )
    )
    try:
        prepare_previous_installation_operation(attempt_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("durable external-create attempt escaped bounded transition")
    expect(
        rebuild_projection(attempt_runtime.backend.read_snapshot(), attempt_operation_id)["generation"] == 0,
        "rejected attempted predecessor was mutated",
    )


    race_runtime, race_preflight, race_operation_id, race_semantic, race_external = seeded(
        "NOT_LAUNCHED"
    )
    race_events = operation_events(race_runtime.backend.read_snapshot(), race_operation_id)
    race_claim = race_events[2]["payload"]
    race_authorization = race_events[3]["payload"]
    original_commit_replanned = race_runtime.commit_replanned

    def insert_attempt_before_fresh_plan(planner, *, max_attempts=4):
        race_runtime.commit_replanned = original_commit_replanned
        original_commit_replanned(
            lambda snapshot: plan_external_create_attempt(
                snapshot,
                operation_id=race_operation_id,
                generation=0,
                claim_id=race_claim["claim_id"],
                dispatch_id=race_authorization["dispatch_id"],
                semantic_effect_key=race_semantic,
                external_dispatch_key_value=race_external,
                execution_binding={
                    "worker_id": "dogfood-developer",
                    "role": "developer",
                    "profile": "dogfood",
                    "workflow_file": "dogfood-worker.yml",
                    "selection_policy_id": "dogfood-test-policy",
                    "default_branch": "main",
                },
                occurred_at=now,
                trusted_context_digest=old_context,
            )
        )
        return original_commit_replanned(planner, max_attempts=max_attempts)

    race_runtime.commit_replanned = insert_attempt_before_fresh_plan
    try:
        prepare_previous_installation_operation(race_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("CAS-racing external-create attempt escaped fresh predicate")
    expect(
        rebuild_projection(race_runtime.backend.read_snapshot(), race_operation_id)["generation"] == 0,
        "CAS-racing attempt was followed by a takeover",
    )

    wider_runtime, wider_preflight, wider_operation_id, wider_semantic, wider_external = seeded(
        "NOT_LAUNCHED"
    )
    middle_context = "intermediate-installation-context"
    wider_runtime.commit_replanned(
        lambda snapshot: plan_vertical_takeover(
            snapshot,
            operation_id=wider_operation_id,
            occurred_at=now,
            trusted_context_digest=middle_context,
        )
    )
    wider_runtime.commit_replanned(
        lambda snapshot: plan_operation_fact(
            snapshot,
            operation_id=wider_operation_id,
            generation=1,
            event_type="loop.step.selected",
            payload={"step": "IMPLEMENTATION_WORK", "task_id": "implementation"},
            occurred_at=now,
            trusted_context_digest=middle_context,
        )
    )
    wider_reserved = wider_runtime.commit_replanned(
        lambda snapshot: plan_semantic_reservation(
            snapshot,
            operation_id=wider_operation_id,
            generation=1,
            target_repository=repository,
            feature_id=feature_id,
            expected_revision=1,
            current_stage="implementation",
            task_identity="vertical:implementation:fixture",
            role="developer",
            candidate_head_sha="a" * 40,
            occurred_at=now,
            trusted_context_digest=middle_context,
        )
    )
    expect(
        wider_reserved.result["semantic_effect_key"] == wider_semantic
        and wider_reserved.result["external_dispatch_key"] == wider_external,
        "wider-history fixture changed the semantic/external key",
    )
    wider_claimed = wider_runtime.commit_replanned(
        lambda snapshot: plan_dispatch_claim(
            snapshot,
            operation_id=wider_operation_id,
            generation=1,
            effect_key=wider_semantic,
            occurred_at=now,
            trusted_context_digest=middle_context,
        )
    )
    wider_runtime.commit_replanned(
        lambda snapshot: plan_authorize_launch(
            snapshot,
            operation_id=wider_operation_id,
            generation=1,
            claim_id=wider_claimed.result["claim_id"],
            dispatch_id="dispatch-intermediate-installation",
            occurred_at=now,
            trusted_context_digest=middle_context,
            verified_expected_revision=1,
            verified_stage="implementation",
            verified_candidate_head_sha="a" * 40,
        )
    )
    wider_runtime.commit_replanned(
        lambda snapshot: plan_launch_lookup(
            snapshot,
            operation_id=wider_operation_id,
            generation=1,
            external_dispatch_key_value=wider_external,
            lookup_state="NOT_LAUNCHED",
            receipt_id=None,
            occurred_at=now,
            trusted_context_digest=middle_context,
        )
    )
    try:
        prepare_previous_installation_operation(wider_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("prior-generation wider history escaped bounded transition")
    expect(
        rebuild_projection(wider_runtime.backend.read_snapshot(), wider_operation_id)["generation"] == 1,
        "rejected wider history was mutated",
    )

    print("- exact old-context NOT_LAUNCHED window takes over once with one immutable effect key")



def prehttp_recovery_fence_tests():
    """Freeze the provider-fenced protected-CAS recovery boundary."""
    from copy import deepcopy
    from operator_store_model import is_immutable_path
    from v03_dogfood_full_composition import RECOVERY_CONTINUATION_PATH

    h = driver_subject.HISTORICAL_PREHTTP_RECOVERY
    expect(
        h["operation_id"] == "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
        and h["external_dispatch_key"] == "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1",
        "historical recovery exact Store identity drifted",
    )
    for path in (
        driver_subject.RECOVERY_AUTHORIZATION_PATH,
        driver_subject.RECOVERY_ATTEMPT_PATH,
        driver_subject.RECOVERY_RECEIPT_PATH,
        RECOVERY_CONTINUATION_PATH,
    ):
        expect(is_immutable_path(path), "bounded recovery fact is not immutable: " + path)
        expect("/reservations/external/" not in path, "recovery fact masquerades as reservation authority")

    good = {
        "schema_version": driver_subject.RECOVERY_SCHEMA,
        "app_id": 4576406,
        "app_client_id": "Iv23libojxnnuF43petx",
        "installation_id": 153325330,
        "historical_status": 401,
        "recovery_status": 200,
        "historical_public_key_digest": driver_subject.HISTORICAL_APP_PUBLIC_KEY_DIGEST,
        "provider_observation_run_id": driver_subject.REVOCATION_PROBE_RUN,
        "provider_observation_blob_sha": driver_subject.REVOCATION_OBSERVATION_BLOB,
        "recovery_public_key_digest": "sha256:" + "a" * 64,
        "observed_at": "2026-10-08T04:00:00Z",
    }
    accepted = driver_subject._validate_provider_rotation_fence(good)
    expect(
        accepted["recovery_authority"] is False
        and accepted["release_eligible"] is False
        and accepted["fence_digest"].startswith("sha256:"),
        "provider fence was promoted to lifecycle/release authority",
    )
    for key, value in (
        ("historical_status", 200), ("recovery_status", 401),
        ("app_id", 1), ("app_client_id", "other"),
        ("historical_public_key_digest", "sha256:" + "b" * 64),
        ("recovery_public_key_digest", driver_subject.HISTORICAL_APP_PUBLIC_KEY_DIGEST),
    ):
        changed = deepcopy(good)
        changed[key] = value
        try:
            driver_subject._validate_provider_rotation_fence(changed)
        except V03DogfoodRuntimeDriverError:
            pass
        else:
            raise AssertionError("unsafe provider fence accepted: " + key)

    recover_source = inspect.getsource(driver_subject.recover_historical_prehttp_attempt)
    plan_source = inspect.getsource(driver_subject._plan_bounded_recovery)
    seal_source = inspect.getsource(driver_subject._seal_recovery_receipt)
    boundary_source = inspect.getsource(driver_subject._bounded_recovery_identity)
    expect(
        'StoreMutation("create_immutable", RECOVERY_CONTINUATION_PATH' in plan_source
        and 'validate_armed_recovery_pair' in plan_source,
        "same-key continuation does not validate immutable originals before protected CAS",
    )
    expect(
        recover_source.index("_commit_recovery_nonempty") < recover_source.index("recovery_dispatch_gateway.launch"),
        "recovery POST can occur before protected authorization/attempt CAS",
    )
    expect(
        recover_source.count("recovery_dispatch_gateway.launch") == 1
        and "if result.get(\"acquired\") is not True:" in recover_source
        and "forbids another POST" in recover_source,
        "recovery replay does not remain lookup-only and one-shot",
    )
    expect(
        'StoreMutation("create_immutable", route["receipt_path"]' in seal_source
        and 'route = recovery_route(snapshot)' in seal_source,
        "recovery run is not sealed by immutable receipt",
    )
    expect(
        '"WAITING_EXTERNAL"' in boundary_source and "last_sequence" in boundary_source
        and "HISTORICAL_WORKER_RECEIPT" in boundary_source
        and "worker.callback.recorded" in boundary_source
        and "persist.confirmed" in boundary_source,
        "historical seq12/no-callback/no-Persist boundary is incomplete",
    )
    print("- old-401/new-200 key fence and authorization/attempt/receipt CAS are fail closed")


def historical_worker_adoption_tests():
    """Recovery receipt is separate from the historical ordinary launch."""
    import v03_dogfood_full_composition as composition
    import v03_dogfood_scenario_runner as runner
    import v03_dogfood_post_run_finalizer as finalizer

    collector_source = inspect.getsource(composition.DogfoodRecoveryCollector.handle)
    runner_wait = inspect.getsource(runner._wait_current_dispatch)
    runner_collect = inspect.getsource(runner._collect_next)
    finalizer_bindings = inspect.getsource(finalizer._durable_run_bindings)
    expect(
        'route = recovery_route(snapshot)' in collector_source
        and 'snapshot.get(route["receipt_path"])' in collector_source
        and "validate_recovery_execution_seal" in collector_source
        and "recovery_dispatch_key" in collector_source
        and "37204777409" in collector_source
        and "gh-aw-recovery-callback-" in collector_source,
        "recovery collector does not bind old launch, sealed successor and callback identity",
    )
    expect(
        "recovery_result_source" in runner_wait
        and "recovery_collector" in runner_collect,
        "scenario runner can route historical claim only through recovery-specific authority",
    )
    expect(
        "recovery_sealed" in finalizer_bindings
        and "recovery_result_source" in finalizer_bindings
        and 'str(run_id) != "37204777409"' in finalizer_bindings,
        "finalizer conflates historical failed receipt with sealed recovery run",
    )
    expect(
        composition.RECOVERY_DEVELOPER_WORKFLOW
        == "ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml",
        "recovery Developer workflow identity drifted",
    )
    print("- scenario collector and finalizer keep historical and recovery receipts distinct")


def historical_worker_evidence_tests():
    """Exercise actual five-job history shapes without constructing authority."""
    import base64
    from copy import deepcopy
    import json
    from pathlib import Path

    w, h = driver_subject.HISTORICAL_FAILED_WORKER, driver_subject.HISTORICAL_PREHTTP_RECOVERY
    run = json.loads(r'''{"id":37204777409,"workflow_id":329456712,"event":"workflow_dispatch","head_branch":"main","head_sha":"5ed049a4cce9c39a42385337da35a46dcaf378eb","path":".github/workflows/ai-sdlc-gh-aw-worker.lock.yml","display_title":"AI-SDLC gh-aw dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1","run_attempt":1,"status":"completed","conclusion":"failure","updated_at":"2026-10-04T13:14:20Z","repository":{"full_name":"DREAM-XIN/ai-sdlc"}}''')
    jobs = json.loads(r'''{"total_count":5,"jobs":[{"id":111443581703,"name":"activation","run_id":37204777409,"run_attempt":1,"head_sha":"5ed049a4cce9c39a42385337da35a46dcaf378eb","status":"completed","conclusion":"success","steps":[{"name":"Set up job","status":"completed","conclusion":"success","number":1},{"name":"Setup Scripts","status":"completed","conclusion":"success","number":2},{"name":"Mask OTLP telemetry headers","status":"completed","conclusion":"success","number":3},{"name":"Generate agentic run info","status":"completed","conclusion":"success","number":4},{"name":"Restore daily AIC scan observations","status":"completed","conclusion":"success","number":5},{"name":"Check daily workflow token guardrail","status":"completed","conclusion":"success","number":6},{"name":"Publish daily AIC scan observations","status":"completed","conclusion":"success","number":7},{"name":"Validate COPILOT_GITHUB_TOKEN secret","status":"completed","conclusion":"success","number":8},{"name":"Check for OAuth tokens","status":"completed","conclusion":"success","number":9},{"name":"Checkout .github and .agents folders","status":"completed","conclusion":"success","number":10},{"name":"Save agent config folders for base branch restoration","status":"completed","conclusion":"success","number":11},{"name":"Check workflow lock file","status":"completed","conclusion":"success","number":12},{"name":"Check compile-agentic version","status":"completed","conclusion":"success","number":13},{"name":"Log runtime features","status":"completed","conclusion":"skipped","number":14},{"name":"Create prompt with built-in context","status":"completed","conclusion":"success","number":15},{"name":"Interpolate variables and render templates","status":"completed","conclusion":"success","number":16},{"name":"Substitute placeholders","status":"completed","conclusion":"success","number":17},{"name":"Validate prompt placeholders","status":"completed","conclusion":"success","number":18},{"name":"Print prompt","status":"completed","conclusion":"success","number":19},{"name":"Upload info artifact","status":"completed","conclusion":"success","number":20},{"name":"Stage prompt files for artifact upload","status":"completed","conclusion":"success","number":21},{"name":"Upload activation artifact","status":"completed","conclusion":"success","number":22},{"name":"Post Checkout .github and .agents folders","status":"completed","conclusion":"success","number":43},{"name":"Post Setup Scripts","status":"completed","conclusion":"success","number":44},{"name":"Complete job","status":"completed","conclusion":"success","number":45}]},{"id":111443640237,"name":"agent","run_id":37204777409,"run_attempt":1,"head_sha":"5ed049a4cce9c39a42385337da35a46dcaf378eb","status":"completed","conclusion":"failure","steps":[{"name":"Set up job","status":"completed","conclusion":"success","number":1},{"name":"Setup Scripts","status":"completed","conclusion":"success","number":2},{"name":"Set runtime paths","status":"completed","conclusion":"success","number":3},{"name":"Mask OTLP telemetry headers","status":"completed","conclusion":"success","number":4},{"name":"Check OTLP telemetry configuration","status":"completed","conclusion":"success","number":5},{"name":"Generate GitHub App token for checkout (0)","status":"completed","conclusion":"failure","number":6},{"name":"Checkout repository","status":"completed","conclusion":"skipped","number":7},{"name":"Checkout dream-xin/ai-sdlc","status":"completed","conclusion":"skipped","number":8},{"name":"Fetch additional refs for dream-xin/ai-sdlc","status":"completed","conclusion":"skipped","number":9},{"name":"Build checkout manifest for safe-outputs handlers","status":"completed","conclusion":"skipped","number":10},{"name":"Initialize agent execution evidence","status":"completed","conclusion":"skipped","number":11},{"name":"Create gh-aw temp directory","status":"completed","conclusion":"skipped","number":12},{"name":"Configure gh CLI for GitHub Enterprise","status":"completed","conclusion":"skipped","number":13},{"name":"Download activation artifact","status":"completed","conclusion":"skipped","number":14},{"name":"Configure Git credentials","status":"completed","conclusion":"skipped","number":15},{"name":"Checkout PR branch","status":"completed","conclusion":"skipped","number":16},{"name":"Install GitHub Copilot CLI","status":"completed","conclusion":"skipped","number":17},{"name":"Install AWF binary","status":"completed","conclusion":"skipped","number":18},{"name":"Generate GitHub App token","status":"completed","conclusion":"skipped","number":19},{"name":"Determine automatic lockdown mode for GitHub MCP Server","status":"completed","conclusion":"skipped","number":20},{"name":"Restore agent config folders from base branch","status":"completed","conclusion":"skipped","number":21},{"name":"Restore inline sub-agents from activation artifact","status":"completed","conclusion":"skipped","number":22},{"name":"Restore inline skills from activation artifact","status":"completed","conclusion":"skipped","number":23},{"name":"Download container images","status":"completed","conclusion":"skipped","number":24},{"name":"Prepare Safe Outputs Directories","status":"completed","conclusion":"skipped","number":25},{"name":"Generate Safe Outputs Config","status":"completed","conclusion":"skipped","number":26},{"name":"Generate Safe Outputs Tools","status":"completed","conclusion":"skipped","number":27},{"name":"Start MCP Gateway","status":"completed","conclusion":"skipped","number":28},{"name":"Mount MCP servers as CLIs","status":"completed","conclusion":"skipped","number":29},{"name":"Clean credentials","status":"completed","conclusion":"skipped","number":30},{"name":"Audit pre-agent workspace","status":"completed","conclusion":"skipped","number":31},{"name":"Execute GitHub Copilot CLI","status":"completed","conclusion":"skipped","number":32},{"name":"Detect agent errors","status":"completed","conclusion":"success","number":33},{"name":"Configure Git credentials","status":"completed","conclusion":"skipped","number":34},{"name":"Copy Copilot session state files to logs","status":"completed","conclusion":"success","number":35},{"name":"Stop MCP Gateway","status":"completed","conclusion":"success","number":36},{"name":"Redact secrets in logs","status":"completed","conclusion":"success","number":37},{"name":"Append agent step summary","status":"completed","conclusion":"success","number":38},{"name":"Copy Safe Outputs","status":"completed","conclusion":"success","number":39},{"name":"Ingest agent output","status":"completed","conclusion":"success","number":40},{"name":"Parse agent logs for step summary","status":"completed","conclusion":"success","number":41},{"name":"Parse MCP Gateway logs for step summary","status":"completed","conclusion":"success","number":42},{"name":"Print firewall logs","status":"completed","conclusion":"success","number":43},{"name":"Parse token usage for step summary","status":"completed","conclusion":"success","number":44},{"name":"Print AWF reflect summary","status":"completed","conclusion":"success","number":45},{"name":"Generate observability summary","status":"completed","conclusion":"success","number":46},{"name":"Write agent output placeholder if missing","status":"completed","conclusion":"success","number":47},{"name":"Upload agent output fallback artifact","status":"completed","conclusion":"success","number":48},{"name":"Upload agent artifacts","status":"completed","conclusion":"success","number":49},{"name":"Post Generate GitHub App token for checkout (0)","status":"completed","conclusion":"success","number":97},{"name":"Post Setup Scripts","status":"completed","conclusion":"success","number":98},{"name":"Complete job","status":"completed","conclusion":"success","number":99}]},{"id":111443685002,"name":"detection","run_id":37204777409,"run_attempt":1,"head_sha":"5ed049a4cce9c39a42385337da35a46dcaf378eb","status":"completed","conclusion":"success","steps":[{"name":"Set up job","status":"completed","conclusion":"success","number":1},{"name":"Setup Scripts","status":"completed","conclusion":"success","number":2},{"name":"Download activation artifact","status":"completed","conclusion":"success","number":3},{"name":"Download agent output artifact","status":"completed","conclusion":"success","number":4},{"name":"Setup agent output environment variable","status":"completed","conclusion":"success","number":5},{"name":"Checkout repository for patch context","status":"completed","conclusion":"skipped","number":6},{"name":"Initialize detection execution evidence","status":"completed","conclusion":"success","number":7},{"name":"Clear inherited Copilot session state","status":"completed","conclusion":"success","number":8},{"name":"Clean stale firewall files from agent artifact","status":"completed","conclusion":"success","number":9},{"name":"Download container images","status":"completed","conclusion":"success","number":10},{"name":"Check if detection needed","status":"completed","conclusion":"success","number":11},{"name":"Clear MCP Config for detection","status":"completed","conclusion":"skipped","number":12},{"name":"Prepare threat detection files","status":"completed","conclusion":"skipped","number":13},{"name":"Setup threat detection","status":"completed","conclusion":"skipped","number":14},{"name":"Ensure threat-detection directory and log","status":"completed","conclusion":"skipped","number":15},{"name":"Install AWF binary","status":"completed","conclusion":"success","number":16},{"name":"Setup Node.js","status":"completed","conclusion":"success","number":17},{"name":"Install GitHub Copilot CLI","status":"completed","conclusion":"success","number":18},{"name":"Install threat-detect binary","status":"completed","conclusion":"skipped","number":19},{"name":"Execute threat detection with AWF","status":"completed","conclusion":"skipped","number":20},{"name":"Render detection log","status":"completed","conclusion":"skipped","number":21},{"name":"Copy detection firewall logs","status":"completed","conclusion":"skipped","number":22},{"name":"Parse threat detection token usage for step summary","status":"completed","conclusion":"success","number":23},{"name":"Upload threat detection artifact","status":"completed","conclusion":"success","number":24},{"name":"Conclude threat detection","status":"completed","conclusion":"success","number":25},{"name":"Post Setup Node.js","status":"completed","conclusion":"success","number":49},{"name":"Post Setup Scripts","status":"completed","conclusion":"success","number":50},{"name":"Complete job","status":"completed","conclusion":"success","number":51}]},{"id":111443824035,"name":"safe_outputs","run_id":37204777409,"run_attempt":1,"head_sha":"5ed049a4cce9c39a42385337da35a46dcaf378eb","status":"completed","conclusion":"failure","steps":[{"name":"Set up job","status":"completed","conclusion":"success","number":1},{"name":"Setup Scripts","status":"completed","conclusion":"success","number":2},{"name":"Mask OTLP telemetry headers","status":"completed","conclusion":"success","number":3},{"name":"Download agent output artifact","status":"completed","conclusion":"success","number":4},{"name":"Setup agent output environment variable","status":"completed","conclusion":"success","number":5},{"name":"Download patch artifact","status":"completed","conclusion":"success","number":6},{"name":"Generate GitHub App token","status":"completed","conclusion":"failure","number":7},{"name":"Generate GitHub App token for checkout (0)","status":"completed","conclusion":"skipped","number":8},{"name":"Generate safe_outputs GitHub App token for checkout (0)","status":"completed","conclusion":"skipped","number":9},{"name":"Checkout repository","status":"completed","conclusion":"skipped","number":10},{"name":"Checkout dream-xin/ai-sdlc","status":"completed","conclusion":"skipped","number":11},{"name":"Fetch additional refs for dream-xin/ai-sdlc","status":"completed","conclusion":"skipped","number":12},{"name":"Configure Git credentials","status":"completed","conclusion":"skipped","number":13},{"name":"Configure GH_HOST for enterprise compatibility","status":"completed","conclusion":"skipped","number":14},{"name":"Process Safe Outputs","status":"completed","conclusion":"skipped","number":15},{"name":"Upload Safe Outputs Items","status":"completed","conclusion":"success","number":16},{"name":"Post Generate GitHub App token","status":"completed","conclusion":"success","number":31},{"name":"Post Setup Scripts","status":"completed","conclusion":"success","number":32},{"name":"Complete job","status":"completed","conclusion":"success","number":33}]},{"id":111443899606,"name":"conclusion","run_id":37204777409,"run_attempt":1,"head_sha":"5ed049a4cce9c39a42385337da35a46dcaf378eb","status":"completed","conclusion":"failure","steps":[{"name":"Set up job","status":"completed","conclusion":"success","number":1},{"name":"Setup Scripts","status":"completed","conclusion":"success","number":2},{"name":"Dispatch structured worker result after Draft PR","status":"completed","conclusion":"failure","number":3},{"name":"Generate GitHub App token","status":"completed","conclusion":"skipped","number":4},{"name":"Download agent output artifact","status":"completed","conclusion":"skipped","number":5},{"name":"Setup agent output environment variable","status":"completed","conclusion":"skipped","number":6},{"name":"Download detection artifact","status":"completed","conclusion":"skipped","number":7},{"name":"Download Safe Outputs Items Manifest","status":"completed","conclusion":"success","number":8},{"name":"Collect usage artifact files","status":"completed","conclusion":"success","number":9},{"name":"Upload usage artifact","status":"completed","conclusion":"success","number":10},{"name":"Wait before retrying usage artifact upload","status":"completed","conclusion":"skipped","number":11},{"name":"Retry upload usage artifact","status":"completed","conclusion":"skipped","number":12},{"name":"Process no-op messages","status":"completed","conclusion":"skipped","number":13},{"name":"Log detection run","status":"completed","conclusion":"skipped","number":14},{"name":"Record missing tool","status":"completed","conclusion":"skipped","number":15},{"name":"Record incomplete","status":"completed","conclusion":"skipped","number":16},{"name":"Handle agent failure","status":"completed","conclusion":"failure","number":17},{"name":"Report failed jobs","status":"completed","conclusion":"failure","number":18},{"name":"Post Setup Scripts","status":"completed","conclusion":"success","number":36},{"name":"Complete job","status":"completed","conclusion":"success","number":37}]}]}''')
    raw = (Path(__file__).resolve().parents[1] / ".github/workflows" / h["workflow_file"]).read_bytes()
    source = dict(sha=w["workflow_blob_sha"], encoding="base64", type="file",
                  path=".github/workflows/" + h["workflow_file"],
                  content=base64.encodebytes(raw).decode("ascii"))
    log = ("2026-10-04T13:14:15Z   FEATURE_ID: " + h["feature_id"] + chr(10)
           + "2026-10-04T13:14:15Z   EXPECTED_REVISION: 1" + chr(10)
           + "2026-10-04T13:14:15Z   TARGET_REF: " + h["target_ref"] + chr(10)
           + "2026-10-04T13:14:15Z   TARGET_REPOSITORY: dream-xin/ai-sdlc" + chr(10)
           + "2026-10-04T13:14:15Z   PR_URL: " + chr(10)).encode("utf-8")
    run_path = f"/actions/runs/{w['run_id']}"
    jobs_path = run_path + "/attempts/1/jobs?per_page=100"
    source_path = "/contents/.github/workflows/" + h["workflow_file"] + "?ref=" + w["head_sha"]
    calls = []

    def collect(run_override=None, jobs_override=None, source_override=None, log_override=None,
                drift_run=False, drift_jobs=False):
        calls.clear()
        run_value = run_override if run_override is not None else run
        jobs_value = jobs_override if jobs_override is not None else jobs
        source_value = source_override if source_override is not None else source
        counts = {}

        def read_json(suffix):
            calls.append(("GET", suffix))
            counts[suffix] = counts.get(suffix, 0) + 1
            if suffix == run_path:
                value = deepcopy(run_value)
                if drift_run and counts[suffix] == 2:
                    value["updated_at"] = "2026-10-08T03:00:00Z"
                return value
            if suffix == jobs_path:
                value = deepcopy(jobs_value)
                if drift_jobs and counts[suffix] == 2:
                    value["jobs"][0]["steps"][0]["conclusion"] = "failure"
                return value
            if suffix == source_path:
                return deepcopy(source_value)
            raise AssertionError("proof reader escaped its exact GET inventory: " + suffix)

        def read_bytes(suffix):
            calls.append(("GET", suffix))
            expect(suffix == "/actions/jobs/111443899606/logs", "log source job drifted")
            return log_override if log_override is not None else log

        return driver_subject.collect_historical_worker_recovery_evidence(
            read_json=read_json, read_bytes=read_bytes
        )

    # Exercise the production wrapper too, including the real reader construction.
    from unittest.mock import patch
    from operator_vertical_gh_aw_github_source import TargetScopedGitHubActionsGhAwResultSource
    http_calls = []
    def http(**kwargs):
        http_calls.append(dict(kwargs))
        expect(kwargs["method"] == "GET", "production observation attempted an HTTP mutation")
        prefix = "https://api.github.com/repos/dream-xin/ai-sdlc"
        expect(kwargs["url"].startswith(prefix), "production reader escaped the fixed repository")
        suffix = kwargs["url"][len(prefix):]
        payloads = {run_path: run, jobs_path: jobs, source_path: source}
        if suffix == "/actions/jobs/111443899606/logs":
            return 200, {}, log
        expect(suffix in payloads, "production reader escaped its bounded GET inventory")
        return 200, {}, json.dumps(payloads[suffix]).encode()
    with patch.object(TargetScopedGitHubActionsGhAwResultSource, "_http", side_effect=http):
        observed = driver_subject.observe_historical_worker_for_review(
            actions_read_token="private-observation-test-token"
        )
    expect(observed == collect() and len(http_calls) == 6,
           "production reader did not reproduce the exact pure observation")
    expect("private-observation-test-token" not in repr(observed),
           "read credential leaked into observation")

    proof = collect()
    expect(len(calls) == 6 and all(method == "GET" for method, _ in calls),
           "historical observation escaped the bounded GET-only reader")
    expect(proof["runtime_receipt_identity"] == "37204777409"
           and proof["stable_reads"] and proof["callback_pr_url_empty"],
           "historical observation lost exact source/receipt identity")
    expect(all(proof[key] is False for key in (
        "provider_invalidation", "future_attempts_fenced", "recovery_authority", "release_eligible"
    )), "historical skipped steps were promoted to launch/release authority")
    expect(proof["observation_digest"] == collect()["observation_digest"],
           "identical stable history did not have a deterministic digest")

    def must_reject(**kwargs):
        try:
            collect(**kwargs)
        except V03DogfoodRuntimeDriverError:
            return
        raise AssertionError("malformed/drifted historical evidence was accepted")

    for key, value in (
        ("run_attempt", 2), ("run_attempt", True), ("status", "in_progress"),
        ("head_sha", "a" * 40), ("display_title", "AI-SDLC gh-aw wrong-key"),
        ("repository", {"full_name": "other/repository"}),
    ):
        must_reject(run_override={**run, key: value})
    must_reject(source_override={**source, "sha": "a" * 40})
    must_reject(source_override={**source, "content": base64.b64encode(b"changed bytes").decode()})
    must_reject(source_override={**source, "content": "not base64!"})
    must_reject(source_override={**source, "type": "symlink"})
    must_reject(jobs_override={**jobs, "total_count": 6})
    missing = deepcopy(jobs)
    missing["jobs"].pop()
    must_reject(jobs_override=missing)
    duplicate = deepcopy(jobs)
    duplicate["jobs"][-1] = deepcopy(duplicate["jobs"][0])
    must_reject(jobs_override=duplicate)
    for job_name, step_name in (
        ("agent", "Execute GitHub Copilot CLI"),
        ("safe_outputs", "Process Safe Outputs"),
        ("detection", "Execute threat detection with AWF"),
    ):
        changed = deepcopy(jobs)
        job = next(j for j in changed["jobs"] if j["name"] == job_name)
        next(s for s in job["steps"] if s["name"] == step_name)["conclusion"] = "success"
        must_reject(jobs_override=changed)
    must_reject(log_override=log.replace(b"PR_URL: ", b"PR_URL: https://github.com/DREAM-XIN/ai-sdlc/pull/999"))
    must_reject(log_override=log.replace(b"EXPECTED_REVISION: 1", b"EXPECTED_REVISION: 2"))
    must_reject(drift_run=True)
    must_reject(drift_jobs=True)
    print("- historical Worker proof brackets exact source/jobs/logs with six GETs and grants no recovery authority")



def recovery_lock_transform_tests(root):
    """Prove the checked-in lock is one deterministic transform of strict compiler output."""
    import hashlib
    import json
    path = root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml"
    hardened = path.read_text()
    lines = hardened.splitlines(keepends=True)
    expect(len(lines) > 3 and lines[1].startswith("# ai-sdlc-recovery-lock-transform: "),
           "recovery Developer lock lacks deterministic transform provenance")
    provenance = json.loads(lines[1].split(": ", 1)[1])
    expect(provenance == {
        "schema": "ai-sdlc.v03-recovery-lock-transform/v1",
        "compiler": "gh-aw-v0.89.21-strict",
        "upstream_blob_sha": "9de8b30b827c956f18f4c65c1a77e3eac3f173e3",
        "persist_credentials_false": 2,
        "removed_trigger_lines": 3,
        "removed_trigger_references": 4,
        "body_hash_from": "2402c641e84ed7201f98975e08d7eacbe1c40a7bdfcd862df1648d638f0026cc",
        "body_hash_to": "2402c641e84ed7201f98975e08d7eacbe1c40a7bdfcd862df1648d638f0026cc",
    }, "recovery lock transform provenance drifted")
    body = "".join(lines[:1] + lines[2:])
    expect("GH_AW_CI_TRIGGER_TOKEN" not in body and "persist-credentials: true" not in body,
           "hardened recovery lock retained forbidden credential surface")
    upstream = body
    upstream = upstream.replace(
        '"DEEPSEEK_API_KEY","GH_AW_DEFAULT_OTLP_ENDPOINT"',
        '"DEEPSEEK_API_KEY","GH_AW_CI_TRIGGER_TOKEN","GH_AW_DEFAULT_OTLP_ENDPOINT"', 1)
    upstream = upstream.replace(
        "#   - GH_AW_DEFAULT_OTLP_ENDPOINT\n",
        "#   - GH_AW_CI_TRIGGER_TOKEN\n#   - GH_AW_DEFAULT_OTLP_ENDPOINT\n", 1)
    safe_checkout = """      - name: Checkout repository
        if: (!cancelled()) && needs.agent.result != 'skipped' && contains(needs.agent.outputs.output_types, 'create_pull_request')
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          token: TOKEN_EXPR
""".replace("TOKEN_EXPR", "$" + "{{ secrets.GITHUB_TOKEN }}")
    safe_subcheckout = """      - name: Checkout dream-xin/ai-sdlc into ai-sdlc
        if: (!cancelled()) && needs.agent.result != 'skipped' && contains(needs.agent.outputs.output_types, 'create_pull_request')
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
"""
    expect(upstream.count(safe_checkout) == 1 and upstream.count(safe_subcheckout) == 1,
           "recovery lock checkout transform sites drifted")
    upstream = upstream.replace(safe_checkout, safe_checkout.replace("persist-credentials: false", "persist-credentials: true"), 1)
    upstream = upstream.replace(safe_subcheckout, safe_subcheckout.replace("persist-credentials: false", "persist-credentials: true"), 1)
    handler = '          GH_AW_SAFE_OUTPUTS_HANDLER_CONFIG: '
    pos = upstream.find(handler)
    expect(pos >= 0, "recovery lock lost Safe Outputs handler")
    end = upstream.find("\n", pos)
    trigger_line = "          GH_AW_CI_TRIGGER_TOKEN: $" + "{{ secrets.GH_AW_CI_TRIGGER_TOKEN }}\n"
    upstream = upstream[:end + 1] + trigger_line + upstream[end + 1:]
    upstream = upstream.replace(
        provenance["body_hash_to"], provenance["body_hash_from"], 1)
    raw = upstream.encode()
    git_blob = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
    expect(git_blob == provenance["upstream_blob_sha"],
           "hardened recovery lock cannot reconstruct exact strict compiler output")
    transformed = upstream
    transformed = transformed.replace(',"GH_AW_CI_TRIGGER_TOKEN"', "", 1)
    transformed = transformed.replace("#   - GH_AW_CI_TRIGGER_TOKEN\n", "", 1)
    transformed = transformed.replace("persist-credentials: true", "persist-credentials: false", 2)
    transformed = transformed.replace(trigger_line, "", 1)
    transformed = transformed.replace(
        provenance["body_hash_from"], provenance["body_hash_to"], 1)
    source = (root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.md").read_text()
    source_body = source.split("\n---\n", 1)[1].rstrip("\n")
    expect(hashlib.sha256(source_body.encode()).hexdigest() == provenance["body_hash_to"],
           "recovery source body is not bound to transformed lock metadata")
    expect(transformed == body, "declared recovery lock transform is not deterministic/reversible")
    print("- recovery Developer lock is an exact reversible hardening of gh-aw v0.89.21 strict output")



def _recovery_worker_preparation_contract(source, body, compiled):
    """Check the parsed production workflow, not a second detector implementation."""
    from copy import deepcopy
    import subprocess
    import os
    safe = source["safe-outputs"]
    threat = safe["threat-detection"]
    expect(threat.get("enabled") is True and threat.get("continue-on-error") is False,
           "Developer source permits absent or advisory-only threat detection")
    prompt = str(threat.get("prompt") or "")
    for fragment in (
        "full security analysis", "every built-in threat criterion and verdict rule",
        "Do not repeat an identical failed inspection command",
        "Missing or uninspectable required evidence is not evidence of safety",
        "never invent a clean verdict or suppress a finding",
    ):
        expect(fragment in prompt, "bounded detector prompt lost: " + fragment)
    expect(source["engine"]["id"] == "copilot" and source["engine"]["model"] == "deepseek-chat",
           "preparation changed the frozen Developer engine/model")
    select = body.index('cd "$GITHUB_WORKSPACE/ai-sdlc"')
    inspect = body.index("1. Decode and inspect")
    expect(select < inspect and "git rev-parse --show-toplevel" in body[:inspect],
           "Developer inspects the outer checkout before proving exact nested workspace")
    for fragment in (
        "wait for every inspection command already started and inspect its result",
        "Do not emit a failure report while a filesystem search or other inspection is still pending",
        "call `report_incomplete` once and terminate the task immediately",
        "no later edits, branch creation, `create_pull_request`, or completion report",
        "Do not fetch, change credentials, or disable TLS verification",
        "Do not pass or waive any Gate. Do not merge or release.",
    ):
        expect(fragment in body, "Developer terminal/workspace contract lost: " + fragment)

    jobs = compiled["jobs"]
    checkouts = [step for job in jobs.values() for step in job.get("steps", [])
                 if str(step.get("uses") or "").startswith("actions/checkout@")]
    expect(len(checkouts) == 6 and all(step.get("with", {}).get("persist-credentials") is False
                                      for step in checkouts),
           "every generated checkout must explicitly disable credential persistence as boolean false")
    nested = [step for step in jobs["agent"]["steps"]
              if step.get("with", {}).get("repository") == "dream-xin/ai-sdlc"]
    expect(len(nested) == 1 and nested[0]["with"].get("path") == "ai-sdlc"
           and nested[0]["with"].get("ref") == "${{ inputs.target_ref }}"
           and nested[0]["with"].get("fetch-depth") == 0,
           "actual Developer nested checkout lost exact target ref/full ancestry")
    detection = jobs["detection"]
    detect_steps = detection["steps"]
    setup = next(step for step in detect_steps if step.get("name") == "Setup threat detection")
    execution = next(step for step in detect_steps if step.get("id") == "detection_agentic_execution")
    conclude = next(step for step in detect_steps if step.get("id") == "detection_conclusion")
    install = next(step for step in detect_steps if step.get("id") == "threat_detect_install")
    for step in (setup, conclude):
        expect(step.get("env", {}).get("GH_AW_DETECTION_CONTINUE_ON_ERROR") == "false",
               "compiled detector strict-mode environment drifted")
    expect(conclude.get("continue-on-error", False) is False and conclude.get("if") == "always()",
           "compiled detector conclusion can swallow failure or skip error handling")
    expect("conclude_threat_detection.sh" in conclude["run"],
           "compiled detector no longer uses the pinned semantic conclusion parser")
    expect(execution.get("env", {}).get("CUSTOM_PROMPT", "").strip() == prompt.strip(),
           "bounded prompt never reaches the actual external detector engine")
    expect(detection.get("timeout-minutes") == 10 and execution.get("timeout-minutes") == 10,
           "preparation changed the surrounding detector job/step budget")
    expect("--engine-timeout" not in execution["run"]
           and "--prompt-template" not in execution["run"]
           and "THREAT_DETECTION_ENGINE_TIMEOUT" not in execution.get("env", {}),
           "preparation replaced full safety template or pinned five-minute engine budget")
    expect("install_threat_detect_binary.sh\" v0.5.2 " in install["run"]
           and "--sha256-amd64 b4ecda6a8f1ee09913c40b58e5e9d3337d2173618d41b1bfdef9207e4e7959b9" in install["run"]
           and "--sha256-arm64 f6260a0f9ad72bcb67c7af19c4ce262ca34e2c3d5ccbf912832a8bd277200904" in install["run"],
           "preparation changed the detector release or compiler-pinned binary bytes")
    expect(detection["outputs"].get("detection_success") ==
           "${{ steps.detection_conclusion.outputs.success }}"
           and detection["outputs"].get("detection_conclusion") ==
           "${{ steps.detection_conclusion.outputs.conclusion }}",
           "Safe Outputs semantic verdict is not wired to the actual conclude step")

    name = "Require first attempt and affirmative detection before Safe Outputs effects"
    expected_env = {
        "RUN_ATTEMPT": "${{ github.run_attempt }}",
        "DETECTION_SUCCESS": "${{ needs.detection.outputs.detection_success }}",
        "DETECTION_CONCLUSION": "${{ needs.detection.outputs.detection_conclusion }}",
    }
    source_guards = [step for step in safe.get("steps", []) if step.get("name") == name]
    safe_job = jobs["safe_outputs"]
    expect("detection" in safe_job["needs"] and "needs.detection.result == 'success'" in safe_job["if"],
           "Safe Outputs no longer requires the detector job to succeed")
    safe_steps = safe_job["steps"]
    guards = [step for step in safe_steps if step.get("name") == name]
    expect(len(source_guards) == len(guards) == 1, "one exact source/compiled before-effect guard is required")
    guard = guards[0]
    expect(guard.get("env") == expected_env and source_guards[0].get("env") == expected_env,
           "before-effect guard accepts caller-selected detector or attempt evidence")
    expect(guard["run"].strip() == source_guards[0]["run"].strip()
           and guard.get("continue-on-error", False) is False and not guard.get("if"),
           "compiled before-effect guard can skip, swallow failure, or diverge from source")
    process_index = next(i for i, step in enumerate(safe_steps) if step.get("id") == "process_safe_outputs")
    expect(safe_steps.index(guard) < process_index, "semantic verdict checked only after Safe Outputs effects")
    expect(not safe_steps[process_index].get("if"),
           "Safe Outputs effects can bypass a failed semantic guard")

    # Run exactly the checked-in shell guard against semantic output combinations.
    for attempt in ("1", "2", "", "0"):
        for success in ("true", "false", "", "unknown"):
            for conclusion in ("success", "failure", "warning", "skipped", "", "unknown"):
                env = {"PATH": os.defpath, "RUN_ATTEMPT": attempt,
                       "DETECTION_SUCCESS": success, "DETECTION_CONCLUSION": conclusion}
                result = subprocess.run(["bash", "-c", guard["run"]], env=env,
                                        capture_output=True, text=True, timeout=5)
                expect((result.returncode == 0) == (
                    attempt == "1" and success == "true" and conclusion == "success"),
                    "actual Safe Outputs guard accepted unknown/failed/skipped/absent verdict or rerun")
    agent_steps = jobs["agent"]["steps"]
    attempt_guard = next(step for step in agent_steps if step.get("name") == "Reject rerun before model execution")
    engine_index = next(i for i, step in enumerate(agent_steps) if step.get("id") == "agentic_execution")
    expect(not agent_steps[engine_index].get("if"), "model execution can bypass a failed attempt guard")
    expect(agent_steps.index(attempt_guard) < engine_index
           and attempt_guard.get("env") == {"RUN_ATTEMPT": "${{ github.run_attempt }}"}
           and attempt_guard.get("continue-on-error", False) is False and not attempt_guard.get("if"),
           "rerun can reach model execution before exact first-attempt check")
    for attempt in ("1", "2", "", "0"):
        result = subprocess.run(["bash", "-c", attempt_guard["run"]],
            env={"PATH": os.defpath, "RUN_ATTEMPT": attempt}, capture_output=True, timeout=5)
        expect((result.returncode == 0) == (attempt == "1"), "actual model guard admitted a rerun")
    return guard, conclude


def recovery_worker_preparation_contract_tests(root):
    from copy import deepcopy
    import yaml
    source_text = (root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.md").read_text()
    _, frontmatter, body = source_text.split("---\n", 2)
    source = yaml.safe_load(frontmatter)
    compiled = yaml.safe_load((root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml").read_text())
    _recovery_worker_preparation_contract(source, body, compiled)
    def step(document, job, name):
        return next(row for row in document["jobs"][job]["steps"] if row.get("name") == name)
    mutations = (
        ("model bypasses attempt guard", lambda s, c: next(row for row in c["jobs"]["agent"]["steps"] if row.get("id") == "agentic_execution").update({"if": "always()"})),
        ("effects bypass semantic guard", lambda s, c: next(row for row in c["jobs"]["safe_outputs"]["steps"] if row.get("id") == "process_safe_outputs").update({"if": "always()"})),
        ("checkout retains credentials", lambda s, c: step(c, "agent", "Checkout repository")["with"].update({"persist-credentials": True})),
        ("nested checkout path", lambda s, c: step(c, "agent", "Checkout dream-xin/ai-sdlc into ai-sdlc")["with"].update(path="outer")),
        ("warn-mode source", lambda s, c: s["safe-outputs"]["threat-detection"].update({"continue-on-error": True})),
        ("conclude swallows failure", lambda s, c: step(c, "detection", "Conclude threat detection").update({"continue-on-error": True})),
        ("conclude skips failure", lambda s, c: step(c, "detection", "Conclude threat detection").update({"if": "success()"})),
        ("conclude warn-mode env", lambda s, c: step(c, "detection", "Conclude threat detection")["env"].update(GH_AW_DETECTION_CONTINUE_ON_ERROR="true")),
        ("execution loses bounded prompt", lambda s, c: step(c, "detection", "Execute threat detection with AWF")["env"].pop("CUSTOM_PROMPT", None)),
        ("execution disables budget", lambda s, c: step(c, "detection", "Execute threat detection with AWF")["env"].update(THREAT_DETECTION_ENGINE_TIMEOUT="0")),
        ("guard swallows failure", lambda s, c: step(c, "safe_outputs", "Require first attempt and affirmative detection before Safe Outputs effects").update({"continue-on-error": True})),
        ("guard skips effects", lambda s, c: step(c, "safe_outputs", "Require first attempt and affirmative detection before Safe Outputs effects").update({"if": "false"})),
        ("guard literal success", lambda s, c: step(c, "safe_outputs", "Require first attempt and affirmative detection before Safe Outputs effects")["env"].update(DETECTION_SUCCESS="true")),
        ("output wire drift", lambda s, c: c["jobs"]["detection"]["outputs"].update(detection_success="true")),
    )
    for label, mutate in mutations:
        s, c = deepcopy(source), deepcopy(compiled)
        mutate(s, c)
        try:
            _recovery_worker_preparation_contract(s, body, c)
        except (AssertionError, ValueError, KeyError, StopIteration):
            pass
        else:
            raise AssertionError("preparation regression missed " + label)
    for fragment in ('cd "$GITHUB_WORKSPACE/ai-sdlc"',
                     "wait for every inspection command already started and inspect its result",
                     "call `report_incomplete` once and terminate the task immediately"):
        try:
            _recovery_worker_preparation_contract(source, body.replace(fragment, "", 1), compiled)
        except (AssertionError, ValueError):
            pass
        else:
            raise AssertionError("preparation regression missed missing Worker guidance")
    # Corrupt both source and compiled guard identically: source/lock parity alone
    # must not pass when either semantic predicate or the attempt fence disappears.
    for line in ('test "$RUN_ATTEMPT" = 1', 'test "$DETECTION_SUCCESS" = true',
                 'test "$DETECTION_CONCLUSION" = success'):
        s, c = deepcopy(source), deepcopy(compiled)
        source_guard = s["safe-outputs"]["steps"][0]
        compiled_guard = step(c, "safe_outputs", source_guard["name"])
        source_guard["run"] = source_guard["run"].replace(line, ":")
        compiled_guard["run"] = compiled_guard["run"].replace(line, ":")
        try:
            _recovery_worker_preparation_contract(s, body, c)
        except AssertionError:
            pass
        else:
            raise AssertionError("real shell truth table missed removed semantic fence")
    print("- Developer source/compiled workspace, terminal report, strict detector and pre-effect guards validated")


def recovery_pinned_detector_semantic_tests(root, binary_path, conclude_script_path):
    """Hosted CI only: execute pinned real parser; never execute a model or Worker."""
    import hashlib
    import json
    import os
    import subprocess
    import tempfile
    from pathlib import Path
    import yaml
    binary_path, conclude_script_path = Path(binary_path).resolve(), Path(conclude_script_path).resolve()
    pins = {
        "b4ecda6a8f1ee09913c40b58e5e9d3337d2173618d41b1bfdef9207e4e7959b9",
        "f6260a0f9ad72bcb67c7af19c4ce262ca34e2c3d5ccbf912832a8bd277200904",
    }
    expect(binary_path.name == "threat-detect" and hashlib.sha256(binary_path.read_bytes()).hexdigest() in pins,
           "semantic tests require actual compiler-pinned threat-detect v0.5.2 bytes")
    wrapper = conclude_script_path.read_bytes()
    expect(hashlib.sha1(b"blob " + str(len(wrapper)).encode() + b"\0" + wrapper).hexdigest() ==
           "c72df00b31d59b67968c6e578bf069c616b0421e",
           "semantic tests require exact official conclude wrapper from setup924af5")
    compiled = yaml.safe_load((root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml").read_text())
    conclusion_step = next(step for step in compiled["jobs"]["detection"]["steps"]
                           if step.get("id") == "detection_conclusion")
    guard = next(step for step in compiled["jobs"]["safe_outputs"]["steps"]
                 if step.get("name") == "Require first attempt and affirmative detection before Safe Outputs effects")
    strict = conclusion_step["env"]["GH_AW_DETECTION_CONTINUE_ON_ERROR"]
    expect(strict == "false" and conclusion_step.get("continue-on-error", False) is False,
           "hosted semantic tests must use compiled strict-mode controls")
    safe = {"prompt_injection": False, "secret_leak": False, "malicious_patch": False, "reasons": []}
    cases = [
        ("affirmative", json.dumps(safe), "true", "success", "success", None, True),
        ("missing-verdict", None, "true", "success", "success", None, False),
        ("empty-verdict", "", "true", "success", "success", None, False),
        ("malformed-verdict", "{not-json", "true", "success", "success", None, False),
        ("missing-semantic-fields", "{}", "true", "success", "success", None, False),
        ("unknown-verdict", json.dumps(dict(safe, prompt_injection="unknown")), "true", "success", "success", None, False),
        ("null-verdict", json.dumps(dict(safe, secret_leak=None)), "true", "success", "success", None, False),
        ("detector-timeout", None, "true", "failure", "success",
         "THREAT_DETECTION_STATUS: status=error reason=engine_timeout\n", False),
        ("detector-cancelled", None, "true", "cancelled", "success", None, False),
        ("install-failure", json.dumps(safe), "true", "skipped", "failure", None, False),
        ("skipped-detector", None, "false", "skipped", "skipped", None, False),
        ("missing-run-detection", None, "", "skipped", "skipped", None, False),
    ]
    for category in ("prompt_injection", "secret_leak", "malicious_patch"):
        cases.append((category, json.dumps(dict(safe, **{category: True})),
                      "true", "success", "success", None, False))
    with tempfile.TemporaryDirectory(prefix="v03-detector-semantics-") as temp:
        base = Path(temp)
        for index, (label, content, required, outcome, installed, log, permit) in enumerate(cases):
            case = base / str(index)
            case.mkdir()
            result = case / "detection_result.json"
            if content is not None:
                result.write_text(content)
            if log is not None:
                (case / "detection.log").write_text(log)
            output, environment, summary = case / "output", case / "env", case / "summary"
            for path in (output, environment, summary):
                path.write_text("")
            env = {"PATH": str(binary_path.parent) + os.pathsep + os.defpath, "HOME": str(case),
                "RUN_DETECTION": required, "DETECTION_AGENTIC_EXECUTION_OUTCOME": outcome,
                "THREAT_DETECT_INSTALL_OUTCOME": installed,
                "GH_AW_DETECTION_CONTINUE_ON_ERROR": strict,
                "GITHUB_OUTPUT": str(output), "GITHUB_ENV": str(environment),
                "GITHUB_STEP_SUMMARY": str(summary)}
            completed = subprocess.run(["bash", str(conclude_script_path), str(result)],
                                       env=env, capture_output=True, text=True, timeout=20)
            values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
            guarded = subprocess.run(["bash", "-c", guard["run"]], env={
                "PATH": os.defpath, "RUN_ATTEMPT": "1",
                "DETECTION_SUCCESS": values.get("success", ""),
                "DETECTION_CONCLUSION": values.get("conclusion", ""),
            }, capture_output=True, text=True, timeout=5)
            expect((guarded.returncode == 0) is permit, "real parser-to-Safe-Outputs guard admitted " + label)
            if permit:
                expect(completed.returncode == 0 and values.get("success") == "true"
                       and values.get("conclusion") == "success", "actual safe verdict did not complete")
            elif required == "true":
                expect(completed.returncode != 0 and values.get("success") == "false"
                       and values.get("conclusion") == "failure", "strict real parser failed open: " + label)
            else:
                expect(values.get("conclusion") == "skipped", "real skipped verdict contract changed")
    print("- pinned real threat-detect parser fails closed for absent/unknown/timeout/threat verdicts before Safe Outputs")


def recovery_safe_output_artifact_fixture(*, run_id, source_head, pr):
    """A real ZIP/JSONL safe-output artifact carried only by fake HTTP."""
    import hashlib
    import io
    import json
    import zipfile
    row = {
        "type": "create_pull_request", "provider": "github",
        "number": pr["number"], "url": pr["html_url"], "id": pr["id"],
        "repo": "dream-xin/ai-sdlc", "metadata": {"node_id": pr["node_id"]},
        "timestamp": "2026-10-09T07:00:00Z",
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(zipfile.ZipInfo("safe-output-items.jsonl", (2026, 10, 9, 7, 0, 0)),
                         json.dumps(row, sort_keys=True) + "\n")
    raw = stream.getvalue()
    artifact_id = run_id + 100
    artifact = {
        "id": artifact_id, "name": "safe-outputs-items", "expired": False,
        "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "archive_download_url": f"https://api.github.com/repos/dream-xin/ai-sdlc/actions/artifacts/{artifact_id}/zip",
        "workflow_run": {
            "id": run_id, "head_sha": source_head, "head_branch": "main",
            "repository_id": 1326302284, "head_repository_id": 1326302284,
        },
    }
    return {"total_count": 1, "artifacts": [artifact]}, raw

def recovery_safe_output_source_tests():
    """Execute the recovery-only Safe Output resolver against realistic GitHub shapes."""
    import json
    from urllib.parse import urlparse
    from operator_vertical import VerticalInvariantError
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig
    from v03_dogfood_full_composition import (
        COLLECTOR_IDENTITY, RECOVERY_DEVELOPER_WORKFLOW, RecoverySafeOutputGhAwResultSource,
    )
    run_id = 40000000001
    key = "dispatch-" + "a" * 40
    source_head = "1" * 40
    candidate_head = "2" * 40
    feature = "F-OPERATOR-V03-DOGFOOD-HAPPY-0001"
    target_ref = "dogfood/v0.3-happy-path-0001"
    prefix = f"gh-aw/{feature}-{run_id}-v1"
    run = {
        "id": run_id, "run_attempt": 1,
        "html_url": f"https://github.com/dream-xin/ai-sdlc/actions/runs/{run_id}",
        "path": ".github/workflows/" + RECOVERY_DEVELOPER_WORKFLOW,
        "display_title": "AI-SDLC gh-aw " + key, "event": "workflow_dispatch",
        "head_branch": "main", "head_sha": source_head,
        "status": "completed", "conclusion": "success",
    }
    jobs = {"total_count": 4, "jobs": [
        {"id": 9001 + index, "name": name, "run_id": run_id, "run_attempt": 1,
         "head_sha": source_head, "status": "completed", "conclusion": "success",
         "steps": ([{"name": "Require first attempt and affirmative detection before Safe Outputs effects",
                    "status": "completed", "conclusion": "success"}] if name == "safe_outputs" else
                   [{"name": "Reject rerun before model execution",
                     "status": "completed", "conclusion": "success"}] if name == "agent" else [])}
        for index, name in enumerate(("safe_outputs", "agent", "detection", "conclusion"))
    ]}
    pr = {
        "number": 901, "id": 1901, "node_id": "PR_test_901",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "html_url": "https://github.com/dream-xin/ai-sdlc/pull/901",
        "state": "open", "draft": True, "title": "[ai-sdlc gh-aw] bounded implementation",
        "head": {"ref": prefix + "-fixed", "sha": candidate_head, "repo": {"full_name": "dream-xin/ai-sdlc"}},
        "base": {"ref": target_ref, "repo": {"full_name": "dream-xin/ai-sdlc"}},
    }
    artifacts, archive = recovery_safe_output_artifact_fixture(
        run_id=run_id, source_head=source_head, pr=pr)
    state = {"run": run, "jobs": jobs, "prs": [pr], "pr": pr,
             "artifacts": artifacts, "archive": archive}
    calls = []
    def http(*, method, url, token):
        calls.append((method, url))
        suffix = urlparse(url).path.split("/repos/dream-xin/ai-sdlc", 1)[1]
        if suffix == f"/actions/runs/{run_id}": value = state["run"]
        elif suffix == f"/actions/runs/{run_id}/attempts/1/jobs": value = state["jobs"]
        elif suffix == "/pulls": value = state["prs"]
        elif suffix == "/pulls/901": value = state["pr"]
        elif suffix == f"/actions/runs/{run_id}/artifacts": value = state["artifacts"]
        elif suffix == f"/actions/artifacts/{run_id + 100}/zip": return 200, {}, state["archive"]
        else: raise AssertionError("recovery resolver escaped exact GET inventory: " + suffix)
        return 200, {}, json.dumps(value).encode()
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main", developer_workflow=RECOVERY_DEVELOPER_WORKFLOW,
        reviewer_workflow="reviewer.lock.yml", qa_workflow="qa.lock.yml")
    source = RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(
            control_repository="dream-xin/ai-sdlc", control_token="control",
            target_token="target", workflows=workflows, collector_identity=COLLECTOR_IDENTITY),
        target_repository="dream-xin/ai-sdlc", http=http)
    trusted = {
        "operation_id": "op", "operation_generation": 1, "operation_profile": "vertical-v0.3",
        "semantic_effect_key": "semantic", "external_dispatch_key": key,
        "dispatch_id": "dispatch", "target_repository": "dream-xin/ai-sdlc",
        "target_ref": target_ref, "feature_id": feature, "expected_revision": 1,
        "feature_stage": "implementation", "role": "developer", "task_id": "TASK-1",
        "launch_candidate_head_sha": "3" * 40, "source_head_sha": source_head}
    resolved = source.resolve(
        external_dispatch_key=key, expected_receipt_identity=str(run_id), trusted_context=trusted)
    expect(resolved.run.run_id == run_id and resolved.run.candidate_pr_number == 901,
           "recovery Safe Output resolver lost exact run/Draft PR")
    expect(source.seal_readiness(
        external_dispatch_key=key, expected_receipt_identity=str(run_id),
        source_head_sha=source_head) == "READY",
        "successful recovery run/Safe Output was not ready to seal")
    state["run"]["status"], state["run"]["conclusion"] = "in_progress", None
    expect(source.seal_readiness(
        external_dispatch_key=key, expected_receipt_identity=str(run_id),
        source_head_sha=source_head) == "PENDING",
        "in-progress recovery run was prematurely sealable")
    state["run"]["status"], state["run"]["conclusion"] = "completed", "success"
    expect("--first-attempt--" in resolved.outputs[0].trusted_uri
           and source.load_content(resolved.outputs[0].trusted_uri),
           "recovery Safe Output lacks digest/content/run lease")
    expect(calls and all(method == "GET" for method, _ in calls),
           "recovery Safe Output resolution attempted a mutation")
    def must_reject(mutate):
        from copy import deepcopy
        saved = deepcopy(state)
        mutate(state)
        try:
            source.resolve(external_dispatch_key=key, expected_receipt_identity=str(run_id), trusted_context=trusted)
        except VerticalInvariantError: pass
        else: raise AssertionError("recovery Safe Output accepted drift/ambiguity")
        state.clear(); state.update(saved)
    must_reject(lambda s: s["run"].update(run_attempt=2))
    must_reject(lambda s: s.update(prs=[s["pr"], dict(s["pr"], number=902)]))
    must_reject(lambda s: s["jobs"]["jobs"][0].update(conclusion="failure"))
    must_reject(lambda s: s["jobs"].update(total_count=3))
    must_reject(lambda s: s["jobs"]["jobs"][0].update(steps=[]))
    must_reject(lambda s: s["pr"]["head"].update(sha="bad"))
    must_reject(lambda s: s["artifacts"].update(total_count=0, artifacts=[]))
    must_reject(lambda s: s["artifacts"].update(total_count=2, artifacts=s["artifacts"]["artifacts"] * 2))
    must_reject(lambda s: s["artifacts"]["artifacts"][0]["workflow_run"].update(id=1))
    must_reject(lambda s: s["artifacts"]["artifacts"][0]["workflow_run"].update(head_sha="0" * 40))
    must_reject(lambda s: s["artifacts"]["artifacts"][0].update(expired=True))
    must_reject(lambda s: s["artifacts"]["artifacts"][0].update(digest="sha256:" + "0" * 64))
    must_reject(lambda s: s.update(archive=b"corrupted archive"))
    must_reject(lambda s: s["pr"].update(node_id="PR_wrong"))
    def rewrite_archive(state, *, duplicate=False, unsafe=False, malformed=False):
        import hashlib
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(state["archive"])) as archive:
            row = archive.read("safe-output-items.jsonl")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                "../safe-output-items.jsonl" if unsafe else "safe-output-items.jsonl",
                b"not-json" if malformed else row + (row if duplicate else b""))
        state["archive"] = stream.getvalue()
        state["artifacts"]["artifacts"][0].update(
            size_in_bytes=len(state["archive"]),
            digest="sha256:" + hashlib.sha256(state["archive"]).hexdigest())
    must_reject(lambda s: rewrite_archive(s, duplicate=True))
    must_reject(lambda s: rewrite_archive(s, unsafe=True))
    must_reject(lambda s: rewrite_archive(s, malformed=True))
    stale = dict(trusted); stale["source_head_sha"] = "4" * 40
    try:
        source.resolve(external_dispatch_key=key, expected_receipt_identity=str(run_id), trusted_context=stale)
    except VerticalInvariantError: pass
    else: raise AssertionError("recovery Safe Output accepted stale main source")
    print("- recovery Safe Output dynamically rejects duplicate/attempt2/wrong-head/stale evidence")



def pinned_provider_revocation_tests():
    """Pin successful real provider facts without retaining historical signing key."""
    from copy import deepcopy
    from unittest.mock import patch
    import base64
    import json
    import v03_dogfood_runtime_driver as subject
    raw = "{\n  \"baseline_run_id\": 37723691424,\n  \"baseline_source_sha\": \"0bb45670495271a4c4019bba7a7313d338de0272\",\n  \"client_id\": \"Iv23libojxnnuF43petx\",\n  \"future_attempts_fenced\": false,\n  \"historical_run_id\": 37204777409,\n  \"historical_workflow_blob_sha\": \"e618477192ef8cde47cf36a9b162aee1069f3eef\",\n  \"old_credential_identity\": \"AI_SDLC_RUNTIME_APP_PRIVATE_KEY\",\n  \"old_key\": {\n    \"app_id\": null,\n    \"app_slug\": null,\n    \"github_date\": \"Fri, 09 Oct 2026 02:59:09 GMT\",\n    \"github_request_id\": \"0BC0:126B9E:193968:53051C:6AC8587D\",\n    \"http_status\": 401,\n    \"public_key_sha256\": \"2765aa5be8fe724421236d1ff15fb6ebaccc51b7476d82c27ead7e366cb32636\"\n  },\n  \"provider_invalidation\": true,\n  \"recovery_authority\": false,\n  \"recovery_credential_identity\": \"AI_SDLC_DOGFOOD_RECOVERY_APP_PRIVATE_KEY\",\n  \"recovery_key\": {\n    \"app_id\": 4576406,\n    \"app_slug\": \"dream-xin-ai-sdlc-runtime-operator\",\n    \"github_date\": \"Fri, 09 Oct 2026 02:59:09 GMT\",\n    \"github_request_id\": \"0BC1:385785:1C3E86:5C5570:6AC8587D\",\n    \"http_status\": 200,\n    \"public_key_sha256\": \"cf2341fc6c86e0a1226f9e4a5e409c4f432f3e326075be6c11425712be75e9ce\"\n  },\n  \"release_eligible\": false,\n  \"repository\": \"dream-xin/ai-sdlc\",\n  \"run_id\": 37877145475,\n  \"schema_version\": \"ai-sdlc.v03-historical-worker-key-revocation-observation/v1\",\n  \"source_sha\": \"9dce67c90df3a8b302e0509d77c9420db353836e\",\n  \"status\": \"REVOCATION_OBSERVED\"\n}\n"
    observed = json.loads(raw)
    source = subject.REVOCATION_PROBE_SOURCE
    output = subject.REVOCATION_OBSERVATION_COMMIT
    tree_sha = "a6848c33e65812c34127afe5948c8e0059610727"
    run_id = subject.REVOCATION_PROBE_RUN
    blob_sha = subject.REVOCATION_OBSERVATION_BLOB
    paths = {
        "run": "actions/runs/" + str(run_id),
        "jobs": "actions/runs/" + str(run_id) + "/jobs?per_page=100",
        "source": "git/commits/" + source,
        "receipt": "git/commits/" + output,
        "tree": "git/trees/" + tree_sha + "?recursive=1",
        "blob": "git/blobs/" + blob_sha,
    }
    mock = {
        paths["run"]: {"id": run_id, "workflow_id": subject.REVOCATION_PROBE_WORKFLOW_ID,
                       "path": subject.REVOCATION_PROBE_WORKFLOW, "event": "push",
                       "head_branch": subject.REVOCATION_PROBE_BRANCH,
                       "head_sha": source, "run_attempt": 1,
                       "status": "completed", "conclusion": "success"},
        paths["jobs"]: {"total_count": 2, "jobs": [
            {"name": name, "run_id": run_id, "run_attempt": 1,
             "status": "completed", "conclusion": "success"}
            for name in ("validate-probe", "verify-key-fence")]},
        paths["source"]: {"sha": source, "parents": [{"sha": subject.REVOCATION_PROBE_PARENT}],
                          "message": "dogfood: verify revoked historical Worker key"},
        paths["receipt"]: {"sha": output, "parents": [{"sha": source}],
                           "tree": {"sha": tree_sha},
                           "message": "dogfood: record bounded provider key revocation observation"},
        paths["tree"]: {"sha": tree_sha, "truncated": False, "tree": [
            {"path": "dogfood/gh-aw-key-revocation-observation.json",
             "mode": "100644", "type": "blob", "sha": blob_sha}]},
        paths["blob"]: {"sha": blob_sha, "encoding": "base64",
                        "size": len(raw.encode()),
                        "content": base64.b64encode(raw.encode()).decode()},
    }
    env = {"GITHUB_REPOSITORY": "DREAM-XIN/ai-sdlc", "GITHUB_API_URL": "https://api.github.com",
           "AI_SDLC_ACTIONS_READ_TOKEN": "read-only-test-token"}
    def verify(facts):
        return subject._load_pinned_revocation_observation(
            env, read_json=lambda name: deepcopy(facts[name]),
        )
    expect(verify(mock) == observed, "immutable provider observation did not validate")
    for mutate in (
        lambda x: x[paths["run"]].update(head_sha="0"*40),
        lambda x: x[paths["run"]].update(run_attempt=2),
        lambda x: x[paths["jobs"]]["jobs"][1].update(conclusion="failure"),
        lambda x: x[paths["source"]].update(parents=[{"sha": "0"*40}]),
        lambda x: x[paths["receipt"]].update(parents=[{"sha": "0"*40}]),
        lambda x: x[paths["tree"]]["tree"][0].update(mode="120000"),
        lambda x: x[paths["blob"]].update(size=1),
        lambda x: x[paths["blob"]].update(content=base64.b64encode(
            raw.replace('"http_status": 401', '"http_status": 200').encode()).decode()),
    ):
        changed = deepcopy(mock)
        mutate(changed)
        try:
            verify(changed)
        except (subject.V03DogfoodRuntimeDriverError, ValueError):
            pass
        else:
            raise AssertionError("tampered provider provenance passed immutable anchor")
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1] /
                ".github/workflows/v03-real-dogfood-scenario.yml").read_text()
    readiness = (Path(__file__).resolve().parents[1] /
                 ".github/workflows/v03-dogfood-readiness.yml").read_text()
    expect("AI_SDLC_HISTORICAL_APP_PRIVATE_KEY:" not in workflow
           and "LEGACY_APP_SECRET_PRESENT:" in workflow
           and "AI_SDLC_LEGACY_SECRET_PRESENT:" in workflow,
           "live recovery retains legacy signing secret or lacks pre-effect deletion gate")
    expect("BLOCKED_LEGACY_CREDENTIAL" in readiness
           and "_load_pinned_revocation_observation" in readiness,
           "zero-effect readiness cannot enforce pinned provider/legacy-secret gates")

    from urllib import request
    class ProviderResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return json.dumps({
            "id": 4576406, "client_id": "Iv23libojxnnuF43petx",
            "slug": "dream-xin-ai-sdlc-runtime-operator"}).encode()
    live_env = dict(env, AI_SDLC_LEGACY_SECRET_PRESENT="false",
                    AI_SDLC_DOGFOOD_RECOVERY_APP_CLIENT_ID="Iv23libojxnnuF43petx",
                    AI_SDLC_RECOVERY_APP_PRIVATE_KEY="test-placeholder")
    calls = []
    def fake_get(req, timeout):
        calls.append((req.full_url, req.get_method(), timeout))
        return ProviderResponse()
    with (
        patch.object(subject, "_load_pinned_revocation_observation", return_value=observed),
        patch.object(subject, "_app_jwt_and_public_digest",
                     return_value=("test-jwt", subject.REVOCATION_NEW_KEY_DIGEST)),
        patch.object(subject.urlrequest, "urlopen", side_effect=fake_get),
    ):
        fence = subject._observe_provider_rotation(live_env)
        replay_fence = subject._observe_provider_rotation(live_env)
        expect(fence["fence_digest"] == replay_fence["fence_digest"],
               "rechecked provider authentication changed frozen CAS recovery identity")
        expect(fence["observed_at"] == observed["old_key"]["github_date"],
               "provider fence uses a mutable local clock rather than immutable observation")
        expect(fence["historical_status"] == 401 and fence["recovery_status"] == 200
               and fence["provider_observation_run_id"] == run_id
               and fence["provider_observation_blob_sha"] == blob_sha,
               "protected fence failed to retain authenticated provider facts")
        for patch_env in (
            {"AI_SDLC_LEGACY_SECRET_PRESENT": "true"},
            {"AI_SDLC_LEGACY_SECRET_PRESENT": ""},
            {"AI_SDLC_HISTORICAL_APP_PRIVATE_KEY": "legacy-must-be-absent"},
            {"AI_SDLC_DOGFOOD_RECOVERY_APP_CLIENT_ID": "wrong"},
        ):
            try:
                subject._observe_provider_rotation(dict(live_env, **patch_env))
            except subject.V03DogfoodRuntimeDriverError:
                pass
            else:
                raise AssertionError("unsafe key configuration passed provider fence")
    expect(calls == [("https://api.github.com/app", "GET", 20)] * 2,
           "two bounded new-key checks made extra or misdirected provider requests")
    print("- pinned provider run/jobs/source/tree/blob and legacy-secret/new-key fences validated")



def recovery_policy_fixture():
    from types import SimpleNamespace
    return SimpleNamespace(
        installation_commit_sha="5" * 40, materialization_commit_sha="4" * 40,
        receipt_digest="3" * 64, bundle_digest="2" * 64)


def armed_recovery_fixture():
    """Immutable historical evidence fixture; never a live Store mutation."""
    import json
    from operator_store_model import StoreSnapshot, digest_json
    from v03_dogfood_full_composition import ARMED_RECOVERY_NO_HTTP_PROOF
    from copy import deepcopy
    subject = driver_subject
    authorization = json.loads("{\"candidate_head_sha\":\"70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23\",\"candidate_pr_number\":552,\"created_at\":\"2026-10-09T06:15:48Z\",\"display_title\":\"AI-SDLC gh-aw recovery-935ad236772d508dfd7e57da6370243dcce4555e\",\"event\":\"workflow_dispatch\",\"expected_revision\":1,\"external_dispatch_key\":\"dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1\",\"feature_id\":\"F-OPERATOR-V03-DOGFOOD-HAPPY-0001\",\"head_branch\":\"main\",\"historical_attempt_id\":\"eca-1f340ab49f04ab7b0747907075aead41b8fa6f23\",\"historical_observation_digest\":\"sha256:a86b7ead37bf96abe9b6e43098b7873b821833c6d93c720ed7409835af18916f\",\"historical_runtime_receipt_identity\":\"37204777409\",\"installation_commit_sha\":\"0b5f0a69db9b3cbc4388e8318948f87ba0a92fea\",\"operation_generation\":1,\"operation_id\":\"op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4\",\"provider_fence_digest\":\"sha256:495d3d2d46955bc805cb8a1134fbf3b339182ef649ed24f2f415d5de52858e74\",\"recovery_dispatch_id\":\"recovery-dispatch-f83e3d4d4e864be91d2bf2cf\",\"recovery_dispatch_key\":\"recovery-935ad236772d508dfd7e57da6370243dcce4555e\",\"role\":\"developer\",\"schema_version\":\"ai-sdlc.v03-dogfood-bounded-recovery/v1\",\"semantic_effect_key\":\"80b31137a408f2b0ee85248bd069b972af80b9777f91f2bd00c9b27b42f9e804\",\"source_head_sha\":\"0b5f0a69db9b3cbc4388e8318948f87ba0a92fea\",\"stage\":\"implementation\",\"target_ref\":\"dogfood/v0.3-happy-path-0001\",\"target_repository\":\"dream-xin/ai-sdlc\",\"task_id\":\"vertical:implementation:1\",\"task_identity\":\"vertical:implementation:1\",\"trusted_context_digest\":\"75b4a104a00421361eb20e1981be148664a13c9686e1e3a5b80b4e1d7bf22b28\",\"worker_blobs\":{\".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml\":\"7fcaea43141b26783204fd851bb9c06762b8f98b\",\".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.md\":\"28e087722f736b663f9891c7708876beb1934488\",\".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.lock.yml\":\"6318bb99352fede75bb2805560fca148fbea6df5\",\".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.md\":\"28bb0bb6e72ddbe782209e0da52f7fdbb6c23f35\"},\"workflow_file\":\"ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml\"}")
    attempt = dict(authorization)
    attempt.update(
        authorization_digest="sha256:" + digest_json(authorization),
        attempt_id="recovery-create-attempt-" + digest_json(authorization)[:32],
        status="ARMED")
    snapshot = StoreSnapshot("armed-original", {
        subject.RECOVERY_AUTHORIZATION_PATH: authorization,
        subject.RECOVERY_ATTEMPT_PATH: attempt,
    })
    fence = subject._validate_provider_rotation_fence({
        "schema_version": subject.RECOVERY_SCHEMA,
        "app_id": 4576406, "app_client_id": "Iv23libojxnnuF43petx",
        "installation_id": 153325330, "historical_status": 401, "recovery_status": 200,
        "historical_public_key_digest": subject.HISTORICAL_APP_PUBLIC_KEY_DIGEST,
        "recovery_public_key_digest": subject.REVOCATION_NEW_KEY_DIGEST,
        "provider_observation_run_id": subject.REVOCATION_PROBE_RUN,
        "provider_observation_blob_sha": subject.REVOCATION_OBSERVATION_BLOB,
        "observed_at": "Fri, 09 Oct 2026 02:59:09 GMT",
    })
    expect(fence["fence_digest"] == authorization["provider_fence_digest"],
           "test provider proof no longer reproduces exact historical fence")
    return snapshot, deepcopy(ARMED_RECOVERY_NO_HTTP_PROOF), fence


def historical_recovery_worker_blobs():
    """Read exact frozen Worker bytes, never substitute new preparation sources."""
    import hashlib
    import subprocess
    from pathlib import Path
    from v03_dogfood_full_composition import ARMED_RECOVERY_SOURCE
    snapshot, _, _ = armed_recovery_fixture()
    expected = snapshot.get(driver_subject.RECOVERY_AUTHORIZATION_PATH)["worker_blobs"]
    root = Path(__file__).resolve().parents[1]
    observed = {}
    for path, pinned in expected.items():
        raw = subprocess.run(["git", "show", f"{ARMED_RECOVERY_SOURCE}:{path}"],
                             cwd=root, check=True, capture_output=True).stdout
        observed[path] = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        expect(observed[path] == pinned, "frozen historical Worker bytes changed: " + path)
    return observed


def recovery_actions_transport_tests(*, create_only=False):
    """Exercise production gateway and Actions transport; fake HTTP only."""
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import patch
    from operator_store_model import apply_plan_to_snapshot
    from v03_dogfood_fixture_pool import require_slot
    from v03_dogfood_full_composition import (
        DogfoodRecoveryActionsTransport, DogfoodRecoveryDispatchGateway,
        DogfoodGitHubCandidateProvider, V03DogfoodCompositionError,
    )
    from urllib.parse import urlparse, parse_qs
    from operator_vertical import VerticalInvariantError
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
    from operator_vertical_gh_aw_actions_transport import (
        GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig,
    )
    subject = driver_subject
    key = "recovery-935ad236772d508dfd7e57da6370243dcce4555e"
    workflows = GhAwVerticalWorkflowMap(
        default_branch="main", developer_workflow=subject.RECOVERY_WORKFLOW,
        reviewer_workflow="ai-sdlc-gh-aw-reviewer.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-deepseek-v03-local.lock.yml")
    roles = tuple(workflows.workflow_for(role) for role in ("developer", "reviewer", "qa"))
    class HTTP:
        def __init__(self):
            self.calls = []
            self.key = key
            self.rows = {workflow: [] for workflow in roles}
            self.fail = set()
            self.ack_loss = False
            self.visible = True
            self.main_sources = ["5" * 40]
        def row(self, workflow, run_id):
            return {"id": run_id, "display_title": "AI-SDLC gh-aw " + self.key,
                    "event": "workflow_dispatch", "head_branch": "main",
                    "path": ".github/workflows/" + workflow}
        def __call__(self, *, method, url, token, body=None):
            path = urlparse(url).path
            if path == "/repos/dream-xin/ai-sdlc/git/ref/heads/main":
                expect(method == "GET" and body is None, "main source check attempted mutation")
                self.calls.append(("GET", "main", None))
                sha = self.main_sources[0]
                if len(self.main_sources) > 1:
                    self.main_sources.pop(0)
                return 200, {}, json.dumps({
                    "ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}).encode()
            prefix = "/repos/dream-xin/ai-sdlc/actions/workflows/"
            expect(path.startswith(prefix), "recovery transport escaped repository")
            workflow, action = path[len(prefix):].rsplit("/", 1)
            expect(workflow in roles and token == "isolated-http-token",
                   "recovery transport escaped role/token binding")
            self.calls.append((method, workflow, body))
            if method == "POST":
                expect(action == "dispatches" and workflow == subject.RECOVERY_WORKFLOW,
                       "recovery key POST escaped exact Developer workflow")
                payload = json.loads(body)
                expect(payload["inputs"]["dispatch_key"] == self.key and payload["ref"] == "main",
                       "recovery POST renamed key or changed trusted ref")
                if self.visible:
                    self.rows[workflow].append(self.row(workflow, 40000000002))
                if self.ack_loss:
                    raise OSError("acknowledgement lost after remote accept")
                return 204, {}, b""
            expect(method == "GET" and action == "runs", "unexpected recovery HTTP effect")
            if workflow in self.fail:
                return 503, {}, b""
            query = parse_qs(urlparse(url).query)
            expect(query["event"] == ["workflow_dispatch"] and query["branch"] == ["main"],
                   "recovery lookup escaped exhaustive trusted query")
            page, size = int(query["page"][0]), int(query["per_page"][0])
            rows = self.rows[workflow]
            return 200, {}, json.dumps({
                "total_count": len(rows),
                "workflow_runs": rows[(page - 1) * size:page * size],
            }).encode()
        @property
        def posts(self):
            return sum(method == "POST" for method, _, _ in self.calls)
    def fixture():
        http = HTTP()
        h = subject.HISTORICAL_PREHTTP_RECOVERY
        candidate_calls = []
        def candidate_get(url, headers):
            candidate_calls.append(url)
            return 200, [{
                "number": h["candidate_pr_number"], "state": "open", "draft": False,
                "head": {"ref": h["target_ref"], "sha": h["candidate_head_sha"],
                         "repo": {"full_name": "dream-xin/ai-sdlc"}},
                "base": {"ref": "main", "repo": {"full_name": "dream-xin/ai-sdlc"}},
            }]
        candidate = DogfoodGitHubCandidateProvider(
            slot=require_slot("happy_path"), repository="dream-xin/ai-sdlc",
            token="candidate-read-only", http_get=candidate_get)
        transport = DogfoodRecoveryActionsTransport(
            GitHubActionsWorkflowTransportConfig(
                control_repository="dream-xin/ai-sdlc", token="isolated-http-token",
                workflows=workflows, page_size=1, max_lookup_pages=4,
                launch_poll_attempts=1, launch_poll_seconds=0),
            candidate_provider=candidate, http=http, sleeper=lambda _: None)
        gateway = DogfoodRecoveryDispatchGateway(transport=transport, workflows=workflows)
        snapshot, proof, fence = armed_recovery_fixture()
        pf = SimpleNamespace(
            execution=SimpleNamespace(repository="dream-xin/ai-sdlc",
                                      installation_commit_sha="5" * 40),
            trusted_context_digest="6" * 64,
            composition=SimpleNamespace(runtime=SimpleNamespace(
                clock=lambda: "2026-10-09T07:00:00Z"),
                policy_authority=recovery_policy_fixture(), recovery_dispatch_gateway=gateway))
        with (patch.object(subject, "_bounded_recovery_identity", return_value=({}, {})),
              patch.object(subject, "_recovery_worker_blobs", return_value=historical_recovery_worker_blobs())):
            plan = subject._plan_bounded_recovery(
                snapshot, preflight=pf, fence=fence, proof=proof)
        snapshot = apply_plan_to_snapshot(snapshot, plan, new_ref_sha="continuation")
        transport.admit_continuation(
            snapshot, allow_post=True, execution_source_head_sha="5" * 40,
            execution_trusted_context_digest="6" * 64,
            execution_materialization_commit_sha="4" * 40,
            execution_policy_receipt_digest="3" * 64,
            execution_policy_bundle_digest="2" * 64)
        http.snapshot = snapshot
        dispatch = subject._bounded_recovery_dispatch(
            pf, snapshot.get(subject.RECOVERY_AUTHORIZATION_PATH))
        return http, transport, gateway, dispatch
    def denied(action, http, message):
        before = len(http.calls)
        try:
            action()
        except (VerticalInvariantError, V03DogfoodRuntimeDriverError, V03DogfoodCompositionError, ValueError):
            pass
        else:
            raise AssertionError(message)
        expect(len(http.calls) == before, message + " crossed HTTP before rejection")

    if create_only:
        return fixture

    # The unchanged shared transport is the exact historical rejector. This
    # proves the old route fails before any HTTP; no generic dispatch-key widening.
    from pathlib import Path
    import hashlib
    old_raw = (Path(__file__).resolve().parent / "operator_vertical_gh_aw_actions_transport.py").read_bytes()
    old_blob = hashlib.sha1(b"blob " + str(len(old_raw)).encode() + bytes([0]) + old_raw).hexdigest()
    expect(old_blob == "ec9ea44f81cda1a052cd984cd345f1572c4903e7",
           "shared transport changed instead of exact dogfood continuation")
    http, transport, gateway, dispatch = fixture()
    old = GitHubActionsVerticalGhAwTransport(transport.config, http=http, sleeper=lambda _: None)
    old_gateway = GhAwVerticalRoleDispatchGateway(transport=old, workflows=workflows)
    denied(lambda: old_gateway.lookup(external_dispatch_key=key), http,
           "frozen old transport lookup crossed HTTP")
    denied(lambda: old_gateway.launch(dispatch=dispatch), http,
           "frozen old transport dispatch crossed HTTP")
    http, transport, gateway, dispatch = fixture()
    expect(gateway.lookup(external_dispatch_key=key)["lookup_state"] == "NOT_LAUNCHED",
           "production transport still rejects the immutable armed recovery key")
    expect([w for m, w, _ in http.calls if m == "GET"] == list(roles),
           "fresh recovery gateway did not scan every trusted role")
    http.calls.clear()
    receipt = gateway.launch(dispatch=dispatch)
    expect(receipt == {"lookup_state": "LAUNCHED", "receipt_id": "40000000002"}
           and http.posts == 1, "real recovery gateway/transport failed exact one-POST launch")
    first_post = next(i for i, call in enumerate(http.calls) if call[0] == "POST")
    expect([w for m, w, _ in http.calls[:first_post] if m == "GET" and w in roles] == list(roles),
           "recovery POST preceded exhaustive all-role absence")
    denied(lambda: gateway.launch(dispatch=dispatch), http,
           "consumed continuation capability admitted a second launch")
    expect(http.posts == 1, "production transport replay repeated recovery POST")
    http.calls.clear()
    expect(gateway.lookup(external_dispatch_key=key)["lookup_state"] == "LAUNCHED",
           "cached recovery lookup lost exact receipt")
    expect({w for m, w, _ in http.calls if m == "GET"} == set(roles),
           "cached recovery lookup skipped cross-role collision checks")
    http.fail.add(roles[2])
    expect(gateway.lookup(external_dispatch_key=key)["lookup_state"] == "UNKNOWN",
           "single positive recovery match masked unknown role")
    http.fail.clear()
    http.rows[roles[1]] = [http.row(roles[1], 40000000003)]
    expect(gateway.lookup(external_dispatch_key=key)["lookup_state"] == "UNKNOWN",
           "cached recovery lookup adopted cross-role collision")

    for source, context in (("4" * 40, "6" * 64), ("5" * 40, "7" * 64)):
        http, transport, gateway, dispatch = fixture()
        denied(lambda: transport.admit_continuation(
            http.snapshot, allow_post=True, execution_source_head_sha=source,
            execution_trusted_context_digest=context,
            execution_materialization_commit_sha="4" * 40,
            execution_policy_receipt_digest="3" * 64,
            execution_policy_bundle_digest="2" * 64), http,
            "stale source/context acquired continuation POST authority")
    for malformed in ("recovery-" + "a" * 40, key.upper(), key + "0", key[:-1],
                      "recovery-test-key", "", "dispatch-" + "z" * 40, "dispatch-" + "a" * 40):
        http, transport, gateway, dispatch = fixture()
        denied(lambda: gateway.lookup(external_dispatch_key=malformed), http,
               "transport admitted malformed/unpinned recovery key " + repr(malformed))
    for role in ("reviewer", "qa"):
        http, transport, gateway, dispatch = fixture()
        changed = dict(dispatch, role=role)
        denied(lambda: gateway.launch(dispatch=changed), http,
               "recovery key acquired " + role + " POST authority")
    for field, value in (
        ("operation_id", "op-other"), ("operation_generation", 2),
        ("semantic_effect_key", "0" * 64), ("dispatch_id", "recovery-dispatch-other"),
        ("feature_id", "F-OTHER"), ("expected_revision", 2),
        ("target_repository", "other/repository"), ("target_ref", "other"),
        ("task_id", "other"), ("task_identity", "other"), ("candidate_head_sha", "0" * 40),
    ):
        http, transport, gateway, dispatch = fixture()
        changed = deepcopy(dispatch); changed[field] = value
        denied(lambda: gateway.launch(dispatch=changed), http,
               "recovery POST admitted drifted " + field)
    for field, value in (
        ("kind", "remediation"), ("goal", "perform another task"),
        ("allowed_scope", ["all repositories"]), ("forbidden_scope", []),
    ):
        http, transport, gateway, dispatch = fixture()
        inputs = gateway._inputs(dispatch)
        payload = json.loads(inputs["task_payload"])
        payload["task"][field] = value
        inputs["task_payload"] = json.dumps(payload)
        denied(lambda: transport.dispatch(workflow=roles[0], ref="main", inputs=inputs),
               http, "recovery POST accepted mutated task " + field)
    http, transport, gateway, dispatch = fixture()
    denied(lambda: transport.lookup(workflow=roles[0], ref="feature", dispatch_key=key),
           http, "recovery lookup accepted non-main ref")
    # Two pages prove absence is exhausted, not inferred from page one.
    http, transport, gateway, dispatch = fixture()
    for workflow in roles:
        http.rows[workflow] = [
            dict(http.row(workflow, 101), display_title="unrelated one"),
            dict(http.row(workflow, 102), display_title="unrelated two")]
    expect(gateway.launch(dispatch=dispatch)["lookup_state"] == "LAUNCHED" and http.posts == 1,
           "paginated all-role absence failed exact recovery dispatch")
    before = http.calls[:next(i for i, call in enumerate(http.calls) if call[0] == "POST")]
    expect([w for m, w, _ in before if w in roles] == [w for w in roles for _ in range(2)],
           "recovery POST did not exhaust every role page")
    for collision in ("cross-role", "duplicate", "unknown", "page-bound"):
        http, transport, gateway, dispatch = fixture()
        if collision == "cross-role":
            http.rows[roles[1]] = [http.row(roles[1], 101)]
        elif collision == "duplicate":
            http.rows[roles[0]] = [http.row(roles[0], 101), http.row(roles[0], 102)]
        elif collision == "page-bound":
            http.rows[roles[2]] = [dict(http.row(roles[2], i), display_title="unrelated")
                                   for i in range(1, 6)]
        else:
            http.fail.add(roles[2])
        expect(gateway.launch(dispatch=dispatch)["lookup_state"] == "UNKNOWN" and http.posts == 0,
               collision + " recovery lookup crossed POST boundary")
    http, transport, gateway, dispatch = fixture()
    http.main_sources = ["4" * 40]
    try:
        changed_source = gateway.launch(dispatch=dispatch)
    except VerticalInvariantError:
        pass
    else:
        expect(changed_source["lookup_state"] == "UNKNOWN",
               "scan-time main drift became a launched receipt")
    expect(http.posts == 0, "scan-time main drift crossed actual HTTP POST boundary")
    http, transport, gateway, dispatch = fixture()
    http.ack_loss = True
    expect(gateway.launch(dispatch=dispatch)["lookup_state"] == "LAUNCHED" and http.posts == 1,
           "production acknowledgement loss did not converge lookup-only")
    http, transport, gateway, dispatch = fixture()
    http.ack_loss = True; http.visible = False
    expect(gateway.launch(dispatch=dispatch)["lookup_state"] == "UNKNOWN" and http.posts == 1,
           "post-create absence was promoted to safe retry")
    print("- real recovery gateway/Actions transport enforces exact identity, exhaustive lookup and one POST")
    return fixture



def armed_recovery_source_proof_tests():
    """Verify real pinned source/run/job/log proof with fake read-only HTTP."""
    import base64
    import hashlib
    import json
    import subprocess
    from copy import deepcopy
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlsplit
    from operator_store_model import canonical_json
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_vertical_gh_aw_actions_transport import (
        GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig)
    from v03_dogfood_full_composition import (
        ARMED_RECOVERY_SOURCE, ARMED_RECOVERY_STORE, ARMED_RECOVERY_SOURCE_BLOBS,
        ARMED_RECOVERY_AUTHORIZATION_BLOB, ARMED_RECOVERY_ATTEMPT_BLOB)
    subject = driver_subject
    snapshot, proof, _ = armed_recovery_fixture()
    facts = {}
    for path, blob in ARMED_RECOVERY_SOURCE_BLOBS.items():
        # CI uses fetch-depth: 0. Read only the pinned ancestor; never fetch
        # mutable network source or accept current-file substitutions.
        raw = subprocess.run(
            ["git", "show", ARMED_RECOVERY_SOURCE + ":" + path],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, check=True).stdout
        expect(hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest() == blob,
               "offline historical source fixture differs from pinned blob")
        facts["/contents/" + path + "?ref=" + ARMED_RECOVERY_SOURCE] = {
            "type": "file", "path": path, "encoding": "base64", "sha": blob,
            "content": base64.b64encode(raw).decode()}
    for path, blob in (
        (subject.RECOVERY_AUTHORIZATION_PATH, ARMED_RECOVERY_AUTHORIZATION_BLOB),
        (subject.RECOVERY_ATTEMPT_PATH, ARMED_RECOVERY_ATTEMPT_BLOB),
    ):
        raw = (canonical_json(snapshot.get(path)) + "\n").encode()
        expect(hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest() == blob,
               "embedded original Store fixture no longer matches pinned bytes")
        facts["/contents/" + path + "?ref=" + ARMED_RECOVERY_STORE] = {
            "type": "file", "path": path, "encoding": "base64", "sha": blob,
            "content": base64.b64encode(raw).decode()}
    run_path, job_path = "/actions/runs/37892560162", "/actions/jobs/113696529763"
    facts[run_path] = {
        "id": 37892560162, "run_attempt": 1, "workflow_id": 342691463,
        "head_sha": ARMED_RECOVERY_SOURCE, "head_branch": "main",
        "path": ".github/workflows/v03-real-dogfood-scenario.yml",
        "event": "workflow_dispatch", "status": "completed", "conclusion": "failure",
        "repository": {"full_name": "dream-xin/ai-sdlc"},
        "updated_at": "2026-10-09T06:16:00Z"}
    facts[job_path] = {
        "id": 113696529763, "run_id": 37892560162, "run_attempt": 1,
        "head_sha": ARMED_RECOVERY_SOURCE, "status": "completed",
        "conclusion": "failure", "name": "dogfood"}
    trace = (
        'v03_dogfood_runtime_driver.py", line 1543, in recover_historical_prehttp_attempt\n'
        "before = preflight.composition.recovery_dispatch_gateway.lookup(\n"
        'operator_vertical_gh_aw_actions_transport.py", line 132, in _validate_lookup_identity\n'
        "operator_vertical.VerticalInvariantError: invalid stable external dispatch key\n"
    ).encode()
    def verify(documents, *, log=trace, status=200, moving=False, malformed=False):
        calls = []
        def http(*, method, url, token, body=None):
            expect(method == "GET" and body is None and token == "proof-read-only",
                   "source proof attempted a mutation")
            parts = urlsplit(url)
            prefix = "/repos/dream-xin/ai-sdlc"
            expect(parts.netloc == "api.github.com" and parts.path.startswith(prefix),
                   "proof escaped exact GitHub repository")
            suffix = parts.path[len(prefix):] + (("?" + parts.query) if parts.query else "")
            calls.append(suffix)
            if suffix == job_path + "/logs":
                return status, {}, log
            value = deepcopy(documents[suffix])
            if moving and suffix == run_path and calls.count(run_path) == 2:
                value["updated_at"] += "changed"
            return 200, {}, (b"not-json" if malformed else json.dumps(value).encode())
        transport = GitHubActionsVerticalGhAwTransport(
            GitHubActionsWorkflowTransportConfig(
                control_repository="dream-xin/ai-sdlc", token="proof-read-only",
                workflows=GhAwVerticalWorkflowMap(
                    default_branch="main", developer_workflow=subject.RECOVERY_WORKFLOW,
                    reviewer_workflow="reviewer.lock.yml", qa_workflow="qa.lock.yml")),
            http=http)
        pf = SimpleNamespace(composition=SimpleNamespace(actions_transport=transport))
        result = subject._observe_armed_recovery_no_http(pf)
        expect(calls.count(run_path) == 2
               and all(path in calls for path in documents)
               and job_path + "/logs" in calls,
               "source proof skipped immutable bytes or bracketed provider observations")
        return result
    expect(verify(facts) == proof, "real source proof rejected exact pinned evidence")
    def reject(documents, **kwargs):
        try: verify(documents, **kwargs)
        except V03DogfoodRuntimeDriverError: pass
        else: raise AssertionError("source proof accepted drifted/malformed evidence")
    for path, field, value in (
        (run_path, "run_attempt", 2), (run_path, "run_attempt", True),
        (run_path, "head_sha", "0" * 40),
        (run_path, "status", "in_progress"), (run_path, "event", "push"),
        (job_path, "run_attempt", 2), (job_path, "run_attempt", True),
        (job_path, "head_sha", "0" * 40),
        (job_path, "run_id", 1), (job_path, "conclusion", "success"),
    ):
        changed = deepcopy(facts); changed[path][field] = value; reject(changed)
    for path in (name for name in facts if name.startswith("/contents/")):
        changed = deepcopy(facts); changed[path]["sha"] = "0" * 40; reject(changed)
        changed = deepcopy(facts); changed[path]["content"] = base64.b64encode(b"tampered").decode()
        reject(changed)
    reject(facts, moving=True)
    reject(facts, malformed=True)
    reject(facts, log=b"invalid stable external dispatch key")
    reject(facts, log=bytes([255]))
    reject(facts, status=404)
    print("- real immutable source proof rejects reruns, moving runs, changed blobs and malformed logs")

def recovery_continuation_cas_tests():
    """Real protected Store CAS chooses one immutable same-key continuation."""
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import patch
    from operator_store_backends import OperatorStoreRuntime
    from operator_vertical import VerticalInvariantError
    from operator_store_git import MemoryStateRefBackend, CasConflict
    from operator_store_model import StoreSnapshot, canonical_json
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from v03_dogfood_full_composition import (
        RECOVERY_CONTINUATION_PATH, validate_armed_recovery_pair,
        validate_recovery_continuation, V03DogfoodCompositionError,
    )
    subject = driver_subject
    snapshot, proof, fence = armed_recovery_fixture()
    backend = MemoryStateRefBackend(
        repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
        snapshot=deepcopy(snapshot))
    runtime = OperatorStoreRuntime(
        backend=backend, protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-10-09T07:00:00Z")
    _, _, gateway, _ = recovery_actions_transport_tests(create_only=True)()
    pf = SimpleNamespace(
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha="5" * 40),
        trusted_context_digest="6" * 64, composition=SimpleNamespace(
            runtime=runtime, policy_authority=recovery_policy_fixture(),
            recovery_dispatch_gateway=gateway))
    def planner(snap):
        return subject._plan_bounded_recovery(snap, preflight=pf, fence=fence, proof=proof)
    def reject(action, reason):
        before = canonical_json(backend.read_snapshot().files)
        try: action()
        except (V03DogfoodRuntimeDriverError, V03DogfoodCompositionError, VerticalInvariantError, ValueError): pass
        else: raise AssertionError("recovery accepted " + reason)
        expect(canonical_json(backend.read_snapshot().files) == before,
               "rejected " + reason + " changed Store")
    with (patch.object(subject, "_bounded_recovery_identity", return_value=({}, {})),
          patch.object(subject, "_recovery_worker_blobs", return_value=historical_recovery_worker_blobs())):
        # Two distinct planners observe the same ref; only one CAS can commit.
        first, second = planner(backend.read_snapshot()), planner(backend.read_snapshot())
        expect(first.result["acquired"] is True and second.result["acquired"] is True,
               "fresh continuation planners were not competing for one claim")
        expect(len(first.mutations) == 1
               and first.mutations[0].path == RECOVERY_CONTINUATION_PATH
               and first.mutations[0].kind == "create_immutable",
               "continuation planner overwrote original authority")
        winner = backend.commit(first, runtime.protected_receipt())
        try: backend.commit(second, runtime.protected_receipt())
        except CasConflict: pass
        else: raise AssertionError("two continuation CAS contenders both committed")
        loser = runtime.commit_replanned(planner)
        expect(loser.result["acquired"] is False,
               "CAS loser replan acquired second POST authority")
        for path, value in snapshot.files.items():
            expect(backend.read_snapshot().get(path) == value,
                   "continuation changed immutable predecessor " + path)
        auth, attempt, continuation = validate_recovery_continuation(backend.read_snapshot())
        expect(auth["source_head_sha"] != continuation["execution_source_head_sha"],
               "continuation silently redefined original authorization source")
        # A conflict before the winner's durable claim must safely replan.
        backend.snapshot = deepcopy(snapshot)
        backend.inject_conflict_once()
        retry = runtime.commit_replanned(planner)
        expect(retry.result["acquired"] is True,
               "benign CAS conflict prevented exactly one fresh continuation winner")
        expect(runtime.commit_replanned(planner).result["acquired"] is False,
               "crash after claim admitted replacement POST authority")
        good = backend.read_snapshot()
        for path in (subject.RECOVERY_AUTHORIZATION_PATH, subject.RECOVERY_ATTEMPT_PATH):
            absent = deepcopy(snapshot); del absent.files[path]
            reject(lambda: planner(absent), "missing original " + path)
            changed = deepcopy(snapshot); changed.files[path]["created_at"] += "changed"
            reject(lambda: planner(changed), "changed original bytes " + path)
        absent = StoreSnapshot("empty", {})
        reject(lambda: planner(absent), "empty Store as fresh recovery authority")
        for field, value in (
            ("execution_source_head_sha", auth["source_head_sha"]),
            ("execution_trusted_context_digest", "bad"),
            ("no_http_proof_digest", "sha256:" + "0" * 64),
            ("authorization_digest", "sha256:" + "0" * 64),
            ("create_attempt_digest", "sha256:" + "0" * 64),
            ("recovery_dispatch_key", "recovery-" + "a" * 40),
            ("provider_fence_digest", "sha256:" + "0" * 64),
            ("admission_version", 2),
        ):
            changed = deepcopy(good)
            changed.files[RECOVERY_CONTINUATION_PATH][field] = value
            reject(lambda: validate_recovery_continuation(changed), "forged continuation " + field)
        changed = deepcopy(good)
        changed.files[RECOVERY_CONTINUATION_PATH]["extra_authority"] = True
        reject(lambda: validate_recovery_continuation(changed), "additional continuation authority")
        wrong_proof = deepcopy(proof); wrong_proof["run_attempt"] = 2
        reject(lambda: subject._plan_bounded_recovery(
            snapshot, preflight=pf, fence=fence, proof=wrong_proof), "attempt-2 no-HTTP proof")
        wrong_fence = dict(fence, fence_digest="sha256:" + "0" * 64)
        reject(lambda: subject._plan_bounded_recovery(
            snapshot, preflight=pf, fence=wrong_fence, proof=proof), "different provider fence")
        with patch.object(subject, "_recovery_worker_blobs", return_value={}):
            reject(lambda: planner(snapshot), "changed Worker sources")
    print("- protected continuation CAS proves one winner, retry, crash fencing and immutable originals")


def recovery_callback_validation_fence_tests(runtime, executor, callback, content_loader):
    """Real coordinator rejects receipt/Feature drift before any handoff."""
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_vertical import FeatureSnapshot, VerticalInvariantError
    from v03_dogfood_full_composition import DogfoodTrustedCallbackCoordinator
    context = callback["context"]
    valid_feature = FeatureSnapshot(
        repository=context.target_repository, feature_id=context.feature_id,
        target_ref=context.target_ref, revision=context.expected_revision,
        manifest_digest="", current_stage=context.feature_stage,
        stages={"implementation": "WORKING"}, gates={}, remediation_tasks=(), artifacts=(),
        candidate_pr_number=None, candidate_head_sha=context.candidate_head_sha)
    saved_snapshot, saved_gateway = runtime.backend.snapshot, executor.feature_gateway
    handoffs = []
    def forbidden_handoff(**kwargs):
        handoffs.append(kwargs)
        raise AssertionError("invalid callback crossed candidate handoff boundary")
    coordinator = DogfoodTrustedCallbackCoordinator(
        delegate=SimpleNamespace(executor=executor, content_loader=content_loader),
        candidate_handoff=SimpleNamespace(adopt=forbidden_handoff))
    try:
        for label in ("feature-revision", "receipt-digest"):
            from dataclasses import replace
            runtime.backend.snapshot = deepcopy(saved_snapshot)
            feature = (replace(valid_feature, revision=context.expected_revision + 1)
                       if label == "feature-revision" else valid_feature)
            executor.feature_gateway = SimpleNamespace(
                read_feature=lambda **kwargs: (feature, {}))
            receipts = deepcopy(callback["receipts"])
            if label == "receipt-digest":
                receipts[0]["sha256"] = "0" * 64
            try:
                coordinator.handle(
                    context=context, callback_id="negative-" + label,
                    worker_payload=callback["worker_payload"], receipts=receipts)
            except VerticalInvariantError:
                pass
            else:
                raise AssertionError("coordinator accepted invalid " + label)
            expect(not handoffs, label + " reached handoff or PATCH")
            added = set(runtime.backend.snapshot.files) - set(saved_snapshot.files)
            expect(not any("handoff" in path for path in added),
                   label + " created a handoff sidecar")
    finally:
        runtime.backend.snapshot = saved_snapshot
        executor.feature_gateway = saved_gateway


def happy_path_recovery_finalization_tests(preflight, sealed, developer_callback, *, expected_human_interventions=None):
    """Real finalizer/provenance/source pipeline over HTTP and Store fixtures only."""
    from copy import deepcopy
    from dataclasses import asdict, replace
    from types import SimpleNamespace
    from unittest.mock import patch
    from urllib.parse import urlparse
    import json
    import v03_dogfood_runtime_driver as driver
    import v03_dogfood_post_run_finalizer as finalizer
    import v03_dogfood_production_provenance as provenance
    from operator_store_model import digest_json, event_path, make_event, operation_events, reservation_path, projection_path
    from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_store import vertical_projection
    from operator_vertical_recovery import plan_vertical_callback_record
    from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource
    from operator_vertical_gh_aw_github_source import _GATE_START, _GATE_END
    from operator_vertical_gh_aw_collector import _build_receipts
    from v03_dogfood_full_composition import (
        DogfoodGitHubCandidateProvider, DogfoodCandidateHandoff, DogfoodRecoveryBoundContentLoader,
        recovery_route, REPLACEMENT_FAILED_RUN, REPLACEMENT_FAILED_PR, REPLACEMENT_FAILED_HEAD,
        REPLACEMENT_FAILED_SOURCE, ARMED_RECOVERY_KEY)

    runtime = preflight.composition.runtime
    saved_snapshot = runtime.backend.snapshot
    runtime.backend.snapshot = deepcopy(saved_snapshot)
    snapshot = runtime.backend.snapshot
    h = driver.HISTORICAL_PREHTTP_RECOVERY
    repository, operation_id = preflight.execution.repository, h["operation_id"]
    old_head, new_head = h["candidate_head_sha"], sealed["output_candidate_head_sha"]
    source_sha, run_id = preflight.execution.installation_commit_sha, int(sealed["receipt_id"])
    original_auth = deepcopy(snapshot.get(driver.RECOVERY_AUTHORIZATION_PATH))
    original_attempt = deepcopy(snapshot.get(driver.RECOVERY_ATTEMPT_PATH))
    route = recovery_route(snapshot)
    replacement = route["ordinal"] == 1
    frozen_predecessor = {path: deepcopy(value) for path, value in snapshot.files.items()
                          if path != projection_path(operation_id)}
    initial_events = deepcopy(operation_events(snapshot, operation_id))
    retained_failed_pr = {
        "number": REPLACEMENT_FAILED_PR, "state": "open", "draft": True,
        "html_url": f"https://github.com/{repository}/pull/{REPLACEMENT_FAILED_PR}",
        "head": {"sha": REPLACEMENT_FAILED_HEAD, "repo": {"full_name": repository}},
        "base": {"ref": h["target_ref"], "sha": old_head, "repo": {"full_name": repository}},
    }
    if replacement:
        expect(type(expected_human_interventions) is int and expected_human_interventions == 4,
               "replacement test needs the reviewed observed-intervention ledger count of four")
        expect(len(initial_events) == 12
               and initial_events[-1]["event_type"] == "dispatch.launch.lookup-recorded"
               and initial_events[-1]["payload"]["receipt_id"] == "37204777409",
               "replacement fixture must preserve the complete actual historical twelve-event prefix")
        expect(run_id != REPLACEMENT_FAILED_RUN
               and sealed["output_candidate_pr_number"] != REPLACEMENT_FAILED_PR
               and new_head != REPLACEMENT_FAILED_HEAD
               and sealed["recovery_dispatch_key"] not in {ARMED_RECOVERY_KEY, h["external_dispatch_key"]}
               and source_sha != REPLACEMENT_FAILED_SOURCE,
               "replacement reused failed execution/output or historical dispatch authority")
    recovery_source = preflight.composition.recovery_result_source
    workflows = recovery_source.config.workflows
    slot = SimpleNamespace(scenario="happy_path", feature_id=h["feature_id"], target_ref=h["target_ref"])
    state = {"head": old_head, "run_patch": {}, "patches": 0}
    routes = {}

    def candidate():
        return {"number": h["candidate_pr_number"], "state": "open", "draft": False,
                "html_url": f"https://github.com/{repository}/pull/{h['candidate_pr_number']}",
                "head": {"ref": h["target_ref"], "sha": state["head"], "repo": {"full_name": repository}},
                "base": {"ref": "main", "repo": {"full_name": repository}}}

    def external_json(path):
        if path == "/pulls":
            return [candidate(), deepcopy(retained_failed_pr)] if replacement else [candidate()]
        if replacement and path == f"/pulls/{REPLACEMENT_FAILED_PR}":
            return deepcopy(retained_failed_pr)
        if path == f"/pulls/{h['candidate_pr_number']}":
            return candidate()
        if path == f"/compare/{old_head}...{new_head}":
            return {"status": "ahead", "ahead_by": 1, "behind_by": 0,
                    "merge_base_commit": {"sha": old_head}}
        if path == f"/actions/runs/{run_id}":
            status, _, raw = recovery_source.http(
                method="GET", url=f"https://api.github.com/repos/{repository}{path}", token="read")
            expect(status == 200, "recovery run fixture did not resolve")
            value = json.loads(raw)
            value.update(repository={"full_name": repository})
            value.update(state["run_patch"])
            return value
        if path in routes:
            return deepcopy(routes[path])
        if path == f"/pulls/{sealed['output_candidate_pr_number']}":
            status, _, raw = recovery_source.http(
                method="GET", url=f"https://api.github.com/repos/{repository}{path}", token="read")
            expect(status == 200, "Developer output fixture did not resolve")
            return json.loads(raw)
        if path.startswith("/git/refs/heads/"):
            return {"object": {"sha": state["head"]}}
        raise AssertionError("finalization escaped exact HTTP fixture: " + path)

    def suffix(url):
        return urlparse(url).path.split("/repos/" + repository, 1)[1]

    def get_json(url, headers):
        return 200, external_json(suffix(url))

    def source_http(*, method, url, token):
        expect(method == "GET", "source unexpectedly mutated external state")
        value = external_json(suffix(url))
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def handoff_http(method, url, headers, body):
        path = suffix(url)
        if method == "PATCH":
            expect(path.startswith("/git/refs/heads/") and body == {"sha": new_head, "force": False},
                   "handoff escaped one exact non-force fast-forward")
            state["head"] = new_head
            state["patches"] += 1
            return 200, {"object": {"sha": new_head}}
        expect(method == "GET", "handoff sent an unexpected HTTP mutation")
        return 200, external_json(path)

    normal_source = FirstAttemptDigestBoundGhAwResultSource(
        recovery_source.config, target_repository=repository, http=source_http)
    bound_loader = DogfoodRecoveryBoundContentLoader(
        result_source=normal_source, recovery_result_source=recovery_source,
        policy_authority=preflight.composition.policy_authority)
    bound_loader.bind_runtime(runtime)
    final_preflight = SimpleNamespace(
        execution=preflight.execution, slot=slot, workflows=workflows,
        candidate_pr_number=h["candidate_pr_number"], candidate_head_sha=new_head,
        composition=SimpleNamespace(runtime=runtime, result_source=normal_source,
            recovery_result_source=recovery_source,
            policy_authority=preflight.composition.policy_authority))

    def emit(event_type, payload):
        current = runtime.backend.snapshot
        sequence = len(operation_events(current, operation_id)) + 1
        event_id = f"happy-fixture-{sequence}"
        event = make_event(operation_id=operation_id, generation=h["generation"],
            sequence=sequence, event_id=event_id, event_type=event_type,
            occurred_at=runtime.clock(), payload=payload,
            trusted_context_digest=preflight.trusted_context_digest)
        current.files[event_path(operation_id, sequence, event_id)] = event
        return event

    def reserve_and_authorize(context, task_identity):
        runtime.backend.snapshot.files[reservation_path(context.semantic_effect_key)] = {
            "semantic_effect_key": context.semantic_effect_key,
            "external_dispatch_key": context.external_dispatch_key,
            "target_repository": context.target_repository, "feature_id": context.feature_id,
            "expected_revision": context.expected_revision, "current_stage": context.feature_stage,
            "role": context.role, "candidate_head_sha": context.candidate_head_sha,
            "task_identity": task_identity}
        emit("dispatch.launch.authorized", {
            "external_dispatch_key": context.external_dispatch_key,
            "semantic_effect_key": context.semantic_effect_key, "dispatch_id": context.dispatch_id,
            "feature_id": context.feature_id, "expected_revision": context.expected_revision,
            "stage": context.feature_stage, "role": context.role,
            "candidate_head_sha": context.candidate_head_sha, "task_id": context.task_id})

    def launch(context, step, receipt):
        emit("loop.step.selected", {"step": step})
        emit("dispatch.claimed", {"external_dispatch_key": context.external_dispatch_key})
        reserve_and_authorize(context, context.task_id)
        emit("dispatch.launch.lookup-recorded", {
            "external_dispatch_key": context.external_dispatch_key,
            "lookup_state": "LAUNCHED", "receipt_id": str(receipt)})

    def record_callback(callback):
        runtime.commit_replanned(lambda current: plan_vertical_callback_record(
            current, context=callback["context"], callback_id=callback["callback_id"],
            worker_payload=callback["worker_payload"], receipts=callback["receipts"],
            occurred_at=runtime.clock(), trusted_context_digest=preflight.trusted_context_digest))

    def accept_and_persist(callback):
        context = callback["context"]
        emit("worker.result.validated", {"callback_id": callback["callback_id"],
            "role": context.role, "dispatch_id": context.dispatch_id})
        material = {"feature_event_id": "happy-" + context.role,
            "expected_revision": context.expected_revision, "target_ref": context.target_ref,
            "candidate_head_sha": new_head}
        for phase in ("requested", "linearized", "confirmed"):
            emit("persist." + phase, dict(material, result_revision=context.expected_revision + 1))
        if context.role != "qa":
            emit("loop.stable-stop", {"status": "WAITING_EXTERNAL"})

    try:
        dev_context = developer_callback["context"]
        if not replacement:
            expect(not operation_events(snapshot, operation_id), "happy fixture expected frozen recovery sidecars only")
            emit("operation.started", {"operation_profile": VERTICAL_PROFILE,
                "target_repository": repository, "feature_id": h["feature_id"], "expected_revision": 1})
            # Preserve original authorization/receipt positions at sequences 11/12.
            emit("loop.step.selected", {"step": "IMPLEMENTATION_WORK"})
            emit("dispatch.claimed", {"external_dispatch_key": h["external_dispatch_key"]})
            for _ in range(7):
                emit("feature.event.translated", {"purpose": "historical-fixture-prefix"})
            reserve_and_authorize(dev_context, h["task_identity"])
            emit("dispatch.launch.lookup-recorded", {
                "external_dispatch_key": h["external_dispatch_key"], "lookup_state": "LAUNCHED",
                "receipt_id": "37204777409"})
        else:
            expect(dev_context.external_dispatch_key == h["external_dispatch_key"]
                   and dev_context.dispatch_id == h["dispatch_id"]
                   and dev_context.candidate_head_sha == old_head,
                   "replacement collector changed original semantic callback authority")
        record_callback(deepcopy(developer_callback))
        provider = DogfoodGitHubCandidateProvider(slot=slot, repository=repository,
            token="fixture", http_get=get_json)
        provider.bind_runtime(runtime)
        executor = TrustedVerticalExecutor(runtime=runtime, feature_gateway=None,
            persist_gateway=None, dispatch_gateway=None,
            config=TrustedVerticalExecutorConfig(target_ref=h["target_ref"],
                trusted_context_digest=preflight.trusted_context_digest, legacy_compatibility_mode=True))
        handoff = DogfoodCandidateHandoff(slot=slot, repository=repository, token="fixture",
            candidate_provider=provider, http_request=handoff_http)
        handoff.content_loader = bound_loader
        recovery_callback_validation_fence_tests(runtime, executor, developer_callback, bound_loader)
        handoff.adopt(executor=executor, context=dev_context,
            callback_id=developer_callback["callback_id"], receipts=developer_callback["receipts"])
        expect(vertical_projection(runtime.backend.read_snapshot(), operation_id)["generation"] == h["generation"],
               "durable Developer handoff broke production Store projection")
        expect(state["patches"] == 1 and state["head"] == new_head,
               "actual handoff did not advance exact fixture")
        accept_and_persist(developer_callback)

        gate_runs = []
        for index, (role, stage, step) in enumerate((
            ("reviewer", "code-review", "CODE_REVIEW"),
            ("qa", "verification", "VERIFICATION_QA")), start=2):
            gate_run = run_id + index - 1
            gate_runs.append(gate_run)
            key, workflow, comment_id = "dispatch-" + str(index) * 40, workflows.workflow_for(role), 9100 + index
            comment_url = f"https://github.com/{repository}/pull/{h['candidate_pr_number']}#issuecomment-{comment_id}"
            context = replace(dev_context, role=role, feature_stage=stage, expected_revision=index,
                semantic_effect_key=str(index) * 64, external_dispatch_key=key,
                dispatch_id="dc-" + str(index) * 40, runtime_receipt_identity=str(gate_run),
                task_id="happy-" + role, candidate_pr_number=h["candidate_pr_number"],
                candidate_head_sha=new_head, worker_identity=f"gh-aw:{workflow}@{source_sha}")
            external = {
                "version": "0.1.0", "contract": "ai-sdlc-gh-aw-" + role + "-result-v0.1",
                "id": "happy-" + role, "feature_id": context.feature_id, "task_id": context.task_id,
                "stage": stage, "role": role, "expected_revision": index,
                "target_repository": repository, "target_ref": context.target_ref,
                "candidate_pr_number": context.candidate_pr_number, "candidate_head_sha": new_head,
                "verdict": "PASS", "occurred_at": runtime.clock(),
                "evidence": [{"id": "happy-evidence", "type": "review" if role == "reviewer" else "verification",
                    "status": "pass", "uri": f"https://github.com/{repository}/actions/runs/{gate_run}"}]}
            if role == "reviewer":
                external["findings"] = []
            else:
                external["checks"] = [{"name": "runtime", "status": "pass"}]
                external["coverage"] = [{"criterion": "happy path", "status": "pass"}]
            routes[f"/issues/comments/{comment_id}"] = {
                "id": comment_id, "html_url": comment_url,
                "issue_url": f"https://api.github.com/repos/{repository}/issues/{context.candidate_pr_number}",
                "user": {"type": "Bot"}, "body": _GATE_START + json.dumps(external) + _GATE_END}
            routes[f"/actions/runs/{gate_run}"] = {
                "id": gate_run, "run_attempt": 1, "repository": {"full_name": repository},
                "html_url": f"https://github.com/{repository}/actions/runs/{gate_run}",
                "path": ".github/workflows/" + workflow, "display_title": "AI-SDLC gh-aw " + key,
                "event": "workflow_dispatch", "head_branch": "main", "head_sha": source_sha,
                "status": "completed", "conclusion": "success"}
            job_id = 9200 + index
            routes[f"/actions/runs/{gate_run}/jobs"] = {"jobs": [
                {"id": job_id - 100, "name": "safe_outputs", "conclusion": "success"},
                {"id": job_id, "name": "conclusion", "conclusion": "success"}]}
            log_values = {
                "SOURCE_RUN_ID": gate_run,
                "SOURCE_WORKFLOW_REF": f"{repository}/.github/workflows/{workflow}@refs/heads/main",
                "TARGET_REPOSITORY": repository, "TARGET_REF": context.target_ref,
                "FEATURE_ID": context.feature_id, "EXPECTED_REVISION": index,
                "STAGE": stage, "ROLE": role, "TRUSTED_TASK_ID": context.task_id,
                "CANDIDATE_PR_NUMBER": context.candidate_pr_number, "CANDIDATE_HEAD_SHA": new_head,
                "COMMENT_ID": comment_id, "COMMENT_URL": comment_url}
            routes[f"/actions/jobs/{job_id}/logs"] = "".join(
                f"2026-10-09T07:00:00Z   {name}: {value}\n" for name, value in log_values.items()).encode()
            launch(context, step, gate_run)
            trusted = dict(asdict(context), launch_candidate_head_sha=new_head)
            resolved = normal_source.resolve(external_dispatch_key=key,
                expected_receipt_identity=str(gate_run), trusted_context=trusted)
            receipts = _build_receipts(coordinator=SimpleNamespace(content_loader=normal_source.load_content),
                context=context, outputs=resolved.outputs,
                declared_outputs={row["label"]: row["kind"] for row in resolved.role_payload["outputs"]},
                collected_at=runtime.clock())
            callback = {"context": context, "receipts": receipts, "worker_payload": resolved.role_payload,
                "callback_id": "gh-aw-callback-" + digest_json({
                    "operation_id": operation_id, "generation": h["generation"], "external_dispatch_key": key,
                    "runtime_receipt_identity": str(gate_run), "run_id": gate_run})[:24]}
            record_callback(callback)
            accept_and_persist(callback)
        emit("notification.created", {"notification_id": "happy-done"})
        emit("operation.done", {"feature_revision": 4})
        emit("loop.stable-stop", {"status": "DONE"})
        expect(vertical_projection(runtime.backend.read_snapshot(), operation_id)["status"] == "DONE",
               "real completed snapshot did not project DONE")
        observation = {
            "scenario": "happy_path", "repository": repository, "feature_id": h["feature_id"],
            "target_ref": h["target_ref"], "operation_id": operation_id,
            "installation_commit_sha": source_sha, "candidate_pr_number": h["candidate_pr_number"],
            "candidate_head_sha": new_head, "final_status": "DONE",
            "workflow_run_ids": [run_id, *gate_runs], "runtime_receipt_identity": str(gate_runs[-1]),
            "repeated_continue_messages": 0, "release_eligible": False, "provenance_verified": False}

        class Response:
            status = 200
            def __init__(self, value): self.value = value
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return json.dumps(self.value).encode()

        def fake_urlopen(req, timeout):
            expect(req.get_method() == "GET", "provenance attempted a non-read request")
            return Response(external_json(suffix(req.full_url)))

        def finalize():
            return finalizer.finalize(observation=observation, preflight=final_preflight,
                source_run_id=run_id + 10, finalizer_run_id=run_id + 11, github_token="fixture")

        with patch.object(provenance, "urlopen", side_effect=fake_urlopen):
            record = finalize()
            expect(record["verdict"] == "PASS" and record["release_eligible"] is True
                   and record["runtime"]["workflow_run_ids"] == [run_id, *gate_runs]
                   and record["assertions"]["independent_review_observed"] is True,
                   "complete production happy-path finalization did not verify")
            expect(runtime.backend.snapshot.get(driver.RECOVERY_AUTHORIZATION_PATH) == original_auth
                   and runtime.backend.snapshot.get(driver.RECOVERY_ATTEMPT_PATH) == original_attempt,
                   "happy-path finalization rewrote frozen ARMED authorization history")
            expect(original_auth["source_head_sha"] != source_sha
                   and sealed["execution_source_head_sha"] == source_sha
                   and (sealed["recovery_dispatch_key"].startswith("dispatch-") if replacement
                        else sealed["recovery_dispatch_key"].startswith("recovery-")),
                   "fixture failed to exercise distinct real execution key and old/new source separation")
            if replacement:
                expect(record["counts"]["human_interventions"] == expected_human_interventions,
                       "replacement finalizer omitted or invented owner intervention history")
                required_disclosure = {
                    f"https://github.com/{repository}/actions/runs/{REPLACEMENT_FAILED_RUN}",
                    f"https://github.com/{repository}/pull/{REPLACEMENT_FAILED_PR}",
                    "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6076638838",
                    "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6076882835",
                }
                expect({uri.lower() for uri in required_disclosure}
                       <= {uri.lower() for uri in record["evidence_uris"]},
                       "replacement finalizer omitted failed run/output/admission evidence")
                expect(all(runtime.backend.snapshot.get(path) == value
                           for path, value in frozen_predecessor.items()),
                       "replacement pipeline rewrote predecessor or immutable replacement records")
                expect(operation_events(runtime.backend.snapshot, operation_id)[:12] == initial_events,
                       "replacement pipeline rewrote the original twelve-event logical launch history")
                expect(external_json(f"/pulls/{REPLACEMENT_FAILED_PR}") == retained_failed_pr
                       and state["patches"] == 1,
                       "replacement closed/adopted failed PR or repeated fixture fast-forward")
            for label, mutate, restore in (
                ("execution-source", lambda: state["run_patch"].update(head_sha=original_auth["source_head_sha"]),
                 lambda: state["run_patch"].clear()),
                ("recovery-key", lambda: state["run_patch"].update(
                    display_title="AI-SDLC gh-aw " + h["external_dispatch_key"]),
                 lambda: state["run_patch"].clear()),
                ("final-candidate-head", lambda: state.update(head="9" * 40),
                 lambda: state.update(head=new_head)),
            ):
                mutate()
                try:
                    finalize()
                except (finalizer.V03DogfoodPostRunFinalizerError,
                        provenance.DogfoodProvenanceVerificationError, VerticalInvariantError, ValueError):
                    pass
                except AssertionError as exc:
                    expected_reason = (
                        "candidate head changed after dogfood evidence was recorded"
                        if label == "final-candidate-head" else
                        "real run differs from protected exact-main launch binding")
                    expect(str(exc) == "real dogfood happy_path: trusted provenance verification failed: " + expected_reason,
                           "negative finalizer failed for an unexpected assertion: " + str(exc))
                else:
                    raise AssertionError("actual finalizer accepted tampered " + label)
                finally:
                    restore()
            # Rehash a forged envelope so the exact collector URI/lease/content
            # binding must reject it independently of the envelope digest.
            dev_event = next(row for row in operation_events(runtime.backend.snapshot, operation_id)
                             if row["event_type"] == "worker.callback.recorded")
            original_payload = deepcopy(dev_event["payload"])
            payload = dev_event["payload"]
            envelope = payload["trusted_callback_envelope"]
            envelope["collected_outputs"][0]["trusted_uri"] = envelope["collected_outputs"][0]["trusted_uri"].replace(
                "--lease-", "--lease-0", 1)
            payload["trusted_callback_envelope_digest"] = digest_json(envelope)
            payload["callback_digest"] = digest_json({
                "worker_payload": envelope["worker_payload"], "receipts": envelope["collected_outputs"]})
            try:
                finalize()
            except (finalizer.V03DogfoodPostRunFinalizerError,
                    provenance.DogfoodProvenanceVerificationError, VerticalInvariantError, ValueError):
                pass
            except AssertionError as exc:
                expect(str(exc) == (
                    "real dogfood happy_path: trusted provenance verifier errored: "
                    "V03DogfoodPostRunFinalizerError: fresh outputs differ from original sealed receipt locations"),
                    "forged-URI negative failed for an unexpected assertion: " + str(exc))
            else:
                raise AssertionError("actual finalizer accepted forged leased Developer URI")
            finally:
                dev_event["payload"] = original_payload
            if replacement:
                frozen_lookup = next(row for row in operation_events(runtime.backend.snapshot, operation_id)
                                     if row["sequence"] == 5)
                old_payload = deepcopy(frozen_lookup["payload"])
                frozen_lookup["payload"].update(lookup_state="LAUNCHED", receipt_id="37204777409")
                try:
                    finalize()
                except (finalizer.V03DogfoodPostRunFinalizerError, VerticalInvariantError):
                    pass
                else:
                    raise AssertionError("finalizer hid consumption in a superseded generation")
                finally:
                    frozen_lookup["payload"] = old_payload
            expect(finalize()["verdict"] == "PASS", "restored production happy-path did not reverify")
    finally:
        runtime.backend.snapshot = saved_snapshot
    print("- real recovery Developer/Reviewer/QA finalizer verifies leased callbacks, handoff and provenance")





def fixed_replacement_fixture():
    """Complete frozen Store and production transport/source; fake external HTTP."""
    import json
    import subprocess
    from copy import deepcopy
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlparse
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_model import StoreSnapshot
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig
    from v03_dogfood_fixture_pool import require_slot
    import v03_dogfood_full_composition as composition
    subject = driver_subject
    root = Path(__file__).resolve().parents[1]
    commit = composition.REPLACEMENT_PREDECESSOR_STORE
    if not hasattr(fixed_replacement_fixture, "_frozen_files"):
        listed = subprocess.run(["git", "ls-tree", "-r", "--name-only", commit, "state/operator/v1"],
                                cwd=root, check=True, capture_output=True, text=True).stdout.splitlines()
        files = {}
        for path in listed:
            if path.endswith(".json"):
                files[path] = json.loads(subprocess.run(["git", "show", f"{commit}:{path}"],
                    cwd=root, check=True, capture_output=True).stdout)
        fixed_replacement_fixture._frozen_files = deepcopy(files)
    # Each test gets an independent deep copy of the same immutable commit.
    frozen = StoreSnapshot(commit, deepcopy(fixed_replacement_fixture._frozen_files))
    composition.validate_replacement_predecessor(frozen)
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
                             snapshot=deepcopy(frozen))
            self.commit_count = 0
        def commit(self, plan, receipt):
            self.commit_count += 1
            result = super().commit(plan, receipt)
            self.snapshot = StoreSnapshot(f"{self.commit_count:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    runtime = OperatorStoreRuntime(backend=Backend(),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED), clock=lambda: "2026-10-09T08:00:00Z")
    http, transport, gateway, _ = recovery_actions_transport_tests(create_only=True)()
    h = subject.HISTORICAL_PREHTTP_RECOVERY
    state = {"run_patch": {}, "old_run_patch": {}, "job_patch": {}, "artifact_patch": {},
             "pr_patch": {}, "route_fail": None, "old_visible": True, "unknown": False}
    new_run = 40000000002
    def pr(number):
        old = number == composition.REPLACEMENT_FAILED_PR
        run = composition.REPLACEMENT_FAILED_RUN if old else new_run
        return {"number": number, "id": 1900 + number, "node_id": f"PR_fixture_{number}",
                "html_url": f"https://github.com/dream-xin/ai-sdlc/pull/{number}",
                "state": "open", "draft": True, "title": "[ai-sdlc gh-aw] bounded implementation",
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "head": {"ref": (f"gh-aw/{h['feature_id']}-{run}-v1-c1a21d03a1e716db" if old else
                                  f"gh-aw/{h['feature_id']}-{run}-v1-fixed"),
                         "sha": composition.REPLACEMENT_FAILED_HEAD if old else "7" * 40,
                         "repo": {"full_name": "dream-xin/ai-sdlc"}},
                "base": {"ref": h["target_ref"], "sha": h["candidate_head_sha"],
                         "repo": {"full_name": "dream-xin/ai-sdlc"}}}
    def run_row(run):
        old = run == composition.REPLACEMENT_FAILED_RUN
        return dict({
            "id": run, "run_attempt": 1, "repository": {"full_name": "dream-xin/ai-sdlc"},
            "html_url": f"https://github.com/dream-xin/ai-sdlc/actions/runs/{run}",
            "path": ".github/workflows/" + subject.RECOVERY_WORKFLOW,
            "display_title": "AI-SDLC gh-aw " + (composition.ARMED_RECOVERY_KEY if old else http.key),
            "event": "workflow_dispatch", "head_branch": "main",
            "head_sha": composition.REPLACEMENT_FAILED_SOURCE if old else "5" * 40,
            "status": "completed", "conclusion": "failure" if old else "success",
        }, **state["old_run_patch" if old else "run_patch"])
    def job_rows(run):
        old = run == composition.REPLACEMENT_FAILED_RUN
        ids = composition.REPLACEMENT_ADMISSION["failed_jobs"] if old else {
            "activation": 9000, "agent": 9001, "detection": 9002, "safe_outputs": 9003, "conclusion": 9004}
        rows = []
        for name, job_id in ids.items():
            row = {"id": job_id, "name": name, "run_id": run, "run_attempt": 1,
                   "head_sha": composition.REPLACEMENT_FAILED_SOURCE if old else "5" * 40,
                   "status": "completed", "conclusion": "failure" if old and name == "conclusion" else "success",
                   "steps": []}
            if not old and name in {"agent", "safe_outputs"}:
                row["steps"] = [{"name": "Reject rerun before model execution" if name == "agent" else
                     "Require first attempt and affirmative detection before Safe Outputs effects",
                     "status": "completed", "conclusion": "success"}]
            if (old, name) in state["job_patch"]:
                row.update(state["job_patch"][(old, name)])
            rows.append(row)
        return {"total_count": len(rows), "jobs": rows}
    def external(path):
        if path == f"/actions/runs/{composition.REPLACEMENT_FAILED_RUN}":
            return run_row(composition.REPLACEMENT_FAILED_RUN)
        if path == f"/actions/runs/{new_run}":
            return run_row(new_run)
        for run in (composition.REPLACEMENT_FAILED_RUN, new_run):
            if path == f"/actions/runs/{run}/attempts/1/jobs":
                return job_rows(run)
        if path == "/pulls":
            return [pr(composition.REPLACEMENT_FAILED_PR), dict(pr(901), **state["pr_patch"])]
        if path in ("/pulls/574", "/pulls/901"):
            return dict(pr(int(path.rsplit("/", 1)[1])), **state["pr_patch"])
        if path == f"/actions/runs/{composition.REPLACEMENT_FAILED_RUN}/artifacts":
            return {"total_count": 1, "artifacts": [dict({
                "id": composition.REPLACEMENT_ADMISSION["failed_artifact_id"], "name": "safe-outputs-items",
                "expired": False, "digest": composition.REPLACEMENT_ADMISSION["failed_artifact_digest"],
                "workflow_run": {"id": composition.REPLACEMENT_FAILED_RUN,
                    "head_sha": composition.REPLACEMENT_FAILED_SOURCE, "head_branch": "main",
                    "repository_id": 1326302284, "head_repository_id": 1326302284},
            }, **state["artifact_patch"])]}
        if path == f"/actions/runs/{new_run}/artifacts":
            value, _ = recovery_safe_output_artifact_fixture(run_id=new_run, source_head="5" * 40, pr=pr(901))
            return value
        if path == f"/actions/artifacts/{new_run + 100}/zip":
            _, archive = recovery_safe_output_artifact_fixture(run_id=new_run, source_head="5" * 40, pr=pr(901))
            return archive
        raise AssertionError("replacement HTTP escaped fixed inventory: " + path)
    old_http = http.__call__
    def action_http(*, method, url, token, body=None):
        suffix = urlparse(url).path.split("/repos/dream-xin/ai-sdlc", 1)[1]
        if suffix.startswith("/actions/workflows/") or suffix == "/git/ref/heads/main":
            return old_http(method=method, url=url, token=token, body=body)
        expect(method == "GET" and body is None, "replacement proof attempted mutation")
        if suffix == state["route_fail"]:
            return 503, {}, b"unavailable"
        value = external(suffix)
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()
    transport._transport_http = action_http
    def source_http(*, method, url, token):
        return action_http(method=method, url=url, token=token)
    source = composition.RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(control_repository="dream-xin/ai-sdlc",
            control_token="read", target_token="read", collector_identity=composition.COLLECTOR_IDENTITY,
            workflows=gateway.workflows), target_repository="dream-xin/ai-sdlc", http=source_http)
    pf = SimpleNamespace(slot=require_slot("happy_path"), workflows=gateway.workflows,
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha="5" * 40),
        trusted_context_digest="6" * 64, composition=SimpleNamespace(runtime=runtime,
            policy_authority=recovery_policy_fixture(), actions_transport=transport,
            recovery_dispatch_gateway=gateway, candidate_provider=transport.candidate_provider,
            result_source=source, recovery_result_source=source))
    authorization = subject._replacement_authorization(frozen, pf)
    http.key = authorization["recovery_dispatch_key"]
    http.rows[subject.RECOVERY_WORKFLOW] = [run_row(composition.REPLACEMENT_FAILED_RUN)]
    return pf, http, state, frozen


def fixed_replacement_admission_tests():
    from copy import deepcopy
    from unittest.mock import patch
    from operator_store_git import CasConflict
    from operator_vertical import VerticalInvariantError
    from operator_store_model import canonical_json
    import v03_dogfood_full_composition as composition
    subject = driver_subject
    def reject(action, pf, http, message):
        before = canonical_json(pf.composition.runtime.backend.read_snapshot().files)
        writes, posts = pf.composition.runtime.backend.commit_count, http.posts
        try: action()
        except (VerticalInvariantError, V03DogfoodRuntimeDriverError, composition.V03DogfoodCompositionError):
            pass
        else: raise AssertionError("replacement accepted " + message)
        expect(pf.composition.runtime.backend.commit_count == writes and http.posts == posts
               and canonical_json(pf.composition.runtime.backend.read_snapshot().files) == before,
               "rejected replacement changed protected Store or dispatched: " + message)
    pf, http, state, frozen = fixed_replacement_fixture()
    subject._bounded_recovery_identity(frozen, pf)
    first = subject._plan_fixed_replacement(frozen, preflight=pf)
    second = subject._plan_fixed_replacement(frozen, preflight=pf)
    expect(first.result["authorization"]["recovery_dispatch_key"] == second.result["authorization"]["recovery_dispatch_key"]
           and first.result["acquired"] is True and len(first.mutations) == 2 and http.posts == 0,
           "fixed replacement identity is not deterministic/prevalidated before CAS")
    runtime = pf.composition.runtime
    runtime.backend.commit(first, runtime.protected_receipt())
    try: runtime.backend.commit(second, runtime.protected_receipt())
    except CasConflict: pass
    else: raise AssertionError("two fixed replacement CAS contenders committed")
    writes = runtime.backend.commit_count
    replay = subject._commit_recovery_nonempty(runtime, lambda snapshot:
        subject._plan_fixed_replacement(snapshot, preflight=pf))
    expect(replay["acquired"] is False and runtime.backend.commit_count == writes,
           "replacement replay minted another claim or empty Store commit")
    reject(lambda: subject.recover_approved_replacement(pf), pf, http, "crash-after-claim empty lookup")
    for path, value in frozen.files.items():
        expect(runtime.backend.snapshot.get(path) == value, "replacement rewrote frozen predecessor")
    changed = deepcopy(runtime.backend.snapshot)
    changed.files[composition.REPLACEMENT_AUTHORIZATION_PATH]["observed_accounting"]["human_interventions"] = 0
    reject(lambda: composition.validate_replacement_chain(changed), pf, http, "rewritten observed accounting")
    for path in composition.REPLACEMENT_PATHS:
        for value in (None, {}, {"ordinal": 2}):
            bad = deepcopy(frozen); bad.files[path] = value
            reject(lambda: subject._plan_fixed_replacement(bad, preflight=pf), pf, http, "partial/null route " + path)
    for label, mutate in (
        ("old attempt2", lambda pf, st: st["old_run_patch"].update(run_attempt=2)),
        ("old active", lambda pf, st: st["old_run_patch"].update(status="in_progress", conclusion=None)),
        ("old moved source", lambda pf, st: st["old_run_patch"].update(head_sha="0" * 40)),
        ("old active job", lambda pf, st: st["job_patch"].update({(True, "safe_outputs"): {"status": "in_progress"}})),
        ("old archive digest", lambda pf, st: st["artifact_patch"].update(digest="sha256:" + "0" * 64)),
        ("retained draft closed", lambda pf, st: st["pr_patch"].update(state="closed")),
        ("provider unavailable", lambda pf, st: st.update(route_fail=f"/actions/runs/{composition.REPLACEMENT_FAILED_RUN}")),
    ):
        pf_bad, http_bad, bad_state, _ = fixed_replacement_fixture()
        mutate(pf_bad, bad_state)
        reject(lambda: subject.recover_approved_replacement(pf_bad), pf_bad, http_bad, label)
    pf_bad, http_bad, _, _ = fixed_replacement_fixture()
    http_bad.main_sources = ["0" * 40]
    reject(lambda: subject.recover_approved_replacement(pf_bad), pf_bad, http_bad, "stale main")
    pf_bad, http_bad, _, _ = fixed_replacement_fixture()
    with patch.object(subject, "_recovery_worker_blobs", return_value={}):
        reject(lambda: subject.recover_approved_replacement(pf_bad), pf_bad, http_bad, "Worker source drift")
    for role in ("reviewer", "qa"):
        pf_bad, http_bad, _, _ = fixed_replacement_fixture()
        workflow = pf_bad.composition.recovery_dispatch_gateway.workflows.workflow_for(role)
        old = deepcopy(http_bad.rows[subject.RECOVERY_WORKFLOW][0]); old["path"] = ".github/workflows/" + workflow
        http_bad.rows[workflow] = [old]
        reject(lambda: subject.recover_approved_replacement(pf_bad), pf_bad, http_bad, "old cross-role collision")
    # Fail before the claim on incomplete global scans, duplicates, stale
    # candidate/payload binding, or any existing handoff for this logical task.
    for label in ("page-failure", "new-duplicate", "candidate-drift", "prior-handoff"):
        pf_bad, http_bad, _, frozen_bad = fixed_replacement_fixture()
        if label == "page-failure":
            http_bad.fail.add(pf_bad.composition.recovery_dispatch_gateway.workflows.qa_workflow)
        elif label == "new-duplicate":
            http_bad.rows[subject.RECOVERY_WORKFLOW].extend([
                http_bad.row(subject.RECOVERY_WORKFLOW, 40000000002),
                http_bad.row(subject.RECOVERY_WORKFLOW, 40000000003)])
        elif label == "candidate-drift":
            provider = pf_bad.composition.candidate_provider
            original_get = provider.http_get
            def moved_candidate(url, headers):
                status, rows = original_get(url, headers)
                rows = deepcopy(rows); rows[0]["head"]["sha"] = "0" * 40
                return status, rows
            provider.http_get = moved_candidate
        else:
            pf_bad.composition.runtime.backend.snapshot.files[
                f"state/operator/v1/operations/{subject.HISTORICAL_PREHTTP_RECOVERY['operation_id']}/dogfood-candidate-handoffs/foreign/intent.json"] = {}
        reject(lambda: subject.recover_approved_replacement(pf_bad), pf_bad, http_bad, label)
    # A successful POST still consumes the sole claim when result evidence fails.
    for label in ("attempt2", "boolean-attempt", "string-attempt", "failed-run", "missing-safety-guard", "failed-safety-guard", "old-output"):
        pf_bad, http_bad, st, _ = fixed_replacement_fixture()
        if label == "attempt2": st["run_patch"]["run_attempt"] = 2
        elif label == "boolean-attempt": st["run_patch"]["run_attempt"] = True
        elif label == "string-attempt": st["run_patch"]["run_attempt"] = "1"
        elif label == "failed-run": st["run_patch"]["conclusion"] = "failure"
        elif label == "missing-safety-guard":
            st["job_patch"][(False, "safe_outputs")] = {"steps": []}
        elif label == "failed-safety-guard":
            st["job_patch"][(False, "safe_outputs")] = {"steps": [{
                "name": "Require first attempt and affirmative detection before Safe Outputs effects",
                "status": "completed", "conclusion": "failure"}]}
        else: st["pr_patch"]["number"] = composition.REPLACEMENT_FAILED_PR
        for replay_index in range(2):
            try: subject.recover_approved_replacement(pf_bad)
            except (VerticalInvariantError, V03DogfoodRuntimeDriverError): pass
            else: raise AssertionError("replacement sealed rejected " + label)
            expect(http_bad.posts == 1
                   and composition.REPLACEMENT_ATTEMPT_PATH in pf_bad.composition.runtime.backend.snapshot.files
                   and composition.REPLACEMENT_RECEIPT_PATH not in pf_bad.composition.runtime.backend.snapshot.files,
                   "failed/uncertain replacement regained creation right: " + label)
    for label in ("prefix", "reservation", "extra-claim", "historical-consumption", "null-historical-seal"):

        pf_bad, http_bad, _, _ = fixed_replacement_fixture()
        from operator_store_model import operation_events, reservation_path, event_path, make_event
        snapshot = pf_bad.composition.runtime.backend.snapshot
        events = operation_events(snapshot, subject.HISTORICAL_PREHTTP_RECOVERY["operation_id"])
        if label == "null-historical-seal":
            snapshot.files[composition.RECOVERY_RECEIPT_PATH] = None
        elif label == "prefix":
            events[0]["occurred_at"] = "changed"
        elif label == "reservation":
            snapshot.files[reservation_path(subject.HISTORICAL_PREHTTP_RECOVERY["semantic_effect_key"])]["task_identity"] = "foreign"
        elif label == "historical-consumption":
            events[4]["payload"].update(lookup_state="LAUNCHED", receipt_id="37204777409")
        else:
            event = make_event(operation_id=subject.HISTORICAL_PREHTTP_RECOVERY["operation_id"], generation=1,
                sequence=13, event_id="unexpected-claim", event_type="dispatch.claimed",
                occurred_at=pf_bad.composition.runtime.clock(), payload={},
                trusted_context_digest=pf_bad.trusted_context_digest)
            snapshot.files[event_path(event["operation_id"], 13, "unexpected-claim")] = event
        reject(lambda: subject.recover_approved_replacement(pf_bad), pf_bad, http_bad, label)
    # Real prehost entry must reject corrupt partial replacement with zero effects.
    for path in composition.REPLACEMENT_PATHS:
        pf_bad, http_bad, _, _ = fixed_replacement_fixture()
        pf_bad.composition.runtime.backend.snapshot.files[path] = None
        with (patch.object(subject, "assemble_preflight", return_value=pf_bad),
              patch.object(subject, "_head", return_value="5" * 40),
              patch.object(subject, "V03DogfoodOpenAIResponsesHost") as host):
            reject(lambda: subject._execute_live(mode=subject.RUN, scenario="happy_path"),
                   pf_bad, http_bad, "prehost null replacement")
            expect(not host.called, "corrupt replacement constructed a model host")
    pf_retry, http_retry, _, _ = fixed_replacement_fixture()
    pf_retry.composition.runtime.backend.inject_conflict_once()
    expect(subject.recover_approved_replacement(pf_retry)["receipt_id"] == "40000000002"
           and http_retry.posts == 1, "CAS retry lost fixed identity or duplicated POST")
    pf, http, state, frozen = fixed_replacement_fixture()
    sealed = subject.recover_approved_replacement(pf)
    expect(http.posts == 1 and sealed["receipt_id"] == "40000000002"
           and sealed["output_candidate_pr_number"] == 901
           and sealed["recovery_dispatch_key"].startswith("dispatch-"),
           "actual fixed replacement transport/source did not seal one distinct output")
    expect(subject.recover_approved_replacement(pf) == sealed and http.posts == 1,
           "fixed replacement replay attempted another create")
    for path, value in frozen.files.items():
        expect(pf.composition.runtime.backend.snapshot.get(path) == value, "replacement altered old history")
    pf_lost, http_lost, _, _ = fixed_replacement_fixture()
    http_lost.ack_loss = True
    expect(subject.recover_approved_replacement(pf_lost)["receipt_id"] == "40000000002"
           and http_lost.posts == 1, "replacement ack-loss failed lookup-only convergence")
    print("- fixed replacement actual protected CAS/transport/source preserves old failure and permits one new create")
    return pf, sealed, frozen


def fixed_replacement_collector_pipeline_tests(preflight, sealed, *, expected_human_interventions):
    """Collect the sealed new run through the real launch, lease, receipt and content paths."""
    from copy import deepcopy
    from types import SimpleNamespace
    import v03_dogfood_full_composition as composition
    import v03_dogfood_runtime_driver as driver
    from operator_store_model import canonical_json, operation_events
    from operator_vertical import FeatureSnapshot, VerticalInvariantError, validate_collected_outputs

    runtime = preflight.composition.runtime
    original = runtime.backend.snapshot
    h = driver.HISTORICAL_PREHTTP_RECOVERY
    source = preflight.composition.recovery_result_source
    bound_loader = composition.DogfoodRecoveryBoundContentLoader(
        result_source=preflight.composition.result_source,
        recovery_result_source=source, policy_authority=preflight.composition.policy_authority)
    bound_loader.bind_runtime(runtime)
    calls = []

    def receive(**callback):
        context = callback["context"]
        feature = FeatureSnapshot(
            repository=context.target_repository, feature_id=context.feature_id,
            target_ref=context.target_ref, revision=1, manifest_digest="",
            current_stage="implementation", stages={}, gates={}, remediation_tasks=(), artifacts=(),
            candidate_pr_number=h["candidate_pr_number"], candidate_head_sha=h["candidate_head_sha"])
        validate_collected_outputs(context=context, feature=feature,
            worker_payload=callback["worker_payload"], receipts=callback["receipts"],
            content_loader=bound_loader)
        calls.append(deepcopy(callback))
        return {"status": "COLLECTED"}

    coordinator = SimpleNamespace(
        executor=SimpleNamespace(runtime=runtime, config=SimpleNamespace(target_ref=h["target_ref"])),
        content_loader=bound_loader, handle=receive)
    collector = composition.DogfoodRecoveryCollector(
        callback_coordinator=coordinator, result_source=source,
        workflows=preflight.composition.recovery_dispatch_gateway.workflows,
        control_repository=preflight.execution.repository, clock=runtime.clock,
        policy_authority=preflight.composition.policy_authority)
    before = canonical_json(original.files)
    import v03_dogfood_scenario_runner as runner
    run = runner._wait_current_dispatch(preflight, h["operation_id"], h["external_dispatch_key"])
    expect(run["id"] == int(sealed["receipt_id"]), "scenario wait selected the failed predecessor")
    expect(runner._launch_receipts(preflight, h["operation_id"]) ==
           ((int(sealed["receipt_id"]),), sealed["receipt_id"]),
           "scenario receipt mapping selected the old recovery route")
    expect(collector.handle(operation_id=h["operation_id"],
        external_dispatch_key=h["external_dispatch_key"]) == {"status": "COLLECTED"},
        "real fixed replacement collector failed before callback validation")
    expect(len(calls) == 1 and canonical_json(runtime.backend.snapshot.files) == before,
           "replacement collection changed frozen Store before its actual callback planner")
    callback = calls[0]
    context = callback["context"]
    expect(context.runtime_receipt_identity == sealed["receipt_id"]
           and context.external_dispatch_key == h["external_dispatch_key"]
           and context.dispatch_id == h["dispatch_id"]
           and context.worker_identity.endswith("@" + sealed["execution_source_head_sha"]),
           "replacement collector conflated original semantic identity and new execution provenance")
    expect(len(callback["receipts"]) == 1
           and callback["receipts"][0]["trusted_uri"] == sealed["safe_output_uri"]
           and "--first-attempt--key-" + sealed["recovery_dispatch_key"] in sealed["safe_output_uri"]
           and "--run-" + sealed["receipt_id"] in sealed["safe_output_uri"],
           "replacement receipt did not carry the distinct real run/key lease")

    def reject_collector(snapshot, label):
        runtime.backend.snapshot = snapshot
        count = len(calls)
        state = canonical_json(snapshot.files)
        for read in (
            lambda: runner._wait_current_dispatch(preflight, h["operation_id"], h["external_dispatch_key"]),
            lambda: runner._launch_receipts(preflight, h["operation_id"]),
        ):
            try: read()
            except (VerticalInvariantError, runner.V03DogfoodScenarioRunnerError): pass
            else: raise AssertionError("scenario selected corrupt replacement: " + label)
        try:
            collector.handle(operation_id=h["operation_id"], external_dispatch_key=h["external_dispatch_key"])
        except VerticalInvariantError:
            pass
        else:
            raise AssertionError("real replacement collector admitted " + label)
        expect(len(calls) == count and canonical_json(snapshot.files) == state,
               "rejected replacement reached callback or mutated protected state")

    try:
        for path in composition.REPLACEMENT_PATHS:
            snapshot = deepcopy(original)
            snapshot.files[path] = None
            reject_collector(snapshot, "null fixed-path document " + path)
        for field, value in (
            ("receipt_id", str(composition.REPLACEMENT_FAILED_RUN)),
            ("output_candidate_pr_number", composition.REPLACEMENT_FAILED_PR),
            ("execution_source_head_sha", composition.REPLACEMENT_FAILED_SOURCE),
            ("safe_output_uri", sealed["safe_output_uri"].replace(
                sealed["recovery_dispatch_key"], composition.ARMED_RECOVERY_KEY, 1)),
            ("collector_dispatch_id", "foreign-semantic-namespace"),
        ):
            snapshot = deepcopy(original)
            snapshot.files[composition.REPLACEMENT_RECEIPT_PATH][field] = value
            reject_collector(snapshot, field)
        runtime.backend.snapshot = original
        expect(isinstance(bound_loader(sealed["safe_output_uri"]), bytes),
               "real replacement loader did not reauthenticate sealed output bytes")
        old_uri = sealed["safe_output_uri"].replace(
            sealed["recovery_dispatch_key"], composition.ARMED_RECOVERY_KEY, 1).replace(
            "--run-" + sealed["receipt_id"], "--run-" + str(composition.REPLACEMENT_FAILED_RUN), 1)
        try:
            bound_loader(old_uri)
        except VerticalInvariantError:
            pass
        else:
            raise AssertionError("replacement content loader adopted the failed predecessor URI")
        snapshot = deepcopy(original)
        snapshot.files[composition.REPLACEMENT_AUTHORIZATION_PATH] = None
        runtime.backend.snapshot = snapshot
        try:
            bound_loader(sealed["safe_output_uri"])
        except VerticalInvariantError:
            pass
        else:
            raise AssertionError("replacement loader fell back after null protected authorization")
        runtime.backend.snapshot = original
        expect(operation_events(original, h["operation_id"])[-1]["sequence"] == 12,
               "replacement fixture callback was consumed before finalizer pipeline")
        # This helper owns actual callback persistence, trusted candidate handoff,
        # production Reviewer/QA resolution and finalizer/provenance verification.
        happy_path_recovery_finalization_tests(preflight, sealed, callback,
            expected_human_interventions=expected_human_interventions)
        expect(canonical_json(original.files) == before,
               "isolated full pipeline modified its frozen predecessor fixture")
    finally:
        runtime.backend.snapshot = original
    print("- fixed replacement crosses real collector/content/Store callback/handoff/full-finalizer boundaries")


def fixed_replacement_route_lock_tests(preflight, sealed):
    """Presence locks the fixed route; malformed replacement state never falls back."""
    from copy import deepcopy
    from dataclasses import replace
    from unittest.mock import patch
    import v03_dogfood_full_composition as composition
    import v03_dogfood_post_run_finalizer as finalizer
    from operator_vertical import VerticalInvariantError
    from operator_store_model import canonical_json

    runtime = preflight.composition.runtime
    original = runtime.backend.snapshot
    binding = composition.recovery_execution_binding(preflight.composition.policy_authority)
    route = composition.recovery_route(original)
    expect(route["ordinal"] == 1 and route["receipt_path"] == composition.REPLACEMENT_RECEIPT_PATH,
           "replacement fixture did not select the one closed ordinal-one route")
    expect(finalizer._validated_recovery_chain(original, execution_binding=binding) == sealed,
           "replacement sealed chain failed real finalizer revalidation")
    expect(str(sealed["receipt_id"]) != str(composition.REPLACEMENT_FAILED_RUN)
           and sealed["output_candidate_pr_number"] != composition.REPLACEMENT_FAILED_PR
           and sealed["output_candidate_head_sha"] != composition.REPLACEMENT_FAILED_HEAD,
           "successful replacement reused the failed predecessor run or output")
    frozen_paths = (composition.RECOVERY_AUTHORIZATION_PATH, composition.RECOVERY_ATTEMPT_PATH,
                    composition.RECOVERY_CONTINUATION_PATH)
    frozen = {path: canonical_json(original.get(path)) for path in frozen_paths}
    historical = deepcopy(original)
    for path in composition.REPLACEMENT_PATHS:
        historical.files.pop(path, None)
    expect(composition.recovery_route(historical)["ordinal"] == 0,
           "valid historical fallback fixture is not available for route-lock tests")

    def reject(snapshot, label):
        runtime.backend.snapshot = snapshot
        before = canonical_json(snapshot.files)
        try:
            finalizer._validated_recovery_chain(snapshot, execution_binding=binding)
        except (finalizer.V03DogfoodPostRunFinalizerError, VerticalInvariantError):
            pass
        else:
            raise AssertionError("finalizer accepted malformed fixed replacement " + label)
        expect(canonical_json(snapshot.files) == before, "replacement validation mutated protected facts")
        expect({path: canonical_json(snapshot.get(path)) for path in frozen_paths} == frozen,
               "replacement rejection rewrote immutable predecessor facts")

    try:
        # Test null as actual document presence, not only truthy dictionaries.
        for path in composition.REPLACEMENT_PATHS:
            for value in (None, {}, "malformed", []):
                snapshot = deepcopy(historical)
                snapshot.files[path] = value
                expect(composition.replacement_present(snapshot) is True,
                       "present malformed replacement document unlocked the old route: " + path)
                try:
                    composition.recovery_route(snapshot)
                except VerticalInvariantError:
                    pass
                else:
                    raise AssertionError("partial replacement fell back to historical authority: " + path)
                reject(snapshot, path)
        for missing in composition.REPLACEMENT_PATHS:
            snapshot = deepcopy(original)
            del snapshot.files[missing]
            reject(snapshot, "missing " + missing)

        # Preserve all digests except the selected receipt field: each boundary
        # independently needs the new execution, new output and materialization.
        for field, value in (
            ("receipt_id", str(composition.REPLACEMENT_FAILED_RUN)),
            ("output_candidate_pr_number", composition.REPLACEMENT_FAILED_PR),
            ("output_candidate_head_sha", composition.REPLACEMENT_FAILED_HEAD),
            ("execution_source_head_sha", composition.REPLACEMENT_FAILED_SOURCE),
            ("execution_materialization_commit_sha", "0" * 40),
            ("execution_policy_receipt_digest", "0" * 64),
            ("execution_policy_bundle_digest", "0" * 64),
            ("recovery_dispatch_key", composition.ARMED_RECOVERY_KEY),
            ("collector_dispatch_id", "foreign-dispatch"),
            ("task_id", "foreign-task"),
        ):
            snapshot = deepcopy(original)
            snapshot.files[composition.REPLACEMENT_RECEIPT_PATH][field] = value
            reject(snapshot, field)
        # A fresh verifier authority must match current materialized source/bundle,
        # not just the source values self-described by the replacement receipt.
        for field in binding:
            changed = dict(binding)
            changed[field] = "0" * len(str(binding[field]))
            try:
                finalizer._validated_recovery_chain(original, execution_binding=changed)
            except (finalizer.V03DogfoodPostRunFinalizerError, VerticalInvariantError):
                pass
            else:
                raise AssertionError("replacement accepted foreign independent execution authority: " + field)
        expect(finalizer._validated_recovery_chain(original, execution_binding=binding) == sealed,
               "restored fixed replacement no longer verifies")
    finally:
        runtime.backend.snapshot = original
    print("- replacement presence/null/partial evidence locks routing and rejects old run/output/source authority")


def bounded_recovery_execution_tests():
    """Exercise CAS winner/replay/ack-loss and zero-POST failure behavior."""
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import patch
    from operator_store_model import StoreSnapshot, apply_plan_to_snapshot, digest_json
    import v03_dogfood_runtime_driver as subject
    from operator_vertical import VerticalInvariantError
    original_snapshot, proof, fence = armed_recovery_fixture()
    transport_fixture = recovery_actions_transport_tests()
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(
                repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
                snapshot=deepcopy(original_snapshot))
            self.commit_count = 0
        def commit(self, plan, receipt):
            self.commit_count += 1
            result = super().commit(plan, receipt)
            # Preserve real CAS semantics while modelling provider SHA40 refs.
            self.snapshot = StoreSnapshot(f"{self.commit_count:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    class Runtime(OperatorStoreRuntime):
        def __init__(self):
            super().__init__(
                backend=Backend(), protection_verifier=StaticProtectionVerifier(status=PROTECTED),
                clock=lambda: "2026-10-09T07:00:00Z")
        @property
        def n(self):
            # An empty plan still crosses the real Store commit boundary.
            return self.backend.commit_count
    def Gateway(ack_loss=False):
        http, transport, gateway, _ = transport_fixture()
        http.ack_loss = ack_loss
        gateway.fixture_http = http
        return gateway
    def ResultSource():
        from urllib.parse import urlparse
        from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
        from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig
        from v03_dogfood_full_composition import (
            RecoverySafeOutputGhAwResultSource, COLLECTOR_IDENTITY)
        import json
        h = subject.HISTORICAL_PREHTTP_RECOVERY
        key = original_snapshot.get(subject.RECOVERY_AUTHORIZATION_PATH)["recovery_dispatch_key"]
        run_id = 40000000002
        def http(*, method, url, token):
            expect(method == "GET", "actual recovery collector attempted mutation")
            suffix = urlparse(url).path.split("/repos/dream-xin/ai-sdlc", 1)[1]
            pr = {
                "number": 901, "id": 1901, "node_id": "PR_test_901",
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "html_url": "https://github.com/dream-xin/ai-sdlc/pull/901",
                "state": "open", "draft": True, "title": "[ai-sdlc gh-aw] bounded implementation",
                "head": {"ref": f"gh-aw/{h['feature_id']}-{run_id}-v1-fixed",
                         "sha": ("8" if source.mismatch else "7") * 40,
                         "repo": {"full_name": "dream-xin/ai-sdlc"}},
                "base": {"ref": h["target_ref"], "repo": {"full_name": "dream-xin/ai-sdlc"}}}
            if suffix == f"/actions/runs/{run_id}":
                value = {
                    "id": run_id, "run_attempt": 1,
                    "html_url": f"https://github.com/dream-xin/ai-sdlc/actions/runs/{run_id}",
                    "path": ".github/workflows/" + subject.RECOVERY_WORKFLOW,
                    "display_title": "AI-SDLC gh-aw " + key, "event": "workflow_dispatch",
                    "head_branch": "main", "head_sha": "5" * 40,
                    "status": "completed", "conclusion": "success"}
            elif suffix == f"/actions/runs/{run_id}/attempts/1/jobs":
                value = {"jobs": [{
                    "id": 9001, "name": "safe_outputs", "run_id": run_id, "run_attempt": 1,
                    "head_sha": "5" * 40, "status": "completed", "conclusion": "success"}]}
            elif suffix == "/pulls":
                value = [pr]
            elif suffix == "/pulls/901":
                value = pr
            elif suffix == f"/actions/runs/{run_id}/artifacts":
                value, _ = recovery_safe_output_artifact_fixture(run_id=run_id, source_head="5" * 40, pr=pr)
            elif suffix == f"/actions/artifacts/{run_id + 100}/zip":
                _, archive = recovery_safe_output_artifact_fixture(run_id=run_id, source_head="5" * 40, pr=pr)
                return 200, {}, archive
            else:
                raise AssertionError("collector escaped exact HTTP inventory: " + suffix)
            return 200, {}, json.dumps(value).encode()
        source = RecoverySafeOutputGhAwResultSource(
            GitHubActionsGhAwResultSourceConfig(
                control_repository="dream-xin/ai-sdlc", control_token="control",
                target_token="target", collector_identity=COLLECTOR_IDENTITY,
                workflows=GhAwVerticalWorkflowMap(
                    default_branch="main", developer_workflow=subject.RECOVERY_WORKFLOW,
                    reviewer_workflow="reviewer.lock.yml", qa_workflow="qa.lock.yml")),
            target_repository="dream-xin/ai-sdlc", http=http)
        source.mismatch = False
        return source
    def preflight(gateway):
        runtime = Runtime()
        return SimpleNamespace(
            slot=SimpleNamespace(scenario="happy_path"),
            execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha="5" * 40),
            trusted_context_digest="6" * 64,
            composition=SimpleNamespace(
                runtime=runtime, recovery_dispatch_gateway=gateway,
                actions_transport=gateway.transport, policy_authority=recovery_policy_fixture(),
                result_source=ResultSource(), recovery_result_source=ResultSource()))
    patches = (
        patch.object(subject, "_recovery_worker_blobs", return_value=historical_recovery_worker_blobs()),
        patch.object(subject, "observe_historical_worker_for_review",
                     return_value={"observation_digest": subject.RECOVERY_OBSERVATION_DIGEST}),
        patch.object(subject, "_observe_provider_rotation", return_value=fence),
        patch.object(subject, "_bounded_recovery_identity", return_value=({}, {})),
        patch.object(subject, "_observe_armed_recovery_no_http", return_value=proof),
        patch.dict(subject.os.environ, {"AI_SDLC_ACTIONS_READ_TOKEN": "test"}, clear=False))
    for p in patches: p.start()
    try:
        gateway = Gateway(); pf = preflight(gateway)
        sealed = subject.recover_historical_prehttp_attempt(pf)
        expect(gateway.fixture_http.posts == 1 and sealed["receipt_id"] == "40000000002",
               "bounded recovery did not make exactly one POST and seal")
        replay = subject.recover_historical_prehttp_attempt(pf)
        expect(replay == sealed and gateway.fixture_http.posts == 1, "bounded recovery replay repeated POST")
        from v03_dogfood_post_run_finalizer import (
            V03DogfoodPostRunFinalizerError, _validated_recovery_chain,
        )
        expect(sealed["source_head_sha"] == original_snapshot.get(subject.RECOVERY_AUTHORIZATION_PATH)["source_head_sha"]
               and sealed["execution_source_head_sha"] == "5" * 40,
               "sealed receipt conflated historical authorization with new execution source")
        from v03_dogfood_full_composition import recovery_execution_binding
        execution_binding = recovery_execution_binding(pf.composition.policy_authority)
        expect(_validated_recovery_chain(
            pf.composition.runtime.backend.read_snapshot(), execution_binding=execution_binding) == sealed,
               "finalizer did not accept the exact complete recovery chain")
        for corrupt in ("missing-authorization", "conflicting-receipt", "attempt-digest"):
            snap = deepcopy(pf.composition.runtime.backend.read_snapshot())
            if corrupt == "missing-authorization":
                del snap.files[subject.RECOVERY_AUTHORIZATION_PATH]
            elif corrupt == "conflicting-receipt":
                snap.files[subject.RECOVERY_RECEIPT_PATH] = {
                    **snap.files[subject.RECOVERY_RECEIPT_PATH], "source_head_sha": "0" * 40,
                }
            else:
                snap.files[subject.RECOVERY_ATTEMPT_PATH] = {
                    **snap.files[subject.RECOVERY_ATTEMPT_PATH], "candidate_head_sha": "0" * 40,
                }
            try: _validated_recovery_chain(snap, execution_binding=execution_binding)
            except V03DogfoodPostRunFinalizerError: pass
            else: raise AssertionError("finalizer accepted " + corrupt + " recovery chain")

        # Execute the real finalizer recovery branch, including task/source context
        # and fresh resolved run/output comparison, rather than a detached helper.
        import v03_dogfood_post_run_finalizer as finalizer_subject
        from operator_store_model import reservation_path
        h = subject.HISTORICAL_PREHTTP_RECOVERY
        final_snapshot = pf.composition.runtime.backend.snapshot
        final_snapshot.files[reservation_path(h["semantic_effect_key"])] = {
            "external_dispatch_key": h["external_dispatch_key"],
            "feature_id": h["feature_id"],
            "role": h["role"],
            "expected_revision": 1,
            "task_identity": h["task_identity"],
        }
        final_preflight = SimpleNamespace(
            execution=pf.execution,
            slot=SimpleNamespace(
                feature_id=h["feature_id"], target_ref=h["target_ref"],
            ),
            candidate_pr_number=h["candidate_pr_number"],
            composition=pf.composition,
        )
        final_events = [
            {
                "event_type": "dispatch.launch.authorized", "operation_generation": 1,
                "sequence": 11, "payload": {
                    "external_dispatch_key": h["external_dispatch_key"],
                    "semantic_effect_key": h["semantic_effect_key"],
                    "dispatch_id": h["dispatch_id"], "stage": h["stage"],
                    "role": h["role"], "candidate_head_sha": h["candidate_head_sha"],
                    "task_id": h["task_id"],
                },
            },
            {
                "event_type": "dispatch.launch.lookup-recorded", "operation_generation": 1,
                "sequence": 12, "payload": {
                    "external_dispatch_key": h["external_dispatch_key"],
                    "lookup_state": "LAUNCHED", "receipt_id": "37204777409",
                },
            },
        ]
        final_observation = {
            "operation_id": h["operation_id"], "scenario": "session_recovery",
        }

        # Execute the actual recovery collector through production run validation
        # and receipt materialization. Only the pre-existing projection is a
        # harness input; never replace _validate_run or _build_receipts.
        import v03_dogfood_full_composition as composition_subject
        bound_loader = composition_subject.DogfoodRecoveryBoundContentLoader(
            result_source=pf.composition.result_source,
            recovery_result_source=pf.composition.recovery_result_source,
            policy_authority=pf.composition.policy_authority)
        bound_loader.bind_runtime(pf.composition.runtime)
        expect(isinstance(bound_loader(sealed["safe_output_uri"]), bytes),
               "production recovery content routing rejected exact sealed output")
        before_count = pf.composition.runtime.n
        before_posts = gateway.fixture_http.posts
        wrong_uri = sealed["safe_output_uri"].replace("--head-" + "5" * 40, "--head-" + "4" * 40)
        try: bound_loader(wrong_uri)
        except VerticalInvariantError: pass
        else: raise AssertionError("production content routing accepted a different recovery URI")
        live_snapshot = pf.composition.runtime.backend.snapshot
        without_seal = deepcopy(live_snapshot)
        del without_seal.files[subject.RECOVERY_RECEIPT_PATH]
        pf.composition.runtime.backend.snapshot = without_seal
        try:
            try: bound_loader(sealed["safe_output_uri"])
            except VerticalInvariantError: pass
            else: raise AssertionError("production content routing accepted an unsealed recovery output")
        finally:
            pf.composition.runtime.backend.snapshot = live_snapshot
        expect(pf.composition.runtime.n == before_count and gateway.fixture_http.posts == before_posts,
               "invalid content routing caused Store or external mutation")
        callback_calls = []
        def callback(**kwargs):
            from operator_vertical import FeatureSnapshot, validate_collected_outputs
            context = kwargs["context"]
            feature = FeatureSnapshot(
                repository=context.target_repository, feature_id=context.feature_id,
                target_ref=context.target_ref, revision=context.expected_revision,
                manifest_digest="", current_stage=context.feature_stage,
                stages={}, gates={}, remediation_tasks=(), artifacts=(),
                candidate_pr_number=None, candidate_head_sha=context.candidate_head_sha)
            validated = validate_collected_outputs(
                context=context, feature=feature, worker_payload=kwargs["worker_payload"],
                receipts=kwargs["receipts"],
                content_loader=bound_loader)
            expect(len(validated) == 1, "real collected-output validation rejected recovery receipt")
            expect(composition_subject.DogfoodGitHubCandidateProvider._developer_receipt({
                "collected_outputs": kwargs["receipts"]}) == (901, "7" * 40),
                "real handoff parser rejected sealed first-attempt Developer output")
            callback_calls.append(kwargs)
            return {"status": "RECORDED"}
        coordinator = SimpleNamespace(
            executor=SimpleNamespace(
                runtime=pf.composition.runtime,
                config=SimpleNamespace(target_ref=h["target_ref"])),
            content_loader=bound_loader,
            handle=callback)
        collector = composition_subject.DogfoodRecoveryCollector(
            callback_coordinator=coordinator,
            result_source=pf.composition.recovery_result_source,
            workflows=pf.composition.recovery_dispatch_gateway.workflows,
            control_repository="dream-xin/ai-sdlc",
            clock=pf.composition.runtime.clock,
            policy_authority=pf.composition.policy_authority)
        projection = {
            "generation": h["generation"], "feature_id": h["feature_id"],
            "expected_feature_revision": 1, "target_repository": "dream-xin/ai-sdlc"}
        with patch.object(composition_subject, "_current_launch_binding", return_value=(
                projection, final_events[0]["payload"], "37204777409")):
            expect(collector.handle(
                operation_id=h["operation_id"], external_dispatch_key=h["external_dispatch_key"]
            ) == {"status": "RECORDED"}, "actual recovery collector did not reach closed callback")
        expect(len(callback_calls) == 1, "recovery collector replayed callback handoff")
        callback_context = callback_calls[0]["context"]
        expect(callback_context.dispatch_id == h["dispatch_id"]
               and callback_context.external_dispatch_key == h["external_dispatch_key"]
               and callback_context.worker_identity.endswith("@" + "5" * 40),
               "collector conflated original semantic authority and new Worker execution source")
        expect(len(callback_calls[0]["receipts"]) == 1
               and callback_calls[0]["receipts"][0]["dispatch_id"] == h["dispatch_id"],
               "collector receipts lost historical callback authority")
        happy_path_recovery_finalization_tests(pf, sealed, callback_calls[0])
        with patch.object(
            finalizer_subject, "vertical_projection",
            return_value={"operation_profile": subject.VERTICAL_PROFILE},
        ):
            bindings = finalizer_subject._durable_run_bindings(
                final_preflight, final_observation, final_events
            )
            expect(int(sealed["receipt_id"]) in bindings,
                   "finalizer recovery branch did not bind sealed successful run")
            pf.composition.recovery_result_source.mismatch = True
            try:
                finalizer_subject._durable_run_bindings(
                    final_preflight, final_observation, final_events
                )
            except V03DogfoodPostRunFinalizerError:
                pass
            else:
                raise AssertionError("finalizer accepted fresh run/output mismatch")
            pf.composition.recovery_result_source.mismatch = False

        # Execute finalize() deferred resolvers through the complete production
        # provenance verifier, with only external HTTP and milestone fixture
        # reconstruction supplied by the harness.
        final_preflight.slot.scenario = "session_recovery"
        final_preflight.candidate_head_sha = h["candidate_head_sha"]
        complete_observation = dict(
            final_observation, repository=pf.execution.repository,
            feature_id=h["feature_id"], target_ref=h["target_ref"],
            candidate_pr_number=h["candidate_pr_number"],
            candidate_head_sha=h["candidate_head_sha"],
            workflow_run_ids=[int(sealed["receipt_id"])],
            runtime_receipt_identity=sealed["receipt_id"])
        categories = {
            name: set(values) for name, _state, values in
            finalizer_subject.SCENARIO_PROFILES["session_recovery"]["milestones"]}
        def verify_finalizer_resolver(**kwargs):
            verifier = kwargs["trusted_facts"]["provenance_verifier"]
            def provenance_http(url, headers):
                if url.endswith("/pulls/" + str(h["candidate_pr_number"])):
                    return 200, {
                        "state": "open", "draft": False,
                        "head": {"sha": h["candidate_head_sha"],
                                 "repo": {"full_name": pf.execution.repository}},
                        "base": {"ref": "main", "repo": {"full_name": pf.execution.repository}}}
                if url.endswith("/actions/runs/" + sealed["receipt_id"]):
                    return 200, {
                        "id": int(sealed["receipt_id"]), "run_attempt": 1,
                        "repository": {"full_name": pf.execution.repository},
                        "event": "workflow_dispatch", "conclusion": "success", "status": "completed",
                        "head_branch": "main", "head_sha": "5" * 40,
                        "display_title": sealed["display_title"],
                        "path": ".github/workflows/" + subject.RECOVERY_WORKFLOW}
                raise AssertionError("provenance escaped exact HTTP inventory: " + url)
            verifier._http_get = provenance_http
            attestation = verifier.verify({
                "repository": pf.execution.repository, "feature_id": h["feature_id"],
                "target_ref": h["target_ref"],
                "candidate": {"pr_number": h["candidate_pr_number"], "head_sha": h["candidate_head_sha"]},
                "adapter": {"adapter_id": finalizer_subject.OPENAI_RESPONSES_ADAPTER_ID},
                "runtime": {"runtime_kind": finalizer_subject.RUNTIME_KIND,
                            "receipt_identity": sealed["receipt_id"],
                            "workflow_run_ids": [int(sealed["receipt_id"])]},
                "provenance": {"verifier_identity": finalizer_subject.VERIFIER_IDENTITY},
                "milestones": kwargs["trusted_facts"]["milestones"],
            })
            return {"verified_receipt": attestation.receipt_identity}
        with (
            patch.object(finalizer_subject, "_durable_operation_facts",
                         return_value=(final_events, {"generation": 1})),
            patch.object(finalizer_subject, "vertical_projection",
                         return_value={"operation_profile": subject.VERTICAL_PROFILE}),
            patch.object(finalizer_subject, "_reconstruct_release_authority",
                         return_value=(categories, {})),
            patch.object(finalizer_subject, "build_release_record",
                         side_effect=verify_finalizer_resolver),
        ):
            expect(finalizer_subject.finalize(
                observation=complete_observation, preflight=final_preflight,
                source_run_id=40000000004, finalizer_run_id=40000000005,
                github_token="unused-read-only-fixture-token"
            ) == {"verified_receipt": sealed["receipt_id"]},
                   "finalize deferred resolver lost its preflight/Store dependency")

        # Every corrupt chain must fail before the actual live pre-host
        # boundary performs even an empty Store commit.
        from v03_dogfood_full_composition import RECOVERY_CONTINUATION_PATH
        clean_snapshot = deepcopy(pf.composition.runtime.backend.snapshot)
        for corrupt in ("receipt-source", "missing-authorization", "attempt-digest",
                        "missing-continuation", "execution-source"):
            broken_snapshot = deepcopy(clean_snapshot)
            if corrupt == "receipt-source":
                broken_snapshot.files[subject.RECOVERY_RECEIPT_PATH]["source_head_sha"] = "0" * 40
            elif corrupt == "missing-authorization":
                del broken_snapshot.files[subject.RECOVERY_AUTHORIZATION_PATH]
            elif corrupt == "attempt-digest":
                broken_snapshot.files[subject.RECOVERY_ATTEMPT_PATH]["authorization_digest"] = "bad"
            elif corrupt == "missing-continuation":
                del broken_snapshot.files[RECOVERY_CONTINUATION_PATH]
            else:
                broken_snapshot.files[RECOVERY_CONTINUATION_PATH]["execution_source_head_sha"] = "4" * 40
            pf.composition.runtime.backend.snapshot = broken_snapshot
            before_post, before_store = gateway.fixture_http.posts, pf.composition.runtime.n
            with (
                patch.object(subject, "assemble_preflight", return_value=pf),
                patch.object(subject, "_head", return_value="5" * 40),
                patch.object(subject, "V03DogfoodOpenAIResponsesHost") as host_constructor,
            ):
                try:
                    subject._execute_live(mode=subject.RUN, scenario="happy_path")
                except (subject.V03DogfoodRuntimeDriverError, VerticalInvariantError):
                    pass
                else:
                    raise AssertionError(corrupt + " crossed the live pre-host boundary")
                expect(not host_constructor.called, corrupt + " constructed a model host")
            expect(gateway.fixture_http.posts == before_post
                   and pf.composition.runtime.n == before_store,
                   corrupt + " caused a POST or Store commit")
        pf.composition.runtime.backend.snapshot = clean_snapshot

        lost = Gateway(ack_loss=True); pf_lost = preflight(lost)
        sealed_lost = subject.recover_historical_prehttp_attempt(pf_lost)
        expect(lost.fixture_http.posts == 1 and sealed_lost["receipt_id"] == "40000000002",
               "ack-loss lookup did not seal without a second POST")
        # Fresh main and real configuration/payload validation must fail before
        # arming a continuation. Drift after the claim still forbids POST.
        from dataclasses import replace
        for drift in ("main-before-claim", "main-before-post", "transport-config"):
            denied_gateway = Gateway()
            denied_pf = preflight(denied_gateway)
            if drift == "main-before-claim":
                denied_gateway.fixture_http.main_sources = ["4" * 40]
            elif drift == "main-before-post":
                denied_gateway.fixture_http.main_sources = ["5" * 40, "4" * 40]
            else:
                denied_gateway.transport.config = replace(
                    denied_gateway.transport.config, api_url="https://other.invalid")
            try:
                subject.recover_historical_prehttp_attempt(denied_pf)
            except (subject.V03DogfoodRuntimeDriverError, VerticalInvariantError):
                pass
            else:
                raise AssertionError(drift + " acquired a recovery POST")
            expect(denied_gateway.fixture_http.posts == 0,
                   drift + " caused a recovery POST")
            expect(denied_pf.composition.runtime.n == (1 if drift == "main-before-post" else 0),
                   drift + " crossed an unauthorized Store claim boundary")
        loser = Gateway(); pf_loser = preflight(loser)
        first_plan = subject._plan_bounded_recovery(
            pf_loser.composition.runtime.backend.read_snapshot(), preflight=pf_loser, fence=fence, proof=proof)
        pf_loser.composition.runtime.backend.snapshot = apply_plan_to_snapshot(
            pf_loser.composition.runtime.backend.snapshot, first_plan, new_ref_sha="race-winner")
        try: subject.recover_historical_prehttp_attempt(pf_loser)
        except subject.V03DogfoodRuntimeDriverError: pass
        else: raise AssertionError("CAS loser without receipt was allowed to POST")
        expect(loser.fixture_http.posts == 0, "CAS loser/unknown receipt performed a POST")
        broken = deepcopy(fence); broken["historical_status"] = 200
        try: subject._validate_provider_rotation_fence(broken)
        except subject.V03DogfoodRuntimeDriverError: pass
        else: raise AssertionError("old-key-still-valid provider fence was accepted")
    finally:
        for p in reversed(patches): p.stop()
    print("- bounded recovery dynamically proves CAS winner/replay/ack-loss and zero-POST loser fencing")

def main():
    for scenario in ("happy_path", "review_remediation", "session_recovery"):
        expect(
            require_mode(mode=VALIDATE_ONLY, scenario=scenario, event_name="pull_request", ref="refs/pull/348/merge")
            == (VALIDATE_ONLY, scenario),
            "PR validate-only mode",
        )
        expect(
            require_mode(mode=PREFLIGHT_ONLY, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/main")
            == (PREFLIGHT_ONLY, scenario),
            "trusted-main preflight mode",
        )
        expect(
            require_mode(mode=RUN, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/main")
            == (RUN, scenario),
            "trusted-main run mode",
        )
        rejected(mode=RUN, scenario=scenario, event_name="pull_request", ref="refs/heads/main")
        rejected(mode=RUN, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/release/v03-dogfood-real-run-342")
        rejected(mode=VALIDATE_ONLY, scenario=scenario, event_name="workflow_dispatch", ref="refs/heads/main")
    rejected(mode=RUN, scenario="unknown", event_name="workflow_dispatch", ref="refs/heads/main")
    rejected(mode="unsafe", scenario="happy_path", event_name="pull_request", ref="refs/pull/348/merge")

    from pathlib import Path
    import traceback
    validation_root = Path(__file__).resolve().parents[1]
    def replacement_pipeline():
        preflight, http, _state, _frozen = fixed_replacement_fixture()
        sealed = driver_subject.recover_approved_replacement(preflight)
        expect(http.posts == 1, "fresh replacement pipeline must launch exactly once")
        fixed_replacement_route_lock_tests(preflight, sealed)
        fixed_replacement_collector_pipeline_tests(preflight, sealed, expected_human_interventions=4)
    failures = []
    # Independent diagnostics continue, but no failing group can become a pass.
    # In particular, a CAS negative failure cannot hide the fresh full pipeline.
    groups = (
        ("installation transition", installation_transition_tests),
        ("pre-HTTP recovery fence", prehttp_recovery_fence_tests),
        ("provider revocation", pinned_provider_revocation_tests),
        ("historical adoption", historical_worker_adoption_tests),
        ("historical Worker evidence", historical_worker_evidence_tests),
        ("strict lock transform", lambda: recovery_lock_transform_tests(validation_root)),
        ("Worker preparation guards", lambda: recovery_worker_preparation_contract_tests(validation_root)),
        ("Safe Output source", recovery_safe_output_source_tests),
        ("immutable source proof", armed_recovery_source_proof_tests),
        ("archival continuation CAS", recovery_continuation_cas_tests),
        ("archival bounded full pipeline", bounded_recovery_execution_tests),
        ("fixed replacement admission and negatives", fixed_replacement_admission_tests),
        ("fixed replacement full pipeline", replacement_pipeline),
    )
    for name, execute in groups:
        try:
            execute()
        except Exception:
            failures.append(name)
            print("FAILED regression group: " + name, flush=True)
            traceback.print_exc()
    if failures:
        raise AssertionError("v0.3 regression groups failed: " + ", ".join(failures))

    provider = dogfood_responses_host_config({"AI_SDLC_DEEPSEEK_API_KEY": "configured-test-key"})
    expect(provider.api_base == DOGFOOD_RESPONSES_API_BASE == "https://api.deepseek.com",
           "dogfood Responses provider endpoint drifted")
    expect(provider.model == DOGFOOD_RESPONSES_MODEL == "deepseek-flash",
           "dogfood Responses model is not fixed before effects")
    expect(provider.continuation_mode == "full_history",
           "DeepSeek Responses transport must remain stateless/full-history")
    try:
        dogfood_responses_host_config({})
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("dogfood Responses transport accepted missing DeepSeek credential")

    from pathlib import Path
    import yaml
    from gh_aw_provider_registry import load_registry
    from v03_dogfood_execution_bindings import credential_identities
    root = Path(__file__).resolve().parents[1]
    live_path = root / ".github/workflows/v03-real-dogfood-scenario.yml"
    live_text = live_path.read_text()
    live = yaml.safe_load(live_text)
    readiness_text = (root / ".github/workflows/v03-dogfood-readiness.yml").read_text()
    recovery_source = (root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.md").read_text()
    recovery_lock = (root / ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml").read_text()
    qa_source = (root / ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.md").read_text()
    qa_lock = (root / ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.lock.yml").read_text()
    for source, lock in ((recovery_source, recovery_lock), (qa_source, qa_lock)):
        expect("GH_AW_CI_TRIGGER_TOKEN" not in source and "\n  conclusion:\n" not in source,
               "recovery Worker source retained a generic collector dispatch")
        expect(lock.startswith('# gh-aw-metadata: {"schema_version":"v4"')
               and '"compiler_version":"v0.89.21"' in lock and '"strict":true' in lock,
               "recovery Worker lock lacks strict pinned compiler provenance")
        expect("persist-credentials: true" not in lock,
               "recovery Worker lock persisted checkout credentials")
    expect("secrets.DEEPSEEK_API_KEY" in live_text and "AI_SDLC_DEEPSEEK_API_KEY" in live_text,
           "real dogfood workflow lacks DeepSeek Responses credential binding")
    expect("AI_SDLC_OPENAI_API_KEY" not in live_text and "AI_SDLC_OPENAI_MODEL" not in live_text,
           "real dogfood workflow still requires unconfigured OpenAI client transport")
    expect("HAS_AI_SDLC_DEEPSEEK_API_KEY" in readiness_text
           and '"DEEPSEEK_API_KEY": "HAS_AI_SDLC_DEEPSEEK_API_KEY"' in readiness_text,
           "readiness does not test the actual DeepSeek client transport credential")
    git_store_transport_tests(live)
    finalizer_text = (root / ".github/workflows/v03-finalize-real-dogfood-scenario.yml").read_text()
    finalizer = yaml.safe_load(finalizer_text)
    for workflow in (live, finalizer):
        for identity in credential_identities(load_registry()):
            expect("HAS_" + identity in workflow["env"], "missing production binding presence signal")
    expect(live["permissions"]["actions"] == "write", "real Worker dispatch lacks authority")
    expect("workflow_run:" not in finalizer_text, "finalizer violates repository trigger boundary")
    expect(finalizer["permissions"]["actions"] == "read", "finalizer must remain read-only")
    expect(any("gh workflow run v03-finalize-real-dogfood-scenario.yml" in step.get("run", "")
               for step in live["jobs"]["dogfood"]["steps"]), "successful dogfood does not queue closed finalizer")
    expect(any("30" in step.get("run", "") and ".status" in step.get("run", "")
               for step in finalizer["jobs"]["finalize"]["steps"]), "finalizer lacks bounded source completion wait")
    print("v0.3 dogfood runtime driver validation passed")
    print("- PR validation cannot enter live preflight/run")
    print("- live preflight/run require workflow_dispatch on refs/heads/main")
    print("- only the three frozen dogfood scenarios are selectable")


if __name__ == "__main__":
    main()
