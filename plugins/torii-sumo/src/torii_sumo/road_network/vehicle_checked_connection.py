"""Choose among existing endpoint guides using full rigid-body space checks."""

import math

from ..core.hamburg_aerial_movement import _endpoint_guide
from .rigid_vehicle_sweep import rigid_vehicle_sweep


def select_vehicle_checked_connection(*, incoming_path, outgoing_path, vehicles, allowed_polygon,
                                       forbidden_polygons=(), include_mirrors=True, clearance_m=0.0):
    """Use the existing four guide shapes. Unsuccessful searches stay unresolved."""
    if len(incoming_path) < 2 or len(outgoing_path) < 2 or not vehicles or allowed_polygon is None:
        raise ValueError('Provide entry and exit paths, vehicles, and a road polygon')
    start, end = incoming_path[-1], outgoing_path[0]
    initial = math.atan2(incoming_path[1][1] - incoming_path[0][1], incoming_path[1][0] - incoming_path[0][0])
    entry = math.atan2(start[1] - incoming_path[-2][1], start[0] - incoming_path[-2][0])
    departure = math.atan2(outgoing_path[1][1] - end[1], outgoing_path[1][0] - end[0])
    alternatives = []
    # ponytail: four existing cubic guides; a failed search requires more evidence
    # or a wider path search, not a claim that no physical trajectory exists.
    for fraction in (0.35, 0.5, 0.65, 0.8):
        guide = _endpoint_guide(start, end, math.degrees(entry), math.degrees(departure), control_fraction=fraction)
        path = list(incoming_path[:-1]) + guide + list(outgoing_path[1:])
        checks = [rigid_vehicle_sweep(path, vehicle, initial_heading_rad=initial, allowed_polygon=allowed_polygon,
                                      forbidden_polygons=forbidden_polygons, include_mirrors=include_mirrors,
                                      clearance_m=clearance_m, include_geometry=False) for vehicle in vehicles]
        accepted = all(c['space_check'] == 'inside_declared_space' and c['nonforward_or_singular_samples'] == 0 for c in checks)
        score = (not accepted, sum(c['nonforward_or_singular_samples'] for c in checks),
                 max(c['maximum_outside_cell_area_m2'] + c['maximum_forbidden_overlap_m2'] for c in checks),
                 sum(c['violating_cell_count'] for c in checks), max(c['maximum_required_steering_deg'] for c in checks))
        alternatives.append(dict(control_fraction=fraction, connection_shape=guide, front_bumper_path=path,
                                 checks=checks, status='pass' if accepted else 'review_required', score=score))
    best = min(alternatives, key=lambda row: row['score'])
    return dict(schema='torii.vehicle-checked-connection/v1', **best,
                alternatives=[{k: v for k, v in row.items() if k not in ('connection_shape', 'front_bumper_path')} for row in alternatives],
                steering_feasibility='not_verified' if any(c['steering_feasibility'] == 'not_verified' for c in best['checks']) else 'see_vehicle_checks',
                claim_boundary='Geometric space for the declared vehicles, road polygon, and initial heading. Four candidates do not exhaust possible paths. Steering limits and actual operation require separate evidence.')
