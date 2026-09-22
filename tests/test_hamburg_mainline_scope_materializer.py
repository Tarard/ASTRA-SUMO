from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.core.hamburg_mainline_scope_materializer import (
    ENTRY_JOIN_GROUPS,
    HAMBURG_ENTRY_SCOPE_PROFILE,
    HAMBURG_MAINLINE_SCOPE_PROFILE,
    JOIN_GROUPS,
    MAINLINE_ROUTE_EDGE_IDS,
    HamburgMainlineScopeError,
    _ordered_route_gate,
    _write_join_patch,
    materialize_hamburg_sandtorkai_mainline_scope_candidate,
    select_hamburg_mainline_scope_edges,
)


def test_mainline_profile_patch_contains_only_the_proven_2394_join(tmp_path: Path) -> None:
    patch = tmp_path / "mainline.nod.xml"
    _write_join_patch(patch)
    root = ET.parse(patch).getroot()
    assert [tuple(item.attrib["nodes"].split()) for item in root.findall("join")] == list(JOIN_GROUPS)


def test_entry_profile_patch_keeps_two_carriageway_specific_groser_grasbrook_cores(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "entry.nod.xml"
    _write_join_patch(patch, join_groups=ENTRY_JOIN_GROUPS)
    root = ET.parse(patch).getroot()
    assert [tuple(item.attrib["nodes"].split()) for item in root.findall("join")] == list(
        ENTRY_JOIN_GROUPS
    )
    assert HAMBURG_ENTRY_SCOPE_PROFILE != HAMBURG_MAINLINE_SCOPE_PROFILE


def test_mainline_profile_rejects_a_different_source_network(tmp_path: Path) -> None:
    source = tmp_path / "other.net.xml"
    source.write_text("<net/>\n", encoding="utf-8")
    with pytest.raises(HamburgMainlineScopeError, match="missing backbone edges"):
        select_hamburg_mainline_scope_edges(source)


def test_mainline_profile_fails_closed_on_missing_backbone(tmp_path: Path) -> None:
    source = tmp_path / "other.net.xml"
    source.write_text("<net/>\n", encoding="utf-8")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(HamburgMainlineScopeError, match="missing backbone edges"):
        materialize_hamburg_sandtorkai_mainline_scope_candidate(
            source_net_file=source,
            expected_source_sha256=expected,
            output_dir=tmp_path / "out",
            profile=HAMBURG_MAINLINE_SCOPE_PROFILE,
        )


def test_mainline_profile_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "other.net.xml"
    source.write_text("<net/>\n", encoding="utf-8")
    with pytest.raises(HamburgMainlineScopeError, match="SHA-256 mismatch"):
        materialize_hamburg_sandtorkai_mainline_scope_candidate(
            source_net_file=source,
            expected_source_sha256="0" * 64,
            output_dir=tmp_path / "out",
            profile=HAMBURG_MAINLINE_SCOPE_PROFILE,
        )


def test_mainline_route_gate_reports_broken_ordered_connection() -> None:
    connections = {
        source: {target}
        for source, target in zip(MAINLINE_ROUTE_EDGE_IDS, MAINLINE_ROUTE_EDGE_IDS[1:])
    }
    connections.pop(MAINLINE_ROUTE_EDGE_IDS[4])
    report = _ordered_route_gate(connections, set(MAINLINE_ROUTE_EDGE_IDS))
    assert report["status"] == "fail"
    assert [MAINLINE_ROUTE_EDGE_IDS[4], MAINLINE_ROUTE_EDGE_IDS[5]] in report["broken_links"]
