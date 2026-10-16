import json
from hashlib import sha256
from pathlib import Path

import pytest

from astra_sumo.core.command_runner import CommandResult
from astra_sumo.core.network_link_semantics import _compare, compare_loaded_link_semantics


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


@pytest.mark.parametrize("fault", ["timeout", "malformed", "invalid_structure", "source_drift", "source_removed", "frozen_drift"])
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
