# 汉堡经典OD方向响应核验

本包保存四次相邻出发时段OD方向测量，**没有执行SPSA，130项原生零残差目标仍未完成**。
基线主130项SSE为10749、MAE为5.961538、TAE为775，全部182项TAE为857。
基线仍有68辆未入网、50辆在途，不能作为正式完成标定。
本轮最优已测端点为 `probe-minus1`：主130项SSE为6898、MAE为5.092308、TAE为662、最大误差27，全部182项TAE为744。
该端点仍有7辆未入网、14辆在途，原始判定为 `diagnostic_candidate`，不是正式标定结果。
方法依据与适用边界见[经典文献复核](../../docs/research/hamburg-classic-calibration-2026-10-23.md)和[本轮响应协议](response-protocol.md.gz)。

保留13个字段乘10个主时段的全部130项真实计数，并与原生E1 `nVehContrib`比较；另保留52项真实初始化计数。
40项站点值来自字段求和，不增加独立观测。
每项运行均保留8640辆请求、原道路、40个感应和11个静态控制、种子104、0.1秒步长、14400秒时长及Libsumo 1.27.1。
两项OD数量变化继续使用原有整数化和联合分位数生成，会改变目标群体内的时刻及路线、车道份额。
`q=-1` 实际改变151个请求时刻和56个字段意图及对应停靠模板。两项OD区间的字段库存由Z4/Z5的77/76变为78/75，不能表述为仅移动一辆车的时刻。
后续搜索以同一个prepared模型和该已测向量为中心，不重建分位数模板。选择依据和来源哈希见[选择记录](response-audit.json.gz)。
这些份额是声明的模型先验，不是观测到的真实OD。测量也未分离排队与感应控制的因果贡献。

| 原生测量 | 130项SSE | 130项MAE | 182项TAE | 未入网 | 在途 | 碰撞 | 传送 | 原始判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| [probe-plus6](probe-plus6/verdict.json.gz) | 9917 | 6.084615 | 873 | 49 | 13 | 0 | 0 | rejected |
| [probe-minus6](probe-minus6/verdict.json.gz) | 11112 | 6.461538 | 922 | 55 | 25 | 0 | 0 | rejected |
| [probe-plus1](probe-plus1/verdict.json.gz) | 10997 | 6.376923 | 911 | 69 | 40 | 0 | 0 | rejected |
| [probe-minus1](probe-minus1/verdict.json.gz) | 6898 | 5.092308 | 744 | 7 | 14 | 0 | 0 | diagnostic_candidate |

- probe-plus6：[130项字段](probe-plus6/native/field-comparison.csv.gz)、[40项站点](probe-plus6/evaluation/station-comparison.csv.gz)、[52项初始化](probe-plus6/evaluation/initialization52-comparison.csv.gz)、[全部182项](probe-plus6/evaluation/all182-comparison.csv.gz)。
- probe-minus6：[130项字段](probe-minus6/native/field-comparison.csv.gz)、[40项站点](probe-minus6/evaluation/station-comparison.csv.gz)、[52项初始化](probe-minus6/evaluation/initialization52-comparison.csv.gz)、[全部182项](probe-minus6/evaluation/all182-comparison.csv.gz)。
- probe-plus1：[130项字段](probe-plus1/native/field-comparison.csv.gz)、[40项站点](probe-plus1/evaluation/station-comparison.csv.gz)、[52项初始化](probe-plus1/evaluation/initialization52-comparison.csv.gz)、[全部182项](probe-plus1/evaluation/all182-comparison.csv.gz)。
- probe-minus1：[130项字段](probe-minus1/native/field-comparison.csv.gz)、[40项站点](probe-minus1/evaluation/station-comparison.csv.gz)、[52项初始化](probe-minus1/evaluation/initialization52-comparison.csv.gz)、[全部182项](probe-minus1/evaluation/all182-comparison.csv.gz)。

所有原始verdict按原字节保留。全130项SSE下降也不能替代MAE零误差或运行有效性检查。
`assignment-audit/`包含分配矩阵、限制及独立新结果，分配比例不等于Jacobian。
`prepared/`保存当前模型、x0和初值一致性证据。每项保存源向量、实际需求、完整原生计数、Summary、TripInfo、车辆路线、碰撞日志、运行和驾驶实现来源。
大体积instant E1和queues仅保留本地路径与SHA-256，见[本地大文件清单](local-only-large-traces.json)。
完整文件及压缩前字节哈希分别见[清单](manifest.json)和[来源](source-provenance.json)。

离线复核和拒绝性测试不启动SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_classical_od_response_20261023_v1/freeze_evidence.py --verify-only benchmarks/hamburg_ring1_classical_od_response_20261023_v1
.venv/Scripts/python.exe -m pytest benchmarks/hamburg_ring1_classical_od_response_20261023_v1/test_freeze_evidence.py -q
```

`reproduce.py`会明确启动一次完整原生测量，使用前先查看其参数。冻结过程只做离线核对。
OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
