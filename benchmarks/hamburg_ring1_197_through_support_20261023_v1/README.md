# 汉堡197直行支持修正：工作候选与完整证据

本包冻结源证据支持的局部工作候选，不是正式零误差结果或现场SPaT。只增加197两条passenger延续3→4、4→5，保留原普通道路、完整旧内部曲线/长度、权限和旧合法转向；旧掉头9的phase0/1及停车左转2的phase3由G改为许可g，新14/15继承原直行1。全部时间参数和type不变，感应实际历史仍会随交通改变。

| 完整14400秒原生结果 | 原selected | v5 |
|---|---:|---:|
| 130 MAE / TAE | 5.092308 / 662 | 4.653846 / 605 |
| 130 SSE / 最大绝对误差 | 6898 / 27 | 9473 / 35 |
| 40派生站点MAE | 14.8 | 12.825 |
| 52 TAE / 182 TAE | 82 / 744 | 78 / 683 |
| 请求 / 未入网 / 在途 | 8640 / 7 / 14 | 8640 / 0 / 34 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | 0 / 0 / 0 |

MAE下降，但SSE和最大误差变差，34辆仍在途，healthy=false。不能只用一个改善指标宣称成功。535 Z5–8的56项真实初始化及主窗观测全部精确匹配，合计1459；主窗这四字段TAE从223降到0，其他字段退步。全部34辆未完成请求来自963776077#0、意向119 Z5。原verdict diagnostic_candidate保留；formal_result=false，130项全零目标未完成。

full保存相同8640请求的原字节、全部13字段208周期原生E1、130主计数、52初始化和40派生站点、182对账、summary/trips/routes/collisions/警告、完整贡献身份与实际执行快照。步长0.1秒、seed104、libsumo1.27.1、原moving-stop及4节点车身hold保持；不按新OD模型重新生成需求。instant及queues本机大文件哈希另列，原生计数无缺测补零或删难字段。

candidate-dispositions.json明确保留全部拒绝原型和v1–v4；v5的network.net.xml是最终二次原生转换产物，candidate.net.xml仅是中间件。核心16项保护、原生request与g/G让行、独立静态审查、源码/测试副本均保留。10次小探针共24请求，9pass与1个字段/路线不兼容的坏solo均保留；全部0unsafe不代表所有人车冲突或完整需求的普遍安全保证。

source audits、SUMO1.27.1官方源码及原版权头、导入语义、原生晚窗诊断和独立完整分群审查均按原字节保存。旧箭头导入机制可以解释被推导的单车道直行支持，不能单独证明现场禁转或SUMO执行缺陷。停车支路、掉头、自行车、步行和信号未删除。原source/legacy引用由既有包及哈希绑定；SUMO随附LICENSE/NOTICE/AUTHORS原文另列upstream-sumo。

[固定全局与197局部航片、NetEdit](review/README.md)保留全部图和可用相对链接。渲染pass与最终review_required分开；图像不证明现场控制或零误差。航片© Freie und Hansestadt Hamburg, LGV，Datenlizenz Deutschland Namensnennung 2.0；OSM© OpenStreetMap contributors，ODbL1.0。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_197_through_support_20261023_v1/freeze_evidence.py --verify-only`。

只有显式调用reproduce.py并指定新output-dir才用既有core重做局部materialization，再以冻结selected需求执行一次完整原生测量。它核源码/版本/hash及全部原生计数，不恢复旧缓存hash、不改OD、不外推14400秒之后的控制。冻结不运行仿真；后续观察与下一阶段目录未纳入。
