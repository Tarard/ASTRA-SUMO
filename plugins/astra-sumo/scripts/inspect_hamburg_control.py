"""Read back a candidate and its intergreens without running traffic."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_control_probe import inspect_hamburg_control


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control_dir", "reference_control_dir", "output_dir"):
        parser.add_argument(name, type=Path)
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    print(json.dumps(inspect_hamburg_control(**vars(parser.parse_args()), sumo_binary=binary)))


if __name__ == "__main__":
    main()
