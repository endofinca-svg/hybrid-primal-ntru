"""Fresh public-input pattern searches with immutable 473.11*k budgets.

No reference secret is opened by this runner.  A hash-chained journal binds
every configuration and launch; resuming never retries an attempted pattern.
Run the independent summarize_budgeted module only after all groups terminate.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shutil
import signal
import sys
import time

from . import run as original


ROOT, HERE, EXPERIMENT = original.ROOT, original.HERE, original.EXPERIMENT
K_VALUES = [12, 24, 32, 38, 40, 42, 44, 48, 52, 56]
UNIT_SECONDS = 473.11
CLEANUP_RESERVE = original.CLEANUP_RESERVE
read, sha, utc = original.read_json, original.sha, original.utc


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def preserve(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False).encode() + b"\n")


def nonnegative(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError("Budget accounting must contain finite nonnegative numbers")
    return float(value)


def attempt_allowance(group, policy):
    cap = nonnegative(group["group_budget_seconds"])
    spent = nonnegative(group["charged_wall_seconds"])
    per_pattern = min(UNIT_SECONDS, nonnegative(policy["per_pattern_seconds"]))
    return max(0.0, min(cap - spent, per_pattern) - CLEANUP_RESERVE)


def next_pattern_index(attempts, k=None):
    return max((entry["index"] for entry in attempts), default=-1) + 1


def public_pattern(k, index):
    if k not in K_VALUES or not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("Invalid public pattern parameters")
    return sorted(original.paired_permutation(index)[0][:k])


def source_paths():
    return original.source_paths() + [Path(__file__).resolve()]


def append(run, state, event, **details):
    ledger = run / "ledger.jsonl"
    previous = None
    if ledger.stat().st_size:
        with ledger.open("rb") as handle:
            previous = json.loads(handle.readlines()[-1])["sha256"]
    state["updated_utc"] = utc()
    record = {"event": event, "recorded_utc": utc(), "previous_sha256": previous,
              "details": details, "state": copy.deepcopy(state)}
    record["sha256"] = digest(record)
    with ledger.open("ab") as handle:
        handle.write(encoded(record) + b"\n")
        handle.flush()
        import os
        os.fsync(handle.fileno())
    original.write_json(run / "run_manifest.json", state)


def journal_state(run):
    previous, latest = None, None
    for line in (run / "ledger.jsonl").read_bytes().splitlines():
        record = json.loads(line)
        recorded = record.pop("sha256")
        if record["previous_sha256"] != previous or digest(record) != recorded:
            raise ValueError("The append-only journal hash chain changed")
        previous, latest = recorded, record["state"]
    if latest is None:
        raise ValueError("A prepared run needs a journal")
    return latest


def policy():
    return {"schema": "ntru-fresh-pattern-budget-policy-v1", "k_values": K_VALUES,
            "total_seconds_per_k": UNIT_SECONDS, "per_pattern_seconds": UNIT_SECONDS,
            "cleanup_reserve_seconds": CLEANUP_RESERVE, "beta_stop": 256,
            "kappa": 0, "tail_enumeration": "T=[]; the sole hypothesis is the empty assignment",
            "target_count_per_pattern": 0, "slicer_calls_per_pattern": 0,
            "candidate_source": "All reduced basis rows after LLL and every complete BKZ tour",
            "pattern_seed": 10001, "solver_seed": 10001,
            "pattern_enumeration": "For index=0,1,... use SHA256('pattern-primal-v1:10001:'+str(index)) as Random seed, shuffle range(128), sorted first k positions. Skip duplicate sets.",
            "baseline_budget_seconds": None,
            "baseline_budget_interpretation": "Historical completed strict-two-tour baseline had no time cap; observed runtime is not a budget.",
            "keygen": "Reuse the saved n=128,q=97,(wf,wg)=(12,24) key, seed=1; uniform fixed-weight supports and independent signs; reject only noninvertible f; do not regenerate.",
            "restart": "User requested deletion of previous method2 results; all ten groups start at index0 with zero historical cost.",
            "online_stop": "First public-valid candidate; independent evaluate after all online groups finish decides joint signed negacyclic equivalence, without feeding reference information into online search.",
            "timing_scope": "Per-k sum of all subprocess wall times including startup, scans, checkpoints, failed/timeout cleanup plus online pattern/config/verification/launch overhead; excludes setup, tests, pauses, independent evaluate and the final provenance accounting commit itself.",
            "group_scheduling": "Sequential single-thread workers across k, in listed order; no parallel solver contention.",
            "budget_rule": "Never increase caps; timeout=min(473.11, remaining)-2.1s reserves SIGTERM/SIGKILL cleanup. A residual <=2.1s is recorded as budget blocked.",
            "bkz": "Same baseline parameters; beta30 step1, two complete tours unless public-valid success or hard timeout, clean does not skip tour2; max min(256,256-k)."}


def prepare(run, prefix=None):
    if prefix is not None:
        raise ValueError("This is a fresh run; importing historical attempts is prohibited")
    run = Path(run).resolve()
    public_path = EXPERIMENT / "public/instance_01.json"
    plan_path = EXPERIMENT / "plan_tour2.json"
    baseline_path = EXPERIMENT / "baseline_tour2/summary.json"
    historical = read(EXPERIMENT / "run_manifest_tour2.json")
    public, plan, baseline = read(public_path), read(plan_path), read(baseline_path)
    config = plan["baseline"]
    expected = {"n": 128, "q": 97, "w_f": 12, "w_g": 24, "kappa": 0, "threads": 1,
                "solver_seed": 10001, "bkz_start": 30, "bkz_jump": 1,
                "bkz_max_tours_per_beta": 2, "bkz_clean_stops_current_beta": False}
    if any(config.get(name) != value for name, value in expected.items()):
        raise ValueError("The baseline reduction protocol differs")
    if (sha(public_path) != historical["public_sha256"] or public["ring"] != "x^n+1"
            or public["n"] != 128 or public["q"] != 97 or public["distribution"]["w_f"] != 12
            or public["distribution"]["w_g"] != 24 or not baseline["audit_passed"]):
        raise ValueError("The original independently verified instance/baseline is required")
    run.mkdir(parents=True, exist_ok=False)
    (run / "provenance").mkdir()
    fixed_policy = policy()
    preserve(run / "policy.json", fixed_policy)
    inputs = {}
    for label, path in (("public", public_path), ("plan", plan_path), ("baseline_summary", baseline_path),
                        ("baseline_config", EXPERIMENT / "baseline_tour2/config.json"),
                        ("baseline_result", EXPERIMENT / "baseline_tour2/result.json"),
                        ("baseline_manifest", EXPERIMENT / "run_manifest_tour2.json")):
        target = run / "provenance" / (label + ".frozen.json")
        shutil.copyfile(path, target)
        inputs[label] = {"source": str(path.relative_to(ROOT)), "sha256": sha(path),
                         "frozen": str(target.relative_to(run))}
    sources = {}
    for path in source_paths():
        relative = str(path.relative_to(ROOT))
        target = run / "provenance/sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        sources[relative] = sha(path)
    runtime = ROOT / "experiments/ntru_e2e/.deps/runtime.json"
    environment = {"python": sys.version, "executable": sys.executable,
                   "runtime_configuration_sha256": sha(runtime)}
    preserve(run / "provenance/environment.json", environment)
    frozen = {"schema": "ntru-fresh-pattern-budget-frozen-v1", "prepared_utc": utc(),
              "policy_sha256": sha(run / "policy.json"), "inputs": inputs, "source_sha256": sources,
              "environment_sha256": sha(run / "provenance/environment.json"),
              "baseline_truth_sha256": historical["truth_sha256"]}
    preserve(run / "provenance/frozen_manifest.json", frozen)
    state = {"schema": "ntru-fresh-pattern-budget-run-v1", "status": "prepared",
             "prepared_utc": frozen["prepared_utc"], "resume_count": 0,
             "frozen_manifest_sha256": sha(run / "provenance/frozen_manifest.json"), "groups": []}
    for k in K_VALUES:
        state["groups"].append({"k": k, "dimension": 256-k, "status": "pending", "attempts": [],
                               "group_budget_seconds": UNIT_SECONDS*k, "charged_wall_seconds": 0.0,
                               "sampling_seconds": 0.0, "remaining_seconds": UNIT_SECONDS*k,
                               "budget_overrun_seconds": 0.0, "skipped_duplicate_indices": []})
    (run / "ledger.jsonl").touch(exist_ok=False)
    append(run, state, "prepared")
    return state


def verify(run):
    run = Path(run).resolve()
    state = journal_state(run)
    if read(run / "run_manifest.json") != state:
        raise ValueError("Run view differs from the hash-chained journal")
    frozen_path = run / "provenance/frozen_manifest.json"
    if sha(frozen_path) != state["frozen_manifest_sha256"]:
        raise ValueError("The frozen provenance changed")
    frozen = read(frozen_path)
    checks = {run / "policy.json": frozen["policy_sha256"],
              run / "provenance/environment.json": frozen["environment_sha256"]}
    for entry in frozen["inputs"].values():
        checks[ROOT / entry["source"]] = entry["sha256"]
        checks[run / entry["frozen"]] = entry["sha256"]
    for relative, expected in frozen["source_sha256"].items():
        checks[ROOT / relative] = expected
        checks[run / "provenance/sources" / relative] = expected
    for group in state["groups"]:
        if group["group_budget_seconds"] != UNIT_SECONDS*group["k"]:
            raise ValueError("A group budget changed")
        nonnegative(group["charged_wall_seconds"])
        indices = [attempt["index"] for attempt in group["attempts"]]
        if indices != sorted(set(indices)):
            raise ValueError("An attempted pattern index was replayed or reordered")
        if sum(nonnegative(a["charged_wall_seconds"]) for a in group["attempts"]) > group["charged_wall_seconds"] + 1e-6:
            raise ValueError("Group accounting omits worker costs")
        for attempt in group["attempts"]:
            if (attempt["reserved_charge_seconds"] > UNIT_SECONDS + 1e-6
                    or attempt["process_timeout_seconds"] > attempt["reserved_charge_seconds"] - CLEANUP_RESERVE + 1e-6):
                raise ValueError("An attempt exceeds the fixed per-pattern cap")
            checks[run / attempt["config"]] = attempt["config_sha256"]
            if "result_sha256" in attempt:
                checks[run / attempt["result"]] = attempt["result_sha256"]
            if "command_sha256" in attempt:
                checks[(run / attempt["command_prefix"]).with_suffix(".command.json")] = attempt["command_sha256"]
    for path, expected in checks.items():
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f"Frozen input or recorded evidence changed: {path}")
    if read(run / "policy.json") != policy():
        raise ValueError("Policy differs from the fixed requested caps/protocol")
    if [g["k"] for g in state["groups"]] != K_VALUES:
        raise ValueError("The ten requested groups must be present in fixed order")
    environment = read(run / "provenance/environment.json")
    if environment["python"] != sys.version or environment["executable"] != sys.executable:
        raise ValueError("Resume must use the same Python runtime")
    if sha(ROOT / "experiments/ntru_e2e/.deps/runtime.json") != environment["runtime_configuration_sha256"]:
        raise ValueError("Pinned native runtime changed")
    return state


def account(group, cost):
    group["charged_wall_seconds"] += nonnegative(cost)
    group["remaining_seconds"] = max(0.0, group["group_budget_seconds"] - group["charged_wall_seconds"])
    group["budget_overrun_seconds"] = max(0.0, group["charged_wall_seconds"] - group["group_budget_seconds"])


def finish_attempt(run, group, attempt, metadata, interrupted=False):
    evidence = original.result_evidence(run, attempt)
    attempt.update(evidence, command_status=metadata.get("status"), exit_code=metadata.get("exit_code"),
                   charged_wall_seconds=metadata["wall_seconds"], finished_utc=utc())
    attempt["status"] = ("success" if evidence.get("worker_status") == "success" else
                         "timeout" if metadata.get("status") == "timeout" else
                         "interrupted" if interrupted or metadata.get("status") == "interrupted" else
                         "exhausted" if evidence.get("worker_status") == "exhausted" else "error")
    command = (run / attempt["command_prefix"]).with_suffix(".command.json")
    attempt["command_sha256"] = sha(command)
    if (run / attempt["result"]).exists():
        attempt["result_sha256"] = sha(run / attempt["result"])
    account(group, attempt["charged_wall_seconds"])
    if attempt["status"] == "success":
        group.update(status="success", successful_pattern_index=attempt["index"],
                     first_success_beta=attempt.get("first_success_beta"),
                     first_success_tour=attempt.get("first_success_tour"))


def reconcile(run, state):
    # A launch is journaled before Popen; never launch that pattern twice.
    import os
    for group in state["groups"]:
        for attempt in group["attempts"]:
            if attempt["status"] != "running":
                continue
            command = (run / attempt["command_prefix"]).with_suffix(".command.json")
            metadata = read(command) if command.exists() else {}
            if metadata.get("status") == "running" and metadata.get("pid"):
                proc = Path(f"/proc/{metadata['pid']}/stat")
                if proc.exists() and proc.read_text().split()[21] == metadata.get("process_start_ticks"):
                    raise RuntimeError("A previous worker is still alive; refusing duplicate execution")
            if "wall_seconds" not in metadata:
                metadata.update(status="interrupted", wall_seconds=attempt["reserved_charge_seconds"],
                                accounting="Conservative full reserved charge after lost runner")
                original.write_json(command, metadata)
            finish_attempt(run, group, attempt, metadata, interrupted=True)
            append(run, state, "attempt_reconciled", k=group["k"], index=attempt["index"])


def execute(run):
    run = Path(run).resolve()
    # Lock avoids two runners racing the same budget and journal.
    lock = (run / "runner.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = verify(run)
    if state["status"] == "online_finished":
        lock.close()
        return state
    def interrupted(signum, _frame):
        raise original.RunnerInterrupted(f"Runner received signal {signum}")
    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        reconcile(run, state)
        state.update(status="online_running", resume_count=state["resume_count"] + 1)
        append(run, state, "runner_started")
        config = read(run / "provenance/plan.frozen.json")["baseline"]
        fixed_policy = read(run / "policy.json")
        for group in state["groups"]:
            while group["status"] not in {"success", "budget_exhausted"}:
                if attempt_allowance(group, fixed_policy) <= 0:
                    group.update(status="budget_exhausted", budget_blocked=True,
                                 failure_stage="remaining_budget_cannot_cover_cleanup_reserve")
                    append(run, state, "group_budget_exhausted", k=group["k"])
                    break
                tick = time.perf_counter()
                verify(run)
                index = max(next_pattern_index(group["attempts"]),
                            max(group["skipped_duplicate_indices"], default=-1)+1)
                pattern = public_pattern(group["k"], index)
                seen = {tuple(a["pattern"]) for a in group["attempts"]}
                if tuple(pattern) in seen:
                    group["skipped_duplicate_indices"].append(index)
                    account(group, time.perf_counter()-tick)
                    append(run, state, "duplicate_pattern_skipped", k=group["k"], index=index)
                    continue
                directory = run / f"k{group['k']:03d}" / f"pattern{index+1:03d}"
                directory.mkdir(parents=True, exist_ok=True)
                trial = dict(config, k=group["k"], pattern=pattern, pattern_index=index,
                             bkz_stop=min(256, group["dimension"]))
                config_path = directory / "config.json"
                if config_path.exists():
                    if read(config_path) != trial or any(p.name != "config.json" for p in directory.iterdir()):
                        raise FileExistsError("Existing unjournaled trial evidence cannot be replayed")
                else:
                    preserve(config_path, trial)
                elapsed = time.perf_counter()-tick
                group["sampling_seconds"] += elapsed
                account(group, elapsed)
                timeout = attempt_allowance(group, fixed_policy)
                if timeout <= 0:
                    group.update(status="budget_exhausted", budget_blocked=True,
                                 failure_stage="budget_exhausted_during_pattern_preparation")
                    append(run, state, "pattern_uncovered_budget", k=group["k"], index=index)
                    break
                attempt = {"index": index, "pattern": pattern, "source_run": str(run),
                           "config": str(config_path.relative_to(run)), "config_sha256": sha(config_path),
                           "result": str((directory / "result.json").relative_to(run)),
                           "command_prefix": f"provenance/k{group['k']:03d}_pattern{index+1:03d}_online",
                           "status": "running", "started_utc": utc(), "charged_wall_seconds": 0.0,
                           "reserved_charge_seconds": timeout+CLEANUP_RESERVE,
                           "process_timeout_seconds": timeout}
                group["attempts"].append(attempt)
                group["status"] = "running"
                append(run, state, "attempt_started", k=group["k"], index=index)
                command = [sys.executable, "-m", original.MODULE, "--public",
                           str(EXPERIMENT / "public/instance_01.json"), "--config", str(config_path),
                           "--output", str(run / attempt["result"])]
                # Charge launch journal/argv work, and subtract all setup from
                # both caps immediately before entering the worker capture.
                extra = max(0.0, time.perf_counter()-tick-elapsed)
                account(group, extra)
                timeout = max(0.0, min(attempt_allowance(group, fixed_policy),
                                        UNIT_SECONDS-elapsed-extra-CLEANUP_RESERVE))
                if timeout <= 0:
                    group["attempts"].pop()
                    group.setdefault("uncovered_patterns", []).append({"index": index,
                        "config": str(config_path.relative_to(run)), "reason": "budget_ended_before_launch"})
                    group.update(status="budget_exhausted", budget_blocked=True,
                                 failure_stage="budget_exhausted_before_worker_launch")
                    append(run, state, "pattern_uncovered_before_launch", k=group["k"], index=index)
                    break
                attempt["process_timeout_seconds"] = timeout
                print(json.dumps({"event": "attempt_started", "k": group["k"],
                                  "pattern_index": index, "timeout_seconds": timeout}), flush=True)
                metadata = original.capture_online(command, run / attempt["command_prefix"], timeout)
                tick = time.perf_counter()
                finish_attempt(run, group, attempt, metadata)
                account(group, time.perf_counter()-tick)
                append(run, state, "attempt_finished", k=group["k"], index=index)
                print(json.dumps({"event": "attempt_finished", "k": group["k"], "pattern_index": index,
                                  "status": attempt["status"], "total_seconds": group["charged_wall_seconds"],
                                  "first_success_beta": attempt.get("first_success_beta")}), flush=True)
                if metadata.get("status") == "interrupted":
                    raise original.RunnerInterrupted("Worker capture was interrupted")
        state.update(status="online_finished", finished_utc=utc())
        append(run, state, "online_finished")
    except original.RunnerInterrupted as error:
        # Discard any partially mutated, uncommitted accounting.  A completed
        # capture is reconciled once from its command evidence on resume.
        state = journal_state(run)
        state.update(status="interrupted", reason=str(error))
        append(run, state, "runner_interrupted")
    except BaseException as error:
        state.update(status="runner_error", reason=f"{type(error).__name__}: {error}")
        append(run, state, "runner_error")
        raise
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)
        lock.close()
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    run = args.run_dir.resolve()
    if args.resume:
        verify(run)
    else:
        prepare(run)
    state = read(run / "run_manifest.json") if args.prepare_only else execute(run)
    print(json.dumps({"status": state["status"], "run_dir": str(run)}, ensure_ascii=False), flush=True)
    return 0 if state["status"] in {"prepared", "online_finished"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
