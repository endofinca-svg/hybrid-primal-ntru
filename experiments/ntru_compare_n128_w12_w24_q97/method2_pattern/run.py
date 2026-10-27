"""Freeze and run public, paired zero-pattern primal experiments.

Use --prepare-only to freeze the complete schedule without starting reduction;
continue it with --resume.  This runner never opens or selects using truth.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import signal
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
ROOT = HERE.parents[2]
DEFAULT_K = [12, 24, 32, 40, 44, 48, 52, 56]
DEFAULT_BUDGET = 473.106938175
CLEANUP_RESERVE = 2.1
MODULE = "experiments.ntru_compare_n128_w12_w24_q97.method2_pattern.solve"


class RunnerInterrupted(Exception):
    """A controlled runner interruption, after child cleanup and accounting."""


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def paired_permutation(index, n=128):
    """Public, domain-separated pattern randomness; no key-dependent input."""
    material = f"pattern-primal-v1:10001:{index}"
    digest = hashlib.sha256(material.encode("ascii")).digest()
    permutation = list(range(n))
    random.Random(int.from_bytes(digest, "big")).shuffle(permutation)
    return permutation, material, digest.hex()


def source_paths():
    paths = [HERE / name for name in ("__init__.py", "solve.py", "run.py")]
    paths += [EXPERIMENT / "__init__.py"]
    paths += [ROOT / "experiments/ntru_e2e" / name
              for name in ("algebra.py", "record_command.py")]
    for optional in (ROOT / "experiments/__init__.py", ROOT / "experiments/ntru_e2e/__init__.py"):
        if optional.exists():
            paths.append(optional)
    return paths


def prepare(run, policy):
    """Create immutable inputs before any online worker may start."""
    plan_path = EXPERIMENT / "plan_tour2.json"
    public_path = EXPERIMENT / "public/instance_01.json"
    baseline_path = EXPERIMENT / "baseline_tour2/summary.json"
    plan, public, baseline = (read_json(path) for path in (plan_path, public_path, baseline_path))
    config = plan["baseline"]
    expected = {"n": 128, "q": 97, "w_f": 12, "w_g": 24,
                "kappa": 0, "threads": 1, "solver_seed": 10001,
                "bkz_start": 30, "bkz_jump": 1, "bkz_max_tours_per_beta": 2,
                "bkz_clean_stops_current_beta": False}
    if any(config.get(name) != value for name, value in expected.items()):
        raise ValueError("Saved baseline configuration differs from the required comparison")
    if (public.get("n") != 128 or public.get("q") != 97 or public.get("ring") != "x^n+1"
            or any(public.get("distribution", {}).get(name) != expected[name]
                   for name in ("w_f", "w_g"))):
        raise ValueError("Expected the original n=128, q=97, weights=(12,24) public instance")
    paths = source_paths()
    if any(not path.is_file() for path in paths):
        raise FileNotFoundError("Finish all online source files before freezing the schedule")
    historical = read_json(EXPERIMENT / "run_manifest_tour2.json")
    if sha(public_path) != historical["public_sha256"]:
        raise ValueError("Public instance differs from the frozen no-pattern baseline")
    if baseline.get("first_success_beta") != 35 or not baseline.get("audit_passed"):
        raise ValueError("A verified strict two-tour plain-primal baseline is required")
    run.mkdir(parents=True, exist_ok=False)
    provenance = run / "provenance"
    provenance.mkdir()
    frozen_inputs = {}
    for label, path in (("plan", plan_path), ("public", public_path), ("baseline_summary", baseline_path)):
        target = provenance / (label + ".frozen.json")
        shutil.copyfile(path, target)
        frozen_inputs[label] = {"source": str(path.relative_to(ROOT)),
                                "sha256": sha(path), "frozen": str(target.relative_to(run))}
    sources = {}
    for path in paths:
        relative = path.relative_to(ROOT)
        target = provenance / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        sources[str(relative)] = sha(path)
    environment = {"python": sys.version, "executable": sys.executable,
                   "platform": platform.platform(), "prepared_utc": utc()}
    runtime = ROOT / "experiments/ntru_e2e/.deps/runtime.json"
    if runtime.exists():
        environment["runtime_configuration_sha256"] = sha(runtime)
    write_json(provenance / "environment.json", environment)
    schedule = {"schema": "ntru-pattern-primal-schedule-v1", "policy": policy,
                "public_source": str(public_path.relative_to(ROOT)), "groups": [],
                "pattern_generator": "Random(int.from_bytes(SHA256('pattern-primal-v1:10001:'+str(index)), 'big')).shuffle(range(128)); zero-based index; sorted first k coordinates",
                "paired_pattern_rule": "For each attempt index, all k use prefixes of the same public permutation.",
                "sampling_charge_rule": "Each k is charged the full public permutation-generation time and its own prefix construction for all prescheduled patterns; unused schedule sampling is included.",
                "timing_scope": "Sum of public sampling and actual subprocess wall times, including every failed attempt and timeout cleanup; source freezing, offline audits and pauses are excluded.",
                "truth_access": "No private file is opened; truth hash, if needed, comes only from the pre-existing baseline manifest.",
                "baseline": {"online_total_wall_seconds": baseline["online_total_wall_seconds"],
                             "first_success_beta": baseline["first_success_beta"],
                             "first_success_tour": baseline["first_success_tour"]}}
    permutations = []
    for index in range(policy["max_patterns"]):
        tick = time.perf_counter()
        permutation, material, digest = paired_permutation(index)
        permutations.append((permutation, material, digest, time.perf_counter() - tick))
    config_hashes = {}
    groups = []
    for k in policy["k_values"]:
        frozen_group = {"k": k, "dimension": 256-k, "patterns": [], "sampling_seconds": 0.0}
        group = {"k": k, "dimension": 256-k, "status": "pending", "attempts": [],
                 "charged_wall_seconds": 0.0, "group_budget_seconds": policy["group_budget_seconds"]}
        for index, (permutation, material, digest, permutation_seconds) in enumerate(permutations):
            tick = time.perf_counter()
            pattern = sorted(permutation[:k])
            sampling_seconds = permutation_seconds + time.perf_counter() - tick
            directory = run / f"k{k:03d}" / f"pattern{index+1:03d}"
            directory.mkdir(parents=True)
            trial_config = dict(config)
            trial_config.update(k=k, pattern=pattern, pattern_index=index, kappa=0,
                                bkz_stop=min(policy["beta_stop"], 256-k))
            config_path = directory / "config.json"
            write_json(config_path, trial_config)
            relative_config = str(config_path.relative_to(run))
            config_hashes[relative_config] = sha(config_path)
            entry = {"index": index, "pattern_number": index+1, "pattern": pattern,
                     "seed_material": material, "seed_sha256": digest,
                     "sampling_seconds": sampling_seconds, "config": relative_config,
                     "config_sha256": config_hashes[relative_config],
                     "result": str((directory / "result.json").relative_to(run)),
                     "command_prefix": f"provenance/k{k:03d}_pattern{index+1:03d}_online"}
            frozen_group["patterns"].append(entry)
            frozen_group["sampling_seconds"] += sampling_seconds
            group["attempts"].append({"index": index, "status": "scheduled",
                                      "charged_wall_seconds": 0.0,
                                      "unused_reason": "not_started"})
        group["sampling_seconds"] = frozen_group["sampling_seconds"]
        group["charged_wall_seconds"] = frozen_group["sampling_seconds"]
        group["remaining_seconds"] = max(0.0, policy["group_budget_seconds"] - group["charged_wall_seconds"])
        schedule["groups"].append(frozen_group)
        groups.append(group)
    write_json(run / "schedule.json", schedule)
    frozen = {"schema": "ntru-pattern-primal-frozen-v1", "frozen_utc": utc(),
              "inputs": frozen_inputs, "source_sha256": sources,
              "config_sha256": config_hashes, "schedule_sha256": sha(run / "schedule.json"),
              "baseline_truth_sha256": historical.get("truth_sha256"),
              "environment_sha256": sha(provenance / "environment.json")}
    write_json(provenance / "frozen_manifest.json", frozen)
    state = {"schema": "ntru-pattern-primal-run-v1", "status": "prepared", "prepared_utc": utc(),
             "frozen_manifest_sha256": sha(provenance / "frozen_manifest.json"),
             "groups": groups, "resume_count": 0,
             "scope": "Method 2, reused public key, zero constraints only, no tail guessing; public-valid stopping, post-hoc truth audit separate."}
    write_json(run / "run_manifest.json", state)
    return schedule, state


def verify_frozen(run, state):
    manifest_path = run / "provenance/frozen_manifest.json"
    if sha(manifest_path) != state["frozen_manifest_sha256"]:
        raise ValueError("Frozen provenance manifest changed")
    frozen = read_json(manifest_path)
    checks = {run / "schedule.json": frozen["schedule_sha256"],
              run / "provenance/environment.json": frozen["environment_sha256"]}
    for item in frozen["inputs"].values():
        checks[ROOT / item["source"]] = item["sha256"]
        checks[run / item["frozen"]] = item["sha256"]
    for relative, digest in frozen["source_sha256"].items():
        checks[ROOT / relative] = digest
        checks[run / "provenance/sources" / relative] = digest
    for relative, digest in frozen["config_sha256"].items():
        checks[run / relative] = digest
    changed = [str(path) for path, digest in checks.items() if not path.is_file() or sha(path) != digest]
    if changed:
        raise ValueError("Frozen input/source changed: " + ", ".join(changed))
    environment = read_json(run / "provenance/environment.json")
    if environment["executable"] != sys.executable or environment["python"] != sys.version:
        raise ValueError("Resume must use the same Python executable and version")
    runtime = ROOT / "experiments/ntru_e2e/.deps/runtime.json"
    if environment.get("runtime_configuration_sha256") and sha(runtime) != environment["runtime_configuration_sha256"]:
        raise ValueError("Pinned runtime configuration changed")


def stop_process(process):
    if process.poll() is not None:
        return process.returncode
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return process.wait()
    try:
        return process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return process.wait()


def capture_online(command, prefix, timeout):
    """Capture the worker with two-second cleanup and interruption evidence."""
    paths = {name: prefix.with_suffix(suffix) for name, suffix in (
        ("metadata", ".command.json"), ("stdout", ".stdout.txt"), ("stderr", ".stderr.txt"))}
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Preserving prior command evidence: " + str(prefix))
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    tick = time.perf_counter()
    metadata = {"argv": [str(value) for value in command], "cwd": str(ROOT),
                "started_utc": utc(), "timeout_seconds": timeout, "status": "running",
                "stdout": paths["stdout"].name, "stderr": paths["stderr"].name}
    write_json(paths["metadata"], metadata)
    process = None
    try:
        with paths["stdout"].open("x") as stdout, paths["stderr"].open("x") as stderr:
            process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            metadata["pid"] = process.pid
            metadata["process_start_ticks"] = Path(f"/proc/{process.pid}/stat").read_text().split()[21]
            write_json(paths["metadata"], metadata)
            try:
                code = process.wait(timeout=timeout)
                metadata.update(status="finished", exit_code=code)
            except subprocess.TimeoutExpired:
                metadata.update(status="timeout", exit_code=stop_process(process))
    except RunnerInterrupted as error:
        metadata.update(status="interrupted", error=str(error),
                        exit_code=stop_process(process) if process else None)
    except Exception as error:
        metadata.update(status="error", error=f"{type(error).__name__}: {error}",
                        exit_code=stop_process(process) if process else None)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    metadata.update(wall_seconds=time.perf_counter()-tick,
                    cpu_seconds=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
                    peak_rss_kib=after.ru_maxrss, finished_utc=utc())
    write_json(paths["metadata"], metadata)
    return metadata


def result_evidence(run, entry):
    path = run / entry["result"]
    if not path.exists():
        return {"result_present": False}
    try:
        result = read_json(path)
    except (ValueError, OSError) as error:
        return {"result_present": True, "result_read_error": str(error)}
    stages = result.get("stages", [])
    last = stages[-1] if stages else {}
    return {"result_present": True, "worker_status": result.get("status"),
            "current_stage": result.get("current_stage"), "completed_stage_count": len(stages),
            "last_completed_stage": last.get("stage_id"), "last_completed_beta": last.get("beta"),
            "last_completed_tour": last.get("tour"), "first_success_beta": result.get("first_success_beta"),
            "first_success_tour": result.get("first_success_tour"),
            "first_success_stage": result.get("first_success_stage"),
            "worker_first_valid_seconds": result.get("first_valid_seconds")}


def reconcile_running(run, schedule, state):
    """Never replay a worker whose previous launch may already have run."""
    for frozen_group, group in zip(schedule["groups"], state["groups"]):
        for entry, attempt in zip(frozen_group["patterns"], group["attempts"]):
            if attempt["status"] != "running":
                continue
            path = (run / entry["command_prefix"]).with_suffix(".command.json")
            metadata = read_json(path) if path.exists() else {}
            if metadata.get("status") == "running" and metadata.get("pid"):
                proc = Path(f"/proc/{metadata['pid']}/stat")
                if proc.exists() and proc.read_text().split()[21] == metadata.get("process_start_ticks"):
                    raise RuntimeError(f"Prior worker PID {metadata['pid']} is still running; refusing duplicate execution")
            if isinstance(metadata.get("wall_seconds"), (int, float)):
                charged = metadata["wall_seconds"]
                accounting = "recovered_completed_capture"
            else:
                charged = attempt["reserved_charge_seconds"]
                accounting = "conservative_full_reserved_charge_after_runner_interruption"
            attempt.update(status="interrupted", charged_wall_seconds=charged,
                           accounting=accounting, reconciled_utc=utc(), **result_evidence(run, entry))
            if attempt.get("worker_status") == "success":
                attempt["status"] = "success"
            group["charged_wall_seconds"] += charged
            group["remaining_seconds"] = max(0.0, group["group_budget_seconds"]-group["charged_wall_seconds"])
            if attempt["status"] == "success":
                group.update(status="success", successful_pattern_index=entry["index"],
                             first_success_beta=attempt.get("first_success_beta"),
                             first_success_tour=attempt.get("first_success_tour"))
                mark_unused(group, "prior_attempt_success")


def mark_unused(group, reason):
    for attempt in group["attempts"]:
        if attempt["status"] == "scheduled":
            attempt.update(status="unused", unused_reason=reason)


def execute(run, schedule, state):
    def save():
        state["updated_utc"] = utc()
        write_json(run / "run_manifest.json", state)

    def interrupted(signum, _frame):
        raise RunnerInterrupted(f"Runner received signal {signum}")

    previous = {number: signal.signal(number, interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        reconcile_running(run, schedule, state)
        state.update(status="online_running", last_started_utc=utc())
        save()
        for frozen_group, group in zip(schedule["groups"], state["groups"]):
            if group["status"] in ("success", "budget_exhausted", "exhausted"):
                continue
            group["status"] = "running"
            for entry, attempt in zip(frozen_group["patterns"], group["attempts"]):
                if attempt["status"] != "scheduled":
                    continue
                remaining = group["group_budget_seconds"] - group["charged_wall_seconds"]
                per_pattern = schedule["policy"]["per_pattern_seconds"]
                allowance = min(remaining, per_pattern) if per_pattern is not None else remaining
                if allowance <= CLEANUP_RESERVE:
                    group["status"] = "budget_exhausted"
                    mark_unused(group, "insufficient_budget_for_cleanup_reserve")
                    save()
                    break
                verify_frozen(run, state)
                result_path = run / entry["result"]
                prefix = run / entry["command_prefix"]
                if result_path.exists() or prefix.with_suffix(".command.json").exists():
                    raise FileExistsError(f"Unaccounted existing trial evidence: {result_path.parent}")
                command = [sys.executable, "-m", MODULE,
                           "--public", str(ROOT / schedule["public_source"]),
                           "--config", str(run / entry["config"]), "--output", str(result_path)]
                attempt.update(status="running", started_utc=utc(), unused_reason=None,
                               reserved_charge_seconds=allowance,
                               process_timeout_seconds=allowance-CLEANUP_RESERVE,
                               charged_before_attempt_seconds=group["charged_wall_seconds"])
                save()
                print(json.dumps({"event": "attempt_started", "k": group["k"],
                                  "pattern_index": entry["index"], "timeout_seconds": allowance-CLEANUP_RESERVE}), flush=True)
                metadata = capture_online(command, prefix, allowance-CLEANUP_RESERVE)
                charged = metadata["wall_seconds"]
                attempt.update(charged_wall_seconds=charged, finished_utc=utc(),
                               command_status=metadata["status"], exit_code=metadata.get("exit_code"),
                               **result_evidence(run, entry))
                attempt["status"] = ("success" if attempt.get("worker_status") == "success" else
                                     "timeout" if metadata["status"] == "timeout" else
                                     "interrupted" if metadata["status"] == "interrupted" else
                                     "exhausted" if attempt.get("worker_status") == "exhausted" else
                                     "error" if metadata["status"] == "error" or metadata.get("exit_code") != 0 else "exhausted")
                group["charged_wall_seconds"] += charged
                group["remaining_seconds"] = max(0.0, group["group_budget_seconds"]-group["charged_wall_seconds"])
                group["budget_overrun_seconds"] = max(0.0, group["charged_wall_seconds"]-group["group_budget_seconds"])
                print(json.dumps({"event": "attempt_finished", "k": group["k"],
                                  "pattern_index": entry["index"], "status": attempt["status"],
                                  "charged_wall_seconds": charged, "group_total_seconds": group["charged_wall_seconds"],
                                  "last_completed_beta": attempt.get("last_completed_beta")}), flush=True)
                if attempt["status"] == "success":
                    group.update(status="success", successful_pattern_index=entry["index"],
                                 first_success_beta=attempt.get("first_success_beta"),
                                 first_success_tour=attempt.get("first_success_tour"))
                    mark_unused(group, "prior_attempt_success")
                    save()
                    break
                save()
                if metadata["status"] == "interrupted":
                    raise RunnerInterrupted(metadata.get("error", "Worker interrupted"))
            if group["status"] == "running":
                group["status"] = "exhausted"
                save()
        state.update(status="online_finished", finished_utc=utc())
        save()
    except RunnerInterrupted as error:
        state.update(status="interrupted", interruption=str(error), interrupted_utc=utc())
        for group in state["groups"]:
            if group["status"] == "running":
                group["status"] = "interrupted"
            for attempt in group["attempts"]:
                if attempt["status"] == "scheduled":
                    attempt["unused_reason"] = "runner_interrupted_resume_allowed"
        save()
    except Exception as error:
        state.update(status="runner_error", error=f"{type(error).__name__}: {error}")
        save()
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return state


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True, help="New path below method2_pattern, e.g. runs/pattern01")
    parser.add_argument("--max-patterns", type=int, default=1)
    parser.add_argument("--group-budget-seconds", type=float, default=DEFAULT_BUDGET)
    parser.add_argument("--per-pattern-seconds", type=float)
    parser.add_argument("--beta-stop", type=int, default=256)
    parser.add_argument("--k-values", type=int, nargs="+", default=DEFAULT_K)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    run = (HERE / args.run_name).resolve()
    if HERE not in run.parents:
        parser.error("--run-name must be a directory below method2_pattern")
    policy = {"max_patterns": args.max_patterns, "group_budget_seconds": args.group_budget_seconds,
              "per_pattern_seconds": args.per_pattern_seconds, "beta_stop": args.beta_stop,
              "k_values": args.k_values, "cleanup_reserve_seconds": CLEANUP_RESERVE}
    if args.resume:
        state = read_json(run / "run_manifest.json")
        verify_frozen(run, state)
        schedule = read_json(run / "schedule.json")
        for flag, name in (("--max-patterns", "max_patterns"), ("--group-budget-seconds", "group_budget_seconds"),
                           ("--per-pattern-seconds", "per_pattern_seconds"), ("--beta-stop", "beta_stop"),
                           ("--k-values", "k_values")):
            if any(token == flag or token.startswith(flag+"=") for token in argv) and policy[name] != schedule["policy"][name]:
                parser.error(f"{flag} differs from the frozen schedule")
        state["resume_count"] += 1
        write_json(run / "run_manifest.json", state)
    else:
        if args.max_patterns < 1:
            parser.error("--max-patterns must be positive")
        if args.max_patterns > 1 and args.per_pattern_seconds is None:
            parser.error("Multiple patterns require an explicit --per-pattern-seconds")
        if (not math.isfinite(args.group_budget_seconds) or args.group_budget_seconds <= CLEANUP_RESERVE
                or args.per_pattern_seconds is not None and (not math.isfinite(args.per_pattern_seconds)
                                                           or args.per_pattern_seconds <= CLEANUP_RESERVE)):
            parser.error("All budgets must be finite and exceed the 2.1-second cleanup reserve")
        if not 30 <= args.beta_stop <= 256:
            parser.error("--beta-stop must be between 30 and 256")
        if (not args.k_values or len(set(args.k_values)) != len(args.k_values)
                or any(not 0 < k < 128 for k in args.k_values)):
            parser.error("--k-values must be distinct integers between 1 and 127")
        schedule, state = prepare(run, policy)
    if not args.prepare_only:
        state = execute(run, schedule, state)
    print(json.dumps({"status": state["status"], "manifest": str(run / "run_manifest.json"),
                      "groups": [{"k": group["k"], "status": group["status"],
                                  "total_seconds": group["charged_wall_seconds"],
                                  "first_success_beta": group.get("first_success_beta")}
                                 for group in state["groups"]]}), flush=True)
    return 130 if state["status"] == "interrupted" else 0


if __name__ == "__main__":
    raise SystemExit(main())
