"""Shared, explicitly MPFR reduction protocol; no reduction occurs on import.

The executor is deliberately separate from input construction.  Only explicit
calls to execute_reduction import fpylll or perform LLL/BKZ.  An empty schedule
means LLL only, not a frozen choice of beta for the main experiment.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import json
import math
from pathlib import Path
import platform
import time


IMPLEMENTATION = "fpylll.algorithms.bkz2.BKZReduction.tour"
STRATEGY = "fpylll_default_extend_unpruned"


def _int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("ascii")).hexdigest()


def _utc():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReductionStage:
    beta: int
    requested_tours: int

    def __post_init__(self):
        if not _int(self.beta) or self.beta < 2:
            raise ValueError("beta must be an integer >= 2")
        if not _int(self.requested_tours) or self.requested_tours < 1:
            raise ValueError("requested_tours must be a positive integer")


@dataclass(frozen=True)
class ReductionProtocol:
    schedule: tuple[ReductionStage, ...] = ()
    float_type: str = "mpfr"
    precision_bits: int = 192
    lll_delta: float = 0.99
    lll_eta: float = 0.51
    lll_method: str = "proved"
    lll_flags: int = 0
    bkz_flags: int = 0
    gso_flags: int = 0
    tour_semantics: str = "strict"
    auto_abort: bool = False
    seed: int = 10001
    threads: int = 1
    strategy: str = STRATEGY

    def __post_init__(self):
        if not isinstance(self.schedule, (list, tuple)):
            raise ValueError("schedule must preserve explicit order")
        schedule = tuple(self.schedule)
        if any(not isinstance(s, ReductionStage) for s in schedule):
            raise ValueError("schedule entries must be ReductionStage objects")
        if any(a.beta >= b.beta for a, b in zip(schedule, schedule[1:])):
            raise ValueError("progressive schedule beta values must strictly increase")
        object.__setattr__(self, "schedule", schedule)
        if self.float_type != "mpfr":
            raise ValueError("only explicit MPFR execution is supported; no precision fallback")
        if not _int(self.precision_bits) or self.precision_bits < 64:
            raise ValueError("MPFR precision_bits must be an integer >= 64")
        if self.lll_method != "proved":
            raise ValueError("only explicit proved initial LLL is supported")
        for name in ("lll_flags", "bkz_flags", "gso_flags"):
            if not _int(getattr(self, name)) or getattr(self, name) != 0:
                raise ValueError(name + " supports only the explicit default 0")
        for name in ("lll_delta", "lll_eta"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(name + " must be finite numeric")
        if not 0.25 < self.lll_delta < 1 or not 0.5 <= self.lll_eta < math.sqrt(self.lll_delta):
            raise ValueError("unsupported LLL delta/eta range")
        if self.tour_semantics != "strict":
            raise ValueError("only strict individual tours are executable; MAX_LOOPS is not strict tours")
        if self.auto_abort is not False:
            raise ValueError("auto_abort is unsupported; clean never skips a requested strict tour")
        if not _int(self.seed) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if not _int(self.threads) or self.threads < 1:
            raise ValueError("threads must be a positive integer")
        if self.strategy != STRATEGY:
            raise ValueError("unsupported strategy source")


def validate_protocol(protocol, dimension=None):
    if not isinstance(protocol, ReductionProtocol):
        raise ValueError("an explicit ReductionProtocol is required")
    if dimension is not None:
        if not _int(dimension) or dimension < 1:
            raise ValueError("dimension must be a positive integer")
        if any(s.beta > dimension for s in protocol.schedule):
            raise ValueError("requested beta exceeds the actual lattice dimension")
    return protocol


def protocol_manifest(protocol):
    validate_protocol(protocol)
    result = asdict(protocol)
    result["schedule"] = [asdict(s) for s in protocol.schedule]
    result.update({
        "schema": "ntru-shared-reduction-protocol-v1",
        "bkz_implementation": IMPLEMENTATION,
        "initial_lll_implementation": "fpylll.LLL.reduction",
        "bkz_internal_lll": "fpylll.LLL.Reduction on the same explicitly MPFR GSO",
        "strategy_resolution": "backend metadata records actual strategy file bytes and SHA256",
        "strategy_extension": "above loaded strategy maximum: Strategy(beta, [], []), unpruned, no preprocessing blocks",
        "strict_tour_definition": "one completed explicit BKZReduction.tour call; clean does not skip later calls",
        "early_termination": "exception or external termination only; no public-success callback in this interface",
        "decoder_internal_precision": "separate future decoder configuration, not specified by this protocol",
        "main_experiment_schedule_frozen": False,
        "historical_anchor": "beta32tour2 was a progressive warm-start success, not a measured cold-start minimum",
    })
    return result


def protocol_from_manifest(document):
    """Validate a saved complete protocol without importing a backend."""
    if not isinstance(document, dict):
        raise ValueError("protocol manifest must be an object")
    fields = set(ReductionProtocol.__dataclass_fields__)
    if any(key not in document for key in fields):
        raise ValueError("protocol manifest is missing required fields")
    schedule = document["schedule"]
    if (not isinstance(schedule, list)
            or any(not isinstance(s, dict) or set(s) != {"beta", "requested_tours"} for s in schedule)):
        raise ValueError("invalid saved schedule")
    values = {key: document[key] for key in fields}
    values["schedule"] = tuple(ReductionStage(**s) for s in schedule)
    protocol = ReductionProtocol(**values)
    if document != protocol_manifest(protocol):
        raise ValueError("protocol manifest contains unknown, missing or inconsistent fields")
    return protocol


def _requested_stages(protocol):
    rows = [{"stage_id": "lll", "kind": "lll", "beta": None, "tour": None}]
    for s in protocol.schedule:
        rows.extend({"stage_id": f"bkz_b{s.beta:03d}_t{tour:02d}", "kind": "bkz",
                     "beta": s.beta, "tour": tour}
                    for tour in range(1, s.requested_tours + 1))
    return rows


def new_execution_record(protocol, *, prior=None):
    """Create a journal with requested work; it never fabricates completions.

    prior is a complete previous executor record.  Integer basis/transform
    proof is additionally required by execute_reduction before resuming it.
    """
    manifest = protocol_manifest(protocol)
    stages, cumulative, lineage = [], 0.0, []
    if prior is not None:
        if not isinstance(prior, dict) or prior.get("schema") != "ntru-reduction-execution-v1":
            raise ValueError("invalid checkpoint execution record")
        if prior.get("status") != "completed" or prior.get("active_stage") is not None:
            raise ValueError("resume requires a checkpoint at a completed protocol boundary")
        previous_protocol = prior.get("requested", {}).get("protocol", {})
        validate_execution_record(prior, protocol_from_manifest(previous_protocol))
        prior_settings = {k: v for k, v in previous_protocol.items() if k != "schedule"}
        settings = {k: v for k, v in manifest.items() if k != "schedule"}
        if prior_settings != settings:
            raise ValueError("checkpoint reduction settings differ; explicit separate lineage is required")
        stages = json.loads(json.dumps(prior.get("stages", []), allow_nan=False))
        requested = _requested_stages(protocol)
        if len(stages) > len(requested):
            raise ValueError("new schedule removes previously completed stages")
        for actual, expected in zip(stages, requested):
            if any(actual.get(k) != v for k, v in expected.items()) or actual.get("status") != "completed":
                raise ValueError("checkpoint stages are not an exact prefix of the new progressive schedule")
        cumulative = prior.get("cumulative_seconds")
        if isinstance(cumulative, bool) or not isinstance(cumulative, (int, float)) or not math.isfinite(cumulative) or cumulative < 0:
            raise ValueError("invalid checkpoint cumulative cost")
        measured = sum(s.get("elapsed_seconds", 0) for s in stages)
        if cumulative < measured:
            raise ValueError("checkpoint cumulative cost is smaller than its completed reduction cost")
        lineage = list(prior.get("lineage", [])) + [{
            "record_sha256": _hash(prior), "prior_cumulative_seconds": cumulative,
            "prior_completed_stage_count": len(stages),
            "rng_state": "not_observable; seed is explicitly reset on each executor invocation",
        }]
    return {
        "schema": "ntru-reduction-execution-v1", "status": "not_started",
        "requested": {"protocol": manifest, "stages": _requested_stages(protocol),
                      "total_tours": sum(s.requested_tours for s in protocol.schedule)},
        "actual": {"completed_tours": sum(s["kind"] == "bkz" for s in stages),
                   "completed_lll_calls": sum(s["kind"] == "lll" for s in stages),
                   "backend_internal_iterations": "not_observable"},
        "stages": stages, "active_stage": None,
        "started_utc": None, "finished_utc": None, "termination_reason": None,
        "elapsed_seconds": 0.0, "prior_cumulative_seconds": cumulative,
        "cumulative_seconds": cumulative, "lineage": lineage,
        "timing_scope": "executor wall: imports, strategy resolution, matrix/GSO setup, all completed and failed reduction work, exact proof; excludes input construction, serialization and decoding",
    }


def validate_execution_record(record, protocol, *, original_basis=None, reduced_basis=None, transform=None):
    """Reject contradictory saved requested/actual records, without a backend.

    This is a consistency check, not proof that arbitrary supplied JSON was
    really measured.  The executor's source/runtime provenance and exact
    integer certificate remain separate requirements.
    """
    validate_protocol(protocol)
    if not isinstance(record, dict) or record.get("schema") != "ntru-reduction-execution-v1":
        raise ValueError("unsupported reduction execution record")
    if record.get("status") not in ("completed", "error") or record.get("active_stage") is not None:
        raise ValueError("only a finished executor record is attachable")
    def fields(value, allowed, label, required=()):
        if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
            raise ValueError("unknown or missing " + label + " fields")
    fields(record, {
        "schema", "status", "requested", "actual", "stages", "active_stage", "started_utc",
        "finished_utc", "termination_reason", "elapsed_seconds", "prior_cumulative_seconds",
        "cumulative_seconds", "lineage", "timing_scope", "original_basis_sha256",
        "reduced_basis_sha256", "transform_sha256", "backend", "gso_setup_seconds", "error",
        "failed_stage", "exact_lattice_verification",
    }, "execution record")
    for key in ("started_utc", "finished_utc"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError("finished execution requires observed start and finish timestamps")
    forbidden = {"f", "g", "reference_f", "reference_g", "truth", "reference", "coverage", "secret", "private"}
    def check_public(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (not isinstance(key, str) or key.lower() in forbidden
                        or any(token in key.lower() for token in ("reference_", "truth", "coverage", "secret", "private"))):
                    raise ValueError("execution record must contain only public reduction metadata")
                check_public(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                check_public(child)
    check_public(record)
    if record.get("synthetic_fixture") is True or record.get("timing_source") == "synthetic_test_fixture_not_a_backend_measurement":
        raise ValueError("synthetic test records are not production backend measurements")
    expected = _requested_stages(protocol)
    requested = record.get("requested", {})
    fields(requested, {"protocol", "stages", "total_tours"}, "requested", {"protocol", "stages", "total_tours"})
    if (_hash(requested.get("protocol")) != _hash(protocol_manifest(protocol))
            or _hash(requested.get("stages")) != _hash(expected)
            or not _int(requested.get("total_tours"))
            or requested["total_tours"] != sum(s.requested_tours for s in protocol.schedule)):
        raise ValueError("requested reduction protocol/stages differ from the package plan")
    stages = record.get("stages")
    if not isinstance(stages, list) or len(stages) > len(expected):
        raise ValueError("invalid completed stage history")
    stage_cost = 0.0
    for s, request in zip(stages, expected):
        fields(s, {"stage_id", "kind", "beta", "tour", "started_utc", "status", "finished_utc",
                   "elapsed_seconds", "clean"}, "completed stage")
        if not isinstance(s, dict) or s.get("status") != "completed":
            raise ValueError("only completed stages belong in the completed history")
        if _hash({k: s.get(k) for k in request}) != _hash(request):
            raise ValueError("history is not an exact prefix of the requested progressive schedule")
        value = s.get("elapsed_seconds")
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid completed stage timing")
        if any(not isinstance(s.get(k), str) or not s[k] for k in ("started_utc", "finished_utc")):
            raise ValueError("stage start and completion observations are required")
        if s.get("clean") is not None and not isinstance(s["clean"], bool):
            raise ValueError("clean must be a backend boolean observation or null")
        stage_cost += value
    actual = record.get("actual", {})
    fields(actual, {"completed_tours", "completed_lll_calls", "backend_internal_iterations",
                    "float_type", "precision_bits", "precision_observation", "threads", "seed_set",
                    "rng_internal_state", "initial_lll_method_argument", "strategy_sha256", "gso_float_type"}, "actual")
    lll_count = sum(s["kind"] == "lll" for s in stages)
    tours = sum(s["kind"] == "bkz" for s in stages)
    if (not _int(actual.get("completed_tours")) or actual["completed_tours"] != tours
            or not _int(actual.get("completed_lll_calls")) or actual["completed_lll_calls"] != lll_count):
        raise ValueError("actual completion counts contradict completed stages")
    for key in ("elapsed_seconds", "prior_cumulative_seconds", "cumulative_seconds"):
        value = record.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid execution wall timing")
    if record["cumulative_seconds"] + 1e-9 < record["prior_cumulative_seconds"] + record["elapsed_seconds"]:
        raise ValueError("cumulative cost discards the current or earlier invocation")
    if stage_cost > record["cumulative_seconds"] + 1e-9:
        raise ValueError("completed stage time exceeds all charged execution time")
    if record["status"] == "completed":
        if len(stages) != len(expected) or record.get("termination_reason") != "requested_schedule_completed":
            raise ValueError("completed status requires every requested strict tour to finish")
    elif record.get("termination_reason") not in ("backend_error", "exact_lattice_proof_failed"):
        raise ValueError("unknown executor error termination reason")
    for key in ("original_basis_sha256", "reduced_basis_sha256", "transform_sha256"):
        digest = record.get(key)
        if key != "original_basis_sha256" and not stages:
            if digest is not None:
                raise ValueError("no completed reduction means no reduced basis/transform digest")
        elif (not isinstance(digest, str) or len(digest) != 64
              or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("invalid bound matrix digest: " + key)
    for matrix, key in ((original_basis, "original_basis_sha256"), (reduced_basis, "reduced_basis_sha256"),
                        (transform, "transform_sha256")):
        if matrix is not None and _hash(_matrix(matrix)) != record.get(key):
            raise ValueError("execution record matrix digest does not match supplied " + key)
    if stages or "precision_bits" in actual:
        if (actual.get("float_type") != "mpfr" or actual.get("precision_bits") != protocol.precision_bits
                or not _int(actual.get("precision_bits")) or actual.get("threads") != protocol.threads
                or not _int(actual.get("threads"))):
            raise ValueError("actual execution precision/type/threads differ from requested settings")
        backend = record.get("backend", {})
        metadata_fields = {"python", "python_implementation", "fpylll_version", "fplll_version",
                           "available_float_types", "strategy", "source_hashes", "precision_before_execution",
                           "threads_before_execution", "backend_probe_did_reduce"}
        fields(backend, metadata_fields, "backend metadata", metadata_fields)
        if backend["backend_probe_did_reduce"] is not False:
            raise ValueError("backend metadata probe cannot claim reduction")
        if (any(not isinstance(backend[k], str) or not backend[k]
                for k in ("python", "python_implementation", "fpylll_version", "fplll_version"))
                or not isinstance(backend["available_float_types"], list)
                or any(not isinstance(t, str) for t in backend["available_float_types"])
                or any(not _int(backend[k]) or backend[k] < 1
                       for k in ("precision_before_execution", "threads_before_execution"))):
            raise ValueError("invalid observed backend version/type/precision metadata")
        strategy = backend["strategy"]
        strategy_fields = {"kind", "path", "sha256", "byte_count", "loaded_max_beta", "above_max"}
        fields(strategy, strategy_fields, "strategy metadata", strategy_fields)
        if (any(not isinstance(strategy[k], str) or not strategy[k] for k in ("path", "above_max"))
                or not _int(strategy["byte_count"]) or strategy["byte_count"] < 0):
            raise ValueError("invalid strategy provenance metadata")
        sources = backend["source_hashes"]
        source_names = {"fpylll.algorithms.bkz", "fpylll.algorithms.bkz2", "fpylll.fplll.lll", "fpylll.fplll.gso"}
        fields(sources, source_names, "backend source hashes", source_names)
        for source in sources.values():
            fields(source, {"path", "sha256"}, "backend source", {"path", "sha256"})
            if (not isinstance(source["path"], str) or not source["path"]
                    or not isinstance(source["sha256"], str) or len(source["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in source["sha256"])):
                raise ValueError("invalid backend source path/digest")
        digest = strategy.get("sha256", "")
        if (strategy.get("kind") != protocol.strategy or not isinstance(digest, str)
                or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or actual.get("strategy_sha256") != digest
                or not _int(strategy.get("loaded_max_beta")) or strategy["loaded_max_beta"] < 0):
            raise ValueError("actual strategy digest/source is missing or inconsistent")
    if tours and actual.get("gso_float_type") != "mpfr":
        raise ValueError("completed BKZ tours require the explicitly observed MPFR GSO")
    failed = record.get("failed_stage")
    if failed is not None:
        fields(failed, {"stage_id", "kind", "beta", "tour", "started_utc", "status", "finished_utc",
                        "elapsed_seconds", "completed_tour", "internal_progress"}, "failed stage")
        if (record["status"] != "error" or len(stages) >= len(expected)
                or _hash({k: failed.get(k) for k in expected[len(stages)]}) != _hash(expected[len(stages)])
                or failed.get("status") != "failed" or failed.get("completed_tour") is not False
                or failed.get("internal_progress") != "not_observable"):
            raise ValueError("failed stage must remain distinct from completed tours")
        seconds = failed.get("elapsed_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("failed reduction work must have a measured nonnegative cost")
        if seconds > record["elapsed_seconds"] + 1e-9 or stage_cost + seconds > record["cumulative_seconds"] + 1e-9:
            raise ValueError("failed stage cost was dropped from current or cumulative execution cost")
        for key in ("started_utc", "finished_utc"):
            if not isinstance(failed.get(key), str) or not failed[key]:
                raise ValueError("failed stage requires observed start and finish timestamps")
    for lineage in record.get("lineage", []):
        fields(lineage, {"record_sha256", "prior_cumulative_seconds", "prior_completed_stage_count", "rng_state"}, "lineage")
    if "error" in record:
        fields(record["error"], {"type", "message"}, "backend error", {"type", "message"})
    if "exact_lattice_verification" in record:
        fields(record["exact_lattice_verification"], {"basis_convention", "certificate_relation", "determinant",
               "dimension", "original_determinant", "original_full_rank", "reduced_determinant",
               "rejection_reasons", "transform_relation", "unimodular", "verified"}, "exact lattice proof")
    return True


def backend_metadata(protocol):
    """Read installed backend/strategy metadata.  Does not run LLL/BKZ."""
    validate_protocol(protocol)
    import fpylll
    from fpylll import BKZ, FPLLL, config
    from fpylll.fplll.bkz_param import load_strategies_json
    strategy_path = Path(BKZ.DEFAULT_STRATEGY.decode() if isinstance(BKZ.DEFAULT_STRATEGY, bytes)
                         else BKZ.DEFAULT_STRATEGY)
    strategy_bytes = strategy_path.read_bytes()
    loaded = load_strategies_json(str(strategy_path))
    source_hashes = {}
    for name in ("fpylll.algorithms.bkz", "fpylll.algorithms.bkz2", "fpylll.fplll.lll", "fpylll.fplll.gso"):
        module = importlib.import_module(name)
        path = Path(module.__file__)
        source_hashes[name] = {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
    return {
        "python": platform.python_version(), "python_implementation": platform.python_implementation(),
        "fpylll_version": fpylll.__version__, "fplll_version": str(config.version),
        "available_float_types": list(config.float_types),
        "strategy": {"kind": STRATEGY, "path": str(strategy_path),
                     "sha256": sha256(strategy_bytes).hexdigest(), "byte_count": len(strategy_bytes),
                     "loaded_max_beta": len(loaded) - 1,
                     "above_max": "unpruned SVP; no preprocessing blocks"},
        "source_hashes": source_hashes,
        "precision_before_execution": FPLLL.get_precision(),
        "threads_before_execution": FPLLL.get_threads(),
        "backend_probe_did_reduce": False,
    }


def _matrix(rows):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("basis must be a nonempty square integer matrix")
    d = len(rows)
    if any(not isinstance(r, (list, tuple)) or len(r) != d or any(not _int(x) for x in r) for r in rows):
        raise ValueError("basis must be a square integer matrix, without float conversion")
    return [list(row) for row in rows]


def _identity(d):
    return [[int(i == j) for j in range(d)] for i in range(d)]


def _rows(matrix):
    return [[int(matrix[i, j]) for j in range(matrix.ncols)] for i in range(matrix.nrows)]


def execute_reduction(original_basis, protocol, *, checkpoint=None):
    """Explicitly execute MPFR initial LLL then each strict scheduled tour.

    On an ordinary backend error, returns the last COMPLETE stage's matrix and
    transform, plus the failed stage's actual charged time.  A killed process
    cannot claim a completed stage.  This function has no budget/supervisor;
    the caller must impose the authorized external process timeout.

    Continuation accepts only this executor's complete, exact-verified output.
    It preserves all earlier stages and wall cost.  RNG state is not serialized;
    the configured seed is explicitly reset and this is recorded in lineage.
    """
    from .exact_lattice import verify_unimodular_transform

    original = _matrix(original_basis)
    d = len(original)
    validate_protocol(protocol, d)
    tick = time.perf_counter()
    current, transform, previous = original, _identity(d), None
    if checkpoint is not None:
        if not isinstance(checkpoint, dict) or checkpoint.get("original_basis") != original:
            raise ValueError("checkpoint does not belong to this original basis")
        if checkpoint.get("lattice_status") != "reduced_verified":
            raise ValueError("historical or unverified checkpoint cannot be resumed as a verified input")
        current = _matrix(checkpoint.get("reduced_basis"))
        transform = _matrix(checkpoint.get("transform"))
        proof = verify_unimodular_transform(original, current, transform)
        if not proof["verified"]:
            raise ValueError("checkpoint exact lattice proof failed: " + str(proof["rejection_reasons"]))
        previous = checkpoint.get("record")
        previous_protocol = protocol_from_manifest(checkpoint.get("protocol"))
        validate_execution_record(previous, previous_protocol, original_basis=original,
                                  reduced_basis=current, transform=transform)
    record = new_execution_record(protocol, prior=previous)
    record.update({"status": "running", "started_utc": _utc(), "original_basis_sha256": _hash(original)})
    last_basis = [r[:] for r in current]
    last_transform = [r[:] for r in transform]
    active_tick = None
    try:
        record["backend"] = backend_metadata(protocol)
        from fpylll import FPLLL, GSO, IntegerMatrix, LLL, BKZ
        from fpylll.algorithms.bkz2 import BKZReduction
        from fpylll.fplll.bkz_param import Strategy, load_strategies_json
        FPLLL.set_threads(protocol.threads)
        FPLLL.set_random_seed(protocol.seed)
        FPLLL.set_precision(protocol.precision_bits)
        precision, threads = FPLLL.get_precision(), FPLLL.get_threads()
        if precision != protocol.precision_bits or threads != protocol.threads:
            raise RuntimeError("backend did not apply requested MPFR precision or thread count")
        record["actual"].update({"float_type": "mpfr", "precision_bits": precision,
                                 "precision_observation": "FPLLL.get_precision plus explicit LLL precision argument and MPFR GSO type",
                                 "threads": threads, "seed_set": protocol.seed,
                                 "rng_internal_state": "not_observable",
                                 "initial_lll_method_argument": protocol.lll_method,
                                 "strategy_sha256": record["backend"]["strategy"]["sha256"]})
        matrix = IntegerMatrix.from_matrix(current, int_type="mpz")
        U = IntegerMatrix.from_matrix(transform, int_type="mpz")
        strategies = list(load_strategies_json(record["backend"]["strategy"]["path"]))
        for beta in range(len(strategies), d + 1):
            strategies.append(Strategy(beta, [], []))
        gso, reducer = None, None
        requests = record["requested"]["stages"]
        for request in requests[len(record["stages"]):]:
            # GSO setup is charged to the executor, not to a claimed BKZ tour.
            if request["kind"] == "bkz" and reducer is None:
                setup = time.perf_counter()
                gso = GSO.Mat(matrix, U=U, float_type="mpfr", flags=protocol.gso_flags)
                if gso.float_type != "mpfr" or FPLLL.get_precision() != protocol.precision_bits:
                    raise RuntimeError("MPFR GSO precision/type mismatch; refusing fallback")
                gso.update_gso()
                reducer = BKZReduction(LLL.Reduction(gso, delta=protocol.lll_delta,
                                                     eta=protocol.lll_eta, flags=protocol.lll_flags))
                record["actual"]["gso_float_type"] = gso.float_type
                record["gso_setup_seconds"] = time.perf_counter() - setup
            record["active_stage"] = dict(request, started_utc=_utc(), status="started")
            active_tick = time.perf_counter()
            if request["kind"] == "lll":
                LLL.reduction(matrix, U=U, delta=protocol.lll_delta, eta=protocol.lll_eta,
                              method=protocol.lll_method, float_type="mpfr",
                              precision=protocol.precision_bits, flags=protocol.lll_flags)
                clean = None
            else:
                params = BKZ.Param(request["beta"], strategies=strategies,
                                   delta=protocol.lll_delta, flags=protocol.bkz_flags)
                clean = bool(reducer.tour(params))
            seconds = time.perf_counter() - active_tick
            completed = dict(record["active_stage"], status="completed", finished_utc=_utc(),
                             elapsed_seconds=seconds, clean=clean)
            record["stages"].append(completed)
            key = "completed_lll_calls" if request["kind"] == "lll" else "completed_tours"
            record["actual"][key] += 1
            record["active_stage"] = None
            active_tick = None
            last_basis, last_transform = _rows(matrix), _rows(U)
        record.update(status="completed", termination_reason="requested_schedule_completed")
    except Exception as error:
        record.update(status="error", termination_reason="backend_error",
                      error={"type": type(error).__name__, "message": str(error)})
        if record["active_stage"] is not None:
            record["failed_stage"] = dict(record["active_stage"], status="failed", finished_utc=_utc(),
                                           elapsed_seconds=time.perf_counter() - active_tick,
                                           completed_tour=False, internal_progress="not_observable")
            record["active_stage"] = None
    proof = verify_unimodular_transform(original, last_basis, last_transform)
    record["exact_lattice_verification"] = proof
    record["finished_utc"] = _utc()
    record["elapsed_seconds"] = time.perf_counter() - tick
    record["cumulative_seconds"] = record["prior_cumulative_seconds"] + record["elapsed_seconds"]
    if not proof["verified"]:
        record.update(status="error", termination_reason="exact_lattice_proof_failed")
    was_reduced = bool(record["stages"])
    record["reduced_basis_sha256"] = _hash(last_basis) if was_reduced else None
    record["transform_sha256"] = _hash(last_transform) if was_reduced else None
    validate_execution_record(record, protocol, original_basis=original,
                              reduced_basis=last_basis if was_reduced else None,
                              transform=last_transform if was_reduced else None)
    lattice_status = "reduced_verified" if was_reduced and proof["verified"] else "original_only"
    return {"original_basis": original, "reduced_basis": last_basis if was_reduced else None,
            "transform": last_transform if was_reduced else None,
            "protocol": protocol_manifest(protocol), "record": record, "lattice_status": lattice_status}
