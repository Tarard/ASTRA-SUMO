from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
import xml.etree.ElementTree as ET

from .command_runner import run_command
from .connection_mode_audit import audit_network_connection_mode


TlsPhysicalConnectionKey = tuple[str, str, str, str, str]


def build_tls_signal_grouping_variant(
    *,
    source_net_file: Path,
    output_dir: Path,
    prefix: str = "tls_signal_grouping",
    max_shared_linkindex_groups: int,
    control_key_by_connection: Mapping[TlsPhysicalConnectionKey, str] | None = None,
) -> dict[str, Any]:
    """Build a review variant that shares only physically compatible TLS columns.

    Identical phase columns are a necessary but insufficient merge condition.
    When SUMO request/foe topology is present, every physical request in a
    proposed shared group must be non-foe with every other request.  Optional
    stable control keys further prevent coincidentally identical program columns
    from being treated as one semantic control.  The report always persists the
    resulting control-key-to-linkIndex relation, including one-to-many bindings
    when foe movements must remain physically separate.
    """
    if not source_net_file.exists():
        return _failure(f"net file does not exist: {source_net_file}")
    if max_shared_linkindex_groups <= 0:
        return {
            "status": "skipped",
            "claim_status": "diagnostic-demo",
            "tls_signal_grouping_status": "not_needed",
            "tls_signal_grouping_variant_file": "",
            "tls_signal_grouping_max_shared_linkindex_groups": max_shared_linkindex_groups,
            "warnings": [],
        }
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_file = output_dir / "tls_signal_grouped.net.xml"
    plan_file = _short_output_path(output_dir, prefix, "_plan.json")
    variant_file.write_bytes(source_net_file.read_bytes())
    compression = _compress_identical_tls_signal_columns(
        variant_file,
        max_shared_linkindex_groups=max_shared_linkindex_groups,
        control_key_by_connection=control_key_by_connection,
    )
    counts = _tls_counts(variant_file)
    plan = {
        "source_net_file": str(source_net_file),
        "variant_file": str(variant_file),
        "tls_signal_grouping_max_shared_linkindex_groups": max_shared_linkindex_groups,
        **compression,
    }
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    warnings = ["TLS signal grouping variant requires SUMO load and Netedit review before adoption"]
    if compression["tls_signal_grouping_request_foe_evidence_status"] == "unavailable":
        warnings.append(
            "No SUMO request/foe matrix was available; identical-column grouping has no physical conflict proof"
        )
    if compression["tls_signal_grouping_unresolved_request_pair_count"]:
        warnings.append(
            "Some identical signal columns were kept separate because their physical request binding was unresolved"
        )
    return {
        "status": "pass",
        "claim_status": "blocked",
        "tls_signal_grouping_status": "variant_created_for_review",
        "tls_signal_grouping_plan_file": str(plan_file),
        "tls_signal_grouping_variant_file": str(variant_file),
        "tls_signal_grouping_max_shared_linkindex_groups": max_shared_linkindex_groups,
        **compression,
        **counts,
        "warnings": warnings,
    }


def build_tls_low_vehicle_control_variant(
    *,
    source_net_file: Path,
    tls_control_review_queue: list[Mapping[str, Any]],
    output_dir: Path,
    prefix: str = "tls_low_vehicle_control",
    max_removed_controlled_connections: int | None = None,
    max_selected_tllogic_count: int | None = None,
) -> dict[str, Any]:
    if not source_net_file.exists():
        return _failure(f"net file does not exist: {source_net_file}")
    queue = [
        item
        for item in tls_control_review_queue
        if item.get("review_type") == "downgrade_low_vehicle_approach_tls" and item.get("tl_id")
    ]
    if not queue or max_removed_controlled_connections == 0 or max_selected_tllogic_count == 0:
        return {
            "status": "skipped",
            "claim_status": "diagnostic-demo",
            "tls_low_vehicle_control_status": "not_needed",
            "tls_low_vehicle_control_variant_file": "",
            "tls_low_vehicle_control_selected_tllogic_count": 0,
            "tls_low_vehicle_control_removed_connection_count": 0,
            "warnings": [],
        }
    queue.sort(
        key=lambda item: (
            int(item.get("controlled_passenger_from_edge_count", 0) or 0),
            int(item.get("controlled_connection_count", 0) or 0),
            str(item.get("tl_id", "")),
        )
    )
    selected: list[Mapping[str, Any]] = []
    selected_connection_count = 0
    for item in queue:
        if max_selected_tllogic_count is not None and len(selected) >= max_selected_tllogic_count:
            break
        connection_count = int(item.get("controlled_connection_count", 0) or 0)
        if (
            max_removed_controlled_connections is not None
            and selected_connection_count + connection_count > max_removed_controlled_connections
        ):
            continue
        selected.append(item)
        selected_connection_count += connection_count
    if not selected:
        return {
            "status": "skipped",
            "claim_status": "diagnostic-demo",
            "tls_low_vehicle_control_status": "budget_exhausted",
            "tls_low_vehicle_control_variant_file": "",
            "tls_low_vehicle_control_selected_tllogic_count": 0,
            "tls_low_vehicle_control_removed_connection_count": 0,
            "warnings": [],
        }

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_file = output_dir / "tls_low_vehicle_control_review.net.xml"
    plan_file = _short_output_path(output_dir, prefix, "_plan.json")
    variant_file.write_bytes(source_net_file.read_bytes())
    selected_tl_ids = [str(item["tl_id"]) for item in selected]
    demotion = demote_tls_ids(variant_file, selected_tl_ids)
    counts = _tls_counts(variant_file)
    plan = {
        "source_net_file": str(source_net_file),
        "variant_file": str(variant_file),
        "max_removed_controlled_connections": max_removed_controlled_connections,
        "max_selected_tllogic_count": max_selected_tllogic_count,
        "selected_tl_ids": selected_tl_ids,
        "selected_connection_count": selected_connection_count,
        "review_policy": "review-only variant; promote only after SUMO load and reference/map validation",
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "status": "pass",
        "claim_status": "blocked",
        "tls_low_vehicle_control_status": "variant_created_for_review",
        "tls_low_vehicle_control_variant_file": str(variant_file),
        "tls_low_vehicle_control_plan_file": str(plan_file),
        "tls_low_vehicle_control_selected_tllogic_count": len(selected_tl_ids),
        "tls_low_vehicle_control_selected_connection_count": selected_connection_count,
        **demotion,
        **counts,
        "warnings": ["Low-vehicle TLS demotion requires SUMO load, Netedit, and map/reference review before adoption"],
    }


def build_tls_non_controller_junction_demotion_variant(
    *,
    source_net_file: Path,
    output_dir: Path,
    prefix: str = "tls_non_controller_junction_demotion",
) -> dict[str, Any]:
    if not source_net_file.exists():
        return _failure(f"net file does not exist: {source_net_file}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    variant_file = output_dir / "tls_non_controller_junction_demoted.net.xml"
    plan_file = _short_output_path(output_dir, prefix, "_plan.json")
    variant_file.write_bytes(source_net_file.read_bytes())
    demotion = _demote_non_controller_traffic_light_junctions(variant_file)
    if demotion["tls_non_controller_traffic_light_junction_demoted_count"] == 0:
        return {
            "status": "skipped",
            "claim_status": "diagnostic-demo",
            "tls_non_controller_junction_demotion_status": "not_needed",
            "tls_non_controller_junction_demotion_variant_file": "",
            **demotion,
            "warnings": [],
        }
    counts = _tls_counts(variant_file)
    plan = {
        "source_net_file": str(source_net_file),
        "variant_file": str(variant_file),
        "demotion_policy": (
            "demote traffic_light junctions whose id is not a tlLogic id and not used as a connection tl id"
        ),
        "review_policy": "review-only variant; promote only after SUMO load and reference/map validation",
        **demotion,
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "status": "pass",
        "claim_status": "blocked",
        "tls_non_controller_junction_demotion_status": "variant_created_for_review",
        "tls_non_controller_junction_demotion_variant_file": str(variant_file),
        "tls_non_controller_junction_demotion_plan_file": str(plan_file),
        **demotion,
        **counts,
        "warnings": [
            "Non-controller traffic-light junction demotion requires SUMO load, Netedit, and reference review before adoption"
        ],
    }


def build_tls_aggregation_variant(
    *,
    net_file: Path,
    tls_audit_report: Mapping[str, Any],
    output_dir: Path,
    prefix: str = "tls_aggregation",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
    controlled_nodes_by_tls_func: Callable[[Path], dict[str, list[str]]] | None = None,
    tls_guess_signals_dist_m: float | None = None,
) -> dict[str, Any]:
    if not net_file.exists():
        return _failure(f"net file does not exist: {net_file}")
    net_file = net_file.resolve()
    output_dir = output_dir.resolve()

    cluster_count = _int_field(tls_audit_report, "tls_cluster_count")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_file = _short_output_path(output_dir, prefix, "_plan.json")
    candidates_file = _short_output_path(output_dir, prefix, "_representatives.csv")
    variant_file = output_dir / "tls_aggregated.net.xml"
    command_record = _short_output_path(output_dir, prefix, "_netconvert.cmd.txt")
    source_tls_counts = _source_tls_program_counts(net_file)

    if cluster_count == 0:
        plan_file.write_text(
            json.dumps(
                {
                    "tls_aggregation_status": "not_needed",
                    "tls_physical_cluster_count": 0,
                    "tls_program_policy": "not_applicable_no_tls_clusters",
                    **source_tls_counts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_representatives_csv(candidates_file, [])
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "tls_aggregation_status": "not_needed",
            "tls_physical_cluster_count": 0,
            "tls_aggregation_plan_file": str(plan_file),
            "tls_aggregation_representatives_file": str(candidates_file),
            "tls_aggregation_variant_file": "",
            "tls_aggregation_command_record": "",
            "tls_aggregation_netconvert": {},
            "tls_program_policy": "not_applicable_no_tls_clusters",
            **source_tls_counts,
            "warnings": [],
        }

    clusters_path = Path(str(tls_audit_report.get("clusters_file", "")))
    if not clusters_path.exists():
        return {
            **_failure(f"TLS clusters file does not exist: {clusters_path}"),
            "tls_aggregation_plan_file": str(plan_file),
            "tls_aggregation_variant_file": "",
        }

    clusters = _read_clusters(clusters_path)
    try:
        controlled_nodes_by_tls = (
            controlled_nodes_by_tls_func(net_file)
            if controlled_nodes_by_tls_func is not None
            else _controlled_nodes_by_tls(net_file)
        )
    except Exception as exc:  # noqa: BLE001 - callback boundary is persisted as a blocked report.
        return {
            **_failure(f"could not derive TLS-controlled junctions: {type(exc).__name__}: {exc}"),
            "tls_aggregation_plan_file": str(plan_file),
            "tls_aggregation_representatives_file": str(candidates_file),
            "tls_aggregation_variant_file": "",
        }
    representatives = _representatives_for_clusters(clusters, controlled_nodes_by_tls)
    _write_representatives_csv(candidates_file, representatives)
    tls_join_dist_m = 20.0
    tls_representative_prune_dist_m = 35.0
    representative_node_ids = list(
        dict.fromkeys(row["representative_node_id"] for row in representatives if row["representative_node_id"])
    )
    representative_node_ids, spatially_pruned_representatives = _spatially_prune_representatives(
        representative_node_ids,
        _junction_positions(net_file),
        tls_representative_prune_dist_m,
    )
    plan = {
        "tls_aggregation_status": "planned_for_review_variant",
        "net_file": str(net_file),
        "variant_file": str(variant_file),
        "tls_physical_cluster_count": len(clusters),
        "representative_count": len(representatives),
        "tls_set_representative_count": len(representative_node_ids),
        "tls_set_spatially_pruned_count": len(spatially_pruned_representatives),
        "tls_set_spatially_pruned_representatives": spatially_pruned_representatives,
        "tls_guess_signals_dist_m": tls_guess_signals_dist_m,
        "tls_program_policy": "discard_loaded_programs_rebuild_tls_set",
        **source_tls_counts,
        "review_policy": (
            "create a separate network with one real SUMO junction set as TLS per physical TLS cluster; "
            "do not overwrite the source network or treat the variant as map-confirmed"
        ),
        "representatives": representatives,
    }
    plan_file.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    if not representative_node_ids:
        return {
            **_failure("no representative junction ids could be derived from TLS clusters"),
            "tls_aggregation_plan_file": str(plan_file),
            "tls_aggregation_representatives_file": str(candidates_file),
            "tls_aggregation_variant_file": "",
        }

    command = [
        "netconvert",
        "--sumo-net-file",
        str(net_file),
        "--tls.discard-loaded",
        "--tls.set",
        ",".join(representative_node_ids),
        "--tls.rebuild",
        "--tls.join",
        "--tls.join-dist",
        str(int(tls_join_dist_m)),
        "--tls.default-type",
        "actuated",
        "--output-file",
        _command_path(variant_file, output_dir),
    ]
    if tls_guess_signals_dist_m is not None:
        command[command.index("--output-file") : command.index("--output-file")] = [
            "--tls.guess-signals",
            "--tls.guess-signals.dist",
            str(int(tls_guess_signals_dist_m)),
        ]
    command_record.write_text(" ".join(command) + "\n", encoding="utf-8")
    try:
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
    except OSError as exc:
        return {
            **_failure(f"{type(exc).__name__}: {exc}"),
            "tls_aggregation_plan_file": str(plan_file),
            "tls_aggregation_representatives_file": str(candidates_file),
            "tls_aggregation_variant_file": str(variant_file),
            "tls_aggregation_command_record": str(command_record),
        }

    status = "pass" if result.get("status") == "pass" and variant_file.exists() else "fail"
    tls_program_preservation = (
        _preserve_compatible_tls_programs(net_file, variant_file, representatives) if status == "pass" else _empty_preservation()
    )
    tls_orphan_cleanup = _demote_uncontrolled_tls_artifacts(variant_file) if status == "pass" else _empty_tls_orphan_cleanup()
    counts = _tls_counts(variant_file) if variant_file.exists() else {}
    tls_connection_preservation = _tls_connection_preservation(source_tls_counts, counts) if status == "pass" else {}
    warnings = ["TLS aggregation variant requires Google Maps and Netedit review before adoption"]
    if source_tls_counts["source_tl_logic_count"] and not tls_program_preservation["tls_program_preserved_count"]:
        warnings.append(
            "TLS aggregation discards loaded tlLogic programs via --tls.discard-loaded; "
            "actuated/minDur/maxDur semantics are not preserved"
        )
    elif tls_program_preservation["tls_program_skipped_count"]:
        warnings.append("Some source tlLogic programs were not compatible with the rebuilt TLS link state length")
    if tls_connection_preservation.get("tls_controlled_connection_preservation_status") == "fail":
        warnings.append(
            "TLS aggregation lost "
            f"{tls_connection_preservation['tls_controlled_connection_regression_count']} controlled TLS connections; "
            "keep the source network for TLS parity review"
        )
    if status != "pass":
        warnings.append(f"TLS aggregation variant was not created: {variant_file}")
    return {
        "status": status,
        "claim_status": "blocked" if status == "pass" else "construction-invalid",
        "tls_aggregation_status": "variant_created_for_review" if status == "pass" else "failed",
        "tls_physical_cluster_count": len(clusters),
        "tls_representative_count": len(representatives),
        "tls_aggregation_plan_file": str(plan_file),
        "tls_aggregation_representatives_file": str(candidates_file),
        "tls_aggregation_variant_file": str(variant_file),
        "tls_aggregation_command_record": str(command_record),
        "tls_aggregation_netconvert": result,
        "tls_program_policy": "discard_loaded_programs_rebuild_tls_set",
        "tls_set_representative_count": len(representative_node_ids),
        "tls_set_spatially_pruned_count": len(spatially_pruned_representatives),
        "tls_set_spatially_pruned_representatives": spatially_pruned_representatives,
        "tls_guess_signals_dist_m": tls_guess_signals_dist_m,
        **source_tls_counts,
        **tls_program_preservation,
        **tls_orphan_cleanup,
        **counts,
        **tls_connection_preservation,
        "warnings": warnings,
    }


def _read_clusters(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _command_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd))
    except ValueError:
        return str(path)


def _representatives_for_clusters(
    clusters: list[Mapping[str, Any]],
    controlled_nodes_by_tls: Mapping[str, list[str]],
) -> list[dict[str, str]]:
    rows = []
    for cluster in clusters:
        tls_ids = _split_tls_ids(str(cluster.get("tls_ids", "")))
        node_counts: dict[str, int] = {}
        for tls_id in tls_ids:
            for node_id in controlled_nodes_by_tls.get(tls_id, []):
                node_counts[node_id] = node_counts.get(node_id, 0) + 1
        representative = ""
        if node_counts:
            representative = sorted(node_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        rows.append(
            {
                "cluster_id": str(cluster.get("cluster_id", "")),
                "representative_node_id": representative,
                "tls_ids": ";".join(tls_ids),
                "tls_count": str(cluster.get("tls_count", len(tls_ids))),
                "google_maps_url": str(cluster.get("google_maps_url", "")),
            }
        )
    return rows


def _split_tls_ids(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", ";").split(";") if item.strip()]


def _junction_positions(net_file: Path) -> dict[str, tuple[float, float]]:
    root = ET.parse(net_file).getroot()
    positions = {}
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if not junction_id or junction_id.startswith(":"):
            continue
        try:
            positions[junction_id] = (float(junction.attrib.get("x", "0")), float(junction.attrib.get("y", "0")))
        except ValueError:
            continue
    return positions


def _spatially_prune_representatives(
    representative_node_ids: list[str],
    positions: Mapping[str, tuple[float, float]],
    join_dist_m: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    kept: list[str] = []
    pruned: list[dict[str, Any]] = []
    max_distance_sq = join_dist_m * join_dist_m
    for node_id in representative_node_ids:
        position = positions.get(node_id)
        matched_kept = ""
        matched_distance_sq = 0.0
        if position is not None:
            for kept_id in kept:
                kept_position = positions.get(kept_id)
                if kept_position is None:
                    continue
                distance_sq = (position[0] - kept_position[0]) ** 2 + (position[1] - kept_position[1]) ** 2
                if distance_sq <= max_distance_sq:
                    matched_kept = kept_id
                    matched_distance_sq = distance_sq
                    break
        if matched_kept:
            pruned.append(
                {
                    "representative_node_id": node_id,
                    "kept_representative_node_id": matched_kept,
                    "distance_m": round(matched_distance_sq**0.5, 2),
                }
            )
        else:
            kept.append(node_id)
    return kept, pruned


def _controlled_nodes_by_tls(net_file: Path) -> dict[str, list[str]]:
    try:
        import sumolib.net  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("sumolib is required for TLS aggregation") from exc

    net = sumolib.net.readNet(str(net_file), withPrograms=True)
    mapping: dict[str, list[str]] = {}
    for tls in net.getTrafficLights():
        node_ids = []
        try:
            node_ids.append(net.getNode(tls.getID()).getID())
        except (KeyError, RuntimeError, ValueError):
            # Some multi-node controllers do not have a node with the TLS id;
            # connection endpoint nodes below remain authoritative.
            node_ids = []
        for incoming_lane, outgoing_lane, _link in tls.getConnections():
            for node in (incoming_lane.getEdge().getToNode(), outgoing_lane.getEdge().getFromNode()):
                node_id = node.getID()
                if not node_id.startswith(":"):
                    node_ids.append(node_id)
        mapping[tls.getID()] = sorted(set(node_ids))
    return mapping


def _write_representatives_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["cluster_id", "representative_node_id", "tls_ids", "tls_count", "google_maps_url"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _tls_counts(net_file: Path) -> dict[str, int]:
    root = ET.parse(net_file).getroot()
    connection_counts = _tls_connection_counts(root)
    return {
        "tls_aggregated_traffic_light_junction_count": sum(
            1 for junction in root.findall("junction") if junction.attrib.get("type") == "traffic_light"
        ),
        "tls_aggregated_tl_logic_count": len(root.findall("tlLogic")),
        "tls_aggregated_controlled_connection_count": connection_counts["controlled"],
        "tls_aggregated_tl_connection_missing_linkindex_count": connection_counts["missing_linkindex"],
    }


def _preserve_compatible_tls_programs(
    source_net_file: Path,
    variant_file: Path,
    representatives: list[Mapping[str, str]],
) -> dict[str, Any]:
    source_root = ET.parse(source_net_file).getroot()
    target_tree = ET.parse(variant_file)
    target_root = target_tree.getroot()
    source_by_id = {tl.attrib["id"]: tl for tl in source_root.findall("tlLogic") if tl.attrib.get("id")}
    target_by_id = {tl.attrib["id"]: tl for tl in target_root.findall("tlLogic") if tl.attrib.get("id")}
    preserved = 0
    skipped: list[dict[str, str]] = []
    for row in representatives:
        target_id = str(row.get("representative_node_id", ""))
        target = target_by_id.get(target_id)
        if target is None:
            skipped.append({"representative_node_id": target_id, "reason": "missing_target_tllogic"})
            continue
        source = next(
            (
                source_by_id[tls_id]
                for tls_id in _split_tls_ids(str(row.get("tls_ids", "")))
                if tls_id in source_by_id and _tls_program_compatible(source_by_id[tls_id], target)
            ),
            None,
        )
        if source is None:
            skipped.append({"representative_node_id": target_id, "reason": "no_compatible_source_tllogic"})
            continue
        replacement = ET.Element("tlLogic", dict(source.attrib))
        replacement.set("id", target_id)
        for child in source:
            replacement.append(copy.deepcopy(child))
        index = list(target_root).index(target)
        target_root.remove(target)
        target_root.insert(index, replacement)
        preserved += 1
    if preserved:
        ET.indent(target_root, space="    ")
        target_tree.write(variant_file, encoding="utf-8", xml_declaration=True)
    return {
        "tls_program_preserved_count": preserved,
        "tls_program_skipped_count": len(skipped),
        "tls_program_skips": skipped,
    }


def _tls_program_compatible(source: ET.Element, target: ET.Element) -> bool:
    source_lengths = {len(phase.attrib.get("state", "")) for phase in source.findall("phase") if phase.attrib.get("state")}
    target_lengths = {len(phase.attrib.get("state", "")) for phase in target.findall("phase") if phase.attrib.get("state")}
    return bool(source_lengths) and source_lengths == target_lengths


def _compress_identical_tls_signal_columns(
    net_file: Path,
    *,
    max_shared_linkindex_groups: int,
    control_key_by_connection: Mapping[TlsPhysicalConnectionKey, str] | None = None,
) -> dict[str, Any]:
    tree = ET.parse(net_file)
    root = tree.getroot()
    tl_ids = {tl_logic.attrib["id"] for tl_logic in root.findall("tlLogic") if tl_logic.attrib.get("id")}
    connections = root.findall("connection")
    connections_by_tl: dict[str, list[tuple[ET.Element, int, int]]] = {tl_id: [] for tl_id in tl_ids}
    for connection_index, connection in enumerate(connections):
        tl_id = connection.attrib.get("tl", "")
        if tl_id not in tl_ids or not connection.attrib.get("linkIndex"):
            continue
        try:
            connections_by_tl.setdefault(tl_id, []).append(
                (connection, int(connection.attrib["linkIndex"]), connection_index)
            )
        except ValueError:
            continue

    foe_evidence = _build_tls_request_foe_evidence(root)
    old_link_indexes_by_connection: dict[int, int] = {
        connection_index: old_index
        for rows in connections_by_tl.values()
        for _connection, old_index, connection_index in rows
    }
    control_keys_by_tl_index = _control_keys_by_tl_link_index(
        connections_by_tl,
        control_key_by_connection=control_key_by_connection,
    )

    signature_by_tl_index: dict[str, dict[int, tuple[str, ...]]] = {}
    signature_groups_by_tl: dict[str, list[list[int]]] = {}
    for tl_logic in root.findall("tlLogic"):
        tl_id = tl_logic.attrib.get("id", "")
        phases = tl_logic.findall("phase")
        used_linkindexes = sorted(
            {link_index for _, link_index, _connection_index in connections_by_tl.get(tl_id, [])}
        )
        if len(used_linkindexes) < 2 or not phases:
            continue
        phase_states = [phase.attrib.get("state", "") for phase in phases]
        if any(max(used_linkindexes) >= len(state) for state in phase_states):
            continue
        groups_by_signature: dict[tuple[str, ...], list[int]] = {}
        for old_index in used_linkindexes:
            signature = tuple(state[old_index] for state in phase_states)
            signature_by_tl_index.setdefault(tl_id, {})[old_index] = signature
            groups_by_signature.setdefault(signature, []).append(old_index)
        groups = [group for group in groups_by_signature.values() if len(group) > 1]
        if groups:
            signature_groups_by_tl[tl_id] = groups

    connection_indices_by_tl_link: dict[tuple[str, int], set[int]] = {}
    for tl_id, rows in connections_by_tl.items():
        for _connection, old_index, connection_index in rows:
            connection_indices_by_tl_link.setdefault((tl_id, old_index), set()).add(connection_index)

    blocked_foe_pairs: list[dict[str, Any]] = []
    unresolved_pairs: set[tuple[str, int, int]] = set()
    merge_candidates: list[tuple[int, str, tuple[int, ...]]] = []
    for tl_id, signature_groups in signature_groups_by_tl.items():
        for signature_group in signature_groups:
            share_compatibility: dict[tuple[int, int], bool] = {}
            for first_position, first_index in enumerate(sorted(signature_group)):
                for second_index in sorted(signature_group)[first_position + 1 :]:
                    share_compatibility[(first_index, second_index)] = _tls_link_indexes_can_share(
                        tl_id=tl_id,
                        first_index=first_index,
                        second_index=second_index,
                        connection_indices_by_tl_link=connection_indices_by_tl_link,
                        foe_evidence=foe_evidence,
                        control_keys_by_tl_index=control_keys_by_tl_index,
                        control_keys_required=control_key_by_connection is not None,
                        blocked_foe_pairs=blocked_foe_pairs,
                        unresolved_pairs=unresolved_pairs,
                        old_link_indexes_by_connection=old_link_indexes_by_connection,
                    )
            partitions: list[list[int]] = []
            for old_index in sorted(signature_group):
                target_partition = next(
                    (
                        partition
                        for partition in partitions
                        if all(
                            share_compatibility[
                                (min(old_index, other_index), max(old_index, other_index))
                            ]
                            for other_index in partition
                        )
                    ),
                    None,
                )
                if target_partition is None:
                    partitions.append([old_index])
                else:
                    target_partition.append(old_index)
            merge_candidates.extend(
                (len(partition) - 1, tl_id, tuple(partition))
                for partition in partitions
                if len(partition) > 1
            )
    merge_candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    groups_by_tl: dict[str, list[list[int]]] = {}
    remaining = max_shared_linkindex_groups
    for _savings, tl_id, group in merge_candidates:
        if remaining <= 0:
            break
        groups_by_tl.setdefault(tl_id, []).append(list(group))
        remaining -= 1

    remapped_connections = 0
    final_index_by_tl_old: dict[str, dict[int, int]] = {
        tl_id: {
            old_index: old_index
            for _connection, old_index, _connection_index in rows
        }
        for tl_id, rows in connections_by_tl.items()
    }
    for tl_logic in root.findall("tlLogic"):
        tl_id = tl_logic.attrib.get("id", "")
        if tl_id not in groups_by_tl:
            continue
        phases = tl_logic.findall("phase")
        phase_states = [phase.attrib.get("state", "") for phase in phases]
        old_target_index: dict[int, int] = {}
        for group in groups_by_tl[tl_id]:
            target = min(group)
            for old_index in group:
                old_target_index[old_index] = target
        kept_old_indexes = sorted(
            {
                old_target_index.get(index, index)
                for _connection, index, _connection_index in connections_by_tl[tl_id]
            }
        )
        new_index_by_old = {old_index: new_index for new_index, old_index in enumerate(kept_old_indexes)}
        final_index_by_tl_old[tl_id] = {
            old_index: new_index_by_old[old_target_index.get(old_index, old_index)]
            for _connection, old_index, _connection_index in connections_by_tl[tl_id]
        }
        for connection, old_index, _connection_index in connections_by_tl[tl_id]:
            new_index = new_index_by_old[old_target_index.get(old_index, old_index)]
            if str(new_index) != connection.attrib.get("linkIndex"):
                connection.set("linkIndex", str(new_index))
                remapped_connections += 1
        for phase, state in zip(phases, phase_states):
            phase.set("state", "".join(state[index] for index in kept_old_indexes))

    merged_groups = sum(len(groups) for groups in groups_by_tl.values())
    if groups_by_tl:
        ET.indent(root, space="    ")
        tree.write(net_file, encoding="utf-8", xml_declaration=True)
    control_bindings = _build_tls_control_key_bindings(
        connections_by_tl=connections_by_tl,
        signature_by_tl_index=signature_by_tl_index,
        control_keys_by_tl_index=control_keys_by_tl_index,
        final_index_by_tl_old=final_index_by_tl_old,
        foe_evidence=foe_evidence,
    )
    control_key_to_link_indices: dict[str, dict[str, list[int]]] = {}
    for binding in control_bindings:
        control_key_to_link_indices.setdefault(str(binding["controller_id"]), {})[
            str(binding["control_key"])
        ] = list(binding["sumo_link_indices"])
    return {
        "tls_signal_grouping_compressed_tllogic_count": len(groups_by_tl),
        "tls_signal_grouping_merged_group_count": merged_groups,
        "tls_signal_grouping_remapped_connection_count": remapped_connections,
        "tls_signal_grouping_request_foe_evidence_status": foe_evidence["status"],
        "tls_signal_grouping_request_bound_connection_count": len(
            foe_evidence["request_bound_connection_indices"]
        ),
        "tls_signal_grouping_blocked_foe_pair_count": len(blocked_foe_pairs),
        "tls_signal_grouping_blocked_foe_pairs": blocked_foe_pairs,
        "tls_signal_grouping_unresolved_request_pair_count": len(unresolved_pairs),
        "tls_signal_grouping_unresolved_request_pairs": [
            {
                "controller_id": tl_id,
                "source_link_indices": [first_index, second_index],
            }
            for tl_id, first_index, second_index in sorted(unresolved_pairs)
        ],
        "tls_signal_grouping_control_bindings": control_bindings,
        "tls_signal_grouping_control_key_to_link_indices": control_key_to_link_indices,
    }


def _build_tls_request_foe_evidence(root: ET.Element) -> dict[str, Any]:
    audit = audit_network_connection_mode(root)
    junction_by_id = {
        junction.attrib.get("id", ""): junction
        for junction in root.findall("junction")
        if junction.attrib.get("id")
    }
    request_bound_connection_indices: set[int] = set()
    controller_ids_with_request_evidence: set[str] = set()
    foe_pairs: set[frozenset[int]] = set()
    foe_pair_details: dict[frozenset[int], dict[str, Any]] = {}
    for record in audit.get("junctions", []):
        junction_id = str(record.get("junction_id", ""))
        junction = junction_by_id.get(junction_id)
        if junction is None:
            continue
        requests = {
            request_index: request
            for request in junction.findall("request")
            if (request_index := _safe_int(request.attrib.get("index"))) is not None
        }
        bindings = record.get("connection_mode_audit", {}).get("request_foe_audit", {}).get(
            "request_bindings", []
        )
        valid_bindings = [
            binding
            for binding in bindings
            if _safe_int(binding.get("request_index")) is not None
            and _safe_int(binding.get("connection_index")) is not None
            and str(binding.get("tl", ""))
        ]
        for binding in valid_bindings:
            request_bound_connection_indices.add(int(binding["connection_index"]))
            controller_ids_with_request_evidence.add(str(binding["tl"]))
        for first_position, first in enumerate(valid_bindings):
            for second in valid_bindings[first_position + 1 :]:
                if first.get("tl") != second.get("tl"):
                    continue
                first_request = int(first["request_index"])
                second_request = int(second["request_index"])
                first_marks_second = _request_marks_foe(requests, first_request, second_request)
                second_marks_first = _request_marks_foe(requests, second_request, first_request)
                if not (first_marks_second or second_marks_first):
                    continue
                pair = frozenset(
                    {int(first["connection_index"]), int(second["connection_index"])}
                )
                foe_pairs.add(pair)
                foe_pair_details[pair] = {
                    "junction_id": junction_id,
                    "controller_id": str(first["tl"]),
                    "request_indices": [first_request, second_request],
                    "connection_indices": [
                        int(first["connection_index"]),
                        int(second["connection_index"]),
                    ],
                    "first_marks_second_as_foe": first_marks_second,
                    "second_marks_first_as_foe": second_marks_first,
                }
    return {
        "status": "available" if request_bound_connection_indices else "unavailable",
        "request_bound_connection_indices": request_bound_connection_indices,
        "controller_ids_with_request_evidence": controller_ids_with_request_evidence,
        "foe_pairs": foe_pairs,
        "foe_pair_details": foe_pair_details,
    }


def _tls_link_indexes_can_share(
    *,
    tl_id: str,
    first_index: int,
    second_index: int,
    connection_indices_by_tl_link: Mapping[tuple[str, int], set[int]],
    foe_evidence: Mapping[str, Any],
    control_keys_by_tl_index: Mapping[str, Mapping[int, set[str]]],
    control_keys_required: bool,
    blocked_foe_pairs: list[dict[str, Any]],
    unresolved_pairs: set[tuple[str, int, int]],
    old_link_indexes_by_connection: Mapping[int, int],
) -> bool:
    first_connections = connection_indices_by_tl_link.get((tl_id, first_index), set())
    second_connections = connection_indices_by_tl_link.get((tl_id, second_index), set())
    if control_keys_required:
        first_keys = control_keys_by_tl_index.get(tl_id, {}).get(first_index, set())
        second_keys = control_keys_by_tl_index.get(tl_id, {}).get(second_index, set())
        if len(first_keys) != 1 or first_keys != second_keys:
            unresolved_pairs.add((tl_id, min(first_index, second_index), max(first_index, second_index)))
            return False
    for first_connection in first_connections:
        for second_connection in second_connections:
            pair = frozenset({first_connection, second_connection})
            if pair not in foe_evidence["foe_pairs"]:
                continue
            detail = dict(foe_evidence["foe_pair_details"][pair])
            detail["source_link_indices"] = sorted(
                {
                    old_link_indexes_by_connection[first_connection],
                    old_link_indexes_by_connection[second_connection],
                }
            )
            if detail not in blocked_foe_pairs:
                blocked_foe_pairs.append(detail)
            return False
    if tl_id in foe_evidence["controller_ids_with_request_evidence"]:
        bound = foe_evidence["request_bound_connection_indices"]
        if not first_connections or not second_connections or not (first_connections | second_connections) <= bound:
            unresolved_pairs.add((tl_id, min(first_index, second_index), max(first_index, second_index)))
            return False
    return True


def _control_keys_by_tl_link_index(
    connections_by_tl: Mapping[str, list[tuple[ET.Element, int, int]]],
    *,
    control_key_by_connection: Mapping[TlsPhysicalConnectionKey, str] | None,
) -> dict[str, dict[int, set[str]]]:
    result: dict[str, dict[int, set[str]]] = {}
    if control_key_by_connection is None:
        return result
    for tl_id, rows in connections_by_tl.items():
        for connection, old_index, _connection_index in rows:
            key = _tls_physical_connection_key(connection)
            control_key = str(control_key_by_connection.get(key, "")).strip()
            if control_key:
                result.setdefault(tl_id, {}).setdefault(old_index, set()).add(control_key)
    return result


def _build_tls_control_key_bindings(
    *,
    connections_by_tl: Mapping[str, list[tuple[ET.Element, int, int]]],
    signature_by_tl_index: Mapping[str, Mapping[int, tuple[str, ...]]],
    control_keys_by_tl_index: Mapping[str, Mapping[int, set[str]]],
    final_index_by_tl_old: Mapping[str, Mapping[int, int]],
    foe_evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for tl_id, rows in connections_by_tl.items():
        for _connection, old_index, connection_index in rows:
            signature = signature_by_tl_index.get(tl_id, {}).get(old_index, ())
            explicit_keys = control_keys_by_tl_index.get(tl_id, {}).get(old_index, set())
            control_keys = explicit_keys or {_phase_signature_control_key(signature)}
            for control_key in control_keys:
                record = records.setdefault(
                    (tl_id, control_key),
                    {
                        "controller_id": tl_id,
                        "control_key": control_key,
                        "phase_signature": list(signature),
                        "source_link_indices": set(),
                        "sumo_link_indices": set(),
                        "connection_indices": set(),
                    },
                )
                record["source_link_indices"].add(old_index)
                record["sumo_link_indices"].add(
                    final_index_by_tl_old.get(tl_id, {}).get(old_index, old_index)
                )
                record["connection_indices"].add(connection_index)
    bindings: list[dict[str, Any]] = []
    for record in records.values():
        connection_indices = record.pop("connection_indices")
        foe_separated = any(
            pair <= connection_indices for pair in foe_evidence["foe_pairs"]
        ) and len(record["sumo_link_indices"]) > 1
        record["source_link_indices"] = sorted(record["source_link_indices"])
        record["sumo_link_indices"] = sorted(record["sumo_link_indices"])
        record["physical_connection_count"] = len(connection_indices)
        record["foe_separated"] = foe_separated
        bindings.append(record)
    return sorted(bindings, key=lambda item: (item["controller_id"], item["control_key"]))


def _tls_physical_connection_key(connection: ET.Element) -> TlsPhysicalConnectionKey:
    return (
        connection.attrib.get("tl", ""),
        connection.attrib.get("from", ""),
        connection.attrib.get("to", ""),
        connection.attrib.get("fromLane", ""),
        connection.attrib.get("toLane", ""),
    )


def _phase_signature_control_key(signature: tuple[str, ...]) -> str:
    return "phase_signature:" + "".join(signature)


def _request_marks_foe(
    requests: Mapping[int, ET.Element],
    first: int,
    second: int,
) -> bool:
    request = requests.get(first)
    if request is None:
        return False
    bits = request.attrib.get("foes", "")
    offset = len(bits) - second - 1
    return 0 <= offset < len(bits) and bits[offset] == "1"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _demote_uncontrolled_tls_artifacts(net_file: Path) -> dict[str, Any]:
    tree = ET.parse(net_file)
    root = tree.getroot()
    junction_ids = {
        junction.attrib["id"]
        for junction in root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib.get("id", "").startswith(":")
    }
    controlled_junctions: set[str] = set()
    controlled_tls_ids: set[str] = set()
    for connection in root.findall("connection"):
        tl_id = connection.attrib.get("tl", "")
        if not tl_id or not connection.attrib.get("linkIndex"):
            continue
        controlled_tls_ids.add(tl_id)
        junction_id = _connection_junction_id(connection, junction_ids)
        if junction_id:
            controlled_junctions.add(junction_id)

    demoted = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if junction.attrib.get("type") == "traffic_light" and junction_id not in controlled_junctions:
            junction.set("type", "priority")
            demoted.append(junction_id)

    removed_tllogics = []
    for tl_logic in list(root.findall("tlLogic")):
        tl_id = tl_logic.attrib.get("id", "")
        if tl_id not in controlled_tls_ids:
            root.remove(tl_logic)
            removed_tllogics.append(tl_id)

    if demoted or removed_tllogics:
        ET.indent(root, space="    ")
        tree.write(net_file, encoding="utf-8", xml_declaration=True)
    return {
        "tls_orphan_traffic_light_junction_demoted_count": len(demoted),
        "tls_orphan_traffic_light_junction_demoted_ids": sorted(demoted),
        "tls_uncontrolled_tllogic_removed_count": len(removed_tllogics),
        "tls_uncontrolled_tllogic_removed_ids": sorted(removed_tllogics),
    }


def _demote_non_controller_traffic_light_junctions(net_file: Path) -> dict[str, Any]:
    tree = ET.parse(net_file)
    root = tree.getroot()
    controller_ids = {
        tl_logic.attrib["id"]
        for tl_logic in root.findall("tlLogic")
        if tl_logic.attrib.get("id")
    }
    controller_ids.update(
        connection.attrib["tl"]
        for connection in root.findall("connection")
        if connection.attrib.get("tl")
    )

    demoted = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if junction.attrib.get("type") == "traffic_light" and junction_id not in controller_ids:
            junction.set("type", "priority")
            demoted.append(junction_id)

    if demoted:
        ET.indent(root, space="    ")
        tree.write(net_file, encoding="utf-8", xml_declaration=True)
    return {
        "tls_non_controller_traffic_light_junction_demoted_count": len(demoted),
        "tls_non_controller_traffic_light_junction_demoted_ids": sorted(demoted),
    }


def demote_tls_ids(net_file: Path, tls_ids: Sequence[str]) -> dict[str, Any]:
    """Remove selected TLS controllers from a SUMO network in place.

    The operation removes the controller attributes from every selected
    connection, removes the selected ``tlLogic`` elements, and demotes a
    touched ``traffic_light`` junction only when no other controller still
    controls it.  Callers remain responsible for proving that the selected
    controllers are safe to retire and for validating the resulting network.
    """

    if not net_file.is_file():
        raise ValueError(f"net file does not exist: {net_file}")
    tls_id_rows: Sequence[str] = (tls_ids,) if isinstance(tls_ids, str) else tls_ids
    normalized_ids = sorted(
        {str(tls_id).strip() for tls_id in tls_id_rows if str(tls_id).strip()}
    )
    selected = set(normalized_ids)
    tree = ET.parse(net_file)
    root = tree.getroot()
    junction_ids = {
        junction.attrib["id"]
        for junction in root.findall("junction")
        if junction.attrib.get("id") and not junction.attrib.get("id", "").startswith(":")
    }
    touched_junctions: set[str] = set()
    removed_connections = 0
    for connection in root.findall("connection"):
        if connection.attrib.get("tl") not in selected:
            continue
        junction_id = _connection_junction_id(connection, junction_ids)
        if junction_id:
            touched_junctions.add(junction_id)
        for attr in ("tl", "linkIndex", "linkIndex2"):
            connection.attrib.pop(attr, None)
        removed_connections += 1

    removed_tllogics = []
    for tl_logic in list(root.findall("tlLogic")):
        tl_id = tl_logic.attrib.get("id", "")
        if tl_id in selected:
            root.remove(tl_logic)
            removed_tllogics.append(tl_id)

    controlled_junctions: set[str] = set()
    for connection in root.findall("connection"):
        if connection.attrib.get("tl") and connection.attrib.get("linkIndex"):
            junction_id = _connection_junction_id(connection, junction_ids)
            if junction_id:
                controlled_junctions.add(junction_id)
    demoted_junctions = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        if (
            junction_id in touched_junctions
            and junction_id not in controlled_junctions
            and junction.attrib.get("type") == "traffic_light"
        ):
            junction.set("type", "priority")
            demoted_junctions.append(junction_id)

    if removed_connections or removed_tllogics or demoted_junctions:
        ET.indent(root, space="    ")
        tree.write(net_file, encoding="utf-8", xml_declaration=True)
    return {
        "tls_demotion_selected_controller_count": len(normalized_ids),
        "tls_demotion_selected_controller_ids": normalized_ids,
        "tls_demotion_removed_connection_count": removed_connections,
        "tls_demotion_removed_tllogic_count": len(removed_tllogics),
        "tls_demotion_removed_tllogic_ids": sorted(removed_tllogics),
        "tls_demotion_demoted_junction_count": len(demoted_junctions),
        "tls_demotion_demoted_junction_ids": sorted(demoted_junctions),
        "tls_low_vehicle_control_removed_connection_count": removed_connections,
        "tls_low_vehicle_control_removed_tllogic_count": len(removed_tllogics),
        "tls_low_vehicle_control_removed_tllogic_ids": sorted(removed_tllogics),
        "tls_low_vehicle_control_demoted_junction_count": len(demoted_junctions),
        "tls_low_vehicle_control_demoted_junction_ids": sorted(demoted_junctions),
    }


# Compatibility alias for code that imported the former private helper while it
# was being promoted into Torii's reusable public core API.
_demote_tls_ids = demote_tls_ids


def _connection_junction_id(connection: ET.Element, junction_ids: set[str]) -> str:
    via = connection.attrib.get("via", "")
    if via.startswith(":"):
        lane_id = via[1:]
        matches = [
            junction_id
            for junction_id in junction_ids
            if lane_id == junction_id or lane_id.startswith(f"{junction_id}_")
        ]
        if matches:
            return max(matches, key=len)
    tl_id = connection.attrib.get("tl", "")
    return tl_id if tl_id in junction_ids else ""


def _empty_tls_orphan_cleanup() -> dict[str, Any]:
    return {
        "tls_orphan_traffic_light_junction_demoted_count": 0,
        "tls_orphan_traffic_light_junction_demoted_ids": [],
        "tls_uncontrolled_tllogic_removed_count": 0,
        "tls_uncontrolled_tllogic_removed_ids": [],
    }


def _empty_preservation() -> dict[str, Any]:
    return {"tls_program_preserved_count": 0, "tls_program_skipped_count": 0, "tls_program_skips": []}


def _source_tls_program_counts(net_file: Path) -> dict[str, int]:
    root = ET.parse(net_file).getroot()
    tl_logics = root.findall("tlLogic")
    phases = [phase for tl_logic in tl_logics for phase in tl_logic.findall("phase")]
    connection_counts = _tls_connection_counts(root)
    return {
        "source_tl_logic_count": len(tl_logics),
        "source_actuated_tl_logic_count": sum(1 for tl_logic in tl_logics if tl_logic.attrib.get("type") == "actuated"),
        "source_tls_phase_count": len(phases),
        "source_tls_phase_with_minmax_count": sum(
            1 for phase in phases if phase.attrib.get("minDur") or phase.attrib.get("maxDur")
        ),
        "source_tls_controlled_connection_count": connection_counts["controlled"],
        "source_tl_connection_missing_linkindex_count": connection_counts["missing_linkindex"],
    }


def _tls_connection_counts(root: ET.Element) -> dict[str, int]:
    connections = [connection for connection in root.findall("connection") if connection.attrib.get("tl")]
    return {
        "controlled": sum(1 for connection in connections if connection.attrib.get("linkIndex")),
        "missing_linkindex": sum(1 for connection in connections if not connection.attrib.get("linkIndex")),
    }


def _tls_connection_preservation(source_counts: Mapping[str, int], variant_counts: Mapping[str, int]) -> dict[str, int | str]:
    source_controlled = int(source_counts.get("source_tls_controlled_connection_count", 0) or 0)
    variant_controlled = int(variant_counts.get("tls_aggregated_controlled_connection_count", 0) or 0)
    regression = max(0, source_controlled - variant_controlled)
    return {
        "tls_controlled_connection_preservation_status": "fail" if regression else "pass",
        "tls_controlled_connection_regression_count": regression,
    }


def _short_output_path(output_dir: Path, prefix: str, suffix: str) -> Path:
    candidate = output_dir / f"{prefix}{suffix}"
    if len(str(candidate.resolve())) < 239:
        return candidate
    digest = hashlib.sha1(prefix.encode("utf-8")).hexdigest()[:10]
    return output_dir / f"p_{digest}{suffix}"


def _int_field(report: Mapping[str, Any], key: str) -> int:
    try:
        return int(report.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return dict(result.to_dict())
    if hasattr(result, "model_dump"):
        return dict(result.model_dump(mode="json"))
    return dict(result)


def _failure(error: str) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "tls_aggregation_status": "failed",
        "error": error,
        "warnings": [error],
    }
