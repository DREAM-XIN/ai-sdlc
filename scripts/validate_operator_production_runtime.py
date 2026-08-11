#!/usr/bin/env python3
"""Deterministic validation for trusted production Operator backend composition."""
from __future__ import annotations

import base64
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from operator_api import API_VERSION, dispatch
from operator_mcp import ADAPTER_ID, READ_TOOLS, build_server
from operator_production_runtime import (
    BoundedTrustedContextProvider,
    RUNTIME_CONFIG_VERSION,
    TrustedFeatureBinding,
    TrustedOperatorRuntimeConfig,
    build_trusted_operator_read_bundle,
)
from operator_store import plan_operation_start
from operator_store_model import StoreSnapshot
from operator_store_protection import PROTECTED, ProtectionReceipt
from operator_store_remote_git import RemoteGitStateRefBackend

TARGET_REPOSITORY = "DREAM-XIN/fixture"
NORMALIZED_TARGET_REPOSITORY = "dream-xin/fixture"
STORE_REPOSITORY = "DREAM-XIN/control-fixture"
NORMALIZED_STORE_REPOSITORY = "dream-xin/control-fixture"
FEATURE_ID = "F-RUNTIME-COMPOSITION-0001"
FEATURE_REF = "feature/F-RUNTIME-COMPOSITION-0001"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
NOW = "2026-08-11T04:30:00Z"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class FixtureProductionVerifier:
    test_only = False

    def __init__(self):
        self.calls = []

    def verify(self, repository, state_ref):
        self.calls.append((repository, state_ref))
        return ProtectionReceipt(
            repository=repository,
            state_ref=state_ref,
            status=PROTECTED,
            verifier_identity="runtime-composition-fixture",
            verified_at=NOW,
            policy_digest="runtime-composition-policy",
        )


class FakeGitHubContents:
    def __init__(self):
        project = {
            "version": "0.1.0",
            "project": {"id": "fixture", "name": "Fixture"},
            "repository": {
                "provider": "github",
                "full_name": TARGET_REPOSITORY,
                "default_branch": "main",
            },
            "defaults": {"workflow_profile": "standard-feature", "runtime_policy": "default", "required_commands": []},
            "context": {"rules": ["AGENTS.md"], "read": ["README.md"]},
            "commands": [],
            "ownership": [],
        }
        feature = {
            "version": "0.1.0",
            "feature": {"id": FEATURE_ID, "title": "Runtime composition fixture"},
            "revision": 7,
            "workflow": {"profile": "standard-feature", "status": "ACTIVE", "current_stage": "implementation"},
            "stages": {},
            "gates": {},
            "artifacts": [],
            "applied_events": [],
        }
        self.files = {
            ("main", ".ai-sdlc/project.yaml"): yaml.safe_dump(project, sort_keys=False),
            (FEATURE_REF, f"state/features/{FEATURE_ID}.yaml"): yaml.safe_dump(feature, sort_keys=False),
        }
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        parsed = urlparse(url)
        prefix = f"/repos/{NORMALIZED_TARGET_REPOSITORY}/contents/"
        if not parsed.path.startswith(prefix):
            return 404, {}
        path = "/".join(unquote(part) for part in parsed.path[len(prefix):].split("/"))
        ref = parse_qs(parsed.query).get("ref", [""])[0]
        text = self.files.get((ref, path))
        if text is None:
            return 404, {}
        return 200, {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


def git(*args, cwd=None, check=True):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=check)


def seed_store(checkout: Path):
    backend = RemoteGitStateRefBackend(
        repo_path=checkout,
        repository=STORE_REPOSITORY,
        state_ref=STATE_REF,
    )
    verifier = FixtureProductionVerifier()
    receipt = verifier.verify(STORE_REPOSITORY, STATE_REF)
    snapshot = backend.read_snapshot()
    require(snapshot == StoreSnapshot(ref_sha=None), "fixture Store did not begin empty")
    plan = plan_operation_start(
        snapshot,
        target_repository=TARGET_REPOSITORY,
        feature_id=FEATURE_ID,
        expected_revision=7,
        idempotency_key="runtime-composition-op",
        occurred_at=NOW,
        trusted_context_digest="runtime-composition-trusted",
    )
    result = backend.commit(plan, receipt)
    return str(result.result["operation_id"]), verifier


def request(capability, *, target=None, context=None, adapter_id=ADAPTER_ID):
    body = {
        "api_version": API_VERSION,
        "request_id": f"runtime-{capability.replace('.', '-')}",
        "capability": capability,
        "client_identity": {"adapter_id": adapter_id},
        "payload": {},
    }
    if target is not None:
        body["target"] = dict(target)
    if context is not None:
        body["context"] = dict(context)
    return body


def validate_config_boundary(root: Path):
    config_file = root / "operator-runtime.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "version": RUNTIME_CONFIG_VERSION,
                "target_repository": TARGET_REPOSITORY,
                "store_repository": STORE_REPOSITORY,
                "installation_ref": "main",
                "store_checkout": "checkout",
                "principal": "fixture-principal",
                "feature_refs": {FEATURE_ID: FEATURE_REF},
                "state_ref": STATE_REF,
                "store_remote_name": "origin",
                "operator_app_slug": "ai-sdlc-operator",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = TrustedOperatorRuntimeConfig.from_file(config_file)
    require(config.target_repository == NORMALIZED_TARGET_REPOSITORY, "target repository identity was not normalized")
    require(config.store_repository == NORMALIZED_STORE_REPOSITORY, "Store repository identity was not normalized")
    require(config.target_repository != config.store_repository, "fixture failed to prove target/Store separation")
    require(config.store_checkout == (root / "checkout").resolve(), "relative Store checkout did not bind to config file directory")
    require(config.feature_ref(FEATURE_ID) == FEATURE_REF, "Feature/ref binding changed")
    try:
        config.feature_ref("F-NOT-ALLOWED")
        raise AssertionError("unconfigured Feature unexpectedly resolved a target ref")
    except Exception as exc:
        require(getattr(exc, "code", None) == "UNAUTHORIZED", "unconfigured Feature did not fail closed UNAUTHORIZED")

    invalid = {
        "version": RUNTIME_CONFIG_VERSION,
        "target_repository": TARGET_REPOSITORY,
        "store_repository": STORE_REPOSITORY,
        "principal": "fixture-principal",
        "feature_refs": {FEATURE_ID: FEATURE_REF},
        "client_selected_backend": "evil",
    }
    try:
        TrustedOperatorRuntimeConfig.from_mapping(invalid)
        raise AssertionError("unknown runtime config authority unexpectedly accepted")
    except ValueError:
        pass
    return config


def validate_bundle_and_scope(config: TrustedOperatorRuntimeConfig, operation_id: str, verifier):
    github = FakeGitHubContents()
    bundle = build_trusted_operator_read_bundle(
        config=config,
        adapter_id=ADAPTER_ID,
        target_read_token="target-read-token",
        store_token="store-protection-token",
        github_api_base="https://api.github.test",
        reader_http_get=github,
        protection_verifier=verifier,
    )
    trusted = bundle.trusted_context_provider.for_request({"repository": TARGET_REPOSITORY, "feature_id": FEATURE_ID})
    require(trusted["trusted_client_adapter_id"] == ADAPTER_ID, "trusted adapter identity drifted")
    require(trusted["trusted_principal"] == "fixture-principal", "trusted principal drifted")
    require(trusted["trusted_scope"] == {"repositories": [NORMALIZED_TARGET_REPOSITORY], "feature_ids": [FEATURE_ID]}, "trusted scope drifted")

    target = {"repository": TARGET_REPOSITORY, "feature_id": FEATURE_ID}

    project = dispatch(request("project.inspect", target=target), trusted_context=trusted, backends=bundle.backends)
    require(project["ok"] is True and project["result"] == {"repository": NORMALIZED_TARGET_REPOSITORY, "installed": True}, f"project.inspect failed: {project}")

    feature = dispatch(request("feature.status", target=target), trusted_context=trusted, backends=bundle.backends)
    require(feature["ok"] is True, feature)
    require(feature["result"] == {"feature_id": FEATURE_ID, "revision": 7, "workflow_status": "ACTIVE", "current_stage": "implementation"}, feature)

    operation = dispatch(
        request("operation.status", target=target, context={"operation_id": operation_id}),
        trusted_context=trusted,
        backends=bundle.backends,
    )
    require(operation["ok"] is True and operation["result"]["operation_id"] == operation_id, operation)
    require(operation["result"]["status"] == "RUNNING", operation)

    inbox = dispatch(request("operator.inbox", target=target), trusted_context=trusted, backends=bundle.backends)
    require(inbox["ok"] is True, inbox)
    require(any(row["operation_id"] == operation_id for row in inbox["result"]["operations"]), inbox)

    decisions = dispatch(request("decision.list", target=target), trusted_context=trusted, backends=bundle.backends)
    notifications = dispatch(request("notification.list", target=target), trusted_context=trusted, backends=bundle.backends)
    require(decisions == {"api_version": API_VERSION, "request_id": "runtime-decision-list", "capability": "decision.list", "ok": True, "result": {"decisions": []}}, decisions)
    require(notifications == {"api_version": API_VERSION, "request_id": "runtime-notification-list", "capability": "notification.list", "ok": True, "result": {"notifications": []}}, notifications)

    require("operation.start" in bundle.backends and "operation.cancel" in bundle.backends, "shared canonical bundle lost approved Store writes")
    require(set(READ_TOOLS.values()).isdisjoint({"operation.start", "operation.cancel", "decision.respond", "notification.ack"}), "MCP write-tool boundary regressed")
    build_server(trusted_context_provider=bundle.trusted_context_provider, backends=bundle.backends, enable_conformance_probe=False)

    outside_repo = dispatch(
        request("feature.status", target={"repository": "DREAM-XIN/other", "feature_id": FEATURE_ID}),
        trusted_context=trusted,
        backends=bundle.backends,
    )
    require(outside_repo["ok"] is False and outside_repo["error"]["code"] == "UNAUTHORIZED", outside_repo)

    outside_feature = dispatch(
        request("feature.status", target={"repository": TARGET_REPOSITORY, "feature_id": "F-OTHER"}),
        trusted_context=trusted,
        backends=bundle.backends,
    )
    require(outside_feature["ok"] is False and outside_feature["error"]["code"] == "UNAUTHORIZED", outside_feature)

    wrong_adapter = dispatch(
        request("feature.status", target=target, adapter_id="evil.adapter"),
        trusted_context=trusted,
        backends=bundle.backends,
    )
    require(wrong_adapter["ok"] is False and wrong_adapter["error"]["code"] == "UNAUTHORIZED", wrong_adapter)

    require(any(f"/repos/{NORMALIZED_TARGET_REPOSITORY}/contents/" in url for url, _ in github.calls), "target truth read used wrong repository")
    require(any(f"ref={FEATURE_REF.replace('/', '%2F')}" in url for url, _ in github.calls), "Feature read did not use trusted configured ref")
    for _, headers in github.calls:
        require(headers.get("Authorization") == "Bearer target-read-token", "target read did not use the dedicated server-owned credential")

    require(any(repo == NORMALIZED_STORE_REPOSITORY for repo, _ in verifier.calls), "Store protection did not use configured control repository")
    require(not any(repo == NORMALIZED_TARGET_REPOSITORY for repo, _ in verifier.calls), "Store protection was incorrectly checked on target repository")


def validate_context_provider_binding():
    config = TrustedOperatorRuntimeConfig(
        target_repository=TARGET_REPOSITORY,
        store_repository=STORE_REPOSITORY,
        installation_ref="main",
        store_checkout=Path("."),
        principal="fixture-principal",
        feature_bindings=(TrustedFeatureBinding(FEATURE_ID, FEATURE_REF),),
    )
    provider = BoundedTrustedContextProvider(config=config, adapter_id=ADAPTER_ID)
    first = provider.for_request(None)
    second = provider.for_request({"repository": "attacker/other", "feature_id": "F-OTHER"})
    require(first == second, "client target mutated server-owned trusted context")


def main():
    validate_context_provider_binding()
    with tempfile.TemporaryDirectory(prefix="ai-sdlc-operator-runtime-compose-") as td:
        root = Path(td)
        remote = root / "control.git"
        checkout = root / "checkout"
        git("init", "--bare", "-q", str(remote))
        git("clone", "-q", str(remote), str(checkout))
        git("config", "user.name", "ai-sdlc-test", cwd=checkout)
        git("config", "user.email", "ai-sdlc@example.invalid", cwd=checkout)
        operation_id, verifier = seed_store(checkout)
        config = validate_config_boundary(root)
        validate_bundle_and_scope(config, operation_id, verifier)

    print("Operator production runtime composition validation passed")
    print("- target repository truth and control/Store repository are separate trust domains")
    print("- target reads and Store protection use separately named credentials")
    print("- project/Feature truth: exact trusted repository and Feature/ref bindings")
    print("- Store/inbox/decision/notification reads: shared durable canonical bundle")
    print("- operation.status: canonical context crosses adapter boundary")
    print("- client target cannot modify trusted scope/principal/adapter identity")
    print("- MCP registers no semantic write tools even when shared bundle contains Store writes")


if __name__ == "__main__":
    main()
