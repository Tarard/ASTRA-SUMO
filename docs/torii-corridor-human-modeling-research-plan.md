# Torii-SUMO 走廊级人类建模闭环研究计划

> 审查基线：\`codex/ingolstadt-osm-baseline\`，Commit \`6d1400c\`，Draft PR #4。
> 文档状态：本项目后续研究、架构重构、实验和产品声明的权威执行规范。
> 实施原则：未满足本文件定义的硬门、退出条件和适用域时，不得宣称完成。

> **2026-07-14 执行修订：** 当前 active goal 是
> [Stage 1-M — Machine REVIEW_READY](stage1-machine-review-ready-plan.md)。它只完成机器证据冻结、无损审核压缩、盲审材料和抽样协议；所有自动晋级继续阻断。Stage 1 只有在后续 Stage 1-H 完成两名独立审核者、第三名 adjudicator 和预注册统计门后才可退出。当前 30 条多城市走廊是 Stage 1 held-out 数据收集，不是 Stage 6 泛化验证。

文中标记含义：

- **【证据支持】**：当前仓库、实验或官方规范已经支持。
- **【待验证假设】**：方向合理，但尚无充分实验结论。
- **【实验问题】**：必须通过预先定义的实验回答。
- **【禁止承诺】**：当前及可预见阶段不应向用户宣称。

---

## 1. 执行摘要和可行性结论

### 1.1 总体结论

Torii-SUMO 当前最有价值的成果，不是某个具体清洗算法，而是已经形成了正确的安全边界：

> 不修改源网络；生成独立候选；绑定输入、工具链和哈希；执行结构、语义、运行时和范围外差分审核；不确定时阻断；把无法自动决定的问题转入精确人工审核。

这一方向应继续。

当前“边界几何混合”实现应保持暂停状态。它只能作为待比较的实验基线，不能成为默认几何策略。仓库交接文档也明确说明：该策略只通过了 lint 和既有单元测试，尚未完成新的 Ingolstadt 全网 source/candidate 差分验证。此前完整 teacher geometry、保守局部重放和共享 TLS 整体合并候选均已暴露不同类型的目标内或范围外回归。

### 1.2 现实可行的目标

**【证据支持】** 下列能力现实可行，并且当前项目已经部分建立：

1. 从 \`.net.xml\` 重建 lane movement、internal path、request/foes、TLS/linkIndex 关系。
2. 检测结构破损、潜在车道语义异常和范围外回归。
3. 为严格限定的局部编辑类型生成可逆候选。
4. 将多个候选作为互斥假设提交审核。
5. 用 manifest、哈希、回滚数据和 display-only \`additional.xml\` 建立可追溯闭环。
6. 对自动无法证明的位置主动 abstain，而不是给出伪成功。

当前同 bbox Ingolstadt 审计覆盖了 2,274 个 junction 和 13,169 条 movement，所有 internal path 均通过现有结构检查，但仍留下 110 个语义审核 junction。这正说明“结构通过”和“建模正确”是两个不同层次。

### 1.3 不能承诺的目标

**【禁止承诺】**

> “任意 OSM 地区完全自动达到有经验的人类 NetEdit 建模质量。”

这个承诺不可验证，也不符合问题本质，原因不是算法暂时不足，而是输入经常不存在唯一答案：

- OSM 的 lanes、turn:lanes、restriction、信号灯、sidewalk、crossing 等信息可能缺失、不精确或只表达部分事实；缺少 turn 标签也不等价于禁止该 movement。
- 人工 SUMO 网络包含建模目的、仿真粒度、局部简化和控制策略选择，不是纯粹从 OSM 可逆推导的真值。
- 实际信号配时还依赖流量、速度、检测器、行人需求、饱和流率和现场安全设计；仅凭道路拓扑不能恢复真实控制方案。
- 两个质量都很高的人工网络可能在 junction 拆分方式、internal geometry、lane grouping 和 signal grouping 上不同，但在仿真语义上等价。

长期产品目标应改写为：

> 在明确适用域内，以高精度自动执行经过认证的编辑类型；对其余情况生成有证据的候选和审核任务；持续扩大自动覆盖率，但不牺牲安全性和可解释性。

核心指标不是单一“自动化率”，而是两个独立量：

$$
\mathrm{AutoPrecision}=P(\mathrm{正确}\mid\mathrm{自动执行})
$$

$$
\mathrm{AutoCoverage}=P(\mathrm{自动执行})
$$

系统应先约束 AutoPrecision，再逐步提高 AutoCoverage。不得通过降低 abstention 来制造“自动化率提升”。

### 1.4 当前最明显的架构问题

1. **拓扑决策和几何求解耦合。** 当前正在用几何变形弥补一个尚未被证明正确的 junction 合并假设。若局部几何约束不可满足，这可能是“拓扑假设错误”的证据，而不是继续扩大变形范围的理由。
2. **teacher 坐标被赋予了过高权重。** teacher 应提供语义、结构模式和候选排序信息，不应天然成为坐标真值。
3. **审核器存在自洽性陷阱。** 如果 internal geometry、request/foes 和 TLS 都由同一生成过程共同产生，它们可能彼此一致但共同错误。必须增加独立的 movement conflict 计算。
4. **范围外差分仍偏重类别计数。** 当前按 junction 和 finding category 比较能发现大量回归，但同一类别中“一个问题消失、另一个新问题出现”可能在计数上互相抵消。
5. **研究原型继续堆入巨型模块。** \`osm_workflow.py\` 和 \`junction_rebuild_candidate.py\` 已经分别约 8,900 行和 12,800 行；继续在其中添加几何特例将快速失去实验可归因性。

---

## 2. 正式的问题定义与能力边界

### 2.1 输入与输出

定义：

- $O$：带时间戳和哈希的 OSM 数据快照。
- $S$：由固定 SUMO 工具链和参数生成的源网络。
- $R$：可选 teacher/reference 网络。
- $E$：证据集合，包括 OSM 标签、restriction、teacher 特征、人工地图观察、工具输出。
- $\Omega$：声明的编辑作用域。
- $\partial\Omega$：作用域与未修改网络之间的边界端口集合。
- $C_v$：候选 variant。
- $\Delta_v$：候选声明执行的语义修改。
- $Q_v$：候选声称满足的质量主张。
- $T$：netconvert、SUMO、脚本、参数、随机种子和版本组成的工具链身份。

系统输出必须属于：

$$
a\in\{\mathrm{auto\text{-}repair},\mathrm{suggest},\mathrm{review},\mathrm{block}\}
$$

### 2.2 “正确清洗”的正式定义

一个 junction 或 corridor 不能因为“看起来像 teacher”而被定义为正确。应定义为：

$$
\mathrm{Accept}(C_v)=
I_{\mathrm{identity}}
\land I_{\mathrm{repro}}
\land I_{\mathrm{struct}}
\land I_{\mathrm{boundary}}
\land I_{\mathrm{semantic}}
\land I_{\mathrm{safety}}
\land I_{\mathrm{runtime}}
\land I_{\mathrm{review}}
$$

其中：

- $I_{\mathrm{identity}}$：source 和 candidate 路径、内容、哈希及角色明确。
- $I_{\mathrm{repro}}$：同一锁定工具链和输入可重现候选及证据。
- $I_{\mathrm{struct}}$：XML、lane graph、internal path、request、TLS 等结构完整。
- $I_{\mathrm{boundary}}$：未声明修改的范围外及边界端口语义不变。
- $I_{\mathrm{semantic}}$：车道、方向、权限、道路等级、movement 和多模式语义有证据支持。
- $I_{\mathrm{safety}}$：冲突 movement、信号状态、行人和铁路安全条件成立。
- $I_{\mathrm{runtime}}$：SUMO load、collision-aware smoke test、route completion 等通过。
- $I_{\mathrm{review}}$：所有受保护或无法机器证明的变化已有绑定候选哈希的人工决定。

还应满足：

$$
D_{\mathrm{outside}}(S,C_v,\Omega)=\varnothing
$$

这里的 $D_{\mathrm{outside}}$ 不是简单元素数量差，而是稳定语义实体差，包括：

- junction 物理身份；
- boundary port；
- approach；
- lane role；
- movement；
- internal path；
- conflict relation；
- TLS signal group；
- multimodal connectivity。

### 2.3 质量是向量，不是总分

建议正式质量向量：

$$
q(C)=(
q_{\mathrm{topology}},
q_{\mathrm{geometry}},
q_{\mathrm{lane}},
q_{\mathrm{movement}},
q_{\mathrm{mode}},
q_{\mathrm{rightOfWay}},
q_{\mathrm{TLS}},
q_{\mathrm{safety}},
q_{\mathrm{provenance}},
q_{\mathrm{reviewability}}
)
$$

这些维度采用词典序硬门：

1. 安全和结构失败不能被其他指标抵消。
2. 范围外回归不能被 teacher 相似度提升抵消。
3. routeability 提升不能抵消 lane connection 错误。
4. teacher distance 只能作为排序指标，不能作为验收门。

### 2.4 teacher 的正确地位

teacher 只承担四个角色：

1. 提供可能的物理 junction 分解或合并假设。
2. 提供 approach、lane role、movement 和控制器模式先验。
3. 提供候选排序特征。
4. 为人工审核提供对照。

teacher 不承担：

- 坐标绝对真值；
- 道路 ID 真值；
- 自动修改授权；
- 实际信号配时真值；
- 范围外 OSM 几何覆盖权。

---

## 3. 核心研究问题和可验证假设

| 编号 | 假设 | 验证方法 | 证伪条件 |
|---|---|---|---|
| H1 | 稳定 movement/entity 签名比 finding 类别计数更可靠地发现范围外回归 | 对已知 mutation 和故障注入比较两种差分方法 | 新方法没有提高检测率，或产生不可接受的误报 |
| H2 | 将物理冲突单元图与 TLS controller 图分离，可降低错误合并率 | 在共享 TLS、多 stop-line 和 offset junction benchmark 上比较 | 分离后仍无法区分 split/merge，或依赖大量站点特例 |
| H3 | 固定边界端口的局部约束求解优于整条道路位移混合 | 在同一拓扑下比较 OSM、teacher copy、现有 blend、局部 solver | solver 不能同时满足边界、路径连续性和范围外零回归 |
| H4 | 独立 movement conflict graph 能发现 request/foes 自洽但错误的候选 | 注入共同错误的 geometry/request/TLS 组合 | 仍被审核器判为安全 |
| H5 | teacher 作为 soft prior 比直接 geometry/XML imitation 更能跨地区泛化 | city-held-out 实验和 teacher ablation | direct imitation 在 held-out 上同样稳定且无 teacher-specific 偏差 |
| H6 | 类别化 selective automation 可以同时保持高精度和逐步扩大覆盖率 | 分编辑类型测量 auto precision、coverage、abstention | 只能通过降低阻断标准提高覆盖率 |
| H7 | 严格子集中的 NEMA movement-to-phase topology 可推导，但实际 timing 不可从 OSM 单独推导 | 合成/官方场景和真实路口双层实验 | phase topology 本身也高度依赖缺失现场信息 |
| H8 | 结构化 review case 可显著降低人工审核时间和遗漏率 | 盲审交叉实验：普通报告对比 review package | 审核时间无改善，或决定一致性下降 |

### 首要研究顺序

H3 不能先于 H2 被解释为最终成功。

当前实验可以在“固定某个拓扑假设”的前提下回答几何可行性，但即使几何成功，也只能证明：

> 该拓扑存在一个满足局部几何约束的实现。

它不能证明：

> 该拓扑就是现实道路应采用的建模方式。

---

## 4. 建议的总体架构与模块职责

### 4.1 四种独立图模型

系统内部不应只有一个解析后的 SUMO XML 树。至少需要四个规范化模型：

1. **证据图 Evidence Graph**
   OSM way/node/relation、teacher 对应、地图观察、来源和置信度。
2. **物理单元图 Physical Cell Graph**
   stop line、conflict zone、approach、connector storage、crossing、grade separation。
3. **车道 movement 图 Lane Movement Graph**
   external lane、direct connection、internal lane path、目标 lane、mode permission。
4. **控制器图 Controller Graph**
   TLS controller、signal group、linkIndex、phase、ring、barrier、detector、受控 movement。

一个 controller 可以覆盖多个物理单元；一个物理单元也可能存在多个控制对象。SUMO 本身允许一个 TLS 控制多个 junction，并允许多个连接共享 signal index，因此 TLS 所有权不能用于反推物理合并。

### 4.2 模块边界

| 模块 | 责任 | 明确禁止 |
|---|---|---|
| Source Registry | 保存 OSM、source net、teacher、工具链及哈希 | 使用未绑定版本的缓存 |
| Canonicalizer | 构建稳定 junction、port、lane role、movement 签名 | 直接提出编辑 |
| Evidence Extractor | 提取 OSM、teacher、结构和地图证据 | 把缺失证据解释为反向证据 |
| Detector | 产生 finding，不修改网络 | 将 finding 自动等同于 defect |
| Hypothesis Engine | 生成 split、merge、partial-repair 等假设 | 只生成一个“最可能”答案 |
| Teacher Matcher | 对齐语义实体并输出不确定度 | 复制 ID 或坐标作为授权 |
| Candidate Planner | 定义作用域、操作、前置条件和回滚 | 写 \`.net.xml\` |
| Geometry Solver | 在固定拓扑和端口约束下求几何 | 改变未声明拓扑 |
| Materializer | 通过受控 plain XML 或窄范围 patch 生成候选 | 覆盖 source |
| Structural Verifier | 验证引用、path、request、TLS 等 | 使用 teacher 作为通过标准 |
| Independent Safety Verifier | 独立计算 conflict 和安全约束 | 直接信任 request/foes |
| Differential Verifier | 比较稳定语义实体和范围闭包 | 只比较数量 |
| Runtime Validator | load、route、collision、teleport 等 | 将 runtime pass 当作完整正确性 |
| Review Packager | 生成 JSON、HTML、additional overlay | 在 overlay 中执行网络修改 |
| Promotion Controller | 强制执行 gate 和 review closure | 接受调用方临时提高 allowance |

### 4.3 执行状态机

\`WorkflowState\` 必须成为真正的执行输入输出，而不是运行结束后的报告适配器：

\`\`\`text
INGESTED
→ CANONICALIZED
→ FINDINGS_READY
→ HYPOTHESES_READY
→ CANDIDATE_PLANNED
→ MATERIALIZED
→ STRUCTURALLY_VERIFIED
→ SAFETY_VERIFIED
→ DIFFERENTIALLY_VERIFIED
→ RUNTIME_VERIFIED
→ REVIEW_PENDING | AUTO_CERTIFIED | BLOCKED
→ ACCEPTED | REJECTED
\`\`\`

任何阶段失败后仍可生成诊断 artifact，但不能跳过失败状态进入后续晋级。

---

## 5. 路网编辑候选的数据模型

候选不应只是“一个修改后的 \`.net.xml\`”。它应是一个版本化、可逆的论证对象。

### 5.1 核心实体

| 实体 | 必要字段 |
|---|---|
| ArtifactIdentity | role、path、sha256、schema、producer、toolchain |
| ScopeSpec | target entities、guard ring、closure rules、boundary ports |
| Finding | stable ID、category、severity、subject、witness、confidence |
| EvidenceRecord | source、timestamp、spatial scope、license、hash、reliability |
| Hypothesis | type、assumptions、predicted changes、alternatives、falsifiers |
| CandidateVariant | parent、hypothesis、operations、expected delta、status |
| PatchOperation | stable targets、preconditions、forward patch、inverse patch |
| InvariantResult | rule ID、subject、pass/fail/review、machine witness |
| RuntimeResult | command、version、seed、outputs、completion、collisions |
| ReviewTask | exact question、variants、required observations、evidence refs |
| ReviewDecision | accepted/rejected/deferred、reviewer、timestamp、rationale |
| Manifest | 完整 artifact DAG、source/candidate identity、gate trace |

### 5.2 稳定语义 ID

不得依赖 netconvert 可能重新编号的 internal edge 或 connection index。应定义：

- \`physical_cell_id\`
- \`boundary_port_id\`
- \`approach_id\`
- \`lane_role_id\`
- \`movement_id\`
- \`internal_path_signature\`
- \`signal_group_id\`
- \`controller_program_signature\`

例如 movement 可由下列信息形成稳定签名：

\`\`\`text
physical cell
+ source boundary port
+ source lane role
+ destination boundary port
+ destination lane role
+ mode
+ turn class
\`\`\`

### 5.3 候选必须是 DAG

多个 variant 之间可能存在：

- 同一 finding 的互斥方案；
- 几何方案依赖拓扑方案；
- TLS 方案依赖 movement graph；
- 人工接受部分 operation 后需要重新物化新候选。

因此候选关系应为 DAG，不应在同一候选文件中逐步原地修改。

### 5.4 精确 delta，禁止数量 allowance

当前按 category count 的差分适合作为粗筛，但最终晋级必须记录：

- 新增、删除、修改了哪个稳定实体；
- 修改前后语义签名；
- 直接原因；
- 派生变化；
- 证据；
- 是否在声明作用域内。

不得使用“允许增加 3 个 crossing”一类数量豁免。现有 candidate contract 已经删除调用方直接提高语义/TLS allowance 的路径，这一边界应保留。

---

## 6. Connection Mode 审核不变量

SUMO 网络本质上包含 edge、lane、junction、lane-to-lane connection、internal lane、right-of-way 和 TLS。request 的 bitstring、connection 的 via、tl、linkIndex 具有明确结构语义；TLS index 与 junction request index 不是同一概念。

### 6.1 结构不变量：失败即 hard fail

#### A. 引用和基数

- edge、lane、junction、controller ID 唯一。
- from、to、via、tl 所有引用存在。
- fromLane、toLane 指向有效 lane ordinal。
- external edge 的 endpoint 与所属 junction 一致。
- incLanes、intLanes 不包含悬空引用。
- 删除或合并后不存在 orphan internal edge、walkingarea 或 crossing。

#### B. Direct movement

每个 direct connection 必须满足：

\`\`\`text
from edge/lane
→ direct via
→ zero or more internal continuations
→ exact declared to edge/lane
\`\`\`

要求：

- internal-links 模式下，机动车 direct movement 必须有可追踪 via。
- \`--no-internal-links\` 生成的网络必须通过显式网络模式识别，不能被统一判作 missing-via。
- internal path 有限、无环。
- 每一步 continuation 可确定。
- continuation 声明的最终外部目标与 direct connection 一致。
- path 结束 lane 与 toLane 一致。
- path 中所有 lane 的 mode permission 兼容。

#### C. 几何连续性

- external lane 尾端和第一个 internal lane 起点连续。
- internal lane 间连续。
- 最后 internal lane 和目标 external lane 起点连续。
- 容差应从未修改网络的局部分布、lane width 和空间精度校准，不能固定为全局常量。

当前审核器将 \`endpoint_tolerance_m\` 默认为 2 m，并固定右侧通行假设；这些参数适合当前 Ingolstadt 基线，但不能作为任意城市规范。

当前 internal path 追踪还使用固定 16-hop 上限。该上限应改成基于 internal 子图大小的有界遍历，并把异常长路径作为独立 finding。

#### D. Request/foes

- request index 与 junction movement ordering 有明确映射。
- response、foes 长度与 request 数量一致。
- bit ordering 明确且经官方小场景校验。
- conflict relation 满足预期对称性或有 SUMO 特定语义说明。
- cont 与实际 internal continuation 结构一致。
- 被删除 movement 不得残留 request bit。
- 新 movement 必须有 request entry。

#### E. TLS/linkIndex

- controlled movement 必须有合法 controller 和 linkIndex。
- 所有 phase state 长度一致。
- 最大 linkIndex 小于每个 state 长度。
- \`linkIndex2\` 必须被显式支持，否则阻断自动生成。
- 普通道路 TLS 必须有有效 program；SUMO runtime rail controller 单独分类。
- 多个 movement 共享 linkIndex 可以合法，但必须证明它们属于同一 signal group、始终接收相同状态，并且不会因共享索引造成冲突放行。

### 6.2 语义不变量：通常 review required

#### A. 通行侧

- right-hand/left-hand 必须来自网络配置或地区证据。
- turn lane 顺序、curb lane、inner lane 的解释依赖通行侧。
- 未确定通行侧时，不得自动执行 lane mapping 或 NEMA 绑定。

#### B. 转向语义

turn class 应由多源一致性决定：

- SUMO dir
- approach 几何
- OSM turn:lanes
- turn restriction relation
- teacher movement
- 人工观察

缺少 turn:lanes 不得解释为 movement 不存在；restriction 必须单独处理。

#### C. Lane ordering

- 同一 from/to edge pair 的 lane mapping 应保持单调，除非有明确 weave 或特殊设计证据。
- 右转、直行、左转 lane 的相对顺序合理。
- dedicated lane 和 shared lane 区分。
- lane count 变化、lane drop 和 lane addition 有局部证据。
- normalized rank jump 只能是审核启发式，不是普适错误定义。

#### D. Coverage

- 每条应参与 junction 的入口机动车 lane 至少存在一个合法 movement。
- 每条应被使用的出口 lane 至少存在一个来源。
- 但 access-restricted lane、边界 stub、停车 lane 和有证据的 lane drop 可以合法缺失 connection。

当前实现把这类缺失归为 review finding 而非结构失败，这一分类是正确的。

#### E. 多模式

- sidewalk、bicycle lane、crossing、walkingarea 的 permission 连续。
- crossing 连接到正确 walkingarea。
- bicycle movement 不被错误绑定到 motor-only lane。
- railway、tram、bridge、tunnel 和 layer 关系不被二维几何误判。
- 机动车修复不能破坏行人或自行车连通性。

### 6.3 安全不变量：失败即 hard fail

现有 request/foes 不能作为唯一安全真值。应从 movement path 几何和 lane envelope 独立构建 conflict graph：

$$
G_{\mathrm{conflict}}=(M,F)
$$

其中 $M$ 是 movement，$F$ 表示两个 movement 的时空占用存在不兼容冲突。

必须验证：

- 被标为 protected green \`G\` 的两个 movement 不得互为 conflict。
- permissive \`g\` movement 必须有明确 yield 关系。
- 共享 signal group 的 movement 不得有互相冲突。
- pedestrian crossing 和车辆 movement 的放行关系安全。
- yellow、all-red 和 phase transition 不产生直接冲突切换。
- railway movement 不得由普通道路启发式自动改写。
- 独立 conflict graph 与 SUMO request/foes 不一致时，自动晋级阻断。

### 6.4 差分不变量

当前 category-count 差分应升级为：

\`\`\`text
stable entity set
+ exact before/after signatures
+ finding witness identity
+ scope membership
\`\`\`

范围外必须满足：

- stable junction set 不变；
- boundary port 不变；
- lane role 不变；
- movement set 和 internal path signature 不变；
- request/foes 和 controller program signature 不变；
- 多模式连通性不变。

同一类别中 resolved finding 与 new finding 不得互相抵消。

---

## 7. Junction/TLS/NEMA 决策算法

### 7.1 共享 TLS 节点的三种假设

对每个共享 controller 的局部 cell，必须同时生成并评估：

- $H_S$：保持拆分，多个 junction 共享一个 controller。
- $H_M$：合并成单一物理 junction。
- $H_P$：保持物理节点，只重建部分 internal connection、request 或 signal binding。

不得从 controller membership 直接选择 $H_M$。SUMO 明确支持一个 TLS 控制多个 junction；netconvert 的 junction join 也只是基于空间和拓扑的启发式处理，并可能重新猜测连接。

### 7.2 物理单元判定证据

#### 保持拆分但共享控制器

出现下列任一强证据时优先 $H_S$：

- 存在多个独立 stop line。
- conflict regions 空间上分离。
- 中间连接段具有可容纳车辆的排队或存储长度。
- 存在中间 crossing、access、tram/rail、median opening。
- 各子 junction 有独立 request/priority 关系。
- 信号协调属于走廊控制，而非单一冲突区控制。
- 合并会使内部道路、crossing 或 lane bundle 成为不合理自环。

#### 合并为一个 junction

只有同时满足下列条件时才允许生成高置信度 merge 候选：

- 所有节点属于同一个连通 conflict envelope。
- 节点间道路只是 OSM 或 netconvert 的分段产物。
- 不存在独立 stop line 或有效中间存储。
- 不存在外部 access、crossing、rail、grade separation。
- 所有 boundary port 可无歧义映射。
- merge 后 legal movement set 保持。
- 局部几何能在固定 boundary port 下满足连续性和最小曲率约束。
- 独立 conflict graph、request/foes 和 controller graph 一致。
- 范围外 stable signatures 完全不变。

#### 只重建部分 internal connection

出现下列状态时选择 $H_P$：

- 物理节点和 stop-line 布局合理。
- external lane endpoint 和 boundary port 不需要变化。
- 问题集中在 fromLane/toLane/via、internal path、request/foes 或 linkIndex。
- 修复可以限制在一个 movement closure 或一个 controller owner closure。
- 不需要删除具有真实道路意义的连接段。

### 7.3 决策规则

采用硬约束优先、排序后置的结构：

1. 构建三个假设。
2. 对每个假设先运行 topology feasibility。
3. topology 通过后运行 geometry feasibility。
4. geometry 通过后生成 lane movement 和 controller candidate。
5. 运行独立 safety audit。
6. 剩余多个可行候选按证据、变更规模和 teacher soft distance 排序。
7. 只有一个候选满足自动认证条件时才可自动执行；否则进入人工审核。

几何不可行不得自动触发扩大 scope。它首先构成对拓扑假设的反证。

### 7.4 NEMA 自动绑定的最低证据

NEMA phase numbering 应被解释为一个标准化控制器表示，而不是对 Ingolstadt 现场控制器的复原声明。SUMO 的 NEMA controller 使用 ring/barrier 结构；同一 ring 同时只能有一个活动 phase，不同 ring 的 phase 只有在不跨 barrier 且兼容时才能并发。

自动生成 phase topology 前至少需要：

1. 明确仿真意图：\`canonical_simulation_plan\` 或 \`field_replication\`。
2. 明确通行侧。
3. 已证明是一个标准三叉或四叉物理冲突单元。
4. approach 对应关系和主次道路证据充分。
5. 所有法律允许的 movement 已完整枚举。
6. 所有禁止 movement 有 restriction 或其他证据。
7. lane-to-movement mapping 完整。
8. protected/permissive left 语义明确。
9. 独立 conflict graph 通过。
10. controller owner closure 完整。
11. 所有机动车、行人、自行车和铁路 controlled link 已纳入。
12. linkIndex 到 signal group 的映射无歧义。
13. phase state 长度和 ring/barrier 关系完整。
14. phase transition、yellow 和 clearance 有安全策略。
15. 若声称实际 timing，必须有流量、速度、检测器或现场配时证据。

### 7.5 NEMA fail-closed 条件

出现下列任一项时阻断自动生成：

- controller 跨多个物理 junction，且未建立多单元模型。
- offset、skew、channelized 或双路口结构。
- slip lane 或独立右转岛未建模。
- movement restriction 缺失或相互矛盾。
- dedicated left lane 不明确。
- protected left 与 shared lane 冲突。
- \`linkIndex2\` 或复杂 signal grouping 未支持。
- 多个 program 的选择规则不明确。
- actuated detector 配置未知但声称实际控制。
- pedestrian、bicycle 或 rail control 未覆盖。
- geometry turn 与 SUMO/OSM turn 语义不一致。
- request/foes 不完整。
- 物理主次道路评分接近，无法唯一确定 phase ownership。
- 输入落在训练或认证适用域之外。

### 7.6 对当前 NEMA 实现的评价

保留的部分：

- scan-only 默认。
- isolated controller 和 vehicle-only 严格边界。
- 非机动车、跨 junction controller、\`linkIndex2\` 等直接阻断。
- movement geometry 与 dir 不一致时阻断。
- generic timing 保持 review-required。

当前实现明确只针对 isolated、vehicle-only、标准三/四叉候选，并拒绝把通用 NEMA timing 当作现场配时。

需要重构的部分：

- 当前要求每个 arm 到其他所有 arm 都存在 movement。真实路口可能合法禁止某些转向，因此应改为“所有法律允许的 movement 均被覆盖”，而非“完整全连接 movement matrix”。
- main/minor axis 不能只靠 priority、lane count、speed 的确定性排序；接近时必须输出多个候选。
- 当前 pedestrian/internal controlled link 会使候选整体不适用，因此它仍是 vehicle-only 研究原型，不是最终行人感知 NEMA 模块。
- 德国真实信号方案不应因采用 NEMA 编号而被描述为“现场等价”。

---

## 8. 几何与拓扑协调方案

### 8.1 对当前边界几何混合的结论

当前策略：

\`\`\`text
远端保持 OSM
近端对齐 teacher
沿 approach polyline 分配位移
\`\`\`

**【待验证假设】** 它在下列严格条件下可能是有效局部候选生成器：

- teacher 和 OSM 表示同一条物理 approach。
- 拓扑及 lane cardinality 已经确定。
- 近端位移相对 approach 长度很小。
- approach 中间无 junction、access、crossing、rail 或 lane transition。
- 道路横断面近似稳定。
- teacher 与 OSM 坐标系已经进行可靠局部配准。

它不是通用正确性原则，主要风险包括：

1. 把 teacher 近端坐标当成硬真值。
2. 独立扭曲各 approach，破坏 lane bundle 之间的相对关系。
3. 只保证位置，不保证切向连续性。
4. 可能产生不合理曲率、折点或 lane offset 扭曲。
5. 不能保证 parallel lanes、sidewalk 和 bicycle lane 一致变形。
6. 可能移动不应属于目标 cell 的中间道路几何。
7. 对短 edge、相邻 junction 和双端受约束 edge 不稳定。
8. 可能通过移动 external lane 来迎合错误 internal geometry。

因此该方法应命名为：

\`one_sided_arclength_displacement_baseline\`

不得命名为“geometry repair”。

### 8.2 更稳健的方案：边界端口约束的局部道路带求解

#### A. 选取局部切割边界

不要把“远端”定义为相邻 junction。应在每个 approach 上寻找第一个稳定横断面，建立 boundary port：

- 中心位置；
- 入/出方向；
- 切向和法向；
- lane 数量；
- lane 顺序；
- lane 宽度；
- mode permission；
- sidewalk/bike/rail 信息；
- layer、bridge、tunnel；
- source geometry hash。

port 外部网络保持不变。

#### B. 固定拓扑后求几何

先选择 $H_S$、$H_M$ 或 $H_P$，再求解几何。优化变量只存在于局部 cell 和 port 内侧。

可采用 cubic Hermite、B-spline 或 elastic/ARAP 类局部变形，目标函数为：

$$
J =
\lambda_1 D_{\mathrm{OSM}}
+\lambda_2 D_{\mathrm{teacherFeature}}
+\lambda_3 \int \kappa(s)^2\,ds
+\lambda_4 \int \kappa'(s)^2\,ds
+\lambda_5 D_{\mathrm{laneBundle}}
+\lambda_6 D_{\mathrm{displacement}}
$$

硬约束：

- boundary port 位置、方向和横断面不变；
- lane order 和 mode permission 不变；
- 无自交；
- lane polygon 不反转；
- 满足最小转弯半径；
- sidewalk/bike lane 与道路带保持一致；
- crossing 与道路带发生合理相交；
- rail、bridge、tunnel 和 layer 关系保持；
- 非目标区域坐标完全不变；
- 最大局部位移受限。

#### C. teacher 只提供 soft features

teacher 可提供：

- approach attachment ordering；
- movement topology；
- stop-line 相对位置；
- internal curve 形状类别；
- lane bundle 相对偏移；
- conflict region 大致范围。

不直接复制：

- 完整 approach polyline；
- 外部 junction 坐标；
- generated internal lane vertex；
- teacher junction polygon 的绝对坐标。

#### D. external lane 与 internal lane 的职责分离

更稳健的顺序：

1. 固定 external boundary ports 和 approach road ribbons。
2. 生成 junction polygon 或 cell envelope。
3. 让 netconvert 在受控输入下重新生成 internal lanes，或由独立局部 movement solver 生成。
4. 再运行完整 Connection Mode 和独立 conflict audit。

不得先复制 internal geometry，再拉动 external roads 去迎合它。

### 8.3 几何可行性应成为拓扑判据

若在合理的 port 和位移约束下，某个 merge 假设无法得到连续、无自交、无范围外变化的几何，则：

\`\`\`text
geometry infeasible
→ reject topology hypothesis
\`\`\`

而不是：

\`\`\`text
geometry infeasible
→ 扩大位移范围
→ 移动相邻 junction
→ 继续迎合 teacher
\`\`\`

---

## 9. 三档自动化和人工审核机制

### 9.1 档位定义

| 档位 | 条件 | 输出 |
|---|---|---|
| A：认证自动修复 | 属于已认证编辑类；所有结构、语义、安全和范围门通过；无未知受保护变化 | 独立候选、证书、可晋级状态 |
| B：自动建议 | 候选结构可构造，但存在多个合理语义假设或缺少外部证据 | 排序 variants、差分、review tasks |
| C：人工裁决 | 决策依赖地图观察、现场设计意图、复杂多模式或控制策略 | 结构化 review case，机器不选答案 |
| Blocked | 候选结构、安全、证据、身份或工具链失败 | 阻断报告和 manifest，不产生成功结论 |

### 9.2 自动修复适用范围

自动档只适用于“正确性可局部决定且作用闭包可证明”的编辑类，例如：

- 精确悬空引用清理；
- 完全相同且无受保护语义的重复元素；
- 已证明无控制、无多模式、无拓扑分支的 micro fragment；
- 不改变 lane cardinality 和 connection interpretation 的确定性规范化。

每个编辑类型独立认证。不得因为一个类型表现良好而批准另一个类型。

### 9.3 自动建议适用范围

默认进入建议档：

- junction split/merge；
- lane count 或 lane role 变化；
- lane-to-lane rewiring；
- sidewalk、crossing、bicycle lane 和 ramp 添加；
- internal geometry 重建；
- shared TLS owner 调整；
- NEMA movement/phase mapping；
- teacher-guided topology 迁移。

只有在后续 benchmark 中形成窄适用域和高置信度证明后，特定子类才可升入自动档。

### 9.4 Review package 数据结构

#### ReviewCase

- \`review_case_id\`
- \`source_sha256\`
- \`candidate_sha256\`
- \`scope_id\`
- \`finding_ids\`
- \`affected_stable_entity_ids\`
- \`decision_type\`
- \`machine_question\`
- \`candidate_variants\`
- \`machine_recommendation\`
- \`confidence_components\`
- \`passed_gates\`
- \`failed_or_unresolved_gates\`
- \`evidence_refs\`
- \`required_observations\`
- \`rollback_ref\`
- \`status\`

#### EvidenceRecord

- 来源类型；
- 数据提供者；
- 空间范围；
- 数据时间；
- 审核时间；
- 哈希；
- 许可和 attribution；
- 观察事实；
- 可靠性；
- 是否可自动复现。

#### ReviewDecision

- accepted、rejected、deferred 或 invalidated
- 精确 variant ID 和候选哈希
- reviewer
- 带时区时间
- 逐 finding 决定
- 观察事实
- rationale
- evidence refs
- rollback
- source/toolchain freshness check

### 9.5 additional.xml 的角色

当前架构合理：

- candidate \`.net.xml\` 是操作性对象；
- JSON 是事实、证据和决定对象；
- manifest 是身份和 provenance 对象；
- \`additional.xml\` 只是视觉索引。

SUMO 的 POI 和 polygon 本来就是显示与调试元素，不应承担道路语义或控制器行为。

overlay 中只应存：

- review case ID；
- finding category；
- severity；
- candidate hash；
- affected stable IDs；
- 简短说明。

完整证据不应塞进 \`param\`。

### 9.6 地图证据

- OSM 数据及派生数据库需记录 ODbL attribution 和相应义务。
- 商业地图或影像只能作为人工观察来源，除非许可明确允许自动获取、保存和再分发。
- manifest 中保存“审核人在某时对某坐标观察到的事实”，而不是默认保存或分发商业影像副本。
- 地图证据过期、时间范围不一致或来源不明时，决定失效。

---

## 10. Benchmark、指标与实验设计

### 10.1 三层 benchmark

#### 第一层：合成和故障注入

构建可组合场景矩阵：

**拓扑维度**

- T 型、标准四叉、五叉；
- offset junction；
- skew junction；
- divided road；
- paired intersections；
- channelized turns；
- shared controller；
- split versus merged；
- left-hand versus right-hand。

**Lane 维度**

- 1–4 lane；
- dedicated left/right；
- shared left-through；
- lane drop/addition；
- bus/bicycle/pedestrian lane；
- access restriction；
- asymmetric incoming/outgoing lanes。

**Movement 故障**

- wrong toLane；
- crossed lane mapping；
- duplicate connection；
- missing connection；
- incorrect via；
- internal path cycle；
- target mismatch；
- dangling internal lane；
- endpoint gap；
- wrong turn direction；
- legal prohibited movement；
- illegal missing movement。

**Right-of-way/TLS 故障**

- request bit 长度错误；
- bit ordering 错误；
- asymmetric foes；
- conflicting protected green；
- shared conflict linkIndex；
- out-of-range linkIndex；
- inconsistent state lengths；
- missing program；
- \`linkIndex2\`；
- pedestrian conflict；
- rail conflict。

每个场景必须同时有：

- clean gold network；
- 单一故障版本；
- 多故障组合版本；
- 预期 finding；
- 预期修复边界；
- 预期 abstention。

#### 第二层：SUMO 官方小场景

至少纳入：

- 官方 PlainXML edge-to-edge 和 lane-to-lane connection 示例；
- joined junction 场景；
- internal-links 和 no-internal-links 两种网络；
- NEMA 标准四叉和 on-ramp 示例；
- pedestrian sidewalk、crossing、walkingarea 教程；
- rail signal/rail crossing 场景；
- signal group 共享 linkIndex 场景。

SUMO 官方文档已经提供 connection、joined junction、NEMA 和 pedestrian 构造示例，可作为规范回归而非 teacher imitation 数据。

#### 第三层：真实 OSM 走廊

Ingolstadt 只作为 development set。held-out 必须覆盖：

- 规则网格；
- 历史城区；
- 郊区主干道；
- divided arterial；
- ramp/interchange；
- tram/rail；
- bridge/tunnel；
- 多行人和自行车设施；
- 左侧通行地区；
- 多节点共享 controller；
- 不同 OSM 完整度等级。

数据划分以城市和道路形态为单位，不能把同一 junction 的邻近切片分入训练和测试。

每个真实 corridor 至少由两名独立人工建模者或审核者处理，再进行 adjudication。teacher disagreement 本身应成为 benchmark 字段。

### 10.2 核心指标

#### 检测指标

- 每个故障类别的 precision、recall、F1。
- safety-critical false negative 数量。
- 结构错误与 review ambiguity 的分类准确率。
- OOD 检测率。

#### 候选指标

- stable movement graph exact match。
- movement precision/recall。
- lane-role exact match。
- boundary port preservation。
- outside-scope exact semantic delta。
- declared versus actual delta。
- candidate edit size。

#### 几何指标

- endpoint gap；
- tangent mismatch；
- curvature 和 curvature variation；
- lane-bundle offset distortion；
- self-intersection 数量；
- 最小转弯半径违规；
- boundary port displacement；
- crossing/road geometric consistency。

容差应根据 untouched baseline 分布预先固定，而不是根据候选结果调参。

#### 安全指标

- independent conflict graph precision/recall；
- protected conflict false negative；
- permissive yield mismatch；
- pedestrian conflict；
- rail conflict；
- collision-aware simulation 结果。

#### 自动化指标

- edit-class-specific AutoPrecision；
- AutoCoverage；
- abstention rate；
- calibration error；
- subgroup performance；
- 自动修复后人工 rollback 率。

#### 人工审核指标

- 每 junction 审核时间；
- 每公里 review item；
- 人工接受率；
- reviewer 间一致性；
- 漏审率；
- 结构化 package 相对普通报告的时间节省。

#### 可复现性指标

- 同一锁定工具链的 normalized semantic hash 一致；
- artifact identity 完整率；
- manifest closure；
- stale evidence 检出率；
- source immutability。

### 10.3 硬验收门

自动档必须满足：

- outside-scope structural regression：0
- outside-scope semantic/review regression：0
- target structural failure：0
- safety failure：0
- 未解释 delta：0
- source mutation：0
- artifact identity failure：0
- stale evidence：0
- runtime collision：0
- 未关闭 protected review：0

不得通过加权总分抵消任何硬失败。

#### 编辑类认证阈值

**【建议阈值】**

一个低风险自动编辑类在 held-out 中至少需要足够多的独立样本，使关键错误率有统计上界。零失败的约 600 个独立样本只能给出约 0.5% 的单侧 95% 失败率上界，因此破坏性 topology/TLS 编辑需要更强证据，且可能长期保持 review-only。

### 10.4 消融实验

#### Teacher 使用方式

- 无 teacher；
- teacher 只用于 graph matching；
- teacher 用于 candidate ranking；
- teacher 作为 soft geometry cost；
- teacher direct geometry replay。

#### Geometry

- OSM 完全保持；
- teacher 完全复制；
- 当前 arclength blend；
- rigid/affine local alignment；
- boundary-port constrained solver。

#### Topology

- split/shared-controller；
- merge；
- partial internal repair。

#### Audit

- load + routeability；
- 加 Connection Mode；
- 加 exact semantic diff；
- 加 independent conflict graph；
- 加 multimodal/rail；
- 完整 gate。

#### Diff

- 数量差；
- category count；
- stable entity exact diff。

#### 泛化

- Ingolstadt 内部交叉验证；
- city-held-out；
- morphology-held-out；
- traffic-side-held-out；
- OSM completeness strata。

---

## 11. 分阶段路线图及每阶段退出条件

| 阶段 | 工作范围 | 退出条件 |
|---|---|---|
| 0. 规范冻结 | 正式 schema、stable IDs、scope、port、claim taxonomy、工具链锁定 | benchmark v1 冻结；CI 可重现；所有 artifact 有 schema/hash |
| 1. Audit-only | 不生成修复，只检测结构、语义和安全问题 | 合成结构故障全部检出；官方场景低误报；held-out reviewer agreement 可接受 |
| 2. 认证 micro-repair | 只处理局部可证明编辑类 | 每类独立达到认证阈值；outside delta 永远为零 |
| 3. Physical-cell hypotheses | 同时生成 split、merge、partial | held-out 上错误 merge/split 被可靠阻断；无 controller-topology 混淆 |
| 4. Local geometry solver | boundary-port 约束求解 | 优于 current blend；无范围外变化；geometry feasibility 可作为 topology 反证 |
| 5A. Vehicle-only NEMA topology | 仅严格标准、无行人和共享 controller | movement、conflict、ring/barrier 全通过；实际 timing 不自动声明 |
| 5B. Pedestrian-aware signal | crossing、walkingarea、clearance | 独立 pedestrian conflict benchmark 零安全错误 |
| 6. 多城市走廊 | 跨城市、形态和通行侧验证 | held-out 指标不依赖 Ingolstadt 特例；review burden 明显下降 |
| 7. 城市级运行 | 只扩大 audit 和候选队列，不扩大自动语义 | 走廊级方法在多个 held-out 地区稳定后才允许启动 |

在阶段 6 之前不得宣传“任意城市自动清洗”。

---

## 12. 主要风险、失败模式和停止条件

### 12.1 主要风险

#### 共同模式失败

生成器和审核器共享同一几何、turn 或 request 假设，可能共同接受错误候选。对策是独立 conflict engine、故障注入和不同实现路径。

#### Teacher 过拟合

通过 teacher similarity 提升掩盖现实 OSM 边界破坏。对策是 teacher-free 指标和 city-held-out。

#### Controller/physical topology 混淆

共享 TLS 被错误解释为一个 junction。对策是双图模型和三假设并行。

#### Scope 蔓延

为了修复局部 endpoint gap 不断扩大 join 或 deformation。对策是不可满足约束直接证伪候选。

#### 全网 round-trip 副作用

\`netconvert\` 重新应用 type、sidewalk、bike lane 等规则，引发 lane index 和 connection 全网变化。此前 1,828 个范围外 review regression 已经证明这是现实风险。

#### 几何看似连续但不具道路意义

endpoint gap 为零不代表曲率、lane ribbon、crossing 和车道排列正确。

#### Routeability 误导

车辆能找到一条路径不能证明每条 lane connection、signal group 或行人 movement 正确。

#### 工具链漂移

SUMO 或 netconvert 版本变化可能改变生成的 internal IDs、junction shape 和 connection guessing。

#### 审核层漂移成真值层

人工 overlay 或 HTML 中的文本不能反向成为修改授权；决定必须绑定候选哈希和结构化证据。

### 12.2 停止条件

出现以下任一项，当前实验方向立即停止，不继续增加特例：

1. 范围外出现任何新结构或语义 finding。
2. solver 必须移动 boundary port 才能成功。
3. 成功依赖复制 teacher 的绝对坐标或 ID。
4. 小幅输入扰动使结果由 pass 变为严重失败。
5. independent conflict graph 与 request/foes 不一致。
6. 方法只改善 teacher similarity，不改善独立指标。
7. 需要为单个 junction 添加专用 ID 规则或站点常量。
8. left-hand、rail、multimodal 等子组产生过度自信。
9. 同一工具链不能确定性复现。
10. 人工审核时间没有降低。
11. topology hypothesis 只有通过无限扩大 geometry scope 才可实现。
12. benchmark 结果依赖 Ingolstadt teacher 参与调参。

---

## 13. 对当前实现“保留、重构、删除”的建议

| 当前部分 | 决定 | 处理方式 |
|---|---|---|
| source/candidate 分离、SHA-256、manifest | 保留 | 作为所有研究阶段的不可绕过底座 |
| atomic artifact writing | 保留 | 扩展到所有旧报告和候选写入 |
| rollback 和 review decision binding | 保留 | 升级为逐 operation、逐 finding 决定 |
| display-only \`additional.xml\` | 保留 | 只保存 review case 索引 |
| code-native Connection Mode audit | 保留并重构 | 拆为 parser、canonical graph、invariant engine、renderer |
| source/candidate TLS scope closure | 保留 | 改用 stable entities，而非只按 junction ID |
| rail runtime controller 区分 | 保留 | 纳入统一 mode/safety model |
| strict NEMA scan | 保留为实验模块 | 明确命名为 vehicle-only strict candidate |
| synthetic NEMA tests | 保留 | 增加禁止 turn、shared group、pedestrian 和 left-hand |
| category-count regression | 重构 | 保留粗筛，增加 exact witness/entity diff |
| hard-coded right-hand assumption | 重构 | 变成显式网络属性和适用域 |
| 固定 2 m endpoint tolerance | 重构 | 由 baseline 分布和 lane scale 校准 |
| 固定 16-hop internal trace | 重构 | 使用子图有界遍历 |
| \`junction_rebuild_candidate.py\` | 冻结后拆分 | evidence、matching、hypothesis、geometry、materialization、verification、rendering 分离 |
| \`osm_workflow.py\` | 重构 | 旧接口作为 facade，执行转入 typed stages |
| shared TLS full-cell replay | 从生产路径删除 | 仅保留为 benchmark 和实验 variant |
| full teacher geometry copy | 从生产路径删除 | 仅作为负基线 |
| 当前 boundary geometry blend | 隔离 | 保留为 ablation baseline，不再继续加特例 |
| nearest node / edge-family 自动映射 | 从授权逻辑删除 | 只能作为候选证据和排序特征 |
| shared controller ⇒ physical merge | 删除 | 明确禁止 |
| 全网 plain XML round-trip 作为局部编辑手段 | 删除 | 除非能证明未声明语义完全不变 |
| direct \`.net.xml\` 通用编辑 | 删除 | 仅保留经过 schema 和 round-trip 验证的极窄 text patch |
| load/routeability 通过即成功 | 删除 | 只能作为必要 gate |
| teacher parity 即正确 | 删除 | teacher distance 仅用于诊断和排序 |
| 数量 allowance 晋级 | 删除 | 只接受 exact reviewed deltas |

当前测试、lint 和四个场景证明了候选契约和有限垂直切片可工作，但并未验证最新几何实验，也没有消除巨型模块和缺少持续 SUMO CI 的架构债务。

---

## 14. 后续几何实验（当前暂停）

MGE-1 保留为预注册的后续实验，但不再是当前主执行目标。Stage 1-M 的三个当前实验是 PCB-453、ROW-1 和 RWC-1，定义见
[`stage1-machine-review-ready-plan.md`](stage1-machine-review-ready-plan.md)。在 Stage 1-M 证据冻结前，不得以 MGE-1、Stage 3 拓扑候选或 Stage 5 控制器实验替代审核证据闭环。

### 实验名称

**MGE-1：固定拓扑下的边界端口几何可行性实验**

### 14.1 实验问题

在完全固定 junction 合并拓扑、lane cardinality、movement mapping 和 controller mapping 的前提下，局部 boundary-port constrained geometry 是否能消除 target 和 outside endpoint gap，同时保持范围外 stable semantic graph 完全不变？

本实验不回答：

- 九个节点是否应该合并；
- teacher 是否正确；
- NEMA 是否合理；
- 方法是否能推广到其他城市。

### 14.2 输入

1. 当前冻结的 Ingolstadt OSM/source net 及哈希。
2. teacher net 及哈希。
3. 已拒绝的完整 shared-controller join 候选。
4. 明确的 target cell 和一圈 guard scope。
5. 固定的：
   - junction membership；
   - boundary edge mapping；
   - lane counts；
   - mode permissions；
   - movement set；
   - TLS owner 和 linkIndex 语义。
6. 从 source OSM 提取的 boundary ports。
7. 当前所有 endpoint gap、Connection Mode 和 differential 报告。
8. 锁定的 SUMO/netconvert 版本、参数和容器。
9. 一组小幅 OSM/teacher 配准扰动，用于稳定性测试。

### 14.3 对比 variant

| Variant | 几何策略 |
|---|---|
| V0 | 完全保持 OSM external geometry，重新生成 internal geometry |
| V1 | teacher geometry 完整复制 |
| V2 | 当前 \`one_sided_arclength_displacement_baseline\` |
| V3 | teacher 局部 rigid/affine 配准，不变形远端 |
| V4 | boundary-port constrained road-ribbon solver，teacher 仅作 soft cost |

所有 variant 使用完全相同的 topology、lane semantics 和 controller semantics。

### 14.4 过程

1. 构建 stable physical-cell、port、lane-role 和 movement IDs。
2. 选择稳定横断面作为 boundary ports。
3. 冻结 port 外所有 XML 语义和几何。
4. 对 teacher 做局部刚性配准，禁止使用全局 ID 或远端坐标。
5. 分别生成五个几何候选。
6. 通过同一 materializer 和 netconvert 生成候选。
7. 对每个候选执行：
   - artifact identity；
   - XML/netconvert；
   - full-network Connection Mode；
   - stable entity exact diff；
   - independent conflict graph；
   - geometry invariants；
   - multimodal/rail preservation；
   - SUMO load；
   - collision-aware routeability；
   - target 和 outside scope 分离报告。
8. 对输入坐标和 teacher 配准施加小幅扰动，重复执行。
9. 生成盲化人工审核包，不显示 variant 名称。
10. 在实验结束前不得调整阈值。

### 14.5 预期产物

每个 variant 必须生成：

- candidate \`.net.xml\`
- topology/geometry plan
- boundary port catalog
- forward/inverse patch
- Connection Mode report
- stable semantic diff
- independent conflict report
- geometry metrics
- SUMO runtime report
- review \`additional.xml\`
- review HTML/JSON
- artifact manifest
- rollback manifest

总实验还应生成：

- 横向 comparison report
- 预注册阈值
- 失败类别统计
- perturbation robustness 报告
- machine recommendation
- blinded human decision 结果

### 14.6 成功标准

V4 只有同时满足下列条件才算几何假设通过：

1. source 路径和哈希不变。
2. port 外 geometry 和 stable semantic signatures 完全不变。
3. outside-scope 新 structural finding 为 0。
4. outside-scope 新 review finding 为 0。
5. target structural finding 为 0。
6. 所有 direct/internal paths 可完整追踪。
7. endpoint gap 不超过实验前从 untouched baseline 固定的容差。
8. boundary port 位置、切向、lane order 和横断面保持。
9. 无道路带自交、lane inversion 或最小半径违规。
10. sidewalk、bicycle、crossing、rail 和 layer 关系不退化。
11. independent conflict graph 无新增冲突。
12. SUMO collision、teleport 和未完成行程为 0。
13. 在预定义小扰动下仍保持同一 gate 结论。
14. 独立指标优于或不劣于 V0–V3。
15. 不依赖 junction ID、edge ID 或 Ingolstadt 专用常量。

### 14.7 失败标准

出现任一项即否决当前几何方法：

- 任何范围外差分；
- 任何 target path endpoint 或 target mismatch；
- 必须移动 boundary port；
- 必须扩大到相邻 junction；
- 必须复制 teacher 远端 geometry；
- geometry success 依赖专用分支；
- 小扰动导致 topology、lane order 或 gate 状态变化；
- request/foes 通过但 independent conflict 失败；
- 只有 teacher similarity 改善，独立指标未改善；
- 所有 variant 都无法满足硬约束。

如果所有固定拓扑 variant 都失败，结论应是：

> 当前完整合并 topology 不具备局部几何可行性，应回到 $H_S$ 与 $H_P$ 比较。

即使 V4 成功，结论也只能是：

> 找到了一个满足当前固定拓扑约束的局部几何生成器。

下一项实验才是使用同一 geometry framework，对 split/shared-controller、merge、partial internal repair 三个 topology hypothesis 进行盲化、held-out 比较。
