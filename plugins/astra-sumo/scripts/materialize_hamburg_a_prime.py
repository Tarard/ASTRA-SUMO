"""Materialize the declared A-prime control candidate and expose unresolved choices."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_control_fallback import materialize_hamburg_a_prime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control_dir", "inventory_dir", "output_dir"):
        parser.add_argument(name, type=Path)
    print(json.dumps(materialize_hamburg_a_prime(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
