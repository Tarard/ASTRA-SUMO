# 首13初始化计数归零与完整运行回退

本包保留六次已完成原生调用：5次0–900秒prefix及唯一14400秒full验证。首13行零误差只属于初始化第一窗，不能写成130主窗归零。full原判rejected：主130 MAE5.46923077、TAE711、SSE11917、max31；52初始化TAE86/SSE1088、全182TAE797、40派生MAE15.675。8594插入、8507到达、87在途、46等待，零碰撞/传送/discard，8522完整事件，535指定56行仍零。全局最好主MAE3.7153846不变。

| Prefix | 首13 TAE | SSE | 范围 |
|---|---:|---:|---|
| reference |22|234|13初始化行|
| advance22 |22|244|13初始化行|
| advance225 |7|17|13初始化行|
| advance-first900 |7|25|13初始化行|
| advance-first900-120225 |0|0|13初始化行|

每个prefix保留全部8640未来请求和全部130+52真实观测；169个未测值显式null，不填0，也不计算未测主130成绩。短时核心的完整人口完成状态不适用于prefix；18项原生/安全/贡献检查另列，不把正常未出发或跨900秒车辆叫作失败。全部未归零候选保留。实际执行有4种脚本字节：三份旧127/577/cd09及最后303，历史版本不会被当前checker覆盖。

434辆最终候选的首13实测零、434唯一预期贡献、无unsafe。完整运行从同一输入由0连续执行，13全E1属性与9000条非CPU summary的5项原parity均过；独立审计另保留事件、到达及hold语义的7项parity。然而后续主窗和完成情况回退，133未完成均为未改8206车辆。不能把前缀分窗独立优化或用旧lag预测未来。初始化累计900/1800/3600、全部130/40/52/182/208、未完成ID、cohort及两套reference gates完整保存。

service-evidence保留首900漏1/8/13个119 Z11/Z12/Z13贡献的原生服务诊断；stock-evidence保留182项计划库存审计及仅4项不符表。请求departure库存不是实际passage计数，不据此推断观测错误或零可行性。控制、OD与lane先验仍是条件模型，不是现场SPaT。未包括新的1800秒分支或任何未完成方向。

离线核验：`.venv\Scripts\python.exe benchmarks/hamburg_ring1_causal_initial_prefix_20261024_v1/reproduce.py --mode verify`，不启动SUMO。

显式重新原生运行：同项目.venv及记录的Libsumo/SUMO1.27.1实现下，运行`.venv\Scripts\python.exe benchmarks/hamburg_ring1_causal_initial_prefix_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-causal-initial-full`。它从本包及固定旧包解压显式输入，以现仓库路径调用core，不依赖历史outputs绝对路径、不生成OD。必须使用新目录，重新保存完整原生及误差对照。该模式供另行显式执行；本次冻结没有额外SUMO。离线检查不等于重新原生复现。

5份短prefix instant均gzip保存，full instant及所有queues仅localhash；完整贡献JSON保存。缺原大文件不能迁机重读旧full逐事件轨迹。重复source按固定包/SHA引用，原始绝对路径仅溯源。初始13零与full失败、有效性与采用、prefix与主130始终分开。
