"""A documented crossing may contain disconnected motor-road components."""
from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from torii_sumo.core.hamburg_junctions import groups as candidate


def _case(monkeypatch, *, has_tls_seed):
    root = ET.Element("net")
    for name, x, y in (("near", 6, 0), ("car1", 10, 0), ("car2", 12, 0), ("side", 12, 1), ("far", 100, 100)):
        ET.SubElement(root, "junction", id=name, x=str(x), y=str(y), type="traffic_light" if name in {"near", "far"} else "priority", shape=f"{x-.1},{y-.1} {x+.1},{y-.1} {x+.1},{y+.1} {x-.1},{y+.1}")
    for name, first, last, shape in (("car", "car1", "car2", "10,0 12,0"), ("side-road", "car2", "side", "12,0 12,1")):
        edge = ET.SubElement(root, "edge", id=name, **{"from": first, "to": last})
        ET.SubElement(edge, "lane", id=name+"_0", index="0", length="2", shape=shape, allow="passenger")
    points = {"a": (-2, 0), "b": (-2, 2), "c": (14, 0), "d": (14, 2)}
    movements = [{"ingress_lane_id": a, "egress_lane_id": b, "intersection_part": "1", "selected_shape_network": [points[a], points[b]]} for a,b in (("a", "c"), ("b", "d"))]
    plans = {"118": {"movements": movements}}
    binding = {"bindings": [{"node_id": "118", "tls_ids": ["near" if has_tls_seed else "far"]}]}
    monkeypatch.setattr(candidate, "_official_lane_boundary_points", lambda plan, root: points)
    monkeypatch.setattr(candidate, "_official_internal_paths", lambda plan, root, points: {"paths": {("a", "c"): [points["a"], points["c"]], ("b", "d"): [points["b"], points["d"]]}, "unusable": []})
    return root, binding, plans


@pytest.mark.parametrize("has_tls_seed", [False, True])
def test_verified_component_is_not_lost_when_another_component_is_seeded(monkeypatch, has_tls_seed):
    root, binding, plans = _case(monkeypatch, has_tls_seed=has_tls_seed)
    before = ET.tostring(root)
    result = candidate._select_join_groups(root, binding, plans)
    assert len(result) == 1
    group = result[0]
    assert set(group["source_node_ids"]) == {"near", "car1", "car2"}
    added = next(row for row in group["evidence_components"] if "car1" in row["source_node_ids"])
    assert set(added["added_source_node_ids"]) == {"car1", "car2"}
    assert set(added["official_drive_line_owner_node_ids"]) == {"car1", "car2"}
    assert "side" not in group["source_node_ids"]  # Eligible and adjacent, but not documented.
    assert before == ET.tostring(root)


def test_component_evidenced_for_two_parts_is_not_stolen(monkeypatch):
    root, binding, plans = _case(monkeypatch, has_tls_seed=True)
    plans["118"]["movements"].extend({**row, "intersection_part": "2"} for row in deepcopy(plans["118"]["movements"]))
    groups = candidate._select_join_groups(root, binding, plans)
    first = next(row for row in groups if row["intersection_part"] == "1")
    assert "car1" not in first["source_node_ids"]
    assert any(row["conflicting_parts"] == ["2"] for row in first["component_selection_reviews"])


def test_component_controlled_by_another_declared_intersection_is_not_stolen(monkeypatch):
    root, binding, plans = _case(monkeypatch, has_tls_seed=True)
    binding["bindings"].append({"node_id": "other", "tls_ids": ["car1"]})
    group = candidate._select_join_groups(root, binding, plans)[0]
    assert "car1" not in group["source_node_ids"]
    assert any(row["conflicting_intersections"] == ["other"] for row in group["component_selection_reviews"])
