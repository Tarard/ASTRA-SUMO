"""Preserve explicit static OSM vehicle restrictions missed by netconvert.

SUMO 1.27.1 imports bus/foot/bicycle exceptions but ignores road-level
vehicle and motor_vehicle tags. Keep original OSM bytes and correct only
source-identified lanes with an explicit broad ``no`` restriction.
"""

from __future__ import annotations

import gzip
import copy
import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr
from sumolib.net.lane import SUMO_VEHICLE_CLASSES, SUMO_VEHICLE_CLASSES_DEPRECATED

from .artifact_io import copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import _MOTORIZED_MODES
from .sumo_commands import run_sumo_load_audit

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


def _patch_lane_permissions(raw: bytes, root: ET.Element, expected: Mapping[str, Mapping[str, str]]) -> bytes:
    """Change only permission attributes on declared lane opening tags."""
    tags = list(re.finditer(rb'''<lane(?=\s)(?:[^<>"']|"[^"]*"|'[^']*')*>''', raw))
    lanes = root.findall("edge/lane")
    if len(tags) != len(lanes):
        raise ValueError("Lane opening tags do not match the parsed network.")
    attributes = re.compile(rb'''\s+([\w:.-]+)\s*=\s*("[^"]*"|'[^']*')''', re.DOTALL)
    chunks, cursor, changed = [], 0, set()
    for match, lane in zip(tags, lanes):
        tag = match.group()
        parsed = ET.fromstring(tag if tag.endswith(b"/>") else tag[:-1] + b"/>")
        if parsed.attrib != lane.attrib:
            raise ValueError("A lane opening tag does not match its parsed attributes.")
        lane_id = lane.get("id")
        if lane_id in expected:
            if lane_id in changed or set(expected[lane_id]) - {"allow", "disallow"}:
                raise ValueError("Permission patch has duplicate lanes or unsupported attributes.")
            edits, written = [], set()
            for attribute in attributes.finditer(tag):
                if attribute.group(1) in (b"allow", b"disallow"):
                    key = attribute.group(1).decode("ascii")
                    if key in expected[lane_id]:
                        edits.append((attribute.start(2), attribute.end(2), quoteattr(expected[lane_id][key]).encode("utf-8")))
                        written.add(key)
                    else:
                        edits.append((attribute.start(), attribute.end(), b""))
            for start, end, replacement in reversed(edits):
                tag = tag[:start] + replacement + tag[end:]
            closing = b"/>" if tag.endswith(b"/>") else b">"
            added = b"".join(b" " + key.encode("ascii") + b"=" + quoteattr(value).encode("utf-8")
                             for key, value in expected[lane_id].items() if key not in written)
            tag = tag[:-len(closing)] + added + closing
            changed.add(lane_id)
        chunks.extend((raw[cursor:match.start()], tag))
        cursor = match.end()
    if changed != set(expected):
        raise ValueError("Permission patch references a missing lane.")
    chunks.append(raw[cursor:])
    return b"".join(chunks)


def _non_permission_state(root: ET.Element, changed_lanes: set[str]) -> bytes:
    masked = copy.deepcopy(root)
    for lane in masked.findall("edge/lane"):
        if lane.get("id") in changed_lanes:
            lane.attrib.pop("allow", None)
            lane.attrib.pop("disallow", None)
    return ET.tostring(masked, encoding="utf-8")


def correct_osm_access_permissions(
    *,
    osm_file: Path,
    net_file: Path,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    sumo_binary: str | None = None,
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
    network_bytes = net_file.read_bytes()
    network_hash = hashlib.sha256(network_bytes).hexdigest()
    root = ET.fromstring(network_bytes)
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
    patch_file = output_dir / "static-access.edg.xml"
    candidate = output_dir / "access-corrected.net.xml"
    if any(path.exists() for path in (native, patch_file, candidate, output_dir / "manifest.json")):
        raise ValueError("Use a new access-correction directory.")
    native.write_bytes(network_bytes)
    ET.indent(patch, space="  ")
    ET.ElementTree(patch).write(patch_file, encoding="utf-8", xml_declaration=True)
    sibling = Path(netconvert_binary).with_name("sumo" + Path(netconvert_binary).suffix)
    validator = sumo_binary or (str(sibling.resolve()) if sibling.is_file() else "sumo")
    report.update({"native_import": {"path": str(native.resolve()), "sha256": network_hash},
                   "edge_patch": {"path": str(patch_file.resolve()), "sha256": file_sha256(patch_file)},
                   "materialization_method": "permission_attributes_only_no_geometry_rebuild",
                   "netconvert_rebuild_count": 0})
    source_validation = run_sumo_load_audit(net_file=native, output_dir=output_dir / "source-load-audit",
        sumo_binary=validator, timeout_seconds=timeout_seconds, command_runner=command_runner)
    report["source_load_audit"] = source_validation
    if source_validation["status"] != "pass":
        report.update(status="blocked", blocked_reason="input_network_failed_sumo_load",
                      source_immutable=file_sha256(osm_file) == source_hash)
        report_file = output_dir / "manifest.json"
        write_json_atomic(report_file, report, sort_keys=True)
        return {**report, "report_file": str(report_file.resolve())}
    candidate.write_bytes(_patch_lane_permissions(network_bytes, root, expected))
    validation = run_sumo_load_audit(net_file=candidate, output_dir=output_dir / "load-audit",
        sumo_binary=validator,
        timeout_seconds=timeout_seconds, command_runner=command_runner)
    command, result = validation.get("command", []), validation.get("command_result", {})
    report.update({"load_audit": validation, "command": command, "result": result})
    passed = validation.get("status") == "pass" and candidate.is_file()
    if passed:
        candidate_root = ET.parse(candidate).getroot()
        after = _external_lanes(candidate_root)
        identity_preserved = set(before) == set(after)
        exact_permissions = identity_preserved and all(_permission_set(_permission_attributes(after[lane])) == _permission_set(attrs) for lane, attrs in expected.items())
        other_permissions = identity_preserved and all(_permission_set(_permission_attributes(before[lane])) == _permission_set(_permission_attributes(after[lane])) for lane in before if lane not in expected)
        unchanged_state = _non_permission_state(root, set(expected)) == _non_permission_state(candidate_root, set(expected))
        passed = exact_permissions and other_permissions and unchanged_state
        report["checks"] = {"external_lane_identity_preserved": identity_preserved, "source_permissions_applied": exact_permissions, "other_lane_permissions_preserved": other_permissions,
                            "non_permission_network_state_preserved": unchanged_state}
    widening = [{"lane_id": lane_id, "added_vehicle_classes": sorted(added)} for lane_id, attrs in expected.items()
                if (added := _permission_set(attrs) - _permission_set(_permission_attributes(before[lane_id])))]
    report["connectivity_review_required"] = bool(widening)
    report["review"].extend({**row, "reason": "newly_allowed_modes_require_connectivity_review"} for row in widening)
    report["source_immutable"] = file_sha256(osm_file) == source_hash
    passed = passed and report["source_immutable"] and file_sha256(net_file) == network_hash
    if passed:
        copy_file_atomic(candidate, net_file)
        report["corrected_network"] = {"path": str(net_file.resolve()), "sha256": file_sha256(net_file)}
    report["status"] = "review_required" if passed and report["review"] else "pass" if passed else "blocked"
    report_file = output_dir / "manifest.json"
    write_json_atomic(report_file, report, sort_keys=True)
    report["report_file"] = str(report_file.resolve())
    return report
