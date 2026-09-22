# Held-out corridor blind-review protocol v2

> **Frozen and executed Stage 1-M protocol.** 本协议冻结机器侧审核材料、抽样和统计设计，
> 但不构成 Stage 1 退出。Stage 1-H 仍要求两名独立审核者和第三名
> adjudicator 完成真实审核。任何自动晋级继续保持 `blocked`。

## 1. 版本关系

v1 是不可变 pilot artifact。它保留原有 30-case 和 AutoPrecision 0.99
规则，也保留 `auto_count == 0` 时 AutoPrecision 未定义并阻断的行为。不得通过
降低 case 数或把未定义值改成 1.0 使 v1 通过。

v2 使用独立的：

- schema；
- parent review benchmark；
- trial ID；
- reserve corpus；
- replacement policy 与公开排序种子；
- witness sampling policy；
- blinding-seed SHA-256 commitment；
- finding-cluster decision contract。

实际执行版本是 `v2-R2`：

- trial ID：`review_a784b285271ca7f510ed79f2`；
- execution parent：`manifest_16660e5e00eeaccc7cb93526`；
- study sampling policy：`policy_b8d470ed55e9c874512f680c`；
- 30 个 effective-corpus package 全部在抽样前冻结 machine assessment；
- reviewer-visible package 不保存 seed preimage、machine label、unit kind、sampling weight 或 hidden-member role。

冻结文件位于 `benchmarks/corridor_human_modeling_v1`：

- `held_out_reserve_corpus.v2.json`
- `held_out_replacement_policy.v2.json`
- `held_out_replacement_plan.v2.json`
- `held_out_source_snapshot_protocol.v2.json`
- `held_out_effective_corpus.v2.json`
- `held_out_replacement_attempt_ledger.v2.json`
- `review_witness_sampling_policy.v2.json`
- `held_out_review_parent.v2.json`
- `held_out_review_preregistration.v2.json`

## 2. 两个互不混用的 cohort

### 2.1 Audit-attention cohort

当前真实走廊只进入 attention cohort。审核单位是 finding cluster 或负样本
unit，不再把一条 raw pedestrian × vehicle conflict 当成一个独立人工任务。

该 cohort 测量：

- weighted attention precision ≥ 0.90；
- weighted attention recall ≥ 0.95；
- raw reviewer agreement ≥ 0.80；
- Cohen's kappa ≥ 0.60；
- median review time ≤ 300 s；
- safety-critical false negative = 0；
- 至少 30 个有效、完整 corridor package。

该 cohort 的 `auto_precision_status` 固定为 `not-applicable`。unit-level
“机器没有提出 attention”只用于估计 finding recall，不能解释为整张网络被机器
自动认证。

### 2.2 Prospective safe-pass cohort

safe-pass cohort 未来仅按时间顺序纳入机器真实输出 `acceptable` 的全部连续样本：

- 禁止从当前 defect-only corpus 回填；
- 禁止看到人工标签后选择样本；
- 至少 600 个 machine-acceptable 样本；
- AutoPrecision 点估计 ≥ 0.99；
- 95% 单侧 Wilson 下界 ≥ 0.99；
- safety-critical false negative = 0；
- AutoCoverage 只报告，不设可通过的最低值。

safe-pass 尚未启动，因此当前不得报告 AutoPrecision 或 AutoCoverage 已测得。

## 3. replay-invalid 与确定性 replacement

以下三个 corridor 只进入 reproducibility census，不进入模型质量分母：

- `london-kings-cross`
- `melbourne-royal-parade`
- `sydney-cross-city-tunnel`

reserve corpus 为每个无效 corridor 预先冻结三个同城市、同 morphology、同通行侧、
同 mode-feature tuple 的候选。排序键为：

```text
sha256(public_seed | invalid_corridor_key | reserve_selection_id)
```

按 digest 升序尝试。当前冻结的 rank-1 是：

- London → `london-liverpool-street`
- Melbourne → `melbourne-st-kilda-domain`
- Sydney → `sydney-eastern-distributor-surry-hills`

只有下列技术原因可以拒绝一个 rank 并尝试下一个：source artifact 不可用、bbox
越界、source extraction 失败、netconvert build 失败、semantic replay invalid、
artifact closure 不完整。所有失败尝试必须保留。机器标签、finding 数量/严重性、
人工决定和材料是否“容易看”均禁止参与 replacement 选择。

实际技术执行按同一 source-snapshot v2 协议从 rank-1 重新开始，结果为：

- London：rank-1 `london-liverpool-street` 通过；
- Melbourne：rank-1 semantic replay invalid，rank-2 `melbourne-dandenong-orrong` 通过；
- Sydney：rank-1、rank-2 semantic replay invalid，rank-3 `sydney-m5-east-kingsgrove` 通过。

source-snapshot v2 对被 bbox 触碰的 OSM `junction=roundabout` way 执行完整
shared-node component closure。旧 bbox-only 尝试不删除，而是以
`source-snapshot-protocol-superseded` 保留；v1 snapshot 不原地改写。每次有效尝试
绑定 clean producer revision/tree、snapshot、run identity、machine report、net replay
和 machine manifest。该结果只证明 replacement 的技术可执行性，不证明其机器 finding
正确，也不授权自动晋级。

## 4. 无损 witness 结构

每个 atomic witness 永久保留稳定 ID、完整 evidence payload 和哈希。聚类必须满足：

```text
W = disjoint_union(C_i)
```

并强制：

- membership coverage = 100%；
- lost、duplicate、extraneous witness = 0；
- confirmed 与 potential 不混合；
- conflict reason、control class、ROW class、request/foes relation、physical cell、
  grade/layer/rail 关系和 hard-safety 状态不混合；
- cluster membership 使用 Merkle root 绑定；
- visible material 包含 medoid、extrema 和 movement-family representatives；
- 一个冻结随机种子选择的 member 以普通 witness 身份混入，不向 reviewer 标记其
  hidden role；
- negative crossing × vehicle pair 采用相同身份和抽样约束。

人工决定只有在 visible representatives 与 hidden member 结论一致、硬字段一致、
未发现新子型且 purity 成立时，才可传播到整个 cluster；否则拆分 cluster。

## 5. 抽样和统计

机器层对 102,398 个 atomic witness、全部 rare/hard/OOD/rail/grade/shared-controller
实体保持全量 census；这不等于要求人工逐条审核。v2-R2 人工样本固定为：

- 每个 corridor 8 个 conflict-site unit，共 240 个；
- 每个 corridor 4 个 negative-pair unit，共 120 个；
- 两个 hard controlled-binding class 的 21 条 assessment 全量进入人工 census；
- 3 个稳定 pedestrian coverage gap 全量进入人工 census；
- 共 384 个 review unit，其中 217 个 conflict site 含独立隐藏 witness。

高频同质 strata 使用冻结随机种子抽样，并为每个 unit 保存 inclusion probability
`pi_i`。unknown-control 等机器 population 必须保留，但不会仅因类别名称而强制整层
进入人工 census；未抽中的 population 保持 unresolved。

population attention 指标使用 Horvitz–Thompson 权重 `w_i = 1 / pi_i`：

```text
weighted precision = sum(w_i * TP_i) / sum(w_i * (TP_i + FP_i))
weighted recall    = sum(w_i * TP_i) / sum(w_i * (TP_i + FN_i))
```

没有 inclusion probability 的结果只能用于界面排序，不得用于总体统计。审核者一致性
按所有实际审核 unit 计算 raw agreement 与 Cohen's kappa；同时保留分层加权诊断值，
但预注册硬门使用原始审核 unit 值。

每个大型 cluster 的 hidden member disagreement 使用 95% 单侧 Wilson 上界；上界必须
≤ 5%，且 safety-critical hidden heterogeneity 必须为 0。若 hidden 与 representative
不一致，当前 cluster 不得传播决定。

## 6. 盲化和决定身份

reviewer-visible dataset 只能包含 blind case/unit/witness code、地图位置、证据材料、
中性问题和 required observations。不得包含：

- true stable entity ID；
- machine label、finding category 或机器推荐；
- inclusion stratum 的机器含义；
- hidden-member role；
- peer decision；
- blinding seed 或 unblinding key。

正式包与独立重复包各 892 个文件，逐相对路径 SHA-256 差异为 0。manifest 绑定
891 个子 artifact；dataset、sampling ledger 和 restricted evaluation key 只通过路径与
哈希闭合。reviewer-visible scanner 对 machine label、attention role 和 hidden role 的
命中数均为 0。

restricted key 绑定 exact machine assessment artifact 的相对路径和 SHA-256。人工决定
开始后修改 assessment、membership、sampling weight、case set 或 evidence hash，均使
trial identity 失效。

每个 unit 由两名 reviewer 独立提交 label、逐 witness label、purity 判断、开始/结束时间、
观察事实和理由。第三名 adjudicator 绑定两份决定后给出最终结论。

## 7. 地图和 additional.xml

OSM、合法可用的地图链接和现场观察只作为人工证据辅助。地图观察记录事实、时间、位置、
来源和许可，不把商业影像副本默认纳入 artifact。

`additional.xml` 仍是 display-only visual index。非盲诊断包可以显示 case ID、位置、类别和
简短说明；正式盲审包只显示 blind code 和中性定位信息，不显示 finding category、severity、
machine label、unit kind 或 hidden role。
它不得包含 `tlLogic`、route、vehicle 或任何能改变网络/仿真行为的元素。候选
`.net.xml`、结构化 JSON、manifest 和人工决定分别承担操作、证据、provenance 和授权
职责。

## 8. 状态门

Stage 1-M 已在以下机器条件闭合后标为 `REVIEW_READY`：合同 CI 覆盖 v2-R2 与 provenance
schema、30 个完整盲化 package、三个 replay-invalid 单列、3 个 stable coverage gap、
冻结 PCB-453 与 effective PCB-459、冻结 RWC-1 与 effective 102,398-witness census、
ROW-1 的两次相同开发报告，以及 clean producer 和完整 artifact DAG。权威记录见
[`stage1m_machine_review_ready_provenance_20260714.v3.json`](../benchmarks/corridor_human_modeling_v1/evidence/stage1m_machine_review_ready_provenance_20260714.v3.json)。

即使 Stage 1-M 通过：

- `stage_1h_human_validation_gate` 仍为 `blocked`；
- `automatic_promotion_gate` 仍为 `blocked`；
- Stage 1 未退出；
- 不得宣称任意城市自动达到专家 NetEdit 质量。
