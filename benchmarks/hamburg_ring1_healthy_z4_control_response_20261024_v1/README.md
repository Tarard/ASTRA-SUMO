# 健康Z4基准与单车候选的被动TLS记录

本包按当地24日命名；源study的23日目录名及原始字节不改。只有两次原样只读record，没有新需求、冻结控制或健康候选提升。reference仍是健康3.7153846基准；one-car仍是被拒绝的4.0923077候选，8634辆到达、6辆在途。没有把记录通过当作候选接受或标定完成。

两份均20/20 checks通过：各208行E1全属性、144000步summary（只排CPU duration）、全部instant事件、到达完整route/exitTimes、tripinfo、driver hold及期末状态与原运行一致。两份50个实际programme和SwitchStates原始XML均gzip保存，每个programme恰覆盖14400秒并与switch颜色时间线对应。原来源输入和旧完整native固定引用42a827e的GS包及f0934b3/cae62ad的batch-1包，不重复源network/demand。大instant/queues仅本地哈希；迁机后无这些大文件就不能逐事件重读其原件，不能把摘要当原始数据。

首次全state向量颜色差为8943.0秒、18653534，仅pedestrian crossing idx22/24由r→G，idx0–20字符均未变，passenger/motor可用位也未变；不能称此刻车行绿改变。首个已证明passenger/motor可用位颜色差为8948.0秒，同一TLS的idx1/2/4/12/13/15/16由y→g/G，晚于首次traffic差8247.6秒700.4秒。全向量有18个actuated控制器有差，已证明passenger位差为17个；10个static颜色和program历史均相同，全部50个programID均未不同。readme_fixed_A只是programID名，不等于static。phase只在真实emit时刻有值；8951.0秒是共同emit事件中的首phase差，不能当完整phase历史首差或前填。

独立movement分析作为原74项之外的新来源单列：431控制位中59个taxonomy unknown，58位无绑定/未解决且可用性保留空值；另1位虽然类别unknown，passenger明确true，未把unknown当不可用。类别来自当前模型完整通路权限，说明允许类，不是该刻车辆实际通过或现场SPaT，也不证明全部计数损失来自信号。

逐TLS、900秒分段、颜色差区间、共同emit phase差及原脚本/计划/准备失败在study-source按原字节留档。control-inventory含原5文件与其原manifest；source-manifests明确原顶清单74项只含inventory的4数据文件，后来独立封口的inventory manifest（659cdd…）另计，未假称它属于原74。

离线核验：项目`.venv/Scripts/python.exe benchmarks/hamburg_ring1_healthy_z4_control_response_20261024_v1/freeze_evidence.py --verify-only`，会从原始50TLS导出/switches重算比较并逐CSV核对，不启动SUMO。`reproduce.py --output-dir <新目录>`仅离线复算全向量与movement两层比较；原record脚本也保留，但完整原生20check复跑还需按固定引用重定位源输入及本地instant原件。独立movement原manifest及所有8文件见movement-control-difference；c1333507…单独绑定，不伪称属于原74。未纳入任何fixed-history试验。

加入运动类别分析前的草稿索引与上下文保留在pre-movement-snapshot。原74项源数据未改；全向量与passenger位的说明在此独立细化。
