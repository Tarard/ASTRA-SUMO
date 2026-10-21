"""Attribute indirect routes to the saved MAP and OSM-derived sources."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.demand_route_attribution import attribute_indirect_routes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("demand_package", "candidate_manifest", "original_network", "output_dir"):
        parser.add_argument(name, type=Path)
    result = attribute_indirect_routes(**vars(parser.parse_args()))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
