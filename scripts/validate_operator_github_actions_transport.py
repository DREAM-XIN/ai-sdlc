#!/usr/bin/env python3
"""Deterministic contract validation for the real GitHub Actions transport."""
from __future__ import annotations

import json
from urllib.parse import urlparse, parse_qs

from operator_github_actions_transport import (
    GitHubActionsTransportConfig,
    GitHubActionsWorkflowTransport,
)

REPO = "DREAM-XIN/ai-sdlc"
WORKFLOW = "ai-sdlc-gh-aw-reviewer-claude.lock.yml"
REF = "main"
KEY = "dispatch-fi-transport-0001"


class FakeHttp:
    def __init__(self):
        self.runs = []
        self.dispatches = []
        self.fail_lookup = False
        self.fail_dispatch = False
        self.delay_visibility = 0
        self.lookup_count = 0

    def __call__(self, *, method, url, body=None):
        parsed = urlparse(url)
        if method == "POST" and parsed.path.endswith("/dispatches"):
            self.dispatches.append((url, body))
            if self.fail_dispatch:
                return 500, {}, b"{}"
            inputs = dict((body or {}).get("inputs") or {})
            self.runs.append({
                "id": len(self.runs) + 100,
                "display_title": f"AI-SDLC gh-aw {inputs['dispatch_key']}",
            })
            return 204, {}, b""
        if method == "GET" and "/runs" in parsed.path:
            self.lookup_count += 1
            if self.fail_lookup:
                return 503, {}, b"{}"
            query = parse_qs(parsed.query)
            assert query["event"] == ["workflow_dispatch"]
            assert query["branch"] == [REF]
            visible = [] if self.lookup_count <= self.delay_visibility else list(self.runs)
            return 200, {}, json.dumps({"workflow_runs": visible}).encode()
        raise AssertionError((method, url, body))


def transport(http):
    return GitHubActionsWorkflowTransport(
        GitHubActionsTransportConfig(
            repository=REPO,
            token="test-token",
            receipt_poll_attempts=3,
            receipt_poll_seconds=0,
        ),
        http=http,
        sleeper=lambda _: None,
    )


def inputs():
    return {"dispatch_key": KEY, "feature_id": "F-TEST"}


def main():
    http = FakeHttp()
    t = transport(http)
    first = t.dispatch(workflow=WORKFLOW, ref=REF, inputs=inputs())
    assert first == {"lookup_state": "LAUNCHED", "receipt_id": "100"}
    assert len(http.dispatches) == 1
    second = t.dispatch(workflow=WORKFLOW, ref=REF, inputs=inputs())
    assert second == first and len(http.dispatches) == 1
    fresh = transport(http)
    assert fresh.lookup(workflow=WORKFLOW, ref=REF, dispatch_key=KEY) == first

    delayed = FakeHttp(); delayed.delay_visibility = 99
    delayed_result = transport(delayed).dispatch(workflow=WORKFLOW, ref=REF, inputs=inputs())
    assert delayed_result == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert len(delayed.dispatches) == 1

    failed = FakeHttp(); failed.fail_dispatch = True
    assert transport(failed).dispatch(workflow=WORKFLOW, ref=REF, inputs=inputs()) == {
        "lookup_state": "UNKNOWN", "receipt_id": None
    }

    unavailable = FakeHttp(); unavailable.fail_lookup = True
    assert transport(unavailable).lookup(workflow=WORKFLOW, ref=REF, dispatch_key=KEY) == {
        "lookup_state": "UNKNOWN", "receipt_id": None
    }

    duplicate = FakeHttp()
    duplicate.runs = [
        {"id": 10, "display_title": f"AI-SDLC gh-aw {KEY}"},
        {"id": 11, "display_title": f"AI-SDLC gh-aw {KEY}"},
    ]
    assert transport(duplicate).lookup(workflow=WORKFLOW, ref=REF, dispatch_key=KEY) == {
        "lookup_state": "UNKNOWN", "receipt_id": None
    }

    absent = FakeHttp()
    assert transport(absent).lookup(workflow=WORKFLOW, ref=REF, dispatch_key=KEY) == {
        "lookup_state": "NOT_LAUNCHED", "receipt_id": None
    }

    print("GitHub Actions workflow transport validation passed")
    print("- stable-key preflight convergence")
    print("- fresh-process exact run-name lookup")
    print("- accepted-but-unobserved => UNKNOWN")
    print("- API error / duplicate receipt => UNKNOWN")


if __name__ == "__main__":
    main()
