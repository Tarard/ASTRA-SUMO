"""M4 control re-index (twin/control_reindex.py): the executed control follows a candidate net's linkIndex layout.

Synthetic nets cover a removed and an added link (with the yield rule), byte preservation and every refusal; the
store-backed checks re-derive the real F1 (index 5 removed at LSA535_part0) and F2 (one link added at 266197536)
controls (skipped when the fragility store is absent). No test starts SUMO.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from astra_sumo.twin import control_reindex as cr

STORE = Path(r"E:\twin-store\fragility")


def _net(links: list[tuple[str, str]], programs: dict[str, list[str]], *, responses: list[str] | None = None,
         extra: str = "") -> ET.Element:
    """One junction J under controller T: ``links[i]`` is link i (from lane, to lane) with via ``:J_<i>_0``."""
    vias = [f":J_{index}_0" for index in range(len(links))]
    size = len(links)
    rows = responses or ["0" * size] * size
    parts = ['<net version="1.20">',
             *[f'<edge id=":J_{index}" function="internal"><lane id="{via}" index="0" length="5"/></edge>'
               for index, via in enumerate(vias)],
             *[f'<tlLogic id="T" type="static" programID="{name}" offset="0">'
               + "".join(f'<phase duration="10" state="{state}"/>' for state in states) + "</tlLogic>"
               for name, states in programs.items()],
             f'<junction id="J" type="traffic_light" intLanes="{" ".join(vias)}">'
             + "".join(f'<request index="{index}" response="{row}" foes="{row}" cont="0"/>'
                       for index, row in enumerate(rows)) + "</junction>",
             *[f'<connection from="{a.rpartition("_")[0]}" to="{b.rpartition("_")[0]}" fromLane="{a.rpartition("_")[2]}" '
               f'toLane="{b.rpartition("_")[2]}" via="{vias[index]}" tl="T" linkIndex="{index}"/>'
               for index, (a, b) in enumerate(links)], extra, "</net>"]
    return ET.fromstring("\n".join(parts))


PARENT_LINKS = [("A_0", "B_0"), ("A_1", "B_1"), ("A_1", "C_0"), ("D_0", "B_0")]
CONTROL = (b'<additional>\n'
           b'    <tlLogic id="T" type="static" programID="readme_fixed_A" offset="0">\n'
           b'        <phase duration="42" state="GGGr" minDur="5" />\n'
           b'        <phase duration="3" state="yyyr" />\n'
           b'        <phase duration="30" state="rrrG" />\n'
           b'    </tlLogic>\n'
           b'    <tlLogic id="U" type="static" programID="readme_fixed_A" offset="0"><param key="k" value="1" />'
           b'<phase duration="9.0" state="GrG" /><phase duration="3.0" state="yry" /></tlLogic>\n'
           b'</additional>\n')
U_PARENT = ('<connection from="E" to="F" fromLane="0" toLane="0" tl="U" linkIndex="0"/>'
            '<connection from="E" to="F" fromLane="1" toLane="1" tl="U" linkIndex="1"/>'
            '<connection from="E" to="G" fromLane="1" toLane="0" tl="U" linkIndex="2"/>')
REMOVED = {"removed_links": [{"pair": ["A_1", "C_0"], "tls_id": "T"}]}


def _removal() -> ET.Element:
    return _net([("A_0", "B_0"), ("A_1", "B_1"), ("D_0", "B_0")], {"0": ["GGr", "rrG"]}, extra=U_PARENT)


def _addition(program: list[str] | None = None) -> ET.Element:
    links = [("A_0", "B_0"), ("A_0", "B_1"), ("A_1", "B_1"), ("A_1", "C_0"), ("D_0", "B_0")]
    responses = ["00000", "00100", "00000", "00000", "00000"]  # the added link 1 yields to link 2 (its sibling)
    return _net(links, {"0": program or ["GgGgr", "rrrrG"]}, responses=responses, extra=U_PARENT)


def _added(rule: str) -> dict:
    return {"added_links": [{"pair": ["A_0", "B_1"], "tls_id": "T", "sibling_pair": ["A_1", "B_1"],
                             "copy_rule": rule}]}


def _states(data: bytes, tl: str) -> list[str]:
    logic = next(row for row in ET.fromstring(data).iter("tlLogic") if row.get("id") == tl)
    return [phase.get("state") for phase in logic.findall("phase")]


def _parent() -> ET.Element:
    return _net(PARENT_LINKS, {"0": ["GGgr", "rrrG"]}, extra=U_PARENT)


def test_a_removed_link_drops_its_column_and_keeps_every_other_byte():
    data, report = cr.reindex_control(CONTROL, _parent(), _removal(), REMOVED)
    assert _states(data, "T") == ["GGr", "yyr", "rrG"] and _states(data, "U") == ["GrG", "yry"]
    plan = report["plans"]["T"]
    assert plan["map"] == {"0": 0, "1": 1, "3": 2} and plan["removed"] == [2] and plan["renumbered"] == 1
    assert plan["embedded_crosscheck"] == [{"program": "0", "retained_equal": True, "added": {}}]
    assert report["changed_controllers"] == ["T"] and report["unchanged_programs"] == 1
    before, after = CONTROL.decode(), data.decode()
    assert re.sub(r'state="[^"]*"', "", before) == re.sub(r'state="[^"]*"', "", after)
    assert before.split('<tlLogic id="U"')[1] == after.split('<tlLogic id="U"')[1]


def test_an_added_link_copies_its_sibling_and_yields_where_the_request_matrix_says_so():
    with pytest.raises(cr.ReindexError, match="verbatim copy gives added link 1 priority green"):
        cr.reindex_control(CONTROL, _parent(), _addition(), _added("verbatim"))
    data, report = cr.reindex_control(CONTROL, _parent(), _addition(), _added("minor_where_yielding"))
    assert _states(data, "T") == ["GgGGr", "yyyyr", "rrrrG"]
    plan = report["plans"]["T"]
    assert plan["map"] == {"0": 0, "1": 2, "2": 3, "3": 4} and plan["added"]["1"]["sibling_old"] == 1
    assert plan["yields"]["1"] == [2]
    assert plan["embedded_crosscheck"][0]["added"] == {"1": {"netconvert": "gr", "sibling": "Gr"}}
    records = report["programs"][0]["added_link_states"]
    assert [(row["sibling_state"], row["written"]) for row in records] == [("G", "g"), ("y", "y"), ("r", "r")]
    # A sibling that is not green where the added link yields is copied verbatim without a refusal.
    quiet = cr.reindex_control(CONTROL.replace(b"GGGr", b"GrGr"), _parent(), _addition(), _added("verbatim"))[0]
    assert _states(quiet, "T")[0] == "GrrGr"


@pytest.mark.parametrize("candidate, declaration, message", [
    (_removal(), {}, "removed links"),
    (_addition(), {}, "added links"),
    (_removal(), {"removed_links": [{"pair": ["A_0", "B_0"], "tls_id": "T"}]}, "removed links"),
    (_addition(), {"added_links": [{"pair": ["A_0", "B_1"], "tls_id": "T", "sibling_pair": ["A_1", "C_9"],
                                    "copy_rule": "verbatim"}]}, "sibling"),
    (_addition(["GgGgr", "rrrrG"]), {"removed_links": [{"pair": ["A_1", "C_0"], "tls_id": "X"}]}, "declared"),
    (_net([("A_0", "B_0"), ("A_1", "B_1"), ("D_0", "B_0")], {"0": ["GrG", "rGr"]}, extra=U_PARENT), REMOVED,
     "not the map netconvert applied"),
])
def test_cases_the_map_cannot_explain_are_refused(candidate, declaration, message):
    with pytest.raises(cr.ReindexError, match=message):
        cr.reindex_control(CONTROL, _parent(), candidate, declaration)


def test_shared_indices_link_index_2_unbound_states_and_unknown_controllers_are_refused():
    shared = _parent()
    ET.SubElement(shared, "connection", {"from": "A", "to": "C", "fromLane": "0", "toLane": "0", "tl": "T",
                                         "linkIndex": "0"})
    with pytest.raises(cr.ReindexError, match="carries two connections"):
        cr.link_layout(shared)
    second = _parent()
    second.findall("connection")[0].set("linkIndex2", "7")
    with pytest.raises(cr.ReindexError, match="linkIndex2"):
        cr.link_layout(second)
    with pytest.raises(cr.ReindexError, match="5 states for 4 links"):
        cr.reindex_control(CONTROL.replace(b'"GGGr"', b'"GGGrr"'), _parent(), _removal(), REMOVED)
    with pytest.raises(cr.ReindexError, match="controllers the nets do not have"):
        cr.reindex_control(CONTROL.replace(b'id="U"', b'id="V"'), _parent(), _removal(), REMOVED)


def _signal_entry(pair: tuple[str, str], reason: str, old: int | None, new: int | None) -> dict:
    return {"fact": f"pair:{pair[0]}>{pair[1]}:signal_group", "kind": "signal_group", "reason": reason,
            "parent_expected": None if old is None else {"tls_id": "T", "link_index": old},
            "expected": None if new is None else {"tls_id": "T", "link_index": new}}


def test_declared_signal_targets_must_equal_the_derived_link_map():
    removed = [_signal_entry(("A_1", "C_0"), "link_removed", 2, None),
               _signal_entry(("D_0", "B_0"), "link_renumbered", 3, 2)]
    _, report = cr.reindex_control(CONTROL, _parent(), _removal(), REMOVED, entries=removed)
    assert report["declared_crosscheck"]["controllers"]["T"] == {"renumbered": 1, "removed": [2], "added": [],
                                                                  "equal": True}
    for entries, message in (
            (removed[:1], "not the link map netconvert applied"),  # a renumbered link left undeclared
            ([removed[0], _signal_entry(("D_0", "B_0"), "link_renumbered", 3, 1)], "not the link map"),
            ([*removed, {**_signal_entry(("E_0", "F_0"), "link_renumbered", 0, 1),
                         "parent_expected": {"tls_id": "U", "link_index": 0},
                         "expected": {"tls_id": "U", "link_index": 1}}], "not the link map"),
            ([*removed, {**removed[1], "reason": "binding_changed"}], "no link-map meaning")):
        with pytest.raises(cr.ReindexError, match=message):
            cr.reindex_control(CONTROL, _parent(), _removal(), REMOVED, entries=entries)
    added = [_signal_entry(("A_0", "B_1"), "link_added", None, 1),
             *[_signal_entry(pair, "link_renumbered", old, old + 1)
               for old, pair in enumerate(PARENT_LINKS) if old >= 1]]
    _, report = cr.reindex_control(CONTROL, _parent(), _addition(), _added("minor_where_yielding"), entries=added)
    assert report["declared_crosscheck"]["controllers"]["T"]["added"] == [1]
    assert "declared_crosscheck" not in cr.reindex_control(CONTROL, _parent(), _removal(), REMOVED)[1]


def test_a_load_check_copies_its_additional_files_and_refuses_outputs_that_leave_them(tmp_path):
    loops = tmp_path / "inputs" / "source-e1.add.xml"
    loops.parent.mkdir()
    loops.write_bytes(b'<additional><e1Detector id="d" lane="A_0" pos="1" period="60" file="field-counts.xml"/>'
                      b'</additional>')
    work = tmp_path / "load"
    work.mkdir()
    staged = cr.stage_load_files(work, [loops])
    assert staged == [work / "source-e1.add.xml"] and staged[0].read_bytes() == loops.read_bytes()
    assert cr.contained_outputs(loops.read_bytes(), loops.name) == ["field-counts.xml"]
    for value in (r"E:\twin-store\m2-pilot\runs\x\native\field-counts.xml", "/tmp/field-counts.xml",
                  "../field-counts.xml", r"..\native\field-counts.xml"):
        other = tmp_path / "inputs" / "e1.add.xml"
        other.write_bytes(f'<additional><inductionLoop id="d" lane="A_0" pos="1" file="{value}"/></additional>'
                          .encode("utf-8"))
        with pytest.raises(cr.ReindexError, match="absolute or leaves its directory"):
            cr.stage_load_files(tmp_path / "load2", [other])
    assert not (tmp_path / "inputs" / "field-counts.xml").exists() and not (tmp_path / "load2").exists()


def test_sumo_load_runs_on_the_staged_copies(tmp_path, monkeypatch):
    from astra_sumo.core import command_runner

    calls = []

    class Result:
        status, returncode, stdout, stderr = "pass", 0, "Warning: w1\n", ""

    def fake(command, cwd, timeout_seconds):
        calls.append((command, Path(cwd)))
        return Result()

    monkeypatch.setattr(command_runner, "run_command", fake)
    loops = tmp_path / "inputs" / "source-e1.add.xml"
    loops.parent.mkdir()
    loops.write_bytes(b'<additional><e1Detector id="d" lane="A_0" pos="1" file="field-counts.xml"/></additional>')
    net = tmp_path / "net.net.xml"
    net.write_bytes(ET.tostring(_parent()))
    result = cr.sumo_load(net, [loops], sumo_binary="sumo", cwd=tmp_path / "load")
    command, cwd = calls[0]
    assert command[command.index("--additional-files") + 1] == "source-e1.add.xml" and cwd == tmp_path / "load"
    assert (tmp_path / "load" / "source-e1.add.xml").is_file()
    assert result["status"] == "pass" and result["outputs_in_load_directory"] == ["field-counts.xml"]


def test_route_continuity_and_load_comparison():
    net = ET.tostring(_removal())
    demand = (b'<routes><vehicle id="a" depart="0"><route edges="A B"/></vehicle>'
              b'<vehicle id="b" depart="1"><route edges="A C"/></vehicle></routes>')
    result = cr.route_continuity(net, demand)
    assert result["routes"] == 2 and result["broken"] == 1 and result["examples"][0]["gaps"] == ["A>C"]
    compared = cr.load_comparison({"status": "pass", "warnings": ["w1", "w2"], "errors": []},
                                  {"status": "pass", "warnings": ["w2", "w3"], "errors": []})
    assert compared["new_warnings"] == ["w3"] and compared["gone_warnings"] == ["w1"]


# ---- store-backed: the real F1, F2 and F3 candidates ---------------------------------------------------------------

def _candidate(name: str) -> tuple[ET.Element, bytes, dict]:
    folder = STORE / "candidates" / name
    manifest = json.loads((folder / "candidate.json").read_text(encoding="utf-8"))
    goal = json.loads(Path(manifest["goal"]["path"]).read_text(encoding="utf-8"))
    return (ET.parse(folder / "network.net.xml").getroot(), (folder / "control.add.xml").read_bytes(),
            goal["reference"]["declared_targets"]["control"], goal["reference"]["declared_targets"]["entries"])


@pytest.mark.skipif(not (STORE / "candidates" / "F3" / "candidate.json").is_file(),
                    reason="the fragility store E:\\twin-store\\fragility has no candidates")
def test_the_real_f1_f2_and_f3_controls_re_derive():
    parent = ET.parse(STORE / "inputs/6c52382e7664eca33fceff6f/network.net.xml").getroot()
    control = (STORE / "inputs/27220b430aa68c24f0ad360c/control.add.xml").read_bytes()
    nets, controls = {}, {}
    for name in ("F1", "F2", "F3"):
        net, stored, declaration, entries = _candidate(name)
        data, report = cr.reindex_control(control, parent, net, declaration, entries=entries)
        assert data == stored, name
        assert all(row["equal"] for row in report["declared_crosscheck"]["controllers"].values()), name
        nets[name], controls[name] = net, data
        plans = report["plans"]
        if name in ("F1", "F3"):
            lsa = plans["LSA535_part0"]
            assert lsa["removed"] == [5] and lsa["renumbered"] == 22 and (lsa["n_parent"], lsa["n_candidate"]) == (28, 27)
            assert _states(data, "LSA535_part0")[0] == "OOrrGOggrrrrrrOgggggOOrrrrr"
        if name in ("F2", "F3"):
            ring = plans["266197536"]
            assert ring["added"]["2"]["sibling_old"] == 1 and ring["renumbered"] == 6
            assert ring["embedded_crosscheck"][0]["added"]["2"] == {"netconvert": "gyrr", "sibling": "Gyrr"}
            assert _states(data, "266197536") == ["GGgGGGrr", "yyyyyyrr", "rrrrrrGG", "rrrrrryy"]
        assert report["changed_controllers"] == sorted({"F1": ["LSA535_part0"], "F2": ["266197536"],
                                                        "F3": ["266197536", "LSA535_part0"]}[name])
    # F3 directly from the parent equals F1's control re-indexed to F3 with F2's declaration.
    chained, _ = cr.reindex_control(controls["F1"], nets["F1"], nets["F3"], _candidate("F2")[2])
    assert chained == controls["F3"]


def test_the_cli_writes_the_reindexed_control_and_its_report(tmp_path):
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    parent, candidate, control = tmp_path / "parent.net.xml", tmp_path / "candidate.net.xml", tmp_path / "c.add.xml"
    parent.write_bytes(ET.tostring(_parent()))
    candidate.write_bytes(ET.tostring(_removal()))
    control.write_bytes(CONTROL)
    declaration = tmp_path / "declaration.json"
    declaration.write_text(json.dumps(REMOVED), encoding="utf-8")
    out = tmp_path / "out" / "control.add.xml"
    done = subprocess.run([sys.executable, "-B", str(root / "plugins/astra-sumo/scripts/astra_twin.py"),
                           "control-reindex", "--parent-net", str(parent), "--candidate-net", str(candidate),
                           "--control", str(control), "--declaration", str(declaration), "--out", str(out), "--json"],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    payload = json.loads(done.stdout)
    assert payload["status"] == "pass" and payload["changed_controllers"] == ["T"]
    assert _states(out.read_bytes(), "T") == ["GGr", "yyr", "rrG"]
    report = json.loads((out.parent / "control.add.xml.report.json").read_text(encoding="utf-8"))
    assert report["files"]["output"]["sha256"] == payload["output_sha256"] and "load_check" not in report
