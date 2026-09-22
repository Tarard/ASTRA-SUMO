from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from torii_sumo.core.digital_twin import parse_mapem
from torii_sumo.core.hamburg_map_kml import (
    HamburgMapKmlError,
    bind_hamburg_map_kml_to_mapem,
    parse_hamburg_map_kml,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MAP_2394 = (
    REPO_ROOT
    / "artifacts"
    / "hamburg_sandtorkai_twin_20260711"
    / "twin_rebuilt_probe"
    / "official"
    / "signals"
    / "assets"
    / "2394_map_kml.kml"
)
MAP_XML_2394 = MAP_2394.with_name("2394_map_xml.xml")
HAMBURG_SCREEN_ASSETS = REPO_ROOT / "artifacts" / "hamburg_corridor_screen_assets_v1"
HAMBURG_SCREEN_EXPORTS = [
    ("1923", "MAP_ITS_19_1923_6.2_R2_Quelle_ETRS89.kml", "MAP_ITS_19_1923_6.2.xml"),
    ("2363", "MAP_ITS_23_2363_6.3_R3_Quelle_ETRS89.kml", "MAP_ITS_23_2363_6.3.xml"),
    ("2150", "MAP_ITS_21_2150_11.2_R2_Quelle_ETRS89.kml", "MAP_ITS_21_2150_11.2.xml"),
]

SYNTHETIC_MAP_KML = """<kml>
  <Folder>
    <name>MAP</name>
    <Folder>
      <name>Base Points</name>
      <Placemark><name>Base Point</name><Point><coordinates>9,53,0</coordinates></Point></Placemark>
    </Folder>
    <Folder>
      <name>Lanes</name>
      <Placemark>
        <name>Lane 1</name><styleUrl>#laneStyleIn</styleUrl>
        <LineString><coordinates>9,53,0 9.1,53.1,0</coordinates></LineString>
      </Placemark>
      <Placemark>
        <name>Lane 2</name><styleUrl>#laneStyleOut</styleUrl>
        <LineString><coordinates>9.1,53.1,0 9.2,53.2,0</coordinates></LineString>
      </Placemark>
    </Folder>
    <Folder><name>Crosswalks</name></Folder>
    <Folder>
      <name>Connections</name>
      <Placemark>
        <name>Con. 1: 1 → 2</name>
        <LineString><coordinates>9.05,53.05,0 9.15,53.15,0</coordinates></LineString>
      </Placemark>
    </Folder>
    <Folder>
      <name>Drive lines</name>
      <Placemark>
        <name>DrvLn. 1: 1 → 2</name>
        <LineString><coordinates>9.05,53.05,0 9.15,53.15,0</coordinates></LineString>
      </Placemark>
    </Folder>
    <Folder>
      <name>Points</name>
      <Placemark><name>Lane 1 A</name><Point><coordinates>9,53,0</coordinates></Point></Placemark>
      <Placemark><name>Lane 1 B</name><Point><coordinates>9.1,53.1,0</coordinates></Point></Placemark>
      <Placemark><name>Lane 2 A</name><Point><coordinates>9.1,53.1,0</coordinates></Point></Placemark>
      <Placemark><name>Lane 2 B</name><Point><coordinates>9.2,53.2,0</coordinates></Point></Placemark>
    </Folder>
    <Folder>
      <name>Merge points</name>
      <Placemark><name>Lane 1 Merge</name><Point><coordinates>9.08,53.08,0</coordinates></Point></Placemark>
    </Folder>
  </Folder>
</kml>
"""


def _write_synthetic_map_kml(tmp_path: Path, text: str = SYNTHETIC_MAP_KML) -> Path:
    path = tmp_path / "synthetic-map.kml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.skipif(
    not MAP_2394.is_file(),
    reason="untracked official Hamburg 2394 MAP KML is unavailable",
)
def test_parse_real_hamburg_2394_map_kml() -> None:
    expected_sha256 = hashlib.sha256(MAP_2394.read_bytes()).hexdigest()

    result = parse_hamburg_map_kml(MAP_2394, expected_sha256=expected_sha256)

    assert result["status"] == "pass"
    assert result["source"]["sha256"] == expected_sha256
    assert result["counts"] == {
        "base_point_count": 2,
        "lanes_folder_count": 13,
        "crosswalks_folder_count": 12,
        "connection_count": 16,
        "drive_line_count": 16,
        "endpoint_count": 50,
        "merge_point_count": 6,
    }
    assert {item["lane_id"] for item in result["lanes"]} == set(range(1, 15)) - {8}
    assert {item["role"] for item in result["lanes"]} == {"ingress", "egress"}
    movement = next(item for item in result["drive_lines"] if item["connection_id"] == 8)
    assert movement["from_lane_id"] == 6
    assert movement["to_lane_id"] == 5
    assert len(movement["coordinates"]) == 4
    assert result["gates"]["connection_drive_line_identity"] == "pass"


@pytest.mark.skipif(
    not (MAP_2394.is_file() and MAP_XML_2394.is_file()),
    reason="untracked official Hamburg 2394 MAP KML/XML files are unavailable",
)
def test_bind_real_hamburg_2394_kml_to_mapem() -> None:
    lanes, connections = parse_mapem(MAP_XML_2394)

    result = bind_hamburg_map_kml_to_mapem(
        parse_hamburg_map_kml(MAP_2394),
        lanes,
        connections,
        expected_node_id="2394",
    )

    assert result["status"] == "pass"
    assert result["counts"] == {
        "lane_count": 25,
        "connection_count": 16,
        "lane_types": {"bikeLane": 4, "crosswalk": 10, "vehicle": 11},
    }
    conn8 = next(item for item in result["connections"] if item["connection_id"] == "8")
    assert conn8["ingress_lane_id"] == "6"
    assert conn8["egress_lane_id"] == "5"
    assert len(conn8["drive_line_coordinates"]) == 4


@pytest.mark.parametrize(
    ("node_id", "kml_name", "xml_name"),
    HAMBURG_SCREEN_EXPORTS,
)
@pytest.mark.skipif(
    not all(
        (HAMBURG_SCREEN_ASSETS / kml_name).is_file()
        and (HAMBURG_SCREEN_ASSETS / xml_name).is_file()
        for _, kml_name, xml_name in HAMBURG_SCREEN_EXPORTS
    ),
    reason="untracked official Hamburg screen export KML/XML files are unavailable",
)
def test_bind_current_hamburg_exports_with_restarted_mapem_connection_ids(
    node_id: str, kml_name: str, xml_name: str
) -> None:
    kml = parse_hamburg_map_kml(HAMBURG_SCREEN_ASSETS / kml_name)
    lanes, connections = parse_mapem(HAMBURG_SCREEN_ASSETS / xml_name)

    result = bind_hamburg_map_kml_to_mapem(kml, lanes, connections, expected_node_id=node_id)

    assert result["status"] == "pass"
    assert result["connection_identity_basis"] == "unique_ingress_egress_lane_pair"
    assert result["gates"]["connection_lane_pair_identity"] == "pass"
    assert all(item["mapem_connection_id"] for item in result["connections"])
    assert all(item["kml_connection_id"] for item in result["connections"])


def test_map_kml_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = _write_synthetic_map_kml(tmp_path)

    with pytest.raises(HamburgMapKmlError, match="SHA-256"):
        parse_hamburg_map_kml(source, expected_sha256="0" * 64)


def test_map_kml_rejects_connection_drive_line_mismatch(tmp_path: Path) -> None:
    text = SYNTHETIC_MAP_KML.replace(
        "DrvLn. 1: 1 → 2",
        "DrvLn. 1: 1 → 1",
        1,
    )
    candidate = _write_synthetic_map_kml(tmp_path, text)

    with pytest.raises(HamburgMapKmlError, match="connection/drive-line identity mismatch"):
        parse_hamburg_map_kml(candidate)


def test_map_kml_preserves_multiple_merge_points_for_one_lane(tmp_path: Path) -> None:
    block = "<Placemark><name>Lane 1 Merge</name><Point><coordinates>9.08,53.08,0</coordinates></Point></Placemark>"
    candidate = _write_synthetic_map_kml(
        tmp_path,
        SYNTHETIC_MAP_KML.replace(block, block + block, 1),
    )

    result = parse_hamburg_map_kml(candidate)

    assert result["counts"]["merge_point_count"] == 2
    lane_1 = [item for item in result["merge_points"] if item["lane_id"] == 1]
    assert len(lane_1) == 2


def test_map_kml_accepts_current_pedestrian_and_bike_way_names(tmp_path: Path) -> None:
    crossings = """<Folder><name>Crosswalks</name>
      <Placemark><name>Pedestrian way 3</name><LineString><coordinates>9,53,0 9.1,53.1,0</coordinates></LineString></Placemark>
      <Placemark><name>Bike way 4</name><LineString><coordinates>9.1,53.1,0 9.2,53.2,0</coordinates></LineString></Placemark>
    </Folder>"""
    points = """<Placemark><name>Pedestrian way 3 A</name><Point><coordinates>9,53,0</coordinates></Point></Placemark>
      <Placemark><name>Pedestrian way 3 B</name><Point><coordinates>9.1,53.1,0</coordinates></Point></Placemark>
      <Placemark><name>Bike way 4 A</name><Point><coordinates>9.1,53.1,0</coordinates></Point></Placemark>
      <Placemark><name>Bike way 4 B</name><Point><coordinates>9.2,53.2,0</coordinates></Point></Placemark>"""
    candidate = _write_synthetic_map_kml(
        tmp_path,
        SYNTHETIC_MAP_KML.replace(
            '<Folder><name>Crosswalks</name></Folder>',
            crossings,
        ).replace(
            '<Placemark><name>Lane 2 B</name><Point><coordinates>9.2,53.2,0</coordinates></Point></Placemark>',
            '<Placemark><name>Lane 2 B</name><Point><coordinates>9.2,53.2,0</coordinates></Point></Placemark>'
            + points,
        ),
    )

    result = parse_hamburg_map_kml(candidate)

    assert [item["lane_id"] for item in result["crossing_lanes"]] == [3, 4]
    assert {
        (item["feature_kind"], item["lane_id"], item["endpoint"])
        for item in result["endpoints"]
        if item["lane_id"] in {3, 4}
    } == {
        ("crosswalk", 3, "A"),
        ("crosswalk", 3, "B"),
        ("crosswalk", 4, "A"),
        ("crosswalk", 4, "B"),
    }
