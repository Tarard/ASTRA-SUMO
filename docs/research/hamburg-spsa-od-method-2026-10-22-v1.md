# Hamburg：OD 定义与标准 SPSA 核对

日期：2026-10-22。状态：原文已核读，标准 SPSA 与原生评价已接通。汉堡 SPSA 尚未迭代，精确标定尚未完成。

## 结论与当前问题

用户指出的 OD 定义问题成立。此前需求按检测字段和目标时段构造，再分配到路线。
这些车辆实际组成 21 个入口—终点对、294 个出发时段单元，但没有作为一个自由的时变 OD 向量联合估计。
这属于需求构造方法的局限。它还不能单独证明车辆总数过大，也不能解释全部残差。

本次只处理 OD 定义与 SPSA 方法。固定五个完整路口、236 条道路和全部 130 个真实计数目标。
40 个站点值仍由字段求和。原生 E1 的 `nVehContrib` 仍是唯一计数依据。
信号、几何与车道行为仍需分别说明。本次不再追加信号试参。

## 核读的原始资料

| 原文 | 核读位置、状态与用途 | 证据范围 |
|---|---|---|
| [Balakrishna, Antoniou 等，2007，TRR，DOI 10.3141/1999-21](https://doi.org/10.3141/1999-21)；[Antoniou 上传的全文](https://www.researchgate.net/publication/277402098_Calibration_of_Microscopic_Traffic_Simulation_Models_Methods_and_Application) | Methodology、Solution approaches、Case study。同行评议论文。T2-方法可信，E2-实验有限。采用时变 OD 定义及仿真测量关系。 | 以出发时段定义 OD，同时考虑需求与驾驶参数。案例有既有 OD 初值，观测还经过汇总、平滑等处理。汉堡不复制这些观测处理，也不删除任何目标。 |
| [Antoniou 等，2015，W–SPSA in practice，作者稿](https://web.mit.edu/cami/Public/CLA_WSPSAinPractice_ISTTT_2015_preprint.pdf)；[Procedia 正式记录](https://doi.org/10.1016/j.trpro.2015.06.013) | 第 2.1–2.4 节、两个案例和附录。T2-方法可信，E2-实验有限。采用普通 SPSA 与 OLS 这一已明示的简化情形。 | 普通 SPSA 与 W-SPSA 是不同方法。后者使用参数与测量之间的权重矩阵。当前代码没有把普通 SPSA 宣称为 W-SPSA。 |
| [Mahajan, Cantelmo, Antoniou，2023，作者提交稿](https://mediatum.ub.tum.de/doc/1701188/o6wl24dt4jm8erz53tllvu6nf.mahajan_2023.pdf)；[作者代码 ACTRYS](https://github.com/vishalmhjn/actrys) | 第 3.2、4.5、5.5–5.6、6.4 节和表 2。预印本，未将其列为已核实的期刊正式版。T2-方法参考，E2-实验有限。 | 真实慕尼黑情形用 450 处计数、5256 个 OD 对和 5 个一小时时段。采用中观 SUMO、W-SPSA 及需求和供给参数分步标定。最终总体 WAPE 为 0.32–0.37，并非零误差。 |
| [Spall，1998，An Overview…](https://www.jhuapl.edu/spsa/pdf-spsa/spall_an_overview.pdf) | 式 5–6、页 489–490 的步骤与 MATLAB 代码。T2-方法可信。当前普通 SPSA 更新的直接依据。 | 每轮梯度估计需要两次目标函数计算。初始评价、最终复测和重复实验另计。有限轮次不保证全局最优或精确计数。 |

2015 年的 Procedia 版本与相关 TRC 文章不能当成两份独立案例证据。
下载的作者稿标题页仍有排版占位信息。资料清单保存其散列，未把它冒充出版 PDF。
2023 年作者稿是“400 多处观测”这一比较的直接相关案例，但尚不能确定它就是用户所指论文。

## 应采用的数学定义

令 $x_{od,h}$ 表示在出发时段 $h$，从入口 $o$ 前往出口 $d$ 的出行数量。
按固定顺序堆叠这些数量，得到 $x=\operatorname{col}_{h,o,d}x_{od,h}$。
检测字段不是 OD 的索引。一个行程可以依次经过不同检测断面。

让固定道路、控制和已声明驾驶参数下的原生 SUMO 返回 $F(x)$。
比较窗口仍按检测器通过时间计数。不能把出发时段直接当成通过时段。
初始普通 SPSA 基线可采用 2015 年第 2.3 节的 OLS 简化形式：

$$L(x)=\sum_{j=1}^{130}(F_j(x)-y_j)^2,\qquad x\geq 0.$$

这项建议不加入未经验证的 OD 真值约束。若使用初始 OD，它只是起点。
若以后采用 OD 先验惩罚，必须说明先验来源及权重，不能将它藏在目标函数中。
站点求和不再进入目标函数，否则同一观测会被重复加权。

普通 SPSA 按 Spall 的一基下标写成：

$$a_k=\frac{a}{(A+k)^\alpha},\quad c_k=\frac{c}{k^\gamma},\quad
\Delta_{ki}\overset{\mathrm{iid}}\sim\{-1,+1\},$$

$$\widehat g_{ki}=\frac{L(\Pi(x_k+c_k\Delta_k))-L(\Pi(x_k-c_k\Delta_k))}{2c_k\Delta_{ki}},
\qquad x_{k+1}=\Pi(x_k-a_k\widehat g_k).$$

$\Pi$ 表示声明范围内的投影。扰动点按 2015 年步骤在运行仿真前投影。
分母保留标准公式，不换成投影后的坐标间距。一次扰动同时改变全部 OD 单元。
代码不使用逐字段增减车辆、挑选改善候选或整数方程代替这些更新。

已另外核读 [Spall，1998，Implementation…](https://www.jhuapl.edu/spsa/PDF-SPSA/Spall_Implementation_of_the_Simultaneous.PDF) 第 III 节及页 820 的增益选择说明。
其建议以 $\alpha=0.602,\gamma=0.101$ 为实用起点，$A$ 通常取允许迭代数的 10% 或更少。
$a$ 应结合初始梯度幅值与期望初始变化量选择，$c$ 应考虑噪声。
这些是选择方法，不能直接给出汉堡的数值。原始连续参数假设也不自动保证整数车辆适配后的收敛性。

## 汉堡输入核对

[冻结的核对资料](../../benchmarks/hamburg_ring1_spsa_od_definition_20261022_v1/README.md)保存完整清单。
道路散列仍为 `2d29bdb4183a9491bbd220324742d3a48a3484a96de8adc0eda984ad477cdb7e`。

- 旧需求共有 8640 个请求。2589 个请求在内部道路 `27064186#0` 结束。
- 该道路仍有合法的前向出口路径。它不能仅因旧路线在此结束，就被当成真实外部出口。
- 边界候选合并已有范围切口及前向道路的终端。仅有掉头连接不应掩盖一个模型边界。
- 候选有 45 条入口道路、41 条出口道路、495 个可达 OD 对和 555 条路线。
- 14 个出发时段包括 4 个初始化时段及 10 个主要时段。全候选空间共有 6930 个 OD 单元。
- 候选路线在道路层面覆盖 13 个字段，没有重复道路或重复路口行程。网络中的合法掉头没有被删除。
- 道路覆盖不证明车道覆盖或原生 E1 通过。范围切口也不证明真实车辆起终点。

495 个 OD 对只是候选空间，尚未作为正式标定范围。
仅 130 个计数通常不能识别唯一的高维 OD。多个 OD 可以产生相同的检测计数。
这不妨碍寻找一个合适的计数拟合，但需要公开初值、路线选择和不可观测部分的处理。

用户已选择从现有需求重组 21-OD 基线，并纠正内部终点。
继承的路线与车道分配应作为先验保留标签。它们不是现场观察的 OD 或驾驶行为。
已生成 294 个 OD 单元。初值保留全部 8640 个请求及原发车时刻。
2589 个内部终点沿 `27064186#0 → 1073054122#1 → 1345098540 → 24483197 → 199500676` 延长。
全部延长路线通过重复道路和重复路口检查。495-OD 全候选空间未被采用。

## 公式、代码与证据对应

| 要求 | 代码 | 保存的证据与状态 |
|---|---|---|
| 同时独立正负扰动、标准增益与两侧损失 | [`core/spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/spsa.py) | 每轮保存 $x_k,a_k,c_k,\Delta_k,x_k^+,x_k^-,L^+,L^-,\widehat g_k,x_{k+1}$ |
| 扰动和更新均限制在声明范围内 | 同上 | 边界检查。投影后仍使用论文分母 |
| 两次损失计算，无隐藏贪心选择 | 同上及 [`test_spsa.py`](../../tests/test_spsa.py) | 已知的耦合 OD 例子恢复已知解。400 次更新恰有 800 次损失计算 |
| 缺失或失败不能当成零 | 同上 | 非有限损失导致明确失败。调用方必须保留原仿真记录 |
| 终点及单次行程核对 | [`core/hamburg_od_definition.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_definition.py) | 冻结的 `audit.json`、`od-support.json`、全部候选和排除路线 |
| 可重做的核对命令 | [`audit_hamburg_od_definition.py`](../../plugins/astra-sumo/scripts/audit_hamburg_od_definition.py) | 只读输入，写入新目录。不运行 SUMO |
| 21-OD 初值与车辆生成 | [`core/hamburg_od_spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_spsa.py) | 初始 8640 个请求、原发车时刻与出口延长检查通过 |
| 原生 E1 目标评价 | 同上调用现有 `core/detector_demand_replay.py` | 保存全部 130 个字段与 40 个站点残差。失败不填零，碰撞运行只记录诊断损失 |
| 原生 SPSA 小例子 | [`test_spsa.py`](../../tests/test_spsa.py) | 两次原生 E1 扰动测量更新一次，再由最终原生运行验证已知目标。零需求也得到实际零计数，未伪装为缺失 |

车辆生成的实现采用各 OD 单元四舍五入，恰好半辆时向上取整。
出发时刻采用继承分布的分位点插值，路线和车道采用对应的经验模板。
这是 SUMO 显式车辆所需的离散化适配，原论文并未为汉堡规定它。
整数初值生成时没有改变原发车时刻。正式扰动试验前仍须固定这项适配、增益与停止设置。
当前先评价已批准的整数初值，不把它称为已完成 SPSA 更新。
已有控制对照仍保留原始记录。出现碰撞的运行不会作为有效标定接受。

最终接受条件保持不变：全部 130 个原生字段值和 40 个派生站点值零残差，且没有碰撞、传送或大量未进入车辆。
普通 SPSA 是否达到这些条件，必须由完整仿真结果回答。
