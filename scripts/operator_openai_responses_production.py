#!/usr/bin/env python3
"""Fail-closed production binding for the OpenAI Responses adapter.

This module deliberately does not copy or reimplement Vertical, Decision,
Notification, Persist, Feature Event, Effect Lineage, or Store authority. It
loads only the final full-Vertical v0.3 production factory when that reviewed
runtime is present on the implementation baseline. Until then production
Responses construction is explicitly unavailable.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib
import inspect
from textwrap import dedent
from typing import Any, Mapping

from operator_api import SystemCapabilitiesBackend
from operator_openai_responses import (
    ADAPTER_ID,
    TOOL_CAPABILITIES,
    WRITE_CAPABILITIES,
    OpenAIResponsesOperatorAdapter,
    TrustedResponsesRegistration,
)
from operator_openai_responses_journal import StoreResponsesCallJournal
from operator_store_backends import OperatorStoreRuntime
from operator_store_remote_git import RemoteGitStateRefBackend

FULL_FACTORY_MODULE = "operator_v03_write_runtime"
FULL_FACTORY_NAME = "build_v03_vertical_write_ready_operator_bundle"
LEGACY_SEMANTIC_ONLY_FACTORY = "build_v03_write_ready_operator_bundle"
CLASSIFIED_EXECUTOR_MODULE = "operator_vertical_reconcile_classified"
CLASSIFIED_EXECUTOR_NAME = "FailureClassifyingTrustedRecoveringVerticalExecutor"
DURABLE_PERSIST_MODULE = "operator_vertical_feature_persist_gateway"
DURABLE_PERSIST_NAME = "DurableVerticalFeaturePersistGateway"
REQUIRED_RESPONSES_CAPABILITIES = frozenset(TOOL_CAPABILITIES.values())
DERIVED_RESPONSES_CAPABILITIES = frozenset({"system.capabilities"})
REQUIRED_RESPONSES_SOURCE_CAPABILITIES = (
    REQUIRED_RESPONSES_CAPABILITIES - DERIVED_RESPONSES_CAPABILITIES
)
FORBIDDEN_MODEL_CAPABILITIES = frozenset({"project.inspect", "operation.resume"})


class ResponsesProductionDependencyUnavailable(RuntimeError):
    """A reviewed hard production dependency is not on the runtime baseline."""

    code = "DEPENDENCY_UNAVAILABLE"


class ResponsesProductionBindingError(RuntimeError):
    """The final production bundle violates the approved Responses trust contract."""

    code = "PRODUCTION_BINDING_INVALID"


@dataclass(frozen=True)
class OpenAIResponsesProductionBundle:
    """Responses-facing view over one final trusted v0.3 production bundle."""

    operator_bundle: Any
    runtime: OperatorStoreRuntime
    registration: TrustedResponsesRegistration
    journal: StoreResponsesCallJournal
    backends: dict[str, Any]
    adapter: OpenAIResponsesOperatorAdapter


def _handler_catches_vertical_invariant(handler: ast.ExceptHandler) -> bool:
    caught = handler.type
    if isinstance(caught, ast.Name):
        return caught.id == "VerticalInvariantError"
    if isinstance(caught, ast.Tuple):
        return any(
            isinstance(item, ast.Name) and item.id == "VerticalInvariantError"
            for item in caught.elts
        )
    return False


def _try_reads_fresh_feature(node: ast.Try) -> bool:
    for statement in node.body:
        for child in ast.walk(statement):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr != "read_feature":
                continue
            owner = child.func.value
            if (
                isinstance(owner, ast.Attribute)
                and owner.attr == "feature_gateway"
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "executor"
            ):
                return True
    return False


def _callable_function_ast(value: Any, expected_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    source = dedent(inspect.getsource(value))
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == expected_name
    ]
    if len(functions) != 1:
        raise ValueError(f"expected exactly one function definition for {expected_name}")
    return functions[0]


def _stale_callback_rejection_boundary_present(callback: Any) -> bool:
    """Require the fresh trusted Feature read inside the deterministic rejection boundary."""

    try:
        function = _callable_function_ast(callback, "process_recorded_callback")
    except (OSError, TypeError, SyntaxError, ValueError, IndentationError):
        return False
    for node in ast.walk(function):
        if not isinstance(node, ast.Try) or not _try_reads_fresh_feature(node):
            continue
        if any(_handler_catches_vertical_invariant(handler) for handler in node.handlers):
            return True
    return False


def _durable_rejection_repair_present(reconcile: Any) -> bool:
    """Require fresh recovery to repair a durable rejection into its mapped stable stop.

    The old half-remediated shape merely skipped callback ids already present in
    ``worker.result.rejected``. The supported shape must recover the durable
    rejection payload, inspect its code, and repair BLOCKED / NEEDS_USER before
    the callback processing call can be reached.
    """

    try:
        function = _callable_function_ast(reconcile, "_reconcile_callback")
    except (OSError, TypeError, SyntaxError, ValueError, IndentationError):
        return False

    rejection_loads = []
    process_lines = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "get"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "rejected"
                and any(isinstance(target, ast.Name) and target.id == "rejection" for target in node.targets)
            ):
                rejection_loads.append(node.lineno)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "process_recorded_callback"
        ):
            process_lines.append(node.lineno)
    if not rejection_loads or not process_lines:
        return False

    required_codes = {"STALE_REVISION", "BLOCKED", "POLICY_DENIED", "NEEDS_USER"}
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "rejection"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        ):
            continue

        strings = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        if not required_codes.issubset(strings) or not {"code", "reason"}.issubset(strings):
            continue

        stable_statuses: set[str] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr != "_stable_stop":
                continue
            for keyword in child.keywords:
                if (
                    keyword.arg == "status"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    stable_statuses.add(keyword.value.value)
        if not {"BLOCKED", "NEEDS_USER"}.issubset(stable_statuses):
            continue
        if min(process_lines) <= node.lineno:
            continue
        return True
    return False


def stale_recorded_callback_convergence_available() -> bool:
    """Observe the hard #255-equivalent semantic shape on the installed baseline.

    This is a readiness probe, not a substitute implementation or release proof.
    It requires both halves of the reviewed convergence contract:

    * the fresh trusted Feature read is inside the deterministic
      ``VerticalInvariantError`` rejection boundary; and
    * fresh restart reconciliation can map an already-durable rejection to the
      required stable stop without reprocessing that callback.
    """

    try:
        callback_module = importlib.import_module("operator_vertical_callback")
        callback = getattr(callback_module, "process_recorded_callback", None)
        reconcile_module = importlib.import_module("operator_vertical_reconcile")
        executor_type = getattr(reconcile_module, "TrustedRecoveringVerticalExecutor", None)
        reconcile = getattr(executor_type, "_reconcile_callback", None) if isinstance(executor_type, type) else None
    except ImportError:
        return False
    if not callable(callback) or not callable(reconcile):
        return False
    return _stale_callback_rejection_boundary_present(callback) and _durable_rejection_repair_present(reconcile)


def _full_vertical_factory():
    try:
        module = importlib.import_module(FULL_FACTORY_MODULE)
    except ImportError as exc:
        raise ResponsesProductionDependencyUnavailable(
            "reviewed full Vertical production runtime is not on this implementation baseline"
        ) from exc
    factory = getattr(module, FULL_FACTORY_NAME, None)
    if not callable(factory):
        raise ResponsesProductionDependencyUnavailable(
            "implementation baseline lacks the authoritative full Vertical production factory"
        )
    return factory


def production_dependency_status() -> dict[str, bool]:
    """Return observation-only readiness of the two Design-v2 hard dependencies."""

    try:
        _full_vertical_factory()
        full_vertical = True
    except ResponsesProductionDependencyUnavailable:
        full_vertical = False
    return {
        "full_vertical_production_factory": full_vertical,
        "stale_recorded_callback_convergence": stale_recorded_callback_convergence_available(),
    }


def require_production_dependencies() -> None:
    status = production_dependency_status()
    missing = sorted(key for key, ready in status.items() if ready is not True)
    if missing:
        raise ResponsesProductionDependencyUnavailable(
            "Responses Supported production dependencies are unavailable: " + ", ".join(missing)
        )


def _reviewed_runtime_type(module_name: str, class_name: str) -> type:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ResponsesProductionDependencyUnavailable(
            f"reviewed final production runtime type is unavailable: {module_name}.{class_name}"
        ) from exc
    expected = getattr(module, class_name, None)
    if not isinstance(expected, type):
        raise ResponsesProductionDependencyUnavailable(
            f"reviewed final production runtime type is missing: {module_name}.{class_name}"
        )
    return expected


def _persist_delegate(executor: Any) -> Any:
    persist_gateway = getattr(executor, "persist_gateway", None)
    if persist_gateway is None:
        raise ResponsesProductionBindingError("final production executor exposes no Persist gateway")
    try:
        return getattr(persist_gateway, "delegate", persist_gateway)
    except Exception as exc:
        raise ResponsesProductionBindingError(
            "final durable Persist gateway is not bound before Responses exposure"
        ) from exc


def _require_runtime(bundle: Any) -> OperatorStoreRuntime:
    runtime = getattr(bundle, "runtime", None)
    if not isinstance(runtime, OperatorStoreRuntime):
        raise ResponsesProductionBindingError(
            "final production bundle does not expose the trusted Operator Store runtime"
        )
    if not isinstance(runtime.backend, RemoteGitStateRefBackend):
        raise ResponsesProductionBindingError(
            "Responses production requires the reviewed Remote-Git Store reference backend"
        )
    verifier = getattr(runtime, "protection_verifier", None)
    if verifier is None or bool(getattr(verifier, "test_only", False)):
        raise ResponsesProductionBindingError(
            "Responses production cannot use a missing or test-only Store protection verifier"
        )
    return runtime


def _require_shared_runtime(bundle: Any, runtime: OperatorStoreRuntime) -> None:
    write_bundle = getattr(bundle, "write_bundle", None)
    vertical_bundle = getattr(bundle, "vertical_bundle", None)
    if write_bundle is None or getattr(write_bundle, "runtime", None) is not runtime:
        raise ResponsesProductionBindingError(
            "adapter write composition does not share the final protected Store runtime"
        )
    if vertical_bundle is None or getattr(vertical_bundle, "runtime", None) is not runtime:
        raise ResponsesProductionBindingError(
            "Vertical composition does not share the final protected Store runtime"
        )

    executor = getattr(bundle, "executor", None)
    if executor is None or getattr(executor, "runtime", None) is not runtime:
        raise ResponsesProductionBindingError(
            "Vertical recovery executor does not share the final protected Store runtime"
        )

    coordinator = getattr(bundle, "decision_notification_coordinator", None)
    if coordinator is None or getattr(coordinator, "runtime", None) is not runtime:
        raise ResponsesProductionBindingError(
            "Decision/Notification coordinator does not share the final protected Store runtime"
        )

    delegate = _persist_delegate(executor)
    if getattr(delegate, "runtime", None) is not runtime:
        raise ResponsesProductionBindingError(
            "final durable Persist gateway does not share the protected Store runtime"
        )


def _require_final_runtime_types(bundle: Any) -> None:
    """Reject a same-named factory that composes obsolete recovery/Persist authority."""

    executor = getattr(bundle, "executor", None)
    classified_executor = _reviewed_runtime_type(
        CLASSIFIED_EXECUTOR_MODULE,
        CLASSIFIED_EXECUTOR_NAME,
    )
    if not isinstance(executor, classified_executor):
        raise ResponsesProductionBindingError(
            "final production bundle does not use classified trusted Vertical recovery"
        )

    delegate = _persist_delegate(executor)
    durable_persist = _reviewed_runtime_type(DURABLE_PERSIST_MODULE, DURABLE_PERSIST_NAME)
    if not isinstance(delegate, durable_persist):
        raise ResponsesProductionBindingError(
            "final production bundle does not use the exact durable Vertical Persist gateway"
        )

    callback_coordinator = getattr(bundle, "callback_coordinator", None)
    if callback_coordinator is None or getattr(callback_coordinator, "executor", None) is not executor:
        raise ResponsesProductionBindingError(
            "final callback coordinator is not bound to the exact classified recovery executor"
        )


def _is_vertical_start_backend(value: Any) -> bool:
    """Check the reviewed runtime type without statically importing absent dependencies."""

    try:
        module = importlib.import_module("operator_vertical_runtime")
        expected = getattr(module, "VerticalLoopStartBackend", None)
    except ImportError:
        return False
    return isinstance(expected, type) and isinstance(value, expected)


def _responses_backends(bundle: Any) -> dict[str, Any]:
    source = getattr(bundle, "backends", None)
    if not isinstance(source, Mapping):
        raise ResponsesProductionBindingError("final production bundle has no canonical backend map")
    missing = REQUIRED_RESPONSES_SOURCE_CAPABILITIES - set(source)
    if missing:
        raise ResponsesProductionBindingError(
            f"final production bundle is missing Responses source capabilities: {sorted(missing)}"
        )

    writes = getattr(bundle, "adapter_write_backends", None)
    if not isinstance(writes, Mapping) or set(writes) != set(WRITE_CAPABILITIES):
        raise ResponsesProductionBindingError(
            "final production bundle does not expose exactly the frozen four-write slice"
        )

    start = source.get("operation.start")
    if not _is_vertical_start_backend(start):
        raise ResponsesProductionBindingError(
            "Responses operation.start is not the trusted profile-bound Vertical start backend"
        )

    # system.capabilities is canonical derived introspection, not a separate
    # production authority. Always derive it over the exact Responses-filtered
    # backend map so broader upstream capabilities (for example project.inspect
    # or server-only operation.resume) cannot appear AVAILABLE to the model.
    result = {
        capability: source[capability]
        for capability in REQUIRED_RESPONSES_SOURCE_CAPABILITIES
    }
    result["system.capabilities"] = SystemCapabilitiesBackend(result)
    if set(result) != set(REQUIRED_RESPONSES_CAPABILITIES):
        raise ResponsesProductionBindingError("Responses model-facing backend map drifted")
    if set(result) & FORBIDDEN_MODEL_CAPABILITIES:
        raise ResponsesProductionBindingError("server-only capability leaked into Responses map")
    return result


def build_openai_responses_production_bundle(
    *,
    config: Any,
    feature_id: str,
    registration_id: str,
    provider_scope_id: str,
    target_read_token: str,
    protection_verifier: Any,
    rollout_verifier: Any,
    resolution_policy_verifier: Any,
    feature_gateway: Any,
    feature_event_gateway: Any,
    dispatch_gateway: Any,
    collector_content_loader: Any,
    policy_verifier: Any,
    trusted_context_digest: str,
    collector_namespace_policy: str,
    trusted_role_policy: str,
    max_auto_steps: int = 16,
    github_api_base: str = "https://api.github.com",
    reader_http_get: Any = None,
    clock: Any = None,
) -> OpenAIResponsesProductionBundle:
    """Build Responses only over the authoritative full-Vertical production path.

    No semantic-only or test fallback exists. If the reviewed hard dependencies
    are absent, construction fails before any Store mutation or external launch.
    """

    require_production_dependencies()
    factory = _full_vertical_factory()
    kwargs = {
        "config": config,
        "adapter_id": ADAPTER_ID,
        "feature_id": feature_id,
        "target_read_token": target_read_token,
        "protection_verifier": protection_verifier,
        "rollout_verifier": rollout_verifier,
        "resolution_policy_verifier": resolution_policy_verifier,
        "feature_gateway": feature_gateway,
        "feature_event_gateway": feature_event_gateway,
        "dispatch_gateway": dispatch_gateway,
        "collector_content_loader": collector_content_loader,
        "policy_verifier": policy_verifier,
        "trusted_context_digest": trusted_context_digest,
        "collector_namespace_policy": collector_namespace_policy,
        "trusted_role_policy": trusted_role_policy,
        "max_auto_steps": max_auto_steps,
        "github_api_base": github_api_base,
        "clock": clock,
    }
    if reader_http_get is not None:
        kwargs["reader_http_get"] = reader_http_get
    operator_bundle = factory(**kwargs)

    runtime = _require_runtime(operator_bundle)
    _require_shared_runtime(operator_bundle, runtime)
    _require_final_runtime_types(operator_bundle)
    backends = _responses_backends(operator_bundle)

    trusted_provider = getattr(
        getattr(operator_bundle, "write_bundle", None),
        "trusted_context_provider",
        None,
    )
    if trusted_provider is None or not callable(getattr(trusted_provider, "for_request", None)):
        raise ResponsesProductionBindingError(
            "final production bundle lacks the trusted canonical context provider"
        )
    target_repository = str(getattr(config, "target_repository", "") or "")
    feature_ref = getattr(config, "feature_ref", None)
    if not target_repository or not callable(feature_ref):
        raise ResponsesProductionBindingError("trusted production runtime config is incomplete")
    target_ref = str(feature_ref(feature_id))
    trusted_context = trusted_provider.for_request(
        {"repository": target_repository, "feature_id": feature_id}
    )
    if not isinstance(trusted_context, dict):
        raise ResponsesProductionBindingError("trusted context provider returned invalid context")
    trusted_context = dict(trusted_context)
    trusted_context["trusted_context_digest"] = trusted_context_digest

    registration = TrustedResponsesRegistration(
        registration_id=registration_id,
        provider_scope_id=provider_scope_id,
        target_repository=target_repository,
        feature_refs={feature_id: target_ref},
        trusted_context=trusted_context,
        human_principal=str(getattr(config, "principal", "") or "") or None,
    )
    journal = StoreResponsesCallJournal(runtime)
    adapter = OpenAIResponsesOperatorAdapter(
        registration=registration,
        backends=backends,
        journal=journal,
    )
    return OpenAIResponsesProductionBundle(
        operator_bundle=operator_bundle,
        runtime=runtime,
        registration=registration,
        journal=journal,
        backends=backends,
        adapter=adapter,
    )
