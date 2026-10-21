# 汉堡 Z.15 入口替换：原生计数无改善

本次只将 Z.15 的 Brandsende 入口换为已有 Ballindamm 入口路线。
初始化小时的全部 52 行原生计数与 [原入口方案](../hamburg_ring1_initialization_population_20261021_v1/README.md) 相同。
未进入车辆减少 8 辆，在途车辆增加 8 辆，到达数与字段通过数均不变。
本候选不作为计数改善方案采用，没有继续主窗试跑。

## 本次唯一修改

现有有限路线池中，119 的 Z.11–Z.13 仅有 `44532531` 与 `25866659#1` 两种入口。
它们共用后续路段。此结论只针对当前有限路线池，不是所有合法道路路线的穷尽证明。
[路线池入口检查](analysis/finite-profile-entry-review.json) 保留各字段的候选。

Z.15 有不同入口的既有路线，允许提出单项入口替换：

| 内容 | 原方案 | 本次候选 |
|---|---|---|
| 路线配置序号 | 240 | 246 |
| 入口 | `543550497#0` | `551288837#2` |
| 道路 | Brandsende，unclassified | Ballindamm，tertiary |
| 入口长度 | 45.877185 m | 139.16 m |
| 汇合道路 | `186714394` | `186714394` |

替换使用既有道路范围的入口声明和乘用车连接，不新增入口，不改变道路、转向或车道权限。
汇合后的道路路线完全相同。新路线没有重复道路、重复物理路口或掉头。
没有开放自行车道，也没有把车辆直接放到检测器前。

全部 8640 辆模型车辆保留。其中 307 辆初始化车辆和 890 辆主窗车辆更换入口路线。
车辆身份、请求时间、类型、移动途经点、人口类别、目标字段及目标时段均不变。
没有再按自由流偏移改变请求时间。
新入口合计承担 309 辆初始化车辆、896 辆主窗车辆。
每十五分钟最多请求 106 辆，低于原先声明的 150 辆入口先验上限。
道路等级、入口先验和源边界支持均不证明实测 OD 或通行能力。

原 130 行主窗目标和 52 行初始化目标逐字节不变。
网络、信号输入和传感器也逐字节不变，车道保持方式相同。
信号控制仍沿用原声明。104/118 固定 SP5 近似、Z.15/K9 备选、其他 OSM 感应控制及 200 施工阶段差异均未解决。

## 原生比较

只运行当地 2026-10-19 06:30–07:30，即 04:30–05:30 UTC 的初始化小时。
使用仓库 `.venv` 的 SUMO、TraCI 1.27.1，步长 0.1 秒，seed 104。
全部在途车辆静止 300 秒时终止的检查保持启用，运行正常到达 3600 秒。

| 检查项 | 原入口方案 | Ballindamm 候选 |
|---|---:|---:|
| 本小时请求车辆 | 1953 | 1953 |
| 实际进入 | 1533 | 1541 |
| 到达 | 1308 | 1308 |
| 仍在路网 | 225 | 233 |
| 已请求但未进入 | 420 | 412 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | 0 / 0 / 0 |
| 原生 E1 完整通过 | 1431 | 1431 |
| 完全一致的字段时段 | 32 / 52 | 32 / 52 |
| 诊断 MAE | 10.923076923076923 | 10.923076923076923 |
| 诊断最大绝对残差 | 82 | 82 |

52 行原生 `nVehContrib` 逐行完全相同，并非只比较总量。
全部 1431 次完整通过均与指定字段对应一次，没有其他字段通过或重复通过。
这些车辆全部恢复原换道方式，没有车辆在恢复前离开。
仍有一次紧急制动警告，原始记录保留。

Z.15 的四个初始化时段目标为 84、67、64、94，原生结果为 44、52、68、58。
残差为 −40、−15、+4、−36，两种入口方案完全相同。
新入口有 44 辆未进入，原 Brandsende 与 Ballindamm 合计为 52 辆。
另外两个主要积压入口仍是 `44532531` 的 279 辆和 `25866659#1` 的 89 辆，数值均未改变。
全部字段和入口见 [逐行计数变化](analysis/field-change.csv) 与 [逐入口结果](analysis/entry-delays.csv)。

## 判断与限制

**已验证**：本次入口替换没有改善初始化小时的任一字段计数。
更长入口只多容纳了 8 辆在途车辆，不能把少量未进入车辆的减少当作通行能力或标定改善。
这是否定本次有限修改的原生证据，不是所有入口分配均无效的证明。

**候选解释**：汇合后的共同路段仍限制通过。
[3599.9 秒的车道存量](analysis/Z15-stock-lanes.json) 记录了共同路段上的 Z.15 车辆。
单个存量快照不能识别因果瓶颈，也不能确认现场排队或真实信号运行。
下一项工作应先定位共同路段的受限放行原因，再改变需求。
本次不调整信号、几何或驾驶参数，也不继续换入口试碰结果。

**未完成**：原 130 个字段时段及 40 个派生站点时段的有效零误差标定。
本次没有主窗回放，不能更新其计数结果。
原 6687 辆主窗人口只是模型假设，站点值仍是字段派生和，均不是新增独立车辆或 OD 证据。
保存的 SUMO 状态仅作证据，局部车道保持过程的状态恢复仍未验证。

## 复现与证据

从仓库根目录执行，两个输出目录必须为新目录：

```powershell
$env:PYTHONPATH = "$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME = "$PWD/.venv/Lib/site-packages/sumo"
$env:PATH = "$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe plugins/astra-sumo/scripts/prepare_hamburg_readme_initialization.py entry-alternative benchmarks/hamburg_ring1_initialization_population_20261021_v1 outputs/new-entry-inputs --profiles-file benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base/ideal-lane-profiles.json.gz
.venv/Scripts/python.exe plugins/astra-sumo/scripts/prepare_hamburg_readme_initialization.py run outputs/new-entry-inputs outputs/new-entry-native
```

- 执行：[入口](../../plugins/astra-sumo/scripts/prepare_hamburg_readme_initialization.py)、[替换与原生运行](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_initialization.py)。
- 分析：[逐车分析](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)、[核对时分析记录](analysis-at-verification.py)、[独立核对](analysis/independent-checks.json)、[判断](analysis/decision.json)。
- 参数及输入：[完整输入](inputs/)、[场景声明](inputs/scenario.json)、[单项替换声明](inputs/entry-alternative-proposal.json)。
- 原始结果：[原生文件](native/)、[本小时结果](native/initialization-check.json)、[基准与本次比较](analysis/comparison.json)。
- 检查：[测试记录](tests.xml)、[实现校验值](implementation-sha256.json)、[完整清单](manifest.json)。

大文件使用无损 gzip。原始失败记录保留。
网络未改变，沿用 [原流程的固定航拍与 NetEdit 图](../hamburg_ring1_lane_section_hold_full_20261021_v1/README.md)。
资料署名和许可沿用 [官方源包](../hamburg_ring1_official_sources_20261020_v1/README.md) 与 OSM。
