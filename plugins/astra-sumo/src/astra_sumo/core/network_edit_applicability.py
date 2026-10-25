"""Check local edit domains independently of whole-intersection certification."""

from __future__ import annotations

from collections import defaultdict, deque
import json
from pathlib import Path
from xml.etree import ElementTree as ET

from shapely.geometry import Polygon

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .network_calibration import _permissions
from .source_movement_support import _index, _internal


AUTOMATIC_EDIT_PROFILES = ("suppress_turnaround", "set_junction_shape", "absorb_reciprocal_fragment")


def _pedestrian_facility_inventory(root):
    index = _index(root)
    result = {}
    for edge in root.findall("edge"):
        kind = edge.get("function")
        if kind not in {"crossing", "walkingarea"}:
            continue
        lanes = edge.findall("lane")
        structure = {"function": kind, "lanes": tuple((lane.get("id"), lane.get("index")) for lane in lanes)}
        if kind == "crossing":
            structure["crossed_edges"] = tuple(sorted(set(edge.get("crossingEdges", "").split())))
        else:
            structure["neighbors"] = tuple((lane.get("id"), tuple(sorted(index["incoming"].get(lane.get("id"), ()))),
                tuple(sorted({target for target, _ in index["outgoing"].get(lane.get("id"), ())}))) for lane in lanes)
        result[edge.get("id")] = structure
    return result


def _boundary_access(root, members, *, incoming_lane_ids=None, outgoing_lane_ids=None):
    """Trace every explicit vehicle-class path inside the declared nodes, without lane changes."""
    index = _index(root)
    incoming, outgoing = set(), set()
    interior = set()
    for lane, (edge_id, element, _) in index["lanes"].items():
        edge = index["edges"][edge_id]
        if _internal(edge):
            if index["owners"].get(lane) in members:
                interior.add(lane)
        elif edge.get("from") in members and edge.get("to") in members:
            interior.add(lane)
        else:
            if edge.get("to") in members:
                incoming.add(lane)
            if edge.get("from") in members:
                outgoing.add(lane)
    if incoming_lane_ids is not None:
        incoming = set(incoming_lane_ids)
    if outgoing_lane_ids is not None:
        outgoing = set(outgoing_lane_ids)
    for selected, endpoint in ((incoming if incoming_lane_ids is not None else (), "to"),
                               (outgoing if outgoing_lane_ids is not None else (), "from")):
        for lane in selected:
            edge = index["edges"][index["lanes"][lane][0]]
            if edge.get(endpoint) not in members and index["owners"].get(lane) not in members:
                raise ValueError("Selected movement lanes must enter or leave the declared junction scope.")
    allowed = {lane: _permissions(row[1], network_version=root.get("version", "0.0")) for lane, row in index["lanes"].items()}
    access = {}
    for start in sorted(incoming):
        reached = defaultdict(set)
        reached[start] = allowed[start]
        pending = deque([start])
        while pending:
            current = pending.popleft()
            for target, _connection in index["outgoing"].get(current, []):
                if target not in interior | outgoing:
                    continue
                # SUMO loads lane permissions; compiled connection directives do not restrict the path.
                modes = reached[current] & allowed[target]
                if modes - reached[target]:
                    reached[target].update(modes)
                    if target in interior:
                        pending.append(target)
        for target in sorted(outgoing):
            if reached[target]:
                access[(start, target)] = reached[target]
    return incoming, outgoing, access


def assess_edit_applicability(ledger, *, candidate_net_file=None, identity_control_report=None):
    """Assess a single accepted edit. A pass establishes technical applicability only."""
    from .corridor_edit_ledger import normalize_edit_operation, validate_edit_ledger, _is_structurally_protected_edge
    from .network_edit_operations import validate_scoped_operation, _shape

    source = Path(ledger["source_net_file"]).resolve(strict=True)
    digest = file_sha256(source)
    root = ET.parse(source).getroot()
    result = {"schema": "astra.network-edit-applicability/v1", "status": "blocked", "checks": {},
              "source": {"path": str(source), "sha256": digest}, "operation_count": len(ledger.get("operations", [])),
              "basis": "explicitly_accepted_source_bound_edit", "certification_claim": "not_established",
              "automatic_promotion": "requires_existing_candidate_gates", "field_correctness": "not_established",
              "limitations": ["No physical grouping or turn prohibition is inferred from the source network.",
                              "Explicit pedestrian connections do not cover implicit walking-area routing.",
                              "A local pass does not certify the whole junction or establish field correctness."]}
    if ledger.get("source_sha256") != digest:
        return {**result, "reason": "source_hash_mismatch"}
    if result["operation_count"] != 1:
        return {**result, "reason": "one_accepted_operation_required"}
    operation = normalize_edit_operation(ledger["operations"][0])
    kind, params = operation["operation"], operation["params"]
    result["operation"] = operation
    if kind not in AUTOMATIC_EDIT_PROFILES:
        return {**result, "status": "not_applicable", "reason": "edit_profile_not_implemented"}
    validation = validate_edit_ledger(ledger, root=root)
    if validation["status"] != "pass" or validate_scoped_operation(operation, root):
        return {**result, "reason": "invalid_operation", "validation": validation}
    if operation["status"] != "accepted":
        return {**result, "status": "review_required", "reason": "operation_not_explicitly_accepted"}
    evidence = []
    for row in operation["evidence"]:
        if not isinstance(row, dict) or not row.get("path") or not Path(row["path"]).is_absolute():
            return {**result, "reason": "absolute_file_evidence_required"}
        path = Path(row["path"]).resolve(strict=True)
        if file_sha256(path) != row.get("sha256"):
            return {**result, "reason": "evidence_hash_mismatch"}
        evidence.append({"path": str(path), "sha256": row["sha256"]})
    result["evidence"] = evidence
    index = _index(root)
    nodes = {row.get("id"): row for row in root.findall("junction")}
    if kind == "suppress_turnaround":
        members = {index["edges"][params["from"]].get("to")}
        connection = next(row for row in root.findall("connection") if all(
            row.get(key) == str(params[key]) for key in ("from", "fromLane", "to", "toLane")))
        if connection.get("tl"):
            return {**result, "status": "review_required", "reason": "controlled_turn_requires_signal_review"}
    else:
        members = set(params["node_ids"]) if kind == "absorb_reciprocal_fragment" else {params["node_id"]}
    scope_edges = {key for key, row in index["edges"].items() if not _internal(row)
                   and (row.get("from") in members or row.get("to") in members)}
    result["scope"] = {"source_junction_ids": sorted(members), "edge_ids": sorted(scope_edges)}
    protected = [key for key in scope_edges if _is_structurally_protected_edge(index["edges"][key])]
    if protected or any(nodes[node].get("type", "").startswith("rail_") for node in members):
        return {**result, "status": "review_required", "reason": "local_grade_or_rail_semantics", "protected_edge_ids": protected}
    if kind == "set_junction_shape":
        polygon = Polygon([tuple(map(float, token.split(",")[:2])) for token in nodes[params["node_id"]].get("shape", "").split()])
        target = Polygon([tuple(map(float, point.split(","))) for point in _shape(params["shape"]).split()])
        if not polygon.is_valid or polygon.area <= 0 or not polygon.covers(target) or polygon.equals(target):
            return {**result, "status": "review_required", "reason": "shape_is_not_a_strict_tightening"}
    if kind == "absorb_reciprocal_fragment":
        if params.get("signal_policy", "preserve") != "preserve" or params.get("interior_lane_change_policy", "fixed_paths") != "fixed_paths":
            return {**result, "status": "review_required", "reason": "diagnostic_control_or_lane_changes_require_review"}
        pedestrian = [key for key, edge in index["edges"].items() if edge.get("function") in {"crossing", "walkingarea"}
                      and any(index["owners"].get(row.get("id")) in members for row in edge.findall("lane"))]
        pedestrian.extend(key for key in scope_edges if any("pedestrian" in _permissions(row, network_version=root.get("version", "0.0"))
                          for row in index["edges"][key].findall("lane")))
        if pedestrian:
            return {**result, "status": "review_required", "reason": "local_pedestrian_join_requires_walking_route_evidence"}
    if kind == "suppress_turnaround":
        _, _, paths = _boundary_access(root, members)
        start = index["keys"][(params["from"], params["fromLane"])]
        target_lane = index["keys"][(params["to"], params["toLane"])]
        removed = paths.get((start, target_lane), set())
        remaining = set().union(*(modes for (lane, target), modes in paths.items() if lane == start and target != target_lane))
        if not removed or not removed <= remaining:
            return {**result, "status": "review_required", "reason": "turn_removal_strands_a_vehicle_class"}
    result["checks"]["local_preconditions"] = "pass"
    if candidate_net_file is None:
        return {**result, "status": "pass", "stage": "preconditions_only"}

    from .connection_mode_audit import audit_network_connection_mode
    from .corridor_edit_ledger import _compare_tls_logic_semantics, _protected_semantic_counts
    from .network_geometry_freeze import compare_frozen_geometry
    from .junction_boundary_rebuild import _outside_delta, _signature

    candidate = Path(candidate_net_file).resolve(strict=True)
    candidate_digest = file_sha256(candidate)
    after = ET.parse(candidate).getroot()
    result["candidate"] = {"path": str(candidate), "sha256": candidate_digest}
    candidate_members = {params["join_id"]} if kind == "absorb_reciprocal_fragment" else members
    old_in, old_out, old_paths = _boundary_access(root, members)
    new_in, new_out, new_paths = _boundary_access(after, candidate_members)
    expected = dict(old_paths)
    if kind == "suppress_turnaround":
        start = index["keys"][(params["from"], params["fromLane"])]
        target_lane = index["keys"][(params["to"], params["toLane"])]
        removed = expected.pop((start, target_lane), set())
        remaining = set().union(*(modes for (lane, _), modes in expected.items() if lane == start))
        if not removed or not removed <= remaining:
            return {**result, "status": "review_required", "reason": "turn_removal_strands_a_vehicle_class"}
    differences = [{"from_lane": pair[0], "to_lane": pair[1], "expected": sorted(expected.get(pair, ())),
                    "actual": sorted(new_paths.get(pair, ()))} for pair in sorted(expected.keys() | new_paths.keys())
                   if expected.get(pair, set()) != new_paths.get(pair, set())]
    result["boundary_paths"] = {"source_pair_count": len(old_paths), "candidate_pair_count": len(new_paths),
                                "audited_vehicle_classes": sorted(set().union(*old_paths.values(), *new_paths.values())),
                                "differences": differences}
    checks = result["checks"]
    checks["boundary_lane_identities"] = "pass" if old_in == new_in and old_out == new_out else "blocked"
    checks["all_class_explicit_boundary_paths"] = "blocked" if differences else "pass"
    geometry = compare_frozen_geometry(source_file=source, candidate_file=candidate,
        mutable_junction_ids=frozenset(members | candidate_members), mutable_edge_ids=frozenset(scope_edges))
    result["geometry_preservation"] = geometry
    checks["outside_geometry_and_fixed_ends"] = geometry["status"]
    outside = _outside_delta(root, after, members | candidate_members)
    result["outside_state"] = outside
    checks["outside_state"] = outside["status"]
    checks["protected_counts"] = "pass" if _protected_semantic_counts(root) == _protected_semantic_counts(after) else "blocked"
    checks["signal_programs"] = _compare_tls_logic_semantics(root, after)["status"]
    structure = audit_network_connection_mode(after, endpoint_tolerance_m=0.1)
    checks["connection_structure"] = "pass" if structure["structural_failure_count"] == 0 else "blocked"
    def facilities(net):
        return {row.get("id"): _signature(row) for row in net.findall("edge")
                if row.get("function") in {"crossing", "walkingarea"}}
    checks["pedestrian_facilities"] = "pass" if facilities(root) == facilities(after) else "blocked"
    old_lanes = {lane.get("id"): lane for edge in root.findall("edge") if edge.get("id") in scope_edges
                 for lane in edge.findall("lane")}
    new_lanes = {lane.get("id"): lane for edge in after.findall("edge") for lane in edge.findall("lane")}
    removed_lanes = {lane.get("id") for edge in root.findall("edge") if kind == "absorb_reciprocal_fragment"
                     and edge.get("from") in members and edge.get("to") in members for lane in edge.findall("lane")}
    checks["boundary_lane_permissions_width_speed"] = "pass" if all(
        key in new_lanes and _permissions(lane, network_version=root.get("version", "0.0")) ==
            _permissions(new_lanes[key], network_version=after.get("version", "0.0")) and all(
            (float(lane.get(attr)) if lane.get(attr) is not None else None) ==
            (float(new_lanes[key].get(attr)) if new_lanes[key].get(attr) is not None else None)
            for attr in ("width", "speed")) for key, lane in old_lanes.items() if key not in removed_lanes) else "blocked"
    checks["pedestrian_lane_geometry"] = "pass" if all(key in new_lanes and all(
        lane.get(attr) == new_lanes[key].get(attr) for attr in ("shape", "length"))
        for key, lane in old_lanes.items() if "pedestrian" in _permissions(lane, network_version=root.get("version", "0.0"))) else "blocked"
    if kind == "set_junction_shape":
        actual = after.find(f"junction[@id='{params['node_id']}']")
        shape = Polygon([tuple(map(float, token.split(",")[:2])) for token in actual.get("shape", "").split()])
        requested = Polygon([tuple(map(float, point.split(","))) for point in _shape(params["shape"]).split()])
        checks["requested_shape"] = "pass" if shape.equals(requested) else "blocked"
        from .junction_contour_control import assess_identity_control, assess_requested_geometry, geometry_delta, walking_area_quality
        control = assess_identity_control(source, root, params["node_id"], identity_control_report)
        result["contour_identity_control"] = control
        checks["native_contour_identity_control"] = control["status"]
        result["contour_geometry_changes"] = {"source_to_candidate": geometry_delta(root, after, params["node_id"]),
                                               "control_to_candidate": None}
        if control.get("geometry") is not None and control.get("candidate"):
            try:
                control_root = ET.parse(control["candidate"]["path"]).getroot()
                effect = assess_requested_geometry(control_root, after, params["node_id"])
                result["requested_contour_geometry"] = effect
                result["contour_geometry_changes"]["control_to_candidate"] = effect["geometry"]
                checks["requested_contour_geometry"] = effect["status"]
                if effect["status"] != "pass":
                    result["reason"] = effect["reason"]
                if control["status"] == "pass":
                    result["pedestrian_geometry_reference"] = "validated_native_identity_control"
                    checks["pedestrian_facilities"] = "pass" if facilities(control_root) == facilities(after) else "review_required"
                    control_lanes = {lane.get("id"): lane for edge in control_root.findall("edge")
                                     if edge.get("id") in scope_edges for lane in edge.findall("lane")}
                    def geometry_signature(lane):
                        return _signature(ET.Element("lane", {key: lane.get(key) for key in ("shape", "length") if lane.get(key) is not None}))
                    checks["pedestrian_lane_geometry"] = "pass" if all(key in new_lanes and
                        geometry_signature(lane) == geometry_signature(new_lanes[key]) for key, lane in control_lanes.items()
                        if "pedestrian" in _permissions(lane, network_version=control_root.get("version", "0.0"))) else "review_required"
                    if effect["status"] == "pass" and any(checks[key] == "review_required" for key in
                            ("pedestrian_facilities", "pedestrian_lane_geometry")):
                        result["reason"] = "pedestrian_layout_change_requires_review"
            except (OSError, ValueError, ET.ParseError) as error:
                checks["native_contour_identity_control"] = "review_required"
                control.update(status="review_required", reason="contour_identity_control_unavailable", error=str(error))
        if control["status"] != "pass":
            result["reason"] = control["reason"]
            result["unqualified_source_pedestrian_checks"] = {
                "status": "not_applicable", "reason": "native_identity_control_not_qualified",
                "source_comparison": {key: checks[key] for key in ("pedestrian_facilities", "pedestrian_lane_geometry")}}
            for key in ("pedestrian_facilities", "pedestrian_lane_geometry"):
                if checks[key] == "blocked":
                    checks[key] = "review_required"
        inventories = [_pedestrian_facility_inventory(net) for net in (root, after)]
        result["pedestrian_structure_reference"] = "immutable_source"
        result["pedestrian_facility_changes"] = {
            "missing_ids": sorted(inventories[0].keys() - inventories[1].keys()),
            "added_ids": sorted(inventories[1].keys() - inventories[0].keys()),
            "changed_structure_ids": sorted(key for key in inventories[0].keys() & inventories[1].keys()
                                            if inventories[0][key] != inventories[1][key])}
        if inventories[0] != inventories[1]:
            checks["pedestrian_facilities"] = "blocked"
            result["reason"] = "pedestrian_facility_structure_changed"
        quality = walking_area_quality(root, after, params["node_id"])
        result["requested_walking_area_quality"] = quality
        result["walking_area_validity_reference"] = "immutable_source"
        checks["walking_area_validity"] = "blocked" if quality["newly_invalid_area_ids"] else "pass"
        if quality["newly_invalid_area_ids"]:
            result["reason"] = "candidate_newly_invalid_walking_areas"
    from ..corridor.canonicalizer import canonicalize_net_xml_file
    from ..corridor.conflict_graph import audit_independent_movement_safety
    from ..corridor.enums import TrafficSide

    def hard_findings(path, net):
        side = TrafficSide.LEFT if net.get("lefthand", "false").lower() in {"true", "1", "yes"} else TrafficSide.RIGHT
        snapshot = canonicalize_net_xml_file(path, traffic_side=side)
        safety = audit_independent_movement_safety(snapshot)
        return {row.witness_signature for row in safety.findings if row.severity.value == "safety"}

    new_hard = sorted(hard_findings(candidate, after) - hard_findings(source, root))
    result["new_independent_safety_witnesses"] = new_hard
    checks["no_new_independent_safety_findings"] = "blocked" if new_hard else "pass"
    result["stage"] = "candidate_checked"
    checks["inputs_unchanged"] = "pass" if (file_sha256(source) == digest and file_sha256(candidate) == candidate_digest
        and all(file_sha256(Path(row["path"])) == row["sha256"] for row in evidence)) else "blocked"
    if kind == "set_junction_shape":
        for name in ("candidate", "materialization_report"):
            identity = result["contour_identity_control"].get(name)
            if not identity or not identity.get("sha256"):
                continue
            try:
                if file_sha256(Path(identity["path"])) != identity["sha256"]:
                    checks["inputs_unchanged"] = "blocked"
            except OSError as error:
                checks["inputs_unchanged"] = "blocked"
                result["contour_identity_control"]["input_error"] = str(error)
    status = "pass" if all(value == "pass" for value in checks.values()) else (
        "review_required" if all(value in {"pass", "review_required"} for value in checks.values()) else "blocked")
    return {**result, "status": status}


def verify_network_edit(*, ledger_file, output_dir, netconvert_binary="netconvert", mode="explore", facts_file=None,
                        routeability_seeds=(42,), lane_artifact_files=None):
    """Explore a typed road edit, or apply the existing release checks explicitly."""
    from .corridor_edit_ledger import materialize_corridor_edit_variant, run_corridor_candidate_gates
    from .network_edit_operations import _sumo_binary
    from .routeability_audit import run_routeability_audit

    if mode == "explore":
        from .network_edit_operations import explore_network_edit
        return explore_network_edit(ledger_file=ledger_file, output_dir=output_dir, netconvert_binary=netconvert_binary,
                                    facts_file=facts_file, routeability_seeds=routeability_seeds,
                                    lane_artifact_files=lane_artifact_files)
    if facts_file is not None:
        raise ValueError("Use facts_file during exploration. Release calibration is a separate final check.")
    if tuple(routeability_seeds) != (42,) or lane_artifact_files is not None:
        raise ValueError("Use routeability_seeds and lane_artifact_files during exploration. "
                         "Release runs the candidate gates unchanged.")
    if mode != "release":
        raise ValueError("Mode must be explore or release.")

    ledger_path, destination = Path(ledger_file).resolve(strict=True), Path(output_dir).resolve()
    ledger_hash = file_sha256(ledger_path)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    source = Path(ledger["source_net_file"]).resolve(strict=True)
    inputs = [ledger_path, source, *[Path(row["path"]).resolve() for operation in ledger.get("operations", [])
                                    for row in operation.get("evidence", []) if isinstance(row, dict) and row.get("path")]]
    if destination.exists() or any(path.is_relative_to(destination) for path in inputs):
        raise ValueError("Use a new output directory separate from every input")
    hashes = {path: file_sha256(path) for path in inputs}
    automatic_profile = (len(ledger.get("operations", [])) == 1
                         and ledger["operations"][0].get("operation") in AUTOMATIC_EDIT_PROFILES)
    preflight = assess_edit_applicability(ledger) if automatic_profile else {
        "status": "pass", "reason": "explicit_release_materialization_without_automatic_profile"}
    destination.mkdir(parents=True)
    report = {"schema": "astra.network-edit-verification/v1", "status": preflight["status"],
              "whole_network_review": {"status": "not_run"},
              "preconditions": preflight, "field_correctness": "not_established", "certification_claim": "not_established",
              "ledger": {"path": str(ledger_path), "sha256": ledger_hash}}
    if preflight["status"] == "pass":
        built = materialize_corridor_edit_variant(output_dir=destination / "candidate", ledger_file=ledger_path,
                                                  netconvert_binary=netconvert_binary)
        report["materialization"] = built
        report["status"] = "review_required" if built["status"] == "review_required" else "blocked"
        if built["status"] in {"pass", "review_required"}:
            sumo_binary = _sumo_binary(netconvert_binary)

            def routeability(**kwargs):
                duarouter = str(Path(sumo_binary).with_name("duarouter" + Path(sumo_binary).suffix))
                return run_routeability_audit(**kwargs, od_reference_net_file=source,
                                             binaries={"sumo": sumo_binary, "duarouter": duarouter})

            gates = run_corridor_candidate_gates(source_net_file=source,
                candidate_net_file=Path(built["candidate_net_file"]), output_dir=destination / "gates",
                materialization_report=built, sumo_binary=sumo_binary, routeability_audit_func=routeability,
                edit_ledger_file=ledger_path if automatic_profile else None, netconvert_binary=netconvert_binary)
            report["candidate_gates"] = gates
            report["whole_network_review"] = gates["whole_network_review"]
            report["applicability"] = gates["gates"].get("edit_applicability", {"status": "not_applicable"})
            report["status"] = gates["status"]
    report["inputs_unchanged"] = all(file_sha256(path) == digest for path, digest in hashes.items())
    if not report["inputs_unchanged"]:
        report["status"] = "blocked"
    report["inputs"] = [{"path": str(path), "sha256": digest} for path, digest in hashes.items()]
    path = destination / "edit-verification.json"
    write_json_atomic(path, report, sort_keys=True)
    write_json_atomic(destination / "manifest.json", {"schema": "astra.network-edit-verification-manifest/v1",
        "inputs": report["inputs"], "report": {"path": str(path), "sha256": file_sha256(path)}}, sort_keys=True)
    return {**report, "report_file": str(path), "manifest_file": str(destination / "manifest.json")}
