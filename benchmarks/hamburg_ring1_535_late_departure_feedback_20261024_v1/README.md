# 535晚期出发反馈：五次完整原生检验

本组最低已测健康主130 MAE为**2.807692308**，TAE365、SSE4117、最大误差23。它来自同29辆请求相对[525健康源](../hamburg_ring1_266197525_partial_control_20261024_v1/README.md)统一提前30秒的完整运行。8640辆全部插入并到达，零等待、在途、碰撞、传送和discard；完整贡献均为唯一预期字段，无重复或额外字段。主130仍未归零。

| 完整运行 | 主130 MAE / TAE | SSE / 最大误差 | 全182 TAE | 派生40站点MAE |
|---|---:|---:|---:|---:|
| 525源 |3.038462 / 395|5039 / 26|492|9.725|
| 压缩β≈0.556156 |3.076923 / 400|5134 / 26|497|9.75|
| −30秒 |2.807692 / 365|4117 / 23|462|8.975|
| +30秒 |3.223077 / 419|5641 / 25|516|10.225|
| −60秒 |2.853846 / 371|4245 / 23|468|9.075|
| −90秒 |2.838462 / 369|4195 / 23|466|9.025|

五个新run均健康。初始化52行保持同源，TAE97、SSE1117；535 Z5–Z8指定56行均为零。156个截至10800秒的原生E1区间全部属性同源，不等于全部车辆轨迹或summary前缀相同。原始verdict与全部未采用点保留。−30仅是这组最低已测点；这些有限单seed结果不证明单调、收敛、最优或容量上限。

所有候选均独立从TAE395源出发，不在失败压缩或−30输入上叠加。选中ID为`od_0256_v00000`至`od_0256_v00028`，均为原535 Z4请求。压缩以10800秒为锚点改变29辆间距；四项普通shift保持它们的原headway。另8611辆、全部ID/route/stop/field/vType、网络、控制、驾驶规则、loops及130+52真实观测保持。出发时段不等于检测通过时段，旧lag投影不能替代新原生计数。

−60/−90中只有v0实际跨请求bin，metadata的`od_cell`由256改为235。实际294库存为235列18→19、256列29→28，L1为2。其他三候选不跨bin。全8640、21OD总量及field总量不变，不能把两项extension叫same-q。准备时的代码风格与描述修订、原执行代码、source-snapshot映射、双driver及所有输入hash均保留，没有将后来的live文件当作旧执行来源。

压缩的旧lag代理预测29辆都提前进入前格，实际只有5辆；原14辆按时车有9辆变晚，原15辆carry仍晚。完整own/other数量账还显示未改车辆的跨窗与其他字段误差变化。相邻普通road出口时刻分解定位不同车辆在LSA200及205069138前后的新增耗时，但exit-to-exit包含internal连接和接收道路，不是纯edge停车时间，也不证明某盏灯是唯一原因。其余offset与extension的逐车、逐窗、208加法账均保留。这些是已完成run的解释，没有新增控制或路线候选。

本统一包收后补的parent residual诊断、compression decomposition/exit-localization、offset独立审查和分解、extension独立审查和分解，以及五个完整run。每项均有130/52/182/40、208 E1、53556、summary、tripinfo、vehroute、完整compact贡献、警告、安全和runtime证据。后补分析不倒写为原候选的前置依据。尾窗没有虚填真实目标零。后续数量试验不属于本包。

与525父包相同的字节按固定SHA引用；gzip解压后保持原字节。十个大instant/queues只留本地路径、大小和SHA。冻结与离线测试没有新SUMO调用，也没有新增公共工具。现场OD、控制与日期适用仍未被拟合结果认证。

离线核验默认不启动SUMO：

```powershell
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_535_late_departure_feedback_20261024_v1/reproduce.py --mode verify
```

显式复现只运行指定的一项，必须使用新目录：

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = (Join-Path $PWD '.venv/Lib/site-packages') + ';' + (Join-Path $PWD 'plugins/astra-sumo/src')
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_535_late_departure_feedback_20261024_v1/reproduce.py --mode native-full --case minus30 --output-dir outputs/reproduce-535-minus30
```

`--case`也接受compress29、plus30、minus60、minus90。入口核项目SUMO/Libsumo1.27.1、binary/pyd与执行core哈希，直接读取冻结需求，不重建OD。固定0.1秒、seed104和原四处bodyhold。复现同一结果不表示标定已完成。
