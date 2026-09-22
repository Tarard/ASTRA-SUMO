from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import shutil
from typing import Any
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.standard_nema_binding import (
    build_standard_nema_phase_binding,
)
from torii_sumo.intersection.nema_reference import (
    build_nema_four_way_reference,
)

from .official_sumo_benchmark_runner import run_official_sumo_benchmark


_OWNER_SCHEMA = "torii.standard-small-network-gate-owner/v1"


def run_standard_small_network_gate(
    *,
    official_spec_file: Path,
    parent_benchmark_file: Path,
    toolchain_lock_file: Path,
    official_source_root: Path,
    output_dir: Path,
    netconvert_binary: str,
    sumo_binary: str,
) -> dict[str, Any]:
    """Run the vehicle-only positive/negative small-network boundary probe."""

    destination = output_dir.resolve()
    _reset_owned_output_directory(destination)
    report_file = destination / "standard-small-network-gate.json"
    manifest_file = destination / "standard-small-network-gate.manifest.json"
    report_file.unlink(missing_ok=True)
    manifest_file.unlink(missing_ok=True)

    official = run_official_sumo_benchmark(
        official_spec_file,
        parent_benchmark_file=parent_benchmark_file,
        toolchain_lock_file=toolchain_lock_file,
        source_root=official_source_root,
        output_dir=destination / "official-sumo",
        netconvert_binary=netconvert_binary,
        sumo_binary=sumo_binary,
    )
    official_cases = {str(case["case_id"]): case for case in official["cases"]}

    binaries = {
        "netconvert": str(Path(netconvert_binary).resolve()),
        "sumo": str(Path(sumo_binary).resolve()),
    }

    def find_binary(name: str) -> str | None:
        return binaries.get(name)

    reference = build_nema_four_way_reference(
        destination / "positive-nema-reference",
        prefix="positive-nema-reference",
        run_sumo_smoke=True,
        require_real_sumo=True,
        which_func=find_binary,
    )
    reference_net = Path(str(reference.get("net_file", "")))
    reference_scan = _scan_one_nema_network(
        net_file=reference_net,
        output_dir=destination / "positive-nema-scan",
        prefix="positive-nema-scan",
    )

    official_scans: dict[str, dict[str, Any]] = {}
    for case_id in ("nema-four-arm", "nema-four-arm-grouped"):
        case = official_cases[case_id]
        official_scans[case_id] = _scan_one_nema_network(
            net_file=Path(str(case["generated_net_path"])),
            output_dir=destination / "official-nema-scans" / case_id,
            prefix=case_id,
        )

    positive_pass = (
        reference.get("status") == "pass"
        and reference.get("netconvert_status") == "pass"
        and reference.get("sumo_smoke_status") == "pass"
        and reference_scan["eligibility_status"] == "eligible"
        and reference_scan["layout_type"] == "four_way"
        and reference_scan["movement_count"] == 12
        and reference_scan["used_nema_phases"] == list(range(1, 9))
    )
    negative_pass = all(
        scan["eligibility_status"] == "review_required"
        and scan["layout_type"] == "four_way"
        and scan["movement_count"] == 16
        and any(blocker.startswith("turnaround_movement_present:") for blocker in scan["blockers"])
        and any(blocker.startswith("protected_left_lane_not_dedicated:") for blocker in scan["blockers"])
        for scan in official_scans.values()
    )
    official_pass = official.get("status") == "pass"
    gates = {
        "official_sumo_9_case_regression": ("pass" if official_pass else "fail"),
        "strict_nema_positive_reference": ("pass" if positive_pass else "fail"),
        "strict_nema_official_ood_abstention": ("pass" if negative_pass else "fail"),
    }
    passed = all(value == "pass" for value in gates.values())
    modal_decision = _modal_expansion_decision(official_cases)
    report = {
        "schema": "torii.standard-small-network-gate/v1",
        "status": "pass" if passed else "fail",
        "automatic_promotion_gate": "blocked",
        "human_validation": False,
        "gates": gates,
        "official_benchmark": {
            "status": official["status"],
            "total_case_count": official["total_case_count"],
            "passed_case_count": official["passed_case_count"],
            "failed_case_count": official["failed_case_count"],
            "manifest_file": official["manifest_file"],
        },
        "strict_nema_positive_reference": {
            "builder_status": reference.get("status"),
            "netconvert_status": reference.get("netconvert_status"),
            "sumo_smoke_status": reference.get("sumo_smoke_status"),
            "controlled_link_count": reference.get("controlled_link_count"),
            "signal_group_count": reference.get("tls_signal_group_count"),
            "scan": reference_scan,
        },
        "official_nema_applicability": official_scans,
        "interpretation": {
            "positive": (
                "The strict policy accepts a dedicated-lane, no-U-turn, "
                "12-movement four-way reference and covers phases 1-8."
            ),
            "negative": (
                "SUMO's official NEMA_4arm examples are normative controller "
                "fixtures but remain outside Torii's automatic candidate "
                "envelope because they contain U-turns and shared "
                "left/through lanes."
            ),
            "no_contradiction": (
                "An official SUMO example can be valid while Torii correctly abstains from rewriting it."
            ),
        },
        "modal_expansion_decision": modal_decision,
        "claim_boundary": (
            "This gate validates a narrow vehicle-only standard four-way "
            "NEMA applicability boundary and official audit regressions. It "
            "does not certify field timing, arbitrary three-way NEMA, "
            "pedestrian/bicycle/ramp repair, or automatic promotion."
        ),
    }
    write_json_atomic(report_file, report, sort_keys=True)
    manifest = {
        "schema": "torii.standard-small-network-gate-manifest/v1",
        "status": report["status"],
        "automatic_promotion_gate": "blocked",
        "source_mutation": False,
        "inputs": [
            _artifact("official_spec", official_spec_file),
            _artifact("parent_benchmark", parent_benchmark_file),
            _artifact("toolchain_lock", toolchain_lock_file),
        ],
        "gates": gates,
        "artifacts": [
            _artifact("generated", path)
            for path in sorted(destination.rglob("*"))
            if path.is_file() and path != manifest_file
        ],
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return {
        **report,
        "report_file": str(report_file),
        "manifest_file": str(manifest_file),
    }


def _scan_one_nema_network(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str,
) -> dict[str, Any]:
    if not net_file.is_file():
        return {
            "status": "fail",
            "eligibility_status": "missing_network",
            "net_file": str(net_file),
            "blockers": ["network_missing"],
        }
    junction_id = _single_tls_junction_id(net_file)
    scan = build_standard_nema_phase_binding(
        net_file,
        output_dir=output_dir,
        prefix=prefix,
        run_runtime_checks=False,
    )
    record = next(
        (item for item in scan.get("candidates", ()) if item.get("junction_id") == junction_id),
        None,
    )
    if record is None:
        return {
            "status": "fail",
            "eligibility_status": "target_missing_from_scan",
            "net_file": str(net_file),
            "junction_id": junction_id,
            "blockers": ["target_missing_from_scan"],
        }
    return {
        "status": "pass",
        "net_file": str(net_file),
        "net_sha256": file_sha256(net_file),
        "junction_id": junction_id,
        "eligibility_status": record["eligibility_status"],
        "layout_type": record["layout_type"],
        "arm_count": record["arm_count"],
        "movement_count": record["direct_vehicle_movement_count"],
        "used_nema_phases": record["used_nema_phases"],
        "phase_by_arm": record["phase_by_arm"],
        "blockers": record["blockers"],
        "scan_report_file": scan["report_file"],
        "scan_manifest_file": scan["manifest_file"],
    }


def _single_tls_junction_id(net_file: Path) -> str:
    root = ET.parse(net_file).getroot()
    ids = [
        str(junction.attrib["id"])
        for junction in root.findall("junction")
        if junction.attrib.get("type") == "traffic_light"
    ]
    if len(ids) != 1:
        raise ValueError(f"Expected one traffic-light junction in {net_file}, got {ids}.")
    return ids[0]


def _modal_expansion_decision(
    cases: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    pedestrian = cases["pedestrian-crossing"]
    ramp = cases["on-ramp"]
    rail = cases["rail-crossing"]
    return {
        "decision": "pedestrian_first",
        "next_stage": ("pedestrian crossing facility + right-of-way evidence + vehicle-conflict closure"),
        "reason": (
            "The vendored official pedestrian case already provides five "
            "movements and four independently reconstructed conflicts with a "
            "passing safety graph, while Connection Mode still requires "
            "review. This is the smallest evidence-rich extension of the "
            "current physical-cell/movement model."
        ),
        "pedestrian_evidence": _case_decision_evidence(pedestrian),
        "bicycle_disposition": ("defer_until_a_vendored_official_or_gold_bicycle_fixture_exists"),
        "ramp_disposition": "after_pedestrian",
        "ramp_evidence": _case_decision_evidence(ramp),
        "rail_disposition": "protected_runtime_special_ood",
        "rail_evidence": _case_decision_evidence(rail),
        "automatic_promotion_gate": "blocked",
    }


def _case_decision_evidence(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "status": case["status"],
        "connection_status": case["connection_status"],
        "independent_safety_status": case["independent_safety_status"],
        "movement_count": case["movement_count"],
        "conflict_count": case["conflict_count"],
        "abstention_proven": case["abstention_proven"],
    }


def _artifact(role: str, path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "role": role,
        "path": str(resolved),
        "sha256": file_sha256(resolved),
    }


def _reset_owned_output_directory(destination: Path) -> None:
    owner = destination / "standard-small-network-gate.owner.json"
    if destination.exists() and any(destination.iterdir()):
        if not owner.is_file():
            raise ValueError(
                "Refusing to clear a non-empty small-network gate directory without Torii ownership metadata."
            )
        payload = json.loads(owner.read_text(encoding="utf-8"))
        if payload.get("schema") != _OWNER_SCHEMA:
            raise ValueError("Small-network gate ownership metadata is invalid.")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    write_json_atomic(
        owner,
        {
            "schema": _OWNER_SCHEMA,
            "purpose": "generated standard-small-network gate artifacts",
        },
        sort_keys=True,
    )
