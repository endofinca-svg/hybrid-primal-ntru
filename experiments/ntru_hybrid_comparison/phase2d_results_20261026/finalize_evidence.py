"""Posthoc public-only evidence checks; never reduce or decode any lattice."""

import ast
import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from experiments.ntru_hybrid_comparison.supervisor import _read_process
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate


HERE = Path(__file__).resolve().parent
ROOT = Path.cwd()


def read(path):
    return json.loads(Path(path).read_text())


def save(name, document):
    with (HERE / name).open("x") as stream:
        json.dump(document, stream, indent=2)
        stream.write("\n")


def main():
    preflight = read(HERE / "preflight.json")
    mismatches = [name for name, expected in preflight["protected_sha256"].items()
                  if not Path(name).is_file() or sha256(Path(name).read_bytes()).hexdigest() != expected]
    private = {}
    for name, before in preflight["private_metadata_only"].items():
        stat = Path(name).stat()
        after = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino}
        private[name] = {"before": before, "after": after, "unchanged": before == after}
    integrity = {"checked_utc": datetime.now(timezone.utc).isoformat(),
                 "protected_sha256_count": len(preflight["protected_sha256"]), "mismatches": mismatches,
                 "private_metadata_only": private, "private_contents_read": False,
                 "all_protected_unchanged": not mismatches and all(row["unchanged"] for row in private.values())}
    save("sealed_integrity.json", integrity)
    assert integrity["all_protected_unchanged"]
    frozen = read(HERE / "small.sources.json")["source_sha256"]
    assert all(sha256(Path(name).read_bytes()).hexdigest() == expected for name, expected in frozen.items())
    experiment = read(HERE / "small_run/experiment.final.json")
    plan = read(HERE / "small.plan.json")
    rows, workers, supervision_records = [], {}, []
    for trial, trial_plan in zip(experiment["trials"], plan["trials"]):
        worker = read(HERE / "small_run" / trial["trial_id"] / "owner/attempt_000/result.json")
        workers[trial["method"]] = worker
        public = read(trial_plan["public_path"])
        attempt = trial["attempts"][0]
        assert trial["execution_status"] == "completed" and trial["deadlines_satisfied"] and trial["cleanup_verified"]
        assert len(trial["attempts"]) == 1 and worker["dimensions"]["d"] <= 32
        assert worker["reduction"]["actual"]["completed_lll_calls"] == 1
        assert worker["reduction"]["actual"]["completed_tours"] == 1
        assert worker["reduction"]["exact_lattice_verification"]["verified"]
        assert worker["cached_reduction_used"] is False and worker["cached_database_used"] is False
        assert worker["progress"]["conservation_holds"]
        assert sum(worker["timings"][name] for name in worker["timings"] if name != "worker_wall_seconds") - worker["timings"]["worker_wall_seconds"] < 1e-7
        for candidate in worker["candidates"]:
            assert validate_public_candidate(public, candidate["f"], candidate["g"]) == candidate["public_validation"]
        for candidate in attempt["owner_candidate_validations"]:
            assert validate_public_candidate(public, candidate["f"], candidate["g"]) == candidate["public_validation"]
        outer, inner = trial["outer_supervision"], attempt["supervision"]
        supervision_records.extend([outer, inner])
        assert outer["ownership"] == "session" and inner["ownership"] == "process_group"
        assert inner["session_id"] == outer["session_id"] and inner["process_group_id"] != outer["process_group_id"]
        assert outer["pid"] != inner["pid"] and outer["returncode"] == inner["returncode"] == 0
        costs = worker["timings"]
        rows.append({"method": trial["method"], "instance_id": trial["instance_id"],
                     "mode": trial["cost_mode"], "k": worker["dimensions"]["k"], "kappa": worker["dimensions"]["kappa"],
                     "dimension": worker["dimensions"]["d"], "pattern_attempts": len(trial["attempts"]),
                     "requested_trial_budget_seconds": trial["config"]["trial_budget_seconds"],
                     "work_deadline_relative_seconds": trial["budget"]["work_deadline_relative_seconds"],
                     "hard_deadline_relative_seconds": trial["budget"]["hard_deadline_relative_seconds"],
                     "total_wall_seconds": trial["total_wall_seconds"], "reduction_seconds": costs["reduction_seconds"],
                     "gso_initialization_seconds": costs["gso_initialization_seconds"],
                     "extra_preprocess_seconds": costs["extra_preprocess_seconds"], "decode_seconds": costs["decode_seconds"],
                     "worker_online_io_seconds": costs["event_checkpoint_io_seconds"],
                     "inner_cleanup_seconds": trial["owner_diagnostics"]["cleanup_seconds"],
                     "outer_cleanup_seconds": trial["cleanup_seconds"],
                     "cleanup_scopes": "inner worker cleanup and outer owner-tree cleanup, potentially nested; do not double add",
                     "success_within_budget": trial["success_within_budget"],
                     "public_valid_observed": trial["public_valid_observed"],
                     "success_source": trial["first_public_valid"]["source"] if trial["first_public_valid"] else None,
                     "success_observed_seconds": (trial["first_public_valid"]["worker_observed_monotonic"] - trial["started_monotonic"]) if trial["first_public_valid"] else None,
                     "first_public_valid_block_size": 4 if trial["first_public_valid"] else None,
                     "block_size_scope": "post complete one-stage beta4 schedule; not a measured minimum or per-tour callback",
                     "execution_status": trial["execution_status"], "trial_stop_reason": trial["stop_reason"],
                     "attempt_stop_reason": worker["stop_reason"], "cleanup_verified": trial["cleanup_verified"],
                     "deadlines_satisfied": trial["deadlines_satisfied"],
                     "target_count": worker["progress"]["target_count"], "submitted_count": worker["progress"]["submitted_count"],
                     "completed_count": worker["progress"]["completed_count"],
                     "candidate_rejections": [c["public_validation"]["rejection_reasons"] for c in worker["candidates"]],
                     "slicer_projection_dimension": worker.get("backend_metadata", {}).get("actual_projection_dimension"),
                     "slicer_kernel_calls": worker.get("backend_metrics", {}).get("slicer_kernel_calls", 0),
                     "slicer_observed_iterations": [c.get("observable_iterations") for c in worker.get("backend_call_evidence", []) if "observable_iterations" in c],
                     "memory_bytes": trial["memory"]["value"], "memory_mib": trial["memory"]["value"] / 2**20,
                     "memory_metric": trial["memory"]["metric_name"], "memory_scope": trial["memory"]["trial_scope"],
                     "memory_sample_interval_seconds": trial["memory"]["sample_interval_seconds"],
                     "memory_is_estimate": True, "strict_memory_limit": False})
    assert workers["D"]["input_digests"] == workers["E"]["input_digests"]
    assert workers["D"]["coordinates"] == workers["E"]["coordinates"]
    assert workers["D"]["seeds"] == workers["E"]["seeds"]
    assert workers["D"]["candidate_counts"] == workers["E"]["candidate_counts"]
    observations = read(HERE / "process_observations.json")
    supervision_records.extend(row["result"] for row in observations)
    for supervision in supervision_records:
        assert supervision["cleanup_verified"] and supervision["hard_deadline_met"]
        assert supervision["residual_active_processes"] == []
        for process in supervision["observed_processes"]:
            current = _read_process(process["pid"])
            assert current is None or current["starttime_ticks"] != process["starttime_ticks"] or current["state"] in ("Z", "X")
    high_low = [r["result"]["memory"]["value"] for r in observations if "test_memory_high_then_low" in r["test_id"]]
    assert len(high_low) == 2 and high_low[1] < high_low[0] - 20 * 2**20
    checks = {"scope": "posthoc public verification, record/budget/memory/cleanup checks only; no backend computation",
              "all_passed": True, "frozen_source_count": len(frozen), "D_E_input_digests_equal": True,
              "D_E_fresh_separate_reduction": True, "actual_lattice_trials": 3, "actual_lattice_retry_or_rerun_count": 0,
              "real_supervision_observations": len(observations), "all_supervision_scopes_clean": True,
              "high_then_low_rss_bytes": high_low, "summary": experiment["summary"],
              "external_wall_seconds": read(HERE / "small.execution.json")["external_wall_seconds"],
              "global_online_wall_seconds": experiment["total_online_wall_seconds"],
              "global_overhead_seconds": experiment["global_overhead_seconds"],
              "rows": rows}
    save("actual_results.json", checks)
    with (HERE / "actual_results.csv").open("x", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    main_path = Path("experiments/ntru_hybrid_comparison/phase2b_results_20261026/public_inputs_n128_k40_kappa4.json")
    main_package = read(main_path)["payload"]
    assert main_package["basis"]["status"] == "original_only" and main_package["basis"]["reduced"] is None
    assert main_package["basis"]["execution"]["status"] == "not_started"
    assert main_package["candidate_batch"]["counts"]["submitted_count"] == main_package["candidate_batch"]["counts"]["completed_count"] == 0
    save("main_package_guard.json", {"scope": "manifest/state/hash check only; no main algebra/GSO/reduction/decode",
                                     "input_path": str(main_path), "sha256_before": preflight["protected_sha256"][str(main_path)],
                                     "sha256_after": sha256(main_path.read_bytes()).hexdigest(),
                                     "basis_status": "original_only", "reduced_basis": None,
                                     "execution_status": "not_started", "counts": main_package["candidate_batch"]["counts"],
                                     "main_n128_compute_calls": 0})
    detached = {}
    for name in ("standalone_worker.py", "fixed_backend.py", "paired_decode.py", "reduction.py", "inputs.py"):
        path = HERE.parent / name
        tree = ast.parse(path.read_text())
        names = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
        detached[name] = [name for name in names if name in ("os.setsid", "os.setpgid", "os.setpgrp", "os.fork", "subprocess.Popen", "subprocess.run")]
    assert not any(detached.values())
    save("worker_boundary_check.json", {"scope": "AST calls in actual solver/decoder path; deliberate launch boundary exists only in supervisor",
                                        "detaching_or_process_launch_calls": detached, "all_absent": True,
                                        "containment_limitation": "unobserved third-party setsid escape is not guaranteed; native runtime must retain session"})
    implementation = ["budgets.py", "summarize.py", "supervisor.py", "standalone.py", "standalone_worker.py", "guarded.py"]
    implementation += ["tests/test_" + name for name in ("budgets.py", "summarize.py", "supervisor.py", "standalone_runner.py", "standalone_worker.py", "guarded.py")]
    save("modified_files.json", {"schema": "ntru-phase2d-files-v1", "new_implementation_and_test_file_count": len(implementation),
                                "implementation_and_test_files": [{"path": str(HERE.parent / name), "change": "added", "sha256": sha256((HERE.parent / name).read_bytes()).hexdigest()} for name in implementation],
                                "modified_existing_implementation_files": [], "preserved_protected_file_count": len(preflight["protected_sha256"]),
                                "result_harnesses": ["run_tests.py", "finalize_evidence.py"], "committed_or_pushed": False})
    print(json.dumps({"sealed_files_unchanged": len(preflight["protected_sha256"]), "source_files_unchanged": len(frozen),
                      "actual_results_validated": len(rows), "high_low_rss_bytes": high_low, "main_n128_status": "original_only"}, indent=2))


if __name__ == "__main__":
    main()
