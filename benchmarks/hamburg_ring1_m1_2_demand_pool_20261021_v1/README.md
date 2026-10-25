# 汉堡 M1.2：需求路线只通过每个路口一次

本包完成离线路线修正，尚未完成原生 E1 计数标定。没有运行交通。
源网络、合法掉头、传感器、13 个字段及全部 130 个观察时段均保持原样。
本包取代 [M1.1](../hamburg_ring1_m1_1_demand_pool_20261021_v1/README.md) 中仍含 452 辆绕行车辆的需求候选。旧结果保留。

## 规则与结果

需求路线不重复有向道路，也不再次经过同一路口。直接连接缺失不再成为保留绕行的理由。
连续通过同一官方路口的不同部分合为一次通过；返回已经经过的部分、经过其他普通路口后再进入另一部分，均另计一次。
同一次通过中的掉头仍允许。此次三组方案都未选中掉头。

原始 3370 条路线中保留 468 条，排除 2902 条。308 个理想车道方案覆盖全部 13 个字段，每个字段至少有两个方案。
理想车道方案的结构秩为 13，固定车道、不换道的结构秩为 8；二者均不是自然驾驶的原生响应实测。
当前有限集合中每辆车最多通过两个字段。这是对本集合的检查，不能推广到任意路网或路线。

入口上限及减半、加倍条件沿用 M1.1。后两组只改变住宅、未分级、服务及居住街道的上限。
主干道路每条合格机动车道 900、三级道路 600、住宅及未分级道路 200、服务及居住街道 100 辆/小时。
这些值是需求先验，不是现场容量。

| 项目 | 减半 | 基准 | 加倍 |
|---|---:|---:|---:|
| 车辆 | 6297 | 6297 | 6297 |
| 车公里 | 4711.618760 | 4565.598282 | 4362.622618 |
| 每车字段通过量 | 1.061934 | 1.061934 | 1.061934 |
| 有理数对偶车辆下界 | 6297 | 6297 | 6297 |
| 重复路口的选中路线 | 0 | 0 | 0 |
| 掉头车辆 | 0 | 0 | 0 |
| -142944318 入口车辆 | 1227 | 1227 | 1227 |
| 28665128#2 入口车辆 | 0 | 0 | 0 |
| 25866659#1 入口车辆 | 250 | 500 | 1000 |
| 31106387 入口车辆 | 120 | 250 | 500 |
| 44532531 入口车辆 | 2482 | 2232 | 1732 |
| 字段整数等式 | 130/130 | 130/130 | 130/130 |
| 派生站点等式 | 40/40 | 40/40 | 40/40 |

三组均满足声明的入口上限。有限模型的最少车辆数由等值的整数方案与弱对偶下界共同确定。
距离目标仍为最小化完整普通及内部道路行程，扣除出发前端位置 5.1 m；此处没有声称真实 OD 已恢复。
全部入口占比和达到上限的时段见 [sensitivity-summary.json](sensitivity-summary.json) 及各组负荷表。
基准达到上限的入口为 `25866659#1`、`27470423#0`、`31106387` 的十个时段及 `543550497#0` 的一个时段。

## 证据边界与复现

等式使用理想车道意图和自由流时间，没有计入信号等待、排队、加减速或换道间隙。
途中车道意图尚未执行。站点是字段之和，不是独立证据。
车辆文件、出发时刻按 0.1 秒取整后的字段等式、入口上限及逐列有理数对偶检查均通过。
`exact_count_fit=false`，原生 E1 尚未测量。

源观察为 2026-10-19 05:30–08:00 UTC，即 07:30–10:00 CEST。输入引用同仓库的冻结 M1 包，不依赖本机生成目录。
使用仓库 `.venv`、SUMO 1.27.1 和本包实现校验值：

```powershell
$env:PYTHONPATH = "$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME = "$PWD/.venv/Lib/site-packages/sumo"
$env:PATH = "$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe plugins/astra-sumo/scripts/build_hamburg_readme_demand.py benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/replay-base.json outputs/ring1-m1-2-replay
```

输出目录必须是新目录。另两组请求为 `replay-half.json` 和 `replay-double.json`。

- 执行：[build_hamburg_readme_demand.py](../../plugins/astra-sumo/scripts/build_hamburg_readme_demand.py)。
- 方法与分析：[demand_route_pool.py](../../plugins/astra-sumo/src/astra_sumo/core/demand_route_pool.py)、[hamburg_readme_demand.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_demand.py)。
- 原始结果：[基准](base/)、[减半](half/)、[加倍](double/)。包含全部字段与派生站点差值、路线、车辆、车道意图、矩阵、负荷、下界及独立复算。
- 来源与核对：[manifest.json](manifest.json)、[Claude Assignment 2b](assignment-002b.txt)、[M1.1 完整间接路线归因](../hamburg_ring1_m1_1_demand_pool_20261021_v1/attribution/report.json)。

控制安装、原生车辆响应和计数校正仍未完成。所有源资料沿用既有官方及 OSM 来源的署名与许可。
