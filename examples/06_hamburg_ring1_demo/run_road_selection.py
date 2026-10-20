"""Rebuild the frozen Ring1 OSM import and select its main-road study scope offline."""
import json
from pathlib import Path
import sys

import sumo

from astra_sumo.core.artifact_io import write_json_atomic
from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.osm_network import build_osm_network
from astra_sumo.core.road_hierarchy import build_road_hierarchy_variant


def run(output_dir):
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    fixture_dir = root / "benchmarks/hamburg_ring1_road_scope_20261020_v1"
    config_bytes = (fixture_dir / "fixture.json").read_bytes()
    fixture = json.loads(config_bytes)
    effective_config = output / "case-input.json"
    effective_config.write_bytes(config_bytes)
    source_osm = fixture_dir / "source.osm.xml.gz"
    if file_sha256(source_osm) != fixture["files"][source_osm.name]["sha256"]:
        raise ValueError("The frozen OSM snapshot hash does not match.")
    binary_dir = Path(sumo.__file__).resolve().parent / "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    netconvert = str(binary_dir / f"netconvert{suffix}")
    build = build_osm_network(bbox=fixture["bbox"], source_osm_path=source_osm,
        historical_date=fixture["osm_snapshot_date"], output_dir=output / "01-osm", prefix="ring1-full",
        allowed_highways=set(fixture["road_classes"]), tls_join_distance_m=fixture["tls_join_distance_m"],
        netconvert_profile=fixture["native_profile"], output_precision=fixture["precision"], netconvert_binary=netconvert)
    write_json_atomic(output / "01-osm-result.json", build)
    case = {"schema": "astra.ring1-demo-partial/v1", "status": "blocked",
        "fixture": {"path": str(effective_config), "sha256": file_sha256(effective_config)},
        "source_osm_sha256": file_sha256(source_osm),
        "implementation": {name: file_sha256(root / "plugins/astra-sumo/src/astra_sumo/core" / name)
                           for name in ["osm_network.py", "road_hierarchy.py", "road_scope.py"]},
        "remaining_stages": ["junction_consolidation", "detector_placement", "count_calibration", "observed_simulated_count_comparison"]}
    if build.get("netconvert", {}).get("command"):
        command = build["netconvert"]["command"]
        raw = Path(build["netconvert"]["cwd"]) / command[command.index("--output-file") + 1]
        prepared_full = Path(build.get("net_file", ""))
        if raw.is_file() and prepared_full.is_file():
            selected = build_road_hierarchy_variant(net_file=prepared_full, output_dir=output / "02-main-roads",
                keep_tier=fixture["keep_tier"], vehicle_class=fixture["vehicle_class"],
                protected_way_ids=fixture["protected_way_ids"], netconvert_binary=netconvert,
                sumo_binary=str(binary_dir / f"sumo{suffix}"))
            case.update(status=selected["status"], raw_native_import={"path": str(raw), "sha256": file_sha256(raw)},
                selection_input={"path": str(prepared_full), "sha256": file_sha256(prepared_full)},
                road_selection_report=selected["report_file"], source_road_count=selected.get("source_road_count"),
                selected_road_count=selected.get("selected_road_count"))
    case["claim_boundary"] = "Only stages 1 and 2 are implemented. A loadable road scope is not a completed sensor-calibrated demonstration."
    write_json_atomic(output / "case.json", case)
    print(json.dumps(case, indent=2), flush=True)
    return 0 if case["status"] in {"measured", "no_effect"} else 3


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
