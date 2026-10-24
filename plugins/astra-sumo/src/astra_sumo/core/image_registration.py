"""Register a flat map screenshot from independent pixel and geographic points."""

import math

import numpy as np
from PIL import Image
from pyproj import Transformer


def register_map_screenshot(image, crs, control_points, crop_px=None):
    """Return a north-up image, projected outer bounds, and fit/check residuals."""
    width, height = image.size
    pixels = np.array([point["pixel"] for point in control_points], dtype=float)
    if np.any(pixels < 0) or np.any(pixels > [width, height]):
        raise ValueError("Control points must lie inside the original screenshot.")
    if len({point["id"] for point in control_points}) != len(control_points):
        raise ValueError("Control point identifiers must be unique.")
    fitting = np.array([point["role"] == "fit" for point in control_points])
    if fitting.sum() < 3:
        raise ValueError("Use at least three noncollinear fit points.")
    transform = Transformer.from_crs(4326, crs, always_xy=True, allow_ballpark=False)
    world = np.array([transform.transform(*point["lon_lat"], errcheck=True) for point in control_points])
    center_pixel, center_world = pixels[fitting].mean(axis=0), world[fitting].mean(axis=0)
    design = np.column_stack((pixels - center_pixel, np.ones(len(pixels))))
    coefficients, _, rank, _ = np.linalg.lstsq(design[fitting], world[fitting] - center_world, rcond=None)
    if rank != 3:
        raise ValueError("Fit points must span two image directions.")
    linear = coefficients[:2].T
    if not np.isfinite(coefficients).all() or abs(np.linalg.det(linear)) <= np.linalg.norm(linear, ord=2)**2 * 1e-8:
        raise ValueError("The screenshot registration collapses a map direction.")
    offset = coefficients[2] + center_world - linear @ center_pixel
    inverse = np.linalg.inv(linear)
    predictions = pixels @ linear.T + offset
    errors = predictions - world
    rows = [{**point, "projected_xy": expected.tolist(), "fitted_xy": predicted.tolist(),
             "error_m": float(np.linalg.norm(error)), "error_px": float(np.linalg.norm(inverse @ error))}
            for point, expected, predicted, error in zip(control_points, world, predictions, errors)]
    x0, y0, x1, y1 = crop_px or (0, 0, width, height)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError("The crop must lie inside the screenshot.")
    corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) @ linear.T + offset
    west, south = corners.min(axis=0)
    east, north = corners.max(axis=0)
    resolution = max(math.sqrt(abs(np.linalg.det(linear))), (east-west) / 4096, (north-south) / 4096)
    size = (max(1, round((east-west) / resolution)), max(1, round((north-south) / resolution)))
    map_per_pixel = np.diag([(east-west) / size[0], -(north-south) / size[1]])
    sample_linear = inverse @ map_per_pixel
    sample_offset = inverse @ (np.array([west, north]) - offset) - [x0, y0]
    affine = (*sample_linear[0], sample_offset[0], *sample_linear[1], sample_offset[1])
    registered = image.crop((x0, y0, x1, y1)).transform(size, Image.Transform.AFFINE, affine,
        resample=Image.Resampling.BICUBIC, fillcolor=(245, 245, 245))
    def summary(role):
        selected = [point for point in rows if point["role"] == role]
        return {"count": len(selected),
                "rmse_m": math.sqrt(sum(point["error_m"]**2 for point in selected) / len(selected)) if selected else None,
                "maximum_error_m": max((point["error_m"] for point in selected), default=None),
                "rmse_px": math.sqrt(sum(point["error_px"]**2 for point in selected) / len(selected)) if selected else None}
    bounds = tuple(map(float, (west, south, east, north)))
    return registered, bounds, {"method": "independent_pixel_lon_lat_affine/v1", "crs": crs.to_string(),
        "pixel_origin": "upper_left_outer_edge", "source_size_px": [width, height], "crop_px": [x0, y0, x1, y1],
        "source_footprint_projected_xy": corners.tolist(), "numpy": np.__version__,
        "registered_size_px": list(size), "registered_bbox": bounds, "registered_north_up": True,
        "pixel_to_projected_xy": np.column_stack((linear, offset)).tolist(),
        "points": rows, "fit": summary("fit"), "check": summary("check"), "field_accuracy_established": False,
        "claim_boundary": "Control points register a flat screenshot. Check points are excluded from fitting. Residuals do not establish imagery or road accuracy. Perspective views require another image."}
