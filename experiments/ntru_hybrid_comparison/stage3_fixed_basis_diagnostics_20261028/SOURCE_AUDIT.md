# 固定基 Slicer 源码核查

原后端和生产默认均保留。下列行号指启动前冻结的原文件；诊断差异为 `diagnostic_backend.patch`，原文件副本在 `backend_original/`。本文件是实现解释，实测数值另见结果。

## 原停止条件的准确含义

- `experiments/ntru_e2e/.deps/g6k_hybrid/kernel/slicer.cpp:15` 的 `recompute_data_for_entry_t` 把 `e.len` 计算为归一化坐标 `yr` 各项的平方和；不是范数开方。`Entry_t.yr` 是 float32，`Entry_t.len` 是 double；写入压缩条目 `cdb_t[].len` 时再转成 float32（原 `kernel/siever.h:333`）。因此停止排序使用的是 float32 保存的平方长度。
- 归一化来自 `experiments/ntru_e2e/slicer_adapter.py:200` 和 `fixed_backend.py:389`：投影坐标为尾部 GSO 系数乘 `sqrt(r_i / gh_sub)`；`gh_sub=gaussian_heuristic(GSO.r()[-40:])` 本身为平方尺度。
- 原 `fixed_backend.py:452–456` 将每个公开完整残差平方半径（本例 `36 - sum(u_i^2)`，等于 `36 - 非零尾部个数`）除以 `gh_sub`，取全部成员中的最小值，再乘 `1.0001`，设置为 `proj_error_bound`。完整目标范围是 80 个非零三元尾部，最小半径平方是 32。
- 原 `slicer_adapter.py:50` 的 `_safe_saturation(N,Nt)` 返回 `index=min(N-1,Nt)`、`scalar=(index+0.5)/Nt`。原 `kernel/slicer.cpp:513–517` 计算整数 `saturation_index = Nt * saturation_scalar` 并在**每次迭代体之前**检查已按长度递增排序的 `cdb_t[saturation_index].len < proj_error_bound`。严格小于，不是小于等于。
- 对正常本例 `N=640`、`Nt=40`，index=40，是第 41 小的条目；scalar=1.0125。只保证至少 41 个**条目**的归一化投影平方长度低于统一阈值，相当于至少 41/640 的条目；它不检查每个投影组，更不保证 41/40 个不同组，也不保证每个原始目标。原注释中的“unique targets”不能改变实际条目排序表达式。实际 group 数量和插入完整性以本轮输出为准。
- 投影是完整残差的必要下界，且此处另有 `1.0001` 松弛；投影停止条件不能保证 212 维完整残差落入返回半径，更不能保证 256 个 f/g 系数的重量、范数、可逆性和公开关系。随机化数据库建立后已可能有足够多的短投影条目，因而第一次迭代前可以停止。

## 目标映射与提升路径

- `fixed_backend.py:245–292` 的 `RecordingMapper` 用每个原目标的尾部 Babai 系数构造精确格平移，仅用于准备投影。完整 `t_used` 保留，不改变约化基或输入包。
- `slicer_adapter.py:40` 把投影量化为 float32，再按第一个非零坐标归一化正负号；`_groups`（:210）只合并完全相同的带符号 float32 投影。成员记录外部整数 ID、原目标、返回半径、sign。
- `fixed_backend.py:423` 为第一批使用 seed=10002；`slicer.cpp:129–210` 每组加入 1 个未随机化条目和 nrand−1=15 个随机化条目。随机化包含数据库向量组合以及一次方向选择，已可改变投影残差；这发生在 Slicer 迭代之前。UID 冲突可能拒绝插入；新入口遇到不完整插入即停止受影响实验。
- `fixed_backend.py:494–523` 遍历最终条目，以 Entry_t.i 查组，然后对组中每个成员用自己的 sign 和原目标 `_lift`；不是只提升每组一个结果。完整 `_lift` 在 `slicer_adapter.py:224`：将归一化投影残差换回尾部 GSO，转为环境坐标，对 `target - projected_residual` 做完整 Babai，再用整数系数乘完整整数基，计算 `||v-target||²`。只在此值不大于该成员半径时返回去重后的候选。
- `algebra.py:211` 的 `reconstruct` 取残差前 n 项为 g，后 |C| 项回填 f_C，f_T 为猜测尾部，f_P=0。因此 `||f||²+||g||² = ||v-target||² + ||u||²`。完整公开验证由 `validation.py:42` 重查三元系数、重量 (12,24)、范数平方 36、h*f=g 与 f 可逆。

## force_kernel_iterations 实际效果

`SlicerConfig` 要求该开关必须同时 `interface_test_mode=true`（`fixed_backend.py:130–133`）。实际 decode 中唯一数值作用是 `fixed_backend.py:456` 把 `proj_error_bound` 设为 0；内核只在 `slicer.cpp:517` 的饱和条件使用该阈值。开关自身不替换目标、不改数据库或随机化，也不会自动将预处理切换到 sample-only，但它附带测试模式标志，且配置允许另一个独立的 sample-only 选项。不能因为历史测试同时用了 sampling-only，就说 force 自身修改了数据库。

本轮不调用该开关。独立诊断构建只把原判断包成 `!diagnostic_bypass_saturation && 原判断`；normal 默认 false、probe 显式 true，且公开阈值原样保留。另改迭代上限；所有其他正确性检查、退出路径和外部截止保留。D-skip 只跳过内核调用，保留原调用开头的压缩目标排序，然后消费全部实际条目和正负成员完成原提升流程。

## 计数器、替换与超时证据

- 原 `itercount_slicer`（`slicer.cpp:542`）在整次分桶、队列处理、替换与重排完成之后递增，表示**完成次数**，不是进入次数；不能从 max_iterations=400 推断实际 400 次。
- 原 `redsucc` 在 :235 成功产生可进入事务 DB 的短向量时递增；它不是最终数据库替换次数，也不是公开解成功数。原 `replacements` 在 :286 真正覆盖数据库条目时递增，原 `reds_during_randomization` 是 :202 随机化条目相对原投影改善超过 1% 的计数。
- 原 Cython `_stat_get_fullscprods`（`g6k/slicer.pyx:70`）错误调用 xorpopcnt 总数 accessor。该字段不得解释为准确内积次数；本轮不修它以免改变原接口，也不使用它判断效果。
- 原替换是对全局最长条目操作（`slicer.cpp:275–350`）。新向量继承**来源**组 ID，目标槽位可能属于另一组，故所有结果不能简单按“槽位”视为同一目标。新构建记录创建的 parent UID→new UID 和替换的 old UID→new UID/目标槽位，以恢复来源祖先。逐条结果同时保存 group、sign、slot、UID；投影最优和完整范数最优分开记录。
- 新构建在迭代体开始前独立记录 entered，原计数递增后记录 completed；每次边界向 `kernel_iterations.tsv` 追加已完成/进入次数及替换、redsucc。超时最多能可靠保存最后一个完整检查点，最后一个未完成迭代中新增的替换数仍可能未知；不把旧值当作精确最终值。Python stage 开始时持久化 null 耗时，只有正常完成才填精确墙钟。

## 状态配对和额外诊断成本

每个 D 配置从同一已保存基、参数和 seed 独立重建正常 BDGL，不继承前一个配置修改。保存有序 lattice DB、目标 DB slot/UID/group/坐标/float 长度、GSO 的 gh_sub、LSH seed，以及两个 MT19937 RNG 的完整流状态。RNG 读取使用 const ostream 序列化，不抽取随机数。对上述内容计算摘要，独立验证器比较配对；UID hash 桶结构、simhash 表本体和线程池内部状态不直接导出，报告列出限制。

获取 pre 完整残差时另建同一整数基的独立 MPFR192 GSO，并在独立 mapper 上提升；不接触原 GSO 或 RNG。完成后重新读取原 Slicer snapshot 并要求摘要完全不变。诊断原始 snapshot 保持插入顺序；停止统计只排序 Python 拷贝的长度。D-normal 在原入口排序，D-skip 在同一边界排序一次，probe 在同一原入口排序。额外提升、全部条目的诊断公开验证和日志保存耗时单列。阶段墙钟与其内测组件有包含关系，不相加推导算法或端到端耗时。

准备测试包括真实 n=15/d=28 的正常 BDGL 微型 fixture：独立重建状态完全相同；诊断 normal 的最终完整 snapshot 与原 FixedSlicerBackend.decode 正常路径完全相同；skip 不调用内核但完成成员提升；probe 在原投影阈值已满足的同一前态仍进入并完成恰好 1 次。小 fixture 的低维 BDGL 警告和日志保留，不作为主实例实验结果。
