"""Execute the frozen phase-two schedule, preserving every attempted trial.

All public configs and the paired pattern schedule are saved before generation.
An interrupted launch is charged its full reserved budget on --resume and is
never rerun.  Private truth is passed only to the final, separate evaluator.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import time

from .attack import atomic_json, file_sha
from .record_command import capture

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# This is inside each frozen trial budget, after the two-second process-group
# cleanup allowance; it covers launch, wait bookkeeping and measurement.
MEASUREMENT_ALLOWANCE_SECONDS = 0.1


def command(module, *args):
    return [sys.executable, str(HERE / "run_in_env.py"), "-m",
            "experiments.ntru_e2e." + module, *map(str, args)]


def paired_patterns(n, seed, count, k):
    rng = random.Random(seed)
    return [{"pattern_index": i, "P": sorted(rng.sample(range(n), k)),
             "backend_seed": rng.randrange(1, 2**31)} for i in range(count)]


def validate_plan(plan, config_path):
    frozen = json.loads((HERE / "provenance/phase2/plan_frozen.json").read_text())
    if file_sha(config_path) != frozen["config_sha256"] or plan != frozen["plan"]:
        raise ValueError("The phase-two parameters must match the previously frozen plan")
    if plan["threads"] != 1 or plan["xi"] != 1:
        raise ValueError("Only the frozen single-threaded unscaled comparison is supported")


def prepare(output_dir, config_path):
    plan = json.loads(config_path.read_text())
    validate_plan(plan, config_path)
    output_dir.mkdir(parents=True, exist_ok=False)
    frozen_config = output_dir / "config_phase2.json"
    frozen_config.write_bytes(config_path.read_bytes())
    config_sha = file_sha(frozen_config)
    source_files = ["run_phase2.py", "solve_phase2.py", "phase2_adapter.py",
                    "slicer_adapter.py", "algebra.py", "generate.py", "run_in_env.py"]
    summary = {"schema": "ntru-e2e-phase2-run-v1", "status": "prepared",
               "started_utc": datetime.now(timezone.utc).isoformat(),
               "config": str(frozen_config), "config_sha256": config_sha,
               "source_sha256": {name: file_sha(HERE / name) for name in source_files},
               "budget_scope": "sum of online subprocess wall times including imports, all arms, evidence writes and supervisor cleanup",
               "total_online_budget_seconds": plan["total_online_budget_seconds"],
               "online_wall_seconds": 0.0, "instances": [], "generation": {}}
    schedules = []
    for case in plan["cases"]:
        case_dir = output_dir / case["name"]
        case_dir.mkdir()
        online_config = {key: plan[key] for key in
                         ("schema", "q", "xi", "threads", "k", "kappa", "patterns_per_instance",
                          "bkz_max_loops", "slicer_randomizations", "slicer_max_iterations", "choices")}
        online_config.update({key: case[key] for key in ("n", "w_f", "w_g")})
        online_config.update(schema="ntru-e2e-phase2-online-v1", plan_sha256=config_sha)
        online_path = case_dir / "online_config.json"
        atomic_json(online_path, online_config)
        for index, (keygen_seed, solver_seed) in enumerate(zip(case["keygen_seeds"], case["solver_seeds"]), 1):
            instance_id = f"instance_{index:02d}"
            trial_dir = case_dir / instance_id
            trial_dir.mkdir()
            record = {"case_id": case["name"], "instance_id": instance_id,
                      "keygen_seed": keygen_seed, "solver_seed": solver_seed,
                      "public": str(case_dir / "public" / f"{instance_id}.json"),
                      "truth": str(case_dir / "private" / f"{instance_id}.truth.json"),
                      "instance_budget_seconds": case["instance_budget_seconds"],
                      "online_wall_seconds": 0.0, "trials": []}
            patterns = paired_patterns(case["n"], solver_seed, plan["patterns_per_instance"], plan["k"])
            schedules.append({"case_id": case["name"], "instance_id": instance_id,
                              "solver_seed": solver_seed, "patterns": patterns})
            for choice_index, choice in enumerate(plan["choices"]):
                for pattern in patterns:
                    trial_id = f"{choice['name']}_p{pattern['pattern_index']}"
                    record["trials"].append({
                        "trial_id": trial_id, "choice_index": choice_index,
                        "pattern_index": pattern["pattern_index"], "expected_P": pattern["P"],
                        "expected_backend_seed": pattern["backend_seed"],
                        "config": str(online_path), "config_sha256": file_sha(online_path),
                        "result": str(trial_dir / f"{trial_id}.result.json"),
                        "command_prefix": str(trial_dir / f"{trial_id}_online"),
                        "trial_budget_seconds": case["trial_budget_seconds"], "status": "pending"})
            summary["instances"].append(record)
    schedule_path = output_dir / "schedule_frozen.json"
    atomic_json(schedule_path, {"plan_sha256": config_sha, "schedules": schedules,
                "frozen_before_key_generation": True, "source_sha256": summary["source_sha256"]})
    summary.update(schedule=str(schedule_path), schedule_sha256=file_sha(schedule_path))
    atomic_json(output_dir / "summary.json", summary)
    return plan, summary


def preserve_online_inventory(output_dir, summary):
    """Keep the evaluator's source stable while summary lifecycle fields change."""
    inventory_path = output_dir / "online_inventory.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text())
        immutable_keys = ("schema", "config", "config_sha256", "source_sha256", "budget_scope",
                          "total_online_budget_seconds", "online_wall_seconds", "instances",
                          "generation", "within_frozen_budgets", "schedule", "schedule_sha256")
        if any(inventory.get(key) != summary.get(key) for key in immutable_keys):
            raise ValueError("Preserving existing online inventory: it differs from the resumed run")
    else:
        atomic_json(inventory_path, summary)


def placeholder(record, trial, status, reason):
    path = Path(trial["result"])
    if path.exists():
        return
    from .algebra import partition, enumerate_tails, multiplication_matrix, make_target
    public = json.loads(Path(record["public"]).read_text())
    config = json.loads(Path(trial["config"]).read_text())
    coordinates = partition(public["n"], trial["expected_P"], config["kappa"])
    guesses = enumerate_tails(config["kappa"], config["w_f"])["guesses"]
    A = multiplication_matrix(public["h"], public["q"])
    targets = [make_target(A, coordinates["T"], u, len(coordinates["C"]), q=public["q"]) for u in guesses]
    atomic_json(path, {"schema": "ntru-e2e-phase2-trial-v1", "status": status, "error": reason,
                      "runner_placeholder": True, "public_sha256": file_sha(record["public"]),
                      "config_sha256": trial["config_sha256"], "coordinates": coordinates,
                      "guesses": guesses, "targets": targets,
                      "bounds_sq": [config["w_f"] + config["w_g"] - sum(x != 0 for x in u) for u in guesses],
                      "arms": {}, "events": [], "bases": {}})


def run(output_dir, config_path, resume=False):
    output_dir = output_dir.resolve()
    summary_path = output_dir / "summary.json"
    if resume:
        summary = json.loads(summary_path.read_text())
        plan = json.loads(Path(summary["config"]).read_text())
        validate_plan(plan, Path(summary["config"]))
        for name, digest in summary["source_sha256"].items():
            if file_sha(HERE / name) != digest:
                raise ValueError(f"Frozen online source changed since launch: {name}")
        if "schedule" in summary and file_sha(summary["schedule"]) != summary.get("schedule_sha256"):
            raise ValueError("Frozen paired schedule changed since launch")
    else:
        plan, summary = prepare(output_dir, config_path)
    def persist():
        summary["updated_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(summary_path, summary)
    if summary["status"] == "finished":
        preserve_online_inventory(output_dir, summary)
        return summary
    # Generate each frozen case once.  All online configs already exist.
    for case in plan["cases"]:
        case_id = case["name"]
        if case_id not in summary["generation"]:
            case_dir = output_dir / case_id
            prefix = case_dir / "generate"
            if prefix.with_suffix(".command.json").exists():
                metadata = json.loads(prefix.with_suffix(".command.json").read_text())
                if metadata.get("exit_code") != 0:
                    raise ValueError(f"Interrupted generation cannot be silently regenerated: {case_id}")
            else:
                metadata = capture(command("generate", "--output-dir", case_dir, "--n", case["n"],
                                    "--q", plan["q"], "--wf", case["w_f"], "--wg", case["w_g"],
                                    "--seeds", *case["keygen_seeds"]), prefix, cwd=ROOT, timeout=60)
            summary["generation"][case_id] = metadata
            persist()
            if metadata.get("exit_code") != 0:
                raise RuntimeError(f"Generation failed: {case_id}")
    summary["status"] = "running"
    persist()
    for record in summary["instances"]:
        for trial in record["trials"]:
            if trial["status"] != "pending":
                continue
            prefix = Path(trial["command_prefix"])
            metadata_path = prefix.with_suffix(".command.json")
            # Recover an attempted command after a parent interruption; never repeat it.
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text())
                overhead = plan["supervisor_allowance_seconds"] + MEASUREMENT_ALLOWANCE_SECONDS
                reservation = trial.get("reserved_online_seconds",
                                        metadata.get("timeout_seconds", trial["trial_budget_seconds"] - overhead)
                                        + overhead)
                charged = metadata.get("wall_seconds", reservation)
                recovered_status = ("interrupted" if metadata.get("status") == "running" else
                                    "timeout" if metadata.get("status") == "timeout" else
                                    "finished" if metadata.get("exit_code") == 0 else "error")
                trial.update(status=recovered_status,
                             online_command=metadata, charged_online_seconds=charged)
                summary["online_wall_seconds"] += charged
                record["online_wall_seconds"] += charged
                placeholder(record, trial, recovered_status, "Prior launch preserved without rerun; full reservation charged if measurement missing")
                result = json.loads(Path(trial["result"]).read_text())
                trial["result_status"] = result.get("status")
                trial["pairing_verified"] = (result.get("coordinates", {}).get("P") == trial["expected_P"] and
                                               result.get("backend_seed") == trial["expected_backend_seed"])
                persist()
                continue
            remaining = min(plan["total_online_budget_seconds"] - summary["online_wall_seconds"],
                            record["instance_budget_seconds"] - record["online_wall_seconds"],
                            trial["trial_budget_seconds"])
            allowance = plan["supervisor_allowance_seconds"]
            if remaining <= allowance + MEASUREMENT_ALLOWANCE_SECONDS:
                trial.update(status="not_run_budget")
                placeholder(record, trial, "not_run_budget", "Frozen budget exhausted")
                persist()
                continue
            print(f"{record['case_id']}/{record['instance_id']}/{trial['trial_id']}: {remaining:.3f}s total budget", flush=True)
            trial["reserved_online_seconds"] = remaining
            persist()
            metadata = capture(command("solve_phase2", "--public", record["public"],
                                      "--config", trial["config"], "--seed", record["solver_seed"],
                                      "--pattern-index", trial["pattern_index"], "--choice-index", trial["choice_index"],
                                      "--output", trial["result"]), prefix, cwd=ROOT,
                               timeout=remaining - allowance - MEASUREMENT_ALLOWANCE_SECONDS)
            charged = metadata["wall_seconds"]
            summary["online_wall_seconds"] += charged
            record["online_wall_seconds"] += charged
            trial.update(online_command=metadata, charged_online_seconds=charged,
                         status="timeout" if metadata["status"] == "timeout" else
                         ("finished" if metadata["exit_code"] == 0 else "error"))
            placeholder(record, trial, trial["status"], "Worker exited before its first checkpoint")
            result = json.loads(Path(trial["result"]).read_text())
            trial["result_status"] = result.get("status")
            trial["pairing_verified"] = (result.get("coordinates", {}).get("P") == trial["expected_P"] and
                                           result.get("backend_seed") == trial["expected_backend_seed"])
            # Do not rewrite worker evidence on external timeout.
            print(f"  {trial['status']}, wall={charged:.3f}s, arms=" +
                  str({key: value.get('status') for key, value in result.get('arms', {}).items()}), flush=True)
            persist()
    summary["status"] = "online_finished"
    summary["online_finished_utc"] = datetime.now(timezone.utc).isoformat()
    summary["within_frozen_budgets"] = (summary["online_wall_seconds"] <= plan["total_online_budget_seconds"] and
                all(r["online_wall_seconds"] <= r["instance_budget_seconds"] for r in summary["instances"]) and
                all(t.get("charged_online_seconds", 0) <= t["trial_budget_seconds"]
                    for r in summary["instances"] for t in r["trials"]))
    persist()
    # The evaluator hashes this stable online inventory.  Lifecycle updates to
    # summary.json after evaluation must not invalidate that evidence hash.
    preserve_online_inventory(output_dir, summary)
    evaluation_prefix = output_dir / "evaluate_phase2"
    if not evaluation_prefix.with_suffix(".command.json").exists():
        summary["evaluation_command"] = capture(command("evaluate_phase2", "--run-dir", output_dir),
                                                 evaluation_prefix, cwd=ROOT, timeout=180)
    else:
        summary["evaluation_command"] = json.loads(evaluation_prefix.with_suffix(".command.json").read_text())
    summary["status"] = "finished" if summary["evaluation_command"].get("exit_code") == 0 else "evaluation_failed"
    persist()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config_phase2.json")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    summary = run(args.output_dir, args.config, args.resume)
    print(json.dumps({key: summary[key] for key in ("status", "online_wall_seconds", "within_frozen_budgets")}))
    return 0 if summary["status"] == "finished" and summary["within_frozen_budgets"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
