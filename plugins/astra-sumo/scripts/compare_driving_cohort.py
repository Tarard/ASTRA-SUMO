"""Run a paired driving request with repository-owned comparison code."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.paired_driving import compare_driving_cohort


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--sumo-binary", default="sumo")
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
    base = args.request.resolve().parent
    request["routes"] = base / request["routes"]
    request["networks"] = {name: base / path for name, path in request["networks"].items()}
    report = compare_driving_cohort(**request, output_dir=args.output_dir, sumo_binary=args.sumo_binary)
    print(json.dumps({"pair_validity_counts": report["pair_validity_counts"],
                      "comparison": str(args.output_dir / "comparison.json")}, indent=2))


if __name__ == "__main__":
    main()
