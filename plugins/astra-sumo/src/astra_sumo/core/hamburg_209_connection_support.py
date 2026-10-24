"""Compile the bounded209 lane-ordinal hypothesis without changing roads or timing."""

from copy import deepcopy
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .junction_split_fanout import _canonical, _curve, _internal_path, _key
from .local_network_conversion import loaded_turn_permissions, local_conversion_input, merge_local_conversion, run_scoped_native_conversion
from .network_geometry_freeze import compare_frozen_geometry, prepare_geometry_frozen_input
from .osm_access import _permission_set


NODES = {"266197536", "266197525"}
REMOVED = {("603103444", i, "73474895", i - 1) for i in (1, 2, 3)}
# New key: node, final signal index, source movement whose access/colors it inherits.
ADDED = {
    ("603103444", 1, "381750336", 1): ("266197536", 6, ("603103444", 0, "381750336", 0)),
    ("603103444", 2, "73474895", 0): ("266197536", 1, ("603103444", 1, "73474895", 0)),
    ("603103444", 3, "73474895", 1): ("266197536", 2, ("603103444", 2, "73474895", 1)),
    ("603103444", 4, "73474895", 2): ("266197536", 3, ("603103444", 3, "73474895", 2)),
    ("73474895", 0, "1108564798#0", 0): ("266197525", 8, ("73474895", 1, "1108564798#0", 1)),
}


def project_program(program):
    """Append only one donor state while preserving every source attribute."""
    identity = program.get("id")
    if identity not in NODES:
        raise ValueError("Select one of the two declared209 controllers.")
    width, donor = (6, 0) if identity == "266197536" else (8, 6)
    result = deepcopy(program)
    if not result.findall("phase"):
        raise ValueError("Require source phases.")
    for phase in result.findall("phase"):
        state = phase.get("state", "")
        if len(state) != width or (identity == "266197525" and state[6] != state[7]):
            raise ValueError("Require source state width and matching old primary colors6/7.")
        phase.set("state", state + state[donor])
    return result


def _control_bytes(raw):
    found = set()

    def replace(match):
        program = ET.fromstring(match.group())
        identity = program.get("id")
        if identity not in NODES:
            return match.group()
        project_program(program)
        found.add(identity)
        donor = 0 if identity == "266197536" else 6
        return re.sub(rb'(<phase\b[^>]*\bstate=)("|\')([^"\']*)\2',
            lambda row: row[1] + row[2] + row[3] + row[3][donor:donor + 1] + row[2], match.group())

    result = re.sub(rb"<tlLogic\b[^>]*>[\s\S]*?</tlLogic>", replace, raw)
    if found != NODES:
        raise ValueError("Require both external209 controller programs.")
    return result


def _rows(root):
    rows = {_key(row): row for row in root.findall("connection")}
    if len(rows) != len(root.findall("connection")):
        raise ValueError("Require unique lane connection keys.")
    return rows


def _programs_match(expected_root, actual_root):
    expected_rows, actual_rows = expected_root.findall("tlLogic"), actual_root.findall("tlLogic")
    expected = {(row.get("id"), row.get("programID")): row for row in expected_rows}
    actual = {(row.get("id"), row.get("programID")): row for row in actual_rows}
    return (len(expected) == len(expected_rows) and len(actual) == len(actual_rows)
        and expected.keys() == actual.keys()
        and all(_canonical(row) == _canonical(actual[key]) for key, row in expected.items()))


def _boundary_attributes_match(before, after):
    return ({key: value for key, value in before.attrib.items() if key != "intLanes"}
        == {key: value for key, value in after.attrib.items() if key != "intLanes"})


def _patch(before):
    rows, access = _rows(before), loaded_turn_permissions(before)
    if not REMOVED <= rows.keys() or any(key in rows for key in ADDED):
        raise ValueError("Require the original misaligned three forwards and absent additions.")
    ordinary = {edge.get("id"): edge for edge in before.findall("edge") if not edge.get("function")}
    patch = ET.Element("connections")
    for key in sorted(REMOVED):
        ET.SubElement(patch, "delete", **{k: rows[key].get(k) for k in ("from", "fromLane", "to", "toLane")})
    expected_bindings = {}
    for key, row in rows.items():
        if key[0] not in ordinary or ordinary[key[0]].get("to") not in NODES or key not in access or key in REMOVED:
            continue
        path = _internal_path(before, row)
        attrs = {k: v for k, v in row.attrib.items() if k not in {"via", "dir", "state", "allow", "disallow", "shape", "contPos"}}
        attrs.update(shape=" ".join(lane.get("shape") for lane in path), length=str(sum(float(lane.get("length")) for lane in path)), speed=path[0].get("speed"))
        attrs.update({"allow": " ".join(sorted(access[key]))} if access[key] else {"disallow": "all"})
        ET.SubElement(patch, "connection", attrs)
        expected_bindings[key] = (row.get("tl"), row.get("linkIndex"), row.get("linkIndex2"))
    lanes = {(edge.get("id"), int(lane.get("index"))): lane for edge in before.findall("edge") for lane in edge.findall("lane")}
    added_access = {}
    for key, (node, signal, donor) in ADDED.items():
        modes = access[donor] & _permission_set(lanes[key[:2]].attrib, network_version=before.get("version")) & _permission_set(lanes[key[2:]].attrib, network_version=before.get("version"))
        if "passenger" not in modes:
            raise ValueError("Require passenger access at both source endpoint lanes and the source donor turn.")
        added_access[key] = modes
        ET.SubElement(patch, "connection", {"from": key[0], "fromLane": str(key[1]), "to": key[2], "toLane": str(key[3]),
            "tl": node, "linkIndex": str(signal), "allow": " ".join(sorted(modes))})
        expected_bindings[key] = (node, str(signal), None)
    return patch, expected_bindings, added_access


def materialize_hamburg_209_connection_support(*, source_file, control_file, output_dir, netconvert_binary):
    """Run scoped netconvert only; retain a review candidate and all failed artifacts."""
    source, control, out = Path(source_file).resolve(strict=True), Path(control_file).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or any(path.is_relative_to(out) for path in (source, control)):
        raise ValueError("Use a new output directory separate from both immutable sources.")
    before, external = ET.parse(source).getroot(), ET.parse(control).getroot()
    patch, bindings, added_access = _patch(before)
    projected = deepcopy(before)
    for root in (projected, external):
        for program in list(root.findall("tlLogic")):
            if program.get("id") in NODES:
                root[list(root).index(program)] = project_program(program)
    out.mkdir(parents=True)
    hashes = {str(path): file_sha256(path) for path in (source, control, Path(__file__))}
    report = {"status": "blocked", "formal_result": False, "traffic_simulation_run": False, "source_hashes": hashes,
        "removed_connections": sorted(REMOVED), "added_connections": list(ADDED), "checks": {},
        "claim_boundary": "Exploratory209 source-ordinal connection hypothesis. No field geometry/control certification, driving safety, capacity or count-calibration claim. Original roads, timing, demand and driver remain protected. Retained734→270 is an inherited model branch, not MAP14→egress5/SG6: that MAP movement is the separate omitted Alsterufer branch, about55m away."}
    try:
        projected_file, frozen = out / "projected-input.net.xml", out / "frozen-input.net.xml"
        ET.ElementTree(projected).write(projected_file, encoding="utf-8", xml_declaration=True)
        report["geometry_input"] = prepare_geometry_frozen_input(source_file=projected_file, output_file=frozen)
        patch_file = out / "connections.con.xml"
        ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
        programs = ET.Element("tlLogics")
        for program in projected.findall("tlLogic"):
            if program.get("id") in NODES:
                programs.append(deepcopy(program))
        for key, binding in bindings.items():
            ET.SubElement(programs, "connection", {"from": key[0], "fromLane": str(key[1]), "to": key[2], "toLane": str(key[3]),
                "tl": binding[0], "linkIndex": binding[1]})
        program_file = out / "projected-programs.tll.xml"
        ET.ElementTree(programs).write(program_file, encoding="utf-8", xml_declaration=True)
        ordinary = {edge.get("id"): edge for edge in before.findall("edge") if not edge.get("function")}
        incident = {key for key, edge in ordinary.items() if NODES & {edge.get("from"), edge.get("to")}}
        candidate = out / "candidate.net.xml"
        execution, conversion = run_scoped_native_conversion(command=[str(netconvert_binary), "--sumo-net-file", str(frozen),
            "--connection-files", str(patch_file), "--tllogic-files", str(program_file), "--offset.disable-normalization", "true",
            "--precision", "12", "--output-file", str(candidate)], mutable_nodes=NODES, mutable_edges=incident,
            output_dir=out, prefix="ordinal", timeout_seconds=120, merge_source_file=source)
        report["execution"] = execution if isinstance(execution, dict) else execution.to_dict()
        report["conversion"] = conversion
        report["restoration"] = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
            mutable_junction_ids=NODES, mutable_edge_ids=set(), expand_mutable_edge_endpoints=False)
        if report["restoration"]["status"] != "pass":
            raise ValueError("Exterior restoration failed.")
        after = ET.parse(candidate).getroot()
        request_signature = {node: [_canonical(row) for row in after.find(f"junction[@id='{node}']").findall("request")] for node in NODES}
        corrections = []
        for node in NODES:
            first, last = before.find(f"junction[@id='{node}']"), after.find(f"junction[@id='{node}']")
            curves = [LineString([tuple(map(float, point.split(",")[:2])) for point in element.get("shape").split()]) for element in (first, last)]
            if curves[0].hausdorff_distance(curves[1]) > 1e-6 or any(abs(float(first.get(a, "0")) - float(last.get(a, "0"))) > 1e-6 for a in ("x", "y", "z")):
                raise ValueError("Native conversion changed the target boundary beyond rounding.")
            for attribute in ("x", "y", "z", "shape", "customShape"):
                if first.get(attribute) != last.get(attribute):
                    corrections.append({"node": node, "attribute": attribute, "native_value": last.get(attribute), "source_value": first.get(attribute)})
                    last.attrib.pop(attribute, None)
                    if first.get(attribute) is not None:
                        last.set(attribute, first.get(attribute))
        report["target_boundary_rounding_restoration"] = corrections
        subset, _ = local_conversion_input(after, NODES, incident)
        if subset is None:
            raise ValueError("Require a strictly local two-junction conversion.")
        after = merge_local_conversion(before, subset, conversion)
        ET.ElementTree(after).write(candidate, encoding="utf-8", xml_declaration=True)
        old, new = _rows(before), _rows(after)
        access = loaded_turn_permissions(after)
        checks = report["checks"]
        checks["exact_turn_key_delta"] = set(access) == (set(loaded_turn_permissions(before)) - REMOVED) | set(ADDED)
        checks["all_existing_turn_permissions_unchanged"] = all(access.get(key) == modes for key, modes in loaded_turn_permissions(before).items() if key not in REMOVED)
        checks["new_turn_permissions_from_donor_and_endpoints"] = all(access.get(key) == modes for key, modes in added_access.items())
        checks["all_declared_signal_bindings"] = all(tuple(new[key].get(a) for a in ("tl", "linkIndex", "linkIndex2")) == value for key, value in bindings.items())
        geometry = []
        for key in bindings.keys() - ADDED.keys():
            first, last = _internal_path(before, old[key]), _internal_path(after, new[key])
            delta = _curve(before, old[key]).hausdorff_distance(_curve(after, new[key]))
            geometry.append({"key": key, "curve_offset_m": delta, "source_length_m": sum(float(lane.get("length")) for lane in first),
                "candidate_length_m": sum(float(lane.get("length")) for lane in last)})
        report["protected_internal_geometry"] = geometry
        checks["existing_internal_curves_and_lengths"] = all(row["curve_offset_m"] <= 1e-6 and abs(row["source_length_m"] - row["candidate_length_m"]) <= 1e-6 for row in geometry)
        checks["programs_and_timing_exact"] = _programs_match(projected, after)
        checks["ordinary_roads_xml_exact"] = all(_canonical(edge) == _canonical(after.find(f"edge[@id='{key}']")) for key, edge in ordinary.items())
        report["ordinary_geometry"] = compare_frozen_geometry(source_file=source, candidate_file=candidate)
        checks["ordinary_geometry_frozen"] = report["ordinary_geometry"]["status"] == "pass"
        checks["target_junction_boundaries_exact"] = all(_boundary_attributes_match(
            before.find(f"junction[@id='{node}']"), after.find(f"junction[@id='{node}']")) for node in NODES)
        checks["native_request_table_complete"] = all({int(r.get("index")) for r in after.find(f"junction[@id='{node}']").findall("request")} == set(range(len(after.find(f"junction[@id='{node}']").get("intLanes").split()))) for node in NODES)
        checks["native_request_tables_not_modified"] = request_signature == {node: [_canonical(row) for row in after.find(f"junction[@id='{node}']").findall("request")] for node in NODES}
        def exterior(root):
            return [_canonical(row) for row in root if not (
                row.tag in {"edge", "junction"} and (row.get("id") in NODES or any(row.get("id", "").startswith(":" + node + "_") for node in NODES))
                or row.tag == "tlLogic" and row.get("id") in NODES
                or row.tag == "connection" and (any(row.get("from", "").startswith(":" + node + "_") for node in NODES)
                    or row.get("from") in ordinary and ordinary[row.get("from")].get("to") in NODES))]
        checks["all_exterior_elements_and_order_exact"] = exterior(before) == exterior(after)
        checks["source_files_immutable"] = all(file_sha256(Path(path)) == value for path, value in hashes.items())
        if all(checks.values()):
            final, copied_control = out / "network.net.xml", out / "control.add.xml"
            final.write_bytes(candidate.read_bytes())
            copied_control.write_bytes(_control_bytes(control.read_bytes()))
            report.update(status="review_required", candidate_file=str(final), candidate_sha256=file_sha256(final),
                control_file=str(copied_control), control_sha256=file_sha256(copied_control))
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    report["source_unchanged"] = all(file_sha256(Path(path)) == value for path, value in hashes.items())
    write_json_atomic(out / "manifest.json", report)
    return report
