"""Keep Hamburg construction checks separate from unresolved geometry review."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import (
    _junction_findings,
    audit_network_connection_mode,
    compare_connection_mode_audits,
    lane_supports_motorized,
)
from .junction_boundary_rebuild import _signature
from .surface_overlap_audit import audit_sumo_lane_junction_surface_overlaps


GEOMETRY_PRECISION_M = 0.1  # Existing Hamburg endpoint and geometry precision.


def _verified_path(record: dict, base: Path, label: str) -> Path:
    if not isinstance(record, dict) or not record.get("path") or not record.get("sha256"):
        raise ValueError(f"{label} requires a path and SHA-256")
    path = Path(record["path"]).expanduser()
    path = (path if path.is_absolute() else base / path).resolve(strict=True)
    if file_sha256(path) != str(record["sha256"]).lower():
        raise ValueError(f"{label} SHA-256 does not match")
    return path


def _external_edges(root: ET.Element) -> dict[str, ET.Element]:
    return {row.get("id"): row for row in root.findall("edge")
            if row.get("function") != "internal" and not row.get("id", "").startswith(":")}


def _geometry_difference(before, after):
    displacement, length_difference, comparable = 0.0, 0.0, True
    for left, right in zip(before.iter(), after.iter()):
        if left.get("shape") != right.get("shape"):
            try:
                shapes = [[tuple(map(float, point.split(","))) for point in row.get("shape", "").split()]
                          for row in (left, right)]
                if not shapes[0] or len(shapes[0]) != len(shapes[1]):
                    comparable = False
                else:
                    distances = [math.dist(a, b) for a, b in zip(*shapes)]
                    if not all(math.isfinite(value) for value in distances):
                        comparable = False
                    else:
                        displacement = max(displacement, *distances)
            except ValueError:
                comparable = False
        if left.get("length") != right.get("length"):
            try:
                difference = abs(float(left.get("length", "nan")) - float(right.get("length", "nan")))
                if not math.isfinite(difference):
                    comparable = False
                else:
                    length_difference = max(length_difference, difference)
            except ValueError:
                comparable = False
    displacement, length_difference = round(displacement, 9), round(length_difference, 9)
    return {"maximum_vertex_displacement_m": displacement, "maximum_length_difference_m": length_difference,
            "paired_geometry_comparable": comparable, "geometry_precision_m": GEOMETRY_PRECISION_M,
            "within_geometry_precision": comparable and max(displacement, length_difference) <= GEOMETRY_PRECISION_M}


def _outside_preservation(source, candidate, groups, approaches):
    before, after = _external_edges(source), _external_edges(candidate)
    members = {node for row in groups for node in row["source_node_ids"]}
    removable = {key for key, edge in before.items() if any(
        edge.get("from") in row["source_node_ids"] and edge.get("to") in row["source_node_ids"]
        for row in groups)}
    declared = {row[key] for row in approaches.get("approaches", [])
                for key in ("source_edge_id", "upstream_edge_id", "downstream_edge_id") if row.get(key)}
    declared.update(row["edge_id"] for row in approaches.get("boundary_geometry_adjustments", []))
    changed, declared_changes = [], []
    for key in sorted(before.keys() & after.keys()):
        if _signature(before[key]) == _signature(after[key]):
            continue
        if key in declared:
            declared_changes.append(key)
        elif before[key].get("from") not in members and before[key].get("to") not in members:
            values = [ET.tostring(row, encoding="unicode") for row in (before[key], after[key])]
            geometry_only = ET.canonicalize(values[0], strip_text=True, exclude_attrs={"shape", "length"}) == (
                ET.canonicalize(values[1], strip_text=True, exclude_attrs={"shape", "length"}))
            changed.append({"edge_id": key, "geometry_only": geometry_only,
                            **_geometry_difference(before[key], after[key]),
                            "before_xml": values[0], "after_xml": values[1]})
    removed = sorted(before.keys() - after.keys() - removable)
    added = sorted(after.keys() - before.keys() - declared)
    locations = [root.find("location") for root in (source, candidate)]
    frames = [tuple(row.get(key) for key in ("netOffset", "projParameter")) if row is not None else None
              for row in locations]
    frame_changed = frames[0] is None or frames[0] != frames[1]
    status = "blocked" if removed or added or frame_changed or any(not row["geometry_only"] for row in changed) else (
        "review_required" if any(not row["within_geometry_precision"] for row in changed) else "pass")
    return {"status": status, "unexpected_removed_edges": removed, "unexplained_new_edges": added,
            "changed_outside_edges": changed, "declared_changed_approach_edges": declared_changes,
            "declared_approach_edge_ids": sorted(declared), "removed_inside_single_group": sorted(
                (before.keys() - after.keys()) & removable),
            "coordinate_frame_changed": frame_changed,
            "claim_boundary": "Only roads wholly inside one declared joined part may disappear. Exact outside "
                              "differences remain listed. Shape and length use the existing 0.1 m geometry "
                              "precision; identities, permissions and all other attributes must match exactly."}


def _official_connectivity(manifest, root):
    declared = manifest.get("official_connection_audit", {})
    actual = {(row.get("from"), int(row.get("fromLane", "-1")), row.get("to"), int(row.get("toLane", "-1")))
              for row in root.findall("connection")}
    required = declared.get("required", [])
    pairs = {tuple(row["sumo_connection"]) for row in required}
    pairs.update(tuple(pair) for row in required for pair in row.get("sumo_connections", []))
    pairs.update(tuple(row["sumo_connection"]) for row in declared.get("boundary_connections", []))
    missing = [list(pair) for pair in sorted(pairs - actual)]
    unresolved = declared.get("ambiguous", [])
    total = int(manifest.get("counts", {}).get("official_vehicle_movements", 0))
    coverage = total > 0 and len(required) + len(unresolved) == total
    blocked = missing or declared.get("missing") or not coverage or declared.get("status") == "blocked"
    review = unresolved or declared.get("unexplained_extra") or declared.get("composed_boundary_path_reviews")
    status = "blocked" if blocked else "review_required" if review or declared.get("status") != "pass" else "pass"
    return {"status": status, "declared_status": declared.get("status"), "official_total": total,
            "required_record_count": len(required), "coverage_counts_agree": coverage,
            "missing_actual_connections": missing, "declared_missing": declared.get("missing", []),
            "unresolved": unresolved, "unexplained_extra": declared.get("unexplained_extra", []),
            "composed_boundary_path_reviews": declared.get("composed_boundary_path_reviews", []),
            "claim_boundary": "This checks declared lane connections on the final network. Separate vehicle "
                              "probes must establish that vehicles traverse their complete internal paths."}


def _surface_findings(root, surface, targets, declared_edges):
    findings = ([{**row, "kind": "junction_surface_overlap"} for row in surface["junction_junction_overlaps"]]
                + [{**row, "kind": "lane_junction_surface_overlap"}
                   for row in surface["external_lane_non_owner_junction_overlaps"]]
                + surface["geometry_errors"])
    target, outside = [], []
    edges = _external_edges(root)
    target_lanes = {lane.get("id") for edge in edges.values()
                    if edge.get("from") in targets or edge.get("to") in targets or edge.get("id") in declared_edges
                    for lane in edge.findall("lane")}
    for row in findings:
        # A road ending at a target can also cross a distant, unrelated polygon.
        # Only the polygon being checked makes an overlap a target finding.
        focused = any(str(row.get(key, "")) in targets for key in (
            "junction_id", "first_junction_id", "second_junction_id", "non_owner_junction_id"))
        if row.get("kind") == "invalid_external_lane_geometry" and row.get("lane_id") in target_lanes:
            focused = True
        (target if focused else outside).append(row)
    return target, outside, target_lanes


def _outside_surface_review(source, candidate, before, after):
    def indexed(root, findings):
        nodes = {row.get("id"): row.get("shape") for row in root.findall("junction")}
        lanes = {row.get("id"): row.get("shape") for row in root.findall("edge/lane")}
        result = {}
        fields = ("kind", "junction_id", "first_junction_id", "second_junction_id", "non_owner_junction_id", "lane_id")
        for row in findings:
            key = tuple(str(row.get(field, "")) for field in fields)
            shapes = {identifier: nodes.get(identifier, lanes.get(identifier)) for identifier in key[1:] if identifier}
            result[key] = (row, json.dumps({"finding": row, "shapes": shapes}, sort_keys=True), shapes)
        return result
    source_findings, candidate_findings = indexed(source, before), indexed(candidate, after)
    inherited, introduced, changed = [], [], []
    for key, (row, signature, shapes) in candidate_findings.items():
        if key not in source_findings:
            introduced.append(row)
        elif signature == source_findings[key][1]:
            inherited.append(row)
        else:
            changed.append({"before": source_findings[key][0], "after": row,
                            "source_shapes": source_findings[key][2], "candidate_shapes": shapes,
                            "reason": "finding_or_its_geometry_changed"})
    return {"status": "review_required" if after else "pass",
            "regression_status": "review_required" if introduced or changed else "pass",
            "inherited_findings": inherited, "introduced_findings": introduced, "changed_findings": changed,
            "claim_boundary": "Unchanged original findings remain visible outside the selected area. Only new or "
                              "changed outside findings affect this construction decision."}


def _geometry_review(manifest, root, surface, targets, declared_edges, source, source_surface, source_targets):
    target, outside, target_lanes = _surface_findings(root, surface, targets, declared_edges)
    _, source_outside, _ = _surface_findings(source, source_surface, source_targets, declared_edges)
    outside_review = _outside_surface_review(source, root, source_outside, outside)
    non_area = [row for row in surface["non_area_junction_exclusions"] if row["junction_id"] in targets]
    edges = _external_edges(root)
    short = []
    for edge in edges.values():
        for lane in edge.findall("lane"):
            if lane.get("id") not in target_lanes or not lane_supports_motorized(lane):
                continue
            length = float(lane.get("length", "nan"))
            if not math.isfinite(length) or length < 5.0:
                short.append({"edge_id": edge.get("id"), "lane_id": lane.get("id"),
                              "length_m": length if math.isfinite(length) else None,
                              "reason": "shorter_than_standard_vehicle" if math.isfinite(length) else "length_missing",
                              "status": "review_required"})
    official = manifest.get("official_connection_audit", {})
    fallback = list(official.get("geometry_rejected", []))
    official_fallbacks = []
    limit = manifest.get("parameters", {}).get("maximum_anchor_projection_error_m")
    for row in manifest.get("movement_materialization", {}).get("movements", []):
        selected_error = row.get("selected_curve_anchor_projection_error_sum_m")
        official_error = row.get("official_curve_anchor_projection_error_sum_m")
        used_error = row.get("anchor_projection_error_sum_m")
        if (row.get("geometry_source") == "official_map_curve" and row.get("selected_source") == "aerial_trace"
                and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                        for value in (limit, selected_error, official_error, used_error))
                and limit > 0 and selected_error > limit and 0 <= official_error <= limit
                and round(official_error, 6) == used_error):
            official_fallbacks.append(row)
            continue
        if row.get("geometry_source") != "selected_curve" and not any(
            (old.get("node_id"), old.get("movement_id")) == (row.get("node_id"), row.get("movement_id"))
            for old in fallback):
            fallback.append({**row, "reason": "selected_curve_not_materialized"})
    fallback.extend({"join_id": value, "reason": "runtime_geometry_fallback"}
                    for value in manifest.get("runtime_fallback_join_ids", []))
    approach = manifest.get("approach_rebuild", {})
    boundary_reviews = [{"join_id": row.get("join_id"), **review}
                        for row in (manifest.get("boundary_rebuild") or {}).get("boundaries", [])
                        for review in row.get("native_port_adjustment_reviews", [])]
    contours = manifest.get("movement_materialization", {}).get("junction_contours", {})
    review = bool(target or short or fallback or approach.get("reviews") or boundary_reviews
                  or contours.get("status") in {"blocked", "review_required"}
                  or approach.get("status") in {"blocked", "review_required"} or surface.get("error")
                  or source_surface.get("error") or outside_review["regression_status"] != "pass")
    return {"status": "review_required" if review else "pass", "target_surface_findings": target,
            "outside_surface_findings": outside, "target_non_area_exclusions": non_area,
            "outside_surface_review": outside_review,
            "surface_audit_status": surface["status"],
            "surface_error": surface.get("error"), "short_target_lanes": short, "short_lane_reference_length_m": 5.0,
            "curve_fallbacks": fallback, "official_curve_fallbacks": official_fallbacks,
            "approach_status": approach.get("status", "not_applicable"),
            "approach_reviews": approach.get("reviews", []), "approach_gates": approach.get("gates", {}),
            "boundary_reviews": boundary_reviews,
            "junction_contours": contours,
            "claim_boundary": "Target polygons and outside findings are listed separately. The 5 m reference is "
                              "the existing default passenger length, not a minimum legal road length. Short roads, "
                              "target overlap findings and unresolved approaches require review, not automatic joining."}


def _reload(candidate, root, targets, destination, netconvert_binary, timeout_seconds):
    reloaded = destination / "reloaded.net.xml"
    command = [netconvert_binary, "--sumo-net-file", str(candidate), "--output-file", str(reloaded),
               "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25"]
    result = run_command(command, cwd=destination, timeout_seconds=timeout_seconds)
    report = {"status": "blocked", "command": result.to_dict(), "changed_external_edges": [],
              "changed_target_boundaries": [], "network": None}
    if result.returncode != 0 or not reloaded.is_file():
        report["error"] = "netconvert did not produce a reloaded network"
        return report
    try:
        after = ET.parse(reloaded).getroot()
        before_edges, after_edges = _external_edges(root), _external_edges(after)
        report["changed_external_edges"] = sorted(key for key in before_edges.keys() | after_edges.keys()
                                                 if key not in before_edges or key not in after_edges
                                                 or _signature(before_edges[key]) != _signature(after_edges[key]))
        nodes = [{row.get("id"): row for row in tree.findall("junction")} for tree in (root, after)]
        fields = ("shape", "customShape", "x", "y", "z")
        report["changed_target_boundaries"] = sorted(key for key in targets
            if key not in nodes[0] or key not in nodes[1]
            or any(nodes[0][key].get(field) != nodes[1][key].get(field) for field in fields))
        report["network"] = {"path": str(reloaded), "sha256": file_sha256(reloaded)}
        report["status"] = "blocked" if report["changed_external_edges"] or report["changed_target_boundaries"] else "pass"
    except (OSError, ET.ParseError, ValueError) as error:
        report["error"] = str(error)
    return report


def audit_hamburg_topology_candidate(
    manifest_file: Path | str,
    output_dir: Path | str,
    *,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
) -> dict[str, Any]:
    """Audit one immutable candidate without demand reconstruction or network edits."""
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    manifest_path = Path(manifest_file).expanduser().resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != "torii.hamburg-aerial-corridor-candidate/v1":
        raise ValueError("candidate manifest schema is invalid")
    source = _verified_path(manifest.get("inputs", {}).get("source_net"), manifest_path.parent, "source network")
    candidate = _verified_path(manifest.get("artifacts", {}).get("network"), manifest_path.parent, "candidate network")
    inputs = {path: file_sha256(path) for path in (manifest_path, source, candidate)}
    if any(path.is_relative_to(destination) for path in inputs):
        raise ValueError("output_dir must not contain source artifacts")
    context = manifest.get("context_rebuild") or {}
    groups = list(manifest.get("physical_parts", [])) + context.get("plan", {}).get("groups", [])
    if not groups or any(not row.get("join_id") or not row.get("source_node_ids") for row in groups):
        raise ValueError("candidate manifest must declare target parts and their source nodes")
    targets = {str(row["join_id"]) for row in groups}
    targets.update(context.get("adjacent_geometry", {}).get("requested_junction_ids", []))
    approaches = manifest.get("approach_rebuild") or {}
    targets.update(row["split_node_id"] for row in approaches.get("approaches", []) if row.get("split_node_id"))
    before, root = ET.parse(source).getroot(), ET.parse(candidate).getroot()
    source_targets = {str(node) for group in groups for node in group["source_node_ids"]}
    source_targets.update(targets & {row.get("id") for row in before.findall("junction")})
    missing_targets = sorted(targets - {row.get("id") for row in root.findall("junction")})
    destination.mkdir(parents=True)
    source_connection = audit_network_connection_mode(before, endpoint_tolerance_m=GEOMETRY_PRECISION_M)
    source_connection_file = destination / "source-connection-audit.json"
    write_json_atomic(source_connection_file, source_connection)
    connection = audit_network_connection_mode(root, endpoint_tolerance_m=GEOMETRY_PRECISION_M)
    connection_file = destination / "connection-audit.json"
    write_json_atomic(connection_file, connection)
    comparison = compare_connection_mode_audits(source_connection, connection,
        target_source_junction_ids=sorted(source_targets), target_candidate_junction_ids=sorted(targets))
    unlocated = {kind: max(0, connection[count] - sum(len(_junction_findings(row, finding_kind=kind))
                    for row in connection["junctions"]))
                 for kind, count in (("structural", "structural_failure_count"), ("review", "review_finding_count"))}
    structural_count = (comparison["target_scope_structural_finding_count"]
                        + comparison["outside_scope_new_structural_finding_count"] + unlocated["structural"])
    structural_blocked = (structural_count or missing_targets or comparison["outside_scope_missing_junction_ids"]
                          or comparison["outside_scope_added_junction_ids"]
                          or comparison["source_traffic_side"] != comparison["candidate_traffic_side"])
    structural = {"status": "blocked" if structural_blocked else "pass", "structural_failure_count": structural_count,
                  "global_status": connection["status"], "global_structural_failure_count": connection["structural_failure_count"],
                  "review_finding_count": connection["review_finding_count"], "missing_target_junctions": missing_targets,
                  "unlocated_structural_failure_count": unlocated["structural"],
                  "source_report_file": str(source_connection_file),
                  "report_file": str(connection_file), "report_sha256": file_sha256(connection_file)}
    connection_review_count = (comparison["target_scope_review_finding_count"]
                               + comparison["outside_scope_new_review_finding_count"] + unlocated["review"])
    inherited_review_count = max(0, connection["review_finding_count"] - connection_review_count)
    connection_review = {"status": "review_required" if connection_review_count else "pass",
                         "target_review_finding_count": comparison["target_scope_review_finding_count"],
                         "new_outside_review_finding_count": comparison["outside_scope_new_review_finding_count"],
                         "unlocated_review_finding_count": unlocated["review"],
                         "inherited_outside_review_finding_count": inherited_review_count,
                         "inherited_outside_status": "review_required" if inherited_review_count else "pass"}
    source_surface = audit_sumo_lane_junction_surface_overlaps(source, report_file=destination / "source-surface-audit.json")
    surface = audit_sumo_lane_junction_surface_overlaps(candidate, report_file=destination / "surface-audit.json")
    outside = _outside_preservation(before, root, groups, approaches)
    geometry = _geometry_review(manifest, root, surface, targets, set(outside["declared_approach_edge_ids"]),
                                before, source_surface, source_targets)
    connectivity = _official_connectivity(manifest, root)
    reload = _reload(candidate, root, targets, destination, netconvert_binary, timeout_seconds)
    unchanged = all(path.is_file() and file_sha256(path) == digest for path, digest in inputs.items())
    statuses = [row["status"] for row in (structural, connectivity, geometry, outside, reload, connection_review)]
    status = "blocked" if not unchanged or "blocked" in statuses else "review_required" if "review_required" in statuses else "pass"
    report = {"schema": "torii.hamburg-topology-audit/v1", "status": status, "decision": status,
              "claim_status": "diagnostic-demo", "inputs": {
                  role: {"path": str(path), "sha256": inputs[path]}
                  for role, path in (("manifest", manifest_path), ("source", source), ("candidate", candidate))},
              "inputs_unchanged": unchanged, "target_junction_ids": sorted(targets),
              "constructor_status": manifest.get("status"),
              "constructor_topology_complete": manifest.get("topology_complete"),
              "structural": structural, "official_connectivity": connectivity, "geometry_review": geometry,
              "connection_review": connection_review, "connection_comparison": comparison,
              "outside_scope_preservation": outside, "reload_stability": reload,
              "claim_boundary": "Construction and geometry decisions remain separate. A passed connection check "
                                "does not clear review items. No detector counts, routes for calibration, or "
                                "historical signal observations are loaded by this audit."}
    report_file = destination / "report.json"
    write_json_atomic(report_file, report)
    return {**report, "report_file": str(report_file), "report_sha256": file_sha256(report_file)}
