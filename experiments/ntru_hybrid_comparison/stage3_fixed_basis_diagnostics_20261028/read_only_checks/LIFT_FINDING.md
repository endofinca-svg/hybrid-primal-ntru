# D 提升路径的坐标运输问题

本节是本轮运行中发现的**实现解释**，补充启动前冻结的源码核查。没有修改已冻结解码器、后端、数值参数或已有结果，也没有运行修正后的解码算法。

## 观察事实

在同一个 index 2 固定基上，将原 E 的 80 个目标与 D-skip 每组的未随机化原始条目逐目标配对：

- E 的最后 40 个整数基系数、目标投影阶段记录的尾部 Babai 系数、D-skip 原始条目提升后的最后 40 个系数，**80/80 完全相同**。
- 前 172 个系数在 **80/80** 目标上不同。D-skip 这 80 个未随机化条目的完整范数平方均大于对应 E 结果。
- 例：ID 0，尾部 `[-1,0,0,0]`，E 完整范数平方 **58934**，D-skip 未随机化条目为 **2024789**；两者最后 40 个系数完全相同，但前 172 个系数的最大差为 **302**。
- 此处用 `entry_was_randomized=false`、`ancestor_slot_id`、目标 ID 和 group/sign 查找原始条目；D-skip 的内核进入与完成数均为 0。

详见 [逐目标系数证据](p2_E_vs_Dskip_original_coefficients.json) 和 [机器可读解释](lifting_transport_audit.json)。前一文件中 `actual_yr_matches_recorded_canonical` 核对数据库 canonical 条目与记录的 canonical 投影；另一字段 `projection_yr_matches_inserted_float32` 对负号成员额外乘了成员符号，其 false 不能视为数据库插入或符号映射失败，不用于此结论。

## 对应版本的源码依据

实际安装的是 fplll **5.5.0**，元数据位置：

`/home/inca/miniforge3/envs/g6k/conda-meta/fplll-5.5.0-hd20a173_0.json`

本地声明和接口说明在：

`/home/inca/miniforge3/envs/g6k/include/fplll/gso.h:184–210`

该头文件 SHA256 为 `87febb36518a189fb35e74a9246a7d06f0773c5f5014fcab19f13c4e2119a7d7`，记录于 [lifting_transport_audit.json](lifting_transport_audit.json)。安装包 SHA256 为 `35fe2404f9d2d92a3529528e58f224dff7bc7c5a1d6c721e6758ed83109db140`。

匹配版本的官方实现：[fplll 5.5.0，gso.cpp:382–409](https://github.com/fplll/fplll/blob/5.5.0/fplll/gso.cpp#L382-L409)。`to_canonical(v,start)` 只在所选子块上解三角方程，然后只组合 `B[start:start+len(v)]` 的原始整数行。它不加入前缀行以消去全局前缀 GSO 分量。

而仓库 [slicer_adapter.py:224](</home/inca/hybrid primal multi decoding for NTRU/experiments/ntru_e2e/slicer_adapter.py:224>) 的 `_lift` 把 40 个残差坐标传给 `to_canonical(...,start=172)`，随后从原目标减去这个环境向量并做全维 Babai。因此，这个向量是具有指定尾部投影的**子基代表**，一般不是仅含全局尾部 GSO 分量的正交投影向量。减去它会同时改变前 172 个 GSO 分量。

一个只说明接口语义的精确二维例子：整数行基 `[(1,0),(100,1)]` 的全局正交行为 `[(1,0),(0,1)]`。尾部坐标 `2/5` 用 `start=1` 转换得到 `(40,2/5)`；真正的全局正交尾部向量为 `(0,2/5)`。前者的额外前缀分量可能改变最终头部 Babai。此例仅作源码代数说明，没有调用任何数值后端。

## 结论和限制

上述语义与“所有尾部整数系数一致、所有头部系数改变”的实测证据一致，说明 D 的完整提升路径存在应优先修复的坐标运输问题。不能把 D-skip 相对 E 的范数恶化简单解释为随机化无效，更不能归给未运行的 Slicer 迭代。

输出仍由整数系数乘固定整数基得到，因此格成员关系、完整重构、范数恢复和公开验证的正确性检查仍成立。本发现影响提升质量和算法解释，没有把一个无效候选当作有效解。

全维补零坐标后转换、或直接固定尾部整数系数再解码前缀，是需要另行核查和测试的修复方向；**本轮没有运行它们，没有证明修复会恢复有效解**。已安排的原实现固定基诊断仍按冻结规则执行。下一步应先修复并验证提升运输，再评估停止规则，最后才考虑新的端到端对照。
