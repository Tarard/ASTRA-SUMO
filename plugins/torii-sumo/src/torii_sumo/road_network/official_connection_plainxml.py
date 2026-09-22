"""Materialize only machine-authorized official lane continuations.

The HH-SIB PlainXML builder intentionally leaves lane connections unresolved.
``official_lane_transition`` is the next evidence stage: it can authorize a
unique continuation across a documented lane-profile cut.  This module is the
small compiler boundary between that evidence and SUMO PlainXML.  It never
fills an abstained direction and never invents a feed into an added pocket.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256


OFFICIAL_CONNECTION_PLAINXML_SCHEMA = (
    "torii.hamburg-official-map-connection-plainxml/v1"
)
_TRANSITION_SCHEMA = "torii.hamburg-official-map-hh-sib-lane-transition-graph/v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STATION_TOLERANCE_M = 1e-3


class OfficialConnectionPlainXmlError(ValueError):
    """Raised when official transition evidence cannot be compiled safely."""


def materialize_hamburg_official_connection_plainxml(
    *,
    lane_transition_graph_file: Path,
    edges_file: Path,
    output_dir: Path,
    plainxml_manifest_file: Path | None = None,
    expected_transition_graph_sha256: str | None = None,
    expected_edges_sha256: str | None = None,
    prefix: str = "official_map_connections",
) -> dict[str, Any]:
    """Write a hash-bound SUMO connection file from authorized transitions.

    The output is a *connection-stage input*, not a complete network.  Every
    possible lane pair across each authorized edge boundary is either an
    explicit official continuation or an explicit ``delete`` directive.  This
    prevents netconvert from silently retaining its guessed lane mapping at
    that boundary.  Directions for which the official graph abstained are
    omitted and keep the stage blocked.
    """

    _validate_prefix(prefix)
    graph_path = Path(lane_transition_graph_file).expanduser().resolve(strict=True)
    edges_path = Path(edges_file).expanduser().resolve(strict=True)
    manifest_path = (
        Path(plainxml_manifest_file).expanduser().resolve(strict=True)
        if plainxml_manifest_file is not None
        else None
    )
    graph_sha256 = _verify_expected_hash(
        graph_path, expected_transition_graph_sha256, "transition graph"
    )
    edges_sha256 = _verify_expected_hash(edges_path, expected_edges_sha256, "edges")

    graph = _read_json_object(graph_path, "lane transition graph")
    if graph.get("schema") != _TRANSITION_SCHEMA:
        raise OfficialConnectionPlainXmlError(
            f"lane transition graph schema must be {_TRANSITION_SCHEMA}"
        )
    graph_inputs = graph.get("inputs")
    if not isinstance(graph_inputs, Mapping):
        raise OfficialConnectionPlainXmlError("lane transition graph inputs are required")
    _verify_graph_input_hash(graph_inputs.get("edges"), edges_sha256, "edges")
    if manifest_path is not None:
        _verify_plainxml_manifest(manifest_path, edges_sha256, graph_inputs)

    edges = _read_edges(edges_path)
    transitions = graph.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise OfficialConnectionPlainXmlError("lane transition graph transitions are required")

    authorized: list[dict[str, Any]] = []
    abstained: list[dict[str, Any]] = []
    for transition in transitions:
        if not isinstance(transition, Mapping):
            raise OfficialConnectionPlainXmlError("lane transition entries must be objects")
        if transition.get("status") == "pass" and transition.get("lane_transition_authorized") is True:
            authorized.append(_compile_transition(transition, edges))
        else:
            abstained.append(
                {
                    "transition_id": str(transition.get("transition_id", "")),
                    "station_direction": str(transition.get("station_direction", "")),
                    "reason": str(transition.get("reason", "not_authorized")),
                }
            )
    if not authorized:
        raise OfficialConnectionPlainXmlError(
            "official lane transition graph contains no authorized continuation"
        )

    directives = _build_directives(authorized)
    destination = Path(output_dir).expanduser().resolve()
    connection_file = destination / f"{prefix}.con.xml"
    output_manifest = destination / f"{prefix}.manifest.json"
    collisions = [path for path in (connection_file, output_manifest) if path.exists()]
    if collisions:
        raise OfficialConnectionPlainXmlError(
            "connection-stage output artifacts already exist: "
            + ", ".join(str(path) for path in collisions)
        )

    destination.mkdir(parents=True, exist_ok=True)
    _write_connections(connection_file, directives)
    graph_status = str(graph.get("status", "blocked"))
    stage_status = "pass" if graph_status == "pass" and not abstained else "blocked"
    manifest: dict[str, Any] = {
        "schema": OFFICIAL_CONNECTION_PLAINXML_SCHEMA,
        "status": stage_status,
        "claim_status": "official_lane_continuation_connection_stage_only",
        "automatic_promotion_gate": "pass" if stage_status == "pass" else "blocked",
        "human_action_required": False,
        "network_materialization_performed": False,
        "inputs": {
            "lane_transition_graph": {
                "path": str(graph_path),
                "sha256": graph_sha256,
                "schema": graph["schema"],
                "graph_id": graph.get("graph_id"),
            },
            "edges": {"path": str(edges_path), "sha256": edges_sha256},
            "plainxml_manifest": (
                {"path": str(manifest_path), "sha256": file_sha256(manifest_path)}
                if manifest_path is not None
                else None
            ),
        },
        "counts": {
            "transition_count": len(transitions),
            "authorized_transition_count": len(authorized),
            "abstained_transition_count": len(abstained),
            "connection_count": len(directives["connections"]),
            "delete_count": len(directives["deletes"]),
        },
        "authorized_transitions": authorized,
        "abstained_transitions": abstained,
        "connection_policy": {
            "explicit_lane_pair_deletes": True,
            "added_lane_upstream_feed_connections": [],
            "invented_connection_to_added_lane": False,
            "omitted_directions_remain_blocked": True,
        },
        "artifacts": {
            "connections": {
                "path": connection_file.name,
                "sha256": file_sha256(connection_file),
                "bytes": connection_file.stat().st_size,
            }
        },
        "claim_boundary": (
            "Only exact official continuation lane pairs at authorized HH-SIB profile cuts are emitted. "
            "The file is not a complete SUMO network: abstained directions, junction movements, priority, "
            "stop lines, signal ownership, and timing remain unresolved."
        ),
    }
    write_json_atomic(output_manifest, manifest, sort_keys=True)
    return {
        **manifest,
        "output_dir": str(destination),
        "connection_file": str(connection_file),
        "manifest_file": str(output_manifest),
    }


def _compile_transition(transition: Mapping[str, Any], edges: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    transition_id = str(transition.get("transition_id", ""))
    if not transition_id:
        raise OfficialConnectionPlainXmlError("authorized transition has no transition_id")
    direction = str(transition.get("station_direction", ""))
    if direction not in {"with_stationing", "against_stationing"}:
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} has invalid station_direction"
        )
    official_link_id = str(transition.get("official_link_id", ""))
    cut = _finite_number(
        transition.get("cut_evidence", {}).get("authorized_model_cut_station_m"),
        f"{transition_id}.cut_evidence.authorized_model_cut_station_m",
    )
    continuation = transition.get("continuation_edges")
    compiler = transition.get("compiler_directive")
    if not isinstance(continuation, list) or not continuation:
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} has no continuation_edges"
        )
    if not isinstance(compiler, Mapping):
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} has no compiler_directive"
        )
    if compiler.get("added_lane_upstream_feed_connections") != []:
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} permits an upstream feed into an added lane"
        )
    added_lane_indices = [
        _lane_index(item, "downstream_sumo_lane_index", transition_id)
        for item in transition.get("added_lanes", [])
        if isinstance(item, Mapping)
    ]
    upstream, downstream = _find_boundary_edges(
        edges, official_link_id, direction, cut, transition_id
    )
    pairs: list[dict[str, int | str]] = []
    for item in continuation:
        if not isinstance(item, Mapping):
            raise OfficialConnectionPlainXmlError(
                f"authorized transition {transition_id!r} has an invalid continuation"
            )
        if item.get("authorization") != "exact_official_overlap_continuation_only":
            raise OfficialConnectionPlainXmlError(
                f"authorized transition {transition_id!r} has an unverified continuation"
            )
        from_lane = _lane_index(item, "upstream_sumo_lane_index", transition_id)
        to_lane = _lane_index(item, "downstream_sumo_lane_index", transition_id)
        if from_lane >= upstream["num_lanes"] or to_lane >= downstream["num_lanes"]:
            raise OfficialConnectionPlainXmlError(
                f"authorized transition {transition_id!r} references a lane outside PlainXML edge widths"
            )
        if to_lane in added_lane_indices:
            raise OfficialConnectionPlainXmlError(
                f"authorized transition {transition_id!r} feeds an added downstream lane"
            )
        pairs.append(
            {
                "from": upstream["id"],
                "to": downstream["id"],
                "fromLane": from_lane,
                "toLane": to_lane,
            }
        )
    keys = [(p["fromLane"], p["toLane"]) for p in pairs]
    if len(set(keys)) != len(keys):
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} contains duplicate lane pairs"
        )
    return {
        "transition_id": transition_id,
        "station_direction": direction,
        "official_link_id": official_link_id,
        "cut_station_m": cut,
        "upstream_edge": upstream,
        "downstream_edge": downstream,
        "continuation_pairs": pairs,
        "added_lane_indices": added_lane_indices,
    }


def _find_boundary_edges(
    edges: Mapping[str, Mapping[str, Any]],
    official_link_id: str,
    direction: str,
    cut: float,
    transition_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    candidates = [
        edge
        for edge in edges.values()
        if edge["orig_id"] == official_link_id and edge["station_direction"] == direction
    ]
    if direction == "with_stationing":
        upstream = [edge for edge in candidates if _close(edge["station_to"], cut) and edge["station_from"] < cut]
        downstream = [edge for edge in candidates if _close(edge["station_from"], cut) and edge["station_to"] > cut]
    else:
        upstream = [edge for edge in candidates if _close(edge["station_from"], cut) and edge["station_to"] > cut]
        downstream = [edge for edge in candidates if _close(edge["station_to"], cut) and edge["station_from"] < cut]
    if len(upstream) != 1 or len(downstream) != 1:
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} does not identify one upstream and one downstream edge"
        )
    before, after = upstream[0], downstream[0]
    if before["to_node"] != after["from_node"]:
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} boundary edges do not share a SUMO node"
        )
    return before, after


def _build_directives(authorized: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    deletes: set[tuple[str, str, int, int]] = set()
    connections: set[tuple[str, str, int, int]] = set()
    for transition in authorized:
        upstream = transition["upstream_edge"]
        downstream = transition["downstream_edge"]
        allowed = {
            (int(pair["fromLane"]), int(pair["toLane"]))
            for pair in transition["continuation_pairs"]
        }
        for from_lane in range(int(upstream["num_lanes"])):
            for to_lane in range(int(downstream["num_lanes"])):
                key = (upstream["id"], downstream["id"], from_lane, to_lane)
                if (from_lane, to_lane) in allowed:
                    connections.add(key)
                else:
                    deletes.add(key)
    return {
        "deletes": [
            {"from": f, "to": t, "fromLane": fl, "toLane": tl}
            for f, t, fl, tl in sorted(deletes)
        ],
        "connections": [
            {"from": f, "to": t, "fromLane": fl, "toLane": tl}
            for f, t, fl, tl in sorted(connections)
        ],
    }


def _write_connections(path: Path, directives: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    root = ET.Element(
        "connections",
        {
            "version": "1.20",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/connections_file.xsd",
        },
    )
    for tag in ("delete", "connection"):
        for row in directives[tag + "s"]:
            ET.SubElement(
                root,
                tag,
                {key: str(value) for key, value in row.items()},
            )
    ET.indent(root, space="    ")
    payload = ET.tostring(root, encoding="unicode")
    write_text_atomic(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + payload + "\n")


def _read_edges(path: Path) -> dict[str, dict[str, Any]]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise OfficialConnectionPlainXmlError(f"invalid PlainXML edges file: {path}") from exc
    if root.tag != "edges":
        raise OfficialConnectionPlainXmlError("PlainXML edges root must be <edges>")
    result: dict[str, dict[str, Any]] = {}
    for element in root.findall("edge"):
        edge_id = str(element.get("id", ""))
        if not edge_id or edge_id in result:
            raise OfficialConnectionPlainXmlError("PlainXML edges contain missing or duplicate ids")
        params = {
            str(param.get("key")): str(param.get("value"))
            for param in element.findall("param")
            if param.get("key") is not None and param.get("value") is not None
        }
        try:
            row = {
                "id": edge_id,
                "from_node": str(element.attrib["from"]),
                "to_node": str(element.attrib["to"]),
                "num_lanes": int(element.attrib["numLanes"]),
                "orig_id": params["origId"],
                "station_direction": params["torii:station_direction"],
                "station_from": float(params["torii:station_from_m"]),
                "station_to": float(params["torii:station_to_m"]),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise OfficialConnectionPlainXmlError(
                f"PlainXML edge {edge_id!r} lacks official station metadata"
            ) from exc
        if row["num_lanes"] <= 0:
            raise OfficialConnectionPlainXmlError(f"PlainXML edge {edge_id!r} has no lanes")
        result[edge_id] = row
    if not result:
        raise OfficialConnectionPlainXmlError("PlainXML edges file is empty")
    return result


def _verify_plainxml_manifest(
    path: Path, edges_sha256: str, graph_inputs: Mapping[str, Any]
) -> None:
    manifest = _read_json_object(path, "PlainXML manifest")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or not isinstance(artifacts.get("edges"), Mapping):
        raise OfficialConnectionPlainXmlError("PlainXML manifest does not describe its edges artifact")
    if str(artifacts["edges"].get("sha256", "")).lower() != edges_sha256:
        raise OfficialConnectionPlainXmlError("PlainXML manifest edges hash does not match the supplied file")
    graph_manifest = graph_inputs.get("plainxml_manifest")
    if isinstance(graph_manifest, Mapping):
        expected = str(graph_manifest.get("sha256", "")).lower()
        if expected and expected != file_sha256(path):
            raise OfficialConnectionPlainXmlError("transition graph PlainXML manifest hash does not match")


def _verify_graph_input_hash(value: Any, actual: str, label: str) -> None:
    if not isinstance(value, Mapping):
        raise OfficialConnectionPlainXmlError(f"transition graph {label} input identity is required")
    expected = str(value.get("sha256", "")).lower()
    if not _SHA256_PATTERN.fullmatch(expected) or expected != actual:
        raise OfficialConnectionPlainXmlError(f"transition graph {label} hash does not match supplied file")


def _verify_expected_hash(path: Path, expected: str | None, label: str) -> str:
    actual = file_sha256(path)
    if expected is not None:
        expected_value = str(expected).lower()
        if not _SHA256_PATTERN.fullmatch(expected_value) or expected_value != actual:
            raise OfficialConnectionPlainXmlError(f"{label} SHA-256 does not match supplied file")
    return actual


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfficialConnectionPlainXmlError(f"invalid {label}: {path}") from exc
    if not isinstance(payload, dict):
        raise OfficialConnectionPlainXmlError(f"{label} must be a JSON object")
    return payload


def _lane_index(item: Mapping[str, Any], key: str, transition_id: str) -> int:
    try:
        value = int(item[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialConnectionPlainXmlError(
            f"authorized transition {transition_id!r} has invalid {key}"
        ) from exc
    if value < 0:
        raise OfficialConnectionPlainXmlError(f"authorized transition {transition_id!r} has negative lane index")
    return value


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise OfficialConnectionPlainXmlError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise OfficialConnectionPlainXmlError(f"{field} must be finite")
    return result


def _close(first: float, second: float) -> bool:
    return abs(float(first) - float(second)) <= _STATION_TOLERANCE_M


def _validate_prefix(prefix: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", prefix):
        raise OfficialConnectionPlainXmlError("prefix must be a safe 1-96 character artifact stem")


__all__ = [
    "OFFICIAL_CONNECTION_PLAINXML_SCHEMA",
    "OfficialConnectionPlainXmlError",
    "materialize_hamburg_official_connection_plainxml",
]
