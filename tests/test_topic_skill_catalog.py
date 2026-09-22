from pathlib import Path

from torii_sumo.core.workflow_catalog import get_workflow_catalog


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "torii-sumo"
REFERENCE_BUNDLES = {"torii-build", "torii-calibrate", "torii-simulate", "torii-report"}


def test_workflow_catalog_points_to_bundled_reference_bundles() -> None:
    catalog = get_workflow_catalog()
    assert catalog["status"] == "pass"
    for row in catalog["scenarios"]:
        assert row["reference_bundle"] in REFERENCE_BUNDLES
        assert row["reference"].startswith(
            f"skills/{row['reference_bundle']}/references/"
        )
        assert (PLUGIN / row["reference"]).is_file()
        assert "skill" not in row


def test_catalog_suggests_expected_reference_bundle_for_product_stages() -> None:
    rows = {row["scenario_id"]: row for row in get_workflow_catalog()["scenarios"]}
    assert rows["osm_network"]["reference_bundle"] == "torii-build"
    assert rows["detector_calibration"]["reference_bundle"] == "torii-calibrate"
    assert rows["environment_preflight"]["reference_bundle"] == "torii-simulate"
    assert rows["run_comparison"]["reference_bundle"] == "torii-report"
