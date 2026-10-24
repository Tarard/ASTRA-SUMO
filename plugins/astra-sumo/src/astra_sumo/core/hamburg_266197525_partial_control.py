"""Unbind four declared continuations while retaining the two counter signals."""

from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .hamburg_209_connection_support import _boundary_attributes_match, _programs_match
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts
from .junction_split_fanout import _canonical, _curve, _internal_path, _key
from .local_network_conversion import loaded_turn_permissions, local_conversion_input, merge_local_conversion, run_scoped_native_conversion
from .network_geometry_freeze import prepare_geometry_frozen_input
from .osm_access import _permission_set


NODE = "266197525"
MAIN = {("73474895", 0, "27064186#0", 0), ("73474895", 0, "1108564798#0", 0),
        ("73474895", 1, "1108564798#0", 1), ("73474895", 2, "1108564798#0", 2)}
COUNTER = {("25866659#2", 0, "25866655#0", 0): 0, ("25866659#2", 1, "25866655#0", 1): 1}


def _target_rows(root):
    edges = {e.get("id"): e for e in root.findall("edge") if not e.get("function")}
    rows = [r for r in root.findall("connection") if r.get("from") in edges and edges[r.get("from")].get("to") == NODE]
    result = {_key(r): r for r in rows}
    if len(result) != len(rows) or result.keys() != MAIN | COUNTER.keys():
        raise ValueError("Require exactly the four main and two counter movements.")
    return result


def _project_partial_control(root):
    result = deepcopy(root)
    rows = _target_rows(result)
    expected = {**COUNTER, ("73474895", 0, "27064186#0", 0): 5, ("73474895", 0, "1108564798#0", 0): 8,
                ("73474895", 1, "1108564798#0", 1): 6, ("73474895", 2, "1108564798#0", 2): 7}
    if any(r.get("tl") != NODE or r.get("linkIndex") != str(expected[k]) for k, r in rows.items()):
        raise ValueError("Require the original six signal bindings.")
    for key in MAIN:
        for attr in ("tl", "linkIndex", "linkIndex2"):
            rows[key].attrib.pop(attr, None)
        rows[key].set("uncontrolled", "true")
    return result


def _movement_requests(root):
    rows = _target_rows(root)
    node = root.find(f"junction[@id='{NODE}']")
    lanes = node.get("intLanes").split()
    requests = {int(r.get("index")): r for r in node.findall("request")}
    indices = {key: lanes.index(_internal_path(root, row)[-1].get("id")) for key, row in rows.items()}
    if set(indices.values()) != set(requests) or len(indices) != len(lanes) or len(requests) != 6:
        raise ValueError("Require a complete one-to-one six-movement request mapping.")
    keys = {index: key for key, index in indices.items()}
    result = {}
    for key, index in indices.items():
        row = requests[index]
        relation = {"cont": row.get("cont")}
        for name in ("response", "foes"):
            bits = row.get(name, "")
            if len(bits) != 6 or set(bits) - {"0", "1"}:
                raise ValueError("Require complete native request bit strings.")
            relation[name] = tuple(sorted(keys[i] for i, bit in enumerate(reversed(bits)) if bit == "1"))
        result[key] = relation
    return result


def _connection_attributes(before, key, row, access):
    if key not in MAIN | COUNTER.keys():
        raise ValueError("Only the six declared movements may enter the patch.")
    path = _internal_path(before, row)
    attrs = {k: v for k, v in row.attrib.items() if k not in {"via", "dir", "state", "tl", "linkIndex", "linkIndex2", "allow", "disallow", "shape", "contPos"}}
    attrs.update(shape=" ".join(r.get("shape") for r in path), length=str(sum(float(r.get("length")) for r in path)), speed=path[0].get("speed"))
    attrs.update({"allow": " ".join(sorted(access[key]))} if access[key] else {"disallow": "all"})
    attrs["uncontrolled"] = "true" if key in MAIN else "false"
    if key in MAIN and key[2] == "1108564798#0":
        attrs["contPos"] = "0"  # Freeze the source's cont=0 single internal path.
    return attrs


def _verify_candidate(before, after, native):
    old, new = _target_rows(before), _target_rows(after)
    roads = [{e.get("id"): e for e in root.findall("edge") if not e.get("function")} for root in (before, after)]
    geometry, protected_connections, protected_lanes, protected_internal_connections = [], [], [], []
    for key in old:
        first, last = _internal_path(before, old[key]), _internal_path(after, new[key])
        geometry.append({"movement": key, "curve_offset_m": _curve(before, old[key]).hausdorff_distance(_curve(after, new[key])),
            "length_delta_m": sum(float(r.get("length")) for r in last) - sum(float(r.get("length")) for r in first),
            "speeds_exact": [float(r.get("speed")) for r in first] == [float(r.get("speed")) for r in last]})
        ignored = {"tl", "linkIndex", "linkIndex2", "state", "via", "uncontrolled", "allow", "disallow", "shape", "length", "speed", "contPos"}
        attrs_exact = {k: v for k, v in old[key].attrib.items() if k not in ignored} == {k: v for k, v in new[key].attrib.items() if k not in ignored}
        cont = new[key].get("contPos")
        attrs_exact &= (cont is None or float(cont) == 0) if key in MAIN and key[2] == "1108564798#0" else cont == old[key].get("contPos")
        attrs_exact &= [ET.tostring(r) for r in old[key]] == [ET.tostring(r) for r in new[key]]
        if new[key].get("speed") is not None:
            attrs_exact &= float(new[key].get("speed")) == float(first[0].get("speed"))
        if new[key].get("length") is not None:
            attrs_exact &= abs(float(new[key].get("length")) - sum(float(r.get("length")) for r in first)) <= 1e-6
        if new[key].get("shape") is not None:
            shape = LineString([tuple(map(float, p.split(",")[:2])) for p in new[key].get("shape").split()])
            attrs_exact &= shape.hausdorff_distance(_curve(before, old[key])) <= 1e-6
        protected_connections.append(attrs_exact)
        lane_ignored = {"id", "index", "shape", "length", "speed", "allow", "disallow"}
        def lane_attrs(lane):
            return {k: float(v) if k == "width" else v for k, v in lane.attrib.items() if k not in lane_ignored}
        protected_lanes.append(len(first) == len(last) and all(lane_attrs(a) == lane_attrs(b)
            and _permission_set(a.attrib, network_version=before.get("version")) == _permission_set(b.attrib, network_version=after.get("version"))
            and [ET.tostring(r) for r in a] == [ET.tostring(r) for r in b] for a, b in zip(first, last, strict=False)))
        def internal_attributes(root, lane):
            edge = lane.get("id").rsplit("_", 1)[0]
            found = [r for r in root.findall("connection") if r.get("from") == edge and r.get("fromLane") == lane.get("index") and r.get("to") == key[2] and r.get("toLane") == str(key[3])]
            if len(found) != 1:
                raise ValueError("Require one internal continuation per protected path segment.")
            ignored_internal = {"from", "fromLane", "via", "tl", "linkIndex", "linkIndex2", "state"}
            return ({k: v for k, v in found[0].attrib.items() if k not in ignored_internal}, [ET.tostring(r) for r in found[0]])
        protected_internal_connections.append(len(first) == len(last) and all(internal_attributes(before, a) == internal_attributes(after, b) for a, b in zip(first, last, strict=False)))
    def exterior(root):
        return [_canonical(r) for r in root if not (
            r.tag in {"edge", "junction"} and (r.get("id") == NODE or r.get("id", "").startswith(":" + NODE + "_"))
            or r.tag == "connection" and (r.get("from", "").startswith(":" + NODE + "_") or r.get("from") in roads[0] and roads[0][r.get("from")].get("to") == NODE))]
    checks = {
        "ordinary_roads_xml_exact": roads[0].keys() == roads[1].keys() and all(_canonical(v) == _canonical(roads[1][k]) for k, v in roads[0].items()),
        "all_turn_keys_and_loaded_permissions_exact": loaded_turn_permissions(before) == loaded_turn_permissions(after),
        "all_programs_exact": _programs_match(before, after),
        "outside_elements_and_order_exact": exterior(before) == exterior(after),
        "target_attributes_exact": _boundary_attributes_match(before.find(f"junction[@id='{NODE}']"), after.find(f"junction[@id='{NODE}']")),
        "four_main_minor_uncontrolled": all(not any(a in new[k].attrib for a in ("tl", "linkIndex", "linkIndex2")) and new[k].get("state") == "m" and new[k].get("uncontrolled") in {"true", "1"} for k in MAIN),
        "counter_bindings_and_states_exact": all(new[k].get("tl") == NODE and new[k].get("linkIndex") == str(index) and new[k].get("state") == "O" and new[k].get("linkIndex2") == old[k].get("linkIndex2") for k, index in COUNTER.items()),
        "requests_and_foes_same_by_movement": _movement_requests(before) == _movement_requests(after),
        "native_requests_not_overlaid": _movement_requests(native) == _movement_requests(after),
        "all_six_internal_curves_lengths_speeds": all(r["curve_offset_m"] <= 1e-6 and abs(r["length_delta_m"]) <= 1e-6 and r["speeds_exact"] for r in geometry),
        "all_six_connection_runtime_attributes_exact": all(protected_connections),
        "all_internal_lane_width_LC_permissions_exact": all(protected_lanes),
        "all_internal_connection_runtime_attributes_exact": all(protected_internal_connections),
    }
    return checks, geometry


def materialize_hamburg_266197525_partial_control(*, source_file, control_file, output_dir, netconvert_binary):
    """Compile one local candidate, preserving failed native artifacts and source bytes."""
    source, control, out = Path(source_file).resolve(strict=True), Path(control_file).resolve(strict=True), Path(output_dir).resolve()
    if out.exists() or any(p.is_relative_to(out) for p in (source, control)):
        raise ValueError("Use a new candidate directory separate from both immutable inputs.")
    if file_sha256(source) != "4cd5d1b95cce0f43b0626e82c7066cce3b1807db35840c115bcf66252437ae80" or file_sha256(control) != "27220b430aa68c24f0ad360c57ee3bd0f4b8dcdc88aead5344bcff00e4f43e3b":
        raise ValueError("Require the frozen209 source network and external control.")
    before = ET.parse(source).getroot()
    projected = _project_partial_control(before)
    out.mkdir(parents=True)
    copied_control = out / "control.add.xml"
    copied_control.write_bytes(control.read_bytes())
    hashes = {str(p): file_sha256(p) for p in (source, control, Path(__file__))}
    report = {"schema": "astra.hamburg.266197525-partial-control/v1", "status": "blocked", "formal_result": False,
        "source_hashes": hashes, "traffic_simulation_run": False, "checks": {}, "control_file": str(copied_control), "control_sha256": file_sha256(copied_control),
        "claim_boundary": "Partial-control source hypothesis only. Four main continuations yield to the original two controlled counter movements. Programme bytes are preserved, not actual actuated histories. Counter control applicability and dated field SPaT remain unverified; no driving safety or count-fit claim."}
    try:
        projected_file, frozen = out / "projected-input.net.xml", out / "frozen-input.net.xml"
        ET.ElementTree(projected).write(projected_file, encoding="utf-8", xml_declaration=True)
        report["geometry_input"] = prepare_geometry_frozen_input(source_file=projected_file, output_file=frozen)
        patch, programs = ET.Element("connections"), ET.Element("tlLogics")
        programs.append(deepcopy(before.find(f"tlLogic[@id='{NODE}']")))
        access = loaded_turn_permissions(before)
        for key, row in _target_rows(before).items():
            ET.SubElement(patch, "connection", _connection_attributes(before, key, row, access))
            if key in COUNTER:
                ET.SubElement(programs, "connection", {**{k: row.get(k) for k in ("from", "fromLane", "to", "toLane")}, "tl": NODE, "linkIndex": str(COUNTER[key])})
        patch_file, program_file = out / "connections.con.xml", out / "programs.tll.xml"
        ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
        ET.ElementTree(programs).write(program_file, encoding="utf-8", xml_declaration=True)
        incident = {e.get("id") for e in before.findall("edge") if not e.get("function") and NODE in {e.get("from"), e.get("to")}}
        candidate = out / "candidate.net.xml"
        execution, conversion = run_scoped_native_conversion(command=[str(netconvert_binary), "--sumo-net-file", str(frozen),
            "--connection-files", str(patch_file), "--tllogic-files", str(program_file), "--offset.disable-normalization", "true", "--precision", "12", "--output-file", str(candidate)],
            mutable_nodes={NODE}, mutable_edges=incident, output_dir=out, prefix="partial", timeout_seconds=120, merge_source_file=source)
        report["execution"], report["conversion"] = execution if isinstance(execution, dict) else execution.to_dict(), conversion
        if conversion["status"] != "local":
            raise ValueError("Require a single-junction local conversion.")
        report["restoration"] = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate, mutable_junction_ids={NODE}, mutable_edge_ids=set(), expand_mutable_edge_endpoints=False)
        if report["restoration"]["status"] != "pass":
            raise ValueError("Exterior restoration failed.")
        after = ET.parse(candidate).getroot()
        first, last = before.find(f"junction[@id='{NODE}']"), after.find(f"junction[@id='{NODE}']")
        shapes = [LineString([tuple(map(float, p.split(",")[:2])) for p in r.get("shape").split()]) for r in (first, last)]
        if shapes[0].hausdorff_distance(shapes[1]) > 1e-6 or any(abs(float(first.get(a, "0")) - float(last.get(a, "0"))) > 1e-6 for a in ("x", "y", "z")):
            raise ValueError("Native target geometry changed beyond rounding.")
        for attr in ("x", "y", "z", "shape", "customShape"):
            last.attrib.pop(attr, None)
            if first.get(attr) is not None:
                last.set(attr, first.get(attr))
        subset, _ = local_conversion_input(after, {NODE}, incident)
        after = merge_local_conversion(before, subset, conversion)
        ET.ElementTree(after).write(candidate, encoding="utf-8", xml_declaration=True)
        native = ET.parse(conversion["native_output"]["path"]).getroot()
        report["checks"], report["geometry"] = _verify_candidate(before, after, native)
        report["request_mapping"] = [{"movement": key, **value} for key, value in _movement_requests(after).items()]
        report["checks"]["external_control_bytes_exact"] = copied_control.read_bytes() == control.read_bytes()
        report["checks"]["source_files_immutable"] = all(file_sha256(Path(p)) == h for p, h in hashes.items())
        if all(report["checks"].values()):
            final = out / "network.net.xml"
            final.write_bytes(candidate.read_bytes())
            report.update(status="review_required", candidate_file=str(final), candidate_sha256=file_sha256(final))
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    report["source_unchanged"] = all(file_sha256(Path(p)) == h for p, h in hashes.items())
    write_json_atomic(out / "manifest.json", report)
    return report
