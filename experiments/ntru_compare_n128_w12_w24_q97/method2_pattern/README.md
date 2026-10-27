# 第二类方法：固定预算的 pattern primal 实验

当前正式批次为 `runs/fresh_473s_per_pattern/`。用户要求删除旧 pattern 结果并重做；原 `runs/paired_patterns_473s/` 和旧 `preflight/` 已删除。第一阶段基线、公钥、私钥文件保留。当前十组均从 pattern 编号 0 开始，旧尝试和旧耗时不计入当前结果。

复用 `../public/instance_01.json`：n=128、q=97、环 x^128+1，公开三元重量 (12,24)。原密钥生成规则为固定 seed=1，均匀固定重量支持及独立随机正负号；仅因 f 不可逆而重采样，上限 128 次。此次不重新生成密钥。

冻结参数及输入哈希见正式批次的 `policy.json`、`provenance/plan.frozen.json` 和 `provenance/frozen_manifest.json`。κ=0：P 为 k 个猜测 f 系数为零的位置，T 为空，C 为其余位置。尾部只有一个空假设，无 CVP 目标，Slicer 调用次数为 0。方法仅改变原始 primal 格的 pattern 零约束，维数为 256−k。

| k | 总预算（秒） |
| ---: | ---: |
| 12 | 5677.32 |
| 24 | 11354.64 |
| 32 | 15139.52 |
| 38 | 17978.18 |
| 40 | 18924.40 |
| 42 | 19870.62 |
| 44 | 20816.84 |
| 48 | 22709.28 |
| 52 | 24601.72 |
| 56 | 26494.16 |

每个 pattern 上限 473.11 秒。进程 timeout 从该上限及组剩余预算扣除准备工作与 2.1 秒清理预留；尾次尝试按实际余额截断。余额不足清理预留时记录预算阻塞，不再启动。记录实际清理超支，不提高预算。在线用时累计进程启动、所有失败与超时、LLL/BKZ、扫描、检查点、清理、公开 pattern 与配置准备、验证及启动开销；测试、设置、暂停、独立 evaluate 和最后的 provenance 计费提交本身不计入。

公开 pattern 顺序固定为 index=0,1,…，以 `SHA256('pattern-primal-v1:10001:'+str(index))` 的整数值初始化 Python Random，打乱 0…127，取前 k 个位置并排序。各 k 使用同一排列的前缀；重复集合跳过并记录。选择与阈值不读取参考秘密。

约化与 `../baseline_tour2/` 一致：单线程，solver seed=10001，MPFR 192 位，LLL proved、delta=0.99、eta=0.51。BKZ 从 30 开始、步长 1；每个块未成功时严格完成两轮，clean 不跳过第二轮，禁用 auto-abort；上限 min(256,256−k)。冻结 fplll 默认 BKZ2 strategy，超过其覆盖的 beta100 后采用预先规定的无剪枝 SVP。各组、各 pattern 串行执行。

每次从原始格基开始。LLL 和每个完整 BKZ tour 后扫描全部基行，检测公开重量、三元系数、f 可逆性及公钥方程；在线遇到公开合法候选后停止。十组在线均结束后，`summarize_budgeted` 才打开参考秘密，独立核验同一 ±x^j 等价类、共同带符号负循环旋转及精确格来源。只移动 f 数组而不相应移动 g 不算成功。公开合法但不等价的候选会被报告为失败，不把等价信息反馈给在线选择。

历史严格两轮基线完整在线用时为 473.106938175 秒，首次成功为 BKZ-35 第 1 轮。其预先冻结预算为 null（不设时间上限），实测耗时不是原预算。比较时披露这一差异。

运行由 `budgeted_search.py` 负责；`run_manifest.json` 是可读状态，`ledger.jsonl` 是带哈希链的记录。已启动的 pattern 在恢复时不重试；失去完整 capture 时保守扣除该次预留。冻结源文件或输入改变时拒绝继续。断电恰好发生在 journal 写入和状态视图更新之间时，校验会拒绝继续，须核对 journal 后恢复视图。

```bash
python3 experiments/ntru_e2e/run_in_env.py -m experiments.ntru_compare_n128_w12_w24_q97.method2_pattern.budgeted_search --run-dir experiments/ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern --resume
python3 experiments/ntru_e2e/run_in_env.py -m experiments.ntru_compare_n128_w12_w24_q97.method2_pattern.summarize_budgeted --run-dir experiments/ntru_compare_n128_w12_w24_q97/method2_pattern/runs/fresh_473s_per_pattern
```

此次启动已连接在线结束后的独立 evaluate。最终产物为 `REPORT.md`、`summary.json`、包含基线行的 `comparison.csv`、`attempts.csv`、`stages.csv`、`protocols.json` 与成功候选 `audit/`。峰值内存优先使用每个 worker 的 RUSAGE_SELF；capture 的历史子进程高水位单独注明。超时是删失观测，不能据此认定 pattern 错误。
