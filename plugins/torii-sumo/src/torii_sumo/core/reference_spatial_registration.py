from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256


SCHEMA = "torii.reference-spatial-registration/v1"
DEFAULT_CORE_RESIDUAL_M = 10.0


@dataclass(frozen=True)
class NetworkCoordinateFrame:
    net_offset_x: float
    net_offset_y: float
    projection: str

    def local_to_projected(self, point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] - self.net_offset_x, point[1] - self.net_offset_y)

    def projected_to_local(self, point: tuple[float, float]) -> tuple[float, float]:
        return (point[0] + self.net_offset_x, point[1] + self.net_offset_y)


def build_reference_spatial_registration(
    *,
    teacher_action_contracts_file: str | Path,
    raw_net_file: str | Path,
    teacher_net_file: str | Path,
    output_dir: str | Path,
    core_residual_m: float = DEFAULT_CORE_RESIDUAL_M,
) -> dict[str, Any]:
    """Register raw and human-cleaned SUMO networks in one projected frame.

    SUMO junction coordinates are local to each network's ``netOffset``.  A raw
    junction ID and a human ``cluster_*`` ID must therefore never be compared or
    screenshotted in their local coordinate systems.  This report creates one
    projected location per teacher action and the two exact local view centers
    needed for a geographically aligned A/B review.
    """

    if core_residual_m <= 0:
        raise ValueError("core_residual_m must be positive")
    action_path = Path(teacher_action_contracts_file).expanduser().resolve(strict=True)
    raw_path = Path(raw_net_file).expanduser().resolve(strict=True)
    teacher_path = Path(teacher_net_file).expanduser().resolve(strict=True)
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    actions = _read_json_object(action_path, "teacher action contracts")
    if actions.get("schema") != "torii.reference_teacher_action_contracts.v1":
        raise ValueError("teacher action contracts must use the v1 reference schema")
    raw_frame, raw_junctions = _read_network(raw_path)
    teacher_frame, teacher_junctions = _read_network(teacher_path)
    if _normalized_projection(raw_frame.projection) != _normalized_projection(
        teacher_frame.projection
    ):
        raise ValueError("raw and human-cleaned networks use different projections")

    cases: list[dict[str, Any]] = []
    for action in actions.get("actions", []):
        if not isinstance(action, dict):
            continue
        reference_id = str(action.get("reference_id", ""))
        source_ids = [
            str(value)
            for value in action.get("teacher_action", {}).get(
                "absorbed_source_node_ids", []
            )
        ]
        source_points = [raw_junctions[node_id] for node_id in source_ids if node_id in raw_junctions]
        missing_source_ids = [node_id for node_id in source_ids if node_id not in raw_junctions]
        teacher_point = teacher_junctions.get(reference_id)
        if teacher_point is None or not source_points:
            cases.append(
                {
                    "reference_id": reference_id,
                    "action_family": str(action.get("action_family", "")),
                    "status": "blocked",
                    "reason": (
                        "human junction is missing"
                        if teacher_point is None
                        else "no declared source junction is present in the raw network"
                    ),
                    "declared_source_node_ids": source_ids,
                    "missing_source_node_ids": missing_source_ids,
                    "promotion_gate_status": "blocked",
                }
            )
            continue

        raw_centroid = _centroid(source_points)
        raw_projected_centroid = raw_frame.local_to_projected(raw_centroid)
        teacher_projected_center = teacher_frame.local_to_projected(teacher_point)
        centroid_residual_m = math.dist(raw_projected_centroid, teacher_projected_center)
        source_radius_m = max(math.dist(raw_centroid, point) for point in source_points)
        aligned_raw_center = raw_frame.projected_to_local(teacher_projected_center)
        aligned_teacher_center = teacher_frame.projected_to_local(teacher_projected_center)
        action_family = str(action.get("action_family", ""))
        exact_core_example = (
            action_family == "bounded_conflict_core_join"
            and not missing_source_ids
            and centroid_residual_m <= core_residual_m
        )
        cases.append(
            {
                "reference_id": reference_id,
                "action_family": action_family,
                "status": "registered",
                "reason": "both networks resolve to one projected location",
                "declared_source_node_ids": source_ids,
                "missing_source_node_ids": missing_source_ids,
                "source_identity_complete": not missing_source_ids,
                "raw_source_centroid_local": _point(raw_centroid),
                "raw_source_centroid_projected": _point(raw_projected_centroid),
                "human_junction_center_local": _point(teacher_point),
                "human_junction_center_projected": _point(teacher_projected_center),
                "centroid_residual_m": round(centroid_residual_m, 6),
                "source_radius_m": round(source_radius_m, 6),
                "spatial_relation": (
                    "coincident_core"
                    if centroid_residual_m <= 3.0
                    else "bounded_local_core"
                    if centroid_residual_m <= core_residual_m
                    else "extended_or_review_case"
                ),
                "exact_core_teaching_example": exact_core_example,
                "aligned_view": {
                    "projected_center": _point(teacher_projected_center),
                    "raw_local_center": _point(aligned_raw_center),
                    "human_cleaned_local_center": _point(aligned_teacher_center),
                    "projected_center_residual_m": 0.0,
                },
                "promotion_gate_status": "blocked",
            }
        )

    report = {
        "schema": SCHEMA,
        "status": "review_ready" if cases else "blocked",
        "claim_status": "geographic-registration-evidence",
        "projection": raw_frame.projection,
        "coordinate_frames": {
            "raw": _frame(raw_frame),
            "human_cleaned": _frame(teacher_frame),
            "human_minus_raw_net_offset": [
                round(teacher_frame.net_offset_x - raw_frame.net_offset_x, 6),
                round(teacher_frame.net_offset_y - raw_frame.net_offset_y, 6),
            ],
        },
        "source_artifacts": {
            "teacher_action_contracts": _artifact(action_path),
            "raw_net": _artifact(raw_path),
            "human_cleaned_net": _artifact(teacher_path),
        },
        "case_count": len(cases),
        "status_counts": dict(sorted(Counter(case["status"] for case in cases).items())),
        "spatial_relation_counts": dict(
            sorted(
                Counter(
                    str(case.get("spatial_relation", "blocked")) for case in cases
                ).items()
            )
        ),
        "exact_core_teaching_example_count": sum(
            bool(case.get("exact_core_teaching_example")) for case in cases
        ),
        "cases": cases,
        "screenshot_policy": (
            "raw and human screenshots must use the two local centers derived from the same "
            "projected_center; separately centering each junction is invalid"
        ),
        "automatic_promotion_gate": "blocked",
        "promotion_gate_reason": (
            "spatial registration proves location identity only; it does not authorize a network edit"
        ),
    }
    report_file = destination / "ingolstadt-reference-spatial-registration.json"
    write_json_atomic(report_file, report, ensure_ascii=False, sort_keys=True)
    return {
        **report,
        "report_file": str(report_file),
        "report_sha256": file_sha256(report_file),
    }


def read_network_coordinate_frame(path: str | Path) -> NetworkCoordinateFrame:
    frame, _junctions = _read_network(Path(path).expanduser().resolve(strict=True))
    return frame


def _read_network(path: Path) -> tuple[NetworkCoordinateFrame, dict[str, tuple[float, float]]]:
    root = ET.parse(path).getroot()
    location = root.find("location")
    if location is None:
        raise ValueError(f"SUMO network has no location metadata: {path}")
    offset_text = location.get("netOffset", "")
    projection = location.get("projParameter", "").strip()
    try:
        offset_x, offset_y = (float(value) for value in offset_text.split(","))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SUMO network has an invalid netOffset: {path}") from exc
    if not projection or projection == "!":
        raise ValueError(f"SUMO network has no usable projected CRS: {path}")
    junctions: dict[str, tuple[float, float]] = {}
    for junction in root.findall("junction"):
        junction_id = junction.get("id", "")
        try:
            point = (float(junction.get("x", "")), float(junction.get("y", "")))
        except ValueError:
            continue
        if junction_id:
            junctions[junction_id] = point
    return NetworkCoordinateFrame(offset_x, offset_y, projection), junctions


def _normalized_projection(value: str) -> str:
    return " ".join(value.split()).lower()


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _point(point: tuple[float, float]) -> list[float]:
    return [round(point[0], 6), round(point[1], 6)]


def _frame(frame: NetworkCoordinateFrame) -> dict[str, Any]:
    return {
        "net_offset": [frame.net_offset_x, frame.net_offset_y],
        "projection": frame.projection,
    }


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _artifact(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": file_sha256(resolved),
    }
