import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.command_runner import run_command
from astra_sumo.core.corridor_edit_ledger import propose_corridor_edits
from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant
from astra_sumo.core.candidate_contracts import file_sha256


def _compile(folder, nodes, edges, connections="<connections/>"):
    folder.mkdir(exist_ok=True)
    for name, text in (("nodes", nodes), ("edges", edges), ("connections", connections)):
        (folder / f"{name}.xml").write_text(text, encoding="utf-8")
    source = folder / "source.net.xml"
    result = run_command(
        [
            shutil.which("netconvert"),
            "-n",
            str(folder / "nodes.xml"),
            "-e",
            str(folder / "edges.xml"),
            "-x",
            str(folder / "connections.xml"),
            "--offset.disable-normalization",
            "true",
            "-o",
            str(source),
        ],
        cwd=folder,
        timeout_seconds=30,
    )
    assert result.returncode == 0, result
    return source


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
@pytest.mark.parametrize("connected,parking", [(False, False), (True, False), (False, True)])
def test_native_isolated_proposals_keep_connected_and_parking_roads(tmp_path, connected, parking):
    nodes = '<nodes><node id="a" x="0" y="0"/><node id="b" x="50" y="0"/><node id="c" x="100" y="0"/></nodes>'
    annotation = '<param key="astra.parking.facility" value="garage"/>' if parking else ""
    continuation = '<edge id="two" from="b" to="c" allow="passenger"/>' if connected else ""
    edges = f'<edges><edge id="one" from="a" to="b" allow="passenger">{annotation}</edge>{continuation}</edges>'
    source = _compile(tmp_path, nodes, edges)
    before = source.read_bytes()
    proposals = propose_corridor_edits(source, include_duplicates=False, include_micro_merges=False)
    assert [row["id"] for row in proposals] == ([] if connected or parking else ["delete-isolated-one"])
    assert source.read_bytes() == before


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
@pytest.mark.parametrize("variant", ["no_reference", "different_name", "reference_present", "reference_absent"])
def test_native_micro_proposals_record_reference_absence_without_inventing_it(tmp_path, variant):
    nodes = '<nodes><node id="west" x="-0.2" y="0"/><node id="micro" x="0" y="0" type="priority"/>'
    nodes += '<node id="east" x="20" y="0"/></nodes>'
    name = "Other Road" if variant == "different_name" else "Ringstrasse"
    edges = (
        "<edges>"
        + "".join(
            f'<edge id="{identity}" from="{start}" to="{end}" name="{road_name}" allow="passenger" speed="10"/>'
            for identity, start, end, road_name in [
                ("f0", "west", "micro", "Ringstrasse"),
                ("f1", "micro", "east", name),
                ("r0", "east", "micro", name),
                ("r1", "micro", "west", "Ringstrasse"),
            ]
        )
        + "</edges>"
    )
    connections = '<connections><connection from="f0" to="f1" fromLane="0" toLane="0"/>'
    connections += '<connection from="r0" to="r1" fromLane="0" toLane="0"/></connections>'
    source = _compile(tmp_path / "source", nodes, edges, connections)
    reference = source if variant == "reference_present" else None
    if variant == "reference_absent":
        reference = _compile(
            tmp_path / "reference",
            '<nodes><node id="west" x="-0.2" y="0"/><node id="east" x="20" y="0"/></nodes>',
            '<edges><edge id="forward" from="west" to="east" allow="passenger"/>'
            '<edge id="reverse" from="east" to="west" allow="passenger"/></edges>',
        )
    before = source.read_bytes()
    proposals = propose_corridor_edits(
        source, reference_net_file=reference, include_isolated=False, include_duplicates=False
    )
    assert len(proposals) == (1 if variant in {"no_reference", "reference_absent"} else 0), proposals
    if proposals:
        assert proposals[0]["operation"] == "merge_edges"
        assert proposals[0]["evidence"][0]["reference_node_absent"] == (True if variant == "reference_absent" else None)
        assert "reference-absent" not in proposals[0]["rationale"]
    if variant == "no_reference":
        digest = file_sha256(source)
        operation = {
            **proposals[0],
            "status": "accepted",
            "evidence": [*proposals[0]["evidence"], {"path": str(source), "sha256": digest}],
        }
        report = materialize_corridor_edit_variant(
            output_dir=tmp_path / "materialized",
            ledger={"source_net_file": str(source), "source_sha256": digest, "operations": [operation]},
            netconvert_binary=shutil.which("netconvert"),
        )
        assert report["status"] == "pass", report
        with open(report["plan_file"], encoding="utf-8") as stream:
            assert stream.read() == "\n"
        candidate = ET.parse(report["candidate_net_file"]).getroot()
        assert "micro" not in {row.get("id") for row in candidate.findall("junction")}
        assert len([row for row in candidate.findall("edge") if not row.get("id").startswith(":")]) == 2
    assert source.read_bytes() == before
