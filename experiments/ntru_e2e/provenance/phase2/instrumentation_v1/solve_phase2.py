"""One public-input-only, paired phase-two trial with durable partial evidence.

The runner owns wall-clock limits.  This worker never reads an inventory, private
key, generator seed, or offline audit result.  All methods run even after success.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
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
METHODS = ("direct_tail", "lll_rows", "lll_babai", "bkz_rows", "bkz_babai", "slicer")
ONLINE_FIELDS = {"schema", "n", "q", "w_f", "w_g", "k", "kappa", "patterns_per_instance",
                 "bkz_max_loops", "slicer_randomizations", "slicer_max_iterations",
                 "choices", "plan_sha256", "xi", "threads"}


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_inputs(public: dict, config: dict) -> None:
    """Reject secret-bearing or ambiguous online files before importing a backend."""
    expected = {"schema", "instance_id", "n", "q", "ring", "h", "distribution"}
    if set(public) != expected or public["schema"] != "ntru-e2e-public-v1":
        raise ValueError("Unexpected public format or nonpublic fields")
    if not _integer(public["n"]) or public["n"] < 1 or not _integer(public["q"]) or public["q"] < 2:
        raise ValueError("Invalid public dimension or modulus")
    n, q = public["n"], public["q"]
    if (public["ring"] != "x^n+1" or not isinstance(public["h"], list)
            or len(public["h"]) != n or any(not _integer(x) or not 0 <= x < q for x in public["h"])):
        raise ValueError("Unsupported public ring or key")
    distribution = public["distribution"]
    if (set(distribution) != {"kind", "w_f", "w_g"}
            or distribution["kind"] != "fixed_weight_ternary"):
        raise ValueError("Unexpected public distribution or nonpublic metadata")
    if set(config) - ONLINE_FIELDS:
        raise ValueError("Online configuration contains unsupported or nonpublic fields")
    required = ONLINE_FIELDS - {"schema", "plan_sha256", "xi", "threads"}
    if required - set(config):
        raise ValueError("Online configuration is incomplete")
    if (config["n"], config["q"]) != (n, q):
        raise ValueError("Public parameters do not match online configuration")
    for key in ("w_f", "w_g"):
        if (not _integer(distribution[key]) or not 0 <= distribution[key] <= n
                or config[key] != distribution[key]):
            raise ValueError("Public weights do not match online configuration")
    for key in ("k", "kappa", "patterns_per_instance", "bkz_max_loops",
                "slicer_randomizations", "slicer_max_iterations"):
        if not _integer(config[key]) or config[key] < 0:
            raise ValueError(f"Invalid public parameter: {key}")
    if (config["k"] + config["kappa"] > n or not config["patterns_per_instance"]
            or not config["bkz_max_loops"] or config["slicer_randomizations"] < 2
            or not config["slicer_max_iterations"]):
        raise ValueError("Invalid public pattern or backend limits")
    if config.get("xi", 1) != 1 or config.get("threads", 1) != 1:
        raise ValueError("Phase two is unscaled and single-threaded")
    choices = config["choices"]
    if not isinstance(choices, list) or not choices:
        raise ValueError("At least one frozen backend choice is required")
    dimension = 2 * n - config["k"] - config["kappa"]
    for choice in choices:
        if (not isinstance(choice, dict) or set(choice) - {"name", "bkz_beta", "sieve_dim"}
                or not {"bkz_beta", "sieve_dim"} <= set(choice)
                or not _integer(choice["bkz_beta"]) or not 2 <= choice["bkz_beta"] <= dimension
                or not _integer(choice["sieve_dim"]) or not 1 <= choice["sieve_dim"] <= dimension):
            raise ValueError("Invalid frozen backend choice")
    if "plan_sha256" in config and (not isinstance(config["plan_sha256"], str)
            or len(config["plan_sha256"]) != 64
            or any(x not in "0123456789abcdef" for x in config["plan_sha256"])):
        raise ValueError("Invalid frozen plan SHA-256")


def paired_pattern(config: dict, seed: int, pattern_index: int) -> tuple[dict, int]:
    """Consume the identical RNG sequence for each choice and each public weight."""
    if not _integer(pattern_index) or not 0 <= pattern_index < config["patterns_per_instance"]:
        raise ValueError("Pattern index is outside the frozen plan")
    rng = random.Random(seed)
    for _ in range(pattern_index + 1):
        coordinates = partition(config["n"], rng.sample(range(config["n"]), config["k"]), config["kappa"])
        backend_seed = rng.randrange(1, 2 ** 31)
    return coordinates, backend_seed


def code_version() -> dict:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, text=True, capture_output=True)
    names = ("solve_phase2.py", "phase2_adapter.py", "slicer_adapter.py", "algebra.py")
    return {"git_head": head.stdout.strip(),
            "source_sha256": {name: file_sha(HERE / name) for name in names if (HERE / name).exists()}}


class WorkerInterrupted(BaseException):
    pass


def worker(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    cpu_started = time.process_time()
    public = json.loads(args.public.read_text())
    config = json.loads(args.config.read_text())
    validate_inputs(public, config)
    if not _integer(args.choice_index) or not 0 <= args.choice_index < len(config["choices"]):
        raise ValueError("Choice index is outside the frozen plan")
    coordinates, backend_seed = paired_pattern(config, args.seed, args.pattern_index)
    choice = config["choices"][args.choice_index]
    output = Path(args.output)
    event_path = output.with_suffix(".events.jsonl")
    if output.exists() or event_path.exists() or output == event_path:
        raise FileExistsError("Refusing to overwrite existing phase-two evidence")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects callers that bypass the runner.
    with output.open("x"):
        pass
    events_file = event_path.open("x", buffering=1)
    result = {"schema": "ntru-e2e-phase2-result-v1", "instance_id": public["instance_id"],
              "public_sha256": file_sha(args.public), "config_sha256": file_sha(args.config),
              "plan_sha256": config.get("plan_sha256"), "experiment_seed": args.seed,
              "pattern_index": args.pattern_index, "choice_index": args.choice_index,
              "configuration": choice, "backend_seed": backend_seed, "coordinates": coordinates,
              "parameters": {key: public[key] for key in ("n", "q", "distribution")},
              "code_version": code_version(), "started_utc": datetime.now(timezone.utc).isoformat(),
              "status": "running", "bases": {}, "events": [], "error": None,
              "excluded_zero_tail": True, "processed_guess_ids": [],
              "arms": {name: {"status": "not_run", "candidates": [], "seconds": None,
                               "operation_seconds": None, "method_setup_seconds": None,
                               "first_valid_seconds": None, "total_first_valid_seconds": None}
                       for name in METHODS},
              "timings": {}, "timing_scope": {
                  "seconds": "method_setup_seconds + operation_seconds; checkpoint I/O excluded",
                  "first_valid_seconds": "uniform construction + required LLL/BKZ/database setup + current arm through first public-valid candidate; other arms and checkpoint I/O excluded",
                  "total_first_valid_seconds": "actual worker wall time through first public-valid candidate, including earlier arms and checkpoints",
                  "setup_includes": "public matrix, basis and all target construction; decoder imports/initialization and reductions required by that arm",
                  "checkpoint_io_seconds": "JSONL serialization/flush and atomic JSON checkpoint serialization/write/replace"}}
    checkpoint_seconds = 0.0
    active_name = None
    arm_started = None
    seen = {name: set() for name in METHODS}
    decoder = None
    A = None

    def elapsed() -> float:
        return time.perf_counter() - started

    def clock() -> float:
        return time.perf_counter() - checkpoint_seconds

    def persist(event_value: dict | None = None) -> None:
        nonlocal checkpoint_seconds
        tick = time.perf_counter()
        if active_name is not None and arm_started is not None:
            arm = result["arms"][active_name]
            arm["operation_seconds"] = max(0.0, clock() - arm_started)
            arm["seconds"] = arm["method_setup_seconds"] + arm["operation_seconds"]
        result["wall_seconds"] = elapsed()
        result["cpu_seconds"] = time.process_time() - cpu_started
        result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["timings"]["checkpoint_io_seconds"] = checkpoint_seconds
        if decoder is not None:
            result["backend_metrics"] = dict(decoder.metrics)
        if event_value is not None:
            events_file.write(json.dumps(event_value, allow_nan=False) + "\n")
            events_file.flush()
        temporary = output.with_name(output.name + ".tmp")
        temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        temporary.replace(output)
        checkpoint_seconds += time.perf_counter() - tick

    def begin(name: str, setup_seconds: float) -> None:
        nonlocal active_name, arm_started
        active_name = name
        result["arms"][name].update(status="running", method_setup_seconds=setup_seconds)
        arm_started = clock()
        persist()

    def finish() -> None:
        nonlocal active_name, arm_started
        arm = result["arms"][active_name]
        arm["operation_seconds"] = max(0.0, clock() - arm_started)
        arm["seconds"] = arm["method_setup_seconds"] + arm["operation_seconds"]
        arm["status"] = "complete"
        event({"event": "arm_complete", "method": active_name})
        active_name = None
        arm_started = None
        persist()

    def accept(name: str, item: dict) -> bool:
        """Use one exact membership/radius/reconstruction/public-check pipeline."""
        n, q = public["n"], public["q"]
        P, C, T = (coordinates[key] for key in ("P", "C", "T"))
        dimension = n + len(C)
        target_id = item.get("target_id")
        if name.endswith("_rows"):
            if target_id is not None:
                raise ValueError("Row candidate unexpectedly has a target")
            target, guess = [0] * dimension, [0] * len(T)
            bound = config["w_f"] + config["w_g"]
        else:
            if not _integer(target_id) or not 0 <= target_id < len(result["targets"]):
                raise ValueError("Backend returned invalid external target ID")
            target, guess, bound = (result[key][target_id] for key in ("targets", "guesses", "bounds_sq"))
        vector, coefficients = item.get("v"), item.get("coefficients")
        if (not isinstance(vector, list) or len(vector) != dimension or any(not _integer(x) for x in vector)
                or not isinstance(coefficients, list) or len(coefficients) != dimension
                or any(not _integer(x) for x in coefficients)):
            raise ValueError("Backend returned malformed lattice vector or coefficients")
        if not lattice_member(vector, A, q, C):
            raise ValueError("Backend returned vector outside public lattice")
        if name == "direct_tail":
            if any(vector) or any(coefficients):
                raise ValueError("Direct-tail baseline must use the zero lattice vector")
        else:
            basis_name = "slicer" if name == "slicer" else name.split("_")[0]
            basis = result["bases"][basis_name]
            actual = [sum(coefficient * row[j] for coefficient, row in zip(coefficients, basis))
                      for j in range(dimension)]
            if actual != vector:
                raise ValueError("Backend coefficients do not reproduce lattice vector")
        residual_sq = sum((value - origin) ** 2 for value, origin in zip(vector, target))
        if residual_sq > bound:
            return False
        candidate = reconstruct(vector, target, P, C, T, guess, n)
        verified = validate_candidate(public, candidate["f"], candidate["g"])
        if not verified["valid"]:
            return False
        if residual_sq != bound:
            raise ValueError("Public-valid fixed-weight candidate has an inconsistent exact radius")
        identity = (target_id, tuple(vector))
        if identity in seen[name]:
            return True
        seen[name].add(identity)
        arm = result["arms"][name]
        arm["candidates"].append({**item, **candidate, "residual_sq": residual_sq, "validation": verified})
        if arm["first_valid_seconds"] is None:
            arm["first_valid_seconds"] = arm["method_setup_seconds"] + max(0.0, clock() - arm_started)
            arm["total_first_valid_seconds"] = elapsed()
        persist()
        return True

    def event(value: dict) -> None:
        record = dict(value)
        candidate = record.pop("candidate", None)
        record["worker_elapsed_seconds"] = elapsed()
        record["arm"] = active_name
        result["events"].append(record)
        if record.get("event") == "slicer_call_started":
            result["processed_guess_ids"] = sorted(set(result["processed_guess_ids"] + record.get("external_ids", [])))
        # The split adapter emits a full candidate only after its corresponding
        # kernel completion event, allowing safe partial recovery at timeout.
        if candidate is not None and active_name == "slicer":
            accept("slicer", candidate)
        persist(record)

    def rows_arm(name: str, setup_seconds: float, rows: list[list[int]]) -> None:
        begin(name, setup_seconds)
        for row_index, row in enumerate(rows):
            # Both signs are explicit baselines, including zero-tail recoveries.
            for sign in (1, -1):
                coefficients = [0] * len(rows)
                coefficients[row_index] = sign
                accept(name, {"target_id": None, "v": [sign * x for x in row],
                              "coefficients": coefficients, "row_index": row_index, "sign": sign})
        finish()

    def babai_arm(name: str, setup_seconds: float) -> None:
        begin(name, setup_seconds)
        for item in decoder.iter_babai(result["targets"], result["bounds_sq"], list(range(len(result["targets"])))):
            accept(name, item)
        finish()

    def interrupted(signum, _frame):
        raise WorkerInterrupted(f"Worker interrupted by signal {signum}")

    previous_handlers = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, interrupted)
    exit_code = 0
    try:
        persist()
        tick = clock()
        n, q = public["n"], public["q"]
        A = multiplication_matrix(public["h"], q)
        P, C, T = (coordinates[key] for key in ("P", "C", "T"))
        rows = basis_rows(A, q, C)
        tails = enumerate_tails(config["kappa"], config["w_f"])
        result["guesses"] = tails["guesses"]
        result["raw_nonzero_tail_count"] = tails["raw_nonzero"]
        result["targets"] = [make_target(A, T, guess, len(C), q=q) for guess in tails["guesses"]]
        result["bounds_sq"] = [config["w_f"] + config["w_g"] - sum(value != 0 for value in guess)
                               for guess in tails["guesses"]]
        result["dimension"] = len(rows)
        result["timings"]["construction_seconds"] = construction_seconds = clock() - tick
        event({"event": "pattern_constructed", "target_count": len(result["targets"])})

        begin("direct_tail", construction_seconds)
        for target_id in range(len(result["targets"])):
            accept("direct_tail", {"target_id": target_id, "v": [0] * len(rows), "coefficients": [0] * len(rows)})
        finish()

        # Import/setup and LLL are timed together: every reduction-based arm
        # requires these dependencies.  No sieve database is constructed here.
        result["stage"] = "lll_setup"
        persist()
        tick = clock()
        from .phase2_adapter import Phase2Decoder
        decoder = Phase2Decoder(rows, choice["bkz_beta"], choice["sieve_dim"], backend_seed,
                                event_callback=event, nrand=config["slicer_randomizations"],
                                max_iterations=config["slicer_max_iterations"],
                                bkz_max_loops=config["bkz_max_loops"])
        result["timings"]["lll_setup_seconds"] = lll_seconds = clock() - tick
        result["bases"]["lll"] = [list(row) for row in decoder.reduced_rows]
        rows_arm("lll_rows", construction_seconds + lll_seconds, result["bases"]["lll"])
        babai_arm("lll_babai", construction_seconds + lll_seconds)

        result["stage"] = "bkz_setup"
        persist()
        tick = clock()
        decoder.reduce_bkz()
        result["timings"]["bkz_setup_seconds"] = bkz_seconds = clock() - tick
        result["bases"]["bkz"] = [list(row) for row in decoder.reduced_rows]
        bkz_setup = construction_seconds + lll_seconds + bkz_seconds
        rows_arm("bkz_rows", bkz_setup, result["bases"]["bkz"])
        babai_arm("bkz_babai", bkz_setup)

        result["stage"] = "database_setup"
        persist()
        tick = clock()
        decoder.build_database()
        result["timings"]["database_setup_seconds"] = database_seconds = clock() - tick
        result["bases"]["slicer"] = [list(row) for row in decoder.reduced_rows]
        begin("slicer", bkz_setup + database_seconds)
        for item in decoder.decode(result["targets"], result["bounds_sq"], list(range(len(result["targets"])))):
            accept("slicer", item)
        finish()
        result["status"] = "complete"
        result["stage"] = "complete"
    except BaseException as exc:
        exit_code = 143 if isinstance(exc, WorkerInterrupted) else 1
        result["status"] = "interrupted" if isinstance(exc, WorkerInterrupted) else "error"
        result["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        if not isinstance(exc, WorkerInterrupted):
            traceback.print_exc()
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        persist()
        events_file.close()
        print(json.dumps({"instance_id": result["instance_id"], "status": result["status"],
                          "wall_seconds": result["wall_seconds"], "output": str(output),
                          "verified_candidates": {name: len(result["arms"][name]["candidates"]) for name in METHODS}}), flush=True)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--pattern-index", type=int, required=True)
    parser.add_argument("--choice-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return worker(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
