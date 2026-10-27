"""Final, post-online audit of the time-limited pattern search and its prefix.

This program is separate from the frozen first-batch summarizer.  No truth
file is opened until the extension verifier certifies that all eight groups
have finished online work by public recovery or their k-dependent time cap.  The original
batch is a charged prefix of this search, not a final failure outcome.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import summarize as prefix_summary
from .run import DEFAULT_K, EXPERIMENT, HERE, ROOT, paired_permutation, read_json, sha


GROUP_FIELDS = prefix_summary.GROUP_FIELDS + [
    "prefix_attempted_pattern_count", "extension_attempted_pattern_count", "prefix_charged_wall_seconds",
    "extension_sampling_seconds", "extension_worker_seconds", "candidate_classification", "equivalent_to_truth",
    "total_worker_seconds", "stopping_policy",
    "duplicate_patterns_rejected", "duplicate_sampling_seconds",
    "total_budget_seconds", "remaining_seconds", "public_valid_recovery", "public_first_success_stage",
    "public_first_success_beta", "public_first_success_tour", "public_successful_pattern_number",
    "unused_prepared_pattern_count",
]
ATTEMPT_FIELDS = ["source_phase", "source_run", "global_attempt_number", "pattern_wall_budget_seconds",
                  "reserved_charge_seconds", "process_timeout_seconds", "sampling_seconds",
                  "original_secret_recovery"] + prefix_summary.ATTEMPT_FIELDS
STAGE_FIELDS = ["source_phase", "source_run", "global_attempt_number"] + prefix_summary.STAGE_FIELDS


def _gate(run: Path) -> tuple[dict, dict, dict, dict, Path]:
    # Importing verification code does not load private truth or run reduction.
    from .extend_until_success import verify_extension

    schedule, state = verify_extension(run)
    if (state.get("status") != "online_finished"
            or state.get("completion") not in {"all_groups_recovered", "all_groups_terminal"}):
        raise ValueError("Final truth audit requires online_finished and all groups terminal")
    groups, frozen_groups = state.get("groups", []), schedule.get("groups", [])
    if (len(groups) != 8 or len(frozen_groups) != 8
            or {group.get("k") for group in groups} != set(DEFAULT_K)
            or [group.get("k") for group in groups] != [group.get("k") for group in frozen_groups]):
        raise ValueError("All eight requested groups must have matching verified extension snapshots")
    for group, scheduled in zip(groups, frozen_groups):
        if group.get("status") not in {"success", "budget_exhausted"}:
            raise ValueError("All eight groups must be successful or budget-exhausted before truth audit")
        if group.get("dimension") != scheduled.get("dimension"):
            raise ValueError("Extension group dimension differs from its verified schedule")
        attempts, entries = group.get("attempts", []), scheduled.get("patterns", [])
        if len(attempts) != len(entries):
            raise ValueError("Extension attempt inventory differs from its verified schedule")
        for attempt, entry in zip(attempts, entries):
            if (attempt.get("index") != entry.get("index")
                    or attempt.get("status") not in {"success", "timeout", "interrupted", "error", "exhausted", "unused"}):
                raise ValueError("An extension worker is unfinished or unaccounted")
            if attempt["status"] == "unused":
                command_path = (run / entry["command_prefix"]).with_suffix(".command.json")
                if (run / entry["result"]).exists() or command_path.exists():
                    raise ValueError("An unused extension pattern has unaccounted worker evidence")
    binding = read_json(run / "prefix_binding.json")
    prefix = Path(binding["prefix_run"]).resolve()
    policy = read_json(run / "policy.json")
    return schedule, state, binding, policy, prefix


def _sources() -> dict[str, str]:
    sources = prefix_summary._offline_sources()
    for path in (HERE / "summarize_search.py", HERE / "extend_until_success.py"):
        sources[str(path.relative_to(ROOT))] = sha(path)
    return sources


def _verify_preserved_outputs(run: Path) -> None:
    path = run / "output_manifest.json"
    if path.exists():
        manifest = read_json(path)
        for relative, digest in manifest.get("output_sha256", {}).items():
            output = run / relative
            if not output.is_file() or sha(output) != digest:
                raise ValueError(f"A previous final output changed: {output}")
        for absolute, digest in manifest.get("external_output_sha256", {}).items():
            output = Path(absolute)
            if not output.is_file() or sha(output) != digest:
                raise ValueError(f"A previous prefix audit output changed: {output}")


def _freeze_offline_sources(run: Path, sources: dict[str, str]) -> set[Path]:
    outputs = set()
    for relative, digest in sources.items():
        target = run / "provenance/final_offline_sources" / relative
        prefix_summary._preserve(target, (ROOT / relative).read_bytes())
        if sha(target) != digest:
            raise ValueError("Offline source changed during final provenance capture")
        outputs.add(target)
    manifest = run / "provenance/final_offline_sources.json"
    prefix_summary._preserve(manifest, prefix_summary._json_bytes({
        "schema": "ntru-pattern-search-offline-sources-v1", "source_sha256": sources,
        "truth_gate": "Verified extension is online_finished; all eight groups are successful or budget-exhausted."}))
    outputs.add(manifest)
    return outputs


def _extension_configuration_checks(run: Path, schedule: dict, baseline_config: dict, public: dict,
                                    policy: dict) -> dict:
    common = {name: value for name, value in baseline_config.items()
              if name not in prefix_summary.ALLOWED_CONFIG_CHANGES}
    count = 0
    for group in schedule["groups"]:
        k, dimension = group["k"], 2 * public["n"] - group["k"]
        if dimension != group["dimension"]:
            raise ValueError("Extension pattern dimension is inconsistent")
        for entry in group["patterns"]:
            path = run / entry["config"]
            config = read_json(path)
            observed = {name: value for name, value in config.items()
                        if name not in prefix_summary.ALLOWED_CONFIG_CHANGES}
            permutation, material, digest = paired_permutation(entry["index"], public["n"])
            pattern = sorted(permutation[:k])
            if (observed != common or config.get("k") != k or config.get("pattern") != pattern
                    or entry.get("pattern") != pattern or config.get("pattern_index") != entry["index"]
                    or config.get("bkz_stop") != min(policy["beta_stop"], dimension)
                    or entry.get("seed_material") != material or entry.get("seed_sha256") != digest
                    or entry.get("pattern_number") != entry["index"] + 1
                    or sha(path) != entry["config_sha256"]):
                raise ValueError(f"Extension worker/pattern differs from the fixed comparison conditions: {path}")
            count += 1
    return {"extension_configurations_checked": count, "same_public_instance": True,
            "baseline_configuration_equal_except": sorted(prefix_summary.ALLOWED_CONFIG_CHANGES),
            "all_other_worker_conditions_match_baseline": True,
            "public_paired_patterns_regenerated_and_verified": True}


def _phase_row(row: dict, phase: str, run: Path, ordinal: int) -> dict:
    value = {**row, "source_phase": phase, "source_run": str(run), "global_attempt_number": ordinal}
    value["original_secret_recovery"] = bool(row["public_valid_recovery"] and row["equivalent_to_truth"] is True)
    for field in ("config_path", "result_path", "audit_path"):
        if value.get(field):
            path = Path(value[field])
            value[field] = str(path if path.is_absolute() else run / path)
    return value


def _prefix_stages(prefix: Path, rows: list[dict]) -> list[dict]:
    stages = []
    for row in rows:
        if not row["result_present"]:
            continue
        result_path = Path(row["result_path"])
        result = read_json(result_path)
        for stage in result.get("stages", []):
            stages.append({"source_phase": "prefix", "source_run": str(prefix),
                           "global_attempt_number": row["global_attempt_number"],
                           "method": "pattern_primal", "k": row["k"], "dimension": row["dimension"],
                           "pattern_number": row["pattern_number"], "pattern_index": row["pattern_index"],
                           "attempt_status": row["attempt_status"], "worker_status": row["worker_status"],
                           "command_status": row["command_status"], "result_path": str(result_path), **stage})
    return stages


def _combined_group(group: dict, prefix_group: dict, rows: list[dict], scheduled: dict,
                    baseline_time: float, policy: dict) -> dict:
    prefix_cost = prefix_summary._nonnegative(prefix_group["charged_wall_seconds"], "prefix cost")
    if not prefix_summary._close(group["prefix_charged_wall_seconds"], prefix_cost):
        raise ValueError("Extension did not preserve the complete prefix charge")
    extension_sampling = prefix_summary._nonnegative(group["extension_sampling_seconds"], "extension sampling")
    extension_worker = prefix_summary._nonnegative(group["extension_worker_seconds"], "extension workers")
    total = prefix_summary._nonnegative(group["charged_wall_seconds"], "full group charge")
    cap = prefix_summary._nonnegative(group["total_budget_seconds"], "k-dependent total cap")
    remaining = prefix_summary._nonnegative(group["remaining_seconds"], "remaining total allowance")
    overrun = prefix_summary._nonnegative(group["budget_overrun_seconds"], "recorded budget overrun")
    if (not prefix_summary._close(cap, policy["unit_group_seconds"] * group["k"])
            or not prefix_summary._close(remaining, max(0.0, cap - total))
            or not prefix_summary._close(overrun, max(0.0, total - cap))):
        raise ValueError("Group cap/remaining/overrun differs from the frozen k-dependent rule")
    extension_rows = [row for row in rows if row["source_phase"] == "extension"]
    accepted_sampling = sum(entry["sampling_seconds"] for entry in scheduled["patterns"])
    if (not prefix_summary._close(extension_sampling, scheduled["sampling_seconds"])
            or accepted_sampling > extension_sampling + 1e-7
            or not prefix_summary._close(extension_worker, sum(row["charged_wall_seconds"] for row in extension_rows))
            or not prefix_summary._close(total, prefix_cost + extension_sampling + extension_worker)):
        raise ValueError("Final group charge omits or double-counts sampling/attempted worker costs")
    successes = [row for row in rows if row["public_valid_recovery"]]
    if (len(successes) > 1 or bool(successes) != (group["status"] == "success")
            or successes and successes[0] is not rows[-1]):
        raise ValueError("Final public stopping status disagrees with independently validated candidates")
    public_success = successes[0] if successes else None
    if public_success and (group.get("first_success_beta") != public_success["first_success_beta"]
            or group.get("first_success_tour") != public_success["first_success_tour"]):
        raise ValueError("Final first-success metadata differs from the audited recovery")
    success = public_success if public_success and public_success["equivalent_to_truth"] is True else None
    audited = [row for row in rows if row["audit_integrity_passed"] is not None]
    strict = [row for row in rows if row["audit_integrity_passed"] is True
              and row["strict_lattice_equivalence_certified"] is True]
    prefix_count = sum(row["source_phase"] == "prefix" for row in rows)
    sampling = prefix_group["sampling_seconds"] + extension_sampling
    worker = sum(row["charged_wall_seconds"] for row in rows)
    if not prefix_summary._close(total, sampling + worker):
        raise ValueError("Final aggregate wall time differs from all true sampling and execution charges")
    covered = [row for row in rows if row["truth_orbit_covered"]]
    if success:
        interpretation = "equivalent_secret"
    elif public_success:
        interpretation = "other_valid_short_secret_original_secret_not_confirmed"
    elif not covered:
        interpretation = "no_truth_orbit_covered_in_attempted_patterns"
    elif any(row["censored"] for row in covered):
        interpretation = "truth_orbit_covered_but_censored"
    else:
        interpretation = "truth_orbit_covered_completed_without_recovery"
    return {"method": "pattern_primal_k_time_cap", "k": group["k"], "dimension": group["dimension"],
            "group_status": group["status"], "attempted_pattern_count": len(rows), "online_trial_count": len(rows),
            "prefix_attempted_pattern_count": prefix_count, "extension_attempted_pattern_count": len(extension_rows),
            "prefix_charged_wall_seconds": prefix_cost, "extension_sampling_seconds": extension_sampling,
            "extension_worker_seconds": extension_worker, "sampling_seconds": sampling, "total_worker_seconds": worker,
            "charged_wall_seconds": total, "success": bool(success),
            "first_success_stage": success["first_success_stage"] if success else None,
            "first_success_beta": success["first_success_beta"] if success else None,
            "first_success_tour": success["first_success_tour"] if success else None,
            "successful_pattern_number": success["pattern_number"] if success else None,
            "baseline_time_over_group_time": baseline_time / total if success and total else None,
            "public_valid_recovery": bool(public_success),
            "public_first_success_stage": public_success["first_success_stage"] if public_success else None,
            "public_first_success_beta": public_success["first_success_beta"] if public_success else None,
            "public_first_success_tour": public_success["first_success_tour"] if public_success else None,
            "public_successful_pattern_number": public_success["pattern_number"] if public_success else None,
            "candidate_classification": public_success["candidate_classification"] if public_success else None,
            "equivalent_to_truth": public_success["equivalent_to_truth"] if public_success else None,
            "compatible_truth_attempt_count": sum(row["truth_orbit_covered"] for row in rows),
            "truth_orbit_covered": any(row["truth_orbit_covered"] for row in rows),
            "compatible_truth_rotation_count_sum": sum(row["compatible_truth_rotation_count"] for row in rows),
            "timeout_count": sum(row["command_status"] == "timeout" or row["attempt_status"] == "timeout" for row in rows),
            "censored_trial_count": sum(row["censored"] for row in rows),
            "complete_trial_count": sum(row["trial_complete"] for row in rows),
            "missing_result_count": sum(not row["result_present"] for row in rows),
            "completed_stage_evidence_count": sum(row["completed_stage_evidence"] for row in rows),
            "integrity_audit_pass_count": sum(row["audit_integrity_passed"] is True for row in rows),
            "strict_math_audit_pass_count": len(strict), "audited_trial_count": len(audited),
            "integrity_all_available_passed": all(row["audit_integrity_passed"] for row in audited) if audited else None,
            "strict_math_audit_all_attempts_passed": len(strict) == len(rows),
            "duplicate_patterns_rejected": group.get("duplicate_patterns_rejected", 0),
            "duplicate_sampling_seconds": max(0.0, extension_sampling - accepted_sampling),
            "unused_prepared_pattern_count": sum(attempt["status"] == "unused" for attempt in group["attempts"]),
            "group_budget_seconds": cap, "total_budget_seconds": cap, "remaining_seconds": remaining,
            "budget_overrun_seconds": overrun, "outcome_interpretation": interpretation,
            "stopping_policy": "Replace unsuccessful public random patterns until public recovery or the k-dependent total time cap; original-secret equivalence audited only after all groups finish."}


def _report(summary: dict) -> str:
    parameters, baseline = summary["parameters"], summary["baseline"]
    policy = summary["search_policy"]
    table, findings = [], []
    for group in summary["groups"]:
        stage = ("LLL" if group["first_success_stage"] == "lll" else
                 f"BKZ-{group['first_success_beta']} 第{group['first_success_tour']}轮") if group["success"] else "—"
        ratio = group["baseline_time_over_group_time"]
        ratio_label = f"{ratio:.3f}" if ratio is not None else "—"
        cap_label = f"{group['total_budget_seconds']:.2f}" if group["total_budget_seconds"] is not None else "—"
        remaining_label = f"{group['remaining_seconds']:.6f}" if group["remaining_seconds"] is not None else "—"
        table.append(f"| {group['k']} | {group['dimension']} | {cap_label} | {group['charged_wall_seconds']:.6f} | "
                     f"{remaining_label} | {group['attempted_pattern_count']} | {'是' if group['success'] else '否'} | "
                     f"{stage} | {ratio_label} | "
                     f"{group['compatible_truth_attempt_count']} | {group['timeout_count']} | "
                     f"{group['strict_math_audit_pass_count']}/{group['online_trial_count']} |")
        if group["k"]:
            if group["success"]:
                outcome = (f"第 {group['successful_pattern_number']} 号 pattern 在 {stage} 恢复原秘密共同带符号旋转，"
                           f"基线/本组={ratio_label}。")
            elif group["public_valid_recovery"]:
                outcome = "找到其他公开有效短秘密，但离线核验不属于原秘密的共同带符号旋转，因此不计为用户要求的成功。"
            elif not group["truth_orbit_covered"]:
                outcome = "已尝试 pattern 均未覆盖原秘密旋转轨道，在预算边界停止；不能据此评价正确 pattern 下的恢复能力。"
            elif group["outcome_interpretation"] == "truth_orbit_covered_but_censored":
                outcome = "已覆盖原秘密旋转轨道，但有关尝试包含超时删失；在预算边界未观察到恢复，不能解释为完整解码失败。"
            else:
                outcome = "已覆盖原秘密旋转轨道，在完整执行的预定日程中未恢复，最终达到本组预算边界。"
            findings.append(f"- k={group['k']}：{outcome} 共尝试 {group['attempted_pattern_count']} 个不同 P，"
                            f"累计 {group['charged_wall_seconds']:.6f}/{group['total_budget_seconds']:.2f} 秒，"
                            f"剩余 {group['remaining_seconds']:.6f} 秒；首轮 {group['prefix_attempted_pattern_count']} 次，"
                            f"续跑 {group['extension_attempted_pattern_count']} 次，超时 {group['timeout_count']} 次。")
    return f"""# pattern primal：每组按 k 设置累计时间上限的最终比较

同一公钥、n={parameters['n']}、q={parameters['q']}、重量 (HW(f),HW(g))=({parameters['w_f']},{parameters['w_g']})。八组 k={summary['k_values']} 均已结束在线工作，其中 **{summary['original_secret_recovery_count']} 组恢复原秘密的共同带符号旋转**，公开有效候选共 {summary['public_valid_recovery_count']} 组；格维数 d=2n-k。无 pattern 的 k=0 基线完整在线时间为 **{baseline['online_total_wall_seconds']:.6f} 秒**。本次只有一把固定密钥，多个 pattern 和 k 不是独立密钥样本。

每组总时间上限为 **{policy['unit_group_seconds']}×k 秒**，计入首轮 `paired_patterns_473s` 的全部时间。未恢复时继续换不同的公开随机 pattern，直到在线公开恢复或达到累计预算边界；不足一次清理预留时间时结束，剩余可能略大于零。首轮已公开恢复的组保留结果。首轮未恢复只是阶段状态，最终结论来自本报告；失败尝试、超时和全部采样成本均未重置或遗漏。

每次从原始 pattern 格重新做 LLL，BKZ 从 {summary['solver_conditions']['bkz_start']} 开始、步长 {summary['solver_conditions']['bkz_jump']}，每块未成功严格完成 {summary['solver_conditions']['bkz_max_tours_per_beta']} 轮，即使 clean 也继续第二轮。每轮后只用公钥检查三元性、精确重量、f 可逆性和公钥方程；首次成功立即停止。除 pattern、对应维数上限和预注册进程预算外，worker 设置与原基线一致。累计成本包含公开采样、所有进程墙钟和超时清理；暂停、测试、源码冻结、密钥生成与离线审计不计入。

| k | 格维数 | 总上限/秒 | 已用/秒 | 剩余/秒 | 实际 P 数 | 原秘密旋转恢复 | 首次成功阶段 | 基线/本组 | 覆盖尝试数 | 超时数 | 严格格审计通过/trial |
| ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
{chr(10).join(table)}

{chr(10).join(findings)}

速度比以基线完整时间除以本组全部成本，大于 1 表示本次更快；不能用最后一个成功 pattern 的耗时替代总成本。首次成功 beta 属于本次渐进 warm-start 轨迹，每块两轮，不是全局最小 beta。k=0 没有 pattern，所以 P 数为 0、trial 数为 1。

用户要求的成功仅指候选与生成秘密满足相同 shift 和 sign 的共同 negacyclic 旋转；其他公开有效秘密单独报告，不计入成功或速度比。在线 worker 只用公钥停止，真值等价性在全部在线工作结束后独立检查，不回馈搜索控制。

所有 pattern 都由冻结的公开随机排列规则产生，没有通过真值筛选。程序先验证前缀证据、扩展账本，并确认全部八组都已公开恢复或预算耗尽，才读取真值。覆盖仅事后统计，不参与换 pattern 或预算决策；按 shift 计数，正负号另列。超时的 running 检查点继续标记为删失，不作为完整解码失败。

独立离线审计重新构造每个 B0，验证保存阶段 B=U B0 和精确 det(U)=±1，再核验候选的公开关系、pattern 零约束和基行来源。没有完成阶段或缺失 result 的尝试只保留实际证据，不伪造完整格证明。最终比较完整性：**{'通过' if summary['comparison_integrity_passed'] else '存在失败或缺证，请检查 attempts.csv'}**。

继续搜索按公开 pattern 序号使用冻结的单次预算：初始 {policy['base_pattern_seconds']} 秒，每 {policy['budget_block']} 个序号翻倍，单次上限 {policy['max_pattern_seconds']} 秒，并受本组剩余总时间限制。进程 timeout 再扣除预留清理时间，完整收费另见 attempts.csv。重复的 P 只用公开信息排除，其采样成本也计入；已准备而未启动的 P 不冒充实际尝试。完整政策保存在 policy.json 和 summary.json。

- [最终结构化结果](summary.json)、[含 k=0 的比较](groups.csv)、[首轮及扩展全部尝试](attempts.csv)、[全部已完成阶段](stages.csv)。
- [扩展运行清单](run_manifest.json)、[扩展 schedule](schedule.json)、[冻结首轮证据](prefix_binding.json)、[预算政策](policy.json)。
- [最终离线源码](provenance/final_offline_sources.json)、[完整输出哈希](output_manifest.json)。
- 首轮阶段报告位于 `{summary['prefix_run']}/REPORT.md`，它的未恢复状态只描述首轮配额；本文件是全部搜索完成后的最终结果。
"""


def summarize(run_dir: str | Path) -> dict:
    run = Path(run_dir).resolve()
    schedule, state, binding, policy, prefix = _gate(run)
    _verify_preserved_outputs(run)
    state_digest, schedule_digest = sha(run / "run_manifest.json"), sha(run / "schedule.json")
    sources = _sources()
    outputs = _freeze_offline_sources(run, sources)
    # This call is intentionally below the final all-groups-terminal gate.
    # It independently audits every true prefix attempt and creates a separate
    # phase report; its result is a charged component of this final report.
    first = prefix_summary.summarize(prefix)
    prefix_schedule = read_json(prefix / "schedule.json")
    public_path = ROOT / prefix_schedule["public_source"]
    public = read_json(public_path)
    truth_path = EXPERIMENT / "private/instance_01.truth.json"
    if sha(public_path) != first["public_sha256"] or sha(truth_path) != first["truth_sha256"]:
        raise ValueError("Public/truth input changed after the independently audited prefix phase")
    truth = read_json(truth_path)
    baseline_result = read_json(EXPERIMENT / "baseline_tour2/result.json")
    config_checks = _extension_configuration_checks(run, schedule, first["solver_conditions"], public, policy)
    rows_by_k = {k: [] for k in DEFAULT_K}
    prefix_state = read_json(prefix / "run_manifest.json")
    prefix_entries = {(group["k"], entry["index"]): entry for group in prefix_schedule["groups"]
                      for entry in group["patterns"]}
    prefix_attempts = {(group["k"], attempt["index"]): attempt for group in prefix_state["groups"]
                       for attempt in group["attempts"]}
    for row in first["attempts"]:
        value = _phase_row(row, "prefix", prefix, len(rows_by_k[row["k"]]) + 1)
        entry, attempt = prefix_entries[(row["k"], row["pattern_index"])], prefix_attempts[(row["k"], row["pattern_index"])]
        value.update(pattern_wall_budget_seconds=prefix_schedule["policy"].get("per_pattern_seconds"),
                     reserved_charge_seconds=attempt.get("reserved_charge_seconds"),
                     process_timeout_seconds=attempt.get("process_timeout_seconds"),
                     sampling_seconds=entry["sampling_seconds"])
        rows_by_k[row["k"]].append(value)
    baseline_group = {**first["groups"][0], "prefix_attempted_pattern_count": 0,
                      "extension_attempted_pattern_count": 0, "prefix_charged_wall_seconds": 0.0,
                      "extension_sampling_seconds": 0.0, "extension_worker_seconds": 0.0,
                      "candidate_classification": "equivalent_secret", "equivalent_to_truth": True,
                      "total_worker_seconds": first["baseline"]["online_total_wall_seconds"],
                      "duplicate_patterns_rejected": 0, "duplicate_sampling_seconds": 0.0,
                      "total_budget_seconds": None, "remaining_seconds": None, "public_valid_recovery": True,
                      "public_first_success_stage": first["baseline"]["first_success_stage"],
                      "public_first_success_beta": first["baseline"]["first_success_beta"],
                      "public_first_success_tour": first["baseline"]["first_success_tour"],
                      "public_successful_pattern_number": None, "unused_prepared_pattern_count": 0,
                      "stopping_policy": "First public-valid recovery in the no-pattern baseline."}
    groups, audit_records, unused_prepared = [baseline_group], [], []
    stage_rows = [{"source_phase": "baseline", "source_run": str(EXPERIMENT / "baseline_tour2"),
                   "global_attempt_number": 1, "method": "plain_primal_baseline", "k": 0,
                   "dimension": 2 * public["n"], "pattern_number": None, "pattern_index": None,
                   "attempt_status": "success", "worker_status": "success", "command_status": "finished",
                   "result_path": str(EXPERIMENT / "baseline_tour2/result.json"), **stage}
                  for stage in baseline_result["stages"]]
    prefix_groups = {group["k"]: group for group in first["groups"]}
    for scheduled, group in zip(schedule["groups"], state["groups"]):
        k = group["k"]
        stage_rows.extend(_prefix_stages(prefix, rows_by_k[k]))
        for entry, attempt in zip(scheduled["patterns"], group["attempts"]):
            if attempt["status"] == "unused":
                unused_prepared.append({"k": k, "pattern_number": entry["pattern_number"], "index": entry["index"],
                                        "config": entry["config"], "sampling_seconds": entry["sampling_seconds"],
                                        "status": "unused", "unused_reason": attempt.get("unused_reason")})
                continue
            row, stages, audit, audit_path = prefix_summary._attempt_row(
                run, group, entry, attempt, truth, public_path, truth_path, sources,
                state_digest, schedule_digest, baseline_result["environment"])
            ordinal = len(rows_by_k[k]) + 1
            row = _phase_row(row, "extension", run, ordinal)
            row.update(pattern_wall_budget_seconds=entry["wall_budget_seconds"],
                       reserved_charge_seconds=attempt.get("reserved_charge_seconds"),
                       process_timeout_seconds=attempt.get("process_timeout_seconds"),
                       sampling_seconds=entry["sampling_seconds"])
            rows_by_k[k].append(row)
            stage_rows.extend({**stage, "source_phase": "extension", "source_run": str(run),
                               "global_attempt_number": ordinal,
                               "result_path": str(run / stage["result_path"])} for stage in stages)
            if audit_path:
                outputs.add(audit_path)
                audit_records.append({"k": k, "pattern_number": entry["pattern_number"],
                                      "path": str(audit_path.relative_to(run)), "sha256": sha(audit_path),
                                      "integrity_passed": audit["integrity_passed"],
                                      "strict_lattice_equivalence_certified": audit["strict_lattice_equivalence_certified"]})
        if len({row["pattern_index"] for row in rows_by_k[k]}) != len(rows_by_k[k]):
            raise ValueError("A pattern index was retried or double-counted across prefix and extension")
        groups.append(_combined_group(group, prefix_groups[k], rows_by_k[k], scheduled,
                                      first["baseline"]["online_total_wall_seconds"], policy))
    attempt_rows = [row for k in DEFAULT_K for row in rows_by_k[k]]
    for name, fields, rows in (("groups.csv", GROUP_FIELDS, groups), ("attempts.csv", ATTEMPT_FIELDS, attempt_rows),
                               ("stages.csv", STAGE_FIELDS, stage_rows)):
        output = run / name
        prefix_summary._preserve(output, prefix_summary._csv_bytes(fields, rows))
        outputs.add(output)
    passed = (first["comparison_integrity_passed"]
              and all(row["result_present"] and row["audit_integrity_passed"] is True for row in attempt_rows)
              and all(row["strict_lattice_equivalence_certified"] is True for row in attempt_rows
                      if row["completed_stage_evidence"]))
    prefix_manifest_path = prefix / "output_manifest.json"
    external_outputs = {str(prefix / relative): digest
                        for relative, digest in read_json(prefix_manifest_path)["output_sha256"].items()}
    external_outputs[str(prefix_manifest_path)] = sha(prefix_manifest_path)
    public_count = sum(group["public_valid_recovery"] for group in groups[1:])
    original_count = sum(group["success"] for group in groups[1:])
    original_success_rows = [row for row in attempt_rows if row["original_secret_recovery"]]
    original_certified = (all(row["audit_integrity_passed"] and row["strict_lattice_equivalence_certified"]
                              for row in original_success_rows) if original_success_rows else None)
    summary = {"schema": "ntru-pattern-primal-k-time-cap-summary-v1", "status": "complete",
               "completion": "all_groups_terminal_and_posthoc_audited", "online_completion": state["completion"],
               "instance_count": 1,
               "online_finished_utc": state.get("finished_utc"), "k_values": list(DEFAULT_K),
               "parameters": first["parameters"], "public_sha256": first["public_sha256"],
               "truth_sha256": first["truth_sha256"], "baseline": first["baseline"],
               "solver_conditions": first["solver_conditions"], "search_policy": policy,
               "configuration_comparison": config_checks, "prefix_run": str(prefix),
               "prefix_summary_sha256": sha(prefix / "summary.json"),
               "prefix_binding_sha256": sha(run / "prefix_binding.json"), "policy_sha256": sha(run / "policy.json"),
               "run_manifest_sha256": state_digest, "schedule_sha256": schedule_digest,
               "offline_audit_source_sha256": sources, "groups": groups, "attempts": attempt_rows,
               "extension_audits": audit_records, "prefix_audits": first["audits"],
               "unused_prepared_patterns": unused_prepared,
               "comparison_integrity_passed": bool(passed), "all_eight_groups_terminal": True,
               "public_valid_recovery_count": public_count, "original_secret_recovery_count": original_count,
               "other_public_valid_recovery_count": public_count - original_count,
               "all_eight_groups_publicly_recovered": public_count == 8,
               "all_eight_groups_original_secret_recovered": original_count == 8,
               "all_eight_successes_mathematically_certified": original_count == 8 and original_certified is True,
               "all_observed_original_successes_mathematically_certified": original_certified,
               "offline_audit_wall_seconds": first["offline_audit_wall_seconds"] + sum(
                   read_json(run / item["path"])["audit_wall_seconds"] for item in audit_records),
               "timing_scope": "All prefix sampling and workers plus all extension sampling and workers, including failed attempts and timeout cleanup; pauses, tests, source freezing and offline auditing excluded.",
               "truth_access_scope": "Only after verified final all-groups success/budget-exhausted termination; coverage never controlled pattern generation, budgets or continuation.",
               "user_success_definition": "Candidate is the same simultaneous signed negacyclic rotation of truth f and g; other public-valid secrets are reported separately and excluded from success speed ratios.",
               "prefix_scope": "The first batch is an intermediate charged phase, not the final task outcome.",
               "censoring_note": "Timed-out running checkpoints are censored observations, including groups ending at their total cap; they are not complete decoding failures.",
               "scope": "One fixed key; paired public patterns; first successful beta is trajectory-specific."}
    report_path = run / "REPORT.md"
    prefix_summary._preserve(report_path, _report(summary).encode())
    outputs.add(report_path)
    summary["output_sha256_before_summary"] = {str(path.relative_to(run)): sha(path) for path in sorted(outputs)}
    summary_path = run / "summary.json"
    prefix_summary._preserve(summary_path, prefix_summary._json_bytes(summary))
    outputs.add(summary_path)
    manifest = {"schema": "ntru-pattern-search-final-output-manifest-v1",
                "public_sha256": first["public_sha256"], "run_manifest_sha256": state_digest,
                "schedule_sha256": schedule_digest, "prefix_binding_sha256": sha(run / "prefix_binding.json"),
                "policy_sha256": sha(run / "policy.json"), "offline_source_sha256": sources,
                "output_sha256": {str(path.relative_to(run)): sha(path) for path in sorted(outputs)},
                "external_output_sha256": external_outputs,
                "note": "This manifest excludes its own hash. Frozen prefix online evidence is bound separately."}
    prefix_summary._preserve(run / "output_manifest.json", prefix_summary._json_bytes(manifest))
    final_schedule, final_state, _, _, _ = _gate(run)
    if (sha(run / "run_manifest.json") != state_digest or sha(run / "schedule.json") != schedule_digest
            or final_schedule != schedule or final_state != state or _sources() != sources):
        raise ValueError("Online evidence or offline sources changed during final summarization")
    if sha(public_path) != first["public_sha256"] or sha(truth_path) != first["truth_sha256"]:
        raise ValueError("Public/truth input changed during final summarization")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = summarize(args.run_dir)
    print(json.dumps({"all_eight_groups_terminal": True,
                      "public_valid_recovery_count": summary["public_valid_recovery_count"],
                      "original_secret_recovery_count": summary["original_secret_recovery_count"],
                      "comparison_integrity_passed": summary["comparison_integrity_passed"],
                      "all_eight_successes_mathematically_certified": summary["all_eight_successes_mathematically_certified"],
                      "attempted_patterns": len(summary["attempts"]),
                      "output": str(args.run_dir.resolve() / "summary.json")}, ensure_ascii=False))
    return 0 if summary["comparison_integrity_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
