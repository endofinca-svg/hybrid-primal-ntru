"""Independent read-only audit of completed stage3 numerical artifacts.

This process is itself budgeted by the stage3 supervisor. It ignores solver
success flags and recomputes integer identities, norms and public validation.
"""

import argparse
from hashlib import sha256
import json
import math
from pathlib import Path
import time
import traceback

from experiments.ntru_e2e.algebra import partition
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.exact_lattice import verify_unimodular_transform
from experiments.ntru_hybrid_comparison.inputs import build_input_bundle, content_sha256
from experiments.ntru_hybrid_comparison.patterns import select_pattern
from experiments.ntru_hybrid_comparison.supervisor import _read_process
from .common import write
from .witness import EXPECTED_FILE, EXPECTED_PUBLIC, PUBLIC, independently_validate, ring_product


CONFIGURATIONS = [(0, "E"), (0, "D-normal"), (2, "E"), (2, "D-skip"),
                  (2, "D-normal"), (2, "D-probe-1"), (2, "D-probe-400")]
PAIR_KEYS = ("lattice_db_sha256", "lattice_db_ordered_sha256", "target_db_sha256",
             "target_db_ordered_sha256", "rng_state_sha256", "sieve_rng_state_sha256",
             "lsh_seed", "gh_sub", "basis_sha256", "target_order_sha256", "id_mapping_sha256")


def read(path):
    return json.loads(Path(path).read_text())


def check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("independent validation work deadline reached")


def stage_intervals(events, finish_upper):
    """No finish means an uncompleted phase's exact duration remains null."""
    opened, intervals = {}, []
    for row in events:
        stage = row.get("stage")
        if row.get("event") == "stage_started":
            if stage in opened:
                raise ValueError("duplicate unclosed stage start")
            opened[stage] = row["monotonic"]
        elif row.get("event") == "stage_finished":
            if stage not in opened:
                raise ValueError("stage finish without start")
            begin = opened.pop(stage)
            intervals.append({"stage": stage, "complete": True,
                              "observed_wall_seconds": row["monotonic"] - begin,
                              "exact_uncompleted_phase_seconds": None})
    intervals.extend({"stage": stage, "complete": False, "observed_wall_seconds": None,
                      "observed_censored_upper_seconds": max(0, finish_upper - begin),
                      "exact_uncompleted_phase_seconds": None}
                     for stage, begin in opened.items())
    return intervals


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def basis_check(stage, index, public, deadline):
    directory = stage / f"pattern_{index}"
    path = directory / "input.json"
    if not path.exists():
        return None, {"status": "not_available", "reason": "completed_input_not_saved"}
    check_deadline(deadline)
    envelope = read(path)
    bundle = envelope["payload"]
    if envelope["payload_sha256"] != content_sha256(bundle):
        raise ValueError("input envelope digest mismatch")
    P = select_pattern(128, 40, 10001, index, "historical_sha256_shuffle_v1")
    coordinates = partition(128, P, 4)
    rebuilt = build_input_bundle(public, **coordinates, policy=CandidatePolicy("full_nonzero"),
                                reduction_protocol=bundle["basis"]["protocol"])
    for key in ("public_content_sha256", "dimensions", "coordinates", "candidate_batch", "targets",
                "ordered_targets_sha256", "id_mapping", "id_mapping_sha256"):
        if bundle[key] != rebuilt[key]:
            raise ValueError("saved input differs from public reconstruction: " + key)
    basis = bundle["basis"]
    if basis["original"] != rebuilt["basis"]["original"]:
        raise ValueError("saved original basis differs from public construction")
    for key, filename in (("original", "original.basis.json"), ("reduced", "reduced.basis.json"),
                          ("transform", "transform.json")):
        if read(directory / filename) != basis[key]:
            raise ValueError("lossless matrix sidecar mismatch: " + filename)
    if content_sha256(basis["original"]) != basis["original_sha256"]:
        raise ValueError("original basis digest mismatch")
    if content_sha256(basis["reduced"]) != basis["reduced_sha256"]:
        raise ValueError("reduced basis digest mismatch")
    check_deadline(deadline)
    certificate = verify_unimodular_transform(basis["original"], basis["reduced"], basis["transform"])
    if not certificate["verified"]:
        raise ValueError("independent lattice certificate rejected")
    manifest = read(directory / "basis_manifest.json")
    if manifest["reduced_sha256"] != basis["reduced_sha256"]:
        raise ValueError("manifest reduced digest mismatch")
    schedule = [(row["beta"], row["requested_tours"]) for row in basis["protocol"]["schedule"]]
    if schedule != [(30, 2), (31, 2), (32, 2)]:
        raise ValueError("reduction schedule outside frozen scope")
    execution = basis["execution"]
    if execution["actual"]["completed_lll_calls"] != 1 or execution["actual"]["completed_tours"] != 6:
        raise ValueError("completed reduction schedule counters inconsistent")
    row_norms = [sum(value * value for value in row) for row in basis["reduced"]]
    basis_rows = read(directory / "basis_rows.json")
    if basis_rows["row_norms_sq"] != row_norms:
        raise ValueError("basis-row squared norms mismatch")
    row_validations = []
    for candidate in basis_rows.get("candidates", []):
        row = basis["reduced"][candidate["row_index"]]
        f = [0] * public["n"]
        for position, value in zip(coordinates["C"], row[public["n"]:]):
            f[position] = value
        if candidate["f"] != f or candidate["g"] != row[:public["n"]]:
            raise ValueError("basis-row candidate does not match actual saved row")
        row_validations.append(independently_validate(public, f, candidate["g"]))
    if sum(row["public_valid"] for row in row_validations) != basis_rows["public_valid_count"]:
        raise ValueError("basis-row public validity count mismatch")
    return bundle, {"status": "verified", "input_file_sha256": sha256(path.read_bytes()).hexdigest(),
                    "original_sha256": basis["original_sha256"], "reduced_sha256": basis["reduced_sha256"],
                    "matrix_reload_identical": True, "certificate": certificate,
                    "historical_digest_matches": manifest.get("historical_digest_matches"),
                    "exact_public_reconstruction": True, "target_count": len(bundle["targets"]),
                    "basis_row_fresh_public_validations": row_validations}


def verify_entry(row, bundle, public):
    identifier = row["backend_integer_id"]
    if not isinstance(identifier, int) or isinstance(identifier, bool) or not 0 <= identifier < 80:
        raise ValueError("telemetry has unknown external target ID")
    mapping = bundle["id_mapping"][identifier]
    if row["tail"] != mapping["u"]:
        raise ValueError("telemetry tail does not match external target ID")
    pair = row["full_pair"]
    f, g = pair["f"], pair["g"]
    n = public["n"]
    if len(f) != n or len(g) != n or any(not isinstance(v, int) or isinstance(v, bool) for v in f + g):
        raise ValueError("telemetry full pair malformed")
    coordinates = bundle["coordinates"]
    if any(f[p] for p in coordinates["P"]) or [f[p] for p in coordinates["T"]] != mapping["u"]:
        raise ValueError("reconstructed pair violates pattern or actual guessed tail")
    ambient = sum(v * v for v in g) + sum(f[p] ** 2 for p in coordinates["C"])
    tail = sum(v * v for v in mapping["u"])
    full = sum(v * v for v in f + g)
    if row["ambient_residual_sq"] != ambient or row["tail_norm_sq"] != tail or row["full_norm_sq"] != full:
        raise ValueError("telemetry full/ambient/tail norm mismatch")
    if full != ambient + tail:
        raise ValueError("full norm decomposition mismatch")
    if bool(row["radius_pass"]) != (ambient <= 36 - tail):
        raise ValueError("radius flag mismatches actual complete ambient bound")
    # Each Babai output is reconstructed from the correct target. Check the
    # implied lattice vector using a polynomial membership equation.
    target = bundle["targets"][identifier]["used"]
    core_poly = [f[i] if i in coordinates["C"] else 0 for i in range(n)]
    vector_head = [g[i] + target[i] for i in range(n)]
    if ring_product(public["h"], core_poly, public["q"]) != [v % public["q"] for v in vector_head]:
        raise ValueError("telemetry implied vector is not in public lattice")
    implied_vector = vector_head + [f[p] for p in coordinates["C"]]
    for key in ("lattice_vector", "ambient_vector", "v"):
        if key in row and row[key] != implied_vector:
            raise ValueError("stored lattice vector differs from complete reconstruction")
    if "coefficients" in row:
        coefficients = row["coefficients"]
        dimension = bundle["dimensions"]["d"]
        if len(coefficients) != dimension or any(not isinstance(v, int) or isinstance(v, bool) for v in coefficients):
            raise ValueError("stored reduced basis coefficients malformed")
        combined = [sum(c * basis_row[j] for c, basis_row in zip(coefficients, bundle["basis"]["reduced"]))
                    for j in range(dimension)]
        if combined != implied_vector:
            raise ValueError("stored reduced basis coefficients do not reconstruct lattice vector")
    if row.get("projection_residual") is not None and row.get("projection_norm_sq_normalized") is not None:
        projected = sum(value * value for value in row["projection_residual"])
        if not math.isclose(projected, row["projection_norm_sq_normalized"], rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("normalized projection squared norm mismatch")
    validation = independently_validate(public, f, g)
    reported = row.get("public_validation")
    if reported is not None:
        reported_valid = reported.get("public_valid", reported.get("valid"))
        if reported_valid is not None and bool(reported_valid) != validation["public_valid"]:
            raise ValueError("telemetry public validity disagrees with independent validation")
    return validation


def no_entry_terminal_check(identifier, result, post):
    summaries = {row["backend_integer_id"]: row for row in result.get("target_summaries", [])}
    summary = summaries.get(identifier, {})
    if (summary.get("terminal") != "completed_no_backend_entry"
            or summary.get("post_lift_count") != 0
            or summary.get("original_backend_completed") is not False
            or identifier not in result.get("missing_external_results", [])):
        raise ValueError("missing final lift has no explicit no-entry terminal evidence")
    owner_groups = [group for group in result.get("projection_groups", []) if any(
        member["backend_integer_id"] == identifier for member in group["members"])]
    if len(owner_groups) != 1:
        raise ValueError("missing external result has ambiguous projection group identity")
    group = owner_groups[0].get("internal_id", owner_groups[0].get("projection_group"))
    if any(entry["group_id"] == group for entry in post["entries"]):
        raise ValueError("no-entry terminal contradicted by saved post-state entries")
    if group not in result.get("post_groups_with_no_entries", []):
        raise ValueError("no-entry group missing from explicit group-loss summary")
    return group


def config_check(stage, index, configuration, bundle, public, deadline):
    directory = stage / f"decode_p{index}_{configuration}"
    if not (directory / "result.json").exists():
        return None, {"status": "not_run_or_no_result", "reason": "result_missing"}
    result = read(directory / "result.json")
    check_deadline(deadline)
    submitted, completed = result.get("submitted_ids", []), result.get("completed_ids", [])
    if submitted != list(range(80)) and result.get("status") == "completed":
        raise ValueError("completed full80 diagnostic did not submit all ordered IDs")
    if completed != list(range(80)) and result.get("status") == "completed":
        raise ValueError("completed full80 diagnostic did not complete all ordered IDs")
    if any(identifier not in submitted for identifier in completed):
        raise ValueError("completed ID was never submitted")
    counters = result.get("counters", {})
    for key in ("iterations_entered", "iterations_completed", "itercount_slicer", "replacements", "redsucc", "kernel_calls"):
        value = counters.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise ValueError("invalid nonnegative integer counter " + key)
    entered, finished = counters.get("iterations_entered"), counters.get("iterations_completed")
    if entered is not None and finished is not None and not (0 <= finished <= entered):
        raise ValueError("kernel iteration counters violate entered/completed ordering")
    if configuration == "D-skip" and any(counters.get(key, 0) for key in ("iterations_entered", "iterations_completed", "kernel_calls")):
        raise ValueError("D-skip entered kernel")
    if configuration == "D-probe-1" and entered is not None and entered > 1:
        raise ValueError("single-step probe entered more than one iteration")
    if configuration == "D-probe-400" and entered is not None and entered > 400:
        raise ValueError("400-iteration probe exceeded iteration cap")
    if result.get("basis_sha256") is not None and result["basis_sha256"] != bundle["basis"]["reduced_sha256"]:
        raise ValueError("decoder started from a different reduced basis")
    counts, valid_rows, phase_ids = {}, [], {}
    identity_keys = set()
    no_entry_completed = []
    for ordinal, row in enumerate(read_jsonl(directory / "target_telemetry.jsonl")):
        if ordinal % 40 == 0:
            check_deadline(deadline)
        phase = row["phase"]
        key = (phase, row["backend_integer_id"], row.get("group_id"), row.get("sign"), row.get("slot_id"))
        if key in identity_keys:
            raise ValueError("duplicate telemetry entry identity")
        identity_keys.add(key)
        phase_ids.setdefault(phase, set()).add(row["backend_integer_id"])
        counts[phase] = counts.get(phase, 0) + 1
        validation = verify_entry(row, bundle, public)
        if validation["public_valid"]:
            valid_rows.append({"phase": phase, "backend_integer_id": row["backend_integer_id"],
                               "slot_id": row.get("slot_id"), "group_id": row.get("group_id"),
                               "sign": row.get("sign"), "uid": row.get("uid"),
                               "owner_candidate_returned": row.get("owner_candidate_returned"),
                               "validation": validation})
    if result.get("status") == "completed":
        final_phase = "babai" if configuration == "E" else "post"
        missing_lift_ids = set(range(80)) - phase_ids.get(final_phase, set())
        if missing_lift_ids and configuration == "E":
            raise ValueError("completed E diagnostic lacks per-target final lift telemetry")
        if missing_lift_ids:
            post = read(directory / "post_kernel_state.json")
            for identifier in sorted(missing_lift_ids):
                no_entry_terminal_check(identifier, result, post)
                no_entry_completed.append(identifier)
        if phase_ids.get(final_phase, set()) | set(no_entry_completed) != set(range(80)):
            raise ValueError("completed diagnostic lacks final lift or explicit no-entry evidence")
    if configuration.startswith("D-") and (directory / "post_kernel_state.json").exists():
        post = read(directory / "post_kernel_state.json")
        for key in ("iterations_entered", "iterations_completed"):
            if counters.get(key) != post[key]:
                raise ValueError("kernel counter differs from saved post-state")
        if counters.get("itercount_slicer") != post["iterations_completed"]:
            raise ValueError("legacy iteration counter differs from explicit completion count")
    candidate_validations = []
    for candidate in result.get("public_valid_candidates", []):
        pair = candidate.get("full_pair", candidate.get("reconstructed", candidate))
        validation = independently_validate(public, pair.get("f"), pair.get("g"))
        if not validation["public_valid"]:
            raise ValueError("claimed public candidate failed independent validation")
        candidate_validations.append(validation)
    return result, {"status": "verified", "worker_status": result.get("status"),
                    "submitted_count": len(submitted), "completed_count": len(completed),
                    "telemetry_rows_by_phase": counts,
                    "all_completed_external_ids_have_final_lift": (phase_ids.get("babai" if configuration == "E" else "post") == set(completed)),
                    "completed_no_backend_entry_ids": no_entry_completed,
                    "all_completed_ids_have_actual_lift_or_verified_no_entry_terminal":
                        (phase_ids.get("babai" if configuration == "E" else "post", set()) | set(no_entry_completed) == set(completed)),
                    "independently_public_valid_lifts": valid_rows,
                    "independently_public_valid_returned_candidates": candidate_validations,
                    "counters": counters, "pairing": result.get("pairing"), "stop": result.get("stop"),
                    "solver_success_flag_used": False}


def pairing_check(results, index):
    states = {configuration: result.get("pairing", {}) for (pattern, configuration), result in results.items()
              if pattern == index and configuration.startswith("D-") and result is not None}
    comparisons = []
    names = list(states)
    for name in names[1:]:
        first, second = states[names[0]], states[name]
        mismatch = [key for key in PAIR_KEYS if first.get(key) != second.get(key)]
        missing = [key for key in PAIR_KEYS if first.get(key) is None or second.get(key) is None]
        comparisons.append({"a": names[0], "b": name, "mismatched_fields": mismatch,
                            "unobservable_or_missing_fields": missing,
                            "observable_fields_match": not mismatch,
                            "strict_observable_start_pairing_confirmed": not mismatch and not missing})
    return {"pattern_index": index, "configurations": names, "comparisons": comparisons,
            "limitation": "Matching observed content/state hashes proves only captured state; hidden native internals remain limited by recorded observability."}


def audit_supervisions(stage, global_start):
    rows = []
    for path in sorted(stage.glob("*/supervision.json")):
        supervision = read(path)
        task = path.parent.name
        request = read(path.parent / "request.json")
        if not supervision.get("cleanup_verified") or supervision.get("residual_active_processes"):
            raise ValueError("unverified task cleanup: " + task)
        if not supervision.get("hard_deadline_met"):
            raise ValueError("task exceeded supervisor hard deadline: " + task)
        if supervision["finished_monotonic"] > request["hard_deadline"]:
            raise ValueError("task exceeded requested hard deadline: " + task)
        if request["hard_deadline"] > global_start + 4800:
            raise ValueError("task deadline exceeds global hard deadline")
        if request["hard_deadline"] - request["work_deadline"] < 5 - 1e-6:
            raise ValueError("task cleanup reserve shorter than five seconds")
        for process in supervision.get("observed_processes", []):
            current = _read_process(process["pid"])
            if current and current["starttime_ticks"] == process["starttime_ticks"] and current["state"] not in ("Z", "X"):
                raise ValueError("old supervised process still active")
        events = read_jsonl(path.parent / "events.jsonl")
        rows.append({"task_id": task, "execution_status": supervision["execution_status"],
                     "cleanup_verified": True, "hard_deadline_met": True,
                     "wall_seconds": supervision["total_wall_seconds"],
                     "peak_rss_bytes": supervision["memory"]["value"],
                     "stages": stage_intervals(events, supervision["finished_monotonic"])})
    return rows


def run(request):
    started = time.monotonic()
    stage, deadline = Path(request["stage3_path"]), request["work_deadline"]
    public = read(PUBLIC)
    if sha256(PUBLIC.read_bytes()).hexdigest() != EXPECTED_FILE or content_sha256(public) != EXPECTED_PUBLIC:
        raise ValueError("independent public input digest mismatch")
    numerical = read(stage / "numerical_started.json")
    global_start = numerical["global_started_monotonic"]
    report = {"schema": "ntru-stage3-independent-validation-v1", "status": "running",
              "all_checks_passed": False, "public_file_sha256": EXPECTED_FILE,
              "public_content_sha256": EXPECTED_PUBLIC, "private_reference_content_read": False,
              "solver_success_flags_used": False, "checks": {}, "configurations": {}, "issues": []}
    bundles, results = {}, {}
    for index in (0, 2):
        bundles[index], report["checks"][f"basis_{index}"] = basis_check(stage, index, public, deadline)
    witness_path = stage / "witness_consistency.json"
    if witness_path.exists():
        witness = read(witness_path)
        # Recompute the published-anchor public validity, rather than trusting
        # witness all_checks_passed as a validation of the candidate.
        anchor = witness.get("anchor_pair", {})
        report["checks"]["witness_anchor_fresh_validation"] = independently_validate(public, anchor.get("f"), anchor.get("g"))
    else:
        witness = None
        report["issues"].append("witness_consistency_missing")
    for index, configuration in CONFIGURATIONS:
        check_deadline(deadline)
        if bundles[index] is None:
            result, audit = None, {"status": "not_run_basis_unavailable"}
        else:
            result, audit = config_check(stage, index, configuration, bundles[index], public, deadline)
        results[(index, configuration)] = result
        report["configurations"][f"p{index}_{configuration}"] = audit
    report["checks"]["starting_state_pairing"] = [pairing_check(results, index) for index in (0, 2)]
    report["checks"]["index2_known_covered_external_ids_submitted"] = []
    if witness:
        pattern2 = next(row for row in witness.get("patterns", []) if row["pattern_index"] == 2)
        expected_ids = [row["external_id_mapping"]["backend_integer_id"] for row in pattern2["compatible_representatives"]]
        for (index, configuration), result in results.items():
            if index != 2 or result is None:
                continue
            actual = result.get("submitted_ids", [])
            report["checks"]["index2_known_covered_external_ids_submitted"].append(
                {"configuration": configuration, "known_external_ids": expected_ids,
                 "actually_submitted": all(identifier in actual for identifier in expected_ids)})
    report["checks"]["task_supervision_and_cleanup"] = audit_supervisions(stage, global_start)
    report["checks"]["global_budget_at_validation"] = {"elapsed_seconds": time.monotonic() - global_start,
         "within_4800_seconds": time.monotonic() < global_start + 4800,
         "scope": "Final outer cleanup/total must additionally be audited from execution.json after this process exits."}
    if not report["checks"]["global_budget_at_validation"]["within_4800_seconds"]:
        raise ValueError("global numerical budget exceeded")
    report.update(status="passed", all_checks_passed=not report["issues"], elapsed_seconds=time.monotonic() - started)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = read(args.request)
    started = time.monotonic()
    try:
        report = run(request)
        status = "completed" if report["all_checks_passed"] else "blocked"
    except BaseException as exc:
        report = {"schema": "ntru-stage3-independent-validation-v1", "status": "blocked",
                  "all_checks_passed": False, "error": f"{type(exc).__name__}: {exc}",
                  "traceback": traceback.format_exc(), "elapsed_seconds": time.monotonic() - started}
        status = "blocked"
    write(Path(request["stage3_path"]) / "validation.json", report)
    write(args.output / "result.json", {"status": status, "all_checks_passed": report["all_checks_passed"],
          "elapsed_seconds": time.monotonic() - started})
    if status != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
