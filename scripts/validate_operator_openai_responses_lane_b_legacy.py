#!/usr/bin/env python3
"""Mandatory OpenAI Responses Lane-B production-composition conformance.

`--probe` is observation-only and never means Lane B passed. Normal execution
requires the hard production dependencies to exist on the implementation
baseline, then drives the actual final production composition using deterministic
provider fixtures and reviewed external seams.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from operator_api import API_VERSION
from operator_decision_policy import DECISION_POLICY_SCHEMA, ProtectedDecisionPolicyVerifier
from operator_effect_resolution import ProtectedEffectResolutionPolicyVerifier
from operator_effect_rollout import LINEAGE_WRITER_CAPABILITY, VerifiedEffectLineageRollout
from operator_openai_responses import TOOL_CAPABILITIES, WRITE_CAPABILITIES
from operator_openai_responses_production import (
    build_openai_responses_production_bundle,
    production_dependency_status,
)
from operator_production_runtime import TrustedFeatureBinding, TrustedOperatorRuntimeConfig
from operator_store_model import digest_json, operation_events, rebuild_projection
from operator_store_protection import PROTECTED, ProtectionReceipt
from operator_vertical import FeatureSnapshot, VERTICAL_PROFILE

TARGET = "DREAM-XIN/responses-lane-b-target"
STORE = "DREAM-XIN/responses-lane-b-control"
FEATURE = "F-RESPONSES-LANE-B-0001"
FEATURE_REF = "feature/F-RESPONSES-LANE-B-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
HEAD = "b" * 40
NOW = "2026-08-11T11:55:00Z"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git(*args: str, cwd: Path | None = None):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def _call(name: str, call_id: str, arguments: dict) -> dict:
    return {
        "type": "function_call",
        "id": f"fc-{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, separators=(",", ":")),
        "status": "completed",
    }


def _decode(output: dict) -> dict:
    require(output.get("type") == "function_call_output", f"invalid Responses output: {output}")
    body = json.loads(output["output"])
    require(body.get("ok") is True, f"Lane-B canonical call failed: {body}")
    return body


class FixtureProtectionVerifier:
    test_only = False

    def verify(self, repository, state_ref):
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity="responses-lane-b-protection",
            verified_at=NOW,
            policy_digest="responses-lane-b-protection-policy",
        )


class FixtureResolutionVerifier(ProtectedEffectResolutionPolicyVerifier):
    def __init__(self):
        self.repository = STORE.lower()
        self.state_ref = STATE_REF
        self.operation_profile = VERTICAL_PROFILE

    def verify_current(self):
        return SimpleNamespace(proposal_profile_digest="responses-lane-b-resolution-profile")


class FixtureRolloutVerifier:
    def verify(self, *, repository, state_ref, operation_profile):
        return VerifiedEffectLineageRollout(
            repository=repository,
            state_ref=state_ref,
            operation_profile=operation_profile,
            effect_lineage_required=True,
            policy_ref="protected://responses-lane-b/effect-lineage",
            policy_digest="responses-lane-b-effect-lineage-policy",
            writer_capability=LINEAGE_WRITER_CAPABILITY,
            writer_fence_receipt_ref="protected://responses-lane-b/writer-fence",
            writer_fence_receipt_digest="responses-lane-b-writer-fence-digest",
            test_only=False,
        )


def decision_policy_loader(repository, state_ref, operation_profile):
    material = {
        "schema_version": DECISION_POLICY_SCHEMA,
        "repository": repository,
        "state_ref": state_ref,
        "operation_profile": operation_profile,
        "policy_ref": "protected://responses-lane-b/decision-policy",
        "policy_epoch": "responses-lane-b-v1",
        "allowed_target_repositories": [TARGET],
        "decision_types": {
            "release-authorization": {
                "choices": {"APPROVE": "release.authorize", "REJECT": "release.reject"},
                "allowed_responders": ["responses-lane-b-principal"],
                "ttl_seconds": 3600,
                "warning_seconds": 300,
            }
        },
    }
    return {**material, "policy_digest": digest_json(material)}


class MutableFeatureTruth:
    def __init__(self, *, stage: str):
        status_by_stage = {
            "implementation": "WORKING" if stage == "implementation" else "DONE",
            "code-review": "READY" if stage == "code-review" else "TODO",
            "verification": "TODO",
            "acceptance": "TODO",
        }
        self.manifest = {
            "protocol_version": "0.1.0",
            "revision": 7,
            "feature": {"id": FEATURE, "title": "Responses Lane-B fixture"},
            "workflow": {
                "profile": "standard-feature",
                "status": "ACTIVE",
                "current_stage": stage,
                "stages": [
                    {"id": name, "status": status}
                    for name, status in status_by_stage.items()
                ],
            },
            "tasks": [],
            "artifacts": [],
            "gates": [
                {"id": "requirement-gate", "status": "PASS"},
                {"id": "design-gate", "status": "PASS"},
                {"id": "code-gate", "status": "PENDING"},
                {"id": "verification-gate", "status": "PENDING"},
                {"id": "release-gate", "status": "PENDING"},
            ],
            "applied_events": [],
        }

    def read_feature(self, *, operation_id):
        return (
            FeatureSnapshot.from_manifest(
                repository=TARGET,
                target_ref=FEATURE_REF,
                manifest=self.manifest,
                candidate_pr_number=999,
                candidate_head_sha=HEAD,
            ),
            dict(self.manifest),
        )

    def apply_event(self, event: dict) -> int:
        require(event["feature_id"] == FEATURE, "Lane-B Event changed Feature identity")
        require(event["expected_revision"] == self.manifest["revision"], "Lane-B Event revision drifted")
        for change in event.get("changes", []):
            if change.get("kind") != "stage":
                continue
            stage_id = str(change["id"])
            for row in self.manifest["workflow"]["stages"]:
                if row["id"] == stage_id:
                    row["status"] = str(change["status"])
                    break
            else:
                raise AssertionError(f"unknown Lane-B stage change: {stage_id}")
        self.manifest["revision"] += 1
        self.manifest["applied_events"].append(event["id"])
        return int(self.manifest["revision"])


class TargetReadHTTP:
    def __init__(self, truth: MutableFeatureTruth):
        self.truth = truth
        self.calls: list[str] = []

    def __call__(self, url, headers):
        self.calls.append(url)
        parsed = urlparse(url)
        prefix = f"/repos/{TARGET.lower()}/contents/"
        if not parsed.path.startswith(prefix):
            return 404, {}
        path = "/".join(unquote(part) for part in parsed.path[len(prefix):].split("/"))
        ref = parse_qs(parsed.query).get("ref", [""])[0]
        if ref != FEATURE_REF or path != f"state/features/{FEATURE}.yaml":
            return 404, {}
        text = yaml.safe_dump(self.truth.manifest, sort_keys=False)
        return 200, {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }


class DispatchTransport:
    def __init__(self):
        self.launches: list[dict] = []
        self.receipts: dict[str, str] = {}

    def launch(self, *, dispatch):
        key = dispatch["external_dispatch_key"]
        if key not in self.receipts:
            self.receipts[key] = f"responses-lane-b-run-{len(self.receipts) + 1}"
            self.launches.append(dict(dispatch))
        return {"lookup_state": "LAUNCHED", "receipt_id": self.receipts[key]}

    def lookup(self, *, external_dispatch_key):
        receipt = self.receipts.get(external_dispatch_key)
        if receipt is None:
            return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
        return {"lookup_state": "LAUNCHED", "receipt_id": receipt}


class GitHubFeatureEventHTTP:
    """Deterministic outer HTTP seam under the real exact-revision Event gateway."""

    def __init__(self, truth: MutableFeatureTruth):
        self.truth = truth
        self.events: dict[str, str] = {}
        self.put_calls: list[str] = []
        self.get_calls: list[str] = []

    @staticmethod
    def _blob_sha(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _content(self, text: str):
        return {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
            "sha": self._blob_sha(text),
        }

    def __call__(self, method, url, headers, body=None):
        parsed = urlparse(url)
        prefix = f"/repos/{TARGET}/contents/"
        if not parsed.path.startswith(prefix):
            return 404, {}
        path = "/".join(unquote(part) for part in parsed.path[len(prefix):].split("/"))
        ref = parse_qs(parsed.query).get("ref", [""])[0]
        if ref != FEATURE_REF:
            return 404, {}

        if method == "GET":
            self.get_calls.append(path)
            if path == f"state/features/{FEATURE}.yaml":
                return 200, self._content(yaml.safe_dump(self.truth.manifest, sort_keys=False))
            event_text = self.events.get(path)
            if event_text is None:
                return 404, {}
            return 200, self._content(event_text)

        if method == "PUT" and path.startswith("events/inbox/"):
            self.put_calls.append(path)
            require(path not in self.events, "real Event gateway attempted duplicate inbox create")
            require(isinstance(body, dict) and isinstance(body.get("content"), str), "Event PUT body missing content")
            event_text = base64.b64decode(body["content"]).decode()
            event = yaml.safe_load(event_text)
            require(isinstance(event, dict), "Event PUT body is not a mapping")
            self.events[path] = event_text
            self.truth.apply_event(event)
            return 201, {"content": {"sha": self._blob_sha(event_text)}}

        return 405, {}


def _dynamic_feature_event_gateway(truth: MutableFeatureTruth):
    try:
        configured = __import__(
            "operator_configured_feature_event_gateway",
            fromlist=["TrustedFeatureEventTarget"],
        )
        production = __import__(
            "operator_production_feature_event_gateway",
            fromlist=["TrustedFeatureEventWriteScope", "ProductionConfiguredFeatureEventGateway"],
        )
        exact = __import__(
            "operator_exact_feature_event_gateway",
            fromlist=["ExactRevisionGitHubFeatureEventGateway"],
        )
    except ImportError as exc:
        raise RuntimeError("Lane-B Feature Event dependency missing after readiness passed") from exc

    http = GitHubFeatureEventHTTP(truth)
    transport = exact.ExactRevisionGitHubFeatureEventGateway(
        token="responses-lane-b-feature-event-writer",
        http_request=http,
        sleeper=lambda _seconds: None,
        poll_attempts=2,
        poll_seconds=0,
    )
    scope = production.TrustedFeatureEventWriteScope(
        repository=TARGET,
        default_branch="main",
        targets=(configured.TrustedFeatureEventTarget(FEATURE, FEATURE_REF),),
    )
    return production.ProductionConfiguredFeatureEventGateway(scope=scope, transport=transport), http


def _config(checkout: Path) -> TrustedOperatorRuntimeConfig:
    return TrustedOperatorRuntimeConfig(
        target_repository=TARGET,
        store_repository=STORE,
        installation_ref="main",
        store_checkout=checkout,
        principal="responses-lane-b-principal",
        feature_bindings=(TrustedFeatureBinding(FEATURE, FEATURE_REF),),
        state_ref=STATE_REF,
    )


def _build_bundle(*, checkout: Path, truth: MutableFeatureTruth, dispatch: DispatchTransport):
    event_gateway, event_http = _dynamic_feature_event_gateway(truth)
    policy = ProtectedDecisionPolicyVerifier(
        repository=STORE,
        state_ref=STATE_REF,
        operation_profile=VERTICAL_PROFILE,
        policy_loader=decision_policy_loader,
    )
    bundle = build_openai_responses_production_bundle(
        config=_config(checkout),
        feature_id=FEATURE,
        registration_id="responses-lane-b-registration",
        provider_scope_id="responses-lane-b-provider",
        target_read_token="responses-lane-b-target-read",
        protection_verifier=FixtureProtectionVerifier(),
        rollout_verifier=FixtureRolloutVerifier(),
        resolution_policy_verifier=FixtureResolutionVerifier(),
        feature_gateway=truth,
        feature_event_gateway=event_gateway,
        dispatch_gateway=dispatch,
        collector_content_loader=lambda *_args, **_kwargs: b"lane-b-content",
        policy_verifier=policy,
        trusted_context_digest="responses-lane-b-trusted-context",
        collector_namespace_policy="collector/responses-lane-b",
        trusted_role_policy="role/responses-lane-b",
        reader_http_get=TargetReadHTTP(truth),
        clock=lambda: NOW,
    )
    return bundle, event_http


def _start_arguments(*, revision: int) -> dict:
    return {
        "api_version": API_VERSION,
        "feature_id": FEATURE,
        "expected_feature_revision": revision,
        "mode": "ASSISTED",
    }


def _equivalent_start_twice(bundle, *, revision: int):
    arguments = _start_arguments(revision=revision)
    first = _decode(bundle.adapter.invoke_function_call(_call("aisdlc_v1_operation_start", "lane-b-start-a", arguments)))
    second = _decode(bundle.adapter.invoke_function_call(_call("aisdlc_v1_operation_start", "lane-b-start-b", arguments)))
    first_id = first["result"]["operation_id"]
    second_id = second["result"]["operation_id"]
    require(first_id == second_id, "equivalent Responses start did not converge to one canonical Operation")
    return first_id


def _exact_start_replay(bundle, *, revision: int):
    item = _call(
        "aisdlc_v1_operation_start",
        "lane-b-persist-start",
        _start_arguments(revision=revision),
    )
    first_output = bundle.adapter.invoke_function_call(item)
    first = _decode(first_output)
    ref_after_first = bundle.runtime.backend.read_snapshot().ref_sha
    second_output = bundle.adapter.invoke_function_call(item)
    require(second_output == first_output, "exact Responses start replay changed function output")
    require(
        bundle.runtime.backend.read_snapshot().ref_sha == ref_after_first,
        "exact Responses start replay mutated protected Store",
    )
    return first["result"]["operation_id"]


def _assert_shared_authority(bundle) -> None:
    runtime = bundle.runtime
    require(bundle.journal.runtime is runtime, "Responses journal does not share final Store runtime")
    require(set(bundle.backends) == set(TOOL_CAPABILITIES.values()), "Responses model-facing ten-tool map drifted")
    require(set(bundle.operator_bundle.adapter_write_backends) == set(WRITE_CAPABILITIES), "final model write surface drifted")
    require("operation.resume" not in bundle.backends, "server-only operation.resume leaked into Responses")
    require(bundle.operator_bundle.write_bundle.runtime is runtime, "adapter write composition split Store runtime")
    require(bundle.operator_bundle.vertical_bundle.runtime is runtime, "Vertical composition split Store runtime")
    require(bundle.operator_bundle.executor.runtime is runtime, "recovery executor split Store runtime")
    require(
        bundle.operator_bundle.decision_notification_coordinator.runtime is runtime,
        "Decision/Notification split Store runtime",
    )


def _init_store(root: Path, name: str) -> Path:
    scenario_root = root / name
    remote = scenario_root / "control.git"
    checkout = scenario_root / "checkout"
    scenario_root.mkdir()
    git("init", "--bare", "-q", str(remote))
    git("clone", "-q", str(remote), str(checkout))
    git("config", "user.name", "ai-sdlc-test", cwd=checkout)
    git("config", "user.email", "ai-sdlc@example.invalid", cwd=checkout)
    return checkout


def run_lane_b() -> dict:
    status = production_dependency_status()
    require(all(status.values()), f"Lane-B dependencies unexpectedly not ready: {status}")

    with tempfile.TemporaryDirectory(prefix="ai-sdlc-responses-lane-b-") as td:
        root = Path(td)

        dispatch_truth = MutableFeatureTruth(stage="implementation")
        dispatch_transport = DispatchTransport()
        dispatch_bundle, dispatch_event_http = _build_bundle(
            checkout=_init_store(root, "dispatch"),
            truth=dispatch_truth,
            dispatch=dispatch_transport,
        )
        _assert_shared_authority(dispatch_bundle)
        operation_id = _equivalent_start_twice(dispatch_bundle, revision=7)
        dispatch_snapshot = dispatch_bundle.runtime.backend.read_snapshot()
        projection = rebuild_projection(dispatch_snapshot, operation_id)
        require(projection["operation_profile"] == VERTICAL_PROFILE, "Responses start lost Vertical profile")
        require(len(dispatch_transport.launches) == 1, "equivalent Responses start created duplicate external launch")
        require(not dispatch_event_http.put_calls, "implementation dispatch scenario unexpectedly wrote a Feature Event")
        require(
            any(event["event_type"] == "dispatch.launch.authorized" for event in operation_events(dispatch_snapshot, operation_id)),
            "Responses start did not reach durable launch authorization",
        )

        persist_truth = MutableFeatureTruth(stage="code-review")
        persist_dispatch = DispatchTransport()
        persist_bundle, persist_event_http = _build_bundle(
            checkout=_init_store(root, "persist"),
            truth=persist_truth,
            dispatch=persist_dispatch,
        )
        _assert_shared_authority(persist_bundle)
        persist_operation = _exact_start_replay(persist_bundle, revision=7)
        persist_projection = rebuild_projection(persist_bundle.runtime.backend.read_snapshot(), persist_operation)
        require(len(persist_event_http.put_calls) == 1, "exact Responses replay duplicated Feature Event inbox write")
        require(len(persist_event_http.events) == 1, "final exact Event transport did not retain one exact Event identity")
        require(
            len(persist_projection.get("confirmed_persists", [])) == 1,
            "final Persist receipt was not confirmed exactly once",
        )
        require(persist_truth.manifest["revision"] == 8, "semantic Persist did not advance exact Feature revision once")
        require(len(persist_dispatch.launches) == 1, "post-Persist code-review dispatch did not converge to one external launch")

        return {
            "status": "PASS",
            "evidence_kind": "lane-b-production-composition",
            "dependencies": status,
            "dispatch_operation_id": operation_id,
            "persist_operation_id": persist_operation,
            "external_launch_count": len(dispatch_transport.launches),
            "semantic_persist_count": len(persist_event_http.put_calls),
            "model_write_capabilities": sorted(WRITE_CAPABILITIES),
            "operation_resume_model_exposed": False,
            "shared_protected_store": True,
            "equivalent_start_converged": True,
            "exact_persist_call_replay_inert": True,
            "real_exact_feature_event_gateway_exercised": True,
        }


def probe() -> dict:
    status = production_dependency_status()
    return {
        "status": "READY" if all(status.values()) else "BLOCKED",
        "evidence_kind": "lane-b-readiness-only",
        "lane_b_passed": False,
        "dependencies": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(probe(), indent=2, sort_keys=True))
        return

    status = production_dependency_status()
    if not all(status.values()):
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "evidence_kind": "lane-b-production-composition",
                    "lane_b_passed": False,
                    "dependencies": status,
                },
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(2)

    result = run_lane_b()
    print("OpenAI Responses Lane-B production-composition validation passed")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
