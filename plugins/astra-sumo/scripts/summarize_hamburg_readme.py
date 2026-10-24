"""Write all field/station comparisons and the complete illustrated README report."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_readme_results import summarize_hamburg_readme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "prepared_dir", "native_dir", "figure_dir", "output_dir"):
        parser.add_argument(name, type=Path)
    print(json.dumps(summarize_hamburg_readme(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
