from __future__ import annotations

from pathlib import Path

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.directed_corridor_routeability import (
    audit_directed_corridor_routeability,
    corridor_audit_to_movement_binding,
)


def test_directed_corridor_audit_passes_and_builds_smoke_binding(
    tmp_path: Path,
) -> None:
    net_file = _write_net(tmp_path / "corridor.net.xml")

    audit = _audit(net_file)

    assert audit["status"] == "pass"
    assert audit["net_sha256"] == file_sha256(net_file)
    assert audit["forward"]["lane_path"] == [
        "e0_0",
        "e1_0",
        "e2_0",
        "e3_0",
        "e4_0",
    ]
    assert audit["forward"]["edge_path"] == ["e0", "e1", "e2", "e3", "e4"]
    assert audit["forward"]["controller_order"] == ["A", "B"]
    assert audit["reverse_proof"]["complete"] is True
    assert audit["reverse_proof"]["expected_order_path_found"] is False

    binding = corridor_audit_to_movement_binding(audit)

    assert binding["binding_status"] == "pass"
    assert binding["net_sha256"] == file_sha256(net_file)
    assert binding["movement_records"] == [
        {
            "stable_movement_id": "directed_corridor_forward",
            "edge_ids": ["e0", "e1", "e2", "e3", "e4"],
            "from_lane_index": 0,
            "to_lane_index": 0,
            "controller_binding_status": "pass",
            "controller_order": ["A", "B"],
            "controlled_arc_keys": [
                ["e0_0", "e1_0", "A", 0],
                ["e1_0", "e2_0", "A", 1],
                ["e2_0", "e3_0", "B", 0],
            ],
        }
    ]


def test_consecutive_duplicate_owner_arcs_collapse_into_one_block(
    tmp_path: Path,
) -> None:
    net_file = _write_net(tmp_path / "corridor.net.xml")

    audit = _audit(net_file)

    blocks = audit["forward"]["controller_blocks"]
    assert audit["status"] == "pass"
    assert [block["owner"] for block in blocks] == ["A", "B"]
    assert len(blocks[0]["controlled_arcs"]) == 2
    assert blocks[0]["first_path_arc_index"] == 0
    assert blocks[0]["last_path_arc_index"] == 1


def test_wrong_forward_controller_order_fails(tmp_path: Path) -> None:
    net_file = _write_net(tmp_path / "corridor.net.xml")

    audit = _audit(net_file, expected_forward_controller_order=("B", "A"))

    assert audit["status"] == "fail"
    assert audit["forward"]["controller_order"] == ["A", "B"]
    assert any("controller order mismatch" in error for error in audit["errors"])


def test_missing_forward_controller_fails(tmp_path: Path) -> None:
    net_file = _write_net(
        tmp_path / "corridor.net.xml",
        forward_tls=("A", "A", None, None),
    )

    audit = _audit(
        net_file,
        validated_controlled_arc_keys={
            ("e0_0", "e1_0", "A", 0): "movement-01",
            ("e1_0", "e2_0", "A", 1): "movement-02",
        },
    )

    assert audit["status"] == "fail"
    assert audit["forward"]["controller_order"] == ["A"]
    assert any("controller order mismatch" in error for error in audit["errors"])


def test_construction_edge_cannot_satisfy_forward_corridor(tmp_path: Path) -> None:
    net_file = _write_net(
        tmp_path / "corridor.net.xml",
        edge_types={"e2": "highway.construction"},
    )

    audit = _audit(net_file)

    assert audit["status"] == "fail"
    assert audit["forward"]["path_count"] == 0
    assert {item["lane_id"] for item in audit["blocked_lanes"]} == {"e2_0"}
    assert "construction/closed" in audit["blocked_lanes"][0]["reason"]


def test_existing_reverse_path_with_expected_owner_order_fails(tmp_path: Path) -> None:
    net_file = _write_net(
        tmp_path / "corridor.net.xml",
        reverse_tls=("B", "A"),
    )

    audit = _audit(net_file)

    assert audit["status"] == "fail"
    assert audit["reverse_proof"]["complete"] is False
    assert audit["reverse_proof"]["expected_order_path_found"] is True
    assert audit["reverse_proof"]["witness"]["lane_path"] == [
        "r0_0",
        "r1_0",
        "r2_0",
    ]
    assert any("forbidden reverse path" in error for error in audit["errors"])


def test_missing_reverse_boundary_lane_is_a_failure(tmp_path: Path) -> None:
    net_file = _write_net(tmp_path / "corridor.net.xml")

    audit = _audit(
        net_file,
        reverse_boundary_lane_sets=(("missing_0",), ("r2_0",)),
    )

    assert audit["status"] == "fail"
    assert audit["reverse_proof"]["complete"] is False
    assert any("lanes are missing: missing_0" in error for error in audit["errors"])


def test_reverse_search_state_limit_is_not_an_absence_proof(tmp_path: Path) -> None:
    net_file = _write_net(
        tmp_path / "corridor.net.xml",
        reverse_tls=(None, None),
    )

    audit = _audit(net_file, max_reverse_states=1)

    assert audit["status"] == "fail"
    assert audit["reverse_proof"]["complete"] is False
    assert audit["reverse_proof"]["state_limit_exceeded"] is True
    assert any("absence is not proven" in error for error in audit["errors"])


def test_controlled_arc_must_be_present_in_movement_evidence(tmp_path: Path) -> None:
    net_file = _write_net(tmp_path / "corridor.net.xml")
    incomplete_evidence = _validated_arcs()
    incomplete_evidence.pop(("e2_0", "e3_0", "B", 0))

    audit = _audit(net_file, validated_controlled_arc_keys=incomplete_evidence)

    assert audit["status"] == "fail"
    assert audit["forward"]["unvalidated_controlled_arc_count"] == 1
    assert any("validated movement evidence" in error for error in audit["errors"])
    with pytest.raises(ValueError, match="only a passing"):
        corridor_audit_to_movement_binding(audit)


def _audit(net_file: Path, **overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "expected_net_sha256": file_sha256(net_file),
        "forward_start_lane": "e0_0",
        "forward_target_lane": "e4_0",
        "expected_forward_controller_order": ("A", "B"),
        "reverse_boundary_lane_sets": (("r0_0",), ("r2_0",)),
        "expected_reverse_controller_order": ("B", "A"),
        "validated_controlled_arc_keys": _validated_arcs(),
    }
    arguments.update(overrides)
    return audit_directed_corridor_routeability(net_file, **arguments)  # type: ignore[arg-type]


def _validated_arcs() -> dict[tuple[str, str, str, int], str]:
    return {
        ("e0_0", "e1_0", "A", 0): "movement-01",
        ("e1_0", "e2_0", "A", 1): "movement-02",
        ("e2_0", "e3_0", "B", 0): "movement-03",
    }


def _write_net(
    path: Path,
    *,
    forward_tls: tuple[str | None, ...] = ("A", "A", "B", None),
    reverse_tls: tuple[str | None, ...] = (None,),
    edge_types: dict[str, str] | None = None,
) -> Path:
    edge_types = edge_types or {}
    edge_rows = []
    for edge_id in ("e0", "e1", "e2", "e3", "e4", "r0", "r1", "r2"):
        type_attribute = f' type="{edge_types[edge_id]}"' if edge_id in edge_types else ""
        edge_rows.append(
            f'<edge id="{edge_id}"{type_attribute}>'
            f'<lane id="{edge_id}_0" index="0" allow="passenger" length="10"/>'
            "</edge>"
        )
    connection_rows = []
    for index, tls_id in enumerate(forward_tls):
        attributes = f' tl="{tls_id}" linkIndex="{index if tls_id == "A" else 0}"' if tls_id else ""
        connection_rows.append(f'<connection from="e{index}" to="e{index + 1}" fromLane="0" toLane="0"{attributes}/>')
    for index, tls_id in enumerate(reverse_tls):
        attributes = f' tl="{tls_id}" linkIndex="{index}"' if tls_id else ""
        connection_rows.append(f'<connection from="r{index}" to="r{index + 1}" fromLane="0" toLane="0"{attributes}/>')
    path.write_text(
        "<net>" + "".join(edge_rows) + "".join(connection_rows) + "</net>",
        encoding="utf-8",
    )
    return path
