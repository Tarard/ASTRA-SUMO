from __future__ import annotations

import re

from pydantic import BaseModel, Field


class IntersectionSceneSpec(BaseModel):
    schema_version: str = "intersection-scene/v1"
    topology: str = "four_way"
    approach_count: int = 4
    control: str = "traffic_light"
    controller: str = "nema_reference"
    allowed_modes: set[str] = Field(default_factory=lambda: {"passenger"})
    pedestrian_crossing: bool = False
    bicycle_support: bool = False
    ramp: bool = False
    tls_semantics: str = "nema"
    link_length_m: float = 180.0
    speed_mps: float = 13.89
    smoke_route: tuple[str, str] = ("W", "E")
    assumptions: tuple[str, ...] = (
        "NEMA is a synthetic/defaulted reference controller, not field-calibrated timing.",
    )


_FOUR_WAY = re.compile(r"\b(?:four[- ]way|4[- ]way|x4)\b")
_THREE_WAY = re.compile(r"\b(?:three[- ]way|3[- ]way|t[- ]junction|t[- ]intersection)\b")
_FIVE_WAY = re.compile(r"\b(?:five[- ]way|5[- ]way)\b")
_SIGNALIZED = re.compile(r"\b(?:tls|traffic[- ]lights?|signalized)\b")
_UNSUPPORTED_FEATURE = re.compile(
    r"\b(?:all[- ]modes|bus(?:es)?|trucks?|"
    r"taxis?|trams?|rail|motorcycles?)\b"
)
_UNSUPPORTED = (
    "Unsupported intersection scene for Phase 1; describe a four-way signalized/TLS "
    "passenger intersection, or a Phase 2 signalized 3/5-way scene with supported "
    "pedestrian, bicycle, and ramp options."
)


def _has_negated_signalization(text: str) -> bool:
    if re.search(r"\bunsignalized\b", text):
        return True
    tokens = re.findall(r"[a-z]+(?:['’][a-z]+)?", text)
    markers = {
        index
        for index, token in enumerate(tokens)
        if token in {"no", "not", "never", "without", "neither", "non", "isn't", "isn’t"}
    }
    signals = {
        index
        for index, token in enumerate(tokens)
        if token in {"tls", "signalized"}
        or (
            token == "traffic"
            and index + 1 < len(tokens)
            and tokens[index + 1] in {"light", "lights"}
        )
    }
    # ponytail: five tokens is the Phase-1 phrase window; use a parser if grammar expands.
    return any(1 <= signal - marker <= 5 for marker in markers for signal in signals)


def resolve_intersection_scene_prompt(prompt: str) -> IntersectionSceneSpec:
    normalized = " ".join(prompt.casefold().split())
    if not normalized or not _SIGNALIZED.search(normalized) or _has_negated_signalization(normalized):
        raise ValueError(_UNSUPPORTED)
    if _FOUR_WAY.search(normalized):
        topology = "four_way"
        approach_count = 4
        labels = ("W", "E")
    elif _THREE_WAY.search(normalized):
        topology = "three_way"
        approach_count = 3
        labels = ("A0", "A1")
    elif _FIVE_WAY.search(normalized):
        topology = "five_way"
        approach_count = 5
        labels = ("A0", "A1")
    else:
        raise ValueError(_UNSUPPORTED)
    if _UNSUPPORTED_FEATURE.search(normalized):
        raise ValueError(_UNSUPPORTED)

    pedestrian_crossing = bool(
        re.search(r"\b(?:pedestrian|ped|walking|crosswalk|sidewalk)s?\b", normalized)
    )
    bicycle_support = bool(
        re.search(r"\b(?:bicycle|bike|biking|cycle|cyclist)s?\b", normalized)
    )
    ramp = bool(re.search(r"\b(?:ramp|slip[- ]lane|motorway[- ]link)\b", normalized))

    if re.search(r"\b(?:actuated|adaptive)\b", normalized):
        controller = "actuated"
        tls_semantics = "actuated"
    elif re.search(r"\b(?:protected[- ]permissive|permissive)\b", normalized):
        controller = "protected_permissive"
        tls_semantics = "protected_permissive"
    elif re.search(r"\b(?:fixed[- ]time|fixed[- ]cycle|static)\b", normalized):
        controller = "fixed_time"
        tls_semantics = "fixed_time"
    elif topology == "four_way":
        controller = "nema_reference"
        tls_semantics = "nema"
    else:
        controller = "fixed_time"
        tls_semantics = "fixed_time"

    allowed_modes = {"passenger"}
    if pedestrian_crossing:
        allowed_modes.add("pedestrian")
    if bicycle_support:
        allowed_modes.add("bicycle")

    link_length_m = _number_before_unit(normalized, "m", default=180.0)
    speed_mps = _number_before_unit(normalized, "m/s", default=13.89)
    assumptions = [
        "NEMA is a synthetic/defaulted reference controller, not field-calibrated timing."
    ]
    if controller != "nema_reference":
        assumptions.append(f"TLS controller semantics requested: {controller}.")
    if pedestrian_crossing:
        assumptions.append("Pedestrian crossings are explicit support geometry and are not inferred from vehicle lanes.")
    if bicycle_support:
        assumptions.append("Bicycle support is represented by an explicit bicycle-capable lane and route smoke.")
    if ramp:
        assumptions.append("The first approach is tagged as a ramp/motorway-link approach for review.")
    return IntersectionSceneSpec(
        topology=topology,
        approach_count=approach_count,
        controller=controller,
        allowed_modes=allowed_modes,
        pedestrian_crossing=pedestrian_crossing,
        bicycle_support=bicycle_support,
        ramp=ramp,
        tls_semantics=tls_semantics,
        link_length_m=link_length_m,
        speed_mps=speed_mps,
        smoke_route=labels,
        assumptions=tuple(assumptions),
    )


def _number_before_unit(text: str, unit: str, *, default: float) -> float:
    if unit == "m":
        unit_pattern = r"m(?!\s*/\s*s)"
    else:
        unit_pattern = re.escape(unit)
    match = re.search(rf"\b(\d+(?:\.\d+)?)\s*{unit_pattern}\b", text)
    return float(match.group(1)) if match else default
