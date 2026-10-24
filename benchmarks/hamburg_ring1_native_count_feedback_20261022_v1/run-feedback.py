"""Run the fixed two-attempt native count correction with repository runtimes."""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess
import sys

import sumo

from astra_sumo.core.hamburg_readme_pipeline import calibrate_hamburg_native_counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve() == (root / ".venv/Scripts/python.exe").resolve()
    versions = {name: version(name) for name in ("eclipse-sumo", "traci", "sumolib")}
    assert set(versions.values()) == {"1.27.1"}
    binary = Path(sumo.__file__).parent / "bin/sumo.exe"
    record = options.output.resolve().with_name(options.output.name + "-execution")
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
                "implementation_sha256": hashes, "maximum_native_trials": 2, "saved_state_restart": False}
    (record / "execution.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    result = calibrate_hamburg_native_counts(repository=root,
        reference_case=root / "benchmarks/hamburg_ring1_primary_entry_full_20261022_v1",
        output_dir=options.output, sumo_binary=binary)
    print(json.dumps(result, ensure_ascii=False), flush=True)
