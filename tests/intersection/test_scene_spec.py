import pytest

from torii_sumo.intersection.scene_spec import resolve_intersection_scene_prompt


def test_resolve_four_way_signalized_scene() -> None:
    spec = resolve_intersection_scene_prompt(
        "  Build a FOUR WAY intersection with a traffic light for passenger cars.  "
    )

    assert spec.model_dump(mode="json") == {
        "schema_version": "intersection-scene/v1",
        "topology": "four_way",
        "approach_count": 4,
        "control": "traffic_light",
        "controller": "nema_reference",
        "allowed_modes": ["passenger"],
        "pedestrian_crossing": False,
        "bicycle_support": False,
        "ramp": False,
        "tls_semantics": "nema",
        "link_length_m": 180.0,
        "speed_mps": 13.89,
        "smoke_route": ["W", "E"],
        "assumptions": [
            "NEMA is a synthetic/defaulted reference controller, not field-calibrated timing."
        ],
    }


@pytest.mark.parametrize("signalization", ["traffic lights", "TLS", "signalized"])
def test_resolves_supported_signalization_terms(signalization: str) -> None:
    spec = resolve_intersection_scene_prompt(
        f"Build a four-way intersection that is {signalization}."
    )

    assert spec.control == "traffic_light"


def test_rejects_unsupported_three_way_priority_scene() -> None:
    with pytest.raises(ValueError, match="Phase 1.*four-way signalized"):
        resolve_intersection_scene_prompt("Build a three-way priority intersection.")


@pytest.mark.parametrize(
    ("prompt", "topology", "approach_count", "controller"),
    [
        ("Build a three-way signalized intersection.", "three_way", 3, "fixed_time"),
        ("Build a five-way signalized intersection.", "five_way", 5, "fixed_time"),
        ("Build a four-way signalized intersection with fixed-time control.", "four_way", 4, "fixed_time"),
        ("Build a four-way signalized intersection with protected-permissive control.", "four_way", 4, "protected_permissive"),
    ],
)
def test_resolves_phase_two_topology_and_controller_terms(
    prompt: str,
    topology: str,
    approach_count: int,
    controller: str,
) -> None:
    spec = resolve_intersection_scene_prompt(prompt)

    assert spec.topology == topology
    assert spec.approach_count == approach_count
    assert spec.controller == controller
    assert spec.tls_semantics == controller if controller != "protected_permissive" else "protected_permissive"


def test_resolves_phase_two_mode_and_ramp_features() -> None:
    spec = resolve_intersection_scene_prompt(
        "Build a five-way signalized intersection with pedestrian, bicycle and ramp support."
    )

    assert spec.pedestrian_crossing is True
    assert spec.bicycle_support is True
    assert spec.ramp is True
    assert spec.allowed_modes == {"passenger", "pedestrian", "bicycle"}


@pytest.mark.parametrize(
    "prompt",
    [
        "Build a four-way unsignalized intersection.",
        "Build a four-way non-signalized intersection.",
        "Build a four-way non signalized intersection.",
        "Build a four-way not signalized intersection.",
        "Build a four-way not-signalized intersection.",
        "Build a four-way intersection that is not a signalized intersection.",
        "Build a four-way intersection that is not-a-signalized intersection.",
        "Build a four-way intersection with no traffic light.",
        "Build a four-way intersection with no traffic lights.",
        "Build a four-way intersection without a traffic light.",
        "Build a four-way intersection without traffic lights.",
        "Build a four-way intersection without any traffic lights.",
        "Build a four-way intersection that isn't signalized.",
        "Build a four-way intersection that does not have traffic lights.",
        "Build a four-way intersection that isn't controlled by traffic lights.",
        "Build a four-way never-signalized intersection.",
        "Build a four-way intersection with neither TLS nor traffic lights.",
        "Build a four-way intersection that is not actually signalized.",
        "Build a four-way intersection, not traffic light controlled.",
        "Build a four-way intersection, not traffic lights controlled.",
        "Build a four-way intersection with no TLS.",
        "Build a four-way intersection without TLS.",
        "Build a four-way intersection, not TLS controlled.",
    ],
)
def test_rejects_explicitly_non_signalized_scene(prompt: str) -> None:
    with pytest.raises(ValueError, match="Phase 1.*four-way signalized"):
        resolve_intersection_scene_prompt(prompt)


@pytest.mark.parametrize("near_match", ["tlssuffix", "traffic-lighting", "signalizedness"])
def test_rejects_signalization_token_near_matches(near_match: str) -> None:
    with pytest.raises(ValueError, match="Phase 1.*four-way signalized"):
        resolve_intersection_scene_prompt(
            f"Build a four-way intersection with {near_match}."
        )


@pytest.mark.parametrize(
    "unsupported_feature",
    ["bus", "truck", "taxi", "taxis", "all modes", "tram", "rail", "motorcycle"],
)
def test_rejects_explicitly_unsupported_features(
    unsupported_feature: str,
) -> None:
    with pytest.raises(ValueError, match="Phase 1.*passenger"):
        resolve_intersection_scene_prompt(
            f"Build a four-way signalized intersection with {unsupported_feature} access."
        )
