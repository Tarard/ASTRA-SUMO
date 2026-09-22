from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from .osm_network import _net_xy_to_latlon, parse_bbox


def derive_reference_net_bbox(
    reference_net_file: str | Path,
    *,
    padding_m: float = 75.0,
    xy_to_latlon_func: Callable[[float, float], tuple[float, float]] | None = None,
) -> dict[str, Any]:
    net_file = Path(reference_net_file)
    if padding_m < 0:
        return _failure(net_file, "padding_m must be non-negative")
    if not net_file.exists():
        return _failure(net_file, f"reference SUMO .net.xml not found: {net_file}")

    try:
        root = ET.parse(net_file).getroot()
        xy_bounds = _read_geometry_xy_bounds(root)
    except (OSError, ET.ParseError, KeyError, ValueError) as exc:
        return _failure(net_file, f"{type(exc).__name__}: {exc}")

    location = root.find("location")
    orig_boundary = location.attrib.get("origBoundary", "") if location is not None else ""
    conv_boundary = location.attrib.get("convBoundary", "") if location is not None else ""
    if xy_bounds is None:
        return _failure(
            net_file,
            "reference SUMO .net.xml has no usable non-internal junction or lane geometry",
            orig_boundary=orig_boundary,
            conv_boundary=conv_boundary,
        )

    converter = xy_to_latlon_func
    converter_warnings: list[str] = []
    if converter is None:
        converter, converter_warnings = _sumo_coordinate_converter(net_file)
    if converter is None:
        return _blocked(
            net_file,
            "reference geometry bbox requires a usable SUMO geo projection",
            orig_boundary=orig_boundary,
            conv_boundary=conv_boundary,
            xy_bounds=xy_bounds,
            warnings=converter_warnings,
        )

    min_x, min_y, max_x, max_y = _pad_xy_bounds(xy_bounds, padding_m)
    try:
        lat_lon_points = [converter(x, y) for x, y in _corner_points(min_x, min_y, max_x, max_y)]
        lats = [float(lat) for lat, _lon in lat_lon_points]
        lons = [float(lon) for _lat, lon in lat_lon_points]
        bbox = _format_bbox(min(lons), min(lats), max(lons), max(lats))
        parse_bbox(bbox)
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        return _blocked(
            net_file,
            f"reference geometry bbox projection failed: {type(exc).__name__}: {exc}",
            orig_boundary=orig_boundary,
            conv_boundary=conv_boundary,
            xy_bounds=xy_bounds,
            warnings=converter_warnings,
        )

    warnings = list(converter_warnings)
    if orig_boundary:
        warnings.append(
            "reference .net.xml origBoundary was not used because it can remain stale after clipping"
        )
    width_m = max_x - min_x
    height_m = max_y - min_y
    return {
        "status": "pass",
        "claim_status": "diagnostic-demo",
        "reference_bbox_status": "derived_from_reference_geometry",
        "reference_net_file": str(net_file),
        "reference_bbox": bbox,
        "reference_bbox_source": "junction_and_lane_geometry",
        "reference_bbox_padding_m": float(padding_m),
        "reference_orig_boundary": orig_boundary,
        "reference_conv_boundary": conv_boundary,
        "reference_xy_bounds": _xy_bounds_dict(xy_bounds),
        "reference_padded_xy_bounds": {
            "min_x": round(min_x, 3),
            "min_y": round(min_y, 3),
            "max_x": round(max_x, 3),
            "max_y": round(max_y, 3),
        },
        "reference_bbox_width_m": round(width_m, 3),
        "reference_bbox_height_m": round(height_m, 3),
        "reference_bbox_area_km2": round((width_m * height_m) / 1_000_000.0, 6),
        "warnings": warnings,
    }


def _read_geometry_xy_bounds(root: ET.Element) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []
    for junction in root.findall("junction"):
        junction_id = junction.attrib.get("id", "")
        junction_type = junction.attrib.get("type", "")
        if junction_id.startswith(":") or junction_type == "internal":
            continue
        _append_point(points, junction.attrib.get("x"), junction.attrib.get("y"))

    for edge in root.findall("edge"):
        edge_id = edge.attrib.get("id", "")
        if edge_id.startswith(":") or edge.attrib.get("function") == "internal":
            continue
        points.extend(_parse_shape(edge.attrib.get("shape", "")))
        for lane in edge.findall("lane"):
            points.extend(_parse_shape(lane.attrib.get("shape", "")))

    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _append_point(points: list[tuple[float, float]], x_value: str | None, y_value: str | None) -> None:
    if x_value is None or y_value is None:
        return
    try:
        x = float(x_value)
        y = float(y_value)
    except ValueError:
        return
    if math.isfinite(x) and math.isfinite(y):
        points.append((x, y))


def _parse_shape(value: str) -> list[tuple[float, float]]:
    points = []
    for item in value.split():
        if "," not in item:
            continue
        x_value, y_value = item.split(",", 1)
        try:
            x = float(x_value)
            y = float(y_value)
        except ValueError:
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    return points


def _sumo_coordinate_converter(
    net_file: Path,
) -> tuple[Callable[[float, float], tuple[float, float]] | None, list[str]]:
    try:
        import sumolib.net  # type: ignore
    except ModuleNotFoundError:
        return None, ["sumolib is required to derive a reference geometry bbox"]

    try:
        net = sumolib.net.readNet(str(net_file))
    except Exception as exc:  # noqa: BLE001 - optional sumolib parser failure is returned as evidence.
        return None, [f"sumolib could not read reference net for bbox derivation: {type(exc).__name__}: {exc}"]
    return lambda x, y: _net_xy_to_latlon(net, x, y), []


def _pad_xy_bounds(bounds: tuple[float, float, float, float], padding_m: float) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = bounds
    return min_x - padding_m, min_y - padding_m, max_x + padding_m, max_y + padding_m


def _corner_points(min_x: float, min_y: float, max_x: float, max_y: float) -> tuple[tuple[float, float], ...]:
    return (
        (min_x, min_y),
        (min_x, max_y),
        (max_x, min_y),
        (max_x, max_y),
    )


def _format_bbox(west: float, south: float, east: float, north: float) -> str:
    return f"{west:.7f},{south:.7f},{east:.7f},{north:.7f}"


def _xy_bounds_dict(bounds: tuple[float, float, float, float]) -> dict[str, float]:
    min_x, min_y, max_x, max_y = bounds
    return {
        "min_x": round(min_x, 3),
        "min_y": round(min_y, 3),
        "max_x": round(max_x, 3),
        "max_y": round(max_y, 3),
    }


def _failure(
    net_file: Path,
    error: str,
    *,
    orig_boundary: str = "",
    conv_boundary: str = "",
) -> dict[str, Any]:
    return {
        "status": "fail",
        "claim_status": "construction-invalid",
        "reference_bbox_status": "failed",
        "reference_net_file": str(net_file),
        "reference_bbox": "",
        "reference_bbox_source": "",
        "reference_bbox_padding_m": "",
        "reference_orig_boundary": orig_boundary,
        "reference_conv_boundary": conv_boundary,
        "error": error,
        "warnings": [error],
    }


def _blocked(
    net_file: Path,
    error: str,
    *,
    orig_boundary: str,
    conv_boundary: str,
    xy_bounds: tuple[float, float, float, float],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "status": "blocked",
        "claim_status": "blocked",
        "reference_bbox_status": "blocked",
        "reference_net_file": str(net_file),
        "reference_bbox": "",
        "reference_bbox_source": "junction_and_lane_geometry",
        "reference_bbox_padding_m": "",
        "reference_orig_boundary": orig_boundary,
        "reference_conv_boundary": conv_boundary,
        "reference_xy_bounds": _xy_bounds_dict(xy_bounds),
        "error": error,
        "warnings": [*warnings, error],
    }
