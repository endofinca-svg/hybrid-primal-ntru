"""Public-input-only, bounded NTRU recovery using the pinned randomized Slicer."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import signal
import subprocess
import sys
import time
import traceback

from .algebra import (basis_rows, enumerate_tails, lattice_member, make_target,
                      multiplication_matrix, partition, reconstruct, validate_candidate)

HERE = Path(__file__).resolve().parent


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_version():
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, text=True, capture_output=True)
    files = [HERE / name for name in ("algebra.py", "attack.py", "slicer_adapter.py", "run_in_env.py", "config_phase1.json")]
    return {"git_head": head.stdout.strip(),
            "source_sha256": {p.name: file_sha(p) for p in files if p.exists()}}


def check_public(public):
    expected = {"schema", "instance_id", "n", "q", "ring", "h", "distribution"}
    if set(public) != expected or public["schema"] != "ntru-e2e-public-v1":
        raise ValueError("Unexpected public format or nonpublic fields")
    if public["ring"] != "x^n+1" or len(public["h"]) != public["n"]:
        raise ValueError("Unsupported ring or public-key dimension")
    if public["distribution"]["kind"] != "fixed_weight_ternary":
        raise ValueError("This experiment requires fixed public ternary weights")


def worker(args):
    # Deliberately no imports of either the generator or private evaluator.
    from .slicer_adapter import SlicerDecoder
    public = json.loads(args.public.read_text())
    check_public(public)
    config = json.loads(args.config.read_text())
    if any(key in config for key in ("keygen_seed", "keygen_seeds", "keygen_max_attempts", "truth", "private")):
        raise ValueError("Online configuration must not contain key generation or private metadata")
    if (public["n"], public["q"]) != (config["n"], config["q"]):
        raise ValueError("Public parameters do not match the frozen experiment")
    if (public["distribution"]["w_f"], public["distribution"]["w_g"]) != (config["w_f"], config["w_g"]):
        raise ValueError("Public weights do not match the frozen experiment")
    if config["threads"] != 1 or config["xi"] != 1:
        raise ValueError("Phase one is unscaled and single-threaded")
    start = time.perf_counter()
    cpu_start = time.process_time()
    result = {"schema": "ntru-e2e-result-v1", "instance_id": public["instance_id"],
              "public_sha256": file_sha(args.public), "config_sha256": file_sha(args.config),
              "experiment_seed": args.seed, "parameters": {k: public[k] for k in ["n", "q", "distribution"]},
              "code_version": code_version(), "started_utc": datetime.now(timezone.utc).isoformat(),
              "status": "running", "recovered": None, "trials": [],
              "diagnostics": {"basis_recoveries": [], "babai": None},
              "excluded_zero_tail": True, "error": None}
    events_path = args.output.with_suffix(".events.jsonl")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    # Fresh invocation has a fresh event log; original outputs are protected by the parent.
    events_file = events_path.open("w", buffering=1)
    current_trial = None

    def persist():
        result["wall_seconds"] = time.perf_counter() - start
        result["cpu_seconds"] = time.process_time() - cpu_start
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        atomic_json(args.output, result)

    def event(value):
        value = dict(value)
        value.update({"experiment_elapsed_seconds": time.perf_counter() - start,
                      "trial_id": current_trial["trial_id"] if current_trial else None})
        events_file.write(json.dumps(value, allow_nan=False) + "\n")
        if current_trial is not None:
            current_trial["events"].append(value)
            if value.get("event") == "slicer_call_started":
                current_trial["processed_guess_ids"] = sorted(set(
                    current_trial["processed_guess_ids"] + value.get("external_ids", [])))
        persist()

    rng = random.Random(args.seed)
    A = multiplication_matrix(public["h"], public["q"])
    persist()
    try:
        for configuration_index, choice in enumerate(config["configurations"]):
            for pattern_index in range(config["max_patterns_per_configuration"]):
                if time.perf_counter() - start >= args.time_limit:
                    result["status"] = "timeout"
                    return
                n, q = public["n"], public["q"]
                coordinates = partition(n, rng.sample(range(n), choice["k"]), choice["kappa"])
                enumerated = enumerate_tails(choice["kappa"], public["distribution"]["w_f"])
                guesses = enumerated["guesses"]
                trial_id = len(result["trials"])
                current_trial = {"trial_id": trial_id, "configuration_index": configuration_index,
                                 "configuration": choice, "pattern_index": pattern_index,
                                 **coordinates, "dimension": 2 * n - choice["k"] - choice["kappa"],
                                 "raw_nonzero_tail_count": enumerated["raw_nonzero"],
                                 "filtered_tail_count": len(guesses), "guesses": guesses,
                                 "processed_guess_ids": [], "slicer_candidates": [], "events": [],
                                 "status": "running", "timings": {}}
                result["trials"].append(current_trial)
                P, C, T = (coordinates[key] for key in ("P", "C", "T"))
                tick = time.perf_counter()
                rows = basis_rows(A, q, C)
                # Centering subtracts qZ^n, and the exact targets are kept for reconstruction.
                targets = [make_target(A, T, u, len(C), q=q) for u in guesses]
                bounds = [public["distribution"]["w_f"] + public["distribution"]["w_g"]
                          - sum(value != 0 for value in u) for u in guesses]
                current_trial["targets"] = targets
                current_trial["public_bounds_sq"] = bounds
                current_trial["unique_ambient_targets"] = len({tuple(t) for t in targets})
                current_trial["timings"]["construction_seconds"] = time.perf_counter() - tick
                event({"event": "pattern_constructed", "target_count": len(targets),
                       "bkz_beta": choice["bkz_beta"], "sieve_dim": choice["sieve_dim"]})
                backend_seed = rng.randrange(1, 2 ** 31)
                current_trial["backend_seed"] = backend_seed
                decoder = SlicerDecoder(rows, choice["bkz_beta"], choice["sieve_dim"],
                                        backend_seed, event_callback=event,
                                        nrand=config["slicer_randomizations"],
                                        max_iterations=config["slicer_max_iterations"],
                                        bkz_max_loops=config["bkz_max_loops"])
                if hasattr(decoder, "reduced_rows"):
                    current_trial["reduced_basis_rows"] = decoder.reduced_rows
                # Public verification only: observe early recovery without changing the online search.
                for checkpoint in getattr(decoder, "basis_checkpoints", []):
                    for row_index, row in enumerate(checkpoint["rows"]):
                        candidate = reconstruct(row, [0] * len(row), P, C, T, [0] * len(T), n)
                        verified = validate_candidate(public, candidate["f"], candidate["g"])
                        if verified["valid"]:
                            result["diagnostics"]["basis_recoveries"].append({
                                "trial_id": trial_id, "stage": checkpoint["stage"], "row_index": row_index,
                                **candidate, "validation": verified})
                            break
                if result["diagnostics"]["babai"] is None:
                    tick = time.perf_counter()
                    babai_candidates = decoder.direct_babai(targets, bounds, list(range(len(targets))))
                    diagnostic = {"trial_id": trial_id, "candidate_count": len(babai_candidates), "valid": []}
                    for item in babai_candidates:
                        target_id = item["target_id"]
                        candidate = reconstruct(item["v"], targets[target_id], P, C, T, guesses[target_id], n)
                        verified = validate_candidate(public, candidate["f"], candidate["g"])
                        if verified["valid"]:
                            diagnostic["valid"].append({**candidate, "target_id": target_id,
                                                        "method": "babai_diagnostic", "validation": verified})
                    diagnostic["seconds"] = time.perf_counter() - tick
                    result["diagnostics"]["babai"] = diagnostic
                    diagnostic["scope"] = "first_pattern_only"
                    persist()
                decoded = decoder.decode(targets, bounds, list(range(len(targets))))
                tick = time.perf_counter()
                for item in decoded:
                    target_id = int(item["target_id"])
                    if not 0 <= target_id < len(guesses):
                        raise ValueError("Backend returned an invalid external target ID")
                    if not lattice_member(item["v"], A, q, C):
                        raise ValueError("Backend returned a vector outside the public row lattice")
                    candidate = reconstruct(item["v"], targets[target_id], P, C, T, guesses[target_id], n)
                    verified = validate_candidate(public, candidate["f"], candidate["g"])
                    trace = {**item, **candidate, "valid": verified["valid"], "validation": verified}
                    current_trial["slicer_candidates"].append(trace)
                    if verified["valid"] and result["recovered"] is None:
                        result["recovered"] = {**candidate, "v": item["v"], "source": {
                            "stage": "slicer", "trial_id": trial_id, "target_id": target_id,
                            "call_id": item["call_id"]}, "validation": verified,
                            "core_is_zero": not any(candidate["f"][position] for position in C)}
                current_trial["timings"]["reconstruction_validation_seconds"] = time.perf_counter() - tick
                current_trial["backend_metrics"] = getattr(decoder, "metrics", {})
                current_trial["status"] = "success" if result["recovered"] else "no_recovery"
                event({"event": "pattern_finished", "candidate_count": len(decoded),
                       "valid_count": sum(c["valid"] for c in current_trial["slicer_candidates"])})
                if result["recovered"]:
                    result["status"] = "success"
                    return
        result["status"] = "no_recovery"
    except BaseException as exc:
        result["status"] = "error"
        result["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        if current_trial:
            current_trial["status"] = "error"
        traceback.print_exc()
        raise
    finally:
        persist()
        events_file.close()
        print(json.dumps({"instance_id": result["instance_id"], "status": result["status"],
                          "patterns": len(result["trials"]), "wall_seconds": result["wall_seconds"],
                          "output": str(args.output)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=HERE / "config_phase1.json")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--time-limit", type=float)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    args.time_limit = min(args.time_limit or config["instance_timeout_seconds"], config["instance_timeout_seconds"])
    if args.worker:
        worker(args)
        return
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing result; choose a fresh --output")
    check_public(json.loads(args.public.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "experiments.ntru_e2e.attack", "--worker", "--public", str(args.public),
               "--config", str(args.config), "--seed", str(args.seed), "--output", str(args.output),
               "--time-limit", str(args.time_limit)]
    started = time.perf_counter()
    process = subprocess.Popen(command, start_new_session=True)
    timed_out = False
    try:
        exit_code = process.wait(timeout=args.time_limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            exit_code = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            exit_code = process.wait()
    result = json.loads(args.output.read_text()) if args.output.exists() else {
        "schema": "ntru-e2e-result-v1", "instance_id": json.loads(args.public.read_text())["instance_id"],
        "status": "error", "recovered": None, "trials": [], "error": "worker exited before first checkpoint"}
    if timed_out:
        result["status"] = "timeout"
        if result["trials"] and result["trials"][-1]["status"] == "running":
            result["trials"][-1]["status"] = "timeout"
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    result["supervisor"] = {"argv": command, "exit_code": exit_code, "timed_out": timed_out,
                            "wall_seconds": time.perf_counter() - started,
                            "cpu_seconds": usage.ru_utime + usage.ru_stime,
                            "peak_rss_kib": usage.ru_maxrss, "limit_seconds": args.time_limit}
    atomic_json(args.output, result)
    print(json.dumps({"status": result["status"], "exit_code": exit_code, "timed_out": timed_out,
                      "wall_seconds": result["supervisor"]["wall_seconds"]}), flush=True)
    raise SystemExit(0 if result["status"] in ("success", "no_recovery", "timeout") else 1)


if __name__ == "__main__":
    main()
