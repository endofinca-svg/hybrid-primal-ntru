"""Staged public-input decoder for the frozen phase-two comparison.

The first-stage adapter is deliberately left unchanged.  This subclass reuses
its target mapping, exact lifting and Slicer invocation, while exposing LLL,
BKZ and lattice database construction as separate operations.  Consequently
both row and Babai baselines can finish before any database is constructed.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Callable, Iterator, Sequence

from .slicer_adapter import SlicerDecoder, _integer, _multiply_left, _residual_sq


class Phase2Decoder(SlicerDecoder):
    """Run LLL now; run BKZ and build a Slicer database only when requested."""

    def __init__(
        self,
        rows: Sequence[Sequence[int]],
        bkz_beta: int,
        sieve_dim: int,
        seed: int,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        *,
        nrand: int = 16,
        max_iterations: int = 400,
        bkz_max_loops: int = 2,
    ) -> None:
        self.original_rows = [[_integer(x) for x in row] for row in rows]
        self.dim = len(self.original_rows)
        if not self.dim or any(len(row) != self.dim for row in self.original_rows):
            raise ValueError("The BDD basis must be nonempty and square")
        self.bkz_beta = _integer(bkz_beta)
        self.sieve_dim = _integer(sieve_dim)
        self.seed = _integer(seed)
        self.nrand = _integer(nrand)
        self.max_iterations = _integer(max_iterations)
        self.bkz_max_loops = _integer(bkz_max_loops)
        if not 2 <= self.bkz_beta <= self.dim:
            raise ValueError("BKZ beta must be between 2 and the basis dimension")
        if not 28 <= self.sieve_dim <= self.dim:
            raise ValueError("The pinned slicer supports this adapter only for 28 <= sieve_dim <= dim")
        if self.seed < 0 or self.nrand < 2 or self.max_iterations < 1 or self.bkz_max_loops < 1:
            raise ValueError("Require seed >= 0, nrand >= 2, and positive iteration/loop caps")
        self._event_callback = event_callback
        self._started = time.perf_counter()
        self._next_call_id = self._next_decode_id = 1
        self._bkz_done = False
        self._database_ready = False
        identity = ["phase2", self.original_rows, self.bkz_beta, self.sieve_dim,
                    self.seed, self.bkz_max_loops]
        self.preprocess_id = hashlib.sha256(
            json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:16]
        self.metrics: dict[str, Any] = {
            "build_lll_seconds": 0.0,
            "build_bkz_seconds": 0.0,
            "build_sieve_seconds": 0.0,
            "build_DB_size": 0,
            "bdgl_like_sieve_calls": 0,
            "target_count": 0,
            "accepted_target_count": 0,
            "candidate_count": 0,
            "lifting_validation_seconds": 0.0,
            "target_db_rejections": 0,
            "babai_diagnostic_calls": 0,
        }
        self._emit("preprocess_start", "preprocessing", dimension=self.dim,
                   bkz_beta=self.bkz_beta, sieve_dim=self.sieve_dim, seed=self.seed,
                   nthreads=1, staged=True)
        self._emit("lll_start", "lll")
        started = time.perf_counter()
        # In particular, do not import or instantiate G6K before the baselines.
        from fpylll import FPLLL, GSO, IntegerMatrix, LLL

        FPLLL.set_random_seed(self.seed)
        basis = IntegerMatrix.from_matrix(self.original_rows, int_type="mpz")
        self.G = GSO.Mat(
            basis, U=IntegerMatrix.identity(self.dim, int_type=basis.int_type),
            UinvT=IntegerMatrix.identity(self.dim, int_type=basis.int_type),
            float_type="long double")
        self.G.update_gso()
        LLL.Reduction(self.G)()
        self.G.update_gso()
        self._snapshot()
        self.basis_checkpoints = [{"stage": "lll", "rows": self.rows}]
        self.metrics["build_lll_seconds"] = time.perf_counter() - started
        self._emit("lll_complete", "lll", seconds=self.metrics["build_lll_seconds"])

    def _snapshot(self) -> None:
        self.rows = [[int(x) for x in row] for row in self.G.B]
        self.reduced_rows = self.rows

    def _lift(self, target, normalized_residual):
        coefficients, vector, distance_sq = super()._lift(target, normalized_residual)
        # The inherited adapter emits candidate_validated immediately after
        # accepting this lift.  Preserve its exact payload at that checkpoint,
        # so a later timeout does not erase already constructed candidates.
        self._latest_lift = {"coefficients": coefficients, "v": vector,
                             "residual_sq": distance_sq}
        return coefficients, vector, distance_sq

    def _emit(self, event: str, phase: str, **fields: Any) -> None:
        if event == "candidate_validated":
            latest = getattr(self, "_latest_lift", None)
            if latest is None or latest["residual_sq"] != fields.get("residual_sq"):
                raise RuntimeError("Validated Slicer candidate has no matching exact lift")
            fields["candidate"] = {**latest, "target_id": fields["target_id"],
                                   "call_id": fields["call_id"], "method": "slicer"}
        super()._emit(event, phase, **fields)

    def iter_babai(
        self, targets: Sequence[Sequence[int]], bounds_sq: Sequence[int | float],
        external_ids: Sequence[Any],
    ) -> Iterator[dict[str, Any]]:
        """Yield each public-radius Babai candidate before decoding the next target.

        A single input validation and completion event retain batch semantics,
        while the worker can validate and checkpoint each candidate immediately.
        """
        integer_targets, bounds, ids = self._validate_batch(targets, bounds_sq, external_ids)
        self.metrics["babai_diagnostic_calls"] += 1
        candidate_count = 0
        for target, bound, external_id in zip(integer_targets, bounds, ids):
            coefficients = [_integer(x) for x in self.G.babai(target)]
            vector = _multiply_left(self.rows, coefficients)
            distance_sq = _residual_sq(target, vector)
            if distance_sq <= bound:
                candidate_count += 1
                yield {"target_id": external_id, "v": vector, "call_id": None,
                       "coefficients": coefficients, "residual_sq": distance_sq,
                       "method": "babai_diagnostic"}
        self._emit("babai_diagnostic_complete", "babai_diagnostic",
                   target_count=len(integer_targets), candidate_count=candidate_count)

    def direct_babai(
        self, targets: Sequence[Sequence[int]], bounds_sq: Sequence[int | float],
        external_ids: Sequence[Any],
    ) -> list[dict[str, Any]]:
        """Compatibility wrapper for callers that need a completed batch list."""
        return list(self.iter_babai(targets, bounds_sq, external_ids))

    def reduce_bkz(self) -> None:
        """Reduce the current LLL basis without importing or constructing G6K."""
        if self._bkz_done or self._database_ready:
            raise RuntimeError("BKZ reduction can run only once, before database construction")
        self._emit("bkz_start", "bkz", beta=self.bkz_beta, max_loops=self.bkz_max_loops)
        started = time.perf_counter()
        from fpylll import BKZ, LLL
        from fpylll.algorithms.bkz2 import BKZReduction

        BKZReduction(self.G)(BKZ.Param(self.bkz_beta, max_loops=self.bkz_max_loops,
                                      flags=BKZ.MAX_LOOPS))
        # Siever.__init__ applies this same LLL operation implicitly.  Complete
        # it here so the BKZ baselines and Slicer see exactly the same basis.
        LLL.Reduction(self.G)()
        self.G.update_gso()
        self._snapshot()
        self.basis_checkpoints.append({"stage": "bkz", "rows": self.rows})
        self.metrics["build_bkz_seconds"] = time.perf_counter() - started
        self._bkz_done = True
        self._emit("bkz_complete", "bkz", seconds=self.metrics["build_bkz_seconds"],
                   final_lll_normalization=True)

    def build_database(self) -> None:
        """Construct the projected lattice-vector database after all baselines."""
        if not self._bkz_done:
            raise RuntimeError("Run BKZ and its baselines before database construction")
        if self._database_ready:
            raise RuntimeError("The lattice database has already been constructed")
        self._emit("sieve_start", "sieve", algorithm="bdgl2", db_size=0)
        started = time.perf_counter()
        from fpylll.util import gaussian_heuristic
        from g6k.siever import SaturationError, Siever
        from g6k.siever_params import SieverParams
        from g6k.slicer import RandomizedSlicer

        expected_rows = self.rows
        params = SieverParams(threads=1, otf_lift=False,
                              saturation_ratio=0.5, saturation_radius=1.32)
        self.g6k = Siever(self.G, params, seed=self.seed)
        if [[int(x) for x in row] for row in self.g6k.M.B] != expected_rows:
            raise RuntimeError("G6K initialization changed the basis used by the BKZ baselines")
        self.g6k.initialize_local(self.dim - self.sieve_dim,
                                  self.dim - self.sieve_dim, self.dim)
        saturated = True
        try:
            self.g6k(alg="bdgl2")
        except SaturationError as exc:
            saturated = False
            self._emit("sieve_saturation_warning", "sieve", message=str(exc),
                       db_size=len(self.g6k))
        self.G = self.g6k.M
        self.G.update_gso()
        self._snapshot()
        if self.rows != expected_rows:
            raise RuntimeError("Lattice database construction changed the BKZ baseline basis")
        self.gh_sub = float(gaussian_heuristic(self.G.r()[-self.sieve_dim:]))
        if len(self.g6k) < 2 or not math.isfinite(self.gh_sub) or self.gh_sub <= 0:
            raise RuntimeError("Preprocessing produced an unusable lattice database or GH scale")
        self._sqrt_r_over_gh = [math.sqrt(float(r) / self.gh_sub)
                                for r in self.G.r()[-self.sieve_dim:]]
        self._sqrt_gh_over_r = [1.0 / value for value in self._sqrt_r_over_gh]
        self._slicer_factory = RandomizedSlicer
        self.metrics["build_sieve_seconds"] = time.perf_counter() - started
        self.metrics["build_DB_size"] = len(self.g6k)
        self._database_ready = True
        self._emit("sieve_complete", "sieve", seconds=self.metrics["build_sieve_seconds"],
                   db_size=len(self.g6k), saturated=saturated, gh_sub=self.gh_sub,
                   baseline_basis_unchanged=True)
        self._emit("preprocess_complete", "preprocessing", metrics=dict(self.metrics))

    def decode(
        self, targets: Sequence[Sequence[int]], bounds_sq: Sequence[int | float],
        external_ids: Sequence[Any],
    ) -> list[dict[str, Any]]:
        if not self._database_ready:
            raise RuntimeError("Construct the lattice database before Slicer decoding")
        return super().decode(targets, bounds_sq, external_ids)
