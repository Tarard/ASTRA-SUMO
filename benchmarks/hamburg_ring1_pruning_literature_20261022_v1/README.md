# 汉堡剪枝文献与只读范围清单

本记录支持[18项文献核查与范围建议](../../docs/research/hamburg-pruning-literature-2026-10-22-v1.md)。
没有改变网络、需求、控制或检测器，没有新剪枝仿真。

## 当前网络

实际网络有360条普通路段，其中343条允许乘用车。
当前8640辆模型车的路线使用116条路段，另有227条允许乘用车的路段不在这些路线中。
未出现在生成路线中，不等于现场无交通，也不证明可以删除。
过街连接、步行区域和路口内部通路不计作普通道路。

[road-inventory.csv](road-inventory.csv)保留全部360条普通道路。
字段包括源道路类型、乘用车权限、模型车经过次数、模型入口请求数、检测字段和官方路口邻接标记。
邻接标记只根据当前网络端点身份，不代表完整官方车道对应。
所有道路的删除判断均为`not_assessed`。
[scope-inventory.json](scope-inventory.json)保留统计口径、源文件哈希和版本。

复算：用仓库`.venv/Scripts/python.exe`执行[inventory-scope.py](inventory-scope.py)。
使用sumolib 1.27.1，无需启动SUMO。
脚本读取固定参考，检查8640辆、无重复道路、乘用车通行与全部13字段，再写出确定性的清单。

## 文献资料

- [retrieval-record.json](retrieval-record.json)：机构或作者网址、获取结果、成功下载的校验值，以及访问失败。
- [publication-metadata.json](publication-metadata.json)：Crossref记录。部分请求受速率限制，相关身份另按出版社或机构记录核对。
- [munich-figure4.png](munich-figure4.png)：Keler等（2023），Figure 4，印刷页157，原PDF第5页。

图源为[原始机构PDF](https://d-nb.info/1297415906/34)，校验值见获取记录。
作者以[CC BY 3.0 DE](https://creativecommons.org/licenses/by/3.0/de/)发布。
图片从PDF直接渲染图和图题区域，不改图中道路或标签。
它用于说明另一项研究的范围选择，不表示该研究取得有效计数标定。

全文和提取文本保留在本地`outputs/hamburg-pruning-literature-20261022-v1/`，不公开转载受限全文。
本记录没有把其他论文引用的场景介绍，替代原论文的方法证据。
