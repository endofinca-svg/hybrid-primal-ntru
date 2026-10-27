# NTRU 小参数代数—格端到端实验第一阶段报告

2026 年 10 月 21 日（Asia/Shanghai）。本阶段已满足约定的端到端验收：固定上游的真实 Slicer 已构建并运行，数学及接口测试通过，公开输入求解实际调用 `bdgl_like_sieve`，seed 2、3 的候选经独立实现验证有效，三个预定 seed 均有正式结果。seed 1 保留原未打补丁版本的超时失败，没有重试。三例使用了不同的后端源码状态，因此本记录不能解释为统一版本的“2/3 成功率”。正式汇总见 [runs/phase1/summary.json](runs/phase1/summary.json)。

开始续作时，目录中已有代数实现、生成器、适配器、初步测试、依赖构建、整组运行入口和 seed 1 的 300 秒超时记录；seed 2、3 尚未运行，独立评估及最终报告尚未完成。检查发现，seed 1 的许多目标未能进入 C++ 目标数据库，不能把已有组件测试或 smoke 正常退出当成端到端通过。本次完成了最小 UID 精度修复、强制重编译、真实 UID 回归、剩余两例正式求解、三例独立评估和证据汇总，并核验了预算约束的整组运行入口。初始审计及续作前冻结的安排分别见 [initial_audit.json](provenance/completion/initial_audit.json)、[continuation_plan.json](provenance/completion/continuation_plan.json)。

仓库保留在用户指定的 `experiments/ntru-e2e-phase1` 分支，审计时 HEAD 为 `452be18c741a2d81c106d3bfe3847428ed57229b`。检查当前目录及其父目录，未发现适用的 `AGENTS.md`。实际 remote 是 `git@github.com:endofinca-svg/hybrid-primal-ntru.git`，与原 prompt 中的仓库 URL 名称不同；此次继续当前本地仓库，未改 remote。没有 reset、clean、stash、push 或远程 PR，也没有修改已有论文 `main.pdf`。

| 文件或目录 | 已完成的职责 |
| --- | --- |
| [algebra.py](algebra.py)、[tests/test_algebra.py](tests/test_algebra.py) | 精确负循环环运算、可逆性、乘法矩阵、分块行基、中心化目标、坐标恢复和公开验证。 |
| [generate.py](generate.py)、[solve.py](solve.py)、[attack.py](attack.py)、[evaluate.py](evaluate.py) | 分离的生成、仅公开输入求解和求解后独立评估；`solve` 是正式命令名，调用 `attack` 实现。 |
| [slicer_adapter.py](slicer_adapter.py)、[tests/test_adapter.py](tests/test_adapter.py) | 每个 pattern 一次 BKZ/筛法构库；实际目标库和 Slicer 调用；目标索引、去重、多候选、归一化和精确 lifting。 |
| [bootstrap.py](bootstrap.py)、[run_in_env.py](run_in_env.py)、[check_slicer_uid.py](check_slicer_uid.py) | 独立本地源码与 venv、固定提交、自动应用记录的补丁、强制构建及真实扩展 UID 回归。 |
| [record_command.py](record_command.py)、[run_phase1.py](run_phase1.py)、[config_phase1.json](config_phase1.json) | 保存命令/输出/退出码/资源；拒绝覆盖旧运行；管理三实例总预算，记录未运行项。 |
| [tests/](tests)、[NOTES.md](NOTES.md)、[README.md](README.md) | 数学、输入分离、来源及预算测试；数学解释边界和可复制用法。 |
| [provenance/](provenance)、[runs/phase1/](runs/phase1) | 原始构建和补丁后证据、三个公开/私有分离实例、正式结果、事件日志、独立评估和汇总。 |

上游来自 `ElenaKirshanova/g6k_hybrid`，实际 HEAD 固定为 `bd72687b2ebbaff16fb692259f83b327bbcd9586`。已检查该提交的 `README.rst`、`setup.py`、`configure.ac`，以及 `hybrid_attack.py`、`run_prog_hyb.py`、`tailBDD.py`、`g6k/slicer.pyx`、`kernel/slicer.cpp`、`kernel/hash_table.inl` 和坐标转换工具。原构建记录保留在 [bootstrap_commands.json](provenance/bootstrap_commands.json)；补丁后版本、导入位置、源码与扩展 SHA256 见 [completion/environment.json](provenance/completion/environment.json)。

实验 Python 为 3.10.19，fpylll 0.6.4，NumPy 2.2.6，Cython 3.2.4，cysignals 1.12.5，pytest 9.0.2；原生依赖 fplll 5.5.0、GMP 6.3.0、MPFR 4.2.1 的包版本和 build 见 [native_dependencies.json](provenance/completion/native_dependencies.json)。实际 C++ 编译器是 Ubuntu g++ 11.4.0。Python 发行版字符串中的 GCC 14.3.0 是 Python 自身构建信息，与本次扩展编译器分开记录。`g6k`、`g6k.siever`、`g6k.slicer` 均确认从本实验 `.deps/g6k_hybrid/` 导入；fpylll 等包由 `/home/inca/miniforge3/envs/g6k/` 的只读共享依赖提供。实验 venv 使用 `--system-site-packages`，源码和编译产物与已有 G6K 隔离，但依赖不是完全封闭快照。最大筛法维数构建为 64，全部正式求解为单线程。

UID 修复只改上游 `kernel/hash_table.inl` 的 `compute_uid_t`。原实现对归一化残差坐标使用 `int16_t(128*y[i])`；绝对值不足 `1/128` 的坐标全部截断为零。seed 1 首个 pattern 的 `gh_sub≈30555.279`，32 个目标的非零归一化投影坐标仍全部落入旧量化零区间，结果与保留的 UID 0 冲突，目标插入被拒绝。公开目标和保存的约化基足以重现这一现象，诊断不需要真实密钥。

补丁将量化改为 `int64_t(llround(1048576.0*y[i]))`，再显式转换为无符号 `UidType` 参与原来的模 `2^64` 哈希运算。量化步长由 `1/128` 降为 `2^-20≈9.54e-7`，对称取整保留反号 UID 关系。筛法的整数 UID、投影归一化、距离、公开半径和 Slicer 解码逻辑均保持原约定。完整差异见 [completion/upstream_source.patch](provenance/completion/upstream_source.patch)，SHA256 为 `e17fe06b6fb3d8695fcac6cfdb280a354080dae3cff4e5532cde81ec2599d34d`。修改处含本地修改说明；上游版权头保留，GPL v2 许可证副本见 [upstream_LICENSE](provenance/completion/upstream_LICENSE)，源码头允许 v2 或以后版本。

修改 `.inl` 后使用 `setup.py build_ext --inplace --force -j2` 强制重新编译，避免仅按 Python 扩展文件时间戳跳过 C++ 头文件变化。强制构建退出码为 0，wall time 122.667 秒。真实扩展回归使用 28 维单位基、真实 `Siever.grow_db(8)` 数据库和 `n_per_target=1`，验证 `[1e-4,0,…]` 插入后目标库增长 1、其反号重复增长 0、恰零目标增长 0，全部通过。这个回归只验证 UID 行为，不充当 BDD 验收；命令和结果见 [slicer_uid_regression.command.json](provenance/completion/slicer_uid_regression.command.json)、[stdout](provenance/completion/slicer_uid_regression.stdout.txt)。

此修复仍有明确边界：恰零投影及小于新量化尺度的投影仍可能被保留 UID 0 拒绝；哈希碰撞并未从数学上消除。更细量化也可能把数值微小差异分成不同 UID，从而减少去重，泛化到极大坐标时还需要有限性及整数范围防护。当前坐标规模、真实扩展回归及最终精确整数距离/公开候选校验覆盖本阶段所用范围，不能据此宣称所有尺度的 UID 问题均已解决。

本次首次重编译命令曾因工作目录在仓库根目录、未找到上游 `setup.py` 而退出 2，记录保留在 [rebuild.command.json](provenance/completion/rebuild.command.json)；随后指定上游源码工作目录完成上述强制构建。未删除这一失败启动记录。

上游 README 的参考 smoke 为 `n=60, betamax=55, nexp=3, approx_factor=0.99`。本机采用更小且受支持的 `n=40, betamax=20, nexp=3, approx_factor=0.99, nthreads=1`，限制 smoke 的构库成本，同时仍直接运行上游 `test_slicer.py`。补丁后 smoke 退出码 0，wall time 9.390 秒，三次样例恢复结果为 Babai 0/3、Slicer 0/3；这证明扩展、数据库和 Slicer 程序路径能够运行，不表示这些随机近似 CVP 样例解码成功。旧未打补丁 smoke 也保留，其 Slicer 结果为 1/3。两轮样例及后端状态不同，不能从这个差异得出质量优劣结论。完整命令、stdout/stderr 分别见 [旧 smoke](provenance/upstream_smoke.command.json) 和 [补丁后 smoke](provenance/completion/upstream_smoke.command.json)。

数学层统一使用 `F_257[x]/(x^32+1)`、`h=g/f`、`xi=1`。公开乘法矩阵满足 `Af-g=0 mod q`；坐标保存有序的 `P,C,T` 映射。实际传给 fpylll 的行基为 `[[qI_n,0],[A_C^T,I_m]]`，维数 `d=n+m=2n-k-kappa`，行列式绝对值 `q^n`。实际中心化目标及对应格向量保留到重构阶段，使用 `v-t=(g,f_C)`，填回 `f_P=0` 和枚举的 `f_T=u`。带符号旋转使用 `(-a[n-1],a[0],…,a[n-2])`。当前本地论文附录 Algorithm 4 已采用 `v-t`；说明文件核对了这一事实，论文保持原样。

在线进程只接收公开文件、公开求解配置及独立求解 seed。固定重量三元密钥的支持均匀选取，非零符号独立均匀选取，生成器实际检查 `f` 可逆，仅因此类生成条件重采样，每个 keygen seed 最多 128 次。公开文件没有秘密、生成 seed、正确旋转、参考 tail 或实际投影误差；参考结果位于独立的 `private/`。在线没有导入生成器和私有评估器。上游 `alg_3_debug_v2` 注入 `-s[-kappa:]`，`run_prog_hyb.py` 还由真实误差配置投影阈值，因此在线仅复用真实 Slicer 核心及参考坐标转换，不调用这些调试驱动。三个独立评估命令都发生在本次两个在线求解命令结束以后，没有向搜索反馈真值。

公开平方半径采用 `R_u^2=w_g+w_f-wt(u)`，归一化为 `R_u^2/gh_sub`。解码器投影转换与上游平方 GH 尺度一致；返回浮点残差仅供 lifting，最终格向量由整数系数精确乘回约化行基，再做完整环境空间的整数距离检查和公开验证。零 tail 全部排除，未使用 `kappa=0` 验收。初始 `kappa=4` 有 80 个非零三元 tail，按公开 `w_f=2` 过滤为 32 个；`kappa=6` 对应 728 个过滤为 72 个。

同一 pattern 下，适配器只构造一次 BKZ 结果和筛法数据库，所有 tail 共享它。投影按实际 float32 和反号约定去重，保存每组全部外部 `target_id`；内部索引通过真实目标库增长核定，拒绝插入不会使后续索引错位。一个目标的多个残差都会检查，最终按 `(target_id,v)` 去重。碰撞重试只创建新目标数据库，不重新构造格、BKZ 或筛法数据库。小批次饱和索引显式保证位于目标库内；投影维数强制至少 28，使 `blocks` 至少为 1。每个实际调用及返回候选都记录 `call_id`、`target_id` 和事件。

| 项目 | seed 1 / solve seed 10001 | seed 2 / solve seed 10002 | seed 3 / solve seed 10003 |
| --- | --- | --- | --- |
| 后端 | 固定提交、未打 UID 补丁 | 固定提交加 UID 补丁 | 固定提交加 UID 补丁 |
| 正式结果 | `timeout`，无候选 | `success` | `success` |
| pattern 数 | 17，最后一个超时 | 1 | 1 |
| 使用配置 | 初始 8 次、备用二 8 次、备用三第 1 次 | 初始配置第 1 次 | 初始配置第 1 次 |
| 实际 Slicer 完成调用 | 8，每次 400 轮 | 1，1 轮 | 1，1 轮 |
| 有效 Slicer 候选 | 0 | 8 | 8 |
| 独立分类 | `no_recovery` | `equivalent_secret` | `equivalent_secret` |
| 等价代表 | 无 | 原参考的 `-x^24` | 原参考的 `-x^14` |
| `end_to_end_passed` | false | true | true |
| 正式命令退出码 / worker 退出码 | 0 / -15 | 0 / 0 | 0 / 0 |

seed 1 的命令退出码 0 表示监督程序正常保存了超时结果；worker 因时限被终止，候选重构验收未通过。离线覆盖诊断显示存在 pattern 兼容且 tail 非零的参考旋转，但它们的正确猜测没有被实际送入 Slicer；失败的重要原因是目标插入阶段的 UID 量化冲突，不能解释为参考旋转不存在。初始和备用二各 8 个 pattern 记录为 `no_recovery`；备用三第一个 pattern 超时，剩余 7 个因该实例预算用尽未运行。seed 2、3 在初始配置成功后停止，初始配置其余 7 个 pattern 及备用配置均按成功停止规则未运行。没有跳过预定 seed，也没有更换密钥重试。

seed 2、3 均使用 `n=32,q=257,w_f=2,w_g=4,k=2,kappa=4,BKZ beta=20,Slicer dim=28,d=58`。每例只进行一次 BKZ 和筛法构库，筛法数据库为 180 条；32 个外部目标按反号投影去重后为 16 个实际目标，各生成 16 条记录，目标数据库共 256 条。两次 `bdgl_like_sieve` 的 `itercount_slicer=1`，分别产生 178、186 次有效替换，因此实际进行了筛法迭代。每例均有 8 个经公开验证的候选。

两例保存的首个成功候选来源均为 `trial_id=0, call_id=1, target_id=9`，猜测 `u=[0,-1,0,0]`；有效误差平方范数为 5，符合公开半径，`f_C` 各有一个非零系数。seed 2 的 core 非零位置为 27，seed 3 为 22，均没有退化为完全由 tail 猜中整个 `f` 的情形。完整的整数 `v`、约化基系数、实际 target、tail、`f/g` 和真实调用事件保存于各 [result](runs/phase1/instance_02.result.json) 及 [result](runs/phase1/instance_03.result.json)。独立评估以另一套负循环乘法和多项式 gcd 检查三元系数、非零、精确重量 2/4、`f` 可逆及公钥关系；同时复核 `v-t=(g,f_C)`、格成员关系和完成调用的来源。两例的 `trace_verified`、`completed_call_verified`、`reconstruction_verified` 及 `end_to_end_passed` 均为 true，证据见 [seed 2 evaluation](runs/phase1/instance_02.evaluation.json)、[seed 3 evaluation](runs/phase1/instance_03.evaluation.json)。

最终 [artifact_audit.json](provenance/completion/artifact_audit.json) 又重新计算了三份评估，均与已存文件一致；两例共 16 个候选全部通过整数 `coefficients×reduced_rows=v`、`v-target=(g,f_C)`、公开半径及独立验证检查。seed 1 原始 JSONL 有 2343 条事件，result 检查点保存 2342 条；末尾额外一条仅为 `target_retry`，说明时限在追加日志后、原子保存结果前打断进程。该差异不影响已完成的 8 次 Slicer 调用和 0 个候选，未改写旧证据以消除此差异。

每例仅首个 pattern 运行了一次直接 Babai 诊断，三例各发现 8 个合法候选；LLL 和 BKZ 的行向量检查也发现可直接公开验证的短秘密。这些结果仅保存在 `diagnostics`，未用于替代 Slicer 候选或提前停止正式搜索。结论是“此参数下尚未证明 Slicer 的必要性”。真实 Slicer 返回及整数 lifting 可以验证端到端接口，但无法证明它优于直接 Babai、LLL/BKZ，也无法把一轮完成解释为复杂度优势。

| 时间/资源项目 | seed 1（旧版，17 patterns） | seed 2（补丁版，1 pattern） | seed 3（补丁版，1 pattern） |
| --- | ---: | ---: | ---: |
| 格及目标构造 / 秒 | 0.163471 | 0.015545 | 0.012552 |
| LLL / 秒 | 3.107243 | 0.025614 | 0.079160 |
| BKZ / 秒 | 0.482108 | 0.021087 | 0.027323 |
| 筛法数据库构造 / 秒 | 16.375236 | 0.726525 | 1.798962 |
| 完成的目标库构造区间，含事件保存 / 秒 | 166.730808 | 0.202735 | 0.304288 |
| Slicer 内核调用 / 秒 | 1.091051 | 0.002499 | 0.005365 |
| lifting 和整数距离校验 / 秒 | 0.001623 | 0.226673 | 0.218983 |
| 候选重构和公开验证 / 秒 | 0.000071 | 0.019286 | 0.008731 |
| 首 pattern Babai 诊断 / 秒 | 0.084637 | 0.045413 | 0.079421 |
| worker wall / 秒 | 299.564191 | 2.144088 | 3.533815 |
| 含监督进程的在线命令 wall / 秒 | 300.814391 | 2.734349 | 4.181107 |
| 在线命令 CPU / 秒 | 301.508998 | 2.694594 | 4.126695 |
| 峰值内存 / KiB | 852996 | 80084 | 80108 |

阶段时间来自已完成事件的累计区间。它们不构成完整可相加的剖析：事件 JSON 保存、解释器启动、诊断、失败/未完成插入及监督收尾另有成本；超时记录还可能缺少最后未完成阶段。特别是 seed 1 的大量目标重试和原子保存开销不能归入表中的 Slicer 内核用时。原始 wall/CPU/RSS 及命令限时以各 `*.command.json` 和 result 的 `supervisor` 为准。

全部正式在线命令的累计 wall 为 **307.729848 秒**，CPU 为 308.330287 秒，低于 1800 秒累计上限；这一数字包含原 seed 1 的 300.814391 秒。旧命令给 worker 300 秒，外部测量另含约 0.814 秒启动/清理开销，此事实完整保留。续作没有再次消耗 seed 1 的预算；其余两例给 worker 295 秒并预留 5 秒监督开销，外层各限 300 秒。冻结的公开配置 SHA256 保持为 `d02b2b8cc04ec23dd8100c1bb88f3835ff8bd3cbdef643d16496b568960461d8`，没有增大求解预算或改动备用配置顺序。构建、smoke 和组件测试与在线预算分开计量。

当前最终测试记录是 **43 passed，22 subtests passed**，pytest 内部时间 1.31 秒、完整命令 wall 2.255 秒、退出码 0，见 [final_tests.stdout.txt](provenance/completion/final_tests.stdout.txt) 和 [final_tests.command.json](provenance/completion/final_tests.command.json)。覆盖乘法矩阵/负循环乘法一致、带符号旋转、行基方向与维数、格成员关系、正确重构、pattern/tail 坐标映射、去重和拒绝插入后的索引、完整整数半径、独立验证，以及预算监督/公开私有入口分离。确定性接口测试和真实 UID 回归分别保留，实际端到端通过依据上述正式求解与离线评估。

以下是已经实际执行的关键命令，均从仓库根目录执行；完整参数、cwd、stdout/stderr、退出码以相应命令元数据为准。原构建由 `bootstrap.py` 执行，修复后的强制编译记录另存于 `completion/rebuild_force.*`。

```bash
python3 experiments/ntru_e2e/bootstrap.py \
  --base-python /home/inca/miniforge3/envs/g6k/bin/python \
  --source /home/inca/g6k_hybrid --jobs 2

python3 experiments/ntru_e2e/run_in_env.py \
  experiments/ntru_e2e/check_slicer_uid.py

python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.generate \
  --output-dir experiments/ntru_e2e/runs/phase1

python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.solve \
  --public experiments/ntru_e2e/runs/phase1/public/instance_02.json \
  --config experiments/ntru_e2e/runs/phase1/config_phase1.json \
  --seed 10002 --time-limit 295 \
  --output experiments/ntru_e2e/runs/phase1/instance_02.result.json

python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.solve \
  --public experiments/ntru_e2e/runs/phase1/public/instance_03.json \
  --config experiments/ntru_e2e/runs/phase1/config_phase1.json \
  --seed 10003 --time-limit 295 \
  --output experiments/ntru_e2e/runs/phase1/instance_03.result.json

python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.evaluate \
  --public experiments/ntru_e2e/runs/phase1/public/instance_02.json \
  --truth experiments/ntru_e2e/runs/phase1/private/instance_02.truth.json \
  --result experiments/ntru_e2e/runs/phase1/instance_02.result.json \
  --output experiments/ntru_e2e/runs/phase1/instance_02.evaluation.json

python3 experiments/ntru_e2e/run_in_env.py \
  -m pytest -q experiments/ntru_e2e/tests
```

实际独立评估还对 `instance_01`、`instance_03` 分别执行同一评估入口，完整命令见 [seed 1](runs/phase1/instance_01_evaluate.command.json)、[seed 2](runs/phase1/instance_02_evaluate.command.json)、[seed 3](runs/phase1/instance_03_evaluate.command.json)。上述生成命令已在原阶段执行，本次保留既有三个密钥文件，没有重新生成它们；旧 seed 1 求解命令仍见 [instance_01_online.command.json](runs/phase1/instance_01_online.command.json)。

补丁后上游 smoke 的实际路径包含本地仓库绝对路径，以下等价写法便于从仓库根目录复制。其参数、工作目录及完整输出见 [completion/upstream_smoke.command.json](provenance/completion/upstream_smoke.command.json)：

```bash
python3 experiments/ntru_e2e/record_command.py \
  --prefix /tmp/ntru-e2e-smoke-reproduction \
  --cwd experiments/ntru_e2e/.deps --timeout 180 -- \
  python3 '/home/inca/hybrid primal multi decoding for NTRU/experiments/ntru_e2e/run_in_env.py' \
  '/home/inca/hybrid primal multi decoding for NTRU/experiments/ntru_e2e/.deps/g6k_hybrid/test_slicer.py' \
  --n 40 --betamax 20 --nexp 3 --approx_factor 0.99 --nthreads 1 --verbose
```

要在当前补丁版本上重新执行完整三 seed，可以使用新的整组入口和一个尚不存在的目录。入口负责冻结配置、生成、按预算逐例公开输入求解、最后独立评估，并记录失败、超时和预算不足的占位结果：

```bash
python3 -m experiments.ntru_e2e.run_phase1 \
  --output-dir experiments/ntru_e2e/runs/reproduction_uid_patch
```

这条整组命令是交付的后续复现方式，**本次没有另外执行统一补丁版本的全三 seed 重跑**；本次已执行的是保留 seed 1 旧证据、补完 seed 2/3 的续作。入口会拒绝已存在的运行目录，公开数据与真值分别放在 `public/`、`private/`。跨机器复现应先按 README 准备相应基础 Python 和编译依赖，再执行 bootstrap；不能直接复用本机 `.deps/runtime.json` 中的绝对路径。

当前 `git status --short` 为 `?? experiments/`、`?? main.pdf`。整个实验尚未纳入版本控制，因此普通 `git diff --stat` 没有已跟踪文件差异，不能据此认为没有新增内容；新增实现、配置、测试、原始证据和本报告均位于 `experiments/ntru_e2e/`。`.deps/` 中的大型环境、源码编译目录和产物由实验忽略规则排除；没有把筛法数据库持久化纳入版本控制。原本未跟踪的 `main.pdf` 保留。

最终文件的校验清单见 [final_manifest.json](provenance/completion/final_manifest.json)，Git 状态快照见 [git_summary.json](provenance/completion/git_summary.json)。清单覆盖实验源文件、配置、报告、测试及文本证据，排除被忽略的大型依赖和清单自身。

本阶段到 `n=32` 停止，没有扩展到 64、128 或更大 NTRU 参数。完成情况支持公开公钥、pattern 降维、非零 tail 枚举、共享预处理的真实 Slicer、候选重构和独立验证这一流程已接通。尚需研究的问题包括：在统一后端版本上预先安排整组三 seed 复验；覆盖 UID 量化边界、恰零投影和跨尺度行为；在预先冻结的后续预算内选择更能区分直接 Babai/LLL/BKZ 与 Slicer 的实例，并保留失败与消融证据。当前 toy 参数不支持大参数安全性、一般成功概率、论文熵采样模型或性能优势的结论。
