"""Build the offline README demand proposal without running traffic."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_readme_demand import build_hamburg_readme_demand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    result = build_hamburg_readme_demand(request_file=args.request, output_dir=args.output_dir, sumo_binary=binary)
    print(json.dumps({key: result.get(key) for key in
        ("status", "route_count", "ideal_profile_count", "integer_status", "vehicle_count", "vehicle_km", "inputs_unchanged")}))


if __name__ == "__main__":
    main()
