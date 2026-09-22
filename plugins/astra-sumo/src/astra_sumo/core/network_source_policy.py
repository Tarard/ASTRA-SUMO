"""Choose the network scenario from user inputs, never from today's date."""

from datetime import date
import re


def _year(value, label):
    if value is not None and (type(value) is not int or not 1900 <= value <= 2200):
        raise ValueError(f'{label} must be a year from 1900 to 2200 or null.')
    return value


def _source_year(record, label, *, design=False):
    if not isinstance(record, dict):
        raise ValueError(f'{label} must be an object.')
    year = _year(record.get('data_year'), label + '.data_year')
    stamp = record.get('document_date')
    if stamp is None:
        return year
    if not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d{2}(-\d{2})?', stamp):
        raise ValueError(f'{label}.document_date must use YYYY-MM or YYYY-MM-DD.')
    parsed = date.fromisoformat(stamp + '-01' if len(stamp) == 7 else stamp)
    document_year = _year(parsed.year, label + '.document_date')
    return year if year is not None else document_year if design else None


def resolve_network_source_policy(request):
    """Resolve authority and period once, before choosing construction stages.

    Supporting maps can fill reviewed gaps. Their publication or retrieval date
    does not move the scenario or overrule explicitly supplied plan topology.
    """
    if not isinstance(request, dict) or not isinstance(request.get('scenario', {}), dict):
        raise ValueError('The request and scenario must be objects.')
    scenario = request.get('scenario', {})
    if set(scenario) - {'target_year'}:
        raise ValueError('scenario currently supports target_year only.')
    rows = request.get('intersections', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('intersections must be a list of objects.')
    has_plan = request.get('construction_plan') is not None
    authority = 'construction_plan' if has_plan else 'official_map' if any(row.get('map_xml') is not None for row in rows) else 'source_osm'
    osm_record = request.get('source_osm')
    osm_year = _source_year({} if osm_record is None else osm_record, 'source_osm')
    map_years = {_source_year(row['map_xml'], 'map_xml') for row in rows if row.get('map_xml') is not None} - {None}
    primary_year = (_source_year(request['construction_plan'], 'construction_plan', design=True) if has_plan
                    else next(iter(map_years)) if len(map_years) == 1 else osm_year if authority == 'source_osm' else None)
    target = _year(scenario.get('target_year'), 'scenario.target_year')
    basis = 'scenario.target_year' if target is not None else None
    if target is None and primary_year is not None:
        target = primary_year
        basis = authority + ('.document_date' if has_plan and request['construction_plan'].get('data_year') is None else '.data_year')
    if target is None and not has_plan and len(map_years) <= 1:
        target = osm_year
        basis = 'source_osm.data_year' if target is not None else None
        if target is None:
            aerial_years = {_year(row.get('aerial_year'), 'aerial_year') for row in rows} - {None}
            if len(aerial_years) == 1:
                target, basis = next(iter(aerial_years)), 'provided_aerial_year'
    primary_period = ('unknown' if target is None or primary_year is None else
                      'same_year' if target == primary_year else 'different_year')
    supporting = request.get('supporting_maps', [])
    if not isinstance(supporting, list) or any(not isinstance(row, dict) for row in supporting):
        raise ValueError('supporting_maps must be a list of objects.')
    support_roles = []
    for row in supporting:
        year = _source_year(row, 'supporting_map')
        requested_role = row.get('role')
        if requested_role not in (None, 'registration_only', 'context_only', 'supplement_missing_only'):
            raise ValueError('Map role must be registration_only, context_only, or supplement_missing_only.')
        role = ('supplement_missing_only' if target is not None and year == target else 'context_only')
        if requested_role in ('registration_only', 'context_only'):
            role = requested_role
        support_roles.append(dict(provider=row.get('provider', 'unspecified'), data_year=year,
            role=role, declared_role=requested_role, geometry_supplement_allowed=role == 'supplement_missing_only',
            overrides_primary_topology=False))
    reasons = []
    if target is None:
        reasons.append('target_year_unresolved_from_user_inputs')
    if primary_period == 'different_year':
        reasons.append('explicit_target_differs_from_primary_data_year')
    if len(map_years) > 1 and not has_plan:
        reasons.append('multiple_primary_MAP_periods')
    return dict(schema='torii.network-source-policy/v1', status='review_required' if reasons else 'pass',
        topology_authority=authority, target_year=target, target_year_basis=basis,
        primary_data_year=primary_year, primary_period_status=primary_period,
        target_state='provided_plan' if has_plan else 'provided_network_data',
        osm_geometry_supplement_allowed=target is not None and osm_year == target,
        osm_role='supplement_missing_only' if has_plan else 'provided_source_network',
        supporting_maps=support_roles, reasons=reasons, select_latest_data=False,
        standards_role='check_applicable_design_conditions_without_replacing_user_scene',
        experience_role='explicit_hypotheses_only',
        claim_boundary='The supplied primary data define topology and period. Different-period supporting maps do not redefine the scenario. Source coverage and physical validity need separate checks.')
