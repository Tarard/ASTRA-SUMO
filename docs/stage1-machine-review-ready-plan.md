# Torii-SUMO Stage 1-M — Machine REVIEW_READY 执行规范

更新时间：2026-07-14  
状态：**Machine REVIEW_READY**；Stage 1-H 与自动晋级保持阻断
上位规范：[`torii-corridor-human-modeling-research-plan.md`](torii-corridor-human-modeling-research-plan.md)

## 1. 目标定义

当前唯一主执行目标正式调整为：

> **Stage 1-M — Machine REVIEW_READY：完成机器证据冻结、无损审核压缩、盲审材料和抽样协议；所有自动晋级继续阻断。**

这不是 Stage 1 退出，也不是研究方向降级。完整 Stage 1 分成两个不可混淆的里程碑：

1. **Stage 1-M — Machine REVIEW_READY**：机器证据、审核单位、抽样协议、盲化材料和可复现身份全部准备完成。
2. **Stage 1-H — Human Validation**：两名独立审核者、第三名 adjudicator，以及预注册统计门槛全部完成。

只有 Stage 1-H 通过，Stage 1 才能退出。`REVIEW_READY`、`Stage 1 complete` 和 `auto-certified` 是三个不同状态。

当前 30 条多城市走廊属于 Stage 1 的真实 held-out 数据收集。它们不是路线图意义上的 Stage 6；Stage 6 只用于验证一个已经完成前序认证的方法能否跨城市迁移。

## 2. 当前机器基线的正确解释

权威 REVIEW_READY 运行记录：

- 6/6 城市源和 30/30 effective-corpus 走廊快照通过身份与引用闭包；snapshot manifest 绑定 40 个 artifact，缺失和哈希失败均为 0。
- 30/30 走廊完成机器流水线，30/30 通过 normalized semantic replay；machine manifest 绑定 1,404 个 artifact，缺失和哈希失败均为 0。
- 冻结的 `PCB-453` 保留为 27 条原始有效走廊基线；三个 replacement 新增 6 条 assessment，因此 effective corpus 的权威 census 是 `PCB-459`：438 条 `runtime-special-controller`、21 条 `ordinary-program-truly-absent`、0 条 ambiguous。459/459 均有唯一 primary class 和精确审核位置。
- 冻结的 `RWC-1` 基线仍为 88,423 个 atomic witness；三个 replacement 新增 13,975 个，effective corpus 共 102,398 个：39,725 confirmed、62,673 potential、98,041 clusters、15,878 site review cases。lost、duplicate、extraneous 和 mixed-hard-key violation 均为 0。
- effective corpus 有 3 个稳定 `PedestrianCoverageGap`：Paris Porte Maillot 2 个、London Liverpool Street 1 个。它们都是 safety coverage blocker + OOD/review task，不是已证明的现场结构缺陷；Paris 两个不得当成两个独立认证地点。
- v2-R2 盲审包包含 30 个 corridor package、384 个 review unit：240 个 conflict site、120 个 negative pair、21 个 hard controlled-binding census 和 3 个 coverage-gap census；217 个 conflict site 带独立隐藏 witness。
- 正式盲审包与独立重复包各有 892 个文件，逐相对路径 SHA-256 差异为 0；reviewer-visible machine-label、attention-role 和 hidden-role 泄漏均为 0。
- 30 个 corridor-level machine label 仍全部为 `defect`，这不是人工标签，也不能产生 AutoPrecision。所有 promotion gate 仍为 `blocked`。

权威机器证据冻结在 [`stage1m_machine_review_ready_provenance_20260714.v3.json`](../benchmarks/corridor_human_modeling_v1/evidence/stage1m_machine_review_ready_provenance_20260714.v3.json)。它保留旧 provenance v3，不覆盖历史 artifact，并绑定精确 clean producer、tree、工具链、输入/输出哈希和 artifact DAG。

## 3. Stage 1-M 十项退出门

Stage 1-M 只有在下列十项同时关闭后才能标记为 `REVIEW_READY`：

1. 当前 head 的 Corridor Contract Freeze CI 全绿。
2. REVIEW_READY provenance 与精确 clean producer commit、tree revision、工具链、全部输入及输出哈希绑定。
3. 至少 30 个有效、完整、盲化的 review case；3 个 replay-invalid corridor 单独进入 reproducibility 统计，不计入模型质量样本。
4. Paris 两个 residual crossing 及 replacement 新增的 London gap 各自具有稳定实体、完整拒绝原因、精确审核位置和 OOD 维度。
5. 冻结 `PCB-453` 与 effective `PCB-459` 均完成全量、确定性的技术分类。
6. 冻结 88,423-witness 基线与 effective 102,398-witness census 全部进入无损 cluster ledger，丢失和重复均为零。
7. cluster 抽样概率、代表 witness、extremal witness 和隐藏成员验证协议冻结。
8. v2 human-review trial、阈值、replacement policy、统计方法和 schema 冻结。
9. 所有自动 promotion gate 明确保持 blocked。
10. 所有状态文档明确写明 `REVIEW_READY != Stage 1 exit`。

任何单项失败仍可生成诊断 artifact，但不得输出 `REVIEW_READY`。

## 4. 工作流隔离

### 4.1 Stage 1 冻结证据流

```text
Stage 1-M frozen machine evidence
  -> blinded human review
  -> independent Reviewer A + Reviewer B
  -> third-party adjudication
  -> preregistered Stage 1-H result
```

冻结后的 machine assessment、finding、source/candidate 身份或 trial parent hash 不得因人工标签或 Stage 2 结果而改变。

### 4.2 Stage 2 development-only 流

Stage 2 可以在独立 development corpus 上并行研究：

- 合成 micro-repair；
- SUMO 官方小场景 repair；
- candidate schema 和 rollback；
- 局部 materializer；
- 故障注入；
- 编辑类型适用域和 abstention 规则。

Stage 1-H 退出前禁止：

- Stage 2 编辑类型认证；
- 宣称达到 AutoPrecision 门槛；
- 使用 Stage 1 blinded held-out case 调参；
- 查看人工标签后修改 Stage 1 machine assessment；
- 把新 Stage 2 variant 插入已冻结 trial；
- 用 Stage 2 输出覆盖 Stage 1 source、candidate、finding 或 machine label。

MGE-1、Stage 3 拓扑晋级、Stage 5 NEMA/行人认证、城市级候选修改和 Stage 6 泛化结论均暂停。MGE-1 保留为后续预注册实验，不再是当前主执行目标。

## 5. 权威数据模型增量

### 5.1 `PedestrianCoverageGap`

Paris residual 不得只保留数量或 raw connection index。每个 gap 至少绑定：

- stable coverage-gap ID；
- stable crossing signature；
- source crossing edge identity；
- owner candidate physical cells；
- walkingarea/crossing chain candidates；
- `crossingEdges` 证据；
- boundary-port candidates；
- lane permission 和几何；
- 全部 rejection reasons；
- source/map coordinates；
- affected movement candidates；
- review overlay location；
- OOD dimensions；
- source、candidate、toolchain 和 producer identity。

分类可以同时包含 OOD 与 review：

- 悬空引用或明确非法 path/permission：structural blocker；
- 结构合法但 priority、owner、control 或现实意图不明确：review task；
- 合法但当前模型未覆盖：OOD evidence + review task；
- source 缺失必要事实：evidence insufficiency。

### 5.2 行人控制语义

旧的 `signalized | uncontrolled` 必须重构为：

- `signalized`
- `priority-unsignalized`
- `unprioritized-unsignalized`
- `unknown-unsignalized`
- `runtime-special`
- `shared-space-or-unsupported`

`unknown-unsignalized` 不得自动降级为有或无行人优先权。

### 5.3 `ControlledPedestrianBindingAssessment`

旧 finding `controlled_pedestrian_signal_group_missing` 改写为中性事实：

> `controlled_pedestrian_effective_program_unresolved`：某 pedestrian movement 声称由 controller 控制，但机器尚未构造出可执行、可验证的 effective signal-group/program closure。

分类器必须给每条 finding 唯一 primary class：

1. `ordinary-program-truly-absent`
2. `external-program-present`
3. `runtime-special-controller`
4. `program-present-link-invalid`
5. `shared-controller-scope-incomplete`
6. `stale-or-ambiguous-control-reference`
7. `unsupported-program-form`

只有第 1、4 类可直接称为机器确认的结构错误。每条 assessment 必须保存 raw connection、controller、owner type、`linkIndex/linkIndex2`、effective sumocfg、net/additional programs、逐 phase state、共享 index movements、physical cells、request/foes row、位置、primary class、secondary flags 和拒绝原因。

### 5.4 无损冲突审核结构

#### AtomicWitness

每个原始 conflict witness 不可变，至少包含：

- `conflict_id`
- `pedestrian_movement_id`
- `vehicle_movement_id`
- `physical_cell_id`
- `crossing_signature`
- `conflict_reason`
- `certainty`
- centerline distance、crossing angle、envelope 参数
- vehicle approach、turn class、lane role
- control class
- observed request/foes relation
- traffic side
- source、candidate、toolchain hashes

#### SiteReviewCase

一级人工审核单位是：

> 一个 pedestrian crossing facility + 全部冲突 vehicle movement families + 一个 right-of-way evidence bundle。

同一 approach 的平行 lane witness 可以进入一个 family，但 lane-level atomic witness 必须保留在 membership 中。

#### PopulationStratum

跨地点只用于抽样和统计，不用于直接传播决定。stratum key 包含：

- control class；
- ROW evidence class；
- conflict reason；
- certainty；
- vehicle turn class；
- traffic side；
- crossing morphology；
- road class/speed band。

### 5.5 无损与纯度不变量

若 atomic witness 集合为 `W`、clusters 为 `C_i`，必须满足：

```text
W = disjoint_union(C_i)
```

硬门：

- membership coverage 100%；
- 丢失 0、重复 0；
- cluster membership 有稳定 hash 或 Merkle root；
- confirmed 与 potential 不混合；
- conflict reason 不混合；
- control class 不混合；
- physical cell 不混合；
- request/response/foes relation 不混合；
- grade/layer/bridge/rail 关系不混合；
- hard safety 与 review-only 不混合。

每个 cluster 至少选择 geometry medoid、风险 extremal、速度/道路等级 extremal、角度 extrema、每个 approach/turn family 和 ROW relation 的代表，以及一个由冻结随机种子选择的隐藏成员。

人工决定只有在硬字段完全相同、代表与隐藏成员决定一致、未发现新子类型、purity 通过且问题完全相同时才能传播；否则必须拆分 cluster。

## 6. v1 与 v2 人工试验

### 6.1 v1 冻结为 pilot

v1 不原地修改。它当前存在结构性不可通过条件：

- 有效完整 case 为 27，低于 30；
- 所有机器标签均为 `defect`，machine `acceptable` 数量为 0；
- evaluator 在 `auto_count == 0` 时令 AutoPrecision 未定义；
- v1 同时要求 AutoPrecision 至少 0.99。

不得把最低 case 数降为 27，也不得把未定义 AutoPrecision 改写成 1.0。

### 6.2 v2 两个 cohort

1. **Audit-attention cohort**：测量 attention precision、attention recall、审核者一致性、审核时间和 safety false negative；AutoPrecision 为 `not-applicable`。
2. **Prospective safe-pass cohort**：未来仅从机器真实输出 `acceptable` 的前瞻性样本测量 AutoPrecision 和 AutoCoverage；不得从 v1 defect-only 样本反推。

三个 replay-invalid corridor 只进入 reproducibility 统计。另从预先冻结的 reserve corpus 按确定性规则补入三个 replacement case，保持城市、形态、通行侧和模式分层；不得依据机器结果或人工可见性挑选。

v2 必须使用新的 schema、trial ID、parent benchmark hash、replacement-policy hash 和 blinding seed hash。reviewer-visible package 不得泄露 machine label。

## 7. 三个当前研究实验

### 7.1 PCB-453 — Controlled Pedestrian Binding Census

**假设**：冻结基线的 453 条 finding 可仅凭冻结机器证据被确定性分入七个 primary classes。replacement 不改写该实验身份，而是形成 effective-corpus 扩展 `PCB-459`。

**机器成功门**：

- 基线 453/453、effective corpus 459/459 均有唯一 primary class；
- unknown 不超过预注册的 5%；
- effective external program 被误分为 ordinary missing：0；
- runtime special 被误分为普通道路 missing：0；
- 两次 clean rerun 的 exact classification diff：0；
- 每条均有精确审核位置和 manifest closure。

**证伪条件**：分类依赖 raw connection index；有效 external program 漏读；shared controller 被解释为 physical merge；相同证据签名产生不同结果；unknown 超限。

### 7.2 ROW-1 — Independent Pedestrian Right-of-Way Oracle

独立验证必须分离四个证据通道：

1. Source Evidence Graph：只读取 netconvert 前输入、OSM、plain crossing priority、builder 参数和地图观察。
2. Independent Geometry Graph：只回答 occupancy path 的空间相交，不回答优先权。
3. Model Claim Graph：把 request/foes/cont、connection state、waiting junction、TLS identity 当作网络声明而非真值。
4. Runtime Behavioral Probe：在受控到达时间和速度下测量 SUMO 对该网络的执行行为，不把运行行为当作现实道路真值。

**成功门**：所有 gold classes 正确；证据不足全部 abstain；co-self-consistent mutants 被 source/model contradiction 检出；已知 unsafe case 误判 pass 为 0；expected-answer 路径不读取 request/foes。

### 7.3 RWC-1 — Lossless Review Witness Compression

**假设**：冻结的 88,423 个 atomic witness 可压缩成 crossing-level review cases 和 population strata，同时保持完整 witness、可逆 membership 和无偏抽样能力。effective-corpus 扩展对 replacement 新增的 13,975 个 witness 应满足相同不变量，总数为 102,398。

**机器 REVIEW_READY 门**：

- atomic membership coverage 100%；
- 丢失、重复均为 0；
- cluster ID 和 membership root 两次 clean run 完全一致；
- 硬字段不同的 witness 不混合；
- rare、hard、OOD strata 全量进入 census；
- inclusion probability 可重建；
- reviewer-visible package 不泄露 machine label；
- promotion gate 保持 blocked。

人工阶段才可评价 cluster 隐藏异质性、agreement、kappa、attention precision/recall 和 review time。

## 8. 抽样协议

- 459 个 controlled-binding assessment：机器全量 census；人工包只对 21 个 hard class assessment 全量审核。
- Paris 两个 residual crossing和 London replacement 的一个 gap：全量审核任务；Paris 两个不得计作两个独立认证地点。
- rare、rail、grade-separated、unknown-control、shared-controller：机器全量保留；是否进入人工 census 由冻结 v2-R2 sampling policy 决定，不因“稀有”自动扩大人工样本。
- 高频 homogeneous cluster：每 corridor 冻结抽取 8 个 conflict site，并记录每个 unit 的 inclusion probability。
- 每 corridor 冻结抽取 4 个 negative pair，用于估计漏检和 attention recall。
- 每个大型 cluster：代表、extremal 和隐藏随机成员。
- 从未产生 conflict finding 的 crossing × vehicle pair 抽取负样本，用于估计漏检和 recall。

没有 inclusion probability 的 attention sampling 只能用于界面排序，不能用于统计推断。

## 9. 当前禁止声明

在相应证据门关闭前，不得宣称：

1. Stage 1 已退出或 REVIEW_READY 等于 Stage 1 complete。
2. 当前 v1 human trial 具备可通过性。
3. 30 条走廊证明跨城市泛化。
4. 30 个 machine defect 等于 30 个现实缺陷。
5. 18,311 个 modeled pedestrian movement 已被证明正确。
6. 34,493 个 confirmed witness 等于 34,493 个安全缺陷。
7. 53,930 个 potential witness 等于实际冲突。
8. 453 条 finding 全部属于现场 program 缺失。
9. Paris 两个 crossing 已被证明结构损坏。
10. request/foes、internal geometry 或 TLS 已被独立证明正确。
11. AutoPrecision、AutoCoverage、precision、recall、kappa 或 review time 已测得。
12. Stage 2 的任何 micro-repair 已认证。
13. Stage 3 split/merge/partial 决策已完成。
14. MGE-1 已执行或通过。
15. NEMA、pedestrian、bicycle、rail 或完整多模式安全已经认证。
16. 三个 netconvert replay mismatch 可通过 XML normalization 忽略。
17. shared TLS controller 是 physical junction merge 证据。
18. 任意 OSM 城市已能自动达到专家 NetEdit 质量。

## 10. 可复现性与证据护照

所有 Stage 1-M 产物必须携带：

- source、candidate、corpus、policy、schema 和 producer identities；
- clean commit 与 tree revision；
- Python、SUMO、netconvert、依赖和命令身份；
- 输入、输出、日志、抽样规则、种子和 membership roots；
- exact gate trace；
- `promotion_status = blocked`；
- stale-evidence 检查；
- artifact DAG closure。

本规范采用的研究方法：预注册假设、机器与人工阶段分离、失败可证伪、确定性重复、冻结阈值与不可变证据。当前证据成熟度为 **Machine REVIEW_READY**，不是 human-validated result，也不是自动修复认证。

## 11. 开发运行记录（非权威 provenance）

截至 2026-07-14，当前实现已完成 RWC-1 的开发级双运行检查。该检查从旧 v3 运行保留的 27 个候选 `.net.xml` 重新执行当前 canonicalizer 和 independent safety audit；旧 canonical JSON 没有被静默补字段或当作当前证据。结果为：

- 27/27 有效 corridor 的完整压缩报告 SHA-256 在相同冻结种子下完全一致；
- 34,493 个 confirmed witness 与 53,930 个 potential witness 全部进入 atomic ledger，共 88,423 个；
- atomic membership coverage 为 100%；lost、duplicate、extraneous 和 mixed-hard-key violation 均为 0；
- 每个 corridor 的 machine review-compression gate 为 pass；
- 每个 corridor 的 automatic promotion gate 仍为 blocked；
- `london-kings-cross`、`melbourne-royal-parade` 和 `sydney-cross-city-tunnel` 仍按 replay-invalid 单列，没有进入上述 27 个模型质量样本。

该历史开发结果只证明压缩器在旧候选快照上的机器闭合与确定性，不证明 finding 对应现实缺陷，也不证明 cluster 内人工决定可传播。它后来由第 2 节所列 clean-producer effective-corpus provenance 取代为当前机器证据，但仍保留为 88,423-witness 基线。

### 11.2 ROW-1 开发运行（非权威 provenance）

截至 2026-07-14，ROW-1 已使用锁定的 SUMO/netconvert 1.27.1 完成两次独立开发运行。两次完整 `row-1-experiment-report.json` 的 SHA-256 均为 `80a540c1045639d2a834ceaddd4e4b913980aec93fb277501089c335a84a02e8`，报告身份均为 `manifest_63f6594cbc1bb25cde1b605d`。结果为：

- 15 个 experiment case，包含 priority、unprioritized、signalized、unknown、shared-space、split crossing、左右侧通行、直/左/右 movement 和四类 mutation；
- 12 个真实双主体 SUMO runtime probe，固定 seed、速度和三种到达顺序；
- case failure 为 0，unsafe false pass 为 0，source-insufficient forced decision 为 0，expected-answer 读取 request/foes 的次数为 0；
- 右侧通行 priority straight 的 simultaneous probe 观察到 vehicle yield，unprioritized straight 观察到 pedestrian yield；左侧通行 priority straight 使用实际对应的另一侧 crossing 后观察到 vehicle yield；
- signalized g/G closure、多阶段 crossing、shared-space、unknown priority 和带 internal continuation 的 turning movement 均保持 `review`，没有被静态 request/foes 分类强制通过；
- request-only reversal 被 source/model contradiction 阻断，并在运行时产生 emergency-braking blocker；完整 opposite-model reversal 即使在 geometry、request/foes 和运行行为上彼此自洽，仍被独立 source evidence 检出；
- 所有 case 的 `automatic_promotion_gate` 和总报告的 promotion gate 均保持 `blocked`。

第一版开发运行曾正确阻断 6 个 case。复核发现其中包含两类实验定义错误：左侧通行的行人实际使用东侧 crossing，而夹具仍审核西侧 crossing；退出 junction 后与 crossing 冲突的 turning movement 只检查了第一段 internal lane。实现随后改为绑定实际 crossing，并追踪 source lane、全部 internal continuation 和 target lane 组成的完整 occupancy path。另一个结果没有被“修成通过”：unprioritized turning movement 的 request closure 包含 `cont` 和内部 waiting junction，当前 oracle 无法独立证明完整关系，因此被正式改为 abstain/review。

以上两次 ROW-1 输出是开发实验，不证明现场道路事实、signal phase 安全或 turning movement 的完整 runtime oracle。其相同报告哈希、工具链和非声明边界现已由第 2 节所列 REVIEW_READY provenance 绑定；ROW-1 的 `automatic_promotion_gate` 仍为 blocked。
