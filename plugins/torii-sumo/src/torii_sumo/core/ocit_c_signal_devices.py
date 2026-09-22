"""Read-only OCIT-C projection into Torii's composable signal-device model.

OCIT-C deliberately does not define one universal signal-group type.  This
adapter therefore preserves the source's logical groups, lamp-output slots,
physical signal-generator names, and allowed signal-image codes while keeping
unknown lens symbols, physical roles, placement, and lane/movement bindings
unresolved.  Controller programs and runtime signal states are out of scope.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from torii_sumo.core.signal_device_profile import (
    AssessmentStatus,
    ControlReference,
    ControlReferenceSystem,
    DisplayBehavior,
    DisplayColor,
    DisplayDirection,
    DisplayMeaning,
    DisplaySymbol,
    DisplayUnit,
    EvidenceRecord,
    EvidenceSourceKind,
    FaceArrangement,
    FaceLayout,
    Installation,
    JurisdictionIdentity,
    LongitudinalPlacement,
    Mounting,
    NonVisualOutput,
    NonVisualPattern,
    OutputModality,
    PhysicalRole,
    ProfileAssessment,
    ServedUser,
    SignalContractModel,
    SignalDeviceIdentity,
    SignalDeviceProfile,
)


SIGNAL_DEVICE_INVENTORY_SCHEMA = "torii.signal-device-profile-inventory/v1"
OCIT_C_SIGNAL_DEVICE_ADAPTER_VERSION = "1.0.0"


class OcitSafetyClass(StrEnum):
    RELEASE = "release"
    STOP = "stop"
    OFF_DARK = "off_dark"
    YELLOW_FLASH = "yellow_flash"


class OcitAllowedSignalImage(SignalContractModel):
    code: str = Field(min_length=1)
    safety_class: OcitSafetyClass
    source_element: str = Field(min_length=1)
    is_end_image: bool | None = None


class OcitLampOutputSlot(SignalContractModel):
    slot_id: str = Field(min_length=1)
    source_label: str | None = None
    head_ids: tuple[str, ...]

    @field_validator("head_ids")
    @classmethod
    def normalize_head_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized:
            raise ValueError("an OCIT lamp-output slot must reference at least one head")
        return normalized


class OcitLogicalSignalGroup(SignalContractModel):
    group_id: str = Field(min_length=1)
    ocit_outstation_number: str | None = None
    traffic_class_native: str = Field(min_length=1)
    served_users: tuple[ServedUser, ...]
    head_ids: tuple[str, ...]
    main_direction: bool | None = None
    technical_subnode: str | None = None
    shutdown_subnode: str | None = None
    allowed_signal_images: tuple[OcitAllowedSignalImage, ...] = ()
    output_slots: tuple[OcitLampOutputSlot, ...] = ()

    @field_validator("served_users", "head_ids")
    @classmethod
    def normalize_set_fields(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(sorted(set(value), key=str))


class UnassignedSignalGenerator(SignalContractModel):
    generator_id: str = Field(min_length=1)
    signal_group_id: str | None = None
    mast_id: str | None = None
    mounting_native: str | None = None
    reason: str = Field(min_length=1)


class OcitSignalSourceMetadata(SignalContractModel):
    node_id: str = Field(min_length=1)
    node_name: str | None = None
    country_native: str | None = None
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    guideline_native: str | None = None
    planning_version: str | None = None
    node_revision: str | None = None
    document_version: str | None = None
    data_structure_version: str | None = None
    block_assignment_version: str | None = None
    adapter_version: Literal["1.0.0"] = OCIT_C_SIGNAL_DEVICE_ADAPTER_VERSION


class AutomaticBindingGate(SignalContractModel):
    status: Literal["blocked"] = "blocked"
    reasons: tuple[str, ...]

    @field_validator("reasons")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized:
            raise ValueError("a blocked automatic-binding gate must state a reason")
        return normalized


class SignalDeviceProfileInventory(SignalContractModel):
    """Public, source-hash-bound artifact returned by the OCIT-C classifier."""

    schema_id: Literal["torii.signal-device-profile-inventory/v1"] = Field(
        default=SIGNAL_DEVICE_INVENTORY_SCHEMA,
        alias="schema",
    )
    status: Literal["review_required"] = "review_required"
    disposition: Literal["classified_signal_device_inventory"] = (
        "classified_signal_device_inventory"
    )
    classification_only: Literal[True] = True
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_format: Literal["ocit_c"] = "ocit_c"
    source_metadata: OcitSignalSourceMetadata
    logical_signal_groups: tuple[OcitLogicalSignalGroup, ...]
    profiles: tuple[SignalDeviceProfile, ...]
    unassigned_signal_generators: tuple[UnassignedSignalGenerator, ...] = ()
    unresolved_dimensions: tuple[str, ...]
    automatic_binding_gate: AutomaticBindingGate
    signal_group_count: int = Field(default=0, ge=0, json_schema_extra={"readOnly": True})
    profile_count: int = Field(default=0, ge=0, json_schema_extra={"readOnly": True})
    unassigned_signal_generator_count: int = Field(
        default=0,
        ge=0,
        json_schema_extra={"readOnly": True},
    )

    @field_validator("logical_signal_groups")
    @classmethod
    def normalize_groups(
        cls,
        value: tuple[OcitLogicalSignalGroup, ...],
    ) -> tuple[OcitLogicalSignalGroup, ...]:
        if len({group.group_id for group in value}) != len(value):
            raise ValueError("logical signal group IDs must be unique")
        return tuple(sorted(value, key=lambda group: _natural_key(group.group_id)))

    @field_validator("profiles")
    @classmethod
    def normalize_profiles(
        cls,
        value: tuple[SignalDeviceProfile, ...],
    ) -> tuple[SignalDeviceProfile, ...]:
        if len({profile.device_id for profile in value}) != len(value):
            raise ValueError("physical signal device IDs must be unique")
        return tuple(sorted(value, key=lambda profile: _natural_key(profile.device_id)))

    @field_validator("unassigned_signal_generators")
    @classmethod
    def normalize_unassigned(
        cls,
        value: tuple[UnassignedSignalGenerator, ...],
    ) -> tuple[UnassignedSignalGenerator, ...]:
        return tuple(sorted(value, key=lambda item: _natural_key(item.generator_id)))

    @field_validator("unresolved_dimensions")
    @classmethod
    def normalize_unresolved(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(value)))
        if not normalized:
            raise ValueError("review_required inventory must state unresolved dimensions")
        return normalized

    @model_validator(mode="after")
    def finalize_counts_and_references(self) -> SignalDeviceProfileInventory:
        group_heads = {
            group.group_id: set(group.head_ids) for group in self.logical_signal_groups
        }
        group_ids = set(group_heads)
        for profile in self.profiles:
            referenced = {
                reference.signal_group_id
                for reference in profile.control_references
                if reference.signal_group_id
            }
            if not referenced or not referenced <= group_ids:
                raise ValueError(
                    f"profile {profile.device_id} must reference a logical group in this inventory"
                )
            missing_head_membership = sorted(
                group_id
                for group_id in referenced
                if profile.device_id not in group_heads[group_id]
            )
            if missing_head_membership:
                raise ValueError(
                    f"profile {profile.device_id} is absent from referenced group heads: "
                    f"{missing_head_membership}"
                )
        object.__setattr__(self, "signal_group_count", len(self.logical_signal_groups))
        object.__setattr__(self, "profile_count", len(self.profiles))
        object.__setattr__(
            self,
            "unassigned_signal_generator_count",
            len(self.unassigned_signal_generators),
        )
        return self

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


class _VendorSignalGenerator(SignalContractModel):
    generator_id: str
    signal_group_id: str | None = None
    mast_id: str | None = None
    mounting_native: str | None = None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, local_name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == local_name]


def _child(element: ET.Element | None, local_name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((child for child in element if _local_name(child.tag) == local_name), None)


def _descendants(element: ET.Element, local_name: str) -> list[ET.Element]:
    return [candidate for candidate in element.iter() if _local_name(candidate.tag) == local_name]


def _text(element: ET.Element | None) -> str:
    return "" if element is None or element.text is None else element.text.strip()


def _optional_text(element: ET.Element | None) -> str | None:
    value = _text(element)
    return value or None


def _natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )


def _node_key(value: str) -> str:
    stripped = value.strip()
    try:
        return str(int(stripped))
    except ValueError:
        return stripped.casefold()


def _parse_optional_bool(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "ja", "yes"}:
        return True
    if normalized in {"false", "0", "nein", "no"}:
        return False
    return None


def _served_users(traffic_class_native: str) -> tuple[ServedUser, ...]:
    normalized = (
        traffic_class_native.casefold()
        .replace("ß", "ss")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
    )
    mapping: dict[str, tuple[ServedUser, ...]] = {
        "kfz": (ServedUser.MOTOR_VEHICLE,),
        "bus": (ServedUser.BUS,),
        "strassenbahn": (ServedUser.TRAM,),
        "rad": (ServedUser.BICYCLE,),
        "fussgaenger": (ServedUser.PEDESTRIAN,),
        "blinde": (ServedUser.ACCESSIBILITY, ServedUser.PEDESTRIAN),
        "keine": (ServedUser.OTHER,),
    }
    return mapping.get(normalized, (ServedUser.UNKNOWN,))


def _source_country_code(country_native: str | None) -> str:
    normalized = (country_native or "").strip().casefold()
    return "DE" if normalized in {"de", "deutschland", "germany"} else "ZZ"


def _source_claimed_references(guideline_native: str | None) -> tuple[str, ...]:
    normalized = (guideline_native or "").strip()
    if normalized.casefold().startswith("rilsa"):
        return (normalized,)
    return ()


def _parse_allowed_signal_images(group: ET.Element) -> tuple[OcitAllowedSignalImage, ...]:
    container = _child(group, "ZulaessigeSignalbilder")
    if container is None:
        return ()
    images: list[OcitAllowedSignalImage] = []
    for section_name, safety_class in (
        ("Frei", OcitSafetyClass.RELEASE),
        ("Gesperrt", OcitSafetyClass.STOP),
    ):
        section = _child(container, section_name)
        if section is None:
            continue
        standard = _text(_child(section, "Standard"))
        if standard:
            images.append(
                OcitAllowedSignalImage(
                    code=standard,
                    safety_class=safety_class,
                    source_element=f"{section_name}/Standard",
                )
            )
        for additional in _children(section, "Zusaetzlich"):
            code = _text(_child(additional, "Signalbild"))
            if not code:
                continue
            images.append(
                OcitAllowedSignalImage(
                    code=code,
                    safety_class=safety_class,
                    source_element=f"{section_name}/Zusaetzlich/Signalbild",
                    is_end_image=_parse_optional_bool(_text(_child(additional, "IstEndbild"))),
                )
            )
    for element_name, safety_class in (
        ("StandardAusDunkel", OcitSafetyClass.OFF_DARK),
        ("StandardGelbblinken", OcitSafetyClass.YELLOW_FLASH),
    ):
        code = _text(_child(container, element_name))
        if code:
            images.append(
                OcitAllowedSignalImage(
                    code=code,
                    safety_class=safety_class,
                    source_element=element_name,
                )
            )
    return tuple(images)


def _parse_output_slots(group: ET.Element) -> tuple[OcitLampOutputSlot, ...]:
    outputs = _child(group, "Lampenausgaenge")
    if outputs is None:
        return ()
    slots: list[OcitLampOutputSlot] = []
    for slot in outputs:
        head_ids = tuple(
            head_id
            for lamp in _children(slot, "Lampe")
            if (head_id := _text(_child(lamp, "Bezeichnung")))
        )
        if not head_ids:
            continue
        slots.append(
            OcitLampOutputSlot(
                slot_id=_local_name(slot.tag),
                source_label=_optional_text(_child(slot, "Bezeichnung")),
                head_ids=head_ids,
            )
        )
    return tuple(slots)


def _parse_vendor_generators(root: ET.Element) -> tuple[_VendorSignalGenerator, ...]:
    rows: list[_VendorSignalGenerator] = []
    seen: set[str] = set()
    for element in _descendants(root, "Signalgeber"):
        generator_id = _text(_child(element, "BezeichnungKurz"))
        if not generator_id:
            continue
        if generator_id in seen:
            raise ValueError(f"OCIT-C defines physical signal generator {generator_id} more than once")
        seen.add(generator_id)
        mast = _child(element, "Mast")
        rows.append(
            _VendorSignalGenerator(
                generator_id=generator_id,
                signal_group_id=_optional_text(_child(element, "SgrBezeichnung")),
                mast_id=_optional_text(_child(mast, "Bezeichnung")),
                mounting_native=_optional_text(_child(mast, "Anbringung")),
            )
        )
    return tuple(rows)


def _non_visual_modality(label: str | None) -> OutputModality | None:
    normalized = (label or "").casefold().replace(" ", "")
    has_tone = "ton" in normalized or "akust" in normalized
    has_vibration = "vibra" in normalized or "tast" in normalized
    if has_tone and has_vibration:
        return OutputModality.AUDIBLE_TACTILE
    if has_tone:
        return OutputModality.AUDIBLE
    if has_vibration:
        return OutputModality.TACTILE
    return None


def _visual_color(slot: OcitLampOutputSlot) -> DisplayColor:
    label = (slot.source_label or "").strip().casefold()
    normalized = label.replace("ü", "ue")
    explicit = {
        "rot": DisplayColor.RED,
        "red": DisplayColor.RED,
        "gelb": DisplayColor.YELLOW,
        "yellow": DisplayColor.YELLOW,
        "amber": DisplayColor.AMBER,
        "gruen": DisplayColor.GREEN,
        "green": DisplayColor.GREEN,
        "weiss": DisplayColor.WHITE,
        "white": DisplayColor.WHITE,
    }
    if normalized:
        return explicit.get(normalized, DisplayColor.UNKNOWN)
    return {
        "rot": DisplayColor.RED,
        "gelb": DisplayColor.YELLOW,
        "gruen": DisplayColor.GREEN,
    }.get(slot.slot_id.casefold(), DisplayColor.UNKNOWN)


def _non_visual_pattern(modality: OutputModality) -> NonVisualPattern:
    if modality is OutputModality.TACTILE:
        return NonVisualPattern.VIBRATION
    return NonVisualPattern.UNKNOWN


def _profile_for_head(
    *,
    node_id: str,
    groups: tuple[OcitLogicalSignalGroup, ...],
    head_id: str,
    source_file: str,
    source_sha256: str,
    jurisdiction: JurisdictionIdentity,
    vendor: _VendorSignalGenerator | None,
) -> SignalDeviceProfile:
    head_slots = [
        (group, slot)
        for group in groups
        for slot in group.output_slots
        if head_id in slot.head_ids
    ]
    served_users = tuple(
        sorted(
            {user for group in groups for user in group.served_users},
            key=str,
        )
    )
    visual_units: list[DisplayUnit] = []
    non_visual_outputs: list[NonVisualOutput] = []
    for group, slot in head_slots:
        modality = _non_visual_modality(slot.source_label)
        if modality is not None:
            non_visual_outputs.append(
                NonVisualOutput(
                    output_id=f"{head_id}:{group.group_id}:{slot.slot_id}:nonvisual",
                    modality=modality,
                    pattern=_non_visual_pattern(modality),
                    served_users=group.served_users,
                    source_channel_id=slot.slot_id,
                    native_code=slot.source_label,
                )
            )
            continue
        visual_units.append(
            DisplayUnit(
                unit_id=f"{head_id}:{group.group_id}:{slot.slot_id}:visual",
                section_index=len(visual_units),
                color=_visual_color(slot),
                symbol=DisplaySymbol.UNKNOWN,
                direction=DisplayDirection.UNKNOWN,
                behavior=DisplayBehavior.UNKNOWN,
                meaning=DisplayMeaning.UNKNOWN,
                native_code=slot.source_label or slot.slot_id,
            )
        )

    modalities: list[OutputModality] = [output.modality for output in non_visual_outputs]
    face_layout: FaceLayout | None = None
    if visual_units:
        modalities.append(OutputModality.VISUAL)
        face_layout = FaceLayout(
            section_count=len(visual_units),
            arrangement=FaceArrangement.UNKNOWN,
            section_order=tuple(range(len(visual_units))),
        )

    evidence_supports = (
        "identity.served_users",
        "identity.display_units",
        "identity.non_visual_outputs",
        "control_references.signal_group_id",
    )
    if jurisdiction.source_claimed_references:
        evidence_supports += (
            "identity.jurisdiction.source_claimed_references",
        )
    evidence = tuple(
        EvidenceRecord(
            evidence_id=f"ocit-c:{node_id}:{group.group_id}:{head_id}",
            source_kind=EvidenceSourceKind.OPERATOR_EXPORT,
            source_ref=source_file,
            observed_fact=(
                f"OCIT-C group {group.group_id} assigns output slots "
                f"{','.join(slot.slot_id for slot in group.output_slots if head_id in slot.head_ids)} "
                f"to signal generator {head_id}"
            ),
            confidence=1.0,
            input_sha256=source_sha256,
            supports_fields=evidence_supports,
        )
        for group in groups
    )
    review_reasons = {
        "automatic_binding_not_authorized_by_device_classification",
        "lane_stop_line_movement_binding_not_resolved",
        "longitudinal_placement_not_explicit_in_ocit_c",
        "ocit_c_has_no_explicit_signal_group_type",
        "physical_role_not_explicit_in_ocit_c",
    }
    if visual_units:
        review_reasons.update(
            {
                "display_symbol_not_explicit_in_ocit_c",
                "face_arrangement_not_explicit_in_ocit_c",
            }
        )
    if any(output.pattern is NonVisualPattern.UNKNOWN for output in non_visual_outputs):
        review_reasons.add("non_visual_pattern_not_explicit_in_ocit_c")

    native_location = None
    if vendor is not None:
        native_location = "/".join(
            value for value in (vendor.mast_id, vendor.mounting_native) if value
        ) or None
    return SignalDeviceProfile(
        profile_id=(
            f"ocit-c:{source_sha256[:12]}:{node_id}:"
            f"{'+'.join(group.group_id for group in groups)}:{head_id}"
        ),
        device_id=head_id,
        identity=SignalDeviceIdentity(
            jurisdiction=jurisdiction,
            served_users=served_users,
            modalities=tuple(modalities),
            display_units=tuple(visual_units),
            non_visual_outputs=tuple(non_visual_outputs),
            face_layout=face_layout,
        ),
        installation=Installation(
            physical_role=PhysicalRole.UNKNOWN,
            longitudinal_placement=LongitudinalPlacement.UNKNOWN,
            mounting=Mounting.UNKNOWN,
            native_location_code=native_location,
        ),
        control_references=tuple(
            ControlReference(
                reference_id=f"ocit-c:{node_id}:{group.group_id}:{head_id}",
                system=ControlReferenceSystem.OCIT,
                controller_id=node_id,
                signal_group_id=group.group_id,
            )
            for group in groups
        ),
        evidence=evidence,
        assessment=ProfileAssessment(
            status=AssessmentStatus.REVIEW_REQUIRED,
            review_reasons=tuple(review_reasons),
        ),
    )


def classify_ocit_c_signal_device_inventory(
    source_bytes: bytes,
    *,
    source_file: str,
    expected_node_id: str | None = None,
) -> SignalDeviceProfileInventory:
    """Classify one immutable OCIT-C byte snapshot without binding or writes."""

    if not source_bytes:
        raise ValueError("OCIT-C source bytes must not be empty")
    uppercase_source = source_bytes.upper()
    if b"<!DOCTYPE" in uppercase_source or b"<!ENTITY" in uppercase_source:
        raise ValueError("OCIT-C XML with DTD or entity declarations is not accepted")
    try:
        root = ET.fromstring(source_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"invalid OCIT-C XML: {exc}") from exc

    header = next(iter(_descendants(root, "Kopfdaten")), None)
    if header is None:
        raise ValueError("OCIT-C file has no Kopfdaten")
    node_id = _text(_child(header, "Kurzbezeichnung"))
    if not node_id:
        raise ValueError("OCIT-C file has no node identifier")
    if expected_node_id is not None:
        expected = expected_node_id.strip()
        if not expected:
            raise ValueError("expected_node_id must not be empty")
        if _node_key(expected) != _node_key(node_id):
            raise ValueError(
                f"OCIT-C node {node_id} does not match expected_node_id {expected_node_id}"
            )

    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    country_native = _optional_text(_child(header, "Laenderbezeichnung"))
    country_code = _source_country_code(country_native)
    guideline_native = _optional_text(_child(header, "Richtlinie"))
    claimed_references = _source_claimed_references(guideline_native)
    version = next(iter(_descendants(root, "DateiVersion")), None)
    metadata = OcitSignalSourceMetadata(
        node_id=node_id,
        node_name=_optional_text(_child(header, "Name")),
        country_native=country_native,
        country_code=country_code,
        guideline_native=guideline_native,
        planning_version=_optional_text(_child(header, "Planungsversion")),
        node_revision=_optional_text(_child(header, "KnotenVersionsstand")),
        document_version=_optional_text(_child(version, "VersionDokument")),
        data_structure_version=_optional_text(_child(version, "VersionDatenstruktur")),
        block_assignment_version=_optional_text(_child(version, "VersionBlockzuordnung")),
    )
    jurisdiction = JurisdictionIdentity(
        country_code=country_code,
        profile_id="de_ocit_c_composable" if country_code == "DE" else "ocit_c_composable",
        profile_capability_references=(
            "BOStrab Annex 4",
            "DIN 32981",
            "DIN EN 12368",
            "RiLSA",
            "StVO § 37",
        )
        if country_code == "DE"
        else (),
        source_claimed_references=claimed_references,
    )

    vendor_generators = _parse_vendor_generators(root)
    vendor_by_id = {generator.generator_id: generator for generator in vendor_generators}
    vendor_by_group: dict[str, set[str]] = {}
    for generator in vendor_generators:
        if generator.signal_group_id:
            vendor_by_group.setdefault(generator.signal_group_id, set()).add(
                generator.generator_id
            )

    groups: list[OcitLogicalSignalGroup] = []
    group_ids: set[str] = set()
    for element in _descendants(root, "Signalgruppe"):
        group_id = _text(_child(element, "BezeichnungKurz"))
        traffic_class = _text(_child(element, "Verkehrsart")) or _text(
            _child(element, "Verkehrart")
        )
        if not group_id or not traffic_class:
            continue
        if group_id in group_ids:
            raise ValueError(f"OCIT-C defines logical signal group {group_id} more than once")
        group_ids.add(group_id)
        output_slots = _parse_output_slots(element)
        lamp_head_ids = {
            head_id for slot in output_slots for head_id in slot.head_ids
        }
        head_ids = tuple(sorted(lamp_head_ids | vendor_by_group.get(group_id, set())))
        groups.append(
            OcitLogicalSignalGroup(
                group_id=group_id,
                ocit_outstation_number=_optional_text(_child(element, "OCITOutstationNr")),
                traffic_class_native=traffic_class,
                served_users=_served_users(traffic_class),
                head_ids=head_ids,
                main_direction=_parse_optional_bool(_text(_child(element, "Hauptrichtung"))),
                technical_subnode=_optional_text(
                    _child(element, "VerkehrstechnischerTeilknoten")
                ),
                shutdown_subnode=_optional_text(_child(element, "AbschaltTeilknoten")),
                allowed_signal_images=_parse_allowed_signal_images(element),
                output_slots=output_slots,
            )
        )

    if not groups:
        raise ValueError("OCIT-C file has no logical signal groups with traffic classes")

    profiles: list[SignalDeviceProfile] = []
    unassigned: list[UnassignedSignalGenerator] = []
    groups_by_lamp_head: dict[str, list[OcitLogicalSignalGroup]] = {}
    for group in groups:
        lamp_head_ids = {
            head_id for slot in group.output_slots for head_id in slot.head_ids
        }
        for head_id in lamp_head_ids:
            groups_by_lamp_head.setdefault(head_id, []).append(group)
        for head_id in sorted(set(group.head_ids) - lamp_head_ids, key=_natural_key):
            generator = vendor_by_id.get(head_id)
            unassigned.append(
                UnassignedSignalGenerator(
                    generator_id=head_id,
                    signal_group_id=group.group_id,
                    mast_id=generator.mast_id if generator else None,
                    mounting_native=generator.mounting_native if generator else None,
                    reason="no_lamp_output_evidence",
                )
            )
    for head_id, head_groups in sorted(
        groups_by_lamp_head.items(),
        key=lambda item: _natural_key(item[0]),
    ):
        profiles.append(
            _profile_for_head(
                node_id=node_id,
                groups=tuple(sorted(head_groups, key=lambda group: _natural_key(group.group_id))),
                head_id=head_id,
                source_file=source_file,
                source_sha256=source_sha256,
                jurisdiction=jurisdiction,
                vendor=vendor_by_id.get(head_id),
            )
        )
    for generator in vendor_generators:
        if not generator.signal_group_id or generator.signal_group_id not in group_ids:
            unassigned.append(
                UnassignedSignalGenerator(
                    generator_id=generator.generator_id,
                    signal_group_id=generator.signal_group_id,
                    mast_id=generator.mast_id,
                    mounting_native=generator.mounting_native,
                    reason=(
                        "signal_group_not_declared"
                        if generator.signal_group_id
                        else "signal_group_reference_missing"
                    ),
                )
            )

    unresolved = {
        "automatic_lane_movement_group_controller_binding",
        "longitudinal_placement",
        "physical_role",
    }
    if any(profile.identity.display_units for profile in profiles):
        unresolved.update({"display_symbol", "face_arrangement"})
    if any(
        ServedUser.UNKNOWN in group.served_users
        for group in groups
    ):
        unresolved.add("unknown_source_traffic_class")
    if unassigned:
        unresolved.add("unassigned_or_unprofiled_signal_generators")
    return SignalDeviceProfileInventory(
        source_file=source_file,
        source_sha256=source_sha256,
        source_metadata=metadata,
        logical_signal_groups=tuple(groups),
        profiles=tuple(profiles),
        unassigned_signal_generators=tuple(unassigned),
        unresolved_dimensions=tuple(unresolved),
        automatic_binding_gate=AutomaticBindingGate(
            reasons=(
                "device_identity_does_not_prove_lane_or_movement_applicability",
                "device_identity_does_not_authorize_controller_phase_or_timing_changes",
                "ocit_c_has_no_universal_explicit_signal_group_type",
            )
        ),
    )


def build_signal_device_profile_inventory_schema() -> dict[str, Any]:
    schema = SignalDeviceProfileInventory.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://github.com/Tarard/Torii-SUMO/schemas/"
        "torii.signal-device-profile-inventory.v1.schema.json"
    )
    schema["x-torii-status"] = "germany-first-read-only-ocit-c-device-inventory"
    return schema
