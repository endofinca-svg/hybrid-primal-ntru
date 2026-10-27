"""Pure protocol tests; real backend acceptance is a separate bounded script."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison.fixed_backend import (
    FixedBabaiBackend, FixedSlicerBackend, RecordingMapper, SlicerConfig,
    _without_constructor_lll,
)


class FakeGSO:
    float_type = "long double"

    def __init__(self, rows):
        self.B = deepcopy(rows)
        self.calls = []

    def babai(self, target):
        self.calls.append(list(target))
        if target[0] == 99:
            raise ArithmeticError("deliberate mock failure")
        return [round(value / self.B[i][i]) for i, value in enumerate(target)]


def working(d=2, scale=2):
    rows = [[scale if i == j else 0 for j in range(d)] for i in range(d)]
    return {"rows": rows, "gso": FakeGSO(rows),
            "gso_metadata": {"float_type": "long double", "precision_bits": None}}


class FakeSlicer:
    reject = False
    bad_internal_id = False
    calls = 0
    missing_residuals = False
    repeat_residuals = False
    kernel_raises = False

    def __init__(self, sieve, seed):
        self.size = 0
        self.iterations = 0

    @property
    def stats(self):
        return {"cdb_t-size:": self.size, "itercount_slicer": self.iterations}

    def grow_db_with_target(self, target, n_per_target):
        if not self.reject:
            self.size += n_per_target

    def set_nthreads(self, value):
        pass

    def set_max_slicer_interations(self, value):
        pass

    def set_Nt(self, value):
        pass

    def set_saturation_scalar(self, value):
        pass

    def set_proj_error_bound(self, value):
        pass

    def bdgl_like_sieve(self, *args):
        type(self).calls += 1
        if self.kernel_raises:
            raise RuntimeError("deliberate mock kernel exception")
        self.iterations += 1

    def itervalues_cdb_t(self, return_with_index=True):
        if not self.missing_residuals:
            yield ([0.0] * 28, 100 if self.bad_internal_id else 0)
            if self.repeat_residuals:
                yield ([0.0] * 28, 0)


class FakeMapper:
    dim = 28

    def __init__(self):
        self.gh_sub = 1.0
        self.lifts = []
        self.error_target = None

    def _validate_batch(self, targets, bounds, ids):
        return deepcopy(targets), list(bounds), list(ids)

    def _groups(self, targets, bounds, ids):
        self.projection_conversions_by_id = {
            external_id: {"kind": "mock-only-conversion", "original_target_used": list(target)}
            for external_id, target in zip(ids, targets)
        }
        return [{"projection": [1.0] + [0.0] * 27,
                 "members": [{"target": target, "bound_sq": bound,
                              "target_id": external_id, "sign": 1}
                             for target, bound, external_id in zip(targets, bounds, ids)]}]

    def _lift(self, target, residual):
        self.lifts.append(list(target))
        if target[0] == self.error_target:
            raise ArithmeticError("deliberate per-member lift error")
        vector = list(target)
        vector[0] += 1
        return ([value // 2 for value in vector], vector, 1)


def fake_slicer_backend():
    backend = FixedSlicerBackend(working(28), SlicerConfig())
    backend.prepared = True
    backend.metrics["preprocess_count"] = 1
    backend._slicer_factory = FakeSlicer
    backend._mapper = FakeMapper()
    backend.siever = []
    FakeSlicer.reject = FakeSlicer.bad_internal_id = False
    FakeSlicer.missing_residuals = FakeSlicer.repeat_residuals = False
    FakeSlicer.kernel_raises = False
    FakeSlicer.calls = 0
    return backend


class FixedBackendTests(unittest.TestCase):
    def test_babai_coefficients_are_converted_to_ambient_vector(self):
        backend = FixedBabaiBackend(working())
        self.assertEqual(backend.preprocess()["preprocess_count"], 0)
        result = backend.decode([[5, 0], [7, 0]], [4, 4], [17, 3])
        self.assertEqual(result["completed_ids"], [17, 3])
        self.assertEqual([row["backend_integer_id"] for row in result["candidates"]], [17, 3])
        self.assertEqual(result["candidates"][0]["coefficients"], [2, 0])
        self.assertEqual(result["candidates"][0]["v"], [4, 0])
        self.assertEqual(result["candidates"][0]["output_origin"], "babai_only")
        self.assertEqual(result["call_evidence"][0]["function"], "GSO.Mat.babai")
        self.assertTrue(result["call_evidence"][0]["returned_normally"])

    def test_babai_no_candidate_is_completed_and_exception_is_error(self):
        backend = FixedBabaiBackend(working())
        backend.preprocess()
        result = backend.decode([[5, 0], [99, 0]], [0, 0], [2, 8])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["completed_ids"], [2])
        self.assertEqual(result["errored_ids"], [8])
        self.assertEqual(result["cancelled_ids"], [])
        self.assertEqual(result["metrics"]["babai_decode_target_count"], 2)
        self.assertEqual(result["metrics"]["babai_decode_completed_target_count"], 1)
        self.assertFalse(result["call_evidence"][1]["returned_normally"])
        self.assertIn("deliberate mock failure", result["call_evidence"][1]["error"])

    def test_babai_reuses_gso_and_rejects_repeated_preprocessing(self):
        source = working()
        backend = FixedBabaiBackend(source)
        backend.preprocess()
        backend.decode([[2, 0]], [1], [7])
        backend.decode([[4, 0]], [1], [8])
        self.assertIs(backend.gso, source["gso"])
        self.assertEqual(source["gso"].calls, [[2, 0], [4, 0]])
        with self.assertRaises(RuntimeError):
            backend.preprocess()

    def test_babai_detects_basis_mutation(self):
        backend = FixedBabaiBackend(working())
        backend.preprocess()
        backend.gso.B[0][0] += 1
        with self.assertRaisesRegex(RuntimeError, "changed"):
            backend.decode([[2, 0]], [1], [0])

    def test_babai_dimension_and_id_validation(self):
        backend = FixedBabaiBackend(working())
        backend.preprocess()
        for targets, ids in (([[1]], [0]), ([[1, 0]], [True]),
                             ([[1, 0], [2, 0]], [0, 0])):
            with self.assertRaises((ValueError, TypeError)):
                backend.decode(targets, [1] * len(targets), ids)

    def test_injected_long_double_cannot_silently_satisfy_requested_mpfr(self):
        with self.assertRaisesRegex(ValueError, "float type differs"):
            FixedBabaiBackend(working(), {"float_type": "mpfr", "precision_bits": 192})

    def test_injected_mpfr_requires_matching_metadata_process_guard_and_precision(self):
        from experiments.ntru_hybrid_comparison import inputs

        source = working()
        source["gso"].float_type = "mpfr"
        source["gso_metadata"].update(float_type="mpfr", precision_bits=192)
        fake_fpylll = SimpleNamespace(FPLLL=SimpleNamespace(get_precision=lambda: 192))
        with patch.dict("sys.modules", {"fpylll": fake_fpylll}):
            with patch.object(inputs, "_decoder_mpfr_precision", None):
                with self.assertRaisesRegex(ValueError, "process decoder guard"):
                    FixedBabaiBackend(source, {"float_type": "mpfr", "precision_bits": 192})
            with patch.object(inputs, "_decoder_mpfr_precision", 192):
                backend = FixedBabaiBackend(source, {"float_type": "mpfr", "precision_bits": 192})
                self.assertIs(backend.gso, source["gso"])
                with patch.object(fake_fpylll.FPLLL, "get_precision", return_value=256):
                    with self.assertRaisesRegex(RuntimeError, "current process precision"):
                        FixedBabaiBackend(source, {"float_type": "mpfr", "precision_bits": 192})
        source["gso_metadata"]["precision_bits"] = 256
        with self.assertRaisesRegex(ValueError, "precision metadata"):
            FixedBabaiBackend(source, {"float_type": "mpfr", "precision_bits": 192})

    def test_pinned_constructor_lll_call_is_suppressed_and_later_forbidden(self):
        class MockSiever:
            original_lll_calls = 0

            def __init__(self, *args, **kwargs):
                self.lll(0, 28)

            def lll(self, *args):
                type(self).original_lll_calls += 1

        instance = _without_constructor_lll(MockSiever)(None)
        self.assertEqual(MockSiever.original_lll_calls, 0)
        self.assertEqual(instance.constructor_lll_suppressed,
                         [{"left": 0, "right": 28, "action": "suppressed_without_calling_LLL"}])
        with self.assertRaises(RuntimeError):
            instance.lll(0, 28)
        with self.assertRaises(RuntimeError):
            instance.split_lll(0, 0, 28)

    def test_unexpected_constructor_without_intercept_is_rejected(self):
        class ChangedSiever:
            def __init__(self, *args, **kwargs):
                pass

        with self.assertRaisesRegex(RuntimeError, "interception changed"):
            _without_constructor_lll(ChangedSiever)(None)

    def test_small_dim_rejected_without_real_backend_import(self):
        with self.assertRaises(ValueError):
            FixedSlicerBackend(working(6))

    def test_failed_slicer_preprocess_attempt_is_not_hidden_by_zero_completions(self):
        backend = FixedSlicerBackend(working(28))
        backend.gso.B[0][0] += 1
        with self.assertRaisesRegex(RuntimeError, "changed"):
            backend.preprocess()
        self.assertEqual(backend.metrics["preprocess_attempt_count"], 1)
        self.assertEqual(backend.metrics["preprocess_count"], 0)

    def test_recording_mapper_preserves_each_exact_nonzero_lattice_shift(self):
        class DiagonalGSO:
            def from_canonical(self, vector):
                return [value / 2 for value in vector]

            def babai(self, coordinates, *, start, gso):
                self.assertions = (start, gso)
                return [round(value) for value in coordinates]

        mapper = RecordingMapper.__new__(RecordingMapper)
        mapper.dim, mapper.sieve_dim = 30, 28
        mapper.G = DiagonalGSO()
        mapper.rows = [[2 if i == j else 0 for j in range(30)] for i in range(30)]
        mapper._sqrt_r_over_gh = [1.0] * 28
        first, second = [0] * 30, [0] * 30
        first[0] = second[0] = 3  # prefix is outside the projected tail
        first[3], second[3] = 5, 7
        before = deepcopy([first, second])
        groups = mapper._groups([first, second], [4, 4], [17, 3])
        self.assertEqual([first, second], before)
        self.assertEqual(len(groups), 1)
        self.assertEqual(mapper.G.assertions, (2, True))
        for external_id, target, coefficient, shift, sign in (
                (17, first, 2, 4, 1), (3, second, 4, 8, -1)):
            conversion = mapper.projection_conversions_by_id[external_id]
            self.assertEqual(conversion["original_target_used"], target)
            self.assertEqual(conversion["full_basis_coefficients"][3], coefficient)
            self.assertEqual(conversion["ambient_shift"][3], shift)
            self.assertEqual(conversion["projected_input"][3], target[3] - shift)
            self.assertEqual(conversion["projected_input"][0], 3)
            self.assertEqual(conversion["canonical_sign"], sign)
            self.assertEqual(conversion["signed_canonical_projection_float32"][1], .5)
            self.assertFalse(conversion["basis_transform"])
            self.assertFalse(conversion["input_package_target_mutated"])
            self.assertEqual(conversion["final_lift_target"], "original_target_used")
        self.assertEqual([member["target"] for member in groups[0]["members"]], before)

    def test_unsafe_db_and_unlabelled_forced_mode_rejected(self):
        for kwargs in ({"db_size": 8}, {"force_kernel_iterations": True},
                       {"preprocessing": "sample_database_only"}, {"blocks": 0},
                       {"nrand": True}, {"buckets": 0}):
            with self.assertRaises(ValueError):
                SlicerConfig(**kwargs)

    def test_projection_group_members_lift_their_own_targets(self):
        backend = fake_slicer_backend()
        targets = [[5] + [0] * 27, [7] + [0] * 27]
        result = backend.decode(targets, [4, 4], [19, 2])
        self.assertEqual(result["completed_ids"], [19, 2])
        self.assertEqual(backend._mapper.lifts, targets)
        self.assertEqual([row["v"][0] for row in result["candidates"]], [6, 8])
        self.assertEqual(len(result["projection_groups"]), 1)
        self.assertEqual(result["projection_groups"][0]["members"][1]["projection_conversion"],
                         {"kind": "mock-only-conversion", "original_target_used": targets[1]})
        self.assertEqual(result["metrics"]["slicer_target_count"], 2)
        self.assertEqual(result["metrics"]["slicer_kernel_calls"], 1)
        self.assertEqual(result["metrics"]["babai_precheck_target_count"], 0)
        self.assertEqual(FakeSlicer.calls, 1)
        self.assertTrue(result["call_evidence"][0]["returned_normally"])
        self.assertEqual(result["call_evidence"][0]["observable_iterations"], 1)
        self.assertEqual(result["call_evidence"][0]["target_db_entries_per_accepted_projection"], 2)
        self.assertEqual(result["call_evidence"][0]["randomized_entries_per_accepted_projection"], 1)
        self.assertEqual(result["call_evidence"][0]["randomization_draw_attempts"], "not_observable")
        for candidate in result["candidates"]:
            self.assertEqual(candidate["output_origin"], "slicer_tail_with_babai_lift")
            self.assertEqual(candidate["slicer_used_in_returned_candidate"], "unknown")

    def test_normal_slicer_no_radius_candidate_is_still_completed(self):
        backend = fake_slicer_backend()
        result = backend.decode([[5] + [0] * 27], [0], [19])
        self.assertEqual(result["completed_ids"], [19])
        self.assertEqual(result["candidates"], [])

    def test_group_without_member_residuals_stays_pending(self):
        backend = fake_slicer_backend()
        FakeSlicer.missing_residuals = True
        result = backend.decode([[5] + [0] * 27], [4], [19])
        self.assertEqual(result["completed_ids"], [])
        self.assertEqual(result["errored_ids"], [])
        self.assertEqual(result["missing_external_results"], [19])
        self.assertEqual(result["metrics"]["babai_lift_call_count"], 0)

    def test_repeated_residuals_do_not_duplicate_terminal_or_exact_candidate(self):
        backend = fake_slicer_backend()
        FakeSlicer.repeat_residuals = True
        result = backend.decode([[5] + [0] * 27], [4], [19])
        self.assertEqual(result["completed_ids"], [19])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["metrics"]["babai_lift_call_count"], 2)

    def test_lift_exception_is_per_external_member_not_whole_group(self):
        backend = fake_slicer_backend()
        backend._mapper.error_target = 7
        result = backend.decode([[5] + [0] * 27, [7] + [0] * 27], [4, 4], [19, 2])
        self.assertEqual(result["completed_ids"], [19])
        self.assertEqual(result["errored_ids"], [2])
        self.assertEqual(result["missing_external_results"], [])
        self.assertEqual([row["backend_integer_id"] for row in result["candidates"]], [19])

    def test_rejected_target_is_error_and_does_not_fake_kernel_call(self):
        backend = fake_slicer_backend()
        FakeSlicer.reject = True
        result = backend.decode([[5] + [0] * 27], [4], [19])
        self.assertEqual(result["completed_ids"], [])
        self.assertEqual(result["errored_ids"], [19])
        self.assertEqual(result["call_evidence"], [])
        self.assertEqual(FakeSlicer.calls, 0)

    def test_unknown_internal_group_id_is_protocol_error(self):
        backend = fake_slicer_backend()
        FakeSlicer.bad_internal_id = True
        with self.assertRaisesRegex(RuntimeError, "unknown internal"):
            backend.decode([[5] + [0] * 27], [4], [19])

    def test_kernel_exception_preserves_actual_call_evidence_and_attempt_count(self):
        backend = fake_slicer_backend()
        FakeSlicer.kernel_raises = True
        with self.assertRaisesRegex(RuntimeError, "mock kernel exception"):
            backend.decode([[5] + [0] * 27, [7] + [0] * 27], [4, 4], [19, 2])
        self.assertEqual(backend.metrics["slicer_kernel_calls"], 1)
        self.assertEqual(backend.metrics["slicer_kernel_completed_calls"], 0)
        self.assertEqual(backend.metrics["slicer_target_count"], 2)
        self.assertEqual(FakeSlicer.calls, 1)
        self.assertEqual(len(backend.call_evidence), 1)
        call = backend.call_evidence[0]
        self.assertFalse(call["returned_normally"])
        self.assertGreaterEqual(call["seconds"], 0)
        self.assertIn("mock kernel exception", call["error"])

    def test_decode_timings_are_parallel_fields_and_preprocess_stays_shared(self):
        backend = fake_slicer_backend()
        first = backend.decode([[5] + [0] * 27], [4], [1])
        second = backend.decode([[7] + [0] * 27], [4], [2])
        self.assertEqual(second["metrics"]["preprocess_count"], 1)
        self.assertEqual(second["metrics"]["slicer_kernel_calls"], 2)
        self.assertEqual(set(first["timings"]), {"target_prepare_seconds", "decode_seconds", "lift_seconds"})
        self.assertTrue(all(value >= 0 for value in first["timings"].values()))


if __name__ == "__main__":
    unittest.main()
