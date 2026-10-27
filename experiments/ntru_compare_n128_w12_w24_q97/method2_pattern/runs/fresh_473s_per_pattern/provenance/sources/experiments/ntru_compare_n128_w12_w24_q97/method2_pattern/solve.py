"""Public-input zero-pattern primal attack with exactly two tours per beta.

This process never reads private truth or the key generation seed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import signal
import time
import traceback

from experiments.ntru_e2e.algebra import basis_rows, multiplication_matrix, validate_candidate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scan_rows(rows, public, core, pattern):
    """Check every row; exact public radius filtering precedes validation."""
    n = public["n"]
    norm_bound = public["distribution"]["w_f"] + public["distribution"]["w_g"]
    norms = [sum(x * x for x in row) for row in rows]
    rejected = Counter()
    for row_index, (row, norm) in enumerate(zip(rows, norms)):
        if norm != norm_bound:
            rejected["exact_weight_norm_mismatch"] += 1
            continue
        f, g = [0] * n, row[:n]
        for position, value in zip(core, row[n:]):
            f[position] = value
        if any(x not in (-1, 0, 1) for x in row):
            rejected["nonternary"] += 1
            continue
        validation = validate_candidate(public, f, g)
        if not validation["valid"]:
            rejected["public_validation_failed"] += 1
            continue
        return {"f": f, "g": g, "vector": row, "row_index": row_index,
                "sign": 1, "norm_sq": norm, "public_validation": validation}, min(norms), dict(rejected)
    return None, min(norms), dict(rejected)


def solve(public_path, config_path, output):
    wall_start, cpu_start = time.perf_counter(), time.process_time()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Preserving prior baseline: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoints = output.parent / "checkpoints"
    checkpoints.mkdir(exist_ok=False)
    public = json.loads(Path(public_path).read_text())
    config = json.loads(Path(config_path).read_text())
    n = public["n"]
    pattern = config["pattern"]
    if (not isinstance(pattern, list) or len(pattern) != config["k"]
            or any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n for i in pattern)
            or len(set(pattern)) != len(pattern) or not 0 < len(pattern) < n):
        raise ValueError("Pattern must contain k distinct public coordinate indices")
    pattern = sorted(pattern)
    core = [i for i in range(n) if i not in set(pattern)]
    dimension = n + len(core)
    if config["bkz_max_tours_per_beta"] != 2 or config["bkz_clean_stops_current_beta"]:
        raise ValueError("This baseline requires exactly two tours per beta; clean does not skip tour 2")
    if (n != config["n"] or public["q"] != config["q"]
            or public["distribution"]["w_f"] != config["w_f"]
            or public["distribution"]["w_g"] != config["w_g"]
            or public["ring"] != "x^n+1" or config["kappa"] != 0):
        raise ValueError("Public instance and pattern-primal configuration differ")
    result = {"schema": "ntru-pattern-primal-result-v1", "status": "running",
              "public_sha256": sha(public_path), "config_sha256": sha(config_path),
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "dimension": dimension, "coordinate_order": ["g", "f_C"],
              "coordinates": {"P": pattern, "C": core, "T": []},
              "pattern_index": config.get("pattern_index"),
              "stages": [], "found_candidate": None, "current_stage": "initialization",
              "timings": {}, "checkpoint_io_seconds": 0.0,
              "timing_scope": "Online includes imports, construction, LLL, all prior BKZ tours, scans and checkpoint writes; offline audit and key generation excluded.",
              "success_detection": "First public-valid basis row, checked after initial LLL and each complete BKZ tour; no truth supplied.",
              "minimum_beta_scope": "First successful beta in this progressive, warm-started schedule; not a globally minimal beta."}
    io_seconds = 0.0
    matrix = transform = None
    events_path = output.parent / "events.jsonl"
    if events_path.exists():
        raise FileExistsError(events_path)
    events = events_path.open("x")

    def write_json(path, value):
        nonlocal io_seconds
        tick = time.perf_counter()
        path = Path(path)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
        temporary.replace(path)
        io_seconds += time.perf_counter() - tick

    def persist(event=None):
        nonlocal io_seconds
        if event:
            record = {"event": event, "stage": result["current_stage"],
                      "elapsed_seconds": time.perf_counter()-wall_start,
                      "recorded_utc": datetime.now(timezone.utc).isoformat()}
            events.write(json.dumps(record) + "\n")
            events.flush()
            print(json.dumps(record), flush=True)
        result["checkpoint_io_seconds"] = io_seconds
        result["worker_wall_seconds"] = time.perf_counter()-wall_start
        result["worker_cpu_seconds"] = time.process_time()-cpu_start
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        write_json(output, result)

    def snapshot(label):
        rows = [[int(x) for x in row] for row in matrix]
        basis_path = checkpoints / (label + ".basis.json")
        transform_path = checkpoints / (label + ".transform.json")
        write_json(basis_path, {"rows": rows})
        write_json(transform_path, {"rows": [[int(x) for x in row] for row in transform]})
        result["latest_complete_basis_path"] = str(basis_path.relative_to(output.parent))
        result["latest_complete_transform_path"] = str(transform_path.relative_to(output.parent))
        return rows

    def check_stage(label, kind, beta, tour, seconds, clean=None):
        rows = [[int(x) for x in row] for row in matrix]
        tick = time.perf_counter()
        candidate, minimum, rejected = scan_rows(rows, public, core, pattern)
        scan_seconds = time.perf_counter()-tick
        stage = {"stage_id": label, "kind": kind, "beta": beta, "tour": tour,
                 "stage_wall_seconds": seconds, "scan_wall_seconds": scan_seconds,
                 "cumulative_wall_seconds": time.perf_counter()-wall_start,
                 "minimum_norm_sq": minimum, "candidate_count": int(candidate is not None),
                 "rejected_rows": rejected, "clean": clean}
        result["stages"].append(stage)
        if candidate is not None:
            candidate.update(beta=beta, tour=tour, stage_id=label)
            result["found_candidate"] = candidate
            result["first_valid_seconds"] = time.perf_counter()-wall_start
            result["first_valid_cpu_seconds"] = time.process_time()-cpu_start
            result["first_success_beta"] = beta
            result["first_success_tour"] = tour
            result["first_success_stage"] = kind
        snapshot(label)
        if candidate is not None:
            result["stage_basis_path"] = result["latest_complete_basis_path"]
            result["transformation_path"] = result["latest_complete_transform_path"]
            result["status"] = "success"
        persist("stage_completed")
        print(json.dumps({"stage_id": label, "minimum_norm_sq": minimum,
                          "stage_wall_seconds": seconds, "candidate": candidate is not None}), flush=True)
        return candidate is not None

    def interrupted(signum, frame):
        raise InterruptedError(f"Worker received signal {signum}; latest complete stage is preserved")

    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        persist("worker_started")
        tick = time.perf_counter()
        import fpylll
        from fpylll import FPLLL, GSO, IntegerMatrix, LLL, BKZ
        from fpylll.algorithms.bkz2 import BKZReduction
        from fpylll.fplll.bkz_param import Strategy, load_strategies_json
        result["timings"]["dependency_import_seconds"] = time.perf_counter()-tick
        FPLLL.set_threads(config["threads"])
        FPLLL.set_random_seed(config["solver_seed"])
        FPLLL.set_precision(config["precision_bits"])
        strategies = load_strategies_json(BKZ.DEFAULT_STRATEGY)
        strategy_original_length = len(strategies)
        strategies = list(strategies)
        for beta in range(len(strategies), dimension+1):
            strategies.append(Strategy(beta, [], []))
        result["environment"] = {"python": platform.python_version(), "fpylll": fpylll.__version__,
                                 "platform": platform.platform(), "threads": config["threads"],
                                 "float_type": "mpfr", "precision_bits": config["precision_bits"],
                                 "strategy_path": BKZ.DEFAULT_STRATEGY.decode(),
                                 "strategy_sha256": sha(BKZ.DEFAULT_STRATEGY.decode()),
                                 "strategy_max_beta": strategy_original_length-1,
                                 "strategy_above_max": "unpruned SVP; no preprocessing blocks"}
        tick = time.perf_counter()
        original = basis_rows(multiplication_matrix(public["h"], public["q"]), public["q"], core)
        matrix = IntegerMatrix.from_matrix(original, int_type="mpz")
        transform = IntegerMatrix.identity(dimension, int_type="mpz")
        result["timings"]["construction_seconds"] = time.perf_counter()-tick
        initial = output.parent / "initial_basis.json"
        write_json(initial, {"rows": original})
        result["initial_basis_path"] = initial.name
        result["initial_basis_sha256"] = sha(initial)
        result["current_stage"] = "lll"
        persist("stage_started")
        tick = time.perf_counter()
        LLL.reduction(matrix, U=transform, delta=config["lll_delta"], eta=config["lll_eta"],
                      method="proved", float_type="mpfr", precision=config["precision_bits"])
        lll_seconds = time.perf_counter()-tick
        if not check_stage("lll", "lll", None, 0, lll_seconds):
            tick = time.perf_counter()
            gso = GSO.Mat(matrix, U=transform, float_type="mpfr")
            gso.update_gso()
            reduction = BKZReduction(LLL.Reduction(gso, delta=config["lll_delta"], eta=config["lll_eta"]))
            result["timings"]["bkz_gso_setup_seconds"] = time.perf_counter()-tick
            for beta in range(config["bkz_start"], min(config["bkz_stop"], dimension)+1, config["bkz_jump"]):
                params = BKZ.Param(beta, strategies=strategies, delta=config["lll_delta"], flags=BKZ.DEFAULT)
                for tour in range(1, config["bkz_max_tours_per_beta"]+1):
                    label = f"bkz_b{beta:03d}_t{tour:02d}"
                    result["current_stage"] = label
                    persist("stage_started")
                    tick = time.perf_counter()
                    clean = bool(reduction.tour(params))
                    seconds = time.perf_counter()-tick
                    if check_stage(label, "bkz", beta, tour, seconds, clean):
                        break
                    if clean and config["bkz_clean_stops_current_beta"]:
                        break
                if result["status"] == "success":
                    break
        if result["status"] != "success":
            result["status"] = "exhausted"
        result["current_stage"] = "finished"
    except BaseException as error:
        result["status"] = "interrupted" if isinstance(error, (InterruptedError, KeyboardInterrupt)) else "error"
        result["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        traceback.print_exc()
    finally:
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        persist("worker_finished")
        events.close()
        for s, handler in previous.items():
            signal.signal(s, handler)
    return 0 if result["status"] == "success" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return solve(args.public, args.config, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
