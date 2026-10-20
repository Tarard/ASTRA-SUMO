"""Recompile custom movement curves after their road endpoints move."""

from copy import deepcopy
from pathlib import Path
import math
import time
import xml.etree.ElementTree as ET

from ..artifact_io import copy_file_atomic
from ..candidate_contracts import file_sha256
from ..connection_mode_audit import audit_network_connection_mode
from ..routeability_audit import _result_to_dict
from ..source_movement_support import _index, _candidate_modes
from .geometry import reanchor_spatial_shape


def _gap_movements(root, nodes):
    audit = audit_network_connection_mode(root, junction_ids=sorted(nodes), endpoint_tolerance_m=0.1)
    return [row for junction in audit["junctions"] for row in junction["connection_mode_audit"]["movement_checks"]
            if any(value.startswith("path_endpoint_gap:") for value in row.get("failures", []))]


def _end_direction_change(before, after, *, start, reference_start=None):
    def direction(points, at_start):
        segments = list(zip(points, points[1:]))
        for left, right in segments if at_start else reversed(segments):
            length = math.dist(left, right)
            if length > 1e-9:
                return tuple((b - a) / length for a, b in zip(left, right))
        return None
    a, b = direction(before, start if reference_start is None else reference_start), direction(after, start)
    return math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))) if a and b else None


def _maximum_vertex_turn(points):
    angles = [_end_direction_change([a, b], [b, c], start=True) for a, b, c in zip(points, points[1:], points[2:])]
    return max((angle for angle in angles if angle is not None), default=0.0)


def rebuild_custom_path_endpoints(*, plan, candidate_file, output_dir, command_runner, timeout_seconds):
    """Reanchor only broken endpoint paths, then let native SUMO recompute their junctions."""
    candidate_file = Path(candidate_file).resolve(strict=True)
    if candidate_file.samefile(Path(plan["source"]["path"])):
        raise ValueError("Custom-path repair must not replace its source.")
    initial_hash = file_sha256(candidate_file)
    nodes = set(plan["mutable_junction_ids"])
    source_root = ET.parse(plan["source"]["path"]).getroot()
    source_lanes = {lane.get("id"): lane for edge in source_root.findall("edge")
                    if not edge.get("function") for lane in edge.findall("lane")}
    count_edit = plan["operation"]["operation"] == "set_lane_count"
    if count_edit:
        from ..network_geometry_freeze import _lane_count_mutable_geometry_edges
        geometry_edges = _lane_count_mutable_geometry_edges(source_root, plan["operation"]["params"]["edge_id"],
                                                          nodes, set(plan["mutable_edge_ids"]))

    def endpoint_view(root):
        if not count_edit:
            return root
        # Use retained road endpoints, rather than temporary native clipping.
        result = deepcopy(root)
        for edge in result.findall("edge"):
            if edge.get("function") or edge.get("id") in geometry_edges:
                continue
            for lane in edge.findall("lane"):
                original = source_lanes.get(lane.get("id"))
                if original is None:
                    continue
                lane.set("shape", original.get("shape"))
                for key in ("width", "endOffset"):
                    if key in original.attrib:
                        lane.set(key, original.get(key))
                    else:
                        lane.attrib.pop(key, None)
        return result

    command = list(plan["command"])
    existing = plan["patches"].get("connections")
    patch = ET.parse(existing["path"]).getroot() if existing else ET.Element("connections")
    attempts = []
    remaining = None
    for iteration in range(3):
        root = endpoint_view(ET.parse(candidate_file).getroot())
        gaps = _gap_movements(root, nodes)
        remaining = gaps
        if not gaps:
            break
        index = _index(root)
        changes = []
        for row in gaps:
            key = (row["from"], int(row["fromLane"]), row["to"], int(row["toLane"]))
            connection = next((item for pair, _, _, item in index["movements"] if pair == key), None)
            if connection is None:
                continue
            start, end = index["keys"][key[:2]], index["keys"][key[2:]]
            _, path = _candidate_modes(index, start, end, connection)
            if len(path) < 3:
                continue
            pieces = [[tuple(map(float, token.split(","))) for token in index["lanes"][lane][1].get("shape", "").split()]
                      for lane in path]
            if any(len(shape) < 2 for shape in pieces):
                continue
            dimensions = max(len(point) for shape in pieces for point in shape)
            pieces = [[point + (0.0,) * (dimensions - len(point)) for point in shape] for shape in pieces]
            if any(math.dist(left[-1], right[0]) > 0.1 for left, right in zip(pieces[1:-1], pieces[2:-1])):
                continue
            old = [point for shape in pieces[1:-1] for point in shape]
            rewritten = reanchor_spatial_shape(old, start=pieces[0][-1], end=pieces[-1][0], preserve_end_directions=True)
            shape = " ".join(",".join(f"{value:.12f}" for value in point) for point in rewritten)
            target = next((item for item in patch.findall("connection") if
                tuple(item.get(name) for name in ("from", "fromLane", "to", "toLane")) == tuple(map(str, key))), None)
            if target is None:
                attrs = {name: connection.get(name) for name in (
                    "from", "fromLane", "to", "toLane", "allow", "disallow", "speed", "length", "type",
                    "tl", "linkIndex", "linkIndex2", "keepClear", "visibility", "uncontrolled", "indirect",
                    "changeLeft", "changeRight") if connection.get(name) is not None}
                target = ET.SubElement(patch, "connection", attrs)
            target.set("shape", shape)
            length = sum(math.dist(a, b) for a, b in zip(old, old[1:]))
            displacement = max(math.dist(old[0], rewritten[0]), math.dist(old[-1], rewritten[-1]))
            changes.append({"connection": list(key), "internal_lane_ids": path[1:-1],
                "before_shape": old, "after_shape": rewritten,
                "maximum_endpoint_correction_m": displacement,
                "geometric_length_before_m": length,
                "geometric_length_after_m": sum(math.dist(a, b) for a, b in zip(rewritten, rewritten[1:])),
                "correction_to_curve_length_ratio": displacement / length if length > 0 else None,
                "entry_direction_change_deg": _end_direction_change(old, rewritten, start=True),
                "exit_direction_change_deg": _end_direction_change(old, rewritten, start=False),
                "maximum_vertex_turn_before_deg": _maximum_vertex_turn(old),
                "maximum_vertex_turn_after_deg": _maximum_vertex_turn(rewritten),
                "entry_alignment_with_road_deg": _end_direction_change(pieces[0], rewritten, start=True, reference_start=False),
                "exit_alignment_with_road_deg": _end_direction_change(pieces[-1], rewritten, start=False, reference_start=True),
                "declared_logical_length_m": float(connection.get("length")) if connection.get("length") is not None else None,
                "logical_length_policy": "preserve_explicit_declaration" if connection.get("length") is not None else "native_geometry_length"})
        if not changes:
            break
        before = Path(output_dir) / f"native-before-path-repair-{iteration}.net.xml"
        patch_file = Path(output_dir) / f"native-path-repair-{iteration}.con.xml"
        if before.exists() or patch_file.exists():
            raise ValueError("Use a new custom-path repair directory.")
        copy_file_atomic(candidate_file, before)
        with patch_file.open("xb") as stream:
            stream.write(ET.tostring(deepcopy(patch), encoding="utf-8", xml_declaration=True))
        if "--connection-files" in command:
            command[command.index("--connection-files") + 1] = str(patch_file.resolve())
        else:
            command.extend(("--connection-files", str(patch_file.resolve())))
        candidate_file.unlink()
        started = time.perf_counter()
        result = _result_to_dict(command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds))
        created = candidate_file.is_file()
        succeeded = result.get("status") == "pass" and result.get("returncode") == 0 and created
        attempts.append({"status": "pass" if succeeded else "fail", "output_created": created,
            "command": list(command), "result": result, "changes": changes,
            "reanchor_method": "smoothstep_with_retained_end_segments",
            "output_sha256": file_sha256(candidate_file) if succeeded else None,
            "elapsed_seconds": time.perf_counter() - started,
            "before": {"path": str(before.resolve()), "sha256": file_sha256(before)},
            "patch": {"path": str(patch_file.resolve()), "sha256": file_sha256(patch_file)}})
        if not succeeded:
            if Path(candidate_file).is_file():
                failed = Path(output_dir) / f"native-failed-path-repair-{iteration}.net.xml"
                copy_file_atomic(candidate_file, failed)
                attempts[-1]["failed_output"] = {"path": str(failed.resolve()), "sha256": file_sha256(failed)}
            copy_file_atomic(before, candidate_file)
            break
        remaining = None
    if remaining is None:
        remaining = _gap_movements(endpoint_view(ET.parse(candidate_file).getroot()), nodes)
    return {"attempts": attempts, "initial_output_sha256": initial_hash, "remaining_endpoint_gap_count": len(remaining),
            "remaining_endpoint_gaps": remaining,
            "endpoint_geometry_reference": "source_geometry_after_restoration" if count_edit else "native_output",
            "native_endpoint_gaps_before_restoration": _gap_movements(ET.parse(candidate_file).getroot(), nodes) if count_edit else [],
            "claim_boundary": "Custom curves follow the edited road endpoints. Native conversion owns the resulting paths and junction requests. No field-accuracy claim."}


def validate_native_path_repair_chain(command, record, final_output):
    """Check every native repair command and the saved output-hash chain."""
    if not record.get("attempts"):
        return
    def without_connection_file(tokens):
        result = list(tokens)
        if result.count("--connection-files") > 1:
            raise ValueError("A repair command has duplicate connection inputs.")
        if "--connection-files" in result:
            index = result.index("--connection-files")
            del result[index:index + 2]
        return result
    expected = without_connection_file(command)
    previous = record["initial_output_sha256"]
    for attempt in record["attempts"]:
        if attempt["status"] != "pass" or attempt["result"].get("status") != "pass" or attempt["result"].get("returncode") != 0:
            raise ValueError("The native repair chain contains a failed command.")
        before, patch = Path(attempt["before"]["path"]), Path(attempt["patch"]["path"])
        if file_sha256(before) != attempt["before"]["sha256"] or attempt["before"]["sha256"] != previous:
            raise ValueError("The native repair input does not match the previous output.")
        if file_sha256(patch) != attempt["patch"]["sha256"]:
            raise ValueError("The native repair patch changed.")
        current = attempt["command"]
        if "--connection-files" not in current or current.index("--connection-files") + 1 >= len(current):
            raise ValueError("The native repair command has no connection-patch path.")
        if without_connection_file(current) != expected or Path(current[current.index("--connection-files") + 1]).resolve() != patch.resolve():
            raise ValueError("The native repair command differs beyond its recorded connection patch.")
        previous = attempt["output_sha256"]
    final_path = Path(final_output["path"])
    if file_sha256(final_path) != final_output["sha256"] or final_output["sha256"] != previous:
        raise ValueError("The final native output does not match the repair chain.")
