# Hamburg 双字段原生路线支持证据

本包保存两辆稀疏车辆的路线支持试验。两车均依次完整通过 119Z12、535Z4，并正常到达。两车出发时刻为 10、910 秒；每车具有两个原生 moving stop，现有车道意向保持仅指定第一个字段。

| 车辆 | 不重复道路 / 路口 | 119Z12 完整离开时刻 | 535Z4 完整离开时刻 |
| --- | --- | --- | --- |
| witness-1 | 25 / 24 | 36.026941391784 | 136.980769230769 |
| witness-2 | 26 / 25 | 997.980769230769 | 1126.980769230769 |

原生 E1 共 208 行，nVehContrib 总和为 4。四条正占用 leave 事件与每车身份、字段和时段逐项一致。无同车重复字段、重复道路或路口。loaded/inserted/arrived 均为 2；waiting/running/collision/teleport/discard 均为 0。

保留当前 238 道路网络、原控制、四节点 body hold 和原车型；步长 0.1 秒、seed 104、结束时刻 14400 秒。后端为项目 .venv 的 Libsumo 1.27.1，实际库路径及 SHA 见 environment.json。完整输入、原生输出和执行脚本按原字节 gzip 保存；source-provenance.json 保存解压后哈希。

这只证明声明的路线与驾驶约束在该稀疏试验中能实现两个字段的原生贡献。不证明拥堵条件下的服务能力、真实 OD 比例、自然车道选择或 MAE=0。保留 130 项真实主计数和 52 项真实初始化计数；本试验的 field-comparison 不是标定结果。真实观测与 SUMO E1 nVehContrib 是不同来源。声明的模型信号不等于现场 SPaT。原生警告原样保存在 native/sumo-errors.log.gz，包含既有 missing-green 警告。

从仓库根目录离线检查（不运行仿真）：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_native_support_20261023_v1/validate.py
```

显式复跑会启动新的原生仿真，必须使用一个不存在的输出目录：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_multifield_native_support_20261023_v1/reproduce.py --output-dir outputs/multifield-support-reproduction
```

本次冻结只执行离线检查，没有新增仿真。网络包含 OpenStreetMap 贡献者数据，按 ODbL 许可使用。
