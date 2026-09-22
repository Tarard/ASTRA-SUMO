from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, Sha256, StableToken
from .enums import GateStatus, TrafficSide
from .ids import require_stable_id, stable_id
from .run_identity import CodeProducerIdentity


Morphology = Literal[
    "grid",
    "historic-core",
    "suburban-arterial",
    "divided-arterial",
    "ramp-interchange",
    "tram-rail",
    "bridge-tunnel",
    "multimodal",
]

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")


class GeographicBbox(ContractModel):
    west: float = Field(ge=-180.0, le=180.0)
    south: float = Field(ge=-90.0, le=90.0)
    east: float = Field(ge=-180.0, le=180.0)
    north: float = Field(ge=-90.0, le=90.0)

    @model_validator(mode="after")
    def validate_bbox(self) -> GeographicBbox:
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("Geographic bbox bounds are not ordered.")
        return self

    def as_sumo_string(self) -> str:
        return f"{self.west:.6f},{self.south:.6f},{self.east:.6f},{self.north:.6f}"


class HeldOutNetworkBuildProfile(ContractModel):
    netconvert_profile: Literal["reference_visual_detail"] = (
        "reference_visual_detail"
    )
    include_railway: Literal[True] = True
    clip_source_ways_to_bbox: Literal[False] = False
    allowed_highways: tuple[str, ...]
    allowed_railways: tuple[str, ...]
    routeability_vehicle_count: int = Field(ge=1)
    routeability_seed: int

    @model_validator(mode="after")
    def validate_profile(self) -> HeldOutNetworkBuildProfile:
        for values, label in (
            (self.allowed_highways, "highways"),
            (self.allowed_railways, "railways"),
        ):
            if not values or len(values) != len(set(values)):
                raise ValueError(f"Held-out {label} must be non-empty and unique.")
            if tuple(sorted(values)) != values:
                raise ValueError(f"Held-out {label} must be sorted for reproducibility.")
        return self


class HeldOutCityExtract(ContractModel):
    source_id: str
    city_group: str
    traffic_side: TrafficSide
    provider: Literal["BBBike"] = "BBBike"
    pbf_url: str
    checksum_url: str
    provider_md5: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_content_length_bytes: int = Field(gt=0)
    expected_last_modified_http: str
    expected_etag: str

    @model_validator(mode="after")
    def validate_source(self) -> HeldOutCityExtract:
        if not _SLUG_RE.fullmatch(self.source_id):
            raise ValueError("Held-out source IDs must be lowercase slugs.")
        if not _SLUG_RE.fullmatch(self.city_group):
            raise ValueError("Held-out city groups must be lowercase slugs.")
        if self.traffic_side is TrafficSide.UNKNOWN:
            raise ValueError("Held-out city extracts require an explicit traffic side.")
        if not self.pbf_url.startswith("https://") or not self.checksum_url.startswith(
            "https://"
        ):
            raise ValueError("Held-out city extracts require HTTPS source URLs.")
        if not _MD5_RE.fullmatch(self.provider_md5):
            raise ValueError("Held-out provider checksums must be lowercase MD5.")
        if not self.expected_last_modified_http or not self.expected_etag:
            raise ValueError("Held-out city extracts require frozen HTTP identity fields.")
        return self


class HeldOutCorridorSelection(ContractModel):
    selection_id: StableToken
    corridor_key: str
    city_source_id: str
    label: str
    center_lat: float = Field(ge=-90.0, le=90.0)
    center_lon: float = Field(ge=-180.0, le=180.0)
    bbox: GeographicBbox
    morphology: Morphology
    preregistered_feature_targets: tuple[str, ...]
    selection_basis: str

    def identity_payload(self) -> dict[str, object]:
        return {
            "corridor_key": self.corridor_key,
            "city_source_id": self.city_source_id,
            "center_lat": self.center_lat,
            "center_lon": self.center_lon,
            "bbox": self.bbox.model_dump(mode="json", by_alias=True),
            "morphology": self.morphology,
            "preregistered_feature_targets": self.preregistered_feature_targets,
        }

    @model_validator(mode="after")
    def validate_selection(self) -> HeldOutCorridorSelection:
        require_stable_id(self.selection_id, kind="scope")
        if self.selection_id != stable_id("scope", self.identity_payload()):
            raise ValueError("selection_id does not match the corridor selection.")
        for value, label in (
            (self.corridor_key, "corridor key"),
            (self.city_source_id, "city source ID"),
        ):
            if not _SLUG_RE.fullmatch(value):
                raise ValueError(f"Held-out {label} must be a lowercase slug.")
        if not (
            self.bbox.south <= self.center_lat <= self.bbox.north
            and self.bbox.west <= self.center_lon <= self.bbox.east
        ):
            raise ValueError("Corridor center must lie inside its frozen bbox.")
        if not self.label or not self.selection_basis:
            raise ValueError("Held-out selections require a label and selection basis.")
        if not self.preregistered_feature_targets:
            raise ValueError("Held-out selections require feature targets.")
        if len(self.preregistered_feature_targets) != len(
            set(self.preregistered_feature_targets)
        ):
            raise ValueError("Held-out feature targets must be unique.")
        return self


class HeldOutCorpusSpec(ContractModel):
    schema_id: str = "torii.corridor.held-out-corpus/v1"
    corpus_id: StableToken
    held_out_review_policy_sha256: Sha256
    parent_benchmark_sha256: Sha256
    provider_attribution: Literal["© OpenStreetMap contributors"]
    provider_license: Literal["Open Data Commons Open Database License (ODbL)"]
    provider_license_url: Literal["https://opendatacommons.org/licenses/odbl/"]
    snapshot_selection_cutoff: str
    minimum_case_count: int = Field(ge=1)
    minimum_city_group_count: int = Field(ge=1)
    minimum_morphology_count: int = Field(ge=1)
    minimum_cases_per_city_group: int = Field(ge=1)
    required_traffic_sides: tuple[TrafficSide, ...]
    required_mode_features: tuple[str, ...]
    network_build_profile: HeldOutNetworkBuildProfile
    city_extracts: tuple[HeldOutCityExtract, ...]
    corridors: tuple[HeldOutCorridorSelection, ...]

    def identity_payload(self) -> dict[str, object]:
        return {
            "held_out_review_policy_sha256": self.held_out_review_policy_sha256,
            "parent_benchmark_sha256": self.parent_benchmark_sha256,
            "provider_attribution": self.provider_attribution,
            "provider_license": self.provider_license,
            "provider_license_url": self.provider_license_url,
            "snapshot_selection_cutoff": self.snapshot_selection_cutoff,
            "minimum_case_count": self.minimum_case_count,
            "minimum_city_group_count": self.minimum_city_group_count,
            "minimum_morphology_count": self.minimum_morphology_count,
            "minimum_cases_per_city_group": self.minimum_cases_per_city_group,
            "required_traffic_sides": self.required_traffic_sides,
            "required_mode_features": self.required_mode_features,
            "network_build_profile": self.network_build_profile.model_dump(
                mode="json", by_alias=True
            ),
            "city_extracts": [
                source.model_dump(mode="json", by_alias=True)
                for source in self.city_extracts
            ],
            "corridors": [
                corridor.model_dump(mode="json", by_alias=True)
                for corridor in self.corridors
            ],
        }

    @model_validator(mode="after")
    def validate_spec(self) -> HeldOutCorpusSpec:
        require_stable_id(self.corpus_id, kind="manifest")
        if self.corpus_id != stable_id("manifest", self.identity_payload()):
            raise ValueError("corpus_id does not match the held-out corpus payload.")
        source_ids = [source.source_id for source in self.city_extracts]
        city_groups = [source.city_group for source in self.city_extracts]
        if not source_ids or len(source_ids) != len(set(source_ids)):
            raise ValueError("Held-out city source IDs must be non-empty and unique.")
        if len(city_groups) != len(set(city_groups)):
            raise ValueError("Held-out city groups must have one frozen extract each.")
        keys = [case.corridor_key for case in self.corridors]
        ids = [case.selection_id for case in self.corridors]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("Held-out corridor identities must be unique.")
        if len(self.corridors) < self.minimum_case_count:
            raise ValueError("Held-out corpus does not meet its minimum case count.")
        source_by_id = {source.source_id: source for source in self.city_extracts}
        counts: dict[str, int] = {}
        for case in self.corridors:
            source = source_by_id.get(case.city_source_id)
            if source is None:
                raise ValueError(
                    f"Corridor references unknown city source: {case.city_source_id}"
                )
            counts[source.city_group] = counts.get(source.city_group, 0) + 1
        if len(counts) < self.minimum_city_group_count:
            raise ValueError("Held-out corpus has too few city groups.")
        if any(count < self.minimum_cases_per_city_group for count in counts.values()):
            raise ValueError("Held-out corpus has an undersized city group.")
        if len({case.morphology for case in self.corridors}) < self.minimum_morphology_count:
            raise ValueError("Held-out corpus has too few morphology strata.")
        observed_sides = {source.traffic_side for source in self.city_extracts}
        if not set(self.required_traffic_sides).issubset(observed_sides):
            raise ValueError("Held-out corpus does not cover required traffic sides.")
        targeted_features = {
            feature
            for case in self.corridors
            for feature in case.preregistered_feature_targets
        }
        if not set(self.required_mode_features).issubset(targeted_features):
            raise ValueError("Held-out corpus does not target every required mode feature.")
        return self


class DownloadedCityExtract(ContractModel):
    source_id: str
    status: GateStatus
    path: str
    sha256: Sha256
    md5: str = Field(pattern=r"^[0-9a-f]{32}$")
    content_length_bytes: int = Field(gt=0)
    provider_md5_matched: bool
    expected_length_matched: bool
    source_reused: bool
    observed_last_modified_http: str
    observed_etag: str

    @model_validator(mode="after")
    def validate_download(self) -> DownloadedCityExtract:
        if self.status is GateStatus.PASS and not (
            self.provider_md5_matched and self.expected_length_matched
        ):
            raise ValueError("Passing city extracts must close provider identity.")
        return self


class CroppedCorridorSnapshot(ContractModel):
    selection_id: StableToken
    corridor_key: str
    status: GateStatus
    city_extract_sha256: Sha256
    path: str
    sha256: Sha256
    selected_way_count: int = Field(ge=0)
    selected_restriction_count: int = Field(ge=0)
    observed_feature_counts: dict[str, int]
    unconfirmed_preregistered_features: tuple[str, ...]
    reference_complete: bool

    @model_validator(mode="after")
    def validate_snapshot(self) -> CroppedCorridorSnapshot:
        require_stable_id(self.selection_id, kind="scope")
        if self.status is GateStatus.PASS and (
            self.unconfirmed_preregistered_features or not self.reference_complete
        ):
            raise ValueError("Passing corridor snapshots must close all frozen claims.")
        return self


class HeldOutCorpusSnapshotReport(ContractModel):
    schema_id: str = "torii.corridor.held-out-corpus-snapshot-report/v1"
    corpus_id: StableToken
    corpus_spec_sha256: Sha256
    held_out_review_policy_sha256: Sha256
    status: GateStatus
    city_extracts: tuple[DownloadedCityExtract, ...]
    corridors: tuple[CroppedCorridorSnapshot, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_report(self) -> HeldOutCorpusSnapshotReport:
        require_stable_id(self.corpus_id, kind="manifest")
        if self.status is GateStatus.PASS and (
            self.blockers
            or any(item.status is not GateStatus.PASS for item in self.city_extracts)
            or any(item.status is not GateStatus.PASS for item in self.corridors)
        ):
            raise ValueError("Passing corpus reports cannot hide unresolved evidence.")
        return self


class HeldOutCorridorMachineResult(ContractModel):
    schema_id: str = "torii.corridor.held-out-corridor-machine-result/v1"
    selection_id: StableToken
    corridor_key: str
    pipeline_status: GateStatus
    machine_label: Literal["defect", "acceptable", "ambiguous"]
    source_osm_sha256: Sha256
    source_osm_immutable: bool
    net_sha256: Sha256 | None = None
    netconvert_status: str
    sumo_load_status: str
    routeability_status: str
    connection_mode_status: str
    independent_safety_status: GateStatus
    applicability_decision: Literal["in-domain", "out-of-domain", "invalid"]
    review_case_id: StableToken | None = None
    artifact_sha256_by_path: dict[str, Sha256]
    finding_categories: tuple[str, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_result(self) -> HeldOutCorridorMachineResult:
        require_stable_id(self.selection_id, kind="scope")
        if self.review_case_id is not None:
            require_stable_id(self.review_case_id, kind="review")
        if self.pipeline_status is GateStatus.PASS and (
            not self.source_osm_immutable
            or self.net_sha256 is None
            or self.review_case_id is None
            or self.blockers
        ):
            raise ValueError("Passing corridor evidence must close identity and review.")
        return self


class HeldOutCorpusMachineReport(ContractModel):
    schema_id: str = "torii.corridor.held-out-corpus-machine-report/v2"
    corpus_id: StableToken
    corpus_spec_sha256: Sha256
    snapshot_report_sha256: Sha256
    certification_envelope_sha256: Sha256
    toolchain_lock_path: str
    toolchain_lock_sha256: Sha256
    run_identity_id: StableToken
    run_identity_path: str
    run_identity_sha256: Sha256
    evidence_build_status: GateStatus
    automatic_promotion_gate: Literal["blocked"] = "blocked"
    expected_case_count: int = Field(ge=1)
    processed_case_count: int = Field(ge=0)
    results: tuple[HeldOutCorridorMachineResult, ...]
    blinded_dataset_path: str | None = None
    blinded_dataset_sha256: Sha256 | None = None
    evaluation_key_path: str | None = None
    evaluation_key_sha256: Sha256 | None = None
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_machine_report(self) -> HeldOutCorpusMachineReport:
        require_stable_id(self.corpus_id, kind="manifest")
        require_stable_id(self.run_identity_id, kind="toolchain")
        if not self.run_identity_path.strip() or not self.toolchain_lock_path.strip():
            raise ValueError(
                "Held-out machine reports require run-identity and toolchain paths."
            )
        if self.processed_case_count != len(self.results):
            raise ValueError("Held-out machine result count does not close.")
        paired = (
            self.blinded_dataset_path,
            self.blinded_dataset_sha256,
            self.evaluation_key_path,
            self.evaluation_key_sha256,
        )
        if any(value is None for value in paired) and any(
            value is not None for value in paired
        ):
            raise ValueError("Held-out blinded artifact identities must be complete.")
        if self.evidence_build_status is GateStatus.PASS and (
            self.processed_case_count != self.expected_case_count
            or any(
                result.pipeline_status is not GateStatus.PASS
                for result in self.results
            )
            or self.blockers
        ):
            raise ValueError("Passing held-out evidence cannot hide missing cases.")
        return self


class HeldOutMachineArtifactIdentity(ContractModel):
    path: str
    sha256: Sha256


class HeldOutCorpusMachineManifest(ContractModel):
    schema_id: str = "torii.corridor.held-out-corpus-machine-manifest/v2"
    corpus_id: StableToken
    evidence_build_status: GateStatus
    automatic_promotion_gate: Literal["blocked"] = "blocked"
    human_review_decisions_present: Literal[False] = False
    toolchain_lock_path: str
    toolchain_lock_sha256: Sha256
    run_identity_id: StableToken
    run_identity_path: str
    run_identity_sha256: Sha256
    producer: CodeProducerIdentity
    artifacts: tuple[HeldOutMachineArtifactIdentity, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> HeldOutCorpusMachineManifest:
        require_stable_id(self.corpus_id, kind="manifest")
        require_stable_id(self.run_identity_id, kind="toolchain")
        by_path = {artifact.path: artifact.sha256 for artifact in self.artifacts}
        if len(by_path) != len(self.artifacts):
            raise ValueError("Held-out machine manifest artifact paths must be unique.")
        required = {
            self.toolchain_lock_path: self.toolchain_lock_sha256,
            self.run_identity_path: self.run_identity_sha256,
        }
        missing_or_mismatched = {
            path: digest
            for path, digest in required.items()
            if by_path.get(path) != digest
        }
        if missing_or_mismatched:
            raise ValueError(
                "Held-out machine manifest does not close provenance artifacts: "
                f"{sorted(missing_or_mismatched)}"
            )
        return self
