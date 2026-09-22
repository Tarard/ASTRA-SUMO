"""Apply authorized junction-shape and movement-geometry repairs."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from ..corridor_simplification import audit_alias_normalized_connections
from ..official_tls_rebuild import edge_lane_signature
from .artifacts import _failure
from .geometry import _estimate_linear_lane_transition_shape, _shape_points
from .network import _connection_key, _connection_key_record
from .signatures import (
    _junction_shape_repair_topology_sha256,
    _junction_shape_tls_sha256,
    _xml_element_semantic_payload,
)


def write_reanchored_normal_junction_movements(
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    output_file: Path,
    junction_id: str,
    declared_added_movement_shapes: dict[tuple[str, str, str, str], str],
) -> dict[str, object]:
    """Reanchor one normal junction's internal movement lanes, and nothing else.

    The candidate may add only the explicitly declared external movements.  An
    existing movement reuses its accepted source via-lane geometry; a declared
    movement uses the supplied endpoint-bound polyline.  This helper is meant
    to run after global ``netconvert`` geometry has already been restored.
    """

    missing = [str(path) for path in (source_net_file, candidate_net_file) if not path.exists()]
    if missing:
        return _failure(f"missing input file(s): {', '.join(missing)}")
    junction_id = str(junction_id).strip()
    if not junction_id or junction_id.startswith(":"):
        return _failure("one normal junction id is required")
    if not declared_added_movement_shapes:
        return _failure("at least one declared added movement shape is required")

    source_net_file = source_net_file.resolve()
    candidate_net_file = candidate_net_file.resolve()
    output_file = output_file.resolve()
    if output_file in {source_net_file, candidate_net_file}:
        return _failure("output file must differ from source and candidate inputs")

    source_sha256 = hashlib.sha256(source_net_file.read_bytes()).hexdigest()
    candidate_sha256 = hashlib.sha256(candidate_net_file.read_bytes()).hexdigest()
    source_root = ET.parse(source_net_file).getroot()
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    internal_prefix = f":{junction_id}_"

    def fail(reason: str, failures: list[dict[str, object]]) -> dict[str, object]:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reason": reason,
            "source_net_file": str(source_net_file),
            "source_sha256": source_sha256,
            "candidate_net_file": str(candidate_net_file),
            "candidate_sha256": candidate_sha256,
            "output_file": str(output_file),
            "junction_id": junction_id,
            "failure_count": len(failures),
            "failures": failures,
        }

    def strict_polyline(shape: str) -> tuple[list[tuple[float, ...]], float] | None:
        points: list[tuple[float, ...]] = []
        try:
            for token in shape.split():
                coordinates = tuple(float(value) for value in token.split(","))
                if len(coordinates) not in {2, 3} or not all(math.isfinite(value) for value in coordinates):
                    return None
                points.append(coordinates)
        except ValueError:
            return None
        if len(points) < 2 or any(len(point) != len(points[0]) for point in points):
            return None
        length = sum(math.dist(left, right) for left, right in zip(points, points[1:]))
        return (points, length) if math.isfinite(length) and length > 0 else None

    def external_connections(root: ET.Element) -> dict[tuple[str, str, str, str], list[ET.Element]]:
        grouped: dict[tuple[str, str, str, str], list[ET.Element]] = {}
        for connection in root.findall("connection"):
            if connection.attrib.get("from", "").startswith(":") or connection.attrib.get("to", "").startswith(":"):
                continue
            grouped.setdefault(_connection_key(connection), []).append(connection)
        return grouped

    def lane_index(root: ET.Element) -> dict[str, list[ET.Element]]:
        indexed: dict[str, list[ET.Element]] = {}
        for lane in root.findall("edge/lane"):
            lane_id = lane.attrib.get("id", "")
            if lane_id:
                indexed.setdefault(lane_id, []).append(lane)
        return indexed

    def external_lane(root: ET.Element, edge_id: str, lane_index_value: str) -> ET.Element | None:
        edge = root.find(f"edge[@id='{edge_id}']")
        if edge is None or edge_id.startswith(":"):
            return None
        lanes = [lane for lane in edge.findall("lane") if lane.attrib.get("index", "0") == lane_index_value]
        return lanes[0] if len(lanes) == 1 else None

    def invariant_hashes(root: ET.Element) -> dict[str, str]:
        external_edges = [
            _xml_element_semantic_payload(edge)
            for edge in root.findall("edge")
            if not edge.attrib.get("id", "").startswith(":")
        ]
        connections = [_xml_element_semantic_payload(connection) for connection in root.findall("connection")]
        tls = [_xml_element_semantic_payload(logic) for logic in root.findall("tlLogic")]
        requests = [
            {
                "junction_id": junction.attrib.get("id", ""),
                "intLanes": junction.attrib.get("intLanes", ""),
                "requests": [_xml_element_semantic_payload(request) for request in junction.findall("request")],
            }
            for junction in root.findall("junction")
            if not junction.attrib.get("id", "").startswith(":")
        ]

        def digest(payload: object) -> str:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()

        return {
            "external_lanes_sha256": digest(external_edges),
            "connections_sha256": digest(connections),
            "tls_sha256": digest(tls),
            "request_matrix_sha256": digest(requests),
        }

    failures: list[dict[str, object]] = []
    source_junctions = source_root.findall(f"junction[@id='{junction_id}']")
    candidate_junctions = candidate_root.findall(f"junction[@id='{junction_id}']")
    if len(source_junctions) != 1 or len(candidate_junctions) != 1:
        failures.append(
            {
                "reason": "target_normal_junction_not_unique",
                "source_count": len(source_junctions),
                "candidate_count": len(candidate_junctions),
            }
        )
        return fail("target junction validation failed", failures)
    source_junction = source_junctions[0]
    candidate_junction = candidate_junctions[0]
    if source_junction.attrib.get("shape", "") != candidate_junction.attrib.get("shape", ""):
        failures.append(
            {
                "reason": "target_junction_shape_changed",
                "source_shape": source_junction.attrib.get("shape", ""),
                "candidate_shape": candidate_junction.attrib.get("shape", ""),
            }
        )

    declared_shapes: dict[tuple[str, str, str, str], str] = {}
    for raw_key, raw_shape in declared_added_movement_shapes.items():
        if not isinstance(raw_key, tuple) or len(raw_key) != 4:
            failures.append({"reason": "declared_movement_key_invalid", "key": repr(raw_key)})
            continue
        key = tuple(str(value) for value in raw_key)
        if not key[0] or not key[1] or not key[2] or not key[3]:
            failures.append({"reason": "declared_movement_key_has_empty_value", "key": _connection_key_record(key)})
            continue
        shape = str(raw_shape).strip()
        if key in declared_shapes:
            failures.append({"reason": "declared_movement_key_duplicate", "key": _connection_key_record(key)})
            continue
        declared_shapes[key] = shape

    source_connections = external_connections(source_root)
    candidate_connections = external_connections(candidate_root)
    duplicate_source_keys = sorted(key for key, values in source_connections.items() if len(values) != 1)
    duplicate_candidate_keys = sorted(key for key, values in candidate_connections.items() if len(values) != 1)
    if duplicate_source_keys or duplicate_candidate_keys:
        failures.append(
            {
                "reason": "external_movement_key_not_unique",
                "source_keys": [_connection_key_record(key) for key in duplicate_source_keys],
                "candidate_keys": [_connection_key_record(key) for key in duplicate_candidate_keys],
            }
        )
    source_keys = set(source_connections)
    candidate_keys = set(candidate_connections)
    declared_keys = set(declared_shapes)
    added_keys = candidate_keys - source_keys
    removed_keys = source_keys - candidate_keys
    if added_keys != declared_keys or removed_keys:
        failures.append(
            {
                "reason": "external_movement_delta_not_declared",
                "declared_additions": [_connection_key_record(key) for key in sorted(declared_keys)],
                "actual_additions": [_connection_key_record(key) for key in sorted(added_keys)],
                "actual_removals": [_connection_key_record(key) for key in sorted(removed_keys)],
            }
        )

    source_lane_index = lane_index(source_root)
    candidate_lane_index = lane_index(candidate_root)

    def target_via_lane(
        connection: ET.Element,
        lanes: dict[str, list[ET.Element]],
        *,
        key: tuple[str, str, str, str],
        side: str,
    ) -> ET.Element | None:
        via_lane_id = connection.attrib.get("via", "")
        matches = lanes.get(via_lane_id, []) if via_lane_id.startswith(internal_prefix) else []
        if len(matches) != 1:
            failures.append(
                {
                    "reason": "target_owned_via_lane_not_unique",
                    "side": side,
                    "key": _connection_key_record(key),
                    "via_lane_id": via_lane_id,
                    "lane_count": len(matches),
                }
            )
            return None
        return matches[0]

    source_target_keys = {
        key
        for key, values in source_connections.items()
        if len(values) == 1 and values[0].attrib.get("via", "").startswith(internal_prefix)
    }
    candidate_target_keys = {
        key
        for key, values in candidate_connections.items()
        if len(values) == 1 and values[0].attrib.get("via", "").startswith(internal_prefix)
    }
    if candidate_target_keys != source_target_keys | declared_keys:
        failures.append(
            {
                "reason": "target_junction_movement_ownership_mismatch",
                "source_keys": [_connection_key_record(key) for key in sorted(source_target_keys)],
                "candidate_keys": [_connection_key_record(key) for key in sorted(candidate_target_keys)],
                "declared_additions": [_connection_key_record(key) for key in sorted(declared_keys)],
            }
        )

    lane_updates: list[tuple[ET.Element, dict[str, str], dict[str, object]]] = []
    target_via_ids: set[str] = set()
    for key in sorted(source_target_keys):
        if len(source_connections.get(key, [])) != 1 or len(candidate_connections.get(key, [])) != 1:
            continue
        source_lane = target_via_lane(source_connections[key][0], source_lane_index, key=key, side="source")
        candidate_lane = target_via_lane(candidate_connections[key][0], candidate_lane_index, key=key, side="candidate")
        if source_lane is None or candidate_lane is None:
            continue
        via_lane_id = candidate_connections[key][0].attrib.get("via", "")
        if via_lane_id in target_via_ids:
            failures.append(
                {
                    "reason": "candidate_via_lane_reused_by_multiple_movements",
                    "key": _connection_key_record(key),
                    "via_lane_id": via_lane_id,
                }
            )
            continue
        target_via_ids.add(via_lane_id)
        attrs = {attr: source_lane.attrib.get(attr, "") for attr in ("speed", "length", "shape")}
        parsed_shape = strict_polyline(attrs["shape"])
        try:
            valid_scalars = all(math.isfinite(float(attrs[attr])) and float(attrs[attr]) > 0 for attr in ("speed", "length"))
        except ValueError:
            valid_scalars = False
        if parsed_shape is None or not valid_scalars:
            failures.append(
                {
                    "reason": "accepted_source_via_lane_geometry_invalid",
                    "key": _connection_key_record(key),
                    "via_lane_id": source_connections[key][0].attrib.get("via", ""),
                }
            )
            continue
        lane_updates.append(
            (
                candidate_lane,
                attrs,
                {
                    "kind": "existing",
                    "key": _connection_key_record(key),
                    "source_via_lane_id": source_connections[key][0].attrib.get("via", ""),
                    "candidate_via_lane_id": via_lane_id,
                },
            )
        )

    for key in sorted(declared_keys):
        if len(candidate_connections.get(key, [])) != 1:
            continue
        connection = candidate_connections[key][0]
        candidate_lane = target_via_lane(connection, candidate_lane_index, key=key, side="candidate")
        if candidate_lane is None:
            continue
        via_lane_id = connection.attrib.get("via", "")
        if via_lane_id in target_via_ids:
            failures.append(
                {
                    "reason": "candidate_via_lane_reused_by_multiple_movements",
                    "key": _connection_key_record(key),
                    "via_lane_id": via_lane_id,
                }
            )
            continue
        target_via_ids.add(via_lane_id)
        parsed = strict_polyline(declared_shapes[key])
        from_lane = external_lane(candidate_root, key[0], key[2])
        to_lane = external_lane(candidate_root, key[1], key[3])
        from_shape = strict_polyline(from_lane.attrib.get("shape", "")) if from_lane is not None else None
        to_shape = strict_polyline(to_lane.attrib.get("shape", "")) if to_lane is not None else None
        if parsed is None or from_shape is None or to_shape is None:
            failures.append(
                {
                    "reason": "declared_movement_or_external_lane_shape_invalid",
                    "key": _connection_key_record(key),
                }
            )
            continue
        points, length = parsed
        expected_start = from_shape[0][-1]
        expected_end = to_shape[0][0]
        if points[0] != expected_start or points[-1] != expected_end:
            failures.append(
                {
                    "reason": "declared_movement_shape_endpoint_mismatch",
                    "key": _connection_key_record(key),
                    "provided_start": list(points[0]),
                    "expected_start": list(expected_start),
                    "provided_end": list(points[-1]),
                    "expected_end": list(expected_end),
                }
            )
            continue
        lane_updates.append(
            (
                candidate_lane,
                {"shape": declared_shapes[key], "length": f"{length:.12g}"},
                {
                    "kind": "added",
                    "key": _connection_key_record(key),
                    "candidate_via_lane_id": via_lane_id,
                    "polyline_length": length,
                },
            )
        )

    if failures:
        return fail("scoped internal movement validation failed", failures)

    invariants_before = invariant_hashes(candidate_root)
    mutations: list[dict[str, object]] = []
    for lane, attrs, record in lane_updates:
        before = {attr: lane.attrib.get(attr) for attr in attrs}
        for attr, value in attrs.items():
            lane.set(attr, value)
        mutations.append({**record, "before": before, "after": attrs})
    candidate_junction.set("shape", source_junction.attrib.get("shape", ""))
    if "customShape" in source_junction.attrib:
        candidate_junction.set("customShape", source_junction.attrib["customShape"])
    else:
        candidate_junction.attrib.pop("customShape", None)

    invariants_after = invariant_hashes(candidate_root)
    changed_invariants = sorted(
        key for key, before in invariants_before.items() if invariants_after.get(key) != before
    )
    if changed_invariants:
        return fail(
            "scoped internal movement repair changed immutable network semantics",
            [{"reason": "immutable_semantic_hash_changed", "fields": changed_invariants}],
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="    ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    source_sha256_after = hashlib.sha256(source_net_file.read_bytes()).hexdigest()
    candidate_sha256_after = hashlib.sha256(candidate_net_file.read_bytes()).hexdigest()
    source_mutated = source_sha256_after != source_sha256
    candidate_mutated = candidate_sha256_after != candidate_sha256
    status = "pass" if not source_mutated and not candidate_mutated else "fail"
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "promotion_status": "review_required",
        "source_net_file": str(source_net_file),
        "source_sha256": source_sha256,
        "source_sha256_after": source_sha256_after,
        "source_network_mutation": source_mutated,
        "candidate_net_file": str(candidate_net_file),
        "candidate_sha256": candidate_sha256,
        "candidate_sha256_after": candidate_sha256_after,
        "candidate_network_mutation": candidate_mutated,
        "output_file": str(output_file),
        "output_sha256": hashlib.sha256(output_file.read_bytes()).hexdigest(),
        "junction_id": junction_id,
        "declared_added_movement_count": len(declared_keys),
        "reanchored_existing_movement_count": len(source_target_keys),
        "reanchored_added_movement_count": len(declared_keys),
        "mutation_count": len(mutations),
        "mutations": mutations,
        "junction_shape": source_junction.attrib.get("shape", ""),
        "junction_custom_shape": source_junction.attrib.get("customShape"),
        "immutable_hashes_before": invariants_before,
        "immutable_hashes_after": invariants_after,
        "failure_count": 0,
        "failures": [],
        "policy": (
            "reanchor only the target normal junction's uniquely owned internal movement lanes; "
            "preserve external lanes, connections, TLS, and request matrices"
        ),
    }


def write_authorized_lane_transition_junction_shapes(
    *,
    candidate_net_file: Path,
    output_file: Path,
    junction_ids: set[str],
    evidence_net_file: Path | None = None,
    excluded_branch_edge_ids_by_junction: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    """Tighten only evidence-authorized linear lane-transition junctions.

    A human-cleaned network normally keeps a real lane-drop or lane-gain node
    separate from the upstream conflict core.  The node polygon can still be
    over-wide after OSM import, so this controller replaces only its ``shape``
    with the convex hull of the adjacent lane endpoints.  Edge geometry,
    lane cardinality, connections, via lanes, and TLS programs are immutable.

    ``evidence_net_file`` should be the pre-rebuild OSM/SUMO baseline.  It is
    used to prove that the selected cell was already a one-in/one-out straight
    road transition before a teacher replay changed boundary edge metadata.
    Selection remains explicit: this writer never scans the whole network and
    never applies SUMO's global ``--junctions.minimal-shape`` option.
    """

    missing = [
        str(path)
        for path in (candidate_net_file, evidence_net_file)
        if path is not None and not path.exists()
    ]
    if missing:
        return _failure(f"missing input file(s): {', '.join(missing)}")
    if not junction_ids:
        return _failure("at least one evidence-authorized junction id is required")

    candidate_net_file = candidate_net_file.resolve()
    output_file = output_file.resolve()
    evidence_net_file = evidence_net_file.resolve() if evidence_net_file is not None else candidate_net_file
    source_sha256 = hashlib.sha256(candidate_net_file.read_bytes()).hexdigest()
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    evidence_root = ET.parse(evidence_net_file).getroot()
    excluded_branch_edge_ids_by_junction = excluded_branch_edge_ids_by_junction or {}

    topology_before = _junction_shape_repair_topology_sha256(candidate_root)
    repairs: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    all_edge_ids = {
        edge.attrib.get("id", "")
        for edge in candidate_root.findall("edge")
        if edge.attrib.get("id")
    }
    for junction_id in sorted(str(value) for value in junction_ids if str(value)):
        candidate_estimate = _estimate_linear_lane_transition_shape(candidate_root, junction_id)
        evidence_estimate = _estimate_linear_lane_transition_shape(evidence_root, junction_id)
        excluded_branch_edge_ids = sorted(
            {
                str(value)
                for value in excluded_branch_edge_ids_by_junction.get(junction_id, [])
                if str(value)
            }
        )
        present_excluded_branch_edge_ids = sorted(set(excluded_branch_edge_ids) & all_edge_ids)
        if candidate_estimate.get("status") != "pass":
            failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "candidate_not_linear_lane_transition",
                    "estimate": candidate_estimate,
                }
            )
            continue
        if evidence_estimate.get("status") != "pass":
            failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "evidence_not_linear_lane_transition",
                    "estimate": evidence_estimate,
                }
            )
            continue
        candidate_edges = (
            candidate_estimate.get("incoming_edge_id"),
            candidate_estimate.get("outgoing_edge_id"),
        )
        evidence_edges = (
            evidence_estimate.get("incoming_edge_id"),
            evidence_estimate.get("outgoing_edge_id"),
        )
        if candidate_edges != evidence_edges:
            failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "candidate_evidence_boundary_edge_mismatch",
                    "candidate_edges": candidate_edges,
                    "evidence_edges": evidence_edges,
                }
            )
            continue
        if present_excluded_branch_edge_ids:
            failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "declared_excluded_branch_is_present_in_candidate",
                    "edge_ids": present_excluded_branch_edge_ids,
                }
            )
            continue

        junction = candidate_root.find(f"junction[@id='{junction_id}']")
        if junction is None:  # defensive; the estimator already checks this
            failures.append({"junction_id": junction_id, "reason": "junction_not_found"})
            continue
        old_shape = junction.attrib.get("shape", "")
        new_shape = str(candidate_estimate["estimated_shape"])
        junction.set("shape", new_shape)
        junction.set("customShape", "true")
        repairs.append(
            {
                "junction_id": junction_id,
                "old_shape": old_shape,
                "new_shape": new_shape,
                "polygon_area_m2": candidate_estimate["polygon_area_m2"],
                "incoming_edge_id": candidate_estimate["incoming_edge_id"],
                "outgoing_edge_id": candidate_estimate["outgoing_edge_id"],
                "incoming_lane_count": candidate_estimate["incoming_lane_count"],
                "outgoing_lane_count": candidate_estimate["outgoing_lane_count"],
                "straight_connection_signatures": candidate_estimate["straight_connection_signatures"],
                "evidence_road_identity": evidence_estimate["road_identity"],
                "declared_excluded_branch_edge_ids": excluded_branch_edge_ids,
            }
        )

    if failures:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "candidate_net_file": str(candidate_net_file),
            "evidence_net_file": str(evidence_net_file),
            "output_file": str(output_file),
            "requested_junction_ids": sorted(junction_ids),
            "repair_count": len(repairs),
            "failure_count": len(failures),
            "failures": failures,
            "policy": "fail closed; no output written when any selected transition lacks evidence",
        }

    topology_after = _junction_shape_repair_topology_sha256(candidate_root)
    if topology_before != topology_after:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reason": "junction_shape_repair_changed_network_topology",
            "topology_sha256_before": topology_before,
            "topology_sha256_after": topology_after,
        }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="    ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    source_sha256_after = hashlib.sha256(candidate_net_file.read_bytes()).hexdigest()
    status = "pass" if source_sha256_after == source_sha256 else "fail"
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "candidate_net_file": str(candidate_net_file),
        "candidate_sha256": source_sha256,
        "candidate_sha256_after": source_sha256_after,
        "source_network_mutation": source_sha256_after != source_sha256,
        "evidence_net_file": str(evidence_net_file),
        "output_file": str(output_file),
        "output_sha256": hashlib.sha256(output_file.read_bytes()).hexdigest(),
        "topology_sha256_before": topology_before,
        "topology_sha256_after": topology_after,
        "repair_count": len(repairs),
        "repairs": repairs,
        "failure_count": 0,
        "failures": [],
        "policy": (
            "Ingolstadt-style boundary repair: retain the linear lane transition and all movements; "
            "replace only the authorized junction polygon with the adjacent-lane endpoint hull"
        ),
    }


def write_authorized_junction_shapes_from_reference(
    *,
    candidate_net_file: Path,
    reference_net_file: Path,
    output_file: Path,
    junction_ids: set[str],
) -> dict[str, object]:
    """Copy only explicitly authorized junction polygons from a reference net.

    The reference must describe the same external edges, lanes, movements,
    junction owners, and TLS programs.  Geometry-only values that SUMO may
    recalculate (lane ``shape``/``length``, junction ``shape``/``intLanes``,
    connection ``via``/``state``) are deliberately excluded from that
    reference identity check.  The output still preserves every candidate
    edge, lane, connection, internal artifact, and TLS byte-for-byte at the
    XML attribute level; only the selected normal junction ``shape`` and
    ``customShape`` attributes may change.

    This is a scoped controller primitive, not an automatic topology repair.
    It never scans for targets and does not authorize joins or movements.
    """

    missing = [
        str(path)
        for path in (candidate_net_file, reference_net_file)
        if not path.exists()
    ]
    if missing:
        return _failure(f"missing input file(s): {', '.join(missing)}")
    requested_junction_ids = sorted({str(value) for value in junction_ids if str(value)})
    if not requested_junction_ids:
        return _failure("at least one evidence-authorized junction id is required")
    if any(junction_id.startswith(":") for junction_id in requested_junction_ids):
        return _failure("internal junction ids are not valid shape-copy targets")

    candidate_net_file = candidate_net_file.resolve()
    reference_net_file = reference_net_file.resolve()
    output_file = output_file.resolve()
    if output_file in {candidate_net_file, reference_net_file}:
        return _failure("output file must differ from candidate and reference inputs")
    candidate_sha256 = hashlib.sha256(candidate_net_file.read_bytes()).hexdigest()
    reference_sha256 = hashlib.sha256(reference_net_file.read_bytes()).hexdigest()
    candidate_tree = ET.parse(candidate_net_file)
    candidate_root = candidate_tree.getroot()
    reference_root = ET.parse(reference_net_file).getroot()

    candidate_junctions = {
        junction.attrib["id"]: junction
        for junction in candidate_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    reference_junctions = {
        junction.attrib["id"]: junction
        for junction in reference_root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib["id"].startswith(":")
    }
    candidate_edge_lane_signature = edge_lane_signature(candidate_net_file)
    reference_edge_lane_signature = edge_lane_signature(reference_net_file)
    connection_audit = audit_alias_normalized_connections(
        candidate_net_file,
        reference_net_file,
    )
    candidate_tls_sha256 = _junction_shape_tls_sha256(candidate_root)
    reference_tls_sha256 = _junction_shape_tls_sha256(reference_root)
    reference_failures: list[dict[str, object]] = []
    if candidate_edge_lane_signature != reference_edge_lane_signature:
        reference_failures.append(
            {
                "reason": "external_edge_lane_signature_mismatch",
                "candidate": candidate_edge_lane_signature,
                "reference": reference_edge_lane_signature,
            }
        )
    connection_failure_fields = (
        "normal_missing_count",
        "normal_extra_count",
        "controlled_missing_count",
        "controlled_extra_count",
    )
    if any(int(connection_audit.get(field, 0) or 0) for field in connection_failure_fields):
        reference_failures.append(
            {
                "reason": "external_movement_signature_mismatch",
                "connection_audit": connection_audit,
            }
        )
    if candidate_tls_sha256 != reference_tls_sha256:
        reference_failures.append(
            {
                "reason": "tls_program_signature_mismatch",
                "candidate_tls_sha256": candidate_tls_sha256,
                "reference_tls_sha256": reference_tls_sha256,
            }
        )
    selected_junction_identity_attrs = ("id", "type", "x", "y", "tl", "incLanes")
    for junction_id in requested_junction_ids:
        candidate_junction = candidate_junctions.get(junction_id)
        reference_junction = reference_junctions.get(junction_id)
        if candidate_junction is None or reference_junction is None:
            reference_failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "junction_missing_from_candidate_or_reference",
                    "candidate_present": candidate_junction is not None,
                    "reference_present": reference_junction is not None,
                }
            )
            continue
        candidate_identity = {
            attr: candidate_junction.attrib.get(attr, "")
            for attr in selected_junction_identity_attrs
        }
        reference_identity = {
            attr: reference_junction.attrib.get(attr, "")
            for attr in selected_junction_identity_attrs
        }
        if candidate_identity != reference_identity:
            reference_failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "selected_junction_identity_mismatch",
                    "candidate": candidate_identity,
                    "reference": reference_identity,
                }
            )
    if reference_failures:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reason": "candidate_reference_topology_mismatch",
            "candidate_net_file": str(candidate_net_file),
            "candidate_sha256": candidate_sha256,
            "reference_net_file": str(reference_net_file),
            "reference_sha256": reference_sha256,
            "output_file": str(output_file),
            "candidate_edge_lane_signature": candidate_edge_lane_signature,
            "reference_edge_lane_signature": reference_edge_lane_signature,
            "connection_audit": connection_audit,
            "candidate_tls_sha256": candidate_tls_sha256,
            "reference_tls_sha256": reference_tls_sha256,
            "reference_failure_count": len(reference_failures),
            "reference_failures": reference_failures,
            "policy": "fail closed; reference geometry must come from the same external topology",
        }

    failures: list[dict[str, object]] = []
    repairs: list[dict[str, object]] = []
    topology_before = _junction_shape_repair_topology_sha256(candidate_root)
    for junction_id in requested_junction_ids:
        candidate_junction = candidate_junctions.get(junction_id)
        reference_junction = reference_junctions.get(junction_id)
        if candidate_junction is None or reference_junction is None:  # proven above
            raise AssertionError("selected junction identity validation was bypassed")
        reference_shape = str(reference_junction.attrib.get("shape", "")).strip()
        reference_points = _shape_points(reference_shape)
        if len(set(reference_points)) < 2 or any(
            not math.isfinite(value) for point in reference_points for value in point
        ):
            failures.append(
                {
                    "junction_id": junction_id,
                    "reason": "reference_junction_shape_has_fewer_than_two_points",
                    "reference_shape": reference_shape,
                }
            )
            continue
        old_shape = str(candidate_junction.attrib.get("shape", ""))
        old_custom_shape = candidate_junction.attrib.get("customShape")
        candidate_junction.set("shape", reference_shape)
        candidate_junction.set("customShape", "true")
        repairs.append(
            {
                "junction_id": junction_id,
                "old_shape": old_shape,
                "new_shape": reference_shape,
                "old_custom_shape": old_custom_shape,
                "new_custom_shape": "true",
            }
        )

    if failures:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "candidate_net_file": str(candidate_net_file),
            "reference_net_file": str(reference_net_file),
            "output_file": str(output_file),
            "requested_junction_ids": requested_junction_ids,
            "repair_count": len(repairs),
            "failure_count": len(failures),
            "failures": failures,
            "policy": "fail closed; no output written when any selected junction lacks a usable shape",
        }

    topology_after = _junction_shape_repair_topology_sha256(candidate_root)
    if topology_before != topology_after:
        return {
            "status": "fail",
            "claim_status": "construction-invalid",
            "reason": "junction_shape_copy_changed_network_topology",
            "topology_sha256_before": topology_before,
            "topology_sha256_after": topology_after,
        }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(candidate_root, space="    ")
    candidate_tree.write(output_file, encoding="utf-8", xml_declaration=True)
    candidate_sha256_after = hashlib.sha256(candidate_net_file.read_bytes()).hexdigest()
    reference_sha256_after = hashlib.sha256(reference_net_file.read_bytes()).hexdigest()
    source_network_mutation = (
        candidate_sha256_after != candidate_sha256 or reference_sha256_after != reference_sha256
    )
    status = "pass" if not source_network_mutation else "fail"
    return {
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "construction-invalid",
        "promotion_status": "review_required",
        "candidate_net_file": str(candidate_net_file),
        "candidate_sha256": candidate_sha256,
        "candidate_sha256_after": candidate_sha256_after,
        "reference_net_file": str(reference_net_file),
        "reference_sha256": reference_sha256,
        "reference_sha256_after": reference_sha256_after,
        "source_network_mutation": source_network_mutation,
        "output_file": str(output_file),
        "output_sha256": hashlib.sha256(output_file.read_bytes()).hexdigest(),
        "requested_junction_ids": requested_junction_ids,
        "candidate_edge_lane_signature": candidate_edge_lane_signature,
        "reference_edge_lane_signature": reference_edge_lane_signature,
        "connection_audit": connection_audit,
        "candidate_tls_sha256": candidate_tls_sha256,
        "reference_tls_sha256": reference_tls_sha256,
        "topology_sha256_before": topology_before,
        "topology_sha256_after": topology_after,
        "repair_count": len(repairs),
        "repairs": repairs,
        "failure_count": 0,
        "failures": [],
        "policy": (
            "copy only explicitly authorized normal-junction shapes from a hash-bound "
            "same-topology reference; preserve every candidate edge, lane, movement, and TLS"
        ),
    }
