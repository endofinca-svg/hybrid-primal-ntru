"""Paired fixed-basis decoding stages, with public-only stopping decisions.

This is not an end-to-end runner. Reduction/build costs stay as provenance;
there is no budget controller, supervisor, memory attribution or truth input.
"""

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import time

from experiments.ntru_e2e.algebra import lattice_member, multiplication_matrix, reconstruct
from .inputs import content_sha256, create_working_copy, load_input_bundle, validate_input_bundle
from .progress import ProgressProtocolError, TargetProgress
from .validation import validate_public_candidate


MODES = ("fixed_basis_full_batch", "fixed_basis_first_public_valid")
PHASES = ("input_load_verify_seconds", "gso_initialization_seconds", "extra_preprocess_seconds",
          "target_prepare_seconds", "decode_seconds", "lift_reconstruct_seconds",
          "public_verify_seconds", "result_output_seconds", "bookkeeping_seconds")


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _vector(value, dimension):
    if (not isinstance(value, (list, tuple)) or len(value) != dimension
            or any(not _integer(x) for x in value)):
        raise ValueError("backend vector must have the full integer dimension")
    return list(value)


def _multiply(rows, coefficients):
    return [sum(a * row[j] for a, row in zip(coefficients, rows)) for j in range(len(rows))]


def _check_fixed(backend, working, expected):
    if working["rows"] != expected:
        raise RuntimeError("working integer basis changed in fixed-basis mode")
    if getattr(backend, "metadata", {}).get("hidden_reduction_calls", 0):
        raise RuntimeError("hidden reduction requested in fixed-basis mode")
    live = getattr(backend, "gso", getattr(backend, "G", None))
    if live is not None:
        rows = [[int(x) for x in row] for row in live.B]
        if rows != expected:
            raise RuntimeError("backend GSO integer basis changed in fixed-basis mode")
    if hasattr(backend, "rows") and backend.rows != expected:
        raise RuntimeError("backend integer basis changed in fixed-basis mode")


def _terminal_observation(progress, declared):
    """At first validation, batch terminal results have already been returned.

    Candidate verification events are ingested before final ledger termination
    so several outputs per target remain possible. This observation overlays
    the already received terminal facts, rather than pretending computation is
    still pending merely because the bookkeeping loop has not visited it yet.
    """
    result = progress.snapshot()
    for target in result["targets"]:
        identifier = target["external_id"]
        if target["state"] == "pending" and identifier in declared:
            target["state"] = declared[identifier]
    for state in ("completed", "errored", "cancelled", "pending", "unsubmitted"):
        result[state + "_count"] = sum(row["state"] == state for row in result["targets"])
    result["conservation_holds"] = result["submitted_count"] == sum(result[name + "_count"] for name in
                                                                       ("completed", "errored", "cancelled", "pending"))
    result["observation_scope"] = "terminal backend reports already returned; candidate validation observed up to this point"
    return result


def run_fixed_basis(input_path, method, *, mode="fixed_basis_full_batch", batch_size=None,
                    backend_config=None, output_path=None, backend_factory=None):
    """Run E or D on a saved verified integer basis and actual used targets.

    first_public_valid drains the current returned batch, then submits no new
    batch. Missing backend terminal results remain pending; projection groups
    and returned candidates never imply completion on their own.
    """
    if method not in ("D", "E") or mode not in MODES:
        raise ValueError("explicit D/E method and fixed-basis mode required")
    if batch_size is not None and (not _integer(batch_size) or batch_size < 1):
        raise ValueError("batch_size must be a positive integer")
    if backend_config is not None and not isinstance(backend_config, dict):
        raise ValueError("backend_config must be a public configuration dictionary")
    config = deepcopy(backend_config or {})
    allowed_config = {"float_type", "precision_bits", "sieve_dim", "seed", "db_size", "nrand",
                      "max_iterations", "preprocessing", "buckets", "blocks", "multi_hash",
                      "interface_test_mode", "force_kernel_iterations", "force_kernel_for_interface"}
    if set(config) - allowed_config:
        raise ValueError("unknown backend configuration fields; reference inputs are not accepted")
    started = time.monotonic()
    phases = dict.fromkeys(PHASES, 0.0)
    tick = time.monotonic()
    if isinstance(input_path, dict):
        bundle = deepcopy(input_path)
        validate_input_bundle(bundle, require_reduced_verified=True)
        input_file_sha = None
        path_label = "in_memory_public_test_fixture"
    else:
        path_label = str(input_path)
        bundle = load_input_bundle(input_path, require_reduced_verified=True)
        input_file_sha = sha256(Path(input_path).read_bytes()).hexdigest()
    public = bundle["public_instance"]
    coordinates = bundle["coordinates"]
    A = multiplication_matrix(public["h"], public["q"])
    phases["input_load_verify_seconds"] = time.monotonic() - tick
    ids = [row["backend_integer_id"] for row in bundle["id_mapping"]]
    identities = {row["backend_integer_id"]: row for row in bundle["id_mapping"]}
    progress = TargetProgress(ids)
    result = {
        "schema": "ntru-paired-fixed-basis-decoding-v1", "pipeline_method": method, "mode": mode,
        "scope": "fixed_basis_decoding_stage_not_end_to_end", "input_path": path_label,
        "input_file_sha256": input_file_sha, "input_kind": bundle["provenance"]["kind"],
        "pairing": {"public_content_sha256": bundle["public_content_sha256"],
                    "original_basis_sha256": bundle["basis"]["original_sha256"],
                    "starting_basis_sha256": bundle["basis"]["reduced_sha256"],
                    "candidate_ordered_sha256": bundle["candidate_batch"]["ordered_sha256"],
                    "ordered_targets_sha256": bundle["ordered_targets_sha256"],
                    "id_mapping_sha256": bundle["id_mapping_sha256"],
                    "coordinates_sha256": content_sha256(coordinates)},
        "dimensions": deepcopy(bundle["dimensions"]), "backend_config": config,
        "shared_reduction": {"protocol": deepcopy(bundle["basis"]["protocol"]),
                             "execution": deepcopy(bundle["basis"]["execution"]),
                             "excluded_from_this_method_wall": True,
                             "cost_is_not_zero": True},
        "zero_tail_policy": "skip; nonzero candidate batch; no added nonzero-vector branch",
        "first_valid_time_origins": {"method": "method entry before input loading",
                                     "progress": "TargetProgress construction after input validation"},
        "basis_row_precheck_enabled": False, "strict_fixed_basis": True,
        "candidates": [], "protocol_errors": [], "backend_batches": [],
        "first_public_valid": None, "stop_reason": None, "backend_metadata": {},
        "metrics": {"preprocess_count": 0, "slicer_kernel_calls": 0,
                    "babai_precheck_target_count": 0, "slicer_target_count": 0},
        "peak_memory_bytes": None, "memory_scope": "not measured; no historical RSS substituted",
    }
    backend = working = None
    first_returned_count = None
    try:
        tick = time.monotonic()
        try:
            working = create_working_copy(bundle)
            if backend_factory is None:
                from .fixed_backend import build_backend
                factory = build_backend
            else:
                factory = backend_factory
            backend = factory(working, method, config)
            _check_fixed(backend, working, bundle["basis"]["reduced"])
        finally:
            phases["gso_initialization_seconds"] = time.monotonic() - tick
        tick = time.monotonic()
        try:
            prepared = backend.preprocess()
            _check_fixed(backend, working, bundle["basis"]["reduced"])
        finally:
            phases["extra_preprocess_seconds"] = time.monotonic() - tick
        result["metrics"]["preprocess_count"] = (prepared or {}).get("preprocess_count", 0)
        size = batch_size or (1 if mode == "fixed_basis_first_public_valid" else max(1, len(ids)))
        for offset in range(0, len(ids), size):
            chunk = ids[offset:offset + size]
            targets = [deepcopy(bundle["targets"][identities[i]["target_index"]]["used"]) for i in chunk]
            bounds = [public["distribution"]["w_f"] + public["distribution"]["w_g"]
                      - sum(x != 0 for x in identities[i]["u"]) for i in chunk]
            for identifier in chunk:
                progress.submit(identifier)
            tick = time.monotonic()
            try:
                returned = backend.decode(targets, bounds, chunk)
                call_wall = time.monotonic() - tick
                _check_fixed(backend, working, bundle["basis"]["reduced"])
            except Exception as error:
                failed_wall = time.monotonic() - tick
                phases["decode_seconds"] += failed_wall
                result["backend_batches"].append({"submitted_ids": chunk, "call_wall_seconds": failed_wall,
                                                  "call_evidence": deepcopy(getattr(backend, "call_evidence", [])),
                                                  "metrics": deepcopy(getattr(backend, "metrics", {})),
                                                  "error": f"{type(error).__name__}: {error}"})
                for identifier in chunk:
                    progress.error(identifier, f"{type(error).__name__}: {error}")
                result["backend_error"] = {"type": type(error).__name__, "message": str(error)}
                result["stop_reason"] = "backend_error"
                break
            if not isinstance(returned, dict):
                raise ValueError("backend must return explicit candidate and terminal result lists")
            timings = returned.get("timings", {})
            if timings:
                values = [timings.get(key, 0.0) for key in ("target_prepare_seconds", "decode_seconds", "lift_seconds")]
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in values):
                    raise ValueError("backend supplied invalid phase timings")
                if sum(values) > call_wall + 1e-6:
                    raise ValueError("backend exclusive phase timings exceed its measured call wall")
                phases["target_prepare_seconds"] += values[0]
                phases["decode_seconds"] += values[1]
                phases["lift_reconstruct_seconds"] += values[2]
            else:
                phases["decode_seconds"] += call_wall
            batch_record = {"submitted_ids": chunk, "call_wall_seconds": call_wall,
                            "backend_timings": deepcopy(timings),
                            "projection_groups": deepcopy(returned.get("projection_groups", [])),
                            "call_evidence": deepcopy(returned.get("call_evidence", [])),
                            "metrics": deepcopy(returned.get("metrics", {}))}
            result["backend_batches"].append(batch_record)
            declared = {}
            for key, state in (("completed_ids", "completed"), ("errored_ids", "errored"), ("cancelled_ids", "cancelled")):
                for identifier in returned.get(key, []):
                    if not _integer(identifier) or identifier not in chunk:
                        result["protocol_errors"].append({"reason": "unknown_or_unsubmitted_terminal_id", "id": identifier})
                    elif identifier in declared:
                        result["protocol_errors"].append({"reason": "duplicate_terminal_event", "id": identifier})
                        declared[identifier] = "errored"
                    else:
                        declared[identifier] = state
            for candidate in returned.get("candidates", []):
                if not isinstance(candidate, dict):
                    result["protocol_errors"].append({"reason": "candidate_is_not_a_record"})
                    continue
                identifier = candidate.get("backend_integer_id")
                if not _integer(identifier) or identifier not in chunk:
                    result["protocol_errors"].append({"reason": "unknown_or_unsubmitted_candidate_id", "id": identifier})
                    continue
                identity = identities[identifier]
                row = {"backend_integer_id": identifier, "candidate_sha_id": identity["candidate_sha_id"],
                       "target_index": identity["target_index"], "pipeline_method": method,
                       "output_origin": candidate.get("output_origin", "unknown"),
                       "slicer_invoked": candidate.get("slicer_invoked", False),
                       "slicer_used_in_returned_candidate": candidate.get("slicer_used_in_returned_candidate", "unknown"),
                       "reconstructed": None, "conversion_rejection": None}
                tick = time.monotonic()
                try:
                    dimension = bundle["dimensions"]["d"]
                    coefficients = _vector(candidate["coefficients"], dimension) if "coefficients" in candidate else None
                    vector = _vector(candidate["v"], dimension) if "v" in candidate else None
                    if coefficients is None and vector is None:
                        raise ValueError("backend returned neither integer coefficients nor an ambient vector")
                    if coefficients is not None:
                        converted = _multiply(bundle["basis"]["reduced"], coefficients)
                        if vector is not None and vector != converted:
                            raise ValueError("ambient vector differs from its integer basis coefficients")
                        vector = converted
                    if not lattice_member(vector, A, public["q"], coordinates["C"]):
                        raise ValueError("backend ambient vector is not in the public lattice")
                    target = bundle["targets"][identity["target_index"]]["used"]
                    row["ambient_vector"] = vector
                    row["actual_target_sha256"] = bundle["targets"][identity["target_index"]]["used_sha256"]
                    row["residual_sq"] = sum((a - b) ** 2 for a, b in zip(vector, target))
                    row["reconstructed"] = reconstruct(vector, target, coordinates["P"], coordinates["C"],
                                                       coordinates["T"], identity["u"], public["n"])
                except (ValueError, TypeError) as error:
                    row["conversion_rejection"] = str(error)
                    declared[identifier] = "errored"
                    result["protocol_errors"].append({"reason": "candidate_conversion_failed",
                                                      "id": identifier, "message": str(error)})
                phases["lift_reconstruct_seconds"] += time.monotonic() - tick
                tick = time.monotonic()
                pair = row["reconstructed"]
                validation = validate_public_candidate(public, pair["f"] if pair else None, pair["g"] if pair else None)
                row["public_validation"] = validation
                phases["public_verify_seconds"] += time.monotonic() - tick
                progress.add_candidate(identifier, public_valid=validation["public_valid"],
                                       solution_key=[pair["f"], pair["g"]] if validation["public_valid"] else None,
                                       origin=row["output_origin"])
                result["candidates"].append(row)
                if validation["public_valid"] and result["first_public_valid"] is None:
                    first_returned_count = len(result["candidates"])
                    result["first_public_valid"] = {"seconds": time.monotonic() - started,
                                                    "backend_integer_id": identifier,
                                                    "progress": _terminal_observation(progress, declared),
                                                    "batch_work_already_returned": True}
            for identifier, state in declared.items():
                try:
                    if state == "completed":
                        progress.complete(identifier)
                    elif state == "errored":
                        progress.error(identifier, "backend_reported_error")
                    else:
                        progress.cancel(identifier, "backend_reported_cancellation")
                except ProgressProtocolError as error:
                    result["protocol_errors"].append({"reason": str(error), "id": identifier})
            if mode == "fixed_basis_first_public_valid" and result["first_public_valid"] is not None:
                result["stop_reason"] = "first_public_valid"
                break
        if result["stop_reason"] is None:
            snapshot = progress.snapshot()
            result["stop_reason"] = "partial_backend_return" if snapshot["pending_count"] else "full_batch_finished"
    except Exception as error:
        result["backend_error"] = {"type": type(error).__name__, "message": str(error)}
        if "chunk" in locals():
            pending = {row["external_id"] for row in progress.snapshot()["targets"] if row["state"] == "pending"}
            for identifier in chunk:
                if identifier in pending:
                    progress.error(identifier, f"backend protocol exception: {type(error).__name__}: {error}")
        result["stop_reason"] = "setup_error" if progress.snapshot()["submitted_count"] == 0 else "backend_protocol_error"
    if backend is not None:
        result["backend_metadata"] = deepcopy(getattr(backend, "metadata", {}))
        result["metrics"].update(deepcopy(getattr(backend, "metrics", {})))
    result["progress"] = progress.snapshot()
    first = result["first_public_valid"]
    result["after_first_public_valid"] = {
        "returned_candidates_ingested": len(result["candidates"]) - first_returned_count if first else 0,
        "wall_seconds": time.monotonic() - started - first["seconds"] if first else 0.0,
        "scope": "drain current batch and bookkeeping; no later batch submitted in first-public-valid mode"}
    if result["protocol_errors"]:
        result["status"] = "protocol_error"
    elif "backend_error" in result:
        result["status"] = "error"
    elif result["progress"]["pending_count"]:
        result["status"] = "partial"
    else:
        result["status"] = "completed"
    # Exclusive phases are bounded by method wall; uninstrumented dispatch,
    # checks, result bookkeeping and backend overhead fall into bookkeeping.
    elapsed = time.monotonic() - started
    phases["bookkeeping_seconds"] = max(0.0, elapsed - sum(phases.values()))
    result["timings"] = {**phases, "total_wall_seconds": elapsed,
                         "phase_semantics": "PHASES are exclusive; backend timings/call evidence are nested diagnostics, never added twice",
                         "reduction_and_construction_excluded": True}
    if output_path is not None:
        tick = time.monotonic()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The sidecar is authoritative for timings measured after the main
        # JSON write. Reporting its own write time would be self-referential.
        result["timing_authority"] = str(path.with_suffix(path.suffix + ".timings.json"))
        result["timings"]["result_output_seconds"] = None
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=True, indent=2, allow_nan=False)
            stream.write("\n")
        output_seconds = time.monotonic() - tick
        result["timings"]["result_output_seconds"] = output_seconds
        result["timings"]["total_wall_seconds"] += output_seconds
        result["timings"]["output_timing_scope"] = "includes main result serialization/write; excludes final metrics-sidecar reporting IO, which enclosing command wall includes"
        path.with_suffix(path.suffix + ".timings.json").write_text(json.dumps(result["timings"], indent=2) + "\n")
    return result
