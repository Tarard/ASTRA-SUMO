from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from astra_sumo.cli import main
from astra_sumo.core.load_replay import run_load_replay

TRIPS = ('<routes><trip id="t0" depart="1" from="in" to="out" departLane="best"/>'
         '<trip id="t1" depart="2" from="in" to="out"/><trip id="t2" depart="3" from="in" to="side"/>'
         '<trip id="t3" depart="4" from="in" to="out"/></routes>')


def _net(path: Path, edges: dict[str, str]) -> Path:
    path.write_text("<net>" + "".join(
        f'<edge id="{edge}" from="a" to="b"><lane id="{edge}_0" index="0" length="50"{permission}/></edge>'
        for edge, permission in edges.items()) + '<edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" length="5"/>'
        "</edge></net>", encoding="utf-8")
    return path


def _runner(results: dict[str, dict[str, list[str]]], calls: list[dict]):
    """A stand-in for the routeability runner: two attempts whose files say which vehicles arrived, collided or stood."""

    def run(*, net_file: Path, output_dir: Path, frozen_trip_file: Path, expected_frozen_trip_sha256: str, **options) -> dict:
        calls.append({"net": net_file.name, "trips": frozen_trip_file, "sha256": expected_frozen_trip_sha256, **options})
        assert hashlib.sha256(frozen_trip_file.read_bytes()).hexdigest() == expected_frozen_trip_sha256
        result = results[net_file.name]
        output_dir.mkdir(parents=True)
        attempts = []
        for end in (options["initial_end"], options["max_end"]):
            positions = output_dir / f"end{end}_positions.xml"
            positions.write_text(f'<fcd-export><timestep time="{end - 1}.00">' + "".join(
                f'<vehicle id="{vehicle}" speed="0.00" pos="7.50" lane="in_0"/>' for vehicle in result.get("standing", [])
            ) + "</timestep></fcd-export>", encoding="utf-8")
            attempts.append({"end": end, "diagnostic_outputs": {"last_step_positions": str(positions)}})
        tripinfo = output_dir / "tripinfo.xml"
        tripinfo.write_text("<tripinfos>" + "".join(f'<tripinfo id="{vehicle}"/>' for vehicle in result["arrived"])
                            + "</tripinfos>", encoding="utf-8")
        collisions = output_dir / "collisions.xml"
        collisions.write_text("<collisions>" + "".join(
            f'<collision collider="{vehicle}" lane=":j_0_0"/>' for vehicle in result.get("collided", [])) + "</collisions>",
            encoding="utf-8")
        final = {**attempts[-1], "tripinfo_file": str(tripinfo),
                 "diagnostic_outputs": {**attempts[-1]["diagnostic_outputs"], "collisions": str(collisions)},
                 "inspection": {"summary": {"collisions": len(result.get("collided", [])), "teleports": 0}}}
        return {"routeability_status": "incomplete", "attempts": [attempts[0], final], "final_attempt": final,
                "report_file": str(output_dir / "report.json")}

    return run


def test_load_replay_gives_every_network_the_same_trips_and_compares_arrivals(tmp_path: Path) -> None:
    trips = tmp_path / "frozen.trips.xml"
    trips.write_text(TRIPS, encoding="utf-8")
    before = trips.read_bytes()
    plain = _net(tmp_path / "plain.net.xml", {"in": "", "out": "", "side": ""})
    astra = _net(tmp_path / "astra.net.xml", {"in": "", "out": "", "side": ""})
    calls: list[dict] = []
    report = run_load_replay(
        trip_file=trips, networks={"plain": plain, "astra": astra}, output_dir=tmp_path / "replay", seed=7,
        runner=_runner({"plain.net.xml": {"arrived": ["t0", "t1"], "collided": ["t1"], "standing": ["t2"]},
                        "astra.net.xml": {"arrived": ["t0", "t1", "t2", "t3"]}}, calls))

    plain_row, astra_row = report["networks"]
    assert (report["status"], report["reference"], report["trip_count"]) == ("measured", "plain", 4)
    # Both runs got the input file itself, the same seed, teleport-free diagnostics and their own folder.
    assert [(call["trips"], call["seed"], call["diagnostic_outputs"], call["vehicle_count"]) for call in calls] == [
        (trips.resolve(), 7, True, 4)] * 2
    # A collider that arrived counts as not arrived; t2 stood at one lane position from 2700 s to 3600 s.
    assert (plain_row["arrived_count"], plain_row["arrived_share"], plain_row["collision_count"]) == (1, 0.25, 1)
    assert plain_row["not_arrived_states"] == {"collision": 1, "not_inserted": 1, "unfinished": 1}
    assert plain_row["collision_junctions"] == [("j", 1)]
    assert plain_row["standing_vehicles"]["vehicle_count"] == 1 and plain_row["standing_vehicles"]["from_s"] == 2700
    assert (astra_row["arrived_share"], astra_row["arrived_share_minus_reference"]) == (1.0, 0.75)
    saved = json.loads(Path(report["report_file"]).read_text(encoding="utf-8"))
    assert saved["schema"] == "astra.load-replay/v1" and len(saved["networks"][0]["not_arrived_vehicles"]) == 3
    assert "not_arrived_vehicles" not in plain_row and trips.read_bytes() == before
    with pytest.raises(ValueError, match="new replay output directory"):
        run_load_replay(trip_file=trips, networks={"plain": plain}, output_dir=tmp_path / "replay", runner=_runner({}, []))


def test_load_replay_skips_trips_a_network_cannot_carry_and_does_not_compare_unequal_sets(tmp_path: Path) -> None:
    trips = tmp_path / "frozen.trips.xml"
    trips.write_text(TRIPS, encoding="utf-8")
    full = _net(tmp_path / "full.net.xml", {"in": "", "out": "", "side": ""})
    # This network has no edge "side", and its "out" is closed to passenger cars on a second net.
    partial = _net(tmp_path / "partial.net.xml", {"in": "", "out": ""})
    closed = _net(tmp_path / "closed.net.xml", {"in": "", "out": ' allow="bus"', "side": ' disallow="passenger"'})
    calls: list[dict] = []
    report = run_load_replay(
        trip_file=trips, networks={"full": full, "partial": partial, "closed": closed}, output_dir=tmp_path / "replay",
        runner=_runner({"full.net.xml": {"arrived": ["t0", "t1", "t2", "t3"]},
                        "partial.net.xml": {"arrived": ["t0", "t1", "t3"]}}, calls))

    full_row, partial_row, closed_row = report["networks"]
    assert (partial_row["trip_count"], partial_row["skipped_trip_count"], partial_row["arrived_share"]) == (3, 1, 1.0)
    # The three trips went to a new file; an arrival share over three trips is not set against one over four.
    subset = Path(partial_row["replayed_trip_file"]["path"])
    assert subset.parent == tmp_path / "replay" / "partial" and subset.read_text(encoding="utf-8").count("<trip ") == 3
    assert calls[1]["trips"] == subset and 'id="t2"' not in subset.read_text(encoding="utf-8")
    assert partial_row["arrived_share_minus_reference"] is None and full_row["arrived_share_minus_reference"] == 0.0
    assert (closed_row["status"], closed_row["reason"]) == ("blocked", "no_trip_has_both_edges_in_this_network")
    assert report["status"] == "measured" and len(calls) == 2


def test_load_replay_cli_blocks_when_no_network_can_carry_a_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trips = tmp_path / "frozen.trips.xml"
    trips.write_text(TRIPS, encoding="utf-8")
    other = _net(tmp_path / "other.net.xml", {"elsewhere": ""})
    code = main(["network", "load-replay", str(trips), str(tmp_path / "replay"), "--net", f"other={other}", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 3 and (payload["status"], payload["reason"]) == ("blocked", "no_network_was_replayed")
    assert payload["networks"][0]["skipped_trip_count"] == 4
    assert json.loads((tmp_path / "replay" / "load-replay.json").read_text(encoding="utf-8"))["status"] == "blocked"
