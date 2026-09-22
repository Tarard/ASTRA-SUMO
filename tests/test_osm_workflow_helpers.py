"""Contract tests for the ``osm_workflow_helpers`` compatibility extraction."""

from __future__ import annotations

import inspect
from pathlib import Path

from torii_sumo.core import osm_workflow as facade
from torii_sumo.core import osm_workflow_helpers as helpers_module


def _defined_functions(module: object) -> list[str]:
    """Function names whose definitions live in ``module`` (not re-imports)."""
    return [
        name
        for name, value in inspect.getmembers(module, inspect.isfunction)
        if getattr(value, "__module__", None) == module.__name__
    ]


def test_facade_reexports_every_helper_function() -> None:
    helper_functions = _defined_functions(helpers_module)
    missing = [name for name in helper_functions if not hasattr(facade, name)]
    assert missing == [], missing

    mismatched = [
        name
        for name in helper_functions
        if getattr(facade, name) is not getattr(helpers_module, name)
    ]
    assert mismatched == [], mismatched


def test_representative_leaf_helpers_keep_behavior() -> None:
    assert helpers_module._intish("3") == 3
    assert helpers_module._intish("x") == 0
    assert helpers_module._gate_value({"status": "pass"}) == "pass"
    assert helpers_module._gate_value({"status": "blocked"}) == "blocked"
    assert helpers_module._gate_value({}) == "fail"
    assert helpers_module._connection_mode_gate_value(None) == "skipped"
    assert helpers_module._connection_mode_gate_value({"status": "review_required"}) == "review_required"
    assert helpers_module._connection_mode_gate_value({"status": "weird"}) == "fail"
    assert helpers_module._class_set(None) == set()
    assert helpers_module._class_set("a, b; c") == {"a", "b", "c"}
    assert helpers_module._safe_path_part("a/b") == "a_b"
    assert helpers_module._safe_path_part("") == "junction"
    assert helpers_module._tls_guess_signal_distance_label(None) == "default"
    assert helpers_module._tls_guess_signal_distance_label(12.0) == "guess12"
    assert helpers_module._tls_guess_signal_distance_label(1.5) == "guess1p5"
    assert helpers_module._delta_count_score({"tls_controlled_connection_count": 2, "other": 9}) == 2
    assert helpers_module._queue_path_value(None, "candidate_file") is None
    assert helpers_module._queue_path_value(
        {"queue_file": "D:/scratch/queue.json", "candidate_file": "candidate.net"},
        "candidate_file",
    ) == Path("D:/scratch/candidate.net")
    assert helpers_module._supports_keyword(lambda **kwargs: None, "x") is True
    assert helpers_module._supports_keyword(lambda a: None, "x") is False
