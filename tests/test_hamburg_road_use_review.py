import json

import pytest

from torii_sumo import cli
from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_road_use_review import inspect_road_use_sample


def sample():
    return {
        "id": "cycling", "point_epsg25832": [0, 2], "image_year": 2024,
        "visual_function": "cycling_indicated",
        "references": [{"id": "cycle", "year": 2024, "category": "Radfahrstreifen",
                        "lines": [[[-10, 2], [10, 2]]]}],
        "map_lanes": [{"id": "bike", "lane_type": "bikeLane",
                       "line": [[-10, 2], [10, 2]], "revocable": False}],
        "road_axes": [{"id": "road", "line": [[-20, 0], [20, 0]]}],
    }


def test_crossing_nearest_lane_cannot_replace_parallel_lane():
    row = sample()
    row["map_lanes"].insert(0, {"id": "crossing", "lane_type": "vehicle",
                                  "line": [[0, -10], [0, 10]]})
    result = inspect_road_use_sample(row)
    assert result["geometric_candidate_lane_ids"] == ["bike"]
    crossing = next(p for p in result["pairs"] if p["map_lane_id"] == "crossing")
    assert "different_line_orientation" in crossing["reasons"]
    assert result["decision"] == "review_required"
    assert result["assigned_lane_id"] is None

    row["references"][0]["lines"][0].reverse()
    reversed_result = inspect_road_use_sample(row)
    assert reversed_result["geometric_candidate_lane_ids"] == ["bike"]
    assert reversed_result["travel_direction_status"] == "unverified"


def test_side_extent_ambiguity_and_dates_remain_visible():
    row = sample()
    row["point_epsg25832"] = [0, 0.6]
    row["references"][0]["lines"] = [[[-10, -0.6], [10, -0.6]]]
    row["map_lanes"][0]["line"] = [[-10, -0.6], [10, -0.6]]
    result = inspect_road_use_sample(row)
    assert "opposite_road_side" in result["pairs"][0]["reasons"]

    row = sample()
    row["map_lanes"][0]["line"] = [[0, 2], [2, 2]]
    assert "insufficient_local_extent" in inspect_road_use_sample(row)["pairs"][0]["reasons"]

    row = sample()
    row["map_lanes"].append({"id": "adjacent", "lane_type": "vehicle",
                             "line": [[-10, 2.5], [10, 2.5]], "revocable": True,
                             "allowed_vehicle_classes": ["bus"]})
    row["references"][0]["year"] = 2026
    result = inspect_road_use_sample(row)
    assert result["association_status"] == "ambiguous_geometric_candidates"
    assert all(p["temporal_status"] == "different_year" for p in result["pairs"])
    assert result["permitted_users_status"] == "unverified"
    assert result["revocable_map_lane_ids"] == ["adjacent"]


def test_cli_reproduces_request_and_preserves_sources(tmp_path, capsys):
    source = tmp_path / "observations.json"
    source.write_text("{}", encoding="utf-8")
    request = tmp_path / "request.json"
    payload = {"schema": "torii.hamburg-road-use-review-request/v1", "crs": "EPSG:25832",
               "sources": [{"path": str(source), "sha256": file_sha256(source)}],
               "samples": [sample()]}
    request.write_text(json.dumps(payload), encoding="utf-8")
    destination = tmp_path / "review"
    assert cli.main(["hamburg", "inspect-road-uses", str(request), str(destination), "--json"]) == 0
    capsys.readouterr()
    report = json.loads((destination / "road-use-review.json").read_text(encoding="utf-8"))
    assert report["summary"]["sample_count"] == 1
    assert report["network_changed"] is False
    assert file_sha256(source) == payload["sources"][0]["sha256"]
    assert report["request_sha256"] == file_sha256(request)
    assert cli.main(["hamburg", "inspect-road-uses", str(request), str(destination), "--json"]) != 0
    source.write_text("changed", encoding="utf-8")
    assert cli.main(["hamburg", "inspect-road-uses", str(request), str(tmp_path / "stale"), "--json"]) != 0
    assert not (tmp_path / "stale").exists()


def test_nonfinite_and_degenerate_geometry_is_rejected():
    row = sample()
    row["map_lanes"][0]["line"] = [[0, 2], [0, 2]]
    with pytest.raises(ValueError):
        inspect_road_use_sample(row)
    row = sample()
    row["point_epsg25832"] = [float("nan"), 2]
    with pytest.raises(ValueError):
        inspect_road_use_sample(row)
    row = sample()
    row["map_lanes"][0]["revocable"] = "false"
    with pytest.raises(ValueError):
        inspect_road_use_sample(row)


def test_geometry_agreement_does_not_erase_parking_or_missing_context():
    row = sample()
    row["visual_function"] = "parking"
    result = inspect_road_use_sample(row)
    assert result["pairs"][0]["observation_relation"] == "parking_observation_near_cycling_records"
    assert result["visual_function"] == "parking"
    assert result["assigned_lane_id"] is None
    row["references"] = []
    assert inspect_road_use_sample(row)["comparison_status"] == "no_nearby_reference_geometry"
