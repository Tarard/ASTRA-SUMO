import xml.etree.ElementTree as ET

import numpy as np

from torii_sumo.core.hamburg_sensor_twin import (
    build_path_time_assignment_evidence,
    build_time_expanded_response,
    solve_bounded_l1_path_time_profiles,
    solve_conserved_path_time_profiles,
    solve_vehicle_shift_selection,
    write_path_time_profiles,
)


def test_time_expanded_response_keeps_one_vehicle_across_two_sensors() -> None:
    matrix, columns = build_time_expanded_response(
        {
            ("source", "upstream", "downstream", "sink"): {
                "upstream": 100.0,
                "downstream": 1100.0,
            }
        },
        ["upstream", "downstream"],
        target_begin=1800,
        target_end=3600,
        bin_seconds=900,
        departure_step_seconds=300,
    )

    column_index = next(index for index, column in enumerate(columns) if column.depart == 1800)
    assert np.flatnonzero(matrix[:, column_index]).tolist() == [0, 3]


def test_path_time_assignment_keeps_multi_sensor_vehicle_in_one_column(tmp_path) -> None:
    demand = tmp_path / "demand.rou.xml"
    demand.write_text(
        """<routes>
        <vehicle id="v0" depart="0"><route edges="source upstream downstream sink"/></vehicle>
        <vehicle id="v1" depart="900"><route edges="source upstream downstream sink"/></vehicle>
        </routes>""",
        encoding="utf-8",
    )
    vehroute = tmp_path / "vehroute.xml"
    vehroute.write_text(
        """<routes>
        <vehicle id="v0" depart="5"><route edges="source upstream downstream sink" exitTimes="10 100 1000 1100"/></vehicle>
        <vehicle id="v1" depart="905"><route edges="source upstream downstream sink" exitTimes="910 1000 1900 2000"/></vehicle>
        </routes>""",
        encoding="utf-8",
    )

    evidence = build_path_time_assignment_evidence(
        demand,
        vehroute,
        sensor_pass_edges={"upstream": ("upstream",), "downstream": ("downstream",)},
        target_begin=0,
        target_end=1800,
        departure_begin=0,
        departure_end=1800,
        bin_seconds=900,
    )

    assert evidence.assignment.shape == (4, 2)
    assert np.flatnonzero(evidence.assignment[:, 0]).tolist() == [0, 3]
    assert evidence.prior_counts.tolist() == [1, 1]

    fine = build_path_time_assignment_evidence(
        demand,
        vehroute,
        sensor_pass_edges={"upstream": ("upstream",), "downstream": ("downstream",)},
        target_begin=0,
        target_end=1800,
        departure_begin=0,
        departure_end=1800,
        bin_seconds=900,
        departure_bin_seconds=300,
    )
    assert fine.assignment.shape == (4, 6)
    assert fine.prior_counts.tolist() == [1, 0, 0, 1, 0, 0]


def test_path_time_update_preserves_each_route_total() -> None:
    proposal = solve_conserved_path_time_profiles(
        np.asarray([[1, 0, 1, 0], [0, 1, 0, 1]], dtype=float),
        np.asarray([4, 0], dtype=float),
        np.asarray([1, 1, 1, 1], dtype=float),
        route_count=2,
        bins_per_route=2,
        prior_weight=0,
        damping=1,
    )

    assert proposal.tolist() == [2, 0, 2, 0]
    assert proposal[:2].sum() == 2
    assert proposal[2:].sum() == 2


def test_bounded_l1_update_matches_counts_without_breaking_route_totals() -> None:
    proposal = solve_bounded_l1_path_time_profiles(
        np.asarray([[1, 0, 1, 0], [0, 1, 0, 1]], dtype=float),
        np.asarray([4, 0], dtype=float),
        np.asarray([1, 1, 1, 1], dtype=float),
        route_count=2,
        bins_per_route=2,
        prior_weight=0.05,
        max_bin_change=1,
    )

    assert proposal.tolist() == [2, 0, 2, 0]
    assert proposal[:2].sum() == 2
    assert proposal[2:].sum() == 2


def test_path_time_writer_retimes_routes_without_changing_totals(tmp_path) -> None:
    source = tmp_path / "source.rou.xml"
    source.write_text(
        """<routes>
        <vehicle id="a0" depart="0" departLane="0"><route edges="a b"/></vehicle>
        <vehicle id="a1" depart="1" departLane="1"><route edges="a b"/></vehicle>
        <vehicle id="b0" depart="2"><route edges="c d"/></vehicle>
        <vehicle id="b1" depart="3"><route edges="c d"/></vehicle>
        </routes>""",
        encoding="utf-8",
    )
    output = tmp_path / "updated.rou.xml"

    write_path_time_profiles(
        source,
        output,
        route_order=(("a", "b"), ("c", "d")),
        profiles=np.asarray([0, 2, 1, 1]),
        departure_begin=0,
        bin_seconds=900,
        depart_lane="free",
    )

    root = ET.parse(output).getroot()
    assert [float(vehicle.attrib["depart"]) for vehicle in root.findall("vehicle")] == [
        2,
        900,
        901,
        903,
    ]
    assert all(vehicle.attrib["departLane"] == "free" for vehicle in root.findall("vehicle"))

    preserved = tmp_path / "preserved.rou.xml"
    write_path_time_profiles(
        source,
        preserved,
        route_order=(("a", "b"), ("c", "d")),
        profiles=np.asarray([0, 2, 1, 1]),
        departure_begin=0,
        bin_seconds=900,
        depart_lane=None,
    )
    preserved_root = ET.parse(preserved).getroot()
    assert {
        vehicle.attrib["id"]: vehicle.attrib.get("departLane")
        for vehicle in preserved_root.findall("vehicle")
    }["a1"] == "1"


def test_vehicle_shift_selection_uses_one_helpful_move() -> None:
    selected = solve_vehicle_shift_selection(
        np.asarray([-1, 1]),
        np.asarray([[1], [-1]]),
        vehicle_ids=("v0",),
        shift_costs=np.asarray([60.0]),
        max_changes=1,
    )

    assert selected.tolist() == [0]
