"""Draw the motion of one or two SUMO runs side by side from their FCD output.

Every vehicle, bicycle and pedestrian position and every signal state in a frame is read from the output files of
the named runs; nothing is interpolated. The picture shows those runs. It does not show field traffic, and a
smooth picture does not prove topology, demand or control.
"""

from __future__ import annotations

import gzip
import math
from pathlib import Path
from typing import Any, Literal
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256

SCHEMA = "astra.motion-review/v1"
CLAIM_BOUNDARY = ("Positions and signal states are read from the SUMO outputs named in inputs. The frames show these "
                  "runs, not field traffic; they do not prove topology, demand, control or field correctness.")
_SUPERSAMPLE = 2
_MAX_FRAMES = 600
_HEADER_PX = 46
_GROUND = (238, 238, 228)
_BUILDING = (226, 202, 172)
_LANE_COLOURS = {"walkingarea": (200, 200, 194), "sidewalk": (178, 178, 172), "internal": (88, 88, 92), "other": (124, 124, 124),
                 "road": (70, 70, 74), "bike": (176, 96, 84), "crossing": (236, 236, 230)}
_OBJECT_COLOURS = {"car_moving": (30, 90, 210), "car_stopped": (190, 30, 170), "bicycle": (20, 150, 60),
                   "pedestrian": (240, 130, 20)}
_SIGNAL_COLOURS = {"green": (40, 190, 70), "yellow": (240, 200, 40), "red": (220, 40, 40)}
LEGEND = "blue: moving vehicle | magenta: stopped vehicle | green: bicycle | orange: pedestrian | dots: signal state"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class _Artifact(_Model):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Panel(_Model):
    title: str = Field(min_length=1, max_length=80)
    net: _Artifact
    fcd: _Artifact
    tls_states: _Artifact | None = None
    summary: _Artifact | None = None


class _Request(_Model):
    schema_id: Literal["astra.motion-review-request/v1"] = Field(alias="schema")
    panels: list[_Panel] = Field(min_length=1, max_length=2)
    bbox: tuple[float, float, float, float]
    begin_s: int = Field(ge=0, strict=True)
    end_s: int = Field(ge=0, strict=True)
    buildings: _Artifact | None = None
    pixels_per_metre: float = Field(default=1.5, gt=0.2, le=8.0)
    frame_ms: int = Field(default=125, ge=20, le=2000, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.bbox[0] >= self.bbox[2] or self.bbox[1] >= self.bbox[3]:
            raise ValueError("Use increasing x and y bounds in network coordinates.")
        if self.end_s <= self.begin_s or self.end_s - self.begin_s >= _MAX_FRAMES:
            raise ValueError(f"Use an interval of 1 to {_MAX_FRAMES - 1} seconds.")
        width = (self.bbox[2] - self.bbox[0]) * self.pixels_per_metre
        height = (self.bbox[3] - self.bbox[1]) * self.pixels_per_metre
        if not (64 <= width <= 1600 and 64 <= height <= 1600):
            raise ValueError("Each panel must be 64 to 1600 pixels wide and high.")
        return self


def build_motion_review_request_schema() -> dict:
    return _Request.model_json_schema()


def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def _points(text: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y, *_ in (point.split(",") for point in text.split())]


def _lane_kind(function: str, lane: ET.Element) -> str:
    if function in {"crossing", "walkingarea", "internal"}:
        return function
    allow, disallow = lane.get("allow"), lane.get("disallow")
    if allow is not None:
        classes = set(allow.split())
        if classes == {"pedestrian"}:
            return "sidewalk"
        if classes <= {"bicycle", "pedestrian"}:
            return "bike"
        return "road" if "passenger" in classes else "other"
    return "other" if disallow is not None and ("passenger" in disallow.split() or disallow == "all") else "road"


def _read_net(path: Path, bbox: tuple[float, float, float, float], margin: float = 30.0) -> dict[str, Any]:
    """Lanes that touch the view, the stop-line end of every signal-controlled lane in it, and the net offset."""
    x0, y0, x1, y1 = bbox
    lanes: list[dict[str, Any]] = []
    lane_end: dict[str, tuple[float, float]] = {}
    heads: dict[str, list[tuple[int, float, float]]] = {}
    offset = None
    with path.open("rb") as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag == "location":
                offset = element.get("netOffset")
            elif element.tag == "edge":
                function = element.get("function", "normal")
                for lane in element.findall("lane"):
                    shape = _points(lane.get("shape", ""))
                    if not shape:
                        continue
                    if function == "normal":
                        lane_end[lane.get("id")] = shape[-1]
                    if any(x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin for x, y in shape):
                        lanes.append({"kind": _lane_kind(function, lane), "width": float(lane.get("width", "3.2")),
                                      "shape": shape})
                element.clear()
            elif element.tag == "connection":
                end = lane_end.get(f"{element.get('from')}_{element.get('fromLane')}") if element.get("tl") else None
                if end and x0 <= end[0] <= x1 and y0 <= end[1] <= y1:
                    heads.setdefault(element.get("tl"), []).append((int(element.get("linkIndex")), *end))
                element.clear()
            elif element.tag in {"junction", "tlLogic"}:
                element.clear()
    if offset is None:
        raise ValueError(f"The network has no location element: {path}")
    return {"lanes": lanes, "heads": heads, "net_offset": tuple(float(value) for value in offset.split(","))}


def _read_buildings(path: Path, bbox: tuple[float, float, float, float]) -> list[list[tuple[float, float]]]:
    x0, y0, x1, y1 = bbox
    polygons = []
    with _open(path) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag == "poly":
                shape = _points(element.get("shape", ""))
                if len(shape) > 2 and any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in shape):
                    polygons.append(shape)
                element.clear()
    return polygons


def _read_fcd(path: Path, bbox, begin: int, end: int) -> dict[int, list[tuple[str, float, float, float, float]]]:
    """Objects inside the view per whole second: kind, x, y, heading and speed."""
    x0, y0, x1, y1 = bbox
    frames: dict[int, list[tuple[str, float, float, float, float]]] = {}
    with _open(path) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "timestep":
                continue
            time = float(element.get("time"))
            if time > end:
                break
            if time >= begin and time == int(time):
                rows = []
                for item in element:
                    x, y = float(item.get("x")), float(item.get("y"))
                    if x0 <= x <= x1 and y0 <= y <= y1:
                        kind = "pedestrian" if item.tag == "person" else "bicycle" if "bicycle" in item.get("type", "") else "car"
                        rows.append((kind, x, y, float(item.get("angle", "0")), float(item.get("speed", "0"))))
                frames[int(time)] = rows
            element.clear()
    return frames


def _read_signal_states(path: Path, wanted: set[str], begin: int, end: int) -> dict[int, dict[str, str]]:
    states: dict[int, dict[str, str]] = {}
    current: dict[str, str] = {}
    with _open(path) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "tlsState":
                continue
            time = float(element.get("time"))
            if time > end:
                break
            if element.get("id") in wanted:
                current[element.get("id")] = element.get("state", "")
            if time >= begin and time == int(time):
                states[int(time)] = dict(current)
            element.clear()
    return states


def _read_summary(path: Path, begin: int, end: int) -> dict[int, dict[str, str]]:
    rows = {}
    with _open(path) as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag == "step":
                time = float(element.get("time"))
                if begin <= time <= end and time == int(time):
                    rows[int(time)] = {key: element.get(key) for key in ("running", "halting", "arrived", "collisions", "teleports")
                                       if element.get(key) is not None}
                element.clear()
    return rows


class _View:
    def __init__(self, bbox: tuple[float, float, float, float], pixels_per_metre: float):
        self.bbox, self.scale = bbox, pixels_per_metre * _SUPERSAMPLE
        self.size = (round((bbox[2] - bbox[0]) * pixels_per_metre), round((bbox[3] - bbox[1]) * pixels_per_metre))

    def px(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.bbox[0]) * self.scale, (self.bbox[3] - y) * self.scale)


def _background(view: _View, net: dict[str, Any], buildings: list[list[tuple[float, float]]]) -> Image.Image:
    image = Image.new("RGB", (view.size[0] * _SUPERSAMPLE, view.size[1] * _SUPERSAMPLE), _GROUND)
    draw = ImageDraw.Draw(image)
    for shape in buildings:
        draw.polygon([view.px(*point) for point in shape], fill=_BUILDING)
    for kind in _LANE_COLOURS:
        for lane in net["lanes"]:
            if lane["kind"] != kind:
                continue
            points = [view.px(*point) for point in lane["shape"]]
            if kind == "walkingarea" and len(points) > 2:
                draw.polygon(points, fill=_LANE_COLOURS[kind])
            elif len(points) > 1:
                draw.line(points, fill=_LANE_COLOURS[kind], width=max(1, round(lane["width"] * view.scale)), joint="curve")
    return image


def _frame(view: _View, background: Image.Image, heads, rows, state: dict[str, str]) -> Image.Image:
    image = background.copy()
    draw = ImageDraw.Draw(image)
    radius = 1.6 * _SUPERSAMPLE
    # In an overview a car is smaller than a pixel: draw every vehicle as a dot of fixed size and leave signals out.
    overview = 4.6 * view.scale < 5 * _SUPERSAMPLE
    for tl, lights in ({} if overview else heads).items():
        text = state.get(tl, "")
        for link, x, y in lights:
            letter = text[link] if link < len(text) else "r"
            colour = _SIGNAL_COLOURS["green" if letter in "Gg" else "yellow" if letter in "yYu" else "red"]
            cx, cy = view.px(x, y)
            draw.ellipse([cx - 2 * radius, cy - 2 * radius, cx + 2 * radius, cy + 2 * radius], fill=colour)
    for kind, x, y, angle, speed in rows:
        if kind == "pedestrian":
            cx, cy = view.px(x, y)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=_OBJECT_COLOURS[kind])
            continue
        length, width = (1.8, 0.9) if kind == "bicycle" else (4.6, 2.0)
        colour = _OBJECT_COLOURS["bicycle" if kind == "bicycle" else "car_moving" if speed > 0.1 else "car_stopped"]
        if overview:
            cx, cy = view.px(x, y)
            dot = (1.2 if kind == "bicycle" else 2.0 if speed > 0.1 else 2.6) * _SUPERSAMPLE
            draw.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=colour)
            continue
        # SUMO reports the front bumper; the heading is clockwise from north.
        heading = math.radians(90.0 - angle)
        dx, dy = math.cos(heading), math.sin(heading)
        draw.polygon([view.px(x + back * length * dx - side * width / 2 * dy, y + back * length * dy + side * width / 2 * dx)
                      for back, side in ((0, 1), (0, -1), (-1, -1), (-1, 1))], fill=colour)
    return image.resize(view.size, Image.Resampling.LANCZOS)


def render_motion_review(*, request_file: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Write an animated GIF, its middle frame and a manifest with the hashes of every input and output."""
    request_path = Path(request_file).resolve(strict=True)
    request = _Request.model_validate_json(request_path.read_bytes())
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ValueError("Choose a new review output directory.")
    inputs: dict[str, dict[str, str]] = {"request": {"path": str(request_path), "sha256": file_sha256(request_path)}}

    def bound(name: str, artifact: _Artifact) -> Path:
        path = (request_path.parent / artifact.path).resolve(strict=True)
        if file_sha256(path) != artifact.sha256:
            raise ValueError(f"The {name} hash does not match the request.")
        inputs[name] = {"path": str(path), "sha256": artifact.sha256}
        return path

    view = _View(request.bbox, request.pixels_per_metre)
    buildings = _read_buildings(bound("buildings", request.buildings), request.bbox) if request.buildings else []
    font = ImageFont.load_default()
    panels, report_panels, offsets = [], [], []
    for index, panel in enumerate(request.panels):
        net = _read_net(bound(f"panel{index}.net", panel.net), request.bbox)
        if not net["lanes"]:
            raise ValueError(f"Panel {index} has no lane in the view.")
        offsets.append(net["net_offset"])
        frames = _read_fcd(bound(f"panel{index}.fcd", panel.fcd), request.bbox, request.begin_s, request.end_s)
        missing = [second for second in range(request.begin_s, request.end_s + 1) if second not in frames]
        if missing:
            raise ValueError(f"Panel {index} has no FCD time step for second {missing[0]}.")
        states = (_read_signal_states(bound(f"panel{index}.tls_states", panel.tls_states), set(net["heads"]),
                                      request.begin_s, request.end_s) if panel.tls_states else {})
        summary = (_read_summary(bound(f"panel{index}.summary", panel.summary), request.begin_s, request.end_s)
                   if panel.summary else {})
        counts = {kind: [sum(row[0] == kind for row in frames[second]) for second in range(request.begin_s, request.end_s + 1)]
                  for kind in ("car", "bicycle", "pedestrian")}
        stopped = [sum(row[0] == "car" and row[4] <= 0.1 for row in frames[second])
                   for second in range(request.begin_s, request.end_s + 1)]
        panels.append((panel.title, _background(view, net, buildings), net["heads"] if panel.tls_states else {}, frames, states, summary))
        report_panels.append({"title": panel.title, "signals_in_view": len(net["heads"]) if panel.tls_states else 0,
                              "objects_in_view": {kind: {"min": min(values), "max": max(values)} for kind, values in counts.items()},
                              "stopped_cars_in_view": {"first_frame": stopped[0], "last_frame": stopped[-1], "max": max(stopped)}})
    if any(math.dist(offsets[0], offset) > 0.05 for offset in offsets):
        raise ValueError("The panels use different network offsets; one view cannot cover them.")

    gap = 6
    size = (view.size[0] * len(panels) + gap * (len(panels) - 1), view.size[1] + _HEADER_PX)
    images = []
    for second in range(request.begin_s, request.end_s + 1):
        canvas = Image.new("RGB", size, "white")
        labels = ImageDraw.Draw(canvas)
        for index, (title, background, heads, frames, states, summary) in enumerate(panels):
            left = index * (view.size[0] + gap)
            canvas.paste(_frame(view, background, heads, frames[second], states.get(second, {})), (left, _HEADER_PX))
            labels.text((left + 6, 3), title, fill="black", font=font)
            row = summary.get(second)
            network = "" if row is None else "   whole network: " + ", ".join(f"{key} {value}" for key, value in row.items())
            labels.text((left + 6, 17), f"t = {second} s{network}", fill=(60, 60, 60), font=font)
        labels.text((6, 31), LEGEND, fill=(60, 60, 60), font=font)
        images.append(canvas)
    if not all(file_sha256(Path(item["path"])) == item["sha256"] for item in inputs.values()):
        raise ValueError("An input changed during rendering. Discard this review directory.")
    destination.mkdir(parents=True, exist_ok=False)
    gif, still = destination / "motion.gif", destination / f"frame-{(request.begin_s + request.end_s) // 2}.png"
    palette = [image.quantize(colors=64, method=Image.Quantize.MEDIANCUT) for image in images]
    palette[0].save(gif, save_all=True, append_images=palette[1:], duration=request.frame_ms, loop=0, optimize=True)
    images[len(images) // 2].save(still, format="PNG")
    report = {"schema": SCHEMA, "status": "measured", "claim_boundary": CLAIM_BOUNDARY, "inputs": inputs,
              "view": {"bbox": list(request.bbox), "pixels_per_metre": request.pixels_per_metre, "panel_size_px": list(view.size),
                       "begin_s": request.begin_s, "end_s": request.end_s, "frame_count": len(images), "frame_ms": request.frame_ms},
              "panels": report_panels,
              "artifacts": {path.name: {"sha256": file_sha256(path), "bytes": path.stat().st_size} for path in (gif, still)},
              "output_dir": str(destination)}
    write_json_atomic(destination / "motion-review.json", report)
    return report
