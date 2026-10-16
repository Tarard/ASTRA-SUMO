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


def rebuild_custom_path_endpoints(*, plan, candidate_file, output_dir, command_runner, timeout_seconds):
    """Reanchor only broken endpoint paths, then let native SUMO recompute their junctions."""
    candidate_file = Path(candidate_file).resolve(strict=True)
    if candidate_file.samefile(Path(plan["source"]["path"])):
        raise ValueError("Custom-path repair must not replace its source.")
    nodes = set(plan["mutable_junction_ids"])
    command = list(plan["command"])
    existing = plan["patches"].get("connections")
    patch = ET.parse(existing["path"]).getroot() if existing else ET.Element("connections")
    attempts = []
    remaining = None
    for iteration in range(3):
        root = ET.parse(candidate_file).getroot()
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
            rewritten = reanchor_spatial_shape(old, start=pieces[0][-1], end=pieces[-1][0])
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
            changes.append({"connection": list(key), "internal_lane_ids": path[1:-1],
                            "before_shape": old, "after_shape": rewritten})
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
        remaining = _gap_movements(ET.parse(candidate_file).getroot(), nodes)
    return {"attempts": attempts, "remaining_endpoint_gap_count": len(remaining),
            "remaining_endpoint_gaps": remaining,
            "claim_boundary": "Custom curves follow the edited road endpoints. Native conversion owns the resulting paths and junction requests. No field-accuracy claim."}
