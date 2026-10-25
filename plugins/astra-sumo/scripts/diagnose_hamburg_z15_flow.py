"""Read existing native Z15 trips, unfinished stock, and the source K8 control."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_route_delay import diagnose_hamburg_z15


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    control = root / "benchmarks/hamburg_ring1_m2_control_20261021_v1"
    result = diagnose_hamburg_z15(case_dir=args.case_dir, output_dir=args.output_dir,
        bindings_file=control / "link-bindings.json.gz", ocit_file=control / "inputs/ocit_200.xml")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
