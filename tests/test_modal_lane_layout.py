import gzip
import shutil
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_edit_operations import _lane_count_layout, validate_scoped_operation


def _road():
    path = Path(__file__).parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    root = ET.fromstring(gzip.decompress(path.read_bytes()))
    return root, root.find("edge[@id='554713076#0']")


@pytest.mark.parametrize(
    "mode,expected", [("passenger", {0: 0, 1: 1, 2: 3, 3: 4}), ("bicycle", {0: 0, 1: 2, 2: 3, 3: 4})]
)
def test_class_selected_right_addition_preserves_outer_facility_indices(mode, expected):
    _, road = _road()
    params = dict(edge_id=road.get("id"), num_lanes=5, add_side="right", vehicle_class=mode)
    assert _lane_count_layout(params, road) == expected


def test_class_selected_reduction_retains_sidewalk_and_cycle_lane():
    _, road = _road()
    assert _lane_count_layout(dict(num_lanes=3, vehicle_class="passenger"), road) == {0: 0, 1: 1, 2: 2}
    assert _lane_count_layout(dict(num_lanes=3, remove_index=2, vehicle_class="passenger"), road) == {0: 0, 1: 1, 3: 2}


def test_class_selection_rejects_removal_of_another_facility():
    _, road = _road()
    with pytest.raises(ValueError, match="selected vehicle class"):
        _lane_count_layout(dict(num_lanes=3, remove_index=0, vehicle_class="passenger"), road)


@pytest.mark.parametrize("mode", ["not-a-class", "passenger bus", "", None])
def test_invalid_lane_class_is_rejected(mode):
    root, road = _road()
    result = validate_scoped_operation(
        dict(operation="set_lane_count", params=dict(edge_id=road.get("id"), num_lanes=5, vehicle_class=mode)), root
    )
    assert result


def test_legacy_raw_lane_index_semantics_stay_explicit():
    _, road = _road()
    assert _lane_count_layout(dict(num_lanes=5, add_side="right"), road) == {0: 1, 1: 2, 2: 3, 3: 4}
    assert _lane_count_layout(dict(num_lanes=3, remove_index=0), road) == {1: 0, 2: 1, 3: 2}


def _access_case():
    return ET.fromstring(
        '<net version="1.20"><edge id="in" from="a" to="j">'
        '<lane id="in_0" index="0" allow="pedestrian"/><lane id="in_1" index="1" allow="bicycle"/>'
        '<lane id="in_2" index="2" allow="passenger"/></edge>'
        '<edge id="out" from="j" to="z"><lane id="out_0" index="0" allow="bicycle"/></edge>'
        '<edge id=":j_w0" function="walkingarea"><lane id=":j_w0_0" index="0" allow="pedestrian"/></edge>'
        '<junction id="j" type="priority"/>'
        '<connection from="in" fromLane="0" to=":j_w0" toLane="0"/>'
        '<connection from="in" fromLane="1" to="out" toLane="0"/></net>'
    )


def test_raw_sidewalk_duplication_is_a_source_relative_failure():
    from astra_sumo.core.network_nonmotor_access import compare_nonmotor_access

    before = _access_case()
    after = deepcopy(before)
    edge = after.find("edge[@id='in']")
    for index, lane in enumerate(edge.findall("lane")):
        lane.set("id", f"in_{index + 1}")
        lane.set("index", str(index + 1))
    extra = deepcopy(edge.find("lane"))
    extra.set("id", "in_0")
    extra.set("index", "0")
    edge.insert(0, extra)
    after.findall("connection")[1].set("fromLane", "2")
    plan = dict(
        operation=dict(operation="set_lane_count", params=dict(edge_id="in", num_lanes=4, add_side="right")),
        mutable_edge_ids=["in"],
        lane_index_mapping={"0": 1, "1": 2, "2": 3},
    )
    result = compare_nonmotor_access(before, after, plan)
    assert result["status"] == "fail"
    assert result["new_multiple_sidewalks"]
    assert any(c["source_lane"] == "in_0" for c in result["changes"])


def test_renamed_walking_area_keeps_junction_access():
    from astra_sumo.core.network_nonmotor_access import compare_nonmotor_access

    before = _access_case()
    after = deepcopy(before)
    edge = after.find("edge[@id=':j_w0']")
    edge.set("id", ":j_w1")
    edge.find("lane").set("id", ":j_w1_0")
    after.find("connection").set("to", ":j_w1")
    plan = dict(operation=dict(operation="set_lane_width", params={}), mutable_edge_ids=["in"])
    result = compare_nonmotor_access(before, after, plan)
    assert result["status"] == "pass"


def test_declared_physical_sidewalk_removal_is_reported():
    from astra_sumo.core.network_nonmotor_access import compare_nonmotor_access

    before = _access_case()
    after = deepcopy(before)
    edge = after.find("edge[@id='in']")
    edge.remove(edge.find("lane"))
    for index, lane in enumerate(edge.findall("lane")):
        lane.set("id", f"in_{index}")
        lane.set("index", str(index))
    after.remove(after.find("connection"))
    after.find("connection").set("fromLane", "0")
    plan = dict(
        operation=dict(operation="set_lane_count", params=dict(edge_id="in", num_lanes=2, remove_index=0)),
        mutable_edge_ids=["in"],
        lane_index_mapping={"1": 0, "2": 1},
    )
    result = compare_nonmotor_access(before, after, plan)
    assert result["status"] == "pass" and result["declared_removed_lane_ids"] == ["in_0"]


def test_declared_bicycle_turn_removal_is_not_an_unrequested_access_loss():
    from astra_sumo.core.network_nonmotor_access import compare_nonmotor_access

    before = _access_case()
    after = deepcopy(before)
    after.remove(after.findall("connection")[1])
    plan = dict(
        operation=dict(
            operation="delete_lane_connection", params={"from": "in", "fromLane": 1, "to": "out", "toLane": 0}
        ),
        mutable_edge_ids=["in", "out"],
    )
    result = compare_nonmotor_access(before, after, plan)
    assert result["status"] == "pass" and len(result["declared_removed_connections"]) == 1


def test_pedestrian_connection_direction_does_not_change_bidirectional_access():
    from astra_sumo.core.network_nonmotor_access import compare_nonmotor_access

    before = _access_case()
    after = deepcopy(before)
    after.find("connection").attrib.update({"from": ":j_w0", "fromLane": "0", "to": "in", "toLane": "0"})
    plan = dict(operation=dict(operation="set_lane_width", params={}), mutable_edge_ids=["in"])
    assert compare_nonmotor_access(before, after, plan)["status"] == "pass"


@pytest.mark.parametrize("binding", [True, False])
def test_new_controlled_lane_movement_requires_an_explicit_compatible_binding(binding):
    root, road = _road()
    row = dict(fromLane=2, toLane=1, tl="LSA2349_part0", linkIndex=1)
    row.update({"from": road.get("id"), "to": "127716467#1"})
    if not binding:
        row.pop("tl")
        row.pop("linkIndex")
    operation = dict(
        operation="set_lane_count",
        params=dict(
            edge_id=road.get("id"), num_lanes=5, add_side="right", vehicle_class="passenger", connections=[row]
        ),
    )
    errors = validate_scoped_operation(operation, root)
    if binding:
        assert errors == []
    else:
        assert errors and "signal binding" in str(errors)


@pytest.mark.parametrize(
    "binding",
    [
        {"tl": "LSA2349_part0"},
        {"linkIndex": 1},
        {"tl": "LSA2349_part0", "linkIndex": 9999},
        {"tl": "LSA2394_part0", "linkIndex": 1},
    ],
)
def test_new_signal_bindings_are_validated_against_source_programs(binding):
    root, road = _road()
    row = {"from": road.get("id"), "fromLane": 2, "to": "127716467#1", "toLane": 1, **binding}
    result = validate_scoped_operation(
        {
            "operation": "set_lane_count",
            "params": {
                "edge_id": road.get("id"),
                "num_lanes": 5,
                "add_side": "right",
                "vehicle_class": "passenger",
                "connections": [row],
            },
        },
        root,
    )
    assert result


def test_modal_lane_request_matches_published_schema():
    import json
    from jsonschema import Draft202012Validator

    path = Path(__file__).parents[1] / "schemas/product/astra.network-edit-ledger.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))

    # Select the operation contract so unrelated ledger evidence is not part of this schema check.
    def find(value):
        if isinstance(value, dict):
            if value.get("properties", {}).get("operation", {}).get("const") == "set_lane_count":
                return value
            for child in value.values():
                result = find(child)
                if result is not None:
                    return result
        elif isinstance(value, list):
            for child in value:
                result = find(child)
                if result is not None:
                    return result
        return None

    contract = find(schema)
    assert contract is not None
    Draft202012Validator(contract).validate(
        {
            "operation": "set_lane_count",
            "params": {
                "edge_id": "road",
                "num_lanes": 5,
                "vehicle_class": "passenger",
                "add_side": "right",
                "connections": [
                    {"from": "road", "fromLane": 2, "to": "out", "toLane": 1, "tl": "signal", "linkIndex": 1}
                ],
            },
        }
    )


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO binaries required")
def test_new_passenger_lane_has_explicit_signalled_movement_and_native_vehicle(tmp_path):
    from astra_sumo.core.network_edit_operations import _inventory, _lane_count_connections
    from astra_sumo.core.network_edit_operations import explore_network_edit
    from astra_sumo.core.candidate_contracts import file_sha256
    from astra_sumo.core.command_runner import run_command

    root, road = _road()
    source = tmp_path / "source.net.xml"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    digest = file_sha256(source)
    edges, _, connections = _inventory(root)
    params = dict(edge_id=road.get("id"), num_lanes=5, add_side="right", vehicle_class="passenger")
    records = _lane_count_connections(params, edges, connections, network_version=root.get("version"))
    supported = {"from", "fromLane", "to", "toLane", "allow", "disallow", "tl", "linkIndex", "linkIndex2"}
    params["connections"] = [
        {
            key: int(value) if key in {"fromLane", "toLane", "linkIndex", "linkIndex2"} else value
            for key, value in row.items()
            if key in supported
        }
        for row in records
    ]
    params["connections"].append(
        {"from": road.get("id"), "fromLane": 2, "to": "127716467#1", "toLane": 1, "tl": "LSA2349_part0", "linkIndex": 1}
    )
    operation = dict(
        id="new-motor-lane",
        operation="set_lane_count",
        status="accepted",
        params=params,
        rationale="Native construction regression with an explicit existing signal group.",
        evidence=[dict(path=str(source), sha256=digest)],
        rollback=dict(action="discard_candidate"),
    )
    import json

    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(dict(source_net_file=str(source), source_sha256=digest, operations=[operation])), encoding="utf-8"
    )
    report = explore_network_edit(
        ledger_file=ledger, output_dir=tmp_path / "candidate", netconvert_binary=shutil.which("netconvert")
    )
    assert report["status"] == "measured", report
    assert report["construction"]["nonmotor_access_preservation"]["status"] == "pass"
    assert report["construction"]["signal_program_preservation"]["status"] == "pass"
    assert report["construction"]["release_checks_passed"] is False
    assert report["construction"]["new_protected_green_conflicts"]
    routes = tmp_path / "vehicle.rou.xml"
    routes.write_text(
        '<routes><vType id="driver" vClass="passenger" sigma="0" speedDev="0"/>'
        '<vehicle id="new-lane-car" type="driver" depart="0" departLane="2"><route edges="554713076#0 127716467#1"/></vehicle></routes>',
        encoding="utf-8",
    )
    trips = tmp_path / "tripinfo.xml"
    result = run_command(
        [
            shutil.which("sumo"),
            "-n",
            report["candidate_net_file"],
            "-r",
            str(routes),
            "--end",
            "600",
            "--tripinfo-output",
            str(trips),
            "--no-step-log",
            "true",
        ],
        timeout_seconds=30,
    )
    assert result.status == "pass", result
    trip = ET.parse(trips).getroot().find("tripinfo")
    assert trip is not None and trip.get("id") == "new-lane-car"
    assert trip.get("departLane") == "554713076#0_2" and float(trip.get("arrival")) > 0
    assert file_sha256(source) == digest
