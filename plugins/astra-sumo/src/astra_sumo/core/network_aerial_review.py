"""Draw saved SUMO geometry over bounded imagery or a registered screenshot."""

from __future__ import annotations

from datetime import date
import hashlib
from html import escape
import io
import json
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
from shapely.geometry import LineString, Point, Polygon, box, shape as geometry_shape
from shapely.ops import transform as transform_geometry

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .reference_spatial_registration import read_network_coordinate_frame
from .network_structure_review import review_network_structures


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


class _ControlPoint(_Model):
    id: str = Field(min_length=1)
    pixel: tuple[float, float]
    lon_lat: tuple[float, float]
    role: Literal["fit", "check"] = "fit"

    @model_validator(mode="after")
    def geographic(self):
        if not (-180 <= self.lon_lat[0] <= 180 and -90 < self.lon_lat[1] < 90):
            raise ValueError("Use longitude and latitude in degrees.")
        return self


class _Aerial(_Artifact):
    bbox: tuple[float, float, float, float] | None = None
    control_points: list[_ControlPoint] | None = Field(default=None, min_length=3, max_length=24)
    crop_px: tuple[int, int, int, int] | None = None
    crs: str
    north_up: bool
    capture_date: date | None
    capture_year: int | None = Field(default=None, ge=1900, le=2200)
    authority: str = Field(min_length=1)
    license: str = Field(min_length=1)
    source_url: str = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_date(self):
        if (self.bbox is None) == (self.control_points is None):
            raise ValueError("Supply either image bounds or screenshot control points.")
        if self.bbox is not None:
            _Bounds(bbox=self.bbox)
            if not self.north_up or self.crop_px is not None:
                raise ValueError("Bounded images must be north-up and already cropped.")
        if self.capture_date and self.capture_year and self.capture_date.year != self.capture_year:
            raise ValueError("The aerial date and year disagree.")
        return self


class _View(_Bounds):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class _Request(_Model):
    schema_id: Literal["astra.network-aerial-review-request/v1"] = Field(alias="schema")
    candidate_net: _Artifact
    source_net: _Artifact | None = None
    parking_facilities: _Artifact | None = None
    aerial: _Aerial
    target_date: date | None
    views: list[_View] | None = Field(default=None, min_length=1, max_length=24)
    width_px: int = Field(default=1200, ge=128, le=4096, strict=True)
    source_label: str = Field(default="Before: source network", min_length=1, max_length=100)
    candidate_label: str = Field(default="After: candidate network", min_length=1, max_length=100)


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
    structures = review_network_structures(root)
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
                            "structure": structures["lane_structures"].get(key[1], "unspecified"),
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
        objects.append({"id": key[1], "kind": "junction", "surface": projected(polygon),
                        "structure": structures["junction_structures"].get(key[1], "unspecified")})
    if not objects:
        raise ValueError("The network contains no drawable geometry.")
    objects.sort(key=lambda row: (row["kind"], row["id"]))
    return objects, {"net_offset": [frame.net_offset_x, frame.net_offset_y],
                     "projection": frame.projection, "default_lane_width_m": 3.2,
                     "lanes_without_explicit_width": default_widths,
                     "invalid_junction_surfaces_drawn_as_outlines": sorted(invalid_surfaces),
                     "zero_geometric_length_lanes_drawn_as_points": sorted(zero_length_lanes),
                     "structure_review": structures,
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


def _draw(background: Image.Image, objects: list[dict], bounds: tuple, title: str,
          *, structure_detail: bool = False, facilities: list[dict] | None = None,
          attribution: str | None = None) -> tuple[Image.Image, dict]:
    width, height = background.size
    extent = box(*bounds)
    image = background.copy().convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    visible = {"lanes": [], "junctions": []}
    hide = {("junction" if name == "interior_junction_ids" else "lane", key)
            for facility in facilities or [] if facility["connection_trace_complete"] for name in (
                "interior_lane_ids", "internal_lane_ids", "interior_junction_ids") for key in facility[name]}

    def pixels(points):
        return [((x - bounds[0]) / (bounds[2] - bounds[0]) * width - 0.5,
                 (bounds[3] - y) / (bounds[3] - bounds[1]) * height - 0.5) for x, y in points]

    if not structure_detail:
        for facility in facilities or []:
            for polygon in _parts(facility["polygon"].intersection(extent)):
                if polygon.geom_type == "Polygon":
                    for ring in [polygon.exterior, *polygon.interiors]:
                        draw.line(pixels(ring.coords), fill=(75, 105, 220, 220), width=3)

    # Render surfaces first. Draw every lane centerline above the surfaces.
    for row in sorted(objects, key=lambda row: (row["kind"] != "junction", row["kind"], row["id"])):
        clipped = row["surface"].intersection(extent)
        if clipped.is_empty:
            continue
        visible["junctions" if row["kind"] == "junction" else "lanes"].append(row["id"])
        if ("junction" if row["kind"] == "junction" else "lane", row["id"]) in hide and not structure_detail:
            continue
        if row.get("structure", "unspecified") != "unspecified" and not structure_detail:
            continue
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
        if ("lane", row["id"]) in hide and not structure_detail:
            continue
        color = _COLORS[row["kind"]]
        tagged = row.get("structure", "unspecified") != "unspecified"
        if tagged and not structure_detail and row["kind"] == "internal":
            continue
        for part in _parts(row["line"].intersection(extent)):
            if part.geom_type == "Point":
                x, y = pixels(part.coords)[0]
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(100, 100, 100, 230) if tagged and not structure_detail else (*color, 255))
                continue
            if part.geom_type != "LineString":
                continue
            points = pixels(part.coords)
            if tagged and not structure_detail:
                # Equal map-distance dashes remain stable when a polyline has extra vertices.
                spacing = max((bounds[2] - bounds[0]) / width * 10, 0.01)
                for index in range(math.ceil(part.length / spacing)):
                    segment = shapely.ops.substring(part, index * spacing, min((index + 0.6) * spacing, part.length))
                    if segment.geom_type == "LineString":
                        draw.line(pixels(segment.coords), fill=(100, 100, 100, 230), width=2)
                continue
            draw.line(points, fill=(*color, 255), width=2)
            if part.length > 5:
                center = part.interpolate(0.55, normalized=True)
                ahead = part.interpolate(0.60, normalized=True)
                a, b = pixels([(center.x, center.y), (ahead.x, ahead.y)])
                angle = math.atan2(b[1] - a[1], b[0] - a[0])
                draw.line([(a[0] - 5 * math.cos(angle - 0.5), a[1] - 5 * math.sin(angle - 0.5)), a,
                           (a[0] - 5 * math.cos(angle + 0.5), a[1] - 5 * math.sin(angle + 0.5))],
                          fill=(*color, 255), width=2)
    if not structure_detail:
        for facility in facilities or []:
            clipped = facility["polygon"].intersection(extent)
            if clipped.is_empty:
                continue
            center = clipped.representative_point()
            x, y = pixels([(center.x, center.y)])[0]
            draw.rounded_rectangle((x - 16, y - 16, x + 16, y + 16), radius=5,
                                   fill=(35, 80, 185, 255), outline="white", width=2)
            draw.line([(x - 6, y + 10), (x - 6, y - 10), (x + 1, y - 10)], fill="white", width=3)
            draw.arc((x - 6, y - 10, x + 9, y + 4), 270, 90, fill="white", width=3)
            draw.line([(x - 6, y + 4), (x + 1, y + 4)], fill="white", width=3)
            for portal in facility["projected_portals"]:
                if not extent.covers(Point(portal["point"])):
                    continue
                px, py = pixels([portal["point"]])[0]
                a, b = pixels(portal["direction"])
                angle = math.atan2(b[1] - a[1], b[0] - a[0])
                triangle = [(px + 10 * math.cos(angle), py + 10 * math.sin(angle)),
                            (px + 8 * math.cos(angle + 2.5), py + 8 * math.sin(angle + 2.5)),
                            (px + 8 * math.cos(angle - 2.5), py + 8 * math.sin(angle - 2.5))]
                draw.polygon(triangle, fill=(20, 190, 90, 255) if portal["role"] == "entry" else (255, 150, 20, 255),
                             outline="white", width=1)
    panel = Image.new("RGB", (width, height + (106 if attribution else 86)), "white")
    panel.paste(image, (0, 24))
    labels = ImageDraw.Draw(panel)
    font = ImageFont.load_default()
    labels.text((6, 5), title + " | N up | visual review only", fill="black", font=font)
    labels.text((6, height + 29), "Cyan: lanes | Pink: internal lanes | Orange: junctions | Yellow/green: walk",
                fill="black", font=font)
    if facilities:
        labels.text((6, height + 61), "Blue P: parking | Green arrow: entry | Orange arrow: exit | All roads retained",
                    fill="black", font=font)
    labels.text((6, height + 45), "Full structure detail: saved geometry, field review required" if structure_detail else
                "Gray dashes: tagged structures / transitions. Full geometry is in the structure detail view.",
                fill="black", font=font)
    if attribution:
        labels.text((6, height + 81), attribution, fill="black", font=font)
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
    for role in ("candidate_net", "source_net", "aerial", "parking_facilities"):
        artifact = getattr(request, role)
        if artifact is None:
            continue
        path = (request_path.parent / artifact.path).resolve(strict=True)
        if file_sha256(path) != artifact.sha256:
            raise ValueError(f"The {role} hash does not match the request.")
        inputs[role] = {"path": str(path), "sha256": artifact.sha256}
    with Image.open(io.BytesIO(Path(inputs["aerial"]["path"]).read_bytes())) as source_image:
        if source_image.getexif().get(274, 1) != 1:
            raise ValueError("Use an image without EXIF rotation.")
        source_image.load()
        aerial = source_image.convert("RGB")
        aerial.info.clear()
    registration = None
    full_bounds = request.aerial.bbox
    if request.aerial.control_points is not None:
        from .image_registration import register_map_screenshot
        aerial, full_bounds, registration = register_map_screenshot(aerial, crs,
            [point.model_dump(mode="json") for point in request.aerial.control_points], request.aerial.crop_px)
    selected_views = request.views or [_View(id="junction", bbox=full_bounds)]
    view_ids = [view.id for view in selected_views]
    if len(set(view_ids)) != len(view_ids):
        raise ValueError("View identifiers must be unique.")
    for view in selected_views:
        if not box(*full_bounds).covers(box(*view.bbox)):
            raise ValueError("Each view must lie inside the declared aerial bounds.")
    networks = {role: _network(Path(inputs[role]["path"]), crs)
                for role in ("source_net", "candidate_net") if role in inputs}
    facilities = []
    if "parking_facilities" in inputs:
        parking = json.loads(Path(inputs["parking_facilities"]["path"]).read_bytes())
        if parking.get("schema") != "astra.parking-facilities/v1":
            raise ValueError("Use a parking facility report.")
        identities = {parking[role]["sha256"] for role in ("source", "network")}
        if any(inputs[role]["sha256"] not in identities for role in networks):
            raise ValueError("The parking facility report belongs to another network.")
        transform = Transformer.from_crs(4326, crs, always_xy=True, allow_ballpark=False)
        def project(x, y, z=None):
            return transform.transform(x, y, errcheck=True)

        for facility in parking["facilities"]:
            polygon = transform_geometry(project, geometry_shape(facility["footprint_lon_lat"]))
            if polygon.geom_type != "Polygon" or not polygon.is_valid or polygon.is_empty or not all(math.isfinite(v) for v in polygon.bounds):
                raise ValueError("The parking footprint is invalid.")
            portals = [{**p, "point": project(*p["point_lon_lat"]),
                        "direction": [project(*point) for point in p["direction_lon_lat"]]} for p in facility["portals"]]
            if any(p["role"] not in {"entry", "exit"} or not all(math.isfinite(v) for point in
                [p["point"], *p["direction"]] for v in point) for p in portals):
                raise ValueError("Parking portal directions must be finite and have a known role.")
            facilities.append({**facility, "polygon": polygon, "projected_portals": portals})
        for _objects, frame in networks.values():
            for turn in frame["structure_review"]["turnarounds_requiring_review"]:
                match = next((facility for facility in facilities if turn["from"] in facility["interior_edge_ids"]
                              and turn["to"] in facility["interior_edge_ids"]), None)
                if match:
                    turn.update(parking_facility_id=match["id"], review_context="parking_interior_geometry",
                                automatic_street_repair_candidate=False)
    if any(row["kind"] not in _COLORS for objects, _frame in networks.values() for row in objects):
        raise ValueError("The network contains an unsupported edge function.")
    dimensions = [(request.width_px, round(request.width_px * (view.bbox[3] - view.bbox[1]) /
                                         (view.bbox[2] - view.bbox[0]))) for view in selected_views]
    if any(height < 64 or height > 4096 for _width, height in dimensions):
        raise ValueError("Each view height must be between 64 and 4096 pixels.")
    for view in selected_views:
        for role, (objects, _frame) in networks.items():
            if not any("line" in row and row["line"].intersects(box(*view.bbox)) for row in objects):
                raise ValueError(f"The view {view.id} contains no lanes from {role}.")
    destination.mkdir(parents=True, exist_ok=False)
    views, artifacts = [], {}
    for view, size in zip(selected_views, dimensions):
        x0, y0, x1, y1 = view.bbox
        w, h = aerial.size
        crop = ((x0 - full_bounds[0]) / (full_bounds[2] - full_bounds[0]) * w,
                (full_bounds[3] - y1) / (full_bounds[3] - full_bounds[1]) * h,
                (x1 - full_bounds[0]) / (full_bounds[2] - full_bounds[0]) * w,
                (full_bounds[3] - y0) / (full_bounds[3] - full_bounds[1]) * h)
        background = aerial.transform(size, Image.Transform.EXTENT, crop, resample=Image.Resampling.BICUBIC)
        panels, detail_panels, coverage = [], [], {}
        files = {"aerial": f"{view.id}-aerial.png"}
        background.save(destination / files["aerial"], format="PNG")
        for role, (objects, frame) in networks.items():
            title = request.source_label if role == "source_net" else request.candidate_label
            attribution = request.aerial.authority + " | " + request.aerial.license
            panel, visible = _draw(background, objects, view.bbox, title, facilities=facilities, attribution=attribution)
            if not visible["lanes"]:
                raise ValueError(f"The view {view.id} contains no lanes from {role}.")
            files[role] = f"{view.id}-{role}.png"
            panel.save(destination / files[role], format="PNG")
            panels.append(panel)
            detail, _ = _draw(background, objects, view.bbox, title, structure_detail=True, facilities=facilities,
                              attribution=attribution)
            files[f"{role}_structure_detail"] = f"{view.id}-{role}-structures.png"
            detail.save(destination / files[f"{role}_structure_detail"], format="PNG")
            detail_panels.append(detail)
            coverage[role] = visible
        comparison = Image.new("RGB", (sum(panel.width for panel in panels), panels[0].height), "white")
        for index, panel in enumerate(panels):
            comparison.paste(panel, (index * panel.width, 0))
        files["comparison"] = f"{view.id}-comparison.png"
        comparison.save(destination / files["comparison"], format="PNG")
        for index, panel in enumerate(detail_panels):
            comparison.paste(panel, (index * panel.width, 0))
        files["structure_detail_comparison"] = f"{view.id}-structures-comparison.png"
        comparison.save(destination / files["structure_detail_comparison"], format="PNG")
        for name in files.values():
            artifacts[name] = {"sha256": file_sha256(destination / name)}
        views.append({"id": view.id, "bbox": view.bbox, "map_size_px": size,
                      "metres_per_pixel": [(x1 - x0) / size[0], (y1 - y0) / size[1]],
                      "files": files, "visible_objects": coverage})
    unchanged = all(file_sha256(Path(item["path"])) == item["sha256"] for item in inputs.values())
    if not unchanged:
        raise ValueError("An input changed during rendering. Discard this review directory.")
    turn_reviews = "".join(
        f"<h2>{escape(role.replace('_', ' '))}: structure turnarounds requiring review</h2>"
        "<p>Road annotations do not establish turning permission or available space.</p>"
        "<table><tr><th>Junction</th><th>From</th><th>To</th><th>Structure</th></tr>" +
        "".join(f"<tr><td>{escape(str(turn['junction_id']))}</td><td>{escape(turn['from'])}</td>"
                f"<td>{escape(turn['to'])}</td><td>{escape(turn['structure'])}</td></tr>"
                for turn in frame["structure_review"]["turnarounds_requiring_review"]) + "</table>"
        for role, (_objects, frame) in networks.items()
    )
    parking_html = ("<h2>Parking facilities</h2><ul>" + "".join(
        f"<li>{escape(facility['name'])}: "
        f"{sum(portal['role'] == 'entry' for portal in facility['portals'])} entries, "
        f"{sum(portal['role'] == 'exit' for portal in facility['portals'])} exits. "
        "Detailed driving paths are retained. Parking operations remain unconfigured.</li>"
        for facility in facilities) + "</ul>") if facilities else ""
    registration_html = ("<h2>Screenshot registration</h2><p>Independent geographic control points. "
        f"Fit RMSE: {registration['fit']['rmse_m']:.3f} m. "
        f"Check points excluded from fitting: {registration['check']['count']}. "
        f"Check RMSE: {escape(str(registration['check']['rmse_m']))} m. "
        "These residuals do not establish road or imagery accuracy.</p>") if registration else ""
    html = ("<!doctype html><meta charset='utf-8'><title>Network aerial review</title>"
            "<h1>Network aerial review</h1><p>Visual review only. Field accuracy and temporal alignment require review.</p>"
            f"<p>{escape(request.aerial.authority)} | {escape(request.aerial.license)} | "
            f"Capture: {escape(str(request.aerial.capture_date or request.aerial.capture_year or 'unknown'))}</p>" + registration_html +
            "".join(f"<h2>{escape(view['id'])}</h2><a href='{view['files']['aerial']}'>Aerial without overlays</a>"
                    f"<p><img style='max-width:100%' alt='{escape(view['id'])} source and candidate' "
                    f"src='{view['files']['comparison']}'></p>"
                    f"<details><summary>Tagged structures: full geometry and turn curves</summary>"
                    f"<img style='max-width:100%' alt='Complete structure geometry' "
                    f"src='{view['files']['structure_detail_comparison']}'></details>" for view in views) + parking_html + turn_reviews)
    write_text_atomic(destination / "review.html", html)
    artifacts["review.html"] = {"sha256": file_sha256(destination / "review.html")}
    report = {"schema": "astra.network-aerial-review/v1", "status": "review_required",
              "render_status": "pass", "promotion_authority": False, "inputs": inputs,
              "inputs_unchanged": unchanged, "crs": crs.to_string(),
              "aerial": request.aerial.model_dump(mode="json"),
              "image_registration": registration,
              "labels": {"source_net": request.source_label, "candidate_net": request.candidate_label},
              "target_date": str(request.target_date) if request.target_date else None,
              "temporal_alignment": "unknown" if not request.target_date or not request.aerial.capture_date else
                  "same_day" if request.target_date == request.aerial.capture_date else "different_dates",
              "frames": {role: frame for role, (_objects, frame) in networks.items()},
              "views": views, "artifacts": artifacts,
              "parking_facility_ids": [facility["id"] for facility in facilities],
              "renderer": {"policy": "astra.network-aerial-render/v4", "python": platform.python_version(),
                           "implementation_sha256": file_sha256(Path(__file__)),
                           "image_registration_sha256": file_sha256(Path(__file__).with_name("image_registration.py")),
                           "structure_review_sha256": file_sha256(Path(__file__).with_name("network_structure_review.py")),
                           "pillow": PIL.__version__, "pyproj": pyproj.__version__,
                           "proj": pyproj.proj_version_str, "shapely": shapely.__version__},
              "claim_boundary": "Lane ribbons use declared widths or the 3.2 m SUMO default. Internal lanes show geometry, "
                  "not a complete connection audit. Images do not certify field alignment, road grouping, "
                  "signal logic, grade separation, or traffic performance."}
    write_json_atomic(destination / "report.json", report, sort_keys=True)
    return {**report, "report_file": str(destination / "report.json"),
            "report_sha256": file_sha256(destination / "report.json"),
            "html_file": str(destination / "review.html")}
