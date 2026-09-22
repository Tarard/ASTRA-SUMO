from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256


_TURN_RANK = {"r": 0, "s": 1, "l": 2}
_MOTORIZED_MODES = frozenset(
    {
        "passenger",
        "private",
        "taxi",
        "hov",
        "delivery",
        "truck",
        "trailer",
        "bus",
        "coach",
        "emergency",
        "authority",
        "army",
        "vip",
        "motorcycle",
        "moped",
    }
)
_FOUR_WAY_COMPATIBLE_PHASE_PAIRS = frozenset(
    {
        frozenset({1, 5}),
        frozenset({1, 6}),
        frozenset({2, 5}),
        frozenset({2, 6}),
        frozenset({3, 7}),
        frozenset({3, 8}),
        frozenset({4, 7}),
        frozenset({4, 8}),
    }
)


def _engineering_plan_lane_arrows_justify_destination_merge(
    evidence: Mapping[str, Any] | None,
    *,
    actual_source_lanes: Sequence[str],
) -> bool:
    """Accept only an exact, hash-bound official lane-arrow merge claim.

    An engineering drawing can prove that two marked source lanes legally feed
    one destination lane.  It does not prove a target-lane fanout or an
    arbitrary connection curve, so this evidence basis is deliberately used
    only by the destination-merge audit.
    """

    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("basis") != "official_engineering_plan_lane_arrows":
        return False
    source_cell = str(evidence.get("source_cell", "")).strip()
    if not source_cell:
        return False
    declared = evidence.get("source_lanes")
    if not isinstance(declared, Sequence) or isinstance(declared, (str, bytes)):
        return False
    normalized = [str(value) for value in declared]
    if not normalized or len(set(normalized)) != len(normalized):
        return False
    if sorted(normalized) != sorted(str(value) for value in actual_source_lanes):
        return False
    official_url = str(evidence.get("official_source_url", "")).strip()
    if not official_url.startswith("https://") or len(official_url) <= len("https://"):
        return False
    source_sha256 = str(evidence.get("official_source_sha256", "")).strip().lower()
    return len(source_sha256) == 64 and all(character in "0123456789abcdef" for character in source_sha256)
_THREE_WAY_COMPATIBLE_PHASE_PAIRS = frozenset(
    {
        frozenset({1, 5}),
        frozenset({1, 6}),
        frozenset({2, 5}),
        frozenset({2, 6}),
    }
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_TRAFFIC_SIDE_ALIASES = {
    "auto": "auto",
    "right": "right",
    "right_hand": "right",
    "right-hand": "right",
    "left": "left",
    "left_hand": "left",
    "left-hand": "left",
}


def build_official_teacher_connection_mode_evidence(
    teacher_net_file: Path,
    *,
    teacher_junction_id: str,
    edge_map: Mapping[str, str],
    source_cell: str,
) -> dict[str, Any]:
    """Project exact official teacher movements into stable candidate keys.

    This is deliberately narrower than a generic reference-network comparison.
    A movement is evidence only when both mapped teacher edges meet at the
    declared physical junction, both lanes carry motorized traffic, and the
    teacher connection retains an explicit official curve.  Candidate
    connection indices are intentionally excluded because ``netconvert`` may
    reorder them.
    """

    teacher = teacher_net_file.resolve()
    normalized_source_cell = str(source_cell).strip()
    normalized_junction_id = str(teacher_junction_id).strip()
    normalized_edge_map = {
        str(teacher_edge): str(candidate_edge)
        for teacher_edge, candidate_edge in edge_map.items()
        if str(teacher_edge) and str(candidate_edge)
    }
    failures: list[str] = []
    if not teacher.is_file():
        failures.append("teacher_net_file_missing")
    if not normalized_source_cell:
        failures.append("source_cell_missing")
    if not normalized_junction_id:
        failures.append("teacher_junction_id_missing")
    if not normalized_edge_map:
        failures.append("edge_map_empty")
    if len(set(normalized_edge_map.values())) != len(normalized_edge_map):
        failures.append("edge_map_not_bijective")
    if failures:
        return {
            "schema": "torii.official_teacher_connection_mode_evidence.v1",
            "status": "fail",
            "failures": failures,
            "teacher_net_file": str(teacher),
            "teacher_junction_id": normalized_junction_id,
            "source_cell": normalized_source_cell,
            "edge_map": dict(sorted(normalized_edge_map.items())),
            "target_fanouts": {},
            "destination_merges": {},
            "lane_bindings": {},
        }

    root = ET.parse(teacher).getroot()
    teacher_junction = root.find(f"junction[@id='{normalized_junction_id}']")
    if teacher_junction is None:
        failures.append("teacher_junction_missing")
    edges = {
        edge.attrib.get("id", ""): edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    teacher_sha256 = file_sha256(teacher)
    lane_bindings: dict[str, dict[str, Any]] = {}
    fanout_lanes: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    fanout_signatures: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    merge_lanes: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    merge_signatures: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    ignored_connections: list[dict[str, str]] = []

    for connection in root.findall("connection"):
        teacher_from = connection.attrib.get("from", "")
        teacher_to = connection.attrib.get("to", "")
        if teacher_from not in normalized_edge_map or teacher_to not in normalized_edge_map:
            continue
        source_edge = edges.get(teacher_from)
        target_edge = edges.get(teacher_to)
        from_lane_index = _as_int(connection.attrib.get("fromLane"))
        to_lane_index = _as_int(connection.attrib.get("toLane"))
        source_lane = (
            source_edge.find(f"lane[@index='{from_lane_index}']")
            if source_edge is not None and from_lane_index is not None
            else None
        )
        target_lane = (
            target_edge.find(f"lane[@index='{to_lane_index}']")
            if target_edge is not None and to_lane_index is not None
            else None
        )
        rejection_reason = ""
        if source_edge is None or target_edge is None:
            rejection_reason = "mapped_teacher_edge_missing"
        elif (
            source_edge.attrib.get("to") != normalized_junction_id
            or target_edge.attrib.get("from") != normalized_junction_id
        ):
            rejection_reason = "connection_outside_declared_teacher_junction"
        elif from_lane_index is None or to_lane_index is None:
            rejection_reason = "lane_index_invalid"
        elif not lane_supports_motorized(source_lane) or not lane_supports_motorized(target_lane):
            rejection_reason = "connection_not_motorized"
        elif not connection.attrib.get("shape", "").strip():
            rejection_reason = "official_connection_curve_missing"
        if rejection_reason:
            ignored_connections.append(
                {
                    "from": teacher_from,
                    "fromLane": connection.attrib.get("fromLane", ""),
                    "to": teacher_to,
                    "toLane": connection.attrib.get("toLane", ""),
                    "reason": rejection_reason,
                }
            )
            continue

        candidate_from = normalized_edge_map[teacher_from]
        candidate_to = normalized_edge_map[teacher_to]
        from_lane = str(from_lane_index)
        to_lane = str(to_lane_index)
        binding_key = "|".join((candidate_from, from_lane, candidate_to, to_lane))
        teacher_signature = "|".join((teacher_from, from_lane, teacher_to, to_lane))
        lane_bindings[binding_key] = {
            "basis": "official_map_connection_curve",
            "source_cell": normalized_source_cell,
            "teacher_net_sha256": teacher_sha256,
            "teacher_junction_id": normalized_junction_id,
            "official_connection_signature": teacher_signature,
            "official_connection_shape": connection.attrib.get("shape", ""),
        }
        fanout_key = (candidate_from, from_lane, candidate_to)
        fanout_lanes[fanout_key].add(to_lane)
        fanout_signatures[fanout_key].append(teacher_signature)
        merge_key = (candidate_from, candidate_to, to_lane)
        merge_lanes[merge_key].add(from_lane)
        merge_signatures[merge_key].append(teacher_signature)

    target_fanouts = {
        "|".join(key): {
            "basis": "official_map_connection_curve",
            "source_cell": normalized_source_cell,
            "teacher_net_sha256": teacher_sha256,
            "teacher_junction_id": normalized_junction_id,
            "target_lanes": sorted(target_lanes),
            "official_connection_signatures": sorted(fanout_signatures[key]),
        }
        for key, target_lanes in sorted(fanout_lanes.items())
        if len(target_lanes) > 1
    }
    destination_merges = {
        "|".join(key): {
            "basis": "official_map_connection_curve",
            "source_cell": normalized_source_cell,
            "teacher_net_sha256": teacher_sha256,
            "teacher_junction_id": normalized_junction_id,
            "source_lanes": sorted(source_lanes),
            "official_connection_signatures": sorted(merge_signatures[key]),
        }
        for key, source_lanes in sorted(merge_lanes.items())
        if len(source_lanes) > 1
    }
    if not lane_bindings:
        failures.append("no_exact_motorized_teacher_connections")
    return {
        "schema": "torii.official_teacher_connection_mode_evidence.v1",
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "teacher_net_file": str(teacher),
        "teacher_net_sha256": teacher_sha256,
        "teacher_junction_id": normalized_junction_id,
        "source_cell": normalized_source_cell,
        "edge_map": dict(sorted(normalized_edge_map.items())),
        "exact_connection_count": len(lane_bindings),
        "target_fanout_count": len(target_fanouts),
        "destination_merge_count": len(destination_merges),
        "ignored_connection_count": len(ignored_connections),
        "ignored_connections": ignored_connections,
        "target_fanouts": target_fanouts,
        "destination_merges": destination_merges,
        "lane_bindings": dict(sorted(lane_bindings.items())),
    }


def resolve_network_traffic_side(
    root: ET.Element,
    requested: str = "auto",
) -> dict[str, Any]:
    """Resolve traffic side from explicit evidence and SUMO's net contract.

    SUMO lane index zero is always the physical right-most lane.  A missing
    ``lefthand`` attribute means right-hand traffic by SUMO's documented
    default; an explicit request that contradicts the network is a hard
    configuration failure rather than an alternate interpretation.
    """

    normalized = _TRAFFIC_SIDE_ALIASES.get(str(requested).strip().casefold())
    if normalized is None:
        raise ValueError("traffic_side must be auto, right, or left")
    raw = root.attrib.get("lefthand")
    failures: list[str] = []
    if raw is None:
        declared = "right"
        evidence = "sumo_default"
    elif raw.strip().casefold() in _TRUE_VALUES:
        declared = "left"
        evidence = "net_attribute"
    elif raw.strip().casefold() in _FALSE_VALUES:
        declared = "right"
        evidence = "net_attribute"
    else:
        declared = normalized if normalized in {"right", "left"} else "right"
        evidence = "invalid_net_attribute"
        failures.append(f"invalid_net_lefthand_attribute:{raw}")
    if normalized in {"right", "left"} and normalized != declared:
        failures.append(f"traffic_side_mismatch:requested_{normalized}:network_{declared}")
    return {
        "status": "fail" if failures else "pass",
        "requested": normalized,
        "effective": declared,
        "evidence": evidence,
        "net_lefthand_attribute": raw,
        "failures": failures,
    }


def build_connection_mode_catalog(root: ET.Element) -> dict[str, Any]:
    """Build the whole-network indexes once for any number of junction audits."""

    edges = {
        edge.attrib["id"]: edge
        for edge in root.findall("edge")
        if edge.attrib.get("id")
    }
    connections = root.findall("connection")
    lane_catalog, lanes_by_edge = _build_lane_catalog(edges)
    internal_lane_count = sum(
        len(lanes_by_edge.get(edge_id, {}))
        for edge_id, edge in edges.items()
        if edge_id.startswith(":")
        or edge.attrib.get("function") == "internal"
    )
    edges_from_junction: dict[str, list[str]] = defaultdict(list)
    for edge_id, edge in edges.items():
        from_junction = edge.attrib.get("from", "")
        if from_junction:
            edges_from_junction[from_junction].append(edge_id)
    outgoing_by_lane: dict[tuple[str, int], list[tuple[int, ET.Element]]] = defaultdict(list)
    for connection_index, connection in enumerate(connections):
        from_lane_index = _as_int(connection.attrib.get("fromLane"))
        if from_lane_index is not None:
            outgoing_by_lane[(connection.attrib.get("from", ""), from_lane_index)].append(
                (connection_index, connection)
            )
    return {
        "edges": edges,
        "connections": connections,
        "internal_link_mode": _infer_internal_link_mode(edges, connections),
        "junctions": {
            junction.attrib["id"]: junction
            for junction in root.findall("junction")
            if junction.attrib.get("id")
        },
        "lane_catalog": lane_catalog,
        "lanes_by_edge": lanes_by_edge,
        "internal_lane_count": internal_lane_count,
        "edges_from_junction": {
            junction_id: tuple(sorted(edge_ids))
            for junction_id, edge_ids in edges_from_junction.items()
        },
        "outgoing_by_lane": outgoing_by_lane,
        "tl_logics_by_id": {
            tl_id: tuple(logics)
            for tl_id, logics in _group_tl_logics(root).items()
        },
    }


def build_network_connection_mode_audit(
    net_file: Path,
    *,
    output_dir: Path,
    prefix: str = "connection_mode_audit",
    junction_ids: Sequence[str] | None = None,
    traffic_side: str = "auto",
    endpoint_tolerance_m: float = 2.0,
    normalized_lane_rank_tolerance: float = 0.5,
) -> dict[str, Any]:
    """Audit NetEdit Connection Mode semantics without launching a GUI.

    The source network is immutable.  The function writes a machine-readable
    report, a display-only ``additional.xml`` review layer, and an artifact
    manifest that binds both outputs to the source network hash.
    """

    source = net_file.resolve()
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report_file = destination / f"{prefix}.json"
    overlay_file = destination / f"{prefix}.review.add.xml"
    manifest_file = destination / f"{prefix}.manifest.json"

    root = ET.parse(source).getroot()
    report = audit_network_connection_mode(
        root,
        junction_ids=junction_ids,
        traffic_side=traffic_side,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
    )
    report.update(
        {
            "source_net_file": str(source),
            "source_sha256": file_sha256(source),
            "source_network_mutation": False,
            "report_file": str(report_file),
            "review_overlay_file": str(overlay_file),
            "manifest_file": str(manifest_file),
        }
    )
    _write_network_connection_review_overlay(
        overlay_file,
        report.get("junctions", []),
        source_sha256=report["source_sha256"],
    )
    write_json_atomic(report_file, report, sort_keys=True)
    manifest = {
        "schema": "torii.connection_mode_artifact_manifest.v1",
        "status": "pass",
        "source_net_file": str(source),
        "source_sha256": report["source_sha256"],
        "source_network_mutation": False,
        "review_overlay_display_only": True,
        "artifacts": [
            {
                "kind": "connection_mode_report",
                "path": str(report_file),
                "sha256": file_sha256(report_file),
            },
            {
                "kind": "display_only_review_overlay",
                "path": str(overlay_file),
                "sha256": file_sha256(overlay_file),
            },
        ],
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return report


def build_connection_mode_regression_audit(
    source_net_file: Path,
    candidate_net_file: Path,
    *,
    output_dir: Path,
    prefix: str = "connection_mode_regression",
    target_source_junction_ids: Sequence[str] = (),
    target_candidate_junction_ids: Sequence[str] = (),
    traffic_side: str = "auto",
    endpoint_tolerance_m: float = 2.0,
    normalized_lane_rank_tolerance: float = 0.5,
    candidate_evidence_justified_target_fanouts: Mapping[str, Mapping[str, Any]] | None = None,
    candidate_evidence_justified_destination_merges: Mapping[str, Mapping[str, Any]] | None = None,
    candidate_evidence_justified_lane_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fail closed when a candidate worsens Connection Mode outside its scope."""

    source = source_net_file.resolve()
    candidate = candidate_net_file.resolve()
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    report_file = destination / f"{prefix}.json"
    overlay_file = destination / f"{prefix}.review.add.xml"
    manifest_file = destination / f"{prefix}.manifest.json"

    source_root = ET.parse(source).getroot()
    candidate_root = ET.parse(candidate).getroot()
    source_audit = audit_network_connection_mode(
        source_root,
        traffic_side=traffic_side,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
    )
    candidate_audit = audit_network_connection_mode(
        candidate_root,
        traffic_side=traffic_side,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
        evidence_justified_target_fanouts=candidate_evidence_justified_target_fanouts,
        evidence_justified_destination_merges=candidate_evidence_justified_destination_merges,
        evidence_justified_lane_bindings=candidate_evidence_justified_lane_bindings,
    )
    requested_source_scope = sorted(
        {str(value) for value in target_source_junction_ids if str(value)}
    )
    requested_candidate_scope = sorted(
        {str(value) for value in target_candidate_junction_ids if str(value)}
    )
    expanded_source_scope, expanded_candidate_scope = (
        _expand_coupled_scopes_by_tls_controller(
            source_audit,
            candidate_audit,
            requested_source_scope,
            requested_candidate_scope,
        )
    )
    report = compare_connection_mode_audits(
        source_audit,
        candidate_audit,
        target_source_junction_ids=expanded_source_scope,
        target_candidate_junction_ids=expanded_candidate_scope,
    )
    source_sha256 = file_sha256(source)
    candidate_sha256 = file_sha256(candidate)
    report.update(
        {
            "source_net_file": str(source),
            "candidate_net_file": str(candidate),
            "source_sha256": source_sha256,
            "candidate_sha256": candidate_sha256,
            "requested_target_source_junction_ids": requested_source_scope,
            "requested_target_candidate_junction_ids": requested_candidate_scope,
            "tls_controller_scope_expansion_applied": (
                requested_source_scope != expanded_source_scope
                or requested_candidate_scope != expanded_candidate_scope
            ),
            "source_network_mutation": False,
            "report_file": str(report_file),
            "review_overlay_file": str(overlay_file),
            "manifest_file": str(manifest_file),
        }
    )
    affected_ids = {
        *report.get("outside_scope_regression_junction_ids", []),
        *report.get("target_scope_flagged_junction_ids", []),
    }
    affected_records = [
        record
        for record in candidate_audit.get("junctions", [])
        if str(record.get("junction_id", "")) in affected_ids
    ]
    _write_network_connection_review_overlay(
        overlay_file,
        affected_records,
        source_sha256=candidate_sha256,
    )
    write_json_atomic(report_file, report, sort_keys=True)
    manifest = {
        "schema": "torii.connection_mode_regression_manifest.v1",
        "status": report["status"],
        "artifact_generation_status": "pass",
        "gate_status": report["status"],
        "automatic_promotion_gate": report["automatic_promotion_gate"],
        "source_net_file": str(source),
        "candidate_net_file": str(candidate),
        "source_sha256": source_sha256,
        "candidate_sha256": candidate_sha256,
        "source_network_mutation": False,
        "review_overlay_display_only": True,
        "artifacts": [
            {
                "kind": "connection_mode_regression_report",
                "path": str(report_file),
                "sha256": file_sha256(report_file),
            },
            {
                "kind": "display_only_regression_overlay",
                "path": str(overlay_file),
                "sha256": file_sha256(overlay_file),
            },
        ],
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return report


def compare_connection_mode_audits(
    source_audit: Mapping[str, Any],
    candidate_audit: Mapping[str, Any],
    *,
    target_source_junction_ids: Sequence[str] = (),
    target_candidate_junction_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Compare exact per-junction findings and retain category counts as diagnostics.

    This legacy comparator now fails closed when one witness is resolved and a
    different witness of the same category appears.  The corridor stage-1
    comparator remains the promotion-grade path because it first maps raw
    connection indices to stable movement identities.
    """

    source_records = {
        str(record.get("junction_id", "")): record
        for record in source_audit.get("junctions", [])
        if record.get("junction_id")
    }
    candidate_records = {
        str(record.get("junction_id", "")): record
        for record in candidate_audit.get("junctions", [])
        if record.get("junction_id")
    }
    source_scope = {str(value) for value in target_source_junction_ids if str(value)}
    candidate_scope = {
        str(value) for value in target_candidate_junction_ids if str(value)
    }
    source_outside_ids = set(source_records) - source_scope
    candidate_outside_ids = set(candidate_records) - candidate_scope
    missing_outside_ids = sorted(source_outside_ids - candidate_outside_ids)
    added_outside_ids = sorted(candidate_outside_ids - source_outside_ids)

    review_regressions: list[dict[str, Any]] = []
    structural_regressions: list[dict[str, Any]] = []
    resolved_review_count = 0
    resolved_structural_count = 0
    compared_ids = sorted(source_outside_ids & candidate_outside_ids)
    for junction_id in compared_ids:
        source_review = Counter(
            _junction_findings(source_records[junction_id], finding_kind="review")
        )
        source_structural = Counter(
            _junction_findings(source_records[junction_id], finding_kind="structural")
        )
        candidate_review = Counter(
            _junction_findings(candidate_records[junction_id], finding_kind="review")
        )
        candidate_structural = Counter(
            _junction_findings(candidate_records[junction_id], finding_kind="structural")
        )
        review_regressions.extend(
            _positive_finding_deltas(
                junction_id,
                source_review,
                candidate_review,
            )
        )
        structural_regressions.extend(
            _positive_finding_deltas(
                junction_id,
                source_structural,
                candidate_structural,
            )
        )
        resolved_review_count += sum(
            max(0, source_review[finding] - candidate_review[finding])
            for finding in source_review.keys() | candidate_review.keys()
        )
        resolved_structural_count += sum(
            max(0, source_structural[finding] - candidate_structural[finding])
            for finding in source_structural.keys() | candidate_structural.keys()
        )

    for junction_id in added_outside_ids:
        candidate_review = Counter(
            _junction_findings(candidate_records[junction_id], finding_kind="review")
        )
        candidate_structural = Counter(
            _junction_findings(candidate_records[junction_id], finding_kind="structural")
        )
        review_regressions.extend(
            _positive_finding_deltas(
                junction_id,
                Counter(),
                candidate_review,
            )
        )
        structural_regressions.extend(
            _positive_finding_deltas(
                junction_id,
                Counter(),
                candidate_structural,
            )
        )

    new_review_count = sum(int(item["delta"]) for item in review_regressions)
    new_structural_count = sum(
        int(item["delta"]) for item in structural_regressions
    )
    affected_ids = sorted(
        {
            *(item["junction_id"] for item in review_regressions),
            *(item["junction_id"] for item in structural_regressions),
            *missing_outside_ids,
            *added_outside_ids,
        }
    )
    source_target_records = [
        source_records[junction_id]
        for junction_id in sorted(source_scope & set(source_records))
    ]
    candidate_target_records = [
        candidate_records[junction_id]
        for junction_id in sorted(candidate_scope & set(candidate_records))
    ]
    source_target_review, source_target_structural = _aggregate_finding_categories(
        source_target_records
    )
    candidate_target_review, candidate_target_structural = _aggregate_finding_categories(
        candidate_target_records
    )
    source_target_review_findings = _aggregate_finding_witnesses(
        source_target_records,
        finding_kind="review",
    )
    candidate_target_review_findings = _aggregate_finding_witnesses(
        candidate_target_records,
        finding_kind="review",
    )
    source_target_structural_findings = _aggregate_finding_witnesses(
        source_target_records,
        finding_kind="structural",
    )
    candidate_target_structural_findings = _aggregate_finding_witnesses(
        candidate_target_records,
        finding_kind="structural",
    )
    target_review_regressions = _scope_finding_deltas(
        source_target_review_findings,
        candidate_target_review_findings,
    )
    target_structural_regressions = _scope_finding_deltas(
        source_target_structural_findings,
        candidate_target_structural_findings,
    )
    target_new_review_count = sum(
        int(item["delta"]) for item in target_review_regressions
    )
    target_new_structural_count = sum(
        int(item["delta"]) for item in target_structural_regressions
    )
    target_review_count = sum(candidate_target_review.values())
    target_structural_count = sum(candidate_target_structural.values())
    target_flagged_ids = sorted(
        str(record.get("junction_id", ""))
        for record in candidate_target_records
        if str(record.get("junction_id", ""))
        and any(sum(categories.values()) for categories in _junction_finding_categories(record))
    )

    blockers = []
    source_traffic_side = str(source_audit.get("traffic_side", ""))
    candidate_traffic_side = str(candidate_audit.get("traffic_side", ""))
    if source_traffic_side != candidate_traffic_side:
        blockers.append("source_candidate_traffic_side_mismatch")
    if new_structural_count:
        blockers.append("new_outside_scope_structural_findings")
    if new_review_count:
        blockers.append("new_outside_scope_review_findings")
    if missing_outside_ids:
        blockers.append("outside_scope_junctions_missing_from_candidate")
    if added_outside_ids:
        blockers.append("outside_scope_junctions_added_to_candidate")
    if target_new_structural_count:
        blockers.append("new_target_scope_structural_findings")
    if target_new_review_count:
        blockers.append("new_target_scope_review_findings")
    status = "fail" if blockers else "pass"
    return {
        "schema": "torii.connection_mode_regression_audit.v1",
        "status": status,
        "claim_status": "verified" if status == "pass" else "construction-invalid",
        "automatic_promotion_gate": "pass" if status == "pass" else "blocked",
        "audit_engine": "static_net_xml_connection_graph_delta",
        "source_traffic_side": source_traffic_side,
        "candidate_traffic_side": candidate_traffic_side,
        "netedit_required_for_gate": False,
        "target_source_junction_ids": sorted(source_scope),
        "target_candidate_junction_ids": sorted(candidate_scope),
        "source_audit_summary": _network_audit_summary(source_audit),
        "candidate_audit_summary": _network_audit_summary(candidate_audit),
        "outside_scope_compared_junction_count": len(compared_ids),
        "outside_scope_missing_junction_ids": missing_outside_ids,
        "outside_scope_added_junction_ids": added_outside_ids,
        "outside_scope_new_review_finding_count": new_review_count,
        "outside_scope_new_structural_finding_count": new_structural_count,
        "outside_scope_resolved_review_finding_count": resolved_review_count,
        "outside_scope_resolved_structural_finding_count": resolved_structural_count,
        "outside_scope_review_regressions": review_regressions,
        "outside_scope_structural_regressions": structural_regressions,
        "outside_scope_review_category_regressions": _scope_category_deltas(
            _aggregate_finding_categories(
                [source_records[junction_id] for junction_id in sorted(source_outside_ids)]
            )[0],
            _aggregate_finding_categories(
                [candidate_records[junction_id] for junction_id in sorted(candidate_outside_ids)]
            )[0],
        ),
        "outside_scope_structural_category_regressions": _scope_category_deltas(
            _aggregate_finding_categories(
                [source_records[junction_id] for junction_id in sorted(source_outside_ids)]
            )[1],
            _aggregate_finding_categories(
                [candidate_records[junction_id] for junction_id in sorted(candidate_outside_ids)]
            )[1],
        ),
        "outside_scope_regression_junction_ids": affected_ids,
        "target_scope_source_review_finding_count": sum(source_target_review.values()),
        "target_scope_source_structural_finding_count": sum(source_target_structural.values()),
        "target_scope_review_finding_count": target_review_count,
        "target_scope_structural_finding_count": target_structural_count,
        "target_scope_new_review_finding_count": target_new_review_count,
        "target_scope_new_structural_finding_count": target_new_structural_count,
        "target_scope_review_regressions": target_review_regressions,
        "target_scope_structural_regressions": target_structural_regressions,
        "target_scope_review_category_regressions": _scope_category_deltas(
            source_target_review,
            candidate_target_review,
        ),
        "target_scope_structural_category_regressions": _scope_category_deltas(
            source_target_structural,
            candidate_target_structural,
        ),
        "target_scope_flagged_junction_ids": target_flagged_ids,
        "blockers": blockers,
        "warnings": [
            "new target-scope review findings require review and block automatic promotion",
            "raw finding witnesses can contain unstable connection indices; use the stable corridor exact-semantic audit for promotion",
        ],
    }


def audit_network_connection_mode(
    root: ET.Element,
    *,
    junction_ids: Sequence[str] | None = None,
    traffic_side: str = "auto",
    endpoint_tolerance_m: float = 2.0,
    normalized_lane_rank_tolerance: float = 0.5,
    evidence_justified_target_fanouts: Mapping[str, Mapping[str, Any]] | None = None,
    evidence_justified_destination_merges: Mapping[str, Mapping[str, Any]] | None = None,
    evidence_justified_lane_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reconstruct and audit Connection Mode for every relevant junction."""

    traffic_side_contract = resolve_network_traffic_side(root, traffic_side)
    effective_traffic_side = str(traffic_side_contract["effective"])
    catalog = build_connection_mode_catalog(root)
    requested = None if junction_ids is None else {str(value) for value in junction_ids}
    missing_requested = sorted(
        requested - set(catalog["junctions"]) if requested is not None else set()
    )
    junction_records: list[dict[str, Any]] = []
    for junction_id, junction in sorted(catalog["junctions"].items()):
        if requested is not None and junction_id not in requested:
            continue
        if not _junction_requires_connection_audit(
            junction,
            catalog=catalog,
        ):
            continue
        audit = audit_standard_connection_mode(
            root,
            junction_id=junction_id,
            movement_rows=(),
            layout_type="unknown",
            traffic_side=effective_traffic_side,
            endpoint_tolerance_m=endpoint_tolerance_m,
            normalized_lane_rank_tolerance=normalized_lane_rank_tolerance,
            evidence_justified_target_fanouts=evidence_justified_target_fanouts,
            evidence_justified_destination_merges=evidence_justified_destination_merges,
            evidence_justified_lane_bindings=evidence_justified_lane_bindings,
            catalog=catalog,
        )
        controller_ids = sorted(
            {
                str(binding.get("tl", ""))
                for binding in audit.get("request_foe_audit", {}).get(
                    "request_bindings", []
                )
                if binding.get("tl")
            }
        )
        junction_records.append(
            {
                "junction_id": junction_id,
                "junction_type": junction.attrib.get("type", ""),
                "position": {
                    "x": _as_float(junction.attrib.get("x"), 0.0),
                    "y": _as_float(junction.attrib.get("y"), 0.0),
                },
                "controller_ids": controller_ids,
                "status": audit["status"],
                "connection_mode_audit": audit,
            }
        )

    tls_audit = _audit_network_tls_link_bindings(
        catalog=catalog,
        junction_records=junction_records,
    )
    tls_by_junction = tls_audit.pop("junction_audits")
    for record in junction_records:
        junction_tls = tls_by_junction.get(
            record["junction_id"],
            _empty_tls_junction_audit(record["junction_id"]),
        )
        record["tls_link_binding_audit"] = junction_tls
        if junction_tls["status"] == "fail":
            record["status"] = "fail"
        elif junction_tls["status"] == "review_required" and record["status"] == "pass":
            record["status"] = "review_required"

    status_counts = Counter(record["status"] for record in junction_records)
    connection_findings = Counter()
    for record in junction_records:
        connection_audit = record["connection_mode_audit"]
        for finding in connection_audit.get("structural_failures", []):
            connection_findings[_finding_category(str(finding))] += 1
        for finding in connection_audit.get("review_findings", []):
            connection_findings[_finding_category(str(finding))] += 1
    for finding in tls_audit.get("structural_failures", []):
        connection_findings[_finding_category(str(finding))] += 1
    for finding in tls_audit.get("review_findings", []):
        connection_findings[_finding_category(str(finding))] += 1
    configuration_failures = [
        f"connection_mode:{failure}"
        for failure in traffic_side_contract["failures"]
    ]
    for finding in configuration_failures:
        connection_findings[_finding_category(finding)] += 1
    status = (
        "fail"
        if (
            missing_requested
            or configuration_failures
            or status_counts["fail"]
            or tls_audit["status"] == "fail"
        )
        else "review_required"
        if status_counts["review_required"] or tls_audit["status"] == "review_required"
        else "pass"
    )
    return {
        "schema": "torii.network_connection_mode_audit.v1",
        "status": status,
        "claim_status": "verified" if status == "pass" else "diagnostic-demo",
        "automatic_promotion_gate": "pass" if status == "pass" else "blocked",
        "audit_engine": "static_net_xml_connection_graph",
        "internal_link_mode": catalog["internal_link_mode"],
        "traffic_side": effective_traffic_side,
        "traffic_side_contract": traffic_side_contract,
        "configuration_failures": configuration_failures,
        "netedit_required_for_gate": False,
        "netedit_role": "optional visual review for flagged junctions only",
        "requested_junction_ids": sorted(requested or []),
        "missing_requested_junction_ids": missing_requested,
        "junction_count": len(junction_records),
        "pass_count": status_counts["pass"],
        "review_required_count": status_counts["review_required"],
        "fail_count": status_counts["fail"],
        "direct_movement_count": sum(
            record["connection_mode_audit"].get("direct_movement_count", 0)
            for record in junction_records
        ),
        "verified_internal_path_count": sum(
            record["connection_mode_audit"].get("verified_internal_path_count", 0)
            for record in junction_records
        ),
        "structural_failure_count": sum(
            len(record["connection_mode_audit"].get("structural_failures", []))
            for record in junction_records
        )
        + len(tls_audit.get("structural_failures", []))
        + len(configuration_failures),
        "review_finding_count": sum(
            len(record["connection_mode_audit"].get("review_findings", []))
            for record in junction_records
        )
        + len(tls_audit.get("review_findings", [])),
        "evidence_justified_target_fanout_count": sum(
            len(record["connection_mode_audit"].get("evidence_justified_target_fanouts", []))
            for record in junction_records
        ),
        "evidence_justified_destination_merge_count": sum(
            len(record["connection_mode_audit"].get("evidence_justified_destination_merges", []))
            for record in junction_records
        ),
        "evidence_justified_lane_binding_count": sum(
            len(record["connection_mode_audit"].get("evidence_justified_lane_bindings", []))
            for record in junction_records
        ),
        "finding_category_counts": dict(sorted(connection_findings.items())),
        "tls_link_binding_audit": tls_audit,
        "junctions": junction_records,
        "warnings": [
            "map imagery or lane-tag evidence remains required before repairing review findings"
        ],
    }


def audit_standard_connection_mode(
    root: ET.Element,
    *,
    junction_id: str,
    movement_rows: Sequence[Mapping[str, Any]],
    layout_type: str,
    traffic_side: str = "auto",
    endpoint_tolerance_m: float = 2.0,
    normalized_lane_rank_tolerance: float = 0.5,
    evidence_justified_target_fanouts: Mapping[str, Mapping[str, Any]] | None = None,
    evidence_justified_destination_merges: Mapping[str, Mapping[str, Any]] | None = None,
    evidence_justified_lane_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed on lane-to-lane bindings that only look routeable.

    The audit models the information visible in NetEdit Connection Mode: the
    direct ``fromLane -> toLane -> via`` binding, the complete internal-lane
    continuation, traffic-side-aware lane roles, the junction request/foe matrix,
    and every movement pair that a canonical NEMA controller may serve at the
    same time. It deliberately remains stricter than SUMO's graph-level
    routeability test.
    """

    traffic_side_contract = resolve_network_traffic_side(root, traffic_side)
    effective_traffic_side = str(traffic_side_contract["effective"])
    hard_blockers: list[str] = []
    for failure in traffic_side_contract["failures"]:
        _block(hard_blockers, failure)
    review_findings: list[str] = []
    justified_lane_bindings: list[dict[str, Any]] = []
    warnings = [
        "map imagery or lane-tag evidence remains required for flagged semantic ambiguities; NetEdit is optional"
    ]
    prepared = catalog if catalog is not None else build_connection_mode_catalog(root)
    edges: Mapping[str, ET.Element] = prepared["edges"]
    connections: Sequence[ET.Element] = prepared["connections"]
    junction = prepared["junctions"].get(junction_id)
    lane_catalog: Mapping[str, Mapping[str, Any]] = prepared["lane_catalog"]
    lanes_by_edge: Mapping[str, Mapping[int, Mapping[str, Any]]] = prepared[
        "lanes_by_edge"
    ]
    outgoing_by_lane: Mapping[
        tuple[str, int], Sequence[tuple[int, ET.Element]]
    ] = prepared["outgoing_by_lane"]

    provided_rows = [dict(row) for row in movement_rows]
    nema_rows_by_index: dict[int, dict[str, Any]] = {}
    for row in provided_rows:
        connection_index = _as_int(row.get("connection_index"))
        if connection_index is None or not 0 <= connection_index < len(connections):
            _block(hard_blockers, f"movement_connection_index_invalid:{row.get('connection_index', '')}")
            continue
        if connection_index in nema_rows_by_index:
            _block(hard_blockers, f"movement_connection_index_duplicate:{connection_index}")
            continue
        nema_rows_by_index[connection_index] = row

    ordered_indices = _ordered_junction_connection_indices(
        junction=junction,
        lane_catalog=lane_catalog,
        outgoing_by_lane=outgoing_by_lane,
        edges=edges,
    )
    rows: list[dict[str, Any]] = []
    for connection_index in ordered_indices:
        row = dict(nema_rows_by_index.get(connection_index, {}))
        row["connection_index"] = connection_index
        row["nema_candidate_movement"] = connection_index in nema_rows_by_index
        rows.append(row)
    movement_checks: list[dict[str, Any]] = []
    valid_lane_rows: list[dict[str, Any]] = []

    signature_counts: Counter[tuple[str, ...]] = Counter()
    binding_counts: Counter[tuple[str, ...]] = Counter()
    via_counts: Counter[str] = Counter()
    target_fanout: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    destination_merges: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    justified_target_fanouts: list[dict[str, Any]] = []
    justified_destination_merges: list[dict[str, Any]] = []

    for row in rows:
        connection_index = _as_int(row.get("connection_index"))
        if connection_index is None or not 0 <= connection_index < len(connections):
            continue
        connection = connections[connection_index]
        source_edge = connection.attrib.get("from", "")
        target_edge = connection.attrib.get("to", "")
        source_index = _as_int(connection.attrib.get("fromLane"))
        target_index = _as_int(connection.attrib.get("toLane"))
        via_lane_id = connection.attrib.get("via", "")
        turn = _connection_turn(connection, row)
        phase = _as_int(row.get("nema_phase"))
        check: dict[str, Any] = {
            "connection_index": connection_index,
            "from": source_edge,
            "fromLane": connection.attrib.get("fromLane", ""),
            "to": target_edge,
            "toLane": connection.attrib.get("toLane", ""),
            "via": via_lane_id,
            "turn": turn,
            "nema_phase": phase,
            "nema_candidate_movement": bool(row.get("nema_candidate_movement")),
            "status": "pass",
            "failures": [],
        }

        source_lane = _lane_for(lanes_by_edge, source_edge, source_index)
        target_lane = _lane_for(lanes_by_edge, target_edge, target_index)
        if source_index is None or source_lane is None:
            _fail(check, hard_blockers, f"source_lane_invalid:{connection_index}:{source_edge}:{connection.attrib.get('fromLane', '')}")
        if target_index is None or target_lane is None:
            _fail(check, hard_blockers, f"target_lane_invalid:{connection_index}:{target_edge}:{connection.attrib.get('toLane', '')}")

        signature = tuple(
            connection.attrib.get(key, "")
            for key in ("from", "to", "fromLane", "toLane", "via")
        )
        binding = signature[:4]
        signature_counts[signature] += 1
        binding_counts[binding] += 1
        if via_lane_id:
            via_counts[via_lane_id] += 1
        target_fanout[(source_edge, connection.attrib.get("fromLane", ""), target_edge)].add(
            connection.attrib.get("toLane", "")
        )
        destination_merges[(source_edge, target_edge, connection.attrib.get("toLane", ""))].add(
            connection.attrib.get("fromLane", "")
        )

        if source_lane is not None and target_lane is not None and source_index is not None and target_index is not None:
            source_count = len(lanes_by_edge.get(source_edge, {}))
            target_count = len(lanes_by_edge.get(target_edge, {}))
            source_motorized = _motorized_lane_indices(lanes_by_edge.get(source_edge, {}))
            target_motorized = _motorized_lane_indices(lanes_by_edge.get(target_edge, {}))
            motorized_movement = (
                source_index in source_motorized and target_index in target_motorized
            )
            check["mode_scope"] = (
                "motorized" if motorized_movement else "non_motorized_or_mixed"
            )
            if motorized_movement:
                source_from_curb = _lane_indices_from_curb(
                    source_motorized,
                    traffic_side=effective_traffic_side,
                )
                target_from_curb = _lane_indices_from_curb(
                    target_motorized,
                    traffic_side=effective_traffic_side,
                )
                source_rank = (source_from_curb.index(source_index) + 0.5) / len(source_from_curb)
                target_rank = (target_from_curb.index(target_index) + 0.5) / len(target_from_curb)
                rank_delta = abs(source_rank - target_rank)
                check.update(
                    {
                        "normalized_source_lane_rank": round(source_rank, 6),
                        "normalized_target_lane_rank": round(target_rank, 6),
                        "normalized_lane_rank_delta": round(rank_delta, 6),
                        "normalized_lane_rank_basis": "curb_to_inner",
                    }
                )
                if rank_delta > normalized_lane_rank_tolerance:
                    binding_key = "|".join(
                        (source_edge, str(source_index), target_edge, str(target_index))
                    )
                    binding_evidence = (evidence_justified_lane_bindings or {}).get(binding_key)
                    finding = f"lane_rank_jump:{connection_index}:{rank_delta:.3f}"
                    if (
                        isinstance(binding_evidence, Mapping)
                        and binding_evidence.get("basis") == "official_map_connection_curve"
                        and str(binding_evidence.get("source_cell", ""))
                    ):
                        justified = {
                            "finding": finding,
                            "key": binding_key,
                            "from": source_edge,
                            "from_lane": source_index,
                            "to": target_edge,
                            "to_lane": target_index,
                            "evidence": dict(binding_evidence),
                        }
                        justified_lane_bindings.append(justified)
                        check.setdefault("evidence_justified_findings", []).append(justified)
                    else:
                        _flag_review(check, review_findings, finding)
                valid_lane_rows.append(
                    {
                        "connection_index": connection_index,
                        "from": source_edge,
                        "from_index": source_index,
                        "to": target_edge,
                        "to_index": target_index,
                        "turn": turn,
                        "phase": phase,
                    }
                )
            check.update(
                {
                    "source_lane_count": source_count,
                    "target_lane_count": target_count,
                    "source_motorized_lane_count": len(source_motorized),
                    "target_motorized_lane_count": len(target_motorized),
                }
            )

        trace, trace_failures, trace_review_findings = _trace_internal_path(
            connection_index=connection_index,
            connection=connection,
            source_lane=source_lane,
            target_lane=target_lane,
            edges=edges,
            outgoing_by_lane=outgoing_by_lane,
            lane_catalog=lane_catalog,
            lanes_by_edge=lanes_by_edge,
            endpoint_tolerance_m=endpoint_tolerance_m,
            internal_link_mode=str(prepared["internal_link_mode"]),
            internal_lane_count=int(prepared["internal_lane_count"]),
        )
        check["internal_path"] = trace
        if trace.get("unusually_long"):
            _flag_review(
                check,
                review_findings,
                (
                    f"internal_path_unusually_long:{connection_index}:"
                    f"{trace.get('internal_lane_chain_length', 0)}"
                ),
            )
        for finding in trace_review_findings:
            _flag_review(check, review_findings, finding)
        for failure in trace_failures:
            _fail(check, hard_blockers, failure)
        movement_checks.append(check)

    for signature, count in sorted(signature_counts.items()):
        if count > 1:
            _block(hard_blockers, f"duplicate_direct_connection:{'|'.join(signature)}:{count}")
    for binding, count in sorted(binding_counts.items()):
        if count > 1:
            _block(hard_blockers, f"duplicate_lane_binding:{'|'.join(binding)}:{count}")
    for via_lane_id, count in sorted(via_counts.items()):
        if count > 1:
            _block(hard_blockers, f"duplicate_direct_via_lane:{via_lane_id}:{count}")
    for key, target_lanes in sorted(target_fanout.items()):
        if len(target_lanes) > 1:
            finding = (
                f"ambiguous_target_lane_fanout:{'|'.join(key)}:"
                f"{','.join(sorted(target_lanes))}"
            )
            evidence = (evidence_justified_target_fanouts or {}).get("|".join(key))
            expected_lanes = (
                sorted(str(value) for value in evidence.get("target_lanes", ()))
                if isinstance(evidence, Mapping)
                else []
            )
            if (
                isinstance(evidence, Mapping)
                and evidence.get("basis") == "official_map_connection_curve"
                and expected_lanes == sorted(target_lanes)
                and str(evidence.get("source_cell", ""))
            ):
                justified_target_fanouts.append(
                    {
                        "finding": finding,
                        "key": "|".join(key),
                        "target_lanes": sorted(target_lanes),
                        "evidence": dict(evidence),
                    }
                )
            else:
                _review(review_findings, finding)
    for key, source_lanes in sorted(destination_merges.items()):
        if len(source_lanes) > 1:
            finding = f"destination_lane_merge:{'|'.join(key)}:{','.join(sorted(source_lanes))}"
            evidence = (evidence_justified_destination_merges or {}).get("|".join(key))
            expected_lanes = (
                sorted(str(value) for value in evidence.get("source_lanes", ()))
                if isinstance(evidence, Mapping)
                else []
            )
            official_map_evidence = (
                isinstance(evidence, Mapping)
                and evidence.get("basis") == "official_map_connection_curve"
                and expected_lanes == sorted(source_lanes)
                and str(evidence.get("source_cell", ""))
            )
            engineering_plan_evidence = _engineering_plan_lane_arrows_justify_destination_merge(
                evidence,
                actual_source_lanes=source_lanes,
            )
            if official_map_evidence or engineering_plan_evidence:
                justified_destination_merges.append(
                    {
                        "finding": finding,
                        "key": "|".join(key),
                        "source_lanes": sorted(source_lanes),
                        "evidence": dict(evidence),
                    }
                )
            else:
                _review(review_findings, finding)

    lane_order_checks = _audit_lane_order(
        valid_lane_rows,
        lanes_by_edge=lanes_by_edge,
        traffic_side=effective_traffic_side,
        review_findings=review_findings,
    )
    _audit_lane_mapping_monotonicity(valid_lane_rows, review_findings=review_findings)
    request_audit = _audit_request_foes(
        junction=junction,
        rows=rows,
        connections=connections,
        edges=edges,
        lane_catalog=lane_catalog,
        outgoing_by_lane=outgoing_by_lane,
        layout_type=layout_type,
        blockers=hard_blockers,
        review_findings=review_findings,
    )
    completeness_audit = _audit_connection_completeness(
        junction=junction,
        ordered_indices=ordered_indices,
        connections=connections,
        edges=edges,
        lane_catalog=lane_catalog,
        lanes_by_edge=lanes_by_edge,
        outgoing_by_lane=outgoing_by_lane,
        edges_from_junction=prepared["edges_from_junction"],
        review_findings=review_findings,
    )

    unique_hard_blockers = list(dict.fromkeys(hard_blockers))
    unique_review_findings = list(dict.fromkeys(review_findings))
    unique_blockers = list(dict.fromkeys(unique_hard_blockers + unique_review_findings))
    status = (
        "fail"
        if unique_hard_blockers
        else "review_required"
        if unique_review_findings
        else "pass"
    )
    return {
        "schema": "torii.connection_mode_audit.v1",
        "status": status,
        "automatic_nema_gate": "pass" if not unique_blockers else "blocked",
        "junction_id": junction_id,
        "layout_type": layout_type,
        "traffic_side": effective_traffic_side,
        "traffic_side_contract": traffic_side_contract,
        "endpoint_tolerance_m": endpoint_tolerance_m,
        "normalized_lane_rank_tolerance": normalized_lane_rank_tolerance,
        "direct_movement_count": len(rows),
        "motorized_direct_movement_count": sum(
            check.get("mode_scope") == "motorized" for check in movement_checks
        ),
        "nema_candidate_movement_count": len(nema_rows_by_index),
        "verified_internal_path_count": sum(
            check.get("internal_path", {}).get("status") == "pass"
            for check in movement_checks
        ),
        "movement_checks": movement_checks,
        "lane_order_checks": lane_order_checks,
        "connection_completeness_audit": completeness_audit,
        "request_foe_audit": request_audit,
        "structural_failures": unique_hard_blockers,
        "review_findings": unique_review_findings,
        "evidence_justified_target_fanouts": justified_target_fanouts,
        "evidence_justified_destination_merges": justified_destination_merges,
        "evidence_justified_lane_bindings": justified_lane_bindings,
        "blockers": unique_blockers,
        "warnings": warnings,
        "human_review_requirement": (
            "review flagged semantic ambiguities against map or lane-tag evidence; "
            "NetEdit is optional visualization"
        ),
    }


def _audit_connection_completeness(
    *,
    junction: ET.Element | None,
    ordered_indices: Sequence[int],
    connections: Sequence[ET.Element],
    edges: Mapping[str, ET.Element],
    lane_catalog: Mapping[str, Mapping[str, Any]],
    lanes_by_edge: Mapping[str, Mapping[int, Mapping[str, Any]]],
    outgoing_by_lane: Mapping[tuple[str, int], Sequence[tuple[int, ET.Element]]],
    edges_from_junction: Mapping[str, Sequence[str]],
    review_findings: list[str],
) -> dict[str, Any]:
    """Find motor lanes that silently disappear at one physical junction.

    Graph-level routeability can still pass when one lane on a multi-lane edge
    has no connection at all because vehicles can use a neighbouring lane.
    NetEdit makes that omission visible in Connection Mode; this check rebuilds
    the same evidence directly from ``incLanes`` and the direct connection set.

    Missing coverage remains a review finding rather than a structural XML
    failure.  Boundary stubs, access-restricted lanes, and intentional lane
    drops are legal SUMO models and need map/lane-marking evidence before a
    repair is materialized.
    """

    audit: dict[str, Any] = {
        "status": "pass",
        "junction_id": "" if junction is None else junction.attrib.get("id", ""),
        "incoming_motorized_lane_count": 0,
        "connected_incoming_motorized_lane_count": 0,
        "outgoing_motorized_lane_count": 0,
        "reachable_outgoing_motorized_lane_count": 0,
        "incoming_lane_checks": [],
        "outgoing_lane_checks": [],
        "findings": [],
    }
    if junction is None:
        return audit

    junction_id = junction.attrib.get("id", "")
    ordered_set = set(ordered_indices)
    incoming_checks: list[dict[str, Any]] = audit["incoming_lane_checks"]
    for lane_id in junction.attrib.get("incLanes", "").split():
        lane = lane_catalog.get(lane_id)
        if lane is None:
            continue
        edge_id = str(lane.get("edge_id", ""))
        lane_index = int(lane.get("index", -1))
        edge = edges.get(edge_id)
        if (
            edge is None
            or edge.attrib.get("to", "") != junction_id
            or _is_internal_or_pedestrian_edge(edge)
            or not lane_supports_motorized(lane.get("element"))
        ):
            continue
        connection_indices = sorted(
            connection_index
            for connection_index, connection in outgoing_by_lane.get(
                (edge_id, lane_index), []
            )
            if connection_index in ordered_set
            and _connection_targets_motorized_lane(
                connection,
                edges=edges,
                lanes_by_edge=lanes_by_edge,
            )
        )
        status = "pass" if connection_indices else "review_required"
        check = {
            "lane_id": lane_id,
            "edge_id": edge_id,
            "lane_index": lane_index,
            "connection_indices": connection_indices,
            "status": status,
        }
        incoming_checks.append(check)
        if not connection_indices:
            finding = f"incoming_motorized_lane_without_connection:{edge_id}:{lane_index}"
            audit["findings"].append(finding)
            _review(review_findings, finding)

    targeted_outgoing_lanes: set[tuple[str, int]] = set()
    for connection_index in ordered_indices:
        if not 0 <= connection_index < len(connections):
            continue
        connection = connections[connection_index]
        target_edge_id = connection.attrib.get("to", "")
        target_index = _as_int(connection.attrib.get("toLane"))
        target_edge = edges.get(target_edge_id)
        target_lane = _lane_for(lanes_by_edge, target_edge_id, target_index)
        if (
            target_edge is not None
            and target_edge.attrib.get("from", "") == junction_id
            and not _is_internal_or_pedestrian_edge(target_edge)
            and target_index is not None
            and target_lane is not None
            and lane_supports_motorized(target_lane.get("element"))
        ):
            targeted_outgoing_lanes.add((target_edge_id, target_index))

    outgoing_checks: list[dict[str, Any]] = audit["outgoing_lane_checks"]
    for edge_id in edges_from_junction.get(junction_id, ()):
        edge = edges[edge_id]
        if (
            _is_internal_or_pedestrian_edge(edge)
        ):
            continue
        for lane_index in _motorized_lane_indices(lanes_by_edge.get(edge_id, {})):
            reachable = (edge_id, lane_index) in targeted_outgoing_lanes
            outgoing_checks.append(
                {
                    "lane_id": str(
                        lanes_by_edge.get(edge_id, {}).get(lane_index, {}).get("id", "")
                    ),
                    "edge_id": edge_id,
                    "lane_index": lane_index,
                    "status": "pass" if reachable else "review_required",
                }
            )
            if not reachable:
                finding = f"outgoing_motorized_lane_without_connection:{edge_id}:{lane_index}"
                audit["findings"].append(finding)
                _review(review_findings, finding)

    audit["incoming_motorized_lane_count"] = len(incoming_checks)
    audit["connected_incoming_motorized_lane_count"] = sum(
        check["status"] == "pass" for check in incoming_checks
    )
    audit["outgoing_motorized_lane_count"] = len(outgoing_checks)
    audit["reachable_outgoing_motorized_lane_count"] = sum(
        check["status"] == "pass" for check in outgoing_checks
    )
    audit["findings"] = list(dict.fromkeys(audit["findings"]))
    audit["status"] = "review_required" if audit["findings"] else "pass"
    return audit


def _is_internal_or_pedestrian_edge(edge: ET.Element) -> bool:
    edge_id = edge.attrib.get("id", "")
    return edge_id.startswith(":") or edge.attrib.get("function", "") in {
        "internal",
        "crossing",
        "walkingarea",
    }


def _connection_targets_motorized_lane(
    connection: ET.Element,
    *,
    edges: Mapping[str, ET.Element],
    lanes_by_edge: Mapping[str, Mapping[int, Mapping[str, Any]]],
) -> bool:
    target_edge_id = connection.attrib.get("to", "")
    target_edge = edges.get(target_edge_id)
    target_lane = _lane_for(
        lanes_by_edge,
        target_edge_id,
        _as_int(connection.attrib.get("toLane")),
    )
    return bool(
        target_edge is not None
        and not _is_internal_or_pedestrian_edge(target_edge)
        and target_lane is not None
        and lane_supports_motorized(target_lane.get("element"))
    )


def _group_tl_logics(root: ET.Element) -> dict[str, list[ET.Element]]:
    grouped: dict[str, list[ET.Element]] = defaultdict(list)
    for logic in root.findall("tlLogic"):
        tl_id = logic.attrib.get("id", "")
        if tl_id:
            grouped[tl_id].append(logic)
    return grouped


def _junction_requires_connection_audit(
    junction: ET.Element,
    *,
    catalog: Mapping[str, Any],
) -> bool:
    junction_id = junction.attrib.get("id", "")
    if not junction_id or junction_id.startswith(":") or junction.attrib.get("type") in {
        "dead_end",
        "internal",
    }:
        return False
    ordered = _ordered_junction_connection_indices(
        junction=junction,
        lane_catalog=catalog["lane_catalog"],
        outgoing_by_lane=catalog["outgoing_by_lane"],
        edges=catalog["edges"],
    )
    if ordered or junction.findall("request"):
        return True

    has_incoming_motorized = False
    for lane_id in junction.attrib.get("incLanes", "").split():
        lane = catalog["lane_catalog"].get(lane_id)
        if lane is None:
            continue
        edge = catalog["edges"].get(str(lane.get("edge_id", "")))
        if (
            edge is not None
            and edge.attrib.get("to", "") == junction_id
            and not _is_internal_or_pedestrian_edge(edge)
            and lane_supports_motorized(lane.get("element"))
        ):
            has_incoming_motorized = True
            break
    has_outgoing_motorized = any(
        not _is_internal_or_pedestrian_edge(catalog["edges"][edge_id])
        and bool(_motorized_lane_indices(catalog["lanes_by_edge"].get(edge_id, {})))
        for edge_id in catalog["edges_from_junction"].get(junction_id, ())
    )
    return has_incoming_motorized and has_outgoing_motorized


def _audit_network_tls_link_bindings(
    *,
    catalog: Mapping[str, Any],
    junction_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate current controller/linkIndex bindings and protected greens."""

    connections: Sequence[ET.Element] = catalog["connections"]
    logics_by_id: Mapping[str, Sequence[ET.Element]] = catalog["tl_logics_by_id"]
    connection_owner: dict[int, str] = {}
    per_junction = {
        str(record["junction_id"]): _empty_tls_junction_audit(
            str(record["junction_id"])
        )
        for record in junction_records
    }
    for record in junction_records:
        junction_id = str(record["junction_id"])
        bindings = record["connection_mode_audit"].get("request_foe_audit", {}).get(
            "request_bindings", []
        )
        for binding in bindings:
            connection_index = _as_int(binding.get("connection_index"))
            if connection_index is not None:
                connection_owner[connection_index] = junction_id

    structural_failures: list[str] = []
    review_findings: list[str] = []
    controller_connections: dict[str, list[int]] = defaultdict(list)
    controller_link_indices: dict[str, set[int]] = defaultdict(set)
    controlled_connection_count = 0
    link_index2_connection_count = 0
    runtime_managed_rail_connection_count = 0
    runtime_managed_rail_controllers: set[str] = set()

    def add_failure(failure: str, *, connection_index: int | None = None) -> None:
        tagged = f"tls_link_binding:{failure}"
        structural_failures.append(tagged)
        junction_id = connection_owner.get(connection_index) if connection_index is not None else None
        if junction_id:
            per_junction[junction_id]["structural_failures"].append(tagged)

    def add_review(finding: str, *, junction_id: str | None = None) -> None:
        tagged = f"tls_link_binding:{finding}"
        review_findings.append(tagged)
        if junction_id:
            per_junction[junction_id]["review_findings"].append(tagged)

    for connection_index, connection in enumerate(connections):
        controller_id = connection.attrib.get("tl", "")
        if not controller_id:
            continue
        controlled_connection_count += 1
        controller_connections[controller_id].append(connection_index)
        logics = logics_by_id.get(controller_id, ())
        owner_id = connection_owner.get(connection_index, "")
        owner = catalog["junctions"].get(owner_id)
        runtime_managed_rail = bool(
            owner is not None
            and owner.attrib.get("type", "") in {"rail_crossing", "rail_signal"}
            and not logics
        )
        if runtime_managed_rail:
            runtime_managed_rail_connection_count += 1
            runtime_managed_rail_controllers.add(controller_id)
            continue
        raw_link_index = connection.attrib.get("linkIndex")
        link_index = _as_int(raw_link_index)
        if link_index is None or link_index < 0:
            add_failure(
                f"link_index_invalid:{connection_index}:{controller_id}:{raw_link_index or 'missing'}",
                connection_index=connection_index,
            )
            continue
        controller_link_indices[controller_id].add(link_index)
        link_indices = [link_index]
        if "linkIndex2" in connection.attrib:
            link_index2_connection_count += 1
            raw_link_index2 = connection.attrib.get("linkIndex2")
            link_index2 = _as_int(raw_link_index2)
            if link_index2 is None or link_index2 < 0:
                add_failure(
                    f"link_index2_invalid:{connection_index}:{controller_id}:"
                    f"{raw_link_index2 or 'missing'}",
                    connection_index=connection_index,
                )
            else:
                link_indices.append(link_index2)
                controller_link_indices[controller_id].add(link_index2)

        if not logics:
            add_failure(
                f"controller_logic_missing:{connection_index}:{controller_id}",
                connection_index=connection_index,
            )
            continue
        for logic in logics:
            program_id = logic.attrib.get("programID", "")
            phases = logic.findall("phase")
            if not phases:
                add_failure(
                    f"controller_program_has_no_phases:{controller_id}:{program_id}",
                    connection_index=connection_index,
                )
                continue
            state_lengths = {len(phase.attrib.get("state", "")) for phase in phases}
            if len(state_lengths) != 1:
                add_failure(
                    f"controller_program_state_lengths_inconsistent:{controller_id}:"
                    f"{program_id}:{','.join(map(str, sorted(state_lengths)))}",
                    connection_index=connection_index,
                )
            for checked_link_index in link_indices:
                if any(
                    checked_link_index >= len(phase.attrib.get("state", ""))
                    for phase in phases
                ):
                    add_failure(
                        f"link_index_out_of_program_state:{connection_index}:{controller_id}:"
                        f"{program_id}:{checked_link_index}",
                        connection_index=connection_index,
                    )

    protected_green_conflicts: list[dict[str, Any]] = []
    shared_foe_link_indices: list[dict[str, Any]] = []
    checked_phase_count = 0
    for record in junction_records:
        junction_id = str(record["junction_id"])
        junction = catalog["junctions"].get(junction_id)
        if junction is None:
            continue
        requests = {
            index: request
            for request in junction.findall("request")
            if (index := _as_int(request.attrib.get("index"))) is not None
        }
        bindings = record["connection_mode_audit"].get("request_foe_audit", {}).get(
            "request_bindings", []
        )
        controlled_bindings = [
            binding
            for binding in bindings
            if binding.get("tl") and _as_int(binding.get("linkIndex")) is not None
        ]
        per_junction[junction_id]["controlled_request_binding_count"] = len(
            controlled_bindings
        )
        for first_position, first in enumerate(controlled_bindings):
            first_request = int(first["request_index"])
            first_link_index = int(first["linkIndex"])
            for second in controlled_bindings[first_position + 1 :]:
                if first.get("tl") != second.get("tl"):
                    continue
                second_request = int(second["request_index"])
                if first_request not in requests or second_request not in requests:
                    continue
                if not (
                    _safely_are_foes(requests, first_request, second_request)
                    or _safely_are_foes(requests, second_request, first_request)
                ):
                    continue
                second_link_index = int(second["linkIndex"])
                controller_id = str(first["tl"])
                if first_link_index == second_link_index:
                    evidence = {
                        "junction_id": junction_id,
                        "controller_id": controller_id,
                        "request_indices": [first_request, second_request],
                        "connection_indices": [
                            first["connection_index"],
                            second["connection_index"],
                        ],
                        "shared_link_index": first_link_index,
                    }
                    shared_foe_link_indices.append(evidence)
                    per_junction[junction_id]["shared_foe_link_indices"].append(evidence)
                    add_review(
                        f"foe_movements_share_link_index:{junction_id}:{controller_id}:"
                        f"{first_request}:{second_request}:{first_link_index}",
                        junction_id=junction_id,
                    )
                for logic in logics_by_id.get(controller_id, ()):
                    program_id = logic.attrib.get("programID", "")
                    for phase_index, phase in enumerate(logic.findall("phase")):
                        checked_phase_count += 1
                        state = phase.attrib.get("state", "")
                        if max(first_link_index, second_link_index) >= len(state):
                            continue
                        if state[first_link_index] != "G" or state[second_link_index] != "G":
                            continue
                        evidence = {
                            "junction_id": junction_id,
                            "controller_id": controller_id,
                            "program_id": program_id,
                            "phase_index": phase_index,
                            "phase_name": phase.attrib.get("name", ""),
                            "request_indices": [first_request, second_request],
                            "connection_indices": [
                                first["connection_index"],
                                second["connection_index"],
                            ],
                            "link_indices": [first_link_index, second_link_index],
                        }
                        protected_green_conflicts.append(evidence)
                        per_junction[junction_id]["protected_green_foe_conflicts"].append(
                            evidence
                        )
                        add_review(
                            f"protected_green_foes:{junction_id}:{controller_id}:"
                            f"{program_id}:{phase_index}:{first_request}:{second_request}",
                            junction_id=junction_id,
                        )

    structural_failures = list(dict.fromkeys(structural_failures))
    review_findings = list(dict.fromkeys(review_findings))
    for audit in per_junction.values():
        audit["structural_failures"] = list(
            dict.fromkeys(audit["structural_failures"])
        )
        audit["review_findings"] = list(dict.fromkeys(audit["review_findings"]))
        audit["status"] = (
            "fail"
            if audit["structural_failures"]
            else "review_required"
            if audit["review_findings"]
            else "pass"
        )

    controller_records = []
    for controller_id, connection_indices in sorted(controller_connections.items()):
        programs = []
        for logic in logics_by_id.get(controller_id, ()):
            lengths = sorted(
                {len(phase.attrib.get("state", "")) for phase in logic.findall("phase")}
            )
            programs.append(
                {
                    "program_id": logic.attrib.get("programID", ""),
                    "type": logic.attrib.get("type", ""),
                    "phase_count": len(logic.findall("phase")),
                    "state_lengths": lengths,
                }
            )
        controller_records.append(
            {
                "controller_id": controller_id,
                "controlled_connection_count": len(connection_indices),
                "connection_indices": connection_indices,
                "used_link_indices": sorted(controller_link_indices[controller_id]),
                "control_semantics": (
                    "sumo_runtime_rail_signal"
                    if controller_id in runtime_managed_rail_controllers
                    else "tlLogic_program"
                ),
                "programs": programs,
            }
        )

    status = (
        "fail"
        if structural_failures
        else "review_required"
        if review_findings
        else "pass"
    )
    return {
        "schema": "torii.tls_link_binding_audit.v1",
        "status": status,
        "controller_count": len(controller_records),
        "controlled_connection_count": controlled_connection_count,
        "link_index2_connection_count": link_index2_connection_count,
        "runtime_managed_rail_connection_count": runtime_managed_rail_connection_count,
        "runtime_managed_rail_controller_ids": sorted(runtime_managed_rail_controllers),
        "checked_phase_pair_count": checked_phase_count,
        "protected_green_foe_conflicts": protected_green_conflicts,
        "shared_foe_link_indices": shared_foe_link_indices,
        "structural_failures": structural_failures,
        "review_findings": review_findings,
        "controllers": controller_records,
        "junction_audits": per_junction,
    }


def _empty_tls_junction_audit(junction_id: str) -> dict[str, Any]:
    return {
        "status": "pass",
        "junction_id": junction_id,
        "controlled_request_binding_count": 0,
        "protected_green_foe_conflicts": [],
        "shared_foe_link_indices": [],
        "structural_failures": [],
        "review_findings": [],
    }


def _write_network_connection_review_overlay(
    path: Path,
    junction_records: Sequence[Mapping[str, Any]],
    *,
    source_sha256: str,
) -> None:
    root = ET.Element("additional")
    for record in junction_records:
        status = str(record.get("status", "pass"))
        if status == "pass":
            continue
        junction_id = str(record.get("junction_id", ""))
        connection_audit = record.get("connection_mode_audit", {})
        tls_audit = record.get("tls_link_binding_audit", {})
        structural = list(connection_audit.get("structural_failures", [])) + list(
            tls_audit.get("structural_failures", [])
        )
        review = list(connection_audit.get("review_findings", [])) + list(
            tls_audit.get("review_findings", [])
        )
        position = record.get("position", {})
        poi = ET.SubElement(
            root,
            "poi",
            {
                "id": f"torii_connection_mode_review_{junction_id}",
                "type": f"torii.review.connection_mode.{status}",
                "color": "255,0,0" if status == "fail" else "255,165,0",
                "layer": "1001",
                "x": str(position.get("x", 0.0)),
                "y": str(position.get("y", 0.0)),
                "name": f"Connection review {junction_id}: {status}",
            },
        )
        values = (
            ("display_only", "true"),
            ("junction_id", junction_id),
            ("status", status),
            ("structural_failures", "; ".join(map(str, structural))),
            ("review_findings", "; ".join(map(str, review))),
            ("source_sha256", source_sha256),
            ("operational_change", "none"),
        )
        for key, value in values:
            ET.SubElement(poi, "param", {"key": key, "value": value})
    ET.indent(root, space="    ")
    write_text_atomic(
        path,
        ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8"),
    )


def _expand_scope_by_tls_controller(
    audit: Mapping[str, Any],
    junction_ids: Sequence[str],
) -> list[str]:
    """Include every audited junction that belongs to a touched TLS controller."""

    records = {
        str(record.get("junction_id", "")): record
        for record in audit.get("junctions", [])
        if record.get("junction_id")
    }
    scope = {str(value) for value in junction_ids if str(value)}
    controllers: set[str] = set()
    changed = True
    while changed:
        changed = False
        for junction_id in tuple(scope):
            record = records.get(junction_id)
            if record is None:
                continue
            for controller_id in record.get("controller_ids", []) or []:
                value = str(controller_id)
                if value and value not in controllers:
                    controllers.add(value)
                    changed = True
        if controllers:
            for junction_id, record in records.items():
                record_controllers = {
                    str(value)
                    for value in record.get("controller_ids", []) or []
                    if str(value)
                }
                if record_controllers & controllers and junction_id not in scope:
                    scope.add(junction_id)
                    changed = True
    return sorted(scope)


def _expand_coupled_scopes_by_tls_controller(
    source_audit: Mapping[str, Any],
    candidate_audit: Mapping[str, Any],
    source_junction_ids: Sequence[str],
    candidate_junction_ids: Sequence[str],
) -> tuple[list[str], list[str]]:
    """Close both sides over shared IDs and every touched TLS controller."""

    shared_scope = {
        *(str(value) for value in source_junction_ids if str(value)),
        *(str(value) for value in candidate_junction_ids if str(value)),
    }
    while True:
        expanded = {
            *_expand_scope_by_tls_controller(source_audit, sorted(shared_scope)),
            *_expand_scope_by_tls_controller(candidate_audit, sorted(shared_scope)),
        }
        if expanded == shared_scope:
            break
        shared_scope = expanded
    scope = sorted(shared_scope)
    return scope, list(scope)


def _aggregate_finding_categories(
    records: Sequence[Mapping[str, Any]],
) -> tuple[Counter[str], Counter[str]]:
    review = Counter()
    structural = Counter()
    for record in records:
        record_review, record_structural = _junction_finding_categories(record)
        review.update(record_review)
        structural.update(record_structural)
    return review, structural


def _scope_category_deltas(
    source: Counter[str],
    candidate: Counter[str],
) -> list[dict[str, Any]]:
    return [
        {
            "category": category,
            "source_count": source[category],
            "candidate_count": candidate[category],
            "delta": candidate[category] - source[category],
        }
        for category in sorted(source.keys() | candidate.keys())
        if candidate[category] > source[category]
    ]


def _junction_findings(
    record: Mapping[str, Any],
    *,
    finding_kind: str,
) -> list[str]:
    key = "review_findings" if finding_kind == "review" else "structural_failures"
    connection_audit = record.get("connection_mode_audit", {})
    tls_audit = record.get("tls_link_binding_audit", {})
    return [
        *map(str, connection_audit.get(key, []) or []),
        *map(str, tls_audit.get(key, []) or []),
    ]


def _junction_finding_categories(
    record: Mapping[str, Any],
) -> tuple[Counter[str], Counter[str]]:
    return (
        Counter(
            _finding_category(finding)
            for finding in _junction_findings(record, finding_kind="review")
        ),
        Counter(
            _finding_category(finding)
            for finding in _junction_findings(record, finding_kind="structural")
        ),
    )


def _aggregate_finding_witnesses(
    records: Sequence[Mapping[str, Any]],
    *,
    finding_kind: str,
) -> Counter[str]:
    findings: Counter[str] = Counter()
    for record in records:
        findings.update(_junction_findings(record, finding_kind=finding_kind))
    return findings


def _scope_finding_deltas(
    source_findings: Counter[str],
    candidate_findings: Counter[str],
) -> list[dict[str, Any]]:
    return _positive_finding_deltas(
        "target-scope",
        source_findings,
        candidate_findings,
    )


def _positive_finding_deltas(
    junction_id: str,
    source_findings: Counter[str],
    candidate_findings: Counter[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for finding in sorted(source_findings.keys() | candidate_findings.keys()):
        delta = candidate_findings[finding] - source_findings[finding]
        if delta <= 0:
            continue
        records.append(
            {
                "junction_id": junction_id,
                "category": _finding_category(finding),
                "finding_witness": finding,
                "source_count": source_findings[finding],
                "candidate_count": candidate_findings[finding],
                "delta": delta,
                "examples": [finding],
            }
        )
    return records


def _network_audit_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "status",
        "junction_count",
        "pass_count",
        "review_required_count",
        "fail_count",
        "direct_movement_count",
        "verified_internal_path_count",
        "structural_failure_count",
        "review_finding_count",
        "finding_category_counts",
    )
    return {key: report.get(key) for key in keys}


def _finding_category(finding: str) -> str:
    parts = finding.split(":")
    if len(parts) >= 2 and parts[0] in {"connection_mode", "tls_link_binding"}:
        return parts[1]
    return parts[0]


def _build_lane_catalog(
    edges: Mapping[str, ET.Element],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[int, dict[str, Any]]]]:
    lane_catalog: dict[str, dict[str, Any]] = {}
    lanes_by_edge: dict[str, dict[int, dict[str, Any]]] = {}
    for edge_id, edge in edges.items():
        indexed: dict[int, dict[str, Any]] = {}
        for ordinal, lane in enumerate(edge.findall("lane")):
            # SUMO connection fromLane/toLane values address the lane's
            # position within its edge. Generated internal edges may contain
            # repeated XML ``index`` attributes (for example two lanes both
            # carrying index="0") while internal connections still address
            # them as lane ordinals 0 and 1. Using the attribute here creates
            # false path jumps and can make an auditor "repair" a valid net.
            lane_index = ordinal
            record = {
                "id": lane.attrib.get("id", ""),
                "edge_id": edge_id,
                "index": lane_index,
                "declared_index": _as_int(lane.attrib.get("index")),
                "shape": _shape_points(lane.attrib.get("shape", "")),
                "element": lane,
            }
            indexed[lane_index] = record
            if record["id"]:
                lane_catalog[str(record["id"])] = record
        lanes_by_edge[edge_id] = indexed
    return lane_catalog, lanes_by_edge


def _trace_internal_path(
    *,
    connection_index: int,
    connection: ET.Element,
    source_lane: Mapping[str, Any] | None,
    target_lane: Mapping[str, Any] | None,
    edges: Mapping[str, ET.Element],
    outgoing_by_lane: Mapping[tuple[str, int], Sequence[tuple[int, ET.Element]]],
    lane_catalog: Mapping[str, Mapping[str, Any]],
    lanes_by_edge: Mapping[str, Mapping[int, Mapping[str, Any]]],
    endpoint_tolerance_m: float,
    internal_link_mode: str,
    internal_lane_count: int,
) -> tuple[dict[str, Any], list[str], list[str]]:
    failures: list[str] = []
    review_findings: list[str] = []
    via_lane_id = connection.attrib.get("via", "")
    target_edge = connection.attrib.get("to", "")
    target_index = _as_int(connection.attrib.get("toLane"))
    trace = {
        "status": "fail",
        "internal_lane_chain": [],
        "internal_connection_indices": [],
        "endpoint_gaps_m": [],
        "endpoint_effective_gaps_m": [],
        "endpoint_width_transition_offsets_m": [],
        "max_endpoint_gap_m": None,
        "max_effective_endpoint_gap_m": None,
        "path_permission_status": "not-verified",
        "path_permission_basis": "",
        "path_permission_overlap": [],
        "internal_lane_chain_length": 0,
        "bounded_hop_limit": 0,
        "unusually_long": False,
    }
    gaps: list[float] = []
    if not via_lane_id:
        source_edge = edges.get(str(source_lane.get("edge_id", ""))) if source_lane else None
        target_edge = edges.get(str(target_lane.get("edge_id", ""))) if target_lane else None
        source_function = source_edge.attrib.get("function", "") if source_edge is not None else ""
        target_function = target_edge.attrib.get("function", "") if target_edge is not None else ""
        if (
            internal_link_mode == "no-internal-links"
            and source_edge is not None
            and target_edge is not None
            and not source_edge.attrib.get("id", "").startswith(":")
            and not target_edge.attrib.get("id", "").startswith(":")
        ):
            trace["path_kind"] = "direct_no_internal_links"
            trace["endpoint_gap_policy"] = "not_available_in_network_mode"
            _audit_path_permissions(
                trace,
                elements=(
                    _lane_element(source_lane),
                    connection,
                    _lane_element(target_lane),
                ),
                connection_index=connection_index,
                failures=failures,
            )
            trace["status"] = "pass"
            if failures:
                trace["status"] = "fail"
            return trace, failures, review_findings
        if source_function == "walkingarea" and target_function == "crossing":
            source_shape = list(source_lane.get("shape", [])) if source_lane else []
            target_shape = list(target_lane.get("shape", [])) if target_lane else []
            if len(source_shape) < 2 or len(target_shape) < 2:
                failures.append(
                    f"lane_shape_missing_or_short:{connection_index}:direct_walkingarea_to_crossing"
                )
            else:
                gaps.append(
                    min(
                        math.dist(source_point, target_point)
                        for source_point in (source_shape[0], source_shape[-1])
                        for target_point in (target_shape[0], target_shape[-1])
                    )
                )
            trace["path_kind"] = "direct_walkingarea_to_crossing"
            trace["endpoint_gap_policy"] = "nearest_endpoint_diagnostic_only"
            trace["endpoint_gaps_m"] = [round(value, 6) for value in gaps]
            trace["endpoint_effective_gaps_m"] = [round(value, 6) for value in gaps]
            trace["endpoint_width_transition_offsets_m"] = [0.0 for _ in gaps]
            trace["max_endpoint_gap_m"] = round(max(gaps), 6) if gaps else None
            trace["max_effective_endpoint_gap_m"] = (
                round(max(gaps), 6) if gaps else None
            )
            _audit_path_permissions(
                trace,
                elements=(
                    _lane_element(source_lane),
                    connection,
                    _lane_element(target_lane),
                ),
                connection_index=connection_index,
                failures=failures,
            )
            trace["status"] = "pass" if not failures else "fail"
            return trace, failures, review_findings
        failures.append(f"missing_direct_via_lane:{connection_index}")
        return trace, failures, review_findings
    via_lane = lane_catalog.get(via_lane_id)
    if via_lane is None:
        failures.append(f"direct_via_lane_not_found:{connection_index}:{via_lane_id}")
        return trace, failures, review_findings
    via_edge_id = str(via_lane["edge_id"])
    via_edge = edges.get(via_edge_id)
    if via_edge is None or (
        via_edge.attrib.get("function") != "internal" and not via_edge_id.startswith(":")
    ):
        failures.append(f"direct_via_lane_not_internal:{connection_index}:{via_lane_id}")
        return trace, failures, review_findings

    effective_gaps: list[float] = []
    width_transition_offsets: list[float] = []
    permission_elements: list[ET.Element | None] = [
        _lane_element(source_lane),
        connection,
        _lane_element(via_lane),
    ]
    path_complete = False
    _check_shape_gap(
        source_lane,
        via_lane,
        connection_index=connection_index,
        hop="entry",
        tolerance=endpoint_tolerance_m,
        gaps=gaps,
        effective_gaps=effective_gaps,
        width_transition_offsets=width_transition_offsets,
        failures=failures,
        review_findings=review_findings,
    )
    current_lane = via_lane
    visited: set[str] = set()
    bounded_hop_limit = max(1, internal_lane_count + 1)
    trace["bounded_hop_limit"] = bounded_hop_limit
    for hop in range(bounded_hop_limit):
        current_lane_id = str(current_lane.get("id", ""))
        if current_lane_id in visited:
            failures.append(f"internal_path_cycle:{connection_index}:{current_lane_id}")
            break
        visited.add(current_lane_id)
        trace["internal_lane_chain"].append(current_lane_id)
        current_edge = str(current_lane["edge_id"])
        current_index = int(current_lane["index"])
        candidates = list(outgoing_by_lane.get((current_edge, current_index), []))
        if len(candidates) != 1:
            failures.append(
                f"internal_path_outgoing_count_not_one:{connection_index}:{current_lane_id}:{len(candidates)}"
            )
            break
        internal_connection_index, internal_connection = candidates[0]
        trace["internal_connection_indices"].append(internal_connection_index)
        permission_elements.append(internal_connection)
        declared_target = internal_connection.attrib.get("to", "")
        declared_target_index = _as_int(internal_connection.attrib.get("toLane"))
        continuation_id = internal_connection.attrib.get("via", "")
        if declared_target != target_edge or declared_target_index != target_index:
            failures.append(
                f"internal_path_target_mismatch:{connection_index}:{internal_connection_index}:"
                f"{declared_target}:{internal_connection.attrib.get('toLane', '')}"
            )
        if continuation_id:
            next_lane = lane_catalog.get(continuation_id)
            if next_lane is None:
                failures.append(
                    f"internal_continuation_lane_not_found:{connection_index}:{continuation_id}"
                )
                break
            next_edge = edges.get(str(next_lane["edge_id"]))
            if next_edge is None or (
                next_edge.attrib.get("function") != "internal"
                and not str(next_lane["edge_id"]).startswith(":")
            ):
                failures.append(
                    f"internal_continuation_not_internal:{connection_index}:{continuation_id}"
                )
                break
            _check_shape_gap(
                current_lane,
                next_lane,
                connection_index=connection_index,
                hop=f"internal_{hop}",
                tolerance=endpoint_tolerance_m,
                gaps=gaps,
                effective_gaps=effective_gaps,
                width_transition_offsets=width_transition_offsets,
                failures=failures,
                review_findings=review_findings,
            )
            permission_elements.append(_lane_element(next_lane))
            current_lane = next_lane
            continue

        final_lane = _lane_for(lanes_by_edge, declared_target, declared_target_index)
        if final_lane is None:
            failures.append(
                f"internal_path_final_lane_invalid:{connection_index}:{declared_target}:"
                f"{internal_connection.attrib.get('toLane', '')}"
            )
            break
        _check_shape_gap(
            current_lane,
            final_lane,
            connection_index=connection_index,
            hop="exit",
            tolerance=endpoint_tolerance_m,
            gaps=gaps,
            effective_gaps=effective_gaps,
            width_transition_offsets=width_transition_offsets,
            failures=failures,
            review_findings=review_findings,
        )
        if target_lane is not None and str(final_lane.get("id", "")) != str(target_lane.get("id", "")):
            failures.append(f"internal_path_final_lane_mismatch:{connection_index}")
        permission_elements.append(_lane_element(final_lane))
        path_complete = True
        break
    else:
        failures.append(
            f"internal_path_bounded_trace_exhausted:{connection_index}:{bounded_hop_limit}"
        )

    trace["endpoint_gaps_m"] = [round(value, 6) for value in gaps]
    trace["endpoint_effective_gaps_m"] = [
        round(value, 6) for value in effective_gaps
    ]
    trace["endpoint_width_transition_offsets_m"] = [
        round(value, 6) for value in width_transition_offsets
    ]
    trace["max_endpoint_gap_m"] = round(max(gaps), 6) if gaps else None
    trace["max_effective_endpoint_gap_m"] = (
        round(max(effective_gaps), 6) if effective_gaps else None
    )
    trace["internal_lane_chain_length"] = len(trace["internal_lane_chain"])
    trace["unusually_long"] = len(trace["internal_lane_chain"]) > 16
    if path_complete:
        _audit_path_permissions(
            trace,
            elements=permission_elements,
            connection_index=connection_index,
            failures=failures,
        )
    trace["status"] = "pass" if not failures else "fail"
    return trace, failures, review_findings


def _infer_internal_link_mode(
    edges: Mapping[str, ET.Element],
    connections: Sequence[ET.Element],
) -> str:
    internal_edge_count = sum(
        edge_id.startswith(":")
        or edge.attrib.get("function", "") in {"internal", "crossing", "walkingarea"}
        for edge_id, edge in edges.items()
    )
    external_direct = []
    for connection in connections:
        source = edges.get(connection.attrib.get("from", ""))
        target = edges.get(connection.attrib.get("to", ""))
        if source is None or target is None:
            continue
        if (
            not source.attrib.get("id", "").startswith(":")
            and not target.attrib.get("id", "").startswith(":")
            and source.attrib.get("function", "") not in {"internal", "crossing", "walkingarea"}
            and target.attrib.get("function", "") not in {"internal", "crossing", "walkingarea"}
        ):
            external_direct.append(connection)
    direct_with_via = sum(bool(connection.attrib.get("via")) for connection in external_direct)
    if external_direct and not internal_edge_count and not direct_with_via:
        return "no-internal-links"
    if external_direct and 0 < direct_with_via < len(external_direct):
        return "mixed"
    if internal_edge_count or direct_with_via:
        return "internal-links"
    return "undetermined"


def _check_shape_gap(
    current_lane: Mapping[str, Any] | None,
    next_lane: Mapping[str, Any] | None,
    *,
    connection_index: int,
    hop: str,
    tolerance: float,
    gaps: list[float],
    effective_gaps: list[float],
    width_transition_offsets: list[float],
    failures: list[str],
    review_findings: list[str],
) -> None:
    current_shape = list(current_lane.get("shape", [])) if current_lane is not None else []
    next_shape = list(next_lane.get("shape", [])) if next_lane is not None else []
    if len(current_shape) < 2 or len(next_shape) < 2:
        failures.append(f"lane_shape_missing_or_short:{connection_index}:{hop}")
        return
    gap, effective_gap, width_transition_offset = _effective_endpoint_gap(
        current_lane,
        next_lane,
        current_shape=current_shape,
        next_shape=next_shape,
    )
    gaps.append(gap)
    effective_gaps.append(effective_gap)
    width_transition_offsets.append(width_transition_offset)
    if effective_gap > tolerance:
        failures.append(f"path_endpoint_gap:{connection_index}:{hop}:{gap:.3f}m")
    elif gap > tolerance and width_transition_offset > 0.0:
        review_findings.append(
            f"path_width_transition_centerline_offset:{connection_index}:{hop}:"
            f"{gap:.3f}m:{effective_gap:.3f}m"
        )


def _effective_endpoint_gap(
    current_lane: Mapping[str, Any] | None,
    next_lane: Mapping[str, Any] | None,
    *,
    current_shape: Sequence[tuple[float, float]],
    next_shape: Sequence[tuple[float, float]],
) -> tuple[float, float, float]:
    """Separate a real path break from a SUMO lane-width centreline shift.

    Netconvert aligns a narrowing or widening lane ribbon at one border.  The
    two lane centre lines can therefore differ by half the width change while
    the traversable ribbons remain continuous.  Only the lateral component of
    that offset is discounted; a longitudinal break remains fully visible.
    """

    delta = (
        next_shape[0][0] - current_shape[-1][0],
        next_shape[0][1] - current_shape[-1][1],
    )
    raw_gap = math.hypot(*delta)
    current_width = _lane_width_m(current_lane)
    next_width = _lane_width_m(next_lane)
    width_transition_offset = abs(current_width - next_width) / 2.0
    effective_candidates: list[float] = []
    for first, second in (
        (current_shape[-2], current_shape[-1]),
        (next_shape[0], next_shape[1]),
    ):
        tangent = _unit_vector(first, second)
        if tangent is None:
            continue
        longitudinal = abs(delta[0] * tangent[0] + delta[1] * tangent[1])
        lateral = abs(delta[0] * -tangent[1] + delta[1] * tangent[0])
        effective_candidates.append(
            math.hypot(
                longitudinal,
                max(0.0, lateral - width_transition_offset),
            )
        )
    effective_gap = min(effective_candidates, default=raw_gap)
    return raw_gap, effective_gap, width_transition_offset


def _unit_vector(
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[float, float] | None:
    delta_x = second[0] - first[0]
    delta_y = second[1] - first[1]
    length = math.hypot(delta_x, delta_y)
    if length <= 0.0:
        return None
    return delta_x / length, delta_y / length


def _lane_width_m(lane: Mapping[str, Any] | None) -> float:
    element = lane.get("element") if lane is not None else None
    if not isinstance(element, ET.Element):
        return 3.2
    width = _as_float(element.attrib.get("width"), 3.2)
    return width if width > 0.0 else 3.2


def _lane_element(lane: Mapping[str, Any] | None) -> ET.Element | None:
    element = lane.get("element") if lane is not None else None
    return element if isinstance(element, ET.Element) else None


def _audit_path_permissions(
    trace: dict[str, Any],
    *,
    elements: Sequence[ET.Element | None],
    connection_index: int,
    failures: list[str],
) -> None:
    available = tuple(element for element in elements if element is not None)
    if len(available) != len(elements):
        return
    overlap, basis = _permission_intersection(available)
    trace["path_permission_basis"] = basis
    trace["path_permission_overlap"] = list(overlap)
    trace["path_permission_status"] = "pass" if overlap else "fail"
    if not overlap:
        failures.append(f"internal_path_mode_permission_empty:{connection_index}")


def _permission_intersection(
    elements: Sequence[ET.Element],
) -> tuple[tuple[str, ...], str]:
    explicit: set[str] | None = None
    disallowed: set[str] = set()
    for element in elements:
        allowed = set(element.attrib.get("allow", "").split())
        denied = set(element.attrib.get("disallow", "").split())
        if "all" in denied:
            return (), "explicit_empty"
        disallowed.update(denied)
        if allowed and "all" not in allowed:
            explicit = allowed if explicit is None else explicit & allowed
    if explicit is None:
        return ("*",), "implicit_all_except_disallow"
    overlap = tuple(sorted(explicit - disallowed))
    return overlap, "explicit_allow_intersection"


def _audit_lane_order(
    rows: Sequence[Mapping[str, Any]],
    *,
    lanes_by_edge: Mapping[str, Mapping[int, Mapping[str, Any]]],
    traffic_side: str,
    review_findings: list[str],
) -> list[dict[str, Any]]:
    by_edge: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        turn = str(row.get("turn", ""))
        if turn in _TURN_RANK:
            by_edge[str(row["from"])][turn].add(int(row["from_index"]))
    checks: list[dict[str, Any]] = []
    for edge_id, turns in sorted(by_edge.items()):
        failures: list[str] = []
        for lower, higher in (("r", "s"), ("r", "l"), ("s", "l")):
            if any(
                lower_lane > higher_lane
                for lower_lane in turns.get(lower, set())
                for higher_lane in turns.get(higher, set())
            ):
                failure = f"turn_lane_order_inversion:{edge_id}:{lower}>{higher}"
                failures.append(failure)
                _review(review_findings, failure)
        lane_count = len(lanes_by_edge.get(edge_id, {}))
        motorized_lanes = _motorized_lane_indices(lanes_by_edge.get(edge_id, {}))
        curb_lane = (
            (min(motorized_lanes) if traffic_side == "right" else max(motorized_lanes))
            if motorized_lanes
            else None
        )
        inner_lane = (
            (max(motorized_lanes) if traffic_side == "right" else min(motorized_lanes))
            if motorized_lanes
            else None
        )
        if turns.get("r") and motorized_lanes and min(turns["r"]) != min(motorized_lanes):
            role = "curb" if traffic_side == "right" else "innermost"
            failure = f"right_turn_not_{role}_lane:{edge_id}:{min(turns['r'])}"
            failures.append(failure)
            _review(review_findings, failure)
        if turns.get("l") and motorized_lanes and max(turns["l"]) != max(motorized_lanes):
            role = "innermost" if traffic_side == "right" else "curb"
            failure = (
                f"left_turn_not_{role}_lane:{edge_id}:{max(turns['l'])}:"
                f"{max(motorized_lanes)}"
            )
            failures.append(failure)
            _review(review_findings, failure)
        checks.append(
            {
                "incoming_edge": edge_id,
                "lane_count": lane_count,
                "traffic_side": traffic_side,
                "motorized_lane_indices": motorized_lanes,
                "curb_lane_index": curb_lane,
                "innermost_lane_index": inner_lane,
                "turn_lanes": {turn: sorted(indices) for turn, indices in sorted(turns.items())},
                "status": "pass" if not failures else "review_required",
                "failures": failures,
            }
        )
    return checks


def _audit_lane_mapping_monotonicity(
    rows: Sequence[Mapping[str, Any]],
    *,
    review_findings: list[str],
) -> None:
    by_edge_pair: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for row in rows:
        by_edge_pair[(str(row["from"]), str(row["to"]))].append(
            (int(row["from_index"]), int(row["to_index"]))
        )
    for (source, target), lane_pairs in sorted(by_edge_pair.items()):
        ordered = sorted(set(lane_pairs))
        for first, second in zip(ordered, ordered[1:]):
            if first[0] < second[0] and first[1] > second[1]:
                _review(
                    review_findings,
                    f"crossed_lane_mapping:{source}:{target}:{first[0]}>{first[1]}:"
                    f"{second[0]}>{second[1]}",
                )


def _audit_request_foes(
    *,
    junction: ET.Element | None,
    rows: Sequence[Mapping[str, Any]],
    connections: Sequence[ET.Element],
    edges: Mapping[str, ET.Element],
    lane_catalog: Mapping[str, Mapping[str, Any]],
    outgoing_by_lane: Mapping[tuple[str, int], Sequence[tuple[int, ET.Element]]],
    layout_type: str,
    blockers: list[str],
    review_findings: list[str],
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "status": "fail",
        "request_count": 0,
        "ordered_movement_count": 0,
        "request_bindings": [],
        "compatible_phase_pairs": _compatible_phase_pair_labels(layout_type),
        "concurrent_pair_check_count": 0,
        "foe_conflicts": [],
        "asymmetric_foe_pair_count": 0,
        "foe_matrix_semantics": "directional_by_design",
        "failures": [],
    }
    failures: list[str] = audit["failures"]
    if junction is None:
        failure = "junction_element_missing"
        failures.append(failure)
        _block(blockers, failure)
        return audit

    row_by_index = {
        int(row["connection_index"]): row
        for row in rows
        if _as_int(row.get("connection_index")) is not None
    }
    ordered_indices = _ordered_junction_connection_indices(
        junction=junction,
        lane_catalog=lane_catalog,
        outgoing_by_lane=outgoing_by_lane,
        edges=edges,
    )
    audit["ordered_movement_count"] = len(ordered_indices)
    nema_candidate_indices = {
        connection_index
        for connection_index, row in row_by_index.items()
        if row.get("nema_candidate_movement")
    }
    unresolved_rows = sorted(nema_candidate_indices - set(ordered_indices))
    if unresolved_rows:
        failure = "request_movement_order_unresolved:" + ",".join(map(str, unresolved_rows))
        failures.append(failure)
        _block(blockers, failure)

    requests = junction.findall("request")
    audit["request_count"] = len(requests)
    if len(requests) != len(ordered_indices):
        failure = f"request_count_mismatch:{len(requests)}:{len(ordered_indices)}"
        failures.append(failure)
        _block(blockers, failure)
    request_by_index: dict[int, ET.Element] = {}
    for request in requests:
        index = _as_int(request.attrib.get("index"))
        if index is None or index in request_by_index:
            continue
        request_by_index[index] = request
    expected_indices = set(range(len(requests)))
    if set(request_by_index) != expected_indices:
        failure = "request_index_set_not_contiguous"
        failures.append(failure)
        _block(blockers, failure)

    matrix_valid = len(requests) == len(ordered_indices) and set(request_by_index) == expected_indices
    if matrix_valid:
        for request_index, request in sorted(request_by_index.items()):
            for field in ("foes", "response"):
                bits = request.attrib.get(field, "")
                if len(bits) != len(requests) or set(bits) - {"0", "1"}:
                    failure = f"request_{field}_bitstring_invalid:{request_index}:{len(bits)}:{len(requests)}"
                    failures.append(failure)
                    _block(blockers, failure)
                    matrix_valid = False

    if matrix_valid:
        asymmetric_count = 0
        for first in range(len(requests)):
            for second in range(first + 1, len(requests)):
                if _are_foes(request_by_index, first, second) != _are_foes(
                    request_by_index, second, first
                ):
                    asymmetric_count += 1
        # SUMO builds directional foe rows. Rules such as mayDefinitelyPass,
        # right-turn overrides, and merge cooperation can legally make the
        # matrix asymmetric, so asymmetry is evidence rather than corruption.
        audit["asymmetric_foe_pair_count"] = asymmetric_count

    request_bindings: list[dict[str, Any]] = []
    if len(ordered_indices) == len(requests):
        for request_index, connection_index in enumerate(ordered_indices):
            connection = connections[connection_index]
            row = row_by_index.get(connection_index, {})
            request_bindings.append(
                {
                    "request_index": request_index,
                    "connection_index": connection_index,
                    "from": connection.attrib.get("from", ""),
                    "fromLane": connection.attrib.get("fromLane", ""),
                    "to": connection.attrib.get("to", ""),
                    "toLane": connection.attrib.get("toLane", ""),
                    "tl": connection.attrib.get("tl", ""),
                    "linkIndex": _as_int(connection.attrib.get("linkIndex")),
                    "linkIndex2": _as_int(connection.attrib.get("linkIndex2")),
                    "turn": _connection_turn(connection, row),
                    "nema_phase": _as_int(row.get("nema_phase")),
                    "nema_vehicle_movement": bool(row.get("nema_candidate_movement")),
                }
            )
    audit["request_bindings"] = request_bindings

    if matrix_valid and len(request_bindings) == len(requests):
        compatible_pairs = _compatible_phase_pairs(layout_type)
        pair_check_count = 0
        foe_conflicts: list[dict[str, Any]] = []
        for first in range(len(request_bindings)):
            for second in range(first + 1, len(request_bindings)):
                first_phase = request_bindings[first]["nema_phase"]
                second_phase = request_bindings[second]["nema_phase"]
                if first_phase is None or second_phase is None:
                    continue
                may_run_together = first_phase == second_phase or frozenset(
                    {first_phase, second_phase}
                ) in compatible_pairs
                if not may_run_together:
                    continue
                pair_check_count += 1
                first_marks_second = _are_foes(request_by_index, first, second)
                second_marks_first = _are_foes(request_by_index, second, first)
                if first_marks_second or second_marks_first:
                    conflict = {
                        "first_request_index": first,
                        "second_request_index": second,
                        "first_connection_index": request_bindings[first]["connection_index"],
                        "second_connection_index": request_bindings[second]["connection_index"],
                        "first_phase": first_phase,
                        "second_phase": second_phase,
                        "first_marks_second_as_foe": first_marks_second,
                        "second_marks_first_as_foe": second_marks_first,
                    }
                    foe_conflicts.append(conflict)
                    _review(
                        review_findings,
                        f"nema_concurrent_movements_are_foes:{first}:{second}:"
                        f"{first_phase}:{second_phase}",
                    )
        audit["concurrent_pair_check_count"] = pair_check_count
        audit["foe_conflicts"] = foe_conflicts

    audit["status"] = (
        "fail"
        if failures
        else "review_required"
        if audit["foe_conflicts"]
        else "pass"
    )
    return audit


def _counts_as_junction_request(
    connection: ET.Element,
    *,
    edges: Mapping[str, ET.Element],
) -> bool:
    from_edge = edges.get(connection.attrib.get("from", ""))
    to_edge = edges.get(connection.attrib.get("to", ""))
    from_function = from_edge.attrib.get("function", "") if from_edge is not None else ""
    to_function = to_edge.attrib.get("function", "") if to_edge is not None else ""
    return not (
        to_function == "walkingarea"
        or from_function == "walkingarea"
        and to_function != "crossing"
    )


def _ordered_junction_connection_indices(
    *,
    junction: ET.Element | None,
    lane_catalog: Mapping[str, Mapping[str, Any]],
    outgoing_by_lane: Mapping[tuple[str, int], Sequence[tuple[int, ET.Element]]],
    edges: Mapping[str, ET.Element],
) -> list[int]:
    if junction is None:
        return []
    ordered_indices: list[int] = []
    for lane_id in junction.attrib.get("incLanes", "").split():
        lane = lane_catalog.get(lane_id)
        if lane is None:
            continue
        candidates = outgoing_by_lane.get((str(lane["edge_id"]), int(lane["index"])), [])
        ordered_indices.extend(
            connection_index
            for connection_index, connection in candidates
            if _counts_as_junction_request(connection, edges=edges)
        )
    return ordered_indices


def _compatible_phase_pairs(layout_type: str) -> frozenset[frozenset[int]]:
    if layout_type == "four_way":
        return _FOUR_WAY_COMPATIBLE_PHASE_PAIRS
    if layout_type == "three_way":
        return _THREE_WAY_COMPATIBLE_PHASE_PAIRS
    return frozenset()


def _compatible_phase_pair_labels(layout_type: str) -> list[str]:
    return sorted("+".join(map(str, sorted(pair))) for pair in _compatible_phase_pairs(layout_type))


def _are_foes(requests: Mapping[int, ET.Element], first: int, second: int) -> bool:
    bits = requests[first].attrib.get("foes", "")
    return bits[len(bits) - second - 1] == "1"


def _safely_are_foes(
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


def _lane_for(
    lanes_by_edge: Mapping[str, Mapping[int, Mapping[str, Any]]],
    edge_id: str,
    lane_index: int | None,
) -> Mapping[str, Any] | None:
    if lane_index is None:
        return None
    return lanes_by_edge.get(edge_id, {}).get(lane_index)


def _connection_turn(connection: ET.Element, row: Mapping[str, Any]) -> str:
    source_turn = connection.attrib.get("dir", "").casefold()
    if source_turn in {"l", "r", "s", "t"}:
        return source_turn
    return str(row.get("effective_dir", row.get("geometry_dir", "")))


def _shape_points(value: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for token in value.split():
        coordinates = token.split(",")
        if len(coordinates) < 2:
            continue
        try:
            x = float(coordinates[0])
            y = float(coordinates[1])
        except ValueError:
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    return points


def lane_supports_motorized(lane: ET.Element | None) -> bool:
    if lane is None:
        return False
    allowed = set(lane.attrib.get("allow", "").split())
    disallowed = set(lane.attrib.get("disallow", "").split())
    if allowed:
        return bool(allowed & _MOTORIZED_MODES)
    return bool(_MOTORIZED_MODES - disallowed)


def _motorized_lane_indices(lanes: Mapping[int, Mapping[str, Any]]) -> list[int]:
    return sorted(
        lane_index
        for lane_index, record in lanes.items()
        if lane_supports_motorized(record.get("element"))
    )


def _lane_indices_from_curb(
    lane_indices: Sequence[int],
    *,
    traffic_side: str,
) -> list[int]:
    ordered = sorted(lane_indices)
    return ordered if traffic_side == "right" else list(reversed(ordered))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _block(blockers: list[str], failure: str) -> None:
    blockers.append(f"connection_mode:{failure}")


def _review(findings: list[str], finding: str) -> None:
    findings.append(f"connection_mode:{finding}")


def _fail(check: dict[str, Any], blockers: list[str], failure: str) -> None:
    check["status"] = "fail"
    check["failures"].append(failure)
    _block(blockers, failure)


def _flag_review(check: dict[str, Any], findings: list[str], finding: str) -> None:
    if check["status"] == "pass":
        check["status"] = "review_required"
    check["failures"].append(finding)
    _review(findings, finding)
