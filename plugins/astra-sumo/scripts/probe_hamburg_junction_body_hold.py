"""Measure the declared junction-body lane hold on unchanged Hamburg inputs."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_junction_body_hold import probe_hamburg_junction_body_hold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    print(json.dumps(probe_hamburg_junction_body_hold(case_dir=args.case_dir, output_dir=args.output_dir, sumo_binary=binary)))


if __name__ == "__main__":
    main()
