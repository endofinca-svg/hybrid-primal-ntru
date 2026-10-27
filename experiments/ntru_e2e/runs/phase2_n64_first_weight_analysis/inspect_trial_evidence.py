"""Read-only reanalysis of existing first-weight results; launches no decoder."""
from collections import Counter
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
CASE = "n64_w4_8"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def secret_key(candidate):
    return tuple(candidate["f"] + candidate["g"])


def main():
    inventory_path = HERE / "runs/phase2/online_inventory.json"
    inventory = json.loads(inventory_path.read_text())
    inputs = {str(inventory_path): sha(inventory_path)}
    trials = []
    for instance in inventory["instances"]:
        if instance["case_id"] != CASE:
            continue
        for trial in instance["trials"]:
            path = Path(trial["result"])
            result = json.loads(path.read_text())
            inputs[str(path)] = sha(path)
            arms, events = result["arms"], result["events"]
            calls = [e for e in events if e.get("event") == "slicer_call_finished"]
            starts = [e for e in events if e.get("event") == "slicer_call_started"]
            if trial["status"] != "finished":
                assert not starts
            pairs = {method: {secret_key(c) for c in arm["candidates"]}
                     for method, arm in arms.items()}
            row_pairs = pairs["lll_rows"] | pairs["bkz_rows"]
            union = set().union(*(pairs[m] for m in pairs if m != "slicer"))
            cp = result["timings"]["checkpoint_io_seconds"]
            description = ("completed" if trial["status"] == "finished" else
                           "Slicer projection preparation before target DB" if arms["slicer"]["status"] == "running" else
                           "lattice database construction" if result.get("stage") == "database_setup" else
                           "BKZ Babai" if arms["bkz_babai"]["status"] == "running" else
                           "LLL row validation")
            trials.append({
                "instance_id": instance["instance_id"], "trial_id": trial["trial_id"],
                "instrumentation_version": trial["instrumentation_version"],
                "command_status": trial["status"], "worker_status": result["status"],
                "interruption_evidence": result.get("error"), "last_phase": description,
                "charged_online_seconds": trial["charged_online_seconds"],
                "worker_wall_seconds": result["wall_seconds"],
                "checkpoint_io_seconds": cp,
                "checkpoint_fraction_of_worker_wall": cp / result["wall_seconds"],
                "timings": result["timings"],
                "arm_timings": {m: {k: v for k, v in arm.items() if k != "candidates"}
                                for m, arm in arms.items()},
                "slicer_calls_started": len(starts), "slicer_calls_finished": len(calls),
                "slicer_kernel_seconds": [e["seconds"] for e in calls],
                "slicer_iterations": [e["stats"]["itercount_slicer"] for e in calls],
                "slicer_target_db_rejections": result.get("backend_metrics", {}).get("target_db_rejections"),
                "overlap": {
                    "slicer_raw_count": len(pairs["slicer"]),
                    "lll_babai_raw_count": len(pairs["lll_babai"]),
                    "bkz_babai_raw_count": len(pairs["bkz_babai"]),
                    "slicer_intersection_lll_babai": len(pairs["slicer"] & pairs["lll_babai"]),
                    "slicer_intersection_bkz_babai": len(pairs["slicer"] & pairs["bkz_babai"]),
                    "slicer_intersection_rows": len(pairs["slicer"] & row_pairs),
                    "slicer_new_vs_rows": len(pairs["slicer"] - row_pairs),
                    "slicer_new_vs_all_baselines": len(pairs["slicer"] - union),
                    "identical_to_bkz_babai": pairs["slicer"] == pairs["bkz_babai"],
                }})
    assert len(trials) == 12
    counts = Counter(t["command_status"] for t in trials)
    assert counts == {"timeout": 7, "interrupted_censored": 1, "finished": 4}
    for trial in trials:
        if trial["command_status"] == "finished":
            assert trial["overlap"]["slicer_raw_count"] == 8
            assert trial["overlap"]["identical_to_bkz_babai"]
            assert trial["overlap"]["slicer_new_vs_all_baselines"] == 0
    report = {
        "schema": "ntru-phase2-n64-trial-reanalysis-v1", "case_id": CASE,
        "scope": "existing first-weight artifacts only; no online execution",
        "status_counts": dict(counts), "input_sha256": inputs,
        "script_sha256": sha(__file__), "trials": trials,
    }
    destination = OUT / "trial_reanalysis.json"
    with destination.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(destination), "status_counts": dict(counts),
                      "all_censored_trials_have_no_kernel_start": True,
                      "complete_trial_slicer_equals_bkz_babai": True}, indent=2))


if __name__ == "__main__":
    main()
