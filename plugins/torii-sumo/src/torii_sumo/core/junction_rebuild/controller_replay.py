"""Materialize scoped and shared-controller teacher networks."""

from __future__ import annotations

import copy
from pathlib import Path
import xml.etree.ElementTree as ET
from .artifacts import _failure
from .boundary_restore import (
    _preserve_mapped_boundary_geometry,
    _remove_edge_lanes_from_destination_junction,
    _restore_external_boundary_connections,
)
from .edge_mapping import _teacher_boundary_edge_ids_touching_internal_subgraph
from .geometry import (
    _clone_transformed_boundary_edge,
    _clone_transformed_junction,
    _clone_transformed_net_element,
    _format_xy,
    _mapped_junction_attrs,
    _mapped_spatial_attrs,
    _translate_shape,
)
from .network import _connection_lane_indices_valid, _first_junction_index, _net_lane_counts, _split
from .target_replay import write_teacher_target_internal_replay_net


def write_scoped_teacher_tls_cell_replay_net(
    *,
    candidate_net_file: Path,
    teacher_net_file: Path,
    output_file: Path,
    junction_id: str,
    edge_map: dict[str, str],
    teacher_junction_id: str | None = None,
    collapse_junction_ids: set[str] | None = None,
    junction_map: dict[str, str] | None = None,
) -> dict[str, object]:
    """Replay one reference TLS cell while collapsing a split OSM junction group.

    ``write_teacher_target_internal_replay_net`` is intentionally permissive: it
    can copy teacher continuation edges so that a small synthetic example stays
    connected.  That is useful for diagnostics, but it is unsafe for a real
    split TLS cell because those copied continuations leave the old split graph
    beside the new teacher cell.  This wrapper adds an explicit cell boundary:
    boundary edges are mapped/reused, non-boundary edges touching the supplied
    candidate members are removed, and teacher endpoint junctions may be mapped
    back to existing candidate endpoints.  No inference is made when a mapping
    is absent; the teacher boundary is copied and remains visible in the
    returned report for review.
    """

    teacher_junction_id = teacher_junction_id or junction_id
    collapse_ids = {str(value) for value in (collapse_junction_ids or set()) if str(value)}
    collapse_ids.add(junction_id)
    ordinary_junction_map = {
        str(key): str(value)
        for key, value in (junction_map or {}).items()
        if str(key) and str(value)
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")
    if not teacher_net_file.exists():
        return _failure(f"teacher net file does not exist: {teacher_net_file}")

    # Keep the permissive writer as the well-tested semantic replay primitive;
    # the scoped cleanup below is the only layer that changes its boundary.
    # Keep the intermediate name deliberately short; the caller already
    # allocates one directory per candidate and Windows path limits apply
    # before the final artifact manifest is written.
    unscoped_file = output_file.parent / "unscoped.net.xml"
    unscoped_file.parent.mkdir(parents=True, exist_ok=True)
    replay_report = write_teacher_target_internal_replay_net(
        candidate_net_file=candidate_net_file,
        teacher_net_file=teacher_net_file,
        output_file=unscoped_file,
        junction_id=junction_id,
        teacher_junction_id=teacher_junction_id,
        edge_map=edge_map,
    )
    if replay_report.get("status") != "pass":
        return {
            **replay_report,
            "scoped_replay_status": "blocked",
            "scoped_replay_reason": "unscoped_teacher_replay_failed",
        }

    try:
        source_root = ET.parse(candidate_net_file).getroot()
        teacher_root = ET.parse(teacher_net_file).getroot()
        candidate_tree = ET.parse(unscoped_file)
        candidate_root = candidate_tree.getroot()
    except (ET.ParseError, OSError, KeyError, ValueError) as exc:
        return _failure(f"scoped TLS cell replay parse failed: {type(exc).__name__}: {exc}")

    target_candidate_junction = candidate_root.find(f"junction[@id='{junction_id}']")
    teacher_junction = teacher_root.find(f"junction[@id='{teacher_junction_id}']")
    if target_candidate_junction is None:
        return _failure(f"candidate junction not found: {junction_id}")
    if teacher_junction is None:
        return _failure(f"teacher junction not found: {teacher_junction_id}")

    teacher_edges = {
        edge.attrib["id"]: edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    source_edges = {
        edge.attrib["id"]: edge
        for edge in source_root.findall("edge")
        if edge.attrib.get("id")
    }
    source_junction_ids = {
        junction.attrib["id"]
        for junction in source_root.findall("junction")
        if junction.attrib.get("id")
    }
    teacher_boundary_edge_ids = _teacher_boundary_edge_ids_touching_internal_subgraph(
        teacher_root.findall("connection"),
        teacher_edges,
        teacher_junction_id,
    )
    effective_edge_map = {str(key): str(value) for key, value in edge_map.items()}
    for teacher_edge_id in teacher_boundary_edge_ids:
        effective_edge_map.setdefault(teacher_edge_id, teacher_edge_id)
    protected_edge_ids = {
        effective_edge_map.get(edge_id, edge_id)
        for edge_id in teacher_boundary_edge_ids
        if effective_edge_map.get(edge_id, edge_id)
    }
    old_member_ids = collapse_ids - {junction_id}
    old_member_prefixes = tuple(f":{member_id}_" for member_id in sorted(old_member_ids))

    target_x = float(target_candidate_junction.attrib.get("x", "0") or 0)
    target_y = float(target_candidate_junction.attrib.get("y", "0") or 0)
    teacher_x = float(teacher_junction.attrib.get("x", "0") or 0)
    teacher_y = float(teacher_junction.attrib.get("y", "0") or 0)
    dx = target_x - teacher_x
    dy = target_y - teacher_y

    def map_junction_id(value: str) -> str:
        if value == teacher_junction_id:
            return junction_id
        return ordinary_junction_map.get(value, value)

    def map_boundary_junction(teacher_endpoint_id: str) -> str:
        mapped_id = map_junction_id(teacher_endpoint_id)
        if mapped_id in source_junction_ids:
            return mapped_id
        if candidate_root.find(f"junction[@id='{mapped_id}']") is not None:
            return mapped_id
        teacher_endpoint = teacher_root.find(f"junction[@id='{teacher_endpoint_id}']")
        if teacher_endpoint is None:
            return mapped_id
        attrs = _mapped_spatial_attrs(
            teacher_endpoint.attrib,
            dx,
            dy,
            effective_edge_map,
            teacher_junction_id,
            junction_id,
        )
        for attr in ("id", "from", "to", "tl"):
            if attr in attrs:
                attrs[attr] = map_junction_id(attrs[attr])
        attrs["id"] = mapped_id
        attrs["incLanes"] = ""
        attrs["intLanes"] = ""
        candidate_root.insert(_first_junction_index(candidate_root), ET.Element("junction", attrs))
        return mapped_id

    def edge_lane_ids(edge: ET.Element) -> set[str]:
        return {
            lane.attrib["id"]
            for lane in edge.findall("lane")
            if lane.attrib.get("id")
        }

    def remove_lane_refs(lane_ids: set[str]) -> None:
        if not lane_ids:
            return
        for junction in candidate_root.findall("junction"):
            inc_lanes = _split(junction.attrib.get("incLanes", ""))
            if inc_lanes:
                junction.set("incLanes", " ".join(lane for lane in inc_lanes if lane not in lane_ids))

    def append_lane_refs(junction_id_value: str, lane_ids: set[str]) -> None:
        junction = candidate_root.find(f"junction[@id='{junction_id_value}']")
        if junction is None or not lane_ids:
            return
        inc_lanes = _split(junction.attrib.get("incLanes", ""))
        for lane_id in sorted(lane_ids):
            if lane_id not in inc_lanes:
                inc_lanes.append(lane_id)
        junction.set("incLanes", " ".join(inc_lanes))

    removed_edge_ids: set[str] = set()
    removed_non_boundary_edge_ids: list[str] = []
    removed_non_boundary_edges_added_by_replay: list[str] = []
    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    # First remove the old split road graph around the supplied physical cell.
    # Internal/crossing/walkingarea elements are handled separately so the
    # teacher cell can remain intact.
    for edge_id, edge in list(candidate_edges.items()):
        if edge_id in protected_edge_ids or edge_id.startswith(":"):
            continue
        if edge.attrib.get("from") in collapse_ids or edge.attrib.get("to") in collapse_ids:
            remove_lane_refs(edge_lane_ids(edge))
            candidate_root.remove(edge)
            removed_edge_ids.add(edge_id)
            removed_non_boundary_edge_ids.append(edge_id)
    # The permissive writer may have copied a continuation edge that was not in
    # the candidate at all.  Keep only the explicit teacher cell boundary.
    for edge in list(candidate_root.findall("edge")):
        edge_id = edge.attrib.get("id", "")
        if (
            not edge_id
            or edge_id in protected_edge_ids
            or edge_id in source_edges
            or edge_id.startswith(":")
            or edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}
        ):
            continue
        remove_lane_refs(edge_lane_ids(edge))
        candidate_root.remove(edge)
        removed_edge_ids.add(edge_id)
        removed_non_boundary_edges_added_by_replay.append(edge_id)

    # Remove member-owned internal artifacts but retain the newly replayed
    # target prefix.  This is the actual split-junction collapse.
    removed_member_internal_edge_ids: list[str] = []
    removed_member_internal_junction_ids: list[str] = []
    for edge in list(candidate_root.findall("edge")):
        edge_id = edge.attrib.get("id", "")
        if edge_id.startswith(old_member_prefixes):
            remove_lane_refs(edge_lane_ids(edge))
            candidate_root.remove(edge)
            removed_edge_ids.add(edge_id)
            removed_member_internal_edge_ids.append(edge_id)
    for junction in list(candidate_root.findall("junction")):
        junction_id_value = junction.attrib.get("id", "")
        if junction_id_value.startswith(old_member_prefixes):
            candidate_root.remove(junction)
            removed_member_internal_junction_ids.append(junction_id_value)

    # Map/reuse each explicit teacher boundary edge.  A candidate edge with a
    # different lane cardinality is replaced by the teacher edge under the
    # mapped candidate ID; otherwise a 1-lane continuation can silently drop
    # several controlled linkIndexes during netconvert.
    remapped_boundary_edge_count = 0
    replaced_lane_cardinality_edge_ids: list[str] = []
    replayed_boundary_geometry_edge_ids: list[str] = []
    preserved_mapped_boundary_geometry_edge_ids: list[str] = []
    boundary_geometry_preservation_failures: list[dict[str, object]] = []
    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    for teacher_edge_id in teacher_boundary_edge_ids:
        teacher_edge = teacher_edges.get(teacher_edge_id)
        if teacher_edge is None:
            continue
        candidate_edge_id = effective_edge_map.get(teacher_edge_id, teacher_edge_id)
        candidate_edge = candidate_edges.get(candidate_edge_id)
        if candidate_edge is None:
            candidate_edge = _clone_transformed_boundary_edge(
                teacher_edge,
                candidate_edge_id,
                dx,
                dy,
                effective_edge_map,
                teacher_junction_id,
                junction_id,
            )
            candidate_root.insert(0, candidate_edge)
            candidate_edges[candidate_edge_id] = candidate_edge
        else:
            replacement = _clone_transformed_boundary_edge(
                teacher_edge,
                candidate_edge_id,
                dx,
                dy,
                effective_edge_map,
                teacher_junction_id,
                junction_id,
            )
            lane_cardinality_changed = len(candidate_edge.findall("lane")) != len(teacher_edge.findall("lane"))
            remove_lane_refs(edge_lane_ids(candidate_edge))
            insert_at = list(candidate_root).index(candidate_edge)
            candidate_root.remove(candidate_edge)
            candidate_root.insert(insert_at, replacement)
            candidate_edge = replacement
            candidate_edges[candidate_edge_id] = candidate_edge
            replayed_boundary_geometry_edge_ids.append(candidate_edge_id)
            if lane_cardinality_changed:
                replaced_lane_cardinality_edge_ids.append(candidate_edge_id)
        mapped_from = map_boundary_junction(teacher_edge.attrib.get("from", ""))
        mapped_to = map_boundary_junction(teacher_edge.attrib.get("to", ""))
        remove_lane_refs(edge_lane_ids(candidate_edge))
        candidate_edge.set("from", mapped_from)
        candidate_edge.set("to", mapped_to)
        geometry_source_edge = source_edges.get(candidate_edge_id)
        if geometry_source_edge is not None:
            geometry_report = _preserve_mapped_boundary_geometry(
                candidate_edge,
                geometry_source_edge,
                target_junction_ids={junction_id},
                source_local_junction_ids=collapse_ids,
            )
            if geometry_report.get("status") == "pass":
                preserved_mapped_boundary_geometry_edge_ids.append(candidate_edge_id)
            elif geometry_report.get("status") == "blocked":
                boundary_geometry_preservation_failures.append(
                    {
                        "teacher_edge_id": teacher_edge_id,
                        "candidate_edge_id": candidate_edge_id,
                        **geometry_report,
                    }
                )
        append_lane_refs(mapped_to, edge_lane_ids(candidate_edge))
        remapped_boundary_edge_count += 1

    # Rewrite connection edge aliases before removing stale member references.
    remapped_connection_count = 0
    for connection in list(candidate_root.findall("connection")):
        for attr in ("from", "to"):
            value = connection.attrib.get(attr, "")
            mapped_value = effective_edge_map.get(value, value)
            if mapped_value != value:
                connection.set(attr, mapped_value)
                remapped_connection_count += 1
        values = tuple(connection.attrib.get(attr, "") for attr in ("from", "to", "via"))
        if any(value in removed_edge_ids for value in values) or any(
            value.startswith(old_member_prefixes) for value in values if value
        ):
            candidate_root.remove(connection)
            continue

    # Drop member junctions that are no longer endpoints.  Do not remove an
    # unrelated external junction merely because the teacher supplied a new
    # boundary endpoint for review.
    for junction in list(candidate_root.findall("junction")):
        junction_id_value = junction.attrib.get("id", "")
        if junction_id_value not in old_member_ids:
            continue
        if not any(
            junction_id_value in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
            for edge in candidate_root.findall("edge")
        ):
            candidate_root.remove(junction)

    external_boundary_connection_report = _restore_external_boundary_connections(
        source_root=source_root,
        target_root=candidate_root,
        boundary_edge_ids={
            effective_edge_map.get(edge_id, edge_id)
            for edge_id in teacher_boundary_edge_ids
            if effective_edge_map.get(edge_id, edge_id) in source_edges
        },
        source_local_junction_ids=collapse_ids,
    )

    # Clean stale lane references left by removed split fragments and reject
    # dangling connections before netconvert sees the variant.
    remaining_edge_ids = {
        edge.attrib["id"]
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    for junction in candidate_root.findall("junction"):
        inc_lanes = [
            lane
            for lane in _split(junction.attrib.get("incLanes", ""))
            if lane.rsplit("_", 1)[0] in remaining_edge_ids
            and not lane.startswith(old_member_prefixes)
        ]
        junction.set("incLanes", " ".join(inc_lanes))
        int_lanes = [
            lane
            for lane in _split(junction.attrib.get("intLanes", ""))
            if not lane.startswith(old_member_prefixes)
        ]
        junction.set("intLanes", " ".join(int_lanes))
    # Replacing a mapped boundary edge with the teacher lane cardinality can
    # invalidate stale connections that belonged to the former split graph.
    # Remove those connections before netconvert/SUMO sees the scoped variant;
    # silently retaining them would turn a safe replay into a construction
    # failure or, worse, a lane-index reinterpretation.
    edge_lane_counts = _net_lane_counts(candidate_root)
    removed_invalid_lane_connection_count = 0
    removed_invalid_lane_connections: list[dict[str, str]] = []
    for connection in list(candidate_root.findall("connection")):
        if _connection_lane_indices_valid(connection, edge_lane_counts):
            continue
        removed_invalid_lane_connections.append(dict(connection.attrib))
        candidate_root.remove(connection)
        removed_invalid_lane_connection_count += 1
    removed_dangling_connection_count = 0
    seen_connection_keys: set[tuple[str, ...]] = set()
    for connection in list(candidate_root.findall("connection")):
        if connection.attrib.get("from") not in remaining_edge_ids or connection.attrib.get("to") not in remaining_edge_ids:
            candidate_root.remove(connection)
            removed_dangling_connection_count += 1
            continue
        key = tuple(
            connection.attrib.get(attr, "")
            for attr in ("from", "to", "fromLane", "toLane", "via", "tl", "linkIndex", "dir", "state")
        )
        if key in seen_connection_keys:
            candidate_root.remove(connection)
            removed_dangling_connection_count += 1
            continue
        seen_connection_keys.add(key)

    ET.indent(candidate_root, space="    ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": "blocked" if boundary_geometry_preservation_failures else "pass",
        "claim_status": "diagnostic-demo",
        "scoped_replay_status": "pass",
        "net_file": str(output_file),
        "unscoped_net_file": str(unscoped_file),
        "junction_id": junction_id,
        "teacher_junction_id": teacher_junction_id,
        "collapse_junction_ids": sorted(collapse_ids),
        "old_member_ids": sorted(old_member_ids),
        "teacher_boundary_edge_ids": teacher_boundary_edge_ids,
        "protected_boundary_edge_ids": sorted(protected_edge_ids),
        "effective_edge_map": dict(sorted(effective_edge_map.items())),
        "junction_map": dict(sorted(ordinary_junction_map.items())),
        "remapped_boundary_edge_count": remapped_boundary_edge_count,
        "remapped_connection_endpoint_count": remapped_connection_count,
        "replaced_lane_cardinality_edge_ids": sorted(replaced_lane_cardinality_edge_ids),
        "replayed_boundary_geometry_edge_ids": sorted(set(replayed_boundary_geometry_edge_ids)),
        "preserved_mapped_boundary_geometry_edge_ids": sorted(
            set(preserved_mapped_boundary_geometry_edge_ids)
        ),
        "boundary_geometry_preservation_failure_count": len(boundary_geometry_preservation_failures),
        "boundary_geometry_preservation_failures": boundary_geometry_preservation_failures,
        "restored_external_boundary_connection_count": external_boundary_connection_report[
            "restored_connection_count"
        ],
        "restored_external_boundary_connections": external_boundary_connection_report[
            "restored_connections"
        ],
        "preserved_existing_external_boundary_connection_count": external_boundary_connection_report[
            "preserved_existing_connection_count"
        ],
        "preserved_existing_external_boundary_connections": external_boundary_connection_report[
            "preserved_existing_connections"
        ],
        "skipped_external_boundary_connection_count": external_boundary_connection_report[
            "skipped_connection_count"
        ],
        "skipped_external_boundary_connections": external_boundary_connection_report[
            "skipped_connections"
        ],
        "removed_non_boundary_edge_count": len(removed_non_boundary_edge_ids),
        "removed_non_boundary_edge_ids": sorted(removed_non_boundary_edge_ids),
        "removed_replay_continuation_edge_count": len(removed_non_boundary_edges_added_by_replay),
        "removed_replay_continuation_edge_ids": sorted(removed_non_boundary_edges_added_by_replay),
        "removed_member_internal_edge_count": len(removed_member_internal_edge_ids),
        "removed_member_internal_edge_ids": sorted(removed_member_internal_edge_ids),
        "removed_member_internal_junction_count": len(removed_member_internal_junction_ids),
        "removed_member_internal_junction_ids": sorted(removed_member_internal_junction_ids),
        "removed_invalid_lane_connection_count": removed_invalid_lane_connection_count,
        "removed_invalid_lane_connections": removed_invalid_lane_connections,
        "removed_dangling_or_duplicate_connection_count": removed_dangling_connection_count,
        "base_replay_report": replay_report,
    }


def write_shared_teacher_tls_controller_replay_net(
    *,
    candidate_net_file: Path,
    teacher_net_file: Path,
    output_file: Path,
    candidate_controller_id: str,
    teacher_controller_id: str,
    owner_map: dict[str, str],
    edge_map: dict[str, str],
    junction_map: dict[str, str] | None = None,
    collapse_junction_ids: set[str] | list[str] | None = None,
) -> dict[str, object]:
    """Replay a TLS whose link indexes span more than one internal owner.

    SUMO permits several physical junctions to use one ``tlLogic``.  The
    single-owner writer above deliberately rejects that shape because mapping
    a secondary ``via`` prefix into the primary prefix would silently change
    topology.  This writer requires an explicit reference-owner to
    candidate-owner map and an explicit boundary-edge map, then replays all
    owner-local internal artifacts and connections in one variant.

    The function is intentionally local to a repair variant.  It never edits
    the source network and it reports every teacher boundary that was copied
    under a generated candidate edge id, so a later semantic gate can decide
    whether the copy is acceptable.
    """

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if not candidate_net_file.exists():
        return _failure(f"candidate net file does not exist: {candidate_net_file}")
    if not teacher_net_file.exists():
        return _failure(f"teacher net file does not exist: {teacher_net_file}")
    try:
        candidate_tree = ET.parse(candidate_net_file)
        candidate_root = candidate_tree.getroot()
        candidate_source_root = copy.deepcopy(candidate_root)
        teacher_root = ET.parse(teacher_net_file).getroot()
    except (ET.ParseError, OSError, ValueError) as exc:
        return _failure(f"shared TLS controller replay parse failed: {type(exc).__name__}: {exc}")

    clean_owner_map = {
        str(key): str(value)
        for key, value in (owner_map or {}).items()
        if str(key) and str(value)
    }
    clean_owner_map.setdefault(teacher_controller_id, candidate_controller_id)
    clean_junction_map = {
        str(key): str(value)
        for key, value in (junction_map or {}).items()
        if str(key) and str(value)
    }
    collapse_ids = {
        str(value)
        for value in (collapse_junction_ids or set())
        if str(value)
    }
    collapse_ids.update(clean_owner_map.values())
    candidate_owner_ids = set(clean_owner_map.values())
    teacher_owner_ids = sorted(clean_owner_map, key=len, reverse=True)
    teacher_edges = {
        edge.attrib["id"]: edge
        for edge in teacher_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_junctions = {
        junction.attrib["id"]: junction
        for junction in teacher_root.findall("junction")
        if junction.attrib.get("id")
    }
    candidate_junctions = {
        junction.attrib["id"]: junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }
    if candidate_controller_id not in candidate_junctions:
        return _failure(f"candidate controller junction not found: {candidate_controller_id}")
    if teacher_controller_id not in teacher_junctions:
        return _failure(f"teacher controller junction not found: {teacher_controller_id}")
    missing_owner_ids = [
        owner_id
        for owner_id in teacher_owner_ids
        if owner_id not in teacher_junctions or clean_owner_map[owner_id] not in candidate_junctions
    ]
    if missing_owner_ids:
        return {
            **_failure("shared TLS owner closure is incomplete"),
            "missing_owner_ids": missing_owner_ids,
            "owner_map": dict(sorted(clean_owner_map.items())),
        }

    def teacher_owner_for(value: str) -> str:
        for owner_id in teacher_owner_ids:
            if value.startswith(f":{owner_id}_"):
                return owner_id
        return ""

    def candidate_internal_ref(value: str) -> str:
        owner_id = teacher_owner_for(value)
        if not owner_id:
            return value
        prefix = f":{owner_id}_"
        return f":{clean_owner_map[owner_id]}_{value[len(prefix):]}"

    def teacher_edge_owner(edge: ET.Element) -> str:
        for endpoint in (edge.attrib.get("from", ""), edge.attrib.get("to", "")):
            if endpoint in teacher_owner_ids:
                return endpoint
        return ""

    def point_delta(owner_id: str = "") -> tuple[float, float]:
        anchor_owner = owner_id if owner_id in clean_owner_map else teacher_controller_id
        teacher_anchor = teacher_junctions.get(anchor_owner)
        candidate_anchor = candidate_junctions.get(clean_owner_map.get(anchor_owner, candidate_controller_id))
        if teacher_anchor is None or candidate_anchor is None:
            return (0.0, 0.0)
        try:
            return (
                float(candidate_anchor.attrib.get("x", "0") or 0)
                - float(teacher_anchor.attrib.get("x", "0") or 0),
                float(candidate_anchor.attrib.get("y", "0") or 0)
                - float(teacher_anchor.attrib.get("y", "0") or 0),
            )
        except (TypeError, ValueError):
            return (0.0, 0.0)

    def map_junction_id(value: str) -> str:
        if value in clean_owner_map:
            return clean_owner_map[value]
        if value in clean_junction_map:
            return clean_junction_map[value]
        return value

    def map_edge_ref(value: str, resolved_edge_map: dict[str, str]) -> str:
        if not value:
            return ""
        if value.startswith(":"):
            return candidate_internal_ref(value)
        return resolved_edge_map.get(value, value if value in candidate_edges else "")

    def map_crossing_edges(value: str, resolved_edge_map: dict[str, str]) -> str:
        mapped: list[str] = []
        for edge_id in _split(value):
            if edge_id.startswith(":"):
                mapped_id = candidate_internal_ref(edge_id)
            else:
                mapped_id = resolved_edge_map.get(edge_id, edge_id if edge_id in candidate_edges else "")
            if mapped_id:
                mapped.append(mapped_id)
        return " ".join(mapped)

    def relevant_connection(connection: ET.Element) -> bool:
        if any(teacher_owner_for(connection.attrib.get(attr, "")) for attr in ("from", "to", "via")):
            return True
        if connection.attrib.get("tl") != teacher_controller_id:
            return False
        for attr in ("from", "to"):
            edge = teacher_edges.get(connection.attrib.get(attr, ""))
            if edge is not None and teacher_edge_owner(edge):
                return True
        return False

    relevant_connections = [
        connection for connection in teacher_root.findall("connection") if relevant_connection(connection)
    ]
    controlled_connections = [
        connection
        for connection in relevant_connections
        if connection.attrib.get("tl") == teacher_controller_id and connection.attrib.get("linkIndex") is not None
    ]
    teacher_link_indices = {
        str(connection.attrib.get("linkIndex", "")) for connection in controlled_connections
    }
    if not controlled_connections:
        return _failure("shared TLS controller has no controlled connections")

    boundary_edge_ids: list[str] = []
    seen_boundary_edge_ids: set[str] = set()
    for connection in relevant_connections:
        for attr in ("from", "to"):
            edge_id = connection.attrib.get(attr, "")
            if not edge_id or edge_id.startswith(":") or edge_id not in teacher_edges:
                continue
            if edge_id not in seen_boundary_edge_ids:
                seen_boundary_edge_ids.add(edge_id)
                boundary_edge_ids.append(edge_id)
    for edge in teacher_edges.values():
        edge_id = edge.attrib.get("id", "")
        owner_id = teacher_owner_for(edge_id)
        if not owner_id:
            continue
        for crossing_edge_id in _split(edge.attrib.get("crossingEdges", "")):
            if crossing_edge_id in teacher_edges and crossing_edge_id not in seen_boundary_edge_ids:
                seen_boundary_edge_ids.add(crossing_edge_id)
                boundary_edge_ids.append(crossing_edge_id)

    resolved_edge_map: dict[str, str] = {}
    generated_boundary_edge_ids: list[str] = []
    edge_mapping_sources: dict[str, str] = {}
    reverse_edge_map: dict[str, str] = {}
    for teacher_edge_id in boundary_edge_ids:
        mapped_edge_id = str((edge_map or {}).get(teacher_edge_id, "")).strip()
        if not mapped_edge_id:
            if teacher_edge_id in candidate_edges:
                mapped_edge_id = teacher_edge_id
                edge_mapping_sources[teacher_edge_id] = "candidate_identity"
            else:
                safe_id = "".join(
                    character if character.isalnum() or character in "_.-" else "_"
                    for character in teacher_edge_id
                ).strip("_") or "edge"
                mapped_edge_id = f"torii_shared_{safe_id}"
                edge_mapping_sources[teacher_edge_id] = "explicit_teacher_boundary_copy"
                generated_boundary_edge_ids.append(teacher_edge_id)
        else:
            edge_mapping_sources[teacher_edge_id] = "explicit_edge_map"
        previous_teacher_edge_id = reverse_edge_map.get(mapped_edge_id)
        if previous_teacher_edge_id and previous_teacher_edge_id != teacher_edge_id:
            return {
                **_failure("shared TLS boundary edge map is not one-to-one"),
                "edge_mapping_conflict": {
                    "candidate_edge_id": mapped_edge_id,
                    "teacher_edge_ids": [previous_teacher_edge_id, teacher_edge_id],
                },
            }
        reverse_edge_map[mapped_edge_id] = teacher_edge_id
        resolved_edge_map[teacher_edge_id] = mapped_edge_id

    unanchored_boundary_edge_ids = sorted(
        teacher_edge_id
        for teacher_edge_id, mapped_edge_id in resolved_edge_map.items()
        if mapped_edge_id not in candidate_edges
    )
    if unanchored_boundary_edge_ids:
        return {
            **_failure("shared TLS boundary geometry anchor is missing"),
            "status": "blocked",
            "shared_controller_replay_status": "blocked",
            "candidate_controller_id": candidate_controller_id,
            "teacher_controller_id": teacher_controller_id,
            "owner_map": dict(sorted(clean_owner_map.items())),
            "junction_map": dict(sorted(clean_junction_map.items())),
            "effective_edge_map": dict(sorted(resolved_edge_map.items())),
            "edge_mapping_sources": dict(sorted(edge_mapping_sources.items())),
            "generated_boundary_edge_ids": sorted(generated_boundary_edge_ids),
            "unanchored_boundary_edge_count": len(unanchored_boundary_edge_ids),
            "unanchored_boundary_edge_ids": unanchored_boundary_edge_ids,
            "replay_policy": "mapped shared boundaries require existing candidate geometry anchors",
        }

    # Remove only the local candidate cell.  Existing normal edges that are
    # explicitly mapped are replaced below; all other network edges remain.
    candidate_internal_prefixes = tuple(f":{owner_id}_" for owner_id in sorted(collapse_ids, key=len, reverse=True))
    removed_internal_edge_ids: list[str] = []
    for edge in list(candidate_root.findall("edge")):
        edge_id = edge.attrib.get("id", "")
        if edge_id.startswith(candidate_internal_prefixes):
            _remove_edge_lanes_from_destination_junction(candidate_root, edge, all_junctions=True)
            candidate_root.remove(edge)
            removed_internal_edge_ids.append(edge_id)
    removed_internal_junction_ids: list[str] = []
    for junction in list(candidate_root.findall("junction")):
        junction_id = junction.attrib.get("id", "")
        if junction_id.startswith(candidate_internal_prefixes):
            candidate_root.remove(junction)
            removed_internal_junction_ids.append(junction_id)

    protected_edge_ids = set(resolved_edge_map.values())
    removed_member_edge_ids: list[str] = []
    for edge in list(candidate_root.findall("edge")):
        edge_id = edge.attrib.get("id", "")
        if (
            not edge_id
            or edge_id.startswith(":")
            or edge_id in protected_edge_ids
            or edge.attrib.get("function") in {"internal", "crossing", "walkingarea"}
            or not ({edge.attrib.get("from", ""), edge.attrib.get("to", "")} & collapse_ids)
        ):
            continue
        _remove_edge_lanes_from_destination_junction(candidate_root, edge, all_junctions=True)
        candidate_root.remove(edge)
        removed_member_edge_ids.append(edge_id)

    removed_connection_count = 0
    removed_edge_id_set = set(removed_member_edge_ids)
    for connection in list(candidate_root.findall("connection")):
        values = tuple(connection.attrib.get(attr, "") for attr in ("from", "to", "via"))
        if (
            connection.attrib.get("tl") in candidate_owner_ids | {candidate_controller_id}
            or any(value.startswith(candidate_internal_prefixes) for value in values if value)
            or any(value in removed_edge_id_set for value in values if value)
            or any(value in protected_edge_ids for value in values if value)
        ):
            candidate_root.remove(connection)
            removed_connection_count += 1

    # Existing mapped boundary edges can carry connections outside the local
    # cell.  They must be removed with the old edge before the teacher edge is
    # inserted, otherwise lane indices from the old split survive silently.
    removed_mapped_edge_connection_count = 0
    for connection in list(candidate_root.findall("connection")):
        if any(connection.attrib.get(attr, "") in protected_edge_ids for attr in ("from", "to")):
            candidate_root.remove(connection)
            removed_mapped_edge_connection_count += 1

    candidate_edges = {
        edge.attrib["id"]: edge
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    candidate_junctions = {
        junction.attrib["id"]: junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id")
    }

    def ensure_boundary_junction(teacher_junction_id: str, owner_hint: str) -> str:
        mapped_id = map_junction_id(teacher_junction_id)
        if mapped_id in candidate_junctions:
            return mapped_id
        source_junction = teacher_junctions.get(teacher_junction_id)
        if source_junction is None:
            return mapped_id
        dx, dy = point_delta(owner_hint)
        attrs = dict(source_junction.attrib)
        if "x" in attrs:
            attrs["x"] = _format_xy(float(attrs["x"]) + dx)
        if "y" in attrs:
            attrs["y"] = _format_xy(float(attrs["y"]) + dy)
        for attr in ("shape", "outlineShape", "customShape"):
            if attr in attrs:
                attrs[attr] = _translate_shape(attrs[attr], dx, dy)
        attrs["id"] = mapped_id
        for attr in ("from", "to", "tl"):
            if attr in attrs:
                attrs[attr] = map_junction_id(attrs[attr])
        if "crossingEdges" in attrs:
            attrs["crossingEdges"] = map_crossing_edges(attrs["crossingEdges"], resolved_edge_map)
        attrs["incLanes"] = ""
        attrs["intLanes"] = ""
        candidate_root.insert(_first_junction_index(candidate_root), ET.Element("junction", attrs))
        candidate_junctions[mapped_id] = candidate_root.find(f"junction[@id='{mapped_id}']")
        return mapped_id

    # Add all normal teacher boundaries first so every mapped connection has a
    # concrete edge endpoint.  A copied boundary is a deliberate artifact, not
    # a nearest-family guess.
    copied_boundary_edge_ids: list[str] = []
    replaced_boundary_edge_ids: list[str] = []
    preserved_mapped_boundary_geometry_edge_ids: list[str] = []
    boundary_geometry_preservation_failures: list[dict[str, object]] = []
    for teacher_edge_id in boundary_edge_ids:
        teacher_edge = teacher_edges[teacher_edge_id]
        mapped_edge_id = resolved_edge_map[teacher_edge_id]
        owner_hint = teacher_edge_owner(teacher_edge)
        mapped_from = ensure_boundary_junction(teacher_edge.attrib.get("from", ""), owner_hint)
        mapped_to = ensure_boundary_junction(teacher_edge.attrib.get("to", ""), owner_hint)
        dx, dy = point_delta(owner_hint)
        clone = _clone_transformed_net_element(
            teacher_edge,
            dx,
            dy,
            resolved_edge_map,
        )
        clone.set("id", mapped_edge_id)
        clone.set("from", mapped_from)
        clone.set("to", mapped_to)
        geometry_source_edge = candidate_source_root.find(f"edge[@id='{mapped_edge_id}']")
        if geometry_source_edge is not None:
            geometry_report = _preserve_mapped_boundary_geometry(
                clone,
                geometry_source_edge,
                target_junction_ids=candidate_owner_ids,
                source_local_junction_ids=collapse_ids,
            )
            if geometry_report.get("status") == "pass":
                preserved_mapped_boundary_geometry_edge_ids.append(mapped_edge_id)
            elif geometry_report.get("status") == "blocked":
                boundary_geometry_preservation_failures.append(
                    {
                        "teacher_edge_id": teacher_edge_id,
                        "candidate_edge_id": mapped_edge_id,
                        **geometry_report,
                    }
                )
        clone.attrib.pop("tl", None)
        for attr in ("crossingEdges",):
            if attr in clone.attrib:
                clone.set(attr, map_crossing_edges(clone.attrib[attr], resolved_edge_map))
        teacher_edge_prefix = f"{teacher_edge_id}_"
        mapped_edge_prefix = f"{mapped_edge_id}_"
        for lane_index, lane in enumerate(clone.findall("lane")):
            old_lane_id = lane.attrib.get("id", "")
            suffix = old_lane_id[len(teacher_edge_prefix):] if old_lane_id.startswith(teacher_edge_prefix) else str(
                lane.attrib.get("index", lane_index)
            )
            lane.set("id", f"{mapped_edge_prefix}{suffix}")
        existing = candidate_edges.get(mapped_edge_id)
        if existing is not None:
            _remove_edge_lanes_from_destination_junction(candidate_root, existing, all_junctions=True)
            insert_at = list(candidate_root).index(existing)
            candidate_root.remove(existing)
            candidate_root.insert(insert_at, clone)
            replaced_boundary_edge_ids.append(teacher_edge_id)
        else:
            candidate_root.insert(_first_junction_index(candidate_root), clone)
            copied_boundary_edge_ids.append(teacher_edge_id)
        candidate_edges[mapped_edge_id] = clone

    # Rebuild every owner-local internal edge and junction under its explicit
    # candidate owner prefix.  This includes non-vehicle movement artifacts.
    copied_internal_edge_count = 0
    copied_internal_junction_count = 0
    for teacher_owner_id in teacher_owner_ids:
        candidate_owner_id = clean_owner_map[teacher_owner_id]
        dx, dy = point_delta(teacher_owner_id)
        teacher_prefix = f":{teacher_owner_id}_"
        candidate_prefix = f":{candidate_owner_id}_"
        for edge in teacher_root.findall("edge"):
            # Owner ids may themselves share prefixes (for example ``tls``
            # and ``tls__owner_01``).  A plain ``startswith`` check would
            # replay the secondary owner's internal artifacts a second time
            # under the primary owner, yielding ids such as
            # ``:candidate__owner_01_0`` whose implicit SUMO junction does
            # not exist.  Resolve ownership with the same longest-prefix
            # rule used for connection and ``via`` mapping.
            if teacher_owner_for(edge.attrib.get("id", "")) != teacher_owner_id:
                continue
            clone = _clone_transformed_net_element(
                edge,
                dx,
                dy,
                resolved_edge_map,
                teacher_owner_id,
                candidate_owner_id,
            )
            if "crossingEdges" in clone.attrib:
                clone.set("crossingEdges", map_crossing_edges(clone.attrib["crossingEdges"], resolved_edge_map))
            candidate_root.insert(_first_junction_index(candidate_root), clone)
            candidate_edges[clone.attrib.get("id", "")] = clone
            copied_internal_edge_count += 1
        for junction in teacher_root.findall("junction"):
            if teacher_owner_for(junction.attrib.get("id", "")) != teacher_owner_id:
                continue
            candidate_root.insert(
                _first_junction_index(candidate_root),
                _clone_transformed_junction(
                    junction,
                    dx,
                    dy,
                    resolved_edge_map,
                    teacher_prefix,
                    candidate_prefix,
                ),
            )
            copied_internal_junction_count += 1

        teacher_normal_junction = teacher_junctions.get(teacher_owner_id)
        candidate_normal_junction = candidate_root.find(f"junction[@id='{candidate_owner_id}']")
        if teacher_normal_junction is None or candidate_normal_junction is None:
            continue
        attrs = _mapped_junction_attrs(
            teacher_normal_junction,
            dx,
            dy,
            resolved_edge_map,
            teacher_prefix,
            candidate_prefix,
        )
        attrs["id"] = candidate_owner_id
        candidate_normal_junction.attrib.clear()
        candidate_normal_junction.attrib.update(attrs)
        for child in list(candidate_normal_junction):
            candidate_normal_junction.remove(child)
        for request in teacher_normal_junction.findall("request"):
            candidate_normal_junction.append(ET.Element("request", dict(request.attrib)))

    # Replace any old local tlLogics with one shared controller logic.  The
    # controller id intentionally remains the primary candidate id while its
    # connections may originate at either physical candidate owner.
    removed_tllogic_ids: list[str] = []
    for tl_logic in list(candidate_root.findall("tlLogic")):
        if tl_logic.attrib.get("id") in candidate_owner_ids | {candidate_controller_id}:
            removed_tllogic_ids.append(tl_logic.attrib.get("id", ""))
            candidate_root.remove(tl_logic)
    teacher_tllogic = teacher_root.find(f"tlLogic[@id='{teacher_controller_id}']")
    if teacher_tllogic is None:
        return _failure(f"teacher tlLogic not found: {teacher_controller_id}")
    controller_dx, controller_dy = point_delta(teacher_controller_id)
    copied_tllogic = _clone_transformed_net_element(
        teacher_tllogic,
        controller_dx,
        controller_dy,
        resolved_edge_map,
    )
    copied_tllogic.set("id", candidate_controller_id)
    candidate_root.insert(
        next(
            (index for index, child in enumerate(list(candidate_root)) if child.tag == "connection"),
            len(list(candidate_root)),
        ),
        copied_tllogic,
    )

    copied_connection_count = 0
    copied_controlled_connection_count = 0
    skipped_connections: list[dict[str, str]] = []
    copied_connections: list[dict[str, str]] = []
    for connection in relevant_connections:
        mapped = dict(connection.attrib)
        owner_hint = teacher_owner_for(connection.attrib.get("via", ""))
        if not owner_hint:
            for attr in ("from", "to"):
                owner_hint = teacher_edge_owner(teacher_edges.get(connection.attrib.get(attr, ""), ET.Element("edge")))
                if owner_hint:
                    break
        for attr in ("from", "to"):
            mapped_value = map_edge_ref(connection.attrib.get(attr, ""), resolved_edge_map)
            if not mapped_value:
                skipped_connections.append(dict(connection.attrib))
                break
            mapped[attr] = mapped_value
        else:
            if mapped.get("via"):
                mapped["via"] = candidate_internal_ref(mapped["via"])
            if mapped.get("tl") == teacher_controller_id:
                mapped["tl"] = candidate_controller_id
            if mapped.get("shape"):
                dx, dy = point_delta(owner_hint)
                mapped["shape"] = _translate_shape(mapped["shape"], dx, dy)
            via_value = mapped.get("via", "")
            if via_value:
                via_edge_id = via_value.rsplit("_", 1)[0]
                if via_edge_id not in candidate_edges:
                    skipped_connections.append(dict(connection.attrib))
                    continue
            if mapped["from"] not in candidate_edges or mapped["to"] not in candidate_edges:
                skipped_connections.append(dict(connection.attrib))
                continue
            candidate_root.append(ET.Element("connection", mapped))
            copied_connections.append(mapped)
            copied_connection_count += 1
            if mapped.get("tl") == candidate_controller_id and mapped.get("linkIndex") is not None:
                copied_controlled_connection_count += 1

    external_boundary_connection_report = _restore_external_boundary_connections(
        source_root=candidate_source_root,
        target_root=candidate_root,
        boundary_edge_ids=protected_edge_ids,
        source_local_junction_ids=collapse_ids,
    )

    # Remove member junctions that no longer have an edge endpoint, then clean
    # lane references and invalid connections before the external SUMO gates.
    candidate_edge_ids = {
        edge.attrib["id"] for edge in candidate_root.findall("edge") if edge.attrib.get("id")
    }
    for junction in list(candidate_root.findall("junction")):
        junction_id = junction.attrib.get("id", "")
        if junction_id not in collapse_ids or junction_id in candidate_owner_ids:
            continue
        if not any(
            junction_id in (edge.attrib.get("from", ""), edge.attrib.get("to", ""))
            for edge in candidate_root.findall("edge")
        ):
            candidate_root.remove(junction)

    for junction in candidate_root.findall("junction"):
        inc_lanes = [
            lane
            for lane in _split(junction.attrib.get("incLanes", ""))
            if lane.rsplit("_", 1)[0] in candidate_edge_ids
        ]
        junction.set("incLanes", " ".join(inc_lanes))
    for owner_id in candidate_owner_ids:
        junction = candidate_root.find(f"junction[@id='{owner_id}']")
        if junction is None:
            continue
        incoming_lanes = [
            lane.attrib["id"]
            for edge in candidate_root.findall("edge")
            if edge.attrib.get("to") == owner_id
            for lane in edge.findall("lane")
            if lane.attrib.get("id")
        ]
        existing_inc_lanes = _split(junction.attrib.get("incLanes", ""))
        junction.set("incLanes", " ".join(dict.fromkeys([*existing_inc_lanes, *incoming_lanes])))

    invalid_connections: list[dict[str, str]] = []
    dangling_connections: list[dict[str, str]] = []
    edge_lane_counts = _net_lane_counts(candidate_root)
    for connection in list(candidate_root.findall("connection")):
        if connection.attrib.get("from") not in candidate_edge_ids or connection.attrib.get("to") not in candidate_edge_ids:
            dangling_connections.append(dict(connection.attrib))
            candidate_root.remove(connection)
            continue
        if not _connection_lane_indices_valid(connection, edge_lane_counts):
            invalid_connections.append(dict(connection.attrib))
            candidate_root.remove(connection)

    actual_controlled_connections = [
        connection
        for connection in candidate_root.findall("connection")
        if connection.attrib.get("tl") == candidate_controller_id and connection.attrib.get("linkIndex") is not None
    ]
    actual_link_indices = {str(connection.attrib.get("linkIndex", "")) for connection in actual_controlled_connections}
    invalid_controlled_connections = [
        connection
        for connection in [*invalid_connections, *dangling_connections]
        if connection.get("tl") == candidate_controller_id and connection.get("linkIndex") is not None
    ]
    missing_link_indices = sorted(teacher_link_indices - actual_link_indices, key=lambda value: int(value) if value.isdigit() else value)
    unexpected_link_indices = sorted(actual_link_indices - teacher_link_indices, key=lambda value: int(value) if value.isdigit() else value)
    status = "pass"
    if (
        skipped_connections
        or invalid_controlled_connections
        or missing_link_indices
        or unexpected_link_indices
        or copied_controlled_connection_count != len(controlled_connections)
        or boundary_geometry_preservation_failures
        or not output_file.parent.exists()
    ):
        status = "blocked"

    ET.indent(candidate_root, space="    ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return {
        "status": status,
        "claim_status": "diagnostic-demo",
        "net_file": str(output_file),
        "candidate_controller_id": candidate_controller_id,
        "teacher_controller_id": teacher_controller_id,
        "owner_map": dict(sorted(clean_owner_map.items())),
        "junction_map": dict(sorted(clean_junction_map.items())),
        "collapse_junction_ids": sorted(collapse_ids),
        "teacher_owner_ids": teacher_owner_ids,
        "candidate_owner_ids": sorted(candidate_owner_ids),
        "teacher_boundary_edge_ids": boundary_edge_ids,
        "effective_edge_map": dict(sorted(resolved_edge_map.items())),
        "edge_mapping_sources": dict(sorted(edge_mapping_sources.items())),
        "generated_boundary_edge_ids": sorted(generated_boundary_edge_ids),
        "unanchored_boundary_edge_count": 0,
        "unanchored_boundary_edge_ids": [],
        "copied_boundary_edge_ids": sorted(copied_boundary_edge_ids),
        "replaced_boundary_edge_ids": sorted(replaced_boundary_edge_ids),
        "preserved_mapped_boundary_geometry_edge_ids": sorted(
            set(preserved_mapped_boundary_geometry_edge_ids)
        ),
        "boundary_geometry_preservation_failure_count": len(boundary_geometry_preservation_failures),
        "boundary_geometry_preservation_failures": boundary_geometry_preservation_failures,
        "restored_external_boundary_connection_count": external_boundary_connection_report[
            "restored_connection_count"
        ],
        "restored_external_boundary_connections": external_boundary_connection_report[
            "restored_connections"
        ],
        "preserved_existing_external_boundary_connection_count": external_boundary_connection_report[
            "preserved_existing_connection_count"
        ],
        "preserved_existing_external_boundary_connections": external_boundary_connection_report[
            "preserved_existing_connections"
        ],
        "skipped_external_boundary_connection_count": external_boundary_connection_report[
            "skipped_connection_count"
        ],
        "skipped_external_boundary_connections": external_boundary_connection_report[
            "skipped_connections"
        ],
        "removed_internal_edge_count": len(removed_internal_edge_ids),
        "removed_internal_junction_count": len(removed_internal_junction_ids),
        "removed_member_edge_count": len(removed_member_edge_ids),
        "removed_connection_count": removed_connection_count,
        "removed_mapped_edge_connection_count": removed_mapped_edge_connection_count,
        "copied_internal_edge_count": copied_internal_edge_count,
        "copied_internal_junction_count": copied_internal_junction_count,
        "removed_tllogic_ids": sorted(removed_tllogic_ids),
        "teacher_controlled_connection_count": len(controlled_connections),
        "teacher_link_indices": sorted(teacher_link_indices, key=lambda value: int(value) if value.isdigit() else value),
        "copied_connection_count": copied_connection_count,
        "copied_controlled_connection_count": copied_controlled_connection_count,
        "actual_controlled_connection_count": len(actual_controlled_connections),
        "actual_link_indices": sorted(actual_link_indices, key=lambda value: int(value) if value.isdigit() else value),
        "missing_link_indices": missing_link_indices,
        "unexpected_link_indices": unexpected_link_indices,
        "skipped_connections": skipped_connections,
        "invalid_connection_count": len(invalid_connections),
        "invalid_connections": invalid_connections,
        "dangling_connection_count": len(dangling_connections),
        "dangling_connections": dangling_connections,
        "invalid_controlled_connection_count": len(invalid_controlled_connections),
        "policy": "explicit multi-owner closure; generated boundary edges remain reviewable artifacts",
    }
