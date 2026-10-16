import importlib.util
from pathlib import Path


def test_recorded_cohort_accounting_and_immediate_lane_access():
    script = Path(__file__).resolve().parents[1] / "benchmarks/hamburg_cohort_diagnosis_20261016_v1/audit_cohort.py"
    spec = importlib.util.spec_from_file_location("frozen_hamburg_cohort_audit", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.audit_cohort()
    expected = {"17": (64, 35, ["66"], 12), "42": (100, 0, [], 0), "104": (60, 39, ["66"], 13)}
    for seed, (arrived, active, pending, fronts) in expected.items():
        case = report["cases"][seed]
        assert case["requested"] == 100
        assert (case["arrived"], case["on_road"], case["pending_ids"]) == (arrived, active, pending)
        assert len(case["lane_front_candidates"]) == fronts
        assert all(row["next_route_edge"] and row["saved_path"] for row in case["lane_front_candidates"])
        assert case["fronts_without_immediate_path"] == []
    assert [report["cases"][seed]["collision_count"] for seed in ("17", "42", "104")] == [1, 1, 0]
