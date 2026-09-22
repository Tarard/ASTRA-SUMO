from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from torii_sumo.core.artifact_io import write_json_atomic
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.command_runner import run_command
from torii_sumo.core.connection_mode_audit import audit_network_connection_mode

from .canonicalizer import canonicalize_net_xml_file
from .conflict_graph import audit_independent_movement_safety
from .enums import GateStatus
from .official_sumo_benchmark_contracts import (
    OfficialSumoBenchmarkReport,
    OfficialSumoBenchmarkSpec,
    OfficialSumoCaseResult,
)
from .netxml import normalized_net_sha256


def run_official_sumo_benchmark(
    spec_file: Path,
    *,
    parent_benchmark_file: Path,
    toolchain_lock_file: Path,
    source_root: Path,
    output_dir: Path,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    prefix: str = "official_sumo_benchmark",
) -> dict[str, Any]:
    """Regenerate and audit frozen SUMO normative scenarios twice.

    Byte hashes may differ because netconvert embeds output paths and timestamps.
    Reproducibility is therefore gated on canonical XML with comments removed,
    while both raw artifacts remain individually hashed in the manifest.
    """

    spec_path = spec_file.resolve()
    parent_path = parent_benchmark_file.resolve()
    toolchain_path = toolchain_lock_file.resolve()
    assets = source_root.resolve()
    destination = output_dir.resolve()
    spec = OfficialSumoBenchmarkSpec.model_validate_json(
        spec_path.read_text(encoding="utf-8")
    )
    if file_sha256(parent_path) != spec.parent_benchmark_sha256:
        raise ValueError("Official benchmark is not bound to this parent benchmark.")
    if file_sha256(toolchain_path) != spec.toolchain_lock_sha256:
        raise ValueError("Official benchmark is not bound to this toolchain lock.")
    toolchain = json.loads(toolchain_path.read_text(encoding="utf-8"))
    expected_versions = {
        str(tool["name"]): str(tool["version"])
        for tool in toolchain.get("tools", ())
    }
    source_paths = _validate_sources(spec, assets)
    source_hashes_before = {
        source_id: file_sha256(path) for source_id, path in source_paths.items()
    }
    tools = {
        "netconvert": _tool_identity(
            netconvert_binary,
            expected_version=expected_versions.get("netconvert", ""),
        ),
        "sumo": _tool_identity(
            sumo_binary,
            expected_version=expected_versions.get("sumo", ""),
        ),
    }
    mismatched_tools = [
        name for name, identity in tools.items() if not identity["version_matches"]
    ]
    if mismatched_tools:
        raise ValueError(
            "Official benchmark tool version mismatch: "
            + ", ".join(sorted(mismatched_tools))
        )

    destination.mkdir(parents=True, exist_ok=True)
    artifact_paths: set[Path] = {
        spec_path,
        parent_path,
        toolchain_path,
        *source_paths.values(),
    }
    results: list[OfficialSumoCaseResult] = []
    for case in spec.cases:
        result, case_artifacts = _run_case(
            case=case,
            source_paths=source_paths,
            source_hashes_before=source_hashes_before,
            output_dir=destination / "cases" / case.case_id,
            netconvert_binary=str(tools["netconvert"]["path"]),
            sumo_binary=str(tools["sumo"]["path"]),
        )
        results.append(result)
        artifact_paths.update(case_artifacts)

    source_immutable = all(
        file_sha256(path) == source_hashes_before[source_id]
        for source_id, path in source_paths.items()
    )
    blockers: list[str] = []
    failed = sum(result.status is not GateStatus.PASS for result in results)
    if failed:
        blockers.append(f"official_sumo_case_failures:{failed}")
    if not source_immutable:
        blockers.append("official_sumo_source_fixture_mutated")
    report = OfficialSumoBenchmarkReport(
        benchmark_id=spec.benchmark_id,
        benchmark_spec_sha256=file_sha256(spec_path),
        parent_benchmark_sha256=file_sha256(parent_path),
        toolchain_lock_sha256=file_sha256(toolchain_path),
        status=GateStatus.FAIL if blockers else GateStatus.PASS,
        total_case_count=len(results),
        passed_case_count=len(results) - failed,
        failed_case_count=failed,
        source_immutable=source_immutable,
        runtime_tools=tools,
        cases=tuple(results),
        blockers=tuple(blockers),
    )
    report_path = destination / f"{prefix}.report.json"
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    artifact_paths.add(report_path)
    manifest_path = destination / f"{prefix}.manifest.json"
    manifest = {
        "schema": "torii.corridor.official-sumo-benchmark-manifest/v1",
        "benchmark_id": spec.benchmark_id,
        "status": report.status.value,
        "source_immutable": source_immutable,
        "upstream": {
            "repository": spec.upstream_repository,
            "tag": spec.upstream_tag,
            "commit": spec.upstream_commit,
            "license_expression": spec.license_expression,
        },
        "runtime_tools": tools,
        "artifacts": [
            {"path": str(path), "sha256": file_sha256(path)}
            for path in sorted(artifact_paths, key=lambda item: item.as_posix())
        ],
    }
    write_json_atomic(manifest_path, manifest, sort_keys=True)
    return {
        **report.model_dump(mode="json", by_alias=True),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
    }


def _validate_sources(
    spec: OfficialSumoBenchmarkSpec,
    source_root: Path,
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for source in spec.source_files:
        path = (source_root / source.vendored_path).resolve()
        try:
            path.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(
                f"Official SUMO source escapes source_root: {source.source_id}"
            ) from exc
        if not path.is_file():
            raise FileNotFoundError(f"Official SUMO source is missing: {path}")
        if file_sha256(path) != source.vendored_sha256:
            raise ValueError(f"Official SUMO source hash mismatch: {source.source_id}")
        paths[source.source_id] = path
    return paths


def _run_case(
    *,
    case: Any,
    source_paths: dict[str, Path],
    source_hashes_before: dict[str, str],
    output_dir: Path,
    netconvert_binary: str,
    sumo_binary: str,
) -> tuple[OfficialSumoCaseResult, set[Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_net = output_dir / f"{case.case_id}.net.xml"
    replay_net = output_dir / f"{case.case_id}.replay.net.xml"
    netconvert_result = run_command(
        _netconvert_command(
            netconvert_binary,
            case.netconvert_arguments,
            source_paths,
            generated_net,
        ),
        cwd=output_dir,
    )
    replay_result = run_command(
        _netconvert_command(
            netconvert_binary,
            case.netconvert_arguments,
            source_paths,
            replay_net,
        ),
        cwd=output_dir,
    )
    command_path = output_dir / "netconvert.command.json"
    replay_command_path = output_dir / "netconvert.replay.command.json"
    write_json_atomic(command_path, netconvert_result.to_dict(), sort_keys=True)
    write_json_atomic(replay_command_path, replay_result.to_dict(), sort_keys=True)
    artifacts = {command_path, replay_command_path}
    blockers: list[str] = []
    if netconvert_result.status != "pass" or not generated_net.is_file():
        blockers.append("netconvert_generation_failed")
    if replay_result.status != "pass" or not replay_net.is_file():
        blockers.append("netconvert_replay_failed")
    if blockers:
        raise RuntimeError(f"Official SUMO case {case.case_id} failed: {blockers}")
    artifacts.update({generated_net, replay_net})
    generated_sha = file_sha256(generated_net)
    replay_sha = file_sha256(replay_net)
    normalized_sha = normalized_net_sha256(generated_net)
    replay_normalized_sha = normalized_net_sha256(replay_net)
    reproducible = normalized_sha == replay_normalized_sha
    if not reproducible:
        blockers.append("canonical_net_replay_mismatch")

    connection = audit_network_connection_mode(
        ET.parse(generated_net).getroot(),
        traffic_side=case.traffic_side.value,
    )
    snapshot = canonicalize_net_xml_file(
        generated_net,
        traffic_side=case.traffic_side,
    )
    safety = audit_independent_movement_safety(snapshot)
    sumo_result = run_command(
        [
            sumo_binary,
            "-n",
            str(generated_net),
            "--begin",
            "0",
            "--end",
            "1",
            "--no-step-log",
            "true",
            "--duration-log.disable",
            "true",
        ],
        cwd=output_dir,
    )
    connection_path = output_dir / "connection-mode.json"
    safety_path = output_dir / "independent-safety.json"
    sumo_path = output_dir / "sumo-load.command.json"
    write_json_atomic(connection_path, connection, sort_keys=True)
    write_json_atomic(
        safety_path,
        safety.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    write_json_atomic(sumo_path, sumo_result.to_dict(), sort_keys=True)
    artifacts.update({connection_path, safety_path, sumo_path})

    connection_categories = {
        str(category): int(count)
        for category, count in connection["finding_category_counts"].items()
    }
    safety_categories = dict(
        sorted(Counter(finding.category for finding in safety.findings).items())
    )
    movement_count = len(safety.conflict_graph.movement_ids)
    conflict_count = len(safety.conflict_graph.conflicts)
    source_immutable = all(
        file_sha256(source_paths[source_id]) == source_hashes_before[source_id]
        for source_id in case.source_file_ids
    )
    abstention_proven = bool(
        connection.get("automatic_promotion_gate") == "blocked"
        or safety.automatic_promotion_gate is GateStatus.BLOCKED
    )
    observed = {
        "internal_link_mode": str(connection["internal_link_mode"]),
        "connection_status": str(connection["status"]),
        "connection_category_counts": connection_categories,
        "independent_safety_status": safety.status.value,
        "safety_category_counts": safety_categories,
        "movement_count": movement_count,
        "conflict_count": conflict_count,
    }
    expected = {
        "internal_link_mode": case.expected_internal_link_mode,
        "connection_status": case.expected_connection_status,
        "connection_category_counts": case.expected_connection_category_counts,
        "independent_safety_status": case.expected_independent_safety_status.value,
        "safety_category_counts": case.expected_safety_category_counts,
        "movement_count": case.expected_movement_count,
        "conflict_count": case.expected_conflict_count,
    }
    for key in expected:
        if observed[key] != expected[key]:
            blockers.append(f"normative_expectation_mismatch:{key}")
    if int(connection["structural_failure_count"]):
        blockers.append(
            f"official_structural_failure:{connection['structural_failure_count']}"
        )
    if sumo_result.status != "pass":
        blockers.append("sumo_load_failed")
    if not source_immutable:
        blockers.append("official_source_fixture_mutated")
    if case.expected_abstention and not abstention_proven:
        blockers.append("expected_fail_closed_abstention_not_proven")
    status = GateStatus.FAIL if blockers else GateStatus.PASS
    result = OfficialSumoCaseResult(
        case_id=case.case_id,
        normative_feature=case.normative_feature,
        status=status,
        source_immutable=source_immutable,
        netconvert_status=netconvert_result.status,
        replay_netconvert_status=replay_result.status,
        sumo_load_status=sumo_result.status,
        generated_net_sha256=generated_sha,
        replay_net_sha256=replay_sha,
        normalized_net_sha256=normalized_sha,
        replay_normalized_net_sha256=replay_normalized_sha,
        reproducible_semantics=reproducible,
        internal_link_mode=observed["internal_link_mode"],
        connection_status=observed["connection_status"],
        connection_category_counts=connection_categories,
        independent_safety_status=safety.status,
        safety_category_counts=safety_categories,
        movement_count=movement_count,
        conflict_count=conflict_count,
        abstention_proven=abstention_proven,
        blockers=tuple(blockers),
        generated_net_path=str(generated_net),
    )
    result_path = output_dir / "result.json"
    write_json_atomic(
        result_path,
        result.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    artifacts.add(result_path)
    return result, artifacts


def _netconvert_command(
    binary: str,
    arguments: tuple[str, ...],
    source_paths: dict[str, Path],
    output_net: Path,
) -> list[str]:
    command = [binary]
    for argument in arguments:
        if argument == "@output-net":
            command.append(str(output_net))
        elif argument.startswith("@source:"):
            command.append(str(source_paths[argument.removeprefix("@source:")]))
        else:
            command.append(argument)
    return command


def _tool_identity(binary: str, *, expected_version: str) -> dict[str, object]:
    requested = Path(binary)
    resolved = (
        requested.resolve()
        if requested.is_file()
        else Path(found).resolve()
        if (found := shutil.which(binary))
        else None
    )
    if resolved is None or not resolved.is_file():
        raise FileNotFoundError(f"Required SUMO tool is missing: {binary}")
    version_result = run_command([str(resolved), "--version"])
    version_output = "\n".join(
        part for part in (version_result.stdout, version_result.stderr) if part
    ).strip()
    match = re.search(r"\b(\d+\.\d+\.\d+)\b", version_output)
    observed_version = match.group(1) if match else ""
    return {
        "path": str(resolved),
        "sha256": file_sha256(resolved),
        "expected_version": expected_version,
        "observed_version": observed_version,
        "version_matches": bool(
            version_result.status == "pass" and observed_version == expected_version
        ),
        "version_output": version_output,
    }
