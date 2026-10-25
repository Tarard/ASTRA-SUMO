# 209车道连接：统一来源、观测与完整原生验证

当前最低已测健康主130 MAE为3.238461538，TAE421、SSE5131、最大误差21。8640请求全部插入并到达，零等待、在途、碰撞、传送和discard；8640个唯一预期字段贡献，无重复或额外字段。535指定56行仍全部零。主130尚未归零，物理适用仍为review_required，不把拟合改善当现场模型认证。

| 指标 | 原offset0 | +15源 | 209候选 |
|---|---:|---:|---:|
| 主130 MAE / SSE |3.715385 / 6709|3.538462 / 5818|3.238462 / 5131|
| 初始52 TAE / SSE |105 / 1005|168 / 2116|136 / 1894|
| 全182 TAE / SSE |588 / 7714|628 / 7934|557 / 7025|
| 派生40站点MAE |10.975|10.55|9.875|

相对+15源，所有这些总体误差均下降；字段并未全改善。119 Z5主TAE从67升到79，535 Z4从41升到63。相对offset0，初始化TAE仍从105升到136。全部130/40/52/182/208行、逐字段及最大残差保留。原full状态diagnostic_measurement_complete与源offset试验的原rejected标签不改；预设门不是用户主130归零目标的替代。

本包只收同一问题链的四次原生调用：60车局部服务fixture、原+15输入的4500秒被动观察、一次5车movement smoke，以及唯一8640全时域验证。60车没有603交叉流，但不证明现场饱和容量。4500观察保留65个完整E1区间、45000条summary、4405–4435秒301帧getter及episode/独立分析；未来117行显式null。它证实原源前缀，不作为新连接full必须不变的门。

当前OSM与2022官方209 MAP/KML支持核查完整五lane序：603的两right与三forward，及734lane0的primary续行。候选保留道路/车道几何、长度、权限、全部合法住宅支路、8640需求、lane意图、真实计数与驾驶规则；只调整声明的连接和新movement的相邻同向颜色投影，phase/type/duration/offset不变。734/110三lane均为机动车，未放宽自行车权限。MAP14→egress5/SG6属于未保留的东北Alsterufer，不是保留的270 Warburg住宅支路。

官方209/15.1 MAP/KML于2026-10-24重新取得，HTTP Last-Modified为2022-08-15；其日期不证明2026-10-19实施。209 OCIT目录无条目，一次同版本路径404，未猜SP1、intergreen或实际SPaT。既有2026航片分辨率与遮挡不足以独立确认逐lane箭头。V1对照、原OSM构建、剪枝/主路恢复lineage及六灯归属缺口均保留，不把猜灯直接叫冗余或bug，未撤灯。

candidate-v1/v2的离线失败、v3原materializer和后续guard-only快照均保存。原构建README中的“尚未traffic运行”是当时状态，不覆盖后来的smoke/full。5车smoke实际观察五个新movement及住宅保留的unique via→receiving lane并完整到达边界，mode31、原生moving waypoints，无setSpeed/position/remove/phase操作；它不认证拥堵或普遍安全。全TLS/FCD/LC及原警告保留。完整运行使用同项目Libsumo1.27.1、0.1秒、seed104、原四处bodyhold。

共用旧网络/需求/观测/core按固定包SHA引用；旧声明路径绑定实际快照，不要求后来的live guard与旧materializer相同。301帧JSONL、fixture/smoke FCD和compact完整贡献可迁移。大queues/instant仅保留本地大小/SHA；原生复现会另生成它们。没有新render、NetEdit或后续校准试验。本次冻结没有额外SUMO。

离线核验：`.venv\Scripts\python.exe benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1/reproduce.py --mode verify`。

显式完整原生复现从仓库根目录运行，必须使用新目录：

```powershell
$env:PYTHONPATH = (Join-Path $PWD '.venv/Lib/site-packages') + ';' + (Join-Path $PWD 'plugins/astra-sumo/src')
$env:SUMO_HOME = Join-Path $PWD '.venv/Lib/site-packages/sumo'
.\.venv\Scripts\python.exe benchmarks/hamburg_ring1_209_ordinal_connection_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-209-ordinal-full
```

入口检查实际模块路径、版本及二进制/core哈希，从冻结显式输入调用原生core，不重新生成OD。离线verify不等于重新原生运行，复现这些结果不表示130零误差或现场SPaT已确定。
