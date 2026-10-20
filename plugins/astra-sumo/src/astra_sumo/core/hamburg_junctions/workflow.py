"""Rebuild official junctions and field loops from saved source inputs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ..artifact_io import write_json_atomic
from ..cached_detector_demand import read_hamburg_count_stream_snapshot
from ..candidate_contracts import file_sha256
from ..command_runner import run_command
from ..connection_mode_audit import audit_network_connection_mode
from ..detector_demand import active_detectors, read_detector_mapping, read_net_lanes, write_e1_additional
from ..hamburg_aerial_approach import build_hamburg_aerial_approach_candidate
from ..hamburg_aerial_movement import build_hamburg_official_movement_plan
from ..hamburg_corridor_candidate import bind_hamburg_corridor_tls_clusters
from ..hamburg_named_detector_bindings import materialize_hamburg_named_detector_bindings
from ..hamburg_official import parse_hamburg_count_streams
from ..local_network_conversion import loaded_turn_permissions
from ..movement_routeability import run_candidate_movement_probes
from ..network_edit_operations import check_scoped_edit, write_scoped_edit_plan
from ..osm_network import audit_tls
from .groups import _compile_official_part_joins, _select_join_groups
from .lanes import _approach_lane_identities
from .movements import _write_movement_candidate
from .source import _project_plan_to_network


def _identity(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": file_sha256(path)}


def _apply_edit(source, operation, output, netconvert, timeout):
    output.mkdir()
    candidate = output / "candidate.net.xml"
    before = _identity(source)
    plan = write_scoped_edit_plan(root=ET.parse(source).getroot(), operation=operation,
        source_file=source, candidate_file=candidate, output_dir=output, prefix="edit",
        netconvert_binary=netconvert, mode="explore")
    command = run_command(plan["command"], cwd=output, timeout_seconds=timeout)
    write_json_atomic(output / "command.json", command.to_dict())
    if command.returncode or not candidate.is_file():
        raise ValueError("Native construction did not produce the typed edit.")
    checked = check_scoped_edit(plan=plan, candidate_file=candidate, output_dir=output,
        netconvert_binary=netconvert, command_runner=run_command, timeout_seconds=timeout, mode="explore")
    write_json_atomic(output / "verification.json", checked)
    if _identity(source) != before or checked["sumo_load"]["status"] != "pass" or checked["driving_integrity"]["status"] != "pass":
        raise ValueError("The edit changed its source or produced an invalid driving network.")
    return candidate, plan, checked


def _remap_lane_origins(before, after, plan, origins, constructed):
    edge_id = plan["operation"]["params"]["edge_id"]
    old = ET.parse(before).getroot().find(f"edge[@id='{edge_id}']").findall("lane")
    new = ET.parse(after).getroot().find(f"edge[@id='{edge_id}']").findall("lane")
    mapping = plan["lane_index_mapping"]
    previous = {str(index): origins.get(lane.get("id"), lane.get("id")) for index, lane in enumerate(old)}
    for lane in old:
        origins.pop(lane.get("id"), None)
    reverse = {index: previous[old_index] for old_index, index in mapping.items() if index is not None}
    origins.update({lane.get("id"): reverse.get(index) for index, lane in enumerate(new)})
    for bindings in constructed.values():
        for lane_id, pair in list(bindings.items()):
            if pair[0] == edge_id:
                index = mapping.get(str(pair[1]))
                if index is None:
                    bindings.pop(lane_id)
                else:
                    bindings[lane_id] = (edge_id, index)
    return edge_id


def rebuild_hamburg_official_corridor(
    *, source_net: Path, official_sources: Path, lsa_identity: Path, lane_count_edits: Path,
    count_fields: Path, count_stations: Path, output_dir: Path,
    netconvert_binary: str = "netconvert", sumo_binary: str = "sumo", timeout_seconds: float = 240,
) -> dict:
    """Measure construction and driving without requiring complete correspondence."""
    source = Path(source_net).resolve(strict=True)
    sources_path = Path(official_sources).resolve(strict=True)
    lsa_path = Path(lsa_identity).resolve(strict=True)
    edits_path = Path(lane_count_edits).resolve(strict=True)
    fields_path, stations_path = Path(count_fields).resolve(strict=True), Path(count_stations).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Use a new corridor output directory.")
    supplied = json.loads(sources_path.read_text(encoding="utf-8"))
    paths = supplied["paths"]
    if not paths or any(not str(node).isdecimal() for node in paths) or len({int(node) for node in paths}) != len(paths):
        raise ValueError("Official sources require distinct numeric node identities.")
    nodes = sorted(paths, key=int)
    resolved = {}
    for node in nodes:
        resolved[node] = {}
        for role in ("map_xml", "map_kml"):
            path = Path(paths[node][role]).expanduser()
            resolved[node][role] = path.resolve(strict=True) if path.is_absolute() else (sources_path.parent / path).resolve(strict=True)
    inputs = [_identity(path) for path in (source, sources_path, lsa_path, edits_path, fields_path, stations_path)]
    inputs.extend(_identity(path) for row in resolved.values() for path in row.values())
    edits = json.loads(edits_path.read_text(encoding="utf-8"))["operations"]
    if not isinstance(edits, list) or any(not isinstance(row, dict) or row.get("operation") != "set_lane_count" for row in edits):
        raise ValueError("The corridor lane-count edits must contain typed set_lane_count operations.")
    output.mkdir(parents=True)
    report_file = output / "run.json"
    implementation = [Path(__file__), Path(__file__).with_name("groups.py"), Path(__file__).with_name("movements.py"),
                      Path(__file__).parents[1] / "network_edit_operations.py",
                      Path(__file__).parents[1] / "local_network_conversion.py"]
    report = {"schema": "astra.hamburg-official-corridor-replay/v1", "status": "running", "mode": "explore",
              "promotion_authority": False, "inputs": inputs, "stages": {}, "warnings": [],
              "current_stage": "official_plans", "report_file": str(report_file),
              "implementation": [_identity(path) for path in implementation],
              "parameters": {"seed": 104, "netconvert_binary": netconvert_binary, "sumo_binary": sumo_binary,
                             "timeout_seconds": timeout_seconds, "field_period_s": 900},
              "calibration": {"status": "not_run"},
              "claim_boundary": "Official geometry, partial movement driving and published-point field-loop layout. No field timing, calibrated demand, count fit or independent geometry validation."}

    def save():
        write_json_atomic(report_file, report)

    def stage(name, value):
        path = output / f"{name}.json"
        write_json_atomic(path, value)
        report["stages"][name] = _identity(path)
        report["current_stage"] = name
        save()

    save()
    try:
        plans = {}
        for node in nodes:
            plan = build_hamburg_official_movement_plan(node_id=node,
                map_xml_file=resolved[node]["map_xml"], map_kml_file=resolved[node]["map_kml"],
                expected_sha256={role: file_sha256(path) for role, path in resolved[node].items()},
                output_file=output / "plans" / f"{node}.json")
            plans[node] = _project_plan_to_network(plan, ET.parse(source).getroot())
        stage("official-plans", {node: _identity(output / "plans" / f"{node}.json") for node in nodes})
        report["current_stage"] = "physical_groups"
        save()
        tls = audit_tls(net_file=source, output_dir=output / "tls-audit", google_maps_temporal_scope="current")
        selection = output / "selection.json"
        write_json_atomic(selection, {"schema": "torii.hamburg-five-corridor-selection/v1",
            "selection_mode": "topology_only", "ordered_node_ids": nodes})
        clusters = Path(tls["clusters_file"])
        binding = bind_hamburg_corridor_tls_clusters(selection_file=selection,
            expected_selection_sha256=file_sha256(selection), lsa_identity_file=lsa_path,
            expected_lsa_identity_sha256=file_sha256(lsa_path), tls_clusters_file=clusters,
            expected_tls_clusters_sha256=file_sha256(clusters), net_file=source,
            expected_net_sha256=file_sha256(source), output_file=output / "cluster-binding.json")
        groups = _select_join_groups(ET.parse(source).getroot(), binding, plans)
        stage("physical-groups", {"source": _identity(source), "groups": groups})
        report["current_stage"] = "physical_joins"
        save()
        joined, command, conversion, boundary = _compile_official_part_joins(source_net=source, groups=groups,
            output_dir=output / "joined", netconvert_binary=netconvert_binary, timeout_seconds=timeout_seconds, seed=104)
        stage("joined-parts", {"command": command.to_dict(), "local_conversion": conversion,
            "boundary_rebuild": boundary, "candidate": _identity(joined)})
        report["current_stage"] = "approach_sections"
        save()
        approach = build_hamburg_aerial_approach_candidate(source_net=joined, plans=plans,
            junction_bindings={(row["node_id"], row["intersection_part"]): row["join_id"] for row in groups},
            output_dir=output / "approaches", netconvert_binary=netconvert_binary, timeout_seconds=timeout_seconds)
        stage("approaches", approach)
        current = Path(approach["candidate_network"]["path"]) if approach.get("candidate_network") else joined
        constructed, origins, changed = _approach_lane_identities(ET.parse(source).getroot(), approach, groups)
        changed = set(changed)

        report["current_stage"] = "empty_loaded_turns"
        save()
        permissions = loaded_turn_permissions(ET.parse(current).getroot())
        empty = sorted(key for key, classes in permissions.items() if not classes)
        cleanup = []
        for index, pair in enumerate(empty):
            operation = {"id": f"remove-empty-loaded-turn-{index}", "operation": "delete_lane_connection",
                "params": dict(zip(("from", "fromLane", "to", "toLane"), pair)),
                "rationale": "The compiled source movement has no traversable vehicle class."}
            edited, _, checked = _apply_edit(current, operation, output / f"empty-turn-{index}", netconvert_binary, timeout_seconds)
            after = loaded_turn_permissions(ET.parse(edited).getroot())
            expected = {key: classes for key, classes in permissions.items() if key != pair}
            if after != expected:
                raise ValueError("Empty-turn cleanup changed other loaded movement permissions.")
            cleanup.append({"operation": operation, "verification": _identity(output / f"empty-turn-{index}" / "verification.json"),
                            "retained_effective_vehicle_classes_exact": True, "status": checked["status"]})
            current, permissions = edited, after
        stage("empty-loaded-turns", {"deleted_count": len(cleanup), "edits": cleanup, "candidate": _identity(current)})

        edit_records = []
        report["current_stage"] = "lane_edits"
        save()
        for index, operation in enumerate(edits):
            before = current
            current, plan, checked = _apply_edit(before, operation, output / f"lane-edit-{index}", netconvert_binary, timeout_seconds)
            changed.add(_remap_lane_origins(before, current, plan, origins, constructed))
            edit_records.append({"operation": operation, "status": checked["status"],
                                 "verification": _identity(output / f"lane-edit-{index}" / "verification.json")})
            report["warnings"].extend(checked.get("warnings", []))
        stage("lane-edits", {"edits": edit_records, "candidate": _identity(current)})
        report["current_stage"] = "official_movements"
        save()
        final = output / "official.net.xml"
        materialized = _write_movement_candidate(joined_net=current, plans=plans, groups=groups, skipped_join_ids=set(),
            output_file=final, maximum_anchor_projection_error_m=10, maximum_lane_projection_error_m=10,
            minimum_lane_match_margin_m=.5, original_net=source, constructed_ingress=constructed, lane_origins=origins,
            identity_changed_edges=sorted(changed), netconvert_binary=netconvert_binary, sumo_binary=sumo_binary,
            timeout_seconds=timeout_seconds, junction_contours="preserve")
        audit = materialized["official_connection_audit"]
        audit["ambiguous"].extend(row for plan in plans.values() for row in plan["unusable_vehicle_movements"])
        stage("official-movements", materialized)
        structure = audit_network_connection_mode(ET.parse(final).getroot(), endpoint_tolerance_m=.1)
        stage("connection-structure", structure)
        if structure["structural_failure_count"]:
            raise ValueError("The constructed network has broken internal driving paths.")
        count = sum(plan["source_vehicle_movement_count"] for plan in plans.values())
        manifest = output / "candidate-manifest.json"
        write_json_atomic(manifest, {"schema": "torii.hamburg-aerial-corridor-candidate/v1", "status": "experimental_unaccepted",
            "promotion_authority": False, "artifacts": {"network": _identity(final)},
            "counts": {"official_vehicle_movements": count}, "official_connection_audit": audit})
        report["current_stage"] = "movement_driving"
        save()
        probes = run_candidate_movement_probes(candidate_manifest=manifest, output_dir=output / "movement-probes", sumo_binary=sumo_binary)
        stage("movement-driving", {key: probes[key] for key in ("status", "official_total", "mapped_and_tested",
            "passed_exact_lane_transition", "unmapped_not_tested", "composed_boundary_path_review_count", "source_immutable")})
        report["network"] = _identity(final)
        report["movement_coverage"] = {"official": count, "passed": probes["passed_exact_lane_transition"],
            "unmapped": probes["unmapped_not_tested"], "unexplained_extra_connections": audit["unexplained_extra"]}
        report["current_stage"] = "field_layout"
        save()
        field_binding = materialize_hamburg_named_detector_bindings(net_file=final, count_stream_file=fields_path,
            output_dir=output / "field-bindings", map_files=tuple(resolved[node]["map_xml"] for node in nodes),
            map_kml_files=tuple(resolved[node]["map_kml"] for node in nodes))
        stage("field-binding", field_binding)
        mapping = Path(field_binding["artifacts"]["detector_mapping"]["path"])
        active = active_detectors(read_detector_mapping(mapping))
        loops = output / "field-loops.add.xml"
        ids = write_e1_additional(loops, active, lanes=read_net_lanes(final), period=900, output_file="field-counts.xml")
        rows = list(csv.DictReader(mapping.open(encoding="utf-8")))
        by_stream = {int(row["stream_id"]): ids[row["detector_id"]] for row in rows if row["detector_id"] in ids}
        fields = read_hamburg_count_stream_snapshot(fields_path)
        by_key = {(str(int(field.node_id)), field.asset_id): field.stream_id for field in fields}
        stations = parse_hamburg_count_streams(json.loads(stations_path.read_text(encoding="utf-8")))
        memberships = []
        for station in stations:
            if station.direction_code not in {"1", "2"}:
                continue
            members, missing = [], []
            for key in station.composition:
                field = by_key.get((str(int(key.partition("-")[0])), key.partition("-")[2]))
                if field is None or field not in by_stream:
                    missing.append(key)
                else:
                    members.append(field)
            memberships.append({"station_stream_id": station.stream_id, "composition": list(station.composition),
                "member_stream_ids": members, "detector_ids": [by_stream[field] for field in members], "missing_fields": missing})
        load = run_command([sumo_binary, "--net-file", str(final), "--additional-files", str(loops),
            "--begin", "0", "--end", "1", "--no-step-log", "true"], cwd=output, timeout_seconds=timeout_seconds)
        stage("field-layout", {"detector_count": len(ids), "additional": _identity(loops), "period_s": 900,
            "count_attribute": "nVehContrib", "station_groups": memberships, "load": load.to_dict(),
            "placement_assumption": "Project published field points onto their associated lanes. Surveyed cross-sections remain unverified.",
            "claim_boundary": "One-second layout load with no traffic demand. No count fitting or experiment-window selection."})
        if load.returncode:
            raise ValueError("SUMO cannot load the field-loop layout.")
        report["topology_complete"] = probes["passed_exact_lane_transition"] == count
        if not report["topology_complete"]:
            report["warnings"].append(f"{count - probes['passed_exact_lane_transition']} official motor movements lack driving proof.")
        if audit["unexplained_extra"]:
            report["warnings"].append(f"{len(audit['unexplained_extra'])} supplementary motor connections lack verified source support.")
        if len(active) != field_binding["stream_count"]:
            report["warnings"].append("The field-loop layout omits inactive field associations. Missing fields are not observed zeros.")
        report["status"] = "measured"
        report["current_stage"] = "measured"
    except Exception as error:
        report.update(status="invalid", error=f"{type(error).__name__}: {error}")
    report["inputs_unchanged"] = all(Path(row["path"]).is_file() and file_sha256(Path(row["path"])) == row["sha256"] for row in inputs)
    report["implementation_unchanged"] = all(file_sha256(Path(row["path"])) == row["sha256"] for row in report["implementation"])
    save()
    return report
