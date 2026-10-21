import shutil
import subprocess

import pytest

from astra_sumo.core.detector_demand_replay import run_point_detector_demand
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages


@pytest.mark.skipif(not shutil.which("netconvert") or not shutil.which("sumo"), reason="SUMO programs required")
def test_native_point_detector_demand_replay_keeps_inputs_and_counts(tmp_path):
    nodes, edges, network, routes, loops, expected = [tmp_path / name for name in
        ("nodes.xml", "edges.xml", "network.xml", "routes.xml", "loops.xml", "expected.csv")]
    nodes.write_text('<nodes><node id="a" x="0" y="0"/><node id="b" x="100" y="0"/><node id="c" x="200" y="0"/></nodes>', encoding="utf-8")
    edges.write_text('<edges><edge id="in" from="a" to="b"/><edge id="out" from="b" to="c"/></edges>', encoding="utf-8")
    compiled = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(network)],
        capture_output=True, text=True, timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    routes.write_text('<routes><vehicle id="v" depart="0" departSpeed="max"><route edges="in out"/></vehicle></routes>', encoding="utf-8")
    loops.write_text('''<additional><inductionLoop id="first" lane="in_0" pos="30" period="30" file="unused.xml"/>
      <inductionLoop id="second" lane="out_0" pos="30" period="30" file="unused.xml"/></additional>''', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfirst,0,30,1\nsecond,0,30,1\n', encoding="utf-8")
    originals = {path: path.read_bytes() for path in (network, routes, loops, expected)}
    output = tmp_path / "replay"
    result = run_point_detector_demand(net_file=network, route_file=routes, e1_additional_file=loops,
        expected_counts_csv=expected, output_dir=output, end_s=60, sumo_binary=shutil.which("sumo"))
    assert result["status"] == "measured" and result["healthy"] and result["observer_complete"], result
    assert result["exact_count_fit"] and result["exact_field_bins"] == 2
    assert result["field_metrics"]["MAE"] == result["field_metrics"]["max_abs_error"] == 0
    assert not result["formal_result"] and result["inputs_unchanged"]
    response = read_contributing_e1_passages(instant_file=output / "instant-e1.xml", periodic_file=output / "field-counts.xml",
        detector_ids=result["instant_to_periodic_ids"], step_length=.1)
    assert response["reconciled"] and len(response["passages"]) == 2
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def test_replay_rejects_an_existing_destination_without_modifying_inputs(tmp_path):
    files = [tmp_path / name for name in ("net", "routes", "loops", "expected")]
    for path in files:
        path.write_text("source", encoding="utf-8")
    with pytest.raises(ValueError, match="new replay directory"):
        run_point_detector_demand(net_file=files[0], route_file=files[1], e1_additional_file=files[2],
            expected_counts_csv=files[3], output_dir=tmp_path, end_s=60)
    assert all(path.read_text(encoding="utf-8") == "source" for path in files)


def test_backend_failure_keeps_all_expected_bins_as_missing(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from astra_sumo.core.detector_demand import read_csv_rows
    import astra_sumo.core.detector_demand_replay as replay

    network, routes, loops, expected = [tmp_path / name for name in ("network.xml", "routes.xml", "loops.xml", "expected.csv")]
    network.write_text('<net/>', encoding="utf-8")
    routes.write_text('<routes><vehicle id="v" depart="0"><route edges="a"/></vehicle></routes>', encoding="utf-8")
    loops.write_text('<additional><inductionLoop id="field" lane="a_0" pos="1" period="30" file="unused"/></additional>', encoding="utf-8")
    expected.write_text('detector_id,begin,end,expected_total\nfield,0,30,2\nfield,30,60,3\n', encoding="utf-8")
    monkeypatch.setattr(replay, "run_command", lambda *args, **kwargs:
        SimpleNamespace(returncode=1, to_dict=lambda: {"status": "fail", "returncode": 1}))
    result = run_point_detector_demand(net_file=network, route_file=routes, e1_additional_file=loops,
        expected_counts_csv=expected, output_dir=tmp_path / "failed", end_s=60)
    assert result["status"] == "invalid" and result["errors"]
    assert result["expected_field_bins"] == result["missing_field_bins"] == 2
    assert result["field_metrics"] is None and not result["exact_count_fit"]
    rows = read_csv_rows(tmp_path / "failed/field-comparison.csv")
    assert len(rows) == 2 and all(row["measured_nVehContrib"] == "" for row in rows)
