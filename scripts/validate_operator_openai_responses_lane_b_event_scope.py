#!/usr/bin/env python3
"""Adversarial scope checks for the Lane-B exact Feature Event outer seam."""
from __future__ import annotations

import inspect

from operator_configured_feature_event_gateway import TrustedFeatureEventTarget
from operator_github_feature_event_gateway import FeatureEventGatewayError
from operator_production_feature_event_gateway import TrustedFeatureEventWriteScope
from validate_operator_openai_responses_lane_b import (
    FEATURE,
    FEATURE_REF,
    TARGET,
    GitHubFeatureEventHTTP,
    MutableFeatureTruth,
    _dynamic_feature_event_gateway,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    truth = MutableFeatureTruth(stage="code-review")
    http = GitHubFeatureEventHTTP(truth)

    # The deterministic outer HTTP seam itself is closed to the exact trusted
    # repository/ref/path shape used by the real exact-revision gateway.
    status, _ = http(
        "GET",
        f"https://api.github.com/repos/foreign/repo/contents/state/features/{FEATURE}.yaml?ref={FEATURE_REF}",
        {},
    )
    require(status == 404, "foreign repository reached the Lane-B HTTP seam")

    status, _ = http(
        "GET",
        f"https://api.github.com/repos/{TARGET}/contents/state/features/{FEATURE}.yaml?ref=main",
        {},
    )
    require(status == 404, "foreign ref reached the Lane-B HTTP seam")

    status, _ = http(
        "GET",
        f"https://api.github.com/repos/{TARGET}/contents/private/secret.txt?ref={FEATURE_REF}",
        {},
    )
    require(status == 404, "unrelated repository path was exposed by the Lane-B HTTP seam")

    status, _ = http(
        "DELETE",
        f"https://api.github.com/repos/{TARGET}/contents/events/inbox/EVT-X.yaml?ref={FEATURE_REF}",
        {},
    )
    require(status == 405, "unsupported HTTP mutation method was accepted")
    require(not http.put_calls, "scope adversaries caused a Feature Event write")

    # The reviewed configured gateway is stronger than a runtime rejection of
    # caller-supplied repository/ref: those authority parameters do not exist on
    # its public write/lookup call shape at all.
    gateway, scoped_http = _dynamic_feature_event_gateway(truth)
    persist_params = set(inspect.signature(gateway.persist_exact_event).parameters)
    lookup_params = set(inspect.signature(gateway.lookup_receipt).parameters)
    require(
        persist_params == {"feature_id", "expected_revision", "event"},
        f"configured Persist call shape expanded: {sorted(persist_params)}",
    )
    require(
        lookup_params
        == {"feature_id", "event_id", "expected_revision", "expected_event_digest"},
        f"configured lookup call shape expanded: {sorted(lookup_params)}",
    )
    require(
        "repository" not in persist_params
        and "target_ref" not in persist_params
        and "repository" not in lookup_params
        and "target_ref" not in lookup_params,
        "caller-selectable repository/ref authority leaked into configured Event gateway",
    )

    baseline_gets = len(scoped_http.get_calls)
    event = {
        "version": "0.1.0",
        "id": "EVT-RESPONSES-LANE-B-SCOPE",
        "feature_id": "F-FOREIGN",
        "expected_revision": 7,
        "occurred_at": "2026-08-11T11:55:00Z",
        "changes": [{"kind": "stage", "id": "code-review", "status": "WORKING"}],
    }
    try:
        gateway.persist_exact_event(
            feature_id="F-FOREIGN",
            expected_revision=7,
            event=event,
        )
    except FeatureEventGatewayError as exc:
        require(exc.code == "UNAUTHORIZED", f"foreign Feature failed with wrong code: {exc.code}")
    else:
        raise AssertionError("configured Event gateway accepted foreign Feature")
    require(
        len(scoped_http.get_calls) == baseline_gets and not scoped_http.put_calls,
        "configured foreign-Feature rejection reached the external Event transport",
    )

    # Production scope also rejects a Feature target that aliases the default
    # branch before a gateway can be constructed.
    try:
        TrustedFeatureEventWriteScope(
            repository=TARGET,
            default_branch="main",
            targets=(TrustedFeatureEventTarget(FEATURE, "main"),),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("default-branch Feature Event target was accepted")

    print("OpenAI Responses Lane-B exact Event scope validation passed")
    print("- foreign repository/ref/path/method fail closed at the outer HTTP seam")
    print("- configured write/lookup APIs expose no caller-selectable repository/ref")
    print("- foreign Feature fails UNAUTHORIZED before external transport")
    print("- default-branch Event target is rejected at configuration time")
    print("- scope adversaries perform zero Feature Event PUTs")


if __name__ == "__main__":
    main()
