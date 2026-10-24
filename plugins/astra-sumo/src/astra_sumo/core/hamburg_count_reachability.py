"""Prioritize unresolved movements using a static upper bound on detector reachability."""

import csv
import json
from collections import defaultdict, deque
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .connection_mode_audit import _MOTORIZED_MODES
from .local_network_conversion import loaded_turn_permissions
from .osm_access import _permission_set


def audit_hamburg_count_reachability(*, candidate_manifest: Path, field_binding_manifest: Path, output_file: Path) -> dict:
    """Keep unknown outlets explicit; a graph path is not a native driving proof."""
    manifest_path, binding_path = Path(candidate_manifest).resolve(strict=True), Path(field_binding_manifest).resolve(strict=True)
    manifest, binding = (json.loads(path.read_text(encoding="utf-8")) for path in (manifest_path, binding_path))
    network_record = manifest["artifacts"]["network"]
    network = Path(network_record["path"])
    network = network.resolve(strict=True) if network.is_absolute() else (manifest_path.parent / network).resolve(strict=True)
    digest = file_sha256(network)
    warnings = []
    if digest != network_record["sha256"]:
        warnings.append("The candidate network differs from its manifest. Outlet correspondence is a supplied hypothesis.")
    if digest != binding["source"]["candidate_net"]["sha256"]:
        warnings.append("The field evidence names a different network hash. Field-to-lane correspondence is unverified.")
    output = Path(output_file).resolve()
    if output.exists() or output in {network, manifest_path, binding_path}:
        raise ValueError("Use a new count-reachability output file.")
    mapping_record = binding["artifacts"]["detector_mapping"]
    mapping_path = Path(mapping_record["path"])
    mapping_path = mapping_path.resolve(strict=True) if mapping_path.is_absolute() else (binding_path.parent / mapping_path).resolve(strict=True)
    mapping_digest = file_sha256(mapping_path)
    if mapping_digest != mapping_record["sha256"]:
        warnings.append("The mapping differs from its recorded hash. The actual saved lane hypotheses are measured.")
    root = ET.parse(network).getroot()
    edges = {edge.get("id"): edge for edge in root.findall("edge") if not edge.get("function")}
    lanes = {lane.get("id"): lane for edge in edges.values() for lane in edge.findall("lane")}
    lane_keys = {(edge.get("id"), index): lane.get("id") for edge in edges.values() for index, lane in enumerate(edge.findall("lane"))}
    permissions = {key: _permission_set(lane.attrib, network_version=root.get("version", "0.0")) for key, lane in lanes.items()}
    outgoing = defaultdict(list)
    for pair, modes in loaded_turn_permissions(root).items():
        outgoing[lane_keys[pair[:2]]].append((lane_keys[pair[2:]], modes, "compiled_connection"))
    for edge in edges.values():
        children = edge.findall("lane")
        for index, lane in enumerate(children):
            for delta, attribute in ((1, "changeLeft"), (-1, "changeRight")):
                if 0 <= index + delta < len(children):
                    other = children[index + delta]
                    modes = permissions[lane.get("id")] & permissions[other.get("id")]
                    if lane.get(attribute) is not None:
                        modes &= _permission_set({"allow": lane.get(attribute)}, network_version=root.get("version", "0.0"))
                    outgoing[lane.get("id")].append((other.get("id"), modes, "ordinary_lane_change_upper_bound"))
    fields, missing_fields = defaultdict(list), []
    with mapping_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            item = {"stream_id": int(row["stream_id"]), "sumo_lane": row["sumo_lane"],
                    "mapping_status": row.get("mapping_status", ""), "mapping_confidence": row.get("mapping_confidence", "")}
            if item["sumo_lane"] in lanes:
                fields[item["sumo_lane"]].append(item)
            else:
                missing_fields.append(item)

    cache = {}

    def reached(start, mode):
        if (start, mode) in cache:
            return cache[start, mode]
        found, paths = {}, {start: [start]}
        queue = deque([start]) if mode in permissions[start] else deque()
        while queue:
            lane = queue.popleft()
            for field in fields[lane]:
                found.setdefault(field["stream_id"], paths[lane])
            for target, modes, _ in outgoing[lane]:
                if mode in modes and target not in paths:
                    paths[target] = [*paths[lane], target]
                    queue.append(target)
        cache[start, mode] = found
        return found

    audit = manifest["official_connection_audit"]
    bindings = {(str(row["node_id"]), str(row["intersection_part"])): row for row in audit["lane_bindings"]}
    rows = []
    for movement in audit["ambiguous"]:
        group = bindings.get((str(movement.get("node_id")), str(movement.get("intersection_part", "0"))), {})
        egress = group.get("egress", {})
        lane_id = str(movement.get("egress_lane_id", ""))
        selected = egress.get("bindings", {}).get(lane_id)
        record = next((row for row in egress.get("records", []) if str(row["lane_id"]) == lane_id), {})
        hypotheses = ([{"edge": selected[0], "lane": selected[1], "basis": "declared_outlet_correspondence"}] if selected else
                      [{**row, "basis": "saved_outlet_candidate"} for row in record.get("candidates", [])])
        usable = [(lane_keys[str(row["edge"]), int(row["lane"])], row) for row in hypotheses
                  if (str(row["edge"]), int(row["lane"])) in lane_keys]
        restriction = movement.get("allowed_vehicle_classes")
        modes = sorted(_MOTORIZED_MODES & set(restriction)) if restriction is not None else sorted(_MOTORIZED_MODES)
        by_mode, witnesses = {}, {}
        for mode in modes:
            available = {}
            for start, hypothesis in usable:
                for field, path in reached(start, mode).items():
                    available.setdefault(field, path)
                    witnesses.setdefault(field, {"vehicle_class": mode, "outlet": hypothesis, "ordinary_lane_path": path})
            by_mode[mode] = sorted(available)
        rows.append({"node_id": movement.get("node_id"), "movement_id": movement.get("movement_id"),
            "intersection_part": movement.get("intersection_part"), "egress_lane_id": lane_id,
            "allowed_vehicle_classes": restriction, "revocable_lane_assumption": movement.get("revocable_lane_assumption"),
            "outlet_hypotheses": hypotheses, "usable_outlet_lane_ids": [lane for lane, _ in usable],
            "status": "outlet_unknown" if not usable else "potential_count_influence" if witnesses else "no_path_in_saved_outlet_hypotheses",
            "reachable_fields_by_vehicle_class": by_mode,
            "witnesses": [{"stream_id": field, **value} for field, value in sorted(witnesses.items())]})
    result = {"schema": "astra.hamburg-count-reachability/v1", "status": "measured",
        "warnings": warnings, "correspondence_basis": "unverified_supplied_hypotheses" if warnings else "matching_saved_evidence",
        "hash_records": {"network_declared": network_record["sha256"], "network_observed": digest,
                         "field_network_declared": binding["source"]["candidate_net"]["sha256"],
                         "mapping_declared": mapping_record["sha256"], "mapping_observed": mapping_digest},
        "network": {"path": str(network), "sha256": digest},
        "candidate_manifest": {"path": str(manifest_path), "sha256": file_sha256(manifest_path)},
        "field_binding_manifest": {"path": str(binding_path), "sha256": file_sha256(binding_path)},
        "tested_vehicle_classes": sorted(_MOTORIZED_MODES), "rows": rows,
        "field_correspondence": [field for records in fields.values() for field in records], "unavailable_fields": missing_fields,
        "potential_count_influence": sum(row["status"] == "potential_count_influence" for row in rows),
        "unknown_outlets": sum(row["status"] == "outlet_unknown" for row in rows),
        "claim_boundary": "Static upper bound using compiled connection permissions and ordinary lane-change permissions. It ignores lane-change space, time, queues and signals. Saved outlet candidates are not exhaustive. Absence of a path is not proof that an unresolved official movement cannot affect counts. This diagnostic neither filters fitting targets nor proves native driving or field legality."}
    write_json_atomic(output, result)
    return result
