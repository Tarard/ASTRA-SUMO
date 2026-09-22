"""Evidence-bounded lane-connection repairs for the Hamburg five-light corridor."""

from __future__ import annotations

import json
import math
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import build_network_connection_mode_audit

REPAIR_SCHEMA = "torii.hamburg-lane-connection-repair/v1"

HAMBURG_DIVERGE_LANE_SHAPE = ((609.03, 607.10), (632.40, 601.61), (714.80, 583.52))

LSA119_OLD_STATE_INDICES = (0, 1, 2, 7, 8, 9, 10, 11, 14, 15, 17)

LSA119_ONE_TO_ONE_LINKS = (
    ("43866090#0", "0", "486864294", "0"),
    ("43866090#0", "1", "486864294", "1"),
    ("603103445#0", "0", "141050975", "0"),
    ("186713422#0", "1", "363112756#1", "0"),
    ("186713422#0", "2", "363112756#1", "1"),
    ("186713422#0", "3", "363112756#1", "2"),
    ("1227325137", "0", "486864294", "0"),
    ("1227325137", "1", "486864294", "1"),
    ("24483344#0", "0", "141050975", "2"),
    ("24483344#0", "1", "141050975", "3"),
    ("1068722010", "0", "363112756#1", "0"),
)

LSA119_DELETED_FANOUTS = (
    ("603103445#0", "0", "141050975", "1"),
    ("603103445#0", "0", "141050975", "2"),
    ("603103445#0", "0", "141050975", "3"),
    ("603103445#0", "0", "141050975", "4"),
    ("24483344#0", "0", "141050975", "0"),
    ("24483344#0", "0", "141050975", "1"),
    ("24483344#0", "1", "141050975", "2"),
    ("24483344#0", "1", "141050975", "4"),
    ("1068722010", "0", "363112756#1", "1"),
    ("1068722010", "0", "363112756#1", "2"),
)


def build_hamburg_lane_connection_repair(
    *,
    source_net: Path | str,
    output_dir: Path | str,
    connection_patch_file: Path | str | None = None,
    expected_connection_patch_sha256: str | None = None,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, Any]:
    """Rebuild the reviewed Hamburg lane links as a separate SUMO candidate."""
    source = Path(source_net).expanduser().resolve(strict=True)
    source_root = ET.parse(source).getroot()
    supplied_patch = (
        Path(connection_patch_file).expanduser().resolve(strict=True)
        if connection_patch_file is not None
        else None
    )
    added_links: set[tuple[str, str, str, str]] = set()
    deleted_links: set[tuple[str, str, str, str]] = set()
    if supplied_patch is not None:
        added_links, deleted_links = _validate_connection_patch(
            source_root, supplied_patch, expected_connection_patch_sha256
        )
    elif expected_connection_patch_sha256 is not None:
        raise ValueError("connection_patch_file is required with its SHA-256")
    destination = Path(output_dir).expanduser().resolve()
    if destination.exists():
        raise ValueError("output_dir must not already exist")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    destination.mkdir(parents=True)
    source_hash = file_sha256(source)

    plain_prefix = destination / "source"
    export = _command_result(
        command_runner(
            [
                str(netconvert_binary),
                "--sumo-net-file",
                str(source),
                "--plain-output-prefix",
                str(plain_prefix),
            ],
            cwd=destination,
            timeout_seconds=timeout_seconds,
        )
    ) if supplied_patch is None else {"status": "not_run", "stdout": "", "stderr": ""}
    _write_log(destination / "plain-export.log", export)
    source_tll = destination / "source.tll.xml"
    if supplied_patch is None and (export.get("status") != "pass" or not source_tll.is_file()):
        raise ValueError("netconvert could not export the source traffic-light logic")

    connection_patch = destination / "lane-connections.con.xml"
    geometry_patch = destination / "diverge-geometry.edg.xml"
    repaired_tll = destination / "repaired.tll.xml"
    if supplied_patch is None:
        write_hamburg_lane_connection_patch(connection_patch)
        write_hamburg_diverge_geometry_patch(geometry_patch)
        write_repaired_lsa119_tllogic(source_tll, repaired_tll)
    else:
        shutil.copyfile(supplied_patch, connection_patch)

    candidate = destination / "hamburg-lane-connection-repaired.net.xml"
    compile_result = _command_result(
        command_runner(
            [
                str(netconvert_binary),
                "--sumo-net-file",
                str(source),
                "--connection-files",
                str(connection_patch),
                *(
                    ["--edge-files", str(geometry_patch), "--tllogic-files", str(repaired_tll)]
                    if supplied_patch is None else []
                ),
                "--offset.disable-normalization",
                "true",
                "--geometry.check-overlap",
                "0.1",
                "--aggregate-warnings",
                "100",
                "--output-file",
                str(candidate),
            ],
            cwd=destination,
            timeout_seconds=timeout_seconds,
        )
    )
    _write_log(destination / "netconvert.log", compile_result)
    compiled = compile_result.get("status") == "pass" and candidate.is_file()
    load = (
        _command_result(
            command_runner(
                [
                    str(sumo_binary),
                    "--net-file",
                    str(candidate),
                    "--begin",
                    "0",
                    "--end",
                    "1",
                    "--no-step-log",
                    "true",
                ],
                cwd=destination,
                timeout_seconds=timeout_seconds,
            )
        )
        if compiled
        else {"status": "not_run", "returncode": None, "stdout": "", "stderr": ""}
    )
    _write_log(destination / "sumo-load.log", load)

    candidate_root = ET.parse(candidate).getroot() if compiled else None
    expected_links = set(LSA119_ONE_TO_ONE_LINKS)
    candidate_links = (
        {
            (
                row.get("from", ""),
                row.get("fromLane", ""),
                row.get("to", ""),
                row.get("toLane", ""),
            )
            for row in candidate_root.findall("connection")
            if row.get("tl") == "LSA119_part0"
        }
        if candidate_root is not None
        else set()
    )
    source_cycle = _cycle_seconds(source_root, "LSA119_part0")
    candidate_cycle = (
        _cycle_seconds(candidate_root, "LSA119_part0")
        if candidate_root is not None
        else None
    )
    compile_log = str(compile_result.get("stdout", "")) + str(
        compile_result.get("stderr", "")
    )
    target_overlap_absent = not (
        "234421319" in compile_log
        and "24483344#0" in compile_log
        and "overlap" in compile_log.lower()
    )
    gates = {
        "source_immutable": "pass" if file_sha256(source) == source_hash else "blocked",
        "netconvert": "pass" if compiled else "blocked",
        "sumo_load": "pass" if load.get("status") == "pass" else "blocked",
        "lsa119_one_to_one_links": "pass" if candidate_links == expected_links else "blocked",
        "lsa119_cycle_preserved": (
            "pass"
            if source_cycle is not None and candidate_cycle == source_cycle
            else "blocked"
        ),
        "reviewed_diverge_overlap": "pass" if target_overlap_absent else "blocked",
    }
    if supplied_patch is not None:
        gates = {key: gates[key] for key in ("source_immutable", "netconvert", "sumo_load")}
        expected_pairs = (_external_connection_pairs(source_root) - deleted_links) | added_links
        gates["exact_connection_patch"] = (
            "pass" if candidate_root is not None and _external_connection_pairs(candidate_root) == expected_pairs else "blocked"
        )
        gates["connection_patch_immutable"] = (
            "pass" if file_sha256(supplied_patch) == file_sha256(connection_patch) == expected_connection_patch_sha256 else "blocked"
        )
    connection_audit = (
        build_network_connection_mode_audit(
            candidate, output_dir=destination / "connection-audit", endpoint_tolerance_m=0.1
        ) if compiled else None
    )
    gates["internal_path_continuity"] = (
        "pass" if connection_audit is not None and connection_audit["status"] != "fail" and connection_audit["structural_failure_count"] == 0 else "blocked"
    )
    passed = all(value == "pass" for value in gates.values())
    manifest_file = destination / "manifest.json"
    report: dict[str, Any] = {
        "schema": REPAIR_SCHEMA,
        "status": "review_required" if passed else "blocked",
        "claim_status": "diagnostic-demo" if passed else "construction-invalid",
        "source_network": {"path": str(source), "sha256": source_hash},
        "candidate_network": (
            {"path": str(candidate), "sha256": file_sha256(candidate)}
            if candidate.is_file()
            else None
        ),
        "edit_scope": {
            "signalized_junction": "LSA119_part0",
            "deleted_fanout_count": len(LSA119_DELETED_FANOUTS),
            "expected_one_to_one_link_count": len(LSA119_ONE_TO_ONE_LINKS),
            "diverge_edge": "234421319",
        } if supplied_patch is None else {
            "mode": "explicit_connection_patch",
            "added_connections": sorted(added_links),
            "deleted_connections": sorted(deleted_links),
        },
        "cycles": {"source_seconds": source_cycle, "candidate_seconds": candidate_cycle} if supplied_patch is None else None,
        "gates": gates,
        "commands": {"plain_export": export, "netconvert": compile_result, "sumo_load": load},
        "artifacts": {
            "connection_patch": str(connection_patch),
            "connection_patch_sha256": file_sha256(connection_patch),
            "geometry_patch": str(geometry_patch) if supplied_patch is None else None,
            "repaired_tll": str(repaired_tll) if supplied_patch is None else None,
            "connection_audit": connection_audit["report_file"] if connection_audit else None,
        },
        "claim_boundary": (
            "This candidate repairs only the reviewed LSA119 lane links and the reviewed 266199070 diverge. "
            "Other corridor fanouts remain review items. Demand must be recalibrated on this candidate."
        ) if supplied_patch is None else (
            "This candidate applies only the explicit lane-connection patch. "
            "Connection continuity does not prove field turn permissions or signal timing. "
            "Recheck routes and signals before traffic experiments."
        ),
    }
    manifest_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report["manifest_file"] = str(manifest_file)
    report["manifest_sha256"] = file_sha256(manifest_file)
    return report


def _external_connection_pairs(root: ET.Element) -> set[tuple[str, str, str, str]]:
    return {
        (row.get("from", ""), row.get("fromLane", ""), row.get("to", ""), row.get("toLane", ""))
        for row in root.findall("connection") if not row.get("from", "").startswith(":")
    }


def _validate_connection_patch(
    source_root: ET.Element, patch: Path, expected_sha256: str | None,
) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    if not expected_sha256 or file_sha256(patch) != expected_sha256:
        raise ValueError("connection patch SHA-256 does not match")
    root = ET.parse(patch).getroot()
    if root.tag != "connections" or not len(root):
        raise ValueError("connection patch must contain explicit lane connections or deletions")
    edges = {edge.get("id"): edge for edge in source_root.findall("edge") if not edge.get("function")}
    changes: dict[str, set[tuple[str, str, str, str]]] = {"connection": set(), "delete": set()}
    for row in root:
        if row.tag not in changes or set(row.attrib) != {"from", "to", "fromLane", "toLane"}:
            raise ValueError("connection patch requires from, to, fromLane, and toLane only")
        pair = (row.get("from", ""), row.get("fromLane", ""), row.get("to", ""), row.get("toLane", ""))
        if pair in changes[row.tag]:
            raise ValueError("connection patch contains duplicate lane pairs")
        for edge_id, lane_index in (pair[:2], pair[2:]):
            if edge_id not in edges or lane_index not in {lane.get("index") for lane in edges[edge_id].findall("lane")}:
                raise ValueError("connection patch references an unknown external lane")
        if not edges[pair[0]].get("to") or edges[pair[0]].get("to") != edges[pair[2]].get("from"):
            raise ValueError("connection patch lanes do not meet at one junction")
        changes[row.tag].add(pair)
    if changes["connection"] & changes["delete"]:
        raise ValueError("connection patch both adds and deletes the same lane pair")
    if not changes["delete"].issubset(_external_connection_pairs(source_root)):
        raise ValueError("connection patch deletes an absent lane pair")
    return changes["connection"], changes["delete"]


def write_hamburg_lane_connection_patch(path: Path) -> None:
    root = ET.Element("connections")
    for source, source_lane, target, target_lane in LSA119_DELETED_FANOUTS:
        ET.SubElement(
            root,
            "delete",
            {
                "from": source,
                "to": target,
                "fromLane": source_lane,
                "toLane": target_lane,
            },
        )
    ET.SubElement(
        root,
        "connection",
        {
            "from": "24483344#0",
            "to": "141050975",
            "fromLane": "0",
            "toLane": "2",
        },
    )
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)


def write_hamburg_diverge_geometry_patch(path: Path) -> None:
    root = ET.Element("edges")
    edge = ET.SubElement(root, "edge", id="234421319")
    ET.SubElement(
        edge,
        "lane",
        index="0",
        shape=" ".join(f"{x:.2f},{y:.2f}" for x, y in HAMBURG_DIVERGE_LANE_SHAPE),
    )
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)


def write_repaired_lsa119_tllogic(source_file: Path, output_file: Path) -> None:
    tree = ET.parse(Path(source_file).resolve(strict=True))
    root = tree.getroot()
    logic = root.find("tlLogic[@id='LSA119_part0']")
    if logic is None:
        raise ValueError("traffic-light file has no LSA119_part0 program")
    for phase in logic.findall("phase"):
        state = phase.attrib.get("state", "")
        if len(state) != 20:
            raise ValueError("LSA119_part0 source state must contain 20 links")
        phase.set("state", "".join(state[index] for index in LSA119_OLD_STATE_INDICES))
    for connection in list(root.findall("connection")):
        if connection.attrib.get("tl") == "LSA119_part0":
            root.remove(connection)
    for link_index, (source, source_lane, target, target_lane) in enumerate(
        LSA119_ONE_TO_ONE_LINKS
    ):
        ET.SubElement(
            root,
            "connection",
            {
                "from": source,
                "to": target,
                "fromLane": source_lane,
                "toLane": target_lane,
                "tl": "LSA119_part0",
                "linkIndex": str(link_index),
            },
        )
    destination = Path(output_file).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree, space="    ")
    tree.write(destination, encoding="utf-8", xml_declaration=True)


def _command_result(value: object) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    raise TypeError("command runner must return a mapping or CommandResult")


def _write_log(path: Path, result: dict[str, Any]) -> None:
    path.write_text(
        str(result.get("stdout", "")) + str(result.get("stderr", "")),
        encoding="utf-8",
    )


def _cycle_seconds(root: ET.Element, tls_id: str) -> float | None:
    logic = root.find(f"tlLogic[@id='{tls_id}']")
    if logic is None:
        return None
    return sum(float(phase.get("duration", "0")) for phase in logic.findall("phase"))


__all__ = [
    "LSA119_DELETED_FANOUTS",
    "LSA119_ONE_TO_ONE_LINKS",
    "REPAIR_SCHEMA",
    "build_hamburg_lane_connection_repair",
    "write_hamburg_diverge_geometry_patch",
    "write_hamburg_lane_connection_patch",
    "write_repaired_lsa119_tllogic",
]
