# 119协调组公共相位原点：三个完整原生响应

本包保留三个新14400秒原生运行，均以原健康full-priority输入为源，只共同改变`LSA119_part0`与`6390683126`的offset。+30直接使用原源，不叠加+15需求或控制。8640显式请求、路线、stop、field、数量、网络、真实130+52观测和四处bodyhold不变；相位状态、时长、类型和两控制器间相对时序不变。没有重采样或新需求生成。

| 公共offset | 主130 MAE / SSE / TAE | 初始52 TAE | 全182 TAE | 完成情况 | 原判定 |
|---|---|---:|---:|---|---|
| 原源0 |3.715385 / 6709 / 483|105|588|8640到达|原offset=0参考|
| +15 |3.538462 / 5818 / 460|168|628|8640到达|rejected，仅182门失败|
| −15（XML75） |4.630769 / 9974 / 602|110|712|27等待、62在途|rejected|
| +30 |3.569231 / 6942 / 464|91|555|8640到达|rejected，仅SSE门失败|

三次均零碰撞/传送/discard、无重复或额外字段，535指定56行仍全部零。40派生站点MAE依次为原源10.975、+15为10.55、−15为13.1、+30为10.55。全部130/52/182/208、完成情况、逐字段TAE和signed残差、原8门及独立核对均保留。audit pass只表示对账通过，不等于候选被采用。

+15是当前实测健康主窗误差最低点，同时初始化更差；它是有条件的Pareto点，不能把原rejected改成原判据已通过。+30初始化与全182较低，但主窗SSE高于原源，原rejected也不改。原offset=0参考3.715、最低主窗健康+15及+30的取舍分列，不自动promotion。原8门保留为本轮预设条件，不能替代用户主130归零目标；没有主130零误差，也不从这些单seed有限点推断平滑、最优或全局可达性。

官方119/SP1配置支持TU90和源相对`SignalzeitenVersatz=0`，并不确证计数日期的绝对灯色。当地午夜周期零点及Z15/K9物理映射仍为声明。仅这两个119来源投影控制器共同平移，保留当前K9→K2及反向间隔；不能因此证明现场协调或普遍安全。网络内另存的95秒program0不能混作外部90秒程序。本研究未新增逐时刻getProgram/getPhase历史，不是field SPaT。原GS priority、197优先关系及lane先验的现场不确定性仍在。

offset-response-analysis按其原封口只分析−15/0/+15，不读后来+30；其“下一次检验”描述是当时记录。四点表额外列入+30实测，不覆盖旧分析。source-control来源、两独立reader、两runner及执行预检按原字节保存；共用network/demand/intents/targets/core按固定旧包SHA引用。三个完整instant和queues仅本地大小/SHA，compact完整贡献保存。未纳入后续fixture或其他方向。

离线核验：`.venv\Scripts\python.exe benchmarks/hamburg_ring1_119_common_offset_20261024_v1/reproduce.py --mode verify`，不启动SUMO。

显式完整原生复现须从仓库根目录先设置本项目环境，避免ambient SUMO_HOME/PYTHONPATH导入系统SUMO。裸venv命令不能保证实际模块来源：

```powershell
$env:PYTHONPATH = Join-Path $PWD 'plugins/astra-sumo/src'
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_119_common_offset_20261024_v1/reproduce.py --mode native-full --case plus15 --output-dir outputs/reproduce-119-offset-plus15
```

入口在任何native调用前检查并记录实际libsumo/traci/sumolib路径、1.27.1版本、二进制及core哈希，拒绝项目.venv外的模块；失败路径也保留。case可选plus15/minus15/plus30；必须新目录。输入从冻结包解压，按当前仓库路径调用core，不生成OD、不依赖历史outputs绝对路径。本次冻结未新增SUMO；离线核验不等于重新原生复现，复现rejected结果不表示标定完成。
