"""Cut network layers from one master SUMO network by removing ordinary edges only.

A derived layer keeps the master's edge, lane, junction and signal identifiers, its coordinate frame and the
signal timeline of every retained controlled link, so one OSM-keyed mapping serves every layer. Phase indices
are layer-specific: netconvert may merge phases whose only difference was a removed link.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .osm_access import _permission_set
from .road_hierarchy import keep_selected_edges_natively
from .sumo_commands import run_sumo_load_audit


# Delivery, emergency and authority alone do not make a vehicle edge: SUMO footways allow delivery.
VEHICLE_LAYER_CLASSES = ("passenger", "bus", "coach", "truck", "trailer", "taxi", "motorcycle", "evehicle", "hov")
# None keeps every master edge.
NETWORK_LAYER_CLASSES: dict[str, tuple[str, ...] | None] = {"vehicle": VEHICLE_LAYER_CLASSES, "multimodal": None}
LONG_CYCLE_THRESHOLD_S = 120.0
COMPONENT_SHARE_THRESHOLD = 0.98
# The coordinate frame; convBoundary shrinks with the removed edges and is recorded, not compared.
_LOCATION_IDENTITY = ("netOffset", "projParameter")
_EXAMPLE_LIMIT = 20
_CLAIM_BOUNDARY = ("Edges are removed from the master only. Identity checks cover identifiers, permissions, "
                   "coordinates and signal timing of retained links; lane lengths at junctions that lost an edge "
                   "are recomputed, and phase indices are specific to each layer.")


def _direct_children(net_file: Path):
    """Yield each complete direct child of <net> with the network version, keeping memory flat."""
    context = ET.iterparse(net_file, events=("start", "end"))
    _, root = next(context)
    version, depth = root.get("version", "0.0"), 0
    for event, element in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth:
            continue
        yield version, element
        root.clear()


def _orig_id(lane: ET.Element) -> str | None:
    return next((row.get("value") for row in lane.findall("param") if row.get("key") == "origId"), None)


def _permission_reader():
    """Permission sets by declaration, shared between lanes so a city network stays small in memory."""
    known: dict[tuple[str | None, str | None, str], frozenset[str]] = {}

    def permissions(lane: ET.Element, version: str) -> frozenset[str]:
        key = (lane.get("allow"), lane.get("disallow"), version)
        if key not in known:
            known[key] = frozenset(_permission_set(lane.attrib, network_version=version))
        return known[key]

    return permissions


def _net_inventory(net_file: Path) -> dict[str, Any]:
    """Read the identifiers and signal programs a layer must keep from a streamed network."""
    edges: dict[str, dict[str, Any]] = {}
    crossings: dict[str, tuple[str, str]] = {}
    junctions: set[str] = set()
    programs: dict[tuple[str, str], dict[str, Any]] = {}
    rows: list[tuple[str, ...]] = []
    location: dict[str, str] = {}
    permissions = _permission_reader()
    for version, element in _direct_children(net_file):
        if element.tag == "location":
            location = dict(element.attrib)
        elif element.tag == "edge":
            function = element.get("function") or ""
            if function == "crossing":
                junction = element.get("id", "")[1:].rsplit("_c", 1)[0]
                crossings[element.get("id", "")] = (junction, " ".join(sorted(element.get("crossingEdges", "").split())))
            elif not function:
                lanes = element.findall("lane")
                edges[element.get("id", "")] = {
                    "lanes": [lane.get("id") for lane in lanes],
                    "permissions": [permissions(lane, version) for lane in lanes],
                    "orig_ids": [_orig_id(lane) for lane in lanes],
                }
        elif element.tag == "junction" and element.get("type") != "internal":
            junctions.add(element.get("id", ""))
        elif element.tag == "tlLogic":
            programs[(element.get("id", ""), element.get("programID", ""))] = {
                "type": element.get("type"), "offset": element.get("offset"),
                "phases": [(float(phase.get("duration", "0")), phase.get("state", "")) for phase in element.findall("phase")]}
        elif element.tag == "connection":
            first, last, tl = element.get("from", ""), element.get("to", ""), element.get("tl")
            if tl or not (first.startswith(":") or last.startswith(":")):
                rows.append((first, element.get("fromLane", ""), last, element.get("toLane", ""), tl or "",
                             element.get("linkIndex", "")))
    connections: dict[tuple[str, ...], tuple[str, str]] = {}
    links: dict[tuple[str, ...], tuple[tuple[str, str], ...]] = {}
    crossing_links: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    for first, first_lane, last, last_lane, tl, index in rows:
        if first in edges and last in edges:
            connections[(first, first_lane, last, last_lane)] = (tl, index)
            if tl:
                links[("connection", first, first_lane, last, last_lane)] = ((tl, index),)
        crossing = crossings.get(last) or crossings.get(first)
        if tl and crossing:
            crossing_links[("crossing", *crossing)].append((tl, index))
    links.update({key: tuple(sorted(values)) for key, values in crossing_links.items()})
    return {"location": location, "edges": edges, "junctions": junctions, "programs": programs,
            "connections": connections, "controlled_links": links}


def _layer_selection(inventory: Mapping[str, Any], layer: str) -> dict[str, Any]:
    if layer not in NETWORK_LAYER_CLASSES:
        raise ValueError(f"Choose a network layer from {', '.join(NETWORK_LAYER_CLASSES)}.")
    classes = NETWORK_LAYER_CLASSES[layer]
    kept, removed, signatures = [], [], Counter()
    for edge_id, edge in sorted(inventory["edges"].items()):
        allowed = set().union(*edge["permissions"])
        if classes is None or allowed & set(classes):
            kept.append(edge_id)
        else:
            removed.append(edge_id)
            signatures[" ".join(sorted(allowed)) or "none"] += 1
    return {"layer": layer, "classes": list(classes) if classes is not None else None,
            "basis": "every_master_edge" if classes is None else "a_lane_allows_a_layer_class",
            "kept_edge_ids": kept, "removed_edge_ids": removed,
            "removed_by_permission_signature": dict(sorted(signatures.items()))}


def select_layer_edges(net: Path | str, layer: str) -> dict[str, Any]:
    """Partition the master's ordinary edges into the ones ``layer`` keeps and the ones it removes.

    The vehicle layer keeps an edge when one of its lanes allows a class in ``VEHICLE_LAYER_CLASSES``; removed
    edges are counted by the classes their lanes allow. The multimodal layer keeps every edge.
    """
    return _layer_selection(_net_inventory(Path(net)), layer)


def _timeline(program: Mapping[str, Any], index: str) -> list[tuple[str, float]] | None:
    """Signal of one link over the cycle, with consecutive equal signals merged."""
    try:
        position = int(index)
    except ValueError:
        return None
    rows: list[list[Any]] = []
    for duration, state in program["phases"]:
        signal = state[position] if 0 <= position < len(state) else ""
        if rows and rows[-1][0] == signal:
            rows[-1][1] += duration
        else:
            rows.append([signal, duration])
    return [(signal, round(duration, 6)) for signal, duration in rows]


def _cycle(program: Mapping[str, Any]) -> float:
    return round(sum(duration for duration, _ in program["phases"]), 6)


def _layer_identity(master: Mapping[str, Any], layer: Mapping[str, Any], kept: set[str]) -> dict[str, Any]:
    master_edges, layer_edges = master["edges"], layer["edges"]
    retained = sorted(set(layer_edges) & set(master_edges))
    changed = {key: [] for key in ("lanes", "permissions", "orig_ids")}
    for edge_id in retained:
        before, after = master_edges[edge_id], layer_edges[edge_id]
        if before["lanes"] != after["lanes"]:
            changed["lanes"].append(edge_id)
            continue
        for key in ("permissions", "orig_ids"):
            changed[key].extend(lane for lane, old, new in zip(after["lanes"], before[key], after[key]) if old != new)
    lost = [key for key, value in master["connections"].items()
            if key[0] in layer_edges and key[2] in layer_edges and layer["connections"].get(key) != value]
    added = [key for key in layer["connections"] if key not in master["connections"]]
    timelines = []
    for key, after in layer["controlled_links"].items():
        before = master["controlled_links"].get(key)
        if before is None or len(before) != len(after):
            timelines.append({"link": list(key), "reason": "link_not_in_master" if before is None else "link_count_changed"})
            continue
        for (old_tl, old_index), (new_tl, new_index) in zip(before, after):
            for program_id in sorted({pid for tl, pid in layer["programs"] if tl == new_tl}):
                old_program = master["programs"].get((old_tl, program_id))
                new_program = layer["programs"][(new_tl, program_id)]
                if old_program is None or _timeline(old_program, old_index) != _timeline(new_program, new_index):
                    timelines.append({"link": list(key), "tl": new_tl, "program_id": program_id,
                                      "reason": "signal_timeline_changed"})
    controllers = []
    for (tl, program_id), program in sorted(layer["programs"].items()):
        before = master["programs"].get((tl, program_id))
        if before is None:
            controllers.append({"tl": tl, "program_id": program_id, "reason": "controller_not_in_master"})
        elif (_cycle(program), program["offset"], program["type"]) != (_cycle(before), before["offset"], before["type"]):
            controllers.append({"tl": tl, "program_id": program_id, "reason": "cycle_offset_or_type_changed",
                                "master_cycle_s": _cycle(before), "layer_cycle_s": _cycle(program)})
    master_tls, layer_tls = {tl for tl, _ in master["programs"]}, {tl for tl, _ in layer["programs"]}
    location = {key: {"master": master["location"].get(key), "layer": layer["location"].get(key)}
                for key in (*_LOCATION_IDENTITY, "convBoundary", "origBoundary")}
    removed_links = sorted(set(master["controlled_links"]) - set(layer["controlled_links"]))
    checks = {
        "edge_ids_subset_of_master": set(layer_edges) <= set(master_edges),
        "selected_edges_realized": set(layer_edges) == kept,
        "junction_ids_subset_of_master": layer["junctions"] <= master["junctions"],
        "tls_ids_subset_of_master": layer_tls <= master_tls,
        "lane_ids_equal": not changed["lanes"],
        "lane_permissions_equal": not changed["permissions"],
        "orig_ids_equal": not changed["orig_ids"],
        "location_equal": all(location[key]["master"] == location[key]["layer"] for key in _LOCATION_IDENTITY),
        "connections_between_retained_edges_kept": not lost and not added,
        "controlled_link_timelines_equal": not timelines,
        "controller_cycle_offset_type_equal": not controllers,
    }
    return {
        "status": "measured" if all(checks.values()) else "blocked",
        **({} if all(checks.values()) else {"reason": "layer_identity_check_failed",
                                            "failed_checks": sorted(key for key, ok in checks.items() if not ok)}),
        "checks": checks,
        "retained_edge_count": len(retained),
        "retained_connection_count": sum(1 for key in layer["connections"] if key in master["connections"]),
        "retained_controlled_link_count": len(layer["controlled_links"]),
        "removed_controlled_link_count": len(removed_links),
        "removed_controlled_links": [list(key) for key in removed_links[:_EXAMPLE_LIMIT]],
        "removed_tls_ids": sorted(master_tls - layer_tls),
        "removed_junction_count": len(master["junctions"] - layer["junctions"]),
        "location": location,
        "changed_lane_lists": changed["lanes"][:_EXAMPLE_LIMIT],
        "changed_lane_permissions": changed["permissions"][:_EXAMPLE_LIMIT],
        "changed_orig_ids": changed["orig_ids"][:_EXAMPLE_LIMIT],
        "lost_connections": [list(key) for key in lost[:_EXAMPLE_LIMIT]],
        "added_connections": [list(key) for key in added[:_EXAMPLE_LIMIT]],
        "changed_signal_timelines": timelines[:_EXAMPLE_LIMIT],
        "changed_controllers": controllers[:_EXAMPLE_LIMIT],
        "basis": "per-link signal timelines with consecutive equal signals merged; phase indices are not compared",
    }


def derive_network_layer(master_net_file: Path | str, output_dir: Path | str, layer: str, *,
                         netconvert_binary: str = "netconvert", sumo_binary: str = "sumo",
                         timeout_seconds: float = 240.0) -> dict[str, Any]:
    """Cut ``layer`` from the master by removing ordinary edges only, and record what it kept.

    Uses ``netconvert --sumo-net-file <master> --keep-edges.input-file <list> --offset.disable-normalization true
    --precision 6`` (preload, never ``--keep-edges.postload``), then the identity checks and a SUMO load audit.
    The output directory must be new and the master is never written. Status is ``measured`` when edges were
    removed and every check holds, ``no_effect`` when the layer keeps every master edge (the layer is then the
    master file), and ``blocked`` with a reason otherwise.
    """
    source, output = Path(master_net_file).resolve(strict=True), Path(output_dir).resolve()
    if output.exists() or source.is_relative_to(output):
        raise ValueError("Use a new output directory separate from the master network.")
    master_hash = file_sha256(source)
    master = _net_inventory(source)
    selection = _layer_selection(master, layer)
    output.mkdir(parents=True)
    kept_file, removed_file = output / "kept-edges.txt", output / "removed-edges.txt"
    kept_file.write_text("".join(f"{edge_id}\n" for edge_id in selection["kept_edge_ids"]), encoding="utf-8")
    removed_file.write_text("".join(f"{edge_id}\n" for edge_id in selection["removed_edge_ids"]), encoding="utf-8")
    candidate = output / f"{layer}.net.xml"
    report: dict[str, Any] = {
        "schema": "astra.network-layer/v1", "status": "blocked", "layer": layer, "classes": selection["classes"],
        "selection_basis": selection["basis"], "master": {"path": str(source), "sha256": master_hash},
        "net_file": "", "sha256": "", "derived_from_sha256": master_hash,
        "edge_count": len(selection["kept_edge_ids"]), "removed_edge_count": len(selection["removed_edge_ids"]),
        "kept_edges_file": str(kept_file), "removed_edges_file": str(removed_file),
        "removed_by_permission_signature": selection["removed_by_permission_signature"],
        "native_conversion_count": 0, "claim_boundary": _CLAIM_BOUNDARY}
    if not selection["removed_edge_ids"]:
        report.update(status="no_effect", reason="layer_keeps_every_master_edge", net_file=str(source), sha256=master_hash,
                      identity={"status": "no_effect", "reason": "layer_keeps_every_master_edge"})
    elif not selection["kept_edge_ids"]:
        report.update(reason="no_master_edge_serves_layer",
                      identity={"status": "blocked", "reason": "no_master_edge_serves_layer"})
    else:
        conversion = keep_selected_edges_natively(source, output, kept_file, candidate,
                                                  netconvert_binary=netconvert_binary, timeout_seconds=timeout_seconds)
        report["source_signal_cleanup"] = conversion["cleanup"]
        if "effective" not in conversion:
            report.update(reason="master_has_unresolved_signal_references",
                          identity={"status": "blocked", "reason": "master_has_unresolved_signal_references"})
        else:
            report.update(command=conversion["native"].to_dict(), native_attempts=conversion["attempts"],
                          native_conversion_count=len(conversion["attempts"]), permission_input=conversion["prepared"])
            if not candidate.is_file():
                report.update(reason="native_layer_conversion_failed",
                              identity={"status": "blocked", "reason": "native_layer_conversion_failed"})
            else:
                identity = _layer_identity(master, _net_inventory(candidate), set(selection["kept_edge_ids"]))
                load = run_sumo_load_audit(net_file=candidate, output_dir=output / "sumo-load",
                                           sumo_binary=str(sumo_binary), timeout_seconds=timeout_seconds)
                report.update(net_file=str(candidate), sha256=file_sha256(candidate), identity=identity, sumo_load=load)
                if identity["status"] != "measured":
                    report["reason"] = "layer_identity_check_failed"
                elif load["status"] != "pass":
                    report["reason"] = "sumo_load_failed"
                else:
                    report["status"] = "measured"
    report["master_unchanged"] = file_sha256(source) == master_hash
    if not report["master_unchanged"]:
        report.update(status="blocked", reason="master_network_changed")
    report["manifest_file"] = str(output / "layer-manifest.json")
    write_json_atomic(output / "layer-manifest.json", report)
    return report


def master_network_layer(master_net_file: Path | str, layer: str) -> dict[str, Any]:
    """Describe the master itself as ``layer``: nothing is removed and the layer file is the master."""
    if layer not in NETWORK_LAYER_CLASSES:
        raise ValueError(f"Choose a network layer from {', '.join(NETWORK_LAYER_CLASSES)}.")
    source = Path(master_net_file).resolve(strict=True)
    digest = file_sha256(source)
    classes = NETWORK_LAYER_CLASSES[layer]
    return {"schema": "astra.network-layer/v1", "status": "no_effect", "reason": "layer_is_master", "layer": layer,
            "classes": list(classes) if classes is not None else None, "net_file": str(source), "sha256": digest,
            "derived_from_sha256": digest, "edge_count": len(_net_inventory(source)["edges"]), "removed_edge_count": 0,
            "identity": {"status": "no_effect", "reason": "layer_is_master"}}


def measure_mode_components(net_file: Path | str, modes: Iterable[str], *,
                            threshold: float = COMPONENT_SHARE_THRESHOLD) -> dict[str, Any]:
    """Measure, without pruning, how much of each mode's network its largest connected component holds.

    Ordinary edges are the nodes. A connection joins two of them when the mode is allowed on both lanes and on
    every internal lane on the way, crossings and walking areas included. Without walking areas, pedestrians
    change edges at any shared junction. A share below ``threshold`` needs review: edges outside the largest
    component cannot be reached from it.
    """
    wanted = frozenset(modes)
    modes = sorted(wanted)
    lane_modes: dict[str, frozenset[str]] = {}
    lane_node: dict[str, str] = {}
    mode_edges: dict[str, set[str]] = {mode: set() for mode in modes}
    edge_ends: dict[str, tuple[str, str]] = {}
    pairs: list[tuple[str, str]] = []
    walkingareas = False
    permissions = _permission_reader()
    for version, element in _direct_children(Path(net_file)):
        if element.tag == "edge":
            function = element.get("function") or ""
            walkingareas |= function == "walkingarea"
            edge_id = element.get("id", "")
            for lane in element.findall("lane"):
                allowed = permissions(lane, version) & wanted
                if not allowed:
                    continue
                lane_modes[lane.get("id", "")] = allowed
                lane_node[lane.get("id", "")] = lane.get("id", "") if function else edge_id
                if not function:
                    for mode in allowed:
                        mode_edges[mode].add(edge_id)
                    edge_ends[edge_id] = (element.get("from", ""), element.get("to", ""))
        elif element.tag == "connection":
            source = f'{element.get("from")}_{element.get("fromLane")}'
            # A movement through a junction continues from its first internal lane.
            following = element.get("via") or f'{element.get("to")}_{element.get("toLane")}'
            if source in lane_modes and following in lane_modes:
                pairs.append((source, following))
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    results = {}
    for mode in modes:
        parent.clear()
        for first, last in pairs:
            if mode in lane_modes[first] and mode in lane_modes[last]:
                parent[find(lane_node[first])] = find(lane_node[last])
        if mode == "pedestrian" and not walkingareas:
            at_node = defaultdict(list)
            for edge_id in mode_edges[mode]:
                for node in edge_ends[edge_id]:
                    at_node[node].append(edge_id)
            for edge_ids in at_node.values():
                for other in edge_ids[1:]:
                    parent[find(other)] = find(edge_ids[0])
        sizes = Counter(find(edge_id) for edge_id in mode_edges[mode])
        total, largest = len(mode_edges[mode]), max(sizes.values(), default=0)
        share = largest / total if total else None
        row = {"edge_count": total, "component_count": len(sizes), "largest_component_edge_count": largest,
               "outside_largest_component_edge_count": total - largest,
               "largest_component_share": None if share is None else round(share, 6)}
        if share is None:
            row.update(gate="review_required", reason="no_edge_allows_mode")
        elif share < threshold:
            row.update(gate="review_required", reason="largest_component_share_below_threshold")
        else:
            row["gate"] = "pass"
        results[mode] = row
    review = sorted(mode for mode, row in results.items() if row["gate"] != "pass")
    return {"status": "measured", "method": "streamed_connections_and_internal_lanes_without_pruning",
            "threshold": threshold, "network_has_walkingareas": walkingareas, "modes": results,
            "gate": "review_required" if review else "pass",
            **({"reason": "mode_network_needs_review", "review_modes": review} if review else {})}


def measure_tls_cycles(net_file: Path | str, *, threshold_s: float = LONG_CYCLE_THRESHOLD_S) -> dict[str, Any]:
    """Cycle length of every signal program, and the programs above ``threshold_s``.

    netconvert can inflate the program of a joined controller once crossings are added, so long cycles are
    listed for review; the lengths are measured, not judged against field timing.
    """
    cycles = [{"tl_id": element.get("id", ""), "program_id": element.get("programID", ""), "type": element.get("type", ""),
               "phase_count": len(element.findall("phase")),
               "cycle_s": round(sum(float(phase.get("duration", "0")) for phase in element.findall("phase")), 6)}
              for _, element in _direct_children(Path(net_file)) if element.tag == "tlLogic"]
    long_cycles = [row for row in cycles if row["cycle_s"] > threshold_s]
    return {"status": "measured", "threshold_s": threshold_s, "controller_count": len(cycles),
            "max_cycle_s": max((row["cycle_s"] for row in cycles), default=None),
            "long_cycle_controllers": long_cycles, "cycles": cycles}
