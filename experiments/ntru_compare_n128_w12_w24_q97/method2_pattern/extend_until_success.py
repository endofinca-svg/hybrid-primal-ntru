"""Continue a completed pattern-search prefix until every k recovers a key.

The public policy, prefix and executable sources are frozen before continuation.
An append-only hash chain records every new configuration, launch and outcome;
schedule.json and run_manifest.json are reconstructible views of that ledger.
No private truth is opened.  Relative --prefix-run paths are relative to the new
run directory, for example --prefix-run ../paired_patterns_473s.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import signal
import sys
import time

from . import run as original


HERE, ROOT, EXPERIMENT = original.HERE, original.ROOT, original.EXPERIMENT
DEFAULT_K, CLEANUP_RESERVE = original.DEFAULT_K, original.CLEANUP_RESERVE
read_json, write_json, sha, utc = original.read_json, original.write_json, original.sha, original.utc
RunnerInterrupted = original.RunnerInterrupted


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def pattern_digest(pattern):
    return digest(sorted(pattern))


def preserve_json(path, value):
    """Permit reuse of a pre-launch orphan only if its exact input is unchanged."""
    path = Path(path)
    data = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"Preserving different existing evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def pattern_budget(policy, index):
    base, cap, block = (policy[name] for name in
                        ("base_pattern_seconds", "max_pattern_seconds", "budget_block"))
    level = index // block
    saturation = max(0, math.ceil(math.log2(cap / base)))
    return float(cap if level >= saturation else min(cap, base * 2**level))


def _prefix_gate(prefix):
    state = read_json(prefix / "run_manifest.json")
    original.verify_frozen(prefix, state)
    schedule = read_json(prefix / "schedule.json")
    if (state.get("status") != "online_finished" or len(state.get("groups", [])) != 8
            or [g.get("k") for g in state["groups"]] != DEFAULT_K
            or [g.get("k") for g in schedule.get("groups", [])] != DEFAULT_K):
        raise ValueError("The complete first-phase online search must finish before continuation is frozen")
    for group, frozen in zip(state["groups"], schedule["groups"]):
        if group.get("status") not in {"success", "budget_exhausted", "exhausted"}:
            raise ValueError("The prefix contains a nonterminal group")
        if len(group["attempts"]) != len(frozen["patterns"]):
            raise ValueError("The prefix inventory is inconsistent")
        for attempt, entry in zip(group["attempts"], frozen["patterns"]):
            if (attempt.get("index") != entry.get("index")
                    or attempt.get("status") not in {"success", "timeout", "interrupted", "error", "exhausted", "unused"}):
                raise ValueError("A prefix attempt remains active or unaccounted")
            result = prefix / entry["result"]
            command = (prefix / entry["command_prefix"]).with_suffix(".command.json")
            if attempt["status"] == "unused" and (result.exists() or command.exists()):
                raise ValueError("An unused prefix pattern has unaccounted online evidence")
            if command.exists() and read_json(command).get("status") == "running":
                raise ValueError("A prefix command remains marked running")
            if attempt["status"] == "success" and (not result.exists() or read_json(result).get("status") != "success"):
                raise ValueError("The prefix success lacks a successful public worker result")
    return state, schedule


def prepare(run, prefix, policy):
    prefix_state, prefix_schedule = _prefix_gate(prefix)
    run.mkdir(parents=True, exist_ok=False)
    provenance = run / "provenance"
    provenance.mkdir()
    files = {}
    for path in sorted(prefix.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Refusing a symlink in frozen prefix evidence: {path}")
        if path.is_file():
            files[str(path.relative_to(prefix))] = sha(path)
    binding = {"schema": "ntru-pattern-search-prefix-binding-v1", "prefix_run": str(prefix),
               "prefix_manifest_sha256": sha(prefix / "run_manifest.json"),
               "prefix_schedule_sha256": sha(prefix / "schedule.json"),
               "prefix_artifact_sha256": files,
               "rule": "Existing prefix files must retain these hashes; later independent offline outputs may be added.",
               "interpretation": "The original group budget ends phase one only; unrecovered groups continue searching."}
    preserve_json(run / "prefix_binding.json", binding)
    preserve_json(run / "policy.json", policy)
    for relative in ("run_manifest.json", "schedule.json", "provenance/frozen_manifest.json"):
        target = provenance / "prefix" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(prefix / relative, target)
    paths = original.source_paths() + [Path(__file__).resolve()]
    sources = {}
    for path in paths:
        relative = path.relative_to(ROOT)
        target = provenance / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        sources[str(relative)] = sha(path)
    prefix_frozen = read_json(prefix / "provenance/frozen_manifest.json")
    inputs = {}
    for label, item in prefix_frozen["inputs"].items():
        source = ROOT / item["source"]
        target = provenance / (label + ".frozen.json")
        shutil.copyfile(source, target)
        inputs[label] = {"source": item["source"], "sha256": item["sha256"],
                         "frozen": str(target.relative_to(run))}
    runtime = ROOT / "experiments/ntru_e2e/.deps/runtime.json"
    environment = {"python": sys.version, "executable": sys.executable,
                   "runtime_configuration_sha256": sha(runtime) if runtime.exists() else None,
                   "rule": "The prefix verifier also checks the same Python executable/version and pinned runtime."}
    preserve_json(provenance / "environment.json", environment)
    frozen = {"schema": "ntru-pattern-search-extension-frozen-v1", "prepared_utc": utc(),
              "policy_sha256": sha(run / "policy.json"), "prefix_binding_sha256": sha(run / "prefix_binding.json"),
              "source_sha256": sources, "inputs": inputs,
              "prefix_snapshot_sha256": {relative: sha(provenance / "prefix" / relative)
                  for relative in ("run_manifest.json", "schedule.json", "provenance/frozen_manifest.json")},
              "environment_sha256": sha(provenance / "environment.json"),
              "baseline_truth_sha256": prefix_frozen.get("baseline_truth_sha256")}
    preserve_json(provenance / "frozen_manifest.json", frozen)
    (run / "ledger.jsonl").touch(exist_ok=False)
    schedule, state, _ = replay(run)
    save_views(run, schedule, state)
    return schedule, state


def initial_views(run):
    policy = read_json(run / "policy.json")
    binding = read_json(run / "prefix_binding.json")
    old_state = read_json(run / "provenance/prefix/run_manifest.json")
    old_schedule = read_json(run / "provenance/prefix/schedule.json")
    frozen = read_json(run / "provenance/frozen_manifest.json")
    schedule = {"schema": "ntru-pattern-primal-until-recovery-schedule-v1", "policy": policy,
                "prefix_run": binding["prefix_run"], "public_source": old_schedule["public_source"],
                "prefix_binding_sha256": sha(run / "prefix_binding.json"),
                "timing_scope": policy["timing_scope"], "groups": []}
    state = {"schema": "ntru-pattern-primal-until-recovery-run-v1", "status": "prepared",
             "prepared_utc": frozen["prepared_utc"], "prefix_run": binding["prefix_run"],
             "frozen_manifest_sha256": sha(run / "provenance/frozen_manifest.json"),
             "completion": None, "resume_count": 0, "groups": []}
    for previous, old_group in zip(old_state["groups"], old_schedule["groups"]):
        actual = [(attempt, entry) for attempt, entry in zip(previous["attempts"], old_group["patterns"])
                  if attempt["status"] != "unused"]
        next_index = max((entry["index"] for _, entry in actual), default=-1) + 1
        success = previous["status"] == "success"
        group = {"k": previous["k"], "dimension": previous["dimension"],
                 "status": "success" if success else "search_continues",
                 "prefix_status": previous["status"],
                 "prefix_interpretation": "recovered" if success else "phase_one_budget_ended_search_continues",
                 "prefix_attempt_count": len(actual), "prefix_charged_wall_seconds": previous["charged_wall_seconds"],
                 "prefix_sampling_seconds": previous["sampling_seconds"],
                 "extension_sampling_seconds": 0.0, "extension_worker_seconds": 0.0,
                 "charged_wall_seconds": previous["charged_wall_seconds"],
                 "sampling_seconds": previous["sampling_seconds"], "attempts": [],
                 "duplicate_patterns_rejected": 0, "next_pattern_index": next_index,
                 "seen_pattern_sha256": [pattern_digest(entry["pattern"]) for _, entry in actual]}
        if success:
            group.update(successful_phase="prefix", successful_pattern_index=previous["successful_pattern_index"],
                         first_success_beta=previous.get("first_success_beta"),
                         first_success_tour=previous.get("first_success_tour"))
        schedule["groups"].append({"k": previous["k"], "dimension": previous["dimension"],
                                   "sampling_seconds": 0.0, "patterns": []})
        state["groups"].append(group)
    return schedule, state


def _apply(schedule, state, event):
    kind = event["event"]
    if kind == "runner_started":
        if state.get("started_utc"):
            state["resume_count"] += 1
        else:
            state["started_utc"] = event["recorded_utc"]
        state.update(status="online_running", completion=None)
    elif kind in {"runner_interrupted", "runner_error", "search_paused_limit", "online_finished"}:
        state.update(status={"runner_interrupted": "interrupted", "runner_error": "runner_error",
                             "search_paused_limit": "search_continues", "online_finished": "online_finished"}[kind])
        if "reason" in event:
            state["reason"] = event["reason"]
        if kind == "online_finished":
            if not all(group["status"] == "success" for group in state["groups"]):
                raise ValueError("A completion event precedes recovery in every group")
            state.update(completion="all_groups_recovered", finished_utc=event["recorded_utc"])
    else:
        groups = {group["k"]: group for group in state["groups"]}
        frozen_groups = {group["k"]: group for group in schedule["groups"]}
        group, frozen_group = groups[event["k"]], frozen_groups[event["k"]]
        if kind in {"duplicate_pattern_rejected", "attempt_prepared"}:
            if group["status"] == "success" or event["index"] != group["next_pattern_index"]:
                raise ValueError("Pattern generation skipped/replayed a public index or followed success")
            sampling = event["sampling_seconds"]
            if not isinstance(sampling, (int, float)) or not math.isfinite(sampling) or sampling < 0:
                raise ValueError("Invalid sampling charge in ledger")
            group["extension_sampling_seconds"] += sampling
            group["sampling_seconds"] += sampling
            group["charged_wall_seconds"] += sampling
            frozen_group["sampling_seconds"] += sampling
            group["next_pattern_index"] += 1
            if kind == "duplicate_pattern_rejected":
                if event["pattern_sha256"] not in group["seen_pattern_sha256"]:
                    raise ValueError("A duplicate rejection has no earlier matching pattern")
                group["duplicate_patterns_rejected"] += 1
            else:
                entry = event["entry"]
                if entry["index"] != event["index"] or pattern_digest(entry["pattern"]) in group["seen_pattern_sha256"]:
                    raise ValueError("A prepared pattern is duplicated or misindexed")
                group["seen_pattern_sha256"].append(pattern_digest(entry["pattern"]))
                frozen_group["patterns"].append(entry)
                group["attempts"].append({"index": entry["index"], "status": "prepared",
                                          "charged_wall_seconds": 0.0, "prepared_utc": event["recorded_utc"]})
        elif kind in {"attempt_started", "attempt_finished"}:
            matches = [attempt for attempt in group["attempts"] if attempt["index"] == event["index"]]
            if len(matches) != 1:
                raise ValueError("Execution event lacks one frozen prepared attempt")
            attempt = matches[0]
            if kind == "attempt_started":
                if attempt["status"] != "prepared":
                    raise ValueError("An already-started attempt cannot be launched again")
                attempt.update(status="running", started_utc=event["recorded_utc"],
                               reserved_charge_seconds=event["reserved_charge_seconds"],
                               process_timeout_seconds=event["process_timeout_seconds"])
            else:
                if attempt["status"] != "running":
                    raise ValueError("A finished attempt was not running, or was already charged")
                charge = event["outcome"]["charged_wall_seconds"]
                if not isinstance(charge, (int, float)) or not math.isfinite(charge) or charge < 0:
                    raise ValueError("Invalid worker charge in ledger")
                attempt.update(event["outcome"], finished_utc=event["recorded_utc"])
                group["extension_worker_seconds"] += charge
                group["charged_wall_seconds"] += charge
                if attempt["status"] == "success":
                    group.update(status="success", successful_phase="extension",
                                 successful_pattern_index=event["index"],
                                 first_success_beta=attempt.get("first_success_beta"),
                                 first_success_tour=attempt.get("first_success_tour"))
        else:
            raise ValueError(f"Unknown ledger event: {kind}")
    state["updated_utc"] = event["recorded_utc"]


def replay(run):
    schedule, state = initial_views(run)
    previous = sha(run / "provenance/frozen_manifest.json")
    records = []
    with (run / "ledger.jsonl").open() as ledger:
        for sequence, line in enumerate(ledger, 1):
            if not line.endswith("\n"):
                raise ValueError("An incomplete final ledger record requires evidence-preserving recovery")
            event = json.loads(line)
            claimed = event.pop("sha256")
            if event.get("sequence") != sequence or event.get("previous_sha256") != previous or digest(event) != claimed:
                raise ValueError("Append-only ledger hash chain is inconsistent")
            event["sha256"] = claimed
            _apply(schedule, state, event)
            previous = claimed
            records.append(event)
    for value in (schedule, state):
        value.update(ledger_head_sha256=previous, ledger_entry_count=len(records))
    return schedule, state, records


def save_views(run, schedule, state):
    write_json(run / "schedule.json", schedule)
    write_json(run / "run_manifest.json", state)


class Journal:
    def __init__(self, run):
        self.run = run
        self.schedule, self.state, self.records = replay(run)

    def append(self, kind, **fields):
        event = {"sequence": len(self.records)+1, "previous_sha256": self.state["ledger_head_sha256"],
                 "recorded_utc": utc(), "event": kind, **fields}
        event["sha256"] = digest(event)
        # Apply to detached views first: a programming error cannot append an
        # invalid event and permanently damage an otherwise valid ledger.
        new_schedule = json.loads(json.dumps(self.schedule))
        new_state = json.loads(json.dumps(self.state))
        _apply(new_schedule, new_state, event)
        with (self.run / "ledger.jsonl").open("a") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
            handle.flush()
            import os
            os.fsync(handle.fileno())
        self.records.append(event)
        for value in (new_schedule, new_state):
            value.update(ledger_head_sha256=event["sha256"], ledger_entry_count=len(self.records))
        self.schedule, self.state = new_schedule, new_state
        save_views(self.run, self.schedule, self.state)
        return event


def _verify_inputs(run, *, check_prefix=True):
    frozen = read_json(run / "provenance/frozen_manifest.json")
    expected = {run / "policy.json": frozen["policy_sha256"],
                run / "prefix_binding.json": frozen["prefix_binding_sha256"],
                run / "provenance/environment.json": frozen["environment_sha256"]}
    for relative, value in frozen["source_sha256"].items():
        expected[ROOT / relative] = value
        expected[run / "provenance/sources" / relative] = value
    for relative, value in frozen["prefix_snapshot_sha256"].items():
        expected[run / "provenance/prefix" / relative] = value
    for item in frozen["inputs"].values():
        expected[ROOT / item["source"]] = item["sha256"]
        expected[run / item["frozen"]] = item["sha256"]
    if check_prefix:
        binding = read_json(run / "prefix_binding.json")
        prefix = Path(binding["prefix_run"])
        for relative, value in binding["prefix_artifact_sha256"].items():
            expected[prefix / relative] = value
    changed = [str(path) for path, value in expected.items() if not path.is_file() or sha(path) != value]
    if changed:
        raise ValueError("Frozen search evidence changed: " + ", ".join(changed))
    environment = read_json(run / "provenance/environment.json")
    if environment["python"] != sys.version or environment["executable"] != sys.executable:
        raise ValueError("The extension must use the original Python executable and version")
    runtime = ROOT / "experiments/ntru_e2e/.deps/runtime.json"
    if environment.get("runtime_configuration_sha256") and sha(runtime) != environment["runtime_configuration_sha256"]:
        raise ValueError("Pinned runtime configuration changed")


def verify_extension(run, state=None):
    """Read-only verification used before final offline truth access.

    Return (schedule, state), after checking the full prefix, executable inputs,
    chained journal, configurations, final trial hashes and materialized views.
    Independent offline files added later to either run directory are allowed.
    """
    run = Path(run).resolve()
    _verify_inputs(run)
    schedule, recovered, records = replay(run)
    policy = schedule["policy"]
    plan = read_json(run / "provenance/plan.frozen.json")
    for group in schedule["groups"]:
        for entry in group["patterns"]:
            permutation, material, seed_hash = original.paired_permutation(entry["index"])
            expected_pattern = sorted(permutation[:group["k"]])
            expected_config = dict(plan["baseline"])
            expected_config.update(k=group["k"], pattern=expected_pattern, pattern_index=entry["index"], kappa=0,
                                   bkz_stop=min(policy["beta_stop"], group["dimension"]))
            config = run / entry["config"]
            if (entry["pattern"] != expected_pattern or entry["seed_material"] != material
                    or entry["seed_sha256"] != seed_hash or entry["pattern_number"] != entry["index"]+1
                    or entry["wall_budget_seconds"] != pattern_budget(policy, entry["index"])
                    or sha(config) != entry["config_sha256"] or read_json(config) != expected_config):
                raise ValueError("A continuation pattern/configuration differs from the frozen public rule")
    for event in records:
        if event["event"] == "attempt_finished":
            for relative, value in event["outcome"].get("artifact_sha256", {}).items():
                path = run / relative
                if not path.is_file() or sha(path) != value:
                    raise ValueError(f"A completed continuation artifact changed: {path}")
    if (read_json(run / "schedule.json") != schedule or read_json(run / "run_manifest.json") != recovered
            or state is not None and state != recovered):
        raise ValueError("Materialized search views disagree with the append-only ledger")
    return schedule, recovered


def prepare_attempt(journal, k):
    run, policy = journal.run, journal.schedule["policy"]
    while True:
        group = next(group for group in journal.state["groups"] if group["k"] == k)
        index = group["next_pattern_index"]
        tick = time.perf_counter()
        permutation, material, seed_hash = original.paired_permutation(index)
        pattern = sorted(permutation[:k])
        sampling = time.perf_counter() - tick
        if pattern_digest(pattern) in group["seen_pattern_sha256"]:
            journal.append("duplicate_pattern_rejected", k=k, index=index,
                           pattern_sha256=pattern_digest(pattern), sampling_seconds=sampling)
            continue
        directory = run / f"k{k:03d}" / f"pattern{index+1:06d}"
        config = dict(read_json(run / "provenance/plan.frozen.json")["baseline"])
        config.update(k=k, pattern=pattern, pattern_index=index, kappa=0,
                      bkz_stop=min(policy["beta_stop"], group["dimension"]))
        config_path = directory / "config.json"
        preserve_json(config_path, config)
        entry = {"index": index, "pattern_number": index+1, "pattern": pattern,
                 "seed_material": material, "seed_sha256": seed_hash,
                 "sampling_seconds": sampling, "config": str(config_path.relative_to(run)),
                 "config_sha256": sha(config_path), "result": str((directory / "result.json").relative_to(run)),
                 "command_prefix": f"provenance/k{k:03d}_pattern{index+1:06d}_online",
                 "wall_budget_seconds": pattern_budget(policy, index)}
        journal.append("attempt_prepared", k=k, index=index, entry=entry, sampling_seconds=sampling)
        return entry


def _artifact_hashes(run, entry):
    paths = list((run / entry["result"]).parent.rglob("*"))
    prefix = run / entry["command_prefix"]
    paths += [prefix.with_suffix(suffix) for suffix in (".command.json", ".stdout.txt", ".stderr.txt")]
    return {str(path.relative_to(run)): sha(path) for path in paths if path.is_file()}


def finish_attempt(journal, k, entry, metadata, accounting="actual_capture_wall"):
    evidence = original.result_evidence(journal.run, entry)
    status = ("success" if evidence.get("worker_status") == "success" else
              "timeout" if metadata.get("status") == "timeout" else
              "interrupted" if metadata.get("status") in {"interrupted", "running"} else
              "exhausted" if evidence.get("worker_status") == "exhausted" else "error")
    outcome = {"status": status, "command_status": metadata.get("status"),
               "exit_code": metadata.get("exit_code"), "charged_wall_seconds": metadata["wall_seconds"],
               "accounting": accounting, "artifact_sha256": _artifact_hashes(journal.run, entry), **evidence}
    journal.append("attempt_finished", k=k, index=entry["index"], outcome=outcome)
    return outcome


def reconcile_running(journal):
    for frozen_group, group in zip(journal.schedule["groups"], journal.state["groups"]):
        for entry, attempt in zip(frozen_group["patterns"], group["attempts"]):
            if attempt["status"] != "running":
                continue
            path = (journal.run / entry["command_prefix"]).with_suffix(".command.json")
            metadata = read_json(path) if path.is_file() else {"status": "interrupted"}
            if metadata.get("status") == "running" and metadata.get("pid"):
                proc = Path(f"/proc/{metadata['pid']}/stat")
                if proc.exists() and proc.read_text().split()[21] == metadata.get("process_start_ticks"):
                    raise RuntimeError(f"Prior worker PID {metadata['pid']} remains active; refusing duplicate execution")
            if isinstance(metadata.get("wall_seconds"), (int, float)):
                accounting = "recovered_actual_capture_wall"
            else:
                metadata["wall_seconds"] = attempt["reserved_charge_seconds"]
                accounting = "conservative_full_reserved_charge_after_interruption"
            finish_attempt(journal, group["k"], entry, metadata, accounting)


def execute(run):
    run = Path(run).resolve()
    _verify_inputs(run)
    journal = Journal(run)
    # Repair only materialized views after a crash; chained events are immutable.
    save_views(run, journal.schedule, journal.state)
    verify_extension(run, journal.state)

    def interrupted(number, _frame):
        raise RunnerInterrupted(f"Continuation runner received signal {number}")

    previous = {number: signal.signal(number, interrupted) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        reconcile_running(journal)
        if journal.state["status"] == "online_finished":
            return journal.state
        journal.append("runner_started")
        while not all(group["status"] == "success" for group in journal.state["groups"]):
            progressed = False
            for k in journal.schedule["policy"]["k_values"]:
                group = next(group for group in journal.state["groups"] if group["k"] == k)
                if group["status"] == "success":
                    continue
                frozen_group = next(group for group in journal.schedule["groups"] if group["k"] == k)
                ready = [(entry, attempt) for entry, attempt in zip(frozen_group["patterns"], group["attempts"])
                         if attempt["status"] == "prepared"]
                limit = journal.schedule["policy"]["max_attempts"]
                if ready:
                    entry = ready[0][0]
                elif limit is not None and len(group["attempts"]) >= limit:
                    continue
                else:
                    entry = prepare_attempt(journal, k)
                _verify_inputs(run, check_prefix=False)
                if sha(run / entry["config"]) != entry["config_sha256"]:
                    raise ValueError("Prepared continuation configuration changed")
                output, prefix = run / entry["result"], run / entry["command_prefix"]
                if output.exists() or any(prefix.with_suffix(suffix).exists() for suffix in
                                          (".command.json", ".stdout.txt", ".stderr.txt")):
                    raise FileExistsError("A prepared continuation has unaccounted prior execution evidence")
                budget = entry["wall_budget_seconds"]
                journal.append("attempt_started", k=k, index=entry["index"], reserved_charge_seconds=budget,
                               process_timeout_seconds=budget-CLEANUP_RESERVE)
                command = [sys.executable, "-m", original.MODULE,
                           "--public", str(ROOT / journal.schedule["public_source"]),
                           "--config", str(run / entry["config"]), "--output", str(output)]
                print(json.dumps({"event": "attempt_started", "k": k, "pattern_index": entry["index"],
                                  "pattern_budget_seconds": budget}), flush=True)
                metadata = original.capture_online(command, prefix, budget-CLEANUP_RESERVE)
                outcome = finish_attempt(journal, k, entry, metadata)
                updated = next(group for group in journal.state["groups"] if group["k"] == k)
                print(json.dumps({"event": "attempt_finished", "k": k, "pattern_index": entry["index"],
                                  "status": outcome["status"], "group_total_seconds": updated["charged_wall_seconds"],
                                  "first_success_beta": updated.get("first_success_beta")}), flush=True)
                progressed = True
                if metadata["status"] == "interrupted":
                    raise RunnerInterrupted(metadata.get("error", "Continuation interrupted"))
            if not progressed:
                journal.append("search_paused_limit", reason="Explicit optional maximum continuation attempts reached; search remains incomplete")
                return journal.state
        journal.append("online_finished")
    except RunnerInterrupted as error:
        journal.append("runner_interrupted", reason=str(error))
    except Exception as error:
        journal.append("runner_error", reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    return journal.state


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-name", required=True, help="New directory below method2_pattern, e.g. runs/until_recovery")
    parser.add_argument("--prefix-run", help="Completed prefix, relative to the new run directory or absolute")
    parser.add_argument("--base-pattern-seconds", type=float, default=120.0)
    parser.add_argument("--max-pattern-seconds", type=float, default=1920.0)
    parser.add_argument("--budget-block", type=int, default=8)
    parser.add_argument("--max-attempts", type=int, help="Optional maximum new attempts per k; default is unlimited")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    run = (HERE / args.run_name).resolve()
    if HERE not in run.parents:
        parser.error("--run-name must be below method2_pattern")
    if args.resume:
        _verify_inputs(run)
        journal = Journal(run)
        save_views(run, journal.schedule, journal.state)
        verify_extension(run, journal.state)
        policy = journal.schedule["policy"]
        for flag, name in (("--base-pattern-seconds", "base_pattern_seconds"),
                           ("--max-pattern-seconds", "max_pattern_seconds"),
                           ("--budget-block", "budget_block"), ("--max-attempts", "max_attempts")):
            if any(token == flag or token.startswith(flag+"=") for token in argv) and getattr(args, name) != policy[name]:
                parser.error(f"{flag} differs from the frozen continuation policy")
        if args.prefix_run and str((run / args.prefix_run).resolve()) != policy["prefix_run"]:
            parser.error("--prefix-run differs from the frozen continuation prefix")
        state = journal.state
    else:
        if not args.prefix_run:
            parser.error("--prefix-run is required when preparing a new continuation")
        if (not math.isfinite(args.base_pattern_seconds) or args.base_pattern_seconds <= CLEANUP_RESERVE
                or not math.isfinite(args.max_pattern_seconds) or args.max_pattern_seconds < args.base_pattern_seconds
                or args.budget_block < 1 or args.max_attempts is not None and args.max_attempts < 1):
            parser.error("Require finite base >2.1s, cap >=base, positive budget block and optional positive attempt limit")
        prefix = (run / args.prefix_run).resolve()
        if prefix == run or run in prefix.parents or prefix in run.parents:
            parser.error("Prefix and continuation must be separate, nonnested run directories")
        policy = {"schema": "ntru-pattern-primal-until-recovery-policy-v1", "created_utc": utc(),
                  "prefix_run": str(prefix), "k_values": DEFAULT_K, "beta_stop": 256,
                  "base_pattern_seconds": args.base_pattern_seconds, "max_pattern_seconds": args.max_pattern_seconds,
                  "budget_block": args.budget_block, "max_attempts": args.max_attempts,
                  "cleanup_reserve_seconds": CLEANUP_RESERVE,
                  "pattern_budget_rule": "min(max_pattern_seconds, base_pattern_seconds * 2**(public_pattern_index // budget_block))",
                  "search_rule": "Round-robin unrecovered k; fresh public pattern after every unsuccessful/timeout attempt; stop each k at first public-valid recovery.",
                  "pattern_rule": "Continue original paired_permutation(index), starting after the largest actually attempted prefix index; reject previously attempted identical subsets publicly.",
                  "prefix_rule": "First-phase budget is a search checkpoint, not final failure; never replay any attempted prefix index.",
                  "truth_access": "No private truth or offline coverage is read or used to select a pattern or budget.",
                  "timing_scope": "Per k: entire prefix charged wall time plus all continuation public sampling and actual captured subprocess wall times, including failures and cleanup; source/input freezing, bookkeeping and offline audit are excluded.",
                  "total_time_limit_seconds": None}
        _, state = prepare(run, prefix, policy)
    if not args.prepare_only:
        state = execute(run)
    print(json.dumps({"status": state["status"], "completion": state["completion"],
                      "manifest": str(run / "run_manifest.json"),
                      "groups": [{"k": group["k"], "status": group["status"],
                                  "total_seconds": group["charged_wall_seconds"],
                                  "extension_attempts": len(group["attempts"]),
                                  "first_success_beta": group.get("first_success_beta")}
                                 for group in state["groups"]]}), flush=True)
    return 130 if state["status"] == "interrupted" else 0


if __name__ == "__main__":
    raise SystemExit(main())
