#!/usr/bin/env python3
"""Adversarial validation for digest-bound production gh-aw content collection."""
from __future__ import annotations

from copy import deepcopy
import json
from urllib.parse import urlparse

from operator_vertical import VerticalInvariantError
from operator_vertical_gh_aw_content_binding import (
    DigestBoundTargetScopedGitHubActionsGhAwResultSource,
    build_digest_bound_production_collector,
)
from operator_vertical_gh_aw_github_source import GitHubActionsGhAwResultSourceConfig
from validate_operator_vertical_gh_aw_github_source import (
    CONTROL,
    HEAD,
    KEY,
    RUN_ID,
    TARGET,
    FakeHttp,
    reviewer_result,
    trusted_context,
    workflows,
)


def config():
    return GitHubActionsGhAwResultSourceConfig(
        control_repository=CONTROL,
        control_token="control-token",
        target_token="target-token",
        workflows=workflows(),
        collector_identity="collector:github-actions/v1",
    )


def source(fake):
    return DigestBoundTargetScopedGitHubActionsGhAwResultSource(
        config(), target_repository=TARGET, http=fake
    )


def resolve(fake):
    return source(fake).resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context(fake.role),
    )


def expect_vertical_error(callback, message):
    try:
        callback()
    except VerticalInvariantError:
        return
    raise AssertionError(message)


class MutatingFakeHttp(FakeHttp):
    def __init__(
        self,
        *,
        role,
        mutate_pr_call=None,
        mutate_comment_call=None,
        pr_mutation=None,
        comment_mutation=None,
    ):
        super().__init__(role=role)
        self.mutate_pr_call = mutate_pr_call
        self.mutate_comment_call = mutate_comment_call
        self.pr_mutation = dict(pr_mutation or {})
        self.comment_mutation = dict(comment_mutation or {})
        self.pr_reads = 0
        self.comment_reads = 0

    @staticmethod
    def _merge(row, mutation):
        row = deepcopy(row)
        for key, value in mutation.items():
            if key == "base_ref":
                row.setdefault("base", {})["ref"] = value
            elif key == "head_ref":
                row.setdefault("head", {})["ref"] = value
            elif key == "head_sha":
                row.setdefault("head", {})["sha"] = value
            elif key == "user_type":
                row.setdefault("user", {})["type"] = value
            elif key == "payload":
                row["body"] = (
                    "<!-- AI-SDLC-GATE-RESULT\n"
                    + json.dumps(value, separators=(",", ":"))
                    + "\nAI-SDLC-GATE-RESULT -->\nsummary"
                )
            else:
                row[key] = value
        return row

    def __call__(self, *, method, url, token):
        path = urlparse(url).path
        if path.endswith("/pulls/41"):
            self.pr_reads += 1
            row = self._pr()
            if self.pr_reads == self.mutate_pr_call:
                row = self._merge(row, self.pr_mutation)
            return 200, {}, json.dumps(row).encode()
        if path.endswith("/issues/comments/91"):
            self.comment_reads += 1
            row = self._comment()
            if self.comment_reads == self.mutate_comment_call:
                row = self._merge(row, self.comment_mutation)
            return 200, {}, json.dumps(row).encode()
        return super().__call__(method=method, url=url, token=token)


def validate_happy_path():
    reviewer = FakeHttp(role="reviewer")
    result = resolve(reviewer)
    uri = result.outputs[0].trusted_uri
    assert "-binding-" in uri and uri.endswith(".json")
    content = json.loads(source(reviewer).load_content(uri))
    assert content["contract"] == "ai-sdlc-gh-aw-reviewer-result-v0.1"

    developer = FakeHttp(role="developer")
    result = resolve(developer)
    uri = result.outputs[0].trusted_uri
    content = json.loads(source(developer).load_content(uri))
    assert content["pr_number"] == 41
    assert content["head_sha"] == HEAD
    assert content["base_ref"] == trusted_context("developer")["target_ref"]


def validate_resolve_to_binding_race():
    developer = MutatingFakeHttp(
        role="developer",
        mutate_pr_call=2,
        pr_mutation={"draft": False},
    )
    expect_vertical_error(
        lambda: resolve(developer),
        "Developer Draft->ready race between base resolve and binding was accepted",
    )

    reviewer = MutatingFakeHttp(
        role="reviewer",
        mutate_comment_call=2,
        comment_mutation={
            "payload": {
                **reviewer_result(),
                "expected_revision": 8,
            }
        },
    )
    expect_vertical_error(
        lambda: resolve(reviewer),
        "Gate comment edit between base resolve and binding was accepted",
    )


def validate_binding_to_receipt_race():
    developer = MutatingFakeHttp(
        role="developer",
        mutate_pr_call=3,
        pr_mutation={"base_ref": "feature/retargeted"},
    )
    src = source(developer)
    result = src.resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context("developer"),
    )
    expect_vertical_error(
        lambda: src.load_content(result.outputs[0].trusted_uri),
        "Developer PR retarget after binding was accepted",
    )

    reviewer = MutatingFakeHttp(
        role="reviewer",
        mutate_comment_call=3,
        comment_mutation={
            "payload": {
                **reviewer_result(),
                "verdict": "REWORK",
                "findings": [
                    {
                        "severity": "MAJOR",
                        "code": "RACE",
                        "message": "edited after resolution",
                    }
                ],
            }
        },
    )
    src = source(reviewer)
    result = src.resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context("reviewer"),
    )
    expect_vertical_error(
        lambda: src.load_content(result.outputs[0].trusted_uri),
        "Gate Safe Output edit after binding was accepted",
    )


def validate_provenance_only_drift():
    reviewer = MutatingFakeHttp(
        role="reviewer",
        mutate_comment_call=3,
        comment_mutation={"user_type": "User"},
    )
    src = source(reviewer)
    result = src.resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context("reviewer"),
    )
    expect_vertical_error(
        lambda: src.load_content(result.outputs[0].trusted_uri),
        "Gate provenance-only drift after binding was accepted",
    )

    developer = MutatingFakeHttp(
        role="developer",
        mutate_pr_call=3,
        pr_mutation={"head_ref": "feature/manual-rewrite"},
    )
    src = source(developer)
    result = src.resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context("developer"),
    )
    expect_vertical_error(
        lambda: src.load_content(result.outputs[0].trusted_uri),
        "Developer head-ref provenance-only drift after binding was accepted",
    )


def validate_supported_production_builder():
    collector = build_digest_bound_production_collector(
        executor=object(),
        source_config=config(),
        target_repository=TARGET,
        workflows=workflows(),
        control_repository=CONTROL,
        clock=lambda: "2026-08-14T00:00:00Z",
        trusted_role_policy="vertical-independent-role-policy/v1",
        collector_namespace_policy="gh-aw-digest-bound/v1",
        http=FakeHttp(role="reviewer"),
    )
    assert isinstance(
        collector.result_source,
        DigestBoundTargetScopedGitHubActionsGhAwResultSource,
    )
    assert collector.callback_coordinator.content_loader.__self__ is collector.result_source
    assert collector.callback_coordinator.content_loader.__func__ is collector.result_source.load_content.__func__


def main():
    validate_happy_path()
    validate_resolve_to_binding_race()
    validate_binding_to_receipt_race()
    validate_provenance_only_drift()
    validate_supported_production_builder()
    print("digest-bound production gh-aw content validation passed")
    print("- resolve->binding PR/comment races fail closed")
    print("- binding->receipt content/provenance races fail closed")
    print("- Developer state/draft/base/head-ref/head-SHA are observation-bound")
    print("- Gate comment provenance + machine payload are observation-bound")
    print("- supported production builder binds callback content loader to hardened source")


if __name__ == "__main__":
    main()
