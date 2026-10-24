"""Run the existing bounded V1 replay against the frozen historical inputs."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    out = parser.parse_args().output_dir.resolve()
    case = Path(__file__).resolve().parent
    repo = case.parents[1]
    python = repo / ".venv/Scripts/python.exe"
    binary = repo / ".venv/Lib/site-packages/sumo/bin/sumo.exe"
    if Path(sys.executable).resolve() != python.resolve():
        raise SystemExit("Use the repository .venv/Scripts/python.exe.")
    evidence = json.loads((case / "environment.json").read_bytes())
    if hashlib.sha256(binary.read_bytes()).hexdigest() != evidence["sumo"]["sha256"]:
        raise SystemExit("The project SUMO binary differs from the frozen measurement.")
    for row in evidence["replay_modules"]:
        if hashlib.sha256((repo / row["path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise SystemExit(f"Replay source changed: {row['path']}")
    out.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(repo / "plugins/astra-sumo/src")}
    for name, inputs, shift in (("ten-trial", "inputs", "120"), ("branch", "branch-source", "117")):
        source = out / (name + "-source")
        source.mkdir()
        for path in (case / inputs).iterdir():
            raw = path.read_bytes()
            (source / path.name.removesuffix(".gz")).write_bytes(
                gzip.decompress(raw) if path.suffix == ".gz" else raw
            )
        if name == "branch" and (source / "demand.rou.xml").read_bytes() != (
            out / "ten-trial/trial-002/demand.rou.xml"
        ).read_bytes():
            raise SystemExit("The measured rejected parent differs from the frozen branch source.")
        subprocess.run([
            str(python), str(repo / "plugins/astra-sumo/scripts/hamburg_event_retiming.py"),
            "--source-run", str(source), "--canonical-counts", str(source / "counts.simulation.15min.csv"),
            "--output-dir", str(out / name), "--sumo-binary", str(binary),
            "--seed", "23423", "--max-trials", "10", "--max-shift", shift,
        ], env=env, check=True)
        actual = json.loads((out / name / "result.json").read_bytes())
        expected = json.loads((case / "searches" / name / "result.json").read_bytes())
        if any(actual["best"][key] != expected["best"][key]
               for key in ("comparisons", "completion", "healthy")):
            raise SystemExit(f"The {name} best result differs from the frozen measurement.")


if __name__ == "__main__":
    main()
