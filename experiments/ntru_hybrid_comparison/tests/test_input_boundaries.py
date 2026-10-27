"""Public-package boundary regressions using synthetic records and mock GSO.

No fixture in this module is a measured reduction, decoder output, or actual
experiment artifact.  The full-journal fixture exercises validation only and
is never saved as a successful artifact.
"""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison import inputs
from experiments.ntru_hybrid_comparison.reduction import protocol_from_manifest
from experiments.ntru_hybrid_comparison.tests.test_inputs import make_bundle
from experiments.ntru_hybrid_comparison.tests.test_reduction import completed_fixture


def exact_synthetic_execution(bundle):
    """An identity integer certificate; explicitly zero measured backend work."""
    rows = deepcopy(bundle["basis"]["original"])
    dimension = len(rows)
    identity = [[int(i == j) for j in range(dimension)] for i in range(dimension)]
    return {
        "original_basis": rows,
        "reduced_basis": deepcopy(rows),
        "transform": identity,
        "protocol": deepcopy(bundle["basis"]["protocol"]),
        "record": {
            "status": "completed", "elapsed_seconds": 0.0,
            "cumulative_seconds": 0.0, "stages": [],
            "timing_source": "synthetic_test_fixture_not_a_backend_measurement",
        },
    }


def full_journal_validation_fixture(bundle):
    """Simulate executor shape only; never call/import a real backend.

    The fixture is confined to test_fixture provenance, and no successful
    serialization of this journal is produced by these tests.
    """
    execution = exact_synthetic_execution(bundle)
    protocol = protocol_from_manifest(execution["protocol"])
    record = completed_fixture(protocol)
    record.update(started_utc="2000-01-01T00:00:00+00:00",
                  finished_utc="2000-01-01T00:00:12+00:00")
    sources = ("fpylll.algorithms.bkz", "fpylll.algorithms.bkz2",
               "fpylll.fplll.lll", "fpylll.fplll.gso")
    record["backend"] = {
        "python": "synthetic-fixture", "python_implementation": "synthetic-fixture",
        "fpylll_version": "synthetic-fixture", "fplll_version": "synthetic-fixture",
        "available_float_types": ["mpfr"],
        "strategy": {"kind": protocol.strategy, "path": "synthetic-not-a-file",
                     "sha256": "0" * 64, "byte_count": 0, "loaded_max_beta": 100,
                     "above_max": "synthetic-fixture"},
        "source_hashes": {name: {"path": "synthetic-not-a-file", "sha256": "0" * 64}
                          for name in sources},
        "precision_before_execution": 192, "threads_before_execution": 1,
        "backend_probe_did_reduce": False,
    }
    record.update(original_basis_sha256=inputs.content_sha256(execution["original_basis"]),
                  reduced_basis_sha256=inputs.content_sha256(execution["reduced_basis"]),
                  transform_sha256=inputs.content_sha256(execution["transform"]))
    execution["record"] = record
    return execution


class PublicInputBoundaryTests(unittest.TestCase):
    def test_exact_synthetic_record_cannot_be_relabelled_as_public_build(self):
        bundle = make_bundle()
        execution = exact_synthetic_execution(bundle)
        attached = inputs.attach_reduction(bundle, execution)
        self.assertEqual(attached["provenance"]["kind"], "test_fixture")
        self.assertEqual(attached["basis"]["status"], "reduced_verified")

        # Neither relabelling before attaching nor after attaching can publish
        # an algebra-only certificate as a measured public reduction product.
        public_bundle = deepcopy(bundle)
        public_bundle["provenance"]["kind"] = "public_input_build_only"
        with self.assertRaises(ValueError):
            inputs.attach_reduction(public_bundle, execution)
        attached["provenance"]["kind"] = "public_input_build_only"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "must-not-be-published.json"
            with self.assertRaises(ValueError):
                inputs.save_input_bundle(path, attached)
            self.assertFalse(path.exists())

    def test_reference_and_unknown_record_fields_are_rejected(self):
        bundle = make_bundle()
        for fixture in (exact_synthetic_execution, full_journal_validation_fixture):
            baseline = fixture(bundle)
            # Establish that later failures are due to inserted metadata,
            # rather than an already invalid synthetic journal.
            inputs.attach_reduction(bundle, baseline)
            for key in ("reference_f", "unknown_fields"):
                with self.subTest(fixture=fixture.__name__, key=key):
                    changed = deepcopy(baseline)
                    changed["record"][key] = [1, 0, -1]
                    with self.assertRaises(ValueError):
                        inputs.attach_reduction(bundle, changed)
            if fixture is full_journal_validation_fixture:
                changed = deepcopy(baseline)
                changed["record"]["backend"]["source_hashes"]["fpylll.fplll.gso"]["reference_f"] = [1]
                with self.assertRaises(ValueError):
                    inputs.attach_reduction(bundle, changed)

    def test_mock_gso_precision_change_is_rejected_before_global_mutation(self):
        class FakeFPLLL:
            precision = 53
            set_calls = []

            @classmethod
            def set_precision(cls, precision):
                cls.set_calls.append(precision)
                cls.precision = precision

            @classmethod
            def get_precision(cls):
                return cls.precision

        class FakeIntegerMatrix:
            @classmethod
            def from_matrix(cls, rows, *, int_type):
                self.assertEqual(int_type, "mpz")
                return deepcopy(rows)

        class FakeGSO:
            class Mat:
                def __init__(self, matrix, *, float_type):
                    self.B = matrix
                    self.float_type = float_type
                    self.update_count = 0

                def update_gso(self):
                    self.update_count += 1

        fake_backend = ModuleType("fpylll")
        fake_backend.FPLLL = FakeFPLLL
        fake_backend.IntegerMatrix = FakeIntegerMatrix
        fake_backend.GSO = FakeGSO
        bundle = make_bundle()
        with patch.dict(sys.modules, {"fpylll": fake_backend}), patch.object(
                inputs, "_decoder_mpfr_precision", None):
            first = inputs.create_working_copy(bundle, use_reduced=False,
                                               float_type="mpfr", precision_bits=192)
            second = inputs.create_working_copy(bundle, use_reduced=False,
                                                float_type="mpfr", precision_bits=192)
            self.assertIsNot(first["gso"], second["gso"])
            first["gso"].B[0][0] += 1
            self.assertEqual(second["gso"].B, bundle["basis"]["original"])
            self.assertEqual(FakeFPLLL.set_calls, [192, 192])
            with self.assertRaisesRegex(ValueError, "share MPFR precision"):
                inputs.create_working_copy(bundle, use_reduced=False,
                                           float_type="mpfr", precision_bits=256)
            self.assertEqual(FakeFPLLL.set_calls, [192, 192])
            self.assertEqual(FakeFPLLL.get_precision(), 192)
            self.assertEqual(inputs._decoder_mpfr_precision, 192)

    def test_protocol_actual_counts_and_matrix_binding_rejected_at_package_boundary(self):
        bundle = make_bundle()
        execution = full_journal_validation_fixture(bundle)
        accepted_fixture = inputs.attach_reduction(bundle, execution)
        self.assertTrue(inputs.validate_input_bundle(accepted_fixture))
        mutations = {
            "precision": lambda r: r["actual"].update(precision_bits=53),
            "lll_completion_count": lambda r: r["actual"].update(completed_lll_calls=0),
            "tour_completion_count": lambda r: r["actual"].update(completed_tours=1),
            "requested_protocol": lambda r: r["requested"]["protocol"].update(seed=987654),
            "original_matrix_binding": lambda r: r.update(original_basis_sha256="0" * 64),
            "reduced_matrix_binding": lambda r: r.update(reduced_basis_sha256="0" * 64),
            "transform_binding": lambda r: r.update(transform_sha256="0" * 64),
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, mutate in mutations.items():
                with self.subTest(mutation=name):
                    changed_execution = deepcopy(execution)
                    mutate(changed_execution["record"])
                    with self.assertRaises(ValueError):
                        inputs.attach_reduction(bundle, changed_execution)
                    changed_bundle = deepcopy(accepted_fixture)
                    mutate(changed_bundle["basis"]["execution"])
                    path = Path(directory) / (name + ".json")
                    with self.assertRaises(ValueError):
                        inputs.save_input_bundle(path, changed_bundle)
                    self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
