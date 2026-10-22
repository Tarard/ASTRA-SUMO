import json
from pathlib import Path

from astra_sumo.core.hamburg_od_definition import audit_hamburg_od_definition


def test_frozen_corridor_ports_keep_all_fields_and_detect_internal_trip_ends(tmp_path):
    root = Path(__file__).resolve().parents[1]
    report = audit_hamburg_od_definition(
        case_dir=root / "benchmarks/hamburg_ring1_munich_corridor_20261022_v1",
        output_dir=tmp_path / "audit")
    assert report["old_requests"] == 8640
    assert report["old_nonboundary_exits"] == {"27064186#0": 2589}
    assert len(report["field_road_coverage"]) == 13
    assert all(report["field_road_coverage"].values())
    support = json.loads((tmp_path / "audit/od-support.json").read_bytes())
    assert len(support["departure_intervals"]) == 14
    assert support["initial_values"] is None
    routes = json.loads((tmp_path / "audit/candidate-routes.json").read_bytes())["routes"]
    assert any(len(r["edges"]) > 1 and r["sink_edge"] == "199500676" for r in routes)
    assert all(r["junction_audit"]["allowed"] and len(r["edges"]) == len(set(r["edges"])) for r in routes)
    assert report["native_runs"] == 0 and not report["formal_result"]
