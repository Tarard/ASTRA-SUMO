"""Measure native contour reconstruction separately from the requested edit."""

from copy import deepcopy
from collections.abc import Mapping
import math
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET

from shapely.geometry import LineString, Point, Polygon

from .candidate_contracts import file_sha256, validate_materialization_evidence
from .artifact_io import write_json_atomic
from .source_movement_support import _index, _internal


# Use the same endpoint tolerance as the existing connection-structure audit.
IDENTITY_TOLERANCE_M = 0.1


def build_identity_control(ledger, *, output_dir, netconvert_binary, command_runner):
    from .corridor_edit_ledger import build_corridor_edit_ledger, materialize_corridor_edit_variant

    source = Path(ledger["source_net_file"]).resolve(strict=True)
    operation = deepcopy(ledger["operations"][0])
    node_id = operation["params"]["node_id"]
    node = {row.get("id"): row for row in ET.parse(source).getroot().findall("junction")}[node_id]
    operation.update(id=operation["id"] + "-identity-control",
        params={"node_id": node_id, "shape": [list(map(float, point.split(",")[:2])) for point in node.get("shape", "").split()]},
        rationale="Native source-contour control. This diagnostic has no promotion authority.")
    folder = Path(tempfile.mkdtemp(prefix="contour-identity-", dir=output_dir))
    try:
        built = build_corridor_edit_ledger(net_file=source, output_dir=folder / "ledger", operations=[operation], include_auto_proposals=False)
        return materialize_corridor_edit_variant(ledger_file=Path(built["ledger_file"]), output_dir=folder / "candidate",
            netconvert_binary=netconvert_binary, command_runner=command_runner)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, ET.ParseError) as error:
        record = {"status": "fail", "source_net_file": str(source), "output_dir": str(folder),
                  "error": f"{type(error).__name__}: {error}", "promotion_authority": False,
                  "report_file": str(folder / "control-failure.json")}
        try:
            write_json_atomic(Path(record["report_file"]), record)
        except OSError as write_error:
            record["record_write_error"] = str(write_error)
        return record


def geometry_delta(before, after, node_id):
    def lanes(root):
        index = _index(root)
        incident = {key for key, edge in index["edges"].items() if node_id in {edge.get("from"), edge.get("to")}}
        return {key: row[1] for key, row in index["lanes"].items()
                if row[0] in incident or index["owners"].get(key) == node_id}

    old, new = lanes(before), lanes(after)
    rows, unavailable = [], []
    for identity in sorted(old.keys() & new.keys()):
        try:
            lengths = [float(lane.get("length")) for lane in (old[identity], new[identity])]
        except (ValueError, TypeError):
            unavailable.append(identity)
            continue
        points = [[tuple(map(float, point.split(","))) for point in lane.get("shape", "").split()]
                  for lane in (old[identity], new[identity])]
        if (not all(math.isfinite(value) for value in lengths)
                or any(not values or any(len(p) not in {2, 3} or not all(math.isfinite(x) for x in p) for p in values) for values in points)):
            unavailable.append(identity)
            continue
        xy = [[point[:2] for point in values] for values in points]
        shapes = [LineString(values) if len(values) > 1 else Point(values[0]) for values in xy]
        spatial = [[(*point, 0.0) if len(point) == 2 else point for point in values] for values in points]
        row = {"lane_id": identity,
            "endpoint_shift_xy_m": max(math.dist(xy[0][i], xy[1][i]) for i in (0, -1)),
            "endpoint_shift_xyz_m": max(math.dist(spatial[0][i], spatial[1][i]) for i in (0, -1)),
            "shape_hausdorff_xy_m": shapes[0].hausdorff_distance(shapes[1]),
            "declared_length_delta_m": lengths[1] - lengths[0]}
        if any(value != 0 for key, value in row.items() if key != "lane_id"):
            rows.append(row)
    return {"scope": "incident roads and all lanes owned by the junction", "compared_lane_count": len(old.keys() & new.keys()) - len(unavailable),
        "missing_lane_ids": sorted(old.keys() - new.keys()), "added_lane_ids": sorted(new.keys() - old.keys()),
        "unmeasured_lane_ids": unavailable, "changes": rows,
        "max_endpoint_shift_xy_m": max((row["endpoint_shift_xy_m"] for row in rows), default=0.0),
        "max_endpoint_shift_xyz_m": max((row["endpoint_shift_xyz_m"] for row in rows), default=0.0),
        "max_shape_hausdorff_xy_m": max((row["shape_hausdorff_xy_m"] for row in rows), default=0.0),
        "max_absolute_declared_length_delta_m": max((abs(row["declared_length_delta_m"]) for row in rows), default=0.0)}


def walking_area_quality(before, after, node_id):
    def areas(root):
        index = _index(root)
        return {lane.get("id"): Polygon([tuple(map(float, point.split(",")[:2])) for point in lane.get("shape").split()])
                for edge in root.findall("edge") if edge.get("function") == "walkingarea" for lane in edge.findall("lane")
                if index["owners"].get(lane.get("id")) == node_id and len(lane.get("shape", "").split()) >= 3}
    old, new = areas(before), areas(after)
    return {"compared_area_count": len(old.keys() & new.keys()),
            "newly_invalid_area_ids": sorted(key for key in old.keys() & new.keys() if old[key].is_valid and not new[key].is_valid)}


def assess_requested_geometry(control, candidate, node_id):
    nodes = [{row.get("id"): row for row in root.findall("junction")}[node_id] for root in (control, candidate)]
    polygons = [Polygon([tuple(map(float, token.split(",")[:2])) for token in row.get("shape", "").split()]) for row in nodes]
    displacement = polygons[0].hausdorff_distance(polygons[1])
    endpoint_budget = displacement + IDENTITY_TOLERANCE_M
    delta = geometry_delta(control, candidate, node_id)
    boundaries, grade, lane_edges, vertical = [], {}, {}, []
    unknown_inputs = set(delta["unmeasured_lane_ids"])
    for root, polygon in zip((control, candidate), polygons):
        ends = {}
        for edge in root.findall("edge"):
            if _internal(edge) or node_id not in {edge.get("from"), edge.get("to")}:
                continue
            edge_id = edge.get("id")
            grade.setdefault(edge_id, 1.0)
            for lane in edge.findall("lane"):
                identity = lane.get("id")
                lane_edges[identity] = edge_id
                if identity in unknown_inputs:
                    continue
                points = [tuple(map(float, point.split(","))) for point in lane.get("shape", "").split()]
                spatial = [(*point, 0.0) if len(point) == 2 else point for point in points]
                for a, b in zip(spatial, spatial[1:]):
                    xy, xyz = math.dist(a[:2], b[:2]), math.dist(a, b)
                    if xy > 1e-12:
                        grade[edge_id] = max(grade[edge_id], xyz / xy)
                    elif xyz > IDENTITY_TOLERANCE_M:
                        vertical.append(identity)
                if not points:
                    continue
                for endpoint, number in (("from", 0), ("to", -1)):
                    if edge.get(endpoint) != node_id:
                        continue
                    offset = float(lane.get("endOffset", edge.get("endOffset", "0"))) if endpoint == "to" else 0.0
                    if not math.isfinite(offset) or offset < 0:
                        unknown_inputs.add(identity)
                        continue
                    distance = Point(points[number][:2]).distance(polygon.boundary)
                    ends[(identity, endpoint)] = {"distance_m": distance, "excess_m": max(0.0, distance - offset)}
        boundaries.append(ends)
    length_budgets = {key: 2 * displacement * factor + IDENTITY_TOLERANCE_M for key, factor in grade.items()}
    oversized = [{**row, "logical_length_budget_m": length_budgets[lane_edges[row["lane_id"]]]}
                 for row in delta["changes"] if row["lane_id"] in lane_edges and (
                     row["endpoint_shift_xy_m"] > endpoint_budget or
                     abs(row["declared_length_delta_m"]) > length_budgets[lane_edges[row["lane_id"]]])]
    boundary_failures = [{"lane_id": key[0], "endpoint": key[1], "control": boundaries[0][key], "candidate": value}
                         for key, value in boundaries[1].items() if key in boundaries[0] and
                         value["excess_m"] > boundaries[0][key]["excess_m"] + IDENTITY_TOLERANCE_M]
    unknown = delta["missing_lane_ids"] or delta["added_lane_ids"] or unknown_inputs or vertical
    reason = ("requested_contour_geometry_unmeasured" if unknown else
              "requested_contour_lane_boundary_mismatch" if boundary_failures else
              "requested_contour_effect_exceeds_displacement" if oversized else "requested_contour_geometry_within_scope")
    return {"status": "review_required" if unknown or oversized or boundary_failures else "pass", "reason": reason,
        "geometry": delta, "contour_displacement_xy_m": displacement, "endpoint_budget_xy_m": endpoint_budget,
        "logical_length_budgets_m": length_budgets, "length_budget_basis": "two moving ends with observed spatial grade",
        "oversized_lane_changes": oversized, "boundary_failures": boundary_failures,
        "near_vertical_lane_ids": sorted(set(vertical)), "unmeasured_input_lane_ids": sorted(unknown_inputs)}


def assess_identity_control(source, before, node_id, report):
    result = {"status": "review_required", "reason": "contour_identity_control_missing",
              "identity_tolerance_m": IDENTITY_TOLERANCE_M, "promotion_authority": False}
    if not report:
        return result
    if not isinstance(report, Mapping):
        return {**result, "reason": "contour_identity_control_unavailable", "error": "Control evidence must be a materialization report."}
    try:
        if report.get("error"):
            result["build_error"] = report["error"]
        if report.get("report_file"):
            path = Path(report["report_file"]).resolve()
            result["materialization_report"] = {"path": str(path), "sha256": None}
            if not report.get("candidate_net_file") and path.is_file():
                result["materialization_report"]["sha256"] = file_sha256(path)
        if not report.get("candidate_net_file"):
            return {**result, "reason": "contour_identity_control_unavailable"}
        control = Path(report["candidate_net_file"]).resolve(strict=True)
        digest = file_sha256(control)
        result["candidate"] = {"path": str(control), "sha256": digest}
        after = ET.parse(control).getroot()
        result["geometry"] = geometry_delta(before, after, node_id)
        result["walking_area_quality"] = walking_area_quality(before, after, node_id)
        if result.get("materialization_report"):
            result["materialization_report"]["sha256"] = file_sha256(Path(result["materialization_report"]["path"]))
        proof = validate_materialization_evidence(report, source_net_file=source, candidate_net_file=control)
        result["materialization_evidence"] = {key: proof[key] for key in ("status", "errors")}
        old_node = {row.get("id"): row for row in before.findall("junction")}[node_id]
        new_node = {row.get("id"): row for row in after.findall("junction")}[node_id]
        polygons = [Polygon([tuple(map(float, point.split(",")[:2])) for point in row.get("shape", "").split()])
                    for row in (old_node, new_node)]
        result["same_source_polygon"] = polygons[0].equals(polygons[1])
        result["custom_shape_declared"] = new_node.get("customShape", "false").lower() in {"true", "1"}
        from .junction_boundary_rebuild import _coordinate_frame
        result["coordinate_frame_preserved"] = _coordinate_frame(before) == _coordinate_frame(after)
        if (proof["status"] != "pass" or report.get("materialization_method") != "netconvert"
                or not result["same_source_polygon"] or not result["custom_shape_declared"]
                or not result["coordinate_frame_preserved"] or file_sha256(control) != digest):
            return {**result, "reason": "contour_identity_control_unverified"}
        delta = result["geometry"]
        if delta["unmeasured_lane_ids"]:
            return {**result, "reason": "contour_identity_geometry_unmeasured"}
        if delta["missing_lane_ids"] or delta["added_lane_ids"]:
            return {**result, "reason": "contour_identity_correspondence_unresolved"}
        unstable = any(delta[key] > IDENTITY_TOLERANCE_M for key in (
            "max_endpoint_shift_xy_m", "max_endpoint_shift_xyz_m", "max_shape_hausdorff_xy_m", "max_absolute_declared_length_delta_m"))
        if not unstable and result["walking_area_quality"]["newly_invalid_area_ids"]:
            return {**result, "reason": "source_contour_worsens_walking_areas"}
        return {**result, "status": "review_required" if unstable else "pass",
                "reason": "custom_contour_recuts_source_geometry" if unstable else "source_contour_reconstruction_stable"}
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError) as error:
        return {**result, "reason": "contour_identity_control_unavailable", "error": f"{type(error).__name__}: {error}"}
