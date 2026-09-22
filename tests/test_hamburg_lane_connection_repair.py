import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo import cli
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_lane_connection_repair import (
    build_hamburg_lane_connection_repair,
    write_hamburg_lane_connection_patch,
    write_repaired_lsa119_tllogic,
)


def test_lsa119_repair_keeps_one_to_one_links_and_remaps_signal_states(tmp_path) -> None:
    source = tmp_path / "source.tll.xml"
    source.write_text(
        """<tlLogics>
        <tlLogic id="LSA119_part0" type="static" programID="0" offset="0">
            <phase duration="42" state="abcdefghijklmnopqrst"/>
        </tlLogic>
        <connection from="603103445#0" to="141050975" fromLane="0" toLane="0" tl="LSA119_part0" linkIndex="2"/>
        <connection from="603103445#0" to="141050975" fromLane="0" toLane="1" tl="LSA119_part0" linkIndex="3"/>
        <connection from="other" to="edge" fromLane="0" toLane="0" tl="other" linkIndex="0"/>
        </tlLogics>""",
        encoding="utf-8",
    )
    output = tmp_path / "repaired.tll.xml"

    write_repaired_lsa119_tllogic(source, output)

    root = ET.parse(output).getroot()
    phase = root.find("tlLogic[@id='LSA119_part0']/phase")
    assert phase is not None
    assert phase.attrib["state"] == "abchijklopr"
    links = root.findall("connection[@tl='LSA119_part0']")
    assert len(links) == 11
    assert {(row.attrib["from"], row.attrib["fromLane"], row.attrib["to"], row.attrib["toLane"]) for row in links} >= {
        ("603103445#0", "0", "141050975", "0"),
        ("24483344#0", "0", "141050975", "2"),
        ("24483344#0", "1", "141050975", "3"),
        ("1068722010", "0", "363112756#1", "0"),
    }
    assert root.find("connection[@tl='other']") is not None


def test_connection_patch_removes_fanout_and_adds_single_missing_link(tmp_path) -> None:
    output = tmp_path / "repair.con.xml"

    write_hamburg_lane_connection_patch(output)

    root = ET.parse(output).getroot()
    assert len(root.findall("delete")) == 10
    additions = root.findall("connection")
    assert len(additions) == 1
    assert additions[0].attrib == {
        "from": "24483344#0",
        "to": "141050975",
        "fromLane": "0",
        "toLane": "2",
    }


def test_cli_routes_hamburg_lane_connection_repair(monkeypatch, tmp_path, capsys) -> None:
    source = tmp_path / "source.net.xml"
    source.write_text("<net/>", encoding="utf-8")
    output = tmp_path / "repair"

    def fake_build(**kwargs):
        assert kwargs["source_net"] == str(source)
        assert kwargs["output_dir"] == str(output)
        return {"status": "review_required", "candidate_network": "candidate.net.xml"}

    monkeypatch.setattr(
        cli,
        "build_hamburg_lane_connection_repair",
        fake_build,
        raising=False,
    )

    exit_code = cli.main(
        [
            "hamburg",
            "repair-lane-connections",
            str(source),
            str(output),
            "--json",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "review_required"


@pytest.mark.parametrize("invalid", ["hash", "lane", "junction"])
def test_explicit_connection_patch_checks_inputs_before_running(tmp_path, invalid):
    source = tmp_path / "source.net.xml"
    source.write_text(
        '<net><edge id="a" from="w" to="j"><lane index="0"/></edge>'
        '<edge id="b" from="j" to="e"><lane index="0"/></edge></net>',
        encoding="utf-8",
    )
    if invalid == "junction":
        source.write_text(source.read_text().replace('from="j"', 'from="other"'), encoding="utf-8")
    patch = tmp_path / "patch.con.xml"
    patch.write_text(
        '<connections><connection from="a" to="b" fromLane="0" toLane="'
        + ("3" if invalid == "lane" else "0") + '"/></connections>',
        encoding="utf-8",
    )

    def do_not_run(*args, **kwargs):
        pytest.fail("Invalid patch must be rejected before netconvert")

    with pytest.raises(ValueError, match={"hash": "SHA-256", "lane": "lane", "junction": "junction"}[invalid]):
        build_hamburg_lane_connection_repair(
            source_net=source,
            output_dir=tmp_path / "candidate",
            connection_patch_file=patch,
            expected_connection_patch_sha256="0" * 64 if invalid == "hash" else file_sha256(patch),
            command_runner=do_not_run,
        )
    assert not (tmp_path / "candidate").exists()


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO binaries are required")
def test_explicit_patch_rebuilds_real_internal_paths_without_hamburg_ids(tmp_path):
    nodes = tmp_path / "nodes.nod.xml"
    nodes.write_text('<nodes><node id="w" x="0" y="0"/><node id="j" x="100" y="0"/><node id="e" x="200" y="0"/></nodes>', encoding="utf-8")
    edges = tmp_path / "edges.edg.xml"
    edges.write_text('<edges><edge id="a" from="w" to="j" numLanes="2"/><edge id="b" from="j" to="e" numLanes="2"/></edges>', encoding="utf-8")
    connections = tmp_path / "source.con.xml"
    connections.write_text('<connections><connection from="a" to="b" fromLane="0" toLane="0"/></connections>', encoding="utf-8")
    source = tmp_path / "source.net.xml"
    subprocess.run([shutil.which("netconvert"), "--node-files", str(nodes), "--edge-files", str(edges), "--connection-files", str(connections), "--output-file", str(source)], check=True, capture_output=True, timeout=30)
    source_hash = file_sha256(source)
    patch = tmp_path / "repair.con.xml"
    patch.write_text('<connections><connection from="a" to="b" fromLane="1" toLane="1"/></connections>', encoding="utf-8")

    result = build_hamburg_lane_connection_repair(
        source_net=source, output_dir=tmp_path / "candidate",
        connection_patch_file=patch, expected_connection_patch_sha256=file_sha256(patch),
    )

    assert result["status"] == "review_required"
    assert set(result["gates"].values()) == {"pass"}
    assert file_sha256(source) == source_hash
    audit = json.loads(Path(result["artifacts"]["connection_audit"]).read_text(encoding="utf-8"))
    assert audit["verified_internal_path_count"] == audit["direct_movement_count"] == 2
