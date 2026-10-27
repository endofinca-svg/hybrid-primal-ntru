"""Stage and basis contracts that make the phase-two comparison fair."""

from contextlib import contextmanager
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from experiments.ntru_e2e.phase2_adapter import Phase2Decoder
from experiments.ntru_e2e.slicer_adapter import SlicerDecoder


@contextmanager
def fake_backend(*, change_siever_basis=False):
    """A staged backend with observable reductions and a tiny exact lattice."""
    actions = []

    class Matrix(list):
        int_type = "mpz"

        @classmethod
        def from_matrix(cls, rows, **_kwargs):
            return cls([list(row) for row in rows])

        @classmethod
        def identity(cls, dimension, **_kwargs):
            return cls([[int(i == j) for j in range(dimension)]
                        for i in range(dimension)])

    class GSO:
        def __init__(self, basis, **_kwargs):
            self.B = basis

        def update_gso(self):
            pass

        def r(self):
            return [100] * len(self.B)

        def babai(self, target):
            actions.append("babai")
            return [round(value / 10) for value in target]

    class LLL:
        @staticmethod
        def Reduction(_matrix):
            def reduce():
                actions.append("lll")
            return reduce

    class BKZReduction:
        def __init__(self, _matrix):
            pass

        def __call__(self, _parameters):
            actions.append("bkz")

    class Siever:
        def __init__(self, matrix, _parameters, seed):
            actions.append("siever_init")
            self.M = matrix
            if change_siever_basis:
                self.M.B[0][0] += 1

        def initialize_local(self, *context):
            actions.append(("context", context))

        def __call__(self, **kwargs):
            assert kwargs == {"alg": "bdgl2"}
            actions.append("sieve")

        def __len__(self):
            return 64

    class SaturationError(Exception):
        pass

    def module(name, **values):
        result = ModuleType(name)
        result.__dict__.update(values)
        return result

    bkz = SimpleNamespace(Param=lambda *args, **kwargs: (args, kwargs), MAX_LOOPS=1)
    modules = {
        "fpylll": module("fpylll", FPLLL=SimpleNamespace(set_random_seed=lambda seed: None),
                          IntegerMatrix=Matrix, GSO=SimpleNamespace(Mat=GSO), LLL=LLL, BKZ=bkz),
        "fpylll.algorithms": module("fpylll.algorithms"),
        "fpylll.algorithms.bkz2": module("fpylll.algorithms.bkz2", BKZReduction=BKZReduction),
        "fpylll.util": module("fpylll.util", gaussian_heuristic=lambda radii: 100),
        "g6k": module("g6k"),
        "g6k.siever": module("g6k.siever", Siever=Siever, SaturationError=SaturationError),
        "g6k.siever_params": module("g6k.siever_params", SieverParams=lambda **kwargs: kwargs),
        "g6k.slicer": module("g6k.slicer", RandomizedSlicer=object),
    }
    with patch.dict(sys.modules, modules):
        yield actions


def diagonal_rows(dimension=28):
    return [[10 * int(i == j) for j in range(dimension)] for i in range(dimension)]


def test_lll_and_bkz_babai_finish_before_lattice_database_construction():
    events = []
    with fake_backend() as actions:
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17, event_callback=events.append)
        lll_rows = decoder.rows
        assert actions == ["lll"]
        assert not hasattr(decoder, "g6k")
        target = [12] + [0] * 27
        first = decoder.direct_babai([target], [4], [55])
        assert first[0]["v"] == [10] + [0] * 27
        decoder.reduce_bkz()
        second = decoder.direct_babai([target], [4], [55])
        assert second == first
        assert actions == ["lll", "babai", "bkz", "lll", "babai"]
        assert not hasattr(decoder, "g6k")
        decoder.build_database()
        assert actions[-3:] == ["siever_init", ("context", (0, 0, 28)), "sieve"]
        assert decoder.rows == lll_rows
        assert [item["stage"] for item in decoder.basis_checkpoints] == ["lll", "bkz"]
        assert decoder.metrics["build_DB_size"] == 64
        assert decoder.metrics["babai_diagnostic_calls"] == 2
        for key in ("build_lll_seconds", "build_bkz_seconds", "build_sieve_seconds"):
            assert decoder.metrics[key] >= 0
        sequence = [event["event"] for event in events]
        assert sequence.index("lll_complete") < sequence.index("babai_diagnostic_complete")
        assert sequence.index("bkz_complete") < sequence.index("sieve_start")


def test_out_of_order_and_repeated_stages_are_rejected():
    with fake_backend():
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17)
        with pytest.raises(RuntimeError, match="Run BKZ"):
            decoder.build_database()
        with pytest.raises(RuntimeError, match="before Slicer decoding"):
            decoder.decode([], [], [])
        decoder.reduce_bkz()
        with pytest.raises(RuntimeError, match="only once"):
            decoder.reduce_bkz()
        decoder.build_database()
        with pytest.raises(RuntimeError, match="already been constructed"):
            decoder.build_database()


def test_babai_streams_first_candidate_before_later_target_work():
    events = []
    with fake_backend() as actions:
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17, event_callback=events.append)
        targets = [[12] + [0] * 27, [21] + [0] * 27]
        with patch.object(decoder, "_validate_batch", wraps=decoder._validate_batch) as validate:
            candidates = decoder.iter_babai(targets, [4, 1], [7, 9])
            first = next(candidates)
            assert first["target_id"] == 7
            assert first["v"][0] == 10 and first["residual_sq"] == 4
            assert actions.count("babai") == 1
            assert not any(event["event"] == "babai_diagnostic_complete" for event in events)
            assert next(candidates)["target_id"] == 9
            assert actions.count("babai") == 2
            with pytest.raises(StopIteration):
                next(candidates)
            validate.assert_called_once_with(targets, [4, 1], [7, 9])
        assert decoder.metrics["babai_diagnostic_calls"] == 1
        complete = [event for event in events if event["event"] == "babai_diagnostic_complete"]
        assert len(complete) == 1 and complete[0]["candidate_count"] == 2


def test_abandoned_babai_stream_preserves_yielded_candidate_without_fake_completion():
    events = []
    with fake_backend() as actions:
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17, event_callback=events.append)
        candidates = decoder.iter_babai([[12] + [0] * 27, [21] + [0] * 27], [4, 1], [7, 9])
        saved = next(candidates)
        candidates.close()
        assert saved["target_id"] == 7
        assert actions.count("babai") == 1
        assert decoder.metrics["babai_diagnostic_calls"] == 1
        assert not any(event["event"] == "babai_diagnostic_complete" for event in events)


def test_hidden_g6k_basis_change_cannot_create_an_unfair_slicer_comparison():
    with fake_backend(change_siever_basis=True):
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17)
        decoder.reduce_bkz()
        with pytest.raises(RuntimeError, match="changed the basis"):
            decoder.build_database()
        assert not decoder._database_ready


def test_decode_reuses_original_exact_adapter_after_database_is_ready():
    with fake_backend():
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17)
        decoder.reduce_bkz()
        decoder.build_database()
        with patch.object(SlicerDecoder, "decode", return_value=[{"target_id": 7}]) as decode:
            assert decoder.decode([[0] * 28], [4], [7]) == [{"target_id": 7}]
        decode.assert_called_once_with([[0] * 28], [4], [7])


def test_validated_event_preserves_exact_candidate_before_decode_returns():
    events = []
    with fake_backend():
        decoder = Phase2Decoder(diagonal_rows(), 20, 28, 17, event_callback=events.append)
        coefficients, vector = [1] + [0] * 27, [10] + [0] * 27
        with patch.object(SlicerDecoder, "_lift", return_value=(coefficients, vector, 4)):
            decoder._lift([12] + [0] * 27, [2] + [0] * 27)
        decoder._emit("candidate_validated", "lifting_validation", call_id=3,
                      target_id=9, residual_sq=4)
        assert events[-1]["candidate"] == {
            "coefficients": coefficients, "v": vector, "residual_sq": 4,
            "target_id": 9, "call_id": 3, "method": "slicer"}


def test_invalid_input_fails_before_compiled_imports():
    with pytest.raises(ValueError, match="28 <= sieve_dim"):
        Phase2Decoder(diagonal_rows(), 20, 27, 17)
    with pytest.raises(TypeError, match="Boolean"):
        Phase2Decoder([[True]], 2, 28, 17)
