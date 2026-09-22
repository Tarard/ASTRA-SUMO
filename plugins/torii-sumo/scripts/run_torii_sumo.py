# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "anyio>=4.0",
#   "mcp[cli]>=1.0.0,<2",
#   "numpy>=1.26",
#   "osmium>=4.3,<5",
#   "Pillow>=10.0",
#   "pyproj>=3.6",
#   "pydantic>=2.7",
#   "pywin32>=306; platform_system == 'Windows'",
#   "scipy>=1.11",
#   "shapely>=2.1,<3",
#   "sumolib>=1.27.1",
#   "traci>=1.20",
#   "tzdata>=2024.1; platform_system == 'Windows'",
# ]
# ///

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PLUGIN_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

def main() -> int | None:
    if sys.argv[1:2] == ["--cli"]:
        from torii_sumo.cli import main as cli_main
        return cli_main(sys.argv[2:])
    from torii_sumo.server import main as server_main
    return server_main()


if __name__ == "__main__":
    raise SystemExit(main())
