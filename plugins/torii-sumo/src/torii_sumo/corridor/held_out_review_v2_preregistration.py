from __future__ import annotations

import hashlib
from pathlib import Path

from torii_sumo.core.candidate_contracts import file_sha256

from .held_out_corpus_contracts import (
    GeographicBbox,
    HeldOutCorpusSpec,
    HeldOutCorridorSelection,
)
from .enums import GateStatus
from .held_out_review_v2_contracts import (
    AuditAttentionCohortPolicyV2,
    HeldOutReplacementPolicyV2,
    HeldOutReserveCorpusV2,
    HeldOutReviewParentV2,
    HeldOutReviewPolicyV2,
    HeldOutSourceSnapshotProtocolV2,
    ProspectiveSafePassCohortPolicyV2,
    ReplacementStratumV2,
    ReserveCorridorCandidateV2,
    ReserveReplacementSlotV2,
    ReviewWitnessSamplingPolicyV2,
    _ALLOWED_REPLACEMENT_FAILURE_REASONS,
    _PROHIBITED_REPLACEMENT_SIGNALS,
    _REQUIRED_REPLACEMENT_MATCH_FIELDS,
)
from .ids import stable_id
from .review_compression_contracts import RWC1_FROZEN_SAMPLING_SEED
from .review_compression import ReviewCompressionPolicy


V2_PUBLIC_REPLACEMENT_SEED = "torii-stage1m-held-out-review-v2-deterministic-reserve-selection-20260714"
V2_BLINDING_SEED_SHA256 = "6c05c9f65ebe101b5b3898caf8bf4e733b821ab3cd40a46efef0eedcafef9b6e"

_RESERVE_CANDIDATES = {
    "london-kings-cross": (
        ("london-euston", "Euston", 51.5282, -0.1337, 0.0060, 0.0097),
        (
            "london-liverpool-street",
            "Liverpool Street",
            51.5178,
            -0.0823,
            0.0060,
            0.0097,
        ),
        (
            "london-victoria-station",
            "Victoria station",
            51.4952,
            -0.1441,
            0.0060,
            0.0097,
        ),
    ),
    "melbourne-royal-parade": (
        (
            "melbourne-st-kilda-domain",
            "St Kilda Road and Domain",
            -37.8298,
            144.9713,
            0.0060,
            0.0076,
        ),
        (
            "melbourne-flemington-elliott",
            "Flemington Road and Elliott Avenue",
            -37.7932,
            144.9490,
            0.0060,
            0.0076,
        ),
        (
            "melbourne-dandenong-orrong",
            "Dandenong Road and Orrong Road",
            -37.8598,
            145.0120,
            0.0060,
            0.0076,
        ),
    ),
    "sydney-cross-city-tunnel": (
        (
            "sydney-lane-cove-tunnel",
            "Lane Cove tunnel",
            -33.8148,
            151.1815,
            0.0060,
            0.0073,
        ),
        (
            "sydney-m5-east-kingsgrove",
            "M5 East at Kingsgrove",
            -33.9410,
            151.1015,
            0.0060,
            0.0073,
        ),
        (
            "sydney-eastern-distributor-surry-hills",
            "Eastern Distributor at Surry Hills",
            -33.8834,
            151.2124,
            0.0060,
            0.0073,
        ),
    ),
}


def build_held_out_reserve_corpus_v2(
    *,
    parent_corpus_file: Path,
) -> HeldOutReserveCorpusV2:
    corpus_path = parent_corpus_file.resolve()
    corpus = HeldOutCorpusSpec.model_validate_json(corpus_path.read_text(encoding="utf-8"))
    selection_by_key = {selection.corridor_key: selection for selection in corpus.corridors}
    traffic_side_by_source = {source.source_id: source.traffic_side for source in corpus.city_extracts}
    slots: list[ReserveReplacementSlotV2] = []
    for invalid_key in sorted(_RESERVE_CANDIDATES):
        invalid = selection_by_key[invalid_key]
        traffic_side = traffic_side_by_source[invalid.city_source_id]
        stratum = ReplacementStratumV2(
            city_source_id=invalid.city_source_id,
            morphology=invalid.morphology,
            traffic_side=traffic_side,
            mode_features=invalid.preregistered_feature_targets,
        )
        candidates = tuple(
            ReserveCorridorCandidateV2(
                selection=_build_candidate_selection(
                    candidate,
                    city_source_id=invalid.city_source_id,
                    morphology=invalid.morphology,
                    feature_targets=invalid.preregistered_feature_targets,
                ),
                traffic_side=traffic_side,
            )
            for candidate in _RESERVE_CANDIDATES[invalid_key]
        )
        slots.append(
            ReserveReplacementSlotV2(
                invalid_corridor_key=invalid_key,
                invalid_selection_id=invalid.selection_id,
                invalid_reason="semantic-replay-invalid",
                required_stratum=stratum,
                candidates=candidates,
            )
        )
    payload = {
        "parent_corpus_sha256": file_sha256(corpus_path),
        "provider_attribution": "© OpenStreetMap contributors",
        "slots": tuple(slots),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = HeldOutReserveCorpusV2.model_construct(
        reserve_corpus_id=stable_id("manifest", {"pending": True}),
        **payload,
    )
    return HeldOutReserveCorpusV2(
        reserve_corpus_id=stable_id("manifest", provisional.identity_payload()),
        **payload,
    )


def build_held_out_replacement_policy_v2(
    *,
    parent_corpus_file: Path,
    reserve_corpus_file: Path,
) -> HeldOutReplacementPolicyV2:
    payload = {
        "parent_corpus_sha256": file_sha256(parent_corpus_file.resolve()),
        "reserve_corpus_sha256": file_sha256(reserve_corpus_file.resolve()),
        "public_selection_seed": V2_PUBLIC_REPLACEMENT_SEED,
        "ranking_algorithm": ("sha256(seed|invalid-corridor-key|selection-id)-ascending"),
        "required_match_fields": _REQUIRED_REPLACEMENT_MATCH_FIELDS,
        "allowed_technical_failure_reasons": (_ALLOWED_REPLACEMENT_FAILURE_REASONS),
        "prohibited_selection_signals": _PROHIBITED_REPLACEMENT_SIGNALS,
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = HeldOutReplacementPolicyV2.model_construct(
        policy_id=stable_id("policy", {"pending": True}),
        **payload,
    )
    return HeldOutReplacementPolicyV2(
        policy_id=stable_id("policy", provisional.identity_payload()),
        **payload,
    )


def build_held_out_source_snapshot_protocol_v2() -> HeldOutSourceSnapshotProtocolV2:
    payload = {"automatic_promotion_gate": GateStatus.BLOCKED}
    provisional = HeldOutSourceSnapshotProtocolV2.model_construct(
        protocol_id=stable_id("policy", {"pending": True}),
        **payload,
    )
    return HeldOutSourceSnapshotProtocolV2(
        protocol_id=stable_id("policy", provisional.identity_payload()),
        **payload,
    )


def build_review_witness_sampling_policy_v2() -> ReviewWitnessSamplingPolicyV2:
    rwc_policy = ReviewCompressionPolicy.build_default()
    payload = {
        "rwc_policy_id": rwc_policy.policy_id,
        "rwc_sampling_seed_sha256": hashlib.sha256(RWC1_FROZEN_SAMPLING_SEED.encode("utf-8")).hexdigest(),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = ReviewWitnessSamplingPolicyV2.model_construct(
        policy_id=stable_id("policy", {"pending": True}),
        **payload,
    )
    return ReviewWitnessSamplingPolicyV2(
        policy_id=stable_id("policy", provisional.identity_payload()),
        **payload,
    )


def build_held_out_review_parent_v2(
    *,
    base_benchmark_file: Path,
    held_out_corpus_file: Path,
    reserve_corpus_file: Path,
    replacement_policy_file: Path,
    sampling_policy_file: Path,
    lossless_compression_schema_file: Path,
) -> HeldOutReviewParentV2:
    payload = {
        "base_benchmark_sha256": file_sha256(base_benchmark_file.resolve()),
        "held_out_corpus_v1_sha256": file_sha256(held_out_corpus_file.resolve()),
        "reserve_corpus_sha256": file_sha256(reserve_corpus_file.resolve()),
        "replacement_policy_sha256": file_sha256(replacement_policy_file.resolve()),
        "sampling_policy_sha256": file_sha256(sampling_policy_file.resolve()),
        "lossless_compression_schema_sha256": file_sha256(lossless_compression_schema_file.resolve()),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = HeldOutReviewParentV2.model_construct(
        parent_id=stable_id("manifest", {"pending": True}),
        **payload,
    )
    return HeldOutReviewParentV2(
        parent_id=stable_id("manifest", provisional.identity_payload()),
        **payload,
    )


def build_held_out_review_policy_v2(
    *,
    parent_review_benchmark_file: Path,
    reserve_corpus_file: Path,
    replacement_policy_file: Path,
    sampling_policy_file: Path,
) -> HeldOutReviewPolicyV2:
    payload = {
        "parent_review_benchmark_sha256": file_sha256(parent_review_benchmark_file.resolve()),
        "reserve_corpus_sha256": file_sha256(reserve_corpus_file.resolve()),
        "replacement_policy_sha256": file_sha256(replacement_policy_file.resolve()),
        "sampling_policy_sha256": file_sha256(sampling_policy_file.resolve()),
        "blinding_seed_sha256": V2_BLINDING_SEED_SHA256,
        "reviewer_ids": (
            stable_id("review", {"trial": "v2", "role": "reviewer-a"}),
            stable_id("review", {"trial": "v2", "role": "reviewer-b"}),
        ),
        "adjudicator_id": stable_id("review", {"trial": "v2", "role": "adjudicator"}),
        "replay_invalid_corridor_keys": tuple(sorted(_RESERVE_CANDIDATES)),
        "audit_attention": AuditAttentionCohortPolicyV2(),
        "prospective_safe_pass": ProspectiveSafePassCohortPolicyV2(),
        "automatic_promotion_gate": GateStatus.BLOCKED,
    }
    provisional = HeldOutReviewPolicyV2.model_construct(
        trial_id=stable_id("review", {"pending": True}),
        **payload,
    )
    return HeldOutReviewPolicyV2(
        trial_id=stable_id("review", provisional.identity_payload()),
        **payload,
    )


def _build_candidate_selection(
    item: tuple[object, ...],
    *,
    city_source_id: str,
    morphology: str,
    feature_targets: tuple[str, ...],
) -> HeldOutCorridorSelection:
    corridor_key, label, center_lat, center_lon, half_lat, half_lon = item
    bbox = GeographicBbox(
        west=round(float(center_lon) - float(half_lon), 6),
        south=round(float(center_lat) - float(half_lat), 6),
        east=round(float(center_lon) + float(half_lon), 6),
        north=round(float(center_lat) + float(half_lat), 6),
    )
    payload = {
        "corridor_key": str(corridor_key),
        "city_source_id": city_source_id,
        "center_lat": float(center_lat),
        "center_lon": float(center_lon),
        "bbox": bbox.model_dump(mode="json", by_alias=True),
        "morphology": morphology,
        "preregistered_feature_targets": feature_targets,
    }
    return HeldOutCorridorSelection(
        selection_id=stable_id("scope", payload),
        **payload,
        label=str(label),
        selection_basis=(
            "Landmark-centered reserve corridor frozen before replacement machine "
            "execution. Eligibility is determined only by the declared stratum "
            "and technical reproducibility, never by findings or human visibility."
        ),
    )
