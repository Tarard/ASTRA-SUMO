"""Resolve Hamburg processed count stations to physical SUMO field sensors.

The official ``Zählstelle`` location is the midpoint of a counting route, not
necessarily a physical detector cross-section.  Its authoritative
``zusammensetzung`` field names the raw ``Zählfeld`` streams that form the
processed total.  Binding therefore joins identities first and uses geometry
only inside the already-audited field-to-lane artifact.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .detector_demand import (
    Detector,
    e1_counts_by_detector_interval_strict,
    geh_value,
    lane_allows_passenger,
    read_net_lanes,
    safe_id,
    write_e1_additional,
)
from .digital_twin import parse_iso_datetime, parse_mapem
from .digital_twin_mapping import write_virtual_e2_additional


COUNT_STATION_BINDING_SCHEMA = "torii.hamburg-count-station-identity-binding/v1"
STATION_GROUP_E1_AUDIT_SCHEMA = "torii.hamburg-station-group-e1-output-audit/v1"
COUNT_STATION_INVENTORY_SCHEMA = "torii.hamburg-count-station-inventory/v1"
FIELD_BINDING_SCHEMA = "torii.hamburg-named-detector-binding/v1"
FIELD_SCOPE_SCHEMA = "torii.hamburg-named-corridor-count-scope/v1"
STATION_OBSERVATION_SCHEMA = "torii.hamburg-station-observation-window/v1"
_FIELD_KEY = re.compile(r"^(?P<node>[0-9]+)-(?P<asset>Z\.[0-9]+(?:_[0-9]+)*)$")
_OSM_EDGE_ID = re.compile(r"^(?P<reverse>-?)(?P<way>[0-9]+)(?:#(?P<segment>[0-9]+))?$")
_SENSOR_TYPE_CROSSWALK = [
    {
        "official_entity_type": "Anzahl_Kfz_Zaehlfeld_5-Min",
        "physical_or_logical": "physical_lane_count_field",
        "observable": "vehicle_count",
        "sumo_primary_representation": "inductionLoop/E1",
        "sumo_primary_semantics": "co-located point count equivalent",
        "sumo_secondary_representation": "laneAreaDetector/E2",
        "sumo_secondary_semantics": "queue/occupancy diagnostic only; not a real-count equivalent",
    },
    {
        "official_entity_type": "Anzahl_Kfz_Zaehlstelle_15-Min direction 1/2",
        "physical_or_logical": "processed_directional_station_group",
        "observable": "sum of official composition fields",
        "sumo_primary_representation": "E1 detector group aggregation",
        "sumo_primary_semantics": "sum member E1 outputs; do not create a station-midpoint detector",
        "sumo_secondary_representation": "none",
        "sumo_secondary_semantics": "not applicable",
    },
    {
        "official_entity_type": "Anzahl_Kfz_Zaehlstelle_15-Min direction 0",
        "physical_or_logical": "processed_station_total_qa",
        "observable": "sum of direction 1/2 station groups",
        "sumo_primary_representation": "QA aggregation only",
        "sumo_primary_semantics": "never a demand constraint or physical detector",
        "sumo_secondary_representation": "none",
        "sumo_secondary_semantics": "not applicable",
    },
    {
        "official_entity_type": "TLD car_detector / vehicle_request",
        "physical_or_logical": "signal_control_presence_or_request_detector",
        "observable": "request/occupancy state, not a five-minute vehicle count",
        "sumo_primary_representation": "TLS detector/input event",
        "sumo_primary_semantics": "signal-control input; keep separate from count calibration",
        "sumo_secondary_representation": "E1/E2 only when detector geometry and observable are explicit",
        "sumo_secondary_semantics": "no automatic equivalence",
    },
    {
        "official_entity_type": "TLD primary_signal / signal_program / cycle_second",
        "physical_or_logical": "signal_state_or_controller_state",
        "observable": "traffic-light state/program timing",
        "sumo_primary_representation": "tlLogic/TraCI signal replay",
        "sumo_primary_semantics": "not a sensor",
        "sumo_secondary_representation": "none",
        "sumo_secondary_semantics": "not applicable",
    },
]


class HamburgCountStationBindingError(ValueError):
    """Raised when an official station identity chain is incomplete."""


def materialize_hamburg_count_station_bindings(
    *,
    net_file: Path,
    expected_network_sha256: str,
    station_inventory_manifest: Path,
    field_binding_manifest: Path,
    field_count_scope_manifest: Path,
    station_observation_manifest: Path,
    output_dir: Path,
    period: int = 900,
    source_bin_seconds: int = 300,
) -> dict[str, Any]:
    """Create physical E1/E2 sensors and processed-station membership groups.

    This is a non-mutating evidence join.  It does not run a feedback loop,
    move network objects, or choose a nearest lane.  Every physical field must
    already have one hash-bound lane identity backed by official MAP identity,
    a unique geometry result, or a uniquely separated official-lane-count
    constellation.
    """

    if period <= 0 or source_bin_seconds <= 0 or period % source_bin_seconds:
        raise HamburgCountStationBindingError("period must be a positive multiple of source_bin_seconds")
    expected_hash = _sha256_text(expected_network_sha256)
    net_path = Path(net_file).expanduser().resolve(strict=True)
    if file_sha256(net_path) != expected_hash:
        raise HamburgCountStationBindingError("frozen SUMO network SHA-256 mismatch")

    inventory_path = Path(station_inventory_manifest).expanduser().resolve(strict=True)
    field_binding_path = Path(field_binding_manifest).expanduser().resolve(strict=True)
    field_scope_path = Path(field_count_scope_manifest).expanduser().resolve(strict=True)
    observation_path = Path(station_observation_manifest).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgCountStationBindingError("output_dir must be empty; choose a new versioned run")

    inventory = _manifest(inventory_path, COUNT_STATION_INVENTORY_SCHEMA)
    field_binding = _manifest(field_binding_path, FIELD_BINDING_SCHEMA)
    field_scope = _manifest(field_scope_path, FIELD_SCOPE_SCHEMA)
    station_window = _manifest(observation_path, STATION_OBSERVATION_SCHEMA)
    for label, payload in (
        ("station inventory", inventory),
        ("field binding", field_binding),
        ("field-count scope", field_scope),
        ("station observation window", station_window),
    ):
        if payload.get("execution_gate") != "pass":
            raise HamburgCountStationBindingError(f"{label} execution gate is not pass")
    field_binding_source = _mapping(field_binding.get("source"), "field binding source")
    bound_network_hash = str(
        _mapping(field_binding_source.get("candidate_net"), "field binding candidate net").get(
            "sha256", ""
        )
    )
    if bound_network_hash != expected_hash:
        raise HamburgCountStationBindingError("field binding is not bound to the frozen SUMO network")
    if _mapping(field_binding.get("gates"), "field binding gates").get("unique_lane_binding") != "pass":
        raise HamburgCountStationBindingError("field binding has no unique-lane gate")

    inventory_raw_path = _artifact(inventory, inventory_path, "count_station_streams_raw")
    station_rows_path = _artifact(inventory, inventory_path, "count_station_streams_normalized")
    field_rows_path = _artifact(field_scope, field_scope_path, "count_streams_normalized")
    field_observations_path = _artifact(field_scope, field_scope_path, "count_observations_normalized")
    station_observations_path = _artifact(station_window, observation_path, "observations_normalized")
    direction_zero_qa_path = _artifact(station_window, observation_path, "direction_zero_qa")
    station_window_inventory_path = _artifact(station_window, observation_path, "station_inventory")
    mapping_path = _artifact(field_binding, field_binding_path, "detector_mapping")
    candidate_path = _artifact(field_binding, field_binding_path, "detector_lane_candidates")
    raw_field_hash = _artifact_hash(field_scope, "count_streams_raw")
    bound_raw_field_hash = str(
        _mapping(field_binding_source.get("count_stream_snapshot"), "field binding count snapshot").get(
            "sha256", ""
        )
    )
    if raw_field_hash != bound_raw_field_hash:
        raise HamburgCountStationBindingError("field binding and field-count scope use different raw inventories")

    station_rows = _json_list(station_rows_path)
    field_rows = _json_list(field_rows_path)
    field_observations = _json_object(field_observations_path)
    station_observations = _json_object(station_observations_path)
    mapping_rows = _csv_rows(mapping_path)
    candidate_rows = _json_object(candidate_path).get("rows")
    if not isinstance(candidate_rows, list):
        raise HamburgCountStationBindingError("field candidate evidence requires a rows list")

    field_by_key, field_by_stream = _field_inventory(field_rows)
    mapping_by_stream = _field_mappings(mapping_rows, field_by_stream)
    candidate_by_stream = _candidate_evidence(candidate_rows)
    station_by_stream = _station_inventory(station_rows)
    directional_ids = _integer_set(inventory.get("directional_station_stream_ids"), "directional streams")
    total_ids = _integer_set(inventory.get("total_validation_stream_ids"), "direction-zero streams")
    if not directional_ids or directional_ids & total_ids:
        raise HamburgCountStationBindingError("station directional/QA identities are empty or overlap")
    if set(station_by_stream) != directional_ids | total_ids:
        raise HamburgCountStationBindingError("station inventory row identities do not match its manifest")
    if any(str(station_by_stream[value].get("direction_code")) not in {"1", "2"} for value in directional_ids):
        raise HamburgCountStationBindingError("directional station stream has a non-directional code")
    if any(str(station_by_stream[value].get("direction_code")) != "0" for value in total_ids):
        raise HamburgCountStationBindingError("direction-zero QA stream has a directional code")
    missing_nodes = sorted(str(node) for node in inventory.get("missing_node_ids", []))
    _validate_station_window_contract(
        station_window,
        inventory_raw_sha256=file_sha256(inventory_raw_path),
        station_window_inventory_sha256=file_sha256(station_window_inventory_path),
        directional_ids=directional_ids,
        total_ids=total_ids,
        missing_nodes=missing_nodes,
        period=period,
    )
    _validate_direction_zero_qa(direction_zero_qa_path, total_ids=total_ids, directional_ids=directional_ids)
    _validate_field_scope_cadence(field_scope, source_bin_seconds=source_bin_seconds)
    trusted_source_hashes, official_map_lane_ids = _validated_identity_sources(
        field_binding_source,
        field_binding_path,
    )

    begin, end = _station_window(station_window)
    if int((end - begin).total_seconds()) % period:
        raise HamburgCountStationBindingError("station observation window is not divisible by period")
    audit_rows: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    membership: list[dict[str, Any]] = []
    detector_by_field_stream: dict[int, Detector] = {}
    identity_by_field_stream: dict[int, dict[str, Any]] = {}
    passenger_lanes_by_edge = _passenger_lanes_by_edge(net_path)
    network_lanes = read_net_lanes(net_path)

    for station_id in sorted(directional_ids):
        station = station_by_stream[station_id]
        members: list[dict[str, Any]] = []
        composition_seen: set[tuple[str, str]] = set()
        for composition_key in station["composition"]:
            field_key = _parse_field_key(composition_key)
            if field_key in composition_seen:
                raise HamburgCountStationBindingError(
                    f"station stream {station_id} repeats composition member {composition_key!r}"
                )
            composition_seen.add(field_key)
            if field_key[0] != _node(str(station["node_id"])):
                raise HamburgCountStationBindingError(
                    f"station stream {station_id} composition crosses official node identities"
                )
            field = field_by_key.get(field_key)
            if field is None:
                raise HamburgCountStationBindingError(
                    f"station stream {station_id} composition member {composition_key!r} is missing"
                )
            field_stream_id = int(field["stream_id"])
            mapping = mapping_by_stream.get(field_stream_id)
            candidate = candidate_by_stream.get(field_stream_id)
            if mapping is None or candidate is None:
                raise HamburgCountStationBindingError(
                    f"field stream {field_stream_id} has no hash-bound SUMO identity evidence"
                )
            identity_evidence = _identity_basis(
                mapping,
                candidate,
                trusted_source_hashes=trusted_source_hashes,
                official_map_lane_ids=official_map_lane_ids,
            )
            lane_id = mapping["sumo_lane"]
            lane = network_lanes.get(lane_id)
            if lane is None or lane.edge_id != mapping["sumo_edge"]:
                raise HamburgCountStationBindingError(
                    f"field stream {field_stream_id} references an invalid SUMO lane identity"
                )
            position = _finite_float(mapping["lane_position"], "lane position")
            if position < 0 or position > lane.length:
                raise HamburgCountStationBindingError(
                    f"field stream {field_stream_id} detector position is outside its SUMO lane"
                )
            detector = detector_by_field_stream.setdefault(
                field_stream_id,
                Detector(
                    detector_id=str(mapping["detector_id"]),
                    source_system="Hamburg SensorThings Anzahl_Kfz_Zaehlfeld_5-Min",
                    direction=str(field.get("direction", "")),
                    edge_id=str(mapping["sumo_edge"]),
                    lane_id=lane_id,
                    lane_position=position,
                    period=str(period),
                    mapping_confidence=str(mapping["mapping_confidence"]),
                    mapping_status="active",
                ),
            )
            member = {
                "field_key": composition_key,
                "field_stream_id": field_stream_id,
                "field_detector_id": detector.detector_id,
                "sumo_edge": detector.edge_id,
                "sumo_lane": detector.lane_id,
                "lane_position": detector.lane_position,
                "identity_basis": identity_evidence["basis"],
                "identity_authority": identity_evidence["authority"],
                "identity_promotion_status": identity_evidence["promotion_status"],
                "mapping_reason": mapping["mapping_reason"],
            }
            previous_identity = identity_by_field_stream.setdefault(
                field_stream_id,
                {
                    "field": field,
                    "mapping": mapping,
                    "identity_evidence": identity_evidence,
                },
            )
            if previous_identity["mapping"] != mapping or previous_identity["identity_evidence"] != identity_evidence:
                raise HamburgCountStationBindingError(
                    f"field stream {field_stream_id} has inconsistent reused identity evidence"
                )
            members.append(member)
            membership.append(
                {
                    "station_stream_id": station_id,
                    "station_id": station["asset_id"],
                    "node_id": station["node_id"],
                    **member,
                }
            )

        station_audit = _audit_station_sum(
            station,
            members,
            field_observations,
            station_observations,
            begin=begin,
            end=end,
            period=period,
            source_bin_seconds=source_bin_seconds,
        )
        audit_rows.extend(station_audit)
        edges = {member["sumo_edge"] for member in members}
        member_lanes = {member["sumo_lane"] for member in members}
        eligible_edge = next(iter(edges)) if len(edges) == 1 else None
        position_span = (
            max(float(member["lane_position"]) for member in members)
            - min(float(member["lane_position"]) for member in members)
        )
        edge_constraint_eligible = bool(
            eligible_edge
            and len(members) == len(member_lanes)
            and member_lanes == passenger_lanes_by_edge.get(eligible_edge, set())
            and position_span <= 1.0
        )
        groups.append(
            {
                "station_stream_id": station_id,
                "station_id": station["asset_id"],
                "node_id": station["node_id"],
                "station_arm": station["station_arm"],
                "direction_code": station["direction_code"],
                "direction": station["direction"],
                "aggregation": "sum_of_official_composition_fields",
                "member_count": len(members),
                "members": members,
                "edge_constraint_eligible": edge_constraint_eligible,
                "edge_constraint_edge_id": eligible_edge if edge_constraint_eligible else None,
                "member_position_span_m": round(position_span, 6),
                "status": "constraint_eligible" if edge_constraint_eligible else "validation_only",
            }
        )

    detectors = sorted(detector_by_field_stream.values(), key=lambda item: item.detector_id)
    e1_path = destination / "station-field-e1.add.xml"
    e2_path = destination / "station-field-e2.add.xml"
    identity_index_path = destination / "physical-field-identity-index.csv"
    membership_path = destination / "station-group-membership.csv"
    group_path = destination / "station-groups.json"
    audit_path = destination / "station-composition-sum.audit.json"
    type_crosswalk_path = destination / "sensor-type-crosswalk.json"
    write_e1_additional(
        e1_path,
        detectors,
        lanes=network_lanes,
        period=period,
        output_file="station-field-e1-output.xml",
    )
    write_virtual_e2_additional(
        e2_path,
        detectors,
        output_file="station-field-e2-output.xml",
        period=period,
    )
    identity_index = _physical_identity_index(identity_by_field_stream, membership)
    _write_identity_index(identity_index_path, identity_index)
    _write_membership(membership_path, membership)
    write_json_atomic(group_path, groups, sort_keys=True)
    write_json_atomic(
        type_crosswalk_path,
        {
            "schema": "torii.hamburg-sensor-type-crosswalk/v1",
            "status": "pass",
            "types": _SENSOR_TYPE_CROSSWALK,
        },
        sort_keys=True,
    )
    write_json_atomic(
        audit_path,
        {
            "status": "pass",
            "begin_utc": begin.isoformat(),
            "end_utc": end.isoformat(),
            "bin_count": len(audit_rows),
            "differing_bin_count": sum(row["difference"] != 0 for row in audit_rows),
            "rows": audit_rows,
        },
        sort_keys=True,
    )

    eligible_groups = [group for group in groups if group["edge_constraint_eligible"]]
    review_identity_streams = sorted(
        stream_id
        for stream_id, row in identity_by_field_stream.items()
        if row["identity_evidence"]["promotion_status"] != "pass"
    )
    automatic_blockers = []
    if missing_nodes:
        automatic_blockers.append(f"missing official station nodes: {', '.join(missing_nodes)}")
    if review_identity_streams:
        automatic_blockers.append(
            "identity evidence source chain remains review-required for field streams: "
            + ", ".join(str(value) for value in review_identity_streams)
        )
    validation_only_ids = [
        group["station_stream_id"] for group in groups if not group["edge_constraint_eligible"]
    ]
    manifest = {
        "schema": COUNT_STATION_BINDING_SCHEMA,
        "status": "partial" if automatic_blockers else "pass",
        "execution_gate": "pass",
        "sensor_identity_binding_gate": "pass",
        "edge_constraint_coverage_gate": "partial" if validation_only_ids else "pass",
        "automatic_promotion_gate": "blocked" if automatic_blockers else "pass",
        "automatic_promotion_blockers": automatic_blockers,
        "supersedes": [
            {
                "artifact": "w3_station_cross_sections_v1",
                "reason": "processed station midpoint is not a physical detector location",
            },
            {
                "artifact": "w3_station_constraints_v1 and w3_station_route_support_probe_v1",
                "reason": "edge constraints must be derived from physical composition fields",
            },
        ],
        "network": {"path": str(net_path), "sha256": expected_hash},
        "station_inventory": {"path": str(inventory_path), "sha256": file_sha256(inventory_path)},
        "field_binding": {"path": str(field_binding_path), "sha256": file_sha256(field_binding_path)},
        "field_count_scope": {"path": str(field_scope_path), "sha256": file_sha256(field_scope_path)},
        "station_observations": {"path": str(observation_path), "sha256": file_sha256(observation_path)},
        "directional_station_stream_ids": sorted(directional_ids),
        "direction_zero_qa_stream_ids": sorted(total_ids),
        "missing_station_node_ids": missing_nodes,
        "station_group_count": len(groups),
        "unique_physical_field_detector_count": len(detectors),
        "group_membership_count": len(membership),
        "composition_sum_bin_count": len(audit_rows),
        "composition_sum_differing_bin_count": 0,
        "period_seconds": period,
        "source_bin_seconds": source_bin_seconds,
        "simulation_begin_utc": begin.isoformat(),
        "simulation_end_utc": end.isoformat(),
        "identity_evidence_counts": _count_values(
            row["identity_evidence"]["basis"] for row in identity_by_field_stream.values()
        ),
        "identity_review_required_field_stream_ids": review_identity_streams,
        "edge_constraint_eligible_station_stream_ids": [
            group["station_stream_id"] for group in eligible_groups
        ],
        "validation_only_station_stream_ids": validation_only_ids,
        "artifacts": {
            "e1_additional": _artifact_record(e1_path),
            "e2_additional": _artifact_record(e2_path),
            "physical_field_identity_index": _artifact_record(identity_index_path),
            "station_group_membership": _artifact_record(membership_path),
            "station_groups": _artifact_record(group_path),
            "composition_sum_audit": _artifact_record(audit_path),
            "sensor_type_crosswalk": _artifact_record(type_crosswalk_path),
            "direction_zero_qa": _artifact_record(direction_zero_qa_path),
        },
        "claim_boundary": (
            "Physical E1/E2 placement follows the official Zählfeld identities named by each "
            "Zählstelle composition; the abstract station midpoint is not used as a detector "
            "position. Station totals are compared with the sum of their member E1 outputs. "
            "Only groups that cover every passenger lane of one directed edge may become edge "
            "constraints; multi-edge groups are validation-only."
        ),
    }
    manifest_path = destination / "station-identity-binding.manifest.json"
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {**manifest, "manifest_path": str(manifest_path)}


def audit_hamburg_station_group_e1_output(
    *,
    station_binding_manifest: Path,
    e1_output_file: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Compare official directional station totals with sums of their physical E1 members."""

    binding_path = Path(station_binding_manifest).expanduser().resolve(strict=True)
    e1_path = Path(e1_output_file).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgCountStationBindingError("output_dir must be empty; choose a new versioned run")

    binding = _manifest(binding_path, COUNT_STATION_BINDING_SCHEMA)
    if binding.get("execution_gate") != "pass":
        raise HamburgCountStationBindingError("station binding execution gate is not pass")
    membership_path = _artifact(binding, binding_path, "station_group_membership")
    groups_path = _artifact(binding, binding_path, "station_groups")
    expected_path = _artifact(binding, binding_path, "composition_sum_audit")
    e1_additional_path = _artifact(binding, binding_path, "e1_additional")

    membership_rows = _csv_rows(membership_path)
    expected_payload = _json_object(expected_path)
    expected_rows = expected_payload.get("rows")
    groups = _json_list(groups_path)
    if expected_payload.get("status") != "pass" or not isinstance(expected_rows, list):
        raise HamburgCountStationBindingError("composition-sum audit is not a passing row set")

    members_by_station: dict[int, list[str]] = {}
    field_keys_by_station: dict[int, set[str]] = {}
    station_identity: dict[int, tuple[str, str]] = {}
    for row in membership_rows:
        station_stream_id = int(row["station_stream_id"])
        detector_id = safe_id(row["field_detector_id"])
        members = members_by_station.setdefault(station_stream_id, [])
        if detector_id in members:
            raise HamburgCountStationBindingError(
                f"station stream {station_stream_id} repeats physical E1 member {detector_id!r}"
            )
        members.append(detector_id)
        field_keys = field_keys_by_station.setdefault(station_stream_id, set())
        if row["field_key"] in field_keys:
            raise HamburgCountStationBindingError(
                f"station stream {station_stream_id} repeats official field {row['field_key']!r}"
            )
        field_keys.add(row["field_key"])
        identity = (row["station_id"], row["node_id"])
        if station_identity.setdefault(station_stream_id, identity) != identity:
            raise HamburgCountStationBindingError(
                f"station stream {station_stream_id} has inconsistent membership identity"
            )

    declared_detector_ids = {
        safe_id(element.attrib.get("id", ""))
        for element in ET.parse(e1_additional_path).getroot().findall(".//inductionLoop")
    }
    membership_detector_ids = {
        detector_id for members in members_by_station.values() for detector_id in members
    }
    if not membership_detector_ids or not membership_detector_ids <= declared_detector_ids:
        raise HamburgCountStationBindingError("station membership references an undeclared physical E1")

    group_ids: set[int] = set()
    for raw_group in groups:
        group = _mapping(raw_group, "station group")
        station_stream_id = int(group.get("station_stream_id"))
        if station_stream_id in group_ids:
            raise HamburgCountStationBindingError("station groups contain a duplicate stream identity")
        group_ids.add(station_stream_id)
        if int(group.get("member_count", -1)) != len(members_by_station.get(station_stream_id, [])):
            raise HamburgCountStationBindingError(
                f"station stream {station_stream_id} group/membership count mismatch"
            )
    if group_ids != set(members_by_station):
        raise HamburgCountStationBindingError("station groups and membership identities differ")

    simulation_begin = parse_iso_datetime(str(binding.get("simulation_begin_utc", "")))
    detector_counts = e1_counts_by_detector_interval_strict(e1_path)
    comparison_rows: list[dict[str, Any]] = []
    seen_expected: set[tuple[int, str, str]] = set()
    expected_station_ids: set[int] = set()
    for row_number, raw_row in enumerate(expected_rows, start=1):
        row = _mapping(raw_row, f"composition-sum row {row_number}")
        station_stream_id = int(row.get("station_stream_id"))
        expected_station_ids.add(station_stream_id)
        members = members_by_station.get(station_stream_id)
        if not members:
            raise HamburgCountStationBindingError(
                f"composition-sum row references unknown station stream {station_stream_id}"
            )
        official_member_counts = _mapping(
            row.get("composition_field_counts"),
            f"composition-sum row {row_number} member counts",
        )
        if set(official_member_counts) != field_keys_by_station[station_stream_id]:
            raise HamburgCountStationBindingError(
                f"station stream {station_stream_id} expected composition/membership mismatch"
            )
        begin_seconds = _whole_simulation_seconds(row.get("begin_utc"), simulation_begin)
        end_seconds = _whole_simulation_seconds(row.get("end_utc"), simulation_begin)
        if end_seconds <= begin_seconds:
            raise HamburgCountStationBindingError("composition-sum interval end must be after begin")
        key = (station_stream_id, f"{begin_seconds:g}", f"{end_seconds:g}")
        if key in seen_expected:
            raise HamburgCountStationBindingError(f"duplicate expected station interval {key!r}")
        seen_expected.add(key)
        member_counts = {
            detector_id: detector_counts.get((detector_id, key[1], key[2])) for detector_id in members
        }
        missing_members = [detector_id for detector_id, value in member_counts.items() if value is None]
        measured = None if missing_members else sum(int(value) for value in member_counts.values())
        expected = int(row.get("station_count"))
        if expected < 0:
            raise HamburgCountStationBindingError("official station count must be non-negative")
        if int(row.get("composition_sum")) != expected or int(row.get("difference")) != 0:
            raise HamburgCountStationBindingError("official station composition-sum evidence is inconsistent")
        difference = measured - expected if measured is not None else None
        station_id, node_id = station_identity[station_stream_id]
        comparison_rows.append(
            {
                "station_stream_id": station_stream_id,
                "station_id": station_id,
                "node_id": node_id,
                "begin": key[1],
                "end": key[2],
                "expected_total": expected,
                "member_detector_ids": ";".join(members),
                "member_counts": json.dumps(member_counts, sort_keys=True),
                "missing_member_detector_ids": ";".join(missing_members),
                "measurement_status": "missing" if missing_members else "matched",
                "measured_nVehContrib": measured,
                "diff_nVehContrib_minus_expected": difference,
                "GEH": geh_value(expected, measured) if measured is not None else None,
            }
        )
    if expected_station_ids != group_ids:
        raise HamburgCountStationBindingError("composition-sum rows do not cover every station group")

    complete_rows = [row for row in comparison_rows if row["measured_nVehContrib"] is not None]
    differences = [int(row["diff_nVehContrib_minus_expected"]) for row in complete_rows]
    missing_count = len(comparison_rows) - len(complete_rows)
    mismatch_count = sum(value != 0 for value in differences)
    absolute = [abs(value) for value in differences]
    metrics = {
        "expected_bin_count": len(comparison_rows),
        "complete_bin_count": len(complete_rows),
        "missing_bin_count": missing_count,
        "exact_match_bin_count": sum(value == 0 for value in differences),
        "mismatch_bin_count": mismatch_count,
        "expected_total": sum(int(row["expected_total"]) for row in comparison_rows),
        "complete_expected_total": sum(int(row["expected_total"]) for row in complete_rows),
        "measured_total": sum(int(row["measured_nVehContrib"]) for row in complete_rows),
        "MAE": sum(absolute) / len(absolute) if absolute else None,
        "RMSE": math.sqrt(sum(value * value for value in differences) / len(differences))
        if differences
        else None,
        "max_abs_error": max(absolute) if absolute else None,
        "GEH_lt5_percent": 100.0
        * sum(float(row["GEH"]) < 5 for row in complete_rows)
        / len(complete_rows)
        if complete_rows
        else None,
    }
    equality_gate = "pass" if missing_count == 0 and mismatch_count == 0 else "blocked"
    blockers = list(binding.get("automatic_promotion_blockers", []))
    if missing_count:
        blockers.append(f"{missing_count} official station bins have missing physical E1 members")
    if mismatch_count:
        blockers.append(f"{mismatch_count} official station bins differ from summed physical E1 output")

    comparison_path = destination / "station-group-e1-comparison.csv"
    summary_path = destination / "station-group-e1-summary.json"
    _write_station_e1_comparison(comparison_path, comparison_rows)
    write_json_atomic(summary_path, metrics, sort_keys=True)
    manifest = {
        "schema": STATION_GROUP_E1_AUDIT_SCHEMA,
        "status": "pass" if equality_gate == "pass" else "blocked",
        "execution_gate": "pass",
        "station_count_equality_gate": equality_gate,
        "automatic_promotion_gate": (
            "pass"
            if equality_gate == "pass" and binding.get("automatic_promotion_gate") == "pass"
            else "blocked"
        ),
        "automatic_promotion_blockers": blockers,
        "station_binding": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
        "e1_output": {"path": str(e1_path), "sha256": file_sha256(e1_path)},
        "count_attribute": "nVehContrib",
        "metrics": metrics,
        "artifacts": {
            "comparison": _artifact_record(comparison_path),
            "summary": _artifact_record(summary_path),
        },
        "claim_boundary": (
            "Each official directional station bin is compared with the sum of every physical E1 "
            "member in its frozen composition. A physical member may belong to more than one station; "
            "a missing member makes the whole station bin missing rather than zero."
        ),
    }
    manifest_path = destination / "station-group-e1-output-audit.manifest.json"
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {**manifest, "manifest_path": str(manifest_path)}


def _identity_basis(
    mapping: Mapping[str, str],
    candidate: Mapping[str, Any],
    *,
    trusted_source_hashes: set[str],
    official_map_lane_ids: set[tuple[str, str]],
) -> dict[str, Any]:
    stream_id = int(mapping["stream_id"])
    if mapping.get("mapping_status") != "active" or candidate.get("candidate_status") != "active":
        raise HamburgCountStationBindingError(f"field stream {stream_id} is not an active identity")
    if _node(str(candidate.get("node_id", ""))) != _node(mapping.get("node_id", "")) or str(
        candidate.get("asset_id", "")
    ) != mapping.get("asset_id", ""):
        raise HamburgCountStationBindingError(f"field stream {stream_id} candidate identity mismatch")
    if candidate.get("selected_lane") != mapping.get("sumo_lane"):
        raise HamburgCountStationBindingError(f"field stream {stream_id} candidate/mapping lane mismatch")
    if mapping.get("official_map_lane"):
        official_lane = str(mapping["official_map_lane"])
        if (
            (_node(mapping.get("node_id", "")), official_lane) not in official_map_lane_ids
            or str(candidate.get("official_map_lane", "")) != official_lane
        ):
            raise HamburgCountStationBindingError(
                f"field stream {stream_id} official MAP identity chain is incomplete"
            )
        detector_to_map = _finite_float(
            candidate.get("official_detector_to_map_distance_m"),
            "official detector-to-MAP distance",
        )
        map_to_sumo = _finite_float(
            candidate.get("official_map_to_sumo_distance_m"),
            "official MAP-to-SUMO distance",
        )
        heading_error = _finite_float(
            candidate.get("official_map_to_sumo_heading_error_deg"),
            "official MAP-to-SUMO heading error",
        )
        if detector_to_map > 6.0 or map_to_sumo > 3.0 or heading_error > 15.0:
            raise HamburgCountStationBindingError(
                f"field stream {stream_id} official MAP geometry exceeds identity tolerance"
            )
        return {
            "basis": "official_map_lane_identity",
            "authority": "official MAP lane + official field identity",
            "promotion_status": "pass" if detector_to_map <= 3.0 else "review_required",
            "source_hashes_closed": True,
            "official_detector_to_map_distance_m": detector_to_map,
            "official_map_to_sumo_distance_m": map_to_sumo,
            "official_map_to_sumo_heading_error_deg": heading_error,
        }
    constellation = candidate.get("constellation_inference")
    if isinstance(constellation, Mapping):
        margin = _finite_float(constellation.get("runner_up_margin_m"), "constellation runner-up margin")
        residual = _finite_float(constellation.get("maximum_residual_m"), "constellation residual")
        sources = constellation.get("official_source_sha256")
        stream_ids = constellation.get("stream_ids")
        if (
            margin >= 1.0
            and residual <= 1.0
            and isinstance(sources, list)
            and sources
            and isinstance(stream_ids, list)
            and stream_id in {int(value) for value in stream_ids}
        ):
            source_hashes = {_sha256_text(str(value)) for value in sources}
            sources_closed = source_hashes <= trusted_source_hashes
            return {
                "basis": "official_lane_count_unique_field_constellation",
                "authority": "official engineering-plan lane count + official field constellation",
                "promotion_status": "pass" if sources_closed else "review_required",
                "source_hashes_closed": sources_closed,
                "official_source_sha256": sorted(source_hashes),
            }
    distance = _finite_float(candidate.get("selected_distance_m"), "selected distance")
    separation = _finite_float(candidate.get("separation_m"), "candidate separation")
    if mapping.get("mapping_confidence") == "high" and distance <= 1.0 and separation >= 1.0:
        return {
            "basis": "unique_official_field_point_to_osm_lane",
            "authority": "official field point + frozen OSM-derived SUMO lane",
            "promotion_status": "pass",
            "source_hashes_closed": True,
        }
    raise HamburgCountStationBindingError(
        f"field stream {stream_id} has no precise official-field-to-SUMO identity proof"
    )


def _audit_station_sum(
    station: Mapping[str, Any],
    members: list[dict[str, Any]],
    field_observations: Mapping[str, Any],
    station_observations: Mapping[str, Any],
    *,
    begin: datetime,
    end: datetime,
    period: int,
    source_bin_seconds: int,
) -> list[dict[str, Any]]:
    station_id = int(station["stream_id"])
    station_index = _observation_index(
        station_observations.get(str(station_id)),
        station_id,
        expected_seconds=period,
    )
    field_indices = {
        member["field_stream_id"]: _observation_index(
            field_observations.get(str(member["field_stream_id"])),
            member["field_stream_id"],
            expected_seconds=source_bin_seconds,
        )
        for member in members
    }
    rows: list[dict[str, Any]] = []
    timestamp = begin
    while timestamp < end:
        station_value = station_index.get(timestamp)
        if station_value is None:
            raise HamburgCountStationBindingError(
                f"station stream {station_id} is missing {timestamp.isoformat()}"
            )
        member_counts: dict[str, int] = {}
        for member in members:
            field_stream_id = member["field_stream_id"]
            source_values = [
                field_indices[field_stream_id].get(timestamp + timedelta(seconds=offset))
                for offset in range(0, period, source_bin_seconds)
            ]
            if any(value is None for value in source_values):
                raise HamburgCountStationBindingError(
                    f"field stream {field_stream_id} is incomplete at {timestamp.isoformat()}"
                )
            member_counts[member["field_key"]] = sum(int(value) for value in source_values)
        member_sum = sum(member_counts.values())
        if member_sum != station_value:
            raise HamburgCountStationBindingError(
                f"station stream {station_id} composition does not reproduce {timestamp.isoformat()}"
            )
        rows.append(
            {
                "station_stream_id": station_id,
                "begin_utc": timestamp.isoformat(),
                "end_utc": (timestamp + timedelta(seconds=period)).isoformat(),
                "station_count": station_value,
                "composition_field_counts": member_counts,
                "composition_sum": member_sum,
                "difference": member_sum - station_value,
            }
        )
        timestamp += timedelta(seconds=period)
    return rows


def _station_window(manifest: Mapping[str, Any]) -> tuple[datetime, datetime]:
    selected = manifest.get("selected_window")
    if not isinstance(selected, Mapping):
        raise HamburgCountStationBindingError("station observation window is missing")
    begin = parse_iso_datetime(str(selected.get("simulation_begin_utc", "")))
    end = parse_iso_datetime(str(selected.get("simulation_end_utc", "")))
    if end <= begin:
        raise HamburgCountStationBindingError("station observation window is invalid")
    return begin, end


def _validate_station_window_contract(
    manifest: Mapping[str, Any],
    *,
    inventory_raw_sha256: str,
    station_window_inventory_sha256: str,
    directional_ids: set[int],
    total_ids: set[int],
    missing_nodes: list[str],
    period: int,
) -> None:
    if station_window_inventory_sha256 != inventory_raw_sha256:
        raise HamburgCountStationBindingError("station observations use a different station inventory")
    observed_directional = _integer_set(
        manifest.get("directional_station_stream_ids"),
        "station-window directional streams",
    )
    if observed_directional != directional_ids:
        raise HamburgCountStationBindingError("station-window directional identities do not match inventory")
    observed_missing = sorted(str(value) for value in manifest.get("missing_station_node_ids", []))
    if observed_missing != missing_nodes:
        raise HamburgCountStationBindingError("station-window missing-node identities do not match inventory")
    selected = _mapping(manifest.get("selected_window"), "station selected window")
    if int(selected.get("output_bin_seconds", 0)) != period:
        raise HamburgCountStationBindingError("station output cadence does not match requested period")
    formal = _mapping(selected.get("formal_window"), "station formal window")
    if int(formal.get("source_bin_seconds", 0)) != period:
        raise HamburgCountStationBindingError("station source cadence does not match requested period")
    if not total_ids:
        raise HamburgCountStationBindingError("station inventory has no direction-zero QA identities")


def _validate_direction_zero_qa(
    path: Path,
    *,
    total_ids: set[int],
    directional_ids: set[int],
) -> None:
    payload = _json_object(path)
    if payload.get("schema") != "torii.hamburg-station-direction-zero-qa/v1" or payload.get("status") != "pass":
        raise HamburgCountStationBindingError("direction-zero station QA did not pass")
    groups = payload.get("groups")
    if not isinstance(groups, list):
        raise HamburgCountStationBindingError("direction-zero station QA has no groups")
    observed_totals: set[int] = set()
    observed_directional: set[int] = set()
    for raw in groups:
        group = _mapping(raw, "direction-zero QA group")
        total_id = int(group.get("total_stream_id"))
        if total_id in observed_totals or int(group.get("different_bin_count", -1)) != 0:
            raise HamburgCountStationBindingError("direction-zero station QA is inconsistent")
        observed_totals.add(total_id)
        members = _integer_set(group.get("directional_stream_ids"), "direction-zero QA members")
        if not members <= directional_ids:
            raise HamburgCountStationBindingError("direction-zero QA references an unknown directional stream")
        observed_directional.update(members)
    if observed_totals != total_ids or observed_directional != directional_ids:
        raise HamburgCountStationBindingError("direction-zero QA identities do not cover the station inventory")


def _validate_field_scope_cadence(manifest: Mapping[str, Any], *, source_bin_seconds: int) -> None:
    selected = _mapping(manifest.get("selected_window"), "field-count selected window")
    formal_raw = selected.get("formal_window")
    if not isinstance(formal_raw, Mapping):
        simulation = _mapping(selected.get("simulation_window"), "field-count simulation window")
        formal_raw = simulation.get("formal_window")
    formal = _mapping(formal_raw, "field-count formal window")
    if int(formal.get("source_bin_seconds", 0)) != source_bin_seconds:
        raise HamburgCountStationBindingError("field-count cadence does not match source_bin_seconds")


def _validated_identity_sources(
    source: Mapping[str, Any],
    manifest_path: Path,
) -> tuple[set[str], set[tuple[str, str]]]:
    trusted: set[str] = set()
    for key in ("candidate_net", "count_stream_snapshot", "movement_lane_evidence"):
        record = source.get(key)
        if record is not None:
            trusted.add(_validate_source_record(record, manifest_path, key))
    official_maps = source.get("official_map_files", [])
    if not isinstance(official_maps, list):
        raise HamburgCountStationBindingError("official_map_files must be a list")
    official_map_lane_ids: set[tuple[str, str]] = set()
    for index, record in enumerate(official_maps):
        label = f"official_map_files[{index}]"
        source_path, source_hash = _validate_source_record_path(record, manifest_path, label)
        trusted.add(source_hash)
        try:
            lanes, _ = parse_mapem(source_path)
        except (OSError, ET.ParseError, ValueError) as exc:
            raise HamburgCountStationBindingError(f"{label} is not a readable MAPEM asset") from exc
        official_map_lane_ids.update(
            (_node(lane.node_id), str(lane.lane_id)) for lane in lanes if lane.node_id and lane.lane_id
        )
    return trusted, official_map_lane_ids


def _validate_source_record(raw: Any, manifest_path: Path, label: str) -> str:
    _, expected = _validate_source_record_path(raw, manifest_path, label)
    return expected


def _validate_source_record_path(raw: Any, manifest_path: Path, label: str) -> tuple[Path, str]:
    record = _mapping(raw, label)
    expected = _sha256_text(str(record.get("sha256", "")))
    raw_path = record.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise HamburgCountStationBindingError(f"{label} has no source path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = manifest_path.parent / path
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise HamburgCountStationBindingError(f"{label} source path is unavailable") from exc
    if file_sha256(path) != expected:
        raise HamburgCountStationBindingError(f"{label} source SHA-256 mismatch")
    return path, expected


def _station_inventory(rows: list[Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise HamburgCountStationBindingError("station inventory row must be an object")
        stream_id = int(raw.get("stream_id"))
        composition = raw.get("composition")
        if stream_id in result or not isinstance(composition, list) or not composition:
            raise HamburgCountStationBindingError("station inventory has a duplicate or empty composition")
        result[stream_id] = raw
    return result


def _field_inventory(rows: list[Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[int, dict[str, Any]]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    by_stream: dict[int, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise HamburgCountStationBindingError("field inventory row must be an object")
        stream_id = int(raw.get("stream_id"))
        key = (_node(str(raw.get("node_id", ""))), str(raw.get("asset_id", "")))
        if stream_id in by_stream or key in by_key:
            raise HamburgCountStationBindingError("field inventory identity is not unique")
        by_key[key] = raw
        by_stream[stream_id] = raw
    return by_key, by_stream


def _field_mappings(rows: list[dict[str, str]], fields: Mapping[int, Mapping[str, Any]]) -> dict[int, dict[str, str]]:
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        stream_id = int(row["stream_id"])
        field = fields.get(stream_id)
        if field is None:
            continue
        if stream_id in result:
            raise HamburgCountStationBindingError(f"duplicate field mapping stream {stream_id}")
        if _node(row["node_id"]) != _node(str(field["node_id"])) or row["asset_id"] != field["asset_id"]:
            raise HamburgCountStationBindingError(f"field mapping identity mismatch for stream {stream_id}")
        result[stream_id] = row
    return result


def _candidate_evidence(rows: list[Any]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        stream_id = int(raw.get("stream_id"))
        if stream_id in result:
            raise HamburgCountStationBindingError(f"duplicate field candidate stream {stream_id}")
        result[stream_id] = raw
    return result


def _observation_index(raw: Any, stream_id: int, *, expected_seconds: int) -> dict[datetime, int]:
    if not isinstance(raw, list):
        raise HamburgCountStationBindingError(f"observations for stream {stream_id} are missing")
    result: dict[datetime, int] = {}
    for row in raw:
        if not isinstance(row, Mapping) or int(row.get("stream_id")) != stream_id:
            raise HamburgCountStationBindingError(f"observation identity mismatch for stream {stream_id}")
        timestamp = parse_iso_datetime(str(row.get("begin_utc", "")))
        end = parse_iso_datetime(str(row.get("end_utc", "")))
        if int((end - timestamp).total_seconds()) != expected_seconds:
            raise HamburgCountStationBindingError(
                f"observation cadence mismatch for stream {stream_id}"
            )
        if timestamp in result:
            raise HamburgCountStationBindingError(f"duplicate observation for stream {stream_id}")
        count = int(row.get("count"))
        if count < 0:
            raise HamburgCountStationBindingError(f"negative observation for stream {stream_id}")
        result[timestamp] = count
    return result


def _passenger_lanes_by_edge(net_path: Path) -> dict[str, set[str]]:
    root = ET.parse(net_path).getroot()
    return {
        edge.attrib["id"]: {
            lane.attrib["id"]
            for lane in edge.findall("lane")
            if lane.attrib.get("id") and lane_allows_passenger(lane)
        }
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("function") and not edge.attrib["id"].startswith(":")
    }


def _parse_field_key(value: str) -> tuple[str, str]:
    match = _FIELD_KEY.fullmatch(str(value))
    if match is None:
        raise HamburgCountStationBindingError(f"invalid official composition field identity {value!r}")
    return _node(match.group("node")), match.group("asset")


def _node(value: str) -> str:
    text = value.strip()
    return text.lstrip("0") or "0"


def _manifest(path: Path, schema: str) -> dict[str, Any]:
    payload = _json_object(path)
    if payload.get("schema") != schema:
        raise HamburgCountStationBindingError(f"unsupported manifest schema in {path}")
    return payload


def _artifact(manifest: Mapping[str, Any], manifest_path: Path, key: str) -> Path:
    artifacts = manifest.get("artifacts")
    record = artifacts.get(key) if isinstance(artifacts, Mapping) else None
    raw_path = record.get("path") if isinstance(record, Mapping) else None
    expected = str(record.get("sha256", "")) if isinstance(record, Mapping) else ""
    if not isinstance(raw_path, str) or not raw_path:
        raise HamburgCountStationBindingError(f"manifest artifact {key!r} is missing")
    path = Path(raw_path)
    if not path.is_absolute():
        path = manifest_path.parent / path
    path = path.resolve(strict=True)
    if file_sha256(path) != expected:
        raise HamburgCountStationBindingError(f"manifest artifact {key!r} SHA-256 mismatch")
    return path


def _artifact_hash(manifest: Mapping[str, Any], key: str) -> str:
    artifacts = manifest.get("artifacts")
    record = artifacts.get(key) if isinstance(artifacts, Mapping) else None
    value = str(record.get("sha256", "")) if isinstance(record, Mapping) else ""
    return _sha256_text(value)


def _artifact_record(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": file_sha256(path)}


def _json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HamburgCountStationBindingError(f"cannot read JSON artifact {path}") from exc
    if not isinstance(payload, dict):
        raise HamburgCountStationBindingError(f"JSON artifact {path} must be an object")
    return payload


def _json_list(path: Path) -> list[Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HamburgCountStationBindingError(f"cannot read JSON artifact {path}") from exc
    if not isinstance(payload, list):
        raise HamburgCountStationBindingError(f"JSON artifact {path} must be a list")
    return payload


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _integer_set(raw: Any, label: str) -> set[int]:
    if not isinstance(raw, list):
        raise HamburgCountStationBindingError(f"{label} must be a list")
    result = {int(value) for value in raw}
    if len(result) != len(raw):
        raise HamburgCountStationBindingError(f"{label} contains duplicates")
    return result


def _finite_float(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise HamburgCountStationBindingError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise HamburgCountStationBindingError(f"{label} must be finite")
    return result


def _sha256_text(value: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise HamburgCountStationBindingError("a SHA-256 hex digest is required")
    return text


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HamburgCountStationBindingError(f"{label} must be an object")
    return value


def _physical_identity_index(
    identities: Mapping[int, Mapping[str, Any]],
    membership: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    membership_by_field: dict[int, list[dict[str, Any]]] = {}
    for row in membership:
        membership_by_field.setdefault(int(row["field_stream_id"]), []).append(row)
    result: list[dict[str, Any]] = []
    for stream_id in sorted(identities):
        identity = identities[stream_id]
        field = _mapping(identity["field"], "physical field identity")
        mapping = _mapping(identity["mapping"], "physical field mapping")
        evidence = _mapping(identity["identity_evidence"], "physical field evidence")
        station_rows = membership_by_field.get(stream_id, [])
        osm = _osm_lineage(str(mapping.get("sumo_edge", "")))
        result.append(
            {
                "official_sensor_type": "Anzahl_Kfz_Zaehlfeld_5-Min",
                "official_node_id": _node(str(field.get("node_id", ""))),
                "official_field_asset_id": str(field.get("asset_id", "")),
                "official_datastream_id": stream_id,
                "official_thing_id": field.get("thing_id", ""),
                "official_direction": str(field.get("direction", "")),
                "official_longitude": field.get("longitude", ""),
                "official_latitude": field.get("latitude", ""),
                "station_stream_ids": ";".join(
                    str(value) for value in sorted({int(row["station_stream_id"]) for row in station_rows})
                ),
                "station_ids": ";".join(sorted({str(row["station_id"]) for row in station_rows})),
                "sumo_edge_id": str(mapping.get("sumo_edge", "")),
                "sumo_lane_id": str(mapping.get("sumo_lane", "")),
                "sumo_lane_position_m": mapping.get("lane_position", ""),
                "sumo_primary_sensor_type": "inductionLoop/E1",
                "sumo_secondary_sensor_type": "laneAreaDetector/E2 diagnostic",
                "osm_way_id_from_sumo_edge": osm["way_id"],
                "osm_way_direction": osm["direction"],
                "osm_way_segment_index": osm["segment_index"],
                "identity_basis": str(evidence.get("basis", "")),
                "identity_authority": str(evidence.get("authority", "")),
                "identity_promotion_status": str(evidence.get("promotion_status", "")),
                "mapping_reason": str(mapping.get("mapping_reason", "")),
            }
        )
    return result


def _osm_lineage(edge_id: str) -> dict[str, str]:
    match = _OSM_EDGE_ID.fullmatch(edge_id)
    if match is None:
        return {"way_id": "", "direction": "", "segment_index": ""}
    return {
        "way_id": match.group("way"),
        "direction": "reverse" if match.group("reverse") else "forward",
        "segment_index": match.group("segment") or "",
    }


def _count_values(values: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        text = str(value)
        result[text] = result.get(text, 0) + 1
    return dict(sorted(result.items()))


def _whole_simulation_seconds(value: Any, simulation_begin: datetime) -> float:
    seconds = (parse_iso_datetime(str(value)) - simulation_begin).total_seconds()
    if seconds < 0 or not seconds.is_integer():
        raise HamburgCountStationBindingError(
            "official station interval must align to whole non-negative SUMO seconds"
        )
    return seconds


def _write_station_e1_comparison(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "station_stream_id",
        "station_id",
        "node_id",
        "begin",
        "end",
        "expected_total",
        "member_detector_ids",
        "member_counts",
        "missing_member_detector_ids",
        "measurement_status",
        "measured_nVehContrib",
        "diff_nVehContrib_minus_expected",
        "GEH",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, buffer.getvalue())


def _write_identity_index(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "official_sensor_type",
        "official_node_id",
        "official_field_asset_id",
        "official_datastream_id",
        "official_thing_id",
        "official_direction",
        "official_longitude",
        "official_latitude",
        "station_stream_ids",
        "station_ids",
        "sumo_edge_id",
        "sumo_lane_id",
        "sumo_lane_position_m",
        "sumo_primary_sensor_type",
        "sumo_secondary_sensor_type",
        "osm_way_id_from_sumo_edge",
        "osm_way_direction",
        "osm_way_segment_index",
        "identity_basis",
        "identity_authority",
        "identity_promotion_status",
        "mapping_reason",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, buffer.getvalue())


def _write_membership(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "station_stream_id",
        "station_id",
        "node_id",
        "field_key",
        "field_stream_id",
        "field_detector_id",
        "sumo_edge",
        "sumo_lane",
        "lane_position",
        "identity_basis",
        "identity_authority",
        "identity_promotion_status",
        "mapping_reason",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomic(path, buffer.getvalue())
