import pytest

from torii_sumo.core.network_source_policy import resolve_network_source_policy


def test_dated_user_plan_controls_target_and_newer_maps_do_not_replace_it():
    policy = resolve_network_source_policy({
        'construction_plan': {'data_year': 2013}, 'source_osm': {'data_year': 2026},
        'supporting_maps': [{'provider': 'google_maps', 'data_year': 2026}],
    })
    assert policy['target_year'] == 2013
    assert policy['topology_authority'] == 'construction_plan'
    assert policy['target_year_basis'] == 'construction_plan.data_year'
    assert policy['osm_geometry_supplement_allowed'] is False
    assert policy['supporting_maps'][0]['role'] == 'context_only'
    assert policy['status'] == 'pass'


def test_explicit_user_year_wins_and_primary_year_difference_is_visible():
    policy = resolve_network_source_policy({'scenario': {'target_year': 2015},
        'construction_plan': {'data_year': 2013}, 'source_osm': {'data_year': 2015}})
    assert policy['target_year'] == 2015
    assert policy['target_year_basis'] == 'scenario.target_year'
    assert policy['primary_period_status'] == 'different_year'
    assert policy['status'] == 'review_required'


def test_only_dated_osm_uses_that_year_not_today_or_optional_new_map():
    policy = resolve_network_source_policy({'source_osm': {'data_year': 2013},
        'supporting_maps': [{'provider': 'google_maps', 'data_year': 2026}]})
    assert policy['target_year'] == 2013
    assert policy['topology_authority'] == 'source_osm'


def test_unresolved_year_stays_unknown_and_legacy_dated_inputs_still_work():
    assert resolve_network_source_policy({'source_osm': {}})['target_year'] is None
    assert resolve_network_source_policy({'source_osm': {}})['status'] == 'review_required'
    old = resolve_network_source_policy({'source_osm': {}, 'intersections': [
        {'map_xml': {}, 'aerial_year': 2024}, {'map_xml': {}, 'aerial_year': 2024}]})
    assert old['target_year'] == 2024
    assert old['topology_authority'] == 'official_map'
    mixed = resolve_network_source_policy({'source_osm': {}, 'intersections': [
        {'map_xml': {'data_year': 2013}}, {'map_xml': {'data_year': 2014}}]})
    assert mixed['target_year'] is None


@pytest.mark.parametrize('year', [True, 2013.5, '2013', 0, 99999])
def test_invalid_year_is_not_coerced(year):
    with pytest.raises(ValueError):
        resolve_network_source_policy({'construction_plan': {'data_year': year}})


def test_design_document_date_is_a_fallback_not_download_date():
    policy = resolve_network_source_policy({'construction_plan': {'document_date': '2013-07'},
                                           'source_osm': {'downloaded_at': '2026-09-09'}})
    assert policy['target_year'] == 2013
    policy = resolve_network_source_policy({'construction_plan': {'downloaded_at': '2026-09-09'}})
    assert policy['target_year'] is None


@pytest.mark.parametrize('role', ['registration_only', 'context_only'])
def test_user_limits_on_auxiliary_map_use_are_never_promoted(role):
    policy = resolve_network_source_policy({'construction_plan': {'data_year': 2013},
        'supporting_maps': [{'provider': 'google_maps', 'data_year': 2013, 'role': role}]})
    assert policy['supporting_maps'][0]['role'] == role
    assert policy['supporting_maps'][0]['geometry_supplement_allowed'] is False


def test_primary_data_year_does_not_disable_supplied_document_date_validation():
    with pytest.raises(ValueError, match='document_date'):
        resolve_network_source_policy({'construction_plan': {'data_year': 2013, 'document_date': 'not-a-date'}})
