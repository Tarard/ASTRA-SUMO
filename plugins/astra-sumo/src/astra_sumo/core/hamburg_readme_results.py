"""Write the full README workflow report from native measurements, including invalid runs."""

from collections import defaultdict, Counter
from datetime import datetime, timedelta
import html
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import numpy as np

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .detector_demand import read_csv_rows, write_csv
from .route_sensor_matrix import read_contributing_e1_passages


STATIONS = {
    "35822": ("0119", {"Z.11", "Z.12", "Z.13"}),
    "33023": ("0119", {"Z.4", "Z.5", "Z.15"}),
    "30666": ("0535", {"Z.5", "Z.6", "Z.7", "Z.8"}),
    "26585": ("0535", {"Z.4", "Z.9", "Z.14"}),
}


def summarize_hamburg_readme(*, repository, prepared_dir, native_dir, figure_dir, output_dir):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from PIL import Image
    from shapely.geometry import LineString

    root, p, native, source_fig, out = [
        Path(x).resolve(strict=True)
        for x in (repository, prepared_dir, native_dir, figure_dir, Path(output_dir).parent)
    ]
    out = Path(output_dir).resolve()
    if out.exists():
        raise ValueError("Use a new report directory.")
    out.mkdir(parents=True)
    figures = out / "figures"
    figures.mkdir()
    run = json.loads((native / "run.json").read_bytes())
    scenario = json.loads((p / "scenario.json").read_bytes())
    field_rows = read_csv_rows(native / "field-comparison.csv")
    assert len(field_rows) == 130

    def interval_key(row):
        return row["detector_id"], float(row["begin"]), float(row["end"])

    source_rows = {interval_key(row): row for row in read_csv_rows(p / "expected-counts.csv")}
    assert set(source_rows) == {interval_key(row) for row in field_rows}
    field_rows = [{**source_rows[interval_key(row)], **row} for row in field_rows]
    groups = defaultdict(list)
    for row in field_rows:
        groups[row["detector_id"]].append(row)

    def available(row):
        return row.get("measured_nVehContrib") not in (None, "", "None")

    diagnostics = [abs(int(r["measured_nVehContrib"]) - int(r["expected_total"])) for r in field_rows if available(r)]
    stations = []
    for station, (node, assets) in STATIONS.items():
        by_time = defaultdict(list)
        for row in field_rows:
            if row["node_id"] == node and row["asset_id"] in assets:
                by_time[row["begin"], row["end"]].append(row)
        assert len(by_time) == 10
        for (begin, end), rows in sorted(by_time.items(), key=lambda pair: float(pair[0][0])):
            assert len(rows) == len(assets)
            expected = sum(int(r["expected_total"]) for r in rows)
            measured = sum(int(r["measured_nVehContrib"]) for r in rows) if all(available(r) for r in rows) else None
            stations.append(
                {
                    "station_stream_id": station,
                    "begin": begin,
                    "end": end,
                    "expected_total": expected,
                    "measured_nVehContrib": measured,
                    "residual": None if measured is None else measured - expected,
                    "availability": "complete" if measured is not None else "missing_constituent_field",
                    "independent_evidence": False,
                }
            )
    assert len(stations) == 40
    station_residuals = [abs(row["residual"]) for row in stations if row["residual"] is not None]
    write_csv(out / "station-comparison.csv", stations, list(stations[0]))
    write_csv(out / "field-comparison.csv", field_rows, list(field_rows[0]))
    colors = ("#1565c0", "#c62828")
    fig, axes = plt.subplots(7, 2, figsize=(15, 20), constrained_layout=True)
    for ax, (identity, rows) in zip(axes.flat, sorted(groups.items())):
        rows.sort(key=lambda r: float(r["begin"]))
        times = [
            datetime.fromisoformat(r["source_begin_utc"].replace("Z", "+00:00")) + timedelta(hours=2, minutes=7.5)
            for r in rows
        ]
        expected = [int(r["expected_total"]) for r in rows]
        measured = [int(r["measured_nVehContrib"]) if available(r) else np.nan for r in rows]
        ax.plot(times, expected, "o-", color=colors[0], label="Observed")
        ax.plot(times, measured, "s--", color=colors[1], label="SUMO native E1")
        ax.set_title(identity.replace("hh_", "").replace("_", " "))
        ax.set_ylabel("veh / 15 min")
        ax.grid(alpha=0.2)
        import matplotlib.dates as mdates

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.tick_params(axis="x", rotation=25)
    axes.flat[-1].axis("off")
    axes.flat[0].legend()
    fig.suptitle(
        "Hamburg Ring1 | 19 Oct 2026, 07:30–10:00 CEST\n"
        + ("Valid native run" if run.get("healthy") else "INVALID RUN — diagnostic counts only"),
        fontsize=16,
    )
    fig.savefig(figures / "field-counts.png", dpi=140)
    plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)
    for ax, station in zip(axes.flat, STATIONS):
        rows = [r for r in stations if r["station_stream_id"] == station]
        ax.plot(range(10), [r["expected_total"] for r in rows], "o-", color=colors[0], label="Observed field sum")
        ax.plot(
            range(10),
            [r["measured_nVehContrib"] if r["measured_nVehContrib"] is not None else np.nan for r in rows],
            "s--",
            color=colors[1],
            label="SUMO E1 field sum",
        )
        ax.set_title("Station " + station + " (derived)")
        ax.set_xticks(
            range(10),
            ["07:30", "07:45", "08:00", "08:15", "08:30", "08:45", "09:00", "09:15", "09:30", "09:45"],
            rotation=40,
        )
        ax.set_ylabel("veh / 15 min")
        ax.grid(alpha=0.2)
    axes.flat[0].legend()
    fig.suptitle("Station totals are field sums, not independent evidence")
    fig.savefig(figures / "station-counts.png", dpi=160)
    plt.close(fig)
    # Copy images produced by the repository renderer and actual NetEdit capture helper.
    for source, name in [
        (source_fig / "pruning/corridor-comparison.png", "01-osm-to-pruned.png"),
        (source_fig / "junctions-final/corridor-comparison.png", "02-pruned-to-junctions.png"),
        (source_fig / "junctions-final/lsa118-comparison.png", "03-lsa118-before-after.png"),
    ]:
        shutil.copyfile(source, figures / name)
    for folder, name in [("netedit-before", "netedit-before.png"), ("netedit-after", "netedit-after.png")]:
        images = list((source_fig / folder).glob("*-inspect.png"))
        assert len(images) == 1
        shutil.copyfile(images[0], figures / name)
    meta = json.loads((source_fig / "source.json").read_bytes())
    net = ET.parse(p / "network.net.xml").getroot()
    offset = np.array(list(map(float, net.find("location").get("netOffset").split(","))))
    lanes = {lane.get("id"): (edge, lane) for edge in net.findall("edge") for lane in edge.findall("lane")}
    image = Image.open(source_fig / "aerial-2026.png")
    bounds = meta["bounds_epsg25832"]
    fig, ax = plt.subplots(figsize=(15, 8), constrained_layout=True)
    ax.imshow(image, extent=(bounds[0], bounds[2], bounds[1], bounds[3]))
    segments = []
    for edge in net.findall("edge"):
        if edge.get("id").startswith(":"):
            continue
        for lane in edge.findall("lane"):
            points = np.array([list(map(float, p.split(","))) for p in lane.get("shape").split()]) - offset
            segments.append(points)
    ax.add_collection(LineCollection(segments, colors="#00e5ff", linewidths=0.5, alpha=0.7))
    sensor_rows = []
    for loop in ET.parse(p / "field-loops.add.xml").getroot():
        edge, lane = lanes[loop.get("lane")]
        line = LineString([tuple(map(float, point.split(","))) for point in lane.get("shape").split()])
        point = line.interpolate(float(loop.get("pos")) / float(lane.get("length")) * line.length)
        x, y = point.x - offset[0], point.y - offset[1]
        ax.scatter([x], [y], s=32, color="#ff1744", edgecolor="white", linewidth=0.6, zorder=5)
        sensor_rows.append(
            {
                "detector_id": loop.get("id"),
                "lane": loop.get("lane"),
                "position_m": loop.get("pos"),
                "easting_epsg25832": x,
                "northing_epsg25832": y,
            }
        )
        label = loop.get("id").replace("hh_", "").split("_")
        ax.annotate(
            label[0] + " " + label[1] + "." + label[2],
            (x, y),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=7,
            color="white",
            bbox={"boxstyle": "round,pad=.1", "fc": "#111111", "alpha": 0.7, "ec": "none"},
        )
    ax.set_xlim(bounds[0], bounds[2])
    ax.set_ylim(bounds[1], bounds[3])
    ax.set_aspect("equal")
    ax.set_title("13 native E1 count fields | current model positions")
    ax.set_xlabel("EPSG:25832 easting (m)")
    ax.set_ylabel("northing (m)")
    fig.text(
        0.01,
        0.005,
        "Aerial: Freie und Hansestadt Hamburg, LGV (2026); Datenlizenz Deutschland Namensnennung 2.0",
        fontsize=8,
    )
    fig.savefig(figures / "04-sensors.png", dpi=180)
    plt.close(fig)
    write_csv(out / "sensor-positions.csv", sensor_rows, list(sensor_rows[0]))
    planned = {v.get("id"): v for v in ET.parse(p / "demand.rou.xml").getroot().findall("vehicle")}
    trip_rows = []
    if (native / "tripinfo.xml").exists():
        try:
            for _, element in ET.iterparse(native / "tripinfo.xml", events=["end"]):
                if element.tag == "tripinfo":
                    trip_rows.append(element)
        except ET.ParseError:
            pass
    delay_by_entry = defaultdict(list)
    inserted_ids = set()
    for trip in trip_rows:
        identity = trip.get("id")
        if identity not in planned or float(trip.get("depart", "-1")) < 0:
            continue
        inserted_ids.add(identity)
        entry = planned[identity].find("route").get("edges").split()[0]
        delay_by_entry[entry].append(float(trip.get("departDelay")))
    expected_by_entry = Counter(v.find("route").get("edges").split()[0] for v in planned.values())
    delay_rows = [
        {
            "entry": entry,
            "planned_vehicles": count,
            "recorded_inserted_vehicles": len(delay_by_entry[entry]),
            "not_recorded_inserted": count - len(delay_by_entry[entry]),
            "max_depart_delay_s": max(delay_by_entry[entry], default=None),
            "p95_depart_delay_s": float(np.quantile(delay_by_entry[entry], 0.95)) if delay_by_entry[entry] else None,
            "above_30_s": bool(delay_by_entry[entry] and max(delay_by_entry[entry]) > 30),
        }
        for entry, count in sorted(expected_by_entry.items())
    ]
    write_csv(out / "entry-delays.csv", delay_rows, list(delay_rows[0]))
    try:
        passage_report = read_contributing_e1_passages(
            instant_file=native / "instant-e1.xml",
            periodic_file=native / "field-counts.xml",
            detector_ids=run["instant_to_periodic_ids"],
            step_length=0.1,
        )
    except (OSError, ValueError, ET.ParseError) as error:
        passage_report = {"status": "unavailable", "reconciled": False, "reason": str(error), "passages": []}
    write_json_atomic(out / "passage-reconciliation.json", passage_report)
    intentions = json.loads((p / "lane-intentions.json").read_bytes())["vehicles"]
    by_vehicle = defaultdict(list)
    for row in passage_report.get("passages", []):
        by_vehicle[row["vehicle_id"]].append(row)
    passage_rows = []
    for wanted in intentions:
        found = [r for r in by_vehicle[wanted["vehicle_id"]] if r["detector_id"] == wanted["field_id"]]
        actual = found[0].get("time_s") if len(found) == 1 else None
        # Preserve native reconciliation records; an unavailable planned passage stays missing.
        passage_rows.append(
            {
                **wanted,
                "actual_native_field_passages": len(found) if passage_report["status"] == "measured" else None,
                "actual_native_time_s": actual,
                "planned_bin": wanted["field_row"] // 13,
                "actual_bin": int((actual - 3600) // 900) if actual is not None else None,
                "bin_shift": int((actual - 3600) // 900) - wanted["field_row"] // 13 if actual is not None else None,
                "all_native_field_passages": len(by_vehicle[wanted["vehicle_id"]]),
            }
        )
    write_csv(out / "vehicle-field-passages.csv", passage_rows, list(passage_rows[0]))
    stats = {
        "schema": "astra.hamburg-readme-final-results/v1",
        "native_status": run["status"],
        "valid_run": run.get("healthy", False),
        "exact_count_fit": run.get("exact_count_fit", False),
        "formal_field_metrics": run.get("field_metrics"),
        "diagnostic_field_MAE": float(np.mean(diagnostics)) if diagnostics else None,
        "diagnostic_max_abs_error": max(diagnostics, default=None),
        "field_rows": 130,
        "station_rows": 40,
        "exact_station_rows": sum(row["residual"] == 0 for row in stations),
        "missing_station_rows": sum(row["residual"] is None for row in stations),
        "diagnostic_station_MAE": float(np.mean(station_residuals)) if station_residuals else None,
        "diagnostic_station_max_abs_error": max(
            (abs(row["residual"]) for row in stations if row["residual"] is not None), default=None
        ),
        "missing_field_rows": run.get("missing_field_bins"),
        "exact_field_rows": run.get("exact_field_bins"),
        "completion": run.get("completion"),
        "network_sha256": file_sha256(p / "network.net.xml"),
        "control_sha256": file_sha256(p / "control.add.xml"),
        "measurement": "SUMO native E1 nVehContrib only",
        "scenario": scenario,
        "claim_boundary": "End-to-end workflow evidence. An invalid native run supplies diagnostics only and cannot establish accepted calibration.",
    }
    write_json_atomic(out / "results.json", stats)
    completion = run.get("completion", {})
    formal_display = str(run["field_metrics"]) if run.get("healthy") else "未计算（原生运行无效）"
    verdict = "有效，且全部计数一致" if stats["valid_run"] and stats["exact_count_fit"] else "未通过标定验收"
    text = f"""# 汉堡 README 走廊：完整流程与原生计数结果

**{verdict}。** 原始 OSM → 道路重要性剪枝 → 物理路口汇聚 → 传感器 → 需求与信号 → 原生仿真 → 同时段计数对比均已有输出。
此处的流程完成不等于标定成功。要求仍为 130 个字段时段和 40 个派生站点时段全部零误差。

范围为 Binnenalster/Ring1，LSA 104、118、119、200、535。固定图像范围为 9.9815,53.5525,10.0065,53.5598。
真实观察窗为 2026-10-19 05:30–08:00 UTC，即 07:30–10:00 CEST。
SUMO 1.27.1，步长 0.1 秒，seed 104，仿真 0 对应当地 06:30，结束 14400 秒。停止检查每 1 秒比较全部在途车辆的速度与位置，静止上限 300 秒。

| 原生运行检查 | 数值 |
|---|---:|
| 计划车辆 | {scenario["vehicles"]} |
| 实际进入 | {completion.get("inserted")} |
| 到达 | {completion.get("arrived")} |
| 结束时仍在路网 | {completion.get("running")} |
| 尚未进入 | {completion.get("waiting")} |
| 碰撞 | {completion.get("collisions")} |
| 传送 | {completion.get("teleports")} |
| 字段可用时段 | {130 - run.get("missing_field_bins", 130)}/130 |
| 字段精确时段 | {run.get("exact_field_bins")}/130 |
| 有效运行的正式计数指标 | {formal_display} |

全部差值见 [130 个字段时段](field-comparison.csv) 和 [40 个派生站点时段](station-comparison.csv)。缺失保留为缺失。
站点值由字段相加，不是独立证据。计数只读原生 E1 的 `nVehContrib`。
诊断 MAE 为 {stats["diagnostic_field_MAE"]}，诊断最大绝对差为 {stats["diagnostic_max_abs_error"]}；无效运行的这些数值不能作为标定结果接受。

## 走廊与图像

![原始 OSM 与重要性剪枝](figures/01-osm-to-pruned.png)
![物理路口汇聚前后](figures/02-pruned-to-junctions.png)
![LSA118 固定范围细节](figures/03-lsa118-before-after.png)
![模型中的 13 个原生 E1 字段](figures/04-sensors.png)

主图由 `astra review render` 生成，前后共用固定范围与同一航拍校验值。航拍为 Hamburg LGV 的 2026 年 WMS 资料，许可为 Datenlizenz Deutschland Namensnennung 2.0，仅作空间背景。
道路数据：© OpenStreetMap contributors，ODbL。官方 MAP、OCIT 及计数资料沿用仓库冻结来源包的署名与许可。
虚拟传感器坐标按当前模型车道长度投影，不是现场测绘验证。

![NetEdit before](figures/netedit-before.png)
![NetEdit after](figures/netedit-after.png)

NetEdit 补充图来自真实 NetEdit 1.27.1，使用相同航拍、中心与缩放；after 在 Inspect 模式选中 LSA118。

## 同时段真实计数与 SUMO 计数

![全部字段计数](figures/field-counts.png)
![全部派生站点计数](figures/station-counts.png)

## 已声明的模型

需求只使用一次通过路口的路线，每车对应一个被观测字段；没有背景交通、重复道路、绕圈、掉头需求或客户道路通行。网络中的合法转向及掉头保留。
入口沿用真实边界与道路等级上限，完整整数等式在仿真前成立，但不作为仿真成功的证据。
车道意图按输入 `lane-intentions.json` 中记录的机制执行。这是声明的驾驶意图，不是已验证的自然驾驶行为。当前设置见本报告 `results.json` 的 scenario。
官方路口采用 M2 的 OCIT 固定方案；Z.15 为带标签的 K9 备选。58 个其余 OSM 信号保留原生感应控制，起始时刻与 seed 固定。它们仍是占位控制，没有现场时序验证。
535 采用已有原生扇出曲线候选，普通道路、显式长度和合法通行权限保持原样。原生曲线不等于官方参考折线或航拍测绘路径。
104、535 两个不符合车道箭头的附加转向声明为红灯；相应道路对仍有其他车道承载。
200 进口施工阶段与 MAP 的不一致、Z.15 官方组未知及其他几何与转向对应限制仍保留。

## 失败记录与结果边界

此前试跑因入口积压或碰撞失败，原始输出保留，不能混入有效结果。当前报告仅覆盖所声明的本次原生运行。
本轮不再继续无上限校正。尚未满足的残差与进入压力直接保留，后续再讨论控制、车道意图和需求可实现性。
入口实际延误及未进入数量见 [逐入口表](entry-delays.csv)。未进入车辆没有被当作零延误。
原生瞬时与周期检测器的对应见 [核对结果](passage-reconciliation.json)，逐车字段记录见 [明细](vehicle-field-passages.csv)。
即使同一窗口精确匹配，也只代表声明模型下的标定拟合，不代表独立预测、真实 OD 或现场信号控制已恢复。

## 复现位置

- 执行：[run_hamburg_readme_pipeline.py](../../plugins/astra-sumo/scripts/run_hamburg_readme_pipeline.py)。
- 需求构造：[hamburg_readme_pipeline.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_pipeline.py)。
- 计数与有效性：[detector_demand_replay.py](../../plugins/astra-sumo/src/astra_sumo/core/detector_demand_replay.py)。
- 分析与图像：[hamburg_readme_results.py](../../plugins/astra-sumo/src/astra_sumo/core/hamburg_readme_results.py)。
- 参数与输入：[inputs](inputs/)、原生结果：[native](native/)、各项校验值：[manifest.json](manifest.json)。
"""
    write_text_atomic(out / "README.md", text)
    document = '<!doctype html><meta charset="utf-8"><title>Hamburg Ring1 workflow</title><style>body{font:16px system-ui;max-width:1300px;margin:30px auto;padding:0 25px;line-height:1.6}img{max-width:100%}pre{white-space:pre-wrap;background:#f4f4f4;padding:20px}.bad{color:#b71c1c}</style>'
    document += f'<h1>汉堡 Ring1 完整流程</h1><h2 class="bad">{html.escape(verdict)}</h2><p>所有原生计数和运行有效性均保留。流程输出不等于零误差标定已完成。</p>'
    for label, name in [
        ("原始 OSM → 剪枝", "01-osm-to-pruned.png"),
        ("物理路口汇聚", "02-pruned-to-junctions.png"),
        ("传感器", "04-sensors.png"),
        ("真实计数与 SUMO", "field-counts.png"),
        ("派生站点", "station-counts.png"),
        ("NetEdit before", "netedit-before.png"),
        ("NetEdit after", "netedit-after.png"),
    ]:
        document += f'<h2>{label}</h2><img src="figures/{name}">'
    document += (
        "<h2>原生结果</h2><pre>"
        + html.escape(json.dumps(stats, indent=2, ensure_ascii=False))
        + '</pre><p><a href="README.md">完整报告与明细链接</a></p>'
    )
    write_text_atomic(out / "report.html", document)
    return stats
