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
            if field == "safe_output_uri":
                from operator_store_model import digest_json
                snapshot.files[composition.REPLACEMENT_RECEIPT_PATH]["safe_output_digest"] = (
                    "sha256:" + digest_json({"trusted_uri": value}))
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
            slot=SimpleNamespace(scenario="happy_path"), workflows=gateway.workflows,
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


POST_HANDOFF_ARCHIVE_B64 = "UEsDBBQACAAIADdFSV0AAAAAAAAAAAAAAAAXAAAAc2FmZS1vdXRwdXQtaXRlbXMuanNvbmx1jk1PwkAURff+jLue0g5fpbOrgSgL0CAmxk0zdJ4wsUPH6asVCf/dNMbEhe7PPfecwSdPUCgDaabCt1VVBHprqWEItKGCwoHZNyqO95YP7W5Q1i6ebxb5KnparmNto8ZUZdwv40maQuDYuh0FqEmaCgTyNRRMIO2iD3v8GUDAh/rdmp7EtxoC1kCN02wmR6NEZgKsw54Y6vwn3csby3U4/XPxK+UiwOR8HXQ4LQ0UdFekeyof1vIRAo5YG826vzrWhoq+BPeb4rWb32270WK7muV5nl/fvgw/bzr0OuuoYe08FIbJcBrJJEqybTJTY6nG04HM5DMuV19QSwcIZYYrbPkAAABkAQAAUEsDBBQACAAIADdFSV0AAAAAAAAAAAAAAAAVAAAAdGVtcG9yYXJ5LWlkLW1hcC5qc29uq+ZSUFBKLI83T09NLs4zLFWyUqjmUlBQUFAqSi3IV7JSUEopSk3M1a3IzNNPzNQtTslJVtKBKMgrzU1KLVKyUjA1N+dSUKjlquUCAFBLBwjwrs91TAAAAE4AAABQSwECLQMUAAgACAA3RUldZYYrbPkAAABkAQAAFwAAAAAAAAAAACAApIEAAAAAc2FmZS1vdXRwdXQtaXRlbXMuanNvbmxQSwECLQMUAAgACAA3RUld8K7PdUwAAABOAAAAFQAAAAAAAAAAACAApIE+AQAAdGVtcG9yYXJ5LWlkLW1hcC5qc29uUEsFBgAAAAACAAIAiAAAAM0BAAAAAA=="

def post_handoff_frozen_provider_fixture(archive_bytes):
    """Exact stopped Store and observed provider snapshots; no live calls in test."""
    import hashlib
    import json
    import subprocess
    import base64
    from copy import deepcopy
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import parse_qs, unquote, urlparse
    from operator_store_model import StoreSnapshot
    import v03_dogfood_full_composition as composition

    expect(isinstance(archive_bytes, bytes) and len(archive_bytes) == 619
           and hashlib.sha256(archive_bytes).hexdigest()
               == "fb19af7d2aa1cf42e58feff958ba34b5041d394a81ec9fed9c4eeee5d4d729bf",
           "post-handoff test requires exact original run-owned archive bytes")
    root = Path(__file__).resolve().parents[1]
    commit = "d3ccde10fb3f30e29d27e51bcc82fb9ce49c93f9"
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "state/operator/v1"],
        cwd=root, check=True, capture_output=True, text=True).stdout.splitlines()
    files = {path: json.loads(subprocess.run(
        ["git", "show", commit + ":" + path], cwd=root, check=True, capture_output=True).stdout)
        for path in listed if path.endswith(".json")}
    snapshot = StoreSnapshot(commit, files)
    composition.validate_post_handoff_predecessor(snapshot)
    observed = json.loads("{\"run\":{\"id\":37905505035,\"run_attempt\":1,\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37905505035\",\"path\":\".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml\",\"display_title\":\"AI-SDLC gh-aw dispatch-86e969e947932b9dd38c608ca80333714c3396d2\",\"event\":\"workflow_dispatch\",\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\",\"status\":\"completed\",\"conclusion\":\"success\",\"workflow_id\":378100093,\"created_at\":\"2026-10-09T08:31:44Z\",\"updated_at\":\"2026-10-09T08:42:11Z\",\"repository\":{\"full_name\":\"DREAM-XIN/ai-sdlc\"}},\"jobs\":{\"total_count\":5,\"jobs\":[{\"id\":113737877171,\"name\":\"activation\",\"status\":\"completed\",\"conclusion\":\"success\",\"run_id\":37905505035,\"run_attempt\":1,\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T08:31:52Z\",\"completed_at\":\"2026-10-09T08:31:55Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T08:31:55Z\",\"completed_at\":\"2026-10-09T08:31:58Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T08:31:58Z\",\"completed_at\":\"2026-10-09T08:31:58Z\"},{\"name\":\"Generate agentic run info\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T08:31:58Z\",\"completed_at\":\"2026-10-09T08:31:58Z\"},{\"name\":\"Restore daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T08:31:58Z\",\"completed_at\":\"2026-10-09T08:31:58Z\"},{\"name\":\"Check daily workflow token guardrail\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T08:31:58Z\",\"completed_at\":\"2026-10-09T08:31:59Z\"},{\"name\":\"Publish daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T08:31:59Z\",\"completed_at\":\"2026-10-09T08:31:59Z\"},{\"name\":\"Check for OAuth tokens\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T08:31:59Z\",\"completed_at\":\"2026-10-09T08:31:59Z\"},{\"name\":\"Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T08:31:59Z\",\"completed_at\":\"2026-10-09T08:32:00Z\"},{\"name\":\"Save agent config folders for base branch restoration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T08:32:00Z\",\"completed_at\":\"2026-10-09T08:32:00Z\"},{\"name\":\"Check workflow lock file\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T08:32:00Z\",\"completed_at\":\"2026-10-09T08:32:01Z\"},{\"name\":\"Check compile-agentic version\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T08:32:01Z\",\"completed_at\":\"2026-10-09T08:32:01Z\"},{\"name\":\"Log runtime features\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T08:32:01Z\",\"completed_at\":\"2026-10-09T08:32:01Z\"},{\"name\":\"Create prompt with built-in context\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T08:32:01Z\",\"completed_at\":\"2026-10-09T08:32:02Z\"},{\"name\":\"Interpolate variables and render templates\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T08:32:02Z\",\"completed_at\":\"2026-10-09T08:32:02Z\"},{\"name\":\"Substitute placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T08:32:02Z\",\"completed_at\":\"2026-10-09T08:32:02Z\"},{\"name\":\"Validate prompt placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T08:32:02Z\",\"completed_at\":\"2026-10-09T08:32:02Z\"},{\"name\":\"Print prompt\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T08:32:02Z\",\"completed_at\":\"2026-10-09T08:32:02Z\"},{\"name\":\"Upload info artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T08:32:02Z\",\"completed_at\":\"2026-10-09T08:32:04Z\"},{\"name\":\"Stage prompt files for artifact upload\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T08:32:04Z\",\"completed_at\":\"2026-10-09T08:32:04Z\"},{\"name\":\"Upload activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-09T08:32:04Z\",\"completed_at\":\"2026-10-09T08:32:05Z\"},{\"name\":\"Post Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-09T08:32:05Z\",\"completed_at\":\"2026-10-09T08:32:06Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-09T08:32:06Z\",\"completed_at\":\"2026-10-09T08:32:06Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-09T08:32:06Z\",\"completed_at\":\"2026-10-09T08:32:06Z\"}]},{\"id\":113738004745,\"name\":\"agent\",\"status\":\"completed\",\"conclusion\":\"success\",\"run_id\":37905505035,\"run_attempt\":1,\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T08:32:11Z\",\"completed_at\":\"2026-10-09T08:32:14Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T08:32:14Z\",\"completed_at\":\"2026-10-09T08:32:16Z\"},{\"name\":\"Reject rerun before model execution\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T08:32:16Z\",\"completed_at\":\"2026-10-09T08:32:16Z\"},{\"name\":\"Validate release-only local Worker identity\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T08:32:16Z\",\"completed_at\":\"2026-10-09T08:32:16Z\"},{\"name\":\"Set runtime paths\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T08:32:16Z\",\"completed_at\":\"2026-10-09T08:32:16Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T08:32:16Z\",\"completed_at\":\"2026-10-09T08:32:16Z\"},{\"name\":\"Check OTLP telemetry configuration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T08:32:16Z\",\"completed_at\":\"2026-10-09T08:32:16Z\"},{\"name\":\"Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T08:32:16Z\",\"completed_at\":\"2026-10-09T08:32:17Z\"},{\"name\":\"Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T08:32:17Z\",\"completed_at\":\"2026-10-09T08:32:20Z\"},{\"name\":\"Fetch additional refs for dream-xin/ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T08:32:20Z\",\"completed_at\":\"2026-10-09T08:32:20Z\"},{\"name\":\"Build checkout manifest for safe-outputs handlers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T08:32:20Z\",\"completed_at\":\"2026-10-09T08:32:21Z\"},{\"name\":\"Initialize agent execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T08:32:21Z\",\"completed_at\":\"2026-10-09T08:32:21Z\"},{\"name\":\"Create gh-aw temp directory\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-09T08:32:21Z\",\"completed_at\":\"2026-10-09T08:32:21Z\"},{\"name\":\"Configure gh CLI for GitHub Enterprise\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T08:32:21Z\",\"completed_at\":\"2026-10-09T08:32:21Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T08:32:21Z\",\"completed_at\":\"2026-10-09T08:32:23Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T08:32:23Z\",\"completed_at\":\"2026-10-09T08:32:23Z\"},{\"name\":\"Checkout PR branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":17,\"started_at\":\"2026-10-09T08:32:23Z\",\"completed_at\":\"2026-10-09T08:32:23Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T08:32:23Z\",\"completed_at\":\"2026-10-09T08:32:34Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T08:32:34Z\",\"completed_at\":\"2026-10-09T08:32:35Z\"},{\"name\":\"Determine automatic lockdown mode for GitHub MCP Server\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T08:32:35Z\",\"completed_at\":\"2026-10-09T08:32:35Z\"},{\"name\":\"Parse integrity filter lists\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-09T08:32:35Z\",\"completed_at\":\"2026-10-09T08:32:35Z\"},{\"name\":\"Restore agent config folders from base branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":22,\"started_at\":\"2026-10-09T08:32:35Z\",\"completed_at\":\"2026-10-09T08:32:35Z\"},{\"name\":\"Restore inline sub-agents from activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-09T08:32:35Z\",\"completed_at\":\"2026-10-09T08:32:35Z\"},{\"name\":\"Restore inline skills from activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-09T08:32:35Z\",\"completed_at\":\"2026-10-09T08:32:35Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":25,\"started_at\":\"2026-10-09T08:32:35Z\",\"completed_at\":\"2026-10-09T08:32:51Z\"},{\"name\":\"Prepare Safe Outputs Directories\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":26,\"started_at\":\"2026-10-09T08:32:51Z\",\"completed_at\":\"2026-10-09T08:32:51Z\"},{\"name\":\"Generate Safe Outputs Config\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":27,\"started_at\":\"2026-10-09T08:32:51Z\",\"completed_at\":\"2026-10-09T08:32:51Z\"},{\"name\":\"Generate Safe Outputs Tools\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":28,\"started_at\":\"2026-10-09T08:32:51Z\",\"completed_at\":\"2026-10-09T08:32:51Z\"},{\"name\":\"Start MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":29,\"started_at\":\"2026-10-09T08:32:51Z\",\"completed_at\":\"2026-10-09T08:32:57Z\"},{\"name\":\"Mount MCP servers as CLIs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":30,\"started_at\":\"2026-10-09T08:32:57Z\",\"completed_at\":\"2026-10-09T08:32:57Z\"},{\"name\":\"Clean credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":31,\"started_at\":\"2026-10-09T08:32:57Z\",\"completed_at\":\"2026-10-09T08:32:57Z\"},{\"name\":\"Audit pre-agent workspace\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":32,\"started_at\":\"2026-10-09T08:32:57Z\",\"completed_at\":\"2026-10-09T08:32:57Z\"},{\"name\":\"Execute GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":33,\"started_at\":\"2026-10-09T08:32:57Z\",\"completed_at\":\"2026-10-09T08:38:41Z\"},{\"name\":\"Detect agent errors\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":34,\"started_at\":\"2026-10-09T08:38:41Z\",\"completed_at\":\"2026-10-09T08:38:41Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":35,\"started_at\":\"2026-10-09T08:38:41Z\",\"completed_at\":\"2026-10-09T08:38:42Z\"},{\"name\":\"Copy Copilot session state files to logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":36,\"started_at\":\"2026-10-09T08:38:42Z\",\"completed_at\":\"2026-10-09T08:38:42Z\"},{\"name\":\"Stop MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":37,\"started_at\":\"2026-10-09T08:38:42Z\",\"completed_at\":\"2026-10-09T08:38:43Z\"},{\"name\":\"Redact secrets in logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":38,\"started_at\":\"2026-10-09T08:38:43Z\",\"completed_at\":\"2026-10-09T08:38:43Z\"},{\"name\":\"Append agent step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":39,\"started_at\":\"2026-10-09T08:38:43Z\",\"completed_at\":\"2026-10-09T08:38:43Z\"},{\"name\":\"Copy Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":40,\"started_at\":\"2026-10-09T08:38:43Z\",\"completed_at\":\"2026-10-09T08:38:43Z\"},{\"name\":\"Ingest agent output\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-09T08:38:43Z\",\"completed_at\":\"2026-10-09T08:38:44Z\"},{\"name\":\"Parse agent logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-09T08:38:44Z\",\"completed_at\":\"2026-10-09T08:38:44Z\"},{\"name\":\"Parse MCP Gateway logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-09T08:38:44Z\",\"completed_at\":\"2026-10-09T08:38:45Z\"},{\"name\":\"Print firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":44,\"started_at\":\"2026-10-09T08:38:45Z\",\"completed_at\":\"2026-10-09T08:38:45Z\"},{\"name\":\"Parse token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":45,\"started_at\":\"2026-10-09T08:38:45Z\",\"completed_at\":\"2026-10-09T08:38:46Z\"},{\"name\":\"Print AWF reflect summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":46,\"started_at\":\"2026-10-09T08:38:46Z\",\"completed_at\":\"2026-10-09T08:38:46Z\"},{\"name\":\"Generate observability summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":47,\"started_at\":\"2026-10-09T08:38:46Z\",\"completed_at\":\"2026-10-09T08:38:46Z\"},{\"name\":\"Write agent output placeholder if missing\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":48,\"started_at\":\"2026-10-09T08:38:46Z\",\"completed_at\":\"2026-10-09T08:38:46Z\"},{\"name\":\"Upload agent output fallback artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":49,\"started_at\":\"2026-10-09T08:38:46Z\",\"completed_at\":\"2026-10-09T08:38:47Z\"},{\"name\":\"Upload agent artifacts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":50,\"started_at\":\"2026-10-09T08:38:47Z\",\"completed_at\":\"2026-10-09T08:38:49Z\"},{\"name\":\"Post Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":98,\"started_at\":\"2026-10-09T08:38:49Z\",\"completed_at\":\"2026-10-09T08:38:50Z\"},{\"name\":\"Post Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":99,\"started_at\":\"2026-10-09T08:38:50Z\",\"completed_at\":\"2026-10-09T08:38:50Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":100,\"started_at\":\"2026-10-09T08:38:50Z\",\"completed_at\":\"2026-10-09T08:38:50Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":101,\"started_at\":\"2026-10-09T08:38:50Z\",\"completed_at\":\"2026-10-09T08:38:50Z\"}]},{\"id\":113740292465,\"name\":\"detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"run_id\":37905505035,\"run_attempt\":1,\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T08:38:56Z\",\"completed_at\":\"2026-10-09T08:38:59Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T08:38:59Z\",\"completed_at\":\"2026-10-09T08:39:01Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T08:39:01Z\",\"completed_at\":\"2026-10-09T08:39:02Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T08:39:02Z\",\"completed_at\":\"2026-10-09T08:39:03Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T08:39:03Z\",\"completed_at\":\"2026-10-09T08:39:03Z\"},{\"name\":\"Checkout repository for patch context\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T08:39:03Z\",\"completed_at\":\"2026-10-09T08:39:04Z\"},{\"name\":\"Initialize detection execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T08:39:04Z\",\"completed_at\":\"2026-10-09T08:39:04Z\"},{\"name\":\"Clear inherited Copilot session state\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T08:39:04Z\",\"completed_at\":\"2026-10-09T08:39:04Z\"},{\"name\":\"Clean stale firewall files from agent artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T08:39:04Z\",\"completed_at\":\"2026-10-09T08:39:04Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T08:39:04Z\",\"completed_at\":\"2026-10-09T08:39:19Z\"},{\"name\":\"Check if detection needed\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T08:39:19Z\",\"completed_at\":\"2026-10-09T08:39:19Z\"},{\"name\":\"Clear MCP Config for detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T08:39:19Z\",\"completed_at\":\"2026-10-09T08:39:19Z\"},{\"name\":\"Prepare threat detection files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-09T08:39:19Z\",\"completed_at\":\"2026-10-09T08:39:19Z\"},{\"name\":\"Setup threat detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T08:39:19Z\",\"completed_at\":\"2026-10-09T08:39:19Z\"},{\"name\":\"Ensure threat-detection directory and log\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T08:39:19Z\",\"completed_at\":\"2026-10-09T08:39:19Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T08:39:19Z\",\"completed_at\":\"2026-10-09T08:39:20Z\"},{\"name\":\"Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T08:39:20Z\",\"completed_at\":\"2026-10-09T08:39:21Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T08:39:21Z\",\"completed_at\":\"2026-10-09T08:39:37Z\"},{\"name\":\"Install threat-detect binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T08:39:37Z\",\"completed_at\":\"2026-10-09T08:39:37Z\"},{\"name\":\"Execute threat detection with AWF\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T08:39:37Z\",\"completed_at\":\"2026-10-09T08:40:24Z\"},{\"name\":\"Render detection log\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-09T08:40:24Z\",\"completed_at\":\"2026-10-09T08:40:24Z\"},{\"name\":\"Copy detection firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":22,\"started_at\":\"2026-10-09T08:40:24Z\",\"completed_at\":\"2026-10-09T08:40:24Z\"},{\"name\":\"Parse threat detection token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-09T08:40:24Z\",\"completed_at\":\"2026-10-09T08:40:24Z\"},{\"name\":\"Upload threat detection artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-09T08:40:24Z\",\"completed_at\":\"2026-10-09T08:40:25Z\"},{\"name\":\"Conclude threat detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":25,\"started_at\":\"2026-10-09T08:40:25Z\",\"completed_at\":\"2026-10-09T08:40:25Z\"},{\"name\":\"Post Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":48,\"started_at\":\"2026-10-09T08:40:25Z\",\"completed_at\":\"2026-10-09T08:40:25Z\"},{\"name\":\"Post Checkout repository for patch context\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":49,\"started_at\":\"2026-10-09T08:40:25Z\",\"completed_at\":\"2026-10-09T08:40:25Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":50,\"started_at\":\"2026-10-09T08:40:25Z\",\"completed_at\":\"2026-10-09T08:40:25Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":51,\"started_at\":\"2026-10-09T08:40:25Z\",\"completed_at\":\"2026-10-09T08:40:25Z\"}]},{\"id\":113740840203,\"name\":\"safe_outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"run_id\":37905505035,\"run_attempt\":1,\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T08:40:50Z\",\"completed_at\":\"2026-10-09T08:40:59Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T08:40:59Z\",\"completed_at\":\"2026-10-09T08:41:13Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T08:41:13Z\",\"completed_at\":\"2026-10-09T08:41:13Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T08:41:13Z\",\"completed_at\":\"2026-10-09T08:41:16Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T08:41:16Z\",\"completed_at\":\"2026-10-09T08:41:16Z\"},{\"name\":\"Download patch artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T08:41:16Z\",\"completed_at\":\"2026-10-09T08:41:18Z\"},{\"name\":\"Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T08:41:18Z\",\"completed_at\":\"2026-10-09T08:41:30Z\"},{\"name\":\"Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T08:41:30Z\",\"completed_at\":\"2026-10-09T08:41:39Z\"},{\"name\":\"Fetch additional refs for dream-xin/ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T08:41:39Z\",\"completed_at\":\"2026-10-09T08:41:40Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T08:41:40Z\",\"completed_at\":\"2026-10-09T08:41:40Z\"},{\"name\":\"Configure GH_HOST for enterprise compatibility\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T08:41:40Z\",\"completed_at\":\"2026-10-09T08:41:40Z\"},{\"name\":\"Require first attempt and affirmative detection before Safe Outputs effects\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T08:41:40Z\",\"completed_at\":\"2026-10-09T08:41:40Z\"},{\"name\":\"Process Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-09T08:41:40Z\",\"completed_at\":\"2026-10-09T08:41:46Z\"},{\"name\":\"Upload Safe Outputs Items\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T08:41:46Z\",\"completed_at\":\"2026-10-09T08:41:47Z\"},{\"name\":\"Post Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":26,\"started_at\":\"2026-10-09T08:41:47Z\",\"completed_at\":\"2026-10-09T08:41:48Z\"},{\"name\":\"Post Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":27,\"started_at\":\"2026-10-09T08:41:48Z\",\"completed_at\":\"2026-10-09T08:41:48Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":28,\"started_at\":\"2026-10-09T08:41:48Z\",\"completed_at\":\"2026-10-09T08:41:49Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":29,\"started_at\":\"2026-10-09T08:41:49Z\",\"completed_at\":\"2026-10-09T08:41:49Z\"}]},{\"id\":113741301089,\"name\":\"conclusion\",\"status\":\"completed\",\"conclusion\":\"success\",\"run_id\":37905505035,\"run_attempt\":1,\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T08:41:56Z\",\"completed_at\":\"2026-10-09T08:41:59Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T08:41:59Z\",\"completed_at\":\"2026-10-09T08:42:01Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T08:42:01Z\",\"completed_at\":\"2026-10-09T08:42:03Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T08:42:03Z\",\"completed_at\":\"2026-10-09T08:42:03Z\"},{\"name\":\"Download detection artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T08:42:03Z\",\"completed_at\":\"2026-10-09T08:42:04Z\"},{\"name\":\"Download Safe Outputs Items Manifest\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T08:42:04Z\",\"completed_at\":\"2026-10-09T08:42:05Z\"},{\"name\":\"Collect usage artifact files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T08:42:05Z\",\"completed_at\":\"2026-10-09T08:42:05Z\"},{\"name\":\"Upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T08:42:05Z\",\"completed_at\":\"2026-10-09T08:42:06Z\"},{\"name\":\"Wait before retrying usage artifact upload\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":9,\"started_at\":\"2026-10-09T08:42:06Z\",\"completed_at\":\"2026-10-09T08:42:06Z\"},{\"name\":\"Retry upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":10,\"started_at\":\"2026-10-09T08:42:06Z\",\"completed_at\":\"2026-10-09T08:42:06Z\"},{\"name\":\"Process no-op messages\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T08:42:06Z\",\"completed_at\":\"2026-10-09T08:42:06Z\"},{\"name\":\"Log detection run\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T08:42:06Z\",\"completed_at\":\"2026-10-09T08:42:06Z\"},{\"name\":\"Record missing tool\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-09T08:42:06Z\",\"completed_at\":\"2026-10-09T08:42:07Z\"},{\"name\":\"Record incomplete\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T08:42:07Z\",\"completed_at\":\"2026-10-09T08:42:07Z\"},{\"name\":\"Handle agent failure\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T08:42:07Z\",\"completed_at\":\"2026-10-09T08:42:07Z\"},{\"name\":\"Report failed jobs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T08:42:07Z\",\"completed_at\":\"2026-10-09T08:42:08Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":32,\"started_at\":\"2026-10-09T08:42:08Z\",\"completed_at\":\"2026-10-09T08:42:08Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":33,\"started_at\":\"2026-10-09T08:42:08Z\",\"completed_at\":\"2026-10-09T08:42:08Z\"}]}]},\"pr\":{\"number\":577,\"id\":4798133019,\"node_id\":\"PR_kwDOTw3ETM8AAAABHf2zGw\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/pull/577\",\"state\":\"closed\",\"draft\":true,\"title\":\"[ai-sdlc gh-aw] docs(v0.3): add dogfood happy-path implementation candidate\",\"body\":\"## Summary\\n\\nAdds the minimal documentation-only implementation candidate required by the assigned Feature\\n`F-OPERATOR-V03-DOGFOOD-HAPPY-0001` (v0.3 release dogfood, `happy_path` scenario).\\n\\nThe single new file `docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md` contains the\\nexact contract marker `dogfood_result: happy-path` and no unrelated changes.\\n\\n## Related feature / task\\n\\n- Feature: `F-OPERATOR-V03-DOGFOOD-HAPPY-0001`\\n- Task: `vertical:implementation:1` (stage `implementation`, role `developer`)\\n- Expected revision: `1`\\n- Candidate head: `70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23`\\n- Trusted ancestry base / PR base: `dogfood/v0.3-happy-path-0001`\\n\\n## Artifacts\\n\\n- Requirement: n/a — this dogfood slot has no separate requirement artifact\\n- Design: n/a — this dogfood slot has no separate design artifact\\n- Task: `docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md` (manifest artifact `dogfood-scenario-task`)\\n- Evidence: `docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md` (this candidate)\\n\\n## Scope\\n\\n### Allowed changes\\n\\n- `docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md` (new, documentation only)\\n\\n### Out-of-scope / intentionally unchanged\\n\\n- `state/features/**` — authoritative Feature Manifest is not edited by this worker\\n- `state/events/**` — no Feature Event is emitted by this worker\\n- `.github/workflows/**`, Gate policy, runtime policy, trusted execution configuration\\n- No product code, no architecture or product scope broadening\\n- No Gate is passed or waived; no merge or release authority is exercised\\n\\n## Verification\\n\\n- [x] Required tests executed — no `.ai-sdlc/project.yaml` exists in this repository, so no\\n      `required_commands` are defined for this work unit; none were run\\n- [ ] Required CI checks pass — deferred to the trusted pipeline\\n- [x] Deviations from approved design documented — none; the candidate follows the task artifact literally\\n- [x] Risks and known limitations documented — see below\\n\\nCommands executed and their results:\\n\\n- `git branch --show-current` → `gh-aw/F-OPERATOR-V03-DOGFOOD-HAPPY-0001-37905505035-v1` (passed)\\n- `git merge-base --is-ancestor origin/dogfood/v0.3-happy-path-0001 HEAD` → passed\\n- `git diff --name-only origin/dogfood/v0.3-happy-path-0001...HEAD` (pre-edit) → empty (passed)\\n- `git diff --name-only origin/dogfood/v0.3-happy-path-0001...HEAD` (post-commit) → exactly\\n  `docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md` (passed)\\n- Forbidden-path check for `state/features/`, `state/events/`, `.github/workflows/` → none found (passed)\\n- `grep -n \\\"dogfood_result: happy-path\\\" docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md`\\n  → match at line 8 (passed)\\n\\nKnown limitations: this candidate is documentation-only evidence. Lifecycle authority remains the\\nprotected Operator Store plus canonical Feature Persist; independent review and verification are\\nlater stages and are not performed here.\\n\\n## Review focus\\n\\n- Confirm the candidate contains the exact `dogfood_result: happy-path` contract marker.\\n- Confirm the changed-file set is exactly one documentation path with no unrelated changes.\\n- Confirm no lifecycle state, Feature Manifest, Feature Event, Gate, or runtime configuration changed.\\n\\n## Gate status\\n\\n- Requirement gate: not applicable to this dogfood slot\\n- Design gate: not applicable to this dogfood slot\\n- Code gate: PENDING (independent review is a later stage)\\n- Verification gate: PENDING (independent QA is a later stage)\\n- Release gate: PENDING\\n\\n> Generated by [AI-SDLC gh-aw Developer (deepseek v0.3 local)](https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37905505035) · ⊞ 26.6K · [◷](https://github.com/search?q=repo%3Adream-xin%2Fai-sdlc+%22gh-aw-workflow-id%3A+ai-sdlc-gh-aw-developer-deepseek-v03-local%22&type=pullrequests)\\n\\n<!-- gh-aw-agentic-workflow: AI-SDLC gh-aw Developer (deepseek v0.3 local), engine: copilot, model: deepseek-chat, id: 37905505035, workflow_id: ai-sdlc-gh-aw-developer-deepseek-v03-local, run: https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37905505035 -->\\n\\n<!-- gh-aw-workflow-id: ai-sdlc-gh-aw-developer-deepseek-v03-local -->\\n<!-- gh-aw-workflow-call-id: DREAM-XIN/ai-sdlc/ai-sdlc-gh-aw-developer-deepseek-v03-local -->\",\"merged\":true,\"merge_commit_sha\":\"a7b208a49668fb9ae16de908a52418106c616819\",\"merged_at\":\"2026-10-09T08:47:29Z\",\"closed_at\":\"2026-10-09T08:47:29Z\",\"merged_by\":{\"login\":\"dream-xin-ai-sdlc-runtime-operator[bot]\",\"id\":316394104,\"node_id\":\"BOT_kgDOEtvKeA\",\"avatar_url\":\"https://avatars.githubusercontent.com/u/33620907?v=4\",\"gravatar_id\":\"\",\"url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D\",\"html_url\":\"https://github.com/apps/dream-xin-ai-sdlc-runtime-operator\",\"followers_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/followers\",\"following_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/following{/other_user}\",\"gists_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/gists{/gist_id}\",\"starred_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/starred{/owner}{/repo}\",\"subscriptions_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/subscriptions\",\"organizations_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/orgs\",\"repos_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/repos\",\"events_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/events{/privacy}\",\"received_events_url\":\"https://api.github.com/users/dream-xin-ai-sdlc-runtime-operator%5Bbot%5D/received_events\",\"type\":\"Bot\",\"user_view_type\":\"public\",\"site_admin\":false},\"user\":{\"login\":\"github-actions[bot]\",\"id\":41898282,\"node_id\":\"MDM6Qm90NDE4OTgyODI=\",\"avatar_url\":\"https://avatars.githubusercontent.com/in/15368?v=4\",\"gravatar_id\":\"\",\"url\":\"https://api.github.com/users/github-actions%5Bbot%5D\",\"html_url\":\"https://github.com/apps/github-actions\",\"followers_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/followers\",\"following_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/following{/other_user}\",\"gists_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/gists{/gist_id}\",\"starred_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/starred{/owner}{/repo}\",\"subscriptions_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/subscriptions\",\"organizations_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/orgs\",\"repos_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/repos\",\"events_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/events{/privacy}\",\"received_events_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/received_events\",\"type\":\"Bot\",\"user_view_type\":\"public\",\"site_admin\":false},\"head\":{\"ref\":\"gh-aw/F-OPERATOR-V03-DOGFOOD-HAPPY-0001-37905505035-v1-02ee4804c679cf64\",\"sha\":\"a7b208a49668fb9ae16de908a52418106c616819\",\"repo\":{\"full_name\":\"DREAM-XIN/ai-sdlc\"}},\"base\":{\"ref\":\"dogfood/v0.3-happy-path-0001\",\"sha\":\"70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23\",\"repo\":{\"full_name\":\"DREAM-XIN/ai-sdlc\"}}},\"artifacts\":{\"total_count\":7,\"artifacts\":[{\"id\":11604980124,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwNDk4MDEyNA==\",\"name\":\"agent\",\"size_in_bytes\":8871735,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604980124\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604980124/zip\",\"expired\":false,\"digest\":\"sha256:c9c416ecca99253d8966739383f6bd5b4d3abb63b82b5246149a672d9a99c0ec\",\"created_at\":\"2026-10-09T08:38:49Z\",\"updated_at\":\"2026-10-09T08:38:49Z\",\"expires_at\":\"2027-01-07T08:31:45Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}},{\"id\":11604578063,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwNDU3ODA2Mw==\",\"name\":\"usage\",\"size_in_bytes\":10462,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604578063\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604578063/zip\",\"expired\":false,\"digest\":\"sha256:8b5058ce5d9a533d739c4561f9e09ff424988bc8e21f52e35228c474a2adad40\",\"created_at\":\"2026-10-09T08:42:06Z\",\"updated_at\":\"2026-10-09T08:42:06Z\",\"expires_at\":\"2027-01-07T08:31:45Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}},{\"id\":11604502612,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwNDUwMjYxMg==\",\"name\":\"detection\",\"size_in_bytes\":21094,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604502612\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604502612/zip\",\"expired\":false,\"digest\":\"sha256:22c7ad39277ae42b684ae1f3d629386a382703090a1573d597ff6c97f0c975f5\",\"created_at\":\"2026-10-09T08:40:25Z\",\"updated_at\":\"2026-10-09T08:40:25Z\",\"expires_at\":\"2027-01-07T08:31:45Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}},{\"id\":11604418350,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwNDQxODM1MA==\",\"name\":\"safe-outputs-items\",\"size_in_bytes\":619,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604418350\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604418350/zip\",\"expired\":false,\"digest\":\"sha256:fb19af7d2aa1cf42e58feff958ba34b5041d394a81ec9fed9c4eeee5d4d729bf\",\"created_at\":\"2026-10-09T08:41:47Z\",\"updated_at\":\"2026-10-09T08:41:47Z\",\"expires_at\":\"2027-01-07T08:31:45Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}},{\"id\":11604306847,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwNDMwNjg0Nw==\",\"name\":\"info\",\"size_in_bytes\":628,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604306847\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604306847/zip\",\"expired\":false,\"digest\":\"sha256:eefc86098658c69cc269d4529c5753e11369e626b460d4014495567240ad416e\",\"created_at\":\"2026-10-09T08:32:04Z\",\"updated_at\":\"2026-10-09T08:32:04Z\",\"expires_at\":\"2027-01-07T08:31:45Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}},{\"id\":11604077541,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwNDA3NzU0MQ==\",\"name\":\"activation\",\"size_in_bytes\":1029171,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604077541\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11604077541/zip\",\"expired\":false,\"digest\":\"sha256:85b0d0eb95cc33e42ee61745122ce52806e4d4d39ab4b25bbd193fc8e347b06e\",\"created_at\":\"2026-10-09T08:32:05Z\",\"updated_at\":\"2026-10-09T08:32:05Z\",\"expires_at\":\"2026-10-10T08:32:04Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}},{\"id\":11603874257,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYwMzg3NDI1Nw==\",\"name\":\"agent-output-fallback\",\"size_in_bytes\":11618,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11603874257\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11603874257/zip\",\"expired\":false,\"digest\":\"sha256:4fb2b33909a060497d5fa1d85e7f884358d31b8e126467ee55ff6240569e3158\",\"created_at\":\"2026-10-09T08:38:47Z\",\"updated_at\":\"2026-10-09T08:38:47Z\",\"expires_at\":\"2027-01-07T08:31:45Z\",\"workflow_run\":{\"id\":37905505035,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"6e75792b8e441167cfaadab2d13667a2d80721b8\"}}]}}")
    state = {"observed": observed, "head": "a7b208a49668fb9ae16de908a52418106c616819",
             "developer_posts": 0, "created_prs": 0, "fixture_patches": 0,
             "changes": {"a7b208a49668fb9ae16de908a52418106c616819": {
                 "filename": "docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md",
                 "status": "added", "sha": "06e53ed96ff23f5e6079ec1ba63d811663fc3b33"}},
             "calls": [], "controller_source": subprocess.run(["git", "rev-parse", "HEAD"],
                 cwd=root, check=True, capture_output=True, text=True).stdout.strip(), "parents": {
                 "a7b208a49668fb9ae16de908a52418106c616819":
                 "70781c774ce0c4dda0b70ea5c19e6bf4b39bbb23"}}
    repository = "dream-xin/ai-sdlc"
    target_ref = "dogfood/v0.3-happy-path-0001"
    def read_ref(): return state["head"]
    def advance_ref(sha, *, change):
        state["parents"][sha] = state["head"]
        state["changes"][sha] = deepcopy(change)
        state["head"] = sha
    def fixture_pr():
        return {"number": 552, "state": "open", "draft": False,
                "html_url": "https://github.com/DREAM-XIN/ai-sdlc/pull/552",
                "head": {"ref": target_ref, "sha": state["head"], "repo": {"full_name": repository}},
                "base": {"ref": "main", "repo": {"full_name": repository}}}
    def response(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()
    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/" + repository
        expect(parsed.path.lower().startswith(prefix), "frozen provider escaped repository")
        path = unquote(parsed.path[len(prefix):])
        query = parse_qs(parsed.query)
        state["calls"].append((method, path))
        expect(method == "GET", "reconciliation attempted external mutation: " + method + " " + path)
        if path == "/actions/runs/37905505035": return response(deepcopy(observed["run"]))
        if path in {"/actions/runs/37905505035/jobs", "/actions/runs/37905505035/attempts/1/jobs"}:
            return response(deepcopy(observed["jobs"]))
        if path == "/actions/runs/37905505035/artifacts": return response(deepcopy(observed["artifacts"]))
        if path == "/actions/artifacts/11604418350/zip": return response(archive_bytes)
        if path == "/git/ref/heads/main":
            return response({"ref": "refs/heads/main", "object": {
                "type": "commit", "sha": state["controller_source"]}})
        if path == "/pulls/577": return response(deepcopy(observed["pr"]))
        if path == "/pulls/552": return response(fixture_pr())
        if path == "/pulls":
            return response([fixture_pr()] if query.get("state") == ["open"] else
                            [fixture_pr(), deepcopy(observed["pr"])])
        if path in {"/git/ref/heads/" + target_ref, "/git/refs/heads/" + target_ref}:
            return response({"object": {"sha": read_ref()}})
        if path.startswith("/compare/"):
            ancestor, descendant = path[len("/compare/"):].split("...", 1)
            cursor, distance, seen, commits = descendant, 0, set(), []
            while cursor != ancestor and cursor in state["parents"] and cursor not in seen:
                seen.add(cursor)
                commits.append(cursor)
                cursor = state["parents"][cursor]
                distance += 1
            if cursor == ancestor:
                files_by_path = {}
                for sha in reversed(commits):
                    change = state["changes"][sha]
                    files_by_path[change["filename"]] = deepcopy(change)
                return response({"status": "ahead" if distance else "identical",
                    "ahead_by": distance, "behind_by": 0, "total_commits": distance,
                    "merge_base_commit": {"sha": ancestor},
                    "commits": [{"sha": sha, "parents": [{"sha": state["parents"][sha]}]}
                                for sha in reversed(commits)],
                    "files": list(files_by_path.values())})
            return response({"status": "diverged", "ahead_by": 0, "behind_by": 1,
                             "merge_base_commit": {"sha": "0" * 40}})
        if path.startswith("/contents/"):
            content_path = path[len("/contents/"):]
            ref = query.get("ref", [None])[0]
            expect(ref is not None, "source proof omitted immutable ref")
            raw = subprocess.run(["git", "show", ref + ":" + content_path],
                cwd=root, check=True, capture_output=True).stdout
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()
            return response({"type": "file", "path": content_path, "sha": blob,
                "encoding": "base64", "content": base64.b64encode(raw).decode()})
        raise AssertionError("unmodeled frozen provider route: " + path)
    return SimpleNamespace(snapshot=snapshot, http=http, state=state, read_ref=read_ref,
        advance_ref=advance_ref, read_source_pr=lambda: deepcopy(observed["pr"]),
        effect_counts=lambda: {key: state[key] for key in
            ("developer_posts", "created_prs", "fixture_patches")})


def build_post_handoff_feature_fixture(preflight, candidate_provider, *, read_ref, advance_ref, slot=None):
    """Real Feature/Persist adapters over fake GitHub Contents and real reducer.

    Only REST storage and asynchronous Persist scheduling are simulated. Every
    Feature Event, revision transition and Store Persist fact is produced by the
    production code. advance_ref(new_sha) updates the shared fake fixture branch;
    it must also affect candidate-provider GETs, without counting as a handoff PATCH.
    """
    import base64
    import hashlib
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from urllib.parse import parse_qs, unquote, urlparse
    import yaml
    from apply_feature_event import apply_event
    from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway
    from operator_release_feature_event_gateway import build_release_decision_event_gateway
    from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
    from operator_store_model import canonical_json
    import v03_dogfood_runtime_driver as driver

    h = driver.HISTORICAL_PREHTTP_RECOVERY
    repository = preflight.execution.repository
    feature_id = h["feature_id"] if slot is None else slot.feature_id
    target_ref = h["target_ref"] if slot is None else slot.target_ref
    manifest_path = "state/features/" + feature_id + ".yaml"
    initial_text = "protocol_version: 0.1.0\nrevision: 1\nfeature:\n  id: F-OPERATOR-V03-DOGFOOD-HAPPY-0001\n  title: 'v0.3 release dogfood: happy_path'\n  risk: low\n  issue: '#342'\nworkflow:\n  profile: v03-release-dogfood\n  status: ACTIVE\n  current_stage: implementation\n  stages:\n  - id: implementation\n    status: WORKING\n  - id: code-review\n    status: TODO\n    gate: code-gate\n  - id: verification\n    status: TODO\n    gate: verification-gate\n  - id: acceptance\n    status: TODO\n    gate: release-gate\ntasks: []\nartifacts:\n- id: dogfood-scenario-task\n  type: dogfood-task\n  uri: docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\n  status: draft\ngates:\n- id: code-gate\n  status: PENDING\n- id: verification-gate\n  status: PENDING\n- id: release-gate\n  status: PENDING\nevidence: []\napplied_events:\n- EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-IMPLEMENTATION-START\nupdated_at: '2026-08-25T07:21:01Z'\n"
    if slot is not None:
        from v03_dogfood_fixture_pool import build_active_manifest
        initial_text = yaml.safe_dump(build_active_manifest(slot, repository=repository), sort_keys=False)
    initial = yaml.safe_load(initial_text)
    expect(initial["feature"]["id"] == feature_id and initial["revision"] == 1,
           "frozen pre-Persist manifest fixture identity changed")
    state = {
        "manifest": initial, "manifest_text": initial_text, "files": {},
        "pending": None, "pending_reads": 0, "puts": 0, "applied": 0,
        "calls": [], "commits": [], "fault": None, "observed_pending": 0,
    }

    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()

    def content(path, raw):
        return {"type": "file", "path": path, "name": path.rsplit("/", 1)[-1],
                "encoding": "base64", "content": base64.b64encode(raw).decode(),
                "sha": blob(raw), "size": len(raw)}

    def commit(kind, path, raw):
        parent = read_ref()
        expect(isinstance(parent, str) and len(parent) == 40, "fake fixture ref lacks a Git SHA")
        sha = hashlib.sha1(canonical_json({
            "parent": parent, "kind": kind, "path": path, "blob": blob(raw),
            "ordinal": len(state["commits"]) + 1,
        }).encode()).hexdigest()
        change = {"filename": path, "sha": blob(raw),
                  "status": "added" if kind == "feature-event-create" else "modified"}
        state["commits"].append({"parent": parent, "sha": sha, "kind": kind, "path": path,
                                 "blob_sha": blob(raw), "change": deepcopy(change)})
        advance_ref(sha, change=change)
        expect(read_ref() == sha, "fake provider did not expose newly committed fixture head")
        return sha

    def apply_pending():
        pending = state["pending"]
        if pending is None or state["fault"] == "pending":
            return
        event = pending["event"]
        if state["fault"] == "unrelated-revision":
            state["manifest"]["revision"] += 1
            state["manifest_text"] = yaml.safe_dump(state["manifest"], sort_keys=False)
            state["pending"] = None
            commit("unrelated-feature-change", manifest_path, state["manifest_text"].encode())
            return
        result = apply_event(deepcopy(state["manifest"]), deepcopy(event))
        expect(result["outcome"] == "APPLIED",
               "real Persist reducer rejected translated callback: " + repr(result.get("errors")))
        expect(result["manifest"]["revision"] == event["expected_revision"] + 1
               and event["id"] in result["manifest"]["applied_events"],
               "real reducer did not produce exact applied Event revision")
        state["manifest"] = result["manifest"]
        state["manifest_text"] = yaml.safe_dump(result["manifest"], sort_keys=False)
        state["pending"] = None
        state["applied"] += 1
        commit("persist-manifest", manifest_path, state["manifest_text"].encode())

    def http(method, url, headers, body):
        parsed = urlparse(url)
        prefix = "/repos/" + repository + "/contents/"
        expect(parsed.scheme == "https" and parsed.netloc == "api.github.test"
               and parsed.path.startswith(prefix), "Feature fixture escaped exact Contents API")
        path = unquote(parsed.path[len(prefix):])
        expect(parse_qs(parsed.query).get("ref") == [target_ref],
               "Feature fixture escaped the exact target ref")
        state["calls"].append((method, path))
        if method == "GET":
            if path == manifest_path:
                if state["pending"] is not None:
                    if state["pending_reads"] == 0:
                        state["pending_reads"] += 1
                        state["observed_pending"] += 1
                    else:
                        apply_pending()
                return 200, content(path, state["manifest_text"].encode())
            raw = state["files"].get(path)
            return (404, {}) if raw is None else (200, content(path, raw))
        expect(method == "PUT" and path.startswith("state/events/" + feature_id + "/")
               and path.endswith(".yaml") and body["branch"] == target_ref
               and "sha" not in body, "Feature fixture allowed a non-create Event mutation")
        if path in state["files"]:
            return 422, {"message": "immutable Event path already exists"}
        raw = base64.b64decode(body["content"], validate=True)
        event = yaml.safe_load(raw)
        expect(path == "state/events/" + feature_id + "/" + event["id"] + ".yaml"
               and event["feature_id"] == feature_id
               and event["expected_revision"] == state["manifest"]["revision"]
               and state["pending"] is None, "Event create escaped exact revision or overlaps pending Persist")
        state["files"][path] = raw
        state["puts"] += 1
        state["pending"] = {"event": event, "path": path}
        state["pending_reads"] = 0
        sha = commit("feature-event-create", path, raw)
        if state["fault"] == "ack-loss":
            raise OSError("fixture lost Event create acknowledgement")
        return 201, {"content": content(path, raw), "commit": {"sha": sha}}

    event_gateway = build_release_decision_event_gateway(
        token="fixture", repository=repository, default_branch="main",
        feature_refs={feature_id: target_ref}, api_base="https://api.github.test",
        http_request=http, sleeper=lambda _: None, poll_attempts=3, poll_seconds=0)
    truth = DurableDecisionFeatureTruthGateway(
        runtime=preflight.composition.runtime, feature_gateway=event_gateway,
        candidate_provider=candidate_provider)
    persist = DurableVerticalFeaturePersistGateway(
        runtime=preflight.composition.runtime, event_gateway=event_gateway)
    return SimpleNamespace(feature_gateway=truth, persist_gateway=persist,
                           event_gateway=event_gateway, state=state, http=http,
                           apply_pending=apply_pending)

def build_post_handoff_gate_fixture(preflight, *, read_ref, fallback_http):
    """Actual Actions transport/source; fake successful Reviewer/QA HTTP results.

    Gate payloads are derived only from the actual production dispatch POST.
    No role selection, launch facts, callbacks, validation or Persist is seeded.
    fallback_http supplies existing Developer/PR/source routes at the HTTP boundary.
    """
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from urllib.parse import unquote, urlparse
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway
    from operator_vertical_gh_aw_actions_transport import (
        GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig)
    from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource
    from operator_vertical_gh_aw_github_source import _GATE_START, _GATE_END
    import v03_dogfood_runtime_driver as driver

    repository = preflight.execution.repository
    workflows = preflight.workflows
    source_sha = preflight.execution.installation_commit_sha
    state = {"runs": [], "posts": [], "routes": {}, "inputs": []}

    def respond(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/" + repository
        expect(parsed.path.startswith(prefix), "gate provider escaped repository")
        path = unquote(parsed.path[len(prefix):])
        if path.startswith("/actions/workflows/") and path.endswith("/runs"):
            workflow = path.split("/")[3]
            rows = [row for row in state["runs"]
                    if row["path"] == ".github/workflows/" + workflow]
            return respond({"total_count": len(rows), "workflow_runs": deepcopy(rows)})
        if method == "POST":
            expect(path.startswith("/actions/workflows/") and path.endswith("/dispatches"),
                   "gate provider received unexpected POST")
            submitted = json.loads(body)
            inputs = submitted["inputs"]
            role = inputs["role"]
            expect(role in {"reviewer", "qa"} and role not in [row["role"] for row in state["inputs"]],
                   "continuation attempted another Developer or duplicate Gate execution")
            workflow = workflows.workflow_for(role)
            expect(path == "/actions/workflows/" + workflow + "/dispatches"
                   and submitted["ref"] == "main"
                   and inputs["candidate_head_sha"] == read_ref(),
                   "actual Gate launch did not bind post-Persist candidate head")
            task = json.loads(inputs["task_payload"])["task"]
            run_id = 47905505035 + len(state["runs"]) + 1
            comment_id, job_id = run_id + 100, run_id + 200
            run = {"id": run_id, "run_attempt": 1, "repository": {"full_name": repository},
                "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
                "path": ".github/workflows/" + workflow,
                "display_title": "AI-SDLC gh-aw " + inputs["dispatch_key"],
                "event": "workflow_dispatch", "head_branch": "main", "head_sha": source_sha,
                "status": "completed", "conclusion": "success"}
            state["runs"].append(run)
            state["posts"].append(deepcopy(submitted))
            state["inputs"].append(deepcopy(inputs))
            comment_url = f"https://github.com/{repository}/pull/{inputs['candidate_pr_number']}#issuecomment-{comment_id}"
            external = {
                "version": "0.1.0", "contract": "ai-sdlc-gh-aw-" + role + "-result-v0.1",
                "id": "real-continuation-" + role, "feature_id": inputs["feature_id"],
                "task_id": task["id"], "stage": inputs["stage"], "role": role,
                "expected_revision": int(inputs["expected_revision"]),
                "target_repository": repository, "target_ref": inputs["target_ref"],
                "candidate_pr_number": int(inputs["candidate_pr_number"]),
                "candidate_head_sha": inputs["candidate_head_sha"], "verdict": "PASS",
                "occurred_at": preflight.composition.runtime.clock(),
                "evidence": [{"id": "real-continuation-" + role,
                    "type": "review" if role == "reviewer" else "verification",
                    "status": "pass", "uri": run["html_url"]}]}
            if role == "reviewer":
                external["findings"] = []
            else:
                external["checks"] = [{"name": "runtime", "status": "pass"}]
                external["coverage"] = [{"criterion": "happy path", "status": "pass"}]
            routes = state["routes"]
            routes[f"/actions/runs/{run_id}"] = run
            routes[f"/issues/comments/{comment_id}"] = {
                "id": comment_id, "html_url": comment_url,
                "issue_url": f"https://api.github.com/repos/{repository}/issues/{inputs['candidate_pr_number']}",
                "user": {"type": "Bot"},
                "body": _GATE_START + json.dumps(external) + _GATE_END}
            routes[f"/actions/runs/{run_id}/jobs"] = {"total_count": 2, "jobs": [
                {"id": job_id - 1, "name": "safe_outputs", "conclusion": "success",
                 "status": "completed", "run_id": run_id, "run_attempt": 1, "head_sha": source_sha},
                {"id": job_id, "name": "conclusion", "conclusion": "success",
                 "status": "completed", "run_id": run_id, "run_attempt": 1, "head_sha": source_sha}]}
            values = {
                "SOURCE_RUN_ID": run_id,
                "SOURCE_WORKFLOW_REF": f"{repository}/.github/workflows/{workflow}@refs/heads/main",
                "TARGET_REPOSITORY": repository, "TARGET_REF": inputs["target_ref"],
                "FEATURE_ID": inputs["feature_id"], "EXPECTED_REVISION": inputs["expected_revision"],
                "STAGE": inputs["stage"], "ROLE": role, "TRUSTED_TASK_ID": task["id"],
                "CANDIDATE_PR_NUMBER": inputs["candidate_pr_number"],
                "CANDIDATE_HEAD_SHA": inputs["candidate_head_sha"],
                "COMMENT_ID": comment_id, "COMMENT_URL": comment_url}
            routes[f"/actions/jobs/{job_id}/logs"] = "".join(
                (f"2026-10-09T09:00:00Z   {name}: {value}" + chr(10)) for name, value in values.items()).encode()
            return 204, {}, b""
        expect(method == "GET", "gate HTTP provider allowed an unapproved effect")
        if path in state["routes"]:
            return respond(deepcopy(state["routes"][path]))
        return fallback_http(method=method, url=url, token=token)

    transport = GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=repository, token="fixture", workflows=workflows,
            launch_poll_attempts=2, launch_poll_seconds=0),
        http=http, sleeper=lambda _: None)
    gateway = GhAwVerticalRoleDispatchGateway(transport=transport, workflows=workflows)
    source = FirstAttemptDigestBoundGhAwResultSource(
        preflight.composition.recovery_result_source.config,
        target_repository=repository, http=http)
    return SimpleNamespace(transport=transport, dispatch_gateway=gateway,
                           result_source=source, state=state, http=http)


def build_post_handoff_responses_host(preflight, *, adapter, expected_revision, session_label="resume"):
    """Real Responses host and adapter; only OpenAI HTTP replies are fake."""
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_api import API_VERSION
    from operator_openai_responses import OpenAIResponsesOperatorAdapter
    from v03_dogfood_openai_host import V03DogfoodOpenAIHostConfig, V03DogfoodOpenAIResponsesHost

    expect(isinstance(adapter, OpenAIResponsesOperatorAdapter),
           "post-handoff entrypoint test requires the actual Responses adapter")
    expect(type(expected_revision) is int and expected_revision >= 1,
           "post-handoff start needs actual trusted Feature revision")
    call_id = "post-handoff-" + session_label + "-revision-" + str(expected_revision)
    response_prefix = "resp_" + session_label + "_" + str(expected_revision)
    responses = [
        {"id": response_prefix + "_start", "status": "completed", "output": [{
            "type": "function_call", "id": "fc_post_handoff_start", "call_id": call_id,
            "name": "aisdlc_v1_operation_status", "status": "completed",
            "arguments": json.dumps({"api_version": API_VERSION,
                "operation_id": "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"})}]},
        {"id": response_prefix + "_waiting", "status": "completed", "output": [{
            "type": "message", "id": "msg_post_handoff_waiting", "role": "assistant",
            "content": [{"type": "output_text", "text": "The trusted operation reached its durable boundary."}]}]},
    ]
    requests = []
    def post(url, headers, body):
        expect(url == "https://openai.fixture/v1/responses"
               and headers["Authorization"] == "Bearer fixture-openai"
               and body["parallel_tool_calls"] is False and body["tools"],
               "real Responses host escaped fixed provider request profile")
        expect(len(requests) < 2, "post-handoff host requested an extra model continuation")
        requests.append(deepcopy(body))
        if len(requests) == 2:
            outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
            expect(len(outputs) == 1 and outputs[0]["call_id"] == call_id,
                   "real adapter did not return the one operation.status result to host")
            response = json.loads(outputs[0]["output"])
            expect(response.get("ok") is True, "real operation.status adapter returned error: " + str(response))
        return 200, responses[len(requests) - 1]
    host = V03DogfoodOpenAIResponsesHost(
        config=V03DogfoodOpenAIHostConfig(api_key="fixture-openai", model="fixture-model",
            api_base="https://openai.fixture/v1", max_tool_turns=2),
        adapter=adapter, http_post=post)
    return SimpleNamespace(host=host, requests=requests, call_id=call_id)


def assert_post_handoff_reconciled_callback(preflight, callback, *, coordinator,
                                             feature_fixture, read_source_pr, effect_counts):
    """Execute admitted re-observation through real coordinator and real Persist.

    Caller has already run reconcile_post_handoff against the frozen 15-event
    Store. No validation, translation, Persist or status facts are fabricated.
    This helper stops at the production coordinator's next stable boundary.
    """
    from copy import deepcopy
    from operator_store_model import operation_events
    from operator_vertical_store import vertical_projection
    from v03_dogfood_full_composition import DogfoodTrustedCallbackCoordinator
    from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
    from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway

    expect(isinstance(coordinator, DogfoodTrustedCallbackCoordinator)
           and isinstance(coordinator.executor.persist_gateway, DurableVerticalFeaturePersistGateway)
           and isinstance(coordinator.executor.feature_gateway, DurableDecisionFeatureTruthGateway),
           "reconciliation regression bypassed production coordinator/Feature/Persist adapters")
    runtime = preflight.composition.runtime
    context = callback["context"]
    before = deepcopy(operation_events(runtime.backend.read_snapshot(), context.operation_id))
    expect(len(before) >= 16 and [row["event_type"] for row in before[12:15]] == [
        "worker.callback.recorded", "worker.result.rejected", "loop.stable-stop"],
        "reconciliation fixture omitted real frozen rejected callback history")
    expect(before[14]["payload"]["status"] == "BLOCKED",
           "reconciliation fixture did not begin at the actual BLOCKED predecessor")
    old_id = before[12]["payload"]["callback_id"]
    expect(callback["callback_id"] != old_id, "reconciliation reused rejected callback identity")
    original_pr = deepcopy(read_source_pr())
    expect(original_pr["number"] == 577 and original_pr["state"] == "closed"
           and original_pr["merged"] is True and original_pr["draft"] is True
           and original_pr["head"]["sha"] == "a7b208a49668fb9ae16de908a52418106c616819"
           and original_pr["merge_commit_sha"] == original_pr["head"]["sha"],
           "reconciliation did not exercise actual closed/merged successful output")
    before_effects = dict(effect_counts())
    before_puts, before_applied = feature_fixture.state["puts"], feature_fixture.state["applied"]
    result = coordinator.handle(**deepcopy(callback))
    after = operation_events(runtime.backend.read_snapshot(), context.operation_id)
    expect(after[:len(before)] == before, "reconciliation rewrote frozen history or admitted callback")
    expect(read_source_pr() == original_pr, "reconciliation rewrote historical source PR metadata")
    after_effects = effect_counts()
    for kind in ("developer_posts", "created_prs", "fixture_patches"):
        expect(after_effects[kind] == before_effects[kind], "reconciliation repeated external effect: " + kind)
    callbacks = [row for row in after if row["event_type"] == "worker.callback.recorded"]
    expect(sum(row["payload"]["callback_id"] == old_id for row in callbacks) == 1
           and sum(row["payload"]["callback_id"] == callback["callback_id"] for row in callbacks) == 1,
           "reconciliation duplicated either immutable observation")
    expect(not any(row["event_type"] == "worker.result.validated"
                   and row["payload"].get("callback_id") == old_id for row in after),
           "reconciliation retroactively accepted rejected original observation")
    accepted = [row for row in after if row["event_type"] == "worker.result.validated"
                and row["payload"].get("callback_id") == callback["callback_id"]]
    translated = [row for row in after if row["event_type"] == "feature.event.translated"
                  and row["payload"].get("callback_id") == callback["callback_id"]]
    expect(len(accepted) == len(translated) == 1, "actual reconciled callback was not accepted/translated exactly once")
    event_id = translated[0]["payload"]["feature_event_id"]
    phases = [row for row in after if row["event_type"].startswith("persist.")
              and row["payload"].get("feature_event_id") == event_id]
    expect([row["event_type"] for row in phases] == [
        "persist.requested", "persist.linearized", "persist.confirmed"],
        "actual durable Persist triplet is missing or duplicated")
    expect(accepted[0]["sequence"] < translated[0]["sequence"] < phases[0]["sequence"]
           < phases[1]["sequence"] < phases[2]["sequence"]
           and phases[2]["payload"]["result_revision"] == context.expected_revision + 1,
           "real Persist ordering/revision differs from reconciled observation")
    expect(feature_fixture.state["puts"] > before_puts
           and feature_fixture.state["applied"] > before_applied
           and feature_fixture.state["observed_pending"] >= 1
           and event_id in feature_fixture.state["manifest"]["applied_events"],
           "canonical Event REST/PENDING/reducer/APPLIED path was bypassed")
    projection = vertical_projection(runtime.backend.read_snapshot(), context.operation_id)
    expect(result["status"] == projection["status"]
           and result["status"] not in {"BLOCKED", "NEEDS_USER"}
           and projection["expected_feature_revision"] == feature_fixture.state["manifest"]["revision"],
           "reconciliation status and real Feature revision did not converge")
    return result


def assert_post_handoff_done_replay(preflight, *, adapter, feature_fixture, gate_fixture, effect_counts):
    """Replay actual status/resume after DONE; completion notification stays unique."""
    from copy import deepcopy
    import v03_dogfood_scenario_runner as runner
    from operator_store_model import operation_events
    from operator_vertical_store import vertical_projection
    operation_id = driver_subject.HISTORICAL_PREHTTP_RECOVERY["operation_id"]
    runtime = preflight.composition.runtime
    before = deepcopy(operation_events(runtime.backend.read_snapshot(), operation_id))
    done = [row for row in before if row["event_type"] == "operation.done"]
    notifications = [row for row in before if row["event_type"] == "notification.created"]
    expect(len(done) == len(notifications) == 1
           and done[0]["sequence"] < notifications[0]["sequence"],
           "actual DONE lacks one later immutable completion notification")
    effects = dict(effect_counts())
    puts, applied, posts = feature_fixture.state["puts"], feature_fixture.state["applied"], len(gate_fixture.state["posts"])
    host = build_post_handoff_responses_host(preflight, adapter=adapter,
        expected_revision=feature_fixture.state["manifest"]["revision"], session_label="completed-replay")
    coordinator = preflight.composition.bundle.decision_notification_coordinator
    old_context = coordinator.trusted_context_digest
    coordinator.trusted_context_digest = "changed-current-context-on-replay"
    before_notification = (runtime.backend.read_snapshot().ref_sha, runtime.backend.commit_count)
    try:
        runner._notify_completed(preflight, operation_id)
        expect(before_notification == (runtime.backend.read_snapshot().ref_sha, runtime.backend.commit_count),
               "existing immutable Notification replay wrote Store under a changed context")
        replay = runner.run_scenario(preflight=preflight, host=host.host)
    finally:
        coordinator.trusted_context_digest = old_context
    expect(replay.operation_id == operation_id and replay.final_status == "DONE"
           and replay.worker_results_consumed == 3
           and vertical_projection(runtime.backend.read_snapshot(), operation_id)["generation"] == 1,
           "completed replay changed Operation identity or lifecycle")
    expect(operation_events(runtime.backend.read_snapshot(), operation_id) == before,
           "completed replay duplicated completion notification or lifecycle facts")
    expect(feature_fixture.state["puts"] == puts and feature_fixture.state["applied"] == applied
           and len(gate_fixture.state["posts"]) == posts and effect_counts() == effects,
           "completed replay repeated Persist or external effects")


def finish_post_handoff_pipeline_tests(preflight, *, gate_fixture, feature_fixture,
                                       read_ref, effect_counts, adapter):
    """Continue real scenario collection through Reviewer/QA and real finalizer.

    Call immediately after atomic reconciliation, before processing its new callback.
    The actual Responses host/adapter/start backend must invoke the bound recovering
    executor. Provider routes alone are fake; all claims, callback facts, translation,
    Persist and DONE are production.
    """
    import json
    from copy import deepcopy
    from dataclasses import is_dataclass, replace
    from types import SimpleNamespace
    from unittest.mock import patch
    import v03_dogfood_runtime_driver as driver
    import v03_dogfood_scenario_runner as runner
    import v03_dogfood_post_run_finalizer as finalizer
    import v03_dogfood_production_provenance as provenance
    from operator_store_model import operation_events, digest_json
    from operator_vertical import VerticalInvariantError
    from operator_vertical_store import vertical_projection

    h = driver.HISTORICAL_PREHTTP_RECOVERY
    runtime = preflight.composition.runtime
    operation_id = h["operation_id"]
    frozen_prefix = deepcopy(operation_events(runtime.backend.read_snapshot(), operation_id)[:15])
    before_effects = dict(effect_counts())
    first_host = build_post_handoff_responses_host(
        preflight, adapter=adapter, expected_revision=feature_fixture.state["manifest"]["revision"],
        session_label="first-observation")
    crash_expected = runtime.backend.fail_confirmation_once
    try:
        first_trace, first_operation, first_status = runner._resume_post_handoff(preflight, first_host.host)
    except OSError as exc:
        expect(crash_expected and str(exc) == "fixture crash before protected Persist confirmation",
               "unexpected error escaped real resume")
        interrupted = operation_events(runtime.backend.read_snapshot(), operation_id)
        expect(feature_fixture.state["applied"] == 1
               and any(e["event_type"] == "persist.linearized" for e in interrupted)
               and not any(e["event_type"] == "persist.confirmed" for e in interrupted)
               and not gate_fixture.state["inputs"],
               "crash fixture did not stop after real provider apply and before Store confirmation")
        first_host = build_post_handoff_responses_host(
            preflight, adapter=adapter, expected_revision=feature_fixture.state["manifest"]["revision"],
            session_label="after-confirmation-crash")
        first_trace, first_operation, first_status = runner._resume_post_handoff(preflight, first_host.host)
    else:
        expect(not crash_expected, "requested protected confirmation crash was not exercised")
    expect(first_operation == operation_id and first_status == "WAITING_EXTERNAL"
           and len(first_host.requests) == 2
           and feature_fixture.state["manifest"]["revision"] > 1
           and [row["role"] for row in gate_fixture.state["inputs"]] == ["reviewer"],
           "first actual host did not persist Developer and stop at one Reviewer")
    progressed_prefix = deepcopy(operation_events(runtime.backend.read_snapshot(), operation_id))
    progressed_puts = feature_fixture.state["puts"]
    host_fixture = build_post_handoff_responses_host(
        preflight, adapter=adapter, expected_revision=feature_fixture.state["manifest"]["revision"],
        session_label="fresh-post-persist")

    scenario = runner.run_scenario(preflight=preflight, host=host_fixture.host)
    expect(len(host_fixture.requests) == 2
           and scenario.operation_id == operation_id
           and scenario.worker_results_consumed == 3
           and scenario.function_call_ids == (host_fixture.call_id,)
           and scenario.dispatch_roles == ("developer", "reviewer", "qa"),
           "actual host/status/resume/scenario entry failed to consume the reconciled prefix exactly once")
    consumed = scenario.worker_results_consumed
    assert_post_handoff_done_replay(preflight, adapter=adapter, feature_fixture=feature_fixture,
        gate_fixture=gate_fixture, effect_counts=effect_counts)
    projection = vertical_projection(runtime.backend.read_snapshot(), operation_id)
    expect(projection["status"] == "DONE" and consumed == 3,
           "actual Reviewer/QA callback and lifecycle paths did not finish DONE")
    runner._notify_completed(preflight, operation_id)
    events = operation_events(runtime.backend.read_snapshot(), operation_id)
    completed_notifications = [e for e in events if e["event_type"] == "notification.created"
                               and e["payload"].get("notification_type") == "operation.completed"]
    expect(len(completed_notifications) == 1, "completion replay duplicated standard Notification")
    done_event = next(e for e in events if e["event_type"] == "operation.done")
    original_done_id = done_event["event_id"]
    done_event["event_id"] = "forged-done"
    try:
        runner._notify_completed(preflight, operation_id)
    except (runner.V03DogfoodScenarioRunnerError, __import__("operator_store_model").StoreInvariantError):
        pass
    else:
        raise AssertionError("completion Notification accepted a forged DONE identity")
    finally:
        done_event["event_id"] = original_done_id

    expect(events[:len(progressed_prefix)] == progressed_prefix,
           "fresh actual host rewrote or replayed the confirmed Developer prefix")
    expect(feature_fixture.state["puts"] > progressed_puts,
           "fresh actual host did not continue actual downstream Persist")
    expect(events[:15] == frozen_prefix, "downstream lifecycle rewrote original blocked history")
    claims = runner._dispatch_rows(preflight, operation_id)
    expect(tuple(runner._dispatch_role(row) for row in claims) == ("developer", "reviewer", "qa"),
           "actual continuation repeated or omitted a role")
    run_ids, receipt = runner._launch_receipts(preflight, operation_id)
    expect(run_ids == (37905505035, *[row["id"] for row in gate_fixture.state["runs"]]),
           "scenario receipts lost original successful Developer or added an execution")
    expect([row["role"] for row in gate_fixture.state["inputs"]] == ["reviewer", "qa"]
           and gate_fixture.state["inputs"][0]["candidate_head_sha"]
               != gate_fixture.state["inputs"][1]["candidate_head_sha"]
           and read_ref() != gate_fixture.state["inputs"][1]["candidate_head_sha"],
           "fake provider failed to advance candidate head through actual Persist commits")
    for kind in ("developer_posts", "created_prs", "fixture_patches"):
        expect(effect_counts()[kind] == before_effects[kind],
               "downstream continuation repeated forbidden external effect: " + kind)
    translated = {row["payload"]["feature_event_id"]: row["payload"]
                  for row in events if row["event_type"] == "feature.event.translated"}
    confirmed = [row["payload"] for row in events if row["event_type"] == "persist.confirmed"]
    expect(len(confirmed) == feature_fixture.state["applied"]
           and len(confirmed) == feature_fixture.state["puts"]
           and all(row["feature_event_id"] in translated for row in confirmed),
           "Store confirmations differ from real Event PUT/reducer applications")
    expect(feature_fixture.state["manifest"]["revision"] == projection["expected_feature_revision"],
           "final Feature revision and Store projection diverged")
    observation = {
        "scenario": "happy_path", "repository": preflight.execution.repository,
        "feature_id": h["feature_id"], "target_ref": h["target_ref"],
        "operation_id": operation_id,
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "candidate_pr_number": h["candidate_pr_number"], "candidate_head_sha": read_ref(),
        "final_status": "DONE", "workflow_run_ids": list(run_ids),
        "runtime_receipt_identity": receipt, "repeated_continue_messages": 0,
        "release_eligible": False, "provenance_verified": False}
    if is_dataclass(preflight):
        final_preflight = replace(preflight, candidate_head_sha=read_ref())
    else:
        final_preflight = SimpleNamespace(**dict(vars(preflight), candidate_head_sha=read_ref()))

    class Response:
        status = 200
        def __init__(self, raw): self.raw = raw
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return self.raw

    def fake_urlopen(req, timeout):
        expect(req.get_method() == "GET", "final provenance attempted provider mutation")
        status, _, raw = gate_fixture.http(method="GET", url=req.full_url, token="fixture")
        expect(status == 200, "final provenance escaped fake provider routes")
        return Response(raw)

    def finalize():
        return finalizer.finalize(
            observation=observation, preflight=final_preflight,
            source_run_id=47905505045, finalizer_run_id=47905505046, github_token="fixture")

    with patch.object(provenance, "urlopen", side_effect=fake_urlopen):
        record = finalize()
        expect(record["verdict"] == "PASS" and record["release_eligible"] is True
               and record["counts"]["human_interventions"] == 4
               and record["runtime"]["workflow_run_ids"] == list(run_ids),
               "real post-handoff full pipeline failed final provenance/release validation")
        # Controller-generated stage-start Persist cycles are not arbitrary
        # extra Worker confirmations. Rehash a forged semantic change so the
        # finalizer must authenticate its exact trusted transition.
        controller_rows = [row for row in events if row["event_type"] == "feature.event.translated"
                           and not row["payload"].get("callback_id")]
        expect(controller_rows, "full lifecycle omitted controller stage-start events")
        changed = controller_rows[0]
        original_payload = deepcopy(changed["payload"])
        for label in ("orphan-persist", "forged-stage-start"):
            if label == "orphan-persist":
                changed["payload"]["feature_event_id"] += "-UNBOUND"
            else:
                event_body = changed["payload"]["feature_event"]
                stage_changes = [item for item in event_body["changes"] if item.get("kind") == "stage"]
                expect(stage_changes, "controller event did not contain a stage transition")
                stage_changes[0]["status"] = "DONE"
                changed["payload"]["feature_event_digest"] = digest_json(event_body)
            try:
                finalize()
            except (finalizer.V03DogfoodPostRunFinalizerError,
                    provenance.DogfoodProvenanceVerificationError, VerticalInvariantError, ValueError):
                pass
            except AssertionError as exc:
                expect(str(exc).startswith("real dogfood happy_path: trusted provenance "),
                       "controller-cycle negative failed outside finalizer: " + str(exc))
            else:
                raise AssertionError("finalizer accepted " + label)
            finally:
                changed["payload"] = deepcopy(original_payload)
        expect(finalize()["verdict"] == "PASS", "restored controller lifecycle did not reverify")
        # Only the exact Developer may retain its archived execution source.
        for run in gate_fixture.state["runs"]:
            original = run["head_sha"]
            run["head_sha"] = "6e75792b8e441167cfaadab2d13667a2d80721b8"
            try:
                finalize()
            except (finalizer.V03DogfoodPostRunFinalizerError,
                    provenance.DogfoodProvenanceVerificationError, VerticalInvariantError, ValueError):
                pass
            except AssertionError as exc:
                expect(str(exc).startswith("real dogfood happy_path: trusted provenance verification failed:")
                       or str(exc) == "real dogfood happy_path: trusted provenance verifier errored: V03DogfoodPostRunFinalizerError: original callback differs from historical launch/fresh run",
                       "source mutation failed outside trusted provenance: " + str(exc))
            else:
                raise AssertionError("current-source Gate incorrectly inherited archived Developer source")
            finally:
                run["head_sha"] = original
        expect(finalize()["verdict"] == "PASS", "restored full pipeline failed re-verification")
    print("- real reconciled Developer/Reviewer/QA scenario and canonical Persist finish DONE and finalize")
    return record



def post_handoff_runtime_fixture():
    import base64
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_store_model import StoreSnapshot, operation_events
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_effect_rollout import ProtectedEffectLineageRolloutVerifier, EffectLineageWriteFence
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
    from v03_dogfood_fixture_pool import require_slot
    import v03_dogfood_full_composition as composition
    provider = post_handoff_frozen_provider_fixture(base64.b64decode(POST_HANDOFF_ARCHIVE_B64, validate=True))
    import subprocess
    from pathlib import Path
    files = provider.snapshot.files
    for name in ("effect-lineage-rollout.json", "writer-fence-receipt.json", "effect-resolution-policy.json", "decision-policy.json"):
        path = "config/operator/v03-vertical-policy/" + name
        files[path] = json.loads(subprocess.run(["git", "show", composition.POST_HANDOFF_STORE + ":" + path],
            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True).stdout)
    policy_path = "config/operator/v03-vertical-policy/"
    rollout_verifier = ProtectedEffectLineageRolloutVerifier(
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-lineage-rollout.json"]),
        writer_fence_receipt_loader=lambda *_: deepcopy(files[policy_path + "writer-fence-receipt.json"]))
    rollout = rollout_verifier.verify(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE)
    resolution = ProtectedEffectResolutionPolicyVerifier(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-resolution-policy.json"]),
        evidence_fact_loader=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected resolution evidence")))
    resolution.verify_current()
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
                             snapshot=deepcopy(provider.snapshot))
            self.commit_count = 0
            self.fail_confirmation_once = False
        def commit(self, plan, receipt):
            if self.fail_confirmation_once and any(isinstance(m.value, dict)
                    and m.value.get("event_type") == "persist.confirmed" for m in plan.mutations):
                self.fail_confirmation_once = False
                raise OSError("fixture crash before protected Persist confirmation")
            result = super().commit(plan, receipt)
            self.commit_count += 1
            self.snapshot = StoreSnapshot(f"{self.commit_count:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    runtime = OperatorStoreRuntime(backend=Backend(), protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout), clock=lambda: "2026-10-09T09:10:00Z")
    workflows = GhAwVerticalWorkflowMap(default_branch="main",
        developer_workflow=composition.RECOVERY_DEVELOPER_WORKFLOW,
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-deepseek-v03-local.lock.yml")
    policy = recovery_policy_fixture()
    provider.state["controller_source"] = policy.installation_commit_sha
    source = composition.RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(control_repository="dream-xin/ai-sdlc",
            control_token="fixture", target_token="fixture", workflows=workflows,
            collector_identity=composition.COLLECTOR_IDENTITY),
        target_repository="dream-xin/ai-sdlc", http=provider.http)
    pf = SimpleNamespace(slot=require_slot("happy_path"), workflows=workflows,
        candidate_pr_number=552, candidate_head_sha=composition.POST_HANDOFF_HEAD,
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha=policy.installation_commit_sha),
        trusted_context_digest="6" * 64,
        composition=SimpleNamespace(runtime=runtime, policy_authority=policy, recovery_result_source=source))
    def get_json(url, headers):
        status, _, raw = provider.http(method="GET", url=url, token="fixture")
        return status, json.loads(raw)
    candidate = composition.DogfoodGitHubCandidateProvider(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", http_get=get_json)
    candidate.bind_runtime(runtime)
    feature = build_post_handoff_feature_fixture(pf, candidate,
        read_ref=provider.read_ref, advance_ref=provider.advance_ref)
    candidate.persist_gateway = feature.persist_gateway
    gates = build_post_handoff_gate_fixture(pf, read_ref=provider.read_ref, fallback_http=provider.http)
    bindings = {role: {"role": role, "workflow_file": workflows.workflow_for(role),
        "default_branch": "main", "worker_id": "fixture-" + role,
        "profile": "fixture-" + role, "selection_policy_id": "fixture-reviewed-policy"} for role in ("developer", "reviewer", "qa")}
    dispatch = composition.DogfoodExecutionBoundDispatchGateway(
        delegate=gates.dispatch_gateway, execution_bindings=bindings)
    one_shot = StoreBackedOneShotExternalCreateGateway(runtime=runtime, delegate=dispatch,
        trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True)
    loader = composition.DogfoodRecoveryBoundContentLoader(
        result_source=gates.result_source, recovery_result_source=source, policy_authority=policy)
    loader.bind_runtime(runtime)
    source.bind_post_handoff(runtime, policy)
    base = TrustedVerticalExecutor(runtime=runtime, feature_gateway=feature.feature_gateway,
        persist_gateway=feature.persist_gateway, dispatch_gateway=one_shot,
        config=TrustedVerticalExecutorConfig(target_ref=pf.slot.target_ref,
            trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True,
            old_writers_quiesced=True, rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest, max_auto_steps=64),
        resolution_policy_verifier=resolution)
    from pathlib import Path
    from operator_production_runtime import TrustedOperatorRuntimeConfig, TrustedFeatureBinding
    from operator_decision_policy import ProtectedDecisionPolicyVerifier
    from validate_v03_dogfood_runtime_composition import assemble_post_handoff_responses_graph, assert_post_handoff_authority_graph
    config = TrustedOperatorRuntimeConfig(target_repository=pf.execution.repository,
        store_repository=pf.execution.repository, installation_ref="main", store_checkout=Path("."),
        principal="post-handoff-fixture",
        feature_bindings=(TrustedFeatureBinding(pf.slot.feature_id, pf.slot.target_ref),))
    decision = ProtectedDecisionPolicyVerifier(repository=config.store_repository, state_ref=config.state_ref,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "decision-policy.json"]))
    def reader_get(url, headers):
        if "/contents/state/features/" in url:
            return feature.http("GET", url.replace("https://api.github.com", "https://api.github.test"), headers, None)
        return get_json(url, headers)
    responses, graph_before = assemble_post_handoff_responses_graph(
        runtime=runtime, base_executor=base, content_loader=loader, slot=pf.slot, config=config,
        policy_authority=policy, decision_policy_verifier=decision,
        trusted_role_policy="fixture-independent-role-policy", collector_namespace_policy="fixture-collector-namespace",
        reader_http_get=reader_get)
    executor = responses.operator_bundle.executor
    delegate = responses.operator_bundle.callback_coordinator
    predecessor_events = deepcopy(operation_events(runtime.backend.read_snapshot(), composition.RECOVERY_OPERATION_ID))
    assert_post_handoff_authority_graph(graph_before, responses, policy, predecessor_events=predecessor_events)
    def forbidden_handoff_http(*args, **kwargs):
        raise AssertionError("post-handoff reconciliation attempted another fixture PATCH")
    handoff = composition.DogfoodCandidateHandoff(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", candidate_provider=candidate, http_request=forbidden_handoff_http)
    handoff.content_loader = loader
    coordinator = composition.DogfoodTrustedCallbackCoordinator(delegate=delegate, candidate_handoff=handoff)
    collector = ProductionGhAwVerticalResultCollector(callback_coordinator=coordinator,
        result_source=gates.result_source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock)
    recovery_collector = composition.DogfoodRecoveryCollector(callback_coordinator=coordinator,
        result_source=source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock, policy_authority=policy)
    pf.composition.__dict__.update(candidate_provider=candidate, feature_event_gateway=feature.event_gateway,
        result_source=gates.result_source, collector=collector, recovery_collector=recovery_collector,
        actions_transport=gates.transport, bundle=responses.operator_bundle,
        responses=responses, graph_before=graph_before, predecessor_events=predecessor_events,
        callback_coordinator=coordinator)
    return pf, provider, feature, gates, coordinator



def normal_and_remediation_autoclose_tests(template_preflight):
    """Actual fresh Developer coordinator/Persist across GitHub auto-close.

    Initial Feature snapshots differ only in normal/remediation lifecycle. All
    Operation facts and callback/Persist outcomes under test are production writes.
    External Actions/PR/Contents responses are fake; no model or live effects run.
    """
    import json
    from copy import deepcopy
    from dataclasses import replace
    from types import SimpleNamespace
    from urllib.parse import parse_qs, unquote, urlparse
    import yaml
    from operator_store import plan_operation_start
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_model import StoreSnapshot, operation_events
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway
    from operator_vertical_gh_aw_actions_transport import GitHubActionsWorkflowTransportConfig
    from operator_vertical_gh_aw_github_source import ProductionGhAwVerticalResultCollector
    from operator_vertical_store import vertical_projection
    from v03_dogfood_full_composition import (
        DogfoodGitHubCandidateProvider, DogfoodCandidateHandoff,
        DogfoodCandidateBoundActionsTransport, DogfoodHandoffAwareResultSource,
        DogfoodTrustedCallbackCoordinator)

    for remediation in (False, True):
        label = "remediation" if remediation else "normal"
        class Backend(MemoryStateRefBackend):
            def __init__(self):
                super().__init__(repository=template_preflight.execution.repository,
                    state_ref="refs/heads/ai-sdlc-operator-state", snapshot=StoreSnapshot("1" * 40, {}))
                self.count = 0
            def commit(self, plan, receipt):
                self.count += 1
                result = super().commit(plan, receipt)
                self.snapshot = StoreSnapshot(f"{self.count + 100:040x}", result.snapshot.files)
                return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
        runtime = OperatorStoreRuntime(backend=Backend(),
            protection_verifier=StaticProtectionVerifier(status=PROTECTED),
            clock=lambda: "2026-10-09T09:00:00Z")
        pf = SimpleNamespace(**dict(vars(template_preflight), composition=SimpleNamespace(
            runtime=runtime, recovery_result_source=template_preflight.composition.recovery_result_source)))
        h = driver_subject.HISTORICAL_PREHTTP_RECOVERY
        repo, ref, feature = pf.execution.repository, h["target_ref"], h["feature_id"]
        old, output_head = h["candidate_head_sha"], ("a" if remediation else "b") * 40
        state = {"head": old, "parents": {output_head: old}, "changes": {},
                 "patches": 0, "developer_posts": 0, "routes": {}, "run": None, "pr": None}
        def read_ref(): return state["head"]
        def advance_ref(sha, *, change):
            state["parents"][sha] = state["head"]
            state["changes"][sha] = deepcopy(change)
            state["head"] = sha
        def fixture_pr():
            return {"number": h["candidate_pr_number"], "state": "open", "draft": False,
                "html_url": f"https://github.com/{repo}/pull/{h['candidate_pr_number']}",
                "head": {"ref": ref, "sha": read_ref(), "repo": {"full_name": repo}},
                "base": {"ref": "main", "repo": {"full_name": repo}}}
        def response(value):
            return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()
        def base_http(*, method, url, token, body=None):
            parsed = urlparse(url)
            path = unquote(parsed.path.split("/repos/" + repo, 1)[1])
            if method != "GET":
                raise AssertionError("unexpected non-provider mutation " + method + " " + path)
            if path == "/pulls": return response([fixture_pr()])
            if path == f"/pulls/{h['candidate_pr_number']}": return response(fixture_pr())
            if state["pr"] and path == f"/pulls/{state['pr']['number']}": return response(deepcopy(state["pr"]))
            if path.startswith("/git/refs/heads/") or path.startswith("/git/ref/heads/"):
                return response({"object": {"sha": read_ref()}})
            if path.startswith("/compare/"):
                ancestor, descendant = path[len("/compare/"):].split("...", 1)
                cursor, chain = descendant, []
                while cursor != ancestor and cursor in state["parents"] and cursor not in chain:
                    chain.append(cursor)
                    cursor = state["parents"][cursor]
                expect(cursor == ancestor, "fake handoff requested unrelated ancestry")
                files = {}
                for sha in reversed(chain):
                    if sha in state["changes"]:
                        change = state["changes"][sha]
                        files[change["filename"]] = deepcopy(change)
                return response({"status": "ahead" if chain else "identical", "ahead_by": len(chain),
                    "behind_by": 0, "merge_base_commit": {"sha": ancestor}, "total_commits": len(chain),
                    "commits": [{"sha": sha, "parents": [{"sha": state["parents"][sha]}]}
                                for sha in reversed(chain)], "files": list(files.values())})
            if path in state["routes"]: return response(deepcopy(state["routes"][path]))
            raise AssertionError("unmodeled normal handoff route: " + path)
        gates = build_post_handoff_gate_fixture(pf, read_ref=read_ref, fallback_http=base_http)
        workflow = pf.workflows.developer_workflow
        def http(*, method, url, token, body=None):
            path = unquote(urlparse(url).path.split("/repos/" + repo, 1)[1])
            if path == "/actions/workflows/" + workflow + "/runs":
                rows = [] if state["run"] is None else [state["run"]]
                return response({"total_count": len(rows), "workflow_runs": deepcopy(rows)})
            if method == "POST" and path == "/actions/workflows/" + workflow + "/dispatches":
                expect(state["developer_posts"] == 0, "duplicate fake Developer create")
                submitted = json.loads(body)
                inputs = submitted["inputs"]
                state["developer_posts"] += 1
                run_id = 57905505035 + int(remediation)
                pr_number, job_id = 1901 + int(remediation), run_id + 10
                state["run"] = {"id": run_id, "run_attempt": 1,
                    "repository": {"full_name": repo}, "html_url": f"https://github.com/{repo}/actions/runs/{run_id}",
                    "path": ".github/workflows/" + workflow,
                    "display_title": "AI-SDLC gh-aw " + inputs["dispatch_key"],
                    "event": "workflow_dispatch", "head_branch": "main",
                    "head_sha": pf.execution.installation_commit_sha, "status": "completed", "conclusion": "success"}
                state["pr"] = {"number": pr_number, "id": 8900 + pr_number,
                    "node_id": "PR_autoclose_" + str(pr_number),
                    "html_url": f"https://github.com/{repo}/pull/{pr_number}", "state": "open", "draft": True,
                    "merged": False, "title": "[ai-sdlc gh-aw] auto-close regression", "body": "immutable fixture body",
                    "user": {"login": "github-actions[bot]", "type": "Bot"},
                    "head": {"ref": f"gh-aw/{feature}-{run_id}-v{inputs['expected_revision']}-fixture",
                             "sha": output_head, "repo": {"full_name": repo}},
                    "base": {"ref": ref, "sha": old, "repo": {"full_name": repo}}}
                state["open_pr"] = deepcopy(state["pr"])
                listing, archive = recovery_safe_output_artifact_fixture(
                    run_id=run_id, source_head=pf.execution.installation_commit_sha, pr=state["pr"])
                artifact_id = listing["artifacts"][0]["id"]
                routes = state["routes"]
                routes[f"/actions/runs/{run_id}"] = state["run"]
                routes[f"/actions/runs/{run_id}/artifacts"] = listing
                routes[f"/actions/artifacts/{artifact_id}/zip"] = archive
                routes[f"/actions/runs/{run_id}/jobs"] = {"jobs": [
                    {"id": job_id - 1, "name": "safe_outputs", "conclusion": "success"},
                    {"id": job_id, "name": "conclusion", "conclusion": "success"}]}
                values = {"RUN_URL": state["run"]["html_url"], "TARGET_REPOSITORY": repo,
                    "TARGET_REF": ref, "FEATURE_ID": feature, "EXPECTED_REVISION": inputs["expected_revision"],
                    "STAGE": inputs["stage"],
                    "TASK_PAYLOAD": inputs["task_payload"], "PR_URL": state["pr"]["html_url"]}
                routes[f"/actions/jobs/{job_id}/logs"] = "".join(
                    f"2026-10-09T09:00:00Z   {key}: {value}" + chr(10) for key, value in values.items()).encode()
                return 204, {}, b""
            return gates.http(method=method, url=url, token=token, body=body)
        def get_json(url, headers):
            status, _, raw = http(method="GET", url=url, token="fixture")
            return status, json.loads(raw)
        provider = DogfoodGitHubCandidateProvider(slot=pf.slot, repository=repo, token="fixture", http_get=get_json)
        provider.bind_runtime(runtime)
        feature_fixture = build_post_handoff_feature_fixture(pf, provider, read_ref=read_ref, advance_ref=advance_ref)
        if remediation:
            manifest = feature_fixture.state["manifest"]
            manifest["workflow"]["current_stage"] = "code-review"
            for stage in manifest["workflow"]["stages"]:
                if stage["id"] == "implementation": stage["status"] = "DONE"
                if stage["id"] == "code-review": stage["status"] = "WORKING"
            manifest["tasks"] = [{"id": feature + "-REMEDIATION", "kind": "remediation",
                "stage": "implementation", "role": "developer", "source_stage": "code-review",
                "feedback": "Fix the independently identified documentation finding.", "status": "WORKING",
                "runtime": "operator-vertical"}]
            manifest["artifacts"].append({"id": "prior-implementation", "type": "implementation",
                "uri": "docs/features/" + feature + "/prior.md", "status": "draft"})
            feature_fixture.state["manifest_text"] = yaml.safe_dump(manifest, sort_keys=False)
        source = DogfoodHandoffAwareResultSource(
            replace(pf.composition.recovery_result_source.config, workflows=pf.workflows),
            target_repository=repo, http=http)
        source.bind_handoff(runtime, feature_fixture.persist_gateway)
        provider.persist_gateway = feature_fixture.persist_gateway
        transport = DogfoodCandidateBoundActionsTransport(
            GitHubActionsWorkflowTransportConfig(control_repository=repo, token="fixture",
                workflows=pf.workflows, launch_poll_attempts=2, launch_poll_seconds=0),
            candidate_provider=provider, http=http, sleeper=lambda _: None)
        gateway = GhAwVerticalRoleDispatchGateway(transport=transport, workflows=pf.workflows)
        base = TrustedVerticalExecutor(runtime=runtime, feature_gateway=feature_fixture.feature_gateway,
            persist_gateway=feature_fixture.persist_gateway, dispatch_gateway=gateway,
            config=TrustedVerticalExecutorConfig(target_ref=ref,
                trusted_context_digest=pf.trusted_context_digest, legacy_compatibility_mode=True))
        executor = FailureClassifyingTrustedRecoveringVerticalExecutor(
            base_executor=base, content_loader=source.load_content,
            trusted_role_policy="normal-autoclose-role-policy",
            collector_namespace_policy="normal-autoclose-collector-policy")
        def handoff_http(method, url, headers, body):
            if method != "PATCH":
                return get_json(url, headers)
            expect(body == {"sha": output_head, "force": False} and state["patches"] == 0,
                   "normal handoff repeated or changed fast-forward")
            state["patches"] += 1
            state["head"] = output_head
            state["pr"].update(state="closed", merged=True, merge_commit_sha=output_head,
                merged_at=runtime.clock(), closed_at=runtime.clock(),
                merged_by={"login": "dream-xin-ai-sdlc-runtime-operator[bot]", "id": 316394104, "type": "Bot"})
            return 200, {"object": {"sha": output_head}}
        handoff = DogfoodCandidateHandoff(slot=pf.slot, repository=repo, token="fixture",
            candidate_provider=provider, http_request=handoff_http)
        handoff.content_loader = source.load_content
        delegate = TrustedVerticalCallbackCoordinator(executor=executor,
            trusted_role_policy=executor.trusted_role_policy,
            collector_namespace_policy=executor.collector_namespace_policy, content_loader=source.load_content)
        coordinator = DogfoodTrustedCallbackCoordinator(delegate=delegate, candidate_handoff=handoff)
        collector = ProductionGhAwVerticalResultCollector(callback_coordinator=coordinator,
            result_source=source, workflows=pf.workflows, control_repository=repo, clock=runtime.clock)
        started = runtime.commit_replanned(lambda snapshot: plan_operation_start(
            snapshot, target_repository=repo, feature_id=feature, expected_revision=1,
            idempotency_key="autoclose-" + label, occurred_at=runtime.clock(),
            trusted_context_digest=pf.trusted_context_digest, operation_profile=VERTICAL_PROFILE))
        operation_id = started.result["operation_id"]
        executor.advance_until_stop(operation_id=operation_id)
        events = operation_events(runtime.backend.read_snapshot(), operation_id)
        claim = next(row for row in events if row["event_type"] == "dispatch.claimed")
        collector.handle(operation_id=operation_id, external_dispatch_key=claim["payload"]["external_dispatch_key"])
        events = operation_events(runtime.backend.read_snapshot(), operation_id)
        accepted = [row for row in events if row["event_type"] == "worker.result.validated"]
        rejected = [row for row in events if row["event_type"] == "worker.result.rejected"]
        expect(len(accepted) == 1 and not rejected and feature_fixture.state["applied"] >= 1
               and state["developer_posts"] == 1 and state["patches"] == 1,
               label + " auto-close did not cross actual coordinator/Persist exactly once")
        expect(vertical_projection(runtime.backend.read_snapshot(), operation_id)["status"] == "WAITING_EXTERNAL"
               and gates.state["inputs"][-1]["role"] == "reviewer",
               label + " Developer did not reach actual next Reviewer dispatch")
        for key in ("number", "body", "base", "head"):
            expect(state["pr"][key] == state["open_pr"][key], "auto-close changed immutable PR " + key)
        callback = next(row for row in events if row["event_type"] == "worker.callback.recorded")
        uri = callback["payload"]["trusted_callback_envelope"]["collected_outputs"][0]["trusted_uri"]
        saved = deepcopy(state["pr"])
        saved_snapshot = deepcopy(runtime.backend.snapshot)
        for field, value in (("merged", False), ("merge_commit_sha", "9" * 40), ("state", "unknown")):
            state["pr"][field] = value
            try:
                source.load_content(uri)
            except VerticalInvariantError:
                pass
            else:
                raise AssertionError(label + " accepted forged closure " + field)
            finally:
                state["pr"] = deepcopy(saved)
            expect(runtime.backend.snapshot.files == saved_snapshot.files and state["patches"] == 1,
                   "read-only handoff verification changed Store or repeated PATCH")
        source.load_content(uri)
        if remediation:
            from dataclasses import asdict
            context = callback["payload"]["trusted_callback_envelope"]["trusted_context"]
            trusted = dict(context, launch_candidate_head_sha=context["candidate_head_sha"])
            log_path = next(path for path in state["routes"] if path.startswith("/actions/jobs/") and path.endswith("/logs"))
            original_log = state["routes"][log_path]
            mutations = (
                ("wrong wire stage", b"STAGE: implementation", b"STAGE: code-review"),
                ("wrong task kind", b'"kind":"remediation"', b'"kind":"stage"'),
                ("wrong task identity", b'code-remediation:', b'code-remediation-forged:'),
            )
            for label, needle, replacement in mutations:
                expect(needle in original_log, "remediation mutation did not exercise real logged input")
                state["routes"][log_path] = original_log.replace(needle, replacement)
                try:
                    source.resolve(external_dispatch_key=context["external_dispatch_key"],
                        expected_receipt_identity=context["runtime_receipt_identity"], trusted_context=trusted)
                except VerticalInvariantError:
                    pass
                else:
                    raise AssertionError("remediation source accepted " + label)
                finally:
                    state["routes"][log_path] = original_log
                expect(state["developer_posts"] == 1 and state["patches"] == 1,
                       "rejected remediation mapping repeated an external effect")
    print("- normal and remediation Developer auto-close cross real coordinator/reducer/Persist")



def post_handoff_admission_tests():
    from copy import deepcopy
    from operator_store_model import operation_events, canonical_json
    from operator_vertical import VerticalInvariantError
    from v03_dogfood_runtime_driver import reconcile_post_handoff
    import v03_dogfood_full_composition as composition
    pf, provider, feature, gates, coordinator = post_handoff_runtime_fixture()
    runtime = pf.composition.runtime
    original = deepcopy(runtime.backend.read_snapshot())
    original_events = operation_events(original, composition.RECOVERY_OPERATION_ID)
    expect(len(original_events) == 15, "reconciliation omitted frozen predecessor")
    result = reconcile_post_handoff(pf)
    expect(result["acquired"] is True, "fresh exact reconciliation did not acquire")
    current = runtime.backend.read_snapshot()
    expect(operation_events(current, composition.RECOVERY_OPERATION_ID)[:15] == original_events,
           "reconciliation changed frozen events")
    expect(len(operation_events(current, composition.RECOVERY_OPERATION_ID)) == 16,
           "reconciliation did not append exactly one observation")
    before = (current.ref_sha, canonical_json(current.files), runtime.backend.commit_count)
    replay = reconcile_post_handoff(pf)
    expect(replay["acquired"] is False and before == (
        runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
        runtime.backend.commit_count), "reconciliation replay mutated protected Store")
    expect(not gates.state["inputs"] and provider.effect_counts() == {
        "developer_posts": 0, "created_prs": 0, "fixture_patches": 0},
        "reconciliation claim reached an external effect")
    for path in composition.POST_HANDOFF_DOCUMENT_BLOBS:
        pf2, external, _, _, _ = post_handoff_runtime_fixture()
        pf2.composition.runtime.backend.snapshot.files[path] = None
        snapshot = pf2.composition.runtime.backend.read_snapshot()
        before = (snapshot.ref_sha, canonical_json(snapshot.files))
        try:
            reconcile_post_handoff(pf2)
        except (VerticalInvariantError, ValueError):
            pass
        else:
            raise AssertionError("reconciliation accepted missing pinned document: " + path)
        expect(before == (pf2.composition.runtime.backend.read_snapshot().ref_sha,
                          canonical_json(pf2.composition.runtime.backend.read_snapshot().files)),
               "invalid predecessor changed Store")
        expect(external.effect_counts() == provider.effect_counts(), "invalid predecessor caused effects")
    print("- exact frozen post-handoff admission and immutable replay verified")


def post_handoff_full_pipeline_tests(*, crash_before_confirmation=False):
    from v03_dogfood_runtime_driver import reconcile_post_handoff
    from validate_v03_dogfood_runtime_composition import assert_post_handoff_authority_graph
    pf, provider, feature, gates, coordinator = post_handoff_runtime_fixture()
    reconcile_post_handoff(pf)
    pf.composition.runtime.backend.fail_confirmation_once = crash_before_confirmation
    finish_post_handoff_pipeline_tests(pf, gate_fixture=gates, feature_fixture=feature,
        read_ref=provider.read_ref, effect_counts=provider.effect_counts,
        adapter=pf.composition.responses.adapter)
    assert_post_handoff_authority_graph(pf.composition.graph_before, pf.composition.responses,
        pf.composition.policy_authority, predecessor_events=pf.composition.predecessor_events)
    from validate_v03_dogfood_scenario_runner import run_case
    shared_files = pf.composition.runtime.backend.read_snapshot().files
    run_case("review_remediation",
        ["WAITING_EXTERNAL"] * 5 + ["DONE"], ["developer", "reviewer", "developer", "reviewer", "qa"],
        store_files=shared_files)
    run_case("session_recovery", ["WAITING_EXTERNAL", "NEEDS_USER"], ["developer"],
        store_files=shared_files)


def post_handoff_reconciliation_negative_tests():
    from copy import deepcopy
    from operator_store import StoreCommandError
    from operator_store_git import CasConflict
    from operator_store_model import canonical_json, operation_events
    from operator_vertical import VerticalInvariantError
    from v03_dogfood_runtime_driver import reconcile_post_handoff, V03DogfoodRuntimeDriverError
    import v03_dogfood_full_composition as c
    errors = (StoreCommandError, VerticalInvariantError, V03DogfoodRuntimeDriverError, c.V03DogfoodCompositionError, ValueError)
    def reject(pf, provider, label):
        runtime = pf.composition.runtime
        before = (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files))
        effects = provider.effect_counts()
        try:
            reconcile_post_handoff(pf)
        except errors:
            pass
        else:
            raise AssertionError("post-handoff reconciliation accepted " + label)
        expect(before == (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files)),
               "rejected reconciliation wrote Store: " + label)
        expect(provider.effect_counts() == effects, "rejected reconciliation caused forbidden effects: " + label)
    for label, mutate in (
        ("run attempt2", lambda p,s: p.state["observed"]["run"].update(run_attempt=2)),
        ("producer source", lambda p,s: p.state["observed"]["run"].update(head_sha="9"*40)),
        ("run failure", lambda p,s: p.state["observed"]["run"].update(conclusion="failure")),
        ("unmerged", lambda p,s: p.state["observed"]["pr"].update(merged=False)),
        ("merge commit", lambda p,s: p.state["observed"]["pr"].update(merge_commit_sha="9"*40)),
        ("merger", lambda p,s: p.state["observed"]["pr"]["merged_by"].update(id=1)),
        ("output content", lambda p,s: p.state["observed"]["pr"]["head"].update(sha="9"*40)),
        ("fixture ref", lambda p,s: p.state.update(head="9"*40)),
        ("consumer source", lambda p,s: p.state.update(controller_source="9"*40)),
    ):
        pf, provider, _, _, _ = post_handoff_runtime_fixture()
        mutate(provider, pf.composition.runtime.backend.snapshot)
        reject(pf, provider, label)
    for index in (12,13,14):
        pf, provider, _, _, _ = post_handoff_runtime_fixture()
        operation_events(pf.composition.runtime.backend.snapshot, c.RECOVERY_OPERATION_ID)[index]["payload"]["tampered"] = True
        reject(pf, provider, "frozen event " + str(index+1))
    for value in (None, {}, {"ordinal": 2}):
        pf, provider, _, _, _ = post_handoff_runtime_fixture()
        pf.composition.runtime.backend.snapshot.files[c.POST_HANDOFF_PATH] = value
        reject(pf, provider, "partial/corrupt attestation")
    for field in c.recovery_execution_binding_fields():
        pf, provider, _, _, _ = post_handoff_runtime_fixture()
        reconcile_post_handoff(pf)
        attestation = pf.composition.runtime.backend.snapshot.files[c.POST_HANDOFF_PATH]
        attestation["consumer_execution_binding"][field] = "9" * (40 if field.endswith("sha") else 64)
        reject(pf, provider, "consumer binding " + field)
    pf, provider, _, _, _ = post_handoff_runtime_fixture()
    runtime = pf.composition.runtime
    snapshot = runtime.backend.read_snapshot()
    _, _, closed, historical = c.observe_post_handoff_pr(pf.composition.recovery_result_source, snapshot)
    def planner(snap):
        return c.plan_post_handoff_reconciliation(snap,
            consumer_binding=c.recovery_execution_binding(pf.composition.policy_authority),
            closed_pr_attestation=closed, historical_open_binding=historical,
            occurred_at=runtime.clock(), trusted_context_digest=pf.trusted_context_digest)
    first, second = planner(snapshot), planner(snapshot)
    runtime.backend.commit(first, runtime.protected_receipt())
    try:
        runtime.backend.commit(second, runtime.protected_receipt())
    except CasConflict:
        pass
    else:
        raise AssertionError("two post-handoff CAS contenders committed")
    expect(reconcile_post_handoff(pf)["acquired"] is False,
           "CAS loser obtained another observation after winner/crash")
    pf2, provider2, _, _, _ = post_handoff_runtime_fixture()
    pf2.composition.runtime.backend.inject_conflict_once()
    result = reconcile_post_handoff(pf2)
    expect(result["acquired"] is True, "safe post-handoff CAS retry failed")
    observation_id = result["attestation"]["observation_callback_id"]
    executor = pf2.composition.bundle.executor
    executor.base._record_fact(c.RECOVERY_OPERATION_ID, "worker.result.rejected",
        {"callback_id": observation_id, "code": "BLOCKED", "reason": "fixture authenticated observation rejection"})
    executor.base._stable_stop(c.RECOVERY_OPERATION_ID, status="BLOCKED", reason="fixture observation failed")
    reject(pf2, provider2, "failed single reconciled observation")
    expect(provider.effect_counts() == provider2.effect_counts() ==
           {"developer_posts":0, "created_prs":0, "fixture_patches":0},
           "CAS/crash/failure replay spent another external effect")
    print("- reconciliation CAS/crash/rejection and exact predecessor/provider/source negatives fail closed")


def post_handoff_read_only_discovery_tests():
    import json
    from copy import deepcopy
    from operator_api import API_VERSION
    from operator_store_model import canonical_json
    from v03_dogfood_runtime_driver import reconcile_post_handoff
    from v03_dogfood_openai_host import V03DogfoodOpenAIResponsesHost, V03DogfoodOpenAIHostError
    import v03_dogfood_scenario_runner as runner
    pf, provider, _, gates, _ = post_handoff_runtime_fixture()
    reconcile_post_handoff(pf)
    runtime = pf.composition.runtime
    template = build_post_handoff_responses_host(pf, adapter=pf.composition.responses.adapter, expected_revision=1)
    status = {"type":"function_call","id":"status-call","call_id":"status-1",
              "name":"aisdlc_v1_operation_status","arguments":json.dumps({
                  "api_version":API_VERSION,"operation_id":runner.RECOVERY_OPERATION_ID})}
    batches = []
    for tool in ("aisdlc_v1_operation_start", "aisdlc_v1_operation_cancel", "aisdlc_v1_decision_respond"):
        batches.append([dict(status, name=tool)])
    batches.append([dict(status, arguments=json.dumps({"api_version":API_VERSION,"operation_id":"op-other"}))])
    batches.append([status, dict(status, call_id="cancel-2", name="aisdlc_v1_operation_cancel")])
    for calls in batches:
        before = (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
                  runtime.backend.commit_count)
        def malicious_post(url, headers, body):
            expect([tool["name"] for tool in body["tools"]] == ["aisdlc_v1_operation_status"],
                   "reconciliation advertised writable discovery tools")
            return 200, {"id":"resp_bad_discovery","status":"completed","output":deepcopy(calls)}
        host = V03DogfoodOpenAIResponsesHost(config=template.host.config,
            adapter=template.host.adapter, http_post=malicious_post)
        try:
            runner._resume_post_handoff(pf, host)
        except (runner.V03DogfoodScenarioRunnerError, V03DogfoodOpenAIHostError):
            pass
        else:
            raise AssertionError("fixed discovery accepted unauthorized provider calls")
        expect(before == (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
                          runtime.backend.commit_count), "disallowed discovery reached adapter journal or Store")
        expect(not gates.state["inputs"] and provider.effect_counts() ==
               {"developer_posts":0,"created_prs":0,"fixture_patches":0},
               "disallowed discovery reached downstream effects")
    print("- fixed discovery restricts advertised tools and rejects writes before adapter invocation")



def selected_dogfood_worker_contract_tests(root, *, developer_inputs=None):
    """Check real selection and gateway grammar; actual planner/Persist paths are tested separately."""
    import json
    import os
    import re
    import subprocess
    from copy import deepcopy
    import yaml

    scenarios = ("happy_path", "review_remediation", "session_recovery")
    roles = {"developer": "implementation", "reviewer": "code-review", "qa": "verification"}
    from v03_dogfood_fixture_pool import require_slot
    expected_model_env = {
        "COPILOT_PROVIDER_BASE_URL": "https://api.deepseek.com",
        "COPILOT_MODEL": "deepseek-chat",
        "COPILOT_PROVIDER_API_KEY": "${{ secrets.DEEPSEEK_API_KEY }}",
        "COPILOT_PROVIDER_TYPE": "openai",
        "COPILOT_PROVIDER_WIRE_API": "completions",
    }
    # PyYAML's YAML1.1 loader treats 'on' as boolean; support that parser detail only.
    def triggers(document):
        return document.get("on", document.get(True))
    def reject(label, call):
        try:
            call()
        except (AssertionError, ValueError, KeyError, StopIteration):
            return
        raise AssertionError("selected Worker contract accepted " + label)
    def unique_step(document, job, *, name=None, step_id=None):
        rows = [s for s in document["jobs"][job]["steps"]
                if (name is None or s.get("name") == name)
                and (step_id is None or s.get("id") == step_id)]
        expect(len(rows) == 1, "missing/duplicate selected Worker step")
        return rows[0]
    def shell_truth_table(step, cases):
        expect(not step.get("if") and step.get("continue-on-error", False) is False,
               "selected Worker guard can skip or swallow rejection")
        for values, accepted in cases:
            result = subprocess.run(["bash", "-c", step["run"]],
                env={"PATH": os.defpath, **values}, capture_output=True, timeout=5)
            expect((result.returncode == 0) is accepted, "selected Worker guard semantic drift")

    def check(role, source, body, compiled, source_text, lock_text):
        expect(source["engine"]["id"] == "copilot" and source["engine"]["model"] == "deepseek-chat",
               "selected dogfood Worker changed paid DeepSeek model")
        expect(source["engine"]["env"] == expected_model_env, "selected source provider binding drift")
        expect(set(triggers(source)) == {"workflow_dispatch"}, "selected Worker has automatic trigger")
        for text in (source_text, lock_text):
            expect("AI_SDLC_RUNTIME_APP_PRIVATE_KEY" not in text
                   and "AI_SDLC_RUNTIME_APP_CLIENT_ID" not in text
                   and "GH_AW_CI_TRIGGER_TOKEN" not in text,
                   "selected Worker retains retired credential/collector dependency")
            # These selected same-repository Workers need no App private key at all.
            expect(not re.search(r"(?:secrets|vars)\.[A-Z0-9_]*(?:APP_PRIVATE_KEY|APP_CLIENT_ID)", text),
                   "selected same-repository Worker unnecessarily depends on App identity")
        allowed_secrets = {"COPILOT_GITHUB_TOKEN", "DEEPSEEK_API_KEY", "GH_AW_DEFAULT_OTLP_ENDPOINT",
            "GH_AW_DEFAULT_OTLP_HEADERS", "GH_AW_GITHUB_MCP_SERVER_TOKEN", "GH_AW_GITHUB_TOKEN", "GITHUB_TOKEN"}
        allowed_vars = {"GH_AW_DEFAULT_DETECTION_MAX_AI_CREDITS", "GH_AW_DEFAULT_MAX_AI_CREDITS",
            "GH_AW_DEFAULT_MAX_DAILY_AI_CREDITS", "GH_AW_DEFAULT_MAX_TURNS", "GH_AW_DEFAULT_OTLP_ENDPOINT",
            "GH_AW_DEFAULT_TIMEOUT_MINUTES", "GH_AW_GITHUB_APPROVAL_LABELS", "GH_AW_GITHUB_BLOCKED_USERS",
            "GH_AW_GITHUB_TRUSTED_USERS", "GH_AW_POLICY_ALLOW_CREATE_PULL_REQUEST", "GH_AW_RUNTIME_FEATURES"}
        expect(set(re.findall(r"secrets\.([A-Z0-9_]+)", source_text)) <= {"GITHUB_TOKEN", "DEEPSEEK_API_KEY"}
               and set(re.findall(r"secrets\.([A-Z0-9_]+)", lock_text)) <= allowed_secrets
               and set(re.findall(r"vars\.([A-Z0-9_]+)", lock_text)) <= allowed_vars,
               "selected source/compiled credential or variable dependency escaped finite allowlist")
        if role == "developer":
            expect("conclusion" not in source.get("jobs", {}), "Developer gained legacy collector transport")
        metadata = json.loads(lock_text.splitlines()[0].split(": ", 1)[1])
        expect(metadata["compiler_version"] == "v0.89.21" and metadata["strict"] is True,
               "selected Worker lock is not strict pinned compiler output")
        checkout = source["checkout"]
        ref = "${{ inputs.target_ref }}" if role == "developer" else "${{ inputs.candidate_head_sha }}"
        expect(checkout["repository"] == "dream-xin/ai-sdlc" and checkout["ref"] == ref
               and checkout["fetch-depth"] == 0 and checkout["github-token"] == "${{ secrets.GITHUB_TOKEN }}",
               "selected source checks out wrong candidate or requires retired credential")
        all_steps = [step for job in compiled["jobs"].values() for step in job.get("steps", [])]
        checkouts = [step for step in all_steps if str(step.get("uses", "")).startswith("actions/checkout@")]
        expect(checkouts and all(step.get("with", {}).get("persist-credentials") is False
                                 for step in checkouts), "selected lock persists checkout credentials")
        steps = compiled["jobs"]["agent"]["steps"]
        nested = [s for s in steps if s.get("with", {}).get("repository") == "dream-xin/ai-sdlc"]
        expect(len(nested) == 1 and nested[0]["with"].get("path") == "ai-sdlc"
               and nested[0]["with"].get("ref") == ref and nested[0]["with"].get("fetch-depth") == 0,
               "selected compiled checkout escaped exact nested candidate")
        engine = unique_step(compiled, "agent", step_id="agentic_execution")
        expect(steps.index(nested[0]) < steps.index(engine)
               and engine.get("working-directory") is None
               and '--container-workdir "${GITHUB_WORKSPACE}"' in engine["run"],
               "selected compiler workspace behavior or checkout-before-model order drifted")
        manifest_step = unique_step(compiled, "agent", name="Build checkout manifest for safe-outputs handlers")
        expect(steps.index(nested[0]) < steps.index(manifest_step) < steps.index(engine)
               and manifest_step["env"]["GH_AW_CHECKOUT_MANIFEST_COUNT"] == "1"
               and manifest_step["env"]["GH_AW_CHECKOUT_REPO_0"] == "dream-xin/ai-sdlc"
               and manifest_step["env"]["GH_AW_CHECKOUT_PATH_0"] == "ai-sdlc"
               and manifest_step["env"]["GH_AW_CHECKOUT_TOKEN_0"] == "${{ secrets.GITHUB_TOKEN }}",
               "actual compiled nested checkout manifest differs from candidate checkout")
        expect("$GITHUB_WORKSPACE/ai-sdlc" in body,
               "selected Worker prompt fails to distinguish nested candidate from outer control checkout")
        if role != "developer":
            expect("${{ inputs.candidate_head_sha }}" in body and "read-only" in body,
                   "Gate workspace guidance lost immutable candidate/read-only contract")
        expect(not engine.get("if"), "selected model can bypass failed preconditions")
        for key, value in expected_model_env.items():
            expect(engine.get("env", {}).get(key) == value, "compiled selected model provider differs: " + key)
        guard = unique_step(compiled, "agent", name="Reject rerun before model execution")
        expect(steps.index(guard) < steps.index(engine)
               and guard["env"] == {"RUN_ATTEMPT": "${{ github.run_attempt }}"},
               "selected model is not first-attempt fenced")
        shell_truth_table(guard, [({"RUN_ATTEMPT": a}, a == "1") for a in ("1", "2", "", "0")])
        identity = unique_step(compiled, "agent", name="Validate release-only local Worker identity")
        expect(steps.index(identity) < steps.index(engine)
               and identity["env"] == {
                   "TARGET_REPOSITORY": "${{ inputs.target_repository }}",
                   "TARGET_REF": "${{ inputs.target_ref }}",
                   "FEATURE_ID": "${{ inputs.feature_id }}",
                   "STAGE": "${{ inputs.stage }}", "ROLE": "${{ inputs.role }}"},
               "selected identity guard is absent, late, or caller-independent")
        identity_cases = []
        for scenario in scenarios:
            slot = require_slot(scenario)
            values = {"TARGET_REPOSITORY": "dream-xin/ai-sdlc", "TARGET_REF": slot.target_ref,
                      "FEATURE_ID": slot.feature_id, "STAGE": roles[role], "ROLE": role}
            identity_cases += [(values, True), (dict(values, TARGET_REF="main"), False)]
        identity_cases += [(dict(identity_cases[0][0], ROLE="product"), False)]
        shell_truth_table(identity, identity_cases)
        safe = source["safe-outputs"]
        expect(safe["github-token"] == "${{ secrets.GITHUB_TOKEN }}",
               "selected Safe Output token dependency drift")
        if role != "developer":
            expect("create-pull-request" not in safe and safe["add-comment"]["max"] == 1
                   and safe["add-comment"]["target"] == "${{ inputs.candidate_pr_number }}"
                   and safe["add-comment"]["target-repo"] == "dream-xin/ai-sdlc",
                   "selected Gate Safe Output escaped exact candidate comment")
            expect(source["tools"]["bash"] is False and source["tools"].get("cli-proxy") is False,
                   "selected Gate has implementation command authority")
            expect("AI-SDLC-GATE-RESULT" in body and "non-authoritative" in body
                   and ("ai-sdlc-gh-aw-" + role + "-result-v0.1") in body,
                   "selected Gate lost trusted collector envelope boundary")
        detector = safe["threat-detection"]
        expect(detector["enabled"] is True and detector["continue-on-error"] is False,
               "selected Safe Outputs detector fails open")
        detect = compiled["jobs"]["detection"]
        execution = unique_step(compiled, "detection", step_id="detection_agentic_execution")
        for key, value in expected_model_env.items():
            expect(execution.get("env", {}).get(key) == value,
                   "compiled detector provider differs from paid execution policy: " + key)
        expect(execution["env"]["CUSTOM_PROMPT"].strip() == detector["prompt"].strip(),
               "compiled detector does not receive full bounded source prompt")
        conclusion = unique_step(compiled, "detection", step_id="detection_conclusion")
        expect(conclusion.get("continue-on-error", False) is False and conclusion.get("if") == "always()"
               and conclusion["env"]["GH_AW_DETECTION_CONTINUE_ON_ERROR"] == "false",
               "selected detector loses mandatory semantic conclusion")
        expect(detect["outputs"]["detection_success"] == "${{ steps.detection_conclusion.outputs.success }}"
               and detect["outputs"]["detection_conclusion"] == "${{ steps.detection_conclusion.outputs.conclusion }}",
               "selected detector outputs are not real conclusion outputs")
        safe_job = compiled["jobs"]["safe_outputs"]
        expect("detection" in safe_job["needs"] and "needs.detection.result == 'success'" in safe_job["if"],
               "selected Safe Outputs lost detector job dependency")
        effect = unique_step(compiled, "safe_outputs", step_id="process_safe_outputs")
        if role != "developer":
            handler = json.loads(effect["env"]["GH_AW_SAFE_OUTPUTS_HANDLER_CONFIG"])
            expect(handler["add_comment"] == {"footer": False, "max": 1,
                "target": "${{ inputs.candidate_pr_number }}", "target-repo": "dream-xin/ai-sdlc"}
                and effect["with"]["github-token"] == "${{ secrets.GITHUB_TOKEN }}",
                "compiled comment effect no longer matches selected exact candidate")
            expect(any(s.get("with", {}).get("name") == "safe-outputs-items"
                       and "/tmp/gh-aw/safe-output-items.jsonl" in s.get("with", {}).get("path", "")
                       for s in safe_job["steps"]),
                   "compiled Gate lacks run-owned Safe Output manifest used by trusted collection")
            identity_name = "Record non-authoritative Gate execution identity"
            metadata_step = unique_step(compiled, "conclusion", name=identity_name)
            source_metadata = [s for s in source["jobs"]["conclusion"]["pre-steps"]
                               if s.get("name") == identity_name]
            expect(len(source_metadata) == 1 and metadata_step["run"] == source_metadata[0]["run"]
                   and metadata_step["env"] == source_metadata[0]["env"]
                   and source["jobs"]["conclusion"]["permissions"] == {"contents": "read"},
                   "actual generated read-only metadata differs from selected source")
            metadata_env = {
                "SOURCE_RUN_ID": "${{ github.run_id }}", "SOURCE_WORKFLOW_REF": "${{ github.workflow_ref }}",
                "SOURCE_HEAD_SHA": "${{ github.sha }}",
                "TRUSTED_TASK_ID": "${{ fromJSON(inputs.task_payload).task.id }}",
                "COMMENT_ID": "${{ needs.safe_outputs.outputs.comment_id }}",
                "COMMENT_URL": "${{ needs.safe_outputs.outputs.comment_url }}",
            }
            metadata_env.update({name: "${{ inputs." + name.lower() + " }}" for name in (
                "TARGET_REPOSITORY", "TARGET_REF", "FEATURE_ID", "EXPECTED_REVISION", "STAGE", "ROLE",
                "CANDIDATE_PR_NUMBER", "CANDIDATE_HEAD_SHA", "TASK_PAYLOAD", "DISPATCH_KEY")})
            expect(metadata_step["env"] == metadata_env, "selected metadata identity uses wrong authority")
            values = {"SOURCE_RUN_ID": "123", "SOURCE_WORKFLOW_REF":
                "dream-xin/ai-sdlc/.github/workflows/" + expected_files[role] + "@refs/heads/main",
                "SOURCE_HEAD_SHA": "a"*40, "TRUSTED_TASK_ID": "vertical:gate:1",
                "COMMENT_ID": "456", "COMMENT_URL": "https://github.com/dream-xin/ai-sdlc/pull/552#issuecomment-456",
                "TARGET_REPOSITORY": "dream-xin/ai-sdlc", "TARGET_REF": "dogfood/v0.3-happy-path-0001",
                "FEATURE_ID": "F-OPERATOR-V03-DOGFOOD-HAPPY-0001", "EXPECTED_REVISION": "4",
                "STAGE": roles[role], "ROLE": role, "CANDIDATE_PR_NUMBER": "552",
                "CANDIDATE_HEAD_SHA": "b"*40, "TASK_PAYLOAD": json.dumps({"task":{"id":"vertical:gate:1"}}),
                "DISPATCH_KEY": "dispatch-" + "c"*40}
            shell_truth_table(metadata_step, [(values, True),
                (dict(values, COMMENT_ID=""), False), (dict(values, TRUSTED_TASK_ID="wrong-task"), False),
                (dict(values, SOURCE_WORKFLOW_REF="other"), False),
                (dict(values, COMMENT_URL="https://github.com/dream-xin/ai-sdlc/pull/553#issuecomment-456"), False)])
        guard = unique_step(compiled, "safe_outputs",
            name="Require first attempt and affirmative detection before Safe Outputs effects")
        expect(safe_job["steps"].index(guard) < safe_job["steps"].index(effect) and not effect.get("if"),
               "selected effects can bypass semantic guard")
        expect(guard["env"] == {
            "RUN_ATTEMPT": "${{ github.run_attempt }}",
            "DETECTION_SUCCESS": "${{ needs.detection.outputs.detection_success }}",
            "DETECTION_CONCLUSION": "${{ needs.detection.outputs.detection_conclusion }}"},
            "selected effect guard trusts literals/caller verdict")
        cases = [({"RUN_ATTEMPT": a, "DETECTION_SUCCESS": s, "DETECTION_CONCLUSION": c},
                   a == "1" and s == "true" and c == "success")
                 for a,s,c in (("1","true","success"), ("2","true","success"),
                    ("1","false","success"), ("1","true","skipped"),
                    ("1","",""), ("1","unknown","success"), ("1","true","failure"))]
        shell_truth_table(guard, cases)

    from types import SimpleNamespace
    from gh_aw_provider_registry import load_registry
    from v03_dogfood_execution_bindings import credential_identities
    from v03_dogfood_live_gate import resolve_current_dogfood_bindings, V03DogfoodLiveGateError
    from v03_dogfood_runtime_preflight import _workflow_map, _execution_bindings
    expected_files = {
        "developer": "ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml",
        "reviewer": "ai-sdlc-gh-aw-reviewer-deepseek-v03-structured-local.lock.yml",
        "qa": "ai-sdlc-gh-aw-qa-deepseek-v03-structured-local.lock.yml",
    }
    presence = {name: False for name in credential_identities(load_registry())}
    presence["DEEPSEEK_API_KEY"] = True
    # Presence is not paid quota: the explicit release policy must remain DeepSeek
    # even when another provider has a configured but unusable credential.
    preferred = resolve_current_dogfood_bindings(presence, scenario="happy_path")
    competing = resolve_current_dogfood_bindings({name: True for name in presence}, scenario="happy_path")
    expect([(r.role, r.worker_workflow, r.selected_profile, r.model) for r in preferred]
           == [(r.role, r.worker_workflow, r.selected_profile, r.model) for r in competing],
           "configured unpaid provider displaced admitted paid DeepSeek execution")
    try:
        resolve_current_dogfood_bindings({name: False for name in presence}, scenario="happy_path")
    except V03DogfoodLiveGateError:
        pass
    else:
        raise AssertionError("current dogfood selection accepted missing paid provider credential")
    seen = {}
    for scenario in scenarios:
        resolved = resolve_current_dogfood_bindings(presence, scenario=scenario)
        gate = SimpleNamespace(scenario=scenario, bindings=resolved)
        workflows = _workflow_map(gate)
        rows = _execution_bindings(gate, workflows)
        raw = {r.role: r for r in resolved}
        expect(set(rows) == set(roles), "actual selected scenario lacks all three roles")
        for role, stage in roles.items():
            row = rows[role]
            expect(row["role"] == role and row["profile"] == "deepseek"
                   and raw[role].stage == stage and raw[role].model == "deepseek-chat"
                   and raw[role].accepted_credential_identities == ("DEEPSEEK_API_KEY",),
                   "actual dogfood selection ignored paid DeepSeek execution policy")
            filename = row["workflow_file"]
            expect(filename == expected_files[role] == workflows.workflow_for(role),
                   "actual selected Worker differs from admitted role-local pair")
            if filename not in seen:
                source_text = (root / ".github/workflows" / filename.replace(".lock.yml", ".md")).read_text()
                lock_text = (root / ".github/workflows" / filename).read_text()
                _, frontmatter, body = source_text.split("---\n", 2)
                source, compiled = yaml.safe_load(frontmatter), yaml.safe_load(lock_text)
                check(role, source, body, compiled, source_text, lock_text)
                for label, mutate in (
                    ("credential persistence", lambda c: next(s for j in c["jobs"].values()
                        for s in j.get("steps", []) if str(s.get("uses", "")).startswith("actions/checkout@"))["with"].update({"persist-credentials": True})),
                    ("model bypass", lambda c: unique_step(c, "agent", step_id="agentic_execution").update({"if": "always()"})),
                    ("effect bypass", lambda c: unique_step(c, "safe_outputs", step_id="process_safe_outputs").update({"if": "always()"})),
                    ("candidate checkout", lambda c: next(s for s in c["jobs"]["agent"]["steps"]
                        if s.get("with", {}).get("repository") == "dream-xin/ai-sdlc")["with"].update(ref="main")),
                    ("checkout manifest location", lambda c: unique_step(c, "agent",
                        name="Build checkout manifest for safe-outputs handlers")["env"].update(GH_AW_CHECKOUT_PATH_0="outer")),
                    ("model credential", lambda c: unique_step(c, "agent", step_id="agentic_execution")["env"].update(COPILOT_PROVIDER_API_KEY="${{ secrets.GEMINI_API_KEY }}")),
                ):
                    bad = deepcopy(compiled)
                    mutate(bad)
                    reject(label, lambda: check(role, source, body, bad, source_text, lock_text))
                reject("missing nested candidate guidance", lambda: check(role, source,
                    body.replace("$GITHUB_WORKSPACE/ai-sdlc", ""), compiled, source_text, lock_text))
                if role != "developer":
                    bad = deepcopy(compiled)
                    bad["jobs"]["conclusion"]["steps"].remove(unique_step(bad, "conclusion",
                        name="Record non-authoritative Gate execution identity"))
                    reject("missing generated identity step", lambda: check(role, source, body, bad, source_text, lock_text))
                reject("retired key dependency", lambda: check(role, source, body, compiled,
                    source_text, lock_text + "\\n# ${{ secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY }}"))
                reject("generic trigger dependency", lambda: check(role, source, body, compiled,
                    source_text + "\\n# ${{ secrets.GH_AW_CI_TRIGGER_TOKEN }}", lock_text))
                seen[filename] = role
            expect(seen[filename] == role, "two selected roles share a Worker")
        slot = require_slot(scenario)
        if developer_inputs is None:
            from operator_vertical import VERTICAL_PROFILE
            from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway
            gateway = GhAwVerticalRoleDispatchGateway(transport=object(), workflows=workflows)
            steps = ["vertical:implementation:1"]
            if scenario == "review_remediation":
                steps.append("vertical:code-remediation:1")
            payloads = []
            for index, task_id in enumerate(steps):
                dispatch = dict(operation_id="op-" + "a" * 40, operation_generation=0,
                    operation_profile=VERTICAL_PROFILE, semantic_effect_key="b" * 64,
                    external_dispatch_key="dispatch-" + "c" * 40,
                    dispatch_id="vertical-" + "d" * 32,
                    target_repository="dream-xin/ai-sdlc", target_ref=slot.target_ref,
                    feature_id=slot.feature_id, expected_revision=1 + index,
                    feature_stage="implementation" if index == 0 else "code-review",
                    task_id=task_id, task_identity=task_id, role="developer",
                    candidate_pr_number=552, candidate_head_sha="e" * 40)
                payloads.append(gateway._inputs(dispatch))
        else:
            payloads = developer_inputs(scenario)
        expect(payloads, "scenario lacks actual generated Developer dispatch inputs")
        expect(len(payloads) >= (2 if scenario == "review_remediation" else 1),
               "remediation scenario did not exercise normal and remediation Developer grammar")
        kinds = set()
        for inputs in payloads:
            task = json.loads(inputs["task_payload"])
            expect(inputs["feature_id"] == slot.feature_id and inputs["target_ref"] == slot.target_ref
                   and inputs["target_repository"].lower() == "dream-xin/ai-sdlc"
                   and inputs["role"] == "developer" and inputs["stage"] == "implementation",
                   "actual normal/remediation Developer inputs violate selected source identity guard")
            kinds.add(task["task"]["kind"])
            expect(task["contract"] == "ai-sdlc-task-v0.1"
                   and task["task"]["feature_id"] == slot.feature_id
                   and task["task"]["role"] == "developer"
                   and task["feature_context"]["id"] == slot.feature_id
                   and task["feature_context"]["vertical"]["expected_revision"] == int(inputs["expected_revision"]),
                   "actual Developer payload identity/revision differs from workflow inputs")
            expect(task["feature_context"]["repository"].lower() == inputs["target_repository"].lower()
                   and task["feature_context"]["manifest_ref"] and task["task"]["id"],
                   "actual Developer task grammar differs from source instructions")
        expect(kinds == ({"stage", "remediation"} if scenario == "review_remediation" else {"stage"}),
               "scenario Developer task kinds do not cover admitted normal/remediation grammar")
    expect(len(seen) == 3, "frozen scenarios do not use the same explicit three selected role Workers")
    print("- actual selected 3x3 dogfood Worker source/compiled contracts validated")

def build_selected_dogfood_gate_fixture(preflight, *, read_ref, fallback_http):
    """Actual Actions transport/source; fake successful Reviewer/QA HTTP results.

    Gate payloads are derived only from the actual production dispatch POST.
    No role selection, launch facts, callbacks, validation or Persist is seeded.
    fallback_http supplies existing Developer/PR/source routes at the HTTP boundary.
    """
    import json
    import hashlib
    import io
    import zipfile
    from pathlib import Path
    import yaml
    from copy import deepcopy
    from types import SimpleNamespace
    from urllib.parse import unquote, urlparse
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway
    from operator_vertical_gh_aw_actions_transport import (
        GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig)
    from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource
    from operator_vertical_gh_aw_github_source import _GATE_START, _GATE_END
    import v03_dogfood_runtime_driver as driver

    repository = preflight.execution.repository
    workflows = preflight.workflows
    source_sha = preflight.execution.installation_commit_sha
    state = {"runs": [], "posts": [], "routes": {}, "inputs": []}

    def respond(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/" + repository
        expect(parsed.path.startswith(prefix), "gate provider escaped repository")
        path = unquote(parsed.path[len(prefix):])
        if path.startswith("/actions/workflows/") and path.endswith("/runs"):
            workflow = path.split("/")[3]
            rows = [row for row in state["runs"]
                    if row["path"] == ".github/workflows/" + workflow]
            rows.extend(deepcopy(row) for row in getattr(preflight, "historical_gate_runs", ())
                        if row["path"] == ".github/workflows/" + workflow)
            return respond({"total_count": len(rows), "workflow_runs": deepcopy(rows)})
        if method == "POST":
            expect(path.startswith("/actions/workflows/") and path.endswith("/dispatches"),
                   "gate provider received unexpected POST")
            submitted = json.loads(body)
            inputs = submitted["inputs"]
            role = inputs["role"]
            expect(role in {"reviewer", "qa"} and role not in [row["role"] for row in state["inputs"]],
                   "continuation attempted another Developer or duplicate Gate execution")
            workflow = workflows.workflow_for(role)
            expect(path == "/actions/workflows/" + workflow + "/dispatches"
                   and submitted["ref"] == "main"
                   and inputs["candidate_head_sha"] == read_ref(),
                   "actual Gate launch did not bind post-Persist candidate head")
            task = json.loads(inputs["task_payload"])["task"]
            run_id = 47905505035 + len(state["runs"]) + 1
            comment_id, job_id = run_id + 100, run_id + 200
            run = {"id": run_id, "run_attempt": 1, "repository": {"full_name": repository},
                "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
                "path": ".github/workflows/" + workflow,
                "display_title": "AI-SDLC gh-aw " + inputs["dispatch_key"],
                "event": "workflow_dispatch", "head_branch": "main", "head_sha": source_sha,
                "status": "completed", "conclusion": "success"}
            state["runs"].append(run)
            state["posts"].append(deepcopy(submitted))
            state["inputs"].append(deepcopy(inputs))
            comment_url = f"https://github.com/{repository}/pull/{inputs['candidate_pr_number']}#issuecomment-{comment_id}"
            external = {
                "version": "0.1.0", "contract": "ai-sdlc-gh-aw-" + role + "-result-v0.1",
                "id": "real-continuation-" + role, "feature_id": inputs["feature_id"],
                "task_id": task["id"], "stage": inputs["stage"], "role": role,
                "expected_revision": int(inputs["expected_revision"]),
                "target_repository": repository, "target_ref": inputs["target_ref"],
                "candidate_pr_number": int(inputs["candidate_pr_number"]),
                "candidate_head_sha": inputs["candidate_head_sha"], "verdict": "PASS",
                "occurred_at": preflight.composition.runtime.clock(),
                "evidence": [{"id": "real-continuation-" + role,
                    "type": "review" if role == "reviewer" else "verification",
                    "status": "pass", "uri": run["html_url"]}]}
            if role == "reviewer":
                external["findings"] = []
            else:
                external["checks"] = [{"name": "runtime", "status": "pass"}]
                external["coverage"] = [{"criterion": "happy path", "status": "pass"}]
            routes = state["routes"]
            routes[f"/actions/runs/{run_id}"] = run
            routes[f"/issues/comments/{comment_id}"] = {
                "id": comment_id, "html_url": comment_url,
                "issue_url": f"https://api.github.com/repos/{repository}/issues/{inputs['candidate_pr_number']}",
                "user": {"type": "Bot"},
                "body": _GATE_START + json.dumps(external) + _GATE_END}
            lock = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows" / workflow).read_text())
            identity_steps = [step for step in lock["jobs"]["conclusion"]["steps"]
                if {"COMMENT_ID", "COMMENT_URL", "TRUSTED_TASK_ID", "SOURCE_RUN_ID", "SOURCE_WORKFLOW_REF"}
                <= set(step.get("env", {}))]
            expect(len(identity_steps) == 1,
                   "fake Gate cannot fabricate metadata absent from selected compiled source")
            expressions = {
                "${{ github.run_id }}": run_id,
                "${{ github.sha }}": source_sha,
                "${{ github.workflow_ref }}": f"{repository}/.github/workflows/{workflow}@refs/heads/main",
                "${{ needs.safe_outputs.outputs.comment_id }}": comment_id,
                "${{ needs.safe_outputs.outputs.comment_url }}": comment_url,
                "${{ fromJSON(inputs.task_payload).task.id }}": task["id"],
            }
            expressions.update({"${{ inputs." + key + " }}": value for key, value in inputs.items()})
            values = {name: expressions[expression] for name, expression in identity_steps[0]["env"].items()}
            jobs = []
            for index, (name, job) in enumerate(lock["jobs"].items()):
                identity = job_id if name == "conclusion" else job_id + index + 1
                jobs.append({"id": identity, "name": name, "conclusion": "success",
                    "status": "completed", "run_id": run_id, "run_attempt": 1, "head_sha": source_sha,
                    "steps": [{"name": step.get("name", step.get("id", "step")),
                               "status": "completed", "conclusion": "success"}
                              for step in job.get("steps", [])]})
            routes[f"/actions/runs/{run_id}/jobs"] = {"total_count": len(jobs), "jobs": jobs}
            routes[f"/actions/runs/{run_id}/attempts/1/jobs"] = {"total_count": len(jobs), "jobs": jobs}
            routes[f"/actions/jobs/{job_id}/logs"] = "".join(
                (f"2026-10-09T09:00:00Z   {name}: {value}" + chr(10)) for name, value in values.items()).encode()
            item = {
                "type": "add_comment", "provider": "github", "id": comment_id,
                "number": int(inputs["candidate_pr_number"]), "url": comment_url, "repo": repository,
                "target": {"provider": "github", "repository": repository,
                           "number": int(inputs["candidate_pr_number"])},
                "timestamp": preflight.composition.runtime.clock(),
            }
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr(zipfile.ZipInfo("safe-output-items.jsonl", (2026, 10, 9, 9, 0, 0)),
                                 json.dumps(item, sort_keys=True) + "\n")
            archive_bytes = stream.getvalue()
            artifact_id = run_id + 300
            artifact = {
                "id": artifact_id, "name": "safe-outputs-items", "expired": False,
                "size_in_bytes": len(archive_bytes), "digest": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
                "archive_download_url": f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip",
                "workflow_run": {"id": run_id, "head_sha": source_sha, "head_branch": "main",
                                "repository_id": 1326302284, "head_repository_id": 1326302284},
            }
            routes[f"/actions/runs/{run_id}/artifacts"] = {"total_count": 1, "artifacts": [artifact]}
            routes[f"/actions/artifacts/{artifact_id}/zip"] = archive_bytes
            return 204, {}, b""
        expect(method == "GET", "gate HTTP provider allowed an unapproved effect")
        if path in state["routes"]:
            return respond(deepcopy(state["routes"][path]))
        return fallback_http(method=method, url=url, token=token)

    transport = GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=repository, token="fixture", workflows=workflows,
            launch_poll_attempts=2, launch_poll_seconds=0),
        http=http, sleeper=lambda _: None)
    gateway = GhAwVerticalRoleDispatchGateway(transport=transport, workflows=workflows)
    from v03_dogfood_full_composition import DogfoodReviewerReplacementSource
    source = DogfoodReviewerReplacementSource(
        preflight.composition.recovery_result_source.config,
        target_repository=repository, http=http)
    source.bind_reviewer(preflight.composition.runtime, preflight.composition.policy_authority)
    return SimpleNamespace(transport=transport, dispatch_gateway=gateway,
                           result_source=source, state=state, http=http)




_FROZEN_GIT_RAW_FILES = {}


def frozen_git_raw_files(root, commit):
    """Cache only immutable Git bytes; every caller parses its own mutable state."""
    import subprocess
    key = (str(root.resolve()), commit)
    if key not in _FROZEN_GIT_RAW_FILES:
        listed = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", commit, "state/operator/v1",
             "config/operator/v03-vertical-policy"],
            cwd=root, check=True, capture_output=True, text=True).stdout.splitlines()
        rows = tuple((path, subprocess.run(
            ["git", "show", commit + ":" + path], cwd=root, check=True,
            capture_output=True).stdout) for path in listed if path.endswith(".json"))
        _FROZEN_GIT_RAW_FILES[key] = rows
    return dict(_FROZEN_GIT_RAW_FILES[key])


def reviewer_frozen_provider_fixture(archive_bytes):
    """Frozen thirty-event prefix and actual failed pre-model Reviewer observations.

    Reuses the independently captured successful Developer/closed-PR provider.
    All existing callback/Persist facts are historical input from one exact Store
    commit. This helper executes no replacement, callback or lifecycle transition.
    """
    import base64
    import hashlib
    import json
    import subprocess
    from copy import deepcopy
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import parse_qs, unquote, urlparse
    import yaml
    from operator_store_model import StoreSnapshot, operation_events
    from operator_vertical_store import vertical_projection

    provider = post_handoff_frozen_provider_fixture(archive_bytes)
    root = Path(__file__).resolve().parents[1]
    commit = "ac95a1cefdeb33dc836ae969168cd992c482401f"
    operation_id = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
    head = "41e0df7089c5907b00bbaeac5dd2be71d4f02d4b"
    repository = "dream-xin/ai-sdlc"
    target_ref = "dogfood/v0.3-happy-path-0001"
    manifest_path = "state/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001.yaml"
    pins = json.loads("{\"state/operator/v1/claims/dispatch/dc-3006ff66a16631b94316e4dac3d4711a4b4d2159.json\":\"1b93a7c81ae8be4bd4aef80a6c1c7c0641f40cc8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/authorization.json\":\"e68d45041c47083c2da5521aa325fcf58bef256b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/create-claim.json\":\"706888dddd1acc157cdbeb5b54ace6539125e16b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/post-handoff-reconciliation-1.json\":\"5178d7697c9b140c64c4dc2cf3fca94bf223c1ab\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/sealed-receipt.json\":\"7558b2b3ffe08bb255d3c287fffa49f5fc988f0e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/authorization.json\":\"d344fca61af21038c929897bc3fd636d4297ee17\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/create-attempt.json\":\"db183bc850c8e9add5abad38ced5728325192aba\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/transport-continuation.json\":\"e2b2edf5e50eedaacbdaee7ef1b0ae64c5f94580\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-candidate-handoffs/1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be/applied.json\":\"eb63e61ff00ae20bbe465d205c98924056e77827\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-candidate-handoffs/1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be/intent.json\":\"da06cd8849fff194e3cdbeb1df54d3efa69f850e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-prehttp-recovery-attempt.json\":\"e9bf99cc5fd8810a6fd08666ff4c17b136bee1b3\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000001-operation-started-739f4331137732d6184000cf3d8b4915.json\":\"86e43c43941b03e9721844b58479d95db93ce5c8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000002-loop-step-selected-2cf82f42399ec59c7283df1711819426.json\":\"f26de71400211607359fb60b58e77ddbab7216ed\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000003-dispatch-claimed-687520874a948a5c4534e4e30d97b366.json\":\"6741a203a62d31e81f1d619d705bb18feddc0173\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000004-dispatch-launch-authorized-5f6d063282780be1d174254ce8ef9134.json\":\"1840f7cac50722dad83cb0118e441e475175d859\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000005-dispatch-launch-lookup-recorded-8629bfae40264dc5d15df601bc67686d.json\":\"6f73721b7c8847ddae58e59bbee801d28cd9ef43\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000006-operation-superseded-972620170d72306fdfa27f564dbc68c4.json\":\"a11e8f98ab5a9511fe30cf22f0ba6fa0c41253d9\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000007-operation-generation-started-559d6292df44700862ae642429872527.json\":\"def317058e40c16a8b35c7390ab5847e678192c5\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000008-loop-step-selected-d13700446f0139fb96e5717dc101bbdf.json\":\"275a6134e086e95c72f7a8c0aa8940a9f35a67c8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000009-dispatch-claimed-f55e33e1ea7fd6b35eb44831e700d91f.json\":\"09cbb9201c82d1e69bcce5b0febc8c28f8948fac\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000010-dispatch-launch-authorized-8ea8faac43fb02dc1c3c8e481a40da93.json\":\"f6f793ce9725e618be0b7b25712e5abe44a55b59\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000011-dispatch-launch-lookup-recorded-ef426f7c675283149f805fdab65861ec.json\":\"ca4581772d9271f0e7d4dece22479a376504e8d5\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000012-dispatch-launch-lookup-recorded-cff7b708649dcf3b6ca354d3f72fbb6d.json\":\"96c43dcefda8558aa73750eb12a5e0d5af419d82\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000013-worker-callback-recorded-6e9bb8c4081e7ac28af2c5bccda2dcb4.json\":\"d773017efeec4ceccd65aaf28c1574c5aa6c9f69\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000014-worker-result-rejected-6e6b1ab67000b9207463c8676cad7be7.json\":\"93c3c64ea155b65bdbeaf78eed0067590aed9ede\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000015-loop-stable-stop-cd01bb348e233107a60b54928e336a00.json\":\"5a6969ba938f6153705d999065f786d4bb3159ec\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000016-worker-callback-recorded-8f4c403e06ae9355b0b245c9df740bf7.json\":\"069beb76d507b71109a1219722e03bf9bd4179f2\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000017-worker-result-validated-ed56d7eadf7efdb50d607eebb60c09e1.json\":\"54c9ddf16627d495c4ef06f80016fda173e12475\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000018-feature-event-translated-146e91f4ed5fa9420bdc79e69576a866.json\":\"b277131c21142e815de9825c9c7f02f5283bf19a\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000019-persist-requested-05b10f2f65d52ecbde4f92a6f48ad0a8.json\":\"03c6cb5628f5896c3dedcfddeb6abb0257b0886d\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000020-persist-linearized-9a302d406aac5e4a2b8aede284386a4c.json\":\"e8ea390b14f84173a2f2ba65003fb7fcb21d1faf\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000021-persist-confirmed-a92f43c724a6f37ad3eef0d7f217977e.json\":\"3f1dac41fef483899b0af3d1de6634e502497986\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000022-loop-step-selected-185d0cd557b4337a582cdcdc9ed7075c.json\":\"19c681ab77d90849e6b67aa53b8ccc3e271c16e6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000023-feature-event-translated-5bd0311cb465491d30a7027e617c4c4c.json\":\"09aedd1fa3bd0fb8b71ef6fd75390e2edc520db4\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000024-persist-requested-1e166d13887db2dee0b82e6a979b850e.json\":\"4dcf56f2ad6a941ccf5b6204cbb418d86f9be0b6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000025-persist-linearized-5681cee3f8344418644af863d69d5ce8.json\":\"1b807744b90d39564e05ce2f378e28fba6ece5b1\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000026-persist-confirmed-c1ee4698394d605b8ba50d7529227216.json\":\"1bd7b63e0b30882f1c304d1be16dad17f3b1dfbe\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000027-loop-step-selected-0c44caba02897e2237f92649007a66fa.json\":\"2400f03e80d353ba989b66a8609f780e955d5dff\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000028-dispatch-claimed-1d7f9416b07a819c38b27bf7535ccc18.json\":\"792494e66a1d31529993832e3cc8b0940ab58244\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000029-dispatch-launch-authorized-551bf29f723465460beea589087e5adf.json\":\"905d1b7f02ebb4a4b59342de885a5120ca2ff089\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000030-dispatch-launch-lookup-recorded-cd54b307a841588acaa8f4ccb197323f.json\":\"20ced53e567b946554fdaaa166f64167c4fe78c8\",\"state/operator/v1/reservations/external/ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25.json\":\"63fbc84909ecc9395c3113da1edb5a91f54c48bb\",\"state/operator/v1/reservations/external/ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25/external-create-attempt.json\":\"2b85c38cf3fa6fa1043205cc7b791828e71ec420\"}")
    pins["state/operator/v1/effect-lineages/members/lin-5aec6488d04a953a1d4f1619b4fb2ec503f53a69f98750e3/ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25.json"] = "1dfa9b783546f4a356cdf11a5a0c42513d9cda63"
    observed = json.loads("{\"run\":{\"id\":37917962742,\"run_attempt\":1,\"workflow_id\":372854672,\"path\":\".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek.lock.yml\",\"display_title\":\"AI-SDLC gh-aw dispatch-3d9202e579f855d0d36bdaaf1872dec1a2b84a28\",\"event\":\"workflow_dispatch\",\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\",\"status\":\"completed\",\"conclusion\":\"failure\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37917962742\",\"created_at\":\"2026-10-09T10:29:57Z\",\"updated_at\":\"2026-10-09T10:32:04Z\",\"run_started_at\":\"2026-10-09T10:29:57Z\",\"repository\":{\"full_name\":\"DREAM-XIN/ai-sdlc\"}},\"jobs\":{\"total_count\":5,\"jobs\":[{\"id\":113778697506,\"run_id\":37917962742,\"run_attempt\":1,\"name\":\"activation\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\",\"started_at\":\"2026-10-09T10:30:02Z\",\"completed_at\":\"2026-10-09T10:30:16Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T10:30:04Z\",\"completed_at\":\"2026-10-09T10:30:06Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T10:30:06Z\",\"completed_at\":\"2026-10-09T10:30:08Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T10:30:08Z\",\"completed_at\":\"2026-10-09T10:30:08Z\"},{\"name\":\"Generate agentic run info\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T10:30:08Z\",\"completed_at\":\"2026-10-09T10:30:08Z\"},{\"name\":\"Restore daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T10:30:08Z\",\"completed_at\":\"2026-10-09T10:30:08Z\"},{\"name\":\"Check daily workflow token guardrail\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T10:30:08Z\",\"completed_at\":\"2026-10-09T10:30:09Z\"},{\"name\":\"Publish daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T10:30:09Z\",\"completed_at\":\"2026-10-09T10:30:09Z\"},{\"name\":\"Check for OAuth tokens\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T10:30:09Z\",\"completed_at\":\"2026-10-09T10:30:09Z\"},{\"name\":\"Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T10:30:09Z\",\"completed_at\":\"2026-10-09T10:30:10Z\"},{\"name\":\"Save agent config folders for base branch restoration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T10:30:10Z\",\"completed_at\":\"2026-10-09T10:30:10Z\"},{\"name\":\"Check workflow lock file\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T10:30:10Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Check compile-agentic version\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Log runtime features\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Create prompt with built-in context\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Interpolate variables and render templates\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Substitute placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Validate prompt placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Print prompt\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:11Z\"},{\"name\":\"Upload info artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T10:30:11Z\",\"completed_at\":\"2026-10-09T10:30:12Z\"},{\"name\":\"Stage prompt files for artifact upload\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T10:30:12Z\",\"completed_at\":\"2026-10-09T10:30:12Z\"},{\"name\":\"Upload activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-09T10:30:12Z\",\"completed_at\":\"2026-10-09T10:30:13Z\"},{\"name\":\"Post Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-09T10:30:13Z\",\"completed_at\":\"2026-10-09T10:30:14Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-09T10:30:14Z\",\"completed_at\":\"2026-10-09T10:30:14Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-09T10:30:14Z\",\"completed_at\":\"2026-10-09T10:30:14Z\"}]},{\"id\":113778789435,\"run_id\":37917962742,\"run_attempt\":1,\"name\":\"agent\",\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\",\"started_at\":\"2026-10-09T10:30:18Z\",\"completed_at\":\"2026-10-09T10:30:33Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T10:30:19Z\",\"completed_at\":\"2026-10-09T10:30:22Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T10:30:22Z\",\"completed_at\":\"2026-10-09T10:30:24Z\"},{\"name\":\"Set runtime paths\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T10:30:24Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Check OTLP telemetry configuration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Generate GitHub App token for checkout (0)\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":6,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Checkout repository\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":7,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Checkout dream-xin/ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":8,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Build checkout manifest for safe-outputs handlers\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":9,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Initialize agent execution evidence\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":10,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Create gh-aw temp directory\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":11,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Configure gh CLI for GitHub Enterprise\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":12,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":14,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Checkout PR branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":15,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":16,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":17,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Generate GitHub App token\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":18,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Determine automatic lockdown mode for GitHub MCP Server\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":19,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Restore agent config folders from base branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":20,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Restore inline sub-agents from activation artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":21,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Restore inline skills from activation artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":22,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":23,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Prepare Safe Outputs Directories\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":24,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Generate Safe Outputs Config\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":25,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Generate Safe Outputs Tools\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":26,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Start MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":27,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Mount MCP servers as CLIs\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":28,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Clean credentials\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":29,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Audit pre-agent workspace\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":30,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Execute GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":31,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Detect agent errors\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":32,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":33,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Copy Copilot session state files to logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":34,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Stop MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":35,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Redact secrets in logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":36,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Append agent step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":37,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Copy Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":38,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:25Z\"},{\"name\":\"Ingest agent output\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":39,\"started_at\":\"2026-10-09T10:30:25Z\",\"completed_at\":\"2026-10-09T10:30:26Z\"},{\"name\":\"Parse agent logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":40,\"started_at\":\"2026-10-09T10:30:26Z\",\"completed_at\":\"2026-10-09T10:30:26Z\"},{\"name\":\"Parse MCP Gateway logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-09T10:30:26Z\",\"completed_at\":\"2026-10-09T10:30:26Z\"},{\"name\":\"Print firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-09T10:30:26Z\",\"completed_at\":\"2026-10-09T10:30:26Z\"},{\"name\":\"Parse token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-09T10:30:26Z\",\"completed_at\":\"2026-10-09T10:30:26Z\"},{\"name\":\"Print AWF reflect summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":44,\"started_at\":\"2026-10-09T10:30:26Z\",\"completed_at\":\"2026-10-09T10:30:26Z\"},{\"name\":\"Generate observability summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":45,\"started_at\":\"2026-10-09T10:30:26Z\",\"completed_at\":\"2026-10-09T10:30:27Z\"},{\"name\":\"Write agent output placeholder if missing\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":46,\"started_at\":\"2026-10-09T10:30:27Z\",\"completed_at\":\"2026-10-09T10:30:27Z\"},{\"name\":\"Upload agent output fallback artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":47,\"started_at\":\"2026-10-09T10:30:27Z\",\"completed_at\":\"2026-10-09T10:30:28Z\"},{\"name\":\"Upload agent artifacts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":48,\"started_at\":\"2026-10-09T10:30:28Z\",\"completed_at\":\"2026-10-09T10:30:29Z\"},{\"name\":\"Post Generate GitHub App token for checkout (0)\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":95,\"started_at\":\"2026-10-09T10:30:29Z\",\"completed_at\":\"2026-10-09T10:30:29Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":96,\"started_at\":\"2026-10-09T10:30:29Z\",\"completed_at\":\"2026-10-09T10:30:29Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":97,\"started_at\":\"2026-10-09T10:30:29Z\",\"completed_at\":\"2026-10-09T10:30:29Z\"}]},{\"id\":113778892780,\"run_id\":37917962742,\"run_attempt\":1,\"name\":\"detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\",\"started_at\":\"2026-10-09T10:30:35Z\",\"completed_at\":\"2026-10-09T10:31:06Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T10:30:36Z\",\"completed_at\":\"2026-10-09T10:30:39Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T10:30:39Z\",\"completed_at\":\"2026-10-09T10:30:40Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T10:30:40Z\",\"completed_at\":\"2026-10-09T10:30:41Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T10:30:41Z\",\"completed_at\":\"2026-10-09T10:30:42Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T10:30:42Z\",\"completed_at\":\"2026-10-09T10:30:42Z\"},{\"name\":\"Checkout repository for patch context\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":6,\"started_at\":\"2026-10-09T10:30:42Z\",\"completed_at\":\"2026-10-09T10:30:42Z\"},{\"name\":\"Initialize detection execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T10:30:42Z\",\"completed_at\":\"2026-10-09T10:30:42Z\"},{\"name\":\"Clear inherited Copilot session state\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T10:30:42Z\",\"completed_at\":\"2026-10-09T10:30:42Z\"},{\"name\":\"Clean stale firewall files from agent artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T10:30:42Z\",\"completed_at\":\"2026-10-09T10:30:42Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T10:30:42Z\",\"completed_at\":\"2026-10-09T10:30:53Z\"},{\"name\":\"Check if detection needed\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T10:30:53Z\",\"completed_at\":\"2026-10-09T10:30:53Z\"},{\"name\":\"Clear MCP Config for detection\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":12,\"started_at\":\"2026-10-09T10:30:53Z\",\"completed_at\":\"2026-10-09T10:30:53Z\"},{\"name\":\"Prepare threat detection files\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T10:30:53Z\",\"completed_at\":\"2026-10-09T10:30:53Z\"},{\"name\":\"Setup threat detection\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":14,\"started_at\":\"2026-10-09T10:30:53Z\",\"completed_at\":\"2026-10-09T10:30:53Z\"},{\"name\":\"Ensure threat-detection directory and log\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":15,\"started_at\":\"2026-10-09T10:30:53Z\",\"completed_at\":\"2026-10-09T10:30:53Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T10:30:53Z\",\"completed_at\":\"2026-10-09T10:30:54Z\"},{\"name\":\"Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T10:30:54Z\",\"completed_at\":\"2026-10-09T10:30:54Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T10:30:54Z\",\"completed_at\":\"2026-10-09T10:31:03Z\"},{\"name\":\"Install threat-detect binary\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":19,\"started_at\":\"2026-10-09T10:31:03Z\",\"completed_at\":\"2026-10-09T10:31:03Z\"},{\"name\":\"Execute threat detection with AWF\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":20,\"started_at\":\"2026-10-09T10:31:03Z\",\"completed_at\":\"2026-10-09T10:31:03Z\"},{\"name\":\"Render detection log\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":21,\"started_at\":\"2026-10-09T10:31:03Z\",\"completed_at\":\"2026-10-09T10:31:03Z\"},{\"name\":\"Copy detection firewall logs\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":22,\"started_at\":\"2026-10-09T10:31:03Z\",\"completed_at\":\"2026-10-09T10:31:03Z\"},{\"name\":\"Parse threat detection token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-09T10:31:03Z\",\"completed_at\":\"2026-10-09T10:31:03Z\"},{\"name\":\"Upload threat detection artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-09T10:31:03Z\",\"completed_at\":\"2026-10-09T10:31:04Z\"},{\"name\":\"Conclude threat detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":25,\"started_at\":\"2026-10-09T10:31:04Z\",\"completed_at\":\"2026-10-09T10:31:04Z\"},{\"name\":\"Post Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":49,\"started_at\":\"2026-10-09T10:31:04Z\",\"completed_at\":\"2026-10-09T10:31:04Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":50,\"started_at\":\"2026-10-09T10:31:04Z\",\"completed_at\":\"2026-10-09T10:31:04Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":51,\"started_at\":\"2026-10-09T10:31:04Z\",\"completed_at\":\"2026-10-09T10:31:04Z\"}]},{\"id\":113779083838,\"run_id\":37917962742,\"run_attempt\":1,\"name\":\"safe_outputs\",\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\",\"started_at\":\"2026-10-09T10:31:12Z\",\"completed_at\":\"2026-10-09T10:31:40Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T10:31:13Z\",\"completed_at\":\"2026-10-09T10:31:20Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T10:31:20Z\",\"completed_at\":\"2026-10-09T10:31:34Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T10:31:34Z\",\"completed_at\":\"2026-10-09T10:31:34Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T10:31:34Z\",\"completed_at\":\"2026-10-09T10:31:36Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T10:31:36Z\",\"completed_at\":\"2026-10-09T10:31:36Z\"},{\"name\":\"Generate GitHub App token\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":6,\"started_at\":\"2026-10-09T10:31:36Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"},{\"name\":\"Configure GH_HOST for enterprise compatibility\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":7,\"started_at\":\"2026-10-09T10:31:37Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"},{\"name\":\"Process Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":8,\"started_at\":\"2026-10-09T10:31:37Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"},{\"name\":\"Upload Safe Outputs Items\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T10:31:37Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"},{\"name\":\"Post Generate GitHub App token\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T10:31:37Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T10:31:37Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T10:31:37Z\",\"completed_at\":\"2026-10-09T10:31:37Z\"}]},{\"id\":113779278658,\"run_id\":37917962742,\"run_attempt\":1,\"name\":\"conclusion\",\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\",\"started_at\":\"2026-10-09T10:31:43Z\",\"completed_at\":\"2026-10-09T10:32:03Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T10:31:45Z\",\"completed_at\":\"2026-10-09T10:31:50Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T10:31:50Z\",\"completed_at\":\"2026-10-09T10:31:58Z\"},{\"name\":\"Dispatch non-authoritative Gate-role recommendation to trusted collector\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":3,\"started_at\":\"2026-10-09T10:31:58Z\",\"completed_at\":\"2026-10-09T10:31:58Z\"},{\"name\":\"Generate GitHub App token\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":4,\"started_at\":\"2026-10-09T10:31:58Z\",\"completed_at\":\"2026-10-09T10:31:58Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":5,\"started_at\":\"2026-10-09T10:31:58Z\",\"completed_at\":\"2026-10-09T10:31:58Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":6,\"started_at\":\"2026-10-09T10:31:58Z\",\"completed_at\":\"2026-10-09T10:31:58Z\"},{\"name\":\"Download detection artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":7,\"started_at\":\"2026-10-09T10:31:58Z\",\"completed_at\":\"2026-10-09T10:31:58Z\"},{\"name\":\"Download Safe Outputs Items Manifest\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T10:31:58Z\",\"completed_at\":\"2026-10-09T10:31:59Z\"},{\"name\":\"Collect usage artifact files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T10:31:59Z\",\"completed_at\":\"2026-10-09T10:31:59Z\"},{\"name\":\"Upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T10:31:59Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Wait before retrying usage artifact upload\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":11,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Retry upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":12,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Process no-op messages\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Log detection run\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":14,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Record missing tool\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":15,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Record incomplete\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":16,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:00Z\"},{\"name\":\"Handle agent failure\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":17,\"started_at\":\"2026-10-09T10:32:00Z\",\"completed_at\":\"2026-10-09T10:32:01Z\"},{\"name\":\"Report failed jobs\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":18,\"started_at\":\"2026-10-09T10:32:01Z\",\"completed_at\":\"2026-10-09T10:32:01Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":36,\"started_at\":\"2026-10-09T10:32:01Z\",\"completed_at\":\"2026-10-09T10:32:01Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":37,\"started_at\":\"2026-10-09T10:32:01Z\",\"completed_at\":\"2026-10-09T10:32:01Z\"}]}]},\"artifacts\":{\"total_count\":6,\"artifacts\":[{\"id\":11611241425,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxMTI0MTQyNQ==\",\"name\":\"activation\",\"size_in_bytes\":1025232,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11611241425\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11611241425/zip\",\"expired\":false,\"digest\":\"sha256:7c05d67c30fd7965159402a9765133adf7128a6bc4b18ec9d380d639597965e5\",\"created_at\":\"2026-10-09T10:30:13Z\",\"updated_at\":\"2026-10-09T10:30:13Z\",\"expires_at\":\"2026-10-10T10:30:12Z\",\"workflow_run\":{\"id\":37917962742,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\"}},{\"id\":11610406910,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxMDQwNjkxMA==\",\"name\":\"detection\",\"size_in_bytes\":570,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610406910\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610406910/zip\",\"expired\":false,\"digest\":\"sha256:ee87864d23b65c8d8e365bb40fa83862224bd7f8006e9a294b624c060aa433a5\",\"created_at\":\"2026-10-09T10:31:04Z\",\"updated_at\":\"2026-10-09T10:31:04Z\",\"expires_at\":\"2027-01-07T10:30:00Z\",\"workflow_run\":{\"id\":37917962742,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\"}},{\"id\":11610246782,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxMDI0Njc4Mg==\",\"name\":\"info\",\"size_in_bytes\":622,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610246782\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610246782/zip\",\"expired\":false,\"digest\":\"sha256:4779b36bcc5bb37f9c02a0a4456bbd75f217f3e9af0ab4daee6b90af4da028f7\",\"created_at\":\"2026-10-09T10:30:12Z\",\"updated_at\":\"2026-10-09T10:30:12Z\",\"expires_at\":\"2027-01-07T10:30:00Z\",\"workflow_run\":{\"id\":37917962742,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\"}},{\"id\":11610121887,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxMDEyMTg4Nw==\",\"name\":\"agent-output-fallback\",\"size_in_bytes\":170,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610121887\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610121887/zip\",\"expired\":false,\"digest\":\"sha256:35f7a8ac45d6504ac9c5304e712244c40c6546f644b1c6ee7a854ebc44bc7cac\",\"created_at\":\"2026-10-09T10:30:28Z\",\"updated_at\":\"2026-10-09T10:30:28Z\",\"expires_at\":\"2027-01-07T10:30:00Z\",\"workflow_run\":{\"id\":37917962742,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\"}},{\"id\":11610087162,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxMDA4NzE2Mg==\",\"name\":\"usage\",\"size_in_bytes\":445,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610087162\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610087162/zip\",\"expired\":false,\"digest\":\"sha256:a58fa2cc17fe2ceee180d9308afce2336891b2215413cd7826af4bfdcecfdd08\",\"created_at\":\"2026-10-09T10:32:00Z\",\"updated_at\":\"2026-10-09T10:32:00Z\",\"expires_at\":\"2027-01-07T10:30:00Z\",\"workflow_run\":{\"id\":37917962742,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\"}},{\"id\":11610061858,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxMDA2MTg1OA==\",\"name\":\"agent\",\"size_in_bytes\":1372,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610061858\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11610061858/zip\",\"expired\":false,\"digest\":\"sha256:c96cfcde2ae9dc34583d2ce975ede2c19ebd20317db1d068f2ff9b13ca0bf1ee\",\"created_at\":\"2026-10-09T10:30:29Z\",\"updated_at\":\"2026-10-09T10:30:29Z\",\"expires_at\":\"2027-01-07T10:30:00Z\",\"workflow_run\":{\"id\":37917962742,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"bd9228219310a8202bf47311e6adb8ea36d598bf\"}}]},\"comments\":[]}")
    feature_manifest_text = "protocol_version: 0.1.0\nrevision: 3\nfeature:\n  id: F-OPERATOR-V03-DOGFOOD-HAPPY-0001\n  title: 'v0.3 release dogfood: happy_path'\n  risk: low\n  issue: '#342'\nworkflow:\n  profile: v03-release-dogfood\n  status: ACTIVE\n  current_stage: code-review\n  stages:\n  - id: implementation\n    status: DONE\n  - id: code-review\n    status: WORKING\n    gate: code-gate\n  - id: verification\n    status: TODO\n    gate: verification-gate\n  - id: acceptance\n    status: TODO\n    gate: release-gate\ntasks: []\nartifacts:\n- id: dogfood-scenario-task\n  type: dogfood-task\n  uri: docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\n  status: draft\n- id: vertical-artifact-3ac60f9b63c79ca4168c\n  status: draft\n  type: implementation\n  uri: docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/worker-runs/vertical-31df3f1ed41b54c58ed4c4030a9f97d9/developer-pr-577-a7b208a49668fb9ae16de908a52418106c616819-binding-52d3fc01396e496b5930f8bbb95ded34b3506e457e5ec867de2d8298c8e6a91c--first-attempt--key-dispatch-86e969e947932b9dd38c608ca80333714c3396d2--run-37905505035--head-6e75792b8e441167cfaadab2d13667a2d80721b8--lease-008fa9d6a8dc823366cf1d367b048cae6e26bfa3db8c18a00448daf1793679e4.json\ngates:\n- id: code-gate\n  status: PENDING\n- id: verification-gate\n  status: PENDING\n- id: release-gate\n  status: PENDING\nevidence: []\napplied_events:\n- EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-IMPLEMENTATION-START\n- EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-IMPLEMENTATION-DONE-454A443721B6\n- EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-CODE-REVIEW-START-47FA4BA969BD\nupdated_at: '2026-10-09T10:24:20Z'\n"
    feature_manifest_blob = "b4191ea1f9d83ee78ec80655d6332e44960a0b3f"
    feature_documents = json.loads("{\"state/events/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-CODE-REVIEW-START-47FA4BA969BD.yaml\":{\"text\":\"changes:\\n- id: code-review\\n  kind: stage\\n  status: WORKING\\nexpected_revision: 2\\nfeature_id: F-OPERATOR-V03-DOGFOOD-HAPPY-0001\\nid: EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-CODE-REVIEW-START-47FA4BA969BD\\noccurred_at: '2026-10-09T10:24:20Z'\\nversion: 0.1.0\\n\",\"sha\":\"56ef88ef4f05ac2d8d95b7260f3b947019c3f508\"},\"state/events/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-IMPLEMENTATION-DONE-454A443721B6.yaml\":{\"text\":\"changes:\\n- kind: artifact-record\\n  record:\\n    id: vertical-artifact-3ac60f9b63c79ca4168c\\n    status: draft\\n    type: implementation\\n    uri: docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/worker-runs/vertical-31df3f1ed41b54c58ed4c4030a9f97d9/developer-pr-577-a7b208a49668fb9ae16de908a52418106c616819-binding-52d3fc01396e496b5930f8bbb95ded34b3506e457e5ec867de2d8298c8e6a91c--first-attempt--key-dispatch-86e969e947932b9dd38c608ca80333714c3396d2--run-37905505035--head-6e75792b8e441167cfaadab2d13667a2d80721b8--lease-008fa9d6a8dc823366cf1d367b048cae6e26bfa3db8c18a00448daf1793679e4.json\\n- id: implementation\\n  kind: stage\\n  status: DONE\\n- id: code-review\\n  kind: stage\\n  status: READY\\nexpected_revision: 1\\nfeature_id: F-OPERATOR-V03-DOGFOOD-HAPPY-0001\\nid: EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-IMPLEMENTATION-DONE-454A443721B6\\noccurred_at: '2026-10-09T10:20:40Z'\\nversion: 0.1.0\\n\",\"sha\":\"261fe74650e30e39402d90b5d6450f66d319e446\"},\"state/events/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-IMPLEMENTATION-START.yaml\":{\"text\":\"version: 0.1.0\\nid: EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-IMPLEMENTATION-START\\nfeature_id: F-OPERATOR-V03-DOGFOOD-HAPPY-0001\\nexpected_revision: 0\\noccurred_at: '2026-08-25T07:21:01Z'\\nchanges:\\n- kind: artifact-record\\n  record:\\n    id: dogfood-scenario-task\\n    type: dogfood-task\\n    uri: docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\\n    status: draft\\n- kind: stage\\n  id: implementation\\n  status: WORKING\\n\",\"sha\":\"bf9b49488887bb1f1f8c4433ff5f964557da6f69\"}}")
    historical_commits = json.loads("[{\"sha\":\"f9b5bbbec9a18ac64c2c6cb7bc2780c168d71397\",\"parent\":\"a7b208a49668fb9ae16de908a52418106c616819\",\"files\":[{\"filename\":\"state/events/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-IMPLEMENTATION-DONE-454A443721B6.yaml\",\"sha\":\"261fe74650e30e39402d90b5d6450f66d319e446\",\"status\":\"added\"}]},{\"sha\":\"b36fbee5bb4397249ce1382b90f45dcd33a6265d\",\"parent\":\"f9b5bbbec9a18ac64c2c6cb7bc2780c168d71397\",\"files\":[{\"filename\":\"state/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001.yaml\",\"sha\":\"f9950d13efce0bcf1ffd53a9b1e8d13cc1f89aa3\",\"status\":\"modified\"}]},{\"sha\":\"70c666436d711f3333687a1fe765a7dcb8a504b6\",\"parent\":\"b36fbee5bb4397249ce1382b90f45dcd33a6265d\",\"files\":[{\"filename\":\"state/events/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/EVT-F-OPERATOR-V03-DOGFOOD-HAPPY-0001-VERTICAL-CODE-REVIEW-START-47FA4BA969BD.yaml\",\"sha\":\"56ef88ef4f05ac2d8d95b7260f3b947019c3f508\",\"status\":\"added\"}]},{\"sha\":\"41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\",\"parent\":\"70c666436d711f3333687a1fe765a7dcb8a504b6\",\"files\":[{\"filename\":\"state/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001.yaml\",\"sha\":\"b4191ea1f9d83ee78ec80655d6332e44960a0b3f\",\"status\":\"modified\"}]}]")

    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()

    raw_files = frozen_git_raw_files(root, commit)
    expect(all(path in raw_files and blob(raw_files[path]) == sha for path, sha in pins.items()),
           "Reviewer fixture changed its exact historical Store/sidecar documents")
    snapshot = StoreSnapshot(commit, {path: json.loads(raw) for path, raw in raw_files.items()})
    events = operation_events(snapshot, operation_id)
    projection = vertical_projection(snapshot, operation_id)
    expect(len(events) == 30 and projection["generation"] == 1
           and projection["status"] == "WAITING_EXTERNAL" and projection["expected_feature_revision"] == 3,
           "Reviewer fixture is not the frozen thirty-event code-review wait")
    expect(events[-1]["event_type"] == "dispatch.launch.lookup-recorded"
           and events[-1]["payload"]["lookup_state"] == "LAUNCHED"
           and str(events[-1]["payload"]["receipt_id"]) == "37917962742"
           and events[-1]["payload"]["external_dispatch_key"]
               == "dispatch-3d9202e579f855d0d36bdaaf1872dec1a2b84a28",
           "Reviewer fixture changed its original logical launch receipt")
    expect(blob(feature_manifest_text.encode()) == feature_manifest_blob,
           "Reviewer fixture manifest bytes changed")
    manifest = yaml.safe_load(feature_manifest_text)
    expect(manifest["revision"] == 3 and manifest["workflow"]["current_stage"] == "code-review"
           and next(row for row in manifest["workflow"]["stages"] if row["id"] == "code-review")["status"] == "WORKING",
           "Reviewer fixture must begin at actual revision-three code-review state")
    feature_event_files = {}
    for path, document in feature_documents.items():
        raw = document["text"].encode()
        expect(blob(raw) == document["sha"], "historical Feature Event bytes changed")
        event = yaml.safe_load(raw)
        expect(event["id"] in manifest["applied_events"],
               "historical Event is not in frozen Feature applied-events")
        feature_event_files[path] = raw

    provider.snapshot = snapshot
    state = provider.state
    state["head"] = head
    state["reviewer_observed"] = observed
    state["reviewer_predecessor_commit"] = commit
    state["reviewer_frozen_events"] = deepcopy(events)
    for row in historical_commits:
        expect(len(row["files"]) == 1, "frozen Persist commit changed more than its one expected file")
        state["parents"][row["sha"]] = row["parent"]
        state["changes"][row["sha"]] = deepcopy(row["files"][0])
    old_http = provider.http

    def response(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def paginate(rows, query):
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["100"])[0])
        expect(page > 0 and 1 <= per_page <= 100, "fake provider received malformed pagination")
        return deepcopy(rows[(page - 1) * per_page:page * per_page])

    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/" + repository
        expect(parsed.scheme == "https" and parsed.netloc == "api.github.com"
               and parsed.path.lower().startswith(prefix + "/"),
               "Reviewer fixture escaped exact GitHub repository")
        path = unquote(parsed.path[len(prefix):])
        query = parse_qs(parsed.query)
        expect(method == "GET", "frozen Reviewer provider attempted a new external effect: " + method)
        old_run = state["reviewer_observed"]["run"]
        old_id = int(old_run["id"])
        if path in {f"/actions/runs/{old_id}", f"/actions/runs/{old_id}/attempts/1"}:
            state["calls"].append((method, path))
            return response(deepcopy(old_run))
        if path in {f"/actions/runs/{old_id}/jobs", f"/actions/runs/{old_id}/attempts/1/jobs"}:
            state["calls"].append((method, path))
            payload = state["reviewer_observed"]["jobs"]
            return response({"total_count": payload["total_count"], "jobs": paginate(payload["jobs"], query)})
        if path == f"/actions/runs/{old_id}/artifacts":
            state["calls"].append((method, path))
            payload = state["reviewer_observed"]["artifacts"]
            return response({"total_count": payload["total_count"], "artifacts": paginate(payload["artifacts"], query)})
        if path == "/actions/jobs/113778789435/logs":
            return 200, {}, b"2026-10-09T10:30:25.1467564Z Error: The 'private-key' input must be set to a non-empty string. If using a secret or variable, ensure it is available in this workflow context.\\n"
        if path.startswith("/actions/workflows/") and path.endswith("/runs"):
            state["calls"].append((method, path))
            workflow = path[len("/actions/workflows/"):-len("/runs")]
            rows = [row for row in (old_run, state["observed"]["run"])
                    if workflow in {row["path"].rsplit("/", 1)[-1], str(row["workflow_id"])}]
            return response({"total_count": len(rows), "workflow_runs": paginate(rows, query)})
        if path == "/actions/runs":
            state["calls"].append((method, path))
            rows = [old_run, state["observed"]["run"]]
            return response({"total_count": len(rows), "workflow_runs": paginate(rows, query)})
        if path == "/issues/552/comments":
            state["calls"].append((method, path))
            return response(paginate(state["reviewer_observed"]["comments"], query))
        if path.startswith("/contents/"):
            content_path = path[len("/contents/"):]
            ref = query.get("ref", [None])[0]
            if ref == head and (content_path == manifest_path or content_path in feature_event_files):
                state["calls"].append((method, path))
                raw = feature_manifest_text.encode() if content_path == manifest_path else feature_event_files[content_path]
                return response({"type": "file", "path": content_path, "name": content_path.rsplit("/", 1)[-1],
                    "encoding": "base64", "content": base64.b64encode(raw).decode(),
                    "sha": blob(raw), "size": len(raw)})
        return old_http(method=method, url=url, token=token, body=body)

    provider.http = http
    provider.feature_manifest_text = feature_manifest_text
    provider.feature_event_files = feature_event_files
    provider.feature_manifest_blob = feature_manifest_blob
    provider.frozen_events = deepcopy(events)
    provider.historical_reviewer_run = deepcopy(observed["run"])
    provider.historical_reviewer_jobs = deepcopy(observed["jobs"])
    return provider


def build_reviewer_frozen_feature_fixture(preflight, candidate_provider, provider):
    """Initialize only the factual revision-three prefix before new transitions.

    The existing fake-HTTP Feature fixture retains its real reducer and real
    Event/Persist gateways. All replacement Reviewer/QA Events, revision changes,
    confirmation facts, DONE and Notifications must be produced after this call.
    """
    from copy import deepcopy
    import yaml
    fixture = build_post_handoff_feature_fixture(
        preflight, candidate_provider, read_ref=provider.read_ref, advance_ref=provider.advance_ref)
    expect(not fixture.state["calls"] and fixture.state["puts"] == 0 and fixture.state["applied"] == 0
           and fixture.state["pending"] is None and not fixture.state["commits"],
           "historical Feature initialization occurred after execution began")
    manifest = yaml.safe_load(provider.feature_manifest_text)
    expect(manifest["revision"] == 3 and manifest["workflow"]["current_stage"] == "code-review",
           "Reviewer fixture cannot reset or advance the historical Feature")
    fixture.state["manifest"] = deepcopy(manifest)
    fixture.state["manifest_text"] = provider.feature_manifest_text
    fixture.state["files"] = deepcopy(provider.feature_event_files)
    fixture.state["historical_revision"] = 3
    fixture.state["historical_applied_event_ids"] = tuple(manifest["applied_events"])
    return fixture


def reviewer_runtime_fixture():
    import base64
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_store_model import StoreSnapshot, operation_events
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_effect_rollout import ProtectedEffectLineageRolloutVerifier, EffectLineageWriteFence
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
    from v03_dogfood_fixture_pool import require_slot
    import v03_dogfood_full_composition as composition
    provider = reviewer_frozen_provider_fixture(base64.b64decode(POST_HANDOFF_ARCHIVE_B64, validate=True))
    import subprocess
    from pathlib import Path
    files = provider.snapshot.files
    for name in ("effect-lineage-rollout.json", "writer-fence-receipt.json", "effect-resolution-policy.json", "decision-policy.json"):
        path = "config/operator/v03-vertical-policy/" + name
        files[path] = json.loads(subprocess.run(["git", "show", composition.REVIEWER_PREDECESSOR_STORE + ":" + path],
            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True).stdout)
    policy_path = "config/operator/v03-vertical-policy/"
    rollout_verifier = ProtectedEffectLineageRolloutVerifier(
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-lineage-rollout.json"]),
        writer_fence_receipt_loader=lambda *_: deepcopy(files[policy_path + "writer-fence-receipt.json"]))
    rollout = rollout_verifier.verify(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE)
    resolution = ProtectedEffectResolutionPolicyVerifier(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-resolution-policy.json"]),
        evidence_fact_loader=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected resolution evidence")))
    resolution.verify_current()
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
                             snapshot=deepcopy(provider.snapshot))
            self.commit_count = 0
            self.fail_confirmation_once = False
        def commit(self, plan, receipt):
            if self.fail_confirmation_once and any(isinstance(m.value, dict)
                    and m.value.get("event_type") == "persist.confirmed" for m in plan.mutations):
                self.fail_confirmation_once = False
                raise OSError("fixture crash before protected Persist confirmation")
            result = super().commit(plan, receipt)
            self.commit_count += 1
            self.snapshot = StoreSnapshot(f"{self.commit_count:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    runtime = OperatorStoreRuntime(backend=Backend(), protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout), clock=lambda: "2026-10-09T09:10:00Z")
    from v03_dogfood_live_gate import resolve_current_dogfood_bindings
    from v03_dogfood_runtime_preflight import _workflow_map, _execution_bindings
    gate = SimpleNamespace(scenario="happy_path", bindings=resolve_current_dogfood_bindings({"DEEPSEEK_API_KEY": True}))
    workflows = _workflow_map(gate)
    policy = recovery_policy_fixture()
    provider.state["controller_source"] = policy.installation_commit_sha
    source = composition.RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(control_repository="dream-xin/ai-sdlc",
            control_token="fixture", target_token="fixture", workflows=workflows,
            collector_identity=composition.COLLECTOR_IDENTITY),
        target_repository="dream-xin/ai-sdlc", http=provider.http)
    pf = SimpleNamespace(slot=require_slot("happy_path"), workflows=workflows,
        candidate_pr_number=552, candidate_head_sha=composition.REVIEWER_CANDIDATE,
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha=policy.installation_commit_sha),
        trusted_context_digest="6" * 64,
        composition=SimpleNamespace(runtime=runtime, policy_authority=policy, recovery_result_source=source))
    def get_json(url, headers):
        status, _, raw = provider.http(method="GET", url=url, token="fixture")
        return status, json.loads(raw)
    candidate = composition.DogfoodGitHubCandidateProvider(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", http_get=get_json)
    candidate.bind_runtime(runtime)
    feature = build_reviewer_frozen_feature_fixture(pf, candidate, provider)
    candidate.persist_gateway = feature.persist_gateway
    pf.historical_gate_runs = [provider.state["reviewer_observed"]["run"], provider.state["observed"]["run"]]
    gates = build_selected_dogfood_gate_fixture(pf, read_ref=provider.read_ref, fallback_http=provider.http)
    bindings = _execution_bindings(gate, workflows)
    dispatch = composition.DogfoodExecutionBoundDispatchGateway(
        delegate=gates.dispatch_gateway, execution_bindings=bindings)
    one_shot = StoreBackedOneShotExternalCreateGateway(runtime=runtime, delegate=dispatch,
        trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True)
    loader = composition.DogfoodRecoveryBoundContentLoader(
        result_source=gates.result_source, recovery_result_source=source, policy_authority=policy)
    loader.bind_runtime(runtime)
    source.bind_post_handoff(runtime, policy)
    base = TrustedVerticalExecutor(runtime=runtime, feature_gateway=feature.feature_gateway,
        persist_gateway=feature.persist_gateway, dispatch_gateway=one_shot,
        config=TrustedVerticalExecutorConfig(target_ref=pf.slot.target_ref,
            trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True,
            old_writers_quiesced=True, rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest, max_auto_steps=64),
        resolution_policy_verifier=resolution)
    from pathlib import Path
    from operator_production_runtime import TrustedOperatorRuntimeConfig, TrustedFeatureBinding
    from operator_decision_policy import ProtectedDecisionPolicyVerifier
    from validate_v03_dogfood_runtime_composition import assemble_post_handoff_responses_graph, assert_post_handoff_authority_graph
    config = TrustedOperatorRuntimeConfig(target_repository=pf.execution.repository,
        store_repository=pf.execution.repository, installation_ref="main", store_checkout=Path("."),
        principal="post-handoff-fixture",
        feature_bindings=(TrustedFeatureBinding(pf.slot.feature_id, pf.slot.target_ref),))
    decision = ProtectedDecisionPolicyVerifier(repository=config.store_repository, state_ref=config.state_ref,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "decision-policy.json"]))
    def reader_get(url, headers):
        if "/contents/state/features/" in url:
            return feature.http("GET", url.replace("https://api.github.com", "https://api.github.test"), headers, None)
        return get_json(url, headers)
    responses, graph_before = assemble_post_handoff_responses_graph(
        runtime=runtime, base_executor=base, content_loader=loader, slot=pf.slot, config=config,
        policy_authority=policy, decision_policy_verifier=decision,
        trusted_role_policy="fixture-independent-role-policy", collector_namespace_policy="fixture-collector-namespace",
        reader_http_get=reader_get)
    executor = responses.operator_bundle.executor
    delegate = responses.operator_bundle.callback_coordinator
    predecessor_events = deepcopy(operation_events(runtime.backend.read_snapshot(), composition.RECOVERY_OPERATION_ID))
    assert_post_handoff_authority_graph(graph_before, responses, policy, predecessor_events=predecessor_events[:15])
    def forbidden_handoff_http(*args, **kwargs):
        raise AssertionError("post-handoff reconciliation attempted another fixture PATCH")
    handoff = composition.DogfoodCandidateHandoff(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", candidate_provider=candidate, http_request=forbidden_handoff_http)
    handoff.content_loader = loader
    coordinator = composition.DogfoodTrustedCallbackCoordinator(delegate=delegate, candidate_handoff=handoff)
    collector = composition.DogfoodReviewerReplacementCollector(policy_authority=policy,callback_coordinator=coordinator,
        result_source=gates.result_source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock)
    recovery_collector = composition.DogfoodRecoveryCollector(callback_coordinator=coordinator,
        result_source=source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock, policy_authority=policy)
    pf.composition.__dict__.update(candidate_provider=candidate, feature_event_gateway=feature.event_gateway,
        result_source=gates.result_source, collector=collector, recovery_collector=recovery_collector,
        actions_transport=gates.transport, dispatch_gateway=dispatch, bundle=responses.operator_bundle,
        responses=responses, graph_before=graph_before, predecessor_events=predecessor_events,
        callback_coordinator=coordinator)
    return pf, provider, feature, gates, coordinator




def finish_reviewer_replacement_pipeline_tests(preflight, *, gate_fixture, feature_fixture,
                                       read_ref, effect_counts, adapter):
    """Continue real scenario collection through Reviewer/QA and real finalizer.

    Call immediately after atomic reconciliation, before processing its new callback.
    The actual Responses host/adapter/start backend must invoke the bound recovering
    executor. Provider routes alone are fake; all claims, callback facts, translation,
    Persist and DONE are production.
    """
    import json
    from copy import deepcopy
    from dataclasses import is_dataclass, replace
    from types import SimpleNamespace
    from unittest.mock import patch
    import v03_dogfood_runtime_driver as driver
    import v03_dogfood_scenario_runner as runner
    import v03_dogfood_post_run_finalizer as finalizer
    import v03_dogfood_production_provenance as provenance
    from operator_store_model import operation_events, digest_json
    from operator_vertical import VerticalInvariantError
    from operator_vertical_store import vertical_projection

    h = driver.HISTORICAL_PREHTTP_RECOVERY
    runtime = preflight.composition.runtime
    operation_id = h["operation_id"]
    frozen_prefix = deepcopy(operation_events(runtime.backend.read_snapshot(), operation_id)[:30])
    before_effects = dict(effect_counts())
    first_host = build_post_handoff_responses_host(
        preflight, adapter=adapter, expected_revision=feature_fixture.state["manifest"]["revision"],
        session_label="first-observation")
    crash_expected = runtime.backend.fail_confirmation_once
    try:
        first_trace, first_operation, first_status = runner._resume_post_handoff(preflight, first_host.host)
    except OSError as exc:
        expect(crash_expected and str(exc) == "fixture crash before protected Persist confirmation",
               "unexpected error escaped real resume")
        interrupted = operation_events(runtime.backend.read_snapshot(), operation_id)
        expect(feature_fixture.state["applied"] == 1
               and any(e["event_type"] == "persist.linearized" for e in interrupted)
               and not any(e["event_type"] == "persist.confirmed" for e in interrupted)
               and not gate_fixture.state["inputs"],
               "crash fixture did not stop after real provider apply and before Store confirmation")
        first_host = build_post_handoff_responses_host(
            preflight, adapter=adapter, expected_revision=feature_fixture.state["manifest"]["revision"],
            session_label="after-confirmation-crash")
        first_trace, first_operation, first_status = runner._resume_post_handoff(preflight, first_host.host)
    else:
        expect(not crash_expected, "requested protected confirmation crash was not exercised")
    expect(first_operation == operation_id and first_status == "WAITING_EXTERNAL"
           and len(first_host.requests) == 2
           and feature_fixture.state["manifest"]["revision"] > 1
           and [row["role"] for row in gate_fixture.state["inputs"]] == ["reviewer"],
           "first actual host did not persist Developer and stop at one Reviewer")
    progressed_prefix = deepcopy(operation_events(runtime.backend.read_snapshot(), operation_id))
    progressed_puts = feature_fixture.state["puts"]
    host_fixture = build_post_handoff_responses_host(
        preflight, adapter=adapter, expected_revision=feature_fixture.state["manifest"]["revision"],
        session_label="fresh-post-persist")

    scenario = runner.run_scenario(preflight=preflight, host=host_fixture.host)
    expect(len(host_fixture.requests) == 2
           and scenario.operation_id == operation_id
           and scenario.worker_results_consumed == 3
           and scenario.function_call_ids == (host_fixture.call_id,)
           and scenario.dispatch_roles == ("developer", "reviewer", "qa"),
           "actual host/status/resume/scenario entry failed to consume the reconciled prefix exactly once")
    consumed = scenario.worker_results_consumed
    assert_post_handoff_done_replay(preflight, adapter=adapter, feature_fixture=feature_fixture,
        gate_fixture=gate_fixture, effect_counts=effect_counts)
    projection = vertical_projection(runtime.backend.read_snapshot(), operation_id)
    expect(projection["status"] == "DONE" and consumed == 3,
           "actual Reviewer/QA callback and lifecycle paths did not finish DONE")
    runner._notify_completed(preflight, operation_id)
    events = operation_events(runtime.backend.read_snapshot(), operation_id)
    completed_notifications = [e for e in events if e["event_type"] == "notification.created"
                               and e["payload"].get("notification_type") == "operation.completed"]
    expect(len(completed_notifications) == 1, "completion replay duplicated standard Notification")
    done_event = next(e for e in events if e["event_type"] == "operation.done")
    original_done_id = done_event["event_id"]
    done_event["event_id"] = "forged-done"
    try:
        runner._notify_completed(preflight, operation_id)
    except (runner.V03DogfoodScenarioRunnerError, __import__("operator_store_model").StoreInvariantError):
        pass
    else:
        raise AssertionError("completion Notification accepted a forged DONE identity")
    finally:
        done_event["event_id"] = original_done_id

    expect(events[:len(progressed_prefix)] == progressed_prefix,
           "fresh actual host rewrote or replayed the confirmed Developer prefix")
    expect(feature_fixture.state["puts"] > progressed_puts,
           "fresh actual host did not continue actual downstream Persist")
    expect(events[:30] == frozen_prefix, "downstream lifecycle rewrote original blocked history")
    claims = runner._dispatch_rows(preflight, operation_id)
    expect(tuple(runner._dispatch_role(row) for row in claims) == ("developer", "reviewer", "qa"),
           "actual continuation repeated or omitted a role")
    run_ids, receipt = runner._launch_receipts(preflight, operation_id)
    expect(run_ids == (37905505035, *[row["id"] for row in gate_fixture.state["runs"]]),
           "scenario receipts lost original successful Developer or added an execution")
    expect([row["role"] for row in gate_fixture.state["inputs"]] == ["reviewer", "qa"]
           and gate_fixture.state["inputs"][0]["candidate_head_sha"]
               != gate_fixture.state["inputs"][1]["candidate_head_sha"]
           and read_ref() != gate_fixture.state["inputs"][1]["candidate_head_sha"],
           "fake provider failed to advance candidate head through actual Persist commits")
    for kind in ("developer_posts", "created_prs", "fixture_patches"):
        expect(effect_counts()[kind] == before_effects[kind],
               "downstream continuation repeated forbidden external effect: " + kind)
    translated = {row["payload"]["feature_event_id"]: row["payload"]
                  for row in events if row["event_type"] == "feature.event.translated"}
    confirmed = [row["payload"] for row in events if row["event_type"] == "persist.confirmed"]
    historical_confirmed = sum(e["event_type"] == "persist.confirmed" for e in frozen_prefix)
    expect(len(confirmed) - historical_confirmed == feature_fixture.state["applied"]
           and len(confirmed) - historical_confirmed == feature_fixture.state["puts"]
           and all(row["feature_event_id"] in translated for row in confirmed),
           "Store confirmations differ from real Event PUT/reducer applications")
    expect(feature_fixture.state["manifest"]["revision"] == projection["expected_feature_revision"],
           "final Feature revision and Store projection diverged")
    observation = {
        "scenario": "happy_path", "repository": preflight.execution.repository,
        "feature_id": h["feature_id"], "target_ref": h["target_ref"],
        "operation_id": operation_id,
        "installation_commit_sha": preflight.execution.installation_commit_sha,
        "candidate_pr_number": h["candidate_pr_number"], "candidate_head_sha": read_ref(),
        "final_status": "DONE", "workflow_run_ids": list(run_ids),
        "runtime_receipt_identity": receipt, "repeated_continue_messages": 0,
        "release_eligible": False, "provenance_verified": False}
    if is_dataclass(preflight):
        final_preflight = replace(preflight, candidate_head_sha=read_ref())
    else:
        final_preflight = SimpleNamespace(**dict(vars(preflight), candidate_head_sha=read_ref()))

    class Response:
        status = 200
        def __init__(self, raw): self.raw = raw
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self): return self.raw

    def fake_urlopen(req, timeout):
        expect(req.get_method() == "GET", "final provenance attempted provider mutation")
        status, _, raw = gate_fixture.http(method="GET", url=req.full_url, token="fixture")
        expect(status == 200, "final provenance escaped fake provider routes")
        return Response(raw)

    def finalize():
        return finalizer.finalize(
            observation=observation, preflight=final_preflight,
            source_run_id=47905505045, finalizer_run_id=47905505046, github_token="fixture")

    with patch.object(provenance, "urlopen", side_effect=fake_urlopen):
        record = finalize()
        expect(record["verdict"] == "PASS" and record["release_eligible"] is True
               and record["counts"]["human_interventions"] == 4
               and record["runtime"]["workflow_run_ids"] == list(run_ids),
               "real Reviewer replacement full pipeline failed final provenance/release validation")
        required_history = {"https://github.com/dream-xin/ai-sdlc/actions/runs/37917962742",
                            "https://github.com/dream-xin/ai-sdlc/issues/239#issuecomment-6079239160"}
        expect(required_history <= {uri.lower() for uri in record["evidence_uris"]},
               "Reviewer replacement hid its failed predecessor or admission")
        # Controller-generated stage-start Persist cycles are not arbitrary
        # extra Worker confirmations. Rehash a forged semantic change so the
        # finalizer must authenticate its exact trusted transition.
        controller_rows = [row for row in events if row["event_type"] == "feature.event.translated"
                           and not row["payload"].get("callback_id")]
        expect(controller_rows, "full lifecycle omitted controller stage-start events")
        changed = controller_rows[-1]
        original_payload = deepcopy(changed["payload"])
        for label in ("orphan-persist", "forged-stage-start"):
            if label == "orphan-persist":
                changed["payload"]["feature_event_id"] += "-UNBOUND"
            else:
                event_body = changed["payload"]["feature_event"]
                stage_changes = [item for item in event_body["changes"] if item.get("kind") == "stage"]
                expect(stage_changes, "controller event did not contain a stage transition")
                stage_changes[0]["status"] = "DONE"
                changed["payload"]["feature_event_digest"] = digest_json(event_body)
            try:
                finalize()
            except (finalizer.V03DogfoodPostRunFinalizerError,
                    provenance.DogfoodProvenanceVerificationError, VerticalInvariantError, ValueError):
                pass
            except AssertionError as exc:
                expect(str(exc).startswith("real dogfood happy_path: trusted provenance "),
                       "controller-cycle negative failed outside finalizer: " + str(exc))
            else:
                raise AssertionError("finalizer accepted " + label)
            finally:
                changed["payload"] = deepcopy(original_payload)
        expect(finalize()["verdict"] == "PASS", "restored controller lifecycle did not reverify")
        # Only the exact Developer may retain its archived execution source.
        for run in gate_fixture.state["runs"]:
            original = run["head_sha"]
            run["head_sha"] = "6e75792b8e441167cfaadab2d13667a2d80721b8"
            try:
                finalize()
            except (finalizer.V03DogfoodPostRunFinalizerError,
                    provenance.DogfoodProvenanceVerificationError, VerticalInvariantError, ValueError):
                pass
            except AssertionError as exc:
                expect(str(exc).startswith("real dogfood happy_path: trusted provenance verification failed:")
                       or str(exc) == "real dogfood happy_path: trusted provenance verifier errored: V03DogfoodPostRunFinalizerError: original callback differs from historical launch/fresh run"
                       or str(exc) == "real dogfood happy_path: trusted provenance verifier errored: VerticalInvariantError: " + (
                           "Reviewer replacement producer source differs" if run["path"].endswith(
                               ("ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local.lock.yml","ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml"))
                           else "local Gate execution differs from current selected source"),
                       "source mutation failed outside trusted provenance: " + str(exc))
            else:
                raise AssertionError("current-source Gate incorrectly inherited archived Developer source")
            finally:
                run["head_sha"] = original
        expect(finalize()["verdict"] == "PASS", "restored full pipeline failed re-verification")
    print("- real reconciled Developer/Reviewer/QA scenario and canonical Persist finish DONE and finalize")
    return record




def selected_gate_manifest_negative_tests(gates):
    """Authenticate actual fake-provider artifacts with the production reader."""
    import hashlib
    import io
    import json
    import zipfile
    from copy import deepcopy
    from operator_vertical import VerticalInvariantError

    source, state = gates.result_source, gates.state
    routes = state["routes"]
    expect(len(state["runs"]) == 2, "manifest tests require actual Reviewer and QA results")
    original_routes = deepcopy(routes)
    before_posts = deepcopy(state["posts"])
    for run in state["runs"]:
        run_id = run["id"]
        listing_path = f"/actions/runs/{run_id}/artifacts"
        artifact = routes[listing_path]["artifacts"][0]
        archive_path = f"/actions/artifacts/{artifact['id']}/zip"
        with zipfile.ZipFile(io.BytesIO(routes[archive_path])) as archive:
            entry = json.loads(archive.read("safe-output-items.jsonl").decode().strip())
        comment = routes[f"/issues/comments/{entry['id']}"]
        def verify():
            return source._run_owned_gate_comment(run_id=run_id, source_head_sha=run["head_sha"],
                comment=comment, candidate_pr_number=entry["number"])
        proof = verify()
        expect(proof["comment_id"] == entry["id"] and proof["run_id"] == run_id,
               "production reader did not authenticate actual run-owned comment")
        def replace_entry(mutator):
            altered = deepcopy(entry)
            mutator(altered)
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w") as archive:
                archive.writestr("safe-output-items.jsonl", json.dumps(altered) + "\n")
            raw = output.getvalue()
            routes[archive_path] = raw
            current = routes[listing_path]["artifacts"][0]
            current.update(size_in_bytes=len(raw), digest="sha256:" + hashlib.sha256(raw).hexdigest())
        mutations = (
            ("missing run manifest", lambda: routes.__setitem__(listing_path, {"total_count": 0, "artifacts": []})),
            ("foreign run", lambda: routes[listing_path]["artifacts"][0]["workflow_run"].update(id=run_id+1)),
            ("foreign producer", lambda: routes[listing_path]["artifacts"][0]["workflow_run"].update(head_sha="0"*40)),
            ("archive digest", lambda: routes.__setitem__(archive_path, routes[archive_path] + b"tamper")),
            ("comment identity with valid archive digest", lambda: replace_entry(lambda row: row.update(id=entry["id"]+1))),
            ("candidate with valid archive digest", lambda: replace_entry(lambda row: row.update(number=entry["number"]+1))),
        )
        for label, mutate in mutations:
            try:
                mutate()
                try:
                    verify()
                except VerticalInvariantError:
                    pass
                else:
                    raise AssertionError("production Gate manifest reader accepted " + label)
            finally:
                routes.clear()
                routes.update(deepcopy(original_routes))
        expect(verify() == proof, "restored Gate artifact proof changed")
    expect(state["posts"] == before_posts, "manifest negative tests dispatched another Worker")
    print("- selected Reviewer/QA run-owned manifest identity/source/content negatives fail closed")



def reviewer_replacement_admission_tests():
    from copy import deepcopy
    from operator_store import StoreCommandError
    from operator_store_git import CasConflict
    from operator_store_model import canonical_json, operation_events
    from operator_vertical import VerticalInvariantError
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    errors = (StoreCommandError, VerticalInvariantError, d.V03DogfoodRuntimeDriverError,
              d.V03DogfoodScenarioRunnerError, ValueError)
    def reject(pf, gates, feature, label):
        runtime = pf.composition.runtime
        before = (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
                  runtime.backend.commit_count, len(gates.state["posts"]), feature.state["puts"])
        try:
            d.recover_reviewer_pre_model(pf)
        except errors:
            pass
        else:
            raise AssertionError("Reviewer replacement accepted " + label)
        expect(before == (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
                          runtime.backend.commit_count, len(gates.state["posts"]), feature.state["puts"]),
               "Reviewer rejected " + label + " after an unauthorized mutation")
    for path in c.REVIEWER_PATHS:
        for value in (None, {}, []):
            pf, provider, feature, gates, _ = reviewer_runtime_fixture()
            pf.composition.runtime.backend.snapshot.files[path] = value
            reject(pf, gates, feature, "partial/null route " + path)
    for label, mutate in (
        ("predecessor event", lambda pf,p: operation_events(pf.composition.runtime.backend.snapshot,c.RECOVERY_OPERATION_ID)[29]["payload"].update(receipt_id="1")),
        ("old attempt two", lambda pf,p: p.state["reviewer_observed"]["run"].update(run_attempt=2)),
        ("old active", lambda pf,p: p.state["reviewer_observed"]["run"].update(status="in_progress")),
        ("old source", lambda pf,p: p.state["reviewer_observed"]["run"].update(head_sha="9"*40)),
        ("candidate drift", lambda pf,p: p.state.update(head="9"*40)),
        ("new main drift", lambda pf,p: p.state.update(controller_source="9"*40)),
    ):
        pf, provider, feature, gates, _ = reviewer_runtime_fixture()
        mutate(pf,provider)
        reject(pf,gates,feature,label)
    for job_name, step_name in (("agent","Execute GitHub Copilot CLI"),("safe_outputs","Process Safe Outputs")):
        pf, provider, feature, gates, _ = reviewer_runtime_fixture()
        job = next(j for j in provider.state["reviewer_observed"]["jobs"]["jobs"] if j["name"] == job_name)
        next(s for s in job["steps"] if s["name"] == step_name)["conclusion"] = "success"
        reject(pf,gates,feature,"predecessor executed "+step_name)

    pf, provider, feature, gates, _ = reviewer_runtime_fixture()
    runtime = pf.composition.runtime
    original = deepcopy(runtime.backend.read_snapshot())
    from operator_store_model import rebuild_projection
    from operator_vertical_store import vertical_projection
    expect(rebuild_projection(original, c.RECOVERY_OPERATION_ID)["expected_feature_revision"] == 1
           and vertical_projection(original, c.RECOVERY_OPERATION_ID)["expected_feature_revision"] == 3,
           "frozen predecessor fixture does not expose canonical Persist revision overlay")
    c.validate_reviewer_predecessor(original, fresh=True)
    proof = d._observe_reviewer_pre_model_failure(pf)
    binding = c.recovery_execution_binding(pf.composition.policy_authority)
    def planner(snapshot):
        return c.plan_reviewer_replacement(snapshot,consumer_binding=binding,
            worker_blobs=d._reviewer_worker_blobs(),failure_proof=proof)
    first, second = planner(original), planner(original)
    expect(len(first.mutations)==2 and all(m.kind=="create_immutable" for m in first.mutations),
           "Reviewer CAS is not one immutable authorization+consumed claim")
    runtime.backend.commit(first,runtime.protected_receipt())
    try: runtime.backend.commit(second,runtime.protected_receipt())
    except CasConflict: pass
    else: raise AssertionError("two Reviewer CAS winners")
    expect(planner(runtime.backend.read_snapshot()).result["acquired"] is False,
           "Reviewer CAS loser acquired a new creation slot")
    reject(pf,gates,feature,"crash after claim with no observed run")
    expect(operation_events(runtime.backend.read_snapshot(),c.RECOVERY_OPERATION_ID)==provider.frozen_events,
           "Reviewer claim altered original logical launch history")
    pf, provider, feature, gates, _ = reviewer_runtime_fixture()
    pf.composition.runtime.backend.inject_conflict_once()
    result = d.recover_reviewer_pre_model(pf)
    expect(len(gates.state["posts"]) == 1 and result["sealed"]["run_id"] != c.REVIEWER_FAILED_RUN,
           "Reviewer CAS retry did not produce one distinct first attempt")
    before = (pf.composition.runtime.backend.commit_count,len(gates.state["posts"]))
    d.recover_reviewer_pre_model(pf)
    expect(before == (pf.composition.runtime.backend.commit_count,len(gates.state["posts"])),
           "Reviewer sealed replay changed Store or POST count")
    for bad in (True, "1", 2):
        gates.state["runs"][0]["run_attempt"] = bad
        reject(pf,gates,feature,"malformed or repeated replacement attempt")
    gates.state["runs"][0]["run_attempt"] = 1
    gates.state["runs"][0]["conclusion"] = "failure"
    reject(pf,gates,feature,"subsequently failed replacement")
    pf, provider, feature, gates, _ = reviewer_runtime_fixture()
    original_http = gates.transport.http
    lost = {"once":True}
    def lost_ack(**kwargs):
        result = original_http(**kwargs)
        if kwargs["method"] == "POST" and lost["once"]:
            lost["once"] = False
            raise OSError("fixture lost dispatch acknowledgement")
        return result
    gates.transport.http = lost_ack
    d.recover_reviewer_pre_model(pf)
    expect(len(gates.state["posts"]) == 1,"Reviewer acknowledgement loss retried POST")


    pf,provider,feature,gates,_=reviewer_runtime_fixture()
    d.recover_reviewer_pre_model(pf)
    snapshot=pf.composition.runtime.backend.read_snapshot()
    auth,_=c.validate_reviewer_authorization(snapshot)
    sealed=c.reviewer_replacement_route(snapshot)["sealed"]
    resolved=pf.composition.result_source.resolve(external_dispatch_key=auth["physical_key"],
        expected_receipt_identity=str(sealed["run_id"]),trusted_context=c.reviewer_trusted_context(auth))
    from dataclasses import fields
    from operator_vertical import TrustedDispatchContext
    from operator_vertical_recovery import plan_vertical_callback_record
    material=c.reviewer_dispatch(auth,physical=False)
    material.update(runtime_receipt_identity=str(c.REVIEWER_FAILED_RUN),
        worker_identity=resolved.run.worker_identity,collector_identity=resolved.run.collector_identity)
    context=TrustedDispatchContext(**{field.name:material[field.name] for field in fields(TrustedDispatchContext)})
    runtime=pf.composition.runtime
    runtime.commit_replanned(lambda snap:plan_vertical_callback_record(snap,context=context,
        callback_id="foreign-reviewer-observation",worker_payload=resolved.role_payload,receipts=[],
        occurred_at=runtime.clock(),trusted_context_digest=pf.trusted_context_digest))
    before=(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,len(gates.state["posts"]))
    try: pf.composition.collector.handle(operation_id=c.RECOVERY_OPERATION_ID,external_dispatch_key=c.REVIEWER_OLD_KEY)
    except errors: pass
    else: raise AssertionError("Reviewer collector accepted a foreign same-logical-key callback")
    expect(before==(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,len(gates.state["posts"])),
           "conflicting Reviewer callback reached another Store or provider effect")

    for label in ("duplicate retired run", "pagination unknown"):
        pf,provider,feature,gates,_=reviewer_runtime_fixture()
        if label == "duplicate retired run":
            extra=deepcopy(provider.state["reviewer_observed"]["run"])
            extra["id"] += 1
            pf.historical_gate_runs.append(extra)
        else:
            original_http=gates.transport.http
            def broken_lookup(**kwargs):
                if "/actions/workflows/" in kwargs["url"] and kwargs["method"]=="GET":
                    return 503,{},b"{}"
                return original_http(**kwargs)
            gates.transport.http=broken_lookup
        reject(pf,gates,feature,label)
    for failed_step in ("Execute threat detection with AWF",
                        "Require first attempt and affirmative detection before Safe Outputs effects"):
        pf,provider,feature,gates,_=reviewer_runtime_fixture()
        original_http=gates.transport.http
        def skipped_guard(**kwargs):
            result=original_http(**kwargs)
            if kwargs["method"]=="POST":
                run=gates.state["runs"][0]
                doc=gates.state["routes"][f"/actions/runs/{run['id']}/attempts/1/jobs"]
                found=[step for job in doc["jobs"] for step in job["steps"] if step["name"]==failed_step]
                expect(len(found)==1,"selected compiled safety step is missing")
                found[0]["conclusion"]="skipped"
            return result
        gates.transport.http=skipped_guard
        try: d.recover_reviewer_pre_model(pf)
        except errors: pass
        else: raise AssertionError("Reviewer sealed a skipped safety execution")
        expect(len(gates.state["posts"])==1 and c.REVIEWER_SEAL_PATH not in pf.composition.runtime.backend.snapshot.files,
               "failed safety result acquired a seal or duplicate execution")
        reject(pf,gates,feature,"failed safety slot replay")
    print("- Reviewer fixed CAS, pre-model proof, partial routes, replay and acknowledgement loss fail closed")


def reviewer_replacement_full_pipeline_tests():
    from copy import deepcopy
    from operator_store_model import operation_events
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    from validate_v03_dogfood_runtime_composition import assert_post_handoff_authority_graph
    pf, provider, feature, gates, _ = reviewer_runtime_fixture()
    original = deepcopy(pf.composition.runtime.backend.read_snapshot().files)
    # The read-only no-sidecar bridge must work before any creation.
    old = c.validate_reviewer_controller_bridge(pf.composition.runtime.backend.read_snapshot(),
        c.recovery_execution_binding(pf.composition.policy_authority),inspection_only=True)
    expect(old["execution_source_head_sha"] == c.REVIEWER_PREDECESSOR_SOURCE,
           "preclaim bridge silently relabeled old controller source")
    result = d.recover_reviewer_pre_model(pf)
    expect(len(gates.state["posts"]) == 1 and result["sealed"]["run_attempt"] == 1,
           "actual Reviewer transport did not consume exactly one first-attempt slot")
    expect(operation_events(pf.composition.runtime.backend.read_snapshot(),c.RECOVERY_OPERATION_ID)==provider.frozen_events,
           "Reviewer physical execution rewrote or fabricated logical launch facts")
    finish_reviewer_replacement_pipeline_tests(pf,gate_fixture=gates,feature_fixture=feature,
        read_ref=provider.read_ref,effect_counts=provider.effect_counts,adapter=pf.composition.responses.adapter)
    snapshot=pf.composition.runtime.backend.read_snapshot()
    expect(operation_events(snapshot,c.RECOVERY_OPERATION_ID)[:30]==provider.frozen_events,
           "Reviewer/QA pipeline changed frozen thirty-event history")
    for path,value in original.items():
        if "/projections/" not in path and "/operations/" not in path.rsplit("/",1)[-1]:
            if path.startswith("state/operator/v1/operations/") and path.endswith("/projection.json"):
                continue
            expect(snapshot.get(path)==value,"Reviewer pipeline rewrote predecessor "+path)
    assert_post_handoff_authority_graph(pf.composition.graph_before,pf.composition.responses,
        pf.composition.policy_authority,predecessor_events=provider.frozen_events[:15])
    selected_gate_manifest_negative_tests(gates)
    print("- actual selected Reviewer replacement/status/Persist/QA/Notification/finalizer pipeline passes")


def bounded_gate_detector_contract_tests(root, *, upstream_pins):
    """Paired with selected_dogfood_worker_contract_tests' real 3x3 selection/guards."""
    import hashlib, json, shlex
    from copy import deepcopy
    import yaml
    prefix = "# ai-sdlc-bounded-gate-lock-transform: "
    def unique(pairs):
        result = {}
        for key, value in pairs:
            expect(key not in result, "duplicate native configuration key")
            result[key] = value
        return result
    def step(doc, key):
        rows = [s for s in doc["jobs"]["detection"]["steps"] if s.get("id") == key]
        expect(len(rows) == 1, "bounded detector step missing/duplicated")
        return rows[0]
    def config(run):
        destination = '> "' + "$" + '{RUNNER_TEMP}/gh-aw/awf-config.json"'
        lines = [line for line in run.splitlines()
                 if line.startswith("printf ") and line.endswith(destination)]
        expect(len(lines) == 1, "ambiguous detector native configuration")
        tokens = shlex.split(lines[0])
        expect(len(tokens) == 5 and tokens[:2] == ["printf", r"%s\n"]
               and tokens[3:] == [">", "$"+"{RUNNER_TEMP}/gh-aw/awf-config.json"], "native config destination drift")
        credit = "$"+"{GH_AW_MAX_AI_CREDITS}"
        expect(tokens[2].count(credit) == 1, "native credit interpolation drift")
        literal = tokens[2].replace(credit, "400")
        expect(literal.count(r"\$schema") == 1, "native schema shell escaping drift")
        literal = literal.replace(r"\$schema", "$schema", 1)
        expect("$"+"{" not in literal and "$(" not in literal, "unknown native interpolation")
        value = json.loads(literal, object_pairs_hook=unique)
        expect(value.get("$schema") == "https://github.com/github/gh-aw-firewall/releases/download/v0.28.23/awf-config.schema.json",
               "native configuration schema pin drift")
        expect('"maxTurns"' not in json.dumps(value), "invented native maxTurns")
        return lines[0], value
    def verify(role, source_text, lock):
        lines = lock.splitlines(keepends=True)
        expect(lines[1].startswith(prefix) and sum(l.startswith(prefix) for l in lines) == 1, "bounded provenance header")
        proof = json.loads(lines[1][len(prefix):], object_pairs_hook=unique)
        metadata = json.loads(lines[0].split(": ", 1)[1], object_pairs_hook=unique)
        _, front, body = source_text.split("---\n", 2)
        source = yaml.safe_load(front)
        body_hash = hashlib.sha256(body.rstrip("\n").encode()).hexdigest()
        pin = upstream_pins[role]
        expect(proof == dict(schema="ai-sdlc.v03-bounded-gate-lock-transform/v1",
            compiler="gh-aw-v0.89.21-strict", upstream_blob_sha=pin["blob_sha"], upstream_sha256=pin["sha256"],
            body_hash=body_hash, detector_native_max_runs_from=500, detector_native_max_runs_to=50,
            detector_cli_version="1.0.90", cli_version_origin="compiler-derived",
            allowed_delta="unique detection AWF apiProxy.maxRuns integer only", inverse_raw_equal=True),
            "bounded transform proof differs from independent strict compiler pins")
        expect(metadata["compiler_version"] == "v0.89.21" and metadata["strict"] is True
               and metadata["body_hash"] == body_hash, "bounded compiler/source identity")
        detector = source["safe-outputs"]["threat-detection"]
        engine = detector["engine"]
        expect(detector["enabled"] is True and detector["continue-on-error"] is False
               and type(detector["retries"]) is int and detector["retries"] == 0
               and engine["id"] == "copilot" and engine["version"] == "1.0.90"
               and engine["model"] == "deepseek-chat" and engine["env"] == source["engine"]["env"]
               and type(engine["max-turns"]) is int and engine["max-turns"] == 50
               and type(engine["harness"]["max-retries"]) is int and engine["harness"]["max-retries"] == 0,
               "bounded detector source engine/retry contract")
        for fragment in ("whether the previous command succeeded or", "THREAT_DETECTION_RESULT_ERROR",
                         "THREAT_DETECTION_RESULT_RECORDED", "Reaching a budget or deadline is never grounds for a clean verdict"):
            expect(fragment in detector["prompt"], "bounded detector lost full-analysis/no-repeat instruction")
        derived = "".join(lines[:1] + lines[2:])
        document = yaml.safe_load(derived)
        execution = step(document, "detection_agentic_execution")
        installs = [s for s in document["jobs"]["detection"]["steps"] if "install_copilot_cli.sh" in s.get("run", "")]
        expect(len(installs) == 1 and shlex.split(installs[0]["run"]) ==
               ["bash", "$"+"{RUNNER_TEMP}/gh-aw/actions/install_copilot_cli.sh", "1.0.90"], "compiler CLI pin propagation")
        env = execution["env"]
        expect(str(env["GH_AW_HARNESS_MAX_RETRIES"]) == "0" and env["CUSTOM_PROMPT"].strip() == detector["prompt"].strip()
               and all(env[k] == v for k, v in engine["env"].items()), "bounded detector generated environment")
        expect("threat-detect --engine copilot --retries 0 --output " in execution["run"]
               and "--engine-timeout" not in execution["run"] and "THREAT_DETECTION_ENGINE_TIMEOUT" not in env,
               "bounded detector retries/default five-minute timeout")
        expect(sum(l.startswith("awf --config ") for l in execution["run"].splitlines()) == 1, "native invocation uniqueness")
        line, native = config(execution["run"])
        expect(type(native["apiProxy"]["maxRuns"]) is int and native["apiProxy"]["maxRuns"] == 50, "native typed fifty")
        new, old = r'\"maxRuns\":50,', r'\"maxRuns\":500,'
        expect(line.count(new) == 1 and derived.count(line) == 1, "unique inverse target")
        raw_line = line.replace(new, old, 1)
        _, raw_native = config(raw_line)
        expected_native = deepcopy(native)
        expected_native["apiProxy"]["maxRuns"] = 500
        expect(raw_native == expected_native and type(raw_native["apiProxy"]["maxRuns"]) is int, "native inverse delta")
        raw = derived.replace(line, raw_line, 1).encode()
        expect(hashlib.sha256(raw).hexdigest() == pin["sha256"]
               and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == pin["blob_sha"],
               "inverse differs from independently captured raw strict compilation")
        expected_doc = deepcopy(document)
        step(expected_doc, "detection_agentic_execution")["run"] = execution["run"].replace(line, raw_line, 1)
        expect(yaml.safe_load(raw) == expected_doc, "another generated workflow field changed")
        return line
    def reject(label, call):
        try:
            call()
        except (AssertionError, ValueError, KeyError, TypeError):
            return
        raise AssertionError("bounded detector accepted " + label)
    for role in ("reviewer", "qa"):
        filename = "ai-sdlc-gh-aw-" + role + "-deepseek-v03-bounded-local.lock.yml"
        source = (root / ".github/workflows" / filename.replace(".lock.yml", ".md")).read_text()
        lock = (root / ".github/workflows" / filename).read_text()
        line = verify(role, source, lock)
        for label, token in (
            ("native500", r'\"maxRuns\":500,'), ("string50", r'\"maxRuns\":\"50\",'),
            ("boolean", r'\"maxRuns\":true,'), ("maxTurns", r'\"maxRuns\":50,\"maxTurns\":50,'),
            ("duplicate", r'\"maxRuns\":50,\"maxRuns\":50,'),
        ):
            bad = lock.replace(line, line.replace(r'\"maxRuns\":50,', token, 1), 1)
            reject(label, lambda bad=bad: verify(role, source, bad))
        reject("unrelated derived bytes", lambda: verify(role, source, lock + "# unauthorized delta\n"))
        reject("source retries", lambda: verify(role, source.replace("        max-retries: 0", "        max-retries: 1", 1), lock))
    print("- bounded Gate native configuration and exact compiler inverses validated")

def reviewer_post_model_frozen_provider_fixture(archive_bytes):
    """Exact frozen30 Store plus actual failed ordinal-one provider observations.

    Only historical input is loaded. The production planner must produce every
    ordinal-two authorization, claim, receipt, callback and Persist transition.
    Detector log data below is an explicitly bounded verbatim excerpt, never a
    substitute for a successful semantic safety result.
    """
    import hashlib
    import json
    import subprocess
    from copy import deepcopy
    from pathlib import Path
    from urllib.parse import parse_qs, unquote, urlparse
    from operator_store_model import StoreSnapshot, operation_events
    from operator_vertical_store import vertical_projection

    provider = reviewer_frozen_provider_fixture(archive_bytes)
    root = Path(__file__).resolve().parents[1]
    commit = "118c421312ce10ce5747595470504cece2ea44e3"
    operation_id = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
    operation_root = "state/operator/v1/operations/" + operation_id + "/"
    first_root = operation_root + "dogfood-reviewer-pre-model-replacement-1/"
    second_root = operation_root + "dogfood-reviewer-post-model-replacement-2/"
    pins = json.loads("{\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/authorization.json\":\"e68d45041c47083c2da5521aa325fcf58bef256b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/create-claim.json\":\"706888dddd1acc157cdbeb5b54ace6539125e16b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/post-handoff-reconciliation-1.json\":\"5178d7697c9b140c64c4dc2cf3fca94bf223c1ab\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/sealed-receipt.json\":\"7558b2b3ffe08bb255d3c287fffa49f5fc988f0e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/authorization.json\":\"d344fca61af21038c929897bc3fd636d4297ee17\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/create-attempt.json\":\"db183bc850c8e9add5abad38ced5728325192aba\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/transport-continuation.json\":\"e2b2edf5e50eedaacbdaee7ef1b0ae64c5f94580\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-candidate-handoffs/1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be/applied.json\":\"eb63e61ff00ae20bbe465d205c98924056e77827\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-candidate-handoffs/1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be/intent.json\":\"da06cd8849fff194e3cdbeb1df54d3efa69f850e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-prehttp-recovery-attempt.json\":\"e9bf99cc5fd8810a6fd08666ff4c17b136bee1b3\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-reviewer-pre-model-replacement-1/authorization.json\":\"9887312d2053849c64986be4bb661938fa08a7e6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-reviewer-pre-model-replacement-1/create-claim.json\":\"f8092c2cfea63b9da61ebc21a5e967b2e4fd6ad3\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000001-operation-started-739f4331137732d6184000cf3d8b4915.json\":\"86e43c43941b03e9721844b58479d95db93ce5c8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000002-loop-step-selected-2cf82f42399ec59c7283df1711819426.json\":\"f26de71400211607359fb60b58e77ddbab7216ed\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000003-dispatch-claimed-687520874a948a5c4534e4e30d97b366.json\":\"6741a203a62d31e81f1d619d705bb18feddc0173\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000004-dispatch-launch-authorized-5f6d063282780be1d174254ce8ef9134.json\":\"1840f7cac50722dad83cb0118e441e475175d859\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000005-dispatch-launch-lookup-recorded-8629bfae40264dc5d15df601bc67686d.json\":\"6f73721b7c8847ddae58e59bbee801d28cd9ef43\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000006-operation-superseded-972620170d72306fdfa27f564dbc68c4.json\":\"a11e8f98ab5a9511fe30cf22f0ba6fa0c41253d9\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000007-operation-generation-started-559d6292df44700862ae642429872527.json\":\"def317058e40c16a8b35c7390ab5847e678192c5\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000008-loop-step-selected-d13700446f0139fb96e5717dc101bbdf.json\":\"275a6134e086e95c72f7a8c0aa8940a9f35a67c8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000009-dispatch-claimed-f55e33e1ea7fd6b35eb44831e700d91f.json\":\"09cbb9201c82d1e69bcce5b0febc8c28f8948fac\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000010-dispatch-launch-authorized-8ea8faac43fb02dc1c3c8e481a40da93.json\":\"f6f793ce9725e618be0b7b25712e5abe44a55b59\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000011-dispatch-launch-lookup-recorded-ef426f7c675283149f805fdab65861ec.json\":\"ca4581772d9271f0e7d4dece22479a376504e8d5\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000012-dispatch-launch-lookup-recorded-cff7b708649dcf3b6ca354d3f72fbb6d.json\":\"96c43dcefda8558aa73750eb12a5e0d5af419d82\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000013-worker-callback-recorded-6e9bb8c4081e7ac28af2c5bccda2dcb4.json\":\"d773017efeec4ceccd65aaf28c1574c5aa6c9f69\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000014-worker-result-rejected-6e6b1ab67000b9207463c8676cad7be7.json\":\"93c3c64ea155b65bdbeaf78eed0067590aed9ede\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000015-loop-stable-stop-cd01bb348e233107a60b54928e336a00.json\":\"5a6969ba938f6153705d999065f786d4bb3159ec\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000016-worker-callback-recorded-8f4c403e06ae9355b0b245c9df740bf7.json\":\"069beb76d507b71109a1219722e03bf9bd4179f2\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000017-worker-result-validated-ed56d7eadf7efdb50d607eebb60c09e1.json\":\"54c9ddf16627d495c4ef06f80016fda173e12475\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000018-feature-event-translated-146e91f4ed5fa9420bdc79e69576a866.json\":\"b277131c21142e815de9825c9c7f02f5283bf19a\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000019-persist-requested-05b10f2f65d52ecbde4f92a6f48ad0a8.json\":\"03c6cb5628f5896c3dedcfddeb6abb0257b0886d\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000020-persist-linearized-9a302d406aac5e4a2b8aede284386a4c.json\":\"e8ea390b14f84173a2f2ba65003fb7fcb21d1faf\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000021-persist-confirmed-a92f43c724a6f37ad3eef0d7f217977e.json\":\"3f1dac41fef483899b0af3d1de6634e502497986\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000022-loop-step-selected-185d0cd557b4337a582cdcdc9ed7075c.json\":\"19c681ab77d90849e6b67aa53b8ccc3e271c16e6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000023-feature-event-translated-5bd0311cb465491d30a7027e617c4c4c.json\":\"09aedd1fa3bd0fb8b71ef6fd75390e2edc520db4\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000024-persist-requested-1e166d13887db2dee0b82e6a979b850e.json\":\"4dcf56f2ad6a941ccf5b6204cbb418d86f9be0b6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000025-persist-linearized-5681cee3f8344418644af863d69d5ce8.json\":\"1b807744b90d39564e05ce2f378e28fba6ece5b1\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000026-persist-confirmed-c1ee4698394d605b8ba50d7529227216.json\":\"1bd7b63e0b30882f1c304d1be16dad17f3b1dfbe\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000027-loop-step-selected-0c44caba02897e2237f92649007a66fa.json\":\"2400f03e80d353ba989b66a8609f780e955d5dff\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000028-dispatch-claimed-1d7f9416b07a819c38b27bf7535ccc18.json\":\"792494e66a1d31529993832e3cc8b0940ab58244\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000029-dispatch-launch-authorized-551bf29f723465460beea589087e5adf.json\":\"905d1b7f02ebb4a4b59342de885a5120ca2ff089\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000030-dispatch-launch-lookup-recorded-cd54b307a841588acaa8f4ccb197323f.json\":\"20ced53e567b946554fdaaa166f64167c4fe78c8\"}")
    observed = json.loads("{\"run\":{\"id\":37927328438,\"run_attempt\":1,\"workflow_id\":379567834,\"path\":\".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local.lock.yml\",\"name\":\"AI-SDLC gh-aw dispatch-d653abeb44f20430dc9a5600150717ff76dd57bb\",\"display_title\":\"AI-SDLC gh-aw dispatch-d653abeb44f20430dc9a5600150717ff76dd57bb\",\"event\":\"workflow_dispatch\",\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"status\":\"completed\",\"conclusion\":\"failure\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37927328438\",\"created_at\":\"2026-10-09T12:01:08Z\",\"updated_at\":\"2026-10-09T12:11:12Z\",\"run_started_at\":\"2026-10-09T12:01:08Z\",\"repository\":{\"full_name\":\"DREAM-XIN/ai-sdlc\"}},\"jobs\":{\"total_count\":5,\"jobs\":[{\"id\":113809299117,\"run_id\":37927328438,\"run_attempt\":1,\"name\":\"activation\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"started_at\":\"2026-10-09T12:01:16Z\",\"completed_at\":\"2026-10-09T12:01:45Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T12:01:17Z\",\"completed_at\":\"2026-10-09T12:01:20Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T12:01:21Z\",\"completed_at\":\"2026-10-09T12:01:28Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T12:01:28Z\",\"completed_at\":\"2026-10-09T12:01:28Z\"},{\"name\":\"Generate agentic run info\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T12:01:28Z\",\"completed_at\":\"2026-10-09T12:01:30Z\"},{\"name\":\"Restore daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T12:01:30Z\",\"completed_at\":\"2026-10-09T12:01:31Z\"},{\"name\":\"Check daily workflow token guardrail\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T12:01:31Z\",\"completed_at\":\"2026-10-09T12:01:31Z\"},{\"name\":\"Publish daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T12:01:31Z\",\"completed_at\":\"2026-10-09T12:01:32Z\"},{\"name\":\"Check for OAuth tokens\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T12:01:32Z\",\"completed_at\":\"2026-10-09T12:01:32Z\"},{\"name\":\"Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T12:01:32Z\",\"completed_at\":\"2026-10-09T12:01:37Z\"},{\"name\":\"Save agent config folders for base branch restoration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T12:01:37Z\",\"completed_at\":\"2026-10-09T12:01:37Z\"},{\"name\":\"Check workflow lock file\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T12:01:37Z\",\"completed_at\":\"2026-10-09T12:01:38Z\"},{\"name\":\"Check compile-agentic version\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T12:01:38Z\",\"completed_at\":\"2026-10-09T12:01:39Z\"},{\"name\":\"Log runtime features\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T12:01:39Z\",\"completed_at\":\"2026-10-09T12:01:39Z\"},{\"name\":\"Create prompt with built-in context\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T12:01:39Z\",\"completed_at\":\"2026-10-09T12:01:39Z\"},{\"name\":\"Interpolate variables and render templates\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T12:01:39Z\",\"completed_at\":\"2026-10-09T12:01:39Z\"},{\"name\":\"Substitute placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T12:01:39Z\",\"completed_at\":\"2026-10-09T12:01:39Z\"},{\"name\":\"Validate prompt placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T12:01:39Z\",\"completed_at\":\"2026-10-09T12:01:40Z\"},{\"name\":\"Print prompt\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T12:01:40Z\",\"completed_at\":\"2026-10-09T12:01:40Z\"},{\"name\":\"Upload info artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T12:01:40Z\",\"completed_at\":\"2026-10-09T12:01:41Z\"},{\"name\":\"Stage prompt files for artifact upload\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T12:01:41Z\",\"completed_at\":\"2026-10-09T12:01:41Z\"},{\"name\":\"Upload activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-09T12:01:41Z\",\"completed_at\":\"2026-10-09T12:01:42Z\"},{\"name\":\"Post Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-09T12:01:42Z\",\"completed_at\":\"2026-10-09T12:01:43Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-09T12:01:43Z\",\"completed_at\":\"2026-10-09T12:01:43Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-09T12:01:43Z\",\"completed_at\":\"2026-10-09T12:01:43Z\"}]},{\"id\":113809496837,\"run_id\":37927328438,\"run_attempt\":1,\"name\":\"agent\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"started_at\":\"2026-10-09T12:01:47Z\",\"completed_at\":\"2026-10-09T12:04:37Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T12:01:47Z\",\"completed_at\":\"2026-10-09T12:01:49Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T12:01:49Z\",\"completed_at\":\"2026-10-09T12:01:51Z\"},{\"name\":\"Reject rerun before model execution\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T12:01:51Z\",\"completed_at\":\"2026-10-09T12:01:51Z\"},{\"name\":\"Validate release-only local Worker identity\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T12:01:51Z\",\"completed_at\":\"2026-10-09T12:01:51Z\"},{\"name\":\"Set runtime paths\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T12:01:51Z\",\"completed_at\":\"2026-10-09T12:01:51Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-09T12:01:51Z\",\"completed_at\":\"2026-10-09T12:01:51Z\"},{\"name\":\"Check OTLP telemetry configuration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T12:01:51Z\",\"completed_at\":\"2026-10-09T12:01:51Z\"},{\"name\":\"Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T12:01:51Z\",\"completed_at\":\"2026-10-09T12:01:53Z\"},{\"name\":\"Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T12:01:53Z\",\"completed_at\":\"2026-10-09T12:01:55Z\"},{\"name\":\"Build checkout manifest for safe-outputs handlers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T12:01:55Z\",\"completed_at\":\"2026-10-09T12:01:55Z\"},{\"name\":\"Initialize agent execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T12:01:55Z\",\"completed_at\":\"2026-10-09T12:01:55Z\"},{\"name\":\"Create gh-aw temp directory\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T12:01:55Z\",\"completed_at\":\"2026-10-09T12:01:55Z\"},{\"name\":\"Configure gh CLI for GitHub Enterprise\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-09T12:01:55Z\",\"completed_at\":\"2026-10-09T12:01:55Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T12:01:55Z\",\"completed_at\":\"2026-10-09T12:01:56Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T12:01:56Z\",\"completed_at\":\"2026-10-09T12:01:56Z\"},{\"name\":\"Checkout PR branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":16,\"started_at\":\"2026-10-09T12:01:56Z\",\"completed_at\":\"2026-10-09T12:01:56Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T12:01:56Z\",\"completed_at\":\"2026-10-09T12:02:06Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T12:02:06Z\",\"completed_at\":\"2026-10-09T12:02:07Z\"},{\"name\":\"Determine automatic lockdown mode for GitHub MCP Server\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T12:02:07Z\",\"completed_at\":\"2026-10-09T12:02:07Z\"},{\"name\":\"Parse integrity filter lists\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T12:02:07Z\",\"completed_at\":\"2026-10-09T12:02:07Z\"},{\"name\":\"Restore agent config folders from base branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":21,\"started_at\":\"2026-10-09T12:02:07Z\",\"completed_at\":\"2026-10-09T12:02:07Z\"},{\"name\":\"Restore inline sub-agents from activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":22,\"started_at\":\"2026-10-09T12:02:07Z\",\"completed_at\":\"2026-10-09T12:02:07Z\"},{\"name\":\"Restore inline skills from activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-09T12:02:07Z\",\"completed_at\":\"2026-10-09T12:02:07Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-09T12:02:07Z\",\"completed_at\":\"2026-10-09T12:02:22Z\"},{\"name\":\"Prepare Safe Outputs Directories\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":25,\"started_at\":\"2026-10-09T12:02:22Z\",\"completed_at\":\"2026-10-09T12:02:22Z\"},{\"name\":\"Generate Safe Outputs Config\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":26,\"started_at\":\"2026-10-09T12:02:22Z\",\"completed_at\":\"2026-10-09T12:02:22Z\"},{\"name\":\"Generate Safe Outputs Tools\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":27,\"started_at\":\"2026-10-09T12:02:22Z\",\"completed_at\":\"2026-10-09T12:02:22Z\"},{\"name\":\"Start MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":28,\"started_at\":\"2026-10-09T12:02:22Z\",\"completed_at\":\"2026-10-09T12:02:28Z\"},{\"name\":\"Mount MCP servers as CLIs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":29,\"started_at\":\"2026-10-09T12:02:28Z\",\"completed_at\":\"2026-10-09T12:02:28Z\"},{\"name\":\"Clean credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":30,\"started_at\":\"2026-10-09T12:02:28Z\",\"completed_at\":\"2026-10-09T12:02:28Z\"},{\"name\":\"Audit pre-agent workspace\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":31,\"started_at\":\"2026-10-09T12:02:28Z\",\"completed_at\":\"2026-10-09T12:02:28Z\"},{\"name\":\"Execute GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":32,\"started_at\":\"2026-10-09T12:02:28Z\",\"completed_at\":\"2026-10-09T12:04:30Z\"},{\"name\":\"Detect agent errors\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":33,\"started_at\":\"2026-10-09T12:04:30Z\",\"completed_at\":\"2026-10-09T12:04:30Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":34,\"started_at\":\"2026-10-09T12:04:30Z\",\"completed_at\":\"2026-10-09T12:04:30Z\"},{\"name\":\"Copy Copilot session state files to logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":35,\"started_at\":\"2026-10-09T12:04:30Z\",\"completed_at\":\"2026-10-09T12:04:30Z\"},{\"name\":\"Stop MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":36,\"started_at\":\"2026-10-09T12:04:30Z\",\"completed_at\":\"2026-10-09T12:04:31Z\"},{\"name\":\"Redact secrets in logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":37,\"started_at\":\"2026-10-09T12:04:31Z\",\"completed_at\":\"2026-10-09T12:04:31Z\"},{\"name\":\"Append agent step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":38,\"started_at\":\"2026-10-09T12:04:31Z\",\"completed_at\":\"2026-10-09T12:04:31Z\"},{\"name\":\"Copy Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":39,\"started_at\":\"2026-10-09T12:04:31Z\",\"completed_at\":\"2026-10-09T12:04:31Z\"},{\"name\":\"Ingest agent output\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":40,\"started_at\":\"2026-10-09T12:04:31Z\",\"completed_at\":\"2026-10-09T12:04:32Z\"},{\"name\":\"Parse agent logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-09T12:04:32Z\",\"completed_at\":\"2026-10-09T12:04:32Z\"},{\"name\":\"Parse MCP Gateway logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-09T12:04:32Z\",\"completed_at\":\"2026-10-09T12:04:32Z\"},{\"name\":\"Print firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-09T12:04:32Z\",\"completed_at\":\"2026-10-09T12:04:33Z\"},{\"name\":\"Parse token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":44,\"started_at\":\"2026-10-09T12:04:33Z\",\"completed_at\":\"2026-10-09T12:04:33Z\"},{\"name\":\"Print AWF reflect summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":45,\"started_at\":\"2026-10-09T12:04:33Z\",\"completed_at\":\"2026-10-09T12:04:33Z\"},{\"name\":\"Generate observability summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":46,\"started_at\":\"2026-10-09T12:04:33Z\",\"completed_at\":\"2026-10-09T12:04:33Z\"},{\"name\":\"Write agent output placeholder if missing\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":47,\"started_at\":\"2026-10-09T12:04:33Z\",\"completed_at\":\"2026-10-09T12:04:33Z\"},{\"name\":\"Upload agent output fallback artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":48,\"started_at\":\"2026-10-09T12:04:33Z\",\"completed_at\":\"2026-10-09T12:04:34Z\"},{\"name\":\"Upload agent artifacts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":49,\"started_at\":\"2026-10-09T12:04:34Z\",\"completed_at\":\"2026-10-09T12:04:34Z\"},{\"name\":\"Post Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":96,\"started_at\":\"2026-10-09T12:04:34Z\",\"completed_at\":\"2026-10-09T12:04:34Z\"},{\"name\":\"Post Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":97,\"started_at\":\"2026-10-09T12:04:34Z\",\"completed_at\":\"2026-10-09T12:04:35Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":98,\"started_at\":\"2026-10-09T12:04:35Z\",\"completed_at\":\"2026-10-09T12:04:35Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":99,\"started_at\":\"2026-10-09T12:04:35Z\",\"completed_at\":\"2026-10-09T12:04:35Z\"}]},{\"id\":113810489745,\"run_id\":37927328438,\"run_attempt\":1,\"name\":\"detection\",\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"started_at\":\"2026-10-09T12:04:39Z\",\"completed_at\":\"2026-10-09T12:10:51Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T12:04:40Z\",\"completed_at\":\"2026-10-09T12:04:43Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T12:04:43Z\",\"completed_at\":\"2026-10-09T12:04:45Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-09T12:04:45Z\",\"completed_at\":\"2026-10-09T12:04:47Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-09T12:04:47Z\",\"completed_at\":\"2026-10-09T12:04:48Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-09T12:04:48Z\",\"completed_at\":\"2026-10-09T12:04:48Z\"},{\"name\":\"Checkout repository for patch context\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":6,\"started_at\":\"2026-10-09T12:04:48Z\",\"completed_at\":\"2026-10-09T12:04:48Z\"},{\"name\":\"Initialize detection execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T12:04:48Z\",\"completed_at\":\"2026-10-09T12:04:48Z\"},{\"name\":\"Clear inherited Copilot session state\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T12:04:48Z\",\"completed_at\":\"2026-10-09T12:04:48Z\"},{\"name\":\"Clean stale firewall files from agent artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T12:04:48Z\",\"completed_at\":\"2026-10-09T12:04:48Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-09T12:04:48Z\",\"completed_at\":\"2026-10-09T12:05:00Z\"},{\"name\":\"Check if detection needed\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-09T12:05:00Z\",\"completed_at\":\"2026-10-09T12:05:00Z\"},{\"name\":\"Clear MCP Config for detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-09T12:05:00Z\",\"completed_at\":\"2026-10-09T12:05:00Z\"},{\"name\":\"Prepare threat detection files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-09T12:05:00Z\",\"completed_at\":\"2026-10-09T12:05:00Z\"},{\"name\":\"Setup threat detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-09T12:05:00Z\",\"completed_at\":\"2026-10-09T12:05:00Z\"},{\"name\":\"Ensure threat-detection directory and log\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-09T12:05:00Z\",\"completed_at\":\"2026-10-09T12:05:00Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T12:05:00Z\",\"completed_at\":\"2026-10-09T12:05:01Z\"},{\"name\":\"Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T12:05:01Z\",\"completed_at\":\"2026-10-09T12:05:01Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-09T12:05:01Z\",\"completed_at\":\"2026-10-09T12:05:22Z\"},{\"name\":\"Install threat-detect binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-09T12:05:22Z\",\"completed_at\":\"2026-10-09T12:05:22Z\"},{\"name\":\"Execute threat detection with AWF\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-09T12:05:22Z\",\"completed_at\":\"2026-10-09T12:10:46Z\"},{\"name\":\"Render detection log\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-09T12:10:46Z\",\"completed_at\":\"2026-10-09T12:10:46Z\"},{\"name\":\"Copy detection firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":22,\"started_at\":\"2026-10-09T12:10:46Z\",\"completed_at\":\"2026-10-09T12:10:46Z\"},{\"name\":\"Parse threat detection token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-09T12:10:46Z\",\"completed_at\":\"2026-10-09T12:10:47Z\"},{\"name\":\"Upload threat detection artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-09T12:10:47Z\",\"completed_at\":\"2026-10-09T12:10:48Z\"},{\"name\":\"Conclude threat detection\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":25,\"started_at\":\"2026-10-09T12:10:48Z\",\"completed_at\":\"2026-10-09T12:10:48Z\"},{\"name\":\"Post Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":49,\"started_at\":\"2026-10-09T12:10:48Z\",\"completed_at\":\"2026-10-09T12:10:48Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":50,\"started_at\":\"2026-10-09T12:10:48Z\",\"completed_at\":\"2026-10-09T12:10:48Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":51,\"started_at\":\"2026-10-09T12:10:48Z\",\"completed_at\":\"2026-10-09T12:10:48Z\"}]},{\"id\":113812648935,\"run_id\":37927328438,\"run_attempt\":1,\"name\":\"conclusion\",\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"started_at\":\"2026-10-09T12:10:55Z\",\"completed_at\":\"2026-10-09T12:11:11Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-09T12:10:58Z\",\"completed_at\":\"2026-10-09T12:11:01Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-09T12:11:01Z\",\"completed_at\":\"2026-10-09T12:11:04Z\"},{\"name\":\"Record non-authoritative Gate execution identity\",\"status\":\"completed\",\"conclusion\":\"failure\",\"number\":3,\"started_at\":\"2026-10-09T12:11:04Z\",\"completed_at\":\"2026-10-09T12:11:04Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":4,\"started_at\":\"2026-10-09T12:11:04Z\",\"completed_at\":\"2026-10-09T12:11:04Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":5,\"started_at\":\"2026-10-09T12:11:04Z\",\"completed_at\":\"2026-10-09T12:11:04Z\"},{\"name\":\"Download detection artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":6,\"started_at\":\"2026-10-09T12:11:04Z\",\"completed_at\":\"2026-10-09T12:11:04Z\"},{\"name\":\"Download Safe Outputs Items Manifest\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-09T12:11:04Z\",\"completed_at\":\"2026-10-09T12:11:04Z\"},{\"name\":\"Collect usage artifact files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-09T12:11:04Z\",\"completed_at\":\"2026-10-09T12:11:05Z\"},{\"name\":\"Upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-09T12:11:05Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Wait before retrying usage artifact upload\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":10,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Retry upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":11,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Process no-op messages\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":12,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Log detection run\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Record missing tool\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":14,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Record incomplete\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":15,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:06Z\"},{\"name\":\"Handle agent failure\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-09T12:11:06Z\",\"completed_at\":\"2026-10-09T12:11:08Z\"},{\"name\":\"Report failed jobs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-09T12:11:08Z\",\"completed_at\":\"2026-10-09T12:11:09Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":34,\"started_at\":\"2026-10-09T12:11:09Z\",\"completed_at\":\"2026-10-09T12:11:09Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":35,\"started_at\":\"2026-10-09T12:11:09Z\",\"completed_at\":\"2026-10-09T12:11:09Z\"}]},{\"id\":113812650050,\"run_id\":37927328438,\"run_attempt\":1,\"name\":\"safe_outputs\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\",\"started_at\":\"2026-10-09T12:10:52Z\",\"completed_at\":\"2026-10-09T12:10:51Z\",\"steps\":[]}]},\"artifacts\":{\"total_count\":6,\"artifacts\":[{\"id\":11615306199,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxNTMwNjE5OQ==\",\"name\":\"usage\",\"size_in_bytes\":445,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11615306199\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11615306199/zip\",\"expired\":false,\"digest\":\"sha256:9ddbdb622f2081412ab40300632f6c3d17c2eb5a5975a73136124f18cc9f8159\",\"created_at\":\"2026-10-09T12:11:06Z\",\"updated_at\":\"2026-10-09T12:11:06Z\",\"expires_at\":\"2027-01-07T12:01:10Z\",\"workflow_run\":{\"id\":37927328438,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\"}},{\"id\":11615291183,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxNTI5MTE4Mw==\",\"name\":\"detection\",\"size_in_bytes\":47356,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11615291183\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11615291183/zip\",\"expired\":false,\"digest\":\"sha256:ba7d688ef4a4e6a11953e05ba55b02ba3a7a3c5d7b4c3adeb3a7d2181172dbe8\",\"created_at\":\"2026-10-09T12:10:48Z\",\"updated_at\":\"2026-10-09T12:10:48Z\",\"expires_at\":\"2027-01-07T12:01:10Z\",\"workflow_run\":{\"id\":37927328438,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\"}},{\"id\":11614806653,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxNDgwNjY1Mw==\",\"name\":\"agent-output-fallback\",\"size_in_bytes\":7320,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614806653\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614806653/zip\",\"expired\":false,\"digest\":\"sha256:8aaa58dd2068255f44ce825c677f31a4d9805dcddef34fc3d9c57bfeb9807641\",\"created_at\":\"2026-10-09T12:04:33Z\",\"updated_at\":\"2026-10-09T12:04:33Z\",\"expires_at\":\"2027-01-07T12:01:10Z\",\"workflow_run\":{\"id\":37927328438,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\"}},{\"id\":11614602643,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxNDYwMjY0Mw==\",\"name\":\"activation\",\"size_in_bytes\":1083418,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614602643\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614602643/zip\",\"expired\":false,\"digest\":\"sha256:473677789b3d60cefe6f821ce470a81570d3d8d46dbd917b6d16f71beaf832e0\",\"created_at\":\"2026-10-09T12:01:42Z\",\"updated_at\":\"2026-10-09T12:01:42Z\",\"expires_at\":\"2026-10-10T12:01:41Z\",\"workflow_run\":{\"id\":37927328438,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\"}},{\"id\":11614473040,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxNDQ3MzA0MA==\",\"name\":\"info\",\"size_in_bytes\":636,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614473040\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614473040/zip\",\"expired\":false,\"digest\":\"sha256:048ea882529c55e17d89f0e54e639889ab807ced5bb71262dd70b098887c412f\",\"created_at\":\"2026-10-09T12:01:40Z\",\"updated_at\":\"2026-10-09T12:01:40Z\",\"expires_at\":\"2027-01-07T12:01:10Z\",\"workflow_run\":{\"id\":37927328438,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\"}},{\"id\":11614239306,\"node_id\":\"MDg6QXJ0aWZhY3QxMTYxNDIzOTMwNg==\",\"name\":\"agent\",\"size_in_bytes\":1064768,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614239306\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11614239306/zip\",\"expired\":false,\"digest\":\"sha256:41906db5ed959e854f7466925c9e594f3519fd2e58f11985b0ddbbe98f8b42e5\",\"created_at\":\"2026-10-09T12:04:34Z\",\"updated_at\":\"2026-10-09T12:04:34Z\",\"expires_at\":\"2027-01-07T12:01:10Z\",\"workflow_run\":{\"id\":37927328438,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"193d96474529556cc0d805bb9be2b0a96909777b\"}}]},\"comments\":[]}")
    failure_issue = json.loads("{\"id\":5777870748,\"number\":580,\"title\":\"[aw] AI-SDLC gh-aw Code Reviewer (deepseek v0.3 release local) produced no safe outputs\",\"state\":\"open\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/issues/580\",\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/580\",\"created_at\":\"2026-10-09T12:11:07Z\",\"updated_at\":\"2026-10-09T12:11:07Z\",\"closed_at\":null,\"body\":\"### Workflow Failure\\n\\n**Workflow:** [AI-SDLC gh-aw Code Reviewer (deepseek v0.3 release local)](https://github.com/DREAM-XIN/ai-sdlc/blob/main/.github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local.md)  \\n**Branch:** main  \\n**Run:** https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37927328438\\n\\n\\n> [!WARNING]\\n> **No Safe Outputs Generated**: The agent job succeeded but did not produce any safe outputs.\\n\\n\\nThis typically indicates:\\n- The safe output server failed to run\\n- The prompt failed to generate any meaningful result\\n- The agent should have called `noop` to explicitly indicate no action was taken\\n- A `safeoutputs` CLI command was malformed and never invoked the CLI\\n\\n\\n\\n### Action Required\\n\\n**Assign this issue to an agent** to debug and fix the issue.\\n\\n\\n<details>\\n<summary>Debug with any coding agent</summary>\\n\\nUse this prompt with any coding agent (GitHub Copilot, Claude, Gemini, etc.):\\n\\n````\\nDebug the agentic workflow failure using https://raw.githubusercontent.com/github/gh-aw/main/debug.md\\n\\nThe failed workflow run is at https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37927328438\\n````\\n\\n</details>\\n\\n<details>\\n<summary>Manually invoke the agent</summary>\\n\\nDebug this workflow failure using your favorite Agent CLI and the `agentic-workflows` prompt.\\n\\n- Start your agent\\n- Load the `agentic-workflows` skill from `.github/skills/agentic-workflows/SKILL.md` or <https://github.com/github/gh-aw/blob/main/.github/skills/agentic-workflows/SKILL.md>\\n- Type `debug the agentic workflow ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local failure in https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37927328438`\\n\\n</details>\\n\\n> [!TIP]\\n> <details>\\n> <summary>Stop reporting this workflow as a failure</summary>\\n>\\n> To stop a workflow from creating failure issues, set `report-failure-as-issue: false` in its frontmatter:\\n> ```yaml\\n> safe-outputs:\\n>   report-failure-as-issue: false\\n> ```\\n>\\n> </details>\\n\\n\\n> Generated from [AI-SDLC gh-aw Code Reviewer (deepseek v0.3 release local)](https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37927328438) · [◷](https://github.com/search?q=repo%3ADREAM-XIN%2Fai-sdlc+is%3Aissue+%22gh-aw-workflow-id%3A+ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local%22&type=issues)\\n\\n<!-- gh-aw-agentic-workflow: AI-SDLC gh-aw Code Reviewer (deepseek v0.3 release local), engine: copilot, id: 37927328438, workflow_id: ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local, run: https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37927328438 -->\\n<!-- gh-aw-failure-issue: true, workflow_id: ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local, branch: main, failure_categories: missing_safe_outputs -->\",\"labels\":[{\"id\":12358948685,\"node_id\":\"LA_kwDOTw3ETM8AAAAC4KaXTQ\",\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/labels/agentic-workflows\",\"name\":\"agentic-workflows\",\"color\":\"ededed\",\"default\":false,\"description\":null,\"archived_at\":null,\"archived_by\":null}],\"comments\":0,\"user\":{\"login\":\"github-actions[bot]\",\"id\":41898282,\"node_id\":\"MDM6Qm90NDE4OTgyODI=\",\"avatar_url\":\"https://avatars.githubusercontent.com/in/15368?v=4\",\"gravatar_id\":\"\",\"url\":\"https://api.github.com/users/github-actions%5Bbot%5D\",\"html_url\":\"https://github.com/apps/github-actions\",\"followers_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/followers\",\"following_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/following{/other_user}\",\"gists_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/gists{/gist_id}\",\"starred_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/starred{/owner}{/repo}\",\"subscriptions_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/subscriptions\",\"organizations_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/orgs\",\"repos_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/repos\",\"events_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/events{/privacy}\",\"received_events_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/received_events\",\"type\":\"Bot\",\"user_view_type\":\"public\",\"site_admin\":false}}")
    detector_log = "2026-10-09T12:05:00.2622384Z   GH_AW_DETECTION_CONTINUE_ON_ERROR: false\n2026-10-09T12:05:22.8305855Z   GH_AW_DETECTION_CONTINUE_ON_ERROR: false\n2026-10-09T12:10:44.5865534Z Error running detection: engine timeout: detection engine did not record a verdict within 5m0s\n2026-10-09T12:10:44.5866652Z THREAT_DETECTION_STATUS: reason=engine_timeout exit=2\n2026-10-09T12:10:46.9380602Z THREAT_DETECTION_STATUS: reason=engine_timeout exit=2\n2026-10-09T12:10:48.4414574Z   DETECTION_AGENTIC_EXECUTION_OUTCOME: failure\n2026-10-09T12:10:48.4415229Z   GH_AW_DETECTION_CONTINUE_ON_ERROR: false\n2026-10-09T12:10:48.4546476Z 📋 detection execution outcome: \"failure\"\n2026-10-09T12:10:48.4566162Z    [1262] THREAT_DETECTION_STATUS: reason=engine_timeout exit=2\n2026-10-09T12:10:48.4567315Z ⚠️  Threat Detection Engine Failure — The analysis engine could not complete. This is a tooling failure, not a security finding.\n2026-10-09T12:10:48.4578120Z ##[error]ERR_SYSTEM: ❌ Detection result file not found at: /tmp/gh-aw/threat-detection/detection_result.json\n"

    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()

    raw_files = frozen_git_raw_files(root, commit)
    expect({path for path in raw_files if path.startswith(operation_root)} == set(pins),
           "post-model fixture changed its exact operation document set")
    expect(all(blob(raw_files[path]) == sha for path, sha in pins.items()),
           "post-model fixture changed frozen journal or sidecar bytes")
    snapshot = StoreSnapshot(commit, {path: json.loads(raw) for path, raw in raw_files.items()})
    events = operation_events(snapshot, operation_id)
    expect(events == provider.frozen_events and len(events) == 30,
           "post-model fixture altered the original thirty-event prefix")
    projection = vertical_projection(snapshot, operation_id)
    expect(projection["generation"] == 1 and projection["status"] == "WAITING_EXTERNAL"
           and projection["expected_feature_revision"] == 3,
           "post-model fixture changed its original code-review wait")
    first_paths = {path for path in raw_files if path.startswith(first_root)}
    expect(first_paths == {first_root + "authorization.json", first_root + "create-claim.json"},
           "ordinal-one history must retain its consumed claim without a fabricated seal")
    authorization = json.loads(raw_files[first_root + "authorization.json"])
    claim = json.loads(raw_files[first_root + "create-claim.json"])
    expect(authorization["ordinal"] == claim["ordinal"] == 1
           and claim["create_consumed"] is True
           and authorization["physical_key"] == claim["physical_key"]
               == "dispatch-d653abeb44f20430dc9a5600150717ff76dd57bb",
           "post-model fixture changed the existing ordinal-one authority")
    expect(not any(path.startswith(second_root) for path in raw_files),
           "post-model fixture must not seed the transition under test")
    expect(observed["run"]["id"] == 37927328438 and observed["run"]["run_attempt"] == 1
           and observed["run"]["conclusion"] == "failure"
           and observed["run"]["head_sha"] == "193d96474529556cc0d805bb9be2b0a96909777b",
           "post-model observation identity drift")
    by_name = {job["name"]: job for job in observed["jobs"]["jobs"]}
    expect(len(by_name) == observed["jobs"]["total_count"] == 5
           and by_name["agent"]["conclusion"] == "success"
           and by_name["detection"]["conclusion"] == "failure"
           and by_name["safe_outputs"]["conclusion"] == "skipped"
           and not by_name["safe_outputs"]["steps"],
           "post-model observation cannot imply Safe Outputs execution")
    expect(any(step["name"] == "Execute GitHub Copilot CLI" and step["conclusion"] == "success"
               for step in by_name["agent"]["steps"])
           and any(step["name"] == "Conclude threat detection" and step["conclusion"] == "failure"
                   for step in by_name["detection"]["steps"])
           and not observed["comments"]
           and not any(row["name"] == "safe-outputs-items" for row in observed["artifacts"]["artifacts"]),
           "post-model fixture no longer represents the observed failed execution")

    provider.snapshot = snapshot
    provider.frozen_events = deepcopy(events)
    provider.frozen_operation_raw_files = {
        path: bytes(raw_files[path]) for path in pins}
    provider.ordinal_one_raw_files = {
        path: bytes(raw_files[path]) for path in first_paths}
    provider.historical_post_model_reviewer_run = deepcopy(observed["run"])
    provider.historical_post_model_reviewer_jobs = deepcopy(observed["jobs"])
    provider.historical_post_model_detector_log = detector_log
    provider.historical_post_model_failure_issue = deepcopy(failure_issue)
    state = provider.state
    state["reviewer_post_model_failure_issue"] = failure_issue
    state["reviewer_post_model_observed"] = observed
    state["reviewer_post_model_detector_log"] = detector_log
    state["reviewer_post_model_predecessor_commit"] = commit
    old_http = provider.http

    def response(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def paginate(rows, query):
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["100"])[0])
        expect(page > 0 and 1 <= per_page <= 100, "malformed provider pagination")
        return deepcopy(rows[(page - 1) * per_page:page * per_page])

    original_pre_model_agent_log = "﻿2026-10-09T10:30:19.8553864Z Current runner version: '2.337.0'\n2026-10-09T10:30:19.8580958Z ##[group]Runner Image Provisioner\n2026-10-09T10:30:19.8581922Z Hosted Compute Agent\n2026-10-09T10:30:19.8582646Z Version: 20261002.596\n2026-10-09T10:30:19.8583321Z Commit: c3d12f3313a95f25162a1503be624f3b0617f8c0\n2026-10-09T10:30:19.8584581Z Build Date: 2026-10-02T22:28:27Z\n2026-10-09T10:30:19.8585437Z Worker ID: {51dbbef9-cbf1-4090-8ccb-460e703db530}\n2026-10-09T10:30:19.8586202Z Region: westus3\n2026-10-09T10:30:19.8586876Z Cloud: Azure\n2026-10-09T10:30:19.8587494Z ##[endgroup]\n2026-10-09T10:30:19.8589163Z ##[group]Operating System\n2026-10-09T10:30:19.8589953Z Ubuntu\n2026-10-09T10:30:19.8590497Z 24.04.5\n2026-10-09T10:30:19.8591122Z LTS\n2026-10-09T10:30:19.8591703Z ##[endgroup]\n2026-10-09T10:30:19.8592408Z ##[group]Runner Image\n2026-10-09T10:30:19.8593056Z Image: ubuntu-24.04\n2026-10-09T10:30:19.8593666Z Version: 20261004.327.1\n2026-10-09T10:30:19.8595280Z Included Software: https://github.com/actions/runner-images/blob/ubuntu24/20261004.327/images/ubuntu/Ubuntu2404-Readme.md\n2026-10-09T10:30:19.8597008Z Image Release: https://github.com/actions/runner-images/releases/tag/ubuntu24%2F20261004.327\n2026-10-09T10:30:19.8598018Z ##[endgroup]\n2026-10-09T10:30:19.8599435Z ##[group]GITHUB_TOKEN Permissions\n2026-10-09T10:30:19.8601712Z Contents: read\n2026-10-09T10:30:19.8602947Z Issues: read\n2026-10-09T10:30:19.8603597Z Metadata: read\n2026-10-09T10:30:19.8604640Z PullRequests: read\n2026-10-09T10:30:19.8605373Z ##[endgroup]\n2026-10-09T10:30:19.8608030Z Secret source: Actions\n2026-10-09T10:30:19.8609203Z Cache mode: write\n2026-10-09T10:30:19.8610167Z Prepare workflow directory\n2026-10-09T10:30:19.9246735Z Prepare all required actions\n2026-10-09T10:30:19.9297903Z Getting action download info\n2026-10-09T10:30:20.2612038Z Download action repository 'github/gh-aw-actions@924af5fdc64061cfbf66fb584c8b07e2ac230c60' (SHA:924af5fdc64061cfbf66fb584c8b07e2ac230c60)\n2026-10-09T10:30:20.5760407Z Download action repository 'actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1' (SHA:bcd2ba49218906704ab6c1aa796996da409d3eb1)\n2026-10-09T10:30:21.1236549Z Download action repository 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1' (SHA:3d3c42e5aac5ba805825da76410c181273ba90b1)\n2026-10-09T10:30:21.1603557Z Download action repository 'actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3' (SHA:3a2844b7e9c422d3c10d287c895573f7108da1b3)\n2026-10-09T10:30:21.7468828Z Download action repository 'actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c' (SHA:3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c)\n2026-10-09T10:30:22.4199879Z Download action repository 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a' (SHA:043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)\n2026-10-09T10:30:22.6905867Z Complete job name: agent\n2026-10-09T10:30:22.7665499Z ##[group]Run github/gh-aw-actions/setup@924af5fdc64061cfbf66fb584c8b07e2ac230c60\n2026-10-09T10:30:22.7666399Z with:\n2026-10-09T10:30:22.7666693Z   destination: /home/runner/work/_temp/gh-aw/actions\n2026-10-09T10:30:22.7667053Z   job-name: agent\n2026-10-09T10:30:22.7667301Z   trace-id: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:22.7667605Z   parent-span-id: 8f37d5d170fe5671\n2026-10-09T10:30:22.7667895Z   safe-output-artifact-client: false\n2026-10-09T10:30:22.7668697Z env:\n2026-10-09T10:30:22.7668943Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:22.7669306Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:22.7670273Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:22.7671147Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:22.7671491Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:22.7671804Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:22.7672051Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:22.7672287Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:22.7672540Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:22.7673016Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:22.7673313Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:22.7673640Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:22.7674070Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:22.7674338Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:22.7674570Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:22.7674808Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:22.7675054Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:22.7675310Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:22.7675607Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:22.7676040Z   GH_AW_SETUP_WORKFLOW_NAME: AI-SDLC gh-aw Code Reviewer (deepseek)\n2026-10-09T10:30:22.7676726Z   GH_AW_CURRENT_WORKFLOW_REF: DREAM-XIN/ai-sdlc/.github/workflows/ai-sdlc-gh-aw-reviewer-deepseek.lock.yml@refs/heads/main\n2026-10-09T10:30:22.7677320Z   GH_AW_INFO_VERSION: 1.0.87\n2026-10-09T10:30:22.7677573Z   GH_AW_INFO_AWF_VERSION: v0.28.23\n2026-10-09T10:30:22.7677849Z   GH_AW_INFO_ENGINE_ID: copilot\n2026-10-09T10:30:22.7678173Z ##[endgroup]\n2026-10-09T10:30:24.5206703Z Successfully copied 515 files to /home/runner/work/_temp/gh-aw/actions\n2026-10-09T10:30:24.8003239Z Successfully copied 26 mcp-scripts files to /home/runner/work/_temp/gh-aw/mcp-scripts\n2026-10-09T10:30:24.9325589Z Successfully copied 88 safe-outputs files to /home/runner/work/_temp/gh-aw/safeoutputs\n2026-10-09T10:30:24.9462170Z [info] [otlp] INPUT_TRACE_ID=ef349964e8428d7901e62b0b1baad9fb (will reuse activation trace)\n2026-10-09T10:30:24.9463274Z [info] [otlp] INPUT_PARENT_SPAN_ID=8f37d5d170fe5671 (will parent setup span)\n2026-10-09T10:30:24.9465843Z [info] [otlp] no OTLP endpoints have usable credentials, skipping setup span\n2026-10-09T10:30:24.9490570Z [info] [otlp] resolved trace-id=ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:24.9491731Z [info] [otlp] trace-id=ef349964e8428d7901e62b0b1baad9fb written to GITHUB_OUTPUT\n2026-10-09T10:30:24.9492651Z [info] [otlp] span-id=61587664f44dd440 written to GITHUB_OUTPUT\n2026-10-09T10:30:24.9493337Z [info] [otlp] parent-span-id=8f37d5d170fe5671 written to GITHUB_OUTPUT\n2026-10-09T10:30:24.9494534Z [info] [otlp] GITHUB_AW_OTEL_TRACE_ID written to GITHUB_ENV\n2026-10-09T10:30:24.9495718Z [info] [otlp] GITHUB_AW_OTEL_PARENT_SPAN_ID written to GITHUB_ENV\n2026-10-09T10:30:24.9496491Z [info] [otlp] GITHUB_AW_OTEL_JOB_START_MS written to GITHUB_ENV\n2026-10-09T10:30:24.9683714Z ##[group]Run if [ -z \"${RUNNER_TOOL_CACHE:-}\" ]; then\n2026-10-09T10:30:24.9684432Z \u001b[36;1mif [ -z \"${RUNNER_TOOL_CACHE:-}\" ]; then\u001b[0m\n2026-10-09T10:30:24.9684913Z \u001b[36;1m  echo \"RUNNER_TOOL_CACHE=${GH_AW_RUNNER_TOOL_CACHE}\" >> \"$GITHUB_ENV\"\u001b[0m\n2026-10-09T10:30:24.9685307Z \u001b[36;1mfi\u001b[0m\n2026-10-09T10:30:24.9685517Z \u001b[36;1m{\u001b[0m\n2026-10-09T10:30:24.9685876Z \u001b[36;1m  echo \"GH_AW_SAFE_OUTPUTS=${RUNNER_TEMP}/gh-aw/safeoutputs/outputs.jsonl\"\u001b[0m\n2026-10-09T10:30:24.9686470Z \u001b[36;1m  echo \"GH_AW_SAFE_OUTPUTS_CONFIG_PATH=${RUNNER_TEMP}/gh-aw/safeoutputs/config.json\"\u001b[0m\n2026-10-09T10:30:24.9687101Z \u001b[36;1m  echo \"GH_AW_SAFE_OUTPUTS_TOOLS_PATH=${RUNNER_TEMP}/gh-aw/safeoutputs/tools.json\"\u001b[0m\n2026-10-09T10:30:24.9687556Z \u001b[36;1m} >> \"$GITHUB_OUTPUT\"\u001b[0m\n2026-10-09T10:30:24.9981898Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:24.9982325Z env:\n2026-10-09T10:30:24.9982636Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:24.9983181Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:24.9984794Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:24.9986147Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:24.9986576Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:24.9987061Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:24.9987448Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:24.9987783Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:24.9988147Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:24.9988747Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:24.9989175Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:24.9989639Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:24.9990009Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:24.9990395Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:24.9990745Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:24.9991099Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:24.9991435Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:24.9991763Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:24.9992190Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:24.9992758Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:24.9993216Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:24.9993561Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:24.9994135Z   GH_AW_RUNNER_TOOL_CACHE: /opt/hostedtoolcache\n2026-10-09T10:30:24.9994596Z ##[endgroup]\n2026-10-09T10:30:25.0143338Z ##[group]Run bash \"${RUNNER_TEMP}/gh-aw/actions/mask_otlp_headers.sh\"\n2026-10-09T10:30:25.0144176Z \u001b[36;1mbash \"${RUNNER_TEMP}/gh-aw/actions/mask_otlp_headers.sh\"\u001b[0m\n2026-10-09T10:30:25.0204194Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:25.0204492Z env:\n2026-10-09T10:30:25.0204727Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.0205102Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.0206156Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.0207131Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.0207447Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.0207787Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.0208062Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.0208321Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.0208629Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.0208882Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.0209206Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.0209554Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.0209829Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.0210118Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.0210382Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.0210724Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.0211008Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.0211255Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.0211583Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.0212012Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.0212416Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.0212770Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.0213079Z ##[endgroup]\n2026-10-09T10:30:25.0379444Z ##[group]Run bash \"${RUNNER_TEMP}/gh-aw/actions/check_otlp_default_credentials.sh\"\n2026-10-09T10:30:25.0380119Z \u001b[36;1mbash \"${RUNNER_TEMP}/gh-aw/actions/check_otlp_default_credentials.sh\"\u001b[0m\n2026-10-09T10:30:25.0440196Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:25.0440492Z env:\n2026-10-09T10:30:25.0440732Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.0441104Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.0442149Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.0443126Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.0443441Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.0443811Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.0444400Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.0444661Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.0444982Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.0445246Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.0445568Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.0446121Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.0446413Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.0446699Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.0446964Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.0447233Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.0447500Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.0447752Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.0448086Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.0448525Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.0448947Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.0449306Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.0449625Z ##[endgroup]\n2026-10-09T10:30:25.0546463Z OTLP telemetry is not configured (GH_AW_DEFAULT_OTLP_ENDPOINT is empty); skipping export.\n2026-10-09T10:30:25.0726733Z ##[group]Run actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1\n2026-10-09T10:30:25.0727238Z with:\n2026-10-09T10:30:25.0727471Z   client-id: Iv23libojxnnuF43petx\n2026-10-09T10:30:25.0727742Z   owner: dream-xin\n2026-10-09T10:30:25.0727965Z   repositories: ai-sdlc\n2026-10-09T10:30:25.0728256Z   github-api-url: https://api.github.com\n2026-10-09T10:30:25.0728570Z   permission-contents: read\n2026-10-09T10:30:25.0728838Z   permission-issues: read\n2026-10-09T10:30:25.0729094Z   permission-pull-requests: read\n2026-10-09T10:30:25.0729365Z   skip-token-revoke: false\n2026-10-09T10:30:25.0729603Z env:\n2026-10-09T10:30:25.0729812Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.0730130Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.0731068Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.0731914Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.0732198Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.0732516Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.0732762Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.0732991Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.0733232Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.0733460Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.0733749Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.0734257Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.0734509Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.0734765Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.0735007Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.0735246Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.0735481Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.0735706Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.0735995Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.0736395Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.0736776Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.0737129Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.0737408Z ##[endgroup]\n2026-10-09T10:30:25.1467564Z Error: The 'private-key' input must be set to a non-empty string. If using a secret or variable, ensure it is available in this workflow context.\n2026-10-09T10:30:25.1469139Z     at run (/home/runner/work/_actions/actions/create-github-app-token/bcd2ba49218906704ab6c1aa796996da409d3eb1/dist/main.cjs:23429:11)\n2026-10-09T10:30:25.1471066Z     at Object.<anonymous> (/home/runner/work/_actions/actions/create-github-app-token/bcd2ba49218906704ab6c1aa796996da409d3eb1/dist/main.cjs:23449:20)\n2026-10-09T10:30:25.1472223Z     at Module._compile (node:internal/modules/cjs/loader:1872:14)\n2026-10-09T10:30:25.1472855Z     at Object..js (node:internal/modules/cjs/loader:2003:10)\n2026-10-09T10:30:25.1473727Z     at Module.load (node:internal/modules/cjs/loader:1594:32)\n2026-10-09T10:30:25.1475102Z     at Module._load (node:internal/modules/cjs/loader:1396:12)\n2026-10-09T10:30:25.1476479Z     at wrapModuleLoad (node:internal/modules/cjs/loader:255:19)\n2026-10-09T10:30:25.1478032Z     at Module.executeUserEntryPoint [as runMain] (node:internal/modules/run_main:154:5)\n2026-10-09T10:30:25.1479405Z     at node:internal/main/run_main_module:33:47\n2026-10-09T10:30:25.1528071Z ##[error]The 'private-key' input must be set to a non-empty string. If using a secret or variable, ensure it is available in this workflow context.\n2026-10-09T10:30:25.1894841Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:25.1895313Z with:\n2026-10-09T10:30:25.1896532Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'detect_agent_errors.cjs'));\nawait main();\n\n2026-10-09T10:30:25.1900219Z   github-token: ***\n2026-10-09T10:30:25.1900477Z   debug: false\n2026-10-09T10:30:25.1900716Z   user-agent: actions/github-script\n2026-10-09T10:30:25.1901005Z   result-encoding: json\n2026-10-09T10:30:25.1901233Z   retries: 0\n2026-10-09T10:30:25.1901475Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:25.1901779Z env:\n2026-10-09T10:30:25.1901986Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.1902355Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.1903265Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.1904272Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.1904571Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.1904880Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.1905133Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.1905362Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.1905614Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.1905848Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.1906128Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.1906437Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.1906685Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.1906939Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.1907174Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.1907418Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.1907646Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.1907867Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.1908161Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.1908545Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.1908903Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.1909217Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.1909526Z   GH_AW_AGENTIC_EXECUTION_OUTCOME: skipped\n2026-10-09T10:30:25.1909827Z   GH_AW_ENGINE_STEP_TIMEOUT_MINUTES: 20\n2026-10-09T10:30:25.1910092Z ##[endgroup]\n2026-10-09T10:30:25.2953445Z [detect-agent-errors] Log file not found: /tmp/gh-aw/agent-stdio.log\n2026-10-09T10:30:25.3099044Z ##[group]Run bash \"${RUNNER_TEMP}/gh-aw/actions/copy_copilot_session_state.sh\"\n2026-10-09T10:30:25.3099680Z \u001b[36;1mbash \"${RUNNER_TEMP}/gh-aw/actions/copy_copilot_session_state.sh\"\u001b[0m\n2026-10-09T10:30:25.3161965Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:25.3162268Z env:\n2026-10-09T10:30:25.3162505Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.3162891Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.3164290Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.3165278Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.3165602Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.3166183Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.3166479Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.3166758Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.3167081Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.3167343Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.3167665Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.3168040Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.3168333Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.3168625Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.3168897Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.3169168Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.3169437Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.3169697Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.3170036Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.3170484Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.3170899Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.3171262Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.3171579Z ##[endgroup]\n2026-10-09T10:30:25.3297316Z No session-state directory found at /home/runner/.copilot/session-state\n2026-10-09T10:30:25.3337433Z ##[group]Run bash \"${RUNNER_TEMP}/gh-aw/actions/stop_mcp_gateway.sh\" \"$GATEWAY_PID\"\n2026-10-09T10:30:25.3338073Z \u001b[36;1mbash \"${RUNNER_TEMP}/gh-aw/actions/stop_mcp_gateway.sh\" \"$GATEWAY_PID\"\u001b[0m\n2026-10-09T10:30:25.3400844Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:25.3401180Z env:\n2026-10-09T10:30:25.3401436Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.3401840Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.3402990Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.3404368Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.3404730Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.3405130Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.3405439Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.3405725Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.3406056Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.3406333Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.3406676Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.3407056Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.3407359Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.3407666Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.3407948Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.3408234Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.3408520Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.3408795Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.3409132Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.3409551Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.3409931Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.3410286Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.3410587Z   MCP_GATEWAY_PORT: \n2026-10-09T10:30:25.3410825Z   MCP_GATEWAY_AGENT_ID: \n2026-10-09T10:30:25.3411075Z   GATEWAY_PID: \n2026-10-09T10:30:25.3411294Z ##[endgroup]\n2026-10-09T10:30:25.3508528Z Gateway PID not provided\n2026-10-09T10:30:25.3509329Z Gateway may not have been started or PID was not captured\n2026-10-09T10:30:25.3510010Z Cleaning up awmg-mcpg container...\n2026-10-09T10:30:25.4830602Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:25.4831077Z with:\n2026-10-09T10:30:25.4832267Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'redact_secrets.cjs'));\nawait main();\n\n2026-10-09T10:30:25.4836298Z   github-token: ***\n2026-10-09T10:30:25.4836760Z   debug: false\n2026-10-09T10:30:25.4837002Z   user-agent: actions/github-script\n2026-10-09T10:30:25.4837291Z   result-encoding: json\n2026-10-09T10:30:25.4837522Z   retries: 0\n2026-10-09T10:30:25.4837768Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:25.4838073Z env:\n2026-10-09T10:30:25.4838283Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.4838629Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.4839576Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.4840426Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.4840721Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.4841040Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.4841306Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.4841541Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.4841796Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.4842038Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.4842340Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.4842658Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.4842913Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.4843173Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.4843425Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.4843677Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.4844202Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.4844468Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.4844772Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.4845176Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.4845542Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.4845863Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.4846449Z   GH_AW_SECRET_NAMES: AI_SDLC_RUNTIME_APP_PRIVATE_KEY,DEEPSEEK_API_KEY,GH_AW_GITHUB_MCP_SERVER_TOKEN,GH_AW_GITHUB_TOKEN,GITHUB_TOKEN\n2026-10-09T10:30:25.4847051Z   SECRET_AI_SDLC_RUNTIME_APP_PRIVATE_KEY: \n2026-10-09T10:30:25.4847446Z   SECRET_DEEPSEEK_API_KEY: ***\n2026-10-09T10:30:25.4847713Z   SECRET_GH_AW_GITHUB_MCP_SERVER_TOKEN: \n2026-10-09T10:30:25.4848337Z   SECRET_GH_AW_GITHUB_TOKEN: ***\n2026-10-09T10:30:25.4850886Z   SECRET_GITHUB_TOKEN: ***\n2026-10-09T10:30:25.4851135Z ##[endgroup]\n2026-10-09T10:30:25.5993334Z Starting secret redaction in /tmp/gh-aw and /home/runner/work/_temp/gh-aw directories\n2026-10-09T10:30:25.6014919Z Found 3 custom secret(s) to redact\n2026-10-09T10:30:25.6015696Z Scanning for built-in credential patterns and custom secrets\n2026-10-09T10:30:25.6049087Z Found 101 file(s) to scan for secrets (1 in /tmp/gh-aw, 100 in /home/runner/work/_temp/gh-aw)\n2026-10-09T10:30:25.6120187Z Secret redaction complete: no secrets found\n2026-10-09T10:30:25.6259086Z ##[group]Run bash \"${RUNNER_TEMP}/gh-aw/actions/append_agent_step_summary.sh\"\n2026-10-09T10:30:25.6259677Z \u001b[36;1mbash \"${RUNNER_TEMP}/gh-aw/actions/append_agent_step_summary.sh\"\u001b[0m\n2026-10-09T10:30:25.6322596Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:25.6322882Z env:\n2026-10-09T10:30:25.6323097Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.6323449Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.6324835Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.6325786Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.6326086Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.6326408Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.6326668Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.6326906Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.6327200Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.6327437Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.6327736Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.6328296Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.6328554Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.6328820Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.6329057Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.6329303Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.6329540Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.6329773Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.6330070Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.6330474Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.6330868Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.6331204Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.6331493Z ##[endgroup]\n2026-10-09T10:30:25.6477144Z ##[group]Run mkdir -p /tmp/gh-aw\n2026-10-09T10:30:25.6477487Z \u001b[36;1mmkdir -p /tmp/gh-aw\u001b[0m\n2026-10-09T10:30:25.6477887Z \u001b[36;1mcp \"$GH_AW_SAFE_OUTPUTS\" /tmp/gh-aw/safeoutputs.jsonl 2>/dev/null || true\u001b[0m\n2026-10-09T10:30:25.6563538Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:25.6564212Z env:\n2026-10-09T10:30:25.6564538Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.6565046Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.6566447Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.6567524Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.6567829Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.6568164Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.6568663Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.6569140Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.6569572Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.6569955Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.6570462Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.6571080Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.6571579Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.6572048Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.6572478Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.6572933Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.6573384Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.6573732Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.6574605Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.6575255Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.6575832Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.6576155Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.6576582Z   GH_AW_SAFE_OUTPUTS: /home/runner/work/_temp/gh-aw/safeoutputs/outputs.jsonl\n2026-10-09T10:30:25.6576980Z ##[endgroup]\n2026-10-09T10:30:25.6793784Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:25.6794547Z with:\n2026-10-09T10:30:25.6795731Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'collect_ndjson_output.cjs'));\nawait main();\n\n2026-10-09T10:30:25.6799338Z   github-token: ***\n2026-10-09T10:30:25.6799570Z   debug: false\n2026-10-09T10:30:25.6799804Z   user-agent: actions/github-script\n2026-10-09T10:30:25.6800084Z   result-encoding: json\n2026-10-09T10:30:25.6800310Z   retries: 0\n2026-10-09T10:30:25.6800546Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:25.6800843Z env:\n2026-10-09T10:30:25.6801052Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:25.6801404Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:25.6802303Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:25.6803304Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:25.6803585Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:25.6803899Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:25.6804289Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:25.6804517Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:25.6804762Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:25.6804993Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:25.6805278Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:25.6805588Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:25.6805831Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:25.6806077Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:25.6806315Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:25.6806553Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:25.6806779Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:25.6806997Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:25.6807288Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:25.6807675Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:25.6808025Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:25.6808337Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:25.6808740Z   GH_AW_SAFE_OUTPUTS: /home/runner/work/_temp/gh-aw/safeoutputs/outputs.jsonl\n2026-10-09T10:30:25.6814596Z   GH_AW_ALLOWED_DOMAINS: api.deepseek.com,api.snapcraft.io,archive.ubuntu.com,azure.archive.ubuntu.com,crl.geotrust.com,crl.globalsign.com,crl.identrust.com,crl.sectigo.com,crl.thawte.com,crl.usertrust.com,crl.verisign.com,crl3.digicert.com,crl4.digicert.com,crls.ssl.com,deepseek.com,json-schema.org,json.schemastore.org,keyserver.ubuntu.com,ocsp.digicert.com,ocsp.geotrust.com,ocsp.globalsign.com,ocsp.identrust.com,ocsp.sectigo.com,ocsp.ssl.com,ocsp.thawte.com,ocsp.usertrust.com,ocsp.verisign.com,packagecloud.io,packages.cloud.google.com,packages.microsoft.com,ppa.launchpad.net,s.symcb.com,s.symcd.com,security.ubuntu.com,ts-crl.ws.symantec.com,ts-ocsp.ws.symantec.com,www.googleapis.com\n2026-10-09T10:30:25.6820033Z   GITHUB_SERVER_URL: https://github.com\n2026-10-09T10:30:25.6820336Z   GITHUB_API_URL: https://api.github.com\n2026-10-09T10:30:25.6820613Z ##[endgroup]\n2026-10-09T10:30:25.7927434Z Found 1 unique mentions in text\n2026-10-09T10:30:26.1755890Z Cached 1 recent collaborators for optimistic resolution\n2026-10-09T10:30:26.3491780Z GET /users/github-actions - 404 with id 6028:1A8637:28ADFA4:8749A42:6AC8C242 in 173ms\n2026-10-09T10:30:26.3492648Z Resolved 1 mentions via individual API calls\n2026-10-09T10:30:26.3492998Z Total allowed mentions: 0\n2026-10-09T10:30:26.3505176Z [OUTPUT COLLECTOR] No allowed mentions - all mentions will be escaped\n2026-10-09T10:30:26.3506339Z [INGESTION] Reading config from: /home/runner/work/_temp/gh-aw/safeoutputs/config.json\n2026-10-09T10:30:26.3506886Z [INGESTION] Raw config content: {}\n2026-10-09T10:30:26.3507099Z \n2026-10-09T10:30:26.3507219Z [INGESTION] Parsed config keys: []\n2026-10-09T10:30:26.3507700Z [INGESTION] Output file path: /home/runner/work/_temp/gh-aw/safeoutputs/outputs.jsonl\n2026-10-09T10:30:26.3509085Z Output file does not exist: /home/runner/work/_temp/gh-aw/safeoutputs/outputs.jsonl — no safe-output items were emitted; treating as empty collection (graceful no-op)\n2026-10-09T10:30:26.3580259Z Stored empty collection to: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:26.3647023Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:26.3647453Z with:\n2026-10-09T10:30:26.3648633Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'parse_copilot_log.cjs'));\nawait main();\n\n2026-10-09T10:30:26.3652241Z   github-token: ***\n2026-10-09T10:30:26.3652464Z   debug: false\n2026-10-09T10:30:26.3652869Z   user-agent: actions/github-script\n2026-10-09T10:30:26.3653152Z   result-encoding: json\n2026-10-09T10:30:26.3653370Z   retries: 0\n2026-10-09T10:30:26.3653608Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:26.3654185Z env:\n2026-10-09T10:30:26.3654405Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:26.3654735Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:26.3655664Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:26.3656507Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:26.3656787Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:26.3657092Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:26.3657334Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:26.3657561Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:26.3657806Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:26.3658033Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:26.3658314Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:26.3658623Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:26.3658867Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:26.3659113Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:26.3659348Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:26.3659586Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:26.3659811Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:26.3660030Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:26.3660316Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:26.3660701Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:26.3661050Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:26.3661362Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:26.3661675Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/sandbox/agent/logs/\n2026-10-09T10:30:26.3662098Z   GH_AW_SAFE_OUTPUTS: /home/runner/work/_temp/gh-aw/safeoutputs/outputs.jsonl\n2026-10-09T10:30:26.3662494Z ##[endgroup]\n2026-10-09T10:30:26.4696796Z Log path not found: /tmp/gh-aw/sandbox/agent/logs/\n2026-10-09T10:30:26.4878294Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:26.4878971Z with:\n2026-10-09T10:30:26.4881024Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'parse_mcp_gateway_log.cjs'));\nawait main();\n\n2026-10-09T10:30:26.4887921Z   github-token: ***\n2026-10-09T10:30:26.4888284Z   debug: false\n2026-10-09T10:30:26.4888642Z   user-agent: actions/github-script\n2026-10-09T10:30:26.4889093Z   result-encoding: json\n2026-10-09T10:30:26.4889457Z   retries: 0\n2026-10-09T10:30:26.4889841Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:26.4890326Z env:\n2026-10-09T10:30:26.4890667Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:26.4891237Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:26.4892801Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:26.4894490Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:26.4894957Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:26.4895471Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:26.4895878Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:26.4896249Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:26.4896650Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:26.4897020Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:26.4897493Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:26.4898009Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:26.4898412Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:26.4899059Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:26.4899453Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:26.4899848Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:26.4900220Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:26.4900585Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:26.4901059Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:26.4901702Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:26.4902293Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:26.4902812Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:26.4903336Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:26.4903836Z ##[endgroup]\n2026-10-09T10:30:26.6055961Z ##[group]=== Listing All Gateway-Related Files ===\n2026-10-09T10:30:26.6057789Z ##[group]📁 Directory: /tmp/gh-aw/mcp-logs\n2026-10-09T10:30:26.6061470Z ##[notice]Directory does not exist: /tmp/gh-aw/mcp-logs\n2026-10-09T10:30:26.6063249Z ##[endgroup]\n2026-10-09T10:30:26.6064246Z ##[endgroup]\n2026-10-09T10:30:26.6064965Z No gateway.jsonl or rpc-messages.jsonl found for steering or DIFC_FILTERED scanning\n2026-10-09T10:30:26.6065921Z No gateway.log found at: /tmp/gh-aw/mcp-logs/gateway.log\n2026-10-09T10:30:26.6066631Z No stderr.log found at: /tmp/gh-aw/mcp-logs/stderr.log\n2026-10-09T10:30:26.6067452Z No gateway.md found at: /tmp/gh-aw/mcp-logs/gateway.md, falling back to log files\n2026-10-09T10:30:26.6068264Z MCP gateway log files are empty or missing\n2026-10-09T10:30:26.6241880Z ##[group]Run bash \"${RUNNER_TEMP}/gh-aw/actions/print_firewall_logs.sh\" --rootless\n2026-10-09T10:30:26.6242866Z \u001b[36;1mbash \"${RUNNER_TEMP}/gh-aw/actions/print_firewall_logs.sh\" --rootless\u001b[0m\n2026-10-09T10:30:26.6323032Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:26.6323445Z env:\n2026-10-09T10:30:26.6323773Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:26.6324574Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:26.6326141Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:26.6327614Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:26.6328073Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:26.6328577Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:26.6328976Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:26.6329349Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:26.6329819Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:26.6330190Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:26.6330655Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:26.6331169Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:26.6331579Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:26.6331992Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:26.6332371Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:26.6332756Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:26.6333126Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:26.6333492Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:26.6334195Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:26.6334842Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:26.6335432Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:26.6335956Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:26.6336478Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:26.6337038Z   AWF_LOGS_DIR: /tmp/gh-aw/sandbox/firewall/logs\n2026-10-09T10:30:26.6337523Z ##[endgroup]\n2026-10-09T10:30:26.6704427Z AWF binary not installed, skipping firewall log summary\n2026-10-09T10:30:26.6707222Z WARNING: Squid access.log not found under /tmp/gh-aw/sandbox/firewall/logs; the MCP gateway did not complete startup. Inspect the Start MCP Gateway step diagnostics.\n2026-10-09T10:30:26.6784460Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:26.6784915Z with:\n2026-10-09T10:30:26.6786107Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'parse_token_usage.cjs'));\nawait main();\n\n2026-10-09T10:30:26.6789881Z   github-token: ***\n2026-10-09T10:30:26.6790114Z   debug: false\n2026-10-09T10:30:26.6790332Z   user-agent: actions/github-script\n2026-10-09T10:30:26.6790610Z   result-encoding: json\n2026-10-09T10:30:26.6790833Z   retries: 0\n2026-10-09T10:30:26.6791073Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:26.6791376Z env:\n2026-10-09T10:30:26.6791575Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:26.6791897Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:26.6792830Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:26.6793669Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:26.6794266Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:26.6794617Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:26.6794862Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:26.6795086Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:26.6795322Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:26.6795543Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:26.6795831Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:26.6796139Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:26.6796385Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:26.6796629Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:26.6796868Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:26.6797113Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:26.6797341Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:26.6797555Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:26.6797837Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:26.6798221Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:26.6798570Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:26.6798875Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:26.6799184Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:26.6799480Z ##[endgroup]\n2026-10-09T10:30:26.7848485Z No token usage data found, skipping summary\n2026-10-09T10:30:26.8015524Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:26.8015957Z with:\n2026-10-09T10:30:26.8017149Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'awf_reflect_summary.cjs'));\nawait main();\n\n2026-10-09T10:30:26.8020736Z   github-token: ***\n2026-10-09T10:30:26.8020983Z   debug: false\n2026-10-09T10:30:26.8021213Z   user-agent: actions/github-script\n2026-10-09T10:30:26.8021493Z   result-encoding: json\n2026-10-09T10:30:26.8021719Z   retries: 0\n2026-10-09T10:30:26.8022164Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:26.8022471Z env:\n2026-10-09T10:30:26.8022679Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:26.8023044Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:26.8024158Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:26.8025009Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:26.8025299Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:26.8025618Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:26.8025866Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:26.8026098Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:26.8026547Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:26.8026786Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:26.8027080Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:26.8027402Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:26.8027655Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:26.8027914Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:26.8028151Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:26.8028395Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:26.8028627Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:26.8028857Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:26.8029149Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:26.8029541Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:26.8029901Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:26.8030231Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:26.8030556Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:26.8030856Z ##[endgroup]\n2026-10-09T10:30:26.9222944Z AWF reflect data not available (AWF not enabled or /reflect not reachable), skipping summary\n2026-10-09T10:30:26.9447526Z ##[group]Run actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3\n2026-10-09T10:30:26.9448125Z with:\n2026-10-09T10:30:26.9449407Z   script: const path = require('path');\nconst actionsDir = path.join(process.env.RUNNER_TEMP, 'gh-aw', 'actions');\nconst { setupGlobals } = require(path.join(actionsDir, 'setup_globals.cjs'));\nsetupGlobals(core, github, context, exec, io, getOctokit);\nconst { main } = require(path.join(actionsDir, 'generate_observability_summary.cjs'));\nawait main(core);\n\n2026-10-09T10:30:26.9455061Z   github-token: ***\n2026-10-09T10:30:26.9455445Z   debug: false\n2026-10-09T10:30:26.9455805Z   user-agent: actions/github-script\n2026-10-09T10:30:26.9456095Z   result-encoding: json\n2026-10-09T10:30:26.9456435Z   retries: 0\n2026-10-09T10:30:26.9456805Z   retry-exempt-status-codes: 400,401,403,404,422\n2026-10-09T10:30:26.9457157Z env:\n2026-10-09T10:30:26.9457518Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:26.9458104Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:26.9459353Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:26.9460194Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:26.9460479Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:26.9460797Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:26.9461049Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:26.9461278Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:26.9461519Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:26.9461738Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:26.9462217Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:26.9462781Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:26.9463168Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:26.9463434Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:26.9463679Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:26.9464211Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:26.9464610Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:26.9464991Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:26.9465288Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:26.9465684Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:26.9466039Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:26.9466349Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:26.9466670Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:26.9466974Z ##[endgroup]\n2026-10-09T10:30:27.0520929Z Generated observability summary in step summary\n2026-10-09T10:30:27.0667458Z ##[group]Run if [ ! -f /tmp/gh-aw/agent_output.json ]; then\n2026-10-09T10:30:27.0667928Z \u001b[36;1mif [ ! -f /tmp/gh-aw/agent_output.json ]; then\u001b[0m\n2026-10-09T10:30:27.0668318Z \u001b[36;1m  echo '{\"items\":[]}' > /tmp/gh-aw/agent_output.json\u001b[0m\n2026-10-09T10:30:27.0668810Z \u001b[36;1mfi\u001b[0m\n2026-10-09T10:30:27.0733484Z shell: /usr/bin/bash -e {0}\n2026-10-09T10:30:27.0733761Z env:\n2026-10-09T10:30:27.0734361Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:27.0734821Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:27.0735774Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:27.0736630Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:27.0736923Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:27.0737239Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:27.0737543Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:27.0737771Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:27.0738009Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:27.0738235Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:27.0738526Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:27.0738850Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:27.0739094Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:27.0739344Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:27.0739571Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:27.0739811Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:27.0740033Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:27.0740252Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:27.0740540Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:27.0740940Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:27.0741301Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:27.0741620Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:27.0741951Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:27.0742263Z ##[endgroup]\n2026-10-09T10:30:27.0925656Z ##[group]Run actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a\n2026-10-09T10:30:27.0926106Z with:\n2026-10-09T10:30:27.0926336Z   name: agent-output-fallback\n2026-10-09T10:30:27.0927767Z   path: /tmp/gh-aw/agent_output.json\n/tmp/gh-aw/safeoutputs.jsonl\n/tmp/gh-aw/agent_execution.json\n/tmp/gh-aw/agent_usage.jsonl\n/tmp/gh-aw/agent_usage.json\n/tmp/gh-aw/sandbox/firewall-audit-logs/api-proxy-logs/token-usage.jsonl\n/tmp/gh-aw/sandbox/firewall/logs/api-proxy-logs/token-usage.jsonl\n/tmp/gh-aw/sandbox/firewall/audit/api-proxy-logs/token-usage.jsonl\n\n2026-10-09T10:30:27.0929215Z   if-no-files-found: ignore\n2026-10-09T10:30:27.0929470Z   compression-level: 6\n2026-10-09T10:30:27.0929698Z   overwrite: false\n2026-10-09T10:30:27.0929921Z   include-hidden-files: false\n2026-10-09T10:30:27.0930165Z   archive: true\n2026-10-09T10:30:27.0930371Z env:\n2026-10-09T10:30:27.0930571Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:27.0930932Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:27.0931861Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:27.0932730Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:27.0933019Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:27.0933326Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:27.0933571Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:27.0933795Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:27.0934356Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:27.0934750Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:27.0935065Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:27.0935381Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:27.0935625Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:27.0935882Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:27.0936115Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:27.0936356Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:27.0936586Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:27.0936809Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:27.0937289Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:27.0937679Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:27.0938034Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:27.0938357Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:27.0938677Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:27.0938971Z ##[endgroup]\n2026-10-09T10:30:27.2370861Z Multiple search paths detected. Calculating the least common ancestor of all paths\n2026-10-09T10:30:27.2567134Z The least common ancestor is /tmp/gh-aw. This will be the root directory of the artifact\n2026-10-09T10:30:27.2568102Z With the provided path, there will be 1 file uploaded\n2026-10-09T10:30:27.2568996Z Artifact name is valid!\n2026-10-09T10:30:27.2569457Z Root directory input is valid!\n2026-10-09T10:30:27.5731339Z Uploading artifact: agent-output-fallback.zip\n2026-10-09T10:30:27.5777002Z Beginning upload of artifact content to blob storage\n2026-10-09T10:30:27.8245125Z Uploaded bytes 170\n2026-10-09T10:30:27.8851666Z Finished uploading artifact content to blob storage!\n2026-10-09T10:30:27.8865396Z SHA256 digest of uploaded artifact is 35f7a8ac45d6504ac9c5304e712244c40c6546f644b1c6ee7a854ebc44bc7cac\n2026-10-09T10:30:27.8866414Z Finalizing artifact upload\n2026-10-09T10:30:28.2287957Z Artifact agent-output-fallback successfully finalized. Artifact ID 11610121887\n2026-10-09T10:30:28.2289236Z Artifact agent-output-fallback has been successfully uploaded! Final size is 170 bytes. Artifact ID is 11610121887\n2026-10-09T10:30:28.2292169Z Artifact download URL: https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37917962742/artifacts/11610121887\n2026-10-09T10:30:28.2426372Z ##[group]Run actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a\n2026-10-09T10:30:28.2426816Z with:\n2026-10-09T10:30:28.2427022Z   name: agent\n2026-10-09T10:30:28.2429163Z   path: /tmp/gh-aw/aw-prompts/prompt.txt\n/tmp/gh-aw/agent_execution.json\n/tmp/gh-aw/sandbox/agent/logs/\n/tmp/gh-aw/redacted-urls.log\n/tmp/gh-aw/mcp-logs/\n/tmp/gh-aw/agent_usage.json\n/tmp/gh-aw/agent_usage.jsonl\n/tmp/gh-aw/agent-stdio.log\n/tmp/gh-aw/pre-agent-audit.txt\n/tmp/gh-aw/github_rate_limits.jsonl\n/tmp/gh-aw/otel.jsonl\n/tmp/gh-aw/otlp-export-errors.jsonl\n/tmp/gh-aw/safeoutputs.jsonl\n/tmp/gh-aw/agent_output.json\n/tmp/gh-aw/aw-*.patch\n/tmp/gh-aw/aw-*.bundle\n/tmp/gh-aw/awf-config.json\n/tmp/gh-aw/sandbox/firewall/logs/\n/tmp/gh-aw/sandbox/firewall/audit/\n/tmp/gh-aw/sandbox/firewall/awf-reflect.json\n\n2026-10-09T10:30:28.2431415Z   if-no-files-found: ignore\n2026-10-09T10:30:28.2431682Z   compression-level: 6\n2026-10-09T10:30:28.2431931Z   overwrite: false\n2026-10-09T10:30:28.2432192Z   include-hidden-files: false\n2026-10-09T10:30:28.2432451Z   archive: true\n2026-10-09T10:30:28.2432667Z env:\n2026-10-09T10:30:28.2432885Z   OTEL_EXPORTER_OTLP_ENDPOINT: \n2026-10-09T10:30:28.2433219Z   OTEL_SERVICE_NAME: gh-aw.ai-sdlc-gh-aw-reviewer-deepseek\n2026-10-09T10:30:28.2434518Z   OTEL_RESOURCE_ATTRIBUTES: gh-aw.workflow.name=AI-SDLC%20gh-aw%20Code%20Reviewer%20%28deepseek%29,gh-aw.repository=DREAM-XIN/ai-sdlc,gh-aw.run.id=37917962742,github.run_id=37917962742,gh-aw.engine.id=copilot\n2026-10-09T10:30:28.2435382Z   OTEL_EXPORTER_OTLP_HEADERS: \n2026-10-09T10:30:28.2435673Z   GH_AW_OTLP_ENDPOINTS: [{\"url\":\"\",\"headers\":\"\"}]\n2026-10-09T10:30:28.2435993Z   GH_AW_OTLP_IF_MISSING: ignore\n2026-10-09T10:30:28.2436253Z   DEFAULT_BRANCH: main\n2026-10-09T10:30:28.2436496Z   GH_AW_ASSETS_ALLOWED_EXTS: \n2026-10-09T10:30:28.2436747Z   GH_AW_ASSETS_BRANCH: \n2026-10-09T10:30:28.2436984Z   GH_AW_ASSETS_MAX_SIZE_KB: 0\n2026-10-09T10:30:28.2437282Z   GH_AW_MCP_LOG_DIR: /tmp/gh-aw/mcp-logs/safeoutputs\n2026-10-09T10:30:28.2437610Z   GH_AW_PR_HEAD_BASE_BRANCH: \n2026-10-09T10:30:28.2437869Z   GH_AW_PR_HEAD_BASE_PR_NUMBER: \n2026-10-09T10:30:28.2438148Z   GH_AW_PR_HEAD_BASE_REF: \n2026-10-09T10:30:28.2438399Z   GH_AW_PR_HEAD_BASE_REPO: \n2026-10-09T10:30:28.2438648Z   GH_AW_PR_HEAD_BASE_SHA: \n2026-10-09T10:30:28.2438896Z   GH_AW_PR_HEAD_REPO: \n2026-10-09T10:30:28.2439381Z   GH_AW_RUNTIME_FEATURES: \n2026-10-09T10:30:28.2439698Z   GH_AW_WORKFLOW_ID_SANITIZED: aisdlcghawreviewerdeepseek\n2026-10-09T10:30:28.2440100Z   GITHUB_AW_OTEL_TRACE_ID: ef349964e8428d7901e62b0b1baad9fb\n2026-10-09T10:30:28.2440478Z   GITHUB_AW_OTEL_PARENT_SPAN_ID: 61587664f44dd440\n2026-10-09T10:30:28.2440818Z   GITHUB_AW_OTEL_JOB_START_MS: 1791541824949\n2026-10-09T10:30:28.2441149Z   GH_AW_AGENT_OUTPUT: /tmp/gh-aw/agent_output.json\n2026-10-09T10:30:28.2441463Z ##[endgroup]\n2026-10-09T10:30:28.3902443Z With the provided path, there will be 3 files uploaded\n2026-10-09T10:30:28.3908779Z Artifact name is valid!\n2026-10-09T10:30:28.3910608Z Root directory input is valid!\n2026-10-09T10:30:28.7442196Z Uploading artifact: agent.zip\n2026-10-09T10:30:28.7516555Z Beginning upload of artifact content to blob storage\n2026-10-09T10:30:29.0164490Z Uploaded bytes 1372\n2026-10-09T10:30:29.0783311Z Finished uploading artifact content to blob storage!\n2026-10-09T10:30:29.0784890Z SHA256 digest of uploaded artifact is c96cfcde2ae9dc34583d2ce975ede2c19ebd20317db1d068f2ff9b13ca0bf1ee\n2026-10-09T10:30:29.0786235Z Finalizing artifact upload\n2026-10-09T10:30:29.4621154Z Artifact agent successfully finalized. Artifact ID 11610061858\n2026-10-09T10:30:29.4622368Z Artifact agent has been successfully uploaded! Final size is 1372 bytes. Artifact ID is 11610061858\n2026-10-09T10:30:29.4626714Z Artifact download URL: https://github.com/DREAM-XIN/ai-sdlc/actions/runs/37917962742/artifacts/11610061858\n2026-10-09T10:30:29.4838810Z Post job cleanup.\n2026-10-09T10:30:29.5561714Z Token is not set\n2026-10-09T10:30:29.5683623Z Post job cleanup.\n2026-10-09T10:30:29.6102989Z [info] [otlp] sending conclusion span \"gh-aw.agent.conclusion\" to configured endpoints\n2026-10-09T10:30:29.6155337Z [info] [otlp] conclusion span export attempted\n2026-10-09T10:30:29.6157707Z Cleaning up /tmp/gh-aw...\n2026-10-09T10:30:29.6276240Z Cleaned up /tmp/gh-aw\n2026-10-09T10:30:29.6380212Z No /tmp/awf-*-chroot-home directories found\n2026-10-09T10:30:29.6487216Z No /tmp/awf-chroot-* directories found\n2026-10-09T10:30:29.6601712Z Evaluate and set job outputs\n2026-10-09T10:30:29.6621583Z Set output 'agentic_engine_timeout'\n2026-10-09T10:30:29.6623797Z Set output 'ai_credits_rate_limit_error'\n2026-10-09T10:30:29.6624857Z Set output 'checkout_pr_success'\n2026-10-09T10:30:29.6625222Z Set output 'has_patch'\n2026-10-09T10:30:29.6625558Z Set output 'http_400_response_error'\n2026-10-09T10:30:29.6625933Z Set output 'inference_access_error'\n2026-10-09T10:30:29.6626296Z Set output 'invocation_cap_exceeded'\n2026-10-09T10:30:29.6626663Z Set output 'max_cache_misses_exceeded'\n2026-10-09T10:30:29.6627027Z Set output 'mcp_policy_error'\n2026-10-09T10:30:29.6627402Z Set output 'missing_model_pricing_error'\n2026-10-09T10:30:29.6627782Z Set output 'model'\n2026-10-09T10:30:29.6628100Z Set output 'model_not_supported_error'\n2026-10-09T10:30:29.6628469Z Set output 'output'\n2026-10-09T10:30:29.6628803Z Set output 'setup-parent-span-id'\n2026-10-09T10:30:29.6629178Z Set output 'setup-span-id'\n2026-10-09T10:30:29.6629583Z Set output 'setup-trace-id'\n2026-10-09T10:30:29.6629952Z Set output 'shell_expansion_guard_rejected'\n2026-10-09T10:30:29.6630344Z Set output 'unknown_model_ai_credits'\n2026-10-09T10:30:29.6631125Z Cleaning up orphan processes\n"
    provider.historical_pre_model_agent_log = original_pre_model_agent_log

    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/dream-xin/ai-sdlc"
        expect(parsed.scheme == "https" and parsed.netloc == "api.github.com"
               and parsed.path.lower().startswith(prefix + "/"),
               "post-model fixture escaped its exact provider repository")
        expect(method == "GET", "frozen post-model provider attempted external effect: " + method)
        path = unquote(parsed.path[len(prefix):])
        query = parse_qs(parsed.query)
        current = state["reviewer_post_model_observed"]
        failed_id = int(current["run"]["id"])
        if path in {f"/actions/runs/{failed_id}", f"/actions/runs/{failed_id}/attempts/1"}:
            state["calls"].append((method, path))
            return response(deepcopy(current["run"]))
        if path in {f"/actions/runs/{failed_id}/jobs", f"/actions/runs/{failed_id}/attempts/1/jobs"}:
            state["calls"].append((method, path))
            payload = current["jobs"]
            return response({"total_count": payload["total_count"], "jobs": paginate(payload["jobs"], query)})
        if path == f"/actions/runs/{failed_id}/artifacts":
            state["calls"].append((method, path))
            payload = current["artifacts"]
            return response({"total_count": payload["total_count"],
                             "artifacts": paginate(payload["artifacts"], query)})
        if path == "/actions/jobs/113810489745/logs":
            state["calls"].append((method, path))
            return response(state["reviewer_post_model_detector_log"].encode())
        if path == "/actions/runs" or (path.startswith("/actions/workflows/") and path.endswith("/runs")):
            state["calls"].append((method, path))
            rows = [current["run"], state["reviewer_observed"]["run"], state["observed"]["run"]]
            if path != "/actions/runs":
                workflow = path[len("/actions/workflows/"):-len("/runs")]
                rows = [row for row in rows if workflow in
                        {row["path"].rsplit("/", 1)[-1], str(row["workflow_id"])}]
            return response({"total_count": len(rows), "workflow_runs": paginate(rows, query)})
        if path == "/issues/580":
            state["calls"].append((method, path))
            return response(deepcopy(state["reviewer_post_model_failure_issue"]))
        if path == "/issues":
            state["calls"].append((method, path))
            return response(paginate([state["reviewer_post_model_failure_issue"]], query))
        if path == "/issues/552/comments":
            state["calls"].append((method, path))
            return response(paginate(current["comments"], query))
        if path == "/actions/jobs/113778789435/logs":
            state["calls"].append((method, path))
            return response(original_pre_model_agent_log.encode("utf-8"))
        return old_http(method=method, url=url, token=token, body=body)

    provider.http = http
    return provider


def reviewer_post_model_runtime_fixture():
    import base64
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_store_model import StoreSnapshot, operation_events
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_effect_rollout import ProtectedEffectLineageRolloutVerifier, EffectLineageWriteFence
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
    from v03_dogfood_fixture_pool import require_slot
    import v03_dogfood_full_composition as composition
    provider = reviewer_post_model_frozen_provider_fixture(base64.b64decode(POST_HANDOFF_ARCHIVE_B64, validate=True))
    import subprocess
    from pathlib import Path
    files = provider.snapshot.files
    for name in ("effect-lineage-rollout.json", "writer-fence-receipt.json", "effect-resolution-policy.json", "decision-policy.json"):
        path = "config/operator/v03-vertical-policy/" + name
        files[path] = json.loads(subprocess.run(["git", "show", composition.REVIEWER_POST_MODEL_STORE + ":" + path],
            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True).stdout)
    policy_path = "config/operator/v03-vertical-policy/"
    rollout_verifier = ProtectedEffectLineageRolloutVerifier(
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-lineage-rollout.json"]),
        writer_fence_receipt_loader=lambda *_: deepcopy(files[policy_path + "writer-fence-receipt.json"]))
    rollout = rollout_verifier.verify(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE)
    resolution = ProtectedEffectResolutionPolicyVerifier(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-resolution-policy.json"]),
        evidence_fact_loader=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected resolution evidence")))
    resolution.verify_current()
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
                             snapshot=deepcopy(provider.snapshot))
            self.commit_count = 0
            self.fail_confirmation_once = False
        def commit(self, plan, receipt):
            if self.fail_confirmation_once and any(isinstance(m.value, dict)
                    and m.value.get("event_type") == "persist.confirmed" for m in plan.mutations):
                self.fail_confirmation_once = False
                raise OSError("fixture crash before protected Persist confirmation")
            result = super().commit(plan, receipt)
            self.commit_count += 1
            self.snapshot = StoreSnapshot(f"{self.commit_count:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    runtime = OperatorStoreRuntime(backend=Backend(), protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout), clock=lambda: "2026-10-09T09:10:00Z")
    from v03_dogfood_live_gate import resolve_current_dogfood_bindings
    from v03_dogfood_runtime_preflight import _workflow_map, _execution_bindings
    gate = SimpleNamespace(scenario="happy_path", bindings=resolve_current_dogfood_bindings({"DEEPSEEK_API_KEY": True}))
    workflows = _workflow_map(gate)
    policy = recovery_policy_fixture()
    provider.state["controller_source"] = policy.installation_commit_sha
    source = composition.RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(control_repository="dream-xin/ai-sdlc",
            control_token="fixture", target_token="fixture", workflows=workflows,
            collector_identity=composition.COLLECTOR_IDENTITY),
        target_repository="dream-xin/ai-sdlc", http=provider.http)
    pf = SimpleNamespace(slot=require_slot("happy_path"), workflows=workflows,
        candidate_pr_number=552, candidate_head_sha=composition.REVIEWER_CANDIDATE,
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha=policy.installation_commit_sha),
        trusted_context_digest="6" * 64,
        composition=SimpleNamespace(runtime=runtime, policy_authority=policy, recovery_result_source=source))
    def get_json(url, headers):
        status, _, raw = provider.http(method="GET", url=url, token="fixture")
        return status, json.loads(raw)
    candidate = composition.DogfoodGitHubCandidateProvider(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", http_get=get_json)
    candidate.bind_runtime(runtime)
    feature = build_reviewer_frozen_feature_fixture(pf, candidate, provider)
    candidate.persist_gateway = feature.persist_gateway
    pf.historical_gate_runs = [provider.state["reviewer_observed"]["run"], provider.state["observed"]["run"],
        provider.state["reviewer_post_model_observed"]["run"]]
    gates = build_selected_dogfood_gate_fixture(pf, read_ref=provider.read_ref, fallback_http=provider.http)
    bindings = _execution_bindings(gate, workflows)
    dispatch = composition.DogfoodExecutionBoundDispatchGateway(
        delegate=gates.dispatch_gateway, execution_bindings=bindings)
    one_shot = StoreBackedOneShotExternalCreateGateway(runtime=runtime, delegate=dispatch,
        trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True)
    loader = composition.DogfoodRecoveryBoundContentLoader(
        result_source=gates.result_source, recovery_result_source=source, policy_authority=policy)
    loader.bind_runtime(runtime)
    source.bind_post_handoff(runtime, policy)
    base = TrustedVerticalExecutor(runtime=runtime, feature_gateway=feature.feature_gateway,
        persist_gateway=feature.persist_gateway, dispatch_gateway=one_shot,
        config=TrustedVerticalExecutorConfig(target_ref=pf.slot.target_ref,
            trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True,
            old_writers_quiesced=True, rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest, max_auto_steps=64),
        resolution_policy_verifier=resolution)
    from pathlib import Path
    from operator_production_runtime import TrustedOperatorRuntimeConfig, TrustedFeatureBinding
    from operator_decision_policy import ProtectedDecisionPolicyVerifier
    from validate_v03_dogfood_runtime_composition import assemble_post_handoff_responses_graph, assert_post_handoff_authority_graph
    config = TrustedOperatorRuntimeConfig(target_repository=pf.execution.repository,
        store_repository=pf.execution.repository, installation_ref="main", store_checkout=Path("."),
        principal="post-handoff-fixture",
        feature_bindings=(TrustedFeatureBinding(pf.slot.feature_id, pf.slot.target_ref),))
    decision = ProtectedDecisionPolicyVerifier(repository=config.store_repository, state_ref=config.state_ref,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "decision-policy.json"]))
    def reader_get(url, headers):
        if "/contents/state/features/" in url:
            return feature.http("GET", url.replace("https://api.github.com", "https://api.github.test"), headers, None)
        return get_json(url, headers)
    responses, graph_before = assemble_post_handoff_responses_graph(
        runtime=runtime, base_executor=base, content_loader=loader, slot=pf.slot, config=config,
        policy_authority=policy, decision_policy_verifier=decision,
        trusted_role_policy="fixture-independent-role-policy", collector_namespace_policy="fixture-collector-namespace",
        reader_http_get=reader_get)
    executor = responses.operator_bundle.executor
    delegate = responses.operator_bundle.callback_coordinator
    predecessor_events = deepcopy(operation_events(runtime.backend.read_snapshot(), composition.RECOVERY_OPERATION_ID))
    assert_post_handoff_authority_graph(graph_before, responses, policy, predecessor_events=predecessor_events[:15])
    def forbidden_handoff_http(*args, **kwargs):
        raise AssertionError("post-handoff reconciliation attempted another fixture PATCH")
    handoff = composition.DogfoodCandidateHandoff(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", candidate_provider=candidate, http_request=forbidden_handoff_http)
    handoff.content_loader = loader
    coordinator = composition.DogfoodTrustedCallbackCoordinator(delegate=delegate, candidate_handoff=handoff)
    collector = composition.DogfoodReviewerReplacementCollector(policy_authority=policy,callback_coordinator=coordinator,
        result_source=gates.result_source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock)
    recovery_collector = composition.DogfoodRecoveryCollector(callback_coordinator=coordinator,
        result_source=source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock, policy_authority=policy)
    pf.composition.__dict__.update(candidate_provider=candidate, feature_event_gateway=feature.event_gateway,
        result_source=gates.result_source, collector=collector, recovery_collector=recovery_collector,
        actions_transport=gates.transport, dispatch_gateway=dispatch, bundle=responses.operator_bundle,
        responses=responses, graph_before=graph_before, predecessor_events=predecessor_events,
        callback_coordinator=coordinator)
    return pf, provider, feature, gates, coordinator






def run_archival_bounded_test(test):
    """Reproduce exact ordinal2 production selection without changing current selection."""
    from unittest.mock import patch
    import v03_dogfood_live_gate as gate
    import v03_dogfood_runtime_preflight as preflight
    with patch.object(preflight, "dogfood_selection_for_scenario",
                      return_value=(gate.CURRENT_DOGFOOD_POLICY, dict(gate.CURRENT_DOGFOOD_WORKFLOWS))):
        return test()

def run_archival_reviewer_test(test):
    """Keep ordinal-one producer workflows exact; current selection is tested independently."""
    from dataclasses import replace
    from unittest.mock import patch
    import v03_dogfood_live_gate as gate
    current=gate.resolve_current_dogfood_bindings({"DEEPSEEK_API_KEY":True})
    workflows={"developer":"ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml",
               "reviewer":"ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local.lock.yml",
               "qa":"ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml"}
    historical=tuple(replace(row,worker_workflow=workflows[row.role]) for row in current)
    import v03_dogfood_runtime_preflight as preflight
    with (patch.object(preflight,"dogfood_selection_for_scenario",return_value=(gate.CURRENT_DOGFOOD_POLICY, workflows)),
          patch.object(gate,"CURRENT_DOGFOOD_WORKFLOWS",workflows),
          patch.object(gate,"CURRENT_DOGFOOD_BLOBS",gate.HISTORICAL_RELEASE_DOGFOOD_BLOBS),
          patch.object(gate,"resolve_current_dogfood_bindings",return_value=historical)):
        return test()


def reviewer_post_model_replacement_admission_tests():
    from copy import deepcopy
    from operator_store import StoreCommandError
    from operator_store_git import CasConflict
    from operator_store_model import canonical_json, operation_events
    from operator_vertical import VerticalInvariantError
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    errors = (StoreCommandError, VerticalInvariantError, d.V03DogfoodRuntimeDriverError,
              d.V03DogfoodScenarioRunnerError, ValueError)
    def reject(pf, gates, feature, label):
        runtime = pf.composition.runtime
        before = (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
                  runtime.backend.commit_count, len(gates.state["posts"]), feature.state["puts"])
        try:
            d.recover_reviewer_post_model(pf)
        except errors:
            pass
        else:
            raise AssertionError("Reviewer replacement accepted " + label)
        expect(before == (runtime.backend.read_snapshot().ref_sha, canonical_json(runtime.backend.read_snapshot().files),
                          runtime.backend.commit_count, len(gates.state["posts"]), feature.state["puts"]),
               "Reviewer rejected " + label + " after an unauthorized mutation")
    for path in c.REVIEWER_POST_MODEL_PATHS:
        for value in (None, {}, []):
            pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
            pf.composition.runtime.backend.snapshot.files[path] = value
            reject(pf, gates, feature, "partial/null route " + path)
    for label, mutate in (
        ("predecessor event", lambda pf,p: operation_events(pf.composition.runtime.backend.snapshot,c.RECOVERY_OPERATION_ID)[29]["payload"].update(receipt_id="1")),
        ("old attempt two", lambda pf,p: p.state["reviewer_post_model_observed"]["run"].update(run_attempt=2)),
        ("old active", lambda pf,p: p.state["reviewer_post_model_observed"]["run"].update(status="in_progress")),
        ("old source", lambda pf,p: p.state["reviewer_post_model_observed"]["run"].update(head_sha="9"*40)),
        ("candidate drift", lambda pf,p: p.state.update(head="9"*40)),
        ("new main drift", lambda pf,p: p.state.update(controller_source="9"*40)),
    ):
        pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
        mutate(pf,provider)
        reject(pf,gates,feature,label)
    for label,mutate in (
        ("original pre-model attempt drift",lambda p:p.state["reviewer_observed"]["run"].update(run_attempt=2)),
        ("agent did not execute",lambda p:next(j for j in p.state["reviewer_post_model_observed"]["jobs"]["jobs"] if j["name"]=="agent").update(conclusion="failure")),
        ("detector relabeled success",lambda p:next(j for j in p.state["reviewer_post_model_observed"]["jobs"]["jobs"] if j["name"]=="detection").update(conclusion="success")),
        ("Safe Outputs executed",lambda p:next(j for j in p.state["reviewer_post_model_observed"]["jobs"]["jobs"] if j["name"]=="safe_outputs").update(conclusion="success")),
        ("timeout evidence missing",lambda p:p.state.update(reviewer_post_model_detector_log="unproven")),
        ("failure issue relabeled",lambda p:p.state["reviewer_post_model_failure_issue"].update(id=1)),
    ):
        pf,provider,feature,gates,_=reviewer_post_model_runtime_fixture()
        mutate(provider);reject(pf,gates,feature,label)
    for value in (None,{}):
        pf,provider,feature,gates,_=reviewer_post_model_runtime_fixture()
        pf.composition.runtime.backend.snapshot.files[c.REVIEWER_SEAL_PATH]=value
        reject(pf,gates,feature,"historical seal must remain absent")

    pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
    runtime = pf.composition.runtime
    original = deepcopy(runtime.backend.read_snapshot())
    from operator_store_model import rebuild_projection
    from operator_vertical_store import vertical_projection
    expect(rebuild_projection(original, c.RECOVERY_OPERATION_ID)["expected_feature_revision"] == 1
           and vertical_projection(original, c.RECOVERY_OPERATION_ID)["expected_feature_revision"] == 3,
           "frozen predecessor fixture does not expose canonical Persist revision overlay")
    c.validate_reviewer_post_model_predecessor(original, fresh=True)
    proof = d._observe_reviewer_post_model_failure(pf)
    binding = c.recovery_execution_binding(pf.composition.policy_authority)
    def planner(snapshot):
        return c.plan_reviewer_post_model_replacement(snapshot,consumer_binding=binding,
            worker_blobs=d._reviewer_worker_blobs(),failure_proof=proof)
    first, second = planner(original), planner(original)
    expect(len(first.mutations)==2 and all(m.kind=="create_immutable" for m in first.mutations),
           "Reviewer CAS is not one immutable authorization+consumed claim")
    runtime.backend.commit(first,runtime.protected_receipt())
    try: runtime.backend.commit(second,runtime.protected_receipt())
    except CasConflict: pass
    else: raise AssertionError("two Reviewer CAS winners")
    expect(planner(runtime.backend.read_snapshot()).result["acquired"] is False,
           "Reviewer CAS loser acquired a new creation slot")
    reject(pf,gates,feature,"crash after claim with no observed run")
    expect(operation_events(runtime.backend.read_snapshot(),c.RECOVERY_OPERATION_ID)==provider.frozen_events,
           "Reviewer claim altered original logical launch history")
    pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
    pf.composition.runtime.backend.inject_conflict_once()
    result = d.recover_reviewer_post_model(pf)
    expect(len(gates.state["posts"]) == 1 and result["sealed"]["run_id"] != c.REVIEWER_POST_MODEL_FAILED_RUN,
           "Reviewer CAS retry did not produce one distinct first attempt")
    snapshot=pf.composition.runtime.backend.read_snapshot()
    for invalid in (True,"1",2):
        snapshot.files[c.REVIEWER_POST_MODEL_SEAL_PATH]["run_attempt"]=invalid
        try: c.reviewer_replacement_route(snapshot)
        except VerticalInvariantError: pass
        else: raise AssertionError("post-model seal accepted malformed attempt")
        finally: snapshot.files[c.REVIEWER_POST_MODEL_SEAL_PATH]["run_attempt"]=1
    before = (pf.composition.runtime.backend.commit_count,len(gates.state["posts"]))
    d.recover_reviewer_post_model(pf)
    expect(before == (pf.composition.runtime.backend.commit_count,len(gates.state["posts"])),
           "Reviewer sealed replay changed Store or POST count")
    for bad in (True, "1", 2):
        gates.state["runs"][0]["run_attempt"] = bad
        reject(pf,gates,feature,"malformed or repeated replacement attempt")
    gates.state["runs"][0]["run_attempt"] = 1
    gates.state["runs"][0]["conclusion"] = "failure"
    reject(pf,gates,feature,"subsequently failed replacement")
    pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
    original_http = gates.transport.http
    lost = {"once":True}
    def lost_ack(**kwargs):
        result = original_http(**kwargs)
        if kwargs["method"] == "POST" and lost["once"]:
            lost["once"] = False
            raise OSError("fixture lost dispatch acknowledgement")
        return result
    gates.transport.http = lost_ack
    d.recover_reviewer_post_model(pf)
    expect(len(gates.state["posts"]) == 1,"Reviewer acknowledgement loss retried POST")


    pf,provider,feature,gates,_=reviewer_post_model_runtime_fixture()
    d.recover_reviewer_post_model(pf)
    snapshot=pf.composition.runtime.backend.read_snapshot()
    auth,_=c.validate_reviewer_authorization(snapshot)
    sealed=c.reviewer_replacement_route(snapshot)["sealed"]
    resolved=pf.composition.result_source.resolve(external_dispatch_key=auth["physical_key"],
        expected_receipt_identity=str(sealed["run_id"]),trusted_context=c.reviewer_trusted_context(auth))
    from dataclasses import fields
    from operator_vertical import TrustedDispatchContext
    from operator_vertical_recovery import plan_vertical_callback_record
    material=c.reviewer_dispatch(auth,physical=False)
    material.update(runtime_receipt_identity=str(c.REVIEWER_FAILED_RUN),
        worker_identity=resolved.run.worker_identity,collector_identity=resolved.run.collector_identity)
    context=TrustedDispatchContext(**{field.name:material[field.name] for field in fields(TrustedDispatchContext)})
    runtime=pf.composition.runtime
    runtime.commit_replanned(lambda snap:plan_vertical_callback_record(snap,context=context,
        callback_id="foreign-reviewer-observation",worker_payload=resolved.role_payload,receipts=[],
        occurred_at=runtime.clock(),trusted_context_digest=pf.trusted_context_digest))
    before=(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,len(gates.state["posts"]))
    try: pf.composition.collector.handle(operation_id=c.RECOVERY_OPERATION_ID,external_dispatch_key=c.REVIEWER_OLD_KEY)
    except errors: pass
    else: raise AssertionError("Reviewer collector accepted a foreign same-logical-key callback")
    expect(before==(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,len(gates.state["posts"])),
           "conflicting Reviewer callback reached another Store or provider effect")

    for key_kind in ("spent","new"):
        pf,provider,feature,gates,_=reviewer_post_model_runtime_fixture()
        d.recover_reviewer_post_model(pf)
        snapshot=pf.composition.runtime.backend.read_snapshot()
        auth,_=c.validate_reviewer_authorization(snapshot)
        sealed=c.reviewer_replacement_route(snapshot)["sealed"]
        resolved=pf.composition.result_source.resolve(external_dispatch_key=auth["physical_key"],
            expected_receipt_identity=str(sealed["run_id"]),trusted_context=c.reviewer_trusted_context(auth))
        from dataclasses import fields
        from operator_vertical import TrustedDispatchContext
        from operator_vertical_recovery import plan_vertical_callback_record
        material=c.reviewer_dispatch(auth,physical=False)
        material.update(runtime_receipt_identity=str(c.REVIEWER_FAILED_RUN),
            worker_identity=resolved.run.worker_identity,collector_identity=resolved.run.collector_identity)
        context=TrustedDispatchContext(**{field.name:material[field.name] for field in fields(TrustedDispatchContext)})
        runtime=pf.composition.runtime
        from dataclasses import replace
        direct_context=replace(context,external_dispatch_key=auth["physical_key"] if key_kind=="new" else c.REVIEWER_POST_MODEL_FAILED_KEY)
        try:
            plan_vertical_callback_record(runtime.backend.read_snapshot(),context=direct_context,
                callback_id="invalid-direct-physical",worker_payload=resolved.role_payload,receipts=[],
                occurred_at=runtime.clock(),trusted_context_digest=pf.trusted_context_digest)
        except (StoreCommandError,VerticalInvariantError):
            pass
        else:
            raise AssertionError("shared planner accepted direct physical callback")
        runtime.commit_replanned(lambda snap:plan_vertical_callback_record(snap,context=context,
            callback_id="foreign-physical-reviewer-"+key_kind,worker_payload=resolved.role_payload,receipts=[],
            occurred_at=runtime.clock(),trusted_context_digest=pf.trusted_context_digest))
        from operator_store_model import digest_json
        tampered=operation_events(runtime.backend.snapshot,c.RECOVERY_OPERATION_ID)[-1]["payload"]
        physical_key=auth["physical_key"] if key_kind=="new" else c.REVIEWER_POST_MODEL_FAILED_KEY
        tampered["external_dispatch_key"]=physical_key
        tampered["trusted_callback_envelope"]["trusted_context"]["external_dispatch_key"]=physical_key
        tampered["trusted_callback_envelope_digest"]=digest_json(tampered["trusted_callback_envelope"])
        before=(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,len(gates.state["posts"]))
        try: pf.composition.collector.handle(operation_id=c.RECOVERY_OPERATION_ID,external_dispatch_key=c.REVIEWER_OLD_KEY)
        except errors: pass
        else: raise AssertionError("Reviewer collector accepted a foreign same-logical-key callback")
        expect(before==(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,len(gates.state["posts"])),
               "conflicting Reviewer callback reached another Store or provider effect")

    for label in ("duplicate retired run", "pagination unknown"):
        pf,provider,feature,gates,_=reviewer_post_model_runtime_fixture()
        if label == "duplicate retired run":
            extra=deepcopy(provider.state["reviewer_post_model_observed"]["run"])
            extra["id"] += 1
            pf.historical_gate_runs.append(extra)
        else:
            original_http=gates.transport.http
            def broken_lookup(**kwargs):
                if "/actions/workflows/" in kwargs["url"] and kwargs["method"]=="GET":
                    return 503,{},b"{}"
                return original_http(**kwargs)
            gates.transport.http=broken_lookup
        reject(pf,gates,feature,label)
    for failed_step in ("Execute threat detection with AWF",
                        "Require first attempt and affirmative detection before Safe Outputs effects"):
        pf,provider,feature,gates,_=reviewer_post_model_runtime_fixture()
        original_http=gates.transport.http
        def skipped_guard(**kwargs):
            result=original_http(**kwargs)
            if kwargs["method"]=="POST":
                run=gates.state["runs"][0]
                doc=gates.state["routes"][f"/actions/runs/{run['id']}/attempts/1/jobs"]
                found=[step for job in doc["jobs"] for step in job["steps"] if step["name"]==failed_step]
                expect(len(found)==1,"selected compiled safety step is missing")
                found[0]["conclusion"]="skipped"
            return result
        gates.transport.http=skipped_guard
        try: d.recover_reviewer_post_model(pf)
        except errors: pass
        else: raise AssertionError("Reviewer sealed a skipped safety execution")
        expect(len(gates.state["posts"])==1 and c.REVIEWER_POST_MODEL_SEAL_PATH not in pf.composition.runtime.backend.snapshot.files,
               "failed safety result acquired a seal or duplicate execution")
        reject(pf,gates,feature,"failed safety slot replay")
    print("- fixed post-model Reviewer CAS, failure history, partial routes, replay and acknowledgement loss fail closed")


def reviewer_post_model_replacement_full_pipeline_tests():
    from copy import deepcopy
    from operator_store_model import operation_events
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    from validate_v03_dogfood_runtime_composition import assert_post_handoff_authority_graph
    pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
    original = deepcopy(pf.composition.runtime.backend.read_snapshot().files)
    # The read-only no-sidecar bridge must work before any creation.
    old = c.validate_reviewer_controller_bridge(pf.composition.runtime.backend.read_snapshot(),
        c.recovery_execution_binding(pf.composition.policy_authority),inspection_only=True)
    expect(old["execution_source_head_sha"] == c.REVIEWER_PREDECESSOR_SOURCE,
           "preclaim bridge silently relabeled old controller source")
    result = d.recover_reviewer_post_model(pf)
    expect(len(gates.state["posts"]) == 1 and result["sealed"]["run_attempt"] == 1,
           "actual Reviewer transport did not consume exactly one first-attempt slot")
    expect(operation_events(pf.composition.runtime.backend.read_snapshot(),c.RECOVERY_OPERATION_ID)==provider.frozen_events,
           "Reviewer physical execution rewrote or fabricated logical launch facts")
    record=finish_reviewer_replacement_pipeline_tests(pf,gate_fixture=gates,feature_fixture=feature,
        read_ref=provider.read_ref,effect_counts=provider.effect_counts,adapter=pf.composition.responses.adapter)
    expect({"https://github.com/dream-xin/ai-sdlc/actions/runs/37927328438",
            "https://github.com/dream-xin/ai-sdlc/issues/580",
            "https://github.com/dream-xin/ai-sdlc/issues/239#issuecomment-6092341042"}
           <= {uri.lower() for uri in record["evidence_uris"]},
           "post-model finalizer omitted real failed history/effects")
    snapshot=pf.composition.runtime.backend.read_snapshot()
    for path,raw in provider.frozen_operation_raw_files.items():
        if path.endswith("/projection.json"):
            continue
        from operator_store_model import canonical_json
        expect((canonical_json(snapshot.get(path))+"\n").encode()==raw,
               "post-model route rewrote exact historical blob "+path)
    expect(operation_events(snapshot,c.RECOVERY_OPERATION_ID)[:30]==provider.frozen_events,
           "Reviewer/QA pipeline changed frozen thirty-event history")
    for path,value in original.items():
        if "/projections/" not in path and "/operations/" not in path.rsplit("/",1)[-1]:
            if path.startswith("state/operator/v1/operations/") and path.endswith("/projection.json"):
                continue
            expect(snapshot.get(path)==value,"Reviewer pipeline rewrote predecessor "+path)
    assert_post_handoff_authority_graph(pf.composition.graph_before,pf.composition.responses,
        pf.composition.policy_authority,predecessor_events=provider.frozen_events[:15])
    selected_gate_manifest_negative_tests(gates)
    print("- actual selected Reviewer replacement/status/Persist/QA/Notification/finalizer pipeline passes")


def structured_gate_authenticated_handoff_tests():
    """Real existing loader/gateways, fake provider HTTP, and actual pinned handler."""
    import base64
    import hashlib
    import json
    import os
    import subprocess
    from copy import deepcopy
    from dataclasses import replace
    from pathlib import Path
    from urllib.parse import parse_qs, unquote, urlparse
    from operator_store_model import operation_events, canonical_json, StoreSnapshot, digest_json
    from operator_vertical import VerticalInvariantError
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    import v03_dogfood_gate_output as output
    from validate_v03_gate_output_contract import verify_context_roundtrip
    root = Path(__file__).resolve().parents[1]
    actions_root = Path(os.environ["GH_AW_ACTIONS_ROOT"])
    pf, provider, feature, gates, _ = reviewer_post_model_runtime_fixture()
    d.recover_reviewer_post_model(pf)
    runtime = pf.composition.runtime
    loader = pf.composition.responses.operator_bundle.callback_coordinator.content_loader
    # Composition's callback wrapper may expose the loader on its real delegate.
    if loader is None:
        raise AssertionError("actual context graph lost bound loader")
    builder = c.DogfoodStructuredGateContextBuilder(runtime=runtime,
        feature_gateway=feature.feature_gateway, persist_gateway=feature.persist_gateway,
        content_loader=loader, candidate_provider=pf.composition.candidate_provider,
        policy_authority=pf.composition.policy_authority)
    directory = "docs/features/" + pf.slot.feature_id
    texts = {}
    for name in ("dogfood-task.md", "implementation.md"):
        path = directory + "/" + name
        texts[path] = subprocess.run(["git", "show", c.REVIEWER_CANDIDATE + ":" + path],
            cwd=root, check=True, capture_output=True).stdout
    state = {"missing": False, "tamper": False}
    old_http = provider.http
    def provider_http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/dream-xin/ai-sdlc/contents/"
        if method == "GET" and parsed.path.startswith(prefix):
            path = unquote(parsed.path[len(prefix):])
            ref = parse_qs(parsed.query).get("ref", [""])[0]
            if (path == directory or path in texts) and ref == provider.read_ref():
                if path == directory:
                    rows = [{"path": name, "type": "file", "sha": hashlib.sha1(
                        b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()}
                        for name, raw in texts.items() if not state["missing"] or name.endswith("dogfood-task.md")]
                    return 200, {}, json.dumps(rows).encode()
                raw = texts[path]
                sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
                if state["tamper"] and path.endswith("implementation.md"):
                    raw += b"changed"
                return 200, {}, json.dumps({"type": "file", "path": path, "sha": sha,
                    "encoding": "base64", "content": base64.b64encode(raw).decode()}).encode()
        return old_http(method=method, url=url, token=token, body=body)
    provider.http = provider_http
    workflows = GhAwVerticalWorkflowMap(default_branch="main",
        developer_workflow=pf.workflows.developer_workflow,
        reviewer_workflow=c.STRUCTURED_GATE_WORKFLOWS["reviewer"],
        qa_workflow=c.STRUCTURED_GATE_WORKFLOWS["qa"])
    captured, rows = [], []
    def transport_http(*, method, url, token, body=None):
        path = unquote(urlparse(url).path)
        if method == "POST":
            payload = json.loads(body)
            inputs = payload["inputs"]
            workflow = path.split("/")[-2]
            expect(workflow in c.STRUCTURED_GATE_WORKFLOWS.values(), "context transport escaped new Gate pair")
            captured.append(deepcopy(inputs))
            rows.append({"id": 950000 + len(rows), "event": "workflow_dispatch", "head_branch": "main",
                "path": ".github/workflows/" + workflow, "display_title": "AI-SDLC gh-aw " + inputs["dispatch_key"]})
            return 204, {}, b""
        if method == "GET" and "/actions/workflows/" in path and path.endswith("/runs"):
            workflow = path.split("/")[-2]
            found = [row for row in rows if row["path"] == ".github/workflows/" + workflow]
            return 200, {}, json.dumps({"total_count": len(found), "workflow_runs": found}).encode()
        raise AssertionError("unexpected structured transport boundary " + method + " " + path)
    transport = GitHubActionsVerticalGhAwTransport(replace(gates.transport.config, workflows=workflows),
        http=transport_http, sleeper=lambda _: None)
    gateway = c.DogfoodStructuredGateDispatchGateway(transport=transport, workflows=workflows, context_builder=builder)
    def from_inputs(inputs):
        payload = json.loads(inputs["task_payload"])
        vertical = payload["feature_context"]["vertical"]
        return {"operation_id": vertical["operation_id"], "operation_generation": vertical["operation_generation"],
            "operation_profile": vertical["profile"], "semantic_effect_key": vertical["semantic_effect_key"],
            "external_dispatch_key": vertical["external_dispatch_key"], "dispatch_id": vertical["dispatch_id"],
            "target_repository": inputs["target_repository"], "target_ref": inputs["target_ref"],
            "feature_id": inputs["feature_id"], "expected_revision": int(inputs["expected_revision"]),
            "feature_stage": inputs["stage"], "task_id": payload["task"]["id"], "role": inputs["role"],
            "candidate_pr_number": int(inputs["candidate_pr_number"]), "candidate_head_sha": inputs["candidate_head_sha"]}
    def env(inputs):
        names = {"TASK_PAYLOAD": "task_payload", "FEATURE_ID": "feature_id", "EXPECTED_REVISION": "expected_revision",
            "DISPATCH_KEY": "dispatch_key", "TARGET_REPOSITORY": "target_repository", "TARGET_REF": "target_ref",
            "STAGE": "stage", "ROLE": "role", "CANDIDATE_PR_NUMBER": "candidate_pr_number",
            "CANDIDATE_HEAD_SHA": "candidate_head_sha"}
        return {**{key: inputs[value] for key, value in names.items()}, "RUN_ATTEMPT": "1"}
    contexts = {}
    for role in ("reviewer", "qa"):
        actual = next(item for item in reversed(gates.state["inputs"]) if item["role"] == role)
        dispatch = from_inputs(actual)
        before = (canonical_json(runtime.backend.read_snapshot().files), runtime.backend.commit_count,
                  feature.state["puts"], len(gates.state["posts"]))
        result = gateway.launch(dispatch=dispatch)
        expect(result["lookup_state"] == "LAUNCHED", "actual structured transport did not receive provider receipt")
        inputs = captured[-1]
        expected = c.GhAwVerticalRoleDispatchGateway(transport=transport, workflows=workflows)._inputs(dispatch)
        original_payload = json.loads(expected["task_payload"])
        enriched_payload = json.loads(inputs["task_payload"])
        context = enriched_payload["feature_context"].pop("gate_context")
        expect(enriched_payload == original_payload, "context handoff altered original task/vertical identity")
        expect(output.context_from_environment(env(inputs)) == context,
               "pre-model helper did not validate actual transported context")
        expect(len(output.canonical(inputs)) <= 32768, "structured dispatch exceeded total input budget")
        roundtrip = verify_context_roundtrip(role, context, root=root, actions_root=actions_root)
        expect(roundtrip["payload"]["candidate_head_sha"] == dispatch["candidate_head_sha"],
               "official published structured result lost actual candidate binding")
        expect(before == (canonical_json(runtime.backend.read_snapshot().files), runtime.backend.commit_count,
                          feature.state["puts"], len(gates.state["posts"])),
               "read-only context handoff mutated protected lifecycle or existing dispatch")
        contexts[role] = context
        bad = env(inputs)
        missing = json.loads(bad["TASK_PAYLOAD"])
        del missing["feature_context"]["gate_context"]
        bad["TASK_PAYLOAD"] = json.dumps(missing)
        try: output.context_from_environment(bad)
        except output.GateOutputContractError: pass
        else: raise AssertionError("missing authenticated context reached model")
        bad = env(inputs); bad["CANDIDATE_HEAD_SHA"] = "9" * 40
        try: output.context_from_environment(bad)
        except output.GateOutputContractError: pass
        else: raise AssertionError("context candidate drift reached model")
        for flag in ("missing", "tamper"):
            state[flag] = True
            try: builder(dispatch)
            except (VerticalInvariantError, output.GateOutputContractError): pass
            else: raise AssertionError("candidate context accepted " + flag)
            finally: state[flag] = False
        bad_dispatch = dict(dispatch, expected_revision=dispatch["expected_revision"] + 1)
        try: builder(bad_dispatch)
        except VerticalInvariantError: pass
        else: raise AssertionError("context ignored protected revision")
        if role == "reviewer":
            # Real callback, translation, reducer and Persist advance to QA; no seeded facts.
            pf.composition.collector.handle(operation_id=c.RECOVERY_OPERATION_ID,
                external_dispatch_key=c.REVIEWER_OLD_KEY)
            expect(any(item["role"] == "qa" for item in gates.state["inputs"]),
                   "actual accepted Reviewer did not advance to QA")
    expect(contexts["reviewer"]["identity"]["candidate_head_sha"] != contexts["qa"]["identity"]["candidate_head_sha"],
           "canonical Persist candidate progression was frozen")
    expect(any(row["kind"] == "review" for row in contexts["qa"]["documents"]),
           "actual QA context omitted accepted review content")
    expect(operation_events(runtime.backend.read_snapshot(), c.RECOVERY_OPERATION_ID)[:30] == provider.frozen_events,
           "context preparation changed immutable historical prefix")
    print("- actual loader/context/dispatch/pre-model validation/official Gate publication handoff passed")

def reviewer_structured_frozen_provider_fixture(archive_bytes):
    """Historical ordinal-two execution, without seeding ordinal-three transitions.

    Log responses are bounded verbatim excerpts. The authentic REWORK comment is
    preserved unchanged and is never promoted into a PASS or a lifecycle event.
    No ZIP is synthesized for the historical safe-outputs-items artifact.
    """
    import base64
    import hashlib
    import json
    import subprocess
    from copy import deepcopy
    from pathlib import Path
    from urllib.parse import parse_qs, unquote, urlparse
    from operator_store_model import StoreSnapshot, operation_events
    from operator_vertical_store import vertical_projection

    provider = reviewer_post_model_frozen_provider_fixture(archive_bytes)
    root = Path(__file__).resolve().parents[1]
    commit = "2fd1aec70ccd4ae53ec606146d477a17d4a3967b"
    operation_id = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
    operation_root = "state/operator/v1/operations/" + operation_id + "/"
    candidate_head = "41e0df7089c5907b00bbaeac5dd2be71d4f02d4b"
    folder = "docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001"
    pins = json.loads("{\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/authorization.json\":\"e68d45041c47083c2da5521aa325fcf58bef256b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/create-claim.json\":\"706888dddd1acc157cdbeb5b54ace6539125e16b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/post-handoff-reconciliation-1.json\":\"5178d7697c9b140c64c4dc2cf3fca94bf223c1ab\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/sealed-receipt.json\":\"7558b2b3ffe08bb255d3c287fffa49f5fc988f0e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/authorization.json\":\"d344fca61af21038c929897bc3fd636d4297ee17\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/create-attempt.json\":\"db183bc850c8e9add5abad38ced5728325192aba\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/transport-continuation.json\":\"e2b2edf5e50eedaacbdaee7ef1b0ae64c5f94580\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-candidate-handoffs/1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be/applied.json\":\"eb63e61ff00ae20bbe465d205c98924056e77827\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-candidate-handoffs/1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be/intent.json\":\"da06cd8849fff194e3cdbeb1df54d3efa69f850e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-prehttp-recovery-attempt.json\":\"e9bf99cc5fd8810a6fd08666ff4c17b136bee1b3\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-reviewer-post-model-replacement-2/authorization.json\":\"789007f3e0fa8ae57538237db1cb0301a49b7e5b\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-reviewer-post-model-replacement-2/create-claim.json\":\"72a617a115d349f9a9b9c18565621917d112d52e\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-reviewer-pre-model-replacement-1/authorization.json\":\"9887312d2053849c64986be4bb661938fa08a7e6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-reviewer-pre-model-replacement-1/create-claim.json\":\"f8092c2cfea63b9da61ebc21a5e967b2e4fd6ad3\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000001-operation-started-739f4331137732d6184000cf3d8b4915.json\":\"86e43c43941b03e9721844b58479d95db93ce5c8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000002-loop-step-selected-2cf82f42399ec59c7283df1711819426.json\":\"f26de71400211607359fb60b58e77ddbab7216ed\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000003-dispatch-claimed-687520874a948a5c4534e4e30d97b366.json\":\"6741a203a62d31e81f1d619d705bb18feddc0173\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000004-dispatch-launch-authorized-5f6d063282780be1d174254ce8ef9134.json\":\"1840f7cac50722dad83cb0118e441e475175d859\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000005-dispatch-launch-lookup-recorded-8629bfae40264dc5d15df601bc67686d.json\":\"6f73721b7c8847ddae58e59bbee801d28cd9ef43\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000006-operation-superseded-972620170d72306fdfa27f564dbc68c4.json\":\"a11e8f98ab5a9511fe30cf22f0ba6fa0c41253d9\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000007-operation-generation-started-559d6292df44700862ae642429872527.json\":\"def317058e40c16a8b35c7390ab5847e678192c5\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000008-loop-step-selected-d13700446f0139fb96e5717dc101bbdf.json\":\"275a6134e086e95c72f7a8c0aa8940a9f35a67c8\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000009-dispatch-claimed-f55e33e1ea7fd6b35eb44831e700d91f.json\":\"09cbb9201c82d1e69bcce5b0febc8c28f8948fac\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000010-dispatch-launch-authorized-8ea8faac43fb02dc1c3c8e481a40da93.json\":\"f6f793ce9725e618be0b7b25712e5abe44a55b59\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000011-dispatch-launch-lookup-recorded-ef426f7c675283149f805fdab65861ec.json\":\"ca4581772d9271f0e7d4dece22479a376504e8d5\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000012-dispatch-launch-lookup-recorded-cff7b708649dcf3b6ca354d3f72fbb6d.json\":\"96c43dcefda8558aa73750eb12a5e0d5af419d82\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000013-worker-callback-recorded-6e9bb8c4081e7ac28af2c5bccda2dcb4.json\":\"d773017efeec4ceccd65aaf28c1574c5aa6c9f69\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000014-worker-result-rejected-6e6b1ab67000b9207463c8676cad7be7.json\":\"93c3c64ea155b65bdbeaf78eed0067590aed9ede\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000015-loop-stable-stop-cd01bb348e233107a60b54928e336a00.json\":\"5a6969ba938f6153705d999065f786d4bb3159ec\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000016-worker-callback-recorded-8f4c403e06ae9355b0b245c9df740bf7.json\":\"069beb76d507b71109a1219722e03bf9bd4179f2\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000017-worker-result-validated-ed56d7eadf7efdb50d607eebb60c09e1.json\":\"54c9ddf16627d495c4ef06f80016fda173e12475\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000018-feature-event-translated-146e91f4ed5fa9420bdc79e69576a866.json\":\"b277131c21142e815de9825c9c7f02f5283bf19a\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000019-persist-requested-05b10f2f65d52ecbde4f92a6f48ad0a8.json\":\"03c6cb5628f5896c3dedcfddeb6abb0257b0886d\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000020-persist-linearized-9a302d406aac5e4a2b8aede284386a4c.json\":\"e8ea390b14f84173a2f2ba65003fb7fcb21d1faf\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000021-persist-confirmed-a92f43c724a6f37ad3eef0d7f217977e.json\":\"3f1dac41fef483899b0af3d1de6634e502497986\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000022-loop-step-selected-185d0cd557b4337a582cdcdc9ed7075c.json\":\"19c681ab77d90849e6b67aa53b8ccc3e271c16e6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000023-feature-event-translated-5bd0311cb465491d30a7027e617c4c4c.json\":\"09aedd1fa3bd0fb8b71ef6fd75390e2edc520db4\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000024-persist-requested-1e166d13887db2dee0b82e6a979b850e.json\":\"4dcf56f2ad6a941ccf5b6204cbb418d86f9be0b6\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000025-persist-linearized-5681cee3f8344418644af863d69d5ce8.json\":\"1b807744b90d39564e05ce2f378e28fba6ece5b1\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000026-persist-confirmed-c1ee4698394d605b8ba50d7529227216.json\":\"1bd7b63e0b30882f1c304d1be16dad17f3b1dfbe\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000027-loop-step-selected-0c44caba02897e2237f92649007a66fa.json\":\"2400f03e80d353ba989b66a8609f780e955d5dff\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000028-dispatch-claimed-1d7f9416b07a819c38b27bf7535ccc18.json\":\"792494e66a1d31529993832e3cc8b0940ab58244\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000029-dispatch-launch-authorized-551bf29f723465460beea589087e5adf.json\":\"905d1b7f02ebb4a4b59342de885a5120ca2ff089\",\"state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/events/00000030-dispatch-launch-lookup-recorded-cd54b307a841588acaa8f4ccb197323f.json\":\"20ced53e567b946554fdaaa166f64167c4fe78c8\"}")
    observed = json.loads("{\"run\":{\"id\":38018044654,\"run_attempt\":1,\"workflow_id\":380203519,\"path\":\".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml\",\"name\":\"AI-SDLC gh-aw dispatch-72f9f220eff8e8a1ab1577c22d3d680bb778abf9\",\"display_title\":\"AI-SDLC gh-aw dispatch-72f9f220eff8e8a1ab1577c22d3d680bb778abf9\",\"event\":\"workflow_dispatch\",\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"status\":\"completed\",\"conclusion\":\"success\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/actions/runs/38018044654\",\"created_at\":\"2026-10-10T02:44:11Z\",\"updated_at\":\"2026-10-10T02:49:32Z\",\"run_started_at\":\"2026-10-10T02:44:11Z\",\"repository\":{\"full_name\":\"DREAM-XIN/ai-sdlc\"}},\"jobs\":{\"total_count\":5,\"jobs\":[{\"id\":114112634510,\"run_id\":38018044654,\"run_attempt\":1,\"name\":\"activation\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"started_at\":\"2026-10-10T02:44:15Z\",\"completed_at\":\"2026-10-10T02:44:27Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-10T02:44:16Z\",\"completed_at\":\"2026-10-10T02:44:18Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-10T02:44:18Z\",\"completed_at\":\"2026-10-10T02:44:20Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-10T02:44:20Z\",\"completed_at\":\"2026-10-10T02:44:20Z\"},{\"name\":\"Generate agentic run info\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-10T02:44:20Z\",\"completed_at\":\"2026-10-10T02:44:21Z\"},{\"name\":\"Restore daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-10T02:44:21Z\",\"completed_at\":\"2026-10-10T02:44:21Z\"},{\"name\":\"Check daily workflow token guardrail\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-10T02:44:21Z\",\"completed_at\":\"2026-10-10T02:44:21Z\"},{\"name\":\"Publish daily AIC scan observations\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-10T02:44:21Z\",\"completed_at\":\"2026-10-10T02:44:21Z\"},{\"name\":\"Check for OAuth tokens\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-10T02:44:21Z\",\"completed_at\":\"2026-10-10T02:44:21Z\"},{\"name\":\"Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-10T02:44:21Z\",\"completed_at\":\"2026-10-10T02:44:22Z\"},{\"name\":\"Save agent config folders for base branch restoration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-10T02:44:22Z\",\"completed_at\":\"2026-10-10T02:44:22Z\"},{\"name\":\"Check workflow lock file\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-10T02:44:22Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Check compile-agentic version\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Log runtime features\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":13,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Create prompt with built-in context\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Interpolate variables and render templates\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Substitute placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Validate prompt placeholders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Print prompt\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:23Z\"},{\"name\":\"Upload info artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-10T02:44:23Z\",\"completed_at\":\"2026-10-10T02:44:24Z\"},{\"name\":\"Stage prompt files for artifact upload\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-10T02:44:24Z\",\"completed_at\":\"2026-10-10T02:44:24Z\"},{\"name\":\"Upload activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-10T02:44:24Z\",\"completed_at\":\"2026-10-10T02:44:25Z\"},{\"name\":\"Post Checkout .github and .agents folders\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-10T02:44:25Z\",\"completed_at\":\"2026-10-10T02:44:26Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-10T02:44:26Z\",\"completed_at\":\"2026-10-10T02:44:26Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-10T02:44:26Z\",\"completed_at\":\"2026-10-10T02:44:26Z\"}]},{\"id\":114112679654,\"run_id\":38018044654,\"run_attempt\":1,\"name\":\"agent\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"started_at\":\"2026-10-10T02:44:30Z\",\"completed_at\":\"2026-10-10T02:47:51Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-10T02:44:31Z\",\"completed_at\":\"2026-10-10T02:44:33Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-10T02:44:33Z\",\"completed_at\":\"2026-10-10T02:44:35Z\"},{\"name\":\"Reject rerun before model execution\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-10T02:44:35Z\",\"completed_at\":\"2026-10-10T02:44:35Z\"},{\"name\":\"Validate release-only local Worker identity\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-10T02:44:35Z\",\"completed_at\":\"2026-10-10T02:44:35Z\"},{\"name\":\"Set runtime paths\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-10T02:44:35Z\",\"completed_at\":\"2026-10-10T02:44:35Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-10T02:44:35Z\",\"completed_at\":\"2026-10-10T02:44:35Z\"},{\"name\":\"Check OTLP telemetry configuration\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-10T02:44:35Z\",\"completed_at\":\"2026-10-10T02:44:35Z\"},{\"name\":\"Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-10T02:44:35Z\",\"completed_at\":\"2026-10-10T02:44:36Z\"},{\"name\":\"Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-10T02:44:36Z\",\"completed_at\":\"2026-10-10T02:44:38Z\"},{\"name\":\"Build checkout manifest for safe-outputs handlers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-10T02:44:38Z\",\"completed_at\":\"2026-10-10T02:44:39Z\"},{\"name\":\"Initialize agent execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-10T02:44:39Z\",\"completed_at\":\"2026-10-10T02:44:39Z\"},{\"name\":\"Create gh-aw temp directory\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-10T02:44:39Z\",\"completed_at\":\"2026-10-10T02:44:39Z\"},{\"name\":\"Configure gh CLI for GitHub Enterprise\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-10T02:44:39Z\",\"completed_at\":\"2026-10-10T02:44:39Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-10T02:44:39Z\",\"completed_at\":\"2026-10-10T02:44:40Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-10T02:44:40Z\",\"completed_at\":\"2026-10-10T02:44:40Z\"},{\"name\":\"Checkout PR branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":16,\"started_at\":\"2026-10-10T02:44:40Z\",\"completed_at\":\"2026-10-10T02:44:40Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-10T02:44:40Z\",\"completed_at\":\"2026-10-10T02:44:48Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-10T02:44:48Z\",\"completed_at\":\"2026-10-10T02:44:49Z\"},{\"name\":\"Determine automatic lockdown mode for GitHub MCP Server\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-10T02:44:49Z\",\"completed_at\":\"2026-10-10T02:44:49Z\"},{\"name\":\"Parse integrity filter lists\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-10T02:44:49Z\",\"completed_at\":\"2026-10-10T02:44:49Z\"},{\"name\":\"Restore agent config folders from base branch\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":21,\"started_at\":\"2026-10-10T02:44:49Z\",\"completed_at\":\"2026-10-10T02:44:49Z\"},{\"name\":\"Restore inline sub-agents from activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":22,\"started_at\":\"2026-10-10T02:44:49Z\",\"completed_at\":\"2026-10-10T02:44:49Z\"},{\"name\":\"Restore inline skills from activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-10T02:44:49Z\",\"completed_at\":\"2026-10-10T02:44:49Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-10T02:44:49Z\",\"completed_at\":\"2026-10-10T02:44:59Z\"},{\"name\":\"Prepare Safe Outputs Directories\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":25,\"started_at\":\"2026-10-10T02:44:59Z\",\"completed_at\":\"2026-10-10T02:44:59Z\"},{\"name\":\"Generate Safe Outputs Config\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":26,\"started_at\":\"2026-10-10T02:44:59Z\",\"completed_at\":\"2026-10-10T02:44:59Z\"},{\"name\":\"Generate Safe Outputs Tools\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":27,\"started_at\":\"2026-10-10T02:44:59Z\",\"completed_at\":\"2026-10-10T02:44:59Z\"},{\"name\":\"Start MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":28,\"started_at\":\"2026-10-10T02:44:59Z\",\"completed_at\":\"2026-10-10T02:45:05Z\"},{\"name\":\"Mount MCP servers as CLIs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":29,\"started_at\":\"2026-10-10T02:45:05Z\",\"completed_at\":\"2026-10-10T02:45:05Z\"},{\"name\":\"Clean credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":30,\"started_at\":\"2026-10-10T02:45:05Z\",\"completed_at\":\"2026-10-10T02:45:05Z\"},{\"name\":\"Audit pre-agent workspace\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":31,\"started_at\":\"2026-10-10T02:45:05Z\",\"completed_at\":\"2026-10-10T02:45:05Z\"},{\"name\":\"Execute GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":32,\"started_at\":\"2026-10-10T02:45:05Z\",\"completed_at\":\"2026-10-10T02:47:41Z\"},{\"name\":\"Detect agent errors\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":33,\"started_at\":\"2026-10-10T02:47:41Z\",\"completed_at\":\"2026-10-10T02:47:41Z\"},{\"name\":\"Configure Git credentials\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":34,\"started_at\":\"2026-10-10T02:47:41Z\",\"completed_at\":\"2026-10-10T02:47:41Z\"},{\"name\":\"Copy Copilot session state files to logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":35,\"started_at\":\"2026-10-10T02:47:41Z\",\"completed_at\":\"2026-10-10T02:47:41Z\"},{\"name\":\"Stop MCP Gateway\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":36,\"started_at\":\"2026-10-10T02:47:41Z\",\"completed_at\":\"2026-10-10T02:47:43Z\"},{\"name\":\"Redact secrets in logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":37,\"started_at\":\"2026-10-10T02:47:43Z\",\"completed_at\":\"2026-10-10T02:47:43Z\"},{\"name\":\"Append agent step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":38,\"started_at\":\"2026-10-10T02:47:43Z\",\"completed_at\":\"2026-10-10T02:47:43Z\"},{\"name\":\"Copy Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":39,\"started_at\":\"2026-10-10T02:47:43Z\",\"completed_at\":\"2026-10-10T02:47:43Z\"},{\"name\":\"Ingest agent output\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":40,\"started_at\":\"2026-10-10T02:47:43Z\",\"completed_at\":\"2026-10-10T02:47:43Z\"},{\"name\":\"Parse agent logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":41,\"started_at\":\"2026-10-10T02:47:43Z\",\"completed_at\":\"2026-10-10T02:47:44Z\"},{\"name\":\"Parse MCP Gateway logs for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":42,\"started_at\":\"2026-10-10T02:47:44Z\",\"completed_at\":\"2026-10-10T02:47:44Z\"},{\"name\":\"Print firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":43,\"started_at\":\"2026-10-10T02:47:44Z\",\"completed_at\":\"2026-10-10T02:47:45Z\"},{\"name\":\"Parse token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":44,\"started_at\":\"2026-10-10T02:47:45Z\",\"completed_at\":\"2026-10-10T02:47:45Z\"},{\"name\":\"Print AWF reflect summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":45,\"started_at\":\"2026-10-10T02:47:45Z\",\"completed_at\":\"2026-10-10T02:47:45Z\"},{\"name\":\"Generate observability summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":46,\"started_at\":\"2026-10-10T02:47:45Z\",\"completed_at\":\"2026-10-10T02:47:45Z\"},{\"name\":\"Write agent output placeholder if missing\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":47,\"started_at\":\"2026-10-10T02:47:45Z\",\"completed_at\":\"2026-10-10T02:47:45Z\"},{\"name\":\"Upload agent output fallback artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":48,\"started_at\":\"2026-10-10T02:47:45Z\",\"completed_at\":\"2026-10-10T02:47:46Z\"},{\"name\":\"Upload agent artifacts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":49,\"started_at\":\"2026-10-10T02:47:46Z\",\"completed_at\":\"2026-10-10T02:47:47Z\"},{\"name\":\"Post Checkout dream-xin/ai-sdlc into ai-sdlc\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":96,\"started_at\":\"2026-10-10T02:47:47Z\",\"completed_at\":\"2026-10-10T02:47:48Z\"},{\"name\":\"Post Checkout repository\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":97,\"started_at\":\"2026-10-10T02:47:48Z\",\"completed_at\":\"2026-10-10T02:47:48Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":98,\"started_at\":\"2026-10-10T02:47:48Z\",\"completed_at\":\"2026-10-10T02:47:48Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":99,\"started_at\":\"2026-10-10T02:47:48Z\",\"completed_at\":\"2026-10-10T02:47:48Z\"}]},{\"id\":114113343613,\"run_id\":38018044654,\"run_attempt\":1,\"name\":\"detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"started_at\":\"2026-10-10T02:47:53Z\",\"completed_at\":\"2026-10-10T02:48:59Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-10T02:47:54Z\",\"completed_at\":\"2026-10-10T02:47:55Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-10T02:47:55Z\",\"completed_at\":\"2026-10-10T02:47:58Z\"},{\"name\":\"Download activation artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-10T02:47:58Z\",\"completed_at\":\"2026-10-10T02:47:59Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-10T02:47:59Z\",\"completed_at\":\"2026-10-10T02:48:00Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-10T02:48:00Z\",\"completed_at\":\"2026-10-10T02:48:00Z\"},{\"name\":\"Checkout repository for patch context\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":6,\"started_at\":\"2026-10-10T02:48:00Z\",\"completed_at\":\"2026-10-10T02:48:00Z\"},{\"name\":\"Initialize detection execution evidence\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-10T02:48:00Z\",\"completed_at\":\"2026-10-10T02:48:00Z\"},{\"name\":\"Clear inherited Copilot session state\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-10T02:48:00Z\",\"completed_at\":\"2026-10-10T02:48:00Z\"},{\"name\":\"Clean stale firewall files from agent artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-10T02:48:00Z\",\"completed_at\":\"2026-10-10T02:48:00Z\"},{\"name\":\"Download container images\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":10,\"started_at\":\"2026-10-10T02:48:00Z\",\"completed_at\":\"2026-10-10T02:48:10Z\"},{\"name\":\"Check if detection needed\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":11,\"started_at\":\"2026-10-10T02:48:10Z\",\"completed_at\":\"2026-10-10T02:48:10Z\"},{\"name\":\"Clear MCP Config for detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-10T02:48:10Z\",\"completed_at\":\"2026-10-10T02:48:10Z\"},{\"name\":\"Prepare threat detection files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-10T02:48:10Z\",\"completed_at\":\"2026-10-10T02:48:11Z\"},{\"name\":\"Setup threat detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-10T02:48:11Z\",\"completed_at\":\"2026-10-10T02:48:11Z\"},{\"name\":\"Ensure threat-detection directory and log\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-10T02:48:11Z\",\"completed_at\":\"2026-10-10T02:48:11Z\"},{\"name\":\"Install AWF binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-10T02:48:11Z\",\"completed_at\":\"2026-10-10T02:48:12Z\"},{\"name\":\"Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-10T02:48:12Z\",\"completed_at\":\"2026-10-10T02:48:12Z\"},{\"name\":\"Install GitHub Copilot CLI\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-10T02:48:12Z\",\"completed_at\":\"2026-10-10T02:48:19Z\"},{\"name\":\"Install threat-detect binary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-10T02:48:19Z\",\"completed_at\":\"2026-10-10T02:48:19Z\"},{\"name\":\"Execute threat detection with AWF\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":20,\"started_at\":\"2026-10-10T02:48:19Z\",\"completed_at\":\"2026-10-10T02:48:55Z\"},{\"name\":\"Render detection log\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":21,\"started_at\":\"2026-10-10T02:48:55Z\",\"completed_at\":\"2026-10-10T02:48:55Z\"},{\"name\":\"Copy detection firewall logs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":22,\"started_at\":\"2026-10-10T02:48:55Z\",\"completed_at\":\"2026-10-10T02:48:55Z\"},{\"name\":\"Parse threat detection token usage for step summary\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":23,\"started_at\":\"2026-10-10T02:48:55Z\",\"completed_at\":\"2026-10-10T02:48:56Z\"},{\"name\":\"Upload threat detection artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":24,\"started_at\":\"2026-10-10T02:48:56Z\",\"completed_at\":\"2026-10-10T02:48:57Z\"},{\"name\":\"Conclude threat detection\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":25,\"started_at\":\"2026-10-10T02:48:57Z\",\"completed_at\":\"2026-10-10T02:48:57Z\"},{\"name\":\"Post Setup Node.js\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":49,\"started_at\":\"2026-10-10T02:48:57Z\",\"completed_at\":\"2026-10-10T02:48:57Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":50,\"started_at\":\"2026-10-10T02:48:57Z\",\"completed_at\":\"2026-10-10T02:48:57Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":51,\"started_at\":\"2026-10-10T02:48:57Z\",\"completed_at\":\"2026-10-10T02:48:57Z\"}]},{\"id\":114113561614,\"run_id\":38018044654,\"run_attempt\":1,\"name\":\"safe_outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"started_at\":\"2026-10-10T02:49:02Z\",\"completed_at\":\"2026-10-10T02:49:13Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-10T02:49:03Z\",\"completed_at\":\"2026-10-10T02:49:05Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-10T02:49:05Z\",\"completed_at\":\"2026-10-10T02:49:07Z\"},{\"name\":\"Mask OTLP telemetry headers\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-10T02:49:07Z\",\"completed_at\":\"2026-10-10T02:49:07Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-10T02:49:07Z\",\"completed_at\":\"2026-10-10T02:49:08Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-10T02:49:08Z\",\"completed_at\":\"2026-10-10T02:49:08Z\"},{\"name\":\"Configure GH_HOST for enterprise compatibility\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-10T02:49:08Z\",\"completed_at\":\"2026-10-10T02:49:09Z\"},{\"name\":\"Require first attempt and affirmative detection before Safe Outputs effects\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-10T02:49:09Z\",\"completed_at\":\"2026-10-10T02:49:09Z\"},{\"name\":\"Process Safe Outputs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-10T02:49:09Z\",\"completed_at\":\"2026-10-10T02:49:10Z\"},{\"name\":\"Upload Safe Outputs Items\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-10T02:49:10Z\",\"completed_at\":\"2026-10-10T02:49:11Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":18,\"started_at\":\"2026-10-10T02:49:11Z\",\"completed_at\":\"2026-10-10T02:49:11Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":19,\"started_at\":\"2026-10-10T02:49:11Z\",\"completed_at\":\"2026-10-10T02:49:11Z\"}]},{\"id\":114113607288,\"run_id\":38018044654,\"run_attempt\":1,\"name\":\"conclusion\",\"status\":\"completed\",\"conclusion\":\"success\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"started_at\":\"2026-10-10T02:49:16Z\",\"completed_at\":\"2026-10-10T02:49:31Z\",\"steps\":[{\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":1,\"started_at\":\"2026-10-10T02:49:17Z\",\"completed_at\":\"2026-10-10T02:49:20Z\"},{\"name\":\"Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":2,\"started_at\":\"2026-10-10T02:49:20Z\",\"completed_at\":\"2026-10-10T02:49:22Z\"},{\"name\":\"Record non-authoritative Gate execution identity\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":3,\"started_at\":\"2026-10-10T02:49:22Z\",\"completed_at\":\"2026-10-10T02:49:22Z\"},{\"name\":\"Download agent output artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":4,\"started_at\":\"2026-10-10T02:49:22Z\",\"completed_at\":\"2026-10-10T02:49:23Z\"},{\"name\":\"Setup agent output environment variable\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":5,\"started_at\":\"2026-10-10T02:49:23Z\",\"completed_at\":\"2026-10-10T02:49:23Z\"},{\"name\":\"Download detection artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":6,\"started_at\":\"2026-10-10T02:49:23Z\",\"completed_at\":\"2026-10-10T02:49:24Z\"},{\"name\":\"Download Safe Outputs Items Manifest\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":7,\"started_at\":\"2026-10-10T02:49:24Z\",\"completed_at\":\"2026-10-10T02:49:25Z\"},{\"name\":\"Collect usage artifact files\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":8,\"started_at\":\"2026-10-10T02:49:25Z\",\"completed_at\":\"2026-10-10T02:49:25Z\"},{\"name\":\"Upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":9,\"started_at\":\"2026-10-10T02:49:25Z\",\"completed_at\":\"2026-10-10T02:49:26Z\"},{\"name\":\"Wait before retrying usage artifact upload\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":10,\"started_at\":\"2026-10-10T02:49:26Z\",\"completed_at\":\"2026-10-10T02:49:26Z\"},{\"name\":\"Retry upload usage artifact\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"number\":11,\"started_at\":\"2026-10-10T02:49:26Z\",\"completed_at\":\"2026-10-10T02:49:26Z\"},{\"name\":\"Process no-op messages\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":12,\"started_at\":\"2026-10-10T02:49:26Z\",\"completed_at\":\"2026-10-10T02:49:26Z\"},{\"name\":\"Log detection run\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":13,\"started_at\":\"2026-10-10T02:49:26Z\",\"completed_at\":\"2026-10-10T02:49:27Z\"},{\"name\":\"Record missing tool\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":14,\"started_at\":\"2026-10-10T02:49:27Z\",\"completed_at\":\"2026-10-10T02:49:27Z\"},{\"name\":\"Record incomplete\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":15,\"started_at\":\"2026-10-10T02:49:27Z\",\"completed_at\":\"2026-10-10T02:49:27Z\"},{\"name\":\"Handle agent failure\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":16,\"started_at\":\"2026-10-10T02:49:27Z\",\"completed_at\":\"2026-10-10T02:49:28Z\"},{\"name\":\"Report failed jobs\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":17,\"started_at\":\"2026-10-10T02:49:28Z\",\"completed_at\":\"2026-10-10T02:49:29Z\"},{\"name\":\"Post Setup Scripts\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":34,\"started_at\":\"2026-10-10T02:49:29Z\",\"completed_at\":\"2026-10-10T02:49:29Z\"},{\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\",\"number\":35,\"started_at\":\"2026-10-10T02:49:29Z\",\"completed_at\":\"2026-10-10T02:49:29Z\"}]}]},\"artifacts\":{\"total_count\":7,\"artifacts\":[{\"id\":11657725741,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1NzcyNTc0MQ==\",\"name\":\"agent-output-fallback\",\"size_in_bytes\":7958,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657725741\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657725741/zip\",\"expired\":false,\"digest\":\"sha256:941127d655b47a3ca4614ea22f7467c012a0a0c7faa2e6e3521bf5bf8d2913c7\",\"created_at\":\"2026-10-10T02:47:46Z\",\"updated_at\":\"2026-10-10T02:47:46Z\",\"expires_at\":\"2027-01-08T02:44:13Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}},{\"id\":11657685930,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1NzY4NTkzMA==\",\"name\":\"safe-outputs-items\",\"size_in_bytes\":495,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657685930\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657685930/zip\",\"expired\":false,\"digest\":\"sha256:46eac190846d28821169713bcfed4cb089dcd792003d07b108786142fbeda8c9\",\"created_at\":\"2026-10-10T02:49:11Z\",\"updated_at\":\"2026-10-10T02:49:11Z\",\"expires_at\":\"2027-01-08T02:44:13Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}},{\"id\":11657560510,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1NzU2MDUxMA==\",\"name\":\"activation\",\"size_in_bytes\":1147877,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657560510\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657560510/zip\",\"expired\":false,\"digest\":\"sha256:a6cf914a0cdef5b009e71e2403a45202853af221905ebe7776a015d253732e4d\",\"created_at\":\"2026-10-10T02:44:25Z\",\"updated_at\":\"2026-10-10T02:44:25Z\",\"expires_at\":\"2026-10-11T02:44:24Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}},{\"id\":11657410505,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1NzQxMDUwNQ==\",\"name\":\"info\",\"size_in_bytes\":632,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657410505\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657410505/zip\",\"expired\":false,\"digest\":\"sha256:e7902061a46f7eb7d27d9dd38ccf9026ffa4a55f0c86e788d6d0374bd74f21f2\",\"created_at\":\"2026-10-10T02:44:24Z\",\"updated_at\":\"2026-10-10T02:44:24Z\",\"expires_at\":\"2027-01-08T02:44:13Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}},{\"id\":11657156209,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1NzE1NjIwOQ==\",\"name\":\"agent\",\"size_in_bytes\":2201123,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657156209\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11657156209/zip\",\"expired\":false,\"digest\":\"sha256:3a8a0b6440bfe557588b30ad37b76dec1798531ad43cc8fe0c44e90f5ad5a2d4\",\"created_at\":\"2026-10-10T02:47:47Z\",\"updated_at\":\"2026-10-10T02:47:47Z\",\"expires_at\":\"2027-01-08T02:44:13Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}},{\"id\":11656898673,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1Njg5ODY3Mw==\",\"name\":\"detection\",\"size_in_bytes\":21998,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11656898673\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11656898673/zip\",\"expired\":false,\"digest\":\"sha256:a561a574ae8e37ae1929073845527efb2be6c45da1a4b0f1c408ba4eab4769fe\",\"created_at\":\"2026-10-10T02:48:56Z\",\"updated_at\":\"2026-10-10T02:48:56Z\",\"expires_at\":\"2027-01-08T02:44:13Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}},{\"id\":11656628927,\"node_id\":\"MDg6QXJ0aWZhY3QxMTY1NjYyODkyNw==\",\"name\":\"usage\",\"size_in_bytes\":6128,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11656628927\",\"archive_download_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/actions/artifacts/11656628927/zip\",\"expired\":false,\"digest\":\"sha256:15143685c0b1edfbd19531973ac307ee9b975eff1e08d5075fe1af34716cb60c\",\"created_at\":\"2026-10-10T02:49:26Z\",\"updated_at\":\"2026-10-10T02:49:26Z\",\"expires_at\":\"2027-01-08T02:44:13Z\",\"workflow_run\":{\"id\":38018044654,\"repository_id\":1326302284,\"head_repository_id\":1326302284,\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\"}}]},\"comment\":{\"id\":6092979158,\"node_id\":\"IC_kwDOTw3ETM8AAAABayt71g\",\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/comments/6092979158\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/pull/552#issuecomment-6092979158\",\"issue_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/issues/552\",\"body\":\"## AI-SDLC Code Review — F-OPERATOR-V03-DOGFOOD-HAPPY-0001\\n\\n```json\\n{\\\"version\\\":\\\"0.1.0\\\",\\\"contract\\\":\\\"ai-sdlc-gh-aw-reviewer-result-v0.1\\\",\\\"id\\\":\\\"vertical:code-review:41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\\\",\\\"feature_id\\\":\\\"F-OPERATOR-V03-DOGFOOD-HAPPY-0001\\\",\\\"task_id\\\":\\\"vertical:code-review:41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\\\",\\\"stage\\\":\\\"code-review\\\",\\\"role\\\":\\\"reviewer\\\",\\\"expected_revision\\\":3,\\\"target_repository\\\":\\\"dream-xin/ai-sdlc\\\",\\\"target_ref\\\":\\\"dogfood/v0.3-happy-path-0001\\\",\\\"candidate_pr_number\\\":552,\\\"candidate_head_sha\\\":\\\"41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\\\",\\\"verdict\\\":\\\"REWORK\\\",\\\"findings\\\":[{\\\"code\\\":\\\"CANDIDATE-BINDING-MISSING\\\",\\\"severity\\\":\\\"BLOCKER\\\",\\\"message\\\":\\\"The Feature Manifest state/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001.yaml (revision 3) contains no PR-bound implementation candidate. Its single draft implementation artifact (vertical-artifact-3ac60f9b63c79ca4168c) has uri docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/worker-runs/vertical-31df3f1ed41b54c58ed4c4030a9f97d9/developer-pr-577-...json, which is not a canonical https://github.com/<owner>/<repo>/pull/<n> URL, and there is no matching implementation-head-* artifact. scripts/gh_aw_candidate.py resolve_current_candidate() therefore raises CandidateError, so the Reviewer PASS path (scripts/operator_vertical.py _draft_implementation_artifact / translate_reviewer_result and scripts/gh_aw_gate_result.py reviewer_event) cannot bind the candidate and cannot translate a PASS into a valid Feature Event. The implementation-done Event EVT-...-VERTICAL-IMPLEMENTATION-DONE-454A443721B6 recorded only the collector file artifact and never emitted the implementation-candidate-*/implementation-head-* records that scripts/gh_aw_candidate_event.py is designed to add.\\\"},{\\\"code\\\":\\\"CI-EVIDENCE-ABSENT\\\",\\\"severity\\\":\\\"MAJOR\\\",\\\"message\\\":\\\"The candidate head commit 41e0df7089c5907b00bbaeac5dd2be71d4f02d4b has zero check runs and zero commit statuses (combined state pending, total_count 0). No required CI evidence exists on the exact reviewed head, so independent verification of the candidate cannot be established from CI.\\\"},{\\\"code\\\":\\\"ARTIFACT-URI-UNRESOLVABLE\\\",\\\"severity\\\":\\\"MINOR\\\",\\\"message\\\":\\\"The manifest implementation artifact URI docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/worker-runs/vertical-31df3f1ed41b54c58ed4c4030a9f97d9/developer-pr-577-...json does not resolve in the candidate tree at 41e0df70 (no worker-runs directory exists). These URIs are collector-materialized rather than committed, so the artifact cannot be independently re-read from the candidate ref.\\\"}],\\\"evidence\\\":[{\\\"id\\\":\\\"review-comment-552\\\",\\\"type\\\":\\\"review\\\",\\\"status\\\":\\\"fail\\\",\\\"uri\\\":\\\"https://github.com/DREAM-XIN/ai-sdlc/pull/552\\\"}],\\\"occurred_at\\\":\\\"2026-10-10T02:45:23Z\\\",\\\"reason\\\":\\\"Reviewer recommends REWORK: the reviewed candidate is not bound as a resolvable PR candidate in the Feature Manifest, so a PASS verdict could not be translated into a valid Feature Event, and no CI evidence exists on the exact head.\\\"}\\n```\\n\\n### Summary\\n\\n**Verdict: REWORK** (non-authoritative recommendation)\\n\\n**Identity verified.** Local checkout HEAD, PR #552 head, and the trusted `candidate_head_sha` all equal `41e0df7089c5907b00bbaeac5dd2be71d4f02d4b`. Feature `F-OPERATOR-V03-DOGFOOD-HAPPY-0001`, stage `code-review`, role `reviewer`, repository `dream-xin/ai-sdlc`, revision 3 all match the task payload.\\n\\n**What is correct.** The implementation candidate `docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md` exists at the exact head and contains the required contract marker `dogfood_result: happy-path` (line 8). The change set is documentation-only and touches no product code, Gate policy, or runtime configuration. The manifest shows `code-review: WORKING` with `code-gate: PENDING`, consistent with an in-flight review.\\n\\n**Why not PASS.**\\n\\n1. **BLOCKER — candidate binding missing.** The manifest has no PR-bound candidate. `resolve_current_candidate()` requires exactly one draft `implementation` artifact with a canonical `https://github.com/.../pull/N` URI plus a matching `implementation-head-*` artifact. The manifest's only `implementation` artifact points at a `docs/.../worker-runs/...json` path, and no `implementation-head-*` artifact exists. Both Reviewer PASS paths (`operator_vertical.translate_reviewer_result` and `gh_aw_gate_result.reviewer_event`) depend on this resolution, so a PASS could not be translated into a valid Feature Event.\\n2. **MAJOR — no CI evidence.** The exact head has zero check runs and zero commit statuses.\\n3. **MINOR — unresolvable artifact URI.** The referenced `worker-runs` JSON is collector-materialized and absent from the candidate tree.\\n\\n**Required remediation.** Emit the candidate-binding records (`implementation-candidate-<sha12>` and `implementation-head-<sha12>`) for PR #552 / `41e0df70` via the trusted candidate-enrichment path, and attach required CI evidence to the exact head before re-review.\\n\\nThis comment is explicitly non-authoritative; lifecycle authority remains the protected Operator Store plus canonical Feature Persist.\\n\\n<!-- gh-aw-agentic-workflow: AI-SDLC gh-aw Code Reviewer (deepseek v0.3 bounded local), engine: copilot, model: deepseek-chat, id: 38018044654, workflow_id: ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local, run: https://github.com/DREAM-XIN/ai-sdlc/actions/runs/38018044654 -->\\n<!-- gh-aw-workflow-call-id: DREAM-XIN/ai-sdlc/ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local -->\",\"created_at\":\"2026-10-10T02:49:10Z\",\"updated_at\":\"2026-10-10T02:49:10Z\",\"author_association\":\"NONE\",\"user\":{\"login\":\"github-actions[bot]\",\"id\":41898282,\"node_id\":\"MDM6Qm90NDE4OTgyODI=\",\"avatar_url\":\"https://avatars.githubusercontent.com/in/15368?v=4\",\"gravatar_id\":\"\",\"url\":\"https://api.github.com/users/github-actions%5Bbot%5D\",\"html_url\":\"https://github.com/apps/github-actions\",\"followers_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/followers\",\"following_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/following{/other_user}\",\"gists_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/gists{/gist_id}\",\"starred_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/starred{/owner}{/repo}\",\"subscriptions_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/subscriptions\",\"organizations_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/orgs\",\"repos_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/repos\",\"events_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/events{/privacy}\",\"received_events_url\":\"https://api.github.com/users/github-actions%5Bbot%5D/received_events\",\"type\":\"Bot\",\"user_view_type\":\"public\",\"site_admin\":false}}}")
    documents = json.loads("{\"docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\":{\"sha\":\"6f7275895c721300a830a74a7dffb3760b8556c9\",\"text\":\"# v0.3 real release dogfood fixture — happy_path\\n\\nFeature: `F-OPERATOR-V03-DOGFOOD-HAPPY-0001`  \\nFixed ref: `dogfood/v0.3-happy-path-0001`\\nScenario task artifact: `dogfood-scenario-task`\\n\\nCreate one minimal documentation-only implementation candidate under this Feature. The candidate must contain `dogfood_result: happy-path` and no unrelated changes. Independent Reviewer and QA should PASS only if that exact contract is satisfied.\\n\\nThis release-only slot is independent from all Issue #221 fault-injection fixtures. It must not\\nbe reset, force-pushed, recycled, or merged as a product change. Worker/model output is evidence\\nonly; lifecycle authority remains the protected Operator Store plus canonical Feature Persist.\\nProduct Acceptance is not performed by this fixture; the Feature may become `acceptance: READY`\\nwhile the dogfood Operation itself reaches its reviewed terminal status.\\n\"},\"docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md\":{\"sha\":\"06e53ed96ff23f5e6079ec1ba63d811663fc3b33\",\"text\":\"# Implementation — F-OPERATOR-V03-DOGFOOD-HAPPY-0001\\n\\nFeature: `F-OPERATOR-V03-DOGFOOD-HAPPY-0001`\\nFixed ref: `dogfood/v0.3-happy-path-0001`\\nScenario task artifact: `dogfood-scenario-task`\\n\\n```yaml\\ndogfood_result: happy-path\\n```\\n\\n## Scope\\n\\nThis is the minimal documentation-only implementation candidate required by\\n`docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md`. It contains the exact\\n`dogfood_result: happy-path` contract marker and no unrelated changes.\\n\\nNo product code, lifecycle state, Feature Manifest, Feature Event, Gate policy, or runtime\\nconfiguration is modified by this candidate. Lifecycle authority remains the protected Operator\\nStore plus canonical Feature Persist; this document is evidence only.\\n\"}}")
    listing = json.loads("[{\"name\":\"dogfood-task.md\",\"path\":\"docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\",\"sha\":\"6f7275895c721300a830a74a7dffb3760b8556c9\",\"size\":895,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/contents/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md?ref=41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/blob/41e0df7089c5907b00bbaeac5dd2be71d4f02d4b/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\",\"git_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/git/blobs/6f7275895c721300a830a74a7dffb3760b8556c9\",\"download_url\":\"https://raw.githubusercontent.com/DREAM-XIN/ai-sdlc/41e0df7089c5907b00bbaeac5dd2be71d4f02d4b/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\",\"type\":\"file\",\"_links\":{\"self\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/contents/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md?ref=41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\",\"git\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/git/blobs/6f7275895c721300a830a74a7dffb3760b8556c9\",\"html\":\"https://github.com/DREAM-XIN/ai-sdlc/blob/41e0df7089c5907b00bbaeac5dd2be71d4f02d4b/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/dogfood-task.md\"}},{\"name\":\"implementation.md\",\"path\":\"docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md\",\"sha\":\"06e53ed96ff23f5e6079ec1ba63d811663fc3b33\",\"size\":736,\"url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/contents/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md?ref=41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\",\"html_url\":\"https://github.com/DREAM-XIN/ai-sdlc/blob/41e0df7089c5907b00bbaeac5dd2be71d4f02d4b/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md\",\"git_url\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/git/blobs/06e53ed96ff23f5e6079ec1ba63d811663fc3b33\",\"download_url\":\"https://raw.githubusercontent.com/DREAM-XIN/ai-sdlc/41e0df7089c5907b00bbaeac5dd2be71d4f02d4b/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md\",\"type\":\"file\",\"_links\":{\"self\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/contents/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md?ref=41e0df7089c5907b00bbaeac5dd2be71d4f02d4b\",\"git\":\"https://api.github.com/repos/DREAM-XIN/ai-sdlc/git/blobs/06e53ed96ff23f5e6079ec1ba63d811663fc3b33\",\"html\":\"https://github.com/DREAM-XIN/ai-sdlc/blob/41e0df7089c5907b00bbaeac5dd2be71d4f02d4b/docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/implementation.md\"}}]")
    log_excerpts = json.loads("{\"114113343613\":\"2026-10-10T02:48:11.0153973Z   GH_AW_DETECTION_CONTINUE_ON_ERROR: false\\n2026-10-10T02:48:19.5568450Z   GH_AW_DETECTION_CONTINUE_ON_ERROR: false\\n2026-10-10T02:48:53.5743242Z THREAT_DETECTION_STATUS: reason=result_recorded exit=0\\n2026-10-10T02:48:55.9040688Z THREAT_DETECTION_STATUS: reason=result_recorded exit=0\\n2026-10-10T02:48:57.0800274Z   DETECTION_AGENTIC_EXECUTION_OUTCOME: success\\n2026-10-10T02:48:57.0800926Z   GH_AW_DETECTION_CONTINUE_ON_ERROR: false\\n2026-10-10T02:48:57.0938841Z 📋 detection execution outcome: \\\"success\\\"\\n2026-10-10T02:48:57.0950563Z    prompt_injection : false\\n2026-10-10T02:48:57.0951083Z    secret_leak      : false\\n2026-10-10T02:48:57.0951588Z    malicious_patch  : false\\n\",\"114113561614\":\"2026-10-10T02:49:05.8931028Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:07.7185689Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:07.7540547Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:08.9806995Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:08.9978712Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:09.0077567Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:09.0087155Z   DETECTION_CONCLUSION: success\\n2026-10-10T02:49:09.0087682Z   DETECTION_SUCCESS: true\\n2026-10-10T02:49:09.0226534Z   GH_AW_DETECTION_CONCLUSION: success\\n2026-10-10T02:49:10.4079918Z Created comment: https://github.com/DREAM-XIN/ai-sdlc/pull/552#issuecomment-6092979158\\n2026-10-10T02:49:10.4081765Z 📝 Manifest: logged add_comment → https://github.com/DREAM-XIN/ai-sdlc/pull/552#issuecomment-6092979158\\n2026-10-10T02:49:10.4166100Z Exported comment_id: 6092979158\\n2026-10-10T02:49:10.4361758Z   GH_AW_DETECTION_CONCLUSION: success\\n\",\"114112184854\":\"2026-10-10T02:49:49.9516755Z operator_vertical.VerticalInvariantError: Gate Safe Output comment has invalid machine envelope\\n\"}")

    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest()

    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", commit, "state/operator/v1",
         "config/operator/v03-vertical-policy"],
        cwd=root, check=True, capture_output=True, text=True).stdout.splitlines()
    raw_files = {path: subprocess.run(
        ["git", "show", commit + ":" + path], cwd=root, check=True, capture_output=True).stdout
        for path in listed if path.endswith(".json")}
    expect({path for path in raw_files if path.startswith(operation_root)} == set(pins),
           "structured fixture changed exact frozen operation document set")
    expect(all(blob(raw_files[path]) == sha for path, sha in pins.items()),
           "structured fixture changed frozen operation bytes")
    expect(all(raw_files[path] == raw for path, raw in provider.frozen_operation_raw_files.items()),
           "structured fixture rewrote predecessor operation records")
    snapshot = StoreSnapshot(commit, {path: json.loads(raw) for path, raw in raw_files.items()})
    events = operation_events(snapshot, operation_id)
    projection = vertical_projection(snapshot, operation_id)
    expect(events == provider.frozen_events and len(events) == 30
           and projection["generation"] == 1 and projection["status"] == "WAITING_EXTERNAL"
           and projection["expected_feature_revision"] == 3,
           "structured fixture advanced the historical code-review wait")
    historical_sidecars = {}
    for name in ("dogfood-reviewer-pre-model-replacement-1",
                 "dogfood-reviewer-post-model-replacement-2"):
        prefix = operation_root + name + "/"
        paths = {path for path in raw_files if path.startswith(prefix)}
        expect(paths == {prefix + "authorization.json", prefix + "create-claim.json"},
               "historical replacement must retain consumed claim without fabricated seal")
        historical_sidecars.update({path: bytes(raw_files[path]) for path in paths})
    expect(observed["run"]["id"] == 38018044654 and observed["run"]["run_attempt"] == 1
           and observed["run"]["conclusion"] == "success"
           and observed["run"]["head_sha"] == "ff2fcfebfceaef2baf4edc2a6de2ab820760d48b"
           and observed["comment"]["id"] == 6092979158
           and '"verdict":"REWORK"' in observed["comment"]["body"],
           "structured fixture changed authentic historical Reviewer result")
    expect(len(observed["jobs"]["jobs"]) == observed["jobs"]["total_count"] == 5
           and all(job["conclusion"] == "success" for job in observed["jobs"]["jobs"]),
           "structured fixture changed successful historical provider jobs")
    expect(all(blob(document["text"].encode("utf-8")) == document["sha"]
               for document in documents.values()),
           "structured fixture changed immutable candidate content bytes")
    expect({row["path"] for row in listing} == set(documents)
           and all(row["type"] == "file" and row["sha"] == documents[row["path"]]["sha"]
                   and row["size"] == len(documents[row["path"]]["text"].encode("utf-8"))
                   for row in listing),
           "structured fixture candidate directory listing differs from captured files")

    provider.snapshot = snapshot
    provider.frozen_events = deepcopy(events)
    provider.frozen_operation_raw_files = {path: bytes(raw_files[path]) for path in pins}
    provider.historical_reviewer_sidecar_raw_files = historical_sidecars
    provider.original_structured_failure_run = deepcopy(observed["run"])
    provider.original_structured_failure_jobs = deepcopy(observed["jobs"])
    provider.original_structured_failure_comment = deepcopy(observed["comment"])
    provider.original_structured_failure_artifacts = deepcopy(observed["artifacts"])
    provider.original_structured_failure_body_bytes = observed["comment"]["body"].encode("utf-8")
    provider.original_structured_failure_log_excerpts = deepcopy(log_excerpts)
    provider.immutable_candidate_documents = deepcopy(documents)
    state = provider.state
    state["reviewer_structured_observed"] = observed
    state["reviewer_structured_log_excerpts"] = log_excerpts
    state["reviewer_structured_predecessor_commit"] = commit
    old_http = provider.http

    def response(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def paginate(rows, query):
        page = int(query.get("page", ["1"])[0])
        per_page = int(query.get("per_page", ["100"])[0])
        expect(page > 0 and 1 <= per_page <= 100, "malformed provider pagination")
        return deepcopy(rows[(page - 1) * per_page:page * per_page])

    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/dream-xin/ai-sdlc"
        expect(parsed.scheme == "https" and parsed.netloc == "api.github.com"
               and parsed.path.lower().startswith(prefix + "/"),
               "structured fixture escaped its exact provider repository")
        expect(method == "GET", "frozen structured provider attempted external effect: " + method)
        path = unquote(parsed.path[len(prefix):])
        query = parse_qs(parsed.query)
        current = state["reviewer_structured_observed"]
        run_id = int(current["run"]["id"])
        if path in {f"/actions/runs/{run_id}", f"/actions/runs/{run_id}/attempts/1"}:
            state["calls"].append((method, path))
            return response(deepcopy(current["run"]))
        if path in {f"/actions/runs/{run_id}/jobs", f"/actions/runs/{run_id}/attempts/1/jobs"}:
            state["calls"].append((method, path))
            payload = current["jobs"]
            return response({"total_count": payload["total_count"], "jobs": paginate(payload["jobs"], query)})
        if path == f"/actions/runs/{run_id}/artifacts":
            state["calls"].append((method, path))
            payload = current["artifacts"]
            return response({"total_count": payload["total_count"],
                             "artifacts": paginate(payload["artifacts"], query)})
        if path.startswith("/actions/jobs/") and path.endswith("/logs"):
            job_id = path[len("/actions/jobs/"):-len("/logs")]
            if job_id in state["reviewer_structured_log_excerpts"]:
                state["calls"].append((method, path))
                return response(state["reviewer_structured_log_excerpts"][job_id].encode("utf-8"))
        if path == "/actions/runs" or (path.startswith("/actions/workflows/") and path.endswith("/runs")):
            state["calls"].append((method, path))
            rows = [current["run"], state["reviewer_post_model_observed"]["run"],
                    state["reviewer_observed"]["run"], state["observed"]["run"]]
            if path != "/actions/runs":
                workflow = path[len("/actions/workflows/"):-len("/runs")]
                rows = [row for row in rows if workflow in
                        {row["path"].rsplit("/", 1)[-1], str(row["workflow_id"])}]
            return response({"total_count": len(rows), "workflow_runs": paginate(rows, query)})
        if path == "/issues/comments/6092979158":
            state["calls"].append((method, path))
            return response(deepcopy(current["comment"]))
        if path == "/issues/552/comments":
            state["calls"].append((method, path))
            rows = [*state["reviewer_post_model_observed"]["comments"], current["comment"]]
            return response(paginate(rows, query))
        if path.startswith("/contents/") and query.get("ref", [None])[0] == candidate_head:
            content_path = path[len("/contents/"):].rstrip("/")
            if content_path == folder:
                state["calls"].append((method, path))
                return response(deepcopy(listing))
            if content_path in documents:
                state["calls"].append((method, path))
                document = documents[content_path]
                raw = document["text"].encode("utf-8")
                return response({"type": "file", "path": content_path,
                    "name": content_path.rsplit("/", 1)[-1], "sha": document["sha"],
                    "size": len(raw), "encoding": "base64",
                    "content": base64.b64encode(raw).decode("ascii")})
        return old_http(method=method, url=url, token=token, body=body)

    provider.http = http
    return provider



def attach_structured_predecessor_controller_fixture(provider, root):
    """Actual public controller metadata and immutable source bytes for ordinal3 tests."""
    from copy import deepcopy
    import base64
    import hashlib
    import json
    import subprocess
    from urllib.parse import parse_qs, unquote, urlparse
    source = "ff2fcfebfceaef2baf4edc2a6de2ab820760d48b"
    controller = json.loads("{\"id\":38017902256,\"run_attempt\":1,\"workflow_id\":342691463,\"path\":\".github/workflows/v03-real-dogfood-scenario.yml\",\"event\":\"workflow_dispatch\",\"head_branch\":\"main\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"status\":\"completed\",\"conclusion\":\"failure\",\"display_title\":\"v0.3 real dogfood happy_path @ ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"updated_at\":\"2026-10-10T02:49:55Z\"}")
    controller_jobs = json.loads("{\"total_count\":2,\"jobs\":[{\"id\":114112184854,\"run_id\":38017902256,\"run_attempt\":1,\"name\":\"dogfood\",\"status\":\"completed\",\"conclusion\":\"failure\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"steps\":[{\"number\":1,\"name\":\"Set up job\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":2,\"name\":\"Validate trusted installation and provider configuration\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":3,\"name\":\"Create bounded Runtime App Feature Event token\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":4,\"name\":\"Run actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":5,\"name\":\"Run actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":6,\"name\":\"Run pip install -r requirements-dev.txt\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":7,\"name\":\"Prove exact trusted-main checkout before mutation\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":8,\"name\":\"Execute one frozen real dogfood scenario\",\"status\":\"completed\",\"conclusion\":\"failure\"},{\"number\":9,\"name\":\"Upload raw scenario observation\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":10,\"name\":\"Publish non-authoritative run receipt to Issue 239\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":11,\"name\":\"Dispatch closed post-run finalizer after successful raw observation\",\"status\":\"completed\",\"conclusion\":\"skipped\"},{\"number\":20,\"name\":\"Post Run actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97\",\"status\":\"completed\",\"conclusion\":\"skipped\"},{\"number\":21,\"name\":\"Post Run actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":22,\"name\":\"Post Create bounded Runtime App Feature Event token\",\"status\":\"completed\",\"conclusion\":\"success\"},{\"number\":23,\"name\":\"Complete job\",\"status\":\"completed\",\"conclusion\":\"success\"}]},{\"id\":114112185859,\"run_id\":38017902256,\"run_attempt\":1,\"name\":\"reject-non-main\",\"status\":\"completed\",\"conclusion\":\"skipped\",\"head_sha\":\"ff2fcfebfceaef2baf4edc2a6de2ab820760d48b\",\"steps\":[]}]}")
    source_pins = {
        ".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.md": "d4cedb1da8549861d86023348d306f8ab60bdef0",
        ".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml": "ff68923c95dbbf7d4bc206f2d2bb03fa81fe5578",
    }
    documents = {}
    for path, expected in source_pins.items():
        raw = subprocess.check_output(["git", "show", source + ":" + path], cwd=root)
        expect(hashlib.sha1(b"blob " + str(len(raw)).encode() + bytes([0]) + raw).hexdigest() == expected,
               "structured predecessor source Git bytes differ")
        documents[path] = {"path": path, "type": "file", "sha": expected, "size": len(raw),
                           "encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}
    state = provider.state
    state["reviewer_structured_controller_run"] = deepcopy(controller)
    state["reviewer_structured_controller_jobs"] = deepcopy(controller_jobs)
    state["reviewer_structured_source_documents"] = deepcopy(documents)
    old_http = provider.http
    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/dream-xin/ai-sdlc"
        if parsed.scheme == "https" and parsed.netloc == "api.github.com" and parsed.path.lower().startswith(prefix + "/"):
            path = unquote(parsed.path[len(prefix):])
            query = parse_qs(parsed.query)
            value = None
            if path == "/actions/runs/38017902256":
                value = state["reviewer_structured_controller_run"]
            elif path == "/actions/runs/38017902256/attempts/1/jobs":
                expect(query == {"per_page": ["100"]}, "controller fixture pagination differs")
                value = state["reviewer_structured_controller_jobs"]
            elif path.startswith("/contents/") and query == {"ref": [source]}:
                value = state["reviewer_structured_source_documents"].get(path[len("/contents/"):])
            if value is not None:
                expect(method == "GET" and body is None, "historical controller fixture attempted an effect")
                state["calls"].append((method, path))
                return 200, {}, json.dumps(deepcopy(value)).encode("utf-8")
        return old_http(method=method, url=url, token=token, body=body)
    provider.http = http
    return provider


def build_structured_dogfood_gate_fixture(preflight, *, read_ref, fallback_http,
                                          expected=(('reviewer', 'PASS'), ('qa', 'PASS'))):
    """Actual structured publication/source over finite fake Gate HTTP results.

    Gate payloads are derived only from the actual production dispatch POST.
    No role selection, launch facts, callbacks, validation or Persist is seeded.
    fallback_http supplies existing Developer/PR/source routes at the HTTP boundary.
    """
    import json
    import hashlib
    import io
    import zipfile
    from pathlib import Path
    import yaml
    from copy import deepcopy
    from types import SimpleNamespace
    from urllib.parse import unquote, urlparse
    from operator_vertical_gh_aw_actions_transport import (
        GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig)
    import os
    from validate_v03_gate_output_contract import verify_context_roundtrip
    from v03_dogfood_gate_output import validate_context

    repository = preflight.execution.repository
    workflows = preflight.workflows
    source_sha = preflight.execution.installation_commit_sha
    expect(all(role in {"reviewer", "qa"} for role, _ in expected),
           "structured fixture expected a non-Gate role")
    state = {"runs": [], "posts": [], "routes": {}, "inputs": [], "roundtrips": []}

    def respond(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()

    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/" + repository
        expect(parsed.path.startswith(prefix), "gate provider escaped repository")
        path = unquote(parsed.path[len(prefix):])
        if path.startswith("/actions/workflows/") and path.endswith("/runs"):
            workflow = path.split("/")[3]
            if workflow == workflows.developer_workflow:
                return fallback_http(method=method, url=url, token=token, body=body)
            rows = [row for row in state["runs"]
                    if row["path"] == ".github/workflows/" + workflow]
            rows.extend(deepcopy(row) for row in getattr(preflight, "historical_gate_runs", ())
                        if row["path"] == ".github/workflows/" + workflow)
            return respond({"total_count": len(rows), "workflow_runs": deepcopy(rows)})
        if method == "POST" and path == "/actions/workflows/" + workflows.developer_workflow + "/dispatches":
            return fallback_http(method=method, url=url, token=token, body=body)
        if method == "POST":
            expect(path.startswith("/actions/workflows/") and path.endswith("/dispatches"),
                   "gate provider received unexpected POST")
            submitted = json.loads(body)
            inputs = submitted["inputs"]
            role = inputs["role"]
            index = len(state["inputs"])
            expect(index < len(expected) and role == expected[index][0]
                   and inputs["dispatch_key"] not in [row["dispatch_key"] for row in state["inputs"]],
                   "structured continuation escaped expected roles or repeated a dispatch")
            verdict = expected[index][1]
            workflow = workflows.workflow_for(role)
            expect(path == "/actions/workflows/" + workflow + "/dispatches"
                   and submitted["ref"] == "main"
                   and inputs["candidate_head_sha"] == read_ref(),
                   "actual Gate launch did not bind post-Persist candidate head")
            task = json.loads(inputs["task_payload"])["task"]
            run_id = 47905505035 + len(state["runs"]) + 1
            comment_id, job_id = run_id + 100, run_id + 200
            run = {"id": run_id, "run_attempt": 1, "repository": {"full_name": repository},
                "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
                "path": ".github/workflows/" + workflow,
                "display_title": "AI-SDLC gh-aw " + inputs["dispatch_key"],
                "event": "workflow_dispatch", "head_branch": "main", "head_sha": source_sha,
                "status": "completed", "conclusion": "success"}
            state["runs"].append(run)
            state["posts"].append(deepcopy(submitted))
            state["inputs"].append(deepcopy(inputs))
            comment_url = f"https://github.com/{repository}/pull/{inputs['candidate_pr_number']}#issuecomment-{comment_id}"
            task_payload = json.loads(inputs["task_payload"])
            context = task_payload["feature_context"]["gate_context"]
            validate_context(context)
            publication = verify_context_roundtrip(
                role, context, root=Path(__file__).resolve().parents[1],
                actions_root=Path(os.environ["GH_AW_ACTIONS_ROOT"]),
                verdict=verdict, run_id=run_id, comment_id=comment_id,
                workflow_sha=source_sha)
            expect(publication["payload"]["task_id"] == task["id"]
                   and publication["payload"]["candidate_head_sha"] == read_ref(),
                   "actual structured publication escaped dispatched task/candidate")
            state["roundtrips"].append(publication)
            routes = state["routes"]
            routes[f"/actions/runs/{run_id}"] = run
            routes[f"/issues/comments/{comment_id}"] = {
                "id": comment_id, "html_url": comment_url,
                "issue_url": f"https://api.github.com/repos/{repository}/issues/{inputs['candidate_pr_number']}",
                "user": {"type": "Bot", "login": "github-actions[bot]", "id": 41898282},
                "created_at": preflight.composition.runtime.clock(),
                "updated_at": preflight.composition.runtime.clock(),
                "body": publication["published_body"]}
            lock = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows" / workflow).read_text())
            identity_steps = [step for step in lock["jobs"]["conclusion"]["steps"]
                if {"COMMENT_ID", "COMMENT_URL", "TRUSTED_TASK_ID", "SOURCE_RUN_ID", "SOURCE_WORKFLOW_REF"}
                <= set(step.get("env", {}))]
            expect(len(identity_steps) == 1,
                   "fake Gate cannot fabricate metadata absent from selected compiled source")
            expressions = {
                "${{ github.run_id }}": run_id,
                "${{ github.sha }}": source_sha,
                "${{ github.workflow_ref }}": f"{repository}/.github/workflows/{workflow}@refs/heads/main",
                "${{ needs.safe_outputs.outputs.comment_id }}": comment_id,
                "${{ needs.safe_outputs.outputs.comment_url }}": comment_url,
                "${{ fromJSON(inputs.task_payload).task.id }}": task["id"],
            }
            expressions.update({"${{ inputs." + key + " }}": value for key, value in inputs.items()})
            values = {name: expressions[expression] for name, expression in identity_steps[0]["env"].items()}
            jobs = []
            for index, (name, job) in enumerate(lock["jobs"].items()):
                identity = job_id if name == "conclusion" else job_id + index + 1
                jobs.append({"id": identity, "name": name, "conclusion": "success",
                    "status": "completed", "run_id": run_id, "run_attempt": 1, "head_sha": source_sha,
                    "steps": [{"name": step.get("name", step.get("id", "step")),
                               "status": "completed", "conclusion": "success"}
                              for step in job.get("steps", [])]})
            routes[f"/actions/runs/{run_id}/jobs"] = {"total_count": len(jobs), "jobs": jobs}
            routes[f"/actions/runs/{run_id}/attempts/1/jobs"] = {"total_count": len(jobs), "jobs": jobs}
            routes[f"/actions/jobs/{job_id}/logs"] = "".join(
                (f"2026-10-09T09:00:00Z   {name}: {value}" + chr(10)) for name, value in values.items()).encode()
            item = {
                "type": "add_comment", "provider": "github", "id": comment_id,
                "number": int(inputs["candidate_pr_number"]), "url": comment_url, "repo": repository,
                "target": {"provider": "github", "repository": repository,
                           "number": int(inputs["candidate_pr_number"])},
                "timestamp": preflight.composition.runtime.clock(),
            }
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr(zipfile.ZipInfo("safe-output-items.jsonl", (2026, 10, 9, 9, 0, 0)),
                                 json.dumps(item, sort_keys=True) + "\n")
            archive_bytes = stream.getvalue()
            artifact_id = run_id + 300
            artifact = {
                "id": artifact_id, "name": "safe-outputs-items", "expired": False,
                "size_in_bytes": len(archive_bytes), "digest": "sha256:" + hashlib.sha256(archive_bytes).hexdigest(),
                "archive_download_url": f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip",
                "workflow_run": {"id": run_id, "head_sha": source_sha, "head_branch": "main",
                                "repository_id": 1326302284, "head_repository_id": 1326302284},
            }
            routes[f"/actions/runs/{run_id}/artifacts"] = {"total_count": 1, "artifacts": [artifact]}
            routes[f"/actions/artifacts/{artifact_id}/zip"] = archive_bytes
            return 204, {}, b""
        expect(method == "GET", "gate HTTP provider allowed an unapproved effect")
        if path in state["routes"]:
            return respond(deepcopy(state["routes"][path]))
        return fallback_http(method=method, url=url, token=token)

    transport = GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=repository, token="fixture", workflows=workflows,
            launch_poll_attempts=2, launch_poll_seconds=0),
        http=http, sleeper=lambda _: None)
    from v03_dogfood_full_composition import DogfoodStructuredGateResultSource
    source = DogfoodStructuredGateResultSource(
        preflight.composition.recovery_result_source.config,
        target_repository=repository, http=http)
    source.bind_reviewer(preflight.composition.runtime, preflight.composition.policy_authority)
    return SimpleNamespace(transport=transport, result_source=source, state=state, http=http)

def reviewer_structured_runtime_fixture(*, verdict='PASS'):
    import base64
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_store_model import StoreSnapshot, operation_events
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_effect_rollout import ProtectedEffectLineageRolloutVerifier, EffectLineageWriteFence
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_callback import TrustedVerticalCallbackCoordinator
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector
    from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
    from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
    from v03_dogfood_fixture_pool import require_slot
    import v03_dogfood_full_composition as composition
    provider = reviewer_structured_frozen_provider_fixture(base64.b64decode(POST_HANDOFF_ARCHIVE_B64, validate=True))
    import subprocess
    import hashlib
    from pathlib import Path
    from urllib.parse import urlparse, parse_qs, unquote
    attach_structured_predecessor_controller_fixture(provider, Path(__file__).resolve().parents[1])
    old_provider_http = provider.http
    def candidate_document_http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/dream-xin/ai-sdlc/contents/"
        if method == "GET" and parsed.path.lower().startswith(prefix) and parse_qs(parsed.query).get("ref") == [provider.read_ref()]:
            path = unquote(parsed.path[len(prefix):])
            documents = provider.immutable_candidate_documents
            if path == "docs/features/" + "F-OPERATOR-V03-DOGFOOD-HAPPY-0001":
                return 200, {}, json.dumps([{"type":"file","path":name,"sha":row["sha"],
                    "size":len(row["text"].encode())} for name,row in documents.items()]).encode()
            if path in documents:
                row = documents[path]
                raw = row["text"].encode()
                return 200, {}, json.dumps({"type":"file","path":path,"sha":row["sha"],
                    "encoding":"base64","content":base64.b64encode(raw).decode()}).encode()
        return old_provider_http(method=method,url=url,token=token,body=body)
    provider.http = candidate_document_http
    files = provider.snapshot.files
    for name in ("effect-lineage-rollout.json", "writer-fence-receipt.json", "effect-resolution-policy.json", "decision-policy.json"):
        path = "config/operator/v03-vertical-policy/" + name
        files[path] = json.loads(subprocess.run(["git", "show", composition.REVIEWER_STRUCTURED_STORE + ":" + path],
            cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True).stdout)
    policy_path = "config/operator/v03-vertical-policy/"
    rollout_verifier = ProtectedEffectLineageRolloutVerifier(
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-lineage-rollout.json"]),
        writer_fence_receipt_loader=lambda *_: deepcopy(files[policy_path + "writer-fence-receipt.json"]))
    rollout = rollout_verifier.verify(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE)
    resolution = ProtectedEffectResolutionPolicyVerifier(repository="dream-xin/ai-sdlc",
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "effect-resolution-policy.json"]),
        evidence_fact_loader=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected resolution evidence")))
    resolution.verify_current()
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(repository="dream-xin/ai-sdlc", state_ref="refs/heads/ai-sdlc-operator-state",
                             snapshot=deepcopy(provider.snapshot))
            self.commit_count = 0
            self.fail_confirmation_once = False
        def commit(self, plan, receipt):
            if self.fail_confirmation_once and any(isinstance(m.value, dict)
                    and m.value.get("event_type") == "persist.confirmed" for m in plan.mutations):
                self.fail_confirmation_once = False
                raise OSError("fixture crash before protected Persist confirmation")
            result = super().commit(plan, receipt)
            self.commit_count += 1
            self.snapshot = StoreSnapshot(f"{self.commit_count:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    runtime = OperatorStoreRuntime(backend=Backend(), protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout), clock=lambda: "2026-10-09T09:10:00Z")
    from v03_dogfood_live_gate import resolve_current_dogfood_bindings
    from v03_dogfood_runtime_preflight import _workflow_map, _execution_bindings
    gate = SimpleNamespace(scenario="happy_path", bindings=resolve_current_dogfood_bindings({"DEEPSEEK_API_KEY": True}, scenario="happy_path"))
    workflows = _workflow_map(gate)
    policy = recovery_policy_fixture()
    provider.state["controller_source"] = policy.installation_commit_sha
    source = composition.RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(control_repository="dream-xin/ai-sdlc",
            control_token="fixture", target_token="fixture", workflows=workflows,
            collector_identity=composition.COLLECTOR_IDENTITY),
        target_repository="dream-xin/ai-sdlc", http=provider.http)
    pf = SimpleNamespace(slot=require_slot("happy_path"), workflows=workflows,
        candidate_pr_number=552, candidate_head_sha=composition.REVIEWER_CANDIDATE,
        execution=SimpleNamespace(repository="dream-xin/ai-sdlc", installation_commit_sha=policy.installation_commit_sha),
        trusted_context_digest="6" * 64,
        composition=SimpleNamespace(runtime=runtime, policy_authority=policy, recovery_result_source=source))
    def get_json(url, headers):
        status, _, raw = provider.http(method="GET", url=url, token="fixture")
        return status, json.loads(raw)
    candidate = composition.DogfoodGitHubCandidateProvider(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", http_get=get_json)
    candidate.bind_runtime(runtime)
    feature = build_reviewer_frozen_feature_fixture(pf, candidate, provider)
    candidate.persist_gateway = feature.persist_gateway
    pf.historical_gate_runs = [provider.state["reviewer_observed"]["run"], provider.state["observed"]["run"],
        provider.state["reviewer_post_model_observed"]["run"], provider.original_structured_failure_run]
    gates = build_structured_dogfood_gate_fixture(pf, read_ref=provider.read_ref, fallback_http=provider.http,
        expected=(("reviewer", verdict), ("qa", "PASS")) if verdict == "PASS" else (("reviewer", verdict),))
    bindings = _execution_bindings(gate, workflows)
    dispatch = composition.DogfoodExecutionBoundDispatchGateway(
        delegate=__import__("operator_vertical_gh_aw").GhAwVerticalRoleDispatchGateway(
            transport=gates.transport, workflows=workflows), execution_bindings=bindings)
    one_shot = StoreBackedOneShotExternalCreateGateway(runtime=runtime, delegate=dispatch,
        trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True)
    loader = composition.DogfoodRecoveryBoundContentLoader(
        result_source=gates.result_source, recovery_result_source=source, policy_authority=policy)
    loader.bind_runtime(runtime)
    source.bind_post_handoff(runtime, policy)
    builder = composition.DogfoodStructuredGateContextBuilder(runtime=runtime,
        feature_gateway=feature.feature_gateway, persist_gateway=feature.persist_gateway,
        content_loader=loader, candidate_provider=candidate, policy_authority=policy)
    dispatch.delegate = composition.DogfoodCurrentStructuredDispatchGateway(
        transport=gates.transport, workflows=workflows, context_builder=builder)
    base = TrustedVerticalExecutor(runtime=runtime, feature_gateway=feature.feature_gateway,
        persist_gateway=feature.persist_gateway, dispatch_gateway=one_shot,
        config=TrustedVerticalExecutorConfig(target_ref=pf.slot.target_ref,
            trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True,
            old_writers_quiesced=True, rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest, max_auto_steps=64),
        resolution_policy_verifier=resolution)
    from pathlib import Path
    from operator_production_runtime import TrustedOperatorRuntimeConfig, TrustedFeatureBinding
    from operator_decision_policy import ProtectedDecisionPolicyVerifier
    from validate_v03_dogfood_runtime_composition import assemble_post_handoff_responses_graph, assert_post_handoff_authority_graph
    config = TrustedOperatorRuntimeConfig(target_repository=pf.execution.repository,
        store_repository=pf.execution.repository, installation_ref="main", store_checkout=Path("."),
        principal="post-handoff-fixture",
        feature_bindings=(TrustedFeatureBinding(pf.slot.feature_id, pf.slot.target_ref),))
    decision = ProtectedDecisionPolicyVerifier(repository=config.store_repository, state_ref=config.state_ref,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[policy_path + "decision-policy.json"]))
    def reader_get(url, headers):
        if "/contents/state/features/" in url:
            return feature.http("GET", url.replace("https://api.github.com", "https://api.github.test"), headers, None)
        return get_json(url, headers)
    responses, graph_before = assemble_post_handoff_responses_graph(
        runtime=runtime, base_executor=base, content_loader=loader, slot=pf.slot, config=config,
        policy_authority=policy, decision_policy_verifier=decision,
        trusted_role_policy="fixture-independent-role-policy", collector_namespace_policy="fixture-collector-namespace",
        reader_http_get=reader_get)
    executor = responses.operator_bundle.executor
    delegate = responses.operator_bundle.callback_coordinator
    predecessor_events = deepcopy(operation_events(runtime.backend.read_snapshot(), composition.RECOVERY_OPERATION_ID))
    assert_post_handoff_authority_graph(graph_before, responses, policy, predecessor_events=predecessor_events[:15])
    def forbidden_handoff_http(*args, **kwargs):
        raise AssertionError("post-handoff reconciliation attempted another fixture PATCH")
    handoff = composition.DogfoodCandidateHandoff(slot=pf.slot, repository=pf.execution.repository,
        token="fixture", candidate_provider=candidate, http_request=forbidden_handoff_http)
    handoff.content_loader = loader
    coordinator = composition.DogfoodTrustedCallbackCoordinator(delegate=delegate, candidate_handoff=handoff)
    collector = composition.DogfoodReviewerReplacementCollector(policy_authority=policy,callback_coordinator=coordinator,
        result_source=gates.result_source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock)
    recovery_collector = composition.DogfoodRecoveryCollector(callback_coordinator=coordinator,
        result_source=source, workflows=workflows, control_repository=pf.execution.repository,
        clock=runtime.clock, policy_authority=policy)
    pf.composition.__dict__.update(candidate_provider=candidate, feature_event_gateway=feature.event_gateway,
        result_source=gates.result_source, collector=collector, recovery_collector=recovery_collector,
        actions_transport=gates.transport, dispatch_gateway=dispatch, bundle=responses.operator_bundle,
        responses=responses, graph_before=graph_before, predecessor_events=predecessor_events,
        callback_coordinator=coordinator)
    return pf, provider, feature, gates, coordinator


def reviewer_structured_admission_tests():
    import json
    from copy import deepcopy
    from operator_store_git import CasConflict
    from operator_store_model import canonical_json, operation_events
    from operator_vertical import VerticalInvariantError
    from operator_store import StoreCommandError
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    errors=(VerticalInvariantError,StoreCommandError,d.V03DogfoodRuntimeDriverError,
            d.V03DogfoodScenarioRunnerError,ValueError)
    def reject(pf,provider,feature,gates,label):
        runtime=pf.composition.runtime
        before=(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,
                len(gates.state["posts"]),feature.state["puts"],provider.effect_counts())
        try: d.recover_reviewer_structured(pf)
        except errors: pass
        else: raise AssertionError("corrected Reviewer accepted "+label)
        expect(before==(canonical_json(runtime.backend.snapshot.files),runtime.backend.commit_count,
                len(gates.state["posts"]),feature.state["puts"],provider.effect_counts()),
               "corrected Reviewer rejected after effects: "+label)
    for path in c.REVIEWER_STRUCTURED_PATHS:
        for value in (None,{},[]):
            pf,p,feature,gates,_=reviewer_structured_runtime_fixture()
            pf.composition.runtime.backend.snapshot.files[path]=value
            reject(pf,p,feature,gates,"partial/null sidecar")
    for mutate in (
        lambda p:p.state["reviewer_structured_observed"]["run"].update(run_attempt=2),
        lambda p:p.state["reviewer_structured_observed"]["comment"].update(body="changed"),
        lambda p:p.state["reviewer_structured_observed"]["comment"].update(updated_at="2099-01-01T00:00:00Z"),
        lambda p:p.state.update(head="9"*40),
        lambda p:p.state.update(controller_source="9"*40)):
        pf,p,feature,gates,_=reviewer_structured_runtime_fixture()
        mutate(p);reject(pf,p,feature,gates,"historical identity/content/candidate/source drift")
    pf,p,feature,gates,_=reviewer_structured_runtime_fixture()
    runtime=pf.composition.runtime
    original=deepcopy(runtime.backend.read_snapshot())
    binding=c.recovery_execution_binding(pf.composition.policy_authority)
    proof=d._observe_reviewer_structured_predecessor(pf)
    auth=c.reviewer_structured_authorization(original,consumer_binding=binding,predecessor_proof=proof)
    gateway=pf.composition.dispatch_gateway.delegate
    context=gateway.context_builder.build_prospective_reviewer(auth)
    inputs=__import__("operator_vertical_gh_aw").GhAwVerticalRoleDispatchGateway._inputs(gateway,c.reviewer_dispatch(auth))
    payload=json.loads(inputs["task_payload"]);payload["feature_context"]["gate_context"]=context
    inputs["task_payload"]=canonical_json(payload)
    def plan(snapshot):
        return c.plan_reviewer_structured_replacement(snapshot,consumer_binding=binding,
            predecessor_proof=proof,dispatch_inputs=inputs)
    first,second=plan(original),plan(original)
    runtime.backend.commit(first,runtime.protected_receipt())
    try: runtime.backend.commit(second,runtime.protected_receipt())
    except CasConflict: pass
    else: raise AssertionError("two corrected Reviewer claim winners")
    expect(plan(runtime.backend.read_snapshot()).result["acquired"] is False,"CAS loser acquired another Reviewer")
    reject(pf,p,feature,gates,"consumed claim without run")
    expect(operation_events(runtime.backend.snapshot,c.RECOVERY_OPERATION_ID)==p.frozen_events,
           "claim changed original journal")

    pf,p,feature,gates,_=reviewer_structured_runtime_fixture()
    pf.composition.runtime.backend.inject_conflict_once()
    result=d.recover_reviewer_structured(pf)
    snap=pf.composition.runtime.backend.read_snapshot()
    auth,claim=c.validate_reviewer_structured_authorization(snap)
    expect(len(gates.state["posts"])==1 and result["sealed"]["recommendation"]=="PASS"
           and gates.state["inputs"][0]==claim["dispatch_inputs"]
           and claim["preclaim_store_commit"]!=snap.ref_sha,
           "corrected Reviewer did not POST exact frozen preclaim bytes once")
    before=(pf.composition.runtime.backend.commit_count,len(gates.state["posts"]),feature.state["puts"])
    d.recover_reviewer_structured(pf)
    expect(before==(pf.composition.runtime.backend.commit_count,len(gates.state["posts"]),feature.state["puts"]),
           "corrected Reviewer replay wrote or relaunched")
    for bad in (True,"1",2):
        snap.files[c.REVIEWER_STRUCTURED_SEAL_PATH]["run_attempt"]=bad
        try:c.reviewer_replacement_route(snap)
        except VerticalInvariantError:pass
        else:raise AssertionError("corrected seal accepted malformed attempt")
        snap.files[c.REVIEWER_STRUCTURED_SEAL_PATH]["run_attempt"]=1

    for invalid in (True, "1", 2):
        gates.state["runs"][0]["run_attempt"] = invalid
        reject(pf,p,feature,gates,"new malformed/repeated attempt")
    gates.state["runs"][0]["run_attempt"] = 1
    gates.state["runs"][0]["conclusion"] = "failure"
    reject(pf,p,feature,gates,"new run lost terminal success")
    pf,p,feature,gates,_=reviewer_structured_runtime_fixture()
    original_http=gates.transport.http
    lost={"once":True}
    def lost_ack(**kwargs):
        reply=original_http(**kwargs)
        if kwargs["method"]=="POST" and lost["once"]:
            lost["once"]=False
            raise OSError("fixture lost corrected Reviewer acknowledgement")
        return reply
    gates.transport.http=lost_ack
    d.recover_reviewer_structured(pf)
    expect(len(gates.state["posts"])==1,"corrected Reviewer lost acknowledgement retried POST")
    print("- fixed corrected Reviewer CAS, frozen payload and lookup-only replay passed")

def reviewer_structured_terminal_tests():
    import json
    from copy import deepcopy
    from operator_store_model import canonical_json, operation_events
    from operator_vertical import VerticalInvariantError
    from operator_vertical_store import vertical_projection
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    import v03_dogfood_scenario_runner as runner
    for verdict in ("REWORK","BLOCKED"):
        pf,p,feature,gates,_=reviewer_structured_runtime_fixture(verdict=verdict)
        runtime=pf.composition.runtime
        historical=bytes(p.original_structured_failure_body_bytes)
        effects=deepcopy(p.effect_counts())
        result=d.recover_reviewer_structured(pf)
        expect(result["terminal"]["outcome"]==verdict and len(gates.state["posts"])==1,
               "non-PASS recommendation was changed or relaunched")
        rows=operation_events(runtime.backend.snapshot,c.RECOVERY_OPERATION_ID)
        expect(rows[:30]==p.frozen_events and len(rows)==31 and rows[-1]["event_type"]=="operation.needs-user"
               and vertical_projection(runtime.backend.snapshot,c.RECOVERY_OPERATION_ID)["status"]=="NEEDS_USER"
               and feature.state["puts"]==0 and p.effect_counts()==effects,
               "non-PASS created callback/Persist/Developer/QA effects")
        expect(p.state["reviewer_structured_observed"]["comment"]["body"].encode()==historical,
               "original REWORK was rewritten")
        before=(canonical_json(runtime.backend.snapshot.files),len(gates.state["posts"]))
        d.recover_reviewer_structured(pf)
        expect(before==(canonical_json(runtime.backend.snapshot.files),len(gates.state["posts"])),
               "non-PASS replay changed terminal history")
        try:pf.composition.collector.handle(operation_id=c.RECOVERY_OPERATION_ID,external_dispatch_key=c.REVIEWER_OLD_KEY)
        except VerticalInvariantError:pass
        else:raise AssertionError("non-PASS entered ordinary remediation callback")
        for role in ("developer","qa"):
            auth,_=c.validate_reviewer_structured_authorization(runtime.backend.snapshot)
            dispatch=c.reviewer_dispatch(auth);dispatch["role"]=role
            try:pf.composition.dispatch_gateway.launch(dispatch=dispatch)
            except VerticalInvariantError:pass
            else:raise AssertionError("terminal recommendation allowed "+role)
        expect(feature.state["puts"]==0 and len(gates.state["posts"])==1 and p.effect_counts()==effects,
               "terminal negative reached a follow-on effect")
    print("- authentic corrected REWORK/BLOCKED stop atomically without lifecycle acceptance")

def reviewer_structured_full_pipeline_tests():
    from copy import deepcopy
    from operator_store_model import operation_events, canonical_json
    import v03_dogfood_full_composition as c
    import v03_dogfood_runtime_driver as d
    pf,p,feature,gates,_=reviewer_structured_runtime_fixture()
    original=deepcopy(pf.composition.runtime.backend.snapshot.files)
    old_body=bytes(p.original_structured_failure_body_bytes)
    d.recover_reviewer_structured(pf)
    record=finish_reviewer_replacement_pipeline_tests(pf,gate_fixture=gates,feature_fixture=feature,
        read_ref=p.read_ref,effect_counts=p.effect_counts,adapter=pf.composition.responses.adapter)
    expect(record["verdict"]=="PASS" and len(gates.state["roundtrips"])==2
           and [r["role"] for r in gates.state["inputs"]]==["reviewer","qa"],
           "actual structured PASS did not reach ordinary QA/finalizer")
    expect("https://github.com/dream-xin/ai-sdlc/pull/552#issuecomment-6092979158"
           in {uri.lower() for uri in record["evidence_uris"]},"finalizer omitted original REWORK")
    snapshot=pf.composition.runtime.backend.read_snapshot()
    for path,raw in p.historical_reviewer_sidecar_raw_files.items():
        expect((canonical_json(snapshot.get(path))+"\n").encode()==raw,"corrected route rewrote old sidecar")
    expect(operation_events(snapshot,c.RECOVERY_OPERATION_ID)[:30]==p.frozen_events
           and p.state["reviewer_structured_observed"]["comment"]["body"].encode()==old_body,
           "corrected route rewrote original history/REWORK")
    print("- actual corrected structured Reviewer/Persist/QA/Notification/finalizer passed")



def build_ordinary_dogfood_provider(preflight):
    """Fake GitHub only: immutable run outputs, real ref ancestry and auto-close."""
    import base64
    import hashlib
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from urllib.parse import unquote, urlparse, parse_qs
    from v03_dogfood_fixture_pool import task_text
    slot = preflight.slot
    repository = preflight.execution.repository
    initial_head = preflight.candidate_head_sha
    candidate_number = preflight.candidate_pr_number
    source_sha = preflight.execution.installation_commit_sha
    state = {"head": initial_head, "parents": {}, "changes": {}, "routes": {},
             "runs": [], "inputs": [], "prs": {}, "patches": 0,
             "documents": {initial_head: {slot.task_path: task_text(slot).encode()}}}
    def read_ref():
        return state["head"]
    def advance_ref(sha, *, change):
        previous = read_ref()
        state["parents"][sha] = previous
        state["changes"][sha] = deepcopy(change)
        state["documents"][sha] = deepcopy(state["documents"][previous])
        state["head"] = sha
    def blob(raw):
        return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
    def document(path, raw):
        return {"type": "file", "path": path, "name": path.rsplit("/", 1)[-1],
                "encoding": "base64", "content": base64.b64encode(raw).decode(),
                "sha": blob(raw), "size": len(raw)}
    def candidate():
        return {"number": candidate_number, "state": "open", "draft": False,
                "html_url": f"https://github.com/{repository}/pull/{candidate_number}",
                "head": {"ref": slot.target_ref, "sha": read_ref(),
                         "repo": {"full_name": repository}},
                "base": {"ref": "main", "repo": {"full_name": repository}}}
    def response(value):
        return 200, {}, value if isinstance(value, bytes) else json.dumps(value).encode()
    def http(*, method, url, token, body=None):
        parsed = urlparse(url)
        prefix = "/repos/" + repository
        expect(parsed.path.startswith(prefix), "ordinary provider escaped repository")
        path = unquote(parsed.path[len(prefix):])
        workflow = preflight.workflows.developer_workflow
        if method == "GET" and path == "/actions/workflows/" + workflow + "/runs":
            return response({"total_count": len(state["runs"]), "workflow_runs": deepcopy(state["runs"])})
        if method == "POST":
            expect(path == "/actions/workflows/" + workflow + "/dispatches",
                   "ordinary provider received a non-Developer POST")
            submitted = json.loads(body)
            inputs = submitted["inputs"]
            index = len(state["inputs"])
            limit = 2 if slot.scenario == "review_remediation" else 1
            expect(index < limit and inputs["role"] == "developer"
                   and submitted["ref"] == "main" and inputs["feature_id"] == slot.feature_id
                   and inputs["target_ref"] == slot.target_ref
                   and json.loads(inputs["task_payload"])["feature_context"]["vertical"]["candidate_head_sha"] == read_ref()
                   and inputs["dispatch_key"] not in [row["dispatch_key"] for row in state["inputs"]],
                   "ordinary Developer exceeded scenario identity/budget")
            task = json.loads(inputs["task_payload"])["task"]
            if index:
                expect(task["kind"] == "remediation", "second Developer lacks real remediation task")
            state["inputs"].append(deepcopy(inputs))
            run_id = 67905505035 + index
            pr_number, job_id = 2901 + index, run_id + 10
            old = read_ref()
            head = hashlib.sha1((slot.scenario + ":" + inputs["dispatch_key"]).encode()).hexdigest()
            marker = ("dogfood_review_state: initial-needs-remediation" if index == 0
                      else "dogfood_review_state: remediated") if slot.scenario == "review_remediation" else (
                      "dogfood_session_choice: PENDING_USER")
            candidate_path = f"docs/features/{slot.feature_id}/implementation.md"
            raw = ("# Synthetic candidate\n\n" + marker + "\n").encode()
            state["parents"][head] = old
            state["changes"][head] = {"filename": candidate_path, "sha": blob(raw),
                                      "status": "added" if index == 0 else "modified"}
            state["documents"][head] = {**deepcopy(state["documents"][old]), candidate_path: raw}
            run = {"id": run_id, "run_attempt": 1, "repository": {"full_name": repository},
                   "html_url": f"https://github.com/{repository}/actions/runs/{run_id}",
                   "path": ".github/workflows/" + workflow,
                   "display_title": "AI-SDLC gh-aw " + inputs["dispatch_key"],
                   "event": "workflow_dispatch", "head_branch": "main", "head_sha": source_sha,
                   "status": "completed", "conclusion": "success"}
            pr = {"number": pr_number, "id": 8900 + pr_number, "node_id": "PR_ordinary_" + str(pr_number),
                  "html_url": f"https://github.com/{repository}/pull/{pr_number}", "state": "open",
                  "draft": True, "merged": False, "title": "[ai-sdlc gh-aw] ordinary candidate",
                  "body": "Immutable synthetic implementation output.",
                  "user": {"login": "github-actions[bot]", "type": "Bot"},
                  "head": {"ref": f"gh-aw/{slot.feature_id}-{run_id}-v{inputs['expected_revision']}-fixture",
                           "sha": head, "repo": {"full_name": repository}},
                  "base": {"ref": slot.target_ref, "sha": old, "repo": {"full_name": repository}}}
            state["runs"].append(run)
            state["prs"][pr_number] = pr
            listing, archive = recovery_safe_output_artifact_fixture(run_id=run_id, source_head=source_sha, pr=pr)
            routes = state["routes"]
            routes[f"/actions/runs/{run_id}"] = run
            routes[f"/actions/runs/{run_id}/artifacts"] = listing
            routes[f"/actions/artifacts/{listing['artifacts'][0]['id']}/zip"] = archive
            routes[f"/actions/runs/{run_id}/jobs"] = {"jobs": [
                {"id": job_id - 1, "name": "safe_outputs", "conclusion": "success"},
                {"id": job_id, "name": "conclusion", "conclusion": "success"}]}
            values = {"RUN_URL": run["html_url"], "TARGET_REPOSITORY": repository,
                      "TARGET_REF": slot.target_ref, "FEATURE_ID": slot.feature_id,
                      "EXPECTED_REVISION": inputs["expected_revision"], "STAGE": inputs["stage"],
                      "TASK_PAYLOAD": inputs["task_payload"], "PR_URL": pr["html_url"]}
            routes[f"/actions/jobs/{job_id}/logs"] = "".join(
                f"2026-10-10T06:00:00Z   {key}: {value}\n" for key, value in values.items()).encode()
            return 204, {}, b""
        expect(method == "GET", "ordinary provider received an unmodeled mutation")
        if path == "/pulls":
            return response([candidate()])
        if path == f"/pulls/{candidate_number}":
            return response(candidate())
        if path.startswith("/pulls/") and path[len("/pulls/"):].isdigit():
            number = int(path[len("/pulls/"):])
            expect(number in state["prs"], "ordinary provider requested unknown source PR")
            return response(deepcopy(state["prs"][number]))
        if path.startswith("/git/refs/heads/") or path.startswith("/git/ref/heads/"):
            expect(path.rsplit("/heads/", 1)[1] == slot.target_ref, "ordinary provider requested foreign ref")
            return response({"object": {"sha": read_ref()}})
        if path.startswith("/contents/"):
            head = parse_qs(parsed.query).get("ref", [None])[0]
            expect(head in state["documents"], "ordinary context requested unknown Git head")
            wanted = path[len("/contents/"):]
            files = state["documents"][head]
            if wanted in files:
                return response(document(wanted, files[wanted]))
            directory = f"docs/features/{slot.feature_id}"
            expect(wanted == directory, "ordinary context requested unknown document")
            return response([document(name, raw) for name, raw in sorted(files.items())
                             if name.startswith(directory + "/")])
        if path.startswith("/compare/"):
            ancestor, descendant = path[len("/compare/"):].split("...", 1)
            cursor, chain = descendant, []
            while cursor != ancestor and cursor in state["parents"] and cursor not in chain:
                chain.append(cursor)
                cursor = state["parents"][cursor]
            expect(cursor == ancestor, "ordinary provider requested unrelated ancestry")
            files = {}
            for sha in reversed(chain):
                if sha in state["changes"]:
                    changed = state["changes"][sha]
                    files[changed["filename"]] = deepcopy(changed)
            return response({"status": "ahead" if chain else "identical", "ahead_by": len(chain),
                "behind_by": 0, "merge_base_commit": {"sha": ancestor}, "total_commits": len(chain),
                "commits": [{"sha": sha, "parents": [{"sha": state["parents"][sha]}]}
                            for sha in reversed(chain)], "files": list(files.values())})
        if path in state["routes"]:
            return response(deepcopy(state["routes"][path]))
        raise AssertionError("ordinary provider requested unmodeled route: " + path)
    def handoff_http(method, url, headers, body):
        if method != "PATCH":
            status, _, raw = http(method=method, url=url, token="fixture")
            return status, json.loads(raw)
        parsed = urlparse(url)
        expected = "/repos/" + repository + "/git/refs/heads/" + slot.target_ref
        expect(unquote(parsed.path) == expected and body.get("force") is False,
               "ordinary handoff escaped fixture ref")
        matches = [pr for pr in state["prs"].values()
                   if pr["head"]["sha"] == body.get("sha") and pr["state"] == "open"]
        expect(len(matches) == 1 and matches[0]["base"]["sha"] == read_ref(),
               "ordinary handoff repeated or changed an authorized fast-forward")
        pr = matches[0]
        state["head"] = pr["head"]["sha"]
        state["patches"] += 1
        pr.update(state="closed", merged=True, merge_commit_sha=read_ref(),
                  merged_at=preflight.composition.runtime.clock(),
                  closed_at=preflight.composition.runtime.clock(),
                  merged_by={"login": "dream-xin-ai-sdlc-runtime-operator[bot]", "id": 316394104, "type": "Bot"})
        return 200, {"object": {"sha": read_ref()}}
    return SimpleNamespace(state=state, http=http, read_ref=read_ref, advance_ref=advance_ref,
                           handoff_http=handoff_http)



def ordinary_structured_runtime_fixture(template, scenario):
    """Assemble canonical classes over fresh Store and fake provider state."""
    import json
    from copy import deepcopy
    from pathlib import Path
    from types import SimpleNamespace
    from operator_store_model import StoreSnapshot
    from operator_store_git import MemoryStateRefBackend, CommitResult
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_effect_rollout import ProtectedEffectLineageRolloutVerifier, EffectLineageWriteFence
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    from operator_vertical import VERTICAL_PROFILE
    from operator_vertical_executor import TrustedVerticalExecutor, TrustedVerticalExecutorConfig
    from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector
    from operator_vertical_gh_aw_actions_transport import GitHubActionsWorkflowTransportConfig
    from operator_external_create_gateway import StoreBackedOneShotExternalCreateGateway
    from operator_production_runtime import TrustedOperatorRuntimeConfig, TrustedFeatureBinding
    from operator_decision_policy import ProtectedDecisionPolicyVerifier
    from v03_dogfood_fixture_pool import require_slot
    from v03_dogfood_live_gate import resolve_current_dogfood_bindings
    from v03_dogfood_runtime_preflight import _workflow_map, _execution_bindings
    from validate_v03_dogfood_runtime_composition import assemble_post_handoff_responses_graph
    import v03_dogfood_full_composition as composition
    expect(scenario in {"review_remediation", "session_recovery"}, "ordinary fixture escaped frozen scenarios")
    slot = require_slot(scenario)
    repository = template.execution.repository
    prefix = "config/operator/v03-vertical-policy/"
    files = {path: deepcopy(value) for path, value in
             template.composition.runtime.backend.read_snapshot().files.items() if path.startswith(prefix)}
    expect(set(files) >= {prefix + name for name in (
        "effect-lineage-rollout.json", "writer-fence-receipt.json",
        "effect-resolution-policy.json", "decision-policy.json")},
        "ordinary fixture lacks original protected policies")
    rollout_verifier = ProtectedEffectLineageRolloutVerifier(
        policy_loader=lambda *_: deepcopy(files[prefix + "effect-lineage-rollout.json"]),
        writer_fence_receipt_loader=lambda *_: deepcopy(files[prefix + "writer-fence-receipt.json"]))
    rollout = rollout_verifier.verify(repository=repository,
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE)
    resolution = ProtectedEffectResolutionPolicyVerifier(repository=repository,
        state_ref="refs/heads/ai-sdlc-operator-state", operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[prefix + "effect-resolution-policy.json"]),
        evidence_fact_loader=lambda *_: (_ for _ in ()).throw(AssertionError("unexpected resolution evidence")))
    resolution.verify_current()
    class Backend(MemoryStateRefBackend):
        def __init__(self):
            super().__init__(repository=repository, state_ref="refs/heads/ai-sdlc-operator-state",
                             snapshot=StoreSnapshot("1" * 40, deepcopy(files)))
            self.commit_count = 0
        def commit(self, plan, receipt):
            result = super().commit(plan, receipt)
            self.commit_count += 1
            self.snapshot = StoreSnapshot(f"{self.commit_count + 100:040x}", result.snapshot.files)
            return CommitResult(self.snapshot.ref_sha, self.read_snapshot(), result.result)
    runtime = OperatorStoreRuntime(backend=Backend(), protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        plan_guard=EffectLineageWriteFence(rollout), clock=lambda: "2026-10-10T06:00:00Z")
    gate = SimpleNamespace(scenario=scenario,
        bindings=resolve_current_dogfood_bindings({"DEEPSEEK_API_KEY": True}, scenario=scenario))
    workflows = _workflow_map(gate)
    bindings = _execution_bindings(gate, workflows)
    policy = template.composition.policy_authority
    pf = SimpleNamespace(slot=slot, workflows=workflows,
        candidate_pr_number=1950 if scenario == "review_remediation" else 1951,
        candidate_head_sha="c" * 40,
        execution=SimpleNamespace(repository=repository, installation_commit_sha=policy.installation_commit_sha),
        trusted_context_digest="6" * 64,
        composition=SimpleNamespace(runtime=runtime, policy_authority=policy))
    external = build_ordinary_dogfood_provider(pf)
    recovery_source = composition.RecoverySafeOutputGhAwResultSource(
        GitHubActionsGhAwResultSourceConfig(control_repository=repository,
            control_token="fixture", target_token="fixture", workflows=workflows,
            collector_identity=composition.COLLECTOR_IDENTITY),
        target_repository=repository, http=external.http)
    pf.composition.recovery_result_source = recovery_source
    expected = (("reviewer", "REWORK"), ("reviewer", "PASS"), ("qa", "PASS")) if (
        scenario == "review_remediation") else ()
    gates = build_structured_dogfood_gate_fixture(pf, read_ref=external.read_ref,
        fallback_http=external.http, expected=expected)
    def get_json(url, headers):
        status, _, raw = gates.http(method="GET", url=url, token="fixture")
        return status, json.loads(raw)
    candidate = composition.DogfoodGitHubCandidateProvider(slot=slot, repository=repository,
        token="fixture", http_get=get_json)
    candidate.bind_runtime(runtime)
    feature = build_post_handoff_feature_fixture(pf, candidate,
        read_ref=external.read_ref, advance_ref=external.advance_ref, slot=slot)
    candidate.persist_gateway = feature.persist_gateway
    gates.result_source.bind_handoff(runtime, feature.persist_gateway)
    recovery_source.bind_post_handoff(runtime, policy)
    loader = composition.DogfoodRecoveryBoundContentLoader(
        result_source=gates.result_source, recovery_result_source=recovery_source,
        policy_authority=policy)
    loader.bind_runtime(runtime)
    builder = composition.DogfoodStructuredGateContextBuilder(
        runtime=runtime, feature_gateway=feature.feature_gateway,
        persist_gateway=feature.persist_gateway, content_loader=loader,
        candidate_provider=candidate, policy_authority=policy)
    gates.result_source.structured_context_builder = builder
    transport = composition.DogfoodCandidateBoundActionsTransport(
        GitHubActionsWorkflowTransportConfig(control_repository=repository, token="fixture",
            workflows=workflows, launch_poll_attempts=2, launch_poll_seconds=0),
        candidate_provider=candidate, http=gates.http, sleeper=lambda _: None)
    gateway = composition.DogfoodCurrentStructuredDispatchGateway(
        transport=transport, workflows=workflows, context_builder=builder)
    dispatch = composition.DogfoodExecutionBoundDispatchGateway(
        delegate=gateway, execution_bindings=bindings)
    one_shot = StoreBackedOneShotExternalCreateGateway(runtime=runtime, delegate=dispatch,
        trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True)
    base = TrustedVerticalExecutor(runtime=runtime, feature_gateway=feature.feature_gateway,
        persist_gateway=feature.persist_gateway, dispatch_gateway=one_shot,
        config=TrustedVerticalExecutorConfig(target_ref=slot.target_ref,
            trusted_context_digest=pf.trusted_context_digest, effect_lineage_required=True,
            old_writers_quiesced=True, rollout_policy_digest=rollout.policy_digest,
            writer_fence_receipt_digest=rollout.writer_fence_receipt_digest, max_auto_steps=64),
        resolution_policy_verifier=resolution)
    config = TrustedOperatorRuntimeConfig(target_repository=repository,
        store_repository=repository, installation_ref="main", store_checkout=Path("."),
        principal="ordinary-structured-fixture",
        feature_bindings=(TrustedFeatureBinding(slot.feature_id, slot.target_ref),))
    decision = ProtectedDecisionPolicyVerifier(repository=repository, state_ref=config.state_ref,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=lambda *_: deepcopy(files[prefix + "decision-policy.json"]))
    def reader_get(url, headers):
        if "/contents/state/features/" in url:
            return feature.http("GET", url.replace("https://api.github.com", "https://api.github.test"), headers, None)
        return get_json(url, headers)
    responses, graph_before = assemble_post_handoff_responses_graph(
        runtime=runtime, base_executor=base, content_loader=loader, slot=slot, config=config,
        policy_authority=policy, decision_policy_verifier=decision,
        trusted_role_policy="fixture-independent-role-policy", collector_namespace_policy="fixture-collector-namespace",
        reader_http_get=reader_get)
    handoff = composition.DogfoodCandidateHandoff(slot=slot, repository=repository,
        token="fixture", candidate_provider=candidate, http_request=external.handoff_http)
    handoff.content_loader = loader
    coordinator = composition.DogfoodTrustedCallbackCoordinator(
        delegate=responses.operator_bundle.callback_coordinator, candidate_handoff=handoff)
    collector = ProductionGhAwVerticalResultCollector(callback_coordinator=coordinator,
        result_source=gates.result_source, workflows=workflows, control_repository=repository, clock=runtime.clock)
    pf.composition.__dict__.update(candidate_provider=candidate, feature_event_gateway=feature.event_gateway,
        result_source=gates.result_source, collector=collector, actions_transport=transport,
        bundle=responses.operator_bundle, responses=responses, graph_before=graph_before,
        callback_coordinator=coordinator)
    return pf, external, feature, gates



def build_ordinary_dogfood_host(preflight, *, discovery=False):
    """Real Responses host/adapter; only the two provider replies are synthetic."""
    import json
    from copy import deepcopy
    from types import SimpleNamespace
    from operator_api import API_VERSION
    from v03_dogfood_openai_host import V03DogfoodOpenAIHostConfig, V03DogfoodOpenAIResponsesHost
    role = "discovery" if discovery else "start"
    name = "aisdlc_v1_operator_inbox" if discovery else "aisdlc_v1_operation_start"
    arguments = {"api_version": API_VERSION}
    if not discovery:
        arguments.update(feature_id=preflight.slot.feature_id,
                         expected_feature_revision=1, mode="ASSISTED")
    call_id = preflight.slot.scenario + "-" + role
    requests = []
    def post(url, headers, body):
        expect(url == "https://openai.fixture/v1/responses"
               and headers["Authorization"] == "Bearer fixture-openai"
               and body["parallel_tool_calls"] is False,
               "ordinary host escaped fake provider boundary")
        expect(len(requests) < 2, "ordinary host attempted an extra model turn")
        requests.append(deepcopy(body))
        if len(requests) == 1:
            expect("previous_response_id" not in body,
                   "ordinary fresh session inherited conversation context")
            return 200, {"id": "resp_" + call_id, "status": "completed", "output": [{
                "type": "function_call", "id": "fc_" + call_id, "call_id": call_id,
                "name": name, "status": "completed", "arguments": json.dumps(arguments)}]}
        outputs = [row for row in body["input"] if row.get("type") == "function_call_output"]
        expect(len(outputs) == 1 and outputs[0]["call_id"] == call_id
               and json.loads(outputs[0]["output"]).get("ok") is True,
               "actual ordinary Responses adapter rejected the bound operation tool")
        return 200, {"id": "resp_" + call_id + "_done", "status": "completed", "output": [{
            "type": "message", "id": "msg_" + call_id, "role": "assistant",
            "content": [{"type": "output_text", "text": "The trusted result is available."}]}]}
    host = V03DogfoodOpenAIResponsesHost(
        config=V03DogfoodOpenAIHostConfig(api_key="fixture-openai", model="fixture-model",
            api_base="https://openai.fixture/v1", max_tool_turns=2),
        adapter=preflight.composition.responses.adapter, http_post=post)
    return SimpleNamespace(host=host, requests=requests, call_id=call_id)



def ordinary_structured_scenario_tests(template):
    """Exercise the existing remediation and session scenarios with actual classes."""
    import json
    from operator_store_model import operation_events
    from operator_vertical_store import vertical_projection
    from operator_vertical import VerticalInvariantError
    from v03_dogfood_scenario_runner import run_scenario, SCENARIO_ROLE_SEQUENCES
    for scenario in ("review_remediation", "session_recovery"):
        pf, external, feature, gates = ordinary_structured_runtime_fixture(template, scenario)
        host = build_ordinary_dogfood_host(pf)
        recovery = build_ordinary_dogfood_host(pf, discovery=True) if scenario == "session_recovery" else None
        observation = run_scenario(preflight=pf, host=host.host,
                                   recovery_host=recovery.host if recovery else None)
        runtime = pf.composition.runtime
        rows = operation_events(runtime.backend.read_snapshot(), observation.operation_id)
        projection = vertical_projection(runtime.backend.read_snapshot(), observation.operation_id)
        expect(observation.dispatch_roles == SCENARIO_ROLE_SEQUENCES[scenario]
               and projection["feature_id"] == pf.slot.feature_id
               and len([row for row in rows if row["event_type"] == "operation.started"]) == 1
               and len(host.requests) == 2,
               "ordinary scenario changed slot, start identity or actual role sequence")
        expect(all(row["feature_id"] == pf.slot.feature_id and row["target_ref"] == pf.slot.target_ref
                   for row in external.state["inputs"] + gates.state["inputs"]),
               "ordinary provider used a happy-path alias")
        if scenario == "session_recovery":
            expect(observation.final_status == "NEEDS_USER"
                   and observation.new_session_discovery_observed
                   and observation.worker_results_consumed == 0
                   and len(recovery.requests) == 2
                   and observation.recovery_discovery_decision_ids
                   and observation.recovery_discovery_notification_ids
                   and len(external.state["inputs"]) == 1
                   and external.state["patches"] == 0
                   and feature.state["puts"] == 0
                   and gates.state["inputs"] == []
                   and not any(row["event_type"] in {
                       "worker.callback.recorded", "worker.result.validated", "persist.confirmed"} for row in rows),
                   "session discovery consumed work, invented a Gate, or replayed an effect")
            continue
        expect(observation.final_status == "DONE" and observation.worker_results_consumed == 5
               and len(external.state["inputs"]) == 2 and external.state["patches"] == 2
               and len(gates.state["inputs"]) == 3
               and [row["payload"]["verdict"] for row in gates.state["roundtrips"]] == ["REWORK", "PASS", "PASS"],
               "actual remediation omitted REWORK, remediation, rereview or QA")
        expected_markers = ("dogfood_review_state: initial-needs-remediation",
                            "dogfood_review_state: remediated", "dogfood_review_state: remediated")
        for inputs, publication, marker in zip(gates.state["inputs"], gates.state["roundtrips"], expected_markers):
            context = json.loads(inputs["task_payload"])["feature_context"]["gate_context"]
            documents = context["documents"]
            candidate_documents = [row for row in documents if row["kind"] == "candidate_document"]
            expect(candidate_documents and any(marker in row["content"] for row in candidate_documents)
                   and all(row["source_head_sha"] == inputs["candidate_head_sha"] for row in candidate_documents)
                   and publication["payload"]["candidate_head_sha"] == inputs["candidate_head_sha"],
                   "structured Gate did not inspect actual current candidate content")
        qa_context = json.loads(gates.state["inputs"][-1]["task_payload"])["feature_context"]["gate_context"]
        review_documents = [row for row in qa_context["documents"] if row["kind"] == "review"]
        expect(len(review_documents) == 1
               and json.loads(review_documents[0]["content"]) == gates.state["roundtrips"][1]["payload"],
               "QA context lost the authentic accepted rereview")
        callbacks = [row for row in rows if row["event_type"] == "worker.callback.recorded"]
        accepted = [row for row in rows if row["event_type"] == "worker.result.validated"]
        expect(len(callbacks) == len(accepted) == 5
               and not any(row["event_type"] == "worker.result.rejected" for row in rows),
               "ordinary visible Gate did not cross actual collector/coordinator acceptance")
        for callback in callbacks:
            envelope = callback["payload"]["trusted_callback_envelope"]
            context = envelope["trusted_context"]
            if context["role"] not in {"reviewer", "qa"}:
                continue
            inputs = next(row for row in gates.state["inputs"]
                          if row["dispatch_key"] == context["external_dispatch_key"])
            expect(context["candidate_head_sha"] == inputs["candidate_head_sha"]
                   and context["expected_revision"] == int(inputs["expected_revision"]),
                   "collector accepted a different structured dispatch binding")
        original_files = json.dumps(runtime.backend.read_snapshot().files, sort_keys=True)
        for callback in callbacks:
            envelope = callback["payload"]["trusted_callback_envelope"]
            context = envelope["trusted_context"]
            if context["role"] not in {"reviewer", "qa"}:
                continue
            run_id = int(context["runtime_receipt_identity"])
            comment = gates.state["routes"][f"/issues/comments/{run_id + 100}"]
            uri = envelope["collected_outputs"][0]["trusted_uri"]
            previous = comment["updated_at"]
            comment["updated_at"] = "2026-10-10T06:00:01Z"
            try:
                pf.composition.result_source.load_content(uri)
            except VerticalInvariantError:
                pass
            else:
                raise AssertionError("structured source accepted an edited official publication")
            finally:
                comment["updated_at"] = previous
            pf.composition.result_source.load_content(uri)
            original_author_id = comment["user"]["id"]
            comment["user"]["id"] = 41898283
            try:
                pf.composition.result_source.load_content(uri)
            except VerticalInvariantError:
                pass
            else:
                raise AssertionError("structured source accepted a different account with the bot login")
            finally:
                comment["user"]["id"] = original_author_id
            pf.composition.result_source.load_content(uri)
        expect(json.dumps(runtime.backend.read_snapshot().files, sort_keys=True) == original_files
               and len(external.state["inputs"]) == external.state["patches"] == 2
               and len(gates.state["inputs"]) == 3,
               "edited-comment rejection changed Store or repeated an effect")
        expect(feature.state["puts"] == feature.state["applied"]
               == len([row for row in rows if row["event_type"] == "persist.confirmed"])
               and projection["expected_feature_revision"] == feature.state["manifest"]["revision"],
               "ordinary lifecycle bypassed canonical REST/reducer/Persist")
    print("- actual remediation structured collector/Persist and fresh session discovery passed")



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
        ("corrected Reviewer frozen CAS", reviewer_structured_admission_tests),
        ("corrected Reviewer terminal recommendations", reviewer_structured_terminal_tests),
        ("corrected Reviewer actual full pipeline", reviewer_structured_full_pipeline_tests),
        ("ordinary structured scenarios", lambda: ordinary_structured_scenario_tests(reviewer_structured_runtime_fixture()[0])),
        ("archival structured Gate preparation handoff", lambda: run_archival_bounded_test(structured_gate_authenticated_handoff_tests)),
        ("selected paid DeepSeek source/lock contracts", lambda: selected_dogfood_worker_contract_tests(validation_root)),
        ("bounded Gate detector transform",lambda:bounded_gate_detector_contract_tests(validation_root,upstream_pins={
            "reviewer":{"blob_sha":"4e25281b296ab23d89047938fb0fffe9582add2e","sha256":"5bd1fc3ff3d577607b956006228a5e4c31f0c1da903cbf37e15ee48b694d7015"},
            "qa":{"blob_sha":"f53abe319904ed858d72f8d05e1b0a5adf5ec6c0","sha256":"0c86033b7a42e2ec5d19397d7ad04daaccce1cb8423d42f79dc8e08b9448812c"}})),
        ("post-model Reviewer CAS and authority",lambda:run_archival_bounded_test(reviewer_post_model_replacement_admission_tests)),
        ("post-model Reviewer actual full pipeline",lambda:run_archival_bounded_test(reviewer_post_model_replacement_full_pipeline_tests)),
        ("Reviewer replacement CAS and authority", lambda: run_archival_reviewer_test(reviewer_replacement_admission_tests)),
        ("Reviewer replacement actual full pipeline", lambda: run_archival_reviewer_test(reviewer_replacement_full_pipeline_tests)),
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
        ("post-handoff admission", post_handoff_admission_tests),
        ("post-handoff required negatives", post_handoff_reconciliation_negative_tests),
        ("post-handoff read-only discovery", post_handoff_read_only_discovery_tests),
        ("post-handoff actual host/Persist pipeline", post_handoff_full_pipeline_tests),
        ("post-handoff provider-applied confirmation crash", lambda: post_handoff_full_pipeline_tests(crash_before_confirmation=True)),
        ("normal and remediation auto-close", lambda: normal_and_remediation_autoclose_tests(post_handoff_runtime_fixture()[0])),
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
