from __future__ import annotations

import argparse
import json
from pathlib import Path

from torii_sumo.core.hamburg_official_corridor_geometry import (
    HamburgOfficialCorridorGeometryError,
    materialize_hamburg_official_corridor_geometry,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stitch official Hamburg HH-SIB axes to MAP 2349/2394 cells.")
    parser.add_argument("--hh-sib-nodes", required=True, type=Path)
    parser.add_argument("--hh-sib-edges", required=True, type=Path)
    parser.add_argument("--hh-sib-types", required=True, type=Path)
    parser.add_argument("--intersection-2349", required=True, type=Path)
    parser.add_argument("--intersection-2394", required=True, type=Path)
    parser.add_argument(
        "--lsa-identity-manifest",
        type=Path,
        help=(
            "optional frozen official Hamburg LSA identity manifest; when supplied, "
            "2403 is compared with the HH-SIB boundary without snapping the road node"
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--netconvert-binary", default="netconvert")
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    args = parser.parse_args()
    try:
        report = materialize_hamburg_official_corridor_geometry(
            hh_sib_nodes_file=args.hh_sib_nodes,
            hh_sib_edges_file=args.hh_sib_edges,
            hh_sib_types_file=args.hh_sib_types,
            intersection_sources={"2349": args.intersection_2349, "2394": args.intersection_2394},
            output_dir=args.output_dir,
            lsa_identity_manifest=args.lsa_identity_manifest,
            netconvert_binary=args.netconvert_binary,
            sumo_binary=args.sumo_binary,
            timeout_seconds=args.timeout_seconds,
        )
    except (OSError, ValueError, HamburgOfficialCorridorGeometryError) as exc:
        print(json.dumps({"status": "error", "automatic_promotion_gate": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["status"] == "review_ready" else 3


if __name__ == "__main__":
    raise SystemExit(main())
