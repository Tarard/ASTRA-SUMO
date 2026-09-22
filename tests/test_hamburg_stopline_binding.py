from __future__ import annotations

import hashlib
from pathlib import Path

from torii_sumo.core.digital_twin_mapping import MapLaneBinding
from torii_sumo.core.hamburg_stopline_binding import estimate_ingress_stopline_bindings


def _binding(
    *, lane: str = "current_0", edge: str = "current", position: float = 1.0
) -> MapLaneBinding:
    return MapLaneBinding(
        node_id="2349",
        map_lane_id="6",
        map_lane_type="vehicle",
        map_role="ingress",
        sumo_edge=edge,
        sumo_lane=lane,
        lane_position=position,
        distance_m=0.5,
        heading_error_deg=1.0,
        mapping_confidence="high",
        mapping_status="active",
    )


def _net(path: Path, connections: tuple[tuple[str, int, str, int], ...]) -> None:
    edges = {
        "pred": ("pred_0", 20.0),
        "other": ("other_0", 15.0),
        "current": ("current_0", 14.0),
    }
    lines = ["<net>"]
    for edge_id, (lane_id, length) in edges.items():
        lines.append(
            f'<edge id="{edge_id}" from="n0" to="n1"><lane id="{lane_id}" index="0" speed="13.9" length="{length}" shape="0,0 {length},0"/></edge>'
        )
    for from_edge, from_lane, to_edge, to_lane in connections:
        lines.append(
            f'<connection from="{from_edge}" to="{to_edge}" fromLane="{from_lane}" toLane="{to_lane}"/>'
        )
    lines.append("</net>")
    path.write_text("\n".join(lines), encoding="utf-8")


def test_estimator_moves_start_side_ingress_to_unique_predecessor(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _net(net, (("pred", 0, "current", 0),))

    result = estimate_ingress_stopline_bindings(net, (_binding(),))

    assert result.status == "pass"
    assert result.source_net_sha256 == hashlib.sha256(net.read_bytes()).hexdigest()
    assert result.bindings[0].sumo_lane == "pred_0"
    assert result.bindings[0].lane_position == 20.0
    assert result.evidence[0].connection_repair_key == ("pred", 0, "current", 0)
    assert result.evidence[0].connection_repair_evidence == "official_map_stopline"
    assert result.evidence[0].source_net_sha256 == result.source_net_sha256
    assert result.evidence[0].original_sumo_lane == "current_0"
    assert result.evidence[0].adjusted_sumo_lane == "pred_0"


def test_estimator_keeps_end_side_and_non_ingress_bindings_unchanged(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _net(net, (("pred", 0, "current", 0),))
    end_side = _binding(position=12.0)
    egress = MapLaneBinding(**{**end_side.__dict__, "map_role": "egress", "lane_position": 1.0})

    result = estimate_ingress_stopline_bindings(net, (end_side, egress))

    assert result.status == "pass"
    assert result.bindings == (end_side, egress)
    assert result.evidence == ()


def test_estimator_requires_unique_predecessor_and_tolerance(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _net(net, (("pred", 0, "current", 0), ("other", 0, "current", 0)))

    ambiguous = estimate_ingress_stopline_bindings(net, (_binding(),))
    outside = estimate_ingress_stopline_bindings(
        net,
        (_binding(position=6.0),),
        start_tolerance_m=5.0,
    )

    assert ambiguous.status == "review_required"
    assert ambiguous.bindings == (_binding(),)
    assert "found 2" in ambiguous.evidence[0].reason
    assert outside.status == "review_required"
    assert "outside" in outside.evidence[0].reason


def test_estimator_blocks_invalid_active_lane_identity(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _net(net, ())
    binding = _binding(lane="missing_0", edge="missing")

    result = estimate_ingress_stopline_bindings(net, (binding,))

    assert result.status == "blocked"
    assert result.bindings == (binding,)
    assert "invalid" in result.evidence[0].reason


def test_estimator_blocks_connection_with_invalid_predecessor_lane(tmp_path: Path) -> None:
    net = tmp_path / "source.net.xml"
    _net(net, (("missing", 0, "current", 0),))

    result = estimate_ingress_stopline_bindings(net, (_binding(),))

    assert result.status == "blocked"
    assert "predecessor" in result.evidence[0].reason
