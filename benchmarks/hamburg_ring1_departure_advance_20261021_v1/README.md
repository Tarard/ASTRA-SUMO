# 汉堡固定人口提前请求检验

**这份提前 900 秒的固定人口候选已否决。** 本次只运行到主观测窗开始，不宣称得到新的主窗标定结果。
基准与候选都完整保留 6687 辆车和 130 个真实字段目标，路线、传感器、控制和车道保持方式保持原样。
候选仅将入口 `44532531` 的 2232 辆车提前 900 秒，目标字段与目标时段保持不变。

原生 SUMO 1.27.1 在当地 06:30–07:30 运行。227 辆提前请求的车全部进入，107 辆到达，120 辆仍在路网中。
碰撞、传送均为 0。206 辆车在 07:30 前完成字段通过：Z.11 为 12，Z.12 为 64，Z.13 为 130。
周期 E1 的 `nVehContrib` 与逐车完整通过记录一致。

## 条件与结论

当前人口固定为 $N=6687$。主窗 130 个字段目标之和也是 $6687$。
当前需求模型假设每车至多贡献一次完整字段计数。
所有道路路线都仅经过一条设有字段的道路，并且不重复道路。
每条车道只有一个检测器，所有检测器距道路末端均短于车辆长度，驾驶采用正常向前行驶与原生瞬时换道。
在没有碰撞、传送或车辆位置重置的条件下，完整通过后车辆已离开这条检测道路，后续路线不再返回。

因此，已有 $206$ 辆在窗前完成通过后，本候选窗内总计数至多为

$$
N-206=6481<6687.
$$

这只是当前固定人口、固定路线和一次字段通过模型下的守恒约束。
它足以否决本候选，不能据此宣称其他需求人口、路线或初始化方式都无法精确标定。
本次没有给窗外真实流量设置零值，也没有用缺失时段代替零计数。
主观测窗没有重新运行，候选主窗的逐行残差保持未测。
全部已知主窗残差仍见 [最近完整运行](../hamburg_ring1_lane_section_hold_full_20261021_v1/README.md) 的 130 个字段及 40 个派生站点记录。

## 证据与复现

- [独立输入与原生计数检查](native/independent-checks.json)。全部车辆 ID、道路路线、途经点和其他属性保持原样。
- [原生守恒检查](native/warmup-check/warmup-check.json)。
- [原始 E1、逐车、碰撞及运行记录](native/warmup-check/)。较大的文件使用无损 gzip。
- [基准完整输入](baseline/full-inputs/) 与 [提前候选完整输入](advance900/full-inputs/)。目标文件保持相同校验值。
- [执行](../../plugins/astra-sumo/scripts/probe_hamburg_departure_shift.py) 与 [计算](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_departure_probe.py)。
- [测试](tests.xml) 与 [文件校验值](manifest.json)。

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/probe_hamburg_departure_shift.py prepare benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1 outputs/new-departure-inputs
.venv/Scripts/python.exe plugins/astra-sumo/scripts/probe_hamburg_departure_shift.py run outputs/new-departure-inputs outputs/new-departure-check
```

该检验只测量观测窗前的通过，不把它们与虚构的零流量比较。
新取得的 [官方初始化计数](../hamburg_ring1_initialization_counts_20261021_v1/README.md) 另行保存，不更改原 130 行目标。
