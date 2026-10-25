"""Measure the approved OD initial vector, or run SPSA with explicit settings."""

import argparse
import hashlib
from importlib.metadata import version
from importlib.util import find_spec
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import sumo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--initial-evaluation-dir", type=Path)
    parser.add_argument("--stop-on-unsafe", action="store_true")
    parser.add_argument("--backend", choices=("traci", "libsumo"), default="traci")
    parser.add_argument("--parallel-pair", action="store_true", help="Evaluate each SPSA loss pair in independent native processes.")
    options = parser.parse_args()
    if options.parallel_pair and not options.settings:
        parser.error("--parallel-pair requires --settings.")
    repository = Path(__file__).resolve().parents[3]
    assert Path(sys.executable).resolve() == (repository / ".venv/Scripts/python.exe").resolve()
    versions = {name: version(name) for name in ("eclipse-sumo", "sumolib", "traci")}
    if options.backend == "libsumo":
        versions["libsumo"] = version("libsumo")
    assert set(versions.values()) == {"1.27.1"}
    binary = Path(sumo.__file__).parent / "bin/sumo.exe"
    record = options.output_dir.resolve().with_name(options.output_dir.name + "-execution")
    record.mkdir(parents=True, exist_ok=False)
    snapshot = record / "implementation"
    source = repository / "plugins/astra-sumo/src/astra_sumo"
    shutil.copytree(source, snapshot / "astra_sumo", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(__file__, record / "run.py")
    sys.path.insert(0, str(snapshot))
    os.environ["PYTHONPATH"] = str(snapshot)
    metadata = {"python": sys.executable, "versions": versions, "numpy_version": version("numpy"),
                "backend": options.backend, "parallel_pair": options.parallel_pair,
                "runtime": {"python_version": sys.version, "PYTHONPATH": os.environ["PYTHONPATH"],
                            "SUMO_HOME": os.environ.get("SUMO_HOME")},
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
                "sumo_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "implementation_sha256": {str(p.relative_to(snapshot)): hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in snapshot.rglob("*.py")}}
    if options.backend == "libsumo":
        library = Path(find_spec("libsumo").origin).parent / "_libsumo.pyd"
        metadata.update(libsumo_library_path=str(library), libsumo_sha256=hashlib.sha256(library.read_bytes()).hexdigest())
    (record / "execution.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    from astra_sumo.core.hamburg_od_spsa import measure_hamburg_od, run_hamburg_od_spsa
    if options.settings:
        settings = json.loads(options.settings.read_bytes())
        result = run_hamburg_od_spsa(prepared_dir=options.prepared_dir, output_dir=options.output_dir,
                                     sumo_binary=binary, settings=settings,
                                     initial_evaluation_dir=options.initial_evaluation_dir,
                                     stop_on_unsafe=options.stop_on_unsafe,
                                     backend=options.backend, parallel_pair=options.parallel_pair)
    else:
        initial = json.loads((options.prepared_dir / "initial-od-vector.json").read_bytes())["values"]
        result = measure_hamburg_od(prepared_dir=options.prepared_dir, values=initial,
                                    output_dir=options.output_dir, sumo_binary=binary, backend=options.backend)
    print(json.dumps(result, indent=2), flush=True)
