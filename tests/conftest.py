from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
FAILURE_ARTIFACTS = ROOT / "outputs" / "pytest-failures" / uuid4().hex
HISTORICAL_HAMBURG_ROOT = (
    ROOT
    / "artifacts"
    / "hamburg_sandtorkai_twin_20260719"
    / "official_first_named_corridor_v1"
)
HISTORICAL_HAMBURG_TESTS = {
    "tests/test_official_lane_stitch.py::test_real_2349_2394_reports_expose_one_official_2_to_3_cut",
    "tests/test_official_lane_transition.py::test_real_eastbound_transition_is_unique_and_right_pocket_starts_empty",
    "tests/test_official_lane_transition.py::test_real_reverse_continuation_passes_but_missing_hh_sib_cut_abstains",
    "tests/test_official_lane_transition.py::test_graph_is_deterministic_when_report_order_is_reversed",
    "tests/test_official_lane_transition.py::test_graph_identity_does_not_depend_on_checkout_path",
    "tests/test_official_lane_transition.py::test_tight_uniqueness_gate_abstains_instead_of_selecting_best_guess",
    "tests/test_official_lane_transition.py::test_edited_stitch_plan_is_rejected_before_lane_matching",
    "tests/test_official_lane_transition.py::test_output_artifact_is_the_exact_returned_graph",
}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Retain failed test inputs before pytest removes their temporary directory."""
    del call
    report = (yield).get_result()
    source = item.funcargs.get("tmp_path")
    if report.failed and source is not None:
        destination = FAILURE_ARTIFACTS / f"{source.name}-{report.when}"
        try:
            shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
            (destination / "pytest-nodeid.txt").write_text(item.nodeid, encoding="utf-8")
        except OSError as error:
            report.sections.append(("Failure artifact copy", str(error)))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip regressions whose intentionally deleted generated fixture is unavailable."""
    del config
    if HISTORICAL_HAMBURG_ROOT.exists():
        return

    marker = pytest.mark.skip(
        reason="historical generated Hamburg artifact fixture is not committed"
    )
    for item in items:
        base_nodeid = item.nodeid.split("[", 1)[0]
        if base_nodeid in HISTORICAL_HAMBURG_TESTS:
            item.add_marker(marker)
