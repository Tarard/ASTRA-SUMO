# 汉堡计数标定：经典文献与方法复核

日期：2026-10-23。本文依据用户要求，优先核查1984—2008年的经典交通标定研究。检索与资料整理使用AI辅助，书目、阅读范围和项目判断分别记录。本次文献复核没有启动新仿真。当前目标仍是13个字段、10个时段的130项原生E1 `nVehContrib`全部零残差，并通过运行有效性检查。

## 已核查的文献

| 文献 | 实际阅读范围 | 对本例的作用 |
|---|---|---|
| Cascetta, E. (1984). *Estimation of trip matrices from traffic counts and survey data: A generalized least squares estimator*. Transportation Research Part B, 18(4–5), 289–299. [DOI](https://doi.org/10.1016/0191-2615(84)90012-2) | 出版书目、摘要及[作者机构记录](https://www.iris.unina.it/handle/11588/367064)。未据正文推导公式。 | 用分配模型联系OD与计数，并显式处理先验、观测误差及模型设定误差。 |
| Cascetta, E., Inaudi, D., & Marquis, G. (1993). *Dynamic Estimators of Origin-Destination Matrices Using Traffic Counts*. Transportation Science, 27(4), 363–373. [出版社](https://pubsonline.informs.org/doi/10.1287/trsc.27.4.363) | 出版社摘要与书目，正文受限。 | 同时或顺序估计时变OD；不把检测器通过时段直接等同于出发时段。 |
| Toledo, T., Ben-Akiva, M. E., Darda, D., Jha, M., & Koutsopoulos, H. N. (2004). *Calibration of Microscopic Traffic Simulation Models with Aggregate Data*. Transportation Research Record, 1876, 10–19. [DOI](https://doi.org/10.3141/1876-02)；[作者大学全文](https://toledo.net.technion.ac.il/files/2012/12/TRR_AggreagteCalibration_04.pdf) | 正文：印刷页11–13的方法与分配反馈，页14的参数筛选，页15–17的案例。 | 按参数组分步估计，同时保留反馈；分配关系随路线选择和行程时间更新。估计顺序可以调整。 |
| Balakrishna, R., Antoniou, C., Ben-Akiva, M., Koutsopoulos, H. N., & Wen, Y. (2007). *Calibration of Microscopic Traffic Simulation Models: Methods and Application*. Transportation Research Record, 1999, 198–207. [DOI](https://doi.org/10.3141/1999-21)；[共同作者上传稿](https://www.researchgate.net/publication/277402098_Calibration_of_Microscopic_Traffic_Simulation_Models_Methods_and_Application) | 2007-04-03最终投稿稿，非出版社排版版。稿页4–5的问题定义，页6的求解，页10的实际估计范围；上传者为共同作者Constantinos Antoniou。 | 以仿真返回的全网测量构造标定目标。一般框架可联合估计，但实际案例因资料有限，只估计时变OD，驾驶参数沿用既有研究并检验敏感性。 |
| Hollander, Y., & Liu, R. (2008). *The principles of calibrating traffic microsimulation models*. Transportation, 35, 347–362. [出版社](https://doi.org/10.1007/s11116-007-9156-2) | 本次仅核出版社摘要与书目；不将此前记录中的全文阅读替代本次核读。 | 明确标定范围、拟合度、重复运行及独立验证的角色。 |

两篇已核读正文的微观标定论文最适合作为本例主线。2004年论文讨论需求、路线选择和驾驶参数之间的反馈；2007年论文展示在资料有限时，将实际估计范围收敛到时变OD的做法。论文中的多日合并、平滑或评价阈值不直接移植到汉堡；本例保留原来的130项真实计数及零残差验收。

2004年论文还要求习惯行程时间与运行产生的行程时间一致，并建议用多次仿真估计期望分配。其观测日条件不表示日内需求恒定。本例借用反馈框架，尚未验证这些假设对单日整数计数及当前感应控制的适用范围。

## 当前方法的问题

道路及碰撞修复已有独立证据。后续16、32、64、107辆的时刻批次却全部使原生误差变差，见[完整试验记录](../../benchmarks/hamburg_ring1_route_timing_feedback_20261023_v1/README.md)。这些批次利用一次仿真的通过时刻生成候选，没有实施SPSA、有限差分梯度或294维OD联合求解，不能据此判断那些方法失败。

当前的主要方法偏差，是把一次运行的出发—通过时间关系用于预测改变需求后的计数。调整会改变排队、车辆相互影响和感应控制状态。固定信号历史只能隔离其中一部分作用，仍不能使行程时间成为常数。2004年论文要求反馈更新分配关系；这一点比继续增加逐车平移次数更直接地对应本例。

以下是本例的数学说明，不是从尚未取得的论文中转录的公式。令x表示OD出发时段需求，A(x)描述这些车辆对检测器通过时段的分配。一次运行可提供A(x)的描述，但它不等于需求的边际响应。即使写成F(x)=A(x)x，需求变化时仍有分配关系变化的影响；不能将当前分配比例直接称为F的导数。

主目标130项、OD变量294个。局部连续近似的秩至多130，但非负边界、整数限制和先验还会影响可行集。这说明需要检验可辨识性及先验，不能据此断言整数解必不唯一，更不能断言零残差不可行。40项站点值是字段求和，不增加独立信息；52项初始化真实计数可另行约束和报告。

## 后续采用的标定主线

采用“时变OD估计—动态分配反馈—完整原生检验”的主线。先从当前经过运行核验的网络及显式需求重建OD初值，确保初值保留当前路线、发车时刻和字段意图。旧OD模板中的道路尾段不得覆盖新的修复。路线份额和车道分配继续标为先验，不冒充真实OD。

先检查需求变量与观测之间的对应及可调整方向，再做少量OD组的原生扰动核验。保留初始化、窗后通过、未进入和未通过的车辆；一次分配表只用于描述或产生候选。更新是否有效，仍由完整130项原生结果判断。

标准SPSA可保留为有文献依据的求解基准，而不是把旧时移算法换个名称。现有实现的取整和分位数重采样会同时影响车辆数、出发时刻与模板组成，必须先明确这些变化属于哪个参数。Spall的光滑随机逼近条件不能直接作为该离散生成过程的收敛证明。参见[Spall 1998实现原文](https://www.jhuapl.edu/spsa/PDF-SPSA/Spall_Implementation_of_the_Simultaneous.PDF)及[仓库原有方法核对](hamburg-spsa-od-method-2026-10-22-v1.md)。

信号控制方式需作为模型条件明确保存。当前从仿真录制的固定历史仅为条件对照，不是现场SPaT；其相同需求自重放已验证，但改变需求后的结果尚未验证。它不能被静默替换成现场控制事实，也不能消除需求引起的排队变化。

原生130项计数误差是主要拟合对象，40项派生站点值不重复进入目标。OD先验的权重、范围或择解作用须单列，避免把计数误差与先验的折中隐藏成“已经归零”。任何代理方程、分配表或优化器停止条件，都不能替代原生MAE=0、最大误差=0和运行有效性检查。

经典文献提供问题定义与可复核的求解框架，并不保证本例有限次运行达到零误差。这里保留MAE=0作为用户要求的验收目标；既不提前降低目标，也不将尚未得到精确解写成已经证明不可行。
