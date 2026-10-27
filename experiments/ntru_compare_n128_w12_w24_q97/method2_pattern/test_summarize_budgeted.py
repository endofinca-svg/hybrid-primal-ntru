"""Offline evidence regressions; these tests do not read private experiment data."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from .summarize_budgeted import combine_timings, memory_evidence, rows_examined, stage_evidence


class StageEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.events = Path(self.temporary.name) / "events.jsonl"

    def write_events(self, *events: tuple[str, str, float]) -> None:
        self.events.write_text("".join(json.dumps({"event": event, "stage": stage, "elapsed_seconds": seconds}) + "\n"
                                      for event, stage, seconds in events))

    def completed_stage(self) -> dict:
        return {"stage_id": "lll", "kind": "lll", "beta": None, "tour": 0,
                "stage_wall_seconds": 3.0, "scan_wall_seconds": 0.25,
                "candidate_count": 0, "rejected_rows": {"norm": 244}, "cumulative_wall_seconds": 4.0}

    def test_completed_scan_count_and_candidate_source(self) -> None:
        stage = self.completed_stage()
        stage.update(candidate_count=1, rejected_rows={"norm": 12, "nonternary": 2})
        rows, totals = stage_evidence({"stages": [stage]}, self.events, False)
        self.assertEqual(rows[0]["basis_rows_examined"], 15)
        self.assertEqual(rows[0]["candidate_source"], "reduced_basis_row")
        self.assertFalse(rows[0]["censored"])
        self.assertEqual(totals["completed_reduction_seconds"], 3.0)
        self.assertEqual(totals["completed_scan_seconds"], 0.25)

    def test_timeout_retains_unfinished_stage_without_inventing_reduction_time(self) -> None:
        self.write_events(("stage_started", "lll", 0.5), ("stage_completed", "lll", 4.0),
                          ("stage_started", "bkz_b030_t01", 4.5), ("worker_finished", "bkz_b030_t01", 9.25))
        rows, totals = stage_evidence({"stages": [self.completed_stage()], "worker_wall_seconds": 9.3}, self.events, True)
        partial = rows[-1]
        self.assertEqual(len(rows), 2)
        self.assertEqual((partial["kind"], partial["beta"], partial["tour"]), ("bkz", 30, 1))
        self.assertTrue(partial["censored"])
        self.assertIsNone(partial["stage_wall_seconds"])
        self.assertIsNone(partial["scan_wall_seconds"])
        self.assertEqual(partial["observed_stage_wall_seconds"], 4.75)
        self.assertEqual(totals["completed_reduction_seconds"], 3.0)
        self.assertEqual(totals["censored_stage_observed_seconds"], 4.75)

    def test_hard_kill_uses_checkpoint_lower_bound(self) -> None:
        self.write_events(("stage_started", "bkz_b031_t02", 10.0))
        rows, totals = stage_evidence({"worker_wall_seconds": 10.25}, self.events, True, 473.11)
        self.assertEqual(rows[-1]["duration_source"], "last_worker_checkpoint_lower_bound")
        self.assertEqual(totals["censored_stage_observed_seconds"], 0.25)
        self.assertAlmostEqual(rows[-1]["observed_stage_wall_seconds_upper_bound"], 463.11)
        self.assertAlmostEqual(totals["censored_stage_upper_bound_seconds"], 463.11)

    def test_missing_worker_result_retains_command_upper_bound(self) -> None:
        rows, totals = stage_evidence({}, self.events, True, 473.11)
        self.assertEqual(rows[-1]["stage_id"], "worker_startup_or_result_unavailable")
        self.assertEqual(totals["censored_stage_observed_seconds"], 0.0)
        self.assertEqual(totals["censored_stage_upper_bound_seconds"], 473.11)

    def test_interrupted_initialization_is_visible(self) -> None:
        self.write_events(("worker_started", "initialization", 0.01),
                          ("worker_finished", "initialization", 0.6))
        rows, totals = stage_evidence({}, self.events, True)
        self.assertEqual(rows[-1]["kind"], "initialization")
        self.assertAlmostEqual(totals["censored_stage_observed_seconds"], 0.59)

    def test_completed_stage_not_duplicated_as_censored(self) -> None:
        self.write_events(("stage_started", "lll", 0.5), ("stage_completed", "lll", 4.0),
                          ("worker_finished", "lll", 4.2))
        rows, totals = stage_evidence({"stages": [self.completed_stage()]}, self.events, True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(totals["censored_stage_observed_seconds"], 0.0)

    def test_nonmonotone_event_clock_is_rejected(self) -> None:
        self.write_events(("stage_started", "lll", 2.0), ("worker_finished", "lll", 1.0))
        with self.assertRaisesRegex(ValueError, "backwards"):
            stage_evidence({}, self.events, True)

    def test_negative_scan_counts_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            rows_examined({"rejected_rows": {"norm": -1}})

    def test_group_totals_keep_censored_and_completed_time_separate(self) -> None:
        _, timings = stage_evidence({"stages": [self.completed_stage()], "timings": {"construction_seconds": 0.5},
                                    "checkpoint_io_seconds": 0.2}, self.events, False)
        totals = combine_timings([{"stage_timings": timings}, {"stage_timings": timings}])
        self.assertEqual(totals["completed_reduction_seconds"], 6.0)
        self.assertEqual(totals["completed_scan_seconds"], 0.5)
        self.assertEqual(totals["censored_stage_observed_seconds"], 0.0)
        self.assertEqual(totals["worker_component_seconds"], {"construction_seconds": 1.0})
        self.assertAlmostEqual(totals["checkpoint_io_seconds"], 0.4)


class MemoryEvidenceTests(unittest.TestCase):
    def test_capture_history_high_water_mark_does_not_replace_missing_worker_peak(self) -> None:
        memory = memory_evidence({}, {"peak_rss_kib": 100000})
        self.assertIsNone(memory["peak_rss_kib"])
        self.assertEqual(memory["command_peak_rss_kib"], 100000)
        self.assertEqual(memory["peak_rss_source"], "unavailable")

    def test_worker_peak_and_capture_peak_are_separate(self) -> None:
        memory = memory_evidence({"peak_rss_kib": 20000}, {"peak_rss_kib": 100000})
        self.assertEqual(memory["peak_rss_kib"], 20000)
        self.assertEqual(memory["command_peak_rss_kib"], 100000)
        self.assertEqual(memory["peak_rss_source"], "worker_RUSAGE_SELF")


if __name__ == "__main__":
    unittest.main()
