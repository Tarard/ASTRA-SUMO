"""Reference facts restate a clean SUMO network under its own ids (M3 known-answer producer)."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.network_calibration import evaluate_network_calibration
from astra_sumo.core.reference_net_facts import (FACT_KINDS, ReferenceFactParameters, build_reference_net_facts,
                                                 write_reference_net_facts)
from astra_sumo.corridor.enums import TrafficSide
from astra_sumo.corridor.synthetic_networks import apply_synthetic_mutation, build_synthetic_fixture


def _x4(tmp_path: Path, mutation: str | None = None) -> Path:
    root = build_synthetic_fixture("standard-x4", traffic_side=TrafficSide.RIGHT)
    if mutation:
        root = apply_synthetic_mutation(root, mutation)
    path = tmp_path / f"x4-{mutation or 'clean'}.net.xml"
    path.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return path


def _score(net: Path, facts: Path, output: Path) -> dict:
    return evaluate_network_calibration(net_file=net, facts_file=facts, output_file=output, mode="explore")


def test_x4_reference_facts_match_their_own_network_and_are_deterministic(tmp_path):
    net = _x4(tmp_path)
    first, second = (write_reference_net_facts(net_file=net, output_file=tmp_path / name, site_id="x4",
                                               target_date="2026-10-25") for name in ("a.json", "b.json"))
    assert first["sha256"] == second["sha256"]
    assert first["fact_counts"] == {"stop_line": 24, "lane_count": 8, "lane_permissions": 24, "movement_inventory": 24,
                                    "signal_group": 12, "lane_centerline": 24, "lane_width": 24, "speed": 24,
                                    "junction_surface": 0}
    assert first["skipped"] == {"junction_shape_missing": 5}
    table = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    ids = {row["id"] for row in table["facts"]}
    assert {"lane:W_in_0:stop_line", "edges:W_in:lane_count:passenger", "lanes:W_in_0:movement_inventory",
            "pair:W_in_0>S_out_0:signal_group", "lane:W_in_0:centerline"} <= ids
    assert {row["mapping_status"] for row in table["facts"]} == {"confirmed"}
    report = _score(net, tmp_path / "a.json", tmp_path / "score.json")
    assert Counter(row["status"] for row in report["facts"]) == {"matched": 164}
    with pytest.raises(ValueError, match="new fact-table path"):
        write_reference_net_facts(net_file=net, output_file=tmp_path / "a.json", site_id="x4", target_date="2026-10-25")


@pytest.mark.skipif(not shutil.which("netconvert"), reason="netconvert required")
def test_netconvert_reference_facts_cover_all_nine_kinds(tmp_path):
    nodes, edges, net = tmp_path / "cross.nod.xml", tmp_path / "cross.edg.xml", tmp_path / "cross.net.xml"
    nodes.write_text('<nodes><node id="C" x="0" y="0" type="traffic_light"/><node id="W" x="-100" y="0"/>'
                     '<node id="E" x="100" y="0"/><node id="S" x="0" y="-100"/><node id="N" x="0" y="100"/></nodes>',
                     encoding="utf-8")
    edges.write_text('<edges>' + "".join(f'<edge id="{a}{b}" from="{a}" to="{b}" numLanes="2" speed="13.89"/>'
                                         for arm in "WESN" for a, b in ((arm, "C"), ("C", arm))) + '</edges>',
                     encoding="utf-8")
    result = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(edges), "-o", str(net)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    table, summary = build_reference_net_facts(net, site_id="cross", target_date="2026-10-25")
    assert all(summary["fact_counts"][kind] > 0 for kind in FACT_KINDS), summary
    assert summary["fact_counts"]["junction_surface"] == 1
    facts_file = tmp_path / "facts.json"
    facts_file.write_text(json.dumps(table), encoding="utf-8")
    report = _score(net, facts_file, tmp_path / "score.json")
    assert {row["status"] for row in report["facts"]} == {"matched"}


def test_reference_facts_expose_injected_faults_on_the_named_elements(tmp_path):
    clean = _x4(tmp_path)
    write_reference_net_facts(net_file=clean, output_file=tmp_path / "facts.json", site_id="x4",
                              target_date="2026-10-25")
    # wrong-turn changes only dir, so no fact sees it; the direction check does (test_twin_direction_check.py).
    for mutation, expected in (("wrong-to-lane", {"lanes:W_in_1:movement_inventory",
                                                  "pair:W_in_1>E_out_1:signal_group"}),
                               ("missing-connection", {"lanes:W_in_0:movement_inventory",
                                                       "pair:W_in_0>S_out_0:signal_group"}),
                               ("shared-link-index-conflict", {"pair:S_in_1>N_out_1:signal_group"}),
                               ("illegal-movement", {"lanes:W_in_2:movement_inventory"}),
                               ("wrong-turn", set())):
        report = _score(_x4(tmp_path, mutation), tmp_path / "facts.json", tmp_path / f"{mutation}.json")
        assert {row["id"] for row in report["facts"] if row["status"] != "matched"} == expected, mutation
    moved = tmp_path / "moved.net.xml"
    root = ET.parse(clean).getroot()
    # A 0.6 m lateral shift breaks the centerline only; a 1 m shortening breaks the stop line only.
    root.find("edge[@id='W_in']/lane[@id='W_in_1']").set("shape", "-100.0,0.6 -5.0,0.6")
    root.find("edge[@id='E_in']/lane[@id='E_in_1']").set("shape", "100.0,0.0 6.0,0.0")
    root.find("edge[@id='E_out']/lane[@id='E_out_2']").set("speed", "10")
    moved.write_bytes(ET.tostring(root, encoding="utf-8"))
    report = _score(moved, tmp_path / "facts.json", tmp_path / "moved.json")
    assert {row["id"] for row in report["facts"] if row["status"] != "matched"} == {
        "lane:W_in_1:centerline", "lane:E_in_1:stop_line", "lane:E_out_2:speed"}


def test_parameters_set_thresholds_and_scope(tmp_path):
    net = _x4(tmp_path)
    parameters = ReferenceFactParameters(stop_line_tolerance_longitudinal_m=0.25, kinds=("stop_line", "speed"),
                                         edge_ids=("W_in",))
    table, summary = build_reference_net_facts(net, site_id="x4", target_date="2026-10-25", parameters=parameters)
    assert summary["fact_counts"] == {"stop_line": 3, "speed": 3}
    assert {row["tolerance_longitudinal_m"] for row in table["facts"] if row["kind"] == "stop_line"} == {0.25}
    assert [row["id"] for row in table["facts"]][:3] == ["lane:W_in_0:stop_line", "lane:W_in_1:stop_line",
                                                          "lane:W_in_2:stop_line"]
    for bad in ({"centerline_tolerance_m": 0.0}, {"speed_tolerance_m_s": 0.005}, {"junction_minimum_iou": 1.0},
                {"kinds": ("stop_line", "stop_line")}, {"kinds": ("lane_shape",)}):
        with pytest.raises(ValueError):
            ReferenceFactParameters(**bad)


def test_ring1_nominal_reference_facts_all_match_their_network(tmp_path):
    net = (Path(__file__).resolve().parents[1]
           / "benchmarks/hamburg_ring1_119_control_diagnosis_20261022_v1/nominal-inputs/network.net.xml")
    summary = write_reference_net_facts(net_file=net, output_file=tmp_path / "facts.json", site_id="ring1-nominal",
                                        target_date="2026-10-22")
    assert summary["reference_net"]["sha256"] == "cc04537494708721e1ad87409ab47cef249c3774dbaf9ed3ec7a7b01710672a4"
    assert summary["fact_counts"] == {"stop_line": 885, "lane_count": 360, "lane_permissions": 885,
                                      "movement_inventory": 885, "signal_group": 488, "lane_centerline": 885,
                                      "lane_width": 885, "speed": 885, "junction_surface": 195}
    report = _score(net, tmp_path / "facts.json", tmp_path / "score.json")
    assert Counter(row["status"] for row in report["facts"]) == {"matched": 6353}


def test_traffic_modes_follow_the_prepared_source_idiom_and_the_scorer_builds_the_pairs(tmp_path):
    # Review finding: the producer kept its own motor-class set and labelled a bicycle+pedestrian lane bicycle, unlike
    # prepared_source_facts; it also copied the scorer's connection-pair index.
    from astra_sumo.core import reference_net_facts

    assert reference_net_facts._traffic_mode({"bicycle", "pedestrian"}) == "pedestrian"
    assert reference_net_facts._traffic_mode({"bicycle"}) == "bicycle"
    assert reference_net_facts._traffic_mode({"passenger", "pedestrian"}) == "motor"
    assert reference_net_facts._traffic_mode(set()) == "unspecified"
    root = build_synthetic_fixture("standard-x4", traffic_side=TrafficSide.RIGHT)
    next(row for row in root.findall("connection") if row.get("from") == "W_in").set("toLane", "9")
    broken = tmp_path / "broken.net.xml"
    broken.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    with pytest.raises(ValueError, match="A network connection references a missing lane."):
        build_reference_net_facts(broken, site_id="x4", target_date="2026-10-25")
    table, summary = build_reference_net_facts(_x4(tmp_path), site_id="x4", target_date="2026-10-25",
                                               source_path="nets/x4.net.xml")
    assert table["sources"][0]["path"] == summary["reference_net"]["path"] == "nets/x4.net.xml"
