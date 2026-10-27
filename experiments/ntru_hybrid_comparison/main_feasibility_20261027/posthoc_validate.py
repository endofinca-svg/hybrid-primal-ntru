"""Read-only posthoc checks of completed B/E/D records and actual public arrays.

This module never builds a lattice, creates a GSO, reduces, decodes, reads a
reference vector, polls, or waits. Run it only after experiment.final.json is
complete. Optional --output writes a new analysis artifact without overwriting.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path

from experiments.ntru_hybrid_comparison.candidates import context_from_public
from experiments.ntru_hybrid_comparison.reduction import (
    protocol_from_manifest, validate_execution_record,
)
from experiments.ntru_hybrid_comparison.summarize import qualify_trial
from experiments.ntru_hybrid_comparison.supervisor import _read_process
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate


HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text())


def event_rows(path):
    """An interrupted final write can be incomplete; retain its valid prefix."""
    events, errors = [], []
    if not path.is_file():
        return [], ["missing_worker_event_file"]
    for index, line in enumerate(path.read_bytes().splitlines()):
        try:
            row = json.loads(line)
            if (not isinstance(row, dict) or row.get("sequence") != index
                    or not isinstance(row.get("monotonic"), (int, float))):
                raise ValueError("bad event sequence/timestamp")
            if events and row["monotonic"] < events[-1]["monotonic"]:
                raise ValueError("nonmonotonic event timestamp")
            events.append(row)
        except (ValueError, TypeError) as error:
            errors.append({"line": index, "error": str(error)})
            break
    return events, errors


def stage_intervals(events, *, termination_upper_monotonic):
    """Wall intervals are diagnostics, not additive backend/CPU measurements.

    No stage_finished event means no precise phase duration. Its last durable
    in-stage observation and verified worker-tree-empty timestamp delimit a
    censored observed wall interval; they cannot prove when backend CPU ended.
    Entry journal/checkpoint work may be inside these intervals.
    """
    opened, intervals = {}, []
    for row in events:
        name, kind = row.get("stage"), row.get("event")
        if kind == "stage_started":
            if name in opened:
                raise ValueError("duplicate stage start without a finish: " + str(name))
            opened[name] = {"start": row["monotonic"], "last_observation": row["monotonic"]}
        if name in opened:
            opened[name]["last_observation"] = row["monotonic"]
        if kind == "stage_finished":
            if name not in opened:
                raise ValueError("stage finish without observed start: " + str(name))
            pending = opened.pop(name)
            intervals.append({"stage": name, "started_monotonic": pending["start"],
                              "finished_event_monotonic": row["monotonic"], "censored": False,
                              "observed_stage_wall_interval_seconds": row["monotonic"] - pending["start"]})
    for name, pending in opened.items():
        upper = termination_upper_monotonic
        intervals.append({"stage": name, "started_monotonic": pending["start"], "censored": True,
                          "finished_event_monotonic": None,
                          "last_durable_in_stage_observation_monotonic": pending["last_observation"],
                          "verified_tree_empty_upper_monotonic": upper,
                          "observed_elapsed_lower_seconds": max(0.0, pending["last_observation"] - pending["start"]),
                          "observed_elapsed_upper_seconds": max(0.0, upper - pending["start"]) if upper is not None else None,
                          "exact_backend_or_cpu_seconds": None,
                          "checkpoint_zero_phase_time_is_not_zero_actual_cost": True})
    return intervals


def progress_check(progress):
    counts = {name: progress.get(name + "_count", 0) for name in
              ("submitted", "completed", "errored", "cancelled", "pending")}
    assert all(isinstance(value, int) and value >= 0 for value in counts.values()), counts
    assert counts["submitted"] == sum(counts[name] for name in ("completed", "errored", "cancelled", "pending")), counts
    return counts


def telemetry_check(events, requested_stages):
    previous = (0, 0)
    snapshots = []
    for event in events:
        if event.get("event") != "reduction_progress":
            continue
        record = event["reduction_record"]
        actual = record["actual"]
        stages = record["stages"]
        prefix = [{key: row[key] for key in ("stage_id", "kind", "beta", "tour")} for row in stages]
        assert prefix == requested_stages[:len(prefix)]
        assert all(row["status"] == "completed" for row in stages)
        lll = sum(row["kind"] == "lll" for row in stages)
        tours = sum(row["kind"] == "bkz" for row in stages)
        assert (actual["completed_lll_calls"], actual["completed_tours"]) == (lll, tours)
        assert lll >= previous[0] and tours >= previous[1]
        previous = lll, tours
        active = record.get("active_stage")
        if active is not None:
            assert len(prefix) < len(requested_stages)
            assert {key: active[key] for key in ("stage_id", "kind", "beta", "tour")} == requested_stages[len(prefix)]
        assert event["observed_before_work_deadline"] == (event["observed_monotonic"] <= event["inherited_work_deadline"])
        snapshots.append({"event": event["reduction_event"], "observed_monotonic": event["observed_monotonic"],
                          "completed_lll": lll, "completed_tours": tours,
                          "active_stage": active, "executor_elapsed_seconds": event["executor_elapsed_seconds"]})
    return snapshots


def validate(directory):
    directory = Path(directory)
    experiment_path = directory / "run/experiment.final.json"
    if not experiment_path.is_file():
        raise RuntimeError("experiment.final.json is absent; this checker does not monitor or wait")
    experiment, plan = read(experiment_path), read(directory / "plan.json")
    assert [t["method"] for t in experiment["trials"]] == ["B", "E", "D"]
    assert len(experiment["trials"]) == 3
    assert experiment["requested_budget_seconds"] == plan["total_budget_seconds"] == 1920
    preflight = read(directory / "preflight.json")
    sources = read(directory / "sources.json")["source_sha256"]
    private_names = set(preflight["private_metadata_only"])
    source_mismatches, protected_mismatches = [], []
    for scope, mapping, errors in (("sources", sources, source_mismatches),
                                   ("protected", preflight["protected_sha256"], protected_mismatches)):
        for name, expected in mapping.items():
            if name in private_names or "private" in Path(name).parts:
                raise ValueError("refuse content read of private data: " + name)
            if sha256(Path(name).read_bytes()).hexdigest() != expected:
                errors.append({"path": name, "scope": scope})
    assert not source_mismatches and not protected_mismatches
    private_metadata = []
    for name, before in preflight["private_metadata_only"].items():
        stat = Path(name).stat()
        after = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino}
        assert before == after
        private_metadata.append({"path": name, "unchanged": True, "content_read": False})
    public_checks = read(directory / "public_and_protocol_checks.json")
    coordinates, input_digests, all_rows = {}, {}, []
    previous_cleanup_failed = False
    for trial, trial_plan in zip(experiment["trials"], plan["trials"]):
        assert trial["trial_id"] == trial_plan["trial_id"] and trial["method"] == trial_plan["method"]
        if previous_cleanup_failed:
            assert trial["started"] is False, "new trial started after unverified cleanup"
        previous_cleanup_failed |= trial.get("cleanup_verified") is False
        qualification = qualify_trial(trial)
        assert qualification["success_contribution"] in (0, 1)
        if trial.get("success_within_budget"):
            assert qualification["qualified_success"]
        assert len(trial["attempts"]) <= 3
        public_path = Path(trial_plan["public_path"])
        public = read(public_path)
        context = context_from_public(public, [0])
        assert sha256(public_path.read_bytes()).hexdigest() == public_checks["public_file_sha256"]
        assert context.public_sha256 == public_checks["public_content_sha256"]
        assert public["n"] == 128 and public["q"] == 97 and public["ring"] == "x^n+1"
        assert public["h"] != [1] + [0] * 127
        assert (public["distribution"]["w_f"], public["distribution"]["w_g"]) == (12, 24)
        assert math.isfinite(trial["total_wall_seconds"]) and trial["total_wall_seconds"] >= 0
        outer = trial.get("outer_supervision", {})
        if trial.get("cleanup_verified"):
            assert outer.get("residual_active_processes") == []
        trial_rows = []
        for attempt in trial["attempts"]:
            index = attempt["pattern_attempt_index"]
            attempt_path = directory / "run" / trial["trial_id"] / "owner" / f"attempt_{index:03d}"
            events, event_errors = event_rows(attempt_path / "events.jsonl")
            worker = read(attempt_path / "result.json") if (attempt_path / "result.json").is_file() else {}
            if worker:
                assert worker["cached_reduction_used"] is False and worker["cached_database_used"] is False
                if worker.get("public_file_sha256"):
                    assert worker["public_file_sha256"] == public_checks["public_file_sha256"]
                    assert worker["public_content_sha256"] == public_checks["public_content_sha256"]
                if worker.get("dimensions"):
                    assert worker["dimensions"]["d"] == (216 if trial["method"] == "B" else 212)
                if worker.get("candidate_counts"):
                    assert worker["candidate_counts"]["exact_unique_count"] == 80
            requested = [{"stage_id": "lll", "kind": "lll", "beta": None, "tour": None}]
            requested += [{"stage_id": f"bkz_b{s['beta']:03d}_t{tour:02d}", "kind": "bkz", "beta": s["beta"], "tour": tour}
                          for s in trial_plan["config"]["worker"]["reduction_protocol"]["schedule"]
                          for tour in range(1, s["requested_tours"] + 1)]
            telemetry = telemetry_check(events, requested)
            if worker.get("reduction"):
                validate_execution_record(worker["reduction"], protocol_from_manifest(trial_plan["config"]["worker"]["reduction_protocol"]))
            inner = attempt.get("supervision", {})
            termination_upper = inner.get("finished_monotonic", outer.get("finished_monotonic"))
            intervals = stage_intervals(events, termination_upper_monotonic=termination_upper)
            counts = progress_check(attempt.get("progress", worker.get("progress", {})))
            candidates = []
            for event in events:
                if event.get("event") == "candidate":
                    checked = validate_public_candidate(public, event.get("f"), event.get("g"))
                    if isinstance(event.get("public_validation"), dict):
                        assert checked == event["public_validation"]
                    candidates.append({"source": event.get("source"), "observed_monotonic": event.get("observed_monotonic"),
                                       "posthoc_public_validation": checked,
                                       "budget_success_not_inferred_from_posthoc_validation": True})
            for validation in attempt.get("owner_candidate_validations", []):
                assert validate_public_candidate(public, validation["f"], validation["g"]) == validation["public_validation"]
            p = attempt.get("coordinates", worker.get("coordinates", {}))
            assert p["P"] == public_checks["planned_patterns"][index]["P"]
            if trial["method"] != "B":
                assert p["T"] == public_checks["planned_patterns"][index]["T_for_DE"]
            coordinates[(trial["method"], index)] = p
            input_digests[(trial["method"], index)] = attempt.get("input_digests", worker.get("input_digests", {}))
            for supervision in (outer, inner):
                if supervision.get("cleanup_verified"):
                    assert supervision["residual_active_processes"] == []
                    for identity in supervision.get("observed_processes", []):
                        now = _read_process(identity["pid"])
                        assert now is None or now["starttime_ticks"] != identity["starttime_ticks"] or now["state"] in ("Z", "X")
            trial_rows.append({"pattern_attempt_index": index, "counts": counts, "event_errors": event_errors,
                               "observed_stage_intervals": intervals, "reduction_telemetry": telemetry,
                               "reported_phase_timings": worker.get("timings", {}), "candidate_checks": candidates,
                               "censored_intervals_are_not_exact_backend_times": any(row["censored"] for row in intervals)})
        all_rows.append({"method": trial["method"], "trial_id": trial["trial_id"],
                         "execution_status": trial.get("execution_status"), "cleanup_verified": trial.get("cleanup_verified"),
                         "total_wall_seconds": trial["total_wall_seconds"], "qualification": qualification,
                         "attempt_checks": trial_rows})
    paired_checks = []
    for index in range(3):
        for left, right in (("B", "E"), ("B", "D"), ("E", "D")):
            if (left, index) in coordinates and (right, index) in coordinates:
                assert coordinates[left, index]["P"] == coordinates[right, index]["P"]
        if ("E", index) in input_digests and ("D", index) in input_digests:
            left, right = input_digests["E", index], input_digests["D", index]
            for key in ("coordinates_sha256", "candidate_ordered_sha256", "ordered_targets_sha256", "id_mapping_sha256", "original_basis_sha256"):
                if key in left and key in right:
                    assert left[key] == right[key], key
            paired_checks.append({"pattern_attempt_index": index, "public_inputs_equal_where_observed": True,
                                  "reduced_basis_digest_equal_if_both_observed":
                                  left.get("reduced_basis_sha256") == right.get("reduced_basis_sha256")
                                  if left.get("reduced_basis_sha256") and right.get("reduced_basis_sha256") else None})
    return {"schema": "ntru-main-posthoc-public-validation-v1", "checked_utc": datetime.now(timezone.utc).isoformat(),
            "all_checks_passed": True, "posthoc_only": True, "main_lattice_backend_calls": 0,
            "reference_contents_read": False, "protected_file_count": len(preflight["protected_sha256"]),
            "source_file_count": len(sources), "private_metadata_only": private_metadata,
            "pair_checks": paired_checks, "trial_checks": all_rows,
            "phase_interval_scope": "wall intervals include dispatch/journal time and are nonadditive diagnostics; open intervals are censored, not exact backend/CPU measurements"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=HERE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.directory)
    if args.output:
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
