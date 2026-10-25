"""Screen the full observation target with declared nominal signal capacities."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_capacity_screen import screen_hamburg_capacity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    print(json.dumps(screen_hamburg_capacity(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
