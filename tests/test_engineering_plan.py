import json

import pytest

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.road_network.adapters.engineering_plan import read_engineering_plan_observations


def _request(tmp_path, **source_changes):
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.7\n% test source\n")
    payload = {
        "schema": "torii.engineering-plan-observations-request/v1",
        "source": {"path": "plan.pdf", "sha256": file_sha256(pdf), "title": "Junction plan",
                   "document_date": "2021-07-01", "document_kind": "design", **source_changes},
        "observations": [{"id": "cycle-width", "page": 67, "location": "north approach cycle lane",
                          "property": "width", "value": 185, "unit": "cm", "width_basis": "including_markings"}],
    }
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload, pdf


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize("basis", ["whole_carriageway", "standard_lane_width"])
def test_explicit_standard_quantity_basis_survives_the_adapter(tmp_path, basis):
    path, payload, _ = _request(tmp_path)
    payload["observations"][0].update(value=5.90, unit="m", width_basis=basis)
    _write(path, payload)
    row = read_engineering_plan_observations(path)["observations"][0]
    assert row["width_basis"] == basis
    assert row["field_status"] == "not_verified"


def test_units_provenance_and_design_do_not_become_built_state(tmp_path):
    path, payload, pdf = _request(tmp_path)
    before = pdf.read_bytes()
    for value, unit in [(1.85, "m"), (185, "cm"), (1850, "mm")]:
        payload["observations"][0].update(value=value, unit=unit)
        _write(path, payload)
        result = read_engineering_plan_observations(path, target_date="2021-07-01")
        row = result["observations"][0]
        assert row["value_m"] == pytest.approx(1.85)
        assert row["raw"] == payload["observations"][0]
        assert row["date_alignment"] == "same_date_only"
        assert row["field_status"] == "not_verified"
        assert row["decision"] == result["decision"] == "review_required"
        assert result["source"]["path"] == str(pdf.resolve())
        assert result["source"]["sha256"] == file_sha256(pdf)
        assert result["request"]["sha256"] == file_sha256(path)
        assert result["network_changed"] is False
    assert pdf.read_bytes() == before


@pytest.mark.parametrize("kind", ["design", "as_built", "survey", "evaluation"])
def test_dates_and_unknown_width_basis_remain_explicit(tmp_path, kind):
    path, payload, _ = _request(tmp_path, document_kind=kind)
    payload["observations"][0].update(width_basis="unreadable dimension arrows", date="2021-06-30")
    _write(path, payload)
    row = read_engineering_plan_observations(path, "2024-01-01")["observations"][0]
    assert row["date_alignment"] == "different_date"
    assert row["source_date"] == "2021-06-30"
    assert row["width_basis"] == "unknown"
    assert row["raw"]["width_basis"] == "unreadable dimension arrows"
    assert row["field_status"] == "not_verified"
    assert row["decision"] == "review_required"
    payload["source"]["document_date"] = None
    payload["observations"][0].pop("date")
    _write(path, payload)
    assert read_engineering_plan_observations(path, "2024-01-01")["observations"][0]["date_alignment"] == "unknown"


@pytest.mark.parametrize("field,value", [
    ("page", 0), ("page", True), ("page", 1.5), ("location", ""), ("id", ""),
    ("property", ""), ("value", 0), ("value", -1), ("value", True), ("value", float("nan")),
    ("value", float("inf")), ("value", 10**400), ("unit", "ft"), ("date", "2021-02-30"), ("date", "20210701"),
])
def test_invalid_observations_are_rejected(tmp_path, field, value):
    path, payload, _ = _request(tmp_path)
    payload["observations"][0][field] = value
    _write(path, payload)
    with pytest.raises(ValueError):
        read_engineering_plan_observations(path)


def test_required_identity_page_and_hash_are_checked(tmp_path):
    path, payload, pdf = _request(tmp_path)
    row = payload["observations"][0]
    row.pop("page")
    _write(path, payload)
    with pytest.raises(ValueError, match="page"):
        read_engineering_plan_observations(path)
    row["page"] = 67
    payload["source"].pop("sha256")
    _write(path, payload)
    with pytest.raises(ValueError, match="SHA-256"):
        read_engineering_plan_observations(path)
    payload["source"]["sha256"] = "0" * 64
    _write(path, payload)
    with pytest.raises(ValueError, match="SHA-256"):
        read_engineering_plan_observations(path)
    payload["source"]["sha256"] = file_sha256(pdf)
    payload["observations"].append(dict(row))
    _write(path, payload)
    with pytest.raises(ValueError, match="unique"):
        read_engineering_plan_observations(path)


@pytest.mark.parametrize("field,value", [("document_date", "2021-13-01"), ("document_date", "20210701"),
                                         ("document_kind", "current_verified"), ("document_kind", []), ("title", "")])
def test_source_metadata_is_validated(tmp_path, field, value):
    path, _, _ = _request(tmp_path, **{field: value})
    with pytest.raises(ValueError):
        read_engineering_plan_observations(path)


def test_nonwidth_zero_and_absent_target_date_do_not_invent_alignment(tmp_path):
    path, payload, _ = _request(tmp_path)
    payload["observations"][0].update(property="offset", value=0)
    _write(path, payload)
    row = read_engineering_plan_observations(path)["observations"][0]
    assert row["value_m"] == 0
    assert row["date_alignment"] == "unknown"
    with pytest.raises(ValueError, match="target_date"):
        read_engineering_plan_observations(path, "2024-01")
