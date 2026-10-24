# Hamburg761 lane fan-out comparison, 2026-10-24

两个固定需求完整比较均未改善计数。源支持和小车通行验证保留，但不选用此候选替换当前计数参考。最佳仍是[remove43](../hamburg_ring1_119_late_demand_thinning_20261024_v1/README.md)的MAE **2.492307692**、TAE324；主窗130值未全部归零。

| Fixed demand | N | Parent main TAE / MAE | Candidate main TAE / MAE | SSE | Max | Initial52 TAE | All182 TAE | Station40 MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| retained8597 | 8597 | 324 / 2.492307692 | 448 / 3.446153846 | 5920 | 28 | 143 | 591 | 10.05 |
| restored8640 | 8640 | 365 / 2.807692308 | 442 / 3.400000000 | 5870 | 28 | 143 | 585 | 9.80 |

每个候选都保持其自身父需求的完整字节。两组父需求不同：8597组来自remove43，8640组来自[−30秒出发反馈](../hamburg_ring1_535_late_departure_feedback_20261024_v1/README.md)。二者不能混为同一需求，也不据两个候选差值独立归因43辆请求。两项均全部进入并到达，每辆车一次正确意向贡献，等待、在途、碰撞、teleport和discard均为零。535 Z5–Z8的56值仍全部精确。源初始化TAE97均恶化为143。网络从零时刻改变，未声明相同交通或计数前缀。

候选只在priority节点7115619106增加 `761590046_0→199500674#0_1/_2`，保留原0→0。全部普通道路XML、权限、控制程序、真实130+52观测和驾驶机制不变。原内部物理曲线保留，但原生三lane内部edge的公共逻辑长度由8.871929米变为 **8.555760785474米**。这是SUMO1.27.1按三条原生曲线弧长取未加权平均的表示变化，不是测绘长度或普通道路长度修改。因此本比较同时包含新增连接及其原生内部长度表示。

2022官方MAP、V1拓扑及有限航片支持这一条件fan-out假设，不能证明2026观测日的现场连接或SPaT。计数恶化也不否证物理假设。外部控制字节不变，不代表实际感应时序必然相同。

源和候选各一次三车探针都完成3辆原完整路线并安全到达。原模型三辆都走via0；候选的Z12/Z13/Z11分别实际走via0/1/2并进入相应接收lane。via观测与下游换道分列。探针保留原field moving-stop，另用199上的probe-only moving waypoint选择出口。全程speedMode31；原机制的laneChangeMode1621/512及位置另外记录。小样本不证明完整需求安全、饱和服务或现场容量。

三次development失败或拒绝、原实现及custom-length解释、v1报告元数据问题和v2更正均保留。v1/v2最终网络字节相同；最终绑定v2的16项scope检查、独立候选核对和17项测试记录。小车执行前的模式术语澄清与候选路径更正保留原plan/runner。完整执行前的source-hash列表格式错误也保留；这些执行前修正均未新增交通仿真。

本包保留core/test源码、源码审查、构建产物、两小车原始FCD和mode、两完整原生208E1及130/52/182/40比较、trip/routes/身份、独立审计、响应账和实际wrapper/shared源码。文件按原字节gzip或相同SHA引用。完整大instant/queues仅保存本地路径、大小和SHA；已封独立审计保留其与compact的原始重建核对。

离线核验不运行SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_761_fanout_20261024_v1/reproduce.py
```

可选原生复现须使用记录的项目SUMO1.27.1和项目环境，显式选择一组，并写入新的包外目录：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_761_fanout_20261024_v1/reproduce.py --mode native-full --case retained8597 --output-dir outputs/761-retained8597-reproduction
```

封包和离线测试未新增原生运行。本组不宣称现场已修复、真实OD、最优或归零。
