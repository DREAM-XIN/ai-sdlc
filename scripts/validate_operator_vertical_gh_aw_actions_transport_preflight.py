#!/usr/bin/env python3
"""Focused global preflight/no-speculative-dispatch checks for Vertical gh-aw transport."""
from __future__ import annotations

from pathlib import Path

from validate_operator_vertical_gh_aw_actions_transport import (
    FakeGitHubActionsHttp,
    KEY,
    WORKFLOWS,
    request_for,
    transport,
)


def validate_existing_selected_run_skips_post():
    workflow, ref, inputs = request_for("developer")
    fake = FakeGitHubActionsHttp(
        runs=[FakeGitHubActionsHttp.run(run_id=7001, workflow=workflow)]
    )
    runtime = transport(fake)
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "LAUNCHED", "receipt_id": "7001"}
    assert fake.post_calls == 0


def validate_wrong_role_collision_skips_post():
    selected, ref, inputs = request_for("developer")
    reviewer_workflow = WORKFLOWS.reviewer_workflow
    fake = FakeGitHubActionsHttp(
        runs=[FakeGitHubActionsHttp.run(run_id=7002, workflow=reviewer_workflow)]
    )
    runtime = transport(fake)
    receipt = runtime.dispatch(workflow=selected, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert fake.post_calls == 0


def validate_multi_role_collision_skips_post():
    selected, ref, inputs = request_for("developer")
    fake = FakeGitHubActionsHttp(
        runs=[
            FakeGitHubActionsHttp.run(run_id=7003, workflow=selected),
            FakeGitHubActionsHttp.run(run_id=7004, workflow=WORKFLOWS.qa_workflow),
        ]
    )
    runtime = transport(fake)
    receipt = runtime.dispatch(workflow=selected, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert fake.post_calls == 0


def validate_unknown_preflight_skips_post():
    workflow, ref, inputs = request_for("developer")
    fake = FakeGitHubActionsHttp(get_status=503)
    runtime = transport(fake)
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert fake.post_calls == 0


def validate_truncated_preflight_skips_post():
    workflow, ref, inputs = request_for("developer")
    runs = [
        FakeGitHubActionsHttp.run(
            run_id=7100 + index,
            workflow=workflow,
            key="dispatch-" + f"{index + 80:040x}",
        )
        for index in range(3)
    ]
    fake = FakeGitHubActionsHttp(runs=runs)
    runtime = transport(fake, page_size=1, max_pages=1)
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "UNKNOWN", "receipt_id": None}
    assert fake.post_calls == 0


def validate_global_exhaustive_absence_allows_one_post():
    workflow, ref, inputs = request_for("developer")
    fake = FakeGitHubActionsHttp(create_run_on_post=True)
    runtime = transport(fake)
    receipt = runtime.dispatch(workflow=workflow, ref=ref, inputs=inputs)
    assert receipt == {"lookup_state": "LAUNCHED", "receipt_id": "9001"}
    assert fake.post_calls == 1
    # Three role workflows were exhaustively preflighted before the POST.
    assert fake.get_calls >= 4


def validate_contract_text():
    raw = Path("scripts/operator_vertical_gh_aw_actions_transport.py").read_text(
        encoding="utf-8"
    )
    global_preflight = raw.index("before = self._global_preflight(")
    post = raw.index('method="POST"')
    assert global_preflight < post
    assert "for workflow in self._workflow_order:" in raw
    assert "cross-role collision/duplicate effect" in raw
    assert 'before.get("lookup_state") == "LAUNCHED"' in raw
    assert 'before.get("lookup_state") != "NOT_LAUNCHED"' in raw
    assert "All three workflows must exhaustively prove" in raw


def main():
    validate_existing_selected_run_skips_post()
    validate_wrong_role_collision_skips_post()
    validate_multi_role_collision_skips_post()
    validate_unknown_preflight_skips_post()
    validate_truncated_preflight_skips_post()
    validate_global_exhaustive_absence_allows_one_post()
    validate_contract_text()
    print("Vertical gh-aw global dispatch preflight validation passed")
    print("- existing selected-role run is adopted with zero POST")
    print("- wrong-role/multi-role stable-key collisions produce UNKNOWN with zero POST")
    print("- UNKNOWN/truncated external state forbids speculative POST")
    print("- all three trusted role workflows must prove absence before one POST")


if __name__ == "__main__":
    main()
