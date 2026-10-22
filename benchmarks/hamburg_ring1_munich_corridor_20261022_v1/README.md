# 汉堡五路口走廊剪枝与重新标定

范围已生成并检查，航拍before/after已先展示。新计数标定尚无完成结果。
本次仍是README的Binnenalster/Ring1，LSA104、118、119、200、535及原固定bbox。
采用[已核查文献中的走廊和边界处理](../../docs/research/hamburg-pruning-literature-2026-10-22-v1.md)，没有搬用慕尼黑研究筛除检测数据的做法。

## 已验证的范围

| 项目 | 原参考 | 本次候选 |
|---|---:|---:|
| 普通道路段 | 360 | 236 |
| 官方物理路口 | 5 | 5 |
| 官方构建部分及程序 | 9 | 9 |
| 检测字段 | 13 | 13 |
| 主窗字段时段 | 130 | 130 |
| 派生站点时段 | 40 | 40 |

排除124条范围外道路。保留既有单次通过通路、五个路口的全部现有进出口、13个断面及必要反向车行道。
各路口进口向上游、出口向下游延伸到源网络道路边界，优先选择源道路等级较高的可达边界。
这些是模型道路断口，不是已测得的现场OD。完整保留既有路口，不代表此前未确定的官方车道对应已解决。
每条道路的理由见[selection.json](selection.json)，边界见[boundaries.json](boundaries.json)。

保留道路的整个普通道路XML内容恢复为原参考，形状、显式长度、宽度、车道数和权限均不改变。
保留道路间的全部加载车道转向权限逐项相同，没有新增或删除允许的转向。
九个官方程序逐字节相同。其他保留程序仅按原通路和过街道路身份对应信号编号，时长不作优化。
结果有50个控制程序，其中40个非官方OSM程序仍为声明的感应占位程序。
Z.15继续使用带标签的K9备选A。现场SPaT、200施工阶段差异和既有535曲线限制仍未解决。
[范围保持检查](corridor-preservation.json)记录实际源与候选哈希。SUMO 1.27.1完成最终网络加载。

## 固定航拍before/after

![范围对照](figures/corridor-comparison.png)

左为360条普通路段的已重建参考，右为本次236条走廊。
两幅图使用同一2026年LGV航拍、同一EPSG:25832范围及尺寸，由`astra review render`产生。
航拍SHA-256为`e60229a4341be5415381a9cd36732d3a026fd972c7d7df3969597418d1b3d902`。
源网络为`cc04537494708721e1ad87409ab47cef249c3774dbaf9ed3ec7a7b01710672a4`。
候选为`2d29bdb4183a9491bbd220324742d3a48a3484a96de8adc0eda984ad477cdb7e`。
图的请求与[绘制记录](aerial-review-report.json)保留这些身份。展示产物和最终执行输入的网络字节相同。
2026年航拍不能确定为计数当天拍摄，也不证明MAP施工阶段与现场相同。

![LSA118对照](figures/lsa118-comparison.png)

NetEdit补充图及记录见[before](netedit-before/)和[after](netedit-after/)。两者均为真实NetEdit 1.27.1。
before复用同一参考哈希的原截图，after重新捕获本候选。
中心不变，按网络初始范围调整百分比zoom，以保持相同的物理宽度。after在Inspect模式选中LSA118。

## 重新准备需求

只使用在原先无碰撞、无传送完整运行中有原生E1完整通过记录的29组路线与车道安排。
按已有道路等级入口上限形成份额先验，在每个字段和时段内重新分配，并限制各入口合计请求数。
同一入口的保留出口路线先均分。该选择是声明的需求先验，不是测得的OD或原生通行能力。
整数种子精确满足输入计数，只用于发起新仿真，不能据此接受标定。

模型仍有8640辆，包括1953辆初始化车和6687辆主窗车。不是现场独立车辆总数的识别结果。
每辆模型车的标识、请求时刻、目标字段和目标时段保持不变。路线份额改变。
观察路段的原有7.5米经过点和车尾清空引导继续使用，三处已声明的路口车身引导保持原样。
这是驾驶意图假设，不是已经验证的自然驾驶行为。

| 入口 | 原参考请求数 | 新种子请求数 |
|---|---:|---:|
| 25866659#1，约8.91米短入口 | 376 | 106 |
| 31106387，服务道路 | 310 | 41 |
| 448511533#3 | 622 | 73 |
| -983227468#1 | 445 | 332 |
| 44532531，主线入口 | 3334 | 3604 |
| 963776077#0，西侧主线入口 | 0 | 1067 |

上述为全体模型车的请求数，尚未证明这些车辆能进入或按目标时段通过。
实际冻结输入位于[inputs](inputs/)，压缩文件解压后保留原字节。
所有130个主窗目标及52个初始化目标原样保留，未删除困难字段，也未填补缺失值。

## 本轮原生标定范围

先运行本候选的新需求种子，再最多运行两次原生计数反馈候选，总计不超过三次。
每次从零时刻连续运行，不依赖尚未验证的状态恢复。
仅使用E1的`nVehContrib`比较全部130行，并报告40个字段和。
首个种子若不安全或未完整测量，不继续拟合。运行保留300秒全体在途车静止停止规则。
反馈只使用本次新运行的通知与计数，不能套用旧参考的排队延误。
接受结果仍须全部计数零误差，且无碰撞、传送或大量未进入。有限尝试未达零不构成不可达证明。

## 执行与资料

- 范围和需求：[hamburg_corridor_pruning.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_corridor_pruning.py)。
- 原生循环：[hamburg_corridor_calibration.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_corridor_calibration.py)。
- 执行：[run-calibration.py](run-calibration.py)。用仓库`.venv`，指定新输出目录和固定图像目录。
- 测量：[detector_demand_replay.py](../../plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py)。分析：[hamburg_readme_results.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)。
- 当前本地候选：`outputs/ring1-munich-corridor-20261022-v3/`。计数运行目录：`outputs/ring1-munich-corridor-calibration-20261022-v1/`。
- 早期转换候选和未运行的准备文件仍保留在旧版outputs，不作为本次原生结果。

底图：Freie und Hansestadt Hamburg, LGV，Datenlizenz Deutschland Namensnennung 2.0。
原道路数据：© OpenStreetMap contributors，[ODbL](https://www.openstreetmap.org/copyright)。
