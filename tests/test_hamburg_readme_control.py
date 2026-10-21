import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_readme_control import embedded_group_bridge, group_state, intersect_group_states
from astra_sumo.core.hamburg_control_probe import compare_clock_outputs
from astra_sumo.core.hamburg_control_probe import _native_intergreens
from astra_sumo.core.hamburg_control_inventory import path_proximity
from astra_sumo.core.hamburg_control_fallback import fallback_decision
from shapely.geometry import LineString


def test_bridge_uses_native_vt_and_keeps_primary_secondary_intersection():
    root = ET.fromstring("""<root><Signalgruppe><BezeichnungKurz>K1</BezeichnungKurz><OCITOutstationNr>1</OCITOutstationNr></Signalgruppe>
      <Signalgruppe><BezeichnungKurz>K7</BezeichnungKurz><OCITOutstationNr>10</OCITOutstationNr></Signalgruppe>
      <TrafficStreamConfigData><intersectionPart>0</intersectionPart><refLaneId>5</refLaneId><refConnectTo>6</refConnectTo>
      <signalGroups><primary><vt>1</vt></primary><secondary><vt>10</vt></secondary></signalGroups></TrafficStreamConfigData></root>""")
    assert embedded_group_bridge(root)[("0", "5", "6")] == {"K1", "K7"}
    root.find(".//primary/vt").text = "99"
    with pytest.raises(ValueError, match="undefined vt"):
        embedded_group_bridge(root)


def test_dark_only_omission_is_declared_but_other_omissions_remain_unresolved():
    group = {"status": "unresolved", "group_id": "K3", "allowed_image_securing_states": {"00": ["release", "stop"]}}
    assert group_state(group, 0) == "O"
    group["allowed_image_securing_states"] = {"03": ["stop"]}
    with pytest.raises(ValueError, match="unresolved"):
        group_state(group, 0)


def test_dark_stop_image_is_not_reinterpreted_as_dark_release():
    group = {
        "status": "measured",
        "allowed_image_securing_states": {"00": ["stop"], "30": ["release"]},
        "intervals": [{"begin_s": 0, "end_s": 90, "signal_image": "00", "securing_states": ["stop"]}],
    }
    assert group_state(group, 0) == "r"


def test_transitions_retain_their_source_durations_and_composed_links_require_both_releases():
    group = {
        "status": "measured",
        "intervals": [
            {"begin_s": 0, "end_s": 1, "signal_image": "0F", "securing_states": ["stop"]},
            {"begin_s": 1, "end_s": 10, "signal_image": "30", "securing_states": ["release"]},
            {"begin_s": 10, "end_s": 13, "signal_image": "0C", "securing_states": ["stop"]},
            {"begin_s": 13, "end_s": 90, "signal_image": "03", "securing_states": ["stop"]},
        ],
    }
    assert [group_state(group, t) for t in [0, 0.9, 1, 9.9, 10, 12.9, 13]] == ["u", "u", "G", "G", "y", "y", "r"]
    assert intersect_group_states(["G", "O"]) == "G"
    assert intersect_group_states(["r", "O"]) == "r"
    assert intersect_group_states(["u", "O"]) == "u"
    assert intersect_group_states(["G", "y"]) == "y"


def test_clock_comparison_tolerates_only_continuous_statistics_not_integer_counts():
    first = {
        "trips": [{"id": "v", "arrival": 90.0}],
        "counts": [{"id": "field", "begin": 0.0, "end": 900.0, "nVehContrib": "1", "nVehEntered": "1", "speed": "10"}],
    }
    second = {"trips": first["trips"], "counts": [{**first["counts"][0], "speed": "10.000000000002"}]}
    report = compare_clock_outputs(first, second)
    assert report["cohort_outputs_equal"] and not report["full_raw_statistics_equal"]
    second["counts"][0]["nVehContrib"] = "2"
    assert not compare_clock_outputs(first, second)["cohort_outputs_equal"]


def test_geometry_screen_keeps_endpoint_merges_separate_from_interior_proximity():
    line = LineString([(0, 0), (20, 0)])
    assert not path_proximity(line, LineString([(20, 0), (20, 20)]))["proximity_flag"]
    assert path_proximity(line, LineString([(0, 10), (20, -10)]))["centerlines_cross"]
    assert path_proximity(line, LineString([(0, 10), (20, -10)]))["proximity_flag"]


def test_fallback_inherits_one_lane_head_but_exposes_multiple_groups():
    row = {
        "physical_key": ["a", 1, "b", 0],
        "tls": "LSA118_part0",
        "pool_lane_feasible_route_ids": ["r"],
        "same_approach_proximity_without_foe": False,
    }
    binding = {
        "physical_key": ["a", 1, "c", 0],
        "tls": "LSA118_part0",
        "origin": "embedded_official_vt",
        "groups": [["118", "K3"]],
    }
    assert fallback_decision(row, [binding]) == ("inherited_lane_head", ("118", "K3"))
    binding["groups"].append(["118", "K4"])
    assert fallback_decision(row, [binding])[0] == "pending_multiple_lane_groups"
    assert fallback_decision(row, [])[0] == "declared_unsignalized_yield"
    row["pool_lane_feasible_route_ids"] = []
    assert fallback_decision(row, [])[0] == "unused_dark_priority"


def test_native_intergreen_readback_counts_dark_yield_as_release(tmp_path):
    import json

    (tmp_path / "inputs").mkdir()
    net = tmp_path / "inputs/network.xml"
    net.write_text("<net>" + "".join(f'<edge id="{e}"><lane id="{e}0" index="0"/></edge>' for e in "abcd") + "</net>")
    rows = [
        {"tls": "LSA119_part0", "physical_key": [a, 0, b, 0], "via": via, "link_index": i, "groups": [["119", group]]}
        for i, (a, b, via, group) in enumerate([("a", "b", "via0", "K7"), ("c", "d", "via1", "K6")])
    ]
    (tmp_path / "link-bindings.json").write_text(json.dumps({"rows": rows}))
    for node in ("104", "118", "119", "200", "535"):
        matrix = (
            (
                "<SicherheitsrelevanteZwischenzeitenmatrix><ZwiZt><Raeumer>K7</Raeumer>"
                "<Einfahrer>K6</Einfahrer><T>5</T></ZwiZt></SicherheitsrelevanteZwischenzeitenmatrix>"
            )
            if node == "119"
            else ""
        )
        (tmp_path / f"inputs/ocit_{node}.xml").write_text("<source>" + matrix + "</source>")
    readback = {
        "programs": {
            "LSA119_part0": {
                "controlled_links": [[("a0", "b0", "via0")], [("c0", "d0", "via1")]],
                "phases": [
                    {"duration": 45, "state": "or"},
                    {"duration": 5, "state": "rr"},
                    {"duration": 40, "state": "rG"},
                ],
            }
        }
    }
    result = _native_intergreens(tmp_path, net, readback)["119"]["bound_rows"][0]
    assert result["status"] == "satisfied" and result["minimum_intergreen_s"] == 5
