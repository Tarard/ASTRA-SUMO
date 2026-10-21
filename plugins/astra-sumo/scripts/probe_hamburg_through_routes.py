"""Measure existing 119–200–535 through routes against all native count fields."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_through_routes import prepare_hamburg_through_prior, probe_hamburg_through_routes
from astra_sumo.core.hamburg_initialization import run_hamburg_initialization


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["probe", "prepare", "run"])
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--probe-dir", type=Path)
    args = parser.parse_args()
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    root = Path(__file__).resolve().parents[3]
    if args.action == "prepare":
        if args.probe_dir is None:
            parser.error("Provide --probe-dir for this checked through-route candidate.")
        result = prepare_hamburg_through_prior(repository=root, case_dir=args.case_dir, probe_dir=args.probe_dir, output_dir=args.output_dir)
    elif args.action == "run":
        result = run_hamburg_initialization(prepared_dir=args.case_dir, output_dir=args.output_dir, sumo_binary=binary)
    else:
        result = probe_hamburg_through_routes(repository=root, case_dir=args.case_dir, output_dir=args.output_dir, sumo_binary=binary)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
