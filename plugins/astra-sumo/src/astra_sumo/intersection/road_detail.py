"""Lower-level, evidence-bound road-arm and connector classification.

The intersection archetype classifier describes a bounded physical cell.  This
module describes the *ports around that cell* and the short pieces of topology
between its candidate conflict centres.  It is intentionally additive: an arm
is not reclassified as a new intersection type, and an OSM marking is never
promoted to a legal lane movement or a reconstruction instruction.

The output uses a small finite vocabulary so the same representation can be
used for a simple T, a four-way junction, and a compound case such as Hamburg
2394.  Every inferred value carries the source way/port/connector evidence and
remains review-only.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .schema import OSMPatch


SCHEMA_ID = "torii.intersection-road-detail/v1"
CLASSIFIER_VERSION = "road-detail-v1"

ROAD_ARM_FORMS = (
    "bidirectional",
    "one_way",
    "directional_pair",
    "link",
    "unknown",
)
ROAD_ARM_ROLES = (
    "continuous_axis_candidate",
    "branch_candidate",
    "connector_candidate",
    "access_candidate",
    "unknown",
)
NETWORK_ROLES = (
    "arterial",
    "collector",
    "local",
    "access",
    "link",
    "unknown",
)
AUTHORITY_CATEGORIES = (
    "hvs",
    "bezirksstrasse",
    "bundesfernstrasse",
    "state_road",
    "county_road",
    "municipal_road",
    "unknown",
)
ROAD_CLASSES = (
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "local",
    "service",
    "link",
    "other",
    "unknown",
)
LANE_ORGANIZATION = (
    "undivided",
    "directional_pair",
    "turn_lane_marked",
    "lane_count_transition",
    "mixed",
    "unknown",
)
CONNECTOR_CLASSES = (
    "storage_connector",
    "short_internal_connector",
    "movement_connector",
    "unknown",
)
CHANNELIZATION_TYPES = (
    "turn_bay_candidate",
    "slip_bypass_candidate",
    "splitter_island_candidate",
    "median_refuge",
    "flare_fanout_candidate",
    "storage_connector",
    "merge_diverge_candidate",
    "protected_corner_candidate",
)

_ROAD_CLASS_BY_HIGHWAY = {
    "motorway": "motorway",
    "motorway_link": "link",
    "trunk": "trunk",
    "trunk_link": "link",
    "primary": "primary",
    "primary_link": "link",
    "secondary": "secondary",
    "secondary_link": "link",
    "tertiary": "tertiary",
    "tertiary_link": "link",
    "unclassified": "local",
    "residential": "local",
    "living_street": "local",
    "service": "service",
    "road": "other",
}
_ROAD_CLASS_RANK = {
    "service": 0,
    "local": 1,
    "other": 2,
    "tertiary": 3,
    "secondary": 4,
    "primary": 5,
    "trunk": 6,
    "motorway": 7,
    "link": 8,
}


def registered_road_detail_vocabulary() -> dict[str, Any]:
    """Return the finite vocabulary used below the physical-cell layer."""

    payload = {
        "schema": "torii.intersection-road-detail-vocabulary/v1",
        "road_arm_forms": list(ROAD_ARM_FORMS),
        "road_arm_roles": list(ROAD_ARM_ROLES),
        "network_roles": list(NETWORK_ROLES),
        "authority_categories": list(AUTHORITY_CATEGORIES),
        "road_classes": list(ROAD_CLASSES),
        "lane_organization": list(LANE_ORGANIZATION),
        "connector_classes": list(CONNECTOR_CLASSES),
        "channelization_types": list(CHANNELIZATION_TYPES),
        "composition_model": {
            "arm": "a semantic boundary port group, not an OSM way count",
            "channelization": "evidence attached to an arm, way, node, or connector",
            "connector": "a bounded internal graph segment or reviewed lane movement",
            "authorization": "classification_only; legal movement and SUMO mutation remain blocked",
        },
    }
    return {**payload, "vocabulary_id": f"road-detail-vocabulary-{_stable_digest(payload)[:20]}"}


def classify_intersection_road_detail(
    patch: OSMPatch,
    physical_cell: Mapping[str, Any],
    *,
    arm_model: Mapping[str, Any],
    topology_evidence: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any] | None = None,
    road_network_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify semantic road arms, channelization, and internal connectors.

    The function consumes the already-derived physical-cell and topology
    evidence.  It never expands the cell, joins nodes, or chooses a SUMO
    connection.  Missing or conflicting evidence is represented explicitly as
    ``unknown``/``review_required`` rather than guessed.
    """

    parent_id = str(physical_cell.get("hypothesis_id", ""))
    topology_parent = str(topology_evidence.get("physical_cell_hypothesis_id", ""))
    if topology_parent != parent_id:
        raise ValueError(
            "topology_evidence physical_cell_hypothesis_id does not match the supplied physical cell"
        )
    if movement_hypotheses is not None and str(
        movement_hypotheses.get("parent_physical_cell_hypothesis_id", "")
    ) != parent_id:
        raise ValueError(
            "movement_hypotheses parent_physical_cell_hypothesis_id does not match the supplied physical cell"
        )

    approaches = sorted(
        (dict(item) for item in physical_cell.get("physical_approaches", ())),
        key=lambda item: str(item.get("physical_approach_id", "")),
    )
    through_axis_ids = {
        str(arm_id)
        for pair in arm_model.get("through_pairs", ())
        for arm_id in pair.get("arm_ids", ())
    }
    arms = [
        _classify_arm(
            patch,
            approach,
            role=("continuous_axis_candidate" if str(approach.get("physical_approach_id")) in through_axis_ids else "branch_candidate"),
            road_network_evidence=road_network_evidence,
        )
        for approach in approaches
    ]
    channelization = _classify_channelization(
        patch,
        approaches=approaches,
        topology_evidence=topology_evidence,
    )
    topology_connectors = _classify_topology_connectors(
        topology_evidence,
        parent_id=parent_id,
    )
    movement_connectors = _classify_movement_connectors(
        movement_hypotheses,
        parent_id=parent_id,
    )
    connectors = sorted(
        topology_connectors + movement_connectors,
        key=lambda item: str(item.get("connector_id", "")),
    )
    connection_relations = _connection_relations(arms, connectors)
    if movement_connectors:
        channelization.append(
            _feature(
                "protected_corner_candidate",
                evidence_ids=[str(item["connector_id"]) for item in movement_connectors],
                attachments={"connector_ids": [str(item["connector_id"]) for item in movement_connectors]},
                rationale=(
                    "A reviewed movement connector is retained as a protected internal "
                    "connection candidate; its legal lane geometry is not inferred here."
                ),
            )
        )
    channelization = _dedupe_features(channelization)
    unknown_arm_ids = [
        str(item["road_arm_id"])
        for item in arms
        if item["road_arm_form"]["value"] == "unknown"
        or item["road_class"]["value"] == "unknown"
    ]
    status = "review_required" if unknown_arm_ids or not arms else "classified"
    payload: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "classifier_version": CLASSIFIER_VERSION,
        "vocabulary_id": registered_road_detail_vocabulary()["vocabulary_id"],
        "generation_status": "pass",
        "status": status,
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "parent_physical_cell_hypothesis_id": parent_id,
        "parent_topology_evidence_id": topology_evidence.get("topology_evidence_id"),
        "parent_movement_hypothesis_set_id": (
            movement_hypotheses.get("hypothesis_set_id") if movement_hypotheses else None
        ),
        "road_arms": arms,
        "channelization": channelization,
        "connectors": connectors,
        "connection_relations": connection_relations,
        "counts": {
            "road_arm_count": len(arms),
            "channelization_feature_count": len(channelization),
            "topology_connector_count": len(topology_connectors),
            "movement_connector_count": len(movement_connectors),
            "connection_relation_count": len(connection_relations),
        },
        "unknown_road_arm_ids": sorted(unknown_arm_ids),
        "review_reasons": _review_reasons(arms, topology_evidence, movement_hypotheses),
        "claim_boundary": (
            "Road-arm, channelization, and connector records are finite OSM/topology "
            "evidence. They do not prove signal ownership, legal lane movements, "
            "physical-core identity, or authorize SUMO mutation."
        ),
    }
    payload["road_detail_id"] = f"road-detail-{_stable_digest(payload)[:20]}"
    return payload


def _classify_arm(
    patch: OSMPatch,
    approach: Mapping[str, Any],
    *,
    role: str,
    road_network_evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    approach_id = str(approach.get("physical_approach_id", ""))
    way_ids = sorted(map(str, approach.get("source_way_ids", ())))
    ways = [patch.ways[way_id] for way_id in way_ids if way_id in patch.ways]
    highways = sorted({str(way.tags.get("highway", "")).strip().lower() for way in ways if way.tags.get("highway")})
    road_classes = sorted(
        {_ROAD_CLASS_BY_HIGHWAY.get(highway, "unknown") for highway in highways},
        key=lambda item: (_ROAD_CLASS_RANK.get(item, -1), item),
    )
    road_class = road_classes[-1] if road_classes else "unknown"
    identity = _road_network_identity(
        ways,
        way_ids=way_ids,
        road_network_evidence=road_network_evidence,
        fallback_osm_class=road_class,
    )
    link_way_ids = sorted(
        way.id for way in ways if str(way.tags.get("highway", "")).lower().endswith("_link")
    )
    flow_roles = sorted(map(str, approach.get("flow_roles", ())))
    member_count = int(approach.get("member_count", 0))
    if link_way_ids:
        form = "link"
        form_reason = "source way is explicitly tagged as a highway link"
    elif member_count == 1 and flow_roles == ["bidirectional"]:
        form = "bidirectional"
        form_reason = "one bidirectional boundary port"
    elif member_count == 1 and len(flow_roles) == 1 and flow_roles[0] in {"incoming", "outgoing"}:
        form = "one_way"
        form_reason = "one directional boundary port"
    elif member_count == 2 and set(flow_roles) == {"incoming", "outgoing"}:
        form = "directional_pair"
        form_reason = "complementary incoming/outgoing ports; divided-vs-one-way remains unresolved"
    else:
        form = "unknown"
        form_reason = "boundary-port flow or grouping evidence is incomplete"

    turn_marked = bool(str(approach.get("incoming_turn_lanes_raw") or "").strip()) or any(
        str(way.tags.get(key, "")).strip()
        for way in ways
        for key in ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward")
    )
    lane_transition = int(approach.get("incoming_lane_count", 0)) != int(approach.get("outgoing_lane_count", 0))
    organization_values = []
    if form == "bidirectional":
        organization_values.append("undivided")
    if form == "directional_pair":
        organization_values.append("directional_pair")
    if turn_marked:
        organization_values.append("turn_lane_marked")
    if lane_transition:
        organization_values.append("lane_count_transition")
    organization_values = sorted(set(organization_values))
    if len(organization_values) == 1:
        organization = organization_values[0]
    elif organization_values:
        organization = "mixed"
    else:
        organization = "unknown"
    role_value = role if role in ROAD_ARM_ROLES else "unknown"
    if link_way_ids:
        role_value = "connector_candidate"
    elif road_class == "service" and role_value == "branch_candidate":
        role_value = "access_candidate"
    arm_payload = {
        "physical_approach_id": approach_id,
        "source_way_ids": way_ids,
        "member_boundary_port_ids": sorted(map(str, approach.get("member_boundary_port_ids", ()))),
        "road_names": sorted({str(way.tags.get("name")) for way in ways if way.tags.get("name")}),
        "highway_tags": highways,
        "flow_roles": flow_roles,
        "bearing_from_seed_deg": _finite_float(approach.get("bearing_from_seed_deg")),
    }
    road_arm_id = f"road-arm-{_stable_digest(arm_payload)[:16]}"
    return {
        "road_arm_id": road_arm_id,
        "physical_approach_id": approach_id,
        "road_arm_form": _dimension(
            "road_arm_form",
            form,
            grade="observed" if form in {"link", "bidirectional", "one_way"} else "rule_derived" if form == "directional_pair" else "unknown",
            evidence_ids=way_ids + sorted(map(str, approach.get("member_boundary_port_ids", ()))),
            rationale=form_reason,
            alternatives=[],
        ),
        "road_arm_role": _dimension(
            "road_arm_role",
            role_value,
            grade="rule_derived" if role_value != "unknown" else "unknown",
            evidence_ids=[approach_id],
            rationale=(
                "Role is a derived axis/branch/link/access candidate. It does not "
                "establish a named-road hierarchy or control assignment."
            ),
        ),
        "road_class": _dimension(
            "road_class",
            road_class,
            grade="observed" if road_class != "unknown" else "unknown",
            evidence_ids=way_ids,
            rationale="Highest observed OSM highway class among the member boundary ways.",
        ),
        "network_role": identity["network_role"],
        "authority_category": identity["authority_category"],
        "lane_organization": _dimension(
            "lane_organization",
            organization,
            grade="observed" if turn_marked else "rule_derived" if organization != "unknown" else "unknown",
            evidence_ids=way_ids,
            rationale=(
                "Lane organization combines directional ports, turn:lanes marking, "
                "and ingress/egress lane-count differences; it is not a lane-geometry reconstruction."
            ),
        ),
        "incoming_lane_count": int(approach.get("incoming_lane_count", 0)),
        "outgoing_lane_count": int(approach.get("outgoing_lane_count", 0)),
        "incoming_turn_lanes_raw": approach.get("incoming_turn_lanes_raw"),
        "bearing_from_seed_deg": _finite_float(approach.get("bearing_from_seed_deg")),
        "source_way_ids": way_ids,
        "member_boundary_port_ids": sorted(map(str, approach.get("member_boundary_port_ids", ()))),
        "source_way_identity_evidence": _source_way_identity_evidence(ways),
        "status": "review_required",
        "road_identity": identity,
    }


def _classify_channelization(
    patch: OSMPatch,
    *,
    approaches: Sequence[Mapping[str, Any]],
    topology_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    path_node_ids = {str(value) for value in topology_evidence.get("path_closure_node_ids", ())}
    seen_way_ids: set[str] = set()
    approach_by_way: dict[str, list[str]] = {}
    for approach in approaches:
        arm_id = str(approach.get("physical_approach_id", ""))
        for way_id in map(str, approach.get("source_way_ids", ())):
            approach_by_way.setdefault(way_id, []).append(arm_id)
    for way in patch.ways.values():
        if way.id in seen_way_ids or not set(map(str, way.node_refs)) & path_node_ids:
            continue
        seen_way_ids.add(way.id)
        tags = way.tags
        highway = str(tags.get("highway", "")).strip().lower()
        arm_ids = sorted(set(approach_by_way.get(way.id, ())))
        turn_raw = next(
            (str(tags.get(key, "")).strip() for key in ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward") if str(tags.get(key, "")).strip()),
            "",
        )
        if turn_raw:
            features.append(
                _feature(
                    "turn_bay_candidate",
                    evidence_ids=[way.id],
                    attachments={"source_way_ids": [way.id], "arm_ids": arm_ids, "turn_lanes_raw": turn_raw},
                    rationale="OSM turn:lanes marking is evidence of lane specialization, not proof of a physical bay.",
                )
            )
        if highway.endswith("_link"):
            features.append(
                _feature(
                    "slip_bypass_candidate",
                    evidence_ids=[way.id],
                    attachments={"source_way_ids": [way.id], "arm_ids": arm_ids, "highway": highway},
                    rationale="An OSM *_link way is an auxiliary-road candidate; its merge/diverge geometry needs review.",
                )
            )
        if any(str(tags.get(key, "")).strip() for key in ("merge:lanes", "change:lanes", "destination:lanes")):
            features.append(
                _feature(
                    "merge_diverge_candidate",
                    evidence_ids=[way.id],
                    attachments={"source_way_ids": [way.id], "arm_ids": arm_ids},
                    rationale="OSM lane-change/destination marking indicates a merge/diverge candidate without proving its conflict core.",
                )
            )
        if tags.get("crossing:island") == "yes":
            features.append(
                _feature(
                    "median_refuge",
                    evidence_ids=[way.id],
                    attachments={"source_way_ids": [way.id], "arm_ids": arm_ids},
                    rationale="Explicit crossing:island=yes is retained as a pedestrian/median refuge feature.",
                )
            )
    for node_id in sorted(path_node_ids):
        node = patch.nodes.get(node_id)
        if node is None:
            continue
        if node.tags.get("crossing:island") == "yes":
            features.append(
                _feature(
                    "splitter_island_candidate",
                    evidence_ids=[node_id],
                    attachments={"source_node_ids": [node_id]},
                    rationale="An OSM island node is a splitter/refuge candidate; geometry and mode are not inferred from the tag alone.",
                )
            )
    for approach in approaches:
        if int(approach.get("incoming_lane_count", 0)) != int(approach.get("outgoing_lane_count", 0)):
            features.append(
                _feature(
                    "flare_fanout_candidate",
                    evidence_ids=[str(approach.get("physical_approach_id", ""))],
                    attachments={
                        "physical_approach_id": str(approach.get("physical_approach_id", "")),
                        "incoming_lane_count": int(approach.get("incoming_lane_count", 0)),
                        "outgoing_lane_count": int(approach.get("outgoing_lane_count", 0)),
                    },
                    rationale="A directional lane-count change is a flare/fanout candidate, not a confirmed channelization shape.",
                )
            )
    for connector in topology_evidence.get("storage_capable_connectors", ()):
        connector_id = _topology_connector_id(connector, str(topology_evidence.get("physical_cell_hypothesis_id", "")))
        features.append(
            _feature(
                "storage_connector",
                evidence_ids=[connector_id],
                attachments={"connector_id": connector_id, **dict(connector)},
                rationale="A graph connector above the storage threshold can explain a compound cell; it does not prove a separate physical junction.",
            )
        )
    return features


def _classify_topology_connectors(
    topology_evidence: Mapping[str, Any],
    *,
    parent_id: str,
) -> list[dict[str, Any]]:
    result = []
    storage_ids = {
        _topology_connector_id(item, parent_id)
        for item in topology_evidence.get("storage_capable_connectors", ())
    }
    for item in topology_evidence.get("branch_connectors", ()):
        connector_id = _topology_connector_id(item, parent_id)
        distance = _finite_float(item.get("graph_distance_m"))
        connector_class = "storage_connector" if connector_id in storage_ids else "short_internal_connector"
        result.append(
            {
                "connector_id": connector_id,
                "connector_class": connector_class,
                "from_branch_node_id": str(item.get("from_branch_node_id", "")),
                "to_branch_node_id": str(item.get("to_branch_node_id", "")),
                "graph_distance_m": distance,
                "storage_capable": bool(item.get("storage_capable")),
                "status": "rule_derived",
                "decision": "review_required",
                "evidence_ids": [str(topology_evidence.get("topology_evidence_id", ""))],
                "claim_boundary": "A graph connector is topology evidence, not a legal SUMO connection.",
            }
        )
    return result


def _classify_movement_connectors(
    movement_hypotheses: Mapping[str, Any] | None,
    *,
    parent_id: str,
) -> list[dict[str, Any]]:
    if not movement_hypotheses:
        return []
    variants = list(movement_hypotheses.get("variants", ()))
    if not variants:
        return []
    consensus_id = movement_hypotheses.get("consensus_variant_id")
    variant = next((item for item in variants if item.get("variant_id") == consensus_id), variants[0])
    result = []
    for movement in variant.get("atomic_movements", ()):
        turn = str(movement.get("turn", "unknown"))
        connector_class = {
            "straight": "movement_connector",
            "left": "movement_connector",
            "right": "movement_connector",
            "uturn": "unknown",
        }.get(turn, "unknown")
        movement_id = str(movement.get("stable_movement_id", ""))
        if not movement_id:
            movement_id = f"movement-connector-{_stable_digest(movement)[:16]}"
        result.append(
            {
                "connector_id": f"{movement_id}-connector",
                "connector_class": connector_class,
                "from_arm_id": str(movement.get("from_physical_approach_id", "")),
                "to_arm_id": str(movement.get("to_physical_approach_id", "")),
                "turn": turn,
                "from_lane_index": movement.get("from_lane_index"),
                "to_lane_index": movement.get("to_lane_index"),
                "status": "rule_derived",
                "decision": "review_required",
                "evidence_ids": [movement_id, str(movement_hypotheses.get("hypothesis_set_id", ""))],
                "parent_physical_cell_hypothesis_id": parent_id,
                "claim_boundary": "An OSM movement hypothesis is not a verified lane connection or signal link.",
            }
        )
    return result


def _road_network_identity(
    ways: Sequence[Any],
    *,
    way_ids: Sequence[str],
    road_network_evidence: Mapping[str, Any] | None,
    fallback_osm_class: str,
) -> dict[str, Any]:
    """Resolve road function with an explicit source-precedence chain.

    ``road_network_evidence`` is deliberately caller-supplied.  A Hamburg HVS
    feature service, a German RIN/RASt inventory, or another jurisdictional
    source can bind a way without changing this generic classifier.  When it is
    absent, the result is an OSM fallback and is never marked authoritative.
    """

    evidence = dict(road_network_evidence or {})
    by_way = evidence.get("by_way_id", {})
    if not isinstance(by_way, Mapping):
        by_way = {}
    records = [dict(by_way.get(str(way_id), {})) for way_id in way_ids if isinstance(by_way.get(str(way_id), {}), Mapping)]
    authority_values = sorted({str(item.get("authority_category", "unknown")) for item in records if item.get("authority_category")})
    network_values = sorted({str(item.get("network_role", "unknown")) for item in records if item.get("network_role")})
    rin_values = sorted({str(item.get("functional_category", "")) for item in records if item.get("functional_category")})
    source_ids = sorted({str(item.get("source_evidence_id", "")) for item in records if item.get("source_evidence_id")})
    authority_conflict = len(set(authority_values) - {"unknown"}) > 1
    network_conflict = len(set(network_values) - {"unknown"}) > 1
    authoritative_network_value = ""
    if authority_conflict:
        authority_value = "unknown"
        authority_grade = "contradicted"
        authority_rationale = "Authoritative road records disagree across member ways."
    else:
        authority_value = next((value for value in authority_values if value in AUTHORITY_CATEGORIES and value != "unknown"), "unknown")
        authority_grade = "observed" if authority_value != "unknown" else "unknown"
        authority_rationale = "Authority category comes from caller-supplied road-network evidence." if authority_value != "unknown" else "No authoritative jurisdiction category was supplied."
    if network_conflict:
        network_value = "unknown"
        network_grade = "contradicted"
        network_rationale = "Road-network evidence assigns conflicting functional roles to member ways."
    else:
        authoritative_network_value = next(
            (value for value in network_values if value in NETWORK_ROLES and value != "unknown"),
            "",
        )
        network_value = authoritative_network_value
        if not authoritative_network_value:
            network_value = _fallback_network_role(fallback_osm_class)
            network_grade = "rule_derived" if network_value != "unknown" else "unknown"
            network_rationale = (
                "Functional role is an OSM fallback only; it must not override an official "
                "road-network category."
            )
        else:
            network_grade = "observed"
            network_rationale = "Functional role comes from caller-supplied road-network evidence."
    rin_value = rin_values[0] if len(rin_values) == 1 else "unknown"
    return {
        "osm_label_evidence": _osm_label_evidence(ways, way_ids=way_ids),
        "network_role": _dimension(
            "network_role",
            network_value,
            grade=network_grade,
            evidence_ids=list(way_ids) + source_ids,
            rationale=network_rationale,
            alternatives=(
                ["arterial", "collector", "local", "access"]
                if network_value == "unknown"
                else []
            ),
        ),
        "authority_category": _dimension(
            "authority_category",
            authority_value,
            grade=authority_grade,
            evidence_ids=list(way_ids) + source_ids,
            rationale=authority_rationale,
        ),
        "functional_category": rin_value,
        "functional_category_source_ids": source_ids,
        "resolution": (
            "contradicted"
            if authority_conflict or network_conflict
            else "authoritative"
            if authority_value != "unknown" or authoritative_network_value
            else "osm_fallback"
        ),
        "osm_highway_class": fallback_osm_class,
        "source_precedence": [
            "jurisdictional_road_network",
            "RIN_or_RASt_functional_category",
            "OSM_highway_fallback",
        ],
    }


def _osm_label_evidence(ways: Sequence[Any], *, way_ids: Sequence[str]) -> dict[str, Any]:
    """Expose observed OSM road labels without turning them into identity truth.

    The road-arm classifier previously used ``name`` internally to derive a
    stable arm ID, but did not return it.  A named corridor such as Am
    Sandtorkai must remain inspectable before a reviewer can decide whether
    two arms are continuous or whether an official road link covers only a
    bounded part of the extract.  OSM labels are evidence only: a shared name
    does not prove legal connectivity, physical continuity, or authority.
    """

    names = sorted(
        {
            str(way.tags.get("name", "")).strip()
            for way in ways
            if str(way.tags.get("name", "")).strip()
        }
    )
    refs = sorted(
        {
            str(way.tags.get("ref", "")).strip()
            for way in ways
            if str(way.tags.get("ref", "")).strip()
        }
    )
    if len(names) == 1:
        coherence = "single_named"
    elif len(names) > 1:
        coherence = "multiple_named"
    elif refs:
        coherence = "ref_only"
    else:
        coherence = "unnamed"
    return {
        "names": names,
        "refs": refs,
        "coherence": coherence,
        "status": "observed" if names or refs else "unknown",
        "decision": "review_required",
        "evidence_ids": sorted({str(value) for value in way_ids if str(value)}),
        "rationale": (
            "OSM name/ref labels identify a candidate road corridor for review; they do not prove "
            "official-link equivalence, physical continuity, legal movements, or signal ownership."
        ),
    }


def _source_way_identity_evidence(ways: Sequence[Any]) -> list[dict[str, Any]]:
    """Return raw per-way labels and lane tags backing an arm classification."""

    return [
        {
            "way_id": str(way.id),
            "name": str(way.tags.get("name", "")).strip() or None,
            "ref": str(way.tags.get("ref", "")).strip() or None,
            "highway": str(way.tags.get("highway", "")).strip() or None,
            "oneway": str(way.tags.get("oneway", "")).strip() or None,
            "lanes": str(way.tags.get("lanes", "")).strip() or None,
            "turn_lanes": next(
                (
                    str(way.tags.get(key, "")).strip()
                    for key in ("turn:lanes", "turn:lanes:forward", "turn:lanes:backward")
                    if str(way.tags.get(key, "")).strip()
                ),
                None,
            ),
        }
        for way in sorted(ways, key=lambda item: str(item.id))
    ]


def _fallback_network_role(osm_class: str) -> str:
    return {
        "motorway": "arterial",
        "trunk": "arterial",
        "primary": "arterial",
        "secondary": "collector",
        "tertiary": "collector",
        "local": "local",
        "service": "access",
        "link": "link",
        "other": "unknown",
    }.get(osm_class, "unknown")


def _connection_relations(
    arms: Sequence[Mapping[str, Any]],
    connectors: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(item.get("physical_approach_id")): item for item in arms}
    relations: list[dict[str, Any]] = []
    for connector in connectors:
        source_id = str(connector.get("from_arm_id", ""))
        target_id = str(connector.get("to_arm_id", ""))
        if not source_id or not target_id or source_id not in by_id or target_id not in by_id:
            continue
        source_role = str(by_id[source_id].get("road_arm_role", {}).get("value", "unknown"))
        target_role = str(by_id[target_id].get("road_arm_role", {}).get("value", "unknown"))
        source_network = str(by_id[source_id].get("network_role", {}).get("value", "unknown"))
        target_network = str(by_id[target_id].get("network_role", {}).get("value", "unknown"))
        if "connector_candidate" in {source_role, target_role} or "link" in {source_network, target_network}:
            relation = "link_to_network"
        elif "access_candidate" in {source_role, target_role} or "access" in {source_network, target_network}:
            relation = "access_to_network"
        elif source_role == target_role == "continuous_axis_candidate":
            relation = "through_axis"
        elif "continuous_axis_candidate" in {source_role, target_role}:
            relation = "branch_to_axis"
        else:
            relation = "arm_to_arm"
        relations.append(
            {
                "connector_id": str(connector.get("connector_id", "")),
                "from_physical_approach_id": source_id,
                "to_physical_approach_id": target_id,
                "from_network_role": source_network,
                "to_network_role": target_network,
                "relation": relation,
                "status": "rule_derived" if relation != "arm_to_arm" else "unknown",
                "decision": "review_required",
                "evidence_ids": list(map(str, connector.get("evidence_ids", ()))) + [str(connector.get("connector_id", ""))],
                "claim_boundary": "Connection relation is a routing candidate; it does not authorize a SUMO connection.",
            }
        )
    return sorted(relations, key=lambda item: str(item.get("connector_id", "")))


def _topology_connector_id(item: Mapping[str, Any], parent_id: str) -> str:
    return f"topology-connector-{_stable_digest({'parent': parent_id, 'from': item.get('from_branch_node_id'), 'to': item.get('to_branch_node_id')})[:16]}"


def _feature(
    feature_type: str,
    *,
    evidence_ids: Sequence[str],
    attachments: Mapping[str, Any],
    rationale: str,
) -> dict[str, Any]:
    if feature_type not in CHANNELIZATION_TYPES:
        raise ValueError(f"unknown channelization type: {feature_type}")
    return {
        "type": feature_type,
        "status": "observed" if feature_type == "median_refuge" else "rule_derived",
        "decision": "review_required",
        "evidence_ids": sorted({str(value) for value in evidence_ids if str(value)}),
        "attachments": dict(attachments),
        "rationale": rationale,
    }


def _dimension(
    dimension: str,
    value: str,
    *,
    grade: str,
    evidence_ids: Sequence[str],
    rationale: str,
    alternatives: Sequence[str] = (),
) -> dict[str, Any]:
    allowed = {
        "road_arm_form": ROAD_ARM_FORMS,
        "road_arm_role": ROAD_ARM_ROLES,
        "network_role": NETWORK_ROLES,
        "authority_category": AUTHORITY_CATEGORIES,
        "road_class": ROAD_CLASSES,
        "lane_organization": LANE_ORGANIZATION,
    }[dimension]
    if value not in allowed:
        raise ValueError(f"invalid {dimension} value: {value}")
    invalid = sorted(set(alternatives) - set(allowed))
    if invalid:
        raise ValueError(f"invalid {dimension} alternatives: {invalid}")
    return {
        "value": value,
        "status": grade,
        "decision": "review_required",
        "evidence_ids": sorted({str(item) for item in evidence_ids if str(item)}),
        "rationale": rationale,
        "alternatives": sorted(set(map(str, alternatives))),
    }


def _review_reasons(
    arms: Sequence[Mapping[str, Any]],
    topology_evidence: Mapping[str, Any],
    movement_hypotheses: Mapping[str, Any] | None,
) -> list[str]:
    reasons: set[str] = set()
    if not arms:
        reasons.add("no_semantic_road_arms")
    if any(item.get("road_arm_form", {}).get("value") == "unknown" for item in arms):
        reasons.add("road_arm_form_unresolved")
    if any(item.get("road_class", {}).get("value") == "unknown" for item in arms):
        reasons.add("road_class_unresolved")
    if any(item.get("road_arm_form", {}).get("value") == "directional_pair" for item in arms):
        reasons.add("divided_vs_one_way_pair_unresolved")
    if topology_evidence.get("morphology") == "paired_or_offset_conflict_centers":
        reasons.add("connector_may_separate_conflict_centers")
    if movement_hypotheses and movement_hypotheses.get("variant_comparison", {}).get("status") != "exact":
        reasons.add("movement_connector_hypotheses_disagree")
    return sorted(reasons)


def _dedupe_features(features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique = {(_stable_digest(dict(item))): dict(item) for item in features}
    return [unique[key] for key in sorted(unique)]


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stable_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
