# 小参数 NTRU 秘密恢复实验

本模块把公开公钥转换为共享格上的一批目标，调用 KKNM25 的真实 Randomized Slicer，再重构并公开验证候选。2026-10-21 已完成第一阶段：seed 2、3 经真实 Slicer 重构及独立验证通过；seed 1 保留原版后端的超时失败记录，其预算耗尽后没有重试。详见 [REPORT.md](REPORT.md)、[结构化汇总](runs/phase1/summary.json) 与 [数学约定](NOTES.md)。三个记录的后端补丁版本不同，不构成同版本成功率测量。直接 Babai 诊断也成功，因此尚未证明 Slicer 的必要性。

## 文件各自负责什么

| 文件或目录 | 作用 |
| --- | --- |
| `bootstrap.py` | 从固定上游版本建立本地源码副本和实验 venv，重新编译 G6K，并记录环境、命令与源码差异。 |
| `run_in_env.py` | 根据 `.deps/runtime.json` 启动实验 Python，优先导入本地编译的 G6K。后续命令统一经此入口运行。 |
| `record_command.py` | 给任意命令保存准确参数、标准输出、标准错误、退出状态、耗时和资源记录。 |
| `generate.py` | 按固定重量三元分布生成实例，把公钥与私有真值分别写入不同目录。 |
| `algebra.py` | 精确整数环运算、行基、目标、坐标映射、候选重构与公开约束检查；不包含解码器。 |
| `slicer_adapter.py` | 一次格预处理、多目标 Slicer 调用、目标编号映射、坐标转换和整数格向量输出；Babai 仅作为独立诊断。 |
| `attack.py` | 正式秘密恢复入口。读取公开实例和公开配置，枚举非零 tail，按预算运行，保存候选及逐阶段证据。 |
| `solve.py` | `attack.py` 的公开命令别名，提供 `generate` / `solve` / `evaluate` 三个独立命令。 |
| `evaluate.py` | 正式进程结束后的独立评估入口。读取私有真值，检查候选、旋转等价关系、覆盖和输出来源。 |
| `run_phase1.py` | 在全新目录执行冻结的三个 seeds，限制单实例和累计在线预算，再统一独立评估。 |
| `check_slicer_uid.py` | 用真实编译扩展检查小目标入库、反号去重与零 UID 排除。 |
| `config_phase1.json` | 公开的初始/备用配置、运行随机种子、线程数和时间预算；不包含密钥生成种子。 |
| `tests/` | 代数接线、目标映射、公开/私有分离与独立评估等接口测试。不能替代真实 Slicer 运行。 |
| `provenance/` | 构建、环境、上游 smoke、源码补丁和命令证据；具体内容以实际生成文件为准。 |
| `.deps/` | 本地上游源码、编译产物、实验 venv 和启动配置。 |
| `runs/` | 各次生成的实例、正式结果、事件日志与离线评估。 |

三个数据入口分开运行：`generate.py` 创建公钥和真值；`attack.py` 只接收公开实例；`evaluate.py` 才接收真值。正式入口的 `--seed` 用于 pattern 和解码器随机性，与生成私有秘密的种子不同。

## 准备本地环境

以下命令都从仓库根目录执行。默认使用本机已有的 `/home/inca/miniforge3/envs/g6k/bin/python` 及 `/home/inca/g6k_hybrid`；在其他机器上应替换为相应路径。

```bash
python3 experiments/ntru_e2e/bootstrap.py \
  --base-python /home/inca/miniforge3/envs/g6k/bin/python \
  --source /home/inca/g6k_hybrid \
  --jobs 2
```

基础环境需要已有 fpylll、Cython、NumPy、cysignals、pytest 及相应编译工具。脚本把上游源码复制到 `.deps/g6k_hybrid`，固定到 `bd72687b2ebbaff16fb692259f83b327bbcd9586` 后重新构建；不在原源码目录编译，也不向基础环境安装包。

实验 venv 使用 `--system-site-packages`，以读取方式共享基础环境中的依赖。这隔离了实验自己的环境目录和 G6K 编译产物，但不是完全独立的依赖快照：基础环境依赖发生变化时，需要重新核对 `provenance/` 中的版本与模块路径。

上游目标 UID 的 `int16_t(128*y[i])` 会把本实验的小投影量化成零。记录的最小补丁改为 `int64_t(llround(2^20*y[i]))`，保留正负号对称、零 UID 保留规则及原距离计算。`bootstrap.py` 自动应用 [补丁](provenance/upstream_source.patch)，校验已有差异，强制重新编译，并将每次构建日志写入新的 `provenance/bootstrap_*/` 目录。原许可证和来源保留；本次实际重新编译、UID 回归和上游 smoke 证据见 [completion/](provenance/completion/)。

## 一条命令运行完整的新验收

完成环境构建后，在仓库根目录执行：

```bash
python3 -m experiments.ntru_e2e.run_phase1 \
  --output-dir experiments/ntru_e2e/runs/reproduction
```

目录必须尚不存在。该命令使用固定 keygen seeds 1、2、3 和独立求解 seeds 10001、10002、10003，逐实例运行，再执行独立评估并生成 `summary.json`；包括失败、超时和预算未运行状态。每实例命令最多 300 秒（worker 预留 5 秒监督开销），累计在线最多 1800 秒。下面的分步命令用于单独复现三个边界。

## 生成、正式运行和独立评估

先选择一个新的运行目录；下面使用 `runs/reproduction`。生成入口默认创建三个固定小参数实例及各自真值：

```bash
python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.generate \
  --output-dir experiments/ntru_e2e/runs/reproduction
```

输出分别为 `public/instance_01.json` 等公开文件，以及 `private/instance_01.truth.json` 等离线真值。不要把私有真值、密钥生成种子或其推导信息加入正式恢复配置。

仅向正式入口提供第一个公开文件和实验随机种子：

```bash
python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.solve \
  --public experiments/ntru_e2e/runs/reproduction/public/instance_01.json \
  --seed 10001 \
  --output experiments/ntru_e2e/runs/reproduction/instance_01.result.json
```

默认使用 `config_phase1.json`。每个实例的总时限包含其诊断和所有重启；正式入口另有监督进程处理超时。它会拒绝覆盖已有结果，因此复现另一轮时应选新目录。一次正式命令只运行一个实例；第二、第三个实例分别使用对应公钥和公开运行种子 `10002`、`10003`。多实例累计预算由外层运行组织负责，不因单例入口存在就自动受控。

正式进程结束后，再把公钥、私有真值和正式输出交给独立评估器：

```bash
python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.evaluate \
  --public experiments/ntru_e2e/runs/reproduction/public/instance_01.json \
  --truth experiments/ntru_e2e/runs/reproduction/private/instance_01.truth.json \
  --result experiments/ntru_e2e/runs/reproduction/instance_01.result.json \
  --output experiments/ntru_e2e/runs/reproduction/instance_01.evaluation.json
```

正式输出的 `.events.jsonl` 记录构造、预处理、实际 Slicer 调用及候选处理。评估时应分别查看候选公开有效性和 `end_to_end_passed`，并结合覆盖、来源、失败类别及早期恢复诊断解释结果。单独出现一个有效短秘密，或命令正常退出，都不等于真实 Slicer 的完整秘密恢复已通过。

## 保存命令证据与运行测试

需要同时保存标准输出、标准错误和资源信息时，可给上述任意命令加记录层。例如记录第一个实例的正式命令：

```bash
python3 experiments/ntru_e2e/record_command.py \
  --prefix experiments/ntru_e2e/runs/reproduction/instance_01_online \
  -- \
  python3 experiments/ntru_e2e/run_in_env.py \
  -m experiments.ntru_e2e.solve \
  --public experiments/ntru_e2e/runs/reproduction/public/instance_01.json \
  --seed 10001 \
  --output experiments/ntru_e2e/runs/reproduction/instance_01.result.json
```

这条命令替代前面的直接正式命令执行；已有结果时应换新目录。记录层按前缀生成 `.command.json`、`.stdout.txt` 和 `.stderr.txt`，也会拒绝覆盖已有命令元数据。

```bash
python3 experiments/ntru_e2e/run_in_env.py \
  -m pytest -q experiments/ntru_e2e/tests
```

本次最终测试为 43 passed、22 subtests passed；证据见 [final_tests.stdout.txt](provenance/completion/final_tests.stdout.txt)。UID 实扩展回归可单独执行：

```bash
python3 experiments/ntru_e2e/run_in_env.py \
  experiments/ntru_e2e/check_slicer_uid.py
```

测试、上游 smoke 与正式实例恢复分别保存证据。小参数结果的结论范围仍以 [NOTES.md](NOTES.md) 为准。
