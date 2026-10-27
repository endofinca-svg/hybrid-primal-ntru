# 阶段 1：现有求解代码只读核查

核查日期：2026-10-26。范围为 `experiments/ntru_e2e/attack.py`、`solve_phase2.py`、`phase2_adapter.py`、`slicer_adapter.py`、`algebra.py`，以及为核对限时链路读取的 `run_phase2.py`、`record_command.py`；另核对 q=97 的 `baseline_tour2/config.json`、`solve_baseline_tour2.py` 和 `method2_pattern/solve.py`，用于比较已有 A/B 与 phase2 的约化后端口径。本次没有读取 private/参考密钥，没有运行 solver、evaluate 或测试，没有修改原代码和结果。

以下链接相对本审计文档，标签给出仓库内路径及已核对行号。这里确认的是当前源代码能力；不能据此声称历史运行使用了同一代码版本，也不能代替历史结果的 provenance 和独立验证核查。

## 1. 可直接复用的部分

### 1.1 公开输入、环与格构造

- [solve_phase2.py:39–88](../../ntru_e2e/solve_phase2.py#L39) 对 public 格式和在线配置采用字段白名单，并核对维数、模数、公开总重量；[113–121](../../ntru_e2e/solve_phase2.py#L113) 只加载公开实例和在线配置。在线配置不接受参考短向量或 keygen seed 字段。
- [algebra.py:47–73](../../ntru_e2e/algebra.py#L47) 实现 `Z[x]/(x^n+1)` 的负循环乘法及带符号旋转；[153–159](../../ntru_e2e/algebra.py#L153) 定义乘法矩阵 `A` 满足 `A*f = h*f mod q`。
- [algebra.py:175–208](../../ntru_e2e/algebra.py#L175) 构造行基 `[[q*I_n, 0], [A_C^T, I_core]]` 和目标 `(-A_T*u, 0)`。目标前块可在模 q 下居中，实际目标被保存供重构使用。
- [algebra.py:211–233](../../ntru_e2e/algebra.py#L211) 从 `v-target` 重构：前块为 `g`，后块填入 `f_C`，尾部为 `f_T=u`，pattern 坐标为零。由 `len(C)=n-k-κ`，格维数为 `2*n-k-κ`；κ=0 时为 `2*n-k`。具体实例是否确为 n=128、q=97、公开总重量 12/24，应以配置和 public 证据另行核对。

### 1.2 一次约化、同基配对与独立来源

- [solve_phase2.py:322–349](../../ntru_e2e/solve_phase2.py#L322) 每个 worker 只构造一个 `Phase2Decoder`、调用一次 `reduce_bkz()`，先运行 BKZ 基行/Babai，再建 Slicer 数据库并解码。没有每个尾部猜测重新 BKZ 的实现。
- [phase2_adapter.py:148–168](../../ntru_e2e/phase2_adapter.py#L148) 在 BKZ 后执行统一 LLL 规范化，再保存基；[183–202](../../ntru_e2e/phase2_adapter.py#L183) 对 G6K 初始化和数据库构造前后的基逐项比较，发生变化即报错。因此当前同 worker 的 BKZ-Babai/Slicer 配对有明确的同基约束。
- [solve_phase2.py:336](../../ntru_e2e/solve_phase2.py#L336) 和 [346](../../ntru_e2e/solve_phase2.py#L346) 分别保存 BKZ 与 Slicer 使用的行基，可供事后核对。所审实现未提供直接加载已保存约化基、只运行一个指定解码器的通用入口。
- [phase2_adapter.py:117–139](../../ntru_e2e/phase2_adapter.py#L117) 是独立逐目标 Babai，候选来源记为 `babai_diagnostic`。[slicer_adapter.py:290–328](../../ntru_e2e/slicer_adapter.py#L290) 真实调用 `bdgl_like_sieve`，再对其残差做 Babai 提升，候选来源记为 `slicer`；[366–369](../../ntru_e2e/slicer_adapter.py#L366) 明确直接 Babai 不是 `decode()` 的 fallback。Slicer 后续的 Babai 提升不能与直接 Babai 对照混为同一来源。
- 本次所审五个求解文件没有尾块枚举解码器，不能把尾部候选枚举当作格上的枚举解码器。是否可从仓库其他模块复用，需在阶段 2 前另行确认。

### 1.3 精确公开检查

- [solve_phase2.py:203–254](../../ntru_e2e/solve_phase2.py#L203) 采用统一的精确格成员、系数重建、距离、重构和公开校验流水线。
- [algebra.py:266–303](../../ntru_e2e/algebra.py#L266) 检查非零、三元系数、总非零重量、`f` 可逆以及 `h*f=g` 和 `g*f^{-1}=h` 的公开关系。
- [solve_phase2.py:305–306](../../ntru_e2e/solve_phase2.py#L305) 对猜测 u 使用公开半径平方 `w_f+w_g-HW(u)`；基行分支使用 `w_f+w_g`（[209–213](../../ntru_e2e/solve_phase2.py#L209)）。接受时精确检查残差距离，并对公开合法固定重量候选核对距离与半径相等（[235–243](../../ntru_e2e/solve_phase2.py#L235)）。

## 2. pattern、尾部与候选顺序的实际定义

- [solve_phase2.py:91–99](../../ntru_e2e/solve_phase2.py#L91) 由 solver seed 使用 `random.sample(range(n),k)` 采样随机位置集合。它不是连续区间。相同 seed/pattern index 在各 choice 下重放相同 RNG 序列。
- [algebra.py:162–172](../../ntru_e2e/algebra.py#L162) 先排序 P，从剩余坐标中取最大的 κ 个作为 T，其余作为 C。尾部位置目前不是随机抽样。P 与 T 不相交由构造保证。
- 当前 pattern 采样没有跨尝试去重；需区分“尝试编号不同”和“位置集合首次出现”。
- [algebra.py:248–263](../../ntru_e2e/algebra.py#L248) 枚举 `1≤HW(u)≤min(κ,w_f)` 的全部三元尾部，最后执行 `guesses.sort()`。因此最终是全局字典序，并非按重量从小到大处理。κ=4 且 w_f≥4 时恰为 80 个非零尾部。
- [solve_phase2.py:301–306](../../ntru_e2e/solve_phase2.py#L301) 一次生成并保存 guesses/targets/bounds；[283](../../ntru_e2e/solve_phase2.py#L283) 和 [348](../../ntru_e2e/solve_phase2.py#L348) 向 Babai 与 Slicer 传入同一列表与同一外部 ID 顺序。

**输入列表顺序相同，不等于内核处理顺序相同。** [slicer_adapter.py:210–222](../../ntru_e2e/slicer_adapter.py#L210) 将相同及正负等价的 float32 投影分组，但保留各成员的原始 target ID；[307–328](../../ntru_e2e/slicer_adapter.py#L307) 按内核返回的残差遍历并逐成员精确提升。这与 Babai 的逐目标顺序不同，应在结果中同时记录统一输入顺序、内部合并和实际覆盖。

## 3. 候选去重、覆盖与零目标缺口

### 3.1 当前记录能说明什么

- [slicer_adapter.py:176–198](../../ntru_e2e/slicer_adapter.py#L176) 要求同批外部 target ID 唯一。[255–274](../../ntru_e2e/slicer_adapter.py#L255) 根据目标数据库实际增长建立内部映射，插入失败记录拒绝；[313–325](../../ntru_e2e/slicer_adapter.py#L313) 将内核 ID 映回原目标并做精确距离检查。
- [slicer_adapter.py:351–361](../../ntru_e2e/slicer_adapter.py#L351) 对插入碰撞的目标用 singleton 数据库重试，并记录仍未解决者。这些重试有实际开销，不能遗漏。
- [solve_phase2.py:244–249](../../ntru_e2e/solve_phase2.py#L244) 对每 arm 的输出候选以 `(target_id,vector)` 去重；[slicer_adapter.py:320–325](../../ntru_e2e/slicer_adapter.py#L320) 同样去重。此处不能直接解释为输入尾部列表或完整 ambient targets 的统一去重统计。
- 原始非零空间大小记录在 [solve_phase2.py:303](../../ntru_e2e/solve_phase2.py#L303)，唯一投影数由 [slicer_adapter.py:362](../../ntru_e2e/slicer_adapter.py#L362) 记录。旧 `attack.py` 另有精确 ambient 目标去重数（[129](../../ntru_e2e/attack.py#L129)），phase2 worker 未以同字段统一记录。

**`processed_guess_ids` 只是开始提交给 Slicer 内核的目标 ID，并非已经完成解码的目标。** [solve_phase2.py:262–263](../../ntru_e2e/solve_phase2.py#L262) 在 `slicer_call_started` 时更新它。整次内核调用若超时，这些 ID 也已经计入。Babai 中断时，没有单独保存那些“已尝试但没有合格候选”的目标 ID。新的完成率统计必须另设 submitted/completed/未覆盖或删失口径。

### 3.2 需要补齐的候选策略

当前 `enumerate_tails(κ,w_f)` 没有独立的尾部重量上限选项；[solve_phase2.py:26–28](../../ntru_e2e/solve_phase2.py#L26) 与 [54–58](../../ntru_e2e/solve_phase2.py#L54) 的配置白名单也没有该参数。对 w_f=12、κ≤12，会全枚举 `3^κ-1` 个尾部，不能直接充当新方案中 `HW≤2` 或 `HW≤3` 的性能扫描。阶段 2 应添加并冻结候选规则、去重策略、顺序和原始／规则过滤／去重／实际提交／完成数量。

### 3.3 零猜测与非零短向量分支

- `enumerate_tails` 排除零尾部；κ=0 时返回空候选列表（[algebra.py:255–263](../../ntru_e2e/algebra.py#L255)）。不能把空列表的 CVP 视为 A/B primal 搜索。
- [solve_phase2.py:209–213](../../ntru_e2e/solve_phase2.py#L209) 和 [270–279](../../ntru_e2e/solve_phase2.py#L270) 有独立 ±基行扫描，使用 zero target/zero tail，适合复用为非零 primal 分支基础。worker 仍会继续建库并运行其余 arms，尚非独立 A/B 端到端入口。
- 旧 [attack.py:143–151](../../ntru_e2e/attack.py#L143) 的基行恢复仅记入 diagnostics，真正 `recovered` 只在 [179–191](../../ntru_e2e/attack.py#L179) 的 Slicer 路径赋值。因此不能把该旧入口的 κ=0 直接当作成功判据正确的 A/B 求解器。

## 4. 新比较口径与已有 worker 的差异

### 4.1 成功定义应分两层

依用户本次总说明，在线恢复成功按预先规定的**公开合法短向量**约束判定；事后另列其与参考向量的关系：原参考、符号／负循环旋转等价，或其他公开合法向量。`validate_candidate` 已支持第一层，不依赖原数组相同。不能沿用此前“只有参考等价才算成功”的旧统计而不标注口径变化。

离线应分列至少三类事件：pattern 有兼容参考旋转、非零尾部被候选列表覆盖、解码器实际重构公开合法向量。输入覆盖、Slicer 提交覆盖与最终重构完成率均不能相互替代。当前所审文件不读取参考向量，所以这些分类应继续由独立 evaluate/audit 承担，不能回流在线选择。

### 4.2 β、tours 和 sieve_dim 独立记录

- [solve_phase2.py:78–84](../../ntru_e2e/solve_phase2.py#L78) 的 choice 分别给出 `bkz_beta`、`sieve_dim`；κ 来自坐标划分，三者不是同一参数。
- [phase2_adapter.py:39–49](../../ntru_e2e/phase2_adapter.py#L39) 实际要求 `28≤sieve_dim≤dim`。在线输入验证允许更小 sieve_dim，但 decoder 随后会拒绝，阶段 2 应统一验证边界。
- [phase2_adapter.py:157–158](../../ntru_e2e/phase2_adapter.py#L157) 使用 fpylll `BKZ.Param(beta,max_loops=...,flags=BKZ.MAX_LOOPS)`。这是最大循环限制，不是已测量的实际 tours，也不能自动称为恰好完成固定两轮。
- [phase2_adapter.py:189–190](../../ntru_e2e/phase2_adapter.py#L189) 的 Slicer 投影是约化基末 `sieve_dim` 维。每个 worker 跑一个固定 β choice；不是此前逐 β、每 β 两轮扫描的同一路径。

### 4.3 既有 A/B 与 phase2 的约化后端并不相同

这是跨方法归因前必须解决的具体公平性差异：

- 既有 q=97 A 基线的 [baseline_tour2/config.json:12–24](../../ntru_compare_n128_w12_w24_q97/baseline_tour2/config.json#L12) 规定 LLL `delta=0.99, eta=0.51, method=proved`、MPFR 192 bit、从 β=30 逐一递增、每 β 严格两轮、clean 不跳过第二轮，以及明确的默认策略／高 β 扩展策略。
- B 的 [method2_pattern/solve.py:170–184](../../ntru_compare_n128_w12_w24_q97/method2_pattern/solve.py#L170) 设置精度、加载策略并记录策略文件哈希；[197–215](../../ntru_compare_n128_w12_w24_q97/method2_pattern/solve.py#L197) 显式调用 proved MPFR LLL，使用 MPFR GSO，传入加载的 `strategies` 并逐次执行 `reduction.tour(params)`。两轮约定允许公开成功或硬超时提前结束，不允许仅因 clean 省略第二轮。
- phase2 的 [phase2_adapter.py:81–88](../../ntru_e2e/phase2_adapter.py#L81) 使用 `long double` GSO 和默认 `LLL.Reduction(self.G)()`，没有显式采用上述 proved MPFR192 约定；[157–158](../../ntru_e2e/phase2_adapter.py#L157) 使用 `BKZReduction(...)(BKZ.Param(...,max_loops=...,flags=BKZ.MAX_LOOPS))`，没有显式传入并记录与 A/B 相同的策略字节及哈希，也不是逐 β 严格两轮的调度。

因此，当前同 worker 的 D/E 同基比较有其内部公平性，但将历史 B 的总耗时与当前 phase2 D/E 直接相比，差异可能同时包含数值精度、LLL、策略和调度成本，不能全部归因于尾部猜测或 Slicer。相同名义 β 或 `max_loops=2` 不能消除这个差异。

阶段 2 应建立共同约化接口并冻结精度、LLL 方法/参数、策略及哈希、实际 tours/停止行为，或把后端差异作为显式实验配置，为 B 和 D/E 各自给出合理调参且成本完整的结果；必要的 B 补测应单列新数据。不能悄悄替换历史后端后继续沿用原耗时。固定基 decoder 试验仍可复用同一份保存基，独立于端到端后端调参问题。

### 4.4 阶段试验不能直接称端到端对照

[solve_phase2.py:311–350](../../ntru_e2e/solve_phase2.py#L311) 固定顺序运行 direct-tail、LLL rows/Babai、BKZ rows/Babai、Slicer，成功后也继续。它很适合配对阶段实验；不能把各 arm 的估算耗时直接当作独立 A–F 方法在相同端到端预算下的完成率。

[145–150](../../ntru_e2e/solve_phase2.py#L145) 已明确区分 setup+operation、首次合法候选的独立方法估算时间、实际 worker wall 和 checkpoint I/O。阶段 2 应保留这些区别，并增加独立方法选择、公开停止条件、失败 pattern 和重试成本累计、相同总预算及删失标记。

现有计时可复用但需补细：

- [301–308](../../ntru_e2e/solve_phase2.py#L301) 将公钥矩阵、建基、全部候选及目标生成合并为 construction，尚未拆开。
- [326](../../ntru_e2e/solve_phase2.py#L326)、[335](../../ntru_e2e/solve_phase2.py#L335)、[345](../../ntru_e2e/solve_phase2.py#L345) 分别记录 LLL setup、BKZ setup 和建库；重构／公开验证并入 arm operation。
- worker 的 [173](../../ntru_e2e/solve_phase2.py#L173) 记录自身累积峰值 RSS，不是各 arm 独立峰值。[record_command.py:40–44](../../ntru_e2e/record_command.py#L40) 的子进程峰值使用 `RUSAGE_CHILDREN.ru_maxrss`，同一父进程后续命令可能继承此前最大值，不能直接解释为该 trial 独立峰值。
- [slicer_adapter.py:303–334](../../ntru_e2e/slicer_adapter.py#L303) 的 lifting timer 包含事件 callback；phase2 callback 内部可能公开验证和 checkpoint（[solve_phase2.py:256–268](../../ntru_e2e/solve_phase2.py#L256)），而 worker clock 则扣除 checkpoint I/O。两类 timer 不能无说明直接相加或混比。

## 5. 预算与进程树：已有机制及静态缺口

- [run_phase2.py:280–317](../../ntru_e2e/run_phase2.py#L280) 根据全局／实例／trial 三种冻结预算取最小剩余额，预留清理和测量开销，按实际命令 wall 计费，记录预算是否满足；[257–273](../../ntru_e2e/run_phase2.py#L257) 对已启动记录恢复时不重跑，缺失测量时按预留预算计费。
- [record_command.py:28–39](../../ntru_e2e/record_command.py#L28) 以及旧 [attack.py:230–241](../../ntru_e2e/attack.py#L230) 用 `start_new_session=True`，超时向进程组 TERM，等待 2 秒后视情况向进程组 KILL。这优于只停止外层等待。
- **尚不能声称无条件保证整棵进程树终止**：KILL 仅在直属进程 `wait(timeout=2)` 仍超时时触发。如果直属父进程收到 TERM 已退出、其子进程忽略 TERM，则不会再发 group KILL；代码没有清理后验证整个组消失。此为静态代码推断，本阶段没有运行相关测试。
- `solve_phase2.py` worker 自身没有 wall 上限，其限时依赖 runner；不得直接绕过 runner 启动后宣称有同样预算保证。

## 6. 阶段 2 的具体入口

仅列待办，本阶段没有实施或启动后续实验。

1. 保留 `Phase2Decoder` 一次 BKZ、Babai 先于 Slicer 建库、基不变检查、精确公开验证，以及原始输入列表和 ID 证据。
2. 增加可冻结的尾部重量上限、候选列表及顺序；明确记录候选原始、规则过滤、去重、提交、完成数量，以及碰撞重试和零尾部覆盖损失。
3. 将同基 D/E、C/F 的阶段试验与独立 A/B/C/D/E/F 端到端计时分开；提供 decoder arm 选择、公开停止及可核对的保存／加载约化基入口。对 B 与 D/E 建立共同或显式分别冻结的约化后端约定，记录精度、LLL 方法/参数、策略哈希和实际 tours；历史 MPFR192/严格两轮结果不能与当前 long-double/MAX_LOOPS 结果直接归因比较。
4. 使用独立非零 primal 分支处理 κ=0；把 BKZ/基行、直接尾部、Babai、Slicer、可用时的枚举来源分列，避免将额外搜索收益归给 Slicer。
5. 统一公开成功与参考等价分类、删失／失败／未覆盖规则，拆细必要阶段计时，补独立 trial/方法内存口径和实际 tours 证据。
6. 加强限时清场并验证子进程存活边界，确保预算确实终止进程树。完成这些检查后，才具备进入 `k=40,κ=4,80` 非零尾部小批验收的代码与统计条件。
