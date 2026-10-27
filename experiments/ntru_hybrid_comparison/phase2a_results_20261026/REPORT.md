# 阶段 2A：候选、公开验证与离线分类

本轮阶段 2A 验收通过。已实际新增实现并运行轻量正确性测试：**50 项通过，0 失败、0 错误、0 跳过**。没有新的在线求解实验，没有调用 CVP、LLL、BKZ 或 Slicer，没有生成新的主实验公钥。阶段 2B、阶段 3 均未启动。

授权来自本轮附件；阶段 1 的 `authorized_current_phase=1`、`proposal_only_not_started` 等历史字段原样保留。本报告是实现与代数正确性检查，不能作为主实验求解验收、性能或加速证据。

## 工作区与既有证据

- 分支：`experiments/ntru-e2e-phase1`；提交：`452be18c741a2d81c106d3bfe3847428ed57229b`。
- 开始时已有未提交内容：`?? experiments/`、`?? main.pdf`。未覆盖或恢复这些内容，未提交或推送远端。
- 已读取阶段 1 的 REPORT、CODE_REVIEW、stage2_entry、audit_manifest、source_snapshot、model 和 candidate_counts，以及本轮相关旧实现；没有重新执行完整阶段 1 审计。
- 没有找到适用的 AGENTS.md。已有纯代数函数从 `experiments/ntru_e2e/algebra.py` 只读导入；旧求解器和后端保持原样。
- 主模型继续为 n=128、q=97、商环 Z_q[x]/(x^128+1)、总非零重量 (12,24)、非零系数 ±1、平方范数 36。公开关系为 h*f=g；pattern 作用于 f，tail 使用删除 pattern 后剩余的原始 f 坐标。本轮未改变主模型。

## 新增文件与接口

新增 10 个实现／测试文件；没有修改原有实现：

| 文件（相对比较实验目录） | 作用 |
| --- | --- |
| `__init__.py`、`tests/__init__.py` | 无启动副作用的包标记 |
| `candidates.py` | CandidateContext、CandidatePolicy、CandidateBatch、context_from_public、build_candidates |
| `validation.py` | PublicConstraints、validate_public_candidate |
| `offline.py` | reference_orbit、classify_against_reference、diagnose_coverage |
| `tests/test_candidates.py` | 候选独立对照、计数、顺序、ID、摘要和非法配置 |
| `tests/test_validation.py` | 公开约束、环关系和可逆性 |
| `tests/test_offline.py` | 负循环分类和三事件覆盖诊断 |
| `tests/test_reconstruction.py` | 显式格向量的小维数代数重构 |
| `tests/test_separation.py` | 无参考与无格后端依赖的实际运行检查 |

完整文件路径与 SHA-256 见 [modified_files.json](modified_files.json)。

### 候选规则、顺序和标识

支持 `full_nonzero` 以及 `weight_range(max_weight=h)`；κ=0 明确拒绝，留给 primal 流程。重量截断直接生成支撑集和符号，不枚举整个 3^κ 空间再过滤。

生成顺序固定为：重量升序、支撑组合字典序、符号字典序（-1 在 +1 前）。输入候选列表保留原顺序，规则过滤后按完整整数尾部向量精确去重，保留第一次出现；u 与 -u 分别保留。不进行浮点近似或目标投影去重。

`CandidateContext` 包含 instance_id、公开内容 SHA-256、n 和**有序原坐标 T**；u[i] 对应 f[T[i]]。候选 ID 绑定该上下文和完整 u，不含解码器、候选规则、pattern 或运行进度。同一上下文中的同一 u 跨规则保持同 ID。批次有序摘要绑定上下文和有序 (u,ID)，不含规则或进度。`context_from_public` 只接受 public-v1 白名单字段，摘要是规范 JSON **内容**摘要，不是原文件字节摘要。

| κ | 1≤HW(u)≤2 实际生成 | 1≤HW(u)≤3 实际生成 | 理论 3^κ−1 |
| --- | ---: | ---: | ---: |
| 4 | 32 | 64 | 80 |
| 6 | 72 | 232 | 728 |
| 8 | 128 | 576 | 6560 |
| 10 | 200 | 1160 | 59048 |
| 12 | 288 | 2048 | 531440 |

κ=4 的 full_nonzero 实际生成 80 个，和独立笛卡尔积 oracle 完全一致。上述直接生成配置的 raw、kept、unique 分别等于实际生成数。接收外部列表时，raw 是接收条目数，kept 是非零且满足重量规则的条目数，unique 是完整整数向量精确去重数；测试包含 raw=6、kept=4、unique=2 的例子。

`projection_group_count=null`（未计算），`submitted_count=0`、`completed_count=0`（未调用解码器）。理论空间大小没有冒充实际生成或处理数量。

[candidate_checks.json](candidate_checks.json) 保存每组规则、数量、稳定顺序摘要与首尾 ID；[full80_manifest.example.json](full80_manifest.example.json) 保存完整 80 项示例。它们使用明确标注的静态上下文夹具，未生成攻击实例、目标或解码输出。

### 公开验证与离线参考的边界

`validate_public_candidate(public,f,g,*,constraints=None)` 不接收参考短向量；重新检查维数、整数系数（拒绝 bool/浮点数）、非零性、三元系数、总重量、范数、负循环模 q 关系及 f 可逆性，返回 `public_valid`、逐项检查和拒绝原因。默认要求可逆，范数默认取 w_f+w_g。额外参考 metadata 与求解器成功标志均不参与判定。

离线 `classify_against_reference` 先执行相同公开验证，然后按 original → signed_negacyclic_equivalent → other_public_valid 分类；不合法候选保留 invalid 和公开拒绝原因。f/g 同时应用同一 x 幂，覆盖 0..2n−1 并精确去重；x^n 对应整体取负。有效候选遇到非法参考会明确报错，不把参考问题写成候选公开无效。

`diagnose_coverage` 分别返回 pattern_compatible、nonzero_tail_covered、public_valid_found。最后一项只来自实际输出的重新验证：没有提供输出为 null，提供空输出为 false；覆盖不能推导重构成功。另有 pattern_incompatible、all_compatible_tails_zero、nonzero_tail_not_covered、covered 四种状态。计数按去重后的联合 f/g 轨道及明确标注的不同尾部统计，不采用独立旋转近似。四种手工例子见 [coverage_examples.json](coverage_examples.json)。在线候选与公开验证不导入 offline 模块。

## 实际运行与证据

正式命令（工作目录为项目根目录，设置 PYTHONDONTWRITEBYTECODE=1）：

```bash
timeout --signal=TERM --kill-after=2s 60s /usr/bin/python3 -B \
  -m unittest discover -s experiments/ntru_hybrid_comparison/tests -t . -v
```

Python 3.10.12；退出码 0，未触发超时。unittest 耗时 1.149 秒，完整命令墙钟 1.447140255 秒。实际命令、时间与原始输出分别见 [tests.command.json](tests.command.json)、[tests.stderr.txt](tests.stderr.txt)、[tests.stdout.txt](tests.stdout.txt)，汇总见 [tests_summary.json](tests_summary.json)。

| 测试模块 | 通过 | 失败／错误／跳过 |
| --- | ---: | --- |
| candidates | 13 | 0 / 0 / 0 |
| validation | 11 | 0 / 0 / 0 |
| offline | 19 | 0 / 0 / 0 |
| reconstruction | 4 | 0 / 0 / 0 |
| separation | 3 | 0 / 0 / 0 |
| 合计 | 50 | 0 / 0 / 0 |

实际动态检查包括：独立穷举 oracle；两种 Python hash seed 的清单一致性；κ=12、重量≤2 仅生成所需支撑／符号的计数；全部负循环幂、绕回变号和普通循环移位的区别；公开有效但轨道不同的手工 h=1 样例；非单位 f 的默认拒绝；四种覆盖状态与实际输出空值语义；隔离进程阻断参考评价及格后端导入后实际生成候选并公开验证。

**代数重构测试**使用手工 n=8、q=17、h=x，P=[1,4]、C=[6,2,5,3]、T=[7,0]、u=[-1,1]。独立普通卷积后折叠 oracle 检查矩阵列方向；显式整数行组合构造格向量，验证 t(u)=(-A_T*u,0)、误差 v−t 和原坐标重构。raw 与 centered target 相差 17e_0；必须把实际目标代表与返回格向量配对。错误符号、尾部顺序或目标代表均被检测。零目标与零格向量只重构零对，公开验证拒绝；未声称解决非零短向量搜索。

静态核查包括历史配置／源码接口和封存摘要；没有动态检查新 worker、正式 runner、基加载、BKZ/Slicer 性能、进程树 supervisor 或内存计费，这些属于后续阶段。没有缺失依赖导致的跳过，没有安装或升级依赖。

## 完整性与本轮验收

[preflight.json](preflight.json) 记录运行前摘要和工作区状态；[sealed_integrity.json](sealed_integrity.json) 记录结束时比较：**1681 个受保护文件 SHA-256 全部一致，变化 0**。范围包含阶段 1 审计、历史 A/B、封存实现和已枚举的后端源码／编译扩展。另对 1 个真实私有参考文件仅比较 size、mtime_ns、inode，均未变化；没有读取其内容，故不声称对该私有文件进行了内容哈希核验。

| 本轮验收项 | 结论与证据 |
| --- | --- |
| 两种候选规则及独立对照 | 通过；test_candidates、candidate_checks |
| 数量、稳定顺序、ID、精确去重 | 通过；清单、跨规则／hash seed／重复列表测试 |
| 提交／完成数不冒充生成数 | 通过；均为 0，投影分组 null |
| 公开验证无参考依赖 | 通过；test_validation、test_separation |
| 负循环及 other_public_valid | 通过；test_offline 的实际手工例子 |
| 零目标与覆盖事件分离 | 通过；零对重构测试、覆盖四状态与 null/false |
| 小型代数重构 | 通过；test_reconstruction 4 项 |
| 保护原代码、后端和历史结果 | 通过；1681 个摘要一致及私有文件元数据一致 |

## 阶段 2B 的实际入口

未发现本轮纯代数检查中的阻断性错误。实现中曾核对到 q 边界不一致，已统一为 q>2 后纳入正式通过的测试；主模型 q=97 不变。纯求逆复用旧模素数算法，本轮验证使用 q=7/17，主模型为 97，未提供任意合数模数的通用正确性证明。

下一阶段可以从 `context_from_public → build_candidates → make_target → decoder → reconstruct → validate_public_candidate` 接入；参考数据只进入事后 `classify_against_reference/diagnose_coverage`。具体任务见 [next_stage_entry.json](next_stage_entry.json)，当前状态仅为提案，未启动。

接入时必须保留有序原坐标 T，并检查 P/C/T 分割与互斥；当前候选上下文只验证 T，P∩T 检查在离线诊断中已有，在线建基前还需接入。新规则采用重量优先顺序，不能混作旧字典序记录。SHA 候选 ID 与旧后端整数 target ID 需要显式映射；公开规范内容摘要与公钥文件字节摘要分别保存。精确目标别名、后端投影分组、实际提交／完成事件也分别记录。

D/E 应在相同原始／约化基、相同目标代表和输入顺序上配对，区分固定基全批与独立端到端首次公开有效退出。保存／加载基、约化参数一致性、Slicer 实际投影维数、来源及阶段成本、内存和超时进程树均待后续实现。本轮没有冻结未经实测支持的 β，也没有启动阶段 2B 或阶段 3。
