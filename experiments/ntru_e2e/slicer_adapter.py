"""Public-input Batch-BDD adapter for the pinned g6k_hybrid RandomizedSlicer.

One decoder owns one reduced basis and one lattice-vector database.  Each decode
call creates a target database, calls the C++ slicer, and lifts its residuals with
Babai.  Ambient vectors and final distance checks use Python integers.  No secret
or actual error vector is accepted by this module.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
import struct
import time
from typing import Any, Callable, Sequence


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("Boolean coordinates are not supported")
    return int(operator.index(value))


def _multiply_left(rows: Sequence[Sequence[int]], coefficients: Sequence[int]) -> list[int]:
    """Multiply without converting lattice coordinates to floating point."""
    if len(coefficients) != len(rows):
        raise ValueError("Wrong number of lattice coefficients")
    return [sum(c * row[j] for c, row in zip(coefficients, rows)) for j in range(len(rows[0]))]


def _residual_sq(target: Sequence[int], vector: Sequence[int]) -> int:
    return sum((t - v) ** 2 for t, v in zip(target, vector))


def _signed_float32_key(vector: Sequence[float]) -> tuple[tuple[float, ...], int]:
    """Canonicalize the precision and +/- convention used by the C++ target DB.

    Exact equal float32 projections are shared.  Near projections are not merged;
    the kernel's coarser UID collisions are detected from actual insertion counts.
    """
    cast = tuple(struct.unpack("f", struct.pack("f", float(x)))[0] for x in vector)
    if not all(math.isfinite(x) for x in cast):
        raise ValueError("Non-finite target projection")
    sign = next((1 if x > 0 else -1 for x in cast if x != 0), 1)
    return tuple(0.0 if x == 0 else sign * x for x in cast), sign


def _safe_saturation(db_size: int, accepted_targets: int) -> tuple[int, float]:
    """The pinned C++ kernel indexes floor(Nt * saturation_scalar) unchecked."""
    if db_size < 1 or accepted_targets < 1:
        raise ValueError("Saturation needs a nonempty target database")
    index = min(db_size - 1, accepted_targets)
    return index, (index + 0.5) / accepted_targets


class SlicerDecoder:
    """Reduce and sieve ``rows`` once, then decode public target batches.

    ``bounds_sq`` are full ambient squared radii from public parameters.  Since
    orthogonal projection cannot increase norm, ``bounds_sq / gh_sub`` are valid
    normalized squared bounds without consulting any true error vector.

    The pinned kernel requires local dimension >= 28.  The randomization count
    and iteration cap are fixed public tuning parameters, not estimator outputs.
    """

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
        self._next_call_id = 1
        self._next_decode_id = 1
        identity = [self.original_rows, self.bkz_beta, self.sieve_dim, self.seed, self.bkz_max_loops]
        self.preprocess_id = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:16]
        self.metrics: dict[str, Any] = {
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
        self._emit("preprocess_start", "preprocessing", dimension=self.dim, bkz_beta=self.bkz_beta,
                   sieve_dim=self.sieve_dim, seed=self.seed, nthreads=1)

        # Imports are deferred so input validation and pure mapping tests do not
        # require the compiled dependency.  The launcher selects the pinned tree.
        from fpylll import BKZ, FPLLL, GSO, IntegerMatrix, LLL
        from fpylll.algorithms.bkz2 import BKZReduction
        from fpylll.util import gaussian_heuristic
        from g6k.siever import SaturationError, Siever
        from g6k.siever_params import SieverParams
        from g6k.slicer import RandomizedSlicer

        self._slicer_factory = RandomizedSlicer
        FPLLL.set_random_seed(self.seed)
        basis = IntegerMatrix.from_matrix(self.original_rows, int_type="mpz")
        matrix = GSO.Mat(basis, U=IntegerMatrix.identity(self.dim, int_type=basis.int_type),
                         UinvT=IntegerMatrix.identity(self.dim, int_type=basis.int_type),
                         float_type="long double")
        matrix.update_gso()
        self._emit("lll_start", "lll")
        LLL.Reduction(matrix)()
        self.basis_checkpoints = [{"stage": "lll", "rows": [[int(x) for x in row] for row in matrix.B]}]
        self._emit("lll_complete", "lll")
        self._emit("bkz_start", "bkz", beta=self.bkz_beta, max_loops=self.bkz_max_loops)
        started = time.perf_counter()
        BKZReduction(matrix)(BKZ.Param(self.bkz_beta, max_loops=self.bkz_max_loops, flags=BKZ.MAX_LOOPS))
        self.metrics["build_bkz_seconds"] = time.perf_counter() - started
        self.basis_checkpoints.append({"stage": "bkz", "rows": [[int(x) for x in row] for row in matrix.B]})
        self._emit("bkz_complete", "bkz", seconds=self.metrics["build_bkz_seconds"])

        params = SieverParams(threads=1, otf_lift=False, saturation_ratio=0.5, saturation_radius=1.32)
        self.g6k = Siever(matrix, params, seed=self.seed)
        self.g6k.initialize_local(self.dim - self.sieve_dim, self.dim - self.sieve_dim, self.dim)
        self._emit("sieve_start", "sieve", algorithm="bdgl2", db_size=len(self.g6k))
        started = time.perf_counter()
        saturated = True
        try:
            self.g6k(alg="bdgl2")
        except SaturationError as exc:
            saturated = False
            self._emit("sieve_saturation_warning", "sieve", message=str(exc), db_size=len(self.g6k))
        self.metrics["build_sieve_seconds"] = time.perf_counter() - started
        self.metrics["build_DB_size"] = len(self.g6k)
        self.G = self.g6k.M
        self.G.update_gso()
        self.rows = [[int(x) for x in row] for row in self.G.B]
        self.reduced_rows = self.rows
        self.gh_sub = float(gaussian_heuristic(self.G.r()[-self.sieve_dim:]))
        if len(self.g6k) < 2 or not math.isfinite(self.gh_sub) or self.gh_sub <= 0:
            raise RuntimeError("Preprocessing produced an unusable lattice database or GH scale")
        self._sqrt_r_over_gh = [math.sqrt(float(r) / self.gh_sub) for r in self.G.r()[-self.sieve_dim:]]
        self._sqrt_gh_over_r = [1.0 / x for x in self._sqrt_r_over_gh]
        self._emit("sieve_complete", "sieve", seconds=self.metrics["build_sieve_seconds"],
                   db_size=len(self.g6k), saturated=saturated, gh_sub=self.gh_sub)
        self._emit("preprocess_complete", "preprocessing", metrics=dict(self.metrics))

    def _emit(self, event: str, phase: str, **fields: Any) -> None:
        if self._event_callback is not None:
            self._event_callback({"event": event, "phase": phase, "preprocess_id": self.preprocess_id,
                                  "elapsed_seconds": time.perf_counter() - self._started, **fields})

    def _validate_batch(
        self, targets: Sequence[Sequence[int]], bounds_sq: Sequence[int | float], external_ids: Sequence[Any]
    ) -> tuple[list[list[int]], list[int | float], list[Any]]:
        if len(targets) != len(bounds_sq) or len(targets) != len(external_ids):
            raise ValueError("Targets, public bounds, and external IDs must have the same length")
        integer_targets = [[_integer(x) for x in t] for t in targets]
        if any(len(t) != self.dim for t in integer_targets):
            raise ValueError("Each target must have the full ambient dimension")
        bounds: list[int | float] = []
        for value in bounds_sq:
            if isinstance(value, bool):
                raise TypeError("Boolean bounds are not supported")
            try:
                bound: int | float = _integer(value)
            except TypeError:
                bound = float(value)
            if bound < 0 or not math.isfinite(bound):
                raise ValueError("Public squared bounds must be finite and nonnegative")
            bounds.append(bound)
        ids = list(external_ids)
        if len(set(ids)) != len(ids):
            raise ValueError("External target IDs must be unique within each batch")
        return integer_targets, bounds, ids

    def _project_reduced(self, target: Sequence[int]) -> list[float]:
        """Babai-shift the tail before conversion, matching upstream alg_2."""
        start = self.dim - self.sieve_dim
        tail_gso = list(self.G.from_canonical(target))[start:]
        coefficients = [_integer(x) for x in self.G.babai(tail_gso, start=start, gso=True)]
        shift = _multiply_left(self.rows, [0] * start + coefficients)
        shifted = [t - s for t, s in zip(target, shift)]
        projected = list(self.G.from_canonical(shifted))[-self.sieve_dim:]
        return [float(x) * scale for x, scale in zip(projected, self._sqrt_r_over_gh)]

    def _groups(self, targets: list[list[int]], bounds: list[int | float], ids: list[Any]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        by_projection: dict[tuple[float, ...], dict[str, Any]] = {}
        for target, bound, external_id in zip(targets, bounds, ids):
            projection, sign = _signed_float32_key(self._project_reduced(target))
            member = {"target": target, "bound_sq": bound, "target_id": external_id, "sign": sign}
            group = by_projection.get(projection)
            if group is None:
                group = {"projection": projection, "members": []}
                groups.append(group)
                by_projection[projection] = group
            group["members"].append(member)
        return groups

    def _lift(self, target: Sequence[int], normalized_residual: Sequence[float]) -> tuple[list[int], list[int], int]:
        coordinates = [float(x) * scale for x, scale in zip(normalized_residual, self._sqrt_gh_over_r)]
        # A nonzero start converts through only those original basis rows; it
        # need not cancel their components along the earlier GSO vectors.
        # Padding the global GSO coordinates preserves a purely orthogonal
        # tail residual, so subtracting it does not perturb the prefix target.
        global_coordinates = [0.0] * (self.dim - self.sieve_dim) + coordinates
        residual = self.G.to_canonical(global_coordinates, start=0)
        coefficients = [_integer(x) for x in self.G.babai([t - float(e) for t, e in zip(target, residual)])]
        vector = _multiply_left(self.rows, coefficients)
        return coefficients, vector, _residual_sq(target, vector)

    def _bucket_parameters(self) -> tuple[int, int, int]:
        blocks = min(2, self.sieve_dim // 28)
        params = self.g6k.params
        size = float(params["db_size_factor"]) * float(params["db_size_base"]) ** self.sieve_dim
        multi_hash = int(params["bdgl_multi_hash"])
        buckets = float(params["bdgl_bucket_size_factor"]) * 2.0 ** ((blocks - 1.0) / (blocks + 1.0))
        buckets *= multi_hash ** ((2.0 * blocks) / (blocks + 1.0)) * size ** (blocks / (1.0 + blocks))
        buckets = min(buckets, multi_hash * size / float(params["bdgl_min_bucket_size"]))
        return max(1, int(max(buckets, 2 ** (blocks - 1)))), blocks, multi_hash

    @staticmethod
    def _target_db_size(slicer: Any) -> int:
        return int(slicer.stats["cdb_t-size:"])

    def _run_groups(self, groups: list[dict[str, Any]], decode_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        call_id = self._next_call_id
        self._next_call_id += 1
        slicer = self._slicer_factory(self.g6k, seed=(self.seed + call_id) % (2**32))
        slicer.set_nthreads(1)
        slicer.set_max_slicer_interations(self.max_iterations)
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        self._emit("target_db_start", "target_db", call_id=call_id, decode_id=decode_id,
                   unique_targets=len(groups), nrand=self.nrand)
        for ordinal, group in enumerate(groups):
            before = self._target_db_size(slicer)
            # The kernel reserves UID zero and rejects +/- UID collisions.  Read
            # the actual growth; never infer an internal ID from input ordinal.
            self._emit("target_insert_start", "target_db", call_id=call_id, ordinal=ordinal,
                       external_ids=[m["target_id"] for m in group["members"]], db_size=before)
            slicer.grow_db_with_target(group["projection"], n_per_target=self.nrand)
            after = self._target_db_size(slicer)
            if after == before:
                rejected.append(group)
                self.metrics["target_db_rejections"] += 1
                self._emit("target_insert_rejected", "target_db", call_id=call_id, ordinal=ordinal,
                           reason="kernel_uid_collision_or_zero", db_size=after)
            elif after == before + self.nrand:
                accepted.append(group)
                self.metrics["accepted_target_count"] += len(group["members"])
                self._emit("target_insert_complete", "target_db", call_id=call_id, ordinal=ordinal,
                           internal_id=len(accepted) - 1, db_size=after)
            else:
                raise RuntimeError("Unexpected target DB growth; cannot preserve target IDs")
        db_size = self._target_db_size(slicer)
        if not accepted:
            self._emit("target_db_empty", "target_db", call_id=call_id, decode_id=decode_id)
            return [], rejected

        # Use the largest full-radius public bound only to select what residuals
        # to inspect.  Each member is then checked against its own exact radius.
        normalized_bounds = [float(m["bound_sq"]) / self.gh_sub for g in accepted for m in g["members"]]
        normalized_bound = max(normalized_bounds)
        saturation_index, saturation_scalar = _safe_saturation(db_size, len(accepted))
        slicer.set_Nt(len(accepted))
        slicer.set_saturation_scalar(saturation_scalar)
        slicer.set_proj_error_bound(min(normalized_bounds) * (1.0 + 1e-4))
        buckets, blocks, multi_hash = self._bucket_parameters()
        self.metrics["bdgl_like_sieve_calls"] += 1
        self._emit("slicer_call_started", "slicer", call_id=call_id, decode_id=decode_id,
                   function="bdgl_like_sieve",
                   external_ids=[m["target_id"] for g in accepted for m in g["members"]],
                   target_db_size=db_size, accepted_unique_targets=len(accepted), buckets=buckets,
                   blocks=blocks, multi_hash=multi_hash, max_iterations=self.max_iterations,
                   saturation_index=saturation_index, saturation_scalar=saturation_scalar,
                   public_normalized_bounds_sq=normalized_bounds, lattice_db_size=len(self.g6k))
        started = time.perf_counter()
        slicer.bdgl_like_sieve(buckets, blocks, multi_hash, False)
        self._emit("slicer_call_finished", "slicer", call_id=call_id, decode_id=decode_id,
                   function="bdgl_like_sieve",
                   seconds=time.perf_counter() - started, stats=dict(slicer.stats))

        started = time.perf_counter()
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[Any, tuple[int, ...]]] = set()
        inspected = 0
        for residual, internal_id in slicer.itervalues_cdb_t(return_with_index=True):
            # The kernel sorts by residual norm.  Use a conservative float32
            # margin here; the ambient acceptance check below is exact.
            projected_sq = sum(float(x) ** 2 for x in residual)
            if projected_sq > normalized_bound * (1.0 + 1e-4) + 1e-10:
                continue
            internal_id = int(internal_id)
            if not 0 <= internal_id < len(accepted):
                raise RuntimeError("Slicer returned an unmapped target ID")
            inspected += 1
            for member in accepted[internal_id]["members"]:
                signed_residual = [member["sign"] * float(x) for x in residual]
                coefficients, vector, distance_sq = self._lift(member["target"], signed_residual)
                identity = (member["target_id"], tuple(vector))
                if distance_sq <= member["bound_sq"] and identity not in seen:
                    seen.add(identity)
                    candidate = {"target_id": member["target_id"], "v": vector,
                                 "call_id": call_id, "coefficients": coefficients,
                                 "residual_sq": distance_sq, "method": "slicer"}
                    candidates.append(candidate)
                    self._emit("candidate_validated", "lifting_validation", call_id=call_id,
                               target_id=member["target_id"], residual_sq=distance_sq)
        elapsed = time.perf_counter() - started
        self.metrics["lifting_validation_seconds"] += elapsed
        self.metrics["candidate_count"] += len(candidates)
        self._emit("lifting_validation_complete", "lifting_validation", call_id=call_id,
                   decode_id=decode_id, seconds=elapsed, residuals_inspected=inspected,
                   candidate_count=len(candidates))
        return candidates, rejected

    def decode(
        self, targets: Sequence[Sequence[int]], bounds_sq: Sequence[int | float], external_ids: Sequence[Any]
    ) -> list[dict[str, Any]]:
        integer_targets, bounds, ids = self._validate_batch(targets, bounds_sq, external_ids)
        decode_id = self._next_decode_id
        self._next_decode_id += 1
        self.metrics["target_count"] += len(integer_targets)
        self._emit("decode_start", "decode", decode_id=decode_id, target_count=len(integer_targets),
                   external_ids=ids, public_bounds_sq=bounds)
        if not integer_targets:
            self._emit("decode_complete", "decode", decode_id=decode_id, candidate_count=0)
            return []
        groups = self._groups(integer_targets, bounds, ids)
        candidates, rejected = self._run_groups(groups, decode_id)
        # A target may collide with a different target's randomization.  A fresh
        # singleton DB retries it without perturbing any accepted ID mappings.
        for group in rejected:
            self._emit("target_retry", "target_db", decode_id=decode_id,
                       external_ids=[m["target_id"] for m in group["members"]])
            retry_candidates, still_rejected = self._run_groups([group], decode_id)
            candidates.extend(retry_candidates)
            if still_rejected:
                self._emit("target_unresolved", "target_db", decode_id=decode_id,
                           external_ids=[m["target_id"] for m in group["members"]],
                           reason="kernel_uid_zero_or_collision_in_fresh_singleton_db")
        self._emit("decode_complete", "decode", decode_id=decode_id, unique_projected_targets=len(groups),
                   candidate_count=len(candidates), metrics=dict(self.metrics))
        return candidates

    def direct_babai(
        self, targets: Sequence[Sequence[int]], bounds_sq: Sequence[int | float], external_ids: Sequence[Any]
    ) -> list[dict[str, Any]]:
        """Independent diagnostic; never called by ``decode`` as a fallback."""
        integer_targets, bounds, ids = self._validate_batch(targets, bounds_sq, external_ids)
        self.metrics["babai_diagnostic_calls"] += 1
        results: list[dict[str, Any]] = []
        for target, bound, external_id in zip(integer_targets, bounds, ids):
            coefficients = [_integer(x) for x in self.G.babai(target)]
            vector = _multiply_left(self.rows, coefficients)
            distance_sq = _residual_sq(target, vector)
            if distance_sq <= bound:
                results.append({"target_id": external_id, "v": vector, "call_id": None,
                                "coefficients": coefficients, "residual_sq": distance_sq,
                                "method": "babai_diagnostic"})
        self._emit("babai_diagnostic_complete", "babai_diagnostic", target_count=len(integer_targets),
                   candidate_count=len(results))
        return results
