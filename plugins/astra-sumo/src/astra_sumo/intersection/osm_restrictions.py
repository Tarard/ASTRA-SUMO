from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .schema import Approach, IntersectionCore, OSMPatch


BlockedTurn = Literal["right", "straight", "left", "uturn"]
RestrictionMode = Literal["no", "only"]
TypedMemberRef = tuple[str, str]

SUPPORTED_RESTRICTIONS: dict[str, tuple[RestrictionMode, BlockedTurn]] = {
    "no_left_turn": ("no", "left"),
    "no_right_turn": ("no", "right"),
    "no_straight_on": ("no", "straight"),
    "no_u_turn": ("no", "uturn"),
    "only_left_turn": ("only", "left"),
    "only_right_turn": ("only", "right"),
    "only_straight_on": ("only", "straight"),
}


@dataclass(frozen=True)
class MovementRestriction:
    relation_id: str
    restriction_type: str
    from_way_id: str
    to_way_id: str
    via_refs: tuple[TypedMemberRef, ...]
    mode: RestrictionMode
    blocked_turn: BlockedTurn | None
    allowed_turn: BlockedTurn | None

    @property
    def evidence(self) -> str:
        return f"osm_restriction:{self.relation_id}:{self.restriction_type}"


def extract_turn_restrictions(
    patch: OSMPatch,
    core: IntersectionCore,
) -> tuple[list[MovementRestriction], list[str]]:
    restrictions: list[MovementRestriction] = []
    warnings: list[str] = []
    core_refs: set[TypedMemberRef] = {
        *{("node", node_id) for node_id in core.core_osm_node_ids},
        *{("way", way_id) for way_id in core.core_way_ids},
    }

    for relation in patch.relations.values():
        if relation.tags.get("type") != "restriction":
            continue

        via_refs = tuple(_typed_member_refs(relation.members, role="via", allowed_types={"node", "way"}))
        if via_refs and core_refs and not (set(via_refs) & core_refs):
            continue

        restriction_type = str(relation.tags.get("restriction", "")).strip().lower()
        if not restriction_type:
            warnings.append(f"osm_restriction:{relation.id}:missing_restriction_type")
            continue

        restriction_semantics = SUPPORTED_RESTRICTIONS.get(restriction_type)
        if restriction_semantics is None:
            warnings.append(f"osm_restriction:{relation.id}:unknown_restriction_type:{restriction_type}")
            continue
        mode, restriction_turn = restriction_semantics

        from_way_ids = _member_refs(relation.members, role="from", allowed_types={"way"})
        to_way_ids = _member_refs(relation.members, role="to", allowed_types={"way"})
        missing_parts = []
        if not from_way_ids:
            missing_parts.append("from_way")
        if not via_refs:
            missing_parts.append("via")
        if not to_way_ids:
            missing_parts.append("to_way")
        if missing_parts:
            warnings.append(f"osm_restriction:{relation.id}:incomplete:{','.join(missing_parts)}")
            continue
        if len(from_way_ids) > 1 or len(to_way_ids) > 1:
            warnings.append(f"osm_restriction:{relation.id}:ambiguous:multiple_from_or_to")
            continue
        if mode == "no" and restriction_turn == "uturn" and from_way_ids[0] == to_way_ids[0]:
            warnings.append(f"osm_restriction:{relation.id}:ambiguous:shared_way_direction")

        restrictions.append(
            MovementRestriction(
                relation_id=relation.id,
                restriction_type=restriction_type,
                from_way_id=from_way_ids[0],
                to_way_id=to_way_ids[0],
                via_refs=via_refs,
                mode=mode,
                blocked_turn=restriction_turn if mode == "no" else None,
                allowed_turn=restriction_turn if mode == "only" else None,
            )
        )

    return restrictions, warnings


def restriction_for_movement(
    restrictions: list[MovementRestriction],
    source: Approach,
    target: Approach,
    turn: str,
    approaches: list[Approach] | None = None,
) -> MovementRestriction | None:
    source_way_ids = set(source.source_way_ids)
    target_way_ids = set(target.source_way_ids)
    # Supported OSM relation from/to members are authoritative for matching here;
    # decoded turn labels remain movement metadata for future geometry-aware use.
    for restriction in restrictions:
        if restriction.mode != "no":
            continue
        if restriction.from_way_id not in source_way_ids or restriction.to_way_id not in target_way_ids:
            continue
        if _has_ambiguous_directional_member(restriction, approaches):
            if turn == restriction.blocked_turn:
                return restriction
            continue
        return restriction
    only_restrictions = [
        restriction
        for restriction in restrictions
        if (
            restriction.mode == "only"
            and restriction.from_way_id in source_way_ids
        )
    ]
    for restriction in only_restrictions:
        if _has_ambiguous_directional_member(restriction, approaches):
            if turn != restriction.allowed_turn:
                return restriction
            continue
        if restriction.to_way_id not in target_way_ids:
            return restriction
    return None


def _has_ambiguous_directional_member(
    restriction: MovementRestriction,
    approaches: list[Approach] | None,
) -> bool:
    # An unsplit bidirectional OSM way can back opposite approaches. Until we
    # disambiguate relation member direction from geometry, skip rather than guess.
    return (
        _has_multiple_directional_approaches(restriction.from_way_id, approaches)
        or _has_multiple_directional_approaches(restriction.to_way_id, approaches)
    )


def _has_multiple_directional_approaches(way_id: str, approaches: list[Approach] | None) -> bool:
    if approaches is None:
        return True
    return sum(way_id in approach.source_way_ids for approach in approaches) > 1


def _typed_member_refs(
    members: list[dict[str, str]],
    *,
    role: str,
    allowed_types: set[str],
) -> list[TypedMemberRef]:
    refs: list[TypedMemberRef] = []
    seen: set[TypedMemberRef] = set()
    for member in members:
        ref = str(member.get("ref", "")).strip()
        member_type = str(member.get("type", "")).strip()
        typed_ref = (member_type, ref)
        if not ref or typed_ref in seen:
            continue
        if member.get("role") == role and member_type in allowed_types:
            refs.append(typed_ref)
            seen.add(typed_ref)
    return refs


def _member_refs(
    members: list[dict[str, str]],
    *,
    role: str,
    allowed_types: set[str],
) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for member in members:
        ref = str(member.get("ref", "")).strip()
        if not ref or ref in seen:
            continue
        if member.get("role") == role and member.get("type") in allowed_types:
            refs.append(ref)
            seen.add(ref)
    return refs
