from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from torii_sumo.core.signal_device_profile import (
    AssessmentStatus,
    ControlMethod,
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
    SignalControlMethodProfile,
    SignalDeviceIdentity,
    SignalDeviceProfile,
    SignalRuntimeState,
    TargetBinding,
    TargetKind,
    build_signal_device_profile_schema,
    index_heads_by_signal_group,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _unit(
    unit_id: str,
    section_index: int,
    color: DisplayColor,
    symbol: DisplaySymbol,
    meaning: DisplayMeaning,
    *,
    direction: DisplayDirection = DisplayDirection.NONE,
    behavior: DisplayBehavior = DisplayBehavior.STEADY,
    native_code: str | None = None,
) -> DisplayUnit:
    return DisplayUnit(
        unit_id=unit_id,
        section_index=section_index,
        color=color,
        symbol=symbol,
        direction=direction,
        behavior=behavior,
        meaning=meaning,
        native_code=native_code,
    )


def _profile(
    name: str,
    users: tuple[ServedUser, ...],
    units: tuple[DisplayUnit, ...],
    *,
    arrangement: FaceArrangement = FaceArrangement.VERTICAL,
    shared: bool = False,
    native_type: str = "Signalgeber",
    physical_role: PhysicalRole = PhysicalRole.MAIN,
    mounting: Mounting = Mounting.ROADSIDE,
    signal_group: str | None = None,
) -> SignalDeviceProfile:
    section_count = max(unit.section_index for unit in units) + 1
    controls = ()
    if signal_group is not None:
        controls = (
            ControlReference(
                reference_id=f"control-{name}",
                system=ControlReferenceSystem.FIELD_CONTROLLER,
                controller_id="controller-de-1",
                signal_group_id=signal_group,
            ),
        )
    return SignalDeviceProfile(
        profile_id=f"profile-{name}",
        device_id=f"head-{name}",
        identity=SignalDeviceIdentity(
            jurisdiction=JurisdictionIdentity(source_native_type_id=native_type),
            served_users=users,
            display_units=units,
            face_layout=FaceLayout(
                section_count=section_count,
                arrangement=arrangement,
                section_order=tuple(range(section_count)),
                shared_or_bimodal_sections=shared,
            ),
        ),
        installation=Installation(
            physical_role=physical_role,
            longitudinal_placement=LongitudinalPlacement.AT_STOP_LINE,
            mounting=mounting,
        ),
        target_bindings=(
            TargetBinding(
                binding_id=f"binding-{name}",
                target_kind=TargetKind.MOVEMENT,
                target_ids=(f"movement-{name}",),
                served_users=users,
                confidence=1.0,
            ),
        ),
        control_references=controls,
        evidence=(
            EvidenceRecord(
                evidence_id=f"evidence-{name}",
                source_kind=EvidenceSourceKind.OFFICIAL_STANDARD,
                source_ref="DE:StVO § 37 / RiLSA / DIN EN 12368",
                observed_fact=f"German signal profile {name}",
                confidence=1.0,
                supports_fields=("identity", "installation"),
            ),
        ),
        assessment=ProfileAssessment(status=AssessmentStatus.PASS),
    )


def test_german_vehicle_circular_ryg_profile() -> None:
    profile = _profile(
        "vehicle-circular",
        (ServedUser.MOTOR_VEHICLE,),
        (
            _unit("red", 0, DisplayColor.RED, DisplaySymbol.CIRCLE, DisplayMeaning.STOP),
            _unit("amber", 1, DisplayColor.AMBER, DisplaySymbol.CIRCLE, DisplayMeaning.PREPARE),
            _unit("green", 2, DisplayColor.GREEN, DisplaySymbol.CIRCLE, DisplayMeaning.PROCEED_IF_CLEAR),
        ),
        signal_group="sg-main",
    )

    assert profile.identity.jurisdiction.country_code == "DE"
    assert profile.identity.jurisdiction.profile_id == "de_stvo_rilsa"
    assert profile.identity.jurisdiction.profile_capability_references == (
        "DIN EN 12368",
        "RiLSA",
        "StVO § 37",
    )
    assert profile.identity.jurisdiction.source_claimed_references == ()
    assert profile.assessment.status is AssessmentStatus.PASS
    assert profile.derived_alias.startswith("de:motor_vehicle:")
    assert "red-circle-none-steady" in profile.derived_alias


def test_german_left_arrow_is_composed_from_display_dimensions() -> None:
    profile = _profile(
        "vehicle-left-arrow",
        (ServedUser.MOTOR_VEHICLE,),
        (
            _unit(
                "red-left",
                0,
                DisplayColor.RED,
                DisplaySymbol.ARROW,
                DisplayMeaning.STOP,
                direction=DisplayDirection.LEFT,
            ),
            _unit(
                "amber-left",
                1,
                DisplayColor.AMBER,
                DisplaySymbol.ARROW,
                DisplayMeaning.PREPARE,
                direction=DisplayDirection.LEFT,
            ),
            _unit(
                "green-left",
                2,
                DisplayColor.GREEN,
                DisplaySymbol.ARROW,
                DisplayMeaning.DIRECTION_CLEAR,
                direction=DisplayDirection.LEFT,
            ),
        ),
    )

    assert {unit.symbol for unit in profile.identity.display_units} == {DisplaySymbol.ARROW}
    assert {unit.direction for unit in profile.identity.display_units} == {DisplayDirection.LEFT}
    assert "green-arrow-left-steady" in profile.derived_alias


def test_german_pedestrian_and_bicycle_profiles_are_user_specific() -> None:
    pedestrian = _profile(
        "pedestrian",
        (ServedUser.PEDESTRIAN,),
        (
            _unit("ped-red", 0, DisplayColor.RED, DisplaySymbol.PEDESTRIAN, DisplayMeaning.STOP),
            _unit("ped-green", 1, DisplayColor.GREEN, DisplaySymbol.PEDESTRIAN, DisplayMeaning.PROCEED),
        ),
        native_type="Fußgängersignalgeber",
    )
    bicycle = _profile(
        "bicycle",
        (ServedUser.BICYCLE,),
        (
            _unit("bike-red", 0, DisplayColor.RED, DisplaySymbol.BICYCLE, DisplayMeaning.STOP),
            _unit("bike-amber", 1, DisplayColor.AMBER, DisplaySymbol.BICYCLE, DisplayMeaning.PREPARE),
            _unit("bike-green", 2, DisplayColor.GREEN, DisplaySymbol.BICYCLE, DisplayMeaning.PROCEED),
        ),
        native_type="Radverkehrssignalgeber",
    )

    assert pedestrian.identity.served_users == (ServedUser.PEDESTRIAN,)
    assert bicycle.identity.served_users == (ServedUser.BICYCLE,)
    assert pedestrian.identity.jurisdiction.source_native_type_id == "Fußgängersignalgeber"
    assert bicycle.identity.jurisdiction.source_native_type_id == "Radverkehrssignalgeber"


def test_german_tram_oepnv_white_bar_profile() -> None:
    profile = _profile(
        "tram-bars",
        (ServedUser.TRAM, ServedUser.PUBLIC_TRANSPORT),
        (
            _unit(
                "f0",
                0,
                DisplayColor.WHITE,
                DisplaySymbol.BAR,
                DisplayMeaning.STOP,
                direction=DisplayDirection.HORIZONTAL,
                native_code="F0",
            ),
            _unit(
                "f1",
                1,
                DisplayColor.WHITE,
                DisplaySymbol.BAR,
                DisplayMeaning.PROCEED,
                direction=DisplayDirection.VERTICAL,
                native_code="F1",
            ),
            _unit(
                "f2-left",
                2,
                DisplayColor.WHITE,
                DisplaySymbol.BAR,
                DisplayMeaning.DIRECTION_CLEAR,
                direction=DisplayDirection.DIAGONAL_LEFT,
                native_code="F2",
            ),
        ),
        arrangement=FaceArrangement.CLUSTER,
        native_type="ÖPNV-Sondersignal",
        mounting=Mounting.TRACKSIDE,
    )

    assert {unit.color for unit in profile.identity.display_units} == {DisplayColor.WHITE}
    assert {unit.native_code for unit in profile.identity.display_units} == {"F0", "F1", "F2"}
    assert profile.installation.mounting is Mounting.TRACKSIDE


def test_lane_control_red_cross_and_green_arrow_share_a_variable_section() -> None:
    profile = _profile(
        "lane-control",
        (ServedUser.LANE_USER, ServedUser.MOTOR_VEHICLE),
        (
            _unit("closed", 0, DisplayColor.RED, DisplaySymbol.CROSS, DisplayMeaning.LANE_CLOSED),
            _unit(
                "open",
                0,
                DisplayColor.GREEN,
                DisplaySymbol.LANE_ARROW,
                DisplayMeaning.LANE_OPEN,
                direction=DisplayDirection.DOWN,
            ),
        ),
        arrangement=FaceArrangement.MATRIX,
        shared=True,
        native_type="Dauerlichtzeichen",
        physical_role=PhysicalRole.OVERHEAD,
        mounting=Mounting.OVERHEAD_PORTAL,
    )

    assert profile.identity.face_layout.section_count == 1
    assert profile.identity.face_layout.shared_or_bimodal_sections is True
    assert profile.installation.physical_role is PhysicalRole.OVERHEAD


def test_ocit_gruen_ton_vibra_channel_is_non_visual_not_green() -> None:
    profile = SignalDeviceProfile(
        profile_id="profile-ocit-ton-vibra",
        device_id="head-ocit-ton-vibra",
        identity=SignalDeviceIdentity(
            jurisdiction=JurisdictionIdentity(
                source_native_type_id="Blinde",
                source_native_subtype_id="Ton/Vibra",
            ),
            served_users=(ServedUser.PEDESTRIAN, ServedUser.ACCESSIBILITY),
            modalities=(OutputModality.AUDIBLE_TACTILE,),
            non_visual_outputs=(
                NonVisualOutput(
                    output_id="accessibility-output",
                    modality=OutputModality.AUDIBLE_TACTILE,
                    pattern=NonVisualPattern.PULSED,
                    served_users=(ServedUser.PEDESTRIAN, ServedUser.ACCESSIBILITY),
                    source_channel_id="Gruen",
                    native_code="Ton/Vibra",
                ),
            ),
        ),
        installation=Installation(
            physical_role=PhysicalRole.AUXILIARY,
            longitudinal_placement=LongitudinalPlacement.AT_STOP_LINE,
            mounting=Mounting.ROADSIDE,
        ),
        target_bindings=(
            TargetBinding(
                binding_id="binding-crossing",
                target_kind=TargetKind.CROSSING,
                target_ids=("crossing-7",),
                served_users=(ServedUser.PEDESTRIAN, ServedUser.ACCESSIBILITY),
                confidence=1.0,
            ),
        ),
        evidence=(
            EvidenceRecord(
                evidence_id="ocit-output",
                source_kind=EvidenceSourceKind.OPERATOR_EXPORT,
                source_ref="OCIT-C Lampenausgaenge/Gruen/Bezeichnung",
                observed_fact="Gruen container is named Ton/Vibra for Verkehrsart Blinde",
                confidence=1.0,
                supports_fields=("identity.non_visual_outputs",),
            ),
        ),
        assessment=ProfileAssessment(status=AssessmentStatus.PASS),
    )

    identity_payload = profile.model_dump(mode="json")["identity"]
    assert identity_payload["display_units"] == []
    assert identity_payload["face_layout"] is None
    assert identity_payload["served_users"] == ["accessibility", "pedestrian"]
    assert identity_payload["non_visual_outputs"][0]["served_users"] == [
        "accessibility",
        "pedestrian",
    ]
    assert identity_payload["non_visual_outputs"][0]["source_channel_id"] == "Gruen"
    assert "color" not in identity_payload["non_visual_outputs"][0]
    assert "green" not in profile.derived_alias


def test_combined_pedestrian_bicycle_head_keeps_both_users_and_shared_sections() -> None:
    profile = _profile(
        "combined-ped-bike",
        (ServedUser.PEDESTRIAN, ServedUser.BICYCLE),
        (
            _unit("ped-red", 0, DisplayColor.RED, DisplaySymbol.PEDESTRIAN, DisplayMeaning.STOP),
            _unit("bike-red", 0, DisplayColor.RED, DisplaySymbol.BICYCLE, DisplayMeaning.STOP),
            _unit("ped-green", 1, DisplayColor.GREEN, DisplaySymbol.PEDESTRIAN, DisplayMeaning.PROCEED),
            _unit("bike-green", 1, DisplayColor.GREEN, DisplaySymbol.BICYCLE, DisplayMeaning.PROCEED),
        ),
        shared=True,
        native_type="Kombisignal Fuß-Rad",
    )

    assert profile.identity.served_users == (ServedUser.BICYCLE, ServedUser.PEDESTRIAN)
    assert profile.target_bindings[0].served_users == (ServedUser.BICYCLE, ServedUser.PEDESTRIAN)


def test_unknown_profile_fails_closed_to_review_required() -> None:
    profile = SignalDeviceProfile(
        profile_id="profile-unknown",
        device_id="head-unknown",
        identity=SignalDeviceIdentity(
            served_users=(ServedUser.UNKNOWN,),
            display_units=(
                _unit(
                    "unknown",
                    0,
                    DisplayColor.UNKNOWN,
                    DisplaySymbol.UNKNOWN,
                    DisplayMeaning.UNKNOWN,
                    direction=DisplayDirection.UNKNOWN,
                    behavior=DisplayBehavior.UNKNOWN,
                ),
            ),
            face_layout=FaceLayout(section_count=1, arrangement=FaceArrangement.UNKNOWN),
        ),
        installation=Installation(
            physical_role=PhysicalRole.UNKNOWN,
            longitudinal_placement=LongitudinalPlacement.UNKNOWN,
            mounting=Mounting.UNKNOWN,
        ),
        assessment=ProfileAssessment(status=AssessmentStatus.REVIEW_REQUIRED),
    )

    assert profile.assessment.status is AssessmentStatus.REVIEW_REQUIRED
    assert set(profile.assessment.review_reasons) == {
        "evidence_missing",
        "face_layout_unknown",
        "identity_unknown_display",
        "identity_unknown_served_user",
        "installation_unknown",
    }

    with pytest.raises(ValidationError, match="pass profile has unresolved review reasons"):
        SignalDeviceProfile.model_validate(
            {
                **profile.model_dump(mode="json", by_alias=True),
                "assessment": {"status": "pass", "review_reasons": []},
            }
        )


def test_multiple_physical_heads_may_reference_one_signal_group() -> None:
    units = (
        _unit("red", 0, DisplayColor.RED, DisplaySymbol.CIRCLE, DisplayMeaning.STOP),
        _unit("amber", 1, DisplayColor.AMBER, DisplaySymbol.CIRCLE, DisplayMeaning.PREPARE),
        _unit("green", 2, DisplayColor.GREEN, DisplaySymbol.CIRCLE, DisplayMeaning.PROCEED_IF_CLEAR),
    )
    primary = _profile(
        "main-primary",
        (ServedUser.MOTOR_VEHICLE,),
        units,
        signal_group="sg-42",
        physical_role=PhysicalRole.MAIN,
    )
    repeater = _profile(
        "main-repeater",
        (ServedUser.MOTOR_VEHICLE,),
        units,
        signal_group="sg-42",
        physical_role=PhysicalRole.REPEATED,
    )

    assert primary.device_id != repeater.device_id
    assert primary.installation.physical_role is PhysicalRole.MAIN
    assert repeater.installation.physical_role is PhysicalRole.REPEATED
    assert primary.control_references[0].signal_group_id == repeater.control_references[0].signal_group_id
    assert index_heads_by_signal_group((repeater, primary)) == {
        "sg-42": ("head-main-primary", "head-main-repeater")
    }


def test_canonical_serialization_round_trips_and_is_order_stable() -> None:
    profile = _profile(
        "canonical",
        (ServedUser.PEDESTRIAN, ServedUser.BICYCLE),
        (
            _unit("green", 1, DisplayColor.GREEN, DisplaySymbol.BICYCLE, DisplayMeaning.PROCEED),
            _unit("red", 0, DisplayColor.RED, DisplaySymbol.BICYCLE, DisplayMeaning.STOP),
        ),
    )

    canonical = profile.canonical_json()
    reparsed = SignalDeviceProfile.model_validate_json(canonical)

    assert canonical == reparsed.canonical_json()
    assert canonical == json.dumps(
        profile.canonical_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canonical.index('"bicycle"') < canonical.index('"pedestrian"')


def test_control_method_and_runtime_state_are_separate_contracts() -> None:
    profile = _profile(
        "separation",
        (ServedUser.MOTOR_VEHICLE,),
        (
            _unit("red", 0, DisplayColor.RED, DisplaySymbol.CIRCLE, DisplayMeaning.STOP),
            _unit("green", 1, DisplayColor.GREEN, DisplaySymbol.CIRCLE, DisplayMeaning.PROCEED_IF_CLEAR),
        ),
    )
    payload = profile.model_dump(mode="json", by_alias=True)
    payload["identity"]["control_strategy"] = "adaptive"

    with pytest.raises(ValidationError, match="device identity cannot contain control/runtime fields"):
        SignalDeviceProfile.model_validate(payload)

    with pytest.raises(ValidationError):
        ControlReference(
            reference_id="invalid",
            system=ControlReferenceSystem.SUMO,
            controller_id="tls-1",
            control_strategy="fixed_time",
        )

    runtime = SignalRuntimeState(
        observation_id="observation-1",
        device_id=profile.device_id,
        observed_at="2026-07-19T12:00:00+02:00",
        active_display_unit_ids=("green",),
        source_ref="OCIT-C snapshot 1",
    )
    method = SignalControlMethodProfile(
        controller_id="controller-de-1",
        method=ControlMethod.TRAFFIC_ACTUATED,
        program_id="weekday",
        source_ref="controller export",
    )

    assert "active_display_unit_ids" not in profile.canonical_dict()
    assert "method" not in profile.canonical_dict()
    assert runtime.schema_id == "torii.signal-runtime-state/v1"
    assert method.schema_id == "torii.signal-control-method/v1"


def test_exported_signal_device_profile_schema_is_current() -> None:
    expected = json.dumps(
        build_signal_device_profile_schema(),
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
    )
    schema_path = REPOSITORY_ROOT / "schemas/torii.signal-device-profile.v1.schema.json"
    assert schema_path.read_text(encoding="utf-8") == expected
