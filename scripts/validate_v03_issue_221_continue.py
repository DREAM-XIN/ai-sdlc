#!/usr/bin/env python3
"""Deterministic scheduling tests; no live dispatch or state writes."""
import unittest
from v03_issue_221_continue import (
    ContinueError, Step, choose_reuse, live_steps, selected_steps,
)

SHA = "a" * 40
STEP = Step("v03-live-remaining-six.yml", "duplicate-worker-completion",
            "v03-live-remaining-six-duplicate-worker-completion")

def run(run_id=1, **changes):
    row = dict(id=run_id, head_sha=SHA, head_branch="main",
               event="workflow_dispatch", status="completed", conclusion="success")
    row.update(changes)
    return row

def artifact(**changes):
    row = dict(id=10, name=STEP.artifact, expired=False)
    row.update(changes)
    return row

class ContinueTests(unittest.TestCase):
    def pick(self, rows, artifacts=None):
        return choose_reuse(STEP, SHA, rows, lambda _: artifacts if artifacts is not None else [artifact()])

    def test_same_version_success_reused(self):
        self.assertEqual(self.pick([run()]), 1)

    def test_new_version_fork_and_pr_runs_not_reused(self):
        for changes in (dict(head_sha="b"*40), dict(head_branch="other"),
                        dict(event="pull_request"), dict(id=True)):
            self.assertIsNone(self.pick([run(**changes)]))

    def test_active_run_blocks_even_with_success(self):
        with self.assertRaises(ContinueError):
            self.pick([run(), run(2, status="in_progress", conclusion=None)])

    def test_failure_never_redispatched(self):
        with self.assertRaises(ContinueError):
            self.pick([run(conclusion="failure")])

    def test_setup_failure_without_artifact_blocks(self):
        with self.assertRaises(ContinueError):
            self.pick([run(conclusion="failure")], [])

    def test_expired_or_missing_evidence_blocks(self):
        for artifacts in ([artifact(expired=True)], []):
            with self.assertRaises(ContinueError):
                self.pick([run()], artifacts)

    def test_duplicate_success_and_duplicate_artifacts_block(self):
        with self.assertRaises(ContinueError):
            self.pick([run(), run(2)])
        with self.assertRaises(ContinueError):
            self.pick([run()], [artifact(), artifact(id=11)])

    def test_other_scenario_success_does_not_skip_requested_scenario(self):
        other = next(s for s in live_steps() if s.workflow == STEP.workflow and s.artifact != STEP.artifact)
        self.assertIsNone(self.pick([run()], [artifact(name=other.artifact)]))

    def test_empty_history_dispatch_allowed(self):
        self.assertIsNone(self.pick([]))

    def test_resume_preserves_full_matrix_and_final_gate(self):
        steps = selected_steps("resume", "")
        self.assertEqual(len(steps), 16)
        self.assertEqual(steps[4:-1], live_steps())
        self.assertEqual(steps[-1].workflow, "v03-final-live-ledger.yml")

    def test_targeted_run_has_no_final_pass(self):
        steps = selected_steps("scenario", STEP.scenario)
        self.assertEqual(len(steps), 5)
        self.assertEqual(steps[-1], STEP)
        self.assertFalse(any(s.workflow == "v03-final-live-ledger.yml" for s in steps))

    def test_invalid_modes_and_scenarios_rejected(self):
        for mode, scenario in (("all", ""), ("resume", STEP.scenario),
                               ("scenario", ""), ("scenario", "invented")):
            with self.assertRaises(ContinueError):
                selected_steps(mode, scenario)

if __name__ == "__main__":
    unittest.main()
