# 阶段 1：已有实验与历史结果核查

核查日期：2026-10-26（Asia/Shanghai）。本次只读取已有配置、manifest、CSV、结果和审计文件；没有运行 solver/evaluate，没有读取 private 真值文件，没有修改原实验数据。本文件中的“独立验证通过”指已有独立审计记录的状态，不表示本次重新执行了独立评估。原文件哈希与预算监督核查由本阶段主报告单独列出。

## 1. 当前 q=97 的 A/B 证据

参数为 n=128、q=97、固定三元重量 HW(f)=12、HW(g)=24，环为 Z_97[x]/(x^128+1)，xi=1。公钥关系 h f = g mod (97,x^128+1)。A 使用 k=κ=0，B 使用零 pattern、κ=0；所有候选均来自约化基行扫描，不是多目标解码。初始行基坐标顺序为 (g,f_C)，维数 256-k。

依据：[严格两轮基线报告](../../ntru_compare_n128_w12_w24_q97/BASELINE_TOUR2_REPORT.md) 第 7—11 行；[基线配置](../../ntru_compare_n128_w12_w24_q97/baseline_tour2/config.json) 第 3—27 行；[pattern 报告](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/REPORT.md) 第 3、33 行。

| 方法 / k | 格维数 | 累计用时 / 秒 | pattern 尝试数 | 超时数 | 首次成功 BKZ β / tour | 已有独立验证 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| A / 0 | 256 | 473.106938 | — | 0 | 35 / 1 | 同一共同带符号负循环旋转等价类 |
| B / 12 | 244 | 428.704435 | 1 | 0 | 33 / 2 | 同上 |
| B / 24 | 232 | 346.670167 | 1 | 0 | 33 / 1 | 同上 |
| B / 32 | 224 | 418.597052 | 1 | 0 | 33 / 2 | 同上 |
| B / 38 | 218 | 766.178857 | 2 | 1 | 32 / 2 | 同上 |
| B / 40 | 216 | 286.524902 | 1 | 0 | 32 / 2 | 同上 |
| B / 42 | 214 | 1396.734484 | 3 | 2 | 34 / 2 | 同上 |
| B / 44 | 212 | 1170.123727 | 3 | 2 | 32 / 1 | 同上 |
| B / 48 | 208 | 1367.151340 | 3 | 2 | 34 / 2 | 同上 |
| B / 52 | 204 | 1177.363451 | 3 | 2 | 32 / 1 | 同上 |
| B / 56 | 200 | 11902.332053 | 26 | 25 | 30 / 2 | 同上 |

表中数据来自 [comparison.csv](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/comparison.csv) 第 2—12 行，以及 [REPORT.md](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/REPORT.md) 第 8—18 行。A 的 attempt_count=1 表示一次普通 primal 命令，不表示运行了一个零 pattern。

### 1.1 k=40 是否包含完整成本

包含本轮实际发生的所有成本。k=40 的唯一尝试是 pattern_index=0 / pattern_number=1，无失败或超时重试：

- 完整在线命令：286.49779491899244 秒。
- 组级公开采样、配置、监督等额外开销：0.027106893991003744 秒。
- 合计：286.52490181298344 秒。

依据：[comparison.csv](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/comparison.csv) 第 7 行，字段 `charged_wall_seconds`、`worker_charged_seconds`、`sampling_and_overhead_seconds`；[attempts.csv](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/attempts.csv) 中 `k=40` 的唯一行，其 `charged_wall_seconds=command_wall_seconds=286.49779491899244`，`source_phase=current`。上述计时包含启动、建基、LLL/BKZ、基行扫描、公开验证、检查点和清理；密钥生成、测试、暂停及独立审计排除在在线攻击成本之外。

因此，“本次该实例的 k=40 比严格两轮普通 primal 快约 1.651 倍”有已有计时支持；不能据此宣称普遍最优 k、总体成功率或完整混合方法加速。

### 1.2 失败、超时和阶段时间

fresh 批次有 44 次 pattern 尝试：10 次公开合法候选停止，34 次超时。十组累计费用之和为 19260.380467950075 秒；44 条命令费用之和为 19255.719617337003 秒；额外组级开销为约 4.660851 秒。超时命令合计 16021.775569760997 秒，也计入相应组成本，没有仅统计成功命令。

这 44 项在 [attempts.csv](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/attempts.csv) 第 2—45 行均来自 `source_phase=current` 和同一个 fresh run；均有 result、`environment_matches_baseline=True`、`protocol_passed=True`。所有组均没有预算超支或预算阻塞。可在 [run_manifest.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/run_manifest.json) 的 `groups[*].attempts`、`groups[*].charged_wall_seconds`、`groups[*].budget_overrun_seconds`，以及 comparison CSV 的相应列复核。

**34 次超时是删失观测，不能表述为 34 个不兼容或“猜错”的 pattern。** 它们没有产生公开合法候选，但当前预算内未完成搜索不等于秘密不存在。成功的十次有独立等价类审计；34 次超时没有由这些成功审计文件变成经过真值判定的失败。

[stages.csv](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/stages.csv) 有 572 条阶段记录：538 条完整记录、34 条中断阶段；其中 44 条 LLL、528 条 BKZ。完成阶段的约化/扫描计时与中断区间必须分开。中断区间含信号和检查点开销；仅有最后检查点时还可能只是下界，不能将其当作精确、完成的约化阶段时长。说明见 [REPORT.md](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/REPORT.md) 第 37—43 行及 [summary.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/summary.json) 第 2306—2311 行。

### 1.3 独立验证与结束时间

十份成功审计均有 `audit_passed=true`、`classification=equivalent_secret`、`strict_lattice_equivalence_certified=true`，且 `failed_checks=[]`、`missing_evidence=[]`。审计验证公开关系、三元重量、f 可逆性、pattern 零约束、保存基行来源、精确整数变换关系和行列式 ±1；等价类要求 f/g 共同带符号负循环旋转。

例：[k040_pattern001.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/audit/k040_pattern001.json) 第 33、161、303、306、353 行；其 `candidate_validation.valid=true`，平方范数36。该 pattern 的 `compatible_shift_count=1` 是事后诊断（同文件第 339—350 行），不应解释为在线先挑出了一个兼容 pattern。

全部在线命令于北京时间 2026-10-23 00:29:32.983 结束；统一独立评估于 00:29:33.435 启动、00:30:58.304 完成，退出码0。依据：[run_manifest.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/run_manifest.json) 的 `finished_utc`；[evaluate.command.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/preflight_fresh_473s/evaluate.command.json) 第 10—19 行。UTC 字段已换算为 Asia/Shanghai。

### 1.4 不能从当前 A/B 推出的结论

1. **仅一个独立公钥。** 十个 k、多个 pattern、旋转或符号都不是新密钥样本。k=40 是在这个实例扫描后选出的点，尚无新实例确认。
2. **A/B 没有相同的事前预算。** A 原先没有时间上限，473.11 秒是观测用时取整；B 每 pattern 473.11 秒，每组 473.11×k 秒。这支持已完成命令用时比较，不构成相同时间预算下完成率比较。
3. **A/B 尚未分别优化约化预算。** 都从 β=30 起、步长1，未成功则严格两完整 tour，clean 不提前切换 β；成功立即停。首次成功 β 是该渐增轨迹上的首次命中，不能说是绝对最低可用 β，也不能免费忽略此前 β 的成本。
4. **没有检验 Slicer 增量。** κ=0、T为空，target_count=0、Slicer调用0；候选为独立 primal 的非零短向量基行搜索。D/E、C/F 还未在 q=97 的这些结果中接通。
5. **pattern 采样类型已固定。** 由 SHA256(`pattern-primal-v1:10001:index`) 派生种子，shuffle全部128坐标后取前k个、排序、去重；这是随机位置集合而非连续区间。同一 index 不同 k 的集合因此嵌套。

依据：[policy.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/policy.json) 第 15—35 行；[summary.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/summary.json) 第 77—105、2268—2311 行。

### 1.5 历史重跑边界

历史 `baseline/` 最多八轮的运行单独保留，不属于 `baseline_tour2/` 严格两轮基线，其时间没有计入上述 A。严格两轮运行从原始基重新执行 LLL，没有复用旧约化基。依据：[BASELINE_TOUR2_REPORT.md](../../ntru_compare_n128_w12_w24_q97/BASELINE_TOUR2_REPORT.md) 第 3—9 行。

fresh 的十组根据当时用户要求从 pattern 序号0重启，前一轮旧 pattern 实验不并入 fresh 的时间与结果。依据：[policy.json](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/policy.json) 第 30 行和 [REPORT.md](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/REPORT.md) 第 39—41 行。本轮没有删除或重写任何历史数据。

## 2. 旧 ntru_e2e 数据可复用范围

旧 `ntru_e2e` 使用 q=257，而当前 A/B 使用 q=97。它们可提供流程、接口、阶段瓶颈和比较设计的证据，但不能合并为同一参数下的性能结果或独立样本。

| 已有数据 | 核实结果 | 可以复用 / 不能宣称 |
| --- | --- | --- |
| phase1，n=32、重量2/4 | seed1用未补丁后端超时；seed2/3用UID补丁后端，真实Slicer调用、整数lifting及独评通过；直接Babai、LLL/BKZ也发现有效候选 | 可复用端到端数学/接口和调用来源证据；不是统一版本“2/3成功率”，没有证明Slicer必要性或性能增益 |
| phase2，n64_w4_8 | 3个密钥、12 trial；8截断，4完整均属于seed3和v2；Slicer未新增基线并集以外的原始候选或等价类，也未更早成功 | 可复用同一约化基对照与计时模板；不能把8截断当8次解码失败或据此宣称优势 |
| phase2，n64_w6_12 | 3个密钥、12 trial，全部v2、finished/complete；保守在线费用226.301617406007秒；原始Slicer候选记录138条 | 已有完整原始数据，后续可在授权阶段离线补评，无需先重跑；尚未见该范围独立评估文件，不能按候选数差异声称新增类或完成率 |
| phase2，n64_w8_16 | 3个密钥、12 trial，全部v2、finished/complete；在线费用245.523380455998秒；原始Slicer候选记录116条 | 同上；这24条更高重量trial是可复用的未评完数据，不是需要自动启动的新实验 |
| phase2，n128_w8_16 / n128_w12_24 | 各1个密钥、各4 trial；分别累计179.658832424004和172.248789709003秒；全部在LLL/BKZ setup超时；实际Slicer内核启动0、候选0，已有独评完整性检查通过 | 可复用预处理瓶颈和候选覆盖的事后诊断；不能称Slicer解码成功率0/8，也不能并入q97性能表 |

具体证据：

- [config_phase1.json](../../ntru_e2e/config_phase1.json) 第 3—19 行固定 n32、q257、重量2/4、配置与预算；[phase1 summary](../../ntru_e2e/runs/phase1/summary.json) 第 18—19 行明确混合后端版本口径。首级报告 [REPORT.md](../../ntru_e2e/REPORT.md) 第 35—41 行说明环、重构和共享预处理，第 43—64 行给真实调用、独评及Babai/基行诊断结论。
- [config_phase2.json](../../ntru_e2e/config_phase2.json) 第 3—16 行固定 q257、k2、κ4、β/s配置，第 19—23 行列各case及预算，第 25—31 行列配对和全成本规则。β与Slicer维数在旧配置中耦合变化，不能把差异单独归因于某一个参数。
- [PHASE2_REPORT.md](../../ntru_e2e/PHASE2_REPORT.md) 第 15—25、29—37、41—71 行给 n64_w4_8 的参数、删失、独立候选验证和无增量结论。累计费用306.155057891996秒包含一次缺少命令测量的中断，按30秒原上限全额计费。实例与v1/v2版本混杂，不能从其差异估计总体性能。
- [phase2 summary](../../ntru_e2e/runs/phase2/summary.json) 的 `instances` 按 `case_id` 聚合：n64_w6_12 的3实例从第 784、1040、1296 行开始；n64_w8_16 从第1552、1808、2064行开始；各有4个v2 complete trial。上述原始候选数按各 `trials[*].result` 中 `arms.slicer.candidates` 长度求和，仅是重复出现的记录数。
- [旧n128分析](../../ntru_e2e/runs/phase2_n128_analysis/ANALYSIS.md) 第 3—27 行及 [trials.csv](../../ntru_e2e/runs/phase2_n128_analysis/trials.csv) 第 2—9 行证明8次预处理超时与0内核调用；[artifact_audit.json](../../ntru_e2e/runs/phase2_n128_analysis/artifact_audit.json) 第 18—30 行记录8份自洽审计、候选0、passed=true。这是现存证据完整性，不是完成解码。

## 3. 对后续阶段的实际入口

已有证据支持复用精确负循环代数、公开输入和独立评估分离、toy真实Slicer接口、q97 A/B基线及成本账本。尚未支持 q97 的 D/E 或 C/F 完整混合恢复、Slicer相对同基Babai的增量、各自优化后的净加速、相同预算完成率或新密钥泛化。

阶段2可据此补齐同一保存约化基和相同目标列表的D/E配对、候选原始/去重/实处理计数、分段完整成本、零tail覆盖损失、来源归因和进程树硬超时。阶段3仍应在实现核查和配置冻结后另行启动；本次没有启动后续在线实验或补做旧数据独立评估。
