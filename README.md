# Hybrid Primal Multi-Decoding Estimator for Sparse-Secret NTRU

本项目用于估计 sparse-secret NTRU 参数在 hybrid primal multi-decoding attack 下的安全复杂度。

主入口：

```bash
sage -python -u run_estimator.sage ...
```

支持的 shape models：

- `gsa`
- `zgsa`

支持的 cost models：

- `MATZOV`
- `BDGL16`
- `CoreSVP`

支持的 secret/error distributions：

- `ternary`
- `binomial`
- `gaussian`

当前核心优化参数：

- `k_pat`：pattern length
- `kappa`：tail guessing dimension
- `beta`：BKZ block size

目录说明：

- `results/final/`：论文或报告中最终采用的整理结果
- `results/raw_logs/`：完整原始运行日志
- `results/tables/`：从日志整理出的表格数据
- `archive/old_logs/`：历史测试日志
- `archive/old_scripts/`：历史脚本或旧实验命令
- `archive/deprecated_results/`：不再作为最终结论使用但仍保留的旧结果
- `scripts/`：可复现实验的运行脚本
- `docs/`：参数、模型和整理说明

## Code organization

- `run_estimator.sage` is the unified entry point.
- `hybrid_estimator/` contains the modular estimator implementation.
- `scripts/experiments/` contains reproducible experiment commands.
- `scripts/plots/` contains plotting scripts.
- `results/` stores selected logs and table data.
- `archive/` stores legacy estimators, old scheme-specific scripts, generated Sage Python files, and old figures.
