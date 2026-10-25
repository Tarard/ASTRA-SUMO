"""Inventory motor dark links before the first full-window native run."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_control_inventory import inventory_hamburg_motor_dark_links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control_dir", "demand_dir", "output_dir"):
        parser.add_argument(name, type=Path)
    print(json.dumps(inventory_hamburg_motor_dark_links(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
