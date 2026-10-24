# 汉堡路线分流与时刻反馈的完整原生证据

目标仍是主窗口130项原生E1残差全部为零。**目标未完成**，本包七项运行均不是有效标定结果。
最小的安全诊断MAE为5.961538，仍有68辆未入网、50辆在途。
主窗口为2026-10-19 05:30–08:00 UTC，对应仿真3600–12600秒。
初始化小时另有52项真实计数。每项均从空网运行至14400秒，8640辆请求、步长0.1秒、种子104、Libsumo1.27.1。
道路、信号和车辆数的变化范围由各项实际输入及哈希明确记录。

| 完整运行 | 主窗TAE | 字段MAE | 最大误差 | 182项TAE | 未入网 | 在途 | 碰撞/传送 | 原始判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| z15-residential-full | 1018 | 7.830769 | 40 | 1204 | 45 | 35 | 0/0 | diagnostic_improvement |
| z15-half-full | 817 | 6.284615 | 30 | 896 | 48 | 24 | 2/2 | rejected |
| z15-half-unsplit-full | 775 | 5.961538 | 32 | 857 | 68 | 50 | 0/0 | diagnostic_improvement |
| timing-01-16 | 946 | 7.276923 | 32 | 1028 | 87 | 20 | 0/0 | rejected |
| timing-01-32 | 854 | 6.569231 | 34 | 936 | 56 | 44 | 0/0 | rejected |
| timing-01-64 | 897 | 6.900000 | 32 | 979 | 91 | 72 | 0/0 | rejected |
| timing-01-128 | 874 | 6.723077 | 30 | 956 | 35 | 63 | 0/0 | rejected |

表中130项字段值来自原生E1 `nVehContrib`。40项站点值由字段求和，不是独立观测。
全部182项、完成状态和安全记录均保留。原始verdict未经修改。
`z15-half-full`的两次碰撞和两次传送使其被拒绝，较低误差不能抵消该失败。
路线分流只改变Z15点后的合法尾段，不是已识别的现场分流比例。
单连接修复的小车复现与边界见[LSA119单连接包](../hamburg_ring1_119_single_split_20261023_v1/README.md)。
完整primary起点见[此前冻结包](../hamburg_ring1_primary_branch_repair_20261023_v1/README.md)。

四个时间批次均被拒绝。16/32/64/128是批次上限，最后一项实际只选107辆。
旧记录平移分值不是新车流预测。64和107辆的投影主窗TAE为663和615，原生实测却为897和874。
`response-audit/`保留贡献归属复核。被修改与未修改车辆的差异不是因果分离，不能把记账分组当成独立交通效果。
原始真实目标没有修改，也没有把逐车请求重新送入分位数车辆生成器。

每项的`prepared/`保存实际输入，`native/`保存原生E1、Summary、TripInfo、车辆路线、碰撞、日志和运行记录。
`evaluation/`保存130项字段、40项站点、182项完整比较和OD计数元数据。
执行脚本、preflight、verdict及执行日志按原字节压缩。输入哈希对应本包快照。
大体积instant E1和逐秒queues保留本地路径与SHA-256，见`local-only-large-traces.json`。
三个900秒prefix的关键结果、计数和完成记录另存。它们的主窗口计数仍为null，不能代替完整运行。

离线核查不启动SUMO：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_hamburg_route_timing_feedback_evidence.py -q
```

完整复跑只读取冻结的明确请求，并复用现有原生执行和汇总函数。每次使用新的输出目录：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH="$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME="$PWD/.venv/Lib/site-packages/sumo"
.venv/Scripts/python.exe benchmarks/hamburg_ring1_route_timing_feedback_20261023_v1/reproduce.py --case timing-01-16 --output-dir outputs/route-timing-recheck-new
```

该命令会运行完整仿真。本次冻结只执行离线检查，没有增加原生运行。
OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
