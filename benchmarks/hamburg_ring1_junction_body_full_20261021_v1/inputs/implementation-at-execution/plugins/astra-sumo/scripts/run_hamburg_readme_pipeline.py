"""Prepare or run the complete README native count workflow."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_readme_pipeline import localize_hamburg_lane_waypoints, prepare_hamburg_readme_run, run_hamburg_readme_prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "local-lanes", "run"])
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--hold-observed-lanes", action="store_true")
    parser.add_argument("--hold-junction-body", action="store_true")
    args = parser.parse_args()
    if args.hold_observed_lanes and args.action != "run":
        parser.error("Use --hold-observed-lanes with run.")
    if args.hold_junction_body and (args.action != "run" or not args.hold_observed_lanes):
        parser.error("Use --hold-junction-body with run and --hold-observed-lanes.")
    if args.action == "prepare":
        result = prepare_hamburg_readme_run(repository=args.source, output_dir=args.output_dir)
    elif args.action == "local-lanes":
        result = localize_hamburg_lane_waypoints(prepared_dir=args.source, output_dir=args.output_dir)
    else:
        binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
        result = run_hamburg_readme_prepared(prepared_dir=args.source, output_dir=args.output_dir, sumo_binary=binary,
                                           hold_observed_lanes=args.hold_observed_lanes, hold_junction_body=args.hold_junction_body)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
