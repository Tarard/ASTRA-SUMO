from __future__ import annotations

from .osm_network import DEFAULT_ALLOWED_HIGHWAYS


DRIVE_HIGHWAYS = {
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
    "residential",
    "living_street",
    "road",
}

HIGHWAY_CLASS_PRESETS = {
    "arterial": set(DEFAULT_ALLOWED_HIGHWAYS),
    "drive": set(DRIVE_HIGHWAYS),
    "drive_plus_unclassified": set(DRIVE_HIGHWAYS) | {"unclassified"},
    "full_vehicle": set(DRIVE_HIGHWAYS) | {"unclassified", "service"},
}

ROAD_LEVEL_SCOPE_OPTIONS = ("arterial", "drive", "drive_plus_unclassified", "full_vehicle")
RECOMMENDED_ROAD_LEVEL_SCOPE = "full_vehicle"
ROAD_LEVEL_SCOPE_QUESTION = (
    "Which road level should Torii include: arterial, drive, drive_plus_unclassified, or full_vehicle?"
)


def resolve_highway_classes(value: str | set[str] | None, *, default_to_recommended: bool = True) -> set[str] | None:
    if value is None:
        return set(HIGHWAY_CLASS_PRESETS[RECOMMENDED_ROAD_LEVEL_SCOPE]) if default_to_recommended else None
    if isinstance(value, set):
        return set(value)

    normalized = value.strip().lower()
    if not normalized:
        return set(HIGHWAY_CLASS_PRESETS[RECOMMENDED_ROAD_LEVEL_SCOPE]) if default_to_recommended else None
    if normalized in HIGHWAY_CLASS_PRESETS:
        return set(HIGHWAY_CLASS_PRESETS[normalized])
    return {item.strip() for item in value.split(",") if item.strip()}
