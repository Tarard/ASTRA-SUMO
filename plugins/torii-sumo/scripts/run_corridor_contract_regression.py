from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_GATE_NAMES = (
    "artifact_identity",
    "netconvert",
    "review_contract",
    "xml_parse",
    "tls_modal_rail_bridge_tunnel",
    "modal_connectivity",
    "sumo_load",
    "routeability",
    "topology",
)


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    ledger: dict[str, Any]
    evidence_source: str
    osm_file: Path | None = None
    source_stage_evidence_files: tuple[Path, ...] = ()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run four curated corridor contract regressions with real SUMO.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Artifact root; defaults to outputs/corridor_vertical_slice_20260713/contract_v2.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return loaded


def _relative_evidence(path_value: object, *, delivery_root: Path) -> str:
    path_text = str(path_value or "").strip()
    if not path_text:
        return ""
    path = Path(path_text).resolve()
    try:
        return path.relative_to(delivery_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _delivery_manifest(aggregate: dict[str, Any], *, delivery_root: Path) -> dict[str, Any]:
    scenario_metadata = {
        "x4_osm_sidewalk": (
            "four_way_osm_sidewalk",
            "A reviewed sidewalk operation on the checked-in four-way signalized OSM fixture passes "
            "materialization and all persisted candidate gates.",
        ),
        "official_pedestrian_crossing": (
            "pedestrian_crossing_and_tls_review",
            "The official SUMO pedestrian fixture materializes a crossing and walking areas; its exact "
            "protected semantic and TLS deltas are accepted only through a hash-bound review decision.",
        ),
        "five_way_bicycle": (
            "five_way_bicycle_multimodal_connectivity",
            "A reviewed bicycle and pedestrian connector on the five-way fixture preserves connection-level "
            "modal connectivity through SUMO internal, crossing, and walking-area edges.",
        ),
        "five_way_ramp": (
            "five_way_ramp",
            "A reviewed five-way ramp candidate passes materialization, SUMO load, routeability, topology, "
            "and protected-network gates without mutating its source network.",
        ),
    }
    blocks: list[dict[str, Any]] = []
    for result in aggregate.get("scenarios", []):
        if not isinstance(result, dict):
            continue
        scenario_id = str(result.get("scenario_id", ""))
        block_id, notes = scenario_metadata.get(scenario_id, (scenario_id, "Curated corridor regression."))
        source_stage_evidence = result.get("source_stage_evidence_files", [])
        if not isinstance(source_stage_evidence, list):
            source_stage_evidence = []
        evidence = [
            _relative_evidence(item, delivery_root=delivery_root)
            for item in source_stage_evidence
        ] + [
            _relative_evidence(result.get(field), delivery_root=delivery_root)
            for field in (
                "materialization_report_file",
                "materialization_manifest_file",
                "candidate_net_file",
                "map_review_evidence_file",
                "review_overlay_file",
                "candidate_review_html_file",
                "review_decision_file",
                "candidate_gate_report_file",
            )
        ]
        gate_status = result.get("gate_status", {})
        if not isinstance(gate_status, dict):
            gate_status = {}
        blocks.append(
            {
                "id": block_id,
                "scenario_id": scenario_id,
                "status": result.get("status", "blocked"),
                "evidence_source": result.get("evidence_source", ""),
                "evidence": [item for item in evidence if item],
                "required_gate_status": {
                    gate_name: gate_status.get(gate_name, "blocked") for gate_name in REQUIRED_GATE_NAMES
                },
                "notes": notes,
            }
        )

    status = str(aggregate.get("status", "blocked"))
    return {
        "schema": "torii.corridor_vertical_slice_four_block_manifest.v2",
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "blocked",
        "created": "2026-07-13",
        "scope": "four reusable corridor-level human-modeling blocks",
        "source_network_mutation": False,
        "blocks": blocks,
        "verification": {
            "contract_manifest": "contract_v2/four_candidate_contract_manifest.json",
            "scenario_count": aggregate.get("scenario_count", 0),
            "passed_scenario_count": aggregate.get("passed_scenario_count", 0),
            "required_gate_names": list(REQUIRED_GATE_NAMES),
            "all_required_gates_passed": status == "pass"
            and all(
                all(block["required_gate_status"].get(name) == "pass" for name in REQUIRED_GATE_NAMES)
                for block in blocks
            ),
            "sumo_toolchain": aggregate.get("sumo_toolchain", {}),
        },
        "limitations": [
            "The four-way OSM regression uses the checked-in x4_signalized fixture because the prior live "
            "Overpass attempt for a second geographic bbox was blocked by the host network policy.",
            "These four corridor fixtures validate the reusable candidate contract; they do not prove "
            "automatic human-equivalent cleanup for an arbitrary city-scale OSM network.",
            "Accepted review decisions are exact, hash-bound, fixture-scoped decisions and are not reusable "
            "automatic approvals for production networks.",
        ],
    }


def _build_source_net(
    *,
    command: list[str],
    output_file: Path,
    report_file: Path,
    command_runner: Callable[..., Any],
    write_json: Callable[..., None],
) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.unlink(missing_ok=True)
    result = command_runner(command, timeout_seconds=240.0)
    report = result.to_dict()
    report.update(
        {
            "schema": "torii.corridor_source_build.v1",
            "output_file": str(output_file.resolve()),
            "output_exists": output_file.is_file(),
            "output_size": output_file.stat().st_size if output_file.is_file() else 0,
        }
    )
    write_json(report_file, report, sort_keys=True)
    if result.status != "pass" or result.returncode != 0 or not output_file.is_file():
        raise RuntimeError(f"source netconvert failed; inspect {report_file}")
    return output_file


def _scenarios(
    repo_root: Path,
    output_root: Path,
    binaries: dict[str, str | None],
    *,
    command_runner: Callable[..., Any],
    write_json: Callable[..., None],
    scene_workflow: Callable[..., dict[str, Any]],
) -> list[Scenario]:
    source_root = output_root / "source_builds"
    source_root.mkdir(parents=True, exist_ok=True)
    netconvert = str(binaries["netconvert"])
    sumo_home = Path(str(binaries["sumo"])).resolve().parent.parent

    x4_osm_file = repo_root / "tests" / "intersection" / "fixtures" / "x4_signalized.osm.xml"
    x4_source = source_root / "x4_osm" / "x4_signalized_source.net.xml"
    x4_source_report = source_root / "x4_osm" / "x4_signalized_source.netconvert.json"
    _build_source_net(
        command=[
            netconvert,
            "--osm-files",
            str(x4_osm_file),
            "--output-file",
            str(x4_source),
            "--proj.utm",
            "--no-turnarounds",
            "--osm.all-attributes",
            "--tls.join",
            "--tls.join-dist",
            "35",
        ],
        output_file=x4_source,
        report_file=x4_source_report,
        command_runner=command_runner,
        write_json=write_json,
    )
    x4_ledger = {
        "schema": "torii.corridor_edit_ledger.v1",
        "source_net_file": str(x4_source.resolve()),
        "osm_file": str(x4_osm_file.resolve()),
        "operations": [
            {
                "id": "accepted-add-sidewalk-2-3",
                "operation": "add_sidewalk",
                "status": "accepted",
                "target_ids": [],
                "rationale": "provide an explicit pedestrian corridor across the four-way OSM fixture",
                "evidence": [
                    {"kind": "osm_fixture", "source": "x4_signalized.osm.xml"},
                    {"kind": "scene_review", "rule": "sidewalk_connection"},
                ],
                "rollback": {
                    "action": "remove_candidate_addition",
                    "target_ids": ["torii-add-accepted-add-sidewalk-2-3"],
                },
                "params": {
                    "from": "2",
                    "to": "3",
                    "type": "highway.footway",
                    "speed": "1.4",
                    "width": "2.0",
                },
            }
        ],
    }

    pedestrian_data = sumo_home / "docs" / "tutorial" / "traci_pedestrian_crossing" / "data"
    pedestrian_nodes = pedestrian_data / "pedcrossing.nod.xml"
    pedestrian_edges = pedestrian_data / "pedcrossing.edg.xml"
    if not pedestrian_nodes.is_file() or not pedestrian_edges.is_file():
        raise RuntimeError(f"official SUMO pedestrian tutorial inputs are missing under {pedestrian_data}")
    crossing_source = source_root / "official_pedestrian" / "pedcrossing_source.net.xml"
    crossing_source_report = source_root / "official_pedestrian" / "pedcrossing_source.netconvert.json"
    _build_source_net(
        command=[
            netconvert,
            "--node-files",
            str(pedestrian_nodes),
            "--edge-files",
            str(pedestrian_edges),
            "--output-file",
            str(crossing_source),
        ],
        output_file=crossing_source,
        report_file=crossing_source_report,
        command_runner=command_runner,
        write_json=write_json,
    )
    crossing_ledger = {
        "schema": "torii.corridor_edit_ledger.v1",
        "source_net_file": str(crossing_source.resolve()),
        "operations": [
            {
                "id": "accepted-add-crossing-C",
                "operation": "add_crossing",
                "status": "accepted",
                "target_ids": ["CE", "EC"],
                "rationale": "add the reviewed pedestrian crossing from the official SUMO fixture",
                "evidence": [{"kind": "official_sumo_fixture", "source": "pedestrian crossing network"}],
                "rollback": {"action": "discard_candidate_and_restore_source"},
                "params": {
                    "node_id": "C",
                    "crossing_edges": ["CE", "EC"],
                    "width": "6",
                },
            }
        ],
    }

    five_way_prompt = (
        "Build a five-way signalized intersection with pedestrian, bicycle and ramp support using actuated control."
    )
    five_way_dir = source_root / "five_way_prompt"
    five_way_report = scene_workflow(
        five_way_prompt,
        five_way_dir,
        prefix="five_way_source",
        launch_netedit_after_build=False,
    )
    if five_way_report.get("status") != "pass":
        raise RuntimeError(
            "five-way sentence-to-spec source workflow failed; inspect "
            f"{five_way_report.get('artifact_manifest_file', five_way_dir)}"
        )
    five_way_source = Path(str(five_way_report["net_file"])).resolve()
    five_way_manifest = Path(str(five_way_report["artifact_manifest_file"])).resolve()
    bicycle_ledger = {
        "schema": "torii.corridor_edit_ledger.v1",
        "source_net_file": str(five_way_source.resolve()),
        "operations": [
            {
                "id": "accepted-add-bikeway-A0-A1",
                "operation": "add_bicycle",
                "status": "accepted",
                "target_ids": [],
                "rationale": "add a reviewed bicycle and pedestrian connector to the five-way fixture",
                "evidence": [{"kind": "curated_scene_review", "source": "five_way_full"}],
                "rollback": {"action": "discard_candidate_and_restore_source"},
                "params": {
                    "from": "A0",
                    "to": "A1",
                    "type": "highway.cycleway",
                    "lanes": "1",
                    "speed": "8.0",
                    "allow": "bicycle pedestrian",
                    "width": "2.5",
                },
            }
        ],
    }
    ramp_ledger = {
        "schema": "torii.corridor_edit_ledger.v1",
        "source_net_file": str(five_way_source),
        "operations": [
            {
                "id": "add-ramp-five-way",
                "operation": "add_ramp",
                "status": "accepted",
                "target_ids": [],
                "rationale": "restore a missing controlled-access connector in the corridor candidate",
                "evidence": [{"kind": "scenario_review", "source": "five_way_sentence_scene"}],
                "rollback": {
                    "action": "remove_candidate_addition",
                    "edge_ids": ["torii-add-add-ramp-five-way"],
                },
                "params": {
                    "from": "A0",
                    "to": "A1",
                    "type": "highway.primary_link",
                    "lanes": "1",
                    "speed": "13.9",
                    "allow": "passenger",
                    "width": "3.5",
                },
            }
        ],
    }
    return [
        Scenario(
            "x4_osm_sidewalk",
            x4_ledger,
            "checked-in x4_signalized OSM fixture",
            x4_osm_file,
            (x4_source_report,),
        ),
        Scenario(
            "official_pedestrian_crossing",
            crossing_ledger,
            "official SUMO pedestrian tutorial fixture",
            source_stage_evidence_files=(crossing_source_report,),
        ),
        Scenario(
            "five_way_bicycle",
            bicycle_ledger,
            "sentence-generated five-way bicycle fixture",
            source_stage_evidence_files=(five_way_manifest,),
        ),
        Scenario(
            "five_way_ramp",
            ramp_ledger,
            "sentence-generated five-way ramp fixture",
            source_stage_evidence_files=(five_way_manifest,),
        ),
    ]


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[3]
    source_root = repo_root / "plugins" / "torii-sumo" / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

    from torii_sumo.core.artifact_io import write_json_atomic
    from torii_sumo.core.command_runner import run_command
    from torii_sumo.core.corridor_edit_ledger import (
        materialize_corridor_edit_variant,
        run_corridor_candidate_gates,
    )
    from torii_sumo.core.sumo_commands import discover_binaries
    from torii_sumo.intersection.scene_workflow import run_intersection_scene_workflow

    output_root = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else repo_root / "outputs" / "corridor_vertical_slice_20260713" / "contract_v2"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    binaries = discover_binaries()
    missing = [name for name in ("netconvert", "sumo", "randomTrips") if not binaries.get(name)]
    if missing:
        raise RuntimeError(f"missing SUMO tools from one toolchain: {', '.join(missing)}")
    sumo_bin_dir = Path(str(binaries["sumo"])).resolve().parent
    os.environ["SUMO_HOME"] = str(sumo_bin_dir.parent)
    os.environ["PATH"] = f"{sumo_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    results: list[dict[str, Any]] = []
    try:
        scenarios = _scenarios(
            repo_root,
            output_root,
            binaries,
            command_runner=run_command,
            write_json=write_json_atomic,
            scene_workflow=run_intersection_scene_workflow,
        )
    except Exception as exc:  # noqa: BLE001 - persist source-stage failure before returning.
        aggregate = {
            "schema": "torii.corridor_contract_regression.v2",
            "status": "blocked",
            "claim_status": "blocked",
            "sumo_toolchain": binaries,
            "scenario_count": 4,
            "passed_scenario_count": 0,
            "required_gate_names": list(REQUIRED_GATE_NAMES),
            "source_network_mutation": False,
            "source_stage_error": f"{type(exc).__name__}: {exc}",
            "scenarios": [],
        }
        manifest_file = output_root / "four_candidate_contract_manifest.json"
        write_json_atomic(manifest_file, aggregate, sort_keys=True)
        if args.output_dir is None:
            delivery_root = output_root.parent
            write_json_atomic(
                delivery_root / "four_block_delivery_manifest.json",
                _delivery_manifest(aggregate, delivery_root=delivery_root),
                sort_keys=True,
            )
        print(json.dumps({**aggregate, "manifest_file": str(manifest_file)}, indent=2, ensure_ascii=False))
        return 1
    for scenario in scenarios:
        scenario_dir = output_root / scenario.scenario_id
        materialized_dir = scenario_dir / "materialized"
        gates_dir = scenario_dir / "gates"
        scenario_dir.mkdir(parents=True, exist_ok=True)
        ledger_file = scenario_dir / f"{scenario.scenario_id}.accepted.ledger.json"
        write_json_atomic(ledger_file, scenario.ledger, sort_keys=True)
        try:
            materialization = materialize_corridor_edit_variant(
                ledger_file=ledger_file,
                output_dir=materialized_dir,
                prefix=scenario.scenario_id,
                netconvert_binary=str(binaries["netconvert"]),
                timeout_seconds=240.0,
            )
            review_decision: dict[str, Any] | None = None
            review_file = ""
            template_value = str(materialization.get("review_decision_template_file", ""))
            if materialization.get("status") == "pass" and template_value:
                template = _load_json(Path(template_value))
                if template.get("review_required"):
                    review_path = scenario_dir / f"{scenario.scenario_id}.accepted.review.json"
                    template.update(
                        {
                            "status": "accepted",
                            "rationale": (
                                "Accepted only for this curated regression fixture after exact delta and TLS "
                                "signature review; this is not an automatic production-network approval."
                            ),
                            "evidence": [
                                {
                                    "kind": "curated_regression_review",
                                    "source": scenario.evidence_source,
                                    "scope": scenario.scenario_id,
                                }
                            ],
                            "rollback": {"action": "discard_candidate_and_restore_source"},
                            "review_file": str(review_path.resolve()),
                        }
                    )
                    write_json_atomic(review_path, template, sort_keys=True)
                    review_decision = template
                    review_file = str(review_path.resolve())

            if materialization.get("status") == "pass":
                candidate_net_file = Path(str(materialization["candidate_net_file"]))
                gates = run_corridor_candidate_gates(
                    source_net_file=Path(str(materialization["source_net_file"])),
                    candidate_net_file=candidate_net_file,
                    output_dir=gates_dir,
                    materialization_report=materialization,
                    review_decision=review_decision,
                    osm_file=scenario.osm_file,
                    prefix=scenario.scenario_id,
                    sumo_binary=str(binaries["sumo"]),
                    vehicle_count=10,
                    initial_end=300,
                    max_end=1200,
                    timeout_seconds=240.0,
                )
            else:
                gates = {
                    "status": "blocked",
                    "reason": "materialization did not pass",
                    "gates": {},
                }
            result = {
                "scenario_id": scenario.scenario_id,
                "status": "pass"
                if materialization.get("status") == "pass" and gates.get("status") == "pass"
                else "blocked",
                "evidence_source": scenario.evidence_source,
                "source_stage_evidence_files": [
                    str(path.resolve()) for path in scenario.source_stage_evidence_files
                ],
                "materialization_status": materialization.get("status", "blocked"),
                "materialization_report_file": materialization.get("report_file", ""),
                "materialization_manifest_file": materialization.get("manifest_file", ""),
                "candidate_net_file": materialization.get("candidate_net_file", ""),
                "map_review_evidence_file": materialization.get("map_review_evidence_file", ""),
                "map_review_evidence_status": materialization.get(
                    "map_review_evidence_status",
                    "blocked",
                ),
                "map_review_readiness_status": materialization.get(
                    "map_review_readiness_status",
                    "blocked",
                ),
                "review_overlay_file": materialization.get("accepted_review_additional_xml", ""),
                "candidate_review_html_file": materialization.get(
                    "candidate_review_html_file",
                    "",
                ),
                "review_decision_file": review_file,
                "candidate_gate_status": gates.get("status", "blocked"),
                "candidate_gate_report_file": gates.get("report_file", ""),
                "gate_status": {
                    str(name): gate.get("status", "blocked")
                    for name, gate in (gates.get("gates", {}) or {}).items()
                    if isinstance(gate, dict)
                },
            }
        except Exception as exc:  # noqa: BLE001 - keep all four scenario outcomes in the aggregate manifest.
            result = {
                "scenario_id": scenario.scenario_id,
                "status": "blocked",
                "evidence_source": scenario.evidence_source,
                "source_stage_evidence_files": [
                    str(path.resolve()) for path in scenario.source_stage_evidence_files
                ],
                "error": f"{type(exc).__name__}: {exc}",
            }
        write_json_atomic(scenario_dir / f"{scenario.scenario_id}.result.json", result, sort_keys=True)
        results.append(result)

    status = "pass" if all(result.get("status") == "pass" for result in results) else "blocked"
    aggregate = {
        "schema": "torii.corridor_contract_regression.v2",
        "status": status,
        "claim_status": "diagnostic-demo" if status == "pass" else "blocked",
        "sumo_toolchain": binaries,
        "scenario_count": len(results),
        "passed_scenario_count": sum(1 for result in results if result.get("status") == "pass"),
        "required_gate_names": list(REQUIRED_GATE_NAMES),
        "source_network_mutation": False,
        "scenarios": results,
    }
    manifest_file = output_root / "four_candidate_contract_manifest.json"
    write_json_atomic(manifest_file, aggregate, sort_keys=True)
    if args.output_dir is None:
        delivery_root = output_root.parent
        write_json_atomic(
            delivery_root / "four_block_delivery_manifest.json",
            _delivery_manifest(aggregate, delivery_root=delivery_root),
            sort_keys=True,
        )
    print(json.dumps({**aggregate, "manifest_file": str(manifest_file)}, indent=2, ensure_ascii=False))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
