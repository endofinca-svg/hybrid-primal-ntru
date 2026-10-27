"""Run the frozen three-instance acceptance without overwriting prior evidence.

From the repository root::

    python3 -m experiments.ntru_e2e.run_phase1 \
        --output-dir experiments/ntru_e2e/runs/reproduction

Each subprocess runs through run_in_env.py.  Only the separate evaluation
subprocesses receive private truth paths, after all online commands have ended.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from .record_command import capture


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
KEYGEN_SEEDS = (1, 2, 3)
ATTACK_SEEDS = (10001, 10002, 10003)
# This is inside, rather than additional to, each configured wall budget.
SUPERVISOR_ALLOWANCE_SECONDS = 5.0


def validate_config(config: dict) -> None:
    if config.get("schema") != "ntru-e2e-phase1-config-v1":
        raise ValueError("unsupported phase-one configuration schema")
    if tuple(config.get(key) for key in ("n", "q", "w_f", "w_g", "xi", "threads")) != (
        32, 257, 2, 4, 1, 1
    ):
        raise ValueError("phase one requires n=32, q=257, weights 2/4, xi=1 and one thread")
    if config.get("experiment_seeds") != list(ATTACK_SEEDS):
        raise ValueError("phase one uses attack seeds 10001, 10002 and 10003")
    for key, maximum in (("instance_timeout_seconds", 300), ("total_timeout_seconds", 1800)):
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a positive finite number <= {maximum}")
        if not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError(f"{key} must be a positive finite number <= {maximum}")


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def _command(module: str, *arguments: object) -> list[str]:
    return [sys.executable, str(HERE / "run_in_env.py"), "-m",
            f"experiments.ntru_e2e.{module}", *map(str, arguments)]


def _missing_result(public_path: Path, output_path: Path, seed: int, status: str,
                    reason: str, config_path: Path) -> None:
    """Record absence explicitly; this contains no invented decoder evidence."""
    public_bytes = public_path.read_bytes()
    public = json.loads(public_bytes)
    _write_json(output_path, {
        "schema": "ntru-e2e-result-v1",
        "instance_id": public["instance_id"],
        "public_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "experiment_seed": seed,
        "status": status,
        "recovered": None,
        "trials": [],
        "error": reason,
        "runner_placeholder": True,
    })


def run_phase1(output_dir: Path, config_path: Path) -> dict:
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    validate_config(config)
    output_dir = output_dir.resolve()
    # Atomic refusal also protects partially completed earlier runs.
    output_dir.mkdir(parents=True, exist_ok=False)
    frozen_config = output_dir / "config_phase1.json"
    frozen_config.write_bytes(config_bytes)
    summary_path = output_dir / "summary.json"
    started = time.perf_counter()
    summary = {
        "schema": "ntru-e2e-phase1-run-v1",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "keygen_seeds": list(KEYGEN_SEEDS),
        "attack_seeds": list(ATTACK_SEEDS),
        "instance_budget_seconds": config["instance_timeout_seconds"],
        "total_online_budget_seconds": config["total_timeout_seconds"],
        "supervisor_allowance_seconds": SUPERVISOR_ALLOWANCE_SECONDS,
        "budget_scope": "sum of recorded online command wall times, including supervisor cleanup",
        "online_wall_seconds": 0.0,
        "status": "running",
        "instances": [],
    }

    def persist() -> None:
        summary["wall_seconds"] = time.perf_counter() - started
        _write_json(summary_path, summary)

    persist()
    generation = capture(
        _command("generate", "--output-dir", output_dir,
                 "--n", config["n"], "--q", config["q"],
                 "--wf", config["w_f"], "--wg", config["w_g"],
                 "--seeds", *KEYGEN_SEEDS),
        output_dir / "commands" / "generate", cwd=REPOSITORY, timeout=60,
    )
    summary["generation"] = generation
    persist()
    for index, (keygen_seed, attack_seed) in enumerate(zip(KEYGEN_SEEDS, ATTACK_SEEDS), 1):
        instance_id = f"instance_{index:02d}"
        public_path = output_dir / "public" / f"{instance_id}.json"
        result_path = output_dir / f"{instance_id}.result.json"
        record = {
            "instance_id": instance_id, "keygen_seed": keygen_seed,
            "attack_seed": attack_seed, "public": str(public_path),
            "result": str(result_path), "online_command": None,
        }
        summary["instances"].append(record)
        remaining = config["total_timeout_seconds"] - summary["online_wall_seconds"]
        command_budget = min(config["instance_timeout_seconds"], remaining)
        if generation["exit_code"] != 0 or not public_path.exists():
            record.update(status="not_run_generation_failed", reason="public generation did not complete")
        elif command_budget <= SUPERVISOR_ALLOWANCE_SECONDS:
            record.update(status="not_run_budget", reason="insufficient online budget after supervisor allowance")
        else:
            online_limit = command_budget - SUPERVISOR_ALLOWANCE_SECONDS
            record["online_limit_seconds"] = online_limit
            print(f"{instance_id}: online seed {attack_seed}, limit {online_limit:.3f}s", flush=True)
            metadata = capture(
                _command("attack", "--public", public_path, "--config", frozen_config,
                         "--seed", attack_seed, "--output", result_path,
                         "--time-limit", online_limit),
                output_dir / "commands" / f"{instance_id}_attack",
                cwd=REPOSITORY, timeout=command_budget,
            )
            record["online_command"] = metadata
            summary["online_wall_seconds"] += metadata["wall_seconds"]
            record["status"] = "timeout" if metadata["status"] == "timeout" else (
                "finished" if metadata["exit_code"] == 0 else "command_failed")
            if result_path.exists():
                try:
                    result = json.loads(result_path.read_text())
                    record["online_result_status"] = result.get("status", "unknown")
                except (ValueError, AttributeError):
                    record["online_result_status"] = "unreadable_result"
        if public_path.exists() and not result_path.exists():
            if record["status"] == "finished":
                record["status"] = "missing_result"
            _missing_result(public_path, result_path, attack_seed, record["status"],
                            record.get("reason", "online command produced no result checkpoint"),
                            frozen_config)
        persist()

    # Evaluate only after the entire online run.  Evaluation never changes search.
    for record in summary["instances"]:
        instance_id = record["instance_id"]
        truth_path = output_dir / "private" / f"{instance_id}.truth.json"
        evaluation_path = output_dir / f"{instance_id}.evaluation.json"
        record["evaluation"] = str(evaluation_path)
        if not all(Path(path).exists() for path in (record["public"], record["result"], truth_path)):
            record["evaluation_status"] = "not_run_missing_input"
        else:
            metadata = capture(
                _command("evaluate", "--public", record["public"], "--truth", truth_path,
                         "--result", record["result"], "--output", evaluation_path),
                output_dir / "commands" / f"{instance_id}_evaluate",
                cwd=REPOSITORY, timeout=60,
            )
            record["evaluation_command"] = metadata
            record["evaluation_status"] = "finished" if metadata["exit_code"] == 0 else "command_failed"
            if metadata["exit_code"] == 0 and evaluation_path.exists():
                evaluation = json.loads(evaluation_path.read_text())
                record.update({key: evaluation.get(key) for key in (
                    "classification", "end_to_end_passed", "failure_category")})
        persist()

    summary["online_acceptance_passed"] = any(
        record.get("end_to_end_passed") is True for record in summary["instances"])
    summary["workflow_complete"] = generation["exit_code"] == 0 and all(
        record.get("evaluation_status") == "finished" for record in summary["instances"])
    summary["status"] = "finished" if summary["workflow_complete"] else "incomplete"
    summary["finished_utc"] = datetime.now(timezone.utc).isoformat()
    persist()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new run directory; an existing directory is always refused")
    parser.add_argument("--config", type=Path, default=HERE / "config_phase1.json")
    args = parser.parse_args(argv)
    try:
        summary = run_phase1(args.output_dir, args.config)
    except (FileExistsError, FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({"summary": str(args.output_dir.resolve() / "summary.json"),
                      "workflow_complete": summary["workflow_complete"],
                      "online_acceptance_passed": summary["online_acceptance_passed"],
                      "online_wall_seconds": summary["online_wall_seconds"]}, indent=2))
    if not summary["workflow_complete"]:
        return 1
    return 0 if summary["online_acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
