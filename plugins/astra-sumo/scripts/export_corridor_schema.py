from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.corridor.schema import build_corridor_schema


DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "schemas"
    / "research"
    / "corridor"
    / "torii.corridor.research-bundle.v1.schema.json"
)


def build_schema() -> dict[str, object]:
    return build_corridor_schema()


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the frozen ASTRA corridor v1 JSON Schema.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_json_atomic(args.output, build_schema(), sort_keys=True)


if __name__ == "__main__":
    main()
