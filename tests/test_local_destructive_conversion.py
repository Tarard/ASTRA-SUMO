from copy import deepcopy
import gzip
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.local_network_conversion import (
    local_conversion_input,
    merge_local_conversion,
    legacy_edge_edit_scope,
)


def _source():
    return ET.fromstring("""<net version="1.20"><location netOffset="0,0" projParameter="!"/>
      <edge id="a" from="w" to="m"><lane id="a_0" index="0" allow="bus" length="5" shape="0,0 5,0"/></edge>
      <edge id="b" from="m" to="e"><lane id="b_0" index="0" allow="bus" length="5" shape="5,0 10,0"/></edge>
      <edge id="far" from="x" to="y"><lane id="far_0" index="0" allow="bicycle" length="10" shape="100,0 110,0"/></edge>
      <junction id="w" x="0" y="0"/><junction id="m" x="5" y="0"/><junction id="e" x="10" y="0"/>
      <junction id="x" x="100" y="0"/><junction id="y" x="110" y="0"/>
      <connection from="a" to="b" fromLane="0" toLane="0" dir="s"/></net>""")


def test_local_merge_uses_existing_source_path_aliases_and_retains_the_exterior():
    source = _source()
    original = ET.tostring(source)
    native, record = local_conversion_input(source, {"w", "m", "e"}, {"a", "b"}, merge_edge_ids={"a", "b"})
    native.remove(native.find("edge[@id='b']"))
    native.remove(native.find("connection[@from='a']"))
    native.remove(native.find("junction[@id='m']"))
    merged = native.find("edge[@id='a']")
    merged.set("to", "e")
    ET.SubElement(merged, "param", key="removedNodeIds", value="m")
    result = merge_local_conversion(source, native, record)
    assert {row.get("id") for row in result.findall("edge")} == {"a", "far"}
    assert result.find("edge[@id='a']").get("to") == "e"
    assert ET.tostring(result.find("edge[@id='far']")) == ET.tostring(source.find("edge[@id='far']"))
    assert ET.tostring(source) == original
    wrong = deepcopy(record)
    wrong["merge_edge_ids"] = ["a"]
    with pytest.raises(ValueError, match="undeclared road"):
        merge_local_conversion(source, native, wrong)


def test_local_delete_rejects_loss_of_an_unrequested_retained_road():
    source = _source()
    native, record = local_conversion_input(source, {"w", "m"}, {"a", "b"}, removed_edge_ids={"a"})
    native.remove(native.find("edge[@id='a']"))
    native.remove(native.find("connection[@from='a']"))
    result = merge_local_conversion(source, native, record)
    assert {row.get("id") for row in result.findall("edge")} == {"b", "far"}
    native.remove(native.find("edge[@id='b']"))
    with pytest.raises(ValueError, match="inventory"):
        merge_local_conversion(source, native, record)


def test_isolated_deletion_adds_only_one_retained_native_context_road():
    source = _source()
    native, record = local_conversion_input(source, {"x", "y"}, {"far"}, removed_edge_ids={"far"})
    assert record["context_edge_ids"] == ["a", "far"]
    assert record["context_anchor_edge_ids"] == ["a"]
    native.remove(native.find("edge[@id='far']"))
    result = merge_local_conversion(source, native, record)
    assert {row.get("id") for row in result.findall("edge")} == {"a", "b"}


def test_a_geometry_merge_must_actually_merge_the_declared_roads():
    source = _source()
    native, record = local_conversion_input(source, {"w", "m", "e"}, {"a", "b"}, merge_edge_ids={"a", "b"})
    with pytest.raises(ValueError, match="not fully realized"):
        merge_local_conversion(source, native, record)


@pytest.mark.parametrize(
    "flags",
    [["--geometry.remove", "false"], ["--geometry.remove=false"], ["--geometry.remove", "--geometry.remove=false"]],
)
def test_saved_merge_commands_cannot_disable_geometry_removal(tmp_path, flags):
    keep = tmp_path / "keep.txt"
    keep.write_text("far\n", encoding="utf-8")
    with pytest.raises(ValueError, match="enabled"):
        legacy_edge_edit_scope(
            _source(), "merge_edges", ["netconvert", *flags, "--geometry.remove.keep-edges.input-file", str(keep)]
        )


@pytest.mark.skipif(not shutil.which("netconvert"), reason="SUMO programs required")
@pytest.mark.parametrize("full_input", [False, True])
def test_hamburg_branching_pair_does_not_report_a_successful_merge(tmp_path, monkeypatch, full_input):
    from astra_sumo.core.candidate_contracts import file_sha256
    from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant

    fixture = Path(__file__).parents[1] / "benchmarks/hamburg_loaded_links_20261018_v1/candidate.net.xml.gz"
    source = tmp_path / "hamburg.net.xml"
    source.write_bytes(gzip.decompress(fixture.read_bytes()))
    original = source.read_bytes()
    if full_input:
        monkeypatch.setattr(
            "astra_sumo.core.local_network_conversion.local_conversion_input",
            lambda *args, **kwargs: (None, {"status": "full_network", "reason": "test_full_input"}),
        )
    operation = {
        "id": "branching-merge",
        "operation": "merge_edges",
        "status": "accepted",
        "target_ids": ["83075269#1", "83075269#2"],
        "rationale": "Controlled no-merge regression.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}],
        "rollback": {"action": "discard_candidate"},
    }
    report = materialize_corridor_edit_variant(
        output_dir=tmp_path / "edit",
        ledger={"source_net_file": str(source), "source_sha256": file_sha256(source), "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["command_result"]["returncode"] == 0, report
    assert report["status"] == "blocked"
    assert "not fully realized" in report["semantic_validation"]["error"]
    if not full_input:
        assert report["candidate_variant_status"] == "failed_local_subset"
        assert report["semantic_validation"]["local_conversion"]["merge_status"] == "failed"
        assert not (tmp_path / "edit/corridor_materialized_variant.native-proposal.net.xml").exists()
    assert source.read_bytes() == original


def test_destructive_scope_keeps_a_prohibition_fallback_explicit():
    source = _source()
    ET.SubElement(source, "prohibition", prohibitor="a->b", prohibited="a->b")
    subset, record = local_conversion_input(source, {"w", "m"}, {"a", "b"}, removed_edge_ids={"a"})
    assert subset is None and record == {"status": "full_network", "reason": "prohibition_references_destructive_edit"}


def test_deleting_every_road_does_not_invent_a_retained_native_anchor():
    subset, record = local_conversion_input(
        _source(), {"w", "m", "e", "x", "y"}, {"a", "b", "far"}, removed_edge_ids={"a", "b", "far"}
    )
    assert subset is None and record == {"status": "full_network", "reason": "no_retained_ordinary_context_road"}


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
def test_native_spur_deletion_preserves_loaded_bus_only_through_access(tmp_path):
    from astra_sumo.core.candidate_contracts import file_sha256, validate_materialization_evidence
    from astra_sumo.core.command_runner import run_command
    from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant
    from astra_sumo.core.local_network_conversion import loaded_turn_permissions
    from astra_sumo.core.sumo_commands import run_sumo_load_audit

    nodes, edges, source = (tmp_path / name for name in ("nodes.xml", "edges.xml", "source.net.xml"))
    nodes.write_text(
        '<nodes><node id="w" x="0" y="0"/><node id="m" x="50" y="0"/>'
        '<node id="e" x="100" y="0"/><node id="d" x="50" y="50"/>'
        '<node id="x" x="1000" y="0"/><node id="y" x="1100" y="0"/></nodes>',
        encoding="utf-8",
    )
    edges.write_text(
        '<edges><edge id="in" from="w" to="m" allow="passenger bus"/>'
        '<edge id="out" from="m" to="e" allow="passenger bus"/>'
        '<edge id="spur" from="m" to="d" allow="passenger bus"/>'
        '<edge id="far" from="x" to="y" allow="passenger bus"/></edges>',
        encoding="utf-8",
    )
    result = run_command(
        [
            shutil.which("netconvert"),
            "-n",
            str(nodes),
            "-e",
            str(edges),
            "--offset.disable-normalization",
            "true",
            "-o",
            str(source),
        ],
        cwd=tmp_path,
        timeout_seconds=30,
    )
    assert result.returncode == 0, result
    tree = ET.parse(source)
    root = tree.getroot()
    connection = root.find("connection[@from='in'][@to='out']")
    root.find(f"edge/lane[@id='{connection.get('via')}']").set("allow", "bus")
    tree.write(source, encoding="utf-8", xml_declaration=True)
    before = source.read_bytes()
    expected = loaded_turn_permissions(root)[("in", 0, "out", 0)]
    assert expected == {"bus"}
    operation = {
        "id": "remove-spur",
        "operation": "delete_edge",
        "status": "accepted",
        "target_ids": ["spur"],
        "rationale": "Controlled spur deletion.",
        "evidence": [{"path": str(source), "sha256": file_sha256(source)}],
        "rollback": {"action": "discard_candidate"},
    }
    report = materialize_corridor_edit_variant(
        output_dir=tmp_path / "edit",
        ledger={"source_net_file": str(source), "source_sha256": file_sha256(source), "operations": [operation]},
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["status"] == "pass", report
    candidate = Path(report["candidate_net_file"])
    assert report["semantic_validation"]["local_conversion"]["converted_normal_edge_count"] == 3
    assert loaded_turn_permissions(ET.parse(candidate).getroot())[("in", 0, "out", 0)] == expected
    assert (
        validate_materialization_evidence(report, source_net_file=source, candidate_net_file=candidate)["status"]
        == "pass"
    )
    assert (
        run_sumo_load_audit(net_file=candidate, output_dir=tmp_path / "load", sumo_binary=shutil.which("sumo"))[
            "status"
        ]
        == "pass"
    )
    assert source.read_bytes() == before
