import json

import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer

from torii_sumo.core.candidate_contracts import file_sha256
from torii_sumo.core.hamburg_aerial_road_edges import (
    build_hamburg_road_edge_evidence, trace_boundary_offsets,
)


def test_continuous_probe_rejects_an_isolated_stronger_side_edge():
    from torii_sumo.core.hamburg_aerial_road_edges import _continuous_offsets
    scores = np.full((7, 5), 0.05)
    scores[:, 2] = 0.4
    scores[3, 4] = 0.9
    chosen = _continuous_offsets(scores, maximum_index_step=1)
    assert chosen == [2] * 7


def test_boundary_search_uses_the_image_and_leaves_blank_sections_unknown():
    rgb = np.full((100, 100, 3), 0.2)
    rgb[42:70, :] = 0.7
    guide = [(x, 38.0) for x in range(15, 85, 5)]
    rows = trace_boundary_offsets(rgb, guide, metres_per_pixel=0.5, max_offset_m=3)
    accepted = [r for r in rows if r['supported']]
    assert len(accepted) >= 10
    assert max(abs(r['point_px'][1] - 42) for r in accepted) <= 1
    blank = trace_boundary_offsets(np.full((100, 100, 3), 0.7), guide, metres_per_pixel=0.5)
    assert not any(r['supported'] for r in blank)
    # A much stronger edge outside the declared search band must not attract the trace.
    rgb[10:20, :] = 1
    assert max(abs(r['point_px'][1] - 42) for r in trace_boundary_offsets(
        rgb, guide, metres_per_pixel=0.5, max_offset_m=3) if r['supported']) <= 1


def test_reference_dates_and_unknown_classes_do_not_become_current_permissions(tmp_path):
    transform = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True)
    bbox = [565000, 5934000, 565040, 5934040]
    ring = [[565000, 5934010], [565040, 5934010], [565040, 5934030], [565000, 5934030], [565000, 5934010]]
    image = tmp_path / 'aerial.png'
    Image.new('RGB', (80, 80), (170, 170, 170)).save(image)
    def source(name, features):
        path = tmp_path / name
        path.write_text(json.dumps(dict(type='FeatureCollection', features=features)), encoding='utf-8')
        return dict(path=str(path), sha256=file_sha256(path))
    topology = source('roads.json', [dict(type='Feature', id='road', properties={'strassenname': 'Test'},
        geometry=dict(type='LineString', coordinates=[transform.transform(565000, 5934020), transform.transform(565040, 5934020)]))])
    sections = source('sections.json', [dict(type='Feature', id='surface', properties={'art_klartext': 'Fahrbahn'},
        geometry=dict(type='Polygon', coordinates=[[transform.transform(*p) for p in ring]])),
        dict(type='Feature', id='missing', properties={'art_klartext': 'unknown'}, geometry=None)])
    report = build_hamburg_road_edge_evidence(
        aerial_image=image, bbox=bbox, aerial_year=2024, references=dict(topology=topology, cross_sections=sections),
        output_dir=tmp_path / 'edges')
    assert report['decision'] == 'review_required'
    assert report['reference_survey_year'] == 2016
    assert report['supported_boundary_chain_count'] == 0
    assert report['missing_geometry_ids'] == ['missing']
    assert report['permissions_changed'] is False
    assert file_sha256(image) == report['inputs']['aerial_image']['sha256']
    with pytest.raises(ValueError, match='hash'):
        build_hamburg_road_edge_evidence(aerial_image=image, bbox=bbox, aerial_year=2024,
            references=dict(topology={**topology, 'sha256': '0' * 64}, cross_sections=sections), output_dir=tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()
