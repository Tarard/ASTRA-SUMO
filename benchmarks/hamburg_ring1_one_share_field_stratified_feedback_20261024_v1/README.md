# 保字段的OD时段方向：同源正负两侧原生对照

搜索参考是a889d72的健康one-share（主MAE3.9384615、8640全到达），全局最好对照仍是42a827e的3.7153846。本包只保存同一方向的plus/minus两次完整原生运行。search-reference改善与global-best改善分别保留8项gates；仅胜3.938不能称超过3.715。audit pass不等于候选采用或标定完成。

| 侧 | 原始判定 | 主130 MAE / SSE / TAE | 初始化52 TAE | 全182 TAE | 派生40 MAE | 到达/等待/在途 |
|---|---|---|---:|---:|---:|---|
| plus | rejected | 5.1 / 13133 / 663 | 105 | 768 | 14.475 | 8542/9/89 |
| minus | rejected | 5.130769230769231 / 11631 / 667 | 105 | 772 | 14.725 | 8543/0/97 |

当前8640实例按29个(OD,field)×14时段分成406列。C182×406由source实际完整贡献重建，原生208行完整对账；其中241个尾部事件没有真实targets，不填假零。130/182行数值秩为130/182，均只是同次经验分配诊断。C/q不是Jacobian，满行秩既不识别406个未知量，也不证明整数、守恒和初始化限制下零误差可行。

只求一个19列、L1=32方向，两侧各改16辆depart，保持29组、21OD、13field总量，固定1953初始化请求与其余8624辆XML/意向及相对顺序。逐field调用原stable helper，使优化分组和实际field约束一致；不重新生成OD。metadata od_cell保持实际294索引，406只在单独vector406保存；v26新增Z5 stop/field未重置。

半径明确为max(1,floor(q×0.25))、下界截0，允许小q改1。源406 prior全正，plus有1个target列到0，minus无零target；没有把零target当零source。主130描述预测plus480/minus544，不能当原生结果。实际时移没有900秒限幅，最大6291.912秒。两侧各自完整130/40/52/182/208、健康、53556、重复/额外意图、执行实现和独立审计全保留。

两侧各自不同16辆，L1=32表示数量预算，不表示小时间扰动。paired-native208-response保留逐行半差(plus−minus)/2与对称偏差plus+minus−2source；这只是该有限方向的实测总响应，不称导数，也不作控制反馈的因果分离。两侧原判均rejected，搜索参考与全局最好对照均不变。

网络/控制/真实targets/探头/所有ID、route、stop、field及四bodyhold保持来源，Libsumo1.27.1/0.1s/seed104/14400秒一致。field先验和控制仍是条件模型，不是现场SPaT、真实OD或真实lane份额。预备阶段source README中的“未运行”是原样历史快照，不是终态。本包不包含后续方向或结果。

大型旧source按固定包/SHA引用，同字节snapshot由byte-references去重。大instant/queues仅localhash，完整compact贡献保存；缺原件不能迁机逐事件重读。freeze、verify及reproduce只离线核算，不启动SUMO，需同仓库固定包和项目.venv。未改公共docs或提交，未宣称130零误差。
