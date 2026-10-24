# Hamburg chronological OD feedback, 2026-10-24

完整14400秒运行达到健康主窗MAE **2.115384615**、TAE275、SSE2833、最大误差22，优于此前remove43的TAE324。8589辆全部进入并到达，每辆车一次唯一正确字段贡献，等待、在途、碰撞、teleport及discard均为零。130主窗值仍未全部归零，真实OD和现场SPaT仍未识别。

| Measurement | Result |
| --- | ---: |
| Main130 TAE / SSE | 275 / 2833 |
| Derived station40 MAE | 6.125 |
| Initialization52 TAE / SSE | 97 / 1117 |
| All182 TAE / SSE | 372 / 3950 |
| First main13 TAE / SSE | 9 / 41 |
| Requested / inserted / arrived | 8589 / 8589 / 8589 |
| 535 Z5–Z8 protected values | All56 exact |

初始化52个原生区间全部属性保持不变。首主窗3600–4500秒只有11个值精确，Z12仍缺5、Z13仍缺4。派生40值不作为独立观测重复计入目标。

此结果来自多步输入变化，不能说相对旧best“只删5辆”。完整来源为：remove43的8597辆先删除3个既有假设请求，得到已冻结的window0_remove3/8594辆参考；其首窗269个Z11/Z12/Z13请求延后30秒；随后仅将od_0097_v00010改用既有counter入口前缀，保留24483192之后完整Z11 suffix、stop、字段和时刻；最后再从输入删除5个h4 Z5请求。道路、控制、车辆类型及130+52真实观测保持不变，无运行中删车或量化重生成。counter4等旁枝仅作prefix比较，不是这条best输入链。

新增counter→1257937836#0组合为第22个OD。原21对顺序保留并追加一对，14个时期重建为308维，8589条意图的实际od_cell均重算；不声称仍为原294维。元数据坐标重排不等于相同数量车辆改变路线。

直接输入父h4_counter_z11_one仅测到4500秒、首13 TAE14，没有完整主130结果。原始shared verdict的source_parent指另一份完整window0_remove3/TAE448参考。`full-result.json`与独立审计明确区分二者，不借旧完整结果填充直接父的117个未知值。

本包保留必要的prefix索引、实际13值、初始化52值、其余117个null、scope、提案和来源hash。各prefix `status=pass`只指有限记录及安全检查通过，不表示全130拟合成功。失败或未改善的有限响应保留；大prefix trace只记本地路径和SHA。完整best的8个prepared文件、原生208E1、summary、tripinfo、完整routes、collisions、130/52/182/40表、compact事件、实际执行wrapper/shared及额外3个依赖快照均保留。完整best的instant/queues只记本地hash，已封独立审计保留raw instant重建核对。

条件库存界也保留：若固定本次52初始化、每请求仅一次当前意向字段贡献和当前字段总量，Z5尚缺1次、535 Z4尚缺11次正确主窗贡献。这个必要条件限制当前固定库存，不是观测矛盾或整体归零不可能。正库存余量也不证明时刻、路由或控制上可行。

离线核验不启动SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1/reproduce.py
```

可选完整复现须使用记录的项目SUMO1.27.1及项目环境，显式选择此case并写入新的包外目录：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_chronological_od_feedback_20261024_v1/reproduce.py --mode native-full --case h4_counter_z11_minus5_z5 --output-dir outputs/chronological-best-reproduction
```

本次封包、核验和测试未新增仿真。结果支持一个更低残差的完整模型输入，不证明真实OD、现场控制、最优或校准完成。
