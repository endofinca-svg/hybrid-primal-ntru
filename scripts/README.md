# Reproducible Experiment Scripts

后续在此目录保存可复现实验命令，并按参数方案分类，而不是按 shape model 单独建立汇总脚本。

当前实验脚本：

- DAWN：`experiments/run_dawn_examples.sh`
- BAT：`experiments/run_bat_examples.sh`
- NEV：`experiments/run_nev_examples.sh`

每个方案脚本可在内部统一包含该方案需要运行的 `gsa` 和 `zgsa` 命令。

历史画图脚本保存在 `plots/`。部分脚本使用相对于项目根目录的输入路径，运行前请检查其输入文件位置。
