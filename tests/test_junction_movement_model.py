from pathlib import Path

from torii_sumo.core.junction_movement_model import (
    audit_movement_graph,
    build_approach_model,
    build_movement_graph,
    classify_turn_direction,
    write_movement_review,
)
from torii_sumo.road_semantics import classify_turn_direction as shared_classify_turn_direction


def _write_fixture(path: Path) -> None:
    path.write_text(
        """<net>
  <edge id="west_in" from="w" to="j" type="highway.primary" name="Main Street">
    <lane id="west_in_0" index="0" allow="passenger" length="10" shape="-10,0 0,0"/>
  </edge>
  <edge id="east_out" from="j" to="e" type="highway.primary" name="Main Street">
    <lane id="east_out_0" index="0" allow="passenger" length="10" shape="0,0 10,1"/>
  </edge>
  <edge id="south_out" from="j" to="s" type="highway.secondary" name="South Road">
    <lane id="south_out_0" index="0" allow="passenger" length="10" shape="0,0 0,-10"/>
  </edge>
  <edge id="north_out" from="j" to="n" type="highway.secondary" name="North Road">
    <lane id="north_out_0" index="0" allow="passenger" length="10" shape="0,0 0,10"/>
  </edge>
  <edge id="west_out" from="j" to="w2" type="highway.primary" name="Main Street">
    <lane id="west_out_0" index="0" allow="passenger" length="10" shape="0,0 -10,0"/>
  </edge>
  <edge id="foot_out" from="j" to="p" type="highway.footway">
    <lane id="foot_out_0" index="0" allow="pedestrian" length="5" shape="0,0 0,5"/>
  </edge>
  <edge id="bike_out" from="j" to="b" type="highway.cycleway">
    <lane id="bike_out_0" index="0" allow="bicycle" length="5" shape="0,0 5,5"/>
  </edge>
  <junction id="w" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="e" x="10" y="0" type="priority"/>
  <junction id="s" x="0" y="-10" type="priority"/>
  <junction id="n" x="0" y="10" type="priority"/>
  <junction id="w2" x="-10" y="0" type="priority"/>
  <junction id="p" x="0" y="5" type="priority"/>
  <junction id="b" x="5" y="5" type="priority"/>
</net>
""",
        encoding="utf-8",
    )


def test_classify_turn_direction() -> None:
    assert classify_turn_direction((1, 0), (1, 0)) == "straight"
    assert classify_turn_direction((1, 0), (0, -1)) == "right"
    assert classify_turn_direction((1, 0), (0, 1)) == "left"
    assert classify_turn_direction((1, 0), (-1, 0)) == "u_turn"
    assert classify_turn_direction((1, 0), (10, 2)) == "straight"


def test_classify_turn_direction_agrees_with_shared_semantics_for_cardinal_turns() -> None:
    cases = [
        ((1, 0), (0, -1)),
        ((1, 0), (0, 1)),
        ((1, 0), (-1, 0)),
    ]

    for in_axis, out_axis in cases:
        assert classify_turn_direction(in_axis, out_axis) == shared_classify_turn_direction(in_axis, out_axis)


def test_build_approach_model_keeps_support_layers_separate(tmp_path: Path) -> None:
    net_file = tmp_path / "fixture.net.xml"
    _write_fixture(net_file)

    model = build_approach_model(net_file, "j")

    vehicle_ids = {approach["id"] for approach in model["vehicle_approaches"]}
    support_ids = {approach["edge_id"] for approach in model["support_approaches"]}
    assert {"in:west_in", "out:east_out", "out:south_out", "out:north_out", "out:west_out"} <= vehicle_ids
    assert "out:foot_out" not in vehicle_ids
    assert "out:bike_out" not in vehicle_ids
    assert support_ids == {"foot_out", "bike_out"}


def test_build_movement_graph_marks_uturn_for_review(tmp_path: Path) -> None:
    net_file = tmp_path / "fixture.net.xml"
    _write_fixture(net_file)

    graph = build_movement_graph(net_file, "j")

    by_target = {movement["target_approach_id"]: movement for movement in graph["movements"]}
    assert by_target["out:east_out"]["turn_class"] == "straight"
    assert by_target["out:south_out"]["turn_class"] == "right"
    assert by_target["out:north_out"]["turn_class"] == "left"
    assert by_target["out:west_out"]["turn_class"] == "u_turn"
    assert by_target["out:west_out"]["status"] == "needs_review"


def test_movement_graph_does_not_emit_every_non_uturn_target(tmp_path: Path) -> None:
    net_file = tmp_path / "cartesian.net.xml"
    net_file.write_text(
        """<net>
  <edge id="west_in" from="w" to="j" type="highway.primary" name="Main"><lane id="west_in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="east_out" from="j" to="e" type="highway.primary" name="Main"><lane id="east_out_0" index="0" allow="passenger" shape="0,0 10,0"/></edge>
  <edge id="south_out" from="j" to="s" type="highway.secondary" name="South"><lane id="south_out_0" index="0" allow="passenger" shape="0,0 0,-10"/></edge>
  <edge id="north_out" from="j" to="n" type="highway.secondary" name="North"><lane id="north_out_0" index="0" allow="passenger" shape="0,0 0,10"/></edge>
  <junction id="w" x="-10" y="0" type="priority"/>
  <junction id="j" x="0" y="0" type="traffic_light"/>
  <junction id="e" x="10" y="0" type="priority"/>
  <junction id="s" x="0" y="-10" type="priority"/>
  <junction id="n" x="0" y="10" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    graph = build_movement_graph(net_file, "j")
    emitted = {
        (movement["source_edge_id"], movement["target_edge_id"])
        for movement in graph["movements"]
        if movement["status"] == "emit"
    }
    review = {
        (movement["source_edge_id"], movement["target_edge_id"])
        for movement in graph["movements"]
        if movement["status"] == "needs_review"
    }

    assert ("west_in", "east_out") in emitted
    assert ("west_in", "south_out") in emitted
    assert ("west_in", "north_out") in review


def test_audit_movement_graph_flags_review_movements(tmp_path: Path) -> None:
    net_file = tmp_path / "fixture.net.xml"
    _write_fixture(net_file)
    graph = build_movement_graph(net_file, "j")

    audit = audit_movement_graph(graph)

    issue_codes = {issue["code"] for issue in audit["issues"]}
    assert audit["status"] == "review"
    assert "low_confidence_movement" in issue_codes
    assert "u_turn_without_explicit_reason" in issue_codes


def test_write_movement_review_exports_json_and_csv(tmp_path: Path) -> None:
    net_file = tmp_path / "fixture.net.xml"
    _write_fixture(net_file)
    graph = build_movement_graph(net_file, "j")
    audit = audit_movement_graph(graph)

    report = write_movement_review(graph, audit, tmp_path / "review", "demo")

    assert Path(report["movement_graph_file"]).is_file()
    assert Path(report["movement_audit_file"]).is_file()
    assert Path(report["approaches_file"]).read_text(encoding="utf-8").splitlines()[0].startswith("id,edge_id")
    assert Path(report["movements_file"]).read_text(encoding="utf-8").splitlines()[0].startswith(
        "source_approach_id,target_approach_id"
    )
