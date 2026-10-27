"""Session-supervised owner for bounded cold-start B/D/E trials.

The owner performs public loading, validation, journaling, and worker launch
inside the outer session. Inner workers get private process groups in that
same session. Thus killing a stuck owner does not lose its lattice workers.
Formatting final reports and replaying interrupted journals is posthoc work.
"""

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import time

from .budgets import create_trial_window
from .inputs import content_sha256
from .standalone import (_append, _memory_across_attempts, _progress_from_events,
                         _read_events, _write_new, run_trial, validate_trial_config)
from .summarize import summarize_trials
from .supervisor import supervise_command


def recover_owner_progress(owner_dir, *, method, trial_id, config):
    """Replay durable facts after interruption, without inventing success.

    Worker candidate flags have not necessarily reached owner public validation.
    Keep those arrays as unvalidated evidence, never as budgeted success.
    """
    directory = Path(owner_dir)
    owner_events = []
    path = directory / "owner_online.jsonl"
    if path.is_file():
        for line in path.read_bytes().splitlines():
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    owner_events.append(row)
            except ValueError:
                break
    prepared = {row["pattern_attempt_index"]: deepcopy(row) for row in owner_events
                if row.get("event") == "pattern_prepared"}
    terminal = {row["pattern_attempt_index"]: deepcopy(row) for row in owner_events
                if row.get("event") == "pattern_terminal"}
    for request_path in sorted(directory.glob("attempt_*/request.json")):
        index = int(request_path.parent.name.removeprefix("attempt_"))
        try:
            request = json.loads(request_path.read_text())
            if request["pattern_attempt_index"] != index:
                raise ValueError("attempt directory/request index mismatch")
            prepared.setdefault(index, {"pattern_attempt_index": index, "coordinates": request["coordinates"],
                                        "seeds": request["seeds"], "config_sha256": request["config_sha256"]})
        except (OSError, ValueError, KeyError, TypeError) as error:
            prepared.setdefault(index, {"pattern_attempt_index": index})
            prepared[index].setdefault("request_replay_errors", []).append(str(error))
    attempts = []
    for index, entry in sorted(prepared.items()):
        attempt = terminal.get(index, entry)
        attempt_dir = directory / f"attempt_{index:03d}"
        events, errors = _read_events(attempt_dir / "events.jsonl")
        worker = None
        try:
            worker = json.loads((attempt_dir / "result.json").read_text())
        except (OSError, ValueError):
            pass
        attempt["events"] = events
        attempt["progress"] = _progress_from_events(events, worker)
        attempt["unvalidated_candidate_events"] = [e for e in events if e.get("event") == "candidate"]
        attempt["replay_errors"] = errors
        attempt["owner_terminal_received"] = index in terminal
        attempt["success_within_budget"] = False
        if index not in terminal:
            attempt["execution_status"] = "owner_interrupted"
            attempt["current_stage"] = events[-1].get("stage") if events else "before_worker_events"
        attempts.append(attempt)
    validations = [v for a in attempts for v in a.get("owner_candidate_validations", [])
                   if v.get("public_validation", {}).get("public_valid") is True]
    return {"schema": "ntru-independent-trial-v1", "method": method, "trial_id": trial_id,
            "instance_id": next((r.get("instance_id") for r in attempts if r.get("instance_id")), None),
            "config": deepcopy(config), "config_sha256": content_sha256(config),
            "cost_mode": "standalone_end_to_end", "started": True, "attempts": attempts,
            "public_valid_observed": bool(validations), "public_candidates": validations,
            "success_within_budget": False, "eligible_for_primary_comparison": False,
            "execution_status": "owner_interrupted", "cleanup_verified": False,
            "supervisor_public_validation": validations[0]["public_validation"] if validations else None,
            "first_public_valid": validations[0] if validations else None,
            "deadlines_satisfied": False, "stop_reason": "owner_result_missing_or_invalid",
            "recovered_from_durable_partial_events": True,
            "unvalidated_worker_candidates_do_not_count_as_public_valid": True,
            "owner_event_count": len(owner_events), "memory": _memory_across_attempts(attempts)}


def run_guarded_trial(public_path, config, method, trial_id, output_dir, *, experiment_started_at,
                      total_budget_seconds, guard_cleanup_reserve_seconds=.2,
                      supervisor=supervise_command, clock=time.monotonic):
    """Charge setup, independent owner/worker tree, cleanup and final journal.

    The guard is a small supervisor; public pipeline operations are preemptible
    in its owner. Final metadata IO is cooperative and measured, with any hard
    deadline overrun disqualifying success. This is not a real-time OS promise.
    """
    started = clock()
    validate_trial_config(config, method)
    reserve = guard_cleanup_reserve_seconds
    if (isinstance(reserve, bool) or not isinstance(reserve, (int, float))
            or not math.isfinite(reserve) or reserve <= 0
            or reserve >= config["cleanup_reserve_seconds"]):
        raise ValueError("positive guard reserve must fit inside trial cleanup reserve")
    window = create_trial_window(experiment_started_at=experiment_started_at,
                                 total_budget_seconds=total_budget_seconds, trial_started_at=started,
                                 trial_budget_seconds=config["trial_budget_seconds"],
                                 cleanup_reserve_seconds=config["cleanup_reserve_seconds"])
    base = {"trial_id": trial_id, "method": method, "instance_id": None, "started": False,
            "cost_mode": "standalone_end_to_end", "attempts": [], "total_wall_seconds": 0.0,
            "cleanup_verified": True, "success_within_budget": False, "public_valid_observed": False,
            "execution_status": "not_started", "eligible_for_primary_comparison": False,
            "deadlines_satisfied": True, "budget": window.to_record(),
            "stop_reason": "insufficient_budget_before_owner", "config_sha256": content_sha256(config)}
    if not window.can_start(now=clock(), minimum_work_seconds=config.get("minimum_attempt_work_seconds", .02)):
        return base
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    owner_dir = directory / "owner"
    request = {"schema": "ntru-guarded-owner-request-v1", "public_path": str(Path(public_path).resolve()),
               "config": config, "method": method, "trial_id": trial_id,
               "owner_output_dir": str(owner_dir), "trial_started_at": started,
               "experiment_started_at": experiment_started_at, "total_budget_seconds": total_budget_seconds}
    _write_new(directory / "owner.request.json", request)
    journal = directory / "guard_online.jsonl"
    _append(journal, {"event": "guard_started", "monotonic": clock(), "trial_started_monotonic": started,
                      "budget": window.to_record(), "owner_request_sha256": content_sha256(request)})
    command = [sys.executable, "-B", "-m", "experiments.ntru_hybrid_comparison.guarded",
               "--owner-request", str(directory / "owner.request.json")]
    try:
        supervision = supervisor(command, work_deadline=window.hard_deadline - reserve,
                                 hard_deadline=window.hard_deadline, cwd=str(Path(__file__).resolve().parents[2]),
                                 env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                 stdout_path=directory / "owner.stdout.txt", stderr_path=directory / "owner.stderr.txt")
    except BaseException as error:
        supervision = {"execution_status": "cleanup_unverified", "cleanup_verified": False,
                       "hard_deadline_met": False, "returncode": None, "cleanup_seconds": None,
                       "supervisor_errors": [{"type": type(error).__name__, "message": str(error)}]}
    owner_error = None
    try:
        record = json.loads((owner_dir / "trial.final.json").read_text())
        for key, expected in (("method", method), ("trial_id", trial_id), ("config_sha256", content_sha256(config)),
                              ("cost_mode", "standalone_end_to_end"), ("started", True)):
            if record.get(key) != expected:
                raise ValueError("owner binding mismatch: " + key)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        owner_error = str(error)
        record = recover_owner_progress(owner_dir, method=method, trial_id=trial_id, config=config)
    inner_status = record["execution_status"]
    record["owner_diagnostics"] = {key: deepcopy(record.get(key)) for key in
                                   ("total_wall_seconds", "cleanup_seconds", "memory", "execution_status",
                                    "deadlines_satisfied", "eligible_for_primary_comparison", "online_finished_monotonic")}
    record["outer_supervision"] = supervision
    record["owner_result_error"] = owner_error
    record["started_monotonic"] = started
    record["budget"] = window.to_record()
    record["guard_cleanup_reserve_seconds"] = reserve
    record["guard_work_deadline_relative_seconds"] = window.hard_deadline - reserve - started
    outer_status = supervision.get("execution_status", "supervisor_exception")
    outer_normal = outer_status == "completed" and supervision.get("returncode") == 0
    record["cleanup_verified"] = supervision.get("cleanup_verified") is True
    if not outer_normal:
        record["execution_status"] = outer_status
        record["stop_reason"] = "owner_boundary_" + outer_status
    elif owner_error:
        record["execution_status"] = "protocol_error"
        record["stop_reason"] = "missing_or_invalid_owner_result"
    else:
        record["execution_status"] = inner_status
    if not record["cleanup_verified"]:
        record["execution_status"] = "cleanup_unverified"
        record["stop_reason"] = "cleanup_unverified_stop_all_following_work"
    record["memory"] = deepcopy(supervision.get("memory", {"value": None, "missing_reason": "no outer RSS evidence"}))
    record["memory"]["trial_scope"] = "owner plus lattice workers and descendants in one session, through cleanup; excludes guard"
    record["cleanup_seconds"] = supervision.get("cleanup_seconds")
    record["cleanup_time_scope"] = "outer cleanup (may overlap inner worker cleanup); inner cleanup is separate diagnostic and is not added again"
    record["deadlines_satisfied"] = bool(record.get("deadlines_satisfied") and supervision.get("hard_deadline_met") is True)
    record["success_within_budget"] = bool(record.get("success_within_budget") and outer_normal and
                                          owner_error is None and record["cleanup_verified"] and record["deadlines_satisfied"])
    record["eligible_for_primary_comparison"] = bool(record.get("eligible_for_primary_comparison") and outer_normal
                                                    and owner_error is None and record["cleanup_verified"])
    _append(journal, {"event": "guard_terminal", "monotonic": clock(), "execution_status": record["execution_status"],
                      "cleanup_verified": record["cleanup_verified"], "outer_supervision": supervision,
                      "qualification_scope": "provisional until final guard journal durability and deadline check"})
    finished = clock()
    record["online_finished_monotonic"] = finished
    record["total_wall_seconds"] = finished - started
    record["deadlines_satisfied"] &= finished <= window.hard_deadline
    record["success_within_budget"] &= record["deadlines_satisfied"]
    record["eligible_for_primary_comparison"] &= record["deadlines_satisfied"]
    record["total_cost_scope"] = "guard trial entry through final necessary durable guard journal; includes owner imports, public preparation, all worker costs, validation, online output, and both cleanup layers; posthoc report excluded"
    record["budget_enforcement_scope"] = "outer session supervises complete public pipeline owner and inherited-session workers; small guard metadata IO is cooperative, measured and overrun-disqualifying"
    _write_new(directory / "trial.final.json", record)
    return record


def run_guarded_experiment(plans, output_dir, *, total_budget_seconds, experiment_started_at=None,
                           guard_cleanup_reserve_seconds=.2, clock=time.monotonic):
    started = clock() if experiment_started_at is None else experiment_started_at
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    records, stop_all = [], False
    for plan in plans:
        if set(plan) != {"public_path", "config", "method", "trial_id"}:
            raise ValueError("unknown guarded trial plan fields")
        if stop_all:
            record = {"trial_id": plan["trial_id"], "method": plan["method"], "instance_id": None,
                      "started": False, "cost_mode": "standalone_end_to_end", "attempts": [],
                      "stop_reason": "not_started_after_cleanup_failure", "total_wall_seconds": 0.0}
        else:
            record = run_guarded_trial(plan["public_path"], plan["config"], plan["method"], plan["trial_id"],
                                       directory / plan["trial_id"], experiment_started_at=started,
                                       total_budget_seconds=total_budget_seconds,
                                       guard_cleanup_reserve_seconds=guard_cleanup_reserve_seconds, clock=clock)
            stop_all = record.get("cleanup_verified") is not True
        records.append(record)
    finished = clock()
    result = {"schema": "ntru-guarded-experiment-v1", "cost_mode": "standalone_end_to_end",
              "requested_budget_seconds": total_budget_seconds, "total_online_wall_seconds": finished - started,
              "experiment_hard_deadline_met": finished <= started + total_budget_seconds,
              "trials": records, "summary": summarize_trials(records),
              "global_overhead_seconds": max(0.0, finished - started - sum(r["total_wall_seconds"] for r in records)),
              "report_generation_scope": "posthoc summaries, hashes, formatting and offline diagnostics are excluded"}
    _write_new(directory / "experiment.final.json", result)
    return result


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--owner-request")
    modes.add_argument("--plan")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.owner_request:
        request = json.loads(Path(args.owner_request).read_text())
        if request.pop("schema") != "ntru-guarded-owner-request-v1":
            raise ValueError("unknown owner request")
        request["output_dir"] = request.pop("owner_output_dir")
        run_trial(**request, worker_ownership="process_group")
        return
    plan = json.loads(Path(args.plan).read_text())
    if set(plan) != {"schema", "total_budget_seconds", "trials"} or plan["schema"] != "ntru-standalone-experiment-plan-v1":
        raise ValueError("unknown guarded experiment plan")
    if not args.output:
        parser.error("--plan requires --output")
    result = run_guarded_experiment(plan["trials"], args.output, total_budget_seconds=plan["total_budget_seconds"],
                                    experiment_started_at=started)
    print(json.dumps({"summary": result["summary"], "experiment_hard_deadline_met": result["experiment_hard_deadline_met"]}, indent=2))


if __name__ == "__main__":
    main()
