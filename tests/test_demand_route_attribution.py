import gzip
import json

from astra_sumo.core.demand_route_attribution import attribute_indirect_routes, lane_road_hypotheses


def test_lane_hypotheses_retain_selected_and_unresolved_candidate_roads():
    binding = {"ingress": {"bindings": {"1": ["a", 0]},
        "records": [{"lane_id": "1", "candidates": [{"edge": "b"}, {"edge": "a"}]}]}}
    assert lane_road_hypotheses(binding, "ingress", "1") == {"a", "b"}
    assert lane_road_hypotheses(binding, "ingress", "2") == set()


def test_attribution_keeps_overlapping_classes_and_counts_each_selected_vehicle_once(tmp_path):
    package = tmp_path / "package"
    (package / "inputs").mkdir(parents=True)
    def repeat(outlet):
        return {"junction": "LSA1", "first_inlet": "a", "last_outlet": outlet}
    contents = {
        "route-pool.json.gz": {"routes": [{"route_id": "r", "junction_audit": {"repeats": [repeat("b"), repeat("c")]}}]},
        "ideal-lane-profiles.json.gz": {"profiles": [{"route_id": "r"}]},
        "provisional-lane-intentions.json.gz": {"vehicles": [{"vehicle_id": "v", "profile_index": 0}]},
    }
    for name, value in contents.items():
        (package / name).write_bytes(gzip.compress(json.dumps(value).encode()))
    (package / "inputs/junction-groups.json").write_text(json.dumps({"groups": [{"node_id": "1", "source_node_ids": ["j"]}]}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"official_connection_audit": {
        "required": [], "ambiguous": [{"node_id": "1", "intersection_part": "p", "movement_id": "m",
            "ingress_lane_id": "1", "egress_lane_id": "2"}],
        "lane_bindings": [{"node_id": "1", "intersection_part": "p",
            "ingress": {"bindings": {"1": ["a", 0]}, "records": []},
            "egress": {"bindings": {"2": ["b", 0]}, "records": []}}]}}))
    source = tmp_path / "net.xml"
    source.write_text('<net><junction id="j" x="0" y="0"/>' + ''.join(
        f'<edge id="{edge}" from="j" to="j"><lane id="{edge}_0" speed="10" length="10" allow="passenger" shape="0,0 10,0"/></edge>'
        for edge in "abc") + '</net>')
    report = attribute_indirect_routes(demand_package=package, candidate_manifest=manifest,
        original_network=source, output_dir=tmp_path / "output")
    assert report["flagged_routes"] == report["selected_indirect_vehicles"] == 1
    assert report["event_classes"] == {"unresolved_official": 1, "absent_in_examined_sources": 1}
    assert report["selected_vehicle_class_memberships"] == {"unresolved_official": 1, "absent_in_examined_sources": 1}
    assert report["unresolved_official_vehicle_memberships"] == {"1:m": 1}
