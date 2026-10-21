# 汉堡原生时段反馈

本包按 [文献与设计记录](../../docs/research/ring1-count-fit-basis-2026-10-21-v2.md) 保存一次原生运行的需求到计数对应。
四篇论文的 DOI、阅读版本及采用范围见 [文献记录](sources.json)。
它没有重新运行仿真，也没有改变已保留的失败结果。原生运行仍无效，零误差标定仍未完成。

| 本次原生记账 | 车辆数 |
|---|---:|
| 观测窗内完整通过 | 4223 |
| 仅在观测窗外完整通过 | 679 |
| 尚未进入 | 1593 |
| 已进入但未完成字段通过 | 192 |
| 请求车辆合计 | 6687 |

全部 130 个真实字段时段均保留，当前请求形成 162 个“计划×出发时段”组。
分母使用全部请求车辆，包含未进入车辆。入口等待与路网内到检测断面的时间分别记录。
观测窗开始前没有出发请求，因此此前一小时的空路网运行没有形成带需求的初始化。
这只是当前模型的初始条件，不是现场零流量观测。

`response.npz` 保存整数分子 `numerators`、请求分母 `denominators`、本次比例 `fractions` 和原生计数 `measured_counts`。
整数分子按列相加，逐行精确重构本次原生 `nVehContrib`。
`rows.json` 保留原字段与时段。`columns.json` 保留计划、入口、出发时段、未服务车辆及实际延误。

这些比例描述同一次回放，不是导数，也不是改变需求后的有效预测。
未采样的路线和出发时段保持未知。窗外缺少真实计数，不设为零。
40 个站点仍在 [原生运行报告](../hamburg_ring1_lane_section_hold_full_20261021_v1/README.md) 中按字段之和报告，不额外作为独立数据。

执行：[analyze_hamburg_temporal_feedback.py](../../plugins/astra-sumo/scripts/analyze_hamburg_temporal_feedback.py)。
计算：[native_temporal_feedback.py](../../plugins/astra-sumo/src/astra_sumo/core/native_temporal_feedback.py)。
参数和源文件校验值见 [report.json](report.json)，源需求、控制与原始记录见 [原生运行](../hamburg_ring1_lane_section_hold_full_20261021_v1/)。

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/analyze_hamburg_temporal_feedback.py benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1 outputs/new-temporal-feedback
```

[测试记录](tests.xml) 覆盖跨时段、窗外通过、未进入车辆、缺失记录、原生时间边界及完整汉堡数据的记账。
