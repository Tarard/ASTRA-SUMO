"""Compare permission-only edits with whole-network native rebuilds."""

from __future__ import annotations

from pathlib import Path
import math
import xml.etree.ElementTree as ET

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .network_geometry_freeze import compare_frozen_geometry, prepare_geometry_frozen_input
from .osm_access import correct_osm_access_permissions, _non_permission_state
from .sumo_commands import run_sumo_load_audit
from .junction_rebuild.restoration import restore_off_scope_netconvert_artifacts


def run_permission_geometry_probe(*, source_net: Path, source_osm: Path, output_dir: Path,
                                  netconvert_binary: str, sumo_binary: str,
                                  output_precision: int | None = None,
                                  timeout_seconds: float = 240.0) -> dict:
    """Apply one derived permission plan to the same immutable network three ways."""
    network, osm, destination = source_net.resolve(strict=True), source_osm.resolve(strict=True), output_dir.resolve()
    if destination.exists() or network.is_relative_to(destination) or osm.is_relative_to(destination):
        raise ValueError("Use a new output directory separate from both sources.")
    identities = {str(path): file_sha256(path) for path in (network, osm)}
    destination.mkdir(parents=True)
    request = {"source_net": str(network), "source_osm": str(osm), "input_sha256": identities,
               "netconvert_binary": netconvert_binary, "sumo_binary": sumo_binary,
               "timeout_seconds": timeout_seconds, "output_precision": output_precision}
    write_json_atomic(destination / "request.json", request, sort_keys=True)
    work = destination / "permission-only.net.xml"
    copy_file_atomic(network, work)
    edit = correct_osm_access_permissions(osm_file=osm, net_file=work, output_dir=destination / "permission-edit",
                                         netconvert_binary=netconvert_binary, sumo_binary=sumo_binary,
                                         timeout_seconds=timeout_seconds)
    if not edit.get("changed_lanes") or edit["status"] not in {"pass", "review_required"}:
        raise ValueError(f"The permission-only case did not produce a validated edit: {edit.get('status')}")
    patch = Path(edit["edge_patch"]["path"])
    changed = {row["lane_id"] for row in edit["changed_lanes"]}
    parent_root = ET.parse(network).getroot()
    protected = _non_permission_state(parent_root, changed)
    cases = {}

    def record(name, candidate, load):
        geometry = compare_frozen_geometry(source_file=network, candidate_file=candidate)
        state_preserved = _non_permission_state(ET.parse(candidate).getroot(), changed) == protected
        cases[name] = {"network": {"path": str(candidate), "sha256": file_sha256(candidate)},
                       "geometry": geometry, "sumo_load": load,
                       "all_non_permission_network_state_preserved": state_preserved}

    record("permission_only", work, edit["load_audit"])
    for name in ("native_no_edit", "native_rebuild", "frozen_native_rebuild"):
        case_dir = destination / name
        case_dir.mkdir()
        input_net = network
        if name == "frozen_native_rebuild":
            input_net = case_dir / "frozen-input.net.xml"
            write_json_atomic(case_dir / "freeze.json", prepare_geometry_frozen_input(
                source_file=network, output_file=input_net), sort_keys=True)
        candidate = case_dir / "candidate.net.xml"
        command = [netconvert_binary, "--sumo-net-file", str(input_net),
                   "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25",
                   "--output-file", str(candidate)]
        if name != "native_no_edit":
            command.extend(("--edge-files", str(patch)))
        if output_precision is not None:
            command.extend(("--precision", str(output_precision)))
        result = run_command(command, cwd=case_dir, timeout_seconds=timeout_seconds).to_dict()
        write_json_atomic(case_dir / "command.json", result, sort_keys=True)
        if result["status"] != "pass" or not candidate.is_file():
            cases[name] = {"status": "blocked", "command_result": result}
            continue
        load = run_sumo_load_audit(net_file=candidate, output_dir=case_dir / "load-audit",
                                  sumo_binary=sumo_binary, timeout_seconds=timeout_seconds)
        record(name, candidate, load)
    if identities != {str(path): file_sha256(path) for path in (network, osm)}:
        raise ValueError("A frozen input changed during the comparison.")
    direct = cases["permission_only"]
    verified = (direct["all_non_permission_network_state_preserved"] and direct["geometry"]["status"] == "pass"
                and direct["sumo_load"]["status"] == "pass")
    complete = all("geometry" in case for case in cases.values())
    report = {"schema": "astra.permission-geometry-preservation-probe/v1",
              "status": "pass" if verified and complete else "blocked", "inputs": identities,
              "comparison_complete": complete, "permission_only_preservation_verified": verified,
              "permission_patch": {"path": str(patch), "sha256": file_sha256(patch)},
              "changed_lane_count": len(changed), "cases": cases,
              "new_mode_connectivity_review_required": edit["connectivity_review_required"],
              "claim_boundary": "This verifies preservation for permission-only edits. It does not validate newly allowed mode paths or geometry/topology edits."}
    write_json_atomic(destination / "comparison.json", report, sort_keys=True)
    return report


def run_scoped_lane_width_probe(*, source_net: Path, lane_id: str, width_m: float, output_dir: Path,
                                netconvert_binary: str, sumo_binary: str,
                                timeout_seconds: float = 240.0) -> dict:
    """Test fixed boundaries when admitting a compiled local width change.

    The native compiler still processes the whole input. Only a candidate with
    unchanged outside geometry and fixed boundary endpoints passes this probe.
    Its field geometry and safety remain subject to independent review.
    """
    if not math.isfinite(width_m) or width_m <= 0:
        raise ValueError("Lane width must be finite and positive.")
    source, destination = source_net.resolve(strict=True), output_dir.resolve()
    if destination.exists():
        raise ValueError("Use a new probe directory.")
    source_hash = file_sha256(source)
    root = ET.parse(source).getroot()
    matches = [(edge, lane) for edge in root.findall("edge") if not edge.get("function")
               for lane in edge.findall("lane") if lane.get("id") == lane_id]
    if len(matches) != 1:
        raise ValueError("Select one existing external lane.")
    edge, lane = matches[0]
    nodes = frozenset((edge.get("from"), edge.get("to")))
    roads = frozenset(e.get("id") for e in root.findall("edge") if not e.get("function")
                      and (e.get("from") in nodes or e.get("to") in nodes))
    destination.mkdir(parents=True)
    request = {"source_net": str(source), "source_sha256": source_hash, "lane_id": lane_id, "width_m": width_m,
               "mutable_junction_ids": sorted(nodes), "mutable_edge_ids": sorted(roads),
               "scope_rule": "Target road endpoints and their incident roads; remote endpoints remain fixed."}
    write_json_atomic(destination / "request.json", request, sort_keys=True)
    source_load = run_sumo_load_audit(net_file=source, output_dir=destination / "source-load",
                                    sumo_binary=sumo_binary, timeout_seconds=timeout_seconds)
    report = {"schema": "astra.scoped-lane-width-probe/v1", "status": "blocked", "request": request,
              "source_load": source_load, "claim_status": "geometry_preservation_only"}
    report_file = destination / "comparison.json"
    if source_load["status"] != "pass":
        write_json_atomic(report_file, report, sort_keys=True)
        return report
    patch = ET.Element("edges")
    ET.SubElement(ET.SubElement(patch, "edge", id=edge.get("id")), "lane", index=lane.get("index"), width=str(width_m))
    patch_file = destination / "width.edg.xml"
    ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
    proposal = destination / "native-proposal.net.xml"
    command = [netconvert_binary, "--sumo-net-file", str(source), "--edge-files", str(patch_file),
               "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25",
               "--output-file", str(proposal)]
    result = run_command(command, cwd=destination, timeout_seconds=timeout_seconds).to_dict()
    write_json_atomic(destination / "command.json", result, sort_keys=True)
    report["command_result"] = result
    if result["status"] != "pass" or not proposal.is_file():
        write_json_atomic(report_file, report, sort_keys=True)
        return report
    report["before_admission"] = compare_frozen_geometry(source_file=source, candidate_file=proposal,
                                                         mutable_edge_ids=roads, mutable_junction_ids=nodes)
    candidate = destination / "candidate.net.xml"
    copy_file_atomic(proposal, candidate)
    admission = restore_off_scope_netconvert_artifacts(source_file=source, target_file=candidate,
        mutable_edge_ids=set(roads), mutable_junction_ids=set(nodes), expand_mutable_edge_endpoints=False)
    after = compare_frozen_geometry(source_file=source, candidate_file=candidate,
                                    mutable_edge_ids=roads, mutable_junction_ids=nodes)
    candidate_root = ET.parse(candidate).getroot()
    changed = next((item for candidate_edge in candidate_root.findall("edge")
                    for item in candidate_edge.findall("lane") if item.get("id") == lane_id), None)
    width_applied = changed is not None and float(changed.get("width", "3.2")) == width_m
    load = run_sumo_load_audit(net_file=candidate, output_dir=destination / "candidate-load",
                              sumo_binary=sumo_binary, timeout_seconds=timeout_seconds)
    immutable = file_sha256(source) == source_hash
    passed = admission["status"] == "pass" and after["status"] == "pass" and width_applied and load["status"] == "pass" and immutable
    report.update(status="pass" if passed else "blocked", admission=admission, after_admission=after,
                  width_applied=width_applied, candidate_load=load, source_immutable=immutable,
                  candidate={"path": str(candidate), "sha256": file_sha256(candidate)},
                  limitation="This is a synthetic geometry-preservation test. It does not accept a new field width or certify traffic safety.")
    write_json_atomic(report_file, report, sort_keys=True)
    return report
