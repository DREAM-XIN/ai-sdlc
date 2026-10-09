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


def validate_post_handoff_reconciliation(snapshot, *, consumer_binding=None):
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
    result_source = DogfoodHandoffAwareResultSource(
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
    durable_truth = DurableDecisionFeatureTruthGateway(
        runtime=responses.runtime,
        feature_gateway=feature_event_gateway,
        candidate_provider=candidate_provider,
    )
    feature_truth.bind(durable_truth)
    candidate_provider.bind_runtime(responses.runtime)
    candidate_provider.persist_gateway = bundle.executor.persist_gateway
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
    collector = ProductionGhAwVerticalResultCollector(
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
