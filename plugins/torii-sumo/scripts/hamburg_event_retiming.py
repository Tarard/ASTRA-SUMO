"""Run bounded departure calibration against the frozen V1 station observations."""

import argparse
import json

from torii_sumo.core.hamburg_event_retiming import calibrate_departures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--canonical-counts", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--seed", type=int, default=23423)
    parser.add_argument("--max-trials", type=int, default=120)
    parser.add_argument("--max-shift", type=float, default=120)
    result = calibrate_departures(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "best": result["best"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
