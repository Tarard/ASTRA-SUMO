"""Find image-supported boundaries near Hamburg's generalized road references.

Google US8938094B1 supplies the road-prior and moving-probe idea, not a model
or trained weights. These contrast probes are a diagnostic adaptation.
"""

from collections import Counter
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from pyproj import Transformer
from scipy import ndimage

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256

_MOTOR_SURFACES = {"Fahrbahn", "Hauptfahrstreifen (HFS)", "1. Überholstreifen (UE1)",
                   "2. Überholstreifen (UE2)", "3. Überholstreifen (UE3)",
                   "Linksabbiegefahrstreifen", "Rechtsabbiegefahrstreifen"}


def _continuous_offsets(scores, *, maximum_index_step):
    """Keep the best continuous sequence instead of following isolated bright edges."""
    index = np.arange(scores.shape[1])
    allowed = np.abs(index[:, None] - index[None, :]) <= maximum_index_step
    previous = scores[0].copy()
    parents = []
    for row in scores[1:]:
        choices = np.where(allowed, previous[:, None], -np.inf)
        parent = np.argmax(choices, axis=0)
        previous = row + choices[parent, index]
        parents.append(parent)
    chosen = [int(np.argmax(previous))]
    for parent in reversed(parents):
        chosen.append(int(parent[chosen[-1]]))
    return chosen[::-1]


def trace_boundary_offsets(rgb, reference_pixels, *, metres_per_pixel, max_offset_m=3.0,
                           minimum_contrast=0.06, minimum_margin=0.01, max_offset_step_m=1.0):
    """Search each normal with a short longitudinal probe. Weak or rival edges remain unknown."""
    pixels = np.asarray(rgb, dtype=float)
    guide = np.asarray(reference_pixels, dtype=float)
    if pixels.ndim != 3 or pixels.shape[2] != 3 or not np.isfinite(pixels).all() or pixels.min() < 0 or pixels.max() > 1:
        raise ValueError("Use a finite RGB image scaled to [0, 1].")
    if guide.ndim != 2 or guide.shape[1] != 2 or len(guide) < 2 or not np.isfinite(guide).all():
        raise ValueError("Use at least two finite guide points.")
    if any(not math.isfinite(v) or v <= 0 for v in (metres_per_pixel, max_offset_m, minimum_contrast, minimum_margin, max_offset_step_m)):
        raise ValueError("Boundary search dimensions and thresholds must be positive.")
    offsets = np.linspace(-max_offset_m, max_offset_m, max(3, math.ceil(2 * max_offset_m / metres_per_pixel) + 1))
    results = []
    profiles = []
    for i, point in enumerate(guide):
        border = (max_offset_m + 1) / metres_per_pixel
        if not (border <= point[0] <= pixels.shape[1] - 1 - border and border <= point[1] <= pixels.shape[0] - 1 - border):
            results.append(dict(reference_px=point.tolist(), point_px=None, supported=False, reason="image_border_reference"))
            continue
        delta = guide[min(i + 1, len(guide) - 1)] - guide[max(i - 1, 0)]
        length = np.linalg.norm(delta)
        if length < 1e-6:
            results.append(dict(reference_px=point.tolist(), point_px=None, supported=False, reason="undefined_normal"))
            continue
        tangent = delta / length
        normal = np.array([-tangent[1], tangent[0]])
        centers = point + offsets[:, None] / metres_per_pixel * normal
        # A moving rectangle averages 2 m along the boundary; the two banks are 0.8 m apart.
        samples = centers[:, None, None, :] + np.linspace(-1, 1, 5)[None, :, None, None] / metres_per_pixel * tangent
        samples = samples + np.array([-0.4, 0.4])[None, None, :, None] / metres_per_pixel * normal
        valid = ((samples[..., 0] >= 0) & (samples[..., 0] <= pixels.shape[1] - 1)
                 & (samples[..., 1] >= 0) & (samples[..., 1] <= pixels.shape[0] - 1)).all(axis=(1, 2))
        values = np.stack([ndimage.map_coordinates(pixels[:, :, c], [samples[..., 1], samples[..., 0]], order=1, mode='nearest') for c in range(3)], axis=-1)
        scores = np.linalg.norm(values[:, :, 1].mean(axis=1) - values[:, :, 0].mean(axis=1), axis=1) / math.sqrt(3)
        scores[~valid] = -1
        profiles.append((i, centers, scores))
        results.append(dict(reference_px=point.tolist()))
    blocks = []
    for profile in profiles:
        if not blocks or profile[0] != blocks[-1][-1][0] + 1:
            blocks.append([])
        blocks[-1].append(profile)
    for block in blocks:
        selected = _continuous_offsets(np.array([p[2] for p in block]),
            maximum_index_step=max(0, math.floor(max_offset_step_m / (offsets[1] - offsets[0]))))
        for (i, centers, scores), best in zip(block, selected):
            _set_boundary_result(results[i], centers, scores, offsets, best, minimum_contrast, minimum_margin)
    return results


def _set_boundary_result(result, centers, scores, offsets, best, minimum_contrast, minimum_margin):
    rivals = scores[np.abs(offsets - offsets[best]) >= 1.0]
    runner_up = max(0.0, float(rivals.max(initial=0)))
    contrast = float(scores[best])
    supported = contrast >= minimum_contrast and contrast - runner_up >= minimum_margin
    reason = "image_contrast_candidate" if supported else "weak_or_ambiguous_contrast"
    if best in (0, len(offsets) - 1):
        supported, reason = False, "search_limit_reached"
    result.update(dict(point_px=centers[best].tolist() if supported else None,
                        offset_m=round(float(offsets[best]), 3), contrast=round(max(0, contrast), 4),
                        contrast_margin=round(max(0, contrast - runner_up), 4), supported=supported, reason=reason))


def _read_reference(record):
    path = Path(record['path']).resolve(strict=True)
    if file_sha256(path) != record['sha256']:
        raise ValueError(f"Reference hash mismatch: {path.name}")
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('type') != 'FeatureCollection' or not isinstance(value.get('features'), list):
        raise ValueError("Reference must be a GeoJSON FeatureCollection.")
    crs = value.get('crs', {}).get('properties', {}).get('name', '')
    if crs and not any(k in crs for k in ('CRS84', '4326')):
        raise ValueError("Hamburg references must use longitude and latitude.")
    return value['features'], dict(path=str(path), sha256=record['sha256'])


def build_hamburg_road_edge_evidence(*, aerial_image, bbox, aerial_year, references, output_dir):
    """Freeze image boundary candidates and a soft road prior for the existing movement tracer."""
    from .hamburg_junctions.geometry import _marching_loop

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new boundary output directory.")
    if len(bbox) != 4 or not all(map(math.isfinite, bbox)) or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError("Use finite ordered EPSG:25832 image bounds.")
    if set(references) != {'topology', 'cross_sections'}:
        raise ValueError("Provide topology and cross_sections source identities.")
    topology, topology_identity = _read_reference(references['topology'])
    sections, sections_identity = _read_reference(references['cross_sections'])
    image_path = Path(aerial_image).resolve(strict=True)
    image_identity = dict(path=str(image_path), sha256=file_sha256(image_path))
    with Image.open(image_path) as source:
        image = source.convert('RGB')
    spacing = (bbox[2] - bbox[0]) / image.width
    if not math.isclose(spacing, (bbox[3] - bbox[1]) / image.height, rel_tol=1e-4):
        raise ValueError("Boundary search requires square ground pixels.")
    transform = Transformer.from_crs('EPSG:4326', 'EPSG:25832', always_xy=True)

    def pixel(lonlat):
        if len(lonlat) < 2 or not all(math.isfinite(v) for v in lonlat[:2]) or abs(lonlat[0]) > 180 or abs(lonlat[1]) > 90:
            raise ValueError("Reference coordinates must be finite longitude and latitude.")
        x, y = transform.transform(*lonlat[:2])
        return ((x - bbox[0]) / spacing, (bbox[3] - y) / spacing)

    def world(point):
        return [bbox[0] + point[0] * spacing, bbox[3] - point[1] * spacing]

    missing = []
    roads = Image.new('1', image.size, 0)
    road_draw = ImageDraw.Draw(roads)
    road_records = []
    for feature in topology:
        geometry = feature.get('geometry')
        if not geometry:
            missing.append(str(feature.get('id')))
            continue
        if geometry['type'] not in ('LineString', 'MultiLineString'):
            raise ValueError("Road topology must contain line geometry.")
        lines = [geometry['coordinates']] if geometry['type'] == 'LineString' else geometry['coordinates']
        for line in lines:
            points = [pixel(p) for p in line]
            if len(points) >= 2:
                road_draw.line(points, fill=1, width=max(1, round(40 / spacing)))
        road_records.append(dict(id=feature.get('id'), properties=feature.get('properties', {})))
    motor = Image.new('1', image.size, 0)
    classes = Counter()
    for feature in sections:
        kind = feature.get('properties', {}).get('art_klartext', '')
        classes[kind] += 1
        geometry = feature.get('geometry')
        if not geometry:
            missing.append(str(feature.get('id')))
            continue
        if geometry['type'] not in ('Polygon', 'MultiPolygon'):
            raise ValueError("Cross sections must contain polygon geometry.")
        if kind not in _MOTOR_SURFACES:
            continue
        polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
        for polygon in polygons:
            part = Image.new('1', image.size, 0)
            draw = ImageDraw.Draw(part)
            for index, ring in enumerate(polygon):
                if len(ring) >= 4:
                    draw.polygon([pixel(p) for p in ring], fill=int(index == 0))
            motor = Image.fromarray(np.asarray(motor) | np.asarray(part))
    motor_mask = np.asarray(motor, dtype=bool) & np.asarray(roads, dtype=bool)
    # Historical trapezoids only influence preference. They never forbid current road space.
    prior = np.ones(motor_mask.shape, dtype=np.float32)
    if motor_mask.any():
        distance = ndimage.distance_transform_edt(~motor_mask) * spacing
        prior = (0.35 + 0.65 * np.exp(-0.5 * (distance / 3.0) ** 2)).astype(np.float32)
    labels, count = ndimage.label(motor_mask)
    boxes = ndimage.find_objects(labels)
    rgb = np.asarray(image, dtype=float) / 255
    traces = []
    boundary_samples = []
    review = image.copy()
    draw = ImageDraw.Draw(review)
    for label in range(1, count + 1):
        box = boxes[label - 1]
        component = labels[box] == label
        if np.count_nonzero(component) * spacing**2 < 25:
            continue
        loop = [(x + box[1].start - 1, y + box[0].start - 1) for x, y in _marching_loop(np.pad(component, 1))]
        if len(loop) < 3:
            continue
        guide = [loop[0]]
        for point in loop[1:]:
            if math.dist(point, guide[-1]) * spacing >= 2:
                guide.append(point)
        if len(guide) < 4:
            continue
        draw.line(guide + [guide[0]], fill=(255, 180, 40), width=2)
        rows = trace_boundary_offsets(rgb, guide, metres_per_pixel=spacing)
        boundary_samples.extend({**row, 'reference_epsg25832': world(row['reference_px']),
                                 'point_epsg25832': world(row['point_px']) if row.get('point_px') else None}
                                for row in rows)
        chain = []
        for row in [*rows, {'supported': False}]:
            p = row.get('point_px')
            if row['supported'] and (not chain or math.dist(chain[-1], p) * spacing <= 5):
                chain.append(p)
                continue
            if len(chain) >= 4:
                traces.append([world(p) for p in chain])
                draw.line([tuple(p) for p in chain], fill=(0, 255, 255), width=3)
            chain = [p] if row['supported'] else []
    destination.mkdir(parents=True, exist_ok=False)
    prior_file = destination / 'motor-road-prior.npy'
    np.save(prior_file, prior, allow_pickle=False)
    review_file = destination / 'boundary-review.png'
    review.save(review_file)
    report = dict(schema='torii.hamburg-aerial-road-edge-evidence/v1', status='pass', decision='review_required',
        claim_status='diagnostic-demo', crs='EPSG:25832', bbox_epsg25832=list(bbox), aerial_year=aerial_year,
        reference_survey_year=2016, reference_geometry='generalized_trapezoids',
        inputs=dict(aerial_image=image_identity, topology=topology_identity, cross_sections=sections_identity),
        topology_records=road_records, cross_section_classes=dict(classes), missing_geometry_ids=missing,
        supported_boundary_chain_count=len(traces), boundary_chains_epsg25832=traces,
        boundary_samples=boundary_samples,
        sample_support_counts=dict(Counter(row['reason'] for row in boundary_samples)),
        reference_motor_area_m2=round(float(motor_mask.sum() * spacing**2), 2),
        permissions_changed=False, lane_counts_changed=False,
        workflow_influence='soft_motor_road_prior_only',
        prior=dict(path=str(prior_file), sha256=file_sha256(prior_file)),
        review_image=dict(path=str(review_file), sha256=file_sha256(review_file)),
        settings=dict(search_half_width_m=3.0, minimum_contrast=0.06, minimum_contrast_margin=0.01,
                      station_spacing_m=2.0, maximum_offset_step_m=1.0, topology_search_half_width_m=20.0),
        claim_boundary='Orange lines are 2016 generalized reference boundaries. Cyan lines are local image-contrast candidates, not confirmed curbs or lane boundaries. Gaps remain unknown. Road topology supplies location and metadata, not current permissions. The prior can influence movement tracing but cannot authorize lane-count, access, or junction-topology changes.')
    for record in report['inputs'].values():
        if file_sha256(Path(record['path'])) != record['sha256']:
            raise ValueError("A boundary input changed during extraction.")
    report_file = destination / 'road-edge-evidence.json'
    write_json_atomic(report_file, report, ensure_ascii=False)
    return {**report, 'report_file': str(report_file)}
