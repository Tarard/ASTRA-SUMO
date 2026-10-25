# Hamburg spillback 修正对照，2026-10-22 V1

状态：四个前缀对照及协调候选的完整窗口试验已完成。提前排空候选的完整试验仍在运行。
本资料冻结已完成证据。全部主要窗口目标仍保留。没有候选作为有效标定接受。

## 已完成的前缀

运行至 3780 秒。初始化比较有 52 项原生 E1 计数。
主要窗口的 130 项在此前缀中尚未完整覆盖，保留为未知，不填零。

| 方案 | 初始化 E1 通过数 | 完全一致项 / 52 | 初始化 MAE | 等待进入 | 碰撞 / 传送 |
|---|---:|---:|---:|---:|---:|
| 原方案 | 1773 | 32 | 4.076923 | 79 | 0 / 0 |
| 删除因剪枝而空置的服务阶段 | 1694 | 33 | 5.826923 | 121 | 0 / 0 |
| 下游汇入协调候选 | 1844 | 27 | 4.211538 | 2 | 0 / 0 |
| 下游汇入提前排空候选 | 1804 | 33 | 3.903846 | 2 | 0 / 0 |

协调候选减少入口积压，但未同时改善所有字段。
相对原方案，协调候选的 119 Z4 初始化计数减少 6。
提前排空候选的 Z4 减少 25、Z15 减少 6。这些损失没有被省略。
空阶段删除候选未被采用。其他两项是待检验控制假设，不是现场配时。

## 原始文件与重做

- [全部比较表](prefix-comparison/prefix-comparison.csv)。
- [全部 52 个字段总量比较](prefix-comparison/all-52-field-comparisons.csv)。
- [全部 208 个初始化时段比较](prefix-comparison/all-208-initialization-bin-comparisons.csv)。
- `prefixes/`：四项原生运行的计数、车辆结果、信号与阻塞观察记录。
- `proposals/`：三个候选控制文件及假设。
- [run-prefix.py](run-prefix.py)、[observe-prefix.py](observe-prefix.py)：前缀运行与只读观察。
- [baseline-prefix-correspondence.json](baseline-prefix-correspondence.json)：原方案前缀与原完整运行一致性。
- [coordinated-prefix-correspondence.json](coordinated-prefix-correspondence.json)：协调前缀与对应完整运行的前缀一致性。
- [tests.log](tests.log)：候选构造检查。

分析函数为 `plugins/astra-sumo/src/astra_sumo/core/hamburg_spillback.py`。
候选构造为 `plugins/astra-sumo/src/astra_sumo/core/hamburg_spillback_repair.py`。
原始比较目录为 `outputs/ring1-spillback-repair-prefix-comparison-20261022-v2/`。
各次完整运行保存到 `outputs/ring1-native-merge-coordination-full-20261022-v1/`
和 `outputs/ring1-native-merge-preclear-full-20261022-v1/`。

## 已完成的协调候选完整运行

[全部 130 个字段残差](merge-coordinated-full/field-comparison.csv)、[40 个派生站点残差](merge-coordinated-full/station-comparison.csv)
及[结果](merge-coordinated-full/results.json)均保留。
字段诊断 MAE 为 11.661538，最大绝对误差 56，22/130 项完全一致。
站点诊断 MAE 为 33.3，最大绝对误差 112，0/40 项完全一致。
运行结束时还有 495 辆等待进入、214 辆在途，并有 2 次碰撞及 2 次传送。
两次碰撞都在 `:LSA104_part0_12_0`，时间为 5349.0 秒和 9753.7 秒。
该运行无效。完整原生计数仅作诊断，不作为可接受的拟合指标。

截至本次记录，提前排空候选的完整运行也已出现碰撞与传送。
其尚未完成的完整窗口计数不与上述完整结果混合。
本次转向 OD 定义与 SPSA 方法核对，没有再增加控制试参。
