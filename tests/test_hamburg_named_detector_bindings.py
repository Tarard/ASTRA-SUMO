from __future__ import annotations

import csv
import json
from pathlib import Path

from pyproj import Transformer

from torii_sumo.core.hamburg_named_detector_bindings import (
    materialize_hamburg_named_detector_bindings,
)


def _write_network(path: Path, *, parallel: bool) -> tuple[float, float]:
    transformer = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    x, y = 565950.0, 5933205.0
    if parallel:
        lanes = (
            f'<lane id="road_0" index="0" speed="13.9" length="100" allow="passenger" shape="{x},{y - 2.5} {x + 100},{y - 2.5}"/>\n'
            f'<lane id="road_1" index="1" speed="13.9" length="100" allow="passenger" shape="{x},{y + 2.5} {x + 100},{y + 2.5}"/>'
        )
    else:
        lanes = f'<lane id="road_0" index="0" speed="13.9" length="100" allow="passenger" shape="{x},{y} {x + 100},{y}"/>'
    path.write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>
<net version="1.27">
  <location netOffset="0,0" convBoundary="{x},{y - 10},{x + 100},{y + 10}" origBoundary="{x},{y - 10},{x + 100},{y + 10}" projParameter="!"/>
  <edge id="road" from="a" to="b" priority="1" numLanes="{2 if parallel else 1}">
    {lanes}
  </edge>
  <junction id="a" type="priority" x="{x}" y="{y}" incLanes="" intLanes=""/>
  <junction id="b" type="priority" x="{x + 100}" y="{y}" incLanes="road_0" intLanes=""/>
</net>
''',
        encoding="utf-8",
    )
    return transformer.transform(x, y)


def _write_stream_snapshot(path: Path, longitude: float, latitude: float) -> None:
    path.write_text(
        json.dumps(
            {
                "pages_by_node": {
                    "2394": [
                        {
                            "value": [
                                {
                                    "@iot.id": 28495,
                                    "properties": {
                                        "knotenName": "2394",
                                        "assetID": "Z.1",
                                        "fahrspur": "",
                                    },
                                    "Thing": {
                                        "@iot.id": 1,
                                        "properties": {
                                            "richtung": "Richtung 1",
                                            "operationStart": "2026-02-28T23:00:00Z",
                                        },
                                        "Locations": [
                                            {
                                                "location": {
                                                    "type": "Feature",
                                                    "geometry": {
                                                        "type": "Point",
                                                        "coordinates": [longitude, latitude],
                                                    },
                                                }
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )


def _write_serial_split_network(path: Path) -> tuple[float, float]:
    transformer = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    x, y = 565950.0, 5933205.0
    path.write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>
<net version="1.27">
  <location netOffset="0,0" convBoundary="{x},{y - 10},{x + 100},{y + 10}" origBoundary="{x},{y - 10},{x + 100},{y + 10}" projParameter="!"/>
  <edge id="road#0" from="a" to="cut" priority="1"><lane id="road#0_0" index="0" speed="13.9" length="50" allow="passenger" shape="{x},{y} {x + 50},{y}"/></edge>
  <edge id="road#1" from="cut" to="b" priority="1"><lane id="road#1_0" index="0" speed="13.9" length="50" allow="passenger" shape="{x + 50},{y} {x + 100},{y}"/></edge>
  <junction id="a" type="priority" x="{x}" y="{y}" incLanes="" intLanes=""/>
  <junction id="cut" type="priority" x="{x + 50}" y="{y}" incLanes="road#0_0" intLanes=""/>
  <junction id="b" type="priority" x="{x + 100}" y="{y}" incLanes="road#1_0" intLanes=""/>
  <connection from="road#0" to="road#1" fromLane="0" toLane="0" dir="s" state="M"/>
</net>
''',
        encoding="utf-8",
    )
    return transformer.transform(x + 50, y)


def _write_map_lane(path: Path) -> None:
    transformer = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    longitude, latitude = transformer.transform(566050.0, 5933202.5)
    path.write_text(
        f"""<MAPEM><map><intersections><IntersectionGeometry>
  <id><id>2394</id></id>
  <refPoint><lat>{round(latitude * 10_000_000)}</lat><long>{round(longitude * 10_000_000)}</long></refPoint>
  <laneSet><GenericLane><laneID>6</laneID><ingressApproach>1</ingressApproach>
    <laneAttributes><laneType><vehicle>00000000</vehicle></laneType></laneAttributes>
    <nodeList><nodes>
      <NodeXY><delta><node-XY5><x>0</x><y>0</y></node-XY5></delta></NodeXY>
      <NodeXY><delta><node-XY5><x>-10000</x><y>0</y></node-XY5></delta></NodeXY>
    </nodes></nodeList>
  </GenericLane></laneSet>
</IntersectionGeometry></intersections></map></MAPEM>""",
        encoding="utf-8",
    )


def _write_constellation_network(path: Path) -> tuple[float, float]:
    transformer = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    x, y = 565950.0, 5933205.0
    lanes = "\n".join(
        f'<lane id="road#0_{index}" index="{index}" speed="13.9" length="100" allow="passenger" '
        f'shape="{x},{y + index * 3.2} {x + 100},{y + index * 3.2}"/>'
        for index in range(4)
    )
    path.write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>
<net version="1.27">
  <location netOffset="0,0" convBoundary="{x},{y - 10},{x + 100},{y + 20}" origBoundary="{x},{y - 10},{x + 100},{y + 20}" projParameter="!"/>
  <edge id="road#0" from="a" to="b" priority="1" numLanes="4">{lanes}</edge>
  <junction id="a" type="priority" x="{x}" y="{y}" incLanes="" intLanes=""/>
  <junction id="b" type="priority" x="{x + 100}" y="{y}" incLanes="road#0_0 road#0_1 road#0_2 road#0_3" intLanes=""/>
</net>
''',
        encoding="utf-8",
    )
    return transformer.transform(x + 50.0, y)


def _write_constellation_streams(path: Path, longitude: float, latitude: float) -> None:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    reverse = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    x, y = transformer.transform(longitude, latitude)
    values = []
    for index in range(4):
        point_lon, point_lat = reverse.transform(x, y + 4.3 + index * 3.2)
        values.append(
            {
                "@iot.id": 100 + index,
                "properties": {"knotenName": "2403", "assetID": f"Z.{index + 1}", "fahrspur": ""},
                "Thing": {
                    "@iot.id": 200 + index,
                    "properties": {"richtung": "Richtung 1", "operationStart": "2026-02-28T23:00:00Z"},
                    "Locations": [
                        {
                            "location": {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [point_lon, point_lat]},
                            }
                        }
                    ],
                },
            }
        )
    path.write_text(json.dumps({"pages_by_node": {"2403": [{"value": values}]}}), encoding="utf-8")


def _write_movement_evidence(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-bounded-movement-evidence/v1",
                "official_node_id": "2403",
                "official_sources": [
                    {"url": "https://example.test/official-plan.pdf", "sha256": "a" * 64}
                ],
                "lane_policy": {"motor_lane_count": 4},
                "authorized_addition": {"from_edge": "road#2"},
            }
        ),
        encoding="utf-8",
    )


def test_projection_fallback_promotes_unique_geometry_binding_and_withholds_no_sensor(tmp_path: Path) -> None:
    net = tmp_path / "candidate.net.xml"
    lon, lat = _write_network(net, parallel=False)
    streams = tmp_path / "streams.raw.json"
    _write_stream_snapshot(streams, lon, lat)

    report = materialize_hamburg_named_detector_bindings(
        net_file=net,
        count_stream_file=streams,
        output_dir=tmp_path / "bindings",
    )

    assert report["automatic_promotion_gate"] == "pass"
    assert report["gates"]["coordinate_projection"] == "pass"
    assert report["artifacts"]["e1_e2_additional"]["status"] == "withheld_pending_complete_lane_mapping"
    with (tmp_path / "bindings" / "detector_mapping.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["mapping_status"] == "active"
    assert row["sumo_lane"] == "road_0"


def test_parallel_lane_tie_is_rejected_without_manual_override(tmp_path: Path) -> None:
    net = tmp_path / "candidate.net.xml"
    lon, lat = _write_network(net, parallel=True)
    streams = tmp_path / "streams.raw.json"
    _write_stream_snapshot(streams, lon, lat)

    report = materialize_hamburg_named_detector_bindings(
        net_file=net,
        count_stream_file=streams,
        output_dir=tmp_path / "bindings",
    )

    assert report["automatic_promotion_gate"] == "blocked"
    assert report["incomplete_stream_ids"] == [28495]
    candidates = json.loads((tmp_path / "bindings" / "detector_lane_candidates.json").read_text(encoding="utf-8"))
    assert candidates["rows"][0]["candidate_status"] == "needs_review"
    assert "ambiguous" in candidates["rows"][0]["reason"]


def test_shared_lane_streams_withhold_sensor_materialization_without_aggregation_evidence(
    tmp_path: Path,
) -> None:
    net = tmp_path / "candidate.net.xml"
    lon, lat = _write_network(net, parallel=False)
    streams = tmp_path / "streams.raw.json"
    _write_stream_snapshot(streams, lon, lat)
    snapshot = json.loads(streams.read_text(encoding="utf-8"))
    duplicate = dict(snapshot["pages_by_node"]["2394"][0]["value"][0])
    duplicate["@iot.id"] = 28496
    duplicate["properties"] = {**duplicate["properties"], "assetID": "Z.2"}
    duplicate["Thing"] = {**duplicate["Thing"], "@iot.id": 2}
    snapshot["pages_by_node"]["2394"][0]["value"].append(duplicate)
    streams.write_text(json.dumps(snapshot), encoding="utf-8")

    report = materialize_hamburg_named_detector_bindings(
        net_file=net,
        count_stream_file=streams,
        output_dir=tmp_path / "bindings",
    )

    assert report["active_mapping_count"] == 2
    assert report["gates"]["unique_lane_binding"] == "pass"
    assert report["gates"]["sensor_aggregation_semantics"] == "blocked"
    assert report["automatic_promotion_gate"] == "blocked"
    assert report["shared_lane_stream_groups"] == [
        {
            "node_id": "2394",
            "sumo_lane": "road_0",
            "sumo_edge": "road",
            "stream_ids": [28495, 28496],
            "asset_ids": ["Z.1", "Z.2"],
            "lane_positions": [0.1, 0.1],
        }
    ]
    assert report["next_action"] == "resolve_shared_lane_detector_aggregation_semantics"


def test_serial_edge_cut_is_one_lane_hypothesis_and_uses_upstream_segment(tmp_path: Path) -> None:
    net = tmp_path / "candidate.net.xml"
    lon, lat = _write_serial_split_network(net)
    streams = tmp_path / "streams.raw.json"
    _write_stream_snapshot(streams, lon, lat)

    report = materialize_hamburg_named_detector_bindings(
        net_file=net,
        count_stream_file=streams,
        output_dir=tmp_path / "bindings",
    )

    assert report["automatic_promotion_gate"] == "pass"
    with (tmp_path / "bindings" / "detector_mapping.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["sumo_lane"] == "road#0_0"
    candidates = json.loads((tmp_path / "bindings" / "detector_lane_candidates.json").read_text(encoding="utf-8"))
    assert candidates["rows"][0]["candidates"][0]["equivalent_segment_lane_ids"] == [
        "road#0_0",
        "road#1_0",
    ]


def test_official_map_lane_identity_resolves_parallel_geometry_tie(tmp_path: Path) -> None:
    net = tmp_path / "candidate.net.xml"
    lon, lat = _write_network(net, parallel=True)
    streams = tmp_path / "streams.raw.json"
    _write_stream_snapshot(streams, lon, lat)
    map_file = tmp_path / "2394-map.xml"
    _write_map_lane(map_file)

    report = materialize_hamburg_named_detector_bindings(
        net_file=net,
        count_stream_file=streams,
        output_dir=tmp_path / "bindings",
        map_files=(map_file,),
    )

    assert report["automatic_promotion_gate"] == "pass"
    with (tmp_path / "bindings" / "detector_mapping.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["sumo_lane"] == "road_0"
    assert "official MAP" in row["mapping_reason"]


def test_official_lane_count_and_detector_constellation_resolve_common_geometry_shift(tmp_path: Path) -> None:
    net = tmp_path / "candidate.net.xml"
    lon, lat = _write_constellation_network(net)
    streams = tmp_path / "streams.raw.json"
    _write_constellation_streams(streams, lon, lat)
    evidence = tmp_path / "movement-evidence.json"
    _write_movement_evidence(evidence)

    report = materialize_hamburg_named_detector_bindings(
        net_file=net,
        count_stream_file=streams,
        output_dir=tmp_path / "bindings",
        movement_evidence_file=evidence,
    )

    assert report["automatic_promotion_gate"] == "pass"
    with (tmp_path / "bindings" / "detector_mapping.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["asset_id"]: row for row in csv.DictReader(handle)}
    assert {asset: rows[asset]["sumo_lane"] for asset in rows} == {
        "Z.1": "road#0_0",
        "Z.2": "road#0_1",
        "Z.3": "road#0_2",
        "Z.4": "road#0_3",
    }
    assert all(row["mapping_confidence"] == "medium" for row in rows.values())
    candidates = json.loads((tmp_path / "bindings" / "detector_lane_candidates.json").read_text(encoding="utf-8"))
    assert all(row["constellation_inference"]["claim_boundary"].startswith("geometric") for row in candidates["rows"])
