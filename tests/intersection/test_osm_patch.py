import gzip
from pathlib import Path

from torii_sumo.intersection.osm_patch import parse_osm_xml


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_osm_xml_keeps_raw_nodes_ways_relations_and_projected_xy() -> None:
    patch = parse_osm_xml(FIXTURES / "x4_signalized.osm.xml")

    assert patch.bbox.min_lon == 11.0
    assert patch.nodes["1"].tags == {"highway": "traffic_signals"}
    assert patch.nodes["1"].x is not None
    assert patch.nodes["1"].y is not None
    assert patch.ways["10"].node_refs == ["2", "1", "3"]
    assert patch.ways["10"].tags["highway"] == "primary"
    assert patch.relations == {}


def test_parse_osm_xml_reads_gzip_source(tmp_path: Path) -> None:
    source = FIXTURES / "x4_signalized.osm.xml"
    gz_file = tmp_path / "x4_signalized.osm.xml.gz"
    gz_file.write_bytes(gzip.compress(source.read_bytes()))

    patch = parse_osm_xml(gz_file)

    assert patch.bbox.min_lon == 11.0
    assert patch.ways["10"].tags["highway"] == "primary"
