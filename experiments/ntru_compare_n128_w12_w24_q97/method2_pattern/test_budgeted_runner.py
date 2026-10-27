"""Public-only regressions for the fresh fixed-budget pattern runner.

These tests never invoke BKZ and never open the experiment's private input.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from . import budgeted_search as search


class FixedAllowanceTests(unittest.TestCase):
    def test_per_pattern_limit_remains_fixed_even_with_generous_policy(self) -> None:
        group = {"group_budget_seconds": 473.11 * 56, "charged_wall_seconds": 0.0}
        try:
            allowance = search.attempt_allowance(group, {"per_pattern_seconds": 946.22})
        except ValueError:
            return
        self.assertLessEqual(allowance, 473.11 - search.CLEANUP_RESERVE)

    def test_last_attempt_can_never_spend_past_its_group(self) -> None:
        for remaining in (0.0, 1.0, 2.1, 2.10001, 13.25, 473.11, 473.12):
            with self.subTest(remaining=remaining):
                group = {"group_budget_seconds": 473.11 * 12,
                         "charged_wall_seconds": 473.11 * 12 - remaining}
                allowance = search.attempt_allowance(group, {"per_pattern_seconds": 473.11})
                self.assertGreaterEqual(allowance, 0.0)
                self.assertLessEqual(allowance, 473.11 - search.CLEANUP_RESERVE)
                if allowance:
                    self.assertLessEqual(allowance + search.CLEANUP_RESERVE,
                                         remaining + 1e-9)
                else:
                    self.assertLessEqual(remaining, search.CLEANUP_RESERVE + 1e-9)

    def test_invalid_accounting_is_rejected_before_launch(self) -> None:
        for invalid in (-1, float("nan"), float("inf"), True, "0"):
            for field in ("group_budget_seconds", "charged_wall_seconds"):
                with self.subTest(invalid=invalid, field=field):
                    group = {"group_budget_seconds": 473.11 * 12,
                             "charged_wall_seconds": 0.0, field: invalid}
                    with self.assertRaises(ValueError):
                        search.attempt_allowance(group, {"per_pattern_seconds": 473.11})


class PublicPatternTests(unittest.TestCase):
    def test_public_patterns_are_reproducible_sorted_sets(self) -> None:
        for k in search.K_VALUES:
            for index in (0, 3, 4, 100):
                with self.subTest(k=k, index=index):
                    pattern = search.public_pattern(k, index)
                    self.assertEqual(pattern, search.public_pattern(k, index))
                    self.assertEqual(len(pattern), k)
                    self.assertEqual(pattern, sorted(set(pattern)))
                    self.assertTrue(all(0 <= coordinate < 128 for coordinate in pattern))

    def test_different_dimensions_use_one_paired_public_permutation(self) -> None:
        for index in (0, 4, 100):
            previous = set()
            for k in search.K_VALUES:
                current = set(search.public_pattern(k, index))
                self.assertTrue(previous < current)
                previous = current

    def test_first_public_attempts_do_not_repeat_patterns(self) -> None:
        for k in search.K_VALUES:
            observed = [tuple(search.public_pattern(k, index)) for index in range(16)]
            self.assertEqual(len(set(observed)), len(observed))

    def test_next_index_preserves_every_launched_status(self) -> None:
        self.assertEqual(search.next_pattern_index([]), 0)
        for status in ("timeout", "interrupted", "error", "exhausted", "success", "running"):
            with self.subTest(status=status):
                attempts = [{"index": 3, "status": status},
                            {"index": 0, "status": "timeout"}]
                self.assertEqual(search.next_pattern_index(attempts), 4)

    def test_next_index_treats_gaps_as_previously_reserved_work(self) -> None:
        attempts = [{"index": 0}, {"index": 5}, {"index": 2}]
        self.assertEqual(search.next_pattern_index(attempts), 6)

    def test_pattern_generator_rejects_invalid_parameters(self) -> None:
        for k, index in ((0, 0), (128, 0), (12, -1), (12, True), (12, 0.5)):
            with self.subTest(k=k, index=index):
                with self.assertRaises(ValueError):
                    search.public_pattern(k, index)


class FreshRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name) / "fresh"
        search.prepare(self.run)
        self.captures = []

    def interrupted_capture(self, command, prefix, timeout):
        """Write only the evidence a terminated capture would have produced."""
        metadata = {"status": "interrupted", "wall_seconds": .125, "exit_code": -15}
        search.original.write_json(prefix.with_suffix(".command.json"), metadata)
        config = search.read(Path(command[command.index("--config") + 1]))
        self.captures.append({"index": config["pattern_index"], "k": config["k"],
                              "pattern": config["pattern"], "timeout": timeout})
        return metadata

    def execute_with(self, capture):
        with mock.patch.object(search.original, "capture_online", side_effect=capture):
            with contextlib.redirect_stdout(io.StringIO()):
                return search.execute(self.run)

    def exhaust_other_groups(self, remaining_in_first=0.0):
        state = search.verify(self.run)
        for number, group in enumerate(state["groups"]):
            remaining = remaining_in_first if number == 0 else 0.0
            group["charged_wall_seconds"] = group["group_budget_seconds"] - remaining
            group["remaining_seconds"] = remaining
        search.append(self.run, state, "test_fixture_precharged_budget")

    def test_prepare_starts_all_ten_groups_without_historical_cost(self) -> None:
        state = search.verify(self.run)
        self.assertEqual([group["k"] for group in state["groups"]], search.K_VALUES)
        for group in state["groups"]:
            self.assertEqual(group["attempts"], [])
            self.assertEqual(group["charged_wall_seconds"], 0.0)
            self.assertEqual(group["group_budget_seconds"], 473.11 * group["k"])
        with self.assertRaises(ValueError):
            search.prepare(self.run.parent / "cannot_import", prefix=self.run)
        with self.assertRaises(FileExistsError):
            search.prepare(self.run)

    def test_exhausted_groups_launch_no_worker_and_finished_resume_is_idle(self) -> None:
        self.exhaust_other_groups()
        with mock.patch.object(search.original, "capture_online") as capture:
            with contextlib.redirect_stdout(io.StringIO()):
                state = search.execute(self.run)
                resumed = search.execute(self.run)
        capture.assert_not_called()
        self.assertEqual(state, resumed)
        self.assertEqual(state["status"], "online_finished")
        self.assertTrue(all(group["status"] == "budget_exhausted"
                            and group["budget_blocked"] for group in state["groups"]))
        self.assertTrue(all(group["attempts"] == [] for group in state["groups"]))

    def test_last_pattern_uses_only_remaining_budget_and_does_not_replay(self) -> None:
        self.exhaust_other_groups(remaining_in_first=100.0)

        def final_timeout(command, prefix, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 100.0 - search.CLEANUP_RESERVE)
            self.captures.append(timeout)
            metadata = {"status": "timeout", "wall_seconds": timeout, "exit_code": -15}
            search.original.write_json(prefix.with_suffix(".command.json"), metadata)
            return metadata

        state = self.execute_with(final_timeout)
        self.assertEqual(len(self.captures), 1)
        group = state["groups"][0]
        self.assertEqual([attempt["index"] for attempt in group["attempts"]], [0])
        self.assertEqual(group["status"], "budget_exhausted")
        self.assertLessEqual(group["charged_wall_seconds"], group["group_budget_seconds"])
        self.assertLessEqual(group["remaining_seconds"], search.CLEANUP_RESERVE)
        with mock.patch.object(search.original, "capture_online") as capture:
            search.execute(self.run)
        capture.assert_not_called()

    def test_launch_journal_exhaustion_never_forces_a_minimum_worker_timeout(self) -> None:
        self.exhaust_other_groups(remaining_in_first=100.0)
        ticks = iter((0.0, 0.0))
        with mock.patch.object(search.time, "perf_counter", side_effect=lambda: next(ticks, 200.0)):
            with mock.patch.object(search.original, "capture_online") as capture:
                with contextlib.redirect_stdout(io.StringIO()):
                    state = search.execute(self.run)
        capture.assert_not_called()
        self.assertEqual(state["groups"][0]["status"], "budget_exhausted")
        self.assertEqual(state["groups"][0]["attempts"], [])
        search.verify(self.run)

    def test_resume_continues_after_an_interrupted_attempt(self) -> None:
        first = self.execute_with(self.interrupted_capture)
        self.assertEqual(first["status"], "interrupted")
        second = self.execute_with(self.interrupted_capture)
        self.assertEqual(second["status"], "interrupted")
        self.assertEqual([entry["index"] for entry in self.captures], [0, 1])
        self.assertTrue(all(entry["timeout"] <= 473.11 - search.CLEANUP_RESERVE
                            for entry in self.captures))
        self.assertEqual([a["index"] for a in second["groups"][0]["attempts"]], [0, 1])
        search.verify(self.run)

    def test_lost_capture_is_conservatively_charged_without_replay(self) -> None:
        with mock.patch.object(search.original, "capture_online",
                               side_effect=RuntimeError("simulated lost capture")):
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "simulated lost capture"):
                    search.execute(self.run)
        unfinished = search.verify(self.run)["groups"][0]["attempts"][0]
        self.assertEqual(unfinished["status"], "running")
        state = self.execute_with(self.interrupted_capture)
        self.assertEqual([entry["index"] for entry in self.captures], [1])
        reconciled = state["groups"][0]["attempts"][0]
        self.assertEqual(reconciled["status"], "interrupted")
        self.assertEqual(reconciled["charged_wall_seconds"],
                         unfinished["reserved_charge_seconds"])
        self.assertLessEqual(reconciled["charged_wall_seconds"], 473.11)
        search.verify(self.run)

    def test_resume_refuses_to_duplicate_a_worker_whose_pid_is_still_alive(self) -> None:
        def alive_capture(command, prefix, timeout):
            metadata = {"status": "running", "pid": os.getpid(),
                        "process_start_ticks": Path(f"/proc/{os.getpid()}/stat").read_text().split()[21]}
            search.original.write_json(prefix.with_suffix(".command.json"), metadata)
            raise RuntimeError("simulated lost runner")

        with self.assertRaisesRegex(RuntimeError, "simulated lost runner"):
            self.execute_with(alive_capture)
        with mock.patch.object(search.original, "capture_online") as capture:
            with self.assertRaisesRegex(RuntimeError, "still alive"):
                search.execute(self.run)
        capture.assert_not_called()
        state = search.verify(self.run)
        self.assertEqual(len(state["groups"][0]["attempts"]), 1)

    def test_resume_skips_a_duplicate_public_pattern_without_launching_it(self) -> None:
        self.execute_with(self.interrupted_capture)
        actual_public_pattern = search.public_pattern

        def repeat_once(k, index):
            return actual_public_pattern(k, 0 if index == 1 else index)

        with mock.patch.object(search, "public_pattern", side_effect=repeat_once):
            state = self.execute_with(self.interrupted_capture)
        self.assertEqual([entry["index"] for entry in self.captures], [0, 2])
        self.assertEqual(state["groups"][0]["skipped_duplicate_indices"], [1])
        self.assertNotEqual(self.captures[0]["pattern"], self.captures[1]["pattern"])
        search.verify(self.run)

    def test_modified_state_view_is_rejected(self) -> None:
        state_path = self.run / "run_manifest.json"
        state = search.read(state_path)
        state["groups"][0]["charged_wall_seconds"] = 0.01
        search.original.write_json(state_path, state)
        with self.assertRaisesRegex(ValueError, "journal"):
            search.verify(self.run)

    def test_modified_ledger_is_rejected(self) -> None:
        path = self.run / "ledger.jsonl"
        record = json.loads(path.read_text())
        record["state"]["groups"][0]["group_budget_seconds"] += 1
        path.write_text(json.dumps(record) + "\n")
        with self.assertRaisesRegex(ValueError, "hash chain"):
            search.verify(self.run)

    def test_modified_policy_or_frozen_input_is_rejected(self) -> None:
        for relative in ("policy.json", "provenance/public.frozen.json"):
            with self.subTest(relative=relative):
                path = self.run / relative
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                try:
                    with self.assertRaisesRegex(ValueError, "changed"):
                        search.verify(self.run)
                finally:
                    path.write_bytes(original)
        search.verify(self.run)

    def test_modified_launched_configuration_or_command_is_rejected(self) -> None:
        state = self.execute_with(self.interrupted_capture)
        attempt = state["groups"][0]["attempts"][0]
        paths = [self.run / attempt["config"],
                 (self.run / attempt["command_prefix"]).with_suffix(".command.json")]
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                try:
                    with self.assertRaisesRegex(ValueError, "changed"):
                        search.verify(self.run)
                finally:
                    path.write_bytes(original)
        search.verify(self.run)


if __name__ == "__main__":
    unittest.main()
