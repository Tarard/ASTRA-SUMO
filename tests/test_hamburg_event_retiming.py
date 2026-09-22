import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.hamburg_event_retiming import (
    compare_passages,
    read_passages,
    retiming_improves,
)


def test_enter_events_keep_v1_bin_boundary_and_station_deduplication(tmp_path):
    source = tmp_path / "events.xml"
    source.write_text('''<instantE1>
      <instantOut id="a" vehID="v0" state="enter" time="899.99"/>
      <instantOut id="b" vehID="v0" state="enter" time="899.99"/>
      <instantOut id="a" vehID="v0" state="stay" time="900"/>
      <instantOut id="b" vehID="v1" state="enter" time="900"/>
      <instantOut id="a" vehID="v2" state="enter" time="1800"/>
    </instantE1>''', encoding="utf-8")
    events = read_passages(source, {"a": 1, "b": 1}, {"v0", "v1", "v2"})
    report = compare_passages(events, {(1, 0): 1, (1, 900): 1}, 0, 900)
    assert [r["measured"] for r in report["comparisons"]] == [1, 1]
    assert report["total_absolute_error"] == 0
    read_passages(source, {"a": 1, "b": 1}, {"v0", "v1", "v2"},
                  expected_entries={("a", 0): 1, ("b", 0): 1, ("b", 900): 1})
    with pytest.raises(ValueError, match="nVehEntered"):
        read_passages(source, {"a": 1, "b": 1}, {"v0", "v1", "v2"},
                      expected_entries={("a", 0): 2})
    ET.parse(source)  # File is readable without a simulator.
    with pytest.raises(ValueError, match="unknown detector"):
        read_passages(source, {"a": 1}, {"v0", "v1", "v2"})


def test_feedback_rejects_cross_station_regressions_and_incomplete_runs():
    current = {"total_absolute_error": 6, "station_absolute_error": {1: 6, 2: 0}}
    candidate = {"total_absolute_error": 4, "station_absolute_error": {1: 3, 2: 1}, "healthy": True}
    assert not retiming_improves(current, candidate)
    candidate.update(station_absolute_error={1: 4, 2: 0})
    assert retiming_improves(current, candidate)
    candidate.update(healthy=False)
    assert not retiming_improves(current, candidate)
    candidate.update(total_absolute_error=None)
    assert not retiming_improves(current, candidate)
    candidate.update(healthy=True, total_absolute_error=6)
    assert not retiming_improves(current, candidate)


def test_periodic_e1_uses_declared_simulation_step_at_bin_boundaries(tmp_path):
    source = tmp_path / "events.xml"
    source.write_text('<instantE1><instantOut id="a" vehID="v" state="enter" time="899.906226435250"/></instantE1>', encoding="utf-8")
    events = read_passages(source, {"a": 1}, {"v"}, step_length=1,
                           expected_entries={("a", 0): 0, ("a", 900): 1})
    assert events == {"v": {(1, 900)}}
    assert compare_passages(events, {(1, 0): 0, (1, 900): 1}, 0, 900)["mae"] == 0
    with pytest.raises(ValueError, match="Simulation step"):
        read_passages(source, {"a": 1}, {"v"}, step_length=0)
