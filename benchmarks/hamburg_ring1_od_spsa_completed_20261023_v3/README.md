# Hamburg 首轮 SPSA 完成记录 V3

整理日期：2026-10-23。首轮核验已完成，有效标定未完成。
四次原生评价均有碰撞、传送和入口积压。全部误差只作诊断，不能作为有效标定指标。
更新后误差未改善。单轮无效运行不能证明 SPSA 无效或零残差不可行。

## 固定场景与方法

地点仍为 Hamburg Binnenalster/Ring1，保留 LSA104、118、119、200、535。
使用既有 236 条道路网络和 13 个检测字段，观测窗口为 2026-10-19 05:30–08:00 UTC，即当地 07:30–10:00 CEST。
真实计数不变。40 个站点值为字段之和，不是独立观测。

需求为 21 个 OD、14 个出发时段，共 294 个变量。
路线、车道和出发分布继承已声明的经验先验，不是观测得到的真实 OD。
信号程序保持冻结。SPSA 调整出发需求，不调整信号或道路。
车道引导与运行监测使用模拟车辆状态，它们不是现场传感器。

仿真步长 0.1 秒，种子 104，运行至 14400 秒。
0–3600 秒为初始化，3600–12600 秒为主要计数窗口，12600–14400 秒为排空观察。
每次均从空网开始，没有保存状态重启。
计数唯一来源为原生 E1 的 `nVehContrib`，按 900 秒聚合。

普通 SPSA 使用 $K=1,A=0,c=2,\alpha=0.602,\gamma=0.101$。
第一对原生损失用于选择增益，使首步变化约为 1 辆，并直接用于首轮更新。
由 $L^+=63702,L^-=78894$ 得到 $a=a_1=1/3798$。
算术检查见 [arithmetic-check.json](arithmetic-check.json)。
实际更新与保存公式一致。相对简化式 $x_1=\max(0,x_0+\Delta)$ 只有约 $1.1\times10^{-16}$ 的浮点差异，整数车辆数完全一致。
该算术检查不替代原生仿真。

## 完整结果

| 评价 | 请求车辆数 | 计数 SSE | 字段诊断 MAE | 最大字段误差 | 碰撞 / 传送 | 等待进入 | 在途 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 初值 | 8640 | 61146 | 14.923077 | 60 | 3 / 3 | 976 | 424 |
| 正向扰动 | 8605 | 63702 | 15.615385 | 72 | 5 / 5 | 1211 | 365 |
| 反向扰动 | 8677 | 78894 | 17.676923 | 67 | 2 / 2 | 1519 | 396 |
| 更新后复测 | 8622 | 65254 | 15.646154 | 62 | 6 / 6 | 1212 | 407 |

初值复用了已完成的原生数据。本轮新增三次完整原生运行，没有重复跑初值。
四次评价都有全部 130 项字段和 40 项派生站点结果，没有删除不匹配项。
初值站点诊断 MAE 为 47.45，更新后为 49.85。完整残差见下表。

| 评价 | 130 个字段时段 | 40 个派生站点时段 |
|---|---|---|
| 初值 | [字段表](evaluations/initial/field-comparison.csv) | [站点表](evaluations/initial/station-comparison.csv) |
| 正向 | [字段表](evaluations/iteration-0001-plus/field-comparison.csv) | [站点表](evaluations/iteration-0001-plus/station-comparison.csv) |
| 反向 | [字段表](evaluations/iteration-0001-minus/field-comparison.csv) | [站点表](evaluations/iteration-0001-minus/station-comparison.csv) |
| 复测 | [字段表](evaluations/final/field-comparison.csv) | [站点表](evaluations/final/station-comparison.csv) |

正向、反向与复测共 13 次碰撞，全部位于 `:LSA104_part0_12_0`。
[安全记录](safety-summary.json)保留车辆、位置、时间和运行汇总。
这支持优先调查该处通行与冲突关系，不能单凭这些结果确定全部残差的原因。

## 新电脑可用的材料

[result.json](result.json)、[settings.json](settings.json)、[evaluations.json](evaluations.json)、[iteration-0001.json](iteration-0001.json)保留更新与全部诊断结果。
[execution.json](execution.json)记录实际源码提交、运行环境和实现散列。
实际计算使用提交 `16dca0a8d466a854d9b6f75e9110e08796c8ca02`。
Python 为 3.12.13，NumPy 为 2.5.1，SUMO、TraCI、sumolib 均为 1.27.1。

各评价目录包含原生 E1、Summary、TripInfo、碰撞、车辆路线及实际输入的 `.gz` 副本。
解压到新的分析目录后可直接复核，不需要重新仿真。
[raw-inventory.json](raw-inventory.json)记录全部本机原始文件。
仅逐秒 `queues.xml` 和即时 `instant-e1.xml` 未加入 Git，清单见 [local-only-large-traces.json](local-only-large-traces.json)。
若要完整调查这些细节，另外迁移旧机对应 `outputs` 目录。不要把文件缺失解释成零交通。

原始研究目录为 `outputs/hamburg-od-spsa-standard-20261022-v2/`。
初值原始目录为 `outputs/hamburg-od-initial-native-20261022-v1/`。
旧绝对路径和进程号只记录执行历史，不能直接用于新电脑重启。
本轮已结束，代码没有原位续接 SPSA 的功能，也没有经验证的保存状态重启。

运行入口：[`run_hamburg_od_spsa.py`](../../plugins/astra-sumo/scripts/run_hamburg_od_spsa.py)。
方法和计数分析：[`hamburg_od_spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_spsa.py)。
OD 输入来源：[已批准模型](../hamburg_ring1_spsa_od_definition_20261022_v1/README.md)。
道路、控制与图像来源：[五路口走廊](../hamburg_ring1_munich_corridor_20261022_v1/README.md)。
本次冻结脚本 [freeze-at-execution.py](freeze-at-execution.py)只读完成结果并核对原生计数，不启动 SUMO。

最终目标仍为 130 项字段和 40 项派生站点全部零残差，并通过运行有效性检查。
本记录不改变目标，不自动开始多轮试验。下一步先核对碰撞、积压及当前模型假设，再处理一个明确问题。
