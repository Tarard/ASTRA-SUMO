import gzip
import json
from pathlib import Path

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.route_sensor_matrix import read_contributing_e1_passages


def test_completed_and_early_leaves_keep_periodic_contribution_semantics(tmp_path):
    instant, periodic = tmp_path / "instant.xml", tmp_path / "periodic.xml"
    instant.write_text('''<instantE1>
      <instantOut id="trace" vehID="first" state="leave" time="0.4" occupancy="0.2"/>
      <instantOut id="trace" vehID="partial" state="leave" time="0.8"/>
      <instantOut id="trace" vehID="boundary" state="leave" time="0.95" occupancy="0.2"/>
      <instantOut id="trace" vehID="later" state="leave" time="1.4" occupancy="0.2"/>
    </instantE1>''', encoding="utf-8")
    periodic.write_text('''<detector><interval id="field" begin="0" end="1" nVehContrib="1"/>
      <interval id="field" begin="1" end="2" nVehContrib="2"/></detector>''', encoding="utf-8")
    result = read_contributing_e1_passages(instant_file=instant, periodic_file=periodic,
        detector_ids={"trace": "field"}, step_length=.1)
    assert result["reconciled"] and result["inputs_unchanged"]
    assert [row["vehicle_id"] for row in result["early_departures"]] == ["partial"]
    assert [row["interval_begin_s"] for row in result["passages"]] == [0, 1, 1]
    periodic.write_text(periodic.read_text().replace('nVehContrib="2"', 'nVehContrib="3"'), encoding="utf-8")
    mismatch = read_contributing_e1_passages(instant_file=instant, periodic_file=periodic,
        detector_ids={"trace": "field"}, step_length=.1)
    assert not mismatch["reconciled"]
    assert mismatch["interval_comparisons"][1]["difference"] == -1


def test_frozen_ring1_events_reconcile_all_periodic_fields(tmp_path):
    package = Path(__file__).parents[1] / "benchmarks/hamburg_ring1_count_probe_20261021_v1"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    paths = {}
    for kind, row in manifest["files"].items():
        compressed = package / row["file"]
        assert file_sha256(compressed) == row["compressed_sha256"]
        path = tmp_path / (kind + ".xml")
        path.write_bytes(gzip.decompress(compressed.read_bytes()))
        assert file_sha256(path) == row["sha256"]
        paths[kind] = path
    result = read_contributing_e1_passages(instant_file=paths["instant_leaves"], periodic_file=paths["periodic"],
        detector_ids=manifest["instant_to_periodic_ids"], step_length=.1)
    assert result["reconciled"] and result["inputs_unchanged"]
    assert len(result["passages"]) == 538
    assert len(result["early_departures"]) == 2
    assert len(result["interval_comparisons"]) == 156
    assert len({row["detector_id"] for row in result["passages"]}) == 13
    assert not result["outside_periodic_intervals"]


@pytest.mark.parametrize("step", [0, -1, float("nan")])
def test_invalid_simulation_step_cannot_assign_passages_to_bins(tmp_path, step):
    with pytest.raises(ValueError, match="step"):
        read_contributing_e1_passages(instant_file=tmp_path / "absent", periodic_file=tmp_path / "absent",
            detector_ids={}, step_length=step)
