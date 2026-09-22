from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.netedit import _which_netedit
from torii_sumo.core.tls_ownership import audit_tls_ownership_rebuild


PW_RENDERFULLCONTENT = 2
SW_SHOWNOACTIVATE = 4
SW_MAXIMIZE = 3
HAMBURG_OFFICIAL_WORKFLOW_SCHEMA_ID = "torii.hamburg-official-tls-workflow.v1"
HAMBURG_OFFICIAL_PRESET_ID = "hamburg-sandtorkai-0228-2421-2394"
HAMBURG_OFFICIAL_NODE_IDS = ("0228", "2421", "2394")
HAMBURG_OFFICIAL_MOVEMENT_COUNTS = {"0228": 16, "2421": 9, "2394": 8}
_DPI_AWARENESS: dict[str, Any] | None = None


@dataclass(frozen=True)
class TargetJunction:
    junction_id: str
    x: float
    y: float
    incoming_lanes: tuple[str, ...]
    junction_type: str
    junction_shape: str


@dataclass(frozen=True)
class CaptureRequest:
    name: str
    mode: str
    selection_type: str
    selection_id: str


@dataclass(frozen=True)
class CandidateReviewIdentity:
    candidate_role: str
    summary_file: Path
    manifest_file: Path
    candidate_evidence_file: Path | None
    source_file: Path
    candidate_file: Path
    review_overlay_file: Path | None
    source_sha256: str
    candidate_sha256: str
    target_junction_id: str
    tls_ownership_recheck: dict[str, Any]


@dataclass(frozen=True)
class HamburgOfficialReviewTarget:
    official_node_id: str
    junction_id: str
    controller_id: str


@dataclass(frozen=True)
class HamburgOfficialReviewIdentity:
    manifest_file: Path
    source_file: Path
    candidate_file: Path
    source_sha256: str
    candidate_sha256: str
    targets: tuple[HamburgOfficialReviewTarget, ...]
    artifact_hash_recheck: tuple[dict[str, Any], ...]
    official_asset_hash_recheck: tuple[dict[str, Any], ...]
    candidate_tls_recheck: dict[str, Any]


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read {label} JSON at {path}.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return value


def _recorded_path(value: Any, *, base_dir: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} path is missing from the workflow summary.")
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    try:
        return path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _require_recorded_hash(
    *,
    path: Path,
    expected_hash: Any,
    label: str,
) -> str:
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise ValueError(f"{label} SHA-256 is missing or malformed.")
    observed_hash = file_sha256(path)
    if observed_hash != expected_hash:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected_hash}, observed {observed_hash}.")
    return observed_hash


def _require_manifest_binding(
    *,
    manifest: dict[str, Any],
    manifest_dir: Path,
    artifact_path: Path,
    expected_hash: str,
    label: str,
) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Manifest artifacts must be a JSON array.")
    matching_hashes: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        recorded = Path(raw_path)
        if not recorded.is_absolute():
            recorded = manifest_dir / recorded
        if _path_key(recorded) == _path_key(artifact_path):
            matching_hashes.append(str(artifact.get("sha256", "")))
    if not matching_hashes:
        raise ValueError(f"Manifest does not bind the {label}: {artifact_path}")
    if expected_hash not in matching_hashes:
        raise ValueError(f"Manifest hash mismatch for {label}: expected {expected_hash}, recorded {matching_hashes}.")


def _verified_hamburg_artifacts(
    *,
    manifest: dict[str, Any],
    manifest_dir: Path,
) -> tuple[dict[str, Any], ...]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Hamburg manifest artifacts must be a non-empty JSON array.")
    verified: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise ValueError(f"Hamburg manifest artifact {index} must be a JSON object.")
        role = artifact.get("role")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"Hamburg manifest artifact {index} lacks a role.")
        path = _recorded_path(
            artifact.get("path"),
            base_dir=manifest_dir,
            label=f"Hamburg artifact {role}",
        )
        key = _path_key(path)
        if key in seen_paths:
            raise ValueError(f"Hamburg manifest repeats artifact path: {path}")
        seen_paths.add(key)
        observed_hash = _require_recorded_hash(
            path=path,
            expected_hash=artifact.get("sha256"),
            label=f"Hamburg artifact {role}",
        )
        verified.append(
            {
                "role": role,
                "path": str(path),
                "sha256": observed_hash,
            }
        )
    return tuple(verified)


def _verified_hamburg_official_assets(
    *,
    manifest: dict[str, Any],
    manifest_dir: Path,
) -> tuple[dict[str, Any], ...]:
    inventory = manifest.get("official_asset_inventory")
    if not isinstance(inventory, list):
        raise ValueError("Hamburg manifest lacks the official MAP/OCIT asset inventory.")
    expected_keys = {
        (node_id, kind)
        for node_id in HAMBURG_OFFICIAL_NODE_IDS
        for kind in ("map_xml", "ocit_xml")
    }
    observed_keys: set[tuple[str, str]] = set()
    verified: list[dict[str, Any]] = []
    for index, artifact in enumerate(inventory):
        if not isinstance(artifact, dict):
            raise ValueError(f"Hamburg official asset {index} must be a JSON object.")
        key = (str(artifact.get("node_id", "")), str(artifact.get("kind", "")))
        if key in observed_keys:
            raise ValueError(f"Hamburg official asset inventory repeats {key[0]}/{key[1]}.")
        observed_keys.add(key)
        path = _recorded_path(
            artifact.get("path"),
            base_dir=manifest_dir,
            label=f"Hamburg official asset {key[0]}/{key[1]}",
        )
        observed_hash = _require_recorded_hash(
            path=path,
            expected_hash=artifact.get("sha256"),
            label=f"Hamburg official asset {key[0]}/{key[1]}",
        )
        verified.append(
            {
                "node_id": key[0],
                "kind": key[1],
                "path": str(path),
                "sha256": observed_hash,
            }
        )
    if observed_keys != expected_keys:
        raise ValueError(
            "Hamburg official asset inventory must contain exactly one MAP and OCIT asset "
            f"for every corridor node; observed {sorted(observed_keys)}."
        )
    return tuple(verified)


def _hamburg_stage_targets(manifest: dict[str, Any]) -> tuple[HamburgOfficialReviewTarget, ...]:
    rebuild = manifest.get("network_rebuild")
    if not isinstance(rebuild, dict) or rebuild.get("status") != "pass":
        raise ValueError("Hamburg network rebuild gate is not pass.")
    if rebuild.get("controller_count") != 3:
        raise ValueError("Hamburg network rebuild must contain exactly three official controllers.")
    stages = rebuild.get("stage_reports")
    if not isinstance(stages, list) or len(stages) != 3:
        raise ValueError("Hamburg network rebuild must contain exactly three replay stages.")
    by_node: dict[str, HamburgOfficialReviewTarget] = {}
    for stage in stages:
        if not isinstance(stage, dict) or stage.get("status") != "pass":
            raise ValueError("Every Hamburg native replay stage must be pass.")
        teacher_controller = str(stage.get("controller_id", ""))
        if not teacher_controller.startswith("HH_"):
            raise ValueError(f"Unexpected Hamburg teacher controller id: {teacher_controller!r}")
        official_node_id = teacher_controller.removeprefix("HH_").zfill(4)
        replay = stage.get("native_teacher_replay")
        if not isinstance(replay, dict) or replay.get("status") != "pass":
            raise ValueError(f"Hamburg native replay for {official_node_id} is not pass.")
        junction_id = str(replay.get("junction_id", "")).strip()
        if not junction_id:
            raise ValueError(f"Hamburg native replay for {official_node_id} lacks a junction id.")
        if official_node_id in by_node:
            raise ValueError(f"Hamburg replay repeats official node {official_node_id}.")
        by_node[official_node_id] = HamburgOfficialReviewTarget(
            official_node_id=official_node_id,
            junction_id=junction_id,
            controller_id=junction_id,
        )
    if set(by_node) != set(HAMBURG_OFFICIAL_NODE_IDS):
        raise ValueError(
            "Hamburg replay stages do not cover exactly the three corridor nodes: "
            f"{sorted(by_node)}."
        )
    return tuple(by_node[node_id] for node_id in HAMBURG_OFFICIAL_NODE_IDS)


def _audit_hamburg_candidate_tls(
    candidate_file: Path,
    targets: tuple[HamburgOfficialReviewTarget, ...],
) -> dict[str, Any]:
    root = ET.parse(candidate_file).getroot()
    logic_by_id = {
        str(logic.get("id", "")): logic
        for logic in root.findall("tlLogic")
        if logic.get("id")
    }
    expected_controller_ids = {target.controller_id for target in targets}
    junction_by_id = {
        str(junction.get("id", "")): junction
        for junction in root.findall("junction")
        if junction.get("id")
    }
    controlled_connections = [
        connection
        for connection in root.findall("connection")
        if connection.get("tl")
    ]
    counts_by_controller: dict[str, int] = {
        controller_id: 0 for controller_id in expected_controller_ids
    }
    errors: list[str] = []
    for connection in controlled_connections:
        controller_id = str(connection.get("tl", ""))
        if controller_id in counts_by_controller:
            counts_by_controller[controller_id] += 1
        link_index = connection.get("linkIndex")
        if link_index is None or not link_index.isdigit():
            errors.append(f"controlled connection for {controller_id} lacks a valid linkIndex")
            continue
        logic = logic_by_id.get(controller_id)
        if logic is None:
            errors.append(f"controlled connection references missing tlLogic {controller_id}")
            continue
        phase_lengths = {
            len(str(phase.get("state", ""))) for phase in logic.findall("phase")
        }
        if not phase_lengths or len(phase_lengths) != 1:
            errors.append(f"tlLogic {controller_id} has no phases or inconsistent state lengths")
        elif int(link_index) >= next(iter(phase_lengths)):
            errors.append(f"tlLogic {controller_id} linkIndex {link_index} exceeds phase state length")
    if set(logic_by_id) != expected_controller_ids:
        errors.append(
            "candidate tlLogic ids are not exactly the three official controllers: "
            f"{sorted(logic_by_id)}"
        )
    for target in targets:
        junction = junction_by_id.get(target.junction_id)
        if junction is None:
            errors.append(f"official target junction {target.junction_id} is missing")
        elif junction.get("type") != "traffic_light":
            errors.append(f"official target junction {target.junction_id} is not traffic_light")
        expected_count = HAMBURG_OFFICIAL_MOVEMENT_COUNTS[target.official_node_id]
        observed_count = counts_by_controller[target.controller_id]
        if observed_count != expected_count:
            errors.append(
                f"official node {target.official_node_id} controls {observed_count} connections; "
                f"expected {expected_count}"
            )
    if len(controlled_connections) != sum(HAMBURG_OFFICIAL_MOVEMENT_COUNTS.values()):
        errors.append(
            f"candidate has {len(controlled_connections)} controlled connections; expected 33"
        )
    return {
        "status": "pass" if not errors else "fail",
        "controller_ids": sorted(logic_by_id),
        "expected_controller_ids": sorted(expected_controller_ids),
        "controlled_connection_count": len(controlled_connections),
        "controlled_connection_counts_by_controller": dict(sorted(counts_by_controller.items())),
        "errors": errors,
    }


def load_bound_hamburg_official_identity(
    *,
    manifest_file: Path,
) -> HamburgOfficialReviewIdentity:
    """Resolve the three-junction Hamburg candidate from its fail-closed official manifest."""

    manifest_path = manifest_file.resolve(strict=True)
    manifest = _read_json_object(manifest_path, "Hamburg official TLS manifest")
    required_values = {
        "schema_id": HAMBURG_OFFICIAL_WORKFLOW_SCHEMA_ID,
        "preset_id": HAMBURG_OFFICIAL_PRESET_ID,
        "status": "pass",
        "claim_status": "official-tls-topology-ready",
        "stage": "complete",
        "automatic_promotion_gate": "blocked",
    }
    for key, expected in required_values.items():
        if manifest.get(key) != expected:
            raise ValueError(
                f"Hamburg manifest {key} must be {expected!r}; observed {manifest.get(key)!r}."
            )
    required_pass_gates = (
        "native_teacher_derivation",
        "network_rebuild",
        "native_geometry_continuity_audit",
        "post_retirement_sumo_load_audit",
        "effective_map_lane_binding_audit",
        "official_movement_physical_endpoint_audit",
        "primary_stream_tls_binding_audit",
        "vehicle_topology_inventory_audit",
        "ocit_group_validation",
        "tld_observed_group_subset_audit",
    )
    for key in required_pass_gates:
        gate = manifest.get(key)
        if not isinstance(gate, dict) or gate.get("status") != "pass":
            raise ValueError(f"Hamburg hard gate {key} is not pass.")
    rebuild_gate = manifest["network_rebuild"]
    if rebuild_gate.get("post_replay_compact_scope_tls_retirement_status") != "pass":
        raise ValueError("Hamburg compact-scope source TLS retirement gate is not pass.")
    if manifest.get("source_net_unchanged") is not True:
        raise ValueError("Hamburg manifest does not prove the frozen source network remained unchanged.")
    if manifest.get("expected_vehicle_topology_movement_count") != 33:
        raise ValueError("Hamburg manifest does not declare the complete 33-movement topology.")
    if manifest.get("expected_primary_stream_count") != 27:
        raise ValueError("Hamburg manifest does not declare the complete 27-stream observation inventory.")
    movement_gate = manifest["official_movement_physical_endpoint_audit"]
    movement_gate_expected = {
        "movement_count": 33,
        "validated_movement_count": 33,
        "unique_physical_endpoint_count": 33,
    }
    for key, expected in movement_gate_expected.items():
        if movement_gate.get(key) != expected:
            raise ValueError(
                f"Hamburg movement endpoint gate {key} must be {expected}; "
                f"observed {movement_gate.get(key)!r}."
            )
    replay_evidence = movement_gate.get("replay_control_evidence")
    if not isinstance(replay_evidence, dict) or replay_evidence.get("status") != "pass":
        raise ValueError("Hamburg movement endpoint replay-control evidence is not pass.")

    source = _recorded_path(
        manifest.get("source_net_file"),
        base_dir=manifest_path.parent,
        label="Hamburg source network",
    )
    source_before = manifest.get("source_net_sha256_before")
    source_after = manifest.get("source_net_sha256_after")
    if source_before != source_after:
        raise ValueError("Hamburg source network before/after hashes disagree.")
    source_hash = _require_recorded_hash(
        path=source,
        expected_hash=source_before,
        label="Hamburg source network",
    )
    candidate = _recorded_path(
        manifest.get("rebuilt_net_file"),
        base_dir=manifest_path.parent,
        label="Hamburg rebuilt network",
    )
    if _path_key(source) == _path_key(candidate):
        raise ValueError("Hamburg review refused: rebuilt network points at the frozen source baseline.")
    artifact_recheck = _verified_hamburg_artifacts(
        manifest=manifest,
        manifest_dir=manifest_path.parent,
    )
    candidate_hash = file_sha256(candidate)
    _require_manifest_binding(
        manifest=manifest,
        manifest_dir=manifest_path.parent,
        artifact_path=candidate,
        expected_hash=candidate_hash,
        label="Hamburg rebuilt network",
    )
    rebuild = manifest["network_rebuild"]
    rebuild_final = _recorded_path(
        rebuild.get("final_net_file"),
        base_dir=manifest_path.parent,
        label="Hamburg native replay final network",
    )
    if _path_key(rebuild_final) != _path_key(candidate):
        raise ValueError("Hamburg top-level rebuilt network disagrees with native replay final network.")
    targets = _hamburg_stage_targets(manifest)
    candidate_tls_recheck = _audit_hamburg_candidate_tls(candidate, targets)
    if candidate_tls_recheck["status"] != "pass":
        raise ValueError(
            "Hamburg candidate TLS recheck failed: "
            + json.dumps(candidate_tls_recheck["errors"], sort_keys=True)
        )
    official_asset_recheck = _verified_hamburg_official_assets(
        manifest=manifest,
        manifest_dir=manifest_path.parent,
    )
    return HamburgOfficialReviewIdentity(
        manifest_file=manifest_path,
        source_file=source,
        candidate_file=candidate,
        source_sha256=source_hash,
        candidate_sha256=candidate_hash,
        targets=targets,
        artifact_hash_recheck=artifact_recheck,
        official_asset_hash_recheck=official_asset_recheck,
        candidate_tls_recheck=candidate_tls_recheck,
    )


def load_bound_candidate_identity(
    *,
    summary_file: Path,
    manifest_file: Path | None = None,
    candidate_role: str = "primary",
) -> CandidateReviewIdentity:
    """Resolve one review candidate from a hash-bound workflow bundle."""

    summary_path = summary_file.resolve(strict=True)
    summary = _read_json_object(summary_path, "workflow summary")
    if summary.get("automatic_promotion_gate") != "blocked":
        raise ValueError("Workflow summary must keep automatic promotion blocked.")
    if candidate_role not in {"primary", "nema-topology"}:
        raise ValueError(f"Unsupported candidate review role: {candidate_role}")

    source = _recorded_path(
        summary.get("source_net_file"),
        base_dir=summary_path.parent,
        label="source network",
    )
    candidate_evidence: Path | None = None
    candidate_record: dict[str, Any] = summary
    if candidate_role == "nema-topology":
        topology_summary = summary.get("tls_topology")
        if not isinstance(topology_summary, dict):
            raise ValueError("Workflow summary lacks a TLS topology stage.")
        if topology_summary.get("status") != "candidate_ready_for_review":
            raise ValueError("NEMA topology review requires candidate_ready_for_review.")
        candidate_evidence = _recorded_path(
            topology_summary.get("artifact_file"),
            base_dir=summary_path.parent,
            label="NEMA topology evidence",
        )
        candidate_record = _read_json_object(candidate_evidence, "NEMA topology evidence")
        if candidate_record.get("automatic_promotion_gate") != "blocked":
            raise ValueError("NEMA topology evidence must keep automatic promotion blocked.")
        if candidate_record.get("status") != "candidate_ready_for_review":
            raise ValueError("NEMA topology evidence is not review-ready.")
    candidate = _recorded_path(
        candidate_record.get("candidate_net_file"),
        base_dir=summary_path.parent,
        label=f"{candidate_role} candidate network",
    )
    if _path_key(source) == _path_key(candidate):
        raise ValueError(
            "Candidate review refused: the summary points candidate_file at the immutable source baseline."
        )

    source_hash = _require_recorded_hash(
        path=source,
        expected_hash=summary.get("source_sha256"),
        label="source network",
    )
    candidate_hash = _require_recorded_hash(
        path=candidate,
        expected_hash=candidate_record.get("candidate_sha256"),
        label=f"{candidate_role} candidate network",
    )
    if source_hash == candidate_hash:
        raise ValueError("Candidate review refused: candidate content is identical to the immutable source baseline.")

    manifest_path = (
        manifest_file.resolve(strict=True)
        if manifest_file is not None
        else (summary_path.parent / "manifest.json").resolve(strict=True)
    )
    manifest = _read_json_object(manifest_path, "artifact manifest")
    if manifest.get("automatic_promotion_gate") != "blocked":
        raise ValueError("Artifact manifest must keep automatic promotion blocked.")

    _require_manifest_binding(
        manifest=manifest,
        manifest_dir=manifest_path.parent,
        artifact_path=summary_path,
        expected_hash=file_sha256(summary_path),
        label="workflow summary",
    )
    _require_manifest_binding(
        manifest=manifest,
        manifest_dir=manifest_path.parent,
        artifact_path=source,
        expected_hash=source_hash,
        label="source network",
    )
    _require_manifest_binding(
        manifest=manifest,
        manifest_dir=manifest_path.parent,
        artifact_path=candidate,
        expected_hash=candidate_hash,
        label="candidate network",
    )
    if candidate_evidence is not None:
        _require_manifest_binding(
            manifest=manifest,
            manifest_dir=manifest_path.parent,
            artifact_path=candidate_evidence,
            expected_hash=file_sha256(candidate_evidence),
            label="NEMA topology evidence",
        )

    tls_ownership = summary.get("tls_ownership")
    if not isinstance(tls_ownership, dict):
        raise ValueError("Workflow summary does not contain a TLS ownership report.")
    if tls_ownership.get("status") != "pass":
        raise ValueError("Stored TLS ownership gate is not pass.")
    source_junction_ids = tls_ownership.get("target_source_junction_ids")
    if not isinstance(source_junction_ids, list) or not source_junction_ids:
        raise ValueError(
            "TLS ownership report lacks stable target_source_junction_ids; "
            "rerun the isolated-junction workflow before visual review."
        )
    target_junction_id = str(tls_ownership.get("target_candidate_junction_id", ""))
    expected_controller_ids = tls_ownership.get("expected_controller_ids")
    expected_connection_count = tls_ownership.get("expected_controlled_connection_count")
    if not target_junction_id:
        raise ValueError("TLS ownership report lacks a target candidate junction.")
    if not isinstance(expected_controller_ids, list) or not expected_controller_ids:
        raise ValueError("TLS ownership report lacks expected controller IDs.")
    if not isinstance(expected_connection_count, int):
        raise ValueError("TLS ownership report lacks the expected movement count.")

    recheck = audit_tls_ownership_rebuild(
        source_net=source,
        candidate_net=candidate,
        target_source_junction_ids=tuple(map(str, source_junction_ids)),
        target_candidate_junction_id=target_junction_id,
        expected_controller_ids=tuple(map(str, expected_controller_ids)),
        expected_controlled_connection_count=expected_connection_count,
        report_schema="torii.netedit-candidate-tls-identity/v1",
    )
    if recheck["status"] != "pass":
        raise ValueError("Candidate TLS ownership recheck failed: " + json.dumps(recheck["findings"], sort_keys=True))
    if candidate_role == "primary":
        for key in (
            "candidate",
            "residual_source_junction_ids",
            "residual_old_controller_ids",
        ):
            if tls_ownership.get(key) != recheck.get(key):
                raise ValueError(f"Stored TLS ownership evidence disagrees with candidate: {key}.")
    else:
        topology_tls = candidate_record.get("tls_ownership")
        if not isinstance(topology_tls, dict):
            raise ValueError("NEMA topology evidence lacks TLS ownership proof.")
        observed_topology_tls = {
            "status": recheck["status"],
            "controller_ids": recheck["candidate"]["target_controller_ids"],
            "controlled_connection_count": recheck["candidate"]["target_controlled_connection_count"],
            "signal_group_count": recheck["candidate"]["target_signal_group_count"],
        }
        for key, value in observed_topology_tls.items():
            if topology_tls.get(key) != value:
                raise ValueError(f"NEMA TLS ownership evidence disagrees with candidate: {key}.")

    overlay: Path | None = None
    overlay_value: Any = summary.get("review_overlay_file")
    if candidate_role == "nema-topology":
        standard = candidate_record.get("standard_builder") or {}
        overlay_value = standard.get("review_overlay_file")
    if overlay_value:
        overlay = _recorded_path(
            overlay_value,
            base_dir=summary_path.parent,
            label="review overlay",
        )
        _require_manifest_binding(
            manifest=manifest,
            manifest_dir=manifest_path.parent,
            artifact_path=overlay,
            expected_hash=file_sha256(overlay),
            label="review overlay",
        )

    return CandidateReviewIdentity(
        candidate_role=candidate_role,
        summary_file=summary_path,
        manifest_file=manifest_path,
        candidate_evidence_file=candidate_evidence,
        source_file=source,
        candidate_file=candidate,
        review_overlay_file=overlay,
        source_sha256=source_hash,
        candidate_sha256=candidate_hash,
        target_junction_id=target_junction_id,
        tls_ownership_recheck=recheck,
    )


def read_target_junction(net_file: Path, junction_id: str) -> TargetJunction:
    root = ET.parse(net_file).getroot()
    for junction in root.findall("junction"):
        if junction.get("id") != junction_id:
            continue
        return TargetJunction(
            junction_id=junction_id,
            x=float(junction.get("x", "0")),
            y=float(junction.get("y", "0")),
            incoming_lanes=tuple(junction.get("incLanes", "").split()),
            junction_type=str(junction.get("type", "")),
            junction_shape=str(junction.get("shape", "")),
        )
    raise ValueError(f"Target junction {junction_id!r} is not present in {net_file}.")


def parse_point(value: str) -> tuple[float, float]:
    left, right = value.split(",", 1)
    return float(left), float(right)


def parse_size(value: str) -> tuple[int, int]:
    left, right = value.split(",", 1)
    return int(left), int(right)


def _view_shape_points(value: str) -> list[tuple[float, float]]:
    return [parse_point(",".join(token.split(",")[:2])) for token in value.split()]


def _view_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    if not points or any(not math.isfinite(value) for point in points for value in point):
        raise ValueError("Cannot fit an empty or non-finite network viewport.")
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def _network_view_bounds(root: ET.Element) -> tuple[tuple[float, float, float, float], str]:
    location = root.find("location")
    if location is not None and location.attrib.get("convBoundary"):
        values = [float(value) for value in location.attrib["convBoundary"].split(",")]
        if len(values) != 4 or any(not math.isfinite(value) for value in values) or values[2] < values[0] or values[3] < values[1]:
            raise ValueError("Network convBoundary must contain four ordered, finite coordinates.")
        return tuple(values), "convBoundary_estimate"
    points = []
    for junction in root.findall("junction"):
        if junction.get("x") is not None and junction.get("y") is not None:
            points.append((float(junction.get("x")), float(junction.get("y"))))
        points.extend(_view_shape_points(junction.get("shape", "")))
    for edge in root.findall("edge"):
        if edge.get("function") != "internal":
            for lane in edge.findall("lane"):
                points.extend(_view_shape_points(lane.get("shape", "")))
    return _view_bounds(points), "network_geometry_estimate"


def _view_lane_context(shape: list[tuple[float, float]], distance_m: float) -> list[tuple[float, float]]:
    if len(shape) < 2:
        return shape
    points, remaining = [shape[0]], distance_m
    for a, b in zip(shape, shape[1:]):
        length = math.dist(a, b)
        if length > remaining:
            points.append(tuple(a[i] + remaining / length * (b[i] - a[i]) for i in (0, 1)))
            break
        points.append(b)
        remaining -= length
    return points


def fitted_viewport(net_file: Path, *, target: TargetJunction | None = None,
                    center: tuple[float, float] | None = None, zoom: float | None = None) -> dict[str, Any]:
    """Fit world-coordinate bounds using SUMO's percentage zoom, not pixels."""
    if zoom is not None and (not math.isfinite(zoom) or zoom <= 0):
        raise ValueError("NetEdit zoom must be a positive, finite percentage.")
    root = ET.parse(net_file).getroot()
    reference, reference_basis = _network_view_bounds(root)
    bounds = reference
    context_distance = 30.0  # Display context, not a traffic-model parameter.
    if target is not None:
        points = _view_shape_points(target.junction_shape) or [(target.x, target.y)]
        for edge in root.findall("edge"):
            for attribute, reverse in (("to", True), ("from", False)):
                if edge.get(attribute) != target.junction_id:
                    continue
                for lane in edge.findall("lane"):
                    shape = _view_shape_points(lane.get("shape", ""))
                    width = float(lane.get("width", "3.2"))
                    if not math.isfinite(width) or width <= 0:
                        raise ValueError("Lane width must be positive and finite for viewport fitting.")
                    for x, y in _view_lane_context(list(reversed(shape)) if reverse else shape, context_distance):
                        points.extend(((x - width / 2, y - width / 2), (x + width / 2, y + width / 2)))
        bounds = _view_bounds(points)
    chosen_center = center or ((target.x, target.y) if zoom is not None and target is not None else ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2))
    if any(not math.isfinite(value) for value in chosen_center):
        raise ValueError("Viewport center must contain finite coordinates.")
    spans = [max(reference[i + 2] - reference[i], 1.0) for i in (0, 1)]
    target_spans = [max(2 * max(abs(bounds[i] - chosen_center[i]), abs(bounds[i + 2] - chosen_center[i])), 1.0) for i in (0, 1)]
    chosen_zoom = zoom if zoom is not None else 100 * 0.9 * min(spans[i] / target_spans[i] for i in (0, 1))
    # GUIDanielPerspectiveChanger uses 100 * original width / viewport width.
    # Aspect-ratio correction only expands this conservative raw viewport.
    return {"center": list(chosen_center), "zoom": chosen_zoom,
            "basis": "explicit_zoom_override" if zoom is not None else "conservative_world_span_fit",
            "zoom_unit": "percent_of_initial_network_view", "reference_bounds": list(reference),
            "reference_bounds_basis": reference_basis, "runtime_grid_measured": False,
            "target_bounds": list(bounds), "connected_lane_context_m": context_distance if target is not None else None,
            "content_fraction": 0.9 if zoom is None else None,
            "limitation": "Initial NetEdit grid bounds are estimated, not measured. Check the actual Connection-mode image; explicit zoom can adjust its framing."}


def network_fit_view(net_file: Path, window_size: str) -> tuple[tuple[float, float], float]:
    """Fit the whole network; window_size remains compatible but is not a zoom unit."""
    view = fitted_viewport(net_file)
    return tuple(view["center"]), view["zoom"]


def aerial_decal(*, net_file: Path, aerial_image: Path, bbox_epsg25832) -> dict[str, Any]:
    """Place caller-declared north-up pixels without rewriting or warping the image."""
    from PIL import Image
    from pyproj import CRS, Transformer
    from pyproj.exceptions import ProjError

    if (not isinstance(bbox_epsg25832, (list, tuple)) or len(bbox_epsg25832) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox_epsg25832)):
        raise ValueError("Aerial bounds require four finite EPSG:25832 coordinates.")
    try:
        west, south, east, north = bbox = tuple(float(value) for value in bbox_epsg25832)
    except OverflowError as error:
        raise ValueError("Aerial bounds must be finite.") from error
    if not all(math.isfinite(value) for value in bbox) or west >= east or south >= north:
        raise ValueError("Aerial bounds must have positive width and height.")
    image_path = Path(aerial_image).resolve(strict=True)
    image_hash = file_sha256(image_path)
    with Image.open(image_path) as image:
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("Use an original north-up PNG or JPEG aerial image.")
        if image.getexif().get(274, 1) != 1:
            raise ValueError("Aerial image orientation must be unrotated and unmirrored.")
        image_size = list(image.size)
        image.load()
    location = ET.parse(net_file).getroot().find("location")
    if location is None:
        raise ValueError("Aerial background requires network projection and offset.")
    try:
        target = CRS.from_user_input(location.get("projParameter", "!"))
        offset = tuple(float(value) for value in location.get("netOffset", "").split(","))
        if (not target.is_projected or len(target.axis_info) != 2
                or any(axis.unit_conversion_factor != 1 for axis in target.axis_info)
                or len(offset) != 2 or not all(math.isfinite(value) for value in offset)):
            raise ValueError("Network projection must use metres and a finite two-coordinate offset.")
        source = CRS.from_epsg(25832)
        geography = Transformer.from_crs(source, "EPSG:4326", always_xy=True)
        transform = Transformer.from_crs(source, target, always_xy=True)
        grid = []
        for fx in (0, 0.5, 1):
            for fy in (0, 0.5, 1):
                x, y = west + fx * (east - west), south + fy * (north - south)
                lon, lat = geography.transform(x, y, errcheck=True)
                area = source.area_of_use
                if not area.west <= lon <= area.east or not area.south <= lat <= area.north:
                    raise ValueError("Aerial bounds lie outside the EPSG:25832 area of use.")
                nx, ny = transform.transform(x, y, errcheck=True)
                grid.append((fx, fy, nx + offset[0], ny + offset[1]))
    except (ValueError, ProjError) as error:
        raise ValueError(f"Cannot locate aerial background: {error}") from error
    x0, y0 = grid[0][2:]
    x1, y1 = grid[-1][2:]
    if not all(math.isfinite(value) for point in grid for value in point) or x1 <= x0 or y1 <= y0:
        raise ValueError("Aerial projection must preserve positive north-up bounds.")
    residual = max(math.hypot(x - (x0 + fx * (x1 - x0)), y - (y0 + fy * (y1 - y0)))
                   for fx, fy, x, y in grid)
    # ponytail: one native rectangle, checked at nine points to 0.1 m; warped projections need a separate georeferenced raster.
    if residual > 0.1:
        raise ValueError("Aerial projection requires rotation or warping beyond the 0.1 m rectangle limit.")
    if file_sha256(image_path) != image_hash:
        raise ValueError("Aerial image changed while its background was prepared.")
    return dict(image=dict(path=str(image_path), sha256=image_hash, size_pixels=image_size), bbox_epsg25832=list(bbox),
                orientation="caller_declared_north_up", network_projection=location.get("projParameter"),
                network_offset=list(offset), rectangle_fit_error_m=residual, rectangle_fit_limit_m=0.1,
                decal=dict(file=str(image_path), centerX=f"{(x0 + x1) / 2:.12g}", centerY=f"{(y0 + y1) / 2:.12g}",
                           width=f"{x1 - x0:.12g}", height=f"{y1 - y0:.12g}", rotation="0", layer="-100", screenRelative="false"),
                claim_boundary="The declared EPSG:25832 bounds locate the original pixels. Image orientation and surveyed alignment remain caller-supplied evidence.")


def viewsettings_text(center: tuple[float, float], *, zoom: float, aerial_background=None) -> str:
    x, y = center
    scheme = '  <scheme name="standard"/>\n'
    decal = "" if aerial_background is None else "  " + ET.tostring(
        ET.Element("decal", aerial_background["decal"]), encoding="unicode") + "\n"
    return (
        "<viewsettings>\n"
        f"{scheme}"
        f"{decal}"
        f'  <viewport zoom="{zoom:g}" x="{x:g}" y="{y:g}" angle="0"/>\n'
        '  <delay value="100"/>\n'
        "</viewsettings>\n"
    )


def selection_text(selection_type: str, selection_id: str) -> str:
    if selection_type == "none":
        return ""
    if selection_type not in {"junction", "lane"}:
        raise ValueError(f"Unsupported NetEdit selection type: {selection_type!r}")
    return f"{selection_type}:{selection_id}\n"


def mode_key(mode: str) -> str | None:
    return {"inspect": "CI", "connection": "C", "tls": "T"}[mode]


def capture_requests(
    target: TargetJunction,
    *,
    name_prefix: str = "target",
    include_neutral_overview: bool = False,
) -> list[CaptureRequest]:
    requests = [
        CaptureRequest(f"{name_prefix}-inspect", "inspect", "junction", target.junction_id),
        CaptureRequest(f"{name_prefix}-tls", "tls", "junction", target.junction_id),
        CaptureRequest(f"{name_prefix}-connection", "connection", "junction", target.junction_id),
    ]
    if include_neutral_overview:
        requests.insert(
            0,
            CaptureRequest(f"{name_prefix}-overview", "inspect", "none", ""),
        )
    return requests


def build_netedit_command(
    *,
    netedit_binary: str,
    net_file: Path,
    view_file: Path,
    selection_file: Path,
    window_size: str,
    window_pos: str,
    additional_file: Path | None = None,
) -> list[str]:
    command = [
        netedit_binary,
        "-s",
        str(net_file),
        "-g",
        str(view_file),
        "--selection-file",
        str(selection_file),
        "--registry-viewport",
        "false",
        "--window-size",
        window_size,
        "--window-pos",
        window_pos,
    ]
    if additional_file is not None:
        command += ["--additional-files", str(additional_file)]
    return command


def _windows_modules() -> tuple[Any, Any, Any, Any]:
    try:
        import win32con
        import win32gui
        import win32process
        import win32ui
    except ImportError as exc:  # pragma: no cover - Windows runtime guard
        raise SystemExit("pywin32 is required for background NetEdit capture on Windows.") from exc
    return win32con, win32gui, win32process, win32ui


def _enable_dpi_awareness() -> dict[str, Any]:
    """Keep Win32 window geometry and PrintWindow bitmaps in physical pixels."""

    global _DPI_AWARENESS
    if _DPI_AWARENESS is not None:
        return _DPI_AWARENESS
    if sys.platform != "win32":
        raise RuntimeError("DPI awareness is only available on Windows.")
    user32 = ctypes.windll.user32
    desired = ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    get_context = user32.GetThreadDpiAwarenessContext
    get_context.restype = ctypes.c_void_p
    contexts_equal = user32.AreDpiAwarenessContextsEqual
    contexts_equal.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    contexts_equal.restype = ctypes.c_bool
    already_enabled = bool(contexts_equal(get_context(), desired))
    if not already_enabled:
        setter = user32.SetProcessDpiAwarenessContext
        setter.argtypes = (ctypes.c_void_p,)
        setter.restype = ctypes.c_bool
        if not setter(desired):
            raise RuntimeError(
                "Unable to enable Per-Monitor-V2 DPI awareness before NetEdit capture."
            )
    _DPI_AWARENESS = {
        "status": "pass",
        "context": "per-monitor-v2",
        "already_enabled": already_enabled,
    }
    return _DPI_AWARENESS


def _windows_for_pid(pid: int) -> list[int]:
    _, win32gui, win32process, _ = _windows_modules()
    windows: list[int] = []

    def enum_window(hwnd: int, _: Any) -> bool:
        _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
        if window_pid == pid and win32gui.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    win32gui.EnumWindows(enum_window, None)
    return windows


def _wait_for_window(pid: int, *, timeout_seconds: float) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        windows = _windows_for_pid(pid)
        for hwnd in windows:
            if _find_fxgl_canvas(hwnd):
                return hwnd
        time.sleep(0.2)
    raise TimeoutError(f"NetEdit process {pid} did not create a visible canvas window.")


def _find_fxgl_canvas(hwnd: int) -> int:
    _, win32gui, _, _ = _windows_modules()
    canvases: list[int] = []

    def find_canvas(child: int, _: Any) -> bool:
        if win32gui.GetClassName(child) == "FXGLCanvas":
            canvases.append(child)
        return True

    win32gui.EnumChildWindows(hwnd, find_canvas, None)
    return canvases[0] if canvases else 0


def _post_mode_key(hwnd: int, mode: str) -> dict[str, Any]:
    key = mode_key(mode)
    if key is None:
        return {
            "status": "not_required",
            "mode": mode,
            "foreground_context_unchanged": True,
        }
    win32con, win32gui, _, _ = _windows_modules()
    target = _find_fxgl_canvas(hwnd) or hwnd
    foreground_before = _foreground_context()
    # FOX ignores shortcut messages for an inactive top-level window. Giving
    # only the NetEdit canvas internal activation/focus messages lets FOX route
    # the shortcut while Windows keeps the user's foreground application.
    win32gui.SendMessage(
        hwnd,
        win32con.WM_ACTIVATE,
        win32con.WA_ACTIVE,
        foreground_before["hwnd"],
    )
    win32gui.SendMessage(target, win32con.WM_SETFOCUS, foreground_before["hwnd"], 0)
    virtual_keys = []
    for character in key:
        virtual_key = ord(character)
        virtual_keys.append(virtual_key)
        scan_code = ctypes.windll.user32.MapVirtualKeyW(virtual_key, 0)
        key_down = 1 | (scan_code << 16)
        key_up = key_down | (1 << 30) | (1 << 31)
        for destination in {target, hwnd}:
            win32gui.SendMessage(
                destination,
                win32con.WM_KEYDOWN,
                virtual_key,
                key_down,
            )
            win32gui.SendMessage(
                destination,
                win32con.WM_KEYUP,
                virtual_key,
                key_up,
            )
        if len(key) > 1:
            time.sleep(0.2)
    win32gui.SendMessage(target, win32con.WM_KILLFOCUS, foreground_before["hwnd"], 0)
    win32gui.SendMessage(
        hwnd,
        win32con.WM_ACTIVATE,
        win32con.WA_INACTIVE,
        foreground_before["hwnd"],
    )
    foreground_after = _foreground_context()
    return {
        "status": "pass",
        "mode": mode,
        "virtual_keys": virtual_keys,
        "target_hwnd": target,
        "target_class": win32gui.GetClassName(target),
        "foreground_before": foreground_before,
        "foreground_after": foreground_after,
        "foreground_context_unchanged": (
            foreground_before["process_id"] == foreground_after["process_id"]
            and foreground_before["hwnd"] == foreground_after["hwnd"]
        ),
    }


def _capture_window(hwnd: int, destination: Path) -> dict[str, Any]:
    _, win32gui, _, win32ui = _windows_modules()
    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top
    window_dc = win32gui.GetWindowDC(hwnd)
    source_dc = win32ui.CreateDCFromHandle(window_dc)
    memory_dc = source_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(source_dc, width, height)
    memory_dc.SelectObject(bitmap)
    try:
        result = ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB",
            (info["bmWidth"], info["bmHeight"]),
            bits,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
        canvas = image.crop((min(230, width), min(100, height), width, height))
        luminance = canvas.convert("L")
        bright_pixels = sum(luminance.histogram()[200:])
        bright_pixel_fraction = bright_pixels / max(1, canvas.width * canvas.height)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination)
    finally:
        win32gui.DeleteObject(bitmap.GetHandle())
        memory_dc.DeleteDC()
        source_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
    return {
        "print_window_result": int(result),
        "width": width,
        "height": height,
        "bright_pixel_fraction": bright_pixel_fraction,
        "sha256": file_sha256(destination),
    }


def _launch_no_activate(command: list[str]) -> subprocess.Popen[bytes]:
    startup_info = subprocess.STARTUPINFO()
    startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup_info.wShowWindow = SW_SHOWNOACTIVATE
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startup_info,
    )


def _foreground_context() -> dict[str, int]:
    _, win32gui, win32process, _ = _windows_modules()
    hwnd = int(win32gui.GetForegroundWindow())
    process_id = 0
    if hwnd:
        _, process_id = win32process.GetWindowThreadProcessId(hwnd)
    return {"hwnd": hwnd, "process_id": int(process_id)}


def _keyboard_layout_context(hwnd: int | None = None) -> dict[str, Any]:
    """Record the target window layout before background key delivery."""

    if sys.platform != "win32":
        return {
            "status": "not_applicable",
            "is_chinese": False,
            "reason": "windows_only",
        }
    _, win32gui, _, _ = _windows_modules()
    user32 = ctypes.windll.user32
    foreground_hwnd = int(win32gui.GetForegroundWindow())
    target_hwnd = foreground_hwnd if hwnd is None else hwnd
    thread_id = 0
    if target_hwnd:
        thread_id = int(user32.GetWindowThreadProcessId(target_hwnd, None))
    if not thread_id:
        return {
            "status": "unknown",
            "is_chinese": None,
            "foreground_hwnd": foreground_hwnd,
            "thread_id": thread_id,
            "reason": "no_foreground_input_thread",
        }
    hkl = int(user32.GetKeyboardLayout(thread_id))
    lang_id = hkl & 0xFFFF
    primary_language_id = lang_id & 0x3FF
    layout_name = ""
    get_layout_name = getattr(user32, "GetKeyboardLayoutNameW", None)
    if get_layout_name is not None:
        buffer = ctypes.create_unicode_buffer(9)
        if get_layout_name(buffer):
            layout_name = buffer.value
    is_chinese = primary_language_id == 0x04
    return {
        "status": "blocked" if is_chinese else "pass",
        "is_chinese": is_chinese,
        "foreground_hwnd": foreground_hwnd,
        "target_hwnd": target_hwnd,
        "thread_id": thread_id,
        "hkl": f"0x{hkl:X}",
        "lang_id": f"0x{lang_id:04X}",
        "primary_language_id": primary_language_id,
        "layout_name": layout_name,
    }


def _ensure_english_window_layout(hwnd: int) -> dict[str, Any]:
    before = _keyboard_layout_context(hwnd)
    if before["status"] != "blocked":
        return {**before, "changed_by_torii": False}
    user32 = ctypes.windll.user32
    loader = user32.LoadKeyboardLayoutW
    loader.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
    loader.restype = wintypes.HANDLE
    english_hkl = int(loader("00000409", 0) or 0)
    if not english_hkl:
        raise RuntimeError("Unable to load the English keyboard layout for NetEdit.")
    _windows_modules()[1].SendMessage(hwnd, 0x0050, 0, english_hkl)  # WM_INPUTLANGCHANGEREQUEST
    after = _keyboard_layout_context(hwnd)
    if after["status"] == "blocked":
        raise RuntimeError("NetEdit rejected the background English keyboard-layout request.")
    return {**after, "changed_by_torii": True, "before": before}


def _force_foreground_window(hwnd: int) -> bool:
    """Restore a foreground window across process input-thread boundaries."""

    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    current_thread = int(kernel32.GetCurrentThreadId())
    foreground_hwnd = int(user32.GetForegroundWindow())
    foreground_thread = int(user32.GetWindowThreadProcessId(foreground_hwnd, None))
    target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
    attached_threads = []
    for thread_id in {foreground_thread, target_thread}:
        if thread_id and thread_id != current_thread:
            if user32.AttachThreadInput(current_thread, thread_id, True):
                attached_threads.append(thread_id)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread_id in reversed(attached_threads):
            user32.AttachThreadInput(current_thread, thread_id, False)
    return int(user32.GetForegroundWindow()) == hwnd


def _observe_and_restore_foreground(
    expected: dict[str, int],
) -> dict[str, Any]:
    _, win32gui, _, _ = _windows_modules()
    observed = _foreground_context()
    attempted = False
    if expected["hwnd"] and observed["process_id"] != expected["process_id"] and win32gui.IsWindow(expected["hwnd"]):
        attempted = True
        _force_foreground_window(expected["hwnd"])
        time.sleep(0.05)
    restored = _foreground_context()
    return {
        "observed": observed,
        "restore_attempted": attempted,
        "restored": restored,
        "context_restored": (expected["process_id"] == 0 or restored["process_id"] == expected["process_id"]),
    }


def _maximize_review_window(hwnd: int, win32con: Any, win32gui: Any) -> dict[str, Any]:
    """Maximize the review window without taking the user's foreground focus."""

    win32gui.ShowWindow(hwnd, SW_MAXIMIZE)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_BOTTOM,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
    )
    is_zoomed = getattr(win32gui, "IsZoomed", None)
    if is_zoomed is None:
        is_zoomed = ctypes.windll.user32.IsZoomed
    if not is_zoomed(hwnd):
        raise RuntimeError("NetEdit review window did not enter maximized mode.")
    return {"status": "pass", "window_state": "maximized"}


def _capture_request(
    *,
    request: CaptureRequest,
    net_file: Path,
    netedit_binary: str,
    output_dir: Path,
    center: tuple[float, float],
    zoom: float,
    window_size: str,
    window_pos: str,
    settle_seconds: float,
    additional_file: Path | None,
    aerial_background: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dpi_awareness = _enable_dpi_awareness()
    win32con, win32gui, _, _ = _windows_modules()
    view_file = output_dir / f"{request.name}.view.xml"
    selection_file = output_dir / f"{request.name}.selection.txt"
    screenshot_file = output_dir / f"{request.name}.png"
    view_file.write_text(
        viewsettings_text(center, zoom=zoom, aerial_background=aerial_background),
        encoding="utf-8",
    )
    preselection_text = selection_text(request.selection_type, request.selection_id)
    selection_file.write_text(preselection_text, encoding="utf-8")
    command = build_netedit_command(
        netedit_binary=netedit_binary,
        net_file=net_file,
        view_file=view_file,
        selection_file=selection_file,
        window_size=window_size,
        window_pos=window_pos,
        additional_file=additional_file,
    )
    foreground_context_before = _foreground_context()
    foreground_before = foreground_context_before["hwnd"]
    foreground_samples = [foreground_before]
    foreground_restore_events: list[dict[str, Any]] = []
    process = _launch_no_activate(command)
    hwnd = 0
    try:
        hwnd = _wait_for_window(process.pid, timeout_seconds=20.0)
        keyboard_layout = _ensure_english_window_layout(hwnd)
        foreground_restore_events.append(_observe_and_restore_foreground(foreground_context_before))
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
        window_presentation = _maximize_review_window(hwnd, win32con, win32gui)
        foreground_restore_events.append(_observe_and_restore_foreground(foreground_context_before))
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
        foreground_restore_events.append(_observe_and_restore_foreground(foreground_context_before))
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
        time.sleep(settle_seconds)
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
        mode_delivery = _post_mode_key(hwnd, request.mode)
        foreground_restore_events.append(_observe_and_restore_foreground(foreground_context_before))
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
        time.sleep(settle_seconds)
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
        capture_attempts = []
        for attempt in range(1, 5):
            capture = _capture_window(hwnd, screenshot_file)
            capture_attempts.append(
                {
                    "attempt": attempt,
                    "bright_pixel_fraction": capture["bright_pixel_fraction"],
                }
            )
            if capture["bright_pixel_fraction"] >= 0.1:
                break
            time.sleep(0.75)
        capture["render_quality"] = "pass" if capture["bright_pixel_fraction"] >= 0.1 else "blocked"
        title = win32gui.GetWindowText(hwnd)
        foreground_samples.append(int(win32gui.GetForegroundWindow()))
    finally:
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        foreground_restore_events.append(_observe_and_restore_foreground(foreground_context_before))
    foreground_context_after = _foreground_context()
    foreground_after = int(win32gui.GetForegroundWindow())
    foreground_samples.append(foreground_after)
    return {
        "name": request.name,
        "mode": request.mode,
        "selection_type": request.selection_type,
        "selection_id": request.selection_id,
        "command": command,
        "pid": process.pid,
        "window_title": title,
        "view_file": str(view_file),
        "selection_file": str(selection_file),
        "preselection_applied": bool(preselection_text),
        "screenshot_file": str(screenshot_file),
        "mode_delivery": mode_delivery,
        "inspect_delivery": {
            "status": "unverified" if request.mode == "inspect" else "not_required",
            "object_type": "junction" if request.mode == "inspect" else None,
            "delivery": "NetEdit junction selection file; attribute-pane inspection not claimed",
            "foreground_context_unchanged": True,
        },
        "foreground_before": foreground_before,
        "foreground_after": foreground_after,
        "foreground_context_before": foreground_context_before,
        "foreground_context_after": foreground_context_after,
        "foreground_context_restored": (
            all(event["context_restored"] for event in foreground_restore_events)
            and (
                foreground_context_before["process_id"] == 0
                or foreground_context_after["process_id"] == foreground_context_before["process_id"]
            )
        ),
        "foreground_restore_events": foreground_restore_events,
        "foreground_samples": foreground_samples,
        "foreground_unchanged": all(sample == foreground_before for sample in foreground_samples),
        "capture_attempts": capture_attempts,
        "window_presentation": window_presentation,
        "dpi_awareness": dpi_awareness,
        "keyboard_layout": keyboard_layout,
        **capture,
    }


def run_background_review(
    *,
    summary_file: Path,
    manifest_file: Path | None,
    output_dir: Path,
    netedit_binary: str,
    center: tuple[float, float] | None,
    zoom: float | None,
    window_size: str,
    window_pos: str,
    settle_seconds: float,
    candidate_role: str = "primary",
) -> dict[str, Any]:
    identity = load_bound_candidate_identity(
        summary_file=summary_file,
        manifest_file=manifest_file,
        candidate_role=candidate_role,
    )
    if sys.platform != "win32":
        raise SystemExit("Background NetEdit capture is currently Windows-only.")
    candidate = identity.candidate_file
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = read_target_junction(candidate, identity.target_junction_id)
    viewport = fitted_viewport(candidate, target=target, center=center, zoom=zoom)
    view_center, zoom = tuple(viewport["center"]), viewport["zoom"]
    executable = _which_netedit(netedit_binary) or netedit_binary
    captures = [
        _capture_request(
            request=request,
            net_file=candidate,
            netedit_binary=executable,
            output_dir=destination,
            center=view_center,
            zoom=zoom,
            window_size=window_size,
            window_pos=window_pos,
            settle_seconds=settle_seconds,
            additional_file=identity.review_overlay_file,
        )
        for request in capture_requests(target)
    ]
    capture_hashes = [item["sha256"] for item in captures]
    mode_images_distinct = len(set(capture_hashes)) == len(capture_hashes)
    report = {
        "schema": "torii.netedit-background-review/v3",
        "status": (
            "review_material_ready"
            if all(
                item["print_window_result"] == 1
                and item["render_quality"] == "pass"
                and item["foreground_unchanged"]
                and item["foreground_context_restored"]
                and item["mode_delivery"]["foreground_context_unchanged"]
                and item.get("keyboard_layout", {}).get("status") != "blocked"
                for item in captures
            )
            and mode_images_distinct
            else "blocked"
        ),
        "candidate_role": identity.candidate_role,
        "summary_file": str(identity.summary_file),
        "summary_sha256": file_sha256(identity.summary_file),
        "manifest_file": str(identity.manifest_file),
        "manifest_sha256": file_sha256(identity.manifest_file),
        "candidate_evidence_file": (
            str(identity.candidate_evidence_file) if identity.candidate_evidence_file is not None else None
        ),
        "source_file": str(identity.source_file),
        "source_sha256": identity.source_sha256,
        "candidate_file": str(candidate),
        "candidate_sha256": identity.candidate_sha256,
        "review_overlay_file": (
            str(identity.review_overlay_file) if identity.review_overlay_file is not None else None
        ),
        "tls_ownership_recheck": identity.tls_ownership_recheck,
        "netedit_binary": executable,
        "target_junction": {
            "id": target.junction_id,
            "type": target.junction_type,
            "x": target.x,
            "y": target.y,
            "incoming_lanes": list(target.incoming_lanes),
        },
        "view_center": list(view_center),
        "zoom": zoom,
        "viewport_fit": viewport,
        "window_size": window_size,
        "mode_delivery": "target-window WM_KEYDOWN/WM_KEYUP",
        "capture_delivery": "target-window PrintWindow(PW_RENDERFULLCONTENT)",
        "selection_delivery": "NetEdit --selection-file",
        "capture_session_count": len(captures),
        "global_keyboard_or_mouse_input_used": False,
        "foreground_context_restored": all(item["foreground_context_restored"] for item in captures),
        "keyboard_layout_context": captures[0].get("keyboard_layout", {}) if captures else {},
        "mode_images_distinct": mode_images_distinct,
        "captures": captures,
        "claim_boundary": (
            "These images provide background NetEdit mode and selection context. "
            "They do not replace the code-native exact lane/via/request/TLS audit, "
            "do not constitute human validation, and do not authorize promotion."
        ),
        "automatic_promotion_gate": "blocked",
    }
    report_file = destination / "netedit-background-review.json"
    write_json_atomic(report_file, report)
    return {**report, "report_file": str(report_file)}


def run_direct_background_review(
    *,
    net_file: Path,
    expected_net_sha256: str,
    target_junction_id: str,
    output_dir: Path,
    netedit_binary: str,
    center: tuple[float, float] | None,
    zoom: float | None,
    window_size: str,
    window_pos: str,
    settle_seconds: float,
    neutral_overview: bool = False,
    aerial_image: Path | None = None,
    aerial_bbox_epsg25832: list[float] | tuple[float, ...] | None = None,
) -> dict[str, Any]:
    """Capture a hash-bound, non-promoting review of one candidate junction.

    This entry point is intentionally narrower than the workflow-bound runners: it
    accepts one immutable candidate and one exact junction owner so intermediate
    geometry can be inspected before official TLS materialisation is complete.
    """

    candidate = net_file.resolve(strict=True)
    candidate_hash_before = _require_recorded_hash(
        path=candidate,
        expected_hash=expected_net_sha256,
        label="direct-review candidate network",
    )
    if not target_junction_id.strip():
        raise ValueError("Direct review requires a non-empty target junction id.")
    if (aerial_image is None) != (aerial_bbox_epsg25832 is None):
        raise ValueError("Provide aerial_image and aerial_bbox_epsg25832 together.")
    background = aerial_decal(net_file=candidate, aerial_image=aerial_image, bbox_epsg25832=aerial_bbox_epsg25832) if aerial_image is not None else None
    if sys.platform != "win32":
        raise SystemExit("Background NetEdit capture is currently Windows-only.")

    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    target = read_target_junction(candidate, target_junction_id)
    viewport = fitted_viewport(candidate, target=target, center=center, zoom=zoom)
    view_center, zoom = tuple(viewport["center"]), viewport["zoom"]
    executable = _which_netedit(netedit_binary) or netedit_binary
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", target.junction_id).strip("._-")
    if not safe_target:
        raise ValueError("Target junction id cannot be represented as a safe artifact name.")
    target_digest = hashlib.sha256(target.junction_id.encode("utf-8")).hexdigest()[:10]
    # ponytail: keep Windows MAX_PATH headroom; the full junction id remains in
    # the report, while filenames use the digest for collision resistance.
    artifact_token = f"{safe_target[:10]}-{target_digest}"
    requests = capture_requests(
        target,
        name_prefix=f"direct-{artifact_token}",
        include_neutral_overview=neutral_overview,
    )
    overview_fit = fitted_viewport(candidate) if neutral_overview else None
    overview_center, overview_zoom = (tuple(overview_fit["center"]), overview_fit["zoom"]) if overview_fit else (view_center, zoom)
    captures = []
    for request in requests:
        request_center, request_zoom = (
            (overview_center, overview_zoom)
            if request.selection_type == "none"
            else (view_center, zoom)
        )
        captures.append(
            _capture_request(
            request=request,
            net_file=candidate,
            netedit_binary=executable,
            output_dir=destination,
            center=request_center,
            zoom=request_zoom,
            window_size=window_size,
            window_pos=window_pos,
            settle_seconds=settle_seconds,
            additional_file=None,
            aerial_background=background,
        )
        )
    candidate_hash_after = file_sha256(candidate)
    candidate_unchanged = candidate_hash_after == candidate_hash_before
    image_unchanged = True
    if background is not None:
        image_path = Path(background["image"]["path"])
        after = file_sha256(image_path) if image_path.is_file() else None
        image_unchanged = after == background["image"]["sha256"]
        background.update(image_sha256_after=after, image_unchanged=image_unchanged)
    mode_capture_hashes = [
        item["sha256"] for item in captures if item["selection_type"] != "none"
    ]
    mode_images_distinct = (
        len(mode_capture_hashes) == 3 and len(set(mode_capture_hashes)) == 3
    )
    capture_gates_pass = all(
        item["print_window_result"] == 1
        and item["render_quality"] == "pass"
        and item["foreground_unchanged"]
        and item["foreground_context_restored"]
        and item["mode_delivery"]["foreground_context_unchanged"]
        and item.get("inspect_delivery", {}).get("foreground_context_unchanged", True)
        and item.get("keyboard_layout", {}).get("status") != "blocked"
        for item in captures
    )
    report = {
        "schema": "torii.netedit-background-review.direct/v1",
        "status": (
            "review_material_ready"
            if capture_gates_pass and mode_images_distinct and candidate_unchanged and image_unchanged
            else "blocked"
        ),
        "review_scope": "single-hash-bound-candidate-junction",
        "candidate_file": str(candidate),
        "candidate_sha256_before": candidate_hash_before,
        "candidate_sha256_after": candidate_hash_after,
        "candidate_unchanged": candidate_unchanged,
        "aerial_background": background,
        "netedit_binary": executable,
        "target_junction": {
            "id": target.junction_id,
            "type": target.junction_type,
            "x": target.x,
            "y": target.y,
            "incoming_lanes": list(target.incoming_lanes),
        },
        "view_center": list(view_center),
        "zoom": zoom,
        "viewport_fit": viewport,
        "neutral_overview_view": (
            {"center": list(overview_center), "zoom": overview_zoom, "viewport_fit": overview_fit}
            if neutral_overview
            else None
        ),
        "window_size": window_size,
        "mode_delivery": "target-window WM_KEYDOWN/WM_KEYUP",
        "capture_delivery": "target-window PrintWindow(PW_RENDERFULLCONTENT)",
        "selection_delivery": (
            "NetEdit --selection-file; empty selection for neutral overview"
            if neutral_overview
            else "NetEdit --selection-file"
        ),
        "inspect_object_delivery": "junction selection only; Net: junction attribute pane remains unverified",
        "inspect_object_source": f"{candidate}#junction[{target.junction_id}]",
        "capture_session_count": len(captures),
        "expected_capture_session_count": 4 if neutral_overview else 3,
        "global_keyboard_or_mouse_input_used": False,
        "foreground_context_restored": all(
            item["foreground_context_restored"] for item in captures
        ),
        "keyboard_layout_context": captures[0].get("keyboard_layout", {}) if captures else {},
        "mode_images_distinct": mode_images_distinct,
        "captures": captures,
        "claim_boundary": (
            "These images provide background NetEdit context for one immutable candidate "
            "junction owner. They do not prove official TLS correctness, do not constitute "
            "human validation, and do not authorize promotion."
        ),
        "automatic_promotion_gate": "blocked",
    }
    report_file = destination / f"review-{target_digest}.json"
    write_json_atomic(report_file, report)
    return {**report, "report_file": str(report_file)}


def run_hamburg_background_review(
    *,
    manifest_file: Path,
    output_dir: Path,
    netedit_binary: str,
    center: tuple[float, float] | None,
    zoom: float | None,
    window_size: str,
    window_pos: str,
    settle_seconds: float,
) -> dict[str, Any]:
    """Capture three modes for each hash-bound Hamburg corridor junction."""

    identity = load_bound_hamburg_official_identity(manifest_file=manifest_file)
    if sys.platform != "win32":
        raise SystemExit("Background NetEdit capture is currently Windows-only.")
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    executable = _which_netedit(netedit_binary) or netedit_binary
    target_rows: list[dict[str, Any]] = []
    captures: list[dict[str, Any]] = []
    for target_identity in identity.targets:
        target = read_target_junction(
            identity.candidate_file,
            target_identity.junction_id,
        )
        viewport = fitted_viewport(identity.candidate_file, target=target, center=center, zoom=zoom)
        view_center = tuple(viewport["center"])
        target_rows.append(
            {
                "official_node_id": target_identity.official_node_id,
                "junction_id": target.junction_id,
                "controller_id": target_identity.controller_id,
                "type": target.junction_type,
                "x": target.x,
                "y": target.y,
                "incoming_lanes": list(target.incoming_lanes),
                "view_center": list(view_center),
                "zoom": viewport["zoom"],
                "viewport_fit": viewport,
            }
        )
        captures.extend(
            _capture_request(
                request=request,
                net_file=identity.candidate_file,
                netedit_binary=executable,
                output_dir=destination,
                center=view_center,
                zoom=viewport["zoom"],
                window_size=window_size,
                window_pos=window_pos,
                settle_seconds=settle_seconds,
                additional_file=None,
            )
            for request in capture_requests(
                target,
                name_prefix=f"hamburg-{target_identity.official_node_id}",
            )
        )
    capture_hashes_by_node = {
        node_id: [
            item["sha256"]
            for item in captures
            if item["name"].startswith(f"hamburg-{node_id}-")
        ]
        for node_id in HAMBURG_OFFICIAL_NODE_IDS
    }
    distinct_modes_by_node = {
        node_id: len(hashes) == 3 and len(set(hashes)) == 3
        for node_id, hashes in capture_hashes_by_node.items()
    }
    capture_gates_pass = all(
        item["print_window_result"] == 1
        and item["render_quality"] == "pass"
        and item["foreground_unchanged"]
        and item["foreground_context_restored"]
        and item["mode_delivery"]["foreground_context_unchanged"]
        and item.get("keyboard_layout", {}).get("status") != "blocked"
        for item in captures
    )
    report = {
        "schema": "torii.netedit-background-review.hamburg-official/v1",
        "status": (
            "review_material_ready"
            if capture_gates_pass and all(distinct_modes_by_node.values())
            else "blocked"
        ),
        "review_scope": "hamburg-sandtorkai-three-official-junctions",
        "manifest_file": str(identity.manifest_file),
        "manifest_sha256": file_sha256(identity.manifest_file),
        "source_file": str(identity.source_file),
        "source_sha256": identity.source_sha256,
        "candidate_file": str(identity.candidate_file),
        "candidate_sha256": identity.candidate_sha256,
        "artifact_hash_recheck": list(identity.artifact_hash_recheck),
        "official_asset_hash_recheck": list(identity.official_asset_hash_recheck),
        "candidate_tls_recheck": identity.candidate_tls_recheck,
        "netedit_binary": executable,
        "target_junctions": target_rows,
        "zoom": zoom,
        "zoom_mode": "automatic_per_junction" if zoom is None else "explicit_zoom_override",
        "window_size": window_size,
        "mode_delivery": "target-window WM_KEYDOWN/WM_KEYUP",
        "capture_delivery": "target-window PrintWindow(PW_RENDERFULLCONTENT)",
        "selection_delivery": "NetEdit --selection-file",
        "capture_session_count": len(captures),
        "expected_capture_session_count": 9,
        "global_keyboard_or_mouse_input_used": False,
        "foreground_context_restored": all(
            item["foreground_context_restored"] for item in captures
        ),
        "keyboard_layout_context": captures[0].get("keyboard_layout", {}) if captures else {},
        "distinct_modes_by_official_node": distinct_modes_by_node,
        "captures": captures,
        "claim_boundary": (
            "These nine images provide background NetEdit context for the three official "
            "Hamburg junctions. They do not replace exact MAP/OCIT lane, movement, request, "
            "and TLS audits, do not constitute human validation, and do not authorize promotion."
        ),
        "automatic_promotion_gate": "blocked",
    }
    report_file = destination / "hamburg-netedit-background-review.json"
    write_json_atomic(report_file, report)
    return {**report, "report_file": str(report_file)}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture Inspect, Traffic Light, and Connection mode evidence from "
            "a background NetEdit window without global keyboard or mouse input."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--summary")
    source.add_argument("--hamburg-manifest")
    source.add_argument("--net-file")
    parser.add_argument("--manifest")
    parser.add_argument("--expected-net-sha256")
    parser.add_argument("--target-junction-id")
    parser.add_argument("--aerial-image", help="Original north-up PNG/JPEG background for --net-file reviews.")
    parser.add_argument("--aerial-bbox-epsg25832", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                        help="Declared image bounds in EPSG:25832 metres. Requires --aerial-image.")
    parser.add_argument(
        "--candidate-role",
        choices=("primary", "nema-topology"),
        default="primary",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--netedit-binary", default="netedit")
    parser.add_argument("--view-center")
    parser.add_argument(
        "--zoom",
        type=float,
        default=None,
        help="Override target-junction zoom as a percentage of the initial network view. Default: fit the junction and nearby lane sections.",
    )
    parser.add_argument("--window-size", default="1400,1000")
    parser.add_argument("--window-pos", default="20,20")
    parser.add_argument("--settle-seconds", type=float, default=1.5)
    parser.add_argument(
        "--neutral-overview",
        action="store_true",
        help="Also capture an automatically fitted full-network overview for direct reviews.",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    if (args.aerial_image or args.aerial_bbox_epsg25832 is not None) and not args.net_file:
        raise SystemExit("Aerial background arguments are only valid with --net-file.")
    if args.net_file:
        if args.manifest:
            raise SystemExit("--manifest is only valid with the isolated --summary workflow.")
        if args.candidate_role != "primary":
            raise SystemExit("--candidate-role is only valid with the isolated --summary workflow.")
        if not args.expected_net_sha256:
            raise SystemExit("--expected-net-sha256 is required with --net-file.")
        if not args.target_junction_id:
            raise SystemExit("--target-junction-id is required with --net-file.")
        report = run_direct_background_review(
            net_file=Path(args.net_file),
            expected_net_sha256=args.expected_net_sha256,
            target_junction_id=args.target_junction_id,
            output_dir=Path(args.out_dir),
            netedit_binary=args.netedit_binary,
            center=parse_point(args.view_center) if args.view_center else None,
            zoom=args.zoom,
            window_size=args.window_size,
            window_pos=args.window_pos,
            settle_seconds=args.settle_seconds,
            neutral_overview=args.neutral_overview,
            aerial_image=Path(args.aerial_image) if args.aerial_image else None,
            aerial_bbox_epsg25832=args.aerial_bbox_epsg25832,
        )
    elif args.hamburg_manifest:
        if args.neutral_overview:
            raise SystemExit("--neutral-overview is only valid with --net-file.")
        if args.manifest:
            raise SystemExit("--manifest is only valid with the isolated --summary workflow.")
        if args.candidate_role != "primary":
            raise SystemExit("--candidate-role is only valid with the isolated --summary workflow.")
        report = run_hamburg_background_review(
            manifest_file=Path(args.hamburg_manifest),
            output_dir=Path(args.out_dir),
            netedit_binary=args.netedit_binary,
            center=parse_point(args.view_center) if args.view_center else None,
            zoom=args.zoom,
            window_size=args.window_size,
            window_pos=args.window_pos,
            settle_seconds=args.settle_seconds,
        )
    else:
        if args.neutral_overview:
            raise SystemExit("--neutral-overview is only valid with --net-file.")
        if args.expected_net_sha256:
            raise SystemExit("--expected-net-sha256 is only valid with --net-file.")
        if args.target_junction_id:
            raise SystemExit("--target-junction-id is only valid with --net-file.")
        report = run_background_review(
            summary_file=Path(args.summary),
            manifest_file=Path(args.manifest) if args.manifest else None,
            output_dir=Path(args.out_dir),
            netedit_binary=args.netedit_binary,
            center=parse_point(args.view_center) if args.view_center else None,
            zoom=args.zoom,
            window_size=args.window_size,
            window_pos=args.window_pos,
            settle_seconds=args.settle_seconds,
            candidate_role=args.candidate_role,
        )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "review_material_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
