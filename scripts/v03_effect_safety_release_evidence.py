#!/usr/bin/env python3
"""Strict release-evidence contract for v0.3 real-runtime effect safety.

Deterministic support is intentionally a different evidence class. This module
accepts BLOCKED/PENDING records for honest work tracking, but a PASS record is
release-eligible only when it carries exact real-runtime identities, durable
protected-Store evidence, complete trusted-main production prerequisites, exact
protected Vertical policy authority, and zero release safety counters.
"""
from __future__ import annotations

import re
from typing import Any

from v03_effect_safety_release_scenarios import RELEASE_REQUIRED_SCENARIOS

SCHEMA_VERSION = "ai-sdlc.v0.3-effect-safety-release-evidence/v1"
REAL_RUNTIME_EVIDENCE_LEVEL = "real-runtime"
NON_PASS_STATUSES = frozenset({"PENDING", "BLOCKED"})
POLICY_RECEIPT_PATH = "config/operator/v03-vertical-policy/bundle-receipt.json"
ZERO_SAFETY_COUNTERS = (
    "duplicate_external_effect_count",
    "unauthorized_lifecycle_transition_count",
    "stale_evidence_accepted_count",
    "speculative_retry_under_unknown_count",
)
REQUIRED_PRODUCTION_PREREQUISITES = frozenset(
    {
        "trusted_main_stable_dispatch_run_name",
        "ruleset_store_runtime_on_main",
        "exact_feature_event_runtime_on_main",
        "vertical_persist_gateway_on_main",
        "classified_persist_recovery_on_main",
        "integrated_vertical_adapter_runtime_on_main",
        "full_vertical_write_factory_on_main",
        "stale_callback_reconciliation_on_main",
        "trusted_main_real_smoke_authority",
        "operator_state_ref_exists",
        "trusted_vertical_policy_authority_on_main",
        "protected_vertical_policy_materializer_on_main",
        "operation_bound_ghaw_collector_on_main",
        "vertical_ghaw_actions_transport_on_main",
        "real_runtime_fixture_provisioner_on_main",
        "canonical_repository_feature_event_gateway_on_main",
        "reviewer_worker_readiness_on_main",
        "protected_vertical_policy_bundle_live",
    }
)

SCENARIO_ASSERTIONS = {
    "lost-ack-crash-takeover": (
        "lost_ack_recovered",
        "takeover_adopted_same_receipt",
        "exactly_one_external_run",
        "durable_generation_takeover_proven",
        "feature_persist_at_most_once",
    ),
    "cancel-before-launch-authorization": (
        "cancel_durable_before_launch_authorization",
        "launch_rejected_after_cancel",
        "zero_external_runs",
    ),
    "launch-authorized-before-cancel": (
        "launch_authorized_before_cancel",
        "only_exact_authorized_dispatch_completed",
        "no_automatic_persist_authority",
    ),
    "cancel-before-persist-linearization": (
        "cancel_durable_before_persist_linearization",
        "zero_feature_writes",
    ),
    "persist-linearized-before-cancel": (
        "persist_linearized_before_cancel",
        "only_exact_linearized_write_completed",
        "no_post_cancel_progression",
    ),
    "persist-ack-loss-recovery": (
        "persist_write_ack_lost",
        "exact_receipt_lookup_before_retry",
        "single_feature_write",
    ),
    "unknown-takeover": (
        "lookup_unknown",
        "takeover_preserved_same_external_key",
        "no_speculative_retry",
    ),
    "duplicate-callback": (
        "duplicate_callback_delivered",
        "one_durable_callback",
        "at_most_one_persist",
    ),
    "duplicate-worker-completion": (
        "same_worker_completion_collected_twice",
        "same_deterministic_callback_id",
        "one_durable_callback",
        "at_most_one_persist",
        "zero_second_external_run",
    ),
    "out-of-order-callback": (
        "late_callback_delivered_after_takeover",
        "stale_callback_rejected",
        "zero_stale_translation_or_persist",
    ),
    "concurrent-resume": (
        "concurrent_resume_race_exercised",
        "cas_fenced_stale_writer",
        "at_most_one_external_or_lifecycle_effect",
    ),
    "stale-candidate-result": (
        "candidate_changed",
        "stale_result_delivered",
        "stale_result_rejected",
        "fresh_candidate_work_exact_bound",
    ),
}

RUNTIME_RECEIPT_SCENARIOS = frozenset(
    {
        "lost-ack-crash-takeover",
        "launch-authorized-before-cancel",
        "unknown-takeover",
        "duplicate-callback",
        "duplicate-worker-completion",
        "out-of-order-callback",
        "stale-candidate-result",
    }
)
CALLBACK_SCENARIOS = frozenset(
    {
        "lost-ack-crash-takeover",
        "duplicate-callback",
        "duplicate-worker-completion",
        "out-of-order-callback",
        "stale-candidate-result",
    }
)
SUCCESSFUL_CALLBACK_PERSIST_SCENARIOS = frozenset(
    {
        "lost-ack-crash-takeover",
        "duplicate-callback",
        "duplicate-worker-completion",
    }
)
PERSIST_RECEIPT_SCENARIOS = frozenset(
    {
        "persist-linearized-before-cancel",
        "persist-ack-loss-recovery",
    }
)
EXACT_PERSIST_SCENARIOS = PERSIST_RECEIPT_SCENARIOS | SUCCESSFUL_CALLBACK_PERSIST_SCENARIOS

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GITHUB_RUN_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/actions/runs/[1-9][0-9]*$")
_GITHUB_EVIDENCE_URI = re.compile(r"^https://github\.com/[^/]+/[^/]+/(?:actions/runs|pull|issues)/.+$")


class ReleaseEvidenceError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseEvidenceError(message)


def _string(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")
    return value.strip()


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field} must be an integer")
    _require(value >= minimum, f"{field} must be >= {minimum}")
    return value


def _validate_common(record: dict[str, Any]) -> str:
    _require(isinstance(record, dict), "release evidence must be an object")
    _require(record.get("schema_version") == SCHEMA_VERSION, "unsupported release evidence schema_version")
    scenario = _string(record.get("scenario_id"), "scenario_id")
    _require(scenario in RELEASE_REQUIRED_SCENARIOS, f"unknown release scenario: {scenario}")
    _require(scenario in SCENARIO_ASSERTIONS, f"missing assertion contract for scenario: {scenario}")

    subject = record.get("subject")
    _require(isinstance(subject, dict), "subject must be an object")
    repository = _string(subject.get("repository"), "subject.repository")
    _require("/" in repository and repository.count("/") == 1, "subject.repository must be owner/repo")
    _string(subject.get("feature_id"), "subject.feature_id")
    _string(subject.get("target_ref"), "subject.target_ref")
    _integer(subject.get("feature_revision"), "subject.feature_revision")
    _integer(subject.get("candidate_pr_number"), "subject.candidate_pr_number", minimum=1)
    candidate_head = _string(subject.get("candidate_head_sha"), "subject.candidate_head_sha")
    _require(bool(_SHA40.fullmatch(candidate_head)), "subject.candidate_head_sha must be an exact 40-char lowercase SHA")
    _string(subject.get("operation_id"), "subject.operation_id")
    _integer(subject.get("operation_generation"), "subject.operation_generation")

    effect = record.get("effect")
    _require(isinstance(effect, dict), "effect must be an object")
    _string(effect.get("semantic_effect_key"), "effect.semantic_effect_key")
    _string(effect.get("external_dispatch_key"), "effect.external_dispatch_key")
    return scenario


def _validate_policy_authority(record: dict[str, Any], durable: dict[str, Any]) -> None:
    policy = record.get("policy_authority")
    _require(isinstance(policy, dict), "PASS requires exact protected Vertical policy authority")
    required = {
        "repository",
        "state_ref",
        "installation_commit_sha",
        "materialization_commit_sha",
        "receipt_ref",
        "receipt_digest",
        "policy_bundle_digest",
        "post_write_verified_state_ref_sha",
        "post_write_protection",
    }
    _require(set(policy) == required, "policy_authority field set is not exact")
    repository = _string(policy.get("repository"), "policy_authority.repository")
    _require(
        repository.lower() == str(record["subject"]["repository"]).lower(),
        "policy authority repository must match subject repository",
    )
    state_ref = _string(policy.get("state_ref"), "policy_authority.state_ref")
    _require(state_ref == durable.get("state_ref"), "policy authority state ref must match durable Store")
    installation_sha = _string(policy.get("installation_commit_sha"), "policy_authority.installation_commit_sha")
    materialization_sha = _string(policy.get("materialization_commit_sha"), "policy_authority.materialization_commit_sha")
    verified_state_sha = _string(
        policy.get("post_write_verified_state_ref_sha"),
        "policy_authority.post_write_verified_state_ref_sha",
    )
    for value, field in (
        (installation_sha, "policy_authority.installation_commit_sha"),
        (materialization_sha, "policy_authority.materialization_commit_sha"),
        (verified_state_sha, "policy_authority.post_write_verified_state_ref_sha"),
    ):
        _require(bool(_SHA40.fullmatch(value)), f"{field} must be an exact lowercase SHA")
    expected_receipt_ref = (
        f"protected-commit://{repository}@{materialization_sha}/{POLICY_RECEIPT_PATH}"
    )
    _require(
        _string(policy.get("receipt_ref"), "policy_authority.receipt_ref") == expected_receipt_ref,
        "policy authority receipt ref/materialization binding mismatch",
    )
    for field in ("receipt_digest", "policy_bundle_digest"):
        digest = _string(policy.get(field), f"policy_authority.{field}")
        _require(bool(_SHA256.fullmatch(digest)), f"policy_authority.{field} must be an exact sha256")
    protection = policy.get("post_write_protection")
    _require(isinstance(protection, dict), "policy_authority.post_write_protection must be an object")
    _require(
        set(protection) == {"verifier_identity", "verified_at", "policy_digest"},
        "policy_authority.post_write_protection field set is not exact",
    )
    _string(protection.get("verifier_identity"), "policy_authority.post_write_protection.verifier_identity")
    _string(protection.get("verified_at"), "policy_authority.post_write_protection.verified_at")
    protection_digest = _string(
        protection.get("policy_digest"), "policy_authority.post_write_protection.policy_digest"
    )
    _require(bool(_SHA256.fullmatch(protection_digest)), "post-write protection policy digest must be an exact sha256")


def _validate_exact_persist(record: dict[str, Any], scenario: str) -> None:
    persist = record.get("persist")
    _require(isinstance(persist, dict), "PASS requires persist evidence object")
    _require(
        "receipt_id" not in persist,
        "persist.receipt_id is not production authority; exact Event id/result revision are authoritative",
    )
    if scenario not in EXACT_PERSIST_SCENARIOS:
        return
    event_id = _string(persist.get("event_id"), "persist.event_id")
    result_revision = _integer(persist.get("result_revision"), "persist.result_revision", minimum=1)
    expected_result_revision = int(record["subject"]["feature_revision"]) + 1
    _require(
        result_revision == expected_result_revision,
        "persist.result_revision must be the exact next Feature revision",
    )
    if scenario in SUCCESSFUL_CALLBACK_PERSIST_SCENARIOS:
        translated = record.get("translated_event")
        _require(isinstance(translated, dict), "successful callback PASS requires translated_event")
        translated_id = _string(translated.get("id"), "translated_event.id")
        translated_digest = _string(translated.get("digest"), "translated_event.digest")
        _require(bool(_SHA256.fullmatch(translated_digest)), "translated_event.digest must be an exact sha256")
        _require(event_id == translated_id, "Persist Event id must equal translated Feature Event id")


def validate_release_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Validate one PASS/BLOCKED/PENDING real-runtime scenario record."""
    scenario = _validate_common(record)
    status = _string(record.get("status"), "status")

    if status in NON_PASS_STATUSES:
        _require(record.get("release_eligible") is False, f"{status} evidence must not be release eligible")
        remaining = record.get("remaining_release_proof")
        _require(
            isinstance(remaining, list) and len(remaining) > 0,
            f"{status} evidence requires remaining_release_proof",
        )
        for index, item in enumerate(remaining):
            _string(item, f"remaining_release_proof[{index}]")
        return record

    _require(status == "PASS", "status must be PASS, PENDING or BLOCKED")
    _require(record.get("release_eligible") is True, "PASS evidence must explicitly be release eligible")
    _require(record.get("evidence_level") == REAL_RUNTIME_EVIDENCE_LEVEL, "PASS requires real-runtime evidence level")
    _require(record.get("control_ref") == "main", "release PASS must execute against trusted main")

    prerequisites = record.get("prerequisites")
    _require(isinstance(prerequisites, dict), "PASS requires trusted-main production prerequisites")
    _require(
        set(prerequisites) == set(REQUIRED_PRODUCTION_PREREQUISITES),
        "PASS production prerequisite key set is not exact",
    )
    _require(
        all(type(prerequisites[key]) is bool and prerequisites[key] is True for key in REQUIRED_PRODUCTION_PREREQUISITES),
        "PASS requires every trusted-main production prerequisite to be true",
    )

    durable = record.get("durable_store")
    _require(isinstance(durable, dict), "PASS requires protected durable Store evidence")
    durable_repository = _string(durable.get("repository"), "durable_store.repository")
    _require(
        durable_repository.lower() == str(record["subject"]["repository"]).lower(),
        "durable Store repository must match subject repository",
    )
    state_ref = _string(durable.get("state_ref"), "durable_store.state_ref")
    _require(state_ref.startswith("refs/heads/"), "durable_store.state_ref must be an exact branch ref")
    snapshot_sha = _string(durable.get("snapshot_sha"), "durable_store.snapshot_sha")
    _require(bool(_SHA40.fullmatch(snapshot_sha)), "durable_store.snapshot_sha must be an exact lowercase SHA")
    projection_digest = _string(durable.get("operation_projection_digest"), "durable_store.operation_projection_digest")
    _require(bool(_SHA256.fullmatch(projection_digest)), "durable_store.operation_projection_digest must be an exact sha256")
    event_ids = durable.get("operation_event_ids")
    _require(isinstance(event_ids, list) and len(event_ids) > 0, "durable_store.operation_event_ids must be non-empty")
    for index, event_id in enumerate(event_ids):
        _string(event_id, f"durable_store.operation_event_ids[{index}]")

    _validate_policy_authority(record, durable)

    github_run = record.get("github_run")
    _require(isinstance(github_run, dict), "PASS requires GitHub runtime evidence")
    run_id = _integer(github_run.get("id"), "github_run.id", minimum=1)
    run_url = _string(github_run.get("url"), "github_run.url")
    _require(bool(_GITHUB_RUN_URL.fullmatch(run_url)), "github_run.url must identify an exact GitHub Actions run")
    _require(run_url.endswith(f"/actions/runs/{run_id}"), "github_run.url/id mismatch")
    _string(github_run.get("workflow"), "github_run.workflow")
    run_head = _string(github_run.get("head_sha"), "github_run.head_sha")
    _require(bool(_SHA40.fullmatch(run_head)), "github_run.head_sha must be an exact lowercase SHA")

    runtime = record.get("runtime")
    _require(isinstance(runtime, dict), "PASS requires runtime evidence")
    _string(runtime.get("adapter"), "runtime.adapter")
    if scenario in RUNTIME_RECEIPT_SCENARIOS:
        run_attempt = _integer(github_run.get("run_attempt"), "github_run.run_attempt", minimum=1)
        _require(run_attempt == 1, "runtime receipt is authorized only for GitHub Actions run_attempt=1")
        runtime_receipt = _string(runtime.get("receipt_id"), "runtime.receipt_id")
        _require(runtime_receipt == str(run_id), "runtime.receipt_id must equal exact github_run.id")

    callback = record.get("callback", {})
    _require(isinstance(callback, dict), "callback evidence must be an object")
    if scenario in CALLBACK_SCENARIOS:
        _string(callback.get("id"), "callback.id")

    translated = record.get("translated_event", {})
    _require(isinstance(translated, dict), "translated_event evidence must be an object")
    if scenario in SUCCESSFUL_CALLBACK_PERSIST_SCENARIOS:
        _string(translated.get("id"), "translated_event.id")
        translated_digest = _string(translated.get("digest"), "translated_event.digest")
        _require(bool(_SHA256.fullmatch(translated_digest)), "translated_event.digest must be an exact sha256")

    _validate_exact_persist(record, scenario)

    observations = record.get("observations")
    _require(isinstance(observations, dict), "PASS requires observation counters")
    for key in ZERO_SAFETY_COUNTERS:
        _require(observations.get(key) == 0, f"PASS requires {key}=0")

    assertions = record.get("assertions")
    _require(isinstance(assertions, dict), "PASS requires scenario assertion evidence")
    required_assertions = set(SCENARIO_ASSERTIONS[scenario])
    _require(set(assertions) == required_assertions, "PASS scenario assertion key set is not exact")
    _require(all(assertions[key] is True for key in required_assertions), "PASS requires every scenario assertion to be true")

    evidence_uris = record.get("evidence_uris")
    _require(
        isinstance(evidence_uris, list) and len(evidence_uris) >= 2,
        "PASS requires at least two durable evidence URIs",
    )
    for index, uri in enumerate(evidence_uris):
        text = _string(uri, f"evidence_uris[{index}]")
        _require(bool(_GITHUB_EVIDENCE_URI.fullmatch(text)), "PASS evidence URIs must be GitHub durable evidence references")

    remaining = record.get("remaining_release_proof")
    _require(isinstance(remaining, list) and not remaining, "PASS cannot claim remaining release proof")
    return record


def validate_release_matrix(
    records: list[dict[str, Any]], *, require_complete_pass: bool = False
) -> dict[str, dict[str, Any]]:
    _require(isinstance(records, list), "release evidence matrix must be a list")
    by_scenario: dict[str, dict[str, Any]] = {}
    for record in records:
        valid = validate_release_evidence(record)
        scenario = valid["scenario_id"]
        _require(scenario not in by_scenario, f"duplicate release scenario evidence: {scenario}")
        by_scenario[scenario] = valid
    if require_complete_pass:
        _require(
            set(by_scenario) == set(RELEASE_REQUIRED_SCENARIOS),
            "complete release evidence matrix lacks required scenarios",
        )
        _require(
            all(row.get("status") == "PASS" and row.get("release_eligible") is True for row in by_scenario.values()),
            "complete release evidence matrix contains non-PASS scenario",
        )
    return by_scenario
