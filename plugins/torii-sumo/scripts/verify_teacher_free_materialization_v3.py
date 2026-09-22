from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_SRC = REPOSITORY_ROOT / "plugins" / "torii-sumo" / "src"
if str(PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SRC))

from torii_sumo.core.teacher_free_materialization_workflow import (  # noqa: E402
    run_teacher_free_materialization_workflow,
)


TOOLCHAIN_LOCK = (
    REPOSITORY_ROOT
    / "benchmarks"
    / "corridor_human_modeling_v1"
    / "toolchain.lock.json"
)
XS1_OSM = (
    REPOSITORY_ROOT
    / "examples"
    / "03_xs1_four_way_tls"
    / "input"
    / "xs1-89129156.osm.xml.gz"
)
XS2_OSM = (
    REPOSITORY_ROOT
    / "examples"
    / "04_xs2_three_way_tls"
    / "input"
    / "xs2-7009179660.osm.xml"
)
HELD_OUT_X4 = (
    REPOSITORY_ROOT
    / "tests"
    / "intersection"
    / "fixtures"
    / "x4_signalized.osm.xml"
)
OUTPUT_ROOT = REPOSITORY_ROOT / "outputs" / "teacher-free-materialization-v3"


def main() -> int:
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/intersection/test_teacher_free_autodiscovery.py",
            "-q",
        ],
        label="focused teacher-free tests",
    )

    xs1 = run_teacher_free_materialization_workflow(
        osm_file=XS1_OSM,
        output_dir=OUTPUT_ROOT / "xs1",
        traffic_side="right",
        toolchain_lock_file=TOOLCHAIN_LOCK,
        timeout_seconds=180.0,
    )
    _require(
        xs1["status"] == "review_ready",
        "XS1 did not reach review_ready",
        xs1,
    )
    _require(
        xs1["gates"]["materialized_candidate_dag_binding"] == "pass",
        "XS1 candidate did not bind its preregistered DAG node",
        xs1,
    )
    _require(
        all(status == "pass" for status in xs1["gates"].values()),
        "XS1 contains a non-passing machine gate",
        xs1,
    )

    xs2_output = OUTPUT_ROOT / "xs2"
    xs2 = run_teacher_free_materialization_workflow(
        osm_file=XS2_OSM,
        output_dir=xs2_output,
        traffic_side="right",
        toolchain_lock_file=TOOLCHAIN_LOCK,
        timeout_seconds=180.0,
    )
    xs2_contract = json.loads(
        (xs2_output / "materialization-contract.json").read_text(
            encoding="utf-8"
        )
    )
    _require(
        xs2["status"] == "blocked" and not xs2["candidate_written"],
        "XS2 did not fail closed before candidate writing",
        xs2,
    )
    _require(
        any(
            "movement_semantic_variants_disagree"
            in item["pre_materialization_blockers"]
            for item in xs2_contract["candidate_assessments"]
        ),
        "XS2 blocker does not preserve the 6/7 movement semantic disagreement",
        xs2_contract,
    )
    _require(
        not (xs2_output / "candidate-join.nod.xml").exists()
        and not (xs2_output / "candidate.net.xml").exists(),
        "XS2 wrote candidate artifacts despite its preflight blocker",
        xs2,
    )

    with tempfile.TemporaryDirectory(prefix="torii-v3-heldout-") as temporary:
        temporary_root = Path(temporary)
        no_signal_osm = temporary_root / "x4-no-signal.osm.xml"
        no_signal_osm.write_text(
            HELD_OUT_X4.read_text(encoding="utf-8").replace(
                '<tag k="highway" v="traffic_signals"/>',
                "",
            ),
            encoding="utf-8",
        )
        held_out = run_teacher_free_materialization_workflow(
            osm_file=no_signal_osm,
            output_dir=temporary_root / "artifacts",
            traffic_side="right",
            toolchain_lock_file=TOOLCHAIN_LOCK,
            binaries={},
        )
        _require(
            held_out["status"] == "not_applicable"
            and not held_out["candidate_written"],
            "Held-out no-signal scene did not exit as not_applicable",
            held_out,
        )

    _run(
        [sys.executable, "-m", "pytest", "-q"],
        label="full pytest suite",
    )
    _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "plugins/torii-sumo/src",
            "plugins/torii-sumo/scripts",
            "tests",
        ],
        label="Ruff",
    )

    result = {
        "schema": "torii.teacher-free-materialization-verification/v1",
        "status": "pass",
        "xs1": {
            "status": xs1["status"],
            "contract_id": xs1["contract_id"],
            "materialized_candidate_id": xs1["materialized_candidate_id"],
            "candidate_binding_id": xs1["candidate_binding_id"],
            "gates": xs1["gates"],
            "manifest_file": xs1["manifest_file"],
        },
        "xs2": {
            "status": xs2["status"],
            "candidate_written": xs2["candidate_written"],
            "contract_id": xs2["contract_id"],
        },
        "held_out": {
            "status": "not_applicable",
            "candidate_written": False,
        },
        "automatic_topology_selection": "blocked",
        "field_timing_reconstruction": "blocked",
        "automatic_promotion_gate": "blocked",
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    result_file = OUTPUT_ROOT / "verification.json"
    result_file.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({**result, "verification_file": str(result_file)}, indent=2))
    return 0


def _run(command: list[str], *, label: str) -> None:
    print(f"\n== {label} ==", flush=True)
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _require(
    condition: bool,
    message: str,
    evidence: object,
) -> None:
    if condition:
        return
    print(message, file=sys.stderr)
    print(json.dumps(evidence, indent=2, default=str), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
