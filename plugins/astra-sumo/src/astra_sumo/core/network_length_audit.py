"""Measure declared lane lengths without changing a network or release decision."""

import math
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic
from .candidate_contracts import file_sha256
from .junction_rebuild.geometry import _polyline_length


def _number(value, *, positive=False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and (number > 0 if positive else number >= 0) else None


def measure_lane_lengths(root):
    """Retain normal-road consistency, custom lengths, offsets, and geometry measures."""
    normal, internal_variation, invalid = [], [], []
    for edge in root.findall("edge"):
        rows = []
        for lane in edge.findall("lane"):
            identity = lane.get("id")
            length = _number(lane.get("length"), positive=True)
            offset = _number(lane.get("endOffset", "0"))
            if length is None:
                invalid.append(identity)
            rows.append({"lane_id": identity, "length_m": length, "end_offset_m": offset,
                         "shape_length_m": _polyline_length(lane.get("shape", ""))})
        lengths = [row["length_m"] for row in rows]
        spread = max(lengths) - min(lengths) if lengths and None not in lengths else None
        if edge.get("function", "normal") != "normal":
            if spread is not None and spread > 1e-6:
                internal_variation.append(edge.get("id"))
            continue
        shapes = [row["shape_length_m"] for row in rows]
        geometry_mean = sum(shapes) / len(shapes) if shapes and None not in shapes else None
        loaded = _number(edge.get("length"), positive=True)
        offsets = [row["end_offset_m"] for row in rows]
        expected = (max(0.1, loaded - sum(offsets) / len(offsets))
                    if loaded is not None and offsets and None not in offsets and not edge.get("bidi") else None)
        normal.append({"edge_id": edge.get("id"), "lanes": rows, "logical_spread_m": spread,
                       "geometry_mean_m": geometry_mean, "declared_edge_length_m": loaded,
                       "explicit_edge_length_present": edge.get("length") is not None,
                       "expected_custom_common_length_m": expected,
                       "custom_common_max_error_m": max(abs(value - expected) for value in lengths)
                       if expected is not None and lengths and None not in lengths else None,
                       "bidirectional_partner": edge.get("bidi")})
    return {"status": "measured", "normal_edge_count": len(normal),
            "unequal_normal_edge_ids": [row["edge_id"] for row in normal
                                        if row["logical_spread_m"] is not None and row["logical_spread_m"] > 1e-6],
            "custom_length_difference_edge_ids": [row["edge_id"] for row in normal
                if row["custom_common_max_error_m"] is not None and row["custom_common_max_error_m"] > 0.011],
            "invalid_length_lane_ids": invalid, "varying_non_normal_edge_ids": internal_variation,
            "normal_edges": normal,
            "claim_boundary": "Logical consistency is not field accuracy. Turning internal lengths may differ. "
                "Geometry means are measurements, not mandatory logical lengths. Bidirectional custom expectations require both partners."}


def audit_network_lengths(*, net_files, output_file):
    paths = list(dict.fromkeys(Path(path).resolve(strict=True) for path in net_files))
    output = Path(output_file).resolve()
    if output.exists() or output in paths or any(output.is_relative_to(path) for path in paths):
        raise ValueError("Use a new length report outside all network inputs.")
    networks = []
    for path in paths:
        record = {"path": str(path), "sha256": file_sha256(path)}
        try:
            root = ET.parse(path).getroot()
            if root.tag != "net":
                raise ValueError("The XML root must be net.")
            record.update(read_status="read", measurements=measure_lane_lengths(root))
        except (OSError, ET.ParseError, ValueError) as error:
            record.update(read_status="unreadable", error=str(error))
        record["unchanged_during_read"] = path.is_file() and file_sha256(path) == record["sha256"]
        networks.append(record)
    report = {"schema": "astra.network-length-audit/v1", "status": "measured",
              "networks": networks}
    write_json_atomic(output, report)
    return {**report, "report_file": str(output), "report_sha256": file_sha256(output)}
