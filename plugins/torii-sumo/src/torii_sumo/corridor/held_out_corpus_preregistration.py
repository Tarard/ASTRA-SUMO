from __future__ import annotations

from pathlib import Path

from torii_sumo.core.candidate_contracts import file_sha256

from .enums import TrafficSide
from .held_out_corpus_contracts import (
    GeographicBbox,
    HeldOutCityExtract,
    HeldOutCorpusSpec,
    HeldOutCorridorSelection,
    HeldOutNetworkBuildProfile,
)
from .held_out_review_contracts import HeldOutReviewPolicy
from .ids import stable_id


_CITY_EXTRACTS = (
    {
        "source_id": "berlin-bbbike-20260711",
        "city_group": "berlin",
        "traffic_side": TrafficSide.RIGHT,
        "provider_md5": "1d725e5434422f94dc6fbe6b0b3fa5c7",
        "expected_content_length_bytes": 180357428,
        "expected_last_modified_http": "Sat, 11 Jul 2026 16:50:24 GMT",
        "expected_etag": '"641849792"',
    },
    {
        "source_id": "amsterdam-bbbike-20260711",
        "city_group": "amsterdam",
        "traffic_side": TrafficSide.RIGHT,
        "provider_md5": "7dcd57d390fc556bda923cab1adc81e2",
        "expected_content_length_bytes": 141948365,
        "expected_last_modified_http": "Sat, 11 Jul 2026 16:32:13 GMT",
        "expected_etag": '"1878350408"',
    },
    {
        "source_id": "paris-bbbike-20260711",
        "city_group": "paris",
        "traffic_side": TrafficSide.RIGHT,
        "provider_md5": "8e8b339ae51b2aa8d432b4b3421b7615",
        "expected_content_length_bytes": 239050771,
        "expected_last_modified_http": "Sat, 11 Jul 2026 16:55:25 GMT",
        "expected_etag": '"874712177"',
    },
    {
        "source_id": "london-bbbike-20260711",
        "city_group": "london",
        "traffic_side": TrafficSide.LEFT,
        "provider_md5": "adc2c11257c3d8c44e43681423ed2090",
        "expected_content_length_bytes": 196666081,
        "expected_last_modified_http": "Sat, 11 Jul 2026 16:55:25 GMT",
        "expected_etag": '"3590488792"',
    },
    {
        "source_id": "melbourne-bbbike-20260711",
        "city_group": "melbourne",
        "traffic_side": TrafficSide.LEFT,
        "provider_md5": "8a37dcda40cbf84614a026bdb3daa1e6",
        "expected_content_length_bytes": 86429050,
        "expected_last_modified_http": "Sat, 11 Jul 2026 16:21:37 GMT",
        "expected_etag": '"3843497657"',
    },
    {
        "source_id": "sydney-bbbike-20260711",
        "city_group": "sydney",
        "traffic_side": TrafficSide.LEFT,
        "provider_md5": "480d263525806f549648c7c518e1fcdb",
        "expected_content_length_bytes": 55931710,
        "expected_last_modified_http": "Sat, 11 Jul 2026 16:21:37 GMT",
        "expected_etag": '"2264562845"',
    },
)


_CORRIDORS = (
    # Right-hand traffic development-held-out cities.
    ("berlin-alexanderplatz", "berlin-bbbike-20260711", "Alexanderplatz", 52.5219, 13.4132, 0.0060, 0.0098, "multimodal", ("pedestrian", "bicycle", "rail")),
    ("berlin-potsdamer-platz", "berlin-bbbike-20260711", "Potsdamer Platz", 52.5096, 13.3760, 0.0060, 0.0098, "historic-core", ("pedestrian", "bicycle", "rail")),
    ("berlin-oberbaum-bridge", "berlin-bbbike-20260711", "Oberbaum Bridge", 52.5017, 13.4450, 0.0060, 0.0098, "bridge-tunnel", ("pedestrian", "bicycle", "rail", "bridge")),
    ("berlin-tiergarten-tunnel", "berlin-bbbike-20260711", "Tiergarten tunnel", 52.5279, 13.3594, 0.0060, 0.0098, "bridge-tunnel", ("pedestrian", "bicycle", "tunnel")),
    ("berlin-funkturm-interchange", "berlin-bbbike-20260711", "Funkturm interchange", 52.5015, 13.2795, 0.0060, 0.0098, "ramp-interchange", ("ramp", "bridge", "rail")),
    ("amsterdam-dam", "amsterdam-bbbike-20260711", "Dam", 52.3731, 4.8922, 0.0060, 0.0098, "historic-core", ("pedestrian", "bicycle", "rail")),
    ("amsterdam-museumplein", "amsterdam-bbbike-20260711", "Museumplein", 52.3579, 4.8810, 0.0060, 0.0098, "multimodal", ("pedestrian", "bicycle")),
    ("amsterdam-ijtunnel", "amsterdam-bbbike-20260711", "IJtunnel", 52.3770, 4.9125, 0.0060, 0.0098, "bridge-tunnel", ("bicycle", "bridge", "tunnel")),
    ("amsterdam-amstel-bridge", "amsterdam-bbbike-20260711", "Amstel bridge", 52.3498, 4.9080, 0.0060, 0.0098, "bridge-tunnel", ("pedestrian", "bicycle", "bridge")),
    ("amsterdam-a10-amstel", "amsterdam-bbbike-20260711", "A10 Amstel interchange", 52.3314, 4.9160, 0.0060, 0.0098, "ramp-interchange", ("bicycle", "ramp", "rail", "bridge")),
    ("paris-arc-de-triomphe", "paris-bbbike-20260711", "Arc de Triomphe", 48.8738, 2.2950, 0.0060, 0.0090, "historic-core", ("pedestrian", "bicycle")),
    ("paris-bastille", "paris-bbbike-20260711", "Bastille", 48.8530, 2.3690, 0.0060, 0.0090, "historic-core", ("pedestrian", "bicycle")),
    ("paris-bercy-bridge", "paris-bbbike-20260711", "Bercy bridge", 48.8386, 2.3795, 0.0060, 0.0090, "bridge-tunnel", ("pedestrian", "bicycle", "rail", "bridge")),
    ("paris-porte-maillot", "paris-bbbike-20260711", "Porte Maillot", 48.8780, 2.2820, 0.0060, 0.0090, "ramp-interchange", ("pedestrian", "bicycle", "ramp", "rail")),
    ("paris-tuileries-tunnel", "paris-bbbike-20260711", "Tuileries tunnel", 48.8615, 2.3270, 0.0060, 0.0090, "bridge-tunnel", ("pedestrian", "bicycle", "tunnel")),
    # Left-hand traffic generalization cities.
    ("london-parliament", "london-bbbike-20260711", "Parliament Square", 51.5007, -0.1246, 0.0060, 0.0097, "historic-core", ("pedestrian", "bicycle", "bridge")),
    ("london-tower-bridge", "london-bbbike-20260711", "Tower Bridge", 51.5055, -0.0754, 0.0060, 0.0097, "bridge-tunnel", ("pedestrian", "bicycle", "bridge")),
    ("london-hyde-park-corner", "london-bbbike-20260711", "Hyde Park Corner", 51.5027, -0.1505, 0.0060, 0.0097, "multimodal", ("pedestrian", "bicycle")),
    ("london-kings-cross", "london-bbbike-20260711", "King's Cross", 51.5308, -0.1238, 0.0060, 0.0097, "tram-rail", ("pedestrian", "bicycle", "rail")),
    ("london-blackwall-tunnel", "london-bbbike-20260711", "Blackwall tunnel", 51.5095, -0.0070, 0.0060, 0.0097, "ramp-interchange", ("ramp", "tunnel")),
    ("melbourne-flinders-swanston", "melbourne-bbbike-20260711", "Flinders and Swanston", -37.8179, 144.9670, 0.0060, 0.0076, "tram-rail", ("pedestrian", "bicycle", "rail")),
    ("melbourne-royal-parade", "melbourne-bbbike-20260711", "Royal Parade", -37.8008, 144.9574, 0.0060, 0.0076, "divided-arterial", ("pedestrian", "bicycle", "rail")),
    ("melbourne-west-gate-bridge", "melbourne-bbbike-20260711", "West Gate Bridge", -37.8290, 144.9080, 0.0060, 0.0076, "ramp-interchange", ("ramp", "bridge")),
    ("melbourne-burnley-tunnel", "melbourne-bbbike-20260711", "Burnley tunnel", -37.8290, 145.0170, 0.0060, 0.0076, "bridge-tunnel", ("ramp", "tunnel")),
    ("melbourne-punt-toorak", "melbourne-bbbike-20260711", "Punt and Toorak", -37.8390, 144.9860, 0.0060, 0.0076, "multimodal", ("pedestrian", "bicycle", "rail")),
    ("sydney-george-park", "sydney-bbbike-20260711", "George and Park", -33.8730, 151.2067, 0.0060, 0.0073, "historic-core", ("pedestrian", "bicycle", "rail")),
    ("sydney-harbour-bridge", "sydney-bbbike-20260711", "Sydney Harbour Bridge", -33.8523, 151.2108, 0.0060, 0.0073, "bridge-tunnel", ("pedestrian", "bicycle", "rail", "bridge")),
    ("sydney-cross-city-tunnel", "sydney-bbbike-20260711", "Cross City Tunnel", -33.8745, 151.2215, 0.0060, 0.0073, "bridge-tunnel", ("pedestrian", "bicycle", "tunnel")),
    ("sydney-central", "sydney-bbbike-20260711", "Central Station", -33.8830, 151.2063, 0.0060, 0.0073, "tram-rail", ("pedestrian", "bicycle", "rail")),
    ("sydney-anzac-cleveland", "sydney-bbbike-20260711", "Anzac and Cleveland", -33.8915, 151.2205, 0.0060, 0.0073, "divided-arterial", ("pedestrian", "bicycle", "rail")),
)


def build_preregistered_held_out_corpus(
    *,
    held_out_review_policy_file: Path,
    parent_benchmark_file: Path,
) -> HeldOutCorpusSpec:
    policy_path = held_out_review_policy_file.resolve()
    benchmark_path = parent_benchmark_file.resolve()
    policy = HeldOutReviewPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    benchmark_sha256 = file_sha256(benchmark_path)
    if policy.parent_benchmark_sha256 != benchmark_sha256:
        raise ValueError("Held-out review policy is not bound to the parent benchmark.")
    sources = tuple(_build_city_extract(item) for item in _CITY_EXTRACTS)
    corridors = tuple(_build_corridor(item) for item in _CORRIDORS)
    profile = HeldOutNetworkBuildProfile(
        allowed_highways=tuple(
            sorted(
                {
                    "cycleway",
                    "footway",
                    "living_street",
                    "motorway",
                    "motorway_link",
                    "path",
                    "pedestrian",
                    "primary",
                    "primary_link",
                    "residential",
                    "secondary",
                    "secondary_link",
                    "service",
                    "steps",
                    "tertiary",
                    "tertiary_link",
                    "track",
                    "trunk",
                    "trunk_link",
                    "unclassified",
                }
            )
        ),
        allowed_railways=tuple(
            sorted(
                {
                    "funicular",
                    "light_rail",
                    "monorail",
                    "narrow_gauge",
                    "rail",
                    "subway",
                    "tram",
                }
            )
        ),
        routeability_vehicle_count=20,
        routeability_seed=20260714,
    )
    payload = {
        "held_out_review_policy_sha256": file_sha256(policy_path),
        "parent_benchmark_sha256": benchmark_sha256,
        "provider_attribution": "© OpenStreetMap contributors",
        "provider_license": "Open Data Commons Open Database License (ODbL)",
        "provider_license_url": "https://opendatacommons.org/licenses/odbl/",
        "snapshot_selection_cutoff": "2026-07-14T00:00:00+02:00",
        "minimum_case_count": policy.minimum_case_count,
        "minimum_city_group_count": policy.minimum_city_group_count,
        "minimum_morphology_count": policy.minimum_morphology_count,
        "minimum_cases_per_city_group": policy.minimum_cases_per_city_group,
        "required_traffic_sides": policy.required_traffic_sides,
        "required_mode_features": policy.required_mode_features,
        "network_build_profile": profile,
        "city_extracts": sources,
        "corridors": corridors,
    }
    return HeldOutCorpusSpec(
        corpus_id=stable_id("manifest", _corpus_identity_payload(payload)),
        **payload,
    )


def _build_city_extract(item: dict[str, object]) -> HeldOutCityExtract:
    city = str(item["city_group"])
    base_url = f"https://download.bbbike.org/osm/bbbike/{city.title()}"
    return HeldOutCityExtract(
        **item,
        pbf_url=f"{base_url}/{city.title()}.osm.pbf",
        checksum_url=f"{base_url}/CHECKSUM.txt",
    )


def _build_corridor(item: tuple[object, ...]) -> HeldOutCorridorSelection:
    (
        corridor_key,
        city_source_id,
        label,
        center_lat,
        center_lon,
        half_lat,
        half_lon,
        morphology,
        targets,
    ) = item
    bbox = GeographicBbox(
        west=round(float(center_lon) - float(half_lon), 6),
        south=round(float(center_lat) - float(half_lat), 6),
        east=round(float(center_lon) + float(half_lon), 6),
        north=round(float(center_lat) + float(half_lat), 6),
    )
    payload = {
        "corridor_key": str(corridor_key),
        "city_source_id": str(city_source_id),
        "center_lat": float(center_lat),
        "center_lon": float(center_lon),
        "bbox": bbox.model_dump(mode="json", by_alias=True),
        "morphology": str(morphology),
        "preregistered_feature_targets": tuple(str(value) for value in targets),
    }
    return HeldOutCorridorSelection(
        selection_id=stable_id("scope", payload),
        **payload,
        label=str(label),
        selection_basis=(
            "Landmark-centered corridor selected before OSM tag extraction; frozen "
            "feature targets must be confirmed from the bound snapshot or reported "
            "as unresolved."
        ),
    )


def _corpus_identity_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        **payload,
        "network_build_profile": payload["network_build_profile"].model_dump(
            mode="json", by_alias=True
        ),
        "city_extracts": [
            source.model_dump(mode="json", by_alias=True)
            for source in payload["city_extracts"]
        ],
        "corridors": [
            corridor.model_dump(mode="json", by_alias=True)
            for corridor in payload["corridors"]
        ],
    }
