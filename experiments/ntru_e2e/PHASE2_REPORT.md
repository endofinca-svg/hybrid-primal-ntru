第二阶段首级重量实验报告（2026-10-22，Asia/Shanghai）

已完成本次限定范围的续接：保持第二阶段在线对照框架、参数和原始记录冻结，仅完成 `n64_w4_8` 的独立离线评估与日志整理。原批次中该重量的 12 个在线试验此前已全部尝试，本次新增在线试验为 **0**，没有重试、替换 seed 或扩大预算。

4 个完整对照试验均来自同一实例（keygen seed 3）和 v2 记录版本。Slicer 在这 4 个试验中恢复了有效秘密，但未产生五项基线并集之外的新原始候选或新旋转/符号等价类，也未更早找到首个有效候选。其余 8 个试验为截断记录，不能记作 Slicer 解码失败。结果尚不能证明 Slicer 的必要性或优势，不能外推为统一版本成功率。

本次范围与冻结依据

原始在线批次在 2026-10-21 已经运行其他 `n=64` 重量和 `n=128`；这些记录早于本次限定。此次没有继续执行或评估它们。`n=128` 后续有界探索须单独请求。原 `summary.json` 的全量评估失败状态继续保留；本次完成状态仅见新目录的 `completion.json`。

原在线计划在 key generation 前冻结，哈希为 `3e55f67477e2d2647911c8244b9e3c93049c4e2b9354b3d215e546b43148bcf6`。本次 `scope_frozen.json` 是离线续接前新增的范围冻结，不追溯宣称它早于历史实验。原配置、schedule、源码版本迁移账本和原运行目录全部保持逐字节不变。

| 项目 | 冻结值 |
| --- | --- |
| 环与公钥参数 | `Z_257[x]/(x^64+1)`，`xi=1` |
| 固定三元重量 | `w_f=4, w_g=8`（非零系数数目） |
| pattern 与 tail | `k=2, kappa=4`，每实例 2 个 pattern |
| 非零 tail | 每 trial 预先构造全部 80 个；实际完成提交另行计数 |
| 格维数 | `64+(64-2-4)=122` |
| 配对选择 | `(BKZ beta, sieve dim)=(20,32)` 与 `(25,40)` |
| 其他后端参数 | BKZ 最多 2 loops；16 randomizations；400 max iterations；1 thread |
| seeds | keygen `1,2,3`；求解 `10001,10002,10003` |
| 原在线预算 | 每 trial 30 秒；每实例 150 秒；原完整批次 1800 秒 |

同一实例的两组 BKZ/sieve 选择共用相同的两个 pattern、backend seeds、80 个 tail 和精确公开界。pattern 清单保存在 [schedule_selected.json](runs/phase2_n64_first_weight/schedule_selected.json)。所有开始格筛建库的 8 个 trial 都先完成 direct-tail、LLL rows/Babai、BKZ rows/Babai 五项基线；Slicer 与 BKZ 基线使用相同的最终行基。

试验状态与预算

| 实例 / keygen seed | 记录版本 | 完整 trial | 超时 | 中断删失 | 保守计费秒数 |
| --- | --- | ---: | ---: | ---: | ---: |
| instance_01 / 1 | v1 | 0 | 4 | 0 | 113.074849 |
| instance_02 / 2 | v1 | 0 | 3 | 1 | 114.475694 |
| instance_03 / 3 | v2 | 4 | 0 | 0 | 78.604515 |

合计保守计费 **306.155058 秒**：11 次有测量的命令累计 276.155058 秒，另一次中断按原 30 秒预留全额计费。各 trial、实例和原批次均未超冻结预算。在线时间包含导入、诊断、预处理、建库、解码、候选验证、检查点写入和监督清理；本次离线评估不计入在线预算。

历史 v1 的 8 个 trial 全部截断。已记录的 v1→v2 迁移仅修正检查点 JSON 的缩进编码开销，同时完善版本与中断记账；参数、候选和事件不变，既有 trial 未重跑。7 次有测量的 v1 超时记录中，检查点 I/O 约占 18.5–20.7 秒。v2 的 4 个完整 trial 全属于 seed 3。实例与版本在这些数据中混杂，无法把差异归因为密钥难度、参数或算法性能。

独立评估结果

| 方法 | 已观察到有效候选的实例数 | 完整方法臂 / 12 | 验证通过的候选记录数 |
| --- | ---: | ---: | ---: |
| Direct tail | 0 | 12 | 0 |
| LLL 行向量 | 3 | 11 | 980 |
| LLL Babai | 3 | 11 | 112 |
| BKZ 行向量 | 3 | 11 | 926 |
| BKZ Babai | 3 | 8 | 88 |
| 真实 Slicer | 1 | 4 | 32 |

这些是“已观察到”的计数，允许在截断前保留下来的有效候选；不是完整 trial 的成功率。候选记录会在不同方法、pattern 和参数选择间重复，不能把 **2,138** 条记录解释为独立密钥数。LLL/BKZ 的 rows 和 Babai 均在三个实例中找到与离线真值同时旋转/符号等价的秘密；direct-tail 未找到候选。

每条候选均独立检查公开关系、精确三元重量、`f` 可逆性、系数乘行基、公开格成员关系、partition/tail 重构和精确残差界。Slicer 候选额外核对已结束的真实内核调用、target ID 和调用后的验证事件。12/12 trial 完整性检查通过；2,138/2,138 候选记录通过，失效候选为 0。

真实 Slicer 共开始并完成 4 次 `bdgl_like_sieve` 调用，每次实际提交并完成全部 80 个目标，跨 trial 累计 320 个目标处理记录；不是 320 个独立目标或密钥。各调用产出 8 条候选，合计 32 条。覆盖统计按实例对两个 pattern 和两个参数选择取旋转/符号并集：

| 实例 | pattern 兼容旋转 | 兼容且 tail 非零的旋转 | 实际完成 Slicer 调用覆盖的旋转 |
| --- | ---: | ---: | ---: |
| instance_01 | 128 | 32 | 0 |
| instance_02 | 126 | 24 | 0 |
| instance_03 | 124 | 32 | 32 |

有 8 个 Slicer 方法臂被截断，其中 4 个 trial 的基线也未全部完成。仅 4 个 v2 trial 可作完整增量和时间比较；Slicer 新原始候选数与新等价类数均为 0，首个候选更快的 trial 数为 0/4。

| 完整 trial（均为 instance_03） | 最早基线首候选秒数 | Slicer 首候选秒数 |
| --- | ---: | ---: |
| b20_s32_p0 | 1.898306 | 4.244202 |
| b20_s32_p1 | 1.945984 | 4.295839 |
| b25_s40_p0 | 2.038793 | 4.586060 |
| b25_s40_p1 | 1.896527 | 4.854423 |

此表使用 worker 记录的 `first_valid_seconds`：计入该方法所必需的构造、导入、LLL/BKZ/建库以及到首个候选的操作，排除其他方法臂和检查点 I/O。`total_first_valid_seconds` 是实际串行执行到首个候选的累计时间，仅保存，不作为方法速度对比。截断臂的 `seconds` 只是已有记录，不能当完整耗时。

续接修复与证据

上次中断发生在全量离线评估的 180 秒超时。本次保留原失败命令，并为离线评估增加按 case 筛选、逐 trial 进度和新输出防覆盖；对重复的同一公开候选复用精确验证/等价类计算，每次候选出现仍检查格向量、系数、半径和内核来源。新续接入口只启动离线 evaluate/audit，没有 generate 或 solve 命令；在评估前后核对全部原运行文件、原 provenance、在线源码和编译扩展的 SHA-256。

本次离线 evaluate/audit 及续接总耗时约 12.535 秒。全套回归测试为 **95 passed, 57 subtests passed**。最终审计 `passed=true`、issues 为空，第一阶段 **135** 个保留文件无变更。

- [范围与哈希冻结](runs/phase2_n64_first_weight/scope_frozen.json)、[完成清单](runs/phase2_n64_first_weight/completion.json)。
- [独立评估 JSON](runs/phase2_n64_first_weight/evaluation_phase2.json)、[产物审计](runs/phase2_n64_first_weight/artifact_audit.json)。
- [12 行逐试验 CSV](runs/phase2_n64_first_weight/trials.csv)、[72 行逐方法臂 CSV](runs/phase2_n64_first_weight/arms.csv)。
- [逐试验评估日志](runs/phase2_n64_first_weight/evaluate_phase2.stdout.txt)、[评估命令与资源记录](runs/phase2_n64_first_weight/evaluate_phase2.command.json)。
- [全套测试日志](provenance/phase2_n64_first_weight/preflight_tests.stdout.txt)、[本次续接命令](provenance/phase2_n64_first_weight/offline_continuation.command.json)。
- [完成后恢复检查](provenance/phase2_n64_first_weight/completed_resume_check.command.json)、[本次全部产物与源码哈希清单](provenance/phase2_n64_first_weight/final_manifest.json)。
- [原始预运行冻结](provenance/phase2/plan_frozen.json)、[既有 v1→v2 账本](runs/phase2/instrumentation_revision.json)。

可从仓库根目录重新核验本次完成状态（只读原实验，不启动在线试验）：

```bash
python3 -m experiments.ntru_e2e.complete_phase2_n64 \
  --run-dir experiments/ntru_e2e/runs/phase2 \
  --output-dir experiments/ntru_e2e/runs/phase2_n64_first_weight \
  --resume
```

独立复现离线评估时使用新的 output directory，去掉 `--resume`；旧范围冻结、报告和命令日志都不会覆盖。全量 `run_phase2 --resume` 是原始完整批次入口，本次限定续接使用上述 `complete_phase2_n64`。本报告没有启动后续重量或新的 `n=128` 探索。
