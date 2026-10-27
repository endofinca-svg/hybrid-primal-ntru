"""Read saved n=128 trials only; never run reduction or an online solver."""
import argparse
import csv
import json
from pathlib import Path


def rotation(values, shift, sign):
    n = len(values)
    return [sign * values[(i - shift) % n] * (1 if i >= shift else -1)
            for i in range(n)]


def analyze(run):
    inventory = json.loads((run / "online_inventory.json").read_text())
    trials = []
    for instance in inventory["instances"]:
        if instance["case_id"] not in {"n128_w8_16", "n128_w12_24"}:
            continue
        truth = json.loads(Path(instance["truth"]).read_text())
        for entry in instance["trials"]:
            result = json.loads(Path(entry["result"]).read_text())
            P, T = (result["coordinates"][name] for name in ("P", "T"))
            compatible, zero, nonzero, tails = 0, 0, 0, set()
            for shift in range(128):
                for sign in (1, -1):
                    f = rotation(truth["f"], shift, sign)
                    if any(f[i] for i in P):
                        continue
                    compatible += 1
                    tail = tuple(f[i] for i in T)
                    if any(tail):
                        nonzero += 1
                        tails.add(tail)
                    else:
                        zero += 1
            rows = result.get("bases", {}).get("lll", [])
            norms = [sum(x*x for x in row) for row in rows]
            distribution = result["parameters"]["distribution"]
            bound = distribution["w_f"] + distribution["w_g"]
            trials.append({
                "case_id": instance["case_id"], "trial_id": entry["trial_id"],
                "result": entry["result"], "instrumentation_version": entry["instrumentation_version"],
                "command_status": entry["online_command"]["status"],
                "command_exit_code": entry["online_command"]["exit_code"],
                "command_wall_seconds": entry["online_command"]["wall_seconds"],
                "snapshot_status": result["status"], "snapshot_stage": result["stage"],
                "snapshot_wall_seconds": result["wall_seconds"],
                "timings": result["timings"],
                "arms": {name: {key: arm.get(key) for key in (
                    "status", "seconds", "operation_seconds", "first_valid_seconds")}
                    | {"candidate_count": len(arm.get("candidates", []))}
                    for name, arm in result["arms"].items()},
                "kernel_started_calls": sum(e.get("event") == "slicer_call_started"
                                            for e in result["events"]),
                "last_events": result["events"][-3:],
                "lll_saved_basis": {"row_count": len(rows),
                    "minimum_norm_sq": min(norms) if norms else None,
                    "rows_within_secret_bound": sum(value <= bound for value in norms)},
                "offline_truth_geometry": {
                    "pattern_compatible_signed_rotations": compatible,
                    "zero_tail_signed_rotations": zero,
                    "nonzero_tail_signed_rotations": nonzero,
                    "distinct_nonzero_tails": len(tails),
                    "all_nonzero_truth_tails_in_saved_guesses": tails <= {tuple(g) for g in result["guesses"]},
                    "universal_zero_tail_signed_rotation_lower_bound":
                        2 * max(0, 128 - (len(P) + len(T)) * distribution["w_f"]),
                },
            })
    return {"scope": "offline analysis of the eight saved n=128 trials; no online work",
            "trials": trials}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.csv.exists():
        raise FileExistsError("Choose new output paths to preserve prior evidence")
    report = analyze(args.run_dir)
    with args.output.open("x") as handle:
        handle.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    with args.csv.open("x") as handle:
        fields = ["case_id", "trial_id", "command_status", "command_exit_code",
                  "command_wall_seconds", "snapshot_status", "snapshot_stage",
                  "snapshot_wall_seconds", "kernel_started_calls"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["trials"])
    print(json.dumps({"trial_count": len(report["trials"]),
                      "output": str(args.output), "csv": str(args.csv)}))
