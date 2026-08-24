#!/usr/bin/env python3
"""Fail-closed validation for the remaining-six trusted-main live producer."""
from __future__ import annotations

import ast
from pathlib import Path

RUNNER = Path("scripts/v03_remaining_six_live_runner.py")
BOOTSTRAP = Path("scripts/run_v03_remaining_six_live.py")
LIVE = Path(".github/workflows/v03-live-remaining-six.yml")
VALIDATE = Path(".github/workflows/validate-v03-live-remaining-six.yml")

EXPECTED = (
    "cancel-before-persist-linearization",
    "persist-linearized-before-cancel",
    "duplicate-callback",
    "out-of-order-callback",
    "duplicate-worker-completion",
    "stale-candidate-result",
)
EXPECTED_KEYS = {
    "cancel-before-persist-linearization": "v03-release-fi-cancel-before-persist-linearization",
    "persist-linearized-before-cancel": "v03-release-fi-persist-linearized-before-cancel",
    "duplicate-callback": "v03-release-fi-duplicate-callback",
    "out-of-order-callback": "v03-release-fi-out-of-order-callback",
    "duplicate-worker-completion": "v03-release-fi-duplicate-worker-completion",
    "stale-candidate-result": "v03-release-fi-stale-candidate-result",
}


def require(value, message):
    if not value:
        raise AssertionError(message)


def assignments(tree):
    return {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    }


def resolved_assignment(tree, name):
    values = assignments(tree)
    strings = {
        key: node.value
        for key, node in values.items()
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    def resolve(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name) and node.id in strings:
            return strings[node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            return tuple(resolve(row) for row in node.elts)
        if isinstance(node, ast.Dict):
            return {resolve(key): resolve(value) for key, value in zip(node.keys, node.values)}
        raise AssertionError(f"unsupported static assignment shape for {name}: {ast.dump(node)}")

    require(name in values, f"missing assignment: {name}")
    return resolve(values[name])


def function(tree, name):
    rows = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(rows) == 1, f"expected exactly one {name}")
    return rows[0]


def calls(fn, name):
    return [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name) and node.func.id == name
            or isinstance(node.func, ast.Attribute) and node.func.attr == name
        )
    ]


def main():
    runner = RUNNER.read_text(encoding="utf-8")
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    live = LIVE.read_text(encoding="utf-8")
    validate = VALIDATE.read_text(encoding="utf-8")
    compile(runner, str(RUNNER), "exec")
    compile(bootstrap, str(BOOTSTRAP), "exec")
    runner_tree = ast.parse(runner)
    bootstrap_tree = ast.parse(bootstrap)

    require("MemoryStateRefBackend" not in runner, "live producer may not use in-memory Store")
    require("force-push" not in runner and "git push -f" not in runner,
            "fixture slots may not be reset/force-pushed")
    for token in (
        "ProductionGhAwVerticalResultCollector",
        "ReplaySafeProductionGhAwCollector",
        "plan_vertical_callback_record",
        "plan_vertical_takeover",
        "plan_cancel",
        "_preflight",
        "_seal",
    ):
        require(token in runner, f"runner lost production/fault boundary: {token}")

    require(tuple(resolved_assignment(runner_tree, "SCENARIOS")) == EXPECTED,
            "runner scenario set differs from closed remaining-six inventory")
    require(resolved_assignment(runner_tree, "IDEMPOTENCY") == EXPECTED_KEYS,
            "runner idempotency map differs from frozen remaining-six identities")
    require(resolved_assignment(bootstrap_tree, "REMAINING_SIX_IDEMPOTENCY") == EXPECTED_KEYS,
            "bootstrap idempotency map differs from runner")
    require("shared.IDEMPOTENCY.update(REMAINING_SIX_IDEMPOTENCY)" in bootstrap,
            "bootstrap does not process-locally extend shared start helper")
    require("runpy.run_module(\"v03_remaining_six_live_runner\", run_name=\"__main__\")" in bootstrap,
            "bootstrap does not enter dedicated remaining-six runner")

    for fn_name, scenario_const in (
        ("run_cancel_before", "CANCEL_BEFORE"),
        ("run_persist_before_cancel", "PERSIST_BEFORE_CANCEL"),
        ("run_duplicate_callback", "DUPLICATE_CALLBACK"),
        ("run_out_of_order", "OUT_OF_ORDER"),
        ("run_duplicate_worker", "DUPLICATE_WORKER"),
        ("run_stale_candidate", "STALE_CANDIDATE"),
    ):
        fn = function(runner_tree, fn_name)
        preflights = calls(fn, "_preflight")
        require(len(preflights) == 1, f"{fn_name} must assemble one production preflight")
        require(isinstance(preflights[0].args[0], ast.Name) and preflights[0].args[0].id == scenario_const,
                f"{fn_name} escaped its fixed scenario slot")
        require(len(calls(fn, "_record")) == 1, f"{fn_name} must seal one scenario record")

    require("CaptureCoordinator(" in runner and "result_source.load_content" in runner,
            "production callback capture lacks trusted output materializer")
    require("for _ in range(2)" in runner, "duplicate callback does not inject two exact deliveries")
    require("SUPERSEDED_GENERATION" in runner, "out-of-order callback lost exact rejection code")
    require("status == 404" in runner and "status == 201" in runner,
            "stale-candidate transition is not one-shot append-only")
    require("old_candidate_head_sha" in runner and "current_candidate_head_sha" in runner,
            "stale-candidate evidence does not bind A and B")

    require("workflow_dispatch:" in live and "pull_request:" not in live,
            "write-capable live workflow must be workflow_dispatch-only")
    require("github.ref != 'refs/heads/main'" in live and "github.ref == 'refs/heads/main'" in live,
            "live workflow is not fail-closed to main")
    require("permission-contents: write" in live,
            "live workflow lacks bounded Runtime App Feature write token")
    require("scripts/run_v03_remaining_six_live.py" in live,
            "live workflow bypasses official bootstrap")
    for scenario in EXPECTED:
        require(f"- {scenario}" in live, f"live workflow omits scenario {scenario}")
    require("actions/upload-artifact@" in live and "issues: write" in live,
            "live workflow lacks durable artifact/Issue receipt")

    require("pull_request:" in validate, "PR-safe validator lacks pull_request trigger")
    require("workflow_dispatch:" not in validate, "PR validator must not become live entrypoint")
    require("permission-contents: write" not in validate and "contents: write" not in validate,
            "PR validator requests contents write authority")
    require("scripts/validate_v03_remaining_six_live_runner.py" in validate,
            "PR validator does not execute fail-closed producer contract")

    print("v0.3 remaining-six trusted-main live producer validation passed")
    print("- exactly six frozen #310 scenario slots and idempotency identities")
    print("- production Actions/Store/Persist/collector path reused; no in-memory Store")
    print("- callback capture retains production content materialization authority")
    print("- stale-candidate transition is one-shot append-only; no reset/force-push")
    print("- PR validation is read-only and split from workflow_dispatch-only live authority")
    print("- evidence is sealed through existing provenance + closed authority-set ledger")


if __name__ == "__main__":
    main()
