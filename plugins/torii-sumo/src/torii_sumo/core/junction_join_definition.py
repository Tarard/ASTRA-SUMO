from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class JunctionJoinRecord:
    source: str
    candidate_id: str
    action: str
    decision: str
    node_ids: tuple[str, ...]
    confidence: str
    reason: str
    google_maps_url: str


def build_junction_join_definition(
    candidates: Sequence[Mapping[str, Any]],
    *,
    output_dir: Path,
    prefix: str = "junction_aggregation",
) -> dict[str, Any]:
    """Write a SUMO plain-XML junction join patch from reviewed candidates."""
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_patch_file = output_dir / f"{prefix}_junction_join.nod.xml"
    definition_file = output_dir / f"{prefix}_junction_join_definition.json"
    definition_csv = output_dir / f"{prefix}_junction_join_definition.csv"

    records: list[JunctionJoinRecord] = []
    invalid_candidates: list[dict[str, str]] = []
    for candidate in candidates:
        parsed = _record_from_candidate(candidate)
        if len(parsed.node_ids) < 2:
            invalid_candidates.append(
                {
                    "candidate_id": parsed.candidate_id,
                    "decision": parsed.decision,
                    "reason": "junction join definitions require at least two node ids",
                }
            )
            continue
        records.append(parsed)

    explicit_joins = [record for record in records if record.action == "join"]
    excluded_groups = [record for record in records if record.action == "exclude"]
    _write_nodes_patch(nodes_patch_file, explicit_joins, excluded_groups)
    _write_definition_csv(definition_csv, records)

    report = {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "sumo_join_semantics": "plain_nodes_join_patch",
        "sumo_official_reference": [
            "https://sumo.dlr.de/docs/Networks/PlainXML.html#joining_nodes",
            "https://sumo.dlr.de/docs/netconvert.html#junctions",
        ],
        "review_policy": (
            "Use SUMO plain nodes <join> only for confirmed physical-intersection groups; "
            "write <joinExclude> for rejected or unresolved map-review groups so a later "
            "radius-based --junctions.join pass cannot silently merge them."
        ),
        "nodes_patch_file": str(nodes_patch_file),
        "definition_file": str(definition_file),
        "definition_csv": str(definition_csv),
        "netconvert_patch_args": netconvert_join_patch_args(nodes_patch_file),
        "candidate_count": len(candidates),
        "valid_candidate_count": len(records),
        "invalid_candidate_count": len(invalid_candidates),
        "explicit_join_count": len(explicit_joins),
        "join_exclude_count": len(excluded_groups),
        "needs_map_review_count": sum(1 for record in records if record.decision == "needs_map_review"),
        "do_not_join_count": sum(1 for record in records if record.decision == "do_not_join"),
        "invalid_candidates": invalid_candidates,
        "records": [_record_to_dict(record) for record in records],
    }
    definition_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def netconvert_join_patch_args(nodes_patch_file: Path) -> list[str]:
    return ["--node-files", str(nodes_patch_file)]


def _record_from_candidate(candidate: Mapping[str, Any]) -> JunctionJoinRecord:
    source = str(candidate.get("source", ""))
    confidence = str(candidate.get("confidence") or candidate.get("aggregation_confidence") or "")
    decision = str(candidate.get("decision") or candidate.get("aggregation_decision") or "needs_map_review")
    if str(candidate.get("corridor_decision", "")) == "reject":
        decision = "do_not_join"
    if decision == "join" and not _join_is_confirmed(source=source, confidence=confidence, candidate=candidate):
        decision = "needs_map_review"
    action = "join" if decision == "join" else "exclude"
    return JunctionJoinRecord(
        source=source,
        candidate_id=str(candidate.get("candidate_id") or candidate.get("cluster_id") or ""),
        action=action,
        decision=decision,
        node_ids=_normalize_node_ids(candidate.get("node_ids", ())),
        confidence=confidence,
        reason=str(candidate.get("reason") or candidate.get("aggregation_reason") or ""),
        google_maps_url=str(candidate.get("google_maps_url", "")),
    )


def _join_is_confirmed(*, source: str, confidence: str, candidate: Mapping[str, Any]) -> bool:
    tokens = {
        value.strip().lower()
        for value in (
            source,
            confidence,
            str(candidate.get("map_review_status", "")),
            str(candidate.get("manual_correction_status", "")),
            str(candidate.get("review_status", "")),
        )
        if value.strip()
    }
    return bool(
        tokens
        & {
            "confirmed",
            "map_confirmed",
            "confirmed_join",
            "target_evidence_confirmed",
        }
    )


def _normalize_node_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = re.split(r"[;,\s]+", value.strip())
    elif isinstance(value, Sequence):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = [str(value).strip()]

    seen: set[str] = set()
    node_ids: list[str] = []
    for item in raw_items:
        if not item or item in seen:
            continue
        node_ids.append(item)
        seen.add(item)
    return tuple(node_ids)


def _write_nodes_patch(
    path: Path,
    explicit_joins: Sequence[JunctionJoinRecord],
    excluded_groups: Sequence[JunctionJoinRecord],
) -> None:
    root = ET.Element("nodes")
    for record in explicit_joins:
        ET.SubElement(root, "join", {"nodes": " ".join(record.node_ids)})
    for record in excluded_groups:
        ET.SubElement(root, "joinExclude", {"nodes": " ".join(record.node_ids)})
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _write_definition_csv(path: Path, records: Sequence[JunctionJoinRecord]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "candidate_id",
                "action",
                "decision",
                "node_ids",
                "confidence",
                "reason",
                "google_maps_url",
            ],
        )
        writer.writeheader()
        for record in records:
            row = _record_to_dict(record)
            row["node_ids"] = ";".join(record.node_ids)
            writer.writerow(row)


def _record_to_dict(record: JunctionJoinRecord) -> dict[str, Any]:
    row = asdict(record)
    row["node_ids"] = list(record.node_ids)
    return row
