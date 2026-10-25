"""Audit OD support on the frozen Hamburg corridor before demand estimation."""

from collections import Counter
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .demand_route_pool import boundary_route_pool
from .detector_demand import read_net


def audit_hamburg_od_definition(*, case_dir, output_dir):
    case, out = Path(case_dir).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or case.is_relative_to(out):
        raise ValueError("Use a new OD audit directory separate from its source.")
    out.mkdir(parents=True)
    for name in ("network.net.xml", "demand.rou.xml", "field-loops.add.xml"):
        path = case / "inputs" / name
        raw = path.read_bytes() if path.exists() else gzip.decompress(path.with_name(name + ".gz").read_bytes())
        (out / name).write_bytes(raw)
    network = ET.parse(out / "network.net.xml").getroot()
    edges, connections = read_net(out / "network.net.xml")
    active = {e for e in edges if edges[e].allows_passenger}
    graph = {e: connections.get(e, set()) & active for e in active}
    zones = json.loads((case / "boundaries.json").read_bytes())["zones"]
    sources = {e for z in zones for e in z["entry_edges"] if e in graph}
    sinks = {e for z in zones for e in z["exit_edges"] if e in graph}
    turns = {(r.get("from"), r.get("to")) for r in network.findall("connection") if r.get("dir") == "t"}
    forward = {e: {f for f in fs if (e, f) not in turns} for e, fs in graph.items()}
    incoming = {e: set() for e in graph}
    for e, fs in forward.items():
        for f in fs:
            incoming[f].add(e)
    # A terminal road remains a model port when its only continuation is a U-turn.
    sources |= {e for e in graph if not incoming[e]}
    sinks |= {e for e in graph if not forward[e]}
    lane_roads = {lane.get("id"): e.get("id") for e in network.findall("edge") for lane in e.findall("lane")}
    fields = {r.get("id"): lane_roads[r.get("lane")]
              for r in ET.parse(out / "field-loops.add.xml").getroot()}
    groups = {j.get("id"): j.get("id").split("_")[0] for j in network.findall("junction")
              if j.get("id", "").startswith(("LSA104_", "LSA118_", "LSA119_", "LSA200_", "LSA535_"))}
    pool, excluded = boundary_route_pool(edges, forward, sources, sinks, fields.values(), groups)
    ods = sorted({(r["source_edge"], r["sink_edge"]) for r in pool})
    coverage = {f: sum(e in r["edges"] for r in pool) for f, e in fields.items()}
    old, bad_entry, bad_exit = Counter(), Counter(), Counter()
    multi = 0
    for v in ET.parse(out / "demand.rou.xml").getroot().findall("vehicle"):
        path = v.find("route").get("edges").split()
        old[path[0], path[-1], int(float(v.get("depart")) // 900)] += 1
        if path[0] not in sources:
            bad_entry[path[0]] += 1
        if path[-1] not in sinks:
            bad_exit[path[-1]] += 1
        multi += len(set(fields.values()) & set(path)) > 1
    report = {"network_sha256": file_sha256(out / "network.net.xml"),
              "source_boundaries_sha256": file_sha256(case / "boundaries.json"),
              "declared_entries": sorted(sources), "declared_exits": sorted(sinks),
              "entry_count": len(sources), "exit_count": len(sinks), "candidate_routes": len(pool),
              "candidate_od_pairs": len(ods), "candidate_od_departure_cells_14_bins": 14 * len(ods),
              "excluded_tour_routes": len(excluded), "field_road_coverage": coverage,
              "old_requests": sum(old.values()), "old_od_departure_cells": len(old),
              "old_unique_od_pairs": len({(o, d) for o, d, h in old}),
              "old_nonboundary_entries": dict(sorted(bad_entry.items())),
              "old_nonboundary_exits": dict(sorted(bad_exit.items())),
              "old_routes_with_multiple_distinct_detector_roads": multi,
              "formal_result": False, "native_runs": 0,
              "claim_boundary": "Structural candidate OD support only. Scope cuts and terminal roads are model ports, not observed origins or destinations. Road coverage is not lane-level or native E1 coverage. No initial OD, route probabilities, or native calibration has been inferred."}
    write_json_atomic(out / "audit.json", report)
    write_json_atomic(out / "candidate-routes.json", {"routes": pool, "excluded": excluded})
    write_json_atomic(out / "od-support.json", {"pairs": [{"origin": o, "destination": d} for o, d in ods],
                      "departure_intervals": [{"begin": 900 * h, "end": 900 * (h + 1)} for h in range(14)],
                      "ordering": "departure interval first, then lexicographic (origin, destination)",
                      "initial_values": None, "route_probabilities": None})
    return report
