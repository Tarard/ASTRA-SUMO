"""Prepare the Hamburg initialization demand or measure its preceding hour."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_initialization import prepare_hamburg_entry_alternative, prepare_hamburg_initialization, run_hamburg_initialization
from astra_sumo.core.hamburg_z15_routes import prepare_z15_k2_routes, probe_z15_k2_routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "entry-alternative", "z15-k2", "probe-z15-k2", "run"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--counts-file", type=Path)
    parser.add_argument("--profiles-file", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.counts_file is None:
            parser.error("Provide --counts-file for preparation.")
        result = prepare_hamburg_initialization(case_dir=args.source, counts_file=args.counts_file, output_dir=args.output_dir)
    elif args.action == "entry-alternative":
        if args.profiles_file is None:
            parser.error("Provide --profiles-file for the entry alternative.")
        result = prepare_hamburg_entry_alternative(case_dir=args.source, profiles_file=args.profiles_file, output_dir=args.output_dir)
    elif args.action == "z15-k2":
        if args.profiles_file is None:
            parser.error("Provide --profiles-file for the Z15 route candidate.")
        root = Path(__file__).resolve().parents[3]
        result = prepare_z15_k2_routes(case_dir=args.source, profiles_file=args.profiles_file, output_dir=args.output_dir,
            bindings_file=root / "benchmarks/hamburg_ring1_m2_control_20261021_v1/link-bindings.json.gz")
    else:
        binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
        run = probe_z15_k2_routes if args.action == "probe-z15-k2" else run_hamburg_initialization
        result = run(prepared_dir=args.source, output_dir=args.output_dir, sumo_binary=binary)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
