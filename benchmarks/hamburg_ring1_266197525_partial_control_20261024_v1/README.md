# 525部分信号控制：来源、有限小车检验与完整原生结果

当前最低已测健康主130 MAE为**3.038461538**，TAE395、SSE5039。8640辆全部插入并到达，等待、在途、碰撞、传送和discard均为零。8640个完整贡献均为唯一预期字段事件，535 Z5–Z8指定56行仍全部零。主130尚未归零，现场控制与日期适用仍为`review_required`。

| 指标 | 209父参照 | 525部分控制 |
|---|---:|---:|
| 主130 MAE / TAE |3.238462 / 421|3.038462 / 395|
| 主130 SSE |5131|5039|
| 主130最大误差 |21|26|
| 初始化52 TAE / SSE |136 / 1894|97 / 1117|
| 全182 TAE / SSE |557 / 7025|492 / 6156|
| 派生40站点MAE |9.875|9.725|

总体误差下降，但最大误差从21升至26，119 Z5主窗TAE从79升至108。最坏主窗为119 Z5的11700–12600秒：真实66、原生40。完整130/52/182/40行、208原生E1区间及逐字段signed/TAE/SSE保留。原始full状态仍为`diagnostic_measurement_complete`，没有把中间改善写成标定完成或现场认证。

唯一改动是266197525上734方向四条movement解除独立信号绑定，成为原生minor状态`m`。住宅734lane0→270和三条primary续行全部保留。节点仍是`traffic_light`，Alsterufer机动车与自行车两条counter movement保留原TL0/1、状态`O`及冲突优先关系。没有删除支路、改全绿、设置`pass=true`或关闭keepClear。全部道路、普通XML、几何、长度、速度、宽度、逐片段权限与换道属性、turn keys、全部内嵌程序和外部控制原字节/语义均受保护。8640完整需求、出发、路线、stop、field、loops及130+52真实观测不变。

这里`O`只指network的priority/off状态，不代表运行时恒为`O`；相同programme XML也不代表实际感应时序相同，解绑可改变counter的感应历史。

来源审查没有找到当前525前734方向的独立停止线证据，但这不能证明现场没有信号。保留的counter控制可能代表已裁掉的上游入口门控，自行车SG也仍有识别缺口。2022官方MAP/KML不能自动证明2026-10-19配置或SPaT。模型采用部分控制假设，并保留这些限制；未恢复不等价的旧V1整节点。

v1只解绑后，native将三条primary内部路径自动拆段，`cont`从0变为1，因此被静态门拒绝。失败输出和a609旧实现保留。v2仅对这三条连接在plain输入中显式设置`contPos=0`，保护源原有单链等待结构。最终native六条movement的response、foes及cont按完整movement重新映射后与源完全相同；没有覆盖请求位、via、state或曲线。v2实际实现32bb、原输入、18项scope负例及独立静态审查保留。

小车证据共6次调用、96车次，全部到达且零安全事件。汽车与自行车的让行机制分别来自不同时间表：car-only timing3和bike-only timing1，不能合写为一次共同episode。原native raw、旧`review_required`审计和正确解码后的记录均保留。Libsumo NextLinks元组曾被按TraCI顺序解码，decoder证据保留实际顺序及更正；它没有改驾驶输入或原生结果。小车试验只支持有界路径与让行机制，不证明现场控制、普遍安全或饱和容量。

完整运行前，root的`review_raw_smoke.py`与`reviewed-smoke-binding.json`直接绑定6项已完成raw及静态/API证据。后补的详细episode和family-final只作为解释，未倒写成full前置依据。full前还发现wrapper与父脚本同名会覆盖快照，修正在任何full执行前完成。未执行旧wrapper/scope保存在`full/pre-execution-driver-correction`，两个实际driver分别保留，preflight各自绑定。

本包收三个525来源审查、decoder、v1失败/v2构建、小车全部raw、唯一full及独立审查。与[209父包](../hamburg_ring1_209_ordinal_connection_20261024_v1/README.md)相同的字节按固定SHA引用。gzip解压后保持原字节。大full instant/queues只保留本地路径、大小和SHA，完整计数、summary、tripinfo、vehroute和compact贡献可迁移。本次冻结没有额外SUMO调用，也没有新增公共工具。

离线核验不会启动SUMO：

```powershell
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_266197525_partial_control_20261024_v1/reproduce.py --mode verify
```

显式完整原生复现必须使用新目录：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = (Join-Path $PWD '.venv/Lib/site-packages') + ';' + (Join-Path $PWD 'plugins/astra-sumo/src')
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_266197525_partial_control_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-525-partial-control
```

入口核项目SUMO/Libsumo1.27.1、binary/pyd与执行core哈希，直接读取冻结显式需求，不重建OD。运行使用0.1秒、seed104及原四处bodyhold。复现同一结果不表示主130已精确或现场SPaT已知。
