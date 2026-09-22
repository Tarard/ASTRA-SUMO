"""2022 synthetic layouts test reuse; none represents a surveyed junction."""

import json
import math
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest
from pyproj import Transformer

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.movement_routeability import run_candidate_movement_probes
from torii_sumo.road_network.continuous_lane_probes import run_continuous_lane_probes
from torii_sumo.road_network.continuous_lanes import reconstruct_continuous_lanes
from torii_sumo.road_network.engineering_topology import build_engineering_topology


pytestmark = pytest.mark.skipif(
    not shutil.which("sumo") or not shutil.which("netconvert"), reason="Native SUMO binaries are required."
)
EVIDENCE = "Synthetic 2022 test design, not actual traffic or manufacturer vehicle evidence."


def _case(tmp_path, layout, *, rotation=None, taper=False):
    configurations = {
        "orthogonal_four_way": (0, [[-100, 0], [-68, 0], [-34, 0], [0, 0]],
                                [("main", [95, 0]), ("north", [0, 90]), ("side", [0, -90])]),
        "oblique_t": (17, [[-100, -25], [-68, -12], [-34, -3], [0, 0]],
                      [("main", [80, 45]), ("side", [40, -85])]),
        "five_arm": (-23, [[-110, 0], [-74, 0], [-37, 0], [0, 0]],
                     [("main", [95, 0]), ("northeast", [45, 90]), ("northwest", [-35, 90]), ("side", [25, -95])]),
        "staggered_pair": (11, [[-100, -12], [-68, -6], [-34, -2], [0, 0]],
                           [("main", [65, 25]), ("side", [0, -90])]),
    }
    angle, local_axis, arms = configurations[layout]
    angle = math.radians(angle if rotation is None else rotation)

    def world(p):
        return [1000 + math.cos(angle) * p[0] - math.sin(angle) * p[1],
                1000 + math.sin(angle) * p[0] + math.cos(angle) * p[1]]

    def displaced(start, end, offset):
        dx, dy = end[0] - start[0], end[1] - start[1]
        size = math.hypot(dx, dy)
        return [[p[0] - offset * dy / size, p[1] + offset * dx / size] for p in (start, end)]

    axis = [world(p) for p in local_axis]
    incoming_unit = [(axis[1][i] - axis[0][i]) / math.dist(axis[0], axis[1]) for i in (0, 1)]
    prefix_start = [axis[0][i] - 40 * incoming_unit[i] for i in (0, 1)]
    nodes = []
    edges = []
    connections = []
    positions = {}

    def node(identity, point, kind="dead_end"):
        positions[identity] = point
        row = dict(id=identity, x=point[0], y=point[1], type=kind, evidence=EVIDENCE)
        if identity == "A":
            # A finite synthetic interface lets native FCD sample its two explicit bindings.
            row["shape"] = [[point[0] + along * incoming_unit[0] - left * incoming_unit[1],
                             point[1] + along * incoming_unit[1] + left * incoming_unit[0]]
                            for along, left in [(-2, -9), (2, -9), (2, 1), (-2, 1)]]
        nodes.append(row)

    def edge(identity, start, end, *, cycle=False, prefix=False):
        specifications = [("bicycle", 1.6, -7.2 if prefix else -4.0), ("passenger", 3.2, -1.6)] if cycle else [("passenger", 3.2, -1.6)]
        lanes = [dict(width_m=width, allow=[mode], shape=displaced(positions[start], positions[end], offset), evidence=EVIDENCE)
                 for mode, width, offset in specifications]
        edges.append(dict(id=identity, **{"from": start, "to": end}, lanes=lanes, speed_m_s=8, evidence=EVIDENCE))

    def connect(start, end, from_lane=0, to_lane=0, **keys):
        connections.append(dict(**{"from": start, "to": end}, fromLane=from_lane, toLane=to_lane, evidence=EVIDENCE, **keys))

    node("P", prefix_start)
    node("A", axis[0], "priority")
    node("J0", axis[-1], "traffic_light")
    edge("pre", "P", "A", cycle=True, prefix=True)
    edge("west_out", "J0", "P")
    for name, point in arms:
        node(name, world(point), "traffic_light" if layout == "staggered_pair" and name == "main" else "dead_end")
        edge(name + "_out", "J0", name, cycle=name == "main")
        edge(name + "_in", name, "J0")
    connect("pre", "approach", 0, toLaneKey="cycle")
    connect("pre", "approach", 1, toLaneKey="through")
    connect("approach", "main_out", to_lane=0, fromLaneKey="cycle")
    connect("approach", "main_out", to_lane=1, fromLaneKey="through")
    connect("approach", "side_out", fromLaneKey="pocket")
    for name, _ in arms:
        connect(name + "_in", "west_out")
        for other, _ in arms:
            if name != other:
                connect(name + "_in", other + "_out", to_lane=1 if other == "main" else 0)
    if layout == "staggered_pair":
        for name, point in [("far_main", [150, 25]), ("far_side", [65, 115])]:
            node(name, world(point))
            edge(name + "_out", "main", name, cycle=name == "far_main")
            edge(name + "_in", name, "main")
        connect("main_out", "far_main_out", 0, 0)
        connect("main_out", "far_main_out", 1, 1)
        connect("main_out", "far_side_out", 1, 0)
        connect("far_main_in", "main_in")
        connect("far_main_in", "far_side_out")
        connect("far_side_in", "main_in")
        connect("far_side_in", "far_main_out", to_lane=1)

    def section_lanes(keys):
        offsets = {"cycle": -7.2, "pocket": -4.8, "through": -1.6}
        return [dict(key=key, width_m=1.6 if key == "cycle" else 3.2,
                     allow=["bicycle"] if key == "cycle" else ["passenger"],
                     lateral_offset_m=offsets[key], evidence=EVIDENCE) for key in keys]

    run = dict(id="approach", **{"from": "A", "to": "J0"}, source_way_ids=["10", "11", "12"],
               axis_shape=axis, evidence=EVIDENCE, speed_m_s=8,
               sections=[dict(start_m=0, end_m=60, lanes=section_lanes(["cycle", "through"]), evidence=EVIDENCE),
                         dict(start_m=60, end_m=None, lanes=section_lanes(["cycle", "pocket", "through"]), evidence=EVIDENCE)])
    if taper:
        run["tapers"] = [dict(start_m=45, end_m=60, evidence=EVIDENCE)]
    data = dict(schema="torii.engineering-topology/v1", source_plan_sha256="1" * 64, crs="EPSG:3857",
                scenario=dict(kind="synthetic_test", target_year=2022), nodes=nodes, edges=edges,
                connections=connections, road_runs=[run])
    to_geo = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    osm = ET.Element("osm", version="0.6")
    for i, point in enumerate(axis, 1):
        lon, lat = to_geo.transform(*point)
        ET.SubElement(osm, "node", id=str(i), lon=str(lon), lat=str(lat))
    for i in range(3):
        way = ET.SubElement(osm, "way", id=str(10 + i))
        ET.SubElement(way, "nd", ref=str(i + 1))
        ET.SubElement(way, "nd", ref=str(i + 2))
        for key, value in {"highway": "secondary", "oneway": "yes", "lanes": "2", "maxspeed": "30"}.items():
            ET.SubElement(way, "tag", k=key, v=value)
    osm_path = tmp_path / "synthetic.osm.xml"
    ET.ElementTree(osm).write(osm_path, encoding="utf-8", xml_declaration=True)
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data, dict(path=str(osm_path), sha256=file_sha256(osm_path), data_year=2022)


def _build(tmp_path, layout, **options):
    path, original, osm = _case(tmp_path, layout, **options)
    before = file_sha256(path), file_sha256(Path(osm["path"]))
    continuity = reconstruct_continuous_lanes(topology_file=path, source_osm=osm, output_dir=tmp_path / "continuity", target_year=2022)
    assert continuity["status"] == "pass", continuity["unresolved"]
    normalized = json.loads(Path(continuity["topology"]["path"]).read_text(encoding="utf-8"))
    built = build_engineering_topology(topology_file=continuity["topology"]["path"], source_osm=osm,
                                      output_dir=tmp_path / "compiled", target_year=2022)
    assert built["status"] == "pass", built["construction_checks"]
    assert (file_sha256(path), file_sha256(Path(osm["path"]))) == before
    return original, continuity, normalized, built


@pytest.mark.parametrize("layout", ["orthogonal_four_way", "oblique_t", "five_arm", "staggered_pair"])
def test_continuous_lane_keys_bind_real_junction_turns_and_native_vehicles_finish(tmp_path, layout):
    original, continuity, normalized, built = _build(tmp_path, layout, taper=True)
    run = continuity["runs"][0]
    assert run["source_way_ids"] == ["10", "11", "12"]
    assert run["generated_edge_count"] == 2  # Three equal source fragments do not become three junctions.
    assert run["segments"][0]["lane_keys"] == ["cycle", "through"]
    assert run["segments"][-1]["lane_keys"] == ["cycle", "pocket", "through"]
    first, last = run["segments"][0]["edge_id"], run["segments"][-1]["edge_id"]
    transitions = {(c["from_key"], c["to_key"], c["fromLane"], c["toLane"]) for c in run["lane_transitions"]}
    assert transitions == {("cycle", "cycle", 0, 0), ("through", "through", 1, 2)}
    assert all(c["endpoint_gap_m"] < 1e-6 for c in run["lane_transitions"])
    expected = set()
    for connection in original["connections"]:
        start, end = connection["from"], connection["to"]
        from_lane, to_lane = connection["fromLane"], connection["toLane"]
        if start == "approach":
            start = last
            from_lane = ["cycle", "pocket", "through"].index(connection["fromLaneKey"])
        if end == "approach":
            end = first
            to_lane = ["cycle", "through"].index(connection["toLaneKey"])
        expected.add((start, from_lane, end, to_lane))
    expected |= {(first, 0, last, 0), (first, 1, last, 2)}
    declared = {(c["from"], c["fromLane"], c["to"], c["toLane"]) for c in normalized["connections"]}
    assert declared == expected
    assert not any("fromLaneKey" in c or "toLaneKey" in c for c in normalized["connections"])
    network = Path(built["artifacts"]["network"]["path"])
    native = ET.parse(network).getroot()
    actual = {(c.get("from"), int(c.get("fromLane")), c.get("to"), int(c.get("toLane")))
              for c in native.findall("connection") if not c.get("from", "").startswith(":")}
    assert actual == expected  # No inferred U-turn, branch fanout, or direct entry into the new pocket.
    assert not built["missing_connections"] and not built["extra_connections"]
    if layout == "staggered_pair":
        assert native.find("junction[@id='J0']") is not None
        assert native.find("junction[@id='main']") is not None
        assert native.find("edge[@id='main_out']").get("to") == "main"
    paths = run_continuous_lane_probes(network_file=network, continuity_file=continuity["report_file"], output_dir=tmp_path / "lane-probes")
    assert paths["lane_paths_passed"] == paths["lane_paths_total"] == 3, paths["path_probes"]
    assert {row["vehicle_class"] for row in paths["path_probes"]} == {"passenger", "bicycle"}
    assert paths["entry_probes"][0]["compatible_carrier_count"] == 1
    assert paths["entry_probes"][0]["status"] == "pass", paths["entry_probes"]
    assert paths["entry_probes"][0]["native_entry_lane_changes"]
    turns = run_candidate_movement_probes(candidate_manifest=tmp_path / "compiled" / "manifest.json",
                                          output_dir=tmp_path / "turn-probes", junction_movements_only=True)
    assert turns["status"] == "pass", turns["results"]
    assert turns["passed_exact_lane_transition"] == turns["declared_total"] == len(original["connections"])
    for result in [*paths["path_probes"], *turns["results"]]:
        assert result["completed_without_collision_or_teleport"]
        assert result["summary"]["arrived"] == "1"
        assert result["summary"]["collisions"] == result["summary"]["teleports"] == "0"


@pytest.mark.parametrize("rotation", [0, 31])
def test_declared_taper_spans_upstream_road_length_in_native_junction_shape(tmp_path, rotation):
    original, continuity, normalized, built = _build(tmp_path, "orthogonal_four_way", rotation=rotation, taper=True)
    run = continuity["runs"][0]
    assert run["tapers"][0]["variable_lane_width_modelled"] is False
    assert run["tapers"][0]["new_lane_available_from_m"] == 60
    assert run["tapers"][0]["new_lane_keys"] == ["pocket"]
    upstream = next(row for row in run["segments"] if row["end_m"] == pytest.approx(60))
    node_id = next(edge["to"] for edge in normalized["edges"] if edge["id"] == upstream["edge_id"])
    native = ET.parse(built["artifacts"]["network"]["path"]).getroot()
    offset = [float(value) for value in native.find("location").get("netOffset").split(",")]
    points = [[float(value) - offset[i] for i, value in enumerate(pair.split(","))]
              for pair in native.find(f"junction[@id='{node_id}']").get("shape").split()]
    axis = original["road_runs"][0]["axis_shape"]
    length = math.dist(axis[0], axis[-1])
    unit = [(axis[-1][i] - axis[0][i]) / length for i in (0, 1)]
    projected = [sum((point[i] - axis[0][i]) * unit[i] for i in (0, 1)) for point in points]
    assert min(projected) <= 45.1
    assert max(projected) >= 59.9
    assert max(projected) - min(projected) >= 14.8, "A tiny section node is not the declared 15 m road taper."
