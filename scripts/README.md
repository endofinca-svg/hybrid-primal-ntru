# Reproducible Experiment Scripts

后续在此目录保存可复现实验命令，并按参数方案分类，而不是按 shape model 单独建立汇总脚本。

计划中的脚本示例：

- DAWN：`run_dawn_512.sh`、`run_dawn_1024.sh`
- BAT：`run_bat.sh`
- NEV：`run_nev.sh`

每个方案脚本可在内部统一包含该方案需要运行的 `gsa` 和 `zgsa` 命令。
