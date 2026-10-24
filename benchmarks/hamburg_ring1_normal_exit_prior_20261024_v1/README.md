# 882辆普通道路出口先验敏感性

本包只保存一份4车路线支持smoke与一次8640辆完整对照。full原判 **rejected**。主130 MAE/SSE/TAE为4.2615384615384615/9390/554，52TAE92、182TAE646、40派生MAE13.05，到达/等待/在途8638/0/2。完整130/40/52/182/208、健康、两套reference gates、全部882实际字段/出口到达和独立审计均保留。audit通过不等于现场先验正确或130零误差完成。

两辆期末仍在途的记录不称死锁或滞留：它们晚插入、已完成各自字段贡献并释放detector hold，未见active hold。期末halting/stopped均0、meanSpeed5.2是aggregate记录，不给出逐车精确速度。arrival=-1/vaporized=end表示SUMO在固定结束时刻输出未完成记录，不是人工删车；没有FCD就不推断其精确lane/TLS状态。

健康one-share源为3.9384615，全局对照最好仍3.7153846。仅把原882 parking-ending请求（535Z4=495、Z9=313、Z14=74）的最终28665128#0换成既有186821028→186821026→142944318，其余7758车辆XML不变；全体ID/depart/stops/fields/网络/控制/真实targets不变。逆向字符串替换恢复原需求字节。原生初始请求不改时刻，但172条初始化路线尾段也改变，不能宣称初始化交通不受影响。

142944318是现存paired road graph-cut，原源路仍续桥；不是现场真实trip终点或road-end证明。100%through是缺停车活动输入时的一项敏感性，真实OD/share未知。4prefix静态既有自然最短通路无repeat/extra、增加37.739348m，来源port/parking-lineage原字节保存；不以支持或拟合推断真实份额。OD destination实际改882辆，294坐标重排使332个metadata索引变化，其中251同时改route、81未改route仅重排。od-coordinate-map保留两套坐标，绝不称q不变或重新生成OD。

119替代出口审查只保留README、summary及原manifest作为限制引用，未复制大型lane-paths，也未选择其候选；不能把这份局部引用称为完整替代出口证据包。

smoke的4个既有主窗ID用原出发时刻，全部13字段和130主窗+52初始化真实targets原样保留。它只检验预期字段、142lane2到达与安全；calibration_selection_applicable=false。其计数误差没有标定意义，不合成四车假观测或当成拟合失败/成功。full计数则与全部真实观测独立重测。

preparation49文件manifest仅覆盖准备阶段。preparation-notes及两次native前reader适配失败（driver_scope字典、actual_changes键）保留原脚本/日志/partial-copy；它们native=0，prepared输入字节未改，不算原生失败。固定source和重复原件按SHA引用，原始路径只供溯源；大instant/queues localhash，完整compact贡献保留。verify/reproduce均离线，无新增SUMO，未写后续结果。
