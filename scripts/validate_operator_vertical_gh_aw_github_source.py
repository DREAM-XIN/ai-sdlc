#!/usr/bin/env python3
"""Adversarial validation for production GitHub Actions gh-aw result sourcing."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import urlparse

from operator_vertical import VERTICAL_PROFILE, VerticalInvariantError
from operator_vertical_gh_aw import GhAwVerticalWorkflowMap
from operator_vertical_gh_aw_github_source import (
    GitHubActionsGhAwResultSourceConfig,
    TargetScopedGitHubActionsGhAwResultSource,
)

ROOT = Path(__file__).resolve().parents[1]
CONTROL = "DREAM-XIN/ai-sdlc"
TARGET = "dream-xin/fixture"
FEATURE = "F-GHAW-COLLECTOR-REAL"
TARGET_REF = "feature/F-GHAW-COLLECTOR-REAL"
HEAD = "a" * 40
SOURCE_SHA = "b" * 40
EFFECT = "c" * 64
KEY = "dispatch-" + "d" * 48
RUN_ID = 101
DISPATCH_ID = "vertical-dispatch-real-1"
NOW = "2026-08-14T03:00:00Z"


def workflows():
    return GhAwVerticalWorkflowMap(
        default_branch="main",
        developer_workflow="ai-sdlc-gh-aw-worker-codex.lock.yml",
        reviewer_workflow="ai-sdlc-gh-aw-reviewer-claude.lock.yml",
        qa_workflow="ai-sdlc-gh-aw-qa-gemini.lock.yml",
    )


def trusted_context(role):
    stage = "implementation" if role == "developer" else "code-review" if role == "reviewer" else "verification"
    return {
        "operation_id": "op-real-1",
        "operation_generation": 0,
        "operation_profile": VERTICAL_PROFILE,
        "semantic_effect_key": EFFECT,
        "external_dispatch_key": KEY,
        "dispatch_id": DISPATCH_ID,
        "target_repository": TARGET,
        "target_ref": TARGET_REF,
        "feature_id": FEATURE,
        "expected_revision": 7,
        "feature_stage": stage,
        "role": role,
        "launch_candidate_head_sha": None if role == "developer" else HEAD,
    }


def reviewer_result():
    return {
        "version": "0.1.0",
        "contract": "ai-sdlc-gh-aw-reviewer-result-v0.1",
        "id": "GATE-REVIEW-1",
        "feature_id": FEATURE,
        "task_id": "REVIEW-1",
        "stage": "code-review",
        "role": "reviewer",
        "expected_revision": 7,
        "target_repository": TARGET,
        "target_ref": TARGET_REF,
        "candidate_pr_number": 42,
        "candidate_head_sha": HEAD,
        "verdict": "PASS",
        "findings": [],
        "evidence": [{"id": "review-1", "type": "review", "status": "pass", "uri": "https://example.invalid/review"}],
        "occurred_at": NOW,
    }


def qa_result():
    return {
        "version": "0.1.0",
        "contract": "ai-sdlc-gh-aw-qa-result-v0.1",
        "id": "GATE-QA-1",
        "feature_id": FEATURE,
        "task_id": "QA-1",
        "stage": "verification",
        "role": "qa",
        "expected_revision": 7,
        "target_repository": TARGET,
        "target_ref": TARGET_REF,
        "candidate_pr_number": 42,
        "candidate_head_sha": HEAD,
        "verdict": "PASS",
        "checks": [{"name": "unit", "status": "pass"}],
        "coverage": [{"criterion": "exact callback", "status": "pass", "evidence": "run"}],
        "evidence": [{"id": "qa-1", "type": "verification", "status": "pass", "uri": "https://example.invalid/qa"}],
        "occurred_at": NOW,
    }


def gate_body(payload):
    return "<!-- AI-SDLC-GATE-RESULT\n" + json.dumps(payload, separators=(",", ":")) + "\nAI-SDLC-GATE-RESULT -->\nsummary"


class FakeHttp:
    def __init__(self, *, role="reviewer", safe="success", title=None, comment_payload=None, env_overrides=None):
        self.role = role
        self.safe = safe
        self.title = title or f"AI-SDLC gh-aw {KEY}"
        self.comment_payload = deepcopy(comment_payload or (qa_result() if role == "qa" else reviewer_result()))
        self.env_overrides = dict(env_overrides or {})

    def _run(self):
        return {
            "id": RUN_ID,
            "html_url": f"https://github.com/{CONTROL}/actions/runs/{RUN_ID}",
            "path": f".github/workflows/{workflows().workflow_for(self.role)}",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "display_title": self.title,
            "head_branch": "main",
            "head_sha": SOURCE_SHA,
        }

    def _jobs(self):
        return {"jobs": [
            {"id": 11, "name": "safe_outputs", "conclusion": self.safe},
            {"id": 12, "name": "conclusion", "conclusion": "success"},
        ]}

    def _developer_env(self):
        vertical = {
            "profile": VERTICAL_PROFILE,
            "operation_id": "op-real-1",
            "operation_generation": 0,
            "semantic_effect_key": EFFECT,
            "external_dispatch_key": KEY,
            "dispatch_id": DISPATCH_ID,
            "expected_revision": 7,
            "candidate_head_sha": None,
        }
        task_payload = {
            "contract": "ai-sdlc-task-v0.1",
            "task": {"id": "vertical:implementation:7", "role": "developer"},
            "feature_context": {"id": FEATURE, "repository": TARGET, "vertical": vertical},
        }
        env = {
            "TARGET_REPOSITORY": TARGET,
            "TARGET_REF": TARGET_REF,
            "FEATURE_ID": FEATURE,
            "EXPECTED_REVISION": "7",
            "STAGE": "implementation",
            "TASK_PAYLOAD": json.dumps(task_payload, separators=(",", ":"), sort_keys=True),
            "PR_URL": f"https://github.com/{TARGET}/pull/41",
            "RUN_URL": f"https://github.com/{CONTROL}/actions/runs/{RUN_ID}",
        }
        env.update(self.env_overrides)
        return env

    def _gate_env(self):
        role = self.role
        env = {
            "TARGET_REPOSITORY": TARGET,
            "TARGET_REF": TARGET_REF,
            "FEATURE_ID": FEATURE,
            "TRUSTED_TASK_ID": "QA-1" if role == "qa" else "REVIEW-1",
            "EXPECTED_REVISION": "7",
            "STAGE": "verification" if role == "qa" else "code-review",
            "ROLE": role,
            "CANDIDATE_PR_NUMBER": "42",
            "CANDIDATE_HEAD_SHA": HEAD,
            "SOURCE_RUN_ID": str(RUN_ID),
            "SOURCE_WORKFLOW_REF": f"{CONTROL}/.github/workflows/{workflows().workflow_for(role)}@refs/heads/main",
            "COMMENT_ID": "91",
            "COMMENT_URL": f"https://github.com/{TARGET}/pull/42#issuecomment-91",
        }
        env.update(self.env_overrides)
        return env

    def _pr(self):
        return {
            "number": 41,
            "html_url": f"https://github.com/{TARGET}/pull/41",
            "state": "open",
            "draft": True,
            "base": {"ref": TARGET_REF},
            "head": {"sha": HEAD, "ref": f"gh-aw/{FEATURE}-{RUN_ID}-v7-salt"},
        }

    def _comment(self):
        return {
            "id": 91,
            "html_url": f"https://github.com/{TARGET}/pull/42#issuecomment-91",
            "issue_url": f"https://api.github.com/repos/{TARGET}/issues/42",
            "user": {"type": "Bot"},
            "body": gate_body(self.comment_payload),
        }

    def __call__(self, *, method, url, token):
        assert method == "GET" and token in {"control-token", "target-token"}
        path = urlparse(url).path
        if path.endswith(f"/actions/runs/{RUN_ID}"):
            return 200, {}, json.dumps(self._run()).encode()
        if path.endswith(f"/actions/runs/{RUN_ID}/jobs"):
            return 200, {}, json.dumps(self._jobs()).encode()
        if path.endswith("/actions/jobs/12/logs"):
            env = self._developer_env() if self.role == "developer" else self._gate_env()
            rows = [f"2026-08-14T03:00:00Z   {key}: {value}" for key, value in env.items()]
            return 200, {}, ("\n".join(rows) + "\n").encode()
        if path.endswith("/pulls/41"):
            return 200, {}, json.dumps(self._pr()).encode()
        if path.endswith("/issues/comments/91"):
            return 200, {}, json.dumps(self._comment()).encode()
        return 404, {}, b"{}"


def source(fake):
    cfg = GitHubActionsGhAwResultSourceConfig(
        control_repository=CONTROL,
        control_token="control-token",
        target_token="target-token",
        workflows=workflows(),
        collector_identity="collector:github-actions/v1",
    )
    return TargetScopedGitHubActionsGhAwResultSource(cfg, target_repository=TARGET, http=fake)


def resolve(fake):
    return source(fake).resolve(
        external_dispatch_key=KEY,
        expected_receipt_identity=str(RUN_ID),
        trusted_context=trusted_context(fake.role),
    )


def expect_closed(fake, message, *, receipt=str(RUN_ID)):
    try:
        source(fake).resolve(
            external_dispatch_key=KEY,
            expected_receipt_identity=receipt,
            trusted_context=trusted_context(fake.role),
        )
    except VerticalInvariantError:
        return
    raise AssertionError(message)


def validate_roles():
    reviewer = FakeHttp(role="reviewer")
    result = resolve(reviewer)
    assert result.run.role == "reviewer" and result.run.candidate_pr_number == 42
    assert result.role_payload["verdict"] == "PASS"
    assert json.loads(source(reviewer).load_content(result.outputs[0].trusted_uri))["contract"] == "ai-sdlc-gh-aw-reviewer-result-v0.1"

    qa = FakeHttp(role="qa")
    result = resolve(qa)
    assert result.role_payload["verdict"] == "PASS"
    assert all(row["status"] == "PASS" for row in result.role_payload["checks"])

    developer = FakeHttp(role="developer")
    result = resolve(developer)
    assert result.run.candidate_pr_number == 41 and result.run.candidate_head_sha == HEAD
    assert "candidate_head_sha" not in result.role_payload
    content = json.loads(source(developer).load_content(result.outputs[0].trusted_uri))
    assert content["pr_number"] == 41 and content["head_sha"] == HEAD


def validate_adversarial():
    expect_closed(FakeHttp(role="reviewer"), "forged receipt accepted", receipt="999")
    expect_closed(FakeHttp(role="reviewer", title="AI-SDLC gh-aw forged"), "wrong stable run-name accepted")
    expect_closed(FakeHttp(role="reviewer", safe="failure"), "failed Safe Output accepted")
    expect_closed(FakeHttp(role="reviewer", env_overrides={"EXPECTED_REVISION": "8"}), "Gate revision mismatch accepted")
    expect_closed(FakeHttp(role="developer", env_overrides={"TARGET_REF": "feature/forged"}), "Developer target-ref mismatch accepted")

    malformed = reviewer_result()
    malformed["candidate_head_sha"] = "f" * 40
    expect_closed(FakeHttp(role="reviewer", comment_payload=malformed), "forged Gate candidate accepted")

    nonpassing = reviewer_result()
    nonpassing["evidence"][0]["status"] = "fail"
    expect_closed(FakeHttp(role="reviewer", comment_payload=nonpassing), "Reviewer PASS with failed evidence accepted")

    qa_nonpassing = qa_result()
    qa_nonpassing["evidence"][0]["status"] = "fail"
    expect_closed(FakeHttp(role="qa", comment_payload=qa_nonpassing), "QA PASS with failed evidence accepted")


def validate_vertical_legacy_fences():
    developer = (ROOT / ".github/workflows/ai-sdlc-gh-aw-result.yml").read_text(encoding="utf-8")
    gate = (ROOT / ".github/workflows/ai-sdlc-gh-aw-gate-result.yml").read_text(encoding="utf-8")
    for label, text in (("developer result", developer), ("gate result", gate)):
        assert "Operation-bound Vertical" in text, f"{label} lacks explicit Vertical writer fence"
        assert "dispatch-[A-Za-z0-9._:-]+" in text, f"{label} lacks stable-key run-name fence"
        assert "steps.legacy_fence.outputs.allowed == 'true'" in text, f"{label} write path ignores fence"
    assert "legacy Feature persistence is fenced" in developer
    assert "legacy Gate persistence is fenced" in gate


def main():
    validate_roles()
    validate_adversarial()
    validate_vertical_legacy_fences()
    print("trusted GitHub Actions gh-aw Vertical result source validation passed")
    print("- durable Store receipt selects exact stable-key Actions run")
    print("- trusted conclusion-log inputs bind exact Safe Output PR/comment")
    print("- Developer/Reviewer/QA results normalize through closed role schemas")
    print("- forged receipt/run/candidate/revision and PASS-with-failed-evidence fail closed")
    print("- Operation-bound Vertical runs cannot use legacy direct Feature/Gate persistence")


if __name__ == "__main__":
    main()
