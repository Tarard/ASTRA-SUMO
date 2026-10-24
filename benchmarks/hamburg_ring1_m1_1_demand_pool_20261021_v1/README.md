# 汉堡 M1.1 入口上限与间接路线归因

本包是离线调查证据。三组整数等式成立，均不是 SUMO 原生 E1 标定结果。
没有运行交通。452 辆间接车辆仍利用 `1975693623` 掉头后返回 535，不能接受为最终需求。
后续单次通过规则将单独冻结，保留本包结果。

## 入口分配

范围、130 个字段时段、40 个派生站点时段和全部源数据沿用 [M1](../hamburg_ring1_m1_demand_pool_20261021_v1/README.md)。
目标仍为最小化含普通及内部道路的车辆行程长度。字段等式、路线集合和出发时段条件不变。
入口上限按合格机动车道计：主干及次干道路及其连接段 900、三级道路 600、住宅及未分级道路 200、服务及居住街道 100 辆/小时。
这是声明的需求先验，不是实测通行能力。敏感性只改变后两类，分别减半与加倍。
每个 15 分钟上限取整数下界，因此 50 辆/小时对应 12 辆/时段。

| 项目 | 减半 | 基准 | 加倍 |
|---|---:|---:|---:|
| 车辆 | 5324 | 5334 | 5334 |
| 车公里 | 4547.092825 | 4442.835544 | 4257.980096 |
| 每车字段通过量 | 1.256011 | 1.253656 | 1.253656 |
| 有限模型车辆下界 | 4825 | 4825 | 4825 |
| 25866659#1 入口车辆 | 250 | 500 | 1000 |
| 31106387 入口车辆 | 120 | 250 | 500 |
| 44532531 入口车辆 | 2482 | 2232 | 1732 |
| -142944318 入口车辆 | 775 | 775 | 775 |
| 28665128#2 入口车辆 | 0 | 0 | 0 |
| 间接掉头车辆 | 452 | 452 | 452 |
| 字段等式 | 130/130 | 130/130 | 130/130 |
| 派生站点等式 | 40/40 | 40/40 | 40/40 |

逐入口分配、占比与达到上限的时段见 [sensitivity-summary.json](sensitivity-summary.json) 和各组 `entry-loads.csv`。
逐列有理数对偶检查、出发时刻按 0.1 秒取整后的等式检查及车辆文件复核均通过。
下界只适用于声明的有限模型。站点仍是字段之和，不是独立证据。

## 间接路线归因

完整检查 M1 的 1163 条标注路线、5581 个重复通过事件及 80 个不同的首进口至末出口组合。
其中 18 个事件匹配未解决的官方 `200:28` 乘用车转向候选；283 个属于其他已说明原因；5280 个在所查 MAP 及限定路口范围的 OSM 来源路径中未找到对应。
路线类别成员数为 18、275、1091，可能重叠，不能相加作为路线总数。
来源路径检查复用已有通行权限与路口归属处理；需要内部换道的路径只列为未验证候选。
MAP 对应使用已保存的道路候选，不把这些候选当作已确认的现场对应。

全部 452 辆选中间接车辆都只属于“所查来源未找到对应”一类；没有匹配未解决官方转向的车辆。
因此，“缺少官方直接转向迫使这批车辆掉头”的解释未获支持。
资料中的缺失也不能证明现场禁止该转向。
每条路线的全部重复事件、每辆间接车辆及来源路径证据见 [归因报告](attribution/report.json)、[事件](attribution/attribution-events.csv)、[路线](attribution/flagged-routes.csv)、[车辆](attribution/selected-indirect-vehicles.csv) 和 [不同转向组合](attribution/unique-movement-attributions.json)。

## 复现与文件

使用仓库 `.venv` 和 SUMO 1.27.1。历史结果需配合 [manifest.json](manifest.json) 中的实现校验值；后续规则可能改变路线集合。
输入引用同仓库的冻结 M1 包，不依赖本机 `outputs`。

```powershell
$env:PYTHONPATH = "$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME = "$PWD/.venv/Lib/site-packages/sumo"
$env:PATH = "$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe plugins/astra-sumo/scripts/build_hamburg_readme_demand.py benchmarks/hamburg_ring1_m1_1_demand_pool_20261021_v1/replay-base.json outputs/ring1-m1-1-replay
.venv/Scripts/python.exe plugins/astra-sumo/scripts/attribute_hamburg_indirect_routes.py benchmarks/hamburg_ring1_m1_demand_pool_20261021_v1 benchmarks/hamburg_ring1_m1_1_demand_pool_20261021_v1/inputs/candidate-manifest.json.gz benchmarks/hamburg_ring1_m1_1_demand_pool_20261021_v1/inputs/original.net.xml outputs/ring1-m1-1-attribution-replay
```

输出目录必须是新目录。另两组请求为 `replay-half.json` 和 `replay-double.json`。
各组保留原始求解报告、矩阵、车辆、理想车道意图、全部差值及负荷表；大文件以 gzip 保存。
没有途中车道引导，也没有 E1 实测计数。

- 执行：[需求准备](../../plugins/astra-sumo/scripts/build_hamburg_readme_demand.py)、[来源归因](../../plugins/astra-sumo/scripts/attribute_hamburg_indirect_routes.py)。
- 分析：[需求模型](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_demand.py)、[来源路径归因](../../plugins/astra-sumo/src/astra_sumo/core/demand_route_attribution.py)。
- 参数与原始结果：[基准](base/)、[减半](half/)、[加倍](double/)、[来源输入](inputs/)。
- 分配来源：[Claude Assignments 2–3](assignment-002-and-003.txt)。

所有原始资料沿用既有官方及 OSM 来源的署名与许可。
