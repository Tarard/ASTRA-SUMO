import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.detector_demand import write_csv
from astra_sumo.core.hamburg_od_reuse import _NATIVE_MODULES, reuse_hamburg_od_initial
from astra_sumo.core.hamburg_readme_results import STATIONS

sumo = pytest.importorskip("sumo")


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.parametrize("matching", [False, True])
def test_reuse_checks_the_declared_body_scope_instead_of_the_old_fixed_nodes(cached_initial, matching):
    nodes = ["14398547", "205069138", "18653534", "LSA104_part0"]
    _write(cached_initial["prepared_dir"] / "scenario.json", {"junction_body_hold_nodes": nodes})
    if matching:
        for name in ("run.json", "native-request.json"):
            path = cached_initial["cached_dir"] / "native" / name
            record = json.loads(path.read_bytes())
            record["junction_body_hold_nodes"] = nodes
            _write(path, record)
        assert reuse_hamburg_od_initial(**cached_initial)[0]["loss"] == 4
    else:
        with pytest.raises(ValueError, match="cached native settings differ"):
            reuse_hamburg_od_initial(**cached_initial)


@pytest.fixture
def cached_initial(tmp_path):
    prepared, proposal, cached = (tmp_path / name for name in ("prepared", "proposal", "cached"))
    native = cached / "native"
    for path in (prepared, proposal, native):
        path.mkdir(parents=True)
    for name in ("network.net.xml", "field-loops.add.xml", "control.add.xml"):
        (prepared / name).write_text(f"<{name.split('.')[0]}/>", encoding="utf-8")
    _write(prepared / "od-model.json", {"fixture": "immutable OD model"})
    _write(prepared / "initial-od-vector.json", {"values": [1] * 294, "vehicle_count": 294})
    vector = {"values": [1.] * 294, "integer_counts": [1] * 294, "vehicle_count": 294,
              "source_model_sha256": file_sha256(prepared / "od-model.json")}
    for folder in (proposal, cached):
        _write(folder / "od-vector.json", vector)
    routes, trips = ET.Element("routes"), ET.Element("tripinfos")
    for index in range(294):
        ET.SubElement(routes, "vehicle", id=f"v{index}", depart="0")
        ET.SubElement(trips, "tripinfo", id=f"v{index}")
    ET.ElementTree(routes).write(proposal / "demand.rou.xml")
    ET.ElementTree(trips).write(native / "tripinfo.xml")
    _write(proposal / "lane-intentions.json", {"vehicles": []})
    expected, counts = [], ET.Element("detector")
    for field, (station, node, asset) in enumerate((station, node, asset)
            for station, (node, assets) in STATIONS.items() for asset in sorted(assets)):
        for h in range(10):
            begin, end = str(3600 + 900 * h), str(4500 + 900 * h)
            expected.append({"detector_id": f"field{field}", "begin": begin, "end": end,
                             "expected_total": "5", "node_id": node, "asset_id": asset})
            ET.SubElement(counts, "interval", id=f"field{field}", begin=begin, end=end,
                          nVehContrib="7" if field == h == 0 else "5")
    write_csv(prepared / "expected-counts.csv", expected, list(expected[0]))
    ET.ElementTree(counts).write(native / "field-counts.xml")
    pairs = ((prepared / "network.net.xml", "network.net.xml"), (proposal / "demand.rou.xml", "demand.rou.xml"),
             (prepared / "field-loops.add.xml", "source-e1.add.xml"),
             (prepared / "expected-counts.csv", "expected-counts.csv"),
             (prepared / "control.add.xml", "signal-programs.add.xml"),
             (proposal / "lane-intentions.json", "lane-intentions.json"))
    sources, snapshots = [], []
    for path, name in pairs:
        shutil.copyfile(path, native / name)
        sources.append({"path": str(path), "sha256": file_sha256(path)})
        snapshots.append({"path": str(native / name), "sha256": file_sha256(native / name)})
    completion = dict(loaded=294, inserted=294, arrived=294, running=0, waiting=0,
                      teleports=0, collisions=0, discarded=0)
    summary = ET.Element("summary")
    ET.SubElement(summary, "step", time="14399.9", **{k: str(v) for k, v in completion.items()})
    ET.ElementTree(summary).write(native / "summary.xml")
    _write(native / "run.json", {"native_status": "pass", "observer_complete": True,
           "missing_field_bins": 0, "expected_field_bins": 130, "inputs_unchanged": True,
           "seed": 104, "step_length_s": .1, "end_s": 14400, "measurement_attribute": "nVehContrib",
           "driver_treatment": "local_observation_and_junction_body_hold", "state_times_s": [],
           "signal_treatment": "declared_additional_programs",
           "junction_body_hold_nodes": ["14398547", "205069138", "18653534"],
           "inputs": sources, "snapshots": snapshots, "completion": completion, "healthy": True})
    _write(native / "command.json", {"status": "pass"})
    command = [str(Path(sumo.__file__).parent / "bin/sumo.exe"), "--seed", "104", "--step-length", "0.1",
               "--end", "14400", "--time-to-teleport", "-1", "--collision.check-junctions", "true"]
    _write(native / "stall-monitor.json", {"status": "completed", "native_returncode": 0,
           "simulation_time_s": 14400, "native_version": [22, "SUMO 1.27.1"],
           "command": [*command, "--remote-port", "1234"]})
    _write(native / "native-request.json", {"end_s": 14400, "all_stopped_timeout_s": 300,
           "monitor_interval_s": None, "junction_body_hold": True,
           "junction_body_hold_nodes": ["14398547", "205069138", "18653534"],
           "command": command})
    execution_dir = cached.with_name(cached.name + "-execution")
    frozen = execution_dir / "implementation/astra_sumo/core"
    frozen.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "plugins/astra-sumo/src/astra_sumo/core"
    hashes = {}
    for name in (*_NATIVE_MODULES, "hamburg_od_spsa.py"):
        shutil.copyfile(source / name, frozen / name)
        hashes[f"astra_sumo\\core\\{name}"] = file_sha256(frozen / name)
    _write(execution_dir / "execution.json", {
        "versions": {name: "1.27.1" for name in ("eclipse-sumo", "sumolib", "traci")},
        "sumo_sha256": file_sha256(Path(sumo.__file__).parent / "bin/sumo.exe"),
        "implementation_sha256": hashes})
    return {"prepared_dir": prepared, "proposal_dir": proposal, "cached_dir": cached}


def test_reuse_recomputes_native_field_and_derived_station_metrics(cached_initial):
    evaluation, provenance = reuse_hamburg_od_initial(**cached_initial)
    assert evaluation["loss"] == 4
    assert evaluation["MAE"] == 2 / 130
    assert evaluation["max_abs_error"] == 2
    assert evaluation["station_MAE"] == 2 / 40
    assert evaluation["station_max_abs_error"] == 2
    assert evaluation["healthy"] and not evaluation["exact_count_fit"]
    assert evaluation["field_rows"] == 130 and evaluation["derived_station_rows"] == 40
    assert len(provenance["input_sha256"]) == 6
    assert provenance["initial_loss_recomputed_from_native_e1"]
    _write(cached_initial["cached_dir"] / "evaluation.json", evaluation)
    assert reuse_hamburg_od_initial(**cached_initial)[0] == evaluation


@pytest.mark.parametrize("folder,name", [("proposal_dir", "demand.rou.xml"),
                                         ("proposal_dir", "lane-intentions.json"),
                                         ("prepared_dir", "control.add.xml"),
                                         ("prepared_dir", "expected-counts.csv")])
def test_changed_proposed_input_cannot_reuse_cached_loss(cached_initial, folder, name):
    with (cached_initial[folder] / name).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="input differs"):
        reuse_hamburg_od_initial(**cached_initial)


def test_unfinished_or_missing_native_counts_are_not_zero(cached_initial):
    native = cached_initial["cached_dir"] / "native"
    monitor = native / "stall-monitor.json"
    original = json.loads(monitor.read_bytes())
    _write(monitor, {**original, "status": "running"})
    with pytest.raises(ValueError, match="completed native"):
        reuse_hamburg_od_initial(**cached_initial)
    _write(monitor, original)
    tree = ET.parse(native / "field-counts.xml")
    tree.getroot().remove(tree.getroot()[0])
    tree.write(native / "field-counts.xml")
    with pytest.raises(ValueError, match="actual native E1"):
        reuse_hamburg_od_initial(**cached_initial)


def test_completed_unsafe_run_remains_a_diagnostic_loss(cached_initial):
    native = cached_initial["cached_dir"] / "native"
    run = json.loads((native / "run.json").read_bytes())
    run["completion"]["collisions"] = 1
    run["healthy"] = False
    _write(native / "run.json", run)
    tree = ET.parse(native / "summary.xml")
    tree.getroot()[-1].set("collisions", "1")
    tree.write(native / "summary.xml")
    evaluation, provenance = reuse_hamburg_od_initial(**cached_initial)
    assert evaluation["loss"] == 4
    assert not evaluation["healthy"] and not evaluation["exact_count_fit"]
    assert not provenance["healthy"]


def test_stale_metrics_and_changed_frozen_implementation_are_rejected(cached_initial):
    cached = cached_initial["cached_dir"]
    evaluation, _ = reuse_hamburg_od_initial(**cached_initial)
    _write(cached / "evaluation.json", {**evaluation, "loss": 0})
    with pytest.raises(ValueError, match="cached metrics differ"):
        reuse_hamburg_od_initial(**cached_initial)
    (cached / "evaluation.json").unlink()
    frozen = cached.with_name(cached.name + "-execution") / "implementation/astra_sumo/core/simulation_stall.py"
    with frozen.open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="implementation hash differs"):
        reuse_hamburg_od_initial(**cached_initial)


def test_metadata_format_fix_can_reuse_native_but_native_command_change_cannot(cached_initial):
    execution = cached_initial["cached_dir"].with_name(cached_initial["cached_dir"].name + "-execution")
    frozen = execution / "implementation/astra_sumo/core/hamburg_od_spsa.py"
    record = json.loads((execution / "execution.json").read_bytes())
    text = frozen.read_text(encoding="utf-8")
    text = text.replace('float(r["begin"])', 'r["begin"]').replace('float(r["end"])', 'r["end"]')
    frozen.write_text(text, encoding="utf-8")
    record["implementation_sha256"]["astra_sumo\\core\\hamburg_od_spsa.py"] = file_sha256(frozen)
    _write(execution / "execution.json", record)
    assert reuse_hamburg_od_initial(**cached_initial)[0]["loss"] == 4
    frozen.write_text(text.replace('output_dir=proposal / "native", end_s=14400',
                                   'output_dir=proposal / "native", end_s=14401'), encoding="utf-8")
    record["implementation_sha256"]["astra_sumo\\core\\hamburg_od_spsa.py"] = file_sha256(frozen)
    _write(execution / "execution.json", record)
    with pytest.raises(ValueError, match="native OD command changed"):
        reuse_hamburg_od_initial(**cached_initial)


def _configure_execution(cached_initial, *, backend, layout):
    cached = cached_initial["cached_dir"]
    native = cached / "native"
    legacy = cached.with_name(cached.name + "-execution")
    execution_dir = legacy
    if layout == "parent":
        execution_dir = cached.parent
        shutil.copytree(legacy / "implementation", execution_dir / "implementation")
        shutil.copyfile(legacy / "execution.json", execution_dir / "execution.json")
        (legacy / "execution.json").rename(legacy / "execution.legacy-disabled.json")
    execution = json.loads((execution_dir / "execution.json").read_bytes())
    execution["backend"] = backend
    for filename in ("run.json", "native-request.json", "stall-monitor.json"):
        record = json.loads((native / filename).read_bytes())
        record["backend"] = backend
        _write(native / filename, record)
    if backend == "libsumo":
        library = pytest.importorskip("libsumo._libsumo")
        digest = file_sha256(Path(library.__file__))
        execution["versions"]["libsumo"] = "1.27.1"
        execution["libsumo_sha256"] = digest
        monitor = json.loads((native / "stall-monitor.json").read_bytes())
        request = json.loads((native / "native-request.json").read_bytes())
        monitor.update(command=request["command"], native_returncode=None, library_completed=True,
                       native_library={"path": library.__file__, "sha256": digest})
        _write(native / "stall-monitor.json", monitor)
    _write(execution_dir / "execution.json", execution)
    return execution_dir


@pytest.mark.parametrize("backend", ["traci", "libsumo"])
@pytest.mark.parametrize("layout", ["legacy", "parent"])
def test_reuse_supports_declared_backends_and_real_execution_layouts(cached_initial, backend, layout):
    execution_dir = _configure_execution(cached_initial, backend=backend, layout=layout)
    evidence_before = {p: p.read_bytes() for p in cached_initial["cached_dir"].rglob("*") if p.is_file()}
    evaluation, provenance = reuse_hamburg_od_initial(**cached_initial)
    assert evaluation["loss"] == 4 and evaluation["field_rows"] == 130
    assert evaluation["derived_station_rows"] == 40
    assert provenance["backend"] == backend
    assert provenance["execution_file"] == str(execution_dir / "execution.json")
    assert len(provenance["input_sha256"]) == 6
    if backend == "libsumo":
        assert provenance["native_library"]["sha256"] == json.loads(
            (execution_dir / "execution.json").read_bytes())["libsumo_sha256"]
    assert all(p.read_bytes() == raw for p, raw in evidence_before.items())
    assert not (cached_initial["cached_dir"] / "evaluation-execution").exists()


@pytest.mark.parametrize("kind", ["command", "returncode", "completion", "backend", "library_hash"])
def test_libsumo_rejects_wrong_monitor_completion_or_runtime(cached_initial, kind):
    _configure_execution(cached_initial, backend="libsumo", layout="parent")
    path = cached_initial["cached_dir"] / "native/stall-monitor.json"
    record = json.loads(path.read_bytes())
    if kind == "command":
        record["command"] += ["--remote-port", "1234"]
    elif kind == "returncode":
        record["native_returncode"] = 0
    elif kind == "completion":
        record["library_completed"] = False
    elif kind == "backend":
        record["backend"] = "traci"
    else:
        record["native_library"]["sha256"] = "0" * 64
    _write(path, record)
    with pytest.raises(ValueError, match="native monitor|Libsumo"):
        reuse_hamburg_od_initial(**cached_initial)


@pytest.mark.parametrize("kind", ["missing_library_hash", "library_hash", "version", "backend", "frozen_code"])
def test_parent_libsumo_execution_rejects_stale_or_incompatible_evidence(cached_initial, kind):
    execution_dir = _configure_execution(cached_initial, backend="libsumo", layout="parent")
    path = execution_dir / "execution.json"
    record = json.loads(path.read_bytes())
    if kind == "missing_library_hash":
        record.pop("libsumo_sha256")
    elif kind == "library_hash":
        record["libsumo_sha256"] = "0" * 64
    elif kind == "version":
        record["versions"]["libsumo"] = "1.26.0"
    elif kind == "backend":
        record["backend"] = "traci"
    else:
        frozen = execution_dir / "implementation/astra_sumo/core/lane_section_hold.py"
        frozen.write_bytes(frozen.read_bytes() + b"\n")
    _write(path, record)
    with pytest.raises(ValueError, match="Libsumo|1.27.1|backend|implementation hash"):
        reuse_hamburg_od_initial(**cached_initial)


def test_parent_layout_does_not_relax_selected_initial_vector_identity(cached_initial):
    _configure_execution(cached_initial, backend="libsumo", layout="parent")
    path = cached_initial["prepared_dir"] / "initial-od-vector.json"
    initial = json.loads(path.read_bytes())
    initial["values"][125] = 0
    _write(path, initial)
    with pytest.raises(ValueError, match="cached OD vector differs"):
        reuse_hamburg_od_initial(**cached_initial)
