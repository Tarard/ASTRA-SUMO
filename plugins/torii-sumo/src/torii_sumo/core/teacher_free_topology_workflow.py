from __future__ import annotations

import html
import json
from pathlib import Path
import shutil
from typing import Any, Mapping
from xml.etree import ElementTree as ET

from torii_sumo.corridor.netxml import normalized_net_sha256
from torii_sumo.intersection.autodiscovery import (
    discover_teacher_free_intersections,
)
from torii_sumo.intersection.osm_patch import parse_osm_xml
from torii_sumo.intersection.topology_discrimination_experiment import (
    build_topology_discrimination_contract,
)

from .artifact_io import (
    relative_or_absolute_path,
    write_json_atomic,
    write_text_atomic,
)
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .producer_identity import capture_code_producer_state
from .sumo_commands import discover_binaries
from .topology_variant_workflow import run_topology_variant


_OWNER_SCHEMA = "torii.teacher-free-topology-workflow-owner/v1"


def run_teacher_free_topology_workflow(
    *,
    osm_file: Path,
    output_dir: Path,
    traffic_side: str,
    toolchain_lock_file: Path,
    binaries: Mapping[str, str | None] | None = None,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Run v4 parallel topology discrimination without selecting a winner."""

    source_osm = osm_file.resolve(strict=True)
    toolchain_lock = toolchain_lock_file.resolve(strict=True)
    destination = output_dir.resolve()
    if destination in source_osm.parents or destination == source_osm.parent:
        raise ValueError(
            "The frozen source OSM must not be stored inside the generated output directory."
        )
    producer = capture_code_producer_state(Path(__file__).resolve().parents[5])
    source_osm_sha256 = file_sha256(source_osm)
    _reset_owned_directory(destination)

    patch = parse_osm_xml(source_osm)
    discovery = discover_teacher_free_intersections(
        source_osm,
        traffic_side=traffic_side,
    )
    discovery_file = destination / "teacher-free-discovery.json"
    write_json_atomic(discovery_file, discovery, sort_keys=True)
    contract = build_topology_discrimination_contract(discovery, patch)
    contract_file = destination / "topology-contract.json"
    write_json_atomic(contract_file, contract, sort_keys=True)

    if contract["status"] != "ready":
        return _write_terminal_bundle(
            destination=destination,
            status=str(contract["status"]),
            source_osm=source_osm,
            source_osm_sha256=source_osm_sha256,
            toolchain_lock=toolchain_lock,
            producer=producer,
            discovery=discovery,
            contract=contract,
            details={
                "terminal_stage": "pre_materialization",
                "reason": (
                    "no applicable vehicle cell"
                    if contract["status"] == "not_applicable"
                    else "base or topology-specific pre-materialization gates blocked all candidate writing"
                ),
            },
        )

    selected_binaries = dict(binaries or discover_binaries())
    missing_binaries = [
        name for name in ("netconvert", "sumo") if not selected_binaries.get(name)
    ]
    if missing_binaries:
        return _write_terminal_bundle(
            destination=destination,
            status="blocked",
            source_osm=source_osm,
            source_osm_sha256=source_osm_sha256,
            toolchain_lock=toolchain_lock,
            producer=producer,
            discovery=discovery,
            contract=contract,
            details={
                "terminal_stage": "toolchain_resolution",
                "reason": "required SUMO binaries are unavailable",
                "missing_binaries": missing_binaries,
            },
        )

    first_plan = contract["candidate_plans"][0]
    discovered_candidate = _candidate_by_id(
        discovery,
        str(first_plan["discovered_candidate_id"]),
    )
    hypothesis = discovered_candidate["hypothesis"]
    candidate_dag = hypothesis["candidate_dag"]
    candidate_dag_file = destination / "candidate-dag.json"
    write_json_atomic(candidate_dag_file, candidate_dag, sort_keys=True)
    assessment = _assessment_by_id(
        contract,
        str(first_plan["discovered_candidate_id"]),
    )
    topology_evidence_file = destination / "topology-evidence.json"
    write_json_atomic(
        topology_evidence_file,
        assessment["topology_evidence"],
        sort_keys=True,
    )

    netconvert = str(selected_binaries["netconvert"])
    sumo = str(selected_binaries["sumo"])
    source_net = destination / "source.net.xml"
    relative_osm = relative_or_absolute_path(source_osm, destination)
    source_command = [
        netconvert,
        "--osm-files",
        relative_osm,
        "--proj.utm",
        "--no-turnarounds",
        "--osm.all-attributes",
        "--verbose",
        "--output-file",
        source_net.name,
    ]
    source_result = run_command(
        source_command,
        cwd=destination,
        timeout_seconds=timeout_seconds,
    ).to_dict()
    source_build_pass = _command_built_file(source_result, source_net)
    source_build = {
        "schema": "torii.teacher-free-topology-source-build/v1",
        "status": "pass" if source_build_pass else "fail",
        "producer": producer,
        "command": source_command,
        "command_result": source_result,
        "source_osm_mutation": file_sha256(source_osm) != source_osm_sha256,
        "source_net": {
            "path": str(source_net),
            "sha256": file_sha256(source_net) if source_net.is_file() else None,
            "normalized_sha256": (
                normalized_net_sha256(source_net) if source_build_pass else None
            ),
        },
    }
    source_build_file = destination / "source-netconvert-build.json"
    write_json_atomic(source_build_file, source_build, sort_keys=True)
    if not source_build_pass or source_build["source_osm_mutation"]:
        return _write_terminal_bundle(
            destination=destination,
            status="blocked",
            source_osm=source_osm,
            source_osm_sha256=source_osm_sha256,
            toolchain_lock=toolchain_lock,
            producer=producer,
            discovery=discovery,
            contract=contract,
            details={
                "terminal_stage": "source_materialization",
                "reason": "source netconvert or source immutability gate failed",
                "source_build_report": str(source_build_file),
            },
        )

    source_scope = tuple(map(str, first_plan["source_junction_ids"]))
    guard_source_ids = _derive_guard_junction_ids(source_net, source_scope)
    scope_derivation = {
        "schema": "torii.teacher-free-topology-scope/v1",
        "method": "source_net_external_edge_boundary_adjacency",
        "target_source_junction_ids": list(source_scope),
        "guard_source_junction_ids": list(guard_source_ids),
        "manual_scope_input": False,
        "scope_expansion_allowed": False,
    }
    scope_file = destination / "scope-derivation.json"
    write_json_atomic(scope_file, scope_derivation, sort_keys=True)

    variants = []
    for plan in contract["candidate_plans"]:
        topology = str(plan["topology_hypothesis"])
        variant_dir = destination / "variants" / _topology_token(topology)
        try:
            result = run_topology_variant(
                source_osm=source_osm,
                source_osm_sha256=source_osm_sha256,
                source_net=source_net,
                source_scope=source_scope,
                guard_source_ids=guard_source_ids,
                candidate_plan=plan,
                contract=contract,
                physical_cell=hypothesis["physical_cell"],
                movement_hypotheses=hypothesis[
                    "vehicle_movement_hypotheses"
                ],
                candidate_dag=candidate_dag,
                output_dir=variant_dir,
                toolchain_lock_file=toolchain_lock,
                netconvert_binary=netconvert,
                sumo_binary=sumo,
                traffic_side=traffic_side,
                timeout_seconds=timeout_seconds,
                producer=producer,
            )
        except Exception as exc:  # noqa: BLE001 - arm isolation is persisted.
            variant_dir.mkdir(parents=True, exist_ok=True)
            result = {
                "schema": "torii.teacher-free-topology-variant/v1",
                "status": "blocked",
                "machine_feasible": False,
                "workflow_state": "BLOCKED",
                "topology_hypothesis": topology,
                "candidate_plan_id": plan["candidate_plan_id"],
                "candidate_dag_node_id": plan["candidate_dag_node_id"],
                "automatic_topology_selection": False,
                "automatic_promotion_gate": "blocked",
                "producer": producer,
                "terminal_stage": "variant_exception",
                "reason": f"{type(exc).__name__}: {exc}",
            }
            summary_file = variant_dir / "summary.json"
            write_json_atomic(summary_file, result, sort_keys=True)
            result["summary_file"] = str(summary_file)
        variants.append(result)

    feasible = [item for item in variants if item.get("machine_feasible")]
    outcome = decide_topology_variant_outcomes(variants)
    decision = str(outcome["machine_decision"])
    workflow_status = str(outcome["status"])
    workflow_state = str(outcome["workflow_state"])

    blind = _write_blind_review_bundle(
        destination / "blind-review",
        feasible_variants=feasible,
    )
    preflight_arms = assessment["topology_arms"]
    rollback_index = {
        "schema": "torii.teacher-free-topology-rollback-index/v1",
        "source_osm": {"path": str(source_osm), "sha256": source_osm_sha256},
        "source_net": source_build["source_net"],
        "variants": [
            {
                "candidate_plan_id": item.get("candidate_plan_id"),
                "topology_hypothesis": item.get("topology_hypothesis"),
                "status": item.get("status"),
                "rollback_file": item.get("artifacts", {}).get("rollback"),
            }
            for item in variants
        ],
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
    }
    rollback_index_file = destination / "rollback-index.json"
    write_json_atomic(rollback_index_file, rollback_index, sort_keys=True)
    summary = {
        "schema": "torii.teacher-free-topology-workflow/v4",
        "status": workflow_status,
        "workflow_state": workflow_state,
        "machine_decision": decision,
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "field_timing_reconstruction": False,
        "scope_expansion_allowed": False,
        "producer": producer,
        "source_mutation": file_sha256(source_osm) != source_osm_sha256,
        "discovery_id": discovery["discovery_id"],
        "contract_id": contract["contract_id"],
        "topology_evidence_id": assessment["topology_evidence"][
            "topology_evidence_id"
        ],
        "candidate_dag_id": candidate_dag["candidate_dag_id"],
        "source_osm": {"path": str(source_osm), "sha256": source_osm_sha256},
        "source_net": source_build["source_net"],
        "materialized_variant_count": len(variants),
        "machine_feasible_variant_count": len(feasible),
        "machine_feasible_candidate_plan_ids": sorted(
            str(item["candidate_plan_id"]) for item in feasible
        ),
        "pre_materialization_topology_arms": preflight_arms,
        "variants": variants,
        "blind_review": blind,
        "artifacts": {
            "discovery": str(discovery_file),
            "topology_contract": str(contract_file),
            "topology_evidence": str(topology_evidence_file),
            "candidate_dag": str(candidate_dag_file),
            "source_build": str(source_build_file),
            "scope_derivation": str(scope_file),
            "rollback_index": str(rollback_index_file),
        },
        "claim_boundary": (
            "Review-ready means at least one preregistered arm passed all machine "
            "gates. Multiple feasible arms remain blind-review alternatives; no "
            "arm is automatically selected or promoted."
        ),
    }
    summary_file = destination / "summary.json"
    write_json_atomic(summary_file, summary, sort_keys=True)
    review_file = destination / "comparison.html"
    write_text_atomic(review_file, _comparison_html(summary))
    manifest_file = destination / "manifest.json"
    _write_manifest(
        manifest_file,
        destination=destination,
        status=workflow_status,
        source_osm=source_osm,
        source_osm_sha256=source_osm_sha256,
        toolchain_lock=toolchain_lock,
        contract=contract,
        decision=decision,
        producer=producer,
    )
    return {
        **summary,
        "summary_file": str(summary_file),
        "review_file": str(review_file),
        "manifest_file": str(manifest_file),
    }


def decide_topology_variant_outcomes(
    variants: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the frozen zero/one/many decision rule without choosing an arm."""

    feasible_count = sum(bool(item.get("machine_feasible")) for item in variants)
    if feasible_count == 0:
        decision = "reject_physical_cell_hypothesis_without_scope_expansion"
        status = "blocked"
        workflow_state = "BLOCKED"
    elif feasible_count == 1:
        decision = "suggest_single_machine_feasible_arm_for_human_review"
        status = "review_ready"
        workflow_state = "REVIEW_PENDING"
    else:
        decision = "blind_review_required"
        status = "review_ready"
        workflow_state = "REVIEW_PENDING"
    return {
        "status": status,
        "workflow_state": workflow_state,
        "machine_decision": decision,
        "machine_feasible_variant_count": feasible_count,
        "scope_expansion_allowed": False,
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "field_timing_reconstruction": False,
    }


def _write_blind_review_bundle(
    destination: Path,
    *,
    feasible_variants: list[Mapping[str, Any]],
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    sortable = []
    for item in feasible_variants:
        candidate = item.get("candidate_net", {})
        normalized_sha = str(candidate.get("normalized_sha256", ""))
        sortable.append((normalized_sha, str(item["candidate_plan_id"]), item))
    rows = []
    key_rows = []
    for index, (_, _, item) in enumerate(sorted(sortable), start=1):
        alias = f"variant-{chr(64 + index)}"
        source_net = Path(str(item["candidate_net"]["path"]))
        alias_net = destination / f"{alias}.net.xml"
        shutil.copy2(source_net, alias_net)
        source_overlay = Path(str(item["artifacts"]["review_overlay"]))
        alias_overlay = destination / f"{alias}.add.xml"
        shutil.copy2(source_overlay, alias_overlay)
        rows.append(
            {
                "blind_variant_id": alias,
                "candidate_net": str(alias_net),
                "candidate_net_sha256": file_sha256(alias_net),
                "candidate_net_normalized_sha256": normalized_net_sha256(alias_net),
                "review_overlay": str(alias_overlay),
                "all_machine_gates_pass": all(
                    status == "pass" for status in item.get("gates", {}).values()
                ),
                "automatic_topology_selection": False,
                "automatic_promotion_gate": "blocked",
            }
        )
        key_rows.append(
            {
                "blind_variant_id": alias,
                "topology_hypothesis": item["topology_hypothesis"],
                "candidate_plan_id": item["candidate_plan_id"],
                "candidate_dag_node_id": item["candidate_dag_node_id"],
                "original_candidate_net": str(source_net),
            }
        )
    review_payload = {
        "schema": "torii.teacher-free-topology-blind-review/v1",
        "status": "ready" if rows else "not_applicable",
        "variant_count": len(rows),
        "variants": rows,
        "review_question": (
            "Which candidate best represents the physical junction/cell while "
            "preserving lane movements, stop-line intent, and multimodal context?"
        ),
        "machine_label_hidden": True,
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
    }
    review_file = destination / "blind-review.json"
    write_json_atomic(review_file, review_payload, sort_keys=True)
    key_file = destination / "blind-key.json"
    write_json_atomic(
        key_file,
        {
            "schema": "torii.teacher-free-topology-blind-key/v1",
            "restricted": True,
            "mapping": key_rows,
        },
        sort_keys=True,
    )
    html_file = destination / "index.html"
    write_text_atomic(html_file, _blind_html(review_payload))
    return {
        "status": review_payload["status"],
        "variant_count": len(rows),
        "review_file": str(review_file),
        "review_html": str(html_file),
        "restricted_key_file": str(key_file),
    }


def _write_terminal_bundle(
    *,
    destination: Path,
    status: str,
    source_osm: Path,
    source_osm_sha256: str,
    toolchain_lock: Path,
    producer: Mapping[str, Any],
    discovery: Mapping[str, Any],
    contract: Mapping[str, Any],
    details: Mapping[str, Any],
) -> dict[str, Any]:
    summary = {
        "schema": "torii.teacher-free-topology-workflow/v4",
        "status": status,
        "workflow_state": "BLOCKED" if status == "blocked" else "HYPOTHESES_READY",
        "machine_decision": "no_candidate_materialized",
        "automatic_topology_selection": False,
        "automatic_promotion_gate": "blocked",
        "field_timing_reconstruction": False,
        "scope_expansion_allowed": False,
        "producer": dict(producer),
        "source_mutation": file_sha256(source_osm) != source_osm_sha256,
        "discovery_id": discovery["discovery_id"],
        "contract_id": contract["contract_id"],
        "candidate_written": False,
        "details": dict(details),
        "materialization_blockers": contract["materialization_blockers"],
        "candidate_assessments": contract["candidate_assessments"],
        "claim_boundary": (
            "No candidate network was written. Unresolved evidence remains a "
            "fail-closed result and does not authorize scope expansion."
        ),
    }
    summary_file = destination / "summary.json"
    write_json_atomic(summary_file, summary, sort_keys=True)
    manifest_file = destination / "manifest.json"
    _write_manifest(
        manifest_file,
        destination=destination,
        status=status,
        source_osm=source_osm,
        source_osm_sha256=source_osm_sha256,
        toolchain_lock=toolchain_lock,
        contract=contract,
        decision="no_candidate_materialized",
        producer=producer,
    )
    return {
        **summary,
        "summary_file": str(summary_file),
        "manifest_file": str(manifest_file),
    }


def _comparison_html(summary: Mapping[str, Any]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('topology_hypothesis')))}</td>"
        f"<td>{html.escape(str(item.get('status')))}</td>"
        f"<td>{html.escape(str(item.get('machine_feasible')))}</td>"
        "</tr>"
        for item in summary["variants"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Torii teacher-free topology discrimination v4</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:72rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.45rem;text-align:left}}</style>
</head><body>
<h1>Teacher-free topology discrimination v4</h1>
<p>Status: <strong>{html.escape(str(summary['status']))}</strong>. Machine decision: <strong>{html.escape(str(summary['machine_decision']))}</strong>.</p>
<table><thead><tr><th>Research arm</th><th>Status</th><th>Machine feasible</th></tr></thead><tbody>{rows}</tbody></table>
<p>Automatic topology selection, field timing, and promotion remain blocked.</p>
<p>{html.escape(str(summary['claim_boundary']))}</p>
</body></html>"""


def _blind_html(payload: Mapping[str, Any]) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item['blind_variant_id']))}</td>"
        f"<td>{html.escape(Path(str(item['candidate_net'])).name)}</td>"
        f"<td>{html.escape(Path(str(item['review_overlay'])).name)}</td>"
        "</tr>"
        for item in payload["variants"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Blind topology review</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;max-width:70rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccc;padding:.45rem;text-align:left}}</style></head>
<body><h1>Blind topology review</h1>
<p>{html.escape(str(payload['review_question']))}</p>
<table><thead><tr><th>Variant</th><th>Network</th><th>Display overlay</th></tr></thead><tbody>{rows}</tbody></table>
<p>Machine labels are hidden. This package cannot authorize promotion.</p></body></html>"""


def _write_manifest(
    path: Path,
    *,
    destination: Path,
    status: str,
    source_osm: Path,
    source_osm_sha256: str,
    toolchain_lock: Path,
    contract: Mapping[str, Any],
    decision: str,
    producer: Mapping[str, Any],
) -> None:
    artifacts = []
    for artifact in sorted(destination.rglob("*")):
        if not artifact.is_file() or artifact == path:
            continue
        artifacts.append(
            {
                "role": "generated",
                "path": str(artifact),
                "sha256": file_sha256(artifact),
            }
        )
    write_json_atomic(
        path,
        {
            "schema": "torii.teacher-free-topology-manifest/v1",
            "status": status,
            "machine_decision": decision,
            "automatic_topology_selection": False,
            "automatic_promotion_gate": "blocked",
            "field_timing_reconstruction": False,
            "scope_expansion_allowed": False,
            "producer": dict(producer),
            "source_mutation": file_sha256(source_osm) != source_osm_sha256,
            "contract_id": contract["contract_id"],
            "inputs": [
                {
                    "role": "frozen_osm_bbox",
                    "path": str(source_osm),
                    "sha256": source_osm_sha256,
                },
                {
                    "role": "toolchain_lock",
                    "path": str(toolchain_lock),
                    "sha256": file_sha256(toolchain_lock),
                },
            ],
            "artifacts": artifacts,
        },
        sort_keys=True,
    )


def _candidate_by_id(
    discovery: Mapping[str, Any],
    candidate_id: str,
) -> Mapping[str, Any]:
    matches = [
        item
        for item in discovery.get("candidates", ())
        if item.get("candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("Topology contract does not bind one discovered candidate.")
    return matches[0]


def _assessment_by_id(
    contract: Mapping[str, Any],
    candidate_id: str,
) -> Mapping[str, Any]:
    matches = [
        item
        for item in contract.get("candidate_assessments", ())
        if item.get("discovered_candidate_id") == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError("Topology contract does not bind one candidate assessment.")
    return matches[0]


def _derive_guard_junction_ids(
    net_file: Path,
    target_junction_ids: tuple[str, ...],
) -> tuple[str, ...]:
    target = set(target_junction_ids)
    guards: set[str] = set()
    for edge in ET.parse(net_file).getroot().findall("edge"):
        if edge.attrib.get("function") == "internal" or str(
            edge.attrib.get("id", "")
        ).startswith(":"):
            continue
        source = str(edge.attrib.get("from", ""))
        destination = str(edge.attrib.get("to", ""))
        if source in target and destination and destination not in target:
            guards.add(destination)
        if destination in target and source and source not in target:
            guards.add(source)
    return tuple(sorted(guards))


def _command_built_file(result: Mapping[str, Any], path: Path) -> bool:
    return (
        result.get("status") == "pass"
        and result.get("returncode") == 0
        and path.is_file()
        and path.stat().st_size > 0
    )


def _topology_token(topology: str) -> str:
    return {
        "preserve_split_shared_controller": "hs",
        "merge_physical_cell": "hm",
        "partial_internal_repair": "hp",
    }[topology]


def _reset_owned_directory(destination: Path) -> None:
    owner = destination / "teacher-free-topology-workflow.owner.json"
    if destination.exists() and any(destination.iterdir()):
        if not owner.is_file():
            raise ValueError(
                "Refusing to clear a non-empty topology directory without Torii ownership metadata."
            )
        payload = json.loads(owner.read_text(encoding="utf-8"))
        if payload.get("schema") != _OWNER_SCHEMA:
            raise ValueError("Teacher-free topology ownership metadata is invalid.")
        if payload.get("owned_root") != str(destination):
            raise ValueError(
                "Teacher-free topology ownership root does not match the output directory."
            )
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        owner,
        {
            "schema": _OWNER_SCHEMA,
            "purpose": "generated teacher-free topology discrimination artifacts",
            "owned_root": str(destination),
        },
        sort_keys=True,
    )
