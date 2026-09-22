from torii_sumo.core.sumo_warning_audit import compare_mapped_tls_warnings, parse_sumo_tls_warnings


def test_parse_sumo_tls_warnings_extracts_category_and_tls_id() -> None:
    warnings = parse_sumo_tls_warnings(
        "\n".join(
            [
                "Warning: Unused states in tlLogic '7616444534', program '0' in phase 0 after tl-index 17",
                "Warning: Missing green phase in tlLogic '39732721', program '0' for tl-index 10.",
                "Warning: At actuated tlLogic 'gneJ21', actuated phase 8 has no controlling detector.",
                "Warning: unrelated network warning",
            ]
        )
    )

    assert [warning["category"] for warning in warnings] == [
        "unused_states",
        "missing_green_phase",
        "actuated_phase_no_detector",
    ]
    assert warnings[0]["tl_id"] == "7616444534"
    assert warnings[0]["signature"] == "unused_states|program=0|phase=0|after_tl_index=17"


def test_compare_mapped_tls_warnings_separates_reference_inherited_from_candidate_new() -> None:
    teacher_stderr = "\n".join(
        [
            "Warning: Unused states in tlLogic '7616444534', program '0' in phase 0 after tl-index 17",
            "Warning: Missing green phase in tlLogic '7616444534', program '0' for tl-index 9.",
        ]
    )
    candidate_stderr = "\n".join(
        [
            "Warning: Unused states in tlLogic 'cluster_tls', program '0' in phase 0 after tl-index 17",
            "Warning: Missing yellow phase in tlLogic 'cluster_tls', program '0' for tl-index 3 when switching to phase 2.",
        ]
    )

    report = compare_mapped_tls_warnings(
        teacher_stderr,
        candidate_stderr,
        {"7616444534": "cluster_tls"},
    )

    assert report["status"] == "pass"
    assert report["mapped_tls_count"] == 1
    assert report["inherited_warning_count"] == 1
    assert report["candidate_only_warning_count"] == 1
    assert report["teacher_only_warning_count"] == 1
    assert report["by_candidate_tls"]["cluster_tls"]["inherited_warning_count"] == 1
    assert report["by_candidate_tls"]["cluster_tls"]["candidate_only_warning_count"] == 1
