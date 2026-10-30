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


_SIGNAL_NODES = """<nodes>
    <node id="A" x="13.7300" y="51.0500" type="priority"/>
    <node id="B" x="13.7314" y="51.0500" type="traffic_light"/>
    <node id="C" x="13.73157" y="51.0500" type="traffic_light"/>
    <node id="D" x="13.73297" y="51.0500" type="traffic_light"/>
    <node id="E" x="13.7314" y="51.0509" type="priority"/>
    <node id="F" x="13.73157" y="51.0491" type="priority"/>
    <node id="G" x="13.73437" y="51.0500" type="priority"/>
    <node id="H" x="13.73297" y="51.0509" type="priority"/>
</nodes>
"""
_SIGNAL_EDGES = """<edges>
    <edge id="AB" from="A" to="B" numLanes="2" speed="13.89"><param key="origId" value="10 11"/><param key="name" value="West Street"/></edge>
    <edge id="BA" from="B" to="A" numLanes="2" speed="13.89"><param key="origId" value="-10"/></edge>
    <edge id="BE" from="B" to="E" numLanes="1" speed="13.89" name="North Road"/>
    <edge id="EB" from="E" to="B" numLanes="1" speed="13.89" name="North Road"/>
    <edge id="BC" from="B" to="C" numLanes="2" speed="13.89"/>
    <edge id="CB" from="C" to="B" numLanes="2" speed="13.89"/>
    <edge id="CF" from="C" to="F" numLanes="1" speed="13.89"/>
    <edge id="FC" from="F" to="C" numLanes="1" speed="13.89"/>
    <edge id="CD" from="C" to="D" numLanes="2" speed="13.89"/>
    <edge id="DC" from="D" to="C" numLanes="2" speed="13.89"/>
    <edge id="DG" from="D" to="G" numLanes="1" speed="13.89"/>
    <edge id="GD" from="G" to="D" numLanes="1" speed="13.89"/>
    <edge id="DH" from="D" to="H" numLanes="1" speed="13.89"/>
    <edge id="HD" from="H" to="D" numLanes="1" speed="13.89"/>
</edges>
"""


@pytest.fixture(scope="session")
def signal_network(tmp_path_factory) -> Path:
    """A native network with a joined controller (no junction of its id), a single one, crossings, walking areas,
    edge parameters and a UTM projection, built once by the packaged netconvert from geographic nodes."""
    import os
    import subprocess

    sumo = pytest.importorskip("sumo")
    netconvert = Path(sumo.__file__).parent / "bin" / ("netconvert.exe" if os.name == "nt" else "netconvert")
    if not netconvert.is_file():
        pytest.skip("The SUMO package netconvert is required.")
    directory = tmp_path_factory.mktemp("signal-network")
    (directory / "n.nod.xml").write_text(_SIGNAL_NODES, encoding="utf-8")
    (directory / "n.edg.xml").write_text(_SIGNAL_EDGES, encoding="utf-8")
    network = directory / "signals.net.xml"
    subprocess.run([str(netconvert), "--node-files", "n.nod.xml", "--edge-files", "n.edg.xml", "--proj.utm",
                    "--tls.join", "--tls.join-dist", "20", "--sidewalks.guess", "--crossings.guess",
                    "--output-file", str(network)], cwd=directory, check=True, capture_output=True)
    return network
