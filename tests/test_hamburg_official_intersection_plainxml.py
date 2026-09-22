from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pyproj import Transformer

from torii_sumo.core.digital_twin import MapConnection, MapLane
from torii_sumo.core.ocit_c import OcitVehicleTopologyInventory, OcitVehicleTopologyMovement

from torii_sumo.core.hamburg_official_intersection_plainxml import (
    HamburgOfficialIntersectionPlainXmlError,
    _validate_single_core_layout_profile,
    _build_plan,
    _write_edges,
    _write_nodes,
    _write_connections,
    _write_tllogic,
    _write_types,
    materialize_hamburg_official_intersection_plainxml,
)


@pytest.mark.parametrize("bits,expected_allow,review", [("10010000", "bus", False), ("00001000", "taxi", False), ("00000000", "passenger taxi bus coach delivery truck motorcycle emergency", False), ("00000100", None, True), ("00100000", None, True), ("", None, True)])
def test_legacy_plainxml_keeps_explicit_lane_permissions(tmp_path, bits, expected_allow, review):
    inverse = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    def geo(point):
        return list(inverse.transform(500000 + point[0], 5900000 + point[1]))
    specification = [("1", "ingress", "1", [(-30, 3.2), (-5, 3.2)]), ("2", "ingress", "1", [(-30, 0), (-5, 0)]), ("3", "egress", "2", [(5, 0), (30, 0)])]
    lanes, rows = [], []
    for lane_id, role, approach, points in specification:
        a, b = (points[0], points[-1]) if role == "ingress" else (points[-1], points[0])
        lanes.append(MapLane("7", lane_id, "vehicle", approach if role == "ingress" else "", approach if role == "egress" else "", 9, 53, (), bits if lane_id == "1" else "00000000"))
        rows.append({"lane_id": lane_id, "ingress_approach": approach if role == "ingress" else "", "egress_approach": approach if role == "egress" else "", "kml_direction_role": role, "coordinates": [geo(point) for point in points], "endpoint_a": geo(a), "endpoint_b": geo(b)})
    connections = [MapConnection("7", lane_id, lane_id, "3", "1", "") for lane_id in ("1", "2")]
    movements = tuple(OcitVehicleTopologyMovement("7", lane_id, lane_id, "3", "1", ("1",), (), "P_1__S_NONE", (), ()) for lane_id in ("1", "2"))
    inventory = OcitVehicleTopologyInventory("pass", 2, 0, 2, 0, 0, "test", movements, ())
    binding = {"node_id": "7", "lanes": rows, "connections": [{"connection_id": lane_id, "connection_coordinates": [rows[int(lane_id) - 1]["endpoint_b"], rows[2]["endpoint_b"]], "drive_line_coordinates": [rows[int(lane_id) - 1]["endpoint_b"], rows[2]["endpoint_b"]], "drive_line_variant": "test"} for lane_id in ("1", "2")]}
    plan = _build_plan(binding=binding, map_lanes=lanes, map_connections=connections, inventory=inventory, projection_crs="EPSG:25832", geometry_tolerance_m=0.25, structural_speed_mps=13.89, structural_lane_width_m=3.2)
    output = tmp_path / "edges.xml"
    _write_edges(output, plan)
    root = ET.parse(output).getroot()
    def compiled(lane_id):
        identity = plan["lane_mapping"][lane_id]
        return root.find(f"edge[@id='{identity['edge_id']}']/lane[@index='{identity['lane_index']}']")
    assert compiled("1").get("allow") == expected_allow
    assert compiled("1").get("disallow") == ("all" if review else None)
    assert compiled("2").get("allow") == "passenger taxi bus coach delivery truck motorcycle emergency"
    assert plan["permission_review_lane_ids"] == (["1"] if review else [])
    assert plan["lane_mapping"]["1"]["vehicle_attribute_bits"] == bits
    if bits == "10010000" and shutil.which("netconvert"):
        nodes, connections_file, lights, types = (tmp_path / name for name in ("nodes.xml", "connections.xml", "lights.xml", "types.xml"))
        _write_nodes(nodes, plan)
        _write_connections(connections_file, plan)
        _write_tllogic(lights, plan)
        _write_types(types, plan)
        net = tmp_path / "network.net.xml"
        built = subprocess.run([shutil.which("netconvert"), "-n", str(nodes), "-e", str(output), "-x", str(connections_file), "--tllogic-files", str(lights), "-t", str(types), "-o", str(net)], capture_output=True, text=True, timeout=30)
        assert built.returncode == 0, built.stderr
        compiled_root = ET.parse(net).getroot()
        bus_id = plan["lane_mapping"]["1"]["sumo_lane_id"]
        ordinary_id = plan["lane_mapping"]["2"]["sumo_lane_id"]
        assert compiled_root.find(f".//lane[@id='{bus_id}']").get("allow") == "bus"
        assert "passenger" in compiled_root.find(f".//lane[@id='{ordinary_id}']").get("allow").split()


def _missing_inputs(tmp_path: Path, node_id: str) -> dict[str, Path]:
    return {
        "map_xml_file": tmp_path / f"missing-{node_id}-map.xml",
        "map_kml_file": tmp_path / f"missing-{node_id}-map.kml",
        "ocit_c_file": tmp_path / f"missing-{node_id}-ocit.xml",
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _classification(
    tmp_path: Path,
    node_id: str,
    *,
    physical_arrangement: str = "single_core",
    control_domain: str = "one_owner_one_controller",
    core_count: int | None = 1,
    owner_count: int = 1,
) -> tuple[Path, str, str]:
    classification_id = f"intersection-archetype-{node_id}-test"
    path = tmp_path / f"{node_id}.classification.json"
    path.write_text(
        json.dumps(
            {
                "schema_id": "torii.composable-intersection-archetype/v2",
                "junction_id": node_id,
                "classification_id": classification_id,
                "status": "classified",
                "classification": {
                    "physical_arrangement": physical_arrangement,
                    "control_domain": control_domain,
                },
                "counts": {
                    "physical_conflict_core_count": core_count,
                    "owner_count_after_rebuild_candidate": owner_count,
                    "controller_domain_count": 1,
                },
                "physical_conflict_core_status": (
                    "known" if core_count is not None else "unknown_pending_conflict_analysis"
                ),
                "execution_hint": {
                    "classification_only": True,
                    "automatic_authorization": "blocked",
                    "controller_domain_ids": [node_id],
                },
            }
        ),
        encoding="utf-8",
    )
    return path, classification_id, _digest(path)


def test_real_2394_is_rejected_by_the_single_core_builder(tmp_path: Path) -> None:
    classification, classification_id, digest = _classification(
        tmp_path,
        "2394",
        physical_arrangement="compound",
        control_domain="multi_owner_single_controller",
        core_count=2,
        owner_count=5,
    )
    output = tmp_path / "candidate"

    with pytest.raises(
        HamburgOfficialIntersectionPlainXmlError,
        match="single-core classification gate failed",
    ):
        materialize_hamburg_official_intersection_plainxml(
            **_missing_inputs(tmp_path, "2394"),
            expected_node_id="2394",
            output_dir=output,
            classification_file=classification,
            accepted_classification_id=classification_id,
            expected_classification_sha256=digest,
            compile_net=False,
        )

    assert not output.exists()


@pytest.mark.parametrize("node_id", ["2349", "2394"])
def test_compound_hamburg_nodes_cannot_enter_the_single_core_compiler(
    node_id: str,
    tmp_path: Path,
) -> None:
    classification, classification_id, digest = _classification(
        tmp_path,
        node_id,
        physical_arrangement="compound",
        control_domain="multi_owner_single_controller",
        core_count=2,
        owner_count=2 if node_id == "2349" else 5,
    )
    output = tmp_path / f"candidate-{node_id}"

    with pytest.raises(HamburgOfficialIntersectionPlainXmlError):
        materialize_hamburg_official_intersection_plainxml(
            **_missing_inputs(tmp_path, node_id),
            expected_node_id=node_id,
            output_dir=output,
            classification_file=classification,
            accepted_classification_id=classification_id,
            expected_classification_sha256=digest,
        )

    assert not output.exists()


def test_materializer_fails_before_writing_on_hash_mismatch(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    classification, classification_id, digest = _classification(tmp_path, "2394")
    inputs = {
        role: tmp_path / name
        for role, name in (
            ("map_xml_file", "map.xml"),
            ("map_kml_file", "map.kml"),
            ("ocit_c_file", "ocit.xml"),
        )
    }
    for path in inputs.values():
        path.write_text("placeholder", encoding="utf-8")
    with pytest.raises(HamburgOfficialIntersectionPlainXmlError, match="SHA-256"):
        materialize_hamburg_official_intersection_plainxml(
            **inputs,
            expected_node_id="2394",
            expected_sha256={"map_xml": "0" * 64},
            output_dir=output,
            classification_file=classification,
            accepted_classification_id=classification_id,
            expected_classification_sha256=digest,
            compile_net=False,
        )
    assert not output.exists()


def test_materializer_never_overwrites_existing_candidate_directory(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    classification, classification_id, digest = _classification(tmp_path, "2349")

    with pytest.raises(HamburgOfficialIntersectionPlainXmlError, match="must not already exist"):
        materialize_hamburg_official_intersection_plainxml(
            **_missing_inputs(tmp_path, "2349"),
            expected_node_id="2349",
            output_dir=output,
            classification_file=classification,
            accepted_classification_id=classification_id,
            expected_classification_sha256=digest,
            compile_net=False,
        )

    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_materializer_requires_a_hash_bound_single_core_classification(
    tmp_path: Path,
) -> None:
    output = tmp_path / "candidate"

    with pytest.raises(
        HamburgOfficialIntersectionPlainXmlError,
        match="requires classification_file",
    ):
        materialize_hamburg_official_intersection_plainxml(
            **_missing_inputs(tmp_path, "2349"),
            expected_node_id="2349",
            output_dir=output,
            compile_net=False,
        )

    assert not output.exists()


def test_compound_classification_fails_before_sources_without_expected_node_id(
    tmp_path: Path,
) -> None:
    classification, classification_id, digest = _classification(
        tmp_path,
        "2394",
        physical_arrangement="compound",
        control_domain="multi_owner_single_controller",
        core_count=2,
        owner_count=5,
    )

    with pytest.raises(
        HamburgOfficialIntersectionPlainXmlError,
        match="single-core classification gate failed",
    ):
        materialize_hamburg_official_intersection_plainxml(
            **_missing_inputs(tmp_path, "2394"),
            output_dir=tmp_path / "candidate",
            classification_file=classification,
            accepted_classification_id=classification_id,
            expected_classification_sha256=digest,
            compile_net=False,
        )


def test_single_core_profile_validator_accepts_only_confirmed_one_by_one_layout(
    tmp_path: Path,
) -> None:
    classification, classification_id, _digest_value = _classification(
        tmp_path,
        "example",
    )

    report = _validate_single_core_layout_profile(
        classification,
        node_id="example",
        accepted_classification_id=classification_id,
    )

    assert report["classification_id"] == classification_id


@pytest.mark.parametrize(
    ("core_count", "owner_count"),
    [(None, 1), (1, 2), (2, 1)],
)
def test_single_core_profile_validator_rejects_unknown_or_multi_core_owner_layout(
    tmp_path: Path,
    core_count: int | None,
    owner_count: int,
) -> None:
    classification, classification_id, _digest_value = _classification(
        tmp_path,
        f"case-{core_count}-{owner_count}",
        core_count=core_count,
        owner_count=owner_count,
    )

    with pytest.raises(
        HamburgOfficialIntersectionPlainXmlError,
        match="single-core classification gate failed",
    ):
        _validate_single_core_layout_profile(
            classification,
            node_id=f"case-{core_count}-{owner_count}",
            accepted_classification_id=classification_id,
        )
