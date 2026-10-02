#!/usr/bin/env python3
"""Exercise PR indexing lag without any GitHub request, dispatch, Store write, or sleep."""
import unittest
from types import SimpleNamespace
from v03_remaining_six_live_runner import _wait_for_candidate_transition

A, B, C = "a"*40, "b"*40, "c"*40

class TransitionTests(unittest.TestCase):
    def fixture(self, heads, number=901):
        self.reads, self.pauses = [], []
        heads = iter(heads)
        def read(**kwargs):
            self.reads.append(kwargs)
            return SimpleNamespace(candidate_pr_number=number, candidate_head_sha=next(heads))
        return SimpleNamespace(
            fixture_candidate=SimpleNamespace(candidate_pr_number=901),
            execution=SimpleNamespace(repository="dream-xin/ai-sdlc"),
            slot=SimpleNamespace(feature_id="fixture", target_ref="verification/fixture"),
            composition=SimpleNamespace(candidate_provider=SimpleNamespace(current_candidate=read)),
        )

    def wait(self, fixture, attempts=3):
        _wait_for_candidate_transition(fixture, old_head=A, new_head=B, attempts=attempts, pause=self.pauses.append)

    def test_delayed_exact_head(self):
        self.wait(self.fixture([A, A, B]))
        self.assertEqual(len(self.reads), 3)
        self.assertEqual(self.pauses, [2.0, 2.0])
        self.assertTrue(all(r["target_ref"] == "verification/fixture" for r in self.reads))

    def test_immediate_exact_head(self):
        self.wait(self.fixture([B]))
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(self.pauses, [])

    def test_old_head_times_out(self):
        with self.assertRaisesRegex(Exception, "bounded visibility wait"):
            self.wait(self.fixture([A, A, A]))
        self.assertEqual(len(self.reads), 3)
        self.assertEqual(len(self.pauses), 2)

    def test_third_head_rejected_immediately(self):
        with self.assertRaisesRegex(Exception, "unexpected transition head"):
            self.wait(self.fixture([C]))
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(self.pauses, [])

    def test_pr_identity_change_rejected(self):
        with self.assertRaisesRegex(Exception, "PR identity changed"):
            self.wait(self.fixture([B], number=902))
        self.assertEqual(self.pauses, [])

    def test_invalid_bound_rejected_without_read(self):
        with self.assertRaisesRegex(Exception, "positive bound"):
            self.wait(self.fixture([B]), attempts=0)
        self.assertEqual(self.reads, [])

if __name__ == "__main__":
    unittest.main()
