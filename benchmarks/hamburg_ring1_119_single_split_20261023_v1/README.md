# LSA119 单连接内部等待复现

本包保留四辆车和一个实测前车速度边界，用于检查单条转向的内部等待表示。
它不是四辆车自然形成原有排队的复现，也不提供现场信号时序或计数标定结论。
前车每0.1秒的期望速度来自未改动的原生观测，原生`speedMode=31`始终保留。
没有移动或指定车辆位置的运行中命令，也没有修改信号程序。

基线在7031.6秒和7032.0秒发生两次碰撞，车对和内部车道与完整运行相同。
第一次碰撞的全部记录与完整运行一致，第二次的位置和速度有差异。
仅对`24483344#1 lane0 → 79014037#1 lane0`设置`contPos=0`后，同一速度边界下未发生碰撞。
候选保留238条普通道路、691条合法车道转向、所有信号绑定和程序。
其他完整曲线和边界保持不变，目标曲线只减少原有分段点，逻辑长度保持74.692751米。
这个结果支持继续核对内部等待表示，尚不证明唯一的碰撞原因。

`fixture/`包含只读输入、原始清单和`runner.py`。
gzip文件保留原始字节，根目录清单同时核对压缩和解压哈希。
`review.json.gz`保留此前四次小探针的结果，`full-source-collisions.xml.gz`保留完整运行的碰撞记录。
回归只将空网推进到7008秒，再运行27秒，不运行7035秒的完整需求。

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'plugins/astra-sumo/src')
$env:SUMO_HOME = (Join-Path (Get-Location) '.venv/Lib/site-packages/sumo')
.\.venv\Scripts\python.exe -m pytest tests/test_junction_split_fanout.py -k four_vehicle -q
```

OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
