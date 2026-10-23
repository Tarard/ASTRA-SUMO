"""Freeze the completed upstream diagnosis and explicit lane probes; never run SUMO."""

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
CLASSIC = REPO / "benchmarks/hamburg_ring1_classical_od_response_20261023_v1"
spec = importlib.util.spec_from_file_location("classic_evidence_helpers", CLASSIC / "freeze_evidence.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
raw, read_json, rows = helper.raw, helper.read_json, helper.rows
digest, sha_raw = helper.digest, helper.sha_raw
periodic_counts, check_comparisons = helper.periodic_counts, helper.check_comparisons
RUNS = ("lane-swap-1pair", "lane-swap-5pair")
ANALYSES = ("selected-next-diagnosis", "queue-audit", "control-audit", "driver-audit", "trace-analysis", "lane-prior-probe-plan", "lane-prior-response-audit")
CORE = ("detector_demand_replay.py", "simulation_stall.py", "lane_section_hold.py", "hamburg_od_spsa.py")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_scope(before, after, changed):
    old = ET.fromstring(raw(before / "demand.rou.xml"))
    new = ET.fromstring(raw(after / "demand.rou.xml"))
    originals = {v.get("id"): v for v in old.findall("vehicle")}
    actual = {v.get("id"): v for v in new.findall("vehicle")}
    oi, ni = (read_json(p / "lane-intentions.json") for p in (before, after))
    of, nf = ({r["vehicle_id"]: r for r in x["vehicles"]} for x in (oi, ni))
    assert list(originals) == list(actual) and originals.keys() == of.keys() == nf.keys()
    assert len(set(changed)) == len(changed) and set(changed) <= originals.keys()
    assert {k: v for k, v in oi.items() if k != "vehicles"} == {k: v for k, v in ni.items() if k != "vehicles"}
    inventory = []
    for vehicles, intentions in ((originals, of), (actual, nf)):
        inventory.append(Counter((math.floor(float(v.get("depart"))*1000+.5)//900000, intentions[i]["field_id"])
                                 for i, v in vehicles.items()))
    assert inventory[0] == inventory[1]
    for identity, vehicle in actual.items():
        original = originals[identity]
        assert vehicle.attrib == original.attrib
        assert {k: v for k, v in of[identity].items() if k != "field_id"} == {
            k: v for k, v in nf[identity].items() if k != "field_id"}
        if identity in changed:
            assert of[identity]["field_id"] != nf[identity]["field_id"]
            a = [s for s in original.findall("stop") if s.get("lane", "").startswith("24483344#1_")]
            b = [s for s in vehicle.findall("stop") if s.get("lane", "").startswith("24483344#1_")]
            assert len(a) == len(b) == 1 and a[0].get("lane") != b[0].get("lane")
            field_lanes = {"hh_0119_Z_4_29437": "24483344#1_0", "hh_0119_Z_5_30494": "24483344#1_1"}
            assert a[0].get("lane") == field_lanes[of[identity]["field_id"]]
            assert b[0].get("lane") == field_lanes[nf[identity]["field_id"]]
            b[0].set("lane", a[0].get("lane"))
        else:
            assert of[identity] == nf[identity]
        assert ET.canonicalize(ET.tostring(vehicle, encoding="unicode"), strip_text=True) == ET.canonicalize(
            ET.tostring(original, encoding="unicode"), strip_text=True)


def completion(native, demand):
    final = ET.fromstring(raw(native / "summary.xml"))[-1]
    result = {k: int(final.get(k)) for k in ("loaded", "inserted", "arrived", "waiting", "running", "collisions", "teleports", "discarded")}
    planned = {v.get("id") for v in ET.fromstring(raw(demand)).findall("vehicle")}
    trips = ET.fromstring(raw(native / "tripinfo.xml")).findall("tripinfo")
    assert len(planned) == result["loaded"] == 8640
    assert len(trips) == len({t.get("id") for t in trips}) == result["inserted"]
    assert {t.get("id") for t in trips} <= planned
    arrived = {t.get("id") for t in trips if float(t.get("arrival")) >= 0}
    assert len(arrived) == result["arrived"]
    assert sum(float(t.get("arrival")) < 0 and t.get("vaporized", "") in {"", "end"} for t in trips) == result["running"]
    assert len(ET.fromstring(raw(native / "collisions.xml")).findall("collision")) == result["collisions"]
    vehicles = ET.fromstring(raw(native / "vehicle-routes.xml")).findall("vehicle")
    assert len(vehicles) == len({v.get("id") for v in vehicles}) == len(arrived)
    assert {v.get("id") for v in vehicles} == arrived
    return result


def audit_run(case, baseline):
    from astra_sumo.core.hamburg_readme_results import STATIONS

    prepared, evaluation, native = case / "prepared", case / "evaluation", case / "native"
    # Local runs retain native below evaluation; frozen runs use the preceding package layout.
    if (evaluation / "native").is_dir():
        native = evaluation / "native"
    preflight, verdict = read_json(case / "preflight.json"), read_json(case / "verdict.json")
    changed = preflight["changed_vehicle_ids"]
    check_scope(baseline / "evaluation", prepared, changed)
    assert len(changed) == len(preflight["pairs"])*2
    for name, expected in preflight["input_sha256"].items():
        assert sha_raw(prepared / name) == expected
    for name in ("network.net.xml", "control.add.xml", "field-loops.add.xml", "expected-counts.csv", "initialization-counts.csv"):
        assert sha_raw(prepared / name) == sha_raw(baseline / "prepared" / name)
    for name in ("demand.rou.xml", "lane-intentions.json"):
        assert raw(prepared / name) == raw(evaluation / name)
    original_vector = read_json(baseline / "evaluation/od-vector.json")
    assert original_vector["source_model_sha256"] == sha_raw(baseline / "prepared/od-model.json")
    vector = read_json(evaluation / "od-vector.json")
    for name in ("values", "integer_counts", "vehicle_count"):
        assert vector[name] == original_vector[name]
    assert vector["source_vector_sha256"] == sha_raw(baseline / "evaluation/od-vector.json")
    assert vector["prior_od_model_sha256"] == original_vector["source_model_sha256"]
    assert vector["paired_vehicle_ids"] == changed and "source_model_sha256" not in vector
    run, monitor = read_json(native / "run.json"), read_json(native / "stall-monitor.json")
    assert run["native_status"] == "pass" and run["observer_complete"] and run["missing_field_bins"] == 0
    assert monitor["status"] == "completed" and monitor["simulation_time_s"] == 14400
    assert (run["seed"], run["step_length_s"], run["end_s"], run["backend"]) == (104, .1, 14400, "libsumo")
    assert run["junction_body_hold_nodes"] == read_json(prepared / "scenario.json")["junction_body_hold_nodes"]
    assert len(run["inputs"]) == len(run["snapshots"]) == 6
    for source, snapshot in zip(run["inputs"], run["snapshots"]):
        assert sha_raw(prepared / Path(source["path"]).name) == source["sha256"] == snapshot["sha256"]
        assert sha_raw(native / Path(snapshot["path"]).name) == snapshot["sha256"]
    counts = periodic_counts(native / "field-counts.xml")
    before_counts = periodic_counts(baseline / "native/field-counts.xml")
    main, init = rows(prepared / "expected-counts.csv"), rows(prepared / "initialization-counts.csv")
    assert len(counts) == 208 and len(main) == 130 and len(init) == 52
    score = check_comparisons(rows(native / "field-comparison.csv"), main, counts)
    full_score = check_comparisons(rows(evaluation / "all182-comparison.csv"), main + init, counts)
    stations = rows(evaluation / "station-comparison.csv")
    groups = defaultdict(lambda: [0, 0, 0, 0])
    for row in main:
        key = helper.key(row)
        for station, (node, assets) in STATIONS.items():
            if row["node_id"] == node and row["asset_id"] in assets:
                g = groups[station, key[1], key[2]]
                g[0] += int(row["expected_total"])
                g[1] += counts[key]
                g[2] += counts[key] - int(row["expected_total"])
                g[3] += 1
    assert len(stations) == len(groups) == 40
    assert len({(r["station_stream_id"], float(r["begin"]), float(r["end"])) for r in stations}) == 40
    for row in stations:
        key = row["station_stream_id"], float(row["begin"]), float(row["end"])
        assert groups[key][:3] == [int(row[n]) for n in ("expected_total", "measured_nVehContrib", "residual")]
        assert groups[key][3] == len(STATIONS[key[0]][1]) and row["independent_evidence"] == "False"
    measured = read_json(evaluation / "evaluation.json")
    assert measured == verdict["after"]
    assert measured["loss"] == score["SSE"] and math.isclose(measured["MAE"], score["MAE"], abs_tol=1e-12)
    assert measured["max_abs_error"] == score["max_abs_error"]
    assert math.isclose(measured["station_MAE"], sum(abs(int(r["residual"])) for r in stations) / 40, abs_tol=1e-12)
    assert measured["station_max_abs_error"] == max(abs(int(r["residual"])) for r in stations)
    completed = completion(native, prepared / "demand.rou.xml")
    assert completed == run["completion"] == measured["completion"]
    healthy = completed["loaded"] == completed["inserted"] == completed["arrived"] and not any(
        completed[k] for k in ("waiting", "running", "collisions", "teleports", "discarded"))
    assert healthy == measured["healthy"] == run["healthy"]
    compact = json.loads(gzip.decompress(raw(evaluation / "contributing-passages.json.gz")))
    for source in compact["inputs"]:
        name = Path(source["path"]).name
        if name == "field-counts.xml" or (native / name).is_file():
            assert sha_raw(native / name) == source["sha256"]
    assert compact["reconciled"] and Counter((r["detector_id"], float(r["interval_begin_s"]), float(r["interval_end_s"]))
        for r in compact["passages"]) == Counter(counts)
    assigned = {r["vehicle_id"]: r["field_id"] for r in read_json(prepared / "lane-intentions.json")["vehicles"]}
    repeat = [list(k) for k, n in Counter((r["vehicle_id"], r["detector_id"]) for r in compact["passages"]).items() if n > 1]
    wrong = [r for r in compact["passages"] if r["detector_id"] != assigned[r["vehicle_id"]]]
    assert repeat == verdict["repeated_vehicle_field"] and wrong == verdict["wrong_field_contributions"]
    vehicles = {v.get("id"): v for v in ET.fromstring(raw(prepared / "demand.rou.xml")).findall("vehicle")}
    earliest = min(float(vehicles[i].get("depart")) for i in changed)
    prefix = [helper.key(r) for r in main + init if float(r["end"]) <= earliest]
    assert prefix and all(counts[k] == before_counts[k] for k in prefix)
    assert len(prefix) == verdict["unchanged_prefix_rows"]
    assert verdict["all182_TAE"] == full_score["TAE"]
    before = read_json(baseline / "evaluation/evaluation.json")
    assert before == verdict["before"]
    safe = not any(completed[k] for k in ("collisions", "teleports", "discarded"))
    accepted = safe and not repeat and not wrong and score["MAE"] < before["MAE"] and score["SSE"] < before["loss"] and completed["waiting"] + completed["running"] <= 21 and completed["waiting"] <= 7
    assert verdict["status"] == ("diagnostic_candidate" if accepted else "rejected")
    assert not verdict["formal_result"] and verdict["source_inputs_unchanged"]
    return {"main130": score, "all182": full_score, "completion": completed, "healthy": healthy,
            "unchanged_prefix_rows": len(prefix), "wrong_field": len(wrong), "repeat_vehicle_field": len(repeat),
            "status": verdict["status"], "changed_vehicles": len(changed)}


def audit_trace(trace, baseline):
    result = read_json(trace / "record-result.json")
    assert result["status"] == "pass" and len(result["checks"]) == 12 and all(result["checks"].values())
    assert periodic_counts(trace / "field-counts.xml") == periodic_counts(baseline / "native/field-counts.xml")
    assert len(periodic_counts(trace / "field-counts.xml")) == 208
    assert sha_raw(trace / "tls-switch-states.xml") == result["state_log"]["sha256"]
    assert sha_raw(trace / "tls-recorded-programs.add.xml") == result["program_export"]["sha256"]
    preflight = read_json(trace / "preflight.json")
    assert sha_raw(trace / "record_tls.snapshot.py") == preflight["script_sha256"]
    for name in ("network.net.xml", "demand.rou.xml", "signal-programs.add.xml", "lane-intentions.json", "source-e1.add.xml", "expected-counts.csv"):
        assert sha_raw(trace / name) == preflight["source_hashes"][name]
    for name, expected in preflight["source_hashes"].items():
        assert sha_raw(baseline / "native" / name) == expected
    complete = completion(trace, trace / "demand.rou.xml")
    assert complete == {k: int(result["completion"][k]) for k in complete}
    ids = read_json(trace / "observed-vehicle-ids.json")["vehicles"]
    assert len(set(ids)) == len(ids) == 115
    fcd = trace / "cohort-fcd.xml"
    fcd = fcd if fcd.exists() else fcd.with_name(fcd.name + ".gz")
    seen = set()
    with (gzip.open(fcd, "rb") if fcd.suffix == ".gz" else fcd.open("rb")) as stream:
        for _, row in ET.iterparse(stream, events=("end",)):
            if row.tag == "vehicle":
                seen.add(row.get("id"))
            row.clear()
    assert seen == set(ids)
    return {"status": "pass", "parity_checks": 12, "native_E1_rows": 208, "FCD_vehicles": 115, "completion": complete}


def verify(case):
    manifest = read_json(case / "manifest.json")
    actual = {p.relative_to(case).as_posix() for p in case.rglob("*")
              if p.is_file() and p != case / "manifest.json" and "__pycache__" not in p.parts}
    assert actual == {r["path"] for r in manifest["files"]}
    for row in manifest["files"]:
        assert digest(case / row["path"]) == row["sha256"] and (case / row["path"]).stat().st_size == row["bytes"]
    for row in read_json(case / "source-provenance.json")["snapshots"]:
        value = gzip.decompress((case / row["snapshot"]).read_bytes())
        assert len(value) == row["raw_bytes"] and hashlib.sha256(value).hexdigest() == row["raw_sha256"]
    results = read_json(case / "results.json")
    assert digest(CLASSIC / "freeze_evidence.py") == results["offline_helper_sha256"]
    assert audit_trace(case / "trace/baseline", case / "baseline") == results["trace"]
    trace_preflight = read_json(case / "trace/baseline/preflight.json")
    assert sha_raw(case / "trace/baseline/tls-observer.add.xml") == trace_preflight["observer_sha256"]
    observed = read_json(case / "trace/baseline/observed-vehicle-ids.json")
    assert observed["sha256"] == sha_raw(case / "selected-next-diagnosis/z5-tail115-vehicles.csv")
    for file, expected in trace_preflight["driver_hashes"].items():
        assert sha_raw(case / "code-source" / file) == expected
    local = read_json(case / "local-only-large-traces.json")["files"]
    assessment = read_json(case / "assessment.json")
    for name in RUNS:
        assert audit_run(case / name, case / "baseline") == results["evaluations"][name]
        original = read_json(case / name / "verdict.json")
        assert assessment["measurements"][name] == {"original_status": original["status"], "after": original["after"], "all182_TAE": original["all182_TAE"]}
        preflight = read_json(case / name / "preflight.json")
        assert sha_raw(case / name / "executed-driver.py") == preflight["driver_sha256"]
        for file, expected in preflight["implementation_sha256"].items():
            assert sha_raw(case / "code-source" / file) == expected
        evaluation_files = {"demand.rou.xml", "lane-intentions.json", "od-vector.json", "evaluation.json"}
        for source, expected in preflight["source_hashes"].items():
            file = Path(source).name
            snapshot = (case / "lane-prior-probe-plan" / file if file == "five-pairs.json" else
                        case / "baseline" / ("evaluation" if file in evaluation_files else "prepared") / file)
            assert sha_raw(snapshot) == expected
        compact = json.loads(gzip.decompress(raw(case / name / "evaluation/contributing-passages.json.gz")))
        instant = next(r for r in local if r["run"] == name and Path(r["path"]).name == "instant-e1.xml")
        assert next(r["sha256"] for r in compact["inputs"] if Path(r["path"]).name == "instant-e1.xml") == instant["sha256"]
    print("Manifest, source bytes, 115 FCD vehicles, native 208/130/40/182, lane scope and verdicts: pass", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--verify-only", type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO / "benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1")
    args = parser.parse_args()
    sys.path.insert(0, str(REPO / "plugins/astra-sumo/src"))
    if args.verify_only:
        verify(args.verify_only.resolve(strict=True))
        return
    baseline_source = REPO / "outputs/hamburg-classical-od-calibration-20261023-v1/probe-minus1/evaluation"
    # Reuse the already frozen baseline layout for source input and metrics checks.
    baseline = CLASSIC / "probe-minus1"
    analyses = {n: ROOT / n for n in ANALYSES}
    analyses["selected-next-diagnosis"] = baseline_source.parents[1] / "selected-next-diagnosis"
    for folder in analyses.values():
        if (folder / "manifest.json").exists():
            for row in read_json(folder / "manifest.json")["files"]:
                assert digest(folder / row["path"]) == row["sha256"]
    trace_result = audit_trace(ROOT / "trace/baseline", baseline)
    evaluated = {name: audit_run(ROOT / name, baseline) for name in RUNS}
    for name in RUNS:
        source = ROOT / name
        preflight = read_json(source / "preflight.json")
        for path, expected in preflight["source_hashes"].items():
            assert digest(path) == expected
        assert digest(source / "executed-driver.py") == preflight["driver_sha256"]
        for file, expected in preflight["implementation_sha256"].items():
            assert digest(REPO / "plugins/astra-sumo/src/astra_sumo/core" / file) == expected
    if args.check_only:
        print(json.dumps({"trace": trace_result, "evaluations": evaluated}, indent=2), flush=True)
        return
    case = args.output_dir.resolve()
    assert not case.exists(), "Use a new benchmark directory."
    assert (ROOT / "lane-prior-response-audit/manifest.json").is_file(), "Wait for the independent response audit manifest."
    case.mkdir(parents=True)
    provenance, local = [], []

    def compress(source, relative):
        path = case / (str(relative).replace("\\", "/") + ".gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        value = source.read_bytes()
        path.write_bytes(gzip.compress(value, mtime=0))
        provenance.append({"source": str(source), "snapshot": path.relative_to(case).as_posix(),
                           "raw_bytes": len(value), "raw_sha256": hashlib.sha256(value).hexdigest()})

    for path in sorted(baseline.rglob("*.gz")):
        if path.parts[-2] in {"prepared", "evaluation", "native"}:
            relative = Path("baseline") / path.relative_to(baseline)
            target = case / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            value = gzip.decompress(path.read_bytes())
            provenance.append({"source": str(path), "snapshot": relative.as_posix(), "raw_bytes": len(value), "raw_sha256": hashlib.sha256(value).hexdigest()})
    for name, folder in analyses.items():
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                compress(path, Path(name) / path.relative_to(folder))
    for path in sorted((ROOT / "trace").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.name in {"instant-e1.xml", "queues.xml"}:
            local.append({"run": "trace/baseline", "path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)})
        else:
            compress(path, path.relative_to(ROOT))
    for name in RUNS:
        source = ROOT / name
        for folder in ("prepared", "evaluation"):
            for path in sorted((source / folder).iterdir()):
                if path.is_file():
                    compress(path, Path(name) / folder / path.name)
        for path in sorted((source / "evaluation/native").iterdir()):
            if path.name in {"instant-e1.xml", "queues.xml"}:
                local.append({"run": name, "path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)})
            elif path.is_file():
                compress(path, Path(name) / "native" / path.name)
        for file in ("preflight.json", "verdict.json", "executed-driver.py"):
            compress(source / file, Path(name) / file)
        for suffix in ("-launch.json", ".stdout.log", ".stderr.log"):
            compress(ROOT / (name + suffix), Path(name) / (name + suffix))
    for file in CORE:
        compress(REPO / "plugins/astra-sumo/src/astra_sumo/core" / file, Path("code-source") / file)
    compress(ROOT / "runtime.json", "runtime.json")
    compress(ROOT / "assessment.json", "assessment.json")
    compress(ROOT / "run_lane_prior_probe.py", "run_lane_prior_probe.py")
    for file in ("freeze_evidence.py", "test_freeze_evidence.py", "reproduce.py"):
        shutil.copyfile(ROOT / file, case / file)
    (case / ".gitattributes").write_text("* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n", encoding="utf-8")
    write_json(case / "source-provenance.json", {"snapshots": provenance})
    write_json(case / "local-only-large-traces.json", {"files": local})
    write_json(case / "results.json", {"formal_result": False, "zero_residual_goal_complete": False,
        "complete_runs": list(RUNS), "trace": trace_result, "evaluations": evaluated,
        "offline_helper_sha256": digest(CLASSIC / "freeze_evidence.py"),
        "claim_boundary": "Separate explicit lane-prior counterfactuals, not an OD quantile update. Actual model TLS replay is not field SPaT. All 130 real targets and 52 initialization counts remain."})
    table = ["| 显式车道先验试验 | 130 SSE | 130 MAE | 182 TAE | 未入网 | 在途 | 碰撞/传送 | 原始判定 |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    for name, value in evaluated.items():
        c = value["completion"]
        table.append(f"| {name} | {value['main130']['SSE']} | {value['main130']['MAE']:.6f} | {value['all182']['TAE']} | {c['waiting']} | {c['running']} | {c['collisions']}/{c['teleports']} | {value['status']} |")
    text = """# 汉堡Z5上游长延误与车道先验核验

主130项真实计数与原生E1 nVehContrib全部一致的目标仍未完成。本包不构成正式标定。
起点为selected probe-minus1：MAE5.092308、SSE6898、TAE662，7辆未入网、14辆在途。
本轮保留相同道路、控制、8640请求、OD数量、路线与发车时刻，仅交换1对或5对车辆的字段意向及对应moving-stop车道。
这是独立车道先验反事实，不是相同OD模型的分位数更新，也不表示识别了真实驾驶意向。

""" + "\n".join(table) + """

1对交换得到最低已测安全诊断MAE4.969231、SSE6636，但未入网10辆、在途15辆，超过预定waiting≤7和unfinished≤21条件，原始标签仍为rejected。
这项计数改善不表示已经消除积压。5对交换首次使全部8640辆插入，仍有16辆在途，而且计数误差变差，因此同样rejected。source5.092308继续保留为本轮选用参考。
完整判断及其范围见[评估记录](assessment.json.gz)，两次有限试验不证明零误差可行或不可行。

每项的native/field-comparison.csv.gz保留13字段×10时段的130项原生对账，evaluation/station-comparison.csv.gz保留40项派生站点值，evaluation/all182-comparison.csv.gz保留52初始化与130主观测。
完整贡献轨迹在evaluation/contributing-passages.json.gz.gz，保留原压缩文件的字节。失败或拒绝结果及原始verdict均保留，不删观测或用缺测补零。
字段库存按请求时段守恒不保证按观测时段计数守恒。安全、未进入、未完成、错字段及重复过点另行核查。

七类分析包括selected-next-diagnosis、queue-audit、control-audit及actual-trace、driver-audit、trace-analysis和lane-prior-probe-plan，均保留原文件与哈希。
trace/baseline保存原生208项E1、Summary、TripInfo、路线、碰撞、115辆完整FCD、TLS状态与程序、全部lanechange、请求与实际执行脚本。
观察回放12项一致性检查通过，104/118状态与90秒声明周期一致。这是当前模型的实际回放，不是现场SPaT。
104 link14和535 link5缺绿等原始警告继续保留。上游排队与后续接收约束不能据单次运行证明不可达。
仅原始instant E1和queues保留为本地路径与SHA-256。FCD及新compact完整贡献已压缩进入本包。

离线核验不启动SUMO：

```powershell
.venv/Scripts/python.exe benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1/freeze_evidence.py --verify-only benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1
.venv/Scripts/python.exe -m pytest benchmarks/hamburg_ring1_z5_upstream_delay_20261023_v1/test_freeze_evidence.py -q
```

reproduce.py必须显式指定case与新output-dir，才会用冻结的明确需求调用原生执行；不会调用OD生成器。
离线通用读入与计数对账复用同仓库[前一经典OD包](../hamburg_ring1_classical_od_response_20261023_v1/README.md)的冻结函数，其源码哈希记录在results.json。
OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
"""
    (case / "README.md").write_text(text, encoding="utf-8")
    write_json(case / "manifest.json", {"formal_result": False, "files": [
        {"path": p.relative_to(case).as_posix(), "bytes": p.stat().st_size, "sha256": digest(p)}
        for p in sorted(case.rglob("*")) if p.is_file() and p != case / "manifest.json" and "__pycache__" not in p.parts]})
    verify(case)


if __name__ == "__main__":
    main()
