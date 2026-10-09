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
from operator_vertical import TrustedDispatchContext, VERTICAL_PROFILE, VerticalInvariantError, validate_worker_result
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
        any(continuation.get(key) != value for key, value in expected.items())
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
    authorization, attempt, continuation = validate_recovery_continuation(snapshot)
    artifact = sealed.get("safe_output_artifact_proof") if isinstance(sealed, dict) else None
    if (
        not isinstance(sealed, dict)
        or not isinstance(artifact, dict)
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


class V03DogfoodCompositionError(RuntimeError):
    pass


HANDOFF_SCHEMA = "ai-sdlc.v03-dogfood-candidate-handoff/v1"


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
            or any(intent.get(k) != v for k, v in binding.items())
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
            or any(applied.get(k) != v for k, v in expected.items())
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
        pending = []
        for callback_id in callbacks:
            if translated.get(callback_id) in confirmed:
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
                or current_head not in {prior_head, adopted_head}
            ):
                raise V03DogfoodCompositionError("incomplete handoff no longer matches fixed candidate lineage")
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
        outcome = executor._commit(plan_intent).result
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
        executor._commit(lambda current: _plan_handoff_applied(current, intent=intent, observed_ref_sha=observed))


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
        if context.role == "developer":
            self.candidate_handoff.adopt(
                executor=self.executor,
                context=context,
                callback_id=callback_id,
                receipts=receipts,
            )
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
        _, _, continuation = validate_recovery_continuation(snapshot)
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
        return validate_recovery_continuation(self._continuation_snapshot)

    def _validate_lookup_identity(self, *, workflow, ref, dispatch_key):
        self._admitted()
        if (
            dispatch_key != ARMED_RECOVERY_KEY
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
    """Resolve the recovery Developer Draft PR from exact run-bound GitHub truth.

    The recovery Worker deliberately has no lifecycle conclusion dispatch.  Its
    trusted result is therefore derived from the successful Safe Outputs job and
    the one open Draft PR whose protected head name embeds the immutable run id.
    """


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

        feature_id = str(trusted_context.get("feature_id") or "")
        expected_revision = int(trusted_context.get("expected_revision") or 0)
        target_ref = str(trusted_context.get("target_ref") or "")
        task_id = str(trusted_context.get("task_id") or "")
        dispatch_id = str(trusted_context.get("dispatch_id") or "")
        if not feature_id or expected_revision < 1 or not target_ref or not task_id or not dispatch_id:
            raise VerticalInvariantError("BLOCKED", "recovery protected context lacks exact task/candidate binding")
        prefix = f"gh-aw/{feature_id}-{run_id}-v{expected_revision}"
        query = parse.urlencode({"state": "open", "base": target_ref, "per_page": 100})
        listed = self._json(self.target_repository, f"/pulls?{query}", self.config.target_token)
        candidates = [
            row for row in listed if isinstance(row, dict)
            and row.get("state") == "open"
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
            or pr.get("state") != "open"
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
        if match and match.group("key") == ARMED_RECOVERY_KEY:
            if self.runtime is None:
                raise VerticalInvariantError("POLICY_DENIED", "recovery content lacks protected runtime")
            snapshot = self.runtime.backend.read_snapshot()
            sealed = snapshot.get(RECOVERY_RECEIPT_PATH)
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
        sealed = snapshot.get(RECOVERY_RECEIPT_PATH)
        authorization = snapshot.get(RECOVERY_AUTHORIZATION_PATH)
        attempt = snapshot.get(RECOVERY_ATTEMPT_PATH)
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
            or authorization.get("task_id") != launch.get("task_id")
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
    result_source = FirstAttemptDigestBoundGhAwResultSource(
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
    bundle = responses.operator_bundle
    content_loader.bind_runtime(responses.runtime)
    durable_truth = DurableDecisionFeatureTruthGateway(
        runtime=responses.runtime,
        feature_gateway=feature_event_gateway,
        candidate_provider=candidate_provider,
    )
    feature_truth.bind(durable_truth)
    candidate_provider.bind_runtime(responses.runtime)
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
