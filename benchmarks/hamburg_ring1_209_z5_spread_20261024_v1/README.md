# 209健康源上的Z5出发跨度检验

一次完整原生检验未改善主130误差。MAE从3.238462升至3.284615，TAE从421升至427，SSE从5131升至5529，最大误差从21升至31。当前最低已测健康主130 MAE仍为[209源的3.238462](../hamburg_ring1_209_ordinal_connection_20261024_v1/README.md)。主130尚未归零。

| 指标 | 209源 | spread1.5 |
|---|---:|---:|
| 主130 MAE / TAE |3.238462 / 421|3.284615 / 427|
| 主130 SSE / 最大误差 |5131 / 21|5529 / 31|
| 初始化52 TAE / SSE |136 / 1894|136 / 1894|
| 全182 TAE / SSE |557 / 7025|563 / 7423|
| 派生40站点MAE |9.875|10.025|
| 插入 / 到达 |8640 / 8640|8640 / 8640|
| 等待 / 在途 / 碰撞 / 传送 / discard |0 / 0 / 0 / 0 / 0|0 / 0 / 0 / 0 / 0|

两次运行均有8640个唯一预期字段贡献，无重复或额外字段。初始化52行计数逐行相同；535 Z5–Z8指定56行仍全部零。原始状态`diagnostic_measurement_complete`保持不变，另将本次选择结论记录为`not_improved`，没有新增SSE或全182晋级门。

唯一公式对源请求bin h4–13的521辆119 Z5车生效：以每个源bin原始最早和最晚出发时刻的中点c，计算`t_new=c+1.5*(t_old-c)`。没有clamp或额外平移。它保持区间中点，不保持样本均值。全部521个native毫秒出发时刻改变；55辆退入前一请求bin，其中3辆跨入初始化。实际294库存重算，初始请求1953→1956，主窗请求6687→6684。21个OD总量、13字段总量、8640总请求保持。另8119辆整车XML原字节和相对顺序保持，全部ID、route、stop、field、vType及驾驶规则不变。

网络、控制、loops和全部130+52真实观测与209源原字节相同。准备后发现scenario继承文本误称“departures unchanged”，仅修正`demand_prior`一属性。旧scenario、scope、prepare和measurement-plan原字节保存在`spread15/metadata-correction-history`。旧输入独立审查与修订记录分别保留，没有改旧hash来掩盖差异。运行父标签明确为`source_209_parent`，不是较早的+15结果。

原生字段变化未按出发投影直接计数。Z5主窗贡献459→478，但TAE79→80；Z4贡献575→558，TAE85→48。两字段合计1034→1036，仅描述这一对完整运行。Z12/Z13 TAE从80/84升至98/104。它不证明共同硬容量、平滑响应或真实出发先验，也不支持继续扩大该spread参数。源Z5的74个尾窗事件均实际到达，不能称为终态积压。

本包保留完整130/52/182/40比较、208原生E1区间、compact逐车完整贡献、summary、tripinfo、vehroute、碰撞与警告、运行输入、代码及runtime哈希。`independent`是终态独立审查；`independent-input`是元数据修订前的范围审查；`source-concentration`保留逐请求bin的跨度、actual插入与E1 lag和74辆尾窗表。所有gzip均保持解压后的原字节，旧共享字节通过固定父包hash引用。大instant/queues仅保留本地路径、大小和SHA。冻结没有启动SUMO，也没有改原生结果。

离线核验默认不会运行SUMO：

```powershell
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_209_z5_spread_20261024_v1/reproduce.py --mode verify
```

只有显式`native-full`会重新运行，必须使用新目录：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = (Join-Path $PWD '.venv/Lib/site-packages') + ';' + (Join-Path $PWD 'plugins/astra-sumo/src')
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_209_z5_spread_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-209-z5-spread
```

入口核项目Libsumo/SUMO1.27.1实际路径、binary/pyd和执行core哈希，直接读取冻结需求，不重建OD。复现同一结果不等于完成标定或证实现场控制。
