# 阶段 2C：固定基 Babai / Slicer 接入验收

**阶段 2C 验收通过。** 真实 Babai、真实 Slicer 数据库预处理和内核调用、逐目标输出转换、公开验证、进度守恒及同基配对均有实际证据。最终自动化测试 149 项通过，0 失败、0 错误、0 跳过。未执行 n=128 的约化、GSO 或解码，未运行参数扫描，未启动阶段 2D。

本轮授权来自 Prompt 4。保留阶段 1、2A、2B 历史文件和用户已有未提交内容；分支 `experiments/ntru-e2e-phase1`，HEAD `452be18c741a2d81c106d3bfe3847428ed57229b`。开始时 `git status` 为 `?? experiments/`、`?? main.pdf`。未找到适用的 AGENTS.md；没有安装依赖、重建后端、提交或 push。

## 新增实现

比较实验目录新增 6 个文件，没有修改旧比较层、封存求解器或后端：

| 文件 | 用途 |
| --- | --- |
| `progress.py` | 外部目标事件、互斥终结状态、候选／有效目标／精确解计数 |
| `fixed_backend.py` | 独立 GSO 的 E/Babai 和 D/Slicer；显式禁止再次约化，记录转换、投影组、逐目标提升和真实调用 |
| `paired_decode.py` | 固定基全批／首次公开有效模式，公开重构验证、阶段计时与结果保存 |
| `tests/test_progress.py` | 11 项状态与事件测试 |
| `tests/test_fixed_backend.py` | 22 项后端契约、精度、内部平移和异常测试 |
| `tests/test_paired_decode.py` | 25 项配对 worker、来源、模式、隔离及计时测试 |

[modified_files.json](modified_files.json) 提供完整路径和摘要。结果目录中的 `real_check.py` 是一次性、外部限时的小型接口验收脚本，不是完整预算 runner 或 supervisor。

## 固定基、精度与后端限制

E/D 都从 `load_input_bundle(require_reduced_verified=True)` 获得独立数据，复用同一整数基、候选序／ID、有序 T 和 `t_used`。每种方法拥有自己的 GSO、数据库及结果。E 全部目标共享一个 GSO，D 同一输入包只建立一次数据库；没有每个猜测重新 BKZ。

旧 Phase2Decoder / SlicerDecoder 的构造器会自动 LLL/BKZ，因此本轮不调用这些构造器，只复用已核查的纯映射方法。固定 G6K Siever 的构造器还会调用 `self.lll`；新子类明确拦截这一次请求，记录 `suppressed_without_calling_LLL`，之后的 lll/split_lll 请求直接拒绝。初始化、预处理及解码后逐项检查真实 GSO 整数基未变。真实记录中无额外基变换，新增 LLL/BKZ 调用均为 0。

当前适配层要求 `28≤sieve_dim≤min(d,compiled_cap)`，编译上限为 64；支持一个显式 BDGL block。底层 FHT 单块要求维数大于 16，而常规冻结入口按 28 维选块，所以已保存 d=6 包只做真实 E 检查。D 使用另一个手工构造、精确验证的 d=28 格，未扩大到 n=128。

共同约化来源分开记录：2B 的 n=4、d=6 包确实由 MPFR192 LLL＋β4 一轮产生；新的 n=15、d=28 包由显式整数幺模变换构造，det(U)=1，没有执行浮点约化。后者的协议是未执行的占位配置，不能把 MPFR192 写成该手工变换的实际计算精度。

两者本轮解码 GSO 均实际为 MPFR192。D 向 G6K 导出 float64 GSO 数据，目标投影／残差存储为 float32，最终系数乘基、格检查和重构使用 Python 任意精度整数。精度配置、注入 GSO 的实际类型／元数据、MPFR 当前全局精度及进程守卫必须一致；不允许存活 GSO 之间静默改变精度。U/UinvT 仅为 D 自己的恒等变换记账，未改变共同整数基。

## 模式、进度及重构

`fixed_basis_full_batch` 处理完整相同外部列表，发现有效输出后继续；基行预检关闭，D 不通过独立 Babai 预检绕开内核。`fixed_basis_first_public_valid` 只以公开验证为停止条件：已返回的当前批次继续摄取，之后不提交新批次。已在内核中执行的批量工作不能撤销，因此记录首个有效时已返回的终结事实和随后多处理的输出／时间。

所有外部目标采用 2B 的整数 ID 映射，不按返回位置猜测身份。状态为 unsubmitted、pending、completed、errored、cancelled，终结状态互斥，始终满足：

`submitted = completed + errored + cancelled + pending`。

合法环境格向量但公开验证失败，仍是正常完成；异常、错误向量格式／格成员、重复或冲突终结按协议错误处理。缺失终结记录保持 pending，不因属于某个投影组而自动完成。每个 D 外部目标必须完成自身提升；同组目标使用各自目标和尾部。候选数、有效候选数、有效目标数和完整 (f,g) 精确去重数分别记录，一个目标多个候选不重复计完成。

实际返回类型明确为整数基系数及环境格向量；两者同时返回时精确核对相乘关系。随后检查维数、整数性和原 q-ary 格成员，使用 `e=v_used−t_used` 重构，并调用 2A 的公开验证。不读取参考文件或离线覆盖标签，零尾部规则保持 skip，没有添加免费短向量搜索分支。

D 的投影准备还会进行一次尾部 Babai 平移。本轮逐外部目标保存完整整数系数、ambient_shift、projected_input、float64 归一化投影、float32／符号分组，并精确核对 shift 属于同格。最终提升使用原来的 `t_used`，不会把临时投影输入或 `t_raw` 混作重构目标。真实微格的这项内部平移为零；非零内部平移与非零模 q 居中平移分别有针对性测试。

E 有效输出来源为 `babai_only`；D 为 `slicer_tail_with_babai_lift`。D 确实调用内核，但无法把每个最终候选归因于某一次内核改进，故 `slicer_used_in_returned_candidate='unknown'`。投影前的 Babai 和内核后的 Babai 提升单独计数，不称为独立 Babai 预检或纯 Slicer 输出。

## 实际结果表

以下为最终源码版本的 real02；raw JSON、最终计时侧车、完整摘要及 CSV 均保存。S 为 [2B 的 n4/d6 包](../phase2b_results_20261026/backend_check.reduced_input.json)，起始基 SHA 为 `9c9c9a3f1ff0e94f09ed1bbf2c0fe561b66715bbd211d988be52b04438c184a1`。M 为 [手工 n15/d28 包](micro_n15_d28.public_input.json)，起始基 SHA 为 `11660468879eca246aff005857e322f08555a0d48f0c767b6a103a8703156387`。M 的 E/D 全部 7 项配对摘要完全一致。

S/C/Err/X/P/U 依次表示提交／完成／异常／取消／待处理／未提交；时间单位均为毫秒。full、first 分别为上述两个完整模式名称。

| 方法／输入 | 模式 | 目标总数 | S/C/Err/X/P/U | 有效目标 | DB预处理完成 | 内核调用 | 私有副本＋GSO | 额外预处理 | 解码 | 提升重构＋公开验证 | 总时间 | 停止原因 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| E / S | full | 2 | 2/2/0/0/0/0 | 2 | 0 | 0 | 198.615 | 0.081 | 0.117 | 0.808 | 213.091 | 全批完成 |
| E / M | full | 2 | 2/2/0/0/0/0 | 2 | 0 | 0 | 28.068 | 0.765 | 0.372 | 2.147 | 64.268 | 全批完成 |
| D / M | full | 2 | 2/2/0/0/0/0 | 2 | 1 | 1 | 27.070 | 636.104 | 0.123 | 4.013 | 707.894 | 全批完成 |
| E / M | first | 2 | 1/1/0/0/0/1 | 1 | 0 | 0 | 24.302 | 0.676 | 0.180 | 0.995 | 57.519 | 首个公开有效 |
| D / M | first | 2 | 1/1/0/0/0/1 | 1 | 1 | 1 | 28.300 | 3.909 | 0.076 | 2.469 | 79.873 | 首个公开有效 |

完整数据见 [actual_results.json](actual_results.json)、[actual_results.csv](actual_results.csv)。真实输出为 [E全批](real02.micro_E_full.json)、[D全批](real02.micro_D_full.json)、[E首次有效](real02.micro_E_first.json)、[D首次有效](real02.micro_D_first.json)、[旧小包E](real02.legacy_E_full.json)。完整投影转换和调用统计保存在每个结果的 backend_batches 中。

D 的每次真实检查都用一个 DB128、单线程、一个 block、4 buckets、multi_hash=1、max_iterations=1。每投影组包含总计 2 条目标 DB 条目，其中 1 条是随机化条目；随机重抽次数不可观测。全批两个外部正负目标共用一个投影组，但分别提升、重构和计数。实际调用 `RandomizedSlicer.bdgl_like_sieve` 正常返回，itercount_slicer 从 0 到 1。

这里使用 `sample_database_only` 构建真实格向量数据库，并开启 `force_kernel_iterations` 以确保进入内核迭代。它是明确的内核接口验收配置，不是生产 BDGL 预处理性能测试，也不证明 Slicer 强于 Babai。两个正负解属于一个符号等价类，2 个有效目标／精确解不等于两次独立密钥恢复。

实际共执行两个外部限时检查：real01 先通过；补齐内部目标平移记录、失败尝试计数及注入精度守卫后，以同一输入／种子执行 real02 再通过。两次均未超时，墙钟分别 1.648334631 秒和 1.654982321 秒，合计 **3.303316952 秒**，未超过 120 秒。每次外部 timeout=30 秒，TERM 后 2 秒 KILL 宽限。没有失败／超时被隐藏，未换种子或选择有利样例；[real01](real01.command.json) 及 [real02](real02.command.json) 的原始输出和结果全部保留。

最终检查启动前冻结了 214 个比较层／测试／直接依赖／运行时模块与夹具文件摘要，见 [real02.sources.json](real02.sources.json) 和 [runtime_before_real02.json](runtime_before_real02.json)。fpylll=0.6.4，fplll=5.5.0，固定 G6K revision 为 `bd72687b2ebbaff16fb692259f83b327bbcd9586`。检查结束后这些文件仍一致。

## 计时口径与正确性检查

使用单调时钟。互不重叠阶段为输入加载验证、私有副本/GSO 初始化、额外预处理、目标准备、解码、提升重构、公开验证、结果输出及 bookkeeping；总时间为这些阶段之和。GSO 阶段包含私有副本的安全重检。E 没有 DB 构建，但其准备检查仍有实际耗时。

内核时间属于 decode_seconds，调用记录中的内核时间只是嵌套诊断，不再次加总。共同建基／约化历史只保留来源，排除在本轮固定基解码墙钟外，不能视为零成本。表中冷导入与暖缓存不同，尤其 D 全批与 D first 的预处理时间不可用于排名；这也不是端到端加速结果。

结果主 JSON 写入前无法包含它自身最终写入时间，因此其 `result_output_seconds=null`，并明确指向 `<result>.timings.json` 权威侧车。侧车／返回值包含主结果实际序列化及写入时间；侧车自身的最终报告 IO 未计入方法内部 wall，完整外部命令 wall 包含它。阶段 2D 端到端预算必须按完整外部 wall 计费。

`first_public_valid.seconds` 从方法入口计时；progress 的相对时间从 ledger 建立时计时，两种起点明确区分。首次有效快照包含已经实际返回的批次终结事实。提前停止之后未提交的目标不判失败；批量后端已经执行的工作和后续输出摄取均记录。内存本轮为 null，没有用历史 RUSAGE 高水位替代。

最终自动化命令：

```bash
timeout --signal=TERM --kill-after=2s 60s /usr/bin/python3 -B \
  -m unittest discover -s experiments/ntru_hybrid_comparison/tests -t . -v
```

设置 PYTHONDONTWRITEBYTECODE=1；**149 通过，0 失败、0 错误、0 跳过**，unittest 3.835 秒，退出 0。包含复跑 2A/2B 的 91 项以及本轮 58 项。模拟／纯测试与实际后端结果分开保存；[tests_summary.json](tests_summary.json)、[实际命令](tests.command.json)、[完整日志](tests.stderr.txt)。新增边界覆盖正常无候选、公开验证失败、异常、取消、部分返回、多个候选、未知／重复／冲突 ID、分组逐成员提升、两种目标代表、参考隔离、隐藏约化及嵌套计时。

[real_output_validation.json](real_output_validation.json) 对最终 5 份真实输出重新执行纯公开验证、精确格成员／重构、逐成员目标平移和计时合计核查，全部通过；没有读取参考或再次调用后端。

## 保护内容与后续入口

[sealed_integrity.json](sealed_integrity.json)：1747 个受保护文件 SHA-256 全部一致，覆盖阶段 1/2A/2B、历史 A/B、封存实现及已枚举后端。另 1 个私有参考文件仅比较 size/mtime_ns/inode，未变化，未读取内容。

主参数包仅做清单／状态及文件摘要检查，未传给建基、约化、GSO 或解码入口。仍为 original_only、not_started，候选数 80、提交 0、完成 0、投影组 null，见 [main_package_guard.json](main_package_guard.json)。

尚未观测或未完成的部分：随机化重抽次数、每个候选是否由内核独有改进（unknown）、峰值内存、生产 BDGL 路径的真实验收、大参数解码效果、独立端到端成本、绝对预算及进程树清理。当前 API 支持生产 bdgl2 配置，但本轮真实验收明确仅覆盖上述小型采样 DB／强制内核路径。

阶段 2D 可调用 `run_fixed_basis` 或低层 `build_backend → preprocess → decode`，继续复用公开验证、精确转换和 TargetProgress。需要新增独立方法端到端执行、失败／超时／所有重试完整成本、绝对 wall 预算、TERM/KILL 后验证后代清理，以及定义明确的单实验内存统计。详见 [next_stage_entry.json](next_stage_entry.json)。本轮不提前实现这些完整功能，也不自动启动阶段 2D。
