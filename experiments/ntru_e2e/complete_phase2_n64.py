"""Finish only the existing n=64 first-weight offline evidence, without reruns.

The original run is immutable.  A new output directory freezes this narrower
scope before the independent evaluator is launched in a separate process.
This entry point has no key-generation or online-solver command.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

from .attack import atomic_json, file_sha
from .record_command import capture
from .run_phase2 import validate_plan, validate_resume_sources

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CASE_ID = "n64_w4_8"
OFFLINE_SOURCES = ("complete_phase2_n64.py", "evaluate_phase2.py", "audit_phase2.py")


def tree_hashes(directory):
    return {str(path.relative_to(directory)): file_sha(path)
            for path in sorted(directory.rglob("*")) if path.is_file()}


def phase1_preservation():
    baseline = HERE / "provenance/phase2/phase1_preservation_before.json"
    expected = json.loads(baseline.read_text())["files_sha256"]
    changed = [name for name, digest in expected.items()
               if not (HERE / name).is_file() or file_sha(HERE / name) != digest]
    if changed:
        raise ValueError(f"Phase-one preservation failed: {changed}")
    return {"manifest": str(baseline), "manifest_sha256": file_sha(baseline),
            "checked_file_count": len(expected), "passed": True}


def backend_preservation():
    environment = json.loads((HERE / "provenance/phase2/environment_resume.json").read_text())
    files = {info["path"]: info["sha256"] for info in environment["runtime"]["modules"].values()}
    for path, digest in files.items():
        if file_sha(path) != digest:
            raise ValueError(f"Recorded compiled backend changed: {path}")
    return files


def selected_records(inventory, plan):
    """Reject an incomplete or altered schedule before any truth is read."""
    case = next(case for case in plan["cases"] if case["name"] == CASE_ID)
    if (case["n"], case["w_f"], case["w_g"]) != (64, 4, 8):
        raise ValueError("Only the frozen n=64 first weight is supported")
    records = [entry for entry in inventory["instances"] if entry["case_id"] == CASE_ID]
    if len(records) != len(case["keygen_seeds"]):
        raise ValueError("First-weight instance schedule is incomplete")
    expected_trials = {(choice, pattern) for choice in range(len(plan["choices"]))
                       for pattern in range(plan["patterns_per_instance"])}
    for index, record in enumerate(records, 1):
        if (record["instance_id"] != f"instance_{index:02d}"
                or record["keygen_seed"] != case["keygen_seeds"][index - 1]
                or record["solver_seed"] != case["solver_seeds"][index - 1]):
            raise ValueError("First-weight seeds or instance order changed")
        actual = {(trial["choice_index"], trial["pattern_index"]) for trial in record["trials"]}
        if actual != expected_trials or len(record["trials"]) != len(expected_trials):
            raise ValueError("First-weight trial schedule is incomplete")
        for trial in record["trials"]:
            if trial["status"] not in {"finished", "timeout", "error", "interrupted_censored"}:
                raise ValueError("Offline continuation requires every selected trial to have been attempted")
            if not Path(trial["result"]).is_file():
                raise ValueError("Missing first-weight trial evidence")
    return records


def freeze_scope(run_dir, output_dir):
    """Create a retrospective scope record; never change the original freeze."""
    run_dir, output_dir = Path(run_dir).resolve(), Path(output_dir).resolve()
    if output_dir == run_dir or run_dir in output_dir.parents:
        raise ValueError("Use a separate output directory outside the original run")
    if output_dir.exists():
        raise FileExistsError("Preserving existing continuation; use --resume")
    inventory_path = run_dir / "online_inventory.json"
    inventory = json.loads(inventory_path.read_text())
    plan_path = Path(inventory["config"])
    plan = json.loads(plan_path.read_text())
    validate_plan(plan, plan_path)
    validate_resume_sources(run_dir, inventory)
    records = selected_records(inventory, plan)
    preservation = phase1_preservation()
    scope = {
        "schema": "ntru-e2e-phase2-n64-offline-scope-v1",
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_kind": "retrospective offline scope; original pre-generation plan remains authoritative",
        "run_dir": str(run_dir), "case_ids": [CASE_ID],
        "online_execution_allowed": False, "new_online_trials": 0,
        "n128_requires_separate_request": True,
        "inventory_path": str(inventory_path), "inventory_sha256": file_sha(inventory_path),
        "plan_sha256": inventory["config_sha256"],
        "schedule_sha256": inventory["schedule_sha256"],
        "case": next(case for case in plan["cases"] if case["name"] == CASE_ID),
        "choices": plan["choices"],
        "selected_instance_count": len(records),
        "selected_trial_count": sum(len(record["trials"]) for record in records),
        "selected_charged_online_seconds": sum(record["online_wall_seconds"] for record in records),
        "active_online_source_sha256": inventory.get("active_source_sha256", inventory["source_sha256"]),
        "offline_source_sha256": {name: file_sha(HERE / name) for name in OFFLINE_SOURCES},
        "compiled_backend_sha256": backend_preservation(),
        "phase1_preservation": preservation,
        "original_run_files_sha256": tree_hashes(run_dir),
        "original_provenance_files_sha256": tree_hashes(HERE / "provenance/phase2"),
    }
    output_dir.mkdir(parents=True)
    for name in OFFLINE_SOURCES:
        (output_dir / "offline_sources").mkdir(exist_ok=True)
        shutil.copyfile(HERE / name, output_dir / "offline_sources" / name)
    shutil.copyfile(plan_path, output_dir / "config_phase2.json")
    shutil.copyfile(records[0]["trials"][0]["config"], output_dir / "online_config.json")
    schedules = json.loads(Path(inventory["schedule"]).read_text())
    atomic_json(output_dir / "schedule_selected.json", {
        "original_schedule_sha256": scope["schedule_sha256"],
        "schedules": [entry for entry in schedules["schedules"] if entry["case_id"] == CASE_ID]})
    scope["snapshot_files_sha256"] = tree_hashes(output_dir)
    atomic_json(output_dir / "scope_frozen.json", scope)
    return scope


def verify_scope(scope, output_dir):
    run_dir = Path(scope["run_dir"])
    if scope["case_ids"] != [CASE_ID] or scope["online_execution_allowed"] is not False:
        raise ValueError("Continuation scope changed")
    if tree_hashes(run_dir) != scope["original_run_files_sha256"]:
        raise ValueError("Original phase-two evidence changed")
    if tree_hashes(HERE / "provenance/phase2") != scope["original_provenance_files_sha256"]:
        raise ValueError("Original phase-two provenance changed")
    for name, digest in scope["snapshot_files_sha256"].items():
        if file_sha(Path(output_dir) / name) != digest:
            raise ValueError(f"Frozen continuation snapshot changed: {name}")
    for name, digest in {**scope["active_online_source_sha256"], **scope["offline_source_sha256"]}.items():
        if file_sha(HERE / name) != digest:
            raise ValueError(f"Frozen source changed: {name}")
    for path, digest in scope["compiled_backend_sha256"].items():
        if file_sha(path) != digest:
            raise ValueError(f"Frozen compiled backend changed: {path}")
    if phase1_preservation() != scope["phase1_preservation"]:
        raise ValueError("Phase-one preservation manifest changed")


def run(run_dir, output_dir, *, resume=False):
    output_dir = Path(output_dir).resolve()
    scope_path = output_dir / "scope_frozen.json"
    scope = json.loads(scope_path.read_text()) if resume else freeze_scope(run_dir, output_dir)
    if Path(run_dir).resolve() != Path(scope["run_dir"]):
        raise ValueError("Continuation refers to a different original run")
    verify_scope(scope, output_dir)
    evaluation_path = output_dir / "evaluation_phase2.json"
    audit_path = output_dir / "artifact_audit.json"
    for module, destination, extra in (
        ("evaluate_phase2", evaluation_path, []),
        ("audit_phase2", audit_path, ["--evaluation", str(evaluation_path)]),
    ):
        prefix = output_dir / module
        if destination.exists():
            metadata_path = prefix.with_suffix(".command.json")
            if not metadata_path.exists() or json.loads(metadata_path.read_text()).get("exit_code") != 0:
                raise ValueError("Preserving unconfirmed offline output; inspect its command log")
            continue
        if prefix.with_suffix(".command.json").exists():
            raise ValueError("Prior offline command failed or was interrupted; preserve it and use a new output directory")
        metadata = capture([
            sys.executable, str(HERE / "run_in_env.py"), "-m",
            "experiments.ntru_e2e." + module,
            "--run-dir", scope["run_dir"], "--case", CASE_ID,
            "--output", str(destination), *extra], prefix, cwd=ROOT, timeout=600)
        if metadata.get("exit_code") != 0:
            raise RuntimeError(f"Offline {module} failed; see {prefix}.stderr.txt")
    verify_scope(scope, output_dir)
    evaluation = json.loads(evaluation_path.read_text())
    audit = json.loads(audit_path.read_text())
    if audit["evaluation_sha256"] != file_sha(evaluation_path):
        raise ValueError("Audited evaluation changed before completion")
    if (evaluation["inventory_sha256"] != scope["inventory_sha256"]
            or evaluation["selected_case_ids"] != [CASE_ID]):
        raise ValueError("Evaluation scope or original inventory changed")
    if (not audit["passed"] or evaluation["metrics"]["invalid_candidate_count"]
            or evaluation["metrics"]["instance_count"] != scope["selected_instance_count"]
            or evaluation["metrics"]["integrity_verified_trials"] != scope["selected_trial_count"]):
        raise ValueError("Independent scoped validation failed")
    completion = {"schema": "ntru-e2e-phase2-n64-completion-v1", "status": "finished",
                  "completed_utc": datetime.now(timezone.utc).isoformat(),
                  "case_ids": [CASE_ID], "new_online_trials": 0,
                  "scope_sha256": file_sha(scope_path),
                  "evaluation_sha256": file_sha(evaluation_path), "audit_sha256": file_sha(audit_path),
                  "original_run_unchanged": True, "phase1_preservation": scope["phase1_preservation"],
                  "selected_charged_online_seconds": scope["selected_charged_online_seconds"],
                  "metrics": evaluation["metrics"]}
    completion_path = output_dir / "completion.json"
    if completion_path.exists():
        existing = json.loads(completion_path.read_text())
        for key in ("scope_sha256", "evaluation_sha256", "audit_sha256"):
            if existing[key] != completion[key]:
                raise ValueError(f"Completed offline evidence changed: {key}")
    else:
        atomic_json(completion_path, completion)
    return json.loads(completion_path.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=HERE / "runs/phase2")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    report = run(args.run_dir, args.output_dir, resume=args.resume)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
