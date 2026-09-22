from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.tls_reference_cleanup import build_tls_reference_cleanup_variant


def _write_network(path: Path, *, include_stale: bool = True, unsafe_stale: bool = False) -> str:
    stale_junction_type = "traffic_light" if unsafe_stale else "priority"
    stale_connection = (
        '<connection from=":stale_w0" to=":stale_c0" fromLane="0" toLane="0" '
        'tl="stale" linkIndex="7" dir="s" state="M"/>'
        if include_stale
        else ""
    )
    text = f'''<?xml version="1.0" encoding="UTF-8"?>
<net>
    <edge id=":stale_w0" function="walkingarea">
        <lane id=":stale_w0_0" allow="pedestrian"/>
    </edge>
    <edge id=":stale_c0" function="crossing">
        <lane id=":stale_c0_0" allow="pedestrian"/>
    </edge>
    <edge id="rail_in"><lane id="rail_in_0" allow="rail"/></edge>
    <edge id="rail_out"><lane id="rail_out_0" allow="rail"/></edge>
    <edge id="road_in"><lane id="road_in_0" allow="passenger"/></edge>
    <edge id="road_out"><lane id="road_out_0" allow="passenger"/></edge>
    <junction id="stale" type="{stale_junction_type}" x="10.0" y="20.0"/>
    <junction id="rail" type="rail_signal" x="30.0" y="40.0"/>
    <junction id="valid" type="traffic_light" x="50.0" y="60.0"/>
    <tlLogic id="valid" type="static" programID="0" offset="0">
        <phase duration="30" state="G"/>
    </tlLogic>
    {stale_connection}
    <connection from="rail_in" to="rail_out" fromLane="0" toLane="0"
        via=":rail_0_0" tl="rail" linkIndex="0" dir="s" state="O"/>
    <connection from="road_in" to="road_out" fromLane="0" toLane="0"
        tl="valid" linkIndex="0" dir="s" state="G"/>
</net>
'''
    path.write_text(text, encoding="utf-8")
    return text


def test_cleanup_creates_minimal_variant_and_preserves_implicit_rail_signal(tmp_path: Path) -> None:
    source = tmp_path / "source.net.xml"
    source_text = _write_network(source)
    source_sha256 = file_sha256(source)

    report = build_tls_reference_cleanup_variant(
        source,
        output_dir=tmp_path / "cleanup",
        prefix="case",
    )

    assert report["status"] == "pass"
    assert report["tls_reference_cleanup_status"] == "variant_created"
    assert report["source_preservation_status"] == "pass"
    assert report["candidate_identity_status"] == "distinct"
    assert report["semantic_preservation_status"] == "pass"
    assert report["minimal_text_patch_status"] == "pass"
    assert report["implicit_controller_ids"] == ["rail"]
    assert report["reference_counts"]["repairable_reference_count"] == 1
    assert file_sha256(source) == source_sha256

    candidate = Path(report["candidate_net_file"])
    candidate_text = candidate.read_text(encoding="utf-8")
    assert candidate_text == source_text.replace(' tl="stale" linkIndex="7"', "")
    connections = ET.parse(candidate).getroot().findall("connection")
    stale = next(connection for connection in connections if connection.attrib.get("from") == ":stale_w0")
    rail = next(connection for connection in connections if connection.attrib.get("tl") == "rail")
    valid = next(connection for connection in connections if connection.attrib.get("tl") == "valid")
    assert "tl" not in stale.attrib
    assert "linkIndex" not in stale.attrib
    assert rail.attrib["linkIndex"] == "0"
    assert valid.attrib["linkIndex"] == "0"

    overlay_root = ET.parse(report["review_overlay_file"]).getroot()
    assert {element.tag for element in overlay_root.iter()} <= {"additional", "poi", "param"}
    poi = overlay_root.find("poi")
    assert poi is not None
    assert poi.attrib["x"] == "10.0"
    assert poi.attrib["y"] == "20.0"

    plan = json.loads(Path(report["plan_file"]).read_text(encoding="utf-8"))
    operation = plan["operations"][0]
    assert operation["removed_attributes"] == {"tl": "stale", "linkIndex": "7"}
    assert operation["rollback"]["attributes"] == operation["removed_attributes"]
    manifest = json.loads(Path(report["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "pass"
    assert any(item["path"] == str(candidate.resolve()) for item in manifest["artifacts"])


def test_cleanup_blocks_missing_road_controller_without_partial_variant(tmp_path: Path) -> None:
    source = tmp_path / "unsafe.net.xml"
    _write_network(source, unsafe_stale=True)
    source_sha256 = file_sha256(source)

    report = build_tls_reference_cleanup_variant(
        source,
        output_dir=tmp_path / "cleanup",
        prefix="unsafe",
    )

    assert report["status"] == "blocked"
    assert report["tls_reference_cleanup_status"] == "blocked_unsafe_reference"
    assert report["candidate_net_file"] == ""
    assert report["reference_counts"]["blocked_reference_count"] == 1
    assert report["blocked_references"][0]["blockers"] == [
        "junction_type_not_safe:traffic_light"
    ]
    assert file_sha256(source) == source_sha256
    assert not (tmp_path / "cleanup" / "unsafe.net.xml").exists()


def test_cleanup_returns_identity_safe_when_only_explicit_and_implicit_controllers_exist(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clean.net.xml"
    _write_network(source, include_stale=False)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    stale_candidate = cleanup_dir / "clean.net.xml"
    stale_overlay = cleanup_dir / "clean.review.add.xml"
    stale_candidate.write_text("stale candidate", encoding="utf-8")
    stale_overlay.write_text("stale overlay", encoding="utf-8")

    report = build_tls_reference_cleanup_variant(
        source,
        output_dir=cleanup_dir,
        prefix="clean",
    )

    assert report["status"] == "pass"
    assert report["claim_status"] == "identity-safe"
    assert report["tls_reference_cleanup_status"] == "no_change"
    assert report["effective_net_file"] == str(source.resolve())
    assert report["candidate_sha256"] == file_sha256(source)
    assert report["review_overlay_file"] == ""
    assert not stale_candidate.exists()
    assert not stale_overlay.exists()
