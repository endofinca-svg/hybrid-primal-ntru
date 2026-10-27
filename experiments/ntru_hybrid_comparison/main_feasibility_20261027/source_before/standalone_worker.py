"""One cold public pattern attempt; process ownership belongs to its supervisor.

No subprocess, session, private input, cached basis, or database is used here.
Blocking backend calls require the enclosing supervisor's absolute deadline.
Durable progress precedes each call, so killed calls retain pending targets.
"""

import argparse
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile
import time

from experiments.ntru_e2e.algebra import (
    basis_rows, lattice_member, multiplication_matrix, reconstruct,
)
from .candidates import CandidatePolicy, context_from_public
from .inputs import (attach_reduction, build_input_bundle, canonical_bytes,
                     content_sha256, create_working_copy)
from .paired_decode import _check_fixed, _multiply, _vector
from .progress import TargetProgress
from .reduction import (ReductionProtocol, ReductionStage, execute_reduction,
                        protocol_from_manifest, protocol_manifest, validate_protocol)
from .validation import validate_public_candidate


SCHEMA = "ntru-standalone-attempt-request-v1"
CONFIG_FIELDS = {"reduction_protocol", "candidate_policy", "target_representation",
                 "backend_config", "check_basis_rows", "zero_tail_policy",
                 "decode_stop_policy", "batch_size", "interface_test_only"}
PHASES = ("public_load_seconds", "input_construction_seconds", "reduction_seconds",
          "reduction_attach_seconds", "basis_row_recovery_seconds",
          "gso_initialization_seconds", "extra_preprocess_seconds",
          "decode_seconds", "lift_reconstruct_seconds", "public_verify_seconds")


class WorkDeadlineReached(RuntimeError):
    """No further computation may begin under the inherited absolute budget."""


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _protocol(document):
    if not isinstance(document, dict):
        raise ValueError("explicit public reduction protocol required")
    if "schema" in document:
        return protocol_from_manifest(document)
    values = deepcopy(document)
    schedule = values.get("schedule", [])
    if not isinstance(schedule, (list, tuple)):
        raise ValueError("reduction schedule must be ordered")
    values["schedule"] = tuple(ReductionStage(**stage) for stage in schedule)
    return ReductionProtocol(**values)


def validate_request(request):
    allowed = {"schema", "public_path", "config", "method", "trial_id",
               "pattern_attempt_index", "coordinates", "seeds", "budget_origin_monotonic",
               "work_deadline", "hard_deadline", "config_sha256", "trial_started_monotonic"}
    required = allowed - {"config_sha256", "trial_started_monotonic"}
    if (not isinstance(request, dict) or set(request) - allowed or required - set(request)
            or request["schema"] != SCHEMA):
        raise ValueError("unknown or missing attempt request fields")
    if request["method"] not in ("B", "D", "E"):
        raise ValueError("standalone method must be B, D or E")
    if not isinstance(request["public_path"], str) or not isinstance(request["trial_id"], str):
        raise ValueError("public path and trial ID must be strings")
    if not _integer(request["pattern_attempt_index"]) or request["pattern_attempt_index"] < 0:
        raise ValueError("attempt index must be a nonnegative integer")
    for name in ("budget_origin_monotonic", "work_deadline", "hard_deadline"):
        value = request[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)):
            raise ValueError("absolute monotonic deadline values must be finite")
    if not request["budget_origin_monotonic"] <= request["work_deadline"] <= request["hard_deadline"]:
        raise ValueError("work and hard deadlines must preserve cleanup reserve")
    config = request["config"]
    if not isinstance(config, dict) or set(config) - CONFIG_FIELDS:
        raise ValueError("unknown worker configuration; reference metadata is not accepted")
    if config.get("zero_tail_policy", "skip") != "skip":
        raise ValueError("only the explicit zero-tail skip policy is implemented")
    if config.get("decode_stop_policy", "first_public_valid") != "first_public_valid":
        raise ValueError("standalone decoding stops at the first public-valid returned batch")
    for key in ("check_basis_rows", "interface_test_only"):
        if key in config and not isinstance(config[key], bool):
            raise ValueError(key + " must be boolean")
    if request["method"] == "B" and config.get("check_basis_rows", True) is not True:
        raise ValueError("B requires nonzero basis-row recovery")
    size = config.get("batch_size", 1)
    if not _integer(size) or size < 1:
        raise ValueError("batch_size must be a positive integer")
    backend = config.get("backend_config", {})
    backend_fields = {"float_type", "precision_bits", "sieve_dim", "seed", "db_size", "nrand",
                      "max_iterations", "preprocessing", "buckets", "blocks", "multi_hash",
                      "interface_test_mode", "force_kernel_iterations", "force_kernel_for_interface"}
    if not isinstance(backend, dict) or set(backend) - backend_fields:
        raise ValueError("unknown backend configuration")
    test_options = (backend.get("interface_test_mode") or backend.get("force_kernel_iterations")
                    or backend.get("force_kernel_for_interface")
                    or backend.get("preprocessing") == "sample_database_only")
    if test_options and not config.get("interface_test_only", False):
        raise ValueError("test-only Slicer options require an explicit interface_test_only label")
    coordinates = request["coordinates"]
    if not isinstance(coordinates, dict) or set(coordinates) != {"P", "C", "T"}:
        raise ValueError("explicit ordered P/C/T partition required")
    if any(not isinstance(coordinates[key], list) for key in coordinates):
        raise ValueError("coordinates must be ordered lists")
    if request["method"] == "B" and coordinates["T"]:
        raise ValueError("B requires kappa=0 and does not use CVP zero targets")
    if request["method"] != "B" and not coordinates["T"]:
        raise ValueError("D/E require nonzero kappa")
    seeds = request["seeds"]
    seed_fields = {"pattern_seed", "tail_seed", "candidate_seed", "reduction_seed", "decoder_seed"}
    if (not isinstance(seeds, dict) or set(seeds) - seed_fields
            or any(not _integer(v) or v < 0 for v in seeds.values())):
        raise ValueError("seeds must be explicit nonnegative public integers")
    protocol = _protocol(config.get("reduction_protocol"))
    if "reduction_seed" in seeds and protocol.seed != seeds["reduction_seed"]:
        raise ValueError("recorded reduction seed differs from the actual reduction protocol")
    if "decoder_seed" in seeds and backend.get("seed", seeds["decoder_seed"]) != seeds["decoder_seed"]:
        raise ValueError("recorded decoder seed differs from backend seed")
    return protocol


class _Journal:
    def __init__(self, result, events_path, result_path, clock):
        self.result, self.clock = result, clock
        self.events_path = Path(events_path) if events_path is not None else None
        self.result_path = Path(result_path) if result_path is not None else None
        self.stream = None
        self.sequence, self.io_seconds = 0, 0.0
        if self.events_path is not None:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.events_path.open("x", encoding="utf-8")
        if self.result_path is not None and self.result_path.exists():
            if self.stream is not None:
                self.stream.close()
            raise FileExistsError(self.result_path)

    def emit(self, event, **fields):
        tick = self.clock()
        record = {"event": event, "sequence": self.sequence, "monotonic": tick,
                  "elapsed_seconds": tick - self.result["budget_origin_monotonic"],
                  "stage": self.result["current_stage"], **fields}
        self.sequence += 1
        if self.stream is not None:
            self.stream.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
        self.io_seconds += self.clock() - tick
        return record

    def checkpoint(self):
        if self.result_path is None:
            return
        tick = self.clock()
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="." + self.result_path.name + ".", dir=self.result_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical_bytes(self.result) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.result_path)
            directory = os.open(self.result_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.io_seconds += self.clock() - tick

    def close(self):
        if self.stream is not None:
            self.stream.close()


def run_attempt(request, *, events_path=None, result_path=None, clock=time.monotonic,
                reduction_executor=None, backend_factory=None):
    """Execute a fresh attempt. Dependency injection is only for pure tests.

    The deadline origin belongs to the enclosing trial, so imports, request
    preparation and all earlier pattern attempts already consume its budget.
    The worker does not assert cleanup or primary-comparison eligibility.
    """
    started = clock()
    protocol = validate_request(request)
    method, config = request["method"], deepcopy(request["config"])
    origin, work = request["budget_origin_monotonic"], request["work_deadline"]
    result = {"schema": "ntru-standalone-pattern-attempt-v1", "mode": "standalone_end_to_end",
              "scope": "cold_public_pattern_attempt_in_independent_supervised_process",
              "method": method, "trial_id": request["trial_id"],
              "pattern_attempt_index": request["pattern_attempt_index"],
              "coordinates": deepcopy(request["coordinates"]), "seeds": deepcopy(request["seeds"]),
              "config_sha256": request.get("config_sha256", content_sha256(config)),
              "worker_config_sha256": content_sha256(config), "reduction_protocol": protocol_manifest(protocol),
              "budget_origin_monotonic": origin, "worker_started_monotonic": started,
              "work_deadline": work, "hard_deadline": request["hard_deadline"],
              "current_stage": "initialization", "status": "running", "stop_reason": None,
              "timings": dict.fromkeys(PHASES, 0.0), "progress": TargetProgress([], clock=clock).snapshot(),
              "input_digests": {}, "candidates": [], "backend_batches": [], "protocol_errors": [],
              "first_public_valid": None, "public_valid_observed": False,
              "interface_test_only": config.get("interface_test_only", False),
              "zero_tail_policy": "skip nonzero-tail candidate generation; optional separately charged basis-row check",
              "basis_row_check_scope": "after the complete configured reduction schedule, not after each tour",
              "target_submission_scope": "durable dispatch-boundary event before backend invocation; externally interrupted handoff can remain pending/dispatch-unconfirmed",
              "candidate_order_randomized": False,
              "cached_reduction_used": False, "cached_database_used": False,
              "cleanup_verified": None, "cleanup_scope": "external supervisor responsibility"}
    journal = _Journal(result, events_path, result_path, clock)
    progress, backend = None, None

    def budget_check():
        if clock() >= work:
            raise WorkDeadlineReached("inherited absolute work deadline reached")

    @contextmanager
    def stage(name):
        budget_check()
        result["current_stage"] = name
        journal.emit("stage_started")
        journal.checkpoint()
        budget_check()
        tick, before_io = clock(), journal.io_seconds
        try:
            yield
        finally:
            result["timings"][name + "_seconds"] += max(0.0, clock() - tick - (journal.io_seconds - before_io))
            journal.emit("stage_finished")
            journal.checkpoint()

    def sync_progress():
        if progress is not None:
            result["progress"] = progress.snapshot()
            if result.get("candidate_counts") is not None:
                result["candidate_counts"]["submitted_count"] = result["progress"]["submitted_count"]
                result["candidate_counts"]["completed_count"] = result["progress"]["completed_count"]

    def observe(pair, source, **details):
        with stage("public_verify"):
            validation = validate_public_candidate(public, pair["f"], pair["g"])
            observed = clock()
            row = {"f": list(pair["f"]), "g": list(pair["g"]), "source": source,
                   "observed_monotonic": observed, "public_validation": validation, **details}
            result["candidates"].append(row)
            if validation["public_valid"]:
                result["public_valid_observed"] = True
                if result["first_public_valid"] is None:
                    result["first_public_valid"] = {"monotonic": observed, "elapsed_seconds": observed - origin,
                                                    "source": source, **details}
            journal.emit("candidate", **row)
            return row

    try:
        journal.emit("attempt_started", method=method, coordinates=result["coordinates"], seeds=result["seeds"])
        journal.checkpoint()
        with stage("public_load"):
            public_bytes = Path(request["public_path"]).read_bytes()
            public = json.loads(public_bytes)
            # This strict public-only helper also validates schema and q/h.
            metadata_context = context_from_public(public, request["coordinates"]["T"] or [0])
            from hashlib import sha256
            result["public_file_sha256"] = sha256(public_bytes).hexdigest()
            result["instance_id"] = public["instance_id"]
            result["public_content_sha256"] = metadata_context.public_sha256
        with stage("input_construction"):
            P, C, T = (request["coordinates"][key] for key in ("P", "C", "T"))
            n, q = public["n"], public["q"]
            combined = P + C + T
            if (len(combined) != n or any(not _integer(i) or not 0 <= i < n for i in combined)
                    or len(set(combined)) != n):
                raise ValueError("P/C/T must partition original public f coordinates")
            A = multiplication_matrix(public["h"], q)
            if method == "B":
                original = basis_rows(A, q, C)
                bundle = None
                result["input_digests"] = {"original_basis_sha256": content_sha256(original),
                                           "coordinates_sha256": content_sha256(request["coordinates"]),
                                           "candidate_ordered_sha256": None, "ordered_targets_sha256": None}
                result["candidate_counts"] = None
                result["candidate_order"] = None
            else:
                policy = CandidatePolicy(**config.get("candidate_policy", {"mode": "full_nonzero"}))
                bundle = build_input_bundle(public, P, C, T, policy,
                                            target_representation=config.get("target_representation", "centered_mod_q"),
                                            reduction_protocol=protocol,
                                            provenance={"kind": "public_input_build_only",
                                                        "source_public_path": request["public_path"],
                                                        "source_public_file_sha256": result["public_file_sha256"]})
                original = bundle["basis"]["original"]
                result["input_digests"] = {"original_basis_sha256": bundle["basis"]["original_sha256"],
                                           "coordinates_sha256": content_sha256(bundle["coordinates"]),
                                           "candidate_ordered_sha256": bundle["candidate_batch"]["ordered_sha256"],
                                           "ordered_targets_sha256": bundle["ordered_targets_sha256"],
                                           "id_mapping_sha256": bundle["id_mapping_sha256"]}
                ids = [row["backend_integer_id"] for row in bundle["id_mapping"]]
                result["candidate_counts"] = deepcopy(bundle["candidate_batch"]["counts"])
                result["candidate_order"] = bundle["candidate_batch"]["order"]
                progress = TargetProgress(ids, clock=clock)
                sync_progress()
            result["dimensions"] = {"n": n, "q": q, "k": len(P), "kappa": len(T), "d": len(original)}
            validate_protocol(protocol, len(original))
        with stage("reduction"):
            executor = reduction_executor or execute_reduction
            execution = executor(original, protocol)
            result["reduction"] = deepcopy(execution["record"])
            result["input_digests"]["reduced_basis_sha256"] = content_sha256(execution["reduced_basis"])
        if execution["record"]["status"] != "completed" or execution["lattice_status"] != "reduced_verified":
            raise RuntimeError("the requested cold reduction did not complete with an exact lattice certificate")
        rows = execution["reduced_basis"]
        if method == "B" or config.get("check_basis_rows", False):
            with stage("basis_row_recovery"):
                norm = public["distribution"]["w_f"] + public["distribution"]["w_g"]
                # Same row reconstruction and exact public radius as pattern
                # primal; no zero-target closest-vector call is performed.
                possible = [(index, reconstruct(row, [0] * len(row), P, C, T, [0] * len(T), n))
                            for index, row in enumerate(rows) if sum(x * x for x in row) == norm]
            for index, pair in possible:
                observe(pair, "basis_row", row_index=index)
                if result["public_valid_observed"]:
                    break
        if result["public_valid_observed"]:
            result.update(status="completed", stop_reason="first_public_valid_basis_row")
        elif method == "B":
            result.update(status="completed", stop_reason="no_public_valid_basis_row")
        else:
            with stage("reduction_attach"):
                bundle = attach_reduction(bundle, execution)
            with stage("gso_initialization"):
                working = create_working_copy(bundle)
                if backend_factory is None:
                    from .fixed_backend import build_backend
                    factory = build_backend
                else:
                    factory = backend_factory
                backend_config = deepcopy(config.get("backend_config", {}))
                if "decoder_seed" in request["seeds"]:
                    backend_config.setdefault("seed", request["seeds"]["decoder_seed"])
                backend = factory(working, method, backend_config)
                _check_fixed(backend, working, rows)
            with stage("extra_preprocess"):
                backend.preprocess()
                _check_fixed(backend, working, rows)
            identities = {row["backend_integer_id"]: row for row in bundle["id_mapping"]}
            size = config.get("batch_size", 1)
            for offset in range(0, len(ids), size):
                budget_check()
                chunk = ids[offset:offset + size]
                targets = [deepcopy(bundle["targets"][identities[i]["target_index"]]["used"]) for i in chunk]
                bounds = [public["distribution"]["w_f"] + public["distribution"]["w_g"]
                          - sum(value != 0 for value in identities[i]["u"]) for i in chunk]
                for identifier in chunk:
                    progress.submit(identifier)
                    sync_progress()
                    journal.emit("target_submitted", external_id=identifier)
                    journal.checkpoint()
                with stage("decode"):
                    returned = backend.decode(targets, bounds, chunk)
                    _check_fixed(backend, working, rows)
                    if not isinstance(returned, dict):
                        raise ValueError("backend must return explicit candidates and terminal IDs")
                    result["backend_batches"].append({"submitted_ids": chunk, "returned": deepcopy(returned)})
                declared = {}
                for key, state in (("completed_ids", "completed"), ("errored_ids", "errored"), ("cancelled_ids", "cancelled")):
                    for identifier in returned.get(key, []):
                        if not _integer(identifier) or identifier not in chunk or identifier in declared:
                            raise ValueError("unknown, unsubmitted or duplicate backend terminal ID")
                        declared[identifier] = state
                for candidate in returned.get("candidates", []):
                    identifier = candidate.get("backend_integer_id") if isinstance(candidate, dict) else None
                    if not _integer(identifier) or identifier not in chunk:
                        raise ValueError("unknown or unsubmitted backend candidate ID")
                    identity = identities[identifier]
                    with stage("lift_reconstruct"):
                        dimension = len(rows)
                        coefficients = _vector(candidate["coefficients"], dimension) if "coefficients" in candidate else None
                        vector = _vector(candidate["v"], dimension) if "v" in candidate else None
                        if coefficients is None and vector is None:
                            raise ValueError("candidate has no full integer lattice representation")
                        if coefficients is not None:
                            converted = _multiply(rows, coefficients)
                            if vector is not None and vector != converted:
                                raise ValueError("candidate coefficients do not match its ambient vector")
                            vector = converted
                        if not lattice_member(vector, A, q, C):
                            raise ValueError("candidate is not in the public lattice")
                        target = bundle["targets"][identity["target_index"]]["used"]
                        pair = reconstruct(vector, target, P, C, T, identity["u"], n)
                    row = observe(pair, candidate.get("output_origin", "unknown"),
                                  backend_integer_id=identifier, candidate_sha_id=identity["candidate_sha_id"])
                    progress.add_candidate(identifier, public_valid=row["public_validation"]["public_valid"],
                                           solution_key=[pair["f"], pair["g"]] if row["public_validation"]["public_valid"] else None,
                                           origin=row["source"])
                for identifier, state in declared.items():
                    if state == "completed":
                        progress.complete(identifier)
                    elif state == "errored":
                        progress.error(identifier, "backend_reported_error")
                    else:
                        progress.cancel(identifier, "backend_reported_cancellation")
                    sync_progress()
                    journal.emit("target_" + state, external_id=identifier)
                    journal.checkpoint()
                sync_progress()
                if result["public_valid_observed"]:
                    break
            result.update(status="completed", stop_reason="first_public_valid" if result["public_valid_observed"] else
                          ("partial_backend_return" if result["progress"]["pending_count"] else "candidate_list_exhausted"))
            if result["progress"]["pending_count"]:
                result["status"] = "partial"
    except WorkDeadlineReached as error:
        result.update(status="work_deadline", stop_reason="absolute_work_deadline", error=str(error))
    except Exception as error:
        result.update(status="error", stop_reason="worker_error", error={"type": type(error).__name__, "message": str(error)})
        # Ordinary backend exceptions are distinct from externally killed calls:
        # only this observed exception can terminate pending IDs as errored.
        if progress is not None:
            for row in progress.snapshot()["targets"]:
                if row["state"] == "pending":
                    progress.error(row["external_id"], "observed_worker_exception")
                    journal.emit("target_errored", external_id=row["external_id"], reason="observed_worker_exception")
    finally:
        sync_progress()
        if backend is not None:
            result["backend_metadata"] = deepcopy(getattr(backend, "metadata", {}))
            result["backend_metrics"] = deepcopy(getattr(backend, "metrics", {}))
            result["backend_call_evidence"] = deepcopy(getattr(backend, "call_evidence", []))
        result["finished_monotonic"] = clock()
        result["timings"]["event_checkpoint_io_seconds"] = journal.io_seconds
        wall = clock() - started
        result["timings"]["worker_wall_seconds"] = wall
        result["timings"]["bookkeeping_seconds"] = max(0.0, wall - sum(result["timings"][name] for name in PHASES)
                                                       - journal.io_seconds)
        result["timing_scope"] = "worker wall from function entry; enclosing trial additionally pays interpreter/import/request, final durable output and cleanup"
        result["worker_work_deadline_met"] = result["finished_monotonic"] <= work
        result["current_stage"] = "terminal"
        journal.checkpoint()
        journal.emit("terminal", status=result["status"], stop_reason=result["stop_reason"],
                     progress=result["progress"], public_valid_observed=result["public_valid_observed"],
                     input_digests=result["input_digests"])
        journal.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    request = json.loads(Path(args.request).read_text())
    result = run_attempt(request, events_path=args.events, result_path=args.result)
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
