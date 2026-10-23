# 汉堡多断面行程替换诊断

原参考仍为selected probe-minus1：8640辆，130项MAE5.092308、SSE6898、TAE662；182项TAE744，未入网7辆、在途14辆。
本轮只测试1对和20对显式行程替换：延长原119 Z12车辆的合法尾段，使其再经过535 Z4，并删除同发车时段的一辆独立535 Z4请求。保留车辆的发车时刻和属性不变，新增第二检测车道的原生moving-stop。
道路、控制、全部130项主观测、52项初始化观测及40项派生站点求和保持不变。请求数分别为8639与8620；按发车时段的预期断面事件库存仍为8640，不等于8640个独立行程。
这不是固定OD模型的数量更新，也不表示识别了真实分流、车辆对应或自然选道。

| 方案 | 130 SSE | 130 MAE | 最大误差 | 182 TAE | 未入网 | 在途 | 碰撞/传送 | 判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| substitute-1pair | 6964 | 5.092308 | 27 | 744 | 27 | 16 | 0/0 | rejected |
| substitute-20pair | 12123 | 6.500000 | 37 | 927 | 29 | 76 | 0/0 | rejected |

两项原始判定均为rejected，原参考保留。实际双断面贡献与副作用见response-audit/complete；不因合法路径存在或全部替换车完成双断面贡献而认定标定成功。
原生步长0.1秒、终点14400秒、seed104、libsumo 1.27.1，保留原4节点车身hold和原40感应/11固定控制。两项为独立原生运行。
所有拒绝结果、原生警告、请求及输入快照均保留。prepared候选与完整contributing-passages.json.gz按原字节保存，未双重压缩。instant-e1与queues仅保留本地SHA-256清单。
离线准备目录offline-substitute-*不含原生运行，不计入2次实测。历史绝对路径只作来源；复跑直接读取冻结候选，不重新生成21OD模型。
主130项零误差目标尚未达到。计数、积压和未改车辆的连锁变化共同决定本次拒绝；有限试验不证明该方法普遍有效或无效。

离线核验：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_substitution_20261023_v1/freeze_evidence.py --verify-only`。
显式原生复跑：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_substitution_20261023_v1/reproduce.py --case substitute-1pair --output-dir outputs/multifield-reproduction`。
只有显式调用reproduce.py才启动一次完整仿真。冻结与核验不启动仿真。
计数核验复用同仓库经典OD包的冻结函数；其哈希及原基线包manifest哈希见results.json。OSM：© OpenStreetMap contributors，ODbL 1.0。
