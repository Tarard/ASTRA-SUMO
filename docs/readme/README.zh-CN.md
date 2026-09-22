<p align="center">
  <img src="../assets/banner.png" alt="ASTRA for SUMO" width="100%">
</p>

# ASTRA

<p align="center">
  <strong>Automated Simulation of TRAnsportation networks for SUMO</strong><br>
  面向 SUMO 的交通网络自动化仿真
</p>

<p align="center">
  ASTRA 将真实交通数据和自然语言任务转换为 SUMO 仿真。
</p>

<p align="center">
  <a href="../codex-plugin-install.md">安装</a> ·
  <a href="../README.md">文档</a> ·
  <a href="../../examples/01_signal_control_audit/task.md">示例</a> ·
  <a href="../../LICENSE">MIT 许可证</a>
</p>

<p align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.de.md">Deutsch</a>
</p>

<p align="center">
  <img src="../assets/astra-framework.png" alt="ASTRA agent 驱动的交通场景构建工作流" width="100%">
</p>

## ASTRA 能做什么

<table>
<tr>
<td width="33%" valign="top">
<h3>构建</h3>
从 OpenStreetMap、车辆轨迹数据和道路施工图构建 SUMO 路网。
</td>
<td width="33%" valign="top">
<h3>校准</h3>
使用真实传感器观测重建交通需求并校准仿真。
</td>
<td width="33%" valign="top">
<h3>仿真</h3>
根据自然语言指令继续运行 SUMO 实验。
</td>
</tr>
</table>

## 快速开始

```powershell
codex plugin marketplace add Tarard/ASTRA-SUMO --ref main
codex plugin add astra-sumo@astra-sumo
```

然后可以向 Codex 这样提问：

```text
Use ASTRA to build a SUMO network from this OSM area.
Check connectivity, traffic signals, and routeability.
```

ASTRA 需要 Python 3.11+ 和 Eclipse SUMO。

ASTRA 在此新仓库中延续 Torii 项目。安装变化和旧命令兼容说明见[迁移指南](../astra-migration.md)。

## 汉堡数字孪生

ASTRA 正被用于重建和验证汉堡市中心的一段真实交通走廊。

<p align="center">
  <img src="../assets/hamburg-digital-twin/torii-v1-corridor-aerial-sensors.png" alt="ASTRA V1 在航拍影像上重建的汉堡走廊，包含车道连接和虚拟传感器" width="100%">
</p>

<p align="center"><sub>ASTRA V1 走廊叠加在航拍影像上，包含车道连接和虚拟传感器。</sub></p>

ASTRA 在同一工作流中结合官方交通数据、航拍影像和 SUMO 路网重建。

<p align="center">
  <img src="../assets/hamburg-digital-twin/torii-v1-four-stage-comparison.png" alt="LSA118 四阶段重建：官方 MAP 端点与朝向、重建曲线，以及清洗前后的 SUMO 车道连接" width="100%">
</p>

<p align="center"><sub>LSA118 重建：官方 MAP 端点与朝向、重建曲线，以及清洗前后的车道连接。</sub></p>

当前汉堡校准能够精确匹配总检测器计数，**每个 15 分钟时间段的 MAE 为 0.15 辆车**。

## 文档

[架构](../architecture.md) ·
[安装](../codex-plugin-install.md) ·
[文档](../README.md) ·
[示例](../../examples/01_signal_control_audit/task.md)

## 许可证

ASTRA-SUMO 使用 [MIT License](../../LICENSE) 许可。

早期 Torii 版本已归档至 [Zenodo](https://doi.org/10.5281/zenodo.20627976)。
