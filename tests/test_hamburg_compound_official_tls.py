from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.hamburg_compound_official_tls import (
    COMPOUND_ROUTING_REMOVALS,
    HamburgCompoundOfficialTlsError,
    _append_connection_delete_directives,
    materialize_hamburg_compound_official_tls_candidate,
    _validate_compound_topology,
    _write_compiled_source_owner_patch,
)
from torii_sumo.core.hamburg_2394_tls_topology import ROUTING_REMOVALS
from torii_sumo.core.official_tls_rebuild import (
    OfficialTlsGroup,
    OfficialTlsPlan,
    PhysicalControlledLink,
)


SCRIPT = Path("plugins/torii-sumo/scripts/build_hamburg_compound_official_tls.py")


def test_compound_routing_prune_reuses_2394_inventory_and_adds_2349() -> None:
    expected = {
        item.key for item in ROUTING_REMOVALS
    } | {("554713078#2", 1, "554713075#0", 1)}

    assert {item.key for item in COMPOUND_ROUTING_REMOVALS} == expected


def test_compound_routing_prune_writes_explicit_compiled_net_deletes(tmp_path: Path) -> None:
    patch = tmp_path / "connections.con.xml"
    patch.write_text("<connections/>\n", encoding="utf-8")

    report = _append_connection_delete_directives(
        patch,
        removals=COMPOUND_ROUTING_REMOVALS,
    )
    delete_keys = {
        (
            element.attrib["from"],
            int(element.attrib["fromLane"]),
            element.attrib["to"],
            int(element.attrib["toLane"]),
        )
        for element in ET.parse(patch).getroot().findall("delete")
    }

    assert report["delete_directive_count"] == 6
    assert delete_keys == {item.key for item in COMPOUND_ROUTING_REMOVALS}


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "build_hamburg_compound_official_tls",
        SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_owner_net(path: Path) -> None:
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<net>
    <edge id="west_in" from="west" to="owner_a"><lane id="west_in_0"/></edge>
    <edge id="west_out" from="owner_a" to="west"><lane id="west_out_0"/></edge>
    <edge id="east_in" from="east" to="owner_b"><lane id="east_in_0"/></edge>
    <edge id="east_out" from="owner_b" to="east"><lane id="east_out_0"/></edge>
    <edge id="passive_in" from="south" to="owner_c"><lane id="passive_in_0"/></edge>
    <edge id="passive_out" from="owner_c" to="south"><lane id="passive_out_0"/></edge>
    <junction id="west" type="dead_end"/>
    <junction id="east" type="dead_end"/>
    <junction id="south" type="dead_end"/>
    <junction id="owner_a" type="traffic_light"/>
    <junction id="owner_b" type="traffic_light"/>
    <junction id="owner_c" type="traffic_light"/>
    <connection from="west_in" to="west_out" fromLane="0" toLane="0" tl="old_a" linkIndex="0"/>
    <connection from="east_in" to="east_out" fromLane="0" toLane="0" tl="old_b" linkIndex="0"/>
    <connection from="passive_in" to="passive_out" fromLane="0" toLane="0" tl="old_c" linkIndex="0"/>
</net>
""",
        encoding="utf-8",
    )


def test_owner_patch_keeps_physical_owners_separate_under_one_controller(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    output = tmp_path / "owners.nod.xml"
    _write_owner_net(source)
    plan = OfficialTlsPlan(
        plan_id="compound",
        version="1",
        groups=(
            OfficialTlsGroup(
                "2349",
                "K1",
                "HH_2349",
                0,
                (PhysicalControlledLink("west_in", 0, "west_out", 0),),
            ),
            OfficialTlsGroup(
                "2349",
                "K2",
                "HH_2349",
                1,
                (PhysicalControlledLink("east_in", 0, "east_out", 0),),
            ),
        ),
        retired_tls_ids=("old_a", "old_b", "old_c"),
    )

    report = _write_compiled_source_owner_patch(source, plan, output)
    nodes = {
        node.attrib["id"]: node.attrib
        for node in ET.parse(output).getroot().findall("node")
    }

    assert report["signal_owner_by_controller"] == {
        "HH_2349": ["owner_a", "owner_b"]
    }
    assert nodes["owner_a"] == {
        "id": "owner_a",
        "type": "traffic_light",
        "tl": "HH_2349",
    }
    assert nodes["owner_b"] == {
        "id": "owner_b",
        "type": "traffic_light",
        "tl": "HH_2349",
    }
    assert nodes["owner_c"] == {"id": "owner_c", "type": "priority"}
    assert "HH_2349" not in nodes


def test_owner_patch_rejects_a_control_link_that_spans_two_physical_owners(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    _write_owner_net(source)
    plan = OfficialTlsPlan(
        plan_id="invalid",
        version="1",
        groups=(
            OfficialTlsGroup(
                "2349",
                "K1",
                "HH_2349",
                0,
                (PhysicalControlledLink("west_in", 0, "east_out", 0),),
            ),
        ),
    )

    with pytest.raises(HamburgCompoundOfficialTlsError, match="single junction owner"):
        _write_compiled_source_owner_patch(source, plan, tmp_path / "invalid.nod.xml")


def _write_compound_topology(path: Path) -> None:
    origins = {
        "cluster_25737304_759714733": "25737304 759714733",
        "cluster_739654528_759714704": "739654528 759714704",
        "cluster_2761334279_757036795": "2761334279 757036795",
        "cluster_3847369287_757036909_76463166": (
            "3847369287 757036909 76463166"
        ),
    }
    passive = (
        "3847369285",
        "3847369288",
        "759714726",
        "cluster_2761334249_610506352_cluster_739654528_759714704",
    )
    root = ET.Element("net")
    for junction_id, origin_ids in origins.items():
        junction = ET.SubElement(root, "junction", {"id": junction_id})
        ET.SubElement(
            junction,
            "param",
            {"key": "origId", "value": origin_ids},
        )
    for junction_id in passive:
        ET.SubElement(root, "junction", {"id": junction_id})
    ET.indent(root, space="    ")
    path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def _write_join_evidence(path: Path, *, conflicting: bool = False) -> None:
    groups = [
        "25737304 759714733",
        "739654528 759714704",
        "3847369287 757036909 76463166",
        "2761334279 757036795",
    ]
    if conflicting:
        groups.append("25737304 739654528")
    root = ET.Element("nodes")
    for nodes in groups:
        ET.SubElement(root, "join", {"nodes": nodes})
    path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def test_compound_topology_gate_requires_exact_local_join_groups(tmp_path: Path) -> None:
    net = tmp_path / "candidate.net.xml"
    join = tmp_path / "candidate.nod.xml"
    _write_compound_topology(net)
    _write_join_evidence(join)

    report = _validate_compound_topology(net, join)

    assert report["status"] == "pass"
    assert report["2349_conflict_core_owner_count"] == 2
    assert report["2394_owner_component_count"] == 5


def test_compound_topology_gate_rejects_an_all_site_or_cross_pair_join(
    tmp_path: Path,
) -> None:
    net = tmp_path / "candidate.net.xml"
    join = tmp_path / "candidate.nod.xml"
    _write_compound_topology(net)
    _write_join_evidence(join, conflicting=True)

    with pytest.raises(HamburgCompoundOfficialTlsError, match="conflicting_groups"):
        _validate_compound_topology(net, join)


def test_cli_reads_the_frozen_official_bundle_and_forwards_hashes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _load_script()
    rows = [
        {"node_id": node, "kind": kind, "sha256": char * 64}
        for node, char in (("2349", "a"), ("2394", "b"))
        for kind in ("map_xml", "map_kml", "ocit_xml")
    ]
    manifest = tmp_path / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-official-signal-asset-bundle/v1",
                "assets": rows,
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_materializer(**kwargs):
        captured.update(kwargs)
        return {"status": "topology_ready"}

    monkeypatch.setattr(
        script,
        "materialize_hamburg_compound_official_tls_candidate",
        fake_materializer,
    )
    result = script.main(
        [
            "--source-net",
            "corridor.net.xml",
            "--join-evidence",
            "joined.nod.xml",
            "--signal-asset-dir",
            "assets",
            "--asset-manifest",
            str(manifest),
            "--output-dir",
            str(tmp_path / "output"),
            "--expected-source-sha256",
            "c" * 64,
            "--expected-join-evidence-sha256",
            "d" * 64,
        ]
    )

    assert result == 0
    assert captured["expected_source_sha256"] == "c" * 64
    assert captured["expected_join_evidence_sha256"] == "d" * 64
    assert captured["expected_asset_sha256"] == {
        "2349_map_xml": "a" * 64,
        "2349_map_kml": "a" * 64,
        "2349_ocit_xml": "a" * 64,
        "2394_map_xml": "b" * 64,
        "2394_map_kml": "b" * 64,
        "2394_ocit_xml": "b" * 64,
    }


def test_cli_forwards_named_scope_manifest_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script = _load_script()
    rows = [
        {"node_id": node, "kind": kind, "sha256": char * 64}
        for node, char in (("2349", "a"), ("2394", "b"))
        for kind in ("map_xml", "map_kml", "ocit_xml")
    ]
    manifest = tmp_path / "assets.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-official-signal-asset-bundle/v1",
                "assets": rows,
            }
        ),
        encoding="utf-8",
    )
    named_scope = tmp_path / "named-scope.json"
    named_scope.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_materializer(**kwargs):
        captured.update(kwargs)
        return {"status": "topology_ready"}

    monkeypatch.setattr(
        script,
        "materialize_hamburg_compound_official_tls_candidate",
        fake_materializer,
    )
    result = script.main(
        [
            "--source-net", "corridor.net.xml",
            "--join-evidence", "joined.nod.xml",
            "--signal-asset-dir", "assets",
            "--asset-manifest", str(manifest),
            "--named-scope-manifest", str(named_scope),
            "--output-dir", str(tmp_path / "output"),
            "--expected-source-sha256", "c" * 64,
            "--expected-join-evidence-sha256", "d" * 64,
        ]
    )

    assert result == 0
    assert captured["named_scope_manifest_file"] == named_scope


def test_named_scope_signal_gate_blocks_before_candidate_directory_creation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.net.xml"
    join = tmp_path / "join.nod.xml"
    assets = tmp_path / "assets"
    named_scope = tmp_path / "named-scope.json"
    output = tmp_path / "candidate"
    source.write_text("<net/>\n", encoding="utf-8")
    join.write_text("<nodes/>\n", encoding="utf-8")
    assets.mkdir()
    named_scope.write_text(
        json.dumps(
            {
                "schema": "torii.hamburg-named-corridor-scope/v1",
                "scope_id": "hamburg_sandtorkai_2349_2394_2403_named_entries_v1",
                "nodes": [{"node_id": node_id} for node_id in ("2349", "2394", "2403")],
                "signal_assets": {"decision": "blocked"},
                "official_road_scope": {
                    "scope_id": "hamburg_sandtorkai_2349_2394_2403_named_entries_v1"
                },
                "sources": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        HamburgCompoundOfficialTlsError,
        match="named corridor signal-stage gate blocked",
    ):
        materialize_hamburg_compound_official_tls_candidate(
            source_net_file=source,
            join_evidence_file=join,
            signal_asset_dir=assets,
            output_dir=output,
            expected_source_sha256="0" * 64,
            expected_join_evidence_sha256="0" * 64,
            expected_asset_sha256={},
            named_scope_manifest_file=named_scope,
        )
    assert not output.exists()
