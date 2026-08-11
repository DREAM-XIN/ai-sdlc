#!/usr/bin/env python3
"""Validate the one production Decision Event factory is release/receipt-safe."""
from __future__ import annotations

from operator_decision_event_runtime import build_production_decision_event_gateway
from operator_github_feature_event_gateway import APPLIED
from operator_release_feature_event_gateway import ReceiptSafeCanonicalFeatureEventGateway
from validate_operator_github_feature_event_gateway import EVENT_ID, FEATURE, REF, REPO, REV
from validate_operator_release_feature_event_gateway import HistoryFakeGitHub, canonical_event


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    fake = HistoryFakeGitHub()
    gateway = build_production_decision_event_gateway(
        token="trusted-event-writer",
        repository=REPO,
        default_branch="main",
        feature_refs={FEATURE: REF},
        api_base="https://api.github.test",
        http_request=fake,
        sleeper=lambda _: None,
        poll_attempts=1,
        poll_seconds=0,
    )
    require(isinstance(gateway.transport, ReceiptSafeCanonicalFeatureEventGateway), type(gateway.transport))
    require(gateway.scope.default_branch == "main", gateway.scope)
    require(gateway.configuration.target_ref(FEATURE) == REF, gateway.configuration)
    feature = gateway.read_feature(feature_id=FEATURE)
    require(feature["revision"] == REV, feature)

    # The production compatibility factory itself must survive inbox cleanup;
    # callers cannot accidentally get a weaker canonical-only transport by
    # importing this older production factory name.
    historical_text, historical_digest = canonical_event()
    fake.history_texts = [historical_text]
    fake.event_text = None
    fake.manifest["applied_events"] = [EVENT_ID, "EVT-LATER"]
    fake.manifest["revision"] = REV + 2
    receipt = gateway.lookup_receipt(
        feature_id=FEATURE,
        event_id=EVENT_ID,
        expected_revision=REV,
        expected_event_digest=historical_digest,
    )
    require(receipt.state == APPLIED, receipt)
    require(receipt.result_revision == REV + 1, receipt)
    require(fake.history_lookup_count == 1, "production factory bypassed exact historical receipt proof")

    for feature_refs in ({}, {FEATURE: "main"}):
        try:
            build_production_decision_event_gateway(
                token="trusted-event-writer",
                repository=REPO,
                default_branch="main",
                feature_refs=feature_refs,
                api_base="https://api.github.test",
                http_request=fake,
                sleeper=lambda _: None,
                poll_attempts=1,
                poll_seconds=0,
            )
            raise AssertionError(f"unsafe Decision Event runtime config unexpectedly accepted: {feature_refs}")
        except ValueError:
            pass

    print("Production Decision Event runtime factory validation passed")
    print("- production compatibility factory delegates to release-safe exact Event transport")
    print("- inbox-cleanup APPLIED recovery proves historical exact Event digest")
    print("- late Feature advance still returns exact expected_revision + 1 receipt")
    print("- schema/revision/default-branch/server-scope layers remain fixed by factory")
    print("- empty Feature scope and default-branch target are rejected")


if __name__ == "__main__":
    main()
