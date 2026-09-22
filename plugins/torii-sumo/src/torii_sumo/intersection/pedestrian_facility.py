from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

from torii_sumo.corridor.enums import TrafficSide
from torii_sumo.corridor.ids import stable_id
from torii_sumo.corridor.pedestrian_row_oracle import (
    infer_source_row_class,
    make_source_row_bundle,
    make_source_row_observation,
)
from torii_sumo.road_semantics import filtered_osm_modes, is_osm_passenger_way

from .schema import OSMPatch, OSMWay


_PEDESTRIAN_SUPPORT_HIGHWAYS = frozenset(
    {
        "bridleway",
        "corridor",
        "cycleway",
        "footway",
        "path",
        "pedestrian",
        "steps",
    }
)
_GRADE_SEPARATION_KEYS = ("bridge", "tunnel", "layer")


def build_osm_pedestrian_facility_audits(
    patch: OSMPatch,
    *,
    crossing_node_ids: Iterable[str],
    traffic_side: str,
    source_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Build source-only crossing audits without consulting a SUMO model claim.

    The audit deliberately separates two questions: OSM may explicitly say that
    a crossing is signalized, while still lacking enough path topology to prove
    that a complete pedestrian facility can be materialized.  request/foes,
    generated internal lanes, and TLS programs are not inputs here.
    """

    normalized_side = TrafficSide(traffic_side)
    evidence_sha256 = source_sha256 or _patch_semantic_sha256(patch)
    audits = []
    for node_id in sorted(set(crossing_node_ids)):
        node = patch.nodes.get(node_id)
        if node is None or not _is_crossing_node(node.tags):
            continue
        audits.append(
            _build_osm_pedestrian_facility_audit(
                patch,
                crossing_node_id=node_id,
                traffic_side=normalized_side,
                source_sha256=evidence_sha256,
            )
        )
    return sorted(audits, key=lambda item: item["facility_id"])


def _build_osm_pedestrian_facility_audit(
    patch: OSMPatch,
    *,
    crossing_node_id: str,
    traffic_side: TrafficSide,
    source_sha256: str,
) -> dict[str, Any]:
    node = patch.nodes[crossing_node_id]
    support_arms = _incident_arms(
        patch,
        node_id=crossing_node_id,
        role="pedestrian-support",
    )
    vehicle_arms = _incident_arms(
        patch,
        node_id=crossing_node_id,
        role="vehicle-road",
    )
    support_way_ids = sorted({arm["way_id"] for arm in support_arms})
    vehicle_way_ids = sorted({arm["way_id"] for arm in vehicle_arms})
    stage_count = 2 if node.tags.get("crossing:island") == "yes" else 1
    is_signalized = node.tags.get("crossing") == "traffic_signals"
    observation = make_source_row_observation(
        source_kind="osm-tag",
        source_sha256=source_sha256,
        subject=f"osm-node/{crossing_node_id}",
        observed_value=json.dumps(
            {
                key: node.tags[key]
                for key in sorted(node.tags)
                if key == "highway"
                or key == "crossing"
                or key.startswith("crossing:")
                or key.startswith("traffic_signals:")
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        expected_answer_eligible=True,
    )
    source_bundle = make_source_row_bundle(
        crossing_node_id=f"osm-node:{crossing_node_id}",
        crossing_edge_ids=tuple(f"osm-way:{way_id}" for way_id in vehicle_way_ids),
        traffic_side=traffic_side,
        crossing_stage_count=stage_count,
        junction_control_kind="traffic-light" if is_signalized else "unknown",
        explicit_crossing_priority=None,
        source_status="complete" if is_signalized else "incomplete",
        observations=(observation,),
    )
    source_decision = infer_source_row_class(source_bundle)

    blockers: list[str] = []
    if len(support_arms) != 2:
        blockers.append("pedestrian_support_arm_count_not_two")
    if len(vehicle_arms) != 2:
        blockers.append("vehicle_road_arm_count_not_two")
    support_opposition = _opposition_deg(support_arms)
    vehicle_opposition = _opposition_deg(vehicle_arms)
    if support_opposition is not None and support_opposition < 135.0:
        blockers.append("pedestrian_support_arms_not_opposed")
    if vehicle_opposition is not None and vehicle_opposition < 135.0:
        blockers.append("vehicle_road_arms_not_opposed")
    crossing_angle = _axis_crossing_angle_deg(support_arms, vehicle_arms)
    if crossing_angle is not None and crossing_angle < 35.0:
        blockers.append("pedestrian_path_nearly_parallel_to_vehicle_road")
    grade_evidence = _grade_separation_evidence(
        patch,
        way_ids=(*support_way_ids, *vehicle_way_ids),
    )
    if grade_evidence:
        blockers.append("grade_separation_requires_review")
    if stage_count != 1:
        blockers.append("multi_stage_crossing_requires_review")
    if not is_signalized:
        blockers.append("crossing_control_not_explicitly_signalized")

    facility_identity = {
        "crossing_node_id": crossing_node_id,
        "support_way_ids": support_way_ids,
        "vehicle_way_ids": vehicle_way_ids,
        "traffic_side": traffic_side.value,
    }
    facility_id = stable_id("coverage", facility_identity)
    topology_evidence = {
        "support_arm_count": len(support_arms),
        "vehicle_arm_count": len(vehicle_arms),
        "support_arms": support_arms,
        "vehicle_arms": vehicle_arms,
        "support_opposition_deg": support_opposition,
        "vehicle_opposition_deg": vehicle_opposition,
        "axis_crossing_angle_deg": crossing_angle,
        "grade_separation_evidence": grade_evidence,
        "geometry_infers_right_of_way": False,
        "request_foes_fields_read": [],
    }
    audit_status = "review_ready" if not blockers else "blocked"
    audit_identity = {
        "facility_id": facility_id,
        "source_bundle_id": source_bundle.source_bundle_id,
        "source_decision_id": source_decision.decision_id,
        "topology_evidence": topology_evidence,
        "blockers": sorted(set(blockers)),
    }
    return {
        "schema": "torii.osm-pedestrian-facility-audit/v1",
        "audit_id": stable_id("finding", audit_identity),
        "facility_id": facility_id,
        "facility_identity_basis": facility_identity,
        "crossing_node_id": crossing_node_id,
        "location": {
            "lat": node.lat,
            "lon": node.lon,
            "local_x_m": node.x,
            "local_y_m": node.y,
        },
        "facility_kind": (
            "shared_pedestrian_bicycle_crossing"
            if any("bicycle" in arm["allowed_modes"] for arm in support_arms)
            else "pedestrian_crossing"
        ),
        "crossing_stage_count": stage_count,
        "source_row_bundle": source_bundle.model_dump(mode="json"),
        "source_row_decision": source_decision.model_dump(mode="json"),
        "topology_evidence": topology_evidence,
        "audit_status": audit_status,
        "blockers": sorted(set(blockers)),
        "next_required_gate": (
            "materialized_model_claim_geometry_and_runtime_audit"
            if audit_status == "review_ready"
            else "source_facility_topology_review"
        ),
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "OSM source evidence classifies control and path completeness only. "
            "It does not prove a generated SUMO crossing, request/foes relation, "
            "signal phase binding, field timing, or real-world safety."
        ),
    }


def _incident_arms(
    patch: OSMPatch,
    *,
    node_id: str,
    role: str,
) -> list[dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    node = patch.nodes[node_id]
    for way in patch.ways.values():
        if not _way_matches_role(way, role):
            continue
        for index, reference in enumerate(way.node_refs):
            if reference != node_id:
                continue
            neighbor_ids = []
            if index > 0:
                neighbor_ids.append(way.node_refs[index - 1])
            if index + 1 < len(way.node_refs):
                neighbor_ids.append(way.node_refs[index + 1])
            for neighbor_id in neighbor_ids:
                neighbor = patch.nodes.get(neighbor_id)
                if neighbor is None:
                    continue
                distance_m = math.hypot(
                    float(neighbor.x or 0.0) - float(node.x or 0.0),
                    float(neighbor.y or 0.0) - float(node.y or 0.0),
                )
                bearing = _bearing_deg(node, neighbor) if distance_m > 0 else None
                identity = {
                    "crossing_node_id": node_id,
                    "way_id": way.id,
                    "neighbor_node_id": neighbor_id,
                    "role": role,
                }
                records[(way.id, neighbor_id)] = {
                    "arm_id": stable_id("approach", identity),
                    "way_id": way.id,
                    "neighbor_node_id": neighbor_id,
                    "role": role,
                    "highway": way.tags.get("highway", ""),
                    "allowed_modes": sorted(_allowed_modes(way)),
                    "distance_m": round(distance_m, 6),
                    "bearing_from_crossing_deg": (round(bearing, 6) if bearing is not None else None),
                }
    return sorted(records.values(), key=lambda item: item["arm_id"])


def _way_matches_role(way: OSMWay, role: str) -> bool:
    highway = way.tags.get("highway", "")
    modes = _allowed_modes(way)
    if role == "pedestrian-support":
        return highway in _PEDESTRIAN_SUPPORT_HIGHWAYS and "pedestrian" in modes
    if role == "vehicle-road":
        return is_osm_passenger_way(way.tags)
    raise ValueError(f"Unknown pedestrian facility arm role: {role}")


def _allowed_modes(way: OSMWay) -> set[str]:
    highway = way.tags.get("highway", "")
    if is_osm_passenger_way(way.tags):
        return filtered_osm_modes(way.tags, {"passenger"})
    if highway == "cycleway":
        modes = {"bicycle"}
        if way.tags.get("foot") in {"yes", "designated", "permissive"}:
            modes.add("pedestrian")
        return filtered_osm_modes(way.tags, modes)
    if highway in {"corridor", "footway", "pedestrian", "steps"}:
        modes = {"pedestrian"}
        if way.tags.get("bicycle") in {"yes", "designated", "permissive"}:
            modes.add("bicycle")
        return filtered_osm_modes(way.tags, modes)
    if highway == "path":
        modes = set()
        if way.tags.get("foot") != "no":
            modes.add("pedestrian")
        if way.tags.get("bicycle") != "no":
            modes.add("bicycle")
        return filtered_osm_modes(way.tags, modes)
    if highway == "bridleway":
        modes = set()
        if way.tags.get("foot") in {"yes", "designated", "permissive"}:
            modes.add("pedestrian")
        if way.tags.get("bicycle") in {"yes", "designated", "permissive"}:
            modes.add("bicycle")
        return filtered_osm_modes(way.tags, modes)
    return set()


def _opposition_deg(arms: list[dict[str, Any]]) -> float | None:
    if len(arms) != 2:
        return None
    first = arms[0]["bearing_from_crossing_deg"]
    second = arms[1]["bearing_from_crossing_deg"]
    if first is None or second is None:
        return None
    return round(abs((float(first) - float(second) + 180.0) % 360.0 - 180.0), 6)


def _axis_crossing_angle_deg(
    support_arms: list[dict[str, Any]],
    vehicle_arms: list[dict[str, Any]],
) -> float | None:
    if len(support_arms) != 2 or len(vehicle_arms) != 2:
        return None
    support = support_arms[0]["bearing_from_crossing_deg"]
    vehicle = vehicle_arms[0]["bearing_from_crossing_deg"]
    if support is None or vehicle is None:
        return None
    delta = abs((float(support) - float(vehicle) + 180.0) % 360.0 - 180.0)
    return round(min(delta, 180.0 - delta), 6)


def _grade_separation_evidence(
    patch: OSMPatch,
    *,
    way_ids: Iterable[str],
) -> list[dict[str, str]]:
    evidence = []
    for way_id in sorted(set(way_ids)):
        way = patch.ways[way_id]
        for key in _GRADE_SEPARATION_KEYS:
            value = way.tags.get(key)
            if value is not None and value not in {"", "0", "no"}:
                evidence.append({"way_id": way_id, "key": key, "value": value})
    return evidence


def _bearing_deg(first: Any, second: Any) -> float:
    return (
        math.degrees(
            math.atan2(
                float(second.x or 0.0) - float(first.x or 0.0),
                float(second.y or 0.0) - float(first.y or 0.0),
            )
        )
        + 360.0
    ) % 360.0


def _is_crossing_node(tags: dict[str, str]) -> bool:
    return bool(tags.get("highway") == "crossing" or tags.get("crossing"))


def _patch_semantic_sha256(patch: OSMPatch) -> str:
    payload = patch.model_dump(mode="json", exclude_none=False)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
