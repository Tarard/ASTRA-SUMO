# 汉堡完整窗口 Libsumo 加速核验

同一台电脑上，保持 0.1 秒步长、种子 104、14400 秒时长及全部交通输入，改用项目环境内的 Libsumo 1.27.1。
完整原生耗时从 958.258 秒降至 547.051 秒，约快 **1.75 倍**，耗时减少 **42.9%**。
这是一次完整配对测量，不是多次运行的速度分布。Libsumo 本次从生成需求到完成汇总共 549.787 秒。

基线是 [LSA119 直行曲线修复后的完整评价](../hamburg_ring1_119_straight_order_20261023_v1/README.md)。
六项交通输入逐字节相同，网络、信号、需求、车道保持规则、检测器及真实计数均保持一致。
本次运行从空网开始，没有改变步长、使用保存状态或减少原生观测。
Libsumo 直接加载原生库，未启动命令首项所示的 sumo.exe。`environment.json` 和原生监视记录保存实际模块及库哈希。
[官方说明](https://sumo.dlr.de/docs/Libsumo.html)介绍了这种调用方式与 TraCI 的差异。

## 已核对的通行结果

| 项目 | 原方式 | Libsumo |
|---|---:|---:|
| 字段诊断 MAE | 16.730769 | 16.730769 |
| 最大绝对误差 | 58 | 58 |
| SSE | 71625 | 71625 |
| 派生站点诊断 MAE | 52.375 | 52.375 |
| 碰撞 / 传送 / 丢弃 | 0 / 0 / 0 | 0 / 0 / 0 |
| 未进入 / 在途 | 1381 / 409 | 1381 / 409 |

全部 130 项主窗口字段计数、40 项派生站点值一致。
另外，全部 208 条周期 E1 记录、7222 条 tripinfo、6813 条完成路线及 144000 步通行汇总均一致。
计数只用原生 E1 `nVehContrib`。40 项站点值仍是字段求和。
逐步汇总仅排除每步实际计算耗时 `duration`，其余属性全部比较。
XML 排版空白和 CSV 换行不作为交通差异。检测器及行程属性没有数值容差。

13731 条检测器车道保持事件及 12620 条路口内部保持事件，在每辆车上的顺序、时刻、模式和数值完全一致。
同一步中，不同车辆的记录先后顺序存在差异。因此原始事件列表并非逐项相同。
复核按车辆保留原顺序，不丢弃事件，不仅比较事件总数。
`backend-correspondence.json` 保留首次直接比较事件列表的失败，原执行核验程序也因此返回非零。
这发生在原生运行和完整计数输出结束后。没有重跑或修改交通输入来消除这项差异。
`first-format-check.json` 保留首次将格式空白误报为差异的离线检查。
最终逐车和通行内容比较见 `traffic-correspondence.json`，原始输出保持不变。

本次只验证加速前后的对应关系。它不证明其他需求、控制或车辆行为假设正确。
MAE 尚未归零，积压仍大，本次不是有效标定。加速方式可用于后续定向候选的原生核验。
旧 TraCI 缓存的来源检查保持严格，未放宽哈希要求来复用不同驱动的评价。

## 复核

在仓库项目环境中执行离线检查：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_libsumo_parity_20261023_v1/verify.py
.venv/Scripts/python.exe -m pytest tests/test_hamburg_libsumo_evidence.py -q
```

要重建并执行同一完整需求，先用 `uv sync --frozen --extra dev --extra native --extra reports` 安装锁定环境。
以下命令会启动完整仿真：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH="$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME="$PWD/.venv/Lib/site-packages/sumo"
$env:PATH="$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
.venv/Scripts/python.exe benchmarks/hamburg_ring1_libsumo_parity_20261023_v1/reproduce.py --output-dir outputs/hamburg-libsumo-new
```

`native/` 保留本次原生周期 E1、路线、行程、汇总、碰撞、请求、命令及日志。
六项相同输入的哈希在 `environment.json`，其冻结副本位于上述 LSA119 基线包。
未加入 Git 的即时 E1 和逐秒队列文件路径及哈希在 `local-only-large-traces.json`。
