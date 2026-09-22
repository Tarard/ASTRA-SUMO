from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .digital_twin import MapConnection, MapLane, SignalStream


SATURDAY_PLAN_SEMANTICS = (
    "Saturday timetable entries are program-selection references only; they do not establish a "
    "fixed second-by-second signal replay, especially when VA/actuated control is enabled."
)

_MOTOR_GROUP_RE = re.compile(r"^K0*(\d+)([A-Z]*)$", re.IGNORECASE)


@dataclass(frozen=True)
class OcitMotorSignalGroup:
    group_id: str
    ocit_outstation_number: str
    signal_heads: tuple[str, ...]


@dataclass(frozen=True)
class OcitVehicleMovement:
    """One lane-to-lane vehicle movement from the OCIT-C embedded MAP.

    ``unavailable`` applies only to the ``signalGroups`` choice.  An
    ``unavailable`` marker below ``staticRegulations/sign`` means that static
    sign information is unavailable and does not invalidate the movement's
    signal-group evidence.
    """

    node_id: str
    ingress_lane_id: str
    egress_lane_id: str
    primary_motor_groups: tuple[str, ...]
    secondary_motor_groups: tuple[str, ...]
    unavailable: bool
    unmapped_primary_vt: tuple[str, ...] = ()
    unmapped_secondary_vt: tuple[str, ...] = ()
    non_motor_only: bool = False


@dataclass(frozen=True)
class OcitVehicleTopologyMovement:
    """One complete official vehicle movement prepared for SUMO TLS topology.

    Hamburg's OCIT-C MAP embeds both ``primary`` and ``secondary`` motor-group
    references.  They are preserved as separate control evidence: neither role
    is silently discarded or promoted.  SUMO topology uses one stable key per
    distinct primary/secondary expression.  Movements may share it only when
    that complete expression is identical, while signal replay must resolve
    its state separately.
    """

    node_id: str
    connection_id: str
    ingress_lane_id: str
    egress_lane_id: str
    map_signal_group: str
    primary_motor_groups: tuple[str, ...]
    secondary_motor_groups: tuple[str, ...]
    topology_control_key: str
    observed_stream_ids: tuple[int, ...]
    observed_signal_groups: tuple[str, ...]


@dataclass(frozen=True)
class OcitVehicleTopologyInventory:
    status: str
    source_movement_count: int
    excluded_non_vehicle_movement_count: int
    movement_count: int
    observed_stream_count: int
    observed_match_count: int
    group_resolution_policy: str
    movements: tuple[OcitVehicleTopologyMovement, ...]
    topology_streams: tuple[SignalStream, ...]
    excluded_non_motor_movement_count: int = 0
    excluded_non_motor_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class OcitGroupSignal:
    group_id: str
    signal_image: str


@dataclass(frozen=True)
class OcitPhase:
    name: str
    ocit_outstation_number: str
    group_signals: tuple[OcitGroupSignal, ...]


@dataclass(frozen=True)
class OcitSaturdayCommand:
    time: str
    program_id: str
    junction_enabled: str
    va_enabled: bool | None


@dataclass(frozen=True)
class OcitSaturdayPlan:
    name: str
    long_name: str
    ocit_outstation_number: str
    commands: tuple[OcitSaturdayCommand, ...]


@dataclass(frozen=True)
class OcitCConfig:
    node_id: str
    node_name: str
    motor_signal_groups: tuple[OcitMotorSignalGroup, ...]
    phases: tuple[OcitPhase, ...]
    signal_program_ids: tuple[str, ...]
    saturday_plans: tuple[OcitSaturdayPlan, ...]
    has_vehicle_actuated_control: bool
    saturday_vehicle_actuated: bool
    saturday_plan_semantics: str
    source_path: str
    vehicle_movements: tuple[OcitVehicleMovement, ...] = ()

    @property
    def motor_group_ids(self) -> tuple[str, ...]:
        return tuple(group.group_id for group in self.motor_signal_groups)

    @property
    def saturday_program_ids(self) -> tuple[str, ...]:
        return tuple(
            command.program_id
            for plan in self.saturday_plans
            for command in plan.commands
            if command.program_id
        )


@dataclass(frozen=True)
class PrimarySignalGroupValidation:
    status: str
    primary_stream_count: int
    checked_group_count: int
    checked_groups: tuple[str, ...]


VEHICLE_TOPOLOGY_GROUP_POLICY = (
    "one_sumo_link_index_per_distinct_primary_secondary_control_expression"
)


def build_vehicle_topology_inventory(
    ocit_configs: Sequence[OcitCConfig],
    map_lanes: Sequence[MapLane],
    map_connections: Sequence[MapConnection],
    observed_streams: Sequence[SignalStream],
) -> OcitVehicleTopologyInventory:
    """Build the complete vehicle topology from OCIT-C and cross-check TLD metadata.

    The official TLD stream snapshot is deliberately *not* treated as a complete
    topology inventory: a service response may omit connections.  OCIT-C supplies
    the complete lane-to-lane movement list, the public MAP supplies the stable
    connection id, and any available TLD ``primary_signal`` streams are used only
    to validate that an observed K group is one of the movement's declared
    primary/secondary references.
    """

    config_by_node: dict[str, OcitCConfig] = {}
    for config in ocit_configs:
        node_id = _normalize_node(config.node_id)
        if node_id in config_by_node:
            raise ValueError(f"multiple OCIT-C configurations supplied for node {config.node_id}")
        config_by_node[node_id] = config
    if not config_by_node:
        raise ValueError("at least one OCIT-C configuration is required")

    map_lane_index: dict[tuple[str, str], list[MapLane]] = {}
    for lane in map_lanes:
        map_lane_index.setdefault(
            (_normalize_node(lane.node_id), lane.lane_id), []
        ).append(lane)
    map_index: dict[tuple[str, str, str], list[MapConnection]] = {}
    for connection in map_connections:
        key = (
            _normalize_node(connection.node_id),
            connection.ingress_lane_id,
            connection.egress_lane_id,
        )
        map_index.setdefault(key, []).append(connection)

    observed_index: dict[tuple[str, str, str], list[SignalStream]] = {}
    primary_observed = [
        stream for stream in observed_streams if stream.layer_name == "primary_signal"
    ]
    if len({stream.stream_id for stream in primary_observed}) != len(primary_observed):
        raise ValueError("observed primary signal stream ids must be unique")
    for stream in primary_observed:
        key = (
            _normalize_node(stream.node_id),
            stream.ingress_lane_id,
            stream.egress_lane_id,
        )
        observed_index.setdefault(key, []).append(stream)

    topology_rows: list[
        tuple[str, MapConnection, OcitVehicleMovement, tuple[SignalStream, ...]]
    ] = []
    covered_observed_ids: set[int] = set()
    source_movement_count = 0
    excluded_non_vehicle_movement_count = 0
    excluded_non_motor_movement_count = 0
    excluded_non_motor_pairs: list[tuple[str, str]] = []
    for node_id, config in sorted(config_by_node.items()):
        seen_pairs: set[tuple[str, str]] = set()
        for movement in config.vehicle_movements:
            source_movement_count += 1
            pair = (movement.ingress_lane_id, movement.egress_lane_id)
            if pair in seen_pairs:
                raise ValueError(
                    f"OCIT-C node {config.node_id} contains duplicate vehicle movement "
                    f"{pair[0]}->{pair[1]}"
                )
            seen_pairs.add(pair)
            if movement.non_motor_only:
                excluded_non_motor_movement_count += 1
                excluded_non_motor_pairs.append(pair)
                continue
            lane_rows = [
                map_lane_index.get((node_id, movement.ingress_lane_id), []),
                map_lane_index.get((node_id, movement.egress_lane_id), []),
            ]
            if any(len(rows) != 1 for rows in lane_rows):
                raise ValueError(
                    f"official MAP does not uniquely define both lanes for OCIT-C movement "
                    f"{config.node_id}/{pair[0]}->{pair[1]}"
                )
            if not all(rows[0].is_vehicle for rows in lane_rows):
                excluded_non_vehicle_movement_count += 1
                continue
            if movement.unavailable:
                raise ValueError(
                    f"OCIT-C node {config.node_id} movement {pair[0]}->{pair[1]} marks its "
                    "signal-group choice unavailable"
                )
            referenced_groups = {
                *movement.primary_motor_groups,
                *movement.secondary_motor_groups,
            }
            if not referenced_groups:
                raise ValueError(
                    f"OCIT-C node {config.node_id} movement {pair[0]}->{pair[1]} has no "
                    "primary or secondary motor signal group"
                )
            key = (node_id, *pair)
            map_rows = map_index.get(key, [])
            if len(map_rows) != 1:
                raise ValueError(
                    f"official MAP has {len(map_rows)} connections for OCIT-C vehicle movement "
                    f"{config.node_id}/{pair[0]}->{pair[1]}; exactly one is required"
                )
            observed_rows = tuple(
                sorted(observed_index.get(key, ()), key=lambda stream: stream.stream_id)
            )
            mismatched = [
                stream
                for stream in observed_rows
                if _normalize_motor_group(stream.signal_group) not in referenced_groups
            ]
            if mismatched:
                details = ", ".join(
                    f"{stream.stream_id}:{stream.signal_group}" for stream in mismatched
                )
                raise ValueError(
                    f"official TLD metadata contradicts the OCIT-C motor-group references for "
                    f"{config.node_id}/{pair[0]}->{pair[1]}: expected one of "
                    f"{sorted(referenced_groups)}, observed {details}"
                )
            covered_observed_ids.update(stream.stream_id for stream in observed_rows)
            topology_rows.append(
                (node_id, map_rows[0], movement, observed_rows)
            )

    primary_observed_ids = {stream.stream_id for stream in primary_observed}
    unmatched_observed = sorted(primary_observed_ids - covered_observed_ids)
    if unmatched_observed:
        raise ValueError(
            "official TLD metadata contains primary vehicle streams absent from the OCIT-C "
            f"movement inventory: {unmatched_observed}"
        )

    topology_rows.sort(
        key=lambda row: (
            row[0],
            _natural_key(row[1].connection_id),
            _natural_key(row[2].ingress_lane_id),
            _natural_key(row[2].egress_lane_id),
        )
    )
    movements: list[OcitVehicleTopologyMovement] = []
    topology_streams: list[SignalStream] = []
    for index, (_node_key, connection, movement, observed_rows) in enumerate(
        topology_rows,
        start=1,
    ):
        topology_control_key = _movement_control_key(movement)
        movements.append(
            OcitVehicleTopologyMovement(
                node_id=movement.node_id,
                connection_id=connection.connection_id,
                ingress_lane_id=movement.ingress_lane_id,
                egress_lane_id=movement.egress_lane_id,
                map_signal_group=connection.signal_group,
                primary_motor_groups=movement.primary_motor_groups,
                secondary_motor_groups=movement.secondary_motor_groups,
                topology_control_key=topology_control_key,
                observed_stream_ids=tuple(stream.stream_id for stream in observed_rows),
                observed_signal_groups=tuple(
                    sorted({_normalize_motor_group(stream.signal_group) or "" for stream in observed_rows})
                ),
            )
        )
        topology_streams.append(
            SignalStream(
                stream_id=-index,
                thing_id=None,
                node_id=movement.node_id,
                connection_id=connection.connection_id,
                ingress_lane_id=movement.ingress_lane_id,
                egress_lane_id=movement.egress_lane_id,
                lane_type="KFZ",
                signal_group=topology_control_key,
                layer_name="primary_signal",
                name=(
                    f"OCIT-C vehicle topology {movement.node_id}/"
                    f"{movement.ingress_lane_id}->{movement.egress_lane_id}"
                ),
            )
        )

    return OcitVehicleTopologyInventory(
        status="pass",
        source_movement_count=source_movement_count,
        excluded_non_vehicle_movement_count=excluded_non_vehicle_movement_count,
        movement_count=len(movements),
        observed_stream_count=len(primary_observed),
        observed_match_count=len(covered_observed_ids),
        group_resolution_policy=VEHICLE_TOPOLOGY_GROUP_POLICY,
        movements=tuple(movements),
        topology_streams=tuple(topology_streams),
        excluded_non_motor_movement_count=excluded_non_motor_movement_count,
        excluded_non_motor_pairs=tuple(
            sorted(
                set(excluded_non_motor_pairs),
                key=lambda pair: (_natural_key(pair[0]), _natural_key(pair[1])),
            )
        ),
    )


def topology_control_index_by_node(
    inventory: OcitVehicleTopologyInventory,
) -> dict[str, dict[str, int]]:
    """Return deterministic per-node SUMO link indices for official movements."""

    grouped: dict[str, set[str]] = {}
    for movement in inventory.movements:
        grouped.setdefault(_normalize_node(movement.node_id), set()).add(
            movement.topology_control_key
        )
    result: dict[str, dict[str, int]] = {}
    for node_id, keys in sorted(grouped.items()):
        result[node_id] = {key: index for index, key in enumerate(sorted(keys))}
    return result


def _movement_control_key(movement: OcitVehicleMovement) -> str:
    primary = "+".join(movement.primary_motor_groups) or "NONE"
    secondary = "+".join(movement.secondary_motor_groups) or "NONE"
    return f"P_{primary}__S_{secondary}"


def parse_ocit_c(
    path: Path,
    *,
    strict_movement_vt: bool = True,
    ignore_non_motor_vt: bool = False,
) -> OcitCConfig:
    root = ET.parse(path).getroot()
    header = next(_descendants(root, "Kopfdaten"), None)
    if header is None:
        raise ValueError(f"OCIT-C file has no Kopfdaten: {path}")
    node_id = _text(_child(header, "Kurzbezeichnung"))
    node_name = _text(_child(header, "Name"))
    if not node_id:
        raise ValueError(f"OCIT-C file has no node identifier: {path}")

    groups: dict[str, dict[str, object]] = {}
    non_motor_vt: set[str] = set()
    for element in _descendants(root, "Signalgruppe"):
        traffic_type = _text(_child(element, "Verkehrsart"))
        raw_group_id = _text(_child(element, "BezeichnungKurz"))
        group_id = _normalize_motor_group(raw_group_id)
        outstation = _text(_child(element, "OCITOutstationNr"))
        if outstation and (traffic_type.casefold() != "kfz" or group_id is None):
            non_motor_vt.add(outstation)
        if traffic_type.casefold() != "kfz" or group_id is None:
            continue
        signal_heads = {
            head
            for lamp in _descendants(element, "Lampe")
            if (head := _text(_child(lamp, "Bezeichnung")))
        }
        existing = groups.setdefault(
            group_id,
            {"outstation": outstation, "heads": set()},
        )
        previous_outstation = str(existing["outstation"])
        if previous_outstation and outstation and previous_outstation != outstation:
            raise ValueError(
                f"OCIT-C node {node_id} defines {group_id} with conflicting outstation numbers"
            )
        if not previous_outstation:
            existing["outstation"] = outstation
        heads = existing["heads"]
        if isinstance(heads, set):
            heads.update(signal_heads)

    for signal_head in _descendants(root, "Signalgeber"):
        group_id = _normalize_motor_group(_text(_child(signal_head, "SgrBezeichnung")))
        head_name = _text(_child(signal_head, "BezeichnungKurz"))
        if group_id not in groups or not head_name:
            continue
        heads = groups[group_id]["heads"]
        if isinstance(heads, set):
            heads.add(head_name)

    motor_signal_groups = tuple(
        OcitMotorSignalGroup(
            group_id=group_id,
            ocit_outstation_number=str(values["outstation"]),
            signal_heads=tuple(sorted(str(item) for item in values["heads"])),
        )
        for group_id, values in sorted(groups.items(), key=lambda item: _group_sort_key(item[0]))
    )
    # A few Hamburg OCIT exports reference bicycle, pedestrian, bus, or
    # auxiliary signal groups from the same embedded MAP traffic-stream list.
    # Their outstation numbers are valid VT references, but they are not
    # passenger-vehicle signal groups and must not be reported as missing
    # motor control evidence.  Keep unknown VT numbers strict; only ignore
    # explicitly identified non-motor groups when the caller opts in.
    motor_vt = {group.ocit_outstation_number for group in motor_signal_groups}
    non_motor_vt.difference_update(motor_vt)
    vehicle_movements = _parse_vehicle_movements(
        root,
        node_id=node_id,
        motor_signal_groups=motor_signal_groups,
        strict_movement_vt=strict_movement_vt,
        non_motor_vt=non_motor_vt if ignore_non_motor_vt else frozenset(),
        ignore_non_motor_vt=ignore_non_motor_vt,
    )

    phases = tuple(_parse_phase(element) for element in _descendants(root, "Phase"))
    signal_program_ids = tuple(
        sorted(
            {
                program_id
                for element in _descendants(root, "Signalprogramm")
                if (program_id := _text(_child(element, "BezeichnungKurz")))
            },
            key=_natural_key,
        )
    )
    saturday_plans = tuple(
        _parse_saturday_plan(element)
        for element in _schedule_elements(root)
        if _is_saturday_name(_text(_child(element, "BezeichnungKurz")))
    )
    saturday_commands = [command for plan in saturday_plans for command in plan.commands]
    has_va_configuration = any(True for _ in _descendants(root, "VASteuerverfahren"))
    has_vehicle_actuated_control = has_va_configuration or any(
        command.va_enabled is True for command in saturday_commands
    )
    saturday_vehicle_actuated = any(command.va_enabled is True for command in saturday_commands)

    return OcitCConfig(
        node_id=node_id,
        node_name=node_name,
        motor_signal_groups=motor_signal_groups,
        phases=phases,
        signal_program_ids=signal_program_ids,
        saturday_plans=saturday_plans,
        has_vehicle_actuated_control=has_vehicle_actuated_control,
        saturday_vehicle_actuated=saturday_vehicle_actuated,
        saturday_plan_semantics=SATURDAY_PLAN_SEMANTICS,
        source_path=str(path),
        vehicle_movements=vehicle_movements,
    )


def validate_primary_signal_groups(
    streams: Sequence[SignalStream],
    ocit_configs: Sequence[OcitCConfig],
) -> PrimarySignalGroupValidation:
    config_by_node: dict[str, OcitCConfig] = {}
    for config in ocit_configs:
        node_key = _normalize_node(config.node_id)
        if node_key in config_by_node:
            raise ValueError(f"multiple OCIT-C configurations supplied for node {config.node_id}")
        config_by_node[node_key] = config

    checked: set[tuple[str, str]] = set()
    primary_stream_count = 0
    missing: list[str] = []
    for stream in streams:
        if stream.layer_name != "primary_signal":
            continue
        group_id = _normalize_motor_group(stream.signal_group)
        if group_id is None:
            continue
        primary_stream_count += 1
        node_key = _normalize_node(stream.node_id)
        checked.add((node_key, group_id))
        config = config_by_node.get(node_key)
        if config is None:
            missing.append(f"node {stream.node_id} has no OCIT-C configuration (stream {stream.stream_id})")
            continue
        if group_id not in set(config.motor_group_ids):
            missing.append(
                f"node {stream.node_id} OCIT-C has no motor signal group {group_id} "
                f"(stream {stream.stream_id})"
            )
    if missing:
        raise ValueError("primary signal group validation failed: " + "; ".join(sorted(missing)))
    checked_groups = tuple(f"{node}/{group}" for node, group in sorted(checked))
    return PrimarySignalGroupValidation(
        status="pass",
        primary_stream_count=primary_stream_count,
        checked_group_count=len(checked),
        checked_groups=checked_groups,
    )


def _parse_phase(element: ET.Element) -> OcitPhase:
    group_signals = tuple(
        OcitGroupSignal(
            group_id=_normalize_group_label(_text(_child(entry, "Signalgruppe"))),
            signal_image=_text(_child(entry, "Signalbild")),
        )
        for entry in _children(element, "PhasenElementeintrag")
        if _text(_child(entry, "Signalgruppe"))
    )
    return OcitPhase(
        name=_text(_child(element, "BezeichnungKurz")),
        ocit_outstation_number=_text(_child(element, "OCITOutstationNr")),
        group_signals=group_signals,
    )


def _parse_vehicle_movements(
    root: ET.Element,
    *,
    node_id: str,
    motor_signal_groups: Sequence[OcitMotorSignalGroup],
    strict_movement_vt: bool,
    non_motor_vt: Sequence[str] = (),
    ignore_non_motor_vt: bool = False,
) -> tuple[OcitVehicleMovement, ...]:
    group_by_vt: dict[str, str] = {}
    for group in motor_signal_groups:
        vt = group.ocit_outstation_number.strip()
        if not vt:
            continue
        previous = group_by_vt.get(vt)
        if previous is not None and previous != group.group_id:
            raise ValueError(
                f"OCIT-C node {node_id} maps vt {vt} to multiple motor groups: "
                f"{previous} and {group.group_id}"
            )
        group_by_vt[vt] = group.group_id

    movements: list[OcitVehicleMovement] = []
    for element in _descendants(root, "TrafficStreamConfigData"):
        ingress_lane_id = _text(_child(element, "refLaneId"))
        egress_lane_id = _text(_child(element, "refConnectTo"))
        if not ingress_lane_id or not egress_lane_id:
            raise ValueError(
                f"OCIT-C node {node_id} has a TrafficStreamConfigData record without "
                "refLaneId/refConnectTo"
            )

        signal_groups = _child(element, "signalGroups")
        primary_vt = _vt_references(_child(signal_groups, "primary"))
        secondary_vt = _vt_references(_child(signal_groups, "secondary"))
        unavailable = signal_groups is not None and any(
            True for _ in _descendants(signal_groups, "unavailable")
        )

        primary_groups, unmapped_primary = _map_motor_vt(
            primary_vt,
            group_by_vt,
            non_motor_vt=non_motor_vt,
        )
        secondary_groups, unmapped_secondary = _map_motor_vt(
            secondary_vt,
            group_by_vt,
            non_motor_vt=non_motor_vt,
        )
        if strict_movement_vt and (unmapped_primary or unmapped_secondary):
            details: list[str] = []
            if unmapped_primary:
                details.append(f"primary vt {', '.join(unmapped_primary)}")
            if unmapped_secondary:
                details.append(f"secondary vt {', '.join(unmapped_secondary)}")
            raise ValueError(
                f"OCIT-C node {node_id} movement {ingress_lane_id}->{egress_lane_id} has "
                f"unmapped motor signal reference(s): {'; '.join(details)}"
            )

        # TrafficStreamConfigData is a broad MAP container.  Keep vehicle
        # movements (including an explicitly unavailable signal-group choice),
        # but do not turn unrelated records without a vt reference into motor
        # movements.
        if not (primary_vt or secondary_vt or unavailable):
            continue
        if ignore_non_motor_vt and not (
            primary_groups or secondary_groups or unmapped_primary or unmapped_secondary or unavailable
        ):
            # This traffic stream is explicitly controlled only by a known
            # non-motor group (bike, pedestrian, bus, or auxiliary).  It is
            # retained in the source XML but marked outside the passenger-
            # vehicle topology inventory so the MAP connection can be audited.
            movements.append(
                OcitVehicleMovement(
                    node_id=node_id,
                    ingress_lane_id=ingress_lane_id,
                    egress_lane_id=egress_lane_id,
                    primary_motor_groups=(),
                    secondary_motor_groups=(),
                    unavailable=False,
                    non_motor_only=True,
                )
            )
            continue
        movements.append(
            OcitVehicleMovement(
                node_id=node_id,
                ingress_lane_id=ingress_lane_id,
                egress_lane_id=egress_lane_id,
                primary_motor_groups=primary_groups,
                secondary_motor_groups=secondary_groups,
                unavailable=unavailable,
                unmapped_primary_vt=unmapped_primary,
                unmapped_secondary_vt=unmapped_secondary,
            )
        )
    return tuple(movements)


def _vt_references(container: ET.Element | None) -> tuple[str, ...]:
    if container is None:
        return ()
    return tuple(
        vt
        for element in _descendants(container, "vt")
        if (vt := _text(element))
    )


def _map_motor_vt(
    vt_references: Sequence[str],
    group_by_vt: dict[str, str],
    *,
    non_motor_vt: Sequence[str] = (),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    groups: list[str] = []
    unmapped: list[str] = []
    non_motor = set(non_motor_vt)
    for vt in vt_references:
        group_id = group_by_vt.get(vt)
        if group_id is None:
            if vt in non_motor:
                continue
            unmapped.append(vt)
        else:
            groups.append(group_id)
    return tuple(groups), tuple(unmapped)


def _parse_saturday_plan(element: ET.Element) -> OcitSaturdayPlan:
    commands = tuple(
        OcitSaturdayCommand(
            time=_text(_child(command, "Uhrzeit")),
            program_id=_text(_child(command, "Programm")),
            junction_enabled=_text(_child(command, "KnotenEinAus")),
            va_enabled=_optional_bool(_text(_child(command, "VA"))),
        )
        for command in _children(element, "Befehl")
    )
    return OcitSaturdayPlan(
        name=_text(_child(element, "BezeichnungKurz")),
        long_name=_text(_child(element, "BezeichnungLang")),
        ocit_outstation_number=_text(_child(element, "OCITOutstationNr")),
        commands=commands,
    )


def _schedule_elements(root: ET.Element) -> Iterable[ET.Element]:
    for element in root.iter():
        if _local_name(element.tag) in {"Tagesplan", "StandardTagesplan"}:
            yield element


def _is_saturday_name(value: str) -> bool:
    normalized = re.sub(r"[^a-z]", "", value.casefold())
    return normalized in {"sa", "samstag"} or normalized.startswith("samstag")


def _normalize_motor_group(value: str) -> str | None:
    compact = re.sub(r"\s+", "", value).upper()
    match = _MOTOR_GROUP_RE.fullmatch(compact)
    if match is None:
        return None
    suffix = match.group(2).upper()
    return f"K{int(match.group(1))}{suffix}"


def _normalize_group_label(value: str) -> str:
    return _normalize_motor_group(value) or re.sub(r"\s+", "", value).upper()


def _normalize_node(value: str) -> str:
    stripped = value.strip()
    try:
        return str(int(stripped))
    except ValueError:
        return stripped.casefold()


def _optional_bool(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized in {"true", "1", "ja", "ein"}:
        return True
    if normalized in {"false", "0", "nein", "aus"}:
        return False
    raise ValueError(f"invalid OCIT-C boolean value: {value!r}")


def _group_sort_key(value: str) -> tuple[int, str]:
    match = _MOTOR_GROUP_RE.fullmatch(value)
    if match is None:
        return (10**9, value)
    return (int(match.group(1)), match.group(2))


def _natural_key(value: str) -> tuple[int, int | str, str]:
    try:
        return (0, int(value), "")
    except ValueError:
        return (1, value.casefold(), value)


def _descendants(root: ET.Element, name: str) -> Iterable[ET.Element]:
    for element in root.iter():
        if _local_name(element.tag) == name:
            yield element


def _children(parent: ET.Element, name: str) -> Iterable[ET.Element]:
    for child in parent:
        if _local_name(child.tag) == name:
            yield child


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    return next(_children(parent, name), None)


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
