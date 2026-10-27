"""Synthetic event state tests; these do not demonstrate a decoder call."""

import unittest

from experiments.ntru_hybrid_comparison.progress import (
    ProgressProtocolError, TargetProgress,
)


class StepClock:
    def __init__(self):
        self.value = 10

    def __call__(self):
        self.value += 1
        return self.value


class ProgressTests(unittest.TestCase):
    def make_progress(self, ids=("a", "b", "c")):
        return TargetProgress(ids, clock=StepClock())

    def assert_conserved(self, snapshot):
        self.assertTrue(snapshot["conservation_holds"])
        self.assertEqual(snapshot["submitted_count"], sum(snapshot[key] for key in (
            "completed_count", "errored_count", "cancelled_count", "pending_count")))
        self.assertEqual(snapshot["target_count"], snapshot["submitted_count"]
                         + snapshot["unsubmitted_count"])

    def test_normal_invalid_and_no_candidate_are_normal_completions(self):
        p = self.make_progress()
        p.submit("a")
        p.add_candidate("a", public_valid=True, solution_key=[[1], [1]],
                        origin="babai_only")
        p.complete("a")
        p.submit("b")
        p.add_candidate("b", public_valid=False, origin="slicer_direct")
        p.complete("b")
        p.submit("c")
        p.complete("c")
        s = p.snapshot()
        self.assertEqual((s["submitted_count"], s["completed_count"],
                          s["returned_candidate_count"], s["public_valid_target_count"]),
                         (3, 3, 2, 1))
        self.assertEqual((s["errored_count"], s["pending_count"]), (0, 0))
        self.assert_conserved(s)

    def test_error_cancel_and_pending_are_exclusive(self):
        p = self.make_progress(("a", "b", "c", "d"))
        for key in ("a", "b", "c"):
            p.submit(key)
        p.error("a", "backend exception")
        p.cancel("b", "explicit backend cancellation")
        s = p.snapshot()
        self.assertEqual([s[key] for key in ("submitted_count", "completed_count",
            "errored_count", "cancelled_count", "pending_count", "unsubmitted_count")],
            [3, 0, 1, 1, 1, 1])
        self.assert_conserved(s)

    def test_multiple_candidates_do_not_complete_multiple_times(self):
        p = self.make_progress(("a", "b"))
        p.submit("a")
        p.add_candidate("a", public_valid=False)
        p.add_candidate("a", public_valid=True, solution_key=[[1], [2]])
        p.add_candidate("a", public_valid=True, solution_key=((1,), (2,)))
        self.assertEqual(p.snapshot()["pending_count"], 1)
        p.complete("a")
        p.submit("b")
        p.add_candidate("b", public_valid=True, solution_key=[[1], [2]])
        p.add_candidate("b", public_valid=True, solution_key=[[-1], [-2]])
        p.complete("b")
        s = p.snapshot()
        self.assertEqual([s[k] for k in ("completed_count", "returned_candidate_count",
            "public_valid_candidate_count", "public_valid_target_count",
            "unique_public_valid_solution_count")], [2, 5, 4, 2, 2])
        self.assert_conserved(s)

    def test_early_stop_and_partial_result_preserve_pending_and_unsubmitted(self):
        p = self.make_progress(("a", "b", "c", "d"))
        p.submit("a")
        p.submit("b")
        p.add_candidate("b", public_valid=True, solution_key=[[1], [1]])
        p.complete("b")
        s = p.snapshot()
        self.assertEqual((s["pending_count"], s["unsubmitted_count"],
                          s["completed_count"]), (1, 2, 1))
        self.assertEqual(s["first_public_valid_seconds"], 3)
        self.assertEqual(s["errored_count"], 0)
        self.assert_conserved(s)

    def test_unknown_ids_and_duplicate_terminal_events_are_atomic_protocol_errors(self):
        p = self.make_progress(("a",))
        with self.assertRaises(ProgressProtocolError):
            p.submit("missing")
        p.submit("a")
        with self.assertRaises(ProgressProtocolError):
            p.submit("a")
        p.complete("a")
        before = p.snapshot()
        for operation in (p.complete, p.error, p.cancel):
            with self.assertRaises(ProgressProtocolError):
                operation("a")
        with self.assertRaises(ProgressProtocolError):
            p.add_candidate("a", public_valid=False)
        self.assertEqual(p.snapshot(), before)

    def test_candidates_require_pending_and_valid_result_requires_key(self):
        p = self.make_progress(("a",))
        with self.assertRaises(ProgressProtocolError):
            p.complete("a")
        with self.assertRaises(ProgressProtocolError):
            p.add_candidate("a", public_valid=False)
        p.submit("a")
        before = p.snapshot()
        for kwargs in ({"public_valid": True}, {"public_valid": 1},
                       {"public_valid": False, "origin": ""},
                       {"public_valid": True, "solution_key": float("nan")}):
            with self.assertRaises(ProgressProtocolError):
                p.add_candidate("a", **kwargs)
        self.assertEqual(p.snapshot(), before)

    def test_order_follows_events_and_frozen_targets_not_return_position(self):
        p = self.make_progress(("a", "b", "c"))
        for key in ("c", "a", "b"):
            p.submit(key)
        p.complete("b")
        p.complete("c")
        s = p.snapshot()
        self.assertEqual([row["external_id"] for row in s["targets"]], ["a", "b", "c"])
        self.assertEqual([row["external_id"] for row in s["events"]],
                         ["c", "a", "b", "b", "c"])
        self.assertEqual([row["sequence"] for row in s["events"]], list(range(5)))
        self.assertEqual([row["elapsed_seconds"] for row in s["events"]], [1, 2, 3, 4, 5])
        self.assert_conserved(s)

    def test_snapshot_and_event_returns_are_detached(self):
        p = self.make_progress(("a",))
        event = p.submit("a")
        event["event"] = "forged"
        key = [[1], [2]]
        p.add_candidate("a", public_valid=True, solution_key=key)
        key[0][0] = 99
        s = p.snapshot()
        s["targets"][0]["state"] = "completed"
        s["events"][1]["solution_key"][0][0] = 99
        actual = p.snapshot()
        self.assertEqual(actual["events"][0]["event"], "submitted")
        self.assertEqual(actual["events"][1]["solution_key"], [[1], [2]])
        self.assertEqual(actual["targets"][0]["state"], "pending")

    def test_empty_target_list_and_rejected_unordered_or_duplicate_ids(self):
        p = self.make_progress(())
        self.assertEqual(p.snapshot()["target_count"], 0)
        self.assertIsNone(p.snapshot()["first_public_valid_seconds"])
        self.assert_conserved(p.snapshot())
        for ids in ({"a"}, {"a": 1}, ("a", "a"), (True,), ("",), (0, "a")):
            with self.assertRaises(ProgressProtocolError):
                self.make_progress(ids)

    def test_integer_ids_use_identity_not_position_and_reject_bools(self):
        p = self.make_progress((0, 1, 2))
        p.submit(2)
        p.complete(2)
        p.submit(0)
        with self.assertRaises(ProgressProtocolError):
            p.complete(False)
        s = p.snapshot()
        self.assertEqual([row["external_id"] for row in s["targets"]], [0, 1, 2])
        self.assertEqual([row["state"] for row in s["targets"]],
                         ["pending", "unsubmitted", "completed"])
        self.assert_conserved(s)

    def test_backward_clock_rejected_before_target_mutation(self):
        clock_values = iter((2, 1))
        p = TargetProgress(["a"], clock=lambda: next(clock_values))
        before = p.snapshot()
        with self.assertRaises(ProgressProtocolError):
            p.submit("a")
        self.assertEqual(p.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
