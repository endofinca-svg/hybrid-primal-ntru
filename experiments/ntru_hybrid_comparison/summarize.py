"""Trial-level accounting, including failed attempts and necessary cleanup.

These helpers consume terminal records from the external supervisor.  They do
not interpret a worker's candidate flag as public validation and do not sum
overlapping decoder sidecar timings into measured cold-start wall costs.
"""

from collections import defaultdict
from collections.abc import Mapping
import math


def _wall_cost(record):
    value = record.get("total_wall_seconds")
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError("started trials require finite nonnegative total_wall_seconds")
    return float(value)


def _status_facts(record):
    """Preserve an attempt's original failure if cleanup changes its terminal status."""
    statuses = set()

    def add_fields(mapping):
        if isinstance(mapping, Mapping):
            for name in ("execution_status", "execution_status_before_cleanup"):
                if isinstance(mapping.get(name), str):
                    statuses.add(mapping[name])

    add_fields(record)
    add_fields(record.get("outer_supervision"))
    for attempt in record.get("attempts", []):
        add_fields(attempt)
        add_fields(attempt.get("supervision"))
    return statuses


def qualify_trial(record):
    """Return a conservative primary-comparison qualification with reasons.

    ``supervisor_public_validation`` must be the result of independently
    validating actual candidate arrays in the external owner process.  This is
    a protocol input, not a substitute for calling the public validator.  The
    runner must never copy it from a worker's output flag.
    """
    if not isinstance(record, Mapping):
        raise ValueError("trial record must be a mapping")
    reasons = []
    checks = (
        (record.get("started") is True, "not_started"),
        (record.get("cost_mode") == "standalone_end_to_end", "not_standalone_end_to_end"),
        (record.get("public_valid_observed") is True, "no_public_valid_observed"),
        (record.get("success_within_budget") is True, "not_success_within_budget"),
        (record.get("execution_status") == "completed", "execution_not_completed_normally"),
        (record.get("cleanup_verified") is True, "cleanup_not_verified"),
        (record.get("eligible_for_primary_comparison") is True, "record_ineligible"),
        (record.get("deadlines_satisfied") is True, "deadline_not_satisfied"),
    )
    reasons.extend(reason for passed, reason in checks if not passed)
    validation = record.get("supervisor_public_validation")
    if not isinstance(validation, Mapping) or validation.get("public_valid") is not True:
        reasons.append("no_independent_supervisor_public_validation")
    return {"qualified_success": not reasons, "disqualification_reasons": reasons,
            "success_contribution": int(not reasons)}


def _summarize_group(records, *, cost_mode, method):
    started = [record for record in records if record.get("started") is True]
    successes = sum(qualify_trial(record)["success_contribution"] for record in started)
    wall = sum(_wall_cost(record) for record in started)
    attempts = sum(len(record.get("attempts", [])) for record in started)
    timeout_statuses = {"timeout", "timed_out", "budget_exhausted"}
    exception_statuses = {"exception", "worker_exception", "worker_nonzero_exit",
                          "nonzero_exit", "protocol_error", "supervisor_exception",
                          "unexpected_parent_exit", "interrupted", "orphaned_descendants",
                          "supervisor_interrupted"}
    cleanup_failures = sum(record.get("cleanup_verified") is not True for record in started)
    return {
        "method": method,
        "cost_mode": cost_mode,
        "cost_scope": ("complete_cold_start_trial_including_failures_retries_and_cleanup"
                       if cost_mode == "standalone_end_to_end"
                       else "fixed_basis_decode_stage_excludes_input_construction_and_reduction"),
        "planned_trial_count": len(records),
        "started_trial_count": len(started),
        "not_started_planned_trial_count": len(records) - len(started),
        "budget_eligible_success_count": successes,
        "failed_trial_count": len(started) - successes,
        "timeout_trial_count": sum(bool(_status_facts(record) & timeout_statuses) for record in started),
        "exception_trial_count": sum(bool(_status_facts(record) & exception_statuses) for record in started),
        "cleanup_failed_or_unverified_trial_count": cleanup_failures,
        "public_valid_observed_trial_count": sum(record.get("public_valid_observed") is True for record in started),
        "total_pattern_attempt_count": attempts,
        "total_actual_wall_cost_seconds": wall,
        "cost_per_budget_eligible_success_seconds": wall / successes if successes else None,
        "cost_per_success_unavailable_reason": (None if successes else "no_budget_eligible_success_observed"),
        "counting_unit": "one_started_trial_contributes_at_most_one_success",
        "failure_categories_may_overlap": True,
    }


def summarize_trials(records):
    """Keep planned, failed, timed-out and successful trials in the denominator.

    Exact duplicate trial identities raise instead of silently dropping a run
    or charging/succeeding it twice.  Grouping always separates fixed-basis
    stage measurements from standalone cold-start trials.
    """
    if not isinstance(records, (list, tuple)):
        raise ValueError("records must be an explicit list or tuple")
    grouped = defaultdict(list)
    identities = set()
    qualifications = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ValueError("trial record must be a mapping")
        for name in ("trial_id", "method"):
            if not isinstance(record.get(name), str) or not record[name]:
                raise ValueError(f"{name} must be a nonempty string")
        cost_mode = record.get("cost_mode")
        if cost_mode not in ("fixed_basis", "standalone_end_to_end"):
            raise ValueError("cost_mode must explicitly name fixed_basis or standalone_end_to_end")
        if not isinstance(record.get("started"), bool):
            raise ValueError("started must be an explicit boolean")
        identity = (record.get("instance_id"), record["method"], record["trial_id"], cost_mode)
        try:
            duplicate = identity in identities
        except TypeError as error:
            raise ValueError("instance_id must be hashable") from error
        if duplicate:
            raise ValueError("duplicate trial identity")
        identities.add(identity)
        attempts = record.get("attempts", [])
        if not isinstance(attempts, (list, tuple)) or any(not isinstance(a, Mapping) for a in attempts):
            raise ValueError("attempts must be an explicit list or tuple of records")
        if not record["started"] and attempts:
            raise ValueError("a not-started planned trial cannot contain attempts")
        if record["started"]:
            _wall_cost(record)
        grouped[(cost_mode, record["method"])].append(record)
        qualifications.append({"instance_id": record.get("instance_id"), "method": record["method"],
                               "trial_id": record["trial_id"], "cost_mode": cost_mode,
                               **qualify_trial(record)})
    groups = [_summarize_group(grouped[key], cost_mode=key[0], method=key[1]) for key in sorted(grouped)]
    started = sum(group["started_trial_count"] for group in groups)
    successes = sum(group["budget_eligible_success_count"] for group in groups)
    wall = sum(group["total_actual_wall_cost_seconds"] for group in groups)
    modes = sorted({key[0] for key in grouped})
    homogeneous_standalone = modes == ["standalone_end_to_end"]
    if not homogeneous_standalone:
        cost_reason = "no_common_standalone_cost_scope"
    elif not successes:
        cost_reason = "no_budget_eligible_success_observed"
    else:
        cost_reason = None
    return {
        "schema": "ntru-trial-summary-v1",
        "planned_trial_count": len(records),
        "started_trial_count": started,
        "not_started_planned_trial_count": len(records) - started,
        "budget_eligible_success_count": successes,
        "failed_trial_count": sum(g["failed_trial_count"] for g in groups),
        "timeout_trial_count": sum(g["timeout_trial_count"] for g in groups),
        "exception_trial_count": sum(g["exception_trial_count"] for g in groups),
        "cleanup_failed_or_unverified_trial_count": sum(g["cleanup_failed_or_unverified_trial_count"] for g in groups),
        "total_pattern_attempt_count": sum(g["total_pattern_attempt_count"] for g in groups),
        "total_actual_wall_cost_seconds": wall,
        "cost_per_budget_eligible_success_seconds": (wall / successes if homogeneous_standalone and successes else None),
        "cost_per_success_unavailable_reason": cost_reason,
        "cost_modes": modes,
        "groups": groups,
        "trial_qualifications": qualifications,
        "posthoc_summary_not_part_of_online_success_decision": True,
    }
