"""Materialize the geometry-safe Hamburg corridor archetype.

The OSM import contains several very close sub-junctions.  Joining every
sub-node into one SUMO polygon creates a self-intersecting surface and loses
official fork movements.  This materializer therefore applies a small,
hash-bound geometry profile:

* join only the two confirmed 0228 sub-groups and the 2421 signal cluster;
* preserve the 0228 branch pair ``199166130``/``243175302`` so its two
  official egress movements remain distinct; and
* replace three inherited oversized junction faces with simple, half-scale
  polygons that do not swallow neighbouring lane faces.

It is a review candidate, never an in-place edit.  Official TLS binding is a
separate stage so topology and historical signal replay remain independently
auditable.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from pathlib import Path
import xml.etree.ElementTree as ET

from .artifact_io import write_json_atomic, write_text_atomic
from .candidate_contracts import file_sha256
from .command_runner import run_command
from .sumo_commands import run_sumo_load_audit
from .surface_overlap_audit import (
    audit_sumo_lane_junction_surface_overlaps,
    compare_sumo_surface_overlap_reports,
)


HAMBURG_CORRIDOR_GEOMETRY_SCHEMA = "torii.hamburg-sandtorkai-corridor-geometry-materializer/v1"
HAMBURG_CORRIDOR_GEOMETRY_PROFILE = "hamburg_sandtorkai_geometry_safe_v1"

JOIN_GROUPS: tuple[tuple[str, ...], ...] = (
    ("1703191982", "268809865"),
    ("5420059034", "759714713", "76463158"),
)

# These are simple polygons in the accepted V10 projected frame.  They are
# derived by scaling the inherited faces around each node center by 0.5.  The
# values are intentionally frozen and reviewed as evidence, rather than
# recomputed from a nearest-node heuristic on every run.
SHAPE_OVERRIDES: Mapping[str, Mapping[str, str]] = {
    "1703191999": {
        "x": "253.07",
        "y": "551.24",
        "type": "traffic_light",
        "shape": (
            "257.425,553.485 257.885,550.315 257.82,549.89 257.14,548.44 "
            "256.355,548.845 256.07,548.975 255.775,549.055 255.415,549.08 "
            "254.93,549.045 254.365,553.81"
        ),
    },
    "25911514": {
        "x": "260.78",
        "y": "552.35",
        "type": "traffic_light",
        "shape": (
            "262.375,555.035 263.13,550.295 261.62,550.265 261.085,550.375 "
            "260.545,550.49 259.9,550.545 259.03,550.48 258.575,553.65 "
            "260.005,554.065 260.475,554.33 260.945,554.6 261.54,554.84"
        ),
    },
    "199166130": {
        "x": "309.30",
        "y": "570.70",
        "type": "traffic_light",
        "tl": "HH_0228",
        "shape": (
            "310.865,573.585 312.58,570.885 312.37,567.515 311.04,566.63 "
            "308.06,567.22 305.67,571.385 306.525,572.13 306.725,572.565 "
            "306.77,573.04 306.665,573.555 306.41,574.115 307.795,574.915 "
            "308.75,573.7 309.255,573.385 309.775,573.26 310.31,573.325"
        ),
    },
}

FOCUS_JUNCTION_IDS = frozenset(
    {
        "228",
        "2421",
        "1703191982",
        "268809865",
        "1703191999",
        "25911514",
        "199166130",
        "243175302",
        "5420059034",
        "759714713",
        "76463158",
    }
)


class HamburgCorridorGeometryMaterializationError(ValueError):
    """Raised when the geometry profile cannot be applied fail-closed."""


def materialize_hamburg_sandtorkai_geometry_safe_candidate(
    *,
    source_net_file: Path,
    expected_source_sha256: str,
    output_dir: Path,
    profile: str = HAMBURG_CORRIDOR_GEOMETRY_PROFILE,
    netconvert_binary: str = "netconvert",
    sumo_binary: str = "sumo",
    timeout_seconds: float = 240.0,
    command_runner: Callable[..., object] = run_command,
) -> dict[str, object]:
    """Build a fresh geometry-safe corridor candidate from the accepted V10 net."""

    source = Path(source_net_file).resolve(strict=True)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if profile != HAMBURG_CORRIDOR_GEOMETRY_PROFILE:
        raise HamburgCorridorGeometryMaterializationError(f"unsupported geometry profile: {profile}")
    if not expected_source_sha256 or file_sha256(source).lower() != expected_source_sha256.lower():
        raise HamburgCorridorGeometryMaterializationError("source network SHA-256 mismatch")
    if destination == source.parent:
        raise HamburgCorridorGeometryMaterializationError("output_dir must not be the source directory")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise HamburgCorridorGeometryMaterializationError("timeout_seconds must be positive")

    source_root = ET.parse(source).getroot()
    source_junction_ids = {
        str(junction.attrib.get("id", ""))
        for junction in source_root.findall("junction")
        if junction.attrib.get("id")
    }
    required = {node for group in JOIN_GROUPS for node in group} | set(SHAPE_OVERRIDES)
    missing = sorted(required - source_junction_ids)
    if missing:
        raise HamburgCorridorGeometryMaterializationError(
            f"source network is not the accepted V10 geometry; missing junctions: {missing}"
        )

    patch_file = destination / "hamburg_sandtorkai_geometry_safe.nod.xml"
    _write_profile_patch(patch_file)
    output_net = destination / "hamburg_sandtorkai_geometry_safe.net.xml"
    joined_file = destination / "hamburg_sandtorkai_geometry_safe.joined_junctions.xml"
    command = [
        str(netconvert_binary),
        "--sumo-net-file",
        str(source),
        "--node-files",
        str(patch_file),
        "--junctions.join-output",
        str(joined_file),
        "--output-file",
        str(output_net),
        "--geometry.check-overlap",
        "0",
    ]
    result = command_runner(command, cwd=destination, timeout_seconds=timeout_seconds)
    command_report = _result_dict(result)
    if command_report.get("status") != "pass" or command_report.get("returncode") != 0:
        raise HamburgCorridorGeometryMaterializationError(
            "geometry netconvert failed: "
            + str(command_report.get("stderr") or command_report.get("error") or command_report)
        )
    if not output_net.is_file():
        raise HamburgCorridorGeometryMaterializationError("netconvert produced no geometry candidate")
    _canonicalize_net_file(output_net)

    candidate_audit = audit_sumo_lane_junction_surface_overlaps(
        output_net,
        report_file=destination / "surface_overlap" / "candidate_surface_overlap_audit.json",
    )
    baseline_audit = audit_sumo_lane_junction_surface_overlaps(
        source,
        report_file=destination / "surface_overlap" / "baseline_surface_overlap_audit.json",
    )
    comparison = compare_sumo_surface_overlap_reports(
        baseline_audit,
        candidate_audit,
        focus_junction_ids=FOCUS_JUNCTION_IDS,
        report_file=destination / "surface_overlap" / "bounded_surface_overlap_comparison.json",
    )
    load_audit = run_sumo_load_audit(
        net_file=output_net,
        output_dir=destination / "sumo_load",
        sumo_binary=sumo_binary,
        timeout_seconds=timeout_seconds,
        command_runner=command_runner,
    )
    source_immutable = file_sha256(source).lower() == expected_source_sha256.lower()
    status = (
        "review_ready"
        if source_immutable
        and command_report.get("status") == "pass"
        and candidate_audit.get("geometry_error_count") == 0
        and comparison.get("status") == "pass"
        and load_audit.get("status") == "pass"
        else "blocked"
    )
    manifest_file = destination / "hamburg_sandtorkai_geometry_safe.manifest.json"
    manifest: dict[str, object] = {
        "schema_id": HAMBURG_CORRIDOR_GEOMETRY_SCHEMA,
        "status": status,
        "claim_status": "geometry-safe-corridor-review-candidate",
        "automatic_promotion_gate": "blocked",
        "profile": profile,
        "source": {
            "path": str(source),
            "sha256_expected": expected_source_sha256,
            "sha256_after": file_sha256(source),
            "immutable": source_immutable,
        },
        "geometry_policy": {
            "join_groups": [list(group) for group in JOIN_GROUPS],
            "protected_branch_groups": [["199166130", "243175302"]],
            "shape_override_ids": sorted(SHAPE_OVERRIDES),
            "shape_override_rule": "frozen half-scale simple polygons around inherited node centers",
            "official_reference": [
                "https://sumo.dlr.de/docs/Networks/PlainXML.html#joining_nodes",
                "https://sumo.dlr.de/docs/netconvert.html#junctions",
            ],
        },
        "patch": {
            "path": str(patch_file),
            "sha256": file_sha256(patch_file),
            "joined_junctions_file": str(joined_file),
        },
        "netconvert": {"command": command, "result": command_report},
        "surface_overlap_audit": candidate_audit,
        "surface_overlap_comparison": comparison,
        "sumo_load_audit": load_audit,
        "artifacts": {"manifest": str(manifest_file), "network": str(output_net)},
        "next_stage": "run the corridor TLS materializer on this geometry-safe net, then reproject MAP and endpoint-aware signal bindings",
    }
    write_json_atomic(manifest_file, manifest, sort_keys=True)
    return manifest


def _write_profile_patch(path: Path) -> None:
    root = ET.Element("nodes")
    for group in JOIN_GROUPS:
        ET.SubElement(root, "join", {"nodes": " ".join(group)})
    for junction_id in sorted(SHAPE_OVERRIDES):
        ET.SubElement(root, "node", {"id": junction_id, **dict(SHAPE_OVERRIDES[junction_id])})
    ET.indent(root, space="    ")
    write_text_atomic(path, '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n")


def _result_dict(result: object) -> dict[str, object]:
    if hasattr(result, "to_dict"):
        return result.to_dict()  # type: ignore[no-any-return]
    return dict(result)  # type: ignore[arg-type]


def _canonicalize_net_file(path: Path) -> None:
    tree = ET.parse(path)
    ET.indent(tree, space="    ")
    write_text_atomic(
        path,
        '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree.getroot(), encoding="unicode") + "\n",
    )
