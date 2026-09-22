from __future__ import annotations

import hashlib
import hmac
import html
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import (
    copy_file_atomic,
    write_json_atomic,
    write_text_atomic,
)
from torii_sumo.core.candidate_contracts import file_sha256

from .enums import GateStatus
from .held_out_review_contracts import (
    BlindReviewDecision,
    BlindedReviewCase,
    BlindedReviewDataset,
    HeldOutAdjudication,
    HeldOutCaseStratum,
    HeldOutEvaluationKey,
    HeldOutReviewMaterial,
    HeldOutReviewMetrics,
    HeldOutReviewPolicy,
    HeldOutReviewReport,
    MachineAssessment,
    UnblindingCaseKey,
)
from .review import ReviewCase


def build_blinded_review_artifacts(
    review_cases: Sequence[ReviewCase],
    *,
    machine_assessments: Mapping[str, MachineAssessment | Mapping[str, Any]],
    case_strata: Mapping[str, HeldOutCaseStratum | Mapping[str, Any]],
    trial_id: str,
    created_at: datetime,
    blinding_seed: str,
    output_dir: Path,
    prefix: str = "held_out_review",
    review_materials: Mapping[
        str, HeldOutReviewMaterial | Mapping[str, Any]
    ] | None = None,
) -> dict[str, Any]:
    """Separate reviewer-visible data from machine labels and true entity IDs."""

    if len(blinding_seed) < 32:
        raise ValueError("Blinding seed must contain at least 32 characters.")
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    case_ids = {case.review_case_id for case in review_cases}
    if set(machine_assessments) != case_ids:
        raise ValueError("Machine assessments must exactly cover the review cases.")
    if set(case_strata) != case_ids:
        raise ValueError("Held-out strata must exactly cover the review cases.")
    provided_materials = dict(review_materials or {})
    if provided_materials and set(provided_materials) != case_ids:
        raise ValueError("Review materials must exactly cover the review cases.")
    blind_cases: list[BlindedReviewCase] = []
    key_cases: list[UnblindingCaseKey] = []
    assessment_paths: list[Path] = []
    reviewer_visible_paths: list[Path] = []
    assessment_dir = destination / "restricted-machine-assessments"
    assessment_dir.mkdir(parents=True, exist_ok=True)
    used_case_codes: set[str] = set()
    for review_case in sorted(review_cases, key=lambda item: item.review_case_id):
        case_code = "case-" + _blind_digest(
            blinding_seed,
            f"case:{review_case.review_case_id}",
            length=12,
        )
        if case_code in used_case_codes:
            raise ValueError("Blinding case-code collision; use a different seed.")
        used_case_codes.add(case_code)
        variant_id_by_code: dict[str, str] = {}
        candidate_sha256_by_code: dict[str, str] = {}
        for variant_id in sorted(review_case.candidate_variant_ids):
            variant_code = "variant-" + _blind_digest(
                blinding_seed,
                f"variant:{review_case.review_case_id}:{variant_id}",
                length=8,
            )
            if variant_code in variant_id_by_code:
                raise ValueError("Blinding variant-code collision; use a different seed.")
            variant_id_by_code[variant_code] = variant_id
            candidate_sha256_by_code[variant_code] = (
                review_case.candidate_sha256_by_variant[variant_id]
            )
        material_fields: dict[str, Any] = {}
        if provided_materials:
            material_value = provided_materials[review_case.review_case_id]
            material = (
                material_value
                if isinstance(material_value, HeldOutReviewMaterial)
                else HeldOutReviewMaterial.model_validate(material_value)
            )
            material_fields, material_paths = _materialize_reviewer_visible_case(
                review_case=review_case,
                material=material,
                case_code=case_code,
                variant_id_by_code=variant_id_by_code,
                candidate_sha256_by_code=candidate_sha256_by_code,
                destination=destination,
            )
            reviewer_visible_paths.extend(material_paths)
        blind_cases.append(
            BlindedReviewCase(
                case_code=case_code,
                source_sha256=review_case.source_sha256,
                candidate_sha256_by_variant_code=candidate_sha256_by_code,
                exact_question=review_case.machine_question,
                required_observations=review_case.required_observations,
                **material_fields,
            )
        )
        assessment_value = machine_assessments[review_case.review_case_id]
        assessment = (
            assessment_value
            if isinstance(assessment_value, MachineAssessment)
            else MachineAssessment.model_validate(assessment_value)
        )
        assessment_path = assessment_dir / f"{case_code}.json"
        write_json_atomic(
            assessment_path,
            assessment.model_dump(mode="json", by_alias=True),
            sort_keys=True,
        )
        assessment_paths.append(assessment_path)
        stratum_value = case_strata[review_case.review_case_id]
        stratum = (
            stratum_value
            if isinstance(stratum_value, HeldOutCaseStratum)
            else HeldOutCaseStratum.model_validate(stratum_value)
        )
        key_cases.append(
            UnblindingCaseKey(
                case_code=case_code,
                review_case_id=review_case.review_case_id,
                variant_id_by_code=variant_id_by_code,
                machine_assessment=assessment,
                machine_assessment_artifact_path=assessment_path.relative_to(
                    destination
                ).as_posix(),
                machine_assessment_artifact_sha256=file_sha256(assessment_path),
                stratum=stratum,
            )
        )
    dataset = BlindedReviewDataset(
        trial_id=trial_id,
        created_at=created_at,
        cases=tuple(blind_cases),
    )
    dataset_path = destination / f"{prefix}.blinded-dataset.json"
    dataset_payload = dataset.model_dump(mode="json", by_alias=True)
    serialized_dataset = json.dumps(dataset_payload, ensure_ascii=False, sort_keys=True)
    forbidden_ids = {
        identifier
        for review_case in review_cases
        for identifier in (
            review_case.review_case_id,
            *review_case.candidate_variant_ids,
        )
    }
    leaked = sorted(identifier for identifier in forbidden_ids if identifier in serialized_dataset)
    if leaked:
        raise ValueError(
            "Reviewer-visible dataset leaks true review/candidate IDs: "
            + ", ".join(leaked)
        )
    if blinding_seed in serialized_dataset:
        raise ValueError("Reviewer-visible dataset leaks the blinding seed.")
    write_json_atomic(dataset_path, dataset_payload, sort_keys=True)
    evaluation_key = HeldOutEvaluationKey(
        trial_id=trial_id,
        blinded_dataset_sha256=file_sha256(dataset_path),
        blinding_seed=blinding_seed,
        cases=tuple(key_cases),
    )
    key_path = destination / f"{prefix}.unblinding-key.json"
    write_json_atomic(
        key_path,
        evaluation_key.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    manifest_path = destination / f"{prefix}.package-manifest.json"
    manifest = {
        "schema": "torii.corridor.blinded-review-package-manifest/v1",
        "trial_id": trial_id,
        "machine_recommendation_hidden": True,
        "peer_decisions_hidden": True,
        "artifacts": [
            {
                "role": "reviewer-visible",
                "path": str(dataset_path),
                "sha256": file_sha256(dataset_path),
            },
            {
                "role": "restricted-unblinding-key",
                "path": str(key_path),
                "sha256": file_sha256(key_path),
            },
            *[
                {
                    "role": "restricted-machine-assessment",
                    "path": str(path),
                    "sha256": file_sha256(path),
                }
                for path in assessment_paths
            ],
            *[
                {
                    "role": "reviewer-visible-case-material",
                    "path": str(path),
                    "sha256": file_sha256(path),
                }
                for path in reviewer_visible_paths
            ],
        ],
    }
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {
        "status": "prepared",
        "trial_id": trial_id,
        "case_count": len(blind_cases),
        "blinded_dataset_file": str(dataset_path),
        "evaluation_key_file": str(key_path),
        "manifest_file": str(manifest_path),
    }


def _materialize_reviewer_visible_case(
    *,
    review_case: ReviewCase,
    material: HeldOutReviewMaterial,
    case_code: str,
    variant_id_by_code: Mapping[str, str],
    candidate_sha256_by_code: Mapping[str, str],
    destination: Path,
) -> tuple[dict[str, Any], list[Path]]:
    if set(material.candidate_artifact_path_by_variant) != set(
        review_case.candidate_variant_ids
    ):
        raise ValueError("Review material candidate paths do not cover variants.")
    case_dir = destination / "reviewer-visible" / case_code
    case_dir.mkdir(parents=True, exist_ok=True)
    candidate_paths: dict[str, str] = {}
    config_paths: dict[str, str] = {}
    written_paths: list[Path] = []
    code_by_variant = {
        variant_id: code for code, variant_id in variant_id_by_code.items()
    }
    overlay_source = Path(material.review_overlay_path).resolve()
    _validate_display_only_overlay(overlay_source)
    overlay_target = case_dir / "review.add.xml"
    copy_file_atomic(overlay_source, overlay_target)
    written_paths.append(overlay_target)
    for variant_id in sorted(review_case.candidate_variant_ids):
        variant_code = code_by_variant[variant_id]
        source = Path(
            material.candidate_artifact_path_by_variant[variant_id]
        ).resolve()
        if not source.is_file():
            raise ValueError(f"Review candidate artifact is missing: {source}")
        expected_sha256 = review_case.candidate_sha256_by_variant[variant_id]
        if file_sha256(source) != expected_sha256:
            raise ValueError("Review candidate artifact hash does not match its variant.")
        target = case_dir / f"{variant_code}.net.xml"
        copy_file_atomic(source, target)
        if file_sha256(target) != expected_sha256:
            raise RuntimeError("Blinded candidate copy failed its hash check.")
        config = case_dir / f"{variant_code}.sumocfg"
        _write_review_config(
            config,
            net_filename=target.name,
            overlay_filename=overlay_target.name,
        )
        candidate_paths[variant_code] = target.relative_to(destination).as_posix()
        config_paths[variant_code] = config.relative_to(destination).as_posix()
        written_paths.extend((target, config))
    html_path = case_dir / "review.html"
    _write_reviewer_html(
        html_path,
        case_code=case_code,
        exact_question=review_case.machine_question,
        required_observations=review_case.required_observations,
        candidate_sha256_by_code=candidate_sha256_by_code,
        candidate_paths=candidate_paths,
        config_paths=config_paths,
        overlay_path=overlay_target.relative_to(destination).as_posix(),
        map_evidence_urls=material.map_evidence_urls,
        osm_attribution=material.osm_attribution,
    )
    written_paths.append(html_path)
    return (
        {
            "candidate_artifact_path_by_variant_code": candidate_paths,
            "review_config_path_by_variant_code": config_paths,
            "review_html_path": html_path.relative_to(destination).as_posix(),
            "review_overlay_path": overlay_target.relative_to(destination).as_posix(),
            "map_evidence_urls": material.map_evidence_urls,
            "osm_attribution": material.osm_attribution,
        },
        written_paths,
    )


def _validate_display_only_overlay(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Review overlay is missing: {path}")
    root = ET.parse(path).getroot()
    tags = {element.tag for element in root.iter()}
    unsupported = sorted(tags - {"additional", "poi", "poly", "param"})
    if root.tag != "additional" or unsupported:
        raise ValueError(
            "Review overlay must be display-only additional.xml; unsupported tags: "
            + ", ".join(unsupported)
        )


def _write_review_config(
    path: Path,
    *,
    net_filename: str,
    overlay_filename: str,
) -> None:
    root = ET.Element("configuration")
    inputs = ET.SubElement(root, "input")
    ET.SubElement(inputs, "net-file", {"value": net_filename})
    ET.SubElement(inputs, "additional-files", {"value": overlay_filename})
    ET.indent(root, space="  ")
    write_text_atomic(
        path,
        ET.tostring(root, encoding="unicode", xml_declaration=True),
    )


def _write_reviewer_html(
    path: Path,
    *,
    case_code: str,
    exact_question: str,
    required_observations: Sequence[str],
    candidate_sha256_by_code: Mapping[str, str],
    candidate_paths: Mapping[str, str],
    config_paths: Mapping[str, str],
    overlay_path: str,
    map_evidence_urls: Sequence[str],
    osm_attribution: str,
) -> None:
    variants = "\n".join(
        "<li><strong>{code}</strong> — SHA-256 <code>{sha}</code>; "
        "<a href=\"{net}\">network</a>; <a href=\"{config}\">review config</a></li>".format(
            code=html.escape(code),
            sha=html.escape(candidate_sha256_by_code[code]),
            net=html.escape(Path(candidate_paths[code]).name),
            config=html.escape(Path(config_paths[code]).name),
        )
        for code in sorted(candidate_sha256_by_code)
    )
    observations = "\n".join(
        f"<li>{html.escape(value)}</li>" for value in required_observations
    )
    map_links = "\n".join(
        f'<li><a href="{html.escape(url)}" rel="noreferrer">Map evidence {index}</a></li>'
        for index, url in enumerate(map_evidence_urls, start=1)
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blind corridor review {html.escape(case_code)}</title>
  <style>
    body {{ font: 16px/1.55 system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #18202a; }}
    .notice {{ padding: 1rem; background: #fff4d6; border-left: 5px solid #d78b00; }}
    code {{ overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <h1>Blind corridor review {html.escape(case_code)}</h1>
  <p class="notice">The additional.xml is display-only. It marks review locations and cannot change roads, connections, right-of-way, or TLS behavior. Machine recommendations and peer decisions are hidden.</p>
  <h2>Question</h2>
  <p>{html.escape(exact_question)}</p>
  <h2>Candidate variants</h2>
  <ul>{variants}</ul>
  <p><a href="{html.escape(Path(overlay_path).name)}">Display-only review overlay</a></p>
  <h2>Required observations</h2>
  <ol>{observations}</ol>
  <h2>External map aids</h2>
  <ul>{map_links}</ul>
  <p>{html.escape(osm_attribution)}. Map links are human-review aids and never approve a candidate automatically.</p>
</body>
</html>
"""
    write_text_atomic(path, document)


def evaluate_held_out_review_trial(
    *,
    policy_file: Path,
    parent_benchmark_file: Path,
    blinded_dataset_file: Path,
    evaluation_key_file: Path,
    decision_files: Sequence[Path],
    adjudication_files: Sequence[Path],
    output_dir: Path,
    prefix: str = "held_out_review",
) -> dict[str, Any]:
    policy_path = policy_file.resolve()
    parent_path = parent_benchmark_file.resolve()
    dataset_path = blinded_dataset_file.resolve()
    key_path = evaluation_key_file.resolve()
    decisions_paths = tuple(path.resolve() for path in decision_files)
    adjudication_paths = tuple(path.resolve() for path in adjudication_files)
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    policy = HeldOutReviewPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    dataset = BlindedReviewDataset.model_validate_json(
        dataset_path.read_text(encoding="utf-8")
    )
    evaluation_key = HeldOutEvaluationKey.model_validate_json(
        key_path.read_text(encoding="utf-8")
    )
    decisions = tuple(
        BlindReviewDecision.model_validate_json(path.read_text(encoding="utf-8"))
        for path in decisions_paths
    )
    adjudications = tuple(
        HeldOutAdjudication.model_validate_json(path.read_text(encoding="utf-8"))
        for path in adjudication_paths
    )
    blockers: list[str] = []
    if file_sha256(parent_path) != policy.parent_benchmark_sha256:
        blockers.append("held_out_policy_parent_benchmark_hash_mismatch")
    for label, trial_id in (
        ("dataset", dataset.trial_id),
        ("evaluation-key", evaluation_key.trial_id),
    ):
        if trial_id != policy.trial_id:
            blockers.append(f"held_out_trial_id_mismatch:{label}")
    if evaluation_key.blinded_dataset_sha256 != file_sha256(dataset_path):
        blockers.append("blinded_dataset_hash_mismatch")
    case_by_code = {case.case_code: case for case in dataset.cases}
    key_by_code = {case.case_code: case for case in evaluation_key.cases}
    if set(case_by_code) != set(key_by_code):
        blockers.append("blinded_dataset_evaluation_key_case_set_mismatch")
    assessment_paths: list[Path] = []
    for case_code in sorted(set(case_by_code) & set(key_by_code)):
        blind_case = case_by_code[case_code]
        key_case = key_by_code[case_code]
        if set(blind_case.candidate_sha256_by_variant_code) != set(
            key_case.variant_id_by_code
        ):
            blockers.append(f"blind_variant_mapping_mismatch:{case_code}")
        assessment_path = (
            key_path.parent / key_case.machine_assessment_artifact_path
        ).resolve()
        try:
            assessment_path.relative_to(key_path.parent)
        except ValueError:
            blockers.append(f"machine_assessment_path_escape:{case_code}")
            continue
        assessment_paths.append(assessment_path)
        if not assessment_path.is_file():
            blockers.append(f"machine_assessment_artifact_missing:{case_code}")
            continue
        if file_sha256(assessment_path) != key_case.machine_assessment_artifact_sha256:
            blockers.append(f"machine_assessment_artifact_hash_mismatch:{case_code}")
            continue
        persisted_assessment = MachineAssessment.model_validate_json(
            assessment_path.read_text(encoding="utf-8")
        )
        if persisted_assessment != key_case.machine_assessment:
            blockers.append(f"machine_assessment_artifact_content_mismatch:{case_code}")

    decision_ids = [decision.decision_id for decision in decisions]
    if len(decision_ids) != len(set(decision_ids)):
        blockers.append("duplicate_blind_review_decision_id")
    adjudication_ids = [item.adjudication_id for item in adjudications]
    if len(adjudication_ids) != len(set(adjudication_ids)):
        blockers.append("duplicate_held_out_adjudication_id")
    decisions_by_case: dict[str, list[BlindReviewDecision]] = defaultdict(list)
    for decision in decisions:
        decisions_by_case[decision.case_code].append(decision)
        if decision.trial_id != policy.trial_id:
            blockers.append(f"review_decision_trial_mismatch:{decision.decision_id}")
        if decision.reviewer_id not in policy.reviewer_ids:
            blockers.append(f"unexpected_reviewer:{decision.decision_id}")
        blind_case = case_by_code.get(decision.case_code)
        if blind_case is None:
            blockers.append(f"review_decision_unknown_case:{decision.decision_id}")
        elif (
            decision.selected_variant_code is not None
            and decision.selected_variant_code
            not in blind_case.candidate_sha256_by_variant_code
        ):
            blockers.append(f"review_decision_unknown_variant:{decision.decision_id}")
    adjudication_by_case: dict[str, list[HeldOutAdjudication]] = defaultdict(list)
    for adjudication in adjudications:
        adjudication_by_case[adjudication.case_code].append(adjudication)
        if adjudication.trial_id != policy.trial_id:
            blockers.append(
                f"adjudication_trial_mismatch:{adjudication.adjudication_id}"
            )
        if adjudication.adjudicator_id != policy.adjudicator_id:
            blockers.append(f"unexpected_adjudicator:{adjudication.adjudication_id}")
        blind_case = case_by_code.get(adjudication.case_code)
        if blind_case is None:
            blockers.append(f"adjudication_unknown_case:{adjudication.adjudication_id}")
        elif (
            adjudication.selected_variant_code is not None
            and adjudication.selected_variant_code
            not in blind_case.candidate_sha256_by_variant_code
        ):
            blockers.append(
                f"adjudication_unknown_variant:{adjudication.adjudication_id}"
            )

    reviewer_sequences: dict[str, list[str]] = {
        reviewer_id: [] for reviewer_id in policy.reviewer_ids
    }
    durations: list[float] = []
    final_labels: dict[str, str] = {}
    completed_review_count = 0
    adjudicated_case_count = 0
    for case_code in sorted(case_by_code):
        case_decisions = decisions_by_case.get(case_code, [])
        if len(case_decisions) != 2:
            blockers.append(
                f"held_out_case_requires_exactly_two_reviews:{case_code}:"
                f"observed_{len(case_decisions)}"
            )
            continue
        by_reviewer = {decision.reviewer_id: decision for decision in case_decisions}
        if set(by_reviewer) != set(policy.reviewer_ids):
            blockers.append(f"held_out_case_reviewer_pair_mismatch:{case_code}")
            continue
        completed_review_count += 2
        for reviewer_id in policy.reviewer_ids:
            decision = by_reviewer[reviewer_id]
            reviewer_sequences[reviewer_id].append(decision.label)
            durations.append(decision.duration_seconds)
        case_adjudications = adjudication_by_case.get(case_code, [])
        if len(case_adjudications) != 1:
            blockers.append(
                f"held_out_case_requires_one_adjudication:{case_code}:"
                f"observed_{len(case_adjudications)}"
            )
            continue
        adjudication = case_adjudications[0]
        if set(adjudication.reviewer_decision_ids) != {
            decision.decision_id for decision in case_decisions
        }:
            blockers.append(f"adjudication_decision_set_mismatch:{case_code}")
            continue
        adjudicated_case_count += 1
        final_labels[case_code] = adjudication.final_label

    case_count = len(case_by_code)
    if case_count < policy.minimum_case_count:
        blockers.append(
            f"held_out_case_count_below_policy:{case_count}/"
            f"{policy.minimum_case_count}"
        )
    stratum_values = [case.stratum for case in key_by_code.values()]
    city_counts = Counter(stratum.city_group for stratum in stratum_values)
    morphology_counts = Counter(stratum.morphology for stratum in stratum_values)
    traffic_side_counts = Counter(
        stratum.traffic_side.value for stratum in stratum_values
    )
    mode_feature_counts = Counter(
        feature
        for stratum in stratum_values
        for feature in set(stratum.mode_features)
    )
    if len(city_counts) < policy.minimum_city_group_count:
        blockers.append("held_out_city_group_count_below_policy")
    if len(morphology_counts) < policy.minimum_morphology_count:
        blockers.append("held_out_morphology_count_below_policy")
    for city_group, count in city_counts.items():
        if count < policy.minimum_cases_per_city_group:
            blockers.append(
                f"held_out_city_group_case_count_below_policy:{city_group}:"
                f"{count}/{policy.minimum_cases_per_city_group}"
            )
    missing_traffic_sides = {
        side.value for side in policy.required_traffic_sides
    } - set(traffic_side_counts)
    if missing_traffic_sides:
        blockers.append(
            "held_out_required_traffic_side_missing:"
            + ",".join(sorted(missing_traffic_sides))
        )
    missing_mode_features = set(policy.required_mode_features) - set(
        mode_feature_counts
    )
    if missing_mode_features:
        blockers.append(
            "held_out_required_mode_feature_missing:"
            + ",".join(sorted(missing_mode_features))
        )
    raw_agreement, cohen_kappa = _reviewer_agreement(
        reviewer_sequences[policy.reviewer_ids[0]],
        reviewer_sequences[policy.reviewer_ids[1]],
    )
    median_seconds = statistics.median(durations) if durations else None
    metric_values = _machine_metrics(final_labels, key_by_code, case_count)
    if raw_agreement is None or raw_agreement < policy.minimum_raw_agreement:
        blockers.append("held_out_raw_agreement_below_policy")
    if cohen_kappa is None:
        blockers.append("held_out_cohen_kappa_undefined")
    elif cohen_kappa < policy.minimum_cohen_kappa:
        blockers.append("held_out_cohen_kappa_below_policy")
    if median_seconds is None:
        blockers.append("held_out_review_time_missing")
    elif median_seconds > policy.maximum_median_review_seconds:
        blockers.append("held_out_median_review_time_above_policy")
    for metric_name, threshold in (
        ("attention_precision", policy.minimum_attention_precision),
        ("attention_recall", policy.minimum_attention_recall),
        ("auto_precision", policy.minimum_auto_precision),
    ):
        value = metric_values[metric_name]
        if value is None:
            blockers.append(f"held_out_{metric_name}_undefined")
        elif value < threshold:
            blockers.append(f"held_out_{metric_name}_below_policy")
    if (
        metric_values["safety_critical_false_negative_count"]
        > policy.maximum_safety_critical_false_negatives
    ):
        blockers.append("held_out_safety_critical_false_negative")
    metrics = HeldOutReviewMetrics(
        case_count=case_count,
        completed_review_count=completed_review_count,
        adjudicated_case_count=adjudicated_case_count,
        raw_agreement=raw_agreement,
        cohen_kappa=cohen_kappa,
        median_review_seconds=median_seconds,
        attention_precision=metric_values["attention_precision"],
        attention_recall=metric_values["attention_recall"],
        auto_precision=metric_values["auto_precision"],
        auto_coverage=metric_values["auto_coverage"],
        abstention_rate=metric_values["abstention_rate"],
        safety_critical_false_negative_count=metric_values[
            "safety_critical_false_negative_count"
        ],
        adjudicated_label_counts=dict(sorted(Counter(final_labels.values()).items())),
        city_group_case_counts=dict(sorted(city_counts.items())),
        morphology_case_counts=dict(sorted(morphology_counts.items())),
        traffic_side_case_counts=dict(sorted(traffic_side_counts.items())),
        mode_feature_case_counts=dict(sorted(mode_feature_counts.items())),
    )
    blockers = list(dict.fromkeys(blockers))
    status = GateStatus.BLOCKED if blockers else GateStatus.PASS
    report = HeldOutReviewReport(
        trial_id=policy.trial_id,
        policy_sha256=file_sha256(policy_path),
        blinded_dataset_sha256=file_sha256(dataset_path),
        evaluation_key_sha256=file_sha256(key_path),
        status=status,
        automatic_promotion_gate=(
            GateStatus.PASS if status is GateStatus.PASS else GateStatus.BLOCKED
        ),
        metrics=metrics,
        blockers=tuple(blockers),
    )
    report_path = destination / f"{prefix}.report.json"
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    input_paths = (
        policy_path,
        parent_path,
        dataset_path,
        key_path,
        *assessment_paths,
        *decisions_paths,
        *adjudication_paths,
    )
    manifest_path = destination / f"{prefix}.manifest.json"
    write_json_atomic(
        manifest_path,
        {
            "schema": "torii.corridor.held-out-review-manifest/v1",
            "trial_id": policy.trial_id,
            "status": status.value,
            "inputs": [
                {"path": str(path), "sha256": file_sha256(path)}
                for path in input_paths
            ],
            "report": {
                "path": str(report_path),
                "sha256": file_sha256(report_path),
            },
        },
        sort_keys=True,
    )
    return {
        **report.model_dump(mode="json", by_alias=True),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
    }


def _blind_digest(seed: str, value: str, *, length: int) -> str:
    return hmac.new(
        seed.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:length]


def _reviewer_agreement(
    first: Sequence[str],
    second: Sequence[str],
) -> tuple[float | None, float | None]:
    if not first or len(first) != len(second):
        return None, None
    total = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / total
    first_counts = Counter(first)
    second_counts = Counter(second)
    labels = set(first_counts) | set(second_counts)
    expected = sum(
        (first_counts[label] / total) * (second_counts[label] / total)
        for label in labels
    )
    if expected >= 1.0:
        return round(observed, 6), None
    kappa = (observed - expected) / (1.0 - expected)
    return round(observed, 6), round(kappa, 6)


def _machine_metrics(
    final_labels: Mapping[str, str],
    key_by_code: Mapping[str, UnblindingCaseKey],
    case_count: int,
) -> dict[str, Any]:
    true_positive = 0
    false_positive = 0
    false_negative = 0
    auto_count = 0
    auto_correct = 0
    abstention_count = 0
    safety_false_negative_count = 0
    for case_code, final_label in final_labels.items():
        key_case = key_by_code.get(case_code)
        if key_case is None:
            continue
        assessment = key_case.machine_assessment
        machine_attention = assessment.machine_label != "acceptable"
        gold_attention = final_label != "acceptable"
        if machine_attention and gold_attention:
            true_positive += 1
        elif machine_attention:
            false_positive += 1
        elif gold_attention:
            false_negative += 1
        if assessment.machine_label == "acceptable":
            auto_count += 1
            if final_label == "acceptable":
                auto_correct += 1
        if assessment.machine_label == "ambiguous":
            abstention_count += 1
        if (
            assessment.safety_critical
            and assessment.machine_label == "acceptable"
            and final_label == "defect"
        ):
            safety_false_negative_count += 1
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    return {
        "attention_precision": (
            round(true_positive / precision_denominator, 6)
            if precision_denominator
            else None
        ),
        "attention_recall": (
            round(true_positive / recall_denominator, 6)
            if recall_denominator
            else None
        ),
        "auto_precision": (
            round(auto_correct / auto_count, 6) if auto_count else None
        ),
        "auto_coverage": round(auto_count / case_count, 6) if case_count else 0.0,
        "abstention_rate": (
            round(abstention_count / case_count, 6) if case_count else 0.0
        ),
        "safety_critical_false_negative_count": safety_false_negative_count,
    }
