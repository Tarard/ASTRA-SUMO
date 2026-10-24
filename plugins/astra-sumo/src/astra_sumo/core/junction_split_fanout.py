"""Compile explicitly selected split movements as complete internal paths."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .local_network_conversion import loaded_turn_permissions, run_scoped_native_conversion
from .network_geometry_freeze import prepare_geometry_frozen_input
from .source_movement_support import _candidate_modes, _index


def _key(row):
    return row.get("from"), int(row.get("fromLane")), row.get("to"), int(row.get("toLane"))


def _canonical(row):
    return ET.canonicalize(ET.tostring(row, encoding="unicode"), strip_text=True)


def _internal_path(root, row):
    index = _index(root)
    key = _key(row)
    _, path = _candidate_modes(index, index["keys"][key[:2]], index["keys"][key[2:]], row)
    if len(path) < 3:
        raise ValueError("A selected movement has no complete internal path.")
    return [index["lanes"][identity][1] for identity in path[1:-1]]


def _curve(root, row):
    return LineString([tuple(map(float, point.split(",")[:2])) for lane in _internal_path(root, row)
                       for point in lane.get("shape", "").split()])


def materialize_unsplit_connection(*, source_file, junction_id, connection_key, output_dir,
                                   netconvert_binary="netconvert"):
    """Compile one split movement while protecting all junction curves and exterior elements."""
    source, out = Path(source_file).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or source.is_relative_to(out):
        raise ValueError("Use a new output directory separate from the source.")
    selected_key = tuple(connection_key)
    if (len(selected_key) != 4 or any(not isinstance(selected_key[i], str) or not selected_key[i] for i in (0, 2))
            or any(type(selected_key[i]) is not int or selected_key[i] < 0 for i in (1, 3))):
        raise ValueError("Select one movement with road IDs and nonnegative integer lane indices.")
    before = ET.parse(source).getroot()
    ordinary = {row.get("id"): row for row in before.findall("edge") if not row.get("function")}
    connections = {_key(row): row for row in before.findall("connection") if row.get("from") in ordinary}
    selected = connections.get(selected_key)
    if selected is None or selected_key[2] not in ordinary:
        raise ValueError("Select an existing movement.")
    if (ordinary[selected_key[0]].get("to") != junction_id
            or ordinary[selected_key[2]].get("from") != junction_id):
        raise ValueError("Select a movement at the declared junction.")
    source_path = _internal_path(before, selected)
    if len(source_path) < 2 or not selected.get("shape"):
        raise ValueError("Select a split internal movement with a complete saved curve.")
    patch = ET.Element("connections")
    attrs = {name: value for name, value in selected.attrib.items() if name not in {"via", "dir", "state"}}
    ET.SubElement(patch, "connection", {**attrs, "contPos": "0"})
    report = _compile_connection_patch(source, junction_id, patch, out, netconvert_binary)
    after = ET.parse(report["candidate_file"]).getroot()
    new_connections = {_key(row): row for row in after.findall("connection") if row.get("from") in ordinary}
    candidate_path = _internal_path(after, new_connections[selected_key])
    geometry = []
    for key, row in connections.items():
        if ordinary[key[0]].get("to") != junction_id:
            continue
        new_row = new_connections[key]
        old_curve, new_curve = _curve(before, row), _curve(after, new_row)
        old_length = sum(float(lane.get("length")) for lane in _internal_path(before, row))
        new_length = sum(float(lane.get("length")) for lane in _internal_path(after, new_row))
        geometry.append({"connection_key": key, "selected": key == selected_key,
            "curve_offset_m": old_curve.hausdorff_distance(new_curve),
            "ordered_vertices_preserved": old_curve.equals_exact(new_curve, 1e-6),
            "endpoints_identical": old_curve.coords[0] == new_curve.coords[0] and old_curve.coords[-1] == new_curve.coords[-1],
            "logical_length_delta_m": new_length - old_length,
            "saved_curve_identical": row.get("shape") == new_row.get("shape")})

    def exterior(root):
        protected = []
        for row in root:
            identity, first = row.get("id", ""), row.get("from", "")
            if row.tag == "junction" and (identity == junction_id or identity.startswith(f":{junction_id}_")):
                continue
            if row.tag == "edge" and identity.startswith(f":{junction_id}_"):
                continue
            if row.tag == "connection" and (first.startswith(f":{junction_id}_")
                    or first in ordinary and ordinary[first].get("to") == junction_id):
                continue
            protected.append(_canonical(row))
        return Counter(protected)

    outside_unchanged = exterior(before) == exterior(after)
    checks = {"selected_path_unsplit": len(candidate_path) == 1,
        "all_complete_curves_preserved": all(row["curve_offset_m"] <= 1e-6 for row in geometry),
        "all_complete_curve_endpoints_identical": all(row["endpoints_identical"] for row in geometry),
        "all_saved_curves_identical": all(row["saved_curve_identical"] for row in geometry),
        "all_unselected_ordered_vertices_preserved": all(row["ordered_vertices_preserved"] for row in geometry if not row["selected"]),
        "all_complete_logical_lengths_preserved": all(abs(row["logical_length_delta_m"]) <= 1e-6 for row in geometry),
        "junction_boundary_identical": all(before.find(f"junction[@id='{junction_id}']").get(name) ==
            after.find(f"junction[@id='{junction_id}']").get(name) for name in ("x", "y", "z", "shape")),
        "outside_junction_elements_identical": outside_unchanged,
        "source_unchanged": file_sha256(source) == report["source_sha256"]}
    report.update(status="review_required" if all(checks.values()) else "blocked", connection_key=selected_key,
        preservation_checks=checks, movement_geometry=geometry,
        source_internal_path=[lane.get("id") for lane in source_path],
        candidate_internal_path=[lane.get("id") for lane in candidate_path],
        maximum_curve_offset_m=max(row["curve_offset_m"] for row in geometry),
        outside_junction_elements_unchanged=outside_unchanged,
        candidate_sha256=file_sha256(Path(report["candidate_file"])),
        claim_boundary="One internal waiting representation candidate. All complete curves, roads, access and signal programs remain protected. Paired driving and physical review are required; no field-timing or count-calibration claim.")
    write_json_atomic(out / "manifest.json", report)
    if not all(checks.values()):
        raise ValueError("Single-connection compilation changed protected geometry or exterior elements; inspect manifest.json.")
    return report


def materialize_unsplit_fanout(*, source_file, junction_id, connection_keys, output_dir,
                               netconvert_binary="netconvert"):
    """Keep curves and access; move native conflict checks to the complete path entry.

    This is an explicit review candidate, never an automatic fanout cleanup.
    Split sibling continuations can overlap without being mutual native foes.
    Native contPos=0 keeps their complete curves in the same-origin check.
    """
    source, out = Path(source_file).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or source.is_relative_to(out):
        raise ValueError("Use a new output directory separate from the source.")
    keys = [tuple(key) for key in connection_keys]
    if len(keys) < 2 or len(set(keys)) != len(keys) or len({key[:3] for key in keys}) != 1:
        raise ValueError("Select distinct exits from the same incoming lane and outgoing road.")
    before = ET.parse(source).getroot()
    ordinary = {e.get("id"): e for e in before.findall("edge") if not e.get("function")}
    connections = {_key(c): c for c in before.findall("connection") if c.get("from") in ordinary}
    selected = [connections.get(key) for key in keys]
    if any(row is None for row in selected) or ordinary[keys[0][0]].get("to") != junction_id:
        raise ValueError("Select existing movements at the declared junction.")
    lanes = {lane.get("id"): lane for edge in before.findall("edge") for lane in edge.findall("lane")}
    for row in selected:
        via = row.get("via")
        if not row.get("shape") or via not in lanes or not any(
                c.get("from") == via.rsplit("_", 1)[0] and c.get("fromLane") == lanes[via].get("index")
                and c.get("to") == row.get("to") and c.get("toLane") == row.get("toLane")
                and c.get("via") in lanes for c in before.findall("connection")):
            raise ValueError("Select split internal movements with complete saved curves.")
    patch = ET.Element("connections")
    for row in selected:
        attrs = {key: value for key, value in row.attrib.items() if key not in {"via", "dir", "state"}}
        attrs["contPos"] = "0"
        ET.SubElement(patch, "connection", attrs)
    report = _compile_connection_patch(source, junction_id, patch, out, netconvert_binary)
    after = ET.parse(report["candidate_file"]).getroot()
    new_connections = {_key(c): c for c in after.findall("connection") if c.get("from") in ordinary}
    curves = {str(key): _curve(before, connections[key]).hausdorff_distance(_curve(after, new_connections[key])) for key in keys}
    if max(curves.values()) > 1e-4:
        raise ValueError("Fanout compilation changed protected curves.")
    report.update(connection_keys=keys, maximum_curve_offset_m=max(curves.values()), curve_offsets_m=curves,
        claim_boundary="Native internal waiting representation only. Candidate needs paired driving and physical review; no count-calibration or field-timing claim.")
    write_json_atomic(out / "manifest.json", report)
    return report


def _compile_connection_patch(source, junction_id, patch, out, netconvert_binary):
    """Compile one junction patch while protecting roads, access and control programs."""
    source, out = Path(source).resolve(strict=True), Path(out).resolve()
    if out.exists() or source.is_relative_to(out):
        raise ValueError("Use a new output directory separate from the source.")
    before = ET.parse(source).getroot()
    ordinary = {e.get("id"): e for e in before.findall("edge") if not e.get("function")}
    connections = {_key(c): c for c in before.findall("connection") if c.get("from") in ordinary}
    original_hash = file_sha256(source)
    out.mkdir(parents=True)
    patch_file = out / "connections.con.xml"
    ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
    frozen = out / "geometry-input.net.xml"
    prepare_geometry_frozen_input(source_file=source, output_file=frozen)
    candidate = out / "candidate.net.xml"
    incident = {key for key, edge in ordinary.items() if junction_id in (edge.get("from"), edge.get("to"))}
    command = [str(netconvert_binary), "--sumo-net-file", str(frozen), "--connection-files", str(patch_file),
               "--offset.disable-normalization", "true", "--output-file", str(candidate)]
    execution, conversion = run_scoped_native_conversion(command=command, mutable_nodes={junction_id},
        mutable_edges=incident, output_dir=out, prefix="fanout", timeout_seconds=60, merge_source_file=source)
    write_json_atomic(out / "conversion.json", {"execution": execution.to_dict(), "conversion": conversion})
    if execution.status != "pass":
        raise RuntimeError("Native fanout compilation failed; retain conversion.json.")
    restoration = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        mutable_junction_ids={junction_id}, mutable_edge_ids=set(), expand_mutable_edge_endpoints=False)
    if restoration["status"] != "pass":
        raise ValueError("Fanout compilation did not preserve the exterior network.")
    tree = ET.parse(candidate)
    after = tree.getroot()
    new_connections = {_key(c): c for c in after.findall("connection") if c.get("from") in ordinary}
    def bindings(rows):
        return {key: tuple(c.get(attr) for attr in ("tl", "linkIndex", "linkIndex2")) for key, c in rows.items()}
    if bindings(connections) != bindings(new_connections):
        raise ValueError("Fanout compilation changed signal bindings or legal lane movements.")
    # Native request tables change, while the supplied control programs must not.
    programs = {(c.get("id"), c.get("programID")): c for c in before.findall("tlLogic")}
    if {(c.get("id"), c.get("programID")) for c in after.findall("tlLogic")} != programs.keys():
        raise ValueError("Fanout compilation changed the signal program inventory.")
    for row in list(after.findall("tlLogic")):
        replacement = programs.get((row.get("id"), row.get("programID")))
        if replacement is None:
            raise ValueError("Fanout compilation added an unexplained signal program.")
        at = list(after).index(row)
        after.remove(row)
        after.insert(at, deepcopy(replacement))
    tree.write(candidate, encoding="utf-8", xml_declaration=True)
    new_ordinary = {e.get("id"): e for e in after.findall("edge") if not e.get("function")}
    unchanged = ordinary.keys() == new_ordinary.keys() and all(_canonical(row) == _canonical(new_ordinary[key]) for key, row in ordinary.items())
    access = loaded_turn_permissions(before) == loaded_turn_permissions(after)
    if not unchanged or not access or file_sha256(source) != original_hash:
        raise ValueError("Connection compilation changed protected roads, permissions or source bytes.")
    report = {"status": "review_required", "source_file": str(source), "source_sha256": original_hash,
        "candidate_file": str(candidate), "candidate_sha256": file_sha256(candidate), "junction_id": junction_id,
        "ordinary_roads_unchanged": unchanged, "turn_permissions_unchanged": access,
        "signal_bindings_unchanged": True, "signal_programs_unchanged": True}
    return report
