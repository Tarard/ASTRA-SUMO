# Hamburg late-demand quantity comparison, 2026-10-24

本组最低已测健康主窗误差为 remove43：MAE **2.492307692**，TAE 324。130 个主窗值仍未全部归零。这里的请求数量是模型输入先验，不是已识别的真实 OD 总量。

| Case | Requests / arrivals | Main MAE | Main TAE | Main SSE | Max | All182 TAE | Station40 MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Source −30 s | 8640 | 2.807692308 | 365 | 4117 | 23 | 462 | 8.975 |
| remove28 | 8612 | 2.607692308 | 339 | 3727 | 23 | 436 | 8.325 |
| remove30 | 8610 | 2.592307692 | 337 | 3723 | 23 | 434 | 8.275 |
| remove43 | 8597 | 2.492307692 | 324 | 3598 | 23 | 421 | 7.950 |

三个候选均全部进入并到达，等待、在途、碰撞、teleport 和 discard 均为零。每辆车只贡献一次预定字段。初始化52值的 TAE 均为97、SSE均为1117；535 Z5–Z8的56值全部精确。相对源，前169个E1区间的全部原生属性相同。这不表示车辆轨迹相同。

删除发生在仿真输入中，未在运行中消车、缩短路线或改字段。remove28 与 remove30 只减少最后出发期的 Z12/Z13 请求；remove43 在 remove30 上再减少2辆Z11和11辆535 Z4。删除集严格嵌套。幸存请求的完整车辆XML、意图、出发、路线、stop和相对顺序保持原样。294维库存与实际请求数逐项核对；OD和字段总量在此试验中有意改变。道路、控制、驾驶规则及130+52真实观测不变。

历史被删车辆的贡献与幸存车的新响应分别记账。最初两项均有2辆Z12从尾窗回到主窗；remove30至remove43的新增13次删除解释全部计数变化，另有155辆幸存车改变事件时刻但不跨计数窗。这些结果不构成一般响应规律、收敛或容量上限。

源支持表的217辆池是先验分析；其中“未物化28辆”的记录保留其历史时态。首次remove43离线审计的导入失败、部分表和修复后 `complete/` 结果均保留，未因此重跑仿真。后续数量试验不属于本包。

本包保存完整130/52/182/40比较、208原生E1、535全部56值、原始判定、身份与路线核对、输入和实际执行源码。独立审计和响应分解均保留原manifest。六个大型 instant/queues 文件只保存本地路径、大小和SHA256；其余保存原字节gzip或SHA完全相同的引用。源共享字节引用[五项出发反馈包](../hamburg_ring1_535_late_departure_feedback_20261024_v1/README.md)，绑定最终seal `ce28186c…`。

离线核验（不运行SUMO）：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_119_late_demand_thinning_20261024_v1/reproduce.py
```

如需重跑，先按记录设置项目 `PYTHONPATH`、`SUMO_HOME` 并使用项目SUMO1.27.1，然后显式执行下列命令，输出目录必须尚不存在且位于冻结包之外：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_119_late_demand_thinning_20261024_v1/reproduce.py --native-full --case remove43 --output-dir outputs/thinning-remove43-reproduction
```

冻结和离线测试不启动原生仿真；它们也不证明真实OD、现场控制或零误差可行性。
