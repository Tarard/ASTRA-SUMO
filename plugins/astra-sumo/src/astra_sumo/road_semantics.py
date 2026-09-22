from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import math
from typing import Any, Literal


ModeLayer = Literal["vehicle", "support", "fused_support_lane"]
IntersectionTurn = Literal["right", "straight", "left", "uturn"]
JunctionTurn = Literal["right", "straight", "left", "u_turn", "unknown"]

VEHICLE_CORE_TYPES = {
    "highway.primary",
    "highway.secondary",
    "highway.tertiary",
    "highway.unclassified",
    "highway.residential",
    "highway.living_street",
}

# Keep OSM source classification explicit.  Access tags answer whether a mode
# may use a way; they do not turn every ``highway=*`` feature (for example a
# platform) into a passenger road.
OSM_PASSENGER_HIGHWAYS = frozenset(
    {
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "living_street",
        "service",
        "road",
    }
)

_BLOCKING_ACCESS_VALUES = {"no", "private"}
_POSITIVE_ACCESS_VALUES = {"yes", "designated", "permissive"}
_MODE_ACCESS_HIERARCHY = {
    "passenger": ("access", "vehicle", "motor_vehicle"),
    "bicycle": ("access", "vehicle", "bicycle"),
    "pedestrian": ("access", "foot"),
}


@dataclass(frozen=True)
class ModeLayerClassification:
    mode_layer: ModeLayer
    is_vehicle_approach: bool
    is_support_only: bool
    fused_support_modes: tuple[frozenset[str], ...]


@dataclass(frozen=True)
class ModalRole:
    modal_primary_role: str
    modal_aggregation_decision: str
    modal_review_action: str
    modal_reason: str
    modal_risk_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "modal_primary_role": self.modal_primary_role,
            "modal_aggregation_decision": self.modal_aggregation_decision,
            "modal_review_action": self.modal_review_action,
            "modal_reason": self.modal_reason,
            "modal_risk_flags": list(self.modal_risk_flags),
        }


def classify_approach_mode_layer(
    allowed_modes: Iterable[str],
    incoming_extra_lane_modes: Iterable[Iterable[str]],
    outgoing_extra_lane_modes: Iterable[Iterable[str]],
) -> ModeLayerClassification:
    modes = {str(mode) for mode in allowed_modes}
    fused_support_modes = _deduped_mode_sets(incoming_extra_lane_modes, outgoing_extra_lane_modes)

    if "passenger" not in modes:
        return ModeLayerClassification(
            mode_layer="support",
            is_vehicle_approach=False,
            is_support_only=True,
            fused_support_modes=(),
        )
    if fused_support_modes:
        return ModeLayerClassification(
            mode_layer="fused_support_lane",
            is_vehicle_approach=True,
            is_support_only=False,
            fused_support_modes=fused_support_modes,
        )
    return ModeLayerClassification(
        mode_layer="vehicle",
        is_vehicle_approach=True,
        is_support_only=False,
        fused_support_modes=(),
    )


def filtered_osm_modes(tags: Mapping[str, str], base_modes: Iterable[str]) -> set[str]:
    base = {str(mode) for mode in base_modes}
    filtered = set(base - set(_MODE_ACCESS_HIERARCHY))
    for mode in base & set(_MODE_ACCESS_HIERARCHY):
        if _mode_allowed_by_hierarchy(tags, mode):
            filtered.add(mode)
    return filtered


def is_osm_passenger_way(tags: Mapping[str, str]) -> bool:
    """Return whether an OSM way belongs in a passenger-road graph."""

    return bool(
        _tag_value(tags, "highway") in OSM_PASSENGER_HIGHWAYS and "passenger" in filtered_osm_modes(tags, {"passenger"})
    )


def classify_turn_direction(in_axis: tuple[float, float], out_axis: tuple[float, float]) -> JunctionTurn:
    incoming = _unit(in_axis)
    outgoing = _unit(out_axis)
    if incoming == (0.0, 0.0) or outgoing == (0.0, 0.0):
        return "unknown"
    angle = math.degrees(math.acos(_clamp(incoming[0] * outgoing[0] + incoming[1] * outgoing[1])))
    if angle <= 45:
        return "straight"
    if angle >= 135:
        return "u_turn"
    cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
    return "left" if cross > 0 else "right"


def classify_turn_from_signed_delta(delta: float) -> IntersectionTurn:
    if abs(delta) > 155:
        return "uturn"
    if abs(delta) < 25:
        return "straight"
    return "right" if delta > 0 else "left"


def classify_modal_role_from_edge(edge: Mapping[str, Any]) -> ModalRole:
    type_id = str(edge.get("type", "") or "")
    function = str(edge.get("function", "") or "")
    text = " ".join(
        str(edge.get(key, "") or "").lower() for key in ("id", "type", "function", "allow", "disallow", "name")
    )
    if type_id.startswith("railway."):
        return _modal_role(
            "rail", "never_join", "railway edge must not be joined into vehicle core", ("railway_present",)
        )
    if type_id in {"highway.motorway", "highway.trunk"} or type_id.endswith("_link"):
        return _modal_role(
            "ramp",
            "never_join",
            "motorway/trunk/link geometry uses ramp or interchange semantics",
            ("ramp_or_interchange",),
        )
    if any(token in text for token in ("bridge", "tunnel", "layer=")):
        return _modal_role(
            "grade_separated",
            "never_join",
            "bridge/tunnel/layer evidence blocks same-level joining",
            ("grade_separation",),
        )
    if "roundabout" in text:
        return _modal_role("roundabout", "never_join", "roundabout topology should be preserved", ("roundabout",))
    if function in {"crossing", "walkingarea"} or "footway" in type_id or "crossing" in type_id:
        return _modal_role(
            "pedestrian",
            "shape_support",
            "pedestrian crossing/walkingarea supports review but not vehicle-core joining",
            ("pedestrian_support",),
        )
    if "cycleway" in type_id or "bicycle" in text:
        return _modal_role(
            "bicycle",
            "shape_support",
            "bicycle infrastructure is support evidence unless map/reference includes it in the core",
            ("bicycle_support",),
        )
    if "service" in type_id or any(
        token in text for token in ("driveway", "parking_aisle", "parking", "private", "alley")
    ):
        return _modal_role(
            "service",
            "protected_terminal",
            "service or parking access is a protected terminal by default",
            ("service_terminal_present",),
        )
    if type_id in VEHICLE_CORE_TYPES or "passenger" in text:
        return _modal_role("vehicle_core", "join_core", "ordinary passenger-drivable urban road", ())
    return _modal_role(
        "unknown", "review_required", "modal role is unknown from SUMO edge attributes", ("unknown_modal_role",)
    )


def _deduped_mode_sets(
    incoming_extra_lane_modes: Iterable[Iterable[str]],
    outgoing_extra_lane_modes: Iterable[Iterable[str]],
) -> tuple[frozenset[str], ...]:
    deduped: list[frozenset[str]] = []
    seen: set[frozenset[str]] = set()
    for raw_modes in (*incoming_extra_lane_modes, *outgoing_extra_lane_modes):
        modes = frozenset(str(mode) for mode in raw_modes if mode)
        if not modes or modes in seen:
            continue
        seen.add(modes)
        deduped.append(modes)
    return tuple(deduped)


def _tag_value(tags: Mapping[str, str], key: str) -> str:
    return str(tags.get(key, "") or "").strip().lower()


def _mode_allowed_by_hierarchy(tags: Mapping[str, str], mode: str) -> bool:
    allowed = True
    for tag_name in _MODE_ACCESS_HIERARCHY[mode]:
        value = _tag_value(tags, tag_name)
        if value in _BLOCKING_ACCESS_VALUES:
            allowed = False
        elif value in _POSITIVE_ACCESS_VALUES:
            allowed = True
    return allowed


def _unit(axis: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(axis[0], axis[1])
    if length == 0:
        return (0.0, 0.0)
    return (axis[0] / length, axis[1] / length)


def _clamp(value: float) -> float:
    return max(-1.0, min(1.0, value))


def _modal_role(primary: str, decision: str, reason: str, flags: tuple[str, ...]) -> ModalRole:
    return ModalRole(
        modal_primary_role=primary,
        modal_aggregation_decision=decision,
        modal_review_action=_modal_review_action(decision),
        modal_reason=reason,
        modal_risk_flags=flags,
    )


def _modal_review_action(decision: str) -> str:
    if decision in {"never_join", "protected_terminal"}:
        return "exclude_from_join"
    if decision == "join_core":
        return "safe_vehicle_core_candidate"
    if decision == "shape_support":
        return "review_modal_support"
    return "review_vehicle_core_boundary"
