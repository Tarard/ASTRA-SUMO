import json
from hashlib import sha256
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.command_runner import CommandResult
from astra_sumo.core.network_link_semantics import (
    _boundary_lane_mapping, _compare, _compare_load_warnings, compare_loaded_link_semantics,
)


def test_load_warning_counts_retain_duplicates_and_separate_native_subnetworks():
    observed = {"snapshots": {"source": {}, "candidate": {}}, "load_logs": {
        "source": {"warnings": ["Warning: old", "Warning: shared", "Warning: shared"]},
        "candidate": {"warnings": ["Warning: new", "Warning: shared"]},
        "native-0": {"warnings": ["Warning: partial network"]}}}
    result = _compare_load_warnings(observed)
    assert result["status"] == "measured"
    assert result["inherited"] == {"count": 1, "lines": {"Warning: shared": 1}}
    assert result["source_only"] == {"count": 2, "lines": {"Warning: old": 1, "Warning: shared": 1}}
    assert result["candidate_only"] == {"count": 1, "lines": {"Warning: new": 1}}
    assert result["by_input"]["native-0"]["warnings"] == ["Warning: partial network"]
    del observed["snapshots"]["candidate"]
    incomplete = _compare_load_warnings(observed)
    assert incomplete["status"] == "unavailable" and incomplete["missing_observations"] == ["candidate"]
    assert "candidate_only" not in incomplete
    assert _compare_load_warnings({})["status"] == "unavailable"


def test_each_junction_uses_its_declared_reference():
    source = {"junctions": {"outside": {"foes": ["kept"]}, "edit": {"foes": ["old"]}}}
    native = {"junctions": {"edit": {"foes": ["new"]}}}
    candidate = {"junctions": {"outside": {"foes": ["kept"]}, "edit": {"foes": ["new"]}}}
    snapshots = {"source": source, "native": native, "candidate": candidate}
    assert _compare(snapshots, {"edit"}) == {"status": "pass", "compared_junction_counts": {"native": 1, "source": 1},
        "unresolved_references": [], "differences": []}
    candidate["junctions"]["outside"] = {"foes": ["new"]}
    result = _compare(snapshots, {"edit"})
    assert result["status"] == "fail"
    assert [(row["junction_id"], row["reference"]) for row in result["differences"]] == [("outside", "source")]
    candidate["junctions"]["edit"] = {"foes": ["old"]}
    assert len(_compare(snapshots, {"edit"})["differences"]) == 2
    del snapshots["native"]
    assert _compare(snapshots, {"edit"})["status"] == "unavailable"
    snapshots["native"] = []
    assert _compare(snapshots, {"edit"})["status"] == "unavailable"


def test_later_native_stages_replace_only_their_declared_junctions():
    snapshots = {"source": {"junctions": {"outside": {}, "old": {}}},
        "early": {"junctions": {"A": {"foes": ["early"]}, "B": {"foes": ["obsolete"]}}},
        "late": {"junctions": {"A": {"foes": ["not_adopted"]}, "B": {"foes": ["late"]}}},
        "candidate": {"junctions": {"outside": {}, "A": {"foes": ["early"]}, "B": {"foes": ["late"]}}}}
    mutable = {"old", "A", "B"}
    references = {"A": "early", "B": "late"}
    result = _compare(snapshots, mutable, references)
    assert result["status"] == "pass" and result["compared_junction_counts"] == {"native": 2, "source": 1}
    assert _compare(snapshots, mutable, {"A": "early"})["unresolved_references"] == ["native:B"]
    assert _compare(snapshots, mutable, {"A": "early", "B": "early"})["status"] == "fail"
    snapshots["empty"] = {"junctions": {}}
    missing = _compare(snapshots, mutable, {"A": "early", "B": "empty"})
    assert missing["status"] == "unavailable" and missing["unresolved_references"] == ["empty:B"]
    assert missing["compared_junction_counts"] == {"native": 1, "source": 1}


def test_boundary_renaming_uses_explicit_lane_indices_and_checks_all_lane_references():
    source = ET.fromstring('<net><edge id="road"><lane index="0" id="custom"/></edge></net>')
    candidate = ET.fromstring('<net><edge id="upstream"><lane index="0" id="new-custom"/></edge></net>')
    mapping, errors = _boundary_lane_mapping(source, candidate, {"road": "upstream"})
    assert mapping == {"custom": "new-custom"} and errors == []
    before = {"lane_links": {"custom": [{"successor": "custom", "via": ":A_0_0", "direction": "s", "yield_to": ["custom"]}]},
        "internal_foes": {":A_0_0": ["custom"]}}
    after = {"lane_links": {"new-custom": [{"successor": "new-custom", "via": ":A_0_0", "direction": "s", "yield_to": ["new-custom"]}]},
        "internal_foes": {":A_0_0": ["new-custom"]}}
    snapshots = {"source": {"junctions": {"A": before}}, "candidate": {"junctions": {"A": after}}}
    assert _compare(snapshots, set(), {}, mapping)["status"] == "pass"
    assert _compare(snapshots, set(), {})["status"] == "fail"
    candidate.find("edge/lane").set("index", "1")
    assert _boundary_lane_mapping(source, candidate, {"road": "upstream"})[1]
    assert _boundary_lane_mapping(source, candidate, {"road": "absent"})[1]
    assert _boundary_lane_mapping(source, candidate, {"road": "upstream", "other": "upstream"})[1]
    candidate.find("edge/lane").set("index", "0")
    ET.SubElement(source.find("edge"), "lane", index="0", id="ambiguous-index")
    assert _boundary_lane_mapping(source, candidate, {"road": "upstream"})[1]


@pytest.mark.parametrize("fault", ["timeout", "malformed", "invalid_structure", "invalid_logs", "source_drift", "source_removed", "frozen_drift"])
def test_incomplete_observation_never_reports_preservation(tmp_path, fault):
    source = tmp_path / "source.net.xml"
    source.write_text("<net/>", encoding="utf-8")

    def worker(command, *, cwd, timeout_seconds):
        assert timeout_seconds == 3
        assert command[1:3] == ["-m", "astra_sumo.core.network_link_semantics"]
        output = Path(command[-1])
        if fault in {"malformed", "invalid_structure"}:
            output.write_text("{" if fault == "malformed" else "[]", encoding="utf-8")
        else:
            observation = {"snapshots": {name: {"junctions": {}} for name in ("source", "native", "candidate")}}
            if fault == "invalid_logs":
                observation["load_logs"] = {"source": {"warnings": [None]}}
            if fault == "frozen_drift":
                frozen = tmp_path / "damaged-input.net.xml"
                frozen.write_text('<net changed="true"/>', encoding="utf-8")
                observation["frozen_inputs"] = [{"path": str(frozen), "sha256": sha256(b"<net/>").hexdigest()}]
            output.write_text(json.dumps(observation), encoding="utf-8")
        if fault == "source_drift":
            source.write_text('<net changed="true"/>', encoding="utf-8")
        if fault == "source_removed":
            source.unlink()
        return CommandResult(command=command, cwd=str(cwd), status="timeout" if fault == "timeout" else "pass",
            returncode=None if fault == "timeout" else 0)

    report = compare_loaded_link_semantics(source_net_file=source, native_net_file=source, candidate_net_file=source,
        mutable_junction_ids=set(), output_dir=tmp_path / "measurement", command_runner=worker, timeout_seconds=3)
    assert report["status"] == "unavailable"
    assert report["inputs_unchanged"] is (fault not in {"source_drift", "source_removed"})
    assert report["frozen_inputs_unchanged"] is (fault != "frozen_drift")
    assert json.loads(Path(report["report_file"]).read_text())["status"] == "unavailable"


def test_missing_sumo_binary_retains_worker_errors(tmp_path):
    source = tmp_path / "source.net.xml"
    source.write_text("<net/>", encoding="utf-8")
    report = compare_loaded_link_semantics(source_net_file=source, native_net_file=source, candidate_net_file=source,
        mutable_junction_ids=set(), output_dir=tmp_path / "measurement", sumo_binary=str(tmp_path / "missing-sumo"))
    assert report["status"] == "unavailable"
    assert set(report["observation_errors"]) == {"source", "native", "candidate"}
    assert report["compared_junction_counts"] == {"native": 0, "source": 0}


def test_changed_declared_native_output_cannot_pass_using_its_new_hash(tmp_path):
    source = tmp_path / "source.net.xml"
    source.write_text("<net/>", encoding="utf-8")

    def worker(command, *, cwd, timeout_seconds):
        request = json.loads(Path(command[-2]).read_text())
        Path(command[-1]).write_text(json.dumps({"snapshots": {name: {"junctions": {"J": {}}}
            for name in request["inputs"]}}), encoding="utf-8")
        return CommandResult(command=command, cwd=str(cwd), status="pass", returncode=0)

    report = compare_loaded_link_semantics(source_net_file=source, candidate_net_file=source,
        native_net_file={"J": {"path": str(source), "sha256": "0" * 64}}, mutable_junction_ids={"J"},
        output_dir=tmp_path / "measurement", command_runner=worker)
    assert report["status"] == "unavailable"
    assert report["native_hash_errors"][0]["junction_id"] == "J"
    assert report["compared_junction_counts"]["native"] == 1


def test_worker_loads_frozen_bytes_and_retains_original_drift(tmp_path, monkeypatch):
    from astra_sumo.core import network_link_semantics
    source, request, output = [tmp_path / name for name in ("source.net.xml", "request.json", "observations.json")]
    raw = b"<net/>"
    source.write_bytes(raw)
    request.write_text(json.dumps({"inputs": {"source": {"path": str(source), "sha256": sha256(raw).hexdigest()}},
        "sumo_binary": "unused"}), encoding="utf-8")

    def capture(network, binary, label):
        assert network != source and network.read_bytes() == raw
        source.write_bytes(b'<net changed="true"/>')
        assert network.read_bytes() == raw
        return {"junctions": {}}

    monkeypatch.setattr(network_link_semantics, "_capture", capture)
    monkeypatch.setattr("sys.argv", ["network_link_semantics", str(request), str(output)])
    network_link_semantics._main()
    observed = json.loads(output.read_text())
    assert observed["snapshots"] == {}
    assert "changed during observation" in observed["errors"]["source"]
    frozen = observed["frozen_inputs"][0]
    assert Path(frozen["path"]).read_bytes() == raw
    assert frozen["sha256"] == sha256(raw).hexdigest()


def test_worker_retains_a_failed_load_log_without_claiming_no_warnings(tmp_path, monkeypatch):
    from astra_sumo.core import network_link_semantics
    source, request, output = [tmp_path / name for name in ("source.net.xml", "request.json", "observations.json")]
    source.write_bytes(b"<net/>")
    request.write_text(json.dumps({"inputs": {"source": {"path": str(source), "sha256": sha256(b"<net/>").hexdigest()}},
        "sumo_binary": "unused"}), encoding="utf-8")

    def capture(network, binary, label):
        network.with_suffix(".load.log").write_text("Warning: before failure\nError: invalid network\n", encoding="utf-8")
        raise ValueError("load failed")

    monkeypatch.setattr(network_link_semantics, "_capture", capture)
    monkeypatch.setattr("sys.argv", ["network_link_semantics", str(request), str(output)])
    network_link_semantics._main()
    observed = json.loads(output.read_text())
    assert observed["snapshots"] == {} and "load failed" in observed["errors"]["source"]
    log = observed["load_logs"]["source"]
    assert log["warnings"] == ["Warning: before failure"]
    assert sha256(Path(log["path"]).read_bytes()).hexdigest() == log["sha256"]
    assert "Error: invalid network" in Path(log["path"]).read_text()
    assert _compare_load_warnings(observed)["status"] == "unavailable"
