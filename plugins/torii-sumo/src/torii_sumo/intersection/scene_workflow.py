from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from torii_sumo.core.netedit import launch_netedit
from torii_sumo.core.routeability_audit import inspect_routeability_outputs
from torii_sumo.core.workflow_review_html import _artifact_hashes

from .nema_reference import build_nema_four_way_reference
from .scene_spec import resolve_intersection_scene_prompt
from .signalized_reference import build_signalized_intersection_reference


_COMMAND_FIELDS = ("command", "cwd", "status", "returncode", "stdout", "stderr", "error")
_SAFE_PREFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def run_intersection_scene_workflow(
    prompt: str,
    output_dir: Path,
    prefix: str = "intersection_scene",
    launch_netedit_after_build: bool = False,
    *,
    builder_func: Callable[..., dict[str, Any]] = build_nema_four_way_reference,
    netedit_func: Callable[[Path], dict[str, Any]] = launch_netedit,
) -> dict[str, Any]:
    _validate_prefix(prefix)
    resolved_spec = resolve_intersection_scene_prompt(prompt).model_dump(mode="json")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _expected_paths(output_dir, prefix)
    manifest_path = paths["artifact_manifest_file"]
    warnings: list[str] = []
    cleanup_errors: list[str] = []
    for path in paths.values():
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            error = f"{path.name}: {type(exc).__name__}: {exc}"
            cleanup_errors.append(error)
            warnings.append(f"cleanup failed: {error}")
    cleanup_error = "; ".join(cleanup_errors)

    if cleanup_error:
        build = {
            "status": "fail",
            "claim_status": "construction-invalid",
            "error": cleanup_error,
        }
    else:
        try:
            if builder_func is build_nema_four_way_reference and _needs_generic_builder(resolved_spec):
                build = build_signalized_intersection_reference(
                    output_dir,
                    prefix=prefix,
                    spec=resolved_spec,
                    run_sumo_smoke=True,
                    require_real_sumo=True,
                )
            else:
                build = builder_func(
                    output_dir,
                    prefix=prefix,
                    run_sumo_smoke=True,
                    require_real_sumo=True,
                )
        except Exception as exc:  # noqa: BLE001 - the manifest is the workflow's failure record.
            build = {
                "status": "fail",
                "claim_status": "construction-invalid",
                "error": f"{type(exc).__name__}: {exc}",
            }

    warnings.extend(_warnings(build.get("warnings")))
    if build.get("error"):
        warnings.append(str(build["error"]))

    if cleanup_error:
        audit: dict[str, Any] = {}
        netconvert_report: dict[str, Any] = {}
        sumo_report: dict[str, Any] = {}
    else:
        audit = _load_json(paths["audit_file"], "TLS audit", warnings)
        netconvert_report = _load_json(
            paths["netconvert_report_file"], "netconvert report", warnings
        )
        sumo_report = _load_json(paths["sumo_report_file"], "SUMO report", warnings)
    builder_status = str(build.get("status", "fail"))
    netconvert_blocked = (
        build.get("netconvert_status") == "blocked"
        or netconvert_report.get("status") == "blocked"
    )
    sumo_blocked = (
        build.get("sumo_smoke_status") == "blocked"
        or sumo_report.get("status") == "blocked"
    )
    builder_stage_unknown = (
        builder_status == "blocked"
        and not _is_known_status(build.get("netconvert_status"))
        and not _is_known_status(build.get("sumo_smoke_status"))
    )
    routeability_blocked = netconvert_blocked or sumo_blocked or builder_stage_unknown
    tls_blocked = netconvert_blocked or builder_stage_unknown

    if cleanup_error:
        routeability = {
            "status": "fail",
            "warnings": ["routeability was not evaluated because cleanup failed"],
        }
    elif routeability_blocked:
        routeability = {"status": "blocked"}
    elif paths["summary_file"].is_file() and paths["tripinfo_file"].is_file():
        try:
            routeability = inspect_routeability_outputs(
                summary_path=paths["summary_file"],
                tripinfo_path=paths["tripinfo_file"],
            )
        except Exception as exc:  # noqa: BLE001 - inspection failures are persisted in the workflow manifest.
            routeability = {
                "status": "fail",
                "warnings": [f"routeability inspection failed: {type(exc).__name__}: {exc}"],
            }
    else:
        missing = [
            path.name
            for path in (paths["summary_file"], paths["tripinfo_file"])
            if not path.is_file()
        ]
        routeability = {
            "status": "fail",
            "warnings": [f"missing routeability output: {name}" for name in missing],
        }
    routeability = _require_positive_smoke(routeability)
    warnings.extend(_warnings(routeability.get("warnings")))

    netconvert_evidence_present = paths["netconvert_report_file"].is_file()
    netconvert_evidence_ok = (
        _is_known_status(netconvert_report.get("status"))
        and type(netconvert_report.get("returncode")) is int
        and netconvert_report["returncode"] == 0
        and paths["net_file"].is_file()
        and _command_targets(netconvert_report, "-o", paths["net_file"])
    )
    netconvert_ok = (
        build.get("netconvert_status") == "pass"
        and netconvert_report.get("status") == "pass"
        and netconvert_evidence_ok
    )
    sumo_evidence_present = paths["sumo_report_file"].is_file()
    sumo_evidence_ok = (
        _is_known_status(sumo_report.get("status"))
        and type(sumo_report.get("returncode")) is int
        and sumo_report["returncode"] == 0
        and paths["sumocfg_file"].is_file()
        and _command_targets(sumo_report, "-c", paths["sumocfg_file"])
    )
    sumo_ok = (
        build.get("sumo_smoke_status") == "pass"
        and sumo_report.get("status") == "pass"
        and sumo_evidence_ok
    )
    tls_ok = _valid_tls(audit, resolved_spec)
    netconvert_status = _check_status(
        netconvert_ok,
        build.get("netconvert_status"),
        netconvert_report.get("status"),
        evidence_present=netconvert_evidence_present,
        evidence_ok=netconvert_evidence_ok,
    )
    sumo_load_status = _check_status(
        sumo_ok,
        build.get("sumo_smoke_status"),
        sumo_report.get("status"),
        evidence_present=sumo_evidence_present,
        evidence_ok=sumo_evidence_ok,
    )
    routeability_status = str(routeability.get("status", "fail"))
    tls_status = "blocked" if tls_blocked else "pass" if tls_ok else "fail"
    construction_checks = (
        netconvert_status,
        sumo_load_status,
        routeability_status,
        tls_status,
    )
    if cleanup_error or builder_status == "fail" or "fail" in construction_checks:
        construction_status = "fail"
    elif builder_status == "blocked" or "blocked" in construction_checks:
        construction_status = "blocked"
    elif builder_status == "pass" and all(status == "pass" for status in construction_checks):
        construction_status = "pass"
    else:
        construction_status = "fail"
    build_ok = construction_status == "pass"

    netedit = {"command": [], "status": "not_requested", "process_id": None}
    if launch_netedit_after_build:
        if build_ok:
            try:
                launched = netedit_func(paths["net_file"])
            except Exception as exc:  # noqa: BLE001 - external launcher failures are persisted, never promoted.
                launched = {
                    "status": "fail",
                    "warnings": [f"NetEdit launch failed: {type(exc).__name__}: {exc}"],
                }
            command = launched.get("command") or _netedit_command(launched, paths["net_file"])
            netedit = {
                "command": command,
                "status": str(launched.get("status", "fail")),
                "process_id": launched.get(
                    "process_id", launched.get("netedit_process_id")
                ),
            }
            warnings.extend(_warnings(launched.get("warnings")))
        else:
            netedit["status"] = "skipped"
            warnings.append("NetEdit was not launched because the build did not pass")

    if construction_status == "fail" or netedit["status"] == "fail":
        status = "fail"
    elif construction_status == "blocked" or netedit["status"] == "blocked":
        status = "blocked"
    else:
        status = "pass"
    claim_status = {
        "pass": "diagnostic-demo",
        "blocked": "blocked",
        "fail": "construction-invalid",
    }[construction_status]
    warnings = list(dict.fromkeys(warnings))

    output_files = (
        {}
        if cleanup_error
        else {
            key: _relative(path, output_dir)
            for key, path in paths.items()
            if key != "artifact_manifest_file" and path.is_file()
        }
    )
    output_files["artifact_manifest_file"] = manifest_path.name
    manifest = {
        "schema_version": "intersection-scene-artifacts/v1",
        "input_prompt": prompt,
        "resolved_spec": resolved_spec,
        "status": status,
        "claim_status": claim_status,
        "path_contract": {
            "output_files": "relative to the artifact manifest directory",
            "commands": "verbatim execution evidence; path arguments may be absolute or relative to command cwd",
        },
        "output_files": output_files,
        "commands": {
            "netconvert": _command(netconvert_report),
            "sumo": _command(sumo_report),
        },
        "checks": {
            "cleanup": {
                "status": "fail" if cleanup_error else "pass",
                **({"error": cleanup_error} if cleanup_error else {}),
            },
            "netconvert": {"status": netconvert_status},
            "sumo_load": {"status": sumo_load_status},
            "routeability": routeability,
            "tls": {"status": tls_status},
        },
        "tls_explanation": audit,
        "netedit": netedit,
        "warnings": warnings,
    }
    artifact_paths = {
        key: path
        for key, path in paths.items()
        if key == "artifact_manifest_file" or path.is_file()
    }
    artifact_hashes, artifact_hash_gate = _artifact_hashes(
        artifact_paths,
        base_dir=output_dir,
        excluded_keys={"artifact_manifest_file"},
    )
    manifest["artifact_hashes"] = artifact_hashes
    manifest["artifact_hash_gate"] = artifact_hash_gate
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "status": status,
        "claim_status": claim_status,
        "netconvert_status": netconvert_status,
        "sumo_load_status": sumo_load_status,
        "routeability_status": routeability_status,
        "tls_status": tls_status,
        "netedit_status": netedit["status"],
        "input_prompt": prompt,
        "resolved_spec": resolved_spec,
        "artifact_manifest_file": str(manifest_path),
        "artifact_hashes": artifact_hashes,
        "artifact_hash_gate": artifact_hash_gate,
        "artifact_hash_gate_status": artifact_hash_gate["status"],
        "net_file": str(paths["net_file"]),
        "sumocfg_file": str(paths["sumocfg_file"]),
        "warnings": warnings,
    }


def _validate_prefix(prefix: str) -> None:
    if (
        not isinstance(prefix, str)
        or _SAFE_PREFIX.fullmatch(prefix) is None
        or prefix.upper() in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(
            "prefix must be a nonempty filename-safe stem using only letters, digits, '_' or '-'"
        )


def _expected_paths(output_dir: Path, prefix: str) -> dict[str, Path]:
    return {
        "node_file": output_dir / f"{prefix}.nod.xml",
        "edge_file": output_dir / f"{prefix}.edg.xml",
        "connection_file": output_dir / f"{prefix}.con.xml",
        "type_file": output_dir / f"{prefix}.typ.xml",
        "tllogic_file": output_dir / f"{prefix}.tll.xml",
        "additional_file": output_dir / f"{prefix}.add.xml",
        "net_file": output_dir / f"{prefix}.net.xml",
        "route_file": output_dir / f"{prefix}.rou.xml",
        "sumocfg_file": output_dir / f"{prefix}.sumocfg",
        "summary_file": output_dir / f"{prefix}_summary.xml",
        "tripinfo_file": output_dir / f"{prefix}_tripinfo.xml",
        "audit_file": output_dir / f"{prefix}_nema_audit.json",
        "evidence_file": output_dir / f"{prefix}_evidence.md",
        "netconvert_report_file": output_dir / f"{prefix}_netconvert_report.json",
        "sumo_report_file": output_dir / f"{prefix}_sumo_report.json",
        "sumo_log_file": output_dir / f"{prefix}_sumo.log",
        "sumo_error_log_file": output_dir / f"{prefix}_sumo_errors.log",
        "artifact_manifest_file": output_dir / f"{prefix}_artifact_manifest.json",
    }


def _load_json(path: Path, label: str, warnings: list[str]) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(f"could not read {label}: {type(exc).__name__}: {exc}")
        return {}
    if not isinstance(value, dict):
        warnings.append(f"{label} is not a JSON object")
        return {}
    return value


def _command(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in _COMMAND_FIELDS if key in report}


def _warnings(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _is_known_status(value: Any) -> bool:
    return value == "pass" or value == "fail" or value == "blocked"


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _require_positive_smoke(routeability: dict[str, Any]) -> dict[str, Any]:
    if routeability.get("status") != "pass":
        return routeability
    summary = routeability.get("summary") or {}
    tripinfo = routeability.get("tripinfo") or {}
    loaded = _optional_int(summary.get("loaded"))
    inserted = _optional_int(summary.get("inserted"))
    arrived = _optional_int(summary.get("arrived"))
    trip_count = _optional_int(tripinfo.get("trip_count"))
    if (
        loaded is not None
        and loaded == inserted == arrived == trip_count
        and loaded > 0
    ):
        return routeability
    warning = "routeability smoke requires loaded == inserted == arrived == trip_count > 0"
    return {
        **routeability,
        "status": "fail",
        "claim_status": "construction-invalid",
        "routeability_status": "invalid-smoke-demand",
        "warnings": list(dict.fromkeys([*_warnings(routeability.get("warnings")), warning])),
    }


def _command_targets(report: dict[str, Any], option: str, expected: Path) -> bool:
    command = report.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        return False
    try:
        option_index = command.index(option)
        target = command[option_index + 1]
        cwd = report.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd):
            return False
        target_path = Path(target)
        if not target_path.is_absolute():
            target_path = (Path(cwd) if cwd is not None else Path.cwd()) / target_path
        return os.path.normcase(str(target_path.resolve())) == os.path.normcase(
            str(expected.resolve())
        )
    except (IndexError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _needs_generic_builder(spec: dict[str, Any]) -> bool:
    return bool(
        spec.get("topology") != "four_way"
        or spec.get("approach_count") != 4
        or spec.get("controller") != "nema_reference"
        or spec.get("pedestrian_crossing")
        or spec.get("bicycle_support")
        or spec.get("ramp")
    )


def _valid_tls(audit: dict[str, Any], spec: dict[str, Any] | None = None) -> bool:
    if audit.get("contract") == "generic-tls/v1":
        return _valid_generic_tls(audit, spec or {})
    expected_phases = [str(index) for index in range(1, 9)]
    phase_order = audit.get("phase_order")
    movement_map = audit.get("movement_map")
    params = audit.get("params")
    if (
        audit.get("tls_id") != "J0"
        or audit.get("controlled_link_count") != 12
        or audit.get("tls_signal_group_count") != 8
        or phase_order != expected_phases
        or not isinstance(movement_map, list)
        or len(movement_map) != 12
        or not isinstance(params, dict)
        or params.get("ring1") != "1,2,3,4"
        or params.get("ring2") != "5,6,7,8"
    ):
        return False
    movements: set[tuple[str, str, str, str]] = set()
    covered_groups: set[int] = set()
    for row in movement_map:
        if not isinstance(row, dict):
            return False
        movement = tuple(
            row.get(key) for key in ("from", "fromLane", "to", "toLane")
        )
        link_index = row.get("linkIndex")
        if (
            not all(isinstance(value, str) and value for value in movement)
            or movement in movements
            or not isinstance(link_index, int)
            or isinstance(link_index, bool)
            or not 0 <= link_index < 8
            or row.get("nemaPhase") != phase_order[link_index]
        ):
            return False
        movements.add(movement)
        covered_groups.add(link_index)
    return covered_groups == set(range(8))


def _valid_generic_tls(audit: dict[str, Any], spec: dict[str, Any]) -> bool:
    movement_map = audit.get("movement_map")
    phase_order = audit.get("phase_order")
    phase_states = audit.get("phase_states")
    feature_contract = audit.get("feature_contract")
    if (
        audit.get("tls_id") != "TLS0"
        or audit.get("controller") != spec.get("controller")
        or audit.get("tls_semantics") != spec.get("tls_semantics")
        or audit.get("topology") != spec.get("topology")
        or audit.get("approach_count") != spec.get("approach_count")
        or not isinstance(movement_map, list)
        or not movement_map
        or audit.get("controlled_link_count") != len(movement_map)
        or not isinstance(phase_order, list)
        or not phase_order
        or not isinstance(phase_states, list)
        or len(phase_order) != len(phase_states)
        or not isinstance(feature_contract, dict)
    ):
        return False
    expected_indexes = list(range(len(movement_map)))
    indexes = [row.get("linkIndex") for row in movement_map if isinstance(row, dict)]
    if indexes != expected_indexes:
        return False
    if any(
        not isinstance(row, dict)
        or not all(isinstance(row.get(key), str) and row.get(key) for key in ("from", "to", "fromLane", "toLane", "mode"))
        or row.get("turn") not in {"r", "s", "l", "u"}
        for row in movement_map
    ):
        return False
    if any(not isinstance(state, str) or len(state) < len(movement_map) for state in phase_states):
        return False
    expected_features = {
        "pedestrian_crossing": bool(spec.get("pedestrian_crossing")),
        "bicycle_support": bool(spec.get("bicycle_support")),
        "ramp": bool(spec.get("ramp")),
    }
    if any(feature_contract.get(key) != value for key, value in expected_features.items()):
        return False
    if expected_features["bicycle_support"]:
        if feature_contract.get("bicycle_connection_count", 0) <= 0:
            return False
        if not any(row.get("mode") == "bicycle" for row in movement_map):
            return False
    elif feature_contract.get("bicycle_connection_count", 0) != 0:
        return False
    if expected_features["ramp"] and not feature_contract.get("ramp_approach"):
        return False
    if expected_features["pedestrian_crossing"] and feature_contract.get("pedestrian_crossing_count", 0) <= 0:
        return False
    return True


def _relative(path: Path, output_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), output_dir.resolve())).as_posix()


def _check_status(
    passed: bool,
    build_reported: Any,
    evidence_reported: Any,
    *,
    evidence_present: bool,
    evidence_ok: bool,
) -> str:
    if passed:
        return "pass"
    if (
        build_reported == "fail"
        or evidence_reported == "fail"
        or (evidence_present and not evidence_ok)
    ):
        return "fail"
    if build_reported == "blocked" or evidence_reported == "blocked":
        return "blocked"
    return "fail"


def _netedit_command(result: dict[str, Any], net_file: Path) -> list[str]:
    binary = result.get("netedit_binary")
    if not binary:
        return []
    option = "--sumocfg-file" if net_file.suffix.lower() == ".sumocfg" else "-s"
    return [str(binary), option, str(net_file)]
