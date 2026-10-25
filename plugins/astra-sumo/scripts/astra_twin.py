"""ASTRA twin profiler CLI (M1): doctor, resolve, goal-create and profile calibrate. Never starts SUMO runs.

Exit codes follow ``astra`` (``cli.py``): 0 for ``pass`` or ``measured``; 3 for ``blocked``, which covers a failed
doctor or resolve check, a blocked profile and a refused or failed command (its ``reason`` and ``error_type`` say
which); 2 for an argparse usage error. The CLI writes only to the paths it is given (the store, a new profile
directory, doctor's scratch inside the clone) and writes no bytecode, so a verifier clone stays as checked out.
"""

from __future__ import annotations

import argparse
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
    from astra_sumo.twin import count_profile, doctor, goal, ids
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
    return 0 if status in {"pass", "measured"} else 3


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
    created = goal.create_calibrate_goal(REPOSITORY_ROOT, args.packet, args.case, goal_id=args.goal_id)
    path, written = goal.write_goal(created, Path(args.store))
    data = path.read_bytes()
    _emit({"status": "pass", "write": written, "goal_id": created.goal_id, "goal": str(path),
           "goal_ref": ids.goal_reference(created.goal_id, data), "baseline": created.baseline.model_dump(),
           "hold_nodes": created.run.junction_body_hold_nodes, "timeout_s": created.run.timeout_s,
           "all_stopped_timeout_s": created.run.all_stopped_timeout_s}, args.json)
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASTRA twin profiler (explore only; no SUMO runs in M1).")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check SUMO, libsumo, the import path and the clone state.")
    check.add_argument("--expect-commit", help="Commit that HEAD must equal; default: the clone directory's name.")
    check.add_argument("--forbid-path", action="append", help="Source path that must not be on any sys.path.")
    check.add_argument("--scratch-root", help="Where the child's empty working directory is created; default: the "
                                              "clone's git-ignored outputs/. It is removed afterwards.")
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=_doctor)
    resolve = commands.add_parser("resolve", help="Resolve a packet case through its byte references.")
    create = commands.add_parser("goal-create", help="Write <store>/goals/<goal_id>/goal.json for a packet case.")
    for sub in (resolve, create):
        sub.add_argument("--packet", required=True, help="Repository-relative benchmark packet directory.")
        sub.add_argument("--case", required=True)
        sub.add_argument("--json", action="store_true")
    resolve.set_defaults(handler=_resolve)
    create.add_argument("--capability", required=True, choices=["calibrate"])
    create.add_argument("--store", required=True)
    create.add_argument("--goal-id", help="Override the derived goal id.")
    create.set_defaults(handler=_goal_create)
    profile = commands.add_parser("profile", help="Profile a run against a goal.")
    kinds = profile.add_subparsers(dest="kind", required=True)
    calibrate = kinds.add_parser("calibrate", help="Offline Calibrate count profile of the goal's baseline run.")
    calibrate.add_argument("--goal", required=True)
    calibrate.add_argument("--run", required=True, help="packet:<case-relative run directory>")
    calibrate.add_argument("--out", required=True, help="New profile directory; an existing one is refused.")
    calibrate.add_argument("--json", action="store_true")
    calibrate.set_defaults(handler=_profile)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Exception as error:  # noqa: BLE001 - CLI boundary must not leak tracebacks.
        _emit({"status": "blocked", "reason": str(error) or type(error).__name__,
               "error_type": type(error).__name__}, args.json)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
