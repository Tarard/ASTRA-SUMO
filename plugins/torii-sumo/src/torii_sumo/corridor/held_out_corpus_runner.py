from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib import request
from xml.etree import ElementTree as ET

import osmium

from torii_sumo.core.artifact_io import write_json_atomic, write_text_atomic
from torii_sumo.core.candidate_contracts import file_sha256

from .enums import GateStatus
from .held_out_corpus_contracts import (
    CroppedCorridorSnapshot,
    DownloadedCityExtract,
    GeographicBbox,
    HeldOutCityExtract,
    HeldOutCorpusSnapshotReport,
    HeldOutCorpusSpec,
    HeldOutCorridorSelection,
)
from .held_out_review_contracts import HeldOutReviewPolicy


_FEATURE_NAMES = ("bicycle", "bridge", "pedestrian", "rail", "ramp", "tunnel")


def build_held_out_osm_snapshots(
    spec_file: Path,
    *,
    held_out_review_policy_file: Path,
    output_dir: Path,
    city_extract_cache_dir: Path | None = None,
    only_city_groups: Sequence[str] = (),
    user_agent: str = "Torii-SUMO held-out corridor research",
    timeout_seconds: float = 900.0,
) -> dict[str, Any]:
    """Download hash-pinned city extracts and crop reference-complete corridors."""

    spec_path = spec_file.resolve()
    policy_path = held_out_review_policy_file.resolve()
    destination = output_dir.resolve()
    extract_cache = (
        city_extract_cache_dir.resolve() if city_extract_cache_dir is not None else destination / "city-extracts"
    )
    spec = HeldOutCorpusSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    policy = HeldOutReviewPolicy.model_validate_json(policy_path.read_text(encoding="utf-8"))
    if file_sha256(policy_path) != spec.held_out_review_policy_sha256:
        raise ValueError("Held-out corpus policy hash mismatch.")
    if policy.parent_benchmark_sha256 != spec.parent_benchmark_sha256:
        raise ValueError("Held-out corpus parent benchmark hash mismatch.")
    _validate_policy_alignment(spec, policy)
    destination.mkdir(parents=True, exist_ok=True)
    notice_path = destination / "OSM-LICENSE-NOTICE.txt"
    write_text_atomic(
        notice_path,
        "\n".join(
            (
                "Torii-SUMO held-out corridor OSM data notice",
                "",
                "Data: © OpenStreetMap contributors",
                "License: Open Data Commons Open Database License (ODbL)",
                "License URL: https://opendatacommons.org/licenses/odbl/",
                "Extract provider: BBBike",
                "Provider URL: https://download.bbbike.org/osm/bbbike/",
                "",
                "The manifest binds each downloaded city extract and cropped derivative",
                "to its provider checksum and an independently computed SHA-256.",
                "",
            )
        ),
    )

    requested_groups = {value.strip() for value in only_city_groups if value.strip()}
    known_groups = {source.city_group for source in spec.city_extracts}
    unknown_groups = sorted(requested_groups - known_groups)
    if unknown_groups:
        raise ValueError(f"Unknown held-out city groups: {', '.join(unknown_groups)}")
    selected_sources = tuple(
        source for source in spec.city_extracts if not requested_groups or source.city_group in requested_groups
    )
    selected_source_ids = {source.source_id for source in selected_sources}
    selected_cases = tuple(case for case in spec.corridors if case.city_source_id in selected_source_ids)
    cases_by_source = {
        source.source_id: tuple(case for case in selected_cases if case.city_source_id == source.source_id)
        for source in selected_sources
    }

    blockers: list[str] = []
    if requested_groups and requested_groups != known_groups:
        blockers.append("partial_corpus_run:" + ",".join(sorted(requested_groups)))
    downloads: list[DownloadedCityExtract] = []
    snapshots: list[CroppedCorridorSnapshot] = []
    corridor_dir = destination / "corridor-osm"
    for source in selected_sources:
        extract_path = extract_cache / f"{source.source_id}.osm.pbf"
        try:
            downloaded = download_city_extract(
                source,
                destination=extract_path,
                user_agent=user_agent,
                timeout_seconds=timeout_seconds,
            )
            downloads.append(downloaded)
        except (OSError, RuntimeError, ValueError) as exc:
            blockers.append(f"city_extract_failed:{source.source_id}:{type(exc).__name__}:{exc}")
            continue
        try:
            city_snapshots = crop_city_extract(
                extract_path,
                city_extract_sha256=downloaded.sha256,
                selections=cases_by_source[source.source_id],
                output_dir=corridor_dir,
            )
            snapshots.extend(city_snapshots)
        except (OSError, RuntimeError, ValueError) as exc:
            blockers.append(f"corridor_crop_failed:{source.source_id}:{type(exc).__name__}:{exc}")

    expected_case_ids = {case.selection_id for case in selected_cases}
    observed_case_ids = {case.selection_id for case in snapshots}
    for missing in sorted(expected_case_ids - observed_case_ids):
        blockers.append(f"corridor_snapshot_missing:{missing}")
    if blockers:
        status = GateStatus.BLOCKED
    elif any(item.status is GateStatus.REVIEW for item in snapshots):
        status = GateStatus.REVIEW
    elif any(item.status is not GateStatus.PASS for item in snapshots):
        status = GateStatus.BLOCKED
    else:
        status = GateStatus.PASS
    report = HeldOutCorpusSnapshotReport(
        corpus_id=spec.corpus_id,
        corpus_spec_sha256=file_sha256(spec_path),
        held_out_review_policy_sha256=file_sha256(policy_path),
        status=status,
        city_extracts=tuple(downloads),
        corridors=tuple(snapshots),
        blockers=tuple(blockers),
    )
    report_path = destination / "held_out_corpus.snapshot-report.json"
    write_json_atomic(
        report_path,
        report.model_dump(mode="json", by_alias=True),
        sort_keys=True,
    )
    artifact_paths = [spec_path, policy_path, notice_path, report_path]
    artifact_paths.extend(Path(item.path) for item in downloads)
    artifact_paths.extend(Path(item.path) for item in snapshots)
    manifest_path = destination / "held_out_corpus.snapshot-manifest.json"
    write_json_atomic(
        manifest_path,
        {
            "schema": "torii.corridor.held-out-corpus-snapshot-manifest/v1",
            "corpus_id": spec.corpus_id,
            "status": status.value,
            "source_extracts_immutable": True,
            "osm_attribution": spec.provider_attribution,
            "osm_license": spec.provider_license,
            "osm_license_url": spec.provider_license_url,
            "artifacts": [
                {"path": str(path.resolve()), "sha256": file_sha256(path.resolve())}
                for path in sorted(set(artifact_paths), key=lambda item: item.as_posix())
            ],
        },
        sort_keys=True,
    )
    return {
        **report.model_dump(mode="json", by_alias=True),
        "report_file": str(report_path),
        "manifest_file": str(manifest_path),
        "license_notice_file": str(notice_path),
    }


def download_city_extract(
    source: HeldOutCityExtract,
    *,
    destination: Path,
    user_agent: str,
    timeout_seconds: float,
) -> DownloadedCityExtract:
    """Download one provider-pinned PBF without replacing a valid cache early."""

    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        sha256, md5, length = _hashes_and_size(target)
        if md5 == source.provider_md5 and length == source.expected_content_length_bytes:
            return DownloadedCityExtract(
                source_id=source.source_id,
                status=GateStatus.PASS,
                path=str(target),
                sha256=sha256,
                md5=md5,
                content_length_bytes=length,
                provider_md5_matched=True,
                expected_length_matched=True,
                source_reused=True,
                observed_last_modified_http=source.expected_last_modified_http,
                observed_etag=source.expected_etag,
            )

    temporary = target.with_name(f".{target.name}.download")
    req = request.Request(source.pbf_url, headers={"User-Agent": user_agent})
    observed_last_modified = ""
    observed_etag = ""
    sha_digest = hashlib.sha256()
    md5_digest = hashlib.md5(usedforsecurity=False)
    length = 0
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response, temporary.open("wb") as handle:
            observed_last_modified = str(response.headers.get("Last-Modified", ""))
            observed_etag = str(response.headers.get("ETag", ""))
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                sha_digest.update(chunk)
                md5_digest.update(chunk)
                length += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        md5 = md5_digest.hexdigest()
        if md5 != source.provider_md5:
            raise RuntimeError(
                f"provider MD5 mismatch for {source.source_id}: expected {source.provider_md5}, observed {md5}"
            )
        if length != source.expected_content_length_bytes:
            raise RuntimeError(
                f"content length mismatch for {source.source_id}: "
                f"expected {source.expected_content_length_bytes}, observed {length}"
            )
        if observed_last_modified != source.expected_last_modified_http:
            raise RuntimeError(
                f"Last-Modified mismatch for {source.source_id}: "
                f"expected {source.expected_last_modified_http!r}, "
                f"observed {observed_last_modified!r}"
            )
        if observed_etag != source.expected_etag:
            raise RuntimeError(
                f"ETag mismatch for {source.source_id}: expected {source.expected_etag!r}, observed {observed_etag!r}"
            )
        os.replace(temporary, target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return DownloadedCityExtract(
        source_id=source.source_id,
        status=GateStatus.PASS,
        path=str(target),
        sha256=sha_digest.hexdigest(),
        md5=md5_digest.hexdigest(),
        content_length_bytes=length,
        provider_md5_matched=True,
        expected_length_matched=True,
        source_reused=False,
        observed_last_modified_http=observed_last_modified,
        observed_etag=observed_etag,
    )


def crop_city_extract(
    city_extract: Path,
    *,
    city_extract_sha256: str,
    selections: Sequence[HeldOutCorridorSelection],
    output_dir: Path,
) -> tuple[CroppedCorridorSnapshot, ...]:
    """Crop several corridors in one location-indexed pass.

    Only highway/railway ways intersecting the frozen bbox are selected.
    Restriction relations are retained only when every referenced way is in
    the same crop. ``BackReferenceWriter(remove_tags=False)`` then adds all
    referenced nodes, including traffic-signal/crossing tags.
    """

    source = city_extract.resolve()
    if file_sha256(source) != city_extract_sha256:
        raise ValueError("City extract hash changed before corridor cropping.")
    destination = output_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    selections_by_id = {case.selection_id: case for case in selections}
    if len(selections_by_id) != len(selections):
        raise ValueError("Corridor crop selections must be unique.")
    selected_way_ids: dict[str, set[int]] = {case.selection_id: set() for case in selections}
    roundabout_way_nodes: dict[int, frozenset[int]] = {}
    for entity in osmium.FileProcessor(source).with_locations():
        if not entity.is_way() or not _is_transport_way(entity):
            continue
        entity_id = int(entity.id)
        if _is_roundabout_way(entity):
            roundabout_way_nodes[entity_id] = frozenset(int(node.ref) for node in entity.nodes)
        for case in selections:
            if _way_intersects_bbox(entity, case.bbox):
                selected_way_ids[case.selection_id].add(entity_id)

    roundabout_component_by_way = _roundabout_component_by_way(roundabout_way_nodes)
    for way_ids in selected_way_ids.values():
        touched_components = {
            roundabout_component_by_way[way_id] for way_id in way_ids if way_id in roundabout_component_by_way
        }
        for component in touched_components:
            way_ids.update(component)

    output_paths = {case.selection_id: destination / f"{case.corridor_key}.osm.xml" for case in selections}
    relation_counts = {case.selection_id: 0 for case in selections}
    with ExitStack() as stack:
        writers = {
            case.selection_id: stack.enter_context(
                osmium.BackReferenceWriter(
                    output_paths[case.selection_id],
                    source,
                    overwrite=True,
                    remove_tags=False,
                    relation_depth=0,
                )
            )
            for case in selections
        }
        for entity in osmium.FileProcessor(source):
            if entity.is_way():
                entity_id = int(entity.id)
                for selection_id, way_ids in selected_way_ids.items():
                    if entity_id in way_ids:
                        writers[selection_id].add(entity)
            elif entity.is_relation() and entity.tags.get("type") == "restriction":
                way_refs = {int(member.ref) for member in entity.members if str(member.type) in {"w", "way"}}
                if not way_refs:
                    continue
                for selection_id, way_ids in selected_way_ids.items():
                    if way_refs.issubset(way_ids):
                        writers[selection_id].add(entity)
                        relation_counts[selection_id] += 1

    if file_sha256(source) != city_extract_sha256:
        raise RuntimeError("City extract changed while corridor crops were built.")
    results: list[CroppedCorridorSnapshot] = []
    for case in selections:
        path = output_paths[case.selection_id]
        reference_complete = _osm_xml_is_reference_complete(path)
        feature_counts = _scan_osm_feature_counts(path)
        unconfirmed = tuple(
            feature for feature in case.preregistered_feature_targets if feature_counts.get(feature, 0) == 0
        )
        if not reference_complete:
            status = GateStatus.BLOCKED
        elif unconfirmed:
            status = GateStatus.REVIEW
        else:
            status = GateStatus.PASS
        results.append(
            CroppedCorridorSnapshot(
                selection_id=case.selection_id,
                corridor_key=case.corridor_key,
                status=status,
                city_extract_sha256=city_extract_sha256,
                path=str(path),
                sha256=file_sha256(path),
                selected_way_count=len(selected_way_ids[case.selection_id]),
                selected_restriction_count=relation_counts[case.selection_id],
                observed_feature_counts=feature_counts,
                unconfirmed_preregistered_features=unconfirmed,
                reference_complete=reference_complete,
            )
        )
    return tuple(results)


def _validate_policy_alignment(
    spec: HeldOutCorpusSpec,
    policy: HeldOutReviewPolicy,
) -> None:
    comparisons = {
        "minimum_case_count": (
            spec.minimum_case_count,
            policy.minimum_case_count,
        ),
        "minimum_city_group_count": (
            spec.minimum_city_group_count,
            policy.minimum_city_group_count,
        ),
        "minimum_morphology_count": (
            spec.minimum_morphology_count,
            policy.minimum_morphology_count,
        ),
        "minimum_cases_per_city_group": (
            spec.minimum_cases_per_city_group,
            policy.minimum_cases_per_city_group,
        ),
        "required_traffic_sides": (
            tuple(spec.required_traffic_sides),
            tuple(policy.required_traffic_sides),
        ),
        "required_mode_features": (
            tuple(spec.required_mode_features),
            tuple(policy.required_mode_features),
        ),
    }
    mismatches = [key for key, (left, right) in comparisons.items() if left != right]
    if mismatches:
        raise ValueError("Held-out corpus contradicts review policy fields: " + ", ".join(mismatches))


def _hashes_and_size(path: Path) -> tuple[str, str, int]:
    sha_digest = hashlib.sha256()
    md5_digest = hashlib.md5(usedforsecurity=False)
    length = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha_digest.update(chunk)
            md5_digest.update(chunk)
            length += len(chunk)
    return sha_digest.hexdigest(), md5_digest.hexdigest(), length


def _is_transport_way(way: Any) -> bool:
    return bool(way.tags.get("highway") or way.tags.get("railway"))


def _is_roundabout_way(way: Any) -> bool:
    return str(way.tags.get("junction", "")).strip().lower() == "roundabout"


def _roundabout_component_by_way(
    roundabout_way_nodes: dict[int, frozenset[int]],
) -> dict[int, frozenset[int]]:
    node_to_way_ids: dict[int, set[int]] = {}
    for way_id, node_ids in roundabout_way_nodes.items():
        for node_id in node_ids:
            node_to_way_ids.setdefault(node_id, set()).add(way_id)
    unseen = set(roundabout_way_nodes)
    component_by_way: dict[int, frozenset[int]] = {}
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        stack = [seed]
        component: set[int] = set()
        while stack:
            way_id = stack.pop()
            component.add(way_id)
            neighbors = {neighbor for node_id in roundabout_way_nodes[way_id] for neighbor in node_to_way_ids[node_id]}
            for neighbor in sorted(neighbors & unseen, reverse=True):
                unseen.remove(neighbor)
                stack.append(neighbor)
        frozen = frozenset(component)
        for way_id in frozen:
            component_by_way[way_id] = frozen
    return component_by_way


def _way_intersects_bbox(way: Any, bbox: GeographicBbox) -> bool:
    points: list[tuple[float, float]] = []
    for node in way.nodes:
        try:
            if node.location.valid():
                points.append((float(node.lon), float(node.lat)))
        except (AttributeError, RuntimeError, ValueError):
            continue
    if any(_point_in_bbox(point, bbox) for point in points):
        return True
    return any(_segment_intersects_bbox(start, end, bbox) for start, end in zip(points, points[1:]))


def _point_in_bbox(point: tuple[float, float], bbox: GeographicBbox) -> bool:
    lon, lat = point
    return bbox.west <= lon <= bbox.east and bbox.south <= lat <= bbox.north


def _segment_intersects_bbox(
    start: tuple[float, float],
    end: tuple[float, float],
    bbox: GeographicBbox,
) -> bool:
    """Liang-Barsky segment/rectangle intersection in geographic degrees."""

    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    lower = 0.0
    upper = 1.0
    for p, q in (
        (-dx, x0 - bbox.west),
        (dx, bbox.east - x0),
        (-dy, y0 - bbox.south),
        (dy, bbox.north - y0),
    ):
        if p == 0:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _osm_xml_is_reference_complete(path: Path) -> bool:
    root = ET.parse(path).getroot()
    ids = {
        "node": {item.attrib.get("id") for item in root.findall("node")},
        "way": {item.attrib.get("id") for item in root.findall("way")},
        "relation": {item.attrib.get("id") for item in root.findall("relation")},
    }
    for way in root.findall("way"):
        if any(node.attrib.get("ref") not in ids["node"] for node in way.findall("nd")):
            return False
    for relation in root.findall("relation"):
        for member in relation.findall("member"):
            member_type = member.attrib.get("type")
            if member_type in ids and member.attrib.get("ref") not in ids[member_type]:
                return False
    return True


def _scan_osm_feature_counts(path: Path) -> dict[str, int]:
    counts = {feature: 0 for feature in _FEATURE_NAMES}
    root = ET.parse(path).getroot()
    for element in _osm_tagged_elements(root):
        tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in element.findall("tag")}
        highway = tags.get("highway", "")
        if (
            highway in {"footway", "path", "pedestrian", "steps"}
            or highway == "crossing"
            or _truthy(tags.get("sidewalk"))
            or any(key.startswith("sidewalk:") and _truthy(value) for key, value in tags.items())
            or tags.get("foot") in {"yes", "designated", "permissive"}
        ):
            counts["pedestrian"] += 1
        if (
            highway == "cycleway"
            or tags.get("bicycle") in {"yes", "designated", "permissive"}
            or any(key == "cycleway" or key.startswith("cycleway:") for key in tags)
        ):
            counts["bicycle"] += 1
        if highway.endswith("_link"):
            counts["ramp"] += 1
        if _truthy(tags.get("railway")):
            counts["rail"] += 1
        if _truthy(tags.get("bridge")):
            counts["bridge"] += 1
        if _truthy(tags.get("tunnel")):
            counts["tunnel"] += 1
    return counts


def _osm_tagged_elements(root: ET.Element) -> Iterable[ET.Element]:
    yield from root.findall("node")
    yield from root.findall("way")
    yield from root.findall("relation")


def _truthy(value: str | None) -> bool:
    return bool(value and value.lower() not in {"no", "false", "0", "none"})
