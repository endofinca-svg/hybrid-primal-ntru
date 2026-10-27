"""Posthoc main-instance accounting; no key generation, reduction or decoding.

Run only after run/experiment.final.json exists. Outputs use exclusive creation
so a repeated invocation cannot overwrite evidence from the first summary.
Public array verification and protected-file checks belong to posthoc_validate.
"""

import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from experiments.ntru_hybrid_comparison.main_feasibility_20261027.posthoc_validate import stage_intervals
from experiments.ntru_hybrid_comparison.summarize import qualify_trial


HERE = Path(__file__).resolve().parent
PHASES = ("public_load", "input_construction", "reduction", "basis_row_recovery",
          "reduction_attach", "gso_initialization", "extra_preprocess", "decode",
          "lift_reconstruct", "public_verify")


def read(path):
    return json.loads(Path(path).read_text())


def maybe_read(path):
    try:
        data = read(path)
        if not isinstance(data, dict):
            raise ValueError("checkpoint is not an object")
        return data, None
    except (OSError, ValueError) as error:
        return {}, str(error)


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def events_from(path):
    if not path.is_file():
        return [], ["missing events file"]
    events, errors = [], []
    for index, line in enumerate(path.read_bytes().splitlines(keepends=True)):
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("sequence") != index:
                raise ValueError("event is not an ordered object")
            events.append(event)
            if not line.endswith(b"\n"):
                errors.append("last event has no terminating newline")
        except (ValueError, TypeError) as error:
            errors.append(f"event {index}: {error}")
            break
    return events, errors


def reduction_prefix(events, worker):
    durable = [event for event in events if event.get("event") == "reduction_progress"]
    snapshot = durable[-1] if durable else worker.get("reduction_progress")
    if not isinstance(snapshot, dict):
        return {"source": "not_observed", "completed_lll_calls": None, "completed_tours": None,
                "completed_stages": [], "active_stage": None, "requested_stages_are_not_completed": True}
    record = snapshot.get("reduction_record", {})
    actual = record.get("actual", {})
    completed = record.get("stages", [])
    return {"source": "last_durable_reduction_progress_event" if durable else "durable_worker_checkpoint_reduction_progress",
            "reduction_event": snapshot.get("reduction_event"), "observed_monotonic": snapshot.get("observed_monotonic"),
            "executor_elapsed_seconds": snapshot.get("executor_elapsed_seconds"),
            "completed_lll_calls": actual.get("completed_lll_calls"), "completed_tours": actual.get("completed_tours"),
            "completed_tours_by_beta": dict(Counter(str(row.get("beta")) for row in completed if row.get("kind") == "bkz")),
            "completed_reduction_call_seconds": sum(row.get("elapsed_seconds", 0) for row in completed),
            "completed_stages": completed, "active_stage": record.get("active_stage"),
            "exact_lattice_verification": record.get("exact_lattice_verification"),
            "actual_numeric_parameters": {key: actual.get(key) for key in
                                           ("float_type", "precision_bits", "gso_float_type", "seed_set", "threads", "strategy_sha256")},
            "requested_stages_are_not_completed": True}


def phase_accounting(events, worker, attempt, outer):
    inner = attempt.get("supervision", {})
    upper = inner.get("finished_monotonic", outer.get("finished_monotonic"))
    intervals = stage_intervals(events, termination_upper_monotonic=upper)
    work_stop = None
    if number(inner.get("started_monotonic")) is not None and number(inner.get("work_seconds")) is not None:
        work_stop = inner["started_monotonic"] + inner["work_seconds"]
    censored_names = set()
    for interval in intervals:
        if interval["censored"]:
            censored_names.add(interval["stage"])
            interval["supervision_work_stopped_monotonic"] = work_stop
            interval["observed_wall_to_work_stop_seconds"] = (max(0.0, work_stop - interval["started_monotonic"])
                                                                  if work_stop is not None else None)
            interval["time_scope"] = "wall observation window including journal/monitoring overhead; not exact backend or CPU time"
    timings = deepcopy(worker.get("timings", attempt.get("stage_timings", {})))
    exact = {}
    for name in PHASES:
        # A killed stage has no final timing: an initialized zero does not
        # describe its actual cost. Preserve the closed part separately.
        value = number(timings.get(name + "_seconds"))
        exact[name + "_seconds"] = None if name in censored_names else value
    return {"reported_checkpoint_timings": timings, "exact_closed_phase_seconds": exact,
            "censored_stage_names": sorted(censored_names), "observed_stage_intervals": intervals,
            "censored_stage_wall_is_not_added_to_exact_phase_seconds": True,
            "all_costs_remain_in_measured_trial_wall": True}


def backend_accounting(worker, events, method, config):
    metadata = worker.get("backend_metadata", {})
    metrics_sources = [worker.get("backend_metrics", {})]
    batches = worker.get("backend_batches", [])
    for batch in batches:
        metrics_sources.append(batch.get("returned", {}).get("metrics", {}))
    metrics = {}
    for source in metrics_sources:
        for key, value in source.items():
            if number(value) is not None:
                metrics[key] = max(metrics.get(key, 0), value)
    evidence = worker.get("backend_call_evidence")
    if not isinstance(evidence, list):
        evidence = [row for batch in batches for row in batch.get("returned", {}).get("call_evidence", [])]
    entered = {row.get("stage") for row in events if row.get("event") == "stage_started"}
    terminal = worker.get("current_stage") == "terminal" and worker.get("status") != "running"
    not_reached = "gso_initialization" not in entered
    exact_metrics = terminal and (bool(worker.get("backend_metrics")) or not_reached)
    before_backend_stages = {"initialization", "public_load", "input_construction", "reduction",
                             "basis_row_recovery", "reduction_attach"}
    # A durable checkpoint inside the sequential pre-backend prefix proves no
    # decoder call has occurred, even if that worker was killed. Absence of a
    # final metrics dictionary alone is not evidence of an unknown call count.
    stage_proves_prefix = worker.get("current_stage") in before_backend_stages
    if worker.get("current_stage") == "public_verify" and "basis_row_recovery" in entered:
        stage_proves_prefix = True
    prefix_zero_proven = bool(not_reached and stage_proves_prefix and not metadata
                             and not evidence and not metrics.get("slicer_kernel_calls", 0)
                             and not metrics.get("slicer_kernel_completed_calls", 0))
    method_has_no_slicer = method in ("B", "E")
    slicer_count_complete = bool(method_has_no_slicer or prefix_zero_proven or exact_metrics)
    calls = metrics.get("slicer_kernel_calls", 0)
    completed_calls = metrics.get("slicer_kernel_completed_calls", 0)
    if method_has_no_slicer or prefix_zero_proven or (not_reached and terminal):
        calls = completed_calls = 0
    return {"requested_slicer_configuration": deepcopy(config.get("backend_config", {})) if method == "D" else None,
            "slicer_options_do_not_describe_babai_execution": method == "E",
            "backend_not_reached": not_reached, "backend_metadata": metadata,
            "actual_projection_dimension": metadata.get("actual_projection_dimension"),
            "actual_lattice_db_size": metadata.get("lattice_db_size"),
            "actual_preprocessing": metadata.get("preprocessing"),
            "actual_kernel_iteration_limit": metadata.get("kernel_iteration_limit"),
            "actual_threads": metadata.get("nthreads"),
            "slicer_kernel_calls": calls if slicer_count_complete else None,
            "observed_slicer_kernel_calls_lower_bound": calls,
            "slicer_kernel_completed_calls": completed_calls if slicer_count_complete else None,
            "observed_slicer_kernel_completed_calls_lower_bound": completed_calls,
            "backend_metric_scope_complete": exact_metrics,
            "slicer_call_count_observation_complete": slicer_count_complete,
            "zero_slicer_calls_proven_by_method": method_has_no_slicer,
            "zero_backend_calls_proven_by_sequential_prefix": prefix_zero_proven,
            "zero_call_proof_current_stage": worker.get("current_stage") if prefix_zero_proven else None,
            "backend_metrics_observed": metrics, "call_evidence": evidence,
            "kernel_iterations_observed": [row.get("observable_iterations") for row in evidence if "observable_iterations" in row],
            "projection_group_records_in_returned_batches": sum(len(batch.get("returned", {}).get("projection_groups", [])) for batch in batches),
            "projection_group_count_scope": "sum of returned batch group records, not inferred global unique groups",
            "no_backend_execution_inferred_from_requested_configuration": True}


def attempt_row(directory, trial, plan, attempt):
    index = attempt["pattern_attempt_index"]
    path = directory / "run" / trial["trial_id"] / "owner" / f"attempt_{index:03d}"
    worker, read_error = maybe_read(path / "result.json")
    events, event_errors = events_from(path / "events.jsonl")
    request, request_error = maybe_read(path / "request.json")
    coordinates = attempt.get("coordinates", worker.get("coordinates", request.get("coordinates", {})))
    accounting = phase_accounting(events, worker, attempt, trial.get("outer_supervision", {}))
    validations = attempt.get("owner_candidate_validations", [])
    rejected = [row for row in validations if row.get("public_validation", {}).get("public_valid") is not True]
    reasons = Counter(reason for row in rejected for reason in row.get("public_validation", {}).get("rejection_reasons", []))
    candidates = []
    for row in validations:
        tail = coordinates.get("T", [])
        f = row.get("f")
        zero_tail = bool(row.get("source") == "basis_row" and tail and isinstance(f, list)
                         and all(isinstance(i, int) and 0 <= i < len(f) and f[i] == 0 for i in tail))
        candidates.append({"source": row.get("source"), "owner_public_valid": row.get("public_validation", {}).get("public_valid"),
                           "rejection_reasons": row.get("public_validation", {}).get("rejection_reasons", []),
                           "worker_observed_monotonic": row.get("worker_observed_monotonic"),
                           "owner_validation_finished_monotonic": row.get("owner_validation_finished_monotonic"),
                           "received_within_attempt_work_budget": row.get("received_within_attempt_work_budget"),
                           "basis_row_zero_tail_padding_branch": zero_tail,
                           "f_T": [f[i] for i in tail] if zero_tail else None,
                           "credit_to_slicer": False if row.get("source") == "basis_row" else None})
    progress = attempt.get("progress", worker.get("progress", {}))
    counts = worker.get("candidate_counts")
    backend = backend_accounting(worker, events, trial["method"], plan["config"]["worker"])
    return {"trial_id": trial["trial_id"], "method": trial["method"], "pattern_attempt_index": index,
            "coordinates": coordinates, "seeds": attempt.get("seeds", worker.get("seeds")),
            "pattern_repeated": attempt.get("pattern_repeated"), "public_file_sha256": worker.get("public_file_sha256"),
            "public_content_sha256": worker.get("public_content_sha256"), "dimensions": worker.get("dimensions"),
            "input_digests": attempt.get("input_digests", worker.get("input_digests", {})),
            "execution_status": attempt.get("execution_status"), "worker_status": worker.get("status"),
            "worker_current_stage": worker.get("current_stage"), "worker_stop_reason": worker.get("stop_reason"),
            "worker_error": worker.get("error"), "protocol_errors": attempt.get("protocol_errors", []),
            "public_valid_observed": attempt.get("public_valid_observed", False),
            "success_within_budget": attempt.get("success_within_budget", False),
            "total_wall_seconds": attempt.get("total_wall_seconds"),
            "supervised_worker_tree_wall_seconds": attempt.get("supervision", {}).get("total_wall_seconds"),
            "cleanup_seconds": attempt.get("supervision", {}).get("cleanup_seconds"),
            "cleanup_verified": attempt.get("cleanup_verified", attempt.get("supervision", {}).get("cleanup_verified")),
            "candidate_counts": counts, "target_count_expected_per_constructed_pattern": 0 if trial["method"] == "B" else 80,
            "target_progress": {key: progress.get(key) for key in ("target_count", "submitted_count", "completed_count", "errored_count", "cancelled_count", "pending_count", "unsubmitted_count", "conservation_holds")},
            "owner_candidate_count": len(validations), "owner_rejected_candidate_count": len(rejected),
            "owner_public_rejection_reason_counts": dict(reasons), "candidate_source_counts": dict(Counter(row.get("source", "unknown") for row in validations)),
            "owner_candidate_classifications": candidates,
            "basis_row_zero_tail_branch_count": sum(row["basis_row_zero_tail_padding_branch"] for row in candidates),
            "phase_accounting": accounting, "reduction_completed_prefix": reduction_prefix(events, worker),
            "backend": backend, "memory_worker_tree_scope": attempt.get("supervision", {}).get("memory"),
            "read_errors": {"worker": read_error, "request": request_error, "events": event_errors},
            "sources": {"worker_result": str(path / "result.json"), "events": str(path / "events.jsonl"), "request": str(path / "request.json")}}


def method_row(directory, trial, plan):
    attempts = [attempt_row(directory, trial, plan, attempt) for attempt in trial.get("attempts", [])]
    qualification = qualify_trial(trial)
    closed_totals, whole_phase_totals = {}, {}
    for name in PHASES:
        key = name + "_seconds"
        known = [row["phase_accounting"]["reported_checkpoint_timings"].get(key) for row in attempts]
        closed_totals[key] = sum(value for value in known if number(value) is not None)
        values = [row["phase_accounting"]["exact_closed_phase_seconds"].get(key) for row in attempts]
        whole_phase_totals[key] = sum(values) if all(number(v) is not None for v in values) else None
    rejection_counts = Counter()
    source_counts = Counter()
    for row in attempts:
        rejection_counts.update(row["owner_public_rejection_reason_counts"])
        source_counts.update(row["candidate_source_counts"])
    generated = [row["candidate_counts"].get("generated_raw_count") if isinstance(row["candidate_counts"], dict) else None for row in attempts]
    filtered = [row["candidate_counts"].get("policy_kept_count") if isinstance(row["candidate_counts"], dict) else None for row in attempts]
    unique = [row["candidate_counts"].get("exact_unique_count") if isinstance(row["candidate_counts"], dict) else None for row in attempts]
    if trial["method"] == "B":
        generated = filtered = unique = [0 for _ in attempts]
    target_totals = {key: sum(row["target_progress"].get(key) or 0 for row in attempts) for key in
                     ("target_count", "submitted_count", "completed_count", "errored_count", "cancelled_count", "pending_count", "unsubmitted_count")}
    success = qualification["qualified_success"]
    status = trial.get("execution_status")
    outcome = "success" if success else ("not_started" if not trial.get("started") else
                                        "timeout" if status in ("timeout", "budget_exhausted") else "failure")
    metrics_complete = all(row["backend"]["slicer_call_count_observation_complete"] for row in attempts)
    kernel_lower = sum(row["backend"]["observed_slicer_kernel_calls_lower_bound"] for row in attempts)
    memory = trial.get("memory", {})
    return {"method": trial["method"], "trial_id": trial["trial_id"], "instance_id": trial.get("instance_id"),
            "mode": trial["cost_mode"], "started": trial.get("started"), "outcome": outcome,
            "execution_status": status, "stop_reason": trial.get("stop_reason"), "qualification": qualification,
            "success_within_budget": success, "public_valid_observed": trial.get("public_valid_observed", False),
            "success_source": trial.get("first_public_valid", {}).get("source") if success else None,
            "first_public_valid": trial.get("first_public_valid"), "pattern_attempt_count": len(attempts),
            "failed_pattern_count": sum(not row["success_within_budget"] for row in attempts),
            "timeout_pattern_count": sum(row["execution_status"] == "timeout" for row in attempts),
            "abnormal_pattern_count": sum(row["execution_status"] not in ("completed", "timeout") for row in attempts),
            "requested_trial_budget_seconds": plan["config"]["trial_budget_seconds"],
            "cleanup_reserve_seconds": plan["config"]["cleanup_reserve_seconds"],
            "max_pattern_attempts": plan["config"]["max_pattern_attempts"], "pattern_rule": plan["config"]["pattern_rule"],
            "k": plan["config"]["k"], "kappa": plan["config"]["kappa"], "dimension": 256 - plan["config"]["k"] - plan["config"]["kappa"],
            "requested_reduction_protocol": plan["config"]["worker"]["reduction_protocol"],
            "total_wall_seconds": trial["total_wall_seconds"],
            "cost_per_budget_success_seconds": trial["total_wall_seconds"] if success else None,
            "cost_per_budget_success_unavailable_reason": None if success else "no_budget_eligible_success_observed",
            "phase_seconds_exact_if_complete": whole_phase_totals,
            "phase_seconds_closed_known_totals": closed_totals,
            "censored_stage_windows": [{"pattern_attempt_index": row["pattern_attempt_index"], **window} for row in attempts
                                       for window in row["phase_accounting"]["observed_stage_intervals"] if window["censored"]],
            "observed_completed_lll_calls": sum(row["reduction_completed_prefix"].get("completed_lll_calls") or 0 for row in attempts),
            "observed_completed_bkz_tours": sum(row["reduction_completed_prefix"].get("completed_tours") or 0 for row in attempts),
            "actual_raw_candidates_generated": sum(generated) if all(number(x) is not None for x in generated) else None,
            "actual_policy_kept_candidates": sum(filtered) if all(number(x) is not None for x in filtered) else None,
            "actual_exact_unique_candidates_across_pattern_attempts": sum(unique) if all(number(x) is not None for x in unique) else None,
            "observed_raw_generated_count_lower_bound": sum(x for x in generated if number(x) is not None),
            "target_totals_across_pattern_attempts": target_totals,
            "owner_rejected_candidate_count": sum(row["owner_rejected_candidate_count"] for row in attempts),
            "owner_public_rejection_reason_counts": dict(rejection_counts), "candidate_source_counts": dict(source_counts),
            "basis_row_zero_tail_branch_count": sum(row["basis_row_zero_tail_branch_count"] for row in attempts),
            "zero_tail_basis_row_is_not_slicer_success": True,
            "slicer_kernel_calls": kernel_lower if metrics_complete else None,
            "observed_slicer_kernel_calls_lower_bound": kernel_lower,
            "slicer_calls_observation_complete": metrics_complete,
            "requested_slicer_configuration": plan["config"]["worker"].get("backend_config") if trial["method"] == "D" else None,
            "actual_slicer_projection_dimensions": sorted({row["backend"]["actual_projection_dimension"] for row in attempts if row["backend"]["actual_projection_dimension"] is not None}),
            "actual_slicer_database_sizes": sorted({row["backend"]["actual_lattice_db_size"] for row in attempts if row["backend"]["actual_lattice_db_size"] is not None}),
            "outer_cleanup_seconds": trial.get("cleanup_seconds"),
            "inner_worker_cleanup_seconds_sum": sum(row["cleanup_seconds"] or 0 for row in attempts),
            "cleanup_scopes_are_nested_and_not_readded_to_total": True,
            "cleanup_verified": trial.get("cleanup_verified"), "deadlines_satisfied": trial.get("deadlines_satisfied"),
            "memory_bytes": memory.get("value"), "memory_mib": memory["value"] / 2**20 if number(memory.get("value")) is not None else None,
            "memory_metric": memory.get("metric_name"), "memory_scope": memory.get("trial_scope", memory.get("scope")),
            "memory_full_metadata": memory, "public_file_sha256_observed": sorted({row["public_file_sha256"] for row in attempts if row["public_file_sha256"]}),
            "attempts": attempts}


def summarize(directory):
    directory = Path(directory).resolve()
    if not (directory / "run/experiment.final.json").is_file():
        raise RuntimeError("run/experiment.final.json is absent; this summarizer does not monitor, wait, or launch work")
    outputs = [directory / name for name in ("actual_results.json", "actual_results.csv")]
    if any(path.exists() for path in outputs):
        raise FileExistsError("refuse to overwrite existing actual_results.json/csv")
    experiment, plan = read(directory / "run/experiment.final.json"), read(directory / "plan.json")
    history = read(directory / "history_evidence.json")
    expected_public_sha = history["public_instance"]["file_sha256"]
    planned = {row["trial_id"]: row for row in plan["trials"]}
    rows = [method_row(directory, trial, planned[trial["trial_id"]]) for trial in experiment["trials"]]
    if [row["method"] for row in rows] != ["B", "E", "D"]:
        raise ValueError("expected one ordered B/E/D run")
    paired = []
    methods = {row["method"]: row for row in rows}
    e_attempts = {row["pattern_attempt_index"]: row for row in methods["E"]["attempts"]}
    d_attempts = {row["pattern_attempt_index"]: row for row in methods["D"]["attempts"]}
    for index in sorted(set(e_attempts) & set(d_attempts)):
        e, d = e_attempts[index], d_attempts[index]
        digests = {}
        for key in ("coordinates_sha256", "candidate_ordered_sha256", "ordered_targets_sha256", "id_mapping_sha256", "original_basis_sha256", "reduced_basis_sha256"):
            left, right = e["input_digests"].get(key), d["input_digests"].get(key)
            digests[key] = {"E": left, "D": right, "comparison": "equal" if left is not None and left == right else
                            "different" if left is not None and right is not None else "not_observed_in_both"}
        paired.append({"pattern_attempt_index": index, "P_equal": e["coordinates"].get("P") == d["coordinates"].get("P"),
                       "T_equal": e["coordinates"].get("T") == d["coordinates"].get("T"),
                       "C_equal": e["coordinates"].get("C") == d["coordinates"].get("C"), "seeds_equal": e["seeds"] == d["seeds"],
                       "digests": digests, "missing_reduced_basis_is_not_a_mismatch": True})
    observed = {sha for row in rows for sha in row["public_file_sha256_observed"]}
    total = sum(row["total_wall_seconds"] for row in rows)
    successful = sum(row["success_within_budget"] for row in rows)
    result = {"schema": "ntru-main-feasibility-results-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "one reused n128 public instance, one B/E/D trial per method; feasibility only, not statistical acceleration",
              "public_instance": history["public_instance"], "expected_public_file_sha256": expected_public_sha,
              "observed_public_file_sha256_set": sorted(observed),
              "all_observed_worker_public_hashes_match_original": observed == {expected_public_sha},
              "method_public_hash_observation_missing": [row["method"] for row in rows if not row["public_file_sha256_observed"]],
              "method_order": [row["method"] for row in rows], "requested_global_budget_seconds": plan["total_budget_seconds"],
              "sum_trial_wall_seconds": total, "global_online_wall_seconds": experiment["total_online_wall_seconds"],
              "global_overhead_seconds": experiment["global_overhead_seconds"],
              "experiment_hard_deadline_met": experiment["experiment_hard_deadline_met"],
              "budget_eligible_success_count": successful, "failed_trial_count": sum(row["started"] and not row["success_within_budget"] for row in rows),
              "all_failures_timeouts_retries_and_cleanup_in_trial_wall": True,
              "mixed_method_cost_per_success_is_not_an_acceleration_estimate": True,
              "summary_from_measured_trials": experiment["summary"], "E_D_same_index_pair_checks": paired,
              "unpaired_indices_are_not_silently_dropped": {"E_only": sorted(set(e_attempts) - set(d_attempts)), "D_only": sorted(set(d_attempts) - set(e_attempts))},
              "posthoc_summary_does_not_change_online_success": True, "private_contents_read": False, "rows": rows}
    csv_rows = []
    for row in rows:
        phases = row["phase_seconds_exact_if_complete"]
        csv_rows.append({"method": row["method"], "instance_id": row["instance_id"], "mode": row["mode"], "outcome": row["outcome"],
                         "execution_status": row["execution_status"], "pattern_attempts": row["pattern_attempt_count"],
                         "k": row["k"], "kappa": row["kappa"], "dimension": row["dimension"], "trial_budget_seconds": row["requested_trial_budget_seconds"],
                         "total_wall_seconds": row["total_wall_seconds"], "reduction_seconds_exact_if_complete": phases["reduction_seconds"],
                         "reduction_seconds_closed_known": row["phase_seconds_closed_known_totals"]["reduction_seconds"],
                         "extra_preprocess_seconds_exact_if_complete": phases["extra_preprocess_seconds"],
                         "decode_seconds_exact_if_complete": phases["decode_seconds"],
                         "censored_stage_windows": json.dumps(row["censored_stage_windows"], sort_keys=True),
                         "actual_completed_lll": row["observed_completed_lll_calls"], "actual_completed_bkz_tours": row["observed_completed_bkz_tours"],
                         "raw_candidates_generated": row["actual_raw_candidates_generated"],
                         "targets_submitted": row["target_totals_across_pattern_attempts"]["submitted_count"],
                         "targets_completed": row["target_totals_across_pattern_attempts"]["completed_count"],
                         "targets_pending": row["target_totals_across_pattern_attempts"]["pending_count"],
                         "public_valid_observed": row["public_valid_observed"], "success_within_budget": row["success_within_budget"],
                         "success_source": row["success_source"], "zero_tail_basis_row_count": row["basis_row_zero_tail_branch_count"],
                         "public_rejection_reason_counts": json.dumps(row["owner_public_rejection_reason_counts"], sort_keys=True),
                         "slicer_calls_observed_lower_bound": row["observed_slicer_kernel_calls_lower_bound"],
                         "slicer_calls_exact_if_observed": row["slicer_kernel_calls"],
                         "actual_slicer_projection_dimensions": json.dumps(row["actual_slicer_projection_dimensions"]),
                         "actual_slicer_db_sizes": json.dumps(row["actual_slicer_database_sizes"]),
                         "memory_bytes": row["memory_bytes"], "memory_mib": row["memory_mib"], "memory_metric": row["memory_metric"],
                         "memory_scope": row["memory_scope"], "cleanup_verified": row["cleanup_verified"],
                         "outer_cleanup_seconds": row["outer_cleanup_seconds"], "inner_cleanup_seconds": row["inner_worker_cleanup_seconds_sum"],
                         "stop_reason": row["stop_reason"], "cost_per_budget_success_seconds": row["cost_per_budget_success_seconds"]})
    with outputs[0].open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    with outputs[1].open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    return result


def check_slicer_call_count_evidence():
    """Pure regression checks; no result files or backend are touched."""
    prefix_events = [{"event": "stage_started", "stage": "reduction"}]
    for method in ("B", "E", "D"):
        row = backend_accounting({"status": "running", "current_stage": "reduction"},
                                 prefix_events, method, {})
        assert row["slicer_kernel_calls"] == row["slicer_kernel_completed_calls"] == 0
        assert row["slicer_call_count_observation_complete"]
        assert row["zero_backend_calls_proven_by_sequential_prefix"]
        assert row["backend_metric_scope_complete"] is False
    decode_events = [{"event": "stage_started", "stage": "gso_initialization"},
                     {"event": "stage_started", "stage": "decode"}]
    uncertain = backend_accounting({"status": "running", "current_stage": "decode"}, decode_events, "D", {})
    assert uncertain["slicer_kernel_calls"] is None
    assert uncertain["slicer_call_count_observation_complete"] is False
    babai = backend_accounting({"status": "running", "current_stage": "decode"}, decode_events, "E", {})
    assert babai["slicer_kernel_calls"] == 0 and babai["slicer_call_count_observation_complete"]
    missing = backend_accounting({}, [], "D", {})
    assert missing["slicer_kernel_calls"] is None
    one_call = backend_accounting({"status": "completed", "current_stage": "terminal",
                                   "backend_metrics": {"slicer_kernel_calls": 1, "slicer_kernel_completed_calls": 1},
                                   "backend_call_evidence": [{"observable_iterations": 0}]}, decode_events, "D", {})
    assert one_call["slicer_kernel_calls"] == 1
    assert one_call["kernel_iterations_observed"] == [0]
    zero_retry = backend_accounting({"status": "running", "current_stage": "reduction"}, prefix_events, "D", {})
    assert one_call["slicer_kernel_calls"] + zero_retry["slicer_kernel_calls"] == 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=str(HERE))
    args = parser.parse_args()
    check_slicer_call_count_evidence()
    result = summarize(args.directory)
    print(json.dumps({"rows": [{key: row[key] for key in ("method", "outcome", "pattern_attempt_count", "total_wall_seconds", "success_source", "cleanup_verified")} for row in result["rows"]],
                      "sum_trial_wall_seconds": result["sum_trial_wall_seconds"], "global_online_wall_seconds": result["global_online_wall_seconds"]}, indent=2))


if __name__ == "__main__":
    main()
