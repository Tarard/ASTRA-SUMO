"""Attribute declared indirect demand routes to saved MAP and OSM-derived movement evidence."""

from collections import Counter, defaultdict
import gzip
from itertools import product
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .detector_demand import write_csv
from .source_movement_support import _index, _source_paths, _source_lane_change_path


def _read(path):
    path = Path(path)
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes())


def lane_road_hypotheses(binding, role, lane):
    side = binding[role]
    roads = {side["bindings"][lane][0]} if lane in side["bindings"] else set()
    for row in side["records"]:
        if row["lane_id"] == lane:
            roads.update(candidate["edge"] for candidate in row.get("candidates", []))
    return roads


def attribute_indirect_routes(*, demand_package, candidate_manifest, original_network, output_dir):
    package, manifest_file, source_file = [Path(p).resolve(strict=True)
                                          for p in (demand_package, candidate_manifest, original_network)]
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new attribution directory.")
    output.mkdir(parents=True)
    pool = _read(package / "route-pool.json.gz")["routes"]
    profiles = _read(package / "ideal-lane-profiles.json.gz")["profiles"]
    vehicles = _read(package / "provisional-lane-intentions.json.gz")["vehicles"]
    audit = _read(manifest_file)["official_connection_audit"]
    groups = _read(package / "inputs/junction-groups.json")["groups"]
    bindings = {(r["node_id"], r["intersection_part"]): r for r in audit["lane_bindings"]}
    members = defaultdict(set)
    for group in groups:
        members[f"LSA{group['node_id']}"].update(group["source_node_ids"])
    original_root = ET.parse(source_file).getroot()
    original = _index(original_root)
    original_nodes = {n.get("id") for n in original_root.findall("junction")}
    selected = Counter(profiles[v["profile_index"]]["route_id"] for v in vehicles)
    official = []
    for unresolved, rows in ((True, audit["ambiguous"]), (False, audit["required"])):
        for row in rows:
            binding = bindings[row["node_id"], row["intersection_part"]]
            official.append({**row, "unresolved": unresolved,
                "inlet_roads": lane_road_hypotheses(binding, "ingress", row["ingress_lane_id"]),
                "outlet_roads": lane_road_hypotheses(binding, "egress", row["egress_lane_id"])})
    cache = {}

    def classify(repeat):
        key = repeat["junction"], repeat["first_inlet"], repeat["last_outlet"]
        if key in cache:
            return cache[key]
        node, first, last = key
        candidates = [r for r in official if f"LSA{r['node_id']}" == node
                      and first in r["inlet_roads"] and last in r["outlet_roads"]]
        missing = [r for r in candidates if r["unresolved"]
                   and (r.get("allowed_vehicle_classes") is None or "passenger" in r["allowed_vehicle_classes"])]
        unknown = [r for r in official if f"LSA{r['node_id']}" == node and r["unresolved"]
                   and ((first in r["inlet_roads"] and not r["outlet_roads"])
                        or (last in r["outlet_roads"] and not r["inlet_roads"]))]
        scope = members.get(node, {node} if node in original_nodes else set())
        source_path = None
        proposed_change = None
        ports_known = first in original["edges"] and last in original["edges"]
        if scope and ports_known:
            starts = [identity for (edge, lane), identity in original["keys"].items()
                      if edge == first and "passenger" in original["lanes"][identity][2]]
            targets = [identity for (edge, lane), identity in original["keys"].items()
                       if edge == last and "passenger" in original["lanes"][identity][2]]
            for start, target in product(starts, targets):
                paths = _source_paths(original, start, target, scope, {"passenger"})
                if paths:
                    source_path = paths[0]["lane_ids"]
                    break
                if proposed_change is None:
                    proposed_change = _source_lane_change_path(original, start, target, scope, "passenger")
        if missing:
            category, reason = "unresolved_official", "Matches saved unresolved passenger-compatible MAP lane hypotheses."
        elif candidates:
            category, reason = "other", "An official declaration exists, but is resolved or not passenger-compatible."
        elif unknown:
            category, reason = "other", "An unresolved official correspondence has an unknown opposite port."
        elif source_path:
            category, reason = "other", "The OSM-derived source has a bounded fixed-lane passenger path."
        elif proposed_change:
            category, reason = "other", "A bounded source path requires unverified interior lane changes."
        elif not ports_known or not scope:
            category, reason = "other", "The original source scope or boundary ports cannot be identified."
        else:
            category, reason = "absent_in_examined_sources", "No matching MAP declaration or bounded passenger path in the examined OSM-derived source."
        result = {"junction": node, "first_inlet": first, "last_outlet": last, "category": category, "reason": reason,
            "official_matches": [{k: r.get(k) for k in ("node_id", "movement_id", "intersection_part", "unresolved", "allowed_vehicle_classes")}
                                 for r in candidates],
            "unresolved_official_ids": [f"{r['node_id']}:{r['movement_id']}" for r in missing],
            "source_fixed_lane_path": source_path, "source_lane_change_candidate": proposed_change,
            "field_turn_prohibition_established": False}
        cache[key] = result
        return result

    event_rows, route_rows, by_route = [], [], {}
    for route in pool:
        if not route["junction_audit"]["repeats"]:
            continue
        results = [classify(repeat) for repeat in route["junction_audit"]["repeats"]]
        by_route[route["route_id"]] = results
        classes = sorted({r["category"] for r in results})
        route_rows.append({"route_id": route["route_id"], "selected_vehicles": selected[route["route_id"]],
            "attribution_events": len(results), "classes": "|".join(classes),
            "unresolved_official_ids": "|".join(sorted({x for r in results for x in r["unresolved_official_ids"]}))})
        for result in results:
            event_rows.append({"route_id": route["route_id"], "selected_vehicles": selected[route["route_id"]],
                **{k: result[k] for k in ("junction", "first_inlet", "last_outlet", "category", "reason")},
                "unresolved_official_ids": "|".join(result["unresolved_official_ids"])})
    vehicle_rows = []
    for vehicle in vehicles:
        route_id = profiles[vehicle["profile_index"]]["route_id"]
        if route_id not in by_route:
            continue
        results = by_route[route_id]
        vehicle_rows.append({"vehicle_id": vehicle["vehicle_id"], "route_id": route_id,
            "classes": "|".join(sorted({r["category"] for r in results})),
            "unresolved_official_ids": "|".join(sorted({x for r in results for x in r["unresolved_official_ids"]}))})
    for name, rows in (("attribution-events.csv", event_rows), ("flagged-routes.csv", route_rows),
                       ("selected-indirect-vehicles.csv", vehicle_rows)):
        write_csv(output / name, rows, list(rows[0]))
    write_json_atomic(output / "unique-movement-attributions.json", {"rows": list(cache.values())})
    report = {"schema": "astra.indirect-route-attribution/v1", "status": "measured",
        "flagged_routes": len(route_rows), "selected_indirect_vehicles": len(vehicle_rows),
        "events": len(event_rows), "unique_spliced_movements": len(cache),
        "event_classes": dict(Counter(r["category"] for r in event_rows)),
        "route_class_memberships": dict(Counter(category for r in route_rows for category in r["classes"].split("|"))),
        "unresolved_official_route_memberships": dict(Counter(movement for r in route_rows
            for movement in r["unresolved_official_ids"].split("|") if movement)),
        "selected_vehicle_class_memberships": {category: sum(category in r["classes"].split("|") for r in vehicle_rows)
                                               for category in sorted({r["category"] for r in event_rows})},
        "unresolved_official_vehicle_memberships": dict(Counter(movement for r in vehicle_rows
            for movement in r["unresolved_official_ids"].split("|") if movement)),
        "sources": [{"path": str(p), "sha256": file_sha256(p)} for p in (manifest_file, source_file)],
        "claim_boundary": "Source-path and lane-correspondence attribution only. Multiple event classes can belong to one vehicle. Absence in incomplete model sources does not prove a field turn prohibition."}
    write_json_atomic(output / "report.json", report)
    return report
