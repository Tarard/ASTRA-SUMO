"""Check installed programs and a small cohort at begin 0 and begin 3600."""

import argparse
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.hamburg_control_probe import probe_hamburg_readme_control


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control_dir", "demand_dir", "field_loops", "output_dir"):
        parser.add_argument(name, type=Path)
    binary = Path(sumo.__file__).parent / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
    print(json.dumps(probe_hamburg_readme_control(**vars(parser.parse_args()), sumo_binary=binary)))


if __name__ == "__main__":
    main()
