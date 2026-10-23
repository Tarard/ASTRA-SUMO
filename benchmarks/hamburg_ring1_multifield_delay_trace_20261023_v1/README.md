# 汉堡单对替换后的观察记录

本包只保存一次同输入观察回放，没有新增需求候选或控制处理，也没有字段或MAE改善主张。源为前包substitute-1pair（提交4b3cb18）：MAE5.092308、SSE6964，未入网27辆、在途16辆，原判定rejected继续保留。主130项零误差目标未达到。

全部12项一致性检查通过：208行E1、144000个交通步骤（只排除CPU duration）、235746条instant事件及8596辆已到达车辆的完整路线/退出时刻与源运行一致。记录耗时263.9078575秒；0.1秒步长、14400秒、seed104和原libsumo 1.27.1驾驶逻辑保持。完整44车声明包含保留双断面车与43个末端未完成请求；其中27辆未插入，没有FCD位置。实际FCD含17辆，每1秒采样并保留200米leader信息。

焦点车已较早选到正确的合法车道链，随后排在未修改的535Z4前车后。实际许可左转窗口与轨迹支持“旧through退出时刻未包含新左转队列服务顺序”的诊断；第一字段hold已释放。该记录不支持继续添加提前换道waypoint，也不能单靠leader与许可绿灯推定完整动态冲突或前车等待原因。完整换道、车身hold、三个服务窗口及TLS差异见trace-v2/independent-analysis/README.md.gz。1秒FCD不是连续轨迹，模型信号也不是现场SPaT。

next-diagnosis保留观察前的窄诊断，其关于缺少FCD/TLS记录的历史表述由本次观察补充。receiving-control-source-audit按原字节保存，仍为review_required：未证实197是重复或独立现场控制。programID为readme_fixed_A不改变其actuated类型；现有航片元数据的不一致也原样保留，不据此删除、合并或修改信号。

完整FCD、lanechanges、51TLS实际state及51份程序、observer请求与原脚本、原生E1/summary/trips/routes/collisions/日志均压缩保留。queues仅列本机哈希；完整instant已入包并以逐事件属性摘要绑定前包本机原始文件。最初cohort准备断言在SUMO启动前失败，日志单列failed-preparation，计0次原生运行。独立分析的review_required与观察一致性pass分开保留。

离线核验不启动SUMO：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_delay_trace_20261023_v1/freeze_evidence.py --verify-only`。

只有显式调用reproduce.py并指定新output-dir才运行一次同输入观察：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_delay_trace_20261023_v1/reproduce.py --output-dir outputs/multifield-delay-trace-reproduction`。入口使用冻结observer与前包固定输入，仅重定位文件路径，不恢复历史绝对目录、不生成OD或改控制。观察程序导出只用于本次记录，不外推14400秒之后的状态。

前包manifest及源文件哈希、失败和分析记录均保留。OSM：© OpenStreetMap contributors，ODbL 1.0。
