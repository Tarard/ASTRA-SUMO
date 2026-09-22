from __future__ import annotations

from pathlib import Path

import numpy as np

from torii_sumo.core.route_sensor_matrix import (
    audit_route_sensor_incidence,
    solve_nonnegative_integer_route_flows,
)


def test_route_sensor_matrix_reports_rank_equivalence_and_unobserved_routes(tmp_path: Path) -> None:
    routes = tmp_path / "routes.csv"
    routes.write_text(
        "route_id,edges\n"
        'r0,"a b"\n'
        'r1,"a c"\n'
        'r2,"b d"\n'
        'r3,"x y"\n',
        encoding="utf-8",
    )
    counts = tmp_path / "counts.xml"
    counts.write_text(
        "<data>"
        "<interval begin='0' end='900'><edge id='a' count='2'/><edge id='b' count='2'/></interval>"
        "<interval begin='900' end='1800'><edge id='a' count='1'/><edge id='b' count='1'/></interval>"
        "</data>",
        encoding="utf-8",
    )

    report = audit_route_sensor_incidence(routes, counts)

    assert report["claim_status"] == "underdetermined-unbounded-without-prior"
    assert report["shape"] == {"sensor_rows": 2, "route_columns": 4}
    assert report["rank"] == 2
    assert report["nullity"] == 2
    assert report["distinct_route_signature_count"] == 4
    assert report["unobserved_route_ids"] == ["r3"]
    assert report["sensor_route_overlap"]["matrix"] == [[2, 1], [1, 2]]
    assert report["independent_bin_expansion"] == {
        "variable_count": 8,
        "rank_upper_bound": 4,
        "nullity_lower_bound": 4,
    }


def test_route_sensor_matrix_groups_identical_route_columns(tmp_path: Path) -> None:
    routes = tmp_path / "routes.csv"
    routes.write_text(
        "route_id,edges\n"
        'r0,"a b"\n'
        'r1,"a c b"\n',
        encoding="utf-8",
    )
    counts = tmp_path / "counts.xml"
    counts.write_text(
        "<data><interval begin='0' end='900'><edge id='a' count='2'/><edge id='b' count='2'/></interval></data>",
        encoding="utf-8",
    )

    report = audit_route_sensor_incidence(routes, counts)

    assert report["rank"] == 1
    assert report["distinct_route_signature_count"] == 1
    assert report["equivalent_route_classes"] == [
        {"signature": [1, 1], "route_count": 2, "route_ids": ["r0", "r1"]}
    ]


def test_integer_route_flow_solver_preserves_nearest_exact_prior() -> None:
    report = solve_nonnegative_integer_route_flows(
        np.asarray([[1, 0, 1], [0, 1, 1]]),
        np.asarray([3, 2]),
        prior=np.asarray([0, 0, 2]),
    )

    assert report["status"] == "pass"
    assert report["objective"] == "minimum_l1_change_from_prior"
    assert report["solution"] == [1, 0, 2]
    assert report["predicted_sensor_counts"] == [3, 2]
    assert report["residual"] == [0, 0]


def test_integer_route_flow_solver_fails_closed_for_infeasible_counts() -> None:
    report = solve_nonnegative_integer_route_flows(
        np.asarray([[1], [1]]),
        np.asarray([1, 2]),
    )

    assert report["status"] == "blocked"
    assert report["failure_kind"] == "infeasible"
    assert report["solution"] is None


def test_integer_route_flow_solver_rejects_lossy_float64_integer_conversion() -> None:
    with np.testing.assert_raises_regex(ValueError, "outside the exact float64 range"):
        solve_nonnegative_integer_route_flows(
            np.asarray([[1]], dtype=np.int64),
            np.asarray([2**53 + 1], dtype=np.int64),
        )
