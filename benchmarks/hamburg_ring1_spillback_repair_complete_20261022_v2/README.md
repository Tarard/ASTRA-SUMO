# Hamburg spillback 修正完整对照 V2

2026-10-22。两项完整控制对照均已完成，均无效。没有接受或继续推广这些控制候选。
它们保留原来的需求，不是新 OD—SPSA 的结果。

| 完整对照 | 字段诊断 MAE | 最大字段误差 | 一致字段 / 130 | 等待进入 | 在途 | 碰撞 / 传送 |
|---|---:|---:|---:|---:|---:|---:|
| [汇入协调候选](../hamburg_ring1_spillback_repair_20261022_v1/merge-coordinated-full/results.json) | 11.661538 | 56 | 22 | 495 | 214 | 2 / 2 |
| 提前排空候选 | 14.807692 | 81 | 26 | 695 | 300 | 2 / 2 |

提前排空候选的站点诊断 MAE 为 43.325，最大绝对误差为 162，0/40 项一致。
其全部 [130 个字段残差](field-comparison.csv)和 [40 个派生站点残差](station-comparison.csv)保留。
[完整结果](results.json)明确标为无效，误差不作为有效标定指标。
碰撞发生在 `:LSA104_part0_12_0`，时间为 4622.4 秒和 7948.1 秒。

计数仅取 SUMO 原生 E1 `nVehContrib`。
原生计数、车辆行程、运行汇总、碰撞和执行记录以 `.gz` 保存在本目录。
完整源文件位置和散列见 [raw-native-inventory.json](raw-native-inventory.json)。
其原始目录为 `outputs/ring1-native-merge-preclear-full-20261022-v1/`。
先前的前缀证据及运行方式见 [V1](../hamburg_ring1_spillback_repair_20261022_v1/README.md)。

分析：`plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py` 与 `hamburg_spillback.py`。
运行：V1 的 `run-prefix.py` 为前缀复现，完整实际运行脚本保存在原始目录的相邻 `-execution/run.py`。
场景：`outputs/ring1-native-merge-preclear-inputs-20261022-v1/`。
没有增加新的控制方案。当前工作集中在固定控制下的 OD—SPSA。
