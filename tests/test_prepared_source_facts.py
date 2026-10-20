import json
from pathlib import Path

import pytest

from astra_sumo.core.network_calibration import evaluate_network_calibration
from astra_sumo.core.prepared_source_facts import build_prepared_source_lane_facts


def _inputs(tmp_path, reference_permissions="passenger"):
    before, after, labels = [tmp_path / name for name in ("before.net.xml", "reference.net.xml", "labels.jsonl")]
    before.write_text(
        """<net version="1.20"><location netOffset="0,0" projParameter="!"/>
      <edge id="10#0" from="a" to="j"><lane id="10#0_0" index="0" allow="passenger" speed="10" length="4.5" shape="0,0 4.5,0"/></edge>
      <edge id="10#1" from="j" to="b"><lane id="10#1_0" index="0" allow="passenger" speed="10" length="4.5" shape="5.5,0 10,0"/></edge>
      <edge id=":j_0" function="internal"><lane id=":j_0_0" index="0" allow="passenger" length="1" shape="4.5,0 5.5,0"/></edge>
      <junction id="a" x="0" y="0"/><junction id="j" x="5" y="0" incLanes="10#0_0" intLanes=":j_0_0"/>
      <junction id="b" x="10" y="0"/>
      <connection from="10#0" fromLane="0" to="10#1" toLane="0" via=":j_0_0"/>
      <connection from=":j_0" fromLane="0" to="10#1" toLane="0"/></net>""",
        encoding="utf-8",
    )
    after.write_text(
        f'''<net version="1.20"><location netOffset="0,0" projParameter="!"/>
      <edge id="10#9" from="a" to="b"><lane id="10#9_0" index="0" allow="{reference_permissions}"
      speed="10" length="10" shape="0,1 10,1"/></edge><junction id="a" x="0" y="1"/>
      <junction id="b" x="10" y="1"/></net>''',
        encoding="utf-8",
    )
    labels.write_text(
        json.dumps(
            {
                "kind": "prepared_way_coordinate_sequence_revision",
                "way_id": "10",
                "external_source_id": "same-source-road",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return before, after, labels


def _build(tmp_path, before, reference, labels, destination="facts"):
    return build_prepared_source_lane_facts(
        before_net_file=before,
        reference_net_file=reference,
        way_labels_file=labels,
        output_dir=tmp_path / destination,
        site_id="model-only",
        model_revision_date="2018-09-19",
        license_name="GPLv3",
        sample_spacing_m=0.5,
    )


def test_fact_adapter_handles_resegmentation_and_saved_internal_geometry(tmp_path):
    before, reference, labels = _inputs(tmp_path)
    originals = {path: path.read_bytes() for path in (before, reference, labels)}
    report = _build(tmp_path, before, reference, labels)
    assert report["status"] == "pass" and report["fact_count"] == 1
    assert report["field_correctness"] == "not_established" and report["field_validity_date"] is None
    tables = {
        name: json.loads(Path(record["path"]).read_text(encoding="utf-8")) for name, record in report["outputs"].items()
    }
    source_fact = tables["before"]["facts"][0]
    assert set(source_fact["selector"]["lane_ids"]) == {"10#0_0", ":j_0_0", "10#1_0"}
    assert source_fact["mapping_status"] == "unresolved"
    assert source_fact["mapping_diagnostics"]["reference_scope"] == "generated_model_after_prepared_source_revision"
    scores = {}
    for name, network in (("before", before), ("reference-self-control", reference)):
        scores[name] = evaluate_network_calibration(
            net_file=network,
            facts_file=report["outputs"][name]["path"],
            output_file=tmp_path / f"{name}-score.json",
            mode="explore",
        )
    old = scores["before"]["facts"][0]["metrics"]
    assert old["mean_lateral_offset_m"] == pytest.approx(1)
    assert old["reference_coverage"] == 1
    assert scores["reference-self-control"]["facts"][0]["metrics"]["mean_lateral_offset_m"] == 0
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert not report["input_changes"]


def test_missing_lane_role_stays_unknown_instead_of_selecting_another_class(tmp_path):
    before, reference, labels = _inputs(tmp_path, reference_permissions="pedestrian")
    report = _build(tmp_path, before, reference, labels)
    assert report["unresolved_before_selectors"] == 1
    score = evaluate_network_calibration(
        net_file=before,
        facts_file=report["outputs"]["before"]["path"],
        output_file=tmp_path / "unknown.json",
        mode="explore",
    )
    assert score["status"] == "measured" and score["facts"][0]["status"] == "unknown"
    assert score["facts"][0]["known"] is True


@pytest.mark.parametrize("invalid", ["duplicate", "wrong_kind", "non_record"])
def test_invalid_registration_labels_do_not_create_output(tmp_path, invalid):
    before, reference, labels = _inputs(tmp_path)
    row = json.loads(labels.read_text(encoding="utf-8"))
    rows = (
        [row, row]
        if invalid == "duplicate"
        else [{**row, "kind": "field_defect"}]
        if invalid == "wrong_kind"
        else [None]
    )
    labels.write_text("\n".join(json.dumps(value) for value in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="Source-way labels"):
        _build(tmp_path, before, reference, labels)
    assert not (tmp_path / "facts").exists()


def test_existing_output_does_not_overwrite_source_inputs(tmp_path):
    before, reference, labels = _inputs(tmp_path)
    original = before.read_bytes()
    with pytest.raises(ValueError, match="new fact directory"):
        _build(tmp_path, before, reference, labels, destination=".")
    assert before.read_bytes() == original


def test_reference_way_without_generated_lanes_remains_in_the_fact_table(tmp_path):
    before, reference, labels = _inputs(tmp_path)
    row = json.loads(labels.read_text(encoding="utf-8"))
    labels.write_text(json.dumps({**row, "way_id": "99"}) + "\n", encoding="utf-8")
    report = _build(tmp_path, before, reference, labels)
    assert report["source_way_count"] == report["fact_count"] == report["unknown_reference_facts"] == 1
    table = json.loads(Path(report["outputs"]["before"]["path"]).read_text(encoding="utf-8"))
    assert table["facts"][0]["expected"] is None


def test_malformed_reference_geometry_is_unknown_without_losing_other_metadata(tmp_path):
    before, reference, labels = _inputs(tmp_path)
    reference.write_text(
        reference.read_text(encoding="utf-8").replace('shape="0,1 10,1"', 'shape="0,1"'), encoding="utf-8"
    )
    report = _build(tmp_path, before, reference, labels)
    assert report["fact_count"] == report["unknown_reference_facts"] == 1
    table = json.loads(Path(report["outputs"]["before"]["path"]).read_text(encoding="utf-8"))
    assert table["facts"][0]["mapping_diagnostics"]["unresolved_reference"]


@pytest.mark.parametrize("spacing", [0, float("nan"), True])
def test_invalid_sample_spacing_does_not_create_fact_output(tmp_path, spacing):
    before, reference, labels = _inputs(tmp_path)
    with pytest.raises(ValueError, match="Sample spacing"):
        build_prepared_source_lane_facts(
            before_net_file=before,
            reference_net_file=reference,
            way_labels_file=labels,
            output_dir=tmp_path / "facts",
            site_id="model-only",
            model_revision_date="2018-09-19",
            license_name="GPLv3",
            sample_spacing_m=spacing,
        )
    assert not (tmp_path / "facts").exists()
