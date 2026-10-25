# 汉堡固定车道需求的容量诊断

本包是有限需求方案的静态诊断，没有运行 SUMO，也不是标定结果。
130 个字段时段和 40 个派生站点时段均保留。输入来自上一轮完整流程的失败方案。

| 假设 | 目标通过数 | 有理数证书给出的上界 | 结论 |
|---|---:|---:|---|
| 每车道 1800 veh/h，名义放行比例，系数 0.8 | 6687 | 4396 | 当前有限规划模型无解 |
| 同上，系数 1.0 | 6687 | 4956 | 当前有限规划模型无解 |

证书按全部 430 个需求列逐项检查。详见 [独立核对](independent-certificate-checks.json)、[完整声明](report.json) 和两份 `*-certificate.json`。
感应信号使用名义相位时长。资源时段按自由流到达时间分配。因此，这些上界不是原生仿真的实际容量上界，更不是现场交通不可行的证明。

当前路线方案为每个字段组合只保留一个车道序列。它把经过 LSA200 的相关车辆全部固定在车道 1。
原生权限和转向连接检查发现，同一道路路线有车道 1–4 的结构备选，字段组合相同，且保留官方已支持转向的筛选。
这些备选仍可能需要在短路段换道。它们不证明四条车道均可在实际行驶中充分利用。
[结构备选记录](structural-lane-alternatives.json) 保留全部证据。

下一步只检验局部车道引导：移除未观测路段的途经点，并恢复这些路段的原生换道。
[移动途经点位置建议](waypoint-relocation-proposal.csv) 本身没有经过完整原生运行验证。
容量诊断是本案例的调查工具，不是日常测量的新增前置条件。

复现：

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/screen_hamburg_signal_capacity.py benchmarks/hamburg_ring1_end_to_end_20261021_v1 outputs/new-capacity-screen
```

执行：[screen_hamburg_signal_capacity.py](../../plugins/astra-sumo/scripts/screen_hamburg_signal_capacity.py)。
计算：[hamburg_capacity_screen.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_capacity_screen.py)。
输入与原生失败记录：[上一轮完整流程](../hamburg_ring1_end_to_end_20261021_v1/README.md)。
原始本地诊断输出位于 `outputs/ring1-capacity-screen-20261021-v2/`。
