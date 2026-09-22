from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from torii_sumo.road_network.official_lane_stitch import (
    OfficialLaneAxisStitchThresholds,
    plan_hamburg_official_map_lane_axis_stitch,
)
from torii_sumo.road_network.official_splice_plan import (
    OFFICIAL_SPLICE_PLAN_SCHEMA,
    OfficialSplicePlanError,
    _merge_through_lane_proof,
    _movement_destination_groups,
    build_hamburg_official_splice_plan,
)

from test_official_lane_stitch import _write_official_inputs


def _build_plan(paths: dict[str, Path], *, output: Path | None = None) -> dict[str, object]:
    return plan_hamburg_official_map_lane_axis_stitch(
        map_binding_reports=[paths["report"]],
        nodes_file=paths["nodes"],
        edges_file=paths["edges"],
        plainxml_manifest_file=paths["manifest"],
        thresholds=OfficialLaneAxisStitchThresholds(minimum_normalized_score_margin=0.25),
    )


def test_coherent_boundary_emits_directional_splice_operations(tmp_path: Path) -> None:
    paths = _write_official_inputs(tmp_path)
    # The shared fixture intentionally has two-lane axes.  Narrow it to one
    # lane per synthetic approach so this test exercises the passing branch.
    paths["edges"].write_text(
        paths["edges"].read_text(encoding="utf-8").replace('numLanes="2"', 'numLanes="1"'),
        encoding="utf-8",
    )
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["artifacts"]["edges"]["sha256"] = hashlib.sha256(paths["edges"].read_bytes()).hexdigest()
    paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
    stitch = _build_plan(paths)
    stitch_path = tmp_path / "stitch.json"
    stitch_path.write_text(json.dumps(stitch), encoding="utf-8")

    result = build_hamburg_official_splice_plan(
        map_binding_reports=[paths["report"]],
        lane_axis_stitch_plan=stitch_path,
        nodes_file=paths["nodes"],
        edges_file=paths["edges"],
        plainxml_manifest_file=paths["manifest"],
    )

    assert result["schema"] == OFFICIAL_SPLICE_PLAN_SCHEMA
    assert result["status"] == "pass"
    assert result["gates"]["network_materialization"] == "blocked"
    assert result["counts"]["axis_split_count"] == 2
    assert all(item["kind"] == "coherent_boundary" for item in (
        approach["splice_event"] for approach in result["approaches"]
    ))


def test_official_merge_coordinate_becomes_reviewed_split_event(tmp_path: Path) -> None:
    paths = _write_official_inputs(tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["lanes"][0]["merge_points"] = [[9.9902, 53.5400, 0.0]]
    paths["report"].write_text(json.dumps(report), encoding="utf-8")
    stitch = _build_plan(paths)
    stitch_path = tmp_path / "stitch.json"
    stitch_path.write_text(json.dumps(stitch), encoding="utf-8")

    result = build_hamburg_official_splice_plan(
        map_binding_reports=[paths["report"]],
        lane_axis_stitch_plan=stitch_path,
        nodes_file=paths["nodes"],
        edges_file=paths["edges"],
        plainxml_manifest_file=paths["manifest"],
    )

    merge_approaches = [
        item for item in result["approaches"]
        if item["splice_event"] and item["splice_event"]["kind"] == "official_merge_event"
    ]
    assert result["status"] == "review_required"
    assert len(merge_approaches) == 1
    assert merge_approaches[0]["splice_event"]["lane_count_before"] == 2
    assert merge_approaches[0]["splice_event"]["lane_count_after"] == 1
    assert result["gates"]["lane_conservation_and_index_assignment"] == "review_required"


def test_edited_stitch_plan_is_rejected(tmp_path: Path) -> None:
    paths = _write_official_inputs(tmp_path)
    stitch = _build_plan(paths)
    stitch["plan_id"] = "tampered"
    stitch_path = tmp_path / "stitch.json"
    stitch_path.write_text(json.dumps(stitch), encoding="utf-8")

    try:
        build_hamburg_official_splice_plan(
            map_binding_reports=[paths["report"]],
            lane_axis_stitch_plan=stitch_path,
            nodes_file=paths["nodes"],
            edges_file=paths["edges"],
            plainxml_manifest_file=paths["manifest"],
        )
    except OfficialSplicePlanError as exc:
        assert "does not exactly match" in str(exc)
    else:
        raise AssertionError("edited stitch plan was accepted")


def test_splice_identity_does_not_depend_on_checkout_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _write_official_inputs(source_dir)
    stitch_path = source_dir / "stitch.json"
    stitch_path.write_text(json.dumps(_build_plan(source)), encoding="utf-8")
    source_result = build_hamburg_official_splice_plan(
        map_binding_reports=[source["report"]],
        lane_axis_stitch_plan=stitch_path,
        nodes_file=source["nodes"],
        edges_file=source["edges"],
        plainxml_manifest_file=source["manifest"],
    )

    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    copied = {
        role: Path(shutil.copy2(path, copy_dir / path.name))
        for role, path in source.items()
    }
    copied_stitch_path = Path(shutil.copy2(stitch_path, copy_dir / stitch_path.name))
    copied_result = build_hamburg_official_splice_plan(
        map_binding_reports=[copied["report"]],
        lane_axis_stitch_plan=copied_stitch_path,
        nodes_file=copied["nodes"],
        edges_file=copied["edges"],
        plainxml_manifest_file=copied["manifest"],
    )

    assert source_result["plan_id"] == copied_result["plan_id"]
    assert source_result["inputs"]["nodes"]["path"] != copied_result["inputs"]["nodes"]["path"]
    assert source_result["inputs"]["lane_axis_stitch_plan"]["path"] != (
        copied_result["inputs"]["lane_axis_stitch_plan"]["path"]
    )


def test_merge_planner_proves_added_lane_from_destination_group() -> None:
    groups = _movement_destination_groups(
        [
            {
                "node_id": "2363",
                "lanes": [
                    {"lane_id": "6", "egress_approach": "1"},
                    {"lane_id": "7", "egress_approach": "1"},
                    {"lane_id": "1", "egress_approach": "4"},
                ],
                "connections": [
                    {"ingress_lane_id": "8", "egress_lane_id": "6"},
                    {"ingress_lane_id": "10", "egress_lane_id": "7"},
                    {"ingress_lane_id": "11", "egress_lane_id": "1"},
                ],
            }
        ]
    )

    assert _merge_through_lane_proof(
        map_lane_ids=["8", "10", "11"],
        axis_count=2,
        merge_lane_ids_starting_at_event=["10", "11"],
        destination_groups=groups["2363"],
    ) == ["8", "10"]

    assert _merge_through_lane_proof(
        map_lane_ids=["8", "10", "11"],
        axis_count=2,
        merge_lane_ids_starting_at_event=["10", "11"],
        destination_groups={
            "8": frozenset({"1", "4"}),
            "10": frozenset({"1", "4"}),
            "11": frozenset({"4"}),
        },
    ) == []


def test_merge_planner_rejects_exact_count_without_movement_provenance() -> None:
    assert _merge_through_lane_proof(
        map_lane_ids=["6", "7", "8", "9"],
        axis_count=3,
        merge_lane_ids_starting_at_event=["8"],
        destination_groups={},
    ) == []
