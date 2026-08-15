#!/usr/bin/env python3
"""Store-backed one-shot external-create gate around the trusted Vertical dispatch gateway."""
from __future__ import annotations

from typing import Any

from operator_store_model import canonical_json, dispatch_claim_id
from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_external_create_attempt import find_external_create_attempt
from operator_effect_lineage_fences import plan_lineage_external_create_attempt


_WORKFLOW_BINDINGS = {
    "ai-sdlc-gh-aw-worker.lock.yml": {
        "worker_id": "workflow:ai-sdlc-gh-aw-worker.lock.yml",
        "profile": "copilot",
        "selection_policy": "v03-frozen-vertical-workflow-map/v1",
    },
    "ai-sdlc-gh-aw-worker-codex.lock.yml": {
        "worker_id": "workflow:ai-sdlc-gh-aw-worker-codex.lock.yml",
        "profile": "codex",
        "selection_policy": "v03-frozen-vertical-workflow-map/v1",
    },
    "ai-sdlc-gh-aw-worker-claude.lock.yml": {
        "worker_id": "workflow:ai-sdlc-gh-aw-worker-claude.lock.yml",
        "profile": "claude",
        "selection_policy": "v03-frozen-vertical-workflow-map/v1",
    },
    "ai-sdlc-gh-aw-worker-gemini.lock.yml": {
        "worker_id": "workflow:ai-sdlc-gh-aw-worker-gemini.lock.yml",
        "profile": "gemini",
        "selection_policy": "v03-frozen-vertical-workflow-map/v1",
    },
    "ai-sdlc-gh-aw-reviewer-claude.lock.yml": {
        "worker_id": "code-review-reviewer-claude",
        "profile": "claude",
        "selection_policy": "v03-frozen-reviewer-provider-order/v1",
        "credential_name": "ANTHROPIC_API_KEY",
    },
    "ai-sdlc-gh-aw-reviewer-copilot.lock.yml": {
        "worker_id": "code-review-reviewer-copilot",
        "profile": "copilot",
        "selection_policy": "v03-frozen-reviewer-provider-order/v1",
        "credential_name": "COPILOT_GITHUB_TOKEN",
    },
    "ai-sdlc-gh-aw-qa-gemini.lock.yml": {
        "worker_id": "verification-qa-gemini",
        "profile": "gemini",
        "selection_policy": "v03-frozen-vertical-workflow-map/v1",
    },
    "ai-sdlc-gh-aw-qa-copilot.lock.yml": {
        "worker_id": "verification-qa-copilot",
        "profile": "copilot",
        "selection_policy": "v03-frozen-vertical-workflow-map/v1",
    },
}


def _normalize_receipt(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        return {"lookup_state": "UNKNOWN", "receipt_id": None}
    state = str(receipt.get("lookup_state", "UNKNOWN"))
    if state not in {"LAUNCHED", "NOT_LAUNCHED", "UNKNOWN"}:
        state = "UNKNOWN"
    return {"lookup_state": state, "receipt_id": receipt.get("receipt_id")}


class StoreBackedOneShotExternalCreateGateway:
    """Exactly one Store-elected caller may cross the delegate's create boundary."""

    def __init__(
        self,
        *,
        runtime,
        delegate,
        trusted_context_digest: str,
        effect_lineage_required: bool,
    ):
        if runtime is None or delegate is None:
            raise ValueError("one-shot external-create gateway requires runtime and delegate")
        if not trusted_context_digest:
            raise ValueError("one-shot external-create gateway requires trusted context")
        if not effect_lineage_required:
            raise ValueError("production external-create gate requires Effect Lineage enforcement")
        self.runtime = runtime
        self.delegate = delegate
        self.trusted_context_digest = trusted_context_digest

    def _binding_from_delegate(self, dispatch: dict[str, Any]) -> dict[str, str]:
        explicit = getattr(self.delegate, "execution_binding", None)
        if callable(explicit):
            value = explicit(dispatch=dispatch)
            if isinstance(value, dict):
                return dict(value)

        workflows = getattr(self.delegate, "workflows", None)
        workflow_for = getattr(workflows, "workflow_for", None)
        default_branch = getattr(workflows, "default_branch", None)
        if not callable(workflow_for) or not isinstance(default_branch, str) or not default_branch:
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "trusted dispatch gateway does not expose an exact workflow execution binding",
            )
        role = str(dispatch.get("role") or "")
        workflow_file = str(workflow_for(role))
        frozen = _WORKFLOW_BINDINGS.get(workflow_file)
        if frozen is None:
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "selected workflow is not in the frozen v0.3 execution binding set",
            )
        binding = {
            "worker_id": str(frozen["worker_id"]),
            "role": role,
            "profile": str(frozen["profile"]),
            "workflow_file": workflow_file,
            "selection_policy": str(frozen["selection_policy"]),
            "default_branch": default_branch,
        }
        if frozen.get("credential_name"):
            binding["credential_name"] = str(frozen["credential_name"])
        return binding

    def _exact_lookup(self, binding: dict[str, Any], external_dispatch_key: str) -> dict[str, Any]:
        exact = getattr(self.delegate, "lookup_execution_binding", None)
        if callable(exact):
            try:
                return _normalize_receipt(
                    exact(
                        execution_binding=dict(binding),
                        external_dispatch_key=external_dispatch_key,
                    )
                )
            except Exception:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}

        transport = getattr(self.delegate, "transport", None)
        lookup = getattr(transport, "lookup", None)
        if callable(lookup):
            try:
                receipt = lookup(
                    workflow=str(binding["workflow_file"]),
                    ref=str(binding["default_branch"]),
                    dispatch_key=external_dispatch_key,
                )
            except Exception:
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            if receipt is None:
                return {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
            return _normalize_receipt(receipt)

        try:
            current = self._binding_from_delegate({"role": binding["role"]})
        except Exception:
            return {"lookup_state": "UNKNOWN", "receipt_id": None}
        if canonical_json(current) != canonical_json(binding):
            return {"lookup_state": "UNKNOWN", "receipt_id": None}
        try:
            return _normalize_receipt(
                self.delegate.lookup(external_dispatch_key=external_dispatch_key)
            )
        except Exception:
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def _existing_attempt(self, external_dispatch_key: str):
        snapshot = self.runtime.backend.read_snapshot()
        return find_external_create_attempt(
            snapshot,
            external_dispatch_key=external_dispatch_key,
        )

    def lookup(self, *, external_dispatch_key: str) -> dict[str, Any]:
        attempt = self._existing_attempt(external_dispatch_key)
        if attempt is not None:
            receipt = self._exact_lookup(
                attempt["execution_binding"],
                external_dispatch_key,
            )
            if receipt["lookup_state"] == "NOT_LAUNCHED":
                return {"lookup_state": "UNKNOWN", "receipt_id": None}
            return receipt
        try:
            return _normalize_receipt(
                self.delegate.lookup(external_dispatch_key=external_dispatch_key)
            )
        except Exception:
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

    def launch(self, *, dispatch: dict[str, Any]) -> dict[str, Any]:
        if dispatch.get("operation_profile") != VERTICAL_PROFILE:
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "one-shot external-create gateway requires the production Vertical profile",
            )
        operation_id = str(dispatch.get("operation_id") or "")
        generation = dispatch.get("operation_generation")
        semantic_effect_key = str(dispatch.get("semantic_effect_key") or "")
        external_dispatch_key = str(dispatch.get("external_dispatch_key") or "")
        dispatch_id = str(dispatch.get("dispatch_id") or "")
        if (
            not operation_id
            or type(generation) is not int
            or generation < 0
            or not semantic_effect_key
            or not external_dispatch_key
            or not dispatch_id
        ):
            raise VerticalInvariantError("INVALID_REQUEST", "external-create dispatch identity is incomplete")

        existing = self._existing_attempt(external_dispatch_key)
        if existing is not None:
            if existing.get("semantic_effect_key") != semantic_effect_key:
                raise VerticalInvariantError(
                    "POLICY_DENIED",
                    "external dispatch key is already bound to another semantic attempt",
                )
            receipt = self._exact_lookup(existing["execution_binding"], external_dispatch_key)
            if receipt["lookup_state"] == "LAUNCHED":
                return receipt
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

        current_binding = self._binding_from_delegate(dispatch)
        try:
            preflight = _normalize_receipt(
                self.delegate.lookup(external_dispatch_key=external_dispatch_key)
            )
        except Exception:
            preflight = {"lookup_state": "UNKNOWN", "receipt_id": None}
        if preflight["lookup_state"] in {"LAUNCHED", "UNKNOWN"}:
            return preflight

        claim_id = dispatch_claim_id(operation_id, generation, semantic_effect_key)
        try:
            committed = self.runtime.commit_replanned(
                lambda snapshot: plan_lineage_external_create_attempt(
                    snapshot,
                    operation_id=operation_id,
                    generation=generation,
                    claim_id=claim_id,
                    dispatch_id=dispatch_id,
                    semantic_effect_key=semantic_effect_key,
                    external_dispatch_key_value=external_dispatch_key,
                    execution_binding=current_binding,
                    occurred_at=self.runtime.clock(),
                    trusted_context_digest=self.trusted_context_digest,
                )
            )
        except Exception:
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

        result = dict(committed.result)
        winning_binding = dict(result["execution_binding"])
        if not result.get("acquired"):
            receipt = self._exact_lookup(winning_binding, external_dispatch_key)
            if receipt["lookup_state"] == "LAUNCHED":
                return receipt
            return {"lookup_state": "UNKNOWN", "receipt_id": None}

        if canonical_json(winning_binding) != canonical_json(current_binding):
            raise VerticalInvariantError(
                "POLICY_DENIED",
                "new external-create attempt returned a different execution binding",
            )

        receipt = _normalize_receipt(self.delegate.launch(dispatch=dispatch))
        if receipt["lookup_state"] == "NOT_LAUNCHED":
            return {"lookup_state": "UNKNOWN", "receipt_id": None}
        return receipt
