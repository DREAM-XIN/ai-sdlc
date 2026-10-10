#!/usr/bin/env python3
"""Production authority graph for one fixed v0.3 real-dogfood fixture.

The OpenAI Responses production bundle is the sole Operator-runtime construction
entrypoint.  The real dogfood therefore exercises the accepted write-capable
adapter rather than bypassing it and calling operation.start directly.  The
same returned Operator bundle is then wired to the production gh-aw collector;
no second Store, Persist, callback or dispatch authority is constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import stat
import zipfile
import json
import re
from typing import Any, Callable
from urllib import error, parse, request

from operator_decision_feature_truth import DurableDecisionFeatureTruthGateway, TrustedCandidateSnapshot
from operator_openai_responses import ADAPTER_ID as OPENAI_RESPONSES_ADAPTER_ID
from operator_openai_responses_production import (
    OpenAIResponsesProductionBundle,
    build_openai_responses_production_bundle,
)
from operator_production_runtime import TrustedOperatorRuntimeConfig
from operator_release_feature_event_gateway import build_release_decision_event_gateway
from operator_store_model import canonical_json, digest_json, normalize_repository, operation_events, reservation_path, rebuild_projection, StoreMutation, StoreMutationPlan
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError, validate_worker_result, validate_collected_outputs
from operator_vertical_callback import process_recorded_callback
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_recovery import plan_vertical_callback_record, recover_vertical_callback, _context_payload, _task_binding_matches
from operator_vertical_gh_aw_actions_transport import GitHubActionsVerticalGhAwTransport, GitHubActionsWorkflowTransportConfig
from operator_vertical_gh_aw_attempt_binding import FirstAttemptDigestBoundGhAwResultSource, _FIRST_ATTEMPT_URI_RE
from operator_vertical_gh_aw_collector import MaterializedGhAwOutput, TrustedGhAwResolvedResult, TrustedGhAwRun
from operator_vertical_gh_aw_github_source import (
    GitHubActionsGhAwResultSourceConfig, ProductionGhAwVerticalResultCollector,
    _build_receipts, _current_launch_binding, _validate_run, _GitHubSafeRedirectHandler,
)
from v03_dogfood_fixture_pool import DogfoodSlot
from v03_dogfood_session_policy import DogfoodSessionDecisionPolicyVerifier
from v03_real_runtime_full_composition import DeferredFixtureFeatureTruthGateway

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_DEVELOPER_PR_URI = re.compile(
    r"^docs/features/(?P<feature>[A-Za-z0-9._:-]+)/worker-runs/(?P<dispatch>[A-Za-z0-9._:-]+)/"
    r"developer-pr-(?P<pr>[1-9][0-9]*)-(?P<head>[0-9a-f]{40})"
    r"(?:-binding-(?P<binding>[0-9a-f]{64})"
    r"--first-attempt--key-(?P<key>[A-Za-z0-9._:-]+)"
    r"--run-(?P<run>[1-9][0-9]*)--head-(?P<run_head>[0-9a-f]{40})"
    r"--lease-(?P<lease>[0-9a-f]{64}))?\.json$"
)
DEFAULT_BRANCH = "main"
COLLECTOR_IDENTITY = "ai-sdlc-v03-real-dogfood-collector"
PROVIDER_SCOPE_ID = "v03-real-release-dogfood"
RECOVERY_DEVELOPER_WORKFLOW = "ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml"
RECOVERY_SCHEMA = "ai-sdlc.v03-dogfood-bounded-recovery/v1"
RECOVERY_OPERATION_ID = "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4"
RECOVERY_BASE_PATH = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-bounded-recovery"
RECOVERY_AUTHORIZATION_PATH = RECOVERY_BASE_PATH + "/authorization.json"
RECOVERY_ATTEMPT_PATH = RECOVERY_BASE_PATH + "/create-attempt.json"
RECOVERY_RECEIPT_PATH = RECOVERY_BASE_PATH + "/sealed-receipt.json"
RECOVERY_OBSERVATION_DIGEST = "sha256:a86b7ead37bf96abe9b6e43098b7873b821833c6d93c720ed7409835af18916f"

# Exact, already-armed pre-HTTP failure. These bytes remain immutable forever.
RECOVERY_CONTINUATION_PATH = RECOVERY_BASE_PATH + "/transport-continuation.json"
RECOVERY_CONTINUATION_SCHEMA = "ai-sdlc.v03-dogfood-transport-continuation/v1"
RECOVERY_COLLECTOR_DISPATCH_ID = "vertical-31df3f1ed41b54c58ed4c4030a9f97d9"
ARMED_RECOVERY_SOURCE = "0b5f0a69db9b3cbc4388e8318948f87ba0a92fea"
ARMED_RECOVERY_STORE = "42df69c5bf3dc85fec9b30d318e2651335d74552"
ARMED_RECOVERY_KEY = "recovery-935ad236772d508dfd7e57da6370243dcce4555e"
ARMED_RECOVERY_AUTHORIZATION_BLOB = "d344fca61af21038c929897bc3fd636d4297ee17"
ARMED_RECOVERY_ATTEMPT_BLOB = "db183bc850c8e9add5abad38ced5728325192aba"
ARMED_RECOVERY_SOURCE_BLOBS = {
    ".github/workflows/v03-real-dogfood-scenario.yml": "4beb772c801233ff9907f70ca6cfc9b2fab0bdce",
    "scripts/v03_dogfood_runtime_driver.py": "ad9f7f9a8e8c34132682c4ebd0d09917bfbc5eb7",
    "scripts/v03_dogfood_full_composition.py": "c61dde6e441dcb62e813ef3bafa172e633dbc741",
    "scripts/operator_vertical_gh_aw.py": "8f0181f31d17d7a81c831b19a68bf63c83a72392",
    "scripts/operator_vertical_gh_aw_actions_transport.py": "ec9ea44f81cda1a052cd984cd345f1572c4903e7",
}
ARMED_RECOVERY_NO_HTTP_PROOF = {
    "schema_version": RECOVERY_CONTINUATION_SCHEMA,
    "proof_kind": "exact-source-rejects-identity-before-http",
    "store_commit": ARMED_RECOVERY_STORE,
    "authorization_blob": ARMED_RECOVERY_AUTHORIZATION_BLOB,
    "create_attempt_blob": ARMED_RECOVERY_ATTEMPT_BLOB,
    "source_head_sha": ARMED_RECOVERY_SOURCE,
    "source_blobs": ARMED_RECOVERY_SOURCE_BLOBS,
    "run_id": 37892560162,
    "run_attempt": 1,
    "job_id": 113696529763,
    "workflow_file": "v03-real-dogfood-scenario.yml",
    "workflow_id": 342691463,
    "dispatch_key": ARMED_RECOVERY_KEY,
    "exception": "invalid stable external dispatch key",
}


def _recovery_document_blob(document):
    raw = (canonical_json(document) + "\n").encode("utf-8")
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw).hexdigest()


def validate_armed_recovery_pair(snapshot):
    """Validate historical bytes, never reconstruct or replace their identity."""
    authorization = snapshot.get(RECOVERY_AUTHORIZATION_PATH)
    attempt = snapshot.get(RECOVERY_ATTEMPT_PATH)
    if (
        not isinstance(authorization, dict) or not isinstance(attempt, dict)
        or _recovery_document_blob(authorization) != ARMED_RECOVERY_AUTHORIZATION_BLOB
        or _recovery_document_blob(attempt) != ARMED_RECOVERY_ATTEMPT_BLOB
        or attempt.get("authorization_digest") != "sha256:" + digest_json(authorization)
    ):
        raise VerticalInvariantError("POLICY_DENIED", "exact armed recovery predecessor bytes are required")
    return authorization, attempt


def validate_recovery_continuation(snapshot):
    authorization, attempt = validate_armed_recovery_pair(snapshot)
    continuation = snapshot.get(RECOVERY_CONTINUATION_PATH)
    if not isinstance(continuation, dict):
        raise VerticalInvariantError("POLICY_DENIED", "recovery transport continuation is missing")
    expected = {
        "schema_version": RECOVERY_CONTINUATION_SCHEMA,
        "admission_version": 1,
        "collector_dispatch_id": RECOVERY_COLLECTOR_DISPATCH_ID,
        "status": "ARMED",
        "authorization_digest": "sha256:" + digest_json(authorization),
        "create_attempt_digest": "sha256:" + digest_json(attempt),
        "original_source_head_sha": ARMED_RECOVERY_SOURCE,
        "no_http_proof": ARMED_RECOVERY_NO_HTTP_PROOF,
        "no_http_proof_digest": "sha256:" + digest_json(ARMED_RECOVERY_NO_HTTP_PROOF),
    }
    for key in (
        "operation_id", "operation_generation", "semantic_effect_key", "external_dispatch_key",
        "recovery_dispatch_key", "recovery_dispatch_id", "workflow_file", "feature_id",
        "target_repository", "target_ref", "task_id", "task_identity", "stage", "role",
        "expected_revision", "candidate_pr_number", "candidate_head_sha",
        "provider_fence_digest", "historical_observation_digest", "worker_blobs",
    ):
        expected[key] = authorization[key]
    if (
        any(canonical_json(continuation.get(key)) != canonical_json(value) for key, value in expected.items())
        or type(continuation.get("admission_version")) is not int
        or set(continuation) != set(expected) | {
            "execution_source_head_sha", "execution_trusted_context_digest",
            "execution_materialization_commit_sha", "execution_policy_receipt_digest",
            "execution_policy_bundle_digest", "created_at"
        }
        or not _SHA40.fullmatch(str(continuation.get("execution_source_head_sha") or ""))
        or continuation["execution_source_head_sha"] == ARMED_RECOVERY_SOURCE
        or not re.fullmatch(r"[0-9a-f]{64}", str(continuation.get("execution_trusted_context_digest") or ""))
        or not _SHA40.fullmatch(str(continuation.get("execution_materialization_commit_sha") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(continuation.get("execution_policy_receipt_digest") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(continuation.get("execution_policy_bundle_digest") or ""))
        or not str(continuation.get("created_at") or "")
    ):
        raise VerticalInvariantError("POLICY_DENIED", "recovery transport continuation binding drifted")
    return authorization, attempt, continuation


def recovery_execution_binding(policy_authority):
    # Stable authority deliberately excludes the mutable preflight Store tip.
    # execution_trusted_context_digest remains historical audit metadata after
    # the one claim-to-POST comparison; it cannot authorize collection by itself.
    return {
        "execution_source_head_sha": policy_authority.installation_commit_sha,
        "execution_policy_bundle_digest": policy_authority.bundle_digest,
        "execution_materialization_commit_sha": policy_authority.materialization_commit_sha,
        "execution_policy_receipt_digest": policy_authority.receipt_digest,
    }


def validate_recovery_execution_seal(snapshot, sealed, *, execution_binding):
    if post_handoff_present(snapshot):
        attestation, _, _ = validate_post_handoff_reconciliation(snapshot, consumer_binding=execution_binding)
        execution_binding = attestation["producer_execution_binding"]
    route = recovery_route(snapshot)
    authorization, attempt, continuation = route["authorization"], route["attempt"], route["bridge"]
    artifact = sealed.get("safe_output_artifact_proof") if isinstance(sealed, dict) else None
    uri = str(sealed.get("safe_output_uri") or "") if isinstance(sealed, dict) else ""
    location = _DEVELOPER_PR_URI.fullmatch(uri)
    if (not location or not location.group("binding") or not location.group("lease")
            or any(location.group(key) != value for key, value in {
                "feature": authorization["feature_id"], "dispatch": RECOVERY_COLLECTOR_DISPATCH_ID,
                "key": authorization["recovery_dispatch_key"], "run": str(sealed.get("receipt_id") or ""),
                "run_head": continuation["execution_source_head_sha"],
                "pr": str(sealed.get("output_candidate_pr_number") or ""),
                "head": str(sealed.get("output_candidate_head_sha") or ""),
            }.items())
            or sealed.get("safe_output_digest") != "sha256:" + digest_json({"trusted_uri": uri})):
        raise VerticalInvariantError("POLICY_DENIED", "recovery seal output location differs from its exact route")
    if (
        not isinstance(sealed, dict)
        or not isinstance(artifact, dict)
        or (route["ordinal"] == 1 and (str(sealed.get("receipt_id")) == str(REPLACEMENT_FAILED_RUN)
            or sealed.get("output_candidate_pr_number") == REPLACEMENT_FAILED_PR
            or sealed.get("output_candidate_head_sha") == REPLACEMENT_FAILED_HEAD))
        or artifact.get("schema_version") != "ai-sdlc.v03-recovery-safe-output-artifact/v1"
        or artifact.get("source_head_sha") != continuation["execution_source_head_sha"]
        or str(artifact.get("run_id") or "") != sealed.get("receipt_id")
        or artifact.get("pr_number") != sealed.get("output_candidate_pr_number")
        or artifact.get("repository") != authorization["target_repository"]
        or sealed.get("safe_output_artifact_digest") != "sha256:" + digest_json(artifact)
        or any(continuation.get(k) != v or sealed.get(k) != v for k, v in execution_binding.items())
        or set(execution_binding) != {"execution_source_head_sha", "execution_policy_bundle_digest",
                                      "execution_materialization_commit_sha", "execution_policy_receipt_digest"}
        or sealed.get("collector_dispatch_id") != RECOVERY_COLLECTOR_DISPATCH_ID
        or sealed.get("continuation_digest") != "sha256:" + digest_json(continuation)
        or sealed.get("execution_source_head_sha") != continuation["execution_source_head_sha"]
        or sealed.get("execution_trusted_context_digest") != continuation["execution_trusted_context_digest"]
        or sealed.get("source_head_sha") != authorization["source_head_sha"]
        or sealed.get("installation_commit_sha") != authorization["installation_commit_sha"]
    ):
        raise VerticalInvariantError("POLICY_DENIED", "recovery seal lacks its exact execution-source bridge")
    return continuation


# A separately approved, closed ordinal-one replacement. This is not a retry
# policy and deliberately has no ordinal parameter or successor discovery.
REPLACEMENT_BASE_PATH = RECOVERY_BASE_PATH + "/approved-replacement-1"
REPLACEMENT_AUTHORIZATION_PATH = REPLACEMENT_BASE_PATH + "/authorization.json"
REPLACEMENT_ATTEMPT_PATH = REPLACEMENT_BASE_PATH + "/create-claim.json"
REPLACEMENT_RECEIPT_PATH = REPLACEMENT_BASE_PATH + "/sealed-receipt.json"
REPLACEMENT_PATHS = (REPLACEMENT_AUTHORIZATION_PATH, REPLACEMENT_ATTEMPT_PATH, REPLACEMENT_RECEIPT_PATH)
REPLACEMENT_HISTORY_BLOBS = ["86e43c43941b03e9721844b58479d95db93ce5c8","f26de71400211607359fb60b58e77ddbab7216ed","6741a203a62d31e81f1d619d705bb18feddc0173","1840f7cac50722dad83cb0118e441e475175d859","6f73721b7c8847ddae58e59bbee801d28cd9ef43","a11e8f98ab5a9511fe30cf22f0ba6fa0c41253d9","def317058e40c16a8b35c7390ab5847e678192c5","275a6134e086e95c72f7a8c0aa8940a9f35a67c8","09cbb9201c82d1e69bcce5b0febc8c28f8948fac","f6f793ce9725e618be0b7b25712e5abe44a55b59","ca4581772d9271f0e7d4dece22479a376504e8d5","96c43dcefda8558aa73750eb12a5e0d5af419d82"]
REPLACEMENT_RESERVATION_BLOB = "d9e466a141f86d352b25508294e73ff226b1a314"
REPLACEMENT_PREDECESSOR_STORE = "c78f3e730e1a3ac6cd47cd4e727d090e5f3c5378"
REPLACEMENT_PREDECESSOR_CONTINUATION_BLOB = "e2b2edf5e50eedaacbdaee7ef1b0ae64c5f94580"
REPLACEMENT_FAILED_SOURCE = "5acdaf7ff930d0da2ebf0fc3ecef0cb5a3362780"
REPLACEMENT_FAILED_RUN = 37897902667
REPLACEMENT_FAILED_PR = 574
REPLACEMENT_FAILED_HEAD = "4c9834433e84ef1d367361894f6021e570918daf"
REPLACEMENT_ADMISSION = {
    "schema_version": "ai-sdlc.v03-fixed-replacement-admission/v1",
    "ordinal": 1,
    "operation_id": RECOVERY_OPERATION_ID,
    "operation_generation": 1,
    "scenario": "happy_path",
    "approval": {"issue_comment_id": 6076638838,
                 "body_digest": "sha256:7ee4c0b99698e4266ac7b10f24e5d74ad240cf7d21710feb99c7315e0a4024e0"},
    "predecessor_store_commit": REPLACEMENT_PREDECESSOR_STORE,
    "predecessor_continuation_blob": REPLACEMENT_PREDECESSOR_CONTINUATION_BLOB,
    "failed_run_id": REPLACEMENT_FAILED_RUN,
    "failed_run_attempt": 1,
    "failed_source_head_sha": REPLACEMENT_FAILED_SOURCE,
    "failed_jobs": {"activation": 113713395811, "agent": 113713524603,
                    "detection": 113715146958, "safe_outputs": 113716999124,
                    "conclusion": 113717117034},
    "failed_pr_number": REPLACEMENT_FAILED_PR,
    "failed_pr_head_sha": REPLACEMENT_FAILED_HEAD,
    "failed_artifact_id": 11601119499,
    "failed_artifact_digest": "sha256:18c7d55a82f07ff6edd0f1f12a168f3576aaf02655f71a6c65db9ec7d6f5efdc",
}
REPLACEMENT_ACCOUNTING_URI = "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6076882835"
REPLACEMENT_ACCOUNTING_DIGEST = "sha256:3bf98da2a812b935c153b64f075d9ca5d71a57f30768e4ec919869cc8c1d7211"
REPLACEMENT_ACCOUNTING = {
    "schema_version": "ai-sdlc.v03-replacement-observed-accounting/v1",
    "operation_id": "op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4",
    "operation_generation": 1,
    "scenario": "happy_path",
    "replacement_ordinal": 1,
    "accounting_date_utc": "2026-10-09",
    "scope": "Verified observed owner operational actions during recovery preparation for this Operation on the accounting date. This is not an exhaustive Operation-lifetime or historical-chat census.",
    "human_interventions": 4,
    "events": [
        {
            "kind": "recovery_credential_configuration",
            "observed_time_utc": "02:38",
            "time_precision": "minute"
        },
        {
            "kind": "old_app_key_revocation",
            "observed_time_utc": "02:57",
            "time_precision": "minute"
        },
        {
            "kind": "old_repository_secret_cleanup",
            "observed_time_utc": "06:09:39",
            "time_precision": "second"
        },
        {
            "kind": "one_replacement_execution_budget_approval",
            "observed_time_utc": "07:35:01",
            "time_precision": "second"
        }
    ],
    "excluded_categories": [
        "development_tool_permissions",
        "technical_disclosure_permissions",
        "code_review_and_merge_administration_without_separate_live_authority"
    ],
    "historical_coverage": "non_exhaustive; earlier interactions are not represented as zero",
    "repeated_continue_messages_source": "Independently measured lifecycle-driving continue interactions in the successful scenario runtime observation; this ledger supplies no value and never overrides that measurement or its existing zero requirement.",
    "release_semantics": "Observed accounting only. No Gate waiver, no assertion of zero lifetime interventions, and no assertion of zero historical repeated-continue messages."
}
REPLACEMENT_WORKER_BLOBS = {
    ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.md": "cc538249d0230dd328bd61ca704263c248ce1910",
    ".github/workflows/ai-sdlc-gh-aw-developer-deepseek-v03-local.lock.yml": "6d94f02c8a462c76627919dcc412c57cf92caba7",
    ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.md": "28bb0bb6e72ddbe782209e0da52f7fdbb6c23f35",
    ".github/workflows/ai-sdlc-gh-aw-qa-deepseek-v03-local.lock.yml": "6318bb99352fede75bb2805560fca148fbea6df5",
}
REPLACEMENT_FAILED_OBSERVATION = {
    "run_id": REPLACEMENT_FAILED_RUN, "run_attempt": 1,
    "source_head_sha": REPLACEMENT_FAILED_SOURCE, "status": "completed", "conclusion": "failure",
    "jobs": REPLACEMENT_ADMISSION["failed_jobs"],
    "failure_reasons": ["recorded report_incomplete", "detection timeout without successful structured verdict"],
    "pr_number": REPLACEMENT_FAILED_PR, "pr_head_sha": REPLACEMENT_FAILED_HEAD,
    "artifact_id": REPLACEMENT_ADMISSION["failed_artifact_id"],
    "artifact_digest": REPLACEMENT_ADMISSION["failed_artifact_digest"],
}


def replacement_present(snapshot):
    return any(path in snapshot.files for path in REPLACEMENT_PATHS)


def validate_replacement_predecessor(snapshot):
    original, attempt, continuation = validate_recovery_continuation(snapshot)
    events = operation_events(snapshot, RECOVERY_OPERATION_ID)
    reservation = snapshot.get(reservation_path(original["semantic_effect_key"]))
    if (len(events) < 12 or [_recovery_document_blob(row) for row in events[:12]] != REPLACEMENT_HISTORY_BLOBS
            or not isinstance(reservation, dict) or _recovery_document_blob(reservation) != REPLACEMENT_RESERVATION_BLOB):
        raise VerticalInvariantError("POLICY_DENIED", "replacement frozen journal/reservation predecessor changed")
    if (_recovery_document_blob(continuation) != REPLACEMENT_PREDECESSOR_CONTINUATION_BLOB
            or continuation["execution_source_head_sha"] != REPLACEMENT_FAILED_SOURCE
            or RECOVERY_RECEIPT_PATH in snapshot.files):
        raise VerticalInvariantError("POLICY_DENIED", "replacement predecessor is not the exact unsealed failed recovery")
    return original, attempt, continuation


def replacement_authorization_identity(authorization):
    return {key: value for key, value in authorization.items()
            if key not in {"created_at", "recovery_dispatch_key", "recovery_dispatch_id", "display_title", "task_payload_digest"}}


def validate_replacement_chain(snapshot):
    original, original_attempt, old_continuation = validate_replacement_predecessor(snapshot)
    authorization = snapshot.get(REPLACEMENT_AUTHORIZATION_PATH)
    claim = snapshot.get(REPLACEMENT_ATTEMPT_PATH)
    if not isinstance(authorization, dict) or not isinstance(claim, dict):
        raise VerticalInvariantError("POLICY_DENIED", "fixed replacement authorization/claim is incomplete")
    expected = dict(original)
    for key in ("created_at", "recovery_dispatch_key", "recovery_dispatch_id", "display_title",
                "worker_blobs", "source_head_sha", "installation_commit_sha", "trusted_context_digest"):
        expected.pop(key, None)
    expected.update({
        "replacement_admission": REPLACEMENT_ADMISSION,
        "replacement_admission_digest": "sha256:" + digest_json(REPLACEMENT_ADMISSION),
        "predecessor_authorization_digest": "sha256:" + digest_json(original),
        "predecessor_attempt_digest": "sha256:" + digest_json(original_attempt),
        "predecessor_continuation_digest": "sha256:" + digest_json(old_continuation),
        "worker_blobs": REPLACEMENT_WORKER_BLOBS,
        "collector_dispatch_id": RECOVERY_COLLECTOR_DISPATCH_ID,
        "failed_observation": REPLACEMENT_FAILED_OBSERVATION,
        "observed_accounting": REPLACEMENT_ACCOUNTING,
        "observed_accounting_digest": REPLACEMENT_ACCOUNTING_DIGEST,
        "observed_accounting_uri": REPLACEMENT_ACCOUNTING_URI,
    })
    variable = {"source_head_sha", "installation_commit_sha", "trusted_context_digest",
                "execution_source_head_sha", "execution_materialization_commit_sha",
                "execution_policy_receipt_digest", "execution_policy_bundle_digest",
                "execution_trusted_context_digest", "created_at",
                "recovery_dispatch_key", "recovery_dispatch_id", "display_title",
                "task_payload_digest"}
    if (set(authorization) != set(expected) | variable
            or any(canonical_json(authorization.get(k)) != canonical_json(v) for k, v in expected.items())
            or not REPLACEMENT_WORKER_BLOBS
            or "sha256:" + digest_json(REPLACEMENT_ACCOUNTING) != REPLACEMENT_ACCOUNTING_DIGEST
            or REPLACEMENT_ACCOUNTING["human_interventions"] != len(REPLACEMENT_ACCOUNTING["events"])
            or authorization.get("source_head_sha") != authorization.get("execution_source_head_sha")
            or authorization.get("installation_commit_sha") != authorization.get("execution_source_head_sha")
            or authorization.get("execution_source_head_sha") in {ARMED_RECOVERY_SOURCE, REPLACEMENT_FAILED_SOURCE}
            or authorization.get("trusted_context_digest") != authorization.get("execution_trusted_context_digest")
            or any(not _SHA40.fullmatch(str(authorization.get(k) or "")) for k in
                   ("execution_source_head_sha", "execution_materialization_commit_sha"))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(authorization.get(k) or "")) for k in
                   ("execution_trusted_context_digest", "execution_policy_receipt_digest", "execution_policy_bundle_digest"))
            or not str(authorization.get("created_at") or "")):
        raise VerticalInvariantError("POLICY_DENIED", "fixed replacement authorization scope drifted")
    identity = replacement_authorization_identity(authorization)
    key = "dispatch-" + digest_json(identity)[:40]
    dispatch_id = "replacement-1-" + digest_json(identity)[:32]
    if (authorization["recovery_dispatch_key"] != key
            or authorization["recovery_dispatch_id"] != dispatch_id
            or authorization["display_title"] != "AI-SDLC gh-aw " + key):
        raise VerticalInvariantError("POLICY_DENIED", "replacement deterministic identity drifted")
    dispatch = dict(authorization, external_dispatch_key=key, dispatch_id=dispatch_id,
                    operation_profile=VERTICAL_PROFILE)
    if authorization["task_payload_digest"] != "sha256:" + digest_json(json.loads(
            GhAwVerticalRoleDispatchGateway._task_payload(dispatch))):
        raise VerticalInvariantError("POLICY_DENIED", "replacement exact task payload drifted")
    expected_claim = dict(authorization, authorization_digest="sha256:" + digest_json(authorization),
                          attempt_id="replacement-1-claim-" + digest_json(authorization)[:32], status="ARMED")
    if canonical_json(claim) != canonical_json(expected_claim):
        raise VerticalInvariantError("POLICY_DENIED", "replacement immutable claim drifted")
    return authorization, claim, claim


def recovery_route(snapshot):
    # Any partial/corrupt replacement sidecar locks the route. Never rescue it
    # using an old receipt, and never enumerate arbitrary successor directories.
    if replacement_present(snapshot):
        authorization, claim, bridge = validate_replacement_chain(snapshot)
        return {"ordinal": 1, "authorization_path": REPLACEMENT_AUTHORIZATION_PATH,
                "attempt_path": REPLACEMENT_ATTEMPT_PATH, "receipt_path": REPLACEMENT_RECEIPT_PATH,
                "authorization": authorization, "attempt": claim, "bridge": bridge}
    authorization, attempt, bridge = validate_recovery_continuation(snapshot)
    return {"ordinal": 0, "authorization_path": RECOVERY_AUTHORIZATION_PATH,
            "attempt_path": RECOVERY_ATTEMPT_PATH, "receipt_path": RECOVERY_RECEIPT_PATH,
            "authorization": authorization, "attempt": attempt, "bridge": bridge}


class V03DogfoodCompositionError(RuntimeError):
    pass


HANDOFF_SCHEMA = "ai-sdlc.v03-dogfood-candidate-handoff/v1"

class _ReadOnlyHandoffResult(RuntimeError):
    def __init__(self, result):
        self.result = result


def _commit_handoff_nonempty(executor, planner):
    def guarded(snapshot):
        plan = planner(snapshot)
        if not plan.mutations:
            raise _ReadOnlyHandoffResult(plan.result)
        return plan
    try:
        return executor._commit(guarded).result
    except _ReadOnlyHandoffResult as replay:
        return replay.result




def _handoff_paths(operation_id, callback_id):
    base = f"state/operator/v1/operations/{operation_id}/dogfood-candidate-handoffs/{digest_json({'callback_id': callback_id})}"
    return base + "/intent.json", base + "/applied.json"


def _handoff_prefix(events, document):
    n = document.get("observed_last_sequence")
    if (type(n) is not int or n < 1 or n > len(events)
            or document.get("operation_journal_digest") != digest_json(events[:n])):
        raise V03DogfoodCompositionError("handoff journal predecessor differs")
    return n


def _handoff_binding(snapshot, operation_id, callback_id, fixture_pr):
    events = operation_events(snapshot, operation_id)
    callbacks = [e for e in events if e.get("event_type") == "worker.callback.recorded"
                 and (e.get("payload") or {}).get("callback_id") == callback_id]
    if len(callbacks) != 1 or type(fixture_pr) is not int or fixture_pr < 1:
        raise V03DogfoodCompositionError("handoff requires one callback and exact fixture PR")
    callback = callbacks[0]
    envelope = recover_vertical_callback(snapshot, operation_id=operation_id, callback_id=callback_id)
    context = envelope["trusted_context"]
    payload = validate_worker_result("developer", envelope["worker_payload"])
    if context.get("role") != "developer" or payload.get("status") != "COMPLETED":
        raise V03DogfoodCompositionError("handoff requires completed Developer result")
    generation = callback["operation_generation"]
    if context.get("operation_generation") != generation:
        raise V03DogfoodCompositionError("handoff callback generation differs")
    launches = [e for e in events if e.get("event_type") == "dispatch.launch.authorized"
                and e.get("operation_generation") == generation
                and (e.get("payload") or {}).get("external_dispatch_key") == context.get("external_dispatch_key")]
    lookups = [e for e in events if e.get("event_type") == "dispatch.launch.lookup-recorded"
               and e.get("operation_generation") == generation
               and (e.get("payload") or {}).get("external_dispatch_key") == context.get("external_dispatch_key")
               and (e.get("payload") or {}).get("lookup_state") == "LAUNCHED"]
    if len(launches) != 1 or len(lookups) != 1 or not (launches[0]["sequence"] < lookups[0]["sequence"] < callback["sequence"]):
        raise V03DogfoodCompositionError("handoff lacks original ordered launch and receipt")
    launch = launches[0]["payload"]
    reservation = snapshot.get(reservation_path(str(context.get("semantic_effect_key") or "")))
    if not isinstance(reservation, dict):
        raise V03DogfoodCompositionError("handoff lacks semantic reservation")
    for field in ("dispatch_id", "semantic_effect_key", "external_dispatch_key", "feature_id", "role", "expected_revision", "candidate_head_sha"):
        if launch.get(field) != context.get(field):
            raise V03DogfoodCompositionError("handoff original launch binding differs: " + field)
    for field in ("external_dispatch_key", "target_repository", "feature_id", "expected_revision", "role", "candidate_head_sha"):
        if reservation.get(field) != context.get(field):
            raise V03DogfoodCompositionError("handoff original reservation differs: " + field)
    starts = [event for event in events if event.get("event_type") == "operation.started"]
    if (len(starts) != 1
            or (starts[0].get("payload") or {}).get("target_repository") != context.get("target_repository")
            or (starts[0].get("payload") or {}).get("feature_id") != context.get("feature_id")
            or not _task_binding_matches(str(reservation.get("task_identity") or ""), str(context.get("task_id") or ""))):
        raise V03DogfoodCompositionError("handoff original Operation/task scope differs")
    if launch.get("stage") != context.get("feature_stage") or reservation.get("current_stage") != context.get("feature_stage"):
        raise V03DogfoodCompositionError("handoff original stage differs")
    receipts = envelope["collected_outputs"]
    source_pr, source_head = DogfoodGitHubCandidateProvider._developer_receipt(envelope)
    matches = [_DEVELOPER_PR_URI.fullmatch(str(row.get("trusted_uri") or "")) for row in receipts
               if isinstance(row, dict) and row.get("kind") == "artifact"]
    matches = [match for match in matches if match]
    if len(matches) != 1 or matches[0].group("feature") != context.get("feature_id") or matches[0].group("dispatch") != context.get("dispatch_id"):
        raise V03DogfoodCompositionError("handoff output URI escaped original callback")
    prior = context.get("candidate_head_sha")
    if not _SHA40.fullmatch(str(prior or "")) or prior == source_head:
        raise V03DogfoodCompositionError("handoff requires distinct exact input and output heads")
    return {
        "operation_id": operation_id, "operation_generation": generation,
        "callback_id": callback_id, "callback_sequence": callback["sequence"],
        "callback_event_digest": digest_json(callback), "callback_envelope_digest": digest_json(envelope),
        "receipts_digest": digest_json(receipts), "launch_event_digest": digest_json(launches[0]),
        "launch_lookup_event_digest": digest_json(lookups[0]), "reservation_digest": digest_json(reservation),
        "dispatch_id": context["dispatch_id"], "external_dispatch_key": context["external_dispatch_key"],
        "semantic_effect_key": context["semantic_effect_key"], "target_repository": context["target_repository"],
        "feature_id": context["feature_id"], "target_ref": context["target_ref"],
        "fixture_candidate_pr_number": fixture_pr, "prior_candidate_head_sha": prior,
        "source_candidate_pr_number": source_pr, "source_candidate_head_sha": source_head,
    }


def _validate_handoff_intent(snapshot, intent, *, binding):
    extras = {"schema_version", "fact_kind", "predecessor_store_commit", "observed_last_sequence", "operation_journal_digest"}
    if (not isinstance(intent, dict) or set(intent) != set(binding) | extras
            or any(canonical_json(intent.get(k)) != canonical_json(v) for k, v in binding.items())
            or intent.get("schema_version") != HANDOFF_SCHEMA or intent.get("fact_kind") != "intent"
            or not _SHA40.fullmatch(str(intent.get("predecessor_store_commit") or ""))):
        raise V03DogfoodCompositionError("handoff intent identity differs")
    events = operation_events(snapshot, binding["operation_id"])
    n = _handoff_prefix(events, intent)
    if binding["callback_sequence"] > n or any(e.get("event_type") in {
        "worker.result.validated", "worker.result.rejected", "feature.event.translated"
    } and (e.get("payload") or {}).get("callback_id") == binding["callback_id"] for e in events[:n]):
        raise V03DogfoodCompositionError("handoff intent order differs")
    return intent


def _validate_handoff_applied(snapshot, intent, applied):
    expected = {
        "schema_version": HANDOFF_SCHEMA, "fact_kind": "applied",
        "operation_id": intent["operation_id"], "callback_id": intent["callback_id"],
        "intent_digest": digest_json(intent), "observed_ref_sha": intent["source_candidate_head_sha"],
    }
    extras = {"predecessor_store_commit", "observed_last_sequence", "operation_journal_digest"}
    if (not isinstance(applied, dict) or set(applied) != set(expected) | extras
            or any(canonical_json(applied.get(k)) != canonical_json(v) for k, v in expected.items())
            or not _SHA40.fullmatch(str(applied.get("predecessor_store_commit") or ""))):
        raise V03DogfoodCompositionError("handoff applied identity differs")
    events = operation_events(snapshot, intent["operation_id"])
    n = _handoff_prefix(events, applied)
    if n < intent["observed_last_sequence"] or any(e.get("event_type") in {
        "worker.result.validated", "worker.result.rejected", "feature.event.translated"
    } and (e.get("payload") or {}).get("callback_id") == intent["callback_id"] for e in events[:n]):
        raise V03DogfoodCompositionError("handoff applied order differs")
    return applied


def _plan_handoff_intent(snapshot, *, binding):
    operation_id, callback_id = binding["operation_id"], binding["callback_id"]
    events = operation_events(snapshot, operation_id)
    intent_path, applied_path = _handoff_paths(operation_id, callback_id)
    existing = snapshot.get(intent_path)
    if existing is not None:
        _validate_handoff_intent(snapshot, existing, binding=binding)
        return StoreMutationPlan(snapshot.ref_sha, (), {"intent": existing, "created": False})
    if snapshot.get(applied_path) is not None:
        raise V03DogfoodCompositionError("applied handoff lacks intent")
    # A different callback cannot race another unresolved handoff on this
    # same operation/ref. Re-evaluate from each protected CAS snapshot.
    prefix = f"state/operator/v1/operations/{operation_id}/dogfood-candidate-handoffs/"
    for path, other in snapshot.files.items():
        if not path.startswith(prefix) or not path.endswith("/intent.json") or path == intent_path:
            continue
        if not isinstance(other, dict):
            raise V03DogfoodCompositionError("conflicting handoff intent is malformed")
        other_id = str(other.get("callback_id") or "")
        fact = read_dogfood_handoff(snapshot, operation_id, other_id)
        if (fact is None or path != _handoff_paths(operation_id, other_id)[0]
                or fact["intent"] != other):
            raise V03DogfoodCompositionError("conflicting handoff intent is incomplete")
        if fact["intent"]["target_ref"] != binding["target_ref"]:
            continue
        translated = {str((event.get("payload") or {}).get("feature_event_id") or "")
                      for event in events if event.get("event_type") == "feature.event.translated"
                      and (event.get("payload") or {}).get("callback_id") == other_id}
        confirmed = {str((event.get("payload") or {}).get("feature_event_id") or "")
                     for event in events if event.get("event_type") == "persist.confirmed"}
        if not (translated & confirmed) - {""}:
            raise V03DogfoodCompositionError("another callback handoff remains unresolved for this target")
    projection = rebuild_projection(snapshot, operation_id)
    if (projection.get("generation") != binding["operation_generation"]
            or projection.get("operation_profile") != VERTICAL_PROFILE
            or projection.get("target_repository") != binding["target_repository"]
            or projection.get("feature_id") != binding["feature_id"]
            or projection.get("status") != "RUNNING"):
        raise V03DogfoodCompositionError("handoff intent escaped current executable generation")
    if any(e.get("event_type") in {"worker.result.validated", "worker.result.rejected", "feature.event.translated"}
           and (e.get("payload") or {}).get("callback_id") == callback_id for e in events):
        raise V03DogfoodCompositionError("handoff intent came after callback consumption")
    intent = dict(binding, schema_version=HANDOFF_SCHEMA, fact_kind="intent",
                  predecessor_store_commit=snapshot.ref_sha,
                  observed_last_sequence=len(events), operation_journal_digest=digest_json(events))
    _validate_handoff_intent(snapshot, intent, binding=binding)
    return StoreMutationPlan(snapshot.ref_sha,
        (StoreMutation("create_immutable", intent_path, intent),), {"intent": intent, "created": True})


def _plan_handoff_applied(snapshot, *, intent, observed_ref_sha):
    operation_id, callback_id = intent["operation_id"], intent["callback_id"]
    intent_path, applied_path = _handoff_paths(operation_id, callback_id)
    if snapshot.get(intent_path) != intent or observed_ref_sha != intent["source_candidate_head_sha"]:
        raise V03DogfoodCompositionError("applied handoff escaped intent or exact observed ref")
    _validate_handoff_intent(snapshot, intent, binding=_handoff_binding(
        snapshot, operation_id, callback_id, intent["fixture_candidate_pr_number"]))
    events = operation_events(snapshot, operation_id)
    existing = snapshot.get(applied_path)
    if existing is not None:
        _validate_handoff_applied(snapshot, intent, existing)
        return StoreMutationPlan(snapshot.ref_sha, (), {"applied": existing})
    if any(e.get("event_type") in {"worker.result.validated", "worker.result.rejected", "feature.event.translated"}
           and (e.get("payload") or {}).get("callback_id") == callback_id for e in events):
        raise V03DogfoodCompositionError("handoff application came after callback consumption")
    applied = {
        "schema_version": HANDOFF_SCHEMA, "fact_kind": "applied",
        "operation_id": operation_id, "callback_id": callback_id,
        "intent_digest": digest_json(intent), "observed_ref_sha": observed_ref_sha,
        "predecessor_store_commit": snapshot.ref_sha,
        "observed_last_sequence": len(events), "operation_journal_digest": digest_json(events),
    }
    _validate_handoff_applied(snapshot, intent, applied)
    return StoreMutationPlan(snapshot.ref_sha,
        (StoreMutation("create_immutable", applied_path, applied),), {"applied": applied})


def read_dogfood_handoff(snapshot, operation_id, callback_id, *, require_applied=False):
    intent_path, applied_path = _handoff_paths(operation_id, callback_id)
    intent, applied = snapshot.get(intent_path), snapshot.get(applied_path)
    if intent is None:
        if applied is not None or require_applied:
            raise V03DogfoodCompositionError("handoff intent missing")
        return None
    if not isinstance(intent, dict):
        raise V03DogfoodCompositionError("handoff intent malformed")
    binding = _handoff_binding(snapshot, operation_id, callback_id, intent.get("fixture_candidate_pr_number"))
    _validate_handoff_intent(snapshot, intent, binding=binding)
    if applied is not None:
        _validate_handoff_applied(snapshot, intent, applied)
    elif require_applied:
        raise V03DogfoodCompositionError("handoff applied proof missing")
    return {"intent": intent, "applied": applied}



# Fixed post-handoff reconsideration of one successful execution. This never
# authorizes an execution, a candidate-ref write, or a second reconciliation.
POST_HANDOFF_ADMISSION = {
    "uri": "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6077801329",
    "body_digest": "sha256:9bc48d9ea996dbe503b46de35279380d94ac6470c54b8234188a2012d87fa9d0",
}
POST_HANDOFF_PATH = REPLACEMENT_BASE_PATH + "/post-handoff-reconciliation-1.json"
POST_HANDOFF_STORE = "d3ccde10fb3f30e29d27e51bcc82fb9ce49c93f9"
POST_HANDOFF_SOURCE = "6e75792b8e441167cfaadab2d13667a2d80721b8"
POST_HANDOFF_RUN = 37905505035
POST_HANDOFF_PR = 577
POST_HANDOFF_HEAD = "a7b208a49668fb9ae16de908a52418106c616819"
POST_HANDOFF_CALLBACK = "gh-aw-recovery-callback-1e23a4b1837d62c529fb4002"
POST_HANDOFF_FACT_BASE = (
    f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-candidate-handoffs/"
    "1f546bd51bfea6214480ecea7789f74bb7c26356a2362357f4bd22aa0367b7be"
)
POST_HANDOFF_DOCUMENT_BLOBS = {
    REPLACEMENT_AUTHORIZATION_PATH: "e68d45041c47083c2da5521aa325fcf58bef256b",
    REPLACEMENT_ATTEMPT_PATH: "706888dddd1acc157cdbeb5b54ace6539125e16b",
    REPLACEMENT_RECEIPT_PATH: "7558b2b3ffe08bb255d3c287fffa49f5fc988f0e",
    POST_HANDOFF_FACT_BASE + "/intent.json": "da06cd8849fff194e3cdbeb1df54d3efa69f850e",
    POST_HANDOFF_FACT_BASE + "/applied.json": "eb63e61ff00ae20bbe465d205c98924056e77827",
}
POST_HANDOFF_HISTORY_BLOBS = REPLACEMENT_HISTORY_BLOBS + [
    "d773017efeec4ceccd65aaf28c1574c5aa6c9f69",
    "93c3c64ea155b65bdbeaf78eed0067590aed9ede",
    "5a6969ba938f6153705d999065f786d4bb3159ec",
]


def post_handoff_present(snapshot):
    return POST_HANDOFF_PATH in snapshot.files


def validate_post_handoff_predecessor(snapshot):
    """Keep the entire rejected observation and applied handoff immutable."""
    validate_replacement_chain(snapshot)
    events = operation_events(snapshot, RECOVERY_OPERATION_ID)
    if (len(events) < 15
            or [_recovery_document_blob(row) for row in events[:15]] != POST_HANDOFF_HISTORY_BLOBS
            or any(not isinstance(snapshot.get(path), dict)
                   or _recovery_document_blob(snapshot.get(path)) != blob
                   for path, blob in POST_HANDOFF_DOCUMENT_BLOBS.items())):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff frozen predecessor changed")
    sealed = snapshot.get(REPLACEMENT_RECEIPT_PATH)
    if (sealed["receipt_id"] != str(POST_HANDOFF_RUN)
            or sealed["execution_source_head_sha"] != POST_HANDOFF_SOURCE
            or sealed["output_candidate_pr_number"] != POST_HANDOFF_PR
            or sealed["output_candidate_head_sha"] != POST_HANDOFF_HEAD):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff producer identity differs")
    return sealed, events


def post_handoff_observation_id(attestation):
    identity = {key: value for key, value in attestation.items()
                if key not in {"created_at", "observation_callback_id"}}
    return "gh-aw-post-handoff-observation-" + digest_json(identity)[:24]



# One separately admitted replacement of the exact pre-model Reviewer failure.
REVIEWER_REPLACEMENT_ADMISSION = {
    "uri": "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6079239160",
    "body_digest": "sha256:27f1f095c9644c009bc650b55aaccc7498a8cdebad7dcc21354e6bdf3622613c",
}
REVIEWER_PREDECESSOR_STORE = "ac95a1cefdeb33dc836ae969168cd992c482401f"
REVIEWER_PREDECESSOR_SOURCE = "bd9228219310a8202bf47311e6adb8ea36d598bf"
REVIEWER_FAILED_RUN = 37917962742
REVIEWER_OLD_WORKFLOW = "ai-sdlc-gh-aw-reviewer-deepseek.lock.yml"
REVIEWER_NEW_WORKFLOW = "ai-sdlc-gh-aw-reviewer-deepseek-v03-release-local.lock.yml"
REVIEWER_OLD_KEY = "dispatch-3d9202e579f855d0d36bdaaf1872dec1a2b84a28"
REVIEWER_LOGICAL_DISPATCH = "vertical-e7823936c0cdccd56dc1d8f5e7a9d2e1"
REVIEWER_SEMANTIC_KEY = "ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25"
REVIEWER_CANDIDATE = "41e0df7089c5907b00bbaeac5dd2be71d4f02d4b"
REVIEWER_TASK = "vertical:code-review:" + REVIEWER_CANDIDATE
REVIEWER_REPLACEMENT_BASE = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-reviewer-pre-model-replacement-1"
REVIEWER_AUTH_PATH = REVIEWER_REPLACEMENT_BASE + "/authorization.json"
REVIEWER_CLAIM_PATH = REVIEWER_REPLACEMENT_BASE + "/create-claim.json"
REVIEWER_SEAL_PATH = REVIEWER_REPLACEMENT_BASE + "/sealed-result.json"
REVIEWER_PATHS = (REVIEWER_AUTH_PATH, REVIEWER_CLAIM_PATH, REVIEWER_SEAL_PATH)
REVIEWER_HISTORY_BLOBS = [
    "86e43c43941b03e9721844b58479d95db93ce5c8",
    "f26de71400211607359fb60b58e77ddbab7216ed",
    "6741a203a62d31e81f1d619d705bb18feddc0173",
    "1840f7cac50722dad83cb0118e441e475175d859",
    "6f73721b7c8847ddae58e59bbee801d28cd9ef43",
    "a11e8f98ab5a9511fe30cf22f0ba6fa0c41253d9",
    "def317058e40c16a8b35c7390ab5847e678192c5",
    "275a6134e086e95c72f7a8c0aa8940a9f35a67c8",
    "09cbb9201c82d1e69bcce5b0febc8c28f8948fac",
    "f6f793ce9725e618be0b7b25712e5abe44a55b59",
    "ca4581772d9271f0e7d4dece22479a376504e8d5",
    "96c43dcefda8558aa73750eb12a5e0d5af419d82",
    "d773017efeec4ceccd65aaf28c1574c5aa6c9f69",
    "93c3c64ea155b65bdbeaf78eed0067590aed9ede",
    "5a6969ba938f6153705d999065f786d4bb3159ec",
    "069beb76d507b71109a1219722e03bf9bd4179f2",
    "54c9ddf16627d495c4ef06f80016fda173e12475",
    "b277131c21142e815de9825c9c7f02f5283bf19a",
    "03c6cb5628f5896c3dedcfddeb6abb0257b0886d",
    "e8ea390b14f84173a2f2ba65003fb7fcb21d1faf",
    "3f1dac41fef483899b0af3d1de6634e502497986",
    "19c681ab77d90849e6b67aa53b8ccc3e271c16e6",
    "09aedd1fa3bd0fb8b71ef6fd75390e2edc520db4",
    "4dcf56f2ad6a941ccf5b6204cbb418d86f9be0b6",
    "1b807744b90d39564e05ce2f378e28fba6ece5b1",
    "1bd7b63e0b30882f1c304d1be16dad17f3b1dfbe",
    "2400f03e80d353ba989b66a8609f780e955d5dff",
    "792494e66a1d31529993832e3cc8b0940ab58244",
    "905d1b7f02ebb4a4b59342de885a5120ca2ff089",
    "20ced53e567b946554fdaaa166f64167c4fe78c8"
]
REVIEWER_DOCUMENT_BLOBS = {
    "state/operator/v1/claims/dispatch/dc-3006ff66a16631b94316e4dac3d4711a4b4d2159.json": "1b93a7c81ae8be4bd4aef80a6c1c7c0641f40cc8",
    "state/operator/v1/effect-lineages/members/lin-5aec6488d04a953a1d4f1619b4fb2ec503f53a69f98750e3/ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25.json": "1dfa9b783546f4a356cdf11a5a0c42513d9cda63",
    "state/operator/v1/operations/op-3f7aa9b6290c8d1d90868dc079ce1af30cbaa7f4/dogfood-bounded-recovery/approved-replacement-1/post-handoff-reconciliation-1.json": "5178d7697c9b140c64c4dc2cf3fca94bf223c1ab",
    "state/operator/v1/reservations/external/ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25.json": "63fbc84909ecc9395c3113da1edb5a91f54c48bb",
    "state/operator/v1/reservations/external/ca592b5e204090e093d46d351b4f2bd369b35d289c541cd066b7007e4515ed25/external-create-attempt.json": "2b85c38cf3fa6fa1043205cc7b791828e71ec420"
}
REVIEWER_FAILURE_JOBS = {
    "activation": 113778697506, "agent": 113778789435, "detection": 113778892780,
    "safe_outputs": 113779083838, "conclusion": 113779278658,
}
REVIEWER_FAILURE_WORKER_BLOBS = {
    ".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek.md": "df202f732df6914975d200def831b15c6100ca89",
    ".github/workflows/ai-sdlc-gh-aw-reviewer-deepseek.lock.yml": "fe034e28b40c325dcca2e8ed639d3885e0910fb2",
}



REVIEWER_POST_MODEL_ADMISSION = {
    "uri": "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6092341042",
    "body_digest": "sha256:aaa968fdd6d14ce06715636dfaed6e6dcdcc3c723f75c281da63df325a3b92ef",
}
REVIEWER_POST_MODEL_STORE = "118c421312ce10ce5747595470504cece2ea44e3"
REVIEWER_POST_MODEL_SOURCE = "193d96474529556cc0d805bb9be2b0a96909777b"
REVIEWER_POST_MODEL_FAILED_RUN = 37927328438
REVIEWER_POST_MODEL_FAILED_KEY = "dispatch-d653abeb44f20430dc9a5600150717ff76dd57bb"
REVIEWER_BOUNDED_WORKFLOW = "ai-sdlc-gh-aw-reviewer-deepseek-v03-bounded-local.lock.yml"
REVIEWER_POST_MODEL_BASE = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-reviewer-post-model-replacement-2"
REVIEWER_POST_MODEL_AUTH_PATH = REVIEWER_POST_MODEL_BASE + "/authorization.json"
REVIEWER_POST_MODEL_CLAIM_PATH = REVIEWER_POST_MODEL_BASE + "/create-claim.json"
REVIEWER_POST_MODEL_SEAL_PATH = REVIEWER_POST_MODEL_BASE + "/sealed-result.json"
REVIEWER_POST_MODEL_PATHS = (REVIEWER_POST_MODEL_AUTH_PATH, REVIEWER_POST_MODEL_CLAIM_PATH, REVIEWER_POST_MODEL_SEAL_PATH)
REVIEWER_POST_MODEL_DOCUMENT_BLOBS = {
    REVIEWER_AUTH_PATH: "9887312d2053849c64986be4bb661938fa08a7e6",
    REVIEWER_CLAIM_PATH: "f8092c2cfea63b9da61ebc21a5e967b2e4fd6ad3",
}
REVIEWER_POST_MODEL_FAILURE_JOBS = {
    "activation":113809299117,"agent":113809496837,"detection":113810489745,
    "safe_outputs":113812650050,"conclusion":113812648935,
}

def reviewer_post_model_present(snapshot):
    return any(path in snapshot.files for path in REVIEWER_POST_MODEL_PATHS)

def reviewer_route_paths(snapshot):
    if reviewer_structured_present(snapshot):
        return {"authorization_path": REVIEWER_STRUCTURED_AUTH_PATH, "claim_path": REVIEWER_STRUCTURED_CLAIM_PATH,
                "seal_path": REVIEWER_STRUCTURED_SEAL_PATH, "workflow": STRUCTURED_GATE_WORKFLOWS["reviewer"], "ordinal": 3}
    if reviewer_post_model_present(snapshot):
        return {"authorization_path":REVIEWER_POST_MODEL_AUTH_PATH,"claim_path":REVIEWER_POST_MODEL_CLAIM_PATH,
                "seal_path":REVIEWER_POST_MODEL_SEAL_PATH,"workflow":REVIEWER_BOUNDED_WORKFLOW,"ordinal":2}
    return {"authorization_path":REVIEWER_AUTH_PATH,"claim_path":REVIEWER_CLAIM_PATH,
            "seal_path":REVIEWER_SEAL_PATH,"workflow":REVIEWER_NEW_WORKFLOW,"ordinal":1}

def validate_reviewer_post_model_predecessor(snapshot, *, fresh=False):
    old, events = validate_reviewer_predecessor(snapshot, fresh=fresh)
    if (any(_recovery_document_blob(snapshot.get(path)) != sha
            for path,sha in REVIEWER_POST_MODEL_DOCUMENT_BLOBS.items())
            or REVIEWER_SEAL_PATH in snapshot.files):
        raise VerticalInvariantError("POLICY_DENIED","fixed post-model Reviewer predecessor differs")
    prior = snapshot.get(REVIEWER_AUTH_PATH)
    if (prior["physical_key"] != REVIEWER_POST_MODEL_FAILED_KEY
            or prior["consumer_execution_binding"]["execution_source_head_sha"] != REVIEWER_POST_MODEL_SOURCE):
        raise VerticalInvariantError("POLICY_DENIED","post-model Reviewer historical producer differs")
    return prior, events

def _reviewer_post_model_identity(snapshot, *, consumer_binding, worker_blobs, failure_proof):
    prior, _ = validate_reviewer_post_model_predecessor(snapshot)
    return {
        "schema_version":"ai-sdlc.v03-reviewer-post-model-replacement/v1","ordinal":2,
        "admission":REVIEWER_POST_MODEL_ADMISSION,"operation_id":RECOVERY_OPERATION_ID,
        "operation_generation":1,"predecessor_store_commit":REVIEWER_POST_MODEL_STORE,
        "predecessor_event_blobs":REVIEWER_HISTORY_BLOBS,
        "predecessor_document_blobs":{**REVIEWER_DOCUMENT_BLOBS,**REVIEWER_POST_MODEL_DOCUMENT_BLOBS},
        "logical_key":REVIEWER_OLD_KEY,"logical_dispatch_id":REVIEWER_LOGICAL_DISPATCH,
        "semantic_effect_key":REVIEWER_SEMANTIC_KEY,"task_id":REVIEWER_TASK,
        "candidate_head_sha":REVIEWER_CANDIDATE,"candidate_pr_number":552,
        "expected_revision":3,"role":"reviewer","stage":"code-review",
        "workflow_file":REVIEWER_BOUNDED_WORKFLOW,"failed_run_id":REVIEWER_POST_MODEL_FAILED_RUN,
        "failed_physical_key":REVIEWER_POST_MODEL_FAILED_KEY,
        "prior_consumer_execution_binding":prior["consumer_execution_binding"],
        "consumer_execution_binding":consumer_binding,"worker_blobs":worker_blobs,
        "post_model_failure_proof":failure_proof,
    }

def validate_reviewer_post_model_authorization(snapshot, *, consumer_binding=None):
    _, events = validate_reviewer_post_model_predecessor(snapshot)
    auth,claim = snapshot.get(REVIEWER_POST_MODEL_AUTH_PATH),snapshot.get(REVIEWER_POST_MODEL_CLAIM_PATH)
    if not isinstance(auth,dict) or not isinstance(claim,dict):
        raise VerticalInvariantError("POLICY_DENIED","post-model Reviewer authorization/claim incomplete")
    expected = _reviewer_complete_authorization(_reviewer_post_model_identity(snapshot,
        consumer_binding=auth.get("consumer_execution_binding"),worker_blobs=auth.get("worker_blobs"),
        failure_proof=auth.get("post_model_failure_proof")))
    binding=auth.get("consumer_execution_binding")
    from v03_dogfood_live_gate import CURRENT_DOGFOOD_BLOBS
    if (canonical_json(auth)!=canonical_json(expected) or not isinstance(binding,dict)
            or set(binding)!=set(recovery_execution_binding_fields())
            or not _SHA40.fullmatch(str(binding.get("execution_source_head_sha") or ""))
            or binding["execution_source_head_sha"] in {REVIEWER_POST_MODEL_SOURCE,REVIEWER_PREDECESSOR_SOURCE,POST_HANDOFF_SOURCE}
            or not _SHA40.fullmatch(str(binding.get("execution_materialization_commit_sha") or ""))
            or any(not re.fullmatch(r"[0-9a-f]{64}",str(binding.get(k) or ""))
                   for k in ("execution_policy_bundle_digest","execution_policy_receipt_digest"))
            or (consumer_binding is not None and canonical_json(binding)!=canonical_json(consumer_binding))
            or canonical_json(claim)!=canonical_json({"schema_version":auth["schema_version"],"ordinal":2,
                "authorization_digest":"sha256:"+digest_json(auth),"physical_key":auth["physical_key"],"create_consumed":True})
            or auth["physical_key"] in {REVIEWER_OLD_KEY,REVIEWER_POST_MODEL_FAILED_KEY}
            or auth["worker_blobs"]!={".github/workflows/"+name:sha for name,sha in CURRENT_DOGFOOD_BLOBS.items()}):
        raise VerticalInvariantError("POLICY_DENIED","post-model Reviewer authority/source differs")
    proof=auth["post_model_failure_proof"]
    fixed={"schema_version":"ai-sdlc.v03-reviewer-post-model-failure/v1",
        "run_id":REVIEWER_POST_MODEL_FAILED_RUN,"run_attempt":1,"source_head_sha":REVIEWER_POST_MODEL_SOURCE,
        "jobs":REVIEWER_POST_MODEL_FAILURE_JOBS,"model_executed":True,"detector_timed_out":True,
        "semantic_safety_pass":False,"safe_outputs_processed":False,"failure_issue_number":580}
    if (not isinstance(proof,dict) or set(proof)!=set(fixed)|{"observation_digest"}
            or any(canonical_json(proof.get(k))!=canonical_json(v) for k,v in fixed.items())
            or not re.fullmatch(r"sha256:[0-9a-f]{64}",str(proof.get("observation_digest") or ""))):
        raise VerticalInvariantError("POLICY_DENIED","post-model Reviewer failure proof differs")
    if any(e["event_type"]=="worker.callback.recorded"
            and e["payload"].get("external_dispatch_key") in {
                REVIEWER_POST_MODEL_FAILED_KEY,auth["physical_key"]} for e in events[30:]):
        raise VerticalInvariantError("POLICY_DENIED","post-model Reviewer callback used a physical execution key")
    _validate_reviewer_callback_relation(snapshot,auth,events,REVIEWER_POST_MODEL_SEAL_PATH)
    return auth,claim

def validate_reviewer_authorization(snapshot, *, consumer_binding=None):
    if reviewer_structured_present(snapshot):
        return validate_reviewer_structured_authorization(snapshot, consumer_binding=consumer_binding)
    if reviewer_post_model_present(snapshot):
        return validate_reviewer_post_model_authorization(snapshot,consumer_binding=consumer_binding)
    return _validate_pre_model_reviewer_authorization(snapshot,consumer_binding=consumer_binding)


def reviewer_replacement_present(snapshot):
    return any(path in snapshot.files for path in (*REVIEWER_PATHS,*REVIEWER_POST_MODEL_PATHS,*REVIEWER_STRUCTURED_PATHS))


def validate_reviewer_predecessor(snapshot, *, fresh=False):
    events = operation_events(snapshot, RECOVERY_OPERATION_ID)
    if (len(events) < 30 or (fresh and len(events) != 30)
            or [_recovery_document_blob(e) for e in events[:30]] != REVIEWER_HISTORY_BLOBS
            or any(_recovery_document_blob(snapshot.get(path)) != blob
                   for path, blob in REVIEWER_DOCUMENT_BLOBS.items())):
        raise VerticalInvariantError("POLICY_DENIED", "fixed Reviewer predecessor history/documents differ")
    old = snapshot.get(POST_HANDOFF_PATH)
    if old["consumer_execution_binding"]["execution_source_head_sha"] != REVIEWER_PREDECESSOR_SOURCE:
        raise VerticalInvariantError("POLICY_DENIED", "fixed Reviewer predecessor controller differs")
    if fresh:
        from operator_vertical_store import vertical_projection
        projection = vertical_projection(snapshot, RECOVERY_OPERATION_ID)
        if (projection["generation"] != 1 or projection["status"] != "WAITING_EXTERNAL"
                or projection["expected_feature_revision"] != 3):
            raise VerticalInvariantError("POLICY_DENIED", "fixed Reviewer predecessor is no longer pending")
    return old, events


def reviewer_dispatch(authorization, *, physical=True):
    return {
        "operation_id": RECOVERY_OPERATION_ID, "operation_generation": 1,
        "operation_profile": VERTICAL_PROFILE, "semantic_effect_key": REVIEWER_SEMANTIC_KEY,
        "external_dispatch_key": authorization["physical_key"] if physical else REVIEWER_OLD_KEY,
        "dispatch_id": authorization["physical_dispatch_id"] if physical else REVIEWER_LOGICAL_DISPATCH,
        "target_repository": "dream-xin/ai-sdlc", "target_ref": "dogfood/v0.3-happy-path-0001",
        "feature_id": "F-OPERATOR-V03-DOGFOOD-HAPPY-0001", "expected_revision": 3,
        "feature_stage": "code-review", "task_id": REVIEWER_TASK, "task_identity": REVIEWER_TASK,
        "role": "reviewer", "candidate_pr_number": 552, "candidate_head_sha": REVIEWER_CANDIDATE,
    }


def _reviewer_authority_identity(snapshot, *, consumer_binding, worker_blobs, failure_proof):
    old, _ = validate_reviewer_predecessor(snapshot)
    return {
        "schema_version": "ai-sdlc.v03-reviewer-pre-model-replacement/v1", "ordinal": 1,
        "admission": REVIEWER_REPLACEMENT_ADMISSION, "operation_id": RECOVERY_OPERATION_ID,
        "operation_generation": 1, "predecessor_store_commit": REVIEWER_PREDECESSOR_STORE,
        "predecessor_event_blobs": REVIEWER_HISTORY_BLOBS,
        "predecessor_document_blobs": REVIEWER_DOCUMENT_BLOBS,
        "logical_key": REVIEWER_OLD_KEY, "logical_dispatch_id": REVIEWER_LOGICAL_DISPATCH,
        "semantic_effect_key": REVIEWER_SEMANTIC_KEY, "task_id": REVIEWER_TASK,
        "candidate_head_sha": REVIEWER_CANDIDATE, "candidate_pr_number": 552,
        "expected_revision": 3, "role": "reviewer", "stage": "code-review",
        "workflow_file": REVIEWER_NEW_WORKFLOW, "failed_run_id": REVIEWER_FAILED_RUN,
        "prior_consumer_execution_binding": old["consumer_execution_binding"],
        "consumer_execution_binding": consumer_binding, "worker_blobs": worker_blobs,
        "pre_model_failure_proof": failure_proof,
    }


def _reviewer_complete_authorization(identity):
    auth = dict(identity)
    auth["physical_key"] = "dispatch-" + digest_json(identity)[:40]
    auth["physical_dispatch_id"] = "vertical-" + digest_json({
        "operation_id": RECOVERY_OPERATION_ID, "generation": 1,
        "external_dispatch_key": auth["physical_key"],
    })[:32]
    auth["payload_digest"] = "sha256:" + digest_json(
        json.loads(GhAwVerticalRoleDispatchGateway._task_payload(reviewer_dispatch(auth))))
    return auth


def _validate_pre_model_reviewer_authorization(snapshot, *, consumer_binding=None):
    old, events = validate_reviewer_predecessor(snapshot)
    auth = snapshot.get(REVIEWER_AUTH_PATH)
    claim = snapshot.get(REVIEWER_CLAIM_PATH)
    if not isinstance(auth, dict) or not isinstance(claim, dict):
        raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement authorization/claim incomplete")
    identity = _reviewer_authority_identity(
        snapshot, consumer_binding=auth.get("consumer_execution_binding"),
        worker_blobs=auth.get("worker_blobs"), failure_proof=auth.get("pre_model_failure_proof"))
    expected = _reviewer_complete_authorization(identity)
    binding = auth.get("consumer_execution_binding")
    if (auth != expected or not isinstance(binding, dict)
            or set(binding) != set(recovery_execution_binding_fields())
            or not _SHA40.fullmatch(str(binding.get("execution_source_head_sha") or ""))
            or binding["execution_source_head_sha"] in {REVIEWER_PREDECESSOR_SOURCE, POST_HANDOFF_SOURCE}
            or not _SHA40.fullmatch(str(binding.get("execution_materialization_commit_sha") or ""))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(k) or ""))
                   for k in ("execution_policy_bundle_digest", "execution_policy_receipt_digest"))
            or (consumer_binding is not None and binding != consumer_binding)
            or claim != {"schema_version": auth["schema_version"], "ordinal": 1,
                         "authorization_digest": "sha256:" + digest_json(auth),
                         "physical_key": auth["physical_key"], "create_consumed": True}):
        raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement authority differs")
    proof = auth["pre_model_failure_proof"]
    if (not isinstance(proof, dict) or proof.get("run_id") != REVIEWER_FAILED_RUN
            or proof.get("source_head_sha") != REVIEWER_PREDECESSOR_SOURCE
            or proof.get("run_attempt") != 1 or proof.get("jobs") != REVIEWER_FAILURE_JOBS
            or proof.get("model_executed") is not False or proof.get("safe_outputs_processed") is not False
            or proof.get("semantic_safety_pass") is not False
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(proof.get("observation_digest") or ""))):
        raise VerticalInvariantError("POLICY_DENIED", "Reviewer pre-model failure proof differs")
    blobs = auth["worker_blobs"]
    required = {".github/workflows/" + x for x in (
        REVIEWER_NEW_WORKFLOW, REVIEWER_NEW_WORKFLOW.replace(".lock.yml", ".md"),
        "ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml",
        "ai-sdlc-gh-aw-qa-deepseek-v03-release-local.md",
        RECOVERY_DEVELOPER_WORKFLOW, RECOVERY_DEVELOPER_WORKFLOW.replace(".lock.yml", ".md"))}
    from v03_dogfood_live_gate import HISTORICAL_RELEASE_DOGFOOD_BLOBS
    if (blobs != {".github/workflows/" + name: blob for name, blob in HISTORICAL_RELEASE_DOGFOOD_BLOBS.items()}
            or not isinstance(blobs, dict) or set(blobs) != required
            or any(not _SHA40.fullmatch(str(v)) for v in blobs.values())):
        raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement selected Worker pins differ")
    _validate_reviewer_callback_relation(snapshot,auth,events,REVIEWER_SEAL_PATH)
    return auth, claim


def _validate_reviewer_callback_relation(snapshot, auth, events, seal_path):
    binding=auth["consumer_execution_binding"]
    callbacks = [e for e in events[30:] if e["event_type"] == "worker.callback.recorded"
                 and e["payload"].get("external_dispatch_key") == REVIEWER_OLD_KEY]
    if len(callbacks) > 1:
        raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement has conflicting observations")
    if callbacks:
        sealed = snapshot.get(seal_path)
        payload = callbacks[0]["payload"]
        envelope = payload.get("trusted_callback_envelope")
        context = envelope.get("trusted_context") if isinstance(envelope, dict) else None
        if not isinstance(sealed, dict) or not isinstance(context, dict):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer callback predates its sealed replacement")
        expected_id = "gh-aw-callback-" + digest_json({
            "operation_id": RECOVERY_OPERATION_ID, "generation": 1, "external_dispatch_key": REVIEWER_OLD_KEY,
            "runtime_receipt_identity": str(sealed.get("run_id")), "run_id": sealed.get("run_id")})[:24]
        expected_context = {"operation_id": RECOVERY_OPERATION_ID, "operation_generation": 1,
            "external_dispatch_key": REVIEWER_OLD_KEY, "dispatch_id": REVIEWER_LOGICAL_DISPATCH,
            "runtime_receipt_identity": str(sealed.get("run_id")), "role": "reviewer", "task_id": REVIEWER_TASK,
            "candidate_pr_number": 552, "candidate_head_sha": REVIEWER_CANDIDATE, "expected_revision": 3,
            "worker_identity": f"gh-aw:{auth['workflow_file']}@{binding['execution_source_head_sha']}"}
        if (payload.get("callback_id") != expected_id
                or any(context.get(k) != v for k,v in expected_context.items())
                or digest_json(envelope) != payload.get("trusted_callback_envelope_digest")
                or "sha256:" + digest_json(envelope.get("worker_payload")) != sealed.get("role_payload_digest")
                or len(envelope.get("collected_outputs") or []) != 1
                or any(envelope["collected_outputs"][0].get(k) != v for k,v in {
                    "trusted_uri": sealed.get("trusted_uri"), "sha256": sealed.get("content_sha256"),
                    "size_bytes": sealed.get("content_size")}.items())
                or any(e["event_type"] == "worker.result.rejected" and e["payload"].get("callback_id") == expected_id
                       for e in events[30:])):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer callback conflicts with sealed physical execution")

def reviewer_replacement_route(snapshot, *, consumer_binding=None, require_seal=True):
    descriptor = reviewer_route_paths(snapshot)
    auth, claim = validate_reviewer_authorization(snapshot, consumer_binding=consumer_binding)
    if descriptor["ordinal"] == 3 and REVIEWER_STRUCTURED_TERMINAL_PATH in snapshot.files:
        reviewer_structured_terminal(snapshot, consumer_binding=consumer_binding)
        raise VerticalInvariantError("NEEDS_USER", "corrected Reviewer recommendation requires owner action")
    sealed = snapshot.get(descriptor["seal_path"])
    if require_seal or descriptor["seal_path"] in snapshot.files:
        if (not isinstance(sealed, dict) or sealed.get("authorization_digest") != "sha256:" + digest_json(auth)
                or sealed.get("claim_digest") != "sha256:" + digest_json(claim)
                or sealed.get("physical_key") != auth["physical_key"]
                or sealed.get("logical_key") != REVIEWER_OLD_KEY
                or type(sealed.get("run_id")) is not int or sealed["run_id"] <= 0
                or sealed["run_id"] in {REVIEWER_FAILED_RUN, REVIEWER_POST_MODEL_FAILED_RUN, REVIEWER_STRUCTURED_PRIOR_RUN, POST_HANDOFF_RUN}
                or type(sealed.get("run_attempt")) is not int or sealed.get("run_attempt") != 1 or sealed.get("conclusion") != "success"
                or sealed.get("execution_binding") != auth["consumer_execution_binding"]
                or sealed.get("workflow_file") != auth["workflow_file"]):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement sealed execution differs")
        expected_fields = {"schema_version", "ordinal", "authorization_digest", "claim_digest",
            "logical_key", "physical_key", "run_id", "run_attempt", "conclusion", "workflow_file",
            "execution_binding", "resolved_digest", "role_payload_digest", "safe_output_proof", "trusted_uri", "content_sha256", "content_size"}
        if auth["ordinal"] == 3:
            expected_fields.add("recommendation")
            if sealed.get("recommendation") != "PASS":
                raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer seal is PASS-only")
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(str(sealed.get("trusted_uri") or ""))
        if (set(sealed) != expected_fields or type(sealed.get("ordinal")) is not int or sealed.get("ordinal") != auth["ordinal"]
                or sealed.get("schema_version") != auth["schema_version"]
                or match is None or match.group("key") != auth["physical_key"]
                or match.group("run") != str(sealed["run_id"])
                or match.group("head") != auth["consumer_execution_binding"]["execution_source_head_sha"]
                or not match.group("base").startswith("docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/worker-runs/" + REVIEWER_LOGICAL_DISPATCH + "/reviewer-comment-")
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(sealed.get("resolved_digest") or ""))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(sealed.get("role_payload_digest") or ""))
                or not re.fullmatch(r"[0-9a-f]{64}", str(sealed.get("content_sha256") or ""))
                or type(sealed.get("content_size")) is not int or sealed["content_size"] <= 0
                or not isinstance(sealed.get("safe_output_proof"), dict)):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement sealed content descriptor differs")
    return {**descriptor,"authorization": auth, "claim": claim, "sealed": sealed,
            "logical_key": REVIEWER_OLD_KEY, "physical_key": auth["physical_key"],
            "receipt_id": str(sealed["run_id"]) if isinstance(sealed, dict) else None}


def validate_reviewer_controller_bridge(snapshot, consumer_binding, *, inspection_only=False):
    old, _ = validate_reviewer_predecessor(snapshot, fresh=not reviewer_replacement_present(snapshot))
    if reviewer_structured_present(snapshot):
        validate_reviewer_structured_authorization(snapshot, consumer_binding=consumer_binding)
    elif (reviewer_post_model_present(snapshot) and isinstance(snapshot.get(REVIEWER_POST_MODEL_AUTH_PATH), dict)
            and snapshot.get(REVIEWER_POST_MODEL_AUTH_PATH).get("consumer_execution_binding") != consumer_binding):
        validate_reviewer_structured_predecessor(snapshot, fresh=True)
        if (not inspection_only or set(consumer_binding) != set(recovery_execution_binding_fields())
                or consumer_binding["execution_source_head_sha"] in {REVIEWER_STRUCTURED_PRIOR_SOURCE,
                    REVIEWER_POST_MODEL_SOURCE, REVIEWER_PREDECESSOR_SOURCE, POST_HANDOFF_SOURCE}):
            raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer bridge is not armed")
    elif reviewer_post_model_present(snapshot):
        validate_reviewer_post_model_authorization(snapshot,consumer_binding=consumer_binding)
    elif (isinstance(snapshot.get(REVIEWER_AUTH_PATH),dict)
            and snapshot.get(REVIEWER_AUTH_PATH)["consumer_execution_binding"] != consumer_binding):
        validate_reviewer_post_model_predecessor(snapshot,fresh=True)
        if (not inspection_only or set(consumer_binding)!=set(recovery_execution_binding_fields())
                or consumer_binding["execution_source_head_sha"] in {
                    POST_HANDOFF_SOURCE,REVIEWER_PREDECESSOR_SOURCE,REVIEWER_POST_MODEL_SOURCE}):
            raise VerticalInvariantError("POLICY_DENIED","post-model Reviewer bridge is not armed")
    elif reviewer_replacement_present(snapshot):
        validate_reviewer_authorization(snapshot,consumer_binding=consumer_binding)
    elif not inspection_only:
        raise VerticalInvariantError("POLICY_DENIED","Reviewer controller bridge is not armed")
    elif (set(consumer_binding)!=set(recovery_execution_binding_fields())
            or consumer_binding["execution_source_head_sha"] in {POST_HANDOFF_SOURCE,REVIEWER_PREDECESSOR_SOURCE}):
        raise VerticalInvariantError("POLICY_DENIED","Reviewer inspection bridge requires new verified policy")
    return old["consumer_execution_binding"]


def validate_post_handoff_reconciliation(snapshot, *, consumer_binding=None):
    if consumer_binding is not None:
        old = snapshot.get(POST_HANDOFF_PATH)
        if isinstance(old, dict) and old.get("consumer_execution_binding") != consumer_binding:
            consumer_binding = validate_reviewer_controller_bridge(snapshot, consumer_binding)

    sealed, events = validate_post_handoff_predecessor(snapshot)
    attestation = snapshot.get(POST_HANDOFF_PATH)
    if not isinstance(attestation, dict):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff attestation is missing or malformed")
    expected = {
        "schema_version": "ai-sdlc.v03-post-handoff-reconciliation/v1",
        "ordinal": 1, "operation_id": RECOVERY_OPERATION_ID, "operation_generation": 1,
        "predecessor_store_commit": POST_HANDOFF_STORE,
        "predecessor_event_blobs": POST_HANDOFF_HISTORY_BLOBS,
        "predecessor_document_blobs": POST_HANDOFF_DOCUMENT_BLOBS,
        "original_callback_id": POST_HANDOFF_CALLBACK,
        "original_callback_envelope_digest": events[12]["payload"]["trusted_callback_envelope_digest"],
        "producer_execution_binding": {key: sealed[key] for key in recovery_execution_binding_fields()},
        "producer_run_id": POST_HANDOFF_RUN, "output_pr_number": POST_HANDOFF_PR,
        "output_head_sha": POST_HANDOFF_HEAD, "execution_authority": False,
    }
    extras = {"consumer_execution_binding", "closed_pr_attestation", "historical_open_binding",
              "observation_callback_id", "created_at", "admission"}
    binding = attestation.get("consumer_execution_binding")
    if (set(attestation) != set(expected) | extras
            or any(canonical_json(attestation.get(k)) != canonical_json(v) for k, v in expected.items())
            or type(attestation.get("ordinal")) is not int
            or not isinstance(binding, dict) or set(binding) != set(recovery_execution_binding_fields())
            or not _SHA40.fullmatch(str(binding.get("execution_source_head_sha") or ""))
            or binding["execution_source_head_sha"] == POST_HANDOFF_SOURCE
            or not _SHA40.fullmatch(str(binding.get("execution_materialization_commit_sha") or ""))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(k) or ""))
                   for k in ("execution_policy_bundle_digest", "execution_policy_receipt_digest"))
            or (consumer_binding is not None and binding != consumer_binding)
            or not attestation.get("created_at")
            or attestation.get("observation_callback_id") != post_handoff_observation_id(attestation)):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff reconciliation authority differs")
    _, closed, historical = post_handoff_binding_material(snapshot)
    if (attestation["admission"] != POST_HANDOFF_ADMISSION
            or attestation["closed_pr_attestation"] != closed
            or attestation["historical_open_binding"] != historical):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff historical/current observations differ")
    new_id = attestation["observation_callback_id"]
    callbacks = [e for e in events if e["event_type"] == "worker.callback.recorded"
                 and (e["payload"].get("trusted_callback_envelope") or {}).get("trusted_context", {}).get("role") == "developer"]
    if (len(callbacks) != 2 or callbacks[0] != events[12]
            or callbacks[1]["sequence"] != 16 or callbacks[1]["operation_generation"] != 1
            or callbacks[1]["payload"].get("callback_id") != new_id
            or {k: v for k, v in callbacks[1]["payload"].items() if k != "callback_id"}
               != {k: v for k, v in events[12]["payload"].items() if k != "callback_id"}):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff observation relation changed")
    return attestation, events[12], callbacks[1]


def recovery_execution_binding_fields():
    return ("execution_source_head_sha", "execution_policy_bundle_digest",
            "execution_materialization_commit_sha", "execution_policy_receipt_digest")


def post_handoff_binding_material(snapshot):
    sealed, events = validate_post_handoff_predecessor(snapshot)
    receipt = events[12]["payload"]["trusted_callback_envelope"]["collected_outputs"][0]
    content = (canonical_json({
        "repository": sealed["target_repository"], "pr_number": POST_HANDOFF_PR,
        "pr_url": sealed["safe_output_artifact_proof"]["pr_url"],
        "base_ref": sealed["target_ref"], "head_sha": POST_HANDOFF_HEAD,
    }) + "\n").encode("utf-8")
    historical = {
        "kind": "developer-pr", "repository": sealed["target_repository"],
        "pr_number": POST_HANDOFF_PR, "pr_url": sealed["safe_output_artifact_proof"]["pr_url"],
        "state": "open", "draft": True, "base_ref": sealed["target_ref"],
        "head_ref": "gh-aw/F-OPERATOR-V03-DOGFOOD-HAPPY-0001-37905505035-v1-02ee4804c679cf64",
        "head_sha": POST_HANDOFF_HEAD, "content_sha256": hashlib.sha256(content).hexdigest(),
    }
    location = _DEVELOPER_PR_URI.fullmatch(sealed["safe_output_uri"])
    if (len(content) != receipt["size_bytes"] or historical["content_sha256"] != receipt["sha256"]
            or digest_json(historical) != location.group("binding")):
        raise VerticalInvariantError("POLICY_DENIED", "pinned historical PR material differs")
    current = dict(historical, state="closed")
    return content, {
        "current_binding_material": current,
        "current_binding_digest": "sha256:" + digest_json(current),
        "state": "closed", "merged": True, "draft": True,
        "merge_commit_sha": POST_HANDOFF_HEAD,
        "merged_at": "2026-10-09T08:47:29Z", "closed_at": "2026-10-09T08:47:29Z",
        "merged_by": {"login": "dream-xin-ai-sdlc-runtime-operator[bot]", "id": 316394104, "type": "Bot"},
        "artifact_proof": sealed["safe_output_artifact_proof"],
    }, {
        "material": historical, "digest": digest_json(historical),
        "trusted_uri": sealed["safe_output_uri"], "content_sha256": receipt["sha256"],
        "size_bytes": receipt["size_bytes"],
    }


def plan_post_handoff_reconciliation(snapshot, *, consumer_binding, closed_pr_attestation,
                                     historical_open_binding, occurred_at, trusted_context_digest):
    from operator_store_model import apply_plan_to_snapshot
    sealed, events = validate_post_handoff_predecessor(snapshot)
    if post_handoff_present(snapshot):
        attestation, _, observation = validate_post_handoff_reconciliation(
            snapshot, consumer_binding=consumer_binding)
        failed = [e for e in events[16:] if e["event_type"] == "worker.result.rejected"
                  and e["payload"].get("callback_id") == attestation["observation_callback_id"]]
        if failed:
            raise VerticalInvariantError("BLOCKED", "the single reconciled observation was rejected")
        return StoreMutationPlan(snapshot.ref_sha, (), {"acquired": False, "attestation": attestation,
                                                       "callback": observation["payload"]})
    projection = rebuild_projection(snapshot, RECOVERY_OPERATION_ID)
    if (len(events) != 15 or projection["generation"] != 1 or projection["status"] != "BLOCKED"
            or projection["expected_feature_revision"] != 1
            or projection["unresolved_unknown"] or projection["lineage_blocks"]
            or projection["pending_decisions"] or projection["requested_persists"]
            or projection["linearized_persists"] or projection["confirmed_persists"]):
        raise VerticalInvariantError("POLICY_DENIED", "reconciliation escaped exact rejected callback boundary")
    _, expected_closed, expected_historical = post_handoff_binding_material(snapshot)
    if closed_pr_attestation != expected_closed or historical_open_binding != expected_historical:
        raise VerticalInvariantError("POLICY_DENIED", "reconciliation fresh proof differs from fixed transition")
    attestation = {
        "schema_version": "ai-sdlc.v03-post-handoff-reconciliation/v1",
        "ordinal": 1, "operation_id": RECOVERY_OPERATION_ID, "operation_generation": 1,
        "predecessor_store_commit": POST_HANDOFF_STORE,
        "predecessor_event_blobs": POST_HANDOFF_HISTORY_BLOBS,
        "predecessor_document_blobs": POST_HANDOFF_DOCUMENT_BLOBS,
        "original_callback_id": POST_HANDOFF_CALLBACK,
        "original_callback_envelope_digest": events[12]["payload"]["trusted_callback_envelope_digest"],
        "producer_execution_binding": {key: sealed[key] for key in recovery_execution_binding_fields()},
        "producer_run_id": POST_HANDOFF_RUN, "output_pr_number": POST_HANDOFF_PR,
        "output_head_sha": POST_HANDOFF_HEAD, "execution_authority": False,
        "consumer_execution_binding": dict(consumer_binding),
        "closed_pr_attestation": closed_pr_attestation,
        "historical_open_binding": historical_open_binding,
        "admission": POST_HANDOFF_ADMISSION,
        "created_at": occurred_at,
    }
    attestation["observation_callback_id"] = post_handoff_observation_id(attestation)
    mutation = StoreMutation("create_immutable", POST_HANDOFF_PATH, attestation)
    provisional = apply_plan_to_snapshot(snapshot, StoreMutationPlan(snapshot.ref_sha, (mutation,), {}))
    envelope = events[12]["payload"]["trusted_callback_envelope"]
    plan = plan_vertical_callback_record(
        provisional, context=TrustedDispatchContext(**envelope["trusted_context"]),
        callback_id=attestation["observation_callback_id"], worker_payload=envelope["worker_payload"],
        receipts=envelope["collected_outputs"], occurred_at=occurred_at,
        trusted_context_digest=trusted_context_digest,
    )
    complete = apply_plan_to_snapshot(provisional, plan)
    validate_post_handoff_reconciliation(complete, consumer_binding=consumer_binding)
    return StoreMutationPlan(snapshot.ref_sha, (mutation, *plan.mutations),
        {"acquired": True, "attestation": attestation,
         "callback": operation_events(complete, RECOVERY_OPERATION_ID)[15]["payload"]})


def observe_post_handoff_pr(source, snapshot):
    """Authenticate current closure separately from the historical open receipt."""
    sealed, events = validate_post_handoff_predecessor(snapshot)
    pr = source._json(source.target_repository, f"/pulls/{POST_HANDOFF_PR}", source.config.target_token)
    expected_url = f"https://github.com/{source.target_repository}/pull/{POST_HANDOFF_PR}"
    before = events[12]["payload"]["trusted_callback_envelope"]
    receipt = before["collected_outputs"][0]
    head = pr.get("head") or {} if isinstance(pr, dict) else {}
    base = pr.get("base") or {} if isinstance(pr, dict) else {}
    if (not isinstance(pr, dict) or type(pr.get("number")) is not int or pr["number"] != POST_HANDOFF_PR
            or pr.get("state") != "closed" or pr.get("merged") is not True or pr.get("draft") is not True
            or pr.get("merge_commit_sha") != POST_HANDOFF_HEAD or head.get("sha") != POST_HANDOFF_HEAD
            or pr.get("merged_at") != "2026-10-09T08:47:29Z" or pr.get("closed_at") != pr["merged_at"]
            or (pr.get("merged_by") or {}).get("login") != "dream-xin-ai-sdlc-runtime-operator[bot]"
            or (pr.get("merged_by") or {}).get("id") != 316394104
            or (pr.get("merged_by") or {}).get("type") != "Bot"
            or str(pr.get("html_url") or "").lower() != expected_url
            or base.get("ref") != sealed["target_ref"]
            or base.get("sha") != sealed["candidate_head_sha"]
            or str((base.get("repo") or {}).get("full_name") or "").lower() != source.target_repository
            or str((head.get("repo") or {}).get("full_name") or "").lower() != source.target_repository
            or head.get("ref") != "gh-aw/F-OPERATOR-V03-DOGFOOD-HAPPY-0001-37905505035-v1-02ee4804c679cf64"):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff PR is not the exact authenticated closure")
    content = source._developer_content_for_target(pr)
    if (len(content) != receipt["size_bytes"]
            or hashlib.sha256(content).hexdigest() != receipt["sha256"]):
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff immutable content differs")
    current_material = source._developer_binding_material(pr, content)
    # This is explicitly historical digest material, never a changed provider response.
    historical_material = dict(current_material, state="open")
    location = _DEVELOPER_PR_URI.fullmatch(sealed["safe_output_uri"])
    if source._binding_digest(historical_material) != location.group("binding"):
        raise VerticalInvariantError("POLICY_DENIED", "historical open-state receipt cannot be re-established")
    proof = source._run_owned_safe_output(run_id=POST_HANDOFF_RUN, source_head_sha=POST_HANDOFF_SOURCE, pr=pr)
    if proof != sealed["safe_output_artifact_proof"]:
        raise VerticalInvariantError("POLICY_DENIED", "post-handoff artifact changed")
    closed = {
        "current_binding_material": current_material,
        "current_binding_digest": "sha256:" + digest_json(current_material),
        "state": pr["state"], "merged": pr["merged"], "draft": pr["draft"],
        "merge_commit_sha": pr["merge_commit_sha"], "merged_at": pr["merged_at"],
        "closed_at": pr["closed_at"],
        "merged_by": {k: pr["merged_by"][k] for k in ("login", "id", "type")},
        "artifact_proof": proof,
    }
    historical = {"material": historical_material, "digest": source._binding_digest(historical_material),
                  "trusted_uri": sealed["safe_output_uri"], "content_sha256": receipt["sha256"],
                  "size_bytes": receipt["size_bytes"]}
    if post_handoff_present(snapshot):
        attestation, _, _ = validate_post_handoff_reconciliation(snapshot)
        if (attestation["closed_pr_attestation"] != closed
                or attestation["historical_open_binding"] != historical):
            raise VerticalInvariantError("POLICY_DENIED", "post-handoff observation changed after authorization")
    return pr, content, closed, historical

from operator_vertical_reconcile_classified import FailureClassifyingTrustedRecoveringVerticalExecutor


class DogfoodPostHandoffRecoveringExecutor(FailureClassifyingTrustedRecoveringVerticalExecutor):
    """Same protected executor base, with one explicit observation supersession."""
    def advance_until_stop(self, *, operation_id):
        current = super().advance_until_stop(operation_id=operation_id)
        authority = getattr(self, "remediation_rereview_authority", None)
        return authority.after_stop(operation_id=operation_id, current=current) if authority is not None else current

    def _reconcile_callback(self, operation_id: str) -> bool | None:
        snapshot = self.runtime.backend.read_snapshot()
        if operation_id != RECOVERY_OPERATION_ID or not post_handoff_present(snapshot):
            return super()._reconcile_callback(operation_id)
        attestation, original, observation = validate_post_handoff_reconciliation(
            snapshot, consumer_binding=recovery_execution_binding(self.post_handoff_policy_authority))
        current = self._public(operation_id)
        if current["status"] in {"CANCELLED", "DONE", "NEEDS_USER"}:
            return None
        events = self._events(operation_id)
        rejected: dict[str, dict[str, Any]] = {}
        for event in events:
            if event["event_type"] != "worker.result.rejected":
                continue
            payload = event.get("payload") or {}
            callback_id = str(payload.get("callback_id") or "")
            if callback_id:
                rejected[callback_id] = dict(payload)
        translated = {
            str((event.get("payload") or {}).get("callback_id"))
            for event in events
            if event["event_type"] == "feature.event.translated"
            and (event.get("payload") or {}).get("callback_id")
        }
        validated = {
            str((event.get("payload") or {}).get("callback_id"))
            for event in events
            if event["event_type"] == "worker.result.validated"
            and (event.get("payload") or {}).get("callback_id")
        }
        generation = self._projection(operation_id)["generation"]
        for event in events:
            if event["event_type"] != "worker.callback.recorded":
                continue
            if int(event["operation_generation"]) != generation:
                continue
            payload = event.get("payload") or {}
            callback_id = str(payload.get("callback_id") or "")
            if callback_id == POST_HANDOFF_CALLBACK:
                continue  # Only the independently authenticated rejected observation.
            if not callback_id or callback_id in translated:
                continue
            rejection = rejected.get(callback_id)
            if rejection is not None:
                code = str(rejection.get("code") or "")
                reason = str(rejection.get("reason") or "durable callback result rejection")
                if code == "NEEDS_USER":
                    if current["status"] != "NEEDS_USER":
                        self._stable_stop(operation_id, status="NEEDS_USER", reason=reason)
                        return True
                    continue
                if code in {"BLOCKED", "POLICY_DENIED", "STALE_REVISION"}:
                    if current["status"] != "BLOCKED":
                        self._stable_stop(operation_id, status="BLOCKED", reason=reason)
                        return True
                    continue
                continue
            if callback_id in validated and current["status"] in {"BLOCKED", "NEEDS_USER"}:
                continue
            envelope = recover_vertical_callback(
                self.runtime.backend.read_snapshot(),
                operation_id=operation_id,
                callback_id=callback_id,
            )
            context = TrustedDispatchContext(**dict(envelope["trusted_context"]))
            process_recorded_callback(
                self,
                context=context,
                callback_id=callback_id,
                worker_payload=dict(envelope["worker_payload"]),
                receipts=list(envelope["collected_outputs"]),
                trusted_role_policy=self.trusted_role_policy,
                collector_namespace_policy=self.collector_namespace_policy,
                content_loader=self.content_loader,
                continue_after=False,
            )
            return True
        return None


def install_post_handoff_executor(responses, policy_authority):
    from dataclasses import replace
    bundle = responses.operator_bundle
    previous = bundle.executor
    if not isinstance(previous, FailureClassifyingTrustedRecoveringVerticalExecutor):
        raise V03DogfoodCompositionError("dogfood reconciliation lacks production recovering executor")
    executor = DogfoodPostHandoffRecoveringExecutor(
        base_executor=previous.base, content_loader=previous.content_loader,
        trusted_role_policy=previous.trusted_role_policy,
        collector_namespace_policy=previous.collector_namespace_policy,
    )
    executor.post_handoff_policy_authority = policy_authority
    bundle.callback_coordinator.executor = executor
    seen = set()
    for backends in (bundle.backends, bundle.vertical_bundle.api_backends, responses.backends):
        for backend in backends.values():
            if id(backend) in seen:
                continue
            seen.add(id(backend))
            if getattr(backend, "executor", None) is previous:
                backend.executor = executor
    vertical = replace(bundle.vertical_bundle, executor=executor)
    bundle = replace(bundle, vertical_bundle=vertical)
    responses = replace(responses, operator_bundle=bundle)
    if (bundle.executor.base is not previous.base or bundle.runtime is not previous.runtime
            or bundle.callback_coordinator.executor is not executor
            or bundle.backends["operation.start"].executor is not executor):
        raise V03DogfoodCompositionError("dogfood reconciliation split production authority")
    return responses

class DogfoodGitHubCandidateProvider:
    """Fresh-read exactly one same-repository PR for one immutable dogfood slot."""

    def __init__(self, *, slot: DogfoodSlot, repository: str, token: str, api_base: str = "https://api.github.com", http_get=None):
        self.slot = slot
        self.repository = normalize_repository(repository)
        self.token = str(token or "")
        self.api_base = str(api_base or "").rstrip("/")
        self.http_get = http_get or self._default_get
        self.runtime = None
        if not self.token or not self.api_base.startswith("https://"):
            raise ValueError("dogfood candidate provider requires trusted HTTPS GitHub read authority")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-dogfood-candidate",
        }

    @staticmethod
    def _default_get(url: str, headers: dict[str, str]) -> tuple[int, object]:
        req = request.Request(url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw.decode()) if raw else []
        except error.HTTPError as exc:
            return int(exc.code), []
        except Exception:
            return 0, []

    def bind_runtime(self, runtime: Any) -> None:
        if self.runtime is not None and self.runtime is not runtime:
            raise V03DogfoodCompositionError("candidate provider runtime binding changed")
        if runtime is None or getattr(runtime, "backend", None) is None:
            raise ValueError("candidate provider requires protected Store runtime")
        self.runtime = runtime

    @staticmethod
    def _developer_receipt(envelope: dict[str, Any]) -> tuple[int, str]:
        receipts = envelope.get("collected_outputs")
        if not isinstance(receipts, list):
            raise V03DogfoodCompositionError("developer callback lacks sealed output receipts")
        matches: list[tuple[int, str]] = []
        for receipt in receipts:
            if not isinstance(receipt, dict) or receipt.get("kind") != "artifact":
                continue
            match = _DEVELOPER_PR_URI.fullmatch(str(receipt.get("trusted_uri") or ""))
            if match:
                matches.append((int(match.group("pr")), match.group("head")))
        if len(matches) != 1:
            raise V03DogfoodCompositionError("developer callback must bind one exact Draft PR output")
        return matches[0]

    def _pending_handoff(self, operation_id):
        if self.runtime is None:
            return None
        snapshot = self.runtime.backend.read_snapshot()
        callbacks, translated, confirmed = {}, {}, set()
        for row in operation_events(snapshot, operation_id):
            payload = row.get("payload") or {}
            kind = row.get("event_type")
            if kind == "worker.callback.recorded":
                envelope = payload.get("trusted_callback_envelope") or {}
                if (envelope.get("trusted_context") or {}).get("role") == "developer":
                    callbacks[payload["callback_id"]] = envelope
            elif kind == "feature.event.translated":
                if payload.get("callback_id") and payload.get("feature_event_id"):
                    translated.setdefault(payload["callback_id"], payload["feature_event_id"])
            elif kind == "persist.confirmed":
                confirmed.add(payload.get("feature_event_id"))
        reconciliation = None
        if post_handoff_present(snapshot):
            reconciliation, _, _ = validate_post_handoff_reconciliation(snapshot)
        pending = []
        for callback_id in callbacks:
            if reconciliation and callback_id == reconciliation["observation_callback_id"]:
                continue
            lifecycle_id = (reconciliation["observation_callback_id"]
                            if reconciliation and callback_id == POST_HANDOFF_CALLBACK else callback_id)
            if translated.get(lifecycle_id) in confirmed:
                continue
            fact = read_dogfood_handoff(snapshot, operation_id, callback_id)
            if fact is not None:
                intent = fact["intent"]
                if (intent["target_repository"] != self.repository or intent["feature_id"] != self.slot.feature_id
                        or intent["target_ref"] != self.slot.target_ref):
                    raise V03DogfoodCompositionError("handoff escaped fixed candidate provider")
                pending.append(intent)
        if len(pending) > 1:
            raise V03DogfoodCompositionError("multiple incomplete Developer handoffs")
        return pending[0] if pending else None

    def _candidate(self) -> dict[str, Any]:
        owner = self.repository.split("/", 1)[0]
        query = parse.urlencode({
            "state": "open",
            "head": f"{owner}:{self.slot.target_ref}",
            "base": DEFAULT_BRANCH,
            "per_page": 100,
        })
        status, payload = self.http_get(
            f"{self.api_base}/repos/{self.repository}/pulls?{query}",
            self._headers(),
        )
        if status != 200 or not isinstance(payload, list):
            raise V03DogfoodCompositionError("dogfood candidate PR truth lookup failed closed")
        rows = [row for row in payload if isinstance(row, dict) and row.get("state") == "open" and row.get("draft") is False]
        if len(rows) != 1:
            raise V03DogfoodCompositionError("dogfood slot must resolve exactly one open non-draft PR")
        row = rows[0]
        head = row.get("head") or {}
        base = row.get("base") or {}
        head_repo = str(((head.get("repo") or {}).get("full_name")) or "").lower()
        base_repo = str(((base.get("repo") or {}).get("full_name")) or "").lower()
        head_sha = str(head.get("sha") or "").lower()
        number = row.get("number")
        if (
            head_repo != self.repository
            or base_repo != self.repository
            or head.get("ref") != self.slot.target_ref
            or base.get("ref") != DEFAULT_BRANCH
            or not isinstance(number, int)
            or isinstance(number, bool)
            or number < 1
            or not _SHA40.fullmatch(head_sha)
        ):
            raise V03DogfoodCompositionError("dogfood candidate PR repository/ref/head authority drifted")
        return row


    def _validate_pending_persist_head(self, snapshot, pending, current_head, lifecycle_id):
        """Permit only independently observed canonical in-progress Persist bytes."""
        gateway = getattr(self, "persist_gateway", None)
        gateway = getattr(gateway, "delegate", gateway)
        from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
        if not isinstance(gateway, DurableVerticalFeaturePersistGateway):
            raise V03DogfoodCompositionError("advanced handoff lacks canonical Persist authority")
        translated = [e["payload"] for e in operation_events(snapshot, pending["operation_id"])
                      if e["event_type"] == "feature.event.translated"
                      and e["payload"].get("callback_id") == lifecycle_id]
        if len(translated) != 1:
            raise V03DogfoodCompositionError("advanced handoff lacks exact translated Event")
        binding = gateway._resolve(event_id=translated[0]["feature_event_id"], target_ref=pending["target_ref"])
        receipt = gateway.event_gateway.lookup_receipt(feature_id=binding.feature_id,
            event_id=binding.event_id, expected_revision=binding.expected_revision,
            expected_event_digest=gateway._canonical_event_digest(binding))
        if receipt.state not in {"PENDING", "APPLIED"}:
            raise V03DogfoodCompositionError("pending Persist has no independent receipt")
        current_head = self._candidate()["head"]["sha"]
        status, comparison = self.http_get(
            f"{self.api_base}/repos/{self.repository}/compare/{pending['source_candidate_head_sha']}...{current_head}",
            self._headers())
        rows = comparison.get("files") if isinstance(comparison, dict) else None
        commits = comparison.get("commits") if isinstance(comparison, dict) else None
        manifest_path = f"state/features/{binding.feature_id}.yaml"
        allowed = {receipt.event_path: receipt.event_blob_sha, manifest_path: receipt.manifest_blob_sha}
        if (status != 200 or not isinstance(rows, list) or not 1 <= len(rows) <= 2
                or not isinstance(commits, list) or type(comparison.get("total_commits")) is not int
                or not 1 <= len(commits) == comparison["total_commits"] <= 4
                or comparison.get("status") != "ahead" or comparison.get("behind_by") != 0
                or (comparison.get("merge_base_commit") or {}).get("sha") != pending["source_candidate_head_sha"]
                or len({row.get("filename") for row in rows}) != len(rows)
                or any(row.get("filename") not in allowed or row.get("sha") != allowed[row["filename"]]
                       or row.get("status") not in {"added", "modified", "removed"} for row in rows)
                or (receipt.state == "PENDING" and {row["filename"] for row in rows} != {receipt.event_path})
                or (receipt.state == "APPLIED" and manifest_path not in {row["filename"] for row in rows})):
            raise V03DogfoodCompositionError("pending handoff contains noncanonical Persist changes")
        if self._candidate()["head"]["sha"] != current_head:
            raise V03DogfoodCompositionError("candidate changed during pending Persist verification")

    def current_candidate(self, *, operation_id: str, repository: str, feature_id: str, target_ref: str) -> TrustedCandidateSnapshot:
        if (
            not operation_id
            or normalize_repository(repository) != self.repository
            or feature_id != self.slot.feature_id
            or target_ref != self.slot.target_ref
        ):
            raise V03DogfoodCompositionError("candidate lookup escaped fixed dogfood slot identity")
        row = self._candidate()
        current_head = str(row["head"]["sha"]).lower()
        pending = self._pending_handoff(operation_id)
        if pending is not None:
            adopted_head = str(pending.get("source_candidate_head_sha") or "").lower()
            prior_head = str(pending.get("prior_candidate_head_sha") or "").lower()
            if (
                int(pending.get("fixture_candidate_pr_number") or 0) != int(row["number"])
                or not _SHA40.fullmatch(adopted_head)
                or not _SHA40.fullmatch(prior_head)
            ):
                raise V03DogfoodCompositionError("incomplete handoff no longer matches fixed candidate lineage")
            if current_head not in {prior_head, adopted_head}:
                snapshot = self.runtime.backend.read_snapshot()
                lifecycle_id = pending["callback_id"]
                if post_handoff_present(snapshot):
                    attestation, _, _ = validate_post_handoff_reconciliation(snapshot)
                    if lifecycle_id == POST_HANDOFF_CALLBACK:
                        lifecycle_id = attestation["observation_callback_id"]
                self._validate_pending_persist_head(snapshot, pending, current_head, lifecycle_id)
            current_head = prior_head
        return TrustedCandidateSnapshot(
            candidate_pr_number=int(row["number"]),
            candidate_head_sha=current_head,
        )


class DogfoodCandidateHandoff:
    """Adopt one sealed Developer Draft PR head onto the isolated fixture ref."""

    def __init__(
        self,
        *,
        slot: DogfoodSlot,
        repository: str,
        token: str,
        candidate_provider: DogfoodGitHubCandidateProvider,
        api_base: str = "https://api.github.com",
        http_request=None,
    ):
        self.slot = slot
        self.repository = normalize_repository(repository)
        self.token = str(token or "")
        self.candidate_provider = candidate_provider
        self.content_loader = None
        self.api_base = str(api_base or "").rstrip("/")
        self.http_request = http_request or self._default_request
        if not self.token or not self.api_base.startswith("https://"):
            raise ValueError("candidate handoff requires trusted HTTPS GitHub write authority")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-sdlc-v03-dogfood-handoff",
        }

    @staticmethod
    def _default_request(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None):
        data = None if body is None else json.dumps(body, sort_keys=True).encode("utf-8")
        request_headers = dict(headers)
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        req = request.Request(url, headers=request_headers, data=data, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return int(response.status), json.loads(raw.decode()) if raw else {}
        except error.HTTPError as exc:
            return int(exc.code), {}
        except Exception:
            return 0, {}

    def _api(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, object]:
        return self.http_request(
            method,
            f"{self.api_base}/repos/{self.repository}{path}",
            self._headers(),
            body,
        )

    def adopt(self, *, executor, context, callback_id, receipts):
        if (context.role != "developer" or context.feature_id != self.slot.feature_id
                or context.target_ref != self.slot.target_ref
                or normalize_repository(context.target_repository) != self.repository):
            raise V03DogfoodCompositionError("candidate handoff escaped fixed Developer authority")
        snapshot = executor.runtime.backend.read_snapshot()
        envelope = recover_vertical_callback(snapshot, operation_id=context.operation_id, callback_id=callback_id)
        if envelope["collected_outputs"] != receipts or envelope["trusted_context"] != _context_payload(context):
            raise V03DogfoodCompositionError("handoff arguments differ from durable callback")
        source_pr, source_head = self.candidate_provider._developer_receipt(envelope)
        if not callable(self.content_loader):
            raise V03DogfoodCompositionError("Developer handoff lacks trusted content loader")
        for receipt in receipts:
            if receipt.get("kind") != "artifact":
                continue
            data = self.content_loader(receipt["trusted_uri"])
            if (not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != receipt.get("sha256")
                    or len(data) != receipt.get("size_bytes")):
                raise V03DogfoodCompositionError("Developer output changed before handoff")
        fixture = self.candidate_provider._candidate()
        fixture_pr = int(fixture["number"])
        prior_head = str(context.candidate_head_sha or "").lower()
        if str(fixture["head"]["sha"]).lower() not in {prior_head, source_head}:
            raise V03DogfoodCompositionError("fixture candidate changed before handoff")
        status, pr = self._api("GET", f"/pulls/{source_pr}")
        if (status != 200 or not isinstance(pr, dict) or pr.get("number") != source_pr
                or pr.get("state") != "open" or pr.get("draft") is not True
                or (pr.get("base") or {}).get("ref") != self.slot.target_ref
                or str((pr.get("head") or {}).get("sha") or "").lower() != source_head
                or str((((pr.get("head") or {}).get("repo") or {}).get("full_name")) or "").lower() != self.repository
                or str((((pr.get("base") or {}).get("repo") or {}).get("full_name")) or "").lower() != self.repository):
            raise V03DogfoodCompositionError("sealed Developer Draft PR changed before handoff")
        status, comparison = self._api("GET", f"/compare/{prior_head}...{source_head}")
        if (status != 200 or not isinstance(comparison, dict) or comparison.get("status") != "ahead"
                or type(comparison.get("ahead_by")) is not int or comparison["ahead_by"] < 1
                or comparison.get("behind_by") != 0
                or str(((comparison.get("merge_base_commit") or {}).get("sha")) or "").lower() != prior_head):
            raise V03DogfoodCompositionError("Developer output is not strict fixture fast-forward")
        def plan_intent(current):
            binding = _handoff_binding(current, context.operation_id, callback_id, fixture_pr)
            if (binding["prior_candidate_head_sha"] != prior_head
                    or binding["source_candidate_pr_number"] != source_pr
                    or binding["source_candidate_head_sha"] != source_head):
                raise V03DogfoodCompositionError("handoff live proof differs from Store callback")
            return _plan_handoff_intent(current, binding=binding)
        outcome = _commit_handoff_nonempty(executor, plan_intent)
        intent, fresh = outcome["intent"], outcome["created"]
        ref_path = f"/git/refs/heads/{parse.quote(self.slot.target_ref, safe='')}"
        def read_ref():
            status, body = self._api("GET", ref_path)
            sha = str(((body.get("object") or {}).get("sha")) or "").lower() if isinstance(body, dict) else ""
            if status != 200 or not _SHA40.fullmatch(sha):
                raise V03DogfoodCompositionError("fixture ref lookup failed closed")
            return sha
        observed = read_ref()
        if observed == prior_head:
            if not fresh:
                raise V03DogfoodCompositionError("existing handoff intent grants lookup only")
            self._api("PATCH", ref_path, {"sha": source_head, "force": False})
            observed = read_ref()
        if observed != source_head:
            raise V03DogfoodCompositionError("exact handoff effect not observed; lookup only")
        _commit_handoff_nonempty(executor, lambda current: _plan_handoff_applied(current, intent=intent, observed_ref_sha=observed))


class DogfoodTrustedCallbackCoordinator:
    """Pause accepted callbacks around the trusted Developer handoff/lifecycle fence."""

    def __init__(self, *, delegate: Any, candidate_handoff: DogfoodCandidateHandoff):
        self.delegate = delegate
        self.executor = delegate.executor
        self.candidate_handoff = candidate_handoff

    @property
    def content_loader(self):
        return self.delegate.content_loader

    @staticmethod
    def _artifact_uri(receipts: list[dict[str, Any]]) -> str:
        rows = [
            str(row.get("trusted_uri") or "")
            for row in receipts
            if isinstance(row, dict) and row.get("kind") == "artifact"
        ]
        if len(rows) != 1 or _DEVELOPER_PR_URI.fullmatch(rows[0]) is None:
            raise V03DogfoodCompositionError("Developer callback lacks one exact implementation receipt")
        return rows[0]

    def _supersede_remediation_artifact(
        self,
        *,
        context: Any,
        callback_id: str,
        receipts: list[dict[str, Any]],
    ) -> None:
        feature, manifest = self.executor.feature_gateway.read_feature(operation_id=context.operation_id)
        tasks = manifest.get("tasks") or []
        remediation = [
            row for row in tasks
            if isinstance(row, dict)
            and row.get("id") == context.task_id
            and row.get("kind") == "remediation"
            and row.get("status") == "DONE"
        ]
        if not remediation:
            return
        uri = self._artifact_uri(receipts)
        drafts = [
            row for row in (manifest.get("artifacts") or [])
            if isinstance(row, dict)
            and row.get("type") == "implementation"
            and row.get("status", "draft") == "draft"
        ]
        current = [row for row in drafts if row.get("uri") == uri]
        previous = [row for row in drafts if row.get("uri") != uri]
        if len(current) != 1 or len(previous) != 1:
            raise VerticalInvariantError(
                "BLOCKED",
                "remediation supersession requires exactly one predecessor and one exact replacement draft",
            )
        event = {
            "version": "0.1.0",
            "id": "EVT-" + context.feature_id + "-VERTICAL-REMEDIATION-SUPERSEDE-"
            + digest_json({
                "callback_id": callback_id,
                "previous": previous[0]["id"],
                "replacement": current[0]["id"],
                "revision": feature.revision,
            })[:12].upper(),
            "feature_id": context.feature_id,
            "expected_revision": feature.revision,
            "occurred_at": self.executor.runtime.clock(),
            "changes": [
                {"kind": "artifact", "id": previous[0]["id"], "status": "superseded"},
            ],
        }
        self.executor._record_fact(
            context.operation_id,
            "feature.event.translated",
            {
                "feature_event_id": event["id"],
                "feature_event_digest": digest_json(event),
                "feature_event": event,
                "feature_revision": feature.revision,
                "feature_stage": feature.current_stage,
                "feature_manifest_digest": feature.manifest_digest,
                "candidate_head_sha": feature.candidate_head_sha,
                "target_ref": feature.target_ref,
                "callback_id": callback_id,
                "purpose": "remediation_artifact_supersession",
                "replacement_artifact_id": current[0]["id"],
                "superseded_artifact_id": previous[0]["id"],
            },
        )
        self.executor._persist(context.operation_id, event, feature)

    def handle(
        self,
        *,
        context: Any,
        callback_id: str,
        worker_payload: dict[str, Any],
        receipts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        projection = self.executor._projection(context.operation_id)
        if projection["generation"] != context.operation_generation:
            raise VerticalInvariantError("SUPERSEDED_GENERATION", "callback belongs to a superseded generation")
        if context.target_ref != self.executor.config.target_ref:
            raise VerticalInvariantError("STALE_REVISION", "callback target ref is outside trusted dogfood runtime")
        authority = getattr(self, "remediation_rereview_authority", None)
        if authority is not None:
            terminal = authority.stop_nonpassing(context=context, callback_id=callback_id,
                worker_payload=worker_payload, receipts=receipts)
            if terminal is not None:
                return terminal
        self.executor._commit(
            lambda snapshot: plan_vertical_callback_record(
                snapshot,
                context=context,
                callback_id=callback_id,
                worker_payload=worker_payload,
                receipts=receipts,
                occurred_at=self.executor.runtime.clock(),
                trusted_context_digest=self.executor.config.trusted_context_digest,
            )
        )
        reconciled = False
        snapshot = self.executor.runtime.backend.read_snapshot()
        if post_handoff_present(snapshot):
            attestation, _, observation = validate_post_handoff_reconciliation(snapshot)
            reconciled = callback_id == attestation["observation_callback_id"]
            if reconciled and observation["payload"]["trusted_callback_envelope"] != {
                    "trusted_context": _context_payload(context),
                    "worker_payload": worker_payload, "collected_outputs": receipts}:
                raise VerticalInvariantError("POLICY_DENIED", "reconciled callback envelope differs")
        if context.role == "developer":
            feature, _ = self.executor.feature_gateway.read_feature(operation_id=context.operation_id)
            validate_collected_outputs(
                context=context, feature=feature, worker_payload=worker_payload,
                receipts=receipts, content_loader=self.delegate.content_loader,
            )
            if not reconciled:
                self.candidate_handoff.adopt(
                    executor=self.executor, context=context, callback_id=callback_id, receipts=receipts,
                )
            else:
                read_dogfood_handoff(snapshot, context.operation_id, POST_HANDOFF_CALLBACK, require_applied=True)
        result = process_recorded_callback(
            self.executor,
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
            trusted_role_policy=self.delegate.trusted_role_policy,
            collector_namespace_policy=self.delegate.collector_namespace_policy,
            content_loader=self.delegate.content_loader,
            continue_after=False,
        )
        if context.role == "developer" and str(result.get("status") or "") not in {"BLOCKED", "NEEDS_USER"}:
            self._supersede_remediation_artifact(
                context=context,
                callback_id=callback_id,
                receipts=receipts,
            )
        return self.executor.advance_until_stop(operation_id=context.operation_id)



class DogfoodCandidateBoundActionsTransport(GitHubActionsVerticalGhAwTransport):
    """Keep Developer's fixed PR head in its payload, under fresh candidate truth.

    The shared transport's Developer contract predates real-dogfood fixtures and
    requires a null candidate payload field. All its other checks and its actual
    one-POST/lookup behavior remain inherited unchanged.
    """

    def __init__(self, config, *, candidate_provider, **kwargs):
        if not isinstance(candidate_provider, DogfoodGitHubCandidateProvider):
            raise ValueError("dogfood transport requires the fixed candidate provider")
        super().__init__(config, **kwargs)
        self.candidate_provider = candidate_provider


    def _http(self, *, method, url, token, body=None):
        if method == "GET" and re.fullmatch(
            r"https://api\.github\.com/repos/dream-xin/ai-sdlc/actions/jobs/[1-9][0-9]*/logs", url.lower()
        ):
            req = request.Request(url, method="GET", headers={
                "Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": self.config.api_version, "User-Agent": self.config.user_agent,
            })
            try:
                with request.build_opener(_GitHubSafeRedirectHandler()).open(req, timeout=30) as response:
                    raw = response.read(4 * 1024 * 1024 + 1)
                    if len(raw) > 4 * 1024 * 1024:
                        raise VerticalInvariantError("BLOCKED", "dogfood proof log exceeds bound")
                    return int(response.status), dict(response.headers.items()), raw
            except VerticalInvariantError:
                raise
            except Exception as exc:
                raise VerticalInvariantError("BLOCKED", "dogfood proof log unavailable") from exc
        return super()._http(method=method, url=url, token=token, body=body)

    def _validate_dispatch_inputs(self, *, workflow, ref, inputs):
        if workflow != self.config.workflows.developer_workflow:
            return super()._validate_dispatch_inputs(workflow=workflow, ref=ref, inputs=inputs)
        try:
            payload = json.loads(inputs["task_payload"])
            context = payload["feature_context"]
            vertical = context["vertical"]
            head = vertical["candidate_head_sha"]
            if not isinstance(head, str) or not _SHA40.fullmatch(head):
                raise ValueError("candidate head is not exact")
            checked = json.loads(inputs["task_payload"])
            checked["feature_context"]["vertical"]["candidate_head_sha"] = None
            checked_inputs = dict(inputs, task_payload=json.dumps(checked, sort_keys=True, separators=(",", ":")))
        except (KeyError, TypeError, ValueError) as exc:
            raise VerticalInvariantError("INVALID_REQUEST", "dogfood Developer payload lacks exact candidate head") from exc
        key = super()._validate_dispatch_inputs(workflow=workflow, ref=ref, inputs=checked_inputs)
        candidate = self.candidate_provider.current_candidate(
            operation_id=str(vertical.get("operation_id") or ""),
            repository=inputs["target_repository"],
            feature_id=inputs["feature_id"],
            target_ref=inputs["target_ref"],
        )
        if candidate.candidate_head_sha != head:
            raise VerticalInvariantError("STALE_REVISION", "dogfood Developer candidate changed before transport")
        # Only the validation copy was normalized. The inherited dispatcher
        # sends the original candidate-bound bytes and retains the stable key.
        return key



def plan_reviewer_replacement(snapshot, *, consumer_binding, worker_blobs, failure_proof):
    if reviewer_replacement_present(snapshot):
        auth, claim = validate_reviewer_authorization(snapshot, consumer_binding=consumer_binding)
        return StoreMutationPlan(snapshot.ref_sha, (), {"acquired": False, "authorization": auth})
    validate_reviewer_predecessor(snapshot, fresh=True)
    auth = _reviewer_complete_authorization(_reviewer_authority_identity(
        snapshot, consumer_binding=consumer_binding, worker_blobs=worker_blobs,
        failure_proof=failure_proof))
    claim = {"schema_version": auth["schema_version"], "ordinal": 1,
             "authorization_digest": "sha256:" + digest_json(auth),
             "physical_key": auth["physical_key"], "create_consumed": True}
    from operator_store_model import apply_plan_to_snapshot
    plan = StoreMutationPlan(snapshot.ref_sha, (
        StoreMutation("create_immutable", REVIEWER_AUTH_PATH, auth),
        StoreMutation("create_immutable", REVIEWER_CLAIM_PATH, claim),
    ), {"acquired": True, "authorization": auth})
    validate_reviewer_authorization(apply_plan_to_snapshot(snapshot, plan), consumer_binding=consumer_binding)
    return plan



def plan_reviewer_post_model_replacement(snapshot, *, consumer_binding, worker_blobs, failure_proof):
    if reviewer_post_model_present(snapshot):
        auth,_=validate_reviewer_post_model_authorization(snapshot,consumer_binding=consumer_binding)
        return StoreMutationPlan(snapshot.ref_sha,(),{"acquired":False,"authorization":auth})
    validate_reviewer_post_model_predecessor(snapshot,fresh=True)
    auth=_reviewer_complete_authorization(_reviewer_post_model_identity(snapshot,
        consumer_binding=consumer_binding,worker_blobs=worker_blobs,failure_proof=failure_proof))
    claim={"schema_version":auth["schema_version"],"ordinal":2,
           "authorization_digest":"sha256:"+digest_json(auth),"physical_key":auth["physical_key"],"create_consumed":True}
    from operator_store_model import apply_plan_to_snapshot
    plan=StoreMutationPlan(snapshot.ref_sha,(
        StoreMutation("create_immutable",REVIEWER_POST_MODEL_AUTH_PATH,auth),
        StoreMutation("create_immutable",REVIEWER_POST_MODEL_CLAIM_PATH,claim)),
        {"acquired":True,"authorization":auth})
    validate_reviewer_post_model_authorization(apply_plan_to_snapshot(snapshot,plan),consumer_binding=consumer_binding)
    return plan

def reviewer_post_model_scan(transport, *, physical_key):
    from dataclasses import replace
    workflows=tuple(dict.fromkeys((
        transport.config.workflows.developer_workflow,transport.config.workflows.reviewer_workflow,
        transport.config.workflows.qa_workflow,REVIEWER_OLD_WORKFLOW,REVIEWER_NEW_WORKFLOW,
        "ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml",REVIEWER_BOUNDED_WORKFLOW)))
    results={}
    for key in (REVIEWER_OLD_KEY,REVIEWER_POST_MODEL_FAILED_KEY,physical_key):
        matches=[]
        for workflow in workflows:
            reader=transport if workflow in (
                transport.config.workflows.developer_workflow,transport.config.workflows.reviewer_workflow,
                transport.config.workflows.qa_workflow) else GitHubActionsVerticalGhAwTransport(
                    replace(transport.config,workflows=replace(transport.config.workflows,reviewer_workflow=workflow)),
                    http=transport.http,sleeper=lambda _:None)
            receipt=reader.lookup(workflow=workflow,ref="main",dispatch_key=key)
            if receipt.get("lookup_state")=="UNKNOWN":
                raise VerticalInvariantError("BLOCKED","post-model Reviewer lookup incomplete")
            if receipt.get("lookup_state")=="LAUNCHED":
                matches.append((workflow,str(receipt.get("receipt_id"))))
            elif receipt.get("lookup_state")!="NOT_LAUNCHED":
                raise VerticalInvariantError("BLOCKED","post-model Reviewer lookup invalid")
        results[key]=matches
    if (results[REVIEWER_OLD_KEY]!=[(REVIEWER_OLD_WORKFLOW,str(REVIEWER_FAILED_RUN))]
            or results[REVIEWER_POST_MODEL_FAILED_KEY]!=[(REVIEWER_NEW_WORKFLOW,str(REVIEWER_POST_MODEL_FAILED_RUN))]):
        raise VerticalInvariantError("BLOCKED","post-model Reviewer predecessor execution inventory changed")
    new=results[physical_key]
    if len(new)>1 or (new and new[0][0]!=REVIEWER_BOUNDED_WORKFLOW):
        raise VerticalInvariantError("BLOCKED","post-model Reviewer successor collides")
    return {"lookup_state":"LAUNCHED" if new else "NOT_LAUNCHED","receipt_id":new[0][1] if new else None}


def reviewer_scan(transport, *, physical_key):
    """Scan both identities over current roles AND the retired Reviewer workflow."""
    from dataclasses import replace
    old_map = replace(transport.config.workflows, reviewer_workflow=REVIEWER_OLD_WORKFLOW)
    old_transport = GitHubActionsVerticalGhAwTransport(
        replace(transport.config, workflows=old_map), http=transport.http, sleeper=lambda _: None)
    workflows = tuple(dict.fromkeys((
        transport.config.workflows.developer_workflow, transport.config.workflows.reviewer_workflow,
        transport.config.workflows.qa_workflow, REVIEWER_OLD_WORKFLOW)))
    results = {}
    for key in (REVIEWER_OLD_KEY, physical_key):
        matches = []
        for workflow in workflows:
            reader = old_transport if workflow == REVIEWER_OLD_WORKFLOW else transport
            receipt = reader.lookup(workflow=workflow, ref="main", dispatch_key=key)
            if receipt.get("lookup_state") == "UNKNOWN":
                raise VerticalInvariantError("BLOCKED", "Reviewer replacement lookup is incomplete")
            if receipt.get("lookup_state") == "LAUNCHED":
                matches.append((workflow, str(receipt.get("receipt_id"))))
            elif receipt.get("lookup_state") != "NOT_LAUNCHED":
                raise VerticalInvariantError("BLOCKED", "Reviewer replacement lookup state is invalid")
        results[key] = matches
    if results[REVIEWER_OLD_KEY] != [(REVIEWER_OLD_WORKFLOW, str(REVIEWER_FAILED_RUN))]:
        raise VerticalInvariantError("BLOCKED", "Reviewer failed predecessor lookup is ambiguous")
    replacement = results[physical_key]
    if len(replacement) > 1 or (replacement and replacement[0][0] != REVIEWER_NEW_WORKFLOW):
        raise VerticalInvariantError("BLOCKED", "Reviewer replacement collides with another execution")
    return {"lookup_state": "LAUNCHED" if replacement else "NOT_LAUNCHED",
            "receipt_id": replacement[0][1] if replacement else None}


class DogfoodReviewerReplacementTransport(GitHubActionsVerticalGhAwTransport):
    def __init__(self, config, *, snapshot, consumer_binding, allow_post, http=None, sleeper=None):
        super().__init__(config, http=http, sleeper=sleeper or __import__('time').sleep)
        self.authorization, _ = validate_reviewer_authorization(snapshot, consumer_binding=consumer_binding)
        self.scan = reviewer_post_model_scan if reviewer_post_model_present(snapshot) else reviewer_scan
        self._allow_post = allow_post is True
        self._original_http = self.http
        self.http = self._reviewer_http

    def _reviewer_http(self, *, method, url, token, body=None):
        if method == "POST":
            auth = self.authorization
            if (not self._allow_post or url != self._api(f"/actions/workflows/{auth['workflow_file']}/dispatches")):
                raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement create slot already consumed")
            self._allow_post = False
            if self.scan(self, physical_key=auth["physical_key"])["lookup_state"] != "NOT_LAUNCHED":
                raise VerticalInvariantError("BLOCKED", "Reviewer replacement appeared before POST")
            self.prepost()
            status, _, raw = self._original_http(method="GET", url=self._api("/git/ref/heads/main"), token=token, body=None)
            if status != 200 or json.loads(raw).get("object", {}).get("sha") != auth["consumer_execution_binding"]["execution_source_head_sha"]:
                raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement main changed before POST")
            status, _, raw = self._original_http(method="GET", url=self._api("/pulls/552"), token=token, body=None)
            pr = json.loads(raw) if status == 200 else {}
            if (pr.get("state") != "open" or pr.get("draft") is not False
                    or pr.get("head", {}).get("sha") != REVIEWER_CANDIDATE
                    or pr.get("head", {}).get("ref") != "dogfood/v0.3-happy-path-0001"
                    or str(pr.get("head", {}).get("repo", {}).get("full_name") or "").lower() != "dream-xin/ai-sdlc"):
                raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement candidate changed before POST")
        return self._original_http(method=method, url=url, token=token, body=body)

    def _validate_dispatch_inputs(self, *, workflow, ref, inputs):
        auth = self.authorization
        expected = GhAwVerticalRoleDispatchGateway(transport=self, workflows=self.config.workflows)._inputs(reviewer_dispatch(auth))
        if workflow != auth["workflow_file"] or ref != "main" or inputs != expected:
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement payload differs from fixed task")
        return super()._validate_dispatch_inputs(workflow=workflow, ref=ref, inputs=inputs)


class DogfoodRecoveryActionsTransport(DogfoodCandidateBoundActionsTransport):
    """One exact legacy key, admitted only by its immutable protected bridge.

    The shared transport's lookup pagination, global collision checks, input
    validation and single-POST acknowledgement semantics remain unchanged.
    """

    def __init__(self, config, *, candidate_provider, **kwargs):
        super().__init__(config, candidate_provider=candidate_provider, **kwargs)
        self._continuation_snapshot = None
        self._allow_post = False
        self._creating = False
        self._transport_http = self.http
        self.http = self._guarded_http

    def _guarded_http(self, *, method, url, token, body=None):
        if method == "POST":
            _, _, continuation = self._admitted()
            if not self._creating or url != self._api(f"/actions/workflows/{RECOVERY_DEVELOPER_WORKFLOW}/dispatches"):
                raise VerticalInvariantError("POLICY_DENIED", "recovery HTTP POST escaped one admitted create")
            status, _, raw = self._transport_http(method="GET", url=self._api("/git/ref/heads/main"),
                                                  token=self.config.token, body=None)
            try:
                ref = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise VerticalInvariantError("BLOCKED", "recovery POST source lookup is indeterminate") from exc
            if (status != 200 or not isinstance(ref, dict) or ref.get("ref") != "refs/heads/main"
                    or (ref.get("object") or {}).get("type") != "commit"
                    or (ref.get("object") or {}).get("sha") != continuation["execution_source_head_sha"]):
                raise VerticalInvariantError("STALE_REVISION", "recovery main changed at HTTP POST boundary")
        return self._transport_http(method=method, url=url, token=token, body=body)

    def admit_continuation(self, snapshot, *, allow_post=False, execution_source_head_sha, execution_trusted_context_digest,
                           execution_materialization_commit_sha, execution_policy_receipt_digest,
                           execution_policy_bundle_digest):
        route = recovery_route(snapshot)
        continuation = route["bridge"]
        if allow_post and (
            continuation["execution_source_head_sha"] != execution_source_head_sha
            or continuation["execution_trusted_context_digest"] != execution_trusted_context_digest
            or continuation["execution_materialization_commit_sha"] != execution_materialization_commit_sha
            or continuation["execution_policy_receipt_digest"] != execution_policy_receipt_digest
            or continuation["execution_policy_bundle_digest"] != execution_policy_bundle_digest
        ):
            raise VerticalInvariantError("POLICY_DENIED", "continuation POST escaped current trusted installation/context")
        # Detach from a caller's mutable snapshot after validating the full chain.
        from copy import deepcopy
        self._continuation_snapshot = deepcopy(snapshot)
        self._allow_post = allow_post is True

    def _admitted(self):
        if self._continuation_snapshot is None:
            raise VerticalInvariantError("POLICY_DENIED", "recovery transport has no protected admission")
        route = recovery_route(self._continuation_snapshot)
        return route["authorization"], route["attempt"], route["bridge"]

    def _validate_lookup_identity(self, *, workflow, ref, dispatch_key):
        authorization, _, _ = self._admitted()
        if (
            dispatch_key != authorization["recovery_dispatch_key"]
            or workflow not in self._trusted_workflows
            or ref != "main" or ref != self.config.workflows.default_branch
            or self.config.workflows.developer_workflow != RECOVERY_DEVELOPER_WORKFLOW
            or self.config.control_repository != "dream-xin/ai-sdlc"
            or self.config.api_url != "https://api.github.com"
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery lookup escaped exact admitted identity")
        # All three configured trusted workflows must be scanned for collisions.
        # No substitute dispatch key is passed to validation or to HTTP.
        return self._role_for_workflow(workflow)

    def _validate_dispatch_inputs(self, *, workflow, ref, inputs):
        authorization, _, _ = self._admitted()
        if workflow != RECOVERY_DEVELOPER_WORKFLOW:
            raise VerticalInvariantError("POLICY_DENIED", "recovery POST is Developer-only")
        key = super()._validate_dispatch_inputs(workflow=workflow, ref=ref, inputs=inputs)
        candidate = self.candidate_provider.current_candidate(
            operation_id=authorization["operation_id"], repository=authorization["target_repository"],
            feature_id=authorization["feature_id"], target_ref=authorization["target_ref"],
        )
        if (candidate.candidate_pr_number, candidate.candidate_head_sha) != (
            authorization["candidate_pr_number"], authorization["candidate_head_sha"]
        ):
            raise VerticalInvariantError("STALE_REVISION", "recovery POST candidate PR/head drifted")
        payload = json.loads(inputs["task_payload"])
        task, vertical = payload["task"], payload["feature_context"]["vertical"]
        expected_dispatch = dict(authorization,
            external_dispatch_key=authorization["recovery_dispatch_key"],
            dispatch_id=authorization["recovery_dispatch_id"],
            operation_profile=VERTICAL_PROFILE,
        )
        if payload != json.loads(GhAwVerticalRoleDispatchGateway._task_payload(expected_dispatch)):
            raise VerticalInvariantError("POLICY_DENIED", "recovery POST task payload is not exact")
        expected_vertical = {
            "profile": VERTICAL_PROFILE,
            "operation_id": authorization["operation_id"],
            "operation_generation": authorization["operation_generation"],
            "semantic_effect_key": authorization["semantic_effect_key"],
            "external_dispatch_key": authorization["recovery_dispatch_key"],
            "dispatch_id": authorization["recovery_dispatch_id"],
            "expected_revision": authorization["expected_revision"],
            "candidate_head_sha": authorization["candidate_head_sha"],
        }
        if (
            vertical != expected_vertical
            or task.get("id") != authorization["task_id"]
            or task.get("role") != authorization["role"]
            or inputs.get("feature_id") != authorization["feature_id"]
            or inputs.get("target_repository") != authorization["target_repository"]
            or inputs.get("target_ref") != authorization["target_ref"]
            or inputs.get("stage") != authorization["stage"]
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery POST lost full original task binding")
        return key

    def dispatch(self, *, workflow, ref, inputs):
        self._admitted()
        if not self._allow_post:
            raise VerticalInvariantError("POLICY_DENIED", "continuation replay is lookup-only")
        # Consume even on validation/lookup failure. Never retry a process-local
        # create or reinterpret absence after an ambiguous acknowledgement.
        self._allow_post = False
        self._creating = True
        try:
            return super().dispatch(workflow=workflow, ref=ref, inputs=inputs)
        finally:
            self._creating = False


class DogfoodRecoveryDispatchGateway(GhAwVerticalRoleDispatchGateway):
    def lookup(self, *, external_dispatch_key):
        # Ignore the parent's per-process role cache. UNKNOWN in any role wins,
        # and a positive match in a different role is not an adoptable receipt.
        return self.transport._global_preflight(
            selected_workflow=RECOVERY_DEVELOPER_WORKFLOW,
            ref=self.workflows.default_branch,
            dispatch_key=external_dispatch_key,
        )


class DogfoodExecutionBoundDispatchGateway:
    """Dogfood-only adapter exposing exact readiness-resolved execution identity."""

    def __init__(self, *, delegate: GhAwVerticalRoleDispatchGateway, execution_bindings: dict[str, dict[str, str]]):
        if not isinstance(delegate, GhAwVerticalRoleDispatchGateway):
            raise ValueError("dogfood dispatch binding requires the production gh-aw gateway")
        if set(execution_bindings) != {"developer", "reviewer", "qa"}:
            raise ValueError("dogfood execution binding map is incomplete")
        self.delegate = delegate
        self.execution_bindings = {role: dict(value) for role, value in execution_bindings.items()}
        self.transport = delegate.transport
        self.workflows = delegate.workflows
        for role, binding in self.execution_bindings.items():
            if (
                binding.get("role") != role
                or binding.get("workflow_file") != self.workflows.workflow_for(role)
                or binding.get("default_branch") != self.workflows.default_branch
                or not binding.get("worker_id")
                or not binding.get("profile")
                or not binding.get("selection_policy_id")
            ):
                raise ValueError(f"dogfood execution binding drifted for {role}")

    def execution_binding(self, *, dispatch: dict[str, Any]) -> dict[str, str]:
        role = str(dispatch.get("role") or "")
        binding = self.execution_bindings.get(role)
        if binding is None:
            raise VerticalInvariantError("POLICY_DENIED", "dogfood dispatch role escaped resolved execution bindings")
        if self.workflows.workflow_for(role) != binding["workflow_file"]:
            raise VerticalInvariantError("POLICY_DENIED", "dogfood workflow changed after readiness binding")
        return dict(binding)

    def launch(self, *, dispatch: dict[str, Any]) -> dict[str, Any]:
        return self.delegate.launch(dispatch=dispatch)

    def lookup(self, *, external_dispatch_key: str) -> dict[str, Any]:
        return self.delegate.lookup(external_dispatch_key=external_dispatch_key)


class RecoverySafeOutputGhAwResultSource(FirstAttemptDigestBoundGhAwResultSource):
    def bind_post_handoff(self, runtime, policy_authority):
        self.post_handoff_runtime = runtime
        self.post_handoff_policy_authority = policy_authority

    def _post_handoff_snapshot(self):
        runtime = getattr(self, "post_handoff_runtime", None)
        if runtime is None:
            return None
        snapshot = runtime.backend.read_snapshot()
        seal = snapshot.get(REPLACEMENT_RECEIPT_PATH)
        if not post_handoff_present(snapshot) and (
                not isinstance(seal, dict) or seal.get("receipt_id") != str(POST_HANDOFF_RUN)):
            return None
        validate_post_handoff_predecessor(snapshot)
        if post_handoff_present(snapshot):
            validate_post_handoff_reconciliation(snapshot, consumer_binding=recovery_execution_binding(
                self.post_handoff_policy_authority))
        return snapshot

    def load_content(self, uri):
        snapshot = self._post_handoff_snapshot()
        if snapshot is None:
            return super().load_content(uri)
        sealed, _ = validate_post_handoff_predecessor(snapshot)
        validate_post_handoff_reconciliation(snapshot, consumer_binding=recovery_execution_binding(
            self.post_handoff_policy_authority))
        if uri != sealed["safe_output_uri"]:
            raise VerticalInvariantError("POLICY_DENIED", "post-handoff content is not original sealed URI")
        before = self._first_attempt_run_snapshot(
            run_id=POST_HANDOFF_RUN, external_dispatch_key=sealed["recovery_dispatch_key"])
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(uri)
        if before["head_sha"] != POST_HANDOFF_SOURCE or digest_json(before) != match.group("lease"):
            raise VerticalInvariantError("POLICY_DENIED", "post-handoff first-attempt lease changed")
        _, content, _, _ = observe_post_handoff_pr(self, snapshot)
        after = self._first_attempt_run_snapshot(
            run_id=POST_HANDOFF_RUN, external_dispatch_key=sealed["recovery_dispatch_key"])
        if not self._same_run_snapshot(before, after):
            raise VerticalInvariantError("POLICY_DENIED", "post-handoff run changed while loading")
        return content


    """Resolve the recovery Developer Draft PR from exact run-bound GitHub truth.

    The recovery Worker deliberately has no lifecycle conclusion dispatch.  Its
    trusted result is therefore derived from the successful Safe Outputs job and
    the one open Draft PR whose protected head name embeds the immutable run id.
    """


    def _json(self, repository, path, token):
        value = super()._json(repository, path, token)
        match = re.fullmatch(r"/actions/runs/([1-9][0-9]*)", path)
        if match and (
            not isinstance(value, dict) or type(value.get("id")) is not int
            or value["id"] != int(match.group(1))
            or type(value.get("run_attempt")) is not int
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery raw run id/attempt is not an exact integer")
        return value

    def _http(self, *, method, url, token):
        if re.fullmatch(r"https://api\.github\.com/repos/dream-xin/ai-sdlc/actions/artifacts/[1-9][0-9]*/zip", url.lower()):
            req = request.Request(url, method=method, headers={
                "Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": self.config.api_version, "User-Agent": self.config.user_agent,
            })
            try:
                with request.build_opener(_GitHubSafeRedirectHandler()).open(req, timeout=30) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                    if len(raw) > 2 * 1024 * 1024:
                        raise VerticalInvariantError("BLOCKED", "recovery Safe Output archive exceeds bound")
                    return int(response.status), dict(response.headers.items()), raw
            except VerticalInvariantError:
                raise
            except Exception as exc:
                raise VerticalInvariantError("BLOCKED", "recovery Safe Output archive unavailable") from exc
        return super()._http(method=method, url=url, token=token)

    def _run_owned_safe_output(self, *, run_id, source_head_sha, pr):
        """Authenticate the PR against successful pinned-handler run artifacts."""
        listing = self._json(self.config.control_repository,
            f"/actions/runs/{run_id}/artifacts?per_page=100", self.config.control_token)
        rows = listing.get("artifacts") if isinstance(listing, dict) else None
        if (not isinstance(rows, list) or type(listing.get("total_count")) is not int
                or listing["total_count"] != len(rows) or len(rows) > 100):
            raise VerticalInvariantError("BLOCKED", "recovery artifact listing is not exhaustive")
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == "safe-outputs-items"]
        if len(matches) != 1:
            raise VerticalInvariantError("BLOCKED", "recovery lacks one run-owned Safe Outputs artifact")
        artifact = matches[0]
        workflow_run = artifact.get("workflow_run")
        if (
            type(artifact.get("id")) is not int or artifact["id"] < 1
            or artifact.get("expired") is not False
            or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= 2 * 1024 * 1024
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact.get("digest") or ""))
            or not isinstance(workflow_run, dict)
            or any(workflow_run.get(k) != v for k, v in {
                "id": run_id, "head_sha": source_head_sha, "head_branch": "main",
                "repository_id": 1326302284, "head_repository_id": 1326302284,
            }.items())
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery artifact run/source/digest binding drifted")
        raw = self._bytes(self.config.control_repository,
            f"/actions/artifacts/{artifact['id']}/zip", self.config.control_token)
        if (
            not 0 < len(raw) <= 2 * 1024 * 1024
            or "sha256:" + hashlib.sha256(raw).hexdigest() != artifact["digest"]
        ):
            raise VerticalInvariantError("BLOCKED", "recovery artifact bytes differ from provider digest")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                names = [item.filename for item in members]
                allowed = {"safe-output-items.jsonl", "temporary-id-map.json", "safe-output-errors.json"}
                if (
                    not members or len(names) != len(set(names))
                    or "safe-output-items.jsonl" not in names or not set(names) <= allowed
                    or sum(item.file_size for item in members) > 2 * 1024 * 1024
                    or any(item.is_dir() or item.flag_bits & 1
                           or stat.S_ISLNK(item.external_attr >> 16)
                           or item.file_size < 0 or item.file_size > 2 * 1024 * 1024
                           for item in members)
                    or archive.getinfo("safe-output-items.jsonl").file_size > 256 * 1024
                ):
                    raise ValueError("unsafe or oversized archive member")
                with archive.open("safe-output-items.jsonl") as member:
                    data = member.read(256 * 1024 + 1)
                if len(data) > 256 * 1024:
                    raise ValueError("oversized manifest")
                lines = data.decode("utf-8").splitlines()
                if not lines or len(lines) > 100:
                    raise ValueError("manifest count")
                entries = [json.loads(line) for line in lines if line.strip()]
                if any(not isinstance(entry, dict) for entry in entries):
                    raise ValueError("manifest row")
        except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
            raise VerticalInvariantError("BLOCKED", "recovery Safe Output artifact is malformed") from exc
        created = [entry for entry in entries if entry.get("type") == "create_pull_request"]
        if len(created) != 1:
            raise VerticalInvariantError("BLOCKED", "recovery artifact lacks one created PR")
        entry = created[0]
        if (
            entry.get("provider") != "github"
            or type(pr.get("id")) is not int or pr["id"] < 1
            or not isinstance(pr.get("node_id"), str) or not pr["node_id"]
            or type(entry.get("id")) is not int or type(entry.get("number")) is not int
            or entry.get("id") != pr["id"] or entry.get("number") != pr["number"]
            or entry.get("url") != pr.get("html_url")
            or str(entry.get("repo") or "").lower() != self.target_repository
            or not isinstance(entry.get("metadata"), dict)
            or entry["metadata"].get("node_id") != pr["node_id"]
            or not isinstance(entry.get("timestamp"), str) or not entry["timestamp"]
        ):
            raise VerticalInvariantError("POLICY_DENIED", "candidate PR is not the exact run-owned Safe Output")
        return {
            "schema_version": "ai-sdlc.v03-recovery-safe-output-artifact/v1",
            "artifact_id": artifact["id"], "archive_digest": artifact["digest"],
            "manifest_entry_digest": "sha256:" + digest_json(entry),
            "run_id": run_id, "source_head_sha": source_head_sha,
            "pr_number": pr["number"], "pr_id": pr["id"], "pr_node_id": pr["node_id"],
            "pr_url": pr["html_url"], "repository": self.target_repository,
        }

    def safe_output_proof(self, *, run_id):
        proof = getattr(self, "_safe_output_proofs", {}).get(int(run_id))
        if proof is None:
            raise VerticalInvariantError("BLOCKED", "recovery run-owned Safe Output was not freshly resolved")
        return json.loads(canonical_json(proof))

    def seal_readiness(self, *, external_dispatch_key: str, expected_receipt_identity: str, source_head_sha: str) -> str:
        """Return PENDING/READY; terminal failure raises without permitting a seal."""
        receipt = str(expected_receipt_identity or "")
        if not receipt.isdigit() or int(receipt) < 1:
            raise VerticalInvariantError("INVALID_REQUEST", "recovery seal receipt is not exact")
        run_id = int(receipt)
        run = self._json(self.config.control_repository, f"/actions/runs/{run_id}", self.config.control_token)
        workflow = self._workflow_file(run) if isinstance(run, dict) else ""
        if (
            not isinstance(run, dict)
            or int(run.get("id") or 0) != run_id
            or int(run.get("run_attempt") or 0) != 1
            or workflow != RECOVERY_DEVELOPER_WORKFLOW
            or str(run.get("html_url") or "").lower()
               != f"https://github.com/{self.config.control_repository}/actions/runs/{run_id}".lower()
            or str(run.get("display_title") or "") != f"AI-SDLC gh-aw {external_dispatch_key}"
            or run.get("event") != "workflow_dispatch"
            or str(run.get("head_branch") or "") != DEFAULT_BRANCH
            or str(run.get("head_sha") or "") != source_head_sha
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery run cannot be sealed against authorized source")
        status = str(run.get("status") or "")
        conclusion = run.get("conclusion")
        if status in {"queued", "in_progress", "waiting", "pending", "requested"}:
            return "PENDING"
        if status != "completed" or conclusion != "success":
            raise VerticalInvariantError("BLOCKED", "recovery run terminated without success")
        jobs_doc = self._json(
            self.config.control_repository,
            f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100",
            self.config.control_token,
        )
        jobs = jobs_doc.get("jobs") if isinstance(jobs_doc, dict) else None
        if not isinstance(jobs, list):
            raise VerticalInvariantError("BLOCKED", "recovery run lacks exact attempt-1 jobs")
        safe = [row for row in jobs if isinstance(row, dict) and row.get("name") == "safe_outputs"]
        if len(safe) != 1:
            raise VerticalInvariantError("BLOCKED", "recovery run lacks one Safe Outputs job")
        safe_status = str(safe[0].get("status") or "")
        safe_conclusion = safe[0].get("conclusion")
        if safe_status in {"queued", "in_progress", "waiting", "pending", "requested"}:
            return "PENDING"
        if (
            safe_status != "completed"
            or safe_conclusion != "success"
            or int(safe[0].get("run_id") or 0) != run_id
            or int(safe[0].get("run_attempt") or 0) != 1
            or str(safe[0].get("head_sha") or "") != source_head_sha
        ):
            raise VerticalInvariantError("BLOCKED", "recovery Safe Outputs job terminated without exact success")
        return "READY"

    def resolve(self, *, external_dispatch_key: str, expected_receipt_identity: str, trusted_context: dict[str, Any]) -> TrustedGhAwResolvedResult:
        if (
            not isinstance(trusted_context, dict)
            or trusted_context.get("role") != "developer"
            or normalize_repository(str(trusted_context.get("target_repository") or "")) != self.target_repository
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery Safe Output source escaped trusted Developer scope")
        receipt = str(expected_receipt_identity or "")
        if not receipt.isdigit() or int(receipt) < 1:
            raise VerticalInvariantError("INVALID_REQUEST", "recovery receipt is not one exact Actions run")
        run_id = int(receipt)
        before = self._first_attempt_run_snapshot(run_id=run_id, external_dispatch_key=external_dispatch_key)
        if (
            before["workflow_file"] != RECOVERY_DEVELOPER_WORKFLOW
            or before["head_sha"] != str(trusted_context.get("source_head_sha") or "")
        ):
            raise VerticalInvariantError("STALE_REVISION", "recovery run is not exact admitted main source")

        jobs_path = f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100"
        jobs_before = self._json(self.config.control_repository, jobs_path, self.config.control_token)
        rows = jobs_before.get("jobs") if isinstance(jobs_before, dict) else None
        safe = [row for row in rows or [] if isinstance(row, dict) and row.get("name") == "safe_outputs"]
        if (
            not isinstance(rows, list)
            or len(safe) != 1
            or safe[0].get("status") != "completed"
            or safe[0].get("conclusion") != "success"
            or int(safe[0].get("run_attempt") or 0) != 1
            or int(safe[0].get("run_id") or 0) != run_id
            or str(safe[0].get("head_sha") or "") != before["head_sha"]
        ):
            raise VerticalInvariantError("BLOCKED", "recovery Safe Outputs job is not one exact successful first attempt")

        if external_dispatch_key != ARMED_RECOVERY_KEY:
            if (run_id == REPLACEMENT_FAILED_RUN or before["head_sha"] == REPLACEMENT_FAILED_SOURCE
                    or type(jobs_before.get("total_count")) is not int
                    or jobs_before["total_count"] != len(rows) or len(rows) > 100):
                raise VerticalInvariantError("POLICY_DENIED", "replacement requires complete fresh first-attempt jobs")
            for name in ("agent", "detection", "safe_outputs", "conclusion"):
                matching = [row for row in rows if row.get("name") == name]
                if (len(matching) != 1 or matching[0].get("status") != "completed"
                        or matching[0].get("conclusion") != "success"
                        or type(matching[0].get("run_attempt")) is not int or matching[0]["run_attempt"] != 1
                        or matching[0].get("run_id") != run_id or matching[0].get("head_sha") != before["head_sha"]):
                    raise VerticalInvariantError("BLOCKED", "replacement jobs lack exact terminal success")
                guard_name = {"agent": "Reject rerun before model execution",
                              "safe_outputs": "Require first attempt and affirmative detection before Safe Outputs effects"}.get(name)
                if guard_name:
                    steps = matching[0].get("steps")
                    guards = [step for step in steps or [] if step.get("name") == guard_name]
                    if (not isinstance(steps, list) or len(guards) != 1
                            or guards[0].get("status") != "completed" or guards[0].get("conclusion") != "success"):
                        raise VerticalInvariantError("BLOCKED", "replacement affirmative safety/first-attempt guard did not succeed")

        feature_id = str(trusted_context.get("feature_id") or "")
        expected_revision = int(trusted_context.get("expected_revision") or 0)
        target_ref = str(trusted_context.get("target_ref") or "")
        task_id = str(trusted_context.get("task_id") or "")
        dispatch_id = str(trusted_context.get("dispatch_id") or "")
        if not feature_id or expected_revision < 1 or not target_ref or not task_id or not dispatch_id:
            raise VerticalInvariantError("BLOCKED", "recovery protected context lacks exact task/candidate binding")
        prefix = f"gh-aw/{feature_id}-{run_id}-v{expected_revision}"
        post_snapshot = self._post_handoff_snapshot()
        if post_snapshot is not None:
            sealed, _ = validate_post_handoff_predecessor(post_snapshot)
            if (run_id != POST_HANDOFF_RUN or external_dispatch_key != sealed["recovery_dispatch_key"]
                    or target_ref != sealed["target_ref"] or task_id != sealed["task_id"]
                    or feature_id != sealed["feature_id"] or expected_revision != 1
                    or dispatch_id != sealed["collector_dispatch_id"]):
                raise VerticalInvariantError("POLICY_DENIED", "post-handoff resolution escaped fixed producer")
            archived_pr, _, _, _ = observe_post_handoff_pr(self, post_snapshot)
        query = parse.urlencode({"state": "open", "base": target_ref, "per_page": 100})
        listed = ([archived_pr] if post_snapshot is not None else
                  self._json(self.target_repository, f"/pulls?{query}", self.config.target_token))
        candidates = [
            row for row in listed if isinstance(row, dict)
            and (row.get("state") == "open" or (post_snapshot is not None and row.get("state") == "closed"))
            and row.get("draft") is True
            and str(row.get("title") or "").startswith("[ai-sdlc gh-aw] ")
            and str((row.get("head") or {}).get("ref") or "").startswith(prefix)
            and str(((row.get("head") or {}).get("repo") or {}).get("full_name") or "").lower() == self.target_repository
            and str(((row.get("base") or {}).get("repo") or {}).get("full_name") or "").lower() == self.target_repository
        ] if isinstance(listed, list) else []
        if len(candidates) != 1:
            raise VerticalInvariantError("BLOCKED", "recovery run does not own exactly one open Draft PR Safe Output")
        number = int(candidates[0].get("number") or 0)
        pr = self._json(self.target_repository, f"/pulls/{number}", self.config.target_token)
        head_sha = str((pr.get("head") or {}).get("sha") or "") if isinstance(pr, dict) else ""
        if (
            not isinstance(pr, dict)
            or int(pr.get("number") or 0) != number
            or number < 1
            or (external_dispatch_key != ARMED_RECOVERY_KEY and (number == REPLACEMENT_FAILED_PR or head_sha == REPLACEMENT_FAILED_HEAD))
            or (pr.get("state") != "open" and not (post_snapshot is not None and pr.get("state") == "closed"))
            or pr.get("draft") is not True
            or str((pr.get("base") or {}).get("ref") or "") != target_ref
            or not _SHA40.fullmatch(head_sha)
            or not str((pr.get("head") or {}).get("ref") or "").startswith(prefix)
            or str(pr.get("html_url") or "").lower() != f"https://github.com/{self.target_repository}/pull/{number}".lower()
        ):
            raise VerticalInvariantError("STALE_REVISION", "recovery Draft PR Safe Output changed after run-bound discovery")

        artifact_proof = self._run_owned_safe_output(run_id=run_id, source_head_sha=before["head_sha"], pr=pr)
        after = self._first_attempt_run_snapshot(run_id=run_id, external_dispatch_key=external_dispatch_key)
        jobs_after = self._json(self.config.control_repository, jobs_path, self.config.control_token)
        if not self._same_run_snapshot(before, after) or canonical_json(jobs_before) != canonical_json(jobs_after):
            raise VerticalInvariantError("BLOCKED", "recovery run/jobs changed while resolving Safe Output")
        trusted_run = TrustedGhAwRun(
            run_id=run_id,
            run_url=before["run_url"],
            receipt_identity=receipt,
            control_repository=normalize_repository(self.config.control_repository),
            workflow_file=RECOVERY_DEVELOPER_WORKFLOW,
            workflow_ref=self.config.workflows.default_branch,
            event="workflow_dispatch",
            status="completed",
            conclusion="success",
            display_title=before["display_title"],
            external_dispatch_key=external_dispatch_key,
            role="developer",
            task_id=task_id,
            worker_identity=f"gh-aw:{RECOVERY_DEVELOPER_WORKFLOW}@{before['head_sha']}",
            collector_identity=self.config.collector_identity,
            candidate_pr_number=number,
            candidate_head_sha=head_sha,
        )
        payload = {
            "status": "COMPLETED",
            "summary": f"Trusted recovery gh-aw Draft PR #{number} completed for {task_id}.",
            "outputs": [{"label": "implementation", "kind": "artifact"}],
        }
        base = MaterializedGhAwOutput(
            "implementation", "artifact", "application/json",
            f"docs/features/{feature_id}/worker-runs/{dispatch_id}/developer-pr-{number}-{head_sha}.json",
        )
        resolved = TrustedGhAwResolvedResult(run=trusted_run, role_payload=payload, outputs=(base,))
        if post_snapshot is not None:
            observe_post_handoff_pr(self, post_snapshot)
            original = _FIRST_ATTEMPT_URI_RE.fullmatch(sealed["safe_output_uri"])
            bound = MaterializedGhAwOutput("implementation", "artifact", "application/json", original.group("base") + ".json")
        else:
            bound = self._revalidate_developer(resolved=resolved, trusted_context=trusted_context, base_uri=base.trusted_uri)
        leased = MaterializedGhAwOutput(
            label=bound.label,
            kind=bound.kind,
            media_type=bound.media_type,
            trusted_uri=self._lease_uri(bound, after),
        )
        if not hasattr(self, "_safe_output_proofs"):
            self._safe_output_proofs = {}
        self._safe_output_proofs[run_id] = artifact_proof
        return TrustedGhAwResolvedResult(run=trusted_run, role_payload=payload, outputs=(leased,))


class DogfoodHandoffAwareResultSource(FirstAttemptDigestBoundGhAwResultSource):
    """Read-only revalidation of an output changed only by its recorded handoff."""

    def _http(self, *, method, url, token):
        if re.fullmatch(r"https://api\.github\.com/repos/dream-xin/ai-sdlc/actions/artifacts/[1-9][0-9]*/zip", url.lower()):
            req = request.Request(url, method=method, headers={
                "Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                "X-GitHub-Api-Version": self.config.api_version, "User-Agent": self.config.user_agent,
            })
            try:
                with request.build_opener(_GitHubSafeRedirectHandler()).open(req, timeout=30) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                    if len(raw) > 2 * 1024 * 1024:
                        raise VerticalInvariantError("BLOCKED", "recovery Safe Output archive exceeds bound")
                    return int(response.status), dict(response.headers.items()), raw
            except VerticalInvariantError:
                raise
            except Exception as exc:
                raise VerticalInvariantError("BLOCKED", "recovery Safe Output archive unavailable") from exc
        return FirstAttemptDigestBoundGhAwResultSource._http(self, method=method, url=url, token=token)



    def _developer_observation(self, *, values, run_id, external_dispatch_key, trusted):
        if trusted.get("feature_stage") != "code-review":
            return super()._developer_observation(values=values, run_id=run_id,
                external_dispatch_key=external_dispatch_key, trusted=trusted)
        runtime = getattr(self, "handoff_runtime", None)
        if runtime is None or trusted.get("role") != "developer":
            raise VerticalInvariantError("POLICY_DENIED", "remediation source lacks protected runtime")
        snapshot = runtime.backend.read_snapshot()
        rows = [e for e in operation_events(snapshot, trusted["operation_id"])
                if e["operation_generation"] == trusted["operation_generation"]]
        launches = [e["payload"] for e in rows if e["event_type"] == "dispatch.launch.authorized"
                    and e["payload"].get("external_dispatch_key") == external_dispatch_key]
        lookups = [e["payload"] for e in rows if e["event_type"] == "dispatch.launch.lookup-recorded"
                   and e["payload"].get("external_dispatch_key") == external_dispatch_key]
        reservation = snapshot.get(reservation_path(trusted["semantic_effect_key"]))
        if (len(launches) != 1 or not isinstance(reservation, dict) or not lookups
                or lookups[-1].get("lookup_state") != "LAUNCHED"
                or {str(p.get("receipt_id")) for p in lookups if p.get("lookup_state") == "LAUNCHED"} != {str(run_id)}
                or any(launches[0].get(k) != v for k, v in {
                    "stage": "code-review", "role": "developer", "dispatch_id": trusted["dispatch_id"],
                    "semantic_effect_key": trusted["semantic_effect_key"],
                    "candidate_head_sha": trusted["launch_candidate_head_sha"]}.items())
                or any(reservation.get(k) != v for k,v in {
                    "external_dispatch_key": external_dispatch_key, "feature_id": trusted["feature_id"],
                    "expected_revision": trusted["expected_revision"], "current_stage": "code-review",
                    "role": "developer"}.items())):
            raise VerticalInvariantError("POLICY_DENIED", "remediation wire mapping lacks exact launch/reservation")
        try:
            payload = json.loads(self._one(values, "TASK_PAYLOAD"))
            task_id = payload["task"]["id"]
        except Exception as exc:
            raise VerticalInvariantError("POLICY_DENIED", "remediation task payload is malformed") from exc
        identity = str(reservation.get("task_identity") or "")
        if (not identity.startswith("vertical:code-remediation:")
                or not _task_binding_matches(identity, task_id)
                or payload["task"].get("kind") != "remediation"
                or self._one(values, "STAGE") != "implementation"):
            raise VerticalInvariantError("POLICY_DENIED", "remediation task/wire stage is not the pinned Worker contract")
        dispatch = dict(trusted, task_id=task_id, task_identity=identity,
                        candidate_head_sha=trusted["launch_candidate_head_sha"])
        if payload != json.loads(GhAwVerticalRoleDispatchGateway._task_payload(dispatch)):
            raise VerticalInvariantError("POLICY_DENIED", "remediation logged task differs from protected dispatch")
        # The immutable workflow wire stage is implementation; the Feature remains
        # code-review. Only this independently bound remediation observation maps it.
        return super()._developer_observation(values=values, run_id=run_id,
            external_dispatch_key=external_dispatch_key,
            trusted=dict(trusted, feature_stage="implementation"))

    def bind_handoff(self, runtime, persist_gateway):
        self.handoff_runtime = runtime
        self.handoff_persist_gateway = persist_gateway

    def _applied_output(self, *, uri=None, trusted=None, key=None, receipt=None):
        runtime = getattr(self, "handoff_runtime", None)
        if runtime is None:
            return None
        snapshot = runtime.backend.read_snapshot()
        matches = []
        from operator_store_model import operation_ids
        for operation_id in operation_ids(snapshot):
            for event in operation_events(snapshot, operation_id):
                if event["event_type"] != "worker.callback.recorded":
                    continue
                payload = event["payload"]
                envelope = payload.get("trusted_callback_envelope") or {}
                context = envelope.get("trusted_context") or {}
                if context.get("role") != "developer":
                    continue
                outputs = envelope.get("collected_outputs") or []
                if len(outputs) != 1:
                    continue
                output = outputs[0]
                if uri is not None and output.get("trusted_uri") != uri:
                    continue
                if trusted is not None and any(context.get(field) != trusted.get(field) for field in (
                        "operation_id", "operation_generation", "feature_id", "expected_revision",
                        "feature_stage", "dispatch_id", "semantic_effect_key", "target_ref")):
                    continue
                if key is not None and context.get("external_dispatch_key") != key:
                    continue
                if receipt is not None and context.get("runtime_receipt_identity") != str(receipt):
                    continue
                fact = read_dogfood_handoff(snapshot, operation_id, payload.get("callback_id"))
                if fact is None or fact["applied"] is None:
                    continue
                if (digest_json(envelope) != payload.get("trusted_callback_envelope_digest")
                        or digest_json({"worker_payload": envelope["worker_payload"], "receipts": outputs})
                           != payload.get("callback_digest")):
                    raise VerticalInvariantError("POLICY_DENIED", "handoff callback integrity changed")
                matches.append((snapshot, event, output, fact))
        if not matches:
            return None
        if len(matches) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "post-handoff output is ambiguous")
        return matches[0]

    def _attested_content(self, matched):
        snapshot, event, receipt, fact = matched
        envelope = event["payload"]["trusted_callback_envelope"]
        context = envelope["trusted_context"]
        uri = receipt["trusted_uri"]
        match = _DEVELOPER_PR_URI.fullmatch(uri)
        lease = _FIRST_ATTEMPT_URI_RE.fullmatch(uri)
        if not match or not lease:
            raise VerticalInvariantError("POLICY_DENIED", "handoff output lacks exact content/run binding")
        run_id = int(lease.group("run"))
        before = self._first_attempt_run_snapshot(run_id=run_id, external_dispatch_key=lease.group("key"))
        if (before["head_sha"] != lease.group("head") or digest_json(before) != lease.group("lease")
                or before["role"] != "developer" or context["runtime_receipt_identity"] != str(run_id)
                or context["worker_identity"] != f"gh-aw:{before['workflow_file']}@{before['head_sha']}"
                or context["collector_identity"] != self.config.collector_identity):
            raise VerticalInvariantError("POLICY_DENIED", "handoff run lease changed")
        pr = self._json(self.target_repository, f"/pulls/{int(match.group('pr'))}", self.config.target_token)
        intent, applied = fact["intent"], fact["applied"]
        if not isinstance(pr, dict):
            raise VerticalInvariantError("POLICY_DENIED", "handoff PR response is malformed")
        if pr.get("state") == "open":
            return super().load_content(uri), before
        head, base = pr.get("head") or {}, pr.get("base") or {}
        if (pr.get("state") != "closed" or pr.get("merged") is not True or pr.get("draft") is not True
                or pr.get("number") != intent["source_candidate_pr_number"]
                or head.get("sha") != intent["source_candidate_head_sha"]
                or pr.get("merge_commit_sha") != head.get("sha") or applied["observed_ref_sha"] != head.get("sha")
                or not pr.get("merged_at") or pr.get("closed_at") != pr["merged_at"]
                or (pr.get("merged_by") or {}).get("login") != "dream-xin-ai-sdlc-runtime-operator[bot]"
                or (pr.get("merged_by") or {}).get("id") != 316394104
                or (pr.get("merged_by") or {}).get("type") != "Bot"
                or base.get("ref") != context["target_ref"]
                or any(str((part.get("repo") or {}).get("full_name") or "").lower() != self.target_repository
                       for part in (head, base))
                or not str(head.get("ref") or "").startswith(
                    f"gh-aw/{context['feature_id']}-{run_id}-v{context['expected_revision']}")):
            raise VerticalInvariantError("POLICY_DENIED", "PR closure is not its exact authorized handoff")
        content = self._developer_content_for_target(pr)
        historical = dict(self._developer_binding_material(pr, content), state="open")
        if (len(content) != receipt["size_bytes"] or hashlib.sha256(content).hexdigest() != receipt["sha256"]
                or digest_json(historical) != match.group("binding")):
            raise VerticalInvariantError("POLICY_DENIED", "handoff historical open-state receipt changed")
        RecoverySafeOutputGhAwResultSource._run_owned_safe_output(
            self, run_id=run_id, source_head_sha=before["head_sha"], pr=pr)
        after = self._first_attempt_run_snapshot(run_id=run_id, external_dispatch_key=lease.group("key"))
        if not self._same_run_snapshot(before, after):
            raise VerticalInvariantError("POLICY_DENIED", "handoff run changed during content observation")
        return content, before

    def load_content(self, uri):
        matched = self._applied_output(uri=uri)
        if matched is None:
            return super().load_content(uri)
        return self._attested_content(matched)[0]

    def resolve(self, *, external_dispatch_key, expected_receipt_identity, trusted_context):
        matched = (self._applied_output(trusted=trusted_context, key=external_dispatch_key,
                                       receipt=expected_receipt_identity)
                   if trusted_context.get("role") == "developer" else None)
        if matched is None:
            return super().resolve(external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        snapshot, event, output, fact = matched
        _, run_snapshot = self._attested_content(matched)
        run, workflow, logs = self._exact_run(external_dispatch_key=external_dispatch_key,
                                             receipt=str(expected_receipt_identity))
        observed = self._developer_observation(values=self._log_env(logs), run_id=int(run["id"]),
            external_dispatch_key=external_dispatch_key, trusted=trusted_context)
        envelope = event["payload"]["trusted_callback_envelope"]
        context = envelope["trusted_context"]
        expected_url = f"https://github.com/{self.target_repository}/pull/{fact['intent']['source_candidate_pr_number']}"
        if (str(observed["pr_url"]).lower() != expected_url or observed["task_id"] != context["task_id"]
                or workflow != self.config.workflows.developer_workflow):
            raise VerticalInvariantError("POLICY_DENIED", "post-handoff conclusion differs from protected output")
        trusted_run = TrustedGhAwRun(
            run_id=int(run["id"]), run_url=run_snapshot["run_url"], receipt_identity=str(run["id"]),
            control_repository=normalize_repository(self.config.control_repository),
            workflow_file=workflow, workflow_ref=self.config.workflows.default_branch,
            event="workflow_dispatch", status="completed", conclusion="success",
            display_title=run_snapshot["display_title"], external_dispatch_key=external_dispatch_key,
            role="developer", task_id=context["task_id"], worker_identity=context["worker_identity"],
            collector_identity=context["collector_identity"],
            candidate_pr_number=fact["intent"]["source_candidate_pr_number"],
            candidate_head_sha=fact["intent"]["source_candidate_head_sha"])
        return TrustedGhAwResolvedResult(run=trusted_run, role_payload=envelope["worker_payload"],
            outputs=(MaterializedGhAwOutput(output["label"], output["kind"], output["media_type"], output["trusted_uri"]),))



def reviewer_resolution_digest(resolved):
    from dataclasses import asdict
    return "sha256:" + digest_json({"run": asdict(resolved.run), "role_payload": resolved.role_payload,
        "outputs": [{"label": o.label, "kind": o.kind, "media_type": o.media_type,
                     "trusted_uri": o.trusted_uri} for o in resolved.outputs]})


def reviewer_trusted_context(authorization):
    trusted = reviewer_dispatch(authorization, physical=False)
    trusted.update(external_dispatch_key=authorization["physical_key"],
        launch_candidate_head_sha=REVIEWER_CANDIDATE,
        source_head_sha=authorization["consumer_execution_binding"]["execution_source_head_sha"])
    return trusted


class DogfoodReviewerReplacementSource(DogfoodHandoffAwareResultSource):
    def _json(self, repository, path, token):
        value = super()._json(repository, path, token)
        match = re.fullmatch(r"/actions/runs/([1-9][0-9]*)", path)
        if match and (not isinstance(value, dict) or type(value.get("id")) is not int
                or value["id"] != int(match.group(1)) or type(value.get("run_attempt")) is not int):
            raise VerticalInvariantError("POLICY_DENIED", "local Gate run id/attempt is not an exact integer")
        return value

    def bind_reviewer(self, runtime, policy_authority):
        self.reviewer_runtime = runtime
        self.reviewer_policy_authority = policy_authority

    def _run_owned_gate_comment(self, *, run_id, source_head_sha, comment, candidate_pr_number):
        """Authenticate one comment creation identity; content is separately leased."""
        listing = self._json(self.config.control_repository,
            f"/actions/runs/{run_id}/artifacts?per_page=100", self.config.control_token)
        rows = listing.get("artifacts") if isinstance(listing, dict) else None
        if (not isinstance(rows, list) or type(listing.get("total_count")) is not int
                or listing["total_count"] != len(rows) or len(rows) > 100):
            raise VerticalInvariantError("BLOCKED", "recovery artifact listing is not exhaustive")
        matches = [row for row in rows if isinstance(row, dict) and row.get("name") == "safe-outputs-items"]
        if len(matches) != 1:
            raise VerticalInvariantError("BLOCKED", "recovery lacks one run-owned Safe Outputs artifact")
        artifact = matches[0]
        workflow_run = artifact.get("workflow_run")
        if (
            type(artifact.get("id")) is not int or artifact["id"] < 1
            or artifact.get("expired") is not False
            or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= 2 * 1024 * 1024
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact.get("digest") or ""))
            or not isinstance(workflow_run, dict)
            or any(workflow_run.get(k) != v for k, v in {
                "id": run_id, "head_sha": source_head_sha, "head_branch": "main",
                "repository_id": 1326302284, "head_repository_id": 1326302284,
            }.items())
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery artifact run/source/digest binding drifted")
        raw = self._bytes(self.config.control_repository,
            f"/actions/artifacts/{artifact['id']}/zip", self.config.control_token)
        if (
            not 0 < len(raw) <= 2 * 1024 * 1024
            or "sha256:" + hashlib.sha256(raw).hexdigest() != artifact["digest"]
        ):
            raise VerticalInvariantError("BLOCKED", "recovery artifact bytes differ from provider digest")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = archive.infolist()
                names = [item.filename for item in members]
                allowed = {"safe-output-items.jsonl", "temporary-id-map.json", "safe-output-errors.json"}
                if (
                    not members or len(names) != len(set(names))
                    or "safe-output-items.jsonl" not in names or not set(names) <= allowed
                    or sum(item.file_size for item in members) > 2 * 1024 * 1024
                    or any(item.is_dir() or item.flag_bits & 1
                           or stat.S_ISLNK(item.external_attr >> 16)
                           or item.file_size < 0 or item.file_size > 2 * 1024 * 1024
                           for item in members)
                    or archive.getinfo("safe-output-items.jsonl").file_size > 256 * 1024
                ):
                    raise ValueError("unsafe or oversized archive member")
                with archive.open("safe-output-items.jsonl") as member:
                    data = member.read(256 * 1024 + 1)
                if len(data) > 256 * 1024:
                    raise ValueError("oversized manifest")
                lines = data.decode("utf-8").splitlines()
                if not lines or len(lines) > 100:
                    raise ValueError("manifest count")
                entries = [json.loads(line) for line in lines if line.strip()]
                if any(not isinstance(entry, dict) for entry in entries):
                    raise ValueError("manifest row")
        except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError) as exc:
            raise VerticalInvariantError("BLOCKED", "recovery Safe Output artifact is malformed") from exc
        if len(entries) != 1:
            raise VerticalInvariantError("BLOCKED", "Reviewer artifact must contain one exact comment effect")
        entry = entries[0]
        if (entry.get("type") != "add_comment" or entry.get("provider") != "github"
                or type(entry.get("id")) is not int or entry["id"] != comment.get("id")
                or type(entry.get("number")) is not int or entry["number"] != candidate_pr_number
                or entry.get("url") != comment.get("html_url")
                or str(entry.get("repo") or "").lower() != self.target_repository
                or entry.get("target") != {"provider": "github", "repository": entry.get("repo"), "number": candidate_pr_number}
                or not isinstance(entry.get("timestamp"), str)):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer comment is not the exact run-owned effect")
        return {"artifact_id": artifact["id"], "archive_digest": artifact["digest"],
                "manifest_entry_digest": "sha256:" + digest_json(entry),
                "run_id": run_id, "source_head_sha": source_head_sha,
                "comment_id": comment["id"], "comment_url": comment["html_url"], "candidate_pr_number": candidate_pr_number}


    def _reviewer_snapshot(self):
        runtime = getattr(self, "reviewer_runtime", None)
        return runtime.backend.read_snapshot() if runtime is not None else None

    def _reviewer_jobs(self, run_id, source_sha):
        doc = self._json(self.config.control_repository,
            f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100", self.config.control_token)
        rows = doc.get("jobs") if isinstance(doc, dict) else None
        if (not isinstance(rows, list) or type(doc.get("total_count")) is not int
                or doc["total_count"] != len(rows) or not 1 <= len(rows) <= 100):
            raise VerticalInvariantError("BLOCKED", "Reviewer first-attempt jobs are not exhaustive")
        for name in ("agent", "detection", "safe_outputs", "conclusion"):
            matches = [r for r in rows if r.get("name") == name]
            if (len(matches) != 1 or matches[0].get("status") != "completed"
                    or matches[0].get("conclusion") != "success"
                    or type(matches[0].get("run_attempt")) is not int or matches[0]["run_attempt"] != 1
                    or matches[0].get("run_id") != run_id or matches[0].get("head_sha") != source_sha):
                raise VerticalInvariantError("BLOCKED", "Reviewer jobs lack exact first-attempt success")
            required = {"agent": ("Reject rerun before model execution", "Execute GitHub Copilot CLI"),
                        "detection": ("Execute threat detection with AWF", "Conclude threat detection"),
                        "safe_outputs": ("Require first attempt and affirmative detection before Safe Outputs effects",
                                         "Process Safe Outputs")}.get(name, ())
            steps = matches[0].get("steps")
            if not isinstance(steps, list):
                raise VerticalInvariantError("BLOCKED", "Reviewer job steps are missing")
            for label in required:
                selected = [step for step in steps if step.get("name") == label]
                if len(selected) != 1 or selected[0].get("conclusion") != "success" or selected[0].get("status") != "completed":
                    raise VerticalInvariantError("BLOCKED", "Reviewer model/safety/effect guard did not succeed")
        return doc


    def _gate_observation(self, *, values, run_id, workflow, trusted):
        observed = super()._gate_observation(values=values, run_id=run_id, workflow=workflow, trusted=trusted)
        if workflow not in {REVIEWER_NEW_WORKFLOW, REVIEWER_BOUNDED_WORKFLOW, "ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml", "ai-sdlc-gh-aw-qa-deepseek-v03-bounded-local.lock.yml", *STRUCTURED_GATE_WORKFLOWS.values()}:
            return observed
        snapshot = self.reviewer_runtime.backend.read_snapshot()
        reservation = snapshot.get(reservation_path(trusted["semantic_effect_key"]))
        if not isinstance(reservation, dict):
            raise VerticalInvariantError("POLICY_DENIED", "local Gate lacks protected task reservation")
        physical = trusted["external_dispatch_key"]
        if reviewer_replacement_present(snapshot) and physical != REVIEWER_OLD_KEY:
            auth, _ = validate_reviewer_authorization(snapshot, consumer_binding=recovery_execution_binding(self.reviewer_policy_authority))
        else:
            auth = None
        if auth and physical == auth["physical_key"]:
            dispatch = reviewer_dispatch(auth)
        else:
            if reservation.get("external_dispatch_key") != physical:
                raise VerticalInvariantError("POLICY_DENIED", "local Gate key differs from reservation")
            dispatch = dict(trusted, task_id=observed["task_id"],
                task_identity=reservation["task_identity"], candidate_pr_number=int(observed["candidate_pr_number"]),
                candidate_head_sha=trusted["launch_candidate_head_sha"])
        if (self._one(values, "DISPATCH_KEY") != physical
                or json.loads(self._one(values, "TASK_PAYLOAD"))
                    != json.loads(GhAwVerticalRoleDispatchGateway._task_payload(dispatch))
                or not _task_binding_matches(reservation["task_identity"], observed["task_id"])):
            raise VerticalInvariantError("POLICY_DENIED", "local Gate logged physical task payload differs")
        return observed

    def _resolve_local_gate(self, *, external_dispatch_key, expected_receipt_identity, trusted_context):
        if trusted_context.get("role") not in {"reviewer", "qa"}:
            return super().resolve(external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        selected = self.config.workflows.workflow_for(trusted_context["role"])
        if selected not in {REVIEWER_NEW_WORKFLOW, REVIEWER_BOUNDED_WORKFLOW, "ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml", "ai-sdlc-gh-aw-qa-deepseek-v03-bounded-local.lock.yml", *STRUCTURED_GATE_WORKFLOWS.values()}:
            return super().resolve(external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        before = self._first_attempt_run_snapshot(run_id=int(expected_receipt_identity), external_dispatch_key=external_dispatch_key)
        binding = recovery_execution_binding(self.reviewer_policy_authority)
        if before["head_sha"] != binding["execution_source_head_sha"] or before["workflow_file"] != selected:
            raise VerticalInvariantError("POLICY_DENIED", "local Gate execution differs from current selected source")
        jobs = self._reviewer_jobs(int(expected_receipt_identity), before["head_sha"])
        resolved = super().resolve(external_dispatch_key=external_dispatch_key,
            expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        if len(resolved.outputs) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "local Gate requires one comment result")
        uri = resolved.outputs[0].trusted_uri
        match = re.search(r"/(?:reviewer|qa)-comment-([1-9][0-9]*)", uri)
        if not match:
            raise VerticalInvariantError("POLICY_DENIED", "local Gate output is not a leased comment")
        comment = self._json(self.target_repository, "/issues/comments/" + match.group(1), self.config.target_token)
        proof = self._run_owned_gate_comment(run_id=resolved.run.run_id, source_head_sha=before["head_sha"],
            comment=comment, candidate_pr_number=resolved.run.candidate_pr_number)
        content = super().load_content(uri)
        if (jobs != self._reviewer_jobs(resolved.run.run_id, before["head_sha"])
                or not self._same_run_snapshot(before, self._first_attempt_run_snapshot(
                    run_id=resolved.run.run_id, external_dispatch_key=external_dispatch_key))):
            raise VerticalInvariantError("BLOCKED", "local Gate proof changed while resolving")
        self._reviewer_proofs = getattr(self, "_reviewer_proofs", {})
        self._reviewer_proofs[resolved.run.run_id] = {
            "resolved_digest": reviewer_resolution_digest(resolved), "role_payload_digest": "sha256:" + digest_json(resolved.role_payload), "safe_output_proof": proof,
            "trusted_uri": uri, "content_sha256": hashlib.sha256(content).hexdigest(), "content_size": len(content)}
        return resolved

    def _load_local_gate(self, uri):
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(str(uri or ""))
        comment_match = re.search(r"/(?:reviewer|qa)-comment-([1-9][0-9]*)", str(uri or ""))
        if not match or not comment_match:
            return super().load_content(uri)
        before = self._first_attempt_run_snapshot(run_id=int(match.group("run")), external_dispatch_key=match.group("key"))
        if before["workflow_file"] not in {REVIEWER_NEW_WORKFLOW, REVIEWER_BOUNDED_WORKFLOW, "ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml", "ai-sdlc-gh-aw-qa-deepseek-v03-bounded-local.lock.yml", *STRUCTURED_GATE_WORKFLOWS.values()}:
            return super().load_content(uri)
        if before["head_sha"] != recovery_execution_binding(self.reviewer_policy_authority)["execution_source_head_sha"]:
            raise VerticalInvariantError("POLICY_DENIED", "local Gate content belongs to another source")
        comment = self._json(self.target_repository, "/issues/comments/" + comment_match.group(1), self.config.target_token)
        issue_url = str(comment.get("issue_url") or "")
        target = re.fullmatch(r"https://api.github.com/repos/" + re.escape(self.target_repository) + r"/issues/([1-9][0-9]*)", issue_url, re.I)
        if target is None:
            raise VerticalInvariantError("POLICY_DENIED", "local Gate comment target differs")
        self._run_owned_gate_comment(run_id=int(match.group("run")), source_head_sha=before["head_sha"],
            comment=comment, candidate_pr_number=int(target.group(1)))
        return super().load_content(uri)


    def resolve(self, *, external_dispatch_key, expected_receipt_identity, trusted_context):
        snapshot = self._reviewer_snapshot()
        if snapshot is None or not reviewer_replacement_present(snapshot):
            return self._resolve_local_gate(external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        auth, _ = validate_reviewer_authorization(snapshot, consumer_binding=recovery_execution_binding(self.reviewer_policy_authority))
        if external_dispatch_key in {REVIEWER_OLD_KEY, REVIEWER_POST_MODEL_FAILED_KEY, REVIEWER_STRUCTURED_PRIOR_KEY}:
            raise VerticalInvariantError("POLICY_DENIED", "failed Reviewer is not an adoptable result")
        if external_dispatch_key != auth["physical_key"]:
            return self._resolve_local_gate(external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        expected = reviewer_trusted_context(auth)
        for key in ("operation_id", "operation_generation", "semantic_effect_key", "dispatch_id",
                    "target_repository", "target_ref", "feature_id", "expected_revision", "feature_stage", "role",
                    "launch_candidate_head_sha"):
            if trusted_context.get(key) != expected[key]:
                raise VerticalInvariantError("POLICY_DENIED", "Reviewer physical/logical task mapping differs")
        run_id = int(expected_receipt_identity)
        before = self._first_attempt_run_snapshot(run_id=run_id, external_dispatch_key=external_dispatch_key)
        if (before["workflow_file"] != auth["workflow_file"]
                or before["head_sha"] != auth["consumer_execution_binding"]["execution_source_head_sha"]):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement producer source differs")
        jobs = self._reviewer_jobs(run_id, before["head_sha"])
        resolved = super().resolve(external_dispatch_key=external_dispatch_key,
            expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        if (resolved.run.task_id != REVIEWER_TASK or resolved.run.candidate_head_sha != REVIEWER_CANDIDATE
                or resolved.run.candidate_pr_number != 552 or len(resolved.outputs) != 1):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer replacement result identity differs")
        uri = resolved.outputs[0].trusted_uri
        match = re.search(r"/reviewer-comment-([1-9][0-9]*)", uri)
        if not match:
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer result lacks one leased comment")
        comment = self._json(self.target_repository, "/issues/comments/" + match.group(1), self.config.target_token)
        proof = self._run_owned_gate_comment(run_id=run_id, source_head_sha=before["head_sha"], comment=comment, candidate_pr_number=552)
        content = super().load_content(uri)
        seal_material = {"resolved_digest": reviewer_resolution_digest(resolved),
                         "role_payload_digest": "sha256:" + digest_json(resolved.role_payload), "safe_output_proof": proof,
                         "trusted_uri": uri, "content_sha256": hashlib.sha256(content).hexdigest(),
                         "content_size": len(content)}
        if (self._reviewer_jobs(run_id, before["head_sha"]) != jobs
                or not self._same_run_snapshot(before, self._first_attempt_run_snapshot(
                    run_id=run_id, external_dispatch_key=external_dispatch_key))):
            raise VerticalInvariantError("BLOCKED", "Reviewer proof changed while resolving")
        if reviewer_route_paths(snapshot)["seal_path"] in snapshot.files:
            sealed = reviewer_replacement_route(snapshot, consumer_binding=recovery_execution_binding(
                self.reviewer_policy_authority))["sealed"]
            if any(sealed.get(k) != v for k, v in seal_material.items()):
                raise VerticalInvariantError("POLICY_DENIED", "Reviewer fresh result differs from immutable seal")
        self._reviewer_proofs = getattr(self, "_reviewer_proofs", {})
        self._reviewer_proofs[run_id] = seal_material
        return resolved

    def load_content(self, uri):
        snapshot = self._reviewer_snapshot()
        if snapshot is None or not reviewer_replacement_present(snapshot):
            return self._load_local_gate(uri)
        auth, _ = validate_reviewer_authorization(snapshot, consumer_binding=recovery_execution_binding(self.reviewer_policy_authority))
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(str(uri or ""))
        if not match or match.group("key") != auth["physical_key"]:
            return self._load_local_gate(uri)
        sealed = reviewer_replacement_route(snapshot, consumer_binding=recovery_execution_binding(self.reviewer_policy_authority))["sealed"]
        if uri != sealed["trusted_uri"]:
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer content is not exact sealed URI")
        proof = sealed["safe_output_proof"]
        comment = self._json(self.target_repository, f"/issues/comments/{proof['comment_id']}", self.config.target_token)
        if self._run_owned_gate_comment(run_id=sealed["run_id"],
                source_head_sha=sealed["execution_binding"]["execution_source_head_sha"], comment=comment, candidate_pr_number=552) != proof:
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer run-owned comment proof changed")
        content = super().load_content(uri)
        if len(content) != sealed["content_size"] or hashlib.sha256(content).hexdigest() != sealed["content_sha256"]:
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer immutable comment content changed")
        return content



class DogfoodReviewerReplacementCollector(ProductionGhAwVerticalResultCollector):
    def __init__(self, *, policy_authority, **kwargs):
        super().__init__(**kwargs)
        self.policy_authority = policy_authority

    def handle(self, *, operation_id, external_dispatch_key):
        snapshot = self.callback_coordinator.executor.runtime.backend.read_snapshot()
        if operation_id != RECOVERY_OPERATION_ID or external_dispatch_key != REVIEWER_OLD_KEY:
            return super().handle(operation_id=operation_id, external_dispatch_key=external_dispatch_key)
        route = reviewer_replacement_route(snapshot, consumer_binding=recovery_execution_binding(self.policy_authority))
        auth, sealed = route["authorization"], route["sealed"]
        projection, launch, receipt = _current_launch_binding(snapshot,
            operation_id=operation_id, external_dispatch_key=external_dispatch_key)
        if receipt != str(REVIEWER_FAILED_RUN):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer original launch receipt changed")
        resolved = self.result_source.resolve(external_dispatch_key=auth["physical_key"],
            expected_receipt_identity=str(sealed["run_id"]), trusted_context=reviewer_trusted_context(auth))
        reservation = snapshot.get(reservation_path(REVIEWER_SEMANTIC_KEY))
        _validate_run(resolved.run, control_repository=self.control_repository, workflows=self.workflows,
            launch=dict(launch, external_dispatch_key=auth["physical_key"]),
            expected_receipt_identity=str(sealed["run_id"]), external_dispatch_key=auth["physical_key"],
            reservation=dict(reservation, external_dispatch_key=auth["physical_key"]))
        context = TrustedDispatchContext(
            operation_id=operation_id, operation_generation=1, operation_profile=VERTICAL_PROFILE,
            semantic_effect_key=REVIEWER_SEMANTIC_KEY, external_dispatch_key=REVIEWER_OLD_KEY,
            dispatch_id=REVIEWER_LOGICAL_DISPATCH, runtime_receipt_identity=str(sealed["run_id"]),
            target_repository="dream-xin/ai-sdlc", target_ref="dogfood/v0.3-happy-path-0001",
            feature_id="F-OPERATOR-V03-DOGFOOD-HAPPY-0001", expected_revision=3,
            feature_stage="code-review", task_id=REVIEWER_TASK, role="reviewer",
            candidate_pr_number=552, candidate_head_sha=REVIEWER_CANDIDATE,
            worker_identity=resolved.run.worker_identity, collector_identity=resolved.run.collector_identity)
        payload = validate_worker_result("reviewer", resolved.role_payload)
        if route["ordinal"] == 3 and payload["verdict"] != "PASS":
            raise VerticalInvariantError("NEEDS_USER", "corrected Reviewer non-PASS cannot enter lifecycle translation")
        declared = {row["label"]: row["kind"] for row in payload["outputs"]}
        receipts = _build_receipts(coordinator=self.callback_coordinator, context=context,
            outputs=resolved.outputs, declared_outputs=declared, collected_at=str(self.clock()))
        callback_id = "gh-aw-callback-" + digest_json({
            "operation_id": operation_id, "generation": 1, "external_dispatch_key": REVIEWER_OLD_KEY,
            "runtime_receipt_identity": str(sealed["run_id"]), "run_id": sealed["run_id"]})[:24]
        return self.callback_coordinator.handle(context=context, callback_id=callback_id,
            worker_payload=payload, receipts=receipts)


class DogfoodRecoveryBoundContentLoader:
    """Route exactly the protected sealed recovery URI, preserving live leases."""

    def __init__(self, *, result_source, recovery_result_source, policy_authority):
        self.result_source = result_source
        self.recovery_result_source = recovery_result_source
        self.policy_authority = policy_authority
        self.runtime = None

    def bind_runtime(self, runtime):
        if self.runtime is not None and self.runtime is not runtime:
            raise V03DogfoodCompositionError("recovery content loader runtime changed")
        self.runtime = runtime

    def inspect_historical(self, uri):
        """Read only the already-accepted Developer evidence for the exact preclaim context."""
        if self.runtime is None:
            raise VerticalInvariantError("POLICY_DENIED", "historical inspection lacks protected runtime")
        self.runtime.protected_receipt()
        snapshot = self.runtime.backend.read_snapshot()
        validate_reviewer_structured_predecessor(snapshot, fresh=True)
        validate_reviewer_controller_bridge(snapshot, recovery_execution_binding(self.policy_authority), inspection_only=True)
        sealed, _ = validate_post_handoff_predecessor(snapshot)
        if uri != sealed["safe_output_uri"]:
            raise VerticalInvariantError("POLICY_DENIED", "historical inspection is restricted to the original Developer lease")
        source = self.recovery_result_source
        before = source._first_attempt_run_snapshot(run_id=POST_HANDOFF_RUN,
            external_dispatch_key=sealed["recovery_dispatch_key"])
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(uri)
        if before["head_sha"] != POST_HANDOFF_SOURCE or digest_json(before) != match.group("lease"):
            raise VerticalInvariantError("POLICY_DENIED", "historical inspection producer lease differs")
        _, content, _, _ = observe_post_handoff_pr(source, snapshot)
        after = source._first_attempt_run_snapshot(run_id=POST_HANDOFF_RUN,
            external_dispatch_key=sealed["recovery_dispatch_key"])
        if not source._same_run_snapshot(before, after) or self.runtime.backend.read_snapshot().ref_sha != snapshot.ref_sha:
            raise VerticalInvariantError("BLOCKED", "historical inspection changed during read")
        return content

    def __call__(self, uri):
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(str(uri or ""))
        snapshot = self.runtime.backend.read_snapshot() if self.runtime is not None else None
        route = recovery_route(snapshot) if snapshot is not None and replacement_present(snapshot) else None
        if match and (match.group("key") == ARMED_RECOVERY_KEY
                      or (route is not None and match.group("key") == route["authorization"]["recovery_dispatch_key"])):
            if self.runtime is None:
                raise VerticalInvariantError("POLICY_DENIED", "recovery content lacks protected runtime")
            snapshot = self.runtime.backend.read_snapshot()
            route = recovery_route(snapshot)
            sealed = snapshot.get(route["receipt_path"])
            validate_recovery_execution_seal(snapshot, sealed, execution_binding=recovery_execution_binding(
                self.policy_authority,
            ))
            if (
                uri != sealed.get("safe_output_uri")
                or match.group("run") != sealed.get("receipt_id")
                or match.group("head") != sealed.get("execution_source_head_sha")
            ):
                raise VerticalInvariantError("POLICY_DENIED", "recovery content URI is not the exact sealed run")
            proof = sealed["safe_output_artifact_proof"]
            current = self.recovery_result_source._run_owned_safe_output(
                run_id=int(sealed["receipt_id"]), source_head_sha=sealed["execution_source_head_sha"],
                pr={"id": proof["pr_id"], "node_id": proof["pr_node_id"],
                    "number": proof["pr_number"], "html_url": proof["pr_url"]},
            )
            if current != proof:
                raise VerticalInvariantError("POLICY_DENIED", "recovery run-owned artifact changed before content load")
            return self.recovery_result_source.load_content(uri)
        return self.result_source.load_content(uri)



def _recovery_launch_task_matches(snapshot, launch, authorization):
    if launch.get("task_id") == authorization.get("task_id"):
        return True
    # The frozen producer omitted task_id. Only its exact event and exact
    # reservation may use the already-defined shared task-identity matcher.
    events = operation_events(snapshot, RECOVERY_OPERATION_ID)
    exact = [row for row in events if row.get("sequence") == 10]
    reservation = snapshot.get(reservation_path(str(authorization.get("semantic_effect_key") or "")))
    return (
        "task_id" not in launch and len(exact) == 1
        and _recovery_document_blob(exact[0]) == "f6f793ce9725e618be0b7b25712e5abe44a55b59"
        and exact[0].get("payload") == launch
        and isinstance(reservation, dict)
        and _recovery_document_blob(reservation) == REPLACEMENT_RESERVATION_BLOB
        and _task_binding_matches(str(reservation.get("task_identity") or ""), str(authorization.get("task_id") or ""))
    )


class DogfoodRecoveryCollector:
    """Collect only the one sealed recovery run, then reuse the closed callback path."""

    def __init__(self, *, callback_coordinator, result_source, workflows, control_repository, clock, policy_authority):
        self.policy_authority = policy_authority
        self.callback_coordinator = callback_coordinator
        self.result_source = result_source
        self.workflows = workflows
        self.control_repository = normalize_repository(control_repository)
        self.clock = clock

    def handle(self, *, operation_id: str, external_dispatch_key: str) -> dict[str, Any]:
        if operation_id != RECOVERY_OPERATION_ID:
            raise VerticalInvariantError("POLICY_DENIED", "recovery collector escaped frozen Operation")
        executor = self.callback_coordinator.executor
        snapshot = executor.runtime.backend.read_snapshot()
        route = recovery_route(snapshot)
        sealed = snapshot.get(route["receipt_path"])
        authorization, attempt = route["authorization"], route["attempt"]
        if not isinstance(authorization, dict) or not isinstance(attempt, dict) or not isinstance(sealed, dict):
            raise VerticalInvariantError("POLICY_DENIED", "recovery collector lacks complete immutable fact chain")
        continuation = validate_recovery_execution_seal(snapshot, sealed, execution_binding=recovery_execution_binding(
            self.policy_authority,
        ))
        authorization_digest = "sha256:" + digest_json(authorization)
        attempt_digest = "sha256:" + digest_json(attempt)
        sealed_bindings = {
            "schema_version": RECOVERY_SCHEMA,
            "operation_id": operation_id,
            "external_dispatch_key": external_dispatch_key,
            "workflow_file": RECOVERY_DEVELOPER_WORKFLOW,
            "authorization_digest": authorization_digest,
            "create_attempt_digest": attempt_digest,
            "historical_observation_digest": RECOVERY_OBSERVATION_DIGEST,
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "head_branch": DEFAULT_BRANCH,
            "role": "developer",
            "stage": "implementation",
        }
        if (
            any(sealed.get(key) != value for key, value in sealed_bindings.items())
            or attempt.get("schema_version") != RECOVERY_SCHEMA
            or attempt.get("authorization_digest") != authorization_digest
            or attempt.get("status") != "ARMED"
            or authorization.get("schema_version") != RECOVERY_SCHEMA
            or authorization.get("historical_observation_digest") != RECOVERY_OBSERVATION_DIGEST
            or sealed.get("provider_fence_digest") != authorization.get("provider_fence_digest")
            or sealed.get("worker_blobs") != authorization.get("worker_blobs")
            or sealed.get("trusted_context_digest") != authorization.get("trusted_context_digest")
            or sealed.get("source_head_sha") != authorization.get("installation_commit_sha")
            or sealed.get("target_repository") != authorization.get("target_repository")
            or sealed.get("target_ref") != authorization.get("target_ref")
            or sealed.get("feature_id") != authorization.get("feature_id")
            or sealed.get("task_id") != authorization.get("task_id")
            or sealed.get("candidate_pr_number") != authorization.get("candidate_pr_number")
            or sealed.get("candidate_head_sha") != authorization.get("candidate_head_sha")
            or sealed.get("expected_revision") != authorization.get("expected_revision")
            or sealed.get("recovery_dispatch_key") != authorization.get("recovery_dispatch_key")
            or sealed.get("recovery_dispatch_id") != authorization.get("recovery_dispatch_id")
            or sealed.get("display_title") != "AI-SDLC gh-aw " + str(sealed.get("recovery_dispatch_key") or "")
            or not str(sealed.get("receipt_id") or "").isdigit()
            or not isinstance(authorization.get("worker_blobs"), dict)
            or len(authorization["worker_blobs"]) != 4
            or not str(authorization.get("provider_fence_digest") or "").startswith("sha256:")
        ):
            raise VerticalInvariantError("POLICY_DENIED", "recovery immutable authorization/attempt/receipt chain drifted")
        for key in (
            "operation_id", "operation_generation", "semantic_effect_key", "external_dispatch_key",
            "recovery_dispatch_key", "recovery_dispatch_id", "workflow_file", "installation_commit_sha",
            "trusted_context_digest", "feature_id", "target_ref", "task_id", "stage", "role",
            "expected_revision", "candidate_pr_number", "candidate_head_sha", "provider_fence_digest",
            "historical_observation_digest", "worker_blobs",
        ):
            if attempt.get(key) != authorization.get(key):
                raise VerticalInvariantError("POLICY_DENIED", f"recovery create-attempt lost {key} binding")
        projection, launch, historical_receipt = _current_launch_binding(
            snapshot, operation_id=operation_id, external_dispatch_key=external_dispatch_key
        )
        if (
            str(historical_receipt) != "37204777409"
            or launch.get("dispatch_id") != RECOVERY_COLLECTOR_DISPATCH_ID
            or str(launch.get("role") or "") != "developer"
            or int(authorization.get("operation_generation") or -1) != int(projection["generation"])
            or authorization.get("feature_id") != projection.get("feature_id")
            or int(authorization.get("expected_revision") or -1) != int(projection["expected_feature_revision"])
            or authorization.get("semantic_effect_key") != launch.get("semantic_effect_key")
            or authorization.get("stage") != launch.get("stage")
            or authorization.get("role") != launch.get("role")
            or authorization.get("candidate_head_sha") != launch.get("candidate_head_sha")
            or not _recovery_launch_task_matches(snapshot, launch, authorization)
            or normalize_repository(str(authorization.get("target_repository") or "")) != normalize_repository(str(projection["target_repository"]))
            or authorization.get("target_ref") != executor.config.target_ref
        ):
            raise VerticalInvariantError("POLICY_DENIED", "historical launch/full recovery binding drifted")
        semantic_key = str(launch["semantic_effect_key"])
        reservation = snapshot.get(reservation_path(semantic_key))
        if not isinstance(reservation, dict):
            raise VerticalInvariantError("INTERNAL_FAILURE", "durable semantic reservation is missing")
        recovery_key = str(sealed["recovery_dispatch_key"])
        receipt = str(sealed["receipt_id"])
        trusted = {
            "operation_id": operation_id,
            "operation_generation": int(projection["generation"]),
            "operation_profile": VERTICAL_PROFILE,
            "semantic_effect_key": semantic_key,
            "external_dispatch_key": recovery_key,
            "dispatch_id": str(sealed["collector_dispatch_id"]),
            "execution_dispatch_id": str(sealed["recovery_dispatch_id"]),
            "target_repository": normalize_repository(str(projection["target_repository"])),
            "target_ref": executor.config.target_ref,
            "feature_id": str(projection["feature_id"]),
            "expected_revision": int(projection["expected_feature_revision"]),
            "feature_stage": str(launch["stage"]),
            "role": "developer",
            "task_id": str(sealed["task_id"]),
            "launch_candidate_head_sha": launch.get("candidate_head_sha"),
            "source_head_sha": str(sealed["execution_source_head_sha"]),
        }
        resolved = self.result_source.resolve(
            external_dispatch_key=recovery_key,
            expected_receipt_identity=receipt,
            trusted_context=trusted,
        )
        recovery_launch = dict(launch)
        recovery_launch.update({
            "external_dispatch_key": recovery_key,
            "dispatch_id": str(sealed["recovery_dispatch_id"]),
        })
        recovery_reservation = dict(reservation)
        recovery_reservation["external_dispatch_key"] = recovery_key
        if (
            resolved.run.run_id != int(receipt)
            or resolved.run.workflow_file != sealed["workflow_file"]
            or resolved.run.workflow_ref != sealed["head_branch"]
            or resolved.run.event != sealed["event"]
            or resolved.run.display_title != sealed["display_title"]
            or resolved.run.external_dispatch_key != sealed["recovery_dispatch_key"]
            or resolved.run.task_id != sealed["task_id"]
            or resolved.run.role != sealed["role"]
            or resolved.run.worker_identity != f"gh-aw:{sealed['workflow_file']}@{sealed['execution_source_head_sha']}"
            or resolved.run.candidate_pr_number != sealed.get("output_candidate_pr_number")
            or resolved.run.candidate_head_sha != sealed.get("output_candidate_head_sha")
            or self.result_source.safe_output_proof(run_id=resolved.run.run_id) != sealed.get("safe_output_artifact_proof")
            or "sha256:" + digest_json(sealed.get("safe_output_artifact_proof")) != sealed.get("safe_output_artifact_digest")
            or len(resolved.outputs) != 1
            or resolved.outputs[0].trusted_uri != sealed.get("safe_output_uri")
            or "sha256:" + digest_json({"trusted_uri": resolved.outputs[0].trusted_uri})
               != sealed.get("safe_output_digest")
        ):
            raise VerticalInvariantError("POLICY_DENIED", "resolved recovery run differs from sealed full binding")
        _validate_run(
            resolved.run,
            control_repository=self.control_repository,
            workflows=self.workflows,
            launch=recovery_launch,
            expected_receipt_identity=receipt,
            external_dispatch_key=recovery_key,
            reservation=recovery_reservation,
        )
        worker_payload = validate_worker_result("developer", resolved.role_payload)
        context = TrustedDispatchContext(
            operation_id=operation_id,
            operation_generation=int(projection["generation"]),
            operation_profile=VERTICAL_PROFILE,
            semantic_effect_key=semantic_key,
            external_dispatch_key=external_dispatch_key,
            dispatch_id=str(launch["dispatch_id"]),
            runtime_receipt_identity=receipt,
            target_repository=str(projection["target_repository"]),
            target_ref=executor.config.target_ref,
            feature_id=str(projection["feature_id"]),
            expected_revision=int(projection["expected_feature_revision"]),
            feature_stage=str(launch["stage"]),
            task_id=resolved.run.task_id,
            role="developer",
            candidate_pr_number=None,
            candidate_head_sha=launch.get("candidate_head_sha"),
            worker_identity=resolved.run.worker_identity,
            collector_identity=resolved.run.collector_identity,
        )
        declared = {str(row["label"]): str(row["kind"]) for row in worker_payload.get("outputs", [])}
        receipts = _build_receipts(
            coordinator=self.callback_coordinator,
            context=context,
            outputs=resolved.outputs,
            declared_outputs=declared,
            collected_at=str(self.clock()),
        )
        if set(declared.items()) != {(row["label"], row["kind"]) for row in receipts}:
            raise VerticalInvariantError("BLOCKED", "sealed recovery outputs differ from role result")
        callback_id = "gh-aw-recovery-callback-" + digest_json({
            "operation_id": operation_id,
            "external_dispatch_key": external_dispatch_key,
            "recovery_dispatch_key": recovery_key,
            "runtime_receipt_identity": receipt,
            "run_id": resolved.run.run_id,
        })[:24]
        return self.callback_coordinator.handle(
            context=context,
            callback_id=callback_id,
            worker_payload=worker_payload,
            receipts=receipts,
        )


@dataclass(frozen=True)
class V03DogfoodFullComposition:
    slot: DogfoodSlot
    workflows: GhAwVerticalWorkflowMap
    candidate_provider: DogfoodGitHubCandidateProvider
    feature_truth_gateway: DeferredFixtureFeatureTruthGateway
    feature_event_gateway: Any
    actions_transport: GitHubActionsVerticalGhAwTransport
    dispatch_gateway: DogfoodExecutionBoundDispatchGateway
    result_source: FirstAttemptDigestBoundGhAwResultSource
    responses: OpenAIResponsesProductionBundle
    bundle: Any
    collector: ProductionGhAwVerticalResultCollector
    recovery_workflows: GhAwVerticalWorkflowMap
    recovery_dispatch_gateway: GhAwVerticalRoleDispatchGateway
    recovery_result_source: FirstAttemptDigestBoundGhAwResultSource
    recovery_collector: DogfoodRecoveryCollector
    policy_authority: Any

    @property
    def runtime(self):
        return self.responses.runtime

    @property
    def adapter(self):
        return self.responses.adapter



def build_v03_dogfood_full_composition(
    *,
    slot: DogfoodSlot,
    config: TrustedOperatorRuntimeConfig,
    adapter_id: str,
    target_read_token: str,
    actions_token: str,
    event_write_token: str,
    control_repository: str,
    workflows: GhAwVerticalWorkflowMap,
    execution_bindings: dict[str, dict[str, str]],
    protection_verifier: Any,
    policy_authority: Any,
    trusted_context_digest: str,
    collector_namespace_policy: str,
    trusted_role_policy: str,
    clock: Callable[[], Any],
    github_api_base: str = "https://api.github.com",
    persist_poll_attempts: int = 60,
    persist_poll_seconds: float = 2.0,
) -> V03DogfoodFullComposition:
    if not isinstance(config, TrustedOperatorRuntimeConfig):
        raise ValueError("trusted Operator runtime config is required")
    if adapter_id != OPENAI_RESPONSES_ADAPTER_ID:
        raise ValueError("real v0.3 dogfood must use the supported OpenAI Responses adapter")
    if normalize_repository(control_repository) != config.target_repository:
        raise ValueError("dogfood control/target repository must be identical")
    if config.feature_ids != frozenset({slot.feature_id}) or config.feature_ref(slot.feature_id) != slot.target_ref:
        raise ValueError("dogfood production composition escaped fixed slot binding")
    if workflows.default_branch != DEFAULT_BRANCH:
        raise ValueError("dogfood workflows must dispatch from main")
    if not all((target_read_token, actions_token, event_write_token, trusted_context_digest)):
        raise ValueError("dogfood composition requires explicit bounded credentials/context")
    if actions_token == event_write_token:
        raise ValueError("Actions/read authority and Feature Event write authority must remain split")
    if not callable(clock):
        raise ValueError("dogfood composition requires trusted clock")
    for name in ("rollout_verifier", "resolution_policy_verifier", "decision_policy_verifier"):
        if getattr(policy_authority, name, None) is None:
            raise ValueError(f"protected policy authority lacks {name}")

    feature_event_gateway = build_release_decision_event_gateway(
        token=event_write_token,
        repository=config.target_repository,
        default_branch=DEFAULT_BRANCH,
        feature_refs={slot.feature_id: slot.target_ref},
        api_base=github_api_base,
        poll_attempts=persist_poll_attempts,
        poll_seconds=persist_poll_seconds,
    )
    candidate_provider = DogfoodGitHubCandidateProvider(
        slot=slot,
        repository=config.target_repository,
        token=target_read_token,
        api_base=github_api_base,
    )
    feature_truth = DeferredFixtureFeatureTruthGateway()
    source_config = GitHubActionsGhAwResultSourceConfig(
        control_repository=control_repository,
        control_token=actions_token,
        target_token=target_read_token,
        workflows=workflows,
        collector_identity=COLLECTOR_IDENTITY,
        api_url=github_api_base,
    )
    structured = (workflows.reviewer_workflow == STRUCTURED_GATE_WORKFLOWS["reviewer"]
                  and workflows.qa_workflow == STRUCTURED_GATE_WORKFLOWS["qa"])
    result_source = (DogfoodStructuredGateResultSource if structured else DogfoodReviewerReplacementSource)(
        source_config,
        target_repository=config.target_repository,
    )
    actions_transport = DogfoodCandidateBoundActionsTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=control_repository,
            token=actions_token,
            workflows=workflows,
            api_url=github_api_base,
        ),
        candidate_provider=candidate_provider,
    )
    recovery_workflows = GhAwVerticalWorkflowMap(
        default_branch=DEFAULT_BRANCH,
        developer_workflow=RECOVERY_DEVELOPER_WORKFLOW,
        reviewer_workflow=workflows.reviewer_workflow,
        qa_workflow=workflows.qa_workflow,
    )
    recovery_source_config = GitHubActionsGhAwResultSourceConfig(
        control_repository=control_repository,
        control_token=actions_token,
        target_token=target_read_token,
        workflows=recovery_workflows,
        collector_identity=COLLECTOR_IDENTITY,
        api_url=github_api_base,
    )
    recovery_result_source = RecoverySafeOutputGhAwResultSource(
        recovery_source_config,
        target_repository=config.target_repository,
    )
    recovery_transport = DogfoodRecoveryActionsTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=control_repository,
            token=actions_token,
            workflows=recovery_workflows,
            api_url=github_api_base,
        ),
        candidate_provider=candidate_provider,
    )
    recovery_dispatch_gateway = DogfoodRecoveryDispatchGateway(
        transport=recovery_transport,
        workflows=recovery_workflows,
    )
    raw_dispatch_gateway = GhAwVerticalRoleDispatchGateway(transport=actions_transport, workflows=workflows)
    dispatch_gateway = DogfoodExecutionBoundDispatchGateway(
        delegate=raw_dispatch_gateway,
        execution_bindings=execution_bindings,
    )

    decision_verifier = policy_authority.decision_policy_verifier
    if slot.scenario == "session_recovery":
        decision_verifier = DogfoodSessionDecisionPolicyVerifier(
            repository=config.store_repository,
            installation_sha=policy_authority.installation_commit_sha,
            token=target_read_token,
            api_base=github_api_base,
        )
    content_loader = DogfoodRecoveryBoundContentLoader(
        result_source=result_source, recovery_result_source=recovery_result_source,
        policy_authority=policy_authority,
    )
    responses = build_openai_responses_production_bundle(
        config=config,
        feature_id=slot.feature_id,
        registration_id=f"v03-dogfood:{slot.scenario}",
        provider_scope_id=PROVIDER_SCOPE_ID,
        target_read_token=target_read_token,
        protection_verifier=protection_verifier,
        rollout_verifier=policy_authority.rollout_verifier,
        resolution_policy_verifier=policy_authority.resolution_policy_verifier,
        feature_gateway=feature_truth,
        feature_event_gateway=feature_event_gateway,
        dispatch_gateway=dispatch_gateway,
        collector_content_loader=content_loader,
        policy_verifier=decision_verifier,
        trusted_context_digest=trusted_context_digest,
        collector_namespace_policy=collector_namespace_policy,
        trusted_role_policy=trusted_role_policy,
        github_api_base=github_api_base,
        clock=clock,
    )
    responses = install_post_handoff_executor(responses, policy_authority)
    bundle = responses.operator_bundle
    content_loader.bind_runtime(responses.runtime)
    recovery_result_source.bind_post_handoff(responses.runtime, policy_authority)
    result_source.bind_handoff(responses.runtime, bundle.executor.persist_gateway)
    result_source.bind_reviewer(responses.runtime, policy_authority)
    durable_truth = DurableDecisionFeatureTruthGateway(
        runtime=responses.runtime,
        feature_gateway=feature_event_gateway,
        candidate_provider=candidate_provider,
    )
    feature_truth.bind(durable_truth)
    candidate_provider.bind_runtime(responses.runtime)
    candidate_provider.persist_gateway = bundle.executor.persist_gateway
    if structured:
        from operator_v03_vertical_production_runtime import _DeferredExactVerticalPersistGateway
        from operator_vertical_feature_persist_gateway import DurableVerticalFeaturePersistGateway
        persist_bridge = bundle.executor.persist_gateway
        if (bundle.executor.feature_gateway is not feature_truth
                or feature_truth.delegate is not durable_truth
                or not isinstance(persist_bridge, _DeferredExactVerticalPersistGateway)
                or not isinstance(persist_bridge.delegate, DurableVerticalFeaturePersistGateway)
                or persist_bridge.delegate.runtime is not responses.runtime):
            raise V03DogfoodCompositionError("structured context production delegate binding differs")
        builder = DogfoodStructuredGateContextBuilder(runtime=responses.runtime,
            feature_gateway=durable_truth, persist_gateway=persist_bridge.delegate,
            content_loader=content_loader, candidate_provider=candidate_provider, policy_authority=policy_authority)
        dispatch_gateway.delegate = DogfoodCurrentStructuredDispatchGateway(
            transport=actions_transport, workflows=workflows, context_builder=builder)
    candidate_handoff = DogfoodCandidateHandoff(
        slot=slot,
        repository=config.target_repository,
        token=event_write_token,
        candidate_provider=candidate_provider,
        api_base=github_api_base,
    )
    candidate_handoff.content_loader = content_loader
    callback_coordinator = DogfoodTrustedCallbackCoordinator(
        delegate=bundle.callback_coordinator,
        candidate_handoff=candidate_handoff,
    )
    if structured and slot.scenario == "review_remediation":
        authority = DogfoodRemediationRereviewAuthority(executor=bundle.executor,
            candidate_provider=candidate_provider, content_loader=content_loader, policy_authority=policy_authority)
        bundle.executor.remediation_rereview_authority = authority
        callback_coordinator.remediation_rereview_authority = authority
        dispatch_gateway.delegate.remediation_rereview_authority = authority
    collector = DogfoodReviewerReplacementCollector(
        policy_authority=policy_authority,
        callback_coordinator=callback_coordinator,
        result_source=result_source,
        workflows=workflows,
        control_repository=control_repository,
        clock=clock,
    )
    recovery_collector = DogfoodRecoveryCollector(
        callback_coordinator=callback_coordinator,
        result_source=recovery_result_source,
        workflows=recovery_workflows,
        control_repository=control_repository,
        clock=clock,
        policy_authority=policy_authority,
    )

    if responses.runtime is not bundle.runtime or durable_truth.runtime is not responses.runtime:
        raise V03DogfoodCompositionError("Responses/dogfood FeatureTruth escaped unique production Store runtime")
    if (
        collector.result_source is not result_source
        or collector.callback_coordinator is not callback_coordinator
        or callback_coordinator.executor is not bundle.callback_coordinator.executor
    ):
        raise V03DogfoodCompositionError("dogfood collector escaped production authority graph")
    if responses.adapter.registration.registration_id != f"v03-dogfood:{slot.scenario}":
        raise V03DogfoodCompositionError("dogfood Responses registration escaped fixed scenario")
    if "operation.resume" in responses.backends:
        raise V03DogfoodCompositionError("server-only operation.resume leaked into dogfood Responses adapter")

    return V03DogfoodFullComposition(
        slot=slot,
        workflows=workflows,
        candidate_provider=candidate_provider,
        feature_truth_gateway=feature_truth,
        feature_event_gateway=feature_event_gateway,
        actions_transport=actions_transport,
        dispatch_gateway=dispatch_gateway,
        result_source=result_source,
        responses=responses,
        bundle=bundle,
        collector=collector,
        recovery_workflows=recovery_workflows,
        recovery_dispatch_gateway=recovery_dispatch_gateway,
        recovery_result_source=recovery_result_source,
        recovery_collector=recovery_collector,
        policy_authority=policy_authority,
    )

# Preparation-only structured Gate handoff. No production composition selects this gateway.
STRUCTURED_GATE_WORKFLOWS = {
    "reviewer": "ai-sdlc-gh-aw-reviewer-deepseek-v03-structured-local.lock.yml",
    "qa": "ai-sdlc-gh-aw-qa-deepseek-v03-structured-local.lock.yml",
}


class DogfoodStructuredGateContextBuilder:
    """Package fresh authenticated evidence; never select a role or create an entitlement."""

    def __init__(self, *, runtime, feature_gateway, persist_gateway, content_loader,
                 candidate_provider, policy_authority):
        self.runtime = runtime
        self.feature_gateway = feature_gateway
        self.persist_gateway = persist_gateway
        self.content_loader = content_loader
        self.candidate_provider = candidate_provider
        self.policy_authority = policy_authority
        if any(getattr(component, "runtime", None) is not runtime for component in
               (feature_gateway, persist_gateway, content_loader, candidate_provider)):
            raise V03DogfoodCompositionError("structured context must retain one protected runtime")

    def build_prospective_reviewer(self, authorization):
        return self(reviewer_dispatch(authorization), prospective_authorization=authorization)

    def __call__(self, dispatch, *, prospective_authorization=None):
        from v03_dogfood_gate_output import validate_context, canonical, sha256, CONTEXT_SCHEMA
        from v03_dogfood_fixture_pool import task_text
        from operator_vertical_store import vertical_projection
        import base64
        runtime = self.runtime
        runtime.protected_receipt()
        snapshot = runtime.backend.read_snapshot()
        operation_id = str(dispatch.get("operation_id") or "")
        events = operation_events(snapshot, operation_id)
        projection = vertical_projection(snapshot, operation_id)
        role = dispatch.get("role")
        stage = {"reviewer": "code-review", "qa": "verification"}.get(role)
        if (stage is None or dispatch.get("operation_profile") != VERTICAL_PROFILE
                or projection["generation"] != dispatch.get("operation_generation")
                or projection["expected_feature_revision"] != dispatch.get("expected_revision")
                or projection["status"] in {"DONE", "CANCELLED", "BLOCKED"}):
            raise VerticalInvariantError("POLICY_DENIED", "structured context is outside its pending Gate")
        logical_key = str(dispatch.get("external_dispatch_key") or "")
        logical_dispatch_id = dispatch.get("dispatch_id")
        # Only the already-existing fixed Reviewer mapping may resolve a physical key.
        if (operation_id == RECOVERY_OPERATION_ID and role == "reviewer"
                and logical_key != REVIEWER_OLD_KEY and reviewer_replacement_present(snapshot)):
            if prospective_authorization is not None:
                validate_reviewer_structured_predecessor(snapshot, fresh=True)
                expected = reviewer_structured_authorization(snapshot,
                    consumer_binding=recovery_execution_binding(self.policy_authority),
                    predecessor_proof=prospective_authorization.get("predecessor_proof"))
                if canonical_json(expected) != canonical_json(prospective_authorization):
                    raise VerticalInvariantError("POLICY_DENIED", "prospective Reviewer authority differs")
                auth = expected
            else:
                route = reviewer_replacement_route(snapshot,
                    consumer_binding=recovery_execution_binding(self.policy_authority), require_seal=False)
                auth = route["authorization"]
            if logical_key != auth["physical_key"] or logical_dispatch_id != auth["physical_dispatch_id"]:
                raise VerticalInvariantError("POLICY_DENIED", "unknown structured context physical mapping")
            logical_key, logical_dispatch_id = auth["logical_key"], auth["logical_dispatch_id"]
        claims = [row for row in events if row["event_type"] == "dispatch.launch.authorized"
                  and row["operation_generation"] == dispatch["operation_generation"]
                  and row["payload"].get("external_dispatch_key") == logical_key]
        if len(claims) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "structured context lacks one protected launch binding")
        launch = claims[0]["payload"]
        expected = {"role": role, "stage": stage, "feature_id": dispatch["feature_id"],
                    "expected_revision": dispatch["expected_revision"],
                    "candidate_head_sha": dispatch["candidate_head_sha"],
                    "semantic_effect_key": dispatch["semantic_effect_key"],
                    "dispatch_id": logical_dispatch_id}
        if any(launch.get(key) != value for key, value in expected.items()):
            raise VerticalInvariantError("POLICY_DENIED", "structured context differs from protected launch")
        reservation = snapshot.get(reservation_path(dispatch["semantic_effect_key"]))
        if not isinstance(reservation, dict) or not _task_binding_matches(
                str(reservation.get("task_identity") or ""), str(dispatch.get("task_id") or "")):
            raise VerticalInvariantError("POLICY_DENIED", "structured context task differs from reservation")
        feature, manifest = self.feature_gateway.read_feature(operation_id=operation_id)
        candidate = self.candidate_provider.current_candidate(operation_id=operation_id,
            repository=dispatch["target_repository"], feature_id=dispatch["feature_id"],
            target_ref=dispatch["target_ref"])
        if (feature.feature_id != dispatch["feature_id"]
                or feature.repository != normalize_repository(dispatch["target_repository"])
                or feature.target_ref != dispatch["target_ref"]
                or feature.revision != dispatch["expected_revision"] or feature.current_stage != stage
                or feature.candidate_head_sha != dispatch["candidate_head_sha"]
                or candidate.candidate_head_sha != dispatch["candidate_head_sha"]
                or candidate.candidate_pr_number != dispatch["candidate_pr_number"]):
            raise VerticalInvariantError("STALE_REVISION", "structured context candidate/Feature drift")
        task_uri = f"docs/features/{feature.feature_id}/dogfood-task.md"
        tasks = [row for row in manifest.get("artifacts", [])
                 if row.get("type") == "dogfood-task" and row.get("uri") == task_uri]
        if len(tasks) != 1:
            raise VerticalInvariantError("BLOCKED", "approved dogfood task reference missing")
        provider = self.candidate_provider
        url = (provider.api_base + "/repos/" + provider.repository + "/contents/"
               + parse.quote(task_uri, safe="/") + "?ref=" + dispatch["candidate_head_sha"])
        status, document = provider.http_get(url, provider._headers())
        if status != 200 or not isinstance(document, dict) or document.get("encoding") != "base64":
            raise VerticalInvariantError("BLOCKED", "approved task bytes unavailable")
        raw = base64.b64decode(document.get("content", ""), validate=False)
        git_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
        if (document.get("sha") != git_blob or raw != task_text(provider.slot).encode("utf-8")):
            raise VerticalInvariantError("POLICY_DENIED", "candidate changed approved dogfood task")
        documents = [{"kind": "approved_task", "uri": task_uri, "content": raw.decode("utf-8"),
            "sha256": sha256(raw), "source_head_sha": dispatch["candidate_head_sha"],
            "run_id": None, "receipt_sha256": None}]
        directory = f"docs/features/{feature.feature_id}"
        status, listing = provider.http_get(provider.api_base + "/repos/" + provider.repository
            + "/contents/" + parse.quote(directory, safe="/") + "?ref=" + dispatch["candidate_head_sha"],
            provider._headers())
        if status != 200 or not isinstance(listing, list) or not 2 <= len(listing) <= 7:
            raise VerticalInvariantError("BLOCKED", "candidate document listing unavailable or unbounded")
        candidate_documents = []
        for row in listing:
            path = str(row.get("path") or "")
            if path == task_uri:
                continue
            if (row.get("type") != "file" or not path.startswith(directory + "/")
                    or "/" in path[len(directory) + 1:] or not path.endswith(".md")
                    or not _SHA40.fullmatch(str(row.get("sha") or ""))):
                raise VerticalInvariantError("BLOCKED", "candidate context has unsupported document shape")
            status, item = provider.http_get(provider.api_base + "/repos/" + provider.repository
                + "/contents/" + parse.quote(path, safe="/") + "?ref=" + dispatch["candidate_head_sha"],
                provider._headers())
            if status != 200 or not isinstance(item, dict) or item.get("encoding") != "base64":
                raise VerticalInvariantError("BLOCKED", "candidate document unavailable")
            content = base64.b64decode(item.get("content", ""), validate=False)
            blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\x00" + content).hexdigest()
            if item.get("sha") != blob or row["sha"] != blob:
                raise VerticalInvariantError("BLOCKED", "candidate document Git blob changed")
            candidate_documents.append({"kind": "candidate_document", "uri": path,
                "content": content.decode("utf-8"), "sha256": sha256(content),
                "source_head_sha": dispatch["candidate_head_sha"], "run_id": None, "receipt_sha256": None})
        if not candidate_documents:
            raise VerticalInvariantError("BLOCKED", "candidate implementation content is missing")
        documents.extend(sorted(candidate_documents, key=lambda row: row["uri"]))
        implementations = [row for row in manifest.get("artifacts", [])
                           if row.get("type") == "implementation" and row.get("status") in {"draft", "approved"}]
        if len(implementations) != 1:
            raise VerticalInvariantError("BLOCKED", "structured context implementation is ambiguous")
        selected = [("implementation", implementations[0], "developer")]
        if role == "qa":
            gate = [row for row in manifest.get("gates", []) if row.get("id") == "code-gate"
                    and row.get("status") == "PASS"]
            if len(gate) != 1:
                raise VerticalInvariantError("BLOCKED", "QA context lacks protected passing code Gate")
            review_ids = set(gate[0].get("evidence") or [])
            reviews = [row for row in manifest.get("evidence", []) if row.get("id") in review_ids
                       and row.get("type") == "review" and row.get("status") == "pass"]
            if len(reviews) != 1:
                raise VerticalInvariantError("BLOCKED", "QA context review evidence is ambiguous")
            selected.append(("review", reviews[0], "reviewer"))
        for kind, record, producer_role in selected:
            matches = []
            for callback in events:
                if callback["event_type"] != "worker.callback.recorded":
                    continue
                payload = callback["payload"]
                callback_id = payload["callback_id"]
                envelope = payload.get("trusted_callback_envelope") or {}
                context = envelope.get("trusted_context") or {}
                if (context.get("role") != producer_role
                        or context.get("operation_id") != operation_id
                        or context.get("operation_generation") != dispatch["operation_generation"]
                        or context.get("feature_id") != feature.feature_id):
                    continue
                receipts = [row for row in envelope.get("collected_outputs", [])
                            if row.get("trusted_uri") == record.get("uri")]
                accepted = [row for row in events if row["event_type"] == "worker.result.validated"
                            and row["payload"].get("callback_id") == callback_id]
                if len(receipts) != 1 or len(accepted) != 1:
                    continue
                if any(row["event_type"] == "worker.result.rejected"
                       and row["payload"].get("callback_id") == callback_id for row in events):
                    raise VerticalInvariantError("BLOCKED", "context callback has conflicting acceptance")
                if digest_json(envelope) != payload.get("trusted_callback_envelope_digest"):
                    raise VerticalInvariantError("BLOCKED", "context envelope digest differs")
                translations = [row for row in events if row["event_type"] == "feature.event.translated"
                    and row["payload"].get("callback_id") == callback_id
                    and any(change.get("record", {}).get("uri") == record["uri"]
                            for change in row["payload"].get("feature_event", {}).get("changes", []))]
                if len(translations) != 1:
                    raise VerticalInvariantError("BLOCKED", "context artifact lacks canonical translation")
                translated = translations[0]
                event_id = translated["payload"]["feature_event_id"]
                confirmations = [row for row in events if row["event_type"] == "persist.confirmed"
                                 and row["payload"].get("feature_event_id") == event_id]
                if (len(confirmations) != 1 or not callback["sequence"] < accepted[0]["sequence"]
                        < translated["sequence"] < confirmations[0]["sequence"] < claims[0]["sequence"]):
                    raise VerticalInvariantError("BLOCKED", "context artifact Persist ordering differs")
                receipt = self.persist_gateway.lookup_feature_event(event_id=event_id, target_ref=feature.target_ref)
                if receipt != {"event_id": event_id, "result_revision": confirmations[0]["payload"]["result_revision"]}:
                    raise VerticalInvariantError("BLOCKED", "context canonical Persist receipt unavailable")
                output = receipts[0]
                lease = _FIRST_ATTEMPT_URI_RE.fullmatch(str(output.get("trusted_uri") or ""))
                if (lease is None or context.get("runtime_receipt_identity") != lease.group("run")
                        or context.get("worker_identity", "").rsplit("@", 1)[-1] != lease.group("head")):
                    raise VerticalInvariantError("BLOCKED", "context producer run/source lease differs")
                content = (self.content_loader.inspect_historical(output["trusted_uri"])
                           if prospective_authorization is not None else self.content_loader(output["trusted_uri"]))
                if (not isinstance(content, bytes) or len(content) != output.get("size_bytes")
                        or sha256(content) != output.get("sha256")):
                    raise VerticalInvariantError("BLOCKED", "context authenticated content differs")
                matches.append({"kind": kind, "uri": output["trusted_uri"], "content": content.decode("utf-8"),
                    "sha256": sha256(content), "source_head_sha": lease.group("head"),
                    "run_id": int(lease.group("run")), "receipt_sha256": digest_json(output)})
            if len(matches) != 1:
                raise VerticalInvariantError("BLOCKED", "context lacks one accepted authenticated producer")
            documents.extend(matches)
        identity = {key: dispatch[key] for key in (
            "operation_id", "operation_generation", "external_dispatch_key", "semantic_effect_key",
            "dispatch_id", "feature_id", "task_id", "role", "expected_revision", "target_repository",
            "target_ref", "candidate_pr_number", "candidate_head_sha")}
        identity["stage"] = stage
        result = {"schema_version": CONTEXT_SCHEMA, "identity": identity,
            "provenance": {"store_commit_sha": snapshot.ref_sha,
                "producer_source_sha": self.policy_authority.installation_commit_sha,
                "producer_policy_digest": self.policy_authority.bundle_digest.removeprefix("sha256:")},
            "documents": documents}
        result["context_sha256"] = sha256(canonical(result))
        validate_context(result)
        fresh = self.candidate_provider.current_candidate(operation_id=operation_id,
            repository=dispatch["target_repository"], feature_id=dispatch["feature_id"],
            target_ref=dispatch["target_ref"])
        fresh_feature, _ = self.feature_gateway.read_feature(operation_id=operation_id)
        if (fresh.candidate_head_sha != candidate.candidate_head_sha
                or fresh.candidate_pr_number != candidate.candidate_pr_number
                or fresh_feature.manifest_digest != feature.manifest_digest):
            raise VerticalInvariantError("STALE_REVISION", "candidate changed while packaging Gate context")
        if runtime.backend.read_snapshot().ref_sha != snapshot.ref_sha:
            raise VerticalInvariantError("STALE_REVISION", "Store changed while packaging Gate context")
        return result


class DogfoodStructuredGateDispatchGateway(GhAwVerticalRoleDispatchGateway):
    """Unselected preparation adapter; existing one-shot dispatch authority remains external."""

    def __init__(self, *, transport, workflows, context_builder):
        super().__init__(transport=transport, workflows=workflows)
        if not isinstance(context_builder, DogfoodStructuredGateContextBuilder):
            raise V03DogfoodCompositionError("structured Gate requires actual authenticated context builder")
        self.context_builder = context_builder

    def _inputs(self, dispatch):
        from v03_dogfood_gate_output import canonical
        role = dispatch.get("role")
        if role not in STRUCTURED_GATE_WORKFLOWS or self.workflows.workflow_for(role) != STRUCTURED_GATE_WORKFLOWS[role]:
            raise VerticalInvariantError("POLICY_DENIED", "structured context is restricted to unselected Gate workflows")
        inputs = super()._inputs(dispatch)
        payload = json.loads(inputs["task_payload"])
        payload["feature_context"]["gate_context"] = self.context_builder(dispatch)
        encoded = canonical(payload)
        if len(encoded) > 32768:
            raise VerticalInvariantError("BLOCKED", "structured task payload exceeds provider input limit")
        inputs["task_payload"] = encoded.decode("utf-8")
        if len(canonical(inputs)) > 32768:
            raise VerticalInvariantError("BLOCKED", "complete structured dispatch inputs exceed bounded budget")
        return inputs

# Fixed, separately admitted corrected Reviewer. Historical producers remain immutable.
REVIEWER_STRUCTURED_STORE = "2fd1aec70ccd4ae53ec606146d477a17d4a3967b"
REVIEWER_STRUCTURED_PRIOR_SOURCE = "ff2fcfebfceaef2baf4edc2a6de2ab820760d48b"
REVIEWER_STRUCTURED_PRIOR_RUN = 38018044654
REVIEWER_STRUCTURED_PRIOR_KEY = "dispatch-72f9f220eff8e8a1ab1577c22d3d680bb778abf9"
REVIEWER_STRUCTURED_PRIOR_COMMENT = 6092979158
REVIEWER_STRUCTURED_PRIOR_BODY_SHA256 = "6053f28b1c0eab9d4e587b2754366b7a5ac66408b460a3247a6786223bb88fc2"
REVIEWER_STRUCTURED_BASE = f"state/operator/v1/operations/{RECOVERY_OPERATION_ID}/dogfood-reviewer-structured-replacement-3"
REVIEWER_STRUCTURED_AUTH_PATH = REVIEWER_STRUCTURED_BASE + "/authorization.json"
REVIEWER_STRUCTURED_CLAIM_PATH = REVIEWER_STRUCTURED_BASE + "/create-claim.json"
REVIEWER_STRUCTURED_SEAL_PATH = REVIEWER_STRUCTURED_BASE + "/sealed-result.json"
REVIEWER_STRUCTURED_TERMINAL_PATH = REVIEWER_STRUCTURED_BASE + "/terminal-observation.json"
REVIEWER_STRUCTURED_PATHS = (REVIEWER_STRUCTURED_AUTH_PATH, REVIEWER_STRUCTURED_CLAIM_PATH,
    REVIEWER_STRUCTURED_SEAL_PATH, REVIEWER_STRUCTURED_TERMINAL_PATH)
REVIEWER_STRUCTURED_PRIOR_BLOBS = {
    REVIEWER_POST_MODEL_AUTH_PATH: "789007f3e0fa8ae57538237db1cb0301a49b7e5b",
    REVIEWER_POST_MODEL_CLAIM_PATH: "72a617a115d349f9a9b9c18565621917d112d52e",
}
REVIEWER_STRUCTURED_ADMISSION = {
    "uri": "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6094596591",
    "body_digest": "sha256:feae949216f15a3397cdd1e5d9927a76ffe796613a1304cec0a84c0e69046c64",
}

def reviewer_structured_present(snapshot):
    return any(path in snapshot.files for path in REVIEWER_STRUCTURED_PATHS)

def validate_reviewer_structured_predecessor(snapshot, *, fresh=False):
    _, events = validate_reviewer_post_model_predecessor(snapshot, fresh=fresh)
    if (any(_recovery_document_blob(snapshot.get(path)) != blob
            for path, blob in REVIEWER_STRUCTURED_PRIOR_BLOBS.items())
            or REVIEWER_POST_MODEL_SEAL_PATH in snapshot.files):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer predecessor bytes differ")
    prior = snapshot.get(REVIEWER_POST_MODEL_AUTH_PATH)
    if (prior["physical_key"] != REVIEWER_STRUCTURED_PRIOR_KEY
            or prior["consumer_execution_binding"]["execution_source_head_sha"] != REVIEWER_STRUCTURED_PRIOR_SOURCE):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer predecessor producer differs")
    return prior, events

def structured_gate_source_blobs():
    from v03_dogfood_live_gate import STRUCTURED_DOGFOOD_BLOBS, STRUCTURED_GATE_HELPER_BLOB
    return {**{".github/workflows/" + name: blob for name, blob in STRUCTURED_DOGFOOD_BLOBS.items()},
            "scripts/v03_dogfood_gate_output.py": STRUCTURED_GATE_HELPER_BLOB}

def reviewer_structured_authorization(snapshot, *, consumer_binding, predecessor_proof):
    prior, _ = validate_reviewer_structured_predecessor(snapshot)
    identity = {
        "schema_version": "ai-sdlc.v03-reviewer-structured-replacement/v1", "ordinal": 3,
        "admission": REVIEWER_STRUCTURED_ADMISSION, "operation_id": RECOVERY_OPERATION_ID,
        "operation_generation": 1, "predecessor_store_commit": REVIEWER_STRUCTURED_STORE,
        "predecessor_event_blobs": REVIEWER_HISTORY_BLOBS,
        "predecessor_document_blobs": {**REVIEWER_DOCUMENT_BLOBS, **REVIEWER_POST_MODEL_DOCUMENT_BLOBS,
                                     **REVIEWER_STRUCTURED_PRIOR_BLOBS},
        "logical_key": REVIEWER_OLD_KEY, "logical_dispatch_id": REVIEWER_LOGICAL_DISPATCH,
        "semantic_effect_key": REVIEWER_SEMANTIC_KEY, "task_id": REVIEWER_TASK,
        "candidate_head_sha": REVIEWER_CANDIDATE, "candidate_pr_number": 552,
        "expected_revision": 3, "role": "reviewer", "stage": "code-review",
        "workflow_file": STRUCTURED_GATE_WORKFLOWS["reviewer"],
        "failed_run_id": REVIEWER_STRUCTURED_PRIOR_RUN, "failed_physical_key": REVIEWER_STRUCTURED_PRIOR_KEY,
        "prior_consumer_execution_binding": prior["consumer_execution_binding"],
        "consumer_execution_binding": consumer_binding, "worker_blobs": structured_gate_source_blobs(),
        "predecessor_proof": predecessor_proof,
    }
    return _reviewer_complete_authorization(identity)

def _validate_structured_inputs(inputs, dispatch, workflows):
    from v03_dogfood_gate_output import validate_context, strict_json
    if not isinstance(inputs, dict) or any(type(k) is not str or type(v) is not str for k,v in inputs.items()):
        raise VerticalInvariantError("POLICY_DENIED", "frozen Gate inputs must be exact strings")
    # Reuse the shared gateway's complete task/vertical wire grammar.
    class NoTransport: pass
    base = GhAwVerticalRoleDispatchGateway(transport=NoTransport(), workflows=workflows)._inputs(dispatch)
    from copy import deepcopy
    payload = strict_json(inputs["task_payload"].encode("utf-8"), limit=32768)
    stripped = deepcopy(payload)
    context = stripped["feature_context"].pop("gate_context", None)
    if (canonical_json(stripped) != canonical_json(json.loads(base["task_payload"]))
            or {k:v for k,v in inputs.items() if k != "task_payload"}
                != {k:v for k,v in base.items() if k != "task_payload"}
            or len(canonical_json(inputs).encode()) > 32768):
        raise VerticalInvariantError("POLICY_DENIED", "frozen structured task payload differs")
    expected = {key: dispatch[key] for key in (
        "operation_id", "operation_generation", "external_dispatch_key", "semantic_effect_key",
        "dispatch_id", "feature_id", "task_id", "role", "expected_revision",
        "target_repository", "target_ref", "candidate_pr_number", "candidate_head_sha")}
    expected["stage"] = dispatch["feature_stage"]
    validate_context(context, expected)
    return context

def validate_reviewer_structured_authorization(snapshot, *, consumer_binding=None):
    _, events = validate_reviewer_structured_predecessor(snapshot)
    auth, claim = snapshot.get(REVIEWER_STRUCTURED_AUTH_PATH), snapshot.get(REVIEWER_STRUCTURED_CLAIM_PATH)
    if not isinstance(auth, dict) or not isinstance(claim, dict):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer authorization/claim incomplete")
    binding = auth.get("consumer_execution_binding")
    expected = reviewer_structured_authorization(snapshot, consumer_binding=binding,
                                                  predecessor_proof=auth.get("predecessor_proof"))
    if (canonical_json(auth) != canonical_json(expected) or not isinstance(binding, dict)
            or set(binding) != set(recovery_execution_binding_fields())
            or any(not _SHA40.fullmatch(str(binding.get(k) or "")) for k in
                   ("execution_source_head_sha", "execution_materialization_commit_sha"))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(binding.get(k) or "")) for k in
                   ("execution_policy_bundle_digest", "execution_policy_receipt_digest"))
            or binding["execution_source_head_sha"] in {REVIEWER_STRUCTURED_PRIOR_SOURCE,
                REVIEWER_POST_MODEL_SOURCE, REVIEWER_PREDECESSOR_SOURCE, POST_HANDOFF_SOURCE}
            or (consumer_binding is not None and canonical_json(binding) != canonical_json(consumer_binding))):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer authority/source differs")
    proof = auth["predecessor_proof"]
    proof_fixed = {"schema_version": "ai-sdlc.v03-reviewer-structured-predecessor/v1",
        "run_id": REVIEWER_STRUCTURED_PRIOR_RUN, "run_attempt": 1,
        "source_head_sha": REVIEWER_STRUCTURED_PRIOR_SOURCE, "controller_run_id": 38017902256,
        "comment_id": REVIEWER_STRUCTURED_PRIOR_COMMENT,
        "body_sha256": REVIEWER_STRUCTURED_PRIOR_BODY_SHA256, "verdict_inventory": "REWORK", "adopted": False}
    if (not isinstance(proof, dict) or set(proof) != set(proof_fixed) | {"observation_digest"}
            or any(canonical_json(proof.get(k)) != canonical_json(v) for k,v in proof_fixed.items())
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(proof.get("observation_digest") or ""))):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer historical proof differs")
    if (set(claim) != {"schema_version", "ordinal", "authorization_digest", "physical_key",
                       "create_consumed", "preclaim_store_commit", "dispatch_inputs", "dispatch_inputs_digest",
                       "context_digest"}
            or claim["schema_version"] != auth["schema_version"] or type(claim["ordinal"]) is not int
            or claim["ordinal"] != 3 or claim["create_consumed"] is not True
            or claim["physical_key"] != auth["physical_key"]
            or claim["authorization_digest"] != "sha256:" + digest_json(auth)
            or not _SHA40.fullmatch(str(claim["preclaim_store_commit"]))
            or claim["dispatch_inputs_digest"] != "sha256:" + digest_json(claim["dispatch_inputs"])):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer frozen claim differs")
    from v03_dogfood_live_gate import STRUCTURED_DOGFOOD_WORKFLOWS
    workflows = GhAwVerticalWorkflowMap(default_branch="main",
        developer_workflow=STRUCTURED_DOGFOOD_WORKFLOWS["developer"],
        reviewer_workflow=STRUCTURED_DOGFOOD_WORKFLOWS["reviewer"], qa_workflow=STRUCTURED_DOGFOOD_WORKFLOWS["qa"])
    context = _validate_structured_inputs(claim["dispatch_inputs"], reviewer_dispatch(auth), workflows)
    if (context["context_sha256"] != claim["context_digest"]
            or context["provenance"]["store_commit_sha"] != claim["preclaim_store_commit"]
            or context["provenance"]["producer_source_sha"] != binding["execution_source_head_sha"]
            or context["provenance"]["producer_policy_digest"] != binding["execution_policy_bundle_digest"]):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer context provenance differs")
    if any(e["event_type"] == "worker.callback.recorded" and e["payload"].get("external_dispatch_key")
           in {REVIEWER_POST_MODEL_FAILED_KEY, REVIEWER_STRUCTURED_PRIOR_KEY, auth["physical_key"]}
           for e in events[30:]):
        raise VerticalInvariantError("POLICY_DENIED", "corrected Reviewer physical-key callback conflicts")
    if REVIEWER_STRUCTURED_TERMINAL_PATH in snapshot.files:
        if (REVIEWER_STRUCTURED_SEAL_PATH in snapshot.files
                or any(e["event_type"] in {"worker.callback.recorded", "feature.event.translated",
                                         "persist.requested", "dispatch.claimed"} for e in events[30:])):
            raise VerticalInvariantError("POLICY_DENIED", "terminal Reviewer has forbidden follow-on facts")
    else:
        _validate_reviewer_callback_relation(snapshot, auth, events, REVIEWER_STRUCTURED_SEAL_PATH)
    return auth, claim

def plan_reviewer_structured_replacement(snapshot, *, consumer_binding, predecessor_proof, dispatch_inputs):
    if reviewer_structured_present(snapshot):
        auth, claim = validate_reviewer_structured_authorization(snapshot, consumer_binding=consumer_binding)
        return StoreMutationPlan(snapshot.ref_sha, (), {"acquired": False, "authorization": auth, "claim": claim})
    validate_reviewer_structured_predecessor(snapshot, fresh=True)
    auth = reviewer_structured_authorization(snapshot, consumer_binding=consumer_binding,
                                              predecessor_proof=predecessor_proof)
    context = json.loads(dispatch_inputs["task_payload"])["feature_context"]["gate_context"]
    claim = {"schema_version": auth["schema_version"], "ordinal": 3,
        "authorization_digest": "sha256:" + digest_json(auth), "physical_key": auth["physical_key"],
        "create_consumed": True, "preclaim_store_commit": snapshot.ref_sha,
        "dispatch_inputs": dispatch_inputs, "dispatch_inputs_digest": "sha256:" + digest_json(dispatch_inputs),
        "context_digest": context["context_sha256"]}
    from operator_store_model import apply_plan_to_snapshot
    plan = StoreMutationPlan(snapshot.ref_sha, (
        StoreMutation("create_immutable", REVIEWER_STRUCTURED_AUTH_PATH, auth),
        StoreMutation("create_immutable", REVIEWER_STRUCTURED_CLAIM_PATH, claim)),
        {"acquired": True, "authorization": auth, "claim": claim})
    validate_reviewer_structured_authorization(apply_plan_to_snapshot(snapshot, plan), consumer_binding=consumer_binding)
    return plan

def reviewer_structured_terminal(snapshot, *, consumer_binding=None):
    auth, claim = validate_reviewer_structured_authorization(snapshot, consumer_binding=consumer_binding)
    row = snapshot.get(REVIEWER_STRUCTURED_TERMINAL_PATH)
    fields = {"schema_version","ordinal","authorization_digest","claim_digest","run_id","physical_key",
              "outcome","role_payload","proof","execution_binding"}
    if (not isinstance(row,dict) or set(row)!=fields
            or row["schema_version"]!=auth["schema_version"] or type(row["ordinal"]) is not int or row["ordinal"]!=3
            or row["authorization_digest"]!="sha256:"+digest_json(auth)
            or row["claim_digest"]!="sha256:"+digest_json(claim)
            or row["physical_key"]!=auth["physical_key"] or row["execution_binding"]!=auth["consumer_execution_binding"]
            or type(row["run_id"]) is not int or row["run_id"]<=0
            or row["run_id"] in {REVIEWER_FAILED_RUN,REVIEWER_POST_MODEL_FAILED_RUN,REVIEWER_STRUCTURED_PRIOR_RUN}
            or row["outcome"] not in {"REWORK","BLOCKED"}
            or not isinstance(row["role_payload"],dict) or not isinstance(row["proof"],dict)
            or row["role_payload"].get("verdict")!=row["outcome"]
            or row["proof"].get("role_payload_digest")!="sha256:"+digest_json(row["role_payload"])):
        raise VerticalInvariantError("POLICY_DENIED","corrected Reviewer terminal observation differs")
    tail=operation_events(snapshot,RECOVERY_OPERATION_ID)[30:]
    reason="Corrected Reviewer "+row["outcome"]+"; observation sha256:"+digest_json(row)
    if (len(tail)!=1 or tail[0]["event_type"]!="operation.needs-user"
            or tail[0]["operation_generation"]!=1
            or tail[0]["payload"]!={"reason_code":"VERTICAL_NEEDS_USER","summary":reason[:512]}):
        raise VerticalInvariantError("POLICY_DENIED","corrected Reviewer terminal stop differs")
    return row

def plan_reviewer_structured_terminal(snapshot, *, run_id, role_payload, proof, consumer_binding,
                                      occurred_at, trusted_context_digest):
    auth,claim=validate_reviewer_structured_authorization(snapshot,consumer_binding=consumer_binding)
    outcome=role_payload.get("verdict")
    if outcome not in {"REWORK","BLOCKED"}:
        raise VerticalInvariantError("POLICY_DENIED","only authenticated non-PASS may form this terminal observation")
    row={"schema_version":auth["schema_version"],"ordinal":3,
        "authorization_digest":"sha256:"+digest_json(auth),"claim_digest":"sha256:"+digest_json(claim),
        "run_id":run_id,"physical_key":auth["physical_key"],"outcome":outcome,
        "role_payload":role_payload,"proof":proof,"execution_binding":consumer_binding}
    if REVIEWER_STRUCTURED_TERMINAL_PATH in snapshot.files:
        existing=reviewer_structured_terminal(snapshot,consumer_binding=consumer_binding)
        if canonical_json(existing)!=canonical_json(row):
            raise VerticalInvariantError("POLICY_DENIED","corrected Reviewer terminal replay changed")
        return StoreMutationPlan(snapshot.ref_sha,(),{"terminal":existing})
    if REVIEWER_STRUCTURED_SEAL_PATH in snapshot.files or len(operation_events(snapshot,RECOVERY_OPERATION_ID))!=30:
        raise VerticalInvariantError("POLICY_DENIED","non-PASS terminal boundary already progressed")
    from operator_store import plan_needs_user
    from operator_store_model import apply_plan_to_snapshot
    mutation=StoreMutation("create_immutable",REVIEWER_STRUCTURED_TERMINAL_PATH,row)
    provisional=apply_plan_to_snapshot(snapshot,StoreMutationPlan(snapshot.ref_sha,(mutation,),{}))
    stop=plan_needs_user(provisional,operation_id=RECOVERY_OPERATION_ID,generation=1,
        reason_code="VERTICAL_NEEDS_USER",
        summary="Corrected Reviewer "+outcome+"; observation sha256:"+digest_json(row),
        occurred_at=occurred_at,trusted_context_digest=trusted_context_digest)
    plan=StoreMutationPlan(snapshot.ref_sha,(mutation,*stop.mutations),{"terminal":row})
    reviewer_structured_terminal(apply_plan_to_snapshot(snapshot,plan),consumer_binding=consumer_binding)
    return plan

def reviewer_structured_scan(transport, *, physical_key):
    from dataclasses import replace
    workflows=tuple(dict.fromkeys((transport.config.workflows.developer_workflow,
        *STRUCTURED_GATE_WORKFLOWS.values(),REVIEWER_OLD_WORKFLOW,REVIEWER_NEW_WORKFLOW,
        REVIEWER_BOUNDED_WORKFLOW,"ai-sdlc-gh-aw-qa-deepseek-v03-release-local.lock.yml",
        "ai-sdlc-gh-aw-qa-deepseek-v03-bounded-local.lock.yml")))
    expected={REVIEWER_OLD_KEY:(REVIEWER_OLD_WORKFLOW,str(REVIEWER_FAILED_RUN)),
        REVIEWER_POST_MODEL_FAILED_KEY:(REVIEWER_NEW_WORKFLOW,str(REVIEWER_POST_MODEL_FAILED_RUN)),
        REVIEWER_STRUCTURED_PRIOR_KEY:(REVIEWER_BOUNDED_WORKFLOW,str(REVIEWER_STRUCTURED_PRIOR_RUN))}
    results={}
    for key in (*expected,physical_key):
        found=[]
        for workflow in workflows:
            reader=transport if workflow in (transport.config.workflows.developer_workflow,
                transport.config.workflows.reviewer_workflow,transport.config.workflows.qa_workflow) else (
                GitHubActionsVerticalGhAwTransport(replace(transport.config,
                    workflows=replace(transport.config.workflows,reviewer_workflow=workflow)),
                    http=transport.http,sleeper=lambda _:None))
            receipt=reader.lookup(workflow=workflow,ref="main",dispatch_key=key)
            if receipt.get("lookup_state")=="LAUNCHED":
                found.append((workflow,str(receipt.get("receipt_id"))))
            elif receipt.get("lookup_state")!="NOT_LAUNCHED":
                raise VerticalInvariantError("BLOCKED","corrected Reviewer inventory is incomplete")
        results[key]=found
    if any(results[key]!=[pair] for key,pair in expected.items()):
        raise VerticalInvariantError("BLOCKED","corrected Reviewer predecessor inventory differs")
    found=results[physical_key]
    if len(found)>1 or (found and found[0][0]!=STRUCTURED_GATE_WORKFLOWS["reviewer"]):
        raise VerticalInvariantError("BLOCKED","corrected Reviewer execution collides")
    return {"lookup_state":"LAUNCHED" if found else "NOT_LAUNCHED","receipt_id":found[0][1] if found else None}

class DogfoodStructuredReviewerTransport(DogfoodReviewerReplacementTransport):
    def __init__(self,config,*,snapshot,consumer_binding,allow_post,http=None,sleeper=None):
        super().__init__(config,snapshot=snapshot,consumer_binding=consumer_binding,
                         allow_post=allow_post,http=http,sleeper=sleeper)
        self.scan=reviewer_structured_scan
        _,self.claim=validate_reviewer_structured_authorization(snapshot,consumer_binding=consumer_binding)

    def _validate_dispatch_inputs(self,*,workflow,ref,inputs):
        if (workflow!=STRUCTURED_GATE_WORKFLOWS["reviewer"] or ref!="main"
                or canonical_json(inputs)!=canonical_json(self.claim["dispatch_inputs"])):
            raise VerticalInvariantError("POLICY_DENIED","corrected Reviewer POST differs from frozen bytes")
        return GitHubActionsVerticalGhAwTransport._validate_dispatch_inputs(self,workflow=workflow,ref=ref,inputs=inputs)

STRUCTURED_INPUT_RECORD_ADMISSION = {"uri":"https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6094596591","body_digest":"sha256:feae949216f15a3397cdd1e5d9927a76ffe796613a1304cec0a84c0e69046c64"}

def structured_gate_input_path(operation_id, external_dispatch_key):
    if not re.fullmatch(r"op-[0-9a-f]{40}", operation_id) or not re.fullmatch(r"dispatch-[0-9a-f]{40}", external_dispatch_key):
        raise VerticalInvariantError("POLICY_DENIED", "structured input record identity is invalid")
    return f"state/operator/v1/operations/{operation_id}/dogfood-structured-gate-inputs/{external_dispatch_key}.json"

def _structured_dispatch_binding(snapshot, dispatch):
    from v03_dogfood_fixture_pool import SLOTS
    # Closed fixture inventory is checked through its actual slot objects below.
    slots = tuple(SLOTS.values()) if isinstance(SLOTS, dict) else tuple(SLOTS)
    if not any(slot.feature_id == dispatch.get("feature_id") and slot.target_ref == dispatch.get("target_ref")
               for slot in slots):
        raise VerticalInvariantError("POLICY_DENIED", "structured inputs escaped fixed fixture inventory")
    if dispatch.get("role") not in {"reviewer", "qa"}:
        raise VerticalInvariantError("POLICY_DENIED", "structured input record is Gate-only")
    events = operation_events(snapshot, dispatch["operation_id"])
    matches = [e for e in events if e["event_type"] == "dispatch.launch.authorized"
        and e["operation_generation"] == dispatch["operation_generation"]
        and e["payload"].get("external_dispatch_key") == dispatch["external_dispatch_key"]]
    if len(matches) != 1:
        raise VerticalInvariantError("POLICY_DENIED", "structured inputs require existing unique launch authority")
    launch = matches[0]
    for key, field in (("semantic_effect_key","semantic_effect_key"),("dispatch_id","dispatch_id"),
        ("feature_id","feature_id"),("expected_revision","expected_revision"),("role","role"),
        ("stage","feature_stage"),("candidate_head_sha","candidate_head_sha")):
        if canonical_json(launch["payload"].get(key)) != canonical_json(dispatch.get(field)):
            raise VerticalInvariantError("POLICY_DENIED", "structured inputs differ from existing launch")
    reservation = snapshot.get(reservation_path(dispatch["semantic_effect_key"]))
    if (not isinstance(reservation,dict)
            or reservation.get("external_dispatch_key") != dispatch["external_dispatch_key"]
            or not _task_binding_matches(reservation.get("task_identity",""),dispatch.get("task_id",""))):
        raise VerticalInvariantError("POLICY_DENIED", "structured inputs differ from existing task reservation")
    if dispatch["operation_id"] == RECOVERY_OPERATION_ID:
        route = reviewer_replacement_route(snapshot, require_seal=True)
        if route["ordinal"] != 3 or dispatch["role"] != "qa":
            raise VerticalInvariantError("POLICY_DENIED", "happy follow-on dispatch requires corrected Reviewer PASS")
    return launch

def validate_structured_gate_input_record(snapshot, *, operation_id, external_dispatch_key, consumer_binding):
    document = snapshot.get(structured_gate_input_path(operation_id,external_dispatch_key))
    fields = {"admission","schema_version","operation_id","operation_generation","external_dispatch_key","dispatch",
        "launch_event_id","launch_event_digest","preclaim_store_commit","consumer_execution_binding",
        "source_blobs","dispatch_inputs","dispatch_inputs_digest","context_digest"}
    if not isinstance(document,dict) or set(document)!=fields:
        raise VerticalInvariantError("POLICY_DENIED","structured input record missing/malformed")
    dispatch=document["dispatch"]
    launch=_structured_dispatch_binding(snapshot,dispatch)
    if (document["admission"] != STRUCTURED_INPUT_RECORD_ADMISSION or "PENDING" in STRUCTURED_INPUT_RECORD_ADMISSION["uri"]
            or document["schema_version"]!="ai-sdlc.v03-structured-gate-inputs/v1"
            or document["operation_id"]!=operation_id or document["external_dispatch_key"]!=external_dispatch_key
            or dispatch["operation_id"]!=operation_id or dispatch["external_dispatch_key"]!=external_dispatch_key
            or type(document["operation_generation"]) is not int or document["operation_generation"]!=dispatch["operation_generation"]
            or document["launch_event_id"]!=launch["event_id"]
            or document["launch_event_digest"]!="sha256:"+digest_json(launch)
            or document["consumer_execution_binding"]!=consumer_binding
            or document["source_blobs"]!=structured_gate_source_blobs()
            or not _SHA40.fullmatch(str(document["preclaim_store_commit"]))
            or document["dispatch_inputs_digest"]!="sha256:"+digest_json(document["dispatch_inputs"])):
        raise VerticalInvariantError("POLICY_DENIED","structured input record protected binding differs")
    from v03_dogfood_live_gate import STRUCTURED_DOGFOOD_WORKFLOWS
    workflows=GhAwVerticalWorkflowMap(default_branch="main",
        developer_workflow=STRUCTURED_DOGFOOD_WORKFLOWS["developer"],
        reviewer_workflow=STRUCTURED_DOGFOOD_WORKFLOWS["reviewer"],
        qa_workflow=STRUCTURED_DOGFOOD_WORKFLOWS["qa"])
    context=_validate_structured_inputs(document["dispatch_inputs"],dispatch,workflows)
    if (context["context_sha256"]!=document["context_digest"]
            or context["provenance"]["store_commit_sha"]!=document["preclaim_store_commit"]
            or context["provenance"]["producer_source_sha"]!=consumer_binding["execution_source_head_sha"]
            or context["provenance"]["producer_policy_digest"]!=consumer_binding["execution_policy_bundle_digest"]):
        raise VerticalInvariantError("POLICY_DENIED","structured input context provenance differs")
    return document

def plan_structured_gate_inputs(snapshot, *, dispatch, inputs, consumer_binding):
    path=structured_gate_input_path(dispatch["operation_id"],dispatch["external_dispatch_key"])
    if path in snapshot.files:
        row=validate_structured_gate_input_record(snapshot,operation_id=dispatch["operation_id"],
            external_dispatch_key=dispatch["external_dispatch_key"],consumer_binding=consumer_binding)
        if canonical_json(row["dispatch"])!=canonical_json(dispatch):
            raise VerticalInvariantError("POLICY_DENIED","structured input replay dispatch differs")
        return StoreMutationPlan(snapshot.ref_sha,(),{"record":row})
    launch=_structured_dispatch_binding(snapshot,dispatch)
    context=json.loads(inputs["task_payload"])["feature_context"]["gate_context"]
    row={"admission":STRUCTURED_INPUT_RECORD_ADMISSION,"schema_version":"ai-sdlc.v03-structured-gate-inputs/v1","operation_id":dispatch["operation_id"],
        "operation_generation":dispatch["operation_generation"],"external_dispatch_key":dispatch["external_dispatch_key"],
        "dispatch":dispatch,"launch_event_id":launch["event_id"],"launch_event_digest":"sha256:"+digest_json(launch),
        "preclaim_store_commit":snapshot.ref_sha,"consumer_execution_binding":consumer_binding,
        "source_blobs":structured_gate_source_blobs(),"dispatch_inputs":inputs,
        "dispatch_inputs_digest":"sha256:"+digest_json(inputs),"context_digest":context["context_sha256"]}
    from operator_store_model import apply_plan_to_snapshot
    plan=StoreMutationPlan(snapshot.ref_sha,(StoreMutation("create_immutable",path,row),),{"record":row})
    validate_structured_gate_input_record(apply_plan_to_snapshot(snapshot,plan),operation_id=dispatch["operation_id"],
        external_dispatch_key=dispatch["external_dispatch_key"],consumer_binding=consumer_binding)
    return plan

class DogfoodCurrentStructuredDispatchGateway(DogfoodStructuredGateDispatchGateway):
    """Existing protected claims remain the only ordinary dispatch authority."""
    def _inputs(self,dispatch):
        if dispatch["role"]=="developer":
            if dispatch["operation_id"]==RECOVERY_OPERATION_ID:
                raise VerticalInvariantError("POLICY_DENIED","corrected happy route authorizes no Developer")
            return GhAwVerticalRoleDispatchGateway._inputs(self,dispatch)
        runtime=self.context_builder.runtime
        binding=recovery_execution_binding(self.context_builder.policy_authority)
        snapshot=runtime.backend.read_snapshot()
        if dispatch["operation_id"]==RECOVERY_OPERATION_ID and dispatch["role"]=="reviewer":
            auth,claim=validate_reviewer_structured_authorization(snapshot,consumer_binding=binding)
            if canonical_json(dispatch)!=canonical_json(reviewer_dispatch(auth)):
                raise VerticalInvariantError("POLICY_DENIED","happy Reviewer differs from fixed corrected claim")
            return dict(claim["dispatch_inputs"])
        path=structured_gate_input_path(dispatch["operation_id"],dispatch["external_dispatch_key"])
        if path not in snapshot.files:
            raise VerticalInvariantError("POLICY_DENIED","structured dispatch inputs were not frozen")
        row=validate_structured_gate_input_record(snapshot,operation_id=dispatch["operation_id"],
            external_dispatch_key=dispatch["external_dispatch_key"],consumer_binding=binding)
        if canonical_json(row["dispatch"])!=canonical_json(dispatch):
            raise VerticalInvariantError("POLICY_DENIED","structured dispatch replay identity differs")
        return dict(row["dispatch_inputs"])

    def launch(self,*,dispatch):
        authority = getattr(self, "remediation_rereview_authority", None)
        if authority is not None:
            authority.validate_dispatch(dispatch)
        if dispatch["role"]!="developer" and not (dispatch["operation_id"]==RECOVERY_OPERATION_ID
                                                 and dispatch["role"]=="reviewer"):
            runtime=self.context_builder.runtime
            binding=recovery_execution_binding(self.context_builder.policy_authority)
            from v03_dogfood_runtime_driver import _commit_recovery_nonempty
            def freeze(snapshot):
                path=structured_gate_input_path(dispatch["operation_id"],dispatch["external_dispatch_key"])
                if path in snapshot.files:
                    return plan_structured_gate_inputs(snapshot,dispatch=dispatch,inputs=None,consumer_binding=binding)
                inputs=DogfoodStructuredGateDispatchGateway._inputs(self,dispatch)
                if runtime.backend.read_snapshot().ref_sha!=snapshot.ref_sha:
                    raise VerticalInvariantError("BLOCKED","Store changed during structured input preparation")
                return plan_structured_gate_inputs(snapshot,dispatch=dispatch,inputs=inputs,consumer_binding=binding)
            _commit_recovery_nonempty(runtime,freeze)
        return super().launch(dispatch=dispatch)

def _structured_metadata_suffix(workflow, run):
    """Exact pinned official metadata, derived from the reviewed compiled environment."""
    from pathlib import Path
    import yaml
    from v03_dogfood_live_gate import STRUCTURED_DOGFOOD_BLOBS
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / workflow
    raw = path.read_bytes()
    if hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest() != STRUCTURED_DOGFOOD_BLOBS[workflow]:
        raise VerticalInvariantError("POLICY_DENIED", "structured metadata source bytes differ")
    config = yaml.safe_load(raw)
    env = config["jobs"]["safe_outputs"]["env"]
    repository = str(run["html_url"]).split("/actions/runs/")[0].removeprefix("https://github.com/")
    if repository.lower() != "dream-xin/ai-sdlc":
        raise VerticalInvariantError("POLICY_DENIED", "structured metadata repository differs")
    workflow_id = workflow.removesuffix(".lock.yml")
    if (env["GH_AW_WORKFLOW_ID"] != workflow_id or env["GH_AW_ENGINE_ID"] != "copilot"
            or env["GH_AW_ENGINE_MODEL"] != "deepseek-chat"
            or env["GH_AW_CALLER_WORKFLOW_ID"] != "${{ github.repository }}/" + workflow_id
            or any(env.get(k) for k in ("GH_AW_ENGINE_VERSION", "GH_AW_TRACKER_ID"))):
        raise VerticalInvariantError("POLICY_DENIED", "structured metadata policy differs")
    return ("<!-- gh-aw-agentic-workflow: " + env["GH_AW_WORKFLOW_NAME"]
            + ", engine: copilot, model: deepseek-chat, id: " + str(run["id"])
            + ", workflow_id: " + workflow_id + ", run: " + run["html_url"] + " -->\n"
            + "<!-- gh-aw-workflow-call-id: " + repository + "/" + workflow_id + " -->")

class DogfoodStructuredGateResultSource(DogfoodReviewerReplacementSource):
    """Visible format is admitted only through exact current structured source and frozen inputs."""

    def _structured_inputs(self, operation_id, key):
        snapshot = self.reviewer_runtime.backend.read_snapshot()
        binding = recovery_execution_binding(self.reviewer_policy_authority)
        if operation_id == RECOVERY_OPERATION_ID and reviewer_structured_present(snapshot):
            auth, claim = validate_reviewer_structured_authorization(snapshot, consumer_binding=binding)
            if key == auth["physical_key"]:
                return claim["dispatch_inputs"]
        return validate_structured_gate_input_record(snapshot, operation_id=operation_id,
            external_dispatch_key=key, consumer_binding=binding)["dispatch_inputs"]

    def _gate_observation(self, *, values, run_id, workflow, trusted):
        if workflow not in STRUCTURED_GATE_WORKFLOWS.values():
            return super()._gate_observation(values=values, run_id=run_id, workflow=workflow, trusted=trusted)
        from operator_vertical_gh_aw_github_source import TargetScopedGitHubActionsGhAwResultSource
        observed = TargetScopedGitHubActionsGhAwResultSource._gate_observation(
            self, values=values, run_id=run_id, workflow=workflow, trusted=trusted)
        inputs = self._structured_inputs(trusted["operation_id"], trusted["external_dispatch_key"])
        if (self._one(values, "TASK_PAYLOAD") != inputs["task_payload"]
                or self._one(values, "DISPATCH_KEY") != inputs["dispatch_key"]):
            raise VerticalInvariantError("POLICY_DENIED", "structured Gate logged inputs differ from protected bytes")
        context = json.loads(inputs["task_payload"])["feature_context"]["gate_context"]
        identity = context["identity"]
        for field, key in (("operation_id", "operation_id"), ("operation_generation", "operation_generation"),
                           ("external_dispatch_key", "external_dispatch_key"), ("role", "role"),
                           ("expected_revision", "expected_revision"), ("stage", "feature_stage"),
                           ("candidate_head_sha", "launch_candidate_head_sha")):
            if canonical_json(identity[field]) != canonical_json(trusted[key]):
                raise VerticalInvariantError("POLICY_DENIED", "structured Gate context differs from callback task")
        run = self._json(self.config.control_repository, f"/actions/runs/{run_id}", self.config.control_token)
        self._structured_parse = (context, identity["role"], _structured_metadata_suffix(workflow, run))
        return observed

    def _gate_payload(self, body):
        active = getattr(self, "_structured_parse", None)
        if active is None:
            return super()._gate_payload(body)
        from v03_dogfood_gate_output import parse_published_gate, GateOutputContractError
        try:
            return parse_published_gate(body, *active)
        except GateOutputContractError as exc:
            raise VerticalInvariantError("BLOCKED", "structured Gate published contract differs") from exc

    def _gate_content_and_binding(self, *, comment, role):
        if getattr(self, "_structured_parse", None) is not None:
            created, updated = comment.get("created_at"), comment.get("updated_at")
            if (not isinstance(created, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created)
                    or updated != created or (comment.get("user") or {}).get("login") != "github-actions[bot]"
                    or (comment.get("user") or {}).get("type") != "Bot"
                    or type((comment.get("user") or {}).get("id")) is not int
                    or (comment.get("user") or {}).get("id") != 41898282):
                raise VerticalInvariantError("POLICY_DENIED", "structured Gate publication was edited or has another author")
        return super()._gate_content_and_binding(comment=comment, role=role)

    def resolve(self, *, external_dispatch_key, expected_receipt_identity, trusted_context):
        self._structured_parse = None
        try:
            if trusted_context.get("operation_id") != RECOVERY_OPERATION_ID:
                return self._resolve_local_gate(external_dispatch_key=external_dispatch_key,
                    expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
            return super().resolve(external_dispatch_key=external_dispatch_key,
                expected_receipt_identity=expected_receipt_identity, trusted_context=trusted_context)
        finally:
            self._structured_parse = None

    def load_content(self, uri):
        self._structured_parse = None
        match = _FIRST_ATTEMPT_URI_RE.fullmatch(str(uri or ""))
        if match:
            run = self._json(self.config.control_repository, f"/actions/runs/{match.group('run')}", self.config.control_token)
            workflow = self._workflow_file(run)
            if workflow in STRUCTURED_GATE_WORKFLOWS.values():
                snapshot = self.reviewer_runtime.backend.read_snapshot()
                # Identify exactly one protected context by key, never by model-supplied text.
                key = match.group("key")
                candidates = []
                if (str(uri).startswith("docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/")
                        and reviewer_structured_present(snapshot)):
                    auth, claim = validate_reviewer_structured_authorization(snapshot,
                        consumer_binding=recovery_execution_binding(self.reviewer_policy_authority))
                    if key == auth["physical_key"]:
                        candidates.append(claim["dispatch_inputs"])
                for path in snapshot.files:
                    if path.endswith("/dogfood-structured-gate-inputs/" + key + ".json"):
                        document = snapshot.get(path)
                        candidates.append(validate_structured_gate_input_record(snapshot,
                            operation_id=document["operation_id"], external_dispatch_key=key,
                            consumer_binding=recovery_execution_binding(self.reviewer_policy_authority))["dispatch_inputs"])
                if len(candidates) != 1:
                    raise VerticalInvariantError("POLICY_DENIED", "structured content lacks unique protected inputs")
                context = json.loads(candidates[0]["task_payload"])["feature_context"]["gate_context"]
                self._structured_parse = (context, context["identity"]["role"], _structured_metadata_suffix(workflow, run))
        try:
            if not str(uri).startswith("docs/features/F-OPERATOR-V03-DOGFOOD-HAPPY-0001/"):
                return self._load_local_gate(uri)
            return super().load_content(uri)
        finally:
            self._structured_parse = None

    def _reviewer_jobs(self, run_id, source_sha):
        doc = super()._reviewer_jobs(run_id, source_sha)
        run = self._json(self.config.control_repository, f"/actions/runs/{run_id}", self.config.control_token)
        if self._workflow_file(run) in STRUCTURED_GATE_WORKFLOWS.values():
            required = {"agent": ("Prepare authenticated Gate context before model",
                                  "Render validated structured Gate output before detection"),
                "detection": ("Verify Gate detector input bytes before scanning",
                              "Verify Gate detector input bytes after scanning",
                              "Upload immutable Gate scan byte receipt"),
                "safe_outputs": ("Prepare exclusive Gate receipt download directory",
                                 "Download exact current-run Gate scan byte receipt",
                                 "Verify scanned Gate bytes before publication")}
            for job, names in required.items():
                rows = [row for row in doc["jobs"] if row["name"] == job]
                for name in names:
                    steps = [step for step in rows[0]["steps"] if step["name"] == name]
                    if len(steps) != 1 or steps[0].get("status") != "completed" or steps[0].get("conclusion") != "success":
                        raise VerticalInvariantError("BLOCKED", "structured Gate trusted context/render/scan guard failed")
        return doc


DOGFOOD_REREVIEW_EVIDENCE_REF = "dogfood:v03:review-remediation:rereview:1"
DOGFOOD_REREVIEW_CAPABILITY_ID = "ai-sdlc:v03:review-remediation:rereview:1"
DOGFOOD_REREVIEW_SUFFIX = "/dogfood-remediation-rereview-1/"
DOGFOOD_REREVIEW_ADMISSION = {
    "uri": "https://github.com/DREAM-XIN/ai-sdlc/issues/239#issuecomment-6097262300",
    "body_digest": "sha256:010998679c1c9a92941acd85da22a9365a3148e77699c21abdc3081719f016cf",
}


def build_dogfood_rereview_capability(*, installation_commit_sha):
    from v03_dogfood_fixture_pool import require_slot
    if not isinstance(installation_commit_sha, str) or not _SHA40.fullmatch(installation_commit_sha):
        raise VerticalInvariantError("POLICY_DENIED", "rereview capability lacks exact installation")
    slot = require_slot("review_remediation")
    return {
        "schema_version": "ai-sdlc.v03-remediation-rereview-capability/v1",
        "type": "DOGFOOD_REMEDIATION_REREVIEW_CAPABILITY",
        "capability_id": DOGFOOD_REREVIEW_CAPABILITY_ID,
        "repository": "dream-xin/ai-sdlc",
        "state_ref": "refs/heads/ai-sdlc-operator-state",
        "operation_profile": VERTICAL_PROFILE,
        "scenario": slot.scenario, "feature_id": slot.feature_id, "target_ref": slot.target_ref,
        "successor_step": "CODE_REREVIEW", "role": "reviewer", "max_rereviews": 1,
        "initial_verdict": "REWORK", "terminal_nonpass": ["REWORK", "BLOCKED"],
        "installation_commit_sha": installation_commit_sha,
        "admission": dict(DOGFOOD_REREVIEW_ADMISSION),
    }


def verify_dogfood_rereview_capability(*, policy_authority):
    from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
    base = policy_authority.resolution_policy_verifier
    if not isinstance(base, ProtectedEffectResolutionPolicyVerifier):
        raise VerticalInvariantError("POLICY_DENIED", "rereview lacks existing protected policy verifier")
    current = base.verify_current()
    expected = build_dogfood_rereview_capability(
        installation_commit_sha=policy_authority.installation_commit_sha)
    raw = base.policy_loader(base.repository, base.state_ref, base.operation_profile)
    observed = base.evidence_fact_loader(current.evidence_verifier.source_id, DOGFOOD_REREVIEW_EVIDENCE_REF)
    if (base.repository != expected["repository"] or base.state_ref != expected["state_ref"]
            or base.operation_profile != VERTICAL_PROFILE
            or canonical_json(observed) != canonical_json(expected)
            or raw.get("strong_evidence_types") != []
            or current.evidence_verifier.strong_evidence_types
            or current.evidence_verifier.source_digest != digest_json({DOGFOOD_REREVIEW_EVIDENCE_REF: expected})
            or raw.get("trusted_profile_digest") != digest_json({
                "installation_commit_sha": policy_authority.installation_commit_sha,
                "operation_profile": VERTICAL_PROFILE})
            or "RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN" not in current.authority.allowed_choices):
        raise VerticalInvariantError("POLICY_DENIED", "protected scoped rereview capability differs")
    return expected



def dogfood_rereview_paths(operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r"op-[0-9a-f]{40}", operation_id):
        raise VerticalInvariantError("POLICY_DENIED", "invalid rereview operation identity")
    base = "state/operator/v1/operations/" + operation_id + DOGFOOD_REREVIEW_SUFFIX
    return base + "binding.json", base + "terminal-observation.json"


def _dogfood_rereview_global_rows(snapshot):
    if not isinstance(snapshot.ref_sha, str) or not _SHA40.fullmatch(snapshot.ref_sha):
        raise VerticalInvariantError("POLICY_DENIED", "rereview requires an existing complete protected Store")
    rows = [(path, value) for path, value in snapshot.files.items()
            if DOGFOOD_REREVIEW_SUFFIX in path]
    for path, value in rows:
        match = re.fullmatch(r"state/operator/v1/operations/(op-[0-9a-f]{40})"
                            r"/dogfood-remediation-rereview-1/(binding|terminal-observation)\.json", path)
        if (match is None or not isinstance(value, dict)
                or value.get("capability_id") != DOGFOOD_REREVIEW_CAPABILITY_ID
                or value.get("operation_id") != match.group(1)):
            raise VerticalInvariantError("POLICY_DENIED", "malformed global rereview consumption history")
    bindings = [(path, row) for path, row in rows if path.endswith("/binding.json")]
    terminals = [(path, row) for path, row in rows if path.endswith("/terminal-observation.json")]
    if len(bindings) > 1 or len(terminals) > 1 or (terminals and not bindings):
        raise VerticalInvariantError("POLICY_DENIED", "rereview capability has ambiguous global consumption")
    if terminals and terminals[0][1]["operation_id"] != bindings[0][1]["operation_id"]:
        raise VerticalInvariantError("POLICY_DENIED", "rereview terminal belongs to another operation")
    return bindings, terminals


def validate_dogfood_rereview_binding(snapshot, *, operation_id, consumer_binding):
    from operator_effect_lineage_model import resolution_path, lineage_members, lineage_proposals
    from operator_vertical_store import vertical_projection
    bindings, terminals = _dogfood_rereview_global_rows(snapshot)
    if not bindings:
        return None
    path, row = bindings[0]
    required = {"schema_version", "capability_id", "admission", "operation_id", "operation_generation",
                "capability_digest", "consumer_execution_binding", "source_blobs", "proof", "proof_digest",
                "resolution_id", "successor_semantic_effect_key", "successor_external_dispatch_key"}
    if (set(row) != required or path != dogfood_rereview_paths(operation_id)[0]
            or row["schema_version"] != "ai-sdlc.v03-remediation-rereview-binding/v1"
            or row["operation_id"] != operation_id
            or type(row["operation_generation"]) is not int
            or row["admission"] != DOGFOOD_REREVIEW_ADMISSION
            or canonical_json(row["consumer_execution_binding"]) != canonical_json(consumer_binding)
            or canonical_json(row["source_blobs"]) != canonical_json(structured_gate_source_blobs())
            or row["capability_digest"] != digest_json(build_dogfood_rereview_capability(
                installation_commit_sha=consumer_binding["execution_source_head_sha"]))):
        raise VerticalInvariantError("POLICY_DENIED", "rereview binding is foreign or changed")
    proof = row["proof"]
    proof_keys = {"operation_id", "operation_generation", "event_count", "event_prefix_digest", "feature",
                  "lineage_id", "proposal", "review", "remediation", "supersession_event_id",
                  "capability_digest", "consumer_execution_binding"}
    if (not isinstance(proof, dict) or set(proof) != proof_keys
            or row["proof_digest"] != digest_json(proof)
            or proof["operation_id"] != operation_id
            or proof["operation_generation"] != row["operation_generation"]
            or proof["capability_digest"] != row["capability_digest"]
            or canonical_json(proof["consumer_execution_binding"]) != canonical_json(consumer_binding)
            or type(proof["event_count"]) is not int or proof["event_count"] < 1):
        raise VerticalInvariantError("POLICY_DENIED", "rereview proof descriptor differs")
    events = operation_events(snapshot, operation_id)
    projection = vertical_projection(snapshot, operation_id)
    if (projection["generation"] != row["operation_generation"]
            or len(events) < proof["event_count"]
            or digest_json(events[:proof["event_count"]]) != proof["event_prefix_digest"]):
        raise VerticalInvariantError("POLICY_DENIED", "rereview predecessor history changed")
    proposal = proof["proposal"]
    if (not isinstance(proposal, dict)
            or proposal.get("operation_id") != operation_id
            or proposal.get("operation_generation") != row["operation_generation"]
            or proposal.get("effect_lineage_id") != proof["lineage_id"]
            or proposal.get("proposed_semantic_effect_key") != row["successor_semantic_effect_key"]):
        raise VerticalInvariantError("POLICY_DENIED", "rereview successor descriptor changed")
    resolution = snapshot.get(resolution_path(proof["lineage_id"], row["resolution_id"]))
    member = lineage_members(snapshot, proof["lineage_id"]).get(row["successor_semantic_effect_key"])
    if (not isinstance(resolution, dict) or not isinstance(member, dict)
            or resolution.get("choice") != "RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN"
            or resolution.get("successor_proposal_id") != proposal.get("proposal_id")
            or resolution.get("successor_proposed_semantic_effect_key") != row["successor_semantic_effect_key"]
            or resolution.get("current_operation_id") != operation_id
            or resolution.get("current_operation_generation") != row["operation_generation"]
            or member.get("external_dispatch_key") != row["successor_external_dispatch_key"]
            or member.get("activated_from_proposal_id") != proposal.get("proposal_id")):
        raise VerticalInvariantError("POLICY_DENIED", "rereview binding lacks its exact shared resolution")
    stored_proposal = lineage_proposals(snapshot, proof["lineage_id"]).get(proposal.get("proposal_id"))
    feature = proof["feature"]
    evidence = resolution.get("evidence")
    if (canonical_json(stored_proposal) != canonical_json(proposal)
            or not isinstance(feature, dict)
            or resolution.get("target_repository") != feature.get("repository")
            or resolution.get("feature_id") != feature.get("feature_id")
            or resolution.get("current_feature_revision") != feature.get("revision")
            or resolution.get("current_target_ref") != feature.get("target_ref")
            or resolution.get("current_candidate_head_sha") != feature.get("candidate_head_sha")
            or not isinstance(evidence, list) or len(evidence) != 1):
        raise VerticalInvariantError("POLICY_DENIED", "rereview frozen tuple differs from shared resolution")
    fact = {"type": "NON_OVERLAPPING_SCOPE", "proof_digest": row["proof_digest"],
            "capability_id": DOGFOOD_REREVIEW_CAPABILITY_ID}
    item = evidence[0]
    ref = DOGFOOD_REREVIEW_EVIDENCE_REF + "#" + row["proof_digest"]
    if (not isinstance(item, dict)
            or set(item) != set(fact) | {"evidence_ref", "trusted_source_id", "trusted_source_digest", "evidence_digest"}
            or any(item.get(key) != value for key, value in fact.items())
            or item.get("evidence_ref") != ref
            or item.get("evidence_digest") != digest_json({
                "evidence_ref": ref, "trusted_source_id": item.get("trusted_source_id"),
                "trusted_source_digest": item.get("trusted_source_digest"), "fact": fact})
            or resolution.get("evidence_digests") != [item.get("evidence_digest")]):
        raise VerticalInvariantError("POLICY_DENIED", "rereview proof is not the consumed shared evidence")
    if terminals:
        terminal = terminals[0][1]
        fields = {"schema_version", "capability_id", "operation_id", "operation_generation",
                  "binding_digest", "callback_id", "context", "worker_payload_digest",
                  "receipt", "content_sha256", "verdict"}
        if (set(terminal) != fields
                or terminal["schema_version"] != "ai-sdlc.v03-remediation-rereview-terminal/v1"
                or terminal["operation_generation"] != row["operation_generation"]
                or terminal["binding_digest"] != digest_json(row)
                or terminal["verdict"] not in {"REWORK", "BLOCKED"}):
            raise VerticalInvariantError("POLICY_DENIED", "rereview terminal descriptor changed")
        stops = [event for event in events if event["event_type"] == "operation.needs-user"
                 and event["payload"].get("summary") == "Rereview " + terminal["verdict"]
                    + "; observation sha256:" + digest_json(terminal)]
        if len(stops) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "rereview terminal lacks atomic stable stop")
    return row



class DogfoodRemediationRereviewAuthority:
    """One protected, result-bound rereview capability; no generic strong evidence."""

    def __init__(self, *, executor, candidate_provider, content_loader, policy_authority):
        self.executor = executor
        self.runtime = executor.runtime
        self.candidate_provider = candidate_provider
        self.content_loader = content_loader
        self.policy_authority = policy_authority
        if (candidate_provider.slot.scenario != "review_remediation"
                or candidate_provider.runtime is not self.runtime
                or content_loader.runtime is not self.runtime):
            raise VerticalInvariantError("POLICY_DENIED", "rereview authority is outside its frozen runtime")

    def _fresh_capability(self, snapshot):
        import base64
        from v03_dogfood_gate_output import strict_json
        _dogfood_rereview_global_rows(snapshot)
        self.runtime.protected_receipt()
        if self.runtime.backend.read_snapshot().ref_sha != snapshot.ref_sha:
            raise VerticalInvariantError("STALE_REVISION", "rereview Store changed before policy verification")
        capability = verify_dogfood_rereview_capability(policy_authority=self.policy_authority)
        base = self.policy_authority.resolution_policy_verifier
        current = base.verify_current()
        expected_policy = base.policy_loader(base.repository, base.state_ref, base.operation_profile)
        expected_evidence = {"source_id": current.evidence_verifier.source_id,
            "source_digest": current.evidence_verifier.source_digest,
            "facts": {DOGFOOD_REREVIEW_EVIDENCE_REF: capability}}
        provider = self.candidate_provider
        for name, expected in (("effect-resolution-policy.json", expected_policy),
                               ("effect-resolution-evidence.json", expected_evidence)):
            path = "config/operator/v03-vertical-policy/" + name
            url = provider.api_base + "/repos/" + provider.repository + "/contents/" + path + "?ref=" + snapshot.ref_sha
            status, item = provider.http_get(url, provider._headers())
            if (status != 200 or not isinstance(item, dict) or item.get("type") != "file"
                    or item.get("encoding") != "base64"):
                raise VerticalInvariantError("POLICY_DENIED", "current rereview policy bytes unavailable")
            try:
                raw = base64.b64decode(item.get("content", ""), validate=False)
                value = strict_json(raw)
            except Exception as exc:
                raise VerticalInvariantError("POLICY_DENIED", "current rereview policy encoding differs") from exc
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\x00" + raw).hexdigest()
            if (len(raw) > 32768 or item.get("sha") != blob
                    or canonical_json(value) != canonical_json(expected)):
                raise VerticalInvariantError("POLICY_DENIED", "current rereview policy or scoped facts changed")
        if self.runtime.backend.read_snapshot().ref_sha != snapshot.ref_sha:
            raise VerticalInvariantError("STALE_REVISION", "rereview Store changed during policy verification")
        return capability, current

    def _producer(self, snapshot, callback, *, role):
        from v03_dogfood_gate_output import strict_json
        operation_id = callback["operation_id"]
        events = operation_events(snapshot, operation_id)
        callback_id = callback["payload"]["callback_id"]
        envelope = recover_vertical_callback(snapshot, operation_id=operation_id, callback_id=callback_id)
        context = envelope["trusted_context"]
        outputs = envelope["collected_outputs"]
        if (context.get("role") != role or context.get("operation_id") != operation_id
                or context.get("operation_generation") != callback["operation_generation"]
                or len(outputs) != 1):
            raise VerticalInvariantError("POLICY_DENIED", "rereview producer context differs")
        from operator_external_create_attempt import (
            external_create_attempt_path, find_external_create_attempt)
        attempt_path = external_create_attempt_path(context["semantic_effect_key"])
        attempt = find_external_create_attempt(snapshot,
            external_dispatch_key=context["external_dispatch_key"])
        if (attempt_path not in snapshot.files or not isinstance(snapshot.files[attempt_path], dict)
                or attempt is None
                or canonical_json(snapshot.files[attempt_path]) != canonical_json(attempt)
                or attempt.get("created_operation_id") != operation_id
                or attempt.get("created_generation") != context["operation_generation"]
                or attempt.get("semantic_effect_key") != context["semantic_effect_key"]
                or attempt.get("creator_dispatch_id") != context["dispatch_id"]
                or attempt.get("execution_binding", {}).get("role") != role):
            raise VerticalInvariantError("POLICY_DENIED", "rereview producer lacks consumed one-shot authority")
        accepted = [row for row in events if row["event_type"] == "worker.result.validated"
                    and row["payload"].get("callback_id") == callback_id]
        rejected = [row for row in events if row["event_type"] == "worker.result.rejected"
                    and row["payload"].get("callback_id") == callback_id]
        if len(accepted) != 1 or rejected:
            raise VerticalInvariantError("POLICY_DENIED", "rereview evidence was not uniquely accepted")
        output = outputs[0]
        lease = _FIRST_ATTEMPT_URI_RE.fullmatch(str(output.get("trusted_uri") or ""))
        if (lease is None or context.get("runtime_receipt_identity") != lease.group("run")
                or context.get("worker_identity", "").rsplit("@", 1)[-1] != lease.group("head")):
            raise VerticalInvariantError("POLICY_DENIED", "rereview producer lease identity differs")
        content = self.content_loader(output["trusted_uri"])
        if (type(content) is not bytes or len(content) != output.get("size_bytes")
                or hashlib.sha256(content).hexdigest() != output.get("sha256")):
            raise VerticalInvariantError("POLICY_DENIED", "rereview producer content changed")
        translated = [row for row in events if row["event_type"] == "feature.event.translated"
            and row["payload"].get("callback_id") == callback_id
            and any(change.get("record", {}).get("uri") == output["trusted_uri"]
                    for change in row["payload"].get("feature_event", {}).get("changes", []))]
        if len(translated) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "rereview producer lacks canonical translation")
        event_id = translated[0]["payload"]["feature_event_id"]
        confirmed = [row for row in events if row["event_type"] == "persist.confirmed"
                     and row["payload"].get("feature_event_id") == event_id]
        if (len(confirmed) != 1 or not callback["sequence"] < accepted[0]["sequence"]
                < translated[0]["sequence"] < confirmed[0]["sequence"]):
            raise VerticalInvariantError("POLICY_DENIED", "rereview producer Persist ordering differs")
        receipt = self.executor.persist_gateway.lookup_feature_event(
            event_id=event_id, target_ref=context["target_ref"])
        if receipt != {"event_id": event_id, "result_revision": confirmed[0]["payload"]["result_revision"]}:
            raise VerticalInvariantError("POLICY_DENIED", "rereview canonical producer Persist unavailable")
        verdict = strict_json(content).get("verdict") if role == "reviewer" else None
        return {"callback_id": callback_id, "callback_event_id": callback["event_id"],
            "validation_event_id": accepted[0]["event_id"], "translation_event_id": translated[0]["event_id"],
            "persist_event_id": confirmed[0]["event_id"], "context": context,
            "receipt": output, "content_sha256": hashlib.sha256(content).hexdigest(),
            "source_head_sha": lease.group("head"), "run_id": int(lease.group("run")),
            "verdict": verdict, "external_create_attempt_digest": digest_json(attempt)}

    def _proof(self, snapshot, *, operation_id, capability, current_policy):
        from operator_vertical_store import vertical_projection
        from operator_vertical_controller import select_vertical_action
        from operator_effect_lineage_model import rebuild_lineage_projection, lineage_proposals, lineage_members
        projection = vertical_projection(snapshot, operation_id)
        slot = self.candidate_provider.slot
        events = operation_events(snapshot, operation_id)
        if (projection["feature_id"] != slot.feature_id or projection["target_repository"] != "dream-xin/ai-sdlc"
                or projection["operation_profile"] != VERTICAL_PROFILE or projection["status"] != "BLOCKED"
                or len(projection.get("lineage_blocks", [])) != 1):
            raise VerticalInvariantError("POLICY_DENIED", "rereview is not the frozen blocked remediation")
        feature, manifest = self.executor.feature_gateway.read_feature(operation_id=operation_id)
        action = select_vertical_action(feature=feature, manifest=manifest, occurred_at=self.runtime.clock())
        if (feature.target_ref != slot.target_ref or feature.feature_id != slot.feature_id
                or feature.repository != "dream-xin/ai-sdlc" or feature.current_stage != "code-review"
                or feature.revision != projection["expected_feature_revision"]
                or action.step != "CODE_REREVIEW" or action.role != "reviewer"):
            raise VerticalInvariantError("POLICY_DENIED", "rereview Feature no longer selects the unique successor")
        candidate = self.candidate_provider.current_candidate(operation_id=operation_id,
            repository=feature.repository, feature_id=feature.feature_id, target_ref=feature.target_ref)
        if candidate.candidate_head_sha != feature.candidate_head_sha:
            raise VerticalInvariantError("STALE_REVISION", "rereview candidate differs from canonical Feature")
        callbacks = [row for row in events if row["event_type"] == "worker.callback.recorded"]
        if len(callbacks) != 3 or any(row["operation_generation"] != projection["generation"] for row in callbacks):
            raise VerticalInvariantError("POLICY_DENIED", "rereview budget requires exactly the initial three callbacks")
        roles = [row["payload"]["trusted_callback_envelope"]["trusted_context"]["role"] for row in callbacks]
        if roles != ["developer", "reviewer", "developer"]:
            raise VerticalInvariantError("POLICY_DENIED", "rereview predecessor role sequence differs")
        review = self._producer(snapshot, callbacks[1], role="reviewer")
        remediation = self._producer(snapshot, callbacks[2], role="developer")
        if (review["verdict"] != "REWORK" or review["context"]["candidate_head_sha"] == feature.candidate_head_sha
                or not callbacks[1]["sequence"] < callbacks[2]["sequence"]):
            raise VerticalInvariantError("POLICY_DENIED", "rereview requires genuine REWORK and a changed candidate")
        tasks = [row for row in manifest.get("tasks", []) if row.get("id") == remediation["context"]["task_id"]
                 and row.get("kind") == "remediation" and row.get("status") == "DONE"]
        review_translation = next(row for row in events if row["event_id"] == review["translation_event_id"])
        task_changes = [change for change in review_translation["payload"]["feature_event"]["changes"]
                        if change.get("record", {}).get("id") == remediation["context"]["task_id"]
                        and change.get("record", {}).get("kind") == "remediation"]
        if len(tasks) != 1 or len(task_changes) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "completed remediation was not created by accepted REWORK")
        read_dogfood_handoff(snapshot, operation_id, remediation["callback_id"], require_applied=True)
        superseded = [row for row in events if row["event_type"] == "feature.event.translated"
            and row["payload"].get("callback_id") == remediation["callback_id"]
            and row["payload"].get("purpose") == "remediation_artifact_supersession"]
        if len(superseded) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "rereview lacks canonical remediation supersession")
        supersession = superseded[0]
        event_id = supersession["payload"]["feature_event_id"]
        confirmations = [row for row in events if row["event_type"] == "persist.confirmed"
                         and row["payload"].get("feature_event_id") == event_id]
        if (len(confirmations) != 1 or confirmations[0]["sequence"] <= supersession["sequence"]
                or self.executor.persist_gateway.lookup_feature_event(event_id=event_id, target_ref=feature.target_ref)
                != {"event_id": event_id, "result_revision": confirmations[0]["payload"]["result_revision"]}):
            raise VerticalInvariantError("POLICY_DENIED", "rereview supersession Persist is not confirmed")
        draft = [row for row in manifest.get("artifacts", []) if row.get("type") == "implementation"
                 and row.get("status") == "draft"]
        retired = [row for row in manifest.get("artifacts", []) if row.get("id") ==
                   supersession["payload"]["superseded_artifact_id"] and row.get("status") == "superseded"]
        if (len(draft) != 1 or len(retired) != 1
                or draft[0].get("id") != supersession["payload"]["replacement_artifact_id"]
                or draft[0].get("uri") != remediation["receipt"]["trusted_uri"]):
            raise VerticalInvariantError("POLICY_DENIED", "rereview implementation replacement differs")
        lineage_id = projection["lineage_blocks"][0]
        lineage = rebuild_lineage_projection(snapshot, lineage_id)
        proposals = lineage_proposals(snapshot, lineage_id)
        members = lineage_members(snapshot, lineage_id)
        proposal = proposals.get(lineage.get("current_proposal_id"))
        if (len(members) != 1 or len(proposals) != 1 or not isinstance(proposal, dict)
                or lineage.get("current_leaf_semantic_effect_key") != review["context"]["semantic_effect_key"]
                or proposal.get("predecessor_semantic_effect_key") != review["context"]["semantic_effect_key"]
                or proposal.get("operation_id") != operation_id
                or proposal.get("operation_generation") != projection["generation"]
                or proposal.get("current_feature_revision") != feature.revision
                or proposal.get("current_target_ref") != feature.target_ref
                or proposal.get("current_candidate_head_sha") != feature.candidate_head_sha
                or proposal.get("trusted_profile_digest") != current_policy.proposal_profile_digest
                or proposal.get("proposed_exact_semantic_material", {}).get("task_identity") != action.task_identity
                or proposal.get("proposed_exact_semantic_material", {}).get("role") != "reviewer"):
            raise VerticalInvariantError("POLICY_DENIED", "rereview exact successor proposal differs")
        proof = {"operation_id": operation_id, "operation_generation": projection["generation"],
            "event_count": len(events), "event_prefix_digest": digest_json(events),
            "feature": {"repository": feature.repository, "feature_id": feature.feature_id,
                "target_ref": feature.target_ref, "revision": feature.revision,
                "candidate_head_sha": feature.candidate_head_sha, "candidate_pr_number": candidate.candidate_pr_number,
                "manifest_digest": feature.manifest_digest},
            "lineage_id": lineage_id, "proposal": proposal, "review": review, "remediation": remediation,
            "supersession_event_id": supersession["event_id"], "capability_digest": digest_json(capability),
            "consumer_execution_binding": recovery_execution_binding(self.policy_authority)}
        fresh_feature, _ = self.executor.feature_gateway.read_feature(operation_id=operation_id)
        if (fresh_feature.manifest_digest != feature.manifest_digest
                or self.runtime.backend.read_snapshot().ref_sha != snapshot.ref_sha):
            raise VerticalInvariantError("STALE_REVISION", "rereview evidence changed before resolution CAS")
        return proof


    def plan(self, snapshot, *, operation_id):
        from dataclasses import replace
        from copy import deepcopy
        from operator_effect_resolution import (
            ProtectedEffectResolutionPolicyVerifier, TrustedEffectEvidenceVerifier,
            plan_effect_resolution, resolution_identity)
        from operator_store_model import apply_plan_to_snapshot
        capability, policy = self._fresh_capability(snapshot)
        existing = validate_dogfood_rereview_binding(snapshot, operation_id=operation_id,
            consumer_binding=recovery_execution_binding(self.policy_authority))
        if existing is not None:
            self._revalidate_producers(snapshot, existing)
            return StoreMutationPlan(snapshot.ref_sha, (), {"status": "ALREADY_CONSUMED", "binding": existing})
        proof = self._proof(snapshot, operation_id=operation_id, capability=capability, current_policy=policy)
        owner = self
        predecessor_key = proof["review"]["context"]["external_dispatch_key"]
        proof_digest = digest_json(proof)
        evidence_ref = DOGFOOD_REREVIEW_EVIDENCE_REF + "#" + proof_digest
        class ScopedPolicy(ProtectedEffectResolutionPolicyVerifier):
            def __init__(self):
                self.__dict__.update(owner.policy_authority.resolution_policy_verifier.__dict__)
            def verify_current(self):
                checked_capability, checked_policy = owner._fresh_capability(snapshot)
                checked = owner._proof(snapshot, operation_id=operation_id,
                    capability=checked_capability, current_policy=checked_policy)
                if canonical_json(checked) != canonical_json(proof):
                    raise VerticalInvariantError("STALE_REVISION", "scoped rereview tuple changed")
                class ScopedEvidence(TrustedEffectEvidenceVerifier):
                    def verify(self, refs, *, predecessor_external_dispatch_key):
                        if refs != [evidence_ref] or predecessor_external_dispatch_key != predecessor_key:
                            raise VerticalInvariantError("POLICY_DENIED", "scoped rereview proof cannot be reused")
                        return super().verify(refs,
                            predecessor_external_dispatch_key=predecessor_external_dispatch_key)
                evidence = ScopedEvidence(
                    source_id=checked_policy.evidence_verifier.source_id + "/dogfood-tuple",
                    source_digest=digest_json({"capability_source": checked_policy.evidence_verifier.source_digest,
                                               "tuple_digest": proof_digest}),
                    fact_loader=lambda ref: {"type": "NON_OVERLAPPING_SCOPE", "proof_digest": proof_digest,
                        "capability_id": DOGFOOD_REREVIEW_CAPABILITY_ID} if ref == evidence_ref else {},
                    strong_evidence_types=frozenset({"NON_OVERLAPPING_SCOPE"}))
                return replace(checked_policy, evidence_verifier=evidence)
        scoped = ScopedPolicy()
        current = scoped.verify_current()
        verified = current.evidence_verifier.verify([evidence_ref],
            predecessor_external_dispatch_key=predecessor_key)
        if len(current.authority.allowed_resolvers) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "scoped rereview resolver is ambiguous")
        resolver = next(iter(current.authority.allowed_resolvers))
        proposal = proof["proposal"]
        feature, _ = self.executor.feature_gateway.read_feature(operation_id=operation_id)
        material = {"target_repository": proof["feature"]["repository"],
            "feature_id": proof["feature"]["feature_id"], "effect_lineage_id": proof["lineage_id"],
            "predecessor_semantic_effect_key": proof["review"]["context"]["semantic_effect_key"],
            "predecessor_external_dispatch_key": predecessor_key,
            "current_operation_id": operation_id, "current_operation_generation": proof["operation_generation"],
            "current_feature_revision": proof["feature"]["revision"],
            "current_target_ref": proof["feature"]["target_ref"],
            "current_candidate_head_sha": proof["feature"]["candidate_head_sha"],
            "successor_proposal_id": proposal["proposal_id"],
            "successor_proposed_semantic_effect_key": proposal["proposed_semantic_effect_key"],
            "choice": "RETIRE_OBSOLETE_NO_DUPLICATE_PROVEN",
            "trusted_policy_ref": current.authority.trusted_policy_ref,
            "trusted_policy_digest": current.authority.trusted_policy_digest,
            "resolver_identity": resolver, "evidence_digests": [row["evidence_digest"] for row in verified]}
        resolution_id = resolution_identity(material)
        shared = plan_effect_resolution(snapshot, policy_verifier=scoped, trusted_feature=feature,
            resolution_id=resolution_id, effect_lineage_id=proof["lineage_id"],
            predecessor_semantic_effect_key=material["predecessor_semantic_effect_key"],
            predecessor_external_dispatch_key=predecessor_key,
            current_operation_id=operation_id, current_operation_generation=proof["operation_generation"],
            successor_proposal_id=proposal["proposal_id"],
            successor_proposed_semantic_effect_key=proposal["proposed_semantic_effect_key"],
            choice=material["choice"], resolver_identity=resolver, evidence_refs=[evidence_ref],
            occurred_at=self.runtime.clock(), trusted_context_digest=self.executor.config.trusted_context_digest)
        row = {"schema_version": "ai-sdlc.v03-remediation-rereview-binding/v1",
            "capability_id": DOGFOOD_REREVIEW_CAPABILITY_ID, "admission": dict(DOGFOOD_REREVIEW_ADMISSION),
            "operation_id": operation_id, "operation_generation": proof["operation_generation"],
            "capability_digest": digest_json(capability),
            "consumer_execution_binding": recovery_execution_binding(self.policy_authority),
            "source_blobs": structured_gate_source_blobs(), "proof": deepcopy(proof), "proof_digest": proof_digest,
            "resolution_id": resolution_id, "successor_semantic_effect_key": shared.result["semantic_effect_key"],
            "successor_external_dispatch_key": shared.result["external_dispatch_key"]}
        binding_path, _ = dogfood_rereview_paths(operation_id)
        combined = StoreMutationPlan(snapshot.ref_sha,
            (StoreMutation("create_immutable", binding_path, row), *shared.mutations),
            dict(shared.result, binding=row))
        validate_dogfood_rereview_binding(apply_plan_to_snapshot(snapshot, combined),
            operation_id=operation_id, consumer_binding=recovery_execution_binding(self.policy_authority))
        if self.runtime.backend.read_snapshot().ref_sha != snapshot.ref_sha:
            raise VerticalInvariantError("STALE_REVISION", "rereview Store changed before atomic consumption")
        return combined

    def _revalidate_producers(self, snapshot, binding):
        events = operation_events(snapshot, binding["operation_id"])
        for name, role in (("review", "reviewer"), ("remediation", "developer")):
            frozen = binding["proof"][name]
            matches = [row for row in events if row["event_id"] == frozen["callback_event_id"]]
            if len(matches) != 1 or canonical_json(self._producer(snapshot, matches[0], role=role)) != canonical_json(frozen):
                raise VerticalInvariantError("POLICY_DENIED", "consumed rereview producer proof changed")

    def after_stop(self, *, operation_id, current):
        if current.get("status") != "BLOCKED":
            return current
        from operator_vertical_store import vertical_projection
        snapshot = self.runtime.backend.read_snapshot()
        projection = vertical_projection(snapshot, operation_id)
        if (projection["feature_id"] != self.candidate_provider.slot.feature_id
                or not projection.get("lineage_blocks")):
            return current
        from v03_dogfood_runtime_driver import _commit_recovery_nonempty
        result = _commit_recovery_nonempty(self.runtime,
            lambda snapshot: self.plan(snapshot, operation_id=operation_id))
        if result["status"] != "RESOLVED":
            return current
        # Re-enter the same recovering executor; only the existing dispatch gateway can POST.
        return super(DogfoodPostHandoffRecoveringExecutor, self.executor).advance_until_stop(
            operation_id=operation_id)

    def validate_dispatch(self, dispatch):
        if dispatch.get("role") != "reviewer":
            return
        snapshot = self.runtime.backend.read_snapshot()
        self._fresh_capability(snapshot)
        binding = validate_dogfood_rereview_binding(snapshot, operation_id=dispatch["operation_id"],
            consumer_binding=recovery_execution_binding(self.policy_authority))
        if binding is None:
            # The initial Reviewer remains governed solely by its original reservation.
            from operator_store_model import reservation_path
            reservation = snapshot.get(reservation_path(dispatch["semantic_effect_key"]))
            if not isinstance(reservation, dict) or "vertical:code-rereview:" in str(reservation.get("task_identity")):
                raise VerticalInvariantError("POLICY_DENIED", "rereview dispatch lacks consumed capability")
            return
        if (dispatch["external_dispatch_key"] != binding["successor_external_dispatch_key"]
                or dispatch["semantic_effect_key"] != binding["successor_semantic_effect_key"]
                or dispatch["operation_generation"] != binding["operation_generation"]
                or dispatch["candidate_head_sha"] != binding["proof"]["feature"]["candidate_head_sha"]
                or dispatch["expected_revision"] != binding["proof"]["feature"]["revision"]
                or dogfood_rereview_paths(dispatch["operation_id"])[1] in snapshot.files):
            raise VerticalInvariantError("POLICY_DENIED", "rereview dispatch exceeds its consumed exact tuple")
        self._revalidate_producers(snapshot, binding)

    def stop_nonpassing(self, *, context, callback_id, worker_payload, receipts):
        if context.role != "reviewer":
            return None
        from v03_dogfood_gate_output import strict_json
        from operator_store import plan_needs_user
        from operator_store_model import apply_plan_to_snapshot
        snapshot = self.runtime.backend.read_snapshot()
        self._fresh_capability(snapshot)
        binding = validate_dogfood_rereview_binding(snapshot, operation_id=context.operation_id,
            consumer_binding=recovery_execution_binding(self.policy_authority))
        if binding is None:
            return None
        if (context.external_dispatch_key != binding["successor_external_dispatch_key"]
                or context.semantic_effect_key != binding["successor_semantic_effect_key"]
                or context.operation_generation != binding["operation_generation"]):
            raise VerticalInvariantError("POLICY_DENIED", "Reviewer callback is outside the sole rereview")
        feature, _ = self.executor.feature_gateway.read_feature(operation_id=context.operation_id)
        validate_worker_result("reviewer", worker_payload)
        validate_collected_outputs(context=context, feature=feature, worker_payload=worker_payload,
            receipts=receipts, content_loader=self.content_loader)
        if len(receipts) != 1:
            raise VerticalInvariantError("POLICY_DENIED", "rereview requires one authenticated output")
        content = self.content_loader(receipts[0]["trusted_uri"])
        payload = strict_json(content)
        if payload.get("verdict") != worker_payload.get("verdict"):
            raise VerticalInvariantError("POLICY_DENIED", "rereview recommendation changed in translation")
        if payload["verdict"] == "PASS":
            return None
        if payload["verdict"] not in {"REWORK", "BLOCKED"}:
            raise VerticalInvariantError("POLICY_DENIED", "unknown rereview recommendation")
        observation = {"schema_version": "ai-sdlc.v03-remediation-rereview-terminal/v1",
            "capability_id": DOGFOOD_REREVIEW_CAPABILITY_ID, "operation_id": context.operation_id,
            "operation_generation": context.operation_generation, "binding_digest": digest_json(binding),
            "callback_id": callback_id, "context": _context_payload(context),
            "worker_payload_digest": digest_json(worker_payload), "receipt": receipts[0],
            "content_sha256": hashlib.sha256(content).hexdigest(), "verdict": payload["verdict"]}
        from v03_dogfood_runtime_driver import _commit_recovery_nonempty
        def terminal_plan(current):
            self._fresh_capability(current)
            fixed = validate_dogfood_rereview_binding(current, operation_id=context.operation_id,
                consumer_binding=recovery_execution_binding(self.policy_authority))
            if canonical_json(fixed) != canonical_json(binding):
                raise VerticalInvariantError("POLICY_DENIED", "rereview binding changed before terminal observation")
            _, path = dogfood_rereview_paths(context.operation_id)
            if path in current.files:
                if canonical_json(current.get(path)) != canonical_json(observation):
                    raise VerticalInvariantError("POLICY_DENIED", "conflicting rereview terminal observation")
                return StoreMutationPlan(current.ref_sha, (), {"status": "NEEDS_USER"})
            if any(row["event_type"] == "worker.callback.recorded"
                   and row["payload"]["trusted_callback_envelope"]["trusted_context"]["external_dispatch_key"]
                   == context.external_dispatch_key for row in operation_events(current, context.operation_id)):
                raise VerticalInvariantError("POLICY_DENIED", "nonpassing rereview was already accepted")
            mutation = StoreMutation("create_immutable", path, observation)
            working = apply_plan_to_snapshot(current, StoreMutationPlan(current.ref_sha, (mutation,), {}))
            stop = plan_needs_user(working, operation_id=context.operation_id,
                generation=context.operation_generation, reason_code="VERTICAL_NEEDS_USER",
                summary="Rereview " + payload["verdict"] + "; observation sha256:" + digest_json(observation),
                occurred_at=self.runtime.clock(), trusted_context_digest=self.executor.config.trusted_context_digest)
            combined = StoreMutationPlan(current.ref_sha, (mutation, *stop.mutations), {"status": "NEEDS_USER"})
            validate_dogfood_rereview_binding(apply_plan_to_snapshot(current, combined),
                operation_id=context.operation_id, consumer_binding=recovery_execution_binding(self.policy_authority))
            return combined
        _commit_recovery_nonempty(self.runtime, terminal_plan)
        return self.executor._public(context.operation_id)

    def validate_historical(self, *, operation_id):
        snapshot = self.runtime.backend.read_snapshot()
        self._fresh_capability(snapshot)
        binding = validate_dogfood_rereview_binding(snapshot, operation_id=operation_id,
            consumer_binding=recovery_execution_binding(self.policy_authority))
        if binding is None:
            raise VerticalInvariantError("POLICY_DENIED", "completed remediation lacks consumed rereview authority")
        self._revalidate_producers(snapshot, binding)
        return binding
