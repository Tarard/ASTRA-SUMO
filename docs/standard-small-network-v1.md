# Torii 标准小路网专家建模闭环 v1

> 状态：development-only 机器闭环已完成；自动晋级仍阻断。本文不改变
> Stage 1-M/1-H 的研究退出条件，也不宣称 NEMA、行人或任意 OSM 路网已认证。

## 目标

把城市级目标缩小为一个可证伪的垂直切片：

1. 由 OSM signal anchors、车辆路径和道路身份推导 physical cell。
2. 把 raw boundary ports 合并为物理 approach。
3. 分别生成 OSM `turn:lanes` 与几何/车道连续性 movement 假设。
4. 保存 preserve-split、merge、partial-repair 的可逆候选 DAG。
5. 将真实 netconvert 候选精确绑定到一个 DAG 节点。
6. 通过 Connection Mode、独立冲突、范围外 exact diff、SUMO load 和 all-turn smoke。
7. 仅当 movement 与 conflict closure 唯一成立时，生成二级 classic NEMA topology 候选。
8. 生成 hash-bound JSON、HTML、rollback、manifest 和不抢前台的 NetEdit 三模式审核图。

teacher 只允许作为后续 soft evidence；当前切片不使用 teacher 坐标或 ID 授权修改。

## XS-1 四叉结果

- 11 个碎片节点由 OSM signal-anchor path closure 独立闭合为一个 physical cell。
- 四个 physical approaches；两个 movement 方法精确同意同一组 12 个 lane movements。
- 实际清洗网络精确绑定到 reversible merge DAG node。
- 源网络 10 个 target TLS junction/controller 变成候选中的一个 physical TLS owner；旧 ID 残留为 0。
- 主候选 12/12 internal paths、12/12 all-turn arrivals、独立冲突 0、范围外 delta 0。
- 二级 NEMA 候选使用 phases 1–8，并再次通过 netconvert、Connection Mode、独立冲突、范围外差分和 12/12 all-turn smoke。
- NEMA 参数只是 canonical simulation placeholders，不是 Ingolstadt 现场配时。

## XS-2 三叉结果

- 六个 signal-cell 节点独立闭合；四个 raw ports 合并为三个 physical approaches。
- strict `turn:lanes` 得到 6 个 movement，geometry continuity 得到 7 个 movement；后者与已物化候选精确一致。
- 六个相关 OSM restrictions 尚未完成 path-level resolution；其中一个从 boundary approach 出发、但 `via` 位于当前 physical cell 外，必须保留为未闭合证据。
- 主候选的结构、TLS ownership、独立冲突、范围外差分和 7/7 all-turn smoke 均通过。
- NEMA 阶段因 movement 共识未闭合而 `abstained`，不写 NEMA `.net.xml`。这是 policy pass，不是运行失败。

## SUMO 官方小场景边界

运行：

```powershell
.\.venv\Scripts\python.exe plugins\torii-sumo\scripts\run_standard_small_network_gate.py
```

机器 gate 包含：

- 九个 SUMO 1.27.1 官方 parser/fail-closed 场景：9/9 通过、输入不变、语义重放一致、SUMO load 通过。
- 一个 dedicated-lane、no-U-turn、12-movement 四叉正例：strict NEMA scan eligible，phases 1–8 完整，SUMO smoke 通过。
- 官方 `NEMA_4arm` 与 grouped-signal-index 两个反例：网络本身合法，但含四条 U-turn 和共享 `left|through` lane，因此 Torii 正确 `review_required`，不自动重写。

官方场景冻结期望已同步到 2026-07-14 的独立冲突 oracle：同一 source port 的相邻分流 lane 不再因 envelope proximity 被误计为冲突；request/foes 已明确证明 yield 的 permissive pair 不再产生 unresolved-yield finding。SUMO 输入和工具链未改变，benchmark identity 已随期望 payload 重算。

## 后台 NetEdit 审核

`netedit_background_review.py` 支持两个 hash-bound 角色：

```powershell
--candidate-role primary
--candidate-role nema-topology
```

每个角色只生成 Inspect、Traffic Light、Connection 三张 site-level 图。模式消息直接发给 FOX canvas，截图通过 `PrintWindow` 获取；三张图的 SHA-256 必须不同，且前台窗口句柄必须全程不变，否则该审核任务阻断。该证据不代替代码审计，也不等于人工验证。

## 下一扩展决策

选择 **pedestrian first**：

- 官方 pedestrian crossing 场景已有 5 个 movement、4 个独立 conflict witness，独立 safety graph 通过，但 Connection Mode 仍需 review。
- 这恰好提供最小的 crossing facility + right-of-way evidence + vehicle-conflict 闭环。
- bicycle 暂缓，直到加入可绑定的官方或人工 gold fixture。
- ramp 放在 pedestrian 之后；官方 on-ramp 仍同时是 Connection Mode 与独立 safety review。
- rail 保持 runtime-special/OOD，不进入普通道路启发式。

下一阶段不是“自动添加人行道”，而是先完成一个行人设施的 audit-only 正例/反例闭环：stable crossing facility、priority class、vehicle conflict families、request/foes model claim、受控 runtime probe、review overlay 和 manifest。

## 当前不能宣称

- XS-1 的 merge 或 NEMA 已由人类接受。
- XS-2 的七个 movement 已由 OSM 唯一证明。
- generic NEMA timing 等于德国现场控制。
- pedestrian、bicycle、ramp 或 rail repair 已认证。
- 任意 OSM 路网可自动达到专家 NetEdit 质量。
