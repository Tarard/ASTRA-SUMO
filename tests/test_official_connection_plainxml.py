from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from torii_sumo.road_network.official_connection_plainxml import (
    OFFICIAL_CONNECTION_PLAINXML_SCHEMA,
    OfficialConnectionPlainXmlError,
    materialize_hamburg_official_connection_plainxml,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ROOT = (
    REPO_ROOT
    / "artifacts"
    / "hamburg_sandtorkai_twin_20260719"
    / "official_first_named_corridor_v1"
)


def _real_inputs() -> dict[str, Path]:
    workflow = REAL_ROOT / "workflow"
    return {
        "lane_transition_graph_file": (
            workflow / "w2_official_map_partial_v2" / "2349_2394_official_lane_transition_graph_v2.json"
        ),
        "edges_file": workflow / "w1_official_plainxml_v4" / "named_corridor_official.edg.xml",
        "plainxml_manifest_file": (
            workflow / "w1_official_plainxml_v4" / "named_corridor_official.manifest.json"
        ),
    }


pytestmark = pytest.mark.skipif(
    not all(path.is_file() for path in _real_inputs().values()),
    reason="requires untracked Hamburg official transition workflow artifacts",
)


def test_real_authorized_transition_writes_only_official_lane_pairs(tmp_path: Path) -> None:
    result = materialize_hamburg_official_connection_plainxml(
        **_real_inputs(),
        output_dir=tmp_path,
    )

    assert result["schema"] == OFFICIAL_CONNECTION_PLAINXML_SCHEMA
    assert result["status"] == "blocked"
    assert result["human_action_required"] is False
    assert result["network_materialization_performed"] is False
    assert result["counts"] == {
        "transition_count": 2,
        "authorized_transition_count": 1,
        "abstained_transition_count": 1,
        "connection_count": 2,
        "delete_count": 4,
    }

    root = ET.parse(result["connection_file"]).getroot()
    rows = [
        (element.tag, element.get("fromLane"), element.get("toLane"))
        for element in root
    ]
    assert [row for row in rows if row[0] == "connection"] == [
        ("connection", "0", "1"),
        ("connection", "1", "2"),
    ]
    assert ("delete", "0", "0") in rows
    assert ("delete", "1", "1") in rows
    assert all(not (tag == "connection" and to_lane == "0") for tag, _, to_lane in rows)

    manifest = json.loads(Path(result["manifest_file"]).read_text(encoding="utf-8"))
    assert manifest["schema"] == result["schema"]
    assert manifest["artifacts"] == result["artifacts"]
    assert manifest["counts"] == result["counts"]


def test_transition_graph_edge_hash_is_a_hard_gate(tmp_path: Path) -> None:
    inputs = _real_inputs()
    edited_graph = tmp_path / "edited-graph.json"
    graph = json.loads(inputs["lane_transition_graph_file"].read_text(encoding="utf-8"))
    graph["inputs"]["edges"]["sha256"] = "0" * 64
    edited_graph.write_text(json.dumps(graph), encoding="utf-8")

    with pytest.raises(OfficialConnectionPlainXmlError, match="edges hash"):
        materialize_hamburg_official_connection_plainxml(
            lane_transition_graph_file=edited_graph,
            edges_file=inputs["edges_file"],
            plainxml_manifest_file=inputs["plainxml_manifest_file"],
            output_dir=tmp_path / "out",
        )
