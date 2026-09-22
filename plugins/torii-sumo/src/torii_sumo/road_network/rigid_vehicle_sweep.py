"""Low-speed rigid-vehicle sweep from a front-bumper-centre path.

The rear axle follows the no-slip bicycle constraint. SUMO's drawn tail and
lane-width buffers are not used as vehicle envelopes. This is not RBSV certification.
"""

from collections.abc import Mapping
import math

from ..core.hamburg_junction_contour import _polygon
from ..core.junction_footprint import _convex_hull
from ..core.surface_overlap_audit import _bbox, _bboxes_overlap, _convex_polygon_intersection, _signed_area, _triangulate_polygon


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite number.") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def _positive(value, name, *, zero=False):
    number = _finite(value, name)
    if number < 0 or (not zero and number == 0):
        raise ValueError(f"{name} must be a finite {'nonnegative' if zero else 'positive'} number.")
    return number


def _angle(value):
    return (value + math.pi) % (2 * math.pi) - math.pi


def _point(value):
    try:
        if len(value) != 2:
            raise ValueError("Use two finite metric coordinates per point.")
        return tuple(_finite(value[i], "coordinate") for i in (0, 1))
    except (TypeError, KeyError, IndexError) as error:
        raise ValueError("Use two finite metric coordinates per point.") from error


def _rectangle(front, heading, length, width):
    h = (math.cos(heading), math.sin(heading))
    n = (-h[1], h[0])
    return [(front[0] + along * h[0] + across * n[0], front[1] + along * h[1] + across * n[1])
            for along, across in ((0, -width / 2), (0, width / 2), (-length, width / 2), (-length, -width / 2))]


def rigid_vehicle_sweep(front_bumper_path, vehicle, *, initial_heading_rad, allowed_polygon=None,
                        forbidden_polygons=(), max_step_m=0.1, max_heading_step_deg=1.0,
                        clearance_m=0.0, include_mirrors=False, include_geometry=True):
    """Check complete swept body cells against one road polygon and optional islands.

    Headings follow the exact straight-segment solution for a rigid point D
    ahead of the rear axle, D=wheelbase+front overhang. Each swept cell contains
    all translations and endpoint rotations, expanded by the rotational sagitta.
    The road and obstacle polygons are declared evidence, not inferred here.
    """
    if not isinstance(vehicle, Mapping):
        raise ValueError("vehicle must contain rigid-body dimensions.")
    kind = vehicle.get('kind', 'rigid')
    if not isinstance(kind, str) or not kind.startswith('rigid'):
        raise ValueError("This model supports rigid vehicles only.")
    dimensions = {k: _positive(vehicle.get(k), k, zero=k.endswith('overhang_m'))
                  for k in ('length_m', 'width_m', 'wheelbase_m', 'front_overhang_m', 'rear_overhang_m')}
    length, wheelbase = dimensions['length_m'], dimensions['wheelbase_m']
    front_distance = wheelbase + dimensions['front_overhang_m']
    if not math.isclose(length, front_distance + dimensions['rear_overhang_m'], rel_tol=0, abs_tol=1e-6):
        raise ValueError("Wheelbase and overhangs must sum to vehicle length.")
    if type(include_mirrors) is not bool or type(include_geometry) is not bool:
        raise ValueError("Geometry options must be booleans.")
    width = dimensions['width_m']
    if include_mirrors:
        width = _positive(vehicle.get('width_including_mirrors_m'), 'width_including_mirrors_m')
        if width < dimensions['width_m']:
            raise ValueError("Mirror-inclusive width cannot be smaller than body width.")
    step = min(_positive(max_step_m, 'max_step_m'), front_distance * math.radians(_positive(max_heading_step_deg, 'max_heading_step_deg')))
    if not math.isfinite(step) or step <= 0:
        raise ValueError("Sampling step is outside the supported numeric range.")
    margin = _positive(clearance_m, 'clearance_m', zero=True)
    initial_angle = _finite(initial_heading_rad, "initial_heading_rad")
    theta = math.atan2(math.sin(initial_angle), math.cos(initial_angle))
    steering_limit = vehicle.get('maximum_equivalent_steering_angle_deg')
    if steering_limit is not None:
        steering_limit = _positive(steering_limit, 'maximum_equivalent_steering_angle_deg')
        if steering_limit >= 90:
            raise ValueError("Equivalent steering limit must be below 90 degrees.")
    try:
        path_iterator = iter(front_bumper_path)
    except TypeError as error:
        raise ValueError("The front-bumper path must contain metric coordinate pairs.") from error
    points = []
    for point in path_iterator:
        value = _point(point)
        if not points or math.dist(points[-1], value) > 1e-9:
            points.append(value)
    if len(points) < 2:
        raise ValueError("The front-bumper path needs two distinct points.")
    origin = points[0]
    def local(p):
        return _point((p[0] - origin[0], p[1] - origin[1]))
    def world(p):
        return list(_point((p[0] + origin[0], p[1] + origin[1])))
    def polygon(values):
        result = _polygon([local(_point(p)) for p in values])
        if not math.isfinite(_signed_area(result)):
            raise ValueError("Polygon area is outside the supported numeric range.")
        return result
    points = [local(p) for p in points]
    segments, planned_cells = [], 0
    # ponytail: bound geometry output to 100,000 cells; use a coarser step or separate shorter paths for larger reviews.
    for first, last in zip(points, points[1:]):
        distance = math.dist(first, last)
        ratio = distance / step
        if not math.isfinite(ratio) or ratio > 100_000 - planned_cells:
            raise ValueError("Sampling requires more than the supported 100,000 swept cells.")
        count = max(1, math.ceil(ratio))
        planned_cells += count
        segments.append((first, last, distance, count))
    road = polygon(allowed_polygon) if allowed_polygon is not None else None
    triangles = [(t, _bbox(t)) for t in _triangulate_polygon(road)] if road else []
    obstacles = [[(t, _bbox(t)) for t in _triangulate_polygon(polygon(poly))]
                 for poly in forbidden_polygons]

    def overlap(cell, box, parts):
        area = sum(abs(_signed_area(piece)) for tri, tri_box in parts if _bboxes_overlap(box, tri_box)
                   and (piece := _convex_polygon_intersection(cell, tri)))
        if not math.isfinite(area):
            raise ValueError("Intersection area is outside the supported numeric range.")
        return area

    poses, cells = [], []
    violations = 0
    first_violation = None
    max_outside = max_obstacle = max_steering = max_inflation = 0.0
    nonforward = 0

    def pose(front, heading):
        return dict(front_bumper_xy=world(front), heading_rad=heading,
                    rear_axle_xy=world((front[0] - front_distance * math.cos(heading), front[1] - front_distance * math.sin(heading))),
                    front_axle_xy=world((front[0] - dimensions['front_overhang_m'] * math.cos(heading), front[1] - dimensions['front_overhang_m'] * math.sin(heading))),
                    body_polygon=[world(p) for p in _rectangle(front, heading, length, width)])
    if include_geometry:
        poses.append(pose(points[0], theta))
    cell_count = 0
    for first, last, distance, count in segments:
        ds = distance / count
        direction = math.atan2(last[1] - first[1], last[0] - first[0])
        for i in range(count):
            a = tuple(first[k] + (last[k] - first[k]) * i / count for k in (0, 1))
            b = tuple(first[k] + (last[k] - first[k]) * (i + 1) / count for k in (0, 1))
            alpha = _angle(direction - theta)
            if abs(alpha) >= math.pi / 2 - 1e-8:
                nonforward += 1
            steering = math.atan2(wheelbase / front_distance * math.sin(alpha), math.cos(alpha))
            max_steering = max(max_steering, abs(math.degrees(steering)))
            new_theta = theta + _angle(direction - 2 * math.atan(math.tan(alpha / 2) * math.exp(-ds / front_distance)) - theta)
            inflation = math.hypot(length, width / 2) * (2 * math.sin(abs(new_theta - theta) / 4)**2)
            max_inflation = max(max_inflation, inflation)
            hull = _convex_hull([p for front in (a, b) for heading in (theta, new_theta)
                                 for p in _rectangle(front, heading, length, width)])
            pad = inflation + margin
            if pad:
                hull = _convex_hull([(p[0] + dx, p[1] + dy) for p in hull for dx in (-pad, pad) for dy in (-pad, pad)])
            box, area = _bbox(hull), abs(_signed_area(hull))
            if not math.isfinite(area) or area <= 0 or any(not math.isfinite(v) for p in hull for v in p):
                raise ValueError("Swept body geometry is outside the supported numeric range.")
            outside = max(0.0, area - overlap(hull, box, triangles)) if road else 0.0
            obstacle = max((overlap(hull, box, parts) for parts in obstacles), default=0.0)
            max_outside, max_obstacle = max(max_outside, outside), max(max_obstacle, obstacle)
            if max(outside, obstacle) > max(1e-8, area * 1e-9):
                violations += 1
                if first_violation is None:
                    first_violation = dict(cell_index=cell_count, outside_area_m2=outside, forbidden_overlap_m2=obstacle,
                                            swept_cell=[world(p) for p in hull])
            cell_count += 1
            theta = new_theta
            if include_geometry:
                poses.append(pose(b, theta))
                cells.append([world(p) for p in hull])
    space_status = ('outside_declared_space' if violations else 'inside_declared_space' if road
                    else 'clear_of_declared_obstacles' if obstacles else 'not_assessed')
    return dict(schema='torii.rigid-vehicle-sweep/v1', model='low_speed_rigid_no_slip', reference_point='front_bumper_center',
                vehicle=vehicle, dimensions_m=dimensions, assessed_width_m=width, include_mirrors=include_mirrors,
                initial_heading_rad=initial_angle, sampling_step_limit_m=step, maximum_cell_count=100_000,
                overlap_absolute_tolerance_m2=1e-8, overlap_relative_area_tolerance=1e-9,
                clearance_m=margin, cell_count=cell_count, poses=poses, swept_cells=cells,
                maximum_rotational_inflation_m=max_inflation, violating_cell_count=violations,
                first_violation=first_violation, maximum_outside_cell_area_m2=max_outside,
                maximum_forbidden_overlap_m2=max_obstacle, space_check=space_status,
                nonforward_or_singular_samples=nonforward, maximum_required_steering_deg=max_steering,
                steering_feasibility='not_verified' if steering_limit is None else 'within_declared_limit' if max_steering <= steering_limit and not nonforward else 'outside_declared_limit',
                rbsv_certification=False, steering_rate_check='not_assessed',
                claim_boundary='A conservative rectangular rigid-body sweep for a declared front-point path and initial heading. Road polygons and vehicle parameters require evidence. It does not certify RBSV vehicles, articulated buses, tyre slip, steering speed, or actual road operation.')
