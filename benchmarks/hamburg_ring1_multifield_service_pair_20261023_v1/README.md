# 汉堡同队列服务配对反事实

本次从原selected输入出发，保留od_0266_v00020的原出发属性并延长合法尾段至535Z4，省去同h12的一辆独立535Z4请求od_0271_v00019。该车是上一已拒绝变体观察到的左转队列前车。此处是8640→8639辆的显式行程替换，预期断面事件库存按14个发车时段仍守恒；不是原OD模型的数量更新，也不证明真实行程对应或自然选道。

| 原生结果 | 原selected参考 | 本次服务配对 |
|---|---:|---:|
| 请求数 | 8640 | 8639 |
| 130 SSE / MAE / 最大误差 | 6898 / 5.092308 / 27 | 6898 / 5.092308 / 27 |
| 40派生站点MAE / 最大误差 | 14.8 / 48 | 14.8 / 48 |
| 182 TAE | 744 | 744 |
| 未入网 / 在途 | 7 / 14 | 7 / 14 |
| 已插入 / 已到达 | 8633 / 8619 | 8632 / 8618 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | 0 / 0 / 0 |

原verdict为rejected，严格计数改善条件未满足，原selected继续保留。全部208项nVehContrib与参考完全相同：True；完整interval全部属性相同：False。两者含义分开记录，不能用相同误差标量替代逐行核对。全部双断面完整贡献已实现，前缀156行保持，但healthy=false，主130项零误差目标仍未达到。

完整贡献事件仍为8626，来自不同车辆的数量由8626变为8625，其中一车贡献两个字段。末端21辆的身份、未插入/在途状态、请求入口和意向字段与参考相同。只有一个E1区间的speed、occupancy和harmonicMeanSpeed改变。该固定seed、控制与driver条件下出现了“请求数不同、计数向量相同”的实例；它位于扩展的显式路线支持，不证明原21OD模型内所有参数不可识别，也不表示全过程等价。

本包只冻结1次已完成原生测量，运行258.924秒，原步长0.1秒、终点14400秒、seed104、libsumo1.27.1、道路、控制、车型、原字段hold和4节点车身hold不变。完整分群贡献及末端身份见response-audit/complete。离线prepare记录不计作原生运行。此有限反事实不能识别真实OD、证明普遍等价或给出现场控制结论。

prepared、原请求与实现快照、原生208E1及130/40/52/182、summary/trips/routes/collisions/全部警告日志、完整compact贡献均保留。instant和queues仅存本机哈希。范围、计数与复跑直接复用前substitution冻结包函数；原参考、witness及函数包由前包manifest绑定，历史绝对路径只作来源。proposal中的“尚未物化”是原提案时的状态，与后续原生结果分开保留。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_service_pair_20261023_v1/freeze_evidence.py --verify-only`。

只有显式调用reproduce.py并指定新目录才通过既有固定需求复跑入口启动1次完整原生运行：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_service_pair_20261023_v1/reproduce.py --output-dir outputs/service-pair-reproduction`。不重建21OD模型。

OSM：© OpenStreetMap contributors，ODbL 1.0。
