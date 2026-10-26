"""INT integrity sources besides the connection-mode audit (M3), on the scope junctions only.

- Request matrix: SUMO writes one ``<request>`` per link with ``foes`` and ``response`` bitstrings (link k is the k-th
  character from the right). A link is never its own foe, a response bit names a foe, and the indices are 0..n-1.
  netconvert can write asymmetric foe pairs (clean Ring1 nominal holds 36 such bits on 5 signalized junctions), so like
  every INT source this one is differential: against the goal's frozen baseline and, in the gate, against the parent.
- Independent movement safety (``corridor/conflict_graph.py``): movement conflicts from internal path geometry, never
  from request/foes, with protected-green, linkIndex2, shared-signal-group and unmapped-link checks. It runs on the
  scope junctions plus every junction that shares one of their traffic-light controllers, cut out of the network.

Both are model integrity findings, not traffic-safety or field claims. Every INT row also names the road lanes its
finding refers to (``lanes``), so a hotspot can be attributed below the junction level.
"""

from __future__ import annotations

import dataclasses
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter

from . import ids

AUDITED_SEVERITIES = ("safety", "structural", "review")
_CANONICAL_ID = re.compile(r"^([a-z_]+?)_[0-9a-f]{16,64}$")
# Connection-mode audit findings whose third token is the connection's document-order index in the net
# (``core/connection_mode_audit.py``); an edit anywhere that adds or removes a connection renumbers all later ones.
CONNECTION_INDEXED = frozenset({
    "movement_connection_index_duplicate", "source_lane_invalid", "target_lane_invalid", "lane_rank_jump",
    "internal_path_unusually_long", "link_index_invalid", "link_index2_invalid", "controller_logic_missing",
    "link_index_out_of_program_state", "lane_shape_missing_or_short", "missing_direct_via_lane",
    "direct_via_lane_not_found", "direct_via_lane_not_internal", "internal_path_cycle",
    "internal_path_outgoing_count_not_one", "internal_path_target_mismatch", "internal_continuation_lane_not_found",
    "internal_continuation_not_internal", "internal_path_final_lane_invalid", "internal_path_final_lane_mismatch",
    "internal_path_bounded_trace_exhausted", "path_endpoint_gap", "path_width_transition_centerline_offset",
    "internal_path_mode_permission_empty"})
_MEASURED = re.compile(r"^-?\d+(?:\.\d+)?m$")
_SEPARATORS = re.compile(r"[:|>,;()\s]+")


def connection_keys(root: ET.Element) -> list[str]:
    """``<from>_<fromLane>><to>_<toLane>`` of every connection in document order."""
    return [ids.connection_key(row) for row in root.findall("connection")]


def audit_measure(finding: str) -> float | None:
    """The largest measured length (``1.589m``) a connection-mode finding carries, or None; ``audit_identity`` drops
    these tokens, so a gate compares them separately (a pre-existing gap that grows is still a regression)."""
    values = [float(token[:-1]) for token in str(finding).split(":") if _MEASURED.match(token)]
    return max(values) if values else None


def road_lane_index(root: ET.Element) -> tuple[set[str], dict[str, int]]:
    """Road lane ids and the lane count of every road edge (``named_lanes`` needs both)."""
    lanes, counts = set(), {}
    for edge in root.findall("edge"):
        if edge.get("function") in {"internal", "crossing", "walkingarea"}:
            continue
        rows = [lane.get("id") for lane in edge.findall("lane")]
        lanes.update(rows)
        counts[edge.get("id")] = len(rows)
    return lanes, counts


def named_lanes(texts, index: tuple[set[str], dict[str, int]]) -> list[str]:
    """Road lanes that finding ids or texts name: a lane id token, or an edge id token followed by a lane index
    (``W_in:0``; ``W_in|S_out|0|0`` names ``S_out_0``). Internal lanes and junction ids name none."""
    lanes, counts = index
    found = set()
    for text in texts:
        tokens = _SEPARATORS.split(str(text or ""))
        for position, token in enumerate(tokens):
            following = tokens[position + 1] if position + 1 < len(tokens) else ""
            if token in lanes:
                found.add(token)
            elif token in counts and following.isdigit() and int(following) < counts[token]:
                found.add(f"{token}_{int(following)}")
    return sorted(found)


def junction_prefix_owner(edge_id: str, junctions) -> str | None:
    """The junction an internal, crossing or walking-area edge ``:<junction>_<n>`` belongs to by its id (the longest
    ``<junction>`` prefix that is a junction)."""
    candidate = edge_id[1:] if edge_id.startswith(":") else ""
    while "_" in candidate:
        candidate = candidate.rpartition("_")[0]
        if candidate in junctions:
            return candidate
    return None


def audit_identity(finding: str, keys: list[str]) -> str:
    """A connection-mode audit finding with its document-order connection index replaced by the connection's lanes
    and its measured lengths dropped, so it keeps its identity when an edit elsewhere renumbers the connections or
    re-derives a length by a millimetre. The finding text itself is kept separately."""
    tokens = str(finding).split(":")

    def key(token: str) -> str:
        index = int(token)
        return keys[index] if 0 <= index < len(keys) else f"#{index}"

    if len(tokens) > 2 and tokens[1] in CONNECTION_INDEXED and tokens[2].isdigit():
        tokens[2] = key(tokens[2])
        if tokens[1] == "internal_path_target_mismatch" and len(tokens) > 3 and tokens[3].isdigit():
            tokens[3] = key(tokens[3])
    return ":".join(token for token in tokens if not _MEASURED.match(token))


# Audit kinds whose fields (the tokens after the kind) carry bare lane indices: field position -> position of the
# edge field it indexes; "rest" indexes every field from that position on (``core/connection_mode_audit.py``).
_INDEXED_FIELDS = {
    "duplicate_direct_connection": {2: 0, 3: 1},  # from|to|fromLane|toLane|via:count
    "duplicate_lane_binding": {2: 0, 3: 1},  # from|to|fromLane|toLane:count
    "ambiguous_target_lane_fanout": {1: 0, "rest": (3, 2)},  # source|fromLane|target:toLane,...
    "destination_lane_merge": {2: 1, "rest": (3, 0)},  # source|target|toLane:fromLane,...
    "crossed_lane_mapping": {2: 0, 3: 1, 4: 0, 5: 1},  # source:target:a>b:c>d
}
_KEEP_SEPARATORS = re.compile(r"([:|>,;()\s]+)")


def remap_lane(lane: str, mapping: dict[str, dict[str, str]]) -> str | None:
    """A road lane id under a declared lane remap ``{edge: {old index: new index}}``; None for a removed lane."""
    edge, _, index = lane.rpartition("_")
    if edge not in mapping or not index.isdigit():
        return lane
    new = mapping[edge].get(index)
    return None if new is None else f"{edge}_{new}"


def remap_identity(identity: str, mapping: dict[str, dict[str, str]]) -> str | None:
    """An INT or structural finding identity under a declared lane remap, None when it names a removed lane.

    Lane ids (``<edge>_<index>``, connection keys ``a_0>b_1``), an edge token followed by lane indices
    (``incoming_motorized_lane_without_connection:<edge>:<index>``) and the positional lane fields of the audit kinds
    in ``_INDEXED_FIELDS`` are remapped; edge-level findings without a lane index keep their identity."""
    if not mapping:
        return identity
    parts = _KEEP_SEPARATORS.split(identity)
    tokens = parts[0::2]

    def index(edge: str, value: str) -> str | None:
        return mapping[edge].get(value) if edge in mapping and value.isdigit() else value

    out = list(tokens)
    done = set()
    kind = next((position for position, token in enumerate(tokens) if token in _INDEXED_FIELDS), None)
    if kind is not None:
        fields, spec = tokens[kind + 1:], _INDEXED_FIELDS[tokens[kind]]
        positions = {position: edge for position, edge in spec.items() if position != "rest"}
        if "rest" in spec:
            start, edge = spec["rest"]
            positions.update({position: edge for position in range(start, len(fields))})
        for position, edge in positions.items():
            if position < len(fields) and edge < len(fields):
                value = index(fields[edge], fields[position])
                if value is None:
                    return None
                out[kind + 1 + position] = value
                done.add(kind + 1 + position)
    for position, token in enumerate(tokens):
        if position in done:
            continue
        if token in mapping:
            # An edge token followed by lane indices (``<edge>:<index>``).
            following = position + 1
            while following < len(tokens) and tokens[following].isdigit() and following not in done:
                value = index(token, tokens[following])
                if value is None:
                    return None
                out[following] = value
                done.add(following)
                following += 1
            continue
        lane = remap_lane(token, mapping)
        if lane is None:
            return None
        out[position] = lane
    parts[0::2] = out
    return "".join(parts)


def comparable(rows: list[dict], mapping: dict[str, dict[str, str]] | None, *, remap: bool,
               renumbered: set[str] | None = None) -> list[tuple]:
    """``(identity, measure)`` of INT rows for a differential comparison across a declared lane remap.

    With ``remap`` (the parent's rows) each identity is remapped (``remap_identity``) and a row naming a removed lane
    is dropped. An independent-safety identity hashes lane ordinals from the curb, so on an edge whose surviving lanes
    change that ordinal (``renumbered``; default every remapped edge) a safety row naming one of its lanes is compared
    by its category and its (remapped) lanes instead, on both nets; elsewhere its hash still names the same finding."""
    mapping = mapping or {}
    renumbered = set(mapping) if renumbered is None else set(renumbered)
    result = []
    for row in rows:
        identity = row["id"]
        if row.get("source") == "independent_safety" and any(
                lane.rpartition("_")[0] in renumbered for lane in row.get("lanes") or []):
            lanes = [remap_lane(lane, mapping) if remap else lane for lane in row["lanes"]]
            if None in lanes:
                continue
            _, anchor, category, _ = (identity.split(":", 3) + ["", "", ""])[:4]
            identity = f"safety:{anchor}:{category}:lanes:{'|'.join(sorted(lanes))}"
        elif remap:
            identity = remap_identity(identity, mapping)
            if identity is None:
                continue
        result.append((identity, row.get("measure")))
    return result


def _bit(bits: str, index: int) -> bool:
    return bits[len(bits) - 1 - index] == "1"


def request_matrix_findings(root: ET.Element, junction_ids: list[str]) -> list[dict]:
    """Self-foes, responses that name no foe, asymmetric foe pairs and broken index sets per scope junction.

    Request k is named by the connection whose via lane is the junction's k-th ``intLanes`` entry (SUMO writes them
    in link order), so a link renumbering elsewhere at the junction keeps the identity; the index is the fallback.
    Rows whose bitstrings do not have one bit per request are skipped (the connection-mode audit reports them)."""
    scope, rows = set(junction_ids), []
    by_via = {row.get("via"): ids.connection_key(row)
              for row in root.findall("connection") if row.get("via") and not row.get("from", "").startswith(":")}
    for junction in root.findall("junction"):
        junction_id = junction.get("id")
        requests = junction.findall("request")
        if junction_id not in scope or not requests:
            continue
        count = len(requests)
        internal = (junction.get("intLanes") or "").split()
        names = [by_via.get(internal[index], internal[index]) if index < len(internal) else str(index)
                 for index in range(count)]

        def add(kind: str, links: tuple[int, ...], text: str, ordered: bool = True) -> None:
            labels = [names[link] if 0 <= link < count else str(link) for link in links]
            detail = "|".join(labels if ordered else sorted(labels))
            rows.append({"id": f"request:{junction_id}:{kind}:{detail}", "junction_id": junction_id,
                         "finding": f"{text} ({detail})"})

        try:
            indices = [int(request.get("index", "")) for request in requests]
        except ValueError:
            indices = []
        if sorted(indices) != list(range(count)):
            rows.append({"id": f"request:{junction_id}:index_set:{count}", "junction_id": junction_id,
                         "finding": f"request indices are not 0..{count - 1}"})
            continue
        foes = {index: request.get("foes", "") for index, request in zip(indices, requests)}
        responses = {index: request.get("response", "") for index, request in zip(indices, requests)}
        valid = {index for index in foes if len(foes[index]) == count and len(responses[index]) == count}
        for index in sorted(valid):
            if _bit(foes[index], index):
                add("self_foe", (index,), f"link {index} is listed as its own foe")
            for other in range(count):
                if _bit(responses[index], other) and not _bit(foes[index], other):
                    add("response_not_foe", (index, other), f"link {index} yields to link {other}, not a foe")
                if other > index and other in valid and _bit(foes[index], other) != _bit(foes[other], index):
                    add("asymmetric_foes", (index, other), f"foes[{index}][{other}] != foes[{other}][{index}]",
                        ordered=False)
    return rows


def _owners(network) -> dict[str, str]:
    """Internal, crossing and walking-area edge -> its junction (intLanes first, then the longest ':<id>_' prefix)."""
    owner = {network.lane_edge_ids[lane]: junction_id for junction_id, junction in network.junctions.items()
             for lane in junction.internal_lane_ids if lane in network.lane_edge_ids}
    for edge_id, edge in network.edges.items():
        if edge.external or edge_id in owner:
            continue
        junction = junction_prefix_owner(edge_id, network.junctions)
        if junction is not None:
            owner[edge_id] = junction
    return owner


def _connection_junction(network, owner: dict[str, str], edge_id: str) -> str | None:
    edge = network.edges.get(edge_id)
    if edge is None:
        return None
    return edge.to_junction if edge.external else owner.get(edge_id)


def controller_groups(network, junction_ids: list[str]) -> list[list[str]]:
    """Scope junctions grouped by shared traffic-light controllers, each closed over the controllers' junctions."""
    owner = _owners(network)
    at = {}
    for connection in network.connections:
        if connection.controller_id:
            junction = _connection_junction(network, owner, connection.from_edge)
            if junction is not None:
                at.setdefault(junction, set()).add(connection.controller_id)
    by_controller: dict[str, set[str]] = {}
    for junction, controllers in at.items():
        for controller in controllers:
            by_controller.setdefault(controller, set()).add(junction)
    remaining, groups = sorted(set(junction_ids) & set(network.junctions)), []
    while remaining:
        group, frontier = set(), [remaining[0]]
        while frontier:
            junction = frontier.pop()
            if junction in group:
                continue
            group.add(junction)
            for controller in at.get(junction, ()):
                frontier.extend(by_controller[controller] - group)
        groups.append(sorted(group))
        remaining = [junction for junction in remaining if junction not in group]
    return groups


def scoped_network(network, junction_ids: list[str]):
    """The junctions, their incident road edges, their internal edges, the connections leaving those junctions and
    the programs of the controllers they use; neighbouring junctions are left out."""
    scope, owner = set(junction_ids), _owners(network)
    edges = {edge_id: edge for edge_id, edge in network.edges.items()
             if (edge.external and (edge.from_junction in scope or edge.to_junction in scope))
             or (not edge.external and owner.get(edge_id) in scope)}
    connections = tuple(connection for connection in network.connections
                        if connection.from_edge in edges and connection.to_edge in edges
                        and _connection_junction(network, owner, connection.from_edge) in scope)
    controllers = {connection.controller_id for connection in connections if connection.controller_id}
    return dataclasses.replace(
        network, edges=edges, lanes={lane.lane_id: lane for edge in edges.values() for lane in edge.lanes},
        lane_edge_ids={lane: edge for lane, edge in network.lane_edge_ids.items() if edge in edges},
        connections=connections,
        junctions={junction_id: row for junction_id, row in network.junctions.items() if junction_id in scope},
        tls_programs={key: value for key, value in network.tls_programs.items() if key in controllers})


def semantic_names(snapshot) -> dict[str, str]:
    """Canonical entity id -> a geometry-free name (edge ids, lane ordinal from the curb, turn class, link index).

    Canonical ids hash boundary geometry and lane widths, so a stop-line or width edit renames every movement of the
    junction; the names keep a finding's identity across such edits, which a differential gate needs."""
    entities, names = {entity.stable_entity_id: entity for entity in snapshot.entities}, {}

    def name(entity_id: str) -> str:
        if entity_id in names:
            return names[entity_id]
        entity = entities[entity_id]
        payload, kind = entity.payload, entity.kind

        def ref(row: dict, key: str) -> str:
            target = row.get(key)
            return name(target) if isinstance(target, str) and target in entities else str(target)

        if kind == "boundary_port":
            value = f"port:{','.join(payload.get('source_anchor_refs') or [])}:{payload.get('flow')}"
        elif kind == "approach":
            value = f"approach:{ref(payload, 'boundary_port_id')}"
        elif kind == "lane_role":
            value = f"{ref(payload, 'boundary_port_id')}#{payload.get('ordinal_from_curb')}:{payload.get('role')}"
        elif kind == "movement":
            value = "movement:" + "|".join(sorted(
                f"{ref(row, 'source_lane_role_id')}>{ref(row, 'destination_lane_role_id')}:{row.get('turn_class')}"
                for row in payload.get("variants") or [] if isinstance(row, dict)))
        elif kind in {"internal_path", "pedestrian_control_binding"}:
            value = f"{kind}:{ref(payload, 'movement_id')}"
        elif kind == "signal_group":
            value = f"signal_group:{sorted(payload.get('source_link_indices') or [])}"
        else:
            value = kind
        names[entity_id] = value
        return value

    return {entity_id: name(entity_id) for entity_id in entities}


def _role_lanes(snapshot, network, side) -> dict[str, str]:
    """Lane-role entity id -> the road lane it stands for (its port's edge and its ordinal from the curb)."""
    from ..corridor.canonicalizer import _ordinal_from_curb

    entities, result = {entity.stable_entity_id: entity for entity in snapshot.entities}, {}
    for entity in snapshot.entities:
        if entity.kind != "lane_role":
            continue
        port = entities.get(entity.payload.get("boundary_port_id"))
        refs = (port.payload.get("source_anchor_refs") or []) if port is not None else []
        edges = [ref.split("/", 1)[1] for ref in refs if isinstance(ref, str) and ref.startswith("external-edge/")]
        if len(edges) != 1 or edges[0] not in network.edges:
            continue
        lanes = network.edges[edges[0]].lanes
        for lane in lanes:
            ordinal = _ordinal_from_curb(lane.ordinal, lane_count=len(lanes), traffic_side=side)
            if ordinal == entity.payload.get("ordinal_from_curb"):
                result[entity.stable_entity_id] = lane.lane_id
    return result


def _finding_lanes(finding, entities: dict, role_lanes: dict[str, str]) -> list[str]:
    """Road lanes of the movements, signal groups, internal paths and lane roles a finding's subject and witness
    reference (a controller or program subject is not expanded, so it never names every lane)."""
    found, seen = set(), set()

    def visit(entity_id) -> None:
        entity = entities.get(entity_id) if isinstance(entity_id, str) else None
        if entity is None or entity_id in seen:
            return
        seen.add(entity_id)
        payload = entity.payload
        if entity.kind == "lane_role" and entity_id in role_lanes:
            found.add(role_lanes[entity_id])
        elif entity.kind == "movement":
            for row in payload.get("variants") or []:
                if isinstance(row, dict):
                    visit(row.get("source_lane_role_id"))
                    visit(row.get("destination_lane_role_id"))
        elif entity.kind in {"internal_path", "pedestrian_control_binding"}:
            visit(payload.get("movement_id"))
        elif entity.kind == "signal_group":
            for movement in payload.get("movement_ids") or []:
                visit(movement)

    def walk(value) -> None:
        if isinstance(value, str) and _CANONICAL_ID.match(value):
            visit(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(finding.subject_id)
    walk(finding.witness)
    return sorted(found)


def _normalized(value, names: dict[str, str]):
    if isinstance(value, str):
        match = _CANONICAL_ID.match(value)
        return value if match is None else names.get(value, match[1])
    if isinstance(value, (list, tuple)):
        items = [_normalized(item, names) for item in value]
        return sorted(items, key=repr)
    if isinstance(value, dict):
        return {key: _normalized(item, names) for key, item in sorted(value.items())}
    return value


def role_free(witness):
    """A normalized witness without the role order of a symmetric pair.

    ``audit_independent_movement_safety`` reports a conflict between two permissive movements once, as
    (``permissive_movement_id``, ``conflicting_movement_id``) in canonical-id order (``corridor/conflict_graph.py``),
    and canonical ids hash geometry, so a geometry edit can swap the roles of the same pair (seen at LSA104_part0 on
    Ring1 after a width edit nearby). When the witness says the relation is checked both ways (``allow_reverse``),
    the two roles become one sorted pair."""
    if not isinstance(witness, dict) or not (witness.get("request_relation") or {}).get("allow_reverse"):
        return witness
    if "permissive_movement_id" not in witness or "conflicting_movement_id" not in witness:
        return witness
    rest = {key: value for key, value in witness.items()
            if key not in {"permissive_movement_id", "conflicting_movement_id"}}
    pair = sorted([witness["permissive_movement_id"], witness["conflicting_movement_id"]], key=repr)
    return {**rest, "movement_pair": pair}


def safety_findings(root: ET.Element, junction_ids: list[str]) -> list[dict]:
    """Independent movement-safety findings per controller group of the scope junctions, anchored at the group's
    first junction and identified by category plus the geometry-free names of their subject and witness. A group the
    audit cannot canonicalize is itself a finding."""
    from ..corridor.canonicalizer import canonicalize_raw_network
    from ..corridor.conflict_graph import audit_independent_movement_safety
    from ..corridor.enums import TrafficSide
    from ..corridor.netxml import parse_net_xml

    if not junction_ids:
        return []
    network = parse_net_xml(root)
    side = TrafficSide.LEFT if network.lefthand else TrafficSide.RIGHT
    rows = []
    for group in controller_groups(network, junction_ids):
        anchor = group[0]
        try:
            snapshot = canonicalize_raw_network(scoped_network(network, group), traffic_side=side)
            report = audit_independent_movement_safety(snapshot)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
            rows.append({"id": f"safety:{anchor}:audit_failed:{type(error).__name__}", "junction_id": anchor,
                         "finding": f"independent safety audit failed on {group}: {error}"})
            continue
        names, seen = semantic_names(snapshot), Counter()
        entities = {entity.stable_entity_id: entity for entity in snapshot.entities}
        role_lanes = _role_lanes(snapshot, network, side)
        for finding in report.findings:
            if finding.severity.value not in AUDITED_SEVERITIES:
                continue
            subject = _normalized(finding.subject_id, names)
            identity = ids.short_hash(json.dumps([finding.category, finding.severity.value, subject,
                                                  role_free(_normalized(finding.witness, names))], sort_keys=True,
                                                 default=str), length=12)
            seen[identity] += 1
            suffix = f"#{seen[identity]}" if seen[identity] > 1 else ""
            rows.append({"id": f"safety:{anchor}:{finding.category}:{identity}{suffix}", "junction_id": anchor,
                         "finding": f"{finding.category} ({finding.severity.value}) on {subject}"
                                    + (f"; group {','.join(group)}" if len(group) > 1 else ""),
                         "lanes": _finding_lanes(finding, entities, role_lanes)})
    return rows


def integrity_rows(root: ET.Element, junction_ids: list[str],
                   index: tuple[set[str], dict[str, int]] | None = None) -> list[dict]:
    """INT rows of both sources, in the profile's row shape, each with the road lanes it names."""
    index = road_lane_index(root) if index is None else index
    rows = []
    for source, found in (("request_matrix", request_matrix_findings(root, junction_ids)),
                          ("independent_safety", safety_findings(root, junction_ids))):
        rows += [{"id": row["id"], "class": "INT", "source": source, "finding": row["finding"],
                  "anchor": ids.element_id("junction", row["junction_id"]), "measure": None,
                  "lanes": sorted(set(row.get("lanes", [])) | set(named_lanes((row["id"], row["finding"]), index)))}
                 for row in found]
    return rows
