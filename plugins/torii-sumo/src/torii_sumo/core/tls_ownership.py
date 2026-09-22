from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def audit_tls_ownership_rebuild(
    *,
    source_net: Path,
    candidate_net: Path,
    target_source_junction_ids: tuple[str, ...],
    target_candidate_junction_id: str,
    expected_controller_ids: tuple[str, ...],
    expected_controlled_connection_count: int,
    report_schema: str = "torii.tls-ownership/v1",
) -> dict[str, Any]:
    """Verify that one declared TLS cell owns the candidate movements.

    Physical traffic-light junctions/controllers are counted independently from
    signal groups. The source network is used only to prove that target identities
    which should have been replaced do not survive in the candidate.
    """

    source = tls_scope_inventory(
        source_net,
        scope_junction_ids=target_source_junction_ids,
    )
    candidate = tls_scope_inventory(
        candidate_net,
        scope_junction_ids=(target_candidate_junction_id,),
    )
    expected = set(expected_controller_ids)
    source_scope_ids = set(target_source_junction_ids)
    candidate_global_controller_ids = set(candidate["all_controller_ids"])
    candidate_global_junction_ids = set(candidate["all_junction_ids"])
    old_source_controller_ids = set(source["scope_controller_ids"]) - expected
    residual_old_controller_ids = sorted(old_source_controller_ids & candidate_global_controller_ids)
    residual_source_junction_ids = sorted(
        (source_scope_ids - {target_candidate_junction_id}) & candidate_global_junction_ids
    )

    findings: list[dict[str, Any]] = []
    if candidate["scope_tls_junction_ids"] != [target_candidate_junction_id]:
        findings.append(
            {
                "category": "candidate_physical_tls_junction_not_unique",
                "expected": [target_candidate_junction_id],
                "observed": candidate["scope_tls_junction_ids"],
            }
        )
    if set(candidate["scope_controller_ids"]) != expected:
        findings.append(
            {
                "category": "candidate_target_controller_set_mismatch",
                "expected": sorted(expected),
                "observed": candidate["scope_controller_ids"],
            }
        )
    if set(candidate["scope_program_controller_ids"]) != expected:
        findings.append(
            {
                "category": "candidate_target_program_set_mismatch",
                "expected": sorted(expected),
                "observed": candidate["scope_program_controller_ids"],
            }
        )
    if candidate["scope_controlled_connection_count"] != expected_controlled_connection_count:
        findings.append(
            {
                "category": "candidate_target_controlled_connection_count_mismatch",
                "expected": expected_controlled_connection_count,
                "observed": candidate["scope_controlled_connection_count"],
            }
        )
    if residual_old_controller_ids:
        findings.append(
            {
                "category": "old_source_controller_identity_survived",
                "controller_ids": residual_old_controller_ids,
            }
        )
    if residual_source_junction_ids:
        findings.append(
            {
                "category": "old_source_tls_cell_junction_survived",
                "junction_ids": residual_source_junction_ids,
            }
        )

    return {
        "schema": report_schema,
        "status": "pass" if not findings else "fail",
        "source_net_file": str(source_net),
        "candidate_net_file": str(candidate_net),
        "target_source_junction_ids": list(target_source_junction_ids),
        "target_candidate_junction_id": target_candidate_junction_id,
        "expected_controller_ids": sorted(expected),
        "expected_controlled_connection_count": (expected_controlled_connection_count),
        "source": {
            "target_scope_junction_count": len(target_source_junction_ids),
            "target_tls_junction_count": len(source["scope_tls_junction_ids"]),
            "target_tls_junction_ids": source["scope_tls_junction_ids"],
            "target_controller_count": len(source["scope_controller_ids"]),
            "target_controller_ids": source["scope_controller_ids"],
            "target_controlled_connection_count": source["scope_controlled_connection_count"],
            "target_signal_group_count": source["scope_signal_group_count"],
        },
        "candidate": {
            "target_tls_junction_count": len(candidate["scope_tls_junction_ids"]),
            "target_tls_junction_ids": candidate["scope_tls_junction_ids"],
            "target_controller_count": len(candidate["scope_controller_ids"]),
            "target_controller_ids": candidate["scope_controller_ids"],
            "target_program_controller_ids": candidate["scope_program_controller_ids"],
            "target_controlled_connection_count": candidate["scope_controlled_connection_count"],
            "target_signal_group_count": candidate["scope_signal_group_count"],
        },
        "removed_source_tls_junction_ids": sorted(
            set(source["scope_tls_junction_ids"]) - candidate_global_junction_ids
        ),
        "removed_source_controller_ids": sorted(old_source_controller_ids - candidate_global_controller_ids),
        "residual_source_junction_ids": residual_source_junction_ids,
        "residual_old_controller_ids": residual_old_controller_ids,
        "findings": findings,
        "interpretation": (
            "traffic-light junction/controller counts describe physical TLS "
            "ownership; signal-group/linkIndex counts describe controlled "
            "lane movements and must not be interpreted as separate physical "
            "intersections"
        ),
        "review_instruction": (
            f"Open {candidate_net.name} for the cleaned network. "
            f"{source_net.name} is the immutable fragmented OSM baseline."
        ),
    }


def audit_topology_variant_tls_ownership(
    *,
    source_net: Path,
    candidate_net: Path,
    target_source_junction_ids: tuple[str, ...],
    target_candidate_junction_ids: tuple[str, ...],
    expected_tls_junction_ids: tuple[str, ...],
    expected_controller_ids: tuple[str, ...],
    retained_source_junction_ids: tuple[str, ...],
    removed_source_junction_ids: tuple[str, ...],
    expected_controlled_connection_count: int | None,
    report_schema: str = "torii.topology-variant-tls-ownership/v1",
) -> dict[str, Any]:
    """Audit TLS ownership for split, merge, and partial-repair variants."""

    source = tls_scope_inventory(
        source_net,
        scope_junction_ids=target_source_junction_ids,
    )
    candidate = tls_scope_inventory(
        candidate_net,
        scope_junction_ids=target_candidate_junction_ids,
    )
    expected_tls = sorted(set(expected_tls_junction_ids))
    expected_controllers = sorted(set(expected_controller_ids))
    retained = set(retained_source_junction_ids)
    removed = set(removed_source_junction_ids)
    all_candidate_junctions = set(candidate["all_junction_ids"])
    all_candidate_controllers = set(candidate["all_controller_ids"])
    old_source_controllers = set(source["scope_controller_ids"]) - set(
        expected_controllers
    )
    residual_old_controllers = sorted(
        old_source_controllers & all_candidate_controllers
    )
    missing_retained_junctions = sorted(retained - all_candidate_junctions)
    residual_removed_junctions = sorted(removed & all_candidate_junctions)
    findings: list[dict[str, Any]] = []

    if candidate["scope_tls_junction_ids"] != expected_tls:
        findings.append(
            {
                "category": "candidate_tls_junction_set_mismatch",
                "expected": expected_tls,
                "observed": candidate["scope_tls_junction_ids"],
            }
        )
    if candidate["scope_controller_ids"] != expected_controllers:
        findings.append(
            {
                "category": "candidate_controller_set_mismatch",
                "expected": expected_controllers,
                "observed": candidate["scope_controller_ids"],
            }
        )
    if candidate["scope_program_controller_ids"] != expected_controllers:
        findings.append(
            {
                "category": "candidate_program_set_mismatch",
                "expected": expected_controllers,
                "observed": candidate["scope_program_controller_ids"],
            }
        )
    if (
        expected_controlled_connection_count is not None
        and candidate["scope_controlled_connection_count"]
        != expected_controlled_connection_count
    ):
        findings.append(
            {
                "category": "candidate_controlled_connection_count_mismatch",
                "expected": expected_controlled_connection_count,
                "observed": candidate["scope_controlled_connection_count"],
            }
        )
    if candidate["scope_controlled_connection_count"] <= 0:
        findings.append({"category": "candidate_has_no_controlled_connections"})
    if residual_old_controllers:
        findings.append(
            {
                "category": "old_source_controller_identity_survived",
                "controller_ids": residual_old_controllers,
            }
        )
    if missing_retained_junctions:
        findings.append(
            {
                "category": "declared_retained_junction_missing",
                "junction_ids": missing_retained_junctions,
            }
        )
    if residual_removed_junctions:
        findings.append(
            {
                "category": "declared_removed_junction_survived",
                "junction_ids": residual_removed_junctions,
            }
        )

    return {
        "schema": report_schema,
        "status": "pass" if not findings else "fail",
        "source_net_file": str(source_net),
        "candidate_net_file": str(candidate_net),
        "target_source_junction_ids": list(target_source_junction_ids),
        "target_candidate_junction_ids": list(target_candidate_junction_ids),
        "expected_tls_junction_ids": expected_tls,
        "expected_controller_ids": expected_controllers,
        "expected_controlled_connection_count": expected_controlled_connection_count,
        "retained_source_junction_ids": sorted(retained),
        "removed_source_junction_ids": sorted(removed),
        "source": {
            "target_tls_junction_ids": source["scope_tls_junction_ids"],
            "target_controller_ids": source["scope_controller_ids"],
            "target_controlled_connection_count": source[
                "scope_controlled_connection_count"
            ],
            "target_signal_group_count": source["scope_signal_group_count"],
        },
        "candidate": {
            "target_tls_junction_ids": candidate["scope_tls_junction_ids"],
            "target_controller_ids": candidate["scope_controller_ids"],
            "target_program_controller_ids": candidate[
                "scope_program_controller_ids"
            ],
            "target_controlled_connection_count": candidate[
                "scope_controlled_connection_count"
            ],
            "target_signal_group_count": candidate["scope_signal_group_count"],
        },
        "residual_old_controller_ids": residual_old_controllers,
        "missing_retained_junction_ids": missing_retained_junctions,
        "residual_removed_junction_ids": residual_removed_junctions,
        "findings": findings,
        "interpretation": (
            "Physical TLS junctions, controller identities, controlled local "
            "links, and boundary-to-boundary movements are distinct counts."
        ),
    }


def tls_scope_inventory(
    net_file: Path,
    *,
    scope_junction_ids: tuple[str, ...],
) -> dict[str, Any]:
    """Inventory physical TLS owners and controlled movements for one scope."""

    root = ET.parse(net_file).getroot()
    scope = set(scope_junction_ids)
    junctions = {
        element.attrib.get("id", ""): element for element in root.findall("junction") if element.attrib.get("id")
    }
    external_edge_targets = {
        element.attrib.get("id", ""): element.attrib.get("to", "")
        for element in root.findall("edge")
        if element.attrib.get("id") and element.attrib.get("function") != "internal"
    }
    all_program_controller_ids = {
        element.attrib.get("id", "") for element in root.findall("tlLogic") if element.attrib.get("id")
    }
    all_controlled_controller_ids: set[str] = set()
    scoped_connections: list[ET.Element] = []
    for connection in root.findall("connection"):
        controller_id = connection.attrib.get("tl", "")
        if not controller_id:
            continue
        all_controlled_controller_ids.add(controller_id)
        owner_junction_id = external_edge_targets.get(connection.attrib.get("from", ""), "")
        if owner_junction_id in scope:
            scoped_connections.append(connection)

    scope_controller_ids = sorted(
        {connection.attrib.get("tl", "") for connection in scoped_connections if connection.attrib.get("tl")}
    )
    scope_program_controller_ids = sorted(set(scope_controller_ids) & all_program_controller_ids)
    signal_groups = {
        (
            connection.attrib.get("tl", ""),
            connection.attrib.get("linkIndex", ""),
        )
        for connection in scoped_connections
        if connection.attrib.get("linkIndex") not in (None, "")
    }
    return {
        "all_junction_ids": sorted(junctions),
        "all_controller_ids": sorted(all_program_controller_ids | all_controlled_controller_ids),
        "scope_tls_junction_ids": sorted(
            junction_id
            for junction_id, junction in junctions.items()
            if junction_id in scope and junction.attrib.get("type", "").startswith("traffic_light")
        ),
        "scope_controller_ids": scope_controller_ids,
        "scope_program_controller_ids": scope_program_controller_ids,
        "scope_controlled_connection_count": len(scoped_connections),
        "scope_signal_group_count": len(signal_groups),
    }
