"""Fixed-basis Babai and pinned Slicer adapters, with no reduction calls.

The caller owns input loading, external progress, public validation and output.
This module accepts only integer targets and explicit backend integer IDs.  It
never accepts a reference vector and never imports the offline evaluator.

Slicer projections/lifts reuse the frozen adapter's pure mapping methods.  Its
constructor is deliberately bypassed: that constructor performs LLL and BKZ.
The pinned Siever constructor also calls ``self.lll``; a small subclass records
and suppresses that call and rejects any subsequent reduction request.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
import time

from experiments.ntru_e2e.slicer_adapter import (
    SlicerDecoder, _integer, _multiply_left, _residual_sq, _safe_saturation,
    _signed_float32_key,
)


def _rows(gso):
    return [[_integer(value) for value in row] for row in gso.B]


def _inputs(working, config, *, transforms=False):
    rows = working.get("rows")
    if (not isinstance(rows, (list, tuple)) or not rows
            or any(not isinstance(row, (list, tuple)) or len(row) != len(rows) for row in rows)):
        raise ValueError("fixed backend requires nonempty square integer rows")
    rows = [[_integer(value) for value in row] for row in rows]
    gso = working.get("gso")
    if gso is None:
        from fpylll import FPLLL, GSO, IntegerMatrix
        from . import inputs

        float_type = config.get("float_type", "mpfr")
        precision = config.get("precision_bits", 192 if float_type == "mpfr" else None)
        if float_type not in ("mpfr", "long double"):
            raise ValueError("explicit decoder GSO type must be mpfr or long double")
        if float_type == "mpfr":
            if isinstance(precision, bool) or not isinstance(precision, int) or precision < 53:
                raise ValueError("explicit MPFR precision must be an integer >=53")
            guard = inputs._decoder_mpfr_precision
            if guard is not None and guard != precision:
                raise ValueError("same-process decoder GSOs must use one MPFR precision")
            if guard is None:
                FPLLL.set_precision(precision)
            if FPLLL.get_precision() != precision:
                raise RuntimeError("MPFR precision changed while fixed decoder GSOs are alive")
        elif precision is not None:
            raise ValueError("long double does not take MPFR precision bits")
        matrix = IntegerMatrix.from_matrix(rows, int_type="mpz")
        options = {"float_type": float_type}
        if transforms:
            options.update(U=IntegerMatrix.identity(len(rows), int_type="mpz"),
                           UinvT=IntegerMatrix.identity(len(rows), int_type="mpz"))
        gso = GSO.Mat(matrix, **options)
        gso.update_gso()
        if float_type == "mpfr":
            inputs._decoder_mpfr_precision = precision
        working["gso"] = gso
        working["gso_metadata"] = {"status": "created_without_reduction", "float_type": float_type,
                                   "precision_bits": precision, "transform_bookkeeping": transforms,
                                   "precision_scope": "process-global MPFR precision fixed for every live decoder GSO"}
    metadata = working.get("gso_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("injected GSO requires explicit numeric metadata")
    requested_type = config.get("float_type", metadata.get("float_type"))
    if requested_type not in ("mpfr", "long double"):
        raise ValueError("injected GSO requires a supported explicit float type")
    if getattr(gso, "float_type", None) != requested_type or metadata.get("float_type") != requested_type:
        raise ValueError("injected GSO float type differs from requested or recorded type")
    requested_precision = config.get("precision_bits", metadata.get("precision_bits"))
    if requested_type == "mpfr":
        if (isinstance(requested_precision, bool) or not isinstance(requested_precision, int)
                or requested_precision < 53 or metadata.get("precision_bits") != requested_precision):
            raise ValueError("injected MPFR GSO precision metadata differs from requested precision")
        from fpylll import FPLLL
        from . import inputs

        if inputs._decoder_mpfr_precision != requested_precision:
            raise ValueError("injected MPFR GSO precision is not established by the process decoder guard")
        if FPLLL.get_precision() != requested_precision:
            raise RuntimeError("injected MPFR GSO precision differs from current process precision")
    elif requested_precision is not None or metadata.get("precision_bits") is not None:
        raise ValueError("injected long double GSO cannot be labelled with MPFR precision bits")
    if _rows(gso) != rows:
        raise ValueError("a matching private GSO must be explicitly initialized")
    return rows, gso


@dataclass(frozen=True)
class SlicerConfig:
    sieve_dim: int = 28
    seed: int = 20261026
    db_size: int = 128
    nrand: int = 2
    max_iterations: int = 1
    preprocessing: str = "bdgl2"
    buckets: int = 4
    blocks: int = 1
    multi_hash: int = 1
    interface_test_mode: bool = False
    force_kernel_iterations: bool = False

    def __post_init__(self):
        for name in ("sieve_dim", "seed", "db_size", "nrand", "max_iterations", "buckets", "blocks", "multi_hash"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.sieve_dim < 28 or self.seed < 0:
            raise ValueError("require sieve_dim >=28 and nonnegative seed")
        # The pinned randomizer starts within about 5*N**.6 entries, then
        # increments an index for about 2+N**.3 trials.  N>=128 keeps this
        # bounded search inside N; tiny databases are unsafe for this backend.
        if not 128 <= self.db_size <= 4096 or self.nrand < 2 or self.max_iterations < 1:
            raise ValueError("require 128<=db_size<=4096, nrand>=2 and positive iterations")
        if self.blocks not in (1, 2) or self.sieve_dim // self.blocks < 28:
            raise ValueError("each explicit BDGL block must have at least 28 coordinates")
        if self.buckets < self.multi_hash or self.multi_hash < 1:
            raise ValueError("require buckets >= multi_hash >=1")
        if self.blocks != 1:
            raise ValueError("this bounded adapter currently supports one explicit BDGL block")
        if self.preprocessing not in ("bdgl2", "sample_database_only"):
            raise ValueError("unknown Slicer preprocessing mode")
        if not isinstance(self.interface_test_mode, bool) or not isinstance(self.force_kernel_iterations, bool):
            raise ValueError("interface test switches must be booleans")
        if (self.preprocessing == "sample_database_only" or self.force_kernel_iterations) and not self.interface_test_mode:
            raise ValueError("sampling-only or forced kernel iterations require explicit interface_test_mode")


class FixedBabaiBackend:
    """One explicitly initialized private GSO reused for all E targets."""

    def __init__(self, working, config=None, *, _transforms=False):
        started = time.perf_counter()
        if config is not None and not isinstance(config, dict):
            raise ValueError("Babai backend config must be a mapping")
        config = {} if config is None else config
        self.rows, self.gso = _inputs(working, config, transforms=_transforms)
        self.dim = len(self.rows)
        self.prepared = False
        self.metrics = {"preprocess_attempt_count": 0, "preprocess_count": 0, "slicer_kernel_calls": 0,
                        "slicer_kernel_completed_calls": 0,
                        "babai_precheck_target_count": 0, "slicer_target_count": 0,
                        "babai_decode_target_count": 0, "projection_babai_target_count": 0,
                        "babai_decode_completed_target_count": 0, "babai_lift_call_count": 0}
        self.metadata = {"method": "E", "gso": deepcopy(working.get("gso_metadata", {})),
                         "basis_changed": False, "hidden_reduction_calls": 0,
                         "constructor_seconds": time.perf_counter() - started,
                         "backend_return_type": "integer basis coefficients converted to ambient integer vector"}
        self._mapper = SlicerDecoder.__new__(SlicerDecoder)
        self._mapper.dim = self.dim
        self.call_evidence = []

    def _check_basis(self):
        if _rows(self.gso) != self.rows:
            raise RuntimeError("fixed-basis backend changed the integer basis")

    def preprocess(self):
        if self.prepared:
            raise RuntimeError("preprocessing can run only once per backend")
        self._check_basis()
        self.prepared = True
        return {"seconds": 0.0, "preprocess_count": 0}

    def decode(self, targets, bounds_sq, backend_integer_ids):
        if not self.prepared:
            raise RuntimeError("call preprocess once before decoding")
        self._check_basis()
        prepared_at = time.perf_counter()
        targets, bounds, ids = self._mapper._validate_batch(targets, bounds_sq, backend_integer_ids)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in ids):
            raise ValueError("backend IDs must be explicit integers")
        prepare_seconds = time.perf_counter() - prepared_at
        candidates, completed, errors = [], [], []
        evidence = []
        decode_seconds = lift_seconds = 0.0
        for target, bound, external_id in zip(targets, bounds, ids):
            try:
                call = {"function": "GSO.Mat.babai", "backend_integer_id": external_id,
                        "target": list(target), "returned_normally": False}
                self.call_evidence.append(call)
                self.metrics["babai_decode_target_count"] += 1
                started = time.perf_counter()
                try:
                    coefficients = [_integer(x) for x in self.gso.babai(target)]
                finally:
                    call["seconds"] = time.perf_counter() - started
                    decode_seconds += call["seconds"]
                call["returned_normally"] = True
                self.metrics["babai_decode_completed_target_count"] += 1
                started = time.perf_counter()
                vector = _multiply_left(self.rows, coefficients)
                residual_sq = _residual_sq(target, vector)
                lift_seconds += time.perf_counter() - started
                if residual_sq <= bound:
                    candidates.append({"backend_integer_id": external_id, "v": vector,
                                       "coefficients": coefficients, "residual_sq": residual_sq,
                                       "output_origin": "babai_only", "slicer_invoked": False,
                                       "slicer_used_in_returned_candidate": False})
                completed.append(external_id)
            except Exception as exc:
                if not call["returned_normally"]:
                    call["error"] = f"{type(exc).__name__}: {exc}"
                errors.append({"backend_integer_id": external_id, "error": f"{type(exc).__name__}: {exc}"})
            evidence.append(deepcopy(call))
        self._check_basis()
        return {"candidates": candidates, "completed_ids": completed,
                "errored_ids": [row["backend_integer_id"] for row in errors],
                "cancelled_ids": [], "errors": errors, "projection_groups": [],
                "timings": {"target_prepare_seconds": prepare_seconds, "decode_seconds": decode_seconds,
                            "lift_seconds": lift_seconds}, "metrics": deepcopy(self.metrics),
                "call_evidence": evidence}


def _without_constructor_lll(siever_class):
    """Use Python dispatch of the pinned constructor's explicit self.lll call."""
    class FixedBasisSiever(siever_class):
        def __init__(self, *args, **kwargs):
            self.constructor_lll_suppressed = []
            self._during_fixed_basis_constructor = True
            try:
                super().__init__(*args, **kwargs)
            finally:
                self._during_fixed_basis_constructor = False
            if len(self.constructor_lll_suppressed) != 1:
                raise RuntimeError("pinned Siever constructor reduction interception changed")

        def lll(self, left, right):
            if not self._during_fixed_basis_constructor:
                raise RuntimeError("LLL forbidden in fixed-basis decoder")
            self.constructor_lll_suppressed.append({"left": left, "right": right,
                                                   "action": "suppressed_without_calling_LLL"})

        def split_lll(self, *args, **kwargs):
            raise RuntimeError("split LLL forbidden in fixed-basis decoder")

    return FixedBasisSiever


class RecordingMapper(SlicerDecoder):
    """Record the frozen projection's target translation without changing it.

    The temporary projected input is ``t_used - shift`` with an exact lattice
    shift certified by its integer coefficients in the unchanged row basis.
    Groups and final lifts retain each member's original ``t_used``.  This is
    target preparation, not another basis transform or an input-package edit.
    Only these pure mapping methods are used; the reducing constructor is never
    invoked.
    """

    def _project_reduced(self, target):
        start = self.dim - self.sieve_dim
        tail_gso = list(self.G.from_canonical(target))[start:]
        tail_coefficients = [_integer(value) for value in self.G.babai(tail_gso, start=start, gso=True)]
        full_coefficients = [0] * start + tail_coefficients
        if len(full_coefficients) != self.dim:
            raise RuntimeError("tail Babai returned an unexpected coefficient dimension")
        shift = _multiply_left(self.rows, full_coefficients)
        shifted = [value - offset for value, offset in zip(target, shift)]
        projected = list(self.G.from_canonical(shifted))[-self.sieve_dim:]
        normalized = [float(value) * scale for value, scale in zip(projected, self._sqrt_r_over_gh)]
        canonical_projection, sign = _signed_float32_key(normalized)
        self._projection_conversion_sequence.append({
            "kind": "tail_babai_lattice_target_translation",
            "original_target_used": list(target), "tail_start": start,
            "full_basis_coefficients": full_coefficients,
            "ambient_shift": shift, "projected_input": shifted,
            "normalized_projection_float64": normalized,
            "signed_canonical_projection_float32": list(canonical_projection),
            "canonical_sign": sign,
            "translation_relation": "projected_input = original_target_used - ambient_shift",
            "exact_lattice_translation_verified_by_coefficients": True,
            "basis_transform": False, "input_package_target_mutated": False,
            "final_lift_target": "original_target_used",
        })
        return normalized

    def _groups(self, targets, bounds, ids):
        self._projection_conversion_sequence = []
        groups = super()._groups(targets, bounds, ids)
        if len(self._projection_conversion_sequence) != len(ids):
            raise RuntimeError("projection conversion records do not match external target count")
        self.projection_conversions_by_id = {
            external_id: deepcopy(conversion)
            for external_id, conversion in zip(ids, self._projection_conversion_sequence)
        }
        return groups


class FixedSlicerBackend(FixedBabaiBackend):
    """One private lattice database, per-call target DB, exact per-ID lifts.

    Returned ``v`` is always an ambient lattice vector, never a projection or
    basis coefficient list.  Each group member is lifted using its own target.
    An explicitly rejected target insertion is an error, not a completed decode.
    """

    def __init__(self, working, config=None):
        if isinstance(config, SlicerConfig):
            slicer_config, common = config, {}
        else:
            common = dict(config or {})
            slicer_values = {name: value for name, value in common.items()
                             if name not in ("float_type", "precision_bits")}
            if "force_kernel_for_interface" in slicer_values:
                if "force_kernel_iterations" in slicer_values:
                    raise ValueError("duplicate force-kernel interface option")
                slicer_values["force_kernel_iterations"] = slicer_values.pop("force_kernel_for_interface")
            slicer_config = SlicerConfig(**slicer_values)
        # Validate the cheap dimension gate before importing the real backend.
        if slicer_config.sieve_dim > len(working["rows"]):
            raise ValueError("Slicer requires explicit 28 <= sieve_dim <= fixed basis dimension")
        super().__init__(working, common, _transforms=True)
        self.config = slicer_config
        if not isinstance(self.config, SlicerConfig) or self.config.sieve_dim > self.dim:
            raise ValueError("Slicer requires explicit 28 <= sieve_dim <= fixed basis dimension")
        self.working = working
        self.metadata.update(method="D", config=asdict(self.config),
                             slicer_numeric_types={"GSO_export": "float64", "target_projection_storage": "float32",
                                                   "integer_lifting": "Python arbitrary precision integers"},
                             strict_fixed_basis=True, additional_basis_transform=False,
                             backend_target_conversion={
                                 "kind": "recorded per-external tail Babai lattice translation",
                                 "basis_transform": False, "input_package_target_mutated": False,
                                 "final_lift_uses": "each original t_used, not the shifted projection input"},
                             preprocess_count_semantics={
                                 "preprocess_attempt_count": "D preprocess entries after the prepared guard, including failed attempts",
                                 "preprocess_count": "normally completed shared lattice database constructions"},
                             babai_roles={"independent_public_success_precheck": False,
                                          "target_projection": "tail Babai shift, not a public-validity precheck",
                                          "final_lift": "full ambient Babai using the returned projected residual"})
        self.call_evidence = []
        self._call_number = 0

    def preprocess(self):
        if self.prepared:
            raise RuntimeError("preprocessing can run only once per backend")
        self.metrics["preprocess_attempt_count"] += 1
        self._check_basis()
        started = time.perf_counter()
        from fpylll import FPLLL, GSO, IntegerMatrix
        from fpylll.util import gaussian_heuristic
        from g6k.siever import Siever
        from g6k.siever_params import SieverParams
        from g6k.slicer import RandomizedSlicer

        metadata = self.metadata["gso"]
        if self.gso.float_type == "mpfr" and FPLLL.get_precision() != metadata.get("precision_bits"):
            raise RuntimeError("live MPFR GSO precision differs from explicit fixed decoder precision")
        conversion_started = time.perf_counter()
        if not (self.gso.transform_enabled and self.gso.inverse_transform_enabled):
            # Pinned Siever requires identity transform bookkeeping.  Recreate
            # only the same integer matrix/GSO type, without altering precision.
            matrix = IntegerMatrix.from_matrix(self.rows, int_type="mpz")
            self.gso = GSO.Mat(matrix, float_type=self.gso.float_type,
                               U=IntegerMatrix.identity(self.dim, int_type="mpz"),
                               UinvT=IntegerMatrix.identity(self.dim, int_type="mpz"))
            self.gso.update_gso()
            self.working["gso"] = self.gso
            self.metadata["gso_transform_bookkeeping_recreated"] = True
        else:
            self.metadata["gso_transform_bookkeeping_recreated"] = False
        self.metadata["gso_conversion_seconds_nested_in_preprocess"] = time.perf_counter() - conversion_started
        cfg = self.config
        params = SieverParams(threads=1, otf_lift=False, saturation_ratio=0.5,
                              saturation_radius=1.32, db_size_base=1.1,
                              db_size_factor=cfg.db_size / 1.1**cfg.sieve_dim,
                              bdgl_multi_hash=cfg.multi_hash, bdgl_min_bucket_size=1)
        fixed_siever = _without_constructor_lll(Siever)
        self.siever = fixed_siever(self.gso, params, seed=cfg.seed)
        if cfg.sieve_dim > self.siever.max_sieving_dim:
            raise ValueError("requested projection exceeds the compiled backend cap")
        self._check_basis()
        self.siever.initialize_local(self.dim - cfg.sieve_dim, self.dim - cfg.sieve_dim, self.dim)
        self.siever.grow_db(cfg.db_size)
        if cfg.preprocessing == "bdgl2":
            self.siever.bdgl_sieve(blocks=cfg.blocks, buckets=cfg.buckets,
                                   check_saturation=False)
        if not 128 <= len(self.siever) <= cfg.db_size + 1:
            raise RuntimeError("lattice database did not respect its explicit bounded size")
        self._check_basis()
        mapper = RecordingMapper.__new__(RecordingMapper)
        mapper.dim, mapper.sieve_dim = self.dim, cfg.sieve_dim
        mapper.G, mapper.rows = self.gso, self.rows
        mapper.gh_sub = float(gaussian_heuristic(self.gso.r()[-cfg.sieve_dim:]))
        if not math.isfinite(mapper.gh_sub) or mapper.gh_sub <= 0:
            raise RuntimeError("unusable projected Gaussian heuristic scale")
        mapper._sqrt_r_over_gh = [math.sqrt(float(r) / mapper.gh_sub) for r in self.gso.r()[-cfg.sieve_dim:]]
        mapper._sqrt_gh_over_r = [1.0 / value for value in mapper._sqrt_r_over_gh]
        self._mapper = mapper
        self._slicer_factory = RandomizedSlicer
        self.prepared = True
        self.metrics["preprocess_count"] = 1
        self.metadata.update(constructor_lll_interception=deepcopy(self.siever.constructor_lll_suppressed),
                             lattice_db_size=len(self.siever), actual_projection_dimension=cfg.sieve_dim,
                             compiled_projection_cap=int(self.siever.max_sieving_dim), nthreads=1,
                             preprocessing=cfg.preprocessing,
                             preprocessing_seconds=time.perf_counter() - started,
                             kernel_iteration_limit=cfg.max_iterations,
                             target_db_entries_per_accepted_projection=cfg.nrand,
                             randomized_entries_per_accepted_projection=cfg.nrand - 1,
                             randomization_draw_attempts="not_observable",
                             interface_test_only=cfg.interface_test_mode)
        return {"seconds": self.metadata["preprocessing_seconds"], "preprocess_count": 1}

    def decode(self, targets, bounds_sq, backend_integer_ids):
        if not self.prepared:
            raise RuntimeError("call preprocess once before decoding")
        self._check_basis()
        started = time.perf_counter()
        targets, bounds, ids = self._mapper._validate_batch(targets, bounds_sq, backend_integer_ids)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in ids):
            raise ValueError("backend IDs must be explicit integers")
        groups = self._mapper._groups(targets, bounds, ids)
        self.metrics["projection_babai_target_count"] += len(ids)
        self._call_number += 1
        cfg = self.config
        slicer = self._slicer_factory(self.siever, seed=(cfg.seed + self._call_number) % 2**32)
        slicer.set_nthreads(1)
        slicer.set_max_slicer_interations(cfg.max_iterations)
        accepted, errors, group_records = [], [], []
        for ordinal, group in enumerate(groups):
            before = int(slicer.stats["cdb_t-size:"])
            slicer.grow_db_with_target(group["projection"], n_per_target=cfg.nrand)
            after = int(slicer.stats["cdb_t-size:"])
            record = {"projection_group": ordinal, "projection": list(group["projection"]),
                      "members": [{"backend_integer_id": member["target_id"], "sign": member["sign"],
                                   "actual_target": list(member["target"]),
                                   "projection_conversion": deepcopy(
                                       self._mapper.projection_conversions_by_id[member["target_id"]])}
                                  for member in group["members"]],
                      "insertion_before": before, "insertion_after": after, "internal_id": None}
            if after == before + cfg.nrand:
                record["internal_id"] = len(accepted)
                accepted.append(group)
            elif after == before:
                errors.extend({"backend_integer_id": member["target_id"],
                               "error": "kernel_uid_zero_or_collision_in_target_insertion"} for member in group["members"])
            else:
                raise RuntimeError("unexpected target database growth; cannot bind target IDs")
            group_records.append(record)
        prepare_seconds = time.perf_counter() - started
        evidence, candidates = [], []
        decode_seconds = lift_seconds = 0.0
        if accepted:
            accepted_ids = [member["target_id"] for group in accepted for member in group["members"]]
            normalized_bounds = [float(member["bound_sq"]) / self._mapper.gh_sub for group in accepted for member in group["members"]]
            index, saturation = _safe_saturation(int(slicer.stats["cdb_t-size:"]), len(accepted))
            slicer.set_Nt(len(accepted))
            slicer.set_saturation_scalar(saturation)
            slicer.set_proj_error_bound(0.0 if cfg.force_kernel_iterations else min(normalized_bounds) * (1 + 1e-4))
            before_stats = dict(slicer.stats)
            call = {"function": "RandomizedSlicer.bdgl_like_sieve", "call_number": self._call_number,
                    "external_ids": accepted_ids, "target_db_size": int(slicer.stats["cdb_t-size:"]),
                    "saturation_index": index, "buckets": cfg.buckets, "blocks": cfg.blocks,
                    "multi_hash": cfg.multi_hash, "max_iterations": cfg.max_iterations,
                    # nrand is the pinned API's n_per_target: one original
                    # entry plus nrand-1 randomized entries, not draw attempts.
                    "nrand": cfg.nrand,
                    "target_db_entries_per_accepted_projection": cfg.nrand,
                    "randomized_entries_per_accepted_projection": cfg.nrand - 1,
                    "randomization_draw_attempts": "not_observable",
                    "forced_interface_iterations": cfg.force_kernel_iterations,
                    "stats_before": before_stats, "returned_normally": False}
            # Evidence is recorded before entering C++, so a caller-provided
            # process checkpoint can preserve invocation on abnormal exits.
            self.call_evidence.append(call)
            self.metrics["slicer_kernel_calls"] += 1
            self.metrics["slicer_target_count"] += len(accepted_ids)
            started = time.perf_counter()
            try:
                slicer.bdgl_like_sieve(cfg.buckets, cfg.blocks, cfg.multi_hash, False)
            except Exception as exc:
                call["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                decode_seconds = time.perf_counter() - started
                call["seconds"] = decode_seconds
            call["returned_normally"] = True
            self.metrics["slicer_kernel_completed_calls"] += 1
            after_stats = dict(slicer.stats)
            call.update(returned_normally=True, seconds=decode_seconds, stats_after=after_stats,
                        observable_iterations=(after_stats.get("itercount_slicer")
                                               if "itercount_slicer" in after_stats else None))
            evidence.append(deepcopy(call))
            seen, lifted_ids = set(), set()
            lift_error_ids = set()
            started = time.perf_counter()
            for residual, internal_id in slicer.itervalues_cdb_t(return_with_index=True):
                internal_id = _integer(internal_id)
                if not 0 <= internal_id < len(accepted):
                    raise RuntimeError("Slicer returned an unknown internal projection-group ID")
                if len(residual) != cfg.sieve_dim or not all(math.isfinite(float(x)) for x in residual):
                    raise RuntimeError("Slicer returned malformed projected residual")
                for member in accepted[internal_id]["members"]:
                    member_id = member["target_id"]
                    if member_id in lift_error_ids:
                        continue
                    try:
                        self.metrics["babai_lift_call_count"] += 1
                        coefficients, vector, distance_sq = self._mapper._lift(
                            member["target"], [member["sign"] * float(x) for x in residual])
                        lifted_ids.add(member_id)
                    except Exception as exc:
                        lift_error_ids.add(member_id)
                        errors.append({"backend_integer_id": member_id,
                                       "error": f"{type(exc).__name__}: {exc}"})
                        continue
                    identity = (member["target_id"], tuple(vector))
                    if distance_sq <= member["bound_sq"] and identity not in seen:
                        seen.add(identity)
                        candidates.append({"backend_integer_id": member["target_id"], "v": vector,
                                           "coefficients": coefficients, "residual_sq": distance_sq,
                                           "output_origin": "slicer_tail_with_babai_lift", "slicer_invoked": True,
                                           # The residual passed through the kernel, but it may
                                           # have been retained unchanged from target insertion.
                                           "slicer_used_in_returned_candidate": "unknown"})
            lift_seconds = time.perf_counter() - started
            completed = [value for value in accepted_ids if value in lifted_ids and value not in lift_error_ids]
            missing = [value for value in accepted_ids if value not in lifted_ids and value not in lift_error_ids]
        else:
            completed, missing = [], []
        self._check_basis()
        return {"candidates": candidates, "completed_ids": completed,
                "errored_ids": [row["backend_integer_id"] for row in errors], "cancelled_ids": [],
                "errors": errors, "projection_groups": group_records,
                "missing_external_results": missing,
                "timings": {"target_prepare_seconds": prepare_seconds,
                            "decode_seconds": decode_seconds, "lift_seconds": lift_seconds},
                "metrics": deepcopy(self.metrics), "call_evidence": evidence}


def backend_factory(working, method, config=None):
    if method == "E":
        return FixedBabaiBackend(working, config)
    if method == "D":
        return FixedSlicerBackend(working, config)
    raise ValueError("fixed-basis backend method must be D or E")


build_backend = backend_factory
