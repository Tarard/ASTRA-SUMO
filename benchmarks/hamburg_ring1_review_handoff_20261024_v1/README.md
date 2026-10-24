# Hamburg calibration review handoff — 2026-10-24

当前标定未完成，实验已经停止。本目录供外部审阅，未启动新仿真。

先读 [中文交接摘要](handoff.txt)，其中说明十八小时工作的主要问题、当前结果和待判断事项。摘要中的提交 `1ebe87e` 是交接前的证据基准。

当前最佳完整有效运行：130项原生 E1 `nVehContrib` 的 MAE **2.115384615**、总绝对误差 **275**、最大绝对误差 **22**。8589辆全部进入并到达，碰撞、传送、丢弃和结束等待均为零。零残差目标尚未达到。

## 读取顺序

1. [最好完整结果与全部残差](../hamburg_ring1_chronological_od_feedback_20261024_v1/README.md)。
2. [固定模拟灯色的对照](../hamburg_ring1_275_fixed_history_20261024_v1/README.md)。同需求重现275，新增9辆得到286，仍未改善。
3. [上游供给中断的离线诊断](service-diagnosis/summary.json)。
4. [入口需求与普通道路出口记录](demand-diagnosis/demand-side-diagnosis.json)。
5. [局部只读观测的执行版本说明](observer-lineage-recovery.json)。实际执行的是 `observer/native/executed-observer.py`，不能把后改的 `observer/record_prefix.py` 当成执行版本。

## 审阅边界

- 原始观测、合法道路及转向保持。不要用改观测、绕圈或运行中消车消除残差。
- 三个上游节点6390683114、244203524、244203525有交通信号或过街资料依据。当前自动生成配时并不等于现场SPaT。
- 451帧定点观测保留。其深入机制解释尚未完成，不宣称已有新控制修复。
- `manifest.json`覆盖交接ZIP中的原始文件，不包含本新增README和.gitattributes。原始摘要与记录按字节保存。
- 脚本和绝对路径是历史执行证据。此目录不是新的可直接启动仿真入口。完整运行入口见前两份基准包。

请首先审查控制假设与可调需求的边界，再提出一个能区分原因的有限试验。当前资料没有证明零残差可行或不可行。
