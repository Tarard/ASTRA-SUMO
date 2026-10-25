"""Measure and finitely correct demand on one prepared Ring1 corridor."""

import gzip
import json
from pathlib import Path
import shutil

from .artifact_io import write_json_atomic
from .detector_demand_replay import run_point_detector_demand
from .hamburg_readme_pipeline import calibrate_hamburg_native_counts
from .hamburg_readme_results import summarize_hamburg_readme


def run_corridor_calibration(*, repository, prepared_dir, figure_dir, output_dir, sumo_binary):
    """Measure the new seed, then allow at most two native feedback candidates."""
    root, prepared, figures = [Path(path).resolve(strict=True) for path in (repository, prepared_dir, figure_dir)]
    out = Path(output_dir).resolve()
    if out.exists() or any(path.is_relative_to(out) for path in (root, prepared, figures)):
        raise ValueError("Use a new calibration directory separate from its inputs.")
    out.mkdir(parents=True)
    shutil.copytree(prepared, out / "inputs")
    p = out / "inputs"
    for name in ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "expected-counts.csv",
                 "control.add.xml", "lane-intentions.json", "scenario.json", "initialization-counts.csv"):
        if not (p / name).is_file() and (p / (name + ".gz")).is_file():
            (p / name).write_bytes(gzip.decompress((p / (name + ".gz")).read_bytes()))
    print(json.dumps({"stage": "corridor_seed_native_start", "end_s": 14400}), flush=True)
    native = run_point_detector_demand(net_file=p / "network.net.xml", route_file=p / "demand.rou.xml",
        e1_additional_file=p / "field-loops.add.xml", expected_counts_csv=p / "expected-counts.csv",
        output_dir=out / "native", end_s=14400, sumo_binary=str(sumo_binary), timeout_seconds=4800,
        signal_program_file=p / "control.add.xml", lane_intentions_file=p / "lane-intentions.json",
        junction_body_hold=True, junction_body_hold_nodes=("14398547", "205069138", "18653534"))
    stats = summarize_hamburg_readme(repository=root, prepared_dir=p, native_dir=out / "native",
        figure_dir=figures, output_dir=out / "report")
    for name in ("field-comparison.csv", "station-comparison.csv", "passage-reconciliation.json", "vehicle-field-passages.csv"):
        shutil.copyfile(out / "report" / name, out / name)
    completion = native.get("completion", {})
    safe = (native.get("native_status") == "pass" and native.get("observer_complete")
        and (native.get("stall_monitor") or {}).get("status") == "completed"
        and not any(completion.get(key, 1) for key in ("collisions", "teleports", "discarded")))
    print(json.dumps({"stage": "corridor_seed_native_done", "diagnostic_MAE": stats["diagnostic_field_MAE"],
                      "maximum_absolute_error": stats["diagnostic_max_abs_error"], "completion": completion,
                      "formal_result": native.get("exact_count_fit", False)}), flush=True)
    result = {"seed": stats, "feedback": None, "selected_case": str(out),
              "formal_result": bool(native.get("exact_count_fit")), "maximum_native_runs": 3}
    if safe and not native.get("exact_count_fit"):
        result["feedback"] = calibrate_hamburg_native_counts(repository=root, reference_case=out,
            output_dir=out / "feedback", sumo_binary=sumo_binary, figure_dir=figures)
        result["selected_case"] = result["feedback"]["selected_case"]
        result["formal_result"] = result["feedback"]["formal_result"]
    result["claim_boundary"] = "One fixed corridor, unchanged 130 targets and 40 derived station rows. A finite calibration attempt, not a proof of infeasibility. Retained candidates require native validation; no count or safety requirement is relaxed."
    write_json_atomic(out / "corridor-calibration-result.json", result)
    return result
