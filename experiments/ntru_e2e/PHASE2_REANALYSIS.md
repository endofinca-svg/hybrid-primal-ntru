第二阶段首级重量结果再分析（2026-10-22）

当前证据支持：`n=64, w_f=4, w_g=8` 的实例在所选 pattern 格上已经容易到 LLL/Babai 就能恢复秘密，Slicer 在此基础上没有增量。现有对照没有隔离 pattern 的作用，不能据此得出“pattern 无优势”或“整个方法无效”。以下分析只读取该首级重量的既有结果；新增的是离线算术诊断，没有生成新实例、运行格约减、建库或 Slicer 内核，也没有探索其他重量或 `n=128`。原报告、冻结配置、命令日志和既有哈希清单保持不变。

“截断”具体表示什么

它表示一次试验在全部预定方法完成前终止。它与完整执行后找不到有效秘密不同，也与事后的独立评估超时不同。先前“12 条完整性检查通过”指文件、目标、候选与来源证据自洽，不表示 12 次在线试验全部完成。

| 试验位置 | 数量 | 中止时实际执行到哪里 | 终止证据 |
| --- | ---: | --- | --- |
| instance_01，两组选择的 pattern 0 | 2 | 格筛库已建，进入投影准备；未启动 Slicer 内核 | command timeout，worker 收到 SIGTERM |
| instance_01，两组选择的 pattern 1 | 2 | 格筛库构建中 | command timeout，worker 收到 SIGTERM |
| instance_02，前三项 | 3 | BKZ Babai 尚未结束 | command timeout，worker 收到 SIGTERM |
| instance_02，最后一项 | 1 | LLL rows 尚未结束 | worker 收到 SIGTERM，外层命令元数据未完成，以 interrupted_censored 保留 |

八项都没有 `slicer_call_started`。最后一项的现存记录不足以判定是谁或什么事件发出了终止信号，不能直接称为“人为终止”。其完整 30 秒预留只用于保守计费，不是实测执行了 30 秒。

正常超时的七项受到每 trial 30 秒总预算约束，其中 2 秒用于监督清理、0.1 秒用于测量预留，实际子进程超时线为 27.9 秒。保存的检查点开销为 18.51–20.66 秒，约占保存的 worker wall time 的 67%–75%。这包括不断序列化整个结果、写入和替换 JSON，以及事件写入，不只是磁盘等待。早先的基线会验证并保存大量候选，即使已经恢复秘密也继续运行，因此后置的 Slicer 阶段被系统性挤压。不能把这八项计算为“八次 Slicer 解码失败”。

为什么这个参数下 LLL 已经足够

`f` 只有 4 个非零系数。行向量基线使用零目标，并将 `P` 和 `T` 的坐标都补为零；这相当于寻找满足 `f_(P∪T)=0` 的秘密旋转。此处 `|P∪T|=2+4=6`。每个约束位置至多排除 4 个旋转，因此至少有

\[
64-4\cdot6=40
\]

个无符号旋转仍满足这些零约束。计入正负号，本例有至少 80 个秘密代表留在格中，范数均为 `sqrt(w_f+w_g)=sqrt(12)`。它们无需猜测非零 tail，也无需 Slicer，即可作为很短的格向量被发现。

实际数据与这一机制一致：

| 实例 | pattern 0 的零 tail 秘密代表数 | pattern 1 的零 tail 秘密代表数 |
| --- | ---: | ---: |
| instance_01 | 82 | 82 |
| instance_02 | 88 | 90 |
| instance_03 | 84 | 82 |

11 个完整 LLL rows 方法臂和 11 个完整 BKZ rows 方法臂，都找到了各自全部零 tail 真值旋转；唯一未完成的 LLL rows 臂保留了 54/90 个。它们全部属于同一秘密旋转/符号等价类，不是许多独立密钥。这个判断来自真实公开攻击输出与事后真值轨道的比较，不是把真值注入攻击。

更强的离线结构核查表明，四个完整 trial 的 LLL 和 BKZ 基恰好前 58 行满足整数环恒等式 `f*y=g*z`。其中 `y` 是格行的前 64 坐标，`z` 是把 core 坐标扩展到 64 维并在 `P∪T` 补零；恒等式在 `Z[x]/(x^64+1)` 内成立，没有模 q。`f` 模 257 可逆意味着相应整数乘法矩阵的行列式非零，因此 `f` 在有理数环内也可逆。这 58 个独立基行已张成全部 58 维秘密图子空间。在 BKZ 基中，这 58 行的平方范数为 12–90，下一行平方范数跃至 13,422–13,550。秘密结构在 LLL 阶段已显著分离；Slicer 的后 32/40 维投影位于其后的部分。

为什么 Slicer 没有增加恢复结果

四个完整 trial 中，每次 Slicer 都返回 8 条候选，与同 trial 的 BKZ Babai 输出逐 target 完全相同。相对 LLL Babai 的重合数依次为 8、2、8、2，不能误写成全部与 LLL Babai 相同。Slicer 相对零 tail 行方法确实增加了非零 tail 的原始秘密代表，但这些代表已经由 Babai 找到，而且同属原秘密的等价类。因此，对完整五项基线并集，新增原始候选和新增等价类都是零。

更直接的离线检验是：只加载保存的基并重建 GSO，重放既有 adapter 的初始 Babai 位移、带符号 float32 投影和回填，不调用任何约减、G6K、建库或随机筛内核。四次都能直接得到历史 Slicer 最终保存的全部 8 条候选，连 `(target_id, v, coefficients, residual_sq)` 都完全一致。

这是离线反事实诊断，证明这些输出在该基上无需随机迭代也可获得；历史日志没有内核前条目的快照，所以不能据此断言历史内部每条输出实际上来自哪个条目。源码确实将未随机化的初始投影保存进 target DB，然后才添加随机化条目。

四次累计 32 条 Slicer 记录仅对应 10 个不同的原始 `(f,g)`，仍为一个秘密等价类。pattern 0 有 28 个兼容非零 tail 的有符号旋转，但只对应 8 个不同 tail；pattern 1 的 30 个旋转也只对应 8 个 tail。同一 tail 的两个正确旋转，其差位于已被前 58 行张成的秘密子空间，因而它们在 Slicer 的尾部投影中相同。提交 80 个目标并不意味着穷举每个目标的所有秘密代表。Slicer 接口可以返回同一 target 的多个向量；本次观察是每个有效 tail 只保存了一条。

为什么完整 trial 的时间也没有优势

| 项目，四个完整 trial | 实测范围 |
| --- | --- |
| 最早基线首个有效候选，含该方法必要 setup | 1.897–2.039 秒 |
| Slicer 首个有效候选，含其必要 setup | 4.244–4.854 秒 |
| Slicer 的格筛建库 | 0.741–1.211 秒 |
| 真正的 `bdgl_like_sieve` 内核 | 5.45–12.75 毫秒 |
| 内核迭代数 / 上限 | 1–2 / 400 |

首候选比较已排除其他基线臂和检查点耗时，不能用“串行执行到 Slicer 的 16–17 秒”作方法速度比较。四次内核也均无 target UID 插入拒绝。因此，当前没有证据把无优势归因为内核迭代预算耗尽或 target 映射丢失。主要额外成本在建库、整批投影与随机化准备、回填和公开验证；同一问题已由便宜的行扫描或 Babai 解出，额外处理没有换来新的恢复能力。checkpoint 开销是七次预算超时的重要原因，但不能单独解释四次完整结果中的零增量；另一次中断的触发原因无法确认。

pattern 的贡献尚未单独测量

六项方法全部在相同 `k=2, kappa=4` 的 pattern 格上工作。这里比较的是在同一 pattern 构造上增加 Slicer 的作用，没有无 pattern 的 `k=0` 对照，也没有原始 NTRU 格的独立 primal 恢复基线。pattern 可能已经帮助这些基线恢复；现有数据既不能证明，也不能排除其净优势。

本次维数仅从 128 降至 122，其中 pattern 减少 2 维，tail 猜测再减少 4 维；只考察两个固定 pattern 和两个耦合的 `(beta,s)` 选择，没有做全局代价优化，也未测试论文的熵采样流程。不能把当前固定重量 toy 的完整枚举和串行取证开销直接对照生产参数的指数复杂度估计。相关 [KKNM25 原始论文](https://eprint.iacr.org/2025/1910) 讨论的是困难 BDD 区间中混合攻击的参数权衡和批量求解收益，并不意味着任意小参数都应比 Babai 更快。

下一轮若要判断组合方法的优势，需要另立冻结方案：先把取证开销与方法耗时分开，采用统一实现版本；在相同 tail 参数和成功判据下加入 `k=0` 对照；再寻找 LLL 行扫描与 Babai 尚不能直接恢复、但降维与批量解码能够发挥作用的区间。仅延长当前 trial 时间或增加 Slicer 迭代上限，并不能解决“基线已经足够”和“未隔离 pattern 贡献”这两个问题。本次没有启动这些新实验。

可复核证据：

- [逐试验中止位置、耗时和候选交集](runs/phase2_n64_first_weight_analysis/trial_reanalysis.json)，对应 [只读分析脚本](runs/phase2_n64_first_weight_analysis/inspect_trial_evidence.py)。
- [初始投影离线重放结果](runs/phase2_n64_first_weight_analysis/initial_projection_replay.json)，对应 [重放脚本](runs/phase2_n64_first_weight_analysis/replay_saved_projections.py) 和 [命令记录](runs/phase2_n64_first_weight_analysis/replay_projection_command.command.json)。
- [秘密轨道与整数子空间诊断](runs/phase2_n64_first_weight_analysis/secret_geometry.json)，对应 [离线几何脚本](runs/phase2_n64_first_weight_analysis/inspect_secret_geometry.py)。
- [原独立评估](runs/phase2_n64_first_weight/evaluation_phase2.json) 与 [原实验报告](PHASE2_REPORT.md)。
