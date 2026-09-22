from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Mapping, Sequence

from .candidate_contracts import file_sha256
from .hamburg_named_signal_binding import (
    HamburgSignalBindingError,
    _load_compound_tls_manifest,
)


def build_hamburg_compound_movement_smoke_binding(
    compound_tls_manifest: Path,
) -> dict[str, Any]:
    """Adapt the hash-bound official movement paths to the existing SUMO smoke runner."""

    payload = _read_json(compound_tls_manifest)
    _load_compound_tls_manifest(Path(compound_tls_manifest).resolve(strict=True))
    movements = payload["tls_derivation"]["movements"]
    records = []
    for movement in movements:
        lane_ids = tuple(map(str, movement["path_lane_ids"]))
        if len(lane_ids) < 2:
            raise ValueError("official movement path must contain at least two lanes")
        edge_ids = _collapse_adjacent(_split_lane_id(lane_id)[0] for lane_id in lane_ids)
        records.append(
            {
                "stable_movement_id": _movement_id(movement),
                "edge_ids": list(edge_ids),
                "from_lane_index": _split_lane_id(lane_ids[0])[1],
                "to_lane_index": _split_lane_id(lane_ids[-1])[1],
                "controller_binding_status": "pass",
            }
        )
    return {
        "binding_status": "pass",
        "binding_id": f"{payload['tls_derivation']['plan_id']}:movement-smoke",
        "candidate_plan_id": payload["tls_derivation"]["plan_id"],
        "topology_hypothesis": "official_compound_multi_owner_tls",
        "movement_records": records,
    }


def audit_hamburg_compound_tls_acceptance(
    *,
    source_net_file: Path,
    candidate_net_file: Path,
    compound_tls_manifest: Path,
    expected_source_sha256: str,
    movement_smoke_report: Path | None,
    expected_movement_count_by_node: Mapping[str, int] | None = None,
    expected_unique_physical_link_count: int = 14,
    expected_controller_ids: Sequence[str] = ("HH_2349", "HH_2394"),
) -> dict[str, Any]:
    """Fail closed on duplicate series controls or an unbound movement smoke.

    Same-group official movements may share one physical stop-line connection;
    different groups may not.  The source and every runtime result are bound to
    exact hashes, while the candidate is only read.
    """

    expected_counts = dict(expected_movement_count_by_node or {"2349": 8, "2394": 8})
    source = Path(source_net_file).resolve(strict=True)
    candidate = Path(candidate_net_file).resolve(strict=True)
    manifest_path = Path(compound_tls_manifest).resolve(strict=True)
    source_before = file_sha256(source)
    candidate_sha256 = file_sha256(candidate)
    errors: list[str] = []

    try:
        payload = _read_json(manifest_path)
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
        payload = {}
        errors.append(f"compound_manifest:{exc}")
    try:
        compound = _load_compound_tls_manifest(manifest_path)
    except (HamburgSignalBindingError, KeyError, TypeError, ValueError) as exc:
        compound = {"movements": {}, "nodes": {}}
        errors.append(f"compound_manifest:{exc}")

    derivation = payload.get("tls_derivation", {})
    movements = derivation.get("movements", []) if isinstance(derivation, Mapping) else []
    movement_counts = Counter(str(row.get("official_node_id", "")) for row in movements)
    movement_ids = [_movement_id(row) for row in movements if isinstance(row, Mapping)]
    selected_owner: dict[tuple[str, int, str, int], tuple[str, str]] = {}
    selected_keys: set[tuple[str, int, str, int]] = set()
    shared_keys: dict[tuple[str, int, str, int], list[str]] = defaultdict(list)
    movement_rows: dict[str, Mapping[str, Any]] = {}
    for row in movements:
        if not isinstance(row, Mapping):
            errors.append("movement_not_object")
            continue
        movement_id = _movement_id(row)
        movement_rows[movement_id] = row
        target = compound["movements"].get(
            (str(row.get("official_node_id", "")), str(row.get("connection_id", "")))
        )
        if target is None or len(target["physical_keys"]) != 1:
            errors.append(f"movement_selection:{movement_id}")
            continue
        physical_key = next(iter(target["physical_keys"]))
        owner = (str(row["official_node_id"]), str(row["signal_group"]))
        previous = selected_owner.setdefault(physical_key, owner)
        if previous != owner:
            errors.append(f"cross_group_physical_share:{physical_key}")
        selected_keys.add(physical_key)
        shared_keys[physical_key].append(movement_id)

    root = ET.parse(candidate).getroot()
    lane_index = _lane_index(root)
    candidate_connections = _connection_index(root)
    controller_ids = tuple(map(str, expected_controller_ids))
    official_candidate_keys = {
        key
        for key, rows in candidate_connections.items()
        if any(row.attrib.get("tl", "") in controller_ids for row in rows)
    }
    assignment_errors = []
    series_errors = []
    for movement_id, row in movement_rows.items():
        target = compound["movements"].get(
            (str(row.get("official_node_id", "")), str(row.get("connection_id", "")))
        )
        if target is None or len(target["physical_keys"]) != 1:
            continue
        selected = next(iter(target["physical_keys"]))
        actual_rows = candidate_connections.get(selected, [])
        expected_assignment = (str(target["controller_id"]), str(target["link_index"]))
        actual_assignments = [
            (item.attrib.get("tl", ""), item.attrib.get("linkIndex", ""))
            for item in actual_rows
        ]
        if actual_assignments != [expected_assignment]:
            assignment_errors.append(
                f"{movement_id}:expected={expected_assignment}:actual={actual_assignments}"
            )
        try:
            path_keys = _path_connection_keys(row["path_lane_ids"], lane_index)
        except (KeyError, TypeError, ValueError) as exc:
            series_errors.append(f"{movement_id}:invalid_path:{exc}")
            continue
        controlled_path_keys = [
            key
            for key in path_keys
            if any(item.attrib.get("tl", "") for item in candidate_connections.get(key, ()))
        ]
        if controlled_path_keys != [selected]:
            series_errors.append(
                f"{movement_id}:selected={selected}:controlled_path={controlled_path_keys}"
            )

    plan_keys = {
        _physical_key(row)
        for group in payload.get("network_rebuild", {}).get("plan", {}).get("groups", [])
        for row in group.get("physical_links", [])
    }
    logic_counts = Counter(
        logic.attrib.get("id", "") for logic in root.findall("tlLogic")
    )
    source_after = file_sha256(source)
    source_record = payload.get("source", {})
    rebuild = payload.get("network_rebuild", {})
    artifact_network = payload.get("artifacts", {}).get("network", {})
    checks = {
        "compound_manifest": "pass" if not any(e.startswith("compound_manifest:") for e in errors) else "blocked",
        "source_immutable": "pass"
        if source_before == source_after == expected_source_sha256
        and source_record.get("sha256") == expected_source_sha256
        and rebuild.get("source_sha256_before") == expected_source_sha256
        and rebuild.get("source_sha256_after") == expected_source_sha256
        and rebuild.get("source_unchanged") is True
        else "blocked",
        "candidate_hash_binding": "pass"
        if artifact_network.get("sha256") == candidate_sha256
        else "blocked",
        "movement_inventory": "pass"
        if movement_counts == Counter(expected_counts)
        and len(movement_ids) == len(set(movement_ids)) == sum(expected_counts.values())
        else "blocked",
        "one_stopline_per_movement": "pass"
        if not any(error.startswith("movement_selection:") for error in errors)
        else "blocked",
        "same_group_sharing_only": "pass"
        if not any(error.startswith("cross_group_physical_share:") for error in errors)
        else "blocked",
        "unique_physical_stoplines": "pass"
        if len(selected_keys) == expected_unique_physical_link_count
        else "blocked",
        "plan_selected_physical_parity": "pass" if plan_keys == selected_keys else "blocked",
        "candidate_selected_physical_parity": "pass"
        if official_candidate_keys == selected_keys and not assignment_errors
        else "blocked",
        "official_controller_inventory": "pass"
        if set(compound["nodes"]) == set(expected_counts)
        and {node["controller_id"] for node in compound["nodes"].values()} == set(controller_ids)
        and all(logic_counts[controller_id] == 1 for controller_id in controller_ids)
        else "blocked",
        "no_duplicate_series_controls": "pass" if not series_errors else "blocked",
        "movement_smoke": _movement_smoke_status(
            movement_smoke_report,
            candidate_sha256=candidate_sha256,
            expected_movement_ids=set(movement_ids),
        ),
    }
    return {
        "schema": "torii.hamburg-compound-tls-acceptance/v1",
        "status": "pass" if all(value == "pass" for value in checks.values()) else "blocked",
        "checks": checks,
        "source": {"path": str(source), "sha256": source_before},
        "candidate": {"path": str(candidate), "sha256": candidate_sha256},
        "movement_count_by_node": dict(sorted(movement_counts.items())),
        "unique_physical_stopline_count": len(selected_keys),
        "shared_same_group_stoplines": [
            {"physical_key": list(key), "movement_ids": ids}
            for key, ids in sorted(shared_keys.items())
            if len(ids) > 1
        ],
        "assignment_errors": assignment_errors,
        "series_control_errors": series_errors,
        "errors": errors,
    }


def _movement_smoke_status(
    report_file: Path | None,
    *,
    candidate_sha256: str,
    expected_movement_ids: set[str],
) -> str:
    if report_file is None:
        return "blocked"
    report = _read_json(Path(report_file).resolve(strict=True))
    checks = report.get("checks", {})
    return (
        "pass"
        if report.get("status") == "pass"
        and report.get("net_sha256") == candidate_sha256
        and set(map(str, report.get("stable_movement_ids", ()))) == expected_movement_ids
        and int(report.get("movement_count", -1)) == len(expected_movement_ids)
        and checks.get("all_expected_vehicles_arrived") is True
        and checks.get("source_immutable") is True
        else "blocked"
    )


def _connection_index(root: ET.Element) -> dict[tuple[str, int, str, int], list[ET.Element]]:
    result: dict[tuple[str, int, str, int], list[ET.Element]] = defaultdict(list)
    for row in root.findall("connection"):
        if row.attrib.get("from", "").startswith(":") or row.attrib.get("to", "").startswith(":"):
            continue
        try:
            result[
                (
                    row.attrib["from"],
                    int(row.attrib["fromLane"]),
                    row.attrib["to"],
                    int(row.attrib["toLane"]),
                )
            ].append(row)
        except (KeyError, ValueError):
            continue
    return result


def _lane_index(root: ET.Element) -> dict[str, tuple[str, int]]:
    return {
        lane.attrib["id"]: (edge.attrib["id"], int(lane.attrib["index"]))
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib["id"].startswith(":")
        for lane in edge.findall("lane")
        if lane.attrib.get("id") and lane.attrib.get("index", "").isdigit()
    }


def _path_connection_keys(
    lane_ids: object,
    lane_index: Mapping[str, tuple[str, int]],
) -> list[tuple[str, int, str, int]]:
    if not isinstance(lane_ids, list) or len(lane_ids) < 2:
        raise ValueError("path_lane_ids must contain at least two lanes")
    pairs = []
    for left, right in zip(lane_ids, lane_ids[1:]):
        left_edge, left_index = lane_index[str(left)]
        right_edge, right_index = lane_index[str(right)]
        pairs.append((left_edge, left_index, right_edge, right_index))
    return pairs


def _physical_key(row: Mapping[str, Any]) -> tuple[str, int, str, int]:
    return (
        str(row["from_edge"]),
        int(row["from_lane"]),
        str(row["to_edge"]),
        int(row["to_lane"]),
    )


def _movement_id(row: Mapping[str, Any]) -> str:
    return f"{row['official_node_id']}:{row['connection_id']}"


def _split_lane_id(lane_id: str) -> tuple[str, int]:
    edge_id, lane_index = str(lane_id).rsplit("_", 1)
    return edge_id, int(lane_index)


def _collapse_adjacent(values: Iterable[str]) -> tuple[str, ...]:
    result = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return tuple(result)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload
