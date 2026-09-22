from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.road_semantics import classify_turn_from_signed_delta

from .geometry import normalize_signed_angle


def bind_topology_candidate_to_dag(
    *,
    candidate_net: Path,
    candidate_plan: Mapping[str, Any],
    physical_cell: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any],
    candidate_dag: Mapping[str, Any],
    tls_ownership: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind boundary-to-boundary lane paths to one topology DAG arm.

    Unlike the single-junction v3 binder, this follows direct external-lane
    connections across any number of preserved SUMO junctions.  Raw internal
    edge IDs and connection indices are evidence only and never identity.
    """

    source = candidate_net.resolve(strict=True)
    root = ET.parse(source).getroot()
    topology = str(candidate_plan["topology_hypothesis"])
    target_junction_ids = set(map(str, candidate_plan["target_junction_ids"]))
    expected_controller_id = str(candidate_plan["target_controller_id"])
    approaches = {
        str(item["physical_approach_id"]): item
        for item in physical_cell.get("physical_approaches", ())
    }
    ports = {
        str(item["boundary_port_id"]): item
        for item in physical_cell.get("raw_boundary_ports", ())
    }
    external_edges = _external_edges(root)
    boundary = _bind_boundary_edges(
        approaches=approaches,
        ports=ports,
        external_edges=external_edges,
        target_junction_ids=target_junction_ids,
    )
    adjacency = _lane_adjacency(root, external_edges)
    interior_edge_ids = {
        edge_id
        for edge_id, edge in external_edges.items()
        if edge.attrib.get("from") in target_junction_ids
        and edge.attrib.get("to") in target_junction_ids
    }
    allowed_edge_ids = (
        interior_edge_ids
        | {
            record["edge_id"]
            for direction in ("incoming", "outgoing")
            for records in boundary[direction].values()
            for record in records
        }
    )
    actual_records, ambiguous_paths = _enumerate_cell_movements(
        approaches=approaches,
        boundary=boundary,
        adjacency=adjacency,
        allowed_edge_ids=allowed_edge_ids,
        expected_controller_id=expected_controller_id,
    )
    actual_ids = {str(item["stable_movement_id"]) for item in actual_records}
    duplicate_actual_ids = sorted(
        movement_id
        for movement_id in actual_ids
        if sum(
            item["stable_movement_id"] == movement_id for item in actual_records
        )
        > 1
    )

    variant_matches = []
    for variant in movement_hypotheses.get("variants", ()):
        expected_ids = {
            str(item["stable_movement_id"])
            for item in variant.get("atomic_movements", ())
        }
        variant_matches.append(
            {
                "variant_id": variant.get("variant_id"),
                "method": variant.get("method"),
                "status": "exact" if actual_ids == expected_ids else "review_required",
                "expected_movement_count": len(expected_ids),
                "actual_movement_count": len(actual_ids),
                "missing_movement_ids": sorted(expected_ids - actual_ids),
                "unexpected_movement_ids": sorted(actual_ids - expected_ids),
            }
        )
    exact_variant_ids = sorted(
        str(item["variant_id"])
        for item in variant_matches
        if item["status"] == "exact"
    )
    semantic_class_ids = sorted(
        {
            str(node["semantic_class_id"])
            for node in candidate_dag.get("nodes", ())
            if node.get("node_kind") == "movement_semantic_class"
            and set(map(str, node.get("movement_variant_ids", ())))
            & set(exact_variant_ids)
        }
    )
    bound_candidate_ids = sorted(
        str(node["candidate_id"])
        for node in candidate_dag.get("nodes", ())
        if node.get("node_kind") == "candidate_variant"
        and node.get("topology_hypothesis") == topology
        and node.get("semantic_class_id") in semantic_class_ids
    )

    expected_dag_candidate_id = str(candidate_plan["candidate_dag_node_id"])
    boundary_findings = list(boundary["findings"])
    wrong_controller_paths = sorted(
        item["stable_movement_id"]
        for item in actual_records
        if item["controller_binding_status"] != "pass"
    )
    structural_findings: list[str] = []
    if boundary_findings:
        structural_findings.append("boundary_ports_do_not_bind_unique_external_edges")
    if not actual_records:
        structural_findings.append("candidate_has_no_boundary_to_boundary_movements")
    if ambiguous_paths:
        structural_findings.append("candidate_movement_lane_path_is_ambiguous")
    if duplicate_actual_ids:
        structural_findings.append("duplicate_stable_candidate_movement")
    if wrong_controller_paths:
        structural_findings.append("movement_path_controller_binding_disagrees")
    if tls_ownership.get("status") != "pass":
        structural_findings.append("tls_ownership_rebuild_not_verified")
    if bound_candidate_ids != [expected_dag_candidate_id]:
        structural_findings.append("materialized_candidate_does_not_bind_declared_dag_arm")

    semantic_findings: list[str] = []
    if not exact_variant_ids:
        semantic_findings.append("candidate_matches_no_movement_variant")
    if movement_hypotheses.get("variant_comparison", {}).get("status") != "exact":
        semantic_findings.append("source_evidence_movement_variants_disagree")
    if movement_hypotheses.get("nested_restriction_ids"):
        semantic_findings.append("nested_turn_restrictions_unresolved")

    payload = {
        "schema": "torii.topology-cell-candidate-binding/v1",
        "candidate_net": {
            "path": str(source),
            "sha256": _file_sha256(source),
            "normalized_sha256": normalized_net_sha256(source),
        },
        "candidate_plan_id": candidate_plan["candidate_plan_id"],
        "candidate_dag_id": candidate_dag.get("candidate_dag_id"),
        "topology_hypothesis": topology,
        "target_junction_ids": sorted(target_junction_ids),
        "expected_controller_id": expected_controller_id,
        "binding_status": "fail" if structural_findings else "pass",
        "semantic_disposition": "review" if semantic_findings else "suggest",
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "bound_candidate_id": (
            bound_candidate_ids[0] if len(bound_candidate_ids) == 1 else None
        ),
        "bound_semantic_class_ids": semantic_class_ids,
        "exact_movement_variant_ids": exact_variant_ids,
        "actual_stable_movement_ids": sorted(actual_ids),
        "movement_count": len(actual_records),
        "movement_records": sorted(
            actual_records,
            key=lambda item: item["stable_movement_id"],
        ),
        "boundary_edge_binding": boundary,
        "interior_external_edge_ids": sorted(interior_edge_ids),
        "allowed_path_edge_ids": sorted(allowed_edge_ids),
        "ambiguous_paths": ambiguous_paths,
        "duplicate_stable_movement_ids": duplicate_actual_ids,
        "wrong_controller_movement_ids": wrong_controller_paths,
        "variant_matches": variant_matches,
        "structural_findings": structural_findings,
        "semantic_findings": semantic_findings,
        "claim_boundary": (
            "A pass proves exact boundary-to-boundary lane-path and declared "
            "controller coverage for one DAG arm. It does not prove that arm "
            "matches the real road or field signal timing."
        ),
    }
    identity_payload = {
        **payload,
        "candidate_net": {
            "normalized_sha256": payload["candidate_net"]["normalized_sha256"]
        },
    }
    return {
        **payload,
        "binding_id": f"cell-binding-{_stable_digest(identity_payload)[:20]}",
    }


def _external_edges(root: ET.Element) -> dict[str, ET.Element]:
    return {
        str(edge.attrib["id"]): edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
        and not str(edge.attrib["id"]).startswith(":")
        and edge.attrib.get("function", "")
        not in {"internal", "crossing", "walkingarea"}
    }


def _bind_boundary_edges(
    *,
    approaches: Mapping[str, Mapping[str, Any]],
    ports: Mapping[str, Mapping[str, Any]],
    external_edges: Mapping[str, ET.Element],
    target_junction_ids: set[str],
) -> dict[str, Any]:
    incoming: dict[str, list[dict[str, Any]]] = {}
    outgoing: dict[str, list[dict[str, Any]]] = {}
    findings = []
    for approach_id, approach in sorted(approaches.items()):
        member_ports = [
            ports[str(port_id)]
            for port_id in approach.get("member_boundary_port_ids", ())
            if str(port_id) in ports
        ]
        incoming_records, incoming_port_findings = _bind_port_edges(
            member_ports=member_ports,
            external_edges=external_edges,
            target_junction_ids=target_junction_ids,
            direction="incoming",
        )
        outgoing_records, outgoing_port_findings = _bind_port_edges(
            member_ports=member_ports,
            external_edges=external_edges,
            target_junction_ids=target_junction_ids,
            direction="outgoing",
        )
        findings.extend(
            {
                **finding,
                "physical_approach_id": approach_id,
            }
            for finding in (*incoming_port_findings, *outgoing_port_findings)
        )
        incoming[approach_id] = incoming_records
        outgoing[approach_id] = outgoing_records
        expected_incoming_lanes = int(approach.get("incoming_lane_count", 0))
        expected_outgoing_lanes = int(approach.get("outgoing_lane_count", 0))
        if expected_incoming_lanes and sum(
            int(item["lane_count"]) for item in incoming_records
        ) != expected_incoming_lanes:
            findings.append(
                {
                    "category": "incoming_boundary_edge_binding_mismatch",
                    "physical_approach_id": approach_id,
                    "expected_lane_count": expected_incoming_lanes,
                    "records": incoming_records,
                }
            )
        if expected_outgoing_lanes and sum(
            int(item["lane_count"]) for item in outgoing_records
        ) != expected_outgoing_lanes:
            findings.append(
                {
                    "category": "outgoing_boundary_edge_binding_mismatch",
                    "physical_approach_id": approach_id,
                    "expected_lane_count": expected_outgoing_lanes,
                    "records": outgoing_records,
                }
            )
    return {
        "status": "pass" if not findings else "fail",
        "method": (
            "stable_boundary_port_identity_then_osm_way_and_target_orientation"
        ),
        "incoming": incoming,
        "outgoing": outgoing,
        "findings": findings,
    }


def _bind_port_edges(
    *,
    member_ports: list[Mapping[str, Any]],
    external_edges: Mapping[str, ET.Element],
    target_junction_ids: set[str],
    direction: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records_by_id: dict[str, dict[str, Any]] = {}
    findings = []
    lane_key = f"{direction}_lane_count"
    for port in member_ports:
        if int(port.get(lane_key, 0)) <= 0:
            continue
        candidates = _boundary_edge_records(
            external_edges,
            target_junction_ids=target_junction_ids,
            way_ids={str(port["way_id"])},
            direction=direction,
        )
        resolved = _resolve_port_boundary_records(
            candidates,
            port=port,
            direction=direction,
        )
        if len(resolved) != 1:
            findings.append(
                {
                    "category": f"{direction}_boundary_port_binding_mismatch",
                    "boundary_port_id": port.get("boundary_port_id"),
                    "outside_node_id": port.get("outside_node_id"),
                    "inside_node_id": port.get("inside_node_id"),
                    "way_id": port.get("way_id"),
                    "records": resolved,
                    "unfiltered_candidate_records": candidates,
                }
            )
            continue
        record = {
            **resolved[0],
            "boundary_port_id": port.get("boundary_port_id"),
        }
        records_by_id[str(record["edge_id"])] = record
    return (
        [records_by_id[key] for key in sorted(records_by_id)],
        findings,
    )


def _resolve_port_boundary_records(
    records: list[dict[str, Any]],
    *,
    port: Mapping[str, Any],
    direction: str,
) -> list[dict[str, Any]]:
    """Resolve repeated OSM way IDs with the stable boundary-port endpoints.

    A single OSM way can cross both sides of a multi-junction cell.  Way ID
    alone therefore cannot identify a port.  The outside OSM node is the
    strongest discriminator and survives both split and joined candidates;
    the inside node is a conservative fallback for preserved-node variants.
    """

    if len(records) <= 1:
        return records
    outside_node_id = str(port.get("outside_node_id", ""))
    outside_field = "from_junction_id" if direction == "incoming" else "to_junction_id"
    outside_matches = [
        item for item in records if str(item.get(outside_field, "")) == outside_node_id
    ]
    if len(outside_matches) == 1:
        return outside_matches
    inside_node_id = str(port.get("inside_node_id", ""))
    inside_field = "to_junction_id" if direction == "incoming" else "from_junction_id"
    inside_matches = [
        item for item in records if str(item.get(inside_field, "")) == inside_node_id
    ]
    if len(inside_matches) == 1:
        return inside_matches
    return records


def _boundary_edge_records(
    external_edges: Mapping[str, ET.Element],
    *,
    target_junction_ids: set[str],
    way_ids: set[str],
    direction: str,
) -> list[dict[str, Any]]:
    records = []
    for edge_id, edge in external_edges.items():
        if _osm_way_id_from_edge(edge_id) not in way_ids:
            continue
        source_id = str(edge.attrib.get("from", ""))
        target_id = str(edge.attrib.get("to", ""))
        is_boundary = (
            target_id in target_junction_ids and source_id not in target_junction_ids
            if direction == "incoming"
            else source_id in target_junction_ids and target_id not in target_junction_ids
        )
        if not is_boundary:
            continue
        records.append(
            {
                "edge_id": edge_id,
                "osm_way_id": _osm_way_id_from_edge(edge_id),
                "from_junction_id": source_id,
                "to_junction_id": target_id,
                "lane_count": len(edge.findall("lane")),
            }
        )
    return sorted(records, key=lambda item: item["edge_id"])


def _lane_adjacency(
    root: ET.Element,
    external_edges: Mapping[str, ET.Element],
) -> dict[tuple[str, int], list[dict[str, Any]]]:
    adjacency: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for connection in root.findall("connection"):
        from_edge = str(connection.attrib.get("from", ""))
        to_edge = str(connection.attrib.get("to", ""))
        if from_edge not in external_edges or to_edge not in external_edges:
            continue
        try:
            from_lane = int(str(connection.attrib["fromLane"]))
            to_lane = int(str(connection.attrib["toLane"]))
        except (KeyError, ValueError):
            continue
        adjacency[(from_edge, from_lane)].append(
            {
                "from_edge_id": from_edge,
                "from_lane_index": from_lane,
                "to_edge_id": to_edge,
                "to_lane_index": to_lane,
                "via_lane_id": connection.attrib.get("via"),
                "controller_id": connection.attrib.get("tl"),
                "link_index": connection.attrib.get("linkIndex"),
                "dir": connection.attrib.get("dir"),
            }
        )
    for records in adjacency.values():
        records.sort(
            key=lambda item: (
                item["to_edge_id"],
                item["to_lane_index"],
                str(item.get("via_lane_id", "")),
            )
        )
    return dict(adjacency)


def _enumerate_cell_movements(
    *,
    approaches: Mapping[str, Mapping[str, Any]],
    boundary: Mapping[str, Any],
    adjacency: Mapping[tuple[str, int], list[dict[str, Any]]],
    allowed_edge_ids: set[str],
    expected_controller_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = []
    ambiguous = []
    for source_id, source in sorted(approaches.items()):
        source_edges = boundary["incoming"].get(source_id, ())
        if len(source_edges) != 1:
            continue
        source_edge = source_edges[0]["edge_id"]
        for target_id, target in sorted(approaches.items()):
            if target_id == source_id:
                continue
            target_edges = boundary["outgoing"].get(target_id, ())
            if len(target_edges) != 1:
                continue
            target_edge = target_edges[0]["edge_id"]
            turn, signed_delta = _turn(source, target)
            for from_lane in range(int(source.get("incoming_lane_count", 0))):
                for to_lane in range(int(target.get("outgoing_lane_count", 0))):
                    paths = _find_lane_paths(
                        (source_edge, from_lane),
                        (target_edge, to_lane),
                        adjacency=adjacency,
                        allowed_edge_ids=allowed_edge_ids,
                    )
                    if not paths:
                        continue
                    movement_payload = {
                        "from_physical_approach_id": source_id,
                        "to_physical_approach_id": target_id,
                        "turn": turn,
                        "mode": "passenger",
                        "from_lane_index": from_lane,
                        "to_lane_index": to_lane,
                    }
                    stable_movement_id = (
                        f"movement-{_stable_digest(movement_payload)[:16]}"
                    )
                    if len(paths) > 1:
                        ambiguous.append(
                            {
                                "stable_movement_id": stable_movement_id,
                                "path_count": len(paths),
                                "paths": paths,
                            }
                        )
                    path = paths[0]
                    controlled = [
                        item for item in path["connections"] if item.get("controller_id")
                    ]
                    controller_ids = sorted(
                        {str(item["controller_id"]) for item in controlled}
                    )
                    controller_status = (
                        "pass"
                        if controlled
                        and controller_ids == [expected_controller_id]
                        and all(
                            str(item.get("link_index", "")).isdigit()
                            for item in controlled
                        )
                        else "fail"
                    )
                    records.append(
                        {
                            "stable_movement_id": stable_movement_id,
                            **movement_payload,
                            "signed_turn_delta_deg": round(signed_delta, 3),
                            "source_edge_id": source_edge,
                            "target_edge_id": target_edge,
                            "edge_ids": path["edge_ids"],
                            "lane_states": path["lane_states"],
                            "connections": path["connections"],
                            "controlled_connection_count": len(controlled),
                            "controller_ids": controller_ids,
                            "controller_binding_status": controller_status,
                        }
                    )
    return records, ambiguous


def _find_lane_paths(
    start: tuple[str, int],
    goal: tuple[str, int],
    *,
    adjacency: Mapping[tuple[str, int], list[dict[str, Any]]],
    allowed_edge_ids: set[str],
    maximum_paths: int = 2,
) -> list[dict[str, Any]]:
    maximum_hops = max(2, len(allowed_edge_ids) + 1)
    queue = deque([(start, [start], [])])
    paths = []
    shortest_hops: int | None = None
    while queue and len(paths) < maximum_paths:
        state, lane_states, connections = queue.popleft()
        hops = len(connections)
        if shortest_hops is not None and hops > shortest_hops:
            break
        if state == goal:
            shortest_hops = hops
            paths.append(
                {
                    "edge_ids": [item[0] for item in lane_states],
                    "lane_states": [
                        {"edge_id": edge_id, "lane_index": lane_index}
                        for edge_id, lane_index in lane_states
                    ],
                    "connections": connections,
                }
            )
            continue
        if hops >= maximum_hops:
            continue
        for connection in adjacency.get(state, ()):
            next_state = (
                str(connection["to_edge_id"]),
                int(connection["to_lane_index"]),
            )
            if next_state[0] not in allowed_edge_ids or next_state in lane_states:
                continue
            queue.append(
                (
                    next_state,
                    [*lane_states, next_state],
                    [*connections, connection],
                )
            )
    return paths


def _turn(
    source: Mapping[str, Any],
    target: Mapping[str, Any]
) -> tuple[str, float]:
    signed_delta = normalize_signed_angle(
        float(target["bearing_from_seed_deg"])
        - ((float(source["bearing_from_seed_deg"]) + 180.0) % 360.0)
    )
    return classify_turn_from_signed_delta(signed_delta), signed_delta


def _osm_way_id_from_edge(edge_id: str) -> str:
    value = edge_id[1:] if edge_id.startswith("-") else edge_id
    return value.split("#", 1)[0]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_digest(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
