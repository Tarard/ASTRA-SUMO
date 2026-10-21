"""Reproduce frozen collisions with native trajectories through a declared end."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_collision_trace import prepare_collision_trace, run_collision_trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--end", type=float)
    args = parser.parse_args()
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    prepare_collision_trace(case_dir=args.case_dir, output_dir=args.output_dir, sumo_binary=binary, end_s=args.end)
    print(json.dumps(run_collision_trace(case_dir=args.case_dir, prepared_dir=args.output_dir)))


if __name__ == "__main__":
    main()
