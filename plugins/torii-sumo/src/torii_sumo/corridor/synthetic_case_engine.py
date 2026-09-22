from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.connection_mode_audit import audit_network_connection_mode

from .audit_adapter import (
    build_scope_from_junction_ids,
    canonicalize_connection_mode_findings,
)
from .canonicalizer import canonicalize_net_xml_file
from .conflict_graph import audit_independent_movement_safety
from .enums import FindingSeverity, GateStatus, TrafficSide
from .exact_diff import compare_canonical_snapshots
from .synthetic_benchmark_contracts import GoldObservation, ObservationMatch
from .synthetic_networks import apply_synthetic_mutation, build_synthetic_fixture


@dataclass(frozen=True)
class CleanSyntheticFixture:
    path: Path
    sha256: str
    audit: dict[str, Any]
    snapshot: Any
    safety: Any
    status: GateStatus
    artifact_paths: frozenset[Path]


@dataclass(frozen=True)
class SyntheticMutationEvidence:
    source_path: Path
    source_sha256: str
    mutant_path: Path
    mutant_sha256: str
    source_immutable: bool
    candidate_audit: dict[str, Any]
    candidate_snapshot: Any
    candidate_safety: Any
    exact_diff: Any
    observed: dict[str, dict[str, int]]
    exact_delta_count: int
    abstention_proven: bool
    artifact_paths: frozenset[Path]


def materialize_clean_synthetic_fixture(
    *,
    fixture_id: str,
    traffic_side: TrafficSide,
    fixture_dir: Path,
) -> CleanSyntheticFixture:
    root = build_synthetic_fixture(fixture_id, traffic_side=traffic_side)
    path = fixture_dir / f"{fixture_id}.{traffic_side.value}.clean.net.xml"
    write_synthetic_xml(path, root)
    source_sha256 = file_sha256(path)
    audit = audit_network_connection_mode(
        ET.parse(path).getroot(),
        traffic_side=traffic_side.value,
        endpoint_tolerance_m=0.01,
    )
    snapshot = canonicalize_net_xml_file(path, traffic_side=traffic_side)
    safety = audit_independent_movement_safety(snapshot)
    connection_path = fixture_dir / f"{fixture_id}.{traffic_side.value}.connection-mode.json"
    safety_path = fixture_dir / f"{fixture_id}.{traffic_side.value}.independent-safety.json"
    write_json_atomic(connection_path, audit, sort_keys=True)
    write_json_atomic(
        safety_path,
        safety.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return CleanSyntheticFixture(
        path=path,
        sha256=source_sha256,
        audit=audit,
        snapshot=snapshot,
        safety=safety,
        status=combined_synthetic_status(audit, safety.status),
        artifact_paths=frozenset({path, connection_path, safety_path}),
    )


def run_synthetic_mutation_sequence(
    *,
    case_id: str,
    mutation_ids: Sequence[str],
    clean_fixture: CleanSyntheticFixture,
    traffic_side: TrafficSide,
    case_dir: Path,
) -> SyntheticMutationEvidence:
    if not mutation_ids:
        raise ValueError("Synthetic mutation sequence cannot be empty.")
    mutant_root = ET.parse(clean_fixture.path).getroot()
    for mutation_id in mutation_ids:
        mutant_root = apply_synthetic_mutation(mutant_root, mutation_id)
    mutant_path = case_dir / f"{case_id}.mutant.net.xml"
    write_synthetic_xml(mutant_path, mutant_root)
    mutant_sha256 = file_sha256(mutant_path)
    candidate_audit = audit_network_connection_mode(
        ET.parse(mutant_path).getroot(),
        traffic_side=traffic_side.value,
        endpoint_tolerance_m=0.01,
    )
    candidate_snapshot = canonicalize_net_xml_file(
        mutant_path,
        traffic_side=traffic_side,
    )
    candidate_safety = audit_independent_movement_safety(candidate_snapshot)
    scope = build_scope_from_junction_ids(
        clean_fixture.snapshot,
        candidate_snapshot,
        target_source_junction_ids=("J0",),
        target_candidate_junction_ids=("J0",),
    )
    source_connection_findings = canonicalize_connection_mode_findings(
        clean_fixture.audit,
        clean_fixture.snapshot,
    )
    candidate_connection_findings = canonicalize_connection_mode_findings(
        candidate_audit,
        candidate_snapshot,
    )
    exact_diff = compare_canonical_snapshots(
        clean_fixture.snapshot,
        candidate_snapshot,
        scope=scope,
        source_findings=(
            *source_connection_findings,
            *clean_fixture.safety.findings,
        ),
        candidate_findings=(
            *candidate_connection_findings,
            *candidate_safety.findings,
        ),
    )
    observed = synthetic_observations(
        connection_findings=candidate_connection_findings,
        safety_findings=candidate_safety.findings,
        exact_diff=exact_diff,
    )
    source_immutable = file_sha256(clean_fixture.path) == clean_fixture.sha256
    exact_delta_count = len(exact_diff.entity_deltas)
    abstention_proven = bool(
        candidate_audit.get("automatic_promotion_gate") == "blocked"
        or candidate_safety.automatic_promotion_gate is GateStatus.BLOCKED
        or exact_delta_count
    )
    connection_path = case_dir / f"{case_id}.connection-mode.json"
    safety_path = case_dir / f"{case_id}.independent-safety.json"
    diff_path = case_dir / f"{case_id}.exact-diff.json"
    write_json_atomic(connection_path, candidate_audit, sort_keys=True)
    write_json_atomic(
        safety_path,
        candidate_safety.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(
        diff_path,
        exact_diff.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return SyntheticMutationEvidence(
        source_path=clean_fixture.path,
        source_sha256=clean_fixture.sha256,
        mutant_path=mutant_path,
        mutant_sha256=mutant_sha256,
        source_immutable=source_immutable,
        candidate_audit=candidate_audit,
        candidate_snapshot=candidate_snapshot,
        candidate_safety=candidate_safety,
        exact_diff=exact_diff,
        observed=observed,
        exact_delta_count=exact_delta_count,
        abstention_proven=abstention_proven,
        artifact_paths=frozenset(
            {mutant_path, connection_path, safety_path, diff_path}
        ),
    )


def synthetic_observations(
    *,
    connection_findings: Sequence[Any],
    safety_findings: Sequence[Any],
    exact_diff: Any,
) -> dict[str, dict[str, int]]:
    channels: dict[str, Counter[str]] = {
        "connection-structural": Counter(),
        "connection-review": Counter(),
        "independent-safety": Counter(),
        "independent-review": Counter(),
        "exact-delta": Counter(),
    }
    for finding in connection_findings:
        channel = (
            "connection-structural"
            if finding.severity is FindingSeverity.STRUCTURAL
            else "connection-review"
        )
        channels[channel][finding.category] += 1
    for finding in safety_findings:
        channel = (
            "independent-safety"
            if finding.severity is FindingSeverity.SAFETY
            else "independent-review"
        )
        channels[channel][finding.category] += 1
    for delta in exact_diff.entity_deltas:
        channels["exact-delta"][f"{delta.entity_kind}:{delta.action.value}"] += 1
    return {
        channel: dict(sorted(counts.items()))
        for channel, counts in channels.items()
    }


def match_synthetic_observation(
    expectation: GoldObservation,
    observed: dict[str, dict[str, int]],
) -> ObservationMatch:
    count = observed.get(expectation.channel, {}).get(expectation.value, 0)
    return ObservationMatch(
        channel=expectation.channel,
        value=expectation.value,
        expected_minimum_count=expectation.minimum_count,
        observed_count=count,
        matched=count >= expectation.minimum_count,
    )


def combined_synthetic_status(
    audit: dict[str, Any],
    safety_status: GateStatus,
) -> GateStatus:
    connection_status = str(audit.get("status", "fail"))
    if connection_status == "fail" or safety_status in {
        GateStatus.FAIL,
        GateStatus.BLOCKED,
    }:
        return GateStatus.BLOCKED
    if connection_status == "review_required" or safety_status is GateStatus.REVIEW:
        return GateStatus.REVIEW
    return GateStatus.PASS


def write_synthetic_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = deepcopy(root)
    ET.indent(serializable, space="    ")
    ET.ElementTree(serializable).write(
        path,
        encoding="utf-8",
        xml_declaration=True,
    )
