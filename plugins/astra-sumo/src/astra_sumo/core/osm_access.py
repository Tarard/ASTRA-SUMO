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
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from itertools import chain
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr
from sumolib.net.lane import SUMO_VEHICLE_CLASSES

from .artifact_io import atomic_writer, copy_file_atomic, write_json_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .connection_mode_audit import _MOTORIZED_MODES
from .sumo_commands import run_sumo_load_audit
from .xml_stream import DirectChildren, binary_blocks, read_blocks

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
# Values naming who may travel or why. SUMO classes cannot express a trip purpose, so the native permission stays.
_PURPOSE_VALUES = frozenset({"private", "permit", "destination", "customers", "delivery", "agricultural", "forestry",
                             "residents", "employees", "students", "visitors", "military"})
_CATEGORY_VALUES = frozenset((_ACCESS_KEYS - {"access"}) | {"coach", "disabled"})
_USER_GROUP_SUFFIXES = frozenset({"disabled", "wheelchair", "residents", "permit", "customers", "students", "employees",
                                  "visitors"})
_EXPLICIT_RULE = "explicit_value"
_REVERTED_ADDITION_REASON = "new_class_has_no_loaded_movement"
_LANE_TAG = re.compile(rb'''<lane(?=\s)(?:[^<>"']|"[^"]*"|'[^']*')*>''')
# A lane tag whose quoted value is still open at the end of a block: a cut there could split the tag.
_OPEN_LANE_TAG = re.compile(rb'''<lane(?=\s)(?:[^<>"']|"[^"]*"|'[^']*')*+(?:"[^"]*+|'[^']*+)\Z''')
_TAG_ATTRIBUTE = re.compile(rb'''\s+([\w:.-]+)\s*=\s*("[^"]*"|'[^']*')''', re.DOTALL)


def _conditional_clauses(value: str) -> list[tuple[str, str]] | None:
    """Read whole (value, condition) clauses without evaluating the conditions.

    A clause without ``@`` has an empty condition. Unbalanced parentheses or a multi-word value give None.
    """
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
        if len(restriction.split()) != 1 or "@" in condition or (separator and not condition.strip()):
            return None
        values.append((restriction.strip(), condition.strip()))
    return values


def _kept_native_rule(value: str) -> tuple[str, str] | None:
    """Rule and reason for a value that keeps the native permission, or None."""
    if value in {"dismount", "use_sidepath"}:
        return value + "_value", value + "_kept_native"
    parts = {part.strip() for part in value.split(";")}
    if parts <= _PURPOSE_VALUES:
        return "purpose_value", "purpose_restriction_kept_native"
    if parts <= _CATEGORY_VALUES:
        return "category_value", "category_value_kept_native"
    return None


def _edge_direction(edge_id: str, way_id: str) -> str | None:
    """Travel direction of a netconvert edge along its OSM way; None when the edge id does not tell."""
    base = edge_id[1:] if edge_id.startswith("-") else edge_id
    if base != way_id and not base.startswith(way_id + "#"):
        return None
    return "backward" if edge_id.startswith("-") else "forward"


def resolve_static_osm_access(tags: Mapping[str, str], attributes: Mapping[str, str], *,
                              keep_modes: Iterable[str] = (), edge_direction: str | None = None) -> dict[str, Any]:
    """Resolve clear broad restrictions and specific static exceptions only.

    Findings are settled by a recorded rule or stay in ``review``. Signage and user-group suffixes are ignored,
    ``unknown`` falls back to the next less specific key, and a ``:forward``/``:backward`` key applies to the
    matching ``edge_direction`` (stays open when the direction is None). Purpose, dismount, use_sidepath and
    category values keep the native state. Delivery-only conditions do not grant unspecified-purpose traffic
    access, including vehicles merely labeled with SUMO's delivery class. Other time windows stay open, because
    no scenario time exists to evaluate them. Modes in ``keep_modes`` belong to a dedicated lane of the edge and
    keep their state.
    """
    normalized = {str(key): str(value).strip().lower() for key, value in tags.items()}
    if not any(normalized.get(key) == "no" for key in ("vehicle", "motor_vehicle")):
        return {"status": "not_applicable", "attributes": dict(attributes), "decisions": [], "review": []}
    suffixed_keys = sorted(key for key in normalized if ":" in key and key.split(":", 1)[0] in _ACCESS_KEYS)
    allowed = set(attributes.get("allow", "").split())
    denied = set(attributes.get("disallow", "").split())
    use_allow = bool(allowed and "all" not in allowed) or "all" in denied
    if "all" in denied:
        allowed = set()
    native, keep = _permission_set(attributes), set(keep_modes)
    decisions, review = [], []
    for mode in sorted(_MOTOR_MODES | {"bicycle", "scooter", "pedestrian"}):
        keys = ["access"]
        if mode != "pedestrian":
            keys.append("vehicle")
        if mode in _MOTOR_MODES:
            keys.append("motor_vehicle")
        keys.extend(_MODE_KEYS.get(mode, ()))
        plain = [key for key in keys if key in normalized]
        suffixed = [key for key in suffixed_keys if key.split(":", 1)[0] in keys]
        if mode in keep:
            if plain or suffixed:
                decisions.append({"mode": mode, "rule": "dedicated_lane", "reason": "mode_kept_for_dedicated_lane"})
            continue
        explicit = []
        for key in plain:
            if normalized[key] == "unknown":
                decisions.append({"mode": mode, "key": key, "value": "unknown", "rule": "unknown_value",
                                  "reason": "unknown_value_falls_back"})
            else:
                explicit.append((key, normalized[key]))
        static = explicit[-1] if explicit else None
        rank = keys.index(static[0]) if static else -1
        chosen, chosen_rank, unresolved = static, rank, False
        for complex_key in suffixed:
            family, suffix = complex_key.split(":", 1)
            if keys.index(family) < rank:
                continue  # A more specific static mode overrides this rule.
            value, parts = normalized[complex_key], suffix.split(":")
            row = {"mode": mode, "key": complex_key, "value": value}
            static_row = {"static_key": static[0], "static_value": static[1]} if static else {}
            if "signed" in parts:
                decisions.append({**row, "rule": "signed_suffix", "reason": "signage_key_ignored"})
            elif parts[0] in _USER_GROUP_SUFFIXES:
                decisions.append({**row, "rule": "user_group_suffix", "reason": "user_group_without_sumo_class"})
            elif value == "unknown":
                decisions.append({**row, "rule": "unknown_value", "reason": "unknown_value_falls_back"})
            elif parts in (["forward"], ["backward"]):
                if static and value == static[1]:
                    decisions.append({**row, **static_row, "rule": "directional_suffix",
                                      "reason": "directional_value_equals_static"})
                elif edge_direction not in {"forward", "backward"}:
                    review.append({**row, **static_row, "reason": "directional_access_edge_direction_unknown"})
                    unresolved = True
                else:
                    applies = parts[0] == edge_direction
                    decisions.append({**row, "edge_direction": edge_direction, "applies_to_edge": applies,
                                      "rule": "directional_suffix", "reason": "directional_access_applied"})
                    if applies and keys.index(family) >= chosen_rank:
                        chosen, chosen_rank = (complex_key, value), keys.index(family)
            elif parts == ["conditional"]:
                clauses = _conditional_clauses(value)
                timed = bool(clauses) and all(condition for _, condition in clauses)
                untimed = bool(clauses) and not any(condition for _, condition in clauses)
                restrictions = {restriction for restriction, _ in clauses or ()}
                delivery_only = bool(static) and static[1] == "no" and restrictions <= {"no", "delivery"}
                if delivery_only and (timed or untimed):
                    decisions.append({**row, **static_row,
                                      "rule": "delivery_only_conditional" if timed else "untimed_delivery_conditional",
                                      "reason": "static_no_preserved_without_delivery_purpose_grant" if timed
                                      else "malformed_conditional_delivery_only"})
                else:
                    review.append({**row, **static_row, "reason": "conditional_time_window_without_scenario_time" if timed
                                   else "conditional_directional_or_lane_access_not_evaluated"})
                    unresolved = True
            else:
                review.append({**row, **static_row, "reason": "conditional_directional_or_lane_access_not_evaluated"})
                unresolved = True
        if unresolved or chosen is None:
            continue
        key, value = chosen
        if value != "no" and value not in _ALLOW:
            kept = _kept_native_rule(value)
            if kept is None:
                review.append({"mode": mode, "key": key, "value": value, "reason": "purpose_or_ambiguous_access_not_evaluated"})
            else:
                decisions.append({"mode": mode, "allowed": mode in native, "key": key, "value": value,
                                  "rule": kept[0], "reason": kept[1]})
            continue
        permits = value in _ALLOW
        decisions.append({"mode": mode, "allowed": permits, "key": key, "value": value,
                          "rule": _EXPLICIT_RULE, "reason": "explicit_access_value_applied"})
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


def _permission_set(attributes: Mapping[str, str], *, network_version: str | None = None) -> set[str]:
    aliases = {"public_emergency": "emergency", "public_authority": "authority", "public_army": "army",
               "public_transport": "bus", "transport": "truck", "lightrail": "tram", "cityrail": "rail_urban", "rail_slow": "rail"}
    # Native 1.27 retains rail_fast as a distinct permission, despite its deprecated name.
    universe = SUMO_VEHICLE_CLASSES - aliases.keys()
    allowed = {aliases.get(value, value) for value in attributes.get("allow", "").split()}
    denied = {aliases.get(value, value) for value in attributes.get("disallow", "").split()}
    if allowed:
        return universe if "all" in allowed else allowed
    if "all" in denied:
        return set()
    if denied and network_version is not None:
        version = tuple(int(value) for value in network_version.split(".")[:2])
        if version < (1, 3):
            denied.add("rail_fast")
        if version < (1, 20) and "rail_urban" in denied:
            denied.update(("subway", "cable_car"))
    return universe - denied


def _permission_attributes_for_version(attributes, *, source_version, target_version=None):
    """Keep effective classes when a declaration moves between network formats."""
    original = {key: attributes[key] for key in ("allow", "disallow") if key in attributes}
    allowed = _permission_set(original, network_version=source_version)
    if allowed == _permission_set(original, network_version=target_version):
        return original
    denied = _permission_set({}) - allowed
    result = {"disallow": " ".join(sorted(denied))} if denied else {}
    if _permission_set(result, network_version=target_version) != allowed:
        result = {"allow": " ".join(sorted(allowed))} if allowed else {"disallow": "all"}
    return result


def _translate_permission_declarations(element, *, source_version, target_version=None):
    if source_version == target_version:
        return []
    changed = []
    for row in element.iter():
        if row.tag not in {"lane", "connection"}:
            continue
        permissions = _permission_attributes_for_version(row.attrib, source_version=source_version, target_version=target_version)
        if permissions != _permission_attributes(row):
            row.attrib.pop("allow", None)
            row.attrib.pop("disallow", None)
            row.attrib.update(permissions)
            changed.append(row.get("id") or [row.get(key) for key in ("from", "fromLane", "to", "toLane")])
    return changed


def _external_lanes(root: ET.Element) -> dict[str, ET.Element]:
    return {lane.attrib["id"]: lane for edge in root.findall("edge") if edge.get("function") != "internal" for lane in edge.findall("lane")}


def _patch_lane_permissions(raw: bytes, root: ET.Element, expected: Mapping[str, Mapping[str, str]]) -> bytes:
    """Change only permission attributes on declared lane opening tags."""
    return _patch_lane_attributes(raw, root, expected, {"allow", "disallow"})


def _patch_lane_attributes(raw: bytes, root: ET.Element, expected: Mapping[str, Mapping[str, str]], attributes_to_change) -> bytes:
    """Change only declared attributes on exact lane opening tags."""
    tags = list(_LANE_TAG.finditer(raw))
    lanes = root.findall("edge/lane")
    if len(tags) != len(lanes):
        raise ValueError("Lane opening tags do not match the parsed network.")
    chunks, cursor, changed = [], 0, set()
    for match, lane in zip(tags, lanes):
        tag = _patched_lane_tag(match.group(), lane.attrib, expected, attributes_to_change, changed)
        chunks.extend((raw[cursor:match.start()], tag))
        cursor = match.end()
    if changed != set(expected):
        raise ValueError("Permission patch references a missing lane.")
    chunks.append(raw[cursor:])
    return b"".join(chunks)


def _patched_lane_tag(tag: bytes, lane: Mapping[str, str], expected: Mapping[str, Mapping[str, str]],
                      attributes_to_change, changed: set[str]) -> bytes:
    """The lane opening tag with its expected attributes, checked against the parsed lane attributes."""
    parsed = ET.fromstring(tag if tag.endswith(b"/>") else tag[:-1] + b"/>")
    if parsed.attrib != lane:
        raise ValueError("A lane opening tag does not match its parsed attributes.")
    lane_id = lane.get("id")
    if lane_id in expected:
        if lane_id in changed or set(expected[lane_id]) - set(attributes_to_change):
            raise ValueError("Permission patch has duplicate lanes or unsupported attributes.")
        edits, written = [], set()
        for attribute in _TAG_ATTRIBUTE.finditer(tag):
            if attribute.group(1).decode("ascii") in attributes_to_change:
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
    return tag


def _edge_lane_attributes(net_file: Path) -> Iterator[dict[str, str]]:
    """Attributes of each lane of a top-level edge in document order, like ``root.findall("edge/lane")``."""
    for element in DirectChildren(binary_blocks(net_file)):
        if element.tag == "edge":
            for lane in element.findall("lane"):
                yield lane.attrib


def _patch_lane_attributes_file(source: Path, candidate: Path, expected: Mapping[str, Mapping[str, str]],
                                attributes_to_change) -> None:
    """Write ``source`` with ``_patch_lane_attributes`` applied to ``candidate``, reading it a block at a time.

    Blocks are cut before a '<' where no lane tag is still open inside a quoted value, so the tags found in the
    blocks are those of the whole file. The candidate is replaced only when every check passes, with the errors
    the whole-file patch raises in the same order.
    """
    lanes = _edge_lane_attributes(source)
    tag_count = lane_count = 0
    changed: set[str] = set()
    first_error: Exception | None = None
    with atomic_writer(candidate) as handle, source.open("rb") as stream:
        pending = b""
        for block in chain(read_blocks(stream, 1 << 20), [b""]):
            data = pending + block
            cut = data.rfind(b"<") if block else len(data)
            if block and (cut <= 0 or _OPEN_LANE_TAG.search(data, 0, cut)):
                pending = data
                continue
            piece, pending = data[:cut], data[cut:]
            cursor = 0
            for match in _LANE_TAG.finditer(piece):
                tag_count += 1
                lane = next(lanes, None)
                if lane is None:
                    continue
                lane_count += 1
                if first_error is not None:
                    continue
                try:
                    tag = _patched_lane_tag(match.group(), lane, expected, attributes_to_change, changed)
                except (ValueError, ET.ParseError) as exc:
                    first_error = exc
                    continue
                if lane.get("id") in expected:
                    handle.write(piece[cursor:match.start()])
                    handle.write(tag)
                    cursor = match.end()
            handle.write(piece[cursor:])
        lane_count += sum(1 for _ in lanes)
        if tag_count != lane_count:
            raise ValueError("Lane opening tags do not match the parsed network.")
        if first_error is not None:
            raise first_error
        if changed != set(expected):
            raise ValueError("Permission patch references a missing lane.")


def _non_permission_state(root: ET.Element, changed_lanes: set[str]) -> bytes:
    masked = copy.deepcopy(root)
    for lane in masked.findall("edge/lane"):
        if lane.get("id") in changed_lanes:
            lane.attrib.pop("allow", None)
            lane.attrib.pop("disallow", None)
    return ET.tostring(masked, encoding="utf-8")


def _streamed_non_permission_state(net_file: Path, changed_lanes: set[str], external_lanes: dict | None = None,
                                   permission_key: Callable[[ET.Element], tuple] | None = None) -> tuple[bytes, str]:
    """``_non_permission_state`` of a network read one top-level element at a time, as comparable parts.

    The parts are the root element without children and a SHA-256 of the masked top-level elements in order, so
    two networks give equal parts exactly when their ``_non_permission_state`` bytes are equal. When given,
    ``external_lanes`` receives the permission key of every lane of a non-internal edge before masking.
    """
    digest = hashlib.sha256()
    network = DirectChildren(binary_blocks(net_file))
    for element in network:
        if element.tag == "edge":
            lanes = element.findall("lane")
            if external_lanes is not None and element.get("function") != "internal":
                external_lanes.update((lane.attrib["id"], permission_key(lane)) for lane in lanes)
            for lane in lanes:
                if lane.get("id") in changed_lanes:
                    lane.attrib.pop("allow", None)
                    lane.attrib.pop("disallow", None)
        digest.update(ET.tostring(element, encoding="utf-8"))
    head = ET.Element(network.root.tag, network.root.attrib)
    head.text, head.tail = network.root.text, network.root.tail
    return ET.tostring(head, encoding="utf-8"), digest.hexdigest()


def _permission_key_reader() -> Callable[[ET.Element], tuple[str | None, str | None]]:
    """A lane's (allow, disallow) declaration, shared between equal lanes so a city network stays small."""
    known: dict[tuple[str | None, str | None], tuple[str | None, str | None]] = {}

    def permission_key(lane: ET.Element) -> tuple[str | None, str | None]:
        key = (lane.get("allow"), lane.get("disallow"))
        return known.setdefault(key, key)

    return permission_key


def _key_attributes(key: tuple[str | None, str | None]) -> dict[str, str]:
    """The ``_permission_attributes`` of a lane from its permission key."""
    return {name: value for name, value in zip(("allow", "disallow"), key) if value is not None}


def _permission_set_reader(network_version: str) -> Callable[[tuple[str | None, str | None]], frozenset[str]]:
    sets: dict[tuple[str | None, str | None], frozenset[str]] = {}

    def modes(key: tuple[str | None, str | None]) -> frozenset[str]:
        if key not in sets:
            sets[key] = frozenset(_permission_set(_key_attributes(key), network_version=network_version))
        return sets[key]

    return modes


def measure_added_class_movements(net_file: Path, additions: Mapping[str, Iterable[str]]) -> dict[str, Any]:
    """Classify added lane classes by the movements a streamed network loads.

    An addition is ``usable`` when one connection into or out of its lane allows the class on the other lane and
    on every internal lane on the way. Otherwise the reason is ``no_loaded_movement`` (no connection),
    ``internal_lane_blocks_class`` or ``no_neighbour_allows_class``. Pedestrians in a network without walking
    areas need no connections: SUMO lets them change edges at a junction, so another pedestrian edge at either
    end makes the addition usable. Usability is symmetric between two added lanes, so reverting the unusable
    additions leaves the result of the others unchanged.
    """
    added = {lane: set(classes) for lane, classes in additions.items() if classes}
    pedestrians = any("pedestrian" in classes for classes in added.values())
    table, keys, lane_rows = {}, [], {}
    sets: dict[int, frozenset[str]] = {}
    movements, successors = defaultdict(list), defaultdict(list)
    lane_ends, added_at, edge_at = {}, defaultdict(list), {}
    several = object()
    context = ET.iterparse(net_file, events=("start", "end"))
    _, root = next(context)
    version, walkingareas, depth = root.get("version", "0.0"), root.get("walkingareas") == "true", 0

    def modes(lane_id):
        index = lane_rows.get(lane_id)
        if index is None:
            return frozenset()
        if index not in sets:
            allow, disallow, _ = keys[index]
            declared = {name: value for name, value in (("allow", allow), ("disallow", disallow)) if value is not None}
            sets[index] = frozenset(_permission_set(declared, network_version=version))
        return sets[index]

    for event, element in context:
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth:
            continue  # Read each direct child of <net> once it is complete.
        if element.tag == "edge":
            function = element.get("function") or ""
            walkingareas |= function == "walkingarea"
            edge_id, ends = element.get("id"), (element.get("from"), element.get("to"))
            for lane in element.findall("lane"):
                lane_id, key = lane.get("id"), (lane.get("allow"), lane.get("disallow"), function)
                if key not in table:
                    table[key] = len(keys)
                    keys.append(key)
                lane_rows[lane_id] = table[key]
                if not pedestrians or function or "pedestrian" not in modes(lane_id):
                    continue
                if "pedestrian" in added.get(lane_id, ()):
                    lane_ends[lane_id] = (edge_id, ends)
                    for node in ends:
                        added_at[node].append(lane_id)
                else:
                    for node in ends:
                        edge_at[node] = edge_id if edge_at.get(node, edge_id) == edge_id else several
        elif element.tag == "connection":
            source = f'{element.get("from")}_{element.get("fromLane")}'
            target = f'{element.get("to")}_{element.get("toLane")}'
            row = (source, target, element.get("via"))
            if source.startswith(":"):
                successors[source].append((target, row[2] or target))
            for lane_id in {source, target} & added.keys():
                movements[lane_id].append(row)
        root.clear()
    del context

    def reaches(via, last, vehicle_class):
        pending, seen = [via or last], set()
        while pending:
            lane_id = pending.pop()
            if lane_id == last:
                return True
            if lane_id in seen or vehicle_class not in modes(lane_id):
                continue
            seen.add(lane_id)
            pending.extend([following for target, following in successors.get(lane_id, ()) if target == last] or [last])
        return False

    def movement(lane_id, vehicle_class):
        if vehicle_class == "pedestrian" and not walkingareas:
            edge_id, ends = lane_ends.get(lane_id, (None, ()))
            for node in ends:
                if edge_at.get(node, edge_id) != edge_id or any(lane_ends[other][0] != edge_id for other in added_at[node]):
                    return "usable"
            return "no_neighbour_allows_class" if ends else "no_loaded_movement"
        rows = [row for row in movements.get(lane_id, ()) if lane_rows.get(row[0]) is None or keys[lane_rows[row[0]]][2] != "internal"]
        if not rows:
            return "no_loaded_movement"
        neighbour = False
        for first, last, via in rows:
            if vehicle_class not in modes(last if first == lane_id else first):
                continue
            neighbour = True
            if reaches(via, last, vehicle_class):
                return "usable"
        return "internal_lane_blocks_class" if neighbour else "no_neighbour_allows_class"

    return {"network_has_walkingareas": walkingareas,
            "additions": {lane_id: {vehicle_class: movement(lane_id, vehicle_class) for vehicle_class in sorted(classes)}
                          for lane_id, classes in added.items()}}


def _without_classes(attributes: Mapping[str, str], classes: set[str], *, network_version: str) -> dict[str, str]:
    """Permission attributes like ``attributes`` with ``classes`` removed."""
    target = _permission_set(attributes, network_version=network_version) - classes
    if "allow" in attributes:
        kept = [value for value in attributes["allow"].split() if value not in classes]
        result = {"allow": " ".join(kept)} if kept else {"disallow": "all"}
    else:
        result = {"disallow": " ".join(sorted(set(attributes.get("disallow", "").split()) | classes))}
    if _permission_set(result, network_version=network_version) != target:
        result = {"allow": " ".join(sorted(target))} if target else {"disallow": "all"}
    return result


def _write_edge_patch(path: Path, expected: Mapping[str, Mapping[str, str]], positions: Mapping[str, tuple[str, int]]) -> None:
    patch, edges = ET.Element("edges"), {}
    for lane_id, attrs in expected.items():
        edge_id, index = positions[lane_id]
        if edge_id not in edges:
            edges[edge_id] = ET.SubElement(patch, "edge", id=edge_id)
        ET.SubElement(edges[edge_id], "lane", index=str(index), **attrs)
    ET.indent(patch, space="  ")
    ET.ElementTree(patch).write(path, encoding="utf-8", xml_declaration=True)


def _movement_report(measured: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for lane_id, classes in measured["additions"].items():
        for vehicle_class, movement in classes.items():
            outcome = {"status": "measured"} if movement == "usable" else {"status": "no_effect", "reason": _REVERTED_ADDITION_REASON}
            rows.append({"lane_id": lane_id, "vehicle_class": vehicle_class, "movement": movement, **outcome})
    reverted = [row for row in rows if row["status"] == "no_effect"]
    return {"method": "streamed_connections_and_internal_lanes",
            "network_has_walkingareas": measured["network_has_walkingareas"],
            "addition_count": len(rows), "usable_count": len(rows) - len(reverted), "reverted_count": len(reverted),
            "by_movement": dict(sorted(Counter(row["movement"] for row in rows).items())),
            "usable_by_class": dict(sorted(Counter(row["vehicle_class"] for row in rows if row["status"] == "measured").items())),
            "reverted_by_class": dict(sorted(Counter(row["vehicle_class"] for row in reverted).items())),
            "rows": rows}


def _unevaluated_access_tags(ways: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    """Count access-family tags of highway ways that the static correction does not evaluate."""
    by_key, by_value, count = Counter(), Counter(), 0
    for tags in ways:
        keys = [key for key in tags if key.split(":", 1)[0] in _ACCESS_KEYS]
        if "highway" not in tags or not keys:
            continue
        count += 1
        by_key.update(keys)
        by_value.update(f"{key}={tags[key].strip().lower()}" for key in keys if ":" not in key)
    return {"way_count": count, "by_key": dict(sorted(by_key.items())), "by_value": dict(sorted(by_value.items()))}


def _collect_unique(rows_by_way: dict[str, dict[tuple, dict]], way_id: str, rows: Iterable[dict]) -> None:
    bucket = rows_by_way.setdefault(way_id, {})
    for row in rows:
        bucket.setdefault(tuple(sorted(row.items())), row)


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
    """Correct a newly built net, retaining its native import and source hashes.

    On an edge with more than one lane, a lane that allows exactly pedestrians or exactly bicycles is a dedicated
    lane: way-level tags leave it unchanged, and the other lanes of the edge keep its mode. Each added lane class
    is measured on the corrected net; an addition without a loaded movement is reverted to the native permission.
    ``review`` keeps only open items; settled findings are listed in ``decisions``.
    """
    source_hash = file_sha256(osm_file)
    ways: dict[str, dict[str, str]] = {}

    def outside_gate() -> Iterator[dict[str, str]]:
        """Tags of each way outside the vehicle/motor_vehicle=no gate; gated ways go to ``ways``."""
        with gzip.open(osm_file, "rb") if osm_file.suffix == ".gz" else osm_file.open("rb") as handle:
            for way in DirectChildren(read_blocks(handle)):
                if way.tag != "way":
                    continue
                tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
                if any(tags.get(key, "").strip().lower() == "no" for key in ("vehicle", "motor_vehicle")):
                    ways[way.attrib["id"]] = tags
                else:
                    yield tags

    unevaluated_outside_gate = _unevaluated_access_tags(outside_gate())
    report: dict[str, Any] = {
        "schema": "torii.osm-static-access-correction/v1",
        "status": "not_applicable", "source_osm": str(osm_file.resolve()),
        "source_sha256": source_hash, "source_immutable": True,
        "restricted_way_count": len(ways), "changed_lane_count": 0,
        "changed_lanes": [], "decisions": [], "review": [], "dedicated_lanes_preserved": 0,
        "connectivity_review_required": False,
        "unevaluated_outside_gate": unevaluated_outside_gate,
        "runtime_activation_changes": False, "revocable_lane_switches": False,
        "time_dependent_rules_evaluated": False,
    }
    if not ways:
        return report
    network_hash = file_sha256(net_file)
    # The network is read one top-level element at a time; external lanes keep only their permission declaration.
    permission_key = _permission_key_reader()
    network = DirectChildren(binary_blocks(net_file))
    network_version = "0.0"
    before: dict[str, tuple[str | None, str | None]] = {}
    expected, positions, matched_ways = {}, {}, set()
    way_decisions, way_findings = {}, {}
    for edge in network:
        network_version = network.root.get("version", "0.0")
        if edge.tag != "edge":
            continue
        if edge.get("function") != "internal":
            for lane in edge.findall("lane"):
                before[lane.attrib["id"]] = permission_key(lane)
        if edge.get("function") in {"internal", "crossing", "walkingarea"}:
            continue
        lanes, dedicated = edge.findall("lane"), {}
        for lane in lanes if len(lanes) > 1 else ():
            modes = _permission_set(_permission_attributes(lane), network_version=network_version)
            if modes in ({"pedestrian"}, {"bicycle"}):
                dedicated[lane.get("id")] = modes
        keep_modes = set().union(*dedicated.values())
        for index, lane in enumerate(lanes):
            ids = {token.lstrip("-") for param in lane.findall("param") if param.get("key") in {"origId", "origID"} for token in param.get("value", "").replace(";", " ").split()}
            matches = ids.intersection(ways)
            if not matches:
                continue
            matched_ways.update(matches)
            if len(ids) != 1:
                report["review"].append({"lane_id": lane.get("id"), "reason": "mixed_original_way_identity", "osm_way_ids": sorted(ids)})
                continue
            way_id = next(iter(matches))
            if lane.get("id") in dedicated:
                report["dedicated_lanes_preserved"] += 1
                continue
            resolved = resolve_static_osm_access(ways[way_id], _permission_attributes(lane), keep_modes=keep_modes,
                                                 edge_direction=_edge_direction(edge.attrib["id"], way_id))
            _collect_unique(way_decisions, way_id, (row for row in resolved["decisions"] if row["rule"] != _EXPLICIT_RULE))
            _collect_unique(way_findings, way_id, resolved["review"])
            attrs = resolved["attributes"]
            if _permission_set(attrs, network_version=network_version) == _permission_set(
                    _permission_attributes(lane), network_version=network_version):
                continue
            expected[lane.attrib["id"]] = attrs
            positions[lane.attrib["id"]] = (edge.attrib["id"], index)
            report["changed_lanes"].append({"way_id": way_id, "edge_id": edge.attrib["id"], "lane_id": lane.attrib["id"], "before": _permission_attributes(lane), "after": attrs, "source_tags": ways[way_id], "decisions": resolved["decisions"]})
    report["decisions"] = [{"way_id": way, "decisions": list(rows.values())} for way, rows in way_decisions.items() if rows]
    report["review"].extend({"way_id": way, "findings": list(rows.values())} for way, rows in way_findings.items() if rows)
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
    copy_file_atomic(net_file, native)
    _write_edge_patch(patch_file, expected, positions)
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
    _patch_lane_attributes_file(native, candidate, expected, {"allow", "disallow"})

    def added_classes(lane_id):
        return (_permission_set(expected[lane_id], network_version=network_version) -
                _permission_set(_key_attributes(before[lane_id]), network_version=network_version))

    additions = {lane_id: added for lane_id in expected if (added := added_classes(lane_id))}
    measured = {}
    if additions:
        # The correction edits lane permissions only and creates no movement, so a class without one is reverted.
        movement_check = measure_added_class_movements(candidate, additions)
        measured = movement_check["additions"]
        report["added_class_movements"] = _movement_report(movement_check)
        unusable = {lane_id: {name for name, movement in classes.items() if movement != "usable"}
                    for lane_id, classes in measured.items()}
        unusable = {lane_id: classes for lane_id, classes in unusable.items() if classes}
        for lane_id, classes in unusable.items():
            attrs = _without_classes(expected[lane_id], classes, network_version=network_version)
            if _permission_set(attrs, network_version=network_version) == _permission_set(
                    _key_attributes(before[lane_id]), network_version=network_version):
                del expected[lane_id]
            else:
                expected[lane_id] = attrs
        if unusable:
            changed_rows = []
            for row in report["changed_lanes"]:
                if row["lane_id"] in expected:
                    changed_rows.append(row)
                    if row["lane_id"] in unusable:
                        row.update(after=expected[row["lane_id"]], reverted_vehicle_classes=sorted(unusable[row["lane_id"]]))
            report["changed_lanes"] = changed_rows
            report["changed_lane_count"] = len(expected)
            _patch_lane_attributes_file(native, candidate, expected, {"allow", "disallow"})
            _write_edge_patch(patch_file, expected, positions)
            report["edge_patch"] = {"path": str(patch_file.resolve()), "sha256": file_sha256(patch_file)}
    validation = run_sumo_load_audit(net_file=candidate, output_dir=output_dir / "load-audit",
        sumo_binary=validator,
        timeout_seconds=timeout_seconds, command_runner=command_runner)
    command, result = validation.get("command", []), validation.get("command_result", {})
    report.update({"load_audit": validation, "command": command, "result": result})
    passed = validation.get("status") == "pass" and candidate.is_file()
    if passed:
        after: dict[str, tuple[str | None, str | None]] = {}
        candidate_state = _streamed_non_permission_state(candidate, set(expected), after, permission_key)
        native_state = _streamed_non_permission_state(native, set(expected))
        modes = _permission_set_reader(network_version)
        identity_preserved = set(before) == set(after)
        exact_permissions = identity_preserved and all(modes(after[lane]) ==
            _permission_set(attrs, network_version=network_version) for lane, attrs in expected.items())
        other_permissions = identity_preserved and all(modes(before[lane]) ==
            modes(after[lane]) for lane in before if lane not in expected)
        unchanged_state = native_state == candidate_state
        passed = exact_permissions and other_permissions and unchanged_state
        report["checks"] = {"external_lane_identity_preserved": identity_preserved, "source_permissions_applied": exact_permissions, "other_lane_permissions_preserved": other_permissions,
                            "non_permission_network_state_preserved": unchanged_state}
    remaining = [{"lane_id": lane_id, "added_vehicle_classes": sorted(added)} for lane_id in expected
                 if (added := {name for name in added_classes(lane_id) if measured.get(lane_id, {}).get(name) != "usable"})]
    report["connectivity_review_required"] = bool(remaining)
    report["review"].extend({**row, "reason": "newly_allowed_modes_require_connectivity_review"} for row in remaining)
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
