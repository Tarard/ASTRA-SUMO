# 197 局部延续：航片与 NetEdit 复核

主入口为 [固定全局及路口图](aerial/review.html)，另有 [197紧裁剪图](detail197/review.html)。均由仓库既有 `astra review render` 生成，没有改写旧图片或自制图像拼接。

前后网络分别绑定源 `b6acb7f5...` 与候选 `68d2f6d2...`。全局范围固定为经纬度 `[9.9815,53.5525,10.0065,53.5598]` 的原 EPSG:25832 包络 `[565012.777442586,5934182.489688758,566680.1599791404,5935017.708749339]`。两图使用同一现有全幅 LGV 2026 航片（SHA `e60229a4...`）；其覆盖恰好与该范围相同，没有把局部535航片拉伸成全局。197 的125米和40×35米视图都是这张全幅图的明确裁剪，不代表新增高分辨率影像。

青色为保存的 SUMO 普通车道，粉色为保存的原生内部曲线，黄/绿为步行几何。官方 MAP/KML 是 `review-context.json` 中另行绑定的来源，不是航片，也不是这些粉色曲线。现有航片渲染器不提供第三层 MAP 矢量叠加；本次没有将其伪装为该图的几何结果。

| 新增普通连接 | 保存的内部车道 | 模型信号索引 |
| --- | --- | --- |
| -186821028 lane3→186821027#0 lane4 | :1975693623_0_2 | 14 |
| -186821028 lane4→186821027#0 lane5 | :1975693623_4_0 | 15 |

控制元数据仅有三处旧信号优先级大小写变化：phase0/1 的旧掉头9、phase3 的旧停车左转2由 `G` 改为许可 `g`。原灯色可用时间、phase参数及其他信号程序保持原声明；新14/15继承原直行1。它是新增冲突下的模型优先级适配，不是现场 SPaT，也不是计数拟合配时。这些内容在元数据中列明，未用静态航片表达动态信号状态。

`netedit-source/`、`netedit-candidate/` 为既有 background-review CLI 采集的补充 Inspect/TLS/Connection 视图，绑定各自网络 SHA。它们是 NetEdit 保存模型的上下文，不代替固定范围主图。窗口原有警告保留，截图不构成驾驶安全、控制来源、容量或零误差证据。

两次审图的 `render_status=pass`、`inputs_unchanged=true`，总体状态保持 `review_required`、没有 promotion authority；CLI因此返回1，不能据此误判为图片生成失败。源网、候选、控制、航片、官方来源及输出图片的完整哈希见 JSON 与清单。本次没有运行交通仿真或改动核心/输入。

航片署名：Freie und Hansestadt Hamburg, LGV；Datenlizenz Deutschland Namensnennung 2.0。拍摄年份2026，不能据此断言十月现场控制和全部标线完全一致。

内部车道 ID 可随原生重编译重新编号；识别新增连接应使用表中的普通道路四元组，不用内部 ID 差集。

NetEdit 补图（前后相同中心、缩放和窗口尺寸，六图质量检查均通过）：

| 模式 | 源网 | 候选 |
| --- | --- | --- |
| inspect | [source](netedit-source/direct-1975693623-6044aaa74d-inspect.png) | [candidate](netedit-candidate/direct-1975693623-6044aaa74d-inspect.png) |
| tls | [source](netedit-source/direct-1975693623-6044aaa74d-tls.png) | [candidate](netedit-candidate/direct-1975693623-6044aaa74d-tls.png) |
| connection | [source](netedit-source/direct-1975693623-6044aaa74d-connection.png) | [candidate](netedit-candidate/direct-1975693623-6044aaa74d-connection.png) |
