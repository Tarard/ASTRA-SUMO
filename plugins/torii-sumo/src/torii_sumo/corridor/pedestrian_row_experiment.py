from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.command_runner import run_command

from .enums import GateStatus, TrafficSide
from .ids import stable_id
from .pedestrian_row_contracts import (
    ObservedYieldBehavior,
    ROWExperimentCaseResult,
    ROWExperimentReport,
    ROWRuntimeProbe,
    SourceROWBundle,
)
from .pedestrian_row_oracle import (
    assess_row_static_consistency,
    build_row_geometry_evidence,
    build_row_model_claim_evidence,
    infer_source_row_class,
    make_source_row_bundle,
    parse_plain_source_row_bundle,
)
from .pedestrian_row_runtime import run_row_runtime_probe


VehicleTurnClass = Literal["straight", "left", "right"]

_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")
_VEHICLE_MOVEMENTS: dict[VehicleTurnClass, tuple[str, str]] = {
    "straight": ("WC", "CE"),
    "left": ("SC", "CW"),
    "right": ("NC", "CW"),
}
def run_row1_experiment(
    *,
    fixture_dir: Path,
    output_dir: Path,
    netconvert_binary: Path,
    sumo_binary: Path,
) -> ROWExperimentReport:
    fixtures = fixture_dir.resolve(strict=True)
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    netconvert = netconvert_binary.resolve(strict=True)
    sumo = sumo_binary.resolve(strict=True)
    fixture_paths = {
        name: fixtures / name
        for name in (
            "four_arm.nod.xml",
            "four_arm_signalized.nod.xml",
            "four_arm.edg.xml",
            "priority.con.xml",
            "unprioritized.con.xml",
            "unknown-priority.con.xml",
        )
    }
    for path in fixture_paths.values():
        path.resolve(strict=True)

    candidates = {
        "priority-right": _materialize_candidate(
            node_file=fixture_paths["four_arm.nod.xml"],
            edge_file=fixture_paths["four_arm.edg.xml"],
            connection_file=fixture_paths["priority.con.xml"],
            output_dir=destination / "candidates" / "priority-right",
            netconvert_binary=netconvert,
            lefthand=False,
        ),
        "unprioritized-right": _materialize_candidate(
            node_file=fixture_paths["four_arm.nod.xml"],
            edge_file=fixture_paths["four_arm.edg.xml"],
            connection_file=fixture_paths["unprioritized.con.xml"],
            output_dir=destination / "candidates" / "unprioritized-right",
            netconvert_binary=netconvert,
            lefthand=False,
        ),
        "priority-left": _materialize_candidate(
            node_file=fixture_paths["four_arm.nod.xml"],
            edge_file=fixture_paths["four_arm.edg.xml"],
            connection_file=fixture_paths["priority.con.xml"],
            output_dir=destination / "candidates" / "priority-left",
            netconvert_binary=netconvert,
            lefthand=True,
        ),
        "signalized-right": _materialize_candidate(
            node_file=fixture_paths["four_arm_signalized.nod.xml"],
            edge_file=fixture_paths["four_arm.edg.xml"],
            connection_file=fixture_paths["priority.con.xml"],
            output_dir=destination / "candidates" / "signalized-right",
            netconvert_binary=netconvert,
            lefthand=False,
        ),
    }
    source_bundles = _source_bundles(fixture_paths)
    cases: list[ROWExperimentCaseResult] = []

    for row_class, candidate_key, expected_behavior in (
        ("priority", "priority-right", "vehicle-yielded"),
        ("unprioritized", "unprioritized-right", "pedestrian-yielded"),
    ):
        source_bundle = source_bundles[row_class]
        for turn_class in ("straight", "left", "right"):
            from_edge, to_edge = _VEHICLE_MOVEMENTS[turn_class]
            candidate = candidates[candidate_key]
            crossing_edge, internal_lane = _candidate_refs(
                candidate,
                from_edge=from_edge,
                to_edge=to_edge,
            )
            case_key = f"gold-{row_class}-right-{turn_class}"
            probes = (
                _runtime_matrix(
                    candidate_net=candidate,
                    crossing_edge_id=crossing_edge,
                    vehicle_internal_lane_id=internal_lane,
                    vehicle_from_edge=from_edge,
                    vehicle_to_edge=to_edge,
                    sumo_binary=sumo,
                    output_dir=destination / "runtime" / case_key,
                )
                if turn_class == "straight"
                else ()
            )
            expected_static = (
                GateStatus.REVIEW
                if row_class == "unprioritized" and turn_class != "straight"
                else GateStatus.PASS
            )
            expected_runtime_behavior = (
                expected_behavior if turn_class == "straight" else None
            )
            expected_source_class = (
                "priority-unsignalized"
                if row_class == "priority"
                else "unprioritized-unsignalized"
            )
            expected_model_class = (
                "ambiguous"
                if row_class == "unprioritized" and turn_class != "straight"
                else expected_source_class
            )
            cases.append(
                build_row_experiment_case(
                    case_key=case_key,
                    case_kind="gold",
                    mutation_kind="none",
                    vehicle_turn_class=turn_class,
                    source_bundle=source_bundle,
                    candidate_net=candidate,
                    crossing_edge_id=crossing_edge,
                    vehicle_from_edge_id=from_edge,
                    vehicle_to_edge_id=to_edge,
                    runtime_probes=probes,
                    expected_source_class=expected_source_class,
                    expected_model_claim_class=expected_model_class,
                    expected_static_status=expected_static,
                    expected_simultaneous_behavior=expected_runtime_behavior,
                )
            )

    left_candidate = candidates["priority-left"]
    left_crossing, left_internal = _candidate_refs(
        left_candidate,
        from_edge="WC",
        to_edge="CE",
        crossing_edge_ids=("CE", "EC"),
    )
    left_probes = _runtime_matrix(
        candidate_net=left_candidate,
        crossing_edge_id=left_crossing,
        vehicle_internal_lane_id=left_internal,
        vehicle_from_edge="WC",
        vehicle_to_edge="CE",
        sumo_binary=sumo,
        output_dir=destination / "runtime" / "gold-priority-left-straight",
        simultaneous_vehicle_depart_s=68.0,
    )
    cases.append(
        build_row_experiment_case(
            case_key="gold-priority-left-straight",
            case_kind="gold",
            mutation_kind="none",
            vehicle_turn_class="straight",
            source_bundle=source_bundles["priority-left"],
            candidate_net=left_candidate,
            crossing_edge_id=left_crossing,
            vehicle_from_edge_id="WC",
            vehicle_to_edge_id="CE",
            runtime_probes=left_probes,
            expected_source_class="priority-unsignalized",
            expected_model_claim_class="priority-unsignalized",
            expected_static_status=GateStatus.PASS,
            expected_simultaneous_behavior="vehicle-yielded",
        )
    )

    cases.extend(
        _static_boundary_cases(
            source_bundles=source_bundles,
            candidates=candidates,
        )
    )
    cases.extend(
        _mutation_cases(
            source_bundles=source_bundles,
            candidates=candidates,
            sumo_binary=sumo,
            output_dir=destination,
        )
    )

    report = build_row_experiment_report(
        cases=tuple(cases),
        fixture_sha256={
            name: file_sha256(path) for name, path in sorted(fixture_paths.items())
        },
        netconvert_binary_sha256=file_sha256(netconvert),
        netconvert_version=_tool_version(netconvert),
        sumo_binary_sha256=file_sha256(sumo),
        sumo_version=_tool_version(sumo),
    )
    write_json_atomic(
        destination / "row-1-experiment-report.json",
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return report


def build_row_experiment_case(
    *,
    case_key: str,
    case_kind: Literal["gold", "mutation", "ood"],
    mutation_kind: str,
    vehicle_turn_class: str,
    source_bundle: SourceROWBundle,
    candidate_net: Path,
    crossing_edge_id: str,
    vehicle_from_edge_id: str,
    vehicle_to_edge_id: str,
    runtime_probes: tuple[ROWRuntimeProbe, ...],
    expected_source_class: str,
    expected_model_claim_class: str,
    expected_static_status: GateStatus,
    expected_simultaneous_behavior: ObservedYieldBehavior | None,
) -> ROWExperimentCaseResult:
    source_decision = infer_source_row_class(source_bundle)
    geometry = build_row_geometry_evidence(
        candidate_net,
        crossing_edge_id=crossing_edge_id,
        vehicle_from_edge_id=vehicle_from_edge_id,
        vehicle_to_edge_id=vehicle_to_edge_id,
    )
    model_claim = build_row_model_claim_evidence(
        candidate_net,
        junction_id="C",
        crossing_edge_id=crossing_edge_id,
        vehicle_from_edge_id=vehicle_from_edge_id,
        vehicle_to_edge_id=vehicle_to_edge_id,
    )
    assessment = assess_row_static_consistency(
        source_decision,
        geometry,
        model_claim,
    )
    probes = tuple(
        sorted(
            runtime_probes,
            key=lambda item: (item.arrival_schedule, item.runtime_probe_id),
        )
    )
    blockers: list[str] = []
    if source_decision.expected_class != expected_source_class:
        blockers.append(
            "source_class_mismatch:"
            f"expected={expected_source_class}:"
            f"observed={source_decision.expected_class}"
        )
    if model_claim.inferred_class != expected_model_claim_class:
        blockers.append(
            "model_claim_class_mismatch:"
            f"expected={expected_model_claim_class}:"
            f"observed={model_claim.inferred_class}"
        )
    if assessment.status is not expected_static_status:
        blockers.append(
            "static_status_mismatch:"
            f"expected={expected_static_status.value}:"
            f"observed={assessment.status.value}"
        )
    if source_decision.model_claim_fields_read:
        blockers.append("expected_answer_read_model_claim")
    for probe in probes:
        runtime_must_pass = (
            case_kind != "mutation"
            or expected_simultaneous_behavior is not None
        )
        if runtime_must_pass and probe.runtime_status is not GateStatus.PASS:
            blockers.append(
                f"runtime_probe_blocked:{probe.arrival_schedule}"
            )
    if expected_simultaneous_behavior is not None:
        simultaneous = [
            probe
            for probe in probes
            if probe.arrival_schedule == "simultaneous"
        ]
        if len(simultaneous) != 1:
            blockers.append("simultaneous_probe_count_not_one")
        elif simultaneous[0].observed_behavior != expected_simultaneous_behavior:
            blockers.append(
                "simultaneous_behavior_mismatch:"
                f"expected={expected_simultaneous_behavior}:"
                f"observed={simultaneous[0].observed_behavior}"
            )
    _check_schedule_order(probes, blockers)
    identity = {
        "case_key": case_key,
        "case_kind": case_kind,
        "mutation_kind": mutation_kind,
        "vehicle_turn_class": vehicle_turn_class,
        "source_bundle_id": source_bundle.source_bundle_id,
        "source_decision_id": source_decision.decision_id,
        "geometry_evidence_id": geometry.geometry_evidence_id,
        "model_claim_id": model_claim.model_claim_id,
        "expected_source_class": expected_source_class,
        "expected_model_claim_class": expected_model_claim_class,
        "runtime_probe_ids": [probe.runtime_probe_id for probe in probes],
    }
    return ROWExperimentCaseResult(
        case_id=stable_id("evidence", identity),
        case_key=case_key,
        case_kind=case_kind,
        mutation_kind=mutation_kind,
        vehicle_turn_class=vehicle_turn_class,
        source_bundle=source_bundle,
        source_decision=source_decision,
        geometry_evidence=geometry,
        model_claim=model_claim,
        static_assessment=assessment,
        runtime_probes=probes,
        expected_source_class=expected_source_class,
        expected_model_claim_class=expected_model_claim_class,
        expected_static_status=expected_static_status,
        expected_simultaneous_behavior=expected_simultaneous_behavior,
        blockers=tuple(blockers),
        case_passed=not blockers,
    )


def build_row_experiment_report(
    *,
    cases: tuple[ROWExperimentCaseResult, ...],
    fixture_sha256: dict[str, str],
    netconvert_binary_sha256: str,
    netconvert_version: str,
    sumo_binary_sha256: str,
    sumo_version: str,
) -> ROWExperimentReport:
    ordered = tuple(sorted(cases, key=lambda item: item.case_id))
    failed_count = sum(not case.case_passed for case in ordered)
    unsafe_false_pass_count = sum(
        case.case_kind == "mutation"
        and case.expected_static_status is GateStatus.BLOCKED
        and case.static_assessment.status is GateStatus.PASS
        for case in ordered
    )
    forced_source_count = sum(
        case.source_decision.expected_class
        in {"unknown-unsignalized", "shared-space-or-unsupported"}
        and not case.source_decision.abstained
        for case in ordered
    )
    model_read_count = sum(
        bool(case.source_decision.model_claim_fields_read) for case in ordered
    )
    status = (
        GateStatus.PASS
        if not failed_count
        and not unsafe_false_pass_count
        and not forced_source_count
        and not model_read_count
        else GateStatus.BLOCKED
    )
    identity = {
        "fixture_sha256": fixture_sha256,
        "netconvert_binary_sha256": netconvert_binary_sha256,
        "netconvert_version": netconvert_version,
        "sumo_binary_sha256": sumo_binary_sha256,
        "sumo_version": sumo_version,
        "case_ids": tuple(case.case_id for case in ordered),
        "status": status,
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    return ROWExperimentReport(
        report_id=stable_id("manifest", identity),
        fixture_sha256=dict(sorted(fixture_sha256.items())),
        netconvert_binary_sha256=netconvert_binary_sha256,
        netconvert_version=netconvert_version,
        sumo_binary_sha256=sumo_binary_sha256,
        sumo_version=sumo_version,
        cases=ordered,
        gold_case_count=sum(case.case_kind == "gold" for case in ordered),
        mutation_case_count=sum(
            case.case_kind == "mutation" for case in ordered
        ),
        ood_case_count=sum(case.case_kind == "ood" for case in ordered),
        runtime_probe_count=sum(len(case.runtime_probes) for case in ordered),
        failed_case_count=failed_count,
        unsafe_false_pass_count=unsafe_false_pass_count,
        source_insufficient_forced_decision_count=forced_source_count,
        expected_answer_model_claim_read_count=model_read_count,
        status=status,
        automatic_promotion_gate=GateStatus.BLOCKED,
    )


def _source_bundles(paths: dict[str, Path]) -> dict[str, SourceROWBundle]:
    def parse(
        nodes: str,
        connections: str,
        side: TrafficSide,
        crossing_edge_ids: tuple[str, str] = ("WC", "CW"),
    ) -> SourceROWBundle:
        return parse_plain_source_row_bundle(
            nodes_file=paths[nodes],
            connections_file=paths[connections],
            crossing_node_id="C",
            crossing_edge_ids=crossing_edge_ids,
            traffic_side=side,
        )

    priority = parse(
        "four_arm.nod.xml",
        "priority.con.xml",
        TrafficSide.RIGHT,
    )
    unknown = parse(
        "four_arm.nod.xml",
        "unknown-priority.con.xml",
        TrafficSide.RIGHT,
    )
    return {
        "priority": priority,
        "unprioritized": parse(
            "four_arm.nod.xml",
            "unprioritized.con.xml",
            TrafficSide.RIGHT,
        ),
        "signalized": parse(
            "four_arm_signalized.nod.xml",
            "priority.con.xml",
            TrafficSide.RIGHT,
        ),
        "unknown": unknown,
        "priority-left": parse(
            "four_arm.nod.xml",
            "priority.con.xml",
            TrafficSide.LEFT,
            ("CE", "EC"),
        ),
        "split": make_source_row_bundle(
            crossing_node_id=priority.crossing_node_id,
            crossing_edge_ids=priority.crossing_edge_ids,
            traffic_side=priority.traffic_side,
            crossing_stage_count=2,
            junction_control_kind="unsignalized",
            explicit_crossing_priority=True,
            source_status="complete",
            observations=priority.observations,
        ),
        "shared": make_source_row_bundle(
            crossing_node_id=unknown.crossing_node_id,
            crossing_edge_ids=unknown.crossing_edge_ids,
            traffic_side=unknown.traffic_side,
            crossing_stage_count=1,
            junction_control_kind="shared-space-or-unsupported",
            explicit_crossing_priority=None,
            source_status="unsupported",
            observations=unknown.observations,
        ),
    }


def _static_boundary_cases(
    *,
    source_bundles: dict[str, SourceROWBundle],
    candidates: dict[str, Path],
) -> list[ROWExperimentCaseResult]:
    cases: list[ROWExperimentCaseResult] = []
    for case_key, source_key, candidate_key, expected_source, expected_model in (
        (
            "gold-signalized-right-straight",
            "signalized",
            "signalized-right",
            "signalized",
            "signalized",
        ),
        (
            "ood-unknown-priority",
            "unknown",
            "priority-right",
            "unknown-unsignalized",
            "priority-unsignalized",
        ),
        (
            "ood-shared-space",
            "shared",
            "priority-right",
            "shared-space-or-unsupported",
            "priority-unsignalized",
        ),
        (
            "ood-split-crossing",
            "split",
            "priority-right",
            "priority-unsignalized",
            "priority-unsignalized",
        ),
    ):
        candidate = candidates[candidate_key]
        crossing, _internal = _candidate_refs(
            candidate,
            from_edge="WC",
            to_edge="CE",
        )
        cases.append(
            build_row_experiment_case(
                case_key=case_key,
                case_kind=("gold" if case_key.startswith("gold") else "ood"),
                mutation_kind=(
                    "none" if case_key.startswith("gold") else "not-applicable"
                ),
                vehicle_turn_class="straight",
                source_bundle=source_bundles[source_key],
                candidate_net=candidate,
                crossing_edge_id=crossing,
                vehicle_from_edge_id="WC",
                vehicle_to_edge_id="CE",
                runtime_probes=(),
                expected_source_class=expected_source,
                expected_model_claim_class=expected_model,
                expected_static_status=GateStatus.REVIEW,
                expected_simultaneous_behavior=None,
            )
        )
    return cases


def _mutation_cases(
    *,
    source_bundles: dict[str, SourceROWBundle],
    candidates: dict[str, Path],
    sumo_binary: Path,
    output_dir: Path,
) -> list[ROWExperimentCaseResult]:
    priority = candidates["priority-right"]
    unprioritized = candidates["unprioritized-right"]
    crossing, internal = _candidate_refs(
        priority,
        from_edge="WC",
        to_edge="CE",
    )
    request_mutant = output_dir / "mutations" / "request-reversed.net.xml"
    _reverse_request_priority(
        priority,
        request_mutant,
        crossing_edge_id=crossing,
        vehicle_from_edge="WC",
        vehicle_to_edge="CE",
    )
    signal_crossing, _ = _candidate_refs(
        candidates["signalized-right"],
        from_edge="WC",
        to_edge="CE",
    )
    signal_mutant = output_dir / "mutations" / "signal-g-G-reversed.net.xml"
    _reverse_signal_g_G(candidates["signalized-right"], signal_mutant)

    definitions = (
        (
            "mutation-request-response-priority-reversed",
            "request-response-priority-reversed",
            source_bundles["priority"],
            request_mutant,
            None,
            "priority-unsignalized",
            "unprioritized-unsignalized",
        ),
        (
            "mutation-source-priority-reversed",
            "source-priority-reversed",
            source_bundles["unprioritized"],
            priority,
            "vehicle-yielded",
            "unprioritized-unsignalized",
            "priority-unsignalized",
        ),
        (
            "mutation-co-self-consistent-model-reversal",
            "co-self-consistent-model-reversal",
            source_bundles["priority"],
            unprioritized,
            "pedestrian-yielded",
            "priority-unsignalized",
            "unprioritized-unsignalized",
        ),
    )
    cases: list[ROWExperimentCaseResult] = []
    for (
        case_key,
        mutation_kind,
        source,
        candidate,
        expected_behavior,
        expected_source,
        expected_model,
    ) in definitions:
        current_crossing, current_internal = _candidate_refs(
            candidate,
            from_edge="WC",
            to_edge="CE",
        )
        probe = _runtime_matrix(
            candidate_net=candidate,
            crossing_edge_id=current_crossing,
            vehicle_internal_lane_id=current_internal,
            vehicle_from_edge="WC",
            vehicle_to_edge="CE",
            sumo_binary=sumo_binary,
            output_dir=output_dir / "runtime" / case_key,
            schedules=("simultaneous",),
        )
        cases.append(
            build_row_experiment_case(
                case_key=case_key,
                case_kind="mutation",
                mutation_kind=mutation_kind,
                vehicle_turn_class="straight",
                source_bundle=source,
                candidate_net=candidate,
                crossing_edge_id=current_crossing,
                vehicle_from_edge_id="WC",
                vehicle_to_edge_id="CE",
                runtime_probes=probe,
                expected_source_class=expected_source,
                expected_model_claim_class=expected_model,
                expected_static_status=GateStatus.BLOCKED,
                expected_simultaneous_behavior=expected_behavior,
            )
        )
    cases.append(
        build_row_experiment_case(
            case_key="mutation-signal-state-g-G-reversed",
            case_kind="mutation",
            mutation_kind="signal-state-g-g-reversed",
            vehicle_turn_class="straight",
            source_bundle=source_bundles["signalized"],
            candidate_net=signal_mutant,
            crossing_edge_id=signal_crossing,
            vehicle_from_edge_id="WC",
            vehicle_to_edge_id="CE",
            runtime_probes=(),
            expected_source_class="signalized",
            expected_model_claim_class="signalized",
            expected_static_status=GateStatus.REVIEW,
            expected_simultaneous_behavior=None,
        )
    )
    return cases


def _materialize_candidate(
    *,
    node_file: Path,
    edge_file: Path,
    connection_file: Path,
    output_dir: Path,
    netconvert_binary: Path,
    lefthand: bool,
) -> Path:
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    raw_net = destination / "candidate.raw.net.xml"
    candidate = destination / "candidate.net.xml"
    command = [
        str(netconvert_binary),
        "--node-files",
        str(node_file.resolve(strict=True)),
        "--edge-files",
        str(edge_file.resolve(strict=True)),
        "--connection-files",
        str(connection_file.resolve(strict=True)),
        "--output-file",
        str(raw_net),
        "--no-turnarounds",
        "true",
    ]
    if lefthand:
        command.extend(("--lefthand", "true"))
    result = run_command(command, cwd=destination)
    write_json_atomic(
        destination / "netconvert.command.json",
        result.to_dict(),
        sort_keys=True,
    )
    if result.status != "pass" or not raw_net.is_file():
        raise RuntimeError(
            "ROW-1 netconvert failed: " + (result.stderr or result.error)
        )
    canonical = ET.canonicalize(
        from_file=str(raw_net),
        with_comments=False,
        strip_text=True,
    )
    write_text_atomic(candidate, canonical)
    return candidate


def _candidate_refs(
    net_file: Path,
    *,
    from_edge: str,
    to_edge: str,
    crossing_edge_ids: tuple[str, str] = ("WC", "CW"),
) -> tuple[str, str]:
    root = ET.parse(net_file).getroot()
    crossing = next(
        (
            edge.attrib["id"]
            for edge in root.findall("edge")
            if edge.attrib.get("function") == "crossing"
            and set(edge.attrib.get("crossingEdges", "").split())
            == set(crossing_edge_ids)
        ),
        None,
    )
    direct = next(
        (
            connection
            for connection in root.findall("connection")
            if connection.attrib.get("from") == from_edge
            and connection.attrib.get("to") == to_edge
            and connection.attrib.get("via")
        ),
        None,
    )
    if crossing is None or direct is None:
        raise ValueError("ROW-1 candidate movement references are incomplete.")
    return crossing, direct.attrib["via"]


def _runtime_matrix(
    *,
    candidate_net: Path,
    crossing_edge_id: str,
    vehicle_internal_lane_id: str,
    vehicle_from_edge: str,
    vehicle_to_edge: str,
    sumo_binary: Path,
    output_dir: Path,
    schedules: tuple[str, ...] = (
        "pedestrian-first",
        "vehicle-first",
        "simultaneous",
    ),
    simultaneous_vehicle_depart_s: float = 70.0,
) -> tuple[ROWRuntimeProbe, ...]:
    probes: list[ROWRuntimeProbe] = []
    departures = {
        "vehicle-first": simultaneous_vehicle_depart_s - 10.0,
        "simultaneous": simultaneous_vehicle_depart_s,
        "pedestrian-first": simultaneous_vehicle_depart_s + 10.0,
    }
    for schedule in schedules:
        schedule_dir = output_dir / schedule
        vehicle_depart = departures[schedule]
        route_file = schedule_dir / "probe.rou.xml"
        write_text_atomic(
            route_file,
            _route_xml(
                vehicle_from_edge=vehicle_from_edge,
                vehicle_to_edge=vehicle_to_edge,
                pedestrian_depart_s=0.0,
                vehicle_depart_s=vehicle_depart,
            ),
        )
        probes.append(
            run_row_runtime_probe(
                net_file=candidate_net,
                route_file=route_file,
                crossing_edge_id=crossing_edge_id,
                vehicle_internal_lane_id=vehicle_internal_lane_id,
                arrival_schedule=schedule,
                pedestrian_depart_s=0.0,
                vehicle_depart_s=vehicle_depart,
                vehicle_speed_mps=13.9,
                sumo_binary=sumo_binary,
                output_dir=schedule_dir,
            )
        )
    return tuple(probes)


def _route_xml(
    *,
    vehicle_from_edge: str,
    vehicle_to_edge: str,
    pedestrian_depart_s: float,
    vehicle_depart_s: float,
) -> str:
    return (
        "<routes>"
        '<vType id="car" vClass="passenger" accel="2.6" decel="4.5" '
        'sigma="0" length="5" minGap="2.5" maxSpeed="13.9" '
        'speedFactor="1" speedDev="0"/>'
        '<vType id="pedType" vClass="pedestrian" maxSpeed="1.4" '
        'speedFactor="1" speedDev="0" impatience="off"/>'
        f'<person id="ped" type="pedType" depart="{pedestrian_depart_s:g}">'
        '<walk edges="NC CS" speed="1.4"/></person>'
        f'<vehicle id="veh" type="car" depart="{vehicle_depart_s:g}" '
        'departSpeed="max">'
        f'<route edges="{vehicle_from_edge} {vehicle_to_edge}"/>'
        "</vehicle></routes>"
    )


def _reverse_request_priority(
    source_net: Path,
    destination: Path,
    *,
    crossing_edge_id: str,
    vehicle_from_edge: str,
    vehicle_to_edge: str,
) -> None:
    claim = build_row_model_claim_evidence(
        source_net,
        junction_id="C",
        crossing_edge_id=crossing_edge_id,
        vehicle_from_edge_id=vehicle_from_edge,
        vehicle_to_edge_id=vehicle_to_edge,
    )
    if claim.relation is None:
        raise ValueError("ROW-1 request mutation cannot map request rows.")
    root = ET.parse(source_net).getroot()
    junction = root.find("junction[@id='C']")
    if junction is None:
        raise ValueError("ROW-1 request mutation junction is missing.")
    rows = {
        int(row.attrib["index"]): row
        for row in junction.findall("request")
    }
    pedestrian_index = claim.relation.pedestrian_request_index
    vehicle_index = claim.relation.vehicle_request_index
    pedestrian_row = rows[pedestrian_index]
    vehicle_row = rows[vehicle_index]
    pedestrian_row.attrib["response"] = _set_request_bit(
        pedestrian_row.attrib.get("response", ""),
        vehicle_index,
        True,
    )
    vehicle_row.attrib["response"] = _set_request_bit(
        vehicle_row.attrib.get("response", ""),
        pedestrian_index,
        False,
    )
    _write_canonical_root(root, destination)


def _set_request_bit(value: str, index: int, enabled: bool) -> str:
    if index < 0 or index >= len(value):
        raise ValueError("ROW-1 request mutation index is out of range.")
    characters = list(value)
    characters[-1 - index] = "1" if enabled else "0"
    return "".join(characters)


def _reverse_signal_g_G(source_net: Path, destination: Path) -> None:
    root = ET.parse(source_net).getroot()
    changed = False
    for phase in root.findall("tlLogic/phase"):
        state = phase.attrib.get("state", "")
        translated = state.translate(str.maketrans({"g": "G", "G": "g"}))
        if translated != state:
            phase.attrib["state"] = translated
            changed = True
    if not changed:
        raise ValueError("ROW-1 signal mutation found no g/G state.")
    _write_canonical_root(root, destination)


def _write_canonical_root(root: ET.Element, destination: Path) -> None:
    canonical = ET.canonicalize(
        xml_data=ET.tostring(root, encoding="unicode"),
        with_comments=False,
        strip_text=True,
    )
    write_text_atomic(destination, canonical)


def _check_schedule_order(
    probes: tuple[ROWRuntimeProbe, ...],
    blockers: list[str],
) -> None:
    for probe in probes:
        if (
            probe.pedestrian_crossing_entry_s is None
            or probe.vehicle_internal_entry_s is None
        ):
            continue
        if (
            probe.arrival_schedule == "pedestrian-first"
            and probe.pedestrian_crossing_entry_s
            >= probe.vehicle_internal_entry_s
        ):
            blockers.append("pedestrian_first_schedule_order_failed")
        if (
            probe.arrival_schedule == "vehicle-first"
            and probe.vehicle_internal_entry_s
            >= probe.pedestrian_crossing_entry_s
        ):
            blockers.append("vehicle_first_schedule_order_failed")


def _tool_version(binary: Path) -> str:
    result = run_command([str(binary), "--version"])
    text = f"{result.stdout}\n{result.stderr}"
    match = _VERSION_RE.search(text)
    if result.status != "pass" or match is None:
        raise ValueError(f"Unable to identify ROW-1 tool version: {binary}")
    return match.group(1)
