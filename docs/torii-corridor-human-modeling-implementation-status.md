# Torii-SUMO 走廊级人类建模闭环实施状态

更新时间：2026-07-14

本文件只记录可复现的实施证据，不改变
`torii-corridor-human-modeling-research-plan.md` 的验收门。

当前 active goal 与机器侧退出门以
[`stage1-machine-review-ready-plan.md`](stage1-machine-review-ready-plan.md)
为准：`Stage 1-M REVIEW_READY != Stage 1 exit`。完整 Stage 1 仍需后续
Stage 1-H 的两名独立审核者、第三名 adjudicator 和预注册统计门。

## 阶段状态

| 阶段 | 状态 | 当前证据或阻断项 |
|---|---|---|
| 0. 规范冻结 | 完成；合同 CI 硬门已扩展 | schema、稳定语义 ID、工具链、manifest 和 source/candidate 身份已冻结；确定性 schema 重生成已覆盖 v2-R2、盲审包与 REVIEW_READY provenance，相关合同测试进入同一 CI。 |
| 1-M. Machine REVIEW_READY | **REVIEW_READY** | 30/30 effective corridor pipeline 与 semantic replay 通过；PCB-453 保留并扩展为 PCB-459；RWC-1 保留并扩展为 102,398 个 witness；3 个 stable coverage gap、ROW-1、384-unit v2-R2 盲审包、确定性重复和完整 provenance 已冻结。Stage 1-H 与所有 promotion 仍 blocked。 |
| 1-H. Human Validation | 未开始 | 必须由两名真实审核者独立作答，第三人 adjudication，并通过预注册统计门；机器不能代填。 |
| 2. 认证 micro-repair | 认证暂停；development-only 可并行 | 合成/官方场景的开发研究允许在隔离语料继续；不得使用 Stage 1 blinded held-out 调参或宣称编辑类型已认证。 |
| 3. Physical-cell hypotheses | 未完成 | split/shared-controller、merge、partial 三假设尚未完成统一 held-out 比较。 |
| 4. Local geometry solver / MGE-1 | 暂停 | 当前 arclength blend 仍仅是消融基线；MGE-1 保留为后续实验，不再是 Stage 1-M 主目标。 |
| 5A/5B. NEMA 与行人安全 | 未完成 | vehicle-only 严格扫描、受控和无信号 crossing 独立冲突审核已存在；无信号 right-of-way、自行车、共享 controller、clearance 和实际 timing 均未认证。 |
| 6. 多城市迁移 | 未开始 | 当前 30 个走廊、6 个城市及左右侧通行快照属于 Stage 1 held-out 数据收集，不是 Stage 6。Stage 6 必须等待前序方法完成认证后再验证迁移。 |
| 7. 城市级运行 | 未开始 | 阶段 6 未退出前不得启动自动语义扩张。 |

## 已冻结的真实 held-out 语料

- 30 个走廊，Berlin、Amsterdam、Paris、London、Melbourne、Sydney 各 5 个。
- 左右侧通行分开；目标覆盖 pedestrian、bicycle、ramp、rail、bridge、tunnel。
- 城市源使用 BBBike 发布的 OSM PBF；provider MD5、HTTP identity、下载字节数和本地 SHA-256 均进入 manifest。
- 裁剪使用 reference-complete writer，并保留被引用交通信号灯和 crossing 节点标签。
- 后续 v2 snapshot 构建对被 bbox 触碰的 OSM `junction=roundabout` way 执行完整同节点组件闭包；否则 bbox 外的一段环岛会被漏掉，并在 netconvert 中变成伪 `dead_end` 和非确定性 roundabout 分组。已冻结的 v1 snapshot 不原地改写。
- feature targets 是预注册待验证目标，不是事后真值；冻结快照不支持时必须报告 review/replacement。
- OSM 派生物明确记录 `© OpenStreetMap contributors` 和 ODbL。

## 已冻结的盲审 v2

- v1 保持不可变 pilot，不降低 30-case 门，也不把未定义 AutoPrecision 改成 1.0。
- v2 attention cohort 只测 weighted attention precision/recall、审核者一致性、审核时间和 safety false negative；AutoPrecision 明确为 `not-applicable`。
- future safe-pass cohort 只接受前瞻性、连续的 machine-acceptable 样本；至少 600 个样本，AutoPrecision 点估计和 95% 单侧 Wilson 下界均须达到 0.99。
- `london-kings-cross`、`melbourne-royal-parade`、`sydney-cross-city-tunnel` 只进入 reproducibility census。
- 三个 replacement slot 各有三个预先冻结候选，并按公开 seed 与 stable selection ID 的 SHA-256 排序；机器标签、finding 数量/严重性和人工可见性不得参与选择。
- 最终技术选择为 London rank-1 `london-liverpool-street`、Melbourne rank-2 `melbourne-dandenong-orrong`、Sydney rank-3 `sydney-m5-east-kingsgrove`。Melbourne rank-1 及 Sydney rank-1/rank-2 的 normalized semantic replay failure 均保留；机器标签、finding 数量/严重性和人工可见性未参与选择。
- 原 bbox-only 裁剪下的尝试保留为 superseded execution；新 trial 输入使用 `held_out_source_snapshot_protocol.v2.json`，并以 `held_out_replacement_attempt_ledger.v2.json` 绑定 clean producer、source snapshot、run identity、machine report、net replay 和 manifest 哈希。
- 完整协议见 [`held-out-corridor-blind-review-protocol-v2.md`](held-out-corridor-blind-review-protocol-v2.md)。
- 正式 v2-R2 包含 30 个 blinded corridor package 和 384 个 review unit：240 conflict site、120 negative pair、21 hard controlled binding、3 coverage gap；217 个 conflict site 带隐藏 witness。
- 正式包与独立重复包各 892 个文件，逐路径哈希差异为 0；reviewer-visible machine label、attention role 和 hidden role 泄漏均为 0。
- 权威机器证据见 [`stage1m_machine_review_ready_provenance_20260714.v3.json`](../benchmarks/corridor_human_modeling_v1/evidence/stage1m_machine_review_ready_provenance_20260714.v3.json)。

全量快照物化证据：

- 6/6 城市源均通过 provider MD5、下载字节数和本地 SHA-256 身份检查。
- 30/30 走廊均 reference-complete，0 个预注册 feature target 未确认。
- snapshot manifest 共绑定 40 个 artifact，逐项复核哈希失败为 0。
- 该 pass 只认证输入身份和裁剪闭包，不认证 SUMO 建模质量。

## Stage 1-M 权威机器冻结

- machine producer 在 clean commit 上完成 30/30 pipeline pass 和 30/30 normalized semantic replay pass；machine manifest 绑定 1,404 个 artifact，缺失与哈希失败均为 0。
- 30 个 corridor-level machine label 全部为 `defect`、30 个 applicability 均为 `out-of-domain`；这些是机器 attention 标签，不是现实缺陷真值。
- `PCB-453` 保留为原 27-case 实验身份。三个 replacement 新增 6 条后，effective census 为 `PCB-459`：438 runtime special、21 ordinary program truly absent、0 ambiguous，459/459 有唯一技术分类和精确位置。
- `RWC-1` 的 88,423-witness 基线保留不变。replacement 新增 13,975 个后，effective census 为 102,398：39,725 confirmed、62,673 potential、98,041 clusters、15,878 sites；无 witness 丢失、重复、越界或硬键混合。
- effective corpus 有 3 个 stable pedestrian coverage gap：Paris Porte Maillot 2 个、London Liverpool Street 1 个。它们是 safety-coverage/OOD review task，不是已证明的 structural defect；Paris 两个只形成一个 certification site group。
- ROW-1 两份独立报告 SHA-256 相同：15 case、12 runtime probe、0 failed、0 unsafe false pass、0 source-insufficient forced decision、0 expected-answer model-claim read；它仍不声明现场路权真值。
- v2-R2 正式盲审包的 dataset SHA-256 为 `3e5270fa12e0d7b367785dc1352c75e4428ee5ad5b4ea27fd04343b202749070`，sampling-ledger SHA-256 为 `654aa56aba0ba0046630466113d4b25992b3301cbfaab742230f04e787d62b52`。restricted evaluation key 只以哈希身份进入 provenance，内容不进入公开文档。
- `stage_1m_machine_review_ready_gate = pass`；`stage_1h_human_validation_gate = blocked`；`automatic_promotion_gate = blocked`。

## Sydney 试跑证据

OSM 快照：

- 绑定城市 PBF SHA-256：`337d2d24e17ceed8049c1fa3e3fd7fab7b9e99911f43c87463b6133f5d39457d`。
- 5/5 走廊均为引用闭合，且各自预注册 feature targets 均在冻结 OSM 中得到标签证据。
- 全语料门仍为 blocked，因为其余 25 个走廊尚未物化。

Sydney Harbour Bridge 首个机器闭环：

- `netconvert`：pass。
- SUMO 1.27.1 load：pass。
- 20 车 routeability：pass，零未完成、碰撞和 teleport。
- Connection Mode：715 个 junction 中 703 pass、12 review-required、0 structural fail，共 29 个 review finding。
- 独立冲突审核：blocked。当前模型尚未覆盖 9 个受控 pedestrian walkingarea→crossing link，并产生其他多模式/潜在冲突审核项。
- 独立冲突 broad-phase 已从全量 2,487,565 个 movement pair 保守缩减到
  7,728 个精确几何判断（0.31%）；3,723 条 conflict 与保存的 exhaustive
  结果逐字段完全一致。该开发机上的单图计算由两分钟级降至约 0.64 秒；
  时间仅作诊断，不作为验收阈值。
- certification applicability：out-of-domain。
- 三档机器分类：ambiguous（安全覆盖不足不是已确认 defect）。
- 自动晋级：blocked。
- 人工标签：不存在；不得把机器结果描述为人工确认 defect 或 acceptable。
- reviewer-visible 包已物化：盲化 HTML、候选 `.net.xml`、可直接加载的
  `.sumocfg` 和只含 POI 的 display-only `additional.xml`；真实 candidate ID、
  review case ID 和机器标签均未泄漏到 HTML。
- 盲化候选与数据集声明的 SHA-256 一致，package manifest 的 7 个 artifact
  哈希全部闭合；盲化 `.sumocfg` 已由 SUMO 1.27.1 实际加载通过。
- 真实 held-out runner 现在强制执行同输入双重 netconvert replay；本次
  primary/replay 原始哈希因生成时间注释不同，但去除注释并规范化 XML 后
  SHA-256 均为
  `dd0ae52b3059f63a27b8210a000c0cbc139311ff28d4e8ccfd3a989dc8a45a29`。
  不一致时会在生成人工审核候选前阻断。

## 首次 30 走廊机器运行

在 commit `8ca462b` 上启动的首次全量运行已经结束：

- 30/30 走廊均被尝试；所有冻结 OSM 源哈希保持不变。
- 27 个走廊完成 netconvert、机器审核流水线并生成绑定哈希的 review case；
  3 个走廊在 netconvert 双重重放门被阻断。
- 机器标签为 0 acceptable、2 ambiguous、28 defect；这些不是人工标签。
- 22 个 SUMO load pass、5 fail、3 not-run；14 个 routeability pass、
  13 fail、3 not-run。
- 13 个 Connection Mode 为 review-required、14 fail、3 not-run；
  independent safety 在 30 个走廊上均未完成认证。
- 顶层 manifest 绑定 497 个 artifact，缺失 0、SHA-256 复核失败 0。
- 27 个盲化审核 case 已生成；真实人工 review decision 和 adjudication 仍均为 0。
- 最终 automatic promotion gate 与 evidence build status 均为 blocked。

3 个 replay failure 不是时间戳或 XML 排版误报：

- London Kings Cross 的同一 junction 在两次构建中分别成为
  `right_before_left` 和 `priority`，request/foes、internal lane 和 connection
  均发生变化。
- Melbourne Royal Parade 与 Sydney Cross City Tunnel 的 edge、junction、TLS
  和 connection multiset 相同，但 `<roundabout>` 分组不同。SUMO 官方文档说明
  roundabout 记录会参与重新导入并可能影响 lane-changing，不能把分组差异忽略为格式。
- 对 London 显式添加 `--seed 42` 后双重重放仍不一致；随机种子必须进入工具链
  身份，但仅锁 seed 不能解决该不确定性。

进一步检查 SUMO 1.27.1 源码（tag `v1_27_1`，commit
`7717f2379d9e314a0c81c5cec748444de06a2a91`）后，已定位到
`NBEdgeCont::extractRoundabouts()`：它以 `std::set<NBEdge*>` 的指针顺序选择
起始边，并在多条候选 outgoing edge 中取第一个匹配项。不同进程因而可能把
同一组 OSM `junction=roundabout` 边切成不同 `EdgeSet`。后续
`markRoundabouts()` 又按该分组设置优先权和删除外部回转连接，所以这是真实语义
不确定性，不是序列化噪声。

现已建立隔离的 PlainXML 确定性输入实验：

- 第一段锁定 `seed=42`、`--no-turnarounds`、`--roundabouts.guess false`、
  `--plain-output.lanes` 和通行侧；
- source PlainXML 从不原地修改，blocked 时删除陈旧 candidate；
- OSM 标记边只有在形成单一有向闭环，或按完整 edge/lane permission contract
  能唯一分解成若干有向闭环时，才生成规范化 bundle；
- open component、特殊节点、未知环岛记录或权限分解不完整均 fail closed；
- 回转连接只记录、不删除，其合法性留给后续 Connection Mode 和证据审核。

三例结果：

- London 两次均因 Percy Circus 的 6-edge/7-node 断裂环岛和 dead-end 节点阻断；
- Sydney 两次均因一个 1-edge/2-node 环岛断片阻断；
- Melbourne 的 7 个物理组件可确定分解为 8 个循环，两次规范化 PlainXML bundle
  SHA-256 均为
  `01e36eaed20927d696287ce4f11345074a28790b256789eb8c27be79e5f2b406`，
  两次重建网络的规范化语义 SHA-256 均为
  `ceb8d397c23b936f553f3b04885654f04405633e47c053a0378615aaae44adba`，
  且 SUMO load pass。

该实验没有进入现有 v1 晋级路径。Melbourne 的确定性两段式网络相对旧的一步式
网络仍产生 141,141 个稳定实体 delta，说明全网 PlainXML 重建只能作为新的、版本化
初始 source 协议评估，不能作为局部 candidate repair，也不能替换冻结 v1 source。
完整机器证据保存在
`benchmarks/corridor_human_modeling_v1/evidence/netconvert_plainxml_determinism_probe_20260714.v1.json`。

首次运行报告 SHA-256 为
`565c9e11b22d6d0dc3e3dd0ff950fb7c292341c3911f6bb626042d7d43b68b3c`，
manifest SHA-256 为
`7dd11c45dd45090a0fe58e1fa9d4af3b9467fd5fb3320729a87f6da01e8cd794`。

## 修正版 30 走廊机器重跑

在 commit `0348269` 上，冻结的 30 条走廊已使用校准优先、宽度过渡修正和完整
movement path permission 审核重新运行：

- 30/30 被尝试，27 个形成完整 assessment 和盲化 review case；原来的 London
  Kings Cross、Melbourne Royal Parade、Sydney Cross City Tunnel 仍在 replay
  gate 阻断，失败集合没有扩大。
- 30 个 source OSM 均保持 immutable；顶层 manifest 绑定 497 个 artifact，缺失
  和 SHA-256 复核失败均为 0。
- SUMO load 仍为 22 pass、5 fail、3 not-run；routeability 仍为 14 pass、
  13 fail、3 not-run；因此机器标签变化并非 runtime 口径变化。
- 25 个完整 case 使用 source-baseline calibration；Amsterdam Amstel Bridge 和
  Sydney George/Park 因 baseline 最大有效 gap 超过 0.5 m lane-scale cap 而正确
  阻断校准，仅使用 2 m 诊断 fallback，不能晋级。
- Connection Mode 变为 26 fail、1 review-required、3 not-run，共 354 个结构
  failure 和 19,513 个 review finding。结构 failure 精确分为 335 个
  `path_endpoint_gap`、17 个 `controller_logic_missing` 和 2 个
  `internal_path_mode_permission_empty`。
- 两个 permission-empty movement 分别位于 Amsterdam Museumplein 和 London
  Tower Bridge；source lane、direct connection、完整 internal path 与 target lane
  的允许模式交集为空。
- 原先仅因安全覆盖不足而标作 ambiguous 的 Amsterdam A10 Amstel 与 Sydney
  Harbour Bridge，现在分别因 5 和 3 个校准后 endpoint gap 被机器标为 defect。
  这仍不是人工真值；必须通过盲审测量该不变量的 precision。
- independent safety 仍在 30/30 blocked；27 个 review case 的人工 decision 和
  adjudication 仍均为 0。

本次报告 SHA-256 为
`4fb50aa5a468bd22fceb2ecfafec9ecf9eb2fd02740f23b78862ef1f100014a5`，
manifest SHA-256 为
`cc702516019071e501fe88307f510a30b23d4710f1bd50cb803ca2d878100237`。
完整静态证据保存在
`benchmarks/corridor_human_modeling_v1/evidence/held_out_machine_run_corrected_20260714.v1.json`。

## 受控行人冲突与 provenance v2 全量重跑

在 clean commit
`e552b82165bef0c4c2b6ca95afdb437c525d5c66` 上，冻结语料已使用 Python
3.12.13、SUMO/netconvert 1.27.1 和锁定依赖完成第三次 30/30 机器运行：

- producer repository、branch、commit、tree、clean worktree、Python/SUMO 可执行文件
  SHA-256、`randomTrips.py`、三个 typemap、依赖版本、超时和 blinding seed hash
  均进入 content-derived run identity；最初检测到 Python 3.14.6 漂移时流水线直接
  阻断，未放宽 toolchain lock。
- machine report 和 manifest 升级为 v2。顶层 manifest 绑定 1,243 个 artifact，
  缺失 0、SHA-256 错误 0，输出目录中未列入 manifest 的文件为 0；旧版只绑定
  497 个 artifact 的 provenance 缺口已经关闭。
- 30 个 source OSM 均保持 immutable，Ingolstadt authoritative source SHA-256
  仍为
  `52114063a325b26f1b50ca08d4686697cc03ea1767dce1dc3815f4a5ae362f57`。
- 非 safety 结果逐走廊与前一轮完全一致：27 pipeline pass、3 replay blocked；
  SUMO load 22 pass/5 fail/3 not-run；routeability 14 pass/13 fail/3 not-run；
  Connection Mode 1 review-required/26 fail/3 not-run；30 个机器标签仍全部为
  defect。这种一致性只证明复跑稳定，不证明机器标签是真值。
- 只有证据闭合的 `walkingarea → crossing → walkingarea` 受控链才生成稳定行人
  movement；owner、权限、几何、continuation、crossingEdges 和 signal group 任一
  不闭合就 fail closed。`crossingEdges` 只用于边界端口证据，不能反推物理 owner。
- 先前 4,529 个 unsupported controlled pedestrian link 现已全部映射；受控
  movement 覆盖由 27,996 增至 32,525，unsupported 由 4,529 降至 0，新增
  4,529 个受控行人 movement。
- 独立冲突图的 `protected_green_movement_conflict` 从 1,465 增至 2,500；新增
  1,035 个是受控行人进入独立几何审核后暴露的机器 finding，不是人工确认的现场
  缺陷。27 个完整 case 中 3 个 safety status 为 review、24 个仍 blocked；另 3 个
  replay failure 在顶层保持 blocked。
- 当前仍有 18,313 个 crossing edge 中的 13,784 个未进入该严格受控链模型，
  另有 27 个 facility owner 未能唯一解析；因此 pedestrian-aware 不等于完整
  pedestrian safety，更不能据此晋级 NEMA 或自动修复。
- 27 个新盲化 review case 已绑定候选哈希；真实人工 decision 和 adjudication
  仍均为 0。

本次 report SHA-256 为
`17f7126b466960cf61288b59c81f8b1a2d53dd5a4a704c8ed24409825722fa79`，
manifest SHA-256 为
`c8b8b20216306da8d535998f26d30488f92b0d4ac02c5906dc186869867e08dd`。
完整静态证据保存在
`benchmarks/corridor_human_modeling_v1/evidence/held_out_machine_run_pedestrian_provenance_20260714.v2.json`。

## 无信号 crossing 与 provenance v3 全量重跑

在 clean commit
`be15f6e4a3eb2ecbc08a8826e5a6afb1f6ccac94` 上，冻结语料又完成一次
30/30 机器运行：

- 新增 `pedestrian_control_binding` 稳定实体，把 crossing movement 的几何语义
  与 signalized/uncontrolled 控制归属分开；有 `tl` 绑定但没有 signal group/program
  的 movement 不再被错误降级成无信号 crossing。
- 18,313 个 crossing edge 中 18,311 个已形成稳定 pedestrian movement：4,529
  个 signalized、13,782 个 uncontrolled；未建模数由 13,784 降至 2。剩余两例均在
  Paris Porte Maillot，但当前 coverage 只保存数量，尚未输出精确稳定 review task。
- 当时输出了 453 个粗粒度 `controlled_pedestrian_signal_group_missing` finding；这个旧名称
  不能证明它们都是 hard defect。当前 PCB-453 已将其重新解释并全量分类，只有
  `ordinary-program-truly-absent` 与 `program-present-link-invalid` 才属于 hard class。
- uncontrolled pedestrian movement 与车辆 movement 形成 34,493 个 confirmed
  centerline conflict 和 53,930 个 potential envelope conflict。两类均保持
  right-of-way review；在 request/foes、crossing priority 和让行关系被独立证明前，
  不得描述为现场缺陷或安全通过。
- protected-green conflict 仍为 2,500，说明新增 uncontrolled movement 没有被误绑
  到 TLS phase；所有非 safety case status 与 v2 逐条一致。
- 30/30 source OSM immutable；27 个完整 review case、3 个相同 replay blocker；
  顶层 manifest 仍绑定 1,243 个 artifact，缺失、哈希错误和输出漏列均为 0。
- 完整 case 的 safety status 为 25 blocked、2 review；顶层连同 3 个 pipeline
  failure 为 28 blocked、2 review。人工 decision 和 adjudication 仍均为 0。

本次 report SHA-256 为
`71888103870ebdae17471d5f77ea2df12ca8721a2037bc462c77fa1133a1d6bb`，
manifest SHA-256 为
`6ecec5b8477ae6298c78b86b15c42767b5f3b2f19b7ea4385b8aa2e5a2393ba4`。
完整静态证据保存在
`benchmarks/corridor_human_modeling_v1/evidence/held_out_machine_run_all_pedestrian_provenance_20260714.v3.json`。

## 真实样本发现并修复的架构缺陷

首轮 Sydney 构建被 canonicalizer 阻断：审核输入声明 left-hand traffic，
但 OSM→SUMO builder 未把通行侧传给 `netconvert`，实际仍生成 right-hand 网络。

修复后：

- 通行侧成为显式构建输入；
- left-hand 输入使用 `netconvert --lefthand`；
- 构建报告和 command record 记录 traffic side；
- 左右侧矛盾继续 fail closed；
- 同一冻结 Harbour Bridge 样本可进入后续审核。

真实语料还暴露了两处重复全网扫描：

- Connection Mode 曾为每条 movement 重算全网 internal-lane 数量；改为 catalog
  单次预计算后，Berlin Alexanderplatz 的 2,527 个 junction、9,843 条
  movement 核心报告逐字段不变，开发机核心审核约 1.39 秒。
- canonicalizer 已有 outgoing connection 索引，却在每条 movement 内重新构建；
  改为复用索引和单次 hop bound 后，Harbour Bridge 的 16,233 个 canonical
  entity 逐字段不变，开发机约 4.12 秒。
- 以上时间只用于确认可运行性，不是质量验收指标；等价回归和冻结 schema
  仍是硬门。

Berlin Alexanderplatz 还暴露了一个审核口径错误：79 个旧
`path_endpoint_gap` 全部来自 1.0 m bicycle internal lane 与较宽混合 lane
之间的宽度过渡，原始中心线偏移最大 3.706009 m，但按相邻 lane tangent
扣除半宽差后的有效 gap 最大仅 0.11767 m。现已：

- 同时保存 raw gap、width-transition offset 和 effective gap；
- 只对横向宽度过渡扣除半宽差，等宽或纵向断裂继续 hard fail；
- 将超过显示阈值但道路带仍连续的情况变成精确 review finding；
- 从校准样本中排除 walkingarea→crossing 的 display-only 最近端点距离。

修正后 Berlin 校准使用 8,919 条 path、18,746 个端点样本，q99.5 为
0.018742 m、最大 0.11767 m、推荐容差 0.038742 m，校准状态为 pass。
使用该严格容差仍有 31 个 non-motorized-or-mixed outlier 被 hard fail；因此
不能据此宣称 Berlin 已正确，也尚未把修正后的审核器结果外推到全语料。

## 当前仍然存在的硬阻断项

1. Stage 1-H 尚无两名真实独立审核者、第三名 adjudicator 或任何可报告的 precision、recall、kappa 和 review time。
2. 当前 corpus 没有 machine-acceptable 样本；prospective safe-pass cohort 尚未启动，因此 AutoPrecision 和 AutoCoverage 仍未定义。
3. v1 trial 只有 27 个完整 case 且无 machine acceptable，结构上无法同时满足 30-case 与 AutoPrecision 门；v1 必须冻结为 pilot，不能放宽阈值。
4. Stage 2–5 仍只允许隔离的 development-only 研究；没有任何 micro-repair、split/merge、geometry、NEMA、pedestrian、bicycle、rail 或多模式编辑类获得认证。

机器侧第 1-M 里程碑已经关闭，但只有第 1 项按预注册门完成后 Stage 1 才能退出。所有自动晋级继续保持 blocked。
