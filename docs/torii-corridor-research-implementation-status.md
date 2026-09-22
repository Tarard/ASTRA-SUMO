# Torii-SUMO 走廊级人类建模闭环：实施状态

> **历史状态页。** 当前 active goal、Stage 1-M/1-H 切分和退出门以
> [`stage1-machine-review-ready-plan.md`](stage1-machine-review-ready-plan.md)
> 与 [`torii-corridor-human-modeling-implementation-status.md`](torii-corridor-human-modeling-implementation-status.md)
> 为准。当前 30 条多城市走廊属于 Stage 1 held-out 数据收集，不是 Stage 6；
> `REVIEW_READY` 不等于 Stage 1 退出。

更新时间：2026-07-14（Europe/Berlin）

权威研究方案：[`docs/torii-corridor-human-modeling-research-plan.md`](torii-corridor-human-modeling-research-plan.md)

本文件只记录已经由代码、测试或实测证明的状态，不替代研究方案，也不把未完成阶段描述为成功。

## 当前结论

- Stage 0“规范冻结”已完成并推送：研究方案、稳定 ID、typed contracts、候选 DAG、scope/boundary-port、workflow state、toolchain lock、benchmark v1、JSON Schema 和 CI 已建立。
- Stage 1“Audit-only”正在实施，核心架构、完整单故障矩阵、首版组合故障交互矩阵、首版适用域/OOD 矩阵、SUMO 1.27.1 官方规范场景矩阵和 held-out 盲审执行合同已落地，但尚未达到阶段退出条件。尚缺真实 held-out corridor 的双人决定、第三人 adjudication 和由这些人工证据产生的最终统计。
- Stage 2–7 与 MGE-1 尚未完成，不能宣称完整项目或任意城市自动清洗已经实现。
- 当前产品承诺仍是 selective automation：高精度窄编辑类自动执行，其余输出候选、证据和 review case；不确定时 abstain/block。

## 已实现并验证

### 身份、规范与可复现性

- source/candidate 路径和 SHA-256 分离，禁止同路径或同内容冒充候选。
- artifact manifest 使用逻辑产物名称、内容哈希、schema、producer 和 toolchain 构建闭合 DAG。
- source 运行前后重新哈希；审核路径只读。
- 显式 traffic-side contract；`.net.xml` 的 `lefthand` 与外部证据冲突时硬阻断。

### 稳定语义图与精确差分

- 从 `.net.xml` 构建 physical cell、boundary port、approach、lane role、movement、internal path、request/foes、signal group、controller/program 和 safety coverage 实体。
- internal edge ID、TLS ID 和 connection 顺序不进入语义身份；原始 ID 只用于诊断映射。
- exact entity signature diff 与 exact finding-witness diff 已实现；同类别“一处消失、一处新增”不能再通过数量抵消。
- 旧 Connection Mode differential gate 也升级为 exact raw-witness 比较；它保留 category count 作为粗筛，并明确提示 promotion 应使用稳定实体版审核。

### Connection Mode 审核

- lane-to-lane、via、完整 internal continuation、target lane、request/foes、TLS/linkIndex、phase state length、shared signal group 和 connection completeness 均可代码审核，不要求每次打开 NetEdit。
- internal path 使用 internal 子图大小约束的有界遍历，不再使用固定 16-hop 失败门；超过 16 只生成 unusually-long review finding。
- lane rank 统一为 curb-to-inner，curb/inner 角色随通行侧变化。
- promotion-grade 接口不使用固定 2 m 默认值，必须传入预注册参数或 source-hash-bound calibration artifact。

### 独立安全图

- movement conflict graph 从 internal path geometry/lane envelope 独立计算，不信任同一生成过程产生的 request/foes。
- 每个 physical cell 生成 safety coverage；未映射 controlled link 和 `linkIndex2` 为硬安全失败。
- pedestrian facility、bicycle-only、pedestrian-only、rail 或未知模式在尚未认证时进入 review 并阻断自动晋级。
- 冲突分为 `confirmed` 与 `potential`：centerline crossing、shared-destination merge 和 collinear overlap 可作为 confirmed；仅 lane-envelope proximity 不能冒充确定安全错误，只生成精确 review。
- protected `G`、permissive `g` 和 shared signal group 均通过独立 conflict graph 审核。
- 两条 movement path 在各自内部顶点相交时会被识别为 confirmed centerline crossing，不再被 segment endpoint 逻辑误降级成 envelope proximity。
- 合法 `--no-internal-links` 网络被显式识别，不再被误报为 missing-via；由于缺少 internal geometry，独立安全结论保持 `review` 并阻断自动晋级。
- 无静态 `tlLogic` 的 SUMO runtime rail controller 可合法使用 `linkIndex=-1`，不再被普通道路 TLS 规则误判为结构错误。
- permissive `g` 与冲突 movement 同时放行时，若尚无独立 yield closure，输出精确 review，而不把“缺少证明”冒充“已经证明的安全缺陷”。

### 合成故障注入 benchmark

- `synthetic_fault_matrix.v1.json` 绑定冻结的 `benchmark.v1.json` SHA-256，覆盖其全部 23 个 fault family，并包含 right-hand 与 left-hand fixture。
- runner 从不可变 clean gold 网络生成独立 mutant，不原地修改 fixture；每个 case 输出 mutant network、Connection Mode、independent safety、exact semantic diff、case result、总报告和 manifest。
- 23 个 case 全部通过预注册 gold expectation：16 个必须被结构/安全审核直接检出，7 个因证据不足或适用域未认证必须精确 abstain。
- H4 的合成反例已通过：request/foes 与旧 Connection Mode 可保持自洽 `pass`，但相交 movement 同时 protected `G` 会被独立 conflict graph 判为 hard safety block。
- pedestrian 与 rail case 的合成冲突可被独立几何图检出，但因为对应 mode applicability 尚未认证，仍维持 `must-abstain`，不能据此宣称 Stage 5B 已完成。
- clean vehicle fixture 的 right-hand、left-hand 和 parallel-lane 版本均为 `pass`；含 pedestrian/rail 的 clean fixture 按设计为 applicability `review`，不伪装成已认证自动域。

### SUMO 1.27.1 官方规范场景 benchmark

- 冻结 Eclipse SUMO `v1_27_1`、resolved commit `7717f2379d9e314a0c81c5cec748444de06a2a91` 的最小输入，并逐文件记录 upstream 与 vendored SHA-256、许可证和 notice。
- 统一 runner 覆盖 9 类场景：edge-to-edge、lane-to-lane、joined junction、no-internal-links、NEMA 四叉、NEMA grouped signal index、pedestrian crossing/walkingarea、rail crossing 和 on-ramp。
- 每个场景用锁定 netconvert 1.27.1 生成两次；原始文件保留各自哈希，去除时间戳/绝对输出路径注释后的 canonical XML 哈希必须一致。
- 本机实测 9/9 netconvert 双重生成通过、9/9 canonical semantic replay 一致、9/9 SUMO load 通过、0 个 Connection Mode structural finding、source hash 全部保持不变。
- 这些场景是 parser/fail-closed 规范回归，不是 teacher 网络：pedestrian 仍因 controlled pedestrian link 未进入独立模型而 `blocked`；rail、no-internal、ramp、NEMA 等保持精确 `review` 或 abstention，不伪装成自动认证成功。

### 组合故障交互 benchmark

- `composite_fault_matrix.v1.json` 同时绑定冻结的总 benchmark 和 23-family 单故障 benchmark 哈希；每个组成故障只能沿用已预注册的 mutation 与 gold witness。
- 10 个双故障 case 覆盖 lane binding、internal path、geometry、request/foes、TLS program/link index、lane ordering、pedestrian 和 rail 的跨层交互。
- 每个组成故障必须至少拥有一个不与同 case 其他组成共享的独占 witness；整体被阻断不等于组成故障均被检出。
- 本机实测 10/10 case、20/20 组成故障、22/22 gold observation 通过，所有 clean source fixture 在运行前后哈希不变。
- 已冻结负对照：`rail-conflict + phase-state-length` 中，错误 phase state shape 会遮蔽 protected rail conflict；runner 因 rail component witness 缺失明确失败，即使网络整体仍被其他结构错误阻断。
- 该结果证明当前审核器能发现一组预注册的跨层遮蔽问题，不代表真实 OOD 组合、pedestrian 或 rail 安全域已经认证。

### Selective-domain 与 OOD benchmark

- `vehicle_x4_certification_envelope.v1.json` 将当前窄适用域显式固定为：左右侧通行、单物理四叉、vehicle-only、static 单 program、单 movement signal group、完整 internal geometry、无 `linkIndex2`、无 pedestrian facility、无 ramp/grade separation。
- envelope 内容使用稳定 policy ID，OOD benchmark 同时绑定 envelope 和总 benchmark 的 SHA-256；修改声明边界后必须产生新身份和新实验。
- 新增实际网络 fixture：clean T3、clean X5、bicycle、no-internal-links、ramp、actuated、multi-program，以及一个 controller 跨两个独立物理四叉口的 shared-controller pair。
- 16-case 预注册矩阵包含 12 个域外案例和 4 个域内负对照；本机实测 OOD recall 12/12、in-domain acceptance 4/4、overconfidence 0、false OOD 0，所有 source fixture 哈希不变。
- `protected-green-conflict` 被保留为 in-domain，但独立安全状态为 `blocked`；T3、X5、ramp、actuated、multi-program 和 shared-controller 可以结构/安全 `pass`，仍因不在当前认证域而阻断。这保证 domain shift 与 defect 是两个正交判断。
- 这只是合成适用域回归，不是现实 OOD 泛化率；真实 city/morphology-held-out 数据仍必须单独统计。

### Held-out corridor 双人盲审门

- 预注册 `held_out_review_preregistration.v1.json`：30 个 case、至少 3 个 held-out city group、6 类 morphology、每城市至少 5 个 case，同时覆盖左右侧通行和 pedestrian/bicycle/ramp/rail/bridge/tunnel。
- reviewer-visible dataset 与 restricted unblinding key 物理分离并分别哈希；前者只含随机 case/variant code，后者保存真实 ID、machine label、safety-critical 标记和 strata。
- 两名固定 reviewer 必须逐案独立提交带时区的开始/结束时间、观察事实和理由，并显式证明 machine recommendation 与 peer decision 被隐藏。
- 第三名 adjudicator 必须逐案绑定两份 decision ID；缺少 reviewer、重复 decision、错误 variant、错误 trial、错误 adjudicator 或未闭合哈希均阻断。
- evaluator 已实现 raw agreement、Cohen's kappa、attention precision/recall、auto precision/coverage、abstention rate、median review time、strata coverage 和 safety-critical false negative 统计。
- 合成合同测试可证明完整、互相一致的人类证据能够通过，也可证明单个 safety-critical false negative 或任何缺失人工证据会保持 `blocked`；这不是实际人工实验结果。

### 容差校准与 MCP 可用面

- source baseline calibration 使用坐标序列化精度、endpoint gap 分布和 lane-width cap 推导 endpoint tolerance。
- calibration 发现 gross source gap、样本不足、坐标精度未知或 traffic-side 冲突时 fail closed。
- 新增 MCP 工具：
  - `sumo_network_connection_mode_calibration`
  - `sumo_network_exact_semantic_regression_audit`
- 后者同时运行稳定语义差分、Connection Mode、独立安全、source immutability 和 hash-closed manifest。

## 真实 Ingolstadt 只读证据

冻结输入：

`outputs/ingolstadt_full_authoritative_20260714_retry18/sumo/ingolstadt_authoritative18.net.xml`

SHA-256：

`52114063a325b26f1b50ca08d4686697cc03ea1767dce1dc3815f4a5ae362f57`

2026-07-14 的 Stage 1 只读实测：

- 914 条 canonical movement。
- 1,846 个 endpoint interface sample。
- 0 条无法追踪的 internal path。
- 坐标精度 0.01 m，median lane width 3.0 m。
- endpoint gap 的 99.5% 分位数和最大值均为 0.0 m。
- 校准 endpoint tolerance 为 0.02 m，calibration status 为 `pass`。
- 219 个 controlled connection 全部进入 canonical movement model；0 个 unsupported controlled link；0 个 `linkIndex2`。
- 独立图发现 0 个 confirmed protected-green conflict、1 个 potential lane-envelope review。
- 该 potential case 是两条相邻直行 lane 的短 internal shape 在约 3.19 m 的 envelope proximity，不被误报为 confirmed safety failure。
- 当前结论为 `review`，automatic promotion 仍为 `blocked`；这不是清洗成功声明。

## 当前阶段尚未满足的退出条件

- independent conflict graph 尚无真实 held-out gold 标注和由人工 adjudication 产生的 precision/recall。
- 组合故障 benchmark 目前是 10 个预注册双故障；尚未覆盖随机三重及以上故障和未知 mutation。首版分布外结构 OOD 分组已落地，但尚无真实城市 OOD 统计。
- pedestrian-aware conflict、rail 专用安全、bicycle 专用语义尚未认证。
- 盲审 package/evaluator 已实现，但尚无 30-case 真实双人决定和第三人 adjudication；因此 review time 与 reviewer agreement 仍没有实测结论。
- Stage 2 的 certified micro-repair 还没有逐编辑类 held-out 认证证据。
- H_S/H_M/H_P physical-cell hypothesis engine、boundary-port geometry solver 和 MGE-1 尚未实现完毕。

## 下一实施顺序

1. 按已冻结的 blind-review policy 填充 30 个真实 held-out corridor case，执行双人审核和 adjudication，生成不可事后调阈值的统计报告。
2. 为组合故障增加随机三重及以上故障和未知 mutation；单故障、双故障与首版结构 OOD 已完成。
3. 实现 Stage 2 仅限可局部证明的 micro-repair，所有操作具备 precondition、forward/inverse patch 和 exact delta。
4. 实现 H_S/H_M/H_P 三假设并行，不再从 shared TLS 推导 physical merge。
5. 实现 boundary-port constrained road-ribbon solver，执行 MGE-1 的 V0–V4 盲化比较。
6. 在上述门通过后再推进 vehicle-only NEMA、pedestrian-aware signal、多城市走廊和城市级队列。
