#!/usr/bin/env python3
"""Shared semantic conformance proof for the independent Responses driver."""
from __future__ import annotations

from operator_conformance import (
    DirectFixtureAdapter,
    FROZEN_CONFORMANCE_SUBSET,
    assert_materially_independent,
    run_conformance_suite,
)
from operator_openai_responses import ADAPTER_ID
from operator_openai_responses_conformance import (
    TRANSPORT_KIND,
    build_lane_a_responses_conformance_adapter,
)


def main() -> None:
    direct = DirectFixtureAdapter()
    responses = build_lane_a_responses_conformance_adapter()

    direct_report = run_conformance_suite(direct)
    responses_report = run_conformance_suite(responses)

    assert responses_report.adapter.adapter_id == ADAPTER_ID
    assert responses_report.adapter.transport_kind == TRANSPORT_KIND
    assert responses_report.adapter.wrapper_depth == 0
    assert not hasattr(responses, "conformance_delegate")
    assert responses_report.exercised_capabilities == FROZEN_CONFORMANCE_SUBSET
    assert responses_report.semantic_signature == direct_report.semantic_signature

    direct_evidence, responses_evidence = assert_materially_independent(direct, responses)
    assert direct_evidence.root_implementation_type != responses_evidence.root_implementation_type
    assert direct_evidence.transport_kind != responses_evidence.transport_kind
    assert direct_evidence.adapter_id != responses_evidence.adapter_id

    snapshot = responses.lane_a_store_backend.read_snapshot()
    binding_paths = sorted(
        path
        for path in snapshot.files
        if "/adapter-calls/openai-responses/" in path
    )
    result_paths = sorted(
        path
        for path in snapshot.files
        if "/adapter-call-results/openai-responses/" in path
    )
    assert binding_paths, "Responses shared conformance did not cross durable call binding journal"
    assert len(binding_paths) == len(result_paths), "Responses conformance journal result/binding mismatch"

    for capability, backend in responses.lane_a_fixture_backends.items():
        assert getattr(backend, "test_only", False) is True, capability

    print("OpenAI Responses independent conformance validation passed")
    print(f"- semantic subset: {len(FROZEN_CONFORMANCE_SUBSET)} capabilities")
    print(f"- transport: {responses_evidence.transport_kind}")
    print("- Lane A: provider-shaped fixture -> production Responses boundary -> deterministic test backends")
    print("- Lane A is explicitly insufficient for Supported production status")


if __name__ == "__main__":
    main()
