# 汉堡主观测窗前的官方计数

本包保存同一批 13 个字段在 2026-10-19 04:30–05:30 UTC，即当地 06:30–07:30 CEST 的计数。
该小时有 156 条五分钟源观测，聚合为 52 个十五分钟字段时段，全部完整。
字段通过次数合计为 1953。该合计不识别独立车辆总量或真实 OD。

来源仍为 Freie und Hansestadt Hamburg 的 [SensorThings 计数服务](https://iot.hamburg.de/v1.1/)。
`monday-observations.json.gz` 保存对原 13 个字段的完整周一响应、请求 URL 和取得时间。
`initialization-counts-15min.csv` 保存前一小时；`all-counts-15min.csv` 保存此前一小时及原主窗。

原主窗仍为 05:30–08:00 UTC，即 07:30–10:00 CEST。
重新取得并聚合的 130 行主窗数值与冻结目标全部一致，详见 [检查结果](report.json)。
没有改写原目标文件，没有删除字段，没有把缺失数据补为零。
四个站点仍是原字段的派生和，本包不增加独立站点证据。

这些资料用于约束初始化需求，不是新的 SUMO 结果，也不是已完成的需求标定。
当前“一字段一次通过”的需求模型若要同时代表初始化期和主窗，应显式区分相应人口与出发时段。
不能仅将既有主窗人口前移，并继续要求全部人口都贡献主窗计数。

复现数据取得与原目标核对：

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/fetch_hamburg_readme_initialization.py benchmarks/hamburg_ring1_count_window_20261020_v1 outputs/new-initialization-counts
```

执行调用仓库既有的 SensorThings 读取、时间窗聚合和完整性检查。
[取得时的执行记录](source-fetch-at-acquisition.py) 保留来源过程，[测试](tests.xml) 可用冻结响应离线重放。
[manifest.json](manifest.json) 保存所有文件校验值。
