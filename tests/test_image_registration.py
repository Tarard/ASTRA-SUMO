import copy
import json

import numpy as np
from PIL import Image
from pyproj import CRS, Transformer
import pytest

from astra_sumo.core.image_registration import register_map_screenshot
from astra_sumo.core.network_aerial_review import render_network_aerial_review
from test_network_aerial_review import _fixture


def _points(linear=None):
    linear = np.array(linear if linear is not None else [[0.5, 0], [0, -0.5]])
    offset = np.array([500000, 5800100])
    inverse = Transformer.from_crs(32632, 4326, always_xy=True)
    return [{"id": str(index), "pixel": list(pixel), "lon_lat": list(inverse.transform(*(linear @ pixel + offset))),
             "role": "check" if index == 3 else "fit"}
            for index, pixel in enumerate(([0, 0], [200, 0], [0, 200], [200, 200]))]


def test_affine_registration_excludes_check_points_and_keeps_rotation():
    linear = [[0.5, 0.12], [0.08, -0.5]]
    points = _points(linear)
    image = Image.new("RGB", (200, 200), (71, 82, 93))
    first, bounds, report = register_map_screenshot(image, CRS.from_epsg(32632), points, (20, 20, 180, 180))
    assert np.asarray(report["pixel_to_projected_xy"])[:, :2] == pytest.approx(np.array(linear), abs=1e-8)
    assert report["fit"]["rmse_m"] < 1e-7 and report["check"]["rmse_m"] < 1e-7
    assert not report["field_accuracy_established"]
    assert first.getpixel((first.width // 2, first.height // 2)) == (71, 82, 93)
    changed = copy.deepcopy(points)
    changed[-1]["lon_lat"][0] += 0.0001
    second, same_bounds, altered = register_map_screenshot(image, CRS.from_epsg(32632), changed, (20, 20, 180, 180))
    assert first.tobytes() == second.tobytes() and same_bounds == bounds
    assert altered["check"]["rmse_m"] > 5
    assert altered["fit"] == report["fit"]


@pytest.mark.parametrize("fault", ["collinear", "outside", "duplicate", "collapsed", "crop"])
def test_bad_registration_does_not_produce_an_image(fault):
    points = _points()
    crop = None
    if fault == "collinear":
        points[2]["pixel"] = [100, 0]
    elif fault == "outside":
        points[-1]["pixel"] = [201, 200]
    elif fault == "duplicate":
        points[-1]["id"] = points[0]["id"]
    elif fault == "collapsed":
        points = _points([[0.5, 0], [0, 0]])
    else:
        crop = (0, 0, 201, 200)
    with pytest.raises(ValueError):
        register_map_screenshot(Image.new("RGB", (200, 200)), CRS.from_epsg(32632), points, crop)


def test_screenshot_before_and_after_share_one_registration_and_background(tmp_path):
    path, request = _fixture(tmp_path)
    request["aerial"].pop("bbox")
    request["aerial"]["control_points"] = _points()
    request["aerial"]["crop_px"] = [0, 0, 200, 200]
    request.pop("views")
    request.update(source_label="Before: OSM import", candidate_label="After: ASTRA result")
    path.write_text(json.dumps(request), encoding="utf-8")
    first = render_network_aerial_review(request_file=path, output_dir=tmp_path / "first")
    second = render_network_aerial_review(request_file=path, output_dir=tmp_path / "second")
    assert first["artifacts"] == second["artifacts"]
    assert first["inputs_unchanged"] and first["image_registration"]["check"]["count"] == 1
    assert first["image_registration"]["check"]["rmse_m"] < 1e-7
    assert first["labels"] == {"source_net": "Before: OSM import", "candidate_net": "After: ASTRA result"}
    view = first["views"][0]
    assert view["visible_objects"]["source_net"] == view["visible_objects"]["candidate_net"]
    assert "Check points excluded from fitting: 1" in (tmp_path / "first/review.html").read_text(encoding="utf-8")
    with Image.open(tmp_path / "first/junction-aerial.png") as background:
        assert background.getpixel((20, 20)) == (200, 10, 20)
    with Image.open(tmp_path / "first/junction-candidate_net.png") as overlay:
        assert overlay.getpixel((20, 104)) == (0, 230, 255)
    with Image.open(tmp_path / "first" / view["files"]["source_net"]) as before, Image.open(
            tmp_path / "first" / view["files"]["candidate_net"]) as after:
        # The title differs. The map and legend use identical pixels.
        assert before.crop((0, 24, before.width, before.height)).tobytes() == after.crop((0, 24, after.width, after.height)).tobytes()
