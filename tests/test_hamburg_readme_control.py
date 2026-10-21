import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_readme_control import embedded_group_bridge, group_state, intersect_group_states
from astra_sumo.core.hamburg_control_probe import compare_clock_outputs


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
