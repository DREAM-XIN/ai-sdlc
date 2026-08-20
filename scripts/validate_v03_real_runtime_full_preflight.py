#!/usr/bin/env python3
"""Deterministic validation for the full real-runtime release preflight."""
from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path

import run_v03_real_runtime_full_preflight as preflight_runner
from v03_effect_safety_release_evidence import REQUIRED_PRODUCTION_PREREQUISITES
from v03_real_runtime_full_preflight import (
    FullRuntimePreflightError,
    build_full_runtime_preflight,
    evaluate_release_fixture,
)
from v03_real_runtime_prerequisites import collect_trusted_main_prerequisites, missing_prerequisites

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "DREAM-XIN/ai-sdlc"
API = "https://api.github.com"
FEATURE = "F-REAL-RUNTIME-FIXTURE-0001"
REF = "feature/F-REAL-RUNTIME-FIXTURE-0001"
PR = 999
HEAD = "a" * 40
STALE_CALLBACK_PREREQUISITE = "stale_callback_reconciliation_on_main"
REAL_SMOKE_AUTHORITY_PREREQUISITE = "trusted_main_real_smoke_authority"
COLLECTOR_PREREQUISITE = "operation_bound_ghaw_collector_on_main"
LIVE_POLICY_PREREQUISITE = "protected_vertical_policy_bundle_live"
STABLE_RUN_NAME_PREREQUISITE = "trusted_main_stable_dispatch_run_name"
REAL_SMOKE_WORKFLOW_PATH = ".github/workflows/v03-real-runtime-effect-safety-smoke.yml"
READY_REAL_SMOKE_WORKFLOW = (ROOT / REAL_SMOKE_WORKFLOW_PATH).read_text(encoding="utf-8")
POLICY_NAMESPACE = "config/operator/v03-vertical-policy"
POLICY_PATHS = {
    "rollout": f"{POLICY_NAMESPACE}/effect-lineage-rollout.json",
    "writer_fence": f"{POLICY_NAMESPACE}/writer-fence-receipt.json",
    "resolution": f"{POLICY_NAMESPACE}/effect-resolution-policy.json",
    "resolution_evidence": f"{POLICY_NAMESPACE}/effect-resolution-evidence.json",
    "decision": f"{POLICY_NAMESPACE}/decision-policy.json",
}
POLICY_RECEIPT_PATH = f"{POLICY_NAMESPACE}/bundle-receipt.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def expect_rejected(callable_, message):
    try:
        callable_()
    except FullRuntimePreflightError:
        return
    raise AssertionError(message)


def encoded(text):
    return {"content": base64.b64encode(text.encode()).decode()}


def digest_json(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


READY_CALLBACK_RUNTIME = """\
from operator_vertical import VerticalInvariantError

def process_recorded_callback(executor):
    try:
        feature, _ = executor.feature_gateway.read_feature(operation_id='fixture')
        if feature is None:
            raise VerticalInvariantError('STALE_REVISION', 'fixture')
    except VerticalInvariantError:
        return 'rejected'
"""

OLD_CALLBACK_RUNTIME = """\
from operator_vertical import VerticalInvariantError

def process_recorded_callback(executor):
    feature, _ = executor.feature_gateway.read_feature(operation_id='fixture')
    try:
        return feature
    except VerticalInvariantError:
        return 'rejected'
"""


def policy_documents():
    docs = {
        "rollout": {"kind": "rollout", "policy_digest": "1" * 64},
        "writer_fence": {"kind": "writer-fence", "state": "QUIESCED"},
        "resolution": {"kind": "resolution", "policy_digest": "2" * 64},
        "resolution_evidence": {"kind": "resolution-evidence", "facts": {}},
        "decision": {"kind": "decision", "decision_types": {}},
    }
    descriptors = {
        name: {"path": POLICY_PATHS[name], "digest": digest_json(value)}
        for name, value in docs.items()
    }
    material = {
        "repository": REPOSITORY.lower(),
        "installation_commit_sha": "7" * 40,
        "state_ref": "refs/heads/ai-sdlc-operator-state",
        "operation_profile": "vertical-implementation-review-qa/v1",
        "artifacts": descriptors,
    }
    receipt = {
        "schema_version": "ai-sdlc.vertical-policy-bundle-receipt/v1",
        **material,
        "bundle_digest": digest_json(material),
        "issued_at": "2026-08-14T00:00:00Z",
        "issuer": "trusted-release-controller",
    }
    receipt["receipt_digest"] = digest_json(receipt)
    return docs, receipt


POLICY_DOCS, POLICY_RECEIPT = policy_documents()


def all_ready_getter(url):
    content_by_path = {
        ".github/workflows/ai-sdlc-gh-aw-reviewer-claude.lock.yml": "run-name: AI-SDLC gh-aw ${{ inputs.dispatch_key }}\n",
        ".github/workflows/ai-sdlc-gh-aw-reviewer-copilot.lock.yml": "run-name: AI-SDLC gh-aw ${{ inputs.dispatch_key }}\n",
        REAL_SMOKE_WORKFLOW_PATH: READY_REAL_SMOKE_WORKFLOW,
        "scripts/operator_store_github_ruleset_protection.py": "class GitHubRulesetProtectionVerifier: pass\n",
        "scripts/operator_production_feature_event_gateway.py": "class ProductionConfiguredFeatureEventGateway: pass\n",
        "scripts/operator_vertical_feature_persist_gateway.py": "class DurableVerticalFeaturePersistGateway: pass\n",
        "scripts/operator_vertical_runtime.py": "FailureClassifyingTrustedRecoveringVerticalExecutor\n",
        "scripts/operator_v03_vertical_production_runtime.py": "def build_v03_vertical_production_bundle(): pass\n",
        "scripts/operator_v03_write_runtime.py": "def build_v03_vertical_write_ready_operator_bundle(): pass\n",
        "scripts/operator_vertical_callback.py": READY_CALLBACK_RUNTIME,
        "scripts/operator_vertical_policy_state.py": "class TrustedVerticalPolicyAuthority: pass\nclass ProtectedVerticalPolicyBundleLoader: pass\n",
        "scripts/operator_protected_policy_materializer.py": "class ProtectedPolicyBundleMaterializer: pass\n",
        "scripts/postverify_v03_vertical_policy_state.py": "ProtectedVerticalPolicyBundleLoader\npost_write_verified_state_ref_sha\n",
        "scripts/operator_vertical_gh_aw_attempt_binding.py": "def build_first_attempt_production_collector(): pass\n",
        "scripts/operator_vertical_gh_aw_actions_transport.py": "class GitHubActionsVerticalGhAwTransport: pass\n",
        "scripts/provision_v03_real_runtime_fixture.py": "F-OPERATOR-V03-REAL-RUNTIME-FI-0001\ndef verify_active_files(): pass\n",
        "scripts/operator_release_feature_event_gateway.py": "class RepositoryReceiptSafeCanonicalFeatureEventGateway: pass\nstate/events/\n",
        "scripts/operator_v03_reviewer_worker_readiness.py": "selection_from_environment\nWORKER_PROVIDER_UNAVAILABLE\n",
    }
    if "/git/ref/heads/ai-sdlc-operator-state" in url:
        return {"ref": "refs/heads/ai-sdlc-operator-state", "object": {"sha": "b" * 40}}
    for name, path in POLICY_PATHS.items():
        if f"/contents/{path}?ref=ai-sdlc-operator-state" in url:
            return encoded(json.dumps(POLICY_DOCS[name], sort_keys=True))
    if f"/contents/{POLICY_RECEIPT_PATH}?ref=ai-sdlc-operator-state" in url:
        return encoded(json.dumps(POLICY_RECEIPT, sort_keys=True))
    for path, content in content_by_path.items():
        if f"/contents/{path}?ref=main" in url:
            return encoded(content)
    return None


def old_callback_getter(url):
    if "/contents/scripts/operator_vertical_callback.py?ref=main" in url:
        return encoded(OLD_CALLBACK_RUNTIME)
    return all_ready_getter(url)


def no_real_smoke_authority_getter(url):
    if f"/contents/{REAL_SMOKE_WORKFLOW_PATH}?ref=main" in url:
        return None
    return all_ready_getter(url)


def missing_collector_getter(url):
    if "/contents/scripts/operator_vertical_gh_aw_attempt_binding.py?ref=main" in url:
        return None
    return all_ready_getter(url)


def missing_copilot_run_name_getter(url):
    if "/contents/.github/workflows/ai-sdlc-gh-aw-reviewer-copilot.lock.yml?ref=main" in url:
        return encoded("name: reviewer copilot without stable run name\n")
    return all_ready_getter(url)


def tampered_policy_getter(url):
    path = POLICY_PATHS["resolution"]
    if f"/contents/{path}?ref=ai-sdlc-operator-state" in url:
        return encoded(json.dumps({"kind": "resolution", "tampered": True}, sort_keys=True))
    return all_ready_getter(url)


def missing_policy_receipt_getter(url):
    if f"/contents/{POLICY_RECEIPT_PATH}?ref=ai-sdlc-operator-state" in url:
        return None
    return all_ready_getter(url)


def runnable_pr():
    return {"number": PR, "state": "open", "head": {"ref": REF, "sha": HEAD}}


def runnable_manifest(stage="code-review", status="WORKING"):
    return {
        "revision": 12,
        "feature": {"id": FEATURE},
        "workflow": {
            "status": "ACTIVE",
            "current_stage": stage,
            "stages": [
                {"id": "implementation", "status": "DONE" if stage != "implementation" else status},
                {"id": "code-review", "status": status if stage == "code-review" else "TODO"},
                {"id": "verification", "status": status if stage == "verification" else "TODO"},
                {"id": "acceptance", "status": "TODO"},
            ],
        },
    }


def assert_zero_effect(record):
    observations = record["observations"]
    require(observations["store_mutation_attempted"] is False, "preflight attempted Store mutation")
    require(observations["external_dispatch_attempted"] is False, "preflight attempted external dispatch")
    require(observations["feature_persist_attempted"] is False, "preflight attempted Feature Persist")
    require(record["release_eligible"] is False, "preflight became release eligible")


def validate_runner_is_read_only():
    source = inspect.getsource(preflight_runner)
    tree = ast.parse(source)
    forbidden_import_prefixes = (
        "operator_store",
        "operator_vertical",
        "operator_github_actions_transport",
        "operator_production",
    )
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    require(
        not any(name.startswith(forbidden_import_prefixes) for name in imported),
        "full-runtime preflight runner imported Store/Vertical/dispatch production authority",
    )
    request_methods = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        is_request = (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "request"
            and node.func.attr == "Request"
        )
        if not is_request:
            continue
        method_keywords = [kw.value for kw in node.keywords if kw.arg == "method"]
        require(len(method_keywords) == 1, "GitHub preflight Request must declare an explicit HTTP method")
        method = method_keywords[0]
        require(
            isinstance(method, ast.Constant) and method.value == "GET",
            "full-runtime preflight attempted a non-GET GitHub request",
        )
        request_methods.append(method.value)
    require(request_methods == ["GET"], "full-runtime preflight must expose exactly one GET request construction path")
    forbidden_calls = {"launch", "persist_feature_event", "plan", "apply_plan", "operation_start", "operation_cancel"}
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    require(not (called & forbidden_calls), f"full-runtime preflight contains effect-capable call names: {sorted(called & forbidden_calls)}")


def assert_single_missing(getter, prerequisite, message):
    observed = collect_trusted_main_prerequisites(
        repository=REPOSITORY, api_base=API, get_json_optional=getter
    )
    require(observed[prerequisite] is False, message)
    require(missing_prerequisites(observed) == [prerequisite], f"{message}: unexpected collateral missing prerequisites")


def main():
    expected_count = len(REQUIRED_PRODUCTION_PREREQUISITES)
    require(expected_count == 18, "full-runtime production prerequisite contract must contain 18 exact observations")
    observed = collect_trusted_main_prerequisites(
        repository=REPOSITORY,
        api_base=API,
        get_json_optional=all_ready_getter,
    )
    require(set(observed) == set(REQUIRED_PRODUCTION_PREREQUISITES), "prerequisite probe key set drifted")
    require(all(observed.values()), f"fully provisioned trusted-main fixture did not produce {expected_count}/{expected_count} readiness")
    require(missing_prerequisites(observed) == [], "ready prerequisite set still reports missing entries")
    require(observed[STALE_CALLBACK_PREREQUISITE] is True, "reviewed stale-callback convergence shape was not detected")
    require(observed[REAL_SMOKE_AUTHORITY_PREREQUISITE] is True, "reviewed real-smoke authority workflow shape was not detected")
    require(observed[LIVE_POLICY_PREREQUISITE] is True, "valid protected policy bundle was not detected")

    assert_single_missing(
        old_callback_getter,
        STALE_CALLBACK_PREREQUISITE,
        "old callback shape with fresh read outside rejection try was accepted",
    )
    assert_single_missing(
        no_real_smoke_authority_getter,
        REAL_SMOKE_AUTHORITY_PREREQUISITE,
        "missing trusted-main real-smoke workflow was accepted",
    )
    assert_single_missing(
        missing_collector_getter,
        COLLECTOR_PREREQUISITE,
        "missing #270 first-attempt collector was accepted",
    )
    assert_single_missing(
        missing_copilot_run_name_getter,
        STABLE_RUN_NAME_PREREQUISITE,
        "Reviewer provider without stable dispatch run-name binding was accepted",
    )
    assert_single_missing(
        tampered_policy_getter,
        LIVE_POLICY_PREREQUISITE,
        "tampered protected policy artifact was accepted",
    )
    assert_single_missing(
        missing_policy_receipt_getter,
        LIVE_POLICY_PREREQUISITE,
        "missing protected policy receipt was accepted",
    )

    unavailable = {key: False for key in REQUIRED_PRODUCTION_PREREQUISITES}
    blocked = build_full_runtime_preflight(prerequisites=unavailable)
    require(blocked["status"] == "BLOCKED", "missing production prerequisites did not block full runtime")
    require(set(blocked["missing_prerequisites"]) == set(REQUIRED_PRODUCTION_PREREQUISITES), "blocked preflight lost missing prerequisites")
    assert_zero_effect(blocked)

    fixture = evaluate_release_fixture(
        feature_id=FEATURE,
        target_ref=REF,
        candidate_pr_number=PR,
        pull_request=runnable_pr(),
        manifest=runnable_manifest(),
    )
    ready = build_full_runtime_preflight(prerequisites=observed, fixture=fixture)
    require(ready["status"] == "READY", "valid runnable Feature/PR fixture did not become preflight READY")
    require(ready["fixture"]["candidate_head_sha"] == HEAD, "preflight lost exact candidate head identity")
    require(ready["fixture"]["feature_revision"] == 12, "preflight lost exact Feature revision")
    assert_zero_effect(ready)

    no_fixture = build_full_runtime_preflight(prerequisites=observed)
    require(no_fixture["status"] == "BLOCKED", "all prerequisites without an explicit fixture became READY")
    assert_zero_effect(no_fixture)
    fixture_error = build_full_runtime_preflight(prerequisites=observed, fixture_error="configured fixture is at acceptance")
    require(fixture_error["status"] == "BLOCKED", "invalid fixture evidence became READY")
    assert_zero_effect(fixture_error)

    for stage, status in (
        ("implementation", "WORKING"),
        ("code-review", "READY"),
        ("code-review", "WORKING"),
        ("verification", "READY"),
        ("verification", "WORKING"),
    ):
        row = evaluate_release_fixture(
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=runnable_pr(),
            manifest=runnable_manifest(stage, status),
        )
        require(row["current_stage"] == stage and row["stage_status"] == status, f"runnable state rejected: {stage}/{status}")

    bad_pr = runnable_pr()
    bad_pr["head"]["ref"] = "feature/foreign"
    expect_rejected(
        lambda: evaluate_release_fixture(
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=bad_pr,
            manifest=runnable_manifest(),
        ),
        "cross-ref candidate PR was accepted",
    )
    acceptance = runnable_manifest()
    acceptance["workflow"]["current_stage"] = "acceptance"
    acceptance["workflow"]["stages"][-1]["status"] = "READY"
    expect_rejected(
        lambda: evaluate_release_fixture(
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=runnable_pr(),
            manifest=acceptance,
        ),
        "acceptance Feature was accepted as fault-injection fixture",
    )
    done = runnable_manifest()
    done["workflow"]["status"] = "DONE"
    expect_rejected(
        lambda: evaluate_release_fixture(
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=runnable_pr(),
            manifest=done,
        ),
        "DONE Feature workflow was accepted as fault-injection fixture",
    )
    invalid_stage_status = runnable_manifest("code-review", "DONE")
    expect_rejected(
        lambda: evaluate_release_fixture(
            feature_id=FEATURE,
            target_ref=REF,
            candidate_pr_number=PR,
            pull_request=runnable_pr(),
            manifest=invalid_stage_status,
        ),
        "non-runnable current stage status was accepted",
    )
    malformed = deepcopy(observed)
    malformed["invented"] = True
    expect_rejected(
        lambda: build_full_runtime_preflight(prerequisites=malformed),
        "preflight accepted a prerequisite key set that drifted from release contract",
    )

    validate_runner_is_read_only()

    print("v0.3 full real-runtime zero-effect preflight validation passed")
    print(f"- trusted-main production prerequisites are exact: {expected_count}")
    print("- current #267/#273/#270/#275/#277/#279/#281 production candidates are explicit prerequisite observations")
    print("- protected Vertical policy readiness requires all six protected documents and receipt/digest integrity")
    print("- both frozen Reviewer provider workflows require stable dispatch-key run-name binding")
    print(f"- {expected_count}/{expected_count} prerequisites alone are insufficient; an explicit existing runnable Feature/PR fixture is required")
    print("- stale-callback prerequisite requires fresh Feature read inside the rejection boundary")
    print("- missing/tampered current production prerequisite material fails closed exactly")
    print("- only ACTIVE implementation/code-review/verification selector states are eligible")
    print("- acceptance/DONE/cross-ref/non-runnable fixtures fail closed")
    print("- executable preflight runner imports no Store/Vertical/dispatch authority and constructs GitHub GET requests only")
    print("- READY and BLOCKED preflight records remain release_eligible=false and attempt zero Store/dispatch/Persist effects")


if __name__ == "__main__":
    main()
