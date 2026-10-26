"""ASTRA twin profiler CLI: doctor, resolve, goal-create (calibrate, build), profile (calibrate, build) and the Build
known-answer harnesses (k1, k2).

Only ``profile build --ledger`` and ``k2 run`` start SUMO tools: ``verify_network_edit`` in explore mode (L3 on the
goal's frozen per-seed trips; k2 injects its faults with no routeability seed) and the L2 movement guard
(``build_profile.run_movement_guard``); every other command is offline. Exit codes follow ``astra`` (``cli.py``): 0 for
``pass``, ``measured`` or ``no_effect``; 3 for ``blocked``, which covers a failed doctor or resolve check, a blocked
profile, a failed known-answer or timing check and a refused or failed command (its ``reason`` and ``error_type`` say
which); 2 for an argparse usage error. The CLI writes only to the paths it is given (the store, a new profile or
harness directory and, for ``--ledger``, its new siblings ``<out>.verify-edit`` and ``<out>.movement-guard``;
doctor's scratch inside the clone),
with transient files beside them, and writes no bytecode, so a verifier clone stays as checked out. The one
exception is verify-edit's command runner (``core/command_runner.py``), which keeps its transient ``astra-command-*``
directories under the system temp directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"
sys.dont_write_bytecode = True

# The clone's own src always comes first. The venv's editable .pth appends it at the end of sys.path, so an earlier
# entry (PYTHONPATH, another install) would otherwise shadow the code whose commit the profile records.
sys.path[:] = [entry for entry in sys.path
               if os.path.normcase(os.path.abspath(entry or ".")) != os.path.normcase(str(SRC_ROOT))]
sys.path.insert(0, str(SRC_ROOT))

try:
    import astra_sumo
    from astra_sumo.twin import build_profile, count_profile, doctor, goal, ids, known_answers
except ImportError as error:
    print(json.dumps({"status": "blocked", "reason": f"cannot import astra_sumo.twin from {SRC_ROOT}: {error}",
                      "error_type": type(error).__name__}, ensure_ascii=False))
    raise SystemExit(3)


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}: {value if not isinstance(value, (dict, list)) else json.dumps(value, ensure_ascii=False)}")


def _code(status: str) -> int:
    return 0 if status in {"pass", "measured", "no_effect"} else 3


def _clone_code() -> str:
    """Repository-relative path of the astra_sumo package in use; code from outside this clone is refused."""
    for module in (astra_sumo, count_profile):
        location = Path(module.__file__).resolve()
        if not location.is_relative_to(SRC_ROOT):
            raise ValueError(f"{module.__name__} was imported from {location}, outside this clone's {SRC_ROOT}")
    return Path(astra_sumo.__file__).resolve().relative_to(REPOSITORY_ROOT).as_posix()


def _doctor(args: argparse.Namespace) -> int:
    report = doctor.run_doctor(REPOSITORY_ROOT, forbidden_paths=args.forbid_path or [],
                               expected_commit=args.expect_commit,
                               scratch_root=Path(args.scratch_root) if args.scratch_root else None)
    _emit(report, args.json)
    return _code(report["status"])


def _resolve(args: argparse.Namespace) -> int:
    _clone_code()
    report = goal.resolve_calibrate_case(REPOSITORY_ROOT, args.packet, args.case)
    _emit(report, args.json)
    return _code(report["status"])


def _goal_create(args: argparse.Namespace) -> int:
    _clone_code()
    if args.capability == "build":
        return _goal_create_build(args)
    created = goal.create_calibrate_goal(REPOSITORY_ROOT, args.packet, args.case, goal_id=args.goal_id)
    path, written = goal.write_goal(created, Path(args.store))
    data = path.read_bytes()
    _emit({"status": "pass", "write": written, "goal_id": created.goal_id, "goal": str(path),
           "goal_ref": ids.goal_reference(created.goal_id, data), "baseline": created.baseline.model_dump(),
           "hold_nodes": created.run.junction_body_hold_nodes, "timeout_s": created.run.timeout_s,
           "all_stopped_timeout_s": created.run.all_stopped_timeout_s}, args.json)
    return 0


def _goal_create_build(args: argparse.Namespace) -> int:
    if not args.net:
        raise ValueError("goal-create --capability build needs --net")
    created, facts, summary = goal.create_build_goal(
        REPOSITORY_ROOT, net=Path(args.net), facts=Path(args.facts) if args.facts else None,
        reference_net=Path(args.reference_net) if args.reference_net else None, site_id=args.site_id,
        target_date=args.target_date, goal_id=args.goal_id, mutable_edge_ids=args.mutable_edge or [],
        mutable_junction_ids=args.mutable_junction or [], ops=args.op, seeds=args.seed,
        lane_artifacts=[Path(path) for path in args.lane_artifact or []], sigma_m=args.sigma_m,
        map_xml=Path(args.map_xml) if args.map_xml else None, scratch_dir=Path(args.store),
        movement_manifest=Path(args.movement_manifest) if args.movement_manifest else None)
    path, written, facts_file = goal.write_build_goal(created, facts, Path(args.store))
    data = path.read_bytes()
    _emit({"status": "pass", "write": written, "goal_id": created.goal_id, "goal": str(path),
           "goal_ref": ids.goal_reference(created.goal_id, data), "facts": str(facts_file),
           "facts_ref": created.reference.facts, "frozen_sha256": created.frozen.sha256, **summary}, args.json)
    return 0


def _profile_build(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    code = _clone_code()
    ledger_only = [name for name, value in (("--netconvert", args.netconvert), ("--no-routeability",
                   args.no_routeability), ("--routeability-seed", args.routeability_seed)) if value]
    if ledger_only and not args.ledger:
        raise ValueError(f"{', '.join(ledger_only)} only apply with --ledger")
    if args.movement_guard and not args.verify_edit:
        raise ValueError("--movement-guard only applies with --verify-edit (--ledger runs its own)")
    if args.no_routeability and args.routeability_seed:
        raise ValueError("--no-routeability and --routeability-seed contradict each other")
    loaded, data, facts, facts_file = goal.load_build_goal(Path(args.goal))
    state = doctor.repository_state(REPOSITORY_ROOT)
    verifier = {"commit": state["commit"], "clean": state["clean"], "astra_sumo": code}
    parent = Path(args.parent_profile) if args.parent_profile else None
    verify_edit = Path(args.verify_edit) if args.verify_edit else None
    guard = Path(args.movement_guard) if args.movement_guard else None
    if args.ledger:
        if Path(args.out).exists():
            raise FileExistsError(f"{args.out} exists; every profile is written to a new directory")
        verify_edit = Path(f"{args.out}.verify-edit")
        seeds = (() if args.no_routeability else None if args.routeability_seed is None
                 else tuple(args.routeability_seed))
        netconvert = args.netconvert or "netconvert"
        verified = build_profile.run_verify_edit(loaded, ledger_file=Path(args.ledger), facts_path=facts_file,
                                                 output_dir=verify_edit, repository_root=REPOSITORY_ROOT,
                                                 netconvert_binary=netconvert, routeability_seeds=seeds,
                                                 parent_profile=parent, goal_bytes=data, verifier=verifier)
        if verified["status"] in {"measured", "no_effect"} and Path(verified["candidate_net_file"]).is_file():
            guard = Path(f"{args.out}.movement-guard")
            build_profile.run_movement_guard(loaded, data, facts, facts_path=facts_file, verify_edit=verify_edit,
                                             output_dir=guard, verifier=verifier, parent_profile=parent,
                                             repository_root=REPOSITORY_ROOT,
                                             sumo_binary=build_profile.sumo_beside(netconvert))
    profile = build_profile.write_build_profile(
        loaded, data, facts, facts_path=facts_file, repository_root=REPOSITORY_ROOT, out_dir=Path(args.out),
        verifier=verifier, net=Path(args.candidate_net) if args.candidate_net else None, verify_edit=verify_edit,
        parent_profile=parent, movement_guard_record=guard)
    gate, structure = profile["gate"], profile["source_structure"]
    _emit({"status": profile["status"], "out": str(Path(args.out)), "goal": profile["goal"], "cand": profile["cand"],
           "parent": profile["parent"], "subject": profile["subject"], "verifier": profile["verifier"],
           "screening": gate["screening"], "first_fail": gate.get("first_fail"),
           # Decision D1: a dirty goal source blocks promotion without a human, whatever the screening says.
           "source_dirty": profile["source_dirty"],
           "source_structure": {"count": structure["count"],
                                **({"promotion": structure["promotion"]} if "promotion" in structure else {})},
           "claim": profile["claim"], "V": profile["V"]["cand"],
           "V_parent": profile["V"]["parent"],
           "invariants": {check["id"]: check["pass"] for check in profile["invariants"]},
           "L2": (gate.get("L2") or {}).get("result"), "L3": (gate.get("L3") or {}).get("result"),
           "hotspots": profile["hotspot_ids"][:5], "verify_edit": profile["inputs"]["verify_edit"],
           "movement_guard": profile["inputs"]["movement_guard"],
           "compact": profile["compact"], "details": profile["details"],
           "timing": {**profile["timing"], "cli_s": round(time.perf_counter() - started, 3)}}, args.json)
    return _code(profile["status"])


def _profile(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    code = _clone_code()
    if not args.run.startswith("packet:"):
        raise ValueError("--run must be packet:<case-relative run directory> in M1")
    loaded, data = goal.load_goal(Path(args.goal))
    reader = goal.case_reader(REPOSITORY_ROOT, loaded.inputs.packet, loaded.inputs.case)
    state = doctor.repository_state(REPOSITORY_ROOT)
    profile = count_profile.write_calibrate_profile(
        loaded, data, reader, run_dir=args.run.removeprefix("packet:"), out_dir=Path(args.out),
        verifier={"commit": state["commit"], "clean": state["clean"], "astra_sumo": code})
    _emit({"status": profile["status"], "out": str(Path(args.out)), "goal": profile["goal"], "cand": profile["cand"],
           "verifier": profile["verifier"],
           "invariants": {check["id"]: check["pass"] for check in profile["invariants"]},
           "score": {key: profile["score"][key] for key in ("tae_main", "sse", "max", "exact", "init52_tae",
                                                            "all182_tae")},
           "compact": profile["compact"], "details": profile["details"],
           "timing": {**profile["timing"], "cli_s": round(time.perf_counter() - started, 3)}}, args.json)
    return _code(profile["status"])


def _known_answers(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    code = _clone_code()
    state = doctor.repository_state(REPOSITORY_ROOT)
    verifier = {"commit": state["commit"], "clean": state["clean"], "astra_sumo": code}
    if args.harness == "k1":
        report = known_answers.run_k1(Path(args.out), repository_root=REPOSITORY_ROOT, verifier=verifier)
        summary = {"rates": report["rates"], "groups": report["groups"], "misses": report["misses"],
                   "nema_also_blocks_wrong_turn": report["wrong_turn"]["nema_also_blocks"]}
    else:
        report = known_answers.run_k2(Path(args.out), repository_root=REPOSITORY_ROOT, verifier=verifier,
                                      netconvert_binary=args.netconvert, perf=not args.no_perf)
        summary = {"cases": {case["id"]: case["status"] for case in report["cases"]},
                   "ring1_direction": {key: report["ring1_direction"][key] for key in (
                       "rule", "checked_count", "finding_count", "ambiguous_count", "baseline_sha256")},
                   "perf_internal_paths": report["perf_internal_paths"], "timing_checks": report["timing_checks"]}
    path = Path(args.out) / f"{args.harness}-report.json"
    # Timing checks (acceptance f) never enter the deterministic report, but a failed one still blocks.
    failed_timing = sorted(name for name, ok in report.get("timing_checks", {}).items() if not ok)
    status = "blocked" if failed_timing else report["status"]
    _emit({"status": status, "harness": args.harness, "out": str(Path(args.out)), "verifier": verifier,
           "report": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "status": report["status"]},
           "failed_checks": report["failed_checks"], "failed_timing_checks": failed_timing, **summary,
           "timing": {**report["timing"], "cli_s": round(time.perf_counter() - started, 3)}}, args.json)
    return _code(status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA twin profiler (explore only): doctor, resolve, goal-create, "
                                                 "profile calibrate|build and the Build known-answer harnesses k1|k2. "
                                                 "Only 'profile build --ledger' and 'k2 run' start SUMO tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check SUMO, libsumo, the import path and the clone state.")
    check.add_argument("--expect-commit", help="Commit that HEAD must equal; default: the clone directory's name.")
    check.add_argument("--forbid-path", action="append", help="Source path that must not be on any sys.path.")
    check.add_argument("--scratch-root", help="Where the child's empty working directory is created; default: the "
                                              "clone's git-ignored outputs/. It is removed afterwards.")
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=_doctor)
    resolve = commands.add_parser("resolve", help="Resolve a packet case through its byte references.")
    create = commands.add_parser("goal-create", help="Write <store>/goals/<goal_id>/goal.json (calibrate: a packet "
                                                     "case; build: a source net and reference facts).")
    for sub, required in ((resolve, True), (create, False)):
        sub.add_argument("--packet", required=required, help="Repository-relative benchmark packet directory.")
        sub.add_argument("--case", required=required)
        sub.add_argument("--json", action="store_true")
    resolve.set_defaults(handler=_resolve)
    create.add_argument("--capability", required=True, choices=["calibrate", "build"])
    create.add_argument("--store", required=True)
    create.add_argument("--goal-id", help="Override the derived goal id.")
    create.add_argument("--net", help="build: the source net the scored set is frozen on.")
    create.add_argument("--facts", help="build: an astra.network-facts/v1 table.")
    create.add_argument("--reference-net", help="build: a clean net; produces same-id facts when --facts is absent "
                                                "and sets the INT baseline.")
    create.add_argument("--site-id", help="build with --reference-net: the fact table's site id.")
    create.add_argument("--target-date", help="build with --reference-net: the fact table's target date.")
    create.add_argument("--mutable-edge", action="append", help="build: a mutable edge id (repeatable).")
    create.add_argument("--mutable-junction", action="append", help="build: a mutable junction id (repeatable).")
    create.add_argument("--op", action="append", help="build: an allowed typed op (repeatable; default all 14).")
    create.add_argument("--seed", action="append", type=int, help="build: a routeability seed (repeatable).")
    create.add_argument("--lane-artifact", action="append", help="build: a lane-bound artifact file (repeatable).")
    create.add_argument("--sigma-m", type=float, default=0.0, help="build: declared combined source sigma in m.")
    create.add_argument("--map-xml", help="build: a MAP XML whose connections the sg_partition check reads.")
    create.add_argument("--movement-manifest", help="build: a supported movement manifest of the source net; L2 then "
                        "runs the movement probes on manifests re-bound to each step's nets.")
    create.set_defaults(handler=_goal_create)
    profile = commands.add_parser("profile", help="Profile a run against a goal.")
    kinds = profile.add_subparsers(dest="kind", required=True)
    calibrate = kinds.add_parser("calibrate", help="Offline Calibrate count profile of the goal's baseline run.")
    calibrate.add_argument("--goal", required=True)
    calibrate.add_argument("--run", required=True, help="packet:<case-relative run directory>")
    calibrate.add_argument("--out", required=True, help="New profile directory; an existing one is refused.")
    calibrate.add_argument("--json", action="store_true")
    calibrate.set_defaults(handler=_profile)
    build = kinds.add_parser("build", help="Build profile of the goal source, a raw net or a verify-edit candidate.")
    build.add_argument("--goal", required=True)
    subject = build.add_mutually_exclusive_group()
    subject.add_argument("--candidate-net", help="A raw net: profiled, never judged.")
    subject.add_argument("--verify-edit", help="A verify-edit explore report (file or directory) to judge.")
    subject.add_argument("--ledger", help="A one-op edit ledger: run verify-edit explore with the goal's facts, seeds "
                                          "and lane artifacts into <out>.verify-edit, then judge it.")
    build.add_argument("--netconvert", help="--ledger only: the netconvert binary (default: netconvert).")
    build.add_argument("--routeability-seed", action="append", type=int,
                       help="--ledger only: override the goal's seeds.")
    build.add_argument("--no-routeability", action="store_true",
                       help="--ledger only: run no routeability seed (L3 then never passes).")
    build.add_argument("--movement-guard", help="--verify-edit only: the L2 movement-guard directory of that "
                                                "candidate (without one L2 is not run and nothing is accepted).")
    build.add_argument("--parent-profile", help="The parent's profile.full.json (an accepted candidate or the goal "
                                                "source); its effective fact table is the step's.")
    build.add_argument("--out", required=True, help="New profile directory; an existing one is refused.")
    build.add_argument("--json", action="store_true")
    build.set_defaults(handler=_profile_build)
    for name, text in (("k1", "K1 known answers: every synthetic X4 mutation against reference facts of its clean "
                               "fixture (offline)."),
                       ("k2", "K2 known answers: ledger and raw faults on the clean Ring1 nominal net; every repair "
                               "judged through verify-edit explore (L3 on frozen trips) and the L2 movement guard.")):
        harness = commands.add_parser(name, help=text)
        harness_commands = harness.add_subparsers(dest="action", required=True)
        run = harness_commands.add_parser("run", help="Run the harness into a new directory.")
        run.add_argument("--out", required=True, help="New output directory; an existing one is refused.")
        if name == "k2":
            run.add_argument("--netconvert", default="netconvert", help="The netconvert binary.")
            run.add_argument("--no-perf", action="store_true", help="Skip the 800 internal-path timing (acceptance f).")
        run.add_argument("--json", action="store_true")
        run.set_defaults(handler=_known_answers, harness=name)
    args = parser.parse_args(argv)
    if args.command == "goal-create" and args.capability == "calibrate" and not (args.packet and args.case):
        parser.error("goal-create --capability calibrate needs --packet and --case")
    try:
        return args.handler(args)
    except Exception as error:  # noqa: BLE001 - CLI boundary must not leak tracebacks.
        _emit({"status": "blocked", "reason": str(error) or type(error).__name__,
               "error_type": type(error).__name__}, args.json)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
