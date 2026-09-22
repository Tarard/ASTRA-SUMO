from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


DomainDecision = Literal["in-domain", "out-of-domain", "invalid"]


class CertificationEnvelope(ContractModel):
    schema_id: str = "torii.corridor.certification-envelope/v1"
    envelope_id: StableToken
    claim_name: str
    allowed_traffic_sides: tuple[TrafficSide, ...]
    allowed_arm_counts: tuple[int, ...]
    allowed_movement_mode_classes: tuple[str, ...]
    allowed_controller_types: tuple[str, ...]
    maximum_controller_owner_cells: int = Field(ge=1)
    maximum_programs_per_controller: int = Field(ge=1)
    maximum_movements_per_signal_group: int = Field(ge=1)
    require_internal_geometry: bool = True
    allow_link_index2: bool = False
    allow_unsupported_controlled_connections: bool = False
    allow_pedestrian_facilities: bool = False
    allow_grade_separation: bool = False
    disallowed_edge_types: tuple[str, ...] = ()

    def identity_payload(self) -> dict[str, object]:
        return {
            "claim_name": self.claim_name,
            "allowed_traffic_sides": [side.value for side in self.allowed_traffic_sides],
            "allowed_arm_counts": list(self.allowed_arm_counts),
            "allowed_movement_mode_classes": list(
                self.allowed_movement_mode_classes
            ),
            "allowed_controller_types": list(self.allowed_controller_types),
            "maximum_controller_owner_cells": self.maximum_controller_owner_cells,
            "maximum_programs_per_controller": self.maximum_programs_per_controller,
            "maximum_movements_per_signal_group": (
                self.maximum_movements_per_signal_group
            ),
            "require_internal_geometry": self.require_internal_geometry,
            "allow_link_index2": self.allow_link_index2,
            "allow_unsupported_controlled_connections": (
                self.allow_unsupported_controlled_connections
            ),
            "allow_pedestrian_facilities": self.allow_pedestrian_facilities,
            "allow_grade_separation": self.allow_grade_separation,
            "disallowed_edge_types": list(self.disallowed_edge_types),
        }

    @model_validator(mode="after")
    def validate_envelope(self) -> CertificationEnvelope:
        require_stable_id(self.envelope_id, kind="policy")
        if self.envelope_id != stable_id("policy", self.identity_payload()):
            raise ValueError("envelope_id does not match the certification payload.")
        if not self.claim_name.strip():
            raise ValueError("Certification envelopes require a claim name.")
        if not self.allowed_traffic_sides or TrafficSide.UNKNOWN in self.allowed_traffic_sides:
            raise ValueError("Certification envelopes require known traffic sides.")
        for values, label in (
            (self.allowed_traffic_sides, "traffic sides"),
            (self.allowed_arm_counts, "arm counts"),
            (self.allowed_movement_mode_classes, "movement modes"),
            (self.allowed_controller_types, "controller types"),
            (self.disallowed_edge_types, "disallowed edge types"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Certification envelope {label} must be unique.")
        if not self.allowed_arm_counts or any(value < 1 for value in self.allowed_arm_counts):
            raise ValueError("Certification envelopes require positive arm counts.")
        if not self.allowed_movement_mode_classes or not self.allowed_controller_types:
            raise ValueError("Certification envelopes require mode and controller domains.")
        return self


class CellApplicabilityFeatures(ContractModel):
    physical_cell_id: StableToken
    incoming_arm_count: int = Field(ge=0)
    outgoing_arm_count: int = Field(ge=0)
    movement_mode_class_counts: dict[str, int]
    internal_path_status_counts: dict[str, int]
    crossing_edge_count: int = Field(ge=0)
    walkingarea_edge_count: int = Field(ge=0)
    link_index2_connection_count: int = Field(ge=0)
    unsupported_controlled_connection_count: int = Field(ge=0)
    edge_types: tuple[str, ...]
    has_grade_separation: bool

    @model_validator(mode="after")
    def validate_cell(self) -> CellApplicabilityFeatures:
        require_stable_id(self.physical_cell_id, kind="cell")
        return self


class ControllerApplicabilityFeatures(ContractModel):
    controller_id: StableToken
    owner_physical_cell_ids: tuple[StableToken, ...]
    program_count: int = Field(ge=0)
    controller_types: tuple[str, ...]
    maximum_signal_group_movement_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_controller(self) -> ControllerApplicabilityFeatures:
        require_stable_id(self.controller_id, kind="controller")
        for cell_id in self.owner_physical_cell_ids:
            require_stable_id(cell_id, kind="cell")
        return self


class NetworkApplicabilityFeatures(ContractModel):
    source_sha256: Sha256 | None
    traffic_side: TrafficSide
    cells: tuple[CellApplicabilityFeatures, ...]
    controllers: tuple[ControllerApplicabilityFeatures, ...]
    unresolved_controlled_connection_count: int = Field(ge=0)
    unresolved_pedestrian_facility_count: int = Field(ge=0)


class ApplicabilityFinding(ContractModel):
    finding_id: StableToken
    category: str
    subject_id: str
    witness: dict[str, Any]

    @model_validator(mode="after")
    def validate_finding(self) -> ApplicabilityFinding:
        require_stable_id(self.finding_id, kind="finding")
        return self


class CertificationApplicabilityReport(ContractModel):
    schema_id: str = "torii.corridor.certification-applicability-report/v1"
    envelope_id: StableToken
    source_sha256: Sha256 | None
    classification_status: GateStatus
    decision: DomainDecision
    certification_gate: GateStatus
    features: NetworkApplicabilityFeatures
    findings: tuple[ApplicabilityFinding, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> CertificationApplicabilityReport:
        require_stable_id(self.envelope_id, kind="policy")
        if self.source_sha256 != self.features.source_sha256:
            raise ValueError("Applicability report source identity does not close.")
        if self.decision == "in-domain" and (
            self.classification_status is not GateStatus.PASS
            or self.certification_gate is not GateStatus.PASS
            or self.findings
            or self.blockers
        ):
            raise ValueError("An in-domain decision cannot contain unresolved evidence.")
        if self.decision == "out-of-domain" and (
            self.classification_status is not GateStatus.PASS
            or self.certification_gate is not GateStatus.BLOCKED
            or not self.findings
        ):
            raise ValueError("Out-of-domain decisions require precise blocking findings.")
        if self.decision == "invalid" and (
            self.classification_status is not GateStatus.FAIL
            or self.certification_gate is not GateStatus.BLOCKED
            or not self.blockers
        ):
            raise ValueError("Invalid applicability reports must fail closed.")
        return self


def evaluate_certification_applicability(
    snapshot: CanonicalNetworkSnapshot,
    envelope: CertificationEnvelope,
) -> CertificationApplicabilityReport:
    features = extract_network_applicability_features(snapshot)
    blockers: list[str] = []
    if not features.cells:
        blockers.append("canonical_snapshot_contains_no_physical_cells")
    if not features.controllers:
        blockers.append("canonical_snapshot_contains_no_controllers")
    if blockers:
        return CertificationApplicabilityReport(
            envelope_id=envelope.envelope_id,
            source_sha256=snapshot.source_sha256,
            classification_status=GateStatus.FAIL,
            decision="invalid",
            certification_gate=GateStatus.BLOCKED,
            features=features,
            findings=(),
            blockers=tuple(blockers),
        )

    raw_findings: list[tuple[str, str, dict[str, Any]]] = []

    def add(category: str, subject_id: str, witness: dict[str, Any]) -> None:
        raw_findings.append((category, subject_id, witness))

    if features.traffic_side not in envelope.allowed_traffic_sides:
        add(
            "traffic_side_outside_envelope",
            "network",
            {
                "observed": features.traffic_side.value,
                "allowed": [side.value for side in envelope.allowed_traffic_sides],
            },
        )
    for cell in features.cells:
        observed_arm_count = max(cell.incoming_arm_count, cell.outgoing_arm_count)
        if observed_arm_count not in envelope.allowed_arm_counts:
            add(
                "physical_cell_arm_count_outside_envelope",
                cell.physical_cell_id,
                {
                    "incoming_arm_count": cell.incoming_arm_count,
                    "outgoing_arm_count": cell.outgoing_arm_count,
                    "allowed": list(envelope.allowed_arm_counts),
                },
            )
        for mode in sorted(cell.movement_mode_class_counts):
            if mode not in envelope.allowed_movement_mode_classes:
                add(
                    "movement_mode_class_outside_envelope",
                    cell.physical_cell_id,
                    {
                        "mode": mode,
                        "count": cell.movement_mode_class_counts[mode],
                        "allowed": list(envelope.allowed_movement_mode_classes),
                    },
                )
        if envelope.require_internal_geometry:
            missing_statuses = {
                status: count
                for status, count in cell.internal_path_status_counts.items()
                if status not in {"complete", "pass"}
            }
            if missing_statuses:
                add(
                    "internal_geometry_outside_envelope",
                    cell.physical_cell_id,
                    {"path_status_counts": missing_statuses},
                )
        if cell.link_index2_connection_count and not envelope.allow_link_index2:
            add(
                "secondary_signal_index_outside_envelope",
                cell.physical_cell_id,
                {"count": cell.link_index2_connection_count},
            )
        if (
            cell.unsupported_controlled_connection_count
            and not envelope.allow_unsupported_controlled_connections
        ):
            add(
                "unsupported_controlled_connection_outside_envelope",
                cell.physical_cell_id,
                {"count": cell.unsupported_controlled_connection_count},
            )
        pedestrian_count = cell.crossing_edge_count + cell.walkingarea_edge_count
        if pedestrian_count and not envelope.allow_pedestrian_facilities:
            add(
                "pedestrian_facility_outside_envelope",
                cell.physical_cell_id,
                {
                    "crossing_edge_count": cell.crossing_edge_count,
                    "walkingarea_edge_count": cell.walkingarea_edge_count,
                },
            )
        disallowed_types = sorted(
            set(cell.edge_types) & set(envelope.disallowed_edge_types)
        )
        if disallowed_types:
            add(
                "edge_type_outside_envelope",
                cell.physical_cell_id,
                {"edge_types": disallowed_types},
            )
        if cell.has_grade_separation and not envelope.allow_grade_separation:
            add(
                "grade_separation_outside_envelope",
                cell.physical_cell_id,
                {"has_grade_separation": True},
            )
    for controller in features.controllers:
        if (
            len(controller.owner_physical_cell_ids)
            > envelope.maximum_controller_owner_cells
        ):
            add(
                "controller_ownership_outside_envelope",
                controller.controller_id,
                {
                    "owner_count": len(controller.owner_physical_cell_ids),
                    "maximum": envelope.maximum_controller_owner_cells,
                },
            )
        if controller.program_count > envelope.maximum_programs_per_controller:
            add(
                "controller_program_count_outside_envelope",
                controller.controller_id,
                {
                    "program_count": controller.program_count,
                    "maximum": envelope.maximum_programs_per_controller,
                },
            )
        disallowed_controller_types = sorted(
            set(controller.controller_types) - set(envelope.allowed_controller_types)
        )
        if disallowed_controller_types:
            add(
                "controller_type_outside_envelope",
                controller.controller_id,
                {
                    "controller_types": disallowed_controller_types,
                    "allowed": list(envelope.allowed_controller_types),
                },
            )
        if (
            controller.maximum_signal_group_movement_count
            > envelope.maximum_movements_per_signal_group
        ):
            add(
                "signal_group_size_outside_envelope",
                controller.controller_id,
                {
                    "maximum_observed": (
                        controller.maximum_signal_group_movement_count
                    ),
                    "maximum_allowed": envelope.maximum_movements_per_signal_group,
                },
            )
    if features.unresolved_controlled_connection_count:
        add(
            "unresolved_controlled_ownership_outside_envelope",
            "network",
            {"count": features.unresolved_controlled_connection_count},
        )
    if (
        features.unresolved_pedestrian_facility_count
        and not envelope.allow_pedestrian_facilities
    ):
        add(
            "unresolved_pedestrian_facility_outside_envelope",
            "network",
            {"count": features.unresolved_pedestrian_facility_count},
        )
    findings = tuple(
        ApplicabilityFinding(
            finding_id=stable_id(
                "finding",
                {
                    "envelope_id": envelope.envelope_id,
                    "source_sha256": snapshot.source_sha256,
                    "category": category,
                    "subject_id": subject_id,
                    "witness": witness,
                },
            ),
            category=category,
            subject_id=subject_id,
            witness=witness,
        )
        for category, subject_id, witness in sorted(
            raw_findings,
            key=lambda item: (item[0], item[1], repr(sorted(item[2].items()))),
        )
    )
    return CertificationApplicabilityReport(
        envelope_id=envelope.envelope_id,
        source_sha256=snapshot.source_sha256,
        classification_status=GateStatus.PASS,
        decision="out-of-domain" if findings else "in-domain",
        certification_gate=GateStatus.BLOCKED if findings else GateStatus.PASS,
        features=features,
        findings=findings,
        blockers=(),
    )


def extract_network_applicability_features(
    snapshot: CanonicalNetworkSnapshot,
) -> NetworkApplicabilityFeatures:
    by_kind: dict[str, list[CanonicalEntity]] = defaultdict(list)
    for entity in snapshot.entities:
        by_kind[entity.kind].append(entity)
    approaches_by_cell: dict[str, Counter[str]] = defaultdict(Counter)
    for approach in by_kind["approach"]:
        for cell_id in approach.owner_physical_cell_ids:
            approaches_by_cell[cell_id][str(approach.payload.get("flow", "unknown"))] += 1
    paths_by_cell: dict[str, Counter[str]] = defaultdict(Counter)
    for path in by_kind["internal_path"]:
        for variant in path.payload.get("path_variants", ()):  # type: ignore[union-attr]
            path_payload = variant.get("path", {}) if isinstance(variant, dict) else {}
            status = str(path_payload.get("status", "unknown"))
            for cell_id in path.owner_physical_cell_ids:
                paths_by_cell[cell_id][status] += 1
    coverage_by_cell: dict[str, CanonicalEntity] = {}
    unresolved_controlled = 0
    unresolved_pedestrian = 0
    for coverage in by_kind["safety_coverage"]:
        if coverage.owner_physical_cell_ids:
            coverage_by_cell[coverage.owner_physical_cell_ids[0]] = coverage
        else:
            unresolved_controlled += int(
                coverage.payload.get("unsupported_controlled_connection_count", 0)
            )
            unresolved_pedestrian += int(
                coverage.payload.get("crossing_edge_count", 0)
            ) + int(coverage.payload.get("walkingarea_edge_count", 0))
    ports_by_cell: dict[str, list[CanonicalEntity]] = defaultdict(list)
    for port in by_kind["boundary_port"]:
        for cell_id in port.owner_physical_cell_ids:
            ports_by_cell[cell_id].append(port)
    cells: list[CellApplicabilityFeatures] = []
    for cell in by_kind["physical_cell"]:
        cell_id = cell.stable_entity_id
        coverage = coverage_by_cell.get(cell_id)
        coverage_payload = coverage.payload if coverage is not None else {}
        edge_types: set[str] = set()
        has_grade_separation = False
        for port in ports_by_cell[cell_id]:
            edge_semantics = port.payload.get("edge_semantics", {})
            if not isinstance(edge_semantics, dict):
                continue
            edge_type = str(edge_semantics.get("type", ""))
            if edge_type:
                edge_types.add(edge_type)
            params = edge_semantics.get("params", {})
            if not isinstance(params, dict):
                continue
            layer = str(params.get("layer", "0"))
            bridge = str(params.get("bridge", "")).casefold()
            tunnel = str(params.get("tunnel", "")).casefold()
            has_grade_separation = has_grade_separation or layer not in {"", "0"}
            has_grade_separation = has_grade_separation or bridge not in {
                "",
                "0",
                "false",
                "no",
            }
            has_grade_separation = has_grade_separation or tunnel not in {
                "",
                "0",
                "false",
                "no",
            }
        cells.append(
            CellApplicabilityFeatures(
                physical_cell_id=cell_id,
                incoming_arm_count=approaches_by_cell[cell_id]["incoming"],
                outgoing_arm_count=approaches_by_cell[cell_id]["outgoing"],
                movement_mode_class_counts={
                    str(key): int(value)
                    for key, value in dict(
                        coverage_payload.get("movement_mode_class_counts", {})
                    ).items()
                },
                internal_path_status_counts=dict(paths_by_cell[cell_id]),
                crossing_edge_count=int(
                    coverage_payload.get("crossing_edge_count", 0)
                ),
                walkingarea_edge_count=int(
                    coverage_payload.get("walkingarea_edge_count", 0)
                ),
                link_index2_connection_count=int(
                    coverage_payload.get("link_index2_connection_count", 0)
                ),
                unsupported_controlled_connection_count=int(
                    coverage_payload.get(
                        "unsupported_controlled_connection_count",
                        0,
                    )
                ),
                edge_types=tuple(sorted(edge_types)),
                has_grade_separation=has_grade_separation,
            )
        )
    programs_by_id = {
        program.stable_entity_id: program
        for program in by_kind["controller_program"]
    }
    groups_by_id = {
        group.stable_entity_id: group for group in by_kind["signal_group"]
    }
    controllers: list[ControllerApplicabilityFeatures] = []
    for controller in by_kind["controller"]:
        program_ids = tuple(
            str(value) for value in controller.payload.get("program_ids", ())
        )
        program_types = sorted(
            {
                str(programs_by_id[program_id].payload.get("controller_type", ""))
                for program_id in program_ids
                if program_id in programs_by_id
            }
        )
        group_ids = tuple(
            str(value) for value in controller.payload.get("signal_group_ids", ())
        )
        maximum_group_size = max(
            (
                len(groups_by_id[group_id].payload.get("movement_ids", ()))
                for group_id in group_ids
                if group_id in groups_by_id
            ),
            default=0,
        )
        controllers.append(
            ControllerApplicabilityFeatures(
                controller_id=controller.stable_entity_id,
                owner_physical_cell_ids=controller.owner_physical_cell_ids,
                program_count=len(program_ids),
                controller_types=tuple(program_types),
                maximum_signal_group_movement_count=maximum_group_size,
            )
        )
    return NetworkApplicabilityFeatures(
        source_sha256=snapshot.source_sha256,
        traffic_side=snapshot.traffic_side,
        cells=tuple(sorted(cells, key=lambda item: item.physical_cell_id)),
        controllers=tuple(
            sorted(controllers, key=lambda item: item.controller_id)
        ),
        unresolved_controlled_connection_count=unresolved_controlled,
        unresolved_pedestrian_facility_count=unresolved_pedestrian,
    )
