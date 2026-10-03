#!/usr/bin/env python3
"""Pure authority-gate validation for the v0.3 dogfood runtime driver."""
from __future__ import annotations

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
    from operator_store_backends import OperatorStoreRuntime
    from operator_store_git import MemoryStateRefBackend
    from operator_store_model import operation_events, rebuild_projection
    from operator_store_protection import PROTECTED, StaticProtectionVerifier
    from operator_vertical import VERTICAL_PROFILE
    from v03_dogfood_fixture_pool import require_slot

    repository = "DREAM-XIN/ai-sdlc"
    state_ref = "refs/heads/ai-sdlc-operator-state"
    feature_id = require_slot("happy_path").feature_id
    old_context = "old-installation-context"
    current_context = "current-installation-context"
    now = "2026-10-03T02:00:00Z"

    def seeded(lookup_state):
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
                trusted_context_digest=old_context,
            )
        )
        preflight = SimpleNamespace(
            execution=SimpleNamespace(repository=repository),
            slot=require_slot("happy_path"),
            trusted_context_digest=current_context,
            composition=SimpleNamespace(runtime=runtime),
        )
        return runtime, preflight, operation_id, reserved.result["external_dispatch_key"]

    runtime, preflight, operation_id, external_key = seeded("NOT_LAUNCHED")
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

    launched_runtime, launched_preflight, _, _ = seeded("LAUNCHED")
    try:
        prepare_previous_installation_operation(launched_preflight)
    except V03DogfoodRuntimeDriverError:
        pass
    else:
        raise AssertionError("launched predecessor escaped bounded transition")
    expect(
        rebuild_projection(launched_runtime.backend.read_snapshot(), operation_id)["generation"] == 0,
        "rejected launched predecessor was mutated",
    )
    print("- exact old-context NOT_LAUNCHED window takes over once with one immutable effect key")



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
