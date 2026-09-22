# Torii-SUMO 接管与架构审计（2026-07-13）

## 结论

项目当前的走廊级方向是现实的，四个约定场景已经形成可重复的真实 SUMO 闭环；但“任意 OSM 城市自动达到人工清洗质量”仍不能作为已完成能力。正确的产品边界是：机器生成可逆候选，安全修改自动通过，受保护变化进入精确人工审核，不可证明的变化阻断，并把审核位置写入可叠加的 SUMO `additional.xml`。

本次接管没有重写既有 OSM 大流程，也没有回退工作树中的既有修改。工作集中在最危险的证据与晋级边界，并用真实 `netconvert`、SUMO 和 `randomTrips.py` 验证。

## 审查范围

- 82 个 `torii_sumo` Python 源文件、80 个测试/fixture 文件。
- Router、Planner、OSM workflow、intersection scene、MCP 工具入口、corridor ledger、routeability、topology、TLS/多模式保护、artifact manifest。
- 正常路径、失败路径、陈旧输出、伪造证据、源/候选冲突、工具链混用和文件写入中断风险。
- 全量单元/集成测试、默认 lint、异常边界 lint、依赖一致性、真实 SUMO 四场景回归。

## 已修复的问题

| 优先级 | 问题 | 风险 | 处理结果 |
|---|---|---|---|
| P0 | 候选 gate 可由调用方直接传入语义/TLS allowance | 未经审核的变化可能被数字参数放行 | 公共 MCP 只接受持久化 materialization report 和可选 review decision；allowance 接口已删除 |
| P0 | source/candidate 可同路径或同内容 | “通过”的候选可能根本没有修改，甚至覆盖源网络 | 强制路径不同、SHA-256 不同，候选身份绑定进报告与 manifest |
| P0 | gate 可消费内存中的、未落盘的证据 | 调用者可构造无法追溯的通过结果 | materialization、review、routeability、topology 均校验持久化文件、路径和候选哈希 |
| P0 | routeability/netconvert 可能误用上次遗留文件 | 当前命令失败仍可能读取旧 routes/trips/candidate | 执行前删除陈旧输出，同时要求 return code 为 0、预期文件存在且 XML 有效 |
| P1 | routeability/topology 的早退分支不总写 manifest | 阻断原因不可审计 | 所有可落盘失败分支都写 report/manifest；runner 异常也被记录 |
| P1 | 多模式连通图忽略 SUMO internal/crossing/walkingarea 边 | 合法 sidewalk/bikeway 被误判为断开 | 改为 connection-level 遍历内部边，再统计正常边；增加回归测试 |
| P1 | SUMO、netconvert、randomTrips 可能来自不同安装 | 版本差导致不可重现行为 | `SUMO_HOME` 优先，所有二进制和工具固定到同一安装根 |
| P1 | 匿名 operation id 使用 Python 内建 `hash()` | 不同进程得到不同 ID，artifact 不可复现 | 改为稳定 SHA-256 ID |
| P1 | 任意 additive operations 被错误视为冲突 | sidewalk + crossing 等合理组合无法物化 | 允许兼容的 additive family；破坏性和添加型混合仍阻断 |
| P1 | 关键 JSON/命令/manifest 直接写入 | 中断可能留下半个、却看似存在的证据文件 | 新增同目录临时文件、flush/fsync、`os.replace` 的原子写入层，关键候选链已迁移 |
| P1 | 四场景脚本依赖 `.gitignore` 排除的旧 `outputs/` | 本机通过，换机器无法复现 | 现在从 tracked OSM、SUMO 官方 pedestrian 教程输入和一句话五向场景现场重建源网络 |
| P1 | OSM visual-detail 输出含陈旧 TLS identity，SUMO 无法 load | 把铁路隐式控制器误当作坏引用会破坏铁路安全；粗暴 normalize 又会重建大量内部元素 | 新增 fail-closed TLS reference classifier；保留 `rail_signal`/`rail_crossing`，只对已经无控制状态的纯人行内部残留生成 byte-minimal 独立候选、回滚计划、review additional.xml 和 manifest |
| P2 | 默认 lint 未覆盖明显的吞异常边界 | 失败可能被静默隐藏 | 默认 lint 与 `BLE001,S110` 扩展检查均通过；保留的外部进程边界均有明确注释并持久化失败 |
| P2 | 旧交付清单仍写 `1067 passed` 并引用弱证据 | 用户看到过期“通过”结论 | 回归脚本现在重建 v2 aggregate 与四块正式清单，不再手工维护测试数字 |

## 三档晋级设计

1. **自动安全档**：没有受保护的语义/TLS 变化，且身份、XML、netconvert、SUMO load、routeability、topology、modal connectivity 全部通过。
2. **需要审核档**：每个候选生成 `*.map-review.json`、`*.review.add.xml`、`*.review.html` 和 `*.review.json`；review decision 必须绑定候选与地图证据 SHA-256，并完整记录结构化观察、审核人、带时区时间、精确 delta、证据、理由和回滚动作。只有 operation 显式声明 `map_review_required: true` 时，地图审核才成为硬门。
3. **阻断档**：缺证据、证据陈旧、同源候选、未审核的受保护变化或任一运行 gate 失败时，不生成可晋级结论，但仍生成阻断报告和 manifest。

调用方不能通过增大容差从第二/第三档绕到第一档。

`additional.xml` 只是一层无副作用的审核可视化：只允许 `poi`、`poly`、`param`，删除/合并几何来自 source，新增几何来自 candidate；任何 rerouter、detector、calibrator 等运行时元素都会使候选契约失败。Google Maps/卫星图、Mapillary、KartaView 或区域地图只提供人工核对基准，不自动批准候选。地图坐标、时间范围和链接会在 gate 中重新计算，不能靠手改 `readiness=pass` 绕过。

## 四个真实场景

统一回归入口：`plugins/torii-sumo/scripts/run_corridor_contract_regression.py`。

| 场景 | 源构建 | 候选 | 结果 |
|---|---|---|---|
| four-way OSM sidewalk | tracked `x4_signalized.osm.xml` -> real netconvert | reviewed sidewalk + additional.xml | 9/9 gates pass |
| official pedestrian crossing | SUMO 官方 pedestrian tutorial nodes/edges -> real netconvert | crossing + walking areas + exact TLS/semantic review | 9/9 gates pass |
| five-way bicycle | 一句话 -> structured spec -> actuated five-way -> netconvert/SUMO | bicycle + pedestrian connector | 9/9 gates pass |
| five-way ramp | 同一可复现五向源场景 | passenger ramp | 9/9 gates pass |

九道 gate 是：artifact identity、netconvert evidence、review contract、XML parse、TLS/modal/rail/bridge/tunnel preservation、modal connectivity、SUMO load、routeability、topology。

正式本机证据位于 `outputs/corridor_vertical_slice_20260713/contract_v2/four_candidate_contract_manifest.json`；该目录是生成物，删除后可由上述脚本完整重建。

## Ingolstadt 同 bbox 教师走廊

统一入口：`plugins/torii-sumo/scripts/run_ingolstadt_corridor_teacher.py`。

2026-07-13 首次在线验收从 Overpass 重新下载 bbox `11.413800,48.755391,11.433800,48.775391`，没有复用旧 OSM extract。raw visual-detail 路网有 58 个内嵌 `tlLogic`、12 个 SUMO 铁路隐式控制器，以及 1 条 junction `32564066` 上已经是 `state=M` 的人行内部连接残留 TLS identity。清理器只删除该连接的 `tl`/`linkIndex` 属性，保持 34,392 条 connection 数量和所有非目标网络元素不变，源 SHA-256 复核通过。

独立候选随后通过 SUMO load；completion-aware routeability 在 1200 秒窗口完成 10/10 行程，collision=0、teleport=0。junction `267517510` 的当前 OSM 证据包含 3 个 `highway=crossing + crossing=traffic_signals` 节点和 3 个 traffic-signal 节点，候选与教师都已是 traffic-light control。教师差异缩小为 `internal_function_counts` 和 `movement_signature_counts`，所以 `teacher_transfer_status=review_required`，没有机械复制人工模型。

第二次在线复跑收到 Overpass HTTP 504，runner 正确输出 blocked，而不是复用缓存冒充成功。随后使用首次下载的 OSM 与 raw net（两者均由 manifest 绑定哈希）通过 `--candidate-net + --source-osm` 做确定性离线重放，完整链再次通过。最终可审核重放产物位于 `outputs/ingolstadt_corridor_teacher_20260713/online_final_replay/`，包括 candidate-bound map JSON、POI/poly-only `additional.xml`、HTML、decision template 和 manifests；在线 504 失败证据保留在相邻 `online_final/` 目录。

## 代码原生 Connection Mode 审计

在 NEMA 层之前新增了独立的 `core/connection_mode_audit.py` 和 MCP 工具
`sumo_network_connection_mode_audit`。它不启动 GUI，而是直接从 `.net.xml`
重建 NetEdit Connection Mode 的 `fromLane -> toLane -> via -> internal path ->
request/foes -> linkIndex -> tlLogic state` 关系。确定的路径、request、lane 或
linkIndex 损坏进入 `fail`；合法但可疑的 merge/fanout、lane drop/addition 和
保护绿 foe 进入 `review_required`；完全无发现才为 `pass`。前两档均阻断自动
晋级，但只有 `fail` 表示结构损坏。每次输出 JSON、display-only
`additional.xml` 和绑定源 SHA-256 的 manifest；面向用户的 OSM workflow 默认
不再打开 NetEdit，NetEdit 只用于代码标记位置的可选可视化审核。铁路
`rail_signal` 按 SUMO runtime controller 单独分类，不要求普通道路 `tlLogic`。

同时新增 `sumo_network_connection_mode_regression_audit`，接受 source/candidate
两个 `.net.xml` 和两侧目标 junction IDs。它分别重建 Connection Mode，并对被触及
TLS controller 做双侧 scope closure，再比较逐路口 structural/review category；
不依赖会被 netconvert 重编号的 internal connection index。目标范围新增 finding、
范围外任意回归、以及无法解释的范围外 junction 身份变化都会阻断晋级。输出包括
差分 JSON、只显示的 `additional.xml` 和同时绑定 source/candidate SHA-256 的
manifest。teacher-guided queue 已默认执行该门；只要失败候选仍有 `.net.xml`，它也
会继续做诊断，而不是因前一层 parity 失败而隐藏 Connection Mode 结果。

当前 Ingolstadt 同 bbox 全网审计覆盖 2,274 个路口和 13,169 条直接 movement，
13,169/13,169 条 internal path 全部结构通过；2,164 个路口 pass，110 个
`review_required`，结构 fail 为 0。审核队列包括 27 条入口机动车空 connection、
19 条出口无来源 connection、merge/fanout 和 junction `267517559` 的 1 组保护绿
foe；12 个铁路 controller 被正确识别为 runtime rail signal。

Südliche Ringstraße 实验验证了这道门的必要性：plain-XML round trip 曾重复应用
type 的 `sidewalkWidth`/`bikeLaneWidth`，导致 lane index 错位并出现 1,828 个
范围外新增 review。round-trip type 清洗消除了这一全网故障。随后 replay 被收紧为：
保留无删除证据的 OSM boundary edge、不复制未映射 teacher edge、保留已映射道路的
OSM 远端 endpoint。最新安全默认候选已经达到范围外 structural/review/junction
identity delta 全为 0，但目标范围仍新增 13 个 structural finding 和 27 个 review，
因此 parity、Connection Mode 和 promotion 三道门均保持 fail。共享 controller 的
整组 join 只作为显式实验候选，且本次也被否决；“同一 controller”不再被视为
“应物理合并 junction”的充分证据。

## 标准三/四叉口 NEMA 相位绑定

新增 `core/standard_nema_binding.py` 和 MCP 工具 `sumo_network_standard_nema_phase_binding`。OSM cleanup 默认只运行 scan：按 physical junction owner、controller scope、lane mode、approach geometry、turn direction 和 movement matrix 分类，输出资格队列、display-only `additional.xml`、HTML、decision template 和 manifest；不传明确 junction 时不会修改路网。

明确选择 eligible junction 后，工具才生成 SHA-256 绑定的独立候选。标准四叉采用跨环归属：主路两侧 left/through-right 为 5/2 与 1/6，支路为 7/4 与 3/8；三叉采用 SUMO NEMA 缺相 `0` 和重复 phase 4。写候选之前新增 Connection Mode 硬门：逐条追踪 `fromLane -> toLane -> via` 及完整 internal-lane continuation，核对右侧通行车道顺序、lane-rank、request/foes 完整性，并阻断 canonical NEMA 可能并发但互为 foes 的 movement。工具只替换目标 connection 的 `linkIndex` 与唯一 `tlLogic`，并保存全部旧值和旧 logic XML 作为回滚。候选必须通过 minimal-patch、semantic preservation、netconvert round trip、SUMO load 和开启 junction collision 检查的 completion-aware routeability，之后仍保持 `review_required`。

SUMO 1.27.1 标准四叉与 T 型三叉真实验收均通过 Connection Mode、candidate validation、netconvert round trip、SUMO load 和 12/12 routeability，且 junction collision 检查开启，0 collision、0 teleport。四叉验证 12 条 internal movement path 并绑定 phases 1–8；三叉验证 6 条 path，使用 phases 1/2/4/6 与 rings `1,2,0,4`、`0,6,0,4`。Ingolstadt 当前同 bbox 扫描绑定到可由 SUMO 加载的 effective TLS-cleaned network：249 条 TLS-junction review record，其中 24 个三叉、4 个四叉，严格 eligible 为 0；1,617 条 request-bound path 全部结构通过，Connection Mode 为 221 pass、28 review_required、0 structural fail，28 个标准几何路口中 15 pass、13 review_required。统一审核层将 28 个 connection-review 位置与 221 个其他 NEMA-scope blocker 分开，主网络未改。review_required 只表示自动安全证明不足，不代表每个被标记 connection 必然错误，也不代表真实城市信号已自动修复。

## 最终验证

- `pytest -q`: **1132 passed in 45.39s**。
- `ruff check .`: pass。
- `ruff check plugins/torii-sumo/src --select BLE001,S110`: pass。
- `pip check`: no broken requirements。
- SUMO 与 netconvert: **1.27.1**，同一安装根。
- 四场景真实回归: **4/4 pass**，每个场景 **9/9 gates pass**，源网络未被修改。
- Ingolstadt 在线同 bbox 教师走廊: build、bounded TLS cleanup、SUMO load、10/10 routeability 和 artifact manifest 均 pass；教师语义迁移保持 `review_required`。

## 仍然存在的架构债务

这些是本次明确保留、不能伪装为“已经完成”的问题：

1. `osm_workflow.py` 约 8,900 行，`run_osm_cleanup_workflow` 约 90 个关键字参数；配置、依赖注入和流程控制混在一个函数边界。
2. `junction_rebuild_candidate.py` 约 12,800 行；证据提取、规划、重放、物化、晋级和渲染耦合。
3. `WorkflowState`/`StageResult` 主要是运行后的报告适配器，还不是实际 executor 状态机。
4. 静态复杂度盘点仍有 97 个 `C901`、52 个 `PLR0912`、143 个 `PLR0913`、50 个 `PLR0915`；这些是拆分队列，不应通过一次大重写解决。
5. corridor contract 的关键写入已原子化，但核心包仍约有 100 个旧式直接写入点，需要逐阶段迁移。
6. 仓库没有依赖 lockfile，也没有可见的 GitHub CI/SUMO acceptance job；本机真实 gate 尚未变成上游持续约束。
7. 当前四场景和 Ingolstadt 单走廊证明的是候选契约与一条真实参考切片，不是任意城市级 OSM 自动等价于 Ingolstadt 人工模型。四场景中的四向 OSM 仍使用 tracked fixture；在线证据目前只覆盖 Ingolstadt 这一 bbox 和一个教师 junction。

## 接管后的下一拆分顺序

1. 在保持现有 MCP/函数签名兼容的前提下，引入 `OsmWorkflowRequest` 与 `OsmWorkflowServices`，先收拢参数和依赖。
2. 让每个真实执行 stage 接收/返回 `WorkflowState`/`StageResult`，随后把旧大函数变成兼容 facade。
3. 按“证据提取 -> 规划 -> 物化 -> gate -> review rendering”拆分 junction 子系统，每一步先补 characterization tests。
4. 为真实 `delete_edge`、`merge_edges` 各增加走廊 acceptance fixture；当前它们有实现与单元级保护，但不属于本次 4/4 真实场景结论。
5. 将已经跑通的 Ingolstadt 同 bbox 单走廊方法推广到第二个物理 junction 和第二个城市走廊；两者稳定后再进入 Ingolstadt 全网回归。

这条顺序保持已经工作的垂直切片，不把架构重构和模型质量实验混成一次不可验证的大改动。
