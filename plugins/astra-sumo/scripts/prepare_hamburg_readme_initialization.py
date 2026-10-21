"""Prepare the Hamburg initialization demand or measure its preceding hour."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_initialization import prepare_hamburg_initialization, run_hamburg_initialization


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "run"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--counts-file", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.counts_file is None:
            parser.error("Provide --counts-file for preparation.")
        result = prepare_hamburg_initialization(case_dir=args.source, counts_file=args.counts_file, output_dir=args.output_dir)
    else:
        binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
        result = run_hamburg_initialization(prepared_dir=args.source, output_dir=args.output_dir, sumo_binary=binary)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
