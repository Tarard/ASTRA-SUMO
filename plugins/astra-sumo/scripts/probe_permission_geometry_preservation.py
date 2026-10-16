"""Run a same-input comparison of three permission-edit methods."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from astra_sumo.core.network_rebuild_probe import run_permission_geometry_probe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-net", type=Path, required=True)
    parser.add_argument("--source-osm", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--netconvert-binary", default="netconvert")
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--output-precision", type=int)
    args = parser.parse_args()
    report = run_permission_geometry_probe(**vars(args))
    print(json.dumps({"status": report["status"], "changed_lane_count": report["changed_lane_count"],
                      "new_mode_connectivity_review_required": report["new_mode_connectivity_review_required"],
                      "cases": {name: {"sumo_load": case.get("sumo_load", {}).get("status"),
                          "changes": case.get("geometry", {}).get("ordinary_lane_attribute_changes"),
                          "other_network_state_preserved": case.get("all_non_permission_network_state_preserved")}
                          for name, case in report["cases"].items()}}, indent=2))
    return 0 if report["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
