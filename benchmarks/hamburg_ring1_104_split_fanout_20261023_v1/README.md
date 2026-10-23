# 汉堡 LSA104 分流追撞与完整时窗复核

2026-10-23。本轮只处理 LSA104 的重复碰撞，没有增加 SPSA 轮数。
完整候选达到零碰撞、零传送，但仍有大量未进入车辆，计数没有改善，**有效标定未完成**。
全部真实观测、130 项字段目标和 40 项派生站点均保留。

## 1. 已验证的原因与修正范围

[V3](../hamburg_ring1_od_spsa_completed_20261023_v3/README.md) 的四次评价共有 16 次碰撞，其中正向、反向和复测的 13 次均在同一右转。
两辆车从 `139083723#0_2` 右转到 `234423652#1` 的不同车道时，即可复现该处追撞，无需高总需求。
同出口车道对照没有碰撞。慢车最高速度 1.3 m/s 是诊断条件，不是实测驾驶参数。

原转向先经过约 0.6 米的内部段，再进入近距离重合的两条延续段。
运行时两条延续段没有互为内部冲突车道。后车在仍有几何重叠的位置失去前车约束。
该证据定位到当前模型的分段表示，不能推断全部 SUMO 分流均有此问题。
[原始碰撞双方的 OD、路线和字段](original-collision-context.csv)保留逐项对应。

`junction_split_fanout.py` 仅对显式选中的两个转向调用原生 `contPos=0` 编译，保留完整内部路径及其入口冲突检查。
[SUMO 连接定义](https://sumo.dlr.de/docs/Networks/PlainXML.html#lane-to-lane-connectivity)说明该设置取消这条连接的内部等待分段，而非取消内部通行或碰撞检查。
[1.27.1 的 MSLink 实现](https://github.com/eclipse-sumo/sumo/blob/v1_27_1/src/microsim/MSLink.cpp)区分同入口分流及延续段的冲突检查。

## 2. 固定场景与输入

仍为 Hamburg Binnenalster/Ring1，保留五个路口、236 条普通道路和原固定展示范围。
复测使用 [V3 更新后的同一批 8622 个请求](../hamburg_ring1_od_spsa_completed_20261023_v3/evaluations/final/)。
需求、发车时刻、车道意图、检测点、真实计数和信号输入逐字节相同。仅网络文件是单独候选。
步长 0.1 秒，seed 104，运行 0–14400 秒，主计数窗 3600–12600 秒，聚合 900 秒。

全部普通道路、通行权限、信号组编号及程序保持不变。
[28 条内部曲线及轮廓检查](all-104-curves.json)显示最大曲线位移约 0.000000462 米，路口轮廓位移为零。
航片、官方 MAP 参考线和原生转弯曲线仍是不同来源，保留既有对应的不确定性。
物理线形未改，继续沿用[固定航片及 NetEdit before/after](../hamburg_ring1_munich_corridor_20261022_v1/README.md#固定航拍beforeafter)。

## 3. 驾驶信息与执行

本轮没有新信号控制，也没有把真实计数反馈给车辆。
原有驾驶处理读取仿真车辆所在车道、位置、累计距离和车长，以 mode 512 保持车道，直到车尾离开检测点或指定路口。
这是明确的驾驶假设，并非现场传感器信息或自然驾驶行为的证明。

先只改内部路径表示。完整复测仍出现一次 `:LSA104_part0_23_1` 内部换道碰撞。
随后只将既有车身保持范围加入 `LSA104_part0`，其他三个指定路口不变。
4500 秒核验为零碰撞、零传送，其后才运行完整窗口。
[前缀结果](prefix/coverage.json)保留 13 项已测主窗计数及 117 项未来缺失值，没有补零或接受整体 MAE。

## 4. 完成情况

| 完整运行 | 请求 | 进入 | 到达 | 在途 | 等待进入 | 碰撞 / 传送 |
|---|---:|---:|---:|---:|---:|---:|
| V3 更新后基线 | 8622 | 7410 | 7003 | 407 | 1212 | 6 / 6 |
| 仅完整内部路径 | 8622 | 7064 | 6663 | 401 | 1558 | 1 / 1 |
| 再加入 LSA104 车身保持 | 8622 | 7140 | 6746 | 394 | 1482 | 0 / 0 |

两次新运行均完整到达 14400 秒，全部 130 项计数存在。
两者均因未完成需求不能接受。安全修正没有解决入口积压或建立现场通行能力。

## 5. 全部计数结果

计数只取原生 E1 `nVehContrib`。下表均为无效运行的诊断数值。
MAE 为全部绝对差的平均值，单位为辆/15 分钟。站点值是所属字段之和，不是额外观测。

| 完整运行 | 字段 MAE | 字段最大误差 | 字段一致 / 130 | SSE | 站点 MAE | 站点最大误差 | 站点一致 / 40 |
|---|---:|---:|---:|---:|---:|---:|---:|
| V3 更新后基线 | 15.646154 | 62 | 4 | 65254 | 49.85 | 144 | 0 |
| 仅完整内部路径 | 17.830769 | 61 | 3 | 78756 | 56.1 | 131 | 0 |
| 再加入 LSA104 车身保持 | 17.761538 | 71 | 5 | 87009 | 56.775 | 122 | 0 |

全部[第一候选字段](native_only/field-comparison.csv)、[站点](native_only/station-comparison.csv)、[车身保持候选字段](body_hold/field-comparison.csv)及[站点](body_hold/station-comparison.csv)保留。
[独立复算](results.json)核对了每项实测目标、原始计数与残差。
零碰撞候选的计数误差和等待进入数量仍高于 V3，不能作为标定改进接受。

## 6. 修正有效性与未采用的尝试

小例子和完整对照共同表明，分流表示修正消除了原重复追撞，但单独使用仍有内部换道风险。
新增的 LSA104 车身保持在本次完整窗口消除了记录到的碰撞，不能据此推广为其他路网的安全保证。
最初单独修正一处内部车道 index 的尝试仍在同一时刻追撞，未采用。
没有禁用碰撞检查、放宽通行权限、修改道路长度、改变实测值或删除合法路线。

## 7. 不确定性与接受条件

仅检验同一随机种子和窗口，没有新增驾驶参数或需求优化。
当前仍需处理插入积压和计数时段偏差，不能把它们直接归因于唯一车辆总数过大。
继承的路线、车道和发车分布仍是经验先验。现场 SPaT、Z15 信号组及 LSA200 施工阶段限制保持原说明。
最终条件仍为全部 130 项零残差、MAE 和最大误差均为零，并满足运行有效性要求。

## 8. 环境、复跑与验证

本机使用仓库 uv.lock 环境：Python 3.12.13，NumPy 2.5.1，SciPy 1.18.0，SUMO/TraCI/sumolib 1.27.1。
系统 PYTHONPATH 曾指向系统 SUMO tools，本轮显式设置下列环境，并使用包内二进制。

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH="$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME="$PWD/.venv/Lib/site-packages/sumo"
$env:PATH="$env:SUMO_HOME/bin;$PWD/.venv/Scripts;$env:PATH"
.venv/Scripts/python.exe benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/reproduce.py --output-dir outputs/lsa104-small-new
.venv/Scripts/python.exe benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/validate.py --treatment native-only --output-dir outputs/lsa104-native-new
.venv/Scripts/python.exe benchmarks/hamburg_ring1_104_split_fanout_20261023_v1/validate.py --treatment body-hold --output-dir outputs/lsa104-body-new
```

每条命令需要新目录。小例子不会自动启动完整计数运行。
[核心修正](../../plugins/astra-sumo/src/astra_sumo/core/junction_split_fanout.py)生成独立候选并检查范围保持。
[回归测试](../../tests/test_junction_split_fanout.py)先复现碰撞，再验证原生无碰撞结果及独立的道路和程序保持检查。
该原生用例已加入 Windows 必跑清单。相关检查见[记录](focused-tests.log)。
[文件清单](manifest.json)保留散列。即时逐车 E1 和逐秒队列保留在本地，其他主要原生输出以 gzip 冻结。
