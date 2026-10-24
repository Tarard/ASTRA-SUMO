# Hamburg OD 定义与 SPSA 核对，2026-10-22 V1

状态：道路与需求核对已完成，标准 SPSA 的已知解和原生 E1 检查通过。尚未完成汉堡 OD—SPSA 标定。
原始计数、道路、控制和已有仿真记录均未修改。

## 文件与结论

- [audit.json](audit.json)：旧需求的 21 个 OD 对、294 个出发单元和内部终点核对。
- [od-support.json](od-support.json)：495 个候选 OD 对与 14 个出发时段。初值和路线分配明确留空。
- [candidate-routes.json.gz](candidate-routes.json.gz)：555 条候选路线及 1677 条因重复行程排除的路线。
- [paper-inventory.json](paper-inventory.json)：论文网址、版本说明及本地 PDF 散列。没有将论文全文加入仓库。
- [方法说明与公式对应表](../../docs/research/hamburg-spsa-od-method-2026-10-22-v1.md)。
- [验证日志](tests.log)和[验证 XML](tests.xml)。

旧需求的 2589 个请求在内部道路 `27064186#0` 结束。
用户已批准重组 21 个 OD、14 个出发时段的初值。
这些行程已沿合法的前向道路延长到模型出口 `199500676`。
生成初值保留全部 8640 个请求、发车时刻和已声明的路线及车道先验。
初值与生成规则保存在 [approved-od-model.json.gz](approved-od-model.json.gz) 和 [initial-od-vector.json](initial-od-vector.json)。
45 条入口与 41 条出口是模型端口候选，包括范围切口和前向终端。
它们尚未被认定为现场真实起终点。495 个 OD 对也不是已接受的标定范围。
全部 13 个字段在候选道路路线中有覆盖。该检查不证明车道分配或原生 E1 通过。

## 重做核对

从仓库根目录运行。使用仓库 `.venv`。

```powershell
$env:PYTHONPATH="$PWD/plugins/astra-sumo/src"
& .venv/Scripts/python.exe plugins/astra-sumo/scripts/audit_hamburg_od_definition.py `
  --case-dir benchmarks/hamburg_ring1_munich_corridor_20261022_v1 `
  --output-dir outputs/hamburg-od-audit-new
```

核对函数为 [`hamburg_od_definition.py`](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_od_definition.py)。
输入来自[原冻结走廊](../hamburg_ring1_munich_corridor_20261022_v1/README.md)。
原始结果目录为 `outputs/hamburg-od-definition-audit-20261022-v2/`。

标准 SPSA 为 [`spsa.py`](../../plugins/astra-sumo/src/astra_sumo/core/spsa.py)。
其目标函数由调用方提供，未将解析计数模型当成原生 SUMO。
测试使用已知的耦合 OD 问题，核对公式、固定随机序列、两次损失计算和边界投影。
它们不是汉堡标定结果。正式扰动试验还需固定整数车辆适配、增益和停止设置。
[`run_hamburg_od_spsa.py`](../../plugins/astra-sumo/scripts/run_hamburg_od_spsa.py)可以单独评价整数初值，或读取显式设置运行标准 SPSA。

全部 130 个字段目标与 40 个派生站点目标保持不变。
最终结果仍要求零残差、无碰撞、无传送，并检查车辆是否完整进入及完成行程。
