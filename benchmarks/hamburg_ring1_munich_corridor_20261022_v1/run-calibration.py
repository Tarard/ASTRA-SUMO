"""Run the reviewed corridor with repository SUMO 1.27.1 and saved source identity."""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import sys

import sumo

from astra_sumo.core.hamburg_corridor_calibration import run_corridor_calibration


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-dir", required=True, type=Path)
    parser.add_argument("--figure-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve() == (root / ".venv/Scripts/python.exe").resolve()
    versions = {name: version(name) for name in ("eclipse-sumo", "traci", "sumolib")}
    assert set(versions.values()) == {"1.27.1"}
    binary = Path(sumo.__file__).parent / "bin/sumo.exe"
    record = options.output_dir.resolve().with_name(options.output_dir.name + "-execution")
    record.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for path in (root / "plugins/astra-sumo/src/astra_sumo").rglob("*.py"):
        relative = path.relative_to(root)
        target = record / "implementation" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata = {"python": sys.executable, "versions": versions,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "sumo_binary": str(binary), "sumo_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "implementation_sha256": hashes, "maximum_native_runs": 3, "saved_state_restart": False}
    (record / "execution.json").write_bytes((json.dumps(metadata, indent=2) + "\n").encode("utf-8"))
    result = run_corridor_calibration(repository=root, sumo_binary=binary, **vars(options))
    print(json.dumps(result, ensure_ascii=False), flush=True)
