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
    """Freeze the one exact historical pre-HTTP recovery boundary."""
    from operator_store_model import is_immutable_path
    from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
    from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
    from operator_vertical_gh_aw_actions_transport import (
        GitHubActionsVerticalGhAwTransport,
        GitHubActionsWorkflowTransportConfig,
    )

    h = driver_subject.HISTORICAL_PREHTTP_RECOVERY
    expect(h["run_id"] == 37089196141, "historical recovery run id drifted")
    expect(
        h["installation_commit_sha"] == "ca74b11b360516c5b881126c7183aaf9d67d83d8",
        "historical recovery source head drifted",
    )
    expect(
        h["operation_id"] == "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
        and h["attempt_id"] == "eca-1f340ab49f04ab7b0747907075aead41b8fa6f23"
        and h["external_dispatch_key"] == "dispatch-d774674fa60b1708668a28ff73c43fe4334eebe1",
        "historical recovery exact Store identity drifted",
    )
    expect(
        is_immutable_path(driver_subject.PREHTTP_RECOVERY_MARKER_PATH),
        "pre-HTTP recovery marker is not a Store immutable path",
    )
    expect(
        "/operations/" in driver_subject.PREHTTP_RECOVERY_MARKER_PATH
        and "/reservations/external/" not in driver_subject.PREHTTP_RECOVERY_MARKER_PATH,
        "recovery marker could masquerade as external reservation authority",
    )

    # Match Git's actual blob object identity. The live recovery proof compares
    # current trusted-main source with immutable historical blob SHAs, so a
    # textual backslash-zero must never stand in for Git's NUL header byte.
    import subprocess
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="v03-dogfood-blob-hash-") as temporary:
        fixture = Path(temporary) / "blob.bin"
        fixture.write_bytes(b"dogfood-blob-regression\n")
        expected_blob = subprocess.run(
            ["git", "hash-object", str(fixture)],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        expect(
            driver_subject._git_blob_sha(fixture) == expected_blob,
            "dogfood source blob hashing differs from Git object identity",
        )

    workflows = GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-deepseek.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )
    config = GitHubActionsWorkflowTransportConfig(
        control_repository="DREAM-XIN/ai-sdlc",
        token="test-only-token",
        workflows=workflows,
    )
    dispatch = {
        "operation_id": h["operation_id"],
        "operation_generation": h["generation"],
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": h["semantic_effect_key"],
        "external_dispatch_key": h["external_dispatch_key"],
        "dispatch_id": h["dispatch_id"],
        "target_repository": "dream-xin/ai-sdlc",
        "target_ref": h["target_ref"],
        "feature_id": h["feature_id"],
        "expected_revision": 1,
        "feature_stage": h["stage"],
        "task_id": h["task_id"],
        "task_identity": h["task_identity"],
        "role": h["role"],
        "candidate_pr_number": h["candidate_pr_number"],
        "candidate_head_sha": h["candidate_head_sha"],
    }
    inputs = GhAwVerticalRoleDispatchGateway(
        transport=object(), workflows=workflows
    )._inputs(dispatch)
    calls = []

    def forbidden_http(**kwargs):
        calls.append(kwargs)
        raise AssertionError("historical validator reached HTTP")

    historical = GitHubActionsVerticalGhAwTransport(
        config, http=forbidden_http, sleeper=lambda _seconds: None
    )
    try:
        historical.dispatch(
            workflow=workflows.developer_workflow,
            ref="main",
            inputs=inputs,
        )
    except VerticalInvariantError as exc:
        expect(exc.code == "POLICY_DENIED", "historical replay changed rejection code")
    else:
        raise AssertionError("historical Developer payload unexpectedly passed shared validator")
    expect(not calls, "historical Developer rejection crossed the HTTP boundary")

    proof_source = inspect.getsource(driver_subject._verify_historical_prehttp_proof)
    recover_source = inspect.getsource(driver_subject.recover_historical_prehttp_attempt)
    marker_source = inspect.getsource(driver_subject._plan_prehttp_recovery_marker)
    execute_source = inspect.getsource(driver_subject._execute_live)
    expect(
        "POLICY_DENIED_BEFORE_HTTP" in proof_source
        and '"provider_invalidation": False' in proof_source
        and "historical_transport.dispatch" in proof_source
        and "if http_calls:" in proof_source,
        "historical proof no longer distinguishes pre-HTTP software proof from provider invalidation",
    )
    expect(
        'require_unknown=True' in marker_source
        and 'StoreMutation("create_immutable", PREHTTP_RECOVERY_MARKER_PATH, value)' in marker_source,
        "recovery marker no longer requires exact UNKNOWN state before immutable acquisition",
    )
    marker_commit = recover_source.index("marker_result = runtime.commit_replanned")
    launch = recover_source.index("preflight.composition.dispatch_gateway.launch")
    expect(marker_commit < launch, "recovery POST can occur before durable marker acquisition")
    expect(
        recover_source.count("dispatch_gateway.launch") == 1,
        "historical recovery contains more than one explicit launch site",
    )
    expect(
        "permission was already consumed" in recover_source
        and "dispatch_gateway.lookup" in recover_source,
        "replayed recovery marker does not fail closed to lookup-only behavior",
    )
    expect(
        execute_source.index("recover_historical_prehttp_attempt(preflight)")
        < execute_source.index("prepare_previous_installation_operation(preflight)"),
        "historical attempt recovery is not evaluated before old no-attempt transition",
    )
    print("- exact historical POLICY_DENIED is replayed with zero HTTP and one marker-before-launch site")
    # Exercise the live recovery control flow with a real immutable Store
    # marker plan, but no network/Worker effects.
    from types import SimpleNamespace
    from unittest.mock import patch
    from operator_store_model import StoreSnapshot, apply_plan_to_snapshot

    class Backend:
        def __init__(self, snapshot=None):
            self.snapshot = snapshot or StoreSnapshot(ref_sha="fixture-state-0", files={})

        def read_snapshot(self):
            return self.snapshot

    class Runtime:
        def __init__(self, backend, order):
            self.backend = backend
            self.order = order
            self.commits = 0

        def clock(self):
            return "2026-10-03T06:55:00Z"

        def commit_replanned(self, planner, *, max_attempts=4):
            self.order.append("marker-commit")
            self.commits += 1
            plan = planner(self.backend.snapshot)
            if plan.mutations:
                self.backend.snapshot = apply_plan_to_snapshot(
                    self.backend.snapshot,
                    plan,
                    new_ref_sha=f"fixture-state-{self.commits}",
                )
            return SimpleNamespace(result=plan.result)

    class RaceRuntime(Runtime):
        def commit_replanned(self, planner, *, max_attempts=4):
            self.order.append("marker-race")
            self.commits += 1
            # Another writer won the marker CAS. The losing path must only
            # lookup and must never call launch.
            return SimpleNamespace(result={"acquired": False})

    class Gateway:
        def __init__(self, lookups, order, *, launch_receipt=None):
            self.lookups = list(lookups)
            self.order = order
            self.launch_count = 0
            self.launch_receipt = launch_receipt or {
                "lookup_state": "LAUNCHED",
                "receipt_id": "recovery-run-1",
            }

        def lookup(self, *, external_dispatch_key):
            expect(
                external_dispatch_key == h["external_dispatch_key"],
                "recovery lookup escaped the fixed external key",
            )
            self.order.append("lookup")
            if not self.lookups:
                raise AssertionError("unexpected extra recovery lookup")
            return self.lookups.pop(0)

        def launch(self, *, dispatch):
            self.order.append("launch")
            self.launch_count += 1
            return dict(self.launch_receipt)

    def preflight_for(runtime, gateway):
        return SimpleNamespace(
            slot=SimpleNamespace(scenario="happy_path"),
            composition=SimpleNamespace(runtime=runtime, dispatch_gateway=gateway),
            trusted_context_digest="current-test-context",
            execution=SimpleNamespace(installation_commit_sha="current-test-installation"),
        )

    def fake_dispatch(_snapshot, _preflight):
        return {"_attempt_id": h["attempt_id"], "external_dispatch_key": h["external_dispatch_key"]}

    fake_identity = (
        {"status": "BLOCKED"},
        {"candidate_head_sha": h["candidate_head_sha"]},
        {"attempt_id": h["attempt_id"]},
    )

    order = []
    runtime = Runtime(Backend(), order)
    gateway = Gateway([{"lookup_state": "NOT_LAUNCHED", "receipt_id": None}], order)
    preflight = preflight_for(runtime, gateway)
    recorded = []
    with (
        patch.object(driver_subject, "_historical_recovery_dispatch", side_effect=fake_dispatch),
        patch.object(driver_subject, "_verify_historical_prehttp_proof", return_value="proof-digest"),
        patch.object(driver_subject, "_historical_attempt_identity", return_value=fake_identity),
        patch.object(
            driver_subject,
            "_record_exact_recovery_launch",
            side_effect=lambda _preflight, *, receipt: recorded.append(dict(receipt)),
        ),
    ):
        expect(
            driver_subject.recover_historical_prehttp_attempt(preflight) is True,
            "first exact pre-HTTP recovery did not complete",
        )
    expect(
        order == ["lookup", "marker-commit", "launch"],
        "marker was not durably acquired between absence lookup and sole launch",
    )
    expect(gateway.launch_count == 1, "first recovery did not contain exactly one launch")
    expect(len(recorded) == 1 and recorded[0]["receipt_id"] == "recovery-run-1",
           "exact recovery receipt was not recorded")
    marker = runtime.backend.snapshot.get(driver_subject.PREHTTP_RECOVERY_MARKER_PATH)
    expect(marker is not None, "recovery launch occurred without durable marker")
    driver_subject._validate_prehttp_recovery_marker(marker, "proof-digest")

    replay_order = []
    replay_runtime = Runtime(Backend(runtime.backend.snapshot), replay_order)
    replay_gateway = Gateway(
        [{"lookup_state": "LAUNCHED", "receipt_id": "recovery-run-1"}],
        replay_order,
    )
    replay = preflight_for(replay_runtime, replay_gateway)
    with (
        patch.object(driver_subject, "_historical_recovery_dispatch", side_effect=fake_dispatch),
        patch.object(driver_subject, "_verify_historical_prehttp_proof", return_value="proof-digest"),
        patch.object(driver_subject, "_historical_attempt_identity", return_value=fake_identity),
        patch.object(driver_subject, "_record_exact_recovery_launch", return_value=None),
    ):
        expect(
            driver_subject.recover_historical_prehttp_attempt(replay) is True,
            "marker replay could not adopt an exact launched receipt",
        )
    expect(
        replay_gateway.launch_count == 0 and replay_order == ["lookup"],
        "durable marker replay performed a second launch",
    )

    # A later trusted-main installation must adopt the already durable exact
    # LAUNCHED fact instead of trying to rewrite the same event identity under
    # a new trusted-context digest.
    prior_launch = {
        "sequence": h["last_sequence"] + 1,
        "operation_generation": h["generation"],
        "event_type": "dispatch.launch.lookup-recorded",
        "trusted_context_digest": "previous-trusted-main-context",
        "payload": {
            "external_dispatch_key": h["external_dispatch_key"],
            "lookup_state": "LAUNCHED",
            "receipt_id": "recovery-run-1",
        },
    }
    with patch.object(driver_subject, "operation_events", return_value=[prior_launch]):
        expect(
            driver_subject._durable_exact_recovery_launch(
                object(), receipt_id="recovery-run-1"
            ) is True,
            "exact durable recovery launch was not recognized across installations",
        )
        try:
            driver_subject._durable_exact_recovery_launch(
                object(), receipt_id="different-receipt"
            )
        except V03DogfoodRuntimeDriverError:
            pass
        else:
            raise AssertionError("conflicting durable recovery receipt was accepted")

        class DurableReplayRuntime:
            def __init__(self):
                self.commits = 0
                self.plan = None

            def clock(self):
                return "2026-10-06T08:30:00Z"

            def commit_replanned(self, planner, *, max_attempts=4):
                self.commits += 1
                snapshot = SimpleNamespace(ref_sha="durable-launch-state")
                self.plan = planner(snapshot)
                return SimpleNamespace(result=self.plan.result)

        durable_runtime = DurableReplayRuntime()
        durable_preflight = SimpleNamespace(
            composition=SimpleNamespace(runtime=durable_runtime),
            trusted_context_digest="new-trusted-main-context",
        )
        with patch.object(
            driver_subject,
            "plan_launch_lookup",
            side_effect=AssertionError("durable exact launch was rewritten"),
        ):
            driver_subject._record_exact_recovery_launch(
                durable_preflight,
                receipt={"lookup_state": "LAUNCHED", "receipt_id": "recovery-run-1"},
            )
        expect(
            durable_runtime.commits == 1
            and durable_runtime.plan is not None
            and not durable_runtime.plan.mutations
            and durable_runtime.plan.result.get("already_recorded") is True,
            "cross-installation recovery replay did not become a no-write adoption",
        )

    crash_order = []
    crash_runtime = Runtime(Backend(runtime.backend.snapshot), crash_order)
    crash_gateway = Gateway(
        [{"lookup_state": "NOT_LAUNCHED", "receipt_id": None}],
        crash_order,
    )
    crash = preflight_for(crash_runtime, crash_gateway)
    with (
        patch.object(driver_subject, "_historical_recovery_dispatch", side_effect=fake_dispatch),
        patch.object(driver_subject, "_verify_historical_prehttp_proof", return_value="proof-digest"),
        patch.object(driver_subject, "_historical_attempt_identity", return_value=fake_identity),
    ):
        try:
            driver_subject.recover_historical_prehttp_attempt(crash)
        except V03DogfoodRuntimeDriverError:
            pass
        else:
            raise AssertionError("crash-after-marker replay escaped fail-closed boundary")
    expect(
        crash_gateway.launch_count == 0 and crash_order == ["lookup"],
        "crash-after-marker replay attempted another launch",
    )

    unknown_order = []
    unknown_runtime = Runtime(Backend(), unknown_order)
    unknown_gateway = Gateway(
        [{"lookup_state": "UNKNOWN", "receipt_id": None}],
        unknown_order,
    )
    unknown = preflight_for(unknown_runtime, unknown_gateway)
    with (
        patch.object(driver_subject, "_historical_recovery_dispatch", side_effect=fake_dispatch),
        patch.object(driver_subject, "_verify_historical_prehttp_proof", return_value="proof-digest"),
        patch.object(driver_subject, "_historical_attempt_identity", return_value=fake_identity),
    ):
        try:
            driver_subject.recover_historical_prehttp_attempt(unknown)
        except V03DogfoodRuntimeDriverError:
            pass
        else:
            raise AssertionError("UNKNOWN preflight escaped recovery fence")
    expect(
        unknown_gateway.launch_count == 0
        and unknown_runtime.commits == 0
        and unknown_order == ["lookup"],
        "UNKNOWN preflight mutated Store or launched recovery",
    )

    race_order = []
    race_runtime = RaceRuntime(Backend(), race_order)
    race_gateway = Gateway(
        [
            {"lookup_state": "NOT_LAUNCHED", "receipt_id": None},
            {"lookup_state": "NOT_LAUNCHED", "receipt_id": None},
        ],
        race_order,
    )
    race = preflight_for(race_runtime, race_gateway)
    with (
        patch.object(driver_subject, "_historical_recovery_dispatch", side_effect=fake_dispatch),
        patch.object(driver_subject, "_verify_historical_prehttp_proof", return_value="proof-digest"),
        patch.object(driver_subject, "_historical_attempt_identity", return_value=fake_identity),
    ):
        try:
            driver_subject.recover_historical_prehttp_attempt(race)
        except V03DogfoodRuntimeDriverError:
            pass
        else:
            raise AssertionError("losing recovery marker race escaped fail-closed boundary")
    expect(
        race_gateway.launch_count == 0
        and race_order == ["lookup", "marker-race", "lookup"],
        "losing marker race obtained launch authority",
    )
    print("- recovery marker acquisition/replay/crash/UNKNOWN/CAS-race paths are dynamically one-shot")




def historical_worker_adoption_tests():
    """A durable launch receipt is checked before any new model session."""
    from copy import deepcopy
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    import v03_dogfood_scenario_runner as runner

    h = driver_subject.HISTORICAL_PREHTTP_RECOVERY
    installation = "a" * 40
    receipt = "37204777409"
    workflow = h["workflow_file"]
    good = dict(
        id=int(receipt), event="workflow_dispatch", head_branch="main",
        head_sha=installation, path=".github/workflows/" + workflow,
        display_title="AI-SDLC gh-aw " + h["external_dispatch_key"],
        run_attempt=1, status="completed", conclusion="success",
    )
    backend = SimpleNamespace(read_snapshot=Mock(return_value=object()))
    source = SimpleNamespace(
        config=SimpleNamespace(control_repository="DREAM-XIN/ai-sdlc", control_token="read-only-test-token"),
        _json=Mock(),
    )
    preflight = SimpleNamespace(
        execution=SimpleNamespace(installation_commit_sha=installation),
        workflows=SimpleNamespace(workflow_for=Mock(return_value=workflow)),
        composition=SimpleNamespace(runtime=SimpleNamespace(backend=backend), result_source=source,
                                    adapter=object()),
    )
    cases = (
        dict(head_sha="5ed049a4cce9c39a42385337da35a46dcaf378eb",
             conclusion="failure"),
        dict(conclusion="failure"),
        dict(run_attempt=2),
        dict(display_title="AI-SDLC gh-aw wrong-key"),
    )
    for changed in cases:
        source._json.reset_mock()
        source._json.return_value = {**good, **changed}
        with (
            patch.object(driver_subject, "assemble_preflight", return_value=preflight),
            patch.object(driver_subject, "_head", return_value=installation),
            patch.object(driver_subject, "recover_historical_prehttp_attempt", return_value=True),
            patch.object(driver_subject, "prepare_previous_installation_operation") as takeover,
            patch.object(runner, "_current_launch_binding",
                         return_value=({}, {"role": "developer"}, receipt)),
            patch.object(driver_subject, "dogfood_responses_host_config") as config,
            patch.object(driver_subject, "V03DogfoodOpenAIResponsesHost") as host,
            patch.object(driver_subject, "run_scenario") as scenario,
        ):
            try:
                driver_subject._execute_live(mode=RUN, scenario="happy_path")
            except V03DogfoodRuntimeDriverError as exc:
                expect("HISTORICAL_WORKER_NOT_COLLECTIBLE" in str(exc),
                       "uncollectible receipt lost actionable blocker identity")
            else:
                raise AssertionError("uncollectible historical Worker reached the model")
            expect(not config.called and not host.called and not scenario.called,
                   "historical Worker rejection happened after model/session work")
            expect(not takeover.called, "adopted receipt triggered a replacement generation")
            source._json.assert_called_once_with(
                "DREAM-XIN/ai-sdlc", "/actions/runs/" + receipt, "read-only-test-token"
            )

    class StopAfterGuard(RuntimeError):
        pass

    source._json.return_value = deepcopy(good)
    with (
        patch.object(driver_subject, "assemble_preflight", return_value=preflight),
        patch.object(driver_subject, "_head", return_value=installation),
        patch.object(driver_subject, "recover_historical_prehttp_attempt", return_value=True),
        patch.object(runner, "_current_launch_binding",
                     return_value=({}, {"role": "developer"}, receipt)),
        patch.object(driver_subject, "dogfood_responses_host_config", return_value=object()),
        patch.object(driver_subject, "V03DogfoodOpenAIResponsesHost", return_value=object()) as host,
        patch.object(driver_subject, "run_scenario", side_effect=StopAfterGuard) as scenario,
    ):
        try:
            driver_subject._execute_live(mode=RUN, scenario="happy_path")
        except StopAfterGuard:
            pass
        else:
            raise AssertionError("collectible current first-attempt receipt did not reach scenario")
        expect(host.call_count == 1 and scenario.call_count == 1,
               "guard prevented a valid exact current-main receipt from continuing")
    print("- adopted historical Worker is checked before model work; stale/failed/rerun/wrong-key remain blocked")



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

    installation_transition_tests()
    prehttp_recovery_fence_tests()
    historical_worker_adoption_tests()
    historical_worker_evidence_tests()

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
