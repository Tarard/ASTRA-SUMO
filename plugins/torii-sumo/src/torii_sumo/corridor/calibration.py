from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from xml.etree import ElementTree as ET

from pydantic import Field, model_validator

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.connection_mode_audit import (
    audit_network_connection_mode,
    resolve_network_traffic_side,
)

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id


class ConnectionAuditCalibrationPolicy(ContractModel):
    schema_id: str = "torii.corridor.connection-audit-calibration-policy/v1"
    policy_id: StableToken
    endpoint_gap_quantile: float = Field(gt=0.0, le=1.0)
    precision_margin_units: float = Field(gt=0.0)
    maximum_lane_width_fraction: float = Field(gt=0.0, le=1.0)
    minimum_endpoint_samples: int = Field(ge=1)
    implicit_sumo_lane_width_m: float = Field(gt=0.0)
    normalized_lane_rank_tolerance: float = Field(ge=0.0, le=1.0)

    def identity_payload(self) -> dict[str, float | int]:
        return {
            "endpoint_gap_quantile": self.endpoint_gap_quantile,
            "precision_margin_units": self.precision_margin_units,
            "maximum_lane_width_fraction": self.maximum_lane_width_fraction,
            "minimum_endpoint_samples": self.minimum_endpoint_samples,
            "implicit_sumo_lane_width_m": self.implicit_sumo_lane_width_m,
            "normalized_lane_rank_tolerance": self.normalized_lane_rank_tolerance,
        }

    @classmethod
    def build(
        cls,
        *,
        endpoint_gap_quantile: float = 0.995,
        precision_margin_units: float = 2.0,
        maximum_lane_width_fraction: float = 0.25,
        minimum_endpoint_samples: int = 30,
        implicit_sumo_lane_width_m: float = 3.2,
        normalized_lane_rank_tolerance: float = 0.5,
    ) -> ConnectionAuditCalibrationPolicy:
        payload: dict[str, float | int] = {
            "endpoint_gap_quantile": endpoint_gap_quantile,
            "precision_margin_units": precision_margin_units,
            "maximum_lane_width_fraction": maximum_lane_width_fraction,
            "minimum_endpoint_samples": minimum_endpoint_samples,
            "implicit_sumo_lane_width_m": implicit_sumo_lane_width_m,
            "normalized_lane_rank_tolerance": normalized_lane_rank_tolerance,
        }
        return cls(
            policy_id=stable_id("policy", payload),
            **payload,
        )

    @model_validator(mode="after")
    def validate_policy(self) -> ConnectionAuditCalibrationPolicy:
        require_stable_id(self.policy_id, kind="policy")
        if self.policy_id != stable_id("policy", self.identity_payload()):
            raise ValueError("policy_id does not match the calibration policy payload.")
        return self


class ConnectionAuditCalibration(ContractModel):
    schema_id: str = "torii.corridor.connection-audit-calibration/v1"
    calibration_id: StableToken
    source_sha256: Sha256
    traffic_side: TrafficSide
    policy: ConnectionAuditCalibrationPolicy
    status: GateStatus
    endpoint_path_count: int = Field(ge=0)
    endpoint_sample_count: int = Field(ge=0)
    rejected_path_count: int = Field(ge=0)
    coordinate_precision_m: float | None = Field(default=None, gt=0.0)
    coordinate_precision_evidence: str
    median_lane_width_m: float | None = Field(default=None, gt=0.0)
    lane_width_evidence: str
    observed_gap_quantile_m: float | None = Field(default=None, ge=0.0)
    maximum_observed_gap_m: float | None = Field(default=None, ge=0.0)
    lane_width_cap_m: float | None = Field(default=None, gt=0.0)
    endpoint_tolerance_m: float | None = Field(default=None, gt=0.0)
    findings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_calibration(self) -> ConnectionAuditCalibration:
        require_stable_id(self.calibration_id, kind="calibration")
        if self.status is GateStatus.PASS and self.endpoint_tolerance_m is None:
            raise ValueError("A passing calibration requires an endpoint tolerance.")
        if self.status is GateStatus.BLOCKED and not self.findings:
            raise ValueError("A blocked calibration requires a finding.")
        return self


def calibrate_connection_mode_audit(
    root: ET.Element,
    *,
    source_sha256: str,
    traffic_side: TrafficSide,
    policy: ConnectionAuditCalibrationPolicy | None = None,
) -> ConnectionAuditCalibration:
    """Calibrate endpoint tolerance from one immutable source baseline.

    The calibration is deliberately unable to hide gross source gaps: any
    observed gap above the lane-scale cap blocks a recommendation.  Review is
    also required when the baseline is too small or serializes integer-only
    geometry, so a fixed 2 m fallback can never silently become a certificate.
    """

    selected_policy = policy or ConnectionAuditCalibrationPolicy.build()
    traffic_contract = resolve_network_traffic_side(root, traffic_side.value)
    findings = list(traffic_contract["failures"])
    effective_side = TrafficSide(str(traffic_contract["effective"]))
    audit = audit_network_connection_mode(
        root,
        traffic_side=effective_side.value,
        endpoint_tolerance_m=1_000_000.0,
        normalized_lane_rank_tolerance=selected_policy.normalized_lane_rank_tolerance,
    )
    gaps: list[float] = []
    endpoint_path_count = 0
    rejected_path_count = 0
    for junction in audit.get("junctions", []):
        for movement in junction.get("connection_mode_audit", {}).get(
            "movement_checks", []
        ):
            trace = movement.get("internal_path", {})
            if trace.get("status") != "pass":
                rejected_path_count += 1
                continue
            if trace.get("endpoint_gap_policy") in {
                "nearest_endpoint_diagnostic_only",
                "not_available_in_network_mode",
            }:
                continue
            endpoint_path_count += 1
            effective_gaps = trace.get("endpoint_effective_gaps_m")
            gaps.extend(
                float(value)
                for value in (
                    effective_gaps
                    if isinstance(effective_gaps, list)
                    else trace.get("endpoint_gaps_m", [])
                )
                if _finite_nonnegative(value)
            )

    coordinate_precision = _coordinate_precision(root)
    if coordinate_precision is None:
        findings.append("coordinate_precision_unavailable")
        precision_evidence = "unavailable"
    else:
        precision_evidence = "serialized_lane_shape_decimals"
        if coordinate_precision >= 1.0:
            findings.append("integer_only_coordinate_serialization_requires_review")

    explicit_widths = _external_lane_widths(root)
    if explicit_widths:
        median_lane_width = float(median(explicit_widths))
        lane_width_evidence = "explicit_external_lane_widths"
    else:
        median_lane_width = selected_policy.implicit_sumo_lane_width_m
        lane_width_evidence = "locked_sumo_default_lane_width"

    quantile_gap = _quantile(gaps, selected_policy.endpoint_gap_quantile)
    maximum_gap = max(gaps) if gaps else None
    lane_cap = median_lane_width * selected_policy.maximum_lane_width_fraction
    tolerance: float | None = None
    blocked = bool(traffic_contract["failures"])
    if not gaps:
        findings.append("no_traceable_endpoint_gap_samples")
        blocked = True
    if coordinate_precision is None:
        blocked = True
    if maximum_gap is not None and maximum_gap > lane_cap:
        findings.append(
            "baseline_endpoint_gap_exceeds_lane_scale_cap:"
            f"{maximum_gap:.6f}>{lane_cap:.6f}"
        )
        blocked = True
    if not blocked and quantile_gap is not None and coordinate_precision is not None:
        proposed = max(
            coordinate_precision,
            quantile_gap + selected_policy.precision_margin_units * coordinate_precision,
        )
        if proposed > lane_cap:
            findings.append(
                "calibrated_tolerance_exceeds_lane_scale_cap:"
                f"{proposed:.6f}>{lane_cap:.6f}"
            )
            blocked = True
        else:
            tolerance = proposed
    if len(gaps) < selected_policy.minimum_endpoint_samples:
        findings.append(
            "endpoint_sample_count_below_policy:"
            f"{len(gaps)}<{selected_policy.minimum_endpoint_samples}"
        )

    status = (
        GateStatus.BLOCKED
        if blocked
        else GateStatus.REVIEW
        if findings
        else GateStatus.PASS
    )
    payload = {
        "source_sha256": source_sha256,
        "traffic_side": effective_side.value,
        "policy_id": selected_policy.policy_id,
        "endpoint_path_count": endpoint_path_count,
        "endpoint_sample_count": len(gaps),
        "rejected_path_count": rejected_path_count,
        "coordinate_precision_m": _rounded(coordinate_precision),
        "median_lane_width_m": _rounded(median_lane_width),
        "observed_gap_quantile_m": _rounded(quantile_gap),
        "maximum_observed_gap_m": _rounded(maximum_gap),
        "lane_width_cap_m": _rounded(lane_cap),
        "endpoint_tolerance_m": _rounded(tolerance),
        "findings": tuple(sorted(set(findings))),
    }
    return ConnectionAuditCalibration(
        calibration_id=stable_id("calibration", payload),
        source_sha256=source_sha256,
        traffic_side=effective_side,
        policy=selected_policy,
        status=status,
        endpoint_path_count=endpoint_path_count,
        endpoint_sample_count=len(gaps),
        rejected_path_count=rejected_path_count,
        coordinate_precision_m=_rounded(coordinate_precision),
        coordinate_precision_evidence=precision_evidence,
        median_lane_width_m=_rounded(median_lane_width),
        lane_width_evidence=lane_width_evidence,
        observed_gap_quantile_m=_rounded(quantile_gap),
        maximum_observed_gap_m=_rounded(maximum_gap),
        lane_width_cap_m=_rounded(lane_cap),
        endpoint_tolerance_m=_rounded(tolerance),
        findings=tuple(sorted(set(findings))),
    )


def build_connection_mode_calibration_artifact(
    net_file: Path,
    *,
    output_file: Path,
    traffic_side: TrafficSide,
    policy: ConnectionAuditCalibrationPolicy | None = None,
) -> ConnectionAuditCalibration:
    source = net_file.resolve()
    calibration = calibrate_connection_mode_audit(
        ET.parse(source).getroot(),
        source_sha256=file_sha256(source),
        traffic_side=traffic_side,
        policy=policy,
    )
    write_json_atomic(
        output_file.resolve(),
        calibration.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return calibration


def _coordinate_precision(root: ET.Element) -> float | None:
    resolutions: list[float] = []
    for lane in root.findall("edge/lane"):
        for point in lane.attrib.get("shape", "").split():
            for component in point.split(",")[:2]:
                try:
                    exponent = Decimal(component).as_tuple().exponent
                except InvalidOperation:
                    continue
                resolutions.append(10.0**exponent if exponent < 0 else 1.0)
    return min(resolutions) if resolutions else None


def _external_lane_widths(root: ET.Element) -> list[float]:
    widths: list[float] = []
    for edge in root.findall("edge"):
        if edge.attrib.get("function", "") in {"internal", "crossing", "walkingarea"}:
            continue
        for lane in edge.findall("lane"):
            try:
                width = float(lane.attrib.get("width", ""))
            except ValueError:
                continue
            if math.isfinite(width) and width > 0:
                widths.append(width)
    return widths


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _finite_nonnegative(value: object) -> bool:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(parsed) and parsed >= 0.0


def _rounded(value: float | None) -> float | None:
    return round(value, 9) if value is not None else None
