"""Create model-only lane references from a registered prepared-source revision."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
import json
import math
from pathlib import Path

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .network_calibration import (
    NetworkFactTable,
    _coordinate_transform,
    _network_root,
    _permissions,
    _saved_lane_paths,
    _shape,
)
from .reference_road_alignment import _edge_direction, _group_edges


def _slots(root, groups, way, direction):
    slots = defaultdict(list)
    for identity, edge in groups.get(way, []):
        if _edge_direction(identity) != direction:
            continue
        ranks = Counter()
        for lane in edge.findall("lane"):
            allowed = tuple(sorted(_permissions(lane, network_version=root.get("version", "0.0"))))
            slots[allowed, ranks[allowed]].append(lane.get("id"))
            ranks[allowed] += 1
    return slots


def build_prepared_source_lane_facts(
    *,
    before_net_file,
    reference_net_file,
    way_labels_file,
    output_dir,
    site_id,
    model_revision_date,
    license_name,
    sample_spacing_m,
):
    """Bind generated lane geometry to source-way labels without field authority.

    Labels must come from the same prepared-source namespace as both networks.
    This adapter records that assumption and preserves tentative correspondence.
    """
    before, reference, labels = [
        Path(value).resolve(strict=True) for value in (before_net_file, reference_net_file, way_labels_file)
    ]
    output = Path(output_dir).resolve()
    if output.exists() or any(path.is_relative_to(output) for path in (before, reference, labels)):
        raise ValueError("Use a new fact directory separate from every input.")
    if type(sample_spacing_m) not in (int, float) or not math.isfinite(sample_spacing_m) or sample_spacing_m <= 0:
        raise ValueError("Sample spacing must be finite and positive in metres.")
    model_date = date.fromisoformat(model_revision_date).isoformat()
    hashes = {path: file_sha256(path) for path in (before, reference, labels)}
    records = [json.loads(line) for line in labels.read_text(encoding="utf-8").splitlines() if line.strip()]
    if (
        not records
        or any(
            not isinstance(row, dict)
            or row.get("kind") != "prepared_way_coordinate_sequence_revision"
            or not isinstance(row.get("way_id"), str)
            or not row["way_id"].isdigit()
            or int(row["way_id"]) <= 0
            or not isinstance(row.get("external_source_id"), str)
            or not row["external_source_id"].strip()
            for row in records
        )
        or len({row["way_id"] for row in records}) != len(records)
    ):
        raise ValueError("Source-way labels require unique positive way IDs and external source identities.")
    roots = {name: _network_root(path.read_bytes()) for name, path in (("before", before), ("reference", reference))}
    if any(root.find("location") is None for root in roots.values()):
        raise ValueError("Source-revision facts require declared coordinate frames.")
    location = roots["reference"].find("location").attrib
    system = (
        {"type": "network_xy_m", "location": dict(location)}
        if location.get("projParameter") in {"", "!", "-", "."}
        else {"type": "projected_crs", "crs": location["projParameter"]}
    )
    transform = _coordinate_transform(roots["reference"], system)
    _coordinate_transform(roots["before"], system)
    groups = {
        name: _group_edges(
            {
                edge.get("id"): edge
                for edge in root.findall("edge")
                if not edge.get("id", "").startswith(":") and edge.get("function", "normal") == "normal"
            }
        )
        for name, root in roots.items()
    }
    reference_lanes = {lane.get("id"): lane for lane in roots["reference"].findall("edge/lane")}
    paths = _saved_lane_paths(roots["before"])
    sources = [
        {
            "id": identity,
            "path": str(path),
            "sha256": hashes[path],
            "source_date": None,
            "authority": authority,
            "license": license_name,
        }
        for identity, path, authority in (
            ("generated-model-reference", reference, "Generated model reference, not a field survey"),
            ("registered-source-revision", labels, "Prepared source-road geometry revisions, not field-defect labels"),
        )
    ]
    facts, controls, correspondences = [], [], []
    for record in records:
        start = len(facts)
        for direction in ("forward", "reverse"):
            old = _slots(roots["before"], groups["before"], record["way_id"], direction)
            new = _slots(roots["reference"], groups["reference"], record["way_id"], direction)
            for slot, reference_ids in new.items():
                ordinary = old.get(slot, [])
                selected = set(ordinary)
                for (first, last), candidates in paths.items():
                    if first in ordinary and last in ordinary and len(candidates) == 1:
                        selected.update(candidates[0][1:-1])
                allowed = slot[0]
                mode = (
                    "motor"
                    if "passenger" in allowed
                    else "bicycle"
                    if "bicycle" in allowed and "pedestrian" not in allowed
                    else "pedestrian"
                    if "pedestrian" in allowed
                    else "unspecified"
                )
                for reference_id in reference_ids:
                    try:
                        expected = [list(point) for point in transform(_shape(reference_lanes[reference_id]))]
                        if len({tuple(point) for point in expected}) < 2:
                            raise ValueError("Reference lane geometry must have positive line length.")
                        reason = None
                    except ValueError as error:
                        expected, reason = None, str(error)
                    diagnostics = {
                        "reference_scope": "generated_model_after_prepared_source_revision",
                        "field_defect_label": None,
                        "field_validity_date": None,
                        "target_date_meaning": "model_revision_date_not_field_state",
                        "external_source_id": record["external_source_id"],
                        "way_id": record["way_id"],
                        "direction": direction,
                        "allowed_classes": list(allowed),
                        "permission_rank": slot[1],
                        "reference_lane_id": reference_id,
                        "candidate_ordinary_lane_ids": ordinary,
                        "candidate_internal_lane_count": len(selected) - len(ordinary),
                        "correspondence_limit": "Source-way, direction, permission set and ordinal only. No confirmed field-lane identity.",
                    }
                    if reason:
                        diagnostics["unresolved_reference"] = reason
                    fact = {
                        "id": f"source-revision:{record['way_id']}:{direction}:{reference_id}",
                        "kind": "lane_centerline",
                        "selector": {"lane_ids": sorted(selected)} if selected else {},
                        "expected": expected,
                        "source_ids": [source["id"] for source in sources],
                        "mapping_status": "unresolved",
                        "mapping_confidence": "medium" if selected else "none",
                        "mapping_method": "prepared_source_way_direction_permission_rank"
                        if selected
                        else "not_modeled_no_source_lane_role",
                        "traffic_mode": mode,
                        "reference_direction": 1,
                        "mapping_diagnostics": diagnostics,
                        "sample_spacing_m": sample_spacing_m,
                    }
                    control = deepcopy(fact)
                    control.update(
                        selector={"lane_id": reference_id},
                        mapping_confidence="medium",
                        mapping_method="generated_reference_object_self_control",
                    )
                    facts.append(fact)
                    controls.append(control)
                    correspondences.append(
                        {
                            "fact_id": fact["id"],
                            "way_id": record["way_id"],
                            "before_selector": fact["selector"],
                            "reference_selector": control["selector"],
                        }
                    )
        if len(facts) == start:
            fact = {
                "id": f"source-revision:{record['way_id']}:not-modeled",
                "kind": "lane_centerline",
                "selector": {},
                "expected": None,
                "source_ids": [source["id"] for source in sources],
                "mapping_status": "unresolved",
                "mapping_confidence": "none",
                "mapping_method": "not_modeled_reference_source_way_absent",
                "mapping_diagnostics": {"way_id": record["way_id"], "reference_scope": "generated_model_only"},
            }
            facts.append(fact)
            controls.append(deepcopy(fact))
    metadata = {
        "schema": "astra.network-facts/v1",
        "site_id": site_id,
        "target_date": model_date,
        "coordinate_system": system,
        "sources": sources,
    }
    tables = {
        name: NetworkFactTable.model_validate({**metadata, "facts": rows}).model_dump(mode="json", by_alias=True)
        for name, rows in (("before", facts), ("reference-self-control", controls))
    }
    output.mkdir(parents=True)
    outputs = {}
    for name, table in tables.items():
        path = output / f"{name}-facts.json"
        write_json_atomic(path, table, sort_keys=True)
        outputs[name] = {"path": str(path), "sha256": file_sha256(path)}
    mapping = output / "selector-correspondence.json"
    write_json_atomic(mapping, correspondences, sort_keys=True)
    changes = [str(path) for path, digest in hashes.items() if file_sha256(path) != digest]
    report = {
        "schema": "astra.prepared-source-fact-generation/v1",
        "status": "blocked" if changes else "pass",
        "claim_status": "model_reference_facts_created",
        "field_correctness": "not_established",
        "field_validity_date": None,
        "model_revision_date": model_date,
        "source_way_count": len(records),
        "fact_count": len(facts),
        "unresolved_before_selectors": sum(not row["selector"] for row in facts),
        "unknown_reference_facts": sum(row["expected"] is None for row in facts),
        "outputs": outputs,
        "selector_correspondence": {"path": str(mapping), "sha256": file_sha256(mapping)},
        "inputs": [{"path": str(path), "sha256": digest} for path, digest in hashes.items()],
        "input_changes": changes,
        "limitations": [
            "Input networks must share the prepared-source namespace used by the labels.",
            "Source-way, direction and permission-rank association is provisional. It does not establish field-lane identity.",
            "The reference self-control checks measurement consistency, not source quality or repair precision.",
        ],
    }
    path = output / "fact-generation.json"
    write_json_atomic(path, report, sort_keys=True)
    return {**report, "report_file": str(path)}
