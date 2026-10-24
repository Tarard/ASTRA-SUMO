# Hamburg early-window quantity responses, 2026-10-24

两个健康试验均未改善全130主窗拟合。最佳仍是[父数量包](../hamburg_ring1_119_late_demand_thinning_20261024_v1/README.md)的remove43：MAE **2.492307692**、TAE324。父manifest绑定最终 `9141f590…`。主窗130值仍未全部归零。

| Case | Input / arrived N | Added / removed | First main13 TAE | All main130 TAE | MAE | SSE | Max | All182 TAE | Station40 MAE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Source remove43 | 8597 | 0 / 0 | 13 | 324 | 2.492307692 | 3598 | 23 | 421 | 7.95 |
| window0 | 8604 | 10 / 3 | 9 | 356 | 2.738461538 | 5498 | 36 | 453 | 8.40 |
| window0_remove3 | 8594 | 0 / 3 | 10 | 448 | 3.446153846 | 6738 | 24 | 545 | 10.55 |

“First main13”指3600–4500秒的13个真实主窗字段，不是初始化。该窗误差下降不能作为全局成功。两个试验各自从remove43出发；remove3不是在失败的window0上继续删车。原始 `diagnostic_measurement_complete` 判定保留，表中的不选用结论来自全130误差回退。

window0新增10个明确标为假设的ID，复制既有相同OD/字段的车辆、完整路线和stop模板；只为新请求设定新出发时刻。它同时从输入删除2辆119 Z4和1辆535 Z4。remove3只删除相同3个ID。全部8594辆共同幸存请求的XML、意图、时刻、路线、stop和相对顺序保持不变。新增ID与原件ID分开核验，294维实际库存重算。两端点的旧lag只用于选取请求空隙，不是新通过时刻的预测。

两个运行均全部进入并到达，各自每辆车只贡献一次预定字段，等待、在途、碰撞、teleport和discard均为零。初始化52值逐行不变，TAE97、SSE1117；前52个原生E1区间全部属性相同。535 Z5–Z8的56值仍全部精确。这些前缀属性相等不代表所有车辆轨迹相同。

window0的10个新ID实际都在首主窗贡献，但9个原有Z11/Z12/Z13请求移至后窗，故新增贡献不等于净计数收益。后补primary诊断保留入路延迟、服务重排、8个Z12/Z13错窗ID和有限早段无通过记录。原offset符号错误及修正前文件完整保存；正确的声明+15主方向绿窗为 `[90k+21,90k+50)`。此修正只影响分析，不改任何原生运行，也不证明硬容量或唯一阻塞原因。

本包保留全部输入、实际执行准备脚本、执行wrapper及实际共享依赖快照，另存原始130/52/182/40、208E1、完整trip/route、身份、runtime、原始判定与独立复核。原XML/JSON/日志按原字节gzip或相同SHA引用。大型instant/queues仅记本地路径、大小及SHA；原始instant对compact的核对由已封独立审计保留。请求数量是模型先验，不是已识别的真实OD。

离线核验不启动SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1/reproduce.py
```

如需显式重跑，使用项目 `.venv` 和记录的SUMO1.27.1环境，将 `PYTHONPATH`、`SUMO_HOME` 指向本项目。输出目录必须尚不存在且位于冻结包之外。

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_early_quantity_feedback_20261024_v1/reproduce.py --mode native-full --case window0 --output-dir outputs/early-quantity-window0-reproduction
```

本次封包、核验和测试未新增原生运行。两项结果不识别真实OD、现场SPaT、最优解或收敛性。
