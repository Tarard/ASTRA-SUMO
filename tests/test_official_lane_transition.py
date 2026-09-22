from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from torii_sumo.road_network.official_lane_transition import (
    OFFICIAL_LANE_TRANSITION_GRAPH_SCHEMA,
    OfficialLaneTransitionGraphError,
    OfficialLaneTransitionThresholds,
    build_hamburg_official_lane_transition_graph,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ROOT = (
    REPO_ROOT
    / "artifacts"
    / "hamburg_sandtorkai_twin_20260719"
    / "official_first_named_corridor_v1"
)


def _real_inputs() -> dict[str, object]:
    signal_dir = REAL_ROOT / "official" / "signals"
    skeleton = REAL_ROOT / "road_skeleton"
    return {
        "map_binding_reports": [
            signal_dir / "2349_map_kml_mapem_binding.json",
            signal_dir / "2394_map_kml_mapem_binding.json",
        ],
        "lane_axis_stitch_plan": (
            REAL_ROOT
            / "stitch_plans"
            / "2349_2394_hh_sib_lane_axis_stitch_v1.json"
        ),
        "nodes_file": skeleton / "sandtorkai_official_named_corridor.nod.xml",
        "edges_file": skeleton / "sandtorkai_official_named_corridor.edg.xml",
        "plainxml_manifest_file": (
            skeleton / "sandtorkai_official_named_corridor.manifest.json"
        ),
    }


def test_real_eastbound_transition_is_unique_and_right_pocket_starts_empty() -> None:
    result = build_hamburg_official_lane_transition_graph(**_real_inputs())

    assert result["schema"] == OFFICIAL_LANE_TRANSITION_GRAPH_SCHEMA
    assert result["status"] == "review_required"
    assert result["human_action_required"] is False
    assert result["network_materialization_performed"] is False
    assert result["counts"] == {
        "direction_count": 2,
        "authorized_direction_count": 1,
        "abstained_direction_count": 1,
        "continuation_edge_count": 2,
        "continuation_identity_count": 3,
        "empty_start_added_lane_count": 1,
        "unresolved_added_lane_count": 1,
    }

    eastbound = next(
        item for item in result["transitions"] if item["station_direction"] == "with_stationing"
    )
    assert eastbound["status"] == "pass"
    assert eastbound["lane_transition_authorized"] is True
    assert [
        item["official_lane_id"] for item in eastbound["upstream_lane_order_right_to_left"]
    ] == ["5", "4"]
    assert [
        item["official_lane_id"] for item in eastbound["downstream_lane_order_right_to_left"]
    ] == ["10", "11", "12"]
    assert [
        (
            item["upstream_official_lane_id"],
            item["upstream_sumo_lane_index"],
            item["downstream_official_lane_id"],
            item["downstream_sumo_lane_index"],
        )
        for item in eastbound["continuation_edges"]
    ] == [("5", 0, "11", 1), ("4", 1, "12", 2)]

    assert eastbound["added_lanes"] == [
        {
            "downstream_official_lane_id": "10",
            "downstream_sumo_lane_index": 0,
            "added_side": "right",
            "initial_vehicle_state": "empty_at_model_cut",
            "upstream_feed_connections": [],
            "official_downstream_movements": [
                {
                    "connection_id": "1",
                    "egress_lane_id": "9",
                    "signal_group": "9",
                    "maneuver_bits": "001000000000",
                }
            ],
            "pocket_semantics": "dedicated_single_official_MAP_movement_pocket",
            "entry_after_cut": "downstream_lane_change_only",
            "invented_connection_forbidden": True,
        }
    ]
    assert eastbound["cut_evidence"]["authorized_model_cut_station_m"] == 698.0
    assert eastbound["cut_evidence"]["cut_basis"] == (
        "exact_HH_SIB_lane_profile_transition"
    )
    assert eastbound["cut_evidence"]["physical_taper_location_claimed"] is False
    assert eastbound["compiler_directive"]["added_lane_upstream_feed_connections"] == []
    assert eastbound["compiler_directive"]["downstream_lane_change_policy"] == {
        "policy": "allow_SUMO_lane_changing_after_model_cut",
        "status": "structural_compiler_default",
        "field_permission_claimed": False,
    }


def test_real_reverse_continuation_passes_but_missing_hh_sib_cut_abstains() -> None:
    result = build_hamburg_official_lane_transition_graph(**_real_inputs())
    reverse = next(
        item for item in result["transitions"] if item["station_direction"] == "against_stationing"
    )

    assert reverse["status"] == "review_required"
    assert reverse["continuation_identity_status"] == "pass"
    assert reverse["lane_transition_authorized"] is False
    assert reverse["reason"] == (
        "no_HH_SIB_lane_profile_transition_for_MAP_lane_count_increase"
    )
    assert reverse["continuation_identity_evidence"] == [
        {
            "upstream_official_lane_id": "14",
            "upstream_sumo_lane_index": 0,
            "downstream_official_lane_id": "2",
            "downstream_sumo_lane_index": 0,
            "rms_centerline_distance_m": 0.243248,
            "peak_centerline_distance_m": 0.250062,
            "rms_heading_delta_deg": 0.127549,
        }
    ]
    assert reverse["continuation_edges"] == []
    assert reverse["added_lanes"] == []
    assert reverse["unresolved_added_lanes"][0]["downstream_official_lane_id"] == "3"
    assert reverse["unresolved_added_lanes"][0]["added_side"] == "left"
    assert reverse["unresolved_added_lanes"][0]["origin_cut_status"] == "review_required"
    assert reverse["cut_evidence"]["authorized_model_cut_station_m"] is None
    assert reverse["cut_evidence"]["MAP_endpoint_A_used_as_physical_taper"] is False
    assert reverse["compiler_directive"] is None


def test_graph_is_deterministic_when_report_order_is_reversed() -> None:
    inputs = _real_inputs()
    repeated = build_hamburg_official_lane_transition_graph(**inputs)
    inputs["map_binding_reports"] = list(reversed(inputs["map_binding_reports"]))
    reversed_result = build_hamburg_official_lane_transition_graph(**inputs)

    assert repeated == reversed_result
    assert repeated["graph_id"] == "official-lane-transition-45ed08a1de702080597cd9ec"


def test_graph_identity_does_not_depend_on_checkout_path(tmp_path: Path) -> None:
    source_inputs = _real_inputs()
    source_result = build_hamburg_official_lane_transition_graph(**source_inputs)
    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    copied_reports = [
        Path(shutil.copy2(path, copy_dir / Path(path).name))
        for path in source_inputs["map_binding_reports"]
    ]
    copied_inputs = {
        "map_binding_reports": copied_reports,
        **{
            role: Path(shutil.copy2(path, copy_dir / Path(path).name))
            for role, path in source_inputs.items()
            if role != "map_binding_reports"
        },
    }

    copied_result = build_hamburg_official_lane_transition_graph(**copied_inputs)

    assert source_result["graph_id"] == copied_result["graph_id"]
    assert source_result["inputs"]["nodes"]["path"] != copied_result["inputs"]["nodes"]["path"]
    assert source_result["inputs"]["lane_axis_stitch_plan"]["path"] != (
        copied_result["inputs"]["lane_axis_stitch_plan"]["path"]
    )


def test_tight_uniqueness_gate_abstains_instead_of_selecting_best_guess() -> None:
    result = build_hamburg_official_lane_transition_graph(
        **_real_inputs(),
        thresholds=OfficialLaneTransitionThresholds(
            minimum_assignment_rms_margin_m=2.0
        ),
    )

    assert result["counts"]["authorized_direction_count"] == 0
    assert all(
        item["reason"] == "ambiguous_assignment_score_margin"
        for item in result["transitions"]
    )
    assert all(item["continuation_edges"] == [] for item in result["transitions"])


def test_edited_stitch_plan_is_rejected_before_lane_matching() -> None:
    inputs = _real_inputs()
    plan_path = Path(inputs["lane_axis_stitch_plan"])
    edited = json.loads(plan_path.read_text(encoding="utf-8"))
    edited["stitch_candidates"][0]["selected_cut"]["station_m"] = 699.0
    inputs["lane_axis_stitch_plan"] = edited

    with pytest.raises(OfficialLaneTransitionGraphError, match="does not exactly match"):
        build_hamburg_official_lane_transition_graph(**inputs)


def test_output_artifact_is_the_exact_returned_graph(tmp_path: Path) -> None:
    destination = tmp_path / "official_lane_transition_graph.json"
    result = build_hamburg_official_lane_transition_graph(
        **_real_inputs(),
        output_file=destination,
    )

    assert json.loads(destination.read_text(encoding="utf-8")) == result
    assert "OpenStreetMap" in result["source_policy"]["excluded_sources"]
    assert "Google_Maps" in result["source_policy"]["excluded_sources"]
