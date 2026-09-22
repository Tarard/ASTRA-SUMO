"""Build a geometry-only SUMO PlainXML candidate from official HH-SIB data.

This module deliberately has no OSM dependency.  It turns exact, explicitly
scoped HH-SIB road links into directed SUMO edge candidates while retaining
the official source hash, network-node identifiers, interval geometry, lane
direction counts, and unconditional speed rule.  It does not infer junction
connections, lane-to-lane movements, priority, stop lines, or traffic-light
logic.  Those are a later official MAP/OCIT stage and remain a hard automatic
promotion blocker in the emitted manifest.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import warnings
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_named_scope import (
    HamburgNamedScopeError,
    validate_hamburg_named_scope_manifest,
)

from .adapters.hamburg_hh_sib import (
    HH_SIB_ADAPTER_SCHEMA,
    read_hamburg_hh_sib_snapshot,
)


OFFICIAL_PLAINXML_SCHEMA = "torii.hamburg-hh-sib-official-plainxml-candidate/v1"
OFFICIAL_CORRIDOR_SCOPE_SCHEMA = "torii.hamburg-hh-sib-corridor-scope/v1"
GENERATOR_POLICY = "torii.hh-sib-directed-interval-plainxml/v1"

_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_UNCONDITIONAL_SPEED_PATTERN = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*km\s*/?\s*h\s*$", re.IGNORECASE)
_GEOMETRY_ORDERS = frozenset(
    {
        "auto",
        "source_order_is_with_stationing",
        "source_order_is_against_stationing",
    }
)
_MOTOR_VCLASSES = "passenger taxi bus coach delivery truck motorcycle emergency"


class OfficialPlainXmlError(ValueError):
    """Raised before artifact writing when official evidence is insufficient."""


@dataclass(frozen=True)
class FrozenHamburgHhSibSnapshot:
    """Complete provenance needed to parse one frozen HH-SIB OGC response."""

    path: Path
    request_url: str
    bbox: tuple[float, float, float, float]
    target_time: datetime
    retrieved_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    expected_sha256: str | None = None

    def read(self) -> dict[str, Any]:
        return read_hamburg_hh_sib_snapshot(
            self.path,
            request_url=self.request_url,
            bbox=self.bbox,
            target_time=self.target_time,
            retrieved_at=self.retrieved_at,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            expected_sha256=self.expected_sha256,
        )


def materialize_hamburg_hh_sib_plainxml_candidate(
    *,
    hh_sib_source: Mapping[str, Any] | FrozenHamburgHhSibSnapshot,
    corridor_scope: Mapping[str, Any],
    output_dir: Path,
    prefix: str = "hh_sib_official_corridor",
    geometry_join_tolerance_m: float = 2.0,
    orientation_margin_m: float = 1.0,
    named_scope_manifest_file: Path | None = None,
) -> dict[str, Any]:
    """Write a separate geometry-only candidate from official HH-SIB evidence.

    ``corridor_scope`` must enumerate exact road-link selectors.  A selector
    may name the adapter's ``source_ref.object_id`` directly, or provide the
    exact ``road_key``, ``from_network_node``, ``to_network_node``, and
    ``branch_code`` tuple.  Optional station bounds must coincide with official
    feature boundaries; this stage never interpolates a partial official
    interval.

    The generated ``.netccfg`` is reproducible build intent, not permission to
    run netconvert for promotion.  Without an official connection file,
    netconvert would guess lane connections.  Therefore netconvert execution
    and automatic promotion remain blocked in every manifest produced here.
    """

    if not _PREFIX_PATTERN.fullmatch(prefix):
        raise OfficialPlainXmlError("prefix must be a safe 1-96 character artifact stem")
    if not math.isfinite(geometry_join_tolerance_m) or geometry_join_tolerance_m <= 0:
        raise OfficialPlainXmlError("geometry_join_tolerance_m must be finite and positive")
    if not math.isfinite(orientation_margin_m) or orientation_margin_m < 0:
        raise OfficialPlainXmlError("orientation_margin_m must be finite and non-negative")

    named_scope_manifest: dict[str, Any] | None = None
    if named_scope_manifest_file is not None:
        try:
            named_scope_manifest = validate_hamburg_named_scope_manifest(
                named_scope_manifest_file,
                require_signal_assets=False,
            )
        except HamburgNamedScopeError as exc:
            raise OfficialPlainXmlError(f"named corridor scope contract is invalid: {exc}") from exc

    report = _load_hh_sib_report(hh_sib_source)
    normalized_scope = _validate_scope(corridor_scope)
    source = _validate_source_report(report)
    selected = _select_links(report, normalized_scope)
    plan = _build_plainxml_plan(
        report=report,
        selected=selected,
        geometry_join_tolerance_m=geometry_join_tolerance_m,
        orientation_margin_m=orientation_margin_m,
    )

    destination = Path(output_dir).expanduser().resolve()
    _validate_separate_destination(destination, source.get("path"))
    paths = {
        "nodes": destination / f"{prefix}.nod.xml",
        "edges": destination / f"{prefix}.edg.xml",
        "types": destination / f"{prefix}.typ.xml",
        "netconvert_config": destination / f"{prefix}.netccfg",
        "candidate_net": destination / f"{prefix}.net.xml",
        "manifest": destination / f"{prefix}.manifest.json",
    }
    collisions = [str(path) for key, path in paths.items() if key != "candidate_net" and path.exists()]
    if paths["candidate_net"].exists():
        collisions.append(str(paths["candidate_net"]))
    if collisions:
        raise OfficialPlainXmlError("candidate output artifacts already exist: " + ", ".join(sorted(collisions)))

    destination.mkdir(parents=True, exist_ok=True)
    _write_nodes(paths["nodes"], plan)
    _write_edges(paths["edges"], plan, source_sha256=str(source["sha256"]))
    _write_types(paths["types"])
    _write_netconvert_config(paths, prefix=prefix, projection=plan["projection"])

    source_report_sha256 = _portable_report_sha256(report)
    scope_sha256 = _stable_digest(normalized_scope)
    candidate_id = "hh-sib-plainxml-" + _stable_digest(
        {
            "generator_policy": GENERATOR_POLICY,
            "source_sha256": source["sha256"],
            "source_report_sha256": source_report_sha256,
            "scope_sha256": scope_sha256,
            "plainxml_sha256": {
                key: file_sha256(paths[key])
                for key in ("nodes", "edges", "types", "netconvert_config")
            },
        }
    )[:24]
    artifacts = {
        key: {
            "path": path.name,
            "sha256": file_sha256(path),
            "bytes": path.stat().st_size,
        }
        for key, path in paths.items()
        if key in {"nodes", "edges", "types", "netconvert_config"}
    }

    source_claim_status = str(report.get("claim_status", report.get("status", "blocked")))
    source_time_status = str(
        report.get("source_snapshot", {}).get("time_alignment_status", "blocked")
    )
    manifest: dict[str, Any] = {
        "schema": OFFICIAL_PLAINXML_SCHEMA,
        "candidate_id": candidate_id,
        "status": "blocked",
        "claim_status": "official_geometry_plainxml_candidate_only",
        "automatic_promotion_gate": "blocked",
        "human_map_review_required": False,
        "source": {
            "kind": "hamburg_hh_sib_official_snapshot",
            "adapter_schema": HH_SIB_ADAPTER_SCHEMA,
            "path": source.get("path"),
            "sha256": source["sha256"],
            "portable_parsed_report_sha256": source_report_sha256,
            "report_claim_status": source_claim_status,
            "time_alignment_status": source_time_status,
            "time_alignment_resolution": (
                "machine-readable official validity metadata is required when status is not pass; "
                "manual map review is not the resolution mechanism"
            ),
            "attribution": report.get("source_attribution"),
            "license": report.get("license"),
        },
        "scope": {
            **normalized_scope,
            "sha256": scope_sha256,
        },
        "named_scope_contract": (
            {
                "path": str(Path(named_scope_manifest_file).resolve()),
                "sha256": file_sha256(Path(named_scope_manifest_file).resolve()),
                "scope_id": named_scope_manifest["scope_id"],
                "decision": named_scope_manifest["decision"],
            }
            if named_scope_manifest_file is not None and named_scope_manifest is not None
            else None
        ),
        "projection": plan["projection"],
        "counts": {
            "selected_link_count": len(selected),
            "official_interval_count": len(plan["intervals"]),
            "node_count": len(plan["nodes"]),
            "directed_edge_count": len(plan["edges"]),
        },
        "selected_links": plan["link_evidence"],
        "gates": {
            "official_source_hash": "pass",
            "official_source_time_alignment": source_time_status,
            "explicit_corridor_scope": "pass",
            "named_scope_contract": "pass" if named_scope_manifest is not None else "not_provided",
            "official_link_identity": "pass",
            "official_interval_geometry": "pass",
            "official_station_orientation": "pass",
            "official_directional_lane_counts": "pass",
            "official_unconditional_speed": "pass",
            "plainxml_artifact_hashes": "pass",
            "official_map_lane_connections": "blocked",
            "lane_to_lane_connection_materialization": "not_run",
            "official_signal_control_binding": "blocked",
            "netconvert_execution": "not_run",
            "automatic_promotion": "blocked",
        },
        "unresolved_machine_stages": [
            {
                "stage": "official_map_lane_connections",
                "status": "blocked",
                "required_evidence": (
                    "hash-bound Hamburg MAP lane/movement topology covering every selected official network node"
                ),
                "human_review_required": False,
            },
            {
                "stage": "official_signal_control_binding",
                "status": "blocked",
                "required_evidence": (
                    "hash-bound Hamburg MAP/OCIT controller, signal-group, stop-line, and movement assignments"
                ),
                "human_review_required": False,
            },
            {
                "stage": "netconvert_materialization",
                "status": "not_run",
                "required_evidence": "official connection PlainXML produced by the preceding machine stage",
                "human_review_required": False,
            },
        ],
        "netconvert": {
            "status": "not_run",
            "command": ["netconvert", "-c", paths["netconvert_config"].name],
            "candidate_net": paths["candidate_net"].name,
            "execution_gate": "blocked_until_official_connection_file_is_bound",
            "warning": (
                "Running this geometry-only config would let netconvert infer lane connections; such output is "
                "diagnostic only and cannot be promoted by this manifest."
            ),
        },
        "artifacts": artifacts,
        "excluded_inputs": ["OpenStreetMap", "OSM-derived SUMO network", "manual map review"],
        "claim_boundary": (
            "The candidate reproduces selected official HH-SIB road-axis geometry, official network-node identity, "
            "station intervals, directional lane counts, and unconditional speed. It makes no claim about legal "
            "lane connections, conflict areas, priority, stop lines, signal ownership, phasing, or timing."
        ),
    }
    write_json_atomic(paths["manifest"], manifest, sort_keys=True)
    return {
        **manifest,
        "output_dir": str(destination),
        "manifest_file": str(paths["manifest"]),
    }


def materialize_hamburg_hh_sib_snapshot_plainxml_candidate(
    *,
    snapshot_file: Path,
    request_url: str,
    bbox: tuple[float, float, float, float],
    target_time: datetime,
    retrieved_at: datetime,
    corridor_scope: Mapping[str, Any],
    output_dir: Path,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    expected_sha256: str | None = None,
    prefix: str = "hh_sib_official_corridor",
    geometry_join_tolerance_m: float = 2.0,
    orientation_margin_m: float = 1.0,
    named_scope_manifest_file: Path | None = None,
) -> dict[str, Any]:
    """Convenience entry point for a frozen HH-SIB response file."""

    return materialize_hamburg_hh_sib_plainxml_candidate(
        hh_sib_source=FrozenHamburgHhSibSnapshot(
            path=snapshot_file,
            request_url=request_url,
            bbox=bbox,
            target_time=target_time,
            retrieved_at=retrieved_at,
            valid_from=valid_from,
            valid_to=valid_to,
            expected_sha256=expected_sha256,
        ),
        corridor_scope=corridor_scope,
        output_dir=output_dir,
        prefix=prefix,
        geometry_join_tolerance_m=geometry_join_tolerance_m,
        orientation_margin_m=orientation_margin_m,
        named_scope_manifest_file=named_scope_manifest_file,
    )


def _load_hh_sib_report(
    source: Mapping[str, Any] | FrozenHamburgHhSibSnapshot,
) -> dict[str, Any]:
    if isinstance(source, FrozenHamburgHhSibSnapshot):
        return source.read()
    if not isinstance(source, Mapping):
        raise OfficialPlainXmlError(
            "hh_sib_source must be a parsed HH-SIB report or FrozenHamburgHhSibSnapshot"
        )
    return copy.deepcopy(dict(source))


def _validate_source_report(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("schema") != HH_SIB_ADAPTER_SCHEMA:
        raise OfficialPlainXmlError("HH-SIB adapter report schema is required")
    if str(report.get("acquisition_status")) != "pass":
        raise OfficialPlainXmlError("HH-SIB acquisition_status must pass")
    if str(report.get("claim_status", report.get("status"))) == "blocked":
        reasons = ", ".join(str(value) for value in report.get("blocking_reasons", []))
        raise OfficialPlainXmlError("blocked HH-SIB report cannot produce PlainXML" + (f": {reasons}" if reasons else ""))
    snapshot = report.get("source_snapshot")
    if not isinstance(snapshot, Mapping):
        raise OfficialPlainXmlError("HH-SIB source_snapshot is required")
    sha256 = str(snapshot.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise OfficialPlainXmlError("HH-SIB source_snapshot.sha256 must be a SHA-256 digest")
    source_path = snapshot.get("path")
    if source_path:
        path = Path(str(source_path)).expanduser().resolve()
        if path.is_file() and file_sha256(path) != sha256:
            raise OfficialPlainXmlError("HH-SIB source snapshot file no longer matches its report hash")
    return {"path": str(source_path) if source_path else None, "sha256": sha256}


def _validate_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(scope, Mapping):
        raise OfficialPlainXmlError("corridor_scope must be a mapping")
    if scope.get("schema") != OFFICIAL_CORRIDOR_SCOPE_SCHEMA:
        raise OfficialPlainXmlError(f"corridor_scope.schema must be {OFFICIAL_CORRIDOR_SCOPE_SCHEMA}")
    scope_id = str(scope.get("scope_id", "")).strip()
    if not scope_id:
        raise OfficialPlainXmlError("corridor_scope.scope_id is required")
    raw_links = scope.get("links")
    if not isinstance(raw_links, Sequence) or isinstance(raw_links, (str, bytes)) or not raw_links:
        raise OfficialPlainXmlError("corridor_scope.links must be a non-empty list")
    links: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_links):
        if not isinstance(raw, Mapping):
            raise OfficialPlainXmlError(f"corridor_scope.links[{index}] must be a mapping")
        object_id = _optional_text(raw.get("link_object_id"))
        tuple_fields = ("road_key", "from_network_node", "to_network_node", "branch_code")
        has_tuple = all(field in raw and raw[field] is not None for field in tuple_fields)
        if bool(object_id) == bool(has_tuple):
            raise OfficialPlainXmlError(
                f"corridor_scope.links[{index}] requires exactly one of link_object_id or the exact link tuple"
            )
        geometry_order = str(raw.get("geometry_order", "auto"))
        if geometry_order not in _GEOMETRY_ORDERS:
            raise OfficialPlainXmlError(
                f"corridor_scope.links[{index}].geometry_order must be one of {sorted(_GEOMETRY_ORDERS)}"
            )
        normalized: dict[str, Any] = {
            "geometry_order": geometry_order,
            "station_from_m": _optional_finite_float(raw.get("station_from_m"), f"links[{index}].station_from_m"),
            "station_to_m": _optional_finite_float(raw.get("station_to_m"), f"links[{index}].station_to_m"),
        }
        if object_id:
            normalized["link_object_id"] = object_id
        else:
            normalized.update(
                {
                    "road_key": str(raw["road_key"]),
                    "from_network_node": str(raw["from_network_node"]),
                    "to_network_node": str(raw["to_network_node"]),
                    "branch_code": _normalize_scalar(raw["branch_code"]),
                }
            )
        start = normalized["station_from_m"]
        end = normalized["station_to_m"]
        if start is not None and start < 0:
            raise OfficialPlainXmlError(f"corridor_scope.links[{index}].station_from_m cannot be negative")
        if start is not None and end is not None and end <= start:
            raise OfficialPlainXmlError(f"corridor_scope.links[{index}] station range must be increasing")
        links.append(normalized)
    return {
        "schema": OFFICIAL_CORRIDOR_SCOPE_SCHEMA,
        "scope_id": scope_id,
        "links": links,
    }


def _select_links(
    report: Mapping[str, Any],
    scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    assertions = report.get("motor_road_link_assertions")
    if not isinstance(assertions, Sequence) or isinstance(assertions, (str, bytes)):
        raise OfficialPlainXmlError("HH-SIB motor_road_link_assertions list is required")
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for selector in scope["links"]:
        matches = [item for item in assertions if isinstance(item, Mapping) and _selector_matches(selector, item)]
        if len(matches) != 1:
            raise OfficialPlainXmlError(
                "each corridor link selector must resolve exactly once; "
                f"selector={json.dumps(selector, ensure_ascii=False, sort_keys=True)} matches={len(matches)}"
            )
        assertion = copy.deepcopy(dict(matches[0]))
        if str(assertion.get("road_link_identity_status")) != "pass":
            raise OfficialPlainXmlError("selected HH-SIB road link lacks authoritative road identity")
        if assertion.get("link_property_conflicts"):
            raise OfficialPlainXmlError("selected HH-SIB road link has conflicting official link properties")
        object_id = _source_object_id(assertion)
        if object_id in selected_ids:
            raise OfficialPlainXmlError(f"duplicate selected HH-SIB link: {object_id}")
        selected_ids.add(object_id)
        assertion["_scope_selector"] = dict(selector)
        selected.append(assertion)
    return sorted(selected, key=_source_object_id)


def _selector_matches(selector: Mapping[str, Any], assertion: Mapping[str, Any]) -> bool:
    if selector.get("link_object_id") is not None:
        return _source_object_id(assertion) == str(selector["link_object_id"])
    return (
        str(assertion.get("road_key")) == str(selector["road_key"])
        and str(assertion.get("from_network_node")) == str(selector["from_network_node"])
        and str(assertion.get("to_network_node")) == str(selector["to_network_node"])
        and _normalize_scalar(assertion.get("branch_code")) == selector["branch_code"]
    )


def _build_plainxml_plan(
    *,
    report: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    geometry_join_tolerance_m: float,
    orientation_margin_m: float,
) -> dict[str, Any]:
    raw_features = report.get("raw_feature_assertions")
    if not isinstance(raw_features, Sequence) or isinstance(raw_features, (str, bytes)):
        raise OfficialPlainXmlError("HH-SIB raw_feature_assertions list is required")
    feature_by_id = {
        str(item.get("feature_id")): item
        for item in raw_features
        if isinstance(item, Mapping) and item.get("feature_id") is not None
    }
    selected_feature_groups: list[tuple[Mapping[str, Any], list[dict[str, Any]]]] = []
    all_lon_lat: list[tuple[float, float]] = []
    for link in selected:
        features: list[dict[str, Any]] = []
        for feature_ref in link.get("feature_refs", []):
            if not isinstance(feature_ref, Mapping):
                raise OfficialPlainXmlError(f"invalid feature reference on {_source_object_id(link)}")
            feature_id = str(feature_ref.get("object_id", ""))
            feature = feature_by_id.get(feature_id)
            if feature is None:
                raise OfficialPlainXmlError(
                    f"selected link {_source_object_id(link)} references missing raw feature {feature_id}"
                )
            normalized = _normalize_interval_feature(feature, link_id=_source_object_id(link))
            features.append(normalized)
            all_lon_lat.extend(normalized["lon_lat"])
        features.sort(key=lambda item: (item["station_from_m"], item["station_to_m"], item["feature_id"]))
        selected_feature_groups.append((link, _apply_station_scope(link, features)))

    projection = _projection_for_lon_lat(all_lon_lat)
    transformer = Transformer.from_crs("EPSG:4326", projection["crs"], always_xy=True)
    node_candidates: dict[str, list[tuple[float, float]]] = {}
    interval_plans: list[dict[str, Any]] = []
    link_evidence: list[dict[str, Any]] = []
    for link, features in selected_feature_groups:
        link_id = _source_object_id(link)
        projected_features = [
            {
                **feature,
                "xy": [tuple(map(float, transformer.transform(lon, lat))) for lon, lat in feature["lon_lat"]],
            }
            for feature in features
        ]
        orientation = _orient_intervals(
            projected_features,
            geometry_order=str(link["_scope_selector"]["geometry_order"]),
            geometry_join_tolerance_m=geometry_join_tolerance_m,
            orientation_margin_m=orientation_margin_m,
        )
        oriented = []
        for feature, reverse in zip(projected_features, orientation["reverse_by_interval"], strict=True):
            xy = list(reversed(feature["xy"])) if reverse else list(feature["xy"])
            oriented.append({**feature, "xy": xy})
        interval_plans.extend(
            _intervals_for_link(
                link,
                oriented,
                node_candidates=node_candidates,
            )
        )
        link_evidence.append(
            {
                "link_object_id": link_id,
                "road_key": link.get("road_key"),
                "road_name": link.get("road_name"),
                "from_network_node": str(link.get("from_network_node")),
                "to_network_node": str(link.get("to_network_node")),
                "branch_code": link.get("branch_code"),
                "length_m": link.get("length_m"),
                "selected_station_range_m": [
                    oriented[0]["station_from_m"],
                    oriented[-1]["station_to_m"],
                ],
                "feature_ids": [item["feature_id"] for item in oriented],
                "orientation": orientation,
                "source_status": link.get("status"),
            }
        )

    nodes = _resolve_nodes(node_candidates, tolerance_m=geometry_join_tolerance_m)
    edges: list[dict[str, Any]] = []
    for interval in interval_plans:
        shape = list(interval["xy"])
        shape[0] = nodes[interval["from_node"]]["xy"]
        shape[-1] = nodes[interval["to_node"]]["xy"]
        if interval["lanes_with"] > 0:
            edges.append(
                _directed_edge(interval, direction="with_stationing", shape=shape)
            )
        if interval["lanes_against"] > 0:
            edges.append(
                _directed_edge(
                    interval,
                    direction="against_stationing",
                    shape=list(reversed(shape)),
                )
            )
    edges.sort(key=lambda item: item["id"])
    return {
        "projection": projection,
        "nodes": nodes,
        "intervals": interval_plans,
        "edges": edges,
        "link_evidence": link_evidence,
    }


def _normalize_interval_feature(feature: Mapping[str, Any], *, link_id: str) -> dict[str, Any]:
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise OfficialPlainXmlError(f"raw feature on {link_id} lacks properties")
    geometry = feature.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("type") != "LineString":
        raise OfficialPlainXmlError(
            f"raw feature {feature.get('feature_id')} on {link_id} requires LineString geometry"
        )
    raw_coordinates = geometry.get("coordinates")
    if not isinstance(raw_coordinates, Sequence) or isinstance(raw_coordinates, (str, bytes)) or len(raw_coordinates) < 2:
        raise OfficialPlainXmlError(f"raw feature {feature.get('feature_id')} requires at least two coordinates")
    lon_lat = [_validate_lon_lat(value, feature_id=str(feature.get("feature_id"))) for value in raw_coordinates]
    station_from = _required_finite_float(properties.get("von_station"), "von_station")
    station_to = _required_finite_float(properties.get("bis_station"), "bis_station")
    if station_to <= station_from:
        raise OfficialPlainXmlError(f"raw feature {feature.get('feature_id')} station interval is not increasing")
    lanes_with = _non_negative_int(
        properties.get("fahrstreifenanzahl_in_stationierungsrichtung"),
        "fahrstreifenanzahl_in_stationierungsrichtung",
    )
    lanes_against = _non_negative_int(
        properties.get("fahrstreifenanzahl_gegen_stationierungsrichtung"),
        "fahrstreifenanzahl_gegen_stationierungsrichtung",
    )
    lanes_both = _non_negative_int(
        properties.get("fahrstreifenanzahl_in_beide_richtungen"),
        "fahrstreifenanzahl_in_beide_richtungen",
    )
    if lanes_both:
        raise OfficialPlainXmlError(
            f"raw feature {feature.get('feature_id')} has shared both-direction lanes; directional SUMO allocation is unresolved"
        )
    if lanes_with + lanes_against == 0:
        raise OfficialPlainXmlError(f"raw feature {feature.get('feature_id')} has no directed motor lanes")
    speed_raw = properties.get("geschwindigkeit")
    speed_mps = _unconditional_speed_mps(speed_raw)
    if speed_mps is None:
        raise OfficialPlainXmlError(
            f"raw feature {feature.get('feature_id')} speed is missing or conditional: {speed_raw!r}"
        )
    return {
        "feature_id": str(feature.get("feature_id")),
        "station_from_m": station_from,
        "station_to_m": station_to,
        "lanes_with": lanes_with,
        "lanes_against": lanes_against,
        "lanes_both": lanes_both,
        "carriageway_code_raw": properties.get("bahnigkeit"),
        "speed_raw": speed_raw,
        "speed_mps": speed_mps,
        "lon_lat": lon_lat,
    }


def _apply_station_scope(
    link: Mapping[str, Any],
    features: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not features:
        raise OfficialPlainXmlError(f"selected link {_source_object_id(link)} has no raw features")
    selector = link["_scope_selector"]
    start = selector.get("station_from_m")
    end = selector.get("station_to_m")
    start = features[0]["station_from_m"] if start is None else float(start)
    end = features[-1]["station_to_m"] if end is None else float(end)
    selected = [
        feature
        for feature in features
        if feature["station_from_m"] >= start and feature["station_to_m"] <= end
    ]
    if not selected or selected[0]["station_from_m"] != start or selected[-1]["station_to_m"] != end:
        raise OfficialPlainXmlError(
            f"station scope for {_source_object_id(link)} must align exactly with official interval boundaries"
        )
    for previous, current in zip(selected, selected[1:]):
        if previous["station_to_m"] != current["station_from_m"]:
            raise OfficialPlainXmlError(f"selected station scope for {_source_object_id(link)} is not contiguous")
    return selected


def _orient_intervals(
    features: Sequence[Mapping[str, Any]],
    *,
    geometry_order: str,
    geometry_join_tolerance_m: float,
    orientation_margin_m: float,
) -> dict[str, Any]:
    if geometry_order != "auto":
        reverse = geometry_order == "source_order_is_against_stationing"
        reverse_by_interval = [reverse] * len(features)
        gaps = _join_gaps(features, reverse_by_interval)
        if gaps and max(gaps) > geometry_join_tolerance_m:
            raise OfficialPlainXmlError(
                "explicit source geometry order does not form a continuous stationing chain"
            )
        return {
            "status": "pass",
            "basis": "explicit_corridor_scope",
            "geometry_order": geometry_order,
            "reverse_by_interval": reverse_by_interval,
            "join_gaps_m": gaps,
        }
    if len(features) < 2:
        raise OfficialPlainXmlError(
            "one-interval link has no internal evidence for stationing geometry order; provide an explicit geometry_order"
        )
    states: dict[bool, list[tuple[float, list[bool]]]] = {
        False: [(0.0, [False])],
        True: [(0.0, [True])],
    }
    for index in range(1, len(features)):
        next_states: dict[bool, list[tuple[float, list[bool]]]] = {}
        current = features[index]["xy"]
        previous = features[index - 1]["xy"]
        for reverse in (False, True):
            current_start = current[-1] if reverse else current[0]
            candidates_for_state: list[tuple[float, list[bool]]] = []
            for previous_reverse, previous_paths in states.items():
                previous_end = previous[0] if previous_reverse else previous[-1]
                join_cost = _xy_distance(previous_end, current_start)
                candidates_for_state.extend(
                    (cost + join_cost, [*path, reverse])
                    for cost, path in previous_paths
                )
            candidates_for_state.sort(key=lambda item: (item[0], item[1]))
            next_states[reverse] = candidates_for_state[:2]
        states = next_states
    candidates = sorted(
        (candidate for paths in states.values() for candidate in paths),
        key=lambda item: (item[0], item[1]),
    )
    best_cost, best_path = candidates[0]
    second_cost = candidates[1][0] if len(candidates) > 1 else math.inf
    gaps = _join_gaps(features, best_path)
    if not gaps or max(gaps) > geometry_join_tolerance_m:
        raise OfficialPlainXmlError("official interval geometries do not form a continuous stationing chain")
    if second_cost - best_cost < orientation_margin_m:
        raise OfficialPlainXmlError("official interval geometry stationing order is ambiguous")
    return {
        "status": "pass",
        "basis": "minimum_gap_across_ordered_official_station_intervals",
        "geometry_order": "per_interval_inferred",
        "reverse_by_interval": best_path,
        "best_chain_gap_sum_m": best_cost,
        "second_best_chain_gap_sum_m": second_cost,
        "join_gaps_m": gaps,
    }


def _join_gaps(features: Sequence[Mapping[str, Any]], reverse_by_interval: Sequence[bool]) -> list[float]:
    gaps: list[float] = []
    for index in range(1, len(features)):
        previous = features[index - 1]["xy"]
        previous_end = previous[0] if reverse_by_interval[index - 1] else previous[-1]
        current = features[index]["xy"]
        current_start = current[-1] if reverse_by_interval[index] else current[0]
        gaps.append(_xy_distance(previous_end, current_start))
    return gaps


def _intervals_for_link(
    link: Mapping[str, Any],
    features: Sequence[Mapping[str, Any]],
    *,
    node_candidates: dict[str, list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    link_id = _source_object_id(link)
    link_token = _stable_digest({"link_object_id": link_id})[:16]
    length_m = float(link["length_m"])
    intervals: list[dict[str, Any]] = []
    for feature in features:
        station_from = float(feature["station_from_m"])
        station_to = float(feature["station_to_m"])
        from_node = _station_node_id(
            link,
            link_token=link_token,
            station_m=station_from,
            length_m=length_m,
        )
        to_node = _station_node_id(
            link,
            link_token=link_token,
            station_m=station_to,
            length_m=length_m,
        )
        node_candidates.setdefault(from_node, []).append(tuple(feature["xy"][0]))
        node_candidates.setdefault(to_node, []).append(tuple(feature["xy"][-1]))
        intervals.append(
            {
                "link_object_id": link_id,
                "link_token": link_token,
                "road_key": str(link["road_key"]),
                "road_name": str(link["road_name"]),
                "feature_id": str(feature["feature_id"]),
                "station_from_m": station_from,
                "station_to_m": station_to,
                "from_node": from_node,
                "to_node": to_node,
                "lanes_with": int(feature["lanes_with"]),
                "lanes_against": int(feature["lanes_against"]),
                "speed_mps": float(feature["speed_mps"]),
                "speed_raw": feature["speed_raw"],
                "carriageway_code_raw": feature["carriageway_code_raw"],
                "xy": list(feature["xy"]),
            }
        )
    return intervals


def _station_node_id(
    link: Mapping[str, Any],
    *,
    link_token: str,
    station_m: float,
    length_m: float,
) -> str:
    if math.isclose(station_m, 0.0, abs_tol=1e-9):
        return "hh_sib.n." + _safe_id(str(link["from_network_node"]))
    if math.isclose(station_m, length_m, abs_tol=1e-9):
        return "hh_sib.n." + _safe_id(str(link["to_network_node"]))
    return f"hh_sib.s.{link_token}.{_station_token(station_m)}"


def _resolve_nodes(
    node_candidates: Mapping[str, Sequence[tuple[float, float]]],
    *,
    tolerance_m: float,
) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for node_id in sorted(node_candidates):
        coordinates = list(node_candidates[node_id])
        canonical = coordinates[0]
        gaps = [_xy_distance(canonical, value) for value in coordinates[1:]]
        if gaps and max(gaps) > tolerance_m:
            raise OfficialPlainXmlError(
                f"official node {node_id} has endpoint disagreement above {tolerance_m} m"
            )
        nodes[node_id] = {
            "xy": canonical,
            "evidence_coordinate_count": len(coordinates),
            "maximum_endpoint_gap_m": max(gaps, default=0.0),
            "official_network_node": node_id.startswith("hh_sib.n."),
        }
    return nodes


def _directed_edge(
    interval: Mapping[str, Any],
    *,
    direction: str,
    shape: Sequence[tuple[float, float]],
) -> dict[str, Any]:
    with_stationing = direction == "with_stationing"
    edge_id = (
        f"hh_sib.e.{interval['link_token']}.{_station_token(interval['station_from_m'])}"
        f"_{_station_token(interval['station_to_m'])}.{'with' if with_stationing else 'against'}"
    )
    return {
        "id": edge_id,
        "from_node": interval["from_node"] if with_stationing else interval["to_node"],
        "to_node": interval["to_node"] if with_stationing else interval["from_node"],
        "num_lanes": interval["lanes_with"] if with_stationing else interval["lanes_against"],
        "speed_mps": interval["speed_mps"],
        "shape": list(shape),
        "direction": direction,
        "link_object_id": interval["link_object_id"],
        "road_key": interval["road_key"],
        "road_name": interval["road_name"],
        "feature_id": interval["feature_id"],
        "station_from_m": interval["station_from_m"],
        "station_to_m": interval["station_to_m"],
        "speed_raw": interval["speed_raw"],
        "carriageway_code_raw": interval["carriageway_code_raw"],
    }


def _write_nodes(path: Path, plan: Mapping[str, Any]) -> None:
    root = ET.Element("nodes")
    for node_id, node in plan["nodes"].items():
        element = ET.SubElement(
            root,
            "node",
            id=node_id,
            x=_decimal(node["xy"][0]),
            y=_decimal(node["xy"][1]),
        )
        ET.SubElement(
            element,
            "param",
            key="torii:control_status",
            value="unresolved_official_map_stage",
        )
        if node["official_network_node"]:
            ET.SubElement(
                element,
                "param",
                key="torii:hh_sib_network_node",
                value=node_id.removeprefix("hh_sib.n."),
            )
    _write_xml(path, root)


def _write_edges(path: Path, plan: Mapping[str, Any], *, source_sha256: str) -> None:
    root = ET.Element("edges")
    for edge in plan["edges"]:
        element = ET.SubElement(
            root,
            "edge",
            id=edge["id"],
            **{
                "from": edge["from_node"],
                "to": edge["to_node"],
                "type": "hh_sib.motor_vehicle",
                "name": edge["road_name"],
                "numLanes": str(edge["num_lanes"]),
                "speed": _decimal(edge["speed_mps"], places=6),
                "spreadType": "right",
                "shape": " ".join(f"{_decimal(x)},{_decimal(y)}" for x, y in edge["shape"]),
            },
        )
        params = {
            "origId": f"official.hh_sib:{edge['link_object_id']}",
            "torii:source_sha256": source_sha256,
            "torii:hh_sib_road_key": edge["road_key"],
            "torii:hh_sib_feature_id": edge["feature_id"],
            "torii:station_from_m": _decimal(edge["station_from_m"], places=3),
            "torii:station_to_m": _decimal(edge["station_to_m"], places=3),
            "torii:station_direction": edge["direction"],
            "torii:speed_rule_raw": str(edge["speed_raw"]),
            "torii:carriageway_code_raw": str(edge["carriageway_code_raw"]),
            "torii:connection_status": "unresolved_official_map_stage",
        }
        for key, value in sorted(params.items()):
            ET.SubElement(element, "param", key=key, value=value)
    _write_xml(path, root)


def _write_types(path: Path) -> None:
    root = ET.Element("types")
    ET.SubElement(
        root,
        "type",
        id="hh_sib.motor_vehicle",
        priority="3",
        numLanes="1",
        speed="13.888889",
        allow=_MOTOR_VCLASSES,
    )
    _write_xml(path, root)


def _write_netconvert_config(
    file_paths: Mapping[str, Path],
    *,
    prefix: str,
    projection: Mapping[str, Any],
) -> None:
    root = ET.Element("configuration")
    input_element = ET.SubElement(root, "input")
    ET.SubElement(input_element, "node-files", value=file_paths["nodes"].name)
    ET.SubElement(input_element, "edge-files", value=file_paths["edges"].name)
    ET.SubElement(input_element, "type-files", value=file_paths["types"].name)
    projection_element = ET.SubElement(root, "projection")
    ET.SubElement(
        projection_element,
        "proj",
        value=_netconvert_projection(projection),
    )
    # PlainXML coordinates are already in the projected CRS.  This preserves
    # the geographic projection metadata in the compiled .net.xml without
    # transforming the metric x/y values a second time.
    ET.SubElement(projection_element, "proj.inverse", value="true")
    output_element = ET.SubElement(root, "output")
    ET.SubElement(output_element, "output-file", value=file_paths["candidate_net"].name)
    processing = ET.SubElement(root, "processing")
    ET.SubElement(processing, "no-turnarounds", value="true")
    root.insert(
        0,
        ET.Comment(
            f" {prefix}: geometry-only; official connection PlainXML is intentionally absent and required before promotion "
        ),
    )
    _write_xml(file_paths["netconvert_config"], root)


def _netconvert_projection(projection: Mapping[str, Any]) -> str:
    target_crs = str(projection.get("crs", "")).strip()
    if not target_crs:
        raise OfficialPlainXmlError("PlainXML projection CRS is required for netconvert")
    try:
        crs = CRS.from_user_input(target_crs)
        if not crs.is_projected:
            raise OfficialPlainXmlError("PlainXML netconvert projection must be projected")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            value = crs.to_proj4()
    except OfficialPlainXmlError:
        raise
    except Exception as exc:
        raise OfficialPlainXmlError(
            f"cannot derive netconvert projection from {target_crs!r}"
        ) from exc
    return re.sub(r"\s+\+type=crs$", "", value.strip())


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    payload = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    write_text_atomic(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + payload + "\n")


def _projection_for_lon_lat(points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    if not points:
        raise OfficialPlainXmlError("selected official geometry is empty")
    mean_lon = sum(point[0] for point in points) / len(points)
    mean_lat = sum(point[1] for point in points) / len(points)
    if mean_lat > 84 or mean_lat < -80:
        raise OfficialPlainXmlError("official geometry is outside the UTM latitude domain")
    zone = max(1, min(60, int(math.floor((mean_lon + 180) / 6)) + 1))
    epsg = (32600 if mean_lat >= 0 else 32700) + zone
    return {
        "source_crs": "EPSG:4326",
        "crs": f"EPSG:{epsg}",
        "method": "deterministic_mean_coordinate_utm_zone",
        "utm_zone": zone,
        "hemisphere": "north" if mean_lat >= 0 else "south",
    }


def _validate_lon_lat(value: Any, *, feature_id: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) < 2:
        raise OfficialPlainXmlError(f"feature {feature_id} has an invalid coordinate")
    lon = _required_finite_float(value[0], "longitude")
    lat = _required_finite_float(value[1], "latitude")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise OfficialPlainXmlError(f"feature {feature_id} has a coordinate outside EPSG:4326")
    return lon, lat


def _unconditional_speed_mps(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        speed_kmh = float(value)
    else:
        match = _UNCONDITIONAL_SPEED_PATTERN.fullmatch(str(value or ""))
        if match is None:
            return None
        speed_kmh = float(match.group(1).replace(",", "."))
    if not math.isfinite(speed_kmh) or speed_kmh <= 0:
        return None
    return speed_kmh / 3.6


def _source_object_id(assertion: Mapping[str, Any]) -> str:
    source_ref = assertion.get("source_ref")
    if not isinstance(source_ref, Mapping) or not source_ref.get("object_id"):
        raise OfficialPlainXmlError("road-link assertion source_ref.object_id is required")
    return str(source_ref["object_id"])


def _validate_separate_destination(destination: Path, raw_source_path: Any) -> None:
    if raw_source_path in (None, ""):
        return
    source_path = Path(str(raw_source_path)).expanduser().resolve()
    if destination == source_path.parent or _is_within(source_path, destination):
        raise OfficialPlainXmlError("output_dir must be a separate candidate directory from the frozen source snapshot")


def _portable_report_sha256(report: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(report))
    snapshot = payload.get("source_snapshot")
    if isinstance(snapshot, dict):
        snapshot.pop("path", None)
    return _stable_digest(payload)


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    if not normalized:
        raise OfficialPlainXmlError("official network node id cannot be normalized safely")
    if normalized != value:
        normalized += "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return normalized


def _station_token(value: float) -> str:
    return _decimal(float(value), places=3).replace("-", "m").replace(".", "p")


def _decimal(value: float, *, places: int = 3) -> str:
    text = f"{float(value):.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _xy_distance(first: Sequence[float], second: Sequence[float]) -> float:
    return math.hypot(float(first[0]) - float(second[0]), float(first[1]) - float(second[1]))


def _normalize_scalar(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise OfficialPlainXmlError(f"{field_name} must be a non-negative integer")
    try:
        integer = int(value)
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialPlainXmlError(f"{field_name} must be a non-negative integer") from exc
    if integer < 0 or numeric != integer:
        raise OfficialPlainXmlError(f"{field_name} must be a non-negative integer")
    return integer


def _required_finite_float(value: Any, field_name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialPlainXmlError(f"{field_name} must be finite") from exc
    if not math.isfinite(numeric):
        raise OfficialPlainXmlError(f"{field_name} must be finite")
    return numeric


def _optional_finite_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _required_finite_float(value, field_name)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


__all__ = [
    "FrozenHamburgHhSibSnapshot",
    "OFFICIAL_CORRIDOR_SCOPE_SCHEMA",
    "OFFICIAL_PLAINXML_SCHEMA",
    "OfficialPlainXmlError",
    "materialize_hamburg_hh_sib_plainxml_candidate",
    "materialize_hamburg_hh_sib_snapshot_plainxml_candidate",
]
