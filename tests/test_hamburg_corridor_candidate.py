from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.hamburg_corridor_candidate import (
    bind_hamburg_corridor_tls_clusters,
    build_hamburg_corridor_candidate_evidence,
    select_hamburg_corridor,
)
from torii_sumo.core.candidate_contracts import file_sha256


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _fixtures(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Path], Path]:
    lsa = _write(
        tmp_path / "lsa.json",
        {
            "decision": "pass",
            "selections": [
                {
                    "expected_node_id": "a",
                    "selected_node": {
                        "official_name": "A",
                        "signal_type": "K-LSA",
                        "point_geometry": {"coordinates": [10.0, 53.0]},
                    },
                },
                {
                    "expected_node_id": "b",
                    "selected_node": {
                        "official_name": "B",
                        "signal_type": "K-LSA",
                        "point_geometry": {"coordinates": [10.001, 53.0]},
                    },
                },
            ],
        },
    )
    static = _write(
        tmp_path / "static.json",
        {
            "status": "pass",
            "execution_gate": "pass",
            "node_ids": ["a", "b"],
            "nodes": {"a": {}, "b": {}},
        },
    )
    counts = _write(
        tmp_path / "counts.json",
        {
            "status": "pass",
            "execution_gate": "pass",
            "parameters": {"requested_count_node_ids": ["a", "b"]},
            "gates": {
                "full_named_node_coverage": "pass",
                "official_observation_window": "pass",
            },
        },
    )
    plainxml = {
        node_id: _write(
            tmp_path / f"plainxml-{node_id}.json",
            {"status": "pass", "compiled_network_audit": {"status": "pass"}},
        )
        for node_id in ("a", "b")
    }
    roads = _write(
        tmp_path / "roads.json",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "r1",
                    "properties": {
                        "von_netzknoten": "n1",
                        "nach_netzknoten": "n2",
                        "strassenname": "Example",
                        "abschnittslaenge": 100,
                    },
                }
            ],
        },
    )
    return lsa, static, counts, plainxml, roads


def test_candidate_keeps_axis_anchor_as_review_gate(tmp_path: Path) -> None:
    lsa, static, counts, plainxml, roads = _fixtures(tmp_path)
    report = build_hamburg_corridor_candidate_evidence(
        candidate_id="candidate",
        ordered_node_ids=("a", "b"),
        lsa_identity_manifest=lsa,
        static_signal_manifest=static,
        count_manifest=counts,
        plainxml_manifests=plainxml,
        official_road_snapshot=roads,
        axis_paths=(
            {
                "from_node_id": "a",
                "to_node_id": "b",
                "start_network_node": "n1",
                "end_network_node": "n2",
                "links": [{"feature_id": "r1", "direction": "forward"}],
            },
        ),
    )
    assert report["status"] == "review_required"
    assert report["gates"]["official_axis_link_chain"] == "pass"
    assert report["gates"]["official_axis_anchor_binding"] == "review_required"
    assert report["distances"][0]["distance_m"] > 0


def test_candidate_blocks_broken_axis_chain(tmp_path: Path) -> None:
    lsa, static, counts, plainxml, roads = _fixtures(tmp_path)
    report = build_hamburg_corridor_candidate_evidence(
        candidate_id="candidate",
        ordered_node_ids=("a", "b"),
        lsa_identity_manifest=lsa,
        static_signal_manifest=static,
        count_manifest=counts,
        plainxml_manifests=plainxml,
        official_road_snapshot=roads,
        axis_paths=(
            {
                "from_node_id": "a",
                "to_node_id": "b",
                "start_network_node": "wrong",
                "end_network_node": "n2",
                "links": [{"feature_id": "r1", "direction": "forward"}],
            },
        ),
    )
    assert report["status"] == "blocked"
    assert report["gates"]["official_axis_link_chain"] == "blocked"


def test_candidate_blocks_node_type_that_is_not_an_intersection(tmp_path: Path) -> None:
    lsa, static, counts, plainxml, roads = _fixtures(tmp_path)
    payload = json.loads(lsa.read_text(encoding="utf-8"))
    payload["selections"][0]["selected_node"]["signal_type"] = "F-LSA"
    lsa.write_text(json.dumps(payload), encoding="utf-8")

    report = build_hamburg_corridor_candidate_evidence(
        candidate_id="three-intersection-candidate",
        ordered_node_ids=("a", "b"),
        lsa_identity_manifest=lsa,
        static_signal_manifest=static,
        count_manifest=counts,
        plainxml_manifests=plainxml,
        official_road_snapshot=roads,
        axis_paths=(
            {
                "from_node_id": "a",
                "to_node_id": "b",
                "start_network_node": "n1",
                "end_network_node": "n2",
                "links": [{"feature_id": "r1", "direction": "forward"}],
            },
        ),
        required_signal_types=("K-LSA",),
    )

    assert report["status"] == "blocked"
    assert report["gates"]["corridor_node_signal_type"] == "blocked"
    assert report["node_type_policy"] == {
        "required_signal_types": ["K-LSA"],
        "observed_signal_types_by_node": {"a": "F-LSA", "b": "K-LSA"},
        "mismatched_signal_types_by_node": {"a": "F-LSA"},
    }


def test_candidate_records_lane_axis_stitch_gate_without_promoting_network(tmp_path: Path) -> None:
    lsa, static, counts, plainxml, roads = _fixtures(tmp_path)
    stitch = _write(
        tmp_path / "lane-stitch.json",
        {
            "schema": "torii.hamburg-official-map-hh-sib-lane-axis-stitch-plan/v1",
            "status": "review_required",
            "decision": "automatic_abstention_no_materialization_for_unmatched_lanes",
        },
    )
    report = build_hamburg_corridor_candidate_evidence(
        candidate_id="candidate",
        ordered_node_ids=("a", "b"),
        lsa_identity_manifest=lsa,
        static_signal_manifest=static,
        count_manifest=counts,
        plainxml_manifests=plainxml,
        official_road_snapshot=roads,
        axis_paths=(
            {
                "from_node_id": "a",
                "to_node_id": "b",
                "start_network_node": "n1",
                "end_network_node": "n2",
                "links": [{"feature_id": "r1", "direction": "forward"}],
            },
        ),
        map_lane_axis_stitch_plan=stitch,
    )
    assert report["gates"]["official_map_hh_sib_lane_axis_stitch"] == "review_required"
    assert report["automatic_promotion_gate"] == "blocked"


@pytest.mark.parametrize(
    ("common_window_status", "expected_status", "expected_mode", "expected_candidate"),
    [
        ("pass", "pass", "strict", "higher_detector_coverage"),
        ("blocked", "diagnostic-only", "registered_fallback", "ring1_east"),
    ],
)
def test_five_corridor_selection_keeps_strict_and_registered_fallback_separate(
    tmp_path: Path,
    common_window_status: str,
    expected_status: str,
    expected_mode: str,
    expected_candidate: str,
) -> None:
    source = _write(tmp_path / "source.json", {"frozen": True})
    base_gates = {
        "exactly_five_distinct_k_lsa": "pass",
        "official_lsa_identity": "pass",
        "official_road_simple_path": "pass",
        "no_skipped_intervening_k_lsa": "pass",
        "exact_static_asset_triplets": "pass",
        "motor_vehicle_primary_tld_metadata": "pass",
        "motor_vehicle_detector_metadata": "pass",
        "complete_detector_window": "pass",
        "common_detector_tld_window": common_window_status,
        "publication_license": "pass",
    }
    if common_window_status == "blocked":
        base_gates["exact_static_asset_triplets"] = "review_required"
        base_gates["motor_vehicle_primary_tld_metadata"] = "blocked"
    candidates = [
        {
            "candidate_id": "ring1_east",
            "ordered_node_ids": ["104", "118", "119", "200", "535"],
            "gates": base_gates,
            "ranking": {
                "exact_static_asset_intersection_count": 5,
                "detector_covered_motor_vehicle_approach_count": 10,
                "complete_common_observation_day_count": 1 if common_window_status == "pass" else 0,
                "ambiguity_count": 0,
                "max_adjacent_gap_m": 595.1,
            },
            "selected_detector_window": {
                "simulation_begin_utc": "2026-07-11T15:10:00Z",
                "formal_begin_utc": "2026-07-11T15:40:00Z",
                "formal_end_utc": "2026-07-11T17:40:00Z",
            },
        },
        {
            "candidate_id": "higher_detector_coverage",
            "ordered_node_ids": ["1", "2", "3", "4", "5"],
            "gates": base_gates,
            "ranking": {
                "exact_static_asset_intersection_count": 5,
                "detector_covered_motor_vehicle_approach_count": 12,
                "complete_common_observation_day_count": 1 if common_window_status == "pass" else 0,
                "ambiguity_count": 0,
                "max_adjacent_gap_m": 500.0,
            },
        },
    ]
    ledger = _write(
        tmp_path / "screening.json",
        {
            "schema": "torii.hamburg-five-corridor-screening/v1",
            "protocol": "hamburg-five-intersection-v1+A1+A2",
            "sources": [
                {
                    "role": "lsa_nodes",
                    "url": "https://api.hamburg.de/example",
                    "query": {},
                    "retrieved_at_utc": "2026-08-27T18:00:00Z",
                    "path": "source.json",
                    "sha256": file_sha256(source),
                    "license": "DL-DE-BY-2.0",
                }
            ],
            "candidates": candidates,
        },
    )

    result = select_hamburg_corridor(
        screening_ledger_file=ledger,
        expected_screening_ledger_sha256=file_sha256(ledger),
        output_file=tmp_path / "selection.json",
    )

    assert result["status"] == expected_status
    assert result["selection_mode"] == expected_mode
    if expected_mode == "strict":
        assert result["strict_selection"]["selected_candidate_id"] == expected_candidate
    else:
        assert result["strict_selection"]["status"] == "blocked"
        assert result["fallback_selection"]["selected_candidate_id"] == expected_candidate
        assert result["fallback_selection"]["ordered_node_ids"] == ["104", "118", "119", "200", "535"]
        assert result["fallback_selection"]["allowed_approximations"] == [
            "exact_static_asset_triplets",
            "motor_vehicle_primary_tld_metadata",
            "common_detector_tld_window",
        ]


@pytest.mark.parametrize("topology_only, compound_centroid", [(False, False), (True, False), (True, True)])
def test_bind_selected_official_nodes_to_unique_tls_clusters(tmp_path: Path, topology_only: bool, compound_centroid: bool) -> None:
    node_ids = ["1", "2"] if topology_only else ["1", "2", "3", "4", "5"]
    selection = _write(
        tmp_path / "selection.json",
        {
            "schema": "torii.hamburg-five-corridor-selection/v1",
            "status": "diagnostic-only",
            "selection_mode": "topology_only" if topology_only else "registered_fallback",
            **({"ordered_node_ids": node_ids} if topology_only else {"fallback_selection": {"ordered_node_ids": node_ids}}),
        },
    )
    lsa = _write(
        tmp_path / "lsa.geojson",
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "MultiPoint", "coordinates": [[10.0 + index / 1000, 53.0]]},
                    "properties": {"knoten": index + 1, "art": "K-LSA", "LSA_Name": f"Node {index + 1}"},
                }
                for index in range(5)
            ],
        },
    )
    clusters = tmp_path / "clusters.csv"
    clusters.write_text(
        "cluster_id,lat,lon,tls_count,tls_ids\n"
        + "\n".join(
            f"G{index + 1:03d},53.0,{10.0 + index / 1000 + (0.002 if compound_centroid else 0)},1,tls-{index + 1}"
            for index in range(5)
        )
        + "\n",
        encoding="utf-8",
    )
    network = tmp_path / "network.net.xml"
    network.write_text(
        "<net>" + "".join(f'<tlLogic id="tls-{index}"/>' for index in range(1, 6)) + "</net>",
        encoding="utf-8",
    )
    if compound_centroid:
        network.write_text(
            '<net><location netOffset="0,0" projParameter="+proj=longlat +datum=WGS84"/>'
            + ''.join(f'<junction id="j-{i}" x="{10 + (i-1)/1000}" y="53"/><tlLogic id="tls-{i}"/><edge id="in-{i}" to="j-{i}"/><connection from="in-{i}" tl="tls-{i}"/>' for i in range(1, 6))
            + '</net>', encoding="utf-8",
        )

    result = bind_hamburg_corridor_tls_clusters(
        selection_file=selection,
        expected_selection_sha256=file_sha256(selection),
        lsa_identity_file=lsa,
        expected_lsa_identity_sha256=file_sha256(lsa),
        tls_clusters_file=clusters,
        expected_tls_clusters_sha256=file_sha256(clusters),
        net_file=network,
        expected_net_sha256=file_sha256(network),
        output_file=tmp_path / "bindings.json",
    )

    assert result["status"] == "pass"
    assert [row["cluster_id"] for row in result["bindings"]] == [f"G{int(node):03d}" for node in node_ids]
    if topology_only:
        assert result["selection_mode"] == "topology_only"
        assert result["gates"]["selected_cluster_coverage"] == "pass"
    if compound_centroid:
        assert all(row["distance_basis"] == "nearest_controlled_junction" for row in result["bindings"])
        assert all(row["distance_m"] < 0.01 for row in result["bindings"])
