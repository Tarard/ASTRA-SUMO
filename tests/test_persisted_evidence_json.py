import json

from astra_sumo.core.candidate_contracts import _compare_persisted_fields


def _report():
    return {
        "semantic_validation": {
            "native_path_repair": {
                "attempts": [
                    {
                        "changes": [
                            {
                                "before_shape": [(0.0, 1.0, 2.0), (3.0, 4.0, 5.0)],
                                "after_shape": [(0.0, 1.0, 2.0), (3.0, 4.1, 5.0)],
                            }
                        ]
                    }
                ]
            }
        }
    }


def test_persisted_json_arrays_match_native_python_coordinate_tuples():
    supplied = _report()
    persisted = json.loads(json.dumps(supplied))
    errors = []
    _compare_persisted_fields(supplied, persisted, ("semantic_validation",), "materialization", errors)
    assert errors == []


def test_changed_persisted_coordinate_is_still_rejected():
    supplied = _report()
    persisted = json.loads(json.dumps(supplied))
    persisted["semantic_validation"]["native_path_repair"]["attempts"][0]["changes"][0]["after_shape"][1][1] = 4.2
    errors = []
    _compare_persisted_fields(supplied, persisted, ("semantic_validation",), "materialization", errors)
    assert errors == [{"code": "materialization_evidence_content_mismatch", "fields": ["semantic_validation"]}]


def test_unserializable_supplied_metadata_is_rejected_without_exception():
    errors = []
    _compare_persisted_fields(
        {"semantic_validation": {"set": {1, 2}}},
        {"semantic_validation": {}},
        ("semantic_validation",),
        "materialization",
        errors,
    )
    assert errors
