from __future__ import annotations

import json
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.command_runner import CommandResult
from torii_sumo.core.connection_mode_audit import (
    _expand_coupled_scopes_by_tls_controller,
    _expand_scope_by_tls_controller,
    audit_network_connection_mode,
    audit_standard_connection_mode,
    build_official_teacher_connection_mode_evidence,
    build_connection_mode_regression_audit,
    build_connection_mode_catalog,
    build_network_connection_mode_audit,
    compare_connection_mode_audits,
)
from torii_sumo.core.standard_nema_binding import build_standard_nema_phase_binding
from torii_sumo.corridor.calibration import (
    ConnectionAuditCalibrationPolicy,
    calibrate_connection_mode_audit,
)
from torii_sumo.corridor.enums import GateStatus, TrafficSide


def _write_standard_network(
    path: Path,
    *,
    arm_names: tuple[str, ...] = ("W", "E", "S", "N"),
    include_turnaround: bool = False,
    lefthand: bool = False,
) -> None:
    root = ET.Element("net", lefthand="true") if lefthand else ET.Element("net")
    coordinates = {"W": (-100, 0), "E": (100, 0), "S": (0, -100), "N": (0, 100)}

    def lane_boundary(arm: str, lane_index: int, *, incoming: bool) -> tuple[float, float]:
        offset = -3.2 + 3.2 * lane_index
        if arm == "W":
            return (-5.0, offset if incoming else -offset)
        if arm == "E":
            return (5.0, -offset if incoming else offset)
        if arm == "S":
            return (-offset if incoming else offset, -5.0)
        return (offset if incoming else -offset, 5.0)

    def point(point_value: tuple[float, float]) -> str:
        return f"{point_value[0]:.1f},{point_value[1]:.1f}"

    for arm in arm_names:
        x, y = coordinates[arm]
        priority = "3" if arm in {"W", "E"} else "2"
        incoming = ET.SubElement(
            root,
            "edge",
            {"id": f"{arm}_in", "from": arm, "to": "J0", "priority": priority},
        )
        outgoing = ET.SubElement(
            root,
            "edge",
            {"id": f"{arm}_out", "from": "J0", "to": arm, "priority": priority},
        )
        incoming_lane_count = 3 if len(arm_names) == 4 else 2
        for lane_index in range(incoming_lane_count):
            incoming_boundary = lane_boundary(arm, lane_index, incoming=True)
            ET.SubElement(
                incoming,
                "lane",
                {
                    "id": f"{arm}_in_{lane_index}",
                    "index": str(lane_index),
                    "allow": "passenger",
                    "speed": "13.89",
                    "length": "100",
                    "shape": f"{x},{y} {point(incoming_boundary)}",
                },
            )
        outgoing_lane_count = 3 if len(arm_names) == 4 else 2
        for lane_index in range(outgoing_lane_count):
            outgoing_boundary = lane_boundary(arm, lane_index, incoming=False)
            ET.SubElement(
                outgoing,
                "lane",
                {
                    "id": f"{arm}_out_{lane_index}",
                    "index": str(lane_index),
                    "allow": "passenger",
                    "speed": "13.89",
                    "length": "100",
                    "shape": f"{point(outgoing_boundary)} {x},{y}",
                },
            )

    movements = {
        ("W", "E", "S", "N"): {
            "W": (("S", 0, 0, "r"), ("E", 1, 1, "s"), ("N", 2, 2, "l")),
            "E": (("N", 0, 0, "r"), ("W", 1, 1, "s"), ("S", 2, 2, "l")),
            "S": (("E", 0, 0, "r"), ("N", 1, 1, "s"), ("W", 2, 2, "l")),
            "N": (("W", 0, 0, "r"), ("S", 1, 1, "s"), ("E", 2, 2, "l")),
        },
        ("W", "E", "S"): {
            "W": (("S", 0, 0, "r"), ("E", 1, 1, "s")),
            "E": (("W", 0, 0, "s"), ("S", 1, 1, "l")),
            "S": (("E", 0, 0, "r"), ("W", 1, 1, "l")),
        },
    }[arm_names]
    movement_specs = [
        (arm, target, source_lane_index, target_lane_index, turn)
        for arm, arm_rows in movements.items()
        for target, source_lane_index, target_lane_index, turn in arm_rows
    ]
    if include_turnaround:
        movement_specs.append(("W", "W", 2, 2, "t"))
    movement_count = len(movement_specs)
    for connection_index, (
        arm,
        target,
        source_lane_index,
        target_lane_index,
        turn,
    ) in enumerate(movement_specs):
        start = lane_boundary(arm, source_lane_index, incoming=True)
        end = lane_boundary(target, target_lane_index, incoming=False)
        midpoint = (-8.0, 0.0) if turn == "t" else (0.0, 0.0)
        internal = ET.SubElement(
            root,
            "edge",
            {"id": f":J0_{connection_index}", "function": "internal"},
        )
        ET.SubElement(
            internal,
            "lane",
            {
                "id": f":J0_{connection_index}_0",
                "index": "0",
                "allow": "passenger",
                "speed": "10",
                "length": "12",
                "shape": f"{point(start)} {point(midpoint)} {point(end)}",
            },
        )
    logic = ET.SubElement(root, "tlLogic", id="J0", type="static", programID="0", offset="7")
    ET.SubElement(logic, "phase", duration="30", state="G" + "r" * (movement_count - 1))
    ET.SubElement(logic, "phase", duration="3", state="y" + "r" * (movement_count - 1))
    junction = ET.SubElement(
        root,
        "junction",
        id="J0",
        type="traffic_light",
        x="0",
        y="0",
        incLanes=" ".join(
            f"{arm}_in_{lane}"
            for arm in arm_names
            for lane in range(3 if len(arm_names) == 4 else 2)
        ),
        intLanes=" ".join(f":J0_{index}_0" for index in range(movement_count)),
    )
    for request_index in range(movement_count):
        ET.SubElement(
            junction,
            "request",
            index=str(request_index),
            response="0" * movement_count,
            foes="0" * movement_count,
            cont="0",
        )
    for arm in arm_names:
        x, y = coordinates[arm]
        ET.SubElement(root, "junction", id=arm, type="priority", x=str(x), y=str(y), incLanes="")

    for connection_index, (
        arm,
        target,
        source_lane_index,
        target_lane_index,
        turn,
    ) in enumerate(movement_specs):
        ET.SubElement(
            root,
            "connection",
            {
                "from": f"{arm}_in",
                "to": f"{target}_out",
                "fromLane": str(source_lane_index),
                "toLane": str(target_lane_index),
                "via": f":J0_{connection_index}_0",
                "tl": "J0",
                "linkIndex": str(connection_index),
                "dir": turn,
                "state": "o",
            },
        )
    for connection_index, (
        _arm,
        target,
        _source_lane_index,
        target_lane_index,
        turn,
    ) in enumerate(movement_specs):
        ET.SubElement(
            root,
            "connection",
            {
                "from": f":J0_{connection_index}",
                "to": f"{target}_out",
                "fromLane": "0",
                "toLane": str(target_lane_index),
                "dir": turn,
                "state": "M",
            },
        )
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def test_standard_four_way_scan_and_materialization_are_reversible(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    _write_standard_network(source)
    source_sha256 = file_sha256(source)

    scan = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "scan",
        run_runtime_checks=False,
    )

    assert scan["status"] == "pass"
    assert scan["nema_binding_status"] == "scan_complete"
    assert scan["scan_counts"] == {
        "traffic_light_junction_count": 1,
        "eligible_count": 1,
        "review_required_count": 0,
        "three_way_count": 0,
        "four_way_count": 1,
    }
    assert scan["candidate_net_file"] == ""
    assert scan["candidates"][0]["connection_mode_audit"]["status"] == "pass"
    assert Path(scan["connection_mode_report_file"]).is_file()

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        prefix="probe",
        junction_id="J0",
        run_runtime_checks=False,
    )

    assert report["status"] == "pass"
    assert report["nema_binding_status"] == "candidate_ready_for_review"
    assert report["promotion_status"] == "review_required"
    assert report["source_preservation_status"] == "pass"
    assert file_sha256(source) == source_sha256
    assert report["candidate_validation"]["status"] == "pass"
    assert report["candidate_validation"]["verified_binding_count"] == 12
    assert report["runtime_validation"]["status"] == "not_run"

    selected = report["selected_candidate"]
    assert selected["layout_type"] == "four_way"
    assert selected["used_nema_phases"] == list(range(1, 9))
    movement_phases = {
        (row["from"], row["geometry_dir"]): row["nema_phase"]
        for row in selected["movement_map"]
    }
    assert movement_phases[("W_in", "l")] == 5
    assert movement_phases[("W_in", "s")] == 2
    assert movement_phases[("E_in", "l")] == 1
    assert movement_phases[("E_in", "s")] == 6
    assert movement_phases[("S_in", "l")] == 7
    assert movement_phases[("N_in", "s")] == 8
    assert selected["nema_compatibility"]["status"] == "pass"
    assert selected["connection_mode_audit"]["status"] == "pass"
    assert selected["connection_mode_audit"]["verified_internal_path_count"] == 12
    assert not selected["connection_mode_audit"]["request_foe_audit"]["foe_conflicts"]

    candidate = ET.parse(report["candidate_net_file"]).getroot()
    logic = candidate.find("tlLogic[@id='J0']")
    assert logic is not None
    assert logic.attrib == {
        "id": "J0",
        "type": "NEMA",
        "programID": "Torii_NEMA_90",
        "offset": "7",
    }
    assert [phase.attrib["name"] for phase in logic.findall("phase")] == [str(value) for value in range(1, 9)]
    assert {len(phase.attrib["state"]) for phase in logic.findall("phase")} == {8}
    assert {param.attrib["key"]: param.attrib["value"] for param in logic.findall("param")}["ring2"] == "5,6,7,8"

    overlay = ET.parse(report["review_overlay_file"]).getroot()
    assert {element.tag for element in overlay.iter()} <= {"additional", "poi", "param"}
    assert overlay.find("poi/param[@key='display_only']").attrib["value"] == "true"
    decision = json.loads(Path(report["review_decision_file"]).read_text(encoding="utf-8"))
    assert decision["status"] == "pending_human_review"
    assert decision["candidate_sha256"] == report["candidate_sha256"]
    plan = json.loads(Path(report["plan_file"]).read_text(encoding="utf-8"))
    assert plan["rollback"]["source_network_immutable"] is True
    assert "<tlLogic" in plan["rollback"]["before_tllogic_xml"]
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["review_overlay_display_only"] is True
    assert any(item["path"] == str(Path(report["candidate_net_file"]).resolve()) for item in manifest["artifacts"])


def test_standard_three_way_uses_missing_phase_placeholders(tmp_path: Path) -> None:
    source = tmp_path / "three_way.net.xml"
    _write_standard_network(source, arm_names=("W", "E", "S"))

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    assert report["status"] == "pass"
    selected = report["selected_candidate"]
    assert selected["layout_type"] == "three_way"
    assert selected["used_nema_phases"] == [1, 2, 4, 6]
    assert selected["nema_params"]["ring1"] == "1,2,0,4"
    assert selected["nema_params"]["ring2"] == "0,6,0,4"
    assert selected["nema_params"]["barrier2Phases"] == "4,4"
    assert selected["connection_mode_audit"]["status"] == "pass"
    logic = ET.parse(report["candidate_net_file"]).getroot().find("tlLogic[@id='J0']")
    assert logic is not None
    assert [phase.attrib["name"] for phase in logic.findall("phase")] == ["1", "2", "4", "6"]
    assert {len(phase.attrib["state"]) for phase in logic.findall("phase")} == {6}


def test_edge_routeability_can_survive_while_connection_mode_gate_blocks_scrambled_lanes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scrambled.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    direct = [connection for connection in root.findall("connection") if connection.attrib.get("tl") == "J0"]
    original_edge_pairs = {(item.attrib["from"], item.attrib["to"]) for item in direct}
    west_right = next(item for item in direct if item.attrib["from"] == "W_in" and item.attrib["dir"] == "r")
    west_left = next(item for item in direct if item.attrib["from"] == "W_in" and item.attrib["dir"] == "l")
    west_right.set("fromLane", "2")
    west_left.set("fromLane", "0")
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    scrambled_pairs = {
        (item.attrib["from"], item.attrib["to"])
        for item in ET.parse(source).getroot().findall("connection")
        if item.attrib.get("tl") == "J0"
    }
    assert scrambled_pairs == original_edge_pairs
    assert report["status"] == "blocked"
    audit = report["selected_candidate"]["connection_mode_audit"]
    assert audit["status"] == "fail"
    assert any(
        blocker.startswith("connection_mode:turn_lane_order_inversion:W_in")
        for blocker in audit["blockers"]
    )
    assert any(
        blocker.startswith("connection_mode:path_endpoint_gap")
        for blocker in audit["blockers"]
    )
    assert not any(
        blocker.startswith("required_arm_to_arm_movement_missing")
        for blocker in report["selected_candidate"]["blockers"]
    )
    assert report["candidate_net_file"] == ""


def test_directional_request_foe_blocks_nema_concurrent_phase_pair(tmp_path: Path) -> None:
    source = tmp_path / "foe-conflict.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    junction = root.find("junction[@id='J0']")
    assert junction is not None
    requests = junction.findall("request")
    movement_count = len(requests)

    # W-left is request 2 (phase 5); E-left is request 5 (phase 1).
    first, second = 2, 5
    first_bits = list(requests[first].attrib["foes"])
    first_bits[movement_count - second - 1] = "1"
    requests[first].set("foes", "".join(first_bits))
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    audit = report["selected_candidate"]["connection_mode_audit"]
    assert report["status"] == "blocked"
    assert audit["request_foe_audit"]["status"] == "review_required"
    assert audit["request_foe_audit"]["foe_conflicts"]
    assert audit["request_foe_audit"]["asymmetric_foe_pair_count"] == 1
    conflict = audit["request_foe_audit"]["foe_conflicts"][0]
    assert conflict["first_marks_second_as_foe"] is True
    assert conflict["second_marks_first_as_foe"] is False
    assert any(
        blocker.startswith("connection_mode:nema_concurrent_movements_are_foes")
        for blocker in audit["blockers"]
    )


def test_directional_foe_for_nonconcurrent_nema_pair_is_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "directional-foe.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    junction = root.find("junction[@id='J0']")
    assert junction is not None
    requests = junction.findall("request")
    movement_count = len(requests)

    # W-right is phase 2 and S-right is phase 4; the two rings cannot serve
    # these across-barrier movements concurrently.
    first, second = 0, 6
    first_bits = list(requests[first].attrib["foes"])
    first_bits[movement_count - second - 1] = "1"
    requests[first].set("foes", "".join(first_bits))
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    audit = report["selected_candidate"]["connection_mode_audit"]
    request_audit = audit["request_foe_audit"]
    assert report["status"] == "pass"
    assert audit["status"] == "pass"
    assert request_audit["status"] == "pass"
    assert request_audit["asymmetric_foe_pair_count"] == 1
    assert request_audit["foe_matrix_semantics"] == "directional_by_design"
    assert not request_audit["foe_conflicts"]


def test_internal_lane_connections_use_edge_ordinal_when_declared_indexes_repeat() -> None:
    root = ET.fromstring(
        """<net>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" shape="0,0 1,0"/>
    <lane id=":j_0_1" index="0" shape="0,1 1,1"/>
  </edge>
  <edge id="plain_internal" function="internal">
    <lane id="plain_internal_0" index="0" shape="0,2 1,2"/>
  </edge>
  <edge id="walk" function="crossing">
    <lane id="walk_0" index="0" allow="pedestrian" shape="0,3 1,3"/>
  </edge>
  <edge id="out"><lane id="out_0" index="0" shape="1,0 2,0"/></edge>
  <connection from=":j_0" to="out" fromLane="0" toLane="0"/>
  <connection from=":j_0" to="out" fromLane="1" toLane="0"/>
</net>"""
    )

    catalog = build_connection_mode_catalog(root)

    assert catalog["lanes_by_edge"][":j_0"][0]["id"] == ":j_0_0"
    assert catalog["lanes_by_edge"][":j_0"][1]["id"] == ":j_0_1"
    assert catalog["lane_catalog"][":j_0_1"]["index"] == 1
    assert catalog["lane_catalog"][":j_0_1"]["declared_index"] == 0
    assert catalog["internal_lane_count"] == 3
    assert sorted(catalog["outgoing_by_lane"]) == [(":j_0", 0), (":j_0", 1)]


def test_right_turn_uses_curbmost_motor_lane_not_pedestrian_or_bicycle_lane() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in">
    <lane id="in_0" index="0" allow="pedestrian" shape="-10,-2 0,-2"/>
    <lane id="in_1" index="1" allow="bicycle" shape="-10,-1 0,-1"/>
    <lane id="in_2" index="2" allow="passenger" shape="-10,0 0,0"/>
  </edge>
  <edge id="out">
    <lane id="out_0" index="0" allow="pedestrian" shape="1,-2 10,-2"/>
    <lane id="out_1" index="1" allow="bicycle" shape="1,-1 10,-1"/>
    <lane id="out_2" index="2" allow="passenger" shape="1,0 10,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="passenger" shape="0,0 1,0"/>
  </edge>
  <junction id="j" type="traffic_light" incLanes="in_0 in_1 in_2" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="2" toLane="2" via=":j_0_0" tl="j" linkIndex="0" dir="r"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="2" dir="r"/>
</net>"""
    )

    audit = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[
            {
                "connection_index": 0,
                "geometry_dir": "r",
                "nema_phase": 2,
            }
        ],
        layout_type="three_way",
    )

    assert audit["status"] == "pass"
    assert audit["lane_order_checks"][0]["motorized_lane_indices"] == [2]
    assert audit["movement_checks"][0]["normalized_source_lane_rank"] == 0.5
    assert audit["movement_checks"][0]["normalized_target_lane_rank"] == 0.5


def test_connection_mode_traces_every_request_not_only_nema_mapped_rows(
    tmp_path: Path,
) -> None:
    source = tmp_path / "all-connections.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()

    audit = audit_standard_connection_mode(
        root,
        junction_id="J0",
        movement_rows=[
            {
                "connection_index": 0,
                "geometry_dir": "r",
                "effective_dir": "r",
                "nema_phase": 2,
            }
        ],
        layout_type="four_way",
    )

    assert audit["status"] == "pass"
    assert audit["direct_movement_count"] == 12
    assert audit["motorized_direct_movement_count"] == 12
    assert audit["nema_candidate_movement_count"] == 1
    assert audit["verified_internal_path_count"] == 12
    assert sum(
        check["nema_candidate_movement"] for check in audit["movement_checks"]
    ) == 1


def test_connection_mode_marks_unconnected_motor_lanes_for_review() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/>
    <lane id="in_1" index="1" allow="passenger" shape="-10,3 0,3"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="1,0 10,0"/>
    <lane id="out_1" index="1" allow="passenger" shape="1,3 10,3"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="passenger" shape="0,0 1,0"/>
  </edge>
  <junction id="j" type="traffic_light" incLanes="in_0 in_1" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" tl="j" linkIndex="0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    audit = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
    )

    assert audit["status"] == "review_required"
    assert audit["structural_failures"] == []
    completeness = audit["connection_completeness_audit"]
    assert completeness["status"] == "review_required"
    assert completeness["incoming_motorized_lane_count"] == 2
    assert completeness["connected_incoming_motorized_lane_count"] == 1
    assert completeness["outgoing_motorized_lane_count"] == 2
    assert completeness["reachable_outgoing_motorized_lane_count"] == 1
    assert completeness["findings"] == [
        "incoming_motorized_lane_without_connection:in:1",
        "outgoing_motorized_lane_without_connection:out:1",
    ]
    assert set(audit["review_findings"]) >= {
        "connection_mode:incoming_motorized_lane_without_connection:in:1",
        "connection_mode:outgoing_motorized_lane_without_connection:out:1",
    }


def test_connection_mode_accepts_only_exact_official_map_lane_fanout() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j"><lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/></edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="1,0 10,0"/>
    <lane id="out_1" index="1" allow="passenger" shape="1,3 10,3"/>
  </edge>
  <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" shape="0,0 1,0"/></edge>
  <edge id=":j_1" function="internal"><lane id=":j_1_0" index="0" allow="passenger" shape="0,0 1,3"/></edge>
  <junction id="j" type="traffic_light" incLanes="in_0" intLanes=":j_0_0 :j_1_0">
    <request index="0" response="00" foes="00" cont="0"/>
    <request index="1" response="00" foes="00" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from="in" to="out" fromLane="0" toLane="1" via=":j_1_0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from=":j_1" to="out" fromLane="0" toLane="1" dir="s"/>
</net>"""
    )

    evidence = {
        "in|0|out": {
            "basis": "official_map_connection_curve",
            "source_cell": "2349",
            "target_lanes": ["0", "1"],
        }
    }
    audit = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        evidence_justified_target_fanouts=evidence,
    )

    assert audit["status"] == "pass"
    assert not any("ambiguous_target_lane_fanout" in item for item in audit["review_findings"])
    assert audit["evidence_justified_target_fanouts"][0]["target_lanes"] == ["0", "1"]

    mismatch = dict(evidence)
    mismatch["in|0|out"] = {**evidence["in|0|out"], "target_lanes": ["0"]}
    rejected = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        evidence_justified_target_fanouts=mismatch,
    )
    assert rejected["status"] == "review_required"
    assert any("ambiguous_target_lane_fanout" in item for item in rejected["review_findings"])


def test_connection_mode_accepts_only_exact_official_map_destination_merge() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,-1 0,-1"/>
    <lane id="in_1" index="1" allow="passenger" shape="-10,1 0,1"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="1,0 10,0"/>
  </edge>
  <junction id="j" type="priority" incLanes="in_0 in_1" intLanes="">
    <request index="0" response="00" foes="00" cont="0"/>
    <request index="1" response="00" foes="00" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="in" to="out" fromLane="1" toLane="0" dir="s"/>
</net>"""
    )
    evidence = {
        "in|out|0": {
            "basis": "official_map_connection_curve",
            "source_cell": "2349",
            "source_lanes": ["0", "1"],
        }
    }

    audit = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        evidence_justified_destination_merges=evidence,
    )

    assert audit["status"] == "pass"
    assert not any("destination_lane_merge" in item for item in audit["review_findings"])
    assert audit["evidence_justified_destination_merges"][0]["source_lanes"] == ["0", "1"]

    rejected = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        evidence_justified_destination_merges={
            "in|out|0": {**evidence["in|out|0"], "source_lanes": ["0"]}
        },
    )
    assert rejected["status"] == "review_required"
    assert any("destination_lane_merge" in item for item in rejected["review_findings"])


def test_connection_mode_accepts_only_hash_bound_engineering_plan_lane_arrow_merge() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,-1 0,-1"/>
    <lane id="in_1" index="1" allow="passenger" shape="-10,1 0,1"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="1,0 10,0"/>
  </edge>
  <junction id="j" type="priority" incLanes="in_0 in_1" intLanes="">
    <request index="0" response="00" foes="00" cont="0"/>
    <request index="1" response="00" foes="00" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from="in" to="out" fromLane="1" toLane="0" dir="s"/>
</net>"""
    )
    exact = {
        "basis": "official_engineering_plan_lane_arrows",
        "source_cell": "2403",
        "source_lanes": ["0", "1"],
        "official_source_url": "https://lsbg.hamburg.de/example-plan.pdf",
        "official_source_sha256": "a" * 64,
    }

    accepted = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        evidence_justified_destination_merges={"in|out|0": exact},
    )
    assert accepted["status"] == "pass"
    assert accepted["evidence_justified_destination_merges"][0]["evidence"] == exact

    invalid_variants = (
        {**exact, "source_lanes": ["0"]},
        {**exact, "source_lanes": ["0", "0", "1"]},
        {**exact, "source_lanes": "01"},
        {**exact, "source_cell": ""},
        {**exact, "official_source_url": "http://lsbg.hamburg.de/example-plan.pdf"},
        {**exact, "official_source_sha256": "not-a-sha256"},
        {**exact, "basis": "manual_lane_arrows"},
    )
    for evidence in invalid_variants:
        rejected = audit_standard_connection_mode(
            root,
            junction_id="j",
            movement_rows=[],
            layout_type="unknown",
            evidence_justified_destination_merges={"in|out|0": evidence},
        )
        assert rejected["status"] == "review_required"
        assert any("destination_lane_merge" in item for item in rejected["review_findings"])


def test_connection_mode_uses_stable_official_lane_binding_for_rank_review() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,-1 0,-1"/>
    <lane id="in_1" index="1" allow="passenger" shape="-10,1 0,1"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="1,-1 10,-1"/>
    <lane id="out_1" index="1" allow="passenger" shape="1,1 10,1"/>
  </edge>
  <junction id="j" type="priority" incLanes="in_0 in_1" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="1" dir="s"/>
  <connection from="in" to="out" fromLane="1" toLane="0" dir="s"/>
</net>"""
    )
    raw = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        normalized_lane_rank_tolerance=0.4,
    )
    assert any("lane_rank_jump:0:0.500" in item for item in raw["review_findings"])

    evidence = {
        "in|0|out|1": {
            "basis": "official_map_connection_curve",
            "source_cell": "node-5",
            "official_connection_id": "curve-15",
        }
    }
    justified = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        normalized_lane_rank_tolerance=0.4,
        evidence_justified_lane_bindings=evidence,
    )

    assert not any("lane_rank_jump:0:0.500" in item for item in justified["review_findings"])
    assert justified["evidence_justified_lane_bindings"][0]["key"] == "in|0|out|1"
    assert justified["movement_checks"][0]["evidence_justified_findings"][0]["evidence"] == evidence[
        "in|0|out|1"
    ]

    rejected = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
        normalized_lane_rank_tolerance=0.4,
        evidence_justified_lane_bindings={
            "in|0|out|1": {"basis": "visual_guess", "source_cell": "node-5"}
        },
    )
    assert any("lane_rank_jump:0:0.500" in item for item in rejected["review_findings"])


def test_official_teacher_connection_evidence_uses_mapped_physical_lane_keys(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    teacher.write_text(
        """<net>
  <edge id="teacher_in" from="a" to="teacher_core">
    <lane id="teacher_in_0" index="0" allow="passenger"/>
    <lane id="teacher_in_1" index="1" allow="passenger"/>
  </edge>
  <edge id="teacher_out" from="teacher_core" to="b">
    <lane id="teacher_out_0" index="0" allow="passenger"/>
    <lane id="teacher_out_1" index="1" allow="passenger"/>
  </edge>
  <edge id="remote" from="b" to="c"><lane id="remote_0" index="0" allow="passenger"/></edge>
  <junction id="teacher_core" type="traffic_light"/>
  <connection from="teacher_in" to="teacher_out" fromLane="0" toLane="0" shape="0,0 1,1"/>
  <connection from="teacher_in" to="teacher_out" fromLane="0" toLane="1" shape="0,0 1,2"/>
  <connection from="teacher_in" to="teacher_out" fromLane="1" toLane="0" shape="0,1 1,1"/>
  <connection from="teacher_out" to="remote" fromLane="0" toLane="0" shape="1,1 2,1"/>
</net>""",
        encoding="utf-8",
    )

    evidence = build_official_teacher_connection_mode_evidence(
        teacher,
        teacher_junction_id="teacher_core",
        edge_map={"teacher_in": "candidate_in", "teacher_out": "candidate_out"},
        source_cell="hamburg-node-5",
    )

    assert evidence["status"] == "pass"
    assert evidence["exact_connection_count"] == 3
    assert evidence["target_fanout_count"] == 1
    assert evidence["destination_merge_count"] == 1
    assert set(evidence["lane_bindings"]) == {
        "candidate_in|0|candidate_out|0",
        "candidate_in|0|candidate_out|1",
        "candidate_in|1|candidate_out|0",
    }
    fanout = evidence["target_fanouts"]["candidate_in|0|candidate_out"]
    assert fanout["target_lanes"] == ["0", "1"]
    assert fanout["basis"] == "official_map_connection_curve"
    assert fanout["source_cell"] == "hamburg-node-5"
    merge = evidence["destination_merges"]["candidate_in|candidate_out|0"]
    assert merge["source_lanes"] == ["0", "1"]
    assert merge["basis"] == "official_map_connection_curve"
    assert merge["source_cell"] == "hamburg-node-5"


def test_official_teacher_connection_evidence_fails_closed_without_bijective_edge_map(
    tmp_path: Path,
) -> None:
    teacher = tmp_path / "teacher.net.xml"
    teacher.write_text('<net><junction id="teacher_core"/></net>', encoding="utf-8")

    evidence = build_official_teacher_connection_mode_evidence(
        teacher,
        teacher_junction_id="teacher_core",
        edge_map={"teacher_in": "candidate", "teacher_out": "candidate"},
        source_cell="hamburg-node-5",
    )

    assert evidence["status"] == "fail"
    assert evidence["failures"] == ["edge_map_not_bijective"]


def test_network_connection_mode_audit_replaces_gui_as_automatic_gate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "network.net.xml"
    _write_standard_network(source)

    report = build_network_connection_mode_audit(
        source,
        output_dir=tmp_path / "audit",
    )

    assert report["status"] == "pass"
    assert report["automatic_promotion_gate"] == "pass"
    assert report["audit_engine"] == "static_net_xml_connection_graph"
    assert report["netedit_required_for_gate"] is False
    assert report["junction_count"] == 1
    assert report["direct_movement_count"] == 12
    assert report["verified_internal_path_count"] == 12
    assert report["tls_link_binding_audit"]["status"] == "pass"
    assert Path(report["report_file"]).is_file()
    assert Path(report["review_overlay_file"]).is_file()
    overlay = ET.parse(report["review_overlay_file"]).getroot()
    assert overlay.findall("poi") == []
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == file_sha256(source)
    assert manifest["review_overlay_display_only"] is True
    assert len(manifest["artifacts"]) == 2


def test_width_transition_centerline_offset_is_review_not_structural_failure() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="bicycle" width="1.0" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" width="8.4" shape="10,3.7 20,3.7"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="bicycle" width="1.0" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    report = audit_network_connection_mode(root, endpoint_tolerance_m=0.2)

    assert report["status"] == "review_required"
    junction = next(item for item in report["junctions"] if item["junction_id"] == "j")
    audit = junction["connection_mode_audit"]
    assert audit["structural_failures"] == []
    assert audit["review_findings"] == [
        "connection_mode:path_width_transition_centerline_offset:0:exit:3.700m:0.000m"
    ]
    path = audit["movement_checks"][0]["internal_path"]
    assert path["endpoint_gaps_m"] == [0.0, 3.7]
    assert path["endpoint_effective_gaps_m"] == [0.0, 0.0]
    assert path["endpoint_width_transition_offsets_m"] == [0.0, 3.7]
    assert path["max_endpoint_gap_m"] == 3.7
    assert path["max_effective_endpoint_gap_m"] == 0.0

    calibration = calibrate_connection_mode_audit(
        root,
        source_sha256="a" * 64,
        traffic_side=TrafficSide.RIGHT,
    )
    assert calibration.status is GateStatus.REVIEW
    assert calibration.maximum_observed_gap_m == 0.0
    assert not any(
        finding.startswith("baseline_endpoint_gap_exceeds_lane_scale_cap")
        for finding in calibration.findings
    )


def test_lane_width_change_does_not_hide_longitudinal_endpoint_break() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="bicycle" width="1.0" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" width="8.4" shape="13.7,0 20,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="bicycle" width="1.0" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    report = audit_network_connection_mode(root, endpoint_tolerance_m=0.2)

    assert report["status"] == "fail"
    junction = next(item for item in report["junctions"] if item["junction_id"] == "j")
    audit = junction["connection_mode_audit"]
    assert any(
        failure.startswith("connection_mode:path_endpoint_gap:0:exit:3.700m")
        for failure in audit["structural_failures"]
    )
    path = audit["movement_checks"][0]["internal_path"]
    assert path["endpoint_gaps_m"] == [0.0, 3.7]
    assert path["endpoint_effective_gaps_m"] == [0.0, 3.7]


def test_internal_path_trace_uses_subgraph_bound_not_fixed_sixteen_hops() -> None:
    root = ET.Element("net")
    incoming = ET.SubElement(root, "edge", {"id": "in", "from": "a", "to": "j"})
    ET.SubElement(
        incoming,
        "lane",
        {
            "id": "in_0",
            "index": "0",
            "allow": "passenger",
            "shape": "-20,0 0,0",
        },
    )
    outgoing = ET.SubElement(root, "edge", {"id": "out", "from": "j", "to": "b"})
    ET.SubElement(
        outgoing,
        "lane",
        {
            "id": "out_0",
            "index": "0",
            "allow": "passenger",
            "shape": "18,0 100,0",
        },
    )
    internal_lane_ids = []
    for index in range(18):
        edge_id = f":j_{index}"
        lane_id = f"{edge_id}_0"
        internal_lane_ids.append(lane_id)
        edge = ET.SubElement(root, "edge", {"id": edge_id, "function": "internal"})
        ET.SubElement(
            edge,
            "lane",
            {
                "id": lane_id,
                "index": "0",
                "allow": "passenger",
                "shape": f"{index},0 {index + 1},0",
            },
        )
    junction = ET.SubElement(
        root,
        "junction",
        {
            "id": "j",
            "type": "priority",
            "incLanes": "in_0",
            "intLanes": " ".join(internal_lane_ids),
        },
    )
    ET.SubElement(
        junction,
        "request",
        {"index": "0", "response": "0", "foes": "0", "cont": "0"},
    )
    ET.SubElement(
        root,
        "connection",
        {
            "from": "in",
            "to": "out",
            "fromLane": "0",
            "toLane": "0",
            "via": internal_lane_ids[0],
            "dir": "s",
        },
    )
    for index in range(18):
        attributes = {
            "from": f":j_{index}",
            "to": "out",
            "fromLane": "0",
            "toLane": "0",
            "dir": "s",
        }
        if index < 17:
            attributes["via"] = internal_lane_ids[index + 1]
        ET.SubElement(root, "connection", attributes)

    audit = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
    )

    movement = audit["movement_checks"][0]
    assert movement["internal_path"]["status"] == "pass"
    assert movement["internal_path"]["internal_lane_chain_length"] == 18
    assert movement["internal_path"]["bounded_hop_limit"] == 19
    assert audit["structural_failures"] == []
    assert (
        "connection_mode:internal_path_unusually_long:0:18"
        in audit["review_findings"]
    )


def test_connection_mode_regression_gate_blocks_new_unconnected_lane(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    candidate = tmp_path / "candidate.net.xml"
    _write_standard_network(source)
    candidate_tree = ET.parse(source)
    candidate_root = candidate_tree.getroot()
    incoming = candidate_root.find("edge[@id='W_in']")
    junction = candidate_root.find("junction[@id='J0']")
    assert incoming is not None
    assert junction is not None
    ET.SubElement(
        incoming,
        "lane",
        {
            "id": "W_in_3",
            "index": "3",
            "allow": "passenger",
            "speed": "13.89",
            "length": "100",
            "shape": "-100,0 -5,6.4",
        },
    )
    junction.set("incLanes", f"{junction.attrib['incLanes']} W_in_3")
    ET.indent(candidate_root, space="    ")
    candidate_tree.write(candidate, encoding="utf-8", xml_declaration=True)

    report = build_connection_mode_regression_audit(
        source,
        candidate,
        output_dir=tmp_path / "regression",
    )

    assert report["status"] == "fail"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["outside_scope_new_review_finding_count"] == 2
    assert report["outside_scope_new_structural_finding_count"] == 0
    assert report["outside_scope_regression_junction_ids"] == ["J0"]
    assert {
        item["category"] for item in report["outside_scope_review_regressions"]
    } >= {
        "incoming_motorized_lane_without_connection",
        "left_turn_not_innermost_lane",
    }
    assert Path(report["report_file"]).is_file()
    assert Path(report["review_overlay_file"]).is_file()
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "fail"
    assert manifest["artifact_generation_status"] == "pass"
    assert manifest["gate_status"] == "fail"
    assert manifest["candidate_sha256"] == file_sha256(candidate)


def test_connection_mode_scope_expands_all_tls_controller_members() -> None:
    audit = {
        "junctions": [
            {"junction_id": "a", "controller_ids": ["tls_main"]},
            {"junction_id": "b", "controller_ids": ["tls_main", "tls_side"]},
            {"junction_id": "c", "controller_ids": ["tls_side"]},
            {"junction_id": "outside", "controller_ids": ["tls_other"]},
        ]
    }

    assert _expand_scope_by_tls_controller(audit, ["a"]) == ["a", "b", "c"]


def test_connection_mode_scope_couples_source_and_candidate_tls_members() -> None:
    source_audit = {
        "junctions": [
            {"junction_id": "source_a", "controller_ids": ["old_tls"]},
            {"junction_id": "shared", "controller_ids": ["old_tls"]},
        ]
    }
    candidate_audit = {
        "junctions": [
            {"junction_id": "shared", "controller_ids": ["new_tls"]},
            {"junction_id": "candidate_cluster", "controller_ids": ["new_tls"]},
        ]
    }

    source_scope, candidate_scope = _expand_coupled_scopes_by_tls_controller(
        source_audit,
        candidate_audit,
        ["source_a"],
        ["shared"],
    )

    assert source_scope == ["candidate_cluster", "shared", "source_a"]
    assert candidate_scope == source_scope


def test_connection_mode_regression_gate_blocks_new_scoped_review_finding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    _write_standard_network(source)
    source_audit = audit_network_connection_mode(ET.parse(source).getroot())
    candidate_audit = json.loads(json.dumps(source_audit))
    target = candidate_audit["junctions"][0]
    target["status"] = "review_required"
    target["connection_mode_audit"]["status"] = "review_required"
    target["connection_mode_audit"]["review_findings"] = [
        "connection_mode:incoming_motorized_lane_without_connection:W_in:3"
    ]

    comparison = compare_connection_mode_audits(
        source_audit,
        candidate_audit,
        target_source_junction_ids=["J0"],
        target_candidate_junction_ids=["J0"],
    )

    assert comparison["status"] == "fail"
    assert comparison["automatic_promotion_gate"] == "blocked"
    assert comparison["outside_scope_new_review_finding_count"] == 0
    assert comparison["target_scope_review_finding_count"] == 1
    assert comparison["target_scope_new_review_finding_count"] == 1
    assert comparison["blockers"] == ["new_target_scope_review_findings"]


def test_connection_mode_regression_gate_allows_unchanged_scoped_review_finding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    _write_standard_network(source)
    source_audit = audit_network_connection_mode(ET.parse(source).getroot())
    target = source_audit["junctions"][0]
    target["status"] = "review_required"
    target["connection_mode_audit"]["status"] = "review_required"
    target["connection_mode_audit"]["review_findings"] = [
        "connection_mode:incoming_motorized_lane_without_connection:W_in:3"
    ]
    candidate_audit = json.loads(json.dumps(source_audit))

    comparison = compare_connection_mode_audits(
        source_audit,
        candidate_audit,
        target_source_junction_ids=["J0"],
        target_candidate_junction_ids=["J0"],
    )

    assert comparison["status"] == "pass"
    assert comparison["automatic_promotion_gate"] == "pass"
    assert comparison["target_scope_review_finding_count"] == 1
    assert comparison["target_scope_new_review_finding_count"] == 0


def test_legacy_regression_gate_does_not_cancel_equal_category_witnesses(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    _write_standard_network(source)
    source_audit = audit_network_connection_mode(ET.parse(source).getroot())
    source_record = source_audit["junctions"][0]
    source_record["connection_mode_audit"]["review_findings"] = [
        "connection_mode:lane_rank_jump:0:0.750"
    ]
    candidate_audit = json.loads(json.dumps(source_audit))
    candidate_audit["junctions"][0]["connection_mode_audit"]["review_findings"] = [
        "connection_mode:lane_rank_jump:7:0.750"
    ]

    comparison = compare_connection_mode_audits(source_audit, candidate_audit)

    assert comparison["status"] == "fail"
    assert comparison["outside_scope_new_review_finding_count"] == 1
    assert comparison["outside_scope_resolved_review_finding_count"] == 1
    assert comparison["outside_scope_review_regressions"][0]["finding_witness"] == (
        "connection_mode:lane_rank_jump:7:0.750"
    )
    assert comparison["outside_scope_review_category_regressions"] == []


def test_network_connection_mode_audit_flags_foe_movements_with_protected_green(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsafe-green.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    junction = root.find("junction[@id='J0']")
    logic = root.find("tlLogic[@id='J0']")
    assert junction is not None
    assert logic is not None
    requests = junction.findall("request")
    movement_count = len(requests)
    first, second = 0, 1
    foe_bits = list(requests[first].attrib["foes"])
    foe_bits[movement_count - second - 1] = "1"
    requests[first].set("foes", "".join(foe_bits))
    phase = logic.findall("phase")[0]
    state = list(phase.attrib["state"])
    state[first] = "G"
    state[second] = "G"
    phase.set("state", "".join(state))

    report = audit_network_connection_mode(root)

    assert report["status"] == "review_required"
    assert report["automatic_promotion_gate"] == "blocked"
    tls = report["tls_link_binding_audit"]
    assert tls["status"] == "review_required"
    assert len(tls["protected_green_foe_conflicts"]) == 1
    conflict = tls["protected_green_foe_conflicts"][0]
    assert conflict["junction_id"] == "J0"
    assert conflict["request_indices"] == [first, second]
    assert any(
        finding.startswith("tls_link_binding:protected_green_foes:J0:J0")
        for finding in tls["review_findings"]
    )


def test_left_hand_connection_audit_uses_explicit_curb_and_inner_roles(
    tmp_path: Path,
) -> None:
    source = tmp_path / "left-hand.net.xml"
    _write_standard_network(source, lefthand=True)
    root = ET.parse(source).getroot()

    audit = audit_network_connection_mode(root)

    assert audit["traffic_side"] == "left"
    assert audit["traffic_side_contract"]["evidence"] == "net_attribute"
    junction_audit = audit["junctions"][0]["connection_mode_audit"]
    west = next(
        check
        for check in junction_audit["lane_order_checks"]
        if check["incoming_edge"] == "W_in"
    )
    assert west["curb_lane_index"] == 2
    assert west["innermost_lane_index"] == 0
    west_right = next(
        check
        for check in junction_audit["movement_checks"]
        if check["from"] == "W_in" and check["turn"] == "r"
    )
    assert west_right["normalized_lane_rank_basis"] == "curb_to_inner"
    assert west_right["normalized_source_lane_rank"] == 0.833333


def test_endpoint_tolerance_is_calibrated_from_precision_and_lane_scale(
    tmp_path: Path,
) -> None:
    source = tmp_path / "calibration.net.xml"
    _write_standard_network(source)
    policy = ConnectionAuditCalibrationPolicy.build(minimum_endpoint_samples=20)

    calibration = calibrate_connection_mode_audit(
        ET.parse(source).getroot(),
        source_sha256=file_sha256(source),
        traffic_side=TrafficSide.RIGHT,
        policy=policy,
    )

    assert calibration.status is GateStatus.PASS
    assert calibration.coordinate_precision_m == 0.1
    assert calibration.lane_width_evidence == "locked_sumo_default_lane_width"
    assert calibration.endpoint_sample_count == 24
    assert calibration.endpoint_tolerance_m == 0.2


def test_endpoint_calibration_excludes_walkingarea_display_distance() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" width="3.2" shape="-10.0,0.0 0.0,0.0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" width="3.2" shape="10.0,0.0 20.0,0.0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" width="3.2" shape="0.0,0.0 10.0,0.0"/>
  </edge>
  <edge id=":p_w0" function="walkingarea">
    <lane id=":p_w0_0" index="0" allow="pedestrian" width="2.0" shape="100.0,0.0 101.0,0.0"/>
  </edge>
  <edge id=":p_c0" function="crossing">
    <lane id=":p_c0_0" index="0" allow="pedestrian" width="4.0" shape="103.0,0.0 104.0,0.0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <junction id="p" type="priority" incLanes=":p_w0_0" intLanes=":p_c0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
  <connection from=":p_w0" to=":p_c0" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    calibration = calibrate_connection_mode_audit(
        root,
        source_sha256="b" * 64,
        traffic_side=TrafficSide.RIGHT,
        policy=ConnectionAuditCalibrationPolicy.build(minimum_endpoint_samples=1),
    )

    assert calibration.status is GateStatus.PASS
    assert calibration.endpoint_path_count == 1
    assert calibration.endpoint_sample_count == 2
    assert calibration.maximum_observed_gap_m == 0.0
    assert calibration.endpoint_tolerance_m == 0.2


def test_endpoint_calibration_blocks_gross_gap_in_source_baseline(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid-calibration.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    internal_lane = root.find("edge[@id=':J0_0']/lane")
    assert internal_lane is not None
    points = internal_lane.attrib["shape"].split()
    points[0] = "10.0,10.0"
    internal_lane.set("shape", " ".join(points))

    calibration = calibrate_connection_mode_audit(
        root,
        source_sha256=file_sha256(source),
        traffic_side=TrafficSide.RIGHT,
        policy=ConnectionAuditCalibrationPolicy.build(minimum_endpoint_samples=1),
    )

    assert calibration.status is GateStatus.BLOCKED
    assert calibration.endpoint_tolerance_m is None
    assert any(
        finding.startswith("baseline_endpoint_gap_exceeds_lane_scale_cap")
        for finding in calibration.findings
    )


def test_explicit_traffic_side_mismatch_is_a_hard_configuration_failure(
    tmp_path: Path,
) -> None:
    source = tmp_path / "left-hand.net.xml"
    _write_standard_network(source, lefthand=True)

    audit = audit_network_connection_mode(
        ET.parse(source).getroot(),
        traffic_side="right",
    )

    assert audit["status"] == "fail"
    assert audit["automatic_promotion_gate"] == "blocked"
    assert audit["configuration_failures"] == [
        "connection_mode:traffic_side_mismatch:requested_right:network_left"
    ]


def test_vehicle_nema_materialization_fails_closed_for_left_hand_network(
    tmp_path: Path,
) -> None:
    source = tmp_path / "left-hand.net.xml"
    _write_standard_network(source, lefthand=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    assert report["status"] == "blocked"
    assert report["candidate_net_file"] == ""
    assert "traffic_side_not_certified_for_vehicle_nema:left" in report[
        "selected_candidate"
    ]["blockers"]


def test_network_connection_mode_audit_fails_invalid_tls_link_index(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid-link-index.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    controlled = next(
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("tl") == "J0"
    )
    controlled.set("linkIndex", "99")

    report = audit_network_connection_mode(root)

    assert report["status"] == "fail"
    assert report["automatic_promotion_gate"] == "blocked"
    tls = report["tls_link_binding_audit"]
    assert tls["status"] == "fail"
    assert any(
        failure.startswith("tls_link_binding:link_index_out_of_program_state")
        for failure in tls["structural_failures"]
    )


def test_network_connection_mode_accepts_runtime_managed_rail_signal_without_tllogic() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="rail_in" from="a" to="rs">
    <lane id="rail_in_0" index="0" allow="rail" shape="-10,0 0,0"/>
  </edge>
  <edge id="rail_out" from="rs" to="b">
    <lane id="rail_out_0" index="0" allow="rail" shape="1,0 10,0"/>
  </edge>
  <edge id=":rs_0" function="internal">
    <lane id=":rs_0_0" index="0" allow="rail" shape="0,0 1,0"/>
  </edge>
  <junction id="rs" type="rail_signal" incLanes="rail_in_0" intLanes=":rs_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from="rail_in" to="rail_out" fromLane="0" toLane="0" via=":rs_0_0" tl="rs" linkIndex="-1" dir="s"/>
  <connection from=":rs_0" to="rail_out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    report = audit_network_connection_mode(root)

    assert report["status"] == "pass"
    tls = report["tls_link_binding_audit"]
    assert tls["status"] == "pass"
    assert tls["runtime_managed_rail_connection_count"] == 1
    assert tls["runtime_managed_rail_controller_ids"] == ["rs"]
    controller = tls["controllers"][0]
    assert controller["control_semantics"] == "sumo_runtime_rail_signal"
    assert controller["programs"] == []


def test_no_internal_links_network_is_a_legal_explicit_network_mode() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes="">
    <request index="0" response="0" foes="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" dir="s" state="M"/>
</net>"""
    )

    report = audit_network_connection_mode(root)

    assert report["status"] == "pass"
    assert report["internal_link_mode"] == "no-internal-links"
    junction = next(item for item in report["junctions"] if item["junction_id"] == "j")
    path = junction["connection_mode_audit"]["movement_checks"][0]["internal_path"]
    assert path["status"] == "pass"
    assert path["path_kind"] == "direct_no_internal_links"
    assert path["endpoint_gap_policy"] == "not_available_in_network_mode"


def test_internal_path_requires_a_common_vehicle_permission() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="10,0 20,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="bicycle" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    report = audit_network_connection_mode(root)

    assert report["status"] == "fail"
    junction = next(item for item in report["junctions"] if item["junction_id"] == "j")
    audit = junction["connection_mode_audit"]
    assert audit["structural_failures"] == [
        "connection_mode:internal_path_mode_permission_empty:0"
    ]
    path = audit["movement_checks"][0]["internal_path"]
    assert path["path_permission_status"] == "fail"
    assert path["path_permission_basis"] == "explicit_allow_intersection"
    assert path["path_permission_overlap"] == []


def test_path_permission_intersection_preserves_wildcard_semantics() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger bicycle" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" disallow="pedestrian" shape="10,0 20,0"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="bicycle" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes=":j_0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" via=":j_0_0" dir="s"/>
  <connection from=":j_0" to="out" fromLane="0" toLane="0" dir="s"/>
</net>"""
    )

    report = audit_network_connection_mode(root)

    assert report["status"] == "pass"
    junction = next(item for item in report["junctions"] if item["junction_id"] == "j")
    path = junction["connection_mode_audit"]["movement_checks"][0]["internal_path"]
    assert path["path_permission_status"] == "pass"
    assert path["path_permission_basis"] == "explicit_allow_intersection"
    assert path["path_permission_overlap"] == ["bicycle"]


def test_no_internal_links_connection_permission_can_block_the_path() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in" from="a" to="j">
    <lane id="in_0" index="0" allow="passenger" shape="-10,0 0,0"/>
  </edge>
  <edge id="out" from="j" to="b">
    <lane id="out_0" index="0" allow="passenger" shape="0,0 10,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="j" type="priority" incLanes="in_0" intLanes="">
    <request index="0" response="0" foes="0"/>
  </junction>
  <junction id="b" type="dead_end" incLanes="out_0" intLanes=""/>
  <connection from="in" to="out" fromLane="0" toLane="0" allow="bicycle" dir="s" state="M"/>
</net>"""
    )

    report = audit_network_connection_mode(root)

    assert report["status"] == "fail"
    junction = next(item for item in report["junctions"] if item["junction_id"] == "j")
    path = junction["connection_mode_audit"]["movement_checks"][0]["internal_path"]
    assert path["path_kind"] == "direct_no_internal_links"
    assert path["path_permission_status"] == "fail"
    assert path["path_permission_overlap"] == []


def test_missing_via_in_mixed_internal_link_network_remains_a_hard_failure() -> None:
    root = ET.fromstring(
        """<net>
  <edge id="in0" from="a" to="j">
    <lane id="in0_0" index="0" allow="passenger" shape="-10,0 -1,0"/>
  </edge>
  <edge id="out0" from="j" to="b">
    <lane id="out0_0" index="0" allow="passenger" shape="1,0 10,0"/>
  </edge>
  <edge id="in1" from="c" to="j">
    <lane id="in1_0" index="0" allow="passenger" shape="0,-10 0,-1"/>
  </edge>
  <edge id="out1" from="j" to="d">
    <lane id="out1_0" index="0" allow="passenger" shape="0,1 0,10"/>
  </edge>
  <edge id=":j_0" function="internal">
    <lane id=":j_0_0" index="0" allow="passenger" shape="-1,0 1,0"/>
  </edge>
  <junction id="a" type="dead_end" incLanes="" intLanes=""/>
  <junction id="b" type="dead_end" incLanes="out0_0" intLanes=""/>
  <junction id="c" type="dead_end" incLanes="" intLanes=""/>
  <junction id="d" type="dead_end" incLanes="out1_0" intLanes=""/>
  <junction id="j" type="priority" incLanes="in0_0 in1_0" intLanes=":j_0_0">
    <request index="0" response="00" foes="00" cont="0"/>
    <request index="1" response="00" foes="00" cont="0"/>
  </junction>
  <connection from="in0" to="out0" fromLane="0" toLane="0" via=":j_0_0" dir="s" state="M"/>
  <connection from="in1" to="out1" fromLane="0" toLane="0" dir="s" state="M"/>
  <connection from=":j_0" to="out0" fromLane="0" toLane="0" dir="s" state="M"/>
</net>"""
    )

    report = audit_network_connection_mode(root)

    assert report["status"] == "fail"
    assert report["internal_link_mode"] == "mixed"
    failures = [
        failure
        for junction in report["junctions"]
        for failure in junction["connection_mode_audit"]["structural_failures"]
    ]
    assert any(
        failure.startswith("connection_mode:missing_direct_via_lane")
        for failure in failures
    )


def test_walkingarea_to_crossing_direct_link_does_not_require_via() -> None:
    root = ET.fromstring(
        """<net>
  <edge id=":j_w0" function="walkingarea">
    <lane id=":j_w0_0" index="0" allow="pedestrian" shape="0,0 1,0"/>
  </edge>
  <edge id=":j_c0" function="crossing">
    <lane id=":j_c0_0" index="0" allow="pedestrian" shape="1,0 2,0"/>
  </edge>
  <junction id="j" type="traffic_light" incLanes=":j_w0_0" intLanes=":j_c0_0">
    <request index="0" response="0" foes="0" cont="0"/>
  </junction>
  <connection from=":j_w0" to=":j_c0" fromLane="0" toLane="0" tl="j" linkIndex="0" dir="s"/>
</net>"""
    )

    audit = audit_standard_connection_mode(
        root,
        junction_id="j",
        movement_rows=[],
        layout_type="unknown",
    )

    assert audit["status"] == "pass"
    assert audit["direct_movement_count"] == 1
    assert audit["motorized_direct_movement_count"] == 0
    path = audit["movement_checks"][0]["internal_path"]
    assert path["status"] == "pass"
    assert path["path_kind"] == "direct_walkingarea_to_crossing"
    assert path["endpoint_gap_policy"] == "nearest_endpoint_diagnostic_only"
    assert path["max_endpoint_gap_m"] == 0.0


def test_vehicle_movement_requires_motorized_source_and_target_lanes(tmp_path: Path) -> None:
    source = tmp_path / "bicycle-target.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    bicycle_target = root.find("edge[@id='S_out']/lane[@index='0']")
    assert bicycle_target is not None
    bicycle_target.set("allow", "bicycle")
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    scan = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "scan",
        run_runtime_checks=False,
    )

    candidate = scan["candidates"][0]
    assert "unsupported_non_motorized_direct_movements:1" in candidate["blockers"]
    assert not any(
        movement["from"] == "W_in" and movement["to"] == "S_out"
        for movement in candidate["movement_map"]
    )


def test_turnaround_blocks_automatic_nema_materialization(tmp_path: Path) -> None:
    source = tmp_path / "turnaround.net.xml"
    _write_standard_network(source, include_turnaround=True)
    source_sha256 = file_sha256(source)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    assert report["status"] == "blocked"
    assert report["nema_binding_status"] == "blocked_ineligible"
    assert report["candidate_net_file"] == ""
    assert any(blocker.startswith("turnaround_movement_present") for blocker in report["selected_candidate"]["blockers"])
    turnaround = next(
        movement
        for movement in report["selected_candidate"]["movement_map"]
        if movement["geometry_dir"] == "t"
    )
    assert turnaround["nema_phase"] is None
    assert turnaround["new_linkIndex"] is None
    assert turnaround["effective_dir"] == "t"
    assert file_sha256(source) == source_sha256
    assert not (tmp_path / "candidate" / "standard_nema_binding.candidate.net.xml").exists()


def test_explicit_turnaround_overrides_split_junction_turn_geometry(tmp_path: Path) -> None:
    source = tmp_path / "split-turnaround.net.xml"
    _write_standard_network(source, arm_names=("W", "E", "S"))
    root = ET.parse(source).getroot()
    split_turnaround = next(
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("from") == "W_in"
        and connection.attrib.get("to") == "S_out"
        and connection.attrib.get("tl") == "J0"
    )
    split_turnaround.set("dir", "t")
    split_turnaround.set("fromLane", "1")
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    candidate = report["selected_candidate"]
    movement = next(
        row
        for row in candidate["movement_map"]
        if row["from"] == "W_in" and row["to"] == "S_out"
    )
    assert report["status"] == "blocked"
    assert movement["source_dir"] == "t"
    assert movement["geometry_dir"] == "r"
    assert movement["effective_dir"] == "t"
    assert movement["nema_phase"] is None
    assert movement["new_linkIndex"] is None
    audit = candidate["connection_mode_audit"]
    assert not any("right_turn_not_curb_lane:W_in" in blocker for blocker in audit["blockers"])
    request = next(
        row
        for row in audit["request_foe_audit"]["request_bindings"]
        if row["connection_index"] == movement["connection_index"]
    )
    assert request["turn"] == "t"
    assert request["nema_phase"] is None


def test_direction_geometry_mismatch_is_not_relabelled_for_lane_order(tmp_path: Path) -> None:
    source = tmp_path / "direction-mismatch.net.xml"
    _write_standard_network(source, arm_names=("W", "E", "S"))
    root = ET.parse(source).getroot()
    movement = next(
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("from") == "W_in"
        and connection.attrib.get("to") == "S_out"
        and connection.attrib.get("tl") == "J0"
    )
    movement.set("dir", "s")
    movement.set("fromLane", "1")
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    candidate = report["selected_candidate"]
    row = next(
        item
        for item in candidate["movement_map"]
        if item["from"] == "W_in" and item["to"] == "S_out"
    )
    assert report["status"] == "blocked"
    assert row["source_dir"] == "s"
    assert row["geometry_dir"] == "r"
    assert row["effective_dir"] == "s"
    assert row["nema_phase"] is None
    assert any(
        blocker.startswith(f"movement_turn_geometry_mismatch:{row['connection_index']}:s:r")
        for blocker in candidate["blockers"]
    )
    audit = candidate["connection_mode_audit"]
    assert not any("right_turn_not_curb_lane:W_in" in blocker for blocker in audit["blockers"])
    request = next(
        item
        for item in audit["request_foe_audit"]["request_bindings"]
        if item["connection_index"] == row["connection_index"]
    )
    assert request["turn"] == "s"
    assert request["nema_phase"] is None


def test_shared_left_lane_is_not_assigned_a_protected_nema_phase(tmp_path: Path) -> None:
    source = tmp_path / "shared-left.net.xml"
    _write_standard_network(source)
    root = ET.parse(source).getroot()
    west_right = next(
        connection
        for connection in root.findall("connection")
        if connection.attrib.get("from") == "W_in"
        and connection.attrib.get("dir") == "r"
        and connection.attrib.get("tl") == "J0"
    )
    west_right.set("fromLane", "2")
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=False,
    )

    candidate = report["selected_candidate"]
    west_left = next(
        row
        for row in candidate["movement_map"]
        if row["from"] == "W_in" and row["effective_dir"] == "l"
    )
    assert report["status"] == "blocked"
    assert west_left["nema_phase"] is None
    assert west_left["new_linkIndex"] is None
    assert west_left["nema_assignment_status"] == "blocked_shared_left_lane"
    assert any(
        blocker.startswith("protected_left_lane_not_dedicated:W_in:2")
        for blocker in candidate["blockers"]
    )


def test_runtime_gates_cover_netconvert_sumo_load_and_routeability(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    _write_standard_network(source)
    commands: list[list[str]] = []

    def fake_command_runner(command: list[str], **_kwargs) -> CommandResult:
        commands.append(command)
        if command[0] == "netconvert":
            shutil.copyfile(command[command.index("-s") + 1], command[command.index("-o") + 1])
        return CommandResult(command=command, cwd=None, status="pass", returncode=0)

    def fake_routeability(**kwargs):
        assert kwargs["net_file"].name.endswith(".netconvert.net.xml")
        assert kwargs["vehicle_count"] == 12
        return {
            "status": "pass",
            "routeability_status": "pass",
            "warnings": [],
        }

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=True,
        run_routeability=True,
        netconvert_binary="netconvert",
        sumo_binary="sumo",
        random_trips_script="randomTrips.py",
        command_runner=fake_command_runner,
        routeability_runner=fake_routeability,
    )

    assert report["status"] == "pass"
    assert report["runtime_validation"]["status"] == "pass"
    assert report["runtime_validation"]["netconvert_status"] == "pass"
    assert report["runtime_validation"]["netconvert_semantic_status"] == "pass"
    assert report["runtime_validation"]["roundtrip_semantic_validation"]["verified_binding_count"] == 12
    assert report["runtime_validation"]["sumo_load_status"] == "pass"
    assert report["runtime_validation"]["routeability_status"] == "pass"
    assert commands[0][0] == "netconvert"
    assert commands[1][0] == "sumo"


def test_netconvert_roundtrip_signal_group_drift_blocks_sumo_and_routeability(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    _write_standard_network(source)
    commands: list[list[str]] = []
    routeability_called = False

    def fake_command_runner(command: list[str], **_kwargs) -> CommandResult:
        commands.append(command)
        assert command[0] == "netconvert"
        destination = Path(command[command.index("-o") + 1])
        shutil.copyfile(command[command.index("-s") + 1], destination)
        root = ET.parse(destination).getroot()
        root.findall("connection")[0].set("linkIndex", "99")
        ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
        return CommandResult(command=command, cwd=None, status="pass", returncode=0)

    def fake_routeability(**_kwargs):
        nonlocal routeability_called
        routeability_called = True
        return {"status": "pass", "routeability_status": "pass", "warnings": []}

    report = build_standard_nema_phase_binding(
        source,
        output_dir=tmp_path / "candidate",
        junction_id="J0",
        run_runtime_checks=True,
        run_routeability=True,
        netconvert_binary="netconvert",
        sumo_binary="sumo",
        random_trips_script="randomTrips.py",
        command_runner=fake_command_runner,
        routeability_runner=fake_routeability,
    )

    assert report["status"] == "blocked"
    assert report["nema_binding_status"] == "candidate_failed_validation"
    assert report["runtime_validation"]["netconvert_status"] == "pass"
    assert report["runtime_validation"]["netconvert_semantic_status"] == "fail"
    assert report["runtime_validation"]["sumo_load_status"] == "blocked"
    assert any(
        failure.startswith("roundtrip_link_index_mismatch")
        for failure in report["runtime_validation"]["roundtrip_semantic_validation"]["failures"]
    )
    assert len(commands) == 1
    assert routeability_called is False
