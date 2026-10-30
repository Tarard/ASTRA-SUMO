"""Extract a native conversion input and merge its declared repair into a source."""

from array import array
from copy import deepcopy
from hashlib import sha256
import heapq
from pathlib import Path
import xml.etree.ElementTree as ET

from .candidate_contracts import file_sha256
from .internal_ownership import resolve_internal_ownership
from .xml_stream import DirectChildren, read_blocks


DEFAULT_GEOMETRY_PRECISION = 6
_INTERNAL_EDGE_FUNCTIONS = {"internal", "crossing", "walkingarea"}


def with_geometry_precision(command):
    """Keep declared input detail unless the caller specifies native precision."""
    command = list(command)
    if not any(value == "--precision" or value.startswith("--precision=") for value in command):
        index = command.index("--output-file") if "--output-file" in command else 1
        command[index:index] = ["--precision", str(DEFAULT_GEOMETRY_PRECISION)]
    return command


def loaded_turn_permissions(root):
    """Read complete ordinary-road movements from lane permissions, including internal chains."""
    from collections import defaultdict
    from .osm_access import _permission_set
    version = root.get("version", "0.0")
    lanes, keys, internal = {}, {}, set()
    for edge in root.findall("edge"):
        for ordinal, lane in enumerate(edge.findall("lane")):
            identity = lane.get("id")
            lanes[identity] = _permission_set(lane.attrib, network_version=version)
            keys[(edge.get("id"), str(ordinal))] = identity
            if edge.get("function") in _INTERNAL_EDGE_FUNCTIONS or edge.get("id", "").startswith(":"):
                internal.add(identity)
    outgoing = defaultdict(list)
    movements = []
    for row in root.findall("connection"):
        first = keys.get((row.get("from"), row.get("fromLane")))
        last = keys.get((row.get("to"), row.get("toLane")))
        if first is None or last is None:
            if row.get("via"):
                raise ValueError("A loaded movement references an unknown endpoint lane.")
            continue
        via = row.get("via") or last
        outgoing[(first, last)].append(via)
        if first not in internal and last not in internal:
            movements.append((row, first, last, via))
    result = {}
    for row, first, last, via in movements:
        pending, seen, allowed = [(via, lanes[first] & lanes[last])], {}, set()
        while pending:
            current, modes = pending.pop()
            if current not in lanes:
                raise ValueError("A loaded movement references an unknown internal lane.")
            modes = (modes & lanes[current]) - seen.get(current, set())
            if not modes:
                continue
            seen.setdefault(current, set()).update(modes)
            if current == last:
                allowed.update(modes)
            elif current in internal:
                pending.extend((nxt, modes) for nxt in outgoing[(current, last)])
        key = (row.get("from"), int(row.get("fromLane")), row.get("to"), int(row.get("toLane")))
        result[key] = allowed
    return result


def with_loaded_turn_permissions(root):
    """Make native re-import reproduce loaded turn access without changing the source."""
    from .osm_access import _permission_set
    result = deepcopy(root)
    permissions = loaded_turn_permissions(root)
    lanes = {(edge.get("id"), ordinal): lane for edge in root.findall("edge")
             for ordinal, lane in enumerate(edge.findall("lane"))}
    version = root.get("version", "0.0")
    for row in result.findall("connection"):
        key = (row.get("from"), int(row.get("fromLane", "-1")), row.get("to"), int(row.get("toLane", "-1")))
        if key not in permissions:
            continue
        endpoints = (_permission_set(lanes[(edge, index)].attrib, network_version=version)
                     for edge, index in (key[:2], key[2:]))
        declared = _permission_set(row.attrib, network_version=version).intersection(*endpoints)
        if declared != permissions[key]:
            _declare_loaded_permissions(row, permissions[key])
    return result


def _loaded_permission_attributes(allowed):
    return {"allow": " ".join(sorted(allowed))} if allowed else {"disallow": "all"}


def _declare_loaded_permissions(row, allowed):
    # netconvert reads directives, whereas SUMO loads the internal lane permissions.
    row.attrib.pop("allow", None)
    row.attrib.pop("disallow", None)
    row.attrib.update(_loaded_permission_attributes(allowed))


class _StreamedTurns:
    """The lanes and connections ``with_loaded_turn_permissions`` reads, kept as numbers for a streamed network.

    Each lane id has a number with the permission declaration of the last lane carrying the id and whether any
    such lane is internal. Each edge id keeps its lane elements by ordinal, the last edge with the id winning per
    ordinal. Declarations are numbered, so a city network holds integers and a few shared sets instead of a tree.
    A connection names a lane by its edge and the ordinal spelled as a plain integer, as in the tree version.
    """

    def __init__(self):
        self.declarations = {}
        self.lane_numbers = {}
        self.lane_declaration, self.lane_internal = array("i"), bytearray()
        self.edge_lanes = {}
        self.element_lane, self.element_declaration = array("i"), array("i")
        self.clear_connections()

    def clear_connections(self):
        # The first successor of each internal lane; later ones in document order go to more_successors.
        self.next_target = self.next_through = None
        self.more_successors = {}
        self.movements, self.respelled = [], []
        self.unknown_endpoint, self.ordinal_error = False, None

    def _declaration(self, row):
        key = (row.get("allow"), row.get("disallow"))
        return self.declarations.setdefault(key, len(self.declarations))

    def add_edge(self, edge):
        internal = edge.get("function") in _INTERNAL_EDGE_FUNCTIONS or edge.get("id", "").startswith(":")
        positions = self.edge_lanes.setdefault(edge.get("id"), [])
        for ordinal, lane in enumerate(edge.findall("lane")):
            declaration, lane_id, number = self._declaration(lane), lane.get("id"), -1
            if lane_id is not None:
                number = self.lane_numbers.setdefault(lane_id, len(self.lane_numbers))
                if number == len(self.lane_declaration):
                    self.lane_declaration.append(declaration)
                    self.lane_internal.append(internal)
                else:
                    self.lane_declaration[number] = declaration
                    self.lane_internal[number] |= internal
            if ordinal == len(positions):
                positions.append(len(self.element_lane))
            else:
                positions[ordinal] = len(self.element_lane)
            self.element_lane.append(number)
            self.element_declaration.append(declaration)

    def _element(self, edge_id, ordinal):
        positions = self.edge_lanes.get(edge_id)
        return positions[ordinal] if positions is not None and 0 <= ordinal < len(positions) else -1

    def _spelled_element(self, edge_id, spelled):
        try:
            ordinal = int(spelled)
        except (TypeError, ValueError):
            return -1
        return self._element(edge_id, ordinal) if str(ordinal) == spelled else -1

    def add_connection(self, row, index):
        """Record connection ``index`` (its position among the connections) once every edge has been added."""
        if self.next_target is None:
            self.next_target = array("i", [-1]) * len(self.lane_numbers)
            self.next_through = array("i", [-1]) * len(self.lane_numbers)
        first_element = self._spelled_element(row.get("from"), row.get("fromLane"))
        last_element = self._spelled_element(row.get("to"), row.get("toLane"))
        first = self.element_lane[first_element] if first_element >= 0 else -1
        last = self.element_lane[last_element] if last_element >= 0 else -1
        via = row.get("via")
        if first < 0 or last < 0:
            self.unknown_endpoint |= bool(via)
        else:
            # -1 names a via lane that does not exist.
            through = self.lane_numbers.get(via, -1) if via else last
            if self.lane_internal[first]:
                if self.next_target[first] < 0:
                    self.next_target[first], self.next_through[first] = last, through
                else:
                    self.more_successors.setdefault(first, []).append((last, through))
            elif not self.lane_internal[last]:
                self.movements.append((index, first_element, last_element, self._declaration(row), first, last, through))
                return
        try:
            ordinals = int(row.get("fromLane", "-1")), int(row.get("toLane", "-1"))
        except ValueError as error:
            self.ordinal_error = self.ordinal_error or (index, error)
            return
        if (str(ordinals[0]), str(ordinals[1])) != (row.get("fromLane"), row.get("toLane")):
            # Another spelling of a lane ordinal can share the integer key of a movement and is bound with it.
            first_element, last_element = self._element(row.get("from"), ordinals[0]), self._element(row.get("to"), ordinals[1])
            if first_element >= 0 and last_element >= 0:
                self.respelled.append((index, first_element, last_element, self._declaration(row)))

    def _successors(self, lane, target):
        if self.next_target[lane] == target:
            yield self.next_through[lane]
        yield from (through for last, through in self.more_successors.get(lane, ()) if last == target)

    def _declaration_classes(self, version):
        """The classes of every lane declaration, computed in document order so the first invalid one raises."""
        from .osm_access import _permission_set
        keys = {number: key for key, number in self.declarations.items()}

        def attributes(number):
            return {name: value for name, value in zip(("allow", "disallow"), keys[number]) if value is not None}

        def classes(number):
            return frozenset(_permission_set(attributes(number), network_version=version))

        sets = {}
        for number in self.element_declaration:
            if number not in sets:
                sets[number] = classes(number)
        return sets, attributes, classes

    def _movement_classes(self, sets):
        if self.unknown_endpoint:
            raise ValueError("A loaded movement references an unknown endpoint lane.")

        def lane_classes(number):
            return sets[self.lane_declaration[number]]

        shared, result = {}, []
        for _, _, _, _, first, last, through in self.movements:
            pending, seen, allowed = [(through, lane_classes(first) & lane_classes(last))], {}, set()
            while pending:
                current, modes = pending.pop()
                if current < 0:
                    raise ValueError("A loaded movement references an unknown internal lane.")
                modes = (modes & lane_classes(current)) - seen.get(current, frozenset())
                if not modes:
                    continue
                seen.setdefault(current, set()).update(modes)
                if current == last:
                    allowed.update(modes)
                elif self.lane_internal[current]:
                    pending.extend((following, modes) for following in self._successors(current, last))
            allowed = frozenset(allowed)
            result.append(shared.setdefault(allowed, allowed))
        return result

    def loaded_classes(self, version):
        """Return the classes ``loaded_turn_permissions`` finds for each movement, in the order of ``movements``.

        Errors are raised as the tree version raises them, in the same order.
        """
        return self._movement_classes(self._declaration_classes(version)[0])

    def reconciliations(self, version):
        """Return the loaded classes ``with_loaded_turn_permissions`` declares, by connection index, and whether
        any declaration changes.

        Errors are raised as the tree versions raise them, in the same order.
        """
        sets, attributes, classes = self._declaration_classes(version)
        permissions = {}
        for movement, allowed in zip(self.movements, self._movement_classes(sets)):
            permissions[(movement[1], movement[2])] = allowed
        rewrites, changed = {}, False
        candidates = heapq.merge((row[:4] for row in self.movements), self.respelled)
        for index, first_element, last_element, declaration in candidates:
            if self.ordinal_error and self.ordinal_error[0] < index:
                raise self.ordinal_error[1]
            allowed = permissions.get((first_element, last_element))
            if allowed is None:
                continue
            if declaration not in sets:
                sets[declaration] = classes(declaration)
            endpoints = sets[self.element_declaration[first_element]] & sets[self.element_declaration[last_element]]
            if sets[declaration] & endpoints != allowed:
                rewrites[index] = allowed
                changed |= attributes(declaration) != _loaded_permission_attributes(allowed)
        if self.ordinal_error:
            raise self.ordinal_error[1]
        return rewrites, changed


def _streamed_reconciliations(source):
    """Stream ``source`` once, or twice when an edge follows a connection, and return its loaded-permission rewrites."""
    turns = _StreamedTurns()
    connections, edge_after_connection = 0, False
    with source.open("rb") as handle:
        stream = DirectChildren(read_blocks(handle))
        for element in stream:
            if element.tag == "edge":
                edge_after_connection |= connections > 0
                turns.add_edge(element)
            elif element.tag == "connection":
                if not edge_after_connection:
                    turns.add_connection(element, connections)
                connections += 1
    if edge_after_connection:
        # The tree version resolves connections against every edge.
        turns.clear_connections()
        connections = 0
        with source.open("rb") as handle:
            for element in DirectChildren(read_blocks(handle)):
                if element.tag == "connection":
                    turns.add_connection(element, connections)
                    connections += 1
    version = stream.root.get("version", "0.0")
    return (*turns.reconciliations(version), version)


def streamed_loaded_turn_permissions(source, *, visit=None):
    """Return ``loaded_turn_permissions`` of the network file ``source`` and its version, read one top-level element
    at a time (twice when an edge follows a connection).

    The classes are shared frozensets. ``visit`` is called with every top-level element of the first read, so a
    caller can collect more from the same pass.
    """
    turns = _StreamedTurns()
    keys = []

    def add_connection(element, index):
        count = len(turns.movements)
        turns.add_connection(element, index)
        if len(turns.movements) > count:
            keys.append((element.get("from"), int(element.get("fromLane")), element.get("to"), int(element.get("toLane"))))

    connections, edge_after_connection = 0, False
    with Path(source).open("rb") as handle:
        stream = DirectChildren(read_blocks(handle))
        for element in stream:
            if visit is not None:
                visit(element)
            if element.tag == "edge":
                edge_after_connection |= connections > 0
                turns.add_edge(element)
            elif element.tag == "connection":
                if not edge_after_connection:
                    add_connection(element, connections)
                connections += 1
    if edge_after_connection:
        turns.clear_connections()
        keys.clear()
        connections = 0
        with Path(source).open("rb") as handle:
            for element in DirectChildren(read_blocks(handle)):
                if element.tag == "connection":
                    add_connection(element, connections)
                    connections += 1
    version = stream.root.get("version", "0.0") if stream.root is not None else "0.0"
    return dict(zip(keys, turns.loaded_classes(version))), version


def _root_tags(root):
    """The start tag with the text before the first child, and the end tag, that ET.tostring writes for ``root``."""
    head = ET.Element(root.tag, root.attrib)
    head.text = root.text
    serialized = ET.tostring(head, encoding="utf-8", short_empty_elements=False)
    split = serialized.rindex(b"</")
    return serialized[:split], serialized[split:]


def _write_permission_input(source, destination, rewrites, version):
    """Write ``source`` with the rewritten connections exactly as ``ET.tostring`` writes the prepared tree.

    Each top-level element is written when it is complete. ElementTree declares the namespaces of the whole tree
    on its root, so a network whose top-level elements use namespaces is written as one tree.
    """
    from .osm_access import _permission_attributes, _permission_set
    changes = []

    def declare(row, allowed):
        before = _permission_attributes(row)
        _declare_loaded_permissions(row, allowed)
        if before != _permission_attributes(row):
            changes.append({"connection": {key: row.get(key) for key in ("from", "fromLane", "to", "toLane")},
                            "before": before, "after": _permission_attributes(row),
                            "loaded_classes": sorted(_permission_set(row.attrib, network_version=version))})

    with destination.open("xb") as handle, source.open("rb") as original:
        stream = DirectChildren(read_blocks(original))
        tags, index = None, 0
        for element in stream:
            if any(name[:1] == "{" for node in element.iter() for name in (node.tag, *node.keys())):
                break
            if tags is None:
                tags = _root_tags(stream.root)
                handle.write(b"<?xml version='1.0' encoding='utf-8'?>\n" + tags[0])
            if element.tag == "connection":
                if index in rewrites:
                    declare(element, rewrites[index])
                index += 1
            handle.write(ET.tostring(element, encoding="utf-8"))
        else:
            handle.write(tags[1])
            return changes
        changes.clear()
        handle.seek(0)
        handle.truncate()
        root = ET.parse(source).getroot()
        for index, row in enumerate(root.findall("connection")):
            if index in rewrites:
                declare(row, rewrites[index])
        handle.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return changes


def prepare_permission_preserving_input(source_file, prepared_file):
    """Bind a separate native input to loaded permissions, without blocking repairs of invalid sources.

    The source is streamed. The prepared input is written only when a connection declaration changes, with the
    bytes ``ET.tostring`` writes for ``with_loaded_turn_permissions`` of the whole tree.
    """
    source = Path(source_file).resolve(strict=True)
    identity = {"path": str(source), "sha256": file_sha256(source)}
    record = {**identity, "source": dict(identity), "preparation": "unchanged", "reconciled_connections": []}
    try:
        rewrites, changed, version = _streamed_reconciliations(source)
    except (ET.ParseError, ValueError) as error:
        return {**record, "preparation": "unavailable", "warning": str(error)}
    if changed:
        destination = Path(prepared_file).resolve()
        changes = _write_permission_input(source, destination, rewrites, version)
        record.update(path=str(destination), sha256=file_sha256(destination),
                      preparation="reconciled", reconciled_connections=changes)
    return record


def _index(root):
    edges = {row.get("id"): row for row in root.findall("edge")}
    if len(edges) != len(root.findall("edge")) or any(not identity for identity in edges):
        raise ValueError("Local conversion requires unique nonempty edge IDs.")
    return {"edges": edges, "ownership": resolve_internal_ownership(root)}


def _owner(element, index):
    if element.tag == "connection":
        edge = index["edges"].get(element.get("from"))
        if edge is None:
            return None
        if edge.get("id") not in index["ownership"]["edge"]:
            return edge.get("to")
        element = edge
    identity = element.get("id", "")
    if identity not in index["ownership"].get(element.tag, {}):
        return identity if element.tag == "junction" else None
    return index["ownership"][element.tag].get(identity)


def legacy_edge_edit_scope(root, kind, command):
    """Derive destructive edit scope from the native road-list input."""
    flags = {"delete_edge": "--remove-edges.input-file", "merge_edges": "--geometry.remove.keep-edges.input-file"}
    if kind not in flags or not isinstance(command, list) or sum(command.count(flag) for flag in flags.values()) != 1:
        raise ValueError("A legacy edit requires one native road-list operation.")
    flag = flags[kind]
    if command.count(flag) != 1 or command.index(flag) + 1 >= len(command):
        raise ValueError("The native road-list input is missing.")
    if kind == "merge_edges":
        settings = [(index, value) for index, value in enumerate(command)
                    if value == "--geometry.remove" or value.startswith("--geometry.remove=")]
        if len(settings) != 1:
            raise ValueError("Native geometry removal must be enabled exactly once.")
        index, token = settings[0]
        value = (token.split("=", 1)[1] if "=" in token else
                 command[index + 1] if index + 1 < len(command) and not command[index + 1].startswith("-") else "true")
        if value.lower() not in {"true", "1", "yes", "on"}:
            raise ValueError("Native geometry removal must be enabled for a merge.")
    edges = {row.get("id"): row for row in root.findall("edge") if row.get("id") and not row.get("id", "").startswith(":")}
    path = Path(command[command.index(flag) + 1]).resolve(strict=True)
    listed = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    if listed - edges.keys():
        raise ValueError("The native road list contains an unknown source road.")
    targets = listed if kind == "delete_edge" else edges.keys() - listed
    if not targets:
        raise ValueError("A destructive edit requires source roads.")
    nodes = {edge.get(end) for key in targets for edge in [edges[key]] for end in ("from", "to") if edge.get(end)}
    mutable = targets | {key for key, edge in edges.items() if edge.get("from") in nodes or edge.get("to") in nodes}
    return nodes, mutable, targets


def legacy_additive_edit_scope(root, command):
    """Derive added-road and crossing scope from the native plain inputs."""
    nodes = {row.get("id") for row in root.findall("junction")}
    edges = {row.get("id"): row for row in root.findall("edge") if row.get("id") and not row.get("id").startswith(":")}
    endpoints, crossings, files = {}, [], []
    for flag, tag, parent in (("--edge-files", "edge", "edges"), ("--connection-files", "crossing", "connections")):
        if flag not in command:
            continue
        if command.count(flag) != 1 or command.index(flag) + 1 >= len(command):
            raise ValueError("Use one saved plain input per additive native option.")
        path = Path(command[command.index(flag) + 1]).resolve(strict=True)
        parsed = ET.parse(path).getroot()
        if parsed.tag != parent or any(row.tag != tag for row in parsed):
            raise ValueError("Additive inputs may only add roads or declared crossings.")
        files.append(path)
        for row in parsed:
            if tag == "edge":
                identity = row.get("id")
                ends = {key: row.get(key) for key in ("from", "to")}
                if (not identity or identity.startswith(":") or identity in edges or identity in endpoints
                        or any(value not in nodes for value in ends.values())):
                    raise ValueError("An added road requires a new identity and existing source junctions.")
                endpoints[identity] = ends
            else:
                crossings.append(row)
    mutable_nodes = {value for ends in endpoints.values() for value in ends.values()}
    for row in crossings:
        node, road_ids = row.get("node"), row.get("edges", "").split()
        if node not in nodes or not road_ids or any(key not in edges and key not in endpoints for key in road_ids):
            raise ValueError("A crossing requires its source junction and declared roads.")
        if any(node not in (endpoints[key].values() if key in endpoints else
                           (edges[key].get("from"), edges[key].get("to"))) for key in road_ids):
            raise ValueError("Crossing roads must meet the declared junction.")
        mutable_nodes.add(node)
    if not mutable_nodes:
        raise ValueError("An additive edit requires roads or crossings.")
    mutable_edges = set(endpoints) | {key for key, edge in edges.items()
                                     if edge.get("from") in mutable_nodes or edge.get("to") in mutable_nodes}
    return mutable_nodes, mutable_edges, endpoints, files


def local_conversion_input(root, mutable_nodes, mutable_edges, *, join_groups=None, edge_endpoints=None, preserve_turn_permissions=True,
                           removed_edge_ids=None, merge_edge_ids=None, rebuild_adjacent_junctions=False):
    """Keep complete edit junctions and the roads needed to position their ends."""
    if root.find("location") is None:
        return None, {"status": "full_network", "reason": "coordinate_frame_not_declared"}
    if not mutable_edges:
        return None, {"status": "full_network", "reason": "no_incident_ordinary_roads"}
    if preserve_turn_permissions:
        root = with_loaded_turn_permissions(root)
    index = _index(root)
    removed, merge_targets = set(removed_edge_ids or ()), set(merge_edge_ids or ())
    if (removed and merge_targets or (removed | merge_targets) - mutable_edges
            or any(key not in index["edges"] or key in index["ownership"]["edge"] for key in removed | merge_targets)):
        raise ValueError("Declare one destructive operation on ordinary source roads inside its scope.")
    edge_endpoints = edge_endpoints or {}
    if (set(edge_endpoints) - mutable_edges or any(not key or key.startswith(":")
            or set(ends) != {"from", "to"} or any(not isinstance(value, str) or not value for value in ends.values())
            for key, ends in edge_endpoints.items())):
        raise ValueError("Declare complete endpoints for each changed ordinary road within the conversion scope.")
    created = set(edge_endpoints) - index["edges"].keys()
    native_nodes, native_edges = set(mutable_nodes), set(mutable_edges)
    if rebuild_adjacent_junctions:
        ordinary = {key: edge for key, edge in index["edges"].items() if key not in index["ownership"]["edge"]}
        native_nodes.update(edge.get(end) for key, edge in ordinary.items() if key in mutable_edges for end in ("from", "to"))
        native_edges.update(key for key, edge in ordinary.items() if edge.get("from") in native_nodes or edge.get("to") in native_nodes)
    aliases = {node: joined for joined, nodes in (join_groups or {}).items() for node in nodes}
    absorbed = {key for key, edge in index["edges"].items() if key not in index["ownership"]["edge"]
                and edge.get("from") in aliases and aliases.get(edge.get("from")) == aliases.get(edge.get("to"))}
    controllers = {}
    for connection in root.findall("connection"):
        if connection.get("tl"):
            controllers.setdefault(connection.get("tl"), set()).add(_owner(connection, index))
    shared = sorted(key for key, owners in controllers.items()
                    if ((owners & mutable_nodes or key in (join_groups or {})) and owners - mutable_nodes)
                    or (owners & native_nodes and owners - native_nodes))
    if shared:
        return None, {"status": "full_network", "reason": "controller_spans_edit_boundary", "controller_ids": shared}
    selected = native_edges - created
    anchors = []
    if removed and selected <= removed:
        # An isolated deletion still needs one retained native road. Its exterior is copied from the source.
        anchors = sorted(key for key, edge in index["edges"].items() if key not in removed
                         and key not in index["ownership"]["edge"] and not edge.get("bidi"))
        if anchors:
            selected.add(anchors[0])
        else:
            return None, {"status": "full_network", "reason": "no_retained_ordinary_context_road"}
    changed = True
    while changed:
        before = set(selected)
        for ring in root.findall("roundabout"):
            edges = set(ring.get("edges", "").split())
            if edges & selected:
                selected.update(edges)
        changed = selected != before
    unpaired = sorted(key for key in selected if index["edges"][key].get("bidi")
                      and index["edges"][key].get("bidi") not in selected)
    if unpaired:
        return None, {"status": "full_network", "reason": "bidirectional_pair_exceeds_context", "edge_ids": unpaired}
    context = {index["edges"][key].get(end) for key in selected for end in ("from", "to")}
    internal = {key for key, owner in index["ownership"]["edge"].items() if owner in native_nodes}
    kept = selected | internal
    lanes = {lane.get("id") for key in kept for lane in index["edges"][key].findall("lane")}
    connections = []
    for connection in root.findall("connection"):
        if connection.get("from") not in kept or connection.get("to") not in kept:
            continue
        row = deepcopy(connection)
        if row.get("via") not in lanes:
            row.attrib.pop("via", None)
        connections.append(row)
    prohibitions = []
    for row in root.findall("prohibition"):
        paths = [row.get(name, "").split("->") for name in ("prohibitor", "prohibited")]
        if any(len(path) != 2 or any(edge not in index["edges"] for edge in path) for path in paths):
            return None, {"status": "full_network", "reason": "unresolved_prohibition_references"}
        if any(edge in absorbed for path in paths for edge in path):
            return None, {"status": "full_network", "reason": "prohibition_references_absorbed_road"}
        if any(edge in edge_endpoints for path in paths for edge in path):
            return None, {"status": "full_network", "reason": "prohibition_references_rewritten_road"}
        if any(edge in removed | merge_targets for path in paths for edge in path):
            return None, {"status": "full_network", "reason": "prohibition_references_destructive_edit"}
        if all(edge in kept for path in paths for edge in path):
            prohibitions.append(deepcopy(row))
    used_tls = {row.get("tl") for row in connections if row.get("tl")}
    result = ET.Element("net", dict(root.attrib))
    for child in root:
        if child.tag in {"location", "type"} or child.tag == "tlLogic" and child.get("id") in used_tls:
            result.append(deepcopy(child))
        elif child.tag == "edge" and child.get("id") in kept:
            result.append(deepcopy(child))
        elif child.tag == "junction" and (child.get("id") in context or _owner(child, index) in native_nodes):
            row = deepcopy(child)
            for name in ("incLanes", "intLanes"):
                if name in row.attrib:
                    row.set(name, " ".join(value for value in row.get(name).split() if value in lanes))
            if child.get("id") not in native_nodes:
                for request in list(row.findall("request")):
                    row.remove(request)
                row.set("customShape", "true")
            result.append(row)
    result.extend(connections)
    result.extend(prohibitions)
    result.extend(deepcopy(row) for row in root.findall("roundabout") if set(row.get("edges", "").split()) <= selected)
    record = {"status": "local", "mutable_junction_ids": sorted(mutable_nodes),
                    "mutable_edge_ids": sorted(mutable_edges), "context_edge_ids": sorted(selected),
                    "context_junction_ids": sorted(context), "controller_ids": sorted(
                        {key for key, owners in controllers.items() if owners & mutable_nodes} | set(join_groups or {})),
                    "source_normal_edge_count": sum(not key.startswith(":") for key in index["edges"]),
                    "converted_normal_edge_count": len(selected)}
    if join_groups:
        record.update(junction_aliases=aliases, absorbed_edge_ids=sorted(absorbed))
    if edge_endpoints:
        record["edge_endpoints"] = deepcopy(edge_endpoints)
    if rebuild_adjacent_junctions:
        record["native_rebuild_junction_ids"] = sorted(native_nodes)
    if removed:
        record["removed_edge_ids"] = sorted(removed)
        if anchors:
            record["context_anchor_edge_ids"] = [anchors[0]]
    if merge_targets:
        record["merge_edge_ids"] = sorted(merge_targets)
    if preserve_turn_permissions:
        record["preserve_loaded_turn_permissions"] = True
    return result, record


def merge_local_conversion(source_root, native_root, record):
    """Copy the exterior from the source and use native elements only inside the edit."""
    from .osm_access import _translate_permission_declarations
    mutable_nodes, mutable_edges = set(record["mutable_junction_ids"]), set(record["mutable_edge_ids"])
    before, after = _index(source_root), _index(native_root)
    actual = {key for key in after["edges"] if not key.startswith(":")}
    endpoints = record.get("edge_endpoints", {})
    created = set(endpoints) - before["edges"].keys()
    aliases = {}
    if record.get("merge_edge_ids"):
        from .corridor_simplification import _corridor_edge_aliases
        aliases = _corridor_edge_aliases(source_root, native_root)
        if set(aliases) - set(record["merge_edge_ids"]):
            raise ValueError("Local geometry removal absorbed an undeclared road.")
        if any(key in before["edges"] and key not in record["context_edge_ids"] for key in aliases.values()):
            raise ValueError("A merged road collides with a retained exterior road.")
    retained = set(record["context_edge_ids"]) - set(record.get("absorbed_edge_ids", [])) - set(record.get("removed_edge_ids", []))
    if actual != {aliases.get(key, key) for key in retained} | created:
        raise ValueError("Local conversion changed the retained ordinary-road inventory.")
    if record.get("merge_edge_ids"):
        from .corridor_simplification import validate_merged_road_access
        validate_merged_road_access(source_root, native_root, aliases, target_ids=record["merge_edge_ids"])
    junction_aliases = record.get("junction_aliases", {})
    for key in actual:
        members = [identity for identity, merged in aliases.items() if merged == key]
        merged_ends = {}
        if members:
            for end, opposite in (("from", "to"), ("to", "from")):
                candidates = {before["edges"][member].get(end) for member in members} - {
                    before["edges"][member].get(opposite) for member in members}
                if len(candidates) != 1:
                    raise ValueError("Merged source roads have ambiguous endpoints.")
                merged_ends[end] = next(iter(candidates))
        for end in ("from", "to"):
            original = before["edges"][key].get(end) if key in before["edges"] else None
            expected = merged_ends[end] if members else endpoints[key][end] if key in endpoints else junction_aliases.get(original, original)
            if after["edges"][key].get(end) != expected:
                raise ValueError("Local conversion changed an undeclared ordinary-road endpoint.")
    locations = [root.find("location") for root in (source_root, native_root)]
    frames = [(tuple(map(float, row.get("netOffset", "0,0").split(","))), row.get("projParameter"))
              if row is not None else None for row in locations]
    if frames[0] is None or frames[0] != frames[1]:
        raise ValueError("Local conversion changed the declared coordinate frame.")
    controllers = set(record["controller_ids"])
    mutable_edges.update(aliases.values())

    def belongs(child, index):
        if child.tag == "edge":
            return child.get("id") in mutable_edges or _owner(child, index) in mutable_nodes
        if child.tag in {"junction", "connection"}:
            return _owner(child, index) in mutable_nodes
        if child.tag == "roundabout":
            return bool(set(child.get("edges", "").split()) & mutable_edges)
        return child.tag == "tlLogic" and child.get("id") in controllers

    def identity(child):
        if child.tag == "connection":
            return tuple(child.get(key) for key in ("from", "fromLane", "to", "toLane"))
        if child.tag == "tlLogic":
            return child.get("id"), child.get("programID")
        if child.tag == "roundabout":
            return tuple(sorted(child.get("edges", "").split()))
        return child.get("id")

    result = deepcopy(source_root)
    ranks = {name: rank for rank, name in enumerate(("location", "type", "edge", "tlLogic", "junction", "connection", "prohibition", "roundabout"))}
    for name in ("edge", "tlLogic", "junction", "connection", "roundabout"):
        native_rows = [child for child in native_root if child.tag == name and belongs(child, after)]
        additions = {identity(child): deepcopy(child) for child in native_rows}
        if len(additions) != len(native_rows):
            raise ValueError(f"Local conversion requires unique {name} identities.")
        for child in additions.values():
            _translate_permission_declarations(child, source_version=native_root.get("version", "0.0"),
                                               target_version=source_root.get("version", "0.0"))
        # SUMO load order affects seeded trajectories, even when all road attributes are identical.
        retained = []
        for child in result:
            if child.tag == name and belongs(child, before):
                # Native connection order defines the request indices in the replacement junction's conflict table.
                replacement = additions.pop(identity(child), None) if name != "connection" else None
                if replacement is not None:
                    retained.append(replacement)
            else:
                retained.append(child)
        result[:] = retained
        at = next((i for i, child in enumerate(result) if ranks.get(child.tag, 8) > ranks[name]), len(result))
        for offset, child in enumerate(additions.values()):
            result.insert(at + offset, child)
    boxes = [row.get("convBoundary") for row in locations]
    if all(boxes):
        a, b = [list(map(float, value.split(","))) for value in boxes]
        result.find("location").set("convBoundary", ",".join(map(str, [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])])))
    return result


def run_scoped_native_conversion(*, command, mutable_nodes, mutable_edges, output_dir, prefix,
                                 timeout_seconds, join_groups=None, merge_source_file=None, edge_endpoints=None,
                                 merge_edge_ids=None, command_runner=None):
    """Run one native conversion and retain its local input and full merged proposal."""
    from .command_runner import run_command
    execute = command_runner or run_command
    command = with_geometry_precision(command)
    source = Path(command[command.index("--sumo-net-file") + 1]).resolve(strict=True)
    output = Path(command[command.index("--output-file") + 1]).resolve()
    if output == source or output.exists():
        raise ValueError("Use a new conversion output separate from its source.")
    source_bytes = source.read_bytes()
    root = ET.fromstring(source_bytes)
    occupied = {row.get("id") for row in root.findall("junction") + root.findall("tlLogic")}
    occupied.update(row.get("tl") for row in root.findall("connection") if row.get("tl"))
    if set(join_groups or {}) & occupied:
        raise ValueError("Use new joined junction and controller IDs.")
    merge_source = Path(merge_source_file).resolve(strict=True) if merge_source_file is not None else source
    merge_bytes = source_bytes if merge_source == source else merge_source.read_bytes()
    merge_root = root if merge_source == source else ET.fromstring(merge_bytes)
    subset, record = local_conversion_input(root, set(mutable_nodes), set(mutable_edges), join_groups=join_groups,
                                            edge_endpoints=edge_endpoints, merge_edge_ids=merge_edge_ids)
    if merge_edge_ids:
        record["operation"] = {"operation": "merge_edges", "target_ids": sorted(merge_edge_ids)}
    if subset is None:
        prepared = with_loaded_turn_permissions(root)
        subset_bytes = source_bytes
        if ET.tostring(prepared) != ET.tostring(root):
            subset_bytes = ET.tostring(prepared, encoding="utf-8", xml_declaration=True)
            record["preserve_loaded_turn_permissions"] = True
    else:
        subset_bytes = ET.tostring(subset, encoding="utf-8", xml_declaration=True)
    record["source"] = {"path": str(source), "sha256": sha256(source_bytes).hexdigest()}
    record["merge_source"] = {"path": str(merge_source), "sha256": sha256(merge_bytes).hexdigest()}
    input_file = Path(output_dir) / f"{prefix}.{'local' if subset is not None else 'full'}-input.net.xml"
    suffix = "native-local-output" if subset is not None else "native-output"
    raw = Path(output_dir) / f"{prefix}.{suffix}.net.xml"
    failure_file = Path(output_dir) / f"{prefix}.conversion-record.json"
    if input_file.exists() or raw.exists() or failure_file.exists() or input_file.resolve() == output or raw.resolve() == output:
        raise ValueError("Use a new conversion output separate from its native snapshot.")
    with input_file.open("xb") as stream:
        stream.write(subset_bytes)
    record["input"] = {"path": str(input_file.resolve()), "sha256": file_sha256(input_file)}
    command[command.index("--sumo-net-file") + 1] = str(input_file.resolve())
    command[command.index("--output-file") + 1] = str(raw.resolve())
    record["command"] = list(command)
    result, exception = None, None
    try:
        result = execute(command, cwd=output_dir, timeout_seconds=timeout_seconds)
        returncode = result.get("returncode") if isinstance(result, dict) else result.returncode
        if raw.is_file():
            record["native_output"] = {"path": str(raw.resolve()), "sha256": file_sha256(raw),
                                       "status": "complete" if returncode == 0 else "incomplete"}
        if returncode == 0 and raw.is_file():
            if subset is not None:
                merged = merge_local_conversion(merge_root, ET.parse(raw).getroot(), record)
                content = ET.tostring(merged, encoding="utf-8", xml_declaration=True)
                proposal = Path(output_dir) / f"{prefix}.merged-proposal.net.xml"
                with proposal.open("xb") as stream:
                    stream.write(content)
                output.write_bytes(content)
                record["merged_proposal"] = {"path": str(proposal.resolve()), "sha256": file_sha256(proposal), "stage": "before_exterior_restoration"}
            else:
                output.write_bytes(raw.read_bytes())
    except Exception as error:
        exception = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        record["source_unchanged"] = file_sha256(source) == record["source"]["sha256"]
        record["merge_source_unchanged"] = file_sha256(merge_source) == record["merge_source"]["sha256"]
        failed = exception is not None or result is None or returncode != 0 or not output.is_file()
        if failed or not record["source_unchanged"] or not record["merge_source_unchanged"]:
            from .artifact_io import write_json_atomic
            execution = dict(result) if isinstance(result, dict) else {
                key: getattr(result, key, None) for key in ("command", "returncode", "stdout", "stderr", "error", "status")}
            execution.update(command=command, cwd=str(output_dir))
            write_json_atomic(failure_file, {"status": "failed" if failed else "source_changed",
                "command": execution, "local_conversion": record, "exception": exception})
    return result, record


def validate_local_conversion(record, source_file, *, command=None):
    """Rebuild the local input and merged proposal from saved source and native files."""
    from .network_edit_operations import scoped_operation_scope, validate_scoped_operation
    source_root = ET.parse(source_file).getroot()
    policy = record.get("lane_count_scope_policy")
    if policy not in {None, "overlap_connector_closure"} or policy and record.get("operation", {}).get("operation") != "set_lane_count":
        raise ValueError("Local conversion has an unsupported lane-count scope policy.")
    if record.get("status") == "full_network" and record.get("preserve_loaded_turn_permissions") is True:
        expected = _expected_conversion_input(record, with_loaded_turn_permissions(source_root))
        input_file = Path(record["input"]["path"])
        if file_sha256(input_file) != record["input"]["sha256"] or input_file.read_bytes() != expected:
            raise ValueError("Full conversion input differs from its source-derived permissions.")
        return input_file
    operation = record["operation"]
    if operation.get("operation") == "additive":
        nodes, edges, endpoints, _ = legacy_additive_edit_scope(source_root, command)
        subset, expected = local_conversion_input(source_root, nodes, edges, edge_endpoints=endpoints,
            preserve_turn_permissions=record.get("preserve_loaded_turn_permissions") is True, rebuild_adjacent_junctions=True)
        return _validate_local_artifacts(record, source_root, subset, expected)
    if operation.get("operation") in {"delete_edge", "merge_edges"}:
        kind = operation["operation"]
        nodes, edges, targets = legacy_edge_edit_scope(source_root, kind, command)
        if set(operation.get("target_ids", [])) != targets:
            raise ValueError("The local destructive operation differs from its native road list.")
        subset, expected = local_conversion_input(source_root, nodes, edges,
            removed_edge_ids=targets if kind == "delete_edge" else None,
            merge_edge_ids=targets if kind == "merge_edges" else None,
            preserve_turn_permissions=record.get("preserve_loaded_turn_permissions") is True)
        return _validate_local_artifacts(record, source_root, subset, expected)
    if validate_scoped_operation(operation, source_root):
        raise ValueError("Local conversion has an invalid scoped operation.")
    nodes, edges = scoped_operation_scope(source_root, operation, include_overlap_connectors=policy is not None)
    params = operation["params"]
    groups = {params["join_id"]: params["node_ids"]} if operation["operation"] in {"join_junctions", "absorb_reciprocal_fragment"} else None
    subset, expected = local_conversion_input(source_root, nodes, edges, join_groups=groups,
        preserve_turn_permissions=record.get("preserve_loaded_turn_permissions") is True)
    return _validate_local_artifacts(record, source_root, subset, expected)


def _validate_local_artifacts(record, source_root, subset, expected):
    if subset is None or any(record.get(key) != value for key, value in expected.items()
                             if key != "context_anchor_edge_ids" or key in record):
        raise ValueError("Local conversion scope differs from the source operation.")
    input_file = Path(record["input"]["path"])
    if (file_sha256(input_file) != record["input"]["sha256"]
            or input_file.read_bytes() != _expected_conversion_input(record, subset)):
        raise ValueError("Local conversion input differs from its source-derived subset.")
    raw = Path(record["native_output"]["path"])
    merged = Path(record["merged_proposal"]["path"])
    if file_sha256(raw) != record["native_output"]["sha256"] or file_sha256(merged) != record["merged_proposal"]["sha256"]:
        raise ValueError("Local conversion artifacts changed.")
    root = merge_local_conversion(source_root, ET.parse(raw).getroot(), expected)
    if merged.read_bytes() != ET.tostring(root, encoding="utf-8", xml_declaration=True):
        raise ValueError("The merged proposal differs from its source and native result.")
    return input_file


def _expected_conversion_input(record, root):
    """Reconstruct source geometry hints without trusting their saved output."""
    expected = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    frozen = record.get("geometry_frozen_input")
    if frozen is None:
        return expected
    operation = record["operation"]
    original = record["unfrozen_input"]
    path = Path(original["path"])
    kind = operation["operation"]
    if (kind not in {"set_lane_count", "add_lane_connection", "delete_lane_connection", "suppress_turnaround"}
            or frozen["source"] != original
            or frozen["frozen_input"] != record["input"]
            or file_sha256(path) != original["sha256"] or path.read_bytes() != expected):
        raise ValueError("Frozen geometry input does not match its source-derived edit input.")
    from .network_geometry_freeze import _freeze_geometry_hints, _lane_count_automatic_pedestrian_nodes
    root = deepcopy(root)
    policy = frozen.get("junction_contour_policy")
    if policy not in {None, "preserve", "preserve_motor", "rebuild_scope"} or (
            kind != "set_lane_count" and policy in {"preserve_motor", "rebuild_scope"}):
        raise ValueError("Frozen geometry input has an unsupported junction contour policy.")
    # Legacy count plans allowed automatic contours. Their saved inputs remain reproducible.
    automatic = (frozenset(j.get("id") for j in root.findall("junction")
        if j.get("type") != "internal" and j.get("customShape", "false") not in {"true", "1"})
        if kind == "set_lane_count" and policy is None else frozenset())
    if policy == "preserve_motor":
        automatic = _lane_count_automatic_pedestrian_nodes(root)
    if policy == "rebuild_scope":
        from .network_edit_operations import scoped_operation_scope
        nodes, _ = scoped_operation_scope(root, operation, include_overlap_connectors=True)
        automatic = frozenset(j.get("id") for j in root.findall("junction") if j.get("id") in nodes
            and j.get("type") != "internal" and j.get("customShape", "false") not in {"true", "1"})
    edges = frozenset({operation["params"]["edge_id"]}) if kind == "set_lane_count" else frozenset()
    hints = _freeze_geometry_hints(root, edges, automatic)
    if any(frozen.get(key) != value for key, value in hints.items()):
        raise ValueError("Frozen geometry hints differ from the declared edit operation.")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def validate_join_boundary_conversion(report, source_file):
    """Reconstruct the post-join conversion input and its retained exterior."""
    from .junction_boundary_rebuild import _groups, _diagnostic_signal_view
    source = Path(source_file).resolve(strict=True)
    original = report["source_network"]
    joined = report["joined_network"]
    record = report["local_conversion"]
    if not Path(original["path"]).samefile(source) or file_sha256(source) != original["sha256"]:
        raise ValueError("Join-boundary conversion does not match its original source.")
    joined_path = Path(joined["path"])
    if file_sha256(joined_path) != joined["sha256"] or joined != record["source"]:
        raise ValueError("The saved joined input changed.")
    groups = {row["join_id"]: row["source_node_ids"] for row in report["plan"]["groups"]}
    original_root = ET.parse(source).getroot()
    if report["signal_policy"] == "rebuild_diagnostic":
        original_root, _ = _diagnostic_signal_view(original_root, groups)
    groups = _groups(original_root, groups)
    nodes = set(groups) | set().union(*groups.values()) | set(report["adjacent_geometry"]["requested_junction_ids"])
    root = ET.parse(joined_path).getroot()
    edges = {row.get("id") for row in root.findall("edge") if not row.get("id", "").startswith(":")
             and any(row.get(end) in nodes for end in ("from", "to"))}
    subset, expected = local_conversion_input(root, nodes, edges,
        preserve_turn_permissions=record.get("preserve_loaded_turn_permissions") is True)
    input_file = _validate_local_artifacts(record, root, subset, expected)
    command = report["commands"][0]["command"]
    if command.count("--sumo-net-file") != 1 or command.index("--sumo-net-file") + 1 >= len(command):
        raise ValueError("Join-boundary native command has no unique input path.")
    if Path(command[command.index("--sumo-net-file") + 1]).resolve() != input_file.resolve():
        raise ValueError("Join-boundary native command uses another input.")
    if (command.count("--output-file") != 1 or command.index("--output-file") + 1 >= len(command)
            or Path(command[command.index("--output-file") + 1]).resolve() != Path(record["native_output"]["path"]).resolve()):
        raise ValueError("Join-boundary native command does not name its retained native output.")
    return input_file
