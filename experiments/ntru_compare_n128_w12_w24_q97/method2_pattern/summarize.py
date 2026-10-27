"""Audit and summarize the eight zero-pattern groups after all online work ends.

Run with ``--run-dir METHOD2_RUN_DIRECTORY`` using the same interpreter as the
online runner.  This program refuses access to private truth until the frozen
public schedule is verified and every group has reached a terminal state.
Existing evidence is preserved: byte-identical summaries may be reused, and an
existing audit is reused only after checking all input and offline-source hashes.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path

from .audit import audit_trial, orbit_coverage
from .run import DEFAULT_K, EXPERIMENT, HERE, ROOT, paired_permutation, read_json, sha, verify_frozen


TERMINAL_GROUPS = {"success", "budget_exhausted", "exhausted"}
UNATTEMPTED = {"scheduled", "unused"}
ALLOWED_CONFIG_CHANGES = {"k", "pattern", "pattern_index", "bkz_stop"}
ENVIRONMENT_KEYS = ("python", "fpylll", "platform", "threads", "float_type", "precision_bits",
                    "strategy_sha256", "strategy_max_beta", "strategy_above_max")
GROUP_FIELDS = [
    "method", "k", "dimension", "group_status", "attempted_pattern_count", "online_trial_count",
    "sampling_seconds", "charged_wall_seconds", "success", "first_success_stage", "first_success_beta",
    "first_success_tour", "successful_pattern_number", "baseline_time_over_group_time",
    "compatible_truth_attempt_count", "truth_orbit_covered", "compatible_truth_rotation_count_sum",
    "timeout_count", "censored_trial_count", "complete_trial_count", "missing_result_count",
    "completed_stage_evidence_count", "integrity_audit_pass_count", "strict_math_audit_pass_count",
    "audited_trial_count", "integrity_all_available_passed", "strict_math_audit_all_attempts_passed",
    "group_budget_seconds", "budget_overrun_seconds", "outcome_interpretation",
]
ATTEMPT_FIELDS = [
    "k", "dimension", "pattern_number", "pattern_index", "pattern", "attempt_status", "command_status",
    "command_exit_code", "charged_wall_seconds", "command_wall_seconds", "result_present", "worker_status",
    "worker_current_stage", "worker_wall_seconds", "worker_first_valid_seconds", "completed_stage_count",
    "completed_bkz_tours", "last_completed_stage", "last_completed_beta", "last_completed_tour",
    "public_valid_recovery", "candidate_classification", "equivalent_to_truth", "equivalence_shift",
    "equivalence_sign", "first_success_stage", "first_success_beta", "first_success_tour", "candidate_norm_sq",
    "compatible_truth_rotation_count", "compatible_signed_truth_representatives", "truth_orbit_covered",
    "censored", "trial_complete", "completed_stage_evidence", "audit_integrity_passed",
    "strict_lattice_equivalence_certified", "audit_failed_checks", "audit_missing_evidence",
    "environment_matches_baseline", "outcome_interpretation", "config_path", "result_path", "audit_path",
]
STAGE_FIELDS = [
    "method", "k", "dimension", "pattern_number", "pattern_index", "attempt_status", "worker_status",
    "command_status", "stage_id", "kind", "beta", "tour", "stage_wall_seconds", "scan_wall_seconds",
    "cumulative_wall_seconds", "minimum_norm_sq", "candidate_count", "clean", "result_path",
]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _preserve(path: Path, content: bytes) -> None:
    """Exclusive first write; a repeated invocation must produce identical bytes."""
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"Refusing to replace different saved evidence: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)


def _csv_bytes(fields: list[str], rows: list[dict]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, separators=(",", ":"), ensure_ascii=False)
                         if isinstance(value, (list, dict)) else value for key, value in row.items()})
    return buffer.getvalue().encode()


def _nonnegative(value: object, label: str) -> float:
    if (not isinstance(value, (float, int)) or isinstance(value, bool)
            or not math.isfinite(value) or value < 0):
        raise ValueError(f"Invalid nonnegative time {label}: {value!r}")
    return float(value)


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-7)


def _offline_sources() -> dict[str, str]:
    paths = [HERE / "audit.py", HERE / "summarize.py", EXPERIMENT / "evaluate_baseline.py",
             EXPERIMENT / "evaluate_baseline_tour2.py"]
    paths += [path for path in (HERE / "__init__.py", EXPERIMENT / "__init__.py",
                               ROOT / "experiments/__init__.py") if path.is_file()]
    return {str(path.relative_to(ROOT)): sha(path) for path in paths}


def _terminal_gate(run: Path, state: dict) -> dict:
    # In particular, no private-path existence check or hashing occurs above
    # this gate.  verify_frozen checks only online/public provenance.
    verify_frozen(run, state)
    if state.get("status") != "online_finished":
        raise ValueError("Private audit requires state.status=online_finished")
    schedule = read_json(run / "schedule.json")
    groups, frozen_groups = state.get("groups", []), schedule.get("groups", [])
    if (len(groups) != 8 or len(frozen_groups) != 8
            or {group.get("k") for group in groups} != set(DEFAULT_K)
            or [group.get("k") for group in groups] != [group.get("k") for group in frozen_groups]):
        raise ValueError("All eight requested k groups must be present in the same frozen order")
    for group, frozen in zip(groups, frozen_groups):
        if group.get("status") not in TERMINAL_GROUPS:
            raise ValueError(f"Group k={group.get('k')} has not finished online work")
        if (group.get("dimension") != frozen.get("dimension")
                or group.get("group_budget_seconds") != schedule["policy"]["group_budget_seconds"]):
            raise ValueError("Group state dimension/budget differs from the frozen schedule")
        attempts, patterns = group.get("attempts", []), frozen.get("patterns", [])
        if len(attempts) != len(patterns) or len(patterns) != schedule["policy"]["max_patterns"]:
            raise ValueError("Run state and frozen pattern inventory differ")
        for attempt, entry in zip(attempts, patterns):
            if attempt.get("index") != entry.get("index") or attempt.get("status") in {"running", "scheduled"}:
                raise ValueError("A pattern remains active/unaccounted, so truth cannot yet be read")
            if attempt.get("status") not in {"success", "timeout", "interrupted", "error", "exhausted", "unused"}:
                raise ValueError("Unknown final attempt status")
            if attempt["status"] == "unused":
                command_path = (run / entry["command_prefix"]).with_suffix(".command.json")
                if (run / entry["result"]).exists() or command_path.exists():
                    raise ValueError("An unused pattern has unaccounted online evidence")
    return schedule


def _configuration_checks(run: Path, schedule: dict, baseline_config: dict, public: dict) -> dict:
    common = {name: value for name, value in baseline_config.items() if name not in ALLOWED_CONFIG_CHANGES}
    checked = 0
    for group in schedule["groups"]:
        k, dimension = group["k"], 2 * public["n"] - group["k"]
        if group["dimension"] != dimension:
            raise ValueError("Frozen group dimension does not match the removed coordinates")
        for entry in group["patterns"]:
            config_path = run / entry["config"]
            config = read_json(config_path)
            observed_common = {name: value for name, value in config.items() if name not in ALLOWED_CONFIG_CHANGES}
            permutation, material, digest = paired_permutation(entry["index"], public["n"])
            expected_pattern = sorted(permutation[:k])
            expected_stop = min(schedule["policy"]["beta_stop"], dimension)
            if (observed_common != common or config.get("k") != k
                    or config.get("pattern") != expected_pattern or entry["pattern"] != expected_pattern
                    or config.get("pattern_index") != entry["index"]
                    or config.get("bkz_stop") != expected_stop or sha(config_path) != entry["config_sha256"]
                    or entry.get("seed_material") != material or entry.get("seed_sha256") != digest
                    or entry.get("pattern_number") != entry["index"] + 1):
                raise ValueError(f"Pattern/configuration differs from paired frozen comparison: {config_path}")
            checked += 1
    return {"all_prescheduled_configurations_checked": checked, "same_public_instance": True,
            "baseline_configuration_equal_except": sorted(ALLOWED_CONFIG_CHANGES),
            "bkz_stop_rule": "min(frozen policy beta_stop, 2*n-k)",
            "budgets_are_external_censoring_rules": schedule["policy"],
            "all_other_worker_conditions_match_baseline": True,
            "paired_public_patterns_regenerated_and_verified": True}


def _checkpoint_binding(result_path: Path, result: dict) -> dict:
    paths = {}
    for field in ("initial_basis_path", "stage_basis_path", "transformation_path",
                  "latest_complete_basis_path", "latest_complete_transform_path"):
        value = result.get(field)
        if value:
            path = Path(value)
            path = path if path.is_absolute() else result_path.parent / path
            paths[field] = {"recorded_path": value, "sha256": sha(path) if path.is_file() else None}
    return paths


def _audit_or_reuse(run: Path, public_path: Path, truth_path: Path, entry: dict,
                    result: dict, sources: dict, state_digest: str, schedule_digest: str) -> tuple[dict, Path]:
    result_path, config_path = run / entry["result"], run / entry["config"]
    output = result_path.parent / "audit.json"
    binding = {"offline_source_sha256": sources, "run_manifest_sha256": state_digest,
               "schedule_sha256": schedule_digest,
               "inputs_sha256": {"public": sha(public_path), "truth": sha(truth_path),
                                 "config": sha(config_path), "result": sha(result_path)},
               "checkpoint_artifacts": _checkpoint_binding(result_path, result),
               "exact_determinant_requested": True}
    if output.exists():
        report = read_json(output)
        if report.get("summarizer_binding") != binding:
            raise FileExistsError(f"Existing audit has different source/input hashes: {output}")
        for artifact in report.get("artifact_hashes", {}).values():
            path = Path(artifact["path"])
            if not path.is_file() or sha(path) != artifact["sha256"]:
                raise ValueError(f"An existing audit's input artifact changed: {path}")
        return report, output
    report = audit_trial(public_path, truth_path, config_path, result_path, exact_determinant=True)
    report["summarizer_binding"] = binding
    report["offline_audit_source_sha256"] = sources
    _preserve(output, _json_bytes(report))
    return report, output


def _attempt_interpretation(row: dict) -> str:
    if row["public_valid_recovery"]:
        return row["candidate_classification"]
    if not row["result_present"]:
        return "missing_online_result_evidence"
    coverage = row["truth_orbit_covered"]
    if row["censored"]:
        return "truth_orbit_covered_but_censored" if coverage else "no_truth_orbit_coverage_and_censored"
    return "truth_orbit_covered_completed_without_recovery" if coverage else "no_truth_orbit_coverage_no_recovery"


def _attempt_row(run: Path, group: dict, entry: dict, attempt: dict, truth: dict, public_path: Path,
                 truth_path: Path, sources: dict, state_digest: str, schedule_digest: str,
                 baseline_environment: dict) -> tuple[dict, list[dict], dict | None, Path | None]:
    command_path = (run / entry["command_prefix"]).with_suffix(".command.json")
    command = read_json(command_path) if command_path.is_file() else {}
    result_path = run / entry["result"]
    result = read_json(result_path) if result_path.is_file() else None
    coverage = orbit_coverage(truth, entry["pattern"])
    charged = _nonnegative(attempt.get("charged_wall_seconds"), "attempt charge")
    if "wall_seconds" in command and not _close(charged, _nonnegative(command["wall_seconds"], "capture wall")):
        raise ValueError(f"Attempt charge differs from captured wall time: {command_path}")
    audit, audit_path = (None, None)
    if result is not None:
        audit, audit_path = _audit_or_reuse(run, public_path, truth_path, entry, result, sources,
                                          state_digest, schedule_digest)
        if audit.get("pattern_coverage") != coverage:
            raise ValueError("Independent pattern coverage calculations disagree")
    result = result or {}
    stages = result.get("stages", [])
    final = stages[-1] if stages else {}
    candidate = result.get("found_candidate") or {}
    equivalence = (audit or {}).get("equivalence") or {}
    public_valid = bool(audit and audit["candidate_validation"]["valid"] and result.get("status") == "success")
    command_status = command.get("status", attempt.get("command_status"))
    censored = (not result or command_status in {"timeout", "interrupted", "error", "running"}
                or result.get("status") in {"running", "interrupted", "error"})
    if public_valid:
        censored = False
    environment = result.get("environment")
    environment_matches = (all(environment.get(key) == baseline_environment.get(key) for key in ENVIRONMENT_KEYS)
                           if isinstance(environment, dict) else None)
    if environment_matches is False:
        raise ValueError(f"Worker environment differs from the original baseline: {result_path}")
    row = {"k": group["k"], "dimension": group["dimension"], "pattern_number": entry["pattern_number"],
           "pattern_index": entry["index"], "pattern": entry["pattern"], "attempt_status": attempt["status"],
           "command_status": command_status, "command_exit_code": command.get("exit_code", attempt.get("exit_code")),
           "charged_wall_seconds": charged, "command_wall_seconds": command.get("wall_seconds"),
           "result_present": bool(result), "worker_status": result.get("status"),
           "worker_current_stage": result.get("current_stage"), "worker_wall_seconds": result.get("worker_wall_seconds"),
           "worker_first_valid_seconds": result.get("first_valid_seconds"), "completed_stage_count": len(stages),
           "completed_bkz_tours": sum(stage.get("kind") == "bkz" for stage in stages),
           "last_completed_stage": final.get("stage_id"), "last_completed_beta": final.get("beta"),
           "last_completed_tour": final.get("tour"), "public_valid_recovery": public_valid,
           "candidate_classification": (audit or {}).get("classification"),
           "equivalent_to_truth": bool(equivalence) if public_valid else None,
           "equivalence_shift": equivalence.get("shift"), "equivalence_sign": equivalence.get("sign"),
           "first_success_stage": result.get("first_success_stage"), "first_success_beta": result.get("first_success_beta"),
           "first_success_tour": result.get("first_success_tour"), "candidate_norm_sq": candidate.get("norm_sq"),
           "compatible_truth_rotation_count": coverage["compatible_shift_count"],
           "compatible_signed_truth_representatives": coverage["compatible_signed_representatives"],
           "truth_orbit_covered": coverage["secret_orbit_available"], "censored": bool(censored),
           "trial_complete": bool(audit and audit["trial_complete"]),
           "completed_stage_evidence": bool(audit and audit["completed_stage_evidence"]),
           "audit_integrity_passed": audit.get("integrity_passed") if audit else None,
           "strict_lattice_equivalence_certified": audit.get("strict_lattice_equivalence_certified") if audit else None,
           "audit_failed_checks": audit.get("failed_checks") if audit else None,
           "audit_missing_evidence": audit.get("missing_evidence") if audit else None,
           "environment_matches_baseline": environment_matches,
           "config_path": entry["config"], "result_path": entry["result"],
           "audit_path": str(audit_path.relative_to(run)) if audit_path else None}
    row["outcome_interpretation"] = _attempt_interpretation(row)
    stage_rows = [{"method": "pattern_primal", "k": group["k"], "dimension": group["dimension"],
                   "pattern_number": entry["pattern_number"], "pattern_index": entry["index"],
                   "attempt_status": attempt["status"], "worker_status": result.get("status"),
                   "command_status": command_status, "result_path": entry["result"], **stage} for stage in stages]
    return row, stage_rows, audit, audit_path


def _baseline_row(baseline: dict, public: dict) -> dict:
    return {"method": "plain_primal_baseline", "k": 0, "dimension": 2 * public["n"],
            "group_status": "success", "attempted_pattern_count": 0, "online_trial_count": 1,
            "sampling_seconds": 0.0, "charged_wall_seconds": baseline["online_total_wall_seconds"],
            "success": True, "first_success_stage": baseline["first_success_stage"],
            "first_success_beta": baseline["first_success_beta"], "first_success_tour": baseline["first_success_tour"],
            "successful_pattern_number": None, "baseline_time_over_group_time": 1.0,
            "compatible_truth_attempt_count": 1, "truth_orbit_covered": True,
            "compatible_truth_rotation_count_sum": public["n"], "timeout_count": 0, "censored_trial_count": 0,
            "complete_trial_count": 1, "missing_result_count": 0, "completed_stage_evidence_count": 1,
            "integrity_audit_pass_count": 1, "strict_math_audit_pass_count": 1, "audited_trial_count": 1,
            "integrity_all_available_passed": True, "strict_math_audit_all_attempts_passed": True,
            "group_budget_seconds": None, "budget_overrun_seconds": 0.0,
            "outcome_interpretation": "equivalent_secret"}


def _group_row(group: dict, frozen: dict, attempts: list[dict], baseline_time: float) -> dict:
    sampling = _nonnegative(frozen["sampling_seconds"], "frozen group sampling")
    if not _close(sampling, _nonnegative(group["sampling_seconds"], "state group sampling")):
        raise ValueError("Sampling charge differs from frozen schedule")
    total = _nonnegative(group["charged_wall_seconds"], "group charge")
    if not _close(total, sampling + sum(row["charged_wall_seconds"] for row in attempts)):
        raise ValueError("Group charge does not include exactly all sampling and attempted workers")
    successes = [row for row in attempts if row["public_valid_recovery"]]
    success = successes[0] if successes else None
    if len(successes) > 1 or bool(success) != (group["status"] == "success"):
        raise ValueError("Group stopping status disagrees with the independent public recovery checks")
    if success and (success is not attempts[-1]
                    or group.get("successful_pattern_index") != success["pattern_index"]
                    or group.get("first_success_beta") != success["first_success_beta"]
                    or group.get("first_success_tour") != success["first_success_tour"]):
        raise ValueError("Group did not stop on, or misrecorded, its first public-valid recovery")
    covered = [row for row in attempts if row["truth_orbit_covered"]]
    audited = [row for row in attempts if row["audit_integrity_passed"] is not None]
    strict = [row for row in attempts if row["audit_integrity_passed"] and row["strict_lattice_equivalence_certified"]]
    if success:
        interpretation = success["candidate_classification"]
    elif not covered:
        interpretation = "no_truth_orbit_covered_in_attempted_patterns"
    elif any(row["censored"] for row in covered):
        interpretation = "truth_orbit_covered_but_censored"
    else:
        interpretation = "truth_orbit_covered_completed_without_recovery"
    return {"method": "pattern_primal", "k": group["k"], "dimension": group["dimension"],
            "group_status": group["status"], "attempted_pattern_count": len(attempts), "online_trial_count": len(attempts),
            "sampling_seconds": sampling, "charged_wall_seconds": total, "success": bool(success),
            "first_success_stage": success["first_success_stage"] if success else None,
            "first_success_beta": success["first_success_beta"] if success else None,
            "first_success_tour": success["first_success_tour"] if success else None,
            "successful_pattern_number": success["pattern_number"] if success else None,
            "baseline_time_over_group_time": baseline_time / total if success and total > 0 else None,
            "compatible_truth_attempt_count": len(covered), "truth_orbit_covered": bool(covered),
            "compatible_truth_rotation_count_sum": sum(row["compatible_truth_rotation_count"] for row in attempts),
            "timeout_count": sum(row["command_status"] == "timeout" or row["attempt_status"] == "timeout" for row in attempts),
            "censored_trial_count": sum(row["censored"] for row in attempts),
            "complete_trial_count": sum(row["trial_complete"] for row in attempts),
            "missing_result_count": sum(not row["result_present"] for row in attempts),
            "completed_stage_evidence_count": sum(row["completed_stage_evidence"] for row in attempts),
            "integrity_audit_pass_count": sum(row["audit_integrity_passed"] is True for row in attempts),
            "strict_math_audit_pass_count": len(strict), "audited_trial_count": len(audited),
            "integrity_all_available_passed": all(row["audit_integrity_passed"] for row in audited) if audited else None,
            "strict_math_audit_all_attempts_passed": bool(attempts) and len(strict) == len(attempts),
            "group_budget_seconds": group["group_budget_seconds"],
            "budget_overrun_seconds": max(0.0, total - group["group_budget_seconds"]),
            "outcome_interpretation": interpretation}


def _report(summary: dict) -> str:
    public, policy, groups = summary["parameters"], summary["policy"], summary["groups"]
    baseline_time = summary["baseline"]["online_total_wall_seconds"]
    table = []
    for row in groups:
        stage = ("LLL" if row["first_success_stage"] == "lll" else
                 f"BKZ-{row['first_success_beta']}/第{row['first_success_tour']}轮") if row["success"] else "—"
        ratio = f"{row['baseline_time_over_group_time']:.3f}" if row["baseline_time_over_group_time"] is not None else "—"
        table.append(f"| {row['k']} | {row['dimension']} | {row['attempted_pattern_count']} | "
                     f"{row['charged_wall_seconds']:.6f} | {'是' if row['success'] else '否'} | {stage} | {ratio} | "
                     f"{row['compatible_truth_attempt_count']} | {row['timeout_count']} | {row['complete_trial_count']} | "
                     f"{row['strict_math_audit_pass_count']}/{row['online_trial_count']} |")
    findings = []
    for row in groups[1:]:
        if row["success"]:
            findings.append(f"- k={row['k']}：第 {row['successful_pattern_number']} 个 pattern 恢复公开有效秘密，"
                            f"完整累计时间 {row['charged_wall_seconds']:.6f} 秒，基线时间/本组时间={row['baseline_time_over_group_time']:.3f}。")
        elif not row["truth_orbit_covered"]:
            findings.append(f"- k={row['k']}：实际尝试的 {row['attempted_pattern_count']} 个 pattern 均未覆盖原秘密的旋转轨道，"
                            f"未观察到其他公开有效恢复；其中 {row['censored_trial_count']} 次为删失。此结果不能说明覆盖正确 pattern 后仍无法恢复。")
        elif row["outcome_interpretation"] == "truth_orbit_covered_but_censored":
            findings.append(f"- k={row['k']}：{row['compatible_truth_attempt_count']} 个已尝试 pattern 覆盖原秘密轨道，"
                            "但包含未完成日程的删失尝试；不能据此断言 BKZ 解码失败或需要更大的最小块大小。")
        else:
            findings.append(f"- k={row['k']}：有 {row['compatible_truth_attempt_count']} 个已尝试 pattern 覆盖原秘密轨道，"
                            "在各自完整执行的预定块大小日程内未恢复。")
    return f"""# n={public['n']}、q={public['q']}、重量 ({public['w_f']},{public['w_g']})：pattern primal 与无 pattern 基线

本报告比较同一公钥上的第二种方法与已完成的第一种 primal 基线。第二种方法只加入 f[P]=0 的公开 pattern，没有 tail 猜测；pattern 长度 k={policy['k_values']}，格维数 d=2n-k。公钥 SHA-256 为 `{summary['public_sha256']}`。本次只有一把独立密钥，各 k 和多个 pattern 属于配对尝试，不构成独立密钥成功率。

在线源码、公开输入、全部 pattern 和 worker 配置在求解前冻结。汇总程序先核验冻结来源，并确认八组均结束在线工作，才读取真值进行离线审计。共同的求解设置与无 pattern 基线一致：初始 LLL，BKZ 从 {summary['solver_conditions']['bkz_start']} 开始，步长 {summary['solver_conditions']['bkz_jump']}，每块未成功时严格完成 {summary['solver_conditions']['bkz_max_tours_per_beta']} 轮，即使 clean 也不跳过第二轮；每轮后公开验证候选，首次成功立即停止。配置差异仅为 k、P、pattern 序号及维数/预定 beta 上限。

每组预先最多采样 {policy['max_patterns']} 个 pattern，单 pattern 进程预算为 {policy['per_pattern_seconds']} 秒，每组累计预算为 {policy['group_budget_seconds']} 秒；预留清理时间 {policy['cleanup_reserve_seconds']} 秒。冻结的 beta 上限为 {policy['beta_stop']}，worker 使用 min(该上限,d)。同一尝试序号的各 k 使用同一个公开随机排列的前 k 个位置；没有使用真值选择或重选 pattern。

累计在线时间包含全部预采样成本、失败尝试、超时进程和清理开销；未使用的预采样成本也计入。密钥生成、测试、暂停和离线审计不计入。无 pattern 基线完整在线时间为 {baseline_time:.6f} 秒；速度比只在恢复成功的组中给出，分子为基线时间，分母为本组全部累计成本。超时前的 running 检查点作为删失证据保存，没有改写为完整解码失败。

| k | 格维数 | 实际尝试 P 数 | 累计在线秒数 | 公开恢复 | 首次成功阶段 | 基线/本组 | 覆盖真值轨道的尝试数 | 超时数 | 完整 trial 数 | 严格格审计通过/trial |
| ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(table)}

k=0 的“实际尝试 P 数”为 0，它对应一次无 pattern 的完整基线 trial。覆盖统计按共同 negacyclic 旋转的 shift 计数，正负号另外记录，不把 ± 当作不同独立密钥。

{chr(10).join(findings)}

离线审计独立构造 B0=[[qI,0],[A_C^T,I_C]]，从保存的最终完整阶段推导唯一整数系数，逐行验证 B=U B0，并用精确整数行列式检查 det(U)=±1。候选独立核验三元系数、精确重量、公钥方程、f 可逆性、pattern 零约束和保存基行来源；与原秘密共同旋转 ± 的等价性单列，其他公开有效秘密仍算恢复。仅有初始格或缺失 result 的尝试不被伪造为完成的约减证明。

当前比较完整性状态：**{'通过' if summary['comparison_integrity_passed'] else '存在未通过或缺失证据，请查看 summary.json 和 attempts.csv'}**。首次成功 beta 只针对本次连续 warm-start、每块两轮的轨迹，并非全局最小 beta。未恢复的组受 pattern 覆盖与预算删失共同影响，不能仅依据维数或成功 beta 推断整体优势。

- [结构化汇总](summary.json)、[含基线的组比较](groups.csv)、[每个实际尝试](attempts.csv)、[所有完成阶段](stages.csv)。
- [冻结公开 schedule](schedule.json)、[在线运行清单](run_manifest.json)、[离线源码清单](provenance/offline_sources.json)、[输出 SHA-256 清单](output_manifest.json)。
- 每个实际生成 result 的 pattern 目录保存独立 `audit.json`；缺失 result 的尝试只在汇总中记录缺证状态。
"""


def summarize(run_dir: str | Path) -> dict:
    run = Path(run_dir).resolve()
    state = read_json(run / "run_manifest.json")
    schedule = _terminal_gate(run, state)
    previous_output_manifest = run / "output_manifest.json"
    if previous_output_manifest.exists():
        for relative, digest in read_json(previous_output_manifest).get("output_sha256", {}).items():
            path = run / relative
            if not path.is_file() or sha(path) != digest:
                raise ValueError(f"A previously summarized output changed: {path}")
    frozen = read_json(run / "provenance/frozen_manifest.json")
    public_path = ROOT / schedule["public_source"]
    public = read_json(public_path)
    plan = read_json(run / frozen["inputs"]["plan"]["frozen"])
    baseline = read_json(run / frozen["inputs"]["baseline_summary"]["frozen"])
    baseline_config = plan["baseline"]
    config_checks = _configuration_checks(run, schedule, baseline_config, public)
    baseline_path = EXPERIMENT / "baseline_tour2"
    baseline_result = read_json(baseline_path / "result.json")
    baseline_audit = read_json(baseline_path / "audit.json")
    if (not baseline.get("audit_passed") or not baseline.get("exact_lattice_equivalence_certified")
            or not baseline_audit.get("audit_passed") or baseline_result.get("status") != "success"
            or baseline_result.get("public_sha256") != sha(public_path)
            or baseline_audit.get("artifact_hashes", {}).get("public", {}).get("sha256") != sha(public_path)
            or baseline["online_total_wall_seconds"] != schedule["baseline"]["online_total_wall_seconds"]
            or read_json(baseline_path / "config.json") != baseline_config):
        raise ValueError("The original strict two-tour baseline or public-key identity is inconsistent")
    # This is the first access to the private file, after the complete gate.
    truth_path = EXPERIMENT / "private/instance_01.truth.json"
    if sha(truth_path) != frozen["baseline_truth_sha256"]:
        raise ValueError("Private truth differs from the original frozen baseline")
    truth = read_json(truth_path)
    for artifact in baseline_audit.get("artifact_hashes", {}).values():
        path = Path(artifact["path"])
        if not path.is_file() or sha(path) != artifact["sha256"]:
            raise ValueError(f"Original baseline audit artifact changed: {path}")
    sources = _offline_sources()
    new_outputs: set[Path] = set()
    for relative, digest in sources.items():
        output = run / "provenance/offline_sources" / relative
        _preserve(output, (ROOT / relative).read_bytes())
        if sha(output) != digest:
            raise ValueError("Offline source changed while being frozen")
        new_outputs.add(output)
    source_manifest = run / "provenance/offline_sources.json"
    _preserve(source_manifest, _json_bytes({"schema": "ntru-pattern-offline-source-manifest-v1",
                                          "source_sha256": sources,
                                          "private_access_gate": "All eight groups terminal; frozen schedule verified."}))
    new_outputs.add(source_manifest)
    state_digest, schedule_digest = sha(run / "run_manifest.json"), sha(run / "schedule.json")
    groups = [_baseline_row(baseline, public)]
    attempt_rows, audit_records = [], []
    stage_rows = [{"method": "plain_primal_baseline", "k": 0, "dimension": 2 * public["n"],
                   "pattern_number": None, "pattern_index": None, "attempt_status": "success",
                   "worker_status": "success", "command_status": "finished",
                   "result_path": str(baseline_path / "result.json"), **stage} for stage in baseline_result["stages"]]
    for frozen_group, group in zip(schedule["groups"], state["groups"]):
        group_attempts = []
        for entry, attempt in zip(frozen_group["patterns"], group["attempts"]):
            if attempt["status"] in UNATTEMPTED:
                continue
            row, stages, audit, audit_path = _attempt_row(
                run, group, entry, attempt, truth, public_path, truth_path, sources,
                state_digest, schedule_digest, baseline_result["environment"])
            group_attempts.append(row)
            attempt_rows.append(row)
            stage_rows.extend(stages)
            if audit_path:
                new_outputs.add(audit_path)
                audit_records.append({"k": group["k"], "pattern_number": entry["pattern_number"],
                                      "path": str(audit_path.relative_to(run)), "sha256": sha(audit_path),
                                      "integrity_passed": audit["integrity_passed"],
                                      "strict_lattice_equivalence_certified": audit["strict_lattice_equivalence_certified"]})
        groups.append(_group_row(group, frozen_group, group_attempts, baseline["online_total_wall_seconds"]))
    for name, fields, rows in (("groups.csv", GROUP_FIELDS, groups), ("attempts.csv", ATTEMPT_FIELDS, attempt_rows),
                               ("stages.csv", STAGE_FIELDS, stage_rows)):
        output = run / name
        _preserve(output, _csv_bytes(fields, rows))
        new_outputs.add(output)
    comparison_passed = (all(row["result_present"] and row["audit_integrity_passed"] is True for row in attempt_rows)
                         and all(row["strict_lattice_equivalence_certified"] is True for row in attempt_rows
                                 if row["completed_stage_evidence"]))
    summary = {"schema": "ntru-pattern-primal-tour2-comparison-v1", "status": "online_finished_offline_summarized",
               "online_finished_utc": state.get("finished_utc"), "instance_count": 1,
               "parameters": {"n": public["n"], "q": public["q"],
                              "w_f": public["distribution"]["w_f"], "w_g": public["distribution"]["w_g"]},
               "public_sha256": sha(public_path), "truth_sha256": sha(truth_path),
               "run_manifest_sha256": state_digest, "schedule_sha256": schedule_digest,
               "frozen_manifest_sha256": sha(run / "provenance/frozen_manifest.json"),
               "offline_audit_source_sha256": sources, "policy": schedule["policy"],
               "solver_conditions": baseline_config, "configuration_comparison": config_checks,
               "baseline": {"summary_sha256": sha(baseline_path / "summary.json"),
                            "result_sha256": sha(baseline_path / "result.json"), "audit_sha256": sha(baseline_path / "audit.json"),
                            "online_total_wall_seconds": baseline["online_total_wall_seconds"],
                            "first_success_stage": baseline["first_success_stage"],
                            "first_success_beta": baseline["first_success_beta"],
                            "first_success_tour": baseline["first_success_tour"]},
               "comparison_integrity_passed": comparison_passed, "groups": groups, "attempts": attempt_rows,
               "audits": audit_records, "offline_audit_wall_seconds": sum(
                   read_json(run / item["path"])["audit_wall_seconds"] for item in audit_records),
               "timing_scope": schedule["timing_scope"],
               "censoring_note": "Timeout/running checkpoints are censored observations, not completed decoding failures.",
               "coverage_note": "Truth coverage is post-hoc and was never used to choose patterns or launch workers.",
               "scope": "One reused key. Ratios use full sampling plus all attempted worker costs. First successful beta is trajectory-specific."}
    report_path = run / "REPORT.md"
    _preserve(report_path, _report(summary).encode())
    new_outputs.add(report_path)
    summary["output_sha256_before_summary"] = {str(path.relative_to(run)): sha(path) for path in sorted(new_outputs)}
    summary_path = run / "summary.json"
    _preserve(summary_path, _json_bytes(summary))
    new_outputs.add(summary_path)
    output_manifest = {"schema": "ntru-pattern-primal-summary-output-manifest-v1",
                       "public_sha256": sha(public_path), "run_manifest_sha256": state_digest,
                       "schedule_sha256": schedule_digest, "offline_source_sha256": sources,
                       "output_sha256": {str(path.relative_to(run)): sha(path) for path in sorted(new_outputs)},
                       "note": "This manifest does not include its own hash; original online artifacts are referenced by immutable input/audit hashes."}
    _preserve(run / "output_manifest.json", _json_bytes(output_manifest))
    # Recheck after all offline work: no frozen source/config/public schedule
    # was silently changed by this stage, and online accounting is unchanged.
    verify_frozen(run, state)
    if sha(run / "run_manifest.json") != state_digest or sha(run / "schedule.json") != schedule_digest:
        raise ValueError("Online state or schedule changed during offline summarization")
    if _offline_sources() != sources:
        raise ValueError("Offline auditor/summarizer sources changed during summarization")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = summarize(args.run_dir)
    print(json.dumps({"comparison_integrity_passed": summary["comparison_integrity_passed"],
                      "attempted_patterns": len(summary["attempts"]),
                      "successful_pattern_groups": sum(group["success"] for group in summary["groups"][1:]),
                      "output": str(args.run_dir.resolve() / "summary.json")}, ensure_ascii=False))
    return 0 if summary["comparison_integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
