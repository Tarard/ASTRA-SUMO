# 汉堡追加五轮 SPSA：首个扰动后安全停止

状态：`review_required`。**批准追加五轮，实际完成两次原生评价、零次 SPSA 更新。标定未完成。**

本包保留初值和第一轮正向扰动的全部 130 项字段计数、40 项派生站点计数及失败证据。
正向扰动出现一次碰撞和一次传送，触发预先启用的 `--stop-on-unsafe`。
未执行反向扰动、更新或后续轮次。程序正常退出表示停止规则生效。
`result.json` 中的 `selected_evaluation` 是触发停止的诊断评价，不是被接受的需求方案。

## 固定输入

运行代码提交为 `8379aa391541eed045b803a34ca4eaa31210b46d`，版本和执行哈希见 `execution.json`。
使用仓库自建 `.venv`，Python 3.12.13、NumPy 2.5.1、SUMO/TraCI/sumolib 1.27.1。
所有原生评价均从空网开始，步长 0.1 秒、种子 104、结束时间 14400 秒。
0–3600 秒初始化，3600–12600 秒对应 2026-10-19 05:30–08:00 UTC 的真实计数。

网络沿用 [LSA104 完整内部路径候选](../hamburg_ring1_104_split_fanout_20261023_v1/README.md)，
SHA256 为 `b293f592f7718626a3cdfc046d9b0fbbd0600d8d88f977258d7bca5f1f16fef7`。
236 条普通道路、道路权限、合法转向、控制程序和 130 项观测均未在本轮调整。
路口内部车道保持范围明确为 `14398547`、`205069138`、`18653534`、`LSA104_part0`。
初值采用 V3 最终的 294 维向量，生成 8622 辆车。
需求、车道意图、控制、检测器来源及真实计数与 V3 最终输入逐字节一致，见 `preflight.json` 和 `prepare.py` 的断言。
新的初值原生评价逐项复现上一包 body-hold 评价的全部 130 项计数，没有复用旧损失缓存。

批准的设置为 K=5、A=0、c=2、alpha=0.602、gamma=0.101、seed=104。
原计划用本次第一对损失选取约一辆首步的增益 a。
由于反向评价未执行，a 尚未估计，不能套用此前不同网络评价得到的 a。
路线、车道和出发分布仍为继承的经验先验，不是现场真实 OD 或自然驾驶的证明。

## 完整评价结果

| 评价 | 字段诊断 MAE | 最大绝对误差 | SSE | 站点诊断 MAE | 碰撞/传送 | 未进入 | 在途 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 初值 | 17.761538 | 71 | 87009 | 56.775 | 0/0 | 1482 | 394 |
| 首轮正向 | 17.200000 | 62 | 71032 | 53.850 | 1/1 | 1217 | 424 |

计数仅取原生 E1 `nVehContrib`，缺失不填零。
每次评价保留 `field-comparison.csv` 全部 130 行和 `station-comparison.csv` 全部 40 行。
站点值由字段求和，不能增加独立证据。
初值虽无碰撞，仍有大量未进入车辆。正向 MAE 略降，但有碰撞及传送。
**两次评价均不满足有效标定，不能接受较低误差的正向方案。**

碰撞时间为 4129.2 秒，类型为 `junction`，位置为 `:LSA119_part0_10_1`。
碰撞双方是 `od_0088_v00009` 和 `od_0078_v00053`。
原始事件、双方完整需求路线、车道意图、车辆路线、控制和网络均在 `evaluations/iteration-0001-plus/` 中。
该记录定位了待查问题，但尚未确定几何、连接、信号冲突或驾驶约束中的原因。
没有因此改变 LSA119，也没有把它归因于总车辆数。

## 可用于定向修正的现有数据

`baseline-attribution/` 是对前一包相同初值原生运行的只读归因，新增仿真数为零。
完整 E1 离开事件与占用记录重建了全部 130 项 `nVehContrib`，没有改用 `nVehEntered`。

- 8622 辆请求中有 1482 辆未进入。五个入口占其中 1307 辆，约 88.2%。
- 6671 辆主窗口出发请求中，4018 辆在窗口内完成字段通过，836 辆在窗口后通过，335 辆已进入但尚无完整字段通过，1482 辆未进入。
- 初始化请求另贡献 416 次主窗口通过。两组合计 4434 次，与主窗口原生计数总数一致。
- 全部字段时段共有 2281 辆次缺口、28 辆次超额，总绝对误差为 2309。
- 119 Z15、Z13、Z4 的缺口分别为 520、399、376。它们不能仅靠调换已有窗口内的超额计数消除。

`field-population.csv` 把全部 8622 辆请求按原有字段意图归组。
每辆车只计入未进入、已进入但未完整通过、窗口前、窗口内或窗口后五类之一。
例如 Z15 关联的 1195 辆请求含初始化车辆：464 辆未进入，125 辆已进入但尚未完整通过，
158 辆在窗口前通过，376 辆在窗口内通过，72 辆在窗口后通过。
这些已存在的未进入和滞后车辆说明，不能把 520 的字段缺口直接解释为还要新增 520 辆车。
该分解核对了每辆车实际通过的字段与原有意图相同，不把 OD 标签当作实际通过证据。

只在既有窗口内重排通过时刻、同时冻结各字段总数时，总绝对误差最多下降 56。
这个界限不包括把窗口外通过移入窗口，也不包括改变排队和进入过程。
`observed-response.npz` 保存本次已发生的 OD 时段归属比例及分子、分母。
这些比例不是改变需求后的因果响应，不能直接承诺新的仿真计数。
合理的下一项小试验需要先解决 LSA119 的通行有效性，再按入口和到达时刻提出有界需求调整。

## 复核与重新运行

先使用仓库 `uv.lock` 建立 `.venv`。
在 PowerShell 7 中显式选用本仓库模块和 SUMO，避免系统旧 TraCI：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH="$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME="$PWD/.venv/Lib/site-packages/sumo"
$env:PATH="$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
.venv/Scripts/python.exe -m pytest -q tests/test_hamburg_od_spsa_five_rounds.py
```

`manifest.json` 固定文件哈希。测试独立复算原生 130 项误差和 40 项字段求和，并核对停止状态。
原始日志中的绝对路径只作历史记录，不能作为新机器的输入路径。
需要重新评价时，以下命令会创建新目录并启动长仿真，不是恢复旧进程：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_od_spsa_five_rounds_20261023_v1/prepare.py --output-dir outputs/hamburg-five-rounds-new
.venv/Scripts/python.exe plugins/astra-sumo/scripts/run_hamburg_od_spsa.py --prepared-dir outputs/hamburg-five-rounds-new/prepared --settings outputs/hamburg-five-rounds-new/settings.json --output-dir outputs/hamburg-five-rounds-new/study --stop-on-unsafe
```

大体积逐秒队列和即时 E1 未加入 Git，原路径、大小及哈希见 `local-only-large-traces.json`。
逐车归因的重新计算需要清单中的即时 E1；周期 E1 的所有计数复核不依赖这些本地大文件。
安全停止后没有自动展开其他仿真。
现场 SPaT、Z15 信号组及 LSA200 施工阶段等既有不确定性仍然保留。
