# 汉堡 M2 固定控制候选

本包安装并检查了声明的固定控制候选。网络校验值保持 `e906ab92d6e3276f5b4dd7370547270af007f9c58a56d9945f421d1aaf2554ff`。
小样本有效，不代表完整时窗有效，也没有完成计数标定。
Claude 要求在完整运行前修正未绑定机动车连接的 `O` 状态。原始 A 候选保留在本包，不能作为最终控制接受。

## 已核对的内容

119、535 使用 OCIT 源引用 `1`（SP1）；104、118、200 使用引用 `5`（SP5）。
104、118 的固定方案替代所选时段的感应控制，是声明的等效处理，没有恢复现场感应响应。
仿真 0 对应 2026-10-19 04:30 UTC，即 06:30 CEST；535 从此时使用 SP1。更早的热身必须另用 SP10。
源周期零点假设为当地午夜。所选周期均为 90 秒，源偏移为零，23400 秒为整周期数。
这个绝对相位是声明，未由现场信号观测确认。

控制对应采用 OCIT 内嵌 `TrafficStreamConfigData` 的 `vt` 引用，经官方车道身份绑定到物理道路与车道连接。
没有用 104、118、200 独立 MAP 中的数字替代 OCIT 组号，也没有用旧连接序号识别转向。
90 个物理连接有内嵌官方组对应，6 个附加连接继承同一进口车道至同一出口道路的组。
119 的组合连接取 K1 与 K7 放行的交集；SP1 的 K1 绿灯位于 K7 放行内，断言通过。
红黄与黄灯分别保留源开始、结束过渡时长。熄灭放行用 `O`；有同时绿灯冲突对象的绿灯用 `g`，其余用 `G`。
仅定义熄灭图像 `00`、且程序省略的组声明为 `O`，包括 200 的 K3、K7；没有把省略的程序行当作已测时序。
200 进口施工阶段不一致仍未解决。

原有 59 个非官方感应控制器中，Z.15 的 `6390683126` 单列为声明方案，其余 58 个保留原相位状态与时长并改为固定运行。
新程序去掉感应时长范围和一个无实际连接的末尾状态字符，实际受控连接的状态不变。
Z.15 三个文件分别为 [A：K9](control-A.add.xml)、[B：K8 熄灭放行](control-B.add.xml)、[C：阻断](control-C.add.xml)。只对 A 运行了本包的原生小样本。

SUMO 1.27.1 读回确认全部 68 个活动程序均为新固定程序，状态与输出文件一致。
76 条两端均有对应的源安全间隔全部满足：104 为 4 条、118 为 14 条、119 为 22 条、200 为 14 条、535 为 22 条。
119 包含声明的 Z.15/K9 对应。其他源间隔仍逐行保留，未计入已验证条数。

## 小样本与限制

相同的 13 辆乘用车分别从仿真 0 和 3600 秒开始，各运行 1800 秒；车辆出发时刻相应平移。
两组均 13 辆进入、13 辆到达，无碰撞、传送、积压或未完成车辆。
相对行程输出及 E1 的整数计数完全相同。
E1 连续统计的最大差异为 $2.0002\times10^{-12}$，核对阈值为 $10^{-9}$；计数没有使用容差。
没有新增警告。源网络的两条无感应检测器警告和一条未用状态字符警告仍在，原始日志保留。
小样本未执行途中车道引导，不能证明 13 个字段都获得了目标分配。

74 个未绑定的官方控制器连接被初始声明为 `O`，其中 34 个允许乘用车。
基准理想车道意图在其中两个连接安排了 872 次通过；自然车道选择还能使用其他连接。
这些值来自离线意图，不是交通实测。全部连接见 [绑定表](link-bindings.json.gz) 和 [乘用车未绑定项](passenger-fallback-inventory.json)。
尤其 535 车道 4 的附加左转可能与车道 5 的官方左转同时进入，历史碰撞已有该路径证据。
安全间隔检查只核对已有对应，不能替这些未绑定项证明安全。
后续 A′ 将另行声明状态；本包不删除合法转向、不改车道权限或几何。

## 复现与原始文件

在仓库根目录使用 `.venv` 和 SUMO 1.27.1：

```powershell
$env:PYTHONPATH = "$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME = "$PWD/.venv/Lib/site-packages/sumo"
$env:PATH = "$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe plugins/astra-sumo/scripts/build_hamburg_readme_control.py benchmarks/hamburg_ring1_m2_control_20261021_v1/replay-request.json outputs/ring1-m2-replay
.venv/Scripts/python.exe plugins/astra-sumo/scripts/probe_hamburg_readme_control.py outputs/ring1-m2-replay benchmarks/hamburg_ring1_m1_2_demand_pool_20261021_v1/base benchmarks/hamburg_ring1_m1_demand_pool_20261021_v1/inputs/field-loops.add.xml outputs/ring1-m2-check-replay
```

两个输出目录必须是新目录。需求与观察文件沿用已冻结的 M1、M1.2；来源署名及许可沿用官方源包与 OSM。

- 执行：[控制安装](../../plugins/astra-sumo/scripts/build_hamburg_readme_control.py)、[原生小样本](../../plugins/astra-sumo/scripts/probe_hamburg_readme_control.py)。
- 方法与分析：[hamburg_readme_control.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_control.py)、[hamburg_control_probe.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_control_probe.py)。
- 参数及源输入：[replay-request.json](replay-request.json)、[inputs](inputs/)。
- 原始结果：[生成报告](report.json)、[原生报告](native-check/report.json)、[原生安全间隔](native-check/native-intergreens.json.gz)、[两组运行](native-check/)。大文件以 gzip 保存。
- 先前失败：[重复程序名加载及检查记录](failed-attempts/)。
- 分配来源：[控制安排](claude-assignment-002-and-003.txt)、[A′ 与完整时窗检验安排](claude-assignment-004.txt)。
- 文件与实现校验值：[manifest.json](manifest.json)。

完整 130 个字段时段、40 个派生站点时段的原生残差仍未完成。`exact_count_fit=false`。
