from __future__ import annotations

import json
from pathlib import Path

import pytest

from torii_sumo.core.reference_spatial_registration import (
    build_reference_spatial_registration,
)


def _net(path: Path, *, offset: str, projection: str, junctions: list[tuple[str, float, float]]) -> None:
    rows = "\n".join(
        f'  <junction id="{junction_id}" type="priority" x="{x}" y="{y}"/>'
        for junction_id, x, y in junctions
    )
    path.write_text(
        "\n".join(
            [
                "<net>",
                f'  <location netOffset="{offset}" projParameter="{projection}"/>',
                rows,
                "</net>",
            ]
        ),
        encoding="utf-8",
    )


def _actions(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "torii.reference_teacher_action_contracts.v1",
                "actions": [
                    {
                        "reference_id": "cluster_a_b",
                        "action_family": "bounded_conflict_core_join",
                        "teacher_action": {"absorbed_source_node_ids": ["a", "b"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_registration_uses_projected_coordinates_and_emits_aligned_views(tmp_path: Path) -> None:
    raw = tmp_path / "raw.net.xml"
    human = tmp_path / "human.net.xml"
    actions = tmp_path / "actions.json"
    _net(
        raw,
        offset="-1000,-2000",
        projection="+proj=utm +zone=32",
        junctions=[("a", 10, 20), ("b", 14, 20)],
    )
    _net(
        human,
        offset="-900,-1800",
        projection="+proj=utm +zone=32",
        junctions=[("cluster_a_b", 112, 220)],
    )
    _actions(actions)

    report = build_reference_spatial_registration(
        teacher_action_contracts_file=actions,
        raw_net_file=raw,
        teacher_net_file=human,
        output_dir=tmp_path / "out",
    )

    case = report["cases"][0]
    assert case["centroid_residual_m"] == 0
    assert case["exact_core_teaching_example"] is True
    assert case["aligned_view"]["raw_local_center"] == [12.0, 20.0]
    assert case["aligned_view"]["human_cleaned_local_center"] == [112.0, 220.0]
    assert case["aligned_view"]["projected_center"] == [1012.0, 2020.0]


def test_registration_rejects_different_projected_coordinate_systems(tmp_path: Path) -> None:
    raw = tmp_path / "raw.net.xml"
    human = tmp_path / "human.net.xml"
    actions = tmp_path / "actions.json"
    _net(raw, offset="0,0", projection="+proj=utm +zone=32", junctions=[("a", 0, 0)])
    _net(
        human,
        offset="0,0",
        projection="+proj=utm +zone=33",
        junctions=[("cluster_a_b", 0, 0)],
    )
    _actions(actions)

    with pytest.raises(ValueError, match="different projections"):
        build_reference_spatial_registration(
            teacher_action_contracts_file=actions,
            raw_net_file=raw,
            teacher_net_file=human,
            output_dir=tmp_path / "out",
        )
