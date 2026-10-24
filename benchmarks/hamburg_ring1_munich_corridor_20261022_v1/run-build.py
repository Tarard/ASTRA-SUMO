"""Rebuild the reviewed corridor and demand without starting a traffic simulation."""

import argparse
import gzip
import hashlib
from importlib.metadata import version
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_corridor_pruning import build_ring1_corridor, prepare_corridor_demand


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    assert Path(sys.executable).resolve() == (root / ".venv/Scripts/python.exe").resolve()
    assert all(version(name) == "1.27.1" for name in ("eclipse-sumo", "traci", "sumolib"))
    assert not options.output_dir.exists(), "Keep each preparation in a new directory."
    binaries = Path(sumo.__file__).parent / "bin"
    result = build_ring1_corridor(repository=root, output_dir=options.output_dir,
        netconvert_binary=str(binaries / "netconvert.exe"), sumo_binary=str(binaries / "sumo.exe"))
    assert result["status"] == "measured", result
    prepared = options.output_dir / "calibration-inputs"
    prepare_corridor_demand(repository=root, corridor_dir=options.output_dir, output_dir=prepared)
    for name in ("network.net.xml", "control.add.xml", "demand.rou.xml", "lane-intentions.json",
                 "expected-counts.csv", "field-loops.add.xml", "initialization-counts.csv"):
        expected = gzip.decompress((Path(__file__).parent / "inputs" / (name + ".gz")).read_bytes())
        assert hashlib.sha256((prepared / name).read_bytes()).digest() == hashlib.sha256(expected).digest(), name
    print(f"All seven prepared inputs reproduce exactly: {prepared}")
