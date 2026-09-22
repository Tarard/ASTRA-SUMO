from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_count_station_binding import (
    HamburgCountStationBindingError,
    audit_hamburg_station_group_e1_output,
    materialize_hamburg_count_station_bindings,
)


UTC = timezone.utc


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": file_sha256(path)}


def _observations(stream_id: int, counts: list[int], *, seconds: int) -> list[dict[str, object]]:
    begin = datetime(2026, 7, 18, tzinfo=UTC)
    return [
        {
            "stream_id": stream_id,
            "begin_utc": (begin + timedelta(seconds=index * seconds)).isoformat(),
            "end_utc": (begin + timedelta(seconds=(index + 1) * seconds)).isoformat(),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def _inputs(tmp_path: Path) -> dict[str, Path]:
    net_path = tmp_path / "corridor.net.xml"
    net_path.write_text(
        """<net>
    <edge id="edge-a" from="a" to="b">
        <lane id="edge-a_0" index="0" speed="13.9" length="100" allow="passenger"/>
        <lane id="edge-a_1" index="1" speed="13.9" length="100" allow="passenger"/>
    </edge>
    <edge id="edge-b" from="b" to="c">
        <lane id="edge-b_0" index="0" speed="13.9" length="80" allow="passenger"/>
    </edge>
</net>""",
        encoding="utf-8",
    )
    raw_fields = tmp_path / "field-streams.raw.json"
    _write_json(raw_fields, {"source": "frozen"})
    field_rows = tmp_path / "field-streams.normalized.json"
    _write_json(
        field_rows,
        [
            {"stream_id": 101, "node_id": "1", "asset_id": "Z.1", "direction": "Richtung 1"},
            {"stream_id": 102, "node_id": "1", "asset_id": "Z.2", "direction": "Richtung 1"},
            {"stream_id": 201, "node_id": "2", "asset_id": "Z.1", "direction": "Richtung 2"},
        ],
    )
    field_observations = tmp_path / "field-observations.json"
    _write_json(
        field_observations,
        {
            "101": _observations(101, [1, 2, 3, 2, 2, 2], seconds=300),
            "102": _observations(102, [4, 5, 6, 1, 1, 1], seconds=300),
            "201": _observations(201, [7, 8, 9, 3, 4, 5], seconds=300),
        },
    )
    field_scope = tmp_path / "field-scope.manifest.json"
    _write_json(
        field_scope,
        {
            "schema": "torii.hamburg-named-corridor-count-scope/v1",
            "execution_gate": "pass",
            "selected_window": {"formal_window": {"source_bin_seconds": 300}},
            "artifacts": {
                "count_streams_raw": _artifact(raw_fields),
                "count_streams_normalized": _artifact(field_rows),
                "count_observations_normalized": _artifact(field_observations),
            },
        },
    )

    mapping = tmp_path / "detector-mapping.csv"
    with mapping.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "detector_id",
                "stream_id",
                "node_id",
                "asset_id",
                "sumo_edge",
                "sumo_lane",
                "lane_position",
                "mapping_confidence",
                "mapping_status",
                "mapping_reason",
                "official_map_lane",
            ],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "detector_id": "field-101",
                    "stream_id": 101,
                    "node_id": 1,
                    "asset_id": "Z.1",
                    "sumo_edge": "edge-a",
                    "sumo_lane": "edge-a_0",
                    "lane_position": 50,
                    "mapping_confidence": "high",
                    "mapping_status": "active",
                    "mapping_reason": "official MAP ingress-lane identity",
                    "official_map_lane": 1,
                },
                {
                    "detector_id": "field-102",
                    "stream_id": 102,
                    "node_id": 1,
                    "asset_id": "Z.2",
                    "sumo_edge": "edge-a",
                    "sumo_lane": "edge-a_1",
                    "lane_position": 50,
                    "mapping_confidence": "high",
                    "mapping_status": "active",
                    "mapping_reason": "official MAP ingress-lane identity",
                    "official_map_lane": 2,
                },
                {
                    "detector_id": "field-201",
                    "stream_id": 201,
                    "node_id": 2,
                    "asset_id": "Z.1",
                    "sumo_edge": "edge-b",
                    "sumo_lane": "edge-b_0",
                    "lane_position": 20,
                    "mapping_confidence": "high",
                    "mapping_status": "active",
                    "mapping_reason": "strict geometry-only binding",
                    "official_map_lane": "",
                },
            ]
        )
    candidates = tmp_path / "detector-candidates.json"
    _write_json(
        candidates,
        {
            "rows": [
                {
                    "stream_id": 101,
                    "node_id": "1",
                    "asset_id": "Z.1",
                    "candidate_status": "active",
                    "selected_lane": "edge-a_0",
                    "official_map_lane": "1",
                    "official_detector_to_map_distance_m": 0.2,
                    "official_map_to_sumo_distance_m": 0.3,
                    "official_map_to_sumo_heading_error_deg": 1.0,
                },
                {
                    "stream_id": 102,
                    "node_id": "1",
                    "asset_id": "Z.2",
                    "candidate_status": "active",
                    "selected_lane": "edge-a_1",
                    "official_map_lane": "2",
                    "official_detector_to_map_distance_m": 0.2,
                    "official_map_to_sumo_distance_m": 0.3,
                    "official_map_to_sumo_heading_error_deg": 1.0,
                },
                {
                    "stream_id": 201,
                    "node_id": "2",
                    "asset_id": "Z.1",
                    "candidate_status": "active",
                    "selected_lane": "edge-b_0",
                    "selected_distance_m": 0.2,
                    "separation_m": 2.0,
                },
            ]
        },
    )
    movement_evidence = tmp_path / "movement-evidence.json"
    official_map = tmp_path / "official-map.xml"
    _write_json(movement_evidence, {"source": "official evidence"})
    official_map.write_text(
        """<MAPEM><IntersectionGeometry><id><id>1</id></id><refPoint><lat>0</lat><long>0</long></refPoint>
<laneSet>
<GenericLane><laneID>1</laneID><laneAttributes><laneType><vehicle/></laneType></laneAttributes></GenericLane>
<GenericLane><laneID>2</laneID><laneAttributes><laneType><vehicle/></laneType></laneAttributes></GenericLane>
</laneSet></IntersectionGeometry></MAPEM>""",
        encoding="utf-8",
    )
    field_binding = tmp_path / "field-binding.manifest.json"
    _write_json(
        field_binding,
        {
            "schema": "torii.hamburg-named-detector-binding/v1",
            "execution_gate": "pass",
            "source": {
                "candidate_net": _artifact(net_path),
                "count_stream_snapshot": _artifact(raw_fields),
                "movement_lane_evidence": _artifact(movement_evidence),
                "official_map_files": [_artifact(official_map)],
            },
            "gates": {"unique_lane_binding": "pass"},
            "artifacts": {
                "detector_mapping": _artifact(mapping),
                "detector_lane_candidates": _artifact(candidates),
            },
        },
    )

    station_rows = tmp_path / "station-streams.normalized.json"
    _write_json(
        station_rows,
        [
            {
                "stream_id": 11,
                "asset_id": "station-11",
                "node_id": "1",
                "direction": "west to east",
                "direction_code": "1",
                "station_arm": "7",
                "composition": ["0001-Z.1", "0001-Z.2"],
            },
            {
                "stream_id": 12,
                "asset_id": "station-12",
                "node_id": "2",
                "direction": "north to south",
                "direction_code": "2",
                "station_arm": "3",
                "composition": ["0002-Z.1"],
            },
            {
                "stream_id": 14,
                "asset_id": "station-14",
                "node_id": "1",
                "direction": "south to north",
                "direction_code": "2",
                "station_arm": "7",
                "composition": ["0001-Z.1"],
            },
            {
                "stream_id": 13,
                "asset_id": "station-total",
                "node_id": "1",
                "direction": "Keine Richtung",
                "direction_code": "0",
                "station_arm": "7",
                "composition": ["0001-Z.1", "0001-Z.2"],
            },
            {
                "stream_id": 15,
                "asset_id": "station-total-2",
                "node_id": "2",
                "direction": "Keine Richtung",
                "direction_code": "0",
                "station_arm": "3",
                "composition": ["0002-Z.1"],
            },
        ],
    )
    station_raw = tmp_path / "station-streams.raw.json"
    _write_json(station_raw, {"source": "station inventory"})
    station_inventory = tmp_path / "station-inventory.manifest.json"
    _write_json(
        station_inventory,
        {
            "schema": "torii.hamburg-count-station-inventory/v1",
            "execution_gate": "pass",
            "directional_station_stream_ids": [11, 12, 14],
            "total_validation_stream_ids": [13, 15],
            "missing_node_ids": ["2349"],
            "artifacts": {
                "count_station_streams_raw": _artifact(station_raw),
                "count_station_streams_normalized": _artifact(station_rows),
            },
        },
    )
    station_observations = tmp_path / "station-observations.json"
    _write_json(
        station_observations,
        {
            "11": _observations(11, [21, 9], seconds=900),
            "12": _observations(12, [24, 12], seconds=900),
            "14": _observations(14, [6, 6], seconds=900),
            "13": _observations(13, [27, 15], seconds=900),
            "15": _observations(15, [24, 12], seconds=900),
        },
    )
    direction_zero_qa = tmp_path / "direction-zero-qa.json"
    _write_json(
        direction_zero_qa,
        {
            "schema": "torii.hamburg-station-direction-zero-qa/v1",
            "status": "pass",
            "groups": [
                {
                    "total_stream_id": 13,
                    "directional_stream_ids": [11, 14],
                    "different_bin_count": 0,
                },
                {
                    "total_stream_id": 15,
                    "directional_stream_ids": [12],
                    "different_bin_count": 0,
                },
            ],
        },
    )
    station_window = tmp_path / "station-window.manifest.json"
    _write_json(
        station_window,
        {
            "schema": "torii.hamburg-station-observation-window/v1",
            "execution_gate": "pass",
            "directional_station_stream_ids": [11, 12, 14],
            "missing_station_node_ids": ["2349"],
            "selected_window": {
                "simulation_begin_utc": "2026-07-18T00:00:00+00:00",
                "simulation_end_utc": "2026-07-18T00:30:00+00:00",
                "output_bin_seconds": 900,
                "formal_window": {"source_bin_seconds": 900},
            },
            "artifacts": {
                "observations_normalized": _artifact(station_observations),
                "direction_zero_qa": _artifact(direction_zero_qa),
                "station_inventory": _artifact(station_raw),
            },
        },
    )
    return {
        "net": net_path,
        "field_binding": field_binding,
        "field_scope": field_scope,
        "station_inventory": station_inventory,
        "station_window": station_window,
        "station_observations": station_observations,
        "station_raw": station_raw,
        "direction_zero_qa": direction_zero_qa,
        "mapping": mapping,
        "candidates": candidates,
    }


def _run(inputs: dict[str, Path], output: Path) -> dict[str, object]:
    return materialize_hamburg_count_station_bindings(
        net_file=inputs["net"],
        expected_network_sha256=file_sha256(inputs["net"]),
        station_inventory_manifest=inputs["station_inventory"],
        field_binding_manifest=inputs["field_binding"],
        field_count_scope_manifest=inputs["field_scope"],
        station_observation_manifest=inputs["station_window"],
        output_dir=output,
    )


def _write_e1_output(path: Path, rows: list[tuple[str, int, int, int]]) -> None:
    root = ET.Element("detector")
    for detector_id, begin, end, count in rows:
        ET.SubElement(
            root,
            "interval",
            {
                "id": detector_id,
                "begin": str(begin),
                "end": str(end),
                "nVehContrib": str(count),
            },
        )
    ET.ElementTree(root).write(path, encoding="unicode")


def test_binds_composition_fields_and_reuses_one_physical_e1(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = _run(inputs, tmp_path / "output")

    assert result["status"] == "partial"
    assert result["execution_gate"] == "pass"
    assert result["station_group_count"] == 3
    assert result["unique_physical_field_detector_count"] == 3
    assert result["group_membership_count"] == 4
    assert result["composition_sum_bin_count"] == 6
    assert result["composition_sum_differing_bin_count"] == 0
    assert result["edge_constraint_eligible_station_stream_ids"] == [11, 12]
    assert result["validation_only_station_stream_ids"] == [14]

    loops = ET.parse(tmp_path / "output" / "station-field-e1.add.xml").getroot().findall("inductionLoop")
    assert {loop.attrib["id"] for loop in loops} == {"field-101", "field-102", "field-201"}
    with (tmp_path / "output" / "station-group-membership.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        membership = list(csv.DictReader(handle))
    assert sum(row["field_detector_id"] == "field-101" for row in membership) == 2
    assert "13" not in {row["station_stream_id"] for row in membership}
    with (tmp_path / "output" / "physical-field-identity-index.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        identities = list(csv.DictReader(handle))
    assert len(identities) == 3
    assert next(row for row in identities if row["official_datastream_id"] == "101")[
        "station_stream_ids"
    ] == "11;14"
    crosswalk = json.loads((tmp_path / "output" / "sensor-type-crosswalk.json").read_text())
    assert {row["physical_or_logical"] for row in crosswalk["types"]} >= {
        "physical_lane_count_field",
        "processed_directional_station_group",
        "signal_control_presence_or_request_detector",
    }


def test_rejects_station_total_that_is_not_the_composition_sum(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    observations = json.loads(inputs["station_observations"].read_text(encoding="utf-8"))
    observations["11"][0]["count"] = 20
    _write_json(inputs["station_observations"], observations)
    window = json.loads(inputs["station_window"].read_text(encoding="utf-8"))
    window["artifacts"]["observations_normalized"] = _artifact(inputs["station_observations"])
    _write_json(inputs["station_window"], window)

    with pytest.raises(HamburgCountStationBindingError, match="composition does not reproduce"):
        _run(inputs, tmp_path / "output")


def test_rejects_field_without_precise_identity_proof(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    rows = list(csv.DictReader(inputs["mapping"].open(encoding="utf-8", newline="")))
    rows[0]["official_map_lane"] = ""
    with inputs["mapping"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    candidates = json.loads(inputs["candidates"].read_text(encoding="utf-8"))
    candidates["rows"][0].update({"selected_distance_m": 2.0, "separation_m": 0.5})
    _write_json(inputs["candidates"], candidates)
    manifest = json.loads(inputs["field_binding"].read_text(encoding="utf-8"))
    manifest["artifacts"]["detector_mapping"] = _artifact(inputs["mapping"])
    manifest["artifacts"]["detector_lane_candidates"] = _artifact(inputs["candidates"])
    _write_json(inputs["field_binding"], manifest)

    with pytest.raises(HamburgCountStationBindingError, match="no precise"):
        _run(inputs, tmp_path / "output")


def test_rejects_station_window_from_another_inventory(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    window = json.loads(inputs["station_window"].read_text(encoding="utf-8"))
    other_inventory = tmp_path / "other-station-inventory.json"
    _write_json(other_inventory, {"source": "different"})
    window["artifacts"]["station_inventory"] = _artifact(other_inventory)
    _write_json(inputs["station_window"], window)

    with pytest.raises(HamburgCountStationBindingError, match="different station inventory"):
        _run(inputs, tmp_path / "output")


def test_rejects_cross_node_station_composition(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inventory = json.loads(inputs["station_inventory"].read_text(encoding="utf-8"))
    station_rows_path = Path(inventory["artifacts"]["count_station_streams_normalized"]["path"])
    rows = json.loads(station_rows_path.read_text(encoding="utf-8"))
    next(row for row in rows if row["stream_id"] == 11)["composition"] = ["0002-Z.1"]
    _write_json(station_rows_path, rows)
    inventory["artifacts"]["count_station_streams_normalized"] = _artifact(station_rows_path)
    _write_json(inputs["station_inventory"], inventory)

    with pytest.raises(HamburgCountStationBindingError, match="crosses official node"):
        _run(inputs, tmp_path / "output")


def test_edge_constraint_requires_one_colocated_field_per_lane(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    rows = list(csv.DictReader(inputs["mapping"].open(encoding="utf-8", newline="")))
    next(row for row in rows if row["stream_id"] == "102")["lane_position"] = "55"
    with inputs["mapping"].open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = json.loads(inputs["field_binding"].read_text(encoding="utf-8"))
    manifest["artifacts"]["detector_mapping"] = _artifact(inputs["mapping"])
    _write_json(inputs["field_binding"], manifest)

    result = _run(inputs, tmp_path / "output")

    assert result["edge_constraint_eligible_station_stream_ids"] == [12]
    assert result["validation_only_station_stream_ids"] == [11, 14]


def test_station_group_e1_audit_sums_shared_physical_members_exactly(tmp_path: Path) -> None:
    binding = _run(_inputs(tmp_path), tmp_path / "binding")
    e1_output = tmp_path / "e1-output.xml"
    _write_e1_output(
        e1_output,
        [
            ("field-101", 0, 900, 6),
            ("field-101", 900, 1800, 6),
            ("field-102", 0, 900, 15),
            ("field-102", 900, 1800, 3),
            ("field-201", 0, 900, 24),
            ("field-201", 900, 1800, 12),
        ],
    )

    result = audit_hamburg_station_group_e1_output(
        station_binding_manifest=Path(binding["manifest_path"]),
        e1_output_file=e1_output,
        output_dir=tmp_path / "audit",
    )

    assert result["station_count_equality_gate"] == "pass"
    assert result["automatic_promotion_gate"] == "blocked"
    assert result["metrics"] == {
        "expected_bin_count": 6,
        "complete_bin_count": 6,
        "missing_bin_count": 0,
        "exact_match_bin_count": 6,
        "mismatch_bin_count": 0,
        "expected_total": 78,
        "complete_expected_total": 78,
        "measured_total": 78,
        "MAE": 0.0,
        "RMSE": 0.0,
        "max_abs_error": 0,
        "GEH_lt5_percent": 100.0,
    }
    with (tmp_path / "audit" / "station-group-e1-comparison.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert [row["measured_nVehContrib"] for row in rows if row["station_stream_id"] == "11"] == [
        "21",
        "9",
    ]
    assert [row["measured_nVehContrib"] for row in rows if row["station_stream_id"] == "14"] == [
        "6",
        "6",
    ]


def test_station_group_e1_audit_propagates_missing_shared_member(tmp_path: Path) -> None:
    binding = _run(_inputs(tmp_path), tmp_path / "binding")
    e1_output = tmp_path / "e1-output.xml"
    _write_e1_output(
        e1_output,
        [
            ("field-101", 0, 900, 6),
            ("field-102", 0, 900, 15),
            ("field-102", 900, 1800, 3),
            ("field-201", 0, 900, 24),
            ("field-201", 900, 1800, 12),
        ],
    )

    result = audit_hamburg_station_group_e1_output(
        station_binding_manifest=Path(binding["manifest_path"]),
        e1_output_file=e1_output,
        output_dir=tmp_path / "audit",
    )

    assert result["station_count_equality_gate"] == "blocked"
    assert result["metrics"]["missing_bin_count"] == 2
    with (tmp_path / "audit" / "station-group-e1-comparison.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    missing = [row for row in rows if row["measurement_status"] == "missing"]
    assert {(row["station_stream_id"], row["begin"]) for row in missing} == {
        ("11", "900"),
        ("14", "900"),
    }
    assert all(row["measured_nVehContrib"] == "" for row in missing)


def test_station_group_e1_audit_blocks_mismatch(tmp_path: Path) -> None:
    binding = _run(_inputs(tmp_path), tmp_path / "binding")
    e1_output = tmp_path / "e1-output.xml"
    _write_e1_output(
        e1_output,
        [
            ("field-101", 0, 900, 6),
            ("field-101", 900, 1800, 6),
            ("field-102", 0, 900, 14),
            ("field-102", 900, 1800, 3),
            ("field-201", 0, 900, 24),
            ("field-201", 900, 1800, 12),
        ],
    )

    result = audit_hamburg_station_group_e1_output(
        station_binding_manifest=Path(binding["manifest_path"]),
        e1_output_file=e1_output,
        output_dir=tmp_path / "audit",
    )

    assert result["station_count_equality_gate"] == "blocked"
    assert result["metrics"]["mismatch_bin_count"] == 1
    assert result["metrics"]["max_abs_error"] == 1


def test_station_group_e1_audit_rejects_stale_membership_hash(tmp_path: Path) -> None:
    binding = _run(_inputs(tmp_path), tmp_path / "binding")
    membership = tmp_path / "binding" / "station-group-membership.csv"
    membership.write_text(membership.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    e1_output = tmp_path / "e1-output.xml"
    _write_e1_output(e1_output, [])

    with pytest.raises(HamburgCountStationBindingError, match="SHA-256 mismatch"):
        audit_hamburg_station_group_e1_output(
            station_binding_manifest=Path(binding["manifest_path"]),
            e1_output_file=e1_output,
            output_dir=tmp_path / "audit",
        )
