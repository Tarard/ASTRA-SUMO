"""Hash-bound evidence contract for screening a Hamburg corridor candidate.

This module deliberately stops before network stitching.  It answers a smaller
question first: do the requested official LSA cells, static MAP/OCIT topology,
motor-vehicle count streams, and official HH-SIB axis links form a credible
candidate for the next stage?  Local MAP cells still need an explicit
HH-SIB-to-cell anchor and lane-transition proof before a combined SUMO network
may be materialized.
"""

from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from pyproj import Transformer

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


HAMBURG_CORRIDOR_CANDIDATE_SCHEMA = "torii.hamburg-corridor-candidate-evidence/v1"
HAMBURG_CORRIDOR_SCREENING_SCHEMA = "torii.hamburg-five-corridor-screening/v1"
HAMBURG_CORRIDOR_SELECTION_SCHEMA = "torii.hamburg-five-corridor-selection/v1"

_FIVE_CORRIDOR_PROTOCOL = "hamburg-five-intersection-v1+A1+A2"
_REGISTERED_FALLBACK_NODES = ("104", "118", "119", "200", "535")
_REGISTERED_FALLBACK_WINDOW = {
    "simulation_begin_utc": "2026-07-11T15:10:00Z",
    "formal_begin_utc": "2026-07-11T15:40:00Z",
    "formal_end_utc": "2026-07-11T17:40:00Z",
}
_STRICT_SELECTION_GATES = (
    "exactly_five_distinct_k_lsa",
    "official_lsa_identity",
    "official_road_simple_path",
    "no_skipped_intervening_k_lsa",
    "exact_static_asset_triplets",
    "motor_vehicle_primary_tld_metadata",
    "motor_vehicle_detector_metadata",
    "complete_detector_window",
    "common_detector_tld_window",
    "publication_license",
)
_PROTOTYPE_HARD_GATES = (
    "exactly_five_distinct_k_lsa",
    "official_lsa_identity",
    "official_road_simple_path",
    "no_skipped_intervening_k_lsa",
    "publication_license",
)
_PROTOTYPE_ADVISORY_GATES = tuple(
    gate for gate in _STRICT_SELECTION_GATES if gate not in _PROTOTYPE_HARD_GATES
)


class HamburgCorridorCandidateError(ValueError):
    """Raised when a corridor evidence package is malformed."""


def select_hamburg_corridor(
    *,
    screening_ledger_file: Path | str,
    expected_screening_ledger_sha256: str,
    output_file: Path | str,
) -> dict[str, Any]:
    """Select a strict five-node corridor or the fixed A1 diagnostic fallback."""

    ledger_path = _existing_file(Path(screening_ledger_file), "screening_ledger_file")
    expected_digest = _sha256_digest(expected_screening_ledger_sha256, "expected_screening_ledger_sha256")
    actual_digest = file_sha256(ledger_path)
    if actual_digest != expected_digest:
        raise HamburgCorridorCandidateError("screening ledger SHA-256 does not match")
    ledger = _read_object(ledger_path, "Hamburg five-corridor screening ledger")
    if ledger.get("schema") != HAMBURG_CORRIDOR_SCREENING_SCHEMA:
        raise HamburgCorridorCandidateError("screening ledger schema is invalid")
    if ledger.get("protocol") != _FIVE_CORRIDOR_PROTOCOL:
        raise HamburgCorridorCandidateError("screening ledger protocol is invalid")

    raw_sources = ledger.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise HamburgCorridorCandidateError("screening ledger sources must be a non-empty list")
    verified_sources: list[dict[str, Any]] = []
    seen_source_roles: set[str] = set()
    source_paths: set[Path] = set()
    for raw_source in raw_sources:
        if not isinstance(raw_source, Mapping):
            raise HamburgCorridorCandidateError("screening ledger source must be an object")
        role = str(raw_source.get("role", "")).strip()
        relative_path = Path(str(raw_source.get("path", "")))
        if not role or role in seen_source_roles:
            raise HamburgCorridorCandidateError("screening ledger source roles must be unique and non-empty")
        if not str(raw_source.get("url", "")).startswith("https://"):
            raise HamburgCorridorCandidateError(f"screening source {role!r} must use an HTTPS URL")
        if not isinstance(raw_source.get("query"), Mapping):
            raise HamburgCorridorCandidateError(f"screening source {role!r} query must be an object")
        if not str(raw_source.get("retrieved_at_utc", "")).strip() or not str(
            raw_source.get("license", "")
        ).strip():
            raise HamburgCorridorCandidateError(f"screening source {role!r} lacks retrieval time or license")
        if relative_path.is_absolute() or not relative_path.parts:
            raise HamburgCorridorCandidateError(f"screening source {role!r} path must be relative")
        source_path = (ledger_path.parent / relative_path).resolve()
        if not source_path.is_relative_to(ledger_path.parent) or not source_path.is_file():
            raise HamburgCorridorCandidateError(f"screening source {role!r} path is outside the ledger or missing")
        expected_source_digest = _sha256_digest(raw_source.get("sha256"), f"source {role} sha256")
        actual_source_digest = file_sha256(source_path)
        if actual_source_digest != expected_source_digest:
            raise HamburgCorridorCandidateError(f"screening source {role!r} SHA-256 does not match")
        seen_source_roles.add(role)
        source_paths.add(source_path)
        verified_sources.append(
            {
                "role": role,
                "path": str(relative_path).replace("\\", "/"),
                "sha256": actual_source_digest,
                "url": str(raw_source["url"]),
                "retrieved_at_utc": str(raw_source["retrieved_at_utc"]),
                "license": str(raw_source["license"]),
            }
        )

    raw_candidates = ledger.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise HamburgCorridorCandidateError("screening ledger candidates must be a non-empty list")
    candidates = [_normalize_selection_candidate(value) for value in raw_candidates]
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    node_sequences = [tuple(candidate["ordered_node_ids"]) for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)) or len(node_sequences) != len(set(node_sequences)):
        raise HamburgCorridorCandidateError("screening candidates must have unique ids and node sequences")

    strict_eligible = sorted(
        (
            candidate
            for candidate in candidates
            if all(candidate["gates"][gate] == "pass" for gate in _STRICT_SELECTION_GATES)
        ),
        key=_selection_rank_key,
    )
    fallback_candidates = [
        candidate for candidate in candidates if tuple(candidate["ordered_node_ids"]) == _REGISTERED_FALLBACK_NODES
    ]
    if len(fallback_candidates) != 1:
        raise HamburgCorridorCandidateError("screening ledger must contain the one registered A1 fallback corridor")
    fallback = fallback_candidates[0]
    fallback_gate_pass = all(fallback["gates"][gate] == "pass" for gate in _PROTOTYPE_HARD_GATES)
    fallback_window_pass = fallback.get("selected_detector_window") == _REGISTERED_FALLBACK_WINDOW

    if strict_eligible:
        selected = strict_eligible[0]
        status = "pass"
        claim_status = "candidate-selection-pass"
        selection_mode = "strict"
        strict_selection = {
            "status": "pass",
            "eligible_candidate_ids": [candidate["candidate_id"] for candidate in strict_eligible],
            "selected_candidate_id": selected["candidate_id"],
            "ordered_node_ids": selected["ordered_node_ids"],
        }
        fallback_selection = {"status": "not_used", "selected_candidate_id": None, "ordered_node_ids": []}
    elif fallback_gate_pass:
        status = "diagnostic-only"
        claim_status = "diagnostic-demo"
        selection_mode = "registered_fallback"
        strict_selection = {"status": "blocked", "eligible_candidate_ids": [], "selected_candidate_id": None}
        fallback_selection = {
            "status": "pass",
            "selected_candidate_id": fallback["candidate_id"],
            "ordered_node_ids": fallback["ordered_node_ids"],
            "construction_scope": "low-gate-prototype",
            "selected_detector_window": fallback.get("selected_detector_window"),
            "detector_window_registration": "pass" if fallback_window_pass else "not_available",
            "advisory_gates": {
                gate: fallback["gates"][gate] for gate in _PROTOTYPE_ADVISORY_GATES
            },
            "allowed_approximations": [
                gate for gate in _PROTOTYPE_ADVISORY_GATES if fallback["gates"][gate] != "pass"
            ],
        }
    else:
        status = "blocked"
        claim_status = "blocked"
        selection_mode = "none"
        strict_selection = {"status": "blocked", "eligible_candidate_ids": [], "selected_candidate_id": None}
        fallback_selection = {
            "status": "blocked",
            "selected_candidate_id": None,
            "ordered_node_ids": [],
            "failed_gates": [
                gate for gate in _PROTOTYPE_HARD_GATES if fallback["gates"][gate] != "pass"
            ],
            "detector_window_registration": "pass" if fallback_window_pass else "not_available",
        }

    report = {
        "schema": HAMBURG_CORRIDOR_SELECTION_SCHEMA,
        "status": status,
        "claim_status": claim_status,
        "selection_mode": selection_mode,
        "strict_selection": strict_selection,
        "fallback_selection": fallback_selection,
        "screened_candidates": [
            {
                "candidate_id": candidate["candidate_id"],
                "ordered_node_ids": candidate["ordered_node_ids"],
                "strict_status": (
                    "pass"
                    if all(candidate["gates"][gate] == "pass" for gate in _STRICT_SELECTION_GATES)
                    else "blocked"
                ),
                "failed_strict_gates": [
                    gate for gate in _STRICT_SELECTION_GATES if candidate["gates"][gate] != "pass"
                ],
                "ranking": candidate["ranking"],
            }
            for candidate in sorted(candidates, key=_selection_rank_key)
        ],
        "ranking_policy": [
            "exact_static_asset_intersection_count descending",
            "detector_covered_motor_vehicle_approach_count descending",
            "complete_common_observation_day_count descending",
            "ambiguity_count ascending",
            "max_adjacent_gap_m ascending",
            "ordered numeric LSA ids ascending",
        ],
        "input": {"path": str(ledger_path), "sha256": actual_digest},
        "verified_sources": verified_sources,
        "claim_boundary": (
            "The low-gate fallback is a public-data prototype. Missing static, detector, or signal-history "
            "evidence is an explicit approximation and cannot support a digital twin validation claim."
        ),
    }
    destination = Path(output_file).expanduser().resolve()
    if destination.exists() or destination in {ledger_path, *source_paths}:
        raise HamburgCorridorCandidateError("output_file must be new and separate from all evidence inputs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination, report, sort_keys=True)
    return {
        **report,
        "output_file": str(destination),
        "output_sha256": file_sha256(destination),
    }


def bind_hamburg_corridor_tls_clusters(
    *,
    selection_file: Path | str,
    expected_selection_sha256: str,
    lsa_identity_file: Path | str,
    expected_lsa_identity_sha256: str,
    tls_clusters_file: Path | str,
    expected_tls_clusters_sha256: str,
    net_file: Path | str,
    expected_net_sha256: str,
    output_file: Path | str,
    max_distance_m: float = 35.0,
) -> dict[str, Any]:
    """Bind selected official LSA points without requiring observation data."""

    if not math.isfinite(max_distance_m) or max_distance_m <= 0:
        raise HamburgCorridorCandidateError("max_distance_m must be finite and positive")
    inputs = {
        "selection": (
            _existing_file(Path(selection_file), "selection_file"),
            _sha256_digest(expected_selection_sha256, "expected_selection_sha256"),
        ),
        "lsa_identity": (
            _existing_file(Path(lsa_identity_file), "lsa_identity_file"),
            _sha256_digest(expected_lsa_identity_sha256, "expected_lsa_identity_sha256"),
        ),
        "tls_clusters": (
            _existing_file(Path(tls_clusters_file), "tls_clusters_file"),
            _sha256_digest(expected_tls_clusters_sha256, "expected_tls_clusters_sha256"),
        ),
        "network": (
            _existing_file(Path(net_file), "net_file"),
            _sha256_digest(expected_net_sha256, "expected_net_sha256"),
        ),
    }
    identities: dict[str, dict[str, str]] = {}
    for role, (path, expected_digest) in inputs.items():
        actual_digest = file_sha256(path)
        if actual_digest != expected_digest:
            raise HamburgCorridorCandidateError(f"{role} SHA-256 does not match")
        identities[role] = {"path": str(path), "sha256": actual_digest}

    selection = _read_object(inputs["selection"][0], "Hamburg corridor selection")
    if selection.get("schema") != HAMBURG_CORRIDOR_SELECTION_SCHEMA:
        raise HamburgCorridorCandidateError("selection schema is invalid")
    selection_key = "strict_selection" if selection.get("selection_mode") == "strict" else "fallback_selection"
    node_ids = (
        selection.get("ordered_node_ids")
        if selection.get("selection_mode") == "topology_only"
        else selection.get(selection_key, {}).get("ordered_node_ids")
    )
    if not isinstance(node_ids, list) or not node_ids:
        raise HamburgCorridorCandidateError("selection must contain distinct ordered node ids")
    node_ids = [str(node_id).strip() for node_id in node_ids]
    if any(not node_id.isascii() or not node_id.isdigit() for node_id in node_ids) or len(set(node_ids)) != len(node_ids):
        raise HamburgCorridorCandidateError("selection must contain distinct numeric node ids")

    lsa_payload = _read_object(inputs["lsa_identity"][0], "Hamburg LSA identity snapshot")
    features = lsa_payload.get("features")
    if not isinstance(features, list):
        raise HamburgCorridorCandidateError("LSA identity snapshot features must be a list")
    lsa_by_id: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, Mapping) or not isinstance(feature.get("properties"), Mapping):
            continue
        properties = feature["properties"]
        try:
            node_id = str(int(str(properties.get("knoten", "")).strip()))
        except ValueError:
            continue
        if node_id not in node_ids:
            continue
        geometry = feature.get("geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, Mapping) else None
        if isinstance(coordinates, list) and len(coordinates) == 1 and isinstance(coordinates[0], list):
            coordinates = coordinates[0]
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise HamburgCorridorCandidateError(f"LSA {node_id} must have one point coordinate")
        if str(properties.get("art", "")).strip() != "K-LSA" or node_id in lsa_by_id:
            raise HamburgCorridorCandidateError(f"LSA {node_id} identity is duplicate or not K-LSA")
        lon, lat = map(float, coordinates)
        if not all(math.isfinite(value) for value in (lat, lon)):
            raise HamburgCorridorCandidateError(f"LSA {node_id} coordinates must be finite")
        lsa_by_id[node_id] = {
            "node_id": node_id,
            "name": str(properties.get("LSA_Name", "")).strip(),
            "lat": lat,
            "lon": lon,
        }
    if set(lsa_by_id) != set(node_ids):
        raise HamburgCorridorCandidateError("LSA identity snapshot does not cover all selected nodes")

    with inputs["tls_clusters"][0].open(encoding="utf-8-sig", newline="") as handle:
        clusters = list(csv.DictReader(handle))
    if len(clusters) < len(node_ids) or len({row.get("cluster_id") for row in clusters}) != len(clusters):
        raise HamburgCorridorCandidateError("TLS audit must contain enough unique clusters for the selected nodes")
    network_root = ET.parse(inputs["network"][0]).getroot()
    member_points = _controlled_junction_points(network_root)
    network_tls_ids = {
        element.attrib["id"]
        for element in network_root.findall("tlLogic")
        if element.attrib.get("id")
    }
    normalized_clusters: list[dict[str, Any]] = []
    for row in clusters:
        cluster_id = str(row.get("cluster_id", "")).strip()
        tls_ids = [value.strip() for value in str(row.get("tls_ids", "")).split(";") if value.strip()]
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HamburgCorridorCandidateError(f"TLS cluster {cluster_id!r} has invalid coordinates") from exc
        if not cluster_id or not math.isfinite(lat) or not math.isfinite(lon) or not tls_ids:
            raise HamburgCorridorCandidateError("TLS cluster identity, coordinates, and controllers are required")
        if not set(tls_ids).issubset(network_tls_ids):
            raise HamburgCorridorCandidateError(f"TLS cluster {cluster_id!r} references an unknown controller")
        normalized_clusters.append(
            {"cluster_id": cluster_id, "lat": lat, "lon": lon, "tls_ids": tls_ids,
             "member_points": [point for tls_id in tls_ids for point in member_points.get(tls_id, [])]}
        )

    bindings: list[dict[str, Any]] = []
    for node_id in node_ids:
        lsa = lsa_by_id[node_id]
        def cluster_distance(value):
            points = value["member_points"] or [{"lon": value["lon"], "lat": value["lat"]}]
            return min(_haversine_m((lsa["lon"], lsa["lat"]), (point["lon"], point["lat"])) for point in points)
        cluster = min(
            normalized_clusters,
            key=cluster_distance,
        )
        distance_m = cluster_distance(cluster)
        bindings.append(
            {
                "node_id": node_id,
                "official_name": lsa["name"],
                "cluster_id": cluster["cluster_id"],
                "distance_m": round(distance_m, 3),
                "distance_basis": "nearest_controlled_junction" if cluster["member_points"] else "cluster_centroid",
                "centroid_distance_m": round(_haversine_m((lsa["lon"], lsa["lat"]), (cluster["lon"], cluster["lat"])), 3),
                "controlled_junction_points": cluster["member_points"],
                "tls_ids": cluster["tls_ids"],
            }
        )
    unique_binding = len({row["cluster_id"] for row in bindings}) == len(node_ids)
    distance_gate = all(row["distance_m"] <= max_distance_m for row in bindings)
    status = "pass" if unique_binding and distance_gate else "blocked"
    report = {
        "schema": "torii.hamburg-five-corridor-tls-cluster-binding/v1",
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "blocked",
        "selection_mode": selection.get("selection_mode"),
        "ordered_node_ids": node_ids,
        "max_distance_m": max_distance_m,
        "gates": {
            "selected_cluster_coverage": "pass",
            "one_to_one_nearest_binding": "pass" if unique_binding else "blocked",
            "physical_area_distance": "pass" if distance_gate else "blocked",
            "network_controller_identity": "pass",
        },
        "bindings": bindings,
        "inputs": identities,
        "claim_boundary": (
            "The nearest controlled junction in each TLS cluster identifies the selected physical signal area; "
            "a centroid fallback is recorded when member coordinates are unavailable. "
            "It does not prove exact lane, movement, controller, or historical signal equivalence."
        ),
    }
    destination = Path(output_file).expanduser().resolve()
    if destination.exists() or destination in {path for path, _digest in inputs.values()}:
        raise HamburgCorridorCandidateError("output_file must be new and separate from all inputs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(destination, report, sort_keys=True)
    return {**report, "output_file": str(destination), "output_sha256": file_sha256(destination)}


def _controlled_junction_points(root: ET.Element) -> dict[str, list[dict[str, Any]]]:
    """A compound controller's centroid is not a physical junction position."""
    location = root.find("location")
    if location is None or location.get("projParameter", "!") in {"!", "-", "."}:
        return {}
    converter = Transformer.from_crs(location.get("projParameter"), "EPSG:4326", always_xy=True)
    offset_x, offset_y = map(float, location.get("netOffset", "0,0").split(","))
    positions = {}
    for node in root.findall("junction"):
        if node.get("id", "").startswith(":") or node.get("x") is None or node.get("y") is None:
            continue
        lon, lat = converter.transform(float(node.get("x")) - offset_x, float(node.get("y")) - offset_y, errcheck=True)
        positions[node.get("id")] = {"junction_id": node.get("id"), "lon": lon, "lat": lat}
    destinations = {edge.get("id"): edge.get("to") for edge in root.findall("edge") if edge.get("to")}
    owners: dict[str, set[str]] = {}
    for connection in root.findall("connection"):
        node_id = destinations.get(connection.get("from"))
        if connection.get("tl") and node_id in positions:
            owners.setdefault(connection.get("tl"), set()).add(node_id)
    for logic in root.findall("tlLogic"):
        if logic.get("id") in positions:
            owners.setdefault(logic.get("id"), set()).add(logic.get("id"))
    return {tls_id: [positions[node_id] for node_id in sorted(nodes)] for tls_id, nodes in owners.items()}


def _sha256_digest(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise HamburgCorridorCandidateError(f"{label} must be a SHA-256 digest")
    digest = value.strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise HamburgCorridorCandidateError(f"{label} must be a SHA-256 digest")
    return digest


def _normalize_selection_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise HamburgCorridorCandidateError("screening candidate must be an object")
    candidate_id = str(value.get("candidate_id", "")).strip()
    raw_node_ids = value.get("ordered_node_ids")
    if not candidate_id:
        raise HamburgCorridorCandidateError("screening candidate_id must be non-empty")
    if not isinstance(raw_node_ids, list) or len(raw_node_ids) != 5:
        raise HamburgCorridorCandidateError(f"candidate {candidate_id!r} must contain exactly five node ids")
    node_ids = [str(node_id).strip() for node_id in raw_node_ids]
    if any(not node_id.isascii() or not node_id.isdigit() for node_id in node_ids) or len(set(node_ids)) != 5:
        raise HamburgCorridorCandidateError(
            f"candidate {candidate_id!r} node ids must be five distinct decimal strings"
        )

    raw_gates = value.get("gates")
    if not isinstance(raw_gates, Mapping):
        raise HamburgCorridorCandidateError(f"candidate {candidate_id!r} gates must be an object")
    allowed_gate_statuses = {"pass", "blocked", "fail", "review_required", "not_run"}
    gates: dict[str, str] = {}
    for gate in _STRICT_SELECTION_GATES:
        status = raw_gates.get(gate)
        if status not in allowed_gate_statuses:
            raise HamburgCorridorCandidateError(
                f"candidate {candidate_id!r} gate {gate!r} has an invalid status"
            )
        gates[gate] = status

    raw_ranking = value.get("ranking")
    if not isinstance(raw_ranking, Mapping):
        raise HamburgCorridorCandidateError(f"candidate {candidate_id!r} ranking must be an object")
    ranking: dict[str, int | float] = {}
    for field in (
        "exact_static_asset_intersection_count",
        "detector_covered_motor_vehicle_approach_count",
        "complete_common_observation_day_count",
        "ambiguity_count",
    ):
        number = raw_ranking.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise HamburgCorridorCandidateError(
                f"candidate {candidate_id!r} ranking field {field!r} must be a non-negative integer"
            )
        ranking[field] = number
    if ranking["exact_static_asset_intersection_count"] > 5:
        raise HamburgCorridorCandidateError(
            f"candidate {candidate_id!r} static asset count cannot exceed five"
        )
    raw_max_gap = raw_ranking.get("max_adjacent_gap_m")
    if isinstance(raw_max_gap, bool) or not isinstance(raw_max_gap, (int, float)):
        raise HamburgCorridorCandidateError(
            f"candidate {candidate_id!r} max_adjacent_gap_m must be a positive finite number"
        )
    max_gap = float(raw_max_gap)
    if not math.isfinite(max_gap) or max_gap <= 0:
        raise HamburgCorridorCandidateError(
            f"candidate {candidate_id!r} max_adjacent_gap_m must be a positive finite number"
        )
    ranking["max_adjacent_gap_m"] = max_gap

    normalized: dict[str, Any] = {
        "candidate_id": candidate_id,
        "ordered_node_ids": node_ids,
        "gates": gates,
        "ranking": ranking,
    }
    raw_window = value.get("selected_detector_window")
    if raw_window is not None:
        if not isinstance(raw_window, Mapping) or set(raw_window) != set(_REGISTERED_FALLBACK_WINDOW):
            raise HamburgCorridorCandidateError(
                f"candidate {candidate_id!r} selected_detector_window has invalid fields"
            )
        window = {field: str(raw_window[field]).strip() for field in _REGISTERED_FALLBACK_WINDOW}
        if any(not timestamp for timestamp in window.values()):
            raise HamburgCorridorCandidateError(
                f"candidate {candidate_id!r} selected_detector_window timestamps must be non-empty"
            )
        normalized["selected_detector_window"] = window
    return normalized


def _selection_rank_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    ranking = candidate["ranking"]
    return (
        -ranking["exact_static_asset_intersection_count"],
        -ranking["detector_covered_motor_vehicle_approach_count"],
        -ranking["complete_common_observation_day_count"],
        ranking["ambiguity_count"],
        ranking["max_adjacent_gap_m"],
        tuple(int(node_id) for node_id in candidate["ordered_node_ids"]),
    )


def build_hamburg_corridor_candidate_evidence(
    *,
    candidate_id: str,
    ordered_node_ids: Sequence[str],
    lsa_identity_manifest: Path,
    static_signal_manifest: Path,
    count_manifest: Path,
    plainxml_manifests: Mapping[str, Path],
    official_road_snapshot: Path | None = None,
    axis_paths: Sequence[Mapping[str, Any]] = (),
    signal_fetch_manifest: Path | None = None,
    map_lane_axis_stitch_plan: Path | None = None,
    official_splice_plan: Path | None = None,
    required_signal_types: Sequence[str] = (),
    output_file: Path | None = None,
) -> dict[str, Any]:
    """Build and optionally write a corridor screening manifest.

    ``axis_paths`` contains one directed path per consecutive pair of LSA
    nodes.  Each path entry has ``start_network_node``, ``end_network_node``
    and ``links``.  A link is ``{"feature_id": "...", "direction":
    "forward"|"reverse"}``.  The function validates only exact source
    records and graph continuity; it intentionally does not infer the local
    MAP-cell anchor or lane-to-lane transitions.
    """

    candidate = str(candidate_id).strip()
    if not candidate:
        raise HamburgCorridorCandidateError("candidate_id must not be empty")
    node_ids = tuple(str(value).strip() for value in ordered_node_ids)
    if len(node_ids) < 2 or any(not value for value in node_ids):
        raise HamburgCorridorCandidateError("at least two non-empty ordered_node_ids are required")
    if len(set(node_ids)) != len(node_ids):
        raise HamburgCorridorCandidateError("ordered_node_ids must be unique")

    lsa_path = _existing_file(lsa_identity_manifest, "lsa_identity_manifest")
    static_path = _existing_file(static_signal_manifest, "static_signal_manifest")
    count_path = _existing_file(count_manifest, "count_manifest")
    lsa = _read_object(lsa_path, "LSA identity manifest")
    static = _read_object(static_path, "static signal manifest")
    counts = _read_object(count_path, "count manifest")

    selections = lsa.get("selections")
    if not isinstance(selections, list):
        raise HamburgCorridorCandidateError("LSA identity manifest has no selections list")
    selected_by_id = {
        str(item.get("expected_node_id", "")): item
        for item in selections
        if isinstance(item, Mapping)
    }
    missing_lsa = [node_id for node_id in node_ids if node_id not in selected_by_id]
    lsa_gate = "pass" if str(lsa.get("decision", "")) == "pass" and not missing_lsa else "blocked"
    required_types = tuple(sorted({str(value).strip() for value in required_signal_types if str(value).strip()}))
    node_signal_types = {
        node_id: str(
            (
                selected_by_id[node_id].get("selected_node")
                if isinstance(selected_by_id[node_id].get("selected_node"), Mapping)
                else {}
            ).get("signal_type", "")
        ).strip()
        for node_id in node_ids
        if node_id in selected_by_id
    }
    mismatched_signal_types = {
        node_id: signal_type
        for node_id, signal_type in node_signal_types.items()
        if required_types and signal_type not in required_types
    }
    signal_type_gate = (
        "pass"
        if not missing_lsa and not mismatched_signal_types
        else "blocked"
    )

    static_nodes = {str(value) for value in static.get("node_ids", [])}
    static_gate = (
        "pass"
        if str(static.get("status", "")) == "pass"
        and str(static.get("execution_gate", "")) == "pass"
        and set(node_ids) == static_nodes
        and all(str(node_id) in static.get("nodes", {}) for node_id in node_ids)
        else "blocked"
    )

    requested_count_nodes = {
        str(value) for value in counts.get("parameters", {}).get("requested_count_node_ids", [])
    }
    count_gates = counts.get("gates") if isinstance(counts.get("gates"), Mapping) else {}
    count_gate = (
        "pass"
        if str(counts.get("status", "")) == "pass"
        and str(counts.get("execution_gate", "")) == "pass"
        and requested_count_nodes == set(node_ids)
        and all(str(count_gates.get(name, "")) == "pass" for name in (
            "full_named_node_coverage",
            "official_observation_window",
        ))
        else "blocked"
    )

    plainxml_status: dict[str, str] = {}
    for node_id in node_ids:
        path = plainxml_manifests.get(node_id)
        if path is None:
            plainxml_status[node_id] = "blocked"
            continue
        payload = _read_object(_existing_file(path, f"PlainXML manifest {node_id}"), f"PlainXML manifest {node_id}")
        plainxml_status[node_id] = (
            "pass"
            if str(payload.get("status", "")) == "pass"
            and str(payload.get("compiled_network_audit", {}).get("status", "")) == "pass"
            else "blocked"
        )
    plainxml_gate = "pass" if all(value == "pass" for value in plainxml_status.values()) else "blocked"

    axis_report: dict[str, Any]
    if official_road_snapshot is None:
        axis_report = {
            "status": "blocked",
            "reason": "official_road_snapshot_not_supplied",
            "segments": [],
        }
    else:
        road_path = _existing_file(official_road_snapshot, "official_road_snapshot")
        axis_report = _validate_axis_paths(road_path, node_ids, axis_paths)

    signal_fetch_report: dict[str, Any] | None = None
    signal_history_gate = "not_run"
    if signal_fetch_manifest is not None:
        signal_fetch_path = _existing_file(signal_fetch_manifest, "signal_fetch_manifest")
        signal_fetch_report = _read_object(signal_fetch_path, "signal fetch manifest")
        signal_history_gate = (
            "pass"
            if str(signal_fetch_report.get("status", "")) == "pass"
            and str(signal_fetch_report.get("execution_gate", "")) == "pass"
            else "blocked"
        )

    lane_stitch_report: dict[str, Any] | None = None
    lane_stitch_gate = "not_run"
    lane_geometry_gate = "not_run"
    lane_geometry_report: dict[str, Any] | None = None
    if map_lane_axis_stitch_plan is not None:
        lane_stitch_path = _existing_file(map_lane_axis_stitch_plan, "MAP-to-HH-SIB lane-axis stitch plan")
        lane_stitch_report = _read_object(lane_stitch_path, "MAP-to-HH-SIB lane-axis stitch plan")
        if lane_stitch_report.get("schema") != "torii.hamburg-official-map-hh-sib-lane-axis-stitch-plan/v1":
            raise HamburgCorridorCandidateError("MAP-to-HH-SIB lane-axis stitch plan schema is invalid")
        lane_stitch_gate = (
            "pass"
            if str(lane_stitch_report.get("status")) == "pass"
            else "review_required"
            if str(lane_stitch_report.get("status")) == "review_required"
            else "blocked"
        )
        raw_conflicts = lane_stitch_report.get("approach_geometry_conflicts")
        if isinstance(raw_conflicts, list):
            conflicts = [
                dict(item)
                for item in raw_conflicts
                if isinstance(item, Mapping)
            ]
            lane_geometry_gate = (
                "pass"
                if all(str(item.get("status", "")) == "pass" for item in conflicts)
                else "review_required"
            )
            lane_geometry_report = {
                "status": lane_geometry_gate,
                "approach_count": len(conflicts),
                "conflict_count": sum(
                    str(item.get("status", "")) != "pass" for item in conflicts
                ),
                "approaches": conflicts,
                "decision": (
                    "materialize_boundary_join"
                    if lane_geometry_gate == "pass"
                    else "split_official_map_merge_points_before_boundary_join"
                ),
            }

    splice_report: dict[str, Any] | None = None
    splice_gate = "not_run"
    if official_splice_plan is not None:
        splice_path = _existing_file(official_splice_plan, "official MAP-to-HH-SIB splice plan")
        splice_report = _read_object(splice_path, "official MAP-to-HH-SIB splice plan")
        if splice_report.get("schema") != "torii.hamburg-official-map-hh-sib-splice-plan/v1":
            raise HamburgCorridorCandidateError("official MAP-to-HH-SIB splice plan schema is invalid")
        splice_gate = (
            "pass"
            if str(splice_report.get("status")) == "pass"
            else "review_required"
            if str(splice_report.get("status")) == "review_required"
            else "blocked"
        )

    ordered_nodes = [_node_record(selected_by_id[node_id], node_id) for node_id in node_ids if node_id in selected_by_id]
    distances = [
        {
            "from_node_id": node_ids[index],
            "to_node_id": node_ids[index + 1],
            "distance_m": round(_haversine_m(
                ordered_nodes[index]["coordinates"],
                ordered_nodes[index + 1]["coordinates"],
            ), 3),
        }
        for index in range(len(ordered_nodes) - 1)
    ]

    gates = {
        "official_lsa_identity": lsa_gate,
        "corridor_node_signal_type": signal_type_gate,
        "official_static_map_kml_ocit": static_gate,
        "per_node_official_plainxml": plainxml_gate,
        "official_motor_vehicle_counts": count_gate,
        "official_axis_link_chain": str(axis_report.get("status", "blocked")),
        "official_axis_anchor_binding": "review_required" if axis_report.get("status") == "pass" else "blocked",
        "official_map_hh_sib_lane_axis_stitch": lane_stitch_gate,
        "official_map_boundary_geometry": lane_geometry_gate,
        "official_map_hh_sib_splice_plan": splice_gate,
        "historical_signal_observations": signal_history_gate,
        "combined_corridor_network": "not_run",
        "same_location_virtual_detectors": "not_run",
        "route_generation_and_completion": "not_run",
        "automatic_promotion": "blocked",
    }
    preflight_pass = all(
        gates[name] == "pass"
        for name in (
            "official_lsa_identity",
            "corridor_node_signal_type",
            "official_static_map_kml_ocit",
            "per_node_official_plainxml",
            "official_motor_vehicle_counts",
            "official_axis_link_chain",
        )
    )
    status = "review_required" if preflight_pass else "blocked"
    input_paths: dict[str, dict[str, Any]] = {
        "lsa_identity_manifest": _artifact_identity(lsa_path),
        "static_signal_manifest": _artifact_identity(static_path),
        "count_manifest": _artifact_identity(count_path),
    }
    if official_road_snapshot is not None:
        input_paths["official_road_snapshot"] = _artifact_identity(_existing_file(official_road_snapshot, "official_road_snapshot"))
    if signal_fetch_manifest is not None:
        input_paths["signal_fetch_manifest"] = _artifact_identity(_existing_file(signal_fetch_manifest, "signal_fetch_manifest"))
    if map_lane_axis_stitch_plan is not None:
        input_paths["map_lane_axis_stitch_plan"] = _artifact_identity(
            _existing_file(map_lane_axis_stitch_plan, "MAP-to-HH-SIB lane-axis stitch plan")
        )
    if official_splice_plan is not None:
        input_paths["official_splice_plan"] = _artifact_identity(
            _existing_file(official_splice_plan, "official MAP-to-HH-SIB splice plan")
        )
    input_paths["plainxml_manifests"] = {
        node_id: _artifact_identity(_existing_file(plainxml_manifests[node_id], f"PlainXML manifest {node_id}"))
        for node_id in node_ids
        if node_id in plainxml_manifests
    }
    manifest: dict[str, Any] = {
        "schema": HAMBURG_CORRIDOR_CANDIDATE_SCHEMA,
        "candidate_id": candidate,
        "status": status,
        "claim_status": "official_static_corridor_candidate" if status == "review_required" else "blocked",
        "automatic_promotion_gate": "blocked",
        "ordered_node_ids": list(node_ids),
        "nodes": ordered_nodes,
        "node_type_policy": {
            "required_signal_types": list(required_types),
            "observed_signal_types_by_node": node_signal_types,
            "mismatched_signal_types_by_node": mismatched_signal_types,
        },
        "distances": distances,
        "inputs": input_paths,
        "axis_connector": axis_report,
        "signal_history_fetch": signal_fetch_report,
        "map_lane_axis_stitch": lane_stitch_report,
        "map_lane_axis_conflicts": lane_geometry_report,
        "official_map_hh_sib_splice_plan": splice_report,
        "plainxml_status_by_node": plainxml_status,
        "gates": gates,
        "claim_boundary": {
            "proves": [
                "official LSA identity and ordered node coordinates",
                "current official MAP/KML/OCIT assets and per-node static PlainXML compilation when the gates pass",
                "complete official motor-vehicle detector inventory and the selected Saturday count window when the count gate passes",
                "directed continuity of the selected HH-SIB feature path",
            ],
            "does_not_prove": [
                "local MAP cell to HH-SIB axis anchor identity",
                "corridor lane-transition legality or SUMO junction connections",
                "historical Saturday signal states when the TLD fetch gate is blocked",
                "a combined SUMO network, route files, or same-location virtual sensors",
            ],
        },
        "autonomous_action": (
            "continue_to_official_axis_anchor_and_lane_transition_stage"
            if status == "review_required"
            else "abstain_until_required_official_evidence_is_complete"
        ),
    }
    if output_file is not None:
        destination = Path(output_file).expanduser().resolve()
        if any(Path(identity["path"]).resolve() == destination for identity in input_paths.values() if isinstance(identity, Mapping) and "path" in identity):
            raise HamburgCorridorCandidateError("output_file must be separate from input artifacts")
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(destination, manifest, sort_keys=True)
        manifest["manifest_file"] = str(destination)
        manifest["manifest_sha256"] = file_sha256(destination)
    return manifest


def _validate_axis_paths(
    snapshot_path: Path,
    node_ids: Sequence[str],
    axis_paths: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"status": "blocked", "reason": f"invalid_official_road_snapshot:{type(exc).__name__}", "segments": []}
    features = payload.get("features") if isinstance(payload, Mapping) else None
    if not isinstance(features, list):
        return {"status": "blocked", "reason": "official_road_feature_collection_required", "segments": []}
    by_id = {str(feature.get("id")): feature for feature in features if isinstance(feature, Mapping) and feature.get("id") is not None}
    expected_segments = len(node_ids) - 1
    if len(axis_paths) != expected_segments:
        return {
            "status": "blocked",
            "reason": f"axis_path_segment_count_mismatch:{len(axis_paths)}:{expected_segments}",
            "segments": [],
        }
    segments: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw_segment in enumerate(axis_paths):
        segment = raw_segment if isinstance(raw_segment, Mapping) else {}
        from_node = str(segment.get("from_node_id", ""))
        to_node = str(segment.get("to_node_id", ""))
        links = segment.get("links") if isinstance(segment.get("links"), list) else []
        expected_from = node_ids[index]
        expected_to = node_ids[index + 1]
        if (from_node, to_node) != (expected_from, expected_to):
            errors.append(f"segment_endpoint_mismatch:{from_node}:{to_node}")
        current = str(segment.get("start_network_node", ""))
        link_records: list[dict[str, Any]] = []
        for raw_link in links:
            link = raw_link if isinstance(raw_link, Mapping) else {}
            feature_id = str(link.get("feature_id", ""))
            direction = str(link.get("direction", "forward")).lower()
            feature = by_id.get(feature_id)
            if feature is None:
                errors.append(f"axis_feature_missing:{feature_id}")
                continue
            properties = feature.get("properties") if isinstance(feature.get("properties"), Mapping) else {}
            required = ("von_netzknoten", "nach_netzknoten", "strassenname", "abschnittslaenge")
            missing = [name for name in required if name not in properties]
            if missing:
                errors.append(f"axis_feature_missing_property:{feature_id}:{','.join(missing)}")
                continue
            raw_from = str(properties["von_netzknoten"])
            raw_to = str(properties["nach_netzknoten"])
            if direction not in {"forward", "reverse"}:
                errors.append(f"axis_direction_invalid:{feature_id}:{direction}")
                continue
            oriented_from, oriented_to = (raw_from, raw_to) if direction == "forward" else (raw_to, raw_from)
            if current and oriented_from != current:
                errors.append(f"axis_chain_break:{feature_id}:{current}:{oriented_from}")
            current = oriented_to
            link_records.append({
                "feature_id": feature_id,
                "direction": direction,
                "road_name": str(properties.get("strassenname", "")),
                "from_network_node": raw_from,
                "to_network_node": raw_to,
                "length_m": properties.get("abschnittslaenge"),
            })
        expected_end = str(segment.get("end_network_node", ""))
        if expected_end and current != expected_end:
            errors.append(f"axis_end_mismatch:{from_node}:{current}:{expected_end}")
        segments.append({
            "from_node_id": from_node,
            "to_node_id": to_node,
            "start_network_node": str(segment.get("start_network_node", "")),
            "end_network_node": expected_end,
            "links": link_records,
            "status": "pass" if not errors else "review_required",
        })
    return {
        "status": "pass" if not errors and all(item["links"] for item in segments) else "blocked",
        "reason": "exact_feature_chain_validated" if not errors else "axis_path_validation_failed",
        "errors": errors,
        "snapshot": _artifact_identity(snapshot_path),
        "segments": segments,
    }


def _node_record(selection: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    selected = selection.get("selected_node") if isinstance(selection.get("selected_node"), Mapping) else {}
    point = selected.get("point_geometry") if isinstance(selected.get("point_geometry"), Mapping) else {}
    coordinates = point.get("coordinates") if isinstance(point.get("coordinates"), list) else []
    if len(coordinates) != 2:
        coordinates = [None, None]
    return {
        "node_id": node_id,
        "official_name": str(selected.get("official_name", "")),
        "signal_type": str(selected.get("signal_type", "")),
        "coordinates": [coordinates[0], coordinates[1]],
    }


def _haversine_m(first: Sequence[Any], second: Sequence[Any]) -> float:
    if len(first) != 2 or len(second) != 2 or any(value is None for value in (*first, *second)):
        return float("nan")
    lon1, lat1, lon2, lat2 = map(float, (first[0], first[1], second[0], second[1]))
    radius = 6371008.8
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _existing_file(value: Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise HamburgCorridorCandidateError(f"{label} must be an existing file: {path}")
    return path


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HamburgCorridorCandidateError(f"invalid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise HamburgCorridorCandidateError(f"{label} must contain a JSON object: {path}")
    return payload


def _artifact_identity(path: Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    return {"path": str(resolved), "sha256": file_sha256(resolved), "bytes": resolved.stat().st_size}


__all__ = [
    "HAMBURG_CORRIDOR_CANDIDATE_SCHEMA",
    "HAMBURG_CORRIDOR_SCREENING_SCHEMA",
    "HAMBURG_CORRIDOR_SELECTION_SCHEMA",
    "HamburgCorridorCandidateError",
    "bind_hamburg_corridor_tls_clusters",
    "build_hamburg_corridor_candidate_evidence",
    "select_hamburg_corridor",
]
