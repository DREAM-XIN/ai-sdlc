#!/usr/bin/env python3
"""Adversarial validation for first-attempt gh-aw production run leases."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from operator_vertical import VerticalInvariantError
from operator_vertical_gh_aw_attempt_binding import (
    FirstAttemptDigestBoundGhAwResultSource,
    build_first_attempt_production_collector,
)
from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig
from validate_operator_vertical_gh_aw_github_source import (
    CONTROL,
    KEY,
    RUN_ID,
    TARGET,
    FakeHttp,
    trusted_context,
    workflows,
)


class AttemptAwareFakeHttp(FakeHttp):
    def __init__(
        self,
        *,
        role="reviewer",
        run_attempt=1,
        mutate_run_from_call=None,
        mutated_run_attempt=2,
        **kwargs,
    ):
        super().__init__(role=role, **kwargs)
        self.initial_run_attempt = run_attempt
        self.mutate_run_from_call = mutate_run_from_call
        self.mutated_run_attempt = mutated_run_attempt
        self.run_reads = 0

    def _run(self):
        row = super()._run()
        attempt = self.initial_run_attempt
        if (
            self.mutate_run_from_call is not None
            and self.run_reads >= self.mutate_run_from_call
        ):
            attempt = self.mutated_run_attempt
        if attempt is not None:
            row["run_attempt"] = attempt
        return row

    def __call__(self, *, method, url, token):
        path = urlparse(url).path
        if path.endswith(f"/actions/runs/{RUN_ID}"):
            self.run_reads += 1
        return super().__call__(method=method, url=url, token=token)


def config():
    return GitHubActionsGhAwResultSourceConfig(
        control_repository=CONTROL,
        control_token="control-token",
        target_token="target-token",
        workflows=workflows(),
        collector_identity="collector:github-actions/v1",
    )


def source(fake):
    return FirstAttemptDigestBoundGhAwResultSource(
        config(), target_repository=TARGET, http=fake
    )


def expect_vertical_error(callback, message):
    try:
        callback()
    except VerticalInvariantError:
        return
    raise AssertionError(message)


def resolve(src, role):
    return src.resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context(role),
    )


def validate_first_attempt_happy_path():
    for role in ("developer", "reviewer", "qa"):
        fake = AttemptAwareFakeHttp(role=role)
        src = source(fake)
        resolved = resolve(src, role)
        assert resolved.run.run_id == RUN_ID
        assert len(resolved.outputs) == 1
        data = src.load_content(resolved.outputs[0].trusted_uri)
        assert isinstance(data, bytes) and data
        # Bracketing causes multiple independent run observations and every one
        # remains first-attempt authority.
        assert fake.run_reads >= 6


def validate_initial_rerun_rejection():
    expect_vertical_error(
        lambda: resolve(source(AttemptAwareFakeHttp(role="reviewer", run_attempt=2)), "reviewer"),
        "run_attempt=2 was accepted under a run-id-only durable receipt",
    )
    expect_vertical_error(
        lambda: resolve(source(AttemptAwareFakeHttp(role="reviewer", run_attempt=None)), "reviewer"),
        "missing run_attempt was accepted",
    )


def validate_run_jobs_logs_bracket():
    # Call 1 is the hardened pre-snapshot. Call 2 is the base source's own
    # run read before it resolves jobs/logs. A real rerun remains attempt 2
    # afterwards, so the hardened post-snapshot must fail closed.
    fake = AttemptAwareFakeHttp(
        role="reviewer",
        mutate_run_from_call=2,
        mutated_run_attempt=2,
    )
    expect_vertical_error(
        lambda: resolve(source(fake), "reviewer"),
        "rerun beginning during run/jobs/log resolution was accepted",
    )


def validate_after_resolve_before_receipt_rerun():
    fake = AttemptAwareFakeHttp(role="reviewer")
    src = source(fake)
    resolved = resolve(src, "reviewer")
    # All resolve-time snapshots were attempt 1. Simulate a manual rerun before
    # _build_receipts() calls the bound content loader.
    fake.mutate_run_from_call = fake.run_reads + 1
    expect_vertical_error(
        lambda: src.load_content(resolved.outputs[0].trusted_uri),
        "rerun after trusted resolve but before receipt loading was accepted",
    )


def validate_during_content_load_rerun():
    fake = AttemptAwareFakeHttp(role="developer")
    src = source(fake)
    resolved = resolve(src, "developer")
    # load_content performs a run snapshot before and after the digest-bound
    # PR/comment read. Make the second snapshot observe attempt 2.
    fake.mutate_run_from_call = fake.run_reads + 2
    expect_vertical_error(
        lambda: src.load_content(resolved.outputs[0].trusted_uri),
        "rerun during collected-output receipt loading was accepted",
    )


def validate_supported_builder():
    fake = AttemptAwareFakeHttp(role="reviewer")
    collector = build_first_attempt_production_collector(
        executor=object(),
        source_config=config(),
        target_repository=TARGET,
        workflows=workflows(),
        control_repository=CONTROL,
        clock=lambda: "2026-08-14T00:00:00Z",
        trusted_role_policy="vertical-independent-role-policy/v1",
        collector_namespace_policy="gh-aw-first-attempt-digest-bound/v1",
        http=fake,
    )
    assert isinstance(
        collector.result_source,
        FirstAttemptDigestBoundGhAwResultSource,
    )
    assert collector.callback_coordinator.content_loader.__self__ is collector.result_source


def main():
    validate_first_attempt_happy_path()
    validate_initial_rerun_rejection()
    validate_run_jobs_logs_bracket()
    validate_after_resolve_before_receipt_rerun()
    validate_during_content_load_rerun()
    validate_supported_builder()
    print("first-attempt production gh-aw run lease validation passed")
    print("- run-id-only durable receipt authorizes run_attempt 1 only")
    print("- run/jobs/log resolution is bracketed by stable first-attempt snapshots")
    print("- rerun after resolve but before receipt adoption fails closed")
    print("- rerun during digest-bound output loading fails closed")
    print("- final supported production builder wires attempt-bound source + content loader")


if __name__ == "__main__":
    main()
