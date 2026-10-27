import dataclasses
import math
import unittest

from experiments.ntru_hybrid_comparison.budgets import (
    BudgetExhausted, cap_attempt, create_trial_window,
)


class AbsoluteBudgetTests(unittest.TestCase):
    def window(self, **overrides):
        values = dict(experiment_started_at=100.0, total_budget_seconds=30.0,
                      trial_started_at=102.0, trial_budget_seconds=20.0,
                      cleanup_reserve_seconds=2.0)
        values.update(overrides)
        return create_trial_window(**values)

    def test_hierarchical_bound_and_reserve(self):
        trial = self.window()
        self.assertEqual((trial.hard_deadline, trial.work_deadline), (122.0, 120.0))
        self.assertEqual(trial.remaining_work(110), 10)
        self.assertEqual(trial.remaining_hard(110), 12)
        total_limited = self.window(total_budget_seconds=10)
        self.assertEqual((total_limited.hard_deadline, total_limited.work_deadline), (110.0, 108.0))

    def test_new_patterns_do_not_reset_trial_budget(self):
        trial = self.window()
        first = cap_attempt(trial, attempt_started_at=103)
        second = cap_attempt(trial, attempt_started_at=119)
        self.assertEqual(first.hard_deadline, second.hard_deadline)
        self.assertEqual(second.remaining_work(119), 1)
        self.assertFalse(second.can_start(now=120))
        with self.assertRaises(BudgetExhausted) as context:
            second.require_work("new_pattern", now=120)
        self.assertEqual(context.exception.stage, "new_pattern")

    def test_loading_construction_and_reduction_consume_decode_budget(self):
        trial = self.window()
        self.assertEqual(trial.require_work("load", now=102), 18)
        self.assertEqual(trial.require_work("construction", now=105), 15)
        self.assertEqual(trial.require_work("reduction", now=111), 9)
        self.assertEqual(trial.require_work("decode", now=117), 3)
        with self.assertRaises(BudgetExhausted):
            trial.require_work("heavy_preprocess", now=117, minimum_work_seconds=4)

    def test_pattern_cap_only_tightens_and_includes_cleanup(self):
        trial = self.window()
        pattern = cap_attempt(trial, attempt_started_at=110, pattern_limit_seconds=5)
        self.assertEqual((pattern.hard_deadline, pattern.work_deadline), (115, 113))
        nested = cap_attempt(pattern, attempt_started_at=112, pattern_limit_seconds=100)
        self.assertEqual((nested.hard_deadline, nested.work_deadline), (115, 113))
        self.assertEqual(pattern.remaining_hard(114), 1)
        self.assertFalse(pattern.can_start(now=114))

    def test_zero_expired_and_insufficient_reserve_do_not_start(self):
        for trial in (self.window(trial_budget_seconds=0),
                      self.window(total_budget_seconds=1),
                      self.window(trial_budget_seconds=1, cleanup_reserve_seconds=2)):
            self.assertFalse(trial.can_start(now=102))
            with self.assertRaises(BudgetExhausted):
                trial.require_work("spawn", now=102)

    def test_no_work_after_bound_even_if_minimum_is_zero(self):
        trial = self.window()
        self.assertTrue(trial.can_start(now=119.5, minimum_work_seconds=0.5))
        self.assertFalse(trial.can_start(now=119.5, minimum_work_seconds=0.6))
        self.assertEqual(trial.remaining_work(125), 0)
        self.assertEqual(trial.remaining_hard(125), 0)

    def test_immutable_window_and_portable_record(self):
        trial = self.window()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            trial.hard_deadline = 999
        record = trial.to_record()
        self.assertEqual(record["requested_budget_seconds"], 20)
        self.assertEqual(record["hard_deadline_relative_seconds"], 20)
        self.assertEqual(record["work_deadline_relative_seconds"], 18)
        self.assertTrue(record["cleanup_reserve_is_inside_budget"])
        self.assertEqual(record["clock"], "time.monotonic")

    def test_bad_inputs_are_rejected(self):
        for value in (-1, True, math.inf, math.nan, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.window(trial_budget_seconds=value)
        with self.assertRaises(ValueError):
            self.window(trial_started_at=99)
        trial = self.window()
        with self.assertRaises(ValueError):
            cap_attempt(trial, attempt_started_at=101)
        for value in (-1, True, math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cap_attempt(trial, attempt_started_at=103, pattern_limit_seconds=value)
        with self.assertRaises(ValueError):
            trial.can_start(now=103, minimum_work_seconds=-1)


if __name__ == "__main__":
    unittest.main()
