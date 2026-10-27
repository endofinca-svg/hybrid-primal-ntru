# 阶段 1：后端、运行环境与测试证据核查

核查日期：2026-10-26（北京时间）。本文件属于新协议的阶段 1 证据，不是旧目录中“phase2”实验的继续运行。本轮仅阅读源码、配置及已有非私密记录，执行轻量 imports、版本、SHA256 和只读 Git 核查；未读取 private，未运行 solver、evaluate、编译或测试，未修改旧实现及旧结果。

## 1. 当前环境及构建一致性

当前后端可导入，固定源码和已加载编译物与已有 completion 环境快照一致。

| 检查项 | 本轮结果 | 证据入口 |
|---|---|---|
| Python 入口 | `experiments/ntru_e2e/.deps/venv/bin/python`，Python 3.10 环境 | [runtime.json L2](../../ntru_e2e/.deps/runtime.json#L2)、[历史 Python 版本 L2](../../ntru_e2e/provenance/completion/environment.json#L2) |
| g6k 固定 HEAD | `bd72687b2ebbaff16fb692259f83b327bbcd9586`，本轮只读 `git rev-parse HEAD` 实查相符 | [bootstrap 固定 commit L14](../../ntru_e2e/bootstrap.py#L14)、[runtime L5](../../ntru_e2e/.deps/runtime.json#L5) |
| fpylll / NumPy / Cython | 本轮 imports：`0.6.4 / 2.2.6 / 3.2.4` | [历史版本快照 L39](../../ntru_e2e/provenance/completion/environment.json#L39) |
| cysignals / pytest | 本轮读取版本：`1.12.5 / 9.0.2` | [历史版本快照 L43](../../ntru_e2e/provenance/completion/environment.json#L43) |
| g6k 模块路径 | `g6k`、`g6k.siever`、`g6k.slicer` 实际来自 `.deps/g6k_hybrid/g6k` | [历史路径 L30](../../ntru_e2e/provenance/completion/environment.json#L30) |
| 后端源码快照 | 历史列出的 11 个文件 SHA256，本轮逐项比对全部一致 | [source_files_sha256 L47](../../ntru_e2e/provenance/completion/environment.json#L47) |
| 编译扩展 | 当前两个 `.so` SHA256 与 completion 快照全部一致 | [extension_sha256 L60](../../ntru_e2e/provenance/completion/environment.json#L60) |
| 编译投影维数上限 | **64**，不等于整个格维数或 κ | [实际 configure 参数 L426](../../ntru_e2e/.deps/g6k_hybrid/config.status#L426)、[历史上限 L64](../../ntru_e2e/provenance/completion/environment.json#L64) |
| 线程设置 | runtime 对 OpenMP / BLAS 等设置为 1；adapter 的 Siever/Slicer 也使用 1 线程 | [runtime L9](../../ntru_e2e/.deps/runtime.json#L9)、[Phase2Decoder 参数 L184](../../ntru_e2e/phase2_adapter.py#L184)、[Slicer 线程 L249](../../ntru_e2e/slicer_adapter.py#L249) |

当前扩展 SHA256：

```text
g6k.siever ab8bf82195da61ab254e09dcc6bb71b2919ac78ec9953220e510fefb66a22bc3
g6k.slicer 17218c3bebe3212c282a5af31e81ebf231f76db0e3c3447e6c90b652d3a6b610
```

本轮核对的 11 个源码为 `README.rst`、`setup.py`、`configure.ac`、`hybrid_attack.py`、`run_prog_hyb.py`、`tailBDD.py`、`g6k/slicer.pyx`、`kernel/slicer.cpp`、`kernel/hash_table.inl`、`utils.py`、`LICENSE`。这些结果验证所列文件与快照一致，不构成对后端全部依赖的完全封装证明。

源码相对固定 HEAD 的跟踪改动为 `kernel/hash_table.inl`；另有未跟踪的配置日志及 Cython 生成文件。本轮未修改它们。记录的 UID 补丁把 `int16_t(128*y)` 改为 `llround(1048576*y)`，提高小目标散列精度，保持符号对称，见[补丁 L9](../../ntru_e2e/provenance/upstream_source.patch#L9)。

两份记录补丁均为：

```text
e17fe06b6fb3d8695fcac6cfdb280a354080dae3cff4e5532cde81ec2599d34d
```

当前 `kernel/hash_table.inl` SHA256 为 `557212569e09b9550d000452669a1fffb57e91519e18f43454cd74790408bed1`，与[completion 记录 L56](../../ntru_e2e/provenance/completion/environment.json#L56)一致。补丁副本见[原记录](../../ntru_e2e/provenance/upstream_source.patch#L1)、[completion 副本](../../ntru_e2e/provenance/completion/upstream_source.patch#L1)。

最初 bootstrap 构建未带 `--force`，见[初次命令 L107](../../ntru_e2e/provenance/bootstrap_commands.json#L107)。之后存在真实的强制重建成功记录：`setup.py build_ext --inplace --force -j2`，exit 0，见[rebuild_force 命令 L5](../../ntru_e2e/provenance/completion/rebuild_force.command.json#L5)、[结果 L16](../../ntru_e2e/provenance/completion/rebuild_force.command.json#L16)。本轮 `.so` 哈希匹配该后续环境快照，不能仅因最初命令缺 `--force` 就认定当前编译物未包含补丁。

当前 `.deps/runtime.json` 仍是旧结构，未包含新版 bootstrap 会生成的 patch/provenance 字段。新版代码入口见[bootstrap L128](../../ntru_e2e/bootstrap.py#L128)；后续构建证据已在 completion 中单独保存。新协议需要冻结完整运行环境证据；[run_in_env.py L13](../../ntru_e2e/run_in_env.py#L13) 当前只读环境并 `execve`，不会自动校验源码或扩展哈希。venv 使用共享基础环境依赖，见[runtime L17](../../ntru_e2e/.deps/runtime.json#L17)，并非依赖完全封装的快照。

## 2. 共享约化与 Slicer 预处理能力

`Phase2Decoder` 已具备公平固定基阶段比较的重要组件，可复用：

- 初始化仅执行 LLL；`reduce_bkz()` 对同一 GSO 执行一次 BKZ，随后额外 LLL 规范化，见[LLL 初始化 L76](../../ntru_e2e/phase2_adapter.py#L76)、[BKZ L148](../../ntru_e2e/phase2_adapter.py#L148)。
- `iter_babai()` 对各目标使用同一个 `self.G.babai`，没有每个尾部猜测重跑 BKZ，见[逐目标 Babai L117](../../ntru_e2e/phase2_adapter.py#L117)。
- `build_database()` 在基线之后才构造一次 G6K 格向量数据库，见[建库 L170](../../ntru_e2e/phase2_adapter.py#L170)。初始化和建库后均检查基没有变化，见[初始化检查 L183](../../ntru_e2e/phase2_adapter.py#L183)、[建库后检查 L201](../../ntru_e2e/phase2_adapter.py#L201)。
- 多次 `decode()` 可共享固定的 `self.g6k` 数据库；每次 `_run_groups()` 新建目标数据库及随机化，不重新约化固定格，见[目标 DB 实例化 L245](../../ntru_e2e/slicer_adapter.py#L245)。目标 DB 开销和拒绝后的 singleton 重试均属于实际解码成本，见[重试 L351](../../ntru_e2e/slicer_adapter.py#L351)。
- 保存 `lll` / `bkz` / `slicer` 基快照，见[worker L327](../../ntru_e2e/solve_phase2.py#L327)、[L336](../../ntru_e2e/solve_phase2.py#L336)、[L346](../../ntru_e2e/solve_phase2.py#L346)。

当前只检查 `28 <= sieve_dim <= dim`，见[adapter L45](../../ntru_e2e/phase2_adapter.py#L45)；未预先对编译上限 64 做相应校验。新协议配置验证需要明确区分 `d`、κ、β 和实际投影维数，并在进入编译后端前验证上限。

另一个跨方法比较缺口是数值与约化策略口径尚未统一。旧混合 `Phase2Decoder` 使用 `long double` GSO 和默认 `LLL.Reduction`，见[phase2_adapter.py L83](../../ntru_e2e/phase2_adapter.py#L83)，BKZ 调用为单个 β 的 `BKZ.Param(..., max_loops=..., flags=BKZ.MAX_LOOPS)`，未显式传入策略文件或逐 β 的严格 tour 循环，见[L157](../../ntru_e2e/phase2_adapter.py#L157)。q=97 的普通 primal 和 k=40 pattern primal 则配置为 **MPFR 192 位、proved LLL、δ=0.99、η=0.51、β 从 30 逐步加 1、每个 β 严格两 tour，clean 不跳过第二 tour**，并约定策略来源，见[普通 primal 配置 L12](../../ntru_compare_n128_w12_w24_q97/baseline_tour2/config.json#L12)、[pattern 配置 L12](../../ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern/k040/pattern001/config.json#L12)。实现加载策略并记录其 SHA256，见[普通 primal L160](../../ntru_compare_n128_w12_w24_q97/solve_baseline_tour2.py#L160)、[pattern L172](../../ntru_compare_n128_w12_w24_q97/method2_pattern/solve.py#L172)；实际逐 tour 调用见[普通 primal L195](../../ntru_compare_n128_w12_w24_q97/solve_baseline_tour2.py#L195)、[pattern L207](../../ntru_compare_n128_w12_w24_q97/method2_pattern/solve.py#L207)。因此，当前“D/E 内部共用基”的保证不能外推成 B 与 D/E 跨方法的约化条件一致。阶段 2 应提供公共约化配置，或将这些数值实现/策略配置显式分组对照；在统一或量化其影响之前，不能把跨组差异直接归因为 Slicer 的收益。

## 3. 当前是全批阶段实验，尚非新协议的独立端到端比较器

现有 worker 顺序运行 `direct_tail → lll_rows → lll_babai → bkz_rows → bkz_babai → slicer`，见[solve_phase2.py L311](../../ntru_e2e/solve_phase2.py#L311)、[L328](../../ntru_e2e/solve_phase2.py#L328)、[L338](../../ntru_e2e/solve_phase2.py#L338)、[L347](../../ntru_e2e/solve_phase2.py#L347)。即使某个 arm 已有公开合法候选，也继续运行余下 arm；对应测试明确要求成功后继续全部 arm，见[test_phase2_worker.py L108](../../ntru_e2e/tests/test_phase2_worker.py#L108)。

Babai generator 能逐目标返回，见[phase2_adapter.py L117](../../ntru_e2e/phase2_adapter.py#L117)，但 worker 的 [babai_arm L281](../../ntru_e2e/solve_phase2.py#L281) 不使用验证成功结果停止。Slicer 每次把整批目标送入 C++，完成后才遍历残差，见[调用 L298](../../ntru_e2e/slicer_adapter.py#L298)、[残差遍历 L307](../../ntru_e2e/slicer_adapter.py#L307)。接口允许调用方自行分批，但当前 worker 没有冻结的小批大小、首成功停止或目标覆盖计数策略。

`first_valid_seconds` 是方法 setup 加该 arm 已发生的操作时间，见[计时起点 L185](../../ntru_e2e/solve_phase2.py#L185)、[首次候选 L250](../../ntru_e2e/solve_phase2.py#L250)。整条 worker 墙钟还包括其他 arm，不能直接当作独立 D/E 的完整端到端耗时。固定基全批阶段实验应单独标注；端到端方法应各有自己的计时、预算和首成功策略。

## 4. Slicer 半径、0 次迭代与覆盖解释

NTRU adapter 使用公开完整平方半径除以 `gh_sub` 构造投影上界，见[公开归一化半径 L280](../../ntru_e2e/slicer_adapter.py#L280)。每个候选随后仍接受精确整数完整半径检查，见[adapter L319](../../ntru_e2e/slicer_adapter.py#L319)，并进行公开 NTRU 重构检查，见[worker L235](../../ntru_e2e/solve_phase2.py#L235)。

内核在每次迭代前检查排序后第 `floor(Nt * saturation_scalar)` 个残差长度；若已在半径内，会在 **0 次迭代**直接返回，见[kernel/slicer.cpp L513](../../ntru_e2e/.deps/g6k_hybrid/kernel/slicer.cpp#L513)。因此：

- “Slicer 调用完成”不能自动归因为筛解码增益；原始投影残差本身可能已满足阈值。
- 饱和条件统计的是残差条数，不是每个 target 都已解码成功，也不是独立重构完成率。
- 新结果需同时报告 `itercount_slicer`、实际进入和完成的目标、Slicer 与 Babai 的候选交集以及 Slicer 新增候选。
- `_safe_saturation()` 已解决该内核下标的边界问题，见[adapter L50](../../ntru_e2e/slicer_adapter.py#L50)，但此修正不改变饱和语义。

后端中名为 `dist_threshold` 的变量只见于[run_prog_hyb.py L141](../../ntru_e2e/.deps/g6k_hybrid/run_prog_hyb.py#L141)，由 GSO/GH 计算，后续未使用。当前 NTRU adapter 实际设置的是 `set_proj_error_bound`，见[adapter L287](../../ntru_e2e/slicer_adapter.py#L287)，不能把这个未使用变量当作当前阈值。

上游 debug 实验在[run_prog_hyb.py L134](../../ntru_e2e/.deps/g6k_hybrid/run_prog_hyb.py#L134)投影真实误差并计算其半径，随后调用 debug 方法。NTRU adapter 未调用该 debug 管线；新实现必须保持公开边界，不能直接把上游 debug 实验入口当作在线求解器复用。

## 5. 没有找到可直接复用的无 Slicer 尾块枚举解码器

对仓库实现入口的核查没有找到已集成的尾块枚举 CVP decoder：

- 根目录 [batchCVP.py L3](../../../batchCVP.py#L3) 是 `batchCVPP_cost` 成本模型，不解码目标。
- [hybrid_estimator.sage L120](../../../hybrid_estimator.sage#L120) 调用的是成本估算。
- 后端 [tailBDD.py L68](../../ntru_e2e/.deps/g6k_hybrid/tailBDD.py#L68)、[L106](../../ntru_e2e/.deps/g6k_hybrid/tailBDD.py#L106) 生成随机格并调用 Slicer，是批 BDD 实验脚本。
- [algebra.py L248](../../ntru_e2e/algebra.py#L248) 枚举尾部猜测列表，不是求解固定格最近向量的枚举器。

阶段 2 可先完成 Slicer/Babai 公平配对；只有确实找到或新增了枚举解码实现后，才可作为独立方法计时，不能把上述模型或候选枚举器标成枚举解码基线。

## 6. 超时和内存测量的缺口

[record_command.py L28](../../ntru_e2e/record_command.py#L28) 为 worker 新建 session；超时向整个进程组发送 TERM，等待主进程 2 秒，仅在主进程仍未退出时才发送 KILL，见[L34](../../ntru_e2e/record_command.py#L34)。

这比仅停止外层等待更强，但尚不能严格保证终止整个实验进程树：若主进程先退出，而后代忽略 TERM，`process.wait()` 会返回，当前分支不会再检查进程组或执行最终 KILL。阶段 2 应补进程组存活检查和最终回收，或使用有明确生命周期的 cgroup；应加入“主进程响应 TERM、后代忽略 TERM”的轻量真实回归。现有 runner 测试用 mock capture 模拟超时，没有验证这种进程树情形，见[test_phase2_runner.py L92](../../ntru_e2e/tests/test_phase2_runner.py#L92)。

runner 已为清理保留 allowance，见[run_phase2.py L280](../../ntru_e2e/run_phase2.py#L280)，可复用其预算扣费思路，但不代替回收保证。

[record_command.py L44](../../ntru_e2e/record_command.py#L44) 的 `peak_rss_kib` 来自 `RUSAGE_CHILDREN.ru_maxrss`，是父进程已回收子进程的历史最大值，可能把此前试验峰值带入后续试验，不能解释成每个 trial 的独立峰值。worker 的 [RUSAGE_SELF L173](../../ntru_e2e/solve_phase2.py#L173) 是自身峰值，但硬杀前可能没有最终 checkpoint，也不覆盖独立子进程。新协议应明确测量对象，并采用逐 trial 的进程或进程树资源计量。

## 7. 测试证据与当前版本的对应

最近已有全套日志为 **95 passed、57 subtests passed**，见[历史日志 L3](../../ntru_e2e/provenance/phase2_n64_first_weight/preflight_tests.stdout.txt#L3)。该次命令在 `2026-10-22T01:09:58Z` 结束，即北京时间 10 月 22 日 09:09:58，见[command L20](../../ntru_e2e/provenance/phase2_n64_first_weight/preflight_tests.command.json#L20)。**本轮未重跑测试，不能称为“本轮 95 项通过”。**

本轮静态 SHA 核对结果：

| 核对集合 | 结果 |
|---|---|
| completion manifest 中根源码和 tests 下的 `.py` | 16 项全部匹配当前文件 |
| n64 final manifest 中根源码和 tests 下的 `.py` | 6 项全部匹配当前文件 |
| phase2 summary 的 `active_source_sha256` | 7 个在线源码全部匹配当前文件 |

清单入口：[completion final_manifest L9](../../ntru_e2e/provenance/completion/final_manifest.json#L9)、[n64 final_manifest L14](../../ntru_e2e/provenance/phase2_n64_first_weight/final_manifest.json#L14)、[phase2 summary](../../ntru_e2e/runs/phase2/summary.json)。初始 `source_sha256` 中 `run_phase2.py`、`solve_phase2.py` 与当前不同，存在已记录的 instrumentation 迁移；其 active hashes 相符，不能据此认定无记录源码漂移。迁移校验规则见[记录补丁 L28](../../ntru_e2e/provenance/phase2/instrumentation_runner_migration.patch#L28)。

上述 manifest 未提供全体 12 个当前测试文件在那次执行时的完整绑定清单，因此证据应表述为“已有通过日志，已核对所列源码和测试快照”，不是今天重新执行后的完整测试认证。

已有测试的重要可复用范围：

- 相同基、数据库创建前先跑 Babai、拒绝隐藏基变化：[test_phase2_adapter.py L104](../../ntru_e2e/tests/test_phase2_adapter.py#L104)、[L182](../../ntru_e2e/tests/test_phase2_adapter.py#L182)。
- 目标重复和符号合并、目标 UID 拒绝不串号、singleton 重试、完整整数半径：[test_adapter.py L117](../../ntru_e2e/tests/test_adapter.py#L117)、[L128](../../ntru_e2e/tests/test_adapter.py#L128)、[L141](../../ntru_e2e/tests/test_adapter.py#L141)。
- Babai 流式返回及中断后候选保存：[test_phase2_adapter.py L147](../../ntru_e2e/tests/test_phase2_adapter.py#L147)、[test_phase2_worker.py L139](../../ntru_e2e/tests/test_phase2_worker.py#L139)。
- adapter/worker 多数契约测试使用 fake backend；真实已编译 UID regression 的历史记录见[command L2](../../ntru_e2e/provenance/completion/slicer_uid_regression.command.json#L2)、[输出](../../ntru_e2e/provenance/completion/slicer_uid_regression.stdout.txt)。

## 8. 阶段 2 的必要入口

以下是本轮发现所需的后续入口，尚未实施：

1. 保留旧固定基全批阶段模式，新增或明确独立 B/D/E 端到端模式；冻结首成功退出与批大小，记录候选原始数、去重数、实际处理数和停止位置。
2. D/E 共享建基和一次 BKZ，在同一基、同一目标顺序上比较；Slicer 目标 DB、格 DB 和重试各自计费，不把其他 arm 的时间混入单方法时间。另需统一 B 与 D/E 的公共约化配置，或显式分组对照 `long double/default LLL/max_loops` 与 `MPFR192/proved LLL/逐 β 严格两 tour/冻结 strategy` 的差异，防止把数值实现差异误算为 Slicer 增益。
3. 公开半径、投影维数、β、κ 分字段冻结；增加实际编译上限 64 的预检查；输出 Slicer 迭代数和相对 Babai 的候选增量。
4. 扩展候选策略与统计，增加新协议要求的完整非零 κ=4 及截断重量列表的正确性检查；不要把 tail 枚举器当枚举 decoder。
5. 修复进程组超时回收保证及逐 trial RSS 计量，补轻量真实进程树回归。
6. 为新协议保存完整源码、测试、运行环境和扩展哈希清单，并对修改后必要的正确性检查生成新日志。阶段 1 到此结束，不自动开始阶段 2 实现或后续攻击实验。
