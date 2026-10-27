"""Small exact reconstruction checks, without invoking a lattice solver.

The oracle uses ordinary polynomial convolution and then folds x^n to -1.
These are algebra reconstruction tests, not solver recovery experiments.
"""

import unittest

from experiments.ntru_e2e import algebra
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate


def convolution_then_fold(left, right, modulus=None):
    """Independent schoolbook polynomial multiplication followed by reduction."""
    n = len(left)
    ordinary = [0] * (2 * n - 1)
    for degree in range(len(ordinary)):
        ordinary[degree] = sum(
            left[index] * right[degree - index]
            for index in range(n)
            if 0 <= degree - index < n
        )
    for degree in range(2 * n - 2, n - 1, -1):
        ordinary[degree - n] -= ordinary[degree]
    folded = ordinary[:n]
    return folded if modulus is None else [value % modulus for value in folded]


def manual_fixture():
    """h=x, so g=x*f; no key generation or secret-dependent selection occurs."""
    f = [1, 0, 1, 0, 0, -1, 0, -1]
    g = [1, 1, 0, 1, 0, 0, -1, 0]
    public = {
        "schema": "ntru-e2e-public-v1",
        "instance_id": "phase2a-manual-reconstruction-only",
        "n": 8,
        "q": 17,
        "ring": "x^n+1",
        "h": [0, 1, 0, 0, 0, 0, 0, 0],
        "distribution": {"kind": "fixed_weight_ternary", "w_f": 4, "w_g": 4},
    }
    return public, f, g


def oracle_member(vector, h, q, core_positions):
    """Membership from polynomial congruence, without using matrix A."""
    n = len(h)
    if len(vector) != n + len(core_positions):
        return False
    core_polynomial = [0] * n
    for position, coefficient in zip(core_positions, vector[n:]):
        core_polynomial[position] = coefficient
    product = convolution_then_fold(h, core_polynomial, q)
    return product == [coefficient % q for coefficient in vector[:n]]


class ReconstructionAlgebraTests(unittest.TestCase):
    def test_column_multiplication_and_row_basis_use_original_coordinate_order(self):
        public, f, g = manual_fixture()
        n, q, h = public["n"], public["q"], public["h"]
        A = algebra.multiplication_matrix(h, q)
        self.assertEqual(convolution_then_fold(h, f), g)
        self.assertEqual(
            [sum(A[row][column] * f[column] for column in range(n)) % q
             for row in range(n)],
            [value % q for value in g],
        )
        # This h gives an asymmetric matrix: an accidental transpose is visible.
        self.assertNotEqual(A, [list(column) for column in zip(*A)])
        C = [6, 2, 5, 3]
        rows = algebra.basis_rows(A, q, C)
        self.assertEqual(len(rows), 2 * n - 2 - 2)
        self.assertTrue(all(len(row) == len(rows) for row in rows))
        for row_index, row in enumerate(rows):
            with self.subTest(row=row_index):
                self.assertTrue(oracle_member(row, h, q, C))
                self.assertTrue(algebra.lattice_member(row, A, q, C))
        for core_index, original_position in enumerate(C):
            monomial = [0] * n
            monomial[original_position] = 1
            self.assertEqual(
                rows[n + core_index],
                convolution_then_fold(h, monomial, q)
                + [int(index == core_index) for index in range(len(C))],
            )

    def test_actual_target_reconstruction_and_explicit_lattice_combination(self):
        public, f, g = manual_fixture()
        n, q, h = public["n"], public["q"], public["h"]
        # Neither the pattern nor the two ordered surviving blocks are intervals.
        P, C, T = [1, 4], [6, 2, 5, 3], [7, 0]
        u = [-1, 1]
        A = algebra.multiplication_matrix(h, q)
        rows = algebra.basis_rows(A, q, C)
        core = [0, 1, -1, 0]
        raw = algebra.make_target(A, T, u, len(C))
        centered = algebra.make_target(A, T, u, len(C), q)
        self.assertEqual(raw, [16, -1] + [0] * 10)
        self.assertEqual(centered, [-1, -1] + [0] * 10)
        self.assertEqual([a - b for a, b in zip(raw, centered)], [17] + [0] * 11)
        tail_polynomial = [1, 0, 0, 0, 0, 0, 0, -1]
        tail_product = convolution_then_fold(h, tail_polynomial)

        for name, target, leading in (
            ("raw", raw, [1] + [0] * 7),
            ("centered", centered, [0] * 8),
        ):
            with self.subTest(target_representative=name):
                self.assertEqual(
                    [(target[index] + tail_product[index]) % q for index in range(n)],
                    [0] * n,
                )
                # Build v from explicit integer row coefficients, not by asserting
                # v=target+error alone. This checks row basis orientation as well.
                coefficients = leading + core
                vector = [
                    sum(coefficient * row[column]
                        for coefficient, row in zip(coefficients, rows))
                    for column in range(len(rows))
                ]
                self.assertEqual(vector, [target[index] + g[index] for index in range(n)] + core)
                self.assertTrue(oracle_member(vector, h, q, C))
                self.assertTrue(algebra.lattice_member(vector, A, q, C))
                error = [value - origin for value, origin in zip(vector, target)]
                self.assertEqual(error, g + core)
                self.assertEqual(
                    algebra.reconstruct(vector, target, P, C, T, u, n),
                    {"f": f, "g": g},
                )
                self.assertEqual(sum(value * value for value in error), 8 - 2)

        valid = validate_public_candidate(public, f, g)
        self.assertTrue(valid["public_valid"], valid)
        # A centered vector with its raw target is still a lattice vector, but
        # reconstructs the wrong integer g. Actual representatives must be kept.
        centered_vector = [centered[index] + g[index] for index in range(n)] + core
        wrong = algebra.reconstruct(centered_vector, raw, P, C, T, u, n)
        self.assertNotEqual(wrong["g"], g)
        self.assertFalse(validate_public_candidate(public, **wrong)["public_valid"])

    def test_wrong_target_sign_and_tail_order_are_detected_independently(self):
        public, f, g = manual_fixture()
        n, q, h = public["n"], public["q"], public["h"]
        P, C, T, u = [1, 4], [6, 2, 5, 3], [7, 0], [-1, 1]
        A = algebra.multiplication_matrix(h, q)
        target = algebra.make_target(A, T, u, len(C), q)
        core = [f[position] for position in C]
        flipped = [-value for value in target]
        wrong_vector = [flipped[index] + g[index] for index in range(n)] + core
        self.assertFalse(oracle_member(wrong_vector, h, q, C))
        self.assertFalse(algebra.lattice_member(wrong_vector, A, q, C))
        vector = [target[index] + g[index] for index in range(n)] + core
        wrong_tail = algebra.reconstruct(vector, target, P, C, list(reversed(T)), u, n)
        self.assertNotEqual(wrong_tail["f"], f)
        self.assertNotEqual(
            convolution_then_fold(h, wrong_tail["f"], q),
            [value % q for value in wrong_tail["g"]],
        )

    def test_zero_target_zero_vector_does_not_reconstruct_a_nonzero_secret(self):
        public, _, _ = manual_fixture()
        n, q = public["n"], public["q"]
        P, C, T = [1, 4], [6, 2, 5, 3], [7, 0]
        A = algebra.multiplication_matrix(public["h"], q)
        target = algebra.make_target(A, T, [0, 0], len(C), q)
        self.assertEqual(target, [0] * (n + len(C)))
        self.assertTrue(oracle_member(target, public["h"], q, C))
        pair = algebra.reconstruct(target, target, P, C, T, [0, 0], n)
        self.assertEqual(pair, {"f": [0] * n, "g": [0] * n})
        self.assertFalse(validate_public_candidate(public, **pair)["public_valid"])


if __name__ == "__main__":
    unittest.main()
