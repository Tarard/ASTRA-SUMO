# 汉堡294变量联合OD SPSA诊断证据

这是一次固定模型、路网和控制的诊断试验，不是完成标定的正式结果。
实际完成实数更新：5。新增完整原生测量：11；有原生请求的尝试：11。
算法终点实际复测：True。初值复用历史实测：True。
按SSE最低的安全实测端点：initial；按MAE最低的安全端点：initial。
程序终止选择：final。观察到健康原生全零：False。
主130项零误差目标尚未达到。

程序完成不等于标定完成。算法更新终点、最终复测和最好扰动端点分别保留，不能互相替代。
原固定OD参考 initial 仍保留：SSE130=6898，MAE130=5.0923076923。本轮11次新增原生测量均未优于该参考。
最终向量有独立原生复测：SSE130=9692，MAE130=6.2，最大绝对误差29；未入网45辆、在途19辆，healthy=false。最终复测是新增测量中误差最低的一次，但不替换原参考。
保留主窗全部130项真实计数、52项初始化计数和40项派生站点求和，没有删除难字段。
参数固定为K=5、A=0、c=1、alpha=0.602、gamma=0.101、seed=104。首对损失差D=-2500，按目标初始步长1确定a=0.0008。原生步长0.1秒、终点14400秒，使用libsumo 1.27.1；每对评价并行执行，各自使用独立进程。
模型原字节固定；联合分位数采样与整数化规则不变。不同数量仍会改变具体路线、车道、时刻份额和总车辆数。
完成一次实数更新不保证整数车辆需求改变；离线中心等价证明按原样保留，不能据此省掉计划的最终原生复测。
第3、4轮均更新了实数向量，但整数车辆需求没有改变；两轮中心的需求XML和车道意图与第2轮中心逐字节相同。
initial-cache单列真实历史原生证据，不是新制造的initial/native。原始记录中的绝对路径只作历史来源，复跑不会把它们当当前路径。
所有警告、unsafe/failure、独立分析和算术审核均保留。instant与queues留本地哈希清单，完整贡献身份已压缩保存。
support-audit只核对固定模型的路线支持范围、源数据和合法连接；其中的静态路径存在性不证明原生通行、真实分流比例或现场几何。该审查新增原生运行数为0。

| 实测 | SSE130 | MAE130 | TAE182 | 未入网 | 在途 | 碰撞 | 健康 |
|---|---:|---:|---:|---:|---:|---:|---|
| initial | 6898 | 5.092308 | 744 | 7 | 14 | 0 | False |
| iteration-0001-plus | 12838 | 7.184615 | 1069 | 82 | 80 | 0 | False |
| iteration-0001-minus | 15338 | 7.661538 | 1157 | 47 | 104 | 0 | False |
| iteration-0002-plus | 14700 | 7.784615 | 1151 | 113 | 69 | 0 | False |
| iteration-0002-minus | 10906 | 6.507692 | 964 | 91 | 32 | 0 | False |
| iteration-0003-plus | 11218 | 6.769231 | 1033 | 33 | 33 | 0 | False |
| iteration-0003-minus | 12565 | 6.746154 | 1038 | 58 | 33 | 0 | False |
| iteration-0004-plus | 13114 | 6.984615 | 1094 | 84 | 59 | 0 | False |
| iteration-0004-minus | 13029 | 7.315385 | 1107 | 98 | 62 | 0 | False |
| iteration-0005-plus | 9933 | 6.223077 | 954 | 48 | 18 | 0 | False |
| iteration-0005-minus | 10644 | 6.630769 | 1009 | 107 | 15 | 0 | False |
| final | 9692 | 6.200000 | 916 | 45 | 19 | 0 | False |

从仓库根目录离线复核：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1/freeze_evidence.py --verify-only benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1`。
显式原生复跑：`.venv/Scripts/python.exe benchmarks/hamburg_ring1_joint_od_spsa_20261023_v1/reproduce.py --output-dir outputs/hamburg-joint-od-reproduction`。
reproduce.py只有显式调用才通过既有CLI复跑完整试验。不提供当前可验证的初值实测目录时，将重新测量初值，额外增加1次原生运行。
冻结不会自动启动仿真。该试验不能证明唯一真实OD、现场SPaT、收敛或真实世界预测精度。
OSM数据：© OpenStreetMap contributors，ODbL 1.0。
