#!/usr/bin/env python3
"""Verification-only real-runtime fault injection adapters for Issue #221.

These wrappers never create new authority. They delegate to already-trusted
production gateways and only interrupt local control flow at approved fault
windows. The launch adapter models a process crash after a trusted external
launch has returned LAUNCHED but before local receipt evidence is persisted.
The Persist adapter models a crash after one exact Feature Event write returns
an authoritative receipt but before local Persist confirmation/reconciliation.
"""
from __future__ import annotations

from typing import Any


class InjectedRunnerCrash(BaseException):
    """Process-level launch fault signal intentionally outside `Exception` handlers."""

    code = "FI_CRASH_AFTER_LAUNCH_BEFORE_LOCAL_ACK"

    def __init__(self, external_dispatch_key: str):
        self.external_dispatch_key = external_dispatch_key
        super().__init__(self.code)


class InjectedPersistRunnerCrash(BaseException):
    """Process-level Persist fault signal intentionally outside `Exception` handlers."""

    code = "FI_CRASH_AFTER_PERSIST_BEFORE_LOCAL_ACK"

    def __init__(self, feature_event_id: str, target_ref: str):
        self.feature_event_id = feature_event_id
        self.target_ref = target_ref
        super().__init__(self.code)


class LostAckCrashAfterLaunchDispatchGateway:
    """Crash once after exact LAUNCHED receipt, discarding local acknowledgement."""

    verification_only = True

    def __init__(self, *, delegate: Any, expected_external_dispatch_key: str):
        if delegate is None or not callable(getattr(delegate, "launch", None)) or not callable(getattr(delegate, "lookup", None)):
            raise ValueError("trusted dispatch delegate with launch/lookup is required")
        if not expected_external_dispatch_key:
            raise ValueError("exact external dispatch key is required for fault injection")
        self.delegate = delegate
        self.expected_external_dispatch_key = str(expected_external_dispatch_key)
        self.injected = False

    def launch(self, *, dispatch: dict[str, Any]):
        if self.injected:
            raise RuntimeError("lost-ACK fault gateway cannot launch again after injected crash")
        if not isinstance(dispatch, dict):
            raise ValueError("fault-injected dispatch must be an object")
        external_key = str(dispatch.get("external_dispatch_key") or "")
        if external_key != self.expected_external_dispatch_key:
            raise ValueError("fault injection dispatch key does not match trusted expected key")

        receipt = self.delegate.launch(dispatch=dispatch)
        if not isinstance(receipt, dict):
            return receipt
        lookup_state = str(receipt.get("lookup_state") or "UNKNOWN")
        receipt_id = receipt.get("receipt_id")
        if lookup_state != "LAUNCHED" or not receipt_id:
            return receipt

        self.injected = True
        raise InjectedRunnerCrash(external_key)

    def lookup(self, *, external_dispatch_key: str):
        if str(external_dispatch_key) != self.expected_external_dispatch_key:
            raise ValueError("fault injection lookup key does not match trusted expected key")
        return self.delegate.lookup(external_dispatch_key=external_dispatch_key)


class LostAckCrashAfterPersistGateway:
    """Crash once after one exact authoritative Feature Persist receipt.

    The expected Event id and target ref are fixed by the trusted harness before
    delegation. The successful receipt is deliberately discarded with the
    process. A fresh process may only use exact Event lookup for recovery; this
    wrapper refuses any second write after injecting the crash.
    """

    verification_only = True

    def __init__(self, *, delegate: Any, expected_feature_event_id: str, expected_target_ref: str):
        if delegate is None or not callable(getattr(delegate, "persist_feature_event", None)) or not callable(getattr(delegate, "lookup_feature_event", None)):
            raise ValueError("trusted Persist delegate with write/lookup is required")
        if not expected_feature_event_id or not expected_target_ref:
            raise ValueError("exact Feature Event id and target ref are required for Persist fault injection")
        self.delegate = delegate
        self.expected_feature_event_id = str(expected_feature_event_id)
        self.expected_target_ref = str(expected_target_ref)
        self.injected = False

    def persist_feature_event(self, *, event: dict[str, Any], target_ref: str):
        if self.injected:
            raise RuntimeError("lost-ACK Persist fault gateway cannot write again after injected crash")
        if not isinstance(event, dict):
            raise ValueError("fault-injected Feature Event must be an object")
        event_id = str(event.get("id") or "")
        if event_id != self.expected_feature_event_id or str(target_ref) != self.expected_target_ref:
            raise ValueError("Persist fault injection binding differs from trusted expected Event/ref")

        receipt = self.delegate.persist_feature_event(event=event, target_ref=target_ref)
        if not isinstance(receipt, dict):
            return receipt
        if receipt.get("event_id") != event_id or not isinstance(receipt.get("result_revision"), int):
            return receipt

        self.injected = True
        raise InjectedPersistRunnerCrash(event_id, str(target_ref))

    def lookup_feature_event(self, *, event_id: str, target_ref: str):
        if str(event_id) != self.expected_feature_event_id or str(target_ref) != self.expected_target_ref:
            raise ValueError("Persist fault lookup binding differs from trusted expected Event/ref")
        return self.delegate.lookup_feature_event(event_id=event_id, target_ref=target_ref)
