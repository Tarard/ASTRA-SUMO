"""Composable, Germany-first descriptions of physical traffic-signal devices.

This module models what a signal head can display, whom it serves, where it is
installed, and what road entities it applies to.  It deliberately does not put
controller strategy, timing programs, phases, or a live signal state inside the
device identity.  Those changing concerns have their own standalone contracts
below.

The default jurisdiction profile names the German StVO/RiLSA/DIN EN 12368
standards family.  Country and native type identifiers remain explicit so the
same finite, composable vocabulary can carry other European national profiles
without pretending that visual and legal meanings are jurisdiction-neutral.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SIGNAL_DEVICE_PROFILE_SCHEMA = "torii.signal-device-profile/v1"
SIGNAL_RUNTIME_STATE_SCHEMA = "torii.signal-runtime-state/v1"
SIGNAL_CONTROL_METHOD_SCHEMA = "torii.signal-control-method/v1"


class SignalContractModel(BaseModel):
    """Strict and immutable base for signal contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        serialize_by_alias=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class ServedUser(StrEnum):
    MOTOR_VEHICLE = "motor_vehicle"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"
    ACCESSIBILITY = "accessibility"
    TRAM = "tram"
    BUS = "bus"
    PUBLIC_TRANSPORT = "public_transport"
    EMERGENCY_VEHICLE = "emergency_vehicle"
    EQUESTRIAN = "equestrian"
    LANE_USER = "lane_user"
    OTHER = "other"
    UNKNOWN = "unknown"


class DisplayColor(StrEnum):
    RED = "red"
    AMBER = "amber"
    YELLOW = "yellow"
    GREEN = "green"
    WHITE = "white"
    BLUE = "blue"
    OTHER = "other"
    UNKNOWN = "unknown"


class DisplaySymbol(StrEnum):
    CIRCLE = "circle"
    ARROW = "arrow"
    PEDESTRIAN = "pedestrian"
    BICYCLE = "bicycle"
    BAR = "bar"
    POINT = "point"
    TRIANGLE = "triangle"
    CROSS = "cross"
    LANE_ARROW = "lane_arrow"
    COUNTDOWN = "countdown"
    LETTER = "letter"
    TEXT = "text"
    OTHER = "other"
    UNKNOWN = "unknown"


class DisplayDirection(StrEnum):
    NONE = "none"
    LEFT = "left"
    RIGHT = "right"
    STRAIGHT = "straight"
    U_TURN = "u_turn"
    DOWN = "down"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    DIAGONAL_LEFT = "diagonal_left"
    DIAGONAL_RIGHT = "diagonal_right"
    BIDIRECTIONAL = "bidirectional"
    OTHER = "other"
    UNKNOWN = "unknown"


class DisplayBehavior(StrEnum):
    STEADY = "steady"
    FLASHING = "flashing"
    STEADY_OR_FLASHING = "steady_or_flashing"
    PULSING = "pulsing"
    ALTERNATING = "alternating"
    VARIABLE = "variable"
    OTHER = "other"
    UNKNOWN = "unknown"


class OutputModality(StrEnum):
    VISUAL = "visual"
    AUDIBLE = "audible"
    TACTILE = "tactile"
    AUDIBLE_TACTILE = "audible_tactile"
    UNKNOWN = "unknown"


class NonVisualPattern(StrEnum):
    CONTINUOUS = "continuous"
    PULSED = "pulsed"
    TICKING = "ticking"
    SPEECH = "speech"
    VIBRATION = "vibration"
    ROTATING_CONE = "rotating_cone"
    OTHER = "other"
    UNKNOWN = "unknown"


class DisplayMeaning(StrEnum):
    STOP = "stop"
    PREPARE = "prepare"
    PROCEED = "proceed"
    PROCEED_IF_CLEAR = "proceed_if_clear"
    WAIT = "wait"
    LANE_CLOSED = "lane_closed"
    LANE_OPEN = "lane_open"
    DIRECTION_CLEAR = "direction_clear"
    ATTENTION = "attention"
    OTHER = "other"
    UNKNOWN = "unknown"


class FaceArrangement(StrEnum):
    SINGLE = "single"
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"
    CLUSTER = "cluster"
    MATRIX = "matrix"
    OTHER = "other"
    UNKNOWN = "unknown"


class PhysicalRole(StrEnum):
    MAIN = "main"
    REPEATED = "repeated"
    OVERHEAD = "overhead"
    AUXILIARY = "auxiliary"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    DUPLICATE = "duplicate"
    REPEATER = "repeater"
    SUPPLEMENTARY = "supplementary"
    OTHER = "other"
    UNKNOWN = "unknown"


class LongitudinalPlacement(StrEnum):
    NEAR_SIDE = "near_side"
    FAR_SIDE = "far_side"
    AT_STOP_LINE = "at_stop_line"
    DOWNSTREAM = "downstream"
    NOT_APPLICABLE = "not_applicable"
    OTHER = "other"
    UNKNOWN = "unknown"


class Mounting(StrEnum):
    ROADSIDE = "roadside"
    MEDIAN = "median"
    MAST_ARM = "mast_arm"
    OVERHEAD_PORTAL = "overhead_portal"
    SUSPENDED = "suspended"
    TRACKSIDE = "trackside"
    SURFACE = "surface"
    OTHER = "other"
    UNKNOWN = "unknown"


class TargetKind(StrEnum):
    APPROACH = "approach"
    LANE = "lane"
    MOVEMENT = "movement"
    CROSSING = "crossing"
    STOP_LINE = "stop_line"
    TRACK = "track"
    CORRIDOR = "corridor"
    OTHER = "other"
    UNKNOWN = "unknown"


class ControlReferenceSystem(StrEnum):
    FIELD_CONTROLLER = "field_controller"
    OCIT = "ocit"
    ETSI_ITS = "etsi_its"
    OPENDRIVE = "opendrive"
    LANELET2 = "lanelet2"
    SUMO = "sumo"
    OTHER = "other"


class EvidenceSourceKind(StrEnum):
    OFFICIAL_STANDARD = "official_standard"
    OFFICIAL_INVENTORY = "official_inventory"
    OPERATOR_EXPORT = "operator_export"
    FIELD_OBSERVATION = "field_observation"
    MAP_IMAGE = "map_image"
    OSM = "osm"
    SUMO = "sumo"
    INFERENCE = "inference"
    OTHER = "other"
    UNKNOWN = "unknown"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    UNKNOWN = "unknown"


class AssessmentStatus(StrEnum):
    PASS = "pass"
    REVIEW_REQUIRED = "review_required"


class ControlMethod(StrEnum):
    FIXED_TIME = "fixed_time"
    TRAFFIC_ACTUATED = "traffic_actuated"
    ADAPTIVE = "adaptive"
    TRANSIT_PRIORITY = "transit_priority"
    MANUAL = "manual"
    OTHER = "other"
    UNKNOWN = "unknown"


def _ordered_unique(values: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return deterministic set semantics while retaining tuple JSON output."""

    return tuple(sorted(set(values), key=lambda value: str(value)))


class JurisdictionIdentity(SignalContractModel):
    """Profile capability plus explicit, non-inferred source metadata.

    ``profile_capability_references`` describes what the Torii vocabulary can
    represent.  It is not evidence that a source instance conforms to those
    documents.  Any such assertion must be supplied separately in
    ``source_claimed_references`` and backed by an :class:`EvidenceRecord`.
    """

    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")
    profile_id: str = "de_stvo_rilsa"
    profile_capability_references: tuple[str, ...] = (
        "DIN EN 12368",
        "RiLSA",
        "StVO § 37",
    )
    source_claimed_references: tuple[str, ...] = ()
    source_native_type_id: str | None = None
    source_native_subtype_id: str | None = None

    @field_validator("country_code", mode="before")
    @classmethod
    def normalize_country_code(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("profile_capability_references", "source_claimed_references")
    @classmethod
    def normalize_reference_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_unique(value)


class DisplayUnit(SignalContractModel):
    """One display capability, not an observation that it is currently lit."""

    unit_id: str = Field(min_length=1)
    section_index: int = Field(ge=0)
    color: DisplayColor
    symbol: DisplaySymbol
    direction: DisplayDirection = DisplayDirection.NONE
    behavior: DisplayBehavior = DisplayBehavior.STEADY
    meaning: DisplayMeaning = DisplayMeaning.UNKNOWN
    native_code: str | None = None


class NonVisualOutput(SignalContractModel):
    """Audible/tactile capability without inventing a visual color.

    ``source_channel_id`` can preserve a source-native slot name such as the
    Hamburg OCIT ``Gruen`` channel even when its actual payload is ``Ton`` or
    ``Vibra``.  The channel name therefore never implies ``DisplayColor.GREEN``.
    """

    output_id: str = Field(min_length=1)
    modality: OutputModality
    pattern: NonVisualPattern
    served_users: tuple[ServedUser, ...]
    source_channel_id: str | None = None
    native_code: str | None = None

    @model_validator(mode="after")
    def validate_non_visual_modality(self) -> NonVisualOutput:
        if self.modality is OutputModality.VISUAL:
            raise ValueError("NonVisualOutput modality cannot be visual")
        if not self.served_users:
            raise ValueError("non-visual output must serve at least one user")
        object.__setattr__(self, "served_users", _ordered_unique(self.served_users))
        return self


class FaceLayout(SignalContractModel):
    section_count: int = Field(ge=1)
    arrangement: FaceArrangement
    section_order: tuple[int, ...] = ()
    shared_or_bimodal_sections: bool = False

    @model_validator(mode="after")
    def validate_section_order(self) -> FaceLayout:
        if self.section_order and set(self.section_order) != set(range(self.section_count)):
            raise ValueError("section_order must be a permutation of all section indices")
        return self


class SignalDeviceIdentity(SignalContractModel):
    """Stable physical/display identity, excluding control and runtime state."""

    forbidden_control_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "control_method",
            "control_strategy",
            "controller_type",
            "current_state",
            "phase",
            "phase_id",
            "program",
            "program_id",
            "runtime_state",
            "signal_state",
            "state",
            "timing_plan",
        }
    )

    jurisdiction: JurisdictionIdentity = Field(default_factory=JurisdictionIdentity)
    served_users: tuple[ServedUser, ...]
    modalities: tuple[OutputModality, ...] = (OutputModality.VISUAL,)
    display_units: tuple[DisplayUnit, ...] = ()
    non_visual_outputs: tuple[NonVisualOutput, ...] = ()
    face_layout: FaceLayout | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_control_and_runtime_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            collisions = sorted(cls.forbidden_control_fields.intersection(value))
            if collisions:
                joined = ", ".join(collisions)
                raise ValueError(f"device identity cannot contain control/runtime fields: {joined}")
        return value

    @field_validator("served_users")
    @classmethod
    def normalize_served_users(cls, value: tuple[ServedUser, ...]) -> tuple[ServedUser, ...]:
        if not value:
            raise ValueError("served_users must not be empty")
        return _ordered_unique(value)

    @field_validator("modalities")
    @classmethod
    def normalize_modalities(cls, value: tuple[OutputModality, ...]) -> tuple[OutputModality, ...]:
        if not value:
            raise ValueError("modalities must not be empty")
        return _ordered_unique(value)

    @field_validator("display_units")
    @classmethod
    def normalize_display_units(cls, value: tuple[DisplayUnit, ...]) -> tuple[DisplayUnit, ...]:
        if len({unit.unit_id for unit in value}) != len(value):
            raise ValueError("display unit IDs must be unique within a device")
        return tuple(sorted(value, key=lambda unit: (unit.section_index, unit.unit_id)))

    @field_validator("non_visual_outputs")
    @classmethod
    def normalize_non_visual_outputs(
        cls,
        value: tuple[NonVisualOutput, ...],
    ) -> tuple[NonVisualOutput, ...]:
        if len({output.output_id for output in value}) != len(value):
            raise ValueError("non-visual output IDs must be unique within a device")
        return tuple(sorted(value, key=lambda output: output.output_id))

    @model_validator(mode="after")
    def validate_display_sections(self) -> SignalDeviceIdentity:
        if not self.display_units and not self.non_visual_outputs:
            raise ValueError("device identity must contain at least one visual or non-visual output")
        if self.display_units and self.face_layout is None:
            raise ValueError("visual display units require a face_layout")
        if not self.display_units and self.face_layout is not None:
            raise ValueError("face_layout cannot be set for a non-visual-only device")
        if bool(self.display_units) != (OutputModality.VISUAL in self.modalities):
            raise ValueError("visual modality and display_units must be declared together")
        declared_non_visual = {output.modality for output in self.non_visual_outputs}
        expected_modalities = set(declared_non_visual)
        if self.display_units:
            expected_modalities.add(OutputModality.VISUAL)
        if set(self.modalities) != expected_modalities:
            raise ValueError("modalities must exactly match the declared visual and non-visual outputs")
        if any(
            user not in self.served_users
            for output in self.non_visual_outputs
            for user in output.served_users
        ):
            raise ValueError("non-visual output user must be included in identity.served_users")
        if not self.display_units:
            return self
        assert self.face_layout is not None
        indices = [unit.section_index for unit in self.display_units]
        if max(indices) >= self.face_layout.section_count:
            raise ValueError("display unit section_index exceeds face_layout.section_count")
        if len(indices) != len(set(indices)) and not self.face_layout.shared_or_bimodal_sections:
            raise ValueError("multiple display units in one section require shared_or_bimodal_sections=true")
        return self


class Installation(SignalContractModel):
    physical_role: PhysicalRole
    longitudinal_placement: LongitudinalPlacement
    mounting: Mounting
    orientation_degrees: float | None = Field(default=None, ge=0.0, lt=360.0)
    native_location_code: str | None = None


class TargetBinding(SignalContractModel):
    binding_id: str = Field(min_length=1)
    target_kind: TargetKind
    target_ids: tuple[str, ...]
    served_users: tuple[ServedUser, ...] = ()
    directions: tuple[DisplayDirection, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("target_ids")
    @classmethod
    def normalize_target_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = _ordered_unique(value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("target_ids must contain at least one non-empty ID")
        return normalized

    @field_validator("served_users", "directions")
    @classmethod
    def normalize_set_like_fields(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        return _ordered_unique(value)


class ControlReference(SignalContractModel):
    """External identity binding only; no algorithm, phase, timing, or live state."""

    reference_id: str = Field(min_length=1)
    system: ControlReferenceSystem
    controller_id: str | None = None
    signal_group_id: str | None = None
    sumo_tl_id: str | None = None
    sumo_link_indices: tuple[int, ...] = ()

    @field_validator("sumo_link_indices")
    @classmethod
    def normalize_link_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("sumo_link_indices must be non-negative")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def require_external_identifier(self) -> ControlReference:
        if not any((self.controller_id, self.signal_group_id, self.sumo_tl_id, self.sumo_link_indices)):
            raise ValueError("control reference must identify a controller, signal group, or SUMO link")
        return self


class EvidenceRecord(SignalContractModel):
    evidence_id: str = Field(min_length=1)
    source_kind: EvidenceSourceKind
    source_ref: str = Field(min_length=1)
    observed_fact: str = Field(min_length=1)
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS
    confidence: float = Field(ge=0.0, le=1.0)
    observed_at: str | None = None
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    supports_fields: tuple[str, ...] = ()

    @field_validator("supports_fields")
    @classmethod
    def normalize_supports_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_unique(value)


class ProfileAssessment(SignalContractModel):
    status: AssessmentStatus
    review_reasons: tuple[str, ...] = ()

    @field_validator("review_reasons")
    @classmethod
    def normalize_review_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_unique(value)


class SignalDeviceProfile(SignalContractModel):
    """Canonical profile for one physical signal head/device instance."""

    schema_id: Literal["torii.signal-device-profile/v1"] = Field(
        default=SIGNAL_DEVICE_PROFILE_SCHEMA,
        alias="schema",
    )
    profile_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    identity: SignalDeviceIdentity
    installation: Installation
    target_bindings: tuple[TargetBinding, ...] = ()
    control_references: tuple[ControlReference, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    assessment: ProfileAssessment
    derived_alias: str = Field(
        default="",
        description="Deterministic human-readable alias derived from device identity.",
        json_schema_extra={"readOnly": True},
    )

    @field_validator("target_bindings")
    @classmethod
    def normalize_target_bindings(cls, value: tuple[TargetBinding, ...]) -> tuple[TargetBinding, ...]:
        if len({binding.binding_id for binding in value}) != len(value):
            raise ValueError("target binding IDs must be unique within a profile")
        return tuple(sorted(value, key=lambda item: item.binding_id))

    @field_validator("control_references")
    @classmethod
    def normalize_control_references(cls, value: tuple[ControlReference, ...]) -> tuple[ControlReference, ...]:
        if len({reference.reference_id for reference in value}) != len(value):
            raise ValueError("control reference IDs must be unique within a profile")
        return tuple(sorted(value, key=lambda item: item.reference_id))

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, value: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        if len({record.evidence_id for record in value}) != len(value):
            raise ValueError("evidence IDs must be unique within a profile")
        return tuple(sorted(value, key=lambda item: item.evidence_id))

    def _build_derived_alias(self) -> str:
        users = "+".join(user.value for user in self.identity.served_users)
        outputs = [
            "-".join(
                (
                    unit.color.value,
                    unit.symbol.value,
                    unit.direction.value,
                    unit.behavior.value,
                )
            )
            for unit in self.identity.display_units
        ]
        outputs.extend(
            f"{output.modality.value}-{output.pattern.value}"
            for output in self.identity.non_visual_outputs
        )
        country = self.identity.jurisdiction.country_code.lower()
        return f"{country}:{users}:{'+'.join(outputs)}"

    def _automatic_review_reasons(self) -> tuple[str, ...]:
        reasons: set[str] = set()
        identity = self.identity

        if ServedUser.UNKNOWN in identity.served_users:
            reasons.add("identity_unknown_served_user")
        if any(
            unit.color is DisplayColor.UNKNOWN
            or unit.symbol is DisplaySymbol.UNKNOWN
            or unit.direction is DisplayDirection.UNKNOWN
            or unit.behavior is DisplayBehavior.UNKNOWN
            or unit.meaning is DisplayMeaning.UNKNOWN
            for unit in identity.display_units
        ):
            reasons.add("identity_unknown_display")
        if identity.face_layout is not None and identity.face_layout.arrangement is FaceArrangement.UNKNOWN:
            reasons.add("face_layout_unknown")
        if OutputModality.UNKNOWN in identity.modalities:
            reasons.add("identity_unknown_modality")
        if any(output.pattern is NonVisualPattern.UNKNOWN for output in identity.non_visual_outputs):
            reasons.add("identity_unknown_non_visual_pattern")
        if (
            self.installation.physical_role is PhysicalRole.UNKNOWN
            or self.installation.longitudinal_placement is LongitudinalPlacement.UNKNOWN
            or self.installation.mounting is Mounting.UNKNOWN
        ):
            reasons.add("installation_unknown")
        if any(binding.target_kind is TargetKind.UNKNOWN for binding in self.target_bindings):
            reasons.add("target_binding_unknown")
        if any(
            user not in identity.served_users
            for binding in self.target_bindings
            for user in binding.served_users
        ):
            reasons.add("target_binding_user_outside_device_identity")
        if not self.evidence:
            reasons.add("evidence_missing")
        if any(record.source_kind is EvidenceSourceKind.UNKNOWN for record in self.evidence):
            reasons.add("evidence_source_unknown")
        if any(record.relation is EvidenceRelation.CONTRADICTS for record in self.evidence):
            reasons.add("evidence_contradiction")
        if any(record.relation is EvidenceRelation.UNKNOWN for record in self.evidence):
            reasons.add("evidence_relation_unknown")
        claimed_references_supported = any(
            "identity.jurisdiction.source_claimed_references" in record.supports_fields
            and record.relation is EvidenceRelation.SUPPORTS
            for record in self.evidence
        )
        if identity.jurisdiction.source_claimed_references and not claimed_references_supported:
            reasons.add("source_claimed_reference_unsubstantiated")
        return tuple(sorted(reasons))

    @model_validator(mode="after")
    def finalize_alias_and_assessment(self) -> SignalDeviceProfile:
        expected_alias = self._build_derived_alias()
        if self.derived_alias and self.derived_alias != expected_alias:
            raise ValueError("derived_alias does not match the device identity")
        object.__setattr__(self, "derived_alias", expected_alias)

        automatic_reasons = self._automatic_review_reasons()
        declared_reasons = set(self.assessment.review_reasons)
        all_reasons = tuple(sorted(declared_reasons.union(automatic_reasons)))

        if self.assessment.status is AssessmentStatus.PASS and all_reasons:
            joined = ", ".join(all_reasons)
            raise ValueError(f"pass profile has unresolved review reasons: {joined}")
        if self.assessment.status is AssessmentStatus.REVIEW_REQUIRED and not all_reasons:
            raise ValueError("review_required profile must state at least one review reason")
        if all_reasons != self.assessment.review_reasons:
            object.__setattr__(
                self,
                "assessment",
                self.assessment.model_copy(update={"review_reasons": all_reasons}),
            )
        return self

    def canonical_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible representation used for hashing."""

        return json.loads(self.canonical_json())

    def canonical_json(self) -> str:
        """Serialize deterministically without whitespace or ASCII escaping."""

        payload = self.model_dump(mode="json", by_alias=True)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class SignalRuntimeState(SignalContractModel):
    """A time-varying observation kept outside :class:`SignalDeviceProfile`."""

    schema_id: Literal["torii.signal-runtime-state/v1"] = Field(
        default=SIGNAL_RUNTIME_STATE_SCHEMA,
        alias="schema",
    )
    observation_id: str = Field(min_length=1)
    device_id: str = Field(min_length=1)
    observed_at: str = Field(min_length=1)
    active_display_unit_ids: tuple[str, ...] = ()
    source_ref: str = Field(min_length=1)

    @field_validator("active_display_unit_ids")
    @classmethod
    def normalize_active_units(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_unique(value)


class SignalControlMethodProfile(SignalContractModel):
    """Controller algorithm metadata kept outside the physical device identity."""

    schema_id: Literal["torii.signal-control-method/v1"] = Field(
        default=SIGNAL_CONTROL_METHOD_SCHEMA,
        alias="schema",
    )
    controller_id: str = Field(min_length=1)
    method: ControlMethod
    program_id: str | None = None
    source_ref: str = Field(min_length=1)


def build_signal_device_profile_schema() -> dict[str, Any]:
    """Build the checked-in JSON Schema for the public profile artifact."""

    schema = SignalDeviceProfile.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://github.com/Tarard/Torii-SUMO/schemas/"
        "torii.signal-device-profile.v1.schema.json"
    )
    schema["x-torii-status"] = "germany-first-europe-compatible-device-contract"
    return schema


def index_heads_by_signal_group(
    profiles: tuple[SignalDeviceProfile, ...] | list[SignalDeviceProfile],
) -> dict[str, tuple[str, ...]]:
    """Index one signal group to one or more physical head IDs.

    No one-to-one assumption is made: main, repeated, overhead, and auxiliary
    heads may all reference the same group.  Duplicate references from one
    device are collapsed deterministically.
    """

    grouped: dict[str, set[str]] = {}
    for profile in profiles:
        for reference in profile.control_references:
            if reference.signal_group_id:
                grouped.setdefault(reference.signal_group_id, set()).add(profile.device_id)
    return {
        signal_group_id: tuple(sorted(device_ids))
        for signal_group_id, device_ids in sorted(grouped.items())
    }
