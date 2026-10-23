"""Compile two source-supported 197 continuations without retiming its control."""

from collections import Counter
from copy import deepcopy
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .junction_split_fanout import _canonical, _curve, _internal_path, _key
from .local_network_conversion import loaded_turn_permissions, local_conversion_input, merge_local_conversion, run_scoped_native_conversion
from .network_geometry_freeze import compare_frozen_geometry, prepare_geometry_frozen_input
from .osm_access import _permission_set


NODE = "1975693623"
ADDITIONS = (("-186821028", 3, "186821027#0", 4), ("-186821028", 4, "186821027#0", 5))
THROUGH = ("-186821028", 2, "186821027#0", 2)


def _extend_state(state):
    if len(state) != 14:
        raise ValueError("Extend only the original fourteen-state-width197 program.")
    through = state[1]
    projected = list(state)
    for index in (2, 9):
        if through == "G" and state[index] == "G":
            projected[index] = "g"
    return "".join(projected) + through + through


def _extend_program(program):
    result = deepcopy(program)
    for phase in result.findall("phase"):
        phase.set("state", _extend_state(phase.get("state", "")))
    return result


def _extend_control_bytes(raw):
    count = 0

    def program(match):
        nonlocal count
        if ET.fromstring(match.group()).get("id") != NODE:
            return match.group()
        count += 1
        return re.sub(rb'(<phase\b[^>]*\bstate=)("|\')([^"\']*)\2',
                      lambda row: row[1] + row[2] + _extend_state(row[3].decode("ascii")).encode("ascii") + row[2], match.group())

    result = re.sub(rb"<tlLogic\b[^>]*>[\s\S]*?</tlLogic>", program, raw)
    if not count:
        raise ValueError("The external control has no197 program to extend.")
    return result


def _connections(root):
    rows = {_key(c): c for c in root.findall("connection")}
    if len(rows) != len(root.findall("connection")):
        raise ValueError("Require unique connection keys, including pedestrian connections.")
    return rows


def _target_roads(root):
    return {e.get("id") for e in root.findall("edge") if not e.get("function")
            and NODE in (e.get("from"), e.get("to"))}


def _input_check(root, control):
    node = root.find(f"junction[@id='{NODE}']")
    rows, access = _connections(root), loaded_turn_permissions(root)
    if (node is None or node.get("type") != "traffic_light" or len(node.findall("request")) != 14
            or _target_roads(root) != {"-186821028", "186821027#0", "186821028", "186821029#1", "28665128#0"}
            or any(k in rows for k in ADDITIONS) or THROUGH not in rows
            or rows[THROUGH].get("tl") != NODE or rows[THROUGH].get("linkIndex") != "1"
            or "passenger" not in access[THROUGH]):
        raise ValueError("Require the original 197 junction, fourteen requests and existing car-through signal1.")
    programs = root.findall(f"tlLogic[@id='{NODE}']") + control.findall(f"tlLogic[@id='{NODE}']")
    if len(programs) < 2 or any(not p.findall("phase") or any(len(s.get("state", "")) != 14 for s in p.findall("phase")) for p in programs):
        raise ValueError("Require the original fourteen-state-width network and external197 programs.")
    def parameters(program):
        copy = deepcopy(program)
        copy.attrib.pop("programID", None)
        return _canonical(copy)

    network_programs = {parameters(p) for p in root.findall(f"tlLogic[@id='{NODE}']")}
    if any(parameters(p) not in network_programs for p in control.findall(f"tlLogic[@id='{NODE}']")):
        raise ValueError("External197 control must match the network priority-conversion program apart from its ID.")
    if access.get(("-186821028", 1, "186821027#0", 1)) != {"bicycle"}:
        raise ValueError("Keep the separate bicycle-only connection1→1.")
    return rows, access


def _patch(root, rows, access, *, add_new=True):
    patch = ET.Element("connections")
    ordinary = {e.get("id"): e for e in root.findall("edge") if not e.get("function")}
    for key, row in rows.items():
        if key[0] not in ordinary or ordinary[key[0]].get("to") != NODE or key not in access:
            continue
        path = _internal_path(root, row)
        shape = " ".join(lane.get("shape") for lane in path)
        attrs = {k: v for k, v in row.attrib.items() if k not in {"via", "state", "dir", "shape", "contPos", "allow", "disallow"}}
        attrs.update(shape=shape, length=str(sum(float(lane.get("length")) for lane in path)))
        if key not in ADDITIONS:
            speeds = {float(lane.get("speed")) for lane in path}
            if len(speeds) != 1 or not all(math.isfinite(value) and value > 0 for value in speeds):
                raise ValueError("Require a uniform finite positive old internal speed to pin.")
            attrs["speed"] = path[0].get("speed")
        else:
            attrs.pop("speed", None)
        attrs.update({"allow": " ".join(sorted(access[key]))} if access[key] else {"disallow": "all"})
        ET.SubElement(patch, "connection", attrs)
    lanes = {(e.get("id"), i): lane for e in root.findall("edge") for i, lane in enumerate(e.findall("lane"))}
    for first, fi, last, li in ADDITIONS if add_new else ():
        modes = _permission_set(lanes[first, fi].attrib, network_version=root.get("version")) & _permission_set(lanes[last, li].attrib, network_version=root.get("version"))
        if "passenger" not in modes:
            raise ValueError("Both new continuations must retain source endpoint passenger access.")
        ET.SubElement(patch, "connection", {"from": first, "fromLane": str(fi), "to": last, "toLane": str(li),
                      "tl": NODE, "linkIndex": "1", "allow": " ".join(sorted(modes))})
    for edge in root.findall("edge"):
        if edge.get("function") != "crossing" or not edge.get("id", "").startswith(f":{NODE}_"):
            continue
        lane = edge.find("lane")
        attrs = {"node": NODE, "edges": edge.get("crossingEdges")}
        bindings = [row for row in root.findall("connection") if row.get("tl") == NODE and row.get("to") == edge.get("id")]
        if len(bindings) != 1:
            raise ValueError("Require one declared source signal binding for each crossing.")
        signal = {key: bindings[0].get(key) for key in ("linkIndex", "linkIndex2") if bindings[0].get(key) is not None}
        ET.SubElement(patch, "crossing", {**attrs, "discard": "true"})
        ET.SubElement(patch, "crossing", {**attrs, **signal, "shape": lane.get("shape"), "width": lane.get("width", "4")})
    edges = {edge.get("id"): edge for edge in root.findall("edge")}
    for ordinal, expected in enumerate(("-186821028 186821027#0", "186821028", "186821029#1", "186821029#1 186821027#0")):
        identity = f":{NODE}_w{ordinal}"
        if identity not in edges:
            raise ValueError("Require the original four walking-area adjacency sets.")
        attached = {row.get("to") if row.get("from") == identity else row.get("from")
                    for row in root.findall("connection") if identity in (row.get("from"), row.get("to"))}
        adjacent = attached & ordinary.keys()
        if not adjacent:
            adjacent = {road for edge_id in attached if edges[edge_id].get("function") == "crossing"
                        for road in edges[edge_id].get("crossingEdges", "").split()}
        if adjacent != set(expected.split()):
            raise ValueError("Source walking-area adjacency differs from the bounded197 support.")
        lane = edges[identity].find("lane")
        ET.SubElement(patch, "walkingArea", node=NODE, edges=expected, shape=lane.get("shape"), width=lane.get("width", "3.2"))
    return patch


def _remap_signals(before, after):
    old, new = _connections(before), _connections(after)
    expected = {k for k, c in old.items() if c.get("tl") == NODE}
    actual = {k for k, c in new.items() if c.get("tl") == NODE}
    if actual != expected | set(ADDITIONS):
        raise ValueError("Native conversion changed a controlled connection key, including pedestrian keys.")
    for key in expected | set(ADDITIONS):
        row = new[key]
        for attr in ("tl", "linkIndex", "linkIndex2"):
            row.attrib.pop(attr, None)
        if key in old:
            binding = old[key]
            row.attrib.update({attr: binding.get(attr) for attr in ("tl", "linkIndex", "linkIndex2") if binding.get(attr) is not None})
        else:
            row.attrib.update(tl=NODE, linkIndex=str(14 + ADDITIONS.index(key)))
    for row in list(after.findall("tlLogic")):
        after.remove(row)
    position = next((i for i, c in enumerate(after) if c.tag == "junction"), len(after))
    for i, row in enumerate(before.findall("tlLogic")):
        after.insert(position + i, _extend_program(row) if row.get("id") == NODE else deepcopy(row))


def _restore_local_geometry(before, after):
    old, new = _connections(before), _connections(after)
    source_access = loaded_turn_permissions(before)
    # Native group-edge averaging can alter an old lane's operational length despite a pinned curve.
    corrections = []
    for key, row in old.items():
        if row.get("tl") != NODE or key not in source_access:
            continue
        old_path, new_path = _internal_path(before, row), _internal_path(after, new[key])
        if len(old_path) == len(new_path) == 1 and _curve(before, row).hausdorff_distance(_curve(after, new[key])) <= 1e-6:
            for first, last in zip(old_path, new_path, strict=True):
                if first.get("length") != last.get("length"):
                    corrections.append({"connection_key": key, "lane": last.get("id"), "native_length": last.get("length"), "source_length": first.get("length")})
                    last.set("length", first.get("length"))
        for attribute, default in (("speed", "0"), ("width", "3.2")):
            values = {float(lane.get(attribute, default)) for lane in old_path}
            if len(values) != 1:
                continue
            source_value = next(iter(values))
            if not math.isfinite(source_value) or any(
                    not math.isfinite(float(lane.get(attribute, default)))
                    or abs(float(lane.get(attribute, default)) - source_value) > 1e-6 for lane in new_path):
                raise ValueError(f"Native internal {attribute} changed beyond rounding.")
            source_text = old_path[0].get(attribute)
            for lane in new_path:
                if lane.get(attribute) != source_text:
                    corrections.append({"connection_key": key, "lane": lane.get("id"), "attribute": attribute,
                                        "native_value": lane.get(attribute), "source_value": source_text})
                    lane.attrib.pop(attribute, None)
                    if source_text is not None:
                        lane.set(attribute, source_text)
    for edge in before.findall("edge"):
        if edge.get("function") not in {"crossing", "walkingarea"} or not edge.get("id", "").startswith(f":{NODE}_"):
            continue
        target = after.find(f"edge[@id='{edge.get('id')}']")
        if target is None or [lane.get("id") for lane in target.findall("lane")] != [lane.get("id") for lane in edge.findall("lane")]:
            raise ValueError("Keep the existing crossing and walking-area lane identities.")
        for first, last in zip(edge.findall("lane"), target.findall("lane"), strict=True):
            curves = [LineString([tuple(map(float, point.split(",")[:2])) for point in lane.get("shape").split()]) for lane in (first, last)]
            if (curves[0].hausdorff_distance(curves[1]) > 1e-6
                    or any(abs(float(first.get(a, default)) - float(last.get(a, default))) > 1e-6
                           for a, default in (("length", "0"), ("width", "3.2"), ("speed", "0")))
                    or _permission_set(first.attrib, network_version=before.get("version")) != _permission_set(last.attrib, network_version=after.get("version"))):
                raise ValueError("Native crossing or walking-area geometry/access changed beyond rounding.")
            last.attrib.clear()
            last.attrib.update(first.attrib)
    original = before.find(f"junction[@id='{NODE}']")
    target = after.find(f"junction[@id='{NODE}']")
    coordinates = [(float(original.get(a, "0")), float(target.get(a, "0"))) for a in ("x", "y", "z")]
    curves = [LineString([tuple(map(float, p.split(",")[:2])) for p in node.get("shape").split()]) for node in (original, target)]
    if any(not math.isfinite(a) or not math.isfinite(b) or abs(a - b) > 1e-6 for a, b in coordinates) or curves[0].hausdorff_distance(curves[1]) > 1e-6:
        raise ValueError("Native junction boundary changed beyond rounding.")
    for attribute in ("x", "y", "z", "shape", "customShape"):
        if original.get(attribute) != target.get(attribute):
            corrections.append({"junction": NODE, "attribute": attribute,
                                "native_value": target.get(attribute), "source_value": original.get(attribute)})
            target.attrib.pop(attribute, None)
            if original.get(attribute) is not None:
                target.set(attribute, original.get(attribute))
    return corrections


def _request_structure(root):
    node = root.find(f"junction[@id='{NODE}']")
    return tuple(node.get(a) for a in ("type", "incLanes", "intLanes")), tuple(_canonical(r) for r in node.findall("request"))


def _right_of_way_checks(root):
    rows, access = _connections(root), loaded_turn_permissions(root)
    target = root.find(f"junction[@id='{NODE}']")
    internal = target.get("intLanes").split()
    requests = {int(r.get("index")): r for r in target.findall("request")}
    indices = {}
    for key, row in rows.items():
        if row.get("tl") != NODE:
            continue
        last = _internal_path(root, row)[-1].get("id") if key in access else f"{key[2]}_{key[3]}"
        indices[key] = internal.index(last)

    def bit(first, second, attribute):
        return requests[indices[first]].get(attribute)[-1 - indices[second]] == "1"

    merging = ADDITIONS[1]
    yielding, relations = {}, {}
    for signal in (2, 9):
        turn = next(k for k, row in rows.items() if row.get("tl") == NODE and row.get("linkIndex") == str(signal))
        conflict = bit(merging, turn, "foes") and bit(turn, merging, "foes")
        yield_direction = bit(turn, merging, "response") and not bit(merging, turn, "response")
        yielding[f"new_merging_turn_has_native_foe_and_old{signal}_yields"] = conflict and yield_direction
        relations[f"new15_old{signal}_mutual_foes"] = conflict
        relations[f"old{signal}_responds_to_new15_only"] = yield_direction
    unsafe = []
    for program in root.findall(f"tlLogic[@id='{NODE}']"):
        for ordinal, phase in enumerate(program.findall("phase")):
            state = phase.get("state")
            for key in ADDITIONS:
                if state[int(rows[key].get("linkIndex"))] != "G":
                    continue
                for other in indices.keys() - {key}:
                    if state[int(rows[other].get("linkIndex"))] == "G" and (bit(key, other, "foes") or bit(other, key, "foes")):
                        unsafe.append({"phase": ordinal, "new_key": key, "foe_key": other})
    return {**yielding,
            "no_new_protected_green_against_protected_foes": not unsafe}, {
            "movement_request_indices": [{"key": k, "request_index": i, "tls_index": int(rows[k].get("linkIndex"))} for k, i in indices.items()],
            **relations, "unsafe_projected_phases": unsafe}


def _checks(before, after):
    old, new = loaded_turn_permissions(before), loaded_turn_permissions(after)
    rows, candidate = _connections(before), _connections(after)
    lanes = {(e.get("id"), i): lane for e in before.findall("edge") for i, lane in enumerate(e.findall("lane"))}
    access = set(new) == set(old) | set(ADDITIONS) and all(new[k] == v for k, v in old.items())
    access &= all(new.get(k) == _permission_set(lanes[k[:2]].attrib, network_version=before.get("version")) &
                  _permission_set(lanes[k[2:]].attrib, network_version=before.get("version")) for k in ADDITIONS)
    geometry = []
    for key in old:
        if rows[key].get("tl") != NODE:
            continue
        first, last = _curve(before, rows[key]), _curve(after, candidate[key])
        old_path, new_path = _internal_path(before, rows[key]), _internal_path(after, candidate[key])
        geometry.append({"connection_key": key, "curve_offset_m": first.hausdorff_distance(last),
            "logical_length_delta_m": sum(float(lane.get("length")) for lane in new_path) - sum(float(lane.get("length")) for lane in old_path),
            "source_parts": len(old_path), "candidate_parts": len(new_path),
            "width_speed_equal": {(float(lane.get("width", "3.2")), float(lane.get("speed"))) for lane in old_path} == {(float(lane.get("width", "3.2")), float(lane.get("speed"))) for lane in new_path}})

    def exterior(root):
        ordinary = {e.get("id"): e for e in root.findall("edge") if not e.get("function")}
        return [_canonical(r) for r in root if not (
            r.tag in {"edge", "junction"} and (r.get("id") == NODE or r.get("id", "").startswith(f":{NODE}_"))
            or r.tag == "tlLogic" and r.get("id") == NODE
            or r.tag == "connection" and (r.get("from", "").startswith(f":{NODE}_") or r.get("to", "").startswith(f":{NODE}_")
                or r.get("from") in ordinary and ordinary[r.get("from")].get("to") == NODE))]

    foot = {e.get("id") for e in before.findall("edge") if e.get("function") in {"crossing", "walkingarea"} and e.get("id", "").startswith(f":{NODE}_")}
    def foot_connections(root):
        return Counter(_canonical(c) for c in root.findall("connection") if c.get("from") in foot or c.get("to") in foot)
    foot_geometry = all(_canonical(before.find(f"edge[@id='{e}']")) == _canonical(after.find(f"edge[@id='{e}']")) for e in foot)
    requests = after.find(f"junction[@id='{NODE}']").findall("request")
    result = {"only_two_added_turns_and_all_loaded_classes_preserved": access,
        "existing_controlled_bindings_including_pedestrians_preserved": all(
            all(candidate[k].get(a) == c.get(a) for a in ("tl", "linkIndex", "linkIndex2")) for k, c in rows.items() if c.get("tl")),
        "new_throughs_use_only_new_signals14_15": all(candidate[k].get("tl") == NODE and candidate[k].get("linkIndex") == str(14 + i) for i, k in enumerate(ADDITIONS)),
        "only_declared_priority_case_change_and_two_states_appended": [_canonical(_extend_program(p) if p.get("id") == NODE else p) for p in before.findall("tlLogic")] == [_canonical(p) for p in after.findall("tlLogic")],
        "all_existing_complete_curves_and_lengths_preserved": all(r["curve_offset_m"] <= 1e-6 and abs(r["logical_length_delta_m"]) <= 1e-6 and r["width_speed_equal"] for r in geometry),
        "crossing_walking_geometry_and_connectivity_identical": foot_geometry and foot_connections(before) == foot_connections(after),
        "outside_elements_and_order_identical": exterior(before) == exterior(after),
        "junction_boundary_identical": all(before.find(f"junction[@id='{NODE}']").get(a) == after.find(f"junction[@id='{NODE}']").get(a) for a in ("x", "y", "z", "shape")),
        "native_sixteen_request_rows_retained": len(requests) == 16 and {int(r.get("index")) for r in requests} == set(range(16)) and all(len(r.get(a, "")) == 16 for r in requests for a in ("response", "foes"))}
    result["all_old_signal_colors_unchanged"] = all(
        first.get("state").lower() == last.get("state")[:14].lower()
        for program in before.findall(f"tlLogic[@id='{NODE}']")
        for first, last in zip(program.findall("phase"), after.find(f"tlLogic[@id='{NODE}'][@programID='{program.get('programID')}']").findall("phase"), strict=True))
    return result, geometry


def materialize_hamburg_197_through_support(*, source_file, control_file, output_dir, netconvert_binary):
    """Produce a bounded review candidate; never run traffic or certify field control."""
    source, control = Path(source_file).resolve(strict=True), Path(control_file).resolve(strict=True)
    out = Path(output_dir).resolve()
    if out.exists() or any(p.is_relative_to(out) for p in (source, control)):
        raise ValueError("Use a new output directory separate from both source files.")
    before, external = ET.parse(source).getroot(), ET.parse(control).getroot()
    rows, access = _input_check(before, external)
    patch = _patch(before, rows, access)
    sources = {str(p): file_sha256(p) for p in (source, control)}
    out.mkdir(parents=True)
    report = {"status": "blocked", "formal_result": False, "traffic_simulation_run": False, "source_hashes": sources, "added_connections": ADDITIONS,
        "new_through_signal_assumption": "New14/15 inherit car-through1. Existing parking-left2 and Uturn9 become permissive g only when both their own state and donor1 are G. All old green/red/yellow colors and all phase parameters remain unchanged; both existing legal turning movements yield to the new straight merge. This declared priority adaptation is not field signal-head evidence.",
        "claim_boundary": "Native actuated histories may change with traffic. This offline candidate has no field SPaT, native driving safety, capacity or count-fit claim.", "checks": {}}
    report["priority_case_changes"] = [{"phase_index": i, "signal_index": j, "before": phase.get("state")[j], "after": _extend_state(phase.get("state"))[j]}
        for i, phase in enumerate(before.find(f"tlLogic[@id='{NODE}']").findall("phase")) for j in range(14)
        if phase.get("state")[j] != _extend_state(phase.get("state"))[j]]
    try:
        frozen, patch_file, candidate = out / "frozen-input.net.xml", out / "connections.con.xml", out / "candidate.net.xml"
        report["geometry_input"] = prepare_geometry_frozen_input(source_file=source, output_file=frozen)
        ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
        execution, conversion = run_scoped_native_conversion(command=[str(netconvert_binary), "--sumo-net-file", str(frozen),
            "--connection-files", str(patch_file), "--offset.disable-normalization", "true", "--precision", "12", "--output-file", str(candidate)],
            mutable_nodes={NODE}, mutable_edges=_target_roads(before), output_dir=out, prefix="through", timeout_seconds=120,
            merge_source_file=source)
        report.update(execution=execution if isinstance(execution, dict) else {key: getattr(execution, key, None) for key in ("command", "returncode", "stdout", "stderr", "error", "status")}, conversion=conversion)
        code = execution.get("returncode") if isinstance(execution, dict) else execution.returncode
        if code != 0 or not candidate.is_file():
            raise ValueError("Native scoped conversion failed; inspect its retained artifacts.")
        report["restoration"] = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
            mutable_junction_ids={NODE}, mutable_edge_ids=set(), expand_mutable_edge_endpoints=False)
        if report["restoration"]["status"] != "pass":
            raise ValueError("Scoped exterior restoration failed.")
        after = ET.parse(candidate).getroot()
        native_requests = _request_structure(after)
        _remap_signals(before, after)
        report["old_internal_attribute_corrections"] = _restore_local_geometry(before, after)
        subset, _ = local_conversion_input(after, {NODE}, _target_roads(before))
        if subset is None:
            raise ValueError("Require a strictly local197 conversion.")
        after = merge_local_conversion(before, subset, conversion)
        # Recompute right of way with the final projected programs, not a post-hoc g/G edit.
        projected = out / "projected-program-input.net.xml"
        ET.ElementTree(after).write(projected, encoding="utf-8", xml_declaration=True)
        final_program = ET.Element("tlLogics")
        for program in after.findall(f"tlLogic[@id='{NODE}']"):
            final_program.append(deepcopy(program))
        for row in after.findall("connection"):
            if row.get("tl") == NODE and row.get("from") in _target_roads(before) and row.get("to") in _target_roads(before):
                ET.SubElement(final_program, "connection", {k: v for k, v in row.attrib.items() if k in {"from", "fromLane", "to", "toLane", "tl", "linkIndex", "linkIndex2"}})
        program_file, final_patch = out / "final-program.tll.xml", out / "final-shapes.con.xml"
        ET.ElementTree(final_program).write(program_file, encoding="utf-8", xml_declaration=True)
        ET.ElementTree(_patch(after, _connections(after), loaded_turn_permissions(after), add_new=False)).write(final_patch, encoding="utf-8", xml_declaration=True)
        final_frozen = out / "final-frozen-input.net.xml"
        report["priority_geometry_input"] = prepare_geometry_frozen_input(source_file=projected, output_file=final_frozen)
        candidate = out / "priority-candidate.net.xml"
        execution, conversion = run_scoped_native_conversion(command=[str(netconvert_binary), "--sumo-net-file", str(final_frozen),
            "--connection-files", str(final_patch), "--tllogic-files", str(program_file), "--offset.disable-normalization", "true", "--precision", "12", "--output-file", str(candidate)],
            mutable_nodes={NODE}, mutable_edges=_target_roads(before), output_dir=out, prefix="priority", timeout_seconds=120,
            merge_source_file=source)
        report.update(priority_execution=execution if isinstance(execution, dict) else {key: getattr(execution, key, None) for key in ("command", "returncode", "stdout", "stderr", "error", "status")}, priority_conversion=conversion)
        code = execution.get("returncode") if isinstance(execution, dict) else execution.returncode
        if code != 0 or not candidate.is_file():
            raise ValueError("Final-program native priority conversion failed; inspect its retained artifacts.")
        report["priority_restoration"] = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
            mutable_junction_ids={NODE}, mutable_edge_ids=set(), expand_mutable_edge_endpoints=False)
        if report["priority_restoration"]["status"] != "pass":
            raise ValueError("Final-program exterior restoration failed.")
        after = ET.parse(candidate).getroot()
        expected = _connections(ET.parse(projected).getroot())
        actual = _connections(after)
        if any(k not in actual or any(actual[k].get(a) != row.get(a) for a in ("tl", "linkIndex", "linkIndex2"))
               for k, row in expected.items() if row.get("tl") == NODE):
            raise ValueError("Final native conversion did not retain the declared sixteen signal bindings.")
        expected_programs = [_canonical(p) for p in ET.parse(projected).getroot().findall(f"tlLogic[@id='{NODE}']")]
        if expected_programs != [_canonical(p) for p in after.findall(f"tlLogic[@id='{NODE}']")]:
            raise ValueError("Final native conversion changed the projected programs.")
        native_requests = _request_structure(after)
        report["final_internal_attribute_corrections"] = _restore_local_geometry(before, after)
        subset, _ = local_conversion_input(after, {NODE}, _target_roads(before))
        if subset is None:
            raise ValueError("Require a strictly local final priority conversion.")
        after = merge_local_conversion(before, subset, conversion)
        ET.ElementTree(after).write(candidate, encoding="utf-8", xml_declaration=True)
        report["checks"], report["existing_movement_geometry"] = _checks(before, after)
        priority_checks, report["priority_audit"] = _right_of_way_checks(after)
        report["checks"].update(priority_checks)
        report["checks"]["native_request_table_not_replaced"] = native_requests == _request_structure(after)
        report["ordinary_geometry"] = compare_frozen_geometry(source_file=source, candidate_file=candidate)
        report["checks"]["all_ordinary_geometry_identical"] = report["ordinary_geometry"]["status"] == "pass"
        report["checks"]["source_files_immutable"] = all(file_sha256(Path(p)) == h for p, h in sources.items())
        if all(report["checks"].values()):
            final, copied_control = out / "network.net.xml", out / "control.add.xml"
            shutil.copyfile(candidate, final)
            copied_control.write_bytes(_extend_control_bytes(control.read_bytes()))
            report.update(status="review_required", candidate_file=str(final), candidate_sha256=file_sha256(final),
                          control_file=str(copied_control), control_sha256=file_sha256(copied_control))
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    report["source_unchanged"] = all(file_sha256(Path(p)) == h for p, h in sources.items())
    write_json_atomic(out / "manifest.json", report)
    return report
