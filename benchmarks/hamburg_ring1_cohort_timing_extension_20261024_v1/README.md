# 全时域cohort时刻延伸：初始化26行零与完整验证退步

本包保留一次0–1800秒prefix和一次14400秒full，共两次原生调用。prefix的26项初始化观测全部零残差，901个完整贡献均为唯一预期字段；其余156项当时为null。完整运行保持同一新输入的26个E1区间全属性和18000条非CPU summary，并通过独立事件、到达及hold核对。初始化26零不等于主130归零。

full原判rejected：主130 TAE562、SSE9896、MAE4.3230769、最大误差39；52初始化TAE42/SSE516；全182 TAE604；40派生站点MAE13.15。8640插入、8605到达、35在途、0等待，零碰撞/传送/discard；8622个完整预期字段贡献、无重复或额外字段，535指定56行仍零。主计数与完整健康均未满足选用条件。全局最好3.7153846不变。

输入从已冻结stage2的126232候选延伸：前901请求原字节与意图保持，其余7739个depart中3253个提前126秒、4486个提前90秒；不重采样quantile，不改ID、路线、stop、field、全局21OD总量、网络、控制或真实观测。379辆跨一个实际请求bin，重算294数量后有53个cell改变、L1=114，不能称same-q。实际初始化/主窗请求为1989/6651；真实观测的初始化/主窗贡献仍为1953/6687。departure库存不是passage计数。

source stage2只测过1800秒，没有同输入full父对照。本次full与健康one-share3.938和全局最好3.715分别比较；不能把差异单独归因于7739辆延伸。独立208cohort表以one-share为reference，继承901辆相对它也曾改时。35个在途均属extended_future7739，其中17个已计字段；unfinished trip记录不能定位末端lane或解释堵塞原因。控制及lane先验仍为条件模型，不是现场SPaT。未包括后续服务试验。

原脚本、预检、短prefix原生、完整130/40/52/182/208、compact贡献和独立审计均按原字节gzip或固定旧包SHA保存。独立prefix原文中“379 changed OD-period cells”是措辞误写；保留原文，实际为379辆跨bin、53个数量cell改变。完整instant及两份queues只保留本地大小/SHA，短prefix instant保留gzip；不能迁机重读未打包的完整instant。

离线核验：`.venv\Scripts\python.exe benchmarks/hamburg_ring1_cohort_timing_extension_20261024_v1/reproduce.py --mode verify`，不启动SUMO。

显式完整原生复现：使用本项目.venv与记录的SUMO/Libsumo1.27.1，运行`.venv\Scripts\python.exe benchmarks/hamburg_ring1_cohort_timing_extension_20261024_v1/reproduce.py --mode native-full --output-dir outputs/reproduce-cohort-timing-extension`。它解压本包及固定旧包的显式8640输入，以当前仓库路径调用core，不生成OD、不依赖历史outputs绝对路径；输出目录必须新建。本次冻结没有额外SUMO。离线核验不等于重新原生复现，复现rejected结果不表示标定完成。
