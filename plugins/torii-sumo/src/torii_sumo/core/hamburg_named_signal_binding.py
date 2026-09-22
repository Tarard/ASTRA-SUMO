"""Bind Hamburg TLD signal metadata to an official-first SUMO corridor.

This module only binds evidence.  It never invents signal phases or rewrites the
candidate network.  A later stage may consume the bindings together with
historical observations.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256


SIGNAL_BINDING_SCHEMA = "torii.hamburg-named-signal-binding/v1"
_COMPOUND_TLS_SCHEMA = "torii.hamburg-compound-official-tls/v1"
_COMPOUND_DERIVATION_SCHEMA = "torii.official-tls-plan-derivation.v1"
_DEFAULT_NODES = ("2349", "2394", "2403")


class HamburgSignalBindingError(ValueError):
    """Raised when signal-binding inputs cannot be interpreted safely."""


def materialize_hamburg_named_signal_binding(
    *,
    net_file: Path,
    intersection_manifests: Mapping[str, Path],
    signal_stream_files: Sequence[Path],
    output_dir: Path,
    required_node_ids: Sequence[str] = _DEFAULT_NODES,
    compound_tls_manifest: Path | None = None,
) -> dict[str, Any]:
    """Create a hash-bound, non-mutating TLD-to-SUMO binding artifact.

    ``intersection_manifests`` must contain the available official MAP cells.
    Missing required nodes are reported as a partial, non-promoting result;
    they are never filled with guessed streams or phases.
    """

    net_path = Path(net_file).expanduser().resolve(strict=True)
    required = tuple(dict.fromkeys(str(node).strip() for node in required_node_ids if str(node).strip()))
    if not required:
        raise HamburgSignalBindingError("at least one required node is needed")
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise HamburgSignalBindingError("output_dir must be empty; choose a new versioned run")

    manifest_paths = _resolve_manifests(intersection_manifests)
    stream_paths = [Path(path).expanduser().resolve(strict=True) for path in signal_stream_files]
    if not stream_paths:
        raise HamburgSignalBindingError("at least one signal stream file is required")
    compound_path = (
        Path(compound_tls_manifest).expanduser().resolve(strict=True)
        if compound_tls_manifest is not None
        else None
    )
    compound = _load_compound_tls_manifest(compound_path) if compound_path is not None else None

    local, input_errors = _load_intersection_manifests(manifest_paths)
    streams, stream_sources, stream_errors = _load_stream_files(stream_paths)
    errors = [*input_errors, *stream_errors]
    try:
        net_root = ET.parse(net_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HamburgSignalBindingError(f"cannot parse candidate SUMO network: {net_path}") from exc

    candidate_connections = _index_candidate_connections(net_root)
    bindings: list[dict[str, Any]] = []
    node_reports: dict[str, dict[str, Any]] = {}
    for node_id in required:
        manifest = local.get(node_id)
        node_streams = [stream for stream in streams if stream["node_id"] == node_id]
        if manifest is None:
            node_reports[node_id] = {
                "status": "missing_official_map_cell",
                "stream_count": len(node_streams),
                "bound_count": 0,
                "expected_vehicle_movement_count": 0,
            }
            continue
        movements = manifest["movements"]
        node_errors: list[dict[str, Any]] = []
        movement_by_connection: dict[str, Mapping[str, Any]] = {}
        duplicate_connection_ids: set[str] = set()
        for movement in movements:
            connection_id = str(movement["connection_id"])
            if connection_id in movement_by_connection:
                duplicate_connection_ids.add(connection_id)
            else:
                movement_by_connection[connection_id] = movement
        if duplicate_connection_ids:
            node_errors.append(
                {
                    "code": "duplicate_official_connection_id",
                    "node_id": node_id,
                    "connection_ids": sorted(duplicate_connection_ids, key=_identifier_sort_key),
                }
            )
        compound_movements = {
            connection_id: target
            for (target_node, connection_id), target in (compound or {}).get("movements", {}).items()
            if target_node == node_id
        }
        compound_node = (compound or {}).get("nodes", {}).get(node_id)
        if compound is not None and compound_node is None:
            node_errors.append({"code": "compound_tls_node_missing", "node_id": node_id})
        if compound_node is not None:
            manifest_connection_ids = set(movement_by_connection)
            compound_connection_ids = set(compound_movements)
            if manifest_connection_ids != compound_connection_ids:
                node_errors.append(
                    {
                        "code": "compound_tls_movement_inventory_mismatch",
                        "node_id": node_id,
                        "missing_connection_ids": sorted(
                            manifest_connection_ids - compound_connection_ids,
                            key=_identifier_sort_key,
                        ),
                        "unexpected_connection_ids": sorted(
                            compound_connection_ids - manifest_connection_ids,
                            key=_identifier_sort_key,
                        ),
                    }
                )
            for connection_id in manifest_connection_ids & compound_connection_ids:
                movement = movement_by_connection[connection_id]
                target = compound_movements[connection_id]
                mismatches = [
                    key
                    for key, target_key in (
                        ("ingress_lane_id", "official_ingress_lane"),
                        ("egress_lane_id", "official_egress_lane"),
                        ("topology_control_key", "signal_group"),
                    )
                    if str(movement.get(key, "")) != str(target.get(target_key, ""))
                ]
                if mismatches:
                    node_errors.append(
                        {
                            "code": "compound_tls_movement_identity_mismatch",
                            "node_id": node_id,
                            "connection_id": connection_id,
                            "mismatches": mismatches,
                        }
                    )
        controller_id = (
            str(compound_node["controller_id"])
            if compound_node is not None
            else str(manifest.get("controller_id", f"hh-map-{node_id}"))
        )
        bound_ids: set[str] = set()
        expected_physical_keys: set[tuple[str, int, str, int]] = set()
        if compound_node is not None:
            expected_physical_keys.update(compound_node["physical_keys"])
        else:
            for movement in movements:
                try:
                    expected_physical_keys.add(_official_movement_physical_key(movement))
                except (KeyError, TypeError, ValueError):
                    node_errors.append(
                        {
                            "code": "official_movement_physical_key_missing",
                            "node_id": node_id,
                            "connection_id": str(movement.get("connection_id", "")),
                        }
                    )
        candidate_rows_for_controller = [
            row
            for (candidate_controller, _link_index), rows in candidate_connections.items()
            if candidate_controller == controller_id
            for row in rows
        ]
        candidate_physical_keys = {
            _candidate_connection_physical_key(row) for row in candidate_rows_for_controller
        }
        candidate_physical_multiplicities = _candidate_physical_multiplicities(candidate_rows_for_controller)
        if candidate_physical_multiplicities:
            node_errors.append(
                {
                    "code": "candidate_physical_connection_multiplicity",
                    "node_id": node_id,
                    "controller_id": controller_id,
                    "connections": candidate_physical_multiplicities,
                }
            )
        missing_physical_keys = expected_physical_keys - candidate_physical_keys
        unexpected_physical_keys = candidate_physical_keys - expected_physical_keys
        if missing_physical_keys or unexpected_physical_keys:
            node_errors.append(
                {
                    "code": "candidate_official_movement_physical_key_mismatch",
                    "node_id": node_id,
                    "controller_id": controller_id,
                    "missing_physical_keys": _physical_key_rows(missing_physical_keys),
                    "unexpected_physical_keys": _physical_key_rows(unexpected_physical_keys),
                }
            )
        for stream in node_streams:
            movement = movement_by_connection.get(stream["connection_id"])
            if movement is None:
                node_errors.append({"code": "stream_connection_not_in_official_map", "stream": stream})
                continue
            mismatches = _movement_mismatches(stream, movement)
            compound_target = compound_movements.get(stream["connection_id"])
            if compound is not None and compound_target is None:
                node_errors.append(
                    {
                        "code": "stream_connection_not_in_compound_tls_evidence",
                        "stream": stream,
                    }
                )
                continue
            target_controller_id = (
                str(compound_target["controller_id"])
                if compound_target is not None
                else controller_id
            )
            link_index = str(
                compound_target["link_index"]
                if compound_target is not None
                else movement["link_index"]
            )
            candidate_rows = candidate_connections.get((target_controller_id, link_index), [])
            if not candidate_rows:
                node_errors.append(
                    {
                        "code": "official_link_index_not_in_candidate",
                        "node_id": node_id,
                        "connection_id": stream["connection_id"],
                        "controller_id": target_controller_id,
                        "link_index": int(link_index),
                    }
                )
                continue
            if compound_target is not None:
                movement_physical_keys = set(compound_target["physical_keys"])
            else:
                try:
                    movement_physical_keys = {_official_movement_physical_key(movement)}
                except (KeyError, TypeError, ValueError):
                    continue
            exact_candidate_rows = [
                row
                for row in candidate_rows
                if _candidate_connection_physical_key(row) in movement_physical_keys
            ]
            matched_physical_keys = {
                _candidate_connection_physical_key(row) for row in exact_candidate_rows
            }
            if matched_physical_keys != movement_physical_keys or len(exact_candidate_rows) != len(
                movement_physical_keys
            ):
                node_errors.append(
                    {
                        "code": "official_movement_candidate_physical_connection_mismatch",
                        "node_id": node_id,
                        "connection_id": stream["connection_id"],
                        "controller_id": target_controller_id,
                        "link_index": int(link_index),
                        "expected_physical_keys": _physical_key_rows(movement_physical_keys),
                        "matching_candidate_count": len(exact_candidate_rows),
                    }
                )
                continue
            if mismatches:
                node_errors.append(
                    {
                        "code": "stream_movement_mismatch",
                        "stream": stream,
                        "movement": movement,
                        "mismatches": mismatches,
                    }
                )
                continue
            bound_ids.add(stream["connection_id"])
            bindings.append(
                {
                    "node_id": node_id,
                    "stream_id": stream["stream_id"],
                    "connection_id": stream["connection_id"],
                    "ingress_lane_id": stream["ingress_lane_id"],
                    "egress_lane_id": stream["egress_lane_id"],
                    "signal_group": stream["signal_group"],
                    "compound_signal_group": (
                        str(compound_target["signal_group"])
                        if compound_target is not None
                        else ""
                    ),
                    "controller_id": target_controller_id,
                    "link_index": int(link_index),
                    "candidate_connections": exact_candidate_rows,
                    "official_map_signal_groups": {
                        "primary": list(movement.get("primary_motor_groups", [])),
                        "secondary": list(movement.get("secondary_motor_groups", [])),
                    },
                }
            )
        unbound = sorted(set(movement_by_connection) - bound_ids, key=_identifier_sort_key)
        if unbound:
            node_errors.append(
                {
                    "code": "official_vehicle_movement_without_signal_stream",
                    "node_id": node_id,
                    "connection_ids": unbound,
                }
            )
        node_reports[node_id] = {
            "status": "pass" if not node_errors else "blocked",
            "stream_count": len(node_streams),
            "bound_count": len(bound_ids),
            "expected_vehicle_movement_count": len(movements),
            "unbound_connection_ids": unbound,
            "official_movement_physical_key_parity": {
                "status": (
                    "pass"
                    if not missing_physical_keys
                    and not unexpected_physical_keys
                    and not candidate_physical_multiplicities
                    and (
                        compound_node is not None
                        or len(expected_physical_keys) == len(movements)
                    )
                    else "blocked"
                ),
                "controller_id": controller_id,
                "expected_count": len(expected_physical_keys),
                "candidate_count": len(candidate_rows_for_controller),
                "candidate_unique_count": len(candidate_physical_keys),
                "missing_physical_keys": _physical_key_rows(missing_physical_keys),
                "unexpected_physical_keys": _physical_key_rows(unexpected_physical_keys),
                "duplicate_physical_connections": candidate_physical_multiplicities,
                "evidence_mode": "compound_osm_tls_plan" if compound_node is not None else "local_map_cell",
            },
            "errors": node_errors,
        }
        errors.extend(node_errors)

    missing_nodes = [node_id for node_id in required if node_id not in local]
    structural_errors = [error for error in errors if error.get("code") != "official_vehicle_movement_without_signal_stream"]
    complete_available = not structural_errors and all(
        report.get("status") == "pass" for node_id, report in node_reports.items() if node_id in local
    )
    status = "partial" if complete_available and missing_nodes else ("pass" if complete_available else "blocked")
    execution_gate = "pass" if complete_available else "blocked"
    promotion_gate = "pass" if complete_available and not missing_nodes else "blocked"
    binding_file = destination / "official-primary-signal-bindings.json"
    manifest_file = destination / "official-primary-signal-binding.manifest.json"
    write_json_atomic(binding_file, {"schema": SIGNAL_BINDING_SCHEMA, "bindings": bindings}, sort_keys=True)
    source_records = [
        {"path": str(path), "sha256": file_sha256(path), **metadata}
        for path, metadata in stream_sources
    ]
    manifest: dict[str, Any] = {
        "schema": SIGNAL_BINDING_SCHEMA,
        "status": status,
        "execution_gate": execution_gate,
        "execution_gate_reason": (
            "available official MAP cells and every supplied TLD stream bind to candidate controller link indices"
            if execution_gate == "pass"
            else "one or more official signal bindings are structurally unresolved"
        ),
        "automatic_promotion_gate": promotion_gate,
        "claim_status": "official-tld-primary-signal-metadata-bound-to-map-movements",
        "source": {
            "candidate_net": {"path": str(net_path), "sha256": file_sha256(net_path)},
            "intersection_manifests": {
                node_id: {"path": str(path), "sha256": file_sha256(path)}
                for node_id, path in manifest_paths.items()
            },
            "signal_stream_files": source_records,
            "compound_tls_manifest": (
                {"path": str(compound_path), "sha256": file_sha256(compound_path)}
                if compound_path is not None
                else None
            ),
        },
        "required_node_ids": list(required),
        "available_map_node_ids": sorted(local),
        "missing_official_signal_node_ids": missing_nodes,
        "node_reports": node_reports,
        "binding_artifact": {"path": str(binding_file), "sha256": file_sha256(binding_file)},
        "historical_signal_replay": {
            "status": "blocked_pending_official_observations",
            "reason": "metadata identifies controlled movements but does not contain the requested Saturday history",
        },
        "errors": errors,
        "gates": {
            "official_source_hashes": "pass",
            "map_movement_identity": "pass" if complete_available else "blocked",
            "official_movement_physical_key_parity": "pass" if complete_available else "blocked",
            "candidate_controller_link_indices": "pass" if complete_available else "blocked",
            "2403_official_signal_asset": "pass" if "2403" not in missing_nodes else "blocked",
            "historical_signal_replay": "blocked_pending_official_observations",
            "automatic_promotion": promotion_gate,
        },
        "claim_boundary": {
            "proves": [
                "which official TLD primary-signal streams were used",
                "which official MAP movement and SUMO controller linkIndex each stream binds to",
                "that the candidate controlled physical connection set exactly equals the selected official TLS plan",
                "that missing 2403 signal data is explicit rather than guessed",
            ],
            "does_not_prove": [
                "historical Saturday signal phases or cycle timing",
                "a complete three-node signal controller while a required node is missing",
            ],
        },
        "artifacts": {"bindings": str(binding_file), "manifest": str(manifest_file)},
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return {**manifest, "manifest_file": str(manifest_file)}


def _resolve_manifests(manifests: Mapping[str, Path]) -> dict[str, Path]:
    if not isinstance(manifests, Mapping) or not manifests:
        raise HamburgSignalBindingError("intersection_manifests must not be empty")
    result: dict[str, Path] = {}
    for raw_node, raw_path in manifests.items():
        node_id = str(raw_node).strip()
        if not node_id:
            raise HamburgSignalBindingError("intersection manifest node id must not be empty")
        if node_id in result:
            raise HamburgSignalBindingError(f"duplicate intersection manifest: {node_id}")
        result[node_id] = Path(raw_path).expanduser().resolve(strict=True)
    return result


def _load_intersection_manifests(paths: Mapping[str, Path]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    loaded: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for node_id, path in paths.items():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"code": "intersection_manifest_invalid", "node_id": node_id, "message": str(exc)})
            continue
        if not isinstance(payload, Mapping) or str(payload.get("node_id")) != node_id:
            errors.append({"code": "intersection_manifest_identity_mismatch", "node_id": node_id})
            continue
        movements = payload.get("movements")
        if not isinstance(movements, list) or not movements:
            errors.append({"code": "intersection_manifest_movements_missing", "node_id": node_id})
            continue
        loaded[node_id] = dict(payload)
    return loaded, errors


def _load_compound_tls_manifest(path: Path) -> dict[str, Any]:
    """Read the existing Torii official-movement plan without re-deriving topology."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HamburgSignalBindingError(f"cannot parse compound TLS manifest: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_id") != _COMPOUND_TLS_SCHEMA:
        raise HamburgSignalBindingError("compound TLS manifest schema is invalid")
    derivation = payload.get("tls_derivation")
    network_rebuild = payload.get("network_rebuild")
    plan = network_rebuild.get("plan") if isinstance(network_rebuild, Mapping) else None
    if (
        payload.get("status") != "topology_ready"
        or not isinstance(derivation, Mapping)
        or derivation.get("schema_id") != _COMPOUND_DERIVATION_SCHEMA
        or derivation.get("status") != "pass"
        or not isinstance(plan, Mapping)
    ):
        raise HamburgSignalBindingError("compound TLS manifest is not topology-ready")
    raw_groups = plan.get("groups")
    raw_movements = derivation.get("movements")
    if not isinstance(raw_groups, list) or not raw_groups or not isinstance(raw_movements, list) or not raw_movements:
        raise HamburgSignalBindingError("compound TLS manifest has no groups or movements")

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    physical_owner: dict[tuple[str, int, str, int], tuple[str, str]] = {}
    for raw in raw_groups:
        if not isinstance(raw, Mapping):
            raise HamburgSignalBindingError("compound TLS group is not an object")
        try:
            node_id = str(raw["official_node_id"])
            signal_group = str(raw["signal_group"])
            controller_id = str(raw["tls_id"])
            link_index = int(raw["link_index"])
            physical_keys = _evidence_physical_keys(raw["physical_links"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HamburgSignalBindingError("compound TLS group identity is invalid") from exc
        key = (node_id, signal_group)
        if key in groups or not controller_id or not physical_keys:
            raise HamburgSignalBindingError(f"compound TLS group is duplicate or empty: {key}")
        node = nodes.setdefault(
            node_id,
            {"controller_id": controller_id, "physical_keys": set(), "link_indices": set()},
        )
        if node["controller_id"] != controller_id or link_index in node["link_indices"]:
            raise HamburgSignalBindingError(f"compound TLS controller/linkIndex is ambiguous for node {node_id}")
        for physical_key in physical_keys:
            previous = physical_owner.get(physical_key)
            if previous is not None:
                raise HamburgSignalBindingError(
                    f"compound physical connection belongs to multiple groups: {previous}, {key}"
                )
            physical_owner[physical_key] = key
        node["physical_keys"].update(physical_keys)
        node["link_indices"].add(link_index)
        groups[key] = {
            "controller_id": controller_id,
            "link_index": link_index,
            "physical_keys": physical_keys,
        }

    movements: dict[tuple[str, str], dict[str, Any]] = {}
    selected_by_node: dict[str, set[tuple[str, int, str, int]]] = {}
    for raw in raw_movements:
        if not isinstance(raw, Mapping):
            raise HamburgSignalBindingError("compound TLS movement is not an object")
        try:
            node_id = str(raw["official_node_id"])
            connection_id = str(raw["connection_id"])
            signal_group = str(raw["signal_group"])
            selected = _evidence_physical_keys(raw["selected_physical_links"])
            official_ingress_lane = str(raw["official_ingress_lane"])
            official_egress_lane = str(raw["official_egress_lane"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HamburgSignalBindingError("compound TLS movement identity is invalid") from exc
        movement_key = (node_id, connection_id)
        group = groups.get((node_id, signal_group))
        if len(selected) != 1:
            raise HamburgSignalBindingError(
                "compound TLS movement must identify exactly one physical stop-line connection "
                f"unless explicit multi-stop-line evidence is added: {movement_key}"
            )
        if movement_key in movements or group is None or not selected <= group["physical_keys"]:
            raise HamburgSignalBindingError(f"compound TLS movement is duplicate or unresolved: {movement_key}")
        movements[movement_key] = {
            **group,
            "signal_group": signal_group,
            "physical_keys": selected,
            "official_ingress_lane": official_ingress_lane,
            "official_egress_lane": official_egress_lane,
        }
        selected_by_node.setdefault(node_id, set()).update(selected)

    for node_id, node in nodes.items():
        if selected_by_node.get(node_id, set()) != node["physical_keys"]:
            raise HamburgSignalBindingError(
                f"compound TLS movements do not cover the physical inventory for node {node_id}"
            )
    return {"movements": movements, "nodes": nodes}


def _evidence_physical_keys(rows: object) -> set[tuple[str, int, str, int]]:
    if not isinstance(rows, list):
        raise TypeError("physical links must be a list")
    keys: set[tuple[str, int, str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("physical link must be an object")
        key = (
            str(row["from_edge"]),
            int(row["from_lane"]),
            str(row["to_edge"]),
            int(row["to_lane"]),
        )
        if key in keys:
            raise ValueError(f"duplicate physical link: {key}")
        keys.add(key)
    return keys


def _load_stream_files(
    paths: Sequence[Path],
) -> tuple[list[dict[str, Any]], list[tuple[Path, dict[str, Any]]], list[dict[str, Any]]]:
    streams_by_id: dict[int, dict[str, Any]] = {}
    source_records: list[tuple[Path, dict[str, Any]]] = []
    errors: list[dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"code": "signal_stream_file_invalid", "path": str(path), "message": str(exc)})
            continue
        values = payload.get("streams") if isinstance(payload, Mapping) else payload
        if not isinstance(values, list):
            errors.append({"code": "signal_streams_missing", "path": str(path)})
            continue
        metadata = {
            "schema": str(payload.get("schema", "")) if isinstance(payload, Mapping) else "",
            "api_base_url": str(payload.get("api_base_url", "")) if isinstance(payload, Mapping) else "",
        }
        source_records.append((path, metadata))
        for value in values:
            if not isinstance(value, Mapping):
                errors.append({"code": "signal_stream_not_object", "path": str(path)})
                continue
            try:
                stream = {
                    "stream_id": int(value["stream_id"]),
                    "node_id": str(value["node_id"]),
                    "connection_id": str(value["connection_id"]),
                    "ingress_lane_id": str(value["ingress_lane_id"]),
                    "egress_lane_id": str(value["egress_lane_id"]),
                    "signal_group": str(value["signal_group"]),
                    "layer_name": str(value.get("layer_name", "")),
                    "lane_type": str(value.get("lane_type", "")),
                }
            except (KeyError, TypeError, ValueError) as exc:
                errors.append({"code": "signal_stream_identity_invalid", "path": str(path), "message": str(exc)})
                continue
            previous = streams_by_id.get(stream["stream_id"])
            if previous is not None and previous != stream:
                errors.append({"code": "conflicting_duplicate_stream_id", "stream_id": stream["stream_id"]})
            else:
                streams_by_id[stream["stream_id"]] = stream
    return sorted(streams_by_id.values(), key=lambda item: (item["node_id"], item["connection_id"], item["stream_id"])), source_records, errors


def _index_candidate_connections(root: ET.Element) -> dict[tuple[str, str], list[dict[str, Any]]]:
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for connection in root.findall("connection"):
        controller = connection.attrib.get("tl")
        link_index = connection.attrib.get("linkIndex")
        if not controller or link_index is None:
            continue
        row = {
            "from": connection.attrib.get("from", ""),
            "to": connection.attrib.get("to", ""),
            "fromLane": int(connection.attrib.get("fromLane", "0")),
            "toLane": int(connection.attrib.get("toLane", "0")),
            "linkIndex": int(link_index),
        }
        indexed.setdefault((controller, link_index), []).append(row)
    return indexed


def _official_movement_physical_key(movement: Mapping[str, Any]) -> tuple[str, int, str, int]:
    return (
        str(movement["from_edge"]),
        int(movement["from_lane"]),
        str(movement["to_edge"]),
        int(movement["to_lane"]),
    )


def _candidate_connection_physical_key(row: Mapping[str, Any]) -> tuple[str, int, str, int]:
    return (str(row["from"]), int(row["fromLane"]), str(row["to"]), int(row["toLane"]))


def _physical_key_row(key: tuple[str, int, str, int]) -> dict[str, Any]:
    return {"from": key[0], "fromLane": key[1], "to": key[2], "toLane": key[3]}


def _physical_key_rows(keys: set[tuple[str, int, str, int]]) -> list[dict[str, Any]]:
    return [_physical_key_row(key) for key in sorted(keys)]


def _identifier_sort_key(value: object) -> tuple[int, int, str]:
    text = str(value)
    try:
        return (0, int(text), text)
    except ValueError:
        return (1, 0, text)


def _candidate_physical_multiplicities(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_physical_key: dict[tuple[str, int, str, int], list[Mapping[str, Any]]] = {}
    for row in rows:
        by_physical_key.setdefault(_candidate_connection_physical_key(row), []).append(row)
    return [
        {
            **_physical_key_row(key),
            "multiplicity": len(matches),
            "link_indices": sorted({int(match["linkIndex"]) for match in matches}),
        }
        for key, matches in sorted(by_physical_key.items())
        if len(matches) > 1
    ]


def _movement_mismatches(stream: Mapping[str, Any], movement: Mapping[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for key in ("ingress_lane_id", "egress_lane_id"):
        if str(stream[key]) != str(movement[key]):
            mismatches.append(key)
    groups = {str(value) for value in [*movement.get("primary_motor_groups", []), *movement.get("secondary_motor_groups", [])]}
    if stream["signal_group"] not in groups:
        mismatches.append("signal_group")
    if stream.get("layer_name") not in {"", "primary_signal"}:
        mismatches.append("layer_name")
    return mismatches
