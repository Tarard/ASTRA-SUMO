from copy import deepcopy
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.candidate_contracts import file_sha256
from astra_sumo.core.candidate_contracts import validate_materialization_evidence
from astra_sumo.core.corridor_edit_ledger import materialize_corridor_edit_variant
from test_network_edit_operations import _source


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO binaries required")
@pytest.mark.parametrize("connected", [False, True])
def test_added_lane_service_is_distinguished_from_geometry(tmp_path, connected):
    source = _source(tmp_path, "set_lane_count")
    digest = file_sha256(source)
    params = dict(edge_id="middle", num_lanes=2)
    if connected:
        params["connections"] = [
            {"from": a, "fromLane": i, "to": b, "toLane": j}
            for a, i, b, j in [
                ("in", 0, "middle", 0),
                ("in", 0, "middle", 1),
                ("middle", 0, "out", 0),
                ("middle", 1, "out", 0),
            ]
        ]
    operation = dict(
        id="lane",
        operation="set_lane_count",
        status="accepted",
        params=params,
        rationale="New lane service regression.",
        evidence=[dict(path=str(source), sha256=digest)],
        rollback=dict(action="discard_candidate"),
    )
    report = materialize_corridor_edit_variant(
        output_dir=tmp_path / "candidate",
        ledger=dict(source_net_file=str(source), source_sha256=digest, operations=[operation]),
        netconvert_binary=shutil.which("netconvert"),
    )
    assert report["status"] == ("pass" if connected else "review_required"), report
    assert report["claim_status"] == "diagnostic-demo"
    service = report["semantic_validation"]["new_lane_service"]
    assert service["status"] == ("pass" if connected else "review_required")
    lane = service["lanes"][0]
    assert lane["lane_id"] == "middle_1"
    assert lane["incoming_movement_count"] == lane["outgoing_movement_count"] == (1 if connected else 0)
    assert report["semantic_validation"]["construction_checks_passed"] is True
    assert report["semantic_validation"]["release_checks_passed"] is connected
    evidence = validate_materialization_evidence(
        report, source_net_file=source, candidate_net_file=Path(report["candidate_net_file"])
    )
    assert evidence["status"] == ("pass" if connected else "review_required"), evidence
    forged = {**report, "candidate_sha256": "0" * 64}
    assert (
        validate_materialization_evidence(
            forged, source_net_file=source, candidate_net_file=Path(report["candidate_net_file"])
        )["status"]
        == "blocked"
    )
    if not connected:
        malformed = {**report, "semantic_validation": {**report["semantic_validation"], "new_lane_service": None}}
        assert (
            validate_materialization_evidence(
                malformed, source_net_file=source, candidate_net_file=Path(report["candidate_net_file"])
            )["status"]
            == "blocked"
        )
    else:
        from astra_sumo.core.command_runner import run_command

        routes = tmp_path / "through.rou.xml"
        routes.write_text(
            '<routes><vehicle id="through" depart="0"><route edges="in middle out"/>'
            '<stop lane="middle_1" endPos="10" duration="0"/></vehicle></routes>',
            encoding="utf-8",
        )
        trips, fcd = tmp_path / "tripinfo.xml", tmp_path / "fcd.xml"
        native = run_command(
            [
                shutil.which("sumo"),
                "-n",
                report["candidate_net_file"],
                "-r",
                str(routes),
                "--end",
                "120",
                "--step-length",
                "0.1",
                "--tripinfo-output",
                str(trips),
                "--fcd-output",
                str(fcd),
                "--no-step-log",
                "true",
            ],
            timeout_seconds=30,
        )
        assert native.status == "pass", native
        trip = ET.parse(trips).getroot().find("tripinfo")
        assert trip is not None and trip.get("id") == "through" and float(trip.get("arrival")) > 0
        observed = [
            v.get("lane")
            for step in ET.parse(fcd).getroot().findall("timestep")
            for v in step.findall("vehicle")
            if v.get("lane", "").startswith("middle_")
        ]
        assert observed and set(observed) == {"middle_1"}
    assert file_sha256(source) == digest


@pytest.mark.skipif(not shutil.which("sumo") or not shutil.which("netconvert"), reason="SUMO binaries required")
def test_release_command_preserves_review_and_unexecuted_gate_status(tmp_path):
    import json
    from astra_sumo.core.network_edit_applicability import verify_network_edit

    source = _source(tmp_path, "set_lane_count")
    digest = file_sha256(source)
    operation = dict(
        id="lane",
        operation="set_lane_count",
        status="accepted",
        params=dict(edge_id="middle", num_lanes=2),
        rationale="Review propagation regression.",
        evidence=[dict(path=str(source), sha256=digest)],
        rollback=dict(action="discard_candidate"),
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps(dict(source_net_file=str(source), source_sha256=digest, operations=[operation])), encoding="utf-8"
    )
    report = verify_network_edit(
        ledger_file=ledger,
        output_dir=tmp_path / "release",
        netconvert_binary=shutil.which("netconvert"),
        mode="release",
    )
    assert report["status"] == "review_required", report
    assert report["candidate_gates"]["execution_status"] == "not_run"


def _case():
    before = ET.fromstring(
        '<net version="1.20"><edge id="in"><lane id="in_0" allow="passenger"/></edge>'
        '<edge id="middle"><lane id="middle_0" allow="passenger"/></edge>'
        '<edge id="out"><lane id="out_0" allow="passenger"/></edge>'
        '<connection from="in" fromLane="0" to="middle" toLane="0"/>'
        '<connection from="middle" fromLane="0" to="out" toLane="0"/></net>'
    )
    after = deepcopy(before)
    ET.SubElement(after.find("edge[@id='middle']"), "lane", id="middle_1", allow="passenger")
    plan = dict(
        operation=dict(operation="set_lane_count", params=dict(edge_id="middle", num_lanes=2)),
        lane_index_mapping={"0": 0},
    )
    return before, after, plan


def _service(before, after, plan):
    from astra_sumo.core.network_edit_operations import _new_lane_service
    from astra_sumo.core.local_network_conversion import loaded_turn_permissions

    return _new_lane_service(before, after, plan, loaded_turn_permissions(before), loaded_turn_permissions(after))


def test_source_departure_boundary_does_not_require_new_incoming_connections():
    before, after, plan = _case()
    before.remove(before.find("connection"))
    after.remove(after.find("connection"))
    ET.SubElement(after, "connection", **{"from": "middle", "fromLane": "1", "to": "out", "toLane": "0"})
    result = _service(before, after, plan)
    assert result["status"] == "pass"
    assert result["lanes"][0]["source_edge_has_compatible_incoming"] is False


def test_an_xml_connection_without_class_access_does_not_count_as_service():
    before, after, plan = _case()
    after.find("edge[@id='out']/lane").set("allow", "bicycle")
    ET.SubElement(after, "connection", **{"from": "middle", "fromLane": "1", "to": "out", "toLane": "0"})
    result = _service(before, after, plan)
    assert result["status"] == "review_required"
    assert result["lanes"][0]["outgoing_movement_count"] == 0
