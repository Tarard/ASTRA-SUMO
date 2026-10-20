from copy import deepcopy
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256, validate_materialization_evidence
from astra_sumo.core.command_runner import run_command
from astra_sumo.core.corridor_edit_ledger import _bound_scoped_edit_plan, propose_corridor_edits
from astra_sumo.core.network_edit_applicability import verify_network_edit


def _source(folder, operation):
    nodes = ET.Element("nodes")
    points = {"ra": (1000, 0), "rb": (1012, 0), "rc": (1024, 0)}
    points.update(
        {"a": (0, 0), "b": (50, 0)}
        if operation == "delete_edge"
        else {"west": (-0.2, 0), "micro": (0, 0), "east": (20, 0)}
    )
    for name, (x, y) in points.items():
        ET.SubElement(nodes, "node", id=name, x=str(x), y=str(y), type="priority")
    edges = ET.Element("edges")
    records = [("r0", "ra", "rb"), ("r1", "rb", "ra"), ("r2", "rb", "rc"), ("r3", "rc", "rb")]
    records += (
        [("123", "a", "b")]
        if operation == "delete_edge"
        else [("f0", "west", "micro"), ("f1", "micro", "east"), ("b0", "east", "micro"), ("b1", "micro", "west")]
    )
    for identity, start, end in records:
        ET.SubElement(
            edges,
            "edge",
            id=identity,
            attrib={"from": start, "to": end, "allow": "passenger", "name": "Ring", "speed": "10"},
        )
    for name, root in (("nodes", nodes), ("edges", edges)):
        ET.ElementTree(root).write(folder / f"{name}.xml", encoding="utf-8")
    source = folder / "source.net.xml"
    command = [
        shutil.which("netconvert"),
        "-n",
        str(folder / "nodes.xml"),
        "-e",
        str(folder / "edges.xml"),
        "--offset.disable-normalization",
        "true",
        "-o",
        str(source),
    ]
    if operation == "merge_edges":
        connections = folder / "connections.xml"
        connections.write_text(
            '<connections><connection from="f0" to="f1" fromLane="0" toLane="0"/>'
            '<connection from="b0" to="b1" fromLane="0" toLane="0"/></connections>',
            encoding="utf-8",
        )
        command.extend(["-x", str(connections)])
    result = run_command(command, cwd=folder, timeout_seconds=30)
    assert result.returncode == 0, result
    return source


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
@pytest.mark.parametrize("kind", ["delete_edge", "merge_edges"])
def test_legacy_native_lists_bind_scope_and_preserve_inherited_topology(tmp_path, kind):
    source = _source(tmp_path, kind)
    digest = file_sha256(source)
    (proposal,) = propose_corridor_edits(
        source,
        include_isolated=kind == "delete_edge",
        include_duplicates=False,
        include_micro_merges=kind == "merge_edges",
    )
    assert proposal["operation"] == kind
    operation = {
        **proposal,
        "status": "accepted",
        "evidence": [*proposal["evidence"], dict(path=str(source), sha256=digest)],
    }
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(dict(source_net_file=str(source), source_sha256=digest, operations=[operation])), encoding="utf-8"
    )
    report = verify_network_edit(
        ledger_file=ledger,
        output_dir=tmp_path / "release",
        mode="release",
        netconvert_binary=shutil.which("netconvert"),
    )
    built = report["materialization"]
    assert built["status"] == "pass", built
    topology = report["candidate_gates"]["gates"]["topology"]
    assert topology["inherited_finding_count"] == 1, topology
    assert report["whole_network_review"]["status"] == "review_required"
    if kind == "delete_edge":
        assert report["status"] == "pass", report
        assert not topology["scope_findings"]
    else:
        assert report["status"] == "review_required", report
        assert topology["scope_findings"]
    evidence = validate_materialization_evidence(
        built, source_net_file=source, candidate_net_file=Path(built["candidate_net_file"])
    )
    scope = _bound_scoped_edit_plan(built, evidence, source, ET.parse(source).getroot())
    assert scope["scope_binding"]["basis"] == "manifest_bound_native_edge_list"
    assert not {"ra", "rb", "rc"} & set(scope["mutable_junction_ids"])
    changed_report = deepcopy(built)
    changed_report.update(operation_family="untrusted", target_edge_ids=["r0"])
    assert _bound_scoped_edit_plan(changed_report, evidence, source, ET.parse(source).getroot()) == scope
    compound = deepcopy(built)
    extra = "--geometry.remove.keep-edges.input-file" if kind == "delete_edge" else "--remove-edges.input-file"
    compound["command"].extend([extra, str(tmp_path / "other-native-input.txt")])
    assert _bound_scoped_edit_plan(compound, evidence, source, ET.parse(source).getroot()) == {}
    Path(built["plan_file"]).write_text("r0\n", encoding="utf-8")
    assert _bound_scoped_edit_plan(built, evidence, source, ET.parse(source).getroot()) == {}
    assert file_sha256(source) == digest
