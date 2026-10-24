# Current275 simulated-history pair, 2026-10-24

同需求reference在静态模拟history下严格重现275源。warmup9在同一history下TAE286、MAE2.2，仍差于275源，最佳未改变。这里冻结的是当前模型的模拟灯色，不是现场SPaT。

| Demand and control | N | Main TAE / SSE | MAE | Max | Initial52 TAE | All182 TAE / SSE | Station40 MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Source275, mixed control | 8589 | 275 / 2833 | 2.115384615 | 22 | 97 | 372 / 3950 | 6.125 |
| Same demand, static source history | 8589 | 275 / 2833 | 2.115384615 | 22 | 97 | 372 / 3950 | 6.125 |
| warmup9, mixed control | 8598 | 422 / 5584 | 3.246153846 | — | 97 | — | — |
| warmup9, static source history | 8598 | 286 / 2688 | 2.200000000 | 22 | 97 | 383 / 3805 | 6.300 |

两次静态回放均全部进入并到达，每车一次正确意向贡献，无等待、在途、碰撞、teleport或discard，535 Z5–Z8全部56值仍精确。首主窗两者TAE均为9：reference的Z12/Z13残差为−5/−4，warmup9为−4/−5。warmup9的SSE更低但MAE更高，不作为新的best或校准完成。

唯一控制处理是把各自完整原需求的control替换为同一份新275 SaveTLSProgram导出。50条程序均为static、offset0、恰覆盖0–14400秒；原programID保留，导出phase ordinal不是源phase index。525保留完整9字符状态，实际受控index仍只有0/1，未裁剪unused位置，也未恢复734信号绑定。旧99f网络的history未使用。

Reference的14项严格门通过：208E1全部属性、144000summary（只排CPU duration）、全部instant事件、完整trip/routes/stops/exitTimes、hold报告、结束状态及指标重现。两侧所有50条实际state/program在全部0.1秒区间均与新275源记录相同。warmup9的mixed与fixed instant序列可以有不同长度，旧序列对比明确为NA；其自身raw instant、compact、E1及各自8589/8598身份分别核验。原执行前错误审计和修订记录保留，prepared8字节未变。

新源记录的原SUMO运行完整结束，原录制器随后因Decimal覆盖wall变量而序列化失败、退出1。原脚本、snapshot、native和错误log均保留。新offline_postcheck未重跑SUMO，重算原20门及颜色门共21项后生成恢复记录。丢失的精确wall elapsed保持null；另存SUMO报告时长192.66秒，未用它冒充wall时间。

本包引用[最终275包](../hamburg_ring1_chronological_od_feedback_20261024_v1/README.md)的 `f1ca7bd2…` seal，不复制18项prefix。保存源录制/恢复、实际导出、两次完整原生208E1、summary、trip/routes、collisions、输入/执行源码、130/40/52/182及compact事件。大instant/queues与本地runtime只存路径、大小及SHA，原始逐行核验由已绑定独立审计保留。另薄存失败mixed warmup9和fill9（TAE430/SSE5892）的数值、E1、scope、输入hash及独立结果。

这些条件结果显示控制history会改变需求扰动的响应。不能把损失差相减当作独立控制贡献，也不能据此推断硬容量、全局上限、真实OD或现场控制正确性。

离线核验不启动SUMO，在仓库根目录执行：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_275_fixed_history_20261024_v1/reproduce.py
```

可选原生复现必须显式执行，并使用新的包外输出目录。先设置项目环境：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH=(Join-Path $PWD '.venv/Lib/site-packages')+';'+(Join-Path $PWD 'plugins/astra-sumo/src')
$env:SUMO_HOME=(Join-Path $PWD '.venv/Lib/site-packages/sumo')
$env:PATH=(Join-Path $PWD '.venv/Scripts')+';'+(Join-Path $PWD '.venv/Lib/site-packages/sumo/bin')+';'+$env:PATH
.venv/Scripts/python.exe benchmarks/hamburg_ring1_275_fixed_history_20261024_v1/reproduce.py --mode native-full --case reference --output-dir outputs/275-static-reference-reproduction
```

封包和离线核验未新增交通仿真。Pass只指有效的模拟history条件对照，不是拟合改善或field验证。
