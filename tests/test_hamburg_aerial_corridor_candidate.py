from __future__ import annotations

import json
import hashlib
import math
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo import cli
from astra_sumo.core import hamburg_aerial_corridor_candidate as candidate_module
from astra_sumo.core.hamburg_junctions import movements as movement_module
from astra_sumo.core.hamburg_aerial_corridor_candidate import (
    _bind_official_lanes,
    _load_movement_plans,
    _project_plan_to_network,
    _read_request,
    _write_movement_candidate,
    fit_movement_shape_to_anchors,
    movement_surface_polygon,
    reanchor_movement_shape,
)
from astra_sumo.core.connection_mode_audit import audit_network_connection_mode


@pytest.mark.parametrize(
    ("status", "rows", "message"),
    [
        ("blocked", [{"node_id": "1", "tls_ids": ["J"]}], "passing cluster binding"),
        ("pass", [{"node_id": "1", "tls_ids": ["J"]}, {"node_id": "1", "tls_ids": ["K"]}], "duplicate node"),
        ("pass", [{"node_id": "1", "tls_ids": ["J"]}, {"node_id": "2", "tls_ids": ["J"]}], "source TLS.*multiple planned"),
        ("pass", [{"node_id": "1", "cluster_id": "same", "tls_ids": ["J"]}, {"node_id": "2", "cluster_id": "same", "tls_ids": ["K"]}], "source cluster.*multiple planned"),
    ],
)
def test_rejects_failed_or_ambiguous_cluster_binding_before_netconvert(tmp_path, monkeypatch, status, rows, message) -> None:
    def artifact(path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def unexpected_command(*args, **kwargs):
        raise AssertionError("netconvert must not run for an invalid binding")

    monkeypatch.setattr(candidate_module, "run_command", unexpected_command)
    network = tmp_path / "source.net.xml"
    network.write_text('<net><location netOffset="0,0" projParameter="+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs"/><junction id="J" x="0" y="0"/><junction id="K" x="100" y="0"/></net>', encoding="utf-8")
    plan_rows = []
    for node_id in sorted({row["node_id"] for row in rows}):
        plan = tmp_path / f"plan-{node_id}.json"
        plan.write_text(json.dumps({"schema": "torii.hamburg-aerial-movement-plan/v1", "node_id": node_id, "crs": "EPSG:25832", "lanes": [{"lane_id": "in", "lane_type": "vehicle", "shape_epsg25832": [[-20, 0], [0, 0]]}], "movements": [{"movement_id": "1", "intersection_part": "0", "ingress_lane_id": "in", "egress_lane_id": "out", "selected_shape_epsg25832": [[-10, 0], [10, 0]]}]}), encoding="utf-8")
        plan_rows.append({"node_id": node_id, "plan_file": str(plan), "plan_sha256": artifact(plan)["sha256"]})
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"schema": "torii.hamburg-aerial-corridor-plan/v1", "intersections": plan_rows}), encoding="utf-8")
    binding = tmp_path / "binding.json"
    binding.write_text(json.dumps({"schema": "torii.hamburg-five-corridor-tls-cluster-binding/v1", "status": status, "bindings": rows, "inputs": {"network": artifact(network)}}), encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema": "torii.hamburg-aerial-corridor-candidate-request/v1", "source_net": artifact(network), "cluster_binding": artifact(binding), "movement_summary": artifact(summary)}), encoding="utf-8")
    output = tmp_path / "candidate"
    with pytest.raises(ValueError, match=message):
        candidate_module.build_hamburg_aerial_combined_candidate(request_file=request, output_dir=output)
    assert not output.exists()


def test_lane_binding_keeps_identity_across_movements_and_rejects_ties() -> None:
    lanes = [
        {"lane_id": "one", "lane_type": "vehicle", "shape_network": [(-20, 0), (0, 0)]},
        {"lane_id": "two", "lane_type": "vehicle", "shape_network": [(-20, 3.2), (0, 3.2)]},
    ]
    movements = [
        {"ingress_lane_id": "one", "egress_lane_id": "out", "selected_shape_network": [(-10, 0), (10, 0)]},
        {"ingress_lane_id": "two", "egress_lane_id": "out", "selected_shape_network": [(-10, 3.2), (10, 3.2)]},
    ]
    candidates = {("in", 0): [(-20, 0), (0, 0)], ("in", 1): [(-20, 3.2), (0, 3.2)]}
    result = _bind_official_lanes(lanes, movements, candidates, role="ingress", max_error_m=10, margin_m=0.5)
    assert result["bindings"] == {"one": ("in", 0), "two": ("in", 1)}
    candidates[("duplicate", 0)] = candidates[("in", 0)]
    result = _bind_official_lanes(lanes, movements, candidates, role="ingress", max_error_m=10, margin_m=0.5)
    assert "one" not in result["bindings"]
    assert result["ambiguous"]


def test_lane_binding_compares_shared_coverage_not_unmapped_road_tail() -> None:
    lanes = [{"lane_id": "one", "lane_type": "vehicle", "shape_network": [(-10, 0), (0, 0)]}]
    movements = [{"ingress_lane_id": "one", "egress_lane_id": "out", "selected_shape_network": [(-10, 0), (10, 0)]}]
    candidates = {("in", 0): [(-100, 0), (0, 0)], ("in", 1): [(-100, 3.2), (0, 3.2)]}
    result = _bind_official_lanes(lanes, movements, candidates, role="ingress", max_error_m=10, margin_m=0.5)
    assert result["bindings"] == {"one": ("in", 0)}
    assert result["records"][0]["candidates"][0]["mean_error_m"] == 0


def test_plan_loading_checks_transitive_hash_before_using_geometry(tmp_path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text('{"node_id":"999","movements":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        _load_movement_plans({"intersections": [{"node_id": "999", "plan_file": str(plan), "plan_sha256": "0" * 64}]})


def test_candidate_request_defaults_and_relative_paths(tmp_path) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema": "torii.hamburg-aerial-corridor-candidate-request/v1", **{key: {"path": "local.json", "sha256": "0" * 64} for key in ("source_net", "cluster_binding", "movement_summary")}}), encoding="utf-8")
    result = _read_request(request)
    assert result["maximum_anchor_projection_error_m"] == 10
    assert result["maximum_lane_projection_error_m"] == 10
    assert result["minimum_lane_match_margin_m"] == 0.5
    assert result["source_net"]["path"] == str(tmp_path / "local.json")


@pytest.mark.parametrize("joins", [[], {"joined": "a b"}, {"joined": ["a"]}, {"joined": ["a", "a"]}, {"first": ["a", "b"], "second": ["b", "c"]}])
def test_context_join_request_rejects_ambiguous_member_groups(tmp_path, joins):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema": candidate_module.REQUEST_SCHEMA, **{key: {"path": "local.json", "sha256": "0" * 64} for key in ("source_net", "cluster_binding", "movement_summary")}, "context_joins": joins}), encoding="utf-8")
    with pytest.raises(ValueError, match="context_joins"):
        _read_request(request)


def test_constructed_lane_identity_keeps_all_approaches_of_one_junction():
    root = ET.fromstring('<net><edge id="west"><lane id="west_0"/></edge><edge id="north"><lane id="north_0"/></edge></net>')
    sections = [{"node_id": "119", "intersection_part": "0", "downstream_edge_id": edge, "source_edge_id": edge, "source_lane_to_downstream_lane": {"0": 0}, "pocket_lane_ids": [], "official_lane_indices": {lane: 0}, "original_to": "joined"} for edge, lane in (("west", "1"), ("north", "3"))]
    groups = [{"node_id": "119", "intersection_part": "0", "join_id": "joined"}]
    identities, origins, _ = candidate_module._approach_lane_identities(root, {"approaches": sections}, groups)
    assert identities["119", "0"] == {"1": ("west", 0), "3": ("north", 0)}
    assert origins == {"west_0": "west_0", "north_0": "north_0"}
    sections[1]["official_lane_indices"] = {"1": 0}
    with pytest.raises(ValueError, match="multiple rebuilt"):
        candidate_module._approach_lane_identities(root, {"approaches": sections}, groups)


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries required")
def test_context_preparation_keeps_source_and_restores_boundary_paths(tmp_path):
    nodes, edges, source = (tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml"))
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="a" x="50" y="0"/><node id="b" x="70" y="0"/><node id="e" x="120" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="a"/><edge id="middle" from="a" to="b"/><edge id="out" from="b" to="e"/></edges>', encoding="utf-8")
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(source)], check=True, capture_output=True, timeout=30)
    before = source.read_bytes()
    options = dict(source_net=source, context_groups={"context": ["a", "b"]}, official_groups=[], netconvert_binary="netconvert", sumo_binary="sumo", timeout_seconds=30, seed=104)
    result = candidate_module._prepare_context_joins(output_dir=tmp_path / "context", **options)
    assert result["status"] == "pass"
    assert result["boundary_after"]["expected_count"] == 1
    assert result["boundary_after"]["missing"] == []
    assert source.read_bytes() == before
    options["official_groups"] = [{"source_node_ids": ["a"]}]
    with pytest.raises(ValueError, match="overlap official"):
        candidate_module._prepare_context_joins(output_dir=tmp_path / "overlap", **options)


def test_plan_coordinates_follow_network_projection_and_offset() -> None:
    root = ET.fromstring('<net><location netOffset="100,200" projParameter="+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs"/></net>')
    plan = {"crs": "EPSG:25832", "lanes": [{"shape_epsg25832": [[500000, 5900000], [500010, 5900000]]}], "movements": [{"selected_shape_epsg25832": [[500000, 5900000], [500010, 5900000]]}]}
    result = _project_plan_to_network(plan, root)
    assert result["movements"][0]["selected_shape_network"][0] == pytest.approx((500100, 5900200))
    assert result["lanes"][0]["shape_network"][1] == pytest.approx((500110, 5900200))
    with pytest.raises(ValueError, match="projection"):
        _project_plan_to_network(plan, ET.fromstring('<net><location netOffset="0,0" projParameter="!"/></net>'))


def test_official_curve_coordinates_are_projected_without_overwriting_aerial_selection():
    root = ET.fromstring('<net><location netOffset="100,200" projParameter="+proj=utm +zone=32 +ellps=GRS80 +units=m +no_defs"/></net>')
    plan = {"crs": "EPSG:25832", "lanes": [], "movements": [{
        "selected_shape_epsg25832": [[500000, 5900006], [500010, 5900006]],
        "official_shape_epsg25832": [[500000, 5900001], [500005, 5900002], [500010, 5900001]],
    }]}
    result = _project_plan_to_network(plan, root)
    row = result["movements"][0]
    assert row["selected_shape_network"][0] == pytest.approx((500100, 5900206))
    assert row["official_shape_network"][1] == pytest.approx((500105, 5900202))
    assert "official_shape_network" not in plan["movements"][0]


@pytest.mark.parametrize("selected_y, official_y, expected_source", [
    (6, 1, "official_map_curve"), (6, 7, "netconvert"), (0, 7, "selected_curve"),
])
def test_failed_aerial_fit_tries_official_curve_at_the_same_anchors_and_limit(
    tmp_path, monkeypatch, selected_y, official_y, expected_source,
):
    from types import SimpleNamespace
    source = tmp_path / "source.net.xml"
    source.write_text('''<net>
      <edge id="in" from="w" to="J"><lane id="in_0" index="0" length="20" shape="-20,0 0,0"/></edge>
      <edge id="out" from="J" to="e"><lane id="out_0" index="0" length="20" shape="10,0 30,0"/></edge>
      <edge id="remote-taper" from="A" to="B"><lane id="remote-taper_0" index="0" length="4" shape="100,0 104,0"/></edge>
      <junction id="J" type="priority" customShape="true" shape="0,-3 10,-3 10,3 0,3"/>
      <connection from="in" fromLane="0" to="out" toLane="0"/>
    </net>''', encoding="utf-8")
    before = source.read_bytes()
    output = tmp_path / "candidate.net.xml"
    def compile_copy(command, **kwargs):
        shutil.copy2(source, output)
        changed = ET.parse(output)
        remote = changed.getroot().find("edge[@id='remote-taper']/lane")
        remote.set("shape", "76,0 104,0")
        remote.set("length", "28")
        changed.write(output, encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(movement_module, "run_command", compile_copy)
    monkeypatch.setattr(movement_module, "audit_source_movement_support", lambda *args, **kwargs: {
        "unresolved": [], "unsupported_redundant_pairs": [], "source_backed_extra": []})
    selected = [(0, selected_y), (10, selected_y)]
    official = [(0, official_y), (5, official_y + 1), (10, official_y)]
    movement = {"movement_id": "1", "intersection_part": "0", "ingress_lane_id": "in", "egress_lane_id": "out",
                "selected_source": "aerial_trace", "selected_shape_network": selected, "official_shape_network": official}
    lanes = [{"lane_id": "in", "lane_type": "vehicle", "shape_network": [(-20, 0), (0, 0)]},
             {"lane_id": "out", "lane_type": "vehicle", "shape_network": [(10, 0), (30, 0)]}]
    report = _write_movement_candidate(joined_net=source, plans={"1": {"lanes": lanes, "movements": [movement]}},
        groups=[{"node_id": "1", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"]}],
        skipped_join_ids=set(), output_file=output, maximum_anchor_projection_error_m=10)
    row = report["movements"][0]
    assert row["selected_source"] == "aerial_trace"
    assert row["geometry_source"] == expected_source
    assert bool(report["official_connection_audit"]["geometry_rejected"]) is (expected_source == "netconvert")
    if selected_y:
        assert row["selected_curve_anchor_projection_error_sum_m"] == pytest.approx(12)
        assert row["official_curve_anchor_projection_error_sum_m"] == pytest.approx(official_y * 2)
    connection = ET.parse(output.with_suffix(".con.xml")).getroot().find("connection")
    if expected_source == "netconvert":
        assert connection.get("shape") is None
    else:
        curve = candidate_module._parse_shape(connection.get("shape"))
        expected, error = fit_movement_shape_to_anchors(official if expected_source == "official_map_curve" else selected,
                                                       start=(0, 0), end=(10, 0))
        assert curve == expected
        assert curve[0] == (0, 0) and curve[-1] == (10, 0)
        assert row["anchor_projection_error_sum_m"] == pytest.approx(error)
    assert source.read_bytes() == before
    remote = ET.parse(output).getroot().find("edge[@id='remote-taper']/lane")
    assert remote.get("shape") == "100,0 104,0"
    assert remote.get("length") == "4"
    assert report["official_connection_audit"]["outside_road_restoration"]["status"] == "pass"
    assert movement["selected_shape_network"] == selected


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="netconvert is not installed")
@pytest.mark.parametrize("fixed_boundary", [False, True])
def test_materialization_rebuilds_connections_and_preserves_unmapped_branch(tmp_path, fixed_boundary) -> None:
    nodes = tmp_path / "nodes.xml"
    edges = tmp_path / "edges.xml"
    connections = tmp_path / "connections.xml"
    fixed_shape = ' shape="-10,-10 10,-10 10,10 -10,10"' if fixed_boundary else ""
    nodes.write_text(f'<nodes><node id="w" x="-80" y="0"/><node id="J" x="0" y="0" type="priority"{fixed_shape}/><node id="e" x="80" y="0"/><node id="n" x="0" y="80"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="J" numLanes="2"/><edge id="out" from="J" to="e" numLanes="2"/><edge id="branch" from="J" to="n" numLanes="1"/></edges>', encoding="utf-8")
    connections.write_text('<connections><connection from="in" to="out" fromLane="0" toLane="0"/><connection from="in" to="out" fromLane="0" toLane="1"/><connection from="in" to="out" fromLane="1" toLane="0"/><connection from="in" to="branch" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    source = tmp_path / "source.net.xml"
    result = subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(connections), "-o", str(source), "--offset.disable-normalization"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    source_root = ET.parse(source).getroot()
    def shape(lane):
        return [tuple(map(float, point.split(","))) for point in lane.get("shape").split()]
    lanes = {(edge.get("id"), index): shape(lane) for edge in source_root.findall("edge") if edge.get("function") != "internal" for index, lane in enumerate(edge.findall("lane"))}
    official_lanes = [{"lane_id": f"{edge}{index}", "lane_type": "vehicle", "shape_network": lanes[(edge, index)]} for edge in ("in", "out") for index in (0, 1)]
    movements = [{"movement_id": str(index), "intersection_part": "0", "ingress_lane_id": f"in{index}", "egress_lane_id": f"out{index}", "selected_source": "aerial_trace", "selected_shape_network": [lanes[("in", index)][-1], lanes[("out", index)][0]]} for index in (0, 1)]
    output = tmp_path / "candidate.net.xml"
    report = _write_movement_candidate(joined_net=source, plans={"999": {"lanes": official_lanes, "movements": movements}}, groups=[{"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"]}], skipped_join_ids=set(), output_file=output, maximum_anchor_projection_error_m=10)
    actual = {tuple(row) for row in report["official_connection_audit"]["actual"]}
    assert ("in", 0, "out", 1) not in actual
    assert {("in", 0, "out", 0), ("in", 1, "out", 1), ("in", 0, "branch", 0)} <= actual
    assert report["official_connection_audit"]["exterior_edges_preserved"] is True
    assert report["official_connection_audit"]["missing"] == []
    audit = audit_network_connection_mode(ET.parse(output).getroot())
    assert audit["structural_failure_count"] == 0, audit["finding_category_counts"]
    # The earlier boundary construction already established this lane's
    # identity. A later whole-lane distance tie must not erase that evidence.
    tied_lanes = [dict(row) for row in official_lanes]
    tied = next(row for row in tied_lanes if row["lane_id"] == "out0")
    tied["shape_network"] = [tuple((a[i] + b[i]) / 2 for i in (0, 1)) for a, b in zip(lanes["out", 0], lanes["out", 1])]
    group = {"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"],
             "boundary_port_classification": {"official_external_ports": [
                 {"lane_id": f"out{i}", "role": "egress", "source_lane_id": f"out_{i}"} for i in (0, 1)]}}
    kept = _write_movement_candidate(joined_net=source, plans={"999": {"lanes": tied_lanes, "movements": movements}}, groups=[group],
        skipped_join_ids=set(), output_file=tmp_path / "preserved-identity.net.xml", maximum_anchor_projection_error_m=10)
    assert kept["materialized_movement_count"] == 2
    egress = kept["official_connection_audit"]["lane_bindings"][0]["egress"]
    assert egress["bindings"]["out0"] == ("out", 0)
    assert next(row for row in egress["records"] if row["lane_id"] == "out0")["reason"] == "preserved_source_boundary_lane_identity"
    # Complete lane identities do not make MAP a complete turn-prohibition
    # list. Preserve an existing different-road movement absent from MAP.
    branch_lane = {"lane_id": "branch0", "lane_type": "vehicle", "shape_network": lanes["branch", 0]}
    branch_movement = {"movement_id": "branch", "intersection_part": "0", "ingress_lane_id": "in1", "egress_lane_id": "branch0",
        "selected_source": "official_map", "selected_shape_network": [lanes["in", 1][-1], lanes["branch", 0][0]]}
    partial_turns = _write_movement_candidate(joined_net=source, plans={"999": {"lanes": [*official_lanes, branch_lane], "movements": [*movements, branch_movement]}},
        groups=[{"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"]}], skipped_join_ids=set(),
        output_file=tmp_path / "partial-turn-list.net.xml", maximum_anchor_projection_error_m=10)
    assert partial_turns["parts"][0]["direct_official_scope_complete"]
    assert ("in", 0, "branch", 0) in {tuple(row) for row in partial_turns["official_connection_audit"]["actual"]}
    # An official bus-only target is not a replacement for the original
    # passenger connection to another lane on the same exit road.
    mode_root = ET.fromstring(ET.tostring(source_root))
    for index, mode in enumerate(("bus", "passenger")):
        mode_root.find(f"edge[@id='out']/lane[@index='{index}']").set("allow", mode)
    mode_source = tmp_path / "mode-source.net.xml"
    ET.ElementTree(mode_root).write(mode_source, encoding="utf-8", xml_declaration=True)
    mode_lanes = [{**row, **({"allowed_vehicle_classes": ["bus" if row["lane_id"] == "out0" else "passenger"]} if row["lane_id"].startswith("out") else {})} for row in official_lanes]
    modes_kept = _write_movement_candidate(joined_net=mode_source, plans={"999": {"lanes": mode_lanes, "movements": movements}},
        groups=[{"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"]}], skipped_join_ids=set(),
        output_file=tmp_path / "mode-preservation.net.xml", maximum_anchor_projection_error_m=10)
    assert ("in", 0, "out", 1) in {tuple(row) for row in modes_kept["official_connection_audit"]["actual"]}
    from astra_sumo.core.hamburg_aerial_approach import _preserved_edge
    built = ET.parse(output).getroot()
    if fixed_boundary:
        # A compiled custom boundary stays authoritative even when the group's
        # earlier official-boundary preview is absent.
        assert built.find("junction[@id='J']").get("shape") == source_root.find("junction[@id='J']").get("shape")
    # Native boundaries must also survive reload; a drawing-only replacement
    # must not overwrite the compiled network's actual lane-cut surface.
    reloaded = tmp_path / "boundary-reloaded.net.xml"
    subprocess.run(["netconvert", "-s", str(output), "-o", str(reloaded), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    later = ET.parse(reloaded).getroot()
    for field in ("shape", "customShape"):
        assert built.find("junction[@id='J']").get(field) == later.find("junction[@id='J']").get(field)
    for edge in built.findall("edge"):
        if edge.get("function") != "internal":
            assert _preserved_edge(edge, later.find(f"edge[@id='{edge.get('id')}']"))
    assert {candidate_module._connection_key(row) for row in built.findall("connection")} == {
        candidate_module._connection_key(row) for row in later.findall("connection")}
    movements[1]["selected_shape_network"] = [(x, y + 100) for x, y in movements[1]["selected_shape_network"]]
    fallback = _write_movement_candidate(joined_net=source, plans={"999": {"lanes": official_lanes, "movements": movements}}, groups=[{"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"]}], skipped_join_ids=set(), output_file=tmp_path / "fallback.net.xml", maximum_anchor_projection_error_m=10)
    assert ("in", 1, "out", 1) in {tuple(row) for row in fallback["official_connection_audit"]["actual"]}
    assert fallback["official_connection_audit"]["missing"] == []
    assert len(fallback["official_connection_audit"]["geometry_rejected"]) == 1

    # An official intermediate port may be internal to a merged junction.
    # A partial direct-movement list cannot forbid its original through path.
    partial = [
        {**movements[0], "egress_lane_id": "middle", "selected_shape_network": [lanes[("in", 0)][-1], (1.0, 0.0)]},
        {**movements[1], "egress_lane_id": "out0", "selected_shape_network": [lanes[("in", 1)][-1], lanes[("out", 0)][0]]},
    ]
    partial_lanes = [*official_lanes, {"lane_id": "middle", "lane_type": "vehicle", "shape_network": [(-1.0, 0.0), (1.0, 0.0)]}]
    incomplete = _write_movement_candidate(joined_net=source, plans={"999": {"lanes": partial_lanes, "movements": partial}}, groups=[{"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"]}], skipped_join_ids=set(), output_file=tmp_path / "partial.net.xml", maximum_anchor_projection_error_m=10)
    assert incomplete["official_connection_audit"]["ambiguous"]
    assert ("in", 0, "out", 0) in {tuple(row) for row in incomplete["official_connection_audit"]["actual"]}
    collinear = [dict(row) for row in partial_lanes]
    next(row for row in collinear if row["lane_id"] == "middle")["shape_network"] = lanes["out", 0]
    group = {"node_id": "999", "intersection_part": "0", "join_id": "J", "source_node_ids": ["J"],
             "boundary_port_classification": {"official_internal_anchors": [{"lane_id": "middle", "role": "egress"}]}}
    internal_anchor = _write_movement_candidate(joined_net=source, plans={"999": {"lanes": collinear, "movements": partial}}, groups=[group],
        skipped_join_ids=set(), output_file=tmp_path / "internal-anchor.net.xml", maximum_anchor_projection_error_m=10)
    egress = internal_anchor["official_connection_audit"]["lane_bindings"][0]["egress"]
    assert egress["bindings"].get("out0") == ("out", 0)
    assert "middle" not in egress["bindings"]
    assert next(row for row in egress["records"] if row["lane_id"] == "middle")["reason"] == "official_anchor_inside_join"


def test_movement_surface_polygon_wraps_crossing_movements_without_huge_box() -> None:
    polygon = movement_surface_polygon(
        [
            [(-10.0, 0.0), (10.0, 0.0)],
            [(0.0, -10.0), (0.0, 10.0)],
        ],
        lane_width_m=3.2,
        resolution_m=0.1,
    )

    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    assert len(polygon) >= 8
    assert min(xs) > -12 and max(xs) < 12
    assert min(ys) > -12 and max(ys) < 12
    assert _area(polygon) < 140


@pytest.mark.parametrize("mode", [None, "preserve", "guarded", "fused", True, "smooth_anything"])
def test_junction_contour_mode_is_explicit_and_validated(tmp_path, mode):
    request = tmp_path / "request.json"
    payload = {"schema": candidate_module.REQUEST_SCHEMA,
               **{key: {"path": "local.json", "sha256": "0" * 64}
                  for key in ("source_net", "cluster_binding", "movement_summary")}}
    if mode is not None:
        payload["junction_contours"] = mode
    request.write_text(json.dumps(payload), encoding="utf-8")
    if mode is True or mode == "smooth_anything":
        with pytest.raises(ValueError, match="junction_contours"):
            _read_request(request)
    else:
        assert _read_request(request)["junction_contours"] == (mode or "preserve")


def test_contour_scope_discovers_unlisted_road_junctions_without_counting_two_way_roads_twice():
    from astra_sumo.core.hamburg_junctions.movements import _contour_target_ids

    root = ET.fromstring('<net><junction id="MAP"/><junction id="unlisted" type="priority"/><junction id="through" type="traffic_light"/></net>')
    for center, peers in [('unlisted', ['west', 'east', 'north']), ('through', ['west', 'east'])]:
        for peer in peers:
            for start, end in [(center, peer), (peer, center)]:
                edge = ET.SubElement(root, 'edge', id=start + '-' + end, attrib={'from': start, 'to': end})
                ET.SubElement(edge, 'lane', allow='passenger')
    groups = [{'join_id': 'MAP'}]
    assert _contour_target_ids(root, groups, include_context=False) == ['MAP']
    assert _contour_target_ids(root, groups, include_context=True) == ['MAP', 'unlisted']


@pytest.mark.parametrize("failure", ["speed", "coverage", "lane_shift"])
@pytest.mark.parametrize("mode", ["guarded", "fused"])
@pytest.mark.parametrize("native_refit", [False, True])
def test_contour_build_keeps_verified_parts_and_rolls_back_failed_parts(tmp_path, monkeypatch, failure, mode, native_refit):
    import sys
    from types import SimpleNamespace

    source = tmp_path / "joined.net.xml"
    source.write_text('''<net>
      <edge id="in" from="w" to="J"><lane id="in_0" index="0" speed="10" length="20" shape="-30,0 -10,0"/></edge>
      <junction id="J" shape="-10,-10 10,-10 10,10 -10,10"/>
      <junction id="K" shape="40,-10 60,-10 60,10 40,10"/>
      <tlLogic id="J" type="static" programID="0" offset="0"><phase duration="30" state="G"/></tlLogic>
    </net>''', encoding="utf-8")
    output = tmp_path / "candidate.net.xml"
    shutil.copy2(source, output)
    baseline = source.read_bytes()
    patch = tmp_path / "fixed.con.xml"
    patch.write_text("<connections/>", encoding="utf-8")
    proposed = {"J": [(-10, -10), (10, -10), (10, 10)], "K": [(40, -10), (60, -10), (60, 10)]}
    module = SimpleNamespace(
        propose_junction_contour=lambda root, identifier, **kwargs: {
            "status": "pass", "changed": True, "proposed_shape": proposed[identifier], "reasons": [],
            "native_boundary_refit": native_refit},
        audit_junction_contour=lambda root, identifier, polygon: {
            "preservation_pass": not (failure == "coverage" and identifier == "K")},
    )
    module.propose_fused_junction_contour = module.propose_junction_contour
    monkeypatch.setitem(sys.modules, "astra_sumo.core.hamburg_junction_contour", module)
    commands = []

    def compile_from_original(command, **kwargs):
        commands.append(command)
        assert command[command.index("--sumo-net-file") + 1] == str(source)
        assert command[command.index("--connection-files") + 1] == str(patch)
        root = ET.parse(source).getroot()
        nodes = ET.parse(command[command.index("--node-files") + 1]).getroot()
        for node in nodes:
            target = root.find(f"junction[@id='{node.get('id')}']")
            target.set("shape", node.get("shape"))
            target.set("customShape", "1")
        if nodes.find("node[@id='K']") is not None and failure == "speed":
            root.find("edge/lane").set("speed", "9")
        if nodes.find("node[@id='K']") is not None and failure == "lane_shift":
            root.find("edge/lane").set("shape", "-30,0.05 -10,0.05")
        if native_refit:
            root.find("tlLogic/phase").set("duration", "5")
        ET.ElementTree(root).write(command[command.index("--output-file") + 1], encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(movement_module, "run_command", compile_from_original)
    root, report = candidate_module._compile_junction_contours(
        ET.parse(output).getroot(), groups=[{"join_id": "J"}, {"join_id": "K"}], output_file=output,
        command=["netconvert", "--sumo-net-file", str(source), "--connection-files", str(patch),
                 "--offset.disable-normalization", "true", "--output-file", str(output)], timeout_seconds=30, mode=mode)
    assert report["mode"] == mode
    assert ET.tostring(ET.parse(report["baseline_network"]["path"]).getroot()) == ET.tostring(ET.fromstring(baseline))
    restored = native_refit and failure != "coverage"
    assert report["accepted_junction_ids"] == (["J", "K"] if restored else ["J"])
    assert report["rejected_junction_ids"] == ([] if restored else ["K"])
    assert root.find("junction[@id='J']").get("customShape") == "1"
    assert root.find("junction[@id='K']").get("customShape") == ("1" if restored else None)
    assert root.find("edge/lane").get("speed") == "10"
    assert root.find("edge/lane").get("shape") == "-30,0 -10,0"
    assert root.find("tlLogic/phase").get("duration") == "30"
    assert source.read_bytes() == baseline
    assert len(commands) == 2
    assert ET.parse(output).getroot().find("edge/lane").get("speed") == "10"


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="netconvert is not installed")
def test_unlisted_joined_turn_keeps_the_original_internal_bus_restriction(tmp_path):
    nodes, edges, connections, source, join, joined = [tmp_path / name for name in
        ("nodes.xml", "edges.xml", "connections.xml", "source.net.xml", "join.xml", "joined.net.xml")]
    nodes.write_text('<nodes><node id="w" x="-80" y="0"/><node id="a" x="-10" y="0"/><node id="b" x="10" y="0"/><node id="e" x="80" y="0"/><node id="n" x="-10" y="80"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="a"/><edge id="middle" from="a" to="b" allow="bus"/><edge id="out" from="b" to="e"/><edge id="side" from="a" to="n"/></edges>', encoding="utf-8")
    connections.write_text('<connections><connection from="in" to="middle" fromLane="0" toLane="0"/><connection from="middle" to="out" fromLane="0" toLane="0"/><connection from="in" to="side" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-x", str(connections), "-o", str(source), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    join.write_text('<nodes><join nodes="a b" id="J" type="priority"/></nodes>', encoding="utf-8")
    subprocess.run(["netconvert", "-s", str(source), "-n", str(join), "-o", str(joined), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    # Reproduce the observed native join: the removed middle road's bus-only
    # permission is absent from the new connection and its internal lanes.
    root = ET.parse(joined).getroot()
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            for lane in edge.findall("lane"):
                for name in ("allow", "disallow"):
                    lane.attrib.pop(name, None)
    for connection in root.findall("connection"):
        for name in ("allow", "disallow"):
            connection.attrib.pop(name, None)
    ET.ElementTree(root).write(joined, encoding="utf-8", xml_declaration=True)
    shapes = {edge: candidate_module._parse_shape(root.find(f"edge[@id='{edge}']/lane").get("shape")) for edge in ("in", "side")}
    lanes = [{"lane_id": edge, "lane_type": "vehicle", "shape_network": shape,
              "junction_endpoint_network": shape[-1] if edge == "in" else shape[0]} for edge, shape in shapes.items()]
    movement = {"movement_id": "1", "intersection_part": "0", "ingress_lane_id": "in", "egress_lane_id": "side", "selected_source": "official_map",
                "selected_shape_network": [shapes["in"][-1], shapes["side"][0]]}
    report = _write_movement_candidate(joined_net=joined, original_net=source, plans={"test": {"lanes": lanes, "movements": [movement]}},
        groups=[{"node_id": "test", "intersection_part": "0", "join_id": "J", "source_node_ids": ["a", "b"]}], skipped_join_ids=set(),
        output_file=tmp_path / "result.net.xml", maximum_anchor_projection_error_m=10)
    support = report["official_connection_audit"]["source_movement_support"]
    assert support["unresolved"] == []
    extra = next(row for row in support["source_backed_extra"] if row["connection"] == ["in", 0, "out", 0])
    assert extra["candidate_vehicle_classes"] == ["bus"]
    assert report["official_connection_audit"]["restored_source_permissions"][0]["allowed_vehicle_classes"] == ["bus"]
    built = ET.parse(tmp_path / "result.net.xml").getroot()
    turn = built.find("connection[@from='in'][@to='out']")
    via = next(lane for edge in built.findall("edge") for lane in edge.findall("lane") if lane.get("id") == turn.get("via"))
    assert candidate_module._lane_motorized_modes(turn) == {"bus"}
    assert candidate_module._lane_motorized_modes(via) == {"bus"}
    # Narrow only the motor classes checked by the source audit.
    assert "pedestrian" not in turn.get("disallow", "").split()
    reloaded = tmp_path / "reloaded.net.xml"
    subprocess.run(["netconvert", "-s", str(tmp_path / "result.net.xml"), "-o", str(reloaded), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    assert candidate_module._lane_motorized_modes(ET.parse(reloaded).getroot().find("connection[@from='in'][@to='out']")) == {"bus"}
    # Explicit official reconstruction may intentionally differ from OSM.
    # It must not be silently narrowed by the supplementary-turn policy.
    out_shape = candidate_module._parse_shape(root.find("edge[@id='out']/lane").get("shape"))
    official_turn = {**movement, "movement_id": "2", "egress_lane_id": "out",
                     "selected_shape_network": [shapes["in"][-1], out_shape[0]]}
    official = _write_movement_candidate(joined_net=joined, original_net=source,
        plans={"test": {"lanes": [*lanes, {"lane_id": "out", "lane_type": "vehicle", "shape_network": out_shape,
            "junction_endpoint_network": out_shape[0], "allowed_vehicle_classes": ["passenger"]}], "movements": [movement, official_turn]}},
        groups=[{"node_id": "test", "intersection_part": "0", "join_id": "J", "source_node_ids": ["a", "b"]}],
        skipped_join_ids=set(), output_file=tmp_path / "official.net.xml", maximum_anchor_projection_error_m=10)
    assert official["official_connection_audit"]["restored_source_permissions"] == []
    assert candidate_module._lane_motorized_modes(ET.parse(tmp_path / "official.net.xml").getroot().find("connection[@from='in'][@to='out']")) == {"passenger"}


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="netconvert is not installed")
def test_joined_candidate_covers_official_segments_without_deleting_through_lanes(tmp_path):
    nodes, edges, source, joined = [tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml", "joined.net.xml")]
    nodes.write_text('<nodes><node id="w" x="-100" y="0"/><node id="left" x="-30" y="0"/><node id="middle" x="0" y="0"/><node id="right" x="30" y="0"/><node id="e" x="100" y="0"/><node id="nw" x="-30" y="60"/><node id="ne" x="30" y="60"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="w" to="left" numLanes="2"/><edge id="egress" from="left" to="middle" numLanes="2"/><edge id="ingress" from="middle" to="right" numLanes="2"/><edge id="out" from="right" to="e" numLanes="2"/><edge id="branch1" from="left" to="nw"/><edge id="branch2" from="right" to="ne"/></edges>', encoding="utf-8")
    subprocess.run(["netconvert", "-n", str(nodes), "-e", str(edges), "-o", str(source), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    joins = tmp_path / "joins.xml"
    joins.write_text('<nodes><join nodes="left middle right" id="joined"/></nodes>', encoding="utf-8")
    subprocess.run(["netconvert", "-s", str(source), "-n", str(joins), "-o", str(joined), "--offset.disable-normalization"], check=True, capture_output=True, timeout=30)
    root = ET.parse(source).getroot()
    shapes = {lane.get("id"): candidate_module._parse_shape(lane.get("shape")) for edge in root.findall("edge") for lane in edge.findall("lane")}
    lanes, movements = [], []
    for index in (0, 1):
        for edge_id, role in (("in", "ingress"), ("egress", "egress"), ("ingress", "ingress"), ("out", "egress")):
            shape = shapes[f"{edge_id}_{index}"]
            lanes.append({"lane_id": f"{edge_id}{index}", "lane_type": "vehicle", "direction_role": role, "shape_network": shape, "junction_endpoint_network": shape[-1] if role == "ingress" else shape[0]})
        for first, last in (("in", "egress"), ("ingress", "out")):
            shape = [shapes[f"{first}_{index}"][-1], shapes[f"{last}_{index}"][0]]
            movements.append({"movement_id": f"{first}{index}-{last}{index}", "intersection_part": "0", "ingress_lane_id": f"{first}{index}", "egress_lane_id": f"{last}{index}", "selected_source": "official_map", "official_shape_network": shape, "selected_shape_network": shape})
    report = _write_movement_candidate(joined_net=joined, original_net=source, plans={"119": {"lanes": lanes, "movements": movements, "max_error_m": 3}}, groups=[{"node_id": "119", "intersection_part": "0", "join_id": "joined", "source_node_ids": ["left", "middle", "right"]}], skipped_join_ids=set(), output_file=tmp_path / "candidate.net.xml", maximum_anchor_projection_error_m=10)
    audit = report["official_connection_audit"]
    assert not audit["ambiguous"]
    assert len(audit["required"]) == 4
    assert report["materialized_movement_count"] == 4
    assert {tuple(row["sumo_connection"]) for row in audit["required"]} == {("in", 0, "out", 0), ("in", 1, "out", 1)}
    assert all(row["status"] == "pass" for row in audit["composition"]["movement_coverage"])
    assert all(row["internal_lane_id"] == "" for row in report["movements"])
    assert all(row["signal_binding_status"] == "composed_submovement_requires_control_mapping" for row in report["movements"])
    next(lane for lane in lanes if lane["lane_id"] == "egress0")["allowed_vehicle_classes"] = ["bus"]
    restricted = _write_movement_candidate(joined_net=joined, original_net=source, plans={"119": {"lanes": lanes, "movements": movements, "max_error_m": 3}}, groups=[{"node_id": "119", "intersection_part": "0", "join_id": "joined", "source_node_ids": ["left", "middle", "right"]}], skipped_join_ids=set(), output_file=tmp_path / "bus.net.xml", maximum_anchor_projection_error_m=10)
    compiled = ET.parse(tmp_path / "bus.net.xml").getroot()
    connection = compiled.find("connection[@from='in'][@fromLane='0'][@to='out'][@toLane='0']")
    internal = next(lane for edge in compiled.findall("edge") for lane in edge.findall("lane") if lane.get("id") == connection.get("via"))
    assert candidate_module._lane_motorized_modes(internal) == {"bus"}
    assert len(restricted["official_connection_audit"]["required"]) == 4


def test_reanchor_movement_shape_preserves_middle_curve() -> None:
    result = reanchor_movement_shape(
        [(0.0, 0.0), (5.0, 3.0), (10.0, 0.0)],
        start=(1.0, 1.0),
        end=(11.0, -1.0),
    )

    assert result[0] == (1.0, 1.0)
    assert result[-1] == (11.0, -1.0)
    assert math.dist(result[1], (6.0, 3.0)) < 0.01


def test_fit_movement_shape_trims_long_official_tails_before_reanchoring() -> None:
    result, error = fit_movement_shape_to_anchors(
        [(-20.0, 0.0), (-5.0, 0.0), (0.0, 3.0), (5.0, 0.0), (20.0, 0.0)],
        start=(-6.0, 1.0),
        end=(6.0, -1.0),
    )

    assert result[0] == (-6.0, 1.0)
    assert result[-1] == (6.0, -1.0)
    assert min(point[0] for point in result) >= -6.0
    assert max(point[0] for point in result) <= 6.0
    assert max(point[1] for point in result[1:-1]) > 2.0
    assert error <= 2.0


def test_cli_routes_hamburg_combined_candidate(monkeypatch, tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"

    def fake_build(*, request_file, output_dir):
        assert request_file == str(request)
        assert output_dir == str(output)
        return {"status": "pass", "decision": "review_required"}

    monkeypatch.setattr(
        cli,
        "build_hamburg_aerial_combined_candidate",
        fake_build,
        raising=False,
    )

    exit_code = cli.main(
        [
            "hamburg",
            "combine-aerial-movements",
            str(request),
            str(output),
            "--json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "review_required"


def _area(points: list[tuple[float, float]]) -> float:
    return abs(
        sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(points, [*points[1:], points[0]])
        )
    ) / 2
