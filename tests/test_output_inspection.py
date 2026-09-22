from pathlib import Path

from torii_sumo.evidence.output_inspection import inspect_output_pair, inspect_run_outputs, inspect_summary


FIXTURES = Path(__file__).parent / "fixtures" / "outputs"


def test_summary_keeps_ended_separate_from_arrived_and_reports_discarded(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.xml"
    summary_path.write_text(
        '<summary><step time="10" loaded="2" inserted="2" ended="2" discarded="1"/></summary>',
        encoding="utf-8",
    )

    summary = inspect_summary(summary_path)

    assert summary.arrived is None
    assert summary.discarded == 1
    assert summary.completion_ratio is None
    assert any("discarded" in warning for warning in summary.warnings)


def test_inspect_run_outputs_reports_completion_and_trip_means() -> None:
    report = inspect_run_outputs(
        "baseline",
        summary_path=FIXTURES / "baseline-summary.xml",
        tripinfo_path=FIXTURES / "baseline-tripinfo.xml",
    )

    assert report.status == "pass"
    assert report.summary is not None
    assert report.summary.completion_ratio == 1.0
    assert report.summary.arrived == 6
    assert report.tripinfo is not None
    assert report.tripinfo.trip_count == 2
    assert report.tripinfo.mean_duration == 50.0
    assert report.tripinfo.mean_waiting_time == 5.0
    assert report.tripinfo.mean_time_loss == 7.0


def test_pair_report_warns_when_completion_differs() -> None:
    report = inspect_output_pair(
        baseline_summary=FIXTURES / "baseline-summary.xml",
        baseline_tripinfo=FIXTURES / "baseline-tripinfo.xml",
        variant_summary=FIXTURES / "variant-summary-incomplete.xml",
        variant_tripinfo=FIXTURES / "variant-tripinfo.xml",
    )

    assert report.status == "warn"
    assert any("completion differs" in warning for warning in report.paired_warnings)
    assert report.variant.summary is not None
    assert report.variant.summary.running == 2
    assert report.variant.summary.teleports == 1


def test_pair_report_passes_when_completion_matches() -> None:
    report = inspect_output_pair(
        baseline_summary=FIXTURES / "baseline-summary.xml",
        baseline_tripinfo=FIXTURES / "baseline-tripinfo.xml",
        variant_summary=FIXTURES / "variant-summary-complete.xml",
        variant_tripinfo=FIXTURES / "variant-tripinfo.xml",
    )

    assert report.status == "pass"
    assert report.claim_status == "diagnostic-demo"


def test_pair_report_requires_summary_evidence_for_diagnostic_demo() -> None:
    report = inspect_output_pair(
        baseline_summary=None,
        baseline_tripinfo=None,
        variant_summary=None,
        variant_tripinfo=None,
    )

    assert report.status in {"warn", "fail"}
    assert report.claim_status == "construction-invalid"


def test_inspect_run_outputs_fails_structurally_for_summary_directory() -> None:
    report = inspect_run_outputs(
        "baseline",
        summary_path=FIXTURES,
        tripinfo_path=FIXTURES / "baseline-tripinfo.xml",
    )

    assert report.status == "fail"
    assert report.summary is not None
    assert report.summary.valid_xml is False


def test_pair_report_rejects_empty_summary_as_completion_evidence(tmp_path: Path) -> None:
    empty_summary = tmp_path / "empty-summary.xml"
    empty_summary.write_text("<summary></summary>", encoding="utf-8")

    report = inspect_output_pair(
        baseline_summary=empty_summary,
        baseline_tripinfo=FIXTURES / "baseline-tripinfo.xml",
        variant_summary=empty_summary,
        variant_tripinfo=FIXTURES / "variant-tripinfo.xml",
    )

    assert report.claim_status == "construction-invalid"


def test_pair_report_rejects_summary_without_completion_counts(tmp_path: Path) -> None:
    no_counts_summary = tmp_path / "no-counts-summary.xml"
    no_counts_summary.write_text(
        '<summary><step time="100.00"/></summary>',
        encoding="utf-8",
    )

    report = inspect_output_pair(
        baseline_summary=no_counts_summary,
        baseline_tripinfo=FIXTURES / "baseline-tripinfo.xml",
        variant_summary=no_counts_summary,
        variant_tripinfo=FIXTURES / "variant-tripinfo.xml",
    )

    assert report.claim_status == "construction-invalid"
