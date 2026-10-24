"""Account for native Hamburg departure bins, entry queues and detector passage bins."""

import argparse
import json
from pathlib import Path

from astra_sumo.core.hamburg_readme_results import write_hamburg_temporal_feedback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    print(json.dumps(write_hamburg_temporal_feedback(**vars(parser.parse_args()))))


if __name__ == "__main__":
    main()
