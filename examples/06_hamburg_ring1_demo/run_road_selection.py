"""Rebuild the frozen Ring1 OSM import and select its main-road study scope offline."""
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

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
    anchor_file = fixture_dir / fixture["official_approach_anchors_file"]
    anchors = json.loads(anchor_file.read_text(encoding="utf-8"))
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
                           for name in ["osm_network.py", "road_hierarchy.py", "road_scope.py", "osm_access.py",
                                        "local_network_conversion.py", "network_geometry_freeze.py",
                                        "junction_rebuild/geometry.py", "junction_rebuild/boundary_restore.py",
                                        "junction_rebuild/restoration.py"]},
        "remaining_stages": ["junction_consolidation", "detector_placement", "count_calibration", "observed_simulated_count_comparison"]}
    if build.get("netconvert", {}).get("command"):
        command = build["netconvert"]["command"]
        raw = Path(build["netconvert"]["cwd"]) / command[command.index("--output-file") + 1]
        prepared_full = Path(build.get("net_file", ""))
        if raw.is_file() and prepared_full.is_file():
            selected = build_road_hierarchy_variant(net_file=prepared_full, output_dir=output / "02-main-roads",
                keep_tier=fixture["keep_tier"], vehicle_class=fixture["vehicle_class"],
                protected_way_ids=fixture["protected_way_ids"], protected_edge_ids=anchors["protected_edge_ids"], netconvert_binary=netconvert,
                protected_edge_evidence=anchors.get("edge_evidence"),
                sumo_binary=str(binary_dir / f"sumo{suffix}"))
            case.update(status=selected["status"], raw_native_import={"path": str(raw), "sha256": file_sha256(raw)},
                selection_input={"path": str(prepared_full), "sha256": file_sha256(prepared_full)},
                road_selection_report=selected["report_file"], source_road_count=selected.get("source_road_count"),
                selected_road_count=selected.get("selected_road_count"))
            prepared_content_hash = hashlib.sha256(ET.canonicalize(prepared_full.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
            case["official_approach_protection"] = {"path": str(anchor_file), "sha256": file_sha256(anchor_file),
                "source_content_matches": prepared_content_hash == anchors["source_network_content_sha256"],
                "source_network_content_sha256": prepared_content_hash,
                "protected_edge_count": len(anchors["protected_edge_ids"]), "motor_lane_counts": anchors["motor_lane_counts"]}
            raw_content_hash = hashlib.sha256(ET.canonicalize(raw.read_text(encoding="utf-8")).encode("utf-8")).hexdigest()
            case["reproduction"] = {"raw_network_content_sha256": raw_content_hash, "checks": {
                "raw_network_matches_fixture": raw_content_hash == fixture["native_network_content_sha256"],
                "source_road_count_matches": selected.get("source_road_count") == fixture["expected_source_road_count"],
                "selected_road_count_matches": selected.get("selected_road_count") == fixture["expected_prepared_selected_road_count"]}}
            case["reproduction"]["selected_network_content_sha256"] = hashlib.sha256(ET.canonicalize(
                Path(selected["candidate"]["path"]).read_text(encoding="utf-8"), strip_text=True, with_comments=False
            ).encode("utf-8")).hexdigest()
            case["reproduction"]["selected_network_hash_recipe"] = "ElementTree C14N 2.0, strip_text=True, with_comments=False"
            geometry = selected.get("retained_road_geometry", {})
            signals = selected.get("signal_program_changes", {})
            case["review"] = {"geometry_status": geometry.get("status", "not_measured"),
                "lane_attribute_changes": geometry.get("ordinary_lane_attribute_changes"),
                "max_lane_shape_displacement_m": geometry.get("max_outside_lane_shape_displacement_xy_m"),
                "signal_status": signals.get("status", "not_measured"),
                "signal_difference_count": len(signals.get("unexpected_changes", {})),
                "signal_changes_by_scope": {key: len(values) for key, values in selected.get("signal_scope_changes", {}).items()},
                "warnings": selected.get("warnings", [])}
            case["reproduction"]["status"] = "matches_fixture" if all(case["reproduction"]["checks"].values()) else "different_input"
            if case["reproduction"]["status"] == "different_input":
                case["review"]["warnings"].append("The run differs from the frozen example expectations. Inspect the reproduction checks.")
            if not case["official_approach_protection"]["source_content_matches"]:
                case["review"]["warnings"].append("The prepared source differs from the tentative official approach evidence. Refresh its correspondence before using the result for calibration.")
    case["claim_boundary"] = "Only stages 1 and 2 are implemented. A loadable road scope is not a completed sensor-calibrated demonstration."
    write_json_atomic(output / "case.json", case)
    print(json.dumps(case, indent=2), flush=True)
    return 0 if case["status"] in {"measured", "no_effect"} else 3


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
