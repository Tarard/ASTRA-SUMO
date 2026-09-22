from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from torii_sumo.core.osm_network import build_osm_network
from torii_sumo.road_network.adapters.osm import read_osm_road_snapshot
from torii_sumo.road_network.adapters.sumo import read_sumo_road_snapshot


FIXTURES = Path(__file__).parent / "fixtures" / "road_network"
OSM_SOURCE_SHA256 = hashlib.sha256((FIXTURES / "r1.osm.xml").read_bytes()).hexdigest()
TARGET_TIME = datetime(2026, 7, 19, 12, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 7, 19, 12, 5, tzinfo=UTC)
VALID_FROM = datetime(2026, 7, 19, tzinfo=UTC)
VALID_TO = datetime(2026, 7, 20, tzinfo=UTC)


def test_osm_adapter_preserves_all_highway_ways_and_source_dimensions() -> None:
    source = FIXTURES / "r1.osm.xml"

    report = read_osm_road_snapshot(
        source,
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "pass"
    assert report["source_snapshot"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["counts"] == {
        "node_count": 10,
        "way_count": 4,
        "highway_way_count": 4,
        "motor_vehicle_way_count": 3,
        "pedestrian_way_count": 1,
        "bicycle_way_count": 0,
        "unknown_mode_way_count": 0,
    }
    by_id = {item["source_ref"]["object_id"]: item for item in report["way_assertions"]}
    assert by_id["100"]["directionality"] == "one_way"
    assert by_id["100"]["directionality_status"] == "observed_tag"
    assert by_id["100"]["tags"]["highway"] == "secondary"
    assert by_id["100"]["geometry_lonlat"] == [
        [9.98, 53.54],
        [9.981, 53.54],
        [9.982, 53.54],
    ]
    assert by_id["200"]["derived_mode_roles"] == ["pedestrian"]
    assert "hamburg_membership" not in by_id["100"]
    assert report["automatic_promotion_gate"] == "blocked"


def test_osm_adapter_hash_mismatch_blocks_without_assertions() -> None:
    report = read_osm_road_snapshot(
        FIXTURES / "r1.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        expected_sha256="0" * 64,
    )

    assert report["status"] == "blocked"
    assert report["way_assertions"] == []
    assert report["blocking_reasons"] == ["source_sha256_mismatch"]


def test_osm_adapter_keeps_access_restricted_ways_without_assigning_blocked_modes() -> None:
    report = read_osm_road_snapshot(
        FIXTURES / "r1_access.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["counts"]["highway_way_count"] == 3
    assert report["counts"]["pedestrian_way_count"] == 1
    assert report["counts"]["bicycle_way_count"] == 0
    assert report["counts"]["unknown_mode_way_count"] == 2
    by_id = {item["way_id"]: item for item in report["way_assertions"]}
    assert by_id["pedestrian-area"]["geometry_role"] == "area_boundary"


def test_osm_directionality_prefers_explicit_oneway_and_retains_implicit_rule_basis() -> None:
    report = read_osm_road_snapshot(
        FIXTURES / "r1_direction.osm.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )
    by_id = {item["way_id"]: item for item in report["way_assertions"]}

    assert by_id["roundabout-default"]["directionality"] == "one_way"
    assert by_id["roundabout-default"]["directionality_status"] == "rule_derived"
    assert by_id["roundabout-explicit-no"]["directionality"] == "bidirectional"
    assert by_id["roundabout-explicit-no"]["directionality_status"] == "observed_tag"
    assert by_id["motorway-default"]["directionality"] == "one_way"
    assert by_id["motorway-default"]["directionality_basis"] == "motorway class implicit oneway"
    assert by_id["motorway-explicit-no"]["directionality"] == "bidirectional"


def test_sumo_adapter_excludes_internal_and_support_edges_from_road_identity() -> None:
    source = FIXTURES / "r1.net.xml"

    report = read_sumo_road_snapshot(
        source,
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        imported_from="osm",
        imported_source_sha256=OSM_SOURCE_SHA256,
    )

    assert report["status"] == "review_required"
    assert report["source_snapshot"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["counts"] == {
        "raw_edge_count": 7,
        "road_edge_assertion_count": 5,
        "internal_edge_excluded_count": 1,
        "support_edge_excluded_count": 1,
        "observed_osm_lineage_edge_count": 3,
        "rule_derived_osm_lineage_edge_count": 1,
        "unresolved_osm_lineage_edge_count": 1,
    }
    assert set(report["osm_source_index"]["100"]["edge_ids"]) == {"-100#0", "100#0", "100#1"}
    assert report["osm_source_index"]["100"]["lineage_status"] == "observed"
    assert report["osm_source_index"]["101"]["lineage_status"] == "rule_derived"
    assert {item["edge_id"] for item in report["excluded_edges"]} == {":j0_0", ":j0_walk"}
    assert (
        next(item for item in report["excluded_edges"] if item["edge_id"] == ":j0_walk")["exclusion_kind"] == "support"
    )
    assert not any(item["source_ref"]["object_id"].startswith(":") for item in report["edge_assertions"])
    assert report["automatic_promotion_gate"] == "blocked"
    assert (
        next(item for item in report["edge_assertions"] if item["edge_id"] == "-100#0")["relative_direction_status"]
        == "rule_derived"
    )


def test_sumo_adapter_converts_local_shapes_back_to_lonlat() -> None:
    report = read_sumo_road_snapshot(
        FIXTURES / "r1.net.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        imported_from="osm",
        imported_source_sha256=OSM_SOURCE_SHA256,
    )
    edge = next(item for item in report["edge_assertions"] if item["edge_id"] == "100#0")

    assert report["projection_status"] == "pass"
    assert edge["geometry_lonlat"][0] == [9.98, 53.54]
    assert edge["geometry_lonlat"][-1] == [9.981, 53.54]


def test_sumo_adapter_does_not_infer_osm_lineage_without_declared_import_provenance() -> None:
    report = read_sumo_road_snapshot(
        FIXTURES / "r1.net.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "review_required"
    assert report["imported_from"] == "unknown"
    assert report["osm_source_index"] == {}
    assert report["counts"]["unresolved_osm_lineage_edge_count"] == 5
    assert "sumo_osm_lineage_unresolved" in report["review_reasons"]


@pytest.mark.skipif(shutil.which("netconvert") is None, reason="SUMO netconvert is required")
def test_osm_network_builder_preserves_observed_orig_id_lineage_for_sumo_adapter(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.osm.xml"
    source.write_text(
        """<osm version="0.6">
  <node id="1" lat="53.540000" lon="9.980000"/>
  <node id="2" lat="53.540000" lon="9.981000"/>
  <way id="4242">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Observed OSM lineage test"/>
  </way>
</osm>""",
        encoding="utf-8",
    )

    build = build_osm_network(
        bbox="9.9700,53.5300,9.9900,53.5500",
        output_dir=tmp_path / "build",
        prefix="observed_orig_id",
        source_osm_path=source,
        allowed_highways={"primary"},
    )

    assert build["status"] == "pass"
    assert "--output.original-names" in build["netconvert"]["command"]
    assert build["netconvert_output_original_names"]["status"] == "pass"
    filtered_source = Path(build["filtered_osm_file"])
    filtered_source_sha256 = hashlib.sha256(filtered_source.read_bytes()).hexdigest()
    report = read_sumo_road_snapshot(
        build["net_file"],
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
        imported_from="osm",
        imported_source_sha256=filtered_source_sha256,
    )

    assert report["counts"]["observed_osm_lineage_edge_count"] > 0
    assert report["osm_source_index"]["4242"]["lineage_status"] == "observed"
    assert "4242" in {
        way_id
        for edge in report["edge_assertions"]
        if edge["osm_lineage_status"] == "observed"
        for way_id in edge["osm_source_way_ids"]
    }


@pytest.mark.parametrize(
    ("reader", "path", "assertion_key", "reason"),
    (
        (read_osm_road_snapshot, Path("corrupt.osm.xml.gz"), "way_assertions", "invalid_osm_xml:EOFError"),
        (read_sumo_road_snapshot, Path("corrupt.net.xml.gz"), "edge_assertions", "invalid_sumo_xml:EOFError"),
    ),
)
def test_source_adapters_block_truncated_gzip(
    reader: object,
    path: Path,
    assertion_key: str,
    reason: str,
) -> None:
    with patch.object(Path, "read_bytes", return_value=b"\x1f\x8b"):
        report = reader(
            path,
            target_time=TARGET_TIME,
            retrieved_at=RETRIEVED_AT,
            valid_from=VALID_FROM,
            valid_to=VALID_TO,
        )

    assert report["status"] == "blocked"
    assert report[assertion_key] == []
    assert report["blocking_reasons"] == [reason]


def test_sumo_adapter_blocks_non_finite_shape_coordinates() -> None:
    report = read_sumo_road_snapshot(
        FIXTURES / "r1_nonfinite.net.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "blocked"
    assert report["edge_assertions"] == []
    assert report["blocking_reasons"] == ["invalid_sumo_xml:ValueError"]


def test_sumo_adapter_fails_closed_on_unknown_edge_function() -> None:
    report = read_sumo_road_snapshot(
        FIXTURES / "r1_unknown_function.net.xml",
        target_time=TARGET_TIME,
        retrieved_at=RETRIEVED_AT,
        valid_from=VALID_FROM,
        valid_to=VALID_TO,
    )

    assert report["status"] == "review_required"
    assert report["edge_assertions"] == []
    assert report["excluded_edges"][0]["edge_role"] == "unknown_function"
    assert report["review_reasons"] == ["sumo_edge_function_unknown:unknown:future_function"]
