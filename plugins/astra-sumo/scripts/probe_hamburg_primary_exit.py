"""Prepare or check the declared major-road destination prior for Z12/Z13."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_exit_prior import prepare_major_exit_prior, probe_major_exit_prior
from astra_sumo.core.hamburg_initialization import run_hamburg_initialization


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "probe", "run"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if args.action == "prepare":
        result = prepare_major_exit_prior(repository=root, case_dir=args.source, output_dir=args.output_dir)
    else:
        binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
        if args.action == "probe":
            result = probe_major_exit_prior(repository=root, prepared_dir=args.source, output_dir=args.output_dir, sumo_binary=binary)
        else:
            result = run_hamburg_initialization(prepared_dir=args.source, output_dir=args.output_dir, sumo_binary=binary)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
