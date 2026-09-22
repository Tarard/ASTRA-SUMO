from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_compound_plainxml import (
    EXPECTED_ABSORBED_EDGE_IDS,
    EXPECTED_CELL_NODES,
    EXPECTED_OSM_TLS_IDS,
    HamburgCompoundGeometryError,
    classification_report_sha256,
    materialize_hamburg_2394_compound_geometry_first_pass,
)


def _classification() -> dict[str, Any]:
    return {
        "schema_id": "torii.composable-intersection-archetype/v2",
        "junction_id": "2394",
        "prototype_id": "hamburg_2394_v1",
        "status": "review_required",
        "automatic_promotion_gate": "blocked",
        "classification_id": "intersection-archetype-test-2394",
        "classification": {
            "base_skeleton": "T3",
            "physical_arrangement": "compound_candidate",
            "channelization_modifiers": [
                "distributed_stopline_markers",
                "lane_fanout",
                "merge_diverge",
                "pedestrian_crossing",
                "preserved_internal_connectors",
            ],
            "control_domain": "multi_owner_single_controller_candidate",
            "movement_graph_class": "complete_no_uturn_arm_graph_with_lane_adjacency",
            "mode_and_restriction_modifiers": [
                "bicycle",
                "motor_vehicle",
                "pedestrian",
            ],
            "family": "channelized_T3_family",
        },
        "counts": {
            "raw_node_count": 8,
            "classification_join_group_count": 2,
            "physical_conflict_core_count": None,
            "owner_count_after_rebuild_candidate": 5,
            "controller_domain_count": 1,
        },
        "physical_conflict_core_status": "unknown_pending_conflict_analysis",
        "official_intersection_parts": ["0"],
        "execution_hint": {
            "strategy": "local_join_candidates_preserve_split_shared_controller",
            "classification_only": True,
            "automatic_authorization": "blocked",
            "authorization_status": "review_required",
            "controller_domain_ids": ["2394"],
            "local_join_candidate_groups": [
                ["3847369287", "757036909", "76463166"],
                ["2761334279", "757036795"],
            ],
            "preserve_owner_components": [
                ["3847369287", "757036909", "76463166"],
                ["3847369288"],
                ["759714726"],
                ["2761334279", "757036795"],
                ["3847369285"],
            ],
        },
    }


def _source_net(path: Path) -> Path:
    root = ET.Element("net")
    cell_types = {
        node_id: "traffic_light" if node_id in EXPECTED_OSM_TLS_IDS else "priority"
        for node_id in EXPECTED_CELL_NODES
    }
    cell_types.update({"guard": "traffic_light", "guard2": "priority"})
    for index, (node_id, node_type) in enumerate(sorted(cell_types.items())):
        ET.SubElement(
            root,
            "junction",
            {
                "id": node_id,
                "type": node_type,
                "x": str(index * 10),
                "y": "0",
                "incLanes": "",
                "intLanes": "",
            },
        )

    edges = {
        "-381540198#2": ("757036909", "3847369287"),
        "381540198#2": ("3847369287", "757036909"),
        "-9702435": ("757036909", "76463166"),
        "9702435": ("76463166", "757036909"),
        "9702432#1": ("2761334279", "757036795"),
        "guard-west": ("guard", "759714726"),
        "west-guard": ("759714726", "guard2"),
    }
    for index, (edge_id, (from_id, to_id)) in enumerate(edges.items()):
        edge = ET.SubElement(
            root,
            "edge",
            {"id": edge_id, "from": from_id, "to": to_id, "type": "highway.secondary"},
        )
        ET.SubElement(
            edge,
            "lane",
            {
                "id": f"{edge_id}_0",
                "index": "0",
                "speed": "13.89",
                "length": "10.0",
                "shape": f"{index * 10},0 {index * 10 + 10},0",
            },
        )

    for tls_id in sorted(EXPECTED_OSM_TLS_IDS | {"guard_tls"}):
        logic = ET.SubElement(
            root,
            "tlLogic",
            {"id": tls_id, "type": "static", "programID": "0", "offset": "0"},
        )
        ET.SubElement(logic, "phase", {"duration": "60", "state": "G"})
    for tls_id in sorted(EXPECTED_OSM_TLS_IDS):
        ET.SubElement(
            root,
            "connection",
            {
                "from": "guard-west",
                "to": "west-guard",
                "fromLane": "0",
                "toLane": "0",
                "via": f":{tls_id}_0_0",
                "tl": tls_id,
                "linkIndex": "0",
                "dir": "s",
                "state": "O",
            },
        )
    ET.SubElement(
        root,
        "connection",
        {
            "from": "guard-west",
            "to": "west-guard",
            "fromLane": "0",
            "toLane": "0",
            "via": ":guard_0_0",
            "tl": "guard_tls",
            "linkIndex": "0",
            "dir": "s",
            "state": "O",
        },
    )
    ET.indent(root, space="    ")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _fake_plain_export(
    *,
    net_file: Path,
    output_dir: Path,
    prefix: str,
    **_kwargs: Any,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source = ET.parse(net_file).getroot()
    paths = {
        "raw_node_file": output_dir / f"{prefix}.nod.xml",
        "raw_edge_file": output_dir / f"{prefix}.edg.xml",
        "raw_connection_file": output_dir / f"{prefix}.con.xml",
        "raw_type_file": output_dir / f"{prefix}.typ.xml",
        "raw_tllogic_file": output_dir / f"{prefix}.tll.xml",
    }
    nodes = ET.Element("nodes")
    for junction in source.findall("junction"):
        ET.SubElement(
            nodes,
            "node",
            {
                key: value
                for key, value in junction.attrib.items()
                if key in {"id", "type", "x", "y"}
            },
        )
    edges = ET.Element("edges")
    for edge in source.findall("edge"):
        edges.append(copy.deepcopy(edge))
    connections = ET.Element("connections")
    tllogics = ET.Element("tlLogics")
    for logic in source.findall("tlLogic"):
        tllogics.append(copy.deepcopy(logic))
    for connection in source.findall("connection"):
        geometric = copy.deepcopy(connection)
        for attribute in ("via", "tl", "linkIndex", "dir", "state"):
            geometric.attrib.pop(attribute, None)
        connections.append(geometric)
        tllogics.append(
            ET.Element(
                "connection",
                {
                    key: connection.attrib[key]
                    for key in ("from", "to", "fromLane", "toLane", "tl", "linkIndex")
                },
            )
        )
    types = ET.Element("types")
    for path, root in (
        (paths["raw_node_file"], nodes),
        (paths["raw_edge_file"], edges),
        (paths["raw_connection_file"], connections),
        (paths["raw_type_file"], types),
        (paths["raw_tllogic_file"], tllogics),
    ):
        ET.indent(root, space="    ")
        ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return {"status": "pass", **{key: str(value) for key, value in paths.items()}}


class _FakeNetconvert:
    def __init__(self, *, remove_extra_edge: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.remove_extra_edge = remove_extra_edge

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        del cwd, timeout_seconds
        self.calls.append(command)
        node_file = Path(command[command.index("--node-files") + 1])
        edge_file = Path(command[command.index("--edge-files") + 1])
        tllogic_file = Path(command[command.index("--tllogic-files") + 1])
        output_file = Path(command[command.index("--output-file") + 1])
        joined_output = Path(command[command.index("--junctions.join-output") + 1])

        node_root = ET.parse(node_file).getroot()
        edge_root = ET.parse(edge_file).getroot()
        tllogic_root = ET.parse(tllogic_file).getroot()
        joined_nodes = {
            node_id
            for join in node_root.findall("join")
            for node_id in join.attrib.get("nodes", "").split()
        }
        result = ET.Element("net")
        for edge in edge_root.findall("edge"):
            edge_id = edge.attrib.get("id", "")
            if edge_id in EXPECTED_ABSORBED_EDGE_IDS or edge_id == self.remove_extra_edge:
                continue
            result.append(copy.deepcopy(edge))
        for logic in tllogic_root.findall("tlLogic"):
            result.append(copy.deepcopy(logic))
        for node in node_root.findall("node"):
            if node.attrib.get("id", "") in joined_nodes:
                continue
            ET.SubElement(
                result,
                "junction",
                {
                    "id": node.attrib.get("id", ""),
                    "type": node.attrib.get("type", "priority"),
                    "x": node.attrib.get("x", "0"),
                    "y": node.attrib.get("y", "0"),
                    "incLanes": "",
                    "intLanes": "",
                },
            )
        for join in node_root.findall("join"):
            ids = sorted(join.attrib.get("nodes", "").split())
            ET.SubElement(
                result,
                "junction",
                {
                    "id": f"cluster_{'_'.join(ids)}",
                    "type": "priority",
                    "x": "0",
                    "y": "0",
                    "incLanes": "",
                    "intLanes": "",
                },
            )
        ET.indent(result, space="    ")
        ET.ElementTree(result).write(output_file, encoding="utf-8", xml_declaration=True)
        joined_output.write_text("<nodes/>\n", encoding="utf-8")
        return {"status": "pass", "returncode": 0, "command": command}


@pytest.mark.parametrize("classification_as_file", [False, True])
def test_materializer_binds_mapping_or_file_and_absorbs_only_authorized_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    classification_as_file: bool,
) -> None:
    source = _source_net(tmp_path / "source.net.xml")
    report = _classification()
    if classification_as_file:
        report_input: dict[str, Any] | Path = tmp_path / "classification.json"
        report_input.write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        report_input = report
    fake_netconvert = _FakeNetconvert()
    monkeypatch.setattr(
        "torii_sumo.core.hamburg_compound_plainxml.export_plain_net_for_teacher_guided_repair",
        _fake_plain_export,
    )

    manifest = materialize_hamburg_2394_compound_geometry_first_pass(
        source_net_file=source,
        classification_report=report_input,
        accepted_classification_id=report["classification_id"],
        expected_source_sha256=file_sha256(source),
        expected_classification_sha256=classification_report_sha256(report_input),
        output_dir=tmp_path / "candidate",
        netconvert_binary="netconvert-test",
        command_runner=fake_netconvert,
    )

    assert manifest["status"] == "review_ready"
    assert manifest["automatic_promotion_gate"] == "blocked"
    assert manifest["gates"]["authorized_absorbed_edge_scope"] == "pass"
    assert {
        row["edge_id"] for row in manifest["authorized_absorbed_edges"]
    } == EXPECTED_ABSORBED_EDGE_IDS
    assert manifest["targeted_osm_tls_retirement"]["tllogic_policy"][
        "removed_program_ids"
    ] == sorted(EXPECTED_OSM_TLS_IDS, key=int)
    assert manifest["final_official_tls_restoration"] == {
        "status": "not_run",
        "required_next_stage": True,
    }
    assert file_sha256(source) == manifest["source"]["sha256_before"]
    assert len(fake_netconvert.calls) == 1


def test_materializer_rejects_unaccepted_group_or_control_domain_before_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_net(tmp_path / "source.net.xml")
    report = _classification()
    report["execution_hint"]["local_join_candidate_groups"][0] = [
        "3847369287",
        "757036909",
    ]
    report["classification"]["control_domain"] = "one_owner_one_controller"
    export_called = False

    def unexpected_export(**_kwargs: Any) -> dict[str, Any]:
        nonlocal export_called
        export_called = True
        return {"status": "fail"}

    monkeypatch.setattr(
        "torii_sumo.core.hamburg_compound_plainxml.export_plain_net_for_teacher_guided_repair",
        unexpected_export,
    )
    with pytest.raises(HamburgCompoundGeometryError, match="control_domain"):
        materialize_hamburg_2394_compound_geometry_first_pass(
            source_net_file=source,
            classification_report=report,
            accepted_classification_id=report["classification_id"],
            expected_source_sha256=file_sha256(source),
            expected_classification_sha256=classification_report_sha256(report),
            output_dir=tmp_path / "candidate",
        )
    assert export_called is False


def test_materializer_rejects_source_or_classification_hash_mismatch(
    tmp_path: Path,
) -> None:
    source = _source_net(tmp_path / "source.net.xml")
    report = _classification()
    with pytest.raises(HamburgCompoundGeometryError, match="source network hash mismatch"):
        materialize_hamburg_2394_compound_geometry_first_pass(
            source_net_file=source,
            classification_report=report,
            accepted_classification_id=report["classification_id"],
            expected_source_sha256="0" * 64,
            expected_classification_sha256=classification_report_sha256(report),
            output_dir=tmp_path / "candidate-source",
        )
    with pytest.raises(HamburgCompoundGeometryError, match="classification report hash mismatch"):
        materialize_hamburg_2394_compound_geometry_first_pass(
            source_net_file=source,
            classification_report=report,
            accepted_classification_id=report["classification_id"],
            expected_source_sha256=file_sha256(source),
            expected_classification_sha256="0" * 64,
            output_dir=tmp_path / "candidate-classification",
        )


def test_materializer_blocks_netconvert_that_removes_one_extra_plain_edge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_net(tmp_path / "source.net.xml")
    report = _classification()
    monkeypatch.setattr(
        "torii_sumo.core.hamburg_compound_plainxml.export_plain_net_for_teacher_guided_repair",
        _fake_plain_export,
    )

    manifest = materialize_hamburg_2394_compound_geometry_first_pass(
        source_net_file=source,
        classification_report=report,
        accepted_classification_id=report["classification_id"],
        expected_source_sha256=file_sha256(source),
        expected_classification_sha256=classification_report_sha256(report),
        output_dir=tmp_path / "candidate",
        command_runner=_FakeNetconvert(remove_extra_edge="guard-west"),
    )

    assert manifest["status"] == "blocked"
    assert manifest["gates"]["authorized_absorbed_edge_scope"] == "fail"
    assert manifest["audits"]["absorbed_edge_scope"]["unexpected_removed_edge_ids"] == [
        "guard-west"
    ]
