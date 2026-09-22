from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus
from .ids import stable_id


PLAINXML_NORMALIZATION_POLICY = "torii.plainxml.osm-roundabout-normalization/v1"
_XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
_BUNDLE_SUFFIXES = {
    "nodes": ".nod.xml",
    "edges": ".edg.xml",
    "connections": ".con.xml",
    "tls": ".tll.xml",
    "types": ".typ.xml",
}
_CANONICALIZABLE_ROUNDABOUT_NODE_TYPES = frozenset({"priority", "right_before_left"})

PlainXmlBundleRole = Literal["nodes", "edges", "connections", "tls", "types"]
PlainXmlArtifactRole = Literal["nodes", "edges", "connections", "tls", "types", "configuration"]


class PlainXmlGenerationPolicy(ContractModel):
    seed: int | None
    no_turnarounds: bool
    roundabout_guessing: bool
    plain_output_lanes: bool
    left_hand: bool


class PlainXmlArtifactIdentity(ContractModel):
    role: PlainXmlArtifactRole
    source_path: str
    source_sha256: Sha256
    output_path: str | None = None
    output_sha256: Sha256 | None = None


class PlainXmlRoundaboutRecord(ContractModel):
    edge_ids: tuple[str, ...]
    node_ids: tuple[str, ...]


class PlainXmlRoundaboutNode(ContractModel):
    node_id: str
    observed_type: str | None
    canonical_type: str = "priority"


class PlainXmlRoundaboutCycle(ContractModel):
    edge_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    permission_partition_sha256: Sha256


class PlainXmlRoundaboutComponent(ContractModel):
    component_id: StableToken
    edge_ids: tuple[str, ...]
    node_ids: tuple[str, ...]
    node_types: tuple[PlainXmlRoundaboutNode, ...]
    directed_simple_cycle: bool
    decomposition_method: Literal[
        "single_directed_cycle",
        "permission_partitioned_directed_cycles",
        "unresolved",
    ]
    canonical_cycles: tuple[PlainXmlRoundaboutCycle, ...]
    blockers: tuple[str, ...]


class PlainXmlNodeTypeChange(ContractModel):
    node_id: str
    before: str
    after: str
    evidence: str = "closed_osm_junction_roundabout_component"


class PlainXmlNormalizationReport(ContractModel):
    schema_id: str = "torii.corridor.plainxml-normalization-report/v1"
    status: GateStatus
    policy_id: str = PLAINXML_NORMALIZATION_POLICY
    source_prefix: str
    output_prefix: str
    generation_policy: PlainXmlGenerationPolicy
    artifacts: tuple[PlainXmlArtifactIdentity, ...]
    roundabout_components: tuple[PlainXmlRoundaboutComponent, ...]
    existing_roundabout_records: tuple[PlainXmlRoundaboutRecord, ...]
    canonical_roundabout_records: tuple[PlainXmlRoundaboutRecord, ...]
    node_type_changes: tuple[PlainXmlNodeTypeChange, ...]
    turnaround_connection_signatures: tuple[str, ...]
    output_bundle_sha256: Sha256 | None = None
    rebuild_arguments: tuple[str, ...] = ()
    source_mutated: bool = False
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> PlainXmlNormalizationReport:
        if self.source_mutated and "source_plainxml_bundle_mutated" not in self.blockers:
            raise ValueError("PlainXML source mutation requires an explicit blocker.")
        output_hashes = tuple(
            artifact.output_sha256
            for artifact in self.artifacts
            if artifact.role != "configuration"
        )
        if self.status is GateStatus.PASS:
            if self.blockers:
                raise ValueError("Passing PlainXML normalization cannot hide blockers.")
            if self.output_bundle_sha256 is None or any(value is None for value in output_hashes):
                raise ValueError("Passing PlainXML normalization requires a complete output bundle.")
            if not self.rebuild_arguments:
                raise ValueError("Passing PlainXML normalization requires deterministic rebuild arguments.")
        else:
            if not self.blockers:
                raise ValueError("Blocked PlainXML normalization requires explicit blockers.")
            if self.output_bundle_sha256 is not None or any(value is not None for value in output_hashes):
                raise ValueError("Blocked PlainXML normalization cannot expose a stale output bundle.")
            if self.rebuild_arguments:
                raise ValueError("Blocked PlainXML normalization cannot advertise rebuild arguments.")
        return self


def normalize_osm_plainxml_bundle(
    source_prefix: Path,
    output_prefix: Path,
    report_path: Path,
) -> PlainXmlNormalizationReport:
    """Materialize a deterministic, evidence-bounded PlainXML bundle.

    This narrow protocol deliberately abstains on incomplete or complex
    roundabouts and on generated turnaround connections. It never mutates the
    netconvert-produced source bundle.
    """

    source = source_prefix.resolve()
    output = output_prefix.resolve()
    if source == output:
        raise ValueError("PlainXML source and output prefixes must be distinct.")

    source_paths = _bundle_paths(source)
    source_config = _prefix_path(source, ".netccfg")
    for path in (*source_paths.values(), source_config):
        path.resolve(strict=True)

    output_paths = _bundle_paths(output)
    _remove_stale_outputs(output_paths.values())

    source_hashes_before = {
        role: file_sha256(path.resolve(strict=True)) for role, path in source_paths.items()
    }
    source_config_hash = file_sha256(source_config.resolve(strict=True))
    generation_policy, policy_blockers = _read_generation_policy(source_config)

    edge_tree = ET.parse(source_paths["edges"])
    node_tree = ET.parse(source_paths["nodes"])
    connection_tree = ET.parse(source_paths["connections"])
    edge_root = edge_tree.getroot()
    node_root = node_tree.getroot()
    connection_root = connection_tree.getroot()

    edge_endpoints = _edge_endpoints(edge_root)
    tagged_edges = _osm_roundabout_edges(edge_root, edge_endpoints)
    edge_permission_contracts = _edge_permission_contracts(edge_root)
    node_types = {
        element.get("id", ""): element.get("type")
        for element in node_root
        if element.tag == "node" and element.get("id")
    }
    components = _roundabout_components(
        tagged_edges,
        node_types,
        edge_permission_contracts,
    )
    existing_records = _roundabout_records(edge_root)
    canonical_records = tuple(
        PlainXmlRoundaboutRecord(edge_ids=cycle.edge_ids, node_ids=cycle.node_ids)
        for component in components
        if not component.blockers
        for cycle in component.canonical_cycles
    )
    turnarounds = _turnaround_connection_signatures(connection_root, edge_endpoints)

    blockers = list(policy_blockers)
    blockers.extend(blocker for component in components for blocker in component.blockers)
    tagged_edge_ids = frozenset(tagged_edges)
    if any(set(record.edge_ids) - tagged_edge_ids for record in existing_records):
        blockers.append("roundabout_record_without_osm_tag_evidence")
    blockers = sorted(set(blockers))

    node_type_changes = tuple(
        PlainXmlNodeTypeChange(
            node_id=node.node_id,
            before=str(node.observed_type),
            after=node.canonical_type,
        )
        for component in components
        if not component.blockers
        for node in component.node_types
        if node.observed_type != node.canonical_type
    )

    output_hashes: dict[str, str] = {}
    rebuild_arguments: tuple[str, ...] = ()
    output_bundle_sha256: str | None = None
    if not blockers:
        _canonicalize_roundabout_nodes(node_root, components)
        _canonicalize_roundabout_records(edge_root, canonical_records)
        trees = {
            "nodes": node_tree,
            "edges": edge_tree,
            "connections": connection_tree,
            "tls": ET.parse(source_paths["tls"]),
            "types": ET.parse(source_paths["types"]),
        }
        for role, tree in trees.items():
            write_text_atomic(output_paths[role], _serialize_xml(tree))
            output_hashes[role] = file_sha256(output_paths[role].resolve(strict=True))
        output_bundle_sha256 = _bundle_sha256(output_hashes)
        rebuild_arguments = _rebuild_arguments(output_paths, generation_policy)

    source_hashes_after = {
        role: file_sha256(path.resolve(strict=True)) for role, path in source_paths.items()
    }
    source_mutated = source_hashes_before != source_hashes_after or source_config_hash != file_sha256(
        source_config.resolve(strict=True)
    )

    artifacts = tuple(
        PlainXmlArtifactIdentity(
            role=role,
            source_path=str(source_paths[role]),
            source_sha256=source_hashes_before[role],
            output_path=str(output_paths[role]) if not blockers else None,
            output_sha256=output_hashes.get(role),
        )
        for role in _BUNDLE_SUFFIXES
    ) + (
        PlainXmlArtifactIdentity(
            role="configuration",
            source_path=str(source_config),
            source_sha256=source_config_hash,
        ),
    )

    if source_mutated:
        blockers = sorted(set((*blockers, "source_plainxml_bundle_mutated")))
        _remove_stale_outputs(output_paths.values())
        output_bundle_sha256 = None
        rebuild_arguments = ()
        artifacts = tuple(
            artifact.model_copy(update={"output_path": None, "output_sha256": None})
            for artifact in artifacts
        )

    report = PlainXmlNormalizationReport(
        status=GateStatus.PASS if not blockers else GateStatus.BLOCKED,
        source_prefix=str(source),
        output_prefix=str(output),
        generation_policy=generation_policy,
        artifacts=artifacts,
        roundabout_components=components,
        existing_roundabout_records=existing_records,
        canonical_roundabout_records=canonical_records,
        node_type_changes=node_type_changes,
        turnaround_connection_signatures=turnarounds,
        output_bundle_sha256=output_bundle_sha256,
        rebuild_arguments=rebuild_arguments,
        source_mutated=source_mutated,
        blockers=tuple(blockers),
    )
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return report


def _prefix_path(prefix: Path, suffix: str) -> Path:
    return Path(f"{prefix}{suffix}")


def _bundle_paths(prefix: Path) -> dict[str, Path]:
    return {role: _prefix_path(prefix, suffix) for role, suffix in _BUNDLE_SUFFIXES.items()}


def _remove_stale_outputs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def _parse_bool(value: str | None) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _read_generation_policy(path: Path) -> tuple[PlainXmlGenerationPolicy, tuple[str, ...]]:
    root = ET.parse(path).getroot()

    def option(xpath: str) -> str | None:
        element = root.find(xpath)
        return None if element is None else element.get("value")

    no_turnarounds = _parse_bool(option("./junctions/no-turnarounds"))
    roundabout_guessing = _parse_bool(option("./processing/roundabouts.guess"))
    plain_output_lanes = _parse_bool(option("./output/plain-output.lanes"))
    left_hand = _parse_bool(option("./processing/lefthand"))
    raw_seed = option("./random_number/seed")
    try:
        seed = int(raw_seed) if raw_seed is not None else None
    except ValueError:
        seed = None

    blockers: list[str] = []
    if no_turnarounds is not True:
        blockers.append("upstream_no_turnarounds_not_enabled")
    if roundabout_guessing is not False:
        blockers.append("upstream_roundabout_guessing_not_disabled")
    if plain_output_lanes is not True:
        blockers.append("upstream_plain_output_lanes_not_enabled")
    if seed is None:
        blockers.append("upstream_seed_missing_or_invalid")
    return (
        PlainXmlGenerationPolicy(
            seed=seed,
            no_turnarounds=no_turnarounds is True,
            roundabout_guessing=roundabout_guessing is True,
            plain_output_lanes=plain_output_lanes is True,
            left_hand=left_hand is True,
        ),
        tuple(blockers),
    )


def _edge_endpoints(root: ET.Element) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for element in root:
        if element.tag != "edge":
            continue
        edge_id = element.get("id")
        from_node = element.get("from")
        to_node = element.get("to")
        if edge_id and from_node and to_node:
            result[edge_id] = (from_node, to_node)
    return result


def _osm_roundabout_edges(
    root: ET.Element,
    edge_endpoints: dict[str, tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for element in root:
        edge_id = element.get("id")
        if element.tag != "edge" or edge_id not in edge_endpoints:
            continue
        if any(
            child.tag == "param"
            and child.get("key") == "junction"
            and str(child.get("value", "")).strip().lower() == "roundabout"
            for child in element
        ):
            result[edge_id] = edge_endpoints[edge_id]
    return result


def _roundabout_components(
    tagged_edges: dict[str, tuple[str, str]],
    node_types: dict[str, str | None],
    edge_permission_contracts: dict[str, str],
) -> tuple[PlainXmlRoundaboutComponent, ...]:
    node_edges: dict[str, set[str]] = defaultdict(set)
    for edge_id, (from_node, to_node) in tagged_edges.items():
        node_edges[from_node].add(edge_id)
        node_edges[to_node].add(edge_id)

    components: list[PlainXmlRoundaboutComponent] = []
    unseen = set(tagged_edges)
    while unseen:
        seed = min(unseen)
        stack = [seed]
        unseen.remove(seed)
        edge_ids: list[str] = []
        while stack:
            edge_id = stack.pop()
            edge_ids.append(edge_id)
            for node_id in tagged_edges[edge_id]:
                for neighbor in sorted(node_edges[node_id]):
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        stack.append(neighbor)

        edges = tuple(sorted(edge_ids))
        nodes = tuple(sorted({node for edge_id in edges for node in tagged_edges[edge_id]}))
        directed_simple_cycle = _is_directed_simple_cycle(edges, tagged_edges)
        blockers: list[str] = []
        decomposition_method: Literal[
            "single_directed_cycle",
            "permission_partitioned_directed_cycles",
            "unresolved",
        ]
        canonical_cycles: tuple[PlainXmlRoundaboutCycle, ...]
        if directed_simple_cycle:
            decomposition_method = "single_directed_cycle"
            canonical_cycles = (
                _roundabout_cycle(edges, tagged_edges, edge_permission_contracts),
            )
        else:
            permission_groups: dict[str, list[str]] = defaultdict(list)
            for edge_id in edges:
                permission_groups[edge_permission_contracts[edge_id]].append(edge_id)
            partition_cycles = tuple(
                _roundabout_cycle(
                    tuple(sorted(group_edges)),
                    tagged_edges,
                    edge_permission_contracts,
                )
                for _, group_edges in sorted(permission_groups.items())
                if _is_directed_simple_cycle(tuple(sorted(group_edges)), tagged_edges)
            )
            permission_partition_is_complete = (
                len(permission_groups) > 1
                and len(partition_cycles) == len(permission_groups)
                and {edge for cycle in partition_cycles for edge in cycle.edge_ids} == set(edges)
            )
            if permission_partition_is_complete:
                decomposition_method = "permission_partitioned_directed_cycles"
                canonical_cycles = tuple(
                    sorted(partition_cycles, key=lambda item: item.edge_ids)
                )
            else:
                decomposition_method = "unresolved"
                canonical_cycles = ()
                blockers.append("roundabout_component_not_directed_simple_cycle")
        observed_nodes = tuple(
            PlainXmlRoundaboutNode(node_id=node_id, observed_type=node_types.get(node_id))
            for node_id in nodes
        )
        if any(node.observed_type is None for node in observed_nodes):
            blockers.append("roundabout_component_node_missing")
        if any(
            node.observed_type is not None
            and node.observed_type not in _CANONICALIZABLE_ROUNDABOUT_NODE_TYPES
            for node in observed_nodes
        ):
            blockers.append("roundabout_component_protected_node_type")
        components.append(
            PlainXmlRoundaboutComponent(
                component_id=stable_id(
                    "cell",
                    {"plainxml_roundabout_edge_ids": edges},
                ),
                edge_ids=edges,
                node_ids=nodes,
                node_types=observed_nodes,
                directed_simple_cycle=directed_simple_cycle,
                decomposition_method=decomposition_method,
                canonical_cycles=canonical_cycles,
                blockers=tuple(sorted(set(blockers))),
            )
        )
    return tuple(sorted(components, key=lambda item: item.component_id))


def _is_directed_simple_cycle(
    edge_ids: tuple[str, ...],
    edge_endpoints: dict[str, tuple[str, str]],
) -> bool:
    nodes = {node for edge_id in edge_ids for node in edge_endpoints[edge_id]}
    indegree: Counter[str] = Counter()
    outdegree: Counter[str] = Counter()
    for edge_id in edge_ids:
        from_node, to_node = edge_endpoints[edge_id]
        outdegree[from_node] += 1
        indegree[to_node] += 1
    return (
        len(edge_ids) >= 2
        and len(edge_ids) == len(nodes)
        and all(indegree[node] == 1 and outdegree[node] == 1 for node in nodes)
    )


def _roundabout_cycle(
    edge_ids: tuple[str, ...],
    edge_endpoints: dict[str, tuple[str, str]],
    edge_permission_contracts: dict[str, str],
) -> PlainXmlRoundaboutCycle:
    nodes = tuple(sorted({node for edge_id in edge_ids for node in edge_endpoints[edge_id]}))
    payload = json.dumps(
        {
            edge_id: edge_permission_contracts[edge_id]
            for edge_id in edge_ids
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return PlainXmlRoundaboutCycle(
        edge_ids=edge_ids,
        node_ids=nodes,
        permission_partition_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _edge_permission_contracts(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for element in root:
        if element.tag != "edge" or not element.get("id"):
            continue
        payload = {
            "edge_allow": element.get("allow"),
            "edge_disallow": element.get("disallow"),
            "lanes": [
                {
                    "index": child.get("index"),
                    "allow": child.get("allow"),
                    "disallow": child.get("disallow"),
                }
                for child in element
                if child.tag == "lane"
            ],
        }
        result[element.get("id", "")] = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        )
    return result


def _roundabout_records(root: ET.Element) -> tuple[PlainXmlRoundaboutRecord, ...]:
    records = (
        PlainXmlRoundaboutRecord(
            edge_ids=tuple(sorted(filter(None, str(element.get("edges", "")).split()))),
            node_ids=tuple(sorted(filter(None, str(element.get("nodes", "")).split()))),
        )
        for element in root
        if element.tag == "roundabout"
    )
    return tuple(sorted(records, key=lambda item: (item.edge_ids, item.node_ids)))


def _turnaround_connection_signatures(
    root: ET.Element,
    edge_endpoints: dict[str, tuple[str, str]],
) -> tuple[str, ...]:
    result: set[str] = set()
    for element in root:
        if element.tag != "connection":
            continue
        from_edge = element.get("from")
        to_edge = element.get("to")
        if from_edge not in edge_endpoints or to_edge not in edge_endpoints:
            continue
        source = edge_endpoints[from_edge]
        target = edge_endpoints[to_edge]
        if source[0] == target[1] and source[1] == target[0]:
            result.add(
                "|".join(f"{key}={value}" for key, value in sorted(element.attrib.items()))
            )
    return tuple(sorted(result))


def _canonicalize_roundabout_nodes(
    root: ET.Element,
    components: tuple[PlainXmlRoundaboutComponent, ...],
) -> None:
    node_ids = {node_id for component in components for node_id in component.node_ids}
    for element in root:
        if element.tag == "node" and element.get("id") in node_ids:
            element.set("type", "priority")


def _canonicalize_roundabout_records(
    root: ET.Element,
    records: tuple[PlainXmlRoundaboutRecord, ...],
) -> None:
    for element in tuple(root):
        if element.tag == "roundabout":
            root.remove(element)
    for record in records:
        ET.SubElement(
            root,
            "roundabout",
            {
                "nodes": " ".join(record.node_ids),
                "edges": " ".join(record.edge_ids),
            },
        )


def _serialize_xml(tree: ET.ElementTree) -> str:
    ET.register_namespace("xsi", _XSI_NAMESPACE)
    ET.indent(tree, space="    ")
    body = ET.tostring(tree.getroot(), encoding="unicode", short_empty_elements=True)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n\n{body}\n'


def _bundle_sha256(output_hashes: dict[str, str]) -> str:
    payload = json.dumps(output_hashes, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rebuild_arguments(
    output_paths: dict[str, Path],
    policy: PlainXmlGenerationPolicy,
) -> tuple[str, ...]:
    result = [
        "--node-files",
        str(output_paths["nodes"]),
        "--edge-files",
        str(output_paths["edges"]),
        "--connection-files",
        str(output_paths["connections"]),
        "--tllogic-files",
        str(output_paths["tls"]),
        "--type-files",
        str(output_paths["types"]),
        "--output-file",
        "<OUTPUT_NET>",
        "--seed",
        str(policy.seed),
        "--no-turnarounds",
        "--roundabouts.guess",
        "false",
    ]
    if policy.left_hand:
        result.append("--lefthand")
    return tuple(result)
