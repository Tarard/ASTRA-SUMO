import csv
import gzip
import json
import shutil
from pathlib import Path
from unittest.mock import Mock
import xml.etree.ElementTree as ET

from torii_sumo.core import osm_workflow
from torii_sumo.core.connectivity import (
    extract_largest_passenger_component_core,
    summarize_passenger_connectivity,
)
from torii_sumo.core.command_runner import CommandResult
from torii_sumo.core.osm_network import (
    build_osm_network,
    build_overpass_query,
    build_routeability_probe,
    build_tls_multisource_review,
    cluster_tls_candidates,
    filter_osm_by_highways,
    google_maps_baseline_fields,
    merge_osm_xml_payloads,
    parse_bbox,
    regional_map_fields,
    robust_download_osm,
    split_bbox,
)
from torii_sumo.core.osm_area import osm_map_url_bbox, resolve_osm_place
from torii_sumo.core.osm_workflow import run_osm_cleanup_workflow
from torii_sumo.core.osm_workflow import _road_connectivity_best_variant_file
from torii_sumo.core.osm_workflow import _road_connectivity_owner_ids
from torii_sumo.core.osm_workflow import _road_connectivity_replay_batch_report
from torii_sumo.core.osm_workflow import _road_connectivity_seed_probe_improved
from torii_sumo.core.osm_workflow import _run_road_connectivity_split_root_alias_repair
from torii_sumo.core.osm_workflow import _sumo_load_net
from torii_sumo.core.topology_audit import audit_topology_fragmentation
from torii_sumo.tools.osm_tools import resolve_highway_classes, sumo_osm_build_network


def _patch_cleanup_stages(monkeypatch, work_dir: Path, connectivity_report: dict[str, object]) -> dict[str, Mock]:
    net_file = work_dir / "network.net.xml"
    osm_file = work_dir / "source.osm.xml"
    scoped_reference_file = work_dir / "reference_scoped.net.xml"
    net_file.parent.mkdir(parents=True, exist_ok=True)
    net_file.write_text("<net/>", encoding="utf-8")
    osm_file.write_text("<osm/>", encoding="utf-8")
    scoped_reference_file.write_text("<net/>", encoding="utf-8")

    def fake_build(**kwargs):
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "bbox": kwargs["bbox"],
            "net_file": str(net_file),
            "filtered_osm_file": str(osm_file),
            "source_osm_file": str(osm_file),
            "road_classes": sorted(kwargs["allowed_highways"]),
            "warnings": [],
        }

    mocks = {
        "build_osm_network": Mock(side_effect=fake_build),
        "audit_tls": Mock(
            return_value={
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "tls_candidate_count": 1,
                "tls_cluster_count": 1,
                "clusters_file": str(work_dir / "tls.csv"),
                "warnings": [],
            }
        ),
        "summarize_passenger_connectivity": Mock(return_value=connectivity_report),
        "extract_largest_passenger_component_core": Mock(
            return_value={"status": "blocked", "claim_status": "construction-invalid", "warnings": []}
        ),
        "audit_topology_fragmentation": Mock(
            return_value={
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "topology_fragmentation_status": "pass",
                "suspicious_cluster_count": 0,
                "warnings": [],
            }
        ),
        "run_routeability_audit": Mock(
            return_value={
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "routeability_status": "pass",
                "warnings": [],
            }
        ),
        "build_network_connection_mode_audit": Mock(
            return_value={
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "connection_mode_audit_status": "pass",
                "warnings": [],
            }
        ),
        "build_standard_nema_phase_binding": Mock(
            return_value={
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "nema_binding_status": "scan_complete",
                "scan_counts": {},
                "warnings": [],
            }
        ),
        "build_tls_aggregation_variant": Mock(
            return_value={
                "status": "blocked",
                "claim_status": "diagnostic-demo",
                "tls_aggregation_status": "skipped",
                "warnings": [],
            }
        ),
        "audit_reference_join_patterns": Mock(
            return_value={
                "status": "pass",
                "claim_status": "reference-audit",
                "reference_join_status": "pass",
                "warnings": [],
            }
        ),
        "audit_reference_hierarchy": Mock(
            return_value={
                "status": "pass",
                "claim_status": "reference-audit",
                "reference_hierarchy_status": "pass",
                "high_hierarchy_issue_count": 0,
                "warnings": [],
            }
        ),
        "audit_reference_scope": Mock(
            return_value={
                "status": "pass",
                "claim_status": "reference-audit",
                "reference_scope_status": "pass",
                "warnings": [],
            }
        ),
        "audit_road_connectivity_parity": Mock(
            return_value={
                "status": "pass",
                "claim_status": "reference-audit",
                "road_connectivity_parity_status": "pass",
                "edge_delta_count": 0,
                "connection_delta_count": 0,
                "warnings": [],
            }
        ),
        "build_reference_bbox_variant": Mock(
            return_value={
                "status": "pass",
                "claim_status": "diagnostic-demo",
                "reference_bbox_scope_status": "variant_created",
                "variant_file": str(scoped_reference_file),
                "warnings": [],
            }
        ),
        "apply_service_passenger_permissions": Mock(
            return_value={"status": "pass", "changed_lane_count": 0, "warnings": []}
        ),
        "build_workflow_review_html": Mock(
            return_value={"status": "pass", "workflow_review_html_status": "pass", "warnings": []}
        ),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(osm_workflow, name, mock)
    return mocks


def test_osm_cleanup_standard_profile_runs_fixed_audits_and_sets_amap_baseline(tmp_path: Path, monkeypatch) -> None:
    mocks = _patch_cleanup_stages(
        monkeypatch,
        tmp_path,
        {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "connectivity_status": "pass",
            "passenger_edge_count": 3,
            "passenger_component_count": 1,
            "largest_component_edge_count": 3,
            "warnings": [],
        },
    )

    report = run_osm_cleanup_workflow(
        output_dir=tmp_path / "out",
        bbox="116.3018,39.9548,116.3176,39.9608",
        profile="standard",
        traffic_layers="passenger",
    )

    for name in (
        "build_osm_network",
        "audit_tls",
        "build_tls_aggregation_variant",
        "summarize_passenger_connectivity",
        "audit_topology_fragmentation",
        "run_routeability_audit",
        "build_network_connection_mode_audit",
        "build_standard_nema_phase_binding",
    ):
        assert mocks[name].called, name
    assert not mocks["audit_reference_join_patterns"].called
    assert report["network_profile"] == "standard"
    assert report["map_baseline_source"] == "Amap/Gaode"
    assert "uri.amap.com/marker" in report["regional_map_baseline"]["regional_map_url"]


def test_osm_cleanup_reference_profile_runs_read_only_reference_audits(tmp_path: Path, monkeypatch) -> None:
    reference_net_file = tmp_path / "reference.net.xml"
    reference_net_file.write_text(
        '<net><edge id="road" type="highway.primary"><lane id="road_0" allow="passenger"/></edge></net>',
        encoding="utf-8",
    )
    mocks = _patch_cleanup_stages(
        monkeypatch,
        tmp_path,
        {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "connectivity_status": "pass",
            "passenger_edge_count": 1,
            "passenger_component_count": 1,
            "largest_component_edge_count": 1,
            "warnings": [],
        },
    )

    report = run_osm_cleanup_workflow(
        output_dir=tmp_path / "out",
        bbox="11.413800,48.755391,11.433800,48.775391",
        profile="reference_matched",
        reference_net_file=reference_net_file,
    )

    assert not mocks["build_tls_aggregation_variant"].called
    assert mocks["build_reference_bbox_variant"].called
    for name in (
        "audit_reference_join_patterns",
        "audit_reference_hierarchy",
        "audit_reference_scope",
        "audit_road_connectivity_parity",
    ):
        assert mocks[name].called, name
    assert mocks["audit_reference_join_patterns"].call_args.kwargs["structural_only"] is False
    assert report["network_profile"] == "reference_matched"


def test_cleanup_uses_permission_candidate_and_keeps_raw_build_identity(tmp_path, monkeypatch):
    mocks = _patch_cleanup_stages(monkeypatch, tmp_path, {
        "status": "pass", "connectivity_status": "pass", "passenger_edge_count": 1,
        "passenger_component_count": 1, "largest_component_edge_count": 1, "warnings": []})
    candidate = tmp_path / "permissions.net.xml"
    candidate.write_text("<net/>", encoding="utf-8")
    mocks["apply_service_passenger_permissions"].return_value = {
        "status": "pass", "changed_lane_count": 1, "net_file": str(candidate), "warnings": []}
    report = run_osm_cleanup_workflow(output_dir=tmp_path / "out", bbox="13.6,50.9,13.9,51.1",
                                      profile="standard", traffic_layers="passenger")
    assert mocks["audit_tls"].call_args_list[0].kwargs["net_file"] == candidate
    assert mocks["build_tls_aggregation_variant"].call_args_list[0].kwargs["net_file"] == candidate
    assert report["raw_net_file"] == str(tmp_path / "network.net.xml")
    assert report["build"]["net_file"] == str(tmp_path / "network.net.xml")


def test_osm_cleanup_preserves_partial_and_severe_connectivity_classes(tmp_path: Path, monkeypatch) -> None:
    cases = (
        (992, 4, "partial-main-component", "partial"),
        (700, 20, "construction-invalid", "fail"),
    )
    for largest_count, component_count, expected_quality, expected_gate in cases:
        work_dir = tmp_path / expected_gate
        _patch_cleanup_stages(
            monkeypatch,
            work_dir,
            {
                "status": "fail",
                "claim_status": "construction-invalid",
                "connectivity_status": "fail",
                "passenger_edge_count": 1000,
                "passenger_component_count": component_count,
                "largest_component_edge_count": largest_count,
                "small_component_count": component_count - 1,
                "isolated_passenger_edge_count": component_count - 1,
                "warnings": [f"passenger network has {component_count} disconnected components"],
            },
        )

        report = run_osm_cleanup_workflow(
            output_dir=work_dir / "out",
            bbox="13.6,50.9,13.9,51.1",
            profile="standard",
            traffic_layers="passenger",
        )

        assert report["network_quality"] == expected_quality
        assert report["gate_status"]["connectivity"] == expected_gate
        assert report["claim_status"] == "construction-invalid"


def test_build_overpass_query_uses_overpass_coordinate_order_and_date() -> None:
    bbox = parse_bbox("13.6000,50.9800,13.9000,51.1500")

    query = build_overpass_query(
        bbox,
        timeout=180,
        historical_date="2024-09-10T00:00:00Z",
    )

    assert '[out:xml][timeout:180][date:"2024-09-10T00:00:00Z"];' in query
    assert 'way["highway"](50.98,13.6,51.15,13.9);' in query
    assert 'relation["type"="restriction"](50.98,13.6,51.15,13.9);' in query


def test_resolve_highway_classes_supports_osmnet_inspired_presets() -> None:
    assert {"primary", "tertiary", "tertiary_link"} <= resolve_highway_classes("arterial")
    assert {"motorway", "primary", "residential", "living_street"} <= resolve_highway_classes("drive")
    assert "unclassified" in resolve_highway_classes("drive_plus_unclassified")
    assert "service" in resolve_highway_classes("full_vehicle")
    assert resolve_highway_classes("primary,residential") == {"primary", "residential"}


def test_road_connectivity_owner_ids_include_seed_geometry_mismatch_endpoints(tmp_path: Path) -> None:
    teacher_net_file = tmp_path / "teacher.net.xml"
    teacher_net_file.write_text(
        """<net>
  <edge id="road#0" from="junction_a" to="junction_b"><lane id="road#0_0" index="0"/></edge>
  <edge id="road#1" from="junction_b" to="junction_c"><lane id="road#1_0" index="0"/></edge>
</net>""",
        encoding="utf-8",
    )
    queue_report = {
        "repair_candidates": [
            {"reference_id": "queue_owner"},
        ]
    }
    seed_report = {
        "parity": {
            "common_edge_geometry_mismatches": [
                {"edge_id": "road#0"},
            ]
        }
    }

    owner_ids = _road_connectivity_owner_ids(
        queue_report,
        seed_report,
        teacher_net_file=teacher_net_file,
        max_owner_count=3,
    )

    assert owner_ids == ["queue_owner", "junction_a", "junction_b"]


def test_road_connectivity_seed_probe_improved_accepts_lower_delta_without_passing() -> None:
    before = {"status": "fail", "edge_delta_count": 4, "connection_delta_count": 5}
    after = {"status": "fail", "edge_delta_count": 2, "connection_delta_count": 1}

    assert _road_connectivity_seed_probe_improved(before, after) is True
    assert _road_connectivity_seed_probe_improved(after, before) is False
    assert _road_connectivity_seed_probe_improved(after, dict(after)) is False


def test_road_connectivity_batch_report_exposes_seed_improved_variant_without_passing(tmp_path: Path) -> None:
    report = _road_connectivity_replay_batch_report(
        [
            {
                "status": "pass",
                "sumo_load_status": "pass",
                "output_file": str(tmp_path / "improved.net.xml"),
                "road_connectivity_seed_probe_improved": True,
                "owner_road_connectivity_audit": {
                    "status": "fail",
                    "gate": {"lane_delta_count": 1},
                },
            }
        ],
        output_dir=tmp_path,
        prefix="road_connectivity",
    )

    assert report["status"] == "fail"
    assert report["output_file"] == str(tmp_path / "improved.net.xml")
    assert report["owner_road_connectivity_audit"]["status"] == "fail"


def test_road_connectivity_best_variant_uses_batch_selected_owner_output(tmp_path: Path) -> None:
    owner_net = tmp_path / "owner.net.xml"
    owner_net.write_text("<net/>", encoding="utf-8")
    report = {
        "status": "fail",
        "sumo_load_status": "fail",
        "output_file": str(owner_net),
        "owner_reports": [
            {
                "status": "pass",
                "sumo_load_status": "pass",
                "output_file": str(owner_net),
                "owner_road_connectivity_audit": {"status": "pass", "gate": {"lane_delta_count": 0}},
            },
            {
                "status": "pass",
                "sumo_load_status": "fail",
                "output_file": str(tmp_path / "failed_owner.net.xml"),
                "owner_road_connectivity_audit": {"status": "pass", "gate": {"lane_delta_count": 0}},
            },
        ],
    }

    assert _road_connectivity_best_variant_file(report) == owner_net


def test_sumo_load_net_falls_back_to_path_netconvert_for_bare_sumo_binary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    net_file = tmp_path / "candidate.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(shutil, "which", lambda name: "netconvert" if name == "netconvert" else None)

    def fake_command_runner(command, **kwargs):
        calls.append(list(command))
        if command[0] == "netconvert":
            output = Path(kwargs["cwd"]) / "sumo_load_candidate_normalized.net.xml"
            output.write_text("<net/>", encoding="utf-8")
            return CommandResult(command=command, cwd=str(kwargs["cwd"]), status="pass", returncode=0)
        return CommandResult(
            command=command,
            cwd=str(kwargs["cwd"]),
            status="pass" if "sumo_load_candidate_normalized.net.xml" in command else "fail",
            returncode=0 if "sumo_load_candidate_normalized.net.xml" in command else 3221225477,
        )

    report = _sumo_load_net(
        net_file,
        output_dir=tmp_path / "sumo_load",
        sumo_binary="sumo",
        timeout_seconds=1,
        command_runner=fake_command_runner,
    )

    assert report["status"] == "pass"
    assert report["direct_sumo_load"]["status"] == "fail"
    assert report["normalization_netconvert"]["status"] == "pass"
    assert calls[1][0] == "netconvert"


def test_run_road_connectivity_split_root_alias_repair_promotes_seed_parity(tmp_path: Path) -> None:
    teacher_net = tmp_path / "teacher.net.xml"
    candidate_net = tmp_path / "candidate.net.xml"
    teacher_net.write_text(
        """<net>
  <edge id="in" from="a" to="b"><lane id="in_0" index="0" shape="0,0 1,0"/></edge>
  <edge id="road#1" from="b" to="c"><lane id="road#1_0" index="0" shape="1,0 2,0"/></edge>
  <junction id="a" type="dead_end"/>
  <junction id="b" type="priority" incLanes="in_0" intLanes=""/>
  <junction id="c" type="dead_end" incLanes="road#1_0" intLanes=""/>
  <connection from="in" to="road#1" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    candidate_net.write_text(
        """<net>
  <edge id="in" from="a" to="b"><lane id="in_0" index="0" shape="0,0 1,0"/></edge>
  <edge id="road" from="b" to="c"><lane id="road_0" index="0" shape="1,0 2,0"/></edge>
  <junction id="a" type="dead_end"/>
  <junction id="b" type="priority" incLanes="in_0" intLanes=""/>
  <junction id="c" type="dead_end" incLanes="road_0" intLanes=""/>
  <connection from="in" to="road" fromLane="0" toLane="0" dir="s"/>
</net>""",
        encoding="utf-8",
    )
    seed_report = {
        "status": "fail",
        "edge_delta_count": 2,
        "connection_delta_count": 0,
        "parity": {
            "edge_ids": {
                "split_root_aliases": [{"root": "road", "teacher_edge_id": "road#1", "candidate_edge_id": "road"}]
            }
        },
    }

    def fake_command_runner(command, **_kwargs):
        output = Path(_kwargs["cwd"]) / "sumo_load_candidate_normalized.net.xml"
        if "--output-file" in command:
            output.write_text(candidate_net.read_text(encoding="utf-8"), encoding="utf-8")
        return CommandResult(
            command=command,
            cwd=str(_kwargs["cwd"]),
            status="pass",
            returncode=0,
            stdout="",
            stderr="",
            error="",
        )

    report = _run_road_connectivity_split_root_alias_repair(
        teacher_net_file=teacher_net,
        candidate_net_file=candidate_net,
        seed_probe_report=seed_report,
        seed_edge_ids=["in"],
        output_dir=tmp_path / "alias_repair",
        prefix="road",
        sumo_binary="sumo",
        timeout_seconds=1,
        command_runner=fake_command_runner,
    )

    assert report["status"] == "pass"
    assert report["seed_probe"]["status"] == "pass"
    assert report["seed_probe"]["edge_delta_count"] == 0
    assert Path(report["output_file"]).exists()


def test_osm_map_url_bbox_extracts_small_area_around_center() -> None:
    bbox = osm_map_url_bbox("https://www.openstreetmap.org/#map=18/48.768610/11.422681")
    parsed = parse_bbox(bbox)

    assert parsed.west < 11.422681 < parsed.east
    assert parsed.south < 48.768610 < parsed.north
    assert parsed.east - parsed.west < 0.01
    assert parsed.north - parsed.south < 0.01


def test_split_bbox_subdivides_large_bbox_without_losing_extent() -> None:
    bbox = parse_bbox("13.0,50.0,14.0,51.0")

    tiles = split_bbox(bbox, max_tile_area_km2=1000.0)

    assert len(tiles) > 1
    assert min(tile.west for tile in tiles) == bbox.west
    assert min(tile.south for tile in tiles) == bbox.south
    assert max(tile.east for tile in tiles) == bbox.east
    assert max(tile.north for tile in tiles) == bbox.north


def test_resolve_osm_place_parses_first_nominatim_candidate() -> None:
    def fake_fetch_json(*, url: str, headers: dict[str, str], timeout_seconds: float):
        assert "nominatim.openstreetmap.org/search" in url
        assert "Altstadt%2C+Dresden" in url
        assert headers["User-Agent"].startswith("Torii-SUMO")
        assert timeout_seconds == 30.0
        return [
            {
                "display_name": "Altstadt, Dresden, Sachsen, Deutschland",
                "osm_type": "relation",
                "osm_id": 192900,
                "lat": "51.0523842",
                "lon": "13.7381876",
                "boundingbox": ["51.0280799", "51.0766681", "13.6864402", "13.7872926"],
            }
        ]

    report = resolve_osm_place(
        "Altstadt, Dresden",
        fetch_json=fake_fetch_json,
        timeout_seconds=30.0,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["area_resolution_status"] == "candidate_found"
    assert report["candidate_display_name"] == "Altstadt, Dresden, Sachsen, Deutschland"
    assert report["candidate_osm_type"] == "relation"
    assert report["candidate_osm_id"] == "192900"
    assert report["candidate_bbox"] == "13.6864402,51.0280799,13.7872926,51.0766681"
    assert report["candidate_lat"] == "51.0523842"
    assert report["candidate_lon"] == "13.7381876"
    assert "openstreetmap.org/search" in report["osm_preview_url"]
    assert report["candidate_osm_url"] == "https://www.openstreetmap.org/relation/192900"


def test_topology_audit_flags_dense_junction_clusters_within_radius(tmp_path: Path) -> None:
    net_file = tmp_path / "fragmented.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="j1" x="0.0" y="0.0" type="traffic_light"/>
  <junction id="j2" x="12.0" y="0.0" type="priority"/>
  <junction id="j3" x="25.0" y="3.0" type="traffic_light"/>
  <junction id="j4" x="120.0" y="120.0" type="priority"/>
  <junction id=":j1_0" x="3.0" y="3.0" type="internal"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology",
        prefix="demo",
        cluster_radius_m=30.0,
        min_cluster_nodes=3,
    )

    assert report["status"] == "blocked"
    assert report["claim_status"] == "blocked"
    assert report["topology_fragmentation_status"] == "needs_review"
    assert report["junction_count"] == 4
    assert report["suspicious_cluster_count"] == 1
    assert report["max_cluster_node_count"] == 3
    cluster = report["suspicious_clusters"][0]
    assert set(cluster["node_ids"]) == {"j1", "j2", "j3"}
    assert cluster["manual_correction_status"] == "needs_map_review"
    assert cluster["map_review_source"] == "Google Maps default map"
    assert cluster["google_maps_url"].startswith("https://www.google.com/maps/@")
    assert "data=!3m1!1e3" not in cluster["google_maps_url"]
    assert "data=!3m1!1e3" in cluster["optional_google_maps_satellite_url"]
    assert cluster["modal_aggregation_decision"] in {"join_core", "review_required"}
    assert cluster["modal_review_action"] in {"safe_vehicle_core_candidate", "review_vehicle_core_boundary"}
    assert "modal_decision_counts" in report
    assert "modal_review_action_counts" in report
    assert "junction_aggregation_blocked_by_modal_count" in report
    assert Path(report["clusters_file"]).is_file()
    csv_header = Path(report["clusters_file"]).read_text(encoding="utf-8").splitlines()[0]
    assert "google_maps_url" in csv_header
    assert "optional_google_maps_satellite_url" in csv_header
    assert "modal_aggregation_decision" in csv_header
    assert "modal_review_action" in csv_header


def test_topology_audit_reports_local_cluster_graph_edges(tmp_path: Path) -> None:
    net_file = tmp_path / "fragmented_with_edges.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id="internal_a" from="j1" to="j2">
    <lane id="internal_a_0" index="0" speed="13.9" length="8.0" shape="0.0,0.0,0.0 8.0,0.0,0.0"/>
  </edge>
  <edge id="internal_b" from="j2" to="j3">
    <lane id="internal_b_0" index="0" speed="13.9" length="9.0" shape="8.0,0.0 16.0,1.0"/>
  </edge>
  <edge id="internal_overlap" from="j1" to="j2">
    <lane id="internal_overlap_0" index="0" speed="13.9" length="8.2" shape="0.3,0.0 8.3,0.1"/>
  </edge>
  <edge id="west_approach" from="west" to="j1">
    <lane id="west_approach_0" index="0" speed="13.9" length="80.0" shape="-80.0,0.0 0.0,0.0"/>
  </edge>
  <edge id="east_departure" from="j3" to="east">
    <lane id="east_departure_0" index="0" speed="13.9" length="80.0" shape="16.0,1.0 96.0,1.0"/>
  </edge>
  <junction id="west" x="-80.0" y="0.0" type="priority"/>
  <junction id="j1" x="0.0" y="0.0" type="traffic_light"/>
  <junction id="j2" x="8.0" y="0.0" type="traffic_light"/>
  <junction id="j3" x="16.0" y="1.0" type="priority"/>
  <junction id="east" x="96.0" y="1.0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology_graph",
        prefix="graph",
        cluster_radius_m=25.0,
        min_cluster_nodes=3,
    )

    cluster = report["suspicious_clusters"][0]
    assert cluster["internal_edge_ids"] == ["internal_a", "internal_b", "internal_overlap"]
    assert cluster["boundary_edge_ids"] == ["east_departure", "west_approach"]
    assert set(cluster["external_junction_ids"]) == {"east", "west"}
    assert cluster["approach_count"] == 2
    assert cluster["direct_connected_node_pair_count"] == 2
    assert cluster["internal_edge_count"] == 3
    assert cluster["boundary_edge_count"] == 2
    assert cluster["traffic_light_node_count"] == 2
    assert cluster["internal_edge_overlap_pair_count"] == 1
    assert cluster["aggregation_recommendation"] == "map_review_join_candidate"
    assert cluster["aggregation_decision"] == "needs_map_review"
    assert cluster["aggregation_confidence"] == "low"
    assert cluster["reference_free_scorer"] == "topology_heuristic_v1"
    assert cluster["short_internal_edge_score"] > 0.8
    assert cluster["traffic_signal_density"] == 0.667
    assert cluster["service_or_parking_risk"] is False
    assert cluster["bridge_tunnel_layer_risk"] is False
    assert cluster["roundabout_or_slip_lane_risk"] is False
    assert "map review" in cluster["aggregation_reason"]
    assert "few_approaches_for_signalized_cluster" in cluster["risk_flags"]
    csv_header = Path(report["clusters_file"]).read_text(encoding="utf-8").splitlines()[0]
    assert "internal_edge_ids" in csv_header
    assert "aggregation_recommendation" in csv_header
    assert "aggregation_decision" in csv_header
    assert "short_internal_edge_score" in csv_header
    assert "physical_intersection_shape" in csv_header
    assert "approach_axis_angles_deg" in csv_header


def test_topology_audit_scores_small_reference_free_join_candidate(tmp_path: Path) -> None:
    net_file = tmp_path / "small_join_candidate.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id="internal_a" from="j1" to="j2" name="Main Street">
    <lane id="internal_a_0" index="0" speed="13.9" length="7.0" shape="0.0,0.0 7.0,0.0"/>
  </edge>
  <edge id="internal_b" from="j2" to="j3" name="Main Street">
    <lane id="internal_b_0" index="0" speed="13.9" length="8.0" shape="7.0,0.0 15.0,1.0"/>
  </edge>
  <edge id="west_approach" from="west" to="j1" name="Main Street">
    <lane id="west_approach_0" index="0" speed="13.9" length="70.0" shape="-70.0,0.0 0.0,0.0"/>
  </edge>
  <edge id="east_departure" from="j3" to="east" name="Main Street">
    <lane id="east_departure_0" index="0" speed="13.9" length="70.0" shape="15.0,1.0 85.0,1.0"/>
  </edge>
  <edge id="north_approach" from="north" to="j2" name="North Road">
    <lane id="north_approach_0" index="0" speed="13.9" length="70.0" shape="7.0,70.0 7.0,0.0"/>
  </edge>
  <junction id="west" x="-70.0" y="0.0" type="priority"/>
  <junction id="j1" x="0.0" y="0.0" type="priority"/>
  <junction id="j2" x="7.0" y="0.0" type="priority"/>
  <junction id="j3" x="15.0" y="1.0" type="priority"/>
  <junction id="east" x="85.0" y="1.0" type="priority"/>
  <junction id="north" x="7.0" y="70.0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology_score",
        prefix="score",
        cluster_radius_m=20.0,
        min_cluster_nodes=3,
    )

    cluster = report["suspicious_clusters"][0]
    assert cluster["aggregation_decision"] == "join"
    assert cluster["aggregation_confidence"] == "medium"
    assert cluster["physical_intersection_shape"] == "t_or_y"
    assert cluster["physical_intersection_score"] > 0.6
    assert cluster["approach_axis_count"] >= 2
    assert cluster["angle_continuity_score"] > 0.7
    assert cluster["short_internal_edge_score"] == 1.0
    assert cluster["same_road_name_score"] >= 0.6
    assert cluster["traffic_signal_density"] == 0.0
    assert cluster["service_or_parking_risk"] is False
    assert cluster["bridge_tunnel_layer_risk"] is False
    assert cluster["roundabout_or_slip_lane_risk"] is False
    assert "short internal edges" in cluster["aggregation_reason"]
    assert report["aggregation_decision_counts"] == {"join": 1}


def test_topology_audit_scores_cross_intersection_shape_from_approach_axes(tmp_path: Path) -> None:
    net_file = tmp_path / "cross_shape_candidate.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id="internal_w" from="jw" to="jc" name="Main Street">
    <lane id="internal_w_0" index="0" speed="13.9" length="6.0" shape="-6.0,0.0 0.0,0.0"/>
  </edge>
  <edge id="internal_e" from="jc" to="je" name="Main Street">
    <lane id="internal_e_0" index="0" speed="13.9" length="6.0" shape="0.0,0.0 6.0,0.0"/>
  </edge>
  <edge id="west_approach" from="west" to="jw" name="Main Street">
    <lane id="west_approach_0" index="0" speed="13.9" length="70.0" shape="-76.0,0.0 -6.0,0.0"/>
  </edge>
  <edge id="east_departure" from="je" to="east" name="Main Street">
    <lane id="east_departure_0" index="0" speed="13.9" length="70.0" shape="6.0,0.0 76.0,0.0"/>
  </edge>
  <edge id="north_approach" from="north" to="jc" name="North Road">
    <lane id="north_approach_0" index="0" speed="13.9" length="70.0" shape="0.0,70.0 0.0,0.0"/>
  </edge>
  <edge id="south_departure" from="jc" to="south" name="North Road">
    <lane id="south_departure_0" index="0" speed="13.9" length="70.0" shape="0.0,0.0 0.0,-70.0"/>
  </edge>
  <junction id="west" x="-76.0" y="0.0" type="priority"/>
  <junction id="jw" x="-6.0" y="0.0" type="priority"/>
  <junction id="jc" x="0.0" y="0.0" type="priority"/>
  <junction id="je" x="6.0" y="0.0" type="priority"/>
  <junction id="east" x="76.0" y="0.0" type="priority"/>
  <junction id="north" x="0.0" y="70.0" type="priority"/>
  <junction id="south" x="0.0" y="-70.0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology_cross_shape",
        prefix="cross_shape",
        cluster_radius_m=15.0,
        min_cluster_nodes=3,
    )

    cluster = report["suspicious_clusters"][0]
    assert cluster["physical_intersection_shape"] == "cross"
    assert cluster["physical_intersection_score"] > 0.8
    assert cluster["approach_axis_count"] == 2
    assert cluster["approach_axis_arm_counts"] == [2, 2]
    assert cluster["dominant_axis_separation_deg"] == 90.0
    assert cluster["angle_continuity_score"] == 1.0
    assert cluster["aggregation_decision"] == "join"
    assert report["physical_intersection_shape_counts"] == {"cross": 1}
    assert report["physical_intersection_candidate_count"] == 1


def test_topology_audit_does_not_auto_join_single_axis_linear_fragment(tmp_path: Path) -> None:
    net_file = tmp_path / "linear_fragment.net.xml"
    net_file.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <edge id="internal_a" from="j1" to="j2" name="Main Street">
    <lane id="internal_a_0" index="0" speed="13.9" length="7.0" shape="0.0,0.0 7.0,0.0"/>
  </edge>
  <edge id="internal_b" from="j2" to="j3" name="Main Street">
    <lane id="internal_b_0" index="0" speed="13.9" length="8.0" shape="7.0,0.0 15.0,0.0"/>
  </edge>
  <edge id="west_approach" from="west" to="j1" name="Main Street">
    <lane id="west_approach_0" index="0" speed="13.9" length="70.0" shape="-70.0,0.0 0.0,0.0"/>
  </edge>
  <edge id="east_departure" from="j3" to="east" name="Main Street">
    <lane id="east_departure_0" index="0" speed="13.9" length="70.0" shape="15.0,0.0 85.0,0.0"/>
  </edge>
  <junction id="west" x="-70.0" y="0.0" type="priority"/>
  <junction id="j1" x="0.0" y="0.0" type="priority"/>
  <junction id="j2" x="7.0" y="0.0" type="priority"/>
  <junction id="j3" x="15.0" y="0.0" type="priority"/>
  <junction id="east" x="85.0" y="0.0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology_linear_shape",
        prefix="linear_shape",
        cluster_radius_m=20.0,
        min_cluster_nodes=3,
    )

    cluster = report["suspicious_clusters"][0]
    assert cluster["physical_intersection_shape"] == "none"
    assert cluster["approach_axis_count"] == 1
    assert cluster["angle_continuity_score"] == 0.0
    assert cluster["aggregation_decision"] == "needs_map_review"
    assert "no stable cross/T intersection shape" in cluster["aggregation_reason"]


def test_topology_audit_passes_sparse_junctions(tmp_path: Path) -> None:
    net_file = tmp_path / "sparse.net.xml"
    net_file.write_text(
        """<net>
  <junction id="a" x="0.0" y="0.0" type="priority"/>
  <junction id="b" x="100.0" y="0.0" type="priority"/>
  <junction id="c" x="0.0" y="100.0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology",
        cluster_radius_m=30.0,
        min_cluster_nodes=3,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["topology_fragmentation_status"] == "pass"
    assert report["suspicious_cluster_count"] == 0


def test_sumo_osm_resolve_place_tool_returns_candidate(monkeypatch) -> None:
    from torii_sumo.tools import osm_tools

    monkeypatch.setattr(
        osm_tools,
        "resolve_osm_place",
        lambda place_name, **_kwargs: {
            "status": "pass",
            "candidate_bbox": "13.6864402,51.0280799,13.7872926,51.0766681",
            "area_input": place_name,
        },
    )

    report = osm_tools.sumo_osm_resolve_place("Altstadt, Dresden")

    assert report["status"] == "pass"
    assert report["area_input"] == "Altstadt, Dresden"
    assert report["candidate_bbox"] == "13.6864402,51.0280799,13.7872926,51.0766681"


def test_merge_osm_xml_payloads_deduplicates_nodes_ways_and_relations() -> None:
    left = b"""<osm version="0.6">
  <node id="1" lat="51.0" lon="13.7"/>
  <way id="10"><nd ref="1"/><tag k="highway" v="primary"/></way>
</osm>"""
    right = b"""<osm version="0.6">
  <node id="1" lat="51.0" lon="13.7"/>
  <node id="2" lat="51.0" lon="13.8"/>
  <way id="10"><nd ref="1"/><tag k="highway" v="primary"/></way>
  <relation id="20"><member type="way" ref="10" role="from"/><tag k="type" v="restriction"/></relation>
</osm>"""

    merged = merge_osm_xml_payloads([left, right])
    root = ET.fromstring(merged)

    assert [node.attrib["id"] for node in root.findall("node")] == ["1", "2"]
    assert [way.attrib["id"] for way in root.findall("way")] == ["10"]
    assert [relation.attrib["id"] for relation in root.findall("relation")] == ["20"]


def test_robust_download_osm_retries_and_merges_tile_payloads() -> None:
    bbox = parse_bbox("13.0,50.0,14.0,51.0")
    calls = 0

    def flaky_download(query: str, *, url: str, user_agent: str, timeout_seconds: float) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary overpass timeout")
        return f"""<osm version="0.6">
  <node id="{calls}" lat="51.0" lon="13.7"/>
  <way id="{calls + 100}"><nd ref="{calls}"/><tag k="highway" v="primary"/></way>
</osm>""".encode("utf-8")

    payload, report = robust_download_osm(
        bbox,
        timeout_seconds=120,
        historical_date=None,
        overpass_url="https://example.test/interpreter",
        user_agent="test-agent",
        max_tile_area_km2=1000.0,
        max_retries=2,
        retry_pause_seconds=0.0,
        download_func=flaky_download,
    )
    root = ET.fromstring(payload)

    assert report["tile_count"] > 1
    assert report["retry_count"] == 1
    assert len(root.findall("way")) == report["tile_count"]
    assert calls == report["tile_count"] + 1


def test_filter_osm_by_highways_keeps_nodes_and_restrictions_for_kept_ways(tmp_path: Path) -> None:
    source = tmp_path / "source.osm.xml.gz"
    target = tmp_path / "target.osm.xml.gz"
    payload = b"""<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <node id="3" lat="51.0" lon="13.72"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
  </way>
  <way id="11">
    <nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="residential"/>
  </way>
  <relation id="20">
    <member type="way" ref="10" role="from"/>
    <tag k="type" v="restriction"/>
  </relation>
</osm>"""
    source.write_bytes(gzip.compress(payload))

    stats = filter_osm_by_highways(source, target, {"primary"})

    root = ET.fromstring(gzip.decompress(target.read_bytes()))
    assert [node.attrib["id"] for node in root.findall("node")] == ["1", "2"]
    assert [way.attrib["id"] for way in root.findall("way")] == ["10"]
    assert [relation.attrib["id"] for relation in root.findall("relation")] == ["20"]
    assert stats == {
        "kept_nodes": 2,
        "kept_ways": 1,
        "dropped_ways": 1,
        "kept_relations": 1,
    }


def test_filter_drops_vehicle_area_perimeters_but_keeps_linear_and_pedestrian_ways(tmp_path):
    source, target = tmp_path / "source.osm", tmp_path / "filtered.osm"
    root = ET.fromstring('<osm><node id="1" lat="0" lon="0"/><node id="2" lat="0" lon="1"/><node id="3" lat="1" lon="1"/></osm>')
    for identifier, highway, area in [("area", "service", "yes"), ("road", "service", "no"),
                                      ("square", "pedestrian", "yes")]:
        way = ET.SubElement(root, "way", id=identifier)
        for ref in ["1", "2", "3", "1"]:
            ET.SubElement(way, "nd", ref=ref)
        ET.SubElement(way, "tag", k="highway", v=highway)
        ET.SubElement(way, "tag", k="area", v=area)
    ET.ElementTree(root).write(source)
    original = source.read_bytes()
    stats = filter_osm_by_highways(source, target, {"service", "pedestrian"})
    assert {w.get("id") for w in ET.parse(target).getroot().findall("way")} == {"road", "square"}
    assert stats["dropped_vehicle_area_way_ids"] == ["area"]
    assert stats["dropped_ways"] == 1
    assert source.read_bytes() == original


def test_filter_osm_by_highways_selects_whole_ways_intersecting_bbox(tmp_path: Path) -> None:
    source = tmp_path / "source.osm.xml"
    target = tmp_path / "filtered.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lon="10.0" lat="50.0"/>
  <node id="2" lon="10.5" lat="50.5"/>
  <node id="3" lon="10.7" lat="50.7"/>
  <node id="4" lon="12.0" lat="52.0"/>
  <way id="101">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>
    <tag k="highway" v="residential"/>
  </way>
</osm>""",
        encoding="utf-8",
    )

    stats = filter_osm_by_highways(
        source,
        target,
        {"residential"},
        bbox=parse_bbox("10.4,50.4,10.8,50.8"),
    )

    root = ET.parse(target).getroot()
    kept_node_ids = [node.attrib["id"] for node in root.findall("node")]
    kept_refs = [node.attrib["ref"] for node in root.find("way").findall("nd")]
    assert kept_node_ids == ["1", "2", "3", "4"]
    assert kept_refs == ["1", "2", "3", "4"]
    assert stats["kept_nodes"] == 4
    assert stats["kept_ways"] == 1
    assert stats["trimmed_ways"] == 0
    assert stats["dropped_nodes_outside_bbox"] == 0
    assert stats["bbox_selection_mode"] == "whole_ways_intersecting_bbox"
    assert stats["retained_nodes_outside_bbox"] == 2


def test_bbox_selection_preserves_reentry_crossings_and_restriction_nodes(tmp_path: Path) -> None:
    source, target = tmp_path / "source.osm.xml", tmp_path / "selected.osm.xml"
    root = ET.Element("osm", version="0.6")
    shapes = {
        "reentry": [(0.2, 0.2), (2, 0.5), (0.8, 0.8)],
        "through": [(-1, 0.5), (2, 0.5)],
        "corner": [(-1, 1), (1, -1)],
        "bbox_overlap_only": [(-1, 0.5), (0.5, 2)],
        "outside": [(2, 2), (3, 3)],
    }
    for identifier, points in shapes.items():
        way = ET.SubElement(root, "way", id=identifier)
        for index, (lon, lat) in enumerate(points):
            ref = f"{identifier}_{index}"
            ET.SubElement(root, "node", id=ref, lon=str(lon), lat=str(lat))
            ET.SubElement(way, "nd", ref=ref)
        ET.SubElement(way, "tag", k="highway", v="primary")
    relation = ET.SubElement(root, "relation", id="restriction")
    ET.SubElement(relation, "member", type="way", ref="reentry", role="from")
    ET.SubElement(relation, "member", type="node", ref="reentry_1", role="via")
    ET.SubElement(relation, "tag", k="type", v="restriction")
    ET.ElementTree(root).write(source, encoding="utf-8")
    original = source.read_bytes()
    stats = filter_osm_by_highways(source, target, {"primary"}, bbox=parse_bbox("0,0,1,1"))
    selected = ET.parse(target).getroot()
    assert {way.get("id") for way in selected.findall("way")} == {"reentry", "through", "corner"}
    for way in selected.findall("way"):
        assert ET.tostring(way) == ET.tostring(root.find(f"way[@id='{way.get('id')}']"))
    assert selected.find("node[@id='reentry_1']") is not None
    assert ET.tostring(selected.find("relation")) == ET.tostring(relation)
    assert stats["dropped_ways_outside_bbox"] == 2
    assert source.read_bytes() == original


def test_filter_osm_by_highways_limits_to_reference_way_scope(tmp_path: Path) -> None:
    source = tmp_path / "source.osm.xml"
    target = tmp_path / "filtered.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lon="10.0" lat="50.0"/>
  <node id="2" lon="10.1" lat="50.1"/>
  <node id="3" lon="10.2" lat="50.2"/>
  <way id="101">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
  </way>
  <way id="202">
    <nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="primary"/>
  </way>
  <relation id="30">
    <member type="way" ref="101" role="from"/>
    <member type="way" ref="202" role="to"/>
    <tag k="type" v="restriction"/>
  </relation>
</osm>""",
        encoding="utf-8",
    )

    stats = filter_osm_by_highways(
        source,
        target,
        {"primary"},
        allowed_way_ids={"101"},
    )

    root = ET.parse(target).getroot()
    assert [way.attrib["id"] for way in root.findall("way")] == ["101"]
    assert [node.attrib["id"] for node in root.findall("node")] == ["1", "2"]
    assert [relation.attrib["id"] for relation in root.findall("relation")] == ["30"]
    assert stats["kept_ways"] == 1
    assert stats["dropped_ways_outside_reference_scope"] == 1


def test_filter_osm_by_highways_forced_ways_bypass_categories_but_not_bbox(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.osm.xml"
    target = tmp_path / "filtered.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lon="10.0" lat="50.0"/>
  <node id="2" lon="10.1" lat="50.1"/>
  <node id="3" lon="10.2" lat="50.2"/>
  <node id="4" lon="10.3" lat="50.3"/>
  <node id="5" lon="12.0" lat="52.0"/>
  <node id="6" lon="12.1" lat="52.1"/>
  <way id="101">
    <nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/>
  </way>
  <way id="202">
    <nd ref="2"/><nd ref="3"/><tag k="highway" v="construction"/>
  </way>
  <way id="203">
    <nd ref="3"/><nd ref="4"/><tag k="highway" v="construction"/>
  </way>
  <way id="303">
    <nd ref="5"/><nd ref="6"/><tag k="highway" v="residential"/>
  </way>
  <way id="404">
    <nd ref="3"/><nd ref="4"/><tag k="highway" v="residential"/>
  </way>
</osm>""",
        encoding="utf-8",
    )

    stats = filter_osm_by_highways(
        source,
        target,
        {"primary", "construction"},
        bbox=parse_bbox("9.9,49.9,10.5,50.5"),
        allowed_way_ids={"101"},
        forced_way_ids={"202", "303", "404", "999"},
    )

    root = ET.parse(target).getroot()
    assert [way.attrib["id"] for way in root.findall("way")] == [
        "101",
        "202",
        "404",
    ]
    assert stats["forced_way_ids_requested"] == ["202", "303", "404", "999"]
    assert stats["forced_way_ids_kept"] == ["202", "404"]
    assert stats["forced_way_ids_missing"] == ["303", "999"]
    assert stats["forced_construction_way_ids_kept"] == ["202"]
    assert stats["dropped_ways_outside_bbox"] == 1


def test_build_osm_network_fails_closed_when_forced_way_is_missing(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <node id="3" lat="53.0" lon="15.70"/>
  <node id="4" lat="53.0" lon="15.71"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/>
  </way>
  <way id="999">
    <nd ref="3"/><nd ref="4"/><tag k="highway" v="residential"/>
  </way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        output_dir=tmp_path / "build",
        source_osm_path=source,
        allowed_highways={"primary"},
        forced_way_ids={"999"},
        clip_source_ways_to_bbox=False,
        command_runner=lambda command, **_kwargs: calls.append(command),
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["forced_way_ids_requested"] == ["999"]
    assert report["forced_way_ids_kept"] == []
    assert report["forced_way_ids_missing"] == ["999"]
    assert report["filter_stats"]["forced_way_ids_missing"] == ["999"]
    assert "not kept inside the requested bbox" in report["error"]
    assert calls == []


def test_build_osm_network_forced_construction_uses_official_typemap_overlay(
    tmp_path: Path,
) -> None:
    typemap_dir = tmp_path / "sumo_home" / "data" / "typemap"
    typemap_dir.mkdir(parents=True)
    base_typemap = typemap_dir / "osmNetconvert.typ.xml"
    base_typemap.write_text(
        """<types>
  <type id="highway.primary" numLanes="2" speed="27.78"/>
  <type id="highway.construction" numLanes="1" speed="13.89"
        priority="4" oneway="false" discard="true"/>
</types>""",
        encoding="utf-8",
    )
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <node id="3" lat="51.0" lon="13.72"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><tag k="highway" v="construction"/>
  </way>
  <way id="11">
    <nd ref="2"/><nd ref="3"/><tag k="highway" v="construction"/>
  </way>
  <way id="12">
    <nd ref="1"/><nd ref="3"/><tag k="highway" v="primary"/>
  </way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> CommandResult:
        calls.append(command)
        assert cwd is not None
        output = cwd / command[command.index("--output-file") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("<net/>", encoding="utf-8")
        return CommandResult(
            command=command,
            cwd=str(cwd),
            status="pass",
            returncode=0,
        )

    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        output_dir=tmp_path / "build",
        prefix="demo",
        source_osm_path=source,
        allowed_highways={"primary", "construction"},
        allowed_way_ids={"12"},
        forced_way_ids={"10"},
        sumo_home=tmp_path / "sumo_home",
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["forced_way_ids_requested"] == ["10"]
    assert report["forced_way_ids_kept"] == ["10"]
    assert report["forced_way_ids_missing"] == []
    assert report["forced_construction_way_ids_kept"] == ["10"]
    filtered_root = ET.fromstring(gzip.decompress(Path(report["filtered_osm_file"]).read_bytes()))
    assert [way.attrib["id"] for way in filtered_root.findall("way")] == [
        "10",
        "12",
    ]
    command = calls[0]
    type_files = command[command.index("--type-files") + 1].split(",")
    assert type_files == [
        str(base_typemap),
        "logs/demo_forced_construction.typ.xml",
    ]
    overlay_file = Path(report["forced_construction_type_overlay_file"])
    overlay_type = ET.parse(overlay_file).getroot().find("type")
    assert overlay_type is not None
    assert overlay_type.attrib == {
        "id": "highway.construction",
        "numLanes": "1",
        "speed": "13.89",
        "priority": "4",
        "oneway": "false",
        "discard": "false",
    }


def test_build_osm_network_forced_construction_rejects_unparseable_typemap(
    tmp_path: Path,
) -> None:
    typemap_dir = tmp_path / "sumo_home" / "data" / "typemap"
    typemap_dir.mkdir(parents=True)
    (typemap_dir / "osmNetconvert.typ.xml").write_text(
        "<types>",
        encoding="utf-8",
    )
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><tag k="highway" v="construction"/>
  </way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        output_dir=tmp_path / "build",
        source_osm_path=source,
        allowed_highways={"primary"},
        forced_way_ids={"10"},
        sumo_home=tmp_path / "sumo_home",
        command_runner=lambda command, **_kwargs: calls.append(command),
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert "not parseable" in report["error"]
    assert report["forced_way_ids_kept"] == ["10"]
    assert calls == []


def test_build_osm_network_from_existing_osm_runs_netconvert_and_records_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
  </way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path | None = None, timeout_seconds: float = 60.0):
        calls.append(command)
        output = Path(command[command.index("--output-file") + 1])
        if not output.is_absolute():
            assert cwd is not None
            output = cwd / output
        assert output.parent.is_dir()
        output.write_text("<net/>", encoding="utf-8")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        output_dir=tmp_path / "build",
        prefix="demo",
        source_osm_path=source,
        allowed_highways={"primary"},
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert Path(report["filtered_osm_file"]).is_file()
    assert Path(report["net_file"]).is_file()
    assert Path(report["command_record"]).is_file()
    assert Path(report["build_manifest_file"]).is_file()
    assert report["netconvert_output_original_names"]["requested"] is True
    assert report["netconvert_output_original_names"]["netconvert_option"] == "--output.original-names"
    manifest = json.loads(Path(report["build_manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["build_scope"] == {
        "allowed_way_ids_count": None,
        "bbox": "13.6000,50.9800,13.9000,51.1500",
        "clip_source_ways_to_bbox": True,
        "bbox_selection_mode": "whole_ways_intersecting_bbox",
        "geo_boundary": None,
        "include_railway": False,
        "road_classes": ["primary"],
        "tls_join_distance_m": 35.0,
        "traffic_side": "right",
    }
    assert manifest["netconvert"]["output_original_names"]["requested"] is True
    assert manifest["netconvert"]["output_original_names"]["expected_parameter_keys"] == [
        "origId",
        "origID",
    ]
    assert manifest["sumo_road_snapshot_import_contract"] == report["sumo_road_snapshot_import_contract"]
    assert (
        manifest["sumo_road_snapshot_import_contract"]["imported_source_sha256"]
        == manifest["netconvert_input_osm_snapshot"]["sha256"]
    )
    command_log = Path(report["command_record"]).read_text(encoding="utf-8")
    assert "netconvert_output_original_names_requested=true" in command_log
    assert "netconvert_output_original_names_option=--output.original-names" in command_log
    assert calls == [
        [
            "netconvert",
            "--osm-files",
            "osm/demo_filtered.osm.xml.gz",
            "--output-file",
            "sumo/demo.net.xml",
            "--proj.utm",
            "--junctions.internal-link-detail",
            "25",
            "--no-turnarounds",
            "--osm.all-attributes",
            "--output.original-names",
            "--tls.join",
            "--tls.join-dist",
            "35",
            "--verbose",
        ]
    ]


def test_build_osm_network_passes_geo_boundary_to_netconvert(tmp_path: Path) -> None:
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/></way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path, timeout_seconds: float) -> CommandResult:
        calls.append(command)
        output = cwd / command[command.index("--output-file") + 1]
        output.write_text("<net/>", encoding="utf-8")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    boundary = "13.69,50.99,13.72,50.99,13.72,51.01,13.69,51.01"
    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        geo_boundary=boundary,
        tls_join_distance_m=75.0,
        output_dir=tmp_path / "build",
        source_osm_path=source,
        allowed_highways={"primary"},
        command_runner=fake_runner,
    )

    command = calls[0]
    assert command[command.index("--keep-edges.in-geo-boundary") + 1] == boundary
    assert command[command.index("--tls.join-dist") + 1] == "75"
    assert report["geo_boundary"] == boundary


def test_build_osm_network_reference_visual_detail_profile_imports_pedestrian_tls_structure(
    tmp_path: Path, monkeypatch
) -> None:
    typemap_dir = tmp_path / "sumo_home" / "data" / "typemap"
    typemap_dir.mkdir(parents=True)
    for name in ("osmNetconvert.typ.xml", "osmNetconvertBicycle.typ.xml", "osmNetconvertPedestrians.typ.xml"):
        (typemap_dir / name).write_text("<types/>", encoding="utf-8")
    monkeypatch.setenv("SUMO_HOME", str(tmp_path / "sumo_home"))

    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
    <tag k="sidewalk" v="both"/>
  </way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(command: list[str], *, cwd: Path | None = None, timeout_seconds: float = 60.0):
        calls.append(command)
        output = Path(command[command.index("--output-file") + 1])
        if not output.is_absolute():
            assert cwd is not None
            output = cwd / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("<net/>", encoding="utf-8")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        output_dir=tmp_path / "build",
        prefix="demo",
        source_osm_path=source,
        allowed_highways={"primary"},
        netconvert_profile="reference_visual_detail",
        command_runner=fake_runner,
    )

    command = calls[0]
    assert report["status"] == "pass"
    assert report["netconvert_profile"] == "reference_visual_detail"
    assert report["netconvert_profile_options"] == [
        "--osm.bike-access",
        "--osm.sidewalks",
        "--osm.crossings",
        "--osm.turn-lanes",
        "--sidewalks.guess.from-permissions",
        "--crossings.guess",
        "--walkingareas",
        "--tls.guess",
        "--tls.guess-signals",
        "--tls.rebuild",
        "--tls.default-type",
        "actuated",
    ]
    for option in report["netconvert_profile_options"]:
        assert option in command
    assert "--tls.guess.joining" not in command
    assert "--no-turnarounds" not in command
    type_files = command[command.index("--type-files") + 1].split(",")
    assert type_files[:-1] == [
        str(typemap_dir / "osmNetconvert.typ.xml"),
        str(typemap_dir / "osmNetconvertBicycle.typ.xml"),
        str(typemap_dir / "osmNetconvertPedestrians.typ.xml"),
    ]
    service_type_file = tmp_path / "build" / type_files[-1]
    assert service_type_file.is_file()
    service_type_text = service_type_file.read_text(encoding="utf-8")
    assert 'id="highway.service"' in service_type_text
    assert 'disallow="pedestrian' in service_type_text


def test_build_osm_network_passes_explicit_left_hand_traffic_to_netconvert(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="-33.85" lon="151.20"/>
  <node id="2" lat="-33.84" lon="151.21"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/></way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_runner(
        command: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 60.0,
    ) -> CommandResult:
        calls.append(command)
        assert cwd is not None
        output = cwd / command[command.index("--output-file") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("<net/>", encoding="utf-8")
        return CommandResult(
            command=command,
            cwd=str(cwd),
            status="pass",
            returncode=0,
        )

    report = build_osm_network(
        bbox="151.19,-33.86,151.22,-33.83",
        output_dir=tmp_path / "build",
        source_osm_path=source,
        allowed_highways={"primary"},
        traffic_side="left",
        command_runner=fake_runner,
    )

    assert report["status"] == "pass"
    assert report["traffic_side"] == "left"
    assert "--lefthand" in calls[0]


def test_build_osm_network_rejects_tum_named_netconvert_profile(tmp_path: Path) -> None:
    source = tmp_path / "input.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="51.0" lon="13.70"/>
  <node id="2" lat="51.0" lon="13.71"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
  </way>
</osm>""",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    report = build_osm_network(
        bbox="13.6000,50.9800,13.9000,51.1500",
        output_dir=tmp_path / "build",
        prefix="demo",
        source_osm_path=source,
        allowed_highways={"primary"},
        netconvert_profile="tum_like_visual_detail",
        command_runner=lambda command, **_kwargs: calls.append(command),
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["error"] == "unsupported netconvert_profile: tum_like_visual_detail"
    assert calls == []


def test_build_osm_network_uses_robust_downloader_when_no_source_osm(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_download(query: str, *, url: str, user_agent: str, timeout_seconds: float) -> bytes:
        calls.append(query)
        node_id = len(calls)
        return f"""<osm version="0.6">
  <node id="{node_id}" lat="51.0" lon="13.70"/>
  <node id="{node_id + 100}" lat="51.0" lon="13.71"/>
  <way id="{node_id + 200}">
    <nd ref="{node_id}"/><nd ref="{node_id + 100}"/>
    <tag k="highway" v="primary"/>
  </way>
</osm>""".encode("utf-8")

    def fake_runner(command: list[str], *, cwd: Path | None = None, timeout_seconds: float = 60.0):
        output = Path(command[command.index("--output-file") + 1])
        assert cwd is not None
        (cwd / output).parent.mkdir(parents=True, exist_ok=True)
        (cwd / output).write_text("<net/>", encoding="utf-8")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = build_osm_network(
        bbox="13.0,50.0,14.0,51.0",
        output_dir=tmp_path / "build",
        prefix="robust",
        allowed_highways={"primary"},
        max_tile_area_km2=1000.0,
        command_runner=fake_runner,
        download_func=fake_download,
    )

    assert report["status"] == "pass"
    assert report["overpass"]["tile_count"] == len(calls)
    assert report["overpass"]["tile_count"] > 1
    assert Path(report["source_osm_file"]).is_file()


def test_sumo_osm_build_network_reports_invalid_bbox_as_construction_invalid(tmp_path: Path) -> None:
    report = sumo_osm_build_network(
        bbox="13.9,51.0,13.6,51.1",
        output_dir=str(tmp_path),
        source_osm_path=str(tmp_path / "missing.osm.xml"),
    )

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert "bbox west must be smaller than east" in report["error"]


def test_cluster_tls_candidates_uses_transitive_distance_components() -> None:
    rows = [
        {"tls_id": "a", "lat": "51.0000000", "lon": "13.7000000", "incoming_road_names": "Main"},
        {"tls_id": "b", "lat": "51.0001000", "lon": "13.7000000", "incoming_road_names": "Main"},
        {"tls_id": "c", "lat": "51.0002000", "lon": "13.7000000", "incoming_road_names": "Main"},
        {"tls_id": "d", "lat": "51.0100000", "lon": "13.7100000", "incoming_road_names": "Far"},
    ]

    clusters = cluster_tls_candidates(rows, radius_m=15.0)

    assert sorted(cluster["tls_ids"] for cluster in clusters) == ["a;b;c", "d"]


def test_google_maps_baseline_fields_record_temporal_scope() -> None:
    current = google_maps_baseline_fields("current", None)
    historical = google_maps_baseline_fields("historical", "2020-05")
    unspecified = google_maps_baseline_fields("unspecified", None)

    assert current["google_maps_baseline_source"] == "Google Maps"
    assert current["google_maps_temporal_scope"] == "current"
    assert current["google_maps_requires_time_confirmation"] == "no"
    assert historical["google_maps_temporal_scope"] == "historical"
    assert historical["google_maps_target_date"] == "2020-05"
    assert historical["google_maps_requires_time_confirmation"] == "no"
    assert unspecified["google_maps_requires_time_confirmation"] == "yes"


def test_regional_map_fields_use_amap_for_mainland_china_coordinate() -> None:
    fields = regional_map_fields(39.959984, 116.315469, label="BIT Zhongguancun TLS")

    assert fields["regional_map_provider"] == "Amap/Gaode"
    assert fields["regional_map_coordinate_system"] == "GCJ-02"
    assert "uri.amap.com/marker" in fields["regional_map_url"]
    assert "BIT+Zhongguancun+TLS" in fields["regional_map_url"]
    assert "WGS84" in fields["regional_map_note"]


def test_build_tls_multisource_review_uses_amap_for_mainland_china_rows() -> None:
    rows = build_tls_multisource_review(
        [
            {
                "tls_id": "BIT-TLS-1",
                "lat": "39.9599840",
                "lon": "116.3154690",
                "connection_count": 8,
                "nearest_osm_signal_id": "",
                "nearest_osm_signal_distance_m": "",
                "has_osm_signal_within_35m": "no",
                "incoming_road_names": "Zhongguancun South Street",
                "outgoing_road_names": "Campus Road",
            }
        ]
    )

    assert rows[0]["regional_map_provider"] == "Amap/Gaode"
    assert rows[0]["regional_map_audit_status"] == "needs_amap_review"
    assert "uri.amap.com/marker" in rows[0]["regional_map_url"]
    assert rows[0]["google_maps_url"].startswith("https://www.google.com/maps/search/")


def test_build_tls_multisource_review_keeps_human_review_boundary() -> None:
    rows = build_tls_multisource_review(
        [
            {
                "tls_id": "J1",
                "lat": "51.0505920",
                "lon": "13.7339600",
                "connection_count": 12,
                "nearest_osm_signal_id": "osm-100",
                "nearest_osm_signal_distance_m": "8.5",
                "has_osm_signal_within_35m": "yes",
                "incoming_road_names": "Main Street",
                "outgoing_road_names": "Bridge Street",
                "google_maps_url": "https://www.google.com/maps/search/?api=1&query=51.050592,13.733960",
            },
            {
                "tls_id": "J2",
                "lat": "51.0600000",
                "lon": "13.7400000",
                "connection_count": 4,
                "nearest_osm_signal_id": "",
                "nearest_osm_signal_distance_m": "",
                "has_osm_signal_within_35m": "no",
                "incoming_road_names": "Side Road",
                "outgoing_road_names": "",
                "google_maps_url": "https://www.google.com/maps/search/?api=1&query=51.060000,13.740000",
            },
        ],
        official_inventory={"J1": {"status": "confirmed", "source_id": "agency-42", "note": "official inventory row"}},
        signal_plans={"J1": {"status": "available", "source_id": "plan-7", "note": "timing plan exists"}},
        field_evidence={"J1": {"status": "photo_confirmed", "source_id": "photo-3", "note": "field photo manifest"}},
    )

    confirmed = rows[0]
    assert confirmed["tls_id"] == "J1"
    assert confirmed["official_inventory_status"] == "confirmed"
    assert confirmed["official_inventory_id"] == "agency-42"
    assert confirmed["signal_plan_status"] == "available"
    assert confirmed["field_evidence_status"] == "photo_confirmed"
    assert confirmed["mapillary_url"].startswith("https://www.mapillary.com/app/")
    assert confirmed["kartaview_url"].startswith("https://kartaview.org/map/@51.050592,13.733960")
    assert confirmed["evidence_level"] == "authoritative"
    assert confirmed["review_status"] == "needs_manual_review"
    assert confirmed["claim_status"] == "diagnostic-demo"

    guessed = rows[1]
    assert guessed["tls_id"] == "J2"
    assert guessed["evidence_level"] == "sumo-guess-only"
    assert guessed["review_status"] == "needs_manual_review"
    assert guessed["claim_status"] == "diagnostic-demo"


def test_net_xy_to_latlon_falls_back_when_sumolib_reports_missing_pyproj() -> None:
    from torii_sumo.core.osm_network import _net_xy_to_latlon

    class FakeNet:
        _location = {"projParameter": "+proj=utm +zone=33 +ellps=WGS84 +datum=WGS84 +units=m +no_defs"}

        def convertXY2LonLat(self, _x: float, _y: float) -> tuple[float, float]:
            raise RuntimeError("Network does not provide geo-projection or pyproj not installed.")

        def getLocationOffset(self) -> tuple[float, float]:
            return (0.0, 0.0)

    lat, lon = _net_xy_to_latlon(FakeNet(), 391000.0, 5655000.0)

    assert 50.0 < lat < 52.0
    assert 12.0 < lon < 15.0


def test_cluster_tls_candidates_carries_google_maps_temporal_baseline() -> None:
    rows = [
        {"tls_id": "a", "lat": "51.0000000", "lon": "13.7000000", "incoming_road_names": "Main"},
        {"tls_id": "b", "lat": "51.0001000", "lon": "13.7000000", "incoming_road_names": "Main"},
    ]

    clusters = cluster_tls_candidates(
        rows,
        radius_m=15.0,
        google_maps_temporal_scope="historical",
        google_maps_target_date="2020-05",
    )

    assert clusters[0]["google_maps_baseline_source"] == "Google Maps"
    assert clusters[0]["google_maps_temporal_scope"] == "historical"
    assert clusters[0]["google_maps_target_date"] == "2020-05"
    assert clusters[0]["google_maps_requires_time_confirmation"] == "no"


def test_build_routeability_probe_uses_user_supplied_road_queries(tmp_path: Path) -> None:
    net_file = tmp_path / "network.net.xml"
    net_file.write_text(
        """<net>
  <edge id="pre" from="n0" to="n1">
    <lane id="pre_0" allow="passenger" speed="13.9" length="10.0"/>
    <param key="name" value="Approach Road"/>
  </edge>
  <edge id="main" from="n1" to="n2">
    <lane id="main_0" allow="passenger" speed="13.9" length="10.0"/>
    <param key="name" value="Main Road"/>
  </edge>
  <edge id="post" from="n2" to="n3">
    <lane id="post_0" allow="passenger" speed="13.9" length="10.0"/>
    <param key="name" value="Exit Road"/>
  </edge>
  <connection from="pre" to="main"/>
  <connection from="main" to="post"/>
</net>""",
        encoding="utf-8",
    )

    report = build_routeability_probe(
        net_file=net_file,
        output_dir=tmp_path / "probe",
        prefix="demo_probe",
        key_edge_queries=[{"label": "main_road", "role": "arterial", "search_terms": ["Main Road"]}],
    )

    route_root = ET.parse(report["route_file"]).getroot()
    cfg_root = ET.parse(report["sumocfg_file"]).getroot()
    key_rows = Path(report["key_edges_file"]).read_text(encoding="utf-8")

    assert report["status"] == "pass"
    assert route_root.find("route").attrib["edges"] == "pre main post"
    assert cfg_root.find("input/net-file").attrib["value"] == "../network.net.xml"
    assert "main_road,arterial,n1,n2,main" in key_rows


def test_all_permissions_agree_for_route_generation_and_passenger_components(tmp_path: Path) -> None:
    net_file = tmp_path / "permissions.net.xml"
    net_file.write_text('''<net>
      <edge id="pre" from="n0" to="n1"><lane id="pre_0" allow="all"/></edge>
      <edge id="main" from="n1" to="n2" name="Main Road"><lane id="main_0" allow="all"/></edge>
      <edge id="post" from="n2" to="n3"><lane id="post_0" allow="all"/></edge>
      <edge id="closed" from="x" to="y"><lane id="closed_0" disallow="all"/></edge>
      <connection from="pre" to="main"/><connection from="main" to="post"/>
    </net>''', encoding="utf-8")
    report = build_routeability_probe(net_file=net_file, output_dir=tmp_path / "probe",
        key_edge_queries=[{"label": "main", "role": "arterial", "search_terms": ["Main Road"]}])
    assert report["status"] == "pass"
    assert ET.parse(report["route_file"]).find("route").get("edges") == "pre main post"
    connectivity = summarize_passenger_connectivity(net_file=net_file)
    assert connectivity["passenger_edge_count"] == 3
    assert connectivity["passenger_component_count"] == 1


def test_summarize_passenger_connectivity_passes_single_component(tmp_path: Path) -> None:
    net_file = tmp_path / "connected.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n0" to="n1">
    <lane id="a_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
  <edge id="b" from="n1" to="n2">
    <lane id="b_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
  <connection from="a" to="b"/>
</net>""",
        encoding="utf-8",
    )

    report = summarize_passenger_connectivity(net_file)

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["connectivity_status"] == "pass"
    assert report["passenger_edge_count"] == 2
    assert report["passenger_component_count"] == 1
    assert report["largest_component_edge_count"] == 2
    assert report["small_component_count"] == 0
    assert report["isolated_passenger_edge_count"] == 0


def test_summarize_passenger_connectivity_fails_disconnected_components(tmp_path: Path) -> None:
    net_file = tmp_path / "disconnected.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n0" to="n1">
    <lane id="a_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
  <edge id="b" from="n2" to="n3">
    <lane id="b_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
</net>""",
        encoding="utf-8",
    )

    report = summarize_passenger_connectivity(net_file)

    assert report["status"] == "fail"
    assert report["claim_status"] == "construction-invalid"
    assert report["connectivity_status"] == "fail"
    assert report["passenger_edge_count"] == 2
    assert report["passenger_component_count"] == 2
    assert report["largest_component_edge_count"] == 1
    assert report["small_component_count"] == 2
    assert report["isolated_passenger_edge_count"] == 2
    assert "passenger network has 2 disconnected components" in report["warnings"]


def test_extract_largest_passenger_component_core_writes_keep_and_discard_records(tmp_path: Path) -> None:
    net_file = tmp_path / "raw.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n0" to="n1">
    <lane id="a_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
  <edge id="b" from="n1" to="n2">
    <lane id="b_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
  <edge id="c" from="n3" to="n4">
    <lane id="c_0" allow="passenger" speed="13.9" length="10.0"/>
  </edge>
  <connection from="a" to="b"/>
</net>""",
        encoding="utf-8",
    )

    calls = []

    def fake_command_runner(command, **kwargs):
        calls.append((command, kwargs))
        output_file = Path(kwargs["cwd"]) / command[command.index("--output-file") + 1]
        output_file.write_text("<net/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": "", "error": ""}

    report = extract_largest_passenger_component_core(
        net_file,
        output_dir=tmp_path / "core",
        prefix="demo",
        command_runner=fake_command_runner,
    )

    keep_edges = Path(report["keep_edges_file"]).read_text(encoding="utf-8").splitlines()
    discard_rows = list(csv.DictReader(Path(report["discarded_components_file"]).open(encoding="utf-8")))
    review_payload = json.loads(Path(report["discarded_components_review_file"]).read_text(encoding="utf-8"))

    assert report["status"] == "pass"
    assert report["network_quality"] == "connected-core"
    assert report["raw_passenger_component_count"] == 2
    assert report["core_passenger_edge_count"] == 2
    assert report["discarded_passenger_edge_count"] == 1
    assert keep_edges == ["a", "b"]
    assert discard_rows == [
        {
            "component_rank": "2",
            "component_size": "1",
            "edge_id": "c",
            "discard_reason": "outside_largest_passenger_component",
        }
    ]
    assert review_payload["status"] == "pending_review"
    assert review_payload["component_count"] == 1
    assert review_payload["records"][0]["location_id"] == "disconnected_component_002"
    assert review_payload["records"][0]["decision"] == "pending"
    command = calls[0][0]
    assert command[:2] == ["netconvert", "--sumo-net-file"]
    assert "--keep-edges.input-file" in command
    assert "--keep-edges.postload" in command


def test_extract_largest_passenger_component_core_uses_cwd_relative_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    net_file = Path("raw.net.xml")
    net_file.write_text(
        """<net>
  <edge id="a" from="n0" to="n1"><lane id="a_0" allow="passenger" speed="13.9" length="10.0"/></edge>
  <edge id="b" from="n1" to="n2"><lane id="b_0" allow="passenger" speed="13.9" length="10.0"/></edge>
  <edge id="c" from="n3" to="n4"><lane id="c_0" allow="passenger" speed="13.9" length="10.0"/></edge>
  <connection from="a" to="b"/>
</net>""",
        encoding="utf-8",
    )

    def fake_command_runner(command, **kwargs):
        cwd = Path(kwargs["cwd"])
        keep_path = cwd / command[command.index("--keep-edges.input-file") + 1]
        output_path = cwd / command[command.index("--output-file") + 1]
        assert keep_path.exists()
        output_path.write_text("<net/>", encoding="utf-8")
        return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": "", "error": ""}

    report = extract_largest_passenger_component_core(
        net_file,
        output_dir=Path("core"),
        prefix="demo",
        command_runner=fake_command_runner,
    )

    assert report["status"] == "pass"
    assert Path(report["connected_core_file"]).exists()


def test_extract_largest_passenger_component_core_falls_back_without_postload(
    tmp_path: Path,
) -> None:
    net_file = tmp_path / "raw.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="n0" to="n1"><lane id="a_0" allow="passenger" speed="13.9" length="10.0"/></edge>
  <edge id="b" from="n1" to="n2"><lane id="b_0" allow="passenger" speed="13.9" length="10.0"/></edge>
  <edge id="c" from="n3" to="n4"><lane id="c_0" allow="passenger" speed="13.9" length="10.0"/></edge>
  <connection from="a" to="b"/>
</net>""",
        encoding="utf-8",
    )

    calls = []

    def fake_command_runner(command, **kwargs):
        calls.append(command)
        if "--keep-edges.postload" not in command:
            output_file = Path(kwargs["cwd"]) / command[command.index("--output-file") + 1]
            output_file.write_text("<net/>", encoding="utf-8")
            return {"status": "pass", "returncode": 0, "stdout": "Success.", "stderr": "", "error": ""}
        return {"status": "fail", "returncode": 1, "stdout": "", "stderr": "PositionVector", "error": ""}

    report = extract_largest_passenger_component_core(
        net_file,
        output_dir=tmp_path / "core",
        prefix="fallback",
        command_runner=fake_command_runner,
    )

    assert report["status"] == "pass"
    assert report["fallback_used"] is True
    assert len(report["command_attempts"]) == 2
    assert "--keep-edges.postload" in calls[0]
    assert "--keep-edges.postload" not in calls[1]


def test_launch_netedit_reports_unavailable_when_binary_missing(tmp_path: Path) -> None:
    from torii_sumo.core.netedit import launch_netedit

    net_file = tmp_path / "network.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    report = launch_netedit(
        net_file,
        which_func=lambda _name: None,
    )

    assert report["status"] == "blocked"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["netedit_status"] == "unavailable"
    assert report["netedit_network_file"] == str(net_file)
    assert "netedit binary not found" in report["warnings"]


def test_launch_netedit_starts_non_blocking_process(tmp_path: Path) -> None:
    from torii_sumo.core.netedit import launch_netedit

    class FakeProcess:
        pid = 12345

    calls: list[list[str]] = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        assert kwargs["stdin"] is not None
        assert kwargs["stdout"] is not None
        assert kwargs["stderr"] is not None
        return FakeProcess()

    net_file = tmp_path / "network.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    report = launch_netedit(
        net_file,
        which_func=lambda _name: "C:/SUMO/bin/netedit.exe",
        popen_func=fake_popen,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["netedit_status"] == "opened"
    assert report["netedit_binary"] == "C:/SUMO/bin/netedit.exe"
    assert report["netedit_process_id"] == 12345
    assert calls == [["C:/SUMO/bin/netedit.exe", "--sumo-net-file", str(net_file)]]


def test_launch_netedit_opens_sumo_config_with_additional_files(tmp_path: Path) -> None:
    from torii_sumo.core.netedit import launch_netedit

    class FakeProcess:
        pid = 23456

    calls: list[list[str]] = []

    def fake_popen(command, **_kwargs):
        calls.append(command)
        return FakeProcess()

    sumocfg_file = tmp_path / "review.sumocfg"
    sumocfg_file.write_text("<configuration/>", encoding="utf-8")

    report = launch_netedit(
        sumocfg_file,
        which_func=lambda _name: "C:/SUMO/bin/netedit.exe",
        popen_func=fake_popen,
    )

    assert report["netedit_status"] == "opened"
    assert report["netedit_open_mode"] == "sumocfg"
    assert report["netedit_input_file"] == str(sumocfg_file)
    assert calls == [["C:/SUMO/bin/netedit.exe", "--sumocfg-file", str(sumocfg_file)]]


def test_launch_netedit_accepts_review_selection_view_and_window_options(tmp_path: Path) -> None:
    from torii_sumo.core.netedit import launch_netedit

    class FakeProcess:
        pid = 34567

    calls: list[list[str]] = []

    def fake_popen(command, **_kwargs):
        calls.append(command)
        return FakeProcess()

    sumocfg_file = tmp_path / "review.sumocfg"
    selection_file = tmp_path / "target.selection.txt"
    view_file = tmp_path / "target.view.xml"
    for path in (sumocfg_file, selection_file, view_file):
        path.write_text("<xml/>", encoding="utf-8")

    report = launch_netedit(
        sumocfg_file,
        gui_settings_file=view_file,
        selection_file=selection_file,
        window_size="1000,900",
        window_pos="1020,0",
        which_func=lambda _name: "C:/SUMO/bin/netedit.exe",
        popen_func=fake_popen,
    )

    assert report["netedit_status"] == "opened"
    assert report["netedit_gui_settings_file"] == str(view_file)
    assert report["netedit_selection_file"] == str(selection_file)
    assert report["netedit_window_size"] == "1000,900"
    assert report["netedit_window_pos"] == "1020,0"
    assert calls == [
        [
            "C:/SUMO/bin/netedit.exe",
            "--sumocfg-file",
            str(sumocfg_file),
            "-g",
            str(view_file),
            "--selection-file",
            str(selection_file),
            "--window-size",
            "1000,900",
            "--window-pos",
            "1020,0",
        ]
    ]


def test_launch_sumo_gui_writes_minimal_config_and_starts_non_blocking_process(tmp_path: Path) -> None:
    from torii_sumo.core.sumo_gui import launch_sumo_gui

    class FakeProcess:
        pid = 24680

    calls: list[list[str]] = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        assert kwargs["stdin"] is not None
        assert kwargs["stdout"] is not None
        assert kwargs["stderr"] is not None
        return FakeProcess()

    net_file = tmp_path / "network.net.xml"
    net_file.write_text("<net/>", encoding="utf-8")

    report = launch_sumo_gui(
        net_file,
        output_dir=tmp_path / "gui",
        prefix="demo",
        which_func=lambda _name: "C:/SUMO/bin/sumo-gui.exe",
        popen_func=fake_popen,
    )

    cfg_file = Path(report["sumo_gui_config_file"])
    cfg_root = ET.parse(cfg_file).getroot()

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["sumo_gui_status"] == "opened"
    assert report["sumo_gui_binary"] == "C:/SUMO/bin/sumo-gui.exe"
    assert report["sumo_gui_process_id"] == 24680
    assert report["sumo_gui_network_file"] == str(net_file)
    assert cfg_root.find("input/net-file").attrib["value"] == str(net_file)
    assert cfg_root.find("time/end").attrib["value"] == "1"
    assert calls == [["C:/SUMO/bin/sumo-gui.exe", "-c", str(cfg_file)]]
