from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .command_runner import run_command


SERVICE_PASSENGER_POLICIES = ("sumo_default", "allow_vehicle_service", "reference_match")


def _normalized_policy(policy: str | None) -> str:
    normalized = (policy or "sumo_default").strip().lower()
    if normalized not in SERVICE_PASSENGER_POLICIES:
        raise ValueError("Unknown service passenger policy.")
    return normalized


def _is_service_edge(edge: ET.Element) -> bool:
    edge_type = edge.get("type", "").strip().lower()
    return edge_type == "service" or edge_type.endswith(".service")


def _add_passenger_to_lane(lane: ET.Element) -> bool:
    from .osm_access import _permission_set
    allowed = _permission_set(lane.attrib)
    if "passenger" in allowed:
        return False
    lane.set("allow", " ".join(sorted(allowed | {"passenger"})))
    lane.attrib.pop("disallow", None)
    return True


def apply_service_passenger_permissions(
    net_file: Path, *, policy: str | None = None, output_dir: Path | None = None,
    netconvert_binary: str = "netconvert", sumo_binary: str = "sumo", timeout_seconds: float = 60,
) -> dict[str, object]:
    """Rebuild a separate candidate so external and internal permissions agree."""
    from .candidate_contracts import file_sha256
    from .osm_access import _external_lanes, _permission_set
    from .source_movement_support import _candidate_modes, _index

    selected_policy = _normalized_policy(policy)
    source = Path(net_file).resolve()
    report = dict(schema="torii.service-passenger-permissions/v1", status="pass", claim_status="diagnostic-demo",
        service_passenger_permission_status="skipped", service_passenger_policy=selected_policy,
        service_edge_count=0, changed_edge_count=0, changed_lane_count=0, net_file=str(source), warnings=[])
    if selected_policy == "sumo_default":
        return report
    if not source.is_file():
        return {**report, "status": "fail", "service_passenger_permission_status": "failed",
                "warnings": [f"net file not found for service passenger permissions: {source}"]}
    digest = file_sha256(source)
    root = ET.parse(source).getroot()
    before = _external_lanes(root)
    expected = {key: _permission_set(lane.attrib) for key, lane in before.items()}
    patch = ET.Element("edges")
    changed = []
    for edge in root.findall("edge"):
        if edge.get("function") == "internal" or not _is_service_edge(edge):
            continue
        report["service_edge_count"] += 1
        edge_patch = None
        for ordinal, original in enumerate(edge.findall("lane")):
            lane = deepcopy(original)
            if not _add_passenger_to_lane(lane):
                continue
            if edge_patch is None:
                edge_patch = ET.SubElement(patch, "edge", id=edge.get("id"))
                report["changed_edge_count"] += 1
            ET.SubElement(edge_patch, "lane", index=str(ordinal), allow=lane.get("allow"))
            expected[lane.get("id")] = _permission_set(lane.attrib)
            changed.append(lane.get("id"))
    report.update(source=dict(path=str(source), sha256=digest), changed_lane_count=len(changed), changed_lanes=changed)
    if not changed:
        report.update(service_passenger_permission_status="unchanged", source_immutable=True)
        return report
    destination = Path(output_dir or source.parent / f"{source.stem}.service-permissions").resolve()
    if destination.exists() or source.is_relative_to(destination):
        raise ValueError("Choose a new permission output directory separate from the source.")
    destination.mkdir(parents=True)
    patch_file, candidate = destination / "permissions.edg.xml", destination / "permissions.net.xml"
    ET.indent(patch, space="  ")
    ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
    command = [str(netconvert_binary), "--sumo-net-file", str(source), "--edge-files", str(patch_file),
               "--offset.disable-normalization", "true", "--output-file", str(candidate)]
    converted = run_command(command, cwd=destination, timeout_seconds=timeout_seconds)
    report.update(netconvert_result=converted.to_dict(), patch=dict(path=str(patch_file), sha256=file_sha256(patch_file)),
                  checks={}, status="blocked", service_passenger_permission_status="failed")
    if converted.returncode == 0 and candidate.is_file():
        actual = ET.parse(candidate).getroot()
        after = _external_lanes(actual)
        original_index, candidate_index = _index(root), _index(actual)
        old_pairs = {row[0]: row for row in original_index["movements"]}
        new_pairs = {row[0]: row for row in candidate_index["movements"]}
        def path_modes(index, row):
            _, first, last, connection = row
            _, chain = _candidate_modes(index, first, last, connection)
            modes = _permission_set(connection.attrib)
            for lane_id in chain:
                modes &= _permission_set(index["lanes"][lane_id][1].attrib)
            for lane_id in chain[1:-1]:
                modes &= _permission_set(index["outgoing"][lane_id][0][1].attrib)
            return modes if chain else set(), chain
        paths = []
        for pair, row in new_pairs.items():
            _, first, last, connection = row
            modes, chain = path_modes(candidate_index, row)
            required = ("passenger" in expected.get(first, set()) & expected.get(last, set())
                        and "passenger" in _permission_set(connection.attrib) and (first in changed or last in changed))
            prior_modes = path_modes(original_index, old_pairs[pair])[0] if pair in old_pairs else set()
            expected_modes = prior_modes | ({"passenger"} if required else set())
            paths.append(dict(connection=list(pair), passenger_required=required,
                              passed=bool(chain) and modes == expected_modes))
        def signals(network):
            return [ET.canonicalize(ET.tostring(t, encoding="unicode"), strip_text=True) for t in network.findall("tlLogic")]
        def same_lane_geometry(first, last):
            for key in ("speed", "length", "width"):
                if not math.isclose(float(first.get(key, "3.2")), float(last.get(key, "3.2")), abs_tol=.01, rel_tol=0):
                    return False
            points = [[tuple(map(float, p.split(','))) for p in lane.get('shape', '').split()] for lane in (first, last)]
            return len(points[0]) == len(points[1]) and all(len(a) == len(b) and math.dist(a, b) <= .01 for a, b in zip(*points))
        checks = dict(external_lanes_preserved=set(before) == set(after),
            exact_external_permissions=set(before) == set(after) and all(_permission_set(after[k].attrib) == v for k, v in expected.items()),
            external_geometry_preserved=set(before) == set(after) and all(same_lane_geometry(before[k], after[k]) for k in before),
            connections_preserved=set(old_pairs) == set(new_pairs), internal_permissions=all(row["passed"] for row in paths),
            connection_permissions_preserved=set(old_pairs) == set(new_pairs) and all(
                _permission_set(old_pairs[pair][3].attrib) == _permission_set(new_pairs[pair][3].attrib) for pair in old_pairs),
            signal_programs_preserved=signals(root) == signals(actual))
        loaded = run_command([str(sumo_binary), "--net-file", str(candidate), "--begin", "0", "--end", "1", "--no-step-log", "true"],
                             cwd=destination, timeout_seconds=timeout_seconds)
        checks["sumo_load"] = loaded.returncode == 0
        report.update(checks=checks, connection_checks=paths, load_result=loaded.to_dict(),
                      candidate=dict(path=str(candidate), sha256=file_sha256(candidate)))
        if all(checks.values()):
            report.update(status="pass", service_passenger_permission_status="applied", net_file=str(candidate))
    report["source_immutable"] = file_sha256(source) == digest
    if not report["source_immutable"]:
        report.update(status="blocked", net_file=str(source))
    if report["status"] != "pass":
        report["warnings"].append("The permission candidate did not pass reconstruction and connection checks.")
    report["claim_boundary"] = "Policy-driven permission candidate. Structural lane paths and SUMO load are checked; field access and traffic performance are not certified."
    report["report_file"] = str(destination / "manifest.json")
    write_json_atomic(Path(report["report_file"]), report)
    return report
