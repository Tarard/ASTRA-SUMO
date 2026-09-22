from __future__ import annotations

import json
from pathlib import Path

from .compile_plain import compile_intersection_to_plain
from .infer_approaches import infer_approaches
from .infer_control import infer_control_model
from .infer_core import infer_intersection_core
from .infer_movements import infer_movement_matrix
from .infer_road_relations import build_road_pair_relation_graph
from .osm_patch import parse_osm_xml
from .schema import IntersectionIR, PatchSeed
from .validate import validate_intersection


def build_intersection_ir(
    osm_file: Path,
    output_dir: Path,
    seed: PatchSeed | None = None,
) -> IntersectionIR:
    patch = parse_osm_xml(osm_file)
    core = infer_intersection_core(patch, seed)
    approaches = infer_approaches(patch, core)
    road_pair_graph = build_road_pair_relation_graph(patch, core, approaches)
    movement_matrix = infer_movement_matrix(core, approaches, road_pair_graph, patch=patch)
    control = infer_control_model(patch, core, approaches, movement_matrix)
    return IntersectionIR(
        intersection_id=core.core_id,
        osm_patch=patch,
        core=core,
        approaches=approaches,
        road_pair_graph=road_pair_graph,
        movement_matrix=movement_matrix,
        control=control,
        compiled=None,
        validation=None,
        claim_status="semantic-model-built",
    )


def clean_intersection(
    osm_file: Path,
    output_dir: Path,
    seed: PatchSeed | None = None,
    compile_net: bool = True,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    ir = build_intersection_ir(osm_file, output_dir, seed)
    artifacts = compile_intersection_to_plain(ir, output_dir, "intersection", compile_net=compile_net)
    validation = validate_intersection(ir, artifacts, output_dir)
    final_ir = ir.model_copy(
        update={
            "compiled": artifacts,
            "validation": validation,
            "claim_status": "intersection-cleaned" if validation.status == "pass" else "blocked",
        }
    )
    (output_dir / "intersection_ir.json").write_text(
        json.dumps(final_ir.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    (output_dir / "validation.json").write_text(
        json.dumps(validation.model_dump(mode="json"), indent=2),
        encoding="utf-8",
    )
    return {
        "status": validation.status,
        "claim_status": final_ir.claim_status,
        "intersection_id": final_ir.intersection_id,
        "topology_type": final_ir.core.topology_type,
        "approach_count": len(final_ir.approaches),
        "movement_count": len(final_ir.movement_matrix.movements),
        "sumo_load_status": validation.sumo_load_status,
        "route_probe_status": validation.route_probe_status,
        "tls_linkindex_status": validation.tls_linkindex_status,
        "missing_movement_count": validation.missing_movement_count,
        "disconnected_edge_count": validation.disconnected_edge_count,
        "internal_fragment_count": validation.internal_fragment_count,
        "approach_mode_counts": validation.approach_mode_counts,
        "vehicle_approach_count": validation.vehicle_approach_count,
        "vehicle_topology_type": validation.vehicle_topology_type,
        "legal_movement_mode_counts": validation.legal_movement_mode_counts,
        "forbidden_cross_mode_movement_count": validation.forbidden_cross_mode_movement_count,
        "restriction_warning_count": len(final_ir.movement_matrix.restriction_warnings),
        "direction_blocked_approach_count": sum(
            1
            for approach in final_ir.approaches
            if "passenger" in approach.allowed_modes
            and not (approach.has_incoming_vehicle_flow and approach.has_outgoing_vehicle_flow)
        ),
        "custom_tllogic_applied": artifacts.custom_tllogic_applied,
        "warning_count_by_severity": validation.warning_count_by_severity,
        "blocking_error_count": validation.blocking_error_count,
        "warnings": validation.warnings,
        "intersection_ir_file": str(output_dir / "intersection_ir.json"),
        "validation_file": str(output_dir / "validation.json"),
        "net_file": artifacts.net_file,
    }
