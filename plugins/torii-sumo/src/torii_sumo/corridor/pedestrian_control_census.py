from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree as ET

from pydantic import model_validator

from .base import ContractModel, Sha256, StableToken
from .canonicalizer import CanonicalEntity, CanonicalNetworkSnapshot
from .enums import GateStatus
from .ids import require_stable_id, stable_id


ProgramSourceKind = Literal["embedded-net", "additional-file"]
ControlledPedestrianBindingClass = Literal[
    "ordinary-program-truly-absent",
    "external-program-present",
    "runtime-special-controller",
    "program-present-link-invalid",
    "shared-controller-scope-incomplete",
    "stale-or-ambiguous-control-reference",
    "unsupported-program-form",
]

_RUNTIME_SPECIAL_JUNCTION_TYPES = frozenset(
    {"rail_crossing", "rail_signal"}
)
_SUPPORTED_EMBEDDED_PROGRAM_TYPES = frozenset({"static"})


class EffectiveTLSProgramEvidence(ContractModel):
    schema_id: str = "torii.corridor.effective-tls-program-evidence/v1"
    program_evidence_id: StableToken
    raw_controller_id: str
    source_kind: ProgramSourceKind
    source_path: str
    source_sha256: Sha256
    source_program_id: str
    controller_type: str
    phase_states: tuple[str, ...]
    phase_state_lengths: tuple[int, ...]

    @model_validator(mode="after")
    def validate_program(self) -> EffectiveTLSProgramEvidence:
        require_stable_id(self.program_evidence_id, kind="evidence")
        if not self.raw_controller_id:
            raise ValueError("TLS program evidence requires a controller ID.")
        if self.phase_state_lengths != tuple(
            len(state) for state in self.phase_states
        ):
            raise ValueError("TLS phase-state lengths do not match states.")
        expected_id = stable_id(
            "evidence",
            {
                "raw_controller_id": self.raw_controller_id,
                "source_kind": self.source_kind,
                "source_sha256": self.source_sha256,
                "source_program_id": self.source_program_id,
                "controller_type": self.controller_type,
                "phase_states": self.phase_states,
            },
        )
        if self.program_evidence_id != expected_id:
            raise ValueError(
                "TLS program evidence ID does not match its semantic identity."
            )
        return self


class EffectiveTLSProgramInventory(ContractModel):
    schema_id: str = "torii.corridor.effective-tls-program-inventory/v1"
    inventory_id: StableToken
    net_path: str
    net_sha256: Sha256
    sumocfg_path: str | None
    sumocfg_sha256: Sha256 | None
    effective_additional_paths: tuple[str, ...]
    effective_additional_sha256: dict[str, Sha256]
    unresolved_additional_paths: tuple[str, ...]
    dynamic_program_configuration_detected: bool
    programs: tuple[EffectiveTLSProgramEvidence, ...]

    @model_validator(mode="after")
    def validate_inventory(self) -> EffectiveTLSProgramInventory:
        require_stable_id(self.inventory_id, kind="manifest")
        if (self.sumocfg_path is None) != (self.sumocfg_sha256 is None):
            raise ValueError(
                "SUMO config path and hash must either both exist or both be absent."
            )
        if set(self.effective_additional_paths) != set(
            self.effective_additional_sha256
        ):
            raise ValueError(
                "Every effective additional file must have exactly one hash."
            )
        program_ids = [
            program.program_evidence_id for program in self.programs
        ]
        if len(program_ids) != len(set(program_ids)):
            raise ValueError("Effective TLS program evidence IDs must be unique.")
        expected_id = stable_id(
            "manifest",
            {
                "net_sha256": self.net_sha256,
                "sumocfg_sha256": self.sumocfg_sha256,
                "effective_additional_sha256": (
                    self.effective_additional_sha256
                ),
                "unresolved_additional_paths": (
                    self.unresolved_additional_paths
                ),
                "dynamic_program_configuration_detected": (
                    self.dynamic_program_configuration_detected
                ),
                "program_evidence_ids": program_ids,
            },
        )
        if self.inventory_id != expected_id:
            raise ValueError(
                "Effective TLS program inventory ID does not match its contents."
            )
        return self


class ControlledPedestrianBindingAssessment(ContractModel):
    schema_id: str = (
        "torii.corridor.controlled-pedestrian-binding-assessment/v1"
    )
    assessment_id: StableToken
    inventory_id: StableToken
    binding_entity_id: StableToken
    movement_id: StableToken
    owner_physical_cell_ids: tuple[StableToken, ...]
    owner_junction_types: tuple[str, ...]
    raw_controller_ids: tuple[str, ...]
    link_indices: tuple[int, ...]
    link_index2_present: bool
    raw_connection_xml: tuple[str, ...]
    program_evidence_ids: tuple[StableToken, ...]
    program_sources: tuple[ProgramSourceKind, ...]
    phase_state_lengths_by_program: dict[StableToken, tuple[int, ...]]
    indexed_states_by_program: dict[StableToken, tuple[str, ...]]
    shared_controller_physical_cell_ids: tuple[StableToken, ...]
    shared_index_movement_ids: tuple[StableToken, ...]
    request_foes_rows: tuple[dict[str, Any], ...]
    crossing_signature: StableToken
    review_position_xy: tuple[float, float] | None
    primary_class: ControlledPedestrianBindingClass
    secondary_flags: tuple[str, ...]
    hard_structural_error: bool
    rejection_reasons: tuple[str, ...]

    @model_validator(mode="after")
    def validate_assessment(self) -> ControlledPedestrianBindingAssessment:
        require_stable_id(self.assessment_id, kind="finding")
        require_stable_id(self.inventory_id, kind="manifest")
        require_stable_id(self.binding_entity_id, kind="binding")
        require_stable_id(self.movement_id, kind="movement")
        require_stable_id(self.crossing_signature, kind="signature")
        for cell_id in (
            *self.owner_physical_cell_ids,
            *self.shared_controller_physical_cell_ids,
        ):
            require_stable_id(cell_id, kind="cell")
        for evidence_id in self.program_evidence_ids:
            require_stable_id(evidence_id, kind="evidence")
        for movement_id in self.shared_index_movement_ids:
            require_stable_id(movement_id, kind="movement")
        if set(self.program_evidence_ids) != set(
            self.phase_state_lengths_by_program
        ) or set(self.program_evidence_ids) != set(
            self.indexed_states_by_program
        ):
            raise ValueError(
                "Every referenced TLS program must have phase-length and indexed-state evidence."
            )
        expected_hard = self.primary_class in {
            "ordinary-program-truly-absent",
            "program-present-link-invalid",
        }
        if self.hard_structural_error != expected_hard:
            raise ValueError(
                "Only ordinary missing programs and invalid links are hard structural errors."
            )
        if not self.rejection_reasons:
            raise ValueError(
                "Controlled pedestrian binding assessments require reasons."
            )
        expected_id = stable_id(
            "finding",
            {
                "inventory_id": self.inventory_id,
                "binding_entity_id": self.binding_entity_id,
                "movement_id": self.movement_id,
                "primary_class": self.primary_class,
            },
        )
        if self.assessment_id != expected_id:
            raise ValueError(
                "Controlled pedestrian assessment ID does not match its identity."
            )
        return self


class ControlledPedestrianBindingCensus(ContractModel):
    schema_id: str = (
        "torii.corridor.controlled-pedestrian-binding-census/v1"
    )
    census_id: StableToken
    inventory_id: StableToken
    source_net_sha256: Sha256
    unresolved_binding_count: int
    class_counts: dict[ControlledPedestrianBindingClass, int]
    ambiguous_fraction: float
    classification_signature: StableToken
    assessments: tuple[ControlledPedestrianBindingAssessment, ...]
    automatic_promotion_gate: GateStatus = GateStatus.BLOCKED

    @model_validator(mode="after")
    def validate_census(self) -> ControlledPedestrianBindingCensus:
        require_stable_id(self.census_id, kind="manifest")
        require_stable_id(self.inventory_id, kind="manifest")
        require_stable_id(
            self.classification_signature,
            kind="signature",
        )
        if self.automatic_promotion_gate is not GateStatus.BLOCKED:
            raise ValueError(
                "PCB census artifacts cannot authorize automatic promotion."
            )
        if self.unresolved_binding_count != len(self.assessments):
            raise ValueError(
                "PCB census count must match exact assessment records."
            )
        assessment_ids = [
            assessment.assessment_id for assessment in self.assessments
        ]
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ValueError("PCB assessment IDs must be unique.")
        expected_counts = dict(
            sorted(Counter(a.primary_class for a in self.assessments).items())
        )
        if self.class_counts != expected_counts:
            raise ValueError("PCB class counts do not match assessments.")
        ambiguous_count = expected_counts.get(
            "stale-or-ambiguous-control-reference",
            0,
        )
        expected_fraction = (
            ambiguous_count / self.unresolved_binding_count
            if self.unresolved_binding_count
            else 0.0
        )
        if abs(self.ambiguous_fraction - expected_fraction) > 1e-12:
            raise ValueError(
                "PCB ambiguous fraction does not match assessments."
            )
        expected_signature = stable_id(
            "signature",
            [
                assessment.model_dump(mode="json", by_alias=True)
                for assessment in self.assessments
            ],
        )
        if self.classification_signature != expected_signature:
            raise ValueError(
                "PCB classification signature does not match assessments."
            )
        expected_census_id = stable_id(
            "manifest",
            {
                "inventory_id": self.inventory_id,
                "source_net_sha256": self.source_net_sha256,
                "classification_signature": self.classification_signature,
            },
        )
        if self.census_id != expected_census_id:
            raise ValueError("PCB census ID does not match its contents.")
        return self


def build_effective_tls_program_inventory(
    net_file: Path,
    *,
    sumocfg_file: Path | None = None,
    additional_files: Sequence[Path] = (),
) -> EffectiveTLSProgramInventory:
    net_path = net_file.resolve(strict=True)
    net_sha256 = _file_sha256(net_path)
    config_path = (
        sumocfg_file.resolve(strict=True)
        if sumocfg_file is not None
        else None
    )
    config_sha256 = _file_sha256(config_path) if config_path else None
    discovered_additional: list[Path] = []
    unresolved_additional: list[str] = []
    dynamic_configuration = False
    if config_path is not None:
        config_root = ET.parse(config_path).getroot()
        dynamic_configuration = _contains_dynamic_program_configuration(
            config_root
        )
        for raw_path in _sumocfg_additional_values(config_root):
            candidate = (config_path.parent / raw_path).resolve()
            if candidate.is_file():
                discovered_additional.append(candidate)
            else:
                unresolved_additional.append(str(candidate))
    for path in additional_files:
        candidate = path.resolve()
        if candidate.is_file():
            discovered_additional.append(candidate)
        else:
            unresolved_additional.append(str(candidate))
    effective_additional = tuple(
        sorted(set(discovered_additional), key=lambda path: path.as_posix())
    )
    program_evidence: list[EffectiveTLSProgramEvidence] = []
    net_root = ET.parse(net_path).getroot()
    dynamic_configuration = (
        dynamic_configuration
        or _contains_dynamic_program_configuration(net_root)
    )
    program_evidence.extend(
        _program_evidence_from_root(
            net_root,
            source_path=net_path,
            source_sha256=net_sha256,
            source_kind="embedded-net",
        )
    )
    additional_sha256: dict[str, str] = {}
    for path in effective_additional:
        digest = _file_sha256(path)
        additional_sha256[str(path)] = digest
        root = ET.parse(path).getroot()
        dynamic_configuration = (
            dynamic_configuration
            or _contains_dynamic_program_configuration(root)
        )
        program_evidence.extend(
            _program_evidence_from_root(
                root,
                source_path=path,
                source_sha256=digest,
                source_kind="additional-file",
            )
        )
    programs = tuple(
        sorted(
            program_evidence,
            key=lambda item: item.program_evidence_id,
        )
    )
    identity_payload = {
        "net_sha256": net_sha256,
        "sumocfg_sha256": config_sha256,
        "effective_additional_sha256": additional_sha256,
        "unresolved_additional_paths": tuple(
            sorted(set(unresolved_additional))
        ),
        "dynamic_program_configuration_detected": dynamic_configuration,
        "program_evidence_ids": [
            program.program_evidence_id for program in programs
        ],
    }
    return EffectiveTLSProgramInventory(
        inventory_id=stable_id("manifest", identity_payload),
        net_path=str(net_path),
        net_sha256=net_sha256,
        sumocfg_path=str(config_path) if config_path else None,
        sumocfg_sha256=config_sha256,
        effective_additional_paths=tuple(
            str(path) for path in effective_additional
        ),
        effective_additional_sha256=additional_sha256,
        unresolved_additional_paths=tuple(
            sorted(set(unresolved_additional))
        ),
        dynamic_program_configuration_detected=dynamic_configuration,
        programs=programs,
    )


def classify_controlled_pedestrian_bindings(
    snapshot: CanonicalNetworkSnapshot,
    inventory: EffectiveTLSProgramInventory,
) -> ControlledPedestrianBindingCensus:
    if (
        snapshot.source_sha256 is not None
        and snapshot.source_sha256 != inventory.net_sha256
    ):
        raise ValueError(
            "PCB inventory net hash does not match the canonical snapshot."
        )
    entities = snapshot.entity_index()
    signal_group_movement_ids = {
        str(movement_id)
        for entity in snapshot.entities
        if entity.kind == "signal_group"
        for movement_id in entity.payload.get("movement_ids", ())
    }
    bindings = [
        entity
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
        and entity.payload.get("control_kind") == "signalized"
        and str(entity.payload.get("movement_id", ""))
        not in signal_group_movement_ids
    ]
    programs_by_controller: dict[
        str,
        list[EffectiveTLSProgramEvidence],
    ] = defaultdict(list)
    for program in inventory.programs:
        programs_by_controller[program.raw_controller_id].append(program)
    raw_controller_cells: dict[str, set[str]] = defaultdict(set)
    for binding in (
        entity
        for entity in snapshot.entities
        if entity.kind == "pedestrian_control_binding"
    ):
        for raw_controller_id in binding.payload.get(
            "raw_controller_ids",
            (),
        ):
            raw_controller_cells[str(raw_controller_id)].update(
                binding.owner_physical_cell_ids
            )
    raw_controller_to_stable = snapshot.raw_id_maps.get(
        "tls_to_controller",
        {},
    )
    connection_xml_by_movement = _raw_connection_xml_by_movement(
        snapshot,
        Path(inventory.net_path),
    )
    assessments = tuple(
        _classify_binding(
            binding,
            entities=entities,
            programs_by_controller=programs_by_controller,
            raw_controller_cells=raw_controller_cells,
            raw_controller_to_stable=raw_controller_to_stable,
            connection_xml_by_movement=connection_xml_by_movement,
            inventory=inventory,
        )
        for binding in sorted(
            bindings,
            key=lambda item: item.stable_entity_id,
        )
    )
    class_counts = dict(
        sorted(Counter(item.primary_class for item in assessments).items())
    )
    ambiguous_fraction = (
        class_counts.get("stale-or-ambiguous-control-reference", 0)
        / len(assessments)
        if assessments
        else 0.0
    )
    classification_signature = stable_id(
        "signature",
        [
            assessment.model_dump(mode="json", by_alias=True)
            for assessment in assessments
        ],
    )
    census_identity = {
        "inventory_id": inventory.inventory_id,
        "source_net_sha256": inventory.net_sha256,
        "classification_signature": classification_signature,
    }
    return ControlledPedestrianBindingCensus(
        census_id=stable_id("manifest", census_identity),
        inventory_id=inventory.inventory_id,
        source_net_sha256=inventory.net_sha256,
        unresolved_binding_count=len(assessments),
        class_counts=class_counts,
        ambiguous_fraction=ambiguous_fraction,
        classification_signature=classification_signature,
        assessments=assessments,
        automatic_promotion_gate=GateStatus.BLOCKED,
    )


def _classify_binding(
    binding: CanonicalEntity,
    *,
    entities: dict[tuple[str, str], CanonicalEntity],
    programs_by_controller: dict[str, list[EffectiveTLSProgramEvidence]],
    raw_controller_cells: dict[str, set[str]],
    raw_controller_to_stable: dict[str, StableToken],
    connection_xml_by_movement: dict[str, tuple[str, ...]],
    inventory: EffectiveTLSProgramInventory,
) -> ControlledPedestrianBindingAssessment:
    movement_id = str(binding.payload["movement_id"])
    raw_controller_ids = tuple(
        sorted(map(str, binding.payload.get("raw_controller_ids", ())))
    )
    link_indices = tuple(
        sorted(
            {
                int(index)
                for index in binding.payload.get("source_link_indices", ())
            }
        )
    )
    owner_cell_ids = tuple(sorted(binding.owner_physical_cell_ids))
    owner_junction_types = tuple(
        sorted(
            {
                str(
                    entities[("physical_cell", cell_id)].payload.get(
                        "junction_type",
                        "",
                    )
                )
                for cell_id in owner_cell_ids
                if ("physical_cell", cell_id) in entities
            }
            or set(
                map(str, binding.payload.get("owner_junction_types", ()))
            )
        )
    )
    programs = tuple(
        sorted(
            (
                program
                for controller_id in raw_controller_ids
                for program in programs_by_controller.get(
                    controller_id,
                    (),
                )
            ),
            key=lambda item: item.program_evidence_id,
        )
    )
    shared_cells = tuple(
        sorted(
            {
                cell_id
                for controller_id in raw_controller_ids
                for cell_id in raw_controller_cells.get(
                    controller_id,
                    (),
                )
            }
        )
    )
    linked_states: dict[str, tuple[str, ...]] = {}
    phase_lengths: dict[str, tuple[int, ...]] = {}
    invalid_link = bool(binding.payload.get("link_index2_present", False))
    for program in programs:
        phase_lengths[program.program_evidence_id] = (
            program.phase_state_lengths
        )
        indexed_states: list[str] = []
        if not program.phase_states or len(
            set(program.phase_state_lengths)
        ) > 1:
            invalid_link = True
        for state in program.phase_states:
            for index in link_indices:
                if index < 0 or index >= len(state):
                    indexed_states.append("?")
                    invalid_link = True
                else:
                    indexed_states.append(state[index])
        linked_states[program.program_evidence_id] = tuple(indexed_states)
    program_sources = tuple(
        sorted({program.source_kind for program in programs})
    )
    controller_types = {program.controller_type for program in programs}
    unsupported_program = (
        inventory.dynamic_program_configuration_detected
        or len(programs) > 1
        or any(
            controller_type not in _SUPPORTED_EMBEDDED_PROGRAM_TYPES
            for controller_type in controller_types
        )
    )
    runtime_special = bool(
        set(owner_junction_types) & _RUNTIME_SPECIAL_JUNCTION_TYPES
    )
    reasons: list[str] = []
    secondary_flags: list[str] = []
    if runtime_special:
        primary_class = "runtime-special-controller"
        reasons.append("owner_junction_uses_runtime_special_control")
        if not programs:
            secondary_flags.append("embedded_program_not_required")
        if invalid_link:
            secondary_flags.append("link_binding_requires_special_oracle")
    elif len(raw_controller_ids) != 1:
        primary_class = "stale-or-ambiguous-control-reference"
        reasons.append("raw_controller_identity_not_unique")
    elif inventory.unresolved_additional_paths and not programs:
        primary_class = "unsupported-program-form"
        reasons.append("effective_program_inventory_incomplete")
    elif not programs:
        primary_class = "ordinary-program-truly-absent"
        reasons.append("no_effective_program_found_for_ordinary_controller")
    elif invalid_link:
        primary_class = "program-present-link-invalid"
        reasons.append("link_index_or_phase_state_length_invalid")
    elif "additional-file" in program_sources:
        primary_class = "external-program-present"
        reasons.append("effective_program_loaded_from_additional_file")
    elif len(shared_cells) > 1:
        primary_class = "shared-controller-scope-incomplete"
        reasons.append("controller_spans_multiple_physical_cells")
    elif unsupported_program:
        primary_class = "unsupported-program-form"
        reasons.append("program_form_outside_current_canonical_model")
    else:
        primary_class = "stale-or-ambiguous-control-reference"
        reasons.append("program_present_but_effective_signal_group_unresolved")
    if binding.payload.get("multiple_source_indices", False):
        secondary_flags.append("multiple_source_indices")
    if binding.payload.get("link_index2_present", False):
        secondary_flags.append("link_index2_present")
    shared_index_movements = _shared_index_movement_ids(
        snapshot_entities=entities,
        raw_controller_ids=raw_controller_ids,
        raw_controller_to_stable=raw_controller_to_stable,
        link_indices=link_indices,
    )
    request_foes_rows = tuple(
        {
            "physical_cell_id": cell_id,
            "request_rows": tuple(
                entities[("physical_cell", cell_id)].payload.get(
                    "requests",
                    (),
                )
            ),
        }
        for cell_id in owner_cell_ids
        if ("physical_cell", cell_id) in entities
    )
    movement = entities.get(("movement", movement_id))
    crossing_signature = stable_id(
        "signature",
        {
            "movement_id": movement_id,
            "movement_payload": movement.payload if movement else {},
        },
    )
    positions = [
        entities[("physical_cell", cell_id)].payload.get("position_xy")
        for cell_id in owner_cell_ids
        if ("physical_cell", cell_id) in entities
    ]
    finite_positions = [
        (float(position[0]), float(position[1]))
        for position in positions
        if isinstance(position, (list, tuple)) and len(position) == 2
    ]
    review_position = (
        (
            round(
                sum(position[0] for position in finite_positions)
                / len(finite_positions),
                6,
            ),
            round(
                sum(position[1] for position in finite_positions)
                / len(finite_positions),
                6,
            ),
        )
        if finite_positions
        else None
    )
    assessment_identity = {
        "inventory_id": inventory.inventory_id,
        "binding_entity_id": binding.stable_entity_id,
        "movement_id": movement_id,
        "primary_class": primary_class,
    }
    return ControlledPedestrianBindingAssessment(
        assessment_id=stable_id("finding", assessment_identity),
        inventory_id=inventory.inventory_id,
        binding_entity_id=binding.stable_entity_id,
        movement_id=movement_id,
        owner_physical_cell_ids=owner_cell_ids,
        owner_junction_types=owner_junction_types,
        raw_controller_ids=raw_controller_ids,
        link_indices=link_indices,
        link_index2_present=bool(
            binding.payload.get("link_index2_present", False)
        ),
        raw_connection_xml=connection_xml_by_movement.get(movement_id, ()),
        program_evidence_ids=tuple(
            program.program_evidence_id for program in programs
        ),
        program_sources=program_sources,
        phase_state_lengths_by_program=phase_lengths,
        indexed_states_by_program=linked_states,
        shared_controller_physical_cell_ids=shared_cells,
        shared_index_movement_ids=shared_index_movements,
        request_foes_rows=request_foes_rows,
        crossing_signature=crossing_signature,
        review_position_xy=review_position,
        primary_class=primary_class,
        secondary_flags=tuple(sorted(set(secondary_flags))),
        hard_structural_error=primary_class
        in {
            "ordinary-program-truly-absent",
            "program-present-link-invalid",
        },
        rejection_reasons=tuple(reasons),
    )


def _shared_index_movement_ids(
    *,
    snapshot_entities: dict[tuple[str, str], CanonicalEntity],
    raw_controller_ids: tuple[str, ...],
    raw_controller_to_stable: dict[str, StableToken],
    link_indices: tuple[int, ...],
) -> tuple[str, ...]:
    stable_controller_ids = {
        str(raw_controller_to_stable[controller_id])
        for controller_id in raw_controller_ids
        if controller_id in raw_controller_to_stable
    }
    if not stable_controller_ids or not link_indices:
        return ()
    return tuple(
        sorted(
            {
                str(movement_id)
                for entity in snapshot_entities.values()
                if entity.kind == "signal_group"
                and str(entity.payload.get("controller_id", ""))
                in stable_controller_ids
                and set(
                    map(
                        int,
                        entity.payload.get("source_link_indices", ()),
                    )
                )
                & set(link_indices)
                for movement_id in entity.payload.get("movement_ids", ())
            }
        )
    )


def _program_evidence_from_root(
    root: ET.Element,
    *,
    source_path: Path,
    source_sha256: str,
    source_kind: ProgramSourceKind,
) -> list[EffectiveTLSProgramEvidence]:
    records: list[EffectiveTLSProgramEvidence] = []
    for logic in root.iter("tlLogic"):
        raw_controller_id = logic.attrib.get("id", "").strip()
        if not raw_controller_id:
            continue
        phase_states = tuple(
            phase.attrib.get("state", "")
            for phase in logic.findall("phase")
        )
        identity = {
            "raw_controller_id": raw_controller_id,
            "source_kind": source_kind,
            "source_sha256": source_sha256,
            "source_program_id": logic.attrib.get("programID", ""),
            "controller_type": logic.attrib.get("type", ""),
            "phase_states": phase_states,
        }
        records.append(
            EffectiveTLSProgramEvidence(
                program_evidence_id=stable_id("evidence", identity),
                raw_controller_id=raw_controller_id,
                source_kind=source_kind,
                source_path=str(source_path),
                source_sha256=source_sha256,
                source_program_id=logic.attrib.get("programID", ""),
                controller_type=logic.attrib.get("type", ""),
                phase_states=phase_states,
                phase_state_lengths=tuple(
                    len(state) for state in phase_states
                ),
            )
        )
    return records


def _sumocfg_additional_values(root: ET.Element) -> tuple[str, ...]:
    values: list[str] = []
    for element in root.findall(".//additional-files"):
        raw_value = element.attrib.get("value", "")
        values.extend(
            value.strip()
            for value in raw_value.replace(";", ",").split(",")
            if value.strip()
        )
    return tuple(values)


def _contains_dynamic_program_configuration(root: ET.Element) -> bool:
    dynamic_tags = {
        "waut",
        "wautjunction",
        "timedevent",
        "trafficsignalprogram",
    }
    return any(
        element.tag.rsplit("}", 1)[-1].lower() in dynamic_tags
        for element in root.iter()
    )


def _raw_connection_xml_by_movement(
    snapshot: CanonicalNetworkSnapshot,
    net_path: Path,
) -> dict[str, tuple[str, ...]]:
    root = ET.parse(net_path).getroot()
    connections = list(root.findall("connection"))
    records: dict[str, list[str]] = defaultdict(list)
    connection_map = snapshot.raw_id_maps.get(
        "connection_index_to_movement",
        {},
    )
    for raw_index, movement_id in connection_map.items():
        try:
            index = int(raw_index)
        except ValueError:
            continue
        if 0 <= index < len(connections):
            records[str(movement_id)].append(
                ET.tostring(
                    connections[index],
                    encoding="unicode",
                    short_empty_elements=True,
                ).strip()
            )
    return {
        movement_id: tuple(values)
        for movement_id, values in records.items()
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
