"""Draw immutable SUMO lane geometry over a declared north-up aerial image."""

from __future__ import annotations

from datetime import date
import hashlib
from html import escape
import io
import math
from pathlib import Path
import platform
from typing import Literal
import xml.etree.ElementTree as ET

import PIL
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator
import pyproj
from pyproj import CRS, Transformer
import shapely
from shapely.affinity import translate
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import transform as transform_geometry

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .reference_spatial_registration import read_network_coordinate_frame


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _Artifact(_Model):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Bounds(_Model):
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def ordered(self):
        if self.bbox[0] >= self.bbox[2] or self.bbox[1] >= self.bbox[3]:
            raise ValueError("Use increasing easting and northing bounds.")
        return self


class _Aerial(_Artifact, _Bounds):
    crs: str
    north_up: Literal[True]
    capture_date: date | None
    capture_year: int | None = Field(default=None, ge=1900, le=2200)
    authority: str = Field(min_length=1)
    license: str = Field(min_length=1)
    source_url: str = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_date(self):
        if self.capture_date and self.capture_year and self.capture_date.year != self.capture_year:
            raise ValueError("The aerial date and year disagree.")
        return self


class _View(_Bounds):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class _Request(_Model):
    schema_id: Literal["astra.network-aerial-review-request/v1"] = Field(alias="schema")
    candidate_net: _Artifact
    source_net: _Artifact | None = None
    aerial: _Aerial
    target_date: date | None
    views: list[_View] = Field(min_length=1, max_length=24)
    width_px: int = Field(default=1200, ge=128, le=4096, strict=True)


def build_network_aerial_review_request_schema() -> dict:
    return _Request.model_json_schema()


def _metric_crs(value: str) -> CRS:
    crs = CRS.from_user_input(value)
    if not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]):
        raise ValueError("Use a projected coordinate system with metre units.")
    return crs


def _shape(element: ET.Element, minimum: int) -> list[tuple[float, float]]:
    points = []
    for token in element.get("shape", "").split():
        values = [float(value) for value in token.split(",")]
        if len(values) not in (2, 3) or not all(math.isfinite(value) for value in values):
            raise ValueError(f"Invalid shape: {element.get('id')}")
        points.append((values[0], values[1]))
    if len(points) < minimum:
        raise ValueError(f"Incomplete shape: {element.get('id')}")
    return points


def _network(path: Path, image_crs: CRS) -> tuple[list[dict], dict]:
    root = ET.parse(path).getroot()
    if root.tag != "net":
        raise ValueError("Use a SUMO network file.")
    frame = read_network_coordinate_frame(path)
    if not all(math.isfinite(value) for value in (frame.net_offset_x, frame.net_offset_y)):
        raise ValueError("The network offset must be finite.")
    transformer = Transformer.from_crs(_metric_crs(frame.projection), image_crs, always_xy=True,
                                       allow_ballpark=False)

    def project(x, y, z=None):
        return transformer.transform(x, y, errcheck=True)

    def projected(geometry):
        return transform_geometry(project, translate(geometry, -frame.net_offset_x, -frame.net_offset_y))

    objects, seen = [], set()
    default_widths = 0
    invalid_surfaces, absent_surfaces, zero_length_lanes = [], [], []
    for edge in root.findall("edge"):
        category = edge.get("function", "normal")
        for lane in edge.findall("lane"):
            key = ("lane", lane.get("id"))
            if not key[1] or key in seen:
                raise ValueError("Lane identifiers must be present and unique.")
            seen.add(key)
            line = LineString(_shape(lane, 2))
            width = float(lane.get("width", "3.2"))
            if not math.isfinite(width) or width <= 0:
                raise ValueError(f"Invalid lane geometry: {key[1]}")
            if line.length == 0:
                zero_length_lanes.append(key[1])
                line = Point(line.coords[0])
            default_widths += "width" not in lane.attrib
            objects.append({"id": key[1], "kind": category,
                            "line": projected(line),
                            "surface": projected(line.buffer(width / 2) if line.geom_type == "Point" else
                                                 line.buffer(width / 2, cap_style=2, join_style=2))})
    for junction in root.findall("junction"):
        key = ("junction", junction.get("id"))
        if not key[1] or key in seen:
            raise ValueError("Junction identifiers must be present and unique.")
        seen.add(key)
        # Internal waiting points and road ends can have no surface polygon.
        if not junction.get("shape", "").strip():
            absent_surfaces.append(key[1])
            continue
        points = _shape(junction, 1)
        if len(set(points)) < 3:
            absent_surfaces.append(key[1])
            continue
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area == 0:
            invalid_surfaces.append(key[1])
            polygon = polygon.boundary
        objects.append({"id": key[1], "kind": "junction", "surface": projected(polygon)})
    if not objects:
        raise ValueError("The network contains no drawable geometry.")
    objects.sort(key=lambda row: (row["kind"], row["id"]))
    return objects, {"net_offset": [frame.net_offset_x, frame.net_offset_y],
                     "projection": frame.projection, "default_lane_width_m": 3.2,
                     "lanes_without_explicit_width": default_widths,
                     "invalid_junction_surfaces_drawn_as_outlines": sorted(invalid_surfaces),
                     "zero_geometric_length_lanes_drawn_as_points": sorted(zero_length_lanes),
                     "junctions_without_surface": sorted(absent_surfaces)}


_COLORS = {"normal": (0, 230, 255), "internal": (255, 65, 220),
           "crossing": (255, 255, 80), "walkingarea": (100, 255, 100),
           "connector": (0, 230, 255), "junction": (255, 160, 40)}


def _parts(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type in {"Polygon", "LineString", "Point"}:
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _parts(part)


def _draw(background: Image.Image, objects: list[dict], bounds: tuple, title: str) -> tuple[Image.Image, dict]:
    width, height = background.size
    extent = box(*bounds)
    image = background.copy().convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    visible = {"lanes": [], "junctions": []}

    def pixels(points):
        return [((x - bounds[0]) / (bounds[2] - bounds[0]) * width - 0.5,
                 (bounds[3] - y) / (bounds[3] - bounds[1]) * height - 0.5) for x, y in points]

    # Render surfaces first. Draw every lane centerline above the surfaces.
    for row in sorted(objects, key=lambda row: (row["kind"] != "junction", row["kind"], row["id"])):
        clipped = row["surface"].intersection(extent)
        if clipped.is_empty:
            continue
        visible["junctions" if row["kind"] == "junction" else "lanes"].append(row["id"])
        color = _COLORS[row["kind"]]
        for part in _parts(clipped):
            if part.geom_type == "Polygon":
                if part.interiors:
                    mask = Image.new("L", image.size)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.polygon(pixels(part.exterior.coords), fill=35)
                    for ring in part.interiors:
                        mask_draw.polygon(pixels(ring.coords), fill=0)
                    image.paste(color, (0, 0, width, height), mask)
                else:
                    draw.polygon(pixels(part.exterior.coords), fill=(*color, 35))
                draw.line(pixels(part.exterior.coords), fill=(*color, 180), width=1)
                for ring in part.interiors:
                    draw.line(pixels(ring.coords), fill=(*color, 180), width=1)
            elif part.geom_type == "LineString":
                draw.line(pixels(part.coords), fill=(*color, 255), width=2)
    for row in objects:
        if "line" not in row:
            continue
        color = _COLORS[row["kind"]]
        for part in _parts(row["line"].intersection(extent)):
            if part.geom_type == "Point":
                x, y = pixels(part.coords)[0]
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(*color, 255))
                continue
            if part.geom_type != "LineString":
                continue
            points = pixels(part.coords)
            draw.line(points, fill=(*color, 255), width=2)
            if part.length > 5:
                center = part.interpolate(0.55, normalized=True)
                ahead = part.interpolate(0.60, normalized=True)
                a, b = pixels([(center.x, center.y), (ahead.x, ahead.y)])
                angle = math.atan2(b[1] - a[1], b[0] - a[0])
                draw.line([(a[0] - 5 * math.cos(angle - 0.5), a[1] - 5 * math.sin(angle - 0.5)), a,
                           (a[0] - 5 * math.cos(angle + 0.5), a[1] - 5 * math.sin(angle + 0.5))],
                          fill=(*color, 255), width=2)
    panel = Image.new("RGB", (width, height + 54), "white")
    panel.paste(image, (0, 24))
    labels = ImageDraw.Draw(panel)
    font = ImageFont.load_default()
    labels.text((6, 5), title + " | N up | visual review only", fill="black", font=font)
    labels.text((6, height + 29), "Cyan: lanes | Pink: internal lanes | Orange: junctions | Yellow/green: walk",
                fill="black", font=font)
    return panel, {key: sorted(value) for key, value in visible.items()}


def render_network_aerial_review(*, request_file: str | Path, output_dir: str | Path) -> dict:
    request_path = Path(request_file).resolve(strict=True)
    request_bytes = request_path.read_bytes()
    request = _Request.model_validate_json(request_bytes)
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new review output directory.")
    crs = _metric_crs(request.aerial.crs)
    inputs = {"request": {"path": str(request_path), "sha256": hashlib.sha256(request_bytes).hexdigest()}}
    if pyproj.network.is_network_enabled():
        raise ValueError("Disable PROJ network access before rendering frozen inputs.")
    for role in ("candidate_net", "source_net", "aerial"):
        artifact = getattr(request, role)
        if artifact is None:
            continue
        path = (request_path.parent / artifact.path).resolve(strict=True)
        if file_sha256(path) != artifact.sha256:
            raise ValueError(f"The {role} hash does not match the request.")
        inputs[role] = {"path": str(path), "sha256": artifact.sha256}
    full_bounds = request.aerial.bbox
    view_ids = [view.id for view in request.views]
    if len(set(view_ids)) != len(view_ids):
        raise ValueError("View identifiers must be unique.")
    for view in request.views:
        if not box(*full_bounds).covers(box(*view.bbox)):
            raise ValueError("Each view must lie inside the declared aerial bounds.")
    with Image.open(io.BytesIO(Path(inputs["aerial"]["path"]).read_bytes())) as source_image:
        if source_image.getexif().get(274, 1) != 1:
            raise ValueError("Use a north-up image without EXIF rotation.")
        source_image.load()
        aerial = source_image.convert("RGB")
        aerial.info.clear()
    networks = {role: _network(Path(inputs[role]["path"]), crs)
                for role in ("source_net", "candidate_net") if role in inputs}
    if any(row["kind"] not in _COLORS for objects, _frame in networks.values() for row in objects):
        raise ValueError("The network contains an unsupported edge function.")
    dimensions = [(request.width_px, round(request.width_px * (view.bbox[3] - view.bbox[1]) /
                                         (view.bbox[2] - view.bbox[0]))) for view in request.views]
    if any(height < 64 or height > 4096 for _width, height in dimensions):
        raise ValueError("Each view height must be between 64 and 4096 pixels.")
    for view in request.views:
        for role, (objects, _frame) in networks.items():
            if not any("line" in row and row["line"].intersects(box(*view.bbox)) for row in objects):
                raise ValueError(f"The view {view.id} contains no lanes from {role}.")
    destination.mkdir(parents=True, exist_ok=False)
    views, artifacts = [], {}
    for view, size in zip(request.views, dimensions):
        x0, y0, x1, y1 = view.bbox
        w, h = aerial.size
        crop = ((x0 - full_bounds[0]) / (full_bounds[2] - full_bounds[0]) * w,
                (full_bounds[3] - y1) / (full_bounds[3] - full_bounds[1]) * h,
                (x1 - full_bounds[0]) / (full_bounds[2] - full_bounds[0]) * w,
                (full_bounds[3] - y0) / (full_bounds[3] - full_bounds[1]) * h)
        background = aerial.transform(size, Image.Transform.EXTENT, crop, resample=Image.Resampling.BICUBIC)
        panels, coverage = [], {}
        files = {"aerial": f"{view.id}-aerial.png"}
        background.save(destination / files["aerial"], format="PNG")
        for role, (objects, frame) in networks.items():
            title = "Source network" if role == "source_net" else "Candidate network"
            panel, visible = _draw(background, objects, view.bbox, title)
            if not visible["lanes"]:
                raise ValueError(f"The view {view.id} contains no lanes from {role}.")
            files[role] = f"{view.id}-{role}.png"
            panel.save(destination / files[role], format="PNG")
            panels.append(panel)
            coverage[role] = visible
        comparison = Image.new("RGB", (sum(panel.width for panel in panels), panels[0].height), "white")
        for index, panel in enumerate(panels):
            comparison.paste(panel, (index * panel.width, 0))
        files["comparison"] = f"{view.id}-comparison.png"
        comparison.save(destination / files["comparison"], format="PNG")
        for name in files.values():
            artifacts[name] = {"sha256": file_sha256(destination / name)}
        views.append({"id": view.id, "bbox": view.bbox, "map_size_px": size,
                      "metres_per_pixel": [(x1 - x0) / size[0], (y1 - y0) / size[1]],
                      "files": files, "visible_objects": coverage})
    unchanged = all(file_sha256(Path(item["path"])) == item["sha256"] for item in inputs.values())
    if not unchanged:
        raise ValueError("An input changed during rendering. Discard this review directory.")
    html = ("<!doctype html><meta charset='utf-8'><title>Network aerial review</title>"
            "<h1>Network aerial review</h1><p>Visual review only. Field accuracy and temporal alignment require review.</p>"
            f"<p>{escape(request.aerial.authority)} | {escape(request.aerial.license)} | "
            f"Capture: {escape(str(request.aerial.capture_date or request.aerial.capture_year or 'unknown'))}</p>" +
            "".join(f"<h2>{escape(view['id'])}</h2><a href='{view['files']['aerial']}'>Aerial without overlays</a>"
                    f"<p><img style='max-width:100%' alt='{escape(view['id'])} source and candidate' "
                    f"src='{view['files']['comparison']}'></p>" for view in views))
    write_text_atomic(destination / "review.html", html)
    artifacts["review.html"] = {"sha256": file_sha256(destination / "review.html")}
    report = {"schema": "astra.network-aerial-review/v1", "status": "review_required",
              "render_status": "pass", "promotion_authority": False, "inputs": inputs,
              "inputs_unchanged": unchanged, "crs": crs.to_string(),
              "aerial": request.aerial.model_dump(mode="json"),
              "target_date": str(request.target_date) if request.target_date else None,
              "temporal_alignment": "unknown" if not request.target_date or not request.aerial.capture_date else
                  "same_day" if request.target_date == request.aerial.capture_date else "different_dates",
              "frames": {role: frame for role, (_objects, frame) in networks.items()},
              "views": views, "artifacts": artifacts,
              "renderer": {"policy": "astra.network-aerial-render/v1", "python": platform.python_version(),
                           "implementation_sha256": file_sha256(Path(__file__)),
                           "pillow": PIL.__version__, "pyproj": pyproj.__version__,
                           "proj": pyproj.proj_version_str, "shapely": shapely.__version__},
              "claim_boundary": "Lane ribbons use declared widths or the 3.2 m SUMO default. Internal lanes show geometry, "
                  "not a complete connection audit. Images do not certify field alignment, road grouping, "
                  "signal logic, grade separation, or traffic performance."}
    write_json_atomic(destination / "report.json", report, sort_keys=True)
    return {**report, "report_file": str(destination / "report.json"),
            "report_sha256": file_sha256(destination / "report.json"),
            "html_file": str(destination / "review.html")}
