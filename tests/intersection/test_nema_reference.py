import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.command_runner import CommandResult
from torii_sumo.intersection.nema_reference import (
    NEMA_PARAMS,
    _audit_nema,
    build_nema_four_way_reference,
)
from torii_sumo.tools.intersection_tools import sumo_nema_four_way_reference_workflow


def _fake_netconvert_from_plain(command: list[str]) -> None:
    node_file = Path(command[command.index("-n") + 1])
    tllogic_file = Path(command[command.index("--tllogic-files") + 1])
    net_file = Path(command[command.index("-o") + 1])

    root = ET.Element("net")
    for node in ET.parse(node_file).getroot().findall("node"):
        ET.SubElement(root, "junction", id=node.attrib["id"], type=node.attrib.get("type", "priority"))
    for logic in ET.parse(tllogic_file).getroot().findall("tlLogic"):
        root.append(logic)
    for connection in ET.parse(tllogic_file).getroot().findall("connection"):
        attrs = dict(connection.attrib)
        attrs.setdefault("dir", {"0": "r", "1": "s", "2": "l"}[attrs["fromLane"]])
        ET.SubElement(root, "connection", **attrs)
    ET.ElementTree(root).write(net_file, encoding="utf-8", xml_declaration=True)


def _fake_runner(command: list[str], **_kwargs) -> CommandResult:
    if command[0] == "netconvert":
        _fake_netconvert_from_plain(command)
    return CommandResult(command=command, cwd=None, status="pass", returncode=0)


def test_build_nema_four_way_reference_writes_dual_ring_nema_artifacts(tmp_path: Path) -> None:
    report = build_nema_four_way_reference(
        tmp_path,
        command_runner=_fake_runner,
        which_func=lambda name: name,
        run_sumo_smoke=False,
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "diagnostic-demo"
    assert report["nema_params"]["total-cycle-length"] == "90"
    assert "cycle-length" not in report["nema_params"]
    assert report["controlled_link_count"] == 12
    assert report["tls_signal_group_count"] == 8
    assert Path(report["net_file"]).is_file()
    assert Path(report["sumocfg_file"]).is_file()

    config = ET.parse(report["sumocfg_file"]).getroot()
    config_paths = {
        "net-file": config.find("input/net-file").attrib["value"],
        "route-files": config.find("input/route-files").attrib["value"],
        "summary-output": config.find("output/summary-output").attrib["value"],
        "tripinfo-output": config.find("output/tripinfo-output").attrib["value"],
    }
    assert config_paths == {
        "net-file": "nema_four_way_reference.net.xml",
        "route-files": "nema_four_way_reference.rou.xml",
        "summary-output": "nema_four_way_reference_summary.xml",
        "tripinfo-output": "nema_four_way_reference_tripinfo.xml",
    }
    assert all(not Path(value).is_absolute() for value in config_paths.values())

    logic = ET.parse(report["additional_file"]).getroot().find("tlLogic")
    assert logic is not None
    assert logic.attrib["type"] == "NEMA"
    assert logic.attrib["programID"] == "NEMA"
    assert {param.attrib["key"]: param.attrib["value"] for param in logic.findall("param")} == NEMA_PARAMS
    assert [phase.attrib["name"] for phase in logic.findall("phase")] == [str(i) for i in range(1, 9)]
    assert {len(phase.attrib["state"]) for phase in logic.findall("phase")} == {8}

    audit = json.loads(Path(report["audit_file"]).read_text(encoding="utf-8"))
    assert audit["phase_order"] == [str(i) for i in range(1, 9)]
    assert {row["linkIndex"] for row in audit["movement_map"]} == set(range(8))
    phase_by_movement = {
        (row["from"], row["turn"]): int(row["nemaPhase"])
        for row in audit["movement_map"]
    }
    assert phase_by_movement[("W_in", "l")] == 5
    assert phase_by_movement[("W_in", "s")] == 2
    assert phase_by_movement[("E_in", "l")] == 1
    assert phase_by_movement[("E_in", "s")] == 6
    assert phase_by_movement[("S_in", "l")] == 7
    assert phase_by_movement[("N_in", "l")] == 3
    json.dumps(report)


@pytest.mark.parametrize(
    "mutation",
    ("short_state", "extra_green", "bad_timing", "missing_connection", "no_connections", "bogus_destination"),
)
def test_audit_rejects_compiled_nema_mutations_with_valid_additional_template(tmp_path: Path, mutation: str) -> None:
    report = build_nema_four_way_reference(
        tmp_path,
        command_runner=_fake_runner,
        which_func=lambda name: name,
        run_sumo_smoke=False,
    )
    net_path = Path(report["net_file"])
    additional_path = Path(report["additional_file"])
    net_root = ET.parse(net_path).getroot()
    logic = net_root.find("tlLogic[@id='J0']")
    assert logic is not None
    controlled = [connection for connection in net_root.findall("connection") if connection.attrib.get("tl") == "J0"]

    if mutation == "short_state":
        logic.findall("phase")[0].set("state", "rrrrrrr")
    elif mutation == "extra_green":
        logic.findall("phase")[0].set("state", "GGrrrrrr")
    elif mutation == "bad_timing":
        logic.findall("phase")[0].set("maxDur", "999")
    elif mutation == "missing_connection":
        net_root.remove(controlled[0])
    elif mutation == "no_connections":
        for connection in controlled:
            net_root.remove(connection)
    else:
        controlled[0].set("to", "bogus")
    ET.ElementTree(net_root).write(net_path, encoding="utf-8", xml_declaration=True)

    with pytest.raises(AssertionError):
        _audit_nema(net_path, additional_path)


def test_sumo_nema_four_way_reference_workflow_returns_json_report(monkeypatch, tmp_path: Path) -> None:
    from torii_sumo.tools import intersection_tools

    def fake_builder(output_dir: Path, **kwargs):
        assert output_dir == tmp_path
        assert kwargs["prefix"] == "probe"
        return {
            "status": "pass",
            "claim_status": "diagnostic-demo",
            "workflow": "nema_four_way_reference",
            "net_file": str(tmp_path / "probe.net.xml"),
            "sumocfg_file": str(tmp_path / "probe.sumocfg"),
        }

    monkeypatch.setattr(intersection_tools, "build_nema_four_way_reference", fake_builder)

    report = sumo_nema_four_way_reference_workflow(
        output_dir=str(tmp_path),
        prefix="probe",
        run_sumo_smoke=False,
    )

    assert report["status"] == "pass"
    assert report["workflow"] == "nema_four_way_reference"
    json.dumps(report)
