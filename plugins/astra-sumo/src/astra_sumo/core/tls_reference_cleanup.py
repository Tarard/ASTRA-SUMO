from __future__ import annotations

import hashlib
import math
import re
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .artifact_io import atomic_writer, write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .xml_stream import DirectChildren, binary_blocks, read_blocks


_IMPLICIT_TLS_JUNCTION_TYPES = frozenset({"rail_crossing", "rail_signal"})
_SAFE_STALE_JUNCTION_TYPES = frozenset({"priority"})
_PEDESTRIAN_INTERNAL_EDGE_FUNCTIONS = frozenset({"crossing", "walkingarea"})
_UNCONTROLLED_CONNECTION_STATES = frozenset({"M", "m"})
_CONNECTION_TAG = re.compile(r"<connection(?=\s)[^<>]*/>", re.DOTALL)
_TEXT_BLOCK_SIZE = 1 << 20


def build_tls_reference_cleanup_variant(
    net_file: Path,
    *,
    output_dir: Path,
    prefix: str = "tls_reference_cleanup",
) -> dict[str, Any]:
    """Create a minimal, reversible candidate for provably stale TLS attributes.

    SUMO rail signals and rail crossings are implicit controllers and therefore do
    not require an embedded ``tlLogic`` element. They are preserved. The only
    automatic repair currently allowed is a stale TLS reference on the second
    half of an internal pedestrian crossing at a priority junction, where the
    stored connection state is already uncontrolled (``M`` or ``m``).
    """

    source = Path(net_file).resolve()
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    plan_file = destination / f"{prefix}.plan.json"
    report_file = destination / f"{prefix}.json"
    manifest_file = destination / f"{prefix}.manifest.json"
    overlay_file = destination / f"{prefix}.review.add.xml"
    variant_file = destination / f"{prefix}.net.xml"

    if not source.is_file():
        return _persist_early_failure(
            error=f"SUMO network does not exist: {source}",
            source=source,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
        )
    if source.suffix.casefold() == ".gz":
        return _persist_early_failure(
            error="minimal TLS reference cleanup currently requires an uncompressed .net.xml input",
            source=source,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
        )
    if variant_file.resolve() == source:
        return _persist_early_failure(
            error="candidate output must be distinct from the source network",
            source=source,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
        )
    try:
        variant_file.unlink(missing_ok=True)
        overlay_file.unlink(missing_ok=True)
    except OSError as exc:
        return _persist_early_failure(
            error=f"could not remove stale candidate artifacts: {type(exc).__name__}: {exc}",
            source=source,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
        )

    source_sha256 = file_sha256(source)
    try:
        audit = _audit_tls_references(lambda: _text_blocks(source))
    except (OSError, UnicodeError, ET.ParseError) as exc:
        return _persist_early_failure(
            error=f"{type(exc).__name__}: {exc}",
            source=source,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
            source_sha256=source_sha256,
        )

    operations = audit["repairable_references"]
    blocked_references = audit["blocked_references"]
    plan: dict[str, Any] = {
        "schema": "torii.tls_reference_cleanup_plan.v1",
        "status": "blocked" if blocked_references else "ready",
        "claim_status": "construction-invalid" if blocked_references else "diagnostic-demo",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "candidate_net_file": "",
        "candidate_sha256": "",
        "implicit_controller_policy": {
            "junction_types": sorted(_IMPLICIT_TLS_JUNCTION_TYPES),
            "reason": (
                "SUMO creates rail-signal and rail-crossing control dynamically; "
                "absence of an embedded tlLogic is not a dangling reference"
            ),
        },
        "repair_policy": {
            "junction_types": sorted(_SAFE_STALE_JUNCTION_TYPES),
            "edge_functions": sorted(_PEDESTRIAN_INTERNAL_EDGE_FUNCTIONS),
            "connection_states": sorted(_UNCONTROLLED_CONNECTION_STATES),
            "removed_attributes": ["tl", "linkIndex", "linkIndex2"],
            "partial_repair_forbidden": True,
        },
        "reference_counts": audit["counts"],
        "operations": operations,
        "blocked_references": blocked_references,
        "rollback": {
            "strategy": "discard candidate and retain source, or restore each operation.before_attributes",
            "source_network_immutable": True,
        },
        "warnings": [],
    }

    if blocked_references:
        plan["warnings"] = [
            "At least one malformed TLS reference is outside the narrow pedestrian-internal repair policy; "
            "no candidate was written"
        ]
        write_json_atomic(plan_file, plan, sort_keys=True)
        report = _base_report(
            status="blocked",
            cleanup_status="blocked_unsafe_reference",
            source=source,
            source_sha256=source_sha256,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
            audit=audit,
        )
        report["warnings"] = list(plan["warnings"])
        write_json_atomic(report_file, report, sort_keys=True)
        _write_manifest(
            manifest_file,
            status="blocked",
            source=source,
            generated_files=[plan_file, report_file],
        )
        return report

    if not operations:
        plan.update(
            {
                "status": "pass",
                "claim_status": "identity-safe",
                "candidate_net_file": str(source),
                "candidate_sha256": source_sha256,
                "warnings": ["No stale TLS reference matched the repair policy; source remains effective"],
            }
        )
        write_json_atomic(plan_file, plan, sort_keys=True)
        report = _base_report(
            status="pass",
            cleanup_status="no_change",
            source=source,
            source_sha256=source_sha256,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
            audit=audit,
        )
        report.update(
            {
                "claim_status": "identity-safe",
                "effective_net_file": str(source),
                "candidate_net_file": str(source),
                "candidate_sha256": source_sha256,
                "source_preservation_status": "pass",
                "semantic_preservation_status": "pass",
                "minimal_text_patch_status": "not_needed",
                "review_overlay_file": "",
                "warnings": list(plan["warnings"]),
            }
        )
        write_json_atomic(report_file, report, sort_keys=True)
        _write_manifest(
            manifest_file,
            status="pass",
            source=source,
            generated_files=[plan_file, report_file],
        )
        return report

    patch_evidence = _patch_connection_tags(
        source,
        variant_file,
        audit["counts"]["connection_count"],
        operations,
    )
    if patch_evidence["status"] != "pass":
        plan.update(
            {
                "status": "blocked",
                "claim_status": "construction-invalid",
                "warnings": [str(patch_evidence["error"])],
            }
        )
        write_json_atomic(plan_file, plan, sort_keys=True)
        report = _base_report(
            status="blocked",
            cleanup_status="blocked_minimal_patch_unavailable",
            source=source,
            source_sha256=source_sha256,
            plan_file=plan_file,
            report_file=report_file,
            manifest_file=manifest_file,
            audit=audit,
        )
        report.update({"minimal_patch": patch_evidence, "warnings": list(plan["warnings"])})
        write_json_atomic(report_file, report, sort_keys=True)
        _write_manifest(
            manifest_file,
            status="blocked",
            source=source,
            generated_files=[plan_file, report_file],
        )
        return report

    candidate_sha256 = file_sha256(variant_file)
    validation = _validate_candidate(
        source=source,
        source_connection_count=audit["counts"]["connection_count"],
        candidate_file=variant_file,
        operations=operations,
    )
    source_after_sha256 = file_sha256(source)
    source_preserved = source_after_sha256 == source_sha256
    candidate_distinct = candidate_sha256 != source_sha256
    valid = validation["status"] == "pass" and source_preserved and candidate_distinct

    plan.update(
        {
            "status": "pass" if valid else "blocked",
            "claim_status": "diagnostic-demo" if valid else "construction-invalid",
            "candidate_net_file": str(variant_file),
            "candidate_sha256": candidate_sha256,
            "source_after_sha256": source_after_sha256,
            "validation": validation,
            "minimal_patch": patch_evidence,
            "warnings": (
                ["Candidate requires SUMO load and routeability validation before promotion"]
                if valid
                else ["Candidate failed identity or semantic-preservation validation and must not be promoted"]
            ),
        }
    )
    write_json_atomic(plan_file, plan, sort_keys=True)

    overlay_path = ""
    if valid:
        _write_review_overlay(
            overlay_file,
            operations=operations,
            source_sha256=source_sha256,
            candidate_sha256=candidate_sha256,
        )
        overlay_path = str(overlay_file)

    report = _base_report(
        status="pass" if valid else "blocked",
        cleanup_status="variant_created" if valid else "variant_failed_validation",
        source=source,
        source_sha256=source_sha256,
        plan_file=plan_file,
        report_file=report_file,
        manifest_file=manifest_file,
        audit=audit,
    )
    report.update(
        {
            "claim_status": "diagnostic-demo" if valid else "construction-invalid",
            "effective_net_file": str(variant_file) if valid else "",
            "candidate_net_file": str(variant_file),
            "candidate_sha256": candidate_sha256,
            "source_after_sha256": source_after_sha256,
            "source_preservation_status": "pass" if source_preserved else "fail",
            "candidate_identity_status": "distinct" if candidate_distinct else "identity-copy",
            "semantic_preservation_status": validation["status"],
            "minimal_text_patch_status": patch_evidence["status"],
            "minimal_patch": patch_evidence,
            "candidate_validation": validation,
            "review_overlay_file": overlay_path,
            "warnings": list(plan["warnings"]),
        }
    )
    write_json_atomic(report_file, report, sort_keys=True)
    generated_files = [plan_file, report_file, variant_file]
    if overlay_file.is_file():
        generated_files.append(overlay_file)
    _write_manifest(
        manifest_file,
        status=str(report["status"]),
        source=source,
        generated_files=generated_files,
    )
    return report


def _text_blocks(path: Path) -> Iterator[str]:
    """The network text as Path.read_text(encoding="utf-8") decodes it, one block at a time."""
    with path.open(encoding="utf-8") as handle:
        yield from read_blocks(handle, _TEXT_BLOCK_SIZE)


def _audit_tls_references(network_blocks: Callable[[], Iterable[str | bytes]]) -> dict[str, Any]:
    """Classify every TLS reference of a network read one top-level element at a time.

    Junctions and controller ids are kept for the whole network, connections only when they carry a TLS
    attribute. A second pass reads the edges that a stale reference names, and only when there is one.
    """
    junctions: dict[str, tuple[str, ...]] = {}
    explicit_controller_ids: set[str] = set()
    references: list[tuple[int, dict[str, str]]] = []
    connection_count = 0
    for element in DirectChildren(network_blocks()):
        if element.tag == "connection":
            attributes = element.attrib
            if attributes.get("tl") or attributes.get("linkIndex") or attributes.get("linkIndex2"):
                references.append((connection_count, dict(attributes)))
            connection_count += 1
        elif element.tag == "junction" and element.attrib.get("id"):
            junctions[element.attrib["id"]] = tuple(element.attrib.get(key, "") for key in ("type", "x", "y"))
        elif element.tag == "tlLogic" and element.attrib.get("id"):
            explicit_controller_ids.add(element.attrib["id"])
    stale_edge_ids = {
        attributes.get(key, "")
        for _, attributes in references
        if attributes.get("tl")
        and attributes["tl"] not in explicit_controller_ids
        and junctions.get(attributes["tl"], ("",))[0] not in _IMPLICIT_TLS_JUNCTION_TYPES
        for key in ("from", "to")
    }
    edges: dict[str, dict[str, Any]] = {}
    if stale_edge_ids:
        for element in DirectChildren(network_blocks()):
            if element.tag == "edge" and element.attrib.get("id") and element.attrib["id"] in stale_edge_ids:
                edges[element.attrib["id"]] = _edge_row(element)

    valid_explicit = 0
    valid_implicit = 0
    repairable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    referenced_controller_ids: set[str] = set()
    implicit_controller_ids: set[str] = set()

    for connection_index, attributes in references:
        tl_id = attributes.get("tl", "")
        link_index = attributes.get("linkIndex", "")
        if not tl_id:
            if link_index or attributes.get("linkIndex2"):
                blocked.append(
                    {
                        "connection_index": connection_index,
                        "classification": "blocked_link_index_without_tls",
                        "before_attributes": dict(attributes),
                        "blockers": ["connection_has_link_index_without_tls_id"],
                    }
                )
            continue
        referenced_controller_ids.add(tl_id)
        if tl_id in explicit_controller_ids:
            valid_explicit += 1
            continue
        junction = junctions.get(tl_id)
        junction_type = junction[0] if junction is not None else ""
        if junction_type in _IMPLICIT_TLS_JUNCTION_TYPES:
            valid_implicit += 1
            implicit_controller_ids.add(tl_id)
            continue

        record, blockers = _classify_stale_reference(
            connection_index=connection_index,
            attributes=attributes,
            junction=junction,
            edges=edges,
        )
        if blockers:
            record.update(
                {
                    "classification": "blocked_unsafe_tls_reference",
                    "blockers": blockers,
                }
            )
            blocked.append(record)
        else:
            record.update(
                {
                    "operation_id": f"tls-reference-cleanup-{len(repairable) + 1:04d}",
                    "classification": "repairable_stale_pedestrian_internal_reference",
                    "removed_attributes": {
                        key: attributes[key]
                        for key in ("tl", "linkIndex", "linkIndex2")
                        if key in attributes
                    },
                    "after_attributes": {
                        key: value
                        for key, value in attributes.items()
                        if key not in {"tl", "linkIndex", "linkIndex2"}
                    },
                    "reason": (
                        "priority junction and pedestrian-only internal connection are already encoded "
                        "with an uncontrolled major/minor state; stale TLS identity prevents SUMO load"
                    ),
                    "rollback": {
                        "action": "restore_removed_attributes",
                        "attributes": {
                            key: attributes[key]
                            for key in ("tl", "linkIndex", "linkIndex2")
                            if key in attributes
                        },
                    },
                }
            )
            repairable.append(record)

    return {
        "counts": {
            "connection_count": connection_count,
            "explicit_tllogic_count": len(explicit_controller_ids),
            "referenced_tls_id_count": len(referenced_controller_ids),
            "explicit_controlled_connection_count": valid_explicit,
            "implicit_controlled_connection_count": valid_implicit,
            "implicit_controller_id_count": len(implicit_controller_ids),
            "repairable_reference_count": len(repairable),
            "blocked_reference_count": len(blocked),
        },
        "implicit_controller_ids": sorted(implicit_controller_ids),
        "repairable_references": repairable,
        "blocked_references": blocked,
    }


def _classify_stale_reference(
    *,
    connection_index: int,
    attributes: dict[str, str],
    junction: tuple[str, ...] | None,
    edges: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    tl_id = attributes.get("tl", "")
    from_id = attributes.get("from", "")
    to_id = attributes.get("to", "")
    from_edge = edges.get(from_id)
    to_edge = edges.get(to_id)
    junction_type, junction_x, junction_y = junction if junction is not None else ("", "", "")
    blockers: list[str] = []

    if junction is None:
        blockers.append("tls_id_has_no_matching_junction")
    elif junction_type not in _SAFE_STALE_JUNCTION_TYPES:
        blockers.append(f"junction_type_not_safe:{junction_type or 'missing'}")
    if junction is not None and not all(_is_finite_number(value) for value in (junction_x, junction_y)):
        blockers.append("junction_review_coordinates_missing_or_invalid")
    if not attributes.get("linkIndex"):
        blockers.append("tls_reference_has_no_link_index")
    if attributes.get("via"):
        blockers.append("connection_is_not_second_stage_internal")
    expected_prefix = f":{tl_id}_"
    if not from_id.startswith(expected_prefix) or not to_id.startswith(expected_prefix):
        blockers.append("connection_endpoints_are_not_internal_to_tls_junction")
    for label, edge in (("from", from_edge), ("to", to_edge)):
        if edge is None:
            blockers.append(f"{label}_edge_missing")
            continue
        function = edge["function"]
        if function not in _PEDESTRIAN_INTERNAL_EDGE_FUNCTIONS:
            blockers.append(f"{label}_edge_not_crossing_or_walkingarea:{function or 'missing'}")
        if not _edge_is_pedestrian_only(edge):
            blockers.append(f"{label}_edge_not_explicitly_pedestrian_only")
    state = attributes.get("state", "")
    if state not in _UNCONTROLLED_CONNECTION_STATES:
        blockers.append(f"connection_state_not_uncontrolled:{state or 'missing'}")

    record = {
        "connection_index": connection_index,
        "junction_id": tl_id,
        "junction_type": junction_type,
        "junction_position": {"x": junction_x, "y": junction_y},
        "connection_locator": {
            key: attributes.get(key, "")
            for key in ("from", "to", "fromLane", "toLane", "via", "dir")
        },
        "before_attributes": dict(attributes),
        "evidence": {
            "embedded_tllogic_absent": True,
            "junction_type": junction_type,
            "from_edge": _edge_evidence(from_edge),
            "to_edge": _edge_evidence(to_edge),
            "connection_state": state,
        },
    }
    return record, blockers


def _edge_row(edge: ET.Element) -> dict[str, Any]:
    return {
        "id": edge.attrib.get("id", ""),
        "function": edge.attrib.get("function", ""),
        "lanes": [
            {
                "id": lane.attrib.get("id", ""),
                "allow": lane.attrib.get("allow", ""),
                "disallow": lane.attrib.get("disallow", ""),
            }
            for lane in edge.findall("lane")
        ],
    }


def _edge_is_pedestrian_only(edge: dict[str, Any]) -> bool:
    lanes = edge["lanes"]
    if not lanes:
        return False
    for lane in lanes:
        allowed = set(lane["allow"].split())
        if not allowed or not allowed.issubset({"pedestrian"}):
            return False
    return True


def _is_finite_number(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _edge_evidence(edge: dict[str, Any] | None) -> dict[str, Any]:
    if edge is None:
        return {"present": False}
    return {
        "present": True,
        "id": edge["id"],
        "function": edge["function"],
        "lanes": [dict(lane) for lane in edge["lanes"]],
    }


class _PatchUnavailable(Exception):
    """The candidate cannot be written; ``evidence`` is the blocked patch record."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.evidence = {"status": "blocked", "error": error}


def _patch_connection_tags(
    source: Path,
    candidate_file: Path,
    connection_count: int,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write the source text with the planned attributes removed from their connection tags.

    The text is decoded as Path.read_text decodes it and read in blocks cut before a '<'. A connection tag holds
    no '<', so each tag lies whole in one block and the blocks give the matches of the whole text. The candidate
    is kept only when every tag binds to a parsed connection.
    """
    targets = {int(operation["connection_index"]): operation for operation in operations}
    tag_count = 0
    changed_tags = 0
    removed_attribute_count = 0
    first_error = ""
    try:
        with atomic_writer(candidate_file, mode="w", encoding="utf-8", newline="") as handle:
            pending = ""
            for block in chain(_text_blocks(source), [""]):
                text = pending + block
                cut = text.rfind("<") if block else len(text)
                if cut <= 0 and block:
                    pending = text
                    continue
                piece, pending = text[:cut], text[cut:]
                cursor = 0
                for match in _CONNECTION_TAG.finditer(piece):
                    connection_index = tag_count
                    tag_count += 1
                    operation = targets.get(connection_index)
                    if operation is None or first_error:
                        continue
                    patched_tag, error, removed = _patched_connection_tag(connection_index, match.group(0), operation)
                    if error:
                        first_error = error
                        continue
                    handle.write(piece[cursor : match.start()])
                    handle.write(patched_tag)
                    cursor = match.end()
                    changed_tags += 1
                    removed_attribute_count += removed
                handle.write(piece[cursor:])
            if tag_count != connection_count:
                raise _PatchUnavailable(
                    "minimal text patch could not bind parsed connections to self-closing XML tags: "
                    f"parsed={connection_count}, tags={tag_count}"
                )
            if first_error:
                raise _PatchUnavailable(first_error)
    except _PatchUnavailable as exc:
        return exc.evidence
    return {
        "status": "pass",
        "source_connection_tag_count": tag_count,
        "changed_connection_tag_count": changed_tags,
        "removed_attribute_count": removed_attribute_count,
        "all_other_source_text_preserved": True,
    }


def _patched_connection_tag(
    connection_index: int,
    tag_text: str,
    operation: dict[str, Any],
) -> tuple[str, str, int]:
    """The tag without the removed attributes, an error text, and the number of removed attributes."""
    try:
        parsed_tag = ET.fromstring(tag_text)
    except ET.ParseError as exc:
        return "", f"connection tag {connection_index} could not be parsed: {exc}", 0
    if dict(parsed_tag.attrib) != operation["before_attributes"]:
        return "", f"connection tag {connection_index} did not match the parsed source element", 0
    patched_tag = tag_text
    for attribute in operation["removed_attributes"]:
        attribute_pattern = re.compile(
            rf"\s+{re.escape(attribute)}\s*=\s*(?:\"[^\"]*\"|'[^']*')"
        )
        patched_tag, count = attribute_pattern.subn("", patched_tag, count=1)
        if count != 1:
            return "", (
                f"attribute {attribute} on connection tag {connection_index} "
                "could not be removed exactly once"
            ), 0
    return patched_tag, "", len(operation["removed_attributes"])


def _validate_candidate(
    *,
    source: Path,
    source_connection_count: int,
    candidate_file: Path,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        post_audit = _audit_tls_references(lambda: binary_blocks(candidate_file))
    except (OSError, ET.ParseError) as exc:
        return {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}

    candidate_connection_count = post_audit["counts"]["connection_count"]
    targets = {int(operation["connection_index"]): operation for operation in operations}
    failures: list[str] = []
    source_digest, candidate_digest = hashlib.sha256(), hashlib.sha256()
    source_connections = _connections_and_digest(DirectChildren(_text_blocks(source)), source_digest)
    candidate_connections = _connections_and_digest(DirectChildren(binary_blocks(candidate_file)), candidate_digest)
    if source_connection_count != candidate_connection_count:
        failures.append("connection_count_changed")
    else:
        for connection_index, (source_attributes, candidate_attributes) in enumerate(
            zip(source_connections, candidate_connections)
        ):
            operation = targets.get(connection_index)
            expected = (
                operation["after_attributes"]
                if operation is not None
                else dict(source_attributes)
            )
            if dict(candidate_attributes) != expected:
                failures.append(f"connection_attributes_changed_unexpectedly:{connection_index}")
    # Read both networks to the end, so each digest covers every element that is not a connection.
    deque(source_connections, maxlen=0)
    deque(candidate_connections, maxlen=0)

    source_invariant_sha256 = source_digest.hexdigest()
    candidate_invariant_sha256 = candidate_digest.hexdigest()
    if source_invariant_sha256 != candidate_invariant_sha256:
        failures.append("non_connection_network_elements_changed")
    if post_audit["counts"]["repairable_reference_count"]:
        failures.append("repairable_tls_references_remain")
    if post_audit["counts"]["blocked_reference_count"]:
        failures.append("unsafe_tls_references_remain")
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "connection_count_preservation_status": (
            "pass" if source_connection_count == candidate_connection_count else "fail"
        ),
        "non_connection_network_elements_preservation_status": (
            "pass" if source_invariant_sha256 == candidate_invariant_sha256 else "fail"
        ),
        "source_non_connection_elements_sha256": source_invariant_sha256,
        "candidate_non_connection_elements_sha256": candidate_invariant_sha256,
        "post_cleanup_reference_counts": post_audit["counts"],
        "post_cleanup_implicit_controller_ids": post_audit["implicit_controller_ids"],
    }


def _connections_and_digest(elements: Iterable[ET.Element], digest: Any) -> Iterator[dict[str, str]]:
    """Yield the attributes of each connection; hash every other element with its tail, then a NUL byte."""
    for element in elements:
        if element.tag == "connection":
            yield element.attrib
        else:
            digest.update(ET.tostring(element, encoding="utf-8"))
            digest.update(b"\0")


def _write_review_overlay(
    path: Path,
    *,
    operations: list[dict[str, Any]],
    source_sha256: str,
    candidate_sha256: str,
) -> None:
    root = ET.Element("additional")
    for operation in operations:
        position = operation["junction_position"]
        poi = ET.SubElement(
            root,
            "poi",
            {
                "id": f"torii_{operation['operation_id']}",
                "type": "torii.review.tls_reference_cleanup",
                "color": "255,165,0",
                "layer": "1000",
                "x": str(position["x"]),
                "y": str(position["y"]),
                "name": f"Review stale TLS reference at {operation['junction_id']}",
            },
        )
        for key, value in (
            ("operation_id", operation["operation_id"]),
            ("junction_id", operation["junction_id"]),
            ("reason", operation["reason"]),
            ("source_sha256", source_sha256),
            ("candidate_sha256", candidate_sha256),
            ("rollback", "discard candidate or restore removed TLS attributes from plan"),
        ):
            ET.SubElement(poi, "param", {"key": str(key), "value": str(value)})
    ET.indent(root, space="    ")
    document = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    write_text_atomic(path, document)


def _base_report(
    *,
    status: str,
    cleanup_status: str,
    source: Path,
    source_sha256: str,
    plan_file: Path,
    report_file: Path,
    manifest_file: Path,
    audit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "torii.tls_reference_cleanup_report.v1",
        "status": status,
        "claim_status": "construction-invalid" if status != "pass" else "diagnostic-demo",
        "tls_reference_cleanup_status": cleanup_status,
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "source_network_mutation": False,
        "effective_net_file": "",
        "candidate_net_file": "",
        "candidate_sha256": "",
        "reference_counts": audit["counts"],
        "implicit_controller_ids": audit["implicit_controller_ids"],
        "repairable_references": audit["repairable_references"],
        "blocked_references": audit["blocked_references"],
        "plan_file": str(plan_file),
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
    }


def _persist_early_failure(
    *,
    error: str,
    source: Path,
    plan_file: Path,
    report_file: Path,
    manifest_file: Path,
    source_sha256: str = "",
) -> dict[str, Any]:
    plan = {
        "schema": "torii.tls_reference_cleanup_plan.v1",
        "status": "blocked",
        "claim_status": "construction-invalid",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "operations": [],
        "blocked_references": [],
        "error": error,
    }
    write_json_atomic(plan_file, plan, sort_keys=True)
    report = {
        "schema": "torii.tls_reference_cleanup_report.v1",
        "status": "blocked",
        "claim_status": "construction-invalid",
        "tls_reference_cleanup_status": "blocked_input",
        "source_net_file": str(source),
        "source_sha256": source_sha256,
        "source_network_mutation": False,
        "effective_net_file": "",
        "candidate_net_file": "",
        "plan_file": str(plan_file),
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
        "error": error,
    }
    write_json_atomic(report_file, report, sort_keys=True)
    generated = [plan_file, report_file]
    _write_manifest(
        manifest_file,
        status="blocked",
        source=source if source.is_file() else None,
        generated_files=generated,
    )
    return report


def _write_manifest(
    path: Path,
    *,
    status: str,
    source: Path | None,
    generated_files: list[Path],
) -> None:
    artifacts: list[dict[str, Any]] = []
    if source is not None and source.is_file():
        artifacts.append(_artifact_record(source, role="source_input"))
    for artifact in generated_files:
        if artifact.is_file():
            artifacts.append(_artifact_record(artifact, role="generated"))
    write_json_atomic(
        path,
        {
            "schema": "torii.tls_reference_cleanup_manifest.v1",
            "status": status,
            "source_overwrite_forbidden": True,
            "artifacts": artifacts,
        },
        sort_keys=True,
    )


def _artifact_record(path: Path, *, role: str) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "role": role,
        "size_bytes": resolved.stat().st_size,
        "sha256": file_sha256(resolved),
    }
