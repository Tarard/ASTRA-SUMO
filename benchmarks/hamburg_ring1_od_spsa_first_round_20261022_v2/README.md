# Hamburg 普通 SPSA 首轮核验 V2

2026-10-22。参数保持用户确认的一轮设置：$\alpha=0.602$、$\gamma=0.101$、$A=0$、$c=2$ 辆。
初始步长仍由第一对原生损失决定，使首步变化约为 1 辆。没有追加多轮试参。
[设置](settings.json)与 [V1 方法说明](../hamburg_ring1_od_spsa_standard_20261022_v1/README.md)一致。

## V1 汇总错误与修正

初值 SUMO 完整运行结束，130 项 E1 记录全部存在。
原汇总把预期表的 `3600.0` 与比较表的 `3600` 当成不同键，未能写出指标。
这不是仿真缺失。原错误日志和失败启动记录保留。

现在按数值时间连接元数据。真实汉堡 CSV 的格式差异已加入回归测试。
初值的道路、需求、信号、车道行为和 SUMO 运行均未重跑或改写。
初值复用仍核对全部输入散列、生成向量、二进制和原生实现。
另比较需求生成函数及原生运行调用的语法树。汇总修正后从原始 E1 重新计算指标。
未删除字段、时段或安全指标。没有以缺失代替零计数。

## 已完成的初值评价

| 指标 | 诊断值 |
|---|---:|
| 字段 MAE / 最大绝对误差 | 14.923077 / 60 |
| 字段完全一致项 | 24 / 130 |
| 字段误差平方和 | 61146 |
| 派生站点 MAE / 最大绝对误差 | 47.45 / 129 |
| 站点完全一致项 | 0 / 40 |
| 碰撞 / 传送 | 3 / 3 |
| 等待进入 / 在途车辆 | 976 / 424 |

初值无效，不能作为已完成标定。
全部 [130 项字段残差](initial-analysis/all-130-field-comparison.csv)与 [40 项派生站点残差](initial-analysis/all-40-station-comparison.csv)保留。
另一份独立计算及输入散列位于 `initial-analysis/`。两份计算一致。
原始初值：`outputs/hamburg-od-initial-native-20261022-v1/`。
原生执行与全部输入副本保持原状。

## 继续首轮

V2 直接等待并检查原生运行是否完整结束，再重新计算初值损失。
它不依赖旧汇总是否成功，也不重新仿真已完成的初值。
随后运行正向扰动、反向扰动、一次标准更新和更新后完整复测。
全部 130 个目标及 40 个派生站点值不变，健康且零残差才可接受。

```powershell
& benchmarks/hamburg_ring1_od_spsa_first_round_20261022_v2/run-study.ps1 `
  -OutputDir outputs/hamburg-od-spsa-standard-new
```

本次原始研究目录：`outputs/hamburg-od-spsa-standard-20261022-v2/`。
脚本为 [`run-study.ps1`](run-study.ps1)，入口为 [`run_hamburg_od_spsa.py`](../../plugins/astra-sumo/scripts/run_hamburg_od_spsa.py)。
指标分析为 [`hamburg_od_spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_spsa.py)。
场景为 `outputs/hamburg-od-baseline-20261022-v1/`，来源见 [OD 初值资料](../hamburg_ring1_spsa_od_definition_20261022_v1/README.md)。
回归检查见 [recovery-tests.log](recovery-tests.log)。

当前只验证首轮方法。路线、车道、控制及初始状态仍按声明先验处理。
单轮结果不证明收敛、真实 OD 唯一性或其他随机种子的稳健性。
