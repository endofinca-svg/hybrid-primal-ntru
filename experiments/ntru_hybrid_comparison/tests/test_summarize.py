from copy import deepcopy
import unittest

from experiments.ntru_hybrid_comparison.summarize import qualify_trial, summarize_trials


class TrialAccountingTests(unittest.TestCase):
    def record(self, trial_id="t1", **overrides):
        result = {"instance_id": "tiny-public-fixture", "method": "E", "trial_id": trial_id,
                  "started": True, "cost_mode": "standalone_end_to_end",
                  "public_valid_observed": True, "success_within_budget": True,
                  "execution_status": "completed", "cleanup_verified": True,
                  "eligible_for_primary_comparison": True, "deadlines_satisfied": True,
                  "supervisor_public_validation": {"public_valid": True},
                  "total_wall_seconds": 4.5,
                  "attempts": [{"pattern_attempt_index": 0, "status": "no_solution"},
                               {"pattern_attempt_index": 1, "status": "valid_found"}],
                  "decoder_sidecar": {"total_seconds": 900, "public_valid_target_count": 80}}
        result.update(overrides)
        return result

    def test_many_valid_targets_still_one_trial_success(self):
        record = self.record()
        report = summarize_trials([record])
        self.assertEqual(report["budget_eligible_success_count"], 1)
        self.assertEqual(report["total_pattern_attempt_count"], 2)
        self.assertEqual(report["total_actual_wall_cost_seconds"], 4.5)
        self.assertEqual(report["cost_per_budget_eligible_success_seconds"], 4.5)

    def test_failed_attempts_timeouts_retries_and_cleanup_remain_in_cost(self):
        failed = self.record("t2", success_within_budget=False, public_valid_observed=False,
                             execution_status="timeout", total_wall_seconds=7.5,
                             attempts=[{"pattern_attempt_index": 0},
                                       {"pattern_attempt_index": 1, "status": "truncated"}])
        cleanup_bad = self.record("t3", cleanup_verified=False, total_wall_seconds=3.0)
        report = summarize_trials([self.record(), failed, cleanup_bad])
        self.assertEqual(report["started_trial_count"], 3)
        self.assertEqual(report["budget_eligible_success_count"], 1)
        self.assertEqual(report["failed_trial_count"], 2)
        self.assertEqual(report["timeout_trial_count"], 1)
        self.assertEqual(report["cleanup_failed_or_unverified_trial_count"], 1)
        self.assertEqual(report["total_pattern_attempt_count"], 6)
        self.assertEqual(report["total_actual_wall_cost_seconds"], 15)
        self.assertEqual(report["cost_per_budget_eligible_success_seconds"], 15)

    def test_zero_success_has_no_finite_cost_per_success(self):
        report = summarize_trials([self.record(success_within_budget=False)])
        self.assertEqual(report["budget_eligible_success_count"], 0)
        self.assertIsNone(report["cost_per_budget_eligible_success_seconds"])
        self.assertIsNone(report["groups"][0]["cost_per_budget_eligible_success_seconds"])

    def test_late_public_valid_output_cannot_mask_timeout(self):
        record = self.record(public_valid_observed=True, success_within_budget=False,
                             execution_status="timeout", deadlines_satisfied=False)
        self.assertFalse(qualify_trial(record)["qualified_success"])
        report = summarize_trials([record])
        self.assertEqual(report["groups"][0]["public_valid_observed_trial_count"], 1)
        self.assertEqual(report["budget_eligible_success_count"], 0)

    def test_exception_or_orphan_exit_cannot_be_hidden_by_valid_candidate(self):
        for status in ("worker_exception", "worker_nonzero_exit", "protocol_error",
                       "unexpected_parent_exit", "supervisor_exception",
                       "orphaned_descendants", "supervisor_interrupted"):
            with self.subTest(status=status):
                record = self.record(execution_status=status)
                self.assertFalse(qualify_trial(record)["qualified_success"])
                self.assertEqual(summarize_trials([record])["exception_trial_count"], 1)

    def test_cleanup_failure_preserves_original_timeout_and_exception_categories(self):
        for prior, category in (("timeout", "timeout_trial_count"),
                                ("orphaned_descendants", "exception_trial_count"),
                                ("supervisor_interrupted", "exception_trial_count")):
            with self.subTest(prior=prior):
                record = self.record(execution_status="cleanup_failed", cleanup_verified=False,
                                     attempts=[{"execution_status": "cleanup_failed",
                                                "supervision": {"execution_status": "cleanup_failed",
                                                                "execution_status_before_cleanup": prior}}])
                report = summarize_trials([record])
                self.assertEqual(report[category], 1)
                self.assertEqual(report["cleanup_failed_or_unverified_trial_count"], 1)
                self.assertEqual(report["budget_eligible_success_count"], 0)

    def test_outer_cleanup_failure_preserves_guarded_timeout_and_exception_categories(self):
        for prior, category in (("timeout", "timeout_trial_count"),
                                ("orphaned_descendants", "exception_trial_count"),
                                ("supervisor_interrupted", "exception_trial_count")):
            with self.subTest(prior=prior):
                record = self.record(execution_status="cleanup_failed", cleanup_verified=False,
                                     outer_supervision={"execution_status": "cleanup_failed",
                                                        "execution_status_before_cleanup": prior})
                report = summarize_trials([record])
                self.assertEqual(report[category], 1)
                self.assertEqual(report["cleanup_failed_or_unverified_trial_count"], 1)
                self.assertEqual(report["budget_eligible_success_count"], 0)

    def test_no_worker_success_flag_substitutes_for_independent_validation(self):
        for validation in (None, {}, {"public_valid": False}, {"public_valid": 1}):
            with self.subTest(validation=validation):
                record = self.record(supervisor_public_validation=validation)
                self.assertFalse(qualify_trial(record)["qualified_success"])

    def test_success_requires_each_qualification_fact(self):
        for key in ("started", "public_valid_observed", "success_within_budget",
                    "cleanup_verified", "eligible_for_primary_comparison", "deadlines_satisfied"):
            with self.subTest(key=key):
                self.assertFalse(qualify_trial(self.record(**{key: False}))["qualified_success"])

    def test_not_started_plans_are_separate_and_costless(self):
        planned = self.record("next", started=False, attempts=[], total_wall_seconds=None)
        report = summarize_trials([self.record(), planned])
        self.assertEqual(report["started_trial_count"], 1)
        self.assertEqual(report["not_started_planned_trial_count"], 1)
        self.assertEqual(report["failed_trial_count"], 0)
        self.assertEqual(report["total_actual_wall_cost_seconds"], 4.5)

    def test_fixed_basis_and_standalone_never_share_cost_scope(self):
        fixed = self.record("fixed", cost_mode="fixed_basis", total_wall_seconds=0.5)
        report = summarize_trials([self.record(), fixed])
        self.assertEqual(report["cost_modes"], ["fixed_basis", "standalone_end_to_end"])
        self.assertEqual(len(report["groups"]), 2)
        self.assertNotEqual(report["groups"][0]["cost_scope"], report["groups"][1]["cost_scope"])
        self.assertIsNone(report["cost_per_budget_eligible_success_seconds"])
        self.assertEqual(report["cost_per_success_unavailable_reason"], "no_common_standalone_cost_scope")

    def test_unique_trials_reject_duplicate_input_and_do_not_mutate_records(self):
        record = self.record()
        before = deepcopy(record)
        summarize_trials([record])
        self.assertEqual(record, before)
        with self.assertRaises(ValueError):
            summarize_trials([record, deepcopy(record)])

    def test_invalid_or_missing_cost_labels_cannot_be_silently_inferred(self):
        for overrides in ({"cost_mode": None}, {"total_wall_seconds": -1},
                          {"total_wall_seconds": float("nan")}, {"started": 1},
                          {"started": False, "attempts": [{}]}, {"attempts": "one"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                summarize_trials([self.record(**overrides)])


if __name__ == "__main__":
    unittest.main()
