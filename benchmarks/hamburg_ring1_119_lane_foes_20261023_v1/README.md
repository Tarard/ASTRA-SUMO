# 119接收段lane-level foes单因素对照

本包只冻结GS266既有priority条件模型中的lane-level foe精度因素。基准为42a827e冻结的GS包（主MAE3.7153846，8640辆健康完成），旧observer、source-audit和full-priority均按固定包及哈希引用，不重复复制。

原生转换开启check-lane-foes.all，仅改变GS的6行ROW请求表和790 lane1→186 lanes2/3两项m→M。控制原始字节、全部几何/长度/道路/权限/合法转向、8640需求与意图、真实130主窗和52初始化计数不变。保留3个真实共享出口/交叉对(0,2)/(1,2)/(1,3)，排除其余5个跨入口对；该精度因素仍继承独立GS控制归属及603优先的未知现场依据，不是现场SPaT。

原生记录原判定 `diagnostic_candidate`，本轮选用结论为**不采用**；保留健康的3.7153846基准。主130项MAE由 3.7153846153846155 变为 5.938461538461539，SSE由 6709 变为 14184。初始化52项TAE由 105 变为 61；全182项TAE由 588 变为 833。waiting/running为 61/94，535指定56项TAE为 0。完整130/40/52/182/208、每字段退步及未完成车辆都保留。初始化改善和无碰撞不能代替主130、全182及未完成情况的整体判定。本配对输入和控制下结果恶化；不据此断言物理选项错误或零误差不可行。

静态CSV保留全部15对，其中8对跨入口。三次共6车smoke只按封存README解释：Z5/603车身同时处于GS范围的不同车道，不是车身重叠；REQ0/3尚未覆盖同时内部占用，只证明这次opened/foe状态和安全到达。RQ5、反向到达、饱和tiny仍未覆盖，不能外推全场景安全。历史静态分析中的未构造/未测字样属于当时阶段，不改写为后续结果。

源码依赖及基础builder按固定哈希引用旧包；新增builder、candidate、说明、微试验和本次完整输入/关键native/独立audit按原字节压缩。大queues/instant等仅local-only哈希，不以哈希代替逐事件数据；完整贡献JSON保留。原始绝对路径只作溯源。离线verify和7项测试不启动SUMO。显式reproduce.py需新output-dir、同仓库固定包、项目.venv/Libsumo1.27.1及固定源实现；只复跑本候选，不生成OD或另改控制。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_119_lane_foes_20261023_v1/freeze_evidence.py --verify-only`。
