"""Acquire official sources, rebuild OSM, and measure the 2349/2394 baseline."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from astra_sumo.core.artifact_io import copy_file_atomic, write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.hamburg_network_calibration import evaluate_hamburg_calibration_snapshot
from astra_sumo.core.hamburg_official import (
    SensorThingsClient, discover_hamburg_signal_assets, download_hamburg_traffic_light_catalog,
    download_resolved_hamburg_signal_assets,
)
from astra_sumo.core.osm_network import build_osm_network, parse_bbox
from astra_sumo.core.road_scope import resolve_highway_classes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--bbox", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--sample-spacing-m", required=True, type=float)
    parser.add_argument("--output-precision", type=int, default=2,
                        help="Native construction precision. The original baseline recipe uses 2.")
    parser.add_argument("--source-osm", type=Path, help="Reuse exact frozen OSM bytes instead of a live download.")
    parser.add_argument("--netconvert-binary", default=str(Path(sys.executable).parent / "netconvert.exe"))
    args = parser.parse_args()
    if args.output_precision < 0:
        parser.error("--output-precision must be a non-negative integer.")
    parse_bbox(args.bbox)
    date.fromisoformat(args.target_date)
    if not math.isfinite(args.sample_spacing_m) or args.sample_spacing_m <= 0:
        raise ValueError("sample-spacing-m must be finite and positive.")
    destination = args.output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    sources = destination / "sources"
    catalog, audit = download_hamburg_traffic_light_catalog(sources / "catalog", timeout_seconds=30)
    assets, discovery = discover_hamburg_signal_assets(catalog, ["2349", "2394"],
                                                     output_dir=sources / "catalog", timeout_seconds=30)
    if {asset.node_id for asset in assets} != {"2349", "2394"}:
        raise ValueError("Both 2349 and 2394 require complete official asset sets.")
    paths, downloads = download_resolved_hamburg_signal_assets(
        SensorThingsClient(catalog.primary_signal_api_base, timeout_seconds=30), assets, sources / "official",
        map_asset_base=catalog.map_asset_base, ocit_c_asset_base=catalog.ocit_c_asset_base)
    official = sources / "official-sources.json"
    write_json_atomic(official, {"catalog": audit, "discovery": discovery, "paths": paths, "downloads": downloads})
    osm = sources / "source.osm.xml"
    if args.source_osm is None:
        url = "https://api.openstreetmap.org/api/0.6/map?" + urlencode({"bbox": args.bbox})
        request = Request(url, headers={"User-Agent": "ASTRA-SUMO road calibration"})
        with urlopen(request, timeout=60) as response:
            content, headers = response.read(), dict(response.headers)
        if ET.fromstring(content).tag != "osm":
            raise ValueError("The OSM endpoint did not return an OSM document.")
        osm.write_bytes(content)
        identity = {"url": url, "headers": headers, "retrieved_at_utc": datetime.now(timezone.utc).isoformat()}
    else:
        copy_file_atomic(args.source_osm, osm)
        identity = {"reused_snapshot": str(args.source_osm.resolve())}
    write_json_atomic(sources / "osm-source.json", {**identity, "path": str(osm), "sha256": file_sha256(osm),
        "license": "ODbL-1.0", "attribution": "OpenStreetMap contributors"})
    request = {"bbox": args.bbox, "target_date": args.target_date,
               "sample_spacing_m": args.sample_spacing_m, "netconvert_profile": "reference_visual_detail",
               "output_precision": args.output_precision,
               "highway_classes": "full_vehicle", "source_osm": str(osm), "netconvert_binary": args.netconvert_binary}
    write_json_atomic(destination / "request.json", request)
    result = build_osm_network(bbox=args.bbox, source_osm_path=osm, output_dir=destination / "raw",
        prefix="sandtorkai", allowed_highways=resolve_highway_classes("full_vehicle"),
        netconvert_profile="reference_visual_detail", output_precision=args.output_precision,
        netconvert_binary=args.netconvert_binary)
    write_json_atomic(destination / "raw-build-result.json", result)
    evaluation = None
    network = Path(result["net_file"]) if result.get("net_file") else None
    if network is not None and network.is_file():
        evaluation = evaluate_hamburg_calibration_snapshot(network=network, official_sources=official,
            output_dir=destination / "calibration", target_date=args.target_date,
            sample_spacing_m=args.sample_spacing_m)
    summary = {"schema": "astra.hamburg-calibration-baseline/v1",
               "status": "review_required" if result["status"] in {"pass", "review_required"} and evaluation is not None else "blocked",
               "construction_status": result["status"], "evaluation": evaluation,
               "request_file": str(destination / "request.json"), "source_manifest": str(official),
               "claim_boundary": "Fresh diagnostic reconstruction. Source publication dates and lane correspondence still require review."}
    write_json_atomic(destination / "run.json", summary, sort_keys=True)
    print(json.dumps(summary, indent=2))
    return 1 if summary["status"] == "review_required" else 3


if __name__ == "__main__":
    raise SystemExit(main())
