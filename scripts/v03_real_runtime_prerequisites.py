#!/usr/bin/env python3
"""Trusted-main production prerequisite probe for v0.3 real-runtime evidence.

The probe is observation-only. It reads trusted ``main`` plus the protected
Operator state ref through GitHub APIs; it never writes Store state, dispatches
a Worker, or persists a Feature Event.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
from typing import Callable, Any
from urllib.parse import quote

from v03_effect_safety_release_evidence import REQUIRED_PRODUCTION_PREREQUISITES
from v03_real_runtime_smoke_workflow_authority import smoke_workflow_authority_ready

CONTROL_REF = "main"
OPERATOR_STATE_REF = "ai-sdlc-operator-state"
STATE_REF = f"refs/heads/{OPERATOR_STATE_REF}"
VERTICAL_PROFILE = "vertical-implementation-review-qa/v1"
REVIEWER_WORKFLOWS = (
    "ai-sdlc-gh-aw-reviewer-claude.lock.yml",
    "ai-sdlc-gh-aw-reviewer-copilot.lock.yml",
)
# Compatibility for the legacy partial transport-smoke path only. The current
# full-runtime prerequisite probe still validates stable run-name binding for
# every workflow in REVIEWER_WORKFLOWS, and final #221 composition selects a
# provider through the reviewed #281 readiness policy.
REVIEWER_WORKFLOW = REVIEWER_WORKFLOWS[0]
REAL_SMOKE_WORKFLOW = "v03-real-runtime-effect-safety-smoke.yml"
POLICY_NAMESPACE = "config/operator/v03-vertical-policy"
POLICY_ARTIFACT_PATHS = {
    "rollout": f"{POLICY_NAMESPACE}/effect-lineage-rollout.json",
    "writer_fence": f"{POLICY_NAMESPACE}/writer-fence-receipt.json",
    "resolution": f"{POLICY_NAMESPACE}/effect-resolution-policy.json",
    "resolution_evidence": f"{POLICY_NAMESPACE}/effect-resolution-evidence.json",
    "decision": f"{POLICY_NAMESPACE}/decision-policy.json",
}
POLICY_RECEIPT_PATH = f"{POLICY_NAMESPACE}/bundle-receipt.json"
POLICY_BUNDLE_SCHEMA = "ai-sdlc.vertical-policy-bundle-receipt/v1"

JsonGetter = Callable[[str], Any | None]


def _decode_content(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    content = value.get("content")
    if not isinstance(content, str) or not content:
        return None
    try:
        return base64.b64decode(content).decode()
    except Exception:
        return None


def _decode_json_content(value: object) -> dict[str, Any] | None:
    text = _decode_content(value)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _digest_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sha40(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}", value))


def _sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _handler_catches_vertical_invariant(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if isinstance(caught, ast.Name):
        return caught.id == "VerticalInvariantError"
    if isinstance(caught, ast.Tuple):
        return any(isinstance(item, ast.Name) and item.id == "VerticalInvariantError" for item in caught.elts)
    return False


def _try_reads_fresh_feature(node: ast.Try) -> bool:
    """Require the fresh Feature read itself to be inside the deterministic rejection boundary."""
    for statement in node.body:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr != "read_feature":
                continue
            owner = child.func.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "feature_gateway"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "executor"
            ):
                return True
    return False


def _stale_callback_reconciliation_ready(source: str | None) -> bool:
    """Detect the reviewed #254 convergence shape without trusting version comments/markers."""
    if not source:
        return False
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "process_recorded_callback"
    ]
    if len(functions) != 1:
        return False
    function = functions[0]
    for node in ast.walk(function):
        if not isinstance(node, ast.Try):
            continue
        if not _try_reads_fresh_feature(node):
            continue
        if any(_handler_catches_vertical_invariant(handler) for handler in node.handlers):
            return True
    return False


def _live_policy_bundle_ready(
    *, repository: str, api: str, get_json_optional: JsonGetter
) -> bool:
    """Verify exact six-file protected policy bytes against the sealed receipt."""
    docs: dict[str, dict[str, Any]] = {}
    state_ref_query = quote(OPERATOR_STATE_REF, safe="")
    for name, path in POLICY_ARTIFACT_PATHS.items():
        value = get_json_optional(
            f"{api}/repos/{repository}/contents/{path}?ref={state_ref_query}"
        )
        parsed = _decode_json_content(value)
        if parsed is None:
            return False
        docs[name] = parsed
    receipt_value = get_json_optional(
        f"{api}/repos/{repository}/contents/{POLICY_RECEIPT_PATH}?ref={state_ref_query}"
    )
    receipt = _decode_json_content(receipt_value)
    if receipt is None:
        return False
    if (
        receipt.get("schema_version") != POLICY_BUNDLE_SCHEMA
        or str(receipt.get("repository") or "").lower() != repository.lower()
        or receipt.get("state_ref") != STATE_REF
        or receipt.get("operation_profile") != VERTICAL_PROFILE
        or not _sha40(receipt.get("installation_commit_sha"))
    ):
        return False

    descriptors = receipt.get("artifacts")
    if not isinstance(descriptors, dict) or set(descriptors) != set(POLICY_ARTIFACT_PATHS):
        return False
    for name, expected_path in POLICY_ARTIFACT_PATHS.items():
        descriptor = descriptors.get(name)
        if (
            not isinstance(descriptor, dict)
            or set(descriptor) != {"path", "digest"}
            or descriptor.get("path") != expected_path
            or not _sha256(descriptor.get("digest"))
            or _digest_json(docs[name]) != descriptor.get("digest")
        ):
            return False

    raw = dict(receipt)
    stored_receipt_digest = raw.pop("receipt_digest", None)
    if not _sha256(stored_receipt_digest) or stored_receipt_digest != _digest_json(raw):
        return False
    material = {
        "repository": repository.lower(),
        "installation_commit_sha": receipt["installation_commit_sha"],
        "state_ref": STATE_REF,
        "operation_profile": VERTICAL_PROFILE,
        "artifacts": descriptors,
    }
    return _sha256(receipt.get("bundle_digest")) and receipt.get("bundle_digest") == _digest_json(material)


def collect_trusted_main_prerequisites(
    *,
    repository: str,
    api_base: str,
    get_json_optional: JsonGetter,
) -> dict[str, bool]:
    """Observe the exact production prerequisites required by release PASS."""
    api = api_base.rstrip("/")

    def main_file_text(path: str) -> str | None:
        result = get_json_optional(
            f"{api}/repos/{repository}/contents/{path}?ref={quote(CONTROL_REF, safe='')}"
        )
        return _decode_content(result)

    reviewer_workflows = [
        main_file_text(f".github/workflows/{workflow}") for workflow in REVIEWER_WORKFLOWS
    ]
    stable_run_name = all(
        text
        and re.search(
            r"^run-name:\s+AI-SDLC gh-aw .*inputs\.dispatch_key",
            text,
            flags=re.MULTILINE,
        )
        for text in reviewer_workflows
    )
    real_smoke_workflow = main_file_text(f".github/workflows/{REAL_SMOKE_WORKFLOW}")
    ruleset_runtime = main_file_text("scripts/operator_store_github_ruleset_protection.py")
    exact_event_runtime = main_file_text("scripts/operator_production_feature_event_gateway.py")
    persist_runtime = main_file_text("scripts/operator_vertical_feature_persist_gateway.py")
    classified_runtime = main_file_text("scripts/operator_vertical_runtime.py")
    integrated_vertical_runtime = main_file_text("scripts/operator_v03_vertical_production_runtime.py")
    write_runtime = main_file_text("scripts/operator_v03_write_runtime.py")
    callback_runtime = main_file_text("scripts/operator_vertical_callback.py")
    policy_authority_runtime = main_file_text("scripts/operator_vertical_policy_state.py")
    policy_materializer_runtime = main_file_text("scripts/operator_protected_policy_materializer.py")
    policy_postverify_runtime = main_file_text("scripts/postverify_v03_vertical_policy_state.py")
    collector_runtime = main_file_text("scripts/operator_vertical_gh_aw_attempt_binding.py")
    actions_transport_runtime = main_file_text("scripts/operator_vertical_gh_aw_actions_transport.py")
    fixture_runtime = main_file_text("scripts/provision_v03_real_runtime_fixture.py")
    canonical_event_runtime = main_file_text("scripts/operator_release_feature_event_gateway.py")
    reviewer_readiness_runtime = main_file_text("scripts/operator_v03_reviewer_worker_readiness.py")
    state_ref = get_json_optional(f"{api}/repos/{repository}/git/ref/heads/{OPERATOR_STATE_REF}")

    observed = {
        "trusted_main_stable_dispatch_run_name": bool(stable_run_name),
        "ruleset_store_runtime_on_main": bool(
            ruleset_runtime and "GitHubRulesetProtectionVerifier" in ruleset_runtime
        ),
        "exact_feature_event_runtime_on_main": bool(
            exact_event_runtime and "ProductionConfiguredFeatureEventGateway" in exact_event_runtime
        ),
        "vertical_persist_gateway_on_main": bool(
            persist_runtime and "DurableVerticalFeaturePersistGateway" in persist_runtime
        ),
        "classified_persist_recovery_on_main": bool(
            classified_runtime and "FailureClassifyingTrustedRecoveringVerticalExecutor" in classified_runtime
        ),
        "integrated_vertical_adapter_runtime_on_main": bool(
            integrated_vertical_runtime and "build_v03_vertical_production_bundle" in integrated_vertical_runtime
        ),
        "full_vertical_write_factory_on_main": bool(
            write_runtime and "build_v03_vertical_write_ready_operator_bundle" in write_runtime
        ),
        "stale_callback_reconciliation_on_main": _stale_callback_reconciliation_ready(callback_runtime),
        "trusted_main_real_smoke_authority": smoke_workflow_authority_ready(real_smoke_workflow),
        "operator_state_ref_exists": state_ref is not None,
        "trusted_vertical_policy_authority_on_main": bool(
            policy_authority_runtime
            and "class TrustedVerticalPolicyAuthority" in policy_authority_runtime
            and "class ProtectedVerticalPolicyBundleLoader" in policy_authority_runtime
        ),
        "protected_vertical_policy_materializer_on_main": bool(
            policy_materializer_runtime
            and "class ProtectedPolicyBundleMaterializer" in policy_materializer_runtime
            and policy_postverify_runtime
            and "post_write_verified_state_ref_sha" in policy_postverify_runtime
            and "ProtectedVerticalPolicyBundleLoader" in policy_postverify_runtime
        ),
        "operation_bound_ghaw_collector_on_main": bool(
            collector_runtime and "def build_first_attempt_production_collector" in collector_runtime
        ),
        "vertical_ghaw_actions_transport_on_main": bool(
            actions_transport_runtime and "class GitHubActionsVerticalGhAwTransport" in actions_transport_runtime
        ),
        "real_runtime_fixture_provisioner_on_main": bool(
            fixture_runtime
            and "F-OPERATOR-V03-REAL-RUNTIME-FI-0001" in fixture_runtime
            and "def verify_active_files" in fixture_runtime
        ),
        "canonical_repository_feature_event_gateway_on_main": bool(
            canonical_event_runtime
            and "RepositoryReceiptSafeCanonicalFeatureEventGateway" in canonical_event_runtime
            and "state/events/" in canonical_event_runtime
        ),
        "reviewer_worker_readiness_on_main": bool(
            reviewer_readiness_runtime
            and "selection_from_environment" in reviewer_readiness_runtime
            and "WORKER_PROVIDER_UNAVAILABLE" in reviewer_readiness_runtime
        ),
        "protected_vertical_policy_bundle_live": _live_policy_bundle_ready(
            repository=repository,
            api=api,
            get_json_optional=get_json_optional,
        ),
    }
    if set(observed) != set(REQUIRED_PRODUCTION_PREREQUISITES):
        raise RuntimeError("trusted-main prerequisite probe drifted from release evidence contract")
    if any(type(value) is not bool for value in observed.values()):
        raise RuntimeError("trusted-main prerequisite probe returned a non-boolean observation")
    return observed


def missing_prerequisites(observed: dict[str, bool]) -> list[str]:
    if set(observed) != set(REQUIRED_PRODUCTION_PREREQUISITES):
        raise ValueError("prerequisite observation key set is not exact")
    return sorted(key for key, ready in observed.items() if ready is not True)
