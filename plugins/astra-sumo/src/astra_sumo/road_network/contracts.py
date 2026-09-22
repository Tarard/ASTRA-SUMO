"""Evidence-bound contracts for official, OSM, SUMO, and canonical roads.

The contracts model source assertions and their relationships.  They do not
authorize node joins, lane/channelization edits, legal movements, traffic-light
bindings, or SUMO network materialization.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


ROAD_NETWORK_SCHEMA = "torii.road-network-semantics/v1"
CONFLATION_RELATION_KINDS = frozenset(
    {
        "equivalent",
        "covers",
        "covered_by",
        "contains_bounded_segment",
        "overlaps",
        "directional_component_of",
        "parallel_carriageway_of",
        "connector_of",
        "unmatched",
    }
)
ROAD_DIRECTIONS = frozenset({"with", "against", "both", "not_applicable", "unknown"})
EVIDENCE_STATUSES = frozenset({"pass", "review_required", "blocked", "not_applicable"})

RoadDirection = Literal["with", "against", "both", "not_applicable", "unknown"]
EvidenceStatus = Literal["pass", "review_required", "blocked", "not_applicable"]
JsonScalar = str | int | float | bool | None


@dataclass(frozen=True)
class RoadCorridor:
    """Source-neutral route/name continuity identity above individual links."""

    corridor_id: str
    names: tuple[str, ...] = ()
    route_refs: tuple[str, ...] = ()
    jurisdiction: str = ""

    def __post_init__(self) -> None:
        if not self.corridor_id.strip():
            raise ValueError("corridor_id must not be empty")
        object.__setattr__(self, "names", tuple(sorted({item.strip() for item in self.names if item.strip()})))
        object.__setattr__(
            self,
            "route_refs",
            tuple(sorted({item.strip() for item in self.route_refs if item.strip()})),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": ROAD_NETWORK_SCHEMA,
            "source_system": "canonical",
            "corridor_id": self.corridor_id,
            "names": list(self.names),
            "route_refs": list(self.route_refs),
            "jurisdiction": self.jurisdiction,
            "classification_only": True,
            "automatic_promotion_gate": "blocked",
        }


@dataclass(frozen=True, order=True)
class RoadObjectRef:
    """Stable reference to one source or canonical road-network object."""

    namespace: str
    object_type: str
    object_id: str
    source_sha256: str = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    provider: str = ""
    dataset: str = ""
    edition: str = ""
    jurisdiction: str = ""

    def __post_init__(self) -> None:
        for field_name in ("namespace", "object_type", "object_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if self.namespace != "canonical":
            _require_sha256(self.source_sha256, "source_sha256")
        elif self.source_sha256:
            _require_sha256(self.source_sha256, "source_sha256")
        object.__setattr__(self, "source_sha256", self.source_sha256.lower())
        _validate_validity(self.valid_from, self.valid_to)

    @property
    def identity_key(self) -> tuple[str, str, str, str]:
        return (self.namespace, self.object_type, self.object_id, self.source_sha256)

    def as_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "source_sha256": self.source_sha256,
            "valid_from": _iso(self.valid_from),
            "valid_to": _iso(self.valid_to),
            "provider": self.provider,
            "dataset": self.dataset,
            "edition": self.edition,
            "jurisdiction": self.jurisdiction,
        }


@dataclass(frozen=True)
class CanonicalRoadLink:
    """A source-neutral topological link; creation is not source promotion."""

    link_id: str
    corridor_id: str
    from_node_id: str
    to_node_id: str
    length_m: float
    directionality: Literal["bidirectional", "one_way", "directional_pair", "unknown"]

    def __post_init__(self) -> None:
        for field_name in ("link_id", "corridor_id", "from_node_id", "to_node_id"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if not math.isfinite(self.length_m) or self.length_m <= 0:
            raise ValueError("length_m must be finite and positive")
        if self.directionality not in {"bidirectional", "one_way", "directional_pair", "unknown"}:
            raise ValueError("unsupported directionality")

    @property
    def ref(self) -> RoadObjectRef:
        return RoadObjectRef(
            namespace="canonical",
            object_type="road_link",
            object_id=self.link_id,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": ROAD_NETWORK_SCHEMA,
            "source_system": "canonical",
            "link_id": self.link_id,
            "corridor_id": self.corridor_id,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
            "length_m": float(self.length_m),
            "directionality": self.directionality,
            "classification_only": True,
            "automatic_promotion_gate": "blocked",
        }


@dataclass(frozen=True)
class RoadPropertyAssignment:
    """One scheme-qualified road property over an optional linear interval."""

    assignment_id: str
    target_ref: RoadObjectRef
    property_name: str
    classification_scheme: str
    value: JsonScalar
    direction: RoadDirection
    evidence_refs: tuple[RoadObjectRef, ...]
    status: EvidenceStatus
    s_from_m: float | None = None
    s_to_m: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.assignment_id.strip():
            raise ValueError("assignment_id must not be empty")
        if not self.property_name.strip():
            raise ValueError("property_name must not be empty")
        if not self.classification_scheme.strip():
            raise ValueError("classification_scheme must not be empty")
        if self.direction not in ROAD_DIRECTIONS:
            raise ValueError("unsupported direction")
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError("unsupported status")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("evidence_refs must not be empty")
        if (self.s_from_m is None) != (self.s_to_m is None):
            raise ValueError("s_from_m and s_to_m must both be supplied or both omitted")
        if self.s_from_m is not None and self.s_to_m is not None:
            if not math.isfinite(self.s_from_m) or not math.isfinite(self.s_to_m):
                raise ValueError("station interval must be finite")
            if self.s_from_m < 0 or self.s_to_m <= self.s_from_m:
                raise ValueError("station interval must satisfy 0 <= s_from_m < s_to_m")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("floating property values must be finite")
        if not isinstance(self.value, (str, int, float, bool, type(None))):
            raise TypeError("property value must be a JSON scalar")

    def as_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "target_ref": self.target_ref.as_dict(),
            "property_name": self.property_name,
            "classification_scheme": self.classification_scheme,
            "value": self.value,
            "direction": self.direction,
            "s_from_m": self.s_from_m,
            "s_to_m": self.s_to_m,
            "status": self.status,
            "reason": self.reason,
            "evidence_refs": [item.as_dict() for item in self.evidence_refs],
            "source_sha256s": sorted({item.source_sha256 for item in self.evidence_refs if item.source_sha256}),
        }


@dataclass(frozen=True)
class ConflationEvidence:
    """Inspectable score vector; it deliberately has no opaque aggregate score."""

    geometry_overlap_ratio: float | None = None
    lateral_distance_m: float | None = None
    heading_delta_deg: float | None = None
    topology_agreement: float | None = None
    source_object_id_agreement: float | None = None
    name_agreement: float | None = None
    road_ref_agreement: float | None = None
    official_road_key_agreement: float | None = None
    carriageway_agreement: float | None = None
    lane_profile_agreement: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "geometry_overlap_ratio",
            "topology_agreement",
            "source_object_id_agreement",
            "name_agreement",
            "road_ref_agreement",
            "official_road_key_agreement",
            "carriageway_agreement",
            "lane_profile_agreement",
        ):
            value = getattr(self, field_name)
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError(f"{field_name} must be between zero and one")
        if self.lateral_distance_m is not None and (
            not math.isfinite(self.lateral_distance_m) or self.lateral_distance_m < 0
        ):
            raise ValueError("lateral_distance_m must be finite and non-negative")
        if self.heading_delta_deg is not None and (
            not math.isfinite(self.heading_delta_deg) or not 0 <= self.heading_delta_deg <= 180
        ):
            raise ValueError("heading_delta_deg must be between zero and 180")

    def as_dict(self) -> dict[str, float | None]:
        return {
            "geometry_overlap_ratio": self.geometry_overlap_ratio,
            "lateral_distance_m": self.lateral_distance_m,
            "heading_delta_deg": self.heading_delta_deg,
            "topology_agreement": self.topology_agreement,
            "source_object_id_agreement": self.source_object_id_agreement,
            "name_agreement": self.name_agreement,
            "road_ref_agreement": self.road_ref_agreement,
            "official_road_key_agreement": self.official_road_key_agreement,
            "carriageway_agreement": self.carriageway_agreement,
            "lane_profile_agreement": self.lane_profile_agreement,
        }


@dataclass(frozen=True)
class ConflationRelation:
    """A many-to-many relationship between source or canonical road objects."""

    relation_id: str
    left_refs: tuple[RoadObjectRef, ...]
    right_refs: tuple[RoadObjectRef, ...]
    relation_kind: str
    direction: RoadDirection
    target_time: datetime
    evidence: ConflationEvidence
    status: EvidenceStatus
    hard_gate_failures: tuple[str, ...] = ()
    alternative_relation_ids: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "left_refs", tuple(self.left_refs))
        object.__setattr__(self, "right_refs", tuple(self.right_refs))
        object.__setattr__(self, "hard_gate_failures", tuple(self.hard_gate_failures))
        object.__setattr__(self, "alternative_relation_ids", tuple(self.alternative_relation_ids))
        object.__setattr__(self, "review_reasons", tuple(self.review_reasons))
        if not self.relation_id.strip():
            raise ValueError("relation_id must not be empty")
        if not self.left_refs or not self.right_refs:
            raise ValueError("a conflation relation needs at least one ref on each side")
        if self.relation_kind not in CONFLATION_RELATION_KINDS:
            raise ValueError("unsupported relation_kind")
        if self.direction not in ROAD_DIRECTIONS:
            raise ValueError("unsupported direction")
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError("unsupported status")
        _require_aware(self.target_time, "target_time")

    def as_dict(self) -> dict[str, Any]:
        refs = (*self.left_refs, *self.right_refs)
        return {
            "schema": ROAD_NETWORK_SCHEMA,
            "relation_id": self.relation_id,
            "left_refs": [item.as_dict() for item in self.left_refs],
            "right_refs": [item.as_dict() for item in self.right_refs],
            "relation_kind": self.relation_kind,
            "direction": self.direction,
            "target_time": self.target_time.isoformat(),
            "evidence": self.evidence.as_dict(),
            "status": self.status,
            "hard_gate_failures": list(self.hard_gate_failures),
            "alternative_relation_ids": list(self.alternative_relation_ids),
            "review_reasons": list(self.review_reasons),
            "reason": self.reason,
            "source_sha256s": sorted({item.source_sha256 for item in refs if item.source_sha256}),
            "classification_only": True,
            "automatic_promotion_gate": "blocked",
        }


def build_conflation_relation(
    *,
    left_refs: tuple[RoadObjectRef, ...],
    right_refs: tuple[RoadObjectRef, ...],
    relation_kind: str,
    direction: RoadDirection,
    target_time: datetime,
    evidence: ConflationEvidence,
    hard_gate_failures: tuple[str, ...] = (),
    alternative_relation_ids: tuple[str, ...] = (),
    review_reasons: tuple[str, ...] = (),
    reason: str = "",
) -> ConflationRelation:
    """Evaluate an inspectable relation without granting mutation authority."""

    left_refs = tuple(sorted(set(left_refs), key=lambda item: item.identity_key))
    right_refs = tuple(sorted(set(right_refs), key=lambda item: item.identity_key))
    alternative_relation_ids = tuple(sorted(set(alternative_relation_ids)))
    _require_aware(target_time, "target_time")
    gates = list(dict.fromkeys(str(item) for item in hard_gate_failures if str(item)))
    relation_review_reasons = list(dict.fromkeys(str(item) for item in review_reasons if str(item)))
    for ref in (*left_refs, *right_refs):
        if ref.namespace == "canonical":
            continue
        if ref.valid_from is None or ref.valid_to is None:
            relation_review_reasons.append(f"source_validity_not_declared:{_ref_label(ref)}")
        elif not ref.valid_from <= target_time < ref.valid_to:
            gates.append(f"target_time_outside_validity:{_ref_label(ref)}")

    if gates:
        status: EvidenceStatus = "blocked"
    elif alternative_relation_ids:
        status = "review_required"
        relation_review_reasons.append("alternative_candidates_present")
    elif relation_kind == "unmatched":
        status = "review_required"
        relation_review_reasons.append("unmatched_source_object")
    elif relation_kind == "contains_bounded_segment":
        # A local OSM extraction can be geometrically contained in an official
        # road link without representing the full official link, its complete
        # station interval, or a category-transfer authority.  Keep that fact
        # observable but deliberately non-promotable until a future, explicit
        # segment-review contract supplies the missing boundary evidence.
        status = "review_required"
        relation_review_reasons.append("bounded_segment_relation_requires_explicit_review")
    elif relation_review_reasons:
        status = "review_required"
    elif _strong_conflation_evidence(evidence):
        status = "pass"
    else:
        status = "review_required"
        relation_review_reasons.append("insufficient_conflation_evidence")

    identity = {
        "left_refs": [item.as_dict() for item in left_refs],
        "right_refs": [item.as_dict() for item in right_refs],
        "relation_kind": relation_kind,
        "direction": direction,
        "target_time": target_time.isoformat(),
        "evidence": evidence.as_dict(),
        "hard_gate_failures": gates,
        "alternative_relation_ids": list(alternative_relation_ids),
        "review_reasons": relation_review_reasons,
        "reason": reason,
    }
    relation_id = f"road-relation-{_stable_digest(identity)[:20]}"
    return ConflationRelation(
        relation_id=relation_id,
        left_refs=left_refs,
        right_refs=right_refs,
        relation_kind=relation_kind,
        direction=direction,
        target_time=target_time,
        evidence=evidence,
        status=status,
        hard_gate_failures=tuple(gates),
        alternative_relation_ids=tuple(alternative_relation_ids),
        review_reasons=tuple(dict.fromkeys(relation_review_reasons)),
        reason=reason,
    )


def project_road_detail_evidence(
    relations: tuple[ConflationRelation, ...],
    assignments: tuple[RoadPropertyAssignment, ...],
) -> dict[str, Any]:
    """Build the legacy ``by_way_id`` view from pass-only reviewed mappings.

    This is an explicit compatibility projection for the current intersection
    road-detail classifier.  The scheme-qualified assertions remain the source
    of truth and are preserved under ``official_properties``.
    """

    assignment_index: dict[tuple[str, str, str, str], list[RoadPropertyAssignment]] = {}
    for assignment in assignments:
        if assignment.status != "pass":
            continue
        assignment_index.setdefault(assignment.target_ref.identity_key, []).append(assignment)

    candidates: dict[str, dict[str, Any]] = {}
    excluded: list[str] = []
    for relation in sorted(relations, key=lambda item: item.relation_id):
        if relation.status != "pass" or relation.relation_kind in {
            "unmatched",
            "contains_bounded_segment",
        }:
            excluded.append(relation.relation_id)
            continue
        refs = (*relation.left_refs, *relation.right_refs)
        osm_refs = [item for item in refs if item.namespace == "osm" and item.object_type == "way"]
        evidence_subjects = [
            item for item in refs if item.namespace == "canonical" or item.namespace.startswith("official.")
        ]
        if not osm_refs or not evidence_subjects:
            excluded.append(relation.relation_id)
            continue
        source_assignments = [
            assignment
            for ref in evidence_subjects
            for assignment in assignment_index.get(ref.identity_key, ())
            if assignment.s_from_m is None and assignment.s_to_m is None
        ]
        for osm_ref in osm_refs:
            item = candidates.setdefault(
                osm_ref.object_id,
                {
                    "values": {},
                    "source_relation_ids": set(),
                    "source_assignment_ids": set(),
                    "source_sha256s": set(),
                    "source_object_refs": {},
                },
            )
            item["source_relation_ids"].add(relation.relation_id)
            item["source_sha256s"].update(ref.source_sha256 for ref in refs if ref.source_sha256)
            for ref in evidence_subjects:
                item["source_object_refs"][ref.identity_key] = ref
            for assignment in source_assignments:
                item["values"].setdefault(assignment.property_name, set()).add(assignment.value)
                item["source_assignment_ids"].add(assignment.assignment_id)
                for evidence_ref in assignment.evidence_refs:
                    if evidence_ref.source_sha256:
                        item["source_sha256s"].add(evidence_ref.source_sha256)
                    item["source_object_refs"][evidence_ref.identity_key] = evidence_ref

    conflicts: list[dict[str, Any]] = []
    by_way_id: dict[str, dict[str, Any]] = {}
    for way_id, candidate in sorted(candidates.items()):
        properties: dict[str, JsonScalar] = {}
        way_conflicts: list[dict[str, Any]] = []
        property_assertions: list[dict[str, Any]] = []
        for property_name, values in sorted(candidate["values"].items()):
            if len(values) == 1:
                properties[property_name] = next(iter(values))
            else:
                conflict = {
                    "osm_way_id": way_id,
                    "property_name": property_name,
                    "values": sorted(values, key=lambda value: str(value)),
                }
                conflicts.append(conflict)
                way_conflicts.append(conflict)
        source_assignments = sorted(
            (
                assignment
                for ref in candidate["source_object_refs"].values()
                for assignment in assignment_index.get(ref.identity_key, ())
                if assignment.s_from_m is None and assignment.s_to_m is None
            ),
            key=lambda assignment: assignment.assignment_id,
        )
        for assignment in source_assignments:
            property_assertions.append(
                {
                    "assignment_id": assignment.assignment_id,
                    "property_name": assignment.property_name,
                    "classification_scheme": assignment.classification_scheme,
                    "value": assignment.value,
                    "evidence_refs": [item.as_dict() for item in assignment.evidence_refs],
                    "evidence_source_sha256s": sorted(
                        {
                            item.source_sha256
                            for item in assignment.evidence_refs
                            if item.source_sha256
                        }
                    ),
                }
            )
        relation_ids = sorted(candidate["source_relation_ids"])
        by_way_id[way_id] = {
            "authority_category": str(properties.get("hamburg_membership", "unknown")),
            "network_role": str(properties.get("network_role", "unknown")),
            "functional_category": str(properties.get("rin_category", "unknown")),
            "official_properties": properties,
            "official_property_assertions": property_assertions,
            "source_evidence_id": relation_ids[0] if relation_ids else "",
            "source_relation_ids": relation_ids,
            "source_assignment_ids": sorted(candidate["source_assignment_ids"]),
            "source_sha256s": sorted(candidate["source_sha256s"]),
            "source_object_refs": [ref.as_dict() for _, ref in sorted(candidate["source_object_refs"].items())],
            "mapping_status": "pass" if not way_conflicts else "review_required",
        }

    status = "pass"
    if conflicts or excluded or not by_way_id:
        status = "review_required"
    return {
        "schema": "torii.road-detail-evidence-projection/v1",
        "status": status,
        "by_way_id": by_way_id,
        "conflicts": conflicts,
        "excluded_relation_ids": sorted(set(excluded)),
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "claim_boundary": (
            "This compatibility view carries reviewed road identity properties only; it does not "
            "authorize junction joins, lane connections, signal binding, or SUMO materialization."
        ),
    }


def _strong_conflation_evidence(evidence: ConflationEvidence) -> bool:
    identity_agreement = max(
        evidence.source_object_id_agreement or 0.0,
        evidence.name_agreement or 0.0,
        evidence.road_ref_agreement or 0.0,
        evidence.official_road_key_agreement or 0.0,
    )
    return bool(
        evidence.geometry_overlap_ratio is not None
        and evidence.geometry_overlap_ratio >= 0.65
        and evidence.lateral_distance_m is not None
        and evidence.lateral_distance_m <= 15.0
        and evidence.heading_delta_deg is not None
        and evidence.heading_delta_deg <= 25.0
        and evidence.topology_agreement is not None
        and evidence.topology_agreement >= 0.75
        and identity_agreement >= 0.75
    )


def _validate_validity(valid_from: datetime | None, valid_to: datetime | None) -> None:
    if valid_from is not None:
        _require_aware(valid_from, "valid_from")
    if valid_to is not None:
        _require_aware(valid_to, "valid_to")
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        raise ValueError("valid_to must be later than valid_from")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_sha256(value: str, field_name: str) -> None:
    normalized = str(value).lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _ref_label(ref: RoadObjectRef) -> str:
    return f"{ref.namespace}:{ref.object_type}:{ref.object_id}"


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
