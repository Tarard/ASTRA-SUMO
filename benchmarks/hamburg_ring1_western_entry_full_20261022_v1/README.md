# 汉堡西侧入口：完整窗口结果

**本方案不采用。** 完整主窗没有延续为有效标定，原入口方案仍为完整参考。
两次运行都保留 8640 辆模型车、1953 次初始化字段目标及 6687 次主窗字段目标。
本次只改变 95 辆初始化车和 427 辆主窗车的道路前缀。所有发车时刻、目标时段、目的地及汇合后的路线不变。
没有增加短服务车道需求，没有改变信号、车道权限或驾驶规则。

| 完整窗口检查 | 原入口参考 | 西侧主路分配 |
|---|---:|---:|
| 进入 / 到达 | 7996 / 7690 | 7706 / 7296 |
| 未进入 / 在途 | 644 / 306 | 934 / 410 |
| 碰撞 / 传送 | 0 / 0 | 0 / 0 |
| 可用字段 / 派生站点时段 | 130 / 40 | 130 / 40 |
| 一致字段 / 派生站点时段 | 31 / 7 | 31 / 7 |
| 字段诊断 MAE | 12.962 | 14.369 |
| 字段最大绝对误差 | 59 | 62 |
| 派生站点诊断 MAE | 39.725 | 45.500 |
| 派生站点最大绝对误差 | 150 | 152 |
| 主窗原生 E1 贡献 | 5182 | 4887 |

两次运行均无效，正式计数指标均为空。站点只是字段之和，不是独立证据。
本次从空路网完整运行至 14400 秒，没有超时或从状态续跑。
全部差值见 [130 个字段时段](field-comparison.csv)、[40 个派生站点时段](station-comparison.csv)及[与参考逐行比较](analysis/all-130-baseline-comparisons.csv)。
没有删除字段、时段、困难车辆或负面结果。

新主路入口的 522 辆请求全部进入，但截至结束只有 375 辆完成指定字段贡献。
其中 147 辆已进入却尚未通过字段。入口进入情况不能代替下游通行或计数。
原两条支路保留请求的未进入数分别为 129 和 144。
其他入口的损失仍保留，见 [全部入口及未进入车辆](data-analysis/entry-pressure.csv)。
例如 Z15 的主窗贡献从 647 降至 508，119 Z4 从 301 降至 278，119 Z5 从 308 降至 286。
535 Z4 从 372 增至 380，但该改善不足以支持采用整体方案。

## 全部残差的原生分解

6687 次主窗目标中，1020 次落在原计划时段，3584 次落在其他主窗时段，790 次落在窗后。
另有 934 辆未进入，359 辆已进入却尚未完成字段通过。
初始化车辆另贡献 283 次主窗观测。因此主窗原生计数为 1020 + 3584 + 283 = 4887。
全部 130 行均满足原生残差分解等式，见 [逐行分解](data-analysis/row-decomposition.csv)及[汇总](data-analysis/decomposition.json)。
这些等式只说明本次运行的计数去向，不预测改变需求后的结果。

完整运行的初始化 52 个 E1 时段逐属性复现前缀，见 [对应检查](analysis/prefix-reproduction-check.json)。
前缀已报告的 Z15 减少 31 次、Z4 减少 13 次等损失全部保留。
该前缀的小流量证明和分配规则见 [前缀报告](../hamburg_ring1_western_entry_prefix_20261022_v1/README.md)。
主窗检验说明，合法且能进入的主路替代，并不自动改善整个走廊的计数。
本轮不再尝试新的西侧入口份额，也不采用这套分配。

## 原碰撞车辆与描述信息

原六辆关注车辆中，四辆在原关注位置完成后轮清空且到达。
一辆改道后不再经过原关注位置，已到达。另一辆仍在原支路上，尚未到达原关注位置。
最后一辆在 14399.9 秒位于 `448511533#3_1`，位置约 23.285 米，速度为零。
没有据此声称六辆都重新完成了原危险动作，也没有从单个停车位置推断阻塞循环。
[路线对应](analysis/original-participant-route-correspondence.json)、[原样检查](analysis/original-participant-check.json)和[最后观测](analysis/unfinished-historical-participant-sample.json)保留这种区分。

执行时的 `scenario.json` 沿用了旧的方案数 27。实际需求包含 29 个不同方案。
该数值是描述信息，不是 SUMO 输入参数。原执行文件和校验值保持不变。
[差异记录](live-observations/scenario-profile-count-note.json)给出原值与实际值。后续生成代码已修正此描述信息。

原生运行：[脚本](run-western-entry-full-20261022-v1.py)。报告与分析：[脚本](analyze-western-entry-full-20261022-v1.py)。
计数分解：[脚本](decompose-western-entry-main-residuals-20261022-v1.py)。原车辆对应：[脚本](check-original-participants.py)。
配置和执行前代码原文：[inputs](inputs/)。全部原生结果：[native](native/)。
[原入口完整参考](../hamburg_ring1_primary_entry_full_20261022_v1/README.md)仍未达到零误差要求。
本次结果不能证明其他需求或控制方案在物理上不可行。它只否定这一次明确的入口分配试验。

---

# 汉堡 README 走廊：完整流程与原生计数结果

**未通过标定验收。** 原始 OSM → 道路重要性剪枝 → 物理路口汇聚 → 传感器 → 需求与信号 → 原生仿真 → 同时段计数对比均已有输出。
此处的流程完成不等于标定成功。要求仍为 130 个字段时段和 40 个派生站点时段全部零误差。

范围为 Binnenalster/Ring1，LSA 104、118、119、200、535。固定图像范围为 9.9815,53.5525,10.0065,53.5598。
真实观察窗为 2026-10-19 05:30–08:00 UTC，即 07:30–10:00 CEST。
SUMO 1.27.1，步长 0.1 秒，seed 104，仿真 0 对应当地 06:30，计划结束 14400 秒。停止检查每 0.1 秒比较全部在途车辆的速度与位置，静止上限 300 秒。


| 原生运行检查 | 数值 |
|---|---:|
| 计划车辆 | 8640 |
| 实际进入 | 7706 |
| 到达 | 7296 |
| 结束时仍在路网 | 410 |
| 尚未进入 | 934 |
| 碰撞 | 0 |
| 传送 | 0 |
| 字段可用时段 | 130/130 |
| 字段精确时段 | 31/130 |
| 有效运行的正式计数指标 | 未计算（原生运行无效） |

全部差值见 [130 个字段时段](field-comparison.csv) 和 [40 个派生站点时段](station-comparison.csv)。缺失保留为缺失。
站点值由字段相加，不是独立证据。计数只读原生 E1 的 `nVehContrib`。
诊断 MAE 为 14.36923076923077，诊断最大绝对差为 62；无效运行的这些数值不能作为标定结果接受。

## 走廊与图像

![原始 OSM 与重要性剪枝](figures/01-osm-to-pruned.png)
![物理路口汇聚前后](figures/02-pruned-to-junctions.png)
![LSA118 固定范围细节](figures/03-lsa118-before-after.png)
![模型中的 13 个原生 E1 字段](figures/04-sensors.png)

主图由 `astra review render` 生成，前后共用固定范围与同一航拍校验值。航拍为 Hamburg LGV 的 2026 年 WMS 资料，许可为 Datenlizenz Deutschland Namensnennung 2.0，仅作空间背景。
道路数据：© OpenStreetMap contributors，ODbL。官方 MAP、OCIT 及计数资料沿用仓库冻结来源包的署名与许可。
虚拟传感器坐标按当前模型车道长度投影，不是现场测绘验证。

![NetEdit before](figures/netedit-before.png)
![NetEdit after](figures/netedit-after.png)

NetEdit 补充图来自真实 NetEdit 1.27.1，使用相同航拍、中心与缩放；after 在 Inspect 模式选中 LSA118。

## 同时段真实计数与 SUMO 计数

![全部字段计数](figures/field-counts.png)
![全部派生站点计数](figures/station-counts.png)

## 已声明的模型

需求只使用一次通过路口的路线，每车对应一个被观测字段；没有额外未观测背景车流、重复道路、绕圈、掉头需求或客户道路通行。网络中的合法转向及掉头保留。
本次模型人口包含初始化 1953 辆、主窗 6687 辆。该人口不识别现场独立车辆总量。
入口沿用已声明的道路范围边界和道路等级先验上限。本次按已声明的路线份额和请求时刻构造需求，保留全部目标行。原生 E1 提供拟合结果。
车道意图按输入 `lane-intentions.json` 中记录的机制执行。这是声明的驾驶意图，不是已验证的自然驾驶行为。当前设置见本报告 `results.json` 的 scenario。
官方路口采用 M2 的 OCIT 固定方案；Z.15 为带标签的 K9 备选。58 个其余 OSM 信号保留原生感应控制，起始时刻与 seed 固定。它们仍是占位控制，没有现场时序验证。
535 采用已有原生扇出曲线候选，普通道路、显式长度和合法通行权限保持原样。原生曲线不等于官方参考折线或航拍测绘路径。
104、535 两个不符合车道箭头的附加转向声明为红灯；相应道路对仍有其他车道承载。
200 进口施工阶段与 MAP 的不一致、Z.15 官方组未知及其他几何与转向对应限制仍保留。

## 失败记录与结果边界

此前试跑因入口积压或碰撞失败，原始输出保留，不能混入有效结果。当前报告仅覆盖所声明的本次原生运行。
本轮不再继续无上限校正。尚未满足的残差与进入压力直接保留，后续再讨论控制、车道意图和需求可实现性。
逐入口表保留已写出的进入延误及缺少行程记录的车辆数，见 [逐入口表](entry-delays.csv)。原始行程输出不完整时，缺少记录的车辆状态保持未知。
原生瞬时与周期检测器的对应见 [核对结果](passage-reconciliation.json.gz)，逐车字段记录见 [明细](vehicle-field-passages.csv.gz)。
即使同一窗口精确匹配，也只代表声明模型下的标定拟合，不代表独立预测、真实 OD 或现场信号控制已恢复。

## 复现位置

- 执行：[run_hamburg_readme_pipeline.py](../../plugins/astra-sumo/scripts/run_hamburg_readme_pipeline.py)。
- 需求构造：[hamburg_readme_pipeline.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_pipeline.py)。
- 计数与有效性：[detector_demand_replay.py](../../plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py)。
- 分析与图像：[hamburg_readme_results.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)。
- 参数与输入：[inputs](inputs/)、原生结果：[native](native/)、各项校验值：[manifest.json](manifest.json)。
