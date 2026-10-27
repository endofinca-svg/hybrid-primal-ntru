"""Budget boundaries and shifted-secret regressions for the capped search."""

from __future__ import annotations

import importlib
import unittest

from experiments.ntru_e2e.algebra import signed_rotate
from experiments.ntru_e2e.generate import generate_instance

from .solve import scan_rows


try:
    search = importlib.import_module(__package__ + ".budgeted_search")
except ModuleNotFoundError as error:
    if error.name != __package__ + ".budgeted_search":
        raise
    search = None


@unittest.skipIf(search is None, "The budgeted search runner has not been created yet")
class AttemptAllowanceTests(unittest.TestCase):
    def allowance(self, remaining: float, per_pattern: float = 473.11) -> float:
        return search.attempt_allowance(
            {"group_budget_seconds": 10000.0,
             "charged_wall_seconds": 10000.0 - remaining},
            {"per_pattern_seconds": per_pattern},
        )

    def test_per_pattern_cap_reserves_cleanup(self) -> None:
        self.assertAlmostEqual(self.allowance(9000.0), 473.11 - search.CLEANUP_RESERVE)

    def test_final_attempt_is_clipped_to_remaining_group_budget(self) -> None:
        self.assertAlmostEqual(self.allowance(100.0), 100.0 - search.CLEANUP_RESERVE)

    def test_fractional_final_budget_remains_usable(self) -> None:
        self.assertAlmostEqual(self.allowance(search.CLEANUP_RESERVE + 0.25), 0.25)

    def test_exhausted_budget_never_launches_another_worker(self) -> None:
        for remaining in (search.CLEANUP_RESERVE, 1.0, 0.0, -1.0):
            with self.subTest(remaining=remaining):
                self.assertAlmostEqual(self.allowance(remaining), 0.0)

    def test_requested_values_and_exact_decimal_budget_unit(self) -> None:
        self.assertEqual(search.K_VALUES, [12, 24, 32, 38, 40, 42, 44, 48, 52, 56])
        self.assertEqual(search.UNIT_SECONDS, 473.11)


class ShiftedSecretTests(unittest.TestCase):
    def test_scan_accepts_simultaneous_negacyclic_wraparound_and_both_signs(self) -> None:
        public, truth = generate_instance(8, 257, 2, 4, 1, "shifted-budget-regression")
        n, shift = public["n"], public["n"] - 1
        self.assertTrue(any(truth["f"][i] for i in range(1, n)))
        for sign in (1, -1):
            with self.subTest(sign=sign):
                f = [sign * value for value in signed_rotate(truth["f"], shift)]
                g = [sign * value for value in signed_rotate(truth["g"], shift)]
                self.assertNotEqual(f, truth["f"])
                # Every original coefficient except coordinate zero wraps and
                # changes sign because the existing ring is x^n + 1.
                for i in range(1, n):
                    self.assertEqual(f[i - 1], -sign * truth["f"][i])
                self.assertEqual(f[-1], sign * truth["f"][0])
                pattern = [i for i, value in enumerate(f) if not value][:3]
                core = [i for i in range(n) if i not in pattern]
                vector = g + [f[i] for i in core]
                found, minimum, rejected = scan_rows([vector], public, core, pattern)
                self.assertIsNotNone(found, rejected)
                self.assertEqual(found["f"], f)
                self.assertEqual(found["g"], g)
                self.assertEqual(found["vector"], vector)
                self.assertTrue(found["public_validation"]["valid"])
                self.assertEqual(minimum, 6)


if __name__ == "__main__":
    unittest.main()
