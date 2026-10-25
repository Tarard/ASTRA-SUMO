"""Prepare or replay the single-entry 900-second departure perturbation."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_departure_probe import prepare_hamburg_departure_probe, run_hamburg_departure_probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_hamburg_departure_probe(case_dir=args.source, output_dir=args.output_dir)
    else:
        binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
        result = run_hamburg_departure_probe(prepared_dir=args.source, output_dir=args.output_dir, sumo_binary=binary)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
