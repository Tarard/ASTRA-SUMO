# 固定健康源灯色历史的条件对照

本包只冻结两次已完成原生运行。共享的50条static历史来自健康源的模拟SaveTLSProgram，control SHA74bff751…，每条恰覆盖14400秒。两份新actual SwitchStates均与该完整state/program历史一致；不是现场SPaT，不按原始phase index作比较，也不在14400秒后重复外推。

| 条件 | MAE130 | SSE130 | TAE130 | TAE52 | TAE182 | 到达/等待/在途 |
|---|---:|---:|---:|---:|---:|---|
| reference历史自回放 | 3.715385 | 6709 | 483 | 105 | 588 | 8640/0/0 |
| 同一单车+31s，固定reference历史 | 3.746154 | 6929 | 487 | 105 | 592 | 8640/0/0 |
| 原mixed控制下同一单车改动（引用旧包） | 4.092308 | 8814 | 532 | 105 | 637 | 8634/0/6 |

reference14项严格输出复核通过：208行E1全属性、144000行summary（仅排CPU duration）、完整instant、tripinfo、arrived routes/stops/exitTimes、driver holds及期末状态；颜色/program逐区间一致。one-car7项完整性/安全/历史执行检查通过，不要求它匹配原mixed交通，也不把pass状态当优化接受。两份均零碰撞/传送/discard、535指定56项零残差。全部130/40/52/182及compact完整贡献保留。

两份原始8640需求、route/stops/vTypes/lane-intentions、网络、真实targets、探头、四bodyhold节点、Libsumo1.27.1/0.1s/seed104/空网起点保持各自来源，未重新生成OD。唯一控制处理为共同的模拟灯色历史，观察adapter只添加SwitchStates输出，不增加驾驶/TLS setter。counterpart比原mixed条件完成情况改善，只能作此历史下的条件对照；3.746仍高于健康3.715，不能称成功优化、真实OD或现场控制证明。独立逐车记录中，该车虽然实际出发延后31秒，Z4完整贡献通知仍为9899秒、同9000–9900窗，不能把请求时移当作观测平移。健康工作基准不变。

source-study的README/manifest为准备阶段快照，原样保存在study-source，不能把其中“未运行”文字当成当前状态。新原生、paired-result、原比较中的失败差异、独立审计及所有存在的failure记录均保存；大instant/queues仅local-only哈希，迁机后无原件就不能逐事件重读。未纳入后续128 jointOD提议/试验，也未改公共docs。

离线verify会重算计数、reference输出parity和50色史，不启动SUMO。reproduce.py只离线重算本包证据。原执行脚本/计划/请求均保留，若另行显式复跑原生需项目.venv及固定来源输入、原instant大文件；本次冻结没有新仿真。
