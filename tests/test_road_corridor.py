import gzip
from pathlib import Path

from torii_sumo.core.road_corridor import (
    audit_node_group_corridors,
    corridor_key_from_osm,
    parse_osm_way_context,
    source_node_ids,
)
from torii_sumo.core.topology_audit import audit_topology_fragmentation


def _write_gzip(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)


def test_parse_osm_way_context_preserves_way_children_from_gzip(tmp_path: Path) -> None:
    osm_file = tmp_path / "network.osm.xml.gz"
    _write_gzip(
        osm_file,
        """<osm>
  <node id="1" lat="48.0" lon="11.0"/>
  <node id="2" lat="48.0" lon="11.1"/>
  <way id="10">
    <nd ref="1"/>
    <nd ref="2"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Harderstrasse"/>
    <tag k="ref" v="B 13"/>
  </way>
</osm>
""",
    )

    context = parse_osm_way_context(osm_file)

    assert context["way_count"] == 1
    assert context["node_way_context"]["1"]["names"] == ["Harderstrasse"]
    assert context["node_way_context"]["1"]["refs"] == ["B 13"]
    assert context["node_way_context"]["1"]["highways"] == ["primary"]
    assert context["node_way_context"]["1"]["corridor_keys"] == ["name:harderstrasse"]
    assert context["node_way_context"]["2"]["corridor_keys"] == ["name:harderstrasse"]


def test_source_node_ids_expands_sumo_cluster_ids() -> None:
    assert source_node_ids("cluster_267517523_4215915459") == ("267517523", "4215915459")
    assert source_node_ids("12345") == ("12345",)


def test_corridor_key_prefers_name_then_ref_then_highway() -> None:
    assert corridor_key_from_osm(name=" Main Street ", ref="B 1", highway="primary") == "name:main street"
    assert corridor_key_from_osm(name="", ref=" B 1 ", highway="primary") == "ref:b 1"
    assert corridor_key_from_osm(name="", ref="", highway="tertiary") == "highway:tertiary"


def test_node_group_allows_one_local_named_intersection_cell() -> None:
    graph = {
        "junctions": {
            "1": {"x": 0.0, "y": 0.0, "type": "traffic_light"},
            "2": {"x": 8.0, "y": 0.0, "type": "priority"},
            "3": {"x": 0.0, "y": 8.0, "type": "priority"},
        }
    }
    osm_context = {
        "node_way_context": {
            "1": {"corridor_keys": ["name:main street", "name:north road"]},
            "2": {"corridor_keys": ["name:main street", "name:north road"]},
            "3": {"corridor_keys": ["name:main street", "name:north road"]},
        }
    }

    audit = audit_node_group_corridors(graph, osm_context, ["1", "2", "3"], "C001", "unit")

    assert audit["corridor_decision"] == "allow"
    assert audit["intersection_cell_count"] == 1
    assert audit["named_corridor_count"] == 2
    assert audit["corridor_partition_count"] == 1
    assert audit["max_corridor_partition_node_count"] == 3


def test_node_group_rejects_large_chained_multi_cell_group() -> None:
    graph = {
        "junctions": {
            str(index): {"x": float(index * 5), "y": 0.0, "type": "priority"}
            for index in range(24)
        }
    }
    osm_context = {"node_way_context": {}}
    for index in range(24):
        cell = index // 8
        osm_context["node_way_context"][str(index)] = {
            "corridor_keys": [f"name:road {cell}", f"name:cross {cell}"]
        }

    audit = audit_node_group_corridors(
        graph,
        osm_context,
        [str(index) for index in range(24)],
        "C999",
        "unit",
    )

    assert audit["corridor_decision"] == "reject"
    assert audit["intersection_cell_count"] == 3
    assert "three or more named intersection cells" in audit["corridor_reason"]


def test_topology_audit_enriches_clusters_with_osm_corridor_context(tmp_path: Path) -> None:
    net_file = tmp_path / "fragmented.net.xml"
    net_file.write_text(
        """<net>
  <edge id="a" from="1" to="2">
    <lane id="a_0" index="0" length="7.0" shape="0,0 7,0"/>
  </edge>
  <edge id="b" from="2" to="3">
    <lane id="b_0" index="0" length="7.0" shape="7,0 7,7"/>
  </edge>
  <junction id="1" x="0.0" y="0.0" type="traffic_light"/>
  <junction id="2" x="7.0" y="0.0" type="priority"/>
  <junction id="3" x="7.0" y="7.0" type="priority"/>
</net>
""",
        encoding="utf-8",
    )
    osm_file = tmp_path / "network.osm.xml.gz"
    _write_gzip(
        osm_file,
        """<osm>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Main Street"/>
  </way>
  <way id="11">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="secondary"/>
    <tag k="name" v="North Road"/>
  </way>
</osm>
""",
    )

    report = audit_topology_fragmentation(
        net_file=net_file,
        output_dir=tmp_path / "topology",
        prefix="corridor",
        cluster_radius_m=12.0,
        min_cluster_nodes=3,
        osm_file=osm_file,
    )

    cluster = report["suspicious_clusters"][0]
    assert report["corridor_audit_status"] == "pass"
    assert report["corridor_decision_counts"] == {"allow": 1}
    assert report["max_intersection_cell_count"] == 1
    assert cluster["corridor_decision"] == "allow"
    assert cluster["corridor_intersection_cell_count"] == 1
    assert cluster["corridor_named_corridor_count"] == 2
    csv_header = Path(report["clusters_file"]).read_text(encoding="utf-8").splitlines()[0]
    assert "corridor_decision" in csv_header
    assert "corridor_intersection_cell_signatures" in csv_header
