# 汉堡计数标定：论文依据与代码设计 V2

本记录保留 [2026-10-20 版本](ring1-count-fit-basis-2026-10-20.md)。它说明本轮新增依据、代码修改及尚未实现的部分。
范围仍为 README 的 Binnenalster/Ring1。目标仍是全部 130 个字段时段和 40 个派生站点时段零误差。

## 论文与阅读范围

| 论文 | 出版与阅读依据 | 采用部分、评价及限制 |
|---|---|---|
| Balakrishna, R., Antoniou, C., Ben-Akiva, M., Koutsopoulos, H. N., Wen, Y. (2007). *Calibration of Microscopic Traffic Simulation Models: Methods and Application*. TRR 1999(1), 198–207. [DOI](https://doi.org/10.3141/1999-21) | 期刊论文。核对作者机构记录，阅读 [作者公开的最终投稿稿](https://www.researchgate.net/publication/277402098_Calibration_of_Microscopic_Traffic_Simulation_Models_Methods_and_Application) 的问题定义、式 1–3、求解与案例部分。 | **T2-方法可信，E2-实验有限**。采用原生仿真计算需求到观测的关系，并保留先验和参数边界。其高速路案例有静态 OD 等附加资料，不能据此证明汉堡精确可行。 |
| Flötteröd, G., Bierlaire, M., Nagel, K. (2011). *Bayesian Demand Calibration for Dynamic Traffic Simulations*. Transportation Science 45(4), 541–561. [DOI](https://doi.org/10.1287/trsc.1100.0367) | 期刊书目已核对。方法阅读依据是 [2010 年作者技术报告全文](https://svn.vsp.tu-berlin.de/repos/public-svn/publications/vspwp/2008/08-19/FloeBierNage_TranspScience_subm1_5jan2010.pdf)，重点为第 3–5 节及算法 2、3。技术报告不另计一篇。 | **T2-方法可信，E2-实验有限**。采用“交通计划、先验、计数与网络运行相互更新”的思路。其比例加载近似明确固定旅行时间，对拥堵表示有限。贝叶斯折中也不保证逐时段计数零差。 |
| Antoniou, C., Lima Azevedo, C., Lu, L., Pereira, F., Ben-Akiva, M. (2015). *W-SPSA in practice: Approximation of weight matrices and calibration of traffic simulation models*. TR-C 59, 129–146. [DOI](https://doi.org/10.1016/j.trc.2015.04.030) | 期刊记录及出版社方法摘录已核对。全文阅读使用 [MIT 的 ISTTT 作者稿](https://web.mit.edu/cami/Public/CLA_WSPSAinPractice_ISTTT_2015_preprint.pdf)，重点为式 1–8、第 4.3 节及两个案例。同名 Procedia 版本 DOI 10.1016/j.trpro.2015.06.013 不另计，未逐页比对期刊终稿。 | **T2-方法可信，E2-实验有限**。采用路线、出发时段与当前旅行时间对应的时变影响信息。论文在每轮更新权重，并用仿真评价结果。其案例有流量、速度等数据，不能照搬所有待估参数。 |
| Hollander, Y., Liu, R. (2008). *The principles of calibrating traffic microsimulation models*. Transportation 35, 347–362. [DOI](https://doi.org/10.1007/s11116-007-9156-2) | 期刊综述。核对出版社记录，阅读 [作者公开全文](https://www.researchgate.net/publication/5159104_The_principles_of_calibrating_traffic_microsimulation_models)，重点为校准范围、拟合度、验证部分及表 3。 | **T2-方法可信**，用于方法原则，不作为性能证据。采用一致的时空配对、明确的参数范围及校准和独立验证的区分。本文不为汉堡提供收敛或精确拟合保证。 |

上表区分正式出版记录与实际读到的作者稿。未取得的期刊终稿不标记为已读全文。
旧版本已核对的 SUMO 计数生成比较和 Munich 案例继续适用，本轮不重复计算论文数量。

## 对应到本次失败的判断

[完整车道保持运行](../../benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1/README.md) 消除了已完成通过车辆的字段误分配。
它仍有 1593 辆未进入，192 辆已进入但没有完成字段通过，3396 辆在其他时段通过指定字段。
这说明下一步需求估计必须表示进入等待与路网内行程时间。固定自由流时延只适合作为初始候选。
这是结合论文和本次原生结果作出的判断，不是论文对汉堡的直接结论。

本轮采用仿真内需求估计作为主线。设 $x$ 表示“道路路线、声明的车道意图、出发时段”的车辆数。
$F(x)$ 表示固定网络、控制方式和驾驶假设后，SUMO 原生 E1 产生的全部字段计数。
最终接受条件始终是 $F(x)=y$，且原生运行有效。整数候选中的等式不替代该条件。

先验只用于限定合理路线和选择需求候选。当前道路等级与入口分配是声明，不是实测 OD 或通行能力。
不把“车辆最少”或“距离最短”单独作为需求合理性的依据。
本轮不使用同一组计数同时修改道路几何、信号含义、跟驰参数和需求，从而补偿彼此的错误。
这是针对现有资料不足的适配选择，不是这些论文禁止联合估计。

真实观测仍只有原有字段计数。原生旅行时间、车辆身份、入口等待和排队是仿真内诊断，不能充当新增现场观测。
40 个站点值继续由字段相加，不在估计目标中重复作为独立证据。
本次仍使用全部 130 行进行同窗拟合。独立预测能力留作未验证项，不通过删掉字段建立新的验收门槛。

## 已实现的第一项修改

新增 [原生时段反馈计算](../../plugins/astra-sumo/src/astra_sumo/core/native_temporal_feedback.py)，并接入 [汉堡结果分析](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)。
它按“计划、原生请求出发时段”归组，并保留全部请求车辆作为分母。
对每个字段时段，保存本次原生完整通过数 $K_{ij}$、请求车辆数 $N_j$，及本次对应比例 $a_{ij}=K_{ij}/N_j$。
整数关系 $\sum_j K_{ij}=y_i^{\mathrm{SUMO}}$ 逐行核对原生 `nVehContrib`。

未进入、已进入但尚未通过、观察窗内通过、仅在窗外通过分别保留。
入口等待使用实际进入时刻减原生请求时刻，路网内时间使用通过时刻减实际进入时刻。
窗外真实计数保持未知。未采样的计划和出发时段也保持未知。
本次观测窗前的出发请求数为 0。提前一小时启动仿真没有形成带交通需求的初始化。
初始化需求和窗口开始时的车辆状态仍须声明。这不代表现场在该小时前没有交通。
请求时间按 [SUMO 1.27.1 的毫秒规则](https://raw.githubusercontent.com/eclipse-sumo/sumo/v1_27_1/src/utils/common/SUMOTime.h) 解释，物理步长仍为 0.1 秒。

这一步是 **adapt**：借用论文中的时变对应关系，增加本案例需要的未进入车辆和窗外通过记账。
它没有实现完整 W-SPSA 或 Cadyts，也没有把 $a_{ij}$ 当作拥堵下的导数或全局预测。
同一次回放重构一致仅验证记账，不证明改变需求后仍可沿用该比例。

## 论文到代码的对应

| 依据 | 设计选择 | 代码与保存证据 |
|---|---|---|
| 2007 年式 1–3，仿真产生观测 | **adopt**：以原生回放评价需求候选 | [原生执行与有效性](../../plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py)、冻结运行的 `native/run.json` |
| 2015 年第 4.3 节，时变需求到测量的影响 | **adapt**：保存本次计划与出发时段到字段时段的对应 | `native_temporal_feedback`，保存整数分子、请求分母、比例及逐列进入等待 |
| 2011 年计划先验与迭代运行 | **adapt**：显式记录需求先验；新需求须重新回放 | 既有路线合法性、入口先验和原生回放；完整贝叶斯选择尚未实现 |
| 2008 年表 3 及独立验证讨论 | **adopt**：保留全部配对及结果边界 | 全部 130 字段行、40 派生站点行、无效运行指标与原始记录 |
| 用户的更强要求 | 保持 MAE=0、最大绝对误差=0及有效运行条件 | 原生验收保持原样，不采用论文中的非零拟合容差 |

## 下一项设计与未完成部分

先用本次反馈定位路线与出发时段的进入压力，再提出一个小规模需求修改方案。
更新需求后重新运行 SUMO，并重新取得对应关系。候选比较使用原生计数与车辆完成情况。
需要区分拟合目标、先验偏离和运行有效性。正负残差不能通过汇总互相抵消。

完整的需求校正循环、影响关系的成对扰动验证以及终止条件尚未实现。
它们必须在下一项变更中单独说明。本记录没有把一次对应比例计算当作论文算法复现或精确标定。

复现本次反馈：

```powershell
.venv/Scripts/python.exe plugins/astra-sumo/scripts/analyze_hamburg_temporal_feedback.py benchmarks/hamburg_ring1_lane_section_hold_full_20261021_v1 outputs/new-temporal-feedback
```

冻结结果见 [原生时段反馈](../../benchmarks/hamburg_ring1_temporal_feedback_20261021_v1/README.md)。
本步骤不再运行仿真，也不改写原始失败结果。
