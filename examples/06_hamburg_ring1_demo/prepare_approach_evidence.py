"""Regenerate the Ring1 approach evidence from frozen OSM and official inputs offline."""
import gzip
import hashlib
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.hamburg_network_calibration import evaluate_hamburg_calibration_snapshot
from astra_sumo.core.hamburg_scope_anchors import write_hamburg_approach_retention
from astra_sumo.core.osm_network import build_osm_network


def run(output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    fixture_dir = repo / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    fixture = json.loads((fixture_dir / "fixture.json").read_text(encoding="utf-8"))
    binaries = Path(sumo.__file__).parent / "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    source = build_osm_network(bbox=fixture["bbox"], source_osm_path=fixture_dir / "source.osm.xml.gz",
        historical_date=fixture["osm_snapshot_date"], output_dir=output / "source", prefix="ring1-full",
        allowed_highways=set(fixture["road_classes"]), tls_join_distance_m=fixture["tls_join_distance_m"],
        netconvert_profile=fixture["native_profile"], output_precision=fixture["precision"],
        netconvert_binary=str(binaries / f"netconvert{suffix}"))
    write_json_atomic(output / "source-build.json", source)
    if not source.get("net_file") or not Path(source["net_file"]).is_file():
        raise ValueError("The frozen OSM import did not produce a source network.")
    official_dir = repo / "benchmarks/hamburg_ring1_official_sources_20261020_v1"
    official = json.loads((official_dir / "manifest.json").read_text(encoding="utf-8"))
    paths, downloads, warnings = {}, [], []
    for row in official["files"]:
        compressed = (official_dir / row["path"]).read_bytes()
        raw = gzip.decompress(compressed)
        if hashlib.sha256(compressed).hexdigest() != row["compressed_sha256"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            warnings.append(f"Official input differs from the frozen hash: {row['path']}")
        path = output / "official-sources" / row["source_filename"]
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        paths.setdefault(row["node_id"], {})[row["kind"]] = str(path)
        downloads.append({"node_id": row["node_id"], "kind": row["kind"], "path": str(path), "sha256": row["sha256"]})
    manifest = output / "official-sources.json"
    write_json_atomic(manifest, {"paths": paths, "downloads": downloads})
    write_json_atomic(output / "input-warnings.json", {"warnings": warnings})
    print("Measuring frozen official source correspondences.", flush=True)
    evaluate_hamburg_calibration_snapshot(network=Path(source["net_file"]), official_sources=manifest,
        output_dir=output / "calibration", target_date=fixture["osm_snapshot_date"][:10], sample_spacing_m=.5, mode="explore")
    report = write_hamburg_approach_retention(calibration_run_file=output / "calibration/run.json",
        output_file=output / "official-approach-anchors.json")
    print(json.dumps({"protected_edges": len(report["protected_edge_ids"]), "warnings": report["warnings"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
