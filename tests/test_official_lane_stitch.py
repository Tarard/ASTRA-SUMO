from __future__ import annotations

import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pyproj import Transformer

import torii_sumo.road_network.official_lane_stitch as official_lane_stitch
from torii_sumo.road_network.official_lane_stitch import (
    OfficialLaneAxisStitchError,
    plan_hamburg_official_map_lane_axis_stitch,
)
from torii_sumo.road_network.official_plainxml import OFFICIAL_PLAINXML_SCHEMA


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ROOT = (
    REPO_ROOT
    / "artifacts"
    / "hamburg_sandtorkai_twin_20260719"
    / "official_first_named_corridor_v1"
)


def _write_official_inputs(tmp_path: Path, *, duplicate_axis: bool = False) -> dict[str, Path]:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
    west = transformer.transform(9.9900, 53.5400)
    east = transformer.transform(9.9920, 53.5400)
    nodes_path = tmp_path / "official.nod.xml"
    edges_path = tmp_path / "official.edg.xml"
    manifest_path = tmp_path / "official.manifest.json"
    map_source_path = tmp_path / "map.kml"
    report_path = tmp_path / "binding.json"

    node_root = ET.Element("nodes")
    ET.SubElement(node_root, "node", id="west", x=str(west[0]), y=str(west[1]))
    ET.SubElement(node_root, "node", id="east", x=str(east[0]), y=str(east[1]))
    _write_xml(nodes_path, node_root)

    edge_root = ET.Element("edges")
    links = ["official.hh_sib:test-link"]
    if duplicate_axis:
        links.append("official.hh_sib:duplicate-link")
    for index, official_link in enumerate(links):
        for direction, from_node, to_node, shape in (
            ("with_stationing", "west", "east", (west, east)),
            ("against_stationing", "east", "west", (east, west)),
        ):
            edge = ET.SubElement(
                edge_root,
                "edge",
                id=f"axis-{index}-{direction}",
                **{
                    "from": from_node,
                    "to": to_node,
                    "numLanes": "2",
                    "shape": " ".join(f"{x},{y}" for x, y in shape),
                },
            )
            params = {
                "origId": official_link,
                "torii:source_sha256": "a" * 64,
                "torii:station_direction": direction,
                "torii:station_from_m": "0",
                "torii:station_to_m": "100",
            }
            for key, value in params.items():
                ET.SubElement(edge, "param", key=key, value=value)
    _write_xml(edges_path, edge_root)

    manifest = {
        "schema": OFFICIAL_PLAINXML_SCHEMA,
        "candidate_id": "synthetic-official-axis",
        "source": {"sha256": "a" * 64},
        "projection": {"source_crs": "EPSG:4326", "crs": "EPSG:32632"},
        "artifacts": {
            "nodes": {"sha256": _sha256(nodes_path), "path": nodes_path.name},
            "edges": {"sha256": _sha256(edges_path), "path": edges_path.name},
        },
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    map_source_path.write_text("official map placeholder", encoding="utf-8")
    lane_geometry = [[9.9910, 53.5400, 0.0], [9.9902, 53.5400, 0.0]]
    endpoint_a = lane_geometry[-1]
    endpoint_b = lane_geometry[0]
    report = {
        "schema": "torii.hamburg-map-kml-mapem-binding/v1",
        "status": "pass",
        "node_id": "test-node",
        "source": {"path": str(map_source_path), "sha256": _sha256(map_source_path)},
        "lanes": [
            {
                "node_id": "test-node",
                "lane_id": "1",
                "lane_type": "vehicle",
                "ingress_approach": "1",
                "egress_approach": "",
                "kml_direction_role": "ingress",
                "coordinates": lane_geometry,
                "endpoint_a": endpoint_a,
                "endpoint_b": endpoint_b,
            },
            {
                "node_id": "test-node",
                "lane_id": "2",
                "lane_type": "vehicle",
                "ingress_approach": "",
                "egress_approach": "2",
                "kml_direction_role": "egress",
                "coordinates": lane_geometry,
                "endpoint_a": endpoint_a,
                "endpoint_b": endpoint_b,
            },
        ],
        "connections": [
            {
                "ingress_lane_id": "1",
                "egress_lane_id": "2",
                "connection_coordinates": [endpoint_b, [9.9911, 53.5400, 0.0], endpoint_b],
            }
        ],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return {
        "nodes": nodes_path,
        "edges": edges_path,
        "manifest": manifest_path,
        "report": report_path,
    }


def _plan(paths: dict[str, Path]) -> dict[str, object]:
    return plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=[paths["report"]],
        nodes_file=paths["nodes"],
        edges_file=paths["edges"],
        plainxml_manifest_file=paths["manifest"],
    )


def test_unique_directional_axis_match_stays_at_approach_level(tmp_path: Path) -> None:
    result = _plan(_write_official_inputs(tmp_path))

    assert result["status"] == "pass"
    assert result["human_action_required"] is False
    assert result["materialization_performed"] is False
    assert result["counts"]["matched_vehicle_lane_count"] == 2
    ingress, egress = result["lanes"]
    assert ingress["binding"]["station_direction"] == "with_stationing"
    assert egress["binding"]["station_direction"] == "against_stationing"
    assert ingress["sumo_lane_index"] is None
    assert egress["individual_lane_allocation"]["decision"] == "automatic_abstention"
    assert sum(item["compatible"] for item in ingress["alternatives"]) == 1
    assert result["gates"]["individual_lane_index_allocation"] == "review_required"


def test_plan_identity_does_not_depend_on_checkout_path(tmp_path: Path) -> None:
    source = _write_official_inputs(tmp_path)
    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    copied = {
        role: Path(shutil.copy2(path, copy_dir / path.name))
        for role, path in source.items()
    }

    assert _plan(source)["plan_id"] == _plan(copied)["plan_id"]


def test_equal_geometry_official_axes_abstain_by_score_margin(tmp_path: Path) -> None:
    result = _plan(_write_official_inputs(tmp_path, duplicate_axis=True))

    assert result["status"] == "review_required"
    assert result["counts"]["abstained_vehicle_lane_count"] == 2
    assert all(item["reason"] == "ambiguous_directed_corridor_score_margin" for item in result["lanes"])
    assert all(item["binding"] is None for item in result["lanes"])
    assert all(item["decision"] == "automatic_abstention_no_materialization" for item in result["lanes"])


def test_official_approach_siblings_can_resolve_one_ambiguous_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lanes = [
        {
            "node_id": "2363",
            "lane_id": lane_id,
            "map_role": "ingress",
            "approach_id": "1",
            "status": "pass",
            "binding": {"corridor_id": "karolinen-axis"},
        }
        for lane_id in ("3", "5")
    ]
    lanes.append(
        {
            "node_id": "2363",
            "lane_id": "4",
            "map_role": "ingress",
            "approach_id": "1",
            "status": "review_required",
            "reason": "ambiguous_directed_corridor_score_margin",
            "alternatives": [
                {"corridor_id": "karolinen-axis", "compatible": True},
                {"corridor_id": "messeplatz-axis", "compatible": True},
            ],
            "binding": None,
        }
    )
    monkeypatch.setattr(
        official_lane_stitch,
        "_prepare_map_lane",
        lambda *args, **kwargs: {"status": "pass"},
    )
    monkeypatch.setattr(
        official_lane_stitch,
        "_build_lane_corridor_binding",
        lambda **kwargs: {"corridor_id": kwargs["selected"]["corridor_id"]},
    )

    official_lane_stitch._resolve_ambiguous_lanes_by_approach_coherence(
        lanes,
        raw_lanes=[{"lane_id": "4"}],
        connections=[],
        corridors={"karolinen-axis": {}},
        transformer=object(),
        thresholds=official_lane_stitch.OfficialLaneAxisStitchThresholds(),
    )

    resolved = lanes[-1]
    assert resolved["status"] == "pass"
    assert resolved["binding"]["corridor_id"] == "karolinen-axis"
    assert resolved["approach_coherence_evidence"]["supporting_lane_ids"] == ["3", "5"]


def test_one_official_sibling_is_not_enough_to_resolve_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lanes = [
        {
            "node_id": "test",
            "lane_id": "1",
            "map_role": "ingress",
            "approach_id": "1",
            "status": "pass",
            "binding": {"corridor_id": "axis-a"},
        },
        {
            "node_id": "test",
            "lane_id": "2",
            "map_role": "ingress",
            "approach_id": "1",
            "status": "review_required",
            "reason": "ambiguous_directed_corridor_score_margin",
            "alternatives": [
                {"corridor_id": "axis-a", "compatible": True},
                {"corridor_id": "axis-b", "compatible": True},
            ],
            "binding": None,
        },
    ]

    official_lane_stitch._resolve_ambiguous_lanes_by_approach_coherence(
        lanes,
        raw_lanes=[{"lane_id": "2"}],
        connections=[],
        corridors={"axis-a": {}},
        transformer=object(),
        thresholds=official_lane_stitch.OfficialLaneAxisStitchThresholds(),
    )

    assert lanes[-1]["status"] == "review_required"
    assert lanes[-1]["binding"] is None


def test_missing_official_approach_id_does_not_resolve_ambiguity() -> None:
    lanes = [
        {
            "node_id": "test",
            "lane_id": lane_id,
            "map_role": "ingress",
            "approach_id": "",
            "status": "pass",
            "binding": {"corridor_id": "axis-a"},
        }
        for lane_id in ("1", "2")
    ]
    lanes.append(
        {
            "node_id": "test",
            "lane_id": "3",
            "map_role": "ingress",
            "approach_id": "",
            "status": "review_required",
            "reason": "ambiguous_directed_corridor_score_margin",
            "alternatives": [{"corridor_id": "axis-a", "compatible": True}],
            "binding": None,
        }
    )

    official_lane_stitch._resolve_ambiguous_lanes_by_approach_coherence(
        lanes,
        raw_lanes=[{"lane_id": "3"}],
        connections=[],
        corridors={"axis-a": {}},
        transformer=object(),
        thresholds=official_lane_stitch.OfficialLaneAxisStitchThresholds(),
    )

    assert lanes[-1]["status"] == "review_required"
    assert lanes[-1]["binding"] is None


def test_plainxml_artifact_hash_mismatch_blocks_before_matching(tmp_path: Path) -> None:
    paths = _write_official_inputs(tmp_path)
    paths["nodes"].write_text(paths["nodes"].read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(OfficialLaneAxisStitchError, match="does not match manifest SHA-256"):
        _plan(paths)


def test_missing_connection_endpoint_proof_abstains_without_human_gate(tmp_path: Path) -> None:
    paths = _write_official_inputs(tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["connections"] = []

    result = plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=[report],
        nodes_file=paths["nodes"],
        edges_file=paths["edges"],
        plainxml_manifest_file=paths["manifest"],
    )

    assert result["human_action_required"] is False
    assert all(
        item["reason"] == "MAP_connections_do_not_prove_endpoint_B_is_the_junction_endpoint"
        for item in result["lanes"]
    )


def test_real_2349_2394_reports_expose_one_official_2_to_3_cut() -> None:
    signal_dir = REAL_ROOT / "official" / "signals"
    skeleton = REAL_ROOT / "road_skeleton"
    reports = [
        signal_dir / "2349_map_kml_mapem_binding.json",
        signal_dir / "2394_map_kml_mapem_binding.json",
    ]

    result = plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=list(reversed(reports)),
        nodes_file=skeleton / "sandtorkai_official_named_corridor.nod.xml",
        edges_file=skeleton / "sandtorkai_official_named_corridor.edg.xml",
        plainxml_manifest_file=skeleton / "sandtorkai_official_named_corridor.manifest.json",
    )
    repeated = plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=reports,
        nodes_file=skeleton / "sandtorkai_official_named_corridor.nod.xml",
        edges_file=skeleton / "sandtorkai_official_named_corridor.edg.xml",
        plainxml_manifest_file=skeleton / "sandtorkai_official_named_corridor.manifest.json",
    )

    assert result["plan_id"] == repeated["plan_id"]
    assert result["status"] == "review_required"
    assert result["gates"]["approach_geometry_and_lane_count"] == "review_required"
    assert any(
        item["status"] == "review_required"
        and any(
            reason in item["reasons"]
            for reason in (
                "map_lane_count_differs_from_hh_sib_boundary_profile",
                "boundary_station_span_exceeds_lane_width",
                "official_map_merge_point_requires_segment_split",
            )
        )
        for item in result["approach_geometry_conflicts"]
    )
    assert result["counts"] == {
        "map_node_count": 2,
        "vehicle_lane_count": 21,
        "matched_vehicle_lane_count": 16,
        "abstained_vehicle_lane_count": 5,
        "approach_count": 12,
        "matched_approach_count": 8,
        "abstained_approach_count": 4,
        "stitch_candidate_count": 2,
        "authorized_approach_stitch_count": 1,
    }
    abstained = {
        (item["node_id"], item["lane_id"])
        for item in result["lanes"]
        if item["status"] != "pass"
    }
    assert abstained == {("2349", "6"), ("2349", "9"), ("2349", "10"), ("2349", "11"), ("2394", "7")}
    assert all(
        item["binding"]["sumo_lane_index"] is None
        for item in result["lanes"]
        if item["status"] == "pass"
    )

    authorized = next(
        item for item in result["stitch_candidates"] if item["approach_level_materialization_authorized"]
    )
    assert authorized["upstream_egress"] == {
        "node_id": "2349",
        "approach_id": "1",
        "vehicle_lane_count": 2,
    }
    assert authorized["downstream_ingress"] == {
        "node_id": "2394",
        "approach_id": "4",
        "vehicle_lane_count": 3,
    }
    assert authorized["envelope_relation"] == "overlap"
    assert authorized["overlap_interval_m"][0] < authorized["overlap_interval_m"][1]
    assert authorized["selected_cut"] == {
        "station_m": 698.0,
        "before_lane_count": 2,
        "after_lane_count": 3,
        "basis": "exact_HH_SIB_adjacent_interval_lane_count_change",
    }
    assert authorized["lane_index_materialization_authorized"] is False


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", short_empty_elements=True)
        + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
