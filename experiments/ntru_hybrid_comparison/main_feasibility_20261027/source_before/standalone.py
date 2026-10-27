"""Independent cold-start trials with absolute budgets and durable accounting.

One owner charges public input preparation, every fresh pattern worker, online
validation, necessary journals and cleanup. Posthoc formatting is separate.
Only public constraints decide recovery; worker success flags are untrusted.
"""

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import random
import time

from .budgets import cap_attempt, create_trial_window
from .candidates import context_from_public
from .inputs import canonical_bytes, content_sha256
from .reduction import protocol_from_manifest
from .summarize import summarize_trials
from .supervisor import supervise_command
from .validation import validate_public_candidate


SCHEMA = "ntru-standalone-trial-config-v1"
CONFIG_FIELDS = {"schema", "trial_budget_seconds", "cleanup_reserve_seconds", "pattern_limit_seconds",
                 "max_pattern_attempts", "k", "kappa", "pattern_seed", "tail_rule", "worker",
                 "minimum_attempt_work_seconds"}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _seconds(value, name, nullable=False):
    if value is None and nullable:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(name + " must be finite nonnegative seconds")


def validate_trial_config(config, method):
    required = CONFIG_FIELDS - {"minimum_attempt_work_seconds", "pattern_limit_seconds"}
    if (not isinstance(config, dict) or set(config) - CONFIG_FIELDS or required - set(config)
            or config["schema"] != SCHEMA or method not in ("B", "D", "E")):
        raise ValueError("unknown or missing public standalone trial configuration")
    for key in ("trial_budget_seconds", "cleanup_reserve_seconds"):
        _seconds(config[key], key)
    _seconds(config.get("pattern_limit_seconds"), "pattern_limit_seconds", nullable=True)
    _seconds(config.get("minimum_attempt_work_seconds", .02), "minimum_attempt_work_seconds")
    for key in ("max_pattern_attempts", "k", "kappa", "pattern_seed"):
        if not _integer(config[key]) or config[key] < 0:
            raise ValueError(key + " must be a nonnegative integer")
    if config["max_pattern_attempts"] < 1 or config["tail_rule"] != "largest_remaining":
        raise ValueError("positive attempts and explicit largest_remaining tail rule required")
    if (method == "B" and config["kappa"] != 0) or (method != "B" and config["kappa"] == 0):
        raise ValueError("B requires kappa0; D/E require a nonzero tail")
    if not isinstance(config["worker"], dict):
        raise ValueError("explicit public worker configuration required")
    protocol_from_manifest(config["worker"].get("reduction_protocol"))
    return config


def _write_new(path, record):
    """An online journal/result write is actual charged work, including fsync."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical_bytes(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _append(path, record):
    with Path(path).open("ab") as stream:
        stream.write(canonical_bytes(record) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_events(path):
    events, errors = [], []
    if not path.is_file():
        return events, ["missing_worker_events"]
    lines = path.read_bytes().splitlines(keepends=True)
    previous = -math.inf
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
            value = event.get("monotonic")
            if (not isinstance(event, dict) or event.get("sequence") != index
                    or isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value < previous):
                raise ValueError("event sequence or monotonic timestamp mismatch")
            previous = value
            events.append(event)
            if not line.endswith(b"\n"):
                errors.append("unterminated_last_worker_event")
        except (ValueError, TypeError, AttributeError) as error:
            errors.append(f"worker_event_{index}: {error}")
            break
    return events, errors


def _progress_from_events(events, worker_result):
    """Keep durable pending states even when a backend is killed mid-call."""
    baseline = deepcopy(worker_result.get("progress")) if isinstance(worker_result, dict) else None
    for event in events:
        if event.get("event") == "terminal" and isinstance(event.get("progress"), dict):
            baseline = deepcopy(event["progress"])
    states = {row["external_id"]: row["state"] for row in (baseline or {}).get("targets", [])}
    for event in events:
        kind, identifier = event.get("event"), event.get("external_id")
        if kind == "target_submitted":
            states.setdefault(identifier, "pending")
            if states[identifier] == "unsubmitted":
                states[identifier] = "pending"
        elif kind in ("target_completed", "target_errored", "target_cancelled"):
            states[identifier] = kind.removeprefix("target_")
    if baseline is None:
        baseline = {}
    for name in ("completed", "errored", "cancelled", "pending", "unsubmitted"):
        baseline[name + "_count"] = sum(state == name for state in states.values())
    baseline["submitted_count"] = sum(state != "unsubmitted" for state in states.values())
    baseline["target_count"] = max(baseline.get("target_count", 0), len(states))
    baseline["targets"] = [{"external_id": identifier, "state": state} for identifier, state in states.items()]
    baseline["conservation_holds"] = baseline["submitted_count"] == sum(baseline[name + "_count"] for name in
                                                                       ("completed", "errored", "cancelled", "pending"))
    baseline["supervisor_termination_infers_no_target_completion"] = True
    return baseline


def _memory_across_attempts(attempts):
    """Sequential attempts do not overlap: their common-scope peak is max."""
    samples = [a.get("supervision", {}).get("memory", {}) for a in attempts]
    measured = [m for m in samples if m.get("value") is not None]
    if not measured:
        return {"metric_name": "sampled_simultaneous_process_tree_rss_bytes", "value": None,
                "unit": "bytes", "scope": "sequential pattern worker trees; excludes owner/supervisor",
                "missing_reason": "no measured process-tree RSS sample", "sampling_estimate": True,
                "strict_memory_limit": False, "enforcement_backend": None}
    scopes = {(m.get("metric_name"), m.get("unit"), m.get("scope")) for m in measured}
    if len(scopes) != 1:
        return {"value": None, "missing_reason": "incompatible memory scopes across attempts"}
    peak = deepcopy(max(measured, key=lambda m: m["value"]))
    peak["trial_aggregation"] = "maximum of same-scope simultaneous-RSS samples across nonoverlapping attempts, never sum of process peaks"
    return peak


def run_trial(public_path, config, method, trial_id, output_dir, *, experiment_started_at,
              total_budget_seconds, supervisor=supervise_command, clock=time.monotonic,
              trial_started_at=None, worker_ownership="session"):
    """Cold-start one method/instance trial; each pattern owns a fresh process."""
    started = clock() if trial_started_at is None else trial_started_at
    if worker_ownership not in ("session", "process_group"):
        raise ValueError("unknown worker ownership boundary")
    validate_trial_config(config, method)
    if not isinstance(trial_id, str) or not trial_id:
        raise ValueError("nonempty trial_id required")
    window = create_trial_window(experiment_started_at=experiment_started_at,
                                 total_budget_seconds=total_budget_seconds, trial_started_at=started,
                                 trial_budget_seconds=config["trial_budget_seconds"],
                                 cleanup_reserve_seconds=config["cleanup_reserve_seconds"])
    record = {"schema": "ntru-independent-trial-v1", "method": method, "trial_id": trial_id,
              "instance_id": None, "cost_mode": "standalone_end_to_end", "started": False,
              "config_sha256": content_sha256(config), "config": deepcopy(config),
              "budget": window.to_record(), "started_monotonic": started,
              "attempts": [], "public_valid_observed": False, "success_within_budget": False,
              "execution_status": "not_started", "cleanup_verified": True,
              "eligible_for_primary_comparison": False, "supervisor_public_validation": None,
              "deadlines_satisfied": True, "stop_reason": "insufficient_budget_before_trial",
              "total_wall_seconds": 0.0, "cleanup_seconds": 0.0,
              "first_public_valid": None, "public_candidates": []}
    minimum = config.get("minimum_attempt_work_seconds", .02)
    if not window.can_start(now=clock(), minimum_work_seconds=minimum):
        return record
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    record["started"] = True
    journal = directory / "owner_online.jsonl"
    _append(journal, {"event": "trial_started", "monotonic": clock(), "trial_id": trial_id,
                      "method": method, "budget": record["budget"], "config_sha256": record["config_sha256"]})
    rng = random.Random(config["pattern_seed"])
    seen_patterns = set()
    try:
        window.require_work("public_input_load", now=clock())
        public = json.loads(Path(public_path).read_bytes())
        metadata = context_from_public(public, [0])
        record["instance_id"] = public["instance_id"]
        record["public_content_sha256"] = metadata.public_sha256
        if config["k"] + config["kappa"] > public["n"]:
            raise ValueError("pattern and tail exceed public dimension")
        runtime_path = Path(__file__).parents[1] / "ntru_e2e/.deps/runtime.json"
        runtime = json.loads(runtime_path.read_text())
        env = dict(os.environ)
        env.update(runtime["environment"])
        for index in range(config["max_pattern_attempts"]):
            now = clock()
            attempt_window = cap_attempt(window, attempt_started_at=now,
                                         pattern_limit_seconds=config.get("pattern_limit_seconds"))
            if not attempt_window.can_start(now=now, minimum_work_seconds=minimum):
                record["stop_reason"] = "insufficient_budget_before_next_pattern"
                if not record["attempts"] or record["execution_status"] == "completed":
                    record["execution_status"] = "budget_exhausted"
                break
            P = sorted(rng.sample(range(public["n"]), config["k"]))
            remaining = [i for i in range(public["n"]) if i not in P]
            split = len(remaining) - config["kappa"]
            coordinates = {"P": P, "C": remaining[:split], "T": remaining[split:]}
            protocol = protocol_from_manifest(config["worker"]["reduction_protocol"])
            seeds = {"pattern_seed": config["pattern_seed"], "tail_seed": 0, "candidate_seed": 0,
                     "reduction_seed": protocol.seed,
                     "decoder_seed": config["worker"].get("backend_config", {}).get("seed", protocol.seed)}
            attempt_dir = directory / f"attempt_{index:03d}"
            attempt_dir.mkdir()
            request = {"schema": "ntru-standalone-attempt-request-v1", "public_path": str(Path(public_path).resolve()),
                       "config": deepcopy(config["worker"]), "method": method, "trial_id": trial_id,
                       "pattern_attempt_index": index, "coordinates": coordinates, "seeds": seeds,
                       "config_sha256": record["config_sha256"], "budget_origin_monotonic": started,
                       "trial_started_monotonic": started, "work_deadline": attempt_window.work_deadline,
                       "hard_deadline": attempt_window.hard_deadline}
            _write_new(attempt_dir / "request.json", request)
            attempt = {"pattern_attempt_index": index, "trial_id": trial_id, "method": method,
                       "instance_id": record["instance_id"], "coordinates": coordinates, "seeds": seeds,
                       "pattern_repeated": tuple(P) in seen_patterns, "config_sha256": record["config_sha256"],
                       "started_monotonic": now, "budget": attempt_window.to_record()}
            seen_patterns.add(tuple(P))
            record["attempts"].append(attempt)
            _append(journal, {"event": "pattern_prepared", "monotonic": clock(), **attempt})
            argv = [runtime["python"], "-B", "-m", "experiments.ntru_hybrid_comparison.standalone_worker",
                    "--request", str(attempt_dir / "request.json"), "--events", str(attempt_dir / "events.jsonl"),
                    "--result", str(attempt_dir / "result.json")]
            try:
                ownership_option = {} if worker_ownership == "session" else {"ownership": "process_group"}
                supervision = supervisor(argv, work_deadline=attempt_window.work_deadline,
                                         hard_deadline=attempt_window.hard_deadline, cwd=str(Path.cwd()), env=env,
                                         stdout_path=attempt_dir / "stdout.txt", stderr_path=attempt_dir / "stderr.txt",
                                         **ownership_option)
            except BaseException as error:
                # The supervisor normally catches and cleans its own failures.
                # If its contract itself breaks, ownership cannot be verified;
                # preserve that fact and forbid every subsequent trial.
                supervision = {"execution_status": "cleanup_unverified", "cleanup_verified": False,
                               "hard_deadline_met": False, "returncode": None,
                               "cleanup_seconds": 0.0, "cleanup_seconds_observed": False,
                               "errors": [{"type": type(error).__name__, "message": str(error)}],
                               "residual_active_processes": None,
                               "ownership_scope": "supervisor failed before returning ownership evidence"}
            attempt["supervision"] = supervision
            record["cleanup_seconds"] += supervision.get("cleanup_seconds", 0.0)
            events, errors = _read_events(attempt_dir / "events.jsonl")
            worker = None
            try:
                worker = json.loads((attempt_dir / "result.json").read_text())
            except (OSError, ValueError) as error:
                errors.append("missing_or_partial_worker_result: " + str(error))
            if isinstance(worker, dict):
                for key, expected in (("method", method), ("trial_id", trial_id), ("pattern_attempt_index", index),
                                      ("coordinates", coordinates), ("seeds", seeds), ("config_sha256", record["config_sha256"])):
                    if worker.get(key) != expected:
                        errors.append("worker_binding_mismatch:" + key)
                attempt["worker_status"] = worker.get("status")
                attempt["stage_timings"] = worker.get("timings", {})
                attempt["input_digests"] = worker.get("input_digests", {})
                attempt["current_stage"] = worker.get("current_stage")
                if worker.get("protocol_errors"):
                    errors.append("worker_reported_protocol_errors:" + repr(worker["protocol_errors"]))
            attempt["events"] = events
            attempt["protocol_errors"] = errors
            attempt["progress"] = _progress_from_events(events, worker)
            candidate_events = [e for e in events if e.get("event") == "candidate"]
            validations = []
            eligible_candidate = False
            for event in candidate_events:
                checked = validate_public_candidate(public, event.get("f"), event.get("g"))
                received = clock()
                row = {"pattern_attempt_index": index, "f": event.get("f"), "g": event.get("g"),
                       "source": event.get("source", "unknown"), "worker_observed_monotonic": event.get("observed_monotonic", event["monotonic"]),
                       "owner_validation_finished_monotonic": received, "public_validation": checked,
                       "received_within_attempt_work_budget": received <= attempt_window.work_deadline}
                validations.append(row)
                if checked["public_valid"]:
                    record["public_valid_observed"] = True
                    record["public_candidates"].append(row)
                    if record["first_public_valid"] is None:
                        record["first_public_valid"] = row
                        record["supervisor_public_validation"] = checked
                    observed = row["worker_observed_monotonic"]
                    if isinstance(observed, (int, float)) and not isinstance(observed, bool):
                        eligible_candidate |= started <= observed <= attempt_window.work_deadline and received <= attempt_window.work_deadline
            attempt["owner_candidate_validations"] = validations
            attempt["public_valid_observed"] = any(v["public_validation"]["public_valid"] for v in validations)
            status = supervision.get("execution_status", "supervisor_exception")
            if status == "completed":
                if errors or not isinstance(worker, dict) or worker.get("status") not in ("completed", "work_deadline"):
                    status = "protocol_error"
                elif worker["status"] == "work_deadline":
                    status = "timeout"
            attempt["execution_status"] = status
            attempt["cleanup_verified"] = supervision.get("cleanup_verified") is True
            record["cleanup_verified"] &= attempt["cleanup_verified"]
            attempt["finished_monotonic"] = clock()
            attempt["total_wall_seconds"] = attempt["finished_monotonic"] - now
            attempt["success_within_budget"] = bool(eligible_candidate and status == "completed"
                                                     and supervision.get("returncode") == 0
                                                     and attempt["cleanup_verified"]
                                                     and supervision.get("hard_deadline_met") is True
                                                     and clock() <= attempt_window.hard_deadline)
            _append(journal, {"event": "pattern_terminal", "monotonic": clock(), **attempt,
                              "qualification_scope": "provisional until this write completes and final trial deadline checks pass"})
            attempt["online_record_durable_monotonic"] = clock()
            attempt["online_terminal_record_seconds"] = attempt["online_record_durable_monotonic"] - attempt["finished_monotonic"]
            attempt["finished_monotonic"] = attempt["online_record_durable_monotonic"]
            attempt["total_wall_seconds"] = attempt["finished_monotonic"] - now
            if attempt["online_record_durable_monotonic"] > attempt_window.hard_deadline:
                attempt["success_within_budget"] = False
            record["execution_status"] = status
            if not attempt["cleanup_verified"]:
                record["execution_status"] = "cleanup_failed"
                record["stop_reason"] = "cleanup_unverified_stop_all_following_work"
                break
            if status not in ("completed", "timeout"):
                record["stop_reason"] = "abnormal_attempt_stop"
                break
            if attempt["success_within_budget"]:
                record["success_within_budget"] = True
                record["stop_reason"] = "first_public_valid_within_budget"
                break
            record["stop_reason"] = "maximum_pattern_attempts_reached"
    except Exception as error:
        record["execution_status"] = "protocol_error"
        record["stop_reason"] = "owner_preparation_or_protocol_exception"
        record["owner_error"] = {"type": type(error).__name__, "message": str(error)}
    record["memory"] = _memory_across_attempts(record["attempts"])
    record["eligible_for_primary_comparison"] = bool(record["cleanup_verified"] and record["execution_status"] in
                                                     ("completed", "timeout", "budget_exhausted"))
    _append(journal, {"event": "trial_terminal", "monotonic": clock(), "execution_status": record["execution_status"],
                      "public_valid_observed": record["public_valid_observed"], "success_within_budget": record["success_within_budget"],
                      "cleanup_verified": record["cleanup_verified"], "stop_reason": record["stop_reason"],
                      "qualification_scope": "provisional until write durability and final deadline checks"})
    finished = clock()
    record["online_finished_monotonic"] = finished
    record["total_wall_seconds"] = finished - started
    # Each attempt's relative deadline is relative to its own start, not trial.
    record["deadlines_satisfied"] = finished <= window.hard_deadline and all(
        a.get("supervision", {}).get("hard_deadline_met") is True
        and a.get("online_record_durable_monotonic", math.inf) <= a["started_monotonic"] + a["budget"]["hard_deadline_relative_seconds"]
        for a in record["attempts"])
    record["success_within_budget"] &= record["deadlines_satisfied"] and record["execution_status"] == "completed"
    record["eligible_for_primary_comparison"] &= record["deadlines_satisfied"]
    record["total_cost_scope"] = "owner trial entry through final necessary durable journal, including preparation, every worker, validation and cleanup; final report formatting is posthoc"
    # Facts are durable; this posthoc projection also applies final qualification.
    _write_new(directory / "trial.final.json", record)
    return record


def run_experiment(plans, output_dir, *, total_budget_seconds, experiment_started_at=None, clock=time.monotonic):
    """Sequential cold trials. A failed cleanup forbids every following trial."""
    started = clock() if experiment_started_at is None else experiment_started_at
    _seconds(total_budget_seconds, "total_budget_seconds")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=False)
    records, stop_all = [], False
    for plan in plans:
        if set(plan) != {"public_path", "config", "method", "trial_id"}:
            raise ValueError("trial plan must contain only public_path/config/method/trial_id")
        if stop_all:
            record = {"trial_id": plan["trial_id"], "method": plan["method"], "instance_id": None,
                      "started": False, "cost_mode": "standalone_end_to_end", "attempts": [],
                      "stop_reason": "not_started_after_cleanup_failure", "total_wall_seconds": 0.0}
        else:
            record = run_trial(plan["public_path"], plan["config"], plan["method"], plan["trial_id"],
                               directory / plan["trial_id"], experiment_started_at=started,
                               total_budget_seconds=total_budget_seconds, clock=clock)
            stop_all = record.get("cleanup_verified") is not True
        records.append(record)
    online_finished = clock()
    result = {"schema": "ntru-independent-experiment-v1", "cost_mode": "standalone_end_to_end",
              "requested_budget_seconds": total_budget_seconds, "total_online_wall_seconds": online_finished - started,
              "experiment_hard_deadline_met": online_finished <= started + total_budget_seconds,
              "trials": records, "summary": summarize_trials(records),
              "global_overhead_seconds": max(0.0, online_finished - started - sum(r.get("total_wall_seconds", 0) for r in records)),
              "report_generation_scope": "posthoc; all necessary trial events/results were already durably committed"}
    _write_new(directory / "experiment.final.json", result)
    return result


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    document = json.loads(Path(args.plan).read_text())
    if set(document) != {"schema", "total_budget_seconds", "trials"} or document["schema"] != "ntru-standalone-experiment-plan-v1":
        raise ValueError("unknown experiment plan")
    result = run_experiment(document["trials"], args.output, total_budget_seconds=document["total_budget_seconds"],
                            experiment_started_at=started)
    print(json.dumps({"summary": result["summary"], "experiment_hard_deadline_met": result["experiment_hard_deadline_met"]}, indent=2))


if __name__ == "__main__":
    main()
