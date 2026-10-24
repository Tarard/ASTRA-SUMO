# 汉堡 M1 离线需求候选

本包完成 Claude 分配的第一项离线准备工作，不是 SUMO 计数标定结果。
没有运行交通，只在 SUMO 1.27.1 中加载路网，读取车道长度、限速和换道权限，仿真步数为零。
字段覆盖、车道意图和到达时间均属于所声明的理想模型。真实驾驶的响应尚未测量。

## 方案与结果

范围保持 README 的 Binnenalster/Ring1 五路口走廊。源观察仍为 2026-10-19 05:30–08:00 UTC，当地 07:30–10:00 CEST。
130 个字段时段全部保留。站点值仍是其成员字段之和，不是独立证据。

| 项目 | 结果 |
|---|---:|
| 保留路线 | 1809 |
| 排除重复道路或可直接替代的绕圈路线 | 1561 |
| 保留但标注的间接转向路线 | 1163 |
| 理想车道意图方案 | 2515 |
| 理想车道意图的结构秩 | 13 |
| 固定车道、不换道的结构秩 | 8 |
| 自然驾驶、无车道引导的原生响应秩 | 未测 |
| 整数方案车辆 | 5334 |
| 预设车道行程总长度 | 3903.357607 车公里 |
| 每车在观察窗内的平均字段通过量 | 1.253656 |
| 当前有限模型的车辆数下界 | 4825 |
| 字段整数等式 | 130/130 成立 |
| 派生站点等式 | 40/40 成立 |
| 高于名义 1800 辆/小时的进口车道时段 | 0 |

两个秩均不是 SUMO 响应实测结果。固定车道模型也不等于 SUMO 自然换道行为。
所有 13 个字段都保留至少两个不重复通过路口的理想车道方案。
整数等式的零差值不是原生 E1 的零误差，`exact_count_fit=false`。

总长度包含普通道路与路口内部道路，并扣除明确设定的出发前端位置 5.1 m。
最小化目标是该有限集合中的车辆行程长度，不是最少车辆数，也不保证恢复真实 OD。
下界来自线性松弛的有理数对偶检验。每一列约束都已重新核对，只适用于本包的有限模型和入口上限。
允许的间接转向方案最多涉及四个字段，因此不能直接使用“每车最多两个字段”的下界推导。

## 路线与入口声明

路线只从声明的边界进入、离开，不重复有向道路。
同一官方路口的连续不同部分合为一次通过，返回已经经过的部分另计一次。其他路口也检查重复通过。
若重复通过可由模型中的直接连接替代，则排除路线。
若直接连接在模型中不存在，则只保留为带标签的间接转向，不把模型缺失解释为现场禁止。
网络及其掉头保持不变，没有新增统一绕行上限。

最终方案使用 452 辆带标签的间接转向车辆。掉头共 452 次，均位于 `1975693623`。
它们仍需后续交通检验，不能据此宣布绕圈或死锁问题已经解决。

入口上限为每条合格机动车道 900 辆/小时。这是初始先验参数，不是实测容量。
短于 7.5 m 的乘用车入口 `-23489785`、`-56754916`、`1360406653`、`1491809365` 均分配零车辆。
另有边界 `186713410#0` 无法按原端点声明解析，同样没有分配车辆。

| 使用的入口 | 方案车辆 |
|---|---:|
| 25866659#1 | 2250 |
| 31106387 | 1122 |
| -142944318 | 775 |
| 44532531 | 482 |
| 142944473#0 | 255 |
| 26623877 | 65 |
| 543550497#0 | 385 |

上表以逐入口文件为准。8.91 m 的 `25866659#1` 在十个时段达到上限，仍是未解决的进入压力。
主干道前缀替换已加入候选，服务道路的主干道替代仍标为候选。
不应把这个初始分配称为已验证的合理交通需求。

车辆全部按标准乘用车设置：长 5 m、最小间距 2.5 m、速度系数 1、sigma=0、出发位置 5.1 m。
车道方案遵守通行与换道权限，但允许普通道路上的瞬时换道，未计换道时间或交通间隙。
自由流时间包括内部道路，未计信号、排队及加减速。
车辆文件只指定出发车道，途中车道意图尚未执行。
按 0.1 s 步长取整出发时刻后，离线等式仍全部成立。

## 数据与复现

使用仓库 `.venv`，运行：

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/build_hamburg_readme_demand.py benchmarks/hamburg_ring1_m1_demand_pool_20261021_v1/replay-request.json outputs/ring1-m1-replay
```

输出目录必须是新目录。此命令不会运行完整交通仿真。

- 执行入口：[build_hamburg_readme_demand.py](../../plugins/astra-sumo/scripts/build_hamburg_readme_demand.py)。
- 路线与车道处理：[demand_route_pool.py](../../plugins/astra-sumo/src/astra_sumo/core/demand_route_pool.py)。
- 时间、求解与分析：[hamburg_readme_demand.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_demand.py)。
- 当前方法边界：[汉堡走廊说明](../../docs/hamburg-readme-case.md)。需求重建沿用[既有逆问题说明](../../docs/hamburg-digital-twin-development-log.md#route-inference-as-a-sensor-matrix-inverse-problem)。
- 参数与原始输入：[replay-request.json](replay-request.json)、[inputs](inputs/)。沿用原 OSM 及官方源包的署名与许可。
- 路线与意图：[路线池](route-pool.json.gz)、[理想车道方案](ideal-lane-profiles.json.gz)、[未执行的途中意图](provisional-lane-intentions.json.gz)。
- 整数方案：[integer-proposal.json](integer-proposal.json)、[车辆文件](provisional-demand.rou.xml.gz)、[矩阵与上限](provisional-system.npz)。
- 完整差值：[字段](provisional-field-comparison.csv)、[派生站点](provisional-station-comparison.csv)。这些表没有原生 E1 实测值。
- 负荷：[入口](entry-loads.csv)、[进口车道](approach-lane-loads.csv)、[原生入口条件](entry-inventory.csv)。
- 下界与复核：[对偶检验](vehicle-lower-bound.json)、[独立复算](independent-checks.json)、[来源及文件校验值](manifest.json)。
- 分配来源：[Claude Assignment 1](assignment-001.txt)。

后续控制安装、原生响应、整数校正及图像仍未完成。正式计数只能来自原生 E1 的 `nVehContrib`。
