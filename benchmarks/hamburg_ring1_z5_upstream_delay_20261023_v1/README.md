# 汉堡Z5上游长延误与车道先验核验

主130项真实计数与原生E1 nVehContrib全部一致的目标仍未完成。本包不构成正式标定。
起点为selected probe-minus1：MAE5.092308、SSE6898、TAE662，7辆未入网、14辆在途。
本轮保留相同道路、控制、8640请求、OD数量、路线与发车时刻，仅交换1对或5对车辆的字段意向及对应moving-stop车道。
这是独立车道先验反事实，不是相同OD模型的分位数更新，也不表示识别了真实驾驶意向。

| 显式车道先验试验 | 130 SSE | 130 MAE | 182 TAE | 未入网 | 在途 | 碰撞/传送 | 原始判定 |
|---|---:|---:|---:|---:|---:|---:|---|
| lane-swap-1pair | 6636 | 4.969231 | 728 | 10 | 15 | 0/0 | rejected |
| lane-swap-5pair | 8644 | 5.523077 | 800 | 0 | 16 | 0/0 | rejected |

1对交换得到最低已测安全诊断MAE4.969231、SSE6636，但未入网10辆、在途15辆，超过预定waiting≤7和unfinished≤21条件，原始标签仍为rejected。
这项计数改善不表示已经消除积压。5对交换首次使全部8640辆插入，仍有16辆在途，而且计数误差变差，因此同样rejected。source5.092308继续保留为本轮选用参考。
完整判断及其范围见[评估记录](assessment.json.gz)，两次有限试验不证明零误差可行或不可行。

每项的native/field-comparison.csv.gz保留13字段×10时段的130项原生对账，evaluation/station-comparison.csv.gz保留40项派生站点值，evaluation/all182-comparison.csv.gz保留52初始化与130主观测。
完整贡献轨迹在evaluation/contributing-passages.json.gz.gz，保留原压缩文件的字节。失败或拒绝结果及原始verdict均保留，不删观测或用缺测补零。
字段库存按请求时段守恒不保证按观测时段计数守恒。安全、未进入、未完成、错字段及重复过点另行核查。

七类分析包括selected-next-diagnosis、queue-audit、control-audit及actual-trace、driver-audit、trace-analysis和lane-prior-probe-plan，均保留原文件与哈希。
trace/baseline保存原生208项E1、Summary、TripInfo、路线、碰撞、115辆完整FCD、TLS状态与程序、全部lanechange、请求与实际执行脚本。
观察回放12项一致性检查通过，104/118状态与90秒声明周期一致。这是当前模型的实际回放，不是现场SPaT。
104 link14和535 link5缺绿等原始警告继续保留。上游排队与后续接收约束不能据单次运行证明不可达。
仅原始instant E1和queues保留为本地路径与SHA-256。FCD及新compact完整贡献已压缩进入本包。

离线核验不启动SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1/freeze_evidence.py --verify-only benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1
.venv/Scripts/python.exe -m pytest benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1/test_freeze_evidence.py -q
```

reproduce.py必须显式指定case与新output-dir，才会用冻结的明确需求调用原生执行；不会调用OD生成器。
离线通用读入与计数对账复用同仓库[前一经典OD包](../hamburg_ring1_classical_od_response_20261023_v1/README.md)的冻结函数，其源码哈希记录在results.json。
OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
