"""Check and freeze an explicit list of completed OD probes; never run SUMO."""

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
CORE = ("artifact_io.py", "candidate_contracts.py", "command_runner.py", "detector_demand.py",
        "detector_demand_replay.py", "hamburg_od_spsa.py", "lane_section_hold.py", "simulation_stall.py")
PREPARED = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "control.add.xml",
            "expected-counts.csv", "lane-intentions.json", "initialization-counts.csv", "scenario.json",
            "od-model.json", "initial-od-vector.json", "initial-equivalence.json", "preparation-request.json")
NATIVE = ("network.net.xml", "demand.rou.xml", "source-e1.add.xml", "signal-programs.add.xml",
          "lane-intentions.json", "expected-counts.csv", "e1.add.xml", "instant.add.xml", "field-counts.xml",
          "field-comparison.csv", "summary.xml", "tripinfo.xml", "vehicle-routes.xml", "collisions.xml",
          "run.json", "native-request.json", "command.json", "stall-monitor.json", "sumo.log", "sumo-errors.log")


def raw(path):
    path = Path(path)
    return path.read_bytes() if path.is_file() else gzip.decompress(path.with_name(path.name + ".gz").read_bytes())


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sha_raw(path):
    return hashlib.sha256(raw(path)).hexdigest()


def read_json(path):
    return json.loads(raw(path))


def rows(path):
    return list(csv.DictReader(io.StringIO(raw(path).decode("utf-8-sig"))))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def key(row):
    return row["detector_id"], float(row["begin"]), float(row["end"])


def periodic_counts(path):
    counts = {}
    for row in ET.fromstring(raw(path)).findall("interval"):
        index = row.get("id"), float(row.get("begin")), float(row.get("end"))
        if index[1] == index[2]:
            continue
        assert index not in counts, "duplicate native E1 interval"
        assert index[1] < index[2]
        value = float(row.get("nVehContrib"))
        assert value >= 0 and value.is_integer()
        counts[index] = int(value)
    return counts


def check_comparisons(comparison, expected, counts):
    targets = {key(r): r for r in expected}
    actual = {key(r): r for r in comparison}
    assert len(targets) == len(expected) and len(actual) == len(comparison) and actual.keys() == targets.keys()
    residuals = []
    for index, target in targets.items():
        assert index in counts, "missing native E1 observation"
        row = actual[index]
        expected_count = int(target["expected_total"])
        difference = counts[index] - expected_count
        assert int(row["expected_total"]) == expected_count
        assert int(row["measured_nVehContrib"]) == counts[index]
        assert int(row.get("residual", row.get("diff_nVehContrib_minus_expected"))) == difference, "reported residual differs"
        residuals.append(difference)
    return {"SSE": sum(x*x for x in residuals), "TAE": sum(map(abs, residuals)),
            "MAE": sum(map(abs, residuals)) / len(residuals), "max_abs_error": max(map(abs, residuals))}


def audit_initial(prepared):
    report = read_json(prepared / "initial-equivalence.json")
    assert report["status"] == "pass" and all(report["checks"].values())
    assert not report["new_native_run"] and not report["cache_reuse_authorized"]
    for name, expected in report["source_hashes"].items():
        assert sha_raw(prepared / name) == expected
    assert sha_raw(prepared / "od-model.json") == report["model_sha256"]
    for name, expected in report["generated_hashes"].items():
        assert sha_raw(prepared / "initial" / name) == expected
    model = read_json(prepared / "od-model.json")
    initial = read_json(prepared / "initial-od-vector.json")
    assert len(model["cells"]) == len(initial["values"]) == 294
    assert initial["values"] == [c["initial_flow"] for c in model["cells"]]
    source = ET.fromstring(raw(prepared / "demand.rou.xml"))
    generated = ET.fromstring(raw(prepared / "initial/demand.rou.xml"))
    def canonical(root):
        return ET.canonicalize(ET.tostring(root, encoding="unicode"), strip_text=True)

    assert canonical(source) == canonical(generated)
    vehicles = {v.get("id"): v for v in source.findall("vehicle")}
    fields = {r["vehicle_id"]: r["field_id"] for r in read_json(prepared / "lane-intentions.json")["vehicles"]}
    seen = set()
    for cell in model["cells"]:
        assert cell["initial_flow"] == len(cell["templates"])
        for template in cell["templates"]:
            identity = template["prior_vehicle_id"]
            assert identity not in seen and canonical(ET.fromstring(template["vehicle_xml"])) == canonical(vehicles[identity])
            assert template["prior_field_intention"] == fields[identity]
            seen.add(identity)
    assert seen == vehicles.keys() and len(vehicles) == sum(initial["values"]) == 8640


def audit_run(prepared, evaluation, native, verdict, execution):
    from astra_sumo.core.hamburg_readme_results import STATIONS

    main, init = rows(prepared / "expected-counts.csv"), rows(prepared / "initialization-counts.csv")
    counts = periodic_counts(native / "field-counts.xml")
    assert len(main) == 130 and len(init) == 52 and len(counts) == 208
    main_score = check_comparisons(rows(native / "field-comparison.csv"), main, counts)
    init_score = check_comparisons(rows(evaluation / "initialization52-comparison.csv"), init, counts)
    full_score = check_comparisons(rows(evaluation / "all182-comparison.csv"), main + init, counts)
    assert main_score["TAE"] + init_score["TAE"] == full_score["TAE"]
    groups = defaultdict(lambda: [0, 0, 0, 0])
    for row in main:
        for station, (node, assets) in STATIONS.items():
            if row["node_id"] == node and row["asset_id"] in assets:
                item = groups[station, float(row["begin"]), float(row["end"])]
                item[0] += int(row["expected_total"])
                item[1] += counts[key(row)]
                item[2] += counts[key(row)] - int(row["expected_total"])
                item[3] += 1
    stations = rows(evaluation / "station-comparison.csv")
    assert len(stations) == len(groups) == 40
    assert len({(r["station_stream_id"], float(r["begin"]), float(r["end"])) for r in stations}) == 40
    for row in stations:
        index = row["station_stream_id"], float(row["begin"]), float(row["end"])
        assert groups[index][:3] == [int(row[k]) for k in ("expected_total", "measured_nVehContrib", "residual")]
        assert groups[index][3] == len(STATIONS[index[0]][1]) and row["independent_evidence"] == "False"
    run, monitor = read_json(native / "run.json"), read_json(native / "stall-monitor.json")
    assert run["native_status"] == "pass" and run["observer_complete"] and run["missing_field_bins"] == 0
    assert monitor["status"] == "completed" and monitor["simulation_time_s"] == 14400
    assert read_json(native / "command.json")["status"] == "pass"
    assert (run["seed"], run["step_length_s"], run["end_s"], run["backend"]) == (104, .1, 14400, "libsumo")
    assert run["junction_body_hold_nodes"] == read_json(prepared / "scenario.json")["junction_body_hold_nodes"]
    aliases = {"network.net.xml": prepared / "network.net.xml", "demand.rou.xml": evaluation / "demand.rou.xml",
               "field-loops.add.xml": prepared / "field-loops.add.xml", "control.add.xml": prepared / "control.add.xml",
               "expected-counts.csv": prepared / "expected-counts.csv", "lane-intentions.json": evaluation / "lane-intentions.json"}
    assert len(run["inputs"]) == len(run["snapshots"]) == 6
    for original, snapshot in zip(run["inputs"], run["snapshots"]):
        assert sha_raw(aliases[Path(original["path"]).name]) == original["sha256"] == snapshot["sha256"]
        assert sha_raw(native / Path(snapshot["path"]).name) == snapshot["sha256"]
    vector = read_json(evaluation / "od-vector.json")
    assert vector["values"] == execution["values"] and len(vector["values"]) == 294
    x0 = read_json(prepared / "initial-od-vector.json")["values"]
    arguments = execution["arguments"]
    x0[arguments["donor"]] -= arguments["delta"]
    x0[arguments["receiver"]] += arguments["delta"]
    assert vector["values"] == x0 and min(x0) >= 0
    assert vector["integer_counts"] == [math.floor(v + .5) for v in vector["values"]]
    assert vector["source_model_sha256"] == sha_raw(prepared / "od-model.json")
    demand = ET.fromstring(raw(evaluation / "demand.rou.xml")).findall("vehicle")
    demand_ids = {v.get("id") for v in demand}
    assert len(demand) == len(demand_ids) == vector["vehicle_count"] == sum(vector["integer_counts"]) == 8640
    actual_cells = Counter(int(v.get("id").split("_")[1]) for v in demand)
    assert [actual_cells[i] for i in range(294)] == vector["integer_counts"]
    final = ET.fromstring(raw(native / "summary.xml"))[-1]
    completion = {k: int(final.get(k)) for k in ("loaded", "inserted", "arrived", "running", "waiting", "collisions", "teleports", "discarded")}
    assert completion == run["completion"] == verdict["completion"]
    trips = ET.fromstring(raw(native / "tripinfo.xml")).findall("tripinfo")
    assert len(trips) == len({t.get("id") for t in trips}) == completion["inserted"]
    assert {t.get("id") for t in trips} <= demand_ids
    arrived = {t.get("id") for t in trips if float(t.get("arrival")) >= 0}
    assert len(arrived) == completion["arrived"] and completion["loaded"] == len(demand)
    assert sum(float(t.get("arrival")) < 0 and t.get("vaporized", "") in {"", "end"} for t in trips) == completion["running"]
    assert len(ET.fromstring(raw(native / "collisions.xml")).findall("collision")) == completion["collisions"]
    routes = ET.fromstring(raw(native / "vehicle-routes.xml")).findall("vehicle")
    assert len(routes) == len({v.get("id") for v in routes}) == len(arrived)
    assert {v.get("id") for v in routes} == arrived
    healthy = (completion["loaded"] == completion["inserted"] == completion["arrived"] and
               not any(completion[k] for k in ("running", "waiting", "collisions", "teleports", "discarded")) and
               all(not t.get("vaporized", "") for t in trips))
    result = read_json(evaluation / "evaluation.json")
    assert result["loss"] == main_score["SSE"] and math.isclose(result["MAE"], main_score["MAE"], abs_tol=1e-12)
    assert result["max_abs_error"] == main_score["max_abs_error"] and result["completion"] == completion
    assert math.isclose(result["station_MAE"], sum(abs(int(r["residual"])) for r in stations) / 40, abs_tol=1e-12)
    assert result["station_max_abs_error"] == max(abs(int(r["residual"])) for r in stations)
    assert healthy == result["healthy"] == run["healthy"] == verdict["healthy"]
    assert verdict["safe"] == (not any(completion[k] for k in ("collisions", "teleports", "discarded")))
    assert verdict["source_inputs_unchanged"] and verdict["preview_and_measure_bytes_identical"]
    assert result["exact_count_fit"] == run["exact_count_fit"] == (healthy and main_score["SSE"] == 0)
    assert all(verdict["measurement"][name] == value for name, value in result.items())
    assert verdict["measurement"]["TAE"] == main_score["TAE"]
    assert verdict["measurement"]["all182_TAE"] == full_score["TAE"]
    baseline = periodic_counts(prepared.parent / "baseline-field-counts.xml")
    residuals = [baseline[key(r)] - int(r["expected_total"]) for r in main]
    assert verdict["baseline"] == {"SSE": sum(x*x for x in residuals), "TAE": sum(map(abs, residuals)),
                                    "MAE": sum(map(abs, residuals)) / 130}
    assert execution["completed"] and execution["new_native_run"] and execution["backend"] == "libsumo"
    assert set(execution["versions"].values()) == {"1.27.1"}
    return {"main130": main_score, "initial52": init_score, "all182": full_score, "completion": completion,
            "healthy": healthy, "exact_count_fit": healthy and main_score["SSE"] == 0}


def verify_frozen(case):
    manifest = read_json(case / "manifest.json")
    actual = {p.relative_to(case).as_posix() for p in case.rglob("*")
              if p.is_file() and p.name != "manifest.json" and "__pycache__" not in p.parts}
    assert actual == {r["path"] for r in manifest["files"]}
    for row in manifest["files"]:
        path = case / row["path"]
        assert path.stat().st_size == row["bytes"] and digest(path) == row["sha256"]
    for row in read_json(case / "source-provenance.json")["snapshots"]:
        value = gzip.decompress((case / row["snapshot"]).read_bytes())
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    audit_initial(case / "prepared")
    for name, result in read_json(case / "results.json")["evaluations"].items():
        target = case / name
        checked = audit_run(target / "prepared", target / "evaluation", target / "native",
                            read_json(target / "verdict.json"), read_json(target / "execution.json"))
        assert checked == result["independent"]
        execution = read_json(target / "execution.json")
        for file in PREPARED:
            assert sha_raw(target / "prepared" / file) == sha_raw(case / "prepared" / file)
        for file in ("demand.rou.xml", "lane-intentions.json", "od-vector.json"):
            assert raw(target / "preview" / file) == raw(target / "evaluation" / file)
        recorded = {k.replace("\\", "/"): v for k, v in execution["implementation_sha256"].items()}
        for file in CORE:
            assert sha_raw(target / "code-source" / file) == recorded["astra_sumo/core/" + file]
    print("Frozen manifest, raw snapshots, 130/40/182 counts and completion: pass", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--run-list", type=Path, help="JSON array of 2 or 4 completed probe directory names.")
    parser.add_argument("--output-dir", type=Path, default=REPO / "benchmarks/hamburg_ring1_classical_od_response_20261023_v1")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--verify-only", type=Path, help="Recheck an existing frozen package without writing.")
    args = parser.parse_args()
    sys.path.insert(0, str(REPO / "plugins/astra-sumo/src"))
    if args.verify_only:
        verify_frozen(args.verify_only.resolve(strict=True))
        return
    assert args.run_list is not None, "Pass the final explicit --run-list; no directory discovery is used."
    root = args.root.resolve(strict=True)
    names = read_json(args.run_list)
    assert isinstance(names, list) and len(names) in (2, 4) and len(set(names)) == len(names)
    assert all(isinstance(n, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", n) for n in names)
    audit_initial(root / "prepared")
    assignment = read_json(root / "assignment-audit/manifest.json")
    for row in assignment["files"]:
        assert digest(root / "assignment-audit" / row["path"]) == row["sha256"]
    results = {}
    for name in names:
        source = root / name
        verdict, execution = read_json(source / "verdict.json"), read_json(source / "execution.json")
        results[name] = {"original_verdict": verdict, "independent": audit_run(source / "source/prepared", source / "evaluation",
                        source / "evaluation/native", verdict, execution)}
        for path, expected in execution["source_hashes"].items():
            assert digest(path) == expected
        for file in PREPARED:
            assert digest(source / "source/prepared" / file) == digest(root / "prepared" / file)
        for file in ("demand.rou.xml", "lane-intentions.json", "od-vector.json"):
            assert (source / "preview" / file).read_bytes() == (source / "evaluation" / file).read_bytes()
        assert digest(source / "measure_probe.snapshot.py") == execution["script_sha256"]
        for file, expected in execution["implementation_sha256"].items():
            assert digest(source / "implementation" / file) == expected
        assert digest(REPO / ".venv/Lib/site-packages/sumo/bin/sumo.exe") == execution["sumo_sha256"]
        assert digest(REPO / ".venv/Lib/site-packages/libsumo/_libsumo.pyd") == execution["libsumo_sha256"]
    if args.check_only:
        print(json.dumps(results, indent=2), flush=True)
        return
    selection = read_json(root / "response-audit.json")
    selected = selection["selected_evaluation"]
    assert selected in names and results[selected]["original_verdict"]["measurement"] == selection["selected_measurement"]
    for relative, expected in selection["input_sha256"].items():
        assert digest(REPO / relative) == expected
    case = args.output_dir.resolve()
    assert not case.exists() and not root.is_relative_to(case), "Use a new, separate benchmark directory."
    case.mkdir(parents=True)
    provenance, local = [], []

    def compress(source, relative):
        target = case / (str(relative).replace("\\", "/") + ".gz")
        target.parent.mkdir(parents=True, exist_ok=True)
        value = source.read_bytes()
        target.write_bytes(gzip.compress(value, mtime=0))
        provenance.append({"source": str(source), "snapshot": target.relative_to(case).as_posix(),
                           "raw_bytes": len(value), "raw_sha256": hashlib.sha256(value).hexdigest()})

    for relative in (*PREPARED, "initial/demand.rou.xml", "initial/lane-intentions.json", "initial/od-vector.json"):
        compress(root / "prepared" / relative, Path("prepared") / relative)
    for path in sorted((root / "assignment-audit").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            compress(path, Path("assignment-audit") / path.relative_to(root / "assignment-audit"))
    for file in ("response-protocol.md", "measure_probe.py", "offline-selfchecks.json"):
        compress(root / file, file)
    for file in ("smaller-amplitude-decision.json", "response-audit.json", "reproduce-selfchecks.json"):
        if (root / file).is_file():
            compress(root / file, file)
    for name in names:
        source = root / name
        for file in PREPARED:
            compress(source / "source/prepared" / file, Path(name) / "prepared" / file)
        for file in ("baseline-field-counts.xml", "baseline-evaluation.json"):
            compress(source / "source" / file, Path(name) / file)
        for file in NATIVE:
            compress(source / "evaluation/native" / file, Path(name) / "native" / file)
        for file in ("demand.rou.xml", "lane-intentions.json", "od-vector.json", "evaluation.json",
                     "station-comparison.csv", "initialization52-comparison.csv", "all182-comparison.csv"):
            compress(source / "evaluation" / file, Path(name) / "evaluation" / file)
        for path in sorted((source / "preview").iterdir()):
            if path.is_file():
                compress(path, Path(name) / "preview" / path.name)
        for file in ("execution.json", "verdict.json", "progress.json", "measure_probe.snapshot.py", "failure.json"):
            if (source / file).is_file():
                compress(source / file, Path(name) / file)
        execution = read_json(source / "execution.json")
        for file in CORE:
            compress(source / "implementation/astra_sumo/core" / file, Path(name) / "code-source" / file)
        for suffix in ("-launch.json", ".stdout.log", ".stderr.log"):
            compress(root / (name + suffix), Path(name) / (name + suffix))
        for file in ("instant-e1.xml", "queues.xml"):
            path = source / "evaluation/native" / file
            local.append({"run": name, "path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)})
    (case / ".gitattributes").write_text("* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n", encoding="utf-8")
    for file in ("freeze_evidence.py", "test_freeze_evidence.py", "reproduce.py"):
        (case / file).write_bytes((root / file).read_bytes())
    (case / "run-list.json").write_bytes(args.run_list.read_bytes())
    write_json(case / "results.json", {"formal_result": False, "complete_runs": names, "evaluations": results,
        "selected_evaluation": selected, "selected_measurement": selection["selected_measurement"],
        "goal": "All 130 native nVehContrib residuals zero, with valid completion and safety.",
        "claim_boundary": "Finite OD direction probes under the current empirical quantile generator and original actuated/static control. Assignment fractions are descriptive, not Jacobians. No unique true OD or finite convergence claim."})
    write_json(case / "source-provenance.json", {"snapshots": provenance})
    write_json(case / "local-only-large-traces.json", {"files": local})
    table = ["| 原生测量 | 130项SSE | 130项MAE | 182项TAE | 未入网 | 在途 | 碰撞 | 传送 | 原始判定 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    links = []
    for name, value in results.items():
        item = value["independent"]
        completion = item["completion"]
        table.append(f"| [{name}]({name}/verdict.json.gz) | {item['main130']['SSE']} | {item['main130']['MAE']:.6f} | {item['all182']['TAE']} | {completion['waiting']} | {completion['running']} | {completion['collisions']} | {completion['teleports']} | {value['original_verdict']['status']} |")
        links.append(f"- {name}：[130项字段]({name}/native/field-comparison.csv.gz)、[40项站点]({name}/evaluation/station-comparison.csv.gz)、[52项初始化]({name}/evaluation/initialization52-comparison.csv.gz)、[全部182项]({name}/evaluation/all182-comparison.csv.gz)。")
    text = """# 汉堡经典OD方向响应核验

本包保存四次相邻出发时段OD方向测量，**没有执行SPSA，130项原生零残差目标仍未完成**。
基线主130项SSE为10749、MAE为5.961538、TAE为775，全部182项TAE为857。
基线仍有68辆未入网、50辆在途，不能作为正式完成标定。
本轮最优已测端点为 `probe-minus1`：主130项SSE为6898、MAE为5.092308、TAE为662、最大误差27，全部182项TAE为744。
该端点仍有7辆未入网、14辆在途，原始判定为 `diagnostic_candidate`，不是正式标定结果。
方法依据与适用边界见[经典文献复核](../../docs/research/hamburg-classic-calibration-2026-10-23.md)和[本轮响应协议](response-protocol.md.gz)。

保留13个字段乘10个主时段的全部130项真实计数，并与原生E1 `nVehContrib`比较；另保留52项真实初始化计数。
40项站点值来自字段求和，不增加独立观测。
每项运行均保留8640辆请求、原道路、40个感应和11个静态控制、种子104、0.1秒步长、14400秒时长及Libsumo 1.27.1。
两项OD数量变化继续使用原有整数化和联合分位数生成，会改变目标群体内的时刻及路线、车道份额。
`q=-1` 实际改变151个请求时刻和56个字段意图及对应停靠模板。两项OD区间的字段库存由Z4/Z5的77/76变为78/75，不能表述为仅移动一辆车的时刻。
后续搜索以同一个prepared模型和该已测向量为中心，不重建分位数模板。选择依据和来源哈希见[选择记录](response-audit.json.gz)。
这些份额是声明的模型先验，不是观测到的真实OD。测量也未分离排队与感应控制的因果贡献。

""" + "\n".join(table) + "\n\n" + "\n".join(links) + """

所有原始verdict按原字节保留。全130项SSE下降也不能替代MAE零误差或运行有效性检查。
`assignment-audit/`包含分配矩阵、限制及独立新结果，分配比例不等于Jacobian。
`prepared/`保存当前模型、x0和初值一致性证据。每项保存源向量、实际需求、完整原生计数、Summary、TripInfo、车辆路线、碰撞日志、运行和驾驶实现来源。
大体积instant E1和queues仅保留本地路径与SHA-256，见[本地大文件清单](local-only-large-traces.json)。
完整文件及压缩前字节哈希分别见[清单](manifest.json)和[来源](source-provenance.json)。

离线复核和拒绝性测试不启动SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_classical_od_response_20261023_v1/freeze_evidence.py --verify-only benchmarks/hamburg_ring1_classical_od_response_20261023_v1
.venv/Scripts/python.exe -m pytest benchmarks/hamburg_ring1_classical_od_response_20261023_v1/test_freeze_evidence.py -q
```

`reproduce.py`会明确启动一次完整原生测量，使用前先查看其参数。冻结过程只做离线核对。
OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
"""
    (case / "README.md").write_text(text, encoding="utf-8")
    write_json(case / "manifest.json", {"formal_result": False, "files": [
        {"path": p.relative_to(case).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
        for p in sorted(case.rglob("*")) if p.is_file() and p.name != "manifest.json"]})
    verify_frozen(case)


if __name__ == "__main__":
    main()
