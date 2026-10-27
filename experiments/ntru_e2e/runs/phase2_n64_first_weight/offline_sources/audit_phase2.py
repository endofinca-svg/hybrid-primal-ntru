"""Read-only consistency checks for phase-two evidence and phase-one preservation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve(run_dir, name):
    path = Path(name)
    return path.resolve() if path.is_absolute() else (run_dir / path).resolve()


def _seconds(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def audit(run_dir, *, case_ids=None, evaluation_path=None):
    """Check selected frozen evidence without opening any private truth file.

    Filtering happens before resolving per-instance artifact paths.  A report
    for a subset must contain exactly that subset, even when the full original
    inventory also contains completed or pending cases outside the scope.
    """
    run_dir = Path(run_dir).resolve()
    before = json.loads((HERE / "provenance/phase2/phase1_preservation_before.json").read_text())
    preservation = {"checked_file_count": len(before["files_sha256"]), "changed": [], "missing": []}
    for name, digest in before["files_sha256"].items():
        path = HERE / name
        if not path.exists():
            preservation["missing"].append(name)
        elif sha(path) != digest:
            preservation["changed"].append(name)
    preservation["passed"] = not preservation["changed"] and not preservation["missing"]
    inventory_path = run_dir / "online_inventory.json"
    if not inventory_path.exists():
        inventory_path = run_dir / "summary.json"
    summary = json.loads(inventory_path.read_text())
    evaluation_path = (run_dir / "evaluation_phase2.json" if evaluation_path is None
                       else Path(evaluation_path).resolve())
    evaluation = json.loads(evaluation_path.read_text())
    plan = json.loads((run_dir / "config_phase2.json").read_text())
    known_cases = {case["name"] for case in plan["cases"]}
    selected = known_cases if case_ids is None else set(case_ids)
    if not selected or selected - known_cases:
        raise ValueError(f"unknown or empty case selection: {sorted(selected - known_cases)}")
    cases = {case["name"]: case for case in plan["cases"] if case["name"] in selected}
    instances = [instance for instance in summary["instances"] if instance["case_id"] in selected]
    expected_count = sum(len(c["keygen_seeds"]) for c in cases.values()) * len(plan["choices"]) * plan["patterns_per_instance"]
    issues, statuses, versions, ordered_trials, paired_instances, candidate_counts = [], {}, {}, 0, 0, 0
    files, expected_evaluated, selected_configs = {}, {}, {}
    plan_path = _resolve(run_dir, summary.get("config", "config_phase2.json"))
    if sha(plan_path) != summary["config_sha256"] or sha(run_dir / "config_phase2.json") != summary["config_sha256"]:
        issues.append("frozen plan hash mismatch")
    schedule_path = _resolve(run_dir, summary.get("schedule", "schedule_frozen.json"))
    schedule = json.loads(schedule_path.read_text())
    if sha(schedule_path) != summary["schedule_sha256"]:
        issues.append("frozen schedule hash mismatch")
    if schedule.get("plan_sha256") != summary["config_sha256"] or schedule.get("source_sha256") != summary["source_sha256"]:
        issues.append("frozen schedule provenance mismatch")
    schedules = {(entry["case_id"], entry["instance_id"]): entry for entry in schedule["schedules"]
                 if entry["case_id"] in selected}
    if len({(i["case_id"], i["instance_id"]) for i in instances}) != len(instances):
        issues.append("duplicate selected inventory instance")
    for case_id, case in cases.items():
        if sum(i["case_id"] == case_id for i in instances) != len(case["keygen_seeds"]):
            issues.append(f"selected instance count mismatch: {case_id}")
    for instance in instances:
        key = (instance["case_id"], instance["instance_id"])
        expected_evaluated[key] = {"public_sha256": sha(_resolve(run_dir, instance["public"])), "trials": {}}
        frozen = schedules.get(key)
        if frozen is None or frozen["solver_seed"] != instance["solver_seed"]:
            issues.append(f"missing or mismatched frozen instance schedule: {key}")
        trial_pairs = [(trial["choice_index"], trial["pattern_index"]) for trial in instance["trials"]]
        required_pairs = [(choice, pattern) for choice in range(len(plan["choices"]))
                          for pattern in range(plan["patterns_per_instance"])]
        if Counter(trial_pairs) != Counter(required_pairs):
            issues.append(f"selected trial schedule mismatch: {key}")
        charged = sum(trial.get("charged_online_seconds", 0) for trial in instance["trials"])
        if (not _seconds(instance.get("online_wall_seconds")) or
                abs(instance["online_wall_seconds"] - charged) > 1e-6 or
                charged > cases[instance["case_id"]]["instance_budget_seconds"]):
            issues.append(f"selected instance budget mismatch: {key}")
        by_pattern = {}
        for trial in instance["trials"]:
            path = _resolve(run_dir, trial["result"])
            result = json.loads(path.read_text())
            digest = sha(path)
            files[str(path.relative_to(run_dir))] = digest
            config_path = _resolve(run_dir, trial["config"])
            config_sha = sha(config_path)
            selected_configs[str(config_path)] = config_sha
            if config_sha != trial.get("config_sha256"):
                issues.append(f"inventory config hash mismatch: {path}")
            if str(path) in expected_evaluated[key]["trials"]:
                issues.append(f"duplicate selected result path: {path}")
            expected_evaluated[key]["trials"][str(path)] = {"result_sha256": digest, "config_sha256": config_sha}
            charge, wall = trial.get("charged_online_seconds"), trial.get("online_command", {}).get("wall_seconds")
            if (not _seconds(charge) or charge > trial["trial_budget_seconds"] or
                    (_seconds(wall) and wall > charge + 1e-6)):
                issues.append(f"selected trial budget mismatch: {path}")
            if frozen is not None:
                pattern = next((p for p in frozen["patterns"] if p["pattern_index"] == trial["pattern_index"]), None)
                if pattern is None or pattern["P"] != trial["expected_P"] or pattern["backend_seed"] != trial["expected_backend_seed"]:
                    issues.append(f"inventory differs from frozen pattern: {path}")
            status = trial["status"]
            statuses[status] = statuses.get(status, 0) + 1
            version = trial.get("instrumentation_version", "unknown")
            versions[version] = versions.get(version, 0) + 1
            if result.get("runner_placeholder"):
                continue
            if (result.get("choice_index") != trial["choice_index"] or
                    result.get("pattern_index") != trial["pattern_index"]):
                issues.append(f"result trial identity mismatch: {path}")
            expected_source = trial.get("source_sha256", summary["source_sha256"])
            allowed_sources = [summary["source_sha256"], summary.get("active_source_sha256", summary["source_sha256"])]
            if expected_source not in allowed_sources:
                issues.append(f"unregistered trial source revision: {path}")
            observed_source = result.get("code_version", {}).get("source_sha256", {})
            worker_sources = set(expected_source) - {"run_phase2.py", "generate.py", "run_in_env.py"}
            if set(observed_source) != worker_sources:
                issues.append(f"trial source hash set mismatch: {path}")
            for name, digest in observed_source.items():
                if expected_source.get(name) != digest:
                    issues.append(f"trial source version mismatch: {path}: {name}")
            if result["coordinates"]["P"] != trial["expected_P"] or result["backend_seed"] != trial["expected_backend_seed"]:
                issues.append(f"pairing mismatch: {path}")
            if result["config_sha256"] != config_sha:
                issues.append(f"config mismatch: {path}")
            if result["public_sha256"] != expected_evaluated[key]["public_sha256"]:
                issues.append(f"public mismatch: {path}")
            if result.get("plan_sha256") != summary["config_sha256"]:
                issues.append(f"result plan hash mismatch: {path}")
            if (len(result["guesses"]) != 80 or len({tuple(g) for g in result["guesses"]}) != 80
                    or any(not any(g) for g in result["guesses"])):
                issues.append(f"not all 80 distinct nonzero tails: {path}")
            identity = {key: result[key] for key in ("coordinates", "backend_seed", "guesses", "targets", "bounds_sq")}
            pattern_index = trial["pattern_index"]
            if pattern_index in by_pattern and by_pattern[pattern_index] != identity:
                issues.append(f"inputs differ across choices: {path}")
            by_pattern[pattern_index] = identity
            names = [event.get("event") for event in result.get("events", [])]
            if "sieve_start" in names:
                start = names.index("sieve_start")
                completed_arms = {e.get("method") for e in result["events"][:start] if e.get("event") == "arm_complete"}
                needed = {"direct_tail", "lll_rows", "lll_babai", "bkz_rows", "bkz_babai"}
                # Worker records the method as either method or arm for readability.
                completed_arms |= {e.get("arm") for e in result["events"][:start] if e.get("event") == "arm_complete"}
                if not needed <= completed_arms:
                    issues.append(f"baselines did not all precede sieve: {path}")
                else:
                    ordered_trials += 1
            if result.get("bases", {}).get("slicer") and result["bases"]["slicer"] != result["bases"].get("bkz"):
                issues.append(f"slicer and BKZ basis differ: {path}")
            candidate_counts += sum(len(arm.get("candidates", [])) for arm in result.get("arms", {}).values())
        if len(by_pattern) == plan["patterns_per_instance"]:
            paired_instances += 1
    total_trials = sum(len(i["trials"]) for i in instances)
    if total_trials != expected_count:
        issues.append(f"expected {expected_count} trials, got {total_trials}")
    if not summary.get("within_frozen_budgets"):
        issues.append("frozen wall budgets exceeded")
    total_charged = sum(i.get("online_wall_seconds", 0) for i in summary["instances"])
    if (abs(total_charged - summary["online_wall_seconds"]) > 1e-6 or
            total_charged > plan["total_online_budget_seconds"]):
        issues.append("total inventory budget mismatch")
    inventory = _resolve(run_dir, evaluation.get("inventory_path", inventory_path))
    if inventory != inventory_path or sha(inventory_path) != evaluation["inventory_sha256"]:
        issues.append("evaluated inventory hash changed")
    if evaluation.get("config_sha256") != summary["config_sha256"]:
        issues.append("evaluated plan hash mismatch")
    reported_scope = evaluation.get("selected_case_ids")
    if reported_scope is not None and set(reported_scope) != selected:
        issues.append("evaluation selected case scope mismatch")
    actual_evaluated = {}
    for instance in evaluation["instances"]:
        key = (instance["case_id"], instance["instance_id"])
        if key in actual_evaluated:
            issues.append(f"duplicate evaluated instance: {key}")
        trials = {}
        for trial in instance["trials"]:
            path = str(_resolve(run_dir, trial["result"]))
            if path in trials:
                issues.append(f"duplicate evaluated trial: {path}")
            trials[path] = {"result_sha256": trial.get("result_sha256"), "config_sha256": trial.get("config_sha256")}
        actual_evaluated[key] = {"public_sha256": instance.get("public_sha256"), "trials": trials}
    if actual_evaluated != expected_evaluated:
        issues.append("evaluation selected instance/trial set or artifact hashes mismatch")
    if evaluation["metrics"]["invalid_candidate_count"]:
        issues.append("independent candidate audit failed")
    if evaluation["metrics"]["integrity_verified_trials"] != total_trials:
        issues.append("independent trial integrity audit failed")
    for name, digest in summary.get("active_source_sha256", summary["source_sha256"]).items():
        if sha(HERE / name) != digest:
            issues.append(f"online source changed after freeze: {name}")
    for revision in summary.get("source_revisions", []):
        revision_path = Path(revision["path"])
        if sha(revision_path) != revision["sha256"]:
            issues.append("instrumentation revision ledger changed")
        ledger = json.loads(revision_path.read_text())
        if ledger["before_source_sha256"] != summary["source_sha256"]:
            issues.append("instrumentation revision has wrong initial source hashes")
        if ledger["after_source_sha256"] != summary.get("active_source_sha256"):
            issues.append("instrumentation revision has wrong active source hashes")
        for name, digest in ledger["before_source_sha256"].items():
            if sha(HERE / "provenance/phase2/instrumentation_v1" / name) != digest:
                issues.append(f"preserved v1 source changed: {name}")
        if ledger.get("plan_sha256") != summary["config_sha256"] or ledger.get("schedule_sha256") != summary["schedule_sha256"]:
            issues.append("instrumentation revision frozen plan/schedule mismatch")
        for path, digest in selected_configs.items():
            if ledger.get("online_config_sha256", {}).get(path) != digest:
                issues.append(f"instrumentation revision selected config mismatch: {path}")
    return {"schema": "ntru-e2e-phase2-artifact-audit-v1", "recorded_utc": datetime.now(timezone.utc).isoformat(),
            "selected_case_ids": sorted(selected), "inventory_path": str(inventory_path),
            "inventory_sha256": sha(inventory_path), "evaluation_path": str(evaluation_path),
            "evaluation_sha256": sha(evaluation_path),
            "phase1_preservation": preservation, "scheduled_trials": expected_count, "recorded_trials": total_trials,
            "trial_status_counts": statuses, "instrumentation_trial_counts": versions,
            "all_baselines_precede_sieve_trials": ordered_trials,
            "instances_with_both_patterns_recorded": paired_instances,
            "stored_candidate_count": candidate_counts, "issues": issues,
            "passed": preservation["passed"] and not issues, "result_sha256": files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--evaluation", type=Path)
    args = parser.parse_args(argv)
    report = audit(args.run_dir.resolve(), case_ids=args.case_ids, evaluation_path=args.evaluation)
    if args.output.exists():
        raise FileExistsError("Preserving prior audit")
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "result_sha256"}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
