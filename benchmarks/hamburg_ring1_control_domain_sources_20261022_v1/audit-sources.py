"""Read frozen source geometry. Do not assign or change signal control."""

import argparse
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET

from shapely.geometry import LineString, Point
import sumolib

from astra_sumo.core.digital_twin import parse_mapem
from astra_sumo.core.hamburg_map_kml import (
    bind_hamburg_map_kml_to_mapem,
    parse_hamburg_map_kml,
)


def audit(repository, output):
    source = repository / "benchmarks/hamburg_ring1_official_sources_20261020_v1"
    net_path = repository / "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/diagnostic-inputs/network.net.xml"
    osm_path = repository / "benchmarks/hamburg_ring1_road_scope_20261020_v1/source.osm.xml.gz"
    net = sumolib.net.readNet(str(net_path), withInternal=True)
    register = json.loads((source / "lsa-identities.json").read_text(encoding="utf-8"))
    nodes = {node.get("id"): node for node in ET.fromstring(gzip.decompress(osm_path.read_bytes())).findall("node")}
    inputs = [net_path, osm_path, source / "lsa-identities.json", source / "manifest.json"]
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    source_rows = {row["path"]: row for row in source_manifest["files"]}
    geometries = {}
    with TemporaryDirectory() as temporary:
        for lsa in ("104", "118", "119", "200", "535"):
            paths = []
            for kind, suffix in (("map_xml", "xml"), ("map_kml", "kml")):
                name = f"{lsa}-{kind}.{suffix}.gz"
                compressed = source / name
                raw = gzip.decompress(compressed.read_bytes())
                assert hashlib.sha256(raw).hexdigest() == source_rows[name]["sha256"]
                inputs.append(compressed)
                path = Path(temporary) / name[:-3]
                path.write_bytes(raw)
                paths.append(path)
            lanes, connections = parse_mapem(paths[0])
            bound = bind_hamburg_map_kml_to_mapem(
                parse_hamburg_map_kml(paths[1]), lanes, connections, expected_node_id=lsa
            )
            geometries[lsa] = bound
    rows = []
    for osm_id in ("266199063", "1966837175", "244203525", "266197525"):
        point = Point(net.getNode(osm_id).getCoord())
        nearest_register = []
        for feature in register["features"]:
            longitude, latitude = feature["geometry"]["coordinates"][0]
            distance = point.distance(Point(net.convertLonLat2XY(longitude, latitude)))
            nearest_register.append({"distance_m": distance, **feature["properties"]})
        nearest_lanes = []
        for lsa, geometry in geometries.items():
            for lane in geometry["lanes"]:
                line = LineString([net.convertLonLat2XY(*coordinate[:2]) for coordinate in lane["coordinates"]])
                connections = [
                    connection for connection in geometry["connections"]
                    if lane["lane_id"] in (connection["ingress_lane_id"], connection["egress_lane_id"])
                ]
                nearest_lanes.append({
                    "lsa": lsa, "lane_id": lane["lane_id"], "lane_type": lane["lane_type"],
                    "distance_m": point.distance(line),
                    "official_connections": [{key: connection[key] for key in (
                        "connection_id", "ingress_lane_id", "egress_lane_id", "signal_group"
                    )} for connection in connections],
                })
        osm_node = nodes[osm_id]
        assert Point(net.convertLonLat2XY(float(osm_node.get("lon")), float(osm_node.get("lat")))).distance(point) < .01
        rows.append({
            "osm_node_id": osm_id, "model_tls_id": "GS_266199063" if osm_id == "266199063" else osm_id,
            "longitude": float(osm_node.get("lon")), "latitude": float(osm_node.get("lat")),
            "osm_tags": {tag.get("k"): tag.get("v") for tag in osm_node.findall("tag")},
            "nearest_register": sorted(nearest_register, key=lambda row: row["distance_m"])[:3],
            "nearest_map_lanes": sorted(nearest_lanes, key=lambda row: row["distance_m"])[:3],
            "control_binding": "unresolved", "model_change": False,
        })
    assert len(rows) == 4 and all(row["control_binding"] == "unresolved" for row in rows)
    result = {
        "schema": "astra.hamburg-control-domain-source-check/v1",
        "sumolib_version": version("sumolib"),
        "inputs": [{"path": path.relative_to(repository).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in inputs],
        "nodes": rows, "model_changed": False, "new_simulations": 0,
        "claim_boundary": "Distances identify candidates only. Official lane/group identities do not bind an OSM signal head. The frozen point register names a separate LSA209 but does not assign these OSM nodes to it. No signal is removed or rebound.",
    }
    assert result["sumolib_version"] == "1.27.1"
    output.mkdir(parents=True, exist_ok=False)
    (output / "source-check.json").write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({"nodes": len(rows), "unresolved": len(rows), "new_simulations": 0, "model_changed": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    audit(Path(__file__).resolve().parents[2], options.output.resolve())
