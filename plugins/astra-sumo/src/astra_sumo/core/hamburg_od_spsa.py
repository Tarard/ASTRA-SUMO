"""Materialize the approved empirical OD baseline and native SPSA loss calls."""

from collections import defaultdict
import gzip
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import junction_visit_audit
from .detector_demand import read_net, read_csv_rows, write_csv
from .detector_demand_replay import run_point_detector_demand
from .hamburg_readme_results import STATIONS
from .lane_section_hold import read_lane_intentions
from .road_hierarchy import _path, _paths
from .spsa import InitialSPSAGainUnavailable, calibrate_spsa


def _body_hold_nodes(prepared_dir):
    path = Path(prepared_dir) / "scenario.json"
    if not path.exists() and not path.with_name(path.name + ".gz").exists():
        return ("14398547", "205069138", "18653534")
    raw = path.read_bytes() if path.exists() else gzip.decompress(path.with_name(path.name + ".gz").read_bytes())
    scenario = json.loads(raw)
    nodes = scenario.get("junction_body_hold_nodes", ["14398547", "205069138", "18653534"]) if isinstance(scenario, dict) else None
    if (not isinstance(nodes, list) or not nodes or any(not isinstance(n, str) or not n or n.strip() != n for n in nodes)
            or len(set(nodes)) != len(nodes)):
        raise ValueError("Declare distinct, nonempty junction_body_hold_nodes in scenario.json.")
    return tuple(nodes)


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
    scenario = case / "inputs/scenario.json"
    if scenario.exists() or scenario.with_name(scenario.name + ".gz").exists():
        (out / "scenario.json").write_bytes(scenario.read_bytes() if scenario.exists() else
            gzip.decompress(scenario.with_name(scenario.name + ".gz").read_bytes()))
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


def prepare_hamburg_od_from_explicit(*, prepared_dir, output_dir):
    """Rebuild the 294-cell empirical prior without extending or replacing routes."""
    source, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or source.is_relative_to(out):
        raise ValueError("Use a new OD model directory separate from its explicit inputs.")
    names = ("network.net.xml", "demand.rou.xml", "field-loops.add.xml", "control.add.xml",
             "expected-counts.csv", "lane-intentions.json", "initialization-counts.csv", "scenario.json")
    paths = [(source / name).resolve(strict=True) for name in names]
    hashes = {name: file_sha256(path) for name, path in zip(names, paths)}
    _body_hold_nodes(source)
    demand = ET.parse(source / "demand.rou.xml").getroot()
    vehicles = demand.findall("vehicle")
    ids = [v.get("id") for v in vehicles]
    if (demand.tag != "routes" or demand.attrib or not vehicles or len(set(ids)) != len(ids)
            or any(not identity for identity in ids)
            or any(row.tag in {"flow", "trip", "person", "personFlow"} for row in demand)):
        raise ValueError("Use unique explicit vehicles under an unadorned routes element.")
    read_lane_intentions(source / "lane-intentions.json", source / "field-loops.add.xml", ids)
    intentions = json.loads((source / "lane-intentions.json").read_bytes())
    fields = {r["vehicle_id"]: r["field_id"] for r in intentions["vehicles"]}
    assignment_prior = intentions.get("mechanism")
    if not isinstance(assignment_prior, str) or not assignment_prior.strip():
        raise ValueError("Declare the empirical lane-intention mechanism.")
    edges, connections = read_net(source / "network.net.xml")
    active = {e for e in edges if edges[e].allows_passenger}
    graph = {e: connections.get(e, set()) & active for e in active}
    network = ET.parse(source / "network.net.xml").getroot()
    groups = {j.get("id"): j.get("id").split("_")[0] for j in network.findall("junction")
              if j.get("id", "").startswith(("LSA104_", "LSA118_", "LSA119_", "LSA200_", "LSA535_"))}
    cells, checked_routes = defaultdict(list), set()
    for vehicle in vehicles:
        route_row = vehicle.find("route")
        if route_row is None or not route_row.get("edges"):
            raise ValueError("Retain one inline explicit route for every request.")
        route = tuple(route_row.get("edges").split())
        if route not in checked_routes:
            if (not set(route) <= active or not junction_visit_audit(route, edges, graph, groups)["allowed"]):
                raise ValueError("Retain legal passenger routes without repeated roads or junctions.")
            checked_routes.add(route)
        depart = float(vehicle.get("depart", "nan"))
        if not math.isfinite(depart) or not 0 <= depart < 12600:
            raise ValueError("Keep departures in the fourteen declared 15-minute intervals.")
        cells[int(depart // 900), route[0], route[-1]].append({
            "vehicle_xml": ET.tostring(vehicle, encoding="unicode"), "prior_depart_s": depart,
            "prior_vehicle_id": vehicle.get("id"), "prior_field_intention": fields[vehicle.get("id")]})
    pairs = sorted({(o, d) for h, o, d in cells})
    if len(pairs) != 21 or len(cells) != 294:
        raise ValueError("Reassess any change to the approved 21-OD, 14-interval support.")
    rows = [{"origin": o, "destination": d, "begin_s": 900 * h, "end_s": 900 * (h + 1),
             "initial_flow": len(cells[h, o, d]),
             "templates": sorted(cells[h, o, d], key=lambda r: (r["prior_depart_s"], r["prior_vehicle_id"]))}
            for h in range(14) for o, d in pairs]
    model = {"source_case": str(source), "prepared_dir": str(out), "source_network_sha256": hashes["network.net.xml"],
             "cells": rows, "exit_extensions": {},
             "type_definitions": [ET.tostring(r, encoding="unicode") for r in demand if r.tag != "vehicle"],
             "assignment_prior": assignment_prior,
             "vehicle_generation": "Round each nonnegative real OD cell to nearest integer, with half rounded upward. Interpolate inherited departure-time quantiles; select the corresponding empirical route and lane template.",
             "explicit_prior": {"source_hashes": hashes, "route_templates_from_current_explicit_requests": True,
                 "claim_boundary": "The current route, lane, stop and within-cell departure distribution is an empirical model prior, not measured OD or route shares. Changed counts retain the existing joint quantile resampling, which may change route and lane shares."},
             "formal_result": False}
    out.mkdir(parents=True)
    for name, path in zip(names, paths):
        copy_file_atomic(path, out / name)
    write_json_atomic(out / "od-model.json", model)
    initial = [row["initial_flow"] for row in rows]
    write_json_atomic(out / "initial-od-vector.json", {"values": initial, "vehicle_count": sum(initial),
                                                     "ordering": "departure interval, then origin and destination"})
    proposal = write_hamburg_od_demand(model_file=out / "od-model.json", values=initial, output_dir=out / "initial")
    generated = ET.parse(proposal / "demand.rou.xml").getroot().findall("vehicle")
    generated_ids = [v.get("id") for v in generated]
    old = {v.get("id"): v for v in vehicles}
    new = {v.get("id"): v for v in generated}
    common = old.keys() & new.keys()
    # Match SUMO TIME2STEPS at its native millisecond resolution, not Python round-to-even.
    def milliseconds(vehicle):
        return math.floor(float(vehicle.get("depart")) * 1000 + .5)

    different_depart = [i for i in sorted(common) if milliseconds(old[i]) != milliseconds(new[i])]
    different_attributes = [i for i in sorted(common) if
        {k: v for k, v in old[i].attrib.items() if k != "depart"} !=
        {k: v for k, v in new[i].attrib.items() if k != "depart"}]
    different_children = [i for i in sorted(common) if
        [ET.canonicalize(ET.tostring(c, encoding="unicode"), strip_text=True) for c in old[i]] !=
        [ET.canonicalize(ET.tostring(c, encoding="unicode"), strip_text=True) for c in new[i]]]
    new_intentions = json.loads((proposal / "lane-intentions.json").read_bytes())
    initial_vector = json.loads((proposal / "od-vector.json").read_bytes())
    old_xml, new_xml = (path.read_bytes() for path in (source / "demand.rou.xml", proposal / "demand.rou.xml"))
    expected_cells = {t["prior_vehicle_id"]: column for column, cell in enumerate(rows) for t in cell["templates"]}
    checks = {"all_source_inputs_unchanged": hashes == {name: file_sha256(path) for name, path in zip(names, paths)},
        "copied_inputs_rawbytes_identical": all(file_sha256(out / name) == hashes[name] for name in names),
        "unique_vehicle_ids_identical": len(generated_ids) == len(new) == len(old) and old.keys() == new.keys(),
        "vehicle_sequence_identical": ids == generated_ids,
        "departure_native_milliseconds_identical": not different_depart,
        "all_non_departure_request_attributes_identical": not different_attributes,
        "all_routes_stops_and_other_children_identical": not different_children,
        "full_demand_canonical_xml_identical": ET.canonicalize(old_xml.decode("utf-8"), strip_text=True) ==
            ET.canonicalize(new_xml.decode("utf-8"), strip_text=True),
        "all_field_intentions_identical": fields == {r["vehicle_id"]: r["field_id"] for r in new_intentions["vehicles"]},
        "cell_assignment_matches_current_requests": expected_cells == {r["vehicle_id"]: r["od_cell"] for r in new_intentions["vehicles"]},
        "integer_initial_counts_identical": initial_vector["integer_counts"] == initial and
            initial_vector["vehicle_count"] == sum(initial) == len(vehicles),
        "body_hold_scope_identical": _body_hold_nodes(source) == _body_hold_nodes(out)}
    report = {"schema": "astra.hamburg-explicit-od-initial-equivalence/v1",
        "status": "pass" if all(checks.values()) else "blocked", "checks": checks,
        "source_prepared_dir": str(source), "prepared_dir": str(out), "generated_initial_dir": str(proposal),
        "source_hashes": hashes, "model_sha256": file_sha256(out / "od-model.json"),
        "generated_hashes": {name: file_sha256(proposal / name) for name in
            ("demand.rou.xml", "lane-intentions.json", "od-vector.json")},
        "vehicle_count": len(vehicles), "od_pairs": len(pairs), "departure_bins": 14, "od_cells": len(rows),
        "different_departure_ids": different_depart, "different_request_attribute_ids": different_attributes,
        "different_route_or_stop_ids": different_children,
        "format": {"demand_rawbytes_identical": hashes["demand.rou.xml"] == file_sha256(proposal / "demand.rou.xml"),
            "intentions_rawbytes_identical": hashes["lane-intentions.json"] == file_sha256(proposal / "lane-intentions.json"),
            "departure_text_differences": sum(old[i].get("depart") != new[i].get("depart") for i in common),
            "vehicle_sequence_differences": sum(a != b for a, b in zip(ids, generated_ids)),
            "demand_body_rawbytes_identical": old_xml.partition(b"?>")[2] == new_xml.partition(b"?>")[2],
            "source_xml_declaration": old_xml.partition(b"?>")[0].decode("utf-8") + "?>",
            "generated_xml_declaration": new_xml.partition(b"?>")[0].decode("utf-8") + "?>"},
        "quantile_resampling_changed": False, "new_native_run": False, "cache_reuse_authorized": False,
        "formal_result": False,
        "claim_boundary": "Initial explicit input correspondence only, not a new native run or permission to alter cached evidence. Changed cell counts retain existing empirical quantile resampling. No prior route or stop is replaced by a historical template."}
    write_json_atomic(out / "initial-equivalence.json", report)
    if not all(checks.values()):
        raise ValueError("The rebuilt initial requests differ. Inspect initial-equivalence.json; preserve the candidate.")
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


def measure_hamburg_od(*, prepared_dir, values, output_dir, sumo_binary, backend="traci"):
    prepared = Path(prepared_dir).resolve(strict=True)
    body_nodes = _body_hold_nodes(prepared)
    proposal = write_hamburg_od_demand(model_file=prepared / "od-model.json", values=values, output_dir=output_dir)
    native = run_point_detector_demand(net_file=prepared / "network.net.xml", route_file=proposal / "demand.rou.xml",
        e1_additional_file=prepared / "field-loops.add.xml", expected_counts_csv=prepared / "expected-counts.csv",
        output_dir=proposal / "native", end_s=14400, sumo_binary=str(sumo_binary), timeout_seconds=4800,
        signal_program_file=prepared / "control.add.xml", lane_intentions_file=proposal / "lane-intentions.json",
        junction_body_hold=True, junction_body_hold_nodes=body_nodes, seed=104, backend=backend)
    return summarize_hamburg_od_measurement(prepared_dir=prepared, proposal_dir=proposal, native=native)


def summarize_hamburg_od_measurement(*, prepared_dir, proposal_dir, native):
    """Validate and summarize an explicit native demand without regenerating it."""
    prepared, proposal = Path(prepared_dir), Path(proposal_dir)
    rows = read_csv_rows(proposal / "native/field-comparison.csv")
    if (not native["observer_complete"] or len(rows) != 130 or native.get("native_status") != "pass"
            or (native.get("stall_monitor") or {}).get("status") != "completed"):
        raise ValueError("The native evaluation is incomplete. Preserve all rows and raw simulation outputs.")
    residuals = [int(r["diff_nVehContrib_minus_expected"]) for r in rows]
    source = {(r["detector_id"], float(r["begin"]), float(r["end"])): r
              for r in read_csv_rows(prepared / "expected-counts.csv")}
    station_groups = defaultdict(list)
    for row in rows:
        metadata = source[row["detector_id"], float(row["begin"]), float(row["end"])]
        for station, (node, assets) in STATIONS.items():
            if metadata["node_id"] == node and metadata["asset_id"] in assets:
                station_groups[station, float(row["begin"]), float(row["end"])].append(row)
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


def run_hamburg_od_spsa(*, prepared_dir, output_dir, sumo_binary, settings, initial_evaluation_dir=None,
                        stop_on_unsafe=False):
    """Evaluate only complete native E1 vectors; record unsafe runs as diagnostic losses."""
    prepared, out = Path(prepared_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or prepared.is_relative_to(out):
        raise ValueError("Use a new SPSA directory separate from its inputs.")
    out.mkdir(parents=True)
    initial = json.loads((prepared / "initial-od-vector.json").read_bytes())["values"]
    write_json_atomic(out / "settings.json", settings)
    evaluations = []
    history = []

    class ExactNativeFit(Exception):
        def __init__(self, theta, name):
            self.theta, self.name = theta.tolist(), name

    class UnsafeNativeEvaluation(Exception):
        def __init__(self, theta, name):
            self.theta, self.name = theta.tolist(), name

    def completed(theta, name, evaluation):
        evaluations.append({"name": name, **evaluation})
        write_json_atomic(out / "evaluations.json", evaluations)
        write_json_atomic(out / "progress.json", {"stage": "native_evaluation_completed", "evaluation": name,
                                                   "completed_iterations": len(history), **evaluation})
        if stop_on_unsafe and any(evaluation["completion"].get(key, 0) for key in ("collisions", "teleports", "discarded")):
            raise UnsafeNativeEvaluation(theta, name)
        if evaluation["exact_count_fit"]:
            raise ExactNativeFit(theta, name)
        return evaluation["loss"]

    def objective(theta, name):
        write_json_atomic(out / "progress.json", {"stage": "native_evaluation_running", "evaluation": name,
                                                   "completed_iterations": len(history)})
        evaluation = measure_hamburg_od(prepared_dir=prepared, values=theta, output_dir=out / name, sumo_binary=sumo_binary)
        return completed(theta, name, evaluation)

    def iteration(row):
        history.append(row)
        write_json_atomic(out / f"iteration-{row['iteration']:04d}.json", row)
        write_json_atomic(out / "progress.json", {"stage": "iteration_completed", "completed_iterations": len(history),
                                                   "a": row["a"], "theta": row["next_theta"]})

    try:
        if initial_evaluation_dir is None:
            objective(np.asarray(initial, dtype=float), "initial")
        else:
            from .hamburg_od_reuse import reuse_hamburg_od_initial
            proposed = write_hamburg_od_demand(model_file=prepared / "od-model.json", values=initial, output_dir=out / "initial")
            evaluation, provenance = reuse_hamburg_od_initial(prepared_dir=prepared, proposal_dir=proposed,
                                                              cached_dir=initial_evaluation_dir)
            write_json_atomic(out / "initial-reuse.json", provenance)
            completed(np.asarray(initial, dtype=float), "initial", {**evaluation, "reused": True})
        result = calibrate_spsa(objective, initial, lower=np.zeros(len(initial)), upper=np.full(len(initial), np.inf),
                                **settings, on_iteration=iteration)
        objective(np.asarray(result["theta"]), "final")
        result.update(final_vector_remeasured=True, selected_evaluation="final")
    except ExactNativeFit as reached:
        result = {"theta": reached.theta, "history": history, "stopping_reason": "healthy_native_zero_residual",
                  "final_vector_remeasured": False, "selected_evaluation": reached.name,
                  "claim": "The selected vector already has a complete, healthy native evaluation. No extra replay is needed."}
    except UnsafeNativeEvaluation as unsafe:
        result = {"theta": unsafe.theta, "history": history, "stopping_reason": "unsafe_native_evaluation",
                  "final_vector_remeasured": False, "selected_evaluation": unsafe.name,
                  "claim": "Stop after a collision, teleport or discard. Preserve all diagnostic losses and raw outputs."}
    except InitialSPSAGainUnavailable as error:
        result = {"theta": initial, "history": history, "stopping_reason": "initial_gain_not_identified",
                  "error": str(error), "final_vector_remeasured": False, "selected_evaluation": "initial"}
    except Exception as error:
        write_json_atomic(out / "failure.json", {"status": "failed", "error": f"{type(error).__name__}: {error}",
                           "completed_iterations": len(history), "completed_native_evaluations": len(evaluations),
                           "formal_result": False, "claim": "Preserve all partial native outputs. This is not evidence of infeasibility."})
        raise
    selected = next(row for row in evaluations if row["name"] == result["selected_evaluation"])
    result.update(formal_result=selected["exact_count_fit"] and result["stopping_reason"] != "unsafe_native_evaluation", completed_iterations=len(history),
                  stop_on_unsafe=stop_on_unsafe,
                  native_evaluations=len(evaluations), evaluations=evaluations,
                  new_native_evaluations=sum(not row.get("reused", False) for row in evaluations),
                  claim_boundary="Unsafe or backlogged loss evaluations are diagnostic. Only a complete healthy selected native evaluation with all 130 exact fields can be accepted. Station values are derived, not independent observations.")
    write_json_atomic(out / "result.json", result)
    write_json_atomic(out / "progress.json", {"stage": "completed", "completed_iterations": len(history),
                                               "stopping_reason": result["stopping_reason"], "formal_result": result["formal_result"]})
    lines = ["# Hamburg ordinary SPSA", "", f"Accepted exact calibration: {result['formal_result']}.",
             f"Stopping reason: {result['stopping_reason']}. Completed updates: {len(history)}.",
             "", "Counts use native E1 nVehContrib. All 130 fields and 40 derived station rows are retained.",
             "Unsafe results have diagnostic errors only. Fixed route/lane/time templates remain priors.",
             "", "[Settings](settings.json), [evaluations](evaluations.json), [result](result.json).",
             "", f"Runner: plugins/astra-sumo/scripts/run_hamburg_od_spsa.py. Scenario: {prepared}.",
             f"Analysis: plugins/astra-sumo/src/astra_sumo/core/hamburg_od_spsa.py. Raw results: {out}.",
             "", "| Evaluation | Diagnostic MAE | Max error | Collisions | Teleports | Waiting | In network | Healthy |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in evaluations:
        state = row["completion"]
        lines.append(f"| {row['name']} | {row['MAE']:.6f} | {row['max_abs_error']} | {state['collisions']} | "
                     f"{state['teleports']} | {state['waiting']} | {state['running']} | {row['healthy']} |")
    if initial_evaluation_dir is not None:
        lines += ["", f"Initial native evidence reused from: {Path(initial_evaluation_dir).resolve()}.",
                  "[Input and native correspondence](initial-reuse.json)."]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result
