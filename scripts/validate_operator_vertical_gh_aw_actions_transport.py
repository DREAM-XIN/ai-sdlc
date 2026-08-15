#!/usr/bin/env python3
"""Adversarial validation for the pure GitHub Actions Vertical gh-aw transport."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_gh_aw import GhAwVerticalRoleDispatchGateway, GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_actions_transport import (
    GitHubActionsVerticalGhAwTransport,
    GitHubActionsWorkflowTransportConfig,
)

CONTROL = "dream-xin/ai-sdlc"
KEY = "dispatch-" + "a" * 40
WORKFLOWS = GhAwVerticalWorkflowMap(
    default_branch="main",
    developer_workflow="ai-sdlc-gh-aw-developer-codex.lock.yml",
    reviewer_workflow="ai-sdlc-gh-aw-reviewer-codex.lock.yml",
    qa_workflow="ai-sdlc-gh-aw-qa-codex.lock.yml",
)


def vertical_dispatch(role="developer"):
    return {
        "operation_id": "op-example",
        "operation_generation": 1,
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": "e" * 64,
        "external_dispatch_key": KEY,
        "dispatch_id": "vertical-example",
        "target_repository": CONTROL,
        "target_ref": "feature/F-REAL-0001",
        "feature_id": "F-REAL-0001",
        "expected_revision": 7,
        "feature_stage": "implementation" if role == "developer" else "code-review" if role == "reviewer" else "verification",
        "task_id": f"task-{role}",
        "task_identity": f"{role}-stage",
        "role": role,
        "candidate_pr_number": None if role == "developer" else 41,
        "candidate_head_sha": None if role == "developer" else "b" * 40,
    }


def request_for(role="developer"):
    gateway = GhAwVerticalRoleDispatchGateway(transport=object(), workflows=WORKFLOWS)
    dispatch = vertical_dispatch(role)
    return WORKFLOWS.workflow_for(role), WORKFLOWS.default_branch, gateway._inputs(dispatch)


class FakeGitHubActionsHttp:
    def __init__(
        self,
        *,
        runs=None,
        post_status=204,
        create_run_on_post=False,
        post_raises_after_accept=False,
        visibility_after_gets=0,
        get_status=200,
        total_count_sequence=None,
        explicit_pages=None,
    ):
        self.runs = list(runs or [])
        self.post_status = post_status
        self.create_run_on_post = create_run_on_post
        self.post_raises_after_accept = post_raises_after_accept
        self.visibility_after_gets = visibility_after_gets
        self.get_status = get_status
        self.total_count_sequence = list(total_count_sequence or [])
        self.explicit_pages = dict(explicit_pages or {})
        self.post_calls = 0
        self.get_calls = 0
        self.last_post = None
        self.next_run_id = 9001

    @staticmethod
    def run(*, run_id, workflow, key=KEY, branch="main"):
        return {
            "id": run_id,
            "display_title": f"AI-SDLC gh-aw {key}",
            "event": "workflow_dispatch",
            "head_branch": branch,
            "path": f".github/workflows/{workflow}",
            "run_attempt": 1,
        }

    def __call__(self, *, method, url, token, body=None):
        parsed = urlparse(url)
        path = parsed.path
        if method == "POST" and path.endswith("/dispatches"):
            self.post_calls += 1
            document = json.loads((body or b"{}").decode())
            workflow = path.split("/actions/workflows/", 1)[1].rsplit("/dispatches", 1)[0]
            self.last_post = {"workflow": workflow, "document": document, "token": token}
            if self.create_run_on_post:
                key = document["inputs"]["dispatch_key"]
                self.runs.insert(
                    0,
                    self.run(
                        run_id=self.next_run_id,
                        workflow=workflow,
                        key=key,
                        branch=document["ref"],
                    ),
                )
                self.next_run_id += 1
            if self.post_raises_after_accept:
                raise RuntimeError("simulated lost HTTP acknowledgement")
            return self.post_status, {}, b""

        if method == "GET" and "/actions/workflows/" in path and path.endswith("/runs"):
            self.get_calls += 1
            if self.get_status != 200:
                return self.get_status, {}, b"{}"
            workflow = path.split("/actions/workflows/", 1)[1].rsplit("/runs", 1)[0]
            query = parse_qs(parsed.query)
            page = int(query.get("page", ["1"])[0])
            per_page = int(query.get("per_page", ["100"])[0])
            visible = self.runs if self.get_calls > self.visibility_after_gets else []
            filtered = [
                row
                for row in visible
                if row.get("path") == f".github/workflows/{workflow}"
            ]
            if page in self.explicit_pages:
                rows = list(self.explicit_pages[page])
            else:
                start = (page - 1) * per_page
                rows = filtered[start : start + per_page]
            if self.total_count_sequence:
                index = min(self.get_calls - 1, len(self.total_count_sequence) - 1)
                total_count = self.total_count_sequence[index]
            else:
                total_count = len(filtered)
            return 200, {}, json.dumps(
                {"total_count": total_count, "workflow_runs": rows}
            ).encode()

        raise AssertionError(f"unexpected fake GitHub call: {method} {url}")


def transport(fake, *, page_size=100, max_pages=20, polls=3):
    return GitHubActionsVerticalGhAwTransport(
        GitHubActionsWorkflowTransportConfig(
            control_repository=CONTROL,
            token="actions-token",
            workflows=WORKFLOWS,
            page_size=page_size,
            max_lookup_pages=max_pages,
            launch_poll_attempts=polls,
            launch_poll_seconds=0,
        ),
        http=fake,
        sleeper=lambda _seconds: None,
    )


def expect_vertical_error(callback, message):
    try:
        callback()
    except VerticalInvariantError:
        return
    raise AssertionError(message)


def validate_accepted_dispatch_and_lease():
    fake = FakeGitHubActionsHttp(create_run_on_post=True)
    runtime = transport(fake)
    workflow, ref, inputs = request_for("developer")
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "LAUNCHED", "receipt_id": "9001"}
    assert fake.post_calls == 1
    assert fake.last_post["document"] == {"ref": ref, "inputs": inputs}


def validate_delayed_visibility_is_unknown():
    fake = FakeGitHubActionsHttp(
        create_run_on_post=True,
        visibility_after_gets=10,
    )
    runtime = transport(fake, polls=3)
    workflow, ref, inputs = request_for("developer")
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert fake.post_calls == 1
    fake.visibility_after_gets = 0
    recovered = runtime.lookup(workflow=workflow, ref=ref, dispatch_key=KEY)
    assert recovered == {"lookup_state": "LAUNCHED", "receipt_id": "9001"}
    assert fake.post_calls == 1


def validate_lost_ack_visibility_lag_is_unknown():
    fake = FakeGitHubActionsHttp(
        create_run_on_post=True,
        post_raises_after_accept=True,
        visibility_after_gets=100,
    )
    runtime = transport(fake, polls=3)
    workflow, ref, inputs = request_for("developer")
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert fake.post_calls == 1
    assert fake.get_calls >= 3
    fake.visibility_after_gets = 0
    recovered = runtime.lookup(workflow=workflow, ref=ref, dispatch_key=KEY)
    assert recovered == {"lookup_state": "LAUNCHED", "receipt_id": "9001"}
    assert fake.post_calls == 1


def validate_lost_ack_visible_run_is_adopted():
    fake = FakeGitHubActionsHttp(
        create_run_on_post=True,
        post_raises_after_accept=True,
    )
    runtime = transport(fake)
    workflow, ref, inputs = request_for("developer")
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "LAUNCHED", "receipt_id": "9001"}
    assert fake.post_calls == 1


def validate_exhaustive_not_launched():
    workflow, ref, _inputs = request_for("developer")
    unrelated = [
        FakeGitHubActionsHttp.run(
            run_id=100 + index,
            workflow=workflow,
            key="dispatch-" + f"{index + 1:040x}",
        )
        for index in range(5)
    ]
    fake = FakeGitHubActionsHttp(runs=unrelated)
    receipt = transport(fake, page_size=2, max_pages=4).lookup(
        workflow=workflow, ref=ref, dispatch_key=KEY
    )
    assert receipt == {"lookup_state": "NOT_LAUNCHED", "receipt_id": None}
    assert fake.get_calls == 3


def validate_truncated_history_is_unknown():
    workflow, ref, _inputs = request_for("developer")
    unrelated = [
        FakeGitHubActionsHttp.run(
            run_id=200 + index,
            workflow=workflow,
            key="dispatch-" + f"{index + 11:040x}",
        )
        for index in range(5)
    ]
    fake = FakeGitHubActionsHttp(runs=unrelated)
    receipt = transport(fake, page_size=2, max_pages=1).lookup(
        workflow=workflow, ref=ref, dispatch_key=KEY
    )
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}


def validate_same_count_page_reorder_is_unknown():
    workflow, ref, _inputs = request_for("developer")
    rows = [
        FakeGitHubActionsHttp.run(
            run_id=500 + index,
            workflow=workflow,
            key="dispatch-" + f"{index + 31:040x}",
        )
        for index in range(4)
    ]
    fake = FakeGitHubActionsHttp(
        runs=rows,
        explicit_pages={1: rows[:2], 2: [rows[1], rows[2]]},
    )
    receipt = transport(fake, page_size=2, max_pages=3).lookup(
        workflow=workflow, ref=ref, dispatch_key=KEY
    )
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}


def validate_duplicate_runs_are_unknown():
    workflow, ref, _inputs = request_for("developer")
    fake = FakeGitHubActionsHttp(
        runs=[
            FakeGitHubActionsHttp.run(run_id=301, workflow=workflow),
            FakeGitHubActionsHttp.run(run_id=302, workflow=workflow),
        ]
    )
    receipt = transport(fake).lookup(
        workflow=workflow, ref=ref, dispatch_key=KEY
    )
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}


def validate_lookup_history_change_is_unknown():
    workflow, ref, _inputs = request_for("developer")
    unrelated = [
        FakeGitHubActionsHttp.run(
            run_id=400 + index,
            workflow=workflow,
            key="dispatch-" + f"{index + 21:040x}",
        )
        for index in range(3)
    ]
    fake = FakeGitHubActionsHttp(
        runs=unrelated,
        total_count_sequence=[3, 4],
    )
    receipt = transport(fake, page_size=2, max_pages=3).lookup(
        workflow=workflow, ref=ref, dispatch_key=KEY
    )
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}


def validate_api_failure_is_unknown():
    workflow, ref, _inputs = request_for("developer")
    fake = FakeGitHubActionsHttp(get_status=503)
    receipt = transport(fake).lookup(
        workflow=workflow, ref=ref, dispatch_key=KEY
    )
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}


def validate_post_failure_has_no_internal_retry():
    fake = FakeGitHubActionsHttp(post_status=500)
    runtime = transport(fake)
    workflow, ref, inputs = request_for("developer")
    expect_vertical_error(
        lambda: runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs),
        "HTTP 500 dispatch did not fail closed",
    )
    assert fake.post_calls == 1


def validate_untrusted_request_denial():
    workflow, ref, inputs = request_for("reviewer")
    cases = []
    cases.append(("foreign.yml", ref, dict(inputs)))
    cases.append((workflow, "feature/untrusted", dict(inputs)))
    for field, value in (
        ("dispatch_key", "bad-key"),
        ("role", "developer"),
        ("unexpected", "value"),
    ):
        bad = dict(inputs)
        bad[field] = value
        cases.append((workflow, ref, bad))

    for mutator in (
        lambda p: p["task"].__setitem__("role", "developer"),
        lambda p: p["feature_context"]["vertical"].__setitem__("expected_revision", 8),
        lambda p: p["feature_context"]["vertical"].__setitem__("external_dispatch_key", "dispatch-" + "f" * 40),
        lambda p: p["feature_context"]["vertical"].__setitem__("candidate_head_sha", "c" * 40),
    ):
        bad = dict(inputs)
        payload = json.loads(bad["task_payload"])
        mutator(payload)
        bad["task_payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        cases.append((workflow, ref, bad))

    for bad_workflow, bad_ref, bad_inputs in cases:
        fake = FakeGitHubActionsHttp()
        runtime = transport(fake)
        expect_vertical_error(
            lambda bw=bad_workflow, br=bad_ref, bi=bad_inputs: runtime.dispatch(
                workflow=bw, ref=br, inputs=bi
            ),
            "untrusted transport request was accepted",
        )
        assert fake.post_calls == 0


def main():
    validate_accepted_dispatch_and_lease()
    validate_delayed_visibility_is_unknown()
    validate_lost_ack_visibility_lag_is_unknown()
    validate_lost_ack_visible_run_is_adopted()
    validate_exhaustive_not_launched()
    validate_truncated_history_is_unknown()
    validate_same_count_page_reorder_is_unknown()
    validate_duplicate_runs_are_unknown()
    validate_lookup_history_change_is_unknown()
    validate_api_failure_is_unknown()
    validate_post_failure_has_no_internal_retry()
    validate_untrusted_request_denial()
    print("production Vertical gh-aw GitHub Actions transport validation passed")
    print("- exactly one workflow_dispatch POST; no internal retry")
    print("- accepted-but-not-visible run remains UNKNOWN")
    print("- lost/ambiguous ACK is contained inside transport and never becomes NOT_LAUNCHED")
    print("- later read-only visibility adopts the original exact run")
    print("- NOT_LAUNCHED requires exhaustive stable unique-id workflow history")
    print("- truncated/reordered/changing/duplicate/API-failure lookup is UNKNOWN")
    print("- workflow/ref/outer+inner task payload surface is strictly Vertical-bound")


if __name__ == "__main__":
    main()