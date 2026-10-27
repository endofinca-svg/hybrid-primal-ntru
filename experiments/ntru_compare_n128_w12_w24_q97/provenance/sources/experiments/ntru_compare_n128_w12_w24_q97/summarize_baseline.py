"""Summarize the completed, independently audited baseline without rerunning it."""
import csv
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    baseline = HERE / "baseline"
    result = json.loads((baseline / "result.json").read_text())
    audit = json.loads((baseline / "audit.json").read_text())
    manifest = json.loads((HERE / "run_manifest.json").read_text())
    plan = json.loads((HERE / "plan.json").read_text())
    if result["status"] != "success" or not audit["audit_passed"] or not audit["strict_lattice_equivalence_certified"]:
        raise ValueError("A successful, exact independent audit is required")
    parameters = audit["parameters"]
    n, q = parameters["n"], parameters["q"]
    w_f, w_g = parameters["w_f"], parameters["w_g"]
    instance, config = plan["instance"], plan["baseline"]
    if any(parameters[name] != instance[name] or parameters[name] != config[name]
           for name in ("n", "q", "w_f", "w_g")):
        raise ValueError("Audited parameters differ from the frozen comparison plan")
    dimension = parameters["dimension"]
    strategy_max_beta = result["environment"]["strategy_max_beta"]
    changed = [name for name, digest in manifest["source_sha256"].items()
               if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    if changed:
        raise ValueError(f"Frozen sources changed: {changed}")
    output = baseline / "summary.json"
    report_path = HERE / "BASELINE_REPORT.md"
    if any(path.exists() for path in (output, report_path, baseline / "stages.csv", baseline / "beta_summary.csv")):
        raise FileExistsError("Preserving prior baseline summaries")
    stages = result["stages"]
    command = manifest["online_command"]
    beta_rows = []
    for beta in dict.fromkeys(stage["beta"] for stage in stages):
        members = [stage for stage in stages if stage["beta"] == beta]
        beta_rows.append({"method": "LLL" if beta is None else "BKZ", "beta": beta,
                          "completed_tours": len(members) if beta is not None else 0,
                          "reduction_wall_seconds": sum(s["stage_wall_seconds"] for s in members),
                          "scan_wall_seconds": sum(s["scan_wall_seconds"] for s in members),
                          "final_minimum_norm_sq": members[-1]["minimum_norm_sq"],
                          "public_valid_recovery": bool(sum(s["candidate_count"] for s in members))})
    summary = {"schema": "ntru-plain-primal-summary-v1", "status": "complete",
               "parameters": audit["parameters"], "instance_count": plan["instance"]["instance_count"],
               "keygen_seed": plan["instance"]["keygen_seed"],
               "solver_seed": plan["baseline"]["solver_seed"],
               "first_success_stage": result["first_success_stage"],
               "first_success_beta": result["first_success_beta"],
               "first_success_tour": result["first_success_tour"],
               "online_total_wall_seconds": command["wall_seconds"],
               "online_total_cpu_seconds": command["cpu_seconds"],
               "online_first_valid_seconds": result["first_valid_seconds"],
               "reduction_total_wall_seconds": sum(s["stage_wall_seconds"] for s in stages),
               "scan_total_wall_seconds": sum(s["scan_wall_seconds"] for s in stages),
               "worker_checkpoint_io_seconds": result["checkpoint_io_seconds"],
               "worker_peak_rss_kib": result["peak_rss_kib"],
               "completed_bkz_tours": sum(s["kind"] == "bkz" for s in stages),
               "candidate_norm_sq": result["found_candidate"]["norm_sq"],
               "candidate_basis_row_index": result["found_candidate"]["row_index"],
               "equivalence": audit["equivalence"],
               "audit_passed": audit["audit_passed"],
               "exact_lattice_equivalence_certified": audit["strict_lattice_equivalence_certified"],
               "frozen_sources_unchanged": True,
               "beta_summary": beta_rows,
               "scope": "One key, one progressive schedule. First successful beta is not globally minimal. Only method 1 of the intended four-method comparison has run."}
    with output.open("x") as handle:
        handle.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    stage_fields = ["stage_id", "kind", "beta", "tour", "stage_wall_seconds", "scan_wall_seconds",
                    "cumulative_wall_seconds", "minimum_norm_sq", "candidate_count", "clean"]
    with (baseline / "stages.csv").open("x") as handle:
        writer = csv.DictWriter(handle, fieldnames=stage_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(stages)
    with (baseline / "beta_summary.csv").open("x") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(beta_rows[0]))
        writer.writeheader()
        writer.writerows(beta_rows)
    table = "\n".join(f"| {row['method']} | {row['beta'] if row['beta'] is not None else '—'} | "
                      f"{row['completed_tours']} | {row['reduction_wall_seconds']:.6f} | "
                      f"{row['scan_wall_seconds']:.6f} | {row['final_minimum_norm_sq']} | "
                      f"{'是' if row['public_valid_recovery'] else '否'} |" for row in beta_rows)
    found = result["found_candidate"]
    equivalence = audit["equivalence"]
    success_label = "初始 LLL" if found["beta"] is None else f"BKZ-{found['beta']} 的第 {found['tour']} 轮"
    text = f"""n={n}、q={q}、重量 ({w_f},{w_g}) 四方法比较：第一项 primal 基线

本次基线已完成，在{success_label}结束后检测到公开验证通过的秘密短向量；离线核验确认它是生成秘密的共同带符号旋转。仅运行第一项方法，其余三项留待后续步骤，共用本目录保存的同一公开实例。

参数为 n={n}、q={q}、环 Z_q[x]/(x^{n}+1)、随机正负号的固定三元重量 w_f={w_f}、w_g={w_g}、xi={config['xi']}。没有 pattern、没有 tail 猜测，格维数 {dimension}，行基 B=[[qI,0],[A^T,I]]，坐标顺序 (g,f)，行列式 q^{n}。keygen seed={instance['keygen_seed']}（只因 f 不可逆而拒绝）、solver seed={config['solver_seed']}；本次独立密钥数为 {instance['instance_count']}。

LLL 使用 {config['lll_method']} 方法、delta={config['lll_delta']}、eta={config['lll_eta']}；整个约减使用 {config['threads']} 个线程、{config['float_type'].upper()} {config['precision_bits']} 位精度。随后沿同一格基从 beta={config['bkz_start']} 开始、步长 {config['bkz_jump']}，上限 {config['bkz_stop']}，每个 beta 最多 {config['bkz_max_tours_per_beta']} 个完整 tour，clean 时提前结束该 beta，每轮后扫描全部基行。使用冻结的 fplll 默认 BKZ2 策略，禁用 auto-abort；beta 超过默认策略覆盖的 {strategy_max_beta} 时预先规定使用无剪枝 SVP。没有结果依赖的 seed 更换、重启或数值精度降级。

完整在线命令实测墙钟时间为 **{command['wall_seconds']:.6f} 秒**，CPU 时间为 **{command['cpu_seconds']:.6f} 秒**。worker 内首个有效候选检测时刻为 **{result['first_valid_seconds']:.6f} 秒**；纯 LLL/BKZ 约减累计 **{summary['reduction_total_wall_seconds']:.6f} 秒**。完整在线时间包含解释器启动、导入、构造、历次约减、扫描和检查点落盘；密钥生成、测试与独立审计均排除。按每轮结束检查，检测时刻不代表向量在内核中最早出现的瞬间。

| 阶段 | beta | 完成轮数 | 约减秒数 | 扫描秒数 | 最后最短行平方范数 | 恢复 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
{table}

候选来自最终保存基的第 {found['row_index']} 行（0-based），平方范数为 {found['norm_sq']}，与秘密同时旋转 shift={equivalence['shift']}、共同符号 sign={equivalence['sign']} 等价。独立审计重新检查三元系数、精确重量、公钥方程、f 可逆性、范数、基行来源；由最终基独立推导系数矩阵 C，逐行精确核对 C*B0=B 与保存的 U，并用整数 Bareiss 算法确认 det(C)={audit['basis']['exact_transformation_determinant']}，因此认证约减前后为同一完整格。审计通过，冻结源码哈希无变化。

本次成功 beta 是上述连续渐增轨迹、每块最多 {config['bkz_max_tours_per_beta']} 轮协议下的首次成功值，不是所有种子、冷启动或其他轮数安排下的最小 beta。后续方法应同时比较完整攻击时间与恢复结果，不能仅比较最后一轮或成功 beta。

- [结构化汇总](baseline/summary.json)、[逐轮 CSV](baseline/stages.csv)、[逐 beta 汇总](baseline/beta_summary.csv)。
- [原始在线结果](baseline/result.json)、[完整在线命令和资源](provenance/baseline_online.command.json)、[事件日志](baseline/events.jsonl)。
- [独立精确审计](baseline/audit.json)、[预先冻结计划](plan.json)、[运行清单](run_manifest.json)。
- [公开实例](public/instance_01.json)、[硬件与软件环境](provenance/environment.json)、[所有完整约减检查点](baseline/checkpoints)。
"""
    with report_path.open("x") as handle:
        handle.write(text)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
