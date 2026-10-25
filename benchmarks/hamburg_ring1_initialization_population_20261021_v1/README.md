# 汉堡初始化需求：分开保存人口并检验前一小时

本次为同一 README 走廊增加有源计数依据的初始化需求，并完成当地 06:30–07:30 的原生检验。
没有运行主观测窗，没有取得新的 130 字段行或 40 派生站点行标定结果。
当前候选有明显入口积压，不能作为已完成的初始化或有效标定接受。

## 资料、人口与固定条件

初始化资料来自 [同一批 13 个官方字段](../hamburg_ring1_initialization_counts_20261021_v1/README.md)。
2026-10-19 04:30–05:30 UTC，即 06:30–07:30 CEST，有 52 个完整十五分钟字段时段，合计 1953 次字段通过。
该合计不识别独立车辆总数或真实 OD。

本次继续声明一辆模型车辆只对应一个观测字段。
据此新增 1953 辆初始化模型车辆，并保留全部 6687 辆主窗车辆。
8640 是本次模型人口，不是实测独立车辆总量。
每个初始化字段时段沿用该字段在原主窗需求中的路线比例，用最大余数法分配整数车辆。
该比例是继承的需求先验，没有新增实测 OD 证据。

新增车辆复制既有类型、道路路线、入口设置和移动途经点。
初始车尾通过时间猜测均匀分布在每个 900 秒时段的第 150–700 秒，再减既有自由流偏移以生成请求时间。
自由流偏移仅用于初始候选，不能替代拥堵下的原生旅行时间。
没有裁去负时间，没有新增绕圈、重复道路、掉头需求或自行车道通行权限。
主窗车辆的出发时刻、路线和途经点逐车保持原样。

原 130 行主窗目标文件逐字节一致，40 个站点仍是这些字段的派生和。
网络、信号输入和传感器文件也逐字节一致。
局部车道保持机制不变，仍是声明的驾驶意图，不是自然驾驶行为验证。

## 原生结果

使用仓库 `.venv` 的 SUMO、TraCI 1.27.1，步长 0.1 秒，seed 104，结束时刻 3600 秒。
全部在途车辆连续静止 300 秒时终止的检查保持启用。
本次正常到达结束时刻，没有触发该终止条件。

| 检查项 | 本次初始化小时 |
|---|---:|
| 已到请求时刻的初始化车辆 | 1953 |
| 实际进入 | 1533 |
| 到达 | 1308 |
| 结束时仍在路网 | 225 |
| 已请求但未进入 | 420 |
| 文件中保留、尚未到请求时刻的主窗车辆 | 6687 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 |
| 原生 E1 完整通过 | 1431 |
| 有计数的初始化字段时段 | 52 / 52 |
| 完全一致的初始化字段时段 | 32 / 52 |
| 诊断 MAE | 10.923076923076923 |
| 诊断最大绝对残差 | 82 |

计数仅使用原生 E1 `nVehContrib`。
全部 52 行原生计数均与瞬时完整通过身份核对一致。
1431 辆完成通过车辆均通过指定字段一次，没有误入其他字段或重复通过。
它们均恢复原换道模式。另有 15 辆在途车辆仍保持目标车道，没有车辆在恢复前离开。
运行记录有一次紧急制动警告，原始日志完整保留。

1953 辆请求与 1431 辆完整通过之间相差 522 辆，其中 420 辆未进入，102 辆已进入但尚未完成字段通过。
结束时的 225 辆在途车辆中，123 辆已完成字段通过，102 辆尚未完成。
这是模型产生的候选状态，不是现场 07:30 的车辆存量观测。

| 主要积压入口 | 请求 | 进入 | 未进入 | 最大进入等待 / 秒 |
|---|---:|---:|---:|---:|
| `44532531` | 808 | 529 | 279 | 1275.366 |
| `25866659#1` | 170 | 81 | 89 | 1982.634 |
| `543550497#0` | 307 | 255 | 52 | 547.356 |

其余入口均保留在 [逐入口表](analysis/initialization-entry-delays.csv)。
全部残差见 [52 行原生对比](native/field-comparison.csv)，不删除困难字段或时段。
原生 `loaded=1954` 包含预读的一辆未来车辆，不能将它当作本小时请求数。
通用全人口完成检查返回 false，因为主窗车辆尚未到请求时刻，且初始化本身仍有积压。
本报告没有把未来车辆错误地计为本小时未进入，也没有据此宣称整个标定有效。

## 初始化期的控制选择

[逐路口源表核对](analysis/source-schedule-check.json) 覆盖普通周一 06:30–10:00。
119、535 的源表为 SP1，200 为 SP5，均关闭 VA。
104、118 的源表为感应 SP1，当前沿用已声明的固定 SP5 近似。
535 在 06:30 切到 SP1，更早初始化需要另行处理 SP10。
这不是现场控制回放。源周期零点、Z.15/K9 备选及 200 施工阶段不一致继续保留。
其他 58 个 OSM 信号保留原生感应方式，其实际时序可随需求变化。
本次核对没有增加通用年度日历选择功能，也不证明当天现场执行了源表。

## 状态、限制与后续范围

**已验证**：初始化和主窗人口分开记录，主窗车辆与目标保留，初始化小时全部原生计数可核对。
**候选**：从 06:30 空网开始，由上述需求形成的 07:30 交通状态。
**未完成**：合理入口分配、拥堵下的需求校正、主窗完整有效运行及全部 130/40 行零误差。

保存了 3599.9 秒的 SUMO 原生车辆状态，作为状态证据。
局部车道保持过程的内部状态尚未序列化，因此没有验证从该文件继续运行。
不能将这份状态文件当作已验证的标定重启点。

本候选表明，沿用原路线比例仍会在原来的入口积压。
初始化时段不必新增为零误差验收门槛，但这次明显积压不能忽略。
下一项需求修改应先针对入口分配和出发压力，保持道路、控制、驾驶方式及全部主窗目标不变。
本次不再继续完整主窗试跑，也不据此证明所有汉堡模型不可行。

## 复现与证据

从仓库根目录准备新目录后执行：

```powershell
$env:PYTHONPATH = "$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME = "$PWD/.venv/Lib/site-packages/sumo"
$env:PATH = "$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe plugins/astra-sumo/scripts/prepare_hamburg_readme_initialization.py prepare benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1 outputs/new-initialization-inputs --counts-file benchmarks/hamburg_ring1_initialization_counts_20261021_v1/initialization-counts-15min.csv
.venv/Scripts/python.exe plugins/astra-sumo/scripts/prepare_hamburg_readme_initialization.py run outputs/new-initialization-inputs outputs/new-initialization-native
```

- 执行：[入口](../../plugins/astra-sumo/scripts/prepare_hamburg_readme_initialization.py)、[需求和原生检验](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_initialization.py)。
- 分析：[逐车时段和人口区分](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)、[独立核对结果](analysis/independent-checks.json)、[核对时的分析记录](analysis-at-verification.py)。
- 输入：[完整输入](inputs/)、[人口先验与配置](inputs/scenario.json)、[原始初始化计数](inputs/initialization-counts.csv)。
- 原始结果：[运行记录](native/)、[本小时结论](native/initialization-check.json)、[原生状态](native/states/)。
- 检查：[26 项测试](tests.xml)、[实现校验值](implementation-sha256.json)、[完整文件清单](manifest.json)。

大文件使用无损 gzip。原有失败记录不变。
来源与许可沿用 [官方源资料](../hamburg_ring1_official_sources_20261020_v1/README.md) 和原有 OSM 来源。
相同网络的 [固定航拍与 NetEdit 图](../hamburg_ring1_lane_section_hold_full_20261021_v1/README.md) 沿用，本次没有改变网络或重新生成图像。
