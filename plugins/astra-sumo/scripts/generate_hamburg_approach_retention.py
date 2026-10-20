"""Generate road-retention evidence from a measured Hamburg calibration snapshot."""
import argparse
import json

from astra_sumo.core.hamburg_scope_anchors import write_hamburg_approach_retention


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration_run")
    parser.add_argument("new_output_file")
    args = parser.parse_args()
    result = write_hamburg_approach_retention(calibration_run_file=args.calibration_run, output_file=args.new_output_file)
    print(json.dumps({key: result[key] for key in ["status", "motor_lane_counts", "warnings"]}, indent=2))
