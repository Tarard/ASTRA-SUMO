"""Materialize the approved empirical OD baseline and native SPSA loss calls."""

from collections import defaultdict
import gzip
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import junction_visit_audit
from .detector_demand import read_net, read_csv_rows, write_csv
from .detector_demand_replay import run_point_detector_demand
from .hamburg_readme_results import STATIONS
from .road_hierarchy import _path, _paths
from .spsa import calibrate_spsa


def prepare_hamburg_od_baseline(*, case_dir, audit_dir, output_dir):
    case, audit, out = Path(case_dir).resolve(strict=True), Path(audit_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or any(p.is_relative_to(out) for p in (case, audit)):
        raise ValueError("Use a new OD baseline directory separate from its inputs.")
    out.mkdir(parents=True)
    for name in ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "control.add.xml",
                 "expected-counts.csv", "lane-intentions.json", "initialization-counts.csv"):
        path = case / "inputs" / name
        raw = path.read_bytes() if path.exists() else gzip.decompress(path.with_name(name + ".gz").read_bytes())
        (out / name).write_bytes(raw)
    checked = json.loads((audit / "audit.json").read_bytes())
    if file_sha256(out / "network.net.xml") != checked["network_sha256"]:
        raise ValueError("The OD audit and baseline network differ.")
    network = ET.parse(out / "network.net.xml").getroot()
    edges, connections = read_net(out / "network.net.xml")
    active = {e for e in edges if edges[e].allows_passenger}
    turns = {(r.get("from"), r.get("to")) for r in network.findall("connection") if r.get("dir") == "t"}
    graph = {e: {f for f in connections.get(e, set()) & active if (e, f) not in turns} for e in active}
    lengths = {e: edges[e].length for e in active}
    sources, sinks = set(checked["declared_entries"]), set(checked["declared_exits"])
    groups = {j.get("id"): j.get("id").split("_")[0] for j in network.findall("junction")
              if j.get("id", "").startswith(("LSA104_", "LSA118_", "LSA119_", "LSA200_", "LSA535_"))}
    intentions = {r["vehicle_id"]: r["field_id"]
                  for r in json.loads((out / "lane-intentions.json").read_bytes())["vehicles"]}
    demand = ET.parse(out / "demand.rou.xml").getroot()
    cells, extensions = defaultdict(list), {}
    for vehicle in demand.findall("vehicle"):
        route = vehicle.find("route").get("edges").split()
        if route[0] not in sources:
            raise ValueError("An inherited origin is not a declared model port.")
        if route[-1] not in sinks:
            if route[-1] not in extensions:
                distance, previous = _paths(graph, lengths, route[-1], active)
                choices = sinks & distance.keys()
                if not choices:
                    raise ValueError("An internal trip end has no forward model exit.")
                destination = min(choices, key=lambda e: (distance[e], e))
                extensions[route[-1]] = _path(previous, route[-1], destination)
            route += extensions[route[-1]][1:]
        if not junction_visit_audit(route, edges, graph, groups)["allowed"]:
            raise ValueError("An inherited or extended route repeats a road or junction.")
        vehicle.find("route").set("edges", " ".join(route))
        depart = float(vehicle.get("depart"))
        if not math.isfinite(depart) or not 0 <= depart < 12600:
            raise ValueError("Keep departures in the fourteen declared 15-minute intervals.")
        key = int(depart // 900), route[0], route[-1]
        cells[key].append({"vehicle_xml": ET.tostring(vehicle, encoding="unicode"),
                           "prior_depart_s": depart, "prior_field_intention": intentions[vehicle.get("id")]})
    pairs = sorted({(o, d) for h, o, d in cells})
    if len(pairs) != 21 or len(cells) != 294:
        raise ValueError("Reassess any change to the approved 21-OD, 14-interval support.")
    rows = [{"origin": o, "destination": d, "begin_s": 900 * h, "end_s": 900 * (h + 1),
             "initial_flow": len(cells[h, o, d]),
             "templates": sorted(cells[h, o, d], key=lambda r: (r["prior_depart_s"], r["vehicle_xml"]))}
            for h in range(14) for o, d in pairs]
    model = {"source_case": str(case), "prepared_dir": str(out), "source_network_sha256": checked["network_sha256"],
             "cells": rows, "exit_extensions": extensions,
             "type_definitions": [ET.tostring(r, encoding="unicode") for r in demand if r.tag != "vehicle"],
             "assignment_prior": "Inherited joint route, lane-intention and departure-time distribution within each OD and departure interval. Fixed empirical quantile resampling; not observed OD or natural driving.",
             "vehicle_generation": "Round each nonnegative real OD cell to nearest integer, with half rounded upward. Interpolate inherited departure-time quantiles; select the corresponding empirical route and lane template.",
             "formal_result": False}
    write_json_atomic(out / "od-model.json", model)
    initial = [r["initial_flow"] for r in rows]
    write_json_atomic(out / "initial-od-vector.json", {"values": initial, "vehicle_count": sum(initial),
                                                     "ordering": "departure interval, then origin and destination"})
    return model


def write_hamburg_od_demand(*, model_file, values, output_dir):
    model = json.loads(Path(model_file).read_bytes())
    cells, out = model["cells"], Path(output_dir).resolve()
    theta = np.asarray(values, dtype=float)
    if theta.shape != (len(cells),) or not np.all(np.isfinite(theta)) or np.any(theta < 0):
        raise ValueError("Supply one finite nonnegative flow for every OD departure cell.")
    if out.exists():
        raise ValueError("Use a new demand directory for each objective evaluation.")
    out.mkdir(parents=True)
    root = ET.Element("routes")
    root.extend(ET.fromstring(text) for text in model["type_definitions"])
    generated, intentions = [], []
    integer = [math.floor(float(value) + .5) for value in theta]
    for column, (cell, count) in enumerate(zip(cells, integer)):
        templates = cell["templates"]
        m = len(templates)
        positions = (np.arange(m) + .5) / m
        for index in range(int(count)):
            quantile = (index + .5) / count
            template = templates[min(m - 1, int(quantile * m))]
            vehicle = ET.fromstring(template["vehicle_xml"])
            identity = f"od_{column:04d}_v{index:05d}"
            depart = float(np.interp(quantile, positions, [r["prior_depart_s"] for r in templates]))
            vehicle.set("id", identity)
            vehicle.set("depart", f"{depart:.12f}")
            generated.append(vehicle)
            intentions.append({"vehicle_id": identity, "field_id": template["prior_field_intention"],
                               "od_cell": column})
    root.extend(sorted(generated, key=lambda v: (float(v.get("depart")), v.get("id"))))
    ET.ElementTree(root).write(out / "demand.rou.xml", encoding="utf-8", xml_declaration=True)
    write_json_atomic(out / "lane-intentions.json", {"mechanism": model["assignment_prior"], "vehicles": intentions})
    write_json_atomic(out / "od-vector.json", {"values": theta.tolist(), "integer_counts": integer,
                                             "vehicle_count": sum(integer), "source_model_sha256": file_sha256(Path(model_file))})
    return out


def measure_hamburg_od(*, prepared_dir, values, output_dir, sumo_binary):
    prepared = Path(prepared_dir).resolve(strict=True)
    proposal = write_hamburg_od_demand(model_file=prepared / "od-model.json", values=values, output_dir=output_dir)
    native = run_point_detector_demand(net_file=prepared / "network.net.xml", route_file=proposal / "demand.rou.xml",
        e1_additional_file=prepared / "field-loops.add.xml", expected_counts_csv=prepared / "expected-counts.csv",
        output_dir=proposal / "native", end_s=14400, sumo_binary=str(sumo_binary), timeout_seconds=4800,
        signal_program_file=prepared / "control.add.xml", lane_intentions_file=proposal / "lane-intentions.json",
        junction_body_hold=True, junction_body_hold_nodes=("14398547", "205069138", "18653534"), seed=104)
    rows = read_csv_rows(proposal / "native/field-comparison.csv")
    if (not native["observer_complete"] or len(rows) != 130 or native.get("native_status") != "pass"
            or (native.get("stall_monitor") or {}).get("status") != "completed"):
        raise ValueError("The native evaluation is incomplete. Preserve all rows and raw simulation outputs.")
    residuals = [int(r["diff_nVehContrib_minus_expected"]) for r in rows]
    source = {(r["detector_id"], r["begin"], r["end"]): r for r in read_csv_rows(prepared / "expected-counts.csv")}
    station_groups = defaultdict(list)
    for row in rows:
        metadata = source[row["detector_id"], row["begin"], row["end"]]
        for station, (node, assets) in STATIONS.items():
            if metadata["node_id"] == node and metadata["asset_id"] in assets:
                station_groups[station, row["begin"], row["end"]].append(row)
    stations = [{"station_stream_id": station, "begin": begin, "end": end,
                 "expected_total": sum(int(r["expected_total"]) for r in group),
                 "measured_nVehContrib": sum(int(r["measured_nVehContrib"]) for r in group),
                 "residual": sum(int(r["diff_nVehContrib_minus_expected"]) for r in group),
                 "independent_evidence": False} for (station, begin, end), group in sorted(station_groups.items())]
    if len(stations) != 40 or any(len(group) != len(STATIONS[key[0]][1]) for key, group in station_groups.items()):
        raise ValueError("Every station row must retain all its constituent detector fields.")
    write_csv(proposal / "station-comparison.csv", stations, list(stations[0]))
    result = {"loss": sum(r * r for r in residuals), "MAE": sum(map(abs, residuals)) / 130,
              "max_abs_error": max(map(abs, residuals)), "healthy": native["healthy"],
              "station_MAE": sum(abs(r["residual"]) for r in stations) / 40,
              "station_max_abs_error": max(abs(r["residual"]) for r in stations),
              "completion": native["completion"], "exact_count_fit": native["exact_count_fit"],
              "count_source": "Native E1 nVehContrib only", "field_rows": 130, "derived_station_rows": 40}
    write_json_atomic(proposal / "evaluation.json", result)
    return result


def run_hamburg_od_spsa(*, prepared_dir, output_dir, sumo_binary, settings):
    """Evaluate only complete native E1 vectors; record unsafe runs as diagnostic losses."""
    prepared, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or prepared.is_relative_to(out):
        raise ValueError("Use a new SPSA directory separate from its inputs.")
    out.mkdir(parents=True)
    initial = json.loads((prepared / "initial-od-vector.json").read_bytes())["values"]
    write_json_atomic(out / "settings.json", settings)
    evaluations = []

    def objective(theta, name):
        evaluation = measure_hamburg_od(prepared_dir=prepared, values=theta, output_dir=out / name, sumo_binary=sumo_binary)
        evaluations.append({"name": name, **evaluation})
        write_json_atomic(out / "evaluations.json", evaluations)
        return evaluation["loss"]

    objective(np.asarray(initial, dtype=float), "initial")
    result = calibrate_spsa(objective, initial, lower=np.zeros(len(initial)), upper=np.full(len(initial), np.inf),
                            **settings, on_iteration=lambda row: write_json_atomic(out / f"iteration-{row['iteration']:04d}.json", row))
    objective(np.asarray(result["theta"]), "final")
    result.update(final_vector_remeasured=True, formal_result=evaluations[-1]["exact_count_fit"],
                  native_evaluations=len(evaluations), evaluations=evaluations,
                  claim_boundary="Unsafe or backlogged loss evaluations are diagnostic. Only a complete healthy final native replay with all 130 exact fields can be accepted. Station values are derived, not independent observations.")
    write_json_atomic(out / "result.json", result)
    return result
