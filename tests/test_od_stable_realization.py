from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_departure_probe import apply_native_count_retiming
from astra_sumo.core.od_stable_realization import propose_stable_od_retiming


def fixture():
    cells = [dict(origin=o, destination="exit", begin_s=10*h, end_s=10*(h+1))
             for h in range(3) for o in ("A", "B")]
    counts, times = [2, 2, 4, 2, 2, 4], [[1, 3], [2, 4], [11, 13, 15, 17], [12, 18], [21, 27], [22, 24, 26, 28]]
    requests = [dict(vehicle_id=f"c{cell}v{k}", od_cell=cell, requested_depart_s=t)
                for cell, values in enumerate(times) for k, t in enumerate(values)]
    return dict(requests=requests, cells=cells, target=[2, 2, 2, 3, 4, 3], frozen_before_s=10), counts


def test_equal_quantiles_and_sorted_same_od_matching_are_deterministic():
    data, counts = fixture()
    before = deepcopy(data)
    result = propose_stable_od_retiming(**data)
    assert data == before
    assert result["prior"] == counts and result["target"] == data["target"]
    assert result["changed_vehicles"] == 3 and result["l1_cell_change"] == 6
    assert {(r["vehicle_id"], r["proposed_depart_s"]) for r in result["proposals"]} == {
        ("c2v1", 21), ("c2v3", 27), ("c5v2", 15)}
    assert not result["initial_requests_changed"] and result["native_runs"] == 0
    assert result["max_absolute_shift_s"] == 11
    assert result["median_absolute_shift_s"] == 10
    assert propose_stable_od_retiming(**(data | {"requests": list(reversed(data["requests"]))})) == result


def test_apply_changes_only_selected_departures_and_retains_all_xml():
    data, _ = fixture()
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", id="car", length="5")
    for r in data["requests"]:
        vehicle = ET.SubElement(routes, "vehicle", id=r["vehicle_id"], depart=str(r["requested_depart_s"]), type="car")
        ET.SubElement(vehicle, "route", edges="entry exit")
        ET.SubElement(vehicle, "stop", lane="entry_0", endPos="10", speed="13.888889")
    result = propose_stable_od_retiming(**data)
    changed = apply_native_count_retiming(routes, result["proposals"])
    old, new = [{r.get("id"): r for r in tree.findall("vehicle")} for tree in (routes, changed)]
    assert old.keys() == new.keys()
    selected = {r["vehicle_id"] for r in result["proposals"]}
    for identity, original in old.items():
        after = deepcopy(new[identity])
        if identity not in selected:
            assert ET.tostring(after) == ET.tostring(original)
        after.set("depart", original.get("depart"))
        assert ET.tostring(after) == ET.tostring(original)
    assert ET.tostring(routes.find("vType")) == ET.tostring(changed.find("vType"))


def test_same_target_requires_no_retiming():
    data, counts = fixture()
    result = propose_stable_od_retiming(**(data | {"target": counts}))
    assert result["proposals"] == [] and result["changed_vehicles"] == 0


def test_initialization_report_uses_the_same_native_milliseconds_as_cell_membership():
    cells = [dict(origin="A", destination="exit", begin_s=a, end_s=b)
             for a, b in ((0, 3600), (3600, 4500), (4500, 5400))]
    requests = [dict(vehicle_id=i, od_cell=c, requested_depart_s=d)
                for i, c, d in (("initial", 0, 100), ("boundary", 1, 3599.9996), ("receiver", 2, 4700))]
    result = propose_stable_od_retiming(requests=requests, cells=cells, target=[1, 0, 2])
    assert [r["vehicle_id"] for r in result["proposals"]] == ["boundary"]
    assert result["proposals"][0]["native_shift_s"] == 1100
    assert result["initial_requests_changed"] == 0


@pytest.mark.parametrize("kind", ["OD_total", "frozen", "fractional", "duplicate_ID", "native_boundary", "missing_receiver_prior"])
def test_invalid_or_unsupported_requests_are_rejected(kind):
    data, _ = fixture()
    if kind == "OD_total":
        data["target"][2] -= 1
        data["target"][3] += 1
    elif kind == "frozen":
        data["target"][0] -= 1
        data["target"][2] += 1
    elif kind == "fractional":
        data["target"][2] = 2.5
    elif kind == "duplicate_ID":
        data["requests"][1]["vehicle_id"] = data["requests"][0]["vehicle_id"]
    elif kind == "native_boundary":
        data["requests"][0]["requested_depart_s"] = 9.9996
    else:
        data["requests"] = [r for r in data["requests"] if r["od_cell"] != 4]
        data["target"][4] = 2
    with pytest.raises(ValueError):
        propose_stable_od_retiming(**data)
