import pytest

from astra_sumo.core.prepared_source_registration import register_prepared_source_way_geometry


def _write(path, *, latitude="43", node_tag="", provenance="provider-road", extra=""):
    path.write_text(
        f'<osm><node id="1" lon="7" lat="43"/><node id="2" lon="7.01" lat="{latitude}">{node_tag}</node>'
        f'<way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/>'
        f'<tag k="ref" v="{provenance}"/></way>{extra}</osm>',
        encoding="utf-8",
    )


def test_registration_separates_coordinate_and_other_source_changes(tmp_path):
    before, reference = tmp_path / "before.osm", tmp_path / "reference.osm"
    _write(before)
    _write(reference, latitude="43.001")
    original = before.read_bytes()
    result = register_prepared_source_way_geometry(
        before_osm_file=before, reference_osm_file=reference, external_source_tag="ref"
    )
    assert result["records"][0]["coordinate_sequence_changed"]
    assert not result["records"][0]["source_state_unchanged"]
    _write(reference, node_tag='<tag k="highway" v="traffic_signals"/>')
    result = register_prepared_source_way_geometry(
        before_osm_file=before, reference_osm_file=reference, external_source_tag="ref"
    )
    assert not result["records"][0]["coordinate_sequence_changed"]
    assert not result["records"][0]["source_state_unchanged"]
    assert before.read_bytes() == original and result["field_defect_labels"] is False


def test_unchanged_state_is_registered_and_ambiguous_provenance_is_unknown(tmp_path):
    before, reference = tmp_path / "before.osm", tmp_path / "reference.osm"
    _write(before)
    _write(reference)
    result = register_prepared_source_way_geometry(
        before_osm_file=before, reference_osm_file=reference, external_source_tag="ref"
    )
    assert result["records"][0]["source_state_unchanged"]
    duplicate = '<way id="11"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/><tag k="ref" v="provider-road"/></way>'
    _write(reference, extra=duplicate)
    result = register_prepared_source_way_geometry(
        before_osm_file=before, reference_osm_file=reference, external_source_tag="ref"
    )
    assert not result["records"] and result["unresolved"][0]["way_id"] == "10"


@pytest.mark.parametrize("extra", ['<node id="1" lon="7" lat="43"/>', '<way id="10"/>'])
def test_duplicate_file_identifiers_do_not_silently_replace_records(tmp_path, extra):
    before, reference = tmp_path / "before.osm", tmp_path / "reference.osm"
    _write(before)
    _write(reference, extra=extra)
    with pytest.raises(ValueError, match="unique node and way"):
        register_prepared_source_way_geometry(
            before_osm_file=before, reference_osm_file=reference, external_source_tag="ref"
        )


def test_invalid_coordinates_remain_unresolved(tmp_path):
    before, reference = tmp_path / "before.osm", tmp_path / "reference.osm"
    _write(before)
    _write(reference, latitude="nan")
    result = register_prepared_source_way_geometry(
        before_osm_file=before, reference_osm_file=reference, external_source_tag="ref"
    )
    assert not result["records"] and result["unresolved"][0]["reason"] == "unresolved_source_geometry"
