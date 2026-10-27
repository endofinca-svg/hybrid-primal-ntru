# 阶段 2D：独立端到端运行、绝对预算与进程监督

**阶段 2D 验收通过。** 已实际实现 B/D/E 冷启动运行器，完成 226 项自动化测试和一次受限 B/D/E 真实验收。独立计费、实际进程树清理和单试验内存统计都有留存证据。三个真实试验都正常终结、满足截止时间并确认没有活动残留；B 得到公开有效解，D/E 没有得到符合公开重量的解。

本轮仅执行 Prompt 5 授权的阶段 2D，续作于 2026-10-27 完成。目录保留开工日期 `20261026`，不是新的实验重复。未执行 n=128 的约化、GSO、解码或参数扫描，未启动阶段 2E。没有读取私有参考文件内容，没有安装依赖、重建后端、提交或 push。分支仍为 `experiments/ntru-e2e-phase1`，HEAD 为 `452be18c741a2d81c106d3bfe3847428ed57229b`；既有未提交内容保留。

## 新增实现与调用链

新增 6 个实现文件和 6 个测试文件；没有修改旧比较层、阶段 1/2A/2B/2C、封存求解器或后端。完整路径和摘要见 [modified_files.json](modified_files.json)。

| 文件 | 用途 |
| --- | --- |
| `budgets.py` | 不可重置的实验／trial／pattern 绝对截止时间；清理预留包含在总预算内 |
| `supervisor.py` | session 或 PG 归属、PID/starttime 与 pidfd 定向清理、subreaper 回收、RSS 采样 |
| `standalone_worker.py` | 从公开输入冷建基、实际约化、B 非零基行恢复或 D/E 解码、重构和公开验证、耐久事件 |
| `standalone.py` | 公开 pattern 重试、每次新 worker、外部重验实际候选、失败及目标状态保留 |
| `guarded.py` | 正式独立入口；外层 session 监督完整 owner，阻塞的公开加载／复验／保存也在该进程边界中 |
| `summarize.py` | 按 trial 至多一次成功、所有失败和重试计费、零成功成本为 null、成本模式分开 |

正式调用链为：`guarded → 独立 session 内的 owner → standalone.run_trial → 同 session 内独立 PG 的 standalone_worker → build_input_bundle / basis_rows → execute_reduction → 基行检查或 build_backend → preprocess 一次 → decode → reconstruct → validate_public_candidate → owner 再次公开验证 → 两层清理与最终资格判定`。

owner 自己的加载、验证和必要日志也受外层监督。Python 3.10 的内层 PG 使用 `preexec_fn=os.setpgrp`，仅允许 Python 线程数及 `/proc/self/task` 都为 1；支持原生 `process_group=0` 的版本使用该参数。worker 不自行启动子进程、fork 或脱离 session；[worker_boundary_check.json](worker_boundary_check.json) 对实际比较层路径做了静态检查。外层 session 因而包含本轮的 owner 和格 worker，而内层只清理自己的 worker PG。

B 用自身 κ=0 的 primal 基，检查非零约化基行并复用已有 `reconstruct` 和公开验证，不调用零目标 CVP。D/E 使用自己的冷建基、约化、GSO 和数据库状态；每批尾部共享当前 pattern 的一次约化和预处理，没有每目标重新 BKZ。固定基实验仍使用旧 `paired_decode.run_fixed_basis`，不把其阶段成本混进本轮冷启动结果。

## 公开策略、预算与资格

每个 trial 的 guard 入口先冻结单调时钟起点；IPC、owner 导入和数据准备都消耗该预算。owner 继承这一原始起点，不能从导入完成时重算预算。适用的实验、trial 和可选 pattern hard deadline 取最小值；每个新 pattern 继承原来的绝对上限。

本轮每 trial 请求 20 秒，其中清理预留 1 秒，计算截止为原 hard deadline 减 1 秒。外层 guard 预留 0.2 秒，包含在上述 1 秒内，外层开始清理的时间比内层工作截止晚 0.8 秒；不是总预算外再增加宽限。实验总预算 70 秒，三个 trial 请求预算之和 60 秒，不超过本轮小格计算允许的 120 秒。

所有建基、约化、目标生成、预处理、解码、重构、公开验证、必要日志／检查点和清理均实际计费。主成本从 guard trial 入口到最终必要 guard 日志耐久完成；owner/worker 子计时是分解信息，不能重复加到主墙钟上。完整表格、摘要、哈希和离线诊断是明确标注的事后分析。少量 guard 元数据 IO 是协作式且实测计时，超出 hard deadline 会取消成功资格；本实现不承诺通用实时操作系统保证。

公开重试策略为 `random.Random(pattern_seed)` 按顺序采样随机位置集合 P，剩余坐标升序，T 取其中最大的 κ 个。候选不随机排序，使用候选生成器稳定顺序／ID；约化和解码器种子独立显式记录，不根据参考筛掉 pattern。本实现不消费旧输入包采样流程中的附加 backend 随机抽数，因此不将不同采样协议的多次结果混写。重复 P 也记录并实际计费，不默默丢弃。

本轮最大 pattern 尝试数为 1，零尾部使用 skip，候选为全部非零三元尾部；D/E 的 κ=1 因而各有 2 个候选，不是主参数 κ=4 的 80 个。所有方法开启已计时的基行检查；若得到公开有效行，正常提前停止，不为了运行 Slicer 强制继续。当前行检查在**完整配置 schedule 后**执行，不逐 LLL/tour 回调；β4 是本夹具的执行配置和检查时点，不是主参数的最小成功 β。

成功要求实际数组通过公开三元性、重量、范数、可逆性及 `h*f=g mod(q,x^n+1)` 检查，且 worker 观察和 owner 重新验证完成均早于工作截止；还要求正常退出、协议正确、全部必要终结和清理满足 hard deadline。`public_valid_observed`、`success_within_budget`、`execution_status`、`cleanup_verified`、`eligible_for_primary_comparison` 分开保存。一个 trial 最多贡献一次成功，非零退出、孤儿异常、协议错误、晚到候选或清理不明都不能被有效候选掩盖。

事件 JSONL 和检查点 flush/fsync。`pattern_terminal`、`trial_terminal` 和 `guard_terminal` 的资格字段明确为耐久性与最终截止检查前的 provisional 值；`trial.final.json` 是最终投影，不能把早期 provisional 标志当最终成功。外部中断后按最后耐久事件恢复，pending 不补为 completed；未经过 owner 复验的 worker 候选保留为 unvalidated 证据。请求文件写到一半也保留已有 pattern 和 pending。

## 真实小型结果

唯一一次真实检查见 [small.command.json](small.command.json)、[small.execution.json](small.execution.json)、[small.plan.json](small.plan.json) 和 [small_run/experiment.final.json](small_run/experiment.final.json)。启动前冻结 230 个源码、运行时及输入文件的摘要，运行后全部一致。未换夹具、调种、重跑或提高预算。

B 为手工公开 n=4、q=17、h=(-1,1,0,0)、重量 (1,2)，k=1、κ=0、d=7。D/E 为同一手工公开 n=15、q=17、h=1、重量 (2,2)，k=1、κ=1、d=28。全部使用实际 MPFR192 初始 proved LLL 和 β4 一次严格 tour，线程数 1、seed=20261027；每个 worker 都重新执行。没有从 2C 加载约化基或数据库。两类夹具的参数不同，B 与 D/E 的时间不能作为算法加速比较。

以下全部为 `standalone_end_to_end`，每组 1 次 pattern、20 秒预算。时间单位为秒，内存为 MiB。

| 方法／实例 | d | 总墙钟 | 约化 | 额外预处理 | 解码 | 内层／外层清理 | 预算内公开有效 | 成功来源／停止原因 | cleanup_verified | 峰值 RSS |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: |
| B／n4 | 7 | 1.670484 | 0.960236 | 0 | 0 | 0.001625／0.000800 | 是 | basis_row；首次公开有效 | true | 88.148 |
| D／n15 | 28 | 2.708908 | 1.033449 | 0.597055 | 0.024400 | 0.001449／0.001068 | 否 | 2 个候选完成，重量／范数不符 | true | 98.246 |
| E／n15 | 28 | 1.999745 | 0.966394 | 0.000812 | 0.004890 | 0.001408／0.000564 | 否 | 2 个候选完成，重量／范数不符 | true | 88.227 |

清理列为两种嵌套范围的诊断时间，已在主总墙钟中；不能再相加计费。worker 的 GSO、建基、attach 精确检查、重构、公开验证、IO 等其他阶段见 [actual_results.json](actual_results.json) 和原始 result.json；表中未逐列展开的成本仍包含在总墙钟。B 的首次公开有效观察距 guard 起点 1.564303 秒，随后的复验／终结／清理也收费。

D/E 配置、P/C/T、种子、候选顺序、目标列表、ID 映射、原始基及约化基摘要全部相同，但进程、建基和约化费用各自承担。各提交／完成 2 个目标，errored/cancelled/pending/unsubmitted 均为 0。它们均重构出重量 (1,1)、范数平方 2 的向量，虽然满足公开关系和可逆性，却不满足夹具要求的重量 (2,2)、范数平方 4，因此如实拒绝。

D 实际完成一次 DB128 预处理、两次 `RandomizedSlicer.bdgl_like_sieve` 调用；每次观察到一次内核迭代，投影维数 28、单 block、4 buckets、multi_hash=1、nrand=2。`force_kernel_iterations=false`，不是强制继续。数据库构建仍明确使用 `sample_database_only`／`interface_test_only=true`；本轮不证明生产 BDGL 预处理性能，不能据此判断 Slicer 提供加速。E 为真实 `GSO.Mat.babai`，来源为 babai_only；D 来源为 slicer_tail_with_babai_lift，单候选是否由内核独有改进仍为 unknown。

三个试验共启动 3 次、预算内成功 1 次、无成功 2 次、超时 0、异常 0、清理失败 0；三个失败或成功尝试都保留。trial 成本之和 6.379137 秒；完整实验在线墙钟 **6.402510 秒**，含 0.023373 秒全局开销；完整外部命令墙钟 **6.754273 秒**，另含解释器启动和事后汇总输出。D、E 每成功成本均为 **null／未观察到成功**。跨不同夹具的汇总成功成本只是统计实现示例，不是研究结论。

[actual_results.csv](actual_results.csv) 提供可复用结果表。[finalize_evidence.py](finalize_evidence.py) 仅在事后重新进行公开验证、计时／状态／摘要／清理核查，不再次执行格约化或解码。

## 进程与内存证据

[supervision_environment.json](supervision_environment.json)：当前 cgroup mount 只读，`systemctl --user show-environment` 返回 1、无法连接 user bus。因此采用独立 session／PG 和 `/proc` 身份核验，没有安装依赖或修改系统服务。

清理针对 PID/starttime，优先 pidfd；TERM 后只使用剩余预留等待，再 KILL，回收直接及 subreaper 收养的本试验进程，并重新检查活动残留。不使用全局 pkill，不以父进程已退出推断全部已结束。若清理无法确认，保存残留和异常并停止后续试验。未观察到、主动 `setsid()` 的外部后代逃逸不受完整 containment 保证；本轮正式路径不采用这种方式。

17 项真实轻量 supervisor 测试通过；其中 helper 导出了 14 条完整 [process_observations.json](process_observations.json)，全部在预算内清理且 `residual_active_processes=[]`。证据特别包括：

- 父进程响应 TERM、孙进程忽略 TERM：孙进程实际收到 KILL 并回收；不是只生成 kill 命令。
- 父进程提前以 0 退出而子孙仍存活：保留 orphaned_descendants、run_success=false，清理成功不能改写运行异常。
- 外层 owner 被测试 hook 阻塞，内层独立 PG 的 worker 忽略 TERM：外层 TERM owner、KILL worker，核验同 session、不同 PG，实际回收且无活动残留。
- worker 异常／显式非零退出、监督器异常／中断进入清理，另一个独立 session 的 sentinel 不被误杀。

内存主指标为 `sampled_simultaneous_process_tree_rss_bytes`：每次同一 `/proc` 扫描汇总当前各 PID RSS，再对时间取最大，不把不同进程的历史峰值相加，也不逐线程重复计数。真实格试验每 10 ms 采样，范围是 **owner（含内层监督逻辑）＋worker＋同 session 后代，排除外层 guard**；内层 worker 指标另存诊断，不能混作相同范围的列。

B/D/E 主峰值分别为 92,430,336／103,018,496／92,512,256 bytes。扫描不是原子快照，短峰可能漏采，共享页可能重复计入，不等于独占物理内存。`sampling_estimate=true`，`strict_memory_limit=false`，enforcement backend 为 null；没有声称 OOM 限额或根据退出码猜测 OOM。

独立高、低内存试验依次仅分配 40 MiB 和 4 MiB，额外申请低于 128 MiB；各自实测峰值 **49,545,216 → 11,796,480 bytes**（47.25 → 11.25 MiB），第二次没有继承历史高水位。另有父子同时分配的小测试检查同一扫描 RSS 汇总，未使用长期 RUSAGE_CHILDREN。

## 测试、完整性与下一阶段

最终自动化结果为 **226 通过，0 失败，0 错误，0 跳过**，约 7.538 秒，包含既有 149 项和新增 77 项。命令、完整日志和真实过程观测分别见 [tests.command.json](tests.command.json)、[tests.stderr.txt](tests.stderr.txt)、[tests_summary.json](tests_summary.json)。纯状态／模拟后端测试与上述真实小格验收分开计数，不把模拟测试当攻击成功。

第一次统一测试暴露了中断时 request.json 截断不能恢复的问题（225 通过、1 error）。已捕获和记录该情况、保留已有尝试和 pending，再统一测试通过；第一次日志与过程观测保存在 `tests_before_replay_fix*`／`process_observations_before_replay_fix.json`，没有删除失败证据。真实格检查只在修复和全测通过后运行了一次。

[sealed_integrity.json](sealed_integrity.json)：**1810 个受保护文件 SHA-256 全部一致**，另 1 个私有参考文件仅比较 size/mtime_ns/inode，未变化。运行前冻结的 230 个新旧源码／运行时／夹具摘要也全部一致。阶段 1/2A/2B/2C、历史 A/B、封存实现和后端保留。

[main_package_guard.json](main_package_guard.json)：主参数输入包仍为 original_only、reduced=null、execution=not_started，80 个候选、提交 0、完成 0；仅读取状态／清单和摘要，没有传入建基、约化、GSO 或解码入口。

下一阶段仅为 [next_stage_entry.json](next_stage_entry.json) 中的 2E 集成核查提案。实际入口已具备：`python -B -m experiments.ntru_hybrid_comparison.guarded --plan <frozen-public-plan.json> --output <new-output-dir>`。2E 需要核查生产预处理、主参数维数／backend cap、基行检查时点、采样协议和全部待冻结预算，形成可审阅的主参数配置和执行命令；本轮不冻结或运行它，也不自动启动 2E。
