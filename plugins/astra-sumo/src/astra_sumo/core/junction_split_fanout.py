"""Compile an explicitly selected split fanout as complete internal paths."""

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


def _curve(root, row):
    index = _index(root)
    key = _key(row)
    _, path = _candidate_modes(index, index["keys"][key[:2]], index["keys"][key[2:]], row)
    if len(path) < 3:
        raise ValueError("A selected movement has no complete internal path.")
    return LineString([tuple(map(float, point.split(",")[:2])) for identity in path[1:-1]
                       for point in index["lanes"][identity][1].get("shape", "").split()])


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
