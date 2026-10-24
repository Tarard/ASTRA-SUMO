# 汉堡通行路线覆盖：人工检验通过，初始化运行无效

本次修正过严的“每车只经过一条观测道路”限制，检验路线池已有的 119→200→535 通行路线。
两组各 40 辆人工车辆均安全完成，原生 E1 字段完全符合人工目标。
扩大至完整初始化小时后出现 **2 次碰撞、2 次传送**。本候选不能作为有效标定结果。
全部真实目标、原生残差和失败记录均保留，没有删除经过冲突地点的合法路线。

## 单项修改及依据

父输入为 [119 目的地先验](../hamburg_ring1_primary_exit_prior_20261021_v1/README.md)。
原路线池已有 `100→106`、`176→182`、`177→183` 三组替代。
它们保留边界入口、119 的 Z.12/Z.13 字段和检测前自由流偏移。
替代路线通向 `142944477`，经过 535 的观测道路 `186821034#0`。
该道路的 Z.4 检测器位于左转车道 5。通行路线从直行车道 3/4 驶向 `186821036#1`。
左转检测车道没有通往该路线下一道路的连接。

按 [SUMO 1.27.1 E1 源码](https://raw.githubusercontent.com/eclipse-sumo/sumo/v1_27_1/src/microsim/output/MSInductLoop.cpp)，`nVehContrib` 排除提前离开检测车道的车辆。
结合近道路末端的位置、固定前进路线和标准瞬时换道，可用检测车道的出口连接限定完整通过。
该推断还要求无重复道路、碰撞、传送或位置重设，详见 [条件和源码位置](analysis/source-semantics.json)。
它不是驾驶行为或安全证明。本次较大运行违反前提，不能对该运行使用这个上界。

代码保留 `one_observed_road_per_route` 作为诊断信息。
条件检查改为每条路线至多经过一条“可从检测车道驶向下一道路”的观测道路。
检测器仍须位于道路末端一个最短车辆长度以内，路线仍不允许重复道路。
本次路线还检查了合法连接、官方转向支持、重复物理路口和掉头。
没有删除网络中的合法掉头，也没有用掉头生成需求。

设计沿用 [论文依据 V2](../../docs/research/ring1-count-fit-basis-2026-10-21-v2.md)：保留合理路线先验，用原生仿真评价候选。
本次没有实现 W-SPSA、Cadyts 或完整整数校正循环。
论文没有提供本次出口比例，也没有保证本案例能达到精确零误差。

## 候选与固定输入

保留父方案的主干/住宅出口总体份额。
仅将既有主干出口车辆按目标时段交错分配，一半继续使用旧出口，一半使用通行路线，奇数余车留在旧出口。

| 人口 | 旧主干出口车辆 | 改至通行出口 | 仍用旧主干出口 | 保留住宅出口 |
|---|---:|---:|---:|---:|
| 初始化 | 851 | 425 | 426 | 95 |
| 主窗 | 2257 | 1126 | 1131 | 249 |

旧出口 `142944538` 为 primary，有 2 条允许 passenger 的车道。
通行出口 `142944477` 为 secondary，有 3 条允许 passenger 的车道。
**1:1 是探索份额，不是实测 OD，也不表示两出口容量相等。**
本次没有调整该比例以追逐更低残差。

全部 8640 辆车的身份、类型、请求时刻、移动途经点、目标字段、目标时段与检测前偏移均保留。
网络、换道权限、信号输入、13 个字段及原有 130 行主窗目标逐字节不变。
40 个站点目标仍由字段相加，不作为独立证据。
没有开放自行车道或修改信号、几何、跟驰参数。
OSM 感应控制的实际时序仍可随需求改变，不能把相同输入解释为相同放行时序。

本次使用 [prepared-v2 输入](inputs/)。未运行的本地 prepared-v1 曾把两个出口都写成两车道，正式运行前已改正说明。
两版本的需求、网络和控制相同，正式运行使用 v2。
配对检验目录中的顶层网络副本经 XML 重写，未用于运行。实际运行网络及其校验值由原生输入快照确定。
后续代码直接复制源文件字节。这个保存方式修正不改变已保存的原生结果。

## 配对人工检验

两组使用同一 40 辆车，从仿真 100 秒起每 2 秒出发，结束于 900 秒。
对照全部使用旧主干出口，候选全部使用对应通行路线。
两组均为 40 辆进入、40 辆到达，无未进入、在途、碰撞、传送或丢弃车辆。
全部 13 个字段均参与检验，Z.12 为 27、Z.13 为 13，其余为 0。
每辆均在指定字段完整通过一次，无其他字段或重复字段通过。
周期 E1 与逐车完整通过记录相符，见 [配对结论](paired-probe/probe-check.json)。
这是人工路线与检测覆盖检验，不是主窗计数拟合或真实出口份额验证。

## 完整初始化小时：无效诊断

使用仓库 SUMO/TraCI 1.27.1，步长 0.1 秒，seed 104。
仿真 0 为 2026-10-19 04:30 UTC，即当地 06:30 CEST，结束于 3600 秒、07:30 CEST。
本小时有 1953 辆请求，另有 6687 辆未来主窗车辆保留，不能算成本小时未进入。
保留既有断面车道保持、控制程序及全部在途车辆静止 300 秒即停止的检查。
本次正常到达 3600 秒，无超时。碰撞和传送使结果无效。

| 检查项 | 父方案 | 本次无效候选 |
|---|---:|---:|
| 实际进入 | 1727 | 1795 |
| 到达 | 1510 | 1624 |
| 结束时在途 | 217 | 171 |
| 已请求但未进入 | 226 | 158 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | **2 / 2 / 0** |
| 原生 E1 完整通过 | 1548 | 1665 |
| 可用 / 完全一致的字段时段 | 52 / 33 | 52 / 33 |
| 诊断 MAE | 7.788461538461538 | 6.230769230769231 |
| 诊断最大绝对残差 | 61 | 37 |

全部 52 行见 [残差和父方案比较](analysis/field-change.csv)。9 行绝对误差下降，6 行上升，37 行不变。
原生 `nVehContrib` 逐行核对，1665 次完整通过均在指定字段，未发现其他字段或重复字段通过。
较低残差不能抵消碰撞和传送。传送也会影响后续排队，因此不将上表作为有效交通性能改善。

| 原生时刻 | 路口 | 内部车道 | 类型 | collider / victim 的实际计划编号 |
|---|---|---|---|---|
| 1881.1 s | `14398547` | `:14398547_3_1` | side | 183 / 177 |
| 2401.1 s | `205069138` | `:205069138_2_2` | junction | 106 / 281 |

两处均为普通连接路口，不是 535 的官方重建内部路口。
计划编号来自本次车辆元数据，不能从保留旧编号的车辆名称推断。
第一处日志的 `laneChange` 是报告阶段，不能据此认定实际发生了换道。
日志确认了位置、参与车辆与计划，尚未确定碰撞原因，见 [事件上下文](analysis/collision-context.json)。
没有碰撞前轨迹复现，不据此认定某个重合曲线、连接间隙或控制状态是原因。

## 结论和下一项

**已验证**：原道路数量限制过严。既有合法通行路线可在人工检验中只贡献指定字段。
**拒绝**：本次较大需求候选发生碰撞和传送，不能接受，也不能用其状态继续正式标定。
**未完成**：冲突原因与修复、合理需求分配、完整整数校正及主窗 130/40 行有效零误差。
下一项只检查这两处原生冲突，不继续尝试出口比例，也不删除合法路线以隐藏冲突。

本轮未重跑主窗。最近主窗仍为 [K31/K2 运行](../hamburg_ring1_z15_k2_full_20261021_v1/README.md)：字段 MAE 19.885、最大误差 86，运行无效。
网络未变，固定范围航拍图与实际 NetEdit 补充图沿用该记录。
Z.15 控制备选、200 施工阶段差异、104/118 固定程序近似、车道意图和几何候选的现场验证限制继续保留。

## 复现与证据

在仓库根目录设置项目环境，使用新输出目录：

```powershell
$env:PYTHONPATH = "$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME = "$PWD/.venv/Lib/site-packages/sumo"
$env:PATH = "$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe plugins/astra-sumo/scripts/probe_hamburg_through_routes.py probe benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1 outputs/new-through-probe
.venv/Scripts/python.exe plugins/astra-sumo/scripts/probe_hamburg_through_routes.py prepare benchmarks/hamburg_ring1_primary_exit_prior_20261021_v1 outputs/new-through-inputs --probe-dir outputs/new-through-probe
.venv/Scripts/python.exe plugins/astra-sumo/scripts/probe_hamburg_through_routes.py run outputs/new-through-inputs outputs/new-through-hour
```

- 执行：[CLI](../../plugins/astra-sumo/scripts/probe_hamburg_through_routes.py)、[路线候选与人工检验](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_through_routes.py)、[初始化执行](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_initialization.py)。
- 分析：[条件检查](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_departure_probe.py)、[核对时脚本](analysis-at-verification.py)、[独立核对](analysis/independent-checks.json)。核对脚本保存当时的本地输入路径，冻结数据位于同名子目录。
- 输入：[场景](inputs/scenario.json)、[份额及源校验值](inputs/through-prior.json)、[完整输入](inputs/)。
- 原始结果：[人工运行](paired-probe/)、[完整初始化](native/)、[原生碰撞](native/collisions.xml)、[本小时结论](native/initialization-check.json)、[父方案比较](analysis/comparison.json)。
- 验证：[测试](tests.xml)、[实现校验值](implementation-sha256.json)、[文件清单](manifest.json)。

大文件以无损 gzip 保存。源许可沿用父证据、官方资料与 OSM。
3599.9 秒状态只含原生状态，没有序列化断面保持控制器，未验证重启。
