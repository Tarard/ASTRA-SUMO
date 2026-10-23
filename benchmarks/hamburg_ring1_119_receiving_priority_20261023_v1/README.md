# 119接收段GS266条件priority对照

本包只冻结一个GS控制归属假设链：来源审查、7200秒只读前缀、两车机制比较、保留六movement的两次共四车合流smoke、v1/v2范围失败与v3、首次准备错误和一次完整8640需求对照。没有把独立推断控制的改变称为现场修复。独立GS灯头/停线、603优先/790让行的现场依据仍未知；保留119及上游真实信号、全部道路和长度、六个合法movement及权限。

完整运行原判定 `diagnostic_candidate`；主130项MAE由 4.653846153846154 变为 3.7153846153846155、SSE由 9473 变为 6709，全182项TAE由 683 变为 588。初始化52项TAE由 78 增至 105，该退步随结果保留。119 Z4主窗TAE也由91增至128，未隐藏该字段恶化。waiting/running分别为 0/0，535指定四字段全部56项TAE 0。完整130/40/52/182/208行、全部终端车辆和事故信息均保存，不按单一改善选用。正式零误差与健康完成不能由来源假设或smoke通过替代。

前缀13项一致性检查通过，502帧原生getter JSONL按原字节gzip入Git。getter标时、原生XML标时、opened/foe/priority与实际储车能力的边界见analysis与原生源码记录。没有私有DriveProcessItem观测，不把绿灯与停车单独当作根因证明。大queues、instant、TLS全记录及FCD只存local-only原始哈希，未删除本地原件；不能把本地哈希当逐样本数据。

本包依赖同仓库已冻结197包；原始源输入通过source-references绑定。全部当前输入和必要原生输出均按原字节压缩。历史绝对路径只作溯源。离线核验用项目 `.venv/Scripts/python.exe benchmarks/hamburg_ring1_119_receiving_priority_20261023_v1/freeze_evidence.py --verify-only`。冻结和测试不启动SUMO。仅显式运行reproduce.py并给全新output-dir才复测一次完整原生；它校验项目.venv、固定实现/Libsumo1.27.1及源哈希，不再生成或调整OD、不改变候选/相位。
