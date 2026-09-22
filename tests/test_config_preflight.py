from pathlib import Path
import shutil

from torii_sumo.evidence.config_preflight import (
    preflight_config,
    preflight_pair,
)


FIXTURES = Path(__file__).parent / "fixtures" / "configs"


def test_pair_preflight_passes_for_distinct_outputs() -> None:
    (FIXTURES / "outputs" / "baseline").mkdir(parents=True, exist_ok=True)
    (FIXTURES / "outputs" / "variant").mkdir(parents=True, exist_ok=True)

    report = preflight_pair(FIXTURES / "baseline.sumocfg", FIXTURES / "variant.sumocfg")

    assert report.status == "pass"
    assert report.baseline.missing_inputs == []
    assert report.variant.missing_inputs == []
    assert report.paired_warnings == []


def test_pair_preflight_warns_for_shared_output_paths() -> None:
    report = preflight_pair(FIXTURES / "baseline.sumocfg", FIXTURES / "shared-output.sumocfg")

    assert report.status == "warn"
    assert any("shared output path" in warning for warning in report.paired_warnings)


def test_pair_preflight_fails_for_missing_input() -> None:
    report = preflight_pair(FIXTURES / "baseline.sumocfg", FIXTURES / "missing-input.sumocfg")

    assert report.status == "fail"
    assert "missing.net.xml" in report.variant.missing_inputs[0]


def test_config_preflight_fails_for_directory_path() -> None:
    report = preflight_config(FIXTURES, "directory")

    assert report.status == "fail"
    assert report.valid_xml is False
    assert any(
        "OSError" in warning or "invalid" in warning or "unreadable" in warning
        for warning in report.warnings
    )


def test_config_preflight_warns_for_missing_output_parent_directories() -> None:
    shutil.rmtree(FIXTURES / "outputs", ignore_errors=True)

    report = preflight_config(FIXTURES / "shared-output.sumocfg", "variant")

    assert report.status == "warn"
    assert report.missing_output_parents
    assert any("output parent" in warning for warning in report.warnings)
