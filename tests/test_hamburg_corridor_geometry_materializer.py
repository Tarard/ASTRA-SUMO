from __future__ import annotations

import xml.etree.ElementTree as ET
import hashlib
from pathlib import Path

import pytest

from torii_sumo.core.hamburg_corridor_geometry_materializer import (
    FOCUS_JUNCTION_IDS,
    HAMBURG_CORRIDOR_GEOMETRY_PROFILE,
    JOIN_GROUPS,
    SHAPE_OVERRIDES,
    HamburgCorridorGeometryMaterializationError,
    _write_profile_patch,
    materialize_hamburg_sandtorkai_geometry_safe_candidate,
)


def test_profile_patch_preserves_protected_branch_and_has_two_explicit_join_groups(tmp_path: Path) -> None:
    path = tmp_path / "profile.nod.xml"
    _write_profile_patch(path)
    root = ET.parse(path).getroot()
    assert [tuple(element.attrib["nodes"].split()) for element in root.findall("join")] == list(JOIN_GROUPS)
    nodes = {element.attrib["id"]: element.attrib for element in root.findall("node")}
    assert set(nodes) == set(SHAPE_OVERRIDES)
    assert nodes["199166130"]["tl"] == "HH_0228"
    assert "243175302" in FOCUS_JUNCTION_IDS


def test_geometry_materializer_rejects_non_v10_network(tmp_path: Path) -> None:
    net = tmp_path / "small.net.xml"
    net.write_text("<net><junction id=\"only\" type=\"priority\"/></net>\n", encoding="utf-8")
    with pytest.raises(HamburgCorridorGeometryMaterializationError, match="missing junctions"):
        materialize_hamburg_sandtorkai_geometry_safe_candidate(
            source_net_file=net,
            expected_source_sha256=hashlib.sha256(net.read_bytes()).hexdigest(),
            output_dir=tmp_path / "out",
            profile=HAMBURG_CORRIDOR_GEOMETRY_PROFILE,
        )
