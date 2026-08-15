#!/usr/bin/env python3
"""Validate the trusted-main-only v0.3 Vertical policy materialization entrypoint."""
from __future__ import annotations

from pathlib import Path
import yaml

from materialize_v03_vertical_policy_state import _policy_documents
from operator_effect_resolution import ALLOWED_RESOLUTION_CHOICES
from operator_effect_rollout import LINEAGE_WRITER_CAPABILITY, REQUIRED_FENCED_CAPABILITIES
from operator_protected_policy_materializer import POLICY_NAMESPACE, REQUIRED_POLICY_PATHS
from operator_vertical import VERTICAL_PROFILE

WORKFLOW = Path(".github/workflows/materialize-v03-vertical-policy-state.yml")
SCRIPT = Path("scripts/materialize_v03_vertical_policy_state.py")
REPO = "dream-xin/ai-sdlc"
STATE_REF = "refs/heads/ai-sdlc-operator-state"
INSTALLATION = "a" * 40


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def fake_protected_ref(repository, state_ref, path):
    return f"protected://{repository}@{state_ref}/{path}"


def fake_seal_receipt(
    *,
    repository,
    installation_commit_sha,
    state_ref,
    operation_profile,
    artifacts,
    issued_at,
    issuer,
    receipt_path,
):
    return {
        "schema_version": "test-bundle-receipt/v1",
        "repository": repository,
        "installation_commit_sha": installation_commit_sha,
        "state_ref": state_ref,
        "operation_profile": operation_profile,
        "artifacts": {
            name: {"path": path}
            for name, (path, _value) in artifacts.items()
        },
        "issued_at": issued_at,
        "issuer": issuer,
        "receipt_path": receipt_path,
    }


def validate_policy_scope():
    docs = _policy_documents(
        repository=REPO,
        installation_commit_sha=INSTALLATION,
        state_ref=STATE_REF,
        issued_at="2026-08-14T00:00:00Z",
        writer_fence_proof={
            "schema_version": "ai-sdlc.vertical-writer-quiescence-proof/v1",
            "installation_commit_sha": INSTALLATION,
            "pre_materialization_ref_sha": "b" * 40,
            "semantic_store_state": "bootstrap-only",
            "bootstrap_path": "state/operator/v1/.bootstrap",
            "bootstrap_sha256": "c" * 64,
            "writer_surface_proof_digest": "d" * 64,
        },
        protected_ref_fn=fake_protected_ref,
        seal_receipt_fn=fake_seal_receipt,
    )
    by_path = {row.path: row.value for row in docs}
    require(set(by_path) == REQUIRED_POLICY_PATHS, "live builder does not emit exact six-file policy bundle")

    rollout = by_path[f"{POLICY_NAMESPACE}/effect-lineage-rollout.json"]
    require(rollout["repository"] == REPO, "rollout repository scope drifted")
    require(rollout["state_ref"] == STATE_REF, "rollout state ref drifted")
    require(rollout["operation_profile"] == VERTICAL_PROFILE, "rollout profile drifted")
    require(rollout["effect_lineage_required"] is True, "Effect Lineage is not required")
    require(
        rollout["writer_capability"] == LINEAGE_WRITER_CAPABILITY,
        "rollout writer capability drifted",
    )

    fence = by_path[f"{POLICY_NAMESPACE}/writer-fence-receipt.json"]
    require(fence["state"] == "QUIESCED", "writer fence is not QUIESCED")
    require(
        set(fence["fenced_capabilities"]) == set(REQUIRED_FENCED_CAPABILITIES),
        "writer fence coverage is not exact",
    )
    require(
        (fence.get("quiescence_proof") or {}).get("semantic_store_state") == "bootstrap-only",
        "writer fence lacks bootstrap-only quiescence proof",
    )
    require(
        len((fence.get("quiescence_proof") or {}).get("writer_surface_proof_digest", "")) == 64,
        "writer fence lacks trusted-main writer-surface proof digest",
    )

    evidence = by_path[f"{POLICY_NAMESPACE}/effect-resolution-evidence.json"]
    require(evidence["facts"] == {}, "live materialization fabricated Effect Resolution evidence")

    resolution = by_path[f"{POLICY_NAMESPACE}/effect-resolution-policy.json"]
    require(
        set(resolution["allowed_choices"]) == set(ALLOWED_RESOLUTION_CHOICES),
        "Effect Resolution choices drifted outside frozen protocol",
    )
    require(
        resolution["allowed_resolvers"] == ["trusted-release-controller"],
        "Effect Resolution resolver authority drifted",
    )
    require(
        resolution["strong_evidence_types"] == [],
        "live policy unexpectedly grants strong-evidence authority",
    )

    decision = by_path[f"{POLICY_NAMESPACE}/decision-policy.json"]
    require(
        decision["allowed_target_repositories"] == [REPO],
        "Decision policy is not same-repository scoped",
    )
    require(
        decision["decision_types"] == {},
        "Issue #221 policy materialization must not pre-authorize Decision types",
    )


def validate_workflow_boundary():
    raw = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(raw)
    trigger = workflow.get("on", workflow.get(True))
    require(trigger == {"workflow_dispatch": None}, "materialization workflow must be manual-only")
    require(workflow.get("permissions") == {}, "top-level workflow permissions must be empty")

    jobs = workflow.get("jobs") or {}
    require(set(jobs) == {"reject-non-main", "materialize"}, "unexpected workflow jobs")
    reject = jobs["reject-non-main"]
    materialize = jobs["materialize"]
    require(
        reject.get("if") == "github.ref != 'refs/heads/main'",
        "non-main dispatch rejection is missing",
    )
    require(
        materialize.get("if") == "github.ref == 'refs/heads/main'",
        "materialization is not trusted-main-only",
    )
    require(
        materialize.get("permissions") == {"contents": "read", "issues": "write"},
        "materialization GITHUB_TOKEN permissions expanded",
    )
    require(materialize.get("timeout-minutes") == 20, "materialization job is not bounded")

    uses = [
        step.get("uses")
        for step in materialize.get("steps", [])
        if isinstance(step, dict) and step.get("uses")
    ]
    require(
        "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1" in uses,
        "bounded App writer token action is missing or unpinned",
    )
    require(
        "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in uses,
        "trusted checkout action is missing or unpinned",
    )
    require(
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in uses,
        "Python setup action is missing or unpinned",
    )
    require(
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in uses,
        "evidence upload action is missing or unpinned",
    )
    require(
        "${{ secrets.AI_SDLC_OPERATOR_ADMIN_TOKEN }}" in raw,
        "admin protection-verifier secret is missing",
    )
    require(
        "${{ secrets.AI_SDLC_RUNTIME_APP_PRIVATE_KEY }}" in raw
        and "${{ vars.AI_SDLC_RUNTIME_APP_CLIENT_ID }}" in raw,
        "bounded writer App credential inputs are missing",
    )
    require(
        "${{ vars.AI_SDLC_OPERATOR_APP_INTEGRATION_ID }}" in raw,
        "trusted Operator Integration id is missing",
    )
    require(
        "AI_SDLC_OPERATOR_APP_SLUG: ${{ steps.writer-token.outputs.app-slug }}" in raw,
        "App slug must come from bounded token action rather than a new repository variable",
    )
    require(
        "vars.AI_SDLC_OPERATOR_APP_SLUG" not in raw,
        "unexpected repository App-slug variable was introduced",
    )
    require(
        "permission-contents: write" in raw and "permission-metadata: read" in raw,
        "bounded writer token permissions are incomplete",
    )
    require("actions: write" not in raw, "materialization workflow gained Actions write authority")
    require("pull-requests: write" not in raw, "materialization workflow gained PR write authority")
    require(
        "PYTHONPATH=scripts python scripts/validate_v03_vertical_writer_fence_authority.py" in raw,
        "trusted-main writer-fence authority audit is not a live precondition",
    )
    require(
        raw.index("validate_v03_vertical_writer_fence_authority.py")
        < raw.index("materialize_v03_vertical_policy_state.py"),
        "writer-fence authority audit must run before policy materialization",
    )
    require(
        "evidence/v03-vertical-writer-fence-proof.json" in raw,
        "writer-fence proof is not retained as durable workflow evidence",
    )
    require(
        "PYTHONPATH=scripts python scripts/materialize_v03_vertical_policy_state.py" in raw,
        "trusted materialization CLI is not the workflow write path",
    )
    require(
        "/issues/263/comments" in raw,
        "durable Issue #263 materialization handoff is missing",
    )


def validate_script_boundary():
    raw = SCRIPT.read_text(encoding="utf-8")
    for required in (
        'os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"',
        'os.environ.get("GITHUB_REF") != "refs/heads/main"',
        "ProtectedVerticalPolicyBundleLoader",
        "seal_receipt",
        "GitHubRepositoryProtectionVerifier",
        "ProtectedPolicyBundleMaterializer",
        '"decision_types": {}',
        "facts: dict[str, dict] = {}",
        "bootstrap-only protected Store state",
        "writer-surface proof",
        "test \"$(git rev-parse HEAD)\" = \"$GITHUB_SHA\"",
    ):
        # The checkout equality assertion lives in workflow rather than Python.
        if required.startswith("test "):
            require(required in WORKFLOW.read_text(encoding="utf-8"), "workflow checkout SHA assertion missing")
        else:
            require(required in raw, f"trusted CLI boundary missing: {required}")
    require("dream-xin/target" not in raw, "test target repository leaked into live policy materialization")
    require("FI_" not in raw, "fault-injection fixture inputs leaked into policy authority")


def main():
    validate_policy_scope()
    validate_workflow_boundary()
    validate_script_boundary()
    print("trusted v0.3 Vertical policy materialization workflow validation passed")
    print("- workflow_dispatch only + explicit refs/heads/main gate")
    print("- admin protection proof separated from bounded App contents writer")
    print("- exact six-file same-repository policy bundle")
    print("- empty evidence source + no pre-authorized Decision types")
    print("- bootstrap-only Store + trusted-main writer-surface proof bind QUIESCED receipt")
    print("- post-write #267 loader verification is mandatory")


if __name__ == "__main__":
    main()
