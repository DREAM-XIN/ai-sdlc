#!/usr/bin/env python3
"""Static authority-order validation for the real-runtime smoke entrypoint."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "v03_real_runtime_effect_safety_smoke.py"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def prepared_subscript(value: ast.AST, key: str) -> bool:
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name) or value.func.id != "str":
        return False
    if len(value.args) != 1:
        return False
    item = value.args[0]
    return (
        isinstance(item, ast.Subscript)
        and isinstance(item.value, ast.Name)
        and item.value.id == "prepared"
        and isinstance(item.slice, ast.Constant)
        and item.slice.value == key
    )


def main():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    require("main" in functions, "real smoke entrypoint lacks main()")
    main_fn = functions["main"]

    calls = [node for node in ast.walk(main_fn) if isinstance(node, ast.Call)]
    missing_calls = [node for node in calls if call_name(node) == "missing_prerequisites"]
    fixture_calls = [node for node in calls if call_name(node) == "prepare_real_runtime_smoke_fixture"]
    execution_calls = [node for node in calls if call_name(node) == "require_real_runtime_execution_authority"]
    transport_calls = [node for node in calls if call_name(node) == "GitHubActionsWorkflowTransport"]
    launch_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "launch"
    ]
    require(len(missing_calls) == 1, "real smoke must have exactly one full-prerequisite gate")
    require(len(fixture_calls) == 1, "real smoke must have exactly one selector-derived fixture gate")
    require(len(execution_calls) == 1, "real smoke must have exactly one manual trusted-main execution gate")
    require(len(transport_calls) >= 1, "real smoke no longer constructs the trusted Actions transport")
    require(len(launch_calls) == 2, "real smoke must contain exactly G0 and G1 launch/adoption calls")

    prerequisite_line = missing_calls[0].lineno
    fixture_line = fixture_calls[0].lineno
    execution_line = execution_calls[0].lineno
    first_transport_line = min(node.lineno for node in transport_calls)
    first_launch_line = min(node.lineno for node in launch_calls)
    require(prerequisite_line < fixture_line, "fixture gate moved before complete trusted-main prerequisite classification")
    require(fixture_line < execution_line, "manual execution authority is checked before exact selector fixture binding")
    require(execution_line < first_transport_line, "trusted Actions transport is constructed before manual trusted-main execution authorization")
    require(execution_line < first_launch_line, "external launch appears before manual trusted-main execution authorization")

    execution_keywords = {kw.arg: kw.value for kw in execution_calls[0].keywords if kw.arg}
    require(set(execution_keywords) == {"github_event_name", "github_ref", "external_execution_authorized"}, "execution gate keyword set drifted")
    for field, env_name in (
        ("github_event_name", "GITHUB_EVENT_NAME"),
        ("github_ref", "GITHUB_REF"),
        ("external_execution_authorized", "FI_EXTERNAL_EXECUTION_AUTHORIZED"),
    ):
        value = execution_keywords[field]
        require(
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and isinstance(value.func.value, ast.Attribute)
            and isinstance(value.func.value.value, ast.Name)
            and value.func.value.value.id == "os"
            and value.func.value.attr == "environ"
            and value.func.attr == "get"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and value.args[0].value == env_name,
            f"execution gate {field} is not read from {env_name}",
        )

    make_dispatch = next(
        (node for node in ast.walk(main_fn) if isinstance(node, ast.FunctionDef) and node.name == "make_dispatch"),
        None,
    )
    require(make_dispatch is not None, "real smoke lacks bounded make_dispatch()")
    returns = [node for node in ast.walk(make_dispatch) if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)]
    require(len(returns) == 1, "make_dispatch must return one explicit dispatch object")
    mapping = {}
    for key_node, value_node in zip(returns[0].value.keys, returns[0].value.values):
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
            mapping[key_node.value] = value_node
    for field in ("role", "task_id", "task_identity", "current_stage"):
        require(field in mapping, f"make_dispatch lacks {field}")
        require(prepared_subscript(mapping[field], field), f"make_dispatch.{field} is not selector-derived from prepared fixture")

    constants = {
        node.value
        for node in ast.walk(make_dispatch)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    require("reviewer" not in constants and "qa" not in constants and "developer" not in constants, "make_dispatch hardcodes a Worker role")
    require(not any(value.startswith("FI-REAL-RUNTIME-") for value in constants), "make_dispatch hardcodes a synthetic FI task identity")

    workflow_for = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "workflow_for"
    ]
    require(len(workflow_for) == 1, "real smoke must select exactly one workflow through trusted role mapping")
    role_argument = workflow_for[0].args[0] if workflow_for[0].args else None
    require(role_argument is not None and prepared_subscript(role_argument, "role"), "workflow selection is not based on selector-derived role")
    require(execution_line < workflow_for[0].lineno < first_launch_line, "workflow role mapping is outside the authorized execution window")

    print("v0.3 real-runtime smoke authority-order validation passed")
    print("- complete prerequisites -> selector fixture -> manual trusted-main authority is the mandatory gate order")
    print("- all three gates precede every trusted transport construction and external launch")
    print("- execution authority is read only from GITHUB_EVENT_NAME/GITHUB_REF/FI_EXTERNAL_EXECUTION_AUTHORIZED")
    print("- role/task/task_identity/current_stage are selector-derived, not hardcoded")
    print("- selected gh-aw workflow is derived from the exact selector role")


if __name__ == "__main__":
    main()
