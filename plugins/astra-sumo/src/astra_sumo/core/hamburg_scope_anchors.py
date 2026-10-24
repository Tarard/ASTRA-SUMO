"""Derive study-retention evidence from one measured source correspondence."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .network_calibration import NetworkFactTable, _coordinate_transform, _shape, centerline_overlap_metrics


def write_hamburg_approach_retention(*, calibration_run_file, output_file):
    run_file = Path(calibration_run_file).resolve(strict=True)
    output = Path(output_file).resolve()
    if output.exists():
        raise ValueError("Use a new approach-retention output file.")
    run = json.loads(run_file.read_text(encoding="utf-8"))
    if run.get("schema") != "astra.hamburg-network-calibration-run/v1" or not run.get("sites"):
        raise ValueError("Use a Hamburg calibration snapshot with at least one site.")
    network = Path(run["network"]["path"]).resolve(strict=True)
    digest = file_sha256(network)
    root = ET.parse(network).getroot()
    owners = {lane.get("id"): edge.get("id") for edge in root.findall("edge") if not edge.get("function")
              for lane in edge.findall("lane")}
    shapes = {lane.get("id"): _shape(lane) for edge in root.findall("edge") if not edge.get("function")
              for lane in edge.findall("lane")}
    warnings = list(run.get("warnings", []))
    if digest != run["network"]["sha256"]:
        warnings.append("The source network differs from the calibration snapshot. These associations remain tentative.")
    anchors, inputs, counts, unresolved, unavailable = [], [], {}, {}, {}
    for node, site in sorted(run["sites"].items()):
        score_file = Path(site["scorecard"]).resolve(strict=True)
        facts_file = Path(site.get("facts_file", score_file.parent / f"{node}-facts.json")).resolve(strict=True)
        table = NetworkFactTable.model_validate_json(facts_file.read_text(encoding="utf-8"))
        score = json.loads(score_file.read_text(encoding="utf-8"))
        if score.get("site_id") != table.site_id:
            raise ValueError("The scorecard and fact table must describe the same site.")
        if score.get("inputs", {}).get(str(network)) != digest:
            warnings.append(f"Site {node} scorecard uses a different source network identity.")
        if score.get("inputs", {}).get(str(facts_file)) != file_sha256(facts_file):
            warnings.append(f"Site {node} fact table differs from its measured scorecard.")
        inputs.append({"node_id": node, "facts_sha256": file_sha256(facts_file), "scorecard_sha256": file_sha256(score_file)})
        rows = {row["id"]: row for row in score["facts"]}
        transform = _coordinate_transform(root, table.coordinate_system)
        projected = {key: transform(points) for key, points in shapes.items()}
        motor = [fact for fact in table.facts if fact.kind == "lane_centerline" and fact.traffic_mode == "motor"]
        unresolved[node], unavailable[node] = [], []
        usable = 0
        for fact in motor:
            parts = fact.selector.get("lane_ids", [fact.selector["lane_id"]] if "lane_id" in fact.selector else [])
            if not parts:
                unresolved[node].append(fact.id)
                continue
            measurement = rows[fact.id]
            if measurement["metrics"].get("mean_lateral_offset_m") is None:
                unavailable[node].append(fact.id)
                continue
            if fact.sample_spacing_m is None:
                raise ValueError("Approach centerlines require a sampling spacing.")
            usable += 1
            by_edge = defaultdict(list)
            for key in parts:
                if key not in owners:
                    warnings.append(f"Source lane {key} is absent from the current ordinary-road inventory.")
                    continue
                by_edge[owners[key]].append(key)
            reference = LineString(fact.expected)
            for edge, keys in sorted(by_edge.items()):
                metrics = centerline_overlap_metrics(reference, [projected[key] for key in keys], fact.sample_spacing_m,
                                                     direction=fact.reference_direction)
                for key in keys:
                    anchors.append({"official_fact": fact.id, "lane_id": key, "edge_id": edge,
                        "mapping_confidence": measurement["mapping_confidence"],
                        "scorecard_lane_reference_coverage": measurement["metrics"]["reference_coverage"],
                        "edge_reference_coverage": metrics["reference_coverage"],
                        "edge_mean_lateral_offset_m": metrics["mean_lateral_offset_m"],
                        "edge_covered_reference_length_m": metrics["covered_reference_length_m"],
                        "reference_length_m": metrics["reference_length_m"], "sample_spacing_m": fact.sample_spacing_m,
                        "edge_coverage_sample_count": metrics["coverage_sample_count"]})
        counts[node] = {"reference_motor_lanes": len(motor), "source_lane_parts_available": len(motor) - len(unresolved[node]),
                        "usable_overlap_retention_anchors": usable, "unmapped_source_lanes": len(unresolved[node]),
                        "source_lateral_offset_unavailable": len(unavailable[node])}
    evidence = {}
    for edge in sorted({row["edge_id"] for row in anchors}):
        supported = [row for row in anchors if row["edge_id"] == edge]
        confidence = sorted({row["mapping_confidence"] for row in supported})
        evidence[edge] = {"basis": "official_motor_approach_correspondence", "mapping_confidences": confidence,
            "has_high_confidence_fact_support": bool(set(confidence) & {"high", "confirmed"}),
            "official_fact_ids": sorted({row["official_fact"] for row in supported})}
    report = {"schema": "astra.hamburg-approach-retention/v1", "status": "measured", "promotion_authority": False,
        "generator": "astra_sumo.core.hamburg_scope_anchors.write_hamburg_approach_retention",
        "source_network_content_sha256": hashlib.sha256(ET.canonicalize(network.read_text(encoding="utf-8")).encode("utf-8")).hexdigest(),
        "inputs": {"calibration_run_sha256": file_sha256(run_file), "measured_network_sha256": digest,
                   "snapshot_network_sha256": run["network"]["sha256"], "sites": inputs},
        "retention_basis": "Retain saved ordinary parts of source motor-lane associations with measured directed overlap. Confidence does not gate retention.",
        "protected_edge_ids": sorted(evidence), "edge_evidence": evidence, "anchors": anchors,
        "motor_lane_counts": counts, "unresolved_motor_lane_facts": unresolved,
        "lateral_offset_unavailable_motor_lane_facts": unavailable, "warnings": warnings,
        "source_unchanged": file_sha256(network) == digest,
        "claim_boundary": "Tentative source-retention evidence. Whole-lane coverage and individual-edge support are separate. No independent field validation."}
    write_json_atomic(output, report, sort_keys=True)
    return report
