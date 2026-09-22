from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from torii_sumo.intersection.composable_archetype import (
    build_ocit_controller_domain_evidence,
)

from .digital_twin import MapConnection, MapLane, SignalStream, parse_mapem
from .digital_twin_mapping import MapLaneBinding, bind_map_lanes_to_network
from .hamburg_official import hamburg_sandtorkai_primary_signal_snapshot
from .ocit_c import (
    OcitCConfig,
    OcitVehicleTopologyInventory,
    build_vehicle_topology_inventory,
    parse_ocit_c,
    topology_control_index_by_node,
)


SCHEMA_ID = "torii.hamburg-2394-multi-owner-tls-topology/v1"
PROTOTYPE_ID = "hamburg_2394_v1"
CONTROLLER_ID = "HH_2394"

SIGNAL_OWNER_IDS = (
    "759714726",
    "cluster_2761334279_757036795",
    "cluster_3847369287_757036909_76463166",
)
PASSIVE_OWNER_IDS = ("3847369285", "3847369288")
MAIN_OWNER_ID = "cluster_3847369287_757036909_76463166"

EXPECTED_MAP_LANE_TO_SUMO_LANE = {
    "2": "9702432#0_0",
    "3": "9702432#0_1",
    "4": "193847534#1_0",
    "5": "193847534#1_1",
    "6": "381540198#1_0",
    "7": "381540198#1_1",
    "9": "-381540198#1_0",
    "10": "47854310#2_0",
    "11": "47854310#2_1",
    "12": "47854310#2_2",
    "14": "554713077_0",
}

EXPECTED_MOVEMENTS = {
    "1": ("10", "9", "P_K6__S_K7", "K7"),
    "2": ("11", "4", "P_K7__S_NONE", "K7"),
    "3": ("12", "5", "P_K7__S_NONE", "K7"),
    "5": ("2", "14", "P_K1__S_NONE", "K1"),
    "6": ("3", "9", "P_NONE__S_K2", "K2"),
    "7": ("6", "4", "P_K3__S_K4", "K4"),
    "8": ("6", "5", "P_K3__S_K4", "K4"),
    "9": ("7", "14", "P_NONE__S_K5", "K5"),
}

EXPECTED_PHASE_IMAGES = {
    "Phase 1": {"K1": "30", "K2": "03", "K3": "00", "K4": "03", "K5": "03", "K6": "00", "K7": "30"},
    "Phase 2": {"K1": "03", "K2": "03", "K3": "00", "K4": "30", "K5": "30", "K6": "30", "K7": "03"},
    "Phase 3": {"K1": "30", "K2": "30", "K3": "30", "K4": "03", "K5": "03", "K6": "00", "K7": "03"},
}


class Hamburg2394TlsTopologyError(ValueError):
    """Raised when 2394 topology cannot be compiled without guessing."""


@dataclass(frozen=True, order=True)
class PhysicalLink:
    from_edge: str
    from_lane: int
    to_edge: str
    to_lane: int

    @property
    def key(self) -> tuple[str, int, str, int]:
        return (self.from_edge, self.from_lane, self.to_edge, self.to_lane)

    @property
    def from_lane_id(self) -> str:
        return f"{self.from_edge}_{self.from_lane}"

    @property
    def to_lane_id(self) -> str:
        return f"{self.to_edge}_{self.to_lane}"


ROUTING_REPAIRS = (
    PhysicalLink("381540198#1", 0, "193847534#0", 1),
    PhysicalLink("60578519", 1, "193847534#0", 0),
    PhysicalLink("60578519", 2, "193847534#0", 1),
)

ROUTING_REMOVALS = (
    PhysicalLink("381540198#1", 1, "193847534#0", 1),
    PhysicalLink("60578519", 0, "193847534#0", 0),
    PhysicalLink("60578519", 1, "193847534#0", 1),
    PhysicalLink("60578519", 2, "554713077", 0),
    PhysicalLink("9702432#2", 1, "193847534#0", 1),
)

EXPECTED_MAIN_ROUTING = (
    PhysicalLink("381540198#1", 0, "193847534#0", 0),
    PhysicalLink("381540198#1", 0, "193847534#0", 1),
    PhysicalLink("381540198#1", 1, "554713077", 0),
    PhysicalLink("60578519", 0, "-381540198#1", 0),
    PhysicalLink("60578519", 1, "193847534#0", 0),
    PhysicalLink("60578519", 2, "193847534#0", 1),
    PhysicalLink("9702432#2", 0, "554713077", 0),
    PhysicalLink("9702432#2", 1, "-381540198#1", 0),
)

EXPECTED_CURRENT_MAIN_ROUTING = tuple(
    sorted((set(EXPECTED_MAIN_ROUTING) - set(ROUTING_REPAIRS)) | set(ROUTING_REMOVALS))
)


def build_hamburg_2394_tls_topology_plan(
    *,
    source_net_file: Path,
    map_file: Path,
    ocit_file: Path,
    classification_file: Path,
    accepted_classification_id: str,
    expected_source_sha256: str,
    expected_map_sha256: str,
    expected_ocit_sha256: str,
    expected_classification_sha256: str,
    observed_streams: Sequence[SignalStream] | None = None,
) -> dict[str, Any]:
    """Compile a fail-closed 2394 topology plan without mutating a SUMO network.

    This is deliberately a second-pass *plan compiler*.  It proves the exact eight
    official vehicle movements, six primary/secondary control expressions, three
    signal-bearing SUMO owners and one shared controller.  It neither installs a
    signal program nor treats the three OCIT phase labels as historical durations.
    """

    paths = {
        "source_net": Path(source_net_file).resolve(),
        "map": Path(map_file).resolve(),
        "ocit": Path(ocit_file).resolve(),
        "classification": Path(classification_file).resolve(),
    }
    if len(set(paths.values())) != len(paths):
        raise Hamburg2394TlsTopologyError("all four evidence paths must be distinct")
    expected_hashes = {
        "source_net": expected_source_sha256,
        "map": expected_map_sha256,
        "ocit": expected_ocit_sha256,
        "classification": expected_classification_sha256,
    }
    actual_hashes: dict[str, str] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise Hamburg2394TlsTopologyError(f"{name} file does not exist: {path}")
        expected = _validated_sha256(expected_hashes[name], name)
        actual = _sha256_file(path)
        if actual != expected:
            raise Hamburg2394TlsTopologyError(
                f"{name} SHA-256 mismatch: expected {expected}, got {actual}"
            )
        actual_hashes[name] = actual

    classification = _read_classification(paths["classification"])
    _validate_classification(classification, accepted_classification_id)

    map_lanes, map_connections = parse_mapem(paths["map"])
    ocit = parse_ocit_c(paths["ocit"])
    streams = tuple(
        observed_streams
        if observed_streams is not None
        else (
            stream
            for stream in hamburg_sandtorkai_primary_signal_snapshot()
            if _normalize_node(stream.node_id) == "2394"
        )
    )
    inventory = build_vehicle_topology_inventory(
        (ocit,), map_lanes, map_connections, streams
    )
    lane_bindings = bind_map_lanes_to_network(paths["source_net"], map_lanes)
    controller = build_ocit_controller_domain_evidence(paths["ocit"], "2394")
    plan = compile_hamburg_2394_tls_topology(
        source_net_file=paths["source_net"],
        map_lanes=map_lanes,
        map_connections=map_connections,
        ocit=ocit,
        inventory=inventory,
        lane_bindings=lane_bindings,
        observed_streams=streams,
        controller_evidence=controller,
    )
    plan["classification"] = {
        "classification_id": classification["classification_id"],
        "prototype_id": classification["prototype_id"],
        "status": classification["status"],
        "automatic_promotion_gate": classification["automatic_promotion_gate"],
    }
    plan["sources"] = {
        name: {"file": str(paths[name]), "sha256": actual_hashes[name]}
        for name in paths
    }
    return plan


def compile_hamburg_2394_tls_topology(
    *,
    source_net_file: Path,
    map_lanes: Sequence[MapLane],
    map_connections: Sequence[MapConnection],
    ocit: OcitCConfig,
    inventory: OcitVehicleTopologyInventory,
    lane_bindings: Sequence[MapLaneBinding],
    observed_streams: Sequence[SignalStream],
    controller_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile already-parsed evidence; exposed for deterministic unit testing."""

    source_net_file = Path(source_net_file).resolve()
    source_hash_before = _sha256_file(source_net_file)
    if _normalize_node(ocit.node_id) != "2394":
        raise Hamburg2394TlsTopologyError(f"expected OCIT node 2394, got {ocit.node_id!r}")
    _validate_map_inventory(map_lanes, map_connections, inventory, observed_streams)
    _validate_lane_bindings(lane_bindings)
    phase_gate = _validate_ocit_gate(ocit, controller_evidence)

    root = ET.parse(source_net_file).getroot()
    junctions = {element.attrib.get("id", ""): element for element in root.findall("junction")}
    all_owners = {*SIGNAL_OWNER_IDS, *PASSIVE_OWNER_IDS}
    missing_owners = sorted(all_owners - set(junctions))
    if missing_owners:
        raise Hamburg2394TlsTopologyError(f"candidate is missing 2394 owners: {missing_owners}")
    non_priority = sorted(
        owner for owner in all_owners if junctions[owner].attrib.get("type") != "priority"
    )
    if non_priority:
        raise Hamburg2394TlsTopologyError(
            "second-pass source must retain all five 2394 owners as priority: "
            f"{non_priority}"
        )
    if any(
        element.attrib.get("id") == CONTROLLER_ID for element in root.findall("tlLogic")
    ):
        raise Hamburg2394TlsTopologyError(
            f"second-pass source already contains controller {CONTROLLER_ID}"
        )

    edges, lane_ids = _edge_and_lane_index(root)
    current_links = _external_links(root)
    current_main = {
        link
        for link in current_links
        if _connection_owner(link, edges) == MAIN_OWNER_ID
    }
    if current_main != set(EXPECTED_CURRENT_MAIN_ROUTING):
        raise Hamburg2394TlsTopologyError(
            "2394 main-owner first-pass routing differs from the reviewed preset; "
            f"missing={sorted(set(EXPECTED_CURRENT_MAIN_ROUTING) - current_main)}, "
            f"unexpected={sorted(current_main - set(EXPECTED_CURRENT_MAIN_ROUTING))}"
        )
    missing_removals = sorted(set(ROUTING_REMOVALS) - set(current_links))
    if missing_removals:
        raise Hamburg2394TlsTopologyError(
            f"declared official routing removals are absent: {missing_removals}"
        )
    preexisting_repairs = sorted(set(ROUTING_REPAIRS) & set(current_links))
    if preexisting_repairs:
        raise Hamburg2394TlsTopologyError(
            f"first-pass source already contains routing repairs: {preexisting_repairs}"
        )
    for repair in ROUTING_REPAIRS:
        if repair.from_lane_id not in lane_ids or repair.to_lane_id not in lane_ids:
            raise Hamburg2394TlsTopologyError(
                f"routing repair references an absent lane: {repair}"
            )
        if _connection_owner(repair, edges) != MAIN_OWNER_ID:
            raise Hamburg2394TlsTopologyError(
                f"routing repair is not local to main owner {MAIN_OWNER_ID}: {repair}"
            )

    patched_links = (set(current_links) - set(ROUTING_REMOVALS)) | set(ROUTING_REPAIRS)
    patched_main = {
        link for link in patched_links if _connection_owner(link, edges) == MAIN_OWNER_ID
    }
    if patched_main != set(EXPECTED_MAIN_ROUTING):
        raise Hamburg2394TlsTopologyError("official routing patch does not yield exactly eight main arcs")

    binding_by_lane = {
        binding.map_lane_id: binding
        for binding in lane_bindings
        if _normalize_node(binding.node_id) == "2394"
        and binding.map_lane_id in EXPECTED_MAP_LANE_TO_SUMO_LANE
    }
    movement_by_id = {movement.connection_id: movement for movement in inventory.movements}
    expression_indices = topology_control_index_by_node(inventory).get("2394", {})
    paths_by_connection: dict[str, list[PhysicalLink]] = {}
    stopline_by_connection: dict[str, PhysicalLink] = {}
    for connection_id in sorted(EXPECTED_MOVEMENTS, key=int):
        movement = movement_by_id[connection_id]
        start = binding_by_lane[movement.ingress_lane_id].sumo_lane
        target = binding_by_lane[movement.egress_lane_id].sumo_lane
        paths, overflow = _find_paths(patched_links, start, target, max_hops=8, max_paths=2)
        if overflow or len(paths) != 1:
            raise Hamburg2394TlsTopologyError(
                f"official movement {connection_id} has {len(paths)} patched candidate paths; "
                "exactly one is required"
            )
        path = paths[0]
        if not path:
            raise Hamburg2394TlsTopologyError(
                f"official movement {connection_id} has an empty lane path"
            )
        paths_by_connection[connection_id] = path
        stopline_by_connection[connection_id] = path[0]

    owners_by_connection = {
        connection_id: _connection_owner(link, edges)
        for connection_id, link in stopline_by_connection.items()
    }
    observed_signal_owners = tuple(sorted(set(owners_by_connection.values())))
    if observed_signal_owners != tuple(sorted(SIGNAL_OWNER_IDS)):
        raise Hamburg2394TlsTopologyError(
            f"derived signal owners={observed_signal_owners}, expected={tuple(sorted(SIGNAL_OWNER_IDS))}"
        )
    if any(owner in PASSIVE_OWNER_IDS for owner in owners_by_connection.values()):
        raise Hamburg2394TlsTopologyError("a passive connector was incorrectly selected as a signal owner")

    physical_owners: dict[PhysicalLink, str] = {}
    movement_rows: list[dict[str, Any]] = []
    groups: dict[str, list[PhysicalLink]] = defaultdict(list)
    stream_by_connection = {
        stream.connection_id: stream
        for stream in observed_streams
        if _normalize_node(stream.node_id) == "2394"
        and stream.layer_name == "primary_signal"
    }
    for connection_id in sorted(EXPECTED_MOVEMENTS, key=int):
        movement = movement_by_id[connection_id]
        stopline = stopline_by_connection[connection_id]
        previous = physical_owners.get(stopline)
        if previous is not None and previous != movement.topology_control_key:
            raise Hamburg2394TlsTopologyError(
                f"physical stop-line link {stopline} is claimed by {previous} and "
                f"{movement.topology_control_key}"
            )
        physical_owners[stopline] = movement.topology_control_key
        groups[movement.topology_control_key].append(stopline)
        stream = stream_by_connection[connection_id]
        movement_rows.append(
            {
                "connection_id": connection_id,
                "official_ingress_lane_id": movement.ingress_lane_id,
                "official_egress_lane_id": movement.egress_lane_id,
                "primary_motor_groups": list(movement.primary_motor_groups),
                "secondary_motor_groups": list(movement.secondary_motor_groups),
                "topology_control_key": movement.topology_control_key,
                "link_index": expression_indices[movement.topology_control_key],
                "tld_metadata_stream_id": stream.stream_id,
                "tld_metadata_signal_group": stream.signal_group,
                "signal_owner_id": owners_by_connection[connection_id],
                "controlled_stopline_link": asdict(stopline),
                "unique_external_lane_path": [asdict(link) for link in paths_by_connection[connection_id]],
            }
        )

    group_rows = [
        {
            "topology_control_key": key,
            "link_index": expression_indices[key],
            "physical_links": [asdict(link) for link in sorted(set(groups[key]))],
        }
        for key in sorted(groups, key=lambda item: expression_indices[item])
    ]
    if len(movement_rows) != 8 or len(group_rows) != 6 or len(physical_owners) != 8:
        raise Hamburg2394TlsTopologyError(
            "hard acceptance failed: expected 8 movements, 6 expressions and 8 stop-line links"
        )

    source_hash_after = _sha256_file(source_net_file)
    if source_hash_after != source_hash_before:
        raise Hamburg2394TlsTopologyError(
            "source network changed while the read-only topology plan was being compiled"
        )
    return {
        "schema_id": SCHEMA_ID,
        "status": "topology_binding_ready",
        "claim_status": "official-static-topology-review-plan",
        "automatic_promotion_gate": "blocked",
        "controller": {
            "tls_id": CONTROLLER_ID,
            "controller_count": 1,
            "signal_owner_ids": list(SIGNAL_OWNER_IDS),
            "signal_owner_count": 3,
            "passive_priority_owner_ids": list(PASSIVE_OWNER_IDS),
            "passive_priority_owner_count": 2,
            "technical_subnode_ids": list(controller_evidence["technical_subnode_ids"]),
        },
        "hard_acceptance": {
            "vehicle_movement_count": len(movement_rows),
            "control_expression_count": len(group_rows),
            "controlled_physical_link_count": len(physical_owners),
            "signal_owner_count": len(observed_signal_owners),
            "controller_count": 1,
            "status": "pass",
        },
        "movement_bindings": movement_rows,
        "control_expression_bindings": group_rows,
        "routing_patch": {
            "owner_id": MAIN_OWNER_ID,
            "remove": [asdict(link) for link in ROUTING_REMOVALS],
            "add": [asdict(link) for link in ROUTING_REPAIRS],
            "resulting_main_owner_connection_count": len(patched_main),
            "resulting_main_owner_connections": [asdict(link) for link in sorted(patched_main)],
        },
        "ocit_structure_gate": phase_gate,
        "tld_metadata_gate": {
            "status": "pass_for_movement_group_crosscheck_only",
            "snapshot_stream_count": len(stream_by_connection),
            "historical_observation_cache_status": "not_provided",
        },
        "materialization": {
            "status": "not_run",
            "reason": "this function compiles a review plan and never mutates or rebuilds a network",
        },
        "operational_signal_timing": {
            "status": "blocked",
            "ocit_phase_images_are_historical_durations": False,
            "historical_two_hour_replay_status": "not_run",
            "reasons": [
                "OCIT phase Signalbild rows contain no phase durations for the selected historical window",
                "Saturday schedule selects vehicle-actuated programs and is not a second-by-second replay",
                "mixed primary/secondary movement expressions require an explicit state-composition rule",
            ],
        },
        "source_net_file": str(source_net_file),
        "source_net_sha256": source_hash_after,
        "source_net_unchanged": True,
    }


def _validate_map_inventory(
    map_lanes: Sequence[MapLane],
    map_connections: Sequence[MapConnection],
    inventory: OcitVehicleTopologyInventory,
    observed_streams: Sequence[SignalStream],
) -> None:
    vehicle_lane_ids = {
        lane.lane_id
        for lane in map_lanes
        if _normalize_node(lane.node_id) == "2394" and lane.is_vehicle
    }
    if vehicle_lane_ids != set(EXPECTED_MAP_LANE_TO_SUMO_LANE):
        raise Hamburg2394TlsTopologyError(
            f"official vehicle MAP lane inventory changed: {sorted(vehicle_lane_ids)}"
        )
    connection_pairs = {
        connection.connection_id: (connection.ingress_lane_id, connection.egress_lane_id)
        for connection in map_connections
        if _normalize_node(connection.node_id) == "2394"
    }
    if len(connection_pairs) != len(
        [connection for connection in map_connections if _normalize_node(connection.node_id) == "2394"]
    ):
        raise Hamburg2394TlsTopologyError("official MAP connection IDs are not unique")
    if (
        inventory.status != "pass"
        or inventory.source_movement_count != 9
        or inventory.excluded_non_vehicle_movement_count != 1
        or inventory.movement_count != 8
    ):
        raise Hamburg2394TlsTopologyError(
            "OCIT/MAP inventory must contain 9 source movements, 1 non-vehicle exclusion and 8 vehicle movements"
        )
    movement_by_id = {movement.connection_id: movement for movement in inventory.movements}
    if set(movement_by_id) != set(EXPECTED_MOVEMENTS):
        raise Hamburg2394TlsTopologyError(
            f"official vehicle connection inventory changed: {sorted(movement_by_id)}"
        )
    stream_by_connection: dict[str, SignalStream] = {}
    for stream in observed_streams:
        if _normalize_node(stream.node_id) != "2394" or stream.layer_name != "primary_signal":
            continue
        if stream.connection_id in stream_by_connection:
            raise Hamburg2394TlsTopologyError(
                f"duplicate 2394 TLD metadata stream for connection {stream.connection_id}"
            )
        stream_by_connection[stream.connection_id] = stream
    if set(stream_by_connection) != set(EXPECTED_MOVEMENTS):
        raise Hamburg2394TlsTopologyError(
            f"TLD metadata does not cover all eight vehicle movements: {sorted(stream_by_connection)}"
        )
    for connection_id, expected in EXPECTED_MOVEMENTS.items():
        ingress, egress, expression, tld_group = expected
        movement = movement_by_id[connection_id]
        observed = stream_by_connection[connection_id]
        actual = (
            movement.ingress_lane_id,
            movement.egress_lane_id,
            movement.topology_control_key,
            observed.signal_group,
        )
        if actual != expected:
            raise Hamburg2394TlsTopologyError(
                f"official movement {connection_id} changed: expected={expected}, actual={actual}"
            )
        if connection_pairs.get(connection_id) != (ingress, egress):
            raise Hamburg2394TlsTopologyError(
                f"standalone MAP contradicts movement {connection_id}: {connection_pairs.get(connection_id)}"
            )


def _validate_lane_bindings(lane_bindings: Sequence[MapLaneBinding]) -> None:
    rows: dict[str, MapLaneBinding] = {}
    for binding in lane_bindings:
        if _normalize_node(binding.node_id) != "2394":
            continue
        if binding.map_lane_id in rows:
            raise Hamburg2394TlsTopologyError(
                f"duplicate final MAP lane binding for {binding.map_lane_id}"
            )
        rows[binding.map_lane_id] = binding
    if set(rows) != set(EXPECTED_MAP_LANE_TO_SUMO_LANE):
        raise Hamburg2394TlsTopologyError(
            f"final MAP lane binding coverage changed: {sorted(rows)}"
        )
    for lane_id, expected_sumo_lane in EXPECTED_MAP_LANE_TO_SUMO_LANE.items():
        row = rows[lane_id]
        if (
            row.sumo_lane != expected_sumo_lane
            or row.mapping_status != "active"
            or row.mapping_confidence != "high"
        ):
            raise Hamburg2394TlsTopologyError(
                f"MAP lane {lane_id} must bind high/active to {expected_sumo_lane}; got {row}"
            )


def _validate_ocit_gate(
    ocit: OcitCConfig, controller_evidence: Mapping[str, Any]
) -> dict[str, Any]:
    groups = {group.group_id: group for group in ocit.motor_signal_groups}
    expected_groups = {f"K{index}" for index in range(1, 8)}
    if set(groups) != expected_groups:
        raise Hamburg2394TlsTopologyError(f"OCIT motor groups changed: {sorted(groups)}")
    if {group.ocit_outstation_number for group in groups.values()} != {
        str(index) for index in range(1, 8)
    }:
        raise Hamburg2394TlsTopologyError("OCIT K1..K7 outstation numbers are not one-to-one")
    if (
        controller_evidence.get("controller_domain_count") != 1
        or controller_evidence.get("controller_domain_ids") != ["2394"]
        or controller_evidence.get("technical_subnode_ids") != ["1"]
    ):
        raise Hamburg2394TlsTopologyError(
            f"OCIT must prove one 2394 controller domain and TK1: {dict(controller_evidence)}"
        )
    phase_rows: dict[str, dict[str, str]] = {}
    for phase in ocit.phases:
        if phase.name in phase_rows:
            raise Hamburg2394TlsTopologyError(f"duplicate OCIT phase name {phase.name!r}")
        row: dict[str, str] = {}
        for signal in phase.group_signals:
            if signal.group_id not in expected_groups:
                continue
            if signal.group_id in row:
                raise Hamburg2394TlsTopologyError(
                    f"phase {phase.name} duplicates motor group {signal.group_id}"
                )
            row[signal.group_id] = signal.signal_image
        phase_rows[phase.name] = row
    if phase_rows != EXPECTED_PHASE_IMAGES:
        raise Hamburg2394TlsTopologyError(
            f"OCIT K1..K7 phase images changed: {phase_rows}"
        )
    if not ocit.has_vehicle_actuated_control or not ocit.saturday_vehicle_actuated:
        raise Hamburg2394TlsTopologyError(
            "2394 official OCIT evidence is expected to declare vehicle-actuated Saturday control"
        )
    return {
        "status": "pass_for_static_group_and_phase_structure_only",
        "controller_domain_count": 1,
        "technical_subnode_ids": ["1"],
        "motor_group_ids": sorted(groups, key=lambda value: int(value[1:])),
        "phase_images": EXPECTED_PHASE_IMAGES,
        "phase_duration_evidence": "absent",
        "saturday_vehicle_actuated": True,
        "operational_timing_authorization": "blocked",
    }


def _edge_and_lane_index(
    root: ET.Element,
) -> tuple[dict[str, tuple[str, str]], set[str]]:
    edges: dict[str, tuple[str, str]] = {}
    lane_ids: set[str] = set()
    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.attrib.get("function"):
            continue
        if edge_id in edges:
            raise Hamburg2394TlsTopologyError(f"duplicate external edge {edge_id}")
        edges[edge_id] = (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
        for lane in edge.findall("lane"):
            lane_id = lane.attrib.get("id", "")
            if lane_id in lane_ids:
                raise Hamburg2394TlsTopologyError(f"duplicate external lane {lane_id}")
            lane_ids.add(lane_id)
    return edges, lane_ids


def _external_links(root: ET.Element) -> tuple[PhysicalLink, ...]:
    links: list[PhysicalLink] = []
    keys: set[tuple[str, int, str, int]] = set()
    for connection in root.findall("connection"):
        if connection.attrib.get("from", "").startswith(":"):
            continue
        link = PhysicalLink(
            connection.attrib["from"],
            int(connection.attrib.get("fromLane", "0")),
            connection.attrib["to"],
            int(connection.attrib.get("toLane", "0")),
        )
        if link.key in keys:
            raise Hamburg2394TlsTopologyError(f"duplicate external connection {link}")
        keys.add(link.key)
        links.append(link)
    return tuple(links)


def _connection_owner(
    link: PhysicalLink, edges: Mapping[str, tuple[str, str]]
) -> str:
    source = edges.get(link.from_edge)
    target = edges.get(link.to_edge)
    if source is None or target is None:
        raise Hamburg2394TlsTopologyError(f"connection references an unknown external edge: {link}")
    if not source[1] or source[1] != target[0]:
        raise Hamburg2394TlsTopologyError(
            f"connection endpoints do not share one owner junction: {link}"
        )
    return source[1]


def _find_paths(
    links: set[PhysicalLink],
    start_lane_id: str,
    target_lane_id: str,
    *,
    max_hops: int,
    max_paths: int,
) -> tuple[list[list[PhysicalLink]], bool]:
    graph: dict[str, list[PhysicalLink]] = defaultdict(list)
    for link in sorted(links):
        graph[link.from_lane_id].append(link)
    paths: list[list[PhysicalLink]] = []
    overflow = False

    def visit(current: str, path: list[PhysicalLink], seen: set[str]) -> None:
        nonlocal overflow
        if overflow:
            return
        if current == target_lane_id:
            paths.append(list(path))
            if len(paths) >= max_paths:
                overflow = True
            return
        if len(path) >= max_hops:
            return
        for link in graph.get(current, ()):
            if link.to_lane_id in seen:
                continue
            visit(link.to_lane_id, [*path, link], {*seen, link.to_lane_id})

    visit(start_lane_id, [], {start_lane_id})
    return paths, overflow


def _read_classification(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Hamburg2394TlsTopologyError(f"cannot read classification: {exc}") from exc
    if not isinstance(value, dict):
        raise Hamburg2394TlsTopologyError("classification root must be a JSON object")
    return value


def _validate_classification(value: Mapping[str, Any], accepted_id: str) -> None:
    expected = {
        "schema_id": "torii.composable-intersection-archetype/v2",
        "junction_id": "2394",
        "prototype_id": PROTOTYPE_ID,
        "classification_id": accepted_id,
        "status": "review_required",
        "automatic_promotion_gate": "blocked",
    }
    mismatches = {
        key: {"expected": wanted, "actual": value.get(key)}
        for key, wanted in expected.items()
        if value.get(key) != wanted
    }
    counts = value.get("counts")
    if not isinstance(counts, Mapping):
        mismatches["counts"] = {"expected": "object", "actual": counts}
    else:
        for key, wanted in (
            ("owner_count_after_rebuild_candidate", 5),
            ("controller_domain_count", 1),
        ):
            if counts.get(key) != wanted:
                mismatches[f"counts.{key}"] = {
                    "expected": wanted,
                    "actual": counts.get(key),
                }
    classification = value.get("classification")
    if not isinstance(classification, Mapping) or classification.get("control_domain") != (
        "multi_owner_single_controller_candidate"
    ):
        mismatches["classification.control_domain"] = {
            "expected": "multi_owner_single_controller_candidate",
            "actual": classification.get("control_domain") if isinstance(classification, Mapping) else None,
        }
    if mismatches:
        raise Hamburg2394TlsTopologyError(f"classification acceptance gate failed: {mismatches}")


def _validated_sha256(value: str, name: str) -> str:
    result = str(value).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", result):
        raise Hamburg2394TlsTopologyError(f"{name} expected SHA-256 must be 64 hex characters")
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_node(value: str) -> str:
    text = str(value).strip()
    stripped = text.lstrip("0")
    return stripped or "0"
