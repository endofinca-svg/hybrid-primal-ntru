# Unified Experiment Commands

后续 DAWN、BAT 和 NEV 的安全估计统一通过根目录的 `run_estimator.sage` 运行，不再使用 `bat_cost.sage`、`nev_cost.sage`、`dawn_cost.py` 等旧方案专用脚本。

命令模板：

```bash
sage -python -u run_estimator.sage \
  -n <n> -q <q> \
  -e <error_distribution> --param_e <error_param> \
  -s <secret_distribution> --param_s <secret_param> \
  --use_pattern \
  --shape_model <gsa|zgsa> \
  --cost_model <MATZOV|BDGL16|CoreSVP> \
  --kappa_max <value> \
  --kpat_max <value> \
  --verbose \
  2>&1 | tee results/raw_logs/<output_name>.txt
```

- DAWN 使用 `ternary` 分布。
- BAT 使用 `gaussian` 分布。
- NEV 使用 `binomial` 分布。
- `shape_model` 支持 `gsa` 和 `zgsa`。
- `cost_model` 支持 `MATZOV`、`BDGL16` 和 `CoreSVP`。

各方案脚本同时容纳 GSA 和 ZGSA 命令，不单独维护 ZGSA 汇总脚本。
