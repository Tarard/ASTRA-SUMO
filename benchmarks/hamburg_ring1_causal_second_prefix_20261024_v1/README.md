# 前26初始化计数归零：三次1800秒原生对照

本包只包含3次0–1800秒原生运行，不含stage2 full或另一个global timing extension。reference首26 TAE30/SSE318，120232候选TAE3/SSE5，126232候选TAE0/SSE0；最后901个完整事件均为唯一预期字段贡献、零unsafe。零误差仅属于前26个初始化观测，剩余156行（26初始化+130主窗）显式null，主130未测、完整健康未证明。主窗最好3.7153846不变。

输入来源是固定dfa505 stage1包的434辆初始时刻候选。三次均保留全部8640未来请求和全部真实182观测。两候选只改第二900秒窗的467个depart：232个119 Z11/Z12/Z13请求由−120改为−126秒，其他235个均−90；实际requested bin和294数量不变，原第一窗434个请求不动，1800秒后7739个请求不动。全部ID、路线、stop、field、vType、网络和控制不变。18k summary与26个原生E1窗口保留，第一13 E1全属性及前9000条非CPU summary保持stage1实际前缀。

声明的119 link0/1绿窗906–935秒，与120232原生车辆前端退出前序路段的935.3/936.1秒证据一并保留。它们不是检测器进入时刻；后续内道行程和车尾离开仍需时间。该材料没有记录本候选实际TLS历史，也不是现场SPaT，不能把声明green解释为新采集的actual state。原证据中的“下一步未测”描述属于当时阶段，后来的126232结果另列。

短时core完整人口状态不适用于前缀。原19项检查与独立证据另存，未到达车辆和end记录不表示人工删车；未完成/未测保留，不把未来填0。较差的reference和120232结果不丢弃。所有来源与历史执行脚本按原字节gzip或固定stage1包SHA引用；queues仅localhash，3份短instant完整gzip保存。

离线核验：`.venv\Scripts\python.exe benchmarks/hamburg_ring1_causal_second_prefix_20261024_v1/reproduce.py --mode verify`，不运行SUMO。

显式原生prefix复现：同项目.venv及记录的SUMO/Libsumo1.27.1实现，运行`.venv\Scripts\python.exe benchmarks/hamburg_ring1_causal_second_prefix_20261024_v1/reproduce.py --mode native-prefix --case 126232 --output-dir outputs/reproduce-causal-second-prefix`。可选reference/120232/126232，只到1800秒。它从当前仓库的冻结显式输入调用core，不生成OD，不取历史outputs绝对路径输入。必须新目录；本次冻结未新增原生。离线核验不等于重新原生复现。
