from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from collections.abc import Mapping
from typing import Any

from torii_sumo.intersection.archetype_profile import classify_osm_intersection_archetype
from torii_sumo.intersection.clean import build_intersection_ir, clean_intersection
from torii_sumo.intersection.movement_hypotheses import build_vehicle_movement_hypotheses
from torii_sumo.intersection.nema_reference import build_nema_four_way_reference
from torii_sumo.intersection.osm_patch import parse_osm_xml_bytes
from torii_sumo.intersection.physical_cell import infer_signal_anchor_physical_cell
from torii_sumo.intersection.road_detail import (
    AUTHORITY_CATEGORIES,
    NETWORK_ROLES,
)
from torii_sumo.intersection.scene_workflow import run_intersection_scene_workflow
from torii_sumo.intersection.schema import CompiledSUMOArtifacts, IntersectionIR, PatchSeed
from torii_sumo.intersection.topology_evidence import build_topology_evidence
from torii_sumo.intersection.validate import (
    _approach_mode_counts,
    _legal_movement_mode_counts,
    _topology_type,
    validate_intersection,
)


_ROAD_NETWORK_EVIDENCE_SCHEMA = "torii.road-detail-evidence-projection/v1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def sumo_intersection_archetype_classify(
    osm_file: str,
    seed_osm_node_id: str,
    traffic_side: str = "right",
    road_network_evidence_file: str | None = None,
) -> dict[str, Any]:
    """Classify one local OSM intersection without writing or mutating artifacts.

    ``road_network_evidence_file`` may point to a frozen
    ``torii.road-detail-evidence-projection/v1`` JSON projection.  Its accepted
    local way records must bind the exact OSM byte snapshot supplied here.  The
    optional evidence enriches road-function classification only; it cannot
    authorize a junction edit, a SUMO lane connection, or signal binding.
    """

    source = Path(osm_file).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"osm_file must be an existing local file: {source}")

    normalized_traffic_side = str(traffic_side).strip().lower()
    if normalized_traffic_side not in {"left", "right"}:
        raise ValueError("traffic_side must be 'left' or 'right'")

    seed_node_id = str(seed_osm_node_id).strip()
    if not seed_node_id:
        raise ValueError("seed_osm_node_id must not be empty")

    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    patch = parse_osm_xml_bytes(
        source_bytes,
        gzip_compressed=source.suffix.casefold() == ".gz",
    )
    road_network_evidence, road_network_provenance = _load_road_network_evidence(
        road_network_evidence_file,
        osm_source_sha256=source_sha256,
        local_way_ids=patch.ways,
    )
    physical_cell = infer_signal_anchor_physical_cell(
        patch,
        seed_node_id=seed_node_id,
    )
    topology_evidence = build_topology_evidence(patch, physical_cell)
    movement_hypotheses = build_vehicle_movement_hypotheses(
        patch,
        physical_cell,
        traffic_side=normalized_traffic_side,
    )
    source_evidence: dict[str, Any] = {
        "sha256": source_sha256,
        "media_type": (
            "application/gzip"
            if source.suffix.casefold() == ".gz"
            else "application/osm+xml"
        ),
        "content_format": "osm_xml",
    }
    if road_network_provenance is not None:
        source_evidence["road_network_evidence"] = {
            "schema": road_network_provenance["schema"],
            "source_sha256": road_network_provenance["source_sha256"],
            "bound_osm_source_sha256": source_sha256,
        }
    archetype_profile = classify_osm_intersection_archetype(
        patch,
        physical_cell,
        topology_evidence=topology_evidence,
        movement_hypotheses=movement_hypotheses,
        source_evidence=source_evidence,
        road_network_evidence=road_network_evidence,
    )
    road_network_resolution = _road_network_resolution_summary(
        archetype_profile["road_detail"],
        road_network_provenance=road_network_provenance,
    )
    return {
        "status": archetype_profile["generation_status"],
        "disposition": archetype_profile["disposition"],
        "type_recognition": archetype_profile["decision_capabilities"]["type_recognition"],
        "automatic_promotion_gate": archetype_profile["automatic_promotion_gate"],
        "source_file": str(source),
        "source_sha256": source_sha256,
        "seed_osm_node_id": seed_node_id,
        "traffic_side": normalized_traffic_side,
        "archetype_profile": archetype_profile,
        "evidence_artifacts": {
            "physical_cell": physical_cell,
            "topology_evidence": topology_evidence,
            "movement_hypotheses": movement_hypotheses,
            "road_detail": archetype_profile["road_detail"],
            "road_network_evidence": road_network_provenance,
        },
        "evidence_summary": {
            "physical_cell_hypothesis_id": physical_cell["hypothesis_id"],
            "physical_approach_count": len(physical_cell["physical_approaches"]),
            "topology_evidence_id": topology_evidence["topology_evidence_id"],
            "topology_morphology": topology_evidence["morphology"],
            "movement_hypothesis_set_id": movement_hypotheses["hypothesis_set_id"],
            "movement_generation_status": movement_hypotheses["generation_status"],
            "movement_unresolved_reasons": movement_hypotheses["unresolved_reasons"],
            "road_detail_id": archetype_profile["road_detail"]["road_detail_id"],
            "road_detail_status": archetype_profile["road_detail"]["status"],
            "road_detail_unknown_count": len(
                archetype_profile["road_detail"]["unknown_road_arm_ids"]
            ),
            "road_network_resolution": road_network_resolution,
        },
    }


def _load_road_network_evidence(
    road_network_evidence_file: str | None,
    *,
    osm_source_sha256: str,
    local_way_ids: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Load one immutable road-detail projection and retain only safe bindings.

    The generic road-detail classifier needs a small ``by_way_id`` mapping.
    This MCP boundary verifies that the projection is the supported schema,
    that it remains classification-only, and that every locally applicable
    accepted mapping names this exact OSM snapshot in ``source_sha256s``.
    """

    if road_network_evidence_file is None:
        return None, None
    raw_path = str(road_network_evidence_file).strip()
    if not raw_path:
        raise ValueError("road_network_evidence_file must not be empty when provided")
    source = Path(raw_path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(
            "road_network_evidence_file must be an existing local JSON file: "
            f"{source}"
        )

    source_bytes = source.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    try:
        parsed = json.loads(source_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("road_network_evidence_file must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("road_network_evidence_file must contain valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("road_network_evidence_file JSON root must be an object")

    evidence = dict(parsed)
    _validate_road_network_evidence_root(evidence)
    raw_by_way = evidence["by_way_id"]
    accepted_by_way: dict[str, dict[str, Any]] = {}
    local_way_id_set = {str(way_id) for way_id in local_way_ids}
    mapping_status_excluded_way_ids: list[str] = []
    unreferenced_by_local_osm_way_ids: list[str] = []
    hash_bound_way_ids: list[str] = []
    for raw_way_id, raw_record in sorted(raw_by_way.items(), key=lambda item: str(item[0])):
        way_id = str(raw_way_id).strip()
        if not way_id:
            raise ValueError("road_network_evidence by_way_id contains an empty way id")
        record = _validate_road_network_evidence_record(way_id, raw_record)
        if way_id not in local_way_id_set:
            unreferenced_by_local_osm_way_ids.append(way_id)
            continue
        if record["mapping_status"] != "pass":
            mapping_status_excluded_way_ids.append(way_id)
            continue
        declared_hashes = {
            str(item).casefold() for item in record["source_sha256s"]
        }
        if osm_source_sha256.casefold() not in declared_hashes:
            raise ValueError(
                "road_network_evidence_file does not bind local OSM way "
                f"{way_id!r} to this osm_file SHA-256"
            )
        accepted_by_way[way_id] = record
        hash_bound_way_ids.append(way_id)

    filtered_evidence = {
        "schema": evidence["schema"],
        "status": evidence["status"],
        "by_way_id": accepted_by_way,
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
    }
    provenance = {
        "source_file": str(source),
        "source_sha256": source_sha256,
        "schema": evidence["schema"],
        "declared_status": evidence["status"],
        "classification_only": True,
        "automatic_promotion_gate": "blocked",
        "bound_osm_source_sha256": osm_source_sha256,
        "hash_bound_way_ids": hash_bound_way_ids,
        "mapping_status_excluded_way_ids": mapping_status_excluded_way_ids,
        "unreferenced_by_local_osm_way_ids": unreferenced_by_local_osm_way_ids,
    }
    return filtered_evidence, provenance


def _validate_road_network_evidence_root(evidence: Mapping[str, Any]) -> None:
    if evidence.get("schema") != _ROAD_NETWORK_EVIDENCE_SCHEMA:
        raise ValueError(
            "road_network_evidence_file schema must be "
            f"{_ROAD_NETWORK_EVIDENCE_SCHEMA!r}"
        )
    if evidence.get("status") not in {"pass", "review_required"}:
        raise ValueError(
            "road_network_evidence_file status must be 'pass' or 'review_required'"
        )
    if evidence.get("classification_only") is not True:
        raise ValueError("road_network_evidence_file must declare classification_only=true")
    if evidence.get("automatic_promotion_gate") != "blocked":
        raise ValueError(
            "road_network_evidence_file automatic_promotion_gate must be 'blocked'"
        )
    by_way = evidence.get("by_way_id")
    if not isinstance(by_way, Mapping):
        raise ValueError("road_network_evidence_file by_way_id must be an object")
    if not isinstance(evidence.get("conflicts"), list):
        raise ValueError("road_network_evidence_file conflicts must be an array")
    excluded = evidence.get("excluded_relation_ids")
    if not isinstance(excluded, list) or not all(
        isinstance(item, str) for item in excluded
    ):
        raise ValueError(
            "road_network_evidence_file excluded_relation_ids must be an array of strings"
        )


def _validate_road_network_evidence_record(
    way_id: str,
    raw_record: Any,
) -> dict[str, Any]:
    if not isinstance(raw_record, Mapping):
        raise ValueError(
            f"road_network_evidence_file by_way_id[{way_id!r}] must be an object"
        )
    record = dict(raw_record)
    for field_name in (
        "authority_category",
        "network_role",
        "functional_category",
        "source_evidence_id",
        "mapping_status",
    ):
        if not isinstance(record.get(field_name), str):
            raise ValueError(
                f"road_network_evidence_file by_way_id[{way_id!r}].{field_name} "
                "must be a string"
            )
    if record["authority_category"] not in AUTHORITY_CATEGORIES:
        raise ValueError(
            f"road_network_evidence_file by_way_id[{way_id!r}].authority_category "
            "is not in the registered road-detail vocabulary"
        )
    if record["network_role"] not in NETWORK_ROLES:
        raise ValueError(
            f"road_network_evidence_file by_way_id[{way_id!r}].network_role "
            "is not in the registered road-detail vocabulary"
        )
    if record["mapping_status"] not in {"pass", "review_required"}:
        raise ValueError(
            f"road_network_evidence_file by_way_id[{way_id!r}].mapping_status "
            "must be 'pass' or 'review_required'"
        )
    for field_name in (
        "source_relation_ids",
        "source_assignment_ids",
        "source_sha256s",
    ):
        values = record.get(field_name)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(
                f"road_network_evidence_file by_way_id[{way_id!r}].{field_name} "
                "must be an array of strings"
            )
    source_hashes = record["source_sha256s"]
    if not source_hashes or not all(_SHA256_RE.fullmatch(value) for value in source_hashes):
        raise ValueError(
            f"road_network_evidence_file by_way_id[{way_id!r}].source_sha256s "
            "must contain SHA-256 hex digests"
        )
    return record


def _road_network_resolution_summary(
    road_detail: Mapping[str, Any],
    *,
    road_network_provenance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Expose how the classifier resolved each arm without changing promotion."""

    arm_resolution_by_id: dict[str, str] = {}
    for arm in road_detail.get("road_arms", ()):
        if not isinstance(arm, Mapping):
            continue
        arm_id = str(arm.get("road_arm_id", ""))
        identity = arm.get("road_identity", {})
        resolution = (
            str(identity.get("resolution", "unknown"))
            if isinstance(identity, Mapping)
            else "unknown"
        )
        if arm_id:
            arm_resolution_by_id[arm_id] = resolution
    counts = {
        resolution: sum(
            value == resolution for value in arm_resolution_by_id.values()
        )
        for resolution in sorted(set(arm_resolution_by_id.values()))
    }
    return {
        "evidence_file_provided": road_network_provenance is not None,
        "authoritative_evidence_used": "authoritative" in counts,
        "osm_fallback_used": "osm_fallback" in counts,
        "contradicted_resolution_used": "contradicted" in counts,
        "unknown_resolution_used": "unknown" in counts,
        "road_arm_resolution_counts": counts,
        "road_arm_resolution_by_id": arm_resolution_by_id,
        "hash_bound_way_count": (
            len(road_network_provenance["hash_bound_way_ids"])
            if road_network_provenance is not None
            else 0
        ),
    }


def sumo_intersection_model(
    osm_file: str,
    output_dir: str,
    seed_osm_node_id: str | None = None,
) -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ir = build_intersection_ir(
        Path(osm_file),
        out,
        seed=PatchSeed(osm_node_id=seed_osm_node_id) if seed_osm_node_id else None,
    )
    ir_file = out / "intersection_ir.json"
    ir_file.write_text(json.dumps(ir.model_dump(mode="json"), indent=2), encoding="utf-8")
    return {
        "status": "pass",
        "intersection_id": ir.intersection_id,
        "topology_type": ir.core.topology_type,
        "approach_count": len(ir.approaches),
        "movement_count": len(ir.movement_matrix.movements),
        "approach_mode_counts": _approach_mode_counts(ir),
        "vehicle_approach_count": sum(1 for approach in ir.approaches if "passenger" in approach.allowed_modes),
        "vehicle_topology_type": _topology_type(
            sum(1 for approach in ir.approaches if "passenger" in approach.allowed_modes)
        ),
        "legal_movement_mode_counts": _legal_movement_mode_counts(ir.movement_matrix.movements),
        "forbidden_cross_mode_movement_count": sum(
            1 for movement in ir.movement_matrix.movements if not movement.allowed and not movement.allowed_modes
        ),
        "restriction_warning_count": len(ir.movement_matrix.restriction_warnings),
        "direction_blocked_approach_count": sum(
            1
            for approach in ir.approaches
            if "passenger" in approach.allowed_modes
            and not (approach.has_incoming_vehicle_flow and approach.has_outgoing_vehicle_flow)
        ),
        "custom_tllogic_applied": None,
        "intersection_ir_file": str(ir_file),
        "claim_status": ir.claim_status,
    }


def sumo_intersection_clean(
    osm_file: str,
    output_dir: str,
    seed_osm_node_id: str | None = None,
    compile_net: bool = True,
) -> dict[str, Any]:
    return clean_intersection(
        osm_file=Path(osm_file),
        output_dir=Path(output_dir),
        seed=PatchSeed(osm_node_id=seed_osm_node_id) if seed_osm_node_id else None,
        compile_net=compile_net,
    )


def sumo_intersection_validate(
    intersection_ir_file: str,
    output_dir: str,
) -> dict[str, Any]:
    ir = IntersectionIR.model_validate_json(Path(intersection_ir_file).read_text(encoding="utf-8"))
    if ir.compiled is None:
        return {
            "status": "fail",
            "claim_status": "blocked",
            "error": "intersection_ir.compiled is required",
        }
    artifacts = CompiledSUMOArtifacts.model_validate(ir.compiled)
    validation = validate_intersection(ir, artifacts, Path(output_dir))
    validation_file = Path(output_dir) / "validation.json"
    validation_file.parent.mkdir(parents=True, exist_ok=True)
    validation_file.write_text(json.dumps(validation.model_dump(mode="json"), indent=2), encoding="utf-8")
    return {
        "status": validation.status,
        "sumo_load_status": validation.sumo_load_status,
        "route_probe_status": validation.route_probe_status,
        "restriction_warning_count": len(ir.movement_matrix.restriction_warnings),
        "direction_blocked_approach_count": sum(
            1
            for approach in ir.approaches
            if "passenger" in approach.allowed_modes
            and not (approach.has_incoming_vehicle_flow and approach.has_outgoing_vehicle_flow)
        ),
        "custom_tllogic_applied": artifacts.custom_tllogic_applied,
        "warning_count_by_severity": validation.warning_count_by_severity,
        "blocking_error_count": validation.blocking_error_count,
        "validation_file": str(validation_file),
        "claim_status": "intersection-cleaned" if validation.status == "pass" else "blocked",
    }


def sumo_nema_four_way_reference_workflow(
    output_dir: str,
    prefix: str = "nema_four_way_reference",
    run_sumo_smoke: bool = True,
    require_real_sumo: bool = False,
) -> dict[str, Any]:
    return build_nema_four_way_reference(
        Path(output_dir),
        prefix=prefix,
        run_sumo_smoke=run_sumo_smoke,
        require_real_sumo=require_real_sumo,
    )


def sumo_intersection_scene_workflow(
    prompt: str,
    output_dir: str,
    prefix: str = "intersection_scene",
    launch_netedit_after_build: bool = False,
) -> dict[str, Any]:
    return run_intersection_scene_workflow(
        prompt,
        Path(output_dir),
        prefix=prefix,
        launch_netedit_after_build=launch_netedit_after_build,
    )
