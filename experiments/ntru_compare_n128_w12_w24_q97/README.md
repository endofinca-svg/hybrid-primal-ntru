本轮参数固定为 n=128、q=97、固定三元重量 (12,24)，不使用 pattern 或 tail 猜测，原始 primal 格维数为 256。

q=97 在密钥生成前按算术启发式选定。格行列式为 q^128，秘密平方范数为 36；高斯启发式长度约为 38.63175，比秘密范数 6 大约 6.44 倍。相比 q=257，高斯启发式长度降低约 38.56%，降低秘密的突出程度。该计算不保证 LLL 失败、BKZ 成功块大小或运行时。所有生成的 f 都显式验证可逆，仅因不可逆而拒绝。

本轮当前有效基线协议见 plan_tour2.json：LLL 后从 BKZ-30 开始，每块严格运行两轮，未成功则立即将块大小加一；找到公开验证通过的秘密立即停止。每轮均检查全部基行。q、公钥、秘密、求解 seed、精度和其他约减条件保持不变。

baseline/ 与 run_manifest.json 保留用户修改轮数前的八轮尝试；当前两轮协议的结果位于 baseline_tour2/，运行清单为 run_manifest_tour2.json，最终报告为 BASELINE_TOUR2_REPORT.md。旧八轮运行不参与两轮协议的基线用时和块大小结论。两轮协议从同一原始格重新执行 LLL，避免混入旧第三至第八轮的约减效果。

此前 n=100 的生成目录已按用户要求删除，操作记录见 setup/deletion_receipt.json。参数选择和协议调整分别记录于 setup/q_selection.json、setup/protocol_change_tour2.json。
