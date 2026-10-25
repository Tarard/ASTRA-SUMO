# Hamburg 普通 SPSA：标准设置首轮核验 V1

2026-10-22。状态：设置与实现已准备。完整初值评价尚在运行，SPSA 尚未产生汉堡更新结果。
用户已授权采用常见走廊标定设置，随后明确确认先做一轮原生核验。
此资料固定实际选择，后续结果另存，不覆盖本记录。
先前未执行的多轮设置保存在 [superseded-unrun-settings.json](superseded-unrun-settings.json)，不进入本次运行。

## 方法与选择

使用已批准的 21 个 OD、14 个出发时段，共 294 个实数流量变量。
每个变量控制该 OD 时段的出行数量。继承的路线、车道和发车分布仍是先验。
完整网络、13 个字段和 130 个真实计数目标保持不变。40 个站点值仅作字段求和报告。

目标函数为原生 E1 `nVehContrib` 与真实计数之差的平方和。
缺失不填零。每次评价从同一空网起始时刻开始，运行到 14400 秒，使用相同步长、控制和仿真种子。
不使用保存状态重启。输入准备见[已批准的 OD 基线](../hamburg_ring1_spsa_od_definition_20261022_v1/README.md)。

| 设置 | 数值与依据 |
|---|---|
| 普通 SPSA | 每轮独立 Bernoulli ±1，同时扰动全部变量。两次原生评价估计梯度 |
| $\alpha,\gamma$ | 0.602、0.101。采用 Spall 实施说明的实用建议 |
| 更新次数 $K$ | 1。用户明确批准的首轮核验，不构成普通 SPSA 收敛保证 |
| $A$ | 0。使用用户确认的首轮设置，满足稳定常数可取迭代数 10% 或更少的实施建议 |
| 初始扰动 $c$ | 每个 OD 时段 2 辆。汉堡整数车辆适配，非通用论文常数 |
| 初始变化幅度 | 1 辆。用第一对真实损失估计 $a$，然后直接使用该对损失完成第一轮 |
| 非负范围 | 扰动和更新投影到 $x\geq0$。不加入未经测量的入口容量上限 |
| 随机种子 | 扰动与 SUMO 均固定为 104。它不证明其他种子下的稳健性 |
| 车辆生成 | 每个实数 OD 单元四舍五入，半辆向上。继承分布按分位点确定车辆模板与发车时刻 |

增益遵循

$$c_k=c/k^\gamma,\qquad a_k=a/(A+k)^\alpha,$$

$$a=\frac{(A+1)^\alpha}{\max_i|\widehat g_{1i}|},\qquad
\widehat g_{1i}=\frac{L(x_1^+)-L(x_1^-)}{2c\Delta_{1i}}.$$

分子中的目标变化量为 1 辆。第一对梯度恰好为零时，不能由该对损失确定 $a$。
此时保留两次原生评价和全部残差，报告增益未能确定，不插入任意步长或无限追加试参。
这个结果也不能证明 OD 标定不可行。

## 停止与接受

完成 1 次更新，然后对返回向量做一次完整原生复测。
如果此前某个已测向量已满足用户的全部原生接受条件，则直接报告该向量并停止。
所有尝试和残差保留，包括较差结果。算法不按单次改善筛选下一步。

只有完整、健康的原生运行且 130 项字段残差全零，才能接受为本窗口精确标定。
40 项站点值派生自字段，不是独立证据。
碰撞、传送、未进入车辆和未完成行程均保留报告。无效运行的误差只作诊断。
低于 5% 的改善或 MAE=0.15 都不构成完成。

## 初值复用与代码对应

已启动的初值不会重新仿真。
复用前核对 6 份输入、OD 向量、原生设置、1.27.1 二进制及实际执行的原生实现。
再次从原生 E1 文件计算 130 项残差及 40 项站点指标。
复用初值即使不健康也保留诊断损失，其有效性不会被改写。

| 要求 | 实现与保存结果 |
|---|---|
| 两侧扰动与普通更新 | [`spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/spsa.py)，逐轮 `iteration-NNNN.json` |
| 第一次梯度确定 $a$ | 同一函数的 `initial_step`，只使用第一对损失，不另加试算 |
| 原生评价及站点求和 | [`hamburg_od_spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_spsa.py)，每个评价目录的 `evaluation.json`、130 个字段与 40 个站点表 |
| 初值复用 | [`hamburg_od_reuse.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_reuse.py)，`initial-reuse.json` |
| 运行与实现固定 | [`run_hamburg_od_spsa.py`](../../plugins/astra-sumo/scripts/run_hamburg_od_spsa.py)，相邻 `-execution/` 目录 |
| 进度和结论 | `progress.json`、`evaluations.json`、`result.json`、`README.md`。失败另存 `failure.json` |

从仓库根目录运行，输出目录必须为新目录：

```powershell
& benchmarks/hamburg_ring1_od_spsa_standard_20261022_v1/run-study.ps1 `
  -OutputDir outputs/hamburg-od-spsa-standard-new
```

初值仍在运行时，额外提供它实际的进程号 `-InitialProducerId`。
脚本仅等待这次已启动的初值，不启动重复初值或修改现有文件。

本次原始结果位置：`outputs/hamburg-od-spsa-standard-20261022-v1/`。
初值位置：`outputs/hamburg-od-initial-native-20261022-v1/`。
路线和车辆输入：`outputs/hamburg-od-baseline-20261022-v1/`。
本目录 [settings.json](settings.json) 是实际设置。

## 论文依据

[Spall，1998，Implementation…](https://www.jhuapl.edu/spsa/PDF-SPSA/Spall_Implementation_of_the_Simultaneous.PDF)，页 819–820，给出指数、稳定常数及初始变化幅度选择增益的实施建议。
[Antoniou 等，2015，W–SPSA in practice](https://web.mit.edu/cami/Public/CLA_WSPSAinPractice_ISTTT_2015_preprint.pdf)提供微观走廊与 OD—驾驶参数的实例。
本次采用普通 SPSA，并未实现其权重矩阵。完整核读记录见[方法说明 V1](../../docs/research/hamburg-spsa-od-method-2026-10-22-v1.md)。

本试验仍是固定先验下的计数标定。它不能识别唯一真实 OD，也不能验证真实车道行为或未观测的信号配时。
