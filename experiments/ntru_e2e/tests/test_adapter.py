"""Contract checks for target provenance, exact lifting, and kernel boundaries.

The compiled library is exercised by the real runner.  These deterministic
checks model documented kernel behaviors that are hard to trigger reliably with
random lattices (a skipped UID insertion and target iteration out of order).
"""

import math
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slicer_adapter import SlicerDecoder, _multiply_left, _safe_saturation


class DiagonalGSO:
    def from_canonical(self, target):
        return tuple(x / 10 for x in target)

    def babai(self, target, start=0, gso=False):
        return tuple(round(x if gso else x / 10) for x in target)

    def to_canonical(self, coordinates, start=0):
        return [0.0] * start + [10 * x for x in coordinates]


class FakeSiever:
    params = {"db_size_factor": 3.2, "db_size_base": (4 / 3) ** 0.5,
              "bdgl_multi_hash": 4, "bdgl_bucket_size_factor": 0.3,
              "bdgl_min_bucket_size": 128}

    def __len__(self):
        return 128


def decoder_fixture(*, dim=28, reject_first_projection=None):
    events, instances = [], []

    class FakeSlicer:
        def __init__(self, _siever, seed=0):
            self.projections = []
            self.db_size = 0
            self.is_first = not instances
            self.entered_cpp = False
            instances.append(self)

        @property
        def stats(self):
            return {"cdb_t-size:": self.db_size}

        def set_nthreads(self, value):
            assert value == 1

        def set_max_slicer_interations(self, value):
            assert value > 0

        def set_Nt(self, value):
            self.nt = value

        def set_saturation_scalar(self, value):
            self.scalar = value

        def set_proj_error_bound(self, value):
            self.bound = value

        def grow_db_with_target(self, target, n_per_target):
            if not any(target):  # UID zero is reserved by the real kernel.
                return
            if self.is_first and target[0] == reject_first_projection:
                return
            self.projections.append(tuple(target))
            self.db_size += n_per_target

        def bdgl_like_sieve(self, buckets, blocks, multi_hash, verbose):
            assert events[-1]["event"] == "slicer_call_started"
            assert 0 <= math.floor(self.nt * self.scalar) < self.db_size
            assert buckets >= 1 and blocks >= 1
            self.entered_cpp = True

        def itervalues_cdb_t(self, return_with_index=True):
            assert self.entered_cpp and return_with_index
            # Reversal prevents accidentally relying on iterator position as ID.
            for internal_id in reversed(range(len(self.projections))):
                yield self.projections[internal_id], internal_id

    decoder = SlicerDecoder.__new__(SlicerDecoder)
    decoder.dim = dim
    decoder.sieve_dim = 28
    decoder.rows = [[10 if i == j else 0 for j in range(dim)] for i in range(dim)]
    decoder.reduced_rows = decoder.rows
    decoder.G = DiagonalGSO()
    decoder.g6k = FakeSiever()
    decoder.gh_sub = 1.0
    decoder._sqrt_r_over_gh = [10.0] * 28
    decoder._sqrt_gh_over_r = [0.1] * 28
    decoder._slicer_factory = FakeSlicer
    decoder._event_callback = events.append
    decoder._started = time.perf_counter()
    decoder.preprocess_id = "test-public-basis"
    decoder._next_call_id = decoder._next_decode_id = 1
    decoder.seed = 17
    decoder.nrand = 2
    decoder.max_iterations = 4
    decoder.metrics = {"target_count": 0, "accepted_target_count": 0, "candidate_count": 0,
                       "lifting_validation_seconds": 0.0, "target_db_rejections": 0,
                       "bdgl_like_sieve_calls": 0, "babai_diagnostic_calls": 0}
    return decoder, events, instances


def target(first, dim=28):
    return [first] + [0] * (dim - 1)


def test_duplicate_and_negative_targets_keep_external_ids():
    decoder, events, instances = decoder_fixture()
    result = decoder.decode([target(12), target(12), target(-12)], [4, 4, 4], [101, 205, 990])
    assert {r["target_id"]: r["v"][0] for r in result} == {101: 10, 205: 10, 990: -10}
    assert len(instances) == 1 and len(instances[0].projections) == 1
    started = next(e for e in events if e["event"] == "slicer_call_started")
    assert started["external_ids"] == [101, 205, 990]
    assert all(r["call_id"] == started["call_id"] and r["method"] == "slicer" for r in result)
    assert all(_multiply_left(decoder.reduced_rows, r["coefficients"]) == r["v"] for r in result)


def test_skipped_uid_does_not_shift_following_target_ids():
    decoder, events, instances = decoder_fixture(reject_first_projection=3.0)
    result = decoder.decode([target(12), target(23), target(34)], [4, 9, 16], [4, 9, 20])
    assert {r["target_id"]: r["v"][0] for r in result} == {4: 10, 9: 20, 20: 30}
    by_id = {r["target_id"]: r["call_id"] for r in result}
    assert by_id[4] == by_id[20] != by_id[9]
    assert len(instances) == 2
    calls = [e for e in events if e["event"] == "slicer_call_started"]
    assert calls[0]["external_ids"] == [4, 20]
    assert calls[1]["external_ids"] == [9]
    assert decoder.metrics["accepted_target_count"] == 3


def test_projected_acceptance_cannot_replace_full_integer_radius_check():
    decoder, _, _ = decoder_fixture(dim=29)
    # Tail residual is 2, inside radius^2=4; the prefix residual is 5.
    t = [5, 12] + [0] * 27
    assert decoder.decode([t], [4], ["outside-full-ball"]) == []
    assert decoder.metrics["bdgl_like_sieve_calls"] == 1


def test_zero_uid_failure_is_logged_and_never_becomes_babai_fallback():
    decoder, events, _ = decoder_fixture()
    assert decoder.decode([target(10)], [0], ["exact-lattice-point"]) == []
    assert any(e["event"] == "target_unresolved" for e in events)
    assert decoder.metrics["bdgl_like_sieve_calls"] == 0
    assert decoder.metrics["babai_diagnostic_calls"] == 0
    diagnostic = decoder.direct_babai([target(10)], [0], ["exact-lattice-point"])
    assert diagnostic[0]["v"] == target(10)
    assert diagnostic[0]["call_id"] is None
    assert diagnostic[0]["method"] == "babai_diagnostic"


def test_integer_multiplication_is_exact_above_float_precision():
    large = 2**60 + 1
    assert _multiply_left([[large, 3], [large - 1, 5]], [2, -2]) == [2, -4]


@pytest.mark.parametrize("size,count", [(1, 1), (2, 1), (4, 2), (16, 16), (256, 16)])
def test_small_batch_saturation_index_is_in_bounds(size, count):
    index, scalar = _safe_saturation(size, count)
    assert math.floor(count * scalar) == index
    assert 0 <= index < size


def test_bad_public_batch_rejected_before_target_database():
    decoder, _, instances = decoder_fixture()
    with pytest.raises(ValueError, match="same length"):
        decoder.decode([target(1)], [], [1])
    with pytest.raises(TypeError):
        decoder.decode([[1.5] * 28], [4], [1])
    with pytest.raises(ValueError, match="finite and nonnegative"):
        decoder.decode([target(1)], [-1], [1])
    assert instances == []


def test_slicer_dimension_guard_runs_without_importing_compiled_dependencies():
    with pytest.raises(ValueError, match="28 <= sieve_dim"):
        SlicerDecoder([[1 if i == j else 0 for j in range(28)] for i in range(28)], 20, 27, 1)
