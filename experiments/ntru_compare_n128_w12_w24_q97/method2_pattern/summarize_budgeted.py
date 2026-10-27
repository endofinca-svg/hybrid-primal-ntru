"""Audit the ten finite-budget pattern searches after every online group ends.

Private truth is inaccessible to this module until budgeted_search.verify has
validated an online_finished manifest with ten terminal groups.  Successful
candidates receive an independent exact lattice proof and a joint signed
negacyclic-shift comparison with the original secret.  Timeouts remain censored
observations; they are never classified as incorrect pattern guesses.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import random
import re


HERE = Path(__file__).resolve().parent
EXPERIMENT = HERE.parent
ROOT = HERE.parents[2]
K_VALUES = [12, 24, 32, 38, 40, 42, 44, 48, 52, 56]
CONFIG_CHANGES = {"k", "pattern", "pattern_index", "bkz_stop"}
ENVIRONMENT_KEYS = ("python", "fpylll", "platform", "threads", "float_type", "precision_bits",
                    "strategy_sha256", "strategy_max_beta", "strategy_above_max")
GROUP_FIELDS = ["method", "k", "dimension", "group_status", "success", "public_valid_recovery",
                "equivalent_to_truth", "equivalence_shift", "equivalence_sign", "attempt_count",
                "prefix_attempt_count", "timeout_count", "missing_result_count", "charged_wall_seconds",
                "worker_charged_seconds", "sampling_and_overhead_seconds", "group_budget_seconds",
                "remaining_seconds", "budget_overrun_seconds", "first_success_beta", "first_success_tour",
                "successful_pattern_number", "baseline_time_over_group_time", "audit_passed",
                "tail_hypothesis_count", "target_count", "slicer_calls", "basis_scan_calls",
                "basis_rows_examined", "candidate_count", "candidate_sources", "stage_timings",
                "peak_rss_kib", "peak_rss_complete", "command_peak_rss_kib", "failure_stage",
                "budget_blocked", "budget_block_reason", "baseline_budget_seconds", "baseline_budget_interpretation"]
ATTEMPT_FIELDS = ["k", "dimension", "pattern_index", "pattern_number", "source_run", "source_phase",
                  "attempt_status", "command_status", "command_exit_code", "charged_wall_seconds",
                  "command_wall_seconds", "result_present", "worker_status", "pattern", "censored",
                  "completed_stage_count", "protocol_passed", "environment_matches_baseline",
                  "public_valid_recovery", "equivalent_to_truth", "equivalence_shift", "equivalence_sign",
                  "first_success_beta", "first_success_tour", "candidate_norm_sq", "audit_passed",
                  "strict_lattice_equivalence_certified", "classification", "config_path", "result_path",
                  "audit_path", "tail_hypothesis_count", "target_count", "slicer_calls", "basis_scan_calls",
                  "basis_rows_examined", "candidate_count", "candidate_source", "candidate_row_index",
                  "current_stage", "failure_stage", "budget_blocked", "budget_block_reason",
                  "configured_timeout_seconds", "worker_wall_seconds", "worker_cpu_seconds", "stage_timings",
                  "peak_rss_kib", "command_peak_rss_kib", "peak_rss_source", "evidence_sha256"]
STAGE_FIELDS = ["k", "dimension", "pattern_index", "pattern_number", "source_run", "attempt_status",
                "worker_status", "stage_id", "kind", "beta", "tour", "stage_wall_seconds",
                "scan_wall_seconds", "cumulative_wall_seconds", "minimum_norm_sq", "candidate_count",
                "clean", "result_path", "stage_status", "censored", "observed_stage_wall_seconds",
                "observed_stage_wall_seconds_upper_bound", "duration_source", "duration_scope",
                "basis_rows_examined", "candidate_source"]


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def preserve(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"Refusing to overwrite different offline evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)


def csv_bytes(fields: list[str], rows: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                         if isinstance(value, (dict, list)) else value for key, value in row.items()})
    return buffer.getvalue().encode()


def nonnegative(value: object, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"Invalid nonnegative time {label}: {value!r}")
    return float(value)


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-6)


def optional_nonnegative(value: object, label: str) -> float | None:
    return None if value is None else nonnegative(value, label)


def rows_examined(stage: dict) -> int:
    """A completed scan stops at its first public-valid basis row."""
    counts = [*stage.get("rejected_rows", {}).values(), stage.get("candidate_count", 0)]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
        raise ValueError("Invalid completed basis-row scan counts")
    return sum(counts)


def stage_evidence(result: dict, events_path: Path, censored: bool,
                   command_wall_seconds: float | None = None) -> tuple[list[dict], dict]:
    """Retain measured completed stages and the observed unfinished interval.

    The latter is elapsed time between worker events, not a completed reduction
    duration.  It includes checkpoint/signal handling overhead and is therefore
    kept separate from the completed reduction and scan totals.  A last saved
    worker checkpoint provides a lower bound when the worker was killed; the
    command's longer time window supplies a separate upper bound.
    """
    command_wall_seconds = optional_nonnegative(command_wall_seconds, "command wall")
    stages = [{**stage, "stage_status": "completed", "censored": False,
               "observed_stage_wall_seconds": None, "observed_stage_wall_seconds_upper_bound": None,
               "duration_source": "completed_worker_stage",
               "duration_scope": "reduction_only; scan separately recorded",
               "basis_rows_examined": rows_examined(stage),
               "candidate_source": "reduced_basis_row" if stage.get("candidate_count") else None}
              for stage in result.get("stages", [])]
    totals = {"completed_reduction_seconds": sum(nonnegative(stage["stage_wall_seconds"], "reduction") for stage in stages),
              "completed_scan_seconds": sum(nonnegative(stage["scan_wall_seconds"], "scan") for stage in stages),
              "censored_stage_observed_seconds": 0.0,
              "censored_stage_upper_bound_seconds": 0.0,
              "worker_component_seconds": {name: nonnegative(value, name)
                                           for name, value in result.get("timings", {}).items()},
              "checkpoint_io_seconds": optional_nonnegative(result.get("checkpoint_io_seconds"), "checkpoint IO")}
    events = []
    if events_path.is_file():
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            nonnegative(event.get("elapsed_seconds"), "event elapsed")
            if events and event["elapsed_seconds"] < events[-1]["elapsed_seconds"]:
                raise ValueError(f"Worker event times run backwards: {events_path}")
            events.append(event)
    if not censored:
        return stages, totals
    completed = {stage.get("stage_id") for stage in stages}
    starts = [event for event in events if event.get("event") == "stage_started"
              and event.get("stage") not in completed]
    # Initial imports/construction precede the first explicit stage_started.
    if not starts and not stages:
        starts = [event for event in events if event.get("event") == "worker_started"]
    if not starts and not stages and command_wall_seconds is not None:
        starts = [{"stage": result.get("current_stage") or "worker_startup_or_result_unavailable", "elapsed_seconds": 0.0}]
    if not starts:
        return stages, totals
    start = starts[-1]
    finished = [event for event in events if event.get("event") == "worker_finished"
                and event["elapsed_seconds"] >= start["elapsed_seconds"]]
    if finished:
        end, source = finished[-1]["elapsed_seconds"], "worker_started_finished_events"
        upper = end - start["elapsed_seconds"]
    else:
        # Do not substitute command wall time: it has a different origin and
        # includes interpreter startup and timeout cleanup after process exit.
        known = [event["elapsed_seconds"] for event in events]
        saved = optional_nonnegative(result.get("worker_wall_seconds"), "worker wall")
        if saved is not None:
            known.append(saved)
        end, source = max(known, default=0.0), "last_worker_checkpoint_lower_bound"
        # The command begins before the worker's timer and ends after worker
        # exit, so this difference is an upper bound, not an exact duration.
        upper = max(0.0, command_wall_seconds - start["elapsed_seconds"]) if command_wall_seconds is not None else None
    duration = max(0.0, end - start["elapsed_seconds"])
    if upper is not None and upper + 1e-6 < duration:
        raise ValueError("Censored worker interval exceeds its command-wall upper bound")
    label = start.get("stage") or result.get("current_stage") or "unknown"
    match = re.fullmatch(r"bkz_b(\d+)_t(\d+)", label)
    stages.append({"stage_id": label, "kind": "bkz" if match else label,
                   "beta": int(match[1]) if match else None, "tour": int(match[2]) if match else None,
                   "stage_wall_seconds": None, "scan_wall_seconds": None,
                   "cumulative_wall_seconds": end, "minimum_norm_sq": None, "candidate_count": 0,
                   "clean": None, "stage_status": "censored", "censored": True,
                   "observed_stage_wall_seconds": duration, "duration_source": source,
                   "observed_stage_wall_seconds_upper_bound": upper,
                   "duration_scope": "unfinished-stage observed interval; includes checkpoint and signal overhead",
                   "basis_rows_examined": None, "candidate_source": None})
    totals["censored_stage_observed_seconds"] = duration
    totals["censored_stage_upper_bound_seconds"] = upper
    return stages, totals


def combine_timings(rows: list[dict]) -> dict:
    totals = {key: sum(row["stage_timings"][key] for row in rows)
              for key in ("completed_reduction_seconds", "completed_scan_seconds", "censored_stage_observed_seconds")}
    components: dict[str, float] = {}
    for row in rows:
        for name, value in row["stage_timings"]["worker_component_seconds"].items():
            components[name] = components.get(name, 0.0) + value
    totals["worker_component_seconds"] = components
    totals["checkpoint_io_seconds"] = sum(row["stage_timings"]["checkpoint_io_seconds"] or 0.0 for row in rows)
    upper_bounds = [row["stage_timings"]["censored_stage_upper_bound_seconds"] for row in rows]
    totals["censored_stage_upper_bound_seconds"] = sum(upper_bounds) if all(value is not None for value in upper_bounds) else None
    return totals


def memory_evidence(result: dict, command: dict) -> dict:
    peak = optional_nonnegative(result.get("peak_rss_kib"), "worker peak RSS")
    return {"peak_rss_kib": peak,
            "command_peak_rss_kib": optional_nonnegative(command.get("peak_rss_kib"), "capture peak RSS"),
            "peak_rss_source": "worker_RUSAGE_SELF" if peak is not None else "unavailable"}


def artifact(source: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("Attempt artifact references must be relative to source_run")
    resolved = (source / path).resolve()
    if not resolved.is_relative_to(source):
        raise ValueError("Attempt artifact reference escapes its source run")
    return resolved


def terminal_gate(run: Path) -> tuple[dict, dict]:
    # Neither private-path inspection nor hashing may precede this gate.
    from .budgeted_search import verify

    verify(run)
    state, policy = read(run / "run_manifest.json"), read(run / "policy.json")
    groups = state.get("groups", [])
    if (state.get("status") != "online_finished" or policy.get("k_values") != K_VALUES
            or len(groups) != len(K_VALUES) or [g.get("k") for g in groups] != K_VALUES
            or not close(nonnegative(policy.get("total_seconds_per_k"), "unit cap"), 473.11)
            or not close(nonnegative(policy.get("per_pattern_seconds"), "pattern cap"), 473.11)):
        raise ValueError("Final truth audit requires a verified, completed ten-group 473.11*k search")
    for group in groups:
        if group.get("status") not in {"success", "budget_exhausted"}:
            raise ValueError(f"Online group k={group.get('k')} has not reached a terminal state")
        if group.get("dimension") != 256 - group["k"]:
            raise ValueError("Group dimension differs from the requested pattern lattice")
        if not close(nonnegative(group.get("group_budget_seconds"), "group cap"), 473.11 * group["k"]):
            raise ValueError("Group cap differs from 473.11*k")
        attempts = group.get("attempts", [])
        if any(a.get("status") not in {"success", "timeout", "interrupted", "error", "exhausted"}
               for a in attempts):
            raise ValueError("An online worker is unfinished or unaccounted")
        successes = [a for a in attempts if a["status"] == "success"]
        if (len(successes) > 1 or bool(successes) != (group["status"] == "success")
                or successes and successes[0] is not attempts[-1]):
            raise ValueError("Group stopping state disagrees with its attempt inventory")
    return state, policy


def offline_sources() -> dict[str, str]:
    paths = [HERE / name for name in ("summarize_budgeted.py", "budgeted_search.py", "audit.py", "run.py", "solve.py")]
    paths += [EXPERIMENT / name for name in ("evaluate_baseline.py", "evaluate_baseline_tour2.py")]
    paths += [ROOT / "experiments/ntru_e2e" / name for name in ("algebra.py", "record_command.py")]
    paths += [p for p in (HERE / "__init__.py", EXPERIMENT / "__init__.py",
                         ROOT / "experiments/__init__.py", ROOT / "experiments/ntru_e2e/__init__.py") if p.exists()]
    return {str(path.relative_to(ROOT)): sha(path) for path in paths}


def freeze_sources(run: Path, sources: dict[str, str]) -> set[Path]:
    outputs = set()
    for relative, digest in sources.items():
        target = run / "provenance/offline_sources" / relative
        preserve(target, (ROOT / relative).read_bytes())
        if sha(target) != digest:
            raise ValueError("An offline source changed during capture")
        outputs.add(target)
    target = run / "provenance/offline_sources.json"
    preserve(target, encoded({"schema": "ntru-budgeted-pattern-offline-sources-v1", "source_sha256": sources,
                             "truth_gate": "Verified online_finished; all ten groups success or budget_exhausted."}))
    outputs.add(target)
    return outputs


def verify_previous_outputs(run: Path) -> None:
    path = run / "output_manifest.json"
    if not path.exists():
        return
    for relative, digest in read(path).get("output_sha256", {}).items():
        output = artifact(run, relative)
        if not output.is_file() or sha(output) != digest:
            raise ValueError(f"A previous offline output changed: {output}")


def paired_pattern(index: int, k: int) -> list[int]:
    digest = hashlib.sha256(f"pattern-primal-v1:10001:{index}".encode("ascii")).digest()
    values = list(range(128))
    random.Random(int.from_bytes(digest, "big")).shuffle(values)
    return sorted(values[:k])


def success_audit(run: Path, k: int, index: int, public: Path, truth: Path,
                  config: Path, result: Path, sources: dict[str, str]) -> tuple[dict, Path]:
    from .audit import audit_trial

    target = run / "audit" / f"k{k:03d}_pattern{index + 1:03d}.json"
    binding = {"inputs_sha256": {"public": sha(public), "truth": sha(truth),
                                 "config": sha(config), "result": sha(result)},
               "offline_source_sha256": sources, "exact_determinant": True}
    if target.exists():
        report = read(target)
        if report.get("offline_binding") != binding:
            raise ValueError(f"Saved candidate audit has different inputs or sources: {target}")
        for evidence in report.get("artifact_hashes", {}).values():
            path = Path(evidence["path"])
            if not path.is_file() or sha(path) != evidence["sha256"]:
                raise ValueError(f"Saved candidate audit artifact changed: {path}")
        return report, target
    report = audit_trial(public, truth, config, result, exact_determinant=True)
    report["offline_binding"] = binding
    preserve(target, encoded(report))
    return report, target


def report_markdown(summary: dict) -> str:
    baseline = summary["baseline"]
    lines = ["同一公钥的 pattern primal 实验（总预算 473.11×k 秒）", "",
             f"参数 n=128、q=97、重量=(12,24)。无 pattern 基线完整在线用时 {baseline['online_total_wall_seconds']:.6f} 秒，"
             f"首次成功 BKZ-{baseline['first_success_beta']}，第 {baseline['first_success_tour']} 轮。",
             "无 pattern 基线复用已完成的历史两轮实验；冻结的预算为 null（历史未设置超时），473.11 秒是其观测用时的取整值。",
             "", "| k | 状态 | 猜测次数 | 超时次数 | 总用时/秒 | 总预算/秒 | 首次成功 BKZ | 基线用时/本组用时 | 秘密平移 (shift,sign) |",
             "| ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |"]
    lines.append(f"| 0（基线） | 成功 | 1 | 0 | {baseline['online_total_wall_seconds']:.6f} | 未设限 | "
                 f"{baseline['first_success_beta']}（第 {baseline['first_success_tour']} 轮） | 1.000000 | "
                 f"({baseline['equivalence']['shift']},{baseline['equivalence']['sign']}) |")
    for group in summary["groups"]:
        if group["success"]:
            status = "成功"
            beta = f"{group['first_success_beta']}（第 {group['first_success_tour']} 轮）" if group["first_success_beta"] is not None else "LLL"
            ratio = f"{group['baseline_time_over_group_time']:.6f}"
            shift = f"({group['equivalence_shift']},{group['equivalence_sign']})"
        else:
            status = "预算截止，未观察到恢复" if group["group_status"] == "budget_exhausted" else "公开候选需检查审计"
            beta, ratio, shift = "—", "—", "—"
        lines.append(f"| {group['k']} | {status} | {group['attempt_count']} | {group['timeout_count']} | "
                     f"{group['charged_wall_seconds']:.6f} | {group['group_budget_seconds']:.2f} | {beta} | {ratio} | {shift} |")
    lines += ["", "| k | 尾部假设数 | 目标数 | Slicer 调用 | 完成的基行扫描 | 公开有效候选 | worker 峰值 RSS/KiB | 失败/截止阶段 | 预算阻塞 |",
              "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |"]
    for group in summary["groups"]:
        peak = group["peak_rss_kib"]
        lines.append(f"| {group['k']} | {group['tail_hypothesis_count']} | {group['target_count']} | "
                     f"{group['slicer_calls']} | {group['basis_scan_calls']} | {group['candidate_count']} | "
                     f"{int(peak) if peak is not None else '未知'} | {group['failure_stage'] or '—'} | "
                     f"{'是' if group['budget_blocked'] else '否'} |")
    lines += ["", "κ=0，T 为空，每个 pattern 只对应一个空尾部假设；目标数和 Slicer 调用均为 0。"
              "候选来源为完成 LLL/BKZ 后的约化基行；每次扫描只保留首个公开有效候选。",
              "", "同一秘密对 (f,g) 的共同带符号负循环平移视为正确恢复。成功候选独立验证公钥方程、三元重量、"
              "f 的可逆性、pattern 的零约束、保存基行来源和整数行列式 ±1；shift 和 sign 来自在线全部结束后的独立审计。",
              "", "每次从原始格基执行 LLL；BKZ 从 30 开始、步长 1，未成功时每块严格两轮，成功后立即停止。"
              "首次成功块大小只适用于保存的渐增轨迹。超时属于删失观测，不能认定其 pattern 猜错。",
              "", "总用时包含本轮全部猜测、所有失败与超时进程、清理以及公开采样开销。密钥生成、测试、暂停和离线审计不计入。"
              "速度比只对已审计确认的秘密恢复给出，分母为本组全部累计时间。",
              "", "各 k 均从 pattern 序号 0 重新开始，仅计本轮结果和用时；具体冻结规则见 policy.json。",
              "", "stages.csv 同时记录完成阶段的约化/扫描用时和中断阶段的删失观测区间；后者包含检查点及信号处理开销，"
              "不冒充完整约化耗时。若只能取得最后检查点，另列观测下界和包含启动/清理的命令用时上界。worker 峰值 RSS 来自各 worker 的 RUSAGE_SELF；"
              "command_peak_rss_kib 是捕获进程的历史子进程高水位，单独保留，不当作每次 worker 的精确峰值。",
              "", f"离线核验：{'通过' if summary['audit_passed'] else '存在未通过项目，请检查 summary.json 和 protocols.json'}。",
              "", "结构化结果见 comparison.csv、attempts.csv、stages.csv、summary.json；成功候选的精确证明位于 audit/。", ""]
    return "\n".join(lines)


def summarize(run_dir: str | Path) -> dict:
    run = Path(run_dir).resolve()
    state, policy = terminal_gate(run)
    verify_previous_outputs(run)
    sources = offline_sources()
    outputs = freeze_sources(run, sources)
    frozen_path = run / "frozen_manifest.json"
    if not frozen_path.exists():
        frozen_path = run / "provenance/frozen_manifest.json"
    frozen = read(frozen_path)
    public_path = EXPERIMENT / "public/instance_01.json"
    baseline_path = EXPERIMENT / "baseline_tour2/summary.json"
    baseline_config_path = EXPERIMENT / "baseline_tour2/config.json"
    baseline_result_path = EXPERIMENT / "baseline_tour2/result.json"
    baseline_manifest_path = EXPERIMENT / "run_manifest_tour2.json"
    baseline_manifest = read(baseline_manifest_path)
    for label, path in (("public", public_path), ("baseline_summary", baseline_path)):
        binding = frozen.get("inputs", {}).get(label)
        if binding and sha(path) != binding["sha256"]:
            raise ValueError(f"The shared {label} input differs from the frozen online input")
    if sha(public_path) != baseline_manifest["public_sha256"]:
        raise ValueError("The reused public instance differs from the baseline")
    baseline, baseline_config, baseline_result = read(baseline_path), read(baseline_config_path), read(baseline_result_path)
    baseline_time = nonnegative(baseline["online_total_wall_seconds"], "baseline time")
    if not baseline.get("audit_passed") or baseline.get("first_success_beta") != 35:
        raise ValueError("The strict two-tour baseline lacks its expected completed audit")
    # The complete public online gate and source freeze have both succeeded.
    truth_path = EXPERIMENT / "private/instance_01.truth.json"
    truth_digest = frozen.get("baseline_truth_sha256", baseline_manifest["truth_sha256"])
    if sha(truth_path) != truth_digest:
        raise ValueError("The audit truth differs from the original shared instance")
    from ..evaluate_baseline_tour2 import audit_protocol

    comparison, attempt_rows, stage_rows, protocols = [], [], [], []
    attempt_bindings = []
    baseline_common = {name: value for name, value in baseline_config.items() if name not in CONFIG_CHANGES}
    baseline_environment = baseline_result.get("environment", {})
    all_passed = True
    for group in state["groups"]:
        rows = []
        for attempt in group["attempts"]:
            k, index = group["k"], attempt["index"]
            source = Path(attempt["source_run"])
            if not source.is_absolute():
                raise ValueError("Attempt source_run must be absolute")
            source = source.resolve()
            if source != run:
                raise ValueError("Fresh search must not import attempts or charges from an earlier run")
            config_path, result_path = artifact(source, attempt["config"]), artifact(source, attempt["result"])
            config = read(config_path)
            common = {name: value for name, value in config.items() if name not in CONFIG_CHANGES}
            if (common != baseline_common or config.get("k") != k or config.get("pattern_index") != index
                    or config.get("pattern") != paired_pattern(index, k)
                    or config.get("bkz_stop") != min(256, group["dimension"])):
                raise ValueError(f"Worker conditions/pattern differ from the baseline comparison: {config_path}")
            command_path = artifact(source, attempt["command_prefix"]).with_suffix(".command.json")
            command = read(command_path)
            cost = nonnegative(attempt["charged_wall_seconds"], "worker charge")
            command_time = nonnegative(command.get("wall_seconds"), "command wall time")
            if not close(cost, command_time) or command.get("status") == "running":
                raise ValueError("An attempt's actual command time is omitted or still running")
            exists = result_path.is_file()
            result = read(result_path) if exists else {}
            status = result.get("status")
            censored = attempt["status"] in {"timeout", "interrupted", "error"}
            events_path = artifact(source, str((result_path.parent / "events.jsonl").relative_to(source)))
            measured_stages, timings = stage_evidence(result, events_path, censored, command_time)
            evidence = {label: {"path": str(path), "sha256": sha(path)}
                        for label, path in (("config", config_path), ("command", command_path),
                                            ("result", result_path), ("events", events_path)) if path.is_file()}
            attempt_bindings.append({"k": k, "pattern_index": index, "artifacts": evidence})
            protocol = None
            environment_matches = None
            if exists:
                if result.get("public_sha256") != sha(public_path) or result.get("config_sha256") != sha(config_path):
                    raise ValueError("Worker result public/configuration binding does not match")
                protocol = audit_protocol(config, result)
                if not result.get("stages") and censored:
                    protocol["checks"].pop("tour2_stages_nonempty", None)
                    protocol["failed_checks"] = [name for name, passed in protocol["checks"].items() if not passed]
                    protocol["protocol_passed"] = not protocol["failed_checks"]
                    protocol["evidence_status"] = "no_completed_stage_to_audit"
                environment = result.get("environment", {})
                environment_matches = (None if censored and not environment else
                                       all(environment.get(key) == baseline_environment.get(key) for key in ENVIRONMENT_KEYS))
                all_passed = all_passed and protocol["protocol_passed"] and environment_matches is not False
            elif not censored:
                raise ValueError("A complete online attempt lacks its worker result")
            if (attempt["status"] == "success") != (status == "success"):
                raise ValueError("Attempt success state disagrees with its public worker result")
            audit, audit_path = None, None
            if attempt["status"] == "success":
                audit, audit_path = success_audit(run, k, index, public_path, truth_path, config_path, result_path, sources)
                outputs.add(audit_path)
                all_passed = all_passed and audit["audit_passed"] and audit["strict_lattice_equivalence_certified"]
            equivalent = bool(audit and audit.get("equivalence") is not None)
            equivalence = audit.get("equivalence") or {} if audit else {}
            public_valid = bool(audit and audit["candidate_validation"]["valid"])
            candidate = result.get("found_candidate") or {}
            candidate_count = sum(stage.get("candidate_count", 0) for stage in result.get("stages", []))
            current_stage = result.get("current_stage")
            failure_stage = None if attempt["status"] == "success" else current_stage
            if failure_stage in {None, "finished"}:
                failure_stage = result.get("stages", [{}])[-1].get("stage_id") if result.get("stages") else "worker_startup_or_result_unavailable"
            if attempt["status"] == "success":
                failure_stage = None if audit and audit["audit_passed"] else "independent_audit"
            budget_blocked = attempt["status"] == "timeout" or command.get("status") == "timeout"
            row = {"k": k, "dimension": group["dimension"], "pattern_index": index, "pattern_number": index + 1,
                   "source_run": str(source), "source_phase": "current",
                   "attempt_status": attempt["status"], "command_status": command.get("status"),
                   "command_exit_code": command.get("exit_code"), "charged_wall_seconds": cost,
                   "command_wall_seconds": command_time, "result_present": exists, "worker_status": status,
                   "pattern": config["pattern"], "censored": censored,
                   "completed_stage_count": len(result.get("stages", [])),
                   "protocol_passed": protocol["protocol_passed"] if protocol else None,
                   "environment_matches_baseline": environment_matches,
                   "public_valid_recovery": public_valid, "equivalent_to_truth": equivalent if audit else None,
                   "equivalence_shift": equivalence.get("shift"), "equivalence_sign": equivalence.get("sign"),
                   "first_success_beta": result.get("first_success_beta"), "first_success_tour": result.get("first_success_tour"),
                   "candidate_norm_sq": audit["candidate_validation"].get("norm_sq") if audit else None,
                   "audit_passed": audit["audit_passed"] if audit else None,
                   "strict_lattice_equivalence_certified": audit["strict_lattice_equivalence_certified"] if audit else None,
                   "classification": audit["classification"] if audit else "censored_no_recovery_observed" if censored else "no_valid_recovery",
                   "config_path": str(config_path), "result_path": str(result_path),
                   "audit_path": str(audit_path) if audit_path else None,
                   "tail_hypothesis_count": 1, "target_count": 0, "slicer_calls": 0,
                   "basis_scan_calls": len(result.get("stages", [])),
                   "basis_rows_examined": sum(rows_examined(stage) for stage in result.get("stages", [])),
                   "candidate_count": candidate_count,
                   "candidate_source": "reduced_basis_row" if candidate_count else None,
                   "candidate_row_index": candidate.get("row_index"), "current_stage": current_stage,
                   "failure_stage": failure_stage, "budget_blocked": budget_blocked,
                   "budget_block_reason": "per_attempt_timeout_clipped_to_remaining_group_allowance" if budget_blocked else None,
                   "configured_timeout_seconds": command.get("timeout_seconds"),
                   "worker_wall_seconds": optional_nonnegative(result.get("worker_wall_seconds"), "worker wall"),
                   "worker_cpu_seconds": optional_nonnegative(result.get("worker_cpu_seconds"), "worker CPU"),
                   "stage_timings": timings, **memory_evidence(result, command), "evidence_sha256": evidence}
            rows.append(row)
            protocols.append({"k": k, "pattern_index": index, "source_run": str(source), "result_path": str(result_path),
                              "protocol": protocol, "environment_matches_baseline": environment_matches})
            for stage in measured_stages:
                stage_rows.append({**{key: row[key] for key in ("k", "dimension", "pattern_index", "pattern_number", "source_run", "attempt_status", "worker_status", "result_path")}, **stage})
        attempt_rows.extend(rows)
        total = nonnegative(group["charged_wall_seconds"], "group charge")
        worker_total = sum(row["charged_wall_seconds"] for row in rows)
        overhead = total - worker_total
        remaining = nonnegative(group["remaining_seconds"], "remaining allowance")
        cap = group["group_budget_seconds"]
        if overhead < -1e-6 or not close(remaining, max(0.0, cap - total)):
            raise ValueError("Total charge omits worker costs or remaining allowance is inconsistent")
        successes = [row for row in rows if row["public_valid_recovery"]]
        recovered = successes[0] if successes else None
        confirmed = bool(recovered and recovered["equivalent_to_truth"] and recovered["audit_passed"]
                         and recovered["strict_lattice_equivalence_certified"])
        group_audit = all(row["protocol_passed"] is not False and row["environment_matches_baseline"] is not False
                          and row["audit_passed"] is not False for row in rows)
        if recovered:
            all_passed = all_passed and confirmed
        comparison.append({"method": "pattern_primal", "k": group["k"], "dimension": group["dimension"],
                           "group_status": group["status"], "success": confirmed,
                           "public_valid_recovery": bool(recovered), "equivalent_to_truth": recovered["equivalent_to_truth"] if recovered else None,
                           "equivalence_shift": recovered["equivalence_shift"] if recovered else None,
                           "equivalence_sign": recovered["equivalence_sign"] if recovered else None,
                           "attempt_count": len(rows), "prefix_attempt_count": sum(row["source_phase"] == "prefix" for row in rows),
                           "timeout_count": sum(row["attempt_status"] == "timeout" for row in rows),
                           "missing_result_count": sum(not row["result_present"] for row in rows),
                           "charged_wall_seconds": total, "worker_charged_seconds": worker_total,
                           "sampling_and_overhead_seconds": max(0.0, overhead), "group_budget_seconds": cap,
                           "remaining_seconds": remaining, "budget_overrun_seconds": max(0.0, total - cap),
                           "first_success_beta": recovered["first_success_beta"] if recovered else None,
                           "first_success_tour": recovered["first_success_tour"] if recovered else None,
                           "successful_pattern_number": recovered["pattern_number"] if recovered else None,
                           "baseline_time_over_group_time": baseline_time / total if confirmed and total else None,
                           "audit_passed": group_audit and (confirmed if recovered else True),
                           "tail_hypothesis_count": len(rows), "target_count": 0, "slicer_calls": 0,
                           "basis_scan_calls": sum(row["basis_scan_calls"] for row in rows),
                           "basis_rows_examined": sum(row["basis_rows_examined"] for row in rows),
                           "candidate_count": sum(row["candidate_count"] for row in rows),
                           "candidate_sources": {"reduced_basis_row": sum(row["candidate_count"] for row in rows)},
                           "stage_timings": combine_timings(rows),
                           "peak_rss_kib": max((row["peak_rss_kib"] for row in rows if row["peak_rss_kib"] is not None), default=None),
                           "peak_rss_complete": bool(rows) and all(row["peak_rss_kib"] is not None for row in rows),
                           "command_peak_rss_kib": max((row["command_peak_rss_kib"] for row in rows if row["command_peak_rss_kib"] is not None), default=None),
                           "failure_stage": None if confirmed else rows[-1]["failure_stage"] if rows else "no_attempt_completed",
                           "budget_blocked": group["status"] == "budget_exhausted",
                           "budget_block_reason": "remaining_group_allowance_exhausted_or_reserved_for_cleanup" if group["status"] == "budget_exhausted" else None,
                           "baseline_budget_seconds": None,
                           "baseline_budget_interpretation": "historical_completed_baseline_without_time_limit"})
    baseline_row = {"method": "plain_primal", "k": 0, "dimension": 256, "group_status": "success",
                    "success": True, "public_valid_recovery": True, "equivalent_to_truth": True,
                    "equivalence_shift": baseline["equivalence"]["shift"],
                    "equivalence_sign": baseline["equivalence"]["sign"], "attempt_count": 1,
                    "prefix_attempt_count": 0, "timeout_count": 0, "missing_result_count": 0,
                    "charged_wall_seconds": baseline_time, "worker_charged_seconds": baseline_result.get("worker_wall_seconds"),
                    "group_budget_seconds": None, "remaining_seconds": None, "budget_overrun_seconds": None,
                    "first_success_beta": baseline["first_success_beta"], "first_success_tour": baseline["first_success_tour"],
                    "baseline_time_over_group_time": 1.0, "audit_passed": True, "tail_hypothesis_count": 1,
                    "target_count": 0, "slicer_calls": 0, "basis_scan_calls": len(baseline_result.get("stages", [])),
                    "basis_rows_examined": sum(rows_examined(stage) for stage in baseline_result.get("stages", [])),
                    "candidate_count": 1, "candidate_sources": {"reduced_basis_row": 1},
                    "stage_timings": stage_evidence(baseline_result, baseline_result_path.parent / "events.jsonl", False)[1],
                    "peak_rss_kib": baseline.get("worker_peak_rss_kib"), "peak_rss_complete": True,
                    "failure_stage": None, "budget_blocked": False, "budget_block_reason": None,
                    "baseline_budget_seconds": None,
                    "baseline_budget_interpretation": "historical_completed_baseline_without_time_limit"}
    summary = {"schema": "ntru-budgeted-pattern-final-summary-v1", "status": "complete", "audit_passed": bool(all_passed),
               "parameters": {"n": 128, "q": 97, "w_f": 12, "w_g": 24}, "instance_count": 1,
               "policy": policy, "run_dir": str(run), "baseline": baseline, "groups": comparison,
               "baseline_budget_seconds": None,
               "baseline_budget_interpretation": "historical_completed_baseline_without_time_limit; 473.11 is rounded observed runtime, not a prior timeout",
               "comparison": [baseline_row, *comparison],
               "input_sha256": {"run_manifest": sha(run / "run_manifest.json"), "policy": sha(run / "policy.json"),
                                "frozen_manifest": sha(frozen_path), "public": sha(public_path), "truth": sha(truth_path),
                                "baseline_summary": sha(baseline_path), "baseline_config": sha(baseline_config_path),
                                "baseline_result": sha(baseline_result_path), "baseline_manifest": sha(baseline_manifest_path),
                                "attempt_artifacts": attempt_bindings},
               "offline_source_sha256": sources,
               "timing_scope": "All current-run worker wall time, timeout cleanup and public sampling/overhead; excludes prior pattern runs, key generation, tests, pauses, and offline audits.",
               "success_rule": "Public-valid short pair and independent exact proof; same joint signed negacyclic shift of the original (f,g) is a correct recovery.",
               "timeout_interpretation": "Censored observation, not evidence that the pattern guess was wrong.",
               "tail_and_decoder_scope": "kappa=0: one empty tail hypothesis per pattern; target_count=0, slicer_calls=0; candidates are reduced basis rows.",
               "memory_scope": "Worker RUSAGE_SELF peak is the per-attempt measurement; a missing worker result leaves it unknown. command_peak_rss_kib is the capture process's cumulative RUSAGE_CHILDREN high-water mark. Group peak is the maximum known worker peak; peak_rss_complete reports coverage.",
               "stage_timing_scope": "Completed reduction/scan measurements are separate from unfinished-stage observed intervals. Censored intervals include checkpoint and signal overhead; last-checkpoint intervals are lower bounds, with command-wall upper bounds including interpreter startup/cleanup. They must not be added as exact full reduction costs.",
               "first_success_beta_scope": "First successful beta in the saved progressive schedule, not a global minimum."}
    for binding in attempt_bindings:
        for item in binding["artifacts"].values():
            if sha(Path(item["path"])) != item["sha256"]:
                raise ValueError("Online evidence changed during offline summary generation")
    artifacts = {"summary.json": encoded(summary), "comparison.csv": csv_bytes(GROUP_FIELDS, [baseline_row, *comparison]),
                 "attempts.csv": csv_bytes(ATTEMPT_FIELDS, attempt_rows), "stages.csv": csv_bytes(STAGE_FIELDS, stage_rows),
                 "protocols.json": encoded({"schema": "ntru-budgeted-pattern-stage-protocols-v1", "attempts": protocols}),
                 "REPORT.md": report_markdown(summary).encode()}
    for relative, content in artifacts.items():
        target = run / relative
        preserve(target, content)
        outputs.add(target)
    if offline_sources() != sources:
        raise ValueError("Offline audit sources changed during summary generation")
    preserve(run / "output_manifest.json", encoded({"schema": "ntru-budgeted-pattern-offline-output-manifest-v1",
                                                   "input_sha256": summary["input_sha256"],
                                                   "offline_source_sha256": sources,
                                                   "output_sha256": {str(p.relative_to(run)): sha(p) for p in sorted(outputs)}}))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = summarize(args.run_dir)
    print(json.dumps({"audit_passed": summary["audit_passed"], "recovered_groups": sum(g["success"] for g in summary["groups"]),
                      "total_groups": len(summary["groups"]), "report": str(Path(args.run_dir).resolve() / "REPORT.md")}, ensure_ascii=False))
    return 0 if summary["audit_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
