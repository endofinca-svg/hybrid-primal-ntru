"""Targeted independent-validator checks on existing small algebra fixtures."""
import copy
import time
import unittest

from . import test_witness
from .validate import check_deadline, no_entry_terminal_check, stage_intervals, verify_entry

witness = test_witness.witness


class ValidatorTests(unittest.TestCase):
    def telemetry(self):
        bundle, f, g = test_witness.WitnessTests().fixture()
        representative = witness.check_representative(bundle, f, g, 0)
        norms = representative["norm_record"]
        return bundle, {"backend_integer_id": representative["external_id_mapping"]["backend_integer_id"],
                        "tail": representative["tail"], "full_pair": norms["pair"],
                        "ambient_residual_sq": norms["ambient_residual_norm_sq"],
                        "tail_norm_sq": norms["tail_norm_sq"], "full_norm_sq": norms["full_pair_norm_sq"],
                        "radius_pass": True, "public_validation": {"public_valid": True}}

    def test_fresh_validation_reconstructs_norm_and_public_relation(self):
        bundle, row = self.telemetry()
        self.assertTrue(verify_entry(row, bundle, bundle["public_instance"])["public_valid"])

    def test_claimed_validation_cannot_override_actual_public_vector(self):
        bundle, row = self.telemetry()
        row["public_validation"]["public_valid"] = False
        with self.assertRaisesRegex(ValueError, "independent validation"):
            verify_entry(row, bundle, bundle["public_instance"])

    def test_full_norm_and_tail_identity_are_recomputed(self):
        bundle, row = self.telemetry()
        for field in ("ambient_residual_sq", "tail_norm_sq", "full_norm_sq"):
            bad = copy.deepcopy(row)
            bad[field] += 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "norm mismatch"):
                verify_entry(bad, bundle, bundle["public_instance"])
        row["tail"] = [1, -1]
        with self.assertRaisesRegex(ValueError, "external target ID"):
            verify_entry(row, bundle, bundle["public_instance"])

    def test_timeout_phase_has_null_exact_time(self):
        rows = [{"event": "stage_started", "stage": "reduction", "monotonic": 10.0}]
        intervals = stage_intervals(rows, 12.5)
        self.assertFalse(intervals[0]["complete"])
        self.assertIsNone(intervals[0]["observed_wall_seconds"])
        self.assertIsNone(intervals[0]["exact_uncompleted_phase_seconds"])
        self.assertEqual(intervals[0]["observed_censored_upper_seconds"], 2.5)

    def test_expired_deadline_prevents_more_validation(self):
        with self.assertRaises(TimeoutError):
            check_deadline(time.monotonic() - 1)

    def test_no_entry_completion_needs_terminal_and_empty_saved_group(self):
        result = {"target_summaries": [{"backend_integer_id": 2, "terminal": "completed_no_backend_entry",
                                      "post_lift_count": 0, "original_backend_completed": False}],
                  "projection_groups": [{"internal_id": 1, "members": [{"backend_integer_id": 2}]}],
                  "missing_external_results": [2], "post_groups_with_no_entries": [1]}
        self.assertEqual(no_entry_terminal_check(2, result, {"entries": [{"group_id": 0}]}), 1)
        with self.assertRaisesRegex(ValueError, "contradicted"):
            no_entry_terminal_check(2, result, {"entries": [{"group_id": 1}]})
        result["target_summaries"][0]["post_lift_count"] = 1
        with self.assertRaisesRegex(ValueError, "terminal evidence"):
            no_entry_terminal_check(2, result, {"entries": []})


if __name__ == "__main__":
    unittest.main()
