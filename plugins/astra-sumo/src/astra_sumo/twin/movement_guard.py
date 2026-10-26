"""L2 deterministic movement probes (M3): run the probes on the parent and the candidate net of one verify-edit step.

The design's L2 tier: ``run_candidate_movement_probes`` (``core/movement_routeability.py``) when a supported movement
manifest exists for both nets (the goal's frozen manifest, re-bound to each net by ``derive_manifest``), otherwise
``run_all_turn_movement_smoke`` on every touched junction (one vehicle per movement, of the first class its loaded turn
permissions admit, so bus, bicycle and other non-passenger movements are driven too; a movement no class may use is
listed as skipped). The expected movements per junction come from the frozen scored ``movement_inventory`` facts, read
through each net's effective fact table, so a lane-count remap keeps physical lanes. The caller
(``build_profile.run_movement_guard``) derives the junctions, expected movements and lane remap; this module only runs
SUMO and writes one record, ``movement-guard.json``, into a new directory. ``build_gate.l2_rule`` judges it: no new
probe or smoke failure on the candidate against the parent.

Explore evidence only: a smoke vehicle that arrives shows a drivable movement under the net's own signals, not field
legality, signal timing or safety.
"""

from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.artifact_io import write_json_atomic
from ..core.candidate_contracts import file_sha256
from .build_gate import MOVEMENT_GUARD_SCHEMA, NETWORK_RUN

SMOKE_END_S = 900
MANIFEST_SCHEMAS = ("torii.hamburg-aerial-corridor-candidate/v1", "torii.engineering-topology-build/v1")
EVENT_KEYS = ("arrived", "running", "waiting", "teleports", "collisions", "discarded")
RECORD = "movement-guard.json"
CLAIM = ("Explore evidence only: deterministic single-vehicle probes on the touched junctions; a movement that "
         "arrives is drivable under the net's own signals, not field legality, signal timing or safety.")


def supported_manifest(path: Path | None, net_sha256: str) -> bool:
    """A movement manifest the probes accept, whose network record is exactly this net."""
    if path is None:
        return False
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    record = (manifest.get("artifacts") or {}).get("network") or {}
    return manifest.get("schema") in MANIFEST_SCHEMAS and str(record.get("sha256", "")).lower() == net_sha256


def _lane(edge: str, index: Any) -> str:
    return f"{edge}_{index}"


def smoke_summary(report: dict) -> dict:
    """The parts of one ``run_all_turn_movement_smoke`` report that ``build_gate.l2_failing`` reads."""
    driven = [[_lane(row["from"], row["from_lane"]), _lane(row["to"], row["to_lane"])] for row in report["movements"]]
    arrived_ids = set(report.get("arrived_vehicle_ids") or ())
    arrived = [pair for pair, vehicle in zip(driven, report["expected_vehicle_ids"]) if vehicle in arrived_ids]
    skipped = [[_lane(a, b), _lane(c, d)] for a, b, c, d in report.get("skipped_movements", [])]
    classes = Counter(row.get("vehicle_class", "passenger") for row in report["movements"])
    checks = report["checks"]
    summary = (report.get("inspection") or {}).get("summary") or {}
    events = {key: summary.get(key) for key in EVENT_KEYS}
    run_ok = bool(checks.get("sumo_command") and checks.get("source_immutable")
                  and all(value is not None for value in events.values()))
    return {"status": "ran", "run_ok": run_ok, "driven": sorted(driven), "arrived": sorted(arrived),
            "skipped": sorted(skipped), "events": events, "vehicle_classes": dict(sorted(classes.items())),
            "smoke": {"status": report["status"], "failed_checks": sorted(key for key, ok in checks.items() if not ok)},
            "report": {"path": report["report_file"], "sha256": file_sha256(Path(report["report_file"]))}}


def _connection_pair(connection) -> list[str]:
    return [_lane(*connection[:2]), _lane(*connection[2:4])]


def probe_summary(report: dict) -> dict:
    """The parts of one ``run_candidate_movement_probes`` report that ``build_gate.l2_failing`` reads: the movements
    it probed (``tested``), those that did not pass (``failing``) and those it lists as not tested."""
    results = [row for row in report["results"] if row.get("sumo_connection")]
    not_tested = report.get("not_tested") or []
    return {"status": "ran", "run_ok": bool(report.get("source_immutable")),
            "failing": sorted(_connection_pair(row["sumo_connection"]) for row in results
                              if row.get("status") != "pass"),
            "tested": sorted(_connection_pair(row["sumo_connection"]) for row in results),
            "not_tested": sorted(_connection_pair(row["sumo_connection"]) for row in not_tested
                                 if isinstance(row, dict) and row.get("sumo_connection")),
            "not_tested_count": len(not_tested),
            "report": {"path": report["report_file"], "sha256": file_sha256(Path(report["report_file"]))}}


def junction_smoke(net: Path, junction: str, pairs: list, output_dir: Path, sumo_binary: str) -> dict:
    """``run_all_turn_movement_smoke`` on one junction, every movement driven by a vehicle class its loaded turn
    permissions admit (passenger first), with the expected movements' counts as its declared expectations (reported
    only), summarized for ``build_gate.l2_failing``."""
    from ..core.movement_routeability import run_all_turn_movement_smoke

    report = run_all_turn_movement_smoke(
        net_file=net, target_junction_id=junction, output_dir=output_dir, sumo_binary=sumo_binary,
        expected_movement_count=len(pairs),
        expected_incoming_approach_count=len({pair[0].rpartition("_")[0] for pair in pairs}),
        expected_outgoing_approach_count=len({pair[1].rpartition("_")[0] for pair in pairs}),
        end_time_s=SMOKE_END_S, vehicle_class_per_movement=True)
    return smoke_summary(report)


def _renamed(edge: Any, index: Any, mapping: dict[str, dict[str, str]]) -> Any:
    """A lane index under the lane remap, in its original type; None when the lane was removed."""
    if str(edge) not in mapping:
        return index
    new = mapping[str(edge)].get(str(index))
    return None if new is None else (int(new) if isinstance(index, int) else str(new))


def derive_manifest(frozen: Path, net: Path, lane_mapping: dict[str, dict[str, str]], out_dir: Path) -> Path:
    """The goal's movement manifest re-bound to ``net`` in a NEW ``out_dir``: its network record names ``net`` and
    its sha256, and every declared SUMO connection is renamed through ``lane_mapping`` (the net's composed lane-count
    remap). A declared movement on a removed lane becomes not tested (Hamburg schema) or is dropped (plan schema), so
    an expected movement there is a failure token in ``build_gate.l2_failing``."""
    frozen, out_dir = Path(frozen).resolve(strict=True), Path(out_dir)
    manifest = json.loads(frozen.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True)

    def renamed(connection: list) -> list | None:
        start, end = _renamed(connection[0], connection[1], lane_mapping), _renamed(connection[2], connection[3],
                                                                                   lane_mapping)
        return None if start is None or end is None else [connection[0], start, connection[2], end, *connection[4:]]

    if manifest.get("schema") == "torii.engineering-topology-build/v1":
        record = manifest["sources"]["topology"]
        source = Path(record["path"]) if Path(record["path"]).is_absolute() else frozen.parent / record["path"]
        if file_sha256(source) != record["sha256"]:
            raise ValueError("movement guard refused: the movement manifest's construction plan changed")
        topology = json.loads(source.read_text(encoding="utf-8"))
        kept = []
        for row in topology.get("connections", []):
            pair = renamed([row["from"], row["fromLane"], row["to"], row["toLane"]])
            if pair is not None:
                kept.append({**row, "fromLane": pair[1], "toLane": pair[3]})
        topology["connections"] = kept
        path = out_dir / "topology.json"
        write_json_atomic(path, topology, sort_keys=True)
        manifest["sources"]["topology"] = {**record, "path": str(path.resolve()), "sha256": file_sha256(path)}
        manifest["declared_topology"] = topology
    else:
        audit = manifest.get("official_connection_audit") or {}
        required, missing = [], []
        for row in audit.get("required", []):
            pair = renamed(row["sumo_connection"])
            if pair is None:
                missing.append({**row, "reason": "lane removed by a declared lane-count remap"})
            else:
                required.append({**row, "sumo_connection": pair})
        boundary = [{**row, "sumo_connection": pair} for row in audit.get("boundary_connections", [])
                    if (pair := renamed(row["sumo_connection"])) is not None]
        manifest["official_connection_audit"] = {**audit, "required": required, "boundary_connections": boundary,
                                                 "ambiguous": [*audit.get("ambiguous", []), *missing]}
    network = (manifest.get("artifacts") or {}).get("network") or {}
    manifest.setdefault("artifacts", {})["network"] = {**network, "path": str(Path(net).resolve()),
                                                       "sha256": file_sha256(Path(net))}
    path = out_dir / "movement-manifest.json"
    write_json_atomic(path, manifest, sort_keys=True)
    return path


def run_movement_guard(*, parent_net: Path, candidate_net: Path, junctions: list[str],
                       expected: dict[str, dict[str, list]], lane_mapping: dict[str, dict[str, str]],
                       output_dir: Path, sumo_binary: str, verify_edit: dict,
                       manifests: dict[str, Path] | None = None, smoke: Callable | None = None,
                       manifest: dict | None = None) -> dict:
    """Run L2 on both nets into a NEW ``output_dir`` and write ``movement-guard.json``; returns the record.

    ``manifest`` is the goal's frozen movement manifest ``{"path", "sha256", "lane_mapping": {"parent", "candidate"}}``
    (each net's composed lane remap): it is re-bound to each net (``derive_manifest``) inside ``output_dir`` and the
    movement probes run on both. ``manifests`` passes manifests already bound to the two nets instead.
    ``smoke(net, junction, pairs, output_dir)`` replaces ``junction_smoke`` (tests without SUMO)."""
    from ..core.movement_routeability import run_candidate_movement_probes

    smoke = smoke or (lambda net, junction, pairs, out: junction_smoke(net, junction, pairs, out, sumo_binary))
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"{output_dir} exists; every movement guard is written to a new directory")
    nets = {"parent": Path(parent_net).resolve(strict=True), "candidate": Path(candidate_net).resolve(strict=True)}
    hashes = {role: file_sha256(path) for role, path in nets.items()}
    if manifest is not None and file_sha256(Path(manifest["path"])) != manifest["sha256"]:
        raise ValueError("movement guard refused: the goal's movement manifest changed since goal-create")
    output_dir.mkdir(parents=True)
    if manifest is not None:
        manifests = {role: derive_manifest(Path(manifest["path"]), net, manifest["lane_mapping"][role],
                                           output_dir / f"{role}-manifest") for role, net in nets.items()}
    manifests = manifests or {}
    probes = all(supported_manifest(manifests.get(role), hashes[role]) for role in nets)
    started = time.perf_counter()
    runs: dict[str, dict[str, dict]] = {"parent": {}, "candidate": {}}
    for role, net in nets.items():
        if probes:
            report = run_candidate_movement_probes(candidate_manifest=manifests[role], output_dir=output_dir / role,
                                                   sumo_binary=sumo_binary)
            runs[role][NETWORK_RUN] = probe_summary(report)
            continue
        present = _junction_ids(net)
        for index, junction in enumerate(junctions):
            if junction not in present:
                runs[role][junction] = {"status": "absent"}
                continue
            runs[role][junction] = smoke(net, junction, expected[role].get(junction, []),
                                         output_dir / role / f"j{index:03d}")
    record = {"schema": MOVEMENT_GUARD_SCHEMA, "method": "movement_probes" if probes else "all_turn_smoke",
              "parent": {"path": str(nets["parent"]), "sha256": hashes["parent"]},
              "candidate": {"path": str(nets["candidate"]), "sha256": hashes["candidate"]},
              "verify_edit": verify_edit, "junctions": list(junctions), "expected": expected,
              "lane_mapping": lane_mapping, "runs": runs,
              "manifests": ({role: {"path": str(manifests[role]), "sha256": file_sha256(Path(manifests[role]))}
                             for role in nets} if probes else None),
              "manifest_source": ({"path": str(manifest["path"]), "sha256": manifest["sha256"]}
                                  if manifest is not None else None),
              "smoke": {"end_s": SMOKE_END_S, "vehicle": "per movement, the first class of the core's "
                        "SMOKE_VEHICLE_CLASSES its turn permissions admit (passenger first, sigma 0)",
                        "vehicle_class_per_movement": True,
                        "reported_only": "the smoke's own 4-way count, approach, turn and tls checks"},
              "nets_unchanged": all(file_sha256(path) == hashes[role] for role, path in nets.items()),
              "claim": CLAIM}
    write_json_atomic(output_dir / RECORD, record, sort_keys=True)
    (output_dir / "movement-guard.timing.json").write_text(
        json.dumps({"wall_s": round(time.perf_counter() - started, 3)}) + "\n", encoding="utf-8")
    return record


def load_record(path: Path) -> tuple[dict, str]:
    """``(record, sha256)`` of a movement-guard directory or file."""
    path = Path(path)
    if path.is_dir():
        path = path / RECORD
    data = path.read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def _junction_ids(net: Path) -> set[str]:
    return {row.get("id") for row in ET.parse(net).getroot().findall("junction") if row.get("type") != "internal"}
