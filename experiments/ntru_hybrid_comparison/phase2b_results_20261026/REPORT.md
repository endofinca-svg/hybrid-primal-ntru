# 阶段 2B：共同约化协议与配对公共输入

阶段 2B 验收通过。已新增实现、运行 91 项轻量正确性测试（全部通过，无失败、错误或跳过），完成唯一一次受限真实后端检查，并构建已有 n=128 公钥的 212 维原始输入包。**未执行 n=128 的 LLL/BKZ、GSO 或解码，未调用 Slicer，未启动参数扫描、阶段 2C 或阶段 3。**

本轮授权来自 Prompt 3 附件。阶段 1、2A 的限制字段和报告保持为历史记录；没有删除或覆盖旧数据。分支 `experiments/ntru-e2e-phase1`，HEAD `452be18c741a2d81c106d3bfe3847428ed57229b`；开始时已有 `?? experiments/`、`?? main.pdf`，没有 reset、clean、stash、提交或 push。未找到适用的 AGENTS.md。

## 实现文件

所有新模块位于 `experiments/ntru_hybrid_comparison/`，没有修改阶段 2A 接口或封存实现。

| 新文件 | 用途 |
| --- | --- |
| `reduction.py` | ReductionProtocol/Stage、规范清单、真实 MPFR 执行入口、严格执行记录校验及续接 |
| `exact_lattice.py` | 任意精度 Bareiss 行列式和行基幺模证书验证 |
| `id_mapping.py` | SHA 候选 ID 与整数 ID 一一映射，乱序、未知、重复、缺失返回诊断 |
| `inputs.py` | 公开建基、坐标及目标、原始／约化状态、原子保存、严格加载、独立工作副本 |
| `build_public_inputs.py` | 可重用的“仅公共输入构建”命令入口，不执行约化 |
| `tests/test_reduction.py` | 协议、实际记录和历史续接的 13 项纯测试 |
| `tests/test_exact_lattice.py` | 幺模、真子格、错误证书及大整数的 7 项测试 |
| `tests/test_id_mapping.py` | 身份恢复与计数隔离的 7 项测试 |
| `tests/test_inputs.py` | 保存加载、目标代表、篡改、原子写入、隔离及大整数的 10 项测试 |
| `tests/test_input_boundaries.py` | 生产／夹具边界、参考字段拒绝、精度守卫和记录绑定的 4 项测试 |

完整路径与 SHA-256 见 [modified_files.json](modified_files.json)。结果目录另有一次性 `backend_check.py`，带已有执行标记拒绝重跑，未接入测试发现或正式 worker。

## 历史约化口径与新协议

本轮只提取相关既有配置和约化执行元数据，未重做全面历史审计。证据见 [historical_reduction_protocol.json](historical_reduction_protocol.json)。

历史 A/B 请求 MPFR 192 位、proved LLL（δ=.99、η=.51）、单线程、逐 β 增加 1、每 β 严格两次显式 tour、clean 不跳过第二次；公开成功及硬超时允许提前终止。已记录结果中 A 实际完成 11 个 BKZ tour，在 β35/tour1 首次成功；B k40 实际完成 6 个，在 β32/tour2 首次成功。B 是 216 维渐进约化后的成功位置，不是冷启动最低 β，也不是本轮 212 维 hybrid 基。未复制或截取其基。

旧混合入口使用 long double、默认 LLL 和 MAX_LOOPS 上限；实际 tours 不能由 max_loops 推定。上述差异保留在历史证据中，未修改旧实现后继续沿用旧时间。

新可执行协议明确支持：

- MPFR，整数精度位数≥64；默认并实际检查 192 位。初始 LLL 通过 `LLL.reduction(...,method='proved',float_type='mpfr',precision=...)`，BKZ 使用同一 MPFR GSO。
- LLL δ/η 明确配置，LLL/BKZ/GSO flags 显式为 0；可配置 seed、线程数及严格递增 β schedule，每段请求 tours。
- 使用 `fpylll.algorithms.bkz2.BKZReduction.tour` 逐次调用；一个完整返回的调用计作一轮，clean 不省略下一轮。不把 requested 自动填入 actual。
- 默认 strategy 文件字节及 SHA-256、后端源码／扩展摘要和版本均记录；超过已加载策略最大 β 时显式扩展为无预处理块的 unpruned 策略。
- 初始 LLL、每个完整 tour、失败阶段、开始／结束、停止原因及实际 wall 分开记录。内部迭代数和 RNG 内部状态不可观测，明确写 not_observable。
- 只从完整协议边界、同原始基且精确验证的检查点续接，保留历史阶段与累计成本。续接会重新设置 seed；不声称恢复内核 RNG 状态或逐位复现无中断运行。

long double、MAX_LOOPS 语义、非默认 flags、auto_abort、其他 LLL method/strategy 的约化请求均明确拒绝，不静默退回另一精度。本轮只实测 MPFR192、单线程、β4；其他合法数值配置尚未做性能或大参数验证。空 schedule 仅表示没有冻结主实验 β，显式执行该协议仍会执行初始 LLL；本轮主参数输入未调用执行入口。

当前 fpylll=0.6.4，fplll=5.5.0；strategy 最大 β=100，文件 SHA-256 为 `f516b0a6f0c580cff72e1e2c3562c44dc6f17e8f99613e9e4020e35481b27a18`。历史结果没有记录的 fplll 版本／内部迭代保留 unknown，不用本轮版本倒填历史。

## 公共包、目标与精确证书

`build_input_bundle` 只接收 public-v1、P/C/T、有序剩余坐标、2A 候选策略及公开元数据。检查完整互斥分割，保存原坐标→f_core/f_tail、格前块→g 的映射。行基为 `[[qI,0],[A_C^T,I]]`，A*f=h*f，维数 d=2n−k−κ。κ=0 留给独立 primal 路径。

每个候选保留完整 u、SHA ID、有序候选摘要，以及 `raw`、`used`、`used_minus_raw` 和相对于原始行基的平移系数。实际目标明确选择 raw 或 centered_mod_q。检查居中平移属于同一格；重构必须使用 v_used−t_used。后端进一步平移的转换记录预留为 not_applied/null，尚未实现未接入的后端逻辑。

整数 ID 和 target_index 均由冻结候选序从 0 开始，候选与 ID 一一对应；相反尾部、相同完整目标或未来投影碰撞不合并外部身份。返回通过显式整数 ID 恢复，未知、重复、缺失分别报告，模拟返回不更新真实提交／完成数。

保存使用同目录临时文件、flush/fsync、原子 replace 和目录 fsync。带 complete 标志和 payload 摘要；加载拒绝不完整文件、重复 JSON key、摘要错误、坐标／候选顺序／目标关系／映射错误。整数直接序列化，不经浮点转换；测试中 2^80+19 的 U 元素与相应大基元素精确保留。

original_only 必须没有约化矩阵、证书和执行耗时；原始基重新按公开构造逐项核对。reduced_verified 重新检查整数 U、det(U)=±1、原基满秩及 B_red=U*B_original；内容摘要不能代替格验证。行乘 2 的真子格即使满足整数变换关系也被拒绝。缺证书的历史矩阵仅在显式 allow_unverified 下隔离为 reduced_unverified，不能通过正式约化基加载门槛。

执行记录还须与包协议、实际完成阶段／计数、精度、策略、三矩阵摘要、累计成本一致，未知或参考字段拒绝进入公开包。纯代数变换夹具用专门 timing_source 和 test_fixture 来源标记，不能改标签冒充生产约化产物。

每次 load 和 create_working_copy 均创建独立可变数据；GSO 仅在显式要求时新建，不执行约化或解码。解码 GSO 精度与生成整数约化基的协议分开记录。MPFR 精度是进程级状态，本接口限制同进程的解码副本使用同一精度；不同精度须在不同进程中使用，不能在已有 GSO 存活时改变全局精度。

## 实际检查结果

统一纯测试命令：

```bash
timeout --signal=TERM --kill-after=2s 60s /usr/bin/python3 -B \
  -m unittest discover -s experiments/ntru_hybrid_comparison/tests -t . -v
```

设置 PYTHONDONTWRITEBYTECODE=1。**91 通过，0 失败、0 错误、0 跳过**；unittest 1.816 秒，完整命令 wall 2.22025305 秒，退出 0，无超时。包括复跑阶段 2A 的 50 项和本轮新增 41 项，未运行旧完整格求解套件。见 [tests.command.json](tests.command.json)、[tests_summary.json](tests_summary.json)、[原始日志](tests.stderr.txt)。

唯一真实后端检查为手工 n=4、q=17、d=6，1 次 proved LLL 和 1 次 β4 tour；请求与实际均为 MPFR192、单线程。外部 timeout 30 秒、TERM 后 2 秒 KILL 宽限，完整命令 wall 1.476370838 秒，退出 0。det(U)=1，B_red=U*B_original，det(B_original)=det(B_red)=83521，精确证明通过。两份真实 GSO／矩阵／目标／ID 及磁盘隔离检查通过。它是后端接口检查，不是求解性能结果或阶段 3 验收。

见 [真实检查结果](backend_check.result.json)、[执行记录与证书](backend_check.execution.json)、[实际命令](backend_check.command.json)、[已验证小型输入包](backend_check.reduced_input.json)。检查后只收紧记录验证器，并以最终代码重新校验既有记录和输入包，没有增加真实约化或 GSO 次数，见 [最终重校验](backend_check.revalidation.json)。

来源限制：实际检查时精确源码快照记录了 reduction.py 与检查脚本；并行 inputs.py/exact_lattice.py 没有独立的执行时快照。最终模块摘要、已有整数证书和最终代码重校验均已保存，不把最终摘要冒充全部执行时摘要。这个限制不影响本次已验证的整数关系，但后续正式运行应在启动前完整冻结所有相关模块。

## 主参数“仅公共输入构建”样例

复用 `experiments/ntru_compare_n128_w12_w24_q97/public/instance_01.json`。按已有公开采样规则，random.Random(10001) 的第 0 个 sample 选 40 个位置；取剩余坐标最大的 4 个作为有序 T。backend seed=254991857。无参考输入，无兼容性筛选，无离线覆盖诊断；此次选择不作为正式选参冻结。

| 项目 | 实际结果 |
| --- | --- |
| n、q、k、κ | 128、97、40、4 |
| d_pattern / d | 216 / 212 |
| 候选规则／数量 | full_nonzero / 80 |
| 来源／基状态 | public_input_build_only / original_only |
| 约化／GSO／解码 | 均未执行 |
| 提交／完成／投影分组 | 0 / 0 / null |
| 非零居中平移目标数 | 80 |
| 保存加载／独立副本 | 一致／通过 |

原始基 SHA-256：`e423d69e11ed9e618505fb44c93f0ed423d76813588657c621501c61f127d00f`

有序候选 SHA-256：`5d635f91f7426a02975fb5597bfba8955b87f01750cb7473807e139769e0e8c5`

有序目标 SHA-256：`328273a7f25e4bf4c7e7df319d8a28cfeafd45631964e287243d21593f38cd39`

产物：[公共输入包](public_inputs_n128_k40_kappa4.json)、[样例清单及摘要](public_inputs_n128_k40_kappa4.json.manifest.json)、[实际命令](public_build.command.json)。命令 wall 13.075300207 秒，包括元数据读取、公开构造、验证、原子保存、两次加载及副本检查，不是约化或求解耗时；其中首次构造约 1.8447 秒。该包尚不能作为已约化 D/E 输入，默认约化加载门槛会拒绝。

## 封存与阶段 2C 入口

[封存检查](sealed_integrity.json)：1705 个保护文件 SHA-256 全部一致，变化 0；覆盖阶段 1/2A、历史 A/B、封存实现和已枚举后端。1 个私有参考文件仅比较 size/mtime_ns/inode，未变化，未读取其内容。未安装依赖、重建后端或修改编译扩展。

本轮没有精度静默回退或真实后端测试阻断。尚未实测 n=128 的约化质量、最优 β、不同精度／线程／策略的性能；尚无真实 Babai/Slicer worker、完整预算 runner 或通用 supervisor。续接仅支持完整协议边界，不恢复内部 RNG 状态。这些限制均未被本轮验收覆盖。

后续接口顺序：`build_input_bundle → execute_reduction → attach_reduction → save_input_bundle`；D/E 各自 `load_input_bundle(require_reduced_verified=True) → create_working_copy`，在同一已验证整数基和 used 目标列表上接入解码器。输出用 `reconcile_results` 恢复身份，再通过现有 `reconstruct` 和阶段 2A 的公开验证；参考分类仅离线执行。原始样例不能绕过约化门槛，test_fixture 也不能当作主实验结果。

具体入口、正式运行前的参数／预算冻结和成本记录待办见 [next_stage_entry.json](next_stage_entry.json)。本轮在此结束，不自动进入阶段 2C。
