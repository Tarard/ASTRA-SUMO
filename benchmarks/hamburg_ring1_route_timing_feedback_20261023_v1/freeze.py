"""Freeze seven completed route/timing trials without running SUMO."""

import csv
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages

CASE = Path(__file__).resolve().parent
REPO = CASE.parents[1]
SOURCE = REPO / "outputs/hamburg-z5-native-feedback-20261023-v1"
RUNS = (
    "z15-residential-full",
    "z15-half-full",
    "z15-half-unsplit-full",
    "timing-01-16",
    "timing-01-32",
    "timing-01-64",
    "timing-01-128",
)
LOG_PREFIX = {
    "z15-residential-full": "z15-full",
    "z15-half-full": "z15-half-full",
    "z15-half-unsplit-full": "unsplit-full",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def compress(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = source.read_bytes()
    target.write_bytes(gzip.compress(raw, mtime=0))
    return {
        "source": str(source),
        "snapshot": target.relative_to(CASE).as_posix(),
        "raw_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    (CASE / ".gitattributes").write_text(
        "* -text whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol\n*.gz binary\n", encoding="utf-8"
    )
    results, provenance, local, code = {}, [], [], {}
    prepared_names = (
        "network.net.xml",
        "control.add.xml",
        "demand.rou.xml",
        "lane-intentions.json",
        "field-loops.add.xml",
        "expected-counts.csv",
        "initialization-counts.csv",
        "scenario.json",
    )
    native_names = (
        "field-counts.xml",
        "summary.xml",
        "tripinfo.xml",
        "vehicle-routes.xml",
        "collisions.xml",
        "sumo.log",
        "sumo-errors.log",
        "native-request.json",
        "stall-monitor.json",
        "run.json",
        "command.json",
        "e1.add.xml",
        "instant.add.xml",
        "field-comparison.csv",
    )
    input_names = {"field-loops.add.xml": "field-loops.add.xml", "control.add.xml": "control.add.xml"}
    for name in RUNS:
        source, target = SOURCE / name, CASE / name
        target.mkdir(exist_ok=True)
        verdict = json.loads((source / "verdict.json").read_bytes())
        run = json.loads((source / "evaluation/native/run.json").read_bytes())
        monitor = json.loads((source / "evaluation/native/stall-monitor.json").read_bytes())
        assert run["native_status"] == "pass" and run["observer_complete"]
        assert monitor["status"] == "completed" and monitor["simulation_time_s"] == 14400
        results[name] = verdict
        for file in prepared_names:
            provenance.append(compress(source / "prepared" / file, target / "prepared" / (file + ".gz")))
        for file in native_names:
            provenance.append(compress(source / "evaluation/native" / file, target / "native" / (file + ".gz")))
        for file in ("evaluation.json", "station-comparison.csv", "all182-comparison.csv", "od-vector.json"):
            provenance.append(compress(source / "evaluation" / file, target / "evaluation" / (file + ".gz")))
        for file in ("preflight.json", "verdict.json", "executed-driver.py"):
            provenance.append(compress(source / file, target / (file + ".gz")))
        if (source / "proposal.json").is_file():
            provenance.append(compress(source / "proposal.json", target / "proposal.json.gz"))
        prefix = LOG_PREFIX.get(name, name)
        for suffix, file in (
            ("-execution.json", "execution.json"),
            (".stdout.log", "stdout.log"),
            (".stderr.log", "stderr.log"),
        ):
            provenance.append(compress(SOURCE / (prefix + suffix), target / (file + ".gz")))
        inputs = []
        for row in run["inputs"]:
            base = Path(row["path"]).name
            file = input_names.get(base, base)
            assert digest(source / "prepared" / file) == row["sha256"]
            inputs.append({**row, "snapshot": "prepared/" + file + ".gz"})
        preflight = json.loads((source / "preflight.json").read_bytes())
        for file, expected in preflight["input_sha256"].items():
            assert digest(source / "prepared" / file) == expected
        for file, expected in preflight["implementation_sha256"].items():
            path = REPO / "plugins/astra-sumo/src/astra_sumo/core" / file
            assert digest(path) == expected
            code[file] = expected
        write_json(
            target / "input-hashes.json",
            {
                "inputs": inputs,
                "initialization_targets": {
                    "snapshot": "prepared/initialization-counts.csv.gz",
                    "sha256": digest(source / "prepared/initialization-counts.csv"),
                },
            },
        )
        for file in ("instant-e1.xml", "queues.xml"):
            path = source / "evaluation/native" / file
            local.append({"run": name, "path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)})
        print(name, verdict["after"]["MAE"], verdict["status"], flush=True)
    for file, expected in code.items():
        path = REPO / "plugins/astra-sumo/src/astra_sumo/core" / file
        provenance.append(compress(path, CASE / "code-source" / (file + ".gz")))
        assert digest(path) == expected
    feedback = REPO / "plugins/astra-sumo/src/astra_sumo/core/native_temporal_feedback.py"
    for name in RUNS[3:]:
        proposal = json.loads((SOURCE / name / "proposal.json").read_bytes())
        assert proposal["core_sha256"] == digest(feedback)
    provenance.append(compress(feedback, CASE / "code-source/native_temporal_feedback.py.gz"))
    timing_native = SOURCE / "z15-half-unsplit-full/evaluation/native"
    timing_run = json.loads((timing_native / "run.json").read_bytes())
    passages = read_contributing_e1_passages(
        instant_file=timing_native / "instant-e1.xml",
        periodic_file=timing_native / "field-counts.xml",
        detector_ids=timing_run["instant_to_periodic_ids"],
        step_length=0.1,
    )
    assert passages["reconciled"]
    (CASE / "timing-source-passages.json.gz").write_bytes(
        gzip.compress((json.dumps(passages, indent=2) + "\n").encode("utf-8"), mtime=0)
    )
    for name in ("baseline-prefix-900", "z15-residential-prefix-900", "z15-half-prefix-900"):
        for file in ("result.json", "field-counts.xml", "summary.xml", "collisions.xml"):
            provenance.append(compress(SOURCE / name / file, CASE / "prefixes" / name / (file + ".gz")))
    audit = SOURCE / "agent-backlog/timing-response-audit"
    for relative in ("review.json", "timing-01-64/review.json", "timing-01-128/review.json"):
        provenance.append(compress(audit / relative, CASE / "response-audit" / (relative + ".gz")))
    write_json(
        CASE / "results.json",
        {
            "formal_result": False,
            "zero_residual_goal_complete": False,
            "goal": "All 130 main-window native E1 residuals must equal zero, with safety and completion assessed separately.",
            "complete_runs": list(RUNS),
            "best_safe_diagnostic": "z15-half-unsplit-full",
            "evaluations": results,
            "primary_reference": "../hamburg_ring1_primary_branch_repair_20261023_v1",
            "four_vehicle_reference": "../hamburg_ring1_119_single_split_20261023_v1",
            "claim_boundary": "Route shares are sensitivity inputs. Notification translation scores failed fresh native evaluations. Forty station values are derived.",
        },
    )
    write_json(CASE / "source-provenance.json", {"snapshots": provenance})
    write_json(CASE / "local-only-large-traces.json", {"files": local})
    write_json(
        CASE / "environment.json",
        {
            "python": sys.version,
            "python_executable": sys.executable,
            "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "source_has_uncommitted_changes": True,
            "versions": {
                name: version(name) for name in ("eclipse-sumo", "libsumo", "traci", "sumolib", "numpy", "scipy")
            },
        },
    )
    table = [
        "| 完整运行 | 主窗TAE | 字段MAE | 最大误差 | 182项TAE | 未入网 | 在途 | 碰撞/传送 | 原始判定 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, verdict in results.items():
        value, completion = verdict["after"], verdict["after"]["completion"]
        with (SOURCE / name / "evaluation/native/field-comparison.csv").open(encoding="utf-8", newline="") as stream:
            tae = sum(abs(int(row["diff_nVehContrib_minus_expected"])) for row in csv.DictReader(stream))
        table.append(
            f"| {name} | {tae} | {value['MAE']:.6f} | {value['max_abs_error']} | {verdict['all182_TAE']} | "
            f"{completion['waiting']} | {completion['running']} | {completion['collisions']}/{completion['teleports']} | {verdict['status']} |"
        )
    text = (
        """# 汉堡路线分流与时刻反馈的完整原生证据

目标仍是主窗口130项原生E1残差全部为零。**目标未完成**，本包七项运行均不是有效标定结果。
最小的安全诊断MAE为5.961538，仍有68辆未入网、50辆在途。
主窗口为2026-10-19 05:30–08:00 UTC，对应仿真3600–12600秒。
初始化小时另有52项真实计数。每项均从空网运行至14400秒，8640辆请求、步长0.1秒、种子104、Libsumo1.27.1。
道路、信号和车辆数的变化范围由各项实际输入及哈希明确记录。

"""
        + "\n".join(table)
        + """

表中130项字段值来自原生E1 `nVehContrib`。40项站点值由字段求和，不是独立观测。
全部182项、完成状态和安全记录均保留。原始verdict未经修改。
`z15-half-full`的两次碰撞和两次传送使其被拒绝，较低误差不能抵消该失败。
路线分流只改变Z15点后的合法尾段，不是已识别的现场分流比例。
单连接修复的小车复现与边界见[LSA119单连接包](../hamburg_ring1_119_single_split_20261023_v1/README.md)。
完整primary起点见[此前冻结包](../hamburg_ring1_primary_branch_repair_20261023_v1/README.md)。

四个时间批次均被拒绝。16/32/64/128是批次上限，最后一项实际只选107辆。
旧记录平移分值不是新车流预测。64和107辆的投影主窗TAE为663和615，原生实测却为897和874。
`response-audit/`保留贡献归属复核。被修改与未修改车辆的差异不是因果分离，不能把记账分组当成独立交通效果。
原始真实目标没有修改，也没有把逐车请求重新送入分位数车辆生成器。

每项的`prepared/`保存实际输入，`native/`保存原生E1、Summary、TripInfo、车辆路线、碰撞、日志和运行记录。
`evaluation/`保存130项字段、40项站点、182项完整比较和OD计数元数据。
执行脚本、preflight、verdict及执行日志按原字节压缩。输入哈希对应本包快照。
大体积instant E1和逐秒queues保留本地路径与SHA-256，见`local-only-large-traces.json`。
三个900秒prefix的关键结果、计数和完成记录另存。它们的主窗口计数仍为null，不能代替完整运行。

离线核查不启动SUMO：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_hamburg_route_timing_feedback_evidence.py -q
```

完整复跑只读取冻结的明确请求，并复用现有原生执行和汇总函数。每次使用新的输出目录：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH="$PWD/plugins/astra-sumo/src"
$env:SUMO_HOME="$PWD/.venv/Lib/site-packages/sumo"
.venv/Scripts/python.exe benchmarks/hamburg_ring1_route_timing_feedback_20261023_v1/reproduce.py --case timing-01-16 --output-dir outputs/route-timing-recheck-new
```

该命令会运行完整仿真。本次冻结只执行离线检查，没有增加原生运行。
OSM数据归属：© OpenStreetMap contributors，遵循[ODbL 1.0](https://www.openstreetmap.org/copyright)。
"""
    )
    (CASE / "README.md").write_text(text, encoding="utf-8")
    write_json(
        CASE / "manifest.json",
        {
            "formal_result": False,
            "files": [
                {"path": path.relative_to(CASE).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}
                for path in sorted(CASE.rglob("*"))
                if path.is_file() and path.name != "manifest.json" and "__pycache__" not in path.parts
            ],
        },
    )


if __name__ == "__main__":
    main()
