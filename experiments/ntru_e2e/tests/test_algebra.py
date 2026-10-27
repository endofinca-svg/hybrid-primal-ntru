"""Exact algebra checks; these tests contain no replacement decoder."""

import itertools
from copy import deepcopy
import random
import unittest

from experiments.ntru_e2e import algebra


def independent_product(a, b, q=None):
    """Ordinary polynomial convolution followed by x^n = -1 folding."""
    n = len(a)
    ordinary = [0] * (2 * n - 1)
    for degree in range(len(ordinary)):
        ordinary[degree] = sum(a[i] * b[degree - i]
                               for i in range(n) if 0 <= degree - i < n)
    for degree in range(2 * n - 2, n - 1, -1):
        ordinary[degree - n] -= ordinary[degree]
    result = ordinary[:n]
    return result if q is None else [value % q for value in result]


def determinant_bareiss(matrix):
    """Small exact determinant, independent of the basis construction."""
    work = [list(row) for row in matrix]
    n, sign, divisor = len(work), 1, 1
    for pivot_index in range(n - 1):
        if work[pivot_index][pivot_index] == 0:
            swap = next((row for row in range(pivot_index + 1, n)
                         if work[row][pivot_index]), None)
            if swap is None:
                return 0
            work[pivot_index], work[swap] = work[swap], work[pivot_index]
            sign = -sign
        pivot = work[pivot_index][pivot_index]
        for row in range(pivot_index + 1, n):
            for column in range(pivot_index + 1, n):
                numerator = (pivot * work[row][column]
                             - work[row][pivot_index] * work[pivot_index][column])
                if numerator % divisor:
                    raise AssertionError("Bareiss division must be exact")
                work[row][column] = numerator // divisor
            work[row][pivot_index] = 0
        divisor = pivot
    return sign * work[-1][-1]


def fixture():
    n, q = 8, 257
    f = [1, 0, 0, 0, 0, 0, -1, 0]
    g = [0, 1, 0, 0, -1, 1, 0, -1]
    h = independent_product(g, algebra.inverse_mod(f, q), q)
    public = {"schema": "ntru-e2e-public-v1", "instance_id": "algebra-fixture",
              "n": n, "q": q, "ring": "x^n+1", "h": h,
              "distribution": {"kind": "fixed_weight_ternary", "w_f": 2, "w_g": 4}}
    return public, f, g


class PolynomialTests(unittest.TestCase):
    def test_product_and_matrix_against_independent_convolution(self):
        rng = random.Random(20261021)
        for n in (1, 2, 4, 8):
            for _ in range(8):
                h = [rng.randrange(-300, 301) for _ in range(n)]
                f = [rng.randrange(-3, 4) for _ in range(n)]
                expected = independent_product(h, f)
                self.assertEqual(algebra.negacyclic_mul(h, f), expected)
                self.assertEqual(algebra.negacyclic_mul(h, f, 257),
                                 [value % 257 for value in expected])
                A = algebra.multiplication_matrix(h, 257)
                actual = [sum(row[j] * f[j] for j in range(n)) % 257 for row in A]
                self.assertEqual(actual, [value % 257 for value in expected])

    def test_signed_rotation_convention_and_commutation(self):
        a, b = [1, 2, 3, 4], [-1, 0, 2, -3]
        self.assertEqual(algebra.signed_rotate(a), [-4, 1, 2, 3])
        self.assertEqual(algebra.signed_rotate(a, -1), [2, 3, 4, -1])
        self.assertEqual(algebra.signed_rotate(a, 4), [-value for value in a])
        for shift in (-9, -4, -1, 0, 1, 4, 9):
            self.assertEqual(independent_product(a, algebra.signed_rotate(b, shift)),
                             algebra.signed_rotate(independent_product(a, b), shift))
            self.assertEqual(algebra.signed_rotate(algebra.signed_rotate(a, shift), -shift), a)

    def test_inverse_and_nonunit_rejection(self):
        for a, q in (([1, 1, 0, 0], 17), ([0, 0, -1, 0], 257), ([3], 17)):
            inverse = algebra.inverse_mod(a, q)
            self.assertEqual(independent_product(a, inverse, q), [1] + [0] * (len(a) - 1))
        for a in ([0, 0, 0, 0], [-2, 1, 0, 0]):
            with self.assertRaises(ValueError):
                algebra.inverse_mod(a, 17)


class LatticeWiringTests(unittest.TestCase):
    def test_partition_order_and_zero_length_tail(self):
        self.assertEqual(algebra.partition(8, [4, 1], 2),
                         {"P": [1, 4], "C": [0, 2, 3, 5], "T": [6, 7]})
        self.assertEqual(algebra.partition(4, [1], 0),
                         {"P": [1], "C": [0, 2, 3], "T": []})
        with self.assertRaises(ValueError):
            algebra.partition(4, [1, 1], 1)

    def test_row_basis_dimension_determinant_and_membership(self):
        n, q = 4, 17
        A = algebra.multiplication_matrix([2, 3, 5, 7], q)
        C = [0, 2]
        rows = algebra.basis_rows(A, q, C)
        self.assertEqual(len(rows), n + len(C))
        self.assertTrue(all(len(row) == n + len(C) for row in rows))
        self.assertEqual(determinant_bareiss(rows), q ** n)
        self.assertEqual(rows[n], [A[row][C[0]] for row in range(n)] + [1, 0])
        self.assertTrue(all(algebra.lattice_member(row, A, q, C) for row in rows))
        coefficients = [2, -3, 0, 1, -1, 2]
        v = [sum(coefficient * row[column] for coefficient, row in zip(coefficients, rows))
             for column in range(n + len(C))]
        self.assertTrue(algebra.lattice_member(v, A, q, C))
        v[0] += 1
        self.assertFalse(algebra.lattice_member(v, A, q, C))

    def test_target_sign_reconstruction_and_centered_representatives(self):
        public, f, g = fixture()
        n, q = public["n"], public["q"]
        A = algebra.multiplication_matrix(public["h"], q)
        coordinates = algebra.partition(n, [4, 1], 2)
        P, C, T = (coordinates[key] for key in ("P", "C", "T"))
        u = [f[index] for index in T]
        targets = [algebra.make_target(A, T, u, len(C), modulus) for modulus in (None, q)]
        for target in targets:
            v = [target[i] + g[i] for i in range(n)] + [f[index] for index in C]
            self.assertTrue(algebra.lattice_member(v, A, q, C))
            self.assertEqual(algebra.reconstruct(v, target, P, C, T, u, n), {"f": f, "g": g})
            rows = algebra.basis_rows(A, q, C)
            core = [f[index] for index in C]
            leading = [(v[i] - sum(A[i][index] * value for index, value in zip(C, core))) // q
                       for i in range(n)]
            coefficients = leading + core
            self.assertEqual([sum(coefficient * row[j] for coefficient, row in zip(coefficients, rows))
                              for j in range(n + len(C))], v)
            self.assertEqual(sum(value * value for value in g + core), 6 - sum(value != 0 for value in u))
        self.assertTrue(all(-(q // 2) <= value <= q // 2 for value in targets[1][:n]))
        self.assertTrue(all((left - right) % q == 0 for left, right in zip(*targets)))
        bad_target = [-value for value in targets[0][:n]] + [0] * len(C)
        bad_v = [bad_target[i] + g[i] for i in range(n)] + [f[index] for index in C]
        self.assertFalse(algebra.lattice_member(bad_v, A, q, C))

    def test_zero_target_demonstrates_trivial_zero_reconstruction(self):
        public, _, _ = fixture()
        A = algebra.multiplication_matrix(public["h"], public["q"])
        coordinates = algebra.partition(public["n"], [1, 4], 2)
        P, C, T = (coordinates[key] for key in ("P", "C", "T"))
        target = algebra.make_target(A, T, [0, 0], len(C), public["q"])
        self.assertEqual(target, [0] * (public["n"] + len(C)))
        pair = algebra.reconstruct(target, target, P, C, T, [0, 0], public["n"])
        self.assertFalse(algebra.validate_candidate(public, pair["f"], pair["g"])["valid"])


class PublicCandidateTests(unittest.TestCase):
    def test_json_booleans_are_not_integer_secret_coefficients_or_weights(self):
        public, f, g = fixture()
        for name, original in (("f", f), ("g", g)):
            changed = [True if value == 1 else value for value in original]
            candidate = {"f": f, "g": g, name: changed}
            with self.subTest(field=name):
                self.assertFalse(algebra.validate_candidate(public, **candidate)["valid"])
        # A boolean public weight also must not pass as integer weight one.
        tiny = deepcopy(public)
        tiny.update(n=1, h=[1])
        tiny["distribution"].update(w_f=True, w_g=1)
        self.assertFalse(algebra.validate_candidate(tiny, [1], [1])["valid"])
        with self.assertRaises(ValueError):
            algebra.partition(4, [True], 1)

    def test_tail_enumeration_covers_exact_feasible_nonzero_set(self):
        result = algebra.enumerate_tails(4, 2)
        expected = {tail for tail in itertools.product((-1, 0, 1), repeat=4)
                    if 1 <= sum(value != 0 for value in tail) <= 2}
        self.assertEqual(result["raw_nonzero"], 80)
        self.assertEqual(len(result["guesses"]), 32)
        self.assertEqual({tuple(tail) for tail in result["guesses"]}, expected)
        self.assertEqual(algebra.enumerate_tails(0, 2), {"raw_nonzero": 0, "guesses": []})
        self.assertEqual(algebra.enumerate_tails(4, 0)["guesses"], [])

    def test_valid_original_sign_and_signed_rotations(self):
        public, f, g = fixture()
        for shift in range(2 * public["n"]):
            candidate_f = algebra.signed_rotate(f, shift)
            candidate_g = algebra.signed_rotate(g, shift)
            result = algebra.validate_candidate(public, candidate_f, candidate_g)
            self.assertTrue(result["valid"], result)
            self.assertTrue(all(result["checks"].values()))
            self.assertEqual(independent_product(public["h"], candidate_f, public["q"]),
                             [value % public["q"] for value in candidate_g])

    def test_rejects_zero_wrong_weight_coefficients_and_public_relation(self):
        public, f, g = fixture()
        zero = algebra.validate_candidate(public, [0] * len(f), [0] * len(g))
        self.assertFalse(zero["valid"])
        self.assertFalse(zero["checks"]["nonzero"])
        self.assertFalse(zero["checks"]["invertible"])
        changed_g = list(g)
        changed_g[1] *= -1
        result = algebra.validate_candidate(public, f, changed_g)
        self.assertTrue(result["checks"]["weights"])
        self.assertFalse(result["checks"]["public_key_relation"])
        doubled = algebra.validate_candidate(public, [2 * value for value in f], [2 * value for value in g])
        self.assertTrue(doubled["checks"]["public_key_relation"])
        self.assertFalse(doubled["checks"]["ternary"])
        wrong_weight = list(f)
        wrong_weight[2] = 1
        result = algebra.validate_candidate(public, wrong_weight, g)
        self.assertFalse(result["checks"]["weights"])
        self.assertFalse(algebra.validate_candidate(public, f[:-1], g)["valid"])


if __name__ == "__main__":
    unittest.main()
