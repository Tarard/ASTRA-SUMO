"""Build the declared README signal programs without changing the network."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_readme_control import build_hamburg_readme_control


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_file", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    print(json.dumps(build_hamburg_readme_control(**vars(args), sumo_binary=binary)))


if __name__ == "__main__":
    main()
