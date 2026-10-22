# 有限次原生计数反馈校正

状态：第一次完整原生重放已完成，候选因积压增加被拒绝。第二次按原定上限减半检验，记录更新时仍在运行。
第一次只把字段总绝对误差从1685降至1681，但未进入车辆从644增至796，在途从306增至319。
字段完全一致行从31降至30，派生站点完全一致行从7降至6。碰撞和传送均为零，仍不是有效标定。
全部[130个字段残差](trial-1/field-comparison.csv)、[40个派生站点残差](trial-1/station-comparison.csv)、[结果](trial-1/results.json)和[原生文件校验值](trial-1/manifest.json)均保留。

本项沿用[完整参考](../hamburg_ring1_primary_entry_full_20261022_v1/README.md)的网络、控制、全部路线、车道意图及130个真实计数目标。
不采用[控制归属核查](../hamburg_ring1_control_domain_sources_20261022_v1/README.md)中没有对应依据的撤灯建议。

每次只移动已在原生E1中完整通过的车辆。
从有多余贡献的时段，或主窗后的实际通过车辆中，选择能移向缺少贡献时段的候选。
每个字段最多移动8辆，每辆最多移动900秒，并保留请求在仿真步内的原有相位。
初始化请求保持不变。未观测到通过的车辆保持原请求，没有补零或猜测通过时刻。
首个准备候选移动82辆，请求变化范围为提前647秒至延后116.5秒。

旧运行的通知延误只用于提议，不作为改变需求后的计数预测。
每个候选都从零时刻连续运行至14400秒，重新读取全部130个原生nVehContrib，并报告40个字段和。
固定最多两次原生运行。若首次没有改善，第二次把请求变化减半。
若首次改善，第二次使用新的原生结果重新提议。
总绝对计数误差须严格下降，未进入数量及未进入加在途总量均不得增加，才保留为下一次提议的候选。
不隐藏任何变差字段。碰撞、传送、丢弃、未完整测量或全体静止会停止本轮。

“保留候选”不代表有效标定。仍须全部目标一致，且满足完整原生运行的有效性要求。
两次尝试未达零，只能说明本次有限校正未达目标，不能证明模型或现场不可达。
该规则只调整请求时刻，不解决未确定的控制归属、现场自然驾驶或人口识别问题。

执行：[run-feedback.py](run-feedback.py)。
提议与输入：[native_temporal_feedback.py](../../plugins/astra-sumo/src/astra_sumo/core/native_temporal_feedback.py)、[hamburg_departure_probe.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_departure_probe.py)。
原生循环：[hamburg_readme_pipeline.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_pipeline.py)。
测量与有效性：[detector_demand_replay.py](../../plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py)。
全部残差和图像：[hamburg_readme_results.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)。
目标运行目录为`outputs/ring1-native-count-feedback-20261022-v2/`，独立的执行记录目录保留实际源码与版本。
使用仓库`.venv`及SUMO、TraCI、sumolib 1.27.1。没有保存状态续跑。
