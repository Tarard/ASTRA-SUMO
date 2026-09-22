from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.connection_mode_audit import (
    build_network_connection_mode_audit,
)
from torii_sumo.core.osm_network import build_osm_network, google_maps_url
from torii_sumo.core.routeability_audit import run_routeability_audit
from torii_sumo.core.sumo_commands import run_sumo_load_audit

from .applicability import (
    CertificationEnvelope,
    evaluate_certification_applicability,
)
from .calibration import build_connection_mode_calibration_artifact
from .canonicalizer import canonicalize_net_xml_file
from .conflict_graph import audit_independent_movement_safety
from .enums import GateStatus
from .held_out_corpus_contracts import (
    CroppedCorridorSnapshot,
    HeldOutCorpusMachineManifest,
    HeldOutCorpusMachineReport,
    HeldOutCorpusSnapshotReport,
    HeldOutCorpusSpec,
    HeldOutCorridorMachineResult,
    HeldOutMachineArtifactIdentity,
)
from .held_out_review_contracts import (
    HeldOutCaseStratum,
    HeldOutReviewMaterial,
    HeldOutReviewPolicy,
    MachineAssessment,
)
from .held_out_review_runner import build_blinded_review_artifacts
from .ids import stable_id
from .net_replay import compare_netconvert_replay
from .pedestrian_control_census import (
    build_effective_tls_program_inventory,
    classify_controlled_pedestrian_bindings,
)
from .review import ReviewCase
from .review_compression import (
    RWC1_FROZEN_SAMPLING_SEED,
    build_lossless_review_compression,
)
from .run_identity import capture_held_out_machine_run_identity


def build_held_out_corridor_machine_evidence(
    spec_file: Path,
    *,
    snapshot_report_file: Path,
    held_out_review_policy_file: Path,
    certification_envelope_file: Path,
    toolchain_lock_file: Path,
    output_dir: Path,
    sumo_home: Path,
    only_corridor_keys: Sequence[str] = (),
    blinding_seed: str | None = None,
    timeout_seconds: float = 300.0,
) -> dict[str, Any]:
    """Build, audit, simulate, and blind every available real-corridor case."""

    spec_path = spec_file.resolve()
    snapshot_path = snapshot_report_file.resolve()
    policy_path = held_out_review_policy_file.resolve()
    envelope_path = certification_envelope_file.resolve()
    toolchain_path = toolchain_lock_file.resolve()
    destination = output_dir.resolve()
    sumo_root = sumo_home.resolve()
    spec = HeldOutCorpusSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    snapshot_report = HeldOutCorpusSnapshotReport.model_validate_json(
        snapshot_path.read_text(encoding="utf-8")
    )
    policy = HeldOutReviewPolicy.model_validate_json(
        policy_path.read_text(encoding="utf-8")
    )
    envelope = CertificationEnvelope.model_validate_json(
        envelope_path.read_text(encoding="utf-8")
    )
    if snapshot_report.corpus_id != spec.corpus_id:
        raise ValueError("Snapshot report belongs to a different held-out corpus.")
    if snapshot_report.corpus_spec_sha256 != file_sha256(spec_path):
        raise ValueError("Snapshot report corpus-spec hash mismatch.")
    if snapshot_report.held_out_review_policy_sha256 != file_sha256(policy_path):
        raise ValueError("Snapshot report held-out policy hash mismatch.")
    if spec.held_out_review_policy_sha256 != file_sha256(policy_path):
        raise ValueError("Held-out machine run policy hash mismatch.")
    netconvert_binary = sumo_root / "bin" / "netconvert.exe"
    sumo_binary = sumo_root / "bin" / "sumo.exe"
    random_trips = sumo_root / "tools" / "randomTrips.py"
    missing_tools = [
        str(path)
        for path in (netconvert_binary, sumo_binary, random_trips)
        if not path.is_file()
    ]
    if missing_tools:
        raise ValueError("Required SUMO tools are missing: " + ", ".join(missing_tools))
    destination.mkdir(parents=True, exist_ok=True)
    requested_keys = {value.strip() for value in only_corridor_keys if value.strip()}
    known_cases = {case.corridor_key: case for case in spec.corridors}
    unknown = sorted(requested_keys - set(known_cases))
    if unknown:
        raise ValueError(f"Unknown held-out corridors: {', '.join(unknown)}")
    snapshot_by_selection = {
        item.selection_id: item for item in snapshot_report.corridors
    }
    selected_cases = tuple(
        case
        for case in spec.corridors
        if (not requested_keys or case.corridor_key in requested_keys)
        and case.selection_id in snapshot_by_selection
    )
    effective_blinding_seed = blinding_seed or secrets.token_hex(32)
    run_identity = capture_held_out_machine_run_identity(
        repository_root=spec_path.parents[2],
        entrypoint="plugins/torii-sumo/scripts/build_held_out_corridor_evidence.py",
        toolchain_lock_file=toolchain_path,
        runtime_tool_paths={
            "netconvert": netconvert_binary,
            "sumo": sumo_binary,
        },
        support_file_paths={
            "randomTrips.py": random_trips,
            "osmNetconvert.typ.xml": (
                sumo_root / "data" / "typemap" / "osmNetconvert.typ.xml"
            ),
            "osmNetconvertBicycle.typ.xml": (
                sumo_root
                / "data"
                / "typemap"
                / "osmNetconvertBicycle.typ.xml"
            ),
            "osmNetconvertPedestrians.typ.xml": (
                sumo_root
                / "data"
                / "typemap"
                / "osmNetconvertPedestrians.typ.xml"
            ),
        },
        selected_corridor_keys=tuple(
            case.corridor_key for case in selected_cases
        ),
        timeout_seconds=timeout_seconds,
        blinding_seed=effective_blinding_seed,
    )
    run_identity_path = destination / "held_out_corpus.run-identity.json"
    write_json_atomic(
        run_identity_path,
        run_identity.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    blockers: list[str] = []
    if len(selected_cases) != len(spec.corridors):
        blockers.append(
            f"machine_evidence_partial:{len(selected_cases)}/{len(spec.corridors)}"
        )
    results: list[HeldOutCorridorMachineResult] = []
    review_cases: list[ReviewCase] = []
    machine_assessments: dict[str, MachineAssessment] = {}
    case_strata: dict[str, HeldOutCaseStratum] = {}
    review_materials: dict[str, HeldOutReviewMaterial] = {}
    source_by_id = {source.source_id: source for source in spec.city_extracts}
    for case in selected_cases:
        snapshot = snapshot_by_selection[case.selection_id]
        city_source = source_by_id[case.city_source_id]
        case_destination = destination / "cases" / case.corridor_key
        try:
            result, review_case, assessment, stratum, material = _build_case_evidence(
                spec=spec,
                case=case,
                snapshot=snapshot,
                city_group=city_source.city_group,
                traffic_side=city_source.traffic_side,
                envelope=envelope,
                destination=case_destination,
                sumo_root=sumo_root,
                netconvert_binary=netconvert_binary,
                sumo_binary=sumo_binary,
                random_trips=random_trips,
                toolchain_id=run_identity.toolchain_id,
                timeout_seconds=timeout_seconds,
            )
            results.append(result)
            review_cases.append(review_case)
            machine_assessments[review_case.review_case_id] = assessment
            case_strata[review_case.review_case_id] = stratum
            review_materials[review_case.review_case_id] = material
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            source_path = Path(snapshot.path).resolve()
            source_immutable = (
                source_path.is_file() and file_sha256(source_path) == snapshot.sha256
            )
            case_blocker = (
                f"machine_case_failed:{case.corridor_key}:"
                f"{type(exc).__name__}:{exc}"
            )
            blockers.append(case_blocker)
            partial_artifacts = {
                path.resolve()
                for path in case_destination.rglob("*")
                if path.is_file()
            }
            if source_path.is_file():
                partial_artifacts.add(source_path)
            source_artifacts = {
                str(path): file_sha256(path) for path in partial_artifacts
            }
            results.append(
                HeldOutCorridorMachineResult(
                    selection_id=case.selection_id,
                    corridor_key=case.corridor_key,
                    pipeline_status=GateStatus.BLOCKED,
                    machine_label="defect",
                    source_osm_sha256=snapshot.sha256,
                    source_osm_immutable=source_immutable,
                    net_sha256=None,
                    netconvert_status="not-completed",
                    sumo_load_status="not-run",
                    routeability_status="not-run",
                    connection_mode_status="not-run",
                    independent_safety_status=GateStatus.BLOCKED,
                    applicability_decision="invalid",
                    review_case_id=None,
                    artifact_sha256_by_path=source_artifacts,
                    finding_categories=("machine_case_pipeline_failure",),
                    blockers=(case_blocker,),
                )
            )

    package: dict[str, Any] | None = None
    if review_cases:
        package = build_blinded_review_artifacts(
            review_cases,
            machine_assessments=machine_assessments,
            case_strata=case_strata,
            trial_id=policy.trial_id,
            created_at=datetime.now(UTC),
            blinding_seed=effective_blinding_seed,
            output_dir=destination / "review-package",
            review_materials=review_materials,
        )
    if len(review_cases) != len(selected_cases):
        blockers.append(
            f"review_case_missing:{len(review_cases)}/{len(selected_cases)}"
        )
    failed_results = [
        result for result in results if result.pipeline_status is not GateStatus.PASS
    ]
    if failed_results:
        blockers.append(f"machine_pipeline_failures:{len(failed_results)}")
    evidence_status = (
        GateStatus.BLOCKED
        if blockers
        else GateStatus.REVIEW
    )
    dataset_path = (
        Path(package["blinded_dataset_file"]).resolve() if package else None
    )
    key_path = Path(package["evaluation_key_file"]).resolve() if package else None
    report = HeldOutCorpusMachineReport(
        corpus_id=spec.corpus_id,
        corpus_spec_sha256=file_sha256(spec_path),
        snapshot_report_sha256=file_sha256(snapshot_path),
        certification_envelope_sha256=file_sha256(envelope_path),
        toolchain_lock_path=str(toolchain_path.resolve()),
        toolchain_lock_sha256=file_sha256(toolchain_path),
        run_identity_id=run_identity.run_identity_id,
        run_identity_path=str(run_identity_path.resolve()),
        run_identity_sha256=file_sha256(run_identity_path),
        evidence_build_status=evidence_status,
        expected_case_count=len(spec.corridors),
        processed_case_count=len(results),
        results=tuple(results),
        blinded_dataset_path=str(dataset_path) if dataset_path else None,
        blinded_dataset_sha256=file_sha256(dataset_path) if dataset_path else None,
        evaluation_key_path=str(key_path) if key_path else None,
        evaluation_key_sha256=file_sha256(key_path) if key_path else None,
        blockers=tuple(dict.fromkeys(blockers)),
    )
    report_path = destination / "held_out_corpus.machine-report.json"
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    artifacts = [
        spec_path,
        snapshot_path,
        policy_path,
        envelope_path,
        toolchain_path,
        run_identity_path,
        report_path,
        *(Path(item.path) for item in run_identity.runtime_tools),
        *(Path(item.path) for item in run_identity.support_files),
    ]
    for result in results:
        artifacts.extend(Path(path) for path in result.artifact_sha256_by_path)
    if package:
        review_package_root = (destination / "review-package").resolve()
        artifacts.extend(
            path for path in review_package_root.rglob("*") if path.is_file()
        )
    manifest_path = destination / "held_out_corpus.machine-manifest.json"
    manifest = HeldOutCorpusMachineManifest(
        corpus_id=spec.corpus_id,
        evidence_build_status=evidence_status,
        toolchain_lock_path=str(toolchain_path.resolve()),
        toolchain_lock_sha256=file_sha256(toolchain_path),
        run_identity_id=run_identity.run_identity_id,
        run_identity_path=str(run_identity_path.resolve()),
        run_identity_sha256=file_sha256(run_identity_path),
        producer=run_identity.producer,
        artifacts=tuple(
            HeldOutMachineArtifactIdentity(
                path=str(path.resolve()),
                sha256=file_sha256(path.resolve()),
            )
            for path in sorted(
                {path for path in artifacts if path.is_file()},
                key=lambda item: item.as_posix(),
            )
        ),
    )
    write_json_atomic(
        manifest_path,
        manifest.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    return {
        **report.model_dump(mode="json", by_alias=True),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
        "review_package": package,
    }


def _build_case_evidence(
    *,
    spec: HeldOutCorpusSpec,
    case: Any,
    snapshot: CroppedCorridorSnapshot,
    city_group: str,
    traffic_side: Any,
    envelope: CertificationEnvelope,
    destination: Path,
    sumo_root: Path,
    netconvert_binary: Path,
    sumo_binary: Path,
    random_trips: Path,
    toolchain_id: str,
    timeout_seconds: float,
) -> tuple[
    HeldOutCorridorMachineResult,
    ReviewCase,
    MachineAssessment,
    HeldOutCaseStratum,
    HeldOutReviewMaterial,
]:
    destination.mkdir(parents=True, exist_ok=True)
    source_osm = Path(snapshot.path).resolve()
    source_sha256 = file_sha256(source_osm)
    if source_sha256 != snapshot.sha256:
        raise ValueError("Cropped OSM snapshot hash mismatch before netconvert.")
    profile = spec.network_build_profile
    build_kwargs = {
        "bbox": case.bbox.as_sumo_string(),
        "prefix": case.corridor_key,
        "source_osm_path": source_osm,
        "allowed_highways": set(profile.allowed_highways),
        "include_railway": profile.include_railway,
        "allowed_railways": set(profile.allowed_railways),
        "netconvert_profile": profile.netconvert_profile,
        "netconvert_binary": str(netconvert_binary),
        "clip_source_ways_to_bbox": profile.clip_source_ways_to_bbox,
        "sumo_home": sumo_root,
        "traffic_side": traffic_side.value,
        "timeout_seconds": timeout_seconds,
    }
    build_report = build_osm_network(
        output_dir=destination / "network-build",
        **build_kwargs,
    )
    build_report_path = destination / "network-build.json"
    write_json_atomic(build_report_path, build_report, sort_keys=True)
    if build_report.get("status") != "pass":
        raise RuntimeError(
            "netconvert failed: " + str(build_report.get("error", "unknown"))
        )
    net_file = Path(str(build_report["net_file"])).resolve()
    if not net_file.is_file():
        raise RuntimeError("netconvert reported success without a network artifact.")
    net_sha256 = file_sha256(net_file)
    replay_build_report = build_osm_network(
        output_dir=destination / "network-build-replay",
        **build_kwargs,
    )
    replay_build_report_path = destination / "network-build-replay.json"
    write_json_atomic(
        replay_build_report_path,
        replay_build_report,
        sort_keys=True,
    )
    if replay_build_report.get("status") != "pass":
        raise RuntimeError(
            "netconvert replay failed: "
            + str(replay_build_report.get("error", "unknown"))
        )
    replay_net_file = Path(str(replay_build_report["net_file"])).resolve()
    if not replay_net_file.is_file():
        raise RuntimeError("netconvert replay succeeded without a network artifact.")
    replay = compare_netconvert_replay(net_file, replay_net_file)
    replay_report_path = destination / "net-replay.json"
    write_json_atomic(
        replay_report_path,
        replay.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    if replay.status is not GateStatus.PASS:
        raise RuntimeError("netconvert normalized replay identity mismatch.")
    load_report = run_sumo_load_audit(
        net_file=net_file,
        output_dir=destination / "sumo-load",
        sumo_binary=str(sumo_binary),
        timeout_seconds=timeout_seconds,
    )
    calibration_path = destination / "connection-mode" / "calibration.json"
    calibration = build_connection_mode_calibration_artifact(
        net_file,
        output_file=calibration_path,
        traffic_side=traffic_side,
    )
    endpoint_tolerance_m, endpoint_tolerance_source = (
        _connection_audit_tolerance(calibration)
    )
    connection_report = build_network_connection_mode_audit(
        net_file,
        output_dir=destination / "connection-mode",
        prefix="connection-mode",
        traffic_side=traffic_side.value,
        endpoint_tolerance_m=endpoint_tolerance_m,
        normalized_lane_rank_tolerance=0.5,
    )
    canonical = canonicalize_net_xml_file(net_file, traffic_side=traffic_side)
    canonical_path = destination / "canonical-network.json"
    write_json_atomic(
        canonical_path,
        canonical.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    program_inventory = build_effective_tls_program_inventory(net_file)
    program_inventory_path = (
        destination / "effective-tls-program-inventory.json"
    )
    write_json_atomic(
        program_inventory_path,
        program_inventory.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    pedestrian_binding_census = classify_controlled_pedestrian_bindings(
        canonical,
        program_inventory,
    )
    pedestrian_binding_census_path = (
        destination / "controlled-pedestrian-binding-census.json"
    )
    write_json_atomic(
        pedestrian_binding_census_path,
        pedestrian_binding_census.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    safety = audit_independent_movement_safety(canonical)
    safety_path = destination / "independent-safety.json"
    write_json_atomic(
        safety_path,
        safety.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    review_compression = build_lossless_review_compression(
        canonical,
        safety,
        source_osm_sha256=source_sha256,
        candidate_net_sha256=net_sha256,
        toolchain_id=toolchain_id,
        sampling_seed=RWC1_FROZEN_SAMPLING_SEED,
        corridor_morphology=case.morphology,
    )
    review_compression_path = destination / "lossless-review-compression.json"
    write_json_atomic(
        review_compression_path,
        review_compression.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    applicability = evaluate_certification_applicability(canonical, envelope)
    applicability_path = destination / "certification-applicability.json"
    write_json_atomic(
        applicability_path,
        applicability.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    routeability = run_routeability_audit(
        net_file=net_file,
        output_dir=destination / "routeability",
        prefix="routeability",
        vehicle_count=profile.routeability_vehicle_count,
        seed=profile.routeability_seed,
        timeout_seconds=timeout_seconds,
        binaries={
            "randomTrips": str(random_trips),
            "sumo": str(sumo_binary),
        },
    )
    source_immutable = file_sha256(source_osm) == source_sha256
    if not source_immutable:
        raise RuntimeError("Cropped OSM source changed during machine evidence build.")

    machine_label, categories, passed_gates, unresolved_gates = _classify_case(
        build_report=build_report,
        load_report=load_report,
        connection_report=connection_report,
        calibration_status=calibration.status,
        safety_status=safety.status,
        safety_categories=tuple(finding.category for finding in safety.findings),
        controlled_pedestrian_binding_classes=tuple(
            pedestrian_binding_census.class_counts
        ),
        review_compression_status=(
            review_compression.machine_review_ready_gate
        ),
        reproducibility_status=replay.status,
        applicability=applicability,
        routeability=routeability,
    )
    candidate_id = stable_id(
        "candidate",
        {
            "corpus_id": spec.corpus_id,
            "selection_id": case.selection_id,
            "net_sha256": net_sha256,
        },
    )
    review_case_id = stable_id(
        "review",
        {
            "corpus_id": spec.corpus_id,
            "selection_id": case.selection_id,
            "source_osm_sha256": source_sha256,
            "candidate_id": candidate_id,
        },
    )
    finding_ids = tuple(
        stable_id(
            "finding",
            {
                "selection_id": case.selection_id,
                "category": category,
            },
        )
        for category in categories
    )
    affected_ids = tuple(
        sorted(
            {
                entity.stable_entity_id
                for entity in canonical.entities
                if entity.kind == "physical_cell"
            }
        )
    ) or (case.selection_id,)
    evidence_refs = tuple(
        stable_id(
            "evidence",
            {
                "selection_id": case.selection_id,
                "kind": kind,
                "sha256": sha256,
            },
        )
        for kind, sha256 in (
            ("osm", source_sha256),
            ("net", net_sha256),
            ("net-replay", file_sha256(replay_report_path)),
            ("connection-mode", file_sha256(Path(connection_report["report_file"]))),
            ("independent-safety", file_sha256(safety_path)),
            (
                "controlled-pedestrian-binding-census",
                file_sha256(pedestrian_binding_census_path),
            ),
            (
                "lossless-review-compression",
                file_sha256(review_compression_path),
            ),
            ("routeability", file_sha256(Path(routeability["report_file"]))),
        )
    )
    rollback_artifact_id = stable_id(
        "artifact",
        {
            "selection_id": case.selection_id,
            "action": "discard-generated-network",
            "candidate_sha256": net_sha256,
        },
    )
    review_case = ReviewCase(
        review_case_id=review_case_id,
        source_sha256=source_sha256,
        candidate_sha256_by_variant={candidate_id: net_sha256},
        scope_id=case.selection_id,
        finding_ids=finding_ids,
        affected_stable_entity_ids=affected_ids,
        decision_type="corridor-modeling-validity",
        machine_question=(
            "Does this blinded SUMO network faithfully represent the observed "
            "physical junction boundaries, legal lane movements, multimodal "
            "connectivity, right-of-way, and signal ownership in the corridor?"
        ),
        candidate_variant_ids=(candidate_id,),
        machine_recommendation=(candidate_id if machine_label == "acceptable" else None),
        confidence_components={
            "artifact_identity": 1.0,
            "runtime_load": 1.0 if load_report["status"] == "pass" else 0.0,
            "connection_structure": (
                1.0 if connection_report["status"] == "pass" else 0.0
            ),
            "independent_safety": 1.0 if safety.status is GateStatus.PASS else 0.0,
            "external_semantics": 0.0,
        },
        passed_gates=passed_gates,
        unresolved_gates=unresolved_gates,
        evidence_refs=evidence_refs,
        required_observations=(
            "Compare lane count, turn lanes, and lane-to-lane movements with current map evidence.",
            "Check whether split/merged physical junction boundaries and storage segments are realistic.",
            "Check pedestrian, bicycle, ramp, rail, bridge, and tunnel continuity where present.",
            "Inspect protected/permissive signal groups and shared-controller ownership.",
            "Record every observed fact and do not infer prohibition from a missing OSM turn tag.",
        ),
        rollback_artifact_id=rollback_artifact_id,
    )
    map_links = {
        "openstreetmap": (
            f"https://www.openstreetmap.org/#map=17/"
            f"{case.center_lat:.6f}/{case.center_lon:.6f}"
        ),
        "google_maps_human_review_aid": google_maps_url(
            case.center_lat, case.center_lon
        ),
    }
    machine_report_path = destination / "machine-assessment-evidence.json"
    write_json_atomic(
        machine_report_path,
        {
            "schema": "torii.corridor.held-out-machine-assessment-evidence/v1",
            "review_case_id": review_case_id,
            "selection_id": case.selection_id,
            "machine_label": machine_label,
            "finding_categories": categories,
            "passed_gates": passed_gates,
            "unresolved_gates": unresolved_gates,
            "source_osm_sha256": source_sha256,
            "candidate_net_sha256": net_sha256,
            "candidate_net_normalized_sha256": replay.primary_normalized_sha256,
            "replay_net_sha256": replay.replay_net_sha256,
            "replay_net_normalized_sha256": replay.replay_normalized_sha256,
            "reproducible_semantics": replay.reproducible_semantics,
            "source_osm_immutable": source_immutable,
            "connection_mode_endpoint_tolerance_m": endpoint_tolerance_m,
            "connection_mode_endpoint_tolerance_source": endpoint_tolerance_source,
            "map_links": map_links,
            "map_links_are_human_review_aids_only": True,
            "artifacts": {
                "source_osm": str(source_osm),
                "candidate_net": str(net_file),
                "network_build": str(build_report_path),
                "network_build_replay": str(replay_build_report_path),
                "replay_net": str(replay_net_file),
                "net_replay": str(replay_report_path),
                "sumo_load": str(load_report["report_file"]),
                "connection_mode": str(connection_report["report_file"]),
                "connection_overlay": str(connection_report["review_overlay_file"]),
                "calibration": str(calibration_path),
                "canonical_network": str(canonical_path),
                "independent_safety": str(safety_path),
                "effective_tls_program_inventory": str(
                    program_inventory_path
                ),
                "controlled_pedestrian_binding_census": str(
                    pedestrian_binding_census_path
                ),
                "lossless_review_compression": str(
                    review_compression_path
                ),
                "applicability": str(applicability_path),
                "routeability": str(routeability["report_file"]),
            },
        },
        sort_keys=True,
    )
    assessment = MachineAssessment(
        machine_label=machine_label,
        machine_report_sha256=file_sha256(machine_report_path),
        finding_categories=categories,
        safety_critical=(
            safety.status is not GateStatus.PASS
            or connection_report["status"] == "fail"
            or routeability.get("routeability_status") == "collision-failure"
        ),
    )
    observed_modes = tuple(
        ["road-motorized"]
        + [
            feature
            for feature in spec.required_mode_features
            if snapshot.observed_feature_counts.get(feature, 0) > 0
        ]
    )
    stratum = HeldOutCaseStratum(
        city_group=city_group,
        morphology=case.morphology,
        traffic_side=traffic_side,
        osm_completeness="unassessed",
        mode_features=observed_modes,
    )
    material = HeldOutReviewMaterial(
        candidate_artifact_path_by_variant={candidate_id: str(net_file)},
        review_overlay_path=str(connection_report["review_overlay_file"]),
        map_evidence_urls=tuple(map_links.values()),
    )
    artifact_paths = {
        source_osm,
        *(
            path.resolve()
            for path in destination.rglob("*")
            if path.is_file()
        ),
    }
    artifact_hashes = {
        str(path.resolve()): file_sha256(path.resolve())
        for path in artifact_paths
        if path.is_file()
    }
    result = HeldOutCorridorMachineResult(
        selection_id=case.selection_id,
        corridor_key=case.corridor_key,
        pipeline_status=GateStatus.PASS,
        machine_label=machine_label,
        source_osm_sha256=source_sha256,
        source_osm_immutable=source_immutable,
        net_sha256=net_sha256,
        netconvert_status=str(build_report["status"]),
        sumo_load_status=str(load_report["status"]),
        routeability_status=str(routeability["status"]),
        connection_mode_status=str(connection_report["status"]),
        independent_safety_status=safety.status,
        applicability_decision=applicability.decision,
        review_case_id=review_case_id,
        artifact_sha256_by_path=artifact_hashes,
        finding_categories=categories,
        blockers=(),
    )
    return result, review_case, assessment, stratum, material


def _connection_audit_tolerance(calibration: Any) -> tuple[float, str]:
    calibrated = getattr(calibration, "endpoint_tolerance_m", None)
    if calibrated is None:
        return 2.0, "diagnostic_fallback_due_blocked_calibration"
    return float(calibrated), "source_baseline_calibration"


def _classify_case(
    *,
    build_report: dict[str, Any],
    load_report: dict[str, Any],
    connection_report: dict[str, Any],
    calibration_status: GateStatus,
    safety_status: GateStatus,
    safety_categories: tuple[str, ...],
    reproducibility_status: GateStatus,
    applicability: Any,
    routeability: dict[str, Any],
    controlled_pedestrian_binding_classes: tuple[str, ...] = (),
    review_compression_status: GateStatus = GateStatus.PASS,
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    categories: set[str] = set(safety_categories)
    categories.update(
        f"controlled_pedestrian_binding:{primary_class}"
        for primary_class in controlled_pedestrian_binding_classes
    )
    statuses = {
        "netconvert": str(build_report.get("status", "fail")),
        "sumo_load": str(load_report.get("status", "fail")),
        "routeability": str(routeability.get("status", "fail")),
        "connection_mode": str(connection_report.get("status", "fail")),
        "calibration": calibration_status.value,
        "independent_safety": safety_status.value,
        "review_compression": review_compression_status.value,
        "reproducibility": reproducibility_status.value,
        "applicability": str(applicability.decision),
    }
    if statuses["connection_mode"] == "fail":
        categories.add("connection_mode_structural_failure")
    elif statuses["connection_mode"] != "pass":
        categories.add("connection_mode_review_required")
    if statuses["calibration"] != "pass":
        categories.add("connection_mode_calibration_unresolved")
    for finding in applicability.findings:
        categories.add(str(finding.category))
    if statuses["applicability"] == "invalid":
        categories.add("certification_applicability_invalid")
    if statuses["sumo_load"] != "pass":
        categories.add("sumo_load_failure")
    if statuses["routeability"] != "pass":
        categories.add("routeability_failure")
    if statuses["reproducibility"] != "pass":
        categories.add("normalized_net_replay_mismatch")
    if statuses["review_compression"] != "pass":
        categories.add("lossless_review_compression_unresolved")
    definitive_safety_defects = {
        "conflicting_movements_share_signal_group",
        "movement_geometry_missing_for_independent_safety",
        "movement_path_permission_empty",
        "protected_green_movement_conflict",
        "signal_group_phase_state_inconsistent",
    }
    definitive_binding_defects = {
        "ordinary-program-truly-absent",
        "program-present-link-invalid",
    }
    defect = (
        statuses["netconvert"] != "pass"
        or statuses["sumo_load"] != "pass"
        or statuses["routeability"] != "pass"
        or statuses["connection_mode"] == "fail"
        or statuses["reproducibility"] != "pass"
        or bool(definitive_safety_defects & set(safety_categories))
        or bool(
            definitive_binding_defects
            & set(controlled_pedestrian_binding_classes)
        )
    )
    ambiguous = (
        statuses["connection_mode"] != "pass"
        or calibration_status is not GateStatus.PASS
        or safety_status is not GateStatus.PASS
        or review_compression_status is not GateStatus.PASS
        or statuses["applicability"] != "in-domain"
    )
    label = "defect" if defect else "ambiguous" if ambiguous else "acceptable"
    passed = tuple(sorted(key for key, status in statuses.items() if status == "pass"))
    unresolved = tuple(
        sorted(key for key, status in statuses.items() if status != "pass")
    )
    return label, tuple(sorted(categories)), passed, unresolved
