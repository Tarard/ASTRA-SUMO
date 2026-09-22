"""Preserve explicit static OSM vehicle restrictions missed by netconvert.

SUMO 1.27.1 imports bus/foot/bicycle exceptions but ignores road-level
vehicle and motor_vehicle tags. Keep original OSM bytes and correct only
source-identified lanes with an explicit broad ``no`` restriction.
"""

from __future__ import annotations

import gzip
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from sumolib.net.lane import SUMO_VEHICLE_CLASSES, SUMO_VEHICLE_CLASSES_DEPRECATED

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import _MOTORIZED_MODES

_MOTOR_MODES = set(_MOTORIZED_MODES) | {"evehicle", "custom1", "custom2"}
_MODE_KEYS = {
    "private": ("motorcar",), "passenger": ("motorcar",),
    "taxi": ("psv", "taxi"), "bus": ("psv", "bus"),
    "coach": ("tourist_bus",), "delivery": ("goods",),
    "truck": ("hgv",), "trailer": ("hgv", "trailer"),
    "motorcycle": ("motorcycle",), "moped": ("moped",),
    "emergency": ("emergency",), "hov": ("hov",),
    "bicycle": ("bicycle",), "scooter": ("small_electric_vehicle",),
    "pedestrian": ("foot",),
}
_ALLOW = {"yes", "designated", "permissive", "official"}
_ACCESS_KEYS = {"access", "vehicle", "motor_vehicle", *[key for keys in _MODE_KEYS.values() for key in keys]}


def _conditional_values(value: str) -> list[str] | None:
    """Read whole value-condition clauses, without evaluating their conditions."""
    depth, start, clauses = 0, 0, []
    for index, character in enumerate(value):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return None
        elif character == ";" and depth == 0:
            clauses.append(value[start:index])
            start = index + 1
    if depth:
        return None
    clauses.append(value[start:])
    values = []
    for clause in clauses:
        restriction, separator, condition = clause.partition("@")
        if not separator or len(restriction.split()) != 1 or not condition.strip() or "@" in condition:
            return None
        values.append(restriction.strip())
    return values


def resolve_static_osm_access(tags: Mapping[str, str], attributes: Mapping[str, str]) -> dict[str, Any]:
    """Resolve clear broad restrictions and specific static exceptions only.

    Ambiguous purpose/private permissions retain their existing state and
    require review. Unresolved rules preserve only the affected mode's current
    state. Delivery-only conditions do not grant unspecified-purpose traffic
    access, including vehicles merely labeled with SUMO's delivery class.
    """
    normalized = {str(key): str(value).strip().lower() for key, value in tags.items()}
    if not any(normalized.get(key) == "no" for key in ("vehicle", "motor_vehicle")):
        return {"status": "not_applicable", "attributes": dict(attributes), "decisions": [], "review": []}
    complex_keys = sorted(key for key in normalized if ":" in key and key.split(":", 1)[0] in _ACCESS_KEYS)
    allowed = set(attributes.get("allow", "").split())
    denied = set(attributes.get("disallow", "").split())
    use_allow = bool(allowed and "all" not in allowed) or "all" in denied
    if "all" in denied:
        allowed = set()
    decisions, review = [], []
    for mode in sorted(_MOTOR_MODES | {"bicycle", "scooter", "pedestrian"}):
        keys = ["access"]
        if mode != "pedestrian":
            keys.append("vehicle")
        if mode in _MOTOR_MODES:
            keys.append("motor_vehicle")
        keys.extend(_MODE_KEYS.get(mode, ()))
        explicit = [(key, normalized[key]) for key in keys if key in normalized]
        if not explicit:
            continue
        key, value = explicit[-1]
        unresolved = False
        for complex_key in complex_keys:
            family = complex_key.split(":", 1)[0]
            if family not in keys or keys.index(family) < keys.index(key):
                continue  # A more specific static mode overrides this rule.
            values = _conditional_values(normalized[complex_key]) if complex_key == family + ":conditional" else None
            ordinary_no = value == "no" and values is not None and set(values) <= {"no", "delivery"}
            review.append({"mode": mode, "key": complex_key, "value": normalized[complex_key],
                           "static_key": key, "static_value": value,
                           "reason": "static_no_preserved_without_delivery_purpose_grant" if ordinary_no else "conditional_directional_or_lane_access_not_evaluated"})
            unresolved |= not ordinary_no
        if unresolved:
            continue
        if value != "no" and value not in _ALLOW:
            review.append({"mode": mode, "key": key, "value": value, "reason": "purpose_or_ambiguous_access_not_evaluated"})
            continue
        permits = value in _ALLOW
        decisions.append({"mode": mode, "allowed": permits, "key": key, "value": value})
        if use_allow:
            allowed.add(mode) if permits else allowed.discard(mode)
        else:
            denied.discard(mode) if permits else denied.add(mode)
    corrected = {"allow": " ".join(sorted(allowed))} if use_allow and allowed else {"disallow": "all"} if use_allow else {"disallow": " ".join(sorted(denied))}
    if not corrected.get("disallow", "") and "allow" not in corrected:
        corrected = {}
    return {"status": "review_required" if review else "pass", "attributes": corrected, "decisions": decisions, "review": review}


def _permission_attributes(lane: ET.Element) -> dict[str, str]:
    return {key: lane.attrib[key] for key in ("allow", "disallow") if key in lane.attrib}


def _permission_set(attributes: Mapping[str, str]) -> set[str]:
    universe = SUMO_VEHICLE_CLASSES - SUMO_VEHICLE_CLASSES_DEPRECATED
    allowed = set(attributes.get("allow", "").split())
    denied = set(attributes.get("disallow", "").split())
    if "all" in denied:
        return set()
    return (allowed if allowed and "all" not in allowed else universe) - denied


def _external_lanes(root: ET.Element) -> dict[str, ET.Element]:
    return {lane.attrib["id"]: lane for edge in root.findall("edge") if edge.get("function") != "internal" for lane in edge.findall("lane")}


def correct_osm_access_permissions(
    *,
    osm_file: Path,
    net_file: Path,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., Any] = run_command,
) -> dict[str, Any]:
    """Correct a newly built net, retaining its native import and source hashes."""
    source_hash = file_sha256(osm_file)
    raw = gzip.decompress(osm_file.read_bytes()) if osm_file.suffix == ".gz" else osm_file.read_bytes()
    ways = {}
    for way in ET.fromstring(raw).findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
        if any(tags.get(key, "").strip().lower() == "no" for key in ("vehicle", "motor_vehicle")):
            ways[way.attrib["id"]] = tags
    report: dict[str, Any] = {
        "schema": "torii.osm-static-access-correction/v1",
        "status": "not_applicable", "source_osm": str(osm_file.resolve()),
        "source_sha256": source_hash, "source_immutable": True,
        "restricted_way_count": len(ways), "changed_lane_count": 0,
        "changed_lanes": [], "review": [],
        "runtime_activation_changes": False, "revocable_lane_switches": False,
        "time_dependent_rules_evaluated": False,
    }
    if not ways:
        return report
    tree = ET.parse(net_file)
    root = tree.getroot()
    before = _external_lanes(root)
    patch = ET.Element("edges")
    expected, reviewed, matched_ways = {}, set(), set()
    for edge in root.findall("edge"):
        if edge.get("function") == "internal":
            continue
        edge_patch = None
        for index, lane in enumerate(edge.findall("lane")):
            ids = {token.lstrip("-") for param in lane.findall("param") if param.get("key") in {"origId", "origID"} for token in param.get("value", "").replace(";", " ").split()}
            matches = ids.intersection(ways)
            if not matches:
                continue
            matched_ways.update(matches)
            if len(ids) != 1:
                report["review"].append({"lane_id": lane.get("id"), "reason": "mixed_original_way_identity", "osm_way_ids": sorted(ids)})
                continue
            way_id = next(iter(matches))
            resolved = resolve_static_osm_access(ways[way_id], _permission_attributes(lane))
            if resolved["review"] and way_id not in reviewed:
                report["review"].append({"way_id": way_id, "findings": resolved["review"]})
                reviewed.add(way_id)
            attrs = resolved["attributes"]
            if _permission_set(attrs) == _permission_set(_permission_attributes(lane)):
                continue
            if edge_patch is None:
                edge_patch = ET.SubElement(patch, "edge", id=edge.attrib["id"])
            ET.SubElement(edge_patch, "lane", index=str(index), **attrs)
            expected[lane.attrib["id"]] = attrs
            report["changed_lanes"].append({"way_id": way_id, "edge_id": edge.attrib["id"], "lane_id": lane.attrib["id"], "before": _permission_attributes(lane), "after": attrs, "source_tags": ways[way_id], "decisions": resolved["decisions"]})
    report["review"].extend({"way_id": way, "reason": "restricted_way_not_identified_in_imported_network"} for way in sorted(set(ways) - matched_ways))
    report["changed_lane_count"] = len(expected)
    if not expected:
        report["status"] = "review_required" if report["review"] else "pass"
        return report
    output_dir.mkdir(parents=True, exist_ok=True)
    native = output_dir / "native-import.net.xml"
    shutil.copy2(net_file, native)
    patch_file = output_dir / "static-access.edg.xml"
    candidate = output_dir / "access-corrected.net.xml"
    ET.indent(patch, space="  ")
    ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
    command = [str(netconvert_binary), "--sumo-net-file", str(native.resolve()), "--edge-files", str(patch_file.resolve()), "--offset.disable-normalization", "true", "--junctions.internal-link-detail", "25", "--output-file", str(candidate.resolve())]
    result = command_runner(command, cwd=output_dir, timeout_seconds=timeout_seconds)
    result = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    report.update({"native_import": {"path": str(native.resolve()), "sha256": file_sha256(native)}, "edge_patch": {"path": str(patch_file.resolve()), "sha256": file_sha256(patch_file)}, "command": command, "result": result})
    passed = result.get("status") == "pass" and candidate.is_file()
    if passed:
        after = _external_lanes(ET.parse(candidate).getroot())
        identity_preserved = set(before) == set(after)
        exact_permissions = identity_preserved and all(_permission_set(_permission_attributes(after[lane])) == _permission_set(attrs) for lane, attrs in expected.items())
        other_permissions = identity_preserved and all(_permission_set(_permission_attributes(before[lane])) == _permission_set(_permission_attributes(after[lane])) for lane in before if lane not in expected)
        passed = exact_permissions and other_permissions
        report["checks"] = {"external_lane_identity_preserved": identity_preserved, "source_permissions_applied": exact_permissions, "other_lane_permissions_preserved": other_permissions}
    report["source_immutable"] = file_sha256(osm_file) == source_hash
    passed = passed and report["source_immutable"]
    if passed:
        shutil.copy2(candidate, net_file)
        report["corrected_network"] = {"path": str(net_file.resolve()), "sha256": file_sha256(net_file)}
    report["status"] = "review_required" if passed and report["review"] else "pass" if passed else "blocked"
    report_file = output_dir / "manifest.json"
    write_json_atomic(report_file, report, sort_keys=True)
    report["report_file"] = str(report_file.resolve())
    return report
