# 汉堡局部路口保持：短连接停滞解除，完整主窗未验证

根据 [完整主窗及人工复核](../hamburg_ring1_junction_body_full_20261021_v1/README.md)，全局路口车身保持会阻止短连接上的必要换道。
本次只收窄这项驾驶约束：在此前发生碰撞的 `14398547`、`205069138` 保持车尾驶清规则，其余路口恢复原生换道。
检测断面保持仍启用，全部车辆、路线、请求时刻、网络、车道权限、信号输入和真实目标不变。
这是新的局部驾驶候选，不是现场驾驶事实或几何修复。

## 6000 秒针对性结果

使用仓库 SUMO/TraCI 1.27.1、步长 0.1 秒、seed 104，从空网络的初始化开始运行至 6000 秒。
对应 2026-10-19 04:30–06:10 UTC，即当地 06:30–08:10 CEST。
此范围覆盖初始化和主窗前 40 分钟，尚未覆盖完整主窗及末尾时段。

| 检查项 | 结果 |
|---|---:|
| 截至 6000 秒已请求 | 3825 |
| 保留的未来请求 | 4815 |
| 已进入 / 到达 | 3226 / 2910 |
| 已请求但未进入 / 在途 | 599 / 316 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 |
| 北入口截至 6000 秒进入 | 1241，原全局规则为 1147 |
| 原停滞时刻 5299.7 秒后，北入口继续进入 | 131 |
| 北入口最后实际进入 | 5995.8 秒 |

`loaded=3853` 包含 28 辆提前加载的未来车辆，不能将它们算作已请求未进入。
原阻塞车辆 `readme_f0_b0_p169_v6` 已于 4637.1 秒到达。原全局运行中，该车在 14400 秒仍未到达。
原两次碰撞涉及的四辆车也均已到达终点，见 [逐车结果](analysis/prefix-check.json)。
这些结果支持继续检验收窄后的假设，不代表总体残差已改善，也不代表全网络或后续时段没有碰撞。

## 全部目标保留，未来时段未知

原 130 个字段时段保留在 [完整表](analysis/all-130-main-rows.csv)。
只有两个完整的 15 分钟主窗时段已测量，共 26 行；其余 **104 行保持缺失**。
原 40 个派生站点时段保留在 [站点表](analysis/all-40-main-stations.csv)，8 行有测量，32 行保持缺失，站点不是独立证据。
08:00–08:10 的部分原生区间没有冒充 08:00–08:15 的完整计数。
没有删除后续目标、按比例补齐计数或将未测行填零，因此本项不报告完整主窗 MAE 或零误差结论。

原生执行器的 `healthy=false` 不能在这里解释为全部未来车辆未进入：本试验保留 8640 辆需求，但只运行到 6000 秒。
599 辆已请求未进入及 316 辆在途仍明确保留，不能据此前缀接受标定。
原生字段对比文件对应完整的初始化小时；主窗可用范围以本页的 130 行表为准。

## 声明与下一项

保持节点写入 [场景](inputs/scenario.json) 的 `junction_body_hold_nodes`，原生请求记录相同名单。
未指定节点名单的历史全局案例继续按原范围复现，不会被静默改成此局部候选。
此候选仍有首次内部观测之前一个仿真步的采样限制。
其他已知几何风险、0.1 m 终点、车道意图、占位感应控制及 Z.15/200 的对应限制没有在本项验证。
不能用新增保持范围、删除合法路径或放宽碰撞处理来掩盖后续失败。
下一项须从初始化开始完整回放该候选，再决定需求时刻或入口分配的具体校正。
原生状态未序列化保持控制器，5999.9 秒状态仅作诊断，不作为已验证重启点。

## 复现位置

完整窗口使用同一输入和节点声明，从初始化重新执行，输出到新目录：

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/run_hamburg_readme_pipeline.py run benchmarks/hamburg_ring1_localized_junction_body_20261021_v1/inputs outputs/new-localized-full --hold-observed-lanes --hold-junction-body
```

须先按完整流程报告设置项目环境。完整回放入口会保存末步原生诊断状态，不将其视为可直接重启的保持控制器状态。

- 执行：[核对时脚本](probe-localized-junction-body.py)、[原生执行器](../../plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py)、[逐步驾驶执行](../../plugins/astra-sumo/src/astra_sumo/core/simulation_stall.py)。脚本使用仓库 `.venv` 和原运行输入，保留原输出路径，复现时需改为新目录。
- 输入：[前缀声明](inputs/prefix-proposal.json)、[全部真实目标](inputs/expected-counts.csv)、[模型需求](inputs/demand.rou.xml.gz)。
- 原始结果：[native](native/)、[原生命令](native/native-request.json)、[5999.9 秒诊断状态](native/states/state_5999.900.xml.gz)。
- 分析：[全部 130/40 行与进入情况](analysis/)、[核对脚本](analyze-localized-junction-body-prefix.py)。
- 检查：[测试](tests.xml)、[实现校验值](implementation-sha256.json)、[文件清单](manifest.json)。

网络未变，图像沿用已按当前网络校验值核对的 [完整流程报告](../hamburg_ring1_junction_body_full_20261021_v1/workflow-report.md)。
全部原始记录保留，大文件无损 gzip 保存。有效零误差标定仍未完成。
