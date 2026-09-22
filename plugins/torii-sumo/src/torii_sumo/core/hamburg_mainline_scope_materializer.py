"""Materialize the bounded Am Sandtorkai mainline corridor.

The imported OSM network contains the three requested signalized locations, but
it also contains the auxiliary Baumwall/Niederbaumbruecke branch at 0228 and a
pair of sub-junctions at 2394 whose generated faces overlap.  This module keeps
the explicit 0228 -> 2421 -> 2394 backbone, the short signal approaches shown
in the corridor screenshot, and a small 0228 boundary stub needed to retain
the official controller link.  It removes the upper-left branch using SUMO's
documented keep-edges/post-load workflow and joins only the proven overlapping
2394 micro-junction pair.

The source network is hash-bound.  The profile is therefore reproducible and
cannot silently select a different OSM import after the official MAP/TLD lane
bindings have been produced.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .detector_demand import read_net
from .sumo_commands import run_sumo_load_audit
from .surface_overlap_audit import audit_sumo_lane_junction_surface_overlaps


HAMBURG_MAINLINE_SCOPE_SCHEMA = "torii.hamburg-sandtorkai-mainline-scope/v1"
HAMBURG_MAINLINE_SCOPE_PROFILE = "hamburg_sandtorkai_mainline_scope_v1"
HAMBURG_ENTRY_SCOPE_PROFILE = "hamburg_sandtorkai_entry_scope_v1"

# This is the geometry-safe/TLS candidate used as the frozen source for the
# current Hamburg package.  Callers still have to pass the hash explicitly.
HAMBURG_V10_SOURCE_SHA256 = "99a056171be0f9327e3ddd204adb27758d6a01ae363b1cef8f93cd330aace4c8"

MAINLINE_ROUTE_EDGE_IDS: tuple[str, ...] = (
    "554713077",
    "60578487#1",
    "60578487#2",
    "60578487#3",
    "554713076#0",
    "554713076#1",
    "554713076#2",
    "194672083",
    "127716467#0",
    "127716467#1",
    "957130878#0",
    "957130878#1",
    "957130878#2",
    "554713081#0",
    "554713081#1",
    "957130880",
    "554713080",
    "30390248#0",
    "30390248#1",
    "30390248#2",
    "30390248#3",
    "30390248#4",
    "554713082#0",
    "554713082#1",
    "554713082#2",
    "554713083#0",
    "554713083#1",
    "4313743#0",
    "4313743#1",
    "562205886",
    "19199901",
    "30598403",
    "297888720",
    "19199492",
    "1231234769#0",
    "1231234769#1",
)

# These are deliberately short boundary/approach edges, not the full branch
# network.  22649708#0 is the 7 m controlled exit at 0228; retaining it keeps
# HH_0228's final MAP link while the longer upper-left branch is removed.
BOUNDARY_EDGE_IDS: tuple[str, ...] = ("60578519", "22649708#0")
SHORT_APPROACH_NAMES: tuple[str, ...] = ("Am Kaiserkai", "Am Sandtorpark")
# The entry-scope profile keeps the west/east side roads needed to inject
# vehicles into the corridor while still excluding the oversized Baumwall
# branch.  These names are derived from the current OSM/Geoportal cross-check;
# signal binding remains an independent official MAP/OCIT stage.
ENTRY_APPROACH_NAMES: tuple[str, ...] = (
    "Am Sandtorpark",
    "Großer Grasbrook",
    "Singapurstraße",
)
MAINLINE_NAMES: tuple[str, ...] = ("Am Sandtorkai",)
EXCLUDED_BRANCH_NAMES: tuple[str, ...] = ("Baumwall",)
EXCLUDED_BRANCH_EDGE_IDS: tuple[str, ...] = (
    "22649708#1",
    "74547371#0",
    "74547371#1",
    "234561088#0",
    "234561088#1",
)
JOIN_GROUPS: tuple[tuple[str, ...], ...] = (("5353176677", "748827192"),)
# Großer Grasbrook crosses the two separated Am Sandtorkai carriageways.  The
# source OSM graph expands each carriageway into two nearby junction nodes;
# joining each opposing pair keeps two physical conflict cores and removes the
# rendered polygon overlap.  Joining all four would erase the divided-road
# structure, so it is intentionally not used.
ENTRY_JOIN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("5353176677", "748827192"),
    ("25737304", "759714733"),
    ("739654528", "759714704"),
)
# The 0.2 m edge between the joined 2394 micro-junctions is intentionally
# consumed by the join.  It must not be treated as a missing retained road.
JOIN_CONSUMED_EDGE_IDS: tuple[str, ...] = ("60578487#0",)
# The two Großer Grasbrook connectors that terminate at the joined opposing
# carriageway pairs are also consumed by netconvert's explicit join.
ENTRY_JOIN_CONSUMED_EDGE_IDS: tuple[str, ...] = (
    "60578487#0",
    "59626578",
    "61649650",
)
CONTROLLER_IDS: tuple[str, ...] = ("HH_0228", "HH_2421", "HH_2394")


class HamburgMainlineScopeError(ValueError):
    """Raised when the hash-bound mainline profile cannot be materialized."""


def select_hamburg_mainline_scope_edges(
    source_net_file: Path,
    *,
    include_short_approaches: bool = True,
    retained_approach_names: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Return the explicit mainline edge selection for a frozen source net."""

    try:
        root = ET.parse(source_net_file).getroot()
    except (OSError, ET.ParseError) as exc:
        raise HamburgMainlineScopeError(f"cannot read source network: {exc}") from exc
    external_edges = {
        edge.attrib.get("id", ""): edge
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
        and not edge.attrib.get("function")
    }
    required = set(MAINLINE_ROUTE_EDGE_IDS) | set(BOUNDARY_EDGE_IDS)
    missing = sorted(required - set(external_edges))
    if missing:
        raise HamburgMainlineScopeError(
            "source network is not the accepted Hamburg geometry candidate; missing backbone edges: "
            + ", ".join(missing)
        )

    selected = set(required)
    approach_names = tuple(
        retained_approach_names
        if retained_approach_names is not None
        else SHORT_APPROACH_NAMES
    )
    for edge_id, edge in external_edges.items():
        name = _edge_name(edge)
        if name in MAINLINE_NAMES:
            selected.add(edge_id)
        if include_short_approaches and name in approach_names:
            selected.add(edge_id)
    return tuple(sorted(selected))


def materialize_hamburg_sandtorkai_mainline_scope_candidate(
    *,
    source_net_file: Path,
    expected_source_sha256: str,
    output_dir: Path,
    profile: str = HAMBURG_MAINLINE_SCOPE_PROFILE,
    include_short_approaches: bool = True,
    retained_approach_names: Sequence[str] | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, object]:
    """Build the review candidate without mutating the source network."""

    source = Path(source_net_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    profile_approaches = {
        HAMBURG_MAINLINE_SCOPE_PROFILE: SHORT_APPROACH_NAMES,
        HAMBURG_ENTRY_SCOPE_PROFILE: ENTRY_APPROACH_NAMES,
    }
    profile_join_groups = {
        HAMBURG_MAINLINE_SCOPE_PROFILE: JOIN_GROUPS,
        HAMBURG_ENTRY_SCOPE_PROFILE: ENTRY_JOIN_GROUPS,
    }
    profile_join_consumed_edges = {
        HAMBURG_MAINLINE_SCOPE_PROFILE: JOIN_CONSUMED_EDGE_IDS,
        HAMBURG_ENTRY_SCOPE_PROFILE: ENTRY_JOIN_CONSUMED_EDGE_IDS,
    }
    if profile not in profile_approaches:
        raise HamburgMainlineScopeError(f"unsupported mainline scope profile: {profile}")
    if not expected_source_sha256 or file_sha256(source).lower() != expected_source_sha256.lower():
        raise HamburgMainlineScopeError("source network SHA-256 mismatch")
    if destination == source.parent:
        raise HamburgMainlineScopeError("output_dir must not be the source directory")
    if timeout_seconds <= 0:
        raise HamburgMainlineScopeError("timeout_seconds must be positive")

    selected_approach_names = tuple(
        retained_approach_names
        if retained_approach_names is not None
        else profile_approaches[profile]
    )
    selected_join_groups = profile_join_groups[profile]
    selected_join_consumed_edges = profile_join_consumed_edges[profile]
    selected = select_hamburg_mainline_scope_edges(
        source,
        include_short_approaches=include_short_approaches,
        retained_approach_names=selected_approach_names,
    )
    selected_set = set(selected)
    keep_edges_file = destination / "hamburg_sandtorkai_mainline_scope.keep_edges.txt"
    write_text_atomic(keep_edges_file, "\n".join(selected) + "\n")
    node_patch_file = destination / "hamburg_sandtorkai_mainline_scope.nod.xml"
    _write_join_patch(node_patch_file, join_groups=selected_join_groups)
    output_net = destination / "hamburg_sandtorkai_mainline_scope.net.xml"
    joined_file = destination / "hamburg_sandtorkai_mainline_scope.joined_junctions.xml"

    command = [
        str(netconvert_binary),
        "--sumo-net-file",
        str(source),
        "--node-files",
        str(node_patch_file),
        "--keep-edges.input-file",
        str(keep_edges_file),
        "--keep-edges.postload",
        "--junctions.join-output",
        str(joined_file),
        "--geometry.check-overlap",
        "0",
        "--output-file",
        str(output_net),
    ]
    attempts: list[list[str]] = [command]
    result = _result_dict(command_runner(command, cwd=destination, timeout_seconds=timeout_seconds))
    fallback_used = False
    if result.get("status") != "pass" or not output_net.is_file():
        fallback = [item for item in command if item != "--keep-edges.postload"]
        attempts.append(fallback)
        result = _result_dict(command_runner(fallback, cwd=destination, timeout_seconds=timeout_seconds))
        fallback_used = result.get("status") == "pass" and output_net.is_file()
    if result.get("status") != "pass" or not output_net.is_file():
        raise HamburgMainlineScopeError(
            "netconvert did not create the mainline candidate: "
            + str(result.get("stderr") or result.get("error") or result)
        )
    _canonicalize_net_file(output_net)

    output_root = ET.parse(output_net).getroot()
    output_edge_ids = {
        edge.attrib.get("id", "")
        for edge in output_root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
    }
    missing_selected = sorted(selected_set - output_edge_ids)
    unexpected_missing_selected = sorted(
        set(missing_selected) - set(selected_join_consumed_edges)
    )
    missing_excluded = sorted(set(EXCLUDED_BRANCH_EDGE_IDS) & output_edge_ids)
    branch_names_retained = sorted(
        {
            _edge_name(edge)
            for edge in output_root.findall("edge")
            if _edge_name(edge) in EXCLUDED_BRANCH_NAMES
        }
    )
    controllers = sorted(
        element.attrib.get("id", "")
        for element in output_root.findall("tlLogic")
        if element.attrib.get("id")
    )
    controller_link_counts = {
        controller: sum(1 for connection in output_root.findall("connection") if connection.attrib.get("tl") == controller)
        for controller in CONTROLLER_IDS
    }
    edges, connections = read_net(output_net)
    passenger_ids = {edge_id for edge_id, edge in edges.items() if edge.allows_passenger}
    component_count = len(_weak_components(passenger_ids, edges, connections))
    route_gate = _ordered_route_gate(connections, output_edge_ids)
    overlap_report = audit_sumo_lane_junction_surface_overlaps(
        output_net,
        report_file=destination / "surface_overlap" / "mainline_scope_surface_overlap_audit.json",
    )
    load_report = run_sumo_load_audit(
        net_file=output_net,
        output_dir=destination / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    source_hash_after = file_sha256(source)
    gates = {
        "source_unchanged": source_hash_after.lower() == expected_source_sha256.lower(),
        "all_selected_edges_preserved": not unexpected_missing_selected,
        "mainline_route_connections_preserved": route_gate["status"] == "pass",
        "three_official_controllers": controllers == sorted(CONTROLLER_IDS),
        "all_controllers_have_links": all(value > 0 for value in controller_link_counts.values()),
        "upper_left_branch_excluded": not missing_excluded and not branch_names_retained,
        "one_passenger_component": component_count == 1,
        "surface_overlap_clean": overlap_report.get("status") == "pass",
        "sumo_load_pass": load_report.get("status") == "pass",
        "strict_scope_reduction": len(output_edge_ids) < len(_external_edge_ids(source)),
    }
    status = "review_ready" if all(gates.values()) else "blocked"
    manifest_file = destination / "hamburg_sandtorkai_mainline_scope.manifest.json"
    candidate_contract_file = (
        destination / "hamburg_sandtorkai_mainline_scope.candidate_manifest.json"
    )
    candidate_contract = {
        # This compatibility contract lets the already audited MAP/signal/
        # demand stages consume a post-TLS scope candidate without pretending
        # that the TLS materializer itself was rerun.
        "schema_id": "torii.hamburg-sandtorkai-corridor-tls-materializer/v1",
        "status": "review_ready" if status == "review_ready" else "blocked",
        "claim_status": "official-static-topology-candidate",
        "automatic_promotion_gate": "blocked",
        "compatibility_role": (
            "post_tls_entry_scope_candidate"
            if profile == HAMBURG_ENTRY_SCOPE_PROFILE
            else "post_tls_mainline_scope_candidate"
        ),
        "artifacts": {"network": str(output_net)},
        "source": {
            "path": str(source),
            "sha256": source_hash_after,
            "scope_manifest": str(manifest_file),
        },
        "selection": {
            "profile": profile,
            "selected_edge_count": len(selected),
            "output_edge_count": len(output_edge_ids),
            "controllers": list(CONTROLLER_IDS),
        },
    }
    write_json_atomic(candidate_contract_file, candidate_contract, sort_keys=True)
    manifest: dict[str, object] = {
        "schema_id": HAMBURG_MAINLINE_SCOPE_SCHEMA,
        "status": status,
        "claim_status": "mainline-corridor-review-candidate" if status == "review_ready" else "construction-invalid",
        "automatic_promotion_gate": "blocked",
        "profile": profile,
        "source": {
            "path": str(source),
            "sha256_expected": expected_source_sha256,
            "sha256_after": source_hash_after,
            "immutable": gates["source_unchanged"],
        },
        "scope_policy": {
            "mainline_names": list(MAINLINE_NAMES),
            "short_approach_names": list(SHORT_APPROACH_NAMES),
            "retained_approach_names": list(selected_approach_names),
            "include_short_approaches": include_short_approaches,
            "ordered_backbone_edges": list(MAINLINE_ROUTE_EDGE_IDS),
            "boundary_edges": list(BOUNDARY_EDGE_IDS),
            "excluded_branch_names": list(EXCLUDED_BRANCH_NAMES),
            "excluded_branch_edge_ids": list(EXCLUDED_BRANCH_EDGE_IDS),
            "join_groups": [list(group) for group in JOIN_GROUPS],
            "retained_join_groups": [list(group) for group in selected_join_groups],
            "boundary_stub_reason": "retain the official HH_0228 MAP link at the corridor cut boundary",
        },
        "selection": {
            "selected_edge_count": len(selected),
            "output_edge_count": len(output_edge_ids),
            "missing_selected_edges": missing_selected,
            "unexpected_missing_selected_edges": unexpected_missing_selected,
            "join_consumed_edge_ids": list(selected_join_consumed_edges),
            "retained_excluded_branch_names": branch_names_retained,
            "retained_excluded_branch_edge_ids": missing_excluded,
        },
        "controllers": {
            "expected": list(CONTROLLER_IDS),
            "output": controllers,
            "link_counts": controller_link_counts,
        },
        "routeability": route_gate,
        "connectivity": {
            "passenger_edge_count": len(passenger_ids),
            "passenger_component_count": component_count,
        },
        "netconvert": {
            "attempts": attempts,
            "result": result,
            "postload_fallback_used": fallback_used,
        },
        "surface_overlap_audit": overlap_report,
        "sumo_load_audit": load_report,
        "gates": gates,
        "artifacts": {
            "network": str(output_net),
            "keep_edges": str(keep_edges_file),
            "node_patch": str(node_patch_file),
            "joined_junctions": str(joined_file),
            "candidate_manifest": str(candidate_contract_file),
            "manifest": str(manifest_file),
        },
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    manifest["manifest_sha256"] = file_sha256(manifest_file)
    return manifest


def _edge_name(edge: ET.Element) -> str:
    return next(
        (
            parameter.attrib.get("value", "").strip()
            for parameter in edge.findall("param")
            if parameter.attrib.get("key", "").strip().lower() == "name"
        ),
        "",
    )


def _external_edge_ids(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    return {
        edge.attrib.get("id", "")
        for edge in root.findall("edge")
        if edge.attrib.get("id") and not edge.attrib.get("id", "").startswith(":")
        and not edge.attrib.get("function")
    }


def _write_join_patch(
    path: Path,
    *,
    join_groups: Sequence[Sequence[str]] = JOIN_GROUPS,
) -> None:
    root = ET.Element("nodes")
    for group in join_groups:
        ET.SubElement(root, "join", {"nodes": " ".join(group)})
    ET.indent(root, space="    ")
    write_text_atomic(
        path,
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode")
        + "\n",
    )


def _ordered_route_gate(connections: Mapping[str, set[str]], output_edge_ids: set[str]) -> dict[str, object]:
    missing_edges = [edge_id for edge_id in MAINLINE_ROUTE_EDGE_IDS if edge_id not in output_edge_ids]
    broken_links = [
        [from_edge, to_edge]
        for from_edge, to_edge in zip(MAINLINE_ROUTE_EDGE_IDS, MAINLINE_ROUTE_EDGE_IDS[1:])
        if to_edge not in connections.get(from_edge, set())
    ]
    return {
        "status": "pass" if not missing_edges and not broken_links else "fail",
        "missing_edges": missing_edges,
        "broken_links": broken_links,
        "edge_count": len(MAINLINE_ROUTE_EDGE_IDS),
    }


def _weak_components(
    passenger_ids: set[str],
    edges: Mapping[str, Any],
    connections: Mapping[str, set[str]],
) -> list[set[str]]:
    graph: dict[str, set[str]] = {edge_id: set() for edge_id in passenger_ids}
    for from_edge, to_edges in connections.items():
        if from_edge not in passenger_ids:
            continue
        for to_edge in to_edges:
            if to_edge in passenger_ids:
                graph[from_edge].add(to_edge)
                graph[to_edge].add(from_edge)
    components: list[set[str]] = []
    remaining = set(passenger_ids)
    while remaining:
        start = min(remaining)
        component: set[str] = set()
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            remaining.discard(current)
            queue.extend(graph[current] - component)
        components.append(component)
    return components


def _result_dict(result: object) -> dict[str, object]:
    if hasattr(result, "to_dict"):
        return result.to_dict()  # type: ignore[no-any-return]
    return dict(result)  # type: ignore[arg-type]


def _canonicalize_net_file(path: Path) -> None:
    tree = ET.parse(path)
    ET.indent(tree, space="    ")
    write_text_atomic(
        path,
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(tree.getroot(), encoding="unicode")
        + "\n",
    )
