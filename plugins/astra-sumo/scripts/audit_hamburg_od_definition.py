"""Audit the frozen Ring1 OD support without running SUMO."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_od_definition import audit_hamburg_od_definition


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    print(json.dumps(audit_hamburg_od_definition(**vars(parser.parse_args())), indent=2))
