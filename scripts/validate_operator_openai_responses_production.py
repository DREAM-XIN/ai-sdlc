#!/usr/bin/env python3
"""Validate fail-closed OpenAI Responses production binding before Lane B is available.

This validator is implementation support only. It does not claim Supported status
or replace the mandatory Lane-B proof against the final reviewed composition.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import patch

from operator_api import SystemCapabilitiesBackend
from operator_openai_responses import TOOL_CAPABILITIES, WRITE_CAPABILITIES
from operator_openai_responses_production import (
    CLASSIFIED_EXECUTOR_MODULE,
    CLASSIFIED_EXECUTOR_NAME,
    DURABLE_PERSIST_MODULE,
    DURABLE_PERSIST_NAME,
    FULL_FACTORY_NAME,
    LEGACY_SEMANTIC_ONLY_FACTORY,
    REQUIRED_RESPONSES_CAPABILITIES,
    REQUIRED_RESPONSES_SOURCE_CAPABILITIES,
    ResponsesProductionBindingError,
    ResponsesProductionDependencyUnavailable,
    _require_final_runtime_types,
    _require_runtime,
    _require_shared_runtime,
    _responses_backends,
    build_openai_responses_production_bundle,
    production_dependency_status,
)
from operator_store_backends import OperatorStoreRuntime
from operator_store_git import MemoryStateRefBackend
from operator_store_protection import PROTECTED, StaticProtectionVerifier
from operator_vertical_runtime import VerticalLoopStartBackend

REPOSITORY = "DREAM-XIN/responses-production-fixture"
STATE_REF = "refs/heads/ai-sdlc-operator-state"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_binding_error(callable_, message: str) -> None:
    try:
        callable_()
    except ResponsesProductionBindingError:
        return
    raise AssertionError(message)


class _Backend:
    def availability(self, capability, trusted_context):
        return True, "AVAILABLE"

    def invoke(self, request, trusted_context):
        return {}


class _Executor:
    def advance_until_stop(self, *, operation_id):
        return {"operation_id": operation_id}


def _memory_runtime() -> OperatorStoreRuntime:
    return OperatorStoreRuntime(
        backend=MemoryStateRefBackend(repository=REPOSITORY, state_ref=STATE_REF),
        protection_verifier=StaticProtectionVerifier(status=PROTECTED),
        clock=lambda: "2026-08-11T11:30:00Z",
    )


def validate_no_semantic_only_fallback() -> None:
    import operator_openai_responses_production as module

    tree = ast.parse(inspect.getsource(module))
    legacy_loads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "LEGACY_SEMANTIC_ONLY_FACTORY"
        and isinstance(node.ctx, ast.Load)
    ]
    require(not legacy_loads, "legacy semantic-only factory is consulted by production binding")
    require(
        FULL_FACTORY_NAME != LEGACY_SEMANTIC_ONLY_FACTORY,
        "full Vertical factory identity collapsed onto semantic-only compatibility helper",
    )


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def validate_authority_check_order() -> None:
    import operator_openai_responses_production as module

    tree = ast.parse(inspect.getsource(module))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_openai_responses_production_bundle"
    ]
    require(len(functions) == 1, "Responses production builder definition drifted")
    calls: dict[str, int] = {}
    for node in ast.walk(functions[0]):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name:
            calls[name] = min(calls.get(name, node.lineno), node.lineno)

    sequence = (
        "require_production_dependencies",
        "_full_vertical_factory",
        "factory",
        "_require_runtime",
        "_require_shared_runtime",
        "_require_final_runtime_types",
        "_responses_backends",
        "StoreResponsesCallJournal",
        "OpenAIResponsesOperatorAdapter",
    )
    missing = [name for name in sequence if name not in calls]
    require(not missing, f"Responses production authority sequence lost calls: {missing}")
    positions = [calls[name] for name in sequence]
    require(
        positions == sorted(positions) and len(set(positions)) == len(positions),
        f"Responses production authority checks moved out of fail-closed order: {dict(zip(sequence, positions))}",
    )


def validate_missing_dependency_fails_before_factory() -> None:
    with patch(
        "operator_openai_responses_production.importlib.import_module",
        side_effect=ImportError("fixture missing reviewed dependency"),
    ):
        try:
            build_openai_responses_production_bundle(
                config=None,
                feature_id="F-RESPONSES-0001",
                registration_id="responses-production-fixture",
                provider_scope_id="provider-production-fixture",
                target_read_token="fixture",
                protection_verifier=None,
                rollout_verifier=None,
                resolution_policy_verifier=None,
                feature_gateway=None,
                feature_event_gateway=None,
                dispatch_gateway=None,
                collector_content_loader=None,
                policy_verifier=None,
                trusted_context_digest="fixture",
                collector_namespace_policy="fixture",
                trusted_role_policy="fixture",
            )
        except ResponsesProductionDependencyUnavailable as exc:
            require(exc.code == "DEPENDENCY_UNAVAILABLE", "dependency error code drifted")
        else:
            raise AssertionError("missing full production runtime unexpectedly constructed Responses")


def validate_model_backend_filter() -> None:
    require(
        REQUIRED_RESPONSES_CAPABILITIES == frozenset(TOOL_CAPABILITIES.values()),
        "Responses required capability set drifted from the fixed tool registry",
    )
    require(
        REQUIRED_RESPONSES_SOURCE_CAPABILITIES
        == REQUIRED_RESPONSES_CAPABILITIES - {"system.capabilities"},
        "system.capabilities is no longer the only derived Responses capability",
    )

    # Match the real final Vertical production bundle: it exposes trusted
    # canonical business/Store backends but does not need to carry the derived
    # system.capabilities introspection backend.
    source = {capability: _Backend() for capability in REQUIRED_RESPONSES_SOURCE_CAPABILITIES}
    source["operation.start"] = VerticalLoopStartBackend(delegate=_Backend(), executor=_Executor())

    # Deliberately make the upstream map broader and even provide a bogus
    # system.capabilities backend. Responses must ignore all three when it
    # constructs the exact model-facing view.
    source["project.inspect"] = _Backend()
    source["operation.resume"] = _Backend()
    upstream_capabilities = _Backend()
    source["system.capabilities"] = upstream_capabilities

    bundle = SimpleNamespace(
        backends=source,
        adapter_write_backends={capability: source[capability] for capability in WRITE_CAPABILITIES},
    )
    filtered = _responses_backends(bundle)
    require(set(filtered) == set(REQUIRED_RESPONSES_CAPABILITIES), "Responses backend filter is not exact")
    require("project.inspect" not in filtered, "project.inspect leaked into Responses model-facing map")
    require("operation.resume" not in filtered, "operation.resume leaked into Responses model-facing map")
    require(
        isinstance(filtered["system.capabilities"], SystemCapabilitiesBackend),
        "Responses did not derive canonical system.capabilities over its filtered map",
    )
    require(
        filtered["system.capabilities"] is not upstream_capabilities,
        "Responses reused an upstream capabilities backend that may observe a broader authority map",
    )

    matrix = filtered["system.capabilities"].invoke({}, {})
    rows = {row["id"]: row for row in matrix["capabilities"]}
    require(rows["system.capabilities"]["available"] is True, "derived capabilities backend hid itself")
    require(rows["feature.status"]["available"] is True, "exposed Responses read became unavailable")
    require(rows["operation.start"]["available"] is True, "trusted Vertical start became unavailable")
    require(
        rows["project.inspect"] == {
            "id": "project.inspect",
            "available": False,
            "reason": "BACKEND_NOT_IMPLEMENTED",
        },
        f"derived Responses capabilities leaked project.inspect availability: {rows['project.inspect']}",
    )
    require(
        rows["operation.resume"] == {
            "id": "operation.resume",
            "available": False,
            "reason": "BACKEND_NOT_IMPLEMENTED",
        },
        f"derived Responses capabilities leaked operation.resume availability: {rows['operation.resume']}",
    )

    missing_real = dict(source)
    missing_real.pop("feature.status")
    expect_binding_error(
        lambda: _responses_backends(
            SimpleNamespace(
                backends=missing_real,
                adapter_write_backends={capability: missing_real[capability] for capability in WRITE_CAPABILITIES},
            )
        ),
        "missing real Responses source capability unexpectedly accepted",
    )

    bad_writes = dict(bundle.adapter_write_backends)
    bad_writes["operation.resume"] = _Backend()
    expect_binding_error(
        lambda: _responses_backends(SimpleNamespace(backends=source, adapter_write_backends=bad_writes)),
        "expanded write slice unexpectedly accepted",
    )

    raw_start = dict(source)
    raw_start["operation.start"] = _Backend()
    expect_binding_error(
        lambda: _responses_backends(
            SimpleNamespace(
                backends=raw_start,
                adapter_write_backends={capability: raw_start[capability] for capability in WRITE_CAPABILITIES},
            )
        ),
        "raw Store-only/non-Vertical operation.start unexpectedly accepted",
    )


def validate_test_store_rejected() -> None:
    runtime = _memory_runtime()
    expect_binding_error(
        lambda: _require_runtime(SimpleNamespace(runtime=runtime)),
        "Memory/test Store runtime unexpectedly accepted as production Responses authority",
    )


def validate_shared_runtime_fence() -> None:
    runtime = _memory_runtime()
    persist = SimpleNamespace(runtime=runtime)
    executor = SimpleNamespace(runtime=runtime, persist_gateway=SimpleNamespace(delegate=persist))
    valid = SimpleNamespace(
        write_bundle=SimpleNamespace(runtime=runtime),
        vertical_bundle=SimpleNamespace(runtime=runtime),
        executor=executor,
        decision_notification_coordinator=SimpleNamespace(runtime=runtime),
    )
    _require_shared_runtime(valid, runtime)

    foreign = _memory_runtime()
    expect_binding_error(
        lambda: _require_shared_runtime(
            SimpleNamespace(
                write_bundle=SimpleNamespace(runtime=runtime),
                vertical_bundle=SimpleNamespace(runtime=foreign),
                executor=executor,
                decision_notification_coordinator=SimpleNamespace(runtime=runtime),
            ),
            runtime,
        ),
        "cross-Store Vertical composition unexpectedly accepted",
    )
    expect_binding_error(
        lambda: _require_shared_runtime(
            SimpleNamespace(
                write_bundle=SimpleNamespace(runtime=runtime),
                vertical_bundle=SimpleNamespace(runtime=runtime),
                executor=SimpleNamespace(
                    runtime=runtime,
                    persist_gateway=SimpleNamespace(delegate=SimpleNamespace(runtime=foreign)),
                ),
                decision_notification_coordinator=SimpleNamespace(runtime=runtime),
            ),
            runtime,
        ),
        "cross-Store Persist authority unexpectedly accepted",
    )

    class UnboundBridge:
        @property
        def delegate(self):
            raise RuntimeError("not bound")

    expect_binding_error(
        lambda: _require_shared_runtime(
            SimpleNamespace(
                write_bundle=SimpleNamespace(runtime=runtime),
                vertical_bundle=SimpleNamespace(runtime=runtime),
                executor=SimpleNamespace(runtime=runtime, persist_gateway=UnboundBridge()),
                decision_notification_coordinator=SimpleNamespace(runtime=runtime),
            ),
            runtime,
        ),
        "unbound deferred Persist bridge escaped as a raw runtime failure",
    )


def validate_exact_final_runtime_types() -> None:
    require(
        CLASSIFIED_EXECUTOR_NAME == "FailureClassifyingTrustedRecoveringVerticalExecutor",
        "classified recovery executor identity drifted",
    )
    require(
        DURABLE_PERSIST_NAME == "DurableVerticalFeaturePersistGateway",
        "durable Persist gateway identity drifted",
    )

    class ClassifiedFixture:
        pass

    class DurablePersistFixture:
        pass

    def type_lookup(module_name, class_name):
        if (module_name, class_name) == (CLASSIFIED_EXECUTOR_MODULE, CLASSIFIED_EXECUTOR_NAME):
            return ClassifiedFixture
        if (module_name, class_name) == (DURABLE_PERSIST_MODULE, DURABLE_PERSIST_NAME):
            return DurablePersistFixture
        raise AssertionError((module_name, class_name))

    durable = DurablePersistFixture()
    executor = ClassifiedFixture()
    executor.persist_gateway = SimpleNamespace(delegate=durable)
    valid = SimpleNamespace(
        executor=executor,
        callback_coordinator=SimpleNamespace(executor=executor),
    )
    with patch("operator_openai_responses_production._reviewed_runtime_type", side_effect=type_lookup):
        _require_final_runtime_types(valid)
        expect_binding_error(
            lambda: _require_final_runtime_types(
                SimpleNamespace(
                    executor=SimpleNamespace(persist_gateway=SimpleNamespace(delegate=durable)),
                    callback_coordinator=SimpleNamespace(executor=executor),
                )
            ),
            "obsolete/non-classified recovery executor unexpectedly accepted",
        )

        wrong_persist_executor = ClassifiedFixture()
        wrong_persist_executor.persist_gateway = SimpleNamespace(delegate=object())
        expect_binding_error(
            lambda: _require_final_runtime_types(
                SimpleNamespace(
                    executor=wrong_persist_executor,
                    callback_coordinator=SimpleNamespace(executor=wrong_persist_executor),
                )
            ),
            "non-durable final Persist gateway unexpectedly accepted",
        )

        expect_binding_error(
            lambda: _require_final_runtime_types(
                SimpleNamespace(
                    executor=executor,
                    callback_coordinator=SimpleNamespace(executor=ClassifiedFixture()),
                )
            ),
            "callback coordinator bound to a different recovery executor unexpectedly accepted",
        )


def main() -> None:
    validate_no_semantic_only_fallback()
    validate_authority_check_order()
    validate_missing_dependency_fails_before_factory()
    validate_model_backend_filter()
    validate_test_store_rejected()
    validate_shared_runtime_fence()
    validate_exact_final_runtime_types()

    status = production_dependency_status()
    require(
        set(status) == {"full_vertical_production_factory", "stale_recorded_callback_convergence"},
        "production dependency readiness key set drifted",
    )
    require(all(type(value) is bool for value in status.values()), "dependency readiness is not boolean")

    print("OpenAI Responses production binding validation passed")
    print("- dependency gate, final factory, Store/type checks and model backend exposure stay in fail-closed order")
    print("- only the authoritative full-Vertical production factory is eligible; no semantic-only fallback")
    print("- final recovery must be classified and final Persist must use the exact durable gateway")
    print("- callback coordinator must share the exact classified recovery executor")
    print("- Memory/test Store, unbound Persist bridge, raw start, expanded writes and split Store authority fail closed")
    print("- system.capabilities is derived over the exact filtered Responses map and cannot leak broader backend availability")
    print("- Responses model-facing backend map remains the exact ten-tool capability set")
    print(f"- current implementation-baseline hard dependency status: {status}")
    print("- this validator is not Lane-B Supported-production evidence")


if __name__ == "__main__":
    main()
