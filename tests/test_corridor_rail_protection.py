import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.corridor_edit_ledger import _is_rail_edge, _is_structurally_protected_edge


@pytest.mark.parametrize("metadata", [
    '<param key="razed:railway" value="tram"/>',
    '<param key="name" value="Railway Street"/>',
    '<param key="destination" value="Tram Museum"/>',
    '<param key="demolished:railway" value="rail"/>',
    '<param key="railway" value="razed"/>',
])
def test_road_history_and_names_do_not_become_current_rail_protection(metadata):
    road = ET.fromstring(f'<edge id="30559663" type="highway.primary" disallow="rail tram">'
                         f'<lane id="30559663_0" allow="passenger bus"/>{metadata}</edge>')
    assert not _is_rail_edge(road)
    assert not _is_structurally_protected_edge(road)


@pytest.mark.parametrize("attributes,metadata,lane", [
    ('type="railway.tram"', '', ''),
    ('type="highway.secondary|railway.rail"', '', ''),
    ('type="highway.secondary"', '<param key="railway" value="tram"/>', ''),
    ('type="custom"', '', '<lane id="track_0" allow="tram passenger"/>'),
    ('type="custom"', '', '<lane id="track_0" allow="rail_urban"/>'),
    ('type="custom"', '<param key="railway" value="disused"/>', ''),
])
def test_current_rail_evidence_remains_protected(attributes, metadata, lane):
    road = ET.fromstring(f'<edge id="track" {attributes}>{metadata}{lane}</edge>')
    assert _is_rail_edge(road) and _is_structurally_protected_edge(road)


def test_historic_rail_tag_does_not_cancel_parking_access_protection():
    road = ET.fromstring('<edge id="parking" type="highway.service"><param key="razed:railway" value="tram"/>'
                         '<param key="service" value="parking_aisle"/></edge>')
    assert not _is_rail_edge(road)
    assert _is_structurally_protected_edge(road)


@pytest.mark.parametrize("railway,allowed", [("razed", True), ("tram", False)])
def test_join_preflight_uses_the_same_rail_classification(monkeypatch, railway, allowed):
    from astra_sumo.core.network_edit_operations import validate_scoped_operation
    from astra_sumo.core import junction_boundary_rebuild
    root = ET.fromstring(f'<net><junction id="a"/><junction id="b"/><edge id="road" from="a" to="b" type="highway.primary">'
                         f'<lane id="road_0" index="0" allow="passenger"/><param key="railway" value="{railway}"/>'
                         '<param key="razed:railway" value="tram"/></edge></net>')
    calls = []
    monkeypatch.setattr(junction_boundary_rebuild, "collect_join_boundary_paths", lambda *args, **kwargs: calls.append(kwargs) or {})
    operation = {"operation": "join_junctions", "params": {"node_ids": ["a", "b"], "join_id": "joined",
        "signal_policy": "rebuild_diagnostic"}}
    errors = validate_scoped_operation(operation, root)
    assert not errors if allowed else any("Protected roads" in row["detail"] for row in errors)
    assert bool(calls) is allowed
